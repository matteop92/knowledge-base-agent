---
name: finish-task
description: After implementation, reconcile the Status section of docs/architecture.md and update at most one relevant detail doc; keep both as current state, never a history log.
---

# Finish task

1. Inspect the completed change and its verification results.
2. Reconcile the Status section of `docs/architecture.md` with the result. Replace the affected row in place. If the work changed no row, leave the table as-is and say it was checked; never invent progress or append dated notes.
3. Update at most one relevant detail doc if its factual description changed: `docs/architecture.md` for the flow, `docs/decisions.md` for a choice and its reason, `docs/evaluation.md` for new measurements, `docs/deploy.md` for hosting, `prompts/README.md` for a new prompt version, or `evals/README.md` for labels and trial scripts. Remove stale claims and keep the doc concise. Do not edit other docs merely to mention the task.
4. Refresh only the short summary at the top of `AGENTS.md` if it became false.
5. Check the edited claims against code and tests. Report which doc changed, or why each remained accurate. Stop.
