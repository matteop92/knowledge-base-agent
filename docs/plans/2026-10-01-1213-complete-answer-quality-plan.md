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

> **Closed on 2026-10-02. Do not execute from this file.** It is superseded by `docs/plans/2026-10-02-001-refactor-remove-cards-finish-answer-gate-plan.md`, which carries the decision to remove evidence cards and all remaining work. This file is kept as the record of the earlier card experiments and answer checks.

## Goal Capsule

- **Objective:** The repaired agent answers the pilot questions usefully, completely, and with source-backed examples and citations.
- **Means:** Remove evidence cards from the system, re-establish the answer baseline on chunk retrieval alone, freeze the revised rubric, calibrate the grader, and decide the final Stage 5 answer gate in that order. (Revised 2026-10-02; see KD2 and KD3.)
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

- KD2. **Decide card work by what cards do for answers.** Cards are never cited; the agent reads the original before using anything. Their only jobs are to find passages chunk search misses, to tell the agent what a chunk contains, and to surface concrete examples. A wrong detail costs a wasted read, while a missing or vague card can hide a passage. U2-U4 optimized field occupancy and zero detail errors before anyone measured whether cards change answers; `v9` doubled card volume and still failed. From 2026-10-02 the order is: measure card value first (U9), then redesign cards against those jobs only if the measurement shows a benefit (U10). R16-R18 replace R2, R3, R5, and the factuality part of R6 for any new candidate; the Field and Card Boundaries table and U2-U4 remain as the record of the superseded approach. (session-settled: user-directed — "boosting volume while reducing quality isn't a good compromise"; go back to the purpose of cards.) Governs R16-R18, U9, U10.
- KD3. **Remove evidence cards and finish this plan on chunk retrieval alone.** A card restates a sentence that is already in an embedded chunk. Three card designs were tried; two failed source review, and the selected one gave only a modest gain on broad questions for about 70% more cost per question (U9). Published evidence points the same way: sentence-level indexes add little when a modern embedding model already retrieves the chunks, and LLM-written layers add errors (`docs/research/2026-10-02-retrieval-complement-research.md`). The user decided on 2026-10-02 to stop creating, storing, searching, and showing cards, to remove the related code including ingestion, and to complete the remaining rubric, grader, and gate work now. What should complement chunk retrieval later (memory across turns, connections across sources, questions about the whole vault) is recorded in `FOUNDATION.md` as later work and is not part of this plan. KD3 supersedes KD2's conditional redesign: U10 and U5 are cancelled. (session-settled: user-directed.) Governs R19-R21, U11.

### Requirements

**Grounded card content**

- R1. A card's statement and any populated situation, action, result, or principle must be supported by its one exact source group and preserve speaker or personal-note attribution.
- R2. Populate a structured field when the group clearly supplies that detail, even if the statement also summarizes it; leave genuinely absent or ambiguous details null. A result must not be inferred merely because an action was described.
- R3. Keep one coherent, useful fact or episode per card without copying the same sentence mechanically into every field. A source group may yield zero, one, or several cards when it contains distinct supported facts; split independent episodes or lessons rather than mixing them, and do not make duplicates merely to increase field occupancy. A derived principle must remain labelled as interpretation.

### Field and Card Boundaries

These are proposed extraction definitions to verify against real source groups in U2, then version in `evals/card_field_guidelines_v1.md`. They define field *meaning*; the model supplies the source-specific values.

| Field | Put in it | Leave it null when |
| --- | --- | --- |
| `situation` | The starting context, problem, or constraint for this one episode. | The source gives a general claim with no particular context. |
| `action` | What someone did, or what the source explicitly advises doing; retain the did-versus-advised distinction in the wording. | No behavior or recommended step is stated. |
| `result` | An observed or reported outcome of that episode, attributed to the source. | The source only predicts a benefit, states an aspiration, or gives an action without an outcome. |
| `principle` | One concise generalizable interpretation supported by this group, marked as derived under the current card contract. A principle stated directly by the source may instead be the card's `statement`. | Generalizing would add an unsupported causal claim or merely repeat the statement. |

