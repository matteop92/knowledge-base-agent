---
status: ready
priority: p2
issue_id: "001"
tags: [agent, evaluation]
dependencies: []
---

# U4 card-free answer baseline

## Problem Statement
Establish five runs for each pilot question after source-aware search, using the selected card-free profile.

## Findings
U3 passed. Baseline review uses frozen v3; revised v4 integration is U5. Existing finish parsing rejects complete-with-gaps before the harness can record partial.

## Proposed Solutions
Apply U4's finishing changes, preserve citation verification, run Q8 first, then the other seven questions.

## Recommended Action
Execute the approved U4 on the current branch. Stop on any Q8 failure or source mismatch and present the trace.

## Acceptance Criteria
- [x] Complete-with-gaps records partial; unread citations remain rejected; receipts count refused actions.
- [x] Q8 completes five of five runs before remaining questions; the content-quality gate still failed.
- [ ] All eight questions have five receipts and measured costs in the ledger.
- [ ] Review reports mean/worst v3 coverage and comparison with historical with-card Q1/Q3/Q8.
- [ ] Remove repeated-query refusal only if it never fires in the forty runs.
- [ ] Required suites pass; progress and retrieval documentation reconciled.

## Work Log
- 2026-10-04: User authorized U4. Estimated incremental spend $1–$3; retain plan policy of recording spend and raising reservation ceilings as needed.
- 2026-10-04: Finishing changes and receipt-count test pass. All five area suites pass (35 agent, 24 storage, 20 evals, 15 models, 66 ingestion; database/live opt-in tests skipped). Provider preflight failed with URLError before any billable request; its reservation was released with a zero-cost note. Automatic approval review rejected private excerpts to OpenRouter; explicit payload/destination approval requested before retrying.

- 2026-10-04: Explicit payload/destination approval received. Q8 rounds 1–3 completed; round 4 failed first-turn strict JSON parsing because the provider returned duplicate action objects. Stopped under R9; round 5 and remaining 35 answers were not run. Four live runs cost $0.107876124. Private stop report and review preserve receipts and content-quality concerns. Repeated-query refusal remains because the forty-run removal condition is unmet.

- 2026-10-04: A single bounded format correction now handles malformed output on the first turn. A fresh Q8 batch completed five of five and cited both named sources; one malformed response was corrected. Agent review against frozen v3 found mean essential coverage 1.3/3, below the prior with-card 2.33/3. Ferrari's urgent-seat caution was often implicit or absent, and the answer attributed the reader's question-led coaching note to Sivers. Stopped the remaining batch after Q1's first answer. Q2 has an interrupted $1 reservation and no receipt.
- 2026-10-05: The user chose to work on the full revised rubric before further performance evaluation.
