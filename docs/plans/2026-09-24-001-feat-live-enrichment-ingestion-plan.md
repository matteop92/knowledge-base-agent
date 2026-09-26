# Stage 4: live enrichment and ingestion readiness

**Status:** Tests 1 and 2 selected structural/raw embeddings and GPT-5 mini `evidence_cards_v5` for the checked corpus. Test 3 found no qualifying summary configuration; summaries are deferred and excluded from the selected ingestion profile. The user approved this narrower Stage 4 acceptance gate. Test 4 still needs the user's separate approval; its live interruption/resume and local load/search proof have not run. Vault text, model outputs, and detailed trial reports remain in ignored `.local/`.

**Current model preference:** Until the user revises it, GPT-5 mini remains the default for further Stage 4 generation experiments and the selected `evidence_cards_v5` configuration. The selected ingestion path uses structural/raw embeddings and cards; it makes no context or summary generation calls. The approved `v16`/`v17` summary prompt follow-up found factual links that instructions alone did not prevent. Any later summary publication needs a separately checked source-grounding method.

## Results so far

| Test | What was run | Result and current decision |
| --- | --- | --- |
| 1. Chunk context and retrieval | Six comparable model profiles on the same 83 structural chunks, six files, 31 questions, and 51 required spans; two more pinned routes returned 429. DeepSeek was added in an approved follow-up. | Structural/raw covered **41/44/47** spans at K=5/10/20 and stays selected. DeepSeek context tied those totals but misclassified an author's highlight as a personal note in the 24-position source review. GPT-5 mini context covered 42/45/47 but also had a reviewed attribution error. No contextual profile passed the full gate. |
| 2. Evidence cards | GPT-5 mini `v5` full corpus, the original four cheaper-model `v5` screens, and an approved DeepSeek prompt follow-up through all 83 chunks. | GPT-5 mini returned **83/83** first-response artifacts, 496 valid cards, **17/18** useful-fact checks, and no issue in a 40-card review. DeepSeek `v7` reached **18/18** on the screen and **83/83** full with 744 valid cards, but covered only **15/18** useful facts and misattributed one card in its own 40-card review. **GPT-5 mini remains the only qualifying card model tested.** |
| 3. Section and document summaries | Original `v14`/`v15` comparison, then an approved GPT-5 mini `v16`/`v17` prompt follow-up with freshly generated sections. | GPT-5 mini `v17` returned **87/87** first-response artifacts and covered **42/45** content points on all six files, but its document summary joined two separate Sivers highlights into one causal event. `v16` reversed an outcome in the three-document screen. The original GPT-5 mini and DeepSeek `v15` outputs also had factual/attribution errors. **No summary model qualifies.** |
| 4. Complete local ingestion | Cards and structural/raw embeddings are selected; summary and context generation are omitted from the approved profile. Implementation and local tests only. | The bounded live interruption/resume, six-file load, publication, search, and source-span resolution proof remains open and needs separate user approval. |

The table is the durable aggregate recap. Source text, generated artifacts, per-question changes, manual review notes, and request-level costs stay in ignored `.local/`; do not copy private vault content into this plan.

### Test 2: same 18-chunk `evidence_cards_v5` screen

Every model faced the same gates in order: **18/18** first-response structured artifacts, exact source spans for **100%** of accepted cards, **17/18** useful-fact checks, and no observed unsupported claim or attribution error. A model failing an earlier gate was not scored as passing the later manual gates; “not reviewed” does not mean “passed.” The 17/18 useful-fact score measures coverage of checklist facts, while 18/18 measures reliable generation of chunk artifacts.

| Model and pinned route | First-response artifacts | Exact source spans among accepted cards | Useful-fact / factuality review | Request latency p50/p95 (18 calls) | Estimated cost (18 calls) | Screen decision |
| --- | --- | --- | --- | --- | --- | --- |
| GPT-5 mini · `azure` | **18/18**, 119 cards | 100% valid | **17/18** checks; 0 issues in later 40-card full-corpus review | 4.56/7.74 s | $0.021861 | Passed; expanded. |
| Qwen3.5 35B-A3B · `deepinfra/fp8` | 17/18, 91 cards | 100% for accepted cards | Full checklist/factuality review stopped after first-response failure. | 3.40/6.30 s | $0.012445 screen | Failed 18/18 reliability gate. |
| Gemini 3.1 Flash Lite · `google-vertex/global/flex` | 17/18, 66 cards | 100% for accepted cards | Full checklist/factuality review stopped after first-response failure. | 17.75/19.39 s | $0.008948 screen | Failed 18/18 reliability gate. |
| DeepSeek V4.1 Flash · `deepinfra/fp8` | 15/18 with reasoning disabled; 0/18 initial configuration | 100% for accepted revised-run cards | Full checklist/factuality review stopped after first-response failure. | 9.20/18.27 s revised run | $0.008484 revised run | Failed 18/18 reliability gate. |
| GLM-5.3-Flash · `deepinfra/fp4` | 0/18 | No accepted cards | No output to review. | 7.15/8.07 s | $0 | Pinned route returned 429; no provider substitution. |