Each card has one short `statement` and at most one coherent value per structured field. Multiple independent principles or examples from one group become separate cards with the same source-group link; no useful grounded fact means zero cards. Card type and field definitions are fixed for the experiment, while the model chooses the values and how many distinct cards the passage warrants.

**Comparison and publication**

- R4. Preserve existing `evidence_cards_v5` artifacts and reports. New extraction uses a distinct prompt and artifact identity bound to the same checked source revisions.
- R5. Determine field applicability from original passages before inspecting candidate output, and score supported-field coverage over applicable source groups rather than raw non-null rate. An eligible field is missed if the candidate emits no grounded card for its group.
- R6. A candidate must retain first-response reliability, exact provenance, useful-fact coverage, and zero observed unsupported or misattributed details under the existing Stage 4 gates.
- R7. Compare card discovery and repaired answers on common questions with the agent prompt, model, limits, chunks, and embeddings held fixed. Neither a card hit nor a generated field becomes citation authority before the original passage is read.
- R8. Keep the currently published card set active unless the candidate passes the card and downstream answer checks. Retain failed candidates privately for diagnosis.
- R9. Reserve and account for new card-generation and answer calls under their applicable existing Stage 4, Stage 5, and cumulative ceilings before any live request; do not treat previous cost estimates as current route prices.

**Answer quality and evaluation**

- R10. Before card generation, run representative repaired-agent Q1/Q2/Q8 checks on the selected `v5` cards, with Q4 if affordable. Record exact prompt, model, limits, source and card identities, inspected passages, answer quality, and spending; material failures must be repaired or recorded before this becomes a comparison baseline.
- R11. Preserve the 21 submitted human judgments and frozen rubric v3. Verify rubric-v4 source alternatives and individual checks, retain the Q3 scope decision, give each source-bound check every passage that supports it rather than a single one, and freeze v4 only after cards are removed (U11) and the user has reviewed the criteria; do not relabel historical judgments as v4 truth.
- R12. Calibrate the evidence-backed grader against human judgments, then validate frozen grader behavior on question-grouped held-out cases. Invalid, unreviewed, or ambiguous grades cannot count as passes.
- R13. Apply the original Stage 5 acceptance rule to the selected profile: the full local answer path and hard provenance/budget gates work, the user judges answerable pilot answers useful, and no known material grounding or essential-facet coverage failure remains. Automatic grades support this judgment but cannot make it alone.
- R14. Keep original source reads as citation authority. Card content is discovery context, and answers/grades must distinguish inspected evidence from gold alternatives or unreviewed sources.
- R15. Keep the original source snapshots, structural/raw chunks and embeddings, historical card artifacts, receipts, and hosted data unchanged. Only the local card selection may change through U5; no summary or new retrieval architecture enters this sequence.

**Card purpose and value (added 2026-10-02, KD2)**

- R16. Before any further card generation, compare answers on the pilot questions with the selected cards available to the agent and with them withheld, holding the harness, prompt, model, limits, chunks, and embeddings fixed. Report what the agent read and cited against the rubric's source-bound evidence spans, essential-facet coverage, and cost. One run per arm is a signal, not proof; state run-to-run variation where it is known.
- R17. A new card candidate is defined by its jobs, not by field occupancy. Each card is one self-contained sentence that names who and what, states its speaker, and says what the passage establishes; it may carry one flag marking a concrete example. A card is written only when someone could plausibly ask a question the passage answers; greetings, logistics, and cut-off fragments get none. The model may see surrounding text to resolve references, but every claim must come from the card's own passage. Situation, action, result, principle fields and the eight-type list are not required.
- R18. A new card candidate is judged by retrieval and answers: required evidence found and read, rubric coverage on matched questions, and card volume no higher than the selected set unless the gain justifies it. The factuality gate is that no reviewed card reverses, misattributes, or invents the substance of its passage; lost hedging and small wording drift are counted and reported, not disqualifying, because cards are leads and never citation authority.
**Card removal (added 2026-10-02, KD3)**

