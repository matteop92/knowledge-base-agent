# Prompts

Each prompt is a Markdown file named `<family>_v<N>.md`. The code names the exact version it loads
(a `PROMPT_VERSION` constant), and run receipts record the version and a hash of the text, so a result
can always be traced to the prompt that produced it. A change to a prompt is a new file, never an edit.

`prompts/` holds the versions in use. `prompts/archive/` holds retired versions, kept so old
trial results stay reproducible. The summary trial in `app/ingestion/summary.py` also reads archived
versions by name.

## In use

| Prompt | Used by | What it does |
| --- | --- | --- |
| `answer_agent_v13.md` | `app/agent/harness.py` | The answer loop for a new question. Tells the agent how to search, read whole chunks, track what the question needs, attribute statements to the right speaker, and stop without a closing offer. |
| `answer_agent_v14.md` | `app/agent/harness.py` | The same loop for a follow-up question, with the earlier turns and the passages already read passed in as memory. |
| `followup_rewrite_v1.md` | `app/agent/memory.py` | Rewrites a follow-up into a stand-alone search query for the first search. |
| `properties_v3.md` | `app/ingestion/properties.py` | Classifies a note: source type and one to three tags from the approved list. |
| `screenshot_extract_v1.md` | `app/capture/screenshot.py` | Reads text and structure out of a screenshot. |
| `youtube_transcript_v1.md` | `app/capture/youtube.py` | Turns a video transcript into a clean note. |
| `youtube_speakers_v1.md` | `app/capture/captions.py` | Labels speakers when a transcript comes from captions. |
| `chunk_context_v1.md`, `chunk_context_v2.md` | `app/ingestion/enrichment.py`, `evals/compare_chunking.py` | One-sentence context written for a chunk. Only used in the chunking trial, which found it did not beat raw chunks. |
| `document_summary_v1.md`, `section_summary_v1.md` | `app/ingestion/summary.py` | The default summary prompts of the generation trial. The profile in use has no summaries. |
| `answer_grader_v1.md`, `answer_grader_v4.md` | `evals/answer_grader.py` | The graders. v4 grades an answer against the frozen 85-check checklist. |

## Changelog

**Answer agent**
- v1 to v5: first harness, tools and action schema.
- v6 to v9: tighter search habits (short queries, no long lists of facets), host-run first search.
- v10 to v12: speaker-aware attribution, no closing offers, safe stop reasons when the budget runs low.
- v13: replaces "cover every point in the passages read" with a relevance test in the question's own words.
  On one run per question it cut points outside the question's scope from 9 to 3 against v12, with a similar overall checklist score. The accepted baseline.
- v14: the follow-up variant of v13, with conversation memory.

**Grader**
- v1: grades an answer against a short list of checks.
- v2 and v3: trial versions with a numeric scale.
- v4: gives the grader each source's title and people, and grades the 85-check checklist on three dimensions (coverage, grounding, scope).

**Properties classifier**
- v1: source type only.
- v2: adds tags from the approved list.
- v3: tags may come from the note's own tags only if they are on the approved list; empty list allowed; suggests new tags.

**Summaries (trial)**
- document and section v1 to v17, group v2 and v3: seventeen rounds of the hierarchical summary experiment. Summaries were later dropped from the live profile, so only v1 stays in this folder.

**Chunk context**
- v1: first one-sentence context. v2: capped at 60 words, no invented facts, speaker attribution kept.
