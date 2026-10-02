Check proposed evidence cards against their source. You receive one source
passage and the cards proposed from it. The passage is the only evidence:
judge every card against this passage alone, and ignore anything you know or
assume about the wider document.

Return one check per card, with the same `card_index`. For `statement`,
`situation`, `action`, `result`, and `principle`, answer:

- `supported`: the passage states this, with the same people, direction,
  certainty, and order of events.
- `unsupported`: anything else.
- `empty`: the card's field is null.

Mark a field `unsupported` when:

- it contains any detail the passage does not state: a setting, object,
  number, name, cause, motive, comparison, or a frequency word such as
  “often” or “many”;
- it reverses what the passage says, for example “did X” where the passage
  says the person ignored, overrode, avoided, or failed to do X;
- it pairs an action from one episode with an outcome from another, such as
  a current habit with the result of earlier behavior;
- it reports as done something the passage only advises, plans, or predicts,
  or reports as an outcome something only hoped for;
- it states as fact what the passage offers as a guess, theory, or question.

A `principle` is a derived generalization: mark it `supported` when it
follows from the passage without adding facts. Wording may differ from the
passage; meaning may not. When unsure, answer `unsupported`. Give a short
`reason` for each card that has an unsupported field, and null otherwise.
