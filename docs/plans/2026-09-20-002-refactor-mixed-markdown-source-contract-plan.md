---
title: "refactor: Prepare source contracts for mixed Markdown"
type: refactor
status: completed
date: 2026-09-20
---

# Prepare source contracts for mixed Markdown

## Overview

This is a focused follow-up to the completed podcast transcript source plan, not a rewrite of its history. Make the existing source capture and parsed-record contracts suitable for the Markdown books, highlights, notes, articles, and two transcript formats inspected in the external Knowledge vault. It establishes a stable handoff for the step 2 ingestion plan; it does not chunk, enrich, embed, or search documents.

## Problem and requirements

The current capture and preview assume the source root is the `Transcripts` folder, and `ParsedTranscript` exposes speaker turns as the only parsed structure. The new examples require a common representation without losing exact source locations. Moving the root to the parent `Knowledge` folder changes the first transcript's relative locator and therefore its deterministic `document_id`; its eval labels need a deliberate one-time rebind.

| ID | Requirement | Proof |
|---|---|---|
| R1 | Use the parent Knowledge folder as the canonical root while originals remain in place. | The first transcript is addressed as `Transcripts/<filename>` and the other categories as sibling relative paths. |
| R2 | Preserve identity and provenance across future runs at a fixed locator. | Same alias and relative path retain `document_id`; changed bytes change `source_sha256`; every block resolves to captured text. |
| R3 | Represent mixed Markdown without forcing non-transcripts into speaker turns. | A versioned parsed document has ordered source-aligned blocks and optional type-specific attributes. |
| R4 | Preserve metadata meaning and attribution. | Raw frontmatter survives; URL, non-URL origin labels, dates, and source-versus-personal-note roles are not conflated. |
| R5 | Keep the existing transcript and eval seed usable. | The first episode still previews; its labels retain exact quotes and hash but use the canonical document ID. |

## Scope and decisions

- Keep the current UTF-8 Markdown capture, immutable local snapshots, root alias, source hash, and half-open Unicode offsets. All seven inspected samples are `.md`; other file types are separate future work.
- Configure the parent Knowledge folder as the canonical root. The root alias remains stable. The first transcript's new locator includes `Transcripts/`, so its ID changes once; no production database exists to migrate. Preserve old ignored run snapshots for inspection, and update tracked eval labels only after verifying the captured bytes and quotes.
- Introduce a common `ParsedDocument`/block contract. Blocks carry kind, order, exact span and raw source text, plus optional heading path, speaker, page reference, and content role as applicable. Type-specific parsers may add fields, but downstream chunking consumes this common contract.
- Keep frontmatter verbatim and normalized metadata separate. Treat `source: Kindle` as an origin label, not a URL; accept a valid `url` field as a possible URL source. A `date` field must remain available even when its semantic role is unclear. Do not label a reader's `My note` as Derek Sivers's prose.
- Report skipped or unclassified body spans with reasons. Whitespace, frontmatter, and known export furniture can be excluded explicitly; substantive prose must not disappear silently.
- Retain the existing transcript parser as the first producer of the new contract. Format routing and the other parsers belong to the dependent step 2 plan.

## Relevant code and references