In the original `v5` comparison, only GPT-5 mini expanded to all **83 chunks**: **83/83** first-response artifacts, **496** exact-span cards, no issue in the stratified 40-card review, 4.10/6.83 s request latency p50/p95, and **$0.094640** estimated cost for that full run. The unchanged 18-chunk sample and `v5` prompt were used in every original screen. Invalid responses were not repaired or counted as first-response success. DeepSeek's separate initial configuration cost **$0.012961** and failed 18/18 requests after exhausting completion length; both configurations count toward cumulative spend. Screen costs are observed trial costs, not projected 83-chunk costs.

#### Approved DeepSeek follow-up with separately versioned prompts

The user explicitly reopened Test 1 for DeepSeek and requested every Test 2 check even if an earlier gate failed. The follow-up kept the same six checked revisions, 18-chunk card screen, 83-chunk corpus, pinned `deepinfra/fp8` route, source-span contract, and useful-fact checklist. It did not run other candidate models. `v6` reduced redundant cards and raised the visible card-output allowance from 700 to 1,400 tokens. Its 17/18 screen failure repeated on a diagnostic retry: the model selected an evidence-group number outside the available range. `v7` additionally states the valid zero-based group indices in each input. Earlier reports remain private and separate; the retry did not turn a failed first response into a success.

| DeepSeek card attempt | First response | Exact spans | Useful-fact and factuality review | Request latency p50/p95 | Usage-based cost | Disposition |
| --- | --- | --- | --- | --- | --- | --- |
| `v6` 18-chunk screen | 17/18; 136 cards | 100% of accepted cards | Superseded by `v7` after the invalid-group failure. | 6.36/11.86 s, 18 calls | $0.007572; one separate diagnostic retry cost $0.000647 | Kept as a failed tuning attempt. |
| `v7` 18-chunk screen | **18/18**; 159 cards | 100% | **15/18** useful facts; full factuality review below. | 9.62/18.48 s, 18 calls | $0.007929 | Reliability passes; useful-fact gate fails. |
| `v7` full 83 chunks | **83/83**; 744 cards | **744/744** validated | One factual/attribution error in a deterministic 40-card, six-file review: a card assigns Hoven's son's accelerated-math example to Ferriss. | 8.10/14.31 s, 65 fresh expansion calls | $0.029835 expansion; **$0.037764** screen plus expansion | Fails useful-fact and zero-error gates; GPT-5 mini stays selected. |

The three `v7` checklist misses are the specific Ferriss framing of the 14-day trial question, the opening Bending Spoons summary of talent and acquired businesses, and the combined friction of added processes. The 18 successful screen artifacts were reused for the 83-chunk expansion with no duplicate calls. Latency for the expansion row covers only its 65 new requests; cost for the full `v7` workflow adds those requests to the earlier 18-call screen. The GPT-5 mini `v5` and DeepSeek `v7` results share corpus and quality gates but use different prompt versions, so they compare tested model-prompt configurations rather than isolate a model-only effect. Detailed per-card review and outputs remain in ignored `.local/`.

### Test 3: same three-document summary screen, then six-file expansion

Every candidate faced the same summary gates: complete first-response structured outputs, at least **20/22** content points on the three checked documents, and no invented fact or attribution error. A model passing the screen could expand to all six files, where the content gate was **41/45** with the same zero-error factuality rule. These 22- and 45-point checklists score document summaries; they are distinct from the 18 useful facts used for cards. GPT-5 mini reused its own previously generated `v14` sections; DeepSeek generated its own `v14` sections before `v15`. Factuality review of the completed documents disqualified both despite their passing content coverage.

| Model and pinned route | Structured reliability | Content checklist | Factuality/attribution review | Request latency p50/p95 and scope | Estimated cost and scope | Decision |
| --- | --- | --- | --- | --- | --- | --- |
| GPT-5 mini · `azure` | 3/3 screen and 6/6 full `v15` documents; saved `v14` sections reused | 21/22 screen; **43/45** full | One unsupported video detail conflates two distinct child examples. | 6.39/12.12 s, six fresh `v15` calls only | $0.012584 for six fresh `v15` calls; $0.061061 for earlier `v14` run | Fails no-invented-fact gate. |
| Gemini 3.1 Flash Lite · `google-vertex/global/flex` | 1/3 screen documents | Not scorable | No complete screen to review. | 17.34/19.38 s | $0.000210 | Failed structured-response gate. |
| DeepSeek V4.1 Flash · `deepinfra/fp8` | 3/3 screen documents; **87/87** full `v14` section and `v15` document artifacts with final configuration | 21/22 screen; **44/45** full | One conflation of the same distinct child examples. | 3.70/6.42 s, 87 fresh `v14` and `v15` calls | $0.025899 for the final 87-call full run | Fails no-invented-fact gate. |
| GLM-5.3-Flash · `deepinfra/fp4` | 0/3 screen documents | Not scorable | No output to review. | 7.77/7.90 s | $0 | Pinned route returned 429; no provider substitution. |

