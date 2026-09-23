# Personal knowledge agent

This repository captures Markdown from an external Knowledge folder and produces source-linked local artifacts. It supports bold-label and bracket-label transcripts, notes, articles, highlights, and book exports. The original files remain unchanged. Search, Supabase storage, and the answer agent are later work.

Start with [SYSTEM.md](SYSTEM.md) for the whole flow and [FOUNDATION.md#current-progress](FOUNDATION.md#current-progress) for stage-by-stage progress; [INGESTION.md](INGESTION.md), [RETRIEVAL.md](RETRIEVAL.md), and [DATA.md](DATA.md) describe each part at its current stage.

## Preview a source

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

## Tests

```powershell
python -m unittest discover -s tests/ingestion -p 'test_*.py' -v
python -m unittest discover -s tests/models -p 'test_*.py' -v
```

Tests use short synthetic fixtures. Optional read-only acceptance against the external vault is controlled by `KNOWLEDGE_SOURCE_ROOT`.
