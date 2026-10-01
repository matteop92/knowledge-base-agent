---
title: Complete Answer Quality - Sequential Plan
type: fix
date: 2026-10-01
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# Complete Answer Quality - Sequential Plan

## Goal Capsule

- **Objective:** The repaired agent answers the pilot questions usefully, completely, and with source-backed examples and citations.
- **Means:** Verify repaired answers on current cards, audit and trial richer cards, select a card set, freeze the revised rubric, calibrate the grader, and decide the final Stage 5 answer gate in that order.
- **Authority:** This is the sole execution plan for remaining answer-quality work. `docs/plans/2026-09-29-1832-fix-retrieval-answer-quality-plan.md` records completed repairs and original requirements; `FOUNDATION.md` owns project progress; captured source revisions own factual truth.
- **Execution profile:** The six checked local source revisions, structural/raw chunks and embeddings, and the selected GPT-5 mini card route remain the comparison controls.
- **Stop conditions:** Do not promote cards with unsupported details, changed attribution or source links, lost useful facts, or an unaccounted live cost. Do not accept an answer baseline while a known material grounding or essential-coverage failure remains.
- **Tail ownership:** Keep private source text, candidate cards, answer receipts, review labels, and field annotations under ignored `.local/`; reconcile `FOUNDATION.md`, `INGESTION.md`, and `RETRIEVAL.md` after implementation.

### Current Starting Point

The former plan's U3 repair is locally verified. Its U4 implementation and isolated database tests are complete, but the representative live Q1/Q2/Q8 check on the selected `v5` cards is outstanding (Q4 if affordable). The 21 submitted human-review entries are preserved; rubric v4 is a draft with unverified source alternatives, and the locally tested grader core is uncalibrated. The `v5` card set is still selected; this plan has not generated or published a revised card set.

---

## Product Contract

### Summary

Complete the remaining answer-quality proof in one sequence. Start by checking the repaired agent on the existing cards, then test whether source-supported card fields improve retrieval and answers, and finally freeze evaluation criteria and judge the selected profile.

### Problem Frame

The repaired harness has local proof but no representative live-answer quality result. In the selected 510-card six-file profile, 468 cards have all four structured fields empty; this includes 73 of 76 cards labelled `example`. The `evidence_cards_v5` prompt requires a short statement and permits null fields. Inspected statements sometimes contain an action or outcome while separate fields are null. The Stage 4 trial measured useful-fact coverage, spans, and sampled factuality, but not field coverage; richer cards still need to show a real answer benefit. The revised answer rubric is not frozen, and the grader has no human calibration or held-out validation. None of these gaps permits an accepted Stage 5 answer baseline yet.

### Key Decisions

- KD1. **Use one sequential plan for all remaining work.** This plan owns the current-card answer check, card experiment, rubric, grader, and final acceptance decision. The earlier plan is a status record; card artifacts remain independently versioned so answer changes can be attributed. (session-settled: user-directed — replace the two-plan execution handoff with one ordered plan.) Governs R5, R7, R9-R15.

### Requirements

**Grounded card content**

- R1. A card's statement and any populated situation, action, result, or principle must be supported by its one exact source group and preserve speaker or personal-note attribution.
- R2. Populate a structured field when the group clearly supplies that detail, even if the statement also summarizes it; leave genuinely absent or ambiguous details null. A result must not be inferred merely because an action was described.
- R3. Keep statements specific and useful without copying the same sentence mechanically into every field; a derived principle must remain labelled as interpretation.

**Comparison and publication**