#### Approved GPT-5 mini prompt follow-up

The `v16` section and document prompts separated anecdotes and discouraged inferring reach from sharing. Its three-document screen returned **42/42** first-response section/document artifacts and covered **21/22** content points, but its Cate Hall section and document summaries reversed the outcome of an anecdote about ignoring a concern; it did not expand. The separately versioned `v17` prompts added explicit action-versus-inaction and outcome checks. Its screen returned **42/42** artifacts, covered **21/22** points, and had no observed factual error. The same ready screen artifacts were reused in the six-file expansion, which returned **87/87** first-response artifacts and covered **42/45** points. It omitted the Luca Ferrari going-public prospect, the Ferriss note's overanalysis-versus-laziness contrast, and what direct parent conversations taught Hoven. Coverage passed the **41/45** gate, but source review found one attribution/factual link in the Sivers document summary: a separate “trust, but verify” delegation highlight was presented as a decision following Steve Jobs's criticism. The Hoven video-age conflation seen in `v15` was absent from `v17`. The `v16` screen cost **$0.032316** at 2.727/4.812 s p50/p95; `v17` screen plus expansion cost **$0.064424** (screen 2.522/4.132 s; fresh expansion 2.409/4.603 s). Detailed outputs and reviews remain in ignored `.local/stage4-gpt5-v16/` and `.local/stage4-gpt5-v17/`. Neither configuration qualifies.

The original summary latency and cost cells describe different measured call sets: GPT-5 mini's `v15` run reused saved `v14` sections, while DeepSeek's final run generated its own sections and documents. They must not be read as a head-to-head price for identical fresh work. DeepSeek's first default and intermediate configurations hit completion-length limits. Their separate private reports and measured costs remain in the cumulative ledger; the final configuration disabled reasoning and allowed up to eight times the visible summary-token bound. This was a technical configuration change, not a prompt or provider change. Across all recorded Stage 4 reports, cumulative usage-based estimated spend is **$1.338130**, leaving **$2.161870** under the $3.50 ceiling. Test 4 was not run.

### Test 1 model results

| Embedding profile | Required spans at K=5/10/20 | Disposition |
| --- | --- | --- |
| Structural/raw control | **41/44/47** | Selected. |
| GPT-4.1 mini context | 41/44/46 | Below the raw control at K=20. |
| GPT-5 mini context | 42/45/47 | Retrieval improved slightly, but reviewed context had an attribution error. |
| Mistral Small 3.2 context | 41/42/47 | Below the raw control at K=10. |
| Qwen3.5 9B context | 41/43/46 | Below the raw control at K=10 and K=20. |
| Qwen3.5 35B-A3B context | 41/41/48 | Higher K=20 span count did not offset the K=10 loss. |
| DeepSeek V4.1 Flash context | 41/44/47 | 83/83 first-response contexts; tied raw retrieval, but a reviewed context misclassified an author highlight as a personal note. |
| GPT-6 Luna and gpt-oss-20b | No comparable result | Their pinned routes returned 429. |

DeepSeek's context follow-up made 83 generation calls at 1.60/2.53 s p50/p95 and 11 pinned Perplexity embedding calls. Combined usage-based estimated cost was **$0.015176**. At K=10 its per-question coverage was identical to structural/raw. At K=5 and K=20, gains and losses offset in the aggregate; no complete question was lost at K=20. The same preselected 24-position context review found the attribution error above, so the structural/raw decision remains unchanged.

## Corpus and controls

Use the six checked vault revisions in `evals/questions_*.yaml`: 83 deterministic structural chunks, 31 supported questions, and 51 required source spans. Refuse changed source hashes or boundaries. The completed context comparison held structural chunks, `chunk_context_v2` prompt and input recipe, `perplexity/pplx-embed-v1-0.6b` document/query embeddings, exact cosine search, and the question set fixed. Fixed 800-token/raw and structural/raw were controls; context was added only to structural chunks. Cards and summaries do not enter the embedding input. GPT-4.1 mini was rerun on the fresh corpus and route alongside the new context candidates.

## Candidates and route policy

