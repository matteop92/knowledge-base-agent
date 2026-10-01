# Evidence card field annotation, version 1

Use the captured source group as the authority. A card's exact span, source hash,
chunk, content role, and speaker must agree with the captured source before any
field is scored. Keep private excerpts and per-group judgments in `.local/`.

For each group, first decide whether it contains zero, one, or several distinct
useful facts. A fact or episode needs one coherent card. Separate independent
episodes or lessons into separate cards, even when they occur in one group.
Do not duplicate a card to improve field counts. A group with no useful,
grounded fact should yield no card.

Annotate each applicable field from the original group **before** viewing a
candidate card. Use `supported`, `absent`, or `ambiguous`. Record a short reason
for `ambiguous` and for any `supported` field that the old card left empty.
An absent field is a correct null, not a missed opportunity. An ambiguous field
does not enter the recall denominator. A supported field with no grounded card
from its group is missed.

| Field | Supported when | Absent when |
| --- | --- | --- |
| `situation` | The group gives a particular starting context, problem, or constraint for this fact. | It gives only general advice or a claim without a particular context. |
| `action` | It states what someone did or explicitly advises a step. Preserve `did` versus `advised` in the value. | It states no behavior or recommended step. |
| `result` | It reports an observed outcome of this episode, with the right attribution. | It only predicts a benefit, expresses a goal, or states an action without an outcome. |
| `principle` | A concise generalization follows from this group without adding a causal claim; label it as derived. | A generalization would add facts, or merely restate the card statement. |

The statement carries the short, useful fact. A structured field can repeat the
same underlying fact in more specific wording when the source supports it, but
should not mechanically copy the statement. A directly stated principle may be
the statement instead of a derived `principle` field.

Synthetic boundary examples:

- **One card.** “When signups fell, I interviewed five customers, then changed
  the form; signups recovered the next week.” Situation: falling signups;
  action: interviews and form change; result: reported recovery. The source
  does not establish which of the two actions caused the recovery.
- **Advice without result.** “If a form is confusing, ask a customer to try it.”
  Situation and advised action are supported. Result is absent; a predicted
  improvement is not an observed result.
- **Multiple cards.** “We shortened the form and signups rose. Separately,
  support changed its reply template and response time fell.” These are two
  episodes with separate action-result links, even though one group contains
  both sentences.
- **Zero cards.** “We will learn more next week.” No useful grounded fact or
  observed outcome is available.
- **Attribution boundary.** If one speaker reports an action and the next
  speaker reports a result, neither card may join them into one episode unless
  a single source group explicitly establishes that link. A personal annotation
  remains a personal note, not the book author's claim.

Reserve whole generation chunks for prompt development or validation. Cards
from one chunk, including cards from different groups, stay in the same split.
Do not inspect candidate validation outputs until the prompt and field-recall
threshold are fixed. Check zero, one, and multiple-card groups in both source
review and candidate review.

## Preregistered candidate screen

The current selected profile has 510 cards: 468 have all four fields null;
`situation`, `action`, `result`, and `principle` occur 9, 13, 6, and 25 times.
The source-bound version-1 audit labels 27 groups in 14 chunks across all six
documents. It marks 56 supported fields (23 situation, 22 action, 7 result,
4 principle); the current selected cards populate 1 of those 56. Seven labels
are reserved in whole validation chunks. Private identities, excerpts, and
reasons are in `.local/card-field-labels-v8.json`.

Before seeing candidate output, require at least 50% grounded coverage of the
56 applicable fields, including at least half of the supported situations,
actions, and results. Require an absolute improvement of at least 40 percentage
points over the old-card baseline. Ambiguous and absent fields stay outside
the denominator. A candidate with no grounded card for a labeled group misses
all supported fields in that group. These numeric gates supplement the existing
18-chunk requirement for 18/18 first-response ready artifacts, at least 17/18
useful facts, exact spans, and zero observed unsupported details or attribution
errors. Validation chunks are reviewed once after the prompt and thresholds
are frozen; a failed screen does not authorize an 83-chunk expansion.
