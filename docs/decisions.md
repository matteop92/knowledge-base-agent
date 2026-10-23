# Decisions

The choices that shaped the project, and why. Each one was made against a measurement where one was possible. The numbers are in [evaluation.md](evaluation.md).

## Principles

1. **Original files are the authority.** Source files are never changed. Anything generated can help find a passage but never replaces the passage as evidence.
2. **Provenance is continuous.** Every stored item resolves to a document ID, the hash of the source it was made from, and an exact span of text. A re-upload replaces the active view only after the new run succeeds.
3. **Search uses two signals.** Embeddings and keyword search run independently and are merged by rank, because their scores cannot be compared directly.
4. **The agent can investigate, within limits the application owns.** It may rephrase, search again and read neighbouring material. Budgets for calls, bytes and time are set by the host, and source text cannot change them.
5. **Models are replaceable.** The application talks to a thin `ModelClient` interface. Today the adapter is OpenRouter, pinned to providers that do not retain or collect data. Every role could be filled by an open-weight model.
6. **Behaviour is inspectable.** Prompts, chunk boundaries, retrieval ranks, tool calls and citation decisions are recorded in receipts that can be reviewed later.
7. **Keep it small.** Python, FastAPI, Postgres with pgvector and full-text search, an explicit agent loop in code, and a plain web app. No graph database, queue or multi-agent framework.

## Decisions and their evidence

| Decision | Why |
| --- | --- |
| **Structural chunks of the raw text** (600 to 1,000 tokens). | On six real files, structural chunks found 47 of 51 required passages in the top 20, against 45 for fixed 800-token chunks. Adding a model-written context sentence to each chunk lost one of those, so it was dropped. |
| **No evidence cards, no summaries in the index.** | A card restated a sentence already in an embedded chunk, gave a small gain on broad questions and cost about 70% more per question. Section and document summaries failed a source-grounding review (one joined two separate highlights into a single cause). Both were removed. |
| **Postgres full-text search, not BM25.** | It is the simple baseline. A true BM25 service would be another piece of infrastructure, justified only by measured need. Merged ranking did not beat vector search alone on the test set, so no more search machinery was added. |
| **Exact vector search, no approximate index yet.** | The corpus is small. An approximate index waits for measured recall and query plans on a larger one. |
| **The host runs the first search; the agent reads whole chunks.** | An early pilot showed the agent stopping after too few relevant passages. Reading whole chunks and checking a finish against unread chunks of sources already used fixed that. |
| **Answer quality is rated by a person against a frozen 85-check list.** | A model grader was built and calibrated on held-out answers, but it missed the pass bar. It now pre-rates answers and a person confirms. See [evaluation.md](evaluation.md). |
| **Prompt v13 is the answer baseline.** | It replaced "cover every point in the passages read" with a relevance test, cutting points outside the question's scope from 9 to 3 with a similar overall score. |
| **The hosted database schema is private.** | The `knowledge` schema is outside the exposed API, `anon` and `authenticated` get no grants, and the app logs in as a separate narrow role. |
| **Capture creates new files only.** | The server never edits or overwrites a note. A file in the vault is changed only by its owner. Capture is refused unless the server's vault sync is two-way. |
| **Spend is capped everywhere.** | Live model calls need an explicit flag and a dollar ceiling. A daily cap applies to all spending, and a ledger records measured cost. |
| **Schema changes are versioned SQL.** | Migrations live in `supabase/migrations/` and deploy through Supabase's GitHub integration when merged into `main`. |

## Later work, not built

- **Connections across sources.** Search finds what matches the question's words. It does not surface a related idea in another source. Candidates are related-chunk links from the existing embeddings and a small theme layer, and they should be tried only after the evaluation includes cross-source and whole-vault questions.
- **Questions about the whole vault.** "Find all" and recurring-theme questions need more passages than a top-20 search returns.
- **Faster answers.** A typical hosted answer takes about 100 seconds (about a dozen model calls at $0.04). Options to test against the frozen checklist: show progress while it works, lower the reasoning effort, cap the rounds, or run searches in parallel.

## Where the first design went

The original design brief is kept in [archive/knowledge-agent-architecture.md](archive/knowledge-agent-architecture.md). It planned evidence cards and summaries, which the trials above removed. Two plans from the build are in [archive/plans/](archive/plans/) to show how work was scoped.
