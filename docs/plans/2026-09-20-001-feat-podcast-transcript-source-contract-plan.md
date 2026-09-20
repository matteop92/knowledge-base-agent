---
title: "feat: Establish podcast transcript source and ingestion contracts"
type: feat
status: completed
date: 2026-09-20
deepened: 2026-09-20
---

# Establish podcast transcript source and ingestion contracts

## Overview

Make one existing Markdown podcast transcript a reproducible, source-aligned input for later ingestion work. This step ends with validated Python records, a local preview, and a small set of questions labeled against original source spans. It does not create chunks or a searchable index.

## Problem and requirements

The original transcript must remain independently readable in the user's existing vault. Later chunking variants and citations need a stable document identity and exact links back to the text that was parsed. The supplied episode #882 transcript is the first real input. Inspection found YAML frontmatter (`title`, `source`, `author`, `published`, `created`, `description`, `tags`), bold Markdown speaker labels, long multi-paragraph turns, no headings, and no timestamps (see `FOUNDATION.md`).

| ID | Requirement | Proof in this step |
|---|---|---|
| R1 | Read a transcript from a configured local source root without modifying it. | The preview reads episode #882 from its vault folder and leaves its bytes unchanged. |
| R2 | Keep a stable document ID across re-ingestion at the same locator; distinguish source revisions and ingestion attempts. | Re-reading the same relative path keeps `document_id`; changed bytes change `source_sha256`; each attempt has its own `run_id`. |
| R3 | Preserve source metadata and exact provenance. | Metadata and every speaker turn validate against the captured source; timestamps are null when absent. |
| R4 | Define inspectable, versioned Pydantic contracts for the next ingestion stage. | Serialized parsed records round-trip and reject invalid spans or unsupported input. |
| R5 | Begin evaluation with this real transcript before chunk IDs exist. | A few realistic questions have manually checked, hash-bound source spans, including a negative question. |

## Scope

Support local UTF-8 Markdown transcripts using the format observed in episode #882: optional YAML frontmatter and line-start `**Speaker:**` markers. Preserve additional frontmatter fields without claiming they have known meaning. A missing title may fall back to the filename; `source`, dates, author, description, and tags may be absent. Reject malformed YAML, invalid UTF-8, and files with no recognizable speaker turns with a clear diagnostic. Do not silently guess speakers or timestamps.

### Deferred to separate plans

- Structural and fixed chunking, overlap, chunk context, evidence cards, summaries, and embeddings: step 2 in `FOUNDATION.md`.
- Supabase tables, active-run promotion, search, agent loop, and UI source-opening behavior: later steps in `FOUNDATION.md`.
- Additional transcript formats, automatic rename detection, cloud-drive APIs, and long-term snapshot cleanup policy.

## Context and decisions

