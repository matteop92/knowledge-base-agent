# Evaluation

Quality is measured in two parts: whether search finds the right passages, and whether the answer is good. Both use questions about real notes, labelled by hand. The labels, the checklist and the scripts are in [`evals/`](../evals/README.md); this page gives the results.

## Search

37 questions over six real files (two interview transcripts, an article, a set of book highlights and two short notes). 31 are answerable and need 51 exact passages between them; the other 6 have no answer in the file and are not scored for recall.

Recall is the number of the 51 required passages that appear in the top K results.

| Method | Top 5 | Top 10 | Top 20 |
| --- | --- | --- | --- |
| Vector search | 41 | 44 | 47 |
| Full-text search | 28 | 40 | 44 |
| Both, merged by rank | 39 | 44 | 47 |

- Vector search is strong on this set, and keyword search misses paraphrases. Merging did not add coverage here; it is kept because keyword search handles exact names and phrases that a vector can blur.
- The embedding model, `perplexity/pplx-embed-v1-0.6b`, was chosen over `baai/bge-m3` in a six-file trial (42 against 40 of 51 passages at K=10). Indexing the corpus cost a fraction of a cent.
- Chunking: structural chunks reached 47 of 51 at K=20, fixed 800-token chunks 45. Adding a generated context sentence to each chunk lost one passage.

## Answers

A checklist of 85 yes/no checks over eight questions, in three groups: *coverage* (does the answer include the point), *grounding* (is the claim supported by the cited source) and *example* (does it give the examples the sources contain). For each question the checklist also records what is out of scope, and a point outside the scope counts as a failed check. 179 source passages back the checks.

| Prompt | Scored checks passed | Points outside scope |
| --- | --- | --- |
| `answer_agent_v13` (accepted) | 63 of 80 (78.8%) | 3 |
| `answer_agent_v12` | 70 of 87 (80.5%) | 9 |

The pass bar was 70%. v13 is lower on coverage (27 of 37 against 34 of 38) and better on relevance. One run per question cannot separate the two on overall score, so the choice favoured fewer off-topic points. v13's two grounding slips were an adaptation presented as the interviewee's own advice, and a verb that overstated the source.

A model grader (`evals/answer_grader.py`) was calibrated on held-out answers and missed the pass bar, so it only pre-rates answers; a person confirms every rating.

## Cost and speed

A typical hosted answer takes about 100 seconds and 12 model calls and costs about $0.04. The eight benchmark questions ran between 16 and 150 seconds locally. The enrichment trials that were later dropped cost about $1.43 in total.

## What the tests cover

The seven test suites (`tests/ingestion`, `models`, `storage`, `agent`, `api`, `capture`, `evals`) use short synthetic fixtures and fake model clients, so they cost nothing and need no key. Tests that touch the database run against a throwaway Postgres. GitHub Actions runs all seven suites on every pull request.

## Limits of this evidence

- Six files and 37 questions is a small test set. The results show that the approach works on this corpus, not how it scales to hundreds of files.
- The answer checklist covers eight questions and was rated by one person.
- One run per question per prompt. The difference between v12 and v13 is within the noise of a single run.
