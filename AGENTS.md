# Project guide

**Status:** The repository can capture, parse, and chunk mixed Markdown from an external vault, with source-linked eval labels and local previews. Prompts and code for model enrichment exist and are tested with fake model responses; no live model has been chosen or run on the vault, and storage, retrieval, and the answer agent are still planned.

Read [SYSTEM.md](SYSTEM.md) for the one-page map, then open only the relevant detail: [INGESTION.md](INGESTION.md) for source processing, [RETRIEVAL.md](RETRIEVAL.md) for search and answering, or [DATA.md](DATA.md) for objects and persistence. These four files describe the current system and intended next stages; keep them concise and update descriptions in place rather than adding a history section.

## Where to find things

- `app/ingestion/`: source capture, parsing, chunking, eval mapping, enrichment, summaries, embeddings, and preview CLI.
- `app/models/`: model interface and opt-in OpenRouter adapter.
- `prompts/`: versioned prompts for chunk context, evidence cards, section summaries, and document summaries.
- `evals/`: checked source-span questions for real vault files and synthetic fixture labels; see [evals/README.md](evals/README.md).
- `tests/`: ingestion and model tests. Run `python -m unittest discover -s tests/ingestion -p 'test_*.py'` and the same command with `tests/models`.
- `docs/plans/`: implementation plans; useful for decisions, but the four living docs above describe how the system works now.
- `README.md`: installation and local preview commands.

The Markdown vault is outside this repository and is read through `KNOWLEDGE_SOURCE_ROOT`. Local source snapshots and generated artifacts go under ignored `.local/ingestion-runs/`; do not commit vault content or model outputs.

## Task workflow

Use the repository skills `$start-task` and `$finish-task` from `.agents/skills/` (or select them through `/skills` where supported). `$start-task` gives a brief orientation and stops; `$finish-task` refreshes the relevant living doc after implementation.