- R4. Preserve existing `evidence_cards_v5` artifacts and reports. New extraction uses a distinct prompt and artifact identity bound to the same checked source revisions.
- R5. Determine field applicability from original passages before inspecting candidate output, and score supported-field coverage over applicable source groups rather than raw non-null rate. An eligible field is missed if the candidate emits no grounded card for its group.
- R6. A candidate must retain first-response reliability, exact provenance, useful-fact coverage, and zero observed unsupported or misattributed details under the existing Stage 4 gates.
- R7. Compare card discovery and repaired answers on common questions with the agent prompt, model, limits, chunks, and embeddings held fixed. Neither a card hit nor a generated field becomes citation authority before the original passage is read.
- R8. Keep the currently published card set active unless the candidate passes the card and downstream answer checks. Retain failed candidates privately for diagnosis.
- R9. Reserve and account for new card-generation and answer calls under their applicable existing Stage 4, Stage 5, and cumulative ceilings before any live request; do not treat previous cost estimates as current route prices.

**Answer quality and evaluation**

- R10. Before card generation, run representative repaired-agent Q1/Q2/Q8 checks on the selected `v5` cards, with Q4 if affordable. Record exact prompt, model, limits, source and card identities, inspected passages, answer quality, and spending; material failures must be repaired or recorded before this becomes a comparison baseline.
- R11. Preserve the 21 submitted human judgments and frozen rubric v3. Verify rubric-v4 source alternatives and individual checks, retain the Q3 scope decision, and freeze v4 only after the selected card set is known; do not relabel historical judgments as v4 truth.
- R12. Calibrate the evidence-backed grader against human judgments, then validate frozen grader behavior on question-grouped held-out cases. Invalid, unreviewed, or ambiguous grades cannot count as passes.
- R13. Apply the original Stage 5 acceptance rule to the selected profile: the full local answer path and hard provenance/budget gates work, the user judges answerable pilot answers useful, and no known material grounding or essential-facet coverage failure remains. Automatic grades support this judgment but cannot make it alone.
- R14. Keep original source reads as citation authority. Card content is discovery context, and answers/grades must distinguish inspected evidence from gold alternatives or unreviewed sources.
- R15. Keep the original source snapshots, structural/raw chunks and embeddings, historical card artifacts, receipts, and hosted data unchanged. Only the local card selection may change through U5; no summary or new retrieval architecture enters this sequence.

### Scope Boundaries

The plan completes the local Stage 5 answer-quality work and may change the locally selected card artifacts after a measured comparison. It does not rework the completed answer-agent action semantics, change source snapshots, chunks, embeddings, card SQL, or hosted data, or add unqualified summaries. The rubric and grader work are in scope because they remain incomplete.

### Acceptance Examples

- AE1. A source group explicitly gives a situation, an action, and an outcome; the generated card places supported details in those fields, keeps attribution, and links to that exact group. Covers R1-R3.
- AE2. A source states advice but no observed outcome; the card may have an action, while `result` stays null. Covers R1, R2.
- AE3. A group contains two speakers or separate highlights; the card does not join them into one cause-and-effect example or attribute one person's action to another. Covers R1, R6.
- AE4. A question's card query exposes a source example as a useful lead, the agent reads the original passage, and the resulting cited answer is at least as accurate and complete as the current-card answer. Covers R7, R8.
- AE5. The repaired agent answers Q1/Q2/Q8 on current cards with source-backed breadth and examples or records a specific material failure for repair; the receipt can be replayed for a card comparison. Covers R10, R14.
- AE6. A historical v3 judgment remains tied to its exact answer while a frozen v4 check records its own acceptable evidence alternatives. Covers R11.
- AE7. A grade marked covered has a validated answer excerpt and appropriate inspected support; a final quality decision also includes human review of the answerable pilot questions. Covers R12, R13.

---

## Planning Contract

### Key Technical Decisions

