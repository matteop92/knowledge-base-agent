---
name: start-task
description: Orient a task by reading SYSTEM.md first, the current progress section of FOUNDATION.md, and at most one relevant detail doc; then stop.
---

# Start task

1. Read `SYSTEM.md` first.
2. Follow its link to `FOUNDATION.md#current-progress`. Read only that section, not the whole Foundation document.
3. If the task needs more detail, follow one relevant link from `SYSTEM.md` to `INGESTION.md`, `RETRIEVAL.md`, or `DATA.md`. Open no other project docs, plans, or source files during this command.
4. Summarize in a few plain sentences the current stage, what remains for the task, and the next place to inspect when work begins.
5. Stop. Do not edit files or begin implementation. `FOUNDATION.md#current-progress` is the progress source of truth; `AGENTS.md` is only a short entry point.