- R19. Ingestion no longer generates evidence cards, the loader and publication no longer require or load them, retrieval has no card search or card lookup, and the answer agent has no card action, card facts, or card switch. Card prompts, card evaluation code, and their tests are deleted. Summaries, chunk context, chunks, embeddings, and source snapshots are untouched.
- R20. The selected local profile is republished without a card requirement, and its search and read proof still passes. Existing private card artifacts and receipts under `.local/` are kept as history. The hosted schema is not changed by this plan: the user chose on 2026-10-02 to leave the `knowledge.cards` table in place, empty and unused.
- R21. After removal, the pilot questions are rerun on chunk retrieval alone and reviewed. The Q8 failure seen without cards (repeated searches until the allowance ran out) is repaired in the harness or recorded as a known limitation before the baseline is used for U6-U8.

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
- KTD6. **Freeze criteria after the card-free baseline exists.** Preserve rubric v3 and the 21 submitted reviews. Complete source-bound v4 alternatives and individual checks, then freeze criteria before final configuration comparison or grader validation. This implements R11 and R12.
- KTD7. **Keep grading evidence-backed and separate from acceptance.** The grader receives frozen checks, generated answers, inspected evidence, and separately labelled gold alternatives; code validates excerpts, citations, applicability, and denominators. Human review and the original Stage 5 gate still decide acceptance. This implements R12-R14.
- KTD8. **Give extraction one versioned meaning.** U2 writes and source-tests a concise field guide with positive, negative, and multi-card examples. U3 puts the operational rules and examples in the versioned card prompt and clear field descriptions in its structured-output schema; code validates structure and source identity, while held-out source review checks semantic support. Keep these surfaces aligned without treating schema validity as proof that a field is true. This implements R1-R6.

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

Current order (KD3): U11 removes cards and re-establishes the answer baseline, then U6, U7, and U8. U10 and U5 are cancelled. The KD2 order before that was U1, then U9, then U10 and U5 only if U9 showed a benefit. The original order was U1 through U8: a failed U4 card screen skips U5 and keeps `v5`; the sequence resumes at U6. A passing card screen permits U5, whose matched answer comparison chooses the card artifacts for U6-U8. Card qualification never accepts overall answer quality; U8 applies the original Stage 5 gate after rubric and grader work.

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

**Files:** `evals/card_field_guidelines_v1.md`, `evals/card_field_audit.py`, `tests/evals/test_card_field_audit.py`, ignored `.local/` audit labels and report.

**Approach:** Reuse the selected 510-card receipts and exact source spans. Choose a deterministic sample across documents and card types, with enough examples and tactics to test the complaint. Verify the proposed field and card boundaries against those originals, then version the annotation guide before labeling candidate output. Mark each field as supported, absent, or ambiguous from the original group; record why any old empty field was eligible. Include examples with zero, one, and multiple distinct cards from a group. Reserve whole chunks for prompt development or validation, and keep validation outputs uninspected until the prompt and threshold are fixed. Publish aggregate counts only; keep private source excerpts in `.local/`.

**Execution note:** Characterize the selected artifact and source applicability before editing generation behavior.

**Test scenarios:**

- A card whose source explicitly states an action and result but whose old fields are null counts as two missed eligible fields.
- A card whose source gives advice without an outcome records `result` as absent, not missed.
- A changed source hash or card span prevents an audit label from being reused.
- Two cards from one generation chunk cannot be split across development and validation, even when they reference different source groups.

**Verification:** The versioned guide defines each field, null boundary, one-card versus multiple-card decision, and source-grounding rule with synthetic examples; private labels point to checked source groups. The audit reproduces the 510-card total and four-field occupancy counts, retains source-group identities privately, and defines a baseline denominator for candidate comparison.

