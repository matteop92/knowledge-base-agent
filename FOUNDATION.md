# Personal Knowledge Agent: Foundation

This document defines the system's purpose, decisions, order of work, and **current progress**. Its progress table below is the single place to track which stages work today and what remains; update rows in place rather than adding a history log. [SYSTEM.md](SYSTEM.md) maps the current flow, while [knowledge-agent-architecture.md](knowledge-agent-architecture.md) preserves the original design brief; current user requirements take precedence where they differ.

## Current progress

| Stage | State now | Next proof needed |
| --- | --- | --- |
| 1. Source identity and transcript contract | Stable document IDs, byte hashes, transcript parsing, and exact source spans persist through the selected six-file local profile and the hosted synthetic fixture; returned spans resolve to captured bytes. | Verify the same provenance on a later bounded private hosted load. |
| 2. Mixed Markdown ingestion | Working locally for representative transcripts, notes, articles, highlights, and a book export: routing, source-aligned blocks, both chunk variants, and label mapping. Context, card, summary, and embedding code passes local tests; the selected cards-plus-structural/raw path has a repeatable six-file load and search proof. | Resolve the Stage 5 answer quality failures before widening the corpus; keep unqualified summaries outside the selected profile. |
| 3. Supabase storage and search | Private schema, validated staged loading, guarded active-run/profile publication, and independent exact vector plus FTS searches work on local Supabase. The active local profile has six structural/raw runs with 83 Perplexity vectors and 510 validated GPT-5 mini cards. A localhost-only, read-only viewer now browses selected cards and raw tables in the existing local database. Migration `20260923152944` is hosted; a restricted `knowledge_runtime_hosted` login completed a synthetic fixture load, publication, vector and full-text search, and stored-byte span resolution through the session pooler. The hosted default profile was reset to null; no vault files were loaded there. | Review the existing public `rls_auto_enable()` security-advisor warning before loading private vault data. Resolve Stage 5 answer quality locally, then perform a bounded private hosted load. Keep exact vector search until HNSW recall and query plans justify an index. |
| 4. Live enrichment and ingestion readiness | The six-file local structural/raw profile remains published with 83 vectors and 510 selected GPT-5 mini `evidence_cards_v5` cards. A source-group audit found 56 applicable structured fields in 27 sampled groups; the active cards populate one. The `evidence_cards_v8` 18-chunk screen produced 18/18 ready responses and provisionally populated 47/56 fields, but held-out review found a reversed action-result claim. The candidate was rejected before six-file expansion or selection change. Summaries remain unqualified and excluded. | Keep `v5` selected. Any revised card candidate needs a new source-grounding screen before expansion; a private hosted load remains Stage 3 work. |
| 5. Retrieval API and answer agent | Local hybrid retrieval and the answer core now use prompt v10 with action-schema v2: original-source reads, bounded search/find, retained valid actions when a model includes stray draft fields, a read requirement after two discovery actions, and finite broad-question limits. Selected-card live checks produced Q1/Q2/Q8/Q4 receipts. Q2 exhausted without a substantive answer; Q1 missed essential facets even with six reads at mini/medium; Q8 invented a hiring sequence; Q4 gave generic advice from one read. Citation provenance and local tests do not make these answers acceptable. | Repair the demonstrated answer investigation and synthesis failures within the selected profile and rerun affected pilot questions before accepting a semantic baseline. Stage 5 accounted upper bound is $5.661205 of $8; combined Stage 4 and Stage 5 accounting is $7.123812 of $10. |
| 6. Evaluation and tuning | On six files, 37 retrieval questions include 31 supported questions requiring 51 spans. Active vector coverage is 41/44/47 at K=5/10/20; FTS is 28/40/44; RRF is 39/44/47. Version-3 rubric and 21 submitted human judgments remain intact. Rubric v4 has 86 split checks and 55 exact source-block alternatives verified against six checked source hashes; it awaits user review before freeze. The grader core now checks inspected text against the originals and requires the supporting citation inside a positive grounding excerpt, but has no human calibration or held-out validation. The rejected card candidate was not compared on answers. | Obtain v4 criteria approval, then calibrate and validate the grader on question-grouped held-out cases. The final Stage 5 gate remains failed on known answer-quality defects and lacks new human usefulness judgments. |
| 7. Compiled knowledge and themes experiment | Deferred until the baseline retrieval and answer system has measurable results. | If baseline evaluation shows repeated cross-source synthesis or recurring-theme failures, test a small compiled layer against the same gold questions. |
| 8. Minimal interface and wider corpus | Not built. | Let a user ask a question, inspect cited passages, and expand the corpus after the core path and compiled-layer decision are measured. |

