# Data and provenance

These are conceptual records, not a deployed database schema. Today they are Python contracts and local JSON artifacts under ignored `.local/ingestion-runs/<run_id>/`. Supabase tables and migration details have not been implemented.

| Object | What it represents | Key link |
| --- | --- | --- |
| Source locator / document | A Markdown path under a stable root alias; `document_id` stays stable when the file's contents change. | `root_alias` + relative path |
| Capture / run | One read of the original bytes, with a fresh `run_id` and `source_sha256`. | Document and exact source revision |
| Parsed document / block | Metadata and ordered structural pieces such as speaker turns, headings, highlights, and personal notes. Excluded spans account for the rest of the source. | Run, source character spans |
| Chunk set / chunk | A fixed or structural view of eligible blocks; each chunk has a variant, version, ID, and exact source spans. | Run, block indices, source spans |
| Enrichment artifact | Chunk context, evidence cards with source quotes, or section/document summaries. | Run, source hash, chunk/source spans, model and prompt versions |
| Embedding spec / batch | Vectors plus provider, model version, dimensions, metric, input recipe, and chunking version. | Run and chunk IDs |
| Eval label / mapping | A manually checked question and required source passage(s), then the chunks that contain those passages. | Document ID and source hash |

`app/ingestion/contracts.py` defines source, block, chunk, and evidence-card records; `run_manifest.py` tracks artifact state and versions; `embeddings.py` defines embedding records. The run manifest distinguishes pending, ready, and failed artifacts. It does not make an incomplete result eligible for search.

The future database needs persistent equivalents for documents, runs, chunks, generated artifacts, and embedding specifications, with an explicit choice of active run and model version for retrieval. Exact tables, indexes, access rules, and migrations remain to be designed. See [INGESTION.md](INGESTION.md) for production of these records, [RETRIEVAL.md](RETRIEVAL.md) for their intended use, and [SYSTEM.md](SYSTEM.md) for the full flow.
