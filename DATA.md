# Data and provenance

The original Markdown vault remains authoritative. Local JSON artifacts under ignored `.local/ingestion-runs/<run_id>/` are validated before loading into the versioned private `knowledge` Postgres schema. The migration has been exercised on disposable local pgvector Postgres; no hosted project has been migrated.

| Object | What it represents | Key link |
| --- | --- | --- |
| Source locator / document | A Markdown path under a stable root alias; `document_id` stays stable when the file's contents change. | Unique `root_alias` + relative path in `knowledge.documents` |
| Capture / run | One read of the original bytes, with a fresh `run_id` and `source_sha256`. | `knowledge.runs` keeps the exact byte snapshot, decoded text, and full parsed JSON |
| Parsed document / block | Metadata and ordered structural pieces such as speaker turns, headings, highlights, and personal notes. Excluded spans account for the rest of the source. | Full parsed payload remains linked to one run and source revision |
| Chunk set / chunk | A fixed or structural view of eligible blocks; each chunk has a variant, version, ID, and exact source spans. | `knowledge.chunk_sets` and `knowledge.chunks` retain both views and parent block indices |
| Enrichment artifact | Chunk context, evidence cards with source quotes, or section/document summaries. | Artifact status/version plus source-linked context, card, and summary rows |
| Embedding spec / batch | Vectors plus provider, model version, dimensions, metric, input recipe, and chunking version. | Canonical spec ID and per-run, per-chunk vector with dimension trigger |
| Eval label / mapping | A manually checked question and required source passage(s), then the chunks that contain those passages. | Document ID and source hash |

`app/ingestion/contracts.py` defines source, block, chunk, and evidence-card records; `run_manifest.py` tracks artifact state and versions; `embeddings.py` defines embedding records. The run manifest distinguishes pending, ready, and failed artifacts. It does not make an incomplete result eligible for search.

`knowledge.profiles` selects one chunk variant/version, embedding spec, and FTS configuration. `knowledge.active_runs` names the validated run for each document/profile; `knowledge.corpus_state` selects one profile for ordinary search. Publication checks complete ready vectors and declared enrichment artifacts in a transaction. Historical and staged runs require an explicit query scope. The schema is outside Supabase's exposed API schemas, grants exclude `anon` and `authenticated`, and a separate `knowledge_runtime` role serves trusted Python code. See [INGESTION.md](INGESTION.md) for production of these records, [RETRIEVAL.md](RETRIEVAL.md) for their intended use, and [SYSTEM.md](SYSTEM.md) for the full flow.