The immediate end-to-end checkpoint is **source → enrichment → stored index → hybrid retrieval → cited answer**. The checked structural/raw and card profile has a complete resumable local ingestion proof and measured hybrid retrieval. Representative repaired live answers have material quality failures, so the answer baseline is unaccepted. Unqualified summaries stay outside the selected profile.

Stage 4 built on the fixed/raw, structural/raw, and structural/contextual comparison as an ingestion baseline. In that comparison, contextual embedding meant a generated chunk-specific note followed by the unchanged structural chunk text. Candidate context models used the same source revisions, structural chunks, context prompt and input recipe, embedding model, checked questions, and exact vector search. The measured Recall@5/10/20, per-question changes, context groundedness, latency, and cost did not justify replacing structural/raw embeddings on this corpus; the focused plan records the results.

Evidence cards and section/document summaries are separate source-linked artifacts, not additional text silently appended to that embedding input. Card comparisons have selected GPT-5 mini; summary comparisons found content coverage but also attribution/factual errors, so the no-invented-fact gate is unresolved. The focused plan records the sample, quality gates, provider routes, results, and spend cap.

The Stage 5 agent has a separate card-search tool, but its effect on answer quality remains unmeasured. Using summaries as a retrieval signal or embedding input requires its own versioned experiment. A search profile selects chunk and embedding versions and required artifacts; it is not a model.

## Outcome

The agent should answer exploratory questions across a mixed personal knowledge base even when the question's wording does not appear in a source. It should find related concepts, inspect the strongest original passages, reason across sources, and cite the passages that support its answer.

The first proof is a question about getting early B2B customers without paid ads. A good answer should discover relevant ideas such as direct outreach, founder-led sales, manual onboarding, networks, and early distribution, and distinguish source evidence from the agent's synthesis.

## System principles

1. **Original files are the authority.** Keep source files unchanged. Generated summaries, context, topics, and evidence cards help discovery but never replace source passages as evidence.
2. **Provenance is continuous.** Every processed artifact must resolve to a document ID, the source hash captured by its ingestion run, and an exact source span. In the future store, one complete run should be active for answers while experimental and historical runs remain available for comparison. Reuploading a file should replace the active derived view only after ingestion succeeds.
3. **Retrieval uses several signals.** Combine embeddings, lexical search, and structured extracted knowledge. Evaluate each signal and their combination against known relevant sources.
4. **The agent can investigate.** It may reformulate a question, search again, inspect neighboring material, and compare sources. The application owns the loop and its limits.
5. **Models are replaceable.** Agent, extraction, embedding, and optional reranking roles are separately configured. The application's thin `ModelClient` owns the stable interface. A direct OpenRouter adapter is the V1 implementation; LiteLLM and local endpoint adapters can be added without changing ingestion, retrieval, or the agent harness. V1 must be capable of running entirely on open-weight models; OpenRouter is an inference gateway, not a requirement to use a proprietary model. Selected agent models must be checked for reliable tool use or structured actions rather than assumed compatible because they share an endpoint.
6. **Behavior is inspectable.** Prompts, chunk boundaries, extraction output, retrieval ranks, tool calls, and citation decisions should be available for debugging and evaluation.
7. **V1 stays small.** Use Python, FastAPI, Supabase Postgres with pgvector and full-text search, and a minimal interface. Add infrastructure only when evaluation shows a need.

