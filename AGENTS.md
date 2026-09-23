# Project guide

**Status:** The repository can capture, parse, and chunk mixed Markdown from an external vault, with source-linked eval labels and local previews. Prompts and code for model enrichment exist and are tested with fake model responses; no live model has been chosen or run on the vault, and storage, retrieval, and the answer agent are still planned.

Read [SYSTEM.md](SYSTEM.md) for the one-page map. [FOUNDATION.md#current-progress](FOUNDATION.md#current-progress) is the single progress tracker; [INGESTION.md](INGESTION.md), [RETRIEVAL.md](RETRIEVAL.md), and [DATA.md](DATA.md) explain individual parts. Update current facts in place rather than adding a history section.

## Where to find things

- `app/ingestion/`: source capture, parsing, chunking, eval mapping, enrichment, summaries, embeddings, and preview CLI.
- `app/models/`: model interface and opt-in OpenRouter adapter.
- `prompts/`: versioned prompts for chunk context, evidence cards, section summaries, and document summaries.
- `evals/`: checked source-span questions for real vault files and synthetic fixture labels; see [evals/README.md](evals/README.md).
- `tests/`: ingestion and model tests. Run `python -m unittest discover -s tests/ingestion -p 'test_*.py'` and the same command with `tests/models`.
- `docs/plans/`: focused implementation plans. `knowledge-agent-architecture.md` is the original design brief; `FOUNDATION.md` holds enduring decisions and current progress.
- `README.md`: installation and local preview commands.

The Markdown vault is outside this repository and is read through `KNOWLEDGE_SOURCE_ROOT`. Local source snapshots and generated artifacts go under ignored `.local/ingestion-runs/`; do not commit vault content or model outputs.

## Task workflow

Use the repository skills `$start-task` and `$finish-task` from `.agents/skills/` (or select them through `/skills` where supported). `$start-task` reads the map and current progress before one relevant detail doc, then stops; `$finish-task` reconciles the progress tracker and the one relevant system doc after implementation.
