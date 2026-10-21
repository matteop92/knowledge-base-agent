---
title: "feat: Settings page with suggested tags, a tags table and a case-study tag"
type: feat
status: active
date: 2026-10-21
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
origin: docs/plans/2026-10-16-002-feat-go-live-web-app-plan.md
---

# feat: Settings page with suggested tags, a tags table and a case-study tag

## Summary

Phase 3 starts with a Settings page (reached from the account menu) whose first tab, Tags, lets Matt approve or block the tags the classifier proposes. The live tag list moves from `config/tags.yaml` into a Supabase table so a tap can change it without a deploy. Approving only affects notes added or changed from then on. A new tag, `case-study`, joins the list.

## Goal Capsule

- **Outcome:** One tap approves or blocks a suggested tag, and the classifier uses the new list on its next run.
- **Done when:** Matt can open Settings, review the four pending suggestions with the notes behind each, approve or block them, and see a new note pick up an approved tag. `case-study` is on the list.
- **Look:** Build from the canvas board source, not from this text: https://claude.ai/artifact/3KJHM6DK1ua3gBfNuJ1hr6 (boards `Main`, `Notes-1-Sheet`, `Desktop-A`).
- **Constraints:** Existing notes are never edited and are not re-tagged. New captures keep getting properties written into the file. No bold button text, no thick or coloured borders, 32 px controls with a mouse and 44 px on touch, Newsreader and Geist, sizes 12/14/16/18/22/26, Phosphor Light icons, no red for errors. The repo is treated as public: no vault content in fixtures. Do not merge; Matt tests locally first.

## Product Contract

Settled with Matt on 2026-10-21 (session-settled: user-directed).

- **R1.** The account menu gets a first item, Settings. Settings has a horizontal tab bar under its title; Tags is the only tab for now, and more can be added later.
- **R2.** The Tags tab lists suggested tags as compact rows: name, "n notes", a cross (block) and a tick (approve). A tag's suggestions are the proposals the classifier stored, grouped by tag name.
- **R3.** Tapping a row opens the notes behind that tag: a bottom sheet on phone, a right-hand panel on desktop (the same mechanism as Source and Gaps). One card per note shows type, author, title and description, and opens the note. Approve and Block sit in the sheet and panel too.
- **R4.** Approved and blocked tags live in a new Supabase table, seeded once from `config/tags.yaml`. After that the table is the source of truth. The yaml stays as the seed and keeps the source types.
- **R5.** Approving or blocking affects only notes classified from then on. Past notes are not re-checked and keep their stored tags.
- **R6.** `case-study` is added: articles or transcripts explaining a specific case study of adoption, AI implementation or some larger project within a company.
- **R7.** Blocked tags are never applied and never suggested again.

## Scope Boundaries

- Not in this plan: re-tagging past notes, editing a tag's rule text, renaming or deleting approved tags, other Settings tabs.
- Deferred to follow-up work: "Approve all" if Matt wants it beyond the canvas sketch.

## Key Technical Decisions

- **KTD1. Table is the source of truth, loaded with a connection.** `TagList.load` gains an optional connection. With one it reads the table; with none, or if the table is empty, it falls back to the yaml. The scheduler reads the list on every tick today with no connection, so it opens one for that read. *(see R4)*
- **KTD2. Seed on first read, not in the migration.** A migration cannot read the yaml. The first load against an empty table inserts the yaml's tags and blocked list, with `case-study` added to the yaml first. Seeding runs once under an advisory lock so two processes cannot double-insert. *(see R4, R6)*
- **KTD3. Suggestions come from stored proposals.** `document_properties.proposed_tag` already holds one proposal per note revision. The suggestion list is those rows for the document's current revision, grouped by tag, minus tags already approved or blocked. No new classification call. *(see R2, R5)*
- **KTD4. Approve does nothing else.** Approving writes one table row. The classifier picks it up on its next run, so only new or changed notes can get the tag. Properties already stored on notes are left alone. *(see R5)*
- **KTD5. The tag rule is optional.** An approved suggestion starts with no "when" line; the classifier already handles tags without one. Editing rules is deferred.
- **KTD6. Reuse the sheet.** The notes view uses the existing `openSheet` mechanism (swappable panel on desktop, bottom sheet on phone) instead of a new overlay.

