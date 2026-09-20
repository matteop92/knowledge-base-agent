# Initial transcript eval labels

`transcript_questions.yaml` contains eight manually selected questions for the first podcast transcript. Positive questions point to exact source spans in speaker turns; the negative question is marked unsupported **by this episode**. The episode does mention cold email for pitching conference organizers, but it does not give a quantified first-20-customer outreach case. These labels are tied to both `document_id` and `source_sha256` and can be checked during preview with `--eval-file evals/transcript_questions.yaml`.

There are no chunk IDs yet. Later chunking experiments should map each source span onto their own chunk sets and compare retrieval on the same source revision. If the transcript changes, review and relabel these questions; a matching document ID alone is insufficient.

These are seed questions, not the full 30–50-question retrieval and answer eval described in `FOUNDATION.md`. Recall@20 and final-answer checks begin when retrieval and the agent exist.