## Component boundaries

| Component | Owns | Does not own |
|---|---|---|
| Source library and catalog | Original files, stable document identity, source metadata | Generated knowledge or search ranking |
| Ingestion | Parsing, normalization, structural chunking, enrichment, embeddings, source mapping | Answer generation |
| Processed store | One active run per document, retained experimental runs, extracted knowledge, versioned embedding indexes, artifact relationships | Source-of-truth files |
| Retrieval layer | A small set of semantic, keyword, evidence, document, and source-reading operations | The agent's reasoning policy |
| Agent harness | Tool loop, search policy, evidence sufficiency, answer synthesis, citation requirements | Provider-specific agent runtime |
| `ModelClient` and adapters | Stable model operations and normalized responses; provider-specific calls stay inside adapters | Search, orchestration, or source permissions |
| Evaluation | Labeled questions, retrieval and answer checks, cost/search traces | Production answers |
| Interface | Asking questions and viewing answers, citations, and excerpts | Direct access to the knowledge store |

The single flow diagram lives in [SYSTEM.md](SYSTEM.md). The table above defines ownership; that diagram shows how the components connect and which ones exist today.

## Original file location

The current source library is an external Markdown vault selected through `KNOWLEDGE_SOURCE_ROOT`. It remains the authoritative file library; ingestion captures local snapshots for reproducible runs without moving or editing the originals. The sync provider and how the future interface opens an original file are still open. Sync alone should not be treated as version history.

## Repository shape

The repository keeps application code, prompts, evaluations, and focused plans separate. Private originals live outside it; the database path below is planned:

```text
sources/fixtures/        Shareable synthetic samples; private originals stay outside the repo
app/                     Ingestion, retrieval, agent, model adapter, and API code
prompts/                 Versioned extraction and agent instructions
supabase/migrations/     Planned processed-store schema changes
evals/                   Questions, expected sources, and run results
tests/                   Code and integration checks
docs/plans/              Focused ce:plan outputs for individual work packages
README.md                Setup and operating instructions
SYSTEM.md                Current system flow and links to detail docs
FOUNDATION.md            Purpose, decisions, and current progress
```

## Data relationships to preserve

At the conceptual level, a stable **document** points to an original Markdown file. An **ingestion run** records the source hash, parser, prompts, models, and enough captured source text to reproduce its evaluation. Its **chunks** retain exact source locations. **Context, topics, evidence cards, and summaries** identify the run and source material from which they were derived. The planned store should activate a complete new run for a document only after ingestion succeeds; failed runs must not become searchable. Prior runs may remain for experiments and comparison, but only the active run should be served for normal questions. Their retention and cleanup rules belong to the storage plan.

**Embedding indexes have a different lifecycle.** Each index has an `embedding_spec`: provider, model, model version, dimensions, distance metric, input recipe version, context prompt version, chunking version, and creation time. A changed model, dimension, or recipe requires a new complete embedding index. The old index remains available until the new one is evaluated and selected. The exact schema and cutover mechanics belong to the storage plan.

The Supabase schema plan will settle tables, keys, indexes, and access rules. The initial candidate concepts are documents, ingestion runs, chunks, evidence cards, summaries, topics, embedding specs, and embeddings. Entities should be added only if their retrieval value and identity rules are clear.

## Retrieval and answer path

For each question, the agent forms a few plausible search angles. pgvector search and Postgres full-text search ranked with `ts_rank` run independently; reciprocal rank fusion (RRF) merges their positions into a candidate list. **Evidence-card search is a separate V1 retrieval tool** that the agent chooses when it needs structured examples, tactics, or claims. Cards do not automatically join the RRF pool. The agent reads the most relevant original chunks, searches again when a material angle is missing, and writes an answer grounded in inspected excerpts. Evaluation will test whether the card tool improves answers or merely adds searches.

Postgres's built-in full-text ranking is **not BM25**. It is the agreed V1 lexical baseline. If evaluation shows weak lexical retrieval, a focused plan can test true BM25. Supabase documents a ParadeDB integration in which a logical replica provides BM25 search; that would be an additional service, justified only by measured need. Reranking is also gated by evaluation. Citation validation checks that each cited span supports the nearby claim, not merely that its ID exists.

