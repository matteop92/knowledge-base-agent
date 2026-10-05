# Personal Knowledge Agent: Foundation

This document defines the system's purpose, decisions, order of work, and **current progress**. Its progress table below is the single place to track which stages work today and what remains; update rows in place rather than adding a history log. [SYSTEM.md](SYSTEM.md) maps the current flow, while [knowledge-agent-architecture.md](knowledge-agent-architecture.md) preserves the original design brief; current user requirements take precedence where they differ.

## Current progress

| Stage | State now | Next proof needed |
| --- | --- | --- |
| 1. Source identity and transcript contract | Stable document IDs, byte hashes, transcript parsing, and exact source spans persist through the selected six-file local profile and the hosted synthetic fixture; returned spans resolve to captured bytes. | Verify the same provenance on a later bounded private hosted load. |
| 2. Mixed Markdown ingestion | Working locally for representative transcripts, notes, articles, highlights, and a book export: routing, source-aligned blocks, both chunk variants, and label mapping. Context, summary, and embedding code passes local tests. The selected six-file local profile uses structural/raw chunks and raw embeddings. | Resolve Stage 5 answer quality before widening the corpus; keep unqualified summaries outside the selected profile. |
| 3. Supabase storage and search | Private schema, validated staged loading, guarded active-run/profile publication, and independent exact vector plus FTS searches work on local Supabase. The selected local profile `stage5-card-free-20261002` reuses six runs and 83 Perplexity vectors, requires no enrichment artifacts, and passed the 41/44/47 of 51 live vector recall proof. The loader ignores card artifacts in historical manifests; existing local card rows remain unused. A restricted hosted login completed a synthetic fixture proof, and the hosted default profile was reset to null; no vault files were loaded there. | Review the existing public `rls_auto_enable()` security-advisor warning before loading private vault data. Resolve Stage 5 answer quality locally, then perform a bounded private hosted load. Keep exact vector search until HNSW recall and query plans justify an index. |
| 4. Live enrichment and ingestion readiness | Card generation and its check pass have been removed. Chunk context, summaries, and embeddings remain available. The six-file structural/raw local profile reuses previously loaded vectors without requiring any enrichment artifact. Summaries remain unqualified and excluded. Stage 4 accounted upper bound is $1.664654 of $3.50. | Reassess summary factuality only if a later experiment needs summaries; a private hosted load remains Stage 3 work. |
| 5. Retrieval API and answer agent | Local hybrid retrieval and the answer core use prompt v12 with action-schema v3. The host searches before the first model turn. Source-aware search matches published title, author, guest, and tags and reserves up to five chunks for each of three named files; candidates show title, people, rank, and preview. A read inspects every span while citations stay span-level. Card search and its budget are removed. The selected six-file profile's Q8 first live search returned both the Bending Spoons interview and Sivers book, with at least five chunks from each. The U3 live vector recall repeat passed at 41/44/47 of 51. U4 finishing changes record complete-with-gaps as partial and count refused actions; the prompt prohibits closing offers/questions. Local suites pass. A bounded one-turn correction now handles malformed structured output even on the first model call; agent tests pass. A fresh Q8 batch completed five of five and cited both named sources each time, but agent review against frozen v3 found mean essential coverage 1.3/3, below the earlier with-card 2.33/3. It misses or only implies Ferrari’s urgent-hiring caution and misattributes a reader note about question-led coaching to Sivers. U4 stopped at the quality gate after Q1 round one; Q2 was interrupted with a $1 conservative reservation and no receipt. The private assessment is .local/stage5-u4-q8-corrected-review.json. No card-free answer baseline has been accepted. | The user chose to develop the full revised rubric before further performance evaluation. Keep the Q8 receipts and quality concerns for comparison; do not resume the live baseline yet or add a new refusal rule. |
| 6. Evaluation and tuning | On six files, 37 retrieval questions include 31 supported questions requiring 51 spans. Active vector coverage is 41/44/47 at K=5/10/20; FTS is 28/40/44; RRF is 39/44/47. Version-3 rubric and 21 submitted human judgments remain intact. The original 91-check v4 draft remains unchanged. A separate restructuring proposal has 92 checks across coverage, grounding, and example, including 35 primary coverage ideas and 17 parent-dependent details; question wording, source mappings, and six snapshot hashes are verified. Q2.C2 is a detail of C1, the Q4 breathing intervention is one check, Q3 accepts explicitly connected source examples, and optional content is defined as separately reported without score impact. It awaits review of remaining primary/detail choices and Q4/Q5 example boundaries and is not yet supported by the loader or grader. The grader core checks inspected text against originals but has no human calibration or held-out validation. | Keep Q1.C2 primary as approved. The user chose to work on the full three-dimension checklist before further answer-performance evaluation. Resolve the remaining dependency and example choices, then integrate the approved structure with the loader and grader. Then widen evidence alternatives, freeze the rubric with the user's approval, and calibrate the grader on question-grouped held-out cases. The Stage 5 gate still needs useful answers, no material grounding failure, and at least 70% of each question's required points covered; it remains unaccepted until the card-free baseline is reviewed. |
| 7. Compiled knowledge and themes experiment | Deferred until the baseline retrieval and answer system has measurable results. | If baseline evaluation shows repeated cross-source synthesis or recurring-theme failures, test a small compiled layer against the same gold questions. |
| 8. Minimal interface and wider corpus | Not built. | Let a user ask a question, inspect cited passages, and expand the corpus after the core path and compiled-layer decision are measured. |

