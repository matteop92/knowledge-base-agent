# Prompts

Each prompt is a Markdown file named `<family>_v<N>.md`. The code names the exact version it loads
(a `PROMPT_VERSION` constant), and run receipts record the version and a hash of the text, so a result
can always be traced to the prompt that produced it. A change to a prompt is a new file, never an edit.

This folder holds only the versions in use. Older versions were removed; the changelog below records what each one changed.

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

The notes below come from the commit that introduced each version.

**Answer agent**
- v1 to v8: the first local retrieval and cited-answer harness, refined over several rounds.
- v9: deeper, source-grounded investigation.
- v11 to v13: the host runs the first search and the agent reads whole chunks.
- v13: adds a relevance test in the question's own words in place of "cover every point in the passages read".
  On one run per question it cut points outside the question's scope from 9 to 3 against v12, with a similar overall checklist score. The accepted baseline.
- v14: the follow-up variant of v13, with conversation memory.

**Grader**
- v1: grades an answer against frozen question-specific checks.
- v2 and v3: the three-dimension checklist (coverage, grounding, scope).
- v4: gives the grader each source's title and people. Used for the frozen 85-check checklist.

**Properties classifier**
- v1: fills missing type, tags, people, date and description from an approved tag list.
- v2: each tag gets a one-line definition, and a note gets at most three tags.
- v3: a note's own list tags are hints beside the approved ones, seven approved tags added, and the tag "technology" blocked.

**Summaries (trial)**
- document and section v1 to v17, group v2 and v3: rounds of the hierarchical summary experiment, run as bounded trials. Summaries are not part of the live profile, so only v1 remains here.

**Chunk context**
- v1: one-sentence context for each chunk. v2: capped at 60 words, no invented facts, speaker attribution kept (used by the chunking trial).