### U3. Extract supported structured details

**Goal:** Produce richer cards without sacrificing their one-group factual boundary.

**Requirements:** R1-R4, R6; KTD2, KTD4, KTD8.

**Dependencies:** U2's applicability definitions.

**Files:** `prompts/evidence_cards_v8.md`, `app/ingestion/enrichment.py`, `app/ingestion/contracts.py` if validation needs adjustment, `tests/ingestion/test_enrichment.py`.

**Approach:** Version the selected GPT-5 mini prompt using U2's field guide: concise field definitions, null and did-versus-advised rules, and short synthetic positive, negative, and multi-card examples. Add clear key descriptions to the output schema. Tell the model that source-supported details belong in their fields even when the statement summarizes the same fact, while absent or uncertain details remain null. Let it emit zero, one, or multiple distinct cards per group without a forced count. Keep the existing group index, exact-span validation, attribution, and derived-principle marker. Add no heuristic that silently fills old cards from statements.

**Test scenarios:**

- Covers AE1. A validated model response with a stated situation, action, and outcome preserves those fields and the unchanged exact source span; the live screen checks whether the revised prompt elicits this response.
- Covers AE2. A validated response for a group without an observed outcome retains a null result; the live screen checks that the model avoids inventing one.
- Covers AE3. Adjacent speaker groups or highlights cannot be combined into one card's supported fields.
- One group with two independent useful facts can yield two nonduplicate cards, while a group with no useful grounded fact yields none.
- A predicted benefit cannot be presented as an observed `result`, and advised behavior cannot be worded as something the speaker already did.
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

**Dependencies:** U11's card-free baseline answers and the preserved human review receipts.

**Files:** `evals/answer_questions_v3.yaml`, `evals/answer_questions_v4.yaml`, `evals/answer_rubric.py`, `evals/answer_trial.py`, `evals/README.md`, `tests/evals/test_answer_rubric.py`, `tests/evals/test_answer_trial.py`; private review receipts under `.local/`.

**Approach:** Reconcile the 21 recovered submitted judgments with their exact question/configuration/answer identities; leave blank or ambiguous entries unreviewed. Complete v4's individual checks, source-revision-bound evidence alternatives, required versus conditional applicability, and rules for qualifications and relevant gaps. Preserve the user's Q3 decision that in-person wording tests are one acceptable option, not a mandatory method. Widen the evidence alternatives: the U9 test showed that a check naming one passage marks an answer down when it cites another passage that supports the same point, so list every supporting passage found in the six sources. Review the changed criteria and source support with the user, then freeze v4 before any graded comparison.

**Test scenarios:**

- Covers AE6. Version-3 reports retain their original fingerprint and cannot be silently scored as v4.
- Missing human judgments or source excerpts are not manufactured; changed source hashes invalidate affected evidence alternatives.
- A supported explanatory example can satisfy a check through alternate wording, while a product name alone cannot satisfy that check.
- The Q3 check accepts another source-supported way to test wording without requiring an in-person conversation or presentation.

**Verification:** Historical review entries remain intact, new answer receipts record that no card artifacts were used, and every frozen v4 check has inspectable applicability and all of its known source alternatives.

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

### U9. Measure whether cards change answers

**Goal:** Know whether the selected cards improve what the agent reads and answers before spending more on card generation.

**Requirements:** R9, R14-R16; KD2.

**Dependencies:** U1's repaired harness and the selected six-file profile.

**Files:** `app/agent/harness.py`, `app/agent/cli.py`, `evals/answer_trial.py`, `evals/card_value.py`, their tests; ignored `.local/` receipts.

**Approach:** Add a switch that withholds card facts and the card-search action while leaving every other input unchanged. Run the eight pilot questions once in each arm at the U1 configuration. For each run, compute from the receipts which rubric-v4 source-bound evidence spans the agent inspected and which it cited, the chunks read, model calls, and cost. Review essential-facet coverage of both arms against rubric v3. Record the arms' identities and the known run-to-run variation.