The immediate end-to-end checkpoint is **source → enrichment → stored index → hybrid retrieval → cited answer**. The selected structural/raw profile has a complete local ingestion and retrieval proof without requiring enrichment artifacts. The card-free answer baseline, rubric freeze, and final gate remain open. Unqualified summaries stay outside the selected profile.

Stage 4 built on the fixed/raw, structural/raw, and structural/contextual comparison as an ingestion baseline. In that comparison, contextual embedding meant a generated chunk-specific note followed by the unchanged structural chunk text. Candidate context models used the same source revisions, structural chunks, context prompt and input recipe, embedding model, checked questions, and exact vector search. The measured Recall@5/10/20, per-question changes, context groundedness, latency, and cost did not justify replacing structural/raw embeddings on this corpus; the focused plan records the results.

Section/document summaries remain a separate source-linked experiment. The prior card experiment and its private receipts are retained as historical evidence, while the running system uses chunk retrieval alone. A search profile selects chunk and embedding versions and any required artifacts; it is not a model.

The Stage 5 agent shows each candidate chunk's source title, people, rank, and preview, reads original source spans, and checks inline citations against inspected passages. Memory across turns and cross-source theme connections remain later work.

## Outcome

The agent should answer exploratory questions across a mixed personal knowledge base even when the question's wording does not appear in a source. It should find related concepts, inspect the strongest original passages, reason across sources, and cite the passages that support its answer.

The first proof is a question about getting early B2B customers without paid ads. A good answer should discover relevant ideas such as direct outreach, founder-led sales, manual onboarding, networks, and early distribution, and distinguish source evidence from the agent's synthesis.

## System principles

1. **Original files are the authority.** Keep source files unchanged. Generated summaries and context may help discovery but never replace source passages as evidence.
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

At the conceptual level, a stable **document** points to an original Markdown file. An **ingestion run** records the source hash, parser, prompts, models, and enough captured source text to reproduce its evaluation. Its **chunks** retain exact source locations. **Context and summaries** identify the run and source material from which they were derived. The store activates complete runs for a document only after validation; failed runs are not searchable. Historical runs remain available for experiments, while only the active run serves normal questions.

**Embedding indexes have a different lifecycle.** Each index has an `embedding_spec`: provider, model, model version, dimensions, distance metric, input recipe version, context prompt version, chunking version, and creation time. A changed model, dimension, or recipe requires a new complete embedding index. The old index remains available until the new one is evaluated and selected. The exact schema and cutover mechanics belong to the storage plan.

The private Supabase schema stores documents, runs, chunks, context, summaries, embedding specs, and vectors. Its unused card table remains for schema compatibility; no running code writes or reads it.

## Retrieval and answer path

For each question, the agent forms a few plausible search angles. pgvector search and Postgres full-text search ranked with `ts_rank` run independently; reciprocal rank fusion (RRF) merges their positions into a candidate list. The agent reads the most relevant original chunks, searches again when a material angle is missing, and writes an answer grounded in inspected excerpts.