- KTD1. **Audit applicability before changing the prompt.** Label a deterministic, source-backed sample across card types and all six files, oversampling examples and tactics. Assign whole generation chunks to prompt development or validation before tuning; retain source-group identities for field scoring. This keeps validation outputs unseen while the prompt is tuned and operationalizes R2 and R5.
- KTD2. **Version and compare the artifact, not the database schema.** The nullable fields already exist in `EvidenceCard` and are searched by `search_cards`; a new prompt version and artifact identity can be evaluated without changing storage columns or rewriting historical cards. This implements R4, R8, and R15.
- KTD3. **Promote on useful evidence, not field density.** Keep the existing 18-chunk useful-fact screen and source-span gate. Add applicable-field recall, unsupported-field and attribution review, card-query lead quality, and matched answer inspection; set a minimum applicable-field gain after the baseline audit but before candidate generation. This implements R5-R7.
- KTD4. **Hold comparison inputs stable.** Regenerate on the same checked source hashes and chunk boundaries with GPT-5 mini on a pinned eligible route. Change only the card prompt/artifact selection for the card comparison. This implements R4, R7, R9, and R10.
- KTD5. **Finish the repaired-answer check before card comparison.** Use the existing local harness, published six-file profile, and source-linked answer receipts. A representative live result is the comparison baseline only after material failures are repaired or explicitly classified; it is not final acceptance. This implements R10, R13, and R14.
- KTD6. **Freeze criteria after selecting cards.** Preserve rubric v3 and the 21 submitted reviews. Complete source-bound v4 alternatives and individual checks, then freeze criteria before final configuration comparison or grader validation. This implements R11 and R12.
- KTD7. **Keep grading evidence-backed and separate from acceptance.** The grader receives frozen checks, generated answers, inspected evidence, and separately labelled gold alternatives; code validates excerpts, citations, applicability, and denominators. Human review and the original Stage 5 gate still decide acceptance. This implements R12-R14.

### High-Level Technical Design

| Stage | Input held fixed | Decision/output |
| --- | --- | --- |
| Repaired-answer baseline | Selected `v5` profile and repaired agent | Representative live Q1/Q2/Q8 receipts, material failures, and costs |
| Source audit | Existing `v5` cards and their captured originals | Eligible field labels, old-card baseline, and separate validation groups |
| Candidate screen | Same selected 18 chunks, model, and source revisions | First-response, fact, field, provenance, and attribution checks |
| Optional expansion | Same 83 chunks and embedding profile | Complete versioned candidate artifacts; no active-profile switch yet |
| Local comparison | Same questions and answer-agent configuration | Card leads, originals actually read, cited answer quality, latency, and cost |
| Card-selection decision | Complete checked candidate and prior selection | Activate the qualified candidate or keep `v5`; preserve both receipts |
| Rubric and grader | Selected card identity, historical judgments, checked sources | Frozen v4 criteria and a grader calibrated and validated on held-out cases |
| Final Stage 5 gate | Selected profile and frozen evaluation identities | Human-reviewed acceptance or explicit unresolved answer-quality failures |

Run U1 through U8 in order. A failed U4 card screen skips U5 and keeps `v5`; the sequence resumes at U6. A passing card screen permits U5, whose matched answer comparison chooses the card artifacts for U6-U8. Card qualification never accepts overall answer quality; U8 applies the original Stage 5 gate after rubric and grader work.

### Risks and Dependencies

- The old statement may already carry the useful fact, so field occupancy may rise without improving retrieval. Measure useful leads and final answers separately.
- Asking for richer fields can invent causal links or move a fact to the wrong speaker. Review each populated field against its exact group and reject unsupported details.
- A new card artifact may alter a live answer for reasons unrelated to card content if prompts, model routes, source revisions, or budgets drift. Pin and record each comparison input.
- Publication selects named ready artifacts per active run. Verify a complete six-file candidate and the former selection before any local switch; preserve a rollback path.
- Previous Stage 4 and Stage 5 ceilings are separate accounting views. Reconcile the shared cumulative ledger and route prices before generation or answer calls.
- The current local database already holds the selected six-file profile. U1 needs a working runtime connection to that existing database and a cost reservation; it does not create a new database or require hosted vault loading.

---

## Implementation Units

### U1. Check repaired answers on the current cards

