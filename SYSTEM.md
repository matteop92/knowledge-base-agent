# System

The original Markdown vault is the authority. Each derived item must point back to the source file and exact source revision; search and answers are still planned. For stage-by-stage completion and the next checkpoint, read [FOUNDATION.md#current-progress](FOUNDATION.md#current-progress).

```mermaid
flowchart TB
    SRC["Original Markdown vault<br/>source of truth"] --> CAP

    subgraph ING["INGESTION"]
        direction TB
        CAP["Capture<br/>document ID, source hash, snapshot"] --> PARSE["Route and parse<br/>source-aligned blocks"]
        PARSE --> CHUNK["Fixed and structural<br/>raw chunks"]
        CHUNK --> ENRICH["Context, evidence cards,<br/>summaries, embeddings"]
        CHUNK --> EVAL["Map checked questions<br/>to source spans and chunks"]
    end

    subgraph MODELS["MODEL CLIENT: model calls only"]
        direction TB
        CLIENT["ModelClient interface<br/>generate and embed"] --> OPENROUTER["OpenRouter adapter<br/>no live vault run yet"]
    end
    ENRICH -. model calls .-> CLIENT

    subgraph STORE["PROCESSED STORE"]
        DB["Supabase<br/>active run, artifacts, search indexes"]
    end
    CHUNK -. planned load .-> DB
    ENRICH -. planned load .-> DB

    QUESTION["User question"] --> UI["Minimal interface"] --> PLAN
    subgraph HARNESS["AGENT HARNESS: decides what to investigate"]
        direction TB
        PLAN["Choose search angles<br/>and when to search again"]
        READ["Inspect original passages<br/>and check evidence"] --> ANSWER["Write answer with<br/>supported citations"]
    end
    PLAN -. reasoning calls .-> CLIENT

    subgraph RETRIEVAL["RETRIEVAL TOOLS: fetch and rank evidence"]
        direction TB
        SEARCH["Vector + Postgres<br/>full-text search"] --> RRF["RRF combines<br/>their ranked lists"]
        CARDS["Separate evidence-card<br/>search when useful"]
    end
    PLAN --> SEARCH
    PLAN --> CARDS
    DB --> SEARCH
    DB --> CARDS
    RRF --> READ
    CARDS --> READ

    classDef source fill:#dcecff,stroke:#3973b9,color:#132b48
    classDef working fill:#dff5e5,stroke:#30834a,color:#173a24
    classDef coded fill:#fff0c7,stroke:#ad7923,color:#4b330b
    classDef planned fill:#eceef3,stroke:#758097,color:#202938
    class SRC,QUESTION source
    class CAP,PARSE,CHUNK,EVAL working
    class ENRICH,CLIENT,OPENROUTER coded
    class DB,UI,PLAN,READ,ANSWER,SEARCH,RRF,CARDS planned
```

**Green:** working locally. **Amber:** code and prompts tested with fake model responses; no live vault model run. **Gray:** planned. The main path runs from the vault at the top through ingestion and storage, then from a question through the agent harness and retrieval tools to a cited answer.

The **agent harness** owns the decisions: what to search, whether to search again, which passages support an answer, and when to stop. **Retrieval tools** fetch and rank candidates. **ModelClient** only sends generation or embedding requests to a provider; it does not choose search steps or inspect the database.

The local preview writes a source snapshot, parsed document, chunk sets, and run manifest under ignored `.local/ingestion-runs/<run_id>/`. It makes no model calls. Model enrichment is required for the intended product, but is not part of that preview command.

Follow one detail link for the task: [INGESTION.md](INGESTION.md) for input and enrichment, [RETRIEVAL.md](RETRIEVAL.md) for search and answers, or [DATA.md](DATA.md) for records and provenance. [README.md](README.md) has commands; [docs/plans/](docs/plans/) holds plans rather than current-state documentation.
