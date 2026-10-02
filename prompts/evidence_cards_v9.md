Extract useful evidence cards from one source group: a contiguous speaker
passage, personal note, or individual highlight. The group is the only
evidence. Use only what it states; do not add a name, setting, number, cause,
or outcome from the title, the section, or what you know or assume about the
rest of the document. A group may begin or end mid-thought; write cards only
for what the visible text completes. Set `evidence_group_index` to 0. The
application attaches the exact group span and attribution.

Return zero cards when no useful grounded fact exists, one card for one
coherent fact or episode, and multiple cards only for distinct facts or
episodes. Do not duplicate a fact to fill fields. Use `example`, `principle`,
`claim`, `counterexample`, `framework`, `tactic`, `mistake`, or `lesson` as the
card type. The short `statement` says what this one card establishes.

Fill a structured field when the group clearly states it, even when the
statement summarizes the same fact. Use concise, specific wording:

- `situation`: the starting context, problem, or constraint for this episode.
- `action`: what someone did or what the source explicitly advises doing.
  Say “did” only for reported behavior and “advises” for a recommendation.
- `result`: an observed or reported outcome of this episode. A hoped-for or
  predicted benefit is not a result. Do not invent a causal link.
- `principle`: a useful generalization supported by this group, only when it
  adds to the statement without inventing facts. It is marked as derived by
  the application. A principle stated directly may instead be the statement.

Use null when a field is absent or ambiguous. Preserve who said or did what,
including personal annotations. Do not infer unreported outcomes, intentions,
or examples. Avoid copying the same sentence mechanically into every field.

Keep each episode whole and separate. A group often holds an earlier episode
and a later change of behavior. The `action` and `result` of one card must
come from the same episode; never pair what the speaker does now with an
outcome of what they did before. Keep the direction of every statement:
ignoring, overriding, avoiding, or failing to do something is the opposite of
doing it, and must be worded that way.

Synthetic examples (not source facts):

- “When signups fell, I interviewed five customers and changed the form;
  signups recovered the next week.” One example card can have situation
  “signups fell,” action “the speaker interviewed customers and changed the
  form,” and result “the speaker reports signups recovered the next week.”
  Do not claim which action caused recovery.
- “If a form is confusing, ask a customer to try it.” A tactic may have action
  “advises asking a customer to try the form”; result is null.
- “We shortened the form and signups rose. Separately, support changed its
  reply template and response time fell.” Make two cards with separate
  action-result pairs.
- “I used to skip the pre-launch checklist, and twice we shipped a broken
  build. Now I run it before every release.” Make two cards. The mistake card
  has action “the speaker skipped the pre-launch checklist” and result “the
  speaker reports twice shipping a broken build.” The tactic card has action
  “the speaker now runs the checklist before every release” and result null,
  because no outcome of the new habit is reported.
- “We will learn more next week.” Return no card.
