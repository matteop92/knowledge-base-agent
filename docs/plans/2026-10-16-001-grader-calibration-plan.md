# Grader calibration against the user's ratings

**Goal:** Decide whether the model grader can stand in for the user's hand ratings on the frozen 85-check checklist, so that later prompt and model changes can be measured over several runs without an hour of hand review each.

**Status:** Steps 1-6 are built and tested: grader prompt v3 with relevance, `evals.grade_answers`, `evals.grader_agreement`, and four planted-error receipts under `.local/grader-planted-2026-10-16/`. No model call has been made; the grader model is the user's choice.

## Inputs

- Frozen checklist `evals/answer_questions_v4_restructured.yaml` (85 checks, 8 questions).
- 16 answers the user rated by hand: one v12 answer and one v13 answer per question. Ratings are in `.local/answer-review-judgments-2026-10-06-adjusted.json` (v12) and `.local/answer-review-judgments-v13-2026-10-15-adjusted.json` (v13), re-keyed to the current checklist when scored.
- All answers were written by `openai/gpt-5-mini` at medium reasoning effort.

## Work before any live call

1. **Relevance in the grader.** The grader does not see a question's `in_scope` or `out_of_scope` today, so it cannot count main points outside the scope, which is part of the user's score. Add the scope lines to the grader input and an `out_of_scope_points` list of exact answer excerpts to the response; reject an excerpt that is not in the answer (the check that would have caught the Q8 paste slip).
2. **Batch runner.** `python -m evals.grade_answers` grades a list of receipts, writes one grade receipt per answer under `.local/`, and has a `--dry-run` that prints input size and the worst-case cost without calling a model. It checks that the route has zero data retention, refuses a price above a ceiling, and stops at a spending cap.
3. **Output allowance.** The current 2,800-token output limit is likely too small for Q1 (19 checks, each with excerpts and a reason); a truncated reply is recorded as ungraded. Raise it, sized from the dry run.
4. **Agreement report.** `python -m evals.grader_agreement` compares grade receipts with the user's ratings and prints the measures below.
5. **Planted errors.** Copy four answers and plant known faults in the answer text, keeping its citations: a claim attributed to the wrong speaker, an invented example, an unsupported number, and an off-scope paragraph. Each must fail the matching check. These make up for having only two real grounding failures among the 16 answers.
6. **Unit tests** with a fake model client, as for the existing grader.

## Model

The grader should come from a different model family than the answer model, because a model tends to rate its own family's writing more kindly. It also needs a zero-data-retention route, as the answer model has, because the input contains vault text. Candidates are chosen by a free route lookup against OpenRouter (a metadata request, not a model call) once the user agrees.

`openai/gpt-5-mini` is kept only as a cheap comparison, not as the grader.

**Pilot before choosing.** The grader is chosen by grading the development half once with each candidate, all at the same reasoning effort (medium), and keeping the model that agrees best with the user's ratings with no grounding false pass. Candidates on 2026-10-16: GLM 5.3, DeepSeek V4 Pro 0813, Qwen 3.8 2.4T and Kimi K3 (open weights) and Claude Sonnet 5.5 (closed reference); each route lists the reasoning setting. The OpenRouter adapter now sends a reasoning effort to any model when asked, not only OpenAI models. Expected pilot cost about $2.75, worst case about $13.50 across the five.

## Cost

Prices are from the free zero-data-retention route lookup on 2026-10-16 (`.local/openrouter-zdr-endpoints-2026-10-16.json`). One run of the plan is 20 answers (16 rated plus 4 planted) graded 3 times, 60 calls. The expected figure assumes about 4 bytes per input token and 6,000 output tokens per call; the worst case counts every input byte as a token and every call at its full output allowance, and is what `--max-usd` must cover.

| Model (route) | Price per million, input / output | Expected, 60 calls | Worst case |
| --- | --- | --- | --- |
| anthropic/claude-sonnet-5.5 (google-vertex/global) | $2 / $10 | $5.59 | $18.13 |
| google/gemini-4-argon (google-vertex/global) | $2 / $10 | $5.59 | $18.13 |
| anthropic/claude-opus-5.5 (google-vertex/global) | $4 / $20 | $11.19 | $36.26 |
| openai/gpt-5-mini (azure), the answer model | $0.25 / $2 | $0.97 | $4.91 |

A prompt revision on the development half adds about a third of a run.

## How agreement is measured

**Split.** Questions are split into a development half (Q1, Q3, Q5, Q7) and a held-out half (Q2, Q4, Q6, Q8); both versions of a question stay in the same half. The grader prompt may be revised only after looking at the development half. The held-out half is graded once, after the prompt is frozen, and is the result that counts. Q8 is held out because it holds both real grounding failures.

**Unit.** One unit is one check on one answer, about 170 units in all. Optional checks are left out, as they are from the score. Q6.C3 is left out because both ratings predate its rewording. The user's "fail" on the optional Q1.C4 and Q1.C5 is read as "not included".

**Measures**, reported separately for the development and held-out halves:

1. Agreement per dimension (coverage, grounding, example): the share of checks where the grader and the user agree.
2. False passes (the grader passes, the user fails) and false fails, listed check by check. A false pass on grounding is the most serious error.
3. Score agreement: the grader's overall total against the user's for v12 and v13, and whether the grader ranks v13 above v12 as the user did (82.3% against 79.3%).
4. Off-scope points per answer: the grader's count against the user's (9 for v12, 3 for v13).
5. Consistency: how many checks change rating across the three repeats.
6. Ungraded replies: invalid or truncated output, which never counts as a pass.

**Proposed bar for using the grader**, for the user to confirm:

- No grounding false pass on the held-out half or on the planted errors.
- Coverage agreement of at least 90% on the held-out half.
- The overall total within 3 percentage points of the user's for both versions, with the same ranking.
- Off-scope counts within one point per answer.
- Fewer than 5% of checks change rating across repeats.

**Disagreements are reviewed with the user before deciding.** Some will be rating slips on the user's side; the Q8 paste slip shows that this happens. Each disagreement is settled as a grader error, a rating error, or an unclear check. Ratings are corrected only with the user's agreement, and the grader is never tuned on the held-out half.

## Out of scope

New answer runs, changes to the answer agent, and a hosted load. If the grader passes, the next step is several runs per question on v13 to measure run-to-run variation.
