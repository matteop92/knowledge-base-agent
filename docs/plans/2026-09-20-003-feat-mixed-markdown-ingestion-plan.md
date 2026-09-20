---
title: "feat: Ingest mixed Markdown with structural chunks and grounded enrichment"
type: feat
status: active
date: 2026-09-20
---

# Ingest mixed Markdown with structural chunks and grounded enrichment

## Overview

Implement step 2 of `FOUNDATION.md` after `docs/plans/2026-09-20-002-refactor-mixed-markdown-source-contract-plan.md`. Process the seven representative external-vault Markdown files through format-aware parsing, source-linked chunking, optional model enrichment, and local versioned artifacts. This produces inspectable inputs for the later Supabase and retrieval plans. It does not claim retrieval quality before a searchable index exists.

## Problem and requirements

The corpus has two transcript label styles, short notes with and without headings, an article with prose and bold tips, book highlights containing quotations plus personal notes, and a roughly 73,000-word book export with chapters, table of contents, index, and missing image assets. One transcript-specific parser or one fixed token splitter cannot preserve all of those relationships.

| ID | Requirement | Proof |
|---|---|---|
| R1 | Route known Markdown structures to a suitable parser without losing unknown content. | Both transcript styles and prose/highlights/book examples produce source-linked blocks; ambiguous files show diagnostics or use a visible generic fallback. |
| R2 | Keep meaningful units intact while meeting a practical chunk budget. | Speaker exchanges, Q&A, stories, headings, lists, highlights with page references, and personal annotations survive structural chunking. |
| R3 | Preserve an isolated comparison with a simple baseline. | Fixed 800-token raw chunks with about 10% overlap and structural raw chunks coexist for the same source revision. |
| R4 | Add grounded enrichment behind the project's own model interface. | Context, evidence cards, and summaries identify their exact source/run/chunk relationships; invalid evidence spans are rejected. |
| R5 | Version all generated artifacts and model choices. | Chunking, prompt, extraction, and full embedding specs appear in run metadata; variants remain inspectable. |
| R6 | Prepare evaluation without overstating it. | Existing span labels map onto chunk variants; structural coverage and attribution checks run now, while Recall@20 waits for search. |

## Scope boundary

All seven inspected examples are Markdown, so this plan supports Markdown only. PDF/EPUB conversion, image interpretation, Supabase persistence, semantic and lexical retrieval, the agent, UI, customer data, and automatic provider/model routing remain separate work. Missing book images are recorded as unresolved references; their unseen contents are not inferred. A generic Markdown fallback retains text and reports reduced structural confidence rather than fabricating a speaker or discarding paragraphs.

## Context and decisions

| Concern | Decision and rationale |
|---|---|
| Ingestion routing | Use explicit source-kind override plus inexpensive folder, frontmatter, and content signatures. Route uncertain cases to a generic Markdown parser with diagnostics. An LLM classifier is an optional later experiment, not a dependency: it cannot repair a missing parser or guarantee citations. This document-type routing is distinct from the provider/model routing deferred in V1. |
| Common output | Every lane emits the source-linked blocks established in plan 002. Keep parser-specific speaker, heading, page, and annotation roles as attributes. |
| Chunking | Keep fixed/raw and structural/raw variants on the same source hash. Target 600–1,000 tokens with roughly 10% overlap for long prose, but do not pad tiny notes or split a short highlight solely to meet a target. Oversized sections are split at lower-level boundaries with recorded reasons. |
| Attribution | A highlighted book passage is quoted source text; `My note` is the user's annotation. A transcript's preface/summary is not a speaker turn. Enrichment may interpret those passages but may not silently change who said or wrote them. |
| Model use | `app/models/` owns a thin `ModelClient`; direct OpenRouter is the initial adapter. Model IDs and prompts come from configuration. Fake clients make deterministic tests possible; an open-weight model can fill every role. Live calls that send vault content outside the machine require an explicit run choice and a visible estimated/request budget. No provider runtime owns orchestration. |
| Evidence cards | Require exact `evidence_span` inside a raw chunk; nullable situation/action/result stay null when absent; a derived principle is marked derived. Reject unsupported cards before indexing. |
| Long book | Parse and chunk the complete text. Model enrichment is batched, resumable, and budgeted; representative sections are sufficient for the first live acceptance run. Build a document summary from section-level summaries so the whole book does not require one model context window. |

Docling's [common document model](https://docling-project.github.io/docling/concepts/docling_document/) and [hierarchical/token-aware chunking](https://docling-project.github.io/docling/concepts/chunking/) are useful design references, not mandated dependencies. OpenRouter documents [embedding requests](https://openrouter.ai/docs/api/api-reference/embeddings/create-embeddings); the application's interface, not that endpoint, remains the dependency for ingestion code.

## High-level flow

> This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.