**Test scenarios:**

- With cards withheld, candidates carry no card facts and a card-search action is rejected without spending a card search.
- The two arms differ only in the card switch; a prompt, model, limit, or source-revision mismatch invalidates the comparison.
- An evidence span counts as inspected only when an inspected passage of the same document revision overlaps it.

**Verification:** A private report gives per-question and total evidence inspected and cited, coverage, and cost for both arms, with an explicit statement of whether the difference is larger than the variation already seen between repeated runs.

### U11. Remove evidence cards and re-establish the baseline

**Goal:** One retrieval path, chunk search and whole-chunk reads, with no card code left to maintain.

**Requirements:** R14, R15 as amended by R20, R19-R21; KD3.

**Dependencies:** U9's result and the user's decision.

**Files:** `app/ingestion/enrichment.py`, `app/ingestion/contracts.py`, `app/ingestion/live_run.py`, `app/storage/loader.py`, `app/storage/records.py`, `app/storage/retrieval.py`, `app/storage/publication.py`, `app/storage/local_viewer.py`, `app/agent/harness.py`, `app/agent/actions.py`, `app/agent/cli.py`, `app/agent/records.py`, `evals/answer_trial.py`, `evals/compare_generation.py`, `evals/retrieval_trial.py`; delete `evals/card_field_audit.py`, `evals/card_value.py`, `evals/card_field_guidelines_v1.md`, `prompts/evidence_cards_v*.md`, `prompts/evidence_card_check_v1.md`, and their tests; a new answer prompt version without card wording; `SYSTEM.md`, `FOUNDATION.md`, `INGESTION.md`, `RETRIEVAL.md`, `DATA.md`, `README.md`, `AGENTS.md`, `evals/README.md`.

**Approach:** Remove in dependency order: the agent (card action, facts, switch, prompt wording), then retrieval (card search, card lookup, card-span read allowance), then storage (loading, publication requirement, viewer), then ingestion (extraction, check pass, live-run step, contract), then evaluation code and prompts. Republish the six-file local profile without the card requirement and rerun the storage and search proof. Rerun the eight pilot questions, review them against rubric v3, and repair or record the Q8 search-loop failure. Keep summaries and chunk context code as they are.

**Test scenarios:**

- No module imports or references a card type, card prompt, or card table; the suites pass.
- A profile that names no card artifacts publishes and answers; a search action named `search_cards` is rejected as unknown.
- The pilot questions complete without cards, and Q8 completes in repeated runs or its failure is recorded with its cause.

**Verification:** The suites pass, the local profile proof passes without cards, and a private report holds the card-free pilot answers with costs and the review against rubric v3.

### U10. Redesign cards against their jobs (cancelled by KD3)

**Goal:** Produce fewer, clearer cards that find passages, describe chunks, and surface examples.

**Requirements:** R1, R4, R8, R9, R17, R18; KD2.

**Dependencies:** U9 shows a card benefit. Otherwise skip this unit and U5 and keep `v5`.

**Files:** a new versioned card prompt, `app/ingestion/enrichment.py`, `app/storage/retrieval.py` if cards are searched by embedding, their tests; ignored `.local/` candidate reports.

**Approach:** Write the card prompt from R17. Screen the same 18 chunks. Review every card for reversed, misattributed, or invented substance and count lesser drift separately. Compare volume with `v5`. If the screen passes R18's factuality gate, expand to the six files and run U5's matched answer comparison.

**Test scenarios:**

- A greeting, a logistics remark, or a cut-off fragment yields no card.
- A card names its subject and speaker rather than "he" or "the company".
- A card that reverses its passage or attributes it to the wrong person fails the screen; a card that drops "I think" is counted, not failed.

