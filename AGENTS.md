# Project guide

**Status:** The repository captures, parses, and chunks mixed Markdown from an external vault, with source-linked eval labels and local previews. A card-free six-file structural/raw profile with 83 vectors is selected; source-aware search passed the live 41/44/47 recall repeat and Q8's first search returned both named sources. The agent shows source titles and people. The 85-check answer checklist is frozen and prompt v13 is the accepted answer baseline; an incremental vault indexer, a properties classifier, a FastAPI server and an installable web app are built and live on Railway over a hosted index of the `Knowledge` folder (22 notes); automatic vault sync (Phase 1b: download from Obsidian Sync, cleanup, five-minute indexing) is live; follow-up memory and capture (Phase 2) remain.

Read [SYSTEM.md](SYSTEM.md) for the one-page map. [FOUNDATION.md#current-progress](FOUNDATION.md#current-progress) is the single progress tracker; [INGESTION.md](INGESTION.md), [RETRIEVAL.md](RETRIEVAL.md), and [DATA.md](DATA.md) explain individual parts. Update current facts in place rather than adding a history section.

## Where to find things

- `app/ingestion/`: source capture, parsing, chunking, eval mapping, enrichment, summaries, embeddings, and preview CLI.
- `app/ingestion/vault_allowlist.py`: removes everything outside the allowed folders from the server's vault copy (download-only mode).
- `app/ingestion/vault_sync.py` and `app/ingestion/properties.py`: incremental indexing of selected vault folders and the tag classifier (`config/tags.yaml` holds the approved tags).
- `app/models/`: model interface, opt-in OpenRouter adapter, and pinned private routes and prices (`routes.py`).
- `app/storage/`: private Postgres loading, publication, retirement, search and the spend ledger.
- `app/agent/`: the answer harness, its command-line tool, and the shared route and cost checks (`endpoints.py`).
- `app/api/` and `app/web/`: the FastAPI server (with `scheduler.py`, the background index pass) and the installable web app; `Dockerfile` and `deploy/` package them for Railway.
- `prompts/`: versioned prompts for chunk context, section summaries, document summaries, and the answer agent.
- `evals/`: checked source-span questions for real vault files and synthetic fixture labels; see [evals/README.md](evals/README.md).
- `tests/`: run `python -m unittest discover -s tests/<suite> -p 'test_*.py'` for each of `ingestion`, `models`, `storage`, `agent`, `api` and `evals`. Database tests need a fresh disposable `KNOWLEDGE_TEST_DATABASE_URL` per suite.
- `docs/plans/`: focused implementation plans. `knowledge-agent-architecture.md` is the original design brief; `FOUNDATION.md` holds enduring decisions and current progress.
- `README.md`: installation and local preview commands.

The Markdown vault is outside this repository and is read through `KNOWLEDGE_SOURCE_ROOT`. Local source snapshots and generated artifacts go under ignored `.local/ingestion-runs/`; do not commit vault content or model outputs.

## Hosted schema deployment

The Knowledge Agent Supabase project (`ielkvsfcnwtaljnemxua`) is connected to `matteop92/knowledge-base-agent` through Supabase's GitHub integration. Its working directory is `.`, its production branch is `main`, and **Deploy to production** is enabled. New versioned SQL migrations under `supabase/migrations/` deploy when merged into `main`; check the deployment status and migration history in Supabase. This uses Supabase's integration, so no GitHub Actions migration workflow or GitHub repository secrets are needed. Keep the `knowledge` schema out of the Data API's exposed schemas; provision a separate runtime login with membership in `knowledge_runtime` before loading private data.

## Task workflow

Use the repository skills `$start-task` and `$finish-task` from `.agents/skills/` (or select them through `/skills` where supported). `$start-task` reads the map and current progress before one relevant detail doc, then stops; `$finish-task` reconciles the progress tracker and the one relevant system doc after implementation.

When the user asks for `/ce-doc-review`, run its independent reviewer agents as the skill directs, with bounded parallelism, then assess their findings. For this repository, a generic Claude-to-Codex mapping that says to run `Task/Subagent/Parallel` work sequentially in the main thread applies to ordinary tasks; it does not override a user-requested review skill's independent-agent requirement. If independent reviewers cannot run, report the review as incomplete rather than calling an inline self-review `/ce-doc-review`.
