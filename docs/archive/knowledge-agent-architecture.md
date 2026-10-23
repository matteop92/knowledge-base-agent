# Personal Knowledge Agent
## V1 Architecture and Build Plan

> **Archived design brief.** This is the first design for the project, kept for its rationale. The built system differs: evidence cards and chunk summaries were tried and removed, and answers rely on chunk retrieval alone. For the current design read [architecture.md](../architecture.md) and [decisions.md](../decisions.md).

### Goal

Build a personal knowledge agent that can answer questions across a mixed knowledge base, including:

- Markdown notes
- Podcast and meeting transcripts
- Books
- Book and podcast summaries
- Internal documentation
- Project documentation
- Customer-specific information

The system should go beyond simple RAG. It should combine:

- keyword search
- semantic search
- structured knowledge extracted during ingestion
- agentic multi-step retrieval
- reasoning over multiple sources
- citations back to the original source

The core use case is exploratory questions where the exact wording may never appear in the corpus.

Example:

> I am trying to get the first 20 customers for a B2B startup without paid ads. What examples, ideas, or evidence from my knowledge base could help?

The agent should be able to search across concepts such as founder-led sales, cold outreach, manual onboarding, personal networks, early customer acquisition, and related ideas, even if the user did not use those exact terms.

---

# High-Level Architecture

```text
                         ┌──────────────────────┐
                         │      Simple UI       │
                         │  Web app / local UI  │
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │    Agent Service     │
                         │  Python + FastAPI    │
                         │                      │
                         │ planning / tool loop │
                         │ search strategy      │
                         │ citation handling    │
                         └──────────┬───────────┘
                                    │
                    ┌───────────────┼────────────────┐
                    │               │                │
                    ▼               ▼                ▼
             semantic search   lexical search   structured search
                    │               │                │
                    └───────────────┼────────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │          Supabase             │
                    │                               │
                    │ documents                     │
                    │ chunks                        │
                    │ embeddings                    │
                    │ evidence cards                │
                    │ topics                        │
                    │ entities                      │
                    │ document summaries            │
                    │ corpus themes                 │
                    │ provenance / citations        │
                    │ Postgres full-text indexes    │
                    │ pgvector                      │
                    └───────────────┬───────────────┘
                                    ▲
                                    │
                    ┌───────────────┴───────────────┐
                    │       Ingestion Worker        │
                    │                               │
                    │ parse                         │
                    │ normalize                     │
                    │ semantic chunk                │
                    │ enrich chunks                 │
                    │ extract evidence              │
                    │ summarize                     │
                    │ embed                         │
                    │ index                         │
                    └───────────────┬───────────────┘
                                    ▲
                                    │
                         ┌──────────┴───────────┐
                         │    Source Library    │
                         │                     │
                         │ Markdown            │
                         │ PDF / EPUB / TXT    │
                         │ transcripts         │
                         │ summaries           │
                         │ notes               │
                         └─────────────────────┘
```

For V1, source files can live in a local or GitHub `/sources` directory.

Later, raw files can move to Supabase Storage, S3, Google Drive, SharePoint, or other company systems while the knowledge index remains separate.

---

# Core Principle

Do not treat the system as:

```text
question
→ vector search
→ nearest chunks
→ answer
```

Build it as:

```text
question
      ↓
agent decides what it needs to investigate
      ↓
multiple retrieval tools
      ↓
semantic chunks
keyword matches
evidence cards
topics
document summaries
corpus themes
      ↓
agent reads promising evidence
      ↓
agent can reformulate and search again
      ↓
answer with citations
```

The agent researches the corpus instead of running one similarity search.

---

# 1. Source Layer

Keep the original source unchanged.

Example repository structure:

```text
/sources
    /transcripts
    /books
    /notes
    /summaries
    /internal-docs
```

Every source should receive a stable `document_id`.

Store basic metadata:

