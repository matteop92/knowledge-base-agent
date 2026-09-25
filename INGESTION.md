# Ingestion

Ingestion turns an external Markdown file into source-linked records and, in a separate model step, generated context, cards, summaries, and vectors. The original file is never changed.

| Stage | Input → output | Current behavior |
| --- | --- | --- |
| Capture | Vault-relative `.md` path → source snapshot and capture manifest | `app/ingestion/source_reader.py` reads UTF-8, gives the path a stable `document_id`, hashes original bytes, and creates a new `run_id`. |
| Route and parse | Snapshot → `ParsedDocument` | `router.py` chooses bold or bracket transcript, Kindle highlights, book, article, note, or generic Markdown. Parsers preserve headings, speaker turns, highlights, notes, source spans, excluded spans, and diagnostics. |
| Chunk | Parsed document → two `ChunkSet`s | `chunking.py` builds `fixed_raw` (800 tokens, 80 overlap) and `structural_raw` (target 600–1000 tokens). Each chunk retains exact source spans. In a six-file exact-vector trial, structural/raw covered 47 of 51 required spans at K=20, versus 45 for fixed/raw. |
| Check | Eval labels + chunk sets → mapping | `eval_mapping.py` checks which chunks contain manually labeled answer spans. This is source coverage, not a retrieval score. |
| Enrich | Chunks + model → local artifacts | `enrichment.py`, `summary.py`, and `embeddings.py` use versioned prompts, source checks, and a `RunManifest` to make context, evidence cards, section/document summaries, and raw or contextual embeddings. Live trials retained structural/raw embeddings and selected GPT-5 mini `evidence_cards_v5` after the cheaper candidates failed first-response or route gates. GPT-5 mini and DeepSeek `v14`/`v15` summaries met document-content coverage, but source review found a factual/attribution error in each, so no summary model qualifies. The combined six-file load/search run remains pending. |

`python -m app.ingestion.preview --relative-path '<path inside vault>'` runs capture through checking and writes `source.md`, `capture.json`, `parsed.json`, both chunk sets, and `run-manifest.json` in `.local/ingestion-runs/<run_id>/`. `--eval-file` adds `eval_mapping.json`; `--source-kind` overrides automatic routing. The preview does not invoke enrichment.

Source text is matched by Unicode character offsets in the decoded snapshot. The same path under the same root alias keeps its document ID; editing the bytes changes the source hash and requires checking eval labels again. Generated artifacts record their model, prompt/input versions, source revision, and completion state so a failed or partial run is visible and resumable. The OpenRouter adapter in `app/models/` requires deliberate live-call selection, request/input budgets, and pinned zero-retention, no-collection routes. The selected raw embedding model is Perplexity 0.6B; `app/ingestion/live_run.py` provides a bounded complete-run path that awaits a qualifying summary model, user approval, and a live interruption/resume plus local publication proof.

See [DATA.md](DATA.md) for the records, [RETRIEVAL.md](RETRIEVAL.md) for how they are intended to be searched, and [SYSTEM.md](SYSTEM.md) for the whole flow.