```text
captured Markdown + canonical locator
  -> route decision + reason
  -> source-aligned blocks + coverage report
  -> fixed/raw and structural/raw chunk variants
  -> context, strict evidence cards, section/document summaries
  -> versioned embedding inputs and vectors
  -> local manifest and span-to-chunk eval mapping
```

## Implementation units

- [ ] **Unit 1 — Route and parse the observed Markdown variants**

  **Goal:** Produce trustworthy common blocks for the inspected corpus. **Requirements:** R1, R2, R6. **Dependencies:** Plan 002.

  **Files:** Create `app/ingestion/router.py`, `app/ingestion/markdown_parser.py`, `app/ingestion/highlight_parser.py`, short synthetic examples under `sources/fixtures/`; modify `app/ingestion/transcript_parser.py`; test `tests/ingestion/test_router.py`, `tests/ingestion/test_markdown_parser.py`, `tests/ingestion/test_highlight_parser.py`, `tests/ingestion/test_transcript_parser.py`.

  **Approach:** Recognize both `**Speaker:**` and escaped bracket speaker labels. Preserve the second transcript's summary and media references outside speaker turns. Parse headings, paragraphs, lists, block quotes, bold article tips, and book section hierarchy into blocks. Treat the book's table of contents, index, Pandoc-style wrappers, and image references as explicit structure or excluded furniture, never as ordinary narrative without a visible decision. Preserve highlight page references and the distinction between book quotation and personal note. Record route reason, parser version, unclassified spans, and unresolved asset references.

  **Test scenarios:**
  - Happy path: each synthetic variant yields ordered blocks with exact source slices and correct type-specific attributes.
  - Edge case: a transcript has preface and summary before the first bracketed speaker; neither is attributed to that speaker. A prose book's bold `STEP 1:` label does not trigger transcript routing.
  - Edge case: a headingless note and article bold tip remain independently addressable blocks.
  - Error path: invalid frontmatter or a speaker-like marker that cannot be classified yields a diagnostic, not a silent drop.
  - Integration: every one of the seven read-only vault examples parses with no unexplained substantive text loss.

  **Verification:** The corpus has a reviewable block/coverage report without model calls.

- [ ] **Unit 2 — Produce fixed and structural chunk variants**

  **Goal:** Create comparable chunks that retain citation locations. **Requirements:** R2, R3, R5, R6. **Dependencies:** Unit 1.

  **Files:** Create `app/ingestion/chunking.py`; modify `app/ingestion/contracts.py`; test `tests/ingestion/test_chunking.py`.

  **Approach:** Record ordered source spans and parent block IDs for each chunk. Make the fixed/raw 800-token plus 10% overlap baseline deterministic. Let structural grouping follow speaker exchanges, local topic/heading boundaries, highlight-plus-note relationships, and complete paragraphs or stories where feasible. Record overlap and split reasons. Keep section context as metadata separate from raw text so enrichment experiments can reuse exactly the same chunks.

  **Test scenarios:**
  - Happy path: fixed and structural variants cover the same eligible source body and remain side by side for one source hash.
  - Edge case: a 200-word note remains one useful chunk; a short book highlight keeps its page and personal-note relationship.
  - Edge case: a long speaker turn or book chapter splits at sensible lower-level boundaries without losing text; overlap does not create false distinct evidence.
  - Error path: any chunk whose cited source slices cannot reconstruct its raw evidence fails validation.

  **Verification:** Chunk previews explain their boundaries, source coverage, and variant identity.

- [ ] **Unit 3 — Add a minimal model client and versioned enrichment inputs**

  **Goal:** Isolate provider calls and make enrichment reproducible. **Requirements:** R4, R5. **Dependencies:** Unit 2.

  **Files:** Create `app/models/client.py`, `app/models/openrouter.py`, `app/ingestion/run_manifest.py`, `prompts/`; modify `.env.example`, `pyproject.toml`; test `tests/models/test_client.py`, `tests/ingestion/test_run_manifest.py`.

  **Approach:** Define only the generation/structured-output and embedding operations needed here. Configuration selects extraction and embedding models independently. Record model/provider/version, prompt versions, input recipe, token usage when available, and errors without logging private source text or secrets. A fake adapter drives all automated tests. A failed model call leaves an incomplete variant, not a partially promoted one.

  **Test scenarios:**
  - Happy path: a fake client receives the intended bounded input and the run records the selected model and prompt versions.
  - Edge case: model or embedding configuration changes create a distinct artifact version rather than overwriting the prior one.
  - Error path: without explicit live-call selection, no vault content is sent to the gateway; timeout, malformed structured response, or absent credentials produce an inspectable failure and no successful enrichment artifact.

  **Verification:** Ingestion code can switch adapters without changing parser or chunker logic.

