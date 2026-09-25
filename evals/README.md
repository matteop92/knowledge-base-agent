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
| `python -m evals.compare_generation` | Test 2 cards and Test 3 summaries; `--cards-only` or `--summaries-only`, sample or full corpus. | `.local/stage4-cards-v5-full/report.json`, `.local/stage4-cards-v5-full/quality-review.json`, `.local/stage4-summaries-v14-expanded/report.json`, `.local/stage4-summaries-v14-expanded/quality-review.json` |
| `python -m app.ingestion.live_run` | Test 4: bounded complete ingestion, interruption/resume, local private load, publication, and search proof. | `.local/stage4-complete/` (no completed live proof yet) |

For the next **card screen**, use the same default 18-chunk sample and final `v5` prompt for each candidate. The example shows GLM; substitute one of `qwen/qwen3.5-35b-a3b`, `google/gemini-3.1-flash-lite`, or `deepseek/deepseek-v4.1-flash` and give each a distinct output path. The shared runtime directory allows the captures to be reused with `--resume`.

```powershell
$sourceRoot = '<value of KNOWLEDGE_SOURCE_ROOT in .env>'
python -m evals.compare_generation --source-root $sourceRoot --runtime-dir .local/stage4-next-cards/runs --output .local/stage4-next-cards/glm-report.json --models z-ai/glm-5.3-flash --cards-only --card-prompt-version evidence_cards_v5 --max-usd 3.50
```

For the next **summary screen**, `v15` uses each model's own `v14` section summaries. This example runs the three new models on the same three-document sample. First evaluate GPT-5 mini `v15` using its existing `v14` run directory and `--resume`; use `--all-summaries` for its six-document check.

```powershell
python -m evals.compare_generation --source-root $sourceRoot --runtime-dir .local/stage4-next-summaries/runs --output .local/stage4-next-summaries/report.json --models google/gemini-3.1-flash-lite deepseek/deepseek-v4.1-flash z-ai/glm-5.3-flash --summaries-only --summary-prompt-family v15 --max-usd 3.50
python -m evals.compare_generation --source-root $sourceRoot --runtime-dir .local/stage4-summaries-v14-expanded/runs --output .local/stage4-summaries-v15-gpt5/report.json --models openai/gpt-5-mini --summaries-only --all-summaries --summary-prompt-family v15 --resume --max-usd 3.50
```

The commands above are **dry runs**. Before any live call, confirm the plan's payload/destination authorization, recheck pinned ZDR routes and current prices, and total the new estimates with spend already incurred under the plan's $3.50 ceiling. Then add `--live --resume` and use a `--max-usd` no greater than the remaining approved budget. The code verifies the pinned endpoint's privacy, structured-output support, and price ceiling before generation. For a full card comparison add `--all-cards`; for a full summary comparison add `--all-summaries`. Use a separate output path for each run so reports are not overwritten. Review the manual useful-fact and summary checklists in the ignored quality-review files; the CLI reports schema, spans, counts, usage, and latency but does not decide factual quality on its own.

After selecting models for cards and summaries, preflight Test 4 with the command below, replacing the model IDs with the winners. This tested dry-run form counts **6 files, 83 chunks, and an upper bound of 266 requests** with the reference models. It generates contexts as artifacts but embeds structural/raw chunk text; do not add `--contextual-embeddings` unless Test 1 is reopened and a contextual profile qualifies.

```powershell
python -m app.ingestion.live_run --source-root $sourceRoot --context-model openai/gpt-4.1-mini --cards-model openai/gpt-5-mini --summary-model openai/gpt-5-mini --card-prompt-version evidence_cards_v5 --summary-prompt-family v15 --max-usd 3.50
```

The complete live run also needs local `KNOWLEDGE_DATABASE_URL`. Use `--request-limit` with `--live` for the bounded interruption and repeat the same command with `--resume` to reuse ready artifacts. Recalculate the cap after model selection and before live calls. Publish and search only after the selected quality gates pass. The ignored `.local/` files and external vault are **not in GitHub**; another chat on this same workspace can inspect them, while a fresh checkout needs the vault and reports copied through a private channel.
