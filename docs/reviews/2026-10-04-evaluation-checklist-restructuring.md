# Evaluation checklist restructuring review

Proposal: [answer_questions_v4_restructured.yaml](../../evals/answer_questions_v4_restructured.yaml).

The existing active draft and frozen version-three rubric remain intact. This proposal changes the checklist, not the answer runner or grader. It remains a draft pending the decisions below and schema integration.

## Result

- Exactly three dimensions: coverage, grounding, example.
- 92 checks: 52 coverage (35 primary, 17 detail), 32 grounding, eight example checks.
- The count increases from 91 because bundled requirements are split. Coverage omissions create fewer independent penalties: details become N/A when their primary is absent.
- Question objects contain only id, question, purpose, answerability, sources, checks.
- All eight question wordings, purposes, answerability values, and source mappings are unchanged.
- Existing valid evidence alternatives are carried forward. One reference for Q5's decision-deadline rationale is widened to the already captured source context.
- Applicable checks use pass/fail; unreviewed remains unreviewed. No half credit, weights, numeric percentages, or new final acceptance gate are introduced.

## Removed or merged

- Removed all eight generic gap checks. No new scored disclaimer requirement replaces them.
- Removed all eight broad usefulness checks. Relevant content is retained in existing primary checks, supporting details, example checks, and grounding checks. Organization and generic clarity preferences are not scored.
- Removed question-level essential_facets, suggested_examples, alternative_evidence, qualifications, prohibited_inferences, useful_answer. Their substantive constraints are preserved in detailed checks or the general factual-support check; suggested illustrations remain evidence options, not a demand to enumerate every example.
- Removed question-level review_status and check-level legacy_facet_ids from the proposal. Approval and old-ID mapping remain review/workflow information rather than scored content. Current loader compatibility must be updated before activation.
- Removed Q1.C10's vague “ownership of products over time” requirement: long-term ownership is already covered by Q1.C9. Its cited span describes integration into a shared operating platform rather than an additional long-term-ownership principle. Adding that as a distinct requirement needs review rather than an assumption.
- Merged Q3.C2 and the general Q3.C7 example requirement into Q3.E1. As approved, any concrete inspected-source example can satisfy it when it illustrates a customer benefit, customer proof, an easier next step, or message testing, and the answer explains that connection. Mentava is an evidence option rather than a mandatory anecdote. Invented or unrelated examples fail.
- Merged Q4.C13 into Q4.C2: interrupt the heavy breathing pattern by taking a deliberate breath. This is one breathing intervention with one judgment and its original evidence span.

## Reclassified and rewritten

| Question | Changes |
| --- | --- |
| Q1 | C2 isolates development and C3 isolates autonomy. C22 makes simplification the primary idea; C7 and C8 are its mechanisms. C18 and C20 are optional details of centralized hiring (C4). C19 is the detail of capacity pressure (C6). C21 preserves intrinsic motivation as an optional detail of variable pay (C5). The former qualification is split into correct speaker attribution and supported product-level scope under grounding. |
| Q2 | Customer benefit and audience choice become primary checks; responding to customer requests and explicit exclusion become details. Willingness to pay and responding to lukewarm demand (C2) are details of strong public response (C1). C2 is N/A when C1 is absent. Reader-note attribution, immediate-abandonment overclaims, and ignoring legal obligations become separate grounding checks. |
| Q3 | Purchase-intent signals are a detail of wording tests. Tim-versus-Niels attribution and transferring Mentava facts to the user's company are grounding checks. No particular wording-test method is required. |
| Q4 | Combine the breathing intervention in C2, and separate detachment from its supporting “weakness” label. Buying time and considering the next move are details of slow nodding. At least one original warning sign suffices; the answer need not enumerate every physical sign. The clinical-treatment qualification becomes grounding. |
| Q5 | Keep the two original rules as primaries. The reason for each rule, previously bundled into usefulness, becomes a detail. Eliminating uncertainty becomes a prohibited overclaim under grounding. |
| Q6 | Separate asking instead of assuming from its detail of stating the observed problem. Unknown facts about the user's situation and agent-adapted wording are judged under grounding. |
| Q7 | Retain the three constraints as primary ideas. Source-versus-agent synthesis, first-month promises, and an unsupported quantified outreach playbook are grounding checks. |
| Q8 | Coverage tests whether hiring and management ideas are present; attribution is judged separately. The question-led feedback suggestion is identified as reader-added “My note,” not quoted Sivers text. Ownership advice stays separate from hiring practices. No invented dialogue or source-provided two-person recruiting system is allowed. |

## Primary/detail decisions requiring review

These are preserved provisionally as independent primaries, matching their existing independent treatment. They are flagged rather than silently made conditional:

| Check | Why its role is ambiguous |
| --- | --- |
| Q1.C3 relative to Q1.C1 | Autonomy can be an independent management principle or a supporting condition for getting value from talented people. The draft keeps it independent. |
| Q6.C2 relative to Q6.C1 | Asking rather than assuming can be a distinct conversation tip, or a method of raising the difficult issue directly. The draft keeps it independent. |
| Q8.C4 relative to Q8.C11 | Question-led feedback can be a distinct management recommendation, or a mechanism for preserving ownership. The draft keeps it independent. |

Q1.C2 remains primary as approved: developing promising people can be included independently of talent-first hiring criteria.

## Other decisions requiring review

- Q4/Q5 example boundary: these short notes describe tactics and reasoning rather than extended case studies. The existing conditional example checks are preserved. Decide whether a concrete source-described tactic counts as an example or whether a distinct source situation is necessary.
- Q1.C10: its original evidence is about shared-platform integration. Decide whether that is an intended independent idea before adding it to the revised coverage checklist.
- Optional means useful extra content that is not needed for a complete answer. Omitted optional checks are N/A; included content is judged pass/fail and reported separately. Optional checks are excluded from the scored numerator and denominator and earn no bonus. Their details also require a passing parent. Grounding still applies to any optional claims. This applies to Q1 centralized hiring, variable pay, and their details, and requires schema support before activation.
- A factual/attribution/qualification error should be assigned to its most specific grounding check and counted once. General factual support should not count the same error again. This is a proposed review rule; the current grader does not enforce it.
- The current freeze logic requires question-level review_status. Since the proposal removes it as requested, approval tracking must be integrated separately before activating or freezing this checklist.

## Verification

Parsed the YAML and checked question wording, metadata, source mappings, unique IDs, allowed dimensions, parent existence and primary status, and conditional parent gates. Checked evidence bounds against all six captured source snapshots and verified their original byte hashes. No model calls were made. No executable scoring code changed; the current grader cannot load this proposal until its schema and dependency handling are updated.