## Implementation Units

### U1. Tags table and list loading

- **Goal:** Store approved and blocked tags in Supabase and make the classifier read them.
- **Requirements:** R4, R5, R6, R7.
- **Dependencies:** none.
- **Files:** `supabase/migrations/20261021000000_tags.sql`, `app/ingestion/properties.py`, `config/tags.yaml`, `app/api/scheduler.py`, `app/api/capture.py`, `app/ingestion/vault_sync.py`, `tests/ingestion/test_properties.py`, `tests/api/test_scheduler.py`.
- **Approach:** `knowledge.tags` with name (primary key), status (`approved` or `blocked`), optional rule text and a decided-at time; runtime role gets select, insert and update, with row-level security like `document_properties`. Add `case-study` with its rule to the yaml. `TagList` reads the table when given a connection, seeds it on first use (KTD2), and keeps the yaml for source types and as fallback. The scheduler, capture classifier and vault-sync command load through that path.
- **Patterns to follow:** `supabase/migrations/20261017000100_document_properties.sql` for grants and policy; `TagList.load` in `app/ingestion/properties.py`.
- **Test scenarios:**
  - Empty table: first load seeds the yaml's tags and blocked list, including `case-study`, and returns them.
  - Seeded table: a second load does not duplicate; an approved row added to the table appears in the list.
  - A blocked row removes the tag from the list and from `blocked` consumers (classifier never applies or proposes it).
  - No connection or unreachable table: falls back to the yaml list without raising.
  - Scheduler tick passes the table-backed list to the pass function.
- **Verification:** ingestion and api suites pass; migration applies on a disposable database.

### U2. Tags API

- **Goal:** Endpoints to list suggestions with their notes and to approve or block a tag.
- **Requirements:** R2, R3, R5, R7.
- **Dependencies:** U1.
- **Files:** `app/api/tags.py`, `app/api/main.py`, `tests/api/test_tags.py`.
- **Approach:** `GET /api/tags` returns suggested tags (name, note count, notes with document id, title, source type, people, description, path for the Obsidian link), plus the approved and blocked names. `POST /api/tags/{name}/approve` and `/block` write one row (KTD4), reject names that fail the tag pattern, and are safe to repeat. Both need a signed-in session like the other routes. Note details come from the same stored properties and run metadata the search labels already use.
- **Patterns to follow:** `app/api/conversations.py` for a store class; `session` dependency and routes in `app/api/main.py`.
- **Test scenarios:**
  - Suggestions group proposals by tag and count notes; only proposals from each note's current revision count.
  - An approved or blocked tag no longer appears as a suggestion.
  - Approve then block the same tag leaves it blocked; approving twice is harmless.
  - A name that is not a valid tag, or an unknown suggestion, returns a clear error.
  - Unauthenticated calls are refused.
  - Approving does not touch `document_properties` (past notes unchanged).
- **Verification:** api tests pass; manual call against the local demo server shows the four pending suggestions.

### U3. Settings view and account menu item

- **Goal:** Settings opens from the account menu and shows the Tags tab with compact suggestion rows.
- **Requirements:** R1, R2, R7.
- **Dependencies:** U2.
- **Files:** `app/web/index.html`, `app/web/app.js`, `app/web/styles.css`, `.local/demo_server.py` (fake data), `tests/api/test_static.py`.
- **Approach:** Add "Settings" as the first account-menu item with its icon, a settings view with the title, a tab bar (Tags active) and the row list as drawn on the `Main`, `Notes-1-Sheet` and `Desktop-A` boards. Phone has a back arrow and 44 px controls; desktop has no back arrow and 32 px controls. Tick and cross call the API and the row moves out of the list with a short confirmation; an error shows in the muted style, not red. Service worker cache version bumps so installed apps refresh.
- **Patterns to follow:** existing tab switching (`setView`), account menu, and style tokens in `app/web/styles.css`.
- **Test scenarios:**
  - The static test sees the Settings menu item and view in the served page.
  - Demo server serves suggestions so the page renders without the database.
  - Manual (Matt): approve and block from a row on phone and desktop; the row disappears and the count in the header drops.
