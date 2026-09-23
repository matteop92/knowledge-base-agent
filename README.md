# Personal knowledge agent

This repository captures UTF-8 Markdown from an existing Knowledge folder and produces source-aligned parsed previews for the supported bold-speaker transcript format. The common parsed document contract also represents headings, prose, highlights, and personal notes for future parsers. Search, chunking, Supabase, and the answer agent are later steps in `FOUNDATION.md`.

## Preview a transcript

Use Python 3.11 or newer. From the repository root, install the dependencies with `python -m pip install -e .`, then set `KNOWLEDGE_SOURCE_ROOT` to the parent Knowledge folder containing `Transcripts` and other source categories. The root alias defaults to `primary`; keep it stable if you move the folder. Pass paths relative to that parent folder.

In PowerShell:

```powershell
$env:KNOWLEDGE_SOURCE_ROOT = '<path to Knowledge folder>'
python -m app.ingestion.preview --relative-path 'Transcripts/<episode filename>.md'
```

For the first episode, add `--eval-file evals/transcript_questions.yaml` to validate the initial question labels against that exact source revision. The command prints a short summary including block and excluded-span counts, and writes `source.md`, `capture.json`, and `parsed.json` under `.local/ingestion-runs/<run_id>/`. This directory is ignored by Git. The original transcript is never edited. Preview currently parses the first transcript format; other Markdown parsers are a later step.

If the original file is replaced, it keeps the same `document_id` at the same root alias and relative path, but receives a new source hash and run ID. Eval labels are tied to one hash and must be checked again after a replacement.

## Tests

Run the standard library test suite from the repository root:

```powershell
python -m unittest discover -s tests/ingestion -p 'test_*.py' -v
```

Tests use a short synthetic transcript in `sources/fixtures/`. The full vault transcript is used only for an optional local acceptance run.

## Current contracts

`app/ingestion/contracts.py` defines source locators, metadata, half-open Unicode character spans, and versioned parsed documents. Each ordered block retains its exact source text and may carry a speaker, heading path, page reference, or personal-note role. Frontmatter, whitespace, export furniture, and unclassified ranges are tracked separately so captured text cannot disappear silently. `source_sha256` is the hash of exact file bytes; offsets refer to `source_text` after UTF-8 decoding and optional BOM removal. Line numbers are one-based. The source snapshot makes old local runs reproducible while the original vault file remains authoritative.