- [ ] **Unit 4 — Generate chunk context and strict evidence cards**

  **Goal:** Add useful discovery artifacts while making source facts checkable. **Requirements:** R4, R5. **Dependencies:** Unit 3.

  **Files:** Create `app/ingestion/enrichment.py`, versioned files under `prompts/`; modify `app/ingestion/contracts.py`; test `tests/ingestion/test_enrichment.py`.

  **Approach:** Generate short context for a chunk using its section and bounded neighboring/document context. Extract the agreed example, principle, claim, counterexample, framework, tactic, mistake, and lesson card types. Preserve card-to-chunk/document/run relationships. Validate each exact evidence span against raw chunk text; nullable fields remain null when source facts are absent. Treat a derived principle and a user's annotation as such. Keep raw and contextualized embedding inputs as separate recipes.

  **Test scenarios:**
  - Happy path: a card supported by an exact transcript or highlight span is accepted with correct attribution.
  - Edge case: a source contains only a principle and no situation/action/result; those fields stay null. A personal note is not presented as a book quote.
  - Error path: invented, normalized, or out-of-chunk evidence text is rejected; model output cannot overwrite raw source text.

  **Verification:** All accepted cards and contexts resolve through a chunk to the captured source.

- [ ] **Unit 5 — Summarize documents and generate versioned embeddings**

  **Goal:** Finish local processed artifacts without binding them to a database. **Requirements:** R4, R5. **Dependencies:** Units 3–4.

  **Files:** Create `app/ingestion/summary.py`, `app/ingestion/embeddings.py`; modify `app/ingestion/contracts.py`; test `tests/ingestion/test_summary.py`, `tests/ingestion/test_embeddings.py`.

  **Approach:** Summarize short documents directly and long books from section summaries. Link summary claims to source sections where possible. Create embeddings under a full `embedding_spec`: provider, model, model version, dimensions, distance metric, input recipe version, context prompt version, chunking version, and creation time. Validate dimensions and finite values. Preserve the old complete version when changing model or recipe; do not imply the new version is better until retrieval evals exist.

  **Test scenarios:**
  - Happy path: a short note and multi-section book produce summaries with source relationships; a fake embedding client produces vectors matching the declared spec.
  - Edge case: an incomplete section-summary batch can resume without duplicating completed sections.
  - Error path: dimension mismatch, non-finite vector, or incomplete corpus version cannot be marked ready.

  **Verification:** A completed local run contains traceable chunks, enrichment, summaries, and embedding versions.

- [ ] **Unit 6 — Expose local previews and source-span eval mapping**

  **Goal:** Make structural outcomes and variant comparisons inspectable before storage/search work. **Requirements:** R3, R5, R6. **Dependencies:** Units 1–5.

  **Files:** Modify `app/ingestion/preview.py`, `evals/README.md`, `README.md`, `FOUNDATION.md`; create `app/ingestion/eval_mapping.py`; test `tests/ingestion/test_preview.py`, `tests/ingestion/test_eval_mapping.py`.

  **Approach:** Preview each lane's route, block coverage, warnings, chunk boundaries, attribution, model usage, and artifact versions without printing the full corpus. Map the existing exact eval spans to each variant's chunks, deduplicating overlap. Add a small number of manually checked labels for the other source categories. Keep retrieval metrics explicitly pending until step 3 builds search; compare structural coverage and label containment now.

  **Test scenarios:**
  - Happy path: the Tim Ferriss eval spans map to both fixed and structural variants; the second transcript, note, highlight, article, and book examples yield concise previews.
  - Edge case: an eval span appears in overlapping chunks but counts as one relevant source span.
  - Error path: a changed source hash or a labeled span omitted by a parser/chunker fails validation.

  **Verification:** A reviewer can identify what was parsed, skipped, chunked, derived, and versioned for each source.

## System-wide impact and risks

The data flow extends `app/ingestion/` and introduces `app/models/`; no production database or API changes occur. Parsing and model failures remain attached to local runs. Raw snapshots and historical variants remain in the ignored runtime directory. The external vault is read-only. Acceptance on real files is opt-in and may incur model cost; deterministic parsing/chunking acceptance should run without a key. Do not commit full book text, private notes, model responses containing source text, or runtime snapshots.

| Risk | Mitigation |
|---|---|
| A route chosen from a folder name is wrong. | Combine explicit override and content signatures, record reason, and use a visible generic fallback when uncertain. |
| Book export furniture pollutes embeddings. | Classify table of contents, index, wrappers, and missing assets separately; keep raw source available. |
| Model enrichment invents evidence or misattributes notes. | Exact-span validation, typed roles, deterministic rejection, and previewable failures. |
| A 73,000-word book makes model calls expensive or exceeds context. | Bound requests, summarize by section, resume batches, and use a small live sample before a full enrichment run. |
| Early variant comparisons claim retrieval success without retrieval. | Report only structural and span-mapping checks here; measure Recall@20 after step 3. |

## Done when

All seven representative Markdown files can be parsed and structurally chunked from the canonical parent root without unexplained text loss; fixed/raw and structural/raw variants coexist; model-derived artifacts are source-grounded and versioned; and exact eval spans map to chunks. The result is ready for a separate Supabase/search plan, not yet an answering agent.
