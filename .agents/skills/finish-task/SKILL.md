---
name: finish-task
description: After implementation in this repository, update only the relevant living architecture doc to match verified current behavior; never append a history log.
---

# Finish task

1. Inspect the completed change and its verification results. Choose exactly one relevant living doc: `SYSTEM.md`, `INGESTION.md`, `RETRIEVAL.md`, or `DATA.md`.
2. Edit that doc in place so its descriptions and current/planned boundary match how the system works now. Remove stale claims; do not append dated updates, changelog entries, task notes, or speculative details.
3. Keep the doc concise. If no factual description changed, leave it untouched and say so. Do not edit the other living docs.
4. Update the two-sentence status at the top of `AGENTS.md` only if the completed work makes it false; keep it exactly two sentences. Make no other AGENTS.md changes.
5. Check the edited text against the code and report which file changed, or why no doc edit was needed. Stop.
