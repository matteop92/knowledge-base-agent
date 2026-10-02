# Personal knowledge agent

This repository captures Markdown from an external Knowledge folder and produces source-linked local artifacts. It supports bold-label and bracket-label transcripts, notes, articles, highlights, and book exports. The original files remain unchanged. Private Postgres storage, hybrid retrieval, and a bounded local answer CLI are available. Live answer quality remains unmeasured.

Start with [SYSTEM.md](SYSTEM.md) for the whole flow and [FOUNDATION.md#current-progress](FOUNDATION.md#current-progress) for stage-by-stage progress; [INGESTION.md](INGESTION.md), [RETRIEVAL.md](RETRIEVAL.md), and [DATA.md](DATA.md) describe each part at its current stage.

For the current Stage 4 experiment and a new-chat handoff, use the [results and next-screen plan](docs/plans/2026-09-24-001-feat-live-enrichment-ingestion-plan.md) and the [trial script commands](evals/README.md#stage-4-trial-scripts-and-handoff).

## Ask a question

Set `KNOWLEDGE_DATABASE_URL` to the private runtime connection and `OPENROUTER_API_KEY` to your key; the CLI does not load `.env` automatically. It reads only the selected published index, with no `KNOWLEDGE_SOURCE_ROOT` dependency. Configuration and current endpoint checks can run without generation or query embedding:

```powershell
python -m app.agent.cli 'What steps could I take to calm myself down when I realize I feel anger?' `
  --dry-run --embedding-provider-tag perplexity/int8 --generation-provider-tag azure `
  --max-embedding-usd-per-million 0.004 --max-generation-usd-per-million 2 `
  --max-usd 0.25 --report .local/answer-preflight.json
```

Replace `--dry-run` with `--live` and choose a new report filename for an answer. Live mode sends questions/searches and relevant candidate snippets and inspected source passages through OpenRouter to the qualified pinned providers. The default generation configuration is GPT-5 mini at medium reasoning. Each request has fresh usage budgets and an evidence ledger; answering uses a read-only transaction. The compact profile allows 12 model actions, five hybrid searches, two card searches, two document finds, eight whole-chunk reads, and 120 seconds. Add `--budget-profile broad` for a finite 20-action, seven-search, three-card-search, four-find, ten-read, 420-second allowance and more answer output; its request/input budgets and worst-case cost are checked separately. `--input-byte-budget` conservatively counts serialized request bytes plus 512 tokens per request; ingestion retains its existing default budget behavior.

Human output includes the answer, cited locators, bounded excerpts, and evidence gaps. `--json` explicitly outputs the full result. Optional `--report` files must be new paths under ignored `.local/`; they contain private answers, inspected passages, action/rank traces, stop reasons, model configuration, usage, and estimated cost. Interrupted or failed attempts are retained when a report was requested; they are not quality passes. Runtime citation checks establish inspected-source provenance; the frozen pilot review establishes semantic support and usefulness.

The approved Stage 5 ceiling is $8 and the cumulative ceiling is $10. A CLI call enforces its own conservative cost bound; the operator must account for previous reports before selecting a new per-call cap. No answer API or deployment is included.

## Preview a source

For answering an already published index, see [Ask a question](#ask-a-question); that path does not require the external vault.

Use Python 3.11 or newer and install dependencies with `python -m pip install -e .`. Set `KNOWLEDGE_SOURCE_ROOT` to the parent Knowledge folder. The root alias defaults to `primary`; keep it stable when moving that folder.

```powershell
$env:KNOWLEDGE_SOURCE_ROOT = '<path to Knowledge folder>'
python -m app.ingestion.preview --relative-path 'Notes/<filename>.md'
python -m app.ingestion.preview --relative-path 'Transcripts/<filename>.md' --eval-file evals/questions_transcript_tim_ferriss.yaml
```

The command reports the chosen route, parsed block roles, coverage warnings, unresolved assets, fixed and structural chunk counts and boundary reasons, and any eval span mapping. It writes a captured snapshot, parsed document, both chunk sets, and run manifest under ignored `.local/ingestion-runs/<run_id>/`. The preview makes no model calls and prints no source passages. `--source-kind` overrides routing when needed.

After a separate enrichment run, use `python -m app.ingestion.preview --inspect-run '.local/ingestion-runs/<run_id>'` to see artifact versions, ready/failed states, model usage, and configured budgets without showing source passages.

The exact source bytes determine `source_sha256`; Unicode character offsets refer to decoded `source_text`. A changed source keeps its document ID at the same locator but gets a new hash and run ID. Labels must be checked against the new revision.

## Model enrichment

`app/ingestion/enrichment.py`, `summary.py`, and `embeddings.py` accept the project's `ModelClient` interface and a `RunManifest`. They write versioned context, evidence cards, summaries, and embedding batches under the run directory. Evidence cards require exact source text and attribution; partial summary and embedding work remains incomplete and can resume. `EmbeddingSpec` includes provider, model and version, dimensions, metric, input recipe, context prompt version, chunking version, and creation time. Changing a model or recipe creates a separate artifact version.

The direct OpenRouter adapter is opt in. Construct `OpenRouterClient(allow_live=True, request_budget=..., estimated_input_token_budget=...)` only after choosing to send source text externally; it also requires `OPENROUTER_API_KEY`. Request and estimated input budgets are enforced before each call. The automated tests use fake clients and require no key. Model outputs and source snapshots stay in the ignored runtime directory; do not commit them. The token estimate uses a local lexical count, so it is a budget guard rather than provider billing data.

## Private storage and candidate search

### Browse the local database

With local Supabase Postgres running, start the read-only viewer from the repository directory:

```powershell
python -m app.storage.local_viewer
```

Open [http://127.0.0.1:8765/](http://127.0.0.1:8765/) in your browser. The evidence cards page has a source-document filter and card-text search; results show 25 cards per page and link to full evidence. The viewer reads `KNOWLEDGE_DATABASE_URL` from your environment or the repository's ignored `.env`, binds only to this computer, and stops when you close its terminal. Restart the same command after a reboot. It reads the selected profile's active cards; other tables remain available as raw views.

Run a local Supabase Postgres instance with Docker and apply the versioned migration:

```powershell
npx supabase start
npx supabase db reset --local
```

The migration creates a non-exposed `knowledge` schema, a non-login `knowledge_runtime` role, source-linked tables, a GIN full-text index, and pgvector storage. Create a separate login role with membership in `knowledge_runtime` outside the repository, then set `KNOWLEDGE_DATABASE_URL` to its direct or session-pooled Postgres connection string. Keep migration credentials separate. The `knowledge` schema is absent from the Data API's exposed schemas in `supabase/config.toml`; check the hosted project's settings before any deployment.

```powershell
python -m app.storage.cli load '.local/ingestion-runs/<run-id>'
python -m app.storage.cli create-profile <profile-id> fixed_raw <chunking-version> <spec-id>
python -m app.storage.cli publish <profile-id> <run-id>
python -m app.storage.cli select-profile <profile-id>
python -m app.storage.cli status
python -m app.storage.cli search-text 'example phrase' --k 10
python -m app.storage.cli search-vector '.local/query-vector.json' <spec-id> cosine --k 10
```

`load` validates the snapshot hash, parsed source, both chunk sets, and ready artifact provenance before staging one transaction. Repeat loads are idempotent; pending artifacts can later become ready. `publish` checks complete ready vectors for the profile and any explicitly required enrichment artifacts before changing the active run. Normal searches only see the selected profile and active run; `--profile-id` together with `--historical-run-id` opts into an older or staged run for evaluation. FTS uses `simple` lexemes; vector search is exact until a measured model-specific HNSW index is justified. CLI search output omits passage text while retaining source IDs and spans.

For a private embedding trial, first preflight the checked files and cost. Live mode requires `OPENROUTER_API_KEY` in the environment or ignored `.env`, a local migrated database URL, and an explicit spend cap. It writes only ignored captures, vectors, and a metric report:

```powershell
python -m evals.compare_embeddings --source-root '<Knowledge folder>' --max-usd 5
python -m evals.compare_embeddings --source-root '<Knowledge folder>' --max-usd 5 --live
```

The trial uses checked source hashes, fixed raw chunks, exact vector search, independent FTS results, and privacy restricted provider routing. It chooses a model only if required-span Recall@10 reaches 0.8 and complete-question coverage reaches 0.7; otherwise the report records no choice. Provider prices and routes must be rechecked when running a new trial.

The first six-file trial selected `perplexity/pplx-embed-v1-0.6b` with fixed 800/80 raw chunks, 1,024 dimensions, and cosine distance. It retrieved 42 of 51 required spans at K=10 across 31 supported questions (26 questions complete); `baai/bge-m3` retrieved 40 spans (25 complete), and independent FTS retrieved 29 spans. The full comparison reported about $0.0012 of model usage; extrapolated indexing of the current ten Markdown files is about $0.0008 for the selected model and about 1 MB of raw vectors. These are small-sample estimates, and OpenRouter's privacy restricted automatic provider route has not been pinned to a single provider version. The private per-question report is in ignored `.local/embedding-trial/report.json`.

## Tests

```powershell
python -m unittest discover -s tests/ingestion -p 'test_*.py' -v
python -m unittest discover -s tests/models -p 'test_*.py' -v
python -m unittest discover -s tests/storage -p 'test_*.py' -v
```

Tests use short synthetic fixtures. Storage integration tests need a fresh disposable pgvector/Supabase database named by `KNOWLEDGE_TEST_DATABASE_URL`; the test applies the migration and creates test roles, so do not point it at a hosted project. Optional read-only ingestion acceptance against the external vault is controlled by `KNOWLEDGE_SOURCE_ROOT`.