- **Verification:** static and api suites pass; Matt checks it locally against the canvas.

### U4. Notes sheet and panel

- **Goal:** Tapping a tag shows the notes behind it, so Matt can judge the tag.
- **Requirements:** R3.
- **Dependencies:** U3.
- **Files:** `app/web/app.js`, `app/web/index.html`, `app/web/styles.css`, `tests/api/test_static.py`.
- **Approach:** Register a `tag-notes` sheet with the existing `openSheet`, swappable with Source and Gaps so the desktop panel stays open as Matt moves between tags. Cards show type and author, title and description, and link to the note with the existing Obsidian URL builder. Approve and Block in the sheet do the same as the row buttons and close or advance the panel.
- **Patterns to follow:** `fillSource`, `openSheet`, `SWAPPABLE` in `app/web/app.js`.
- **Test scenarios:**
  - Phone: tapping a row opens a bottom sheet with one card per note; Back closes it.
  - Desktop: the right panel opens beside the list and swaps when another row is tapped.
  - Approving from the panel removes the row and closes the panel.
  - A note with no description or author still renders a clean card.
- **Verification:** Matt checks it locally on phone width and desktop against the canvas boards.

### U6. Make `.md` upload visible in Add (done)

- **Goal:** Matt can see that Add takes `.md` files.
- **Requirements:** tweak requested 2026-10-21.
- **Dependencies:** none.
- **Files:** `app/web/index.html`, `app/web/sw.js`, `tests/api/test_static.py`.
- **Approach:** The attach button uses a paperclip icon and the label "Attach a screenshot or .md file"; the placeholder reads "Paste text or a link, or attach a screenshot or .md file". Upload itself already worked. The service worker cache version moves to v15.
- **Test scenarios:** the static test checks the new cache version. Manual: Matt picks a `.md` file and sees its text, frontmatter and formatting kept.
- **Verification:** static suite passes.

### U5. Docs

- **Goal:** Keep the maps current.
- **Requirements:** all.
- **Dependencies:** U1 to U4.
- **Files:** `FOUNDATION.md`, `SYSTEM.md`, `AGENTS.md`, `docs/plans/2026-10-16-002-feat-go-live-web-app-plan.md`.
- **Approach:** Update the progress row, the tag description (table is the source of truth, yaml is the seed), the Phase 3 note in the go-live plan, and the file map.
- **Test expectation:** none -- documentation only.

## Open Questions

- ~~`.md` upload in Add:~~ resolved 2026-10-21, built as U6. Was: `app/web/capture.js` already accepts `.md`, `.markdown` and `.txt` files (picked, dropped or shared) and keeps their frontmatter. Matt asked for this as a tweak, so something is not working for him. Waiting for what he saw before planning a unit. Not blocking U1 to U5.
- **"Approve all" button:** drawn on the canvas but not discussed. Include in U3 unless Matt says otherwise.

## Risks

- **Seeding race or a stale list:** two processes starting together could seed twice; the advisory lock and a primary key on `name` prevent duplicates (U1).
- **Classifier lag:** a newly approved tag applies from the next index pass (about five minutes) or the next capture. The page should say "applies to notes you add from now on" as the canvas does.
- **One proposal per note:** a note proposes at most one tag per revision, so counts under-state how many notes would fit. Acceptable for review.

## Verification Contract

| Gate | Command | Applies to |
| --- | --- | --- |
| Ingestion tests | `python -m unittest discover -s tests/ingestion -p 'test_*.py'` | U1 |
| API tests | `python -m unittest discover -s tests/api -p 'test_*.py'` | U1 to U4 |
| Storage and migration | disposable `KNOWLEDGE_TEST_DATABASE_URL`; migration applies cleanly | U1 |
| Local look | `python .local\demo_server.py` or `.local\run-live.ps1`, Ctrl+Shift+R | U3, U4 |
| Code review | full `/ce-code-review` before any PR | all |

## Definition of Done

- The tags table exists and drives the classifier; `case-study` is on the list.
- Settings shows pending suggestions; approve and block work from rows and from the sheet or panel.
- Suites above pass, the page matches the canvas, and Matt has tested it locally.
- `FOUNDATION.md#current-progress` and the system docs are updated through `$finish-task`.