| Stage | Search stack | Decision gate |
|---|---|---|
| V1 | pgvector + Postgres FTS/`ts_rank` + RRF | Establish retrieval and answer baselines. |
| V2 candidate | pgvector + BM25 + RRF, potentially through ParadeDB | Add only if lexical retrieval is weak enough to justify another service. |
| V3 candidate | Add a reranker | Adopt only if it improves the evals enough to justify latency and cost. |

The retrieval plan will define the exact tool contracts, ranking method, filters, and result budgets. The agent plan will define loop states, stopping rules, tool limits, and citation behavior. Neither belongs in this foundation document.

The table under [Current progress](#current-progress) is the work map for focused plans. Keep each plan small enough to implement and evaluate without claiming the whole system is complete.

## Future compiled knowledge layer

After the baseline retrieval and answer system has been evaluated, a small compiled knowledge layer may maintain durable cross-source themes, concepts, relationships, contradictions, and synthesis. It should complement the evidence layer rather than replace it, and it is intentionally deferred until baseline retrieval and answer metrics exist.

The decision gate is whether the baseline reliably retrieves individual evidence but struggles with recurring themes, cross-document synthesis, contradictions, or repeatedly reconstructing the same concepts. If those failures do not appear, the compiled layer has not earned the added complexity.

Compiled themes remain derived artifacts. Every claim in them must resolve to supporting evidence cards or chunks and ultimately to exact spans in the original sources.

The initial experiment should compare **A. the existing retrieval and agent system** with **B. the same system plus a small set of compiled themes**, using the same gold questions. Measure answer completeness, citation faithfulness, searches and tool calls, latency, and cost. Theme creation, update, merge, split, contradiction, and deprecation rules belong in a later focused plan, not in this foundation document.

## Decisions for focused plans to carry forward

- **First source:** Podcast transcripts. Preserve speaker exchanges, Q&A pairs, complete stories, and topic changes. Later note/book plans should preserve headings and paragraphs.
- **Replacement and experiments:** Reuploading a transcript runs ordinary ingestion and switches its active chunks, cards, summaries, and other derived artifacts only when the new run succeeds. Keep experimental and historical runs long enough to compare chunking, prompts, and embeddings; they are not served in ordinary answers. Long-term source-file version management is outside the first work package.
- **Chunking:** Start with structural chunks around 600-1,000 tokens and roughly 10% overlap. The deliberately simple baseline is fixed 800-token chunks, 10% overlap, and no enrichment. Overlapping source spans need deduplication in retrieval and evaluation.
- **Evidence cards:** Start with `type`, `statement`, nullable `situation`, `action`, and `result`, `principle`, `evidence_span`, `chunk_id`, and `document_id`. Mark `principle` as derived interpretation. `evidence_span` must occur in `chunk.raw_text`; cards that fail deterministic validation never enter search. Missing facts remain null rather than being inferred.
- **Embedding versions:** Record the full `embedding_spec` above and build a complete parallel index when it changes. Compare the new and old indexes on the same evaluation set before switching the active one.
- **Search:** V1 retrieves vector and Postgres FTS/`ts_rank` top-K lists independently and combines ranks with RRF. Evidence cards remain a separate agent-selected tool. BM25 through a separately evaluated search layer and reranking are later candidates, not V1 dependencies.
- **Access:** V1 contains personal transcripts, notes, and books only. Customer-specific data and its authorization model come later.

## Evaluation contract

Build a gold set of roughly 30-50 real questions with manually labeled relevant chunks. Mark the evidence that **must** be found separately from useful additional evidence. Include single-source, multi-source synthesis, recurring-pattern or “find all,” and negative questions with no supporting evidence.

Retrieval evaluation reports Recall@5, Recall@10, and especially Recall@20. Answer evaluation is separate: check whether citations point to exact supporting spans and whether the answer is **supported, complete, balanced, and calibrated**. A topically related span is not a correct citation. Claims about repeated patterns require multiple independent supporting passages. Record searches and token use so improvements can be weighed against cost.

The chunking and context tests isolate one change at a time:

| Experiment | Control | Treatment | Question |
|---|---|---|---|
| A: chunking | Fixed 800-token chunks, 10% overlap, raw text | Structural 600-1,000-token chunks, roughly 10% overlap, raw text | Does chunking improve Recall@20? |
| B: context | Structural chunks, raw text | The same structural chunks with chunk-specific context | Does context improve Recall@20 beyond chunking? |

Fixed 800-token raw chunks remain the overall baseline. The six-file A/B trial held the embedding model and exact vector retrieval fixed: structural/raw improved Recall@20 by two required spans, while adding context lost one of those spans. This is directional evidence for the initial chunk choice, not a final retrieval setting. Separate search comparisons still need to measure semantic search, Postgres FTS, and RRF; a later reranker test holds the winning candidate generation fixed. A focused plan will specify the remaining comparison order, evidence-card on/off test, and exact gold-set labeling rules.

## Choices still open

- Which existing cloud drive, if any, should sync the external vault?
- How should the first UI open an original file: local path, cloud-drive link, or both?

## Boundaries for V1

The intended V1 stack is Python, FastAPI, Supabase Postgres, pgvector, Postgres full-text search, an explicit in-code agent loop, the application's `ModelClient` with a direct OpenRouter adapter, and a simple UI. Every model role can be filled with an open-weight model. LiteLLM is an optional later adapter when multiple gateways or local hosting justify it. The compiled knowledge layer is not a V1 dependency, but V1 provenance must allow future themes to resolve through evidence cards or chunks to exact source spans. A model router, knowledge graph, Neo4j, Kafka, OpenSearch, Kubernetes, and multiple specialized agents require evidence from V1 before they enter the work map.

The source contract, mixed Markdown ingestion, and local storage/search path have code behind them. The Stage 4 focused plan records the bounded live-enrichment trials and completed local ingestion proof.

## Research informing the experiments

- [New Relic's AWS case study](https://aws.amazon.com/blogs/machine-learning/new-relic-transforms-productivity-with-generative-ai-on-aws/) reports that hierarchical chunking worked best in its own tests. That motivates an experiment here rather than prescribing a winner.
- [PDI's AWS case study](https://aws.amazon.com/blogs/machine-learning/how-pdi-built-an-enterprise-grade-rag-system-for-ai-applications-with-aws/) used a 70% chunk-content, 10% overlap, 20% **document-summary** allocation and reported answer approval rising from 60% to 79%. Its reused document summary is distinct from chunk-specific context.
- [Anthropic's contextual retrieval study](https://www.anthropic.com/engineering/contextual-retrieval) evaluated top-20 retrieval failures and reported improvements from context and reranking. Its results motivate Recall@20 and separate context/reranker tests.
- [Qdrant's embedding migration guide](https://qdrant.tech/documentation/tutorials-operations/embedding-model-migration/) shows a parallel-index cutover pattern. [OpenSearch's RRF guide](https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/rrf/) explains why rank fusion works when lexical and vector scores cannot be compared directly.
- [OpenRouter's API documentation](https://openrouter.ai/docs/quickstart) describes its common model endpoint, [Responses endpoint](https://openrouter.ai/docs/api/api-reference/responses/create-responses), and [tool-calling interface](https://openrouter.ai/docs/guides/features/tool-calling). [Supabase's ParadeDB integration](https://supabase.com/partners/paradedb) documents the possible BM25 path, which requires a separate replicated search service.
- [Pinecone's agent knowledge work](https://www.pinecone.io/blog/introducing-nexus-knowledge-engine/) emphasizes source-level provenance for derived artifacts; its [benchmark account](https://www.pinecone.io/blog/nexus-ea-benchmarks/) uses questions and expected answers drawn from the corpus. These are external examples, not quality guarantees for this project.
