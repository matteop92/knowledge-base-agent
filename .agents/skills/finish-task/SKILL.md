---
name: finish-task
description: After implementation, reconcile FOUNDATION.md's current progress and update at most one relevant system detail doc; keep both as current state, never a history log.
---

# Finish task

1. Inspect the completed change and its verification results.
2. Always reconcile `FOUNDATION.md#current-progress` with the result. Replace the affected stage's present state and next proof in place. If the work changed no stage or next proof, leave the table as-is and say it was checked; never invent progress or append dated notes.
3. Update at most one relevant system detail doc if its factual description changed: `SYSTEM.md` for the overall flow, `INGESTION.md` for input and enrichment, `RETRIEVAL.md` for search and answering, or `DATA.md` for records. Remove stale claims and keep the doc concise. Do not edit other detail docs merely to mention the task.
4. Refresh only the two-sentence status at the top of `AGENTS.md` if it became false. Keep it consistent with Foundation. Leave the original `knowledge-agent-architecture.md` as the design brief.
5. Check the edited claims against code and tests. Report which progress row and detail doc changed, or why each remained accurate. Stop.