**Verification:** The screen reports card count against `v5`, the substance-error count, the drift count, and accounted spend, with an explicit pass or fail under R18.

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
- U9 records whether cards change what the agent reads, cites, and answers. U11 removes cards from ingestion, storage, retrieval, the agent, and evaluation, and records a card-free answer baseline. U10 and U5 are cancelled.
- U8 records human-reviewed Stage 5 acceptance only if the selected profile satisfies the original answer-quality gate; otherwise it records the specific unmet gate and does not claim a passed baseline. Current progress is reconciled and abandoned experimental code is removed while private historical receipts remain retained.

---

## Execution outcomes

**Overall status: incomplete.** A harness repair on 2026-10-02 (prompt v11) removed the demonstrated investigation failures: the four pilot questions now complete from whole-chunk reads with span-level citations. One known coverage defect in Q1 and the absence of human review keep U1 open. A second card candidate (`v9`) also failed the factuality gate. This plan remains the owner of the unfinished work; no replacement plan was created.

| Unit | Work completed | Outcome and remaining issue |
| --- | --- | --- |
| U1 — Current-card answers | Ran live Q1, Q2, Q8, and Q4 on the selected `v5` cards under prompts v9/v10, then repaired the harness and reran them under prompt v11 at medium reasoning: host search with the question, one candidate per chunk ranked by best rank with that chunk's cards shown, whole-chunk reads with span-level citations, ignored stray action fields, specific rejection feedback, repeated-query rejection, and a finish check against unread chunks of sources already used. | **Improved, not yet an accepted baseline.** Before the repair the agent never searched cards, read 32-430 characters per read, saw only the last 16 candidates, and lost up to half its calls to invalid actions. In the final v11 run all four questions completed; Q1 read ten chunks, Q2 five, Q8 five across both authors, Q4 one. By agent review against rubric v3, Q2, Q4, and Q8 cover their essential facets; Q1 omits centralized talent decisions and the no-variable-pay practice, and Q1 and Q8 close with a gaps paragraph or an offer of help. Human usefulness review has not happened. |
| U2 — Source field labels | Audited the 510 selected cards and labeled 27 source groups across six checked files before viewing candidate output. Wrote the field guide and audit code. | **Audit complete.** The sampled originals support 56 structured fields; the selected cards populate one. Labels and source excerpts remain under ignored `.local/`. This measurement does not improve answers by itself. |
| U3 — Richer card candidate | Added versioned `evidence_cards_v8` extraction and separate-highlight grouping, then `evidence_cards_v9`: one evidence group per extraction request, plus `evidence_card_check_v1`, a second pass that checks each card against its own group and drops unsupported cards or clears unsupported fields. Generated bounded 18-chunk candidates without changing `v5`. | **Trial artifacts complete, card improvement unfinished.** Prompts and trial outputs are available for diagnosis, but neither candidate qualified for use. |
| U4 — Candidate screen | Screened `v8` on 18 chunks and reviewed held-out cards. Screened `v9` three ways (shared-context extraction and check; shared extraction with per-group check; per-group extraction and check) and read every card of the last two against its source group. The gate was restated with the user as zero errors after the check pass on a full review of the screen sample. | **Failed factuality gate twice.** `v8`: 47/56 fields, one reversed action-result claim. `v9` final variant: 18/18 ready, 44/56 fields, 203 cards, and 10 cards with an unsupported or misread detail (3 material: an old outcome paired with a new habit, a description attached to the wrong person, a misread remark). The check removes cross-group borrowing but does not reach zero errors with GPT-5 mini on both passes. The useful-fact gate and six-file expansion were not run. |
| U9 — Card value test (added 2026-10-02) | Added a switch that withholds card facts and card search, and `evals/card_value.py`. Ran Q1-Q8 once per arm, then Q1, Q3, and Q8 twice more per arm, at the U1 configuration. | **Complete: a modest benefit.** The five narrow questions were the same in both arms. On the three broad ones, with cards the agent read more chunks (means 7.7/5.7/7.0 against 5.3/3.3/4.0) and, by agent review against rubric v3, covered somewhat more essential facets (Q1 5.3 against 4.3 of 8; Q3 3.2 against 2.8 of 4; Q8 2.3 against 1.2 of 3). Q8 exhausted its allowance twice without cards and never with them. Cards raised measured cost per run by roughly 60-70%. Three runs per arm and agent scoring make this a signal, not proof. Both arms keep missing the same points (Q1 centralized talent and variable pay; Q3 in-person wording tests). The rubric-v4 named-passage count proved too strict as a coverage measure: each check names one passage, and answers often cite another valid one. |
| U10 — Job-defined card redesign (added 2026-10-02) | Not started. | **Cancelled by KD3.** |
| U11 — Remove cards and re-establish the baseline (added 2026-10-02) | Not started. | **Next.** |
| U5 — Matched card comparison and selection | Never run. | **Cancelled by KD3.** There is no card candidate to compare. |
| U6 — Revised answer rubric | Preserved rubric v3 and the 21 submitted human judgments. Drafted 91 individually assessable v4 checks and verified 59 source-block alternatives against six captured source hashes; kept in-person wording tests optional for Q3. | **Not complete.** V4 is still a draft pending user review and has not been frozen. Historical v3 ratings were not transferred to v4. |
| U7 — Grader calibration | Added local checks that inspected passages match the checked originals and that positive grounding excerpts include their cited passage ID. | **Not complete.** No human calibration, fault-control run, or question-grouped held-out validation was performed. The grader cannot establish an answer-quality pass. |
| U8 — Final Stage 5 gate | Recorded a private failure decision, retained receipts, reconciled spending, and updated current project documentation. | **Acceptance did not pass; formal final evaluation is unfinished.** Known coverage and grounding failures already rule out acceptance. New human usefulness judgments, frozen-v4 grading, and a passing repaired-answer run are absent. |

