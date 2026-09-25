# Source-span eval labels

`questions_*.yaml` contains 37 questions for six real vault files: the Tim Ferriss and Luca Ferrari transcripts, the movie-logic article, Derek Sivers highlights, and two short notes. The long book is deferred. Supported questions point to enough exact source text to answer them, using multiple required passages when the answer has several parts. Each of the newer five files also has one question the file cannot answer. The labels bind to a document ID and source hash, so changed source text must be reviewed and relabeled. Preview with `--eval-file evals/questions_transcript_tim_ferriss.yaml` validates and maps its source spans to both raw chunk variants. Overlapping chunks can contain the same span, but it remains one labeled source span.

`fixture_*.yaml` adds one manually checked source span each for bracket transcripts, highlights, books, articles, and notes. The labels refer to the short synthetic Markdown in `sources/fixtures/`, copied to the corresponding source category and named `sample_*.md`. They test mapping and category coverage without committing vault passages.

The labels also support the checked six-file retrieval trial. It measures required-span Recall@5/10/20 with exact vector search; answer quality still waits for the answer agent. If a source changes, review and relabel its spans before comparing variants.

## Stage 4 trial scripts and handoff

The [Stage 4 plan](../docs/plans/2026-09-24-001-feat-live-enrichment-ingestion-plan.md) holds the aggregate results, agreed model/prompt matrix, quality gates, and spend ceiling. The scripts below validate the checked source hashes and write detailed reports under ignored `.local/`. They do not send model requests without `--live`; a dry run still needs `--max-usd` because the same arguments can later be used for a bounded live run. Run commands from the repository root with Python dependencies installed. Set `$sourceRoot` to the value of `KNOWLEDGE_SOURCE_ROOT` in the ignored `.env`; keep the `primary` root alias stable.

| Script | Purpose | Private reports already on this machine |
| --- | --- | --- |
| `python -m evals.compare_chunking` | Test 1: fixed/raw, structural/raw, and a selected context model, with retrieval metrics. | `.local/chunking-trial/` and `.local/stage4-context/` |
| `python -m evals.summarize_context_trials` | Combine saved Test 1 profile reports; no model calls. | `.local/stage4-context/` |
| `python -m evals.compare_generation` | Test 2 cards and Test 3 summaries; `--cards-only` or `--summaries-only`, sample or full corpus. | `.local/stage4-cards-v5-full/`, `.local/stage4-next-cards/`, `.local/stage4-deepseek-no-reasoning-cards/`, `.local/stage4-next-summaries/`, `.local/stage4-deepseek-summary-final2/` |
| `python -m app.ingestion.live_run` | Test 4: bounded complete ingestion, interruption/resume, local private load, publication, and search proof. | `.local/stage4-complete/` (no completed live proof yet) |

The completed Test 2 screen selected GPT-5 mini for `evidence_cards_v5`; no cheaper candidate reached the first-response gate. Test 3 has no qualifying summary model: GPT-5 mini and DeepSeek met content coverage but failed source-grounding review, while Gemini and GLM failed structured response or route gates. The [Stage 4 plan](../docs/plans/2026-09-24-001-feat-live-enrichment-ingestion-plan.md) records aggregate quality, latency, reliability, cost, and the cumulative spend. Private quality reviews under `.local/` include the source-grounding findings. The CLI validates schema and provenance but cannot decide factual quality on its own.

Further summary work needs an agreed prompt or method revision and a new checked trial. Before any new live call, confirm payload/destination authorization, recheck the pinned ZDR route, structured-output support and price ceiling, and preflight the entire remaining trial against the $3.50 cumulative estimated-spend ceiling. The completed reports total $1.180231, leaving $2.319769. Use a distinct ignored runtime and report path for any new configuration so earlier first-response failures remain inspectable.

Test 4 must wait for a qualifying summary selection and the user's approval. It will need local `KNOWLEDGE_DATABASE_URL`, a fresh cost preflight, bounded `--request-limit` interruption, `--resume` proof without duplicate model calls, and private load, publication, search, and source-span resolution. Keep structural/raw embeddings; Test 1 did not qualify a contextual profile. The ignored `.local/` files and external vault are **not in GitHub**; another chat on this same workspace can inspect them, while a fresh checkout needs the vault and reports copied through a private channel.