**Goal:** Establish a trustworthy live-answer baseline before changing card artifacts.

**Requirements:** R9, R10, R13-R15; KTD4, KTD5.

**Dependencies:** The former plan's locally verified U3/U4 repairs, the existing published six-file local profile, and a working runtime connection to that database.

**Files:** `evals/answer_trial.py` only if the existing runner needs a narrow receipt or baseline fix, `tests/evals/test_answer_trial.py` for such a fix, ignored `.local/` answer and ledger receipts. Repair `app/agent/` or `app/storage/` only for a demonstrated material failure.

**Approach:** Confirm the current `v5` card selection and source hashes, preflight the existing local database connection and pinned model route, and reserve spend under the Stage 5 and cumulative ceilings. Run Q1/Q2/Q8 with the repaired prompt and limits, plus Q4 if affordable. Inspect actual answers, original passages read, citation support, missing facets/examples, attribution, finalization reason, latency, and cost; repair a material failure and rerun its affected case before using it as a baseline. Retain prompt, model, limits, selected-card identities, and source revisions for U5's matched comparison.

**Test scenarios:**

- A current-card answer includes relevant source examples or records the specific missing material; an invented scenario cannot pass as a source example. Covers AE5.
- A citation to a card field without a corresponding original read fails provenance review.
- A runtime connection or route failure is recorded as unverified, not as an answer-quality result; no new database is created to mask the issue.
- The planned batch cannot start when its conservative reservation would exceed the remaining approved ceilings.

**Verification:** Representative current-card answers and their receipts are inspected against the known Q1/Q2/Q8 failures. The result is a fixed comparison baseline with identified limitations, not a final Stage 5 pass.

### U2. Establish source-backed field labels

**Goal:** Know which fields the original passages actually support and quantify the current-card gap.

**Requirements:** R1, R2, R5; KTD1.

**Dependencies:** U1's fixed current-card answer baseline, the current `v5` artifacts, and captured six-file sources.

**Files:** `evals/card_field_audit.py`, `tests/evals/test_card_field_audit.py`, ignored `.local/` audit labels and report.

**Approach:** Reuse the selected 510-card receipts and exact source spans. Choose a deterministic sample across documents and card types, with enough examples and tactics to test the complaint. Mark each field as supported, absent, or ambiguous from the original group; record why any old empty field was eligible. Reserve whole chunks for prompt development or validation, and keep validation outputs uninspected until the prompt and threshold are fixed. Publish aggregate counts only.

**Execution note:** Characterize the selected artifact and source applicability before editing generation behavior.

**Test scenarios:**

- A card whose source explicitly states an action and result but whose old fields are null counts as two missed eligible fields.
- A card whose source gives advice without an outcome records `result` as absent, not missed.
- A changed source hash or card span prevents an audit label from being reused.
- Two cards from one generation chunk cannot be split across development and validation, even when they reference different source groups.

**Verification:** The audit reproduces the 510-card total and four-field occupancy counts, retains source-group identities privately, and defines a baseline denominator for candidate comparison.

### U3. Extract supported structured details

**Goal:** Produce richer cards without sacrificing their one-group factual boundary.

**Requirements:** R1-R4, R6; KTD2, KTD4.

**Dependencies:** U2's applicability definitions.

**Files:** `prompts/evidence_cards_v8.md`, `app/ingestion/enrichment.py`, `app/ingestion/contracts.py` if validation needs adjustment, `tests/ingestion/test_enrichment.py`.

**Approach:** Version the selected GPT-5 mini prompt. Tell the model that source-supported details belong in their fields even when the statement summarizes the same fact, while absent or uncertain details remain null. Keep the existing group index, exact-span validation, attribution, and derived-principle marker. Add no heuristic that silently fills old cards from statements.

**Test scenarios:**