The initial context screen compared `openai/gpt-4.1-mini` (incumbent), `openai/gpt-5-mini`, `openai/gpt-6-luna`, `qwen/qwen3.5-9b`, `qwen/qwen3.5-35b-a3b`, `mistralai/mistral-small-3.2-24b-instruct`, and `openai/gpt-oss-20b`; an approved follow-up tested DeepSeek on the same controls. The four initial non-OpenAI candidates have Apache 2.0 weights. Their vendor cards report instruction-following or long-context strengths; those were screening evidence, not proof on this vault: [Qwen 9B](https://huggingface.co/Qwen/Qwen3.5-9B), [Qwen 35B-A3B](https://huggingface.co/Qwen/Qwen3.5-35B-A3B), [Mistral Small 3.2](https://huggingface.co/mistralai/Mistral-Small-3.2-24B-Instruct-2506), [gpt-oss-20b](https://huggingface.co/openai/gpt-oss-20b).

| Model | Pinned endpoint tag | Maximum observed input/output USD per million tokens |
| --- | --- | --- |
| GPT-4.1 mini, GPT-5 mini, GPT-6 Luna | `azure` | 0.40/1.60, 0.25/2.00, 0.10/0.50 respectively |
| Qwen3.5 9B | `siliconflow/fp8` | 0.10/0.15 |
| Qwen3.5 35B-A3B | `deepinfra/fp8` | 0.14/1.00 |
| Mistral Small 3.2 24B | `deepinfra/fp8` | 0.075/0.20 |
| gpt-oss-20b | `akashml/fp4` | 0.02/0.10 |
| Perplexity embed 0.6B | `perplexity/int8` | 0.004/0 |

### Agreed trial matrix and current disposition

| Test and prompt | Reference | Agreed screen | Disposition |
| --- | --- | --- | --- |
| 1: `chunk_context_v2` | Structural/raw and six comparable context profiles, including the later DeepSeek follow-up | Complete. | Structural/raw selected for this corpus. |
| 2: `evidence_cards_v5`, then DeepSeek `v6`/`v7` | GPT-5 mini on 83 chunks | Qwen3.5 35B-A3B, Gemini 3.1 Flash Lite, DeepSeek V4.1 Flash, GLM-5.3-Flash on the same 18 preselected chunks; later DeepSeek-only prompt revisions and 83-chunk expansion. Complete. | Only GPT-5 mini passed every quality gate; DeepSeek's full follow-up failed useful-fact and factuality gates. |
| 3: `section_summary_v14` + `document_summary_v15`, then GPT-5 mini `v16`/`v17` | GPT-5 mini `v14` on six files and `v15` on its saved sections; `v16`/`v17` each generated fresh sections | Gemini 3.1 Flash Lite, DeepSeek V4.1 Flash, and GLM-5.3-Flash on the same three checked documents, using each model's own `v14` sections. GPT-5 mini `v16`/`v17` follow-up complete. | GPT-5 mini and DeepSeek `v15` failed source-grounding review. GPT-5 mini `v16` failed its screen; `v17` passed coverage on six files but failed factuality. No summary model qualifies. |
| 4: structural/raw embeddings and selected `evidence_cards_v5` | No live complete run | The user approved omitting unqualified summaries and unused chunk context from this profile. Test 4 still needs separate approval. | Bounded, resumable six-file ingestion and local private load/search proof remains open. |

Qwen's earlier card screen used `v3`, so it is historical evidence, not part of the head-to-head `v5` result above. Its earlier summary screen had attribution errors; the agreed summary slate excluded it. Grok was excluded by agreement. Google, DeepSeek, and GLM were candidates because their observed eligible routes were cheaper than the [GPT-5 mini reference](https://openrouter.ai/openai/gpt-5-mini) in both input and output tokens, with structured-output support; vendor claims alone do not qualify a model.

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

The user approved a narrower acceptance profile: capture and parse the six checked revisions, extract GPT-5 mini `evidence_cards_v5` cards, and embed the structural/raw chunks through the pinned Perplexity route. Do not generate or publish unqualified document/section summaries or chunk context in this profile. Preflight the selected routes, token estimates, and cumulative trial spend under the $3.50 ceiling before any live call. After separate user approval for Test 4, interrupt one bounded run and resume it, showing ready artifacts are reused without duplicate model calls. Validate manifests, load all six runs into local private Supabase, publish the cards-plus-structural/raw profile, search checked queries, and resolve returned spans to captured source bytes. Record counts, failures, model/prompt/endpoint versions, measured latency and spend, and limitations in an IDs-only private report. Recompute the complete-run estimate; the earlier 266-request estimate included unselected context and summary generation.

Do not mark Stage 4 complete until the selected cards/raw profile and resumable load/search proof pass. The summary experiment stays unqualified and outside this profile; any later use needs its own gate. Reconcile `FOUNDATION.md#current-progress` and `INGESTION.md` with accepted facts after implementation.
