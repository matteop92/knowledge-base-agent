# Ingestion

Ingestion turns an external Markdown file into source-linked records and, in a separate model step, generated context, summaries, and vectors. The original file is never changed.

| Stage | Input → output | Current behavior |
| --- | --- | --- |
| Capture | Vault-relative `.md` path → source snapshot and capture manifest | `app/ingestion/source_reader.py` reads UTF-8, gives the path a stable `document_id`, hashes original bytes, and creates a new `run_id`. |
| Route and parse | Snapshot → `ParsedDocument` | `router.py` chooses bold or bracket transcript, Kindle highlights, book, article, note, or generic Markdown. Parsers preserve headings, speaker turns, highlights, notes, source spans, excluded spans, and diagnostics. |
| Chunk | Parsed document → two `ChunkSet`s | `chunking.py` builds `fixed_raw` (800 tokens, 80 overlap) and `structural_raw` (target 600–1000 tokens). Each chunk retains exact source spans. In a six-file exact-vector trial, structural/raw covered 47 of 51 required spans at K=20, versus 45 for fixed/raw. |
| Check | Eval labels + chunk sets → mapping | `eval_mapping.py` checks which chunks contain manually labeled answer spans. This is source coverage, not a retrieval score. |
| Enrich | Chunks + model → local artifacts | `enrichment.py`, `summary.py`, and `embeddings.py` use versioned prompts, source checks, and a `RunManifest` to make chunk context, section/document summaries, and raw or contextual embeddings. The selected six-file local profile uses structural/raw chunks and Perplexity raw embeddings without required enrichment artifacts. Summaries remain unqualified. |

`python -m app.ingestion.preview --relative-path '<path inside vault>'` runs capture through checking and writes `source.md`, `capture.json`, `parsed.json`, both chunk sets, and `run-manifest.json` in `.local/ingestion-runs/<run_id>/`. `--eval-file` adds `eval_mapping.json`; `--source-kind` overrides automatic routing. The preview does not invoke enrichment.

The parsed metadata retains `author` and `guest` as separate people lists, as well as title and tags. A missing title falls back to the file name. The preview report includes `missing_metadata` for absent title or author, alongside the file locator, so these files remain visible while still ingesting.

Source text is matched by Unicode character offsets in the decoded snapshot. The same path under the same root alias keeps its document ID; editing the bytes changes the source hash and requires checking eval labels again. Generated artifacts record their model, prompt/input versions, source revision, and completion state so a failed or partial run is visible and resumable. The OpenRouter adapter in `app/models/` requires deliberate live-call selection, request/input budgets, and pinned zero-retention, no-collection routes. The selected raw embedding model is Perplexity 0.6B. The six previously loaded runs are now published under a profile with no required enrichment artifacts; the card artifacts in their old manifests are ignored by the loader. Hosted loading is separate.

See [DATA.md](DATA.md) for the records, [RETRIEVAL.md](RETRIEVAL.md) for how they are intended to be searched, and [SYSTEM.md](SYSTEM.md) for the whole flow.
