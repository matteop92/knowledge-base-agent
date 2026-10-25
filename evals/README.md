# Evaluation

This folder measures two things: whether search finds the right passages, and whether the written answer is good. Everything here runs without a model call unless you pass `--live`. Reports are written under the ignored `.local/` folder.

## What is measured

**Search (retrieval).** `questions_*.yaml` holds 37 questions about six real source files: two interview transcripts, an article, a set of book highlights and two short notes. Each supported question points to the exact source text needed to answer it, 51 passages in total. Each file also has one question it cannot answer. Labels are bound to a document ID and the hash of the source text, so a changed source has to be relabelled before results are compared.

The score is Recall@K: how many of the 51 required passages appear in the top K results.

| Method | Top 5 | Top 10 | Top 20 |
| --- | --- | --- | --- |
| Vector search | 41 | 44 | 47 |
| Full-text search | 28 | 40 | 44 |
| Both, merged by rank | 39 | 44 | 47 |

Run it with `python -m evals.retrieval_trial` (a dry run checks the labels against the stored sources).

**Answers.** `answer_questions_v4_restructured.yaml` is the frozen answer checklist: 85 yes/no checks across eight questions, grouped into coverage (does the answer include the point), grounding (is the claim supported by the cited source) and example. It also lists, per question, a relevance scope, so points outside the question count against the answer. 179 source passages are verified against the source hashes. Prompt `answer_agent_v13` scores **63 of 80 scored checks (78.8%)**, with 3 points outside scope. The pass bar was 70%.

A model grader (`answer_grader.py`) can pre-rate answers, but it missed the pass bar on a held-out calibration, so a person confirms every rating. `answer_review.py` builds a page for rating saved answers and scores the export.

```bash
python -m evals.answer_review build <answer-receipt.json>... --output .local/review.html
python -m evals.answer_review score <judgments.json> --output .local/score.json
```

## Files

| File | What it is |
| --- | --- |
| `questions_*.yaml` | Source-span labels for the retrieval trial. |
| `fixture_*.yaml` | One checked span each for the synthetic sources in `sources/fixtures/`; they test the mapping without any real notes. |
| `answer_questions_v4_restructured.yaml` | The frozen 85-check answer checklist. |
| `answer_questions.yaml` | The earlier eight-question pilot rubric, still used by `answer_trial.py`. |
| `answer_rubric.py`, `answer_review.py`, `answer_trial.py` | Load the rubric, rate saved answers, run a bounded trial of one configuration. |
| `answer_grader.py`, `grade_answers.py`, `grader_agreement.py` | The model grader and how well it agrees with a person. |
| `speed_trial.py` | Times answers from a private question set through the web app's answer engine, with a spend ceiling and a shared ledger. |
| `retrieval_trial.py` | Recall@K for vector, full-text and merged search. |
| `compare_chunking.py`, `compare_embeddings.py`, `summarize_context_trials.py` | Trials used to pick the chunking, embedding model and enrichment. |
| `hosted_smoke.py` | A small check of a hosted database login. |

## Trial results that shaped the design

- **Chunking.** Structural chunks of the raw text (600 to 1,000 tokens) were selected over fixed-size chunks and over chunks with a model-written context sentence.
- **Summaries and evidence cards.** Model-written summaries and evidence cards did not pass a source-grounding review (for example, joining two separate highlights into one cause), so the live index has neither.
- **Embeddings.** The selected profile is six files, 83 vectors.

The enrichment trials cost about $1.43 in total.

## Running things

Run from the repository root with `pip install -e ".[server,test]"`. Trial scripts that call a model need `OPENROUTER_API_KEY` and a database URL in `.env`, and always take a `--max-usd` ceiling. The six source files are private vault notes that are not in this repository, so the retrieval and answer trials can be read here but not rerun from a fresh clone. The tests in `tests/evals/` run on any clone.
