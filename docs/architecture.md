# Architecture

The Markdown vault is the authority. A file is processed into an index once, and many questions then use that index. These are two separate flows. Every derived item points back to the exact source revision it came from.

Contents: [Building the index](#building-the-index) · [Answering a question](#answering-a-question) · [Data and provenance](#data-and-provenance) · [Search](#search) · [The answer loop](#the-answer-loop) · [The web app](#the-web-app) · [Status](#status)

## Building the index

```mermaid
flowchart TB
    SRC["1 · Markdown file"] --> CAP["2 · Capture: document ID, byte hash, snapshot"]
    CAP --> PARSE["3 · Route and parse<br/>transcript, highlights, book, article or note"]
    PARSE --> BLOCKS["4 · Source-aligned blocks, excluded spans, diagnostics"]
    BLOCKS --> STRUCT["5 · Structural chunks<br/>600–1000 tokens, structure kept"]
    STRUCT --> EMBED["6 · Embed each chunk<br/>(model call through ModelClient)"]
    EMBED --> CHECK["7 · Validate source links and versions"]
    CHECK --> STORE["8 · Postgres stores the run and builds the indexes"]
```

| Stage | What it does | Code |
| --- | --- | --- |
| Capture | Reads the file as UTF-8, gives its path a stable `document_id`, hashes the original bytes and starts a new `run_id`. | `app/ingestion/source_reader.py` |
| Route and parse | Chooses a parser for bold or bracket transcripts, Kindle highlights, books, articles, notes or generic Markdown. Parsers keep headings, speaker turns, highlights, personal notes, exact source spans, excluded spans and diagnostics. | `router.py` and the parsers in `app/ingestion/` |
| Chunk | Builds chunks of whole structural units (target 600 to 1,000 tokens). Each chunk keeps its exact source spans. A fixed 800-token variant (80 overlap) exists as the baseline the structural variant was compared with. | `chunking.py` |
| Embed | Turns each chunk into a vector. The selected model is `perplexity/pplx-embed-v1-0.6b` (1,024 dimensions, cosine distance). | `embeddings.py`, `app/models/` |
| Check and store | Validates that every record resolves to a source revision, then loads one complete run into Postgres in a transaction. A failed or partial run is never searchable. | `run_manifest.py`, `app/storage/` |

Text offsets are Unicode character offsets in the decoded snapshot. Editing a file changes its hash and gives it a new run; the document ID stays the same. The tool that reads the vault never edits it.

After the first load, `app/ingestion/vault_sync.py` keeps the index current. It indexes the vault's `Knowledge` folder incrementally by content hash, retires deleted files behind a guard (it refuses to retire more than 10% or 20 files at once), and classifies each note's type, tags, people, date and description (`properties.py`, using the live tag list in the `knowledge.tags` table).

**Not used in the live profile.** Chunk contexts, section and document summaries and evidence cards were all tried (see [decisions.md](decisions.md)). The selected profile needs none of them. Chunk-context code remains in `app/ingestion/enrichment.py`; the summary generators and evidence cards were removed, and only the stored `Summary` record stays so earlier runs still load.

## Answering a question

```mermaid
flowchart TB
    Q["1 · Question"] --> HOST["2 · Host runs the first hybrid search"]
    HOST --> HYB["3 · Vector search + Postgres full-text search"]
    HYB --> RRF["4 · Rank fusion (RRF)"]
    RRF --> LOOP["5 · Agent loop: read whole chunks, search or find again,<br/>track what the question needs"]
    LOOP --> CITE["6 · Host checks every citation against a passage the agent read"]
    CITE --> ANS["7 · Answer with citations, or an honest statement of the gaps"]
```

The application owns the loop and its limits; the model only chooses the next action. Retrieval tools return candidates, never answers.

## Data and provenance

Local JSON artifacts under ignored `.local/ingestion-runs/<run_id>/` are validated before loading into the private `knowledge` Postgres schema (`supabase/migrations/`). The schema is not exposed through Supabase's API; a separate login that inherits the narrow `knowledge_runtime` grants is used by the trusted Python code.

| Object | What it is |
| --- | --- |
| Document | A Markdown path under a stable root alias. The ID stays the same when the contents change. |
| Run | One read of the original bytes with its own `run_id` and `source_sha256`. It keeps the exact bytes, the decoded text and the parsed JSON. |
| Chunk set and chunk | A view of the eligible blocks with a variant and version. Each chunk has exact source spans. |
| Embedding spec and vector | Provider, model, dimensions, metric, input recipe and chunking version, with one vector per chunk. Changing any of these means a new complete index. |
| Profile | Chooses one chunk variant, embedding spec and full-text configuration. |
| Active run | The validated run for each document under a profile. Only active runs serve normal questions. |
| Document properties | Generated type, tags, people, date and description beside the unchanged source. |

Publication checks that all vectors are complete in one transaction before a run becomes active. Older and staged runs need an explicit scope to be queried.

## Search

`app/storage/search.py` runs two independent searches over the active profile:

- **Vector search**: exact pgvector search with the selected embedding spec. Before a private query is embedded, the endpoint must qualify for zero data retention, no data collection, a pinned provider and the price ceiling.
- **Full-text search**: Postgres `simple` lexemes with `websearch_to_tsquery` and `ts_rank` (not BM25). A whole-question search with no all-term hits retries as an OR query.

`app/storage/retrieval.py` combines them with reciprocal rank fusion (the sum of `1/(60+rank)` over at most 20 hits per channel, at most 20 candidates, duplicates removed). Search also reads each document's title, authors, guests and tags, so a question that names a source ("what does the Bending Spoons interview say") reaches it even if those words are not in the chunk text. Up to five chunks per named source are reserved.

Reading a passage re-checks document, run, hash and chunk identity and returns at most 2,400 characters at a time; longer chunks continue with a new citation ID. Results (measured recall) are in [evaluation.md](evaluation.md).

## The answer loop

`app/agent/harness.py` runs strictly parsed JSON actions (`search`, `find`, `read`, `finish`) through `ModelClient`. Each call has its own budgets for model calls, searches, finds, reads, bytes and time. Source text cannot change those limits.

- Count and list questions about the sources ("how many articles by X", "which notes are tagged Y") skip the loop. A small router model (`gpt-6-luna`, prompt `property_router_v1`) turns the question into a filter on authors, guests, people, tags, type, date or title, and `app/agent/properties.py` answers it from the stored properties with one SQL query, citing each listed source's opening passage. Any other question, or a router failure, goes to the loop. Only first questions are routed.
- The host runs one hybrid search before the first model turn. Candidates show source title, people, rank and a short preview, and are listed unread-first. The web app then reads the six best chunks itself (`host_reads`), so the first model turn already has passages to work with.
- A `read` returns every passage of a chunk. Each passage has its own citation ID (`E3.1`).
- The agent tracks the material parts of the question as needs, and marks each one resolved with inspected passage IDs, or excluded with a reason.
- A finish with an open need gets one correction. A finish can also be sent back for unread chunks of sources already used (`coverage_checks`, up to two). The web app turns this off: in the speed trials it cost 40% of answer time for about one point of graded accuracy.
- Complete answers need inline citations that point at passages actually read. This proves provenance, not that a claim is semantically supported; that is checked by people (see [evaluation.md](evaluation.md)).
- One malformed or cut-off reply gets one retry. A second one is an explicit failure.
- The result carries the cited passages, the full ledger of what was inspected, retrieval ranks, the gaps, the stop reason and the cost.

Follow-ups (`app/agent/memory.py`) rewrite the question into a stand-alone search query, resend the last three turns, and carry forward up to 24 passages from the last six turns (24 KB), dropping any whose source has changed. They use prompt `answer_agent_v14`; first questions use v13. See [prompts/README.md](../prompts/README.md).

`python -m app.agent.cli` runs the same core from the command line inside a read-only transaction and writes private reports only to new files under `.local/`.

## The web app

`app/api/` is a FastAPI server and `app/web/` a plain JavaScript installable web app (no build step).

- **Sign-in**: an email code for one allowed user.
- **Answers**: run as background jobs the page polls, with cited excerpts that link back to the original note, a thumbs-down note and a daily spend cap.
- **Capture**: pasted text, `.md` files, screenshots and YouTube links become new notes in the `Knowledge` folders (`app/capture/`). New files only; nothing is ever edited or overwritten.
- **Sync**: the server keeps a copy of the `Knowledge` folder through Obsidian Sync and indexes new, edited and deleted notes every five minutes (`app/api/scheduler.py`, `deploy/`).
- **Settings**: approve or block the tags the classifier proposes.

Hosting is described in [deploy.md](deploy.md).

## Status

| Area | State |
| --- | --- |
| Ingestion and chunking | Working for transcripts, notes, articles, highlights and book exports. |
| Storage and search | Working locally and on the hosted database (33 notes in the `Knowledge` folder, 257 chunks). |
| Answer agent | Prompt v13 is the accepted baseline (78.8% of scored checks on the frozen checklist). Answers take about 37 s at the median and 92 s at p95 (from 114 s and 280 s); count and list questions about 8 s. |
| Web app and capture | Live, with follow-up memory and capture from the phone. |
| Vault sync | Live; the server's sync is two-way so captured notes reach the owner's devices. |
| Later ideas | Cross-source connections and questions about the whole vault; neither has been built. See [decisions.md](decisions.md). |
