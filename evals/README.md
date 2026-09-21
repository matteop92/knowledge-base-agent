# Source-span eval labels

`transcript_questions.yaml` contains eleven manually selected questions for the Tim Ferriss episode. Positive questions point to exact source spans; the negative question is unsupported by that episode. Labels bind to both document ID and source hash. Preview with `--eval-file evals/transcript_questions.yaml` validates and maps each required and useful span to both raw chunk variants. Overlapping chunks can contain the same span, but it remains one labeled source span.

`fixture_*.yaml` adds one manually checked source span each for bracket transcripts, highlights, books, articles, and notes. The labels refer to the short synthetic Markdown in `sources/fixtures/`, copied to the corresponding source category and named `sample_*.md`. They test mapping and category coverage without committing vault passages.

`real_*.yaml` contains 26 questions for five other representative vault files: the movie-logic article, Derek Sivers highlights, two short notes, and the Luca Ferrari transcript. Each file has supported questions tied to exact source text and one question the file cannot answer. The long book is deferred. Together with the eleven Tim Ferriss questions, these make a 37-question seed set across the observed source formats. The YAML stores short evidence excerpts, document IDs, and source hashes, so a changed source must be reviewed and relabeled.

These checks report source coverage and label containment. Recall@5/10/20, ranking, and answer quality must wait for a searchable index and agent. The Tim Ferriss set is a seed for the later 30–50-question gold set, not a retrieval score. If a source changes, review and relabel its spans before comparing variants.
