---
title: Paper Refinement - Deferred Parts
type: feat
date: 2026-10-21
status: deferred
---

# Paper Refinement - Deferred Parts

Parts of the Paper refinement design that were shown on the boards but left out of the first build. Source: the Product Contract of `docs/plans/2026-10-21-002-feat-paper-refinement-plan.md` (KTD6 and Scope Boundaries) and the design handoff folder `2026-10-paper-refinement` (boards and screens, kept outside the repo because they show personal names). Each one needs its own go-ahead before it is built.

## Needs a server change

- **P1. Starter questions. Built 2026-10-22.** `GET /api/starters` returns up to three notes loaded in the last 7 days; each becomes the question "What do my notes say about <title>?" under the empty question box. One tap asks it. Screens: D1, M1.
- **P9. "In use" tags. Built 2026-10-22.** `GET /api/tags` now also returns `approved` (tag names). Settings shows them read-only under Suggested; Add shows up to eight under the tag field and one tap adds one as a chip. Screens: S1, S3.

## Left out for now

- **P7. Account tab** in Settings. The boards do not say what it holds.
- **P8. Richer Tags.** (The note-title line in each row on desktop is now built; it uses titles the tags endpoint already returns.) The rest: "closest tag in use" in the drawer, "1 of N" with auto-advance, undo toast. Needs new server data. Screens: S1, S2, SM3.
- **P10. Shortcut hints** (⌘↵) in feedback and Add. Only if the shortcut is built. Screens: D5, A2.
