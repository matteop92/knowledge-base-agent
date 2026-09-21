# System

The original Markdown vault is the authority. Each derived item must point back to the source file and exact source revision; search and answers are still planned. For stage-by-stage completion and the next checkpoint, read [FOUNDATION.md#current-progress](FOUNDATION.md#current-progress).

```mermaid
flowchart TB
    A["1 · READ AND STRUCTURE<br/>External Markdown → document ID, hash, saved snapshot<br/>Route format → blocks with exact source spans"]
    B["2 · CHUNK AND ENRICH<br/>Fixed + structural chunks → model enrichment<br/>ModelClient → OpenRouter for generation and embedding<br/>Context, evidence cards, summaries, vectors<br/>Validate quotes and record versions"]
    C["3 · STORE AND INDEX<br/>Supabase saves a complete active run and linked artifacts<br/>Build vector, full-text, and evidence-card indexes"]
    D["4 · INVESTIGATE A QUESTION<br/>Agent harness chooses search angles and tools<br/>Retrieval: vector + full text → RRF; cards stay separate<br/>Harness searches again when evidence is missing"]
    E["5 · ANSWER FROM SOURCES<br/>Agent harness reads original passages and checks support<br/>It uses ModelClient to synthesize, then verifies citations"]

    A --> B --> C --> D --> E

    classDef working fill:#dff5e5,stroke:#30834a,color:#173a24
    classDef coded fill:#fff0c7,stroke:#ad7923,color:#4b330b
    classDef planned fill:#eceef3,stroke:#758097,color:#202938
    class A working
    class B coded
    class C,D,E planned
```

**Green:** working locally. **Amber:** raw chunks work locally; enrichment code and prompts pass fake-model tests, but no model has run on the vault. **Gray:** planned. A user asks the question after an index has been built.

**ModelClient** handles model calls in stages 2 and 5; the OpenRouter adapter exists, but no live model has been selected. The **agent harness** controls stages 4 and 5: it chooses searches, decides whether the evidence is enough, and checks citations. The retrieval tools in stage 4 fetch and rank candidates; they do not decide what the answer should say. Today's checked questions map to source spans and chunks; retrieval and answer scoring come later.

The local preview writes a source snapshot, parsed document, chunk sets, and run manifest under ignored `.local/ingestion-runs/<run_id>/`. It makes no model calls. Model enrichment is required for the intended product, but is not part of that preview command.

Follow one detail link for the task: [INGESTION.md](INGESTION.md) for input and enrichment, [RETRIEVAL.md](RETRIEVAL.md) for search and answers, or [DATA.md](DATA.md) for records and provenance. [README.md](README.md) has commands; [docs/plans/](docs/plans/) holds plans rather than current-state documentation.