- Covers AE1. A validated model response with a stated situation, action, and outcome preserves those fields and the unchanged exact source span; the live screen checks whether the revised prompt elicits this response.
- Covers AE2. A validated response for a group without an observed outcome retains a null result; the live screen checks that the model avoids inventing one.
- Covers AE3. Adjacent speaker groups or highlights cannot be combined into one card's supported fields.
- A malformed group index, changed source text, or failed model response cannot leave a ready candidate artifact.
- The same run may retain `v5` and new artifacts with distinct identities without changing historical bytes.

**Verification:** Focused ingestion tests pass and a source-linked candidate can be produced without changing the storage schema or existing artifacts.

### U4. Compare the candidate before expansion

**Goal:** Decide whether the revised extraction is accurate and materially more useful before generating all 83 chunks.

**Requirements:** R5, R6, R9; KTD1, KTD3, KTD4.

**Dependencies:** U2, U3.

**Files:** `evals/compare_generation.py`, `evals/card_field_audit.py`, `tests/evals/test_card_field_audit.py`, `evals/README.md`, ignored `.local/` candidate reports.

**Approach:** Fix the field-recall threshold from U2 before the first candidate output. Run the existing 18-chunk screen on unchanged source revisions and a pinned eligible GPT-5 mini route under a reserved cap. Compare applicable-field recall to `v5` over the same audited source groups, counting an eligible field as missed when the candidate emits no grounded card for its group; retain the existing 17/18 useful-fact gate, first-response requirement, exact-span requirement, and zero observed unsupported/attribution errors. Review validation chunks without using them to tune the prompt. Expand to 83 chunks only if the screen passes.

**Test scenarios:**

- The report separates applicable-field misses from correctly null fields and from unsupported populated fields.
- Candidate fields cannot earn coverage when their card's source span, speaker, or hash disagrees with the label.
- A first-response failure, useful-fact regression, or unsupported action-result link fails the screen even if more fields are populated.
- An interrupted expansion reuses ready artifacts and records unknown-usage reservations without duplicate calls.

**Verification:** The screen has an explicit pass/fail decision with sample sizes, old/new field recall, fact coverage, source-grounding findings, latency, and accounted spend; a failed screen leaves the current cards selected.

### U5. Test retrieval impact and decide card selection

**Goal:** Select a card set only after it helps find source-backed answers without regression.

**Requirements:** R4, R6-R9; KTD2-KTD4.

**Dependencies:** U4 passes, U1's representative repaired-answer baseline exists, and a complete candidate set is validated. Skip this unit and retain `v5` if U4 rejects the candidate.

**Files:** `app/storage/publication.py` only if existing selection needs a narrow extension, `tests/storage/test_stage3_integration.py`, `evals/answer_trial.py`, `tests/evals/test_answer_trial.py`, ignored `.local/` matched-answer reports.

**Approach:** Load the complete candidate into a separate local profile and verify every named card artifact is ready and source-linked. Compare current and candidate card queries, then run U1's repaired-agent questions again with only selected card artifacts changed. Inspect discovered leads, passages read, citation support, required examples, attribution, answer completeness, runtime, and cost. Activate the candidate only if the source-backed answer checks do not regress and the card-specific benefits are clear; otherwise retain `v5` and record why. Record the selected card identity and matched receipts for U6-U8.

**Test scenarios:**

- Covers AE4. A query that previously missed a supported example now surfaces its card, and the agent reads the original passage before citing it.
- A candidate with a missing artifact, stale source hash, wrong chunk variant, or unpublished run cannot become active.
- Matched questions use identical agent prompt, model, limits, and source revisions; a mismatch invalidates the comparison.
- A new card hit that yields an unsupported claim or hides a required source example prevents promotion despite better field coverage.
- A failed or rejected switch leaves the former six-file selection queryable and its receipts unchanged.

**Verification:** A private, inspectable comparison identifies the selected card version and its measured answer impact. Publication and rollback checks pass in a disposable database before any local profile switch. This selects cards only; U8 decides final answer quality.

### U6. Freeze the revised rubric and human reference data

