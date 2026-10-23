---
name: start-task
description: Orient a task by reading README.md and docs/architecture.md (its Status section), then at most one relevant detail doc; then stop.
---

# Start task

1. Read `README.md`, then `docs/architecture.md`, focusing on its Status section.
2. If the task needs more detail, open one relevant doc: `docs/decisions.md` (why a choice was made), `docs/evaluation.md` (measurements), `docs/deploy.md` (hosting and sync), `prompts/README.md` (prompt versions) or `evals/README.md` (labels and trial scripts). Open no other project docs or source files during this command.
3. Summarize in a few plain sentences the current state, what remains for the task, and the next place to inspect when work begins.
4. Stop. Do not edit files or begin implementation. The Status section of `docs/architecture.md` is the progress source of truth; `AGENTS.md` is only a short entry point.