```yaml
document_id:
source_type:
title:
author:
date:
source_path:
customer:
project:
tags:
```

Not every field needs to exist for every document.

The original source remains the ultimate evidence.

---

# 2. Ingestion Layer

The ingestion worker runs whenever a new document is added or changed.

Pipeline:

```text
source file
   ↓
extract / normalize text
   ↓
identify document structure
   ↓
semantic chunking
   ↓
generate chunk-specific context
   ↓
extract structured knowledge
   ↓
create embeddings
   ↓
write everything to Supabase
```

## Semantic chunking

Avoid fixed 500-token cuts where possible.

Prefer boundaries such as:

- topic changes
- headings
- speaker turns
- question + answer
- complete examples or stories
- paragraphs that form one argument

A chunk can still have a target token range, but semantic coherence matters more than exact size.

Initial V1 target:

```text
~600 to 1,000 tokens
```

with small overlap only where useful.

---

# 3. What to Store for Every Chunk

Each chunk should preserve both raw evidence and derived information.

Example:

```yaml
chunk_id:
document_id:

raw_text:
  Original text copied exactly from source.

context:
  Short LLM-generated explanation of what this passage is about
  and why it matters within the larger document.

topics:
  - founder-led sales
  - customer acquisition
  - cold outreach

entities:
  people:
  companies:
  products:
  projects:

evidence_cards:
  - type: example
    situation:
    action:
    result:
    principle:

source_location:
  page:
  heading:
  timestamp_start:
  timestamp_end:
```

Do not replace raw text with generated summaries.

Derived fields exist to improve retrieval. Raw text remains the evidence.

---

# 4. Evidence Cards

Evidence cards are compact reusable knowledge units extracted during ingestion.

Example:

```yaml
type: example

situation:
  Founder needed initial users before having a scalable acquisition channel.

action:
  Personally contacted roughly 200 potential customers.

result:
  Around 15 became early customers.

principle:
  Manual founder-led outreach can create learning and early traction
  before scalable acquisition exists.

source:
  document_id: ...
  chunk_id: ...
```

Possible card types:

```text
example
principle
claim
counterexample
framework
tactic
mistake
decision
lesson
quote-worthy insight
```

Keep this list small initially.

The point is to make useful ideas searchable even when the user's vocabulary differs from the original source.

---

# 5. Document-Level Knowledge

Each document should also have higher-level representations.

Store:

```text
document summary
main themes
important examples
important entities
key claims
important disagreements or caveats
```

This gives the agent a cheap way to decide whether an entire document deserves deeper reading.

---

# 6. Corpus-Level Knowledge

This can be added after V1 ingestion works reliably.

Periodically analyze the whole corpus to identify recurring themes.

Example:

```yaml
theme:
  founder-led early customer acquisition

related_concepts:
  - cold outreach
  - personal introductions
  - manual onboarding
  - direct sales
  - community building

supporting_evidence:
  - chunk_1827
  - chunk_4139
  - chunk_7201

contradictory_evidence:
  - chunk_9211
```

This layer helps with questions that require synthesis across many documents.

---

# 7. Supabase

Use Supabase as the central processed knowledge store.

Suggested V1 tables:

```text
documents
chunks
evidence_cards
topics
chunk_topics
entities
chunk_entities
document_summaries
```

Add later:

```text
corpus_themes
theme_evidence
retrieval_logs
evaluations
```

## Search

Use both:

```text
pgvector
```

for semantic search

and:

```text
Postgres full-text search
```

for lexical / keyword search.

The agent should be able to combine both.

---

# 8. Agent Service

The actual agent should live in its own Python service.

Suggested stack:

```text
Python
FastAPI
OpenAI Responses API / Agents SDK
```

Claude can later be supported as another model provider.

The agent service owns the harness.

The harness defines:

- system instructions
- tools
- search strategy
- maximum search iterations
- evidence requirements
- citation requirements
- when to broaden a query
- when to inspect the original source
- when enough evidence has been collected