- `app/ingestion/source_reader.py`: root alias, path-safe capture, deterministic identity, snapshots.
- `app/ingestion/contracts.py`: `SourceSpan`, `TranscriptTurn`, `ParsedTranscript`, and provenance validation.
- `app/ingestion/transcript_parser.py`: observed bold-speaker parser, including a known malformed label.
- `app/ingestion/preview.py` and `app/ingestion/eval_labels.py`: local preview and source-hash-bound labels.
- `evals/transcript_questions.yaml`: existing manually checked source spans.
- `FOUNDATION.md`: mixed-source goal and provenance invariant.
- [Docling's common document model](https://docling-project.github.io/docling/concepts/docling_document/) is design precedent for a hierarchy shared by format-specific readers; this plan does not adopt Docling as a dependency.

## Implementation units

- [x] **Unit 1 — Canonicalize source location and rebind the first eval**

  **Goal:** Set one stable locator convention before more source IDs and labels accumulate. **Requirements:** R1, R2, R5. **Dependencies:** None.

  **Files:** Modify `README.md`, `sources/README.md`, `.env.example`, `evals/transcript_questions.yaml`, `evals/README.md`, `app/ingestion/source_reader.py`; test `tests/ingestion/test_source_reader.py`, `tests/ingestion/test_preview.py`.

  **Approach:** Document parent-root configuration and category-relative paths; make transcript-only source-reader messages generic. Recompute only the first eval's `document_id` under the canonical locator after checking its source hash and all labeled quotes. Keep the old completed plan as historical context.

  **Test scenarios:**
  - Happy path: a nested `Transcripts/episode.md` locator can be captured from the parent root and keeps its ID on repeat capture.
  - Edge case: moving the configured root directory without changing alias or relative locator retains the ID; changing relative locator changes it.
  - Error path: traversal or a symlink outside the parent root is still rejected.
  - Integration: the first real transcript's updated eval labels validate with the canonical root and unchanged source hash.

  **Verification:** Preview and labels use one documented root convention, and originals remain unchanged.

- [x] **Unit 2 — Add a source-aligned common document contract**

  **Goal:** Give all Markdown parsers one validated output shape. **Requirements:** R2, R3, R4. **Dependencies:** Unit 1.

  **Files:** Modify `app/ingestion/contracts.py`; test `tests/ingestion/test_contracts.py`.

  **Approach:** Separate generic document identity, metadata, and blocks from transcript-specific turns. Version serialized records. A block's raw text must equal its source slice; ordered blocks cannot overlap except where an explicitly modeled relationship requires it. Retain speaker, heading, page, and role as optional typed attributes rather than inserting fake speakers. Track any intentionally excluded source ranges separately from content blocks.

  **Test scenarios:**
  - Happy path: a transcript turn, prose paragraph, heading, and highlight can each round-trip with valid source spans.
  - Edge case: a Markdown note with no heading and absent publication date remains valid; a personal annotation remains distinct from the highlighted author's text.
  - Error path: overlapping independent blocks, out-of-range spans, and raw text that differs from the captured source are rejected.

  **Verification:** A downstream consumer can inspect source-linked blocks without knowing the original document category.

- [x] **Unit 3 — Preserve transcript behavior through the new contract**

  **Goal:** Move the current producer, preview, and label validator to the common contract without changing what the first episode says. **Requirements:** R3, R5. **Dependencies:** Unit 2.

  **Files:** Modify `app/ingestion/transcript_parser.py`, `app/ingestion/preview.py`, `app/ingestion/eval_labels.py`; test `tests/ingestion/test_transcript_parser.py`, `tests/ingestion/test_preview.py`.

  **Approach:** Emit typed speaker blocks, retain the observed turn boundaries and source slices, and validate labels against eligible content blocks. Include block counts and unclassified-span diagnostics in preview. This is a compatibility migration, not a new transcript-format detector.

  **Test scenarios:**
  - Happy path: the existing synthetic transcript and the first real episode produce the same speaker order, exact quote slices, and metadata.
  - Edge case: the known missing opening `**` on a familiar speaker still creates a Tim Ferriss block.
  - Error path: unsupported or unmatched transcript body text is reported rather than attributed to a neighboring speaker.
  - Integration: all current positive and negative eval labels validate against the new serialized output.

  **Verification:** The common contract is real, while step 1's proven transcript path remains usable.

## Risks and deferred work

| Risk | Response |
|---|---|
| The canonical root changes the first document ID. | Rebind once before storage exists; verify hash and exact quotes, and retain legacy local snapshots. |
| A generic block model could erase useful format detail. | Keep optional typed attributes and original raw text; test speaker and annotation attribution. |
| A parser could silently skip text. | Make coverage and excluded-range diagnostics part of the contract and preview. |

Actual parsing of the second transcript, notes, articles, highlights, and book; chunking; model calls; embeddings; Supabase; and retrieval are covered by later plans. The exact attribute names may change during implementation, but source-aligned block semantics are required.
