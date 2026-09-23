# System

The original Markdown vault is the authority. A file is processed into an index once, then many questions can use that index; these are two different sequences. Every derived item must point back to the exact source revision. For stage-by-stage completion and the next checkpoint, read [FOUNDATION.md#current-progress](FOUNDATION.md#current-progress).

## A. Build the index from a file

This runs when a Markdown file is ingested. The two chunking paths produce separate versions of the same source; the eval check is a parallel check, not a required step for every file.

```mermaid
flowchart TB
    SRC["1 · Original Markdown file"] --> CAP["2 · Capture ID, byte hash,<br/>and source snapshot"]
    CAP --> PARSE["3 · Route format and parse<br/>transcript, highlights, book, article, or note"]
    PARSE --> BLOCKS["4 · Source-aligned blocks,<br/>excluded spans, and diagnostics"]
    BLOCKS --> FIXED["5a · Fixed raw chunks<br/>800 tokens, 80 overlap"]
    BLOCKS --> STRUCT["5b · Structural raw chunks<br/>target 600–1000 tokens; preserve structure"]
    FIXED --> CHUNKS["6 · Two versioned chunk sets<br/>with exact source spans"]
    STRUCT --> CHUNKS
    CHUNKS --> LABELS["Eval check · Map manually checked<br/>answer spans to both chunk sets"]
    CHUNKS --> CLIENT["7 · ModelClient interface<br/>OpenRouter adapter for model calls"]
    CLIENT --> CONTEXT["8a · Chunk context"]
    CLIENT --> CARDS["8b · Evidence cards<br/>with exact quotes"]
    CLIENT --> SUMMARIES["8c · Section and<br/>document summaries"]
    CLIENT --> EMBEDDINGS["8d · Raw or contextual<br/>embeddings"]
    CONTEXT --> CHECK["9 · Validate source links,<br/>versions, and run status"]
    CARDS --> CHECK
    SUMMARIES --> CHECK
    EMBEDDINGS --> CHECK
    CHECK --> STORE["10 · Supabase stores a complete run<br/>and builds search indexes"]

    classDef source fill:#dcecff,stroke:#3973b9,color:#132b48
    classDef working fill:#dff5e5,stroke:#30834a,color:#173a24
    classDef model fill:#fff0c7,stroke:#ad7923,color:#4b330b
    classDef planned fill:#eceef3,stroke:#758097,color:#202938
    class SRC source
    class CAP,PARSE,BLOCKS,FIXED,STRUCT,CHUNKS,LABELS,STORE working
    class CLIENT,CONTEXT,CARDS,SUMMARIES,EMBEDDINGS,CHECK model
```

**Green** runs locally, including both chunk variants, label mapping, and the private storage/search path. **Amber** has code and prompts tested with fake model responses; raw-chunk embeddings have also been compared live on six checked vault files, while extraction, contexts, cards, and summaries have no live-vault trial. `ModelClient` sends generation and embedding requests; it does not choose what to search.

## B. Answer a question from the index

This starts later, when a user asks a question against the active run stored in A. The agent harness controls the investigation; retrieval tools only return evidence candidates.

```mermaid
flowchart TB
    QUESTION["1 · User asks a question"] --> PLAN["2 · Agent harness chooses<br/>search angles and tools"]
    PLAN --> HYBRID["3a · Retrieval tools run<br/>vector + Postgres full-text search"]
    HYBRID --> RRF["4 · RRF combines the<br/>two ranked result lists"]
    PLAN --> CARDSEARCH["3b · Evidence-card search<br/>is a separate tool"]
    RRF --> INSPECT["5 · Agent harness reads original<br/>source spans and checks gaps"]
    CARDSEARCH --> INSPECT
    INSPECT --> MODEL["6 · Same ModelClient interface<br/>helps synthesize the answer"]
    MODEL --> CITE["7 · Agent harness verifies each<br/>citation against its passage"]
    CITE --> ANSWER["8 · Answer with supported citations"]

    classDef input fill:#dcecff,stroke:#3973b9,color:#132b48
    classDef harness fill:#eee4fa,stroke:#7752a6,color:#35204f
    classDef retrieval fill:#e1f1f5,stroke:#37839a,color:#173d49
    classDef model fill:#fff0c7,stroke:#ad7923,color:#4b330b
    classDef output fill:#eceef3,stroke:#758097,color:#202938
    class QUESTION input
    class PLAN,INSPECT,CITE harness
    class HYBRID,RRF,CARDSEARCH retrieval
    class MODEL model
    class ANSWER output
```

The question-answer path as a whole is **planned**. Independent vector and full-text candidate queries work locally; rank fusion, the agent harness, and answer citations have not been built. Purple boxes are the agent harness, teal boxes are retrieval tools, and amber is the shared ModelClient. If inspected passages leave a gap, the harness repeats step 2 before answering. The checked questions now measure first-pass candidate recall; answer quality remains unevaluated.

The local preview writes a source snapshot, parsed document, chunk sets, and run manifest under ignored `.local/ingestion-runs/<run_id>/`. It makes no model calls. Model enrichment is required for the intended product, but is not part of that preview command.

Follow one detail link for the task: [INGESTION.md](INGESTION.md) for input and enrichment, [RETRIEVAL.md](RETRIEVAL.md) for search and answers, or [DATA.md](DATA.md) for records and provenance. [README.md](README.md) has commands; [docs/plans/](docs/plans/) holds plans rather than current-state documentation.