The LLM provides reasoning.

The application code controls what the LLM is allowed to do.

## Model optionality

Treat models as replaceable dependencies.

Keep at least four independently configurable model roles:

```text
agent_model        reasoning + tool use
ingestion_model    chunk context + evidence extraction
embedding_model    vector representations
reranker_model     optional candidate reranking
```

For example, the agent might use a strong hosted model while ingestion uses a cheaper model, embeddings use a dedicated embedding model, and reranking uses a separate small model.

Put a thin internal interface in front of model calls, for example:

```python
class ModelClient:
    async def generate(...): ...
    async def generate_structured(...): ...
    async def run_with_tools(...): ...
```

Use LiteLLM behind this interface initially because it offers a common API over many hosted and local providers, including OpenAI, Anthropic, Hugging Face, Ollama, OpenRouter and others.

Do not scatter provider-specific SDK calls through the codebase.

Model selection should come from configuration, for example:

```yaml
models:
  agent: openrouter/...
  ingestion: ollama/...
  embeddings: ...
  reranker: ...
```

The rest of the application should not care which provider serves the model.

For the agent role, require the selected model to support reliable tool use or structured outputs. If a future open-source model lacks native tool calling, allow an adapter to translate structured JSON actions into the same internal tool-call format.

A model router is not required for V1. The architecture should simply make it possible to add one later without rewriting ingestion, retrieval or the agent harness.

The application code controls what the LLM is allowed to do.

---

# 9. Agent Tools

Start with a small toolset.

```python
semantic_search(query, filters=None)

keyword_search(query, filters=None)

search_evidence_cards(query, filters=None)

search_documents(query, filters=None)

read_chunk(chunk_id)

read_surrounding_chunks(chunk_id)

read_document_summary(document_id)

read_document(document_id)
```

Later:

```python
search_topics(...)
search_entities(...)
search_corpus_themes(...)
find_related_evidence(...)
```

Avoid exposing dozens of tiny database functions directly to the model.

Give the model a few strong retrieval primitives.

---

# 10. Agent Retrieval Loop

Example user question:

> What ideas from my knowledge base could help me reach the first 20 customers for a B2B startup without paid advertising?

Possible agent behavior:

```text
1. Interpret the information need.

2. Generate several retrieval angles:
   - first customers
   - founder-led sales
   - cold outreach
   - manual customer acquisition
   - personal networks
   - early distribution
   - doing things that do not scale

3. Search semantic index.

4. Search keyword index.

5. Search evidence cards.

6. Merge + rerank candidates.

7. Read the strongest original chunks.

8. Discover another useful concept:
   community-led acquisition.

9. Search again.

10. Gather enough independent examples.

11. Produce synthesis.

12. Cite each important example back to its source.
```

The multi-step loop is the main difference from ordinary RAG.

---

# 11. Interface

Keep V1 extremely simple.

Possible options:

```text
Next.js web chat
Streamlit
simple local web interface
CLI
```

A web interface can live on Vercel while the Python agent API runs elsewhere.

The UI should show:

```text
user question
answer
sources
expandable supporting excerpts
```

Eventually add:

```text
filters
source type
project
customer
date
author
topic
```

---

# 12. Hosting

A practical V1:

```text
GitHub
    source code
    optional source files

Supabase
    Postgres
    pgvector
    full-text search
    derived knowledge

Railway / Cloud Run
    ingestion worker
    agent API

Model providers
    OpenAI / Anthropic / OpenRouter / local or self-hosted models

LiteLLM / model adapter
    common model interface

Vercel
    simple UI
```

The ingestion worker and agent service can initially live in the same Python codebase and even the same deployment.

Split them only when needed.

---

# 12A. Model Layer and Provider Independence

The model layer should sit behind the agent harness rather than define it.