**Goal:** State what useful, source-supported answers must contain without changing the meaning of historical reviews.

**Requirements:** R11, R13, R14; KTD6.

**Dependencies:** U4's rejection or U5's card-selection decision; the representative U1 answers and preserved human review receipts.

**Files:** `evals/answer_questions_v3.yaml`, `evals/answer_questions_v4.yaml`, `evals/answer_rubric.py`, `evals/answer_trial.py`, `evals/README.md`, `tests/evals/test_answer_rubric.py`, `tests/evals/test_answer_trial.py`; private review receipts under `.local/`.

**Approach:** Reconcile the 21 recovered submitted judgments with their exact question/configuration/answer identities; leave blank or ambiguous entries unreviewed. Complete v4's individual checks, source-revision-bound evidence alternatives, required versus conditional applicability, and rules for qualifications and relevant gaps. Preserve the user's Q3 decision that in-person wording tests are one acceptable option, not a mandatory method. Review the changed criteria and source support, then freeze v4 before any graded comparison.

**Test scenarios:**

- Covers AE6. Version-3 reports retain their original fingerprint and cannot be silently scored as v4.
- Missing human judgments or source excerpts are not manufactured; changed source hashes invalidate affected evidence alternatives.
- A supported explanatory example can satisfy a check through alternate wording, while a product name alone cannot satisfy that check.
- The Q3 check accepts another source-supported way to test wording without requiring an in-person conversation or presentation.

**Verification:** Historical review entries remain intact, the selected card identity is attached to new answer receipts, and every frozen v4 check has inspectable applicability and source alternatives.

### U7. Calibrate and validate the grader

**Goal:** Make automated quality judgments useful and auditable without turning them into unreviewed passes.

**Requirements:** R11-R14; KTD6, KTD7.

**Dependencies:** U6's frozen rubric and retained, identified answer receipts.

**Files:** `evals/answer_grader.py`, `evals/answer_trial.py`, `evals/answer_rubric.py`, `prompts/answer_grader_v1.md`, `tests/evals/test_answer_grader.py`, `tests/evals/test_answer_trial.py`, `evals/README.md`.

**Approach:** Complete the existing grader core over immutable answer receipts. Validate exact answer excerpts, source revisions, inspected citation IDs, check completeness, and applicability in code before calculating separate coverage, examples, grounding, qualification, gaps, and usefulness results. Calibrate on human judgments and explicit fault-injected controls, then freeze grader prompt/model/route/rubric/evidence identity before question-grouped held-out validation. Review critical false passes and disagreements, including invented scenarios and citations that are topical but do not support the claim.

**Test scenarios:**

- Covers AE7. A covered grade includes a valid answer excerpt and relevant inspected support; gold evidence not read by the agent cannot rescue grounding.
- Invalid, truncated, injected, ambiguous, or unreviewed grader output remains ungraded rather than increasing a pass rate.
- Calibration and held-out validation share no question group; model identity and expected grades are hidden from the grader.
- An irrelevant gap in a three-tip answer is marked separately from an honest limitation on a requested, unsupported contribution.

**Verification:** Report per-dimension agreement, false passes, misses, unresolved judgments, and sample sizes on held-out cases. Resolve every identified critical grounding/example false pass before using the grader for comparison; do not infer a general model winner from the small pilot.

### U8. Decide the final Stage 5 answer-quality gate

**Goal:** Decide whether the selected local profile now produces acceptable answers on the answerable pilot questions.

**Requirements:** R10-R15; KTD5-KTD7.

**Dependencies:** U6 and U7, plus the recorded U4 rejection or U5 card-selection decision.

**Files:** `evals/answer_trial.py` only if final reporting needs a narrow change, `evals/README.md`, `FOUNDATION.md`, `RETRIEVAL.md`, `INGESTION.md` where current facts change; ignored `.local/` final reports and human judgments.