- The repository has `FOUNDATION.md` and `knowledge-agent-architecture.md`, but no application code or existing test patterns. `FOUNDATION.md` is the current architecture reference where the two differ.
- **Source location:** Configure one local source root and a stable root alias (default `primary`). Address a file by a relative locator under that root. Store the alias and locator, not a machine-specific absolute path, in parsed records. Keep the alias unchanged when moving the root between machines. Keep the original in the vault; no transcript copy enters tracked `sources/`.
- **Identity:** Derive `document_id` deterministically from a configured root alias plus normalized relative locator. A reupload at the same locator keeps its ID. A rename is a new locator in this first version; an explicit relink can be designed later. The source URL is metadata and an eventual opening option, not the identity key.
- **Three distinct identifiers:** `document_id` names the logical source at that locator; `source_sha256` hashes the exact original bytes; `run_id` names a parsing attempt. This prevents a parser change or reupload from obscuring what produced an artifact.
- **Source fidelity:** Capture the bytes once, then parse that captured version. Decode as UTF-8 (accept a UTF-8 BOM), preserve the decoded text's line endings and Markdown, and use zero-based half-open Unicode character offsets into it. The parsed record retains this `source_text`; source line numbers are one-based aids for humans. A span must reproduce the recorded raw text by slicing `source_text`. Keep cleaned display/search text separate from raw evidence.
- **Turn shape:** A line-start speaker marker begins a turn; subsequent paragraphs belong to that speaker until the next marker. Record speaker label and the full turn span, including its marker, with an optional content span excluding the marker. This preserves interruptions as separate turns for the later chunker to group. `timestamp_start` and `timestamp_end` remain null for episode #882.
- **Local reproducibility:** Keep an immutable source snapshot and capture manifest under a configurable, ignored runtime data directory, keyed by `run_id`, with source hash and parser/schema versions. Write a successful parsed preview only after the full record validates; a failed parse may retain its capture and diagnostic. The external vault file remains authoritative. This local artifact is an interim contract, not the Supabase schema or active-run mechanism.
- **Safe parsing:** Parse frontmatter as data without constructing arbitrary YAML objects. The source file is never executed or rendered during ingestion. [PyYAML documents `safe_load` for this purpose](https://pyyaml.org/wiki/PyYAMLDocumentation).

The contract names below describe responsibilities, not prescribed class signatures: `SourceLocator`, `SourceMetadata`, `SourceSpan`, `TranscriptTurn`, and `ParsedTranscript`/run manifest. Use Pydantic models for typed validation and serialization; [Pydantic's model documentation](https://pydantic.dev/docs/validation/latest/concepts/models/) describes those capabilities. The span invariant also needs a check against the captured source text, since field types alone cannot prove provenance.

## Output shape

```text
app/ingestion/              contracts, source reader, transcript parser, preview entry point
sources/fixtures/           short synthetic Markdown transcript used by tests
evals/                     seed questions and source-span labels
tests/ingestion/            contract, source-reader, parser, and preview tests
.local/                     ignored run snapshots and previews, created at runtime
```

## Implementation units

- [x] **Unit 1 — Define source-aligned records and minimal Python project setup**

  **Goal:** Establish the typed handoff consumed by parsing now and chunking later. **Requirements:** R3, R4. **Dependencies:** None.

  **Files:** Create `pyproject.toml`, `app/ingestion/contracts.py`, `tests/ingestion/test_contracts.py`, `sources/fixtures/sample_transcript.md`.

  **Approach:** Define typed metadata with the original frontmatter block retained; locator, document/revision/run IDs; full captured `source_text`; turn order, speaker, nullable timestamps, and spans. Version the serialized contract. Validation checks nonempty IDs, ordered nonoverlapping turn spans, bounds, and exact source slicing. Use a synthetic fixture with Unicode punctuation and multiline turns so tests can live in the repository without copying the full episode.

  **Test scenarios:**
  - Happy path: A valid two-speaker record with multiline turns serializes and restores with identical metadata, IDs, and spans.
  - Edge case: Missing optional dates and timestamps remain null; an extra frontmatter key survives round-trip.
  - Error path: A turn whose claimed raw text differs from its source slice, or whose offsets extend beyond the source, is rejected.

  **Verification:** The contract makes source locations unambiguous and can be consumed without a database or model provider.

- [x] **Unit 2 — Resolve and snapshot one local source safely**

  **Goal:** Give the parser stable input while leaving the vault file untouched. **Requirements:** R1, R2. **Dependencies:** Unit 1.

  **Files:** Create `app/ingestion/source_reader.py`, `tests/ingestion/test_source_reader.py`, `.env.example`, `.gitignore`.

  **Approach:** Resolve a relative Markdown locator beneath the configured root, reject traversal and resolved paths outside it, read original bytes once, compute SHA-256, assign deterministic `document_id`, and create a run snapshot and manifest in the ignored runtime directory. Never put a user-specific absolute path in configuration examples or tracked records. A source-root alias is part of the identity input, so changing the machine path alone does not change the ID.

  **Test scenarios:**
  - Happy path: Two reads at the same locator have the same document ID and hash; their run IDs differ, and the original bytes are unchanged.
  - Edge case: Changed file bytes at the same locator retain document ID but yield a new hash; a UTF-8 BOM decodes without shifting offsets in the decoded text.
  - Error path: Missing or empty file, unsupported extension, invalid UTF-8, `..` traversal, and a symlink escaping the root fail before producing a parsed record.
  - Integration: The snapshot bytes hash to the manifest's `source_sha256`, and the parser receives those same bytes rather than re-reading a possibly changed vault file.

  **Verification:** A run can be reproduced from its snapshot even after the vault file is replaced, without exposing the original path or text in a tracked file.

- [x] **Unit 3 — Parse the observed transcript format with exact turn locations**

  **Goal:** Turn episode #882 into validated speaker turns and metadata without losing citation text. **Requirements:** R3, R4. **Dependencies:** Units 1–2.

  **Files:** Create `app/ingestion/transcript_parser.py`, `tests/ingestion/test_transcript_parser.py`.

  **Approach:** Recognize frontmatter only when it is delimited at the beginning; parse it safely and retain its original block alongside normalized fields. Recognize bold speaker labels only at the start of a line. Treat following paragraphs as part of that turn, preserve raw Markdown in spans, and leave absent timestamps null. Keep title fallback and author-display normalization distinct from the original metadata. Report unsupported formatting instead of manufacturing a single speaker or discarding unmatched body text before the first turn.

  **Test scenarios:**
  - Happy path: The synthetic fixture yields ordered turns with exact speaker names, paragraph continuation, and spans that slice back to their original text.
  - Edge case: Inline bold text inside a turn does not create a new speaker; CRLF line endings and Unicode punctuation keep correct offsets and one-based line locations.
  - Error path: Malformed frontmatter and a body without a recognizable speaker marker produce specific failures rather than partial records.
  - Local acceptance (opt-in, not CI): The provided episode #882 file yields its frontmatter, Tim Ferriss and Niels Hoven turns, null timestamps, and no unmatched body text; every turn span validates against the captured snapshot.

  **Verification:** The parsed record can support later Q&A/story grouping and exact citation spans without reparsing the original file.

- [x] **Unit 4 — Add a preview and source-span eval seed**

  **Goal:** Make the first transcript inspectable and give later chunking experiments stable labels. **Requirements:** R1, R5. **Dependencies:** Units 1–3.

  **Files:** Create `app/ingestion/preview.py`, `tests/ingestion/test_preview.py`, `evals/transcript_questions.yaml`, `evals/README.md`, `sources/README.md`, `README.md`.

  **Approach:** Provide a local entry point that accepts root configuration and a relative locator, writes a validated run preview, and reports document ID, hash, metadata, turn count, warnings, and source locations without dumping the full transcript. Seed roughly 5–8 questions from episode #882, such as which channels brought retained customers, what the trial is meant to solve, and whether the episode establishes a quantified first-20-customers cold-outreach case. Label must-find versus useful spans against `document_id` and `source_sha256`; mark unsupported questions explicitly. Later eval work maps these labels onto each chunking variant rather than hard-coding chunk IDs now.

  **Test scenarios:**
  - Happy path: Previewing the synthetic fixture creates a validated run artifact and concise summary without modifying its source.
  - Error path: An invalid transcript exits with a clear diagnostic and does not produce a successful preview artifact.
  - Integration: Test-created labels for the synthetic fixture resolve positive labels to exact nonempty source slices and leave the negative label without an asserted supporting span.
  - Local acceptance (opt-in, not CI): Previewing the provided episode #882 resolves each positive label against its captured source revision.

  **Verification:** The user can inspect the episode's parsed structure and labels locally, and a later plan can compare chunking approaches on the same source revision.

## Dependencies and risks

| Risk or dependency | Response |
|---|---|
| The vault path is machine-specific and may be synced or moved. | Configure the root; persist only root alias and relative locator. |
| A new copy of the transcript could be committed accidentally. | Ignore runtime snapshots and previews; keep tracked fixtures synthetic and short. |
| Normalization could break exact citations. | Keep immutable captured source text, validate all half-open spans against it, and separate display text from evidence. |
| Speaker labels in later transcripts may differ. | Support the observed format first and fail clearly for unsupported formats; add adapters in a later source-format plan. |
| Reupload or parser changes could invalidate eval labels. | Bind each label set to the source hash and retain run snapshots for comparison. |

## Done when

The provided episode #882 transcript can be previewed from its existing vault location without alteration; its identity, metadata, speakers, source hash, and exact spans are inspectable; invalid records fail validation; and the first hash-bound eval questions are labeled against original spans. No chunking, enrichment, Supabase writes, or agent behavior is implied by this milestone.