```text
                         Agent Harness
                              │
                              ▼
                    Internal ModelClient
                              │
                              ▼
                       LiteLLM adapter
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
       OpenAI              Anthropic          OpenRouter
                                                  │
                                      ┌───────────┴───────────┐
                                      ▼                       ▼
                                hosted models          local/self-hosted
                                                       Ollama / compatible
```

The retrieval stack remains unchanged when the model changes.

```text
Supabase / pgvector / FTS
            ↓
retrieval tools
            ↓
agent harness
            ↓
ModelClient
            ↓
whichever model is configured
```

V1 should use a simple configuration file or environment variables rather than automatic routing.

Example:

```yaml
models:
  agent:
    provider: openrouter
    model: ...
  ingestion:
    provider: ollama
    model: ...
  embeddings:
    provider: ...
    model: ...
  reranker:
    enabled: false
    provider: ...
    model: ...
```

Later, the same abstraction can support:

- cost-based routing
- fallback models
- task-based routing
- local models for sensitive data
- expensive models only for difficult queries
- A/B testing models in evals

Do not let model-provider concerns leak into the ingestion, database, retrieval or UI layers.

---

# 13. Repository Structure

Suggested starting structure:

```text
knowledge-agent/

    README.md

    sources/
        transcripts/
        books/
        notes/
        summaries/

    app/
        api/
        agent/
        retrieval/
        ingestion/
        models/

    prompts/
        chunk_context.md
        evidence_extraction.md
        document_summary.md
        agent_system.md

    migrations/
        supabase/

    evals/
        questions.yaml

    tests/

    .env.example
```

---

# 14. Evaluation

Do not wait until the system feels finished.

Create 20 to 30 questions where you already know what good retrieval should find.

Examples:

```text
Find examples of founders getting early customers manually.

What arguments in the corpus challenge the idea that paid ads are necessary?

What advice appears repeatedly about difficult negotiations?

Find cases where someone changed strategy after talking directly to customers.
```

Evaluate separately:

```text
Did retrieval find the right evidence?

Did it miss important evidence?

Did the final answer accurately represent the evidence?

Were citations correct?

Did the agent waste searches or tokens?
```

This will tell us whether chunking, extraction, retrieval, or reasoning needs improvement.

---

# 15. What Not to Add Yet

Do not add these to V1 unless an actual retrieval problem requires them:

```text
knowledge graph
Neo4j
Kafka
OpenSearch
multiple specialized agents
model router
Kubernetes
complex event pipelines
```

They solve real problems at larger scale, but they also create complexity.

---

# 16. Future Organization-Level Architecture

When this grows from a personal corpus into company knowledge:

```text
Google Drive
Slack
Notion
CRM
GitHub
meeting transcripts
customer systems
internal databases
        ↓
connectors
        ↓
normalization + permissions
        ↓
knowledge processing
        ↓
search / vector indexes
        ↓
knowledge graph where useful
        ↓
agent retrieval layer
```

At that point, a knowledge graph can sit beside the chunk/index system.

Example:

```text
Customer A
   ├── Project Alpha
   │      ├── Meeting 128
   │      ├── Proposal.pdf
   │      └── Person Sarah
   │
   └── Support Issue 42
```

Vector search answers:

> Which passages discuss negotiation problems similar to this one?

The graph answers:

> Which meetings, people, documents, and projects are connected to Customer A?

They complement each other.

A graph database should not automatically replace the chunk/vector store.

---

# V1 Success Criteria

The first useful version should:

1. Accept Markdown and text-based source files.
2. Normalize and ingest them.
3. Create semantic chunks.
4. Generate chunk-specific context.
5. Extract evidence cards.
6. Create embeddings.
7. Support semantic + keyword retrieval.
8. Give an LLM retrieval tools.
9. Allow the LLM to run several searches when useful.
10. Produce answers with traceable sources.
11. Have a minimal usable chat interface.
12. Include a small evaluation set.

Once this works reliably, expand the corpus and architecture.


---
