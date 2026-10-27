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

A model grader (`evals/answer_grader.py`, `gpt-5-mini` at medium reasoning, prompt v4) pre-rates answers; a person confirms ratings that decide a prompt. On held-out answers it agreed with the person on 97.1% of grounding checks with no false pass, caught 4 of 4 planted errors, but agreed on only 75% of coverage checks (bar: 90%) and scores about 11 points above the person. `gpt-6-luna` as grader was worse (50% grounding agreement). It is used to compare configurations graded the same way, read per dimension, not as an absolute score.

Reasoning effort: `gpt-5-mini` at medium beat low and minimal on answer quality in an earlier test, so it is fixed and was not rerun.

## Routing, catalog answers and speaker attribution

The eval sets about the vault (questions, expected results, the exported feedback) live under the ignored `.local/` folder because they name real notes. This section records numbers only.

**Router (TypeSafe Jev 1.14, `prompts/question_router_v1.json`).** The 67 labelled questions from the 2026-10-26 experiment, run again on the final spec as one combined call per question, in three option orders. A route counts as right when it matches the label; narrowing properties are the accepted type or folder, a period when the label has one, and "newest" when it has one.

| Option order | Right at 0.6 | Plain sent to another route | Narrowing properties |
| --- | --- | --- | --- |
| plain, narrowed, catalog (used) | 66 of 67 | 0 | 16 of 16 |
| catalog, narrowed, plain | 66 of 67 | 1 | 16 of 16 |
| narrowed, catalog, plain | 65 of 67 | 1 | 16 of 16 |

The one miss in the used order is a catalog question that went plain (route probability 0.45), which is the safe side. The thresholds stay at 0.6 for narrowed and catalog: 0.5 matched it in the used order but sent three plain questions elsewhere when catalog was listed first, 0.7 and 0.8 lost routes and a narrowing property. The used order is the only one that sent no plain question to another route. A confident yes to a filter whose value comes back as none (a person or tag that is not in the vault) sends the question to plain instead of dropping the filter, which would have widened a count to every source. A wording that asked one set of yes/no questions for both narrowing ("use only…") and catalog filters ("how many articles") dropped the type and period from catalog plans, so the two now have separate questions.

A second set of 30 questions (held-out phrasings, "latest" as a narrowing, as a catalog sort and as a topic word, and rewritten follow-ups) scored 24 correct at 0.6 in the used order with 8 of 8 narrowing properties right. Three of the six misses are catalog questions about people who are not in this vault, which now go plain (the safe side). The others: two plain questions that mention a kind of source ("the saved podcast episodes", "in the transcripts") and so were narrowed (the labels are debatable; a wrong narrowing still widens to the whole vault when it finds nothing), and one follow-up count that went plain. The first set was used to tune the wording and the second was written afterwards, so the second is the fairer estimate.

**Catalog answers.** 26 questions with expected results taken from the database's own listing (counts, authors, guests, tags, dates, newest and oldest), not from the catalog code. Through the real router and catalog code over a source list rebuilt from that listing, 25 of 26 match. The miss was routed narrowed ("which book highlights did I add first"). These are drafts until each is approved.

**Seed evals.** Both evals made from the two flagged turns fail on the saved answers: the catalog answer listed seven notes with no result set, and the attribution check finds the sentence crediting Tim Ferriss with a passage spoken by Niels Hoven (the speaker is found from the stored run). A corrected sentence that names Niels Hoven passes. Runs of the new routing and prompts against the live database, the v13 run-to-run spread, v15 against v13 on the 85-check list, and the v16 follow-up read have not been done yet, so v13 and v14 stay the answer prompts.

## Cost and speed

A speed set of 25 questions (6 count or list questions about the sources, 8 single-source, 8 multi-source and 3 follow-ups; 5 are real questions asked in the app and 8 are the checklist questions), each run 3 times through the web app's answer engine against the hosted database. Times are the answer's elapsed time; model time is the sum of model request times.

| | Before | After |
| --- | --- | --- |
| Elapsed p50 / p95 | 114 s / 280 s | 37 s / 92 s |
| Model time p50 / p95 | 69 s / 151 s | 20 s / 65 s |
| Model calls per answer | 5.5 | 1.7 |
| Cost per answer | $0.020 | $0.008 |
| Failed answers (of 75) | 9 | 1 |
| Graded checklist score | 89.3% | 88.9% |

"Before" is the answer loop with two host fixes that change nothing the model sees (the database cuts each passage instead of sending the whole note, and the model route is checked once per answer); without them a laptop run hit the 300 s limit. "After" adds the router for count and list questions, six chunks read by the host before the first model turn, and no finish sent back for unread chunks. Count and list questions went from about 200 s to 8 s, multi-source questions about 61% faster, single-source p95 46% faster with the median unchanged.

Where the time went before, on the eight checklist questions: finishes sent back for unread chunks 40%, turns that only chose a chunk to read 33%, the accepted finish 24%, the host about 2%. Other causes checked:

- Prompt caching already works: 95 to 99% of input tokens per call were served from the cache.
- Repeated searches: 28 of 49 model searches repeated an earlier query, but the loop already refuses a repeat without running it, so a search cache would save nothing.
- Tighter limits: the faster loop averages 1.7 model calls, well under the cap of 12, so a lower cap would only cut off the rare long answer.
- Running the first search alongside the first model call: the first call needs the search results, and the host's search and reads take about 2% of the time. Streaming does not shorten a JSON action.

Alternatives:

| Option | Elapsed p50 / p95 | Cost per answer | Graded score |
| --- | --- | --- | --- |
| Chosen: no finish sent back | 37 s / 92 s | $0.008 | 88.9% |
| One finish sent back (`coverage_checks = 1`), the fallback if answers feel incomplete | 48 s / 106 s | $0.013 | 90.2% (19 answers) |
| Ten chunks read up front | slower than six on the checklist questions | $0.015 | 87.7% |

`gpt-6-luna` as the answer model had a worse p95 and was dropped. The scores differ by less than the grader's own noise, so the faster option was chosen.

## What the tests cover

The seven test suites (`tests/ingestion`, `models`, `storage`, `agent`, `api`, `capture`, `evals`) use short synthetic fixtures and fake model clients, so they cost nothing and need no key. Tests that touch the database run against a throwaway Postgres. GitHub Actions runs all seven suites on every pull request.

## Limits of this evidence

- Six files and 37 questions is a small test set. The results show that the approach works on this corpus, not how it scales to hundreds of files.
- The answer checklist covers eight questions and was rated by one person.
- One run per question per prompt. The difference between v12 and v13 is within the noise of a single run.
