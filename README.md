# Personal knowledge agent

This repository is at **step 1: podcast transcript source handling**. It can read a Markdown transcript from an existing local folder, preserve exact source spans, and create a local parsed preview. Search, chunking, Supabase, and the answer agent are later steps in `FOUNDATION.md`.

## Preview a transcript

Use Python 3.11 or newer. From the repository root, install the dependencies with `python -m pip install -e .`, then set `KNOWLEDGE_SOURCE_ROOT` to the folder containing your original `.md` transcripts. The root alias defaults to `primary`; keep it stable if you move the folder.

In PowerShell:

```powershell
$env:KNOWLEDGE_SOURCE_ROOT = '<folder containing original transcripts>'
python -m app.ingestion.preview --relative-path '<transcript filename>.md'
```

For the first episode, add `--eval-file evals/transcript_questions.yaml` to validate the initial question labels against that exact source revision. The command prints a short summary and writes `source.md`, `capture.json`, and `parsed.json` under `.local/ingestion-runs/<run_id>/`. This directory is ignored by Git. The original transcript is never edited.

If the original file is replaced, it keeps the same `document_id` at the same root alias and relative path, but receives a new source hash and run ID. Eval labels are tied to one hash and must be checked again after a replacement.

## Tests

Run the standard library test suite from the repository root:

```powershell
python -m unittest discover -s tests/ingestion -p 'test_*.py' -v
```

Tests use a short synthetic transcript in `sources/fixtures/`. The full vault transcript is used only for an optional local acceptance run.

## Current contracts

`app/ingestion/contracts.py` defines source locators, metadata, half-open Unicode character spans, turns, and parsed transcripts. `source_sha256` is the hash of exact file bytes; offsets refer to `source_text` after UTF-8 decoding and optional BOM removal. Line numbers are one-based. The source snapshot makes old local runs reproducible while the original vault file remains authoritative.