Postgres's built-in full-text ranking is **not BM25**. It is the agreed V1 lexical baseline. If evaluation shows weak lexical retrieval, a focused plan can test true BM25. Supabase documents a ParadeDB integration in which a logical replica provides BM25 search; that would be an additional service, justified only by measured need. Reranking is also gated by evaluation. Citation validation checks that each cited span supports the nearby claim, not merely that its ID exists.

| Stage | Search stack | Decision gate |
|---|---|---|
| V1 | pgvector + Postgres FTS/`ts_rank` + RRF | Establish retrieval and answer baselines. |
| V2 candidate | pgvector + BM25 + RRF, potentially through ParadeDB | Add only if lexical retrieval is weak enough to justify another service. |
| V3 candidate | Add a reranker | Adopt only if it improves the evals enough to justify latency and cost. |

The retrieval plan will define the exact tool contracts, ranking method, filters, and result budgets. The agent plan will define loop states, stopping rules, tool limits, and citation behavior. Neither belongs in this foundation document.

The table under [Current progress](#current-progress) is the work map for focused plans. Keep each plan small enough to implement and evaluate without claiming the whole system is complete.

## What should complement chunk retrieval (later work)

Evidence cards were removed: a card restated a sentence already in an embedded chunk, gave only a modest gain on broad questions, and cost about 70% more per question. The research note in `docs/research/2026-10-02-retrieval-complement-research.md` found little published support for sentence-level or LLM-written index layers when chunk retrieval is already strong. Nothing replaces cards in the current plan. The intended use is a conversation over hundreds of files, including whole books, where follow-up questions are normal and one answer need not be exhaustive. Three gaps follow from that use and have not been tested:

- **Memory across turns.** Each question currently starts from an empty ledger. A follow-up should build on the sources already read.
- **Connections across sources.** Search finds what matches the question's words; it does not surface a related idea in another source. Candidates include related-chunk links from the existing embeddings and a theme layer.
- **Questions about the whole vault.** Recurring themes and "find all" questions need more passages than a top-20 search returns.

Before building any of these, make the evaluation match the use: a larger slice of the vault and questions that include follow-up chains and cross-source or vault-wide questions. The compiled knowledge layer below is one candidate for the second and third gaps and stays gated on that evidence.

## Future compiled knowledge layer

After the baseline retrieval and answer system has been evaluated, a small compiled knowledge layer may maintain durable cross-source themes, concepts, relationships, contradictions, and synthesis. It should complement the evidence layer rather than replace it, and it is intentionally deferred until baseline retrieval and answer metrics exist.

The decision gate is whether the baseline reliably retrieves individual evidence but struggles with recurring themes, cross-document synthesis, contradictions, or repeatedly reconstructing the same concepts. If those failures do not appear, the compiled layer has not earned the added complexity.

Compiled themes remain derived artifacts. Every claim in them must resolve to supporting chunks and ultimately to exact spans in the original sources.

The initial experiment should compare **A. the existing retrieval and agent system** with **B. the same system plus a small set of compiled themes**, using the same gold questions. Measure answer completeness, citation faithfulness, searches and tool calls, latency, and cost. Theme creation, update, merge, split, contradiction, and deprecation rules belong in a later focused plan, not in this foundation document.

## Decisions for focused plans to carry forward

- **First source:** Podcast transcripts. Preserve speaker exchanges, Q&A pairs, complete stories, and topic changes. Later note/book plans should preserve headings and paragraphs.
- **Replacement and experiments:** Reuploading a transcript runs ordinary ingestion and switches its active chunks, summaries, and other derived artifacts only when the new run succeeds. Keep experimental and historical runs long enough to compare chunking, prompts, and embeddings; they are not served in ordinary answers. Long-term source-file version management is outside the first work package.
- **Chunking:** Start with structural chunks around 600-1,000 tokens and roughly 10% overlap. The deliberately simple baseline is fixed 800-token chunks, 10% overlap, and no enrichment. Overlapping source spans need deduplication in retrieval and evaluation.
- **Embedding versions:** Record the full `embedding_spec` above and build a complete parallel index when it changes. Compare the new and old indexes on the same evaluation set before switching the active one.
- **Search:** V1 retrieves vector and Postgres FTS/`ts_rank` top-K lists independently and combines ranks with RRF. BM25 through a separately evaluated search layer and reranking are later candidates, not V1 dependencies.
- **Access:** V1 contains personal transcripts, notes, and books only. Customer-specific data and its authorization model come later.

## Evaluation contract

Build a gold set of roughly 30-50 real questions with manually labeled relevant chunks. Mark the evidence that **must** be found separately from useful additional evidence. Include single-source, multi-source synthesis, recurring-pattern or “find all,” and negative questions with no supporting evidence.

Retrieval evaluation reports Recall@5, Recall@10, and especially Recall@20. Answer evaluation is separate: check whether citations point to exact supporting spans and whether the answer is **supported, complete, balanced, and calibrated**. A topically related span is not a correct citation. Claims about repeated patterns require multiple independent supporting passages. Record searches and token use so improvements can be weighed against cost.

The chunking and context tests isolate one change at a time:

| Experiment | Control | Treatment | Question |
|---|---|---|---|
| A: chunking | Fixed 800-token chunks, 10% overlap, raw text | Structural 600-1,000-token chunks, roughly 10% overlap, raw text | Does chunking improve Recall@20? |
| B: context | Structural chunks, raw text | The same structural chunks with chunk-specific context | Does context improve Recall@20 beyond chunking? |

Fixed 800-token raw chunks remain the overall baseline. The six-file A/B trial held the embedding model and exact vector retrieval fixed: structural/raw improved Recall@20 by two required spans, while adding context lost one of those spans. This is directional evidence for the initial chunk choice, not a final retrieval setting. Separate search comparisons still need to measure semantic search, Postgres FTS, and RRF; a later reranker test holds the winning candidate generation fixed. The checked retrieval labels and earlier card-value comparison remain available as historical evaluation records.

## Choices still open

- Which existing cloud drive, if any, should sync the external vault?
- How should the first UI open an original file: local path, cloud-drive link, or both?

## Boundaries for V1

The intended V1 stack is Python, FastAPI, Supabase Postgres, pgvector, Postgres full-text search, an explicit in-code agent loop, the application's `ModelClient` with a direct OpenRouter adapter, and a simple UI. Every model role can be filled with an open-weight model. LiteLLM is an optional later adapter when multiple gateways or local hosting justify it. The compiled knowledge layer is not a V1 dependency, but V1 provenance must allow future themes to resolve through chunks to exact source spans. A model router, knowledge graph, Neo4j, Kafka, OpenSearch, Kubernetes, and multiple specialized agents require evidence from V1 before they enter the work map.

The source contract, mixed Markdown ingestion, and local storage/search path have code behind them. The Stage 4 focused plan records the bounded live-enrichment trials and completed local ingestion proof.

## Research informing the experiments

- [New Relic's AWS case study](https://aws.amazon.com/blogs/machine-learning/new-relic-transforms-productivity-with-generative-ai-on-aws/) reports that hierarchical chunking worked best in its own tests. That motivates an experiment here rather than prescribing a winner.
- [PDI's AWS case study](https://aws.amazon.com/blogs/machine-learning/how-pdi-built-an-enterprise-grade-rag-system-for-ai-applications-with-aws/) used a 70% chunk-content, 10% overlap, 20% **document-summary** allocation and reported answer approval rising from 60% to 79%. Its reused document summary is distinct from chunk-specific context.
- [Anthropic's contextual retrieval study](https://www.anthropic.com/engineering/contextual-retrieval) evaluated top-20 retrieval failures and reported improvements from context and reranking. Its results motivate Recall@20 and separate context/reranker tests.
- [Qdrant's embedding migration guide](https://qdrant.tech/documentation/tutorials-operations/embedding-model-migration/) shows a parallel-index cutover pattern. [OpenSearch's RRF guide](https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/rrf/) explains why rank fusion works when lexical and vector scores cannot be compared directly.
- [OpenRouter's API documentation](https://openrouter.ai/docs/quickstart) describes its common model endpoint, [Responses endpoint](https://openrouter.ai/docs/api/api-reference/responses/create-responses), and [tool-calling interface](https://openrouter.ai/docs/guides/features/tool-calling). [Supabase's ParadeDB integration](https://supabase.com/partners/paradedb) documents the possible BM25 path, which requires a separate replicated search service.
- [Pinecone's agent knowledge work](https://www.pinecone.io/blog/introducing-nexus-knowledge-engine/) emphasizes source-level provenance for derived artifacts; its [benchmark account](https://www.pinecone.io/blog/nexus-ea-benchmarks/) uses questions and expected answers drawn from the corpus. These are external examples, not quality guarantees for this project.