**Approach:** Compare only common questions with frozen rubric, grader, selected card artifacts, source revisions, prompt/model/limits, and explicit spend accounting. Inspect representative answers and traces with the user; record provenance, semantic grounding, essential-facet and source-example coverage, relevant gaps, usefulness, runtime success, latency, and cost separately. If a known material failure remains, repair the responsible layer within the existing scope and recheck affected cases; if it cannot be resolved within the approved budget, report the baseline as unaccepted. Reconcile current progress and remove abandoned experimental code while preserving private historical receipts.

**Test scenarios:**

- A result with valid citation IDs but unsupported claims or a missed essential facet cannot pass the gate.
- An honest partial answer can pass a partial-answer rubric, but invented source claims cannot.
- A card-search on/off comparison runs only when the corrected card tool returns useful leads and the comparison identities are fixed.
- An unaffordable or unavailable live route is recorded as an unverified gate, never silently counted as success.

**Verification:** The final report records what passed, failed, or was not run and why; the user can inspect actual answers and traces. The Stage 5 baseline is accepted only under the original plan's hard provenance/budget checks and human usefulness judgment, with no known material grounding or essential-facet failure on the answerable pilot questions.

---

## Verification Contract

Use the repository's unittest discovery for `tests/ingestion`, `tests/evals`, `tests/storage`, `tests/agent`, and `tests/models` when those surfaces change; storage integration needs a disposable database. U1's current-card live answers precede the U2-U4 card experiment. Check exact card source spans and active-artifact filtering at the database boundary. Keep the 18-chunk screen, six-file expansion, matched-answer comparison, grader calibration, held-out validation, and final human-reviewed gate as distinct receipts so costs, model routes, prompt/rubric versions, card artifacts, and source hashes remain traceable. A missing live route or unaffordable reservation stops live work without changing the selected profile or claiming an accepted baseline. Review private source excerpts and actual answers; neither field counts nor automatic grades alone prove factuality or usefulness.

---

## Definition of Done

- U1 records representative repaired answers on the current cards, their costs and identities, and specific remaining failures; it does not claim final acceptance.
- U2 identifies source-applicable fields and the current-card baseline without treating nulls as automatic defects.
- U3 produces versioned, source-linked candidate cards and leaves `v5` immutable.
- U4 either rejects the candidate with evidence or passes the preregistered field, fact, provenance, reliability, and factuality gates before expansion.
- If U4 rejects the candidate, the card experiment ends with `v5` selected and U5 is skipped. If U4 passes, U5 records a matched retrieval and answer comparison, a justified card-selection decision, and a verified rollback path; no card becomes citation authority on its own.
- U6 preserves historical judgments and freezes source-backed rubric v4 after the card decision. U7 calibrates and validates the grader on held-out cases, with critical false passes resolved.
- U8 records human-reviewed Stage 5 acceptance only if the selected profile satisfies the original answer-quality gate; otherwise it records the specific unmet gate and does not claim a passed baseline. Current progress is reconciled and abandoned experimental code is removed while private historical receipts remain retained.

---

## Sources and Research

- `prompts/evidence_cards_v5.md`, `app/ingestion/enrichment.py`, `app/ingestion/contracts.py`, `app/storage/retrieval.py`, and `app/storage/publication.py` define current extraction, provenance, search, and selection boundaries.
- `docs/plans/2026-09-24-001-feat-live-enrichment-ingestion-plan.md` records the selected card configuration and original card gates. `docs/plans/2026-09-29-1832-fix-retrieval-answer-quality-plan.md` records the completed local repairs and remaining-work handoff; `docs/plans/2026-09-27-001-feat-retrieval-answer-agent-plan.md` defines the original Stage 5 acceptance gate.
- [Anthropic's agent-evaluation guidance](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) supports fixed cases plus trace and human review; [OpenAI's evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices) supports comparing prompt variants against representative labeled cases. Here the cases remain private, source-bound, and matched across card versions.
