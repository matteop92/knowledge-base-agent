# Stage 4: live enrichment and ingestion readiness

**Status:** Test 1 is complete. Tests 2 and 3 have GPT-5 mini reference results but need the agreed cheaper-model screens and final-prompt comparisons. Test 4 is implemented but its live interruption/resume and local load/search proof remain pending. Vault text, model outputs, and detailed trial reports remain in ignored `.local/`.

## Results so far

| Test | What was run | Result and current decision |
| --- | --- | --- |
| 1. Chunk context and retrieval | Five comparable model profiles on the same 83 structural chunks, six files, 31 questions, and 51 required spans; two more pinned routes returned 429. | Structural/raw covered **41/44/47** spans at K=5/10/20 and stays selected. GPT-5 mini context covered 42/45/47, but a reviewed context misattributed a Derek Sivers highlight; no contextual profile passed the full gate. No further context-model screen is planned. |
| 2. Evidence cards | GPT-5 mini `evidence_cards_v5` on all 83 chunks; Qwen3.5 35B-A3B on 18 chunks with the earlier `v3` prompt. | GPT-5 mini returned **83/83** ready artifacts on the first attempt and 496 cards with valid source spans. It covered **17/18** useful-fact checks; a stratified 40-card review found no unsupported claim or attribution error. Qwen returned **18/18** in its earlier screen, but has no comparable `v5` result. The least-cost qualifying choice remains open. |
| 3. Section and document summaries | GPT-5 mini summary family `v14` across six files. | **87/87** artifacts were ready on the first attempt. Conservative review found **39/45** document-content points, short of the 90% gate; six document summaries had no observed unsupported claim or attribution error. `document_summary_v15` is prepared but untested live. |
| 4. Complete local ingestion | Implementation and local tests only. | The bounded live interruption/resume, six-file load, publication, search, and source-span resolution proof remains open. |

The table is the durable aggregate recap. Source text, generated artifacts, per-question changes, manual review notes, and request-level costs stay in ignored `.local/`; do not copy private vault content into this plan.

### Test 1 model results

| Embedding profile | Required spans at K=5/10/20 | Disposition |
| --- | --- | --- |
| Structural/raw control | **41/44/47** | Selected. |
| GPT-4.1 mini context | 41/44/46 | Below the raw control at K=20. |
| GPT-5 mini context | 42/45/47 | Retrieval improved slightly, but reviewed context had an attribution error. |
| Mistral Small 3.2 context | 41/42/47 | Below the raw control at K=10. |
| Qwen3.5 9B context | 41/43/46 | Below the raw control at K=10 and K=20. |
| Qwen3.5 35B-A3B context | 41/41/48 | Higher K=20 span count did not offset the K=10 loss. |
| GPT-6 Luna and gpt-oss-20b | No comparable result | Their pinned routes returned 429. |

## Corpus and controls

Use the six checked vault revisions in `evals/questions_*.yaml`: 83 deterministic structural chunks, 31 supported questions, and 51 required source spans. Refuse changed source hashes or boundaries. The completed context comparison held structural chunks, `chunk_context_v2` prompt and input recipe, `perplexity/pplx-embed-v1-0.6b` document/query embeddings, exact cosine search, and the question set fixed. Fixed 800-token/raw and structural/raw were controls; context was added only to structural chunks. Cards and summaries do not enter the embedding input. GPT-4.1 mini was rerun on the fresh corpus and route alongside the new context candidates.

## Candidates and route policy

