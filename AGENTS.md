# Project guide

A personal knowledge agent: it indexes a Markdown vault into Postgres and answers questions with cited passages, through a command-line tool and an installable web app. The server, sync, capture and follow-up memory are live. The prompt v13 answer baseline is accepted; results are in [docs/evaluation.md](docs/evaluation.md).

Read [README.md](README.md) for the overview, then [docs/architecture.md](docs/architecture.md) for the system map. [docs/decisions.md](docs/decisions.md) holds the choices and why, [docs/evaluation.md](docs/evaluation.md) the measurements, [docs/deploy.md](docs/deploy.md) the hosting guide. Update facts in place; do not add a history section.

## Where to find things

- `app/ingestion/`: source capture, parsing, chunking, eval mapping, enrichment, summaries, embeddings, and preview CLI.
- `app/ingestion/vault_allowlist.py`: removes everything outside the allowed folders from the server's vault copy (download-only mode; in two-way mode only folders the sync tool's skip list names exactly).
- `app/ingestion/vault_sync.py` and `app/ingestion/properties.py`: incremental indexing of selected vault folders and the tag classifier (the `knowledge.tags` table holds the live tag list; `config/tags.yaml` is its seed).
- `app/models/`: model interface, opt-in OpenRouter adapter, and pinned private routes and prices (`routes.py`).
- `app/storage/`: private Postgres loading, publication, retirement, search and the spend ledger.
- `app/agent/`: the answer harness, its command-line tool, and the shared route and cost checks (`endpoints.py`).
- `app/capture/`: captured text, `.md` files, screenshots and YouTube links become new notes in the `Knowledge` folders (new files only).
- `app/api/` and `app/web/`: the FastAPI server (with `scheduler.py`, the background index pass, and `tags.py`, the suggested-tags store behind Settings) and the installable web app; `Dockerfile` and `deploy/` package them for Railway.
- `prompts/`: versioned prompts in use, with a changelog in `prompts/README.md`; retired versions are in `prompts/archive/`.
- `evals/`: checked source-span questions, the frozen answer checklist and the trial scripts; see [evals/README.md](evals/README.md).
- `tests/`: run `python -m unittest discover -s tests/<suite> -p 'test_*.py'` for each of `ingestion`, `models`, `storage`, `agent`, `api`, `capture` and `evals`. Database tests need a fresh disposable `KNOWLEDGE_TEST_DATABASE_URL` per suite.
- `docs/`: architecture, decisions, evaluation and deploy guides; working plans in `docs/plans/` are local only (gitignored).
- `README.md`: overview, quick start and limits.

The Markdown vault is outside this repository and is read through `KNOWLEDGE_SOURCE_ROOT`. Local source snapshots and generated artifacts go under ignored `.local/ingestion-runs/`; do not commit vault content, private folder names, personal data, hostnames or project IDs, or model outputs. Treat the repository as public.

## Web app type scale (fixed)

The Paper refinement replaced the old six-size scale (12/14/16/18/22/26). Only the sizes in `TYPE_SCALE` in `tests/api/test_static.py` exist: 10.5, 11, 11.5, 12, 12.5, 13, 13.5, 14, 14.5, 15, 16, 17, 17.5, 18, 18.5, 19, 24, 26, 27, 30, 32, 36, 40, 44 (the quote mark on a cited passage) and 46 px. One size per role, and a new screen reuses the roles instead of choosing sizes; the test rejects any other size.

- Newsreader (serif): display heading on a new question 46 (phone 36); page title 40 (phone 32); question 27; answer 18.5; drawer title 26 (phone 24); tag name in the drawer 32 (phone 30); gap text 18.5 (phone 18); cited passage 17.5 (phone 17); note title in a card 17.5; feedback heading 19; brand 17.
- Geist (sans): small section labels 11, upper case, wide tracking; interface text 14 (history and chips 13.5); sub-lines 15 (intro and helper text 14 to 14.5); captions and meta 12 to 12.5; inputs 16; footnote markers 11 (10.5 when filled).
- Buttons: regular weight, 32 px with a mouse and 44 px on touch; small row actions are 28 px with a larger touch target around them. Nothing grows beyond that to match a mock-up. Footnote and number chips are weight 500 at most. Icons are Phosphor Light.
- State (open source, open gaps, open tag, drag target) is shown with a fill, never with a ring or outline; keyboard focus on buttons and links is a thin dark ring.

## Hosted schema deployment

The Supabase project is connected to `matteop92/knowledge-base-agent` through Supabase's GitHub integration. Its working directory is `.`, its production branch is `main`, and **Deploy to production** is enabled. New versioned SQL migrations under `supabase/migrations/` deploy when merged into `main`; check the deployment status and migration history in Supabase. This uses Supabase's integration, so no GitHub Actions migration workflow or GitHub repository secrets are needed. Keep the `knowledge` schema out of the Data API's exposed schemas; provision a separate runtime login with membership in `knowledge_runtime` before loading private data.

## Task workflow

Use the repository skills `$start-task` and `$finish-task` from `.agents/skills/` (or select them through `/skills` where supported). `$start-task` reads the map and the Status section of docs/architecture.md before one relevant detail doc, then stops; `$finish-task` reconciles the Status section and the one relevant doc after implementation.