The accounted upper bounds are **$1.664654 Stage 4**, **$7.664189 Stage 5**, and **$9.328843 cumulative**. The user stated on 2026-10-02 that budget is not a constraint; the Stage 5 and cumulative reservation ceilings were raised from $8 and $10 to $9 and $11 for U9, and Stage 4's remains $3.50. Ingestion, evaluation, agent, model, and storage unit suites passed; the storage suite skipped six integration tests without a disposable database. The 59 rubric spans passed a separate check against the six captured source hashes. Private receipts and the failure decision are retained under `.local/`; no vault content or model output is committed.

The remaining work, in order, is U11 (remove cards and re-establish the baseline), U6 (widen rubric v4's evidence alternatives, then the user's review and freeze), U7 (grader calibration and held-out validation), and U8 (the final gate with the user's recorded usefulness review). The user reviewed the v11 answers informally on 2026-10-02 and judged their quality very good; the recorded per-question usefulness review for U8 is still to be done. The card gate question is settled by KD2 and R18: cards are judged by retrieval and answers, and only a reversed, misattributed, or invented card fails factuality. U6 and U7 remain unfinished as stated above.

---

## Sources and Research

- `prompts/evidence_cards_v5.md`, `app/ingestion/enrichment.py`, `app/ingestion/contracts.py`, `app/storage/retrieval.py`, and `app/storage/publication.py` define current extraction, provenance, search, and selection boundaries.
- `docs/plans/2026-09-24-001-feat-live-enrichment-ingestion-plan.md` records the selected card configuration and original card gates. `docs/plans/2026-09-29-1832-fix-retrieval-answer-quality-plan.md` records the completed local repairs and remaining-work handoff; `docs/plans/2026-09-27-001-feat-retrieval-answer-agent-plan.md` defines the original Stage 5 acceptance gate.
- [Anthropic's agent-evaluation guidance](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) supports fixed cases plus trace and human review; [OpenAI's evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices) supports comparing prompt variants against representative labeled cases. Here the cases remain private, source-bound, and matched across card versions.