The completed context screen compared `openai/gpt-4.1-mini` (incumbent), `openai/gpt-5-mini`, `openai/gpt-6-luna`, `qwen/qwen3.5-9b`, `qwen/qwen3.5-35b-a3b`, `mistralai/mistral-small-3.2-24b-instruct`, and `openai/gpt-oss-20b`. The latter four have Apache 2.0 weights. Their vendor cards report instruction-following or long-context strengths; those were screening evidence, not proof on this vault: [Qwen 9B](https://huggingface.co/Qwen/Qwen3.5-9B), [Qwen 35B-A3B](https://huggingface.co/Qwen/Qwen3.5-35B-A3B), [Mistral Small 3.2](https://huggingface.co/mistralai/Mistral-Small-3.2-24B-Instruct-2506), [gpt-oss-20b](https://huggingface.co/openai/gpt-oss-20b).

| Model | Pinned endpoint tag | Maximum observed input/output USD per million tokens |
| --- | --- | --- |
| GPT-4.1 mini, GPT-5 mini, GPT-6 Luna | `azure` | 0.40/1.60, 0.25/2.00, 0.10/0.50 respectively |
| Qwen3.5 9B | `siliconflow/fp8` | 0.10/0.15 |
| Qwen3.5 35B-A3B | `deepinfra/fp8` | 0.14/1.00 |
| Mistral Small 3.2 24B | `deepinfra/fp8` | 0.075/0.20 |
| gpt-oss-20b | `akashml/fp4` | 0.02/0.10 |
| Perplexity embed 0.6B | `perplexity/int8` | 0.004/0 |

### Agreed next screen

| Test and prompt | Reference already run | Models to screen next | Expansion if the gate passes |
| --- | --- | --- | --- |
| 1: `chunk_context_v2` | Structural/raw and five comparable context profiles | None; context choice is settled for this corpus. | None. |
| 2: `evidence_cards_v5` | GPT-5 mini on 83 chunks | Qwen3.5 35B-A3B, Gemini 3.1 Flash Lite, DeepSeek V4.1 Flash, GLM-5.3-Flash on the same 18 preselected chunks. | Run qualifying cheaper candidates on all 83 chunks using `v5`, then compare quality, reliability, latency, and measured cost with GPT-5 mini. |
| 3: `section_summary_v14` + `document_summary_v15` | GPT-5 mini `v14` on six files; its `v15` document revision remains untested. | GPT-5 mini `v15`, plus Gemini 3.1 Flash Lite, DeepSeek V4.1 Flash, and GLM-5.3-Flash on the same three checked documents. Each model uses its **own** `v14` section summaries as input to `v15`. | Run qualifying candidates on all six files; compare the same document-content checklist and first-response success. |
| 4: selected card and summary prompts | No live complete run | No new model screen; use the selected qualifying models and structural/raw embeddings. | One bounded, resumable six-file ingestion and local private load/search proof. |

Qwen's prior card screen used `v3`, so it is historical evidence, not a head-to-head `v5` result. Its earlier summary screen had attribution errors; the new summary slate excludes it. Grok is excluded by agreement. Google, DeepSeek, and GLM are candidates because their observed eligible routes are cheaper than the [GPT-5 mini reference](https://openrouter.ai/openai/gpt-5-mini) in both input and output tokens, with structured-output support; vendor claims alone do not qualify a model.

| New candidate | Pinned eligible endpoint observed | Observed input/output USD per million tokens | Model evidence |
| --- | --- | --- | --- |
| [Gemini 3.1 Flash Lite](https://openrouter.ai/google/gemini-3.1-flash-lite) | `google-vertex/global/flex` | 0.125/0.75, Flex service tier | [Google model documentation](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite); [Flex tier behavior](https://openrouter.ai/docs/guides/features/service-tiers) |
| [DeepSeek V4.1 Flash](https://openrouter.ai/deepseek/deepseek-v4.1-flash) | `deepinfra/fp8` | 0.14/0.42 | [Open-weight MIT model card](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) |
| [GLM-5.3-Flash](https://openrouter.ai/z-ai/glm-5.3-flash) | `deepinfra/fp4` | 0.075/0.25, promotional | [Open-weight MIT model card](https://huggingface.co/zai-org/GLM-5.3-Flash) |

These are observed route and price snapshots, not guaranteed future availability. The Gemini Flex route trades lower price for lower availability and potentially higher latency; use that pinned service tier for a fair price comparison. Recheck all routes, prices, privacy flags, and structured-output support immediately before calling. If an eligible route disappears, record the omission and revise the comparison rather than silently substituting a different tier.

Immediately before live calls, inspect [current endpoints](https://openrouter.ai/docs/api/api-reference/endpoints/list-endpoints) and [ZDR endpoints](https://openrouter.ai/docs/api/api-reference/endpoints/list-endpoints-zdr). Pin an eligible endpoint tag per model and use `only`, `allow_fallbacks: false`, `require_parameters: true`, `data_collection: deny`, and `zdr: true` on every request, including embeddings. Verify structured-output support for generation. Record endpoint and price in the private trial report. Omit a candidate if no suitable route remains. Each command is explicit `--live --max-usd`; request and estimated-token budgets are enforced. Preflight the whole agreed trial against a **$3.50 estimated-spend ceiling**, then track measured usage. Recheck prices if a route changes.

## Test 1: chunk context and retrieval

Completed on the checked six-file corpus. Each comparable candidate generated context for all 83 structural chunks; unchanged structural text plus context was embedded with the fixed Perplexity model. Profiles were compared with fixed/raw and structural/raw at Recall@5/10/20, counting covered required spans and complete questions, with per-question gains/losses, p50/p95 latency, tokens, and estimated cost recorded in the private reports. Stratified context and changed-outcome review checked unsupported claims, lost attribution, and repetition. The acceptance gate required at least 44/51 spans at K=10 and 47/51 at K=20, no lost complete question at K=20 versus structural/raw, and no invented fact or attribution error in reviewed contexts. No profile passed the whole gate, so structural/raw stays selected for this corpus.

## Test 2: evidence cards

Screen the four candidates in the agreed slate on the **same preselected 18 structural chunks**: both note chunks, all three article chunks, three evenly spaced highlights chunks, and five evenly spaced chunks from each transcript. The exact IDs were saved before the first card screen. Use `evidence_cards_v5` for every candidate. Assess successful first response, exact quote/offset validation, source attribution, useful-fact coverage against the existing source-derived checklist, cards per chunk, latency, and cost. Require 100% exact quote/span validity for accepted cards, at least 95% first-request structured success (**18/18** on this sample), no fabricated claim or attribution error in manual review, and at least 90% useful-fact coverage (**17/18** checks). Empty lists are valid for chunks with no useful grounded card. Expand each qualifying cheaper candidate to **all 83 chunks** and choose the least expensive model that still meets the quality and reliability gates. Record failures; do not silently repair them.

## Test 3: section and document summaries

First run the prepared GPT-5 mini `document_summary_v15` revision against its own `section_summary_v14` outputs. Then screen Gemini 3.1 Flash Lite, DeepSeek V4.1 Flash, and GLM-5.3-Flash on the same checked short note, article/highlights source, and transcript. Use unchanged structural chunks and hierarchy; generate each candidate's own `v14` section summaries before its `v15` document summary. Review the prewritten central-idea checklists, factuality, attribution, uncertainty, first-response structured success, section/document latency, and cost. Require no invented fact or attribution error, complete structured responses, and at least 90% checklist coverage: **20/22** points for the three-document screen. Expand each qualifying cheaper candidate to all six checked files; the expanded **45-point** review needs at least **41/45** points. Choose the least expensive model that meets the full quality and reliability gates.

## Test 4: complete local ingestion

After selecting the qualifying card and summary models, preflight the full live run again with their actual routes, token estimates, and the cumulative trial spend under the $3.50 ceiling. Run all six files through context, cards, summaries, and structural/raw embeddings. Interrupt one run after a bounded batch and resume it, showing ready artifacts are reused with no duplicate model calls. Validate manifests, load all six runs into local private Supabase, publish the structural/raw profile, search checked queries, and resolve returned spans to captured source bytes. Record counts, failures, model/prompt/endpoint versions, measured latency and spend, and limitations in an IDs-only private report. The earlier dry-run estimate of 266 requests and about $2.00 used the old candidate mix; recompute it before Test 4.

Do not mark Stage 4 complete until the quality review and resumable load/search proof pass. Reconcile `FOUNDATION.md#current-progress` and `INGESTION.md` with accepted facts after implementation.
