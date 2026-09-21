# System

The original Markdown vault is the authority. Each derived item must point back to the source file and exact source revision; search and answers are still planned. For stage-by-stage completion and the next checkpoint, read [FOUNDATION.md#current-progress](FOUNDATION.md#current-progress).

```mermaid
flowchart TB
    SOURCE["SOURCE<br/>Original Markdown vault"]
    INGEST["INGESTION<br/>Capture → parse → source-linked chunks"]
    ENRICH["MODEL ENRICHMENT<br/>Context, evidence cards, summaries, embeddings<br/>Calls ModelClient → OpenRouter"]
    STORE["PROCESSED STORE<br/>Supabase and searchable indexes"]
    ASK["QUESTION<br/>User asks through the interface"]
    PLAN["AGENT HARNESS · investigate<br/>Choose searches and decide when to search again<br/>Uses ModelClient for reasoning"]
    SEARCH["RETRIEVAL TOOLS · find evidence<br/>Vector + full-text results → RRF<br/>Evidence-card search stays separate"]
    CHECK["AGENT HARNESS · verify<br/>Read original passages and check support"]
    ANSWER["ANSWER<br/>Write with exact source citations"]

    SOURCE --> INGEST --> ENRICH --> STORE --> ASK --> PLAN --> SEARCH --> CHECK --> ANSWER

    classDef source fill:#dcecff,stroke:#3973b9,color:#132b48
    classDef working fill:#dff5e5,stroke:#30834a,color:#173a24
    classDef coded fill:#fff0c7,stroke:#ad7923,color:#4b330b
    classDef planned fill:#eceef3,stroke:#758097,color:#202938
    class SOURCE,ASK source
    class INGEST working
    class ENRICH coded
    class STORE,PLAN,SEARCH,CHECK,ANSWER planned
```

**Green:** working locally. **Amber:** code and prompts tested with fake model responses; no live vault model run. **Gray:** planned. The blue boxes are the external source and the user's question. Each arrow means “next in the end-to-end journey”; the user asks after the index is ready.

The **agent harness** owns the decisions: what to search, whether to search again, which passages support an answer, and when to stop. **Retrieval tools** fetch and rank candidates. **ModelClient** is shared by enrichment and the harness; it only sends generation or embedding requests to a provider. It does not choose search steps or inspect the database. Today's checked questions map to source spans and chunks; retrieval and answer scoring come later.

The local preview writes a source snapshot, parsed document, chunk sets, and run manifest under ignored `.local/ingestion-runs/<run_id>/`. It makes no model calls. Model enrichment is required for the intended product, but is not part of that preview command.

Follow one detail link for the task: [INGESTION.md](INGESTION.md) for input and enrichment, [RETRIEVAL.md](RETRIEVAL.md) for search and answers, or [DATA.md](DATA.md) for records and provenance. [README.md](README.md) has commands; [docs/plans/](docs/plans/) holds plans rather than current-state documentation.
