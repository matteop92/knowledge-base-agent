---
title: Paper Refinement - Plan
type: feat
date: 2026-10-21
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
origin: design handoff folder "2026-10-paper-refinement" (boards + screens, kept outside the repo; the screens show personal names)
---

# Paper Refinement - Plan

## Goal Capsule

- **Objective:** Refine the shipped Paper look across the sidebar, Ask, Add and Settings · Tags, on desktop and phone: type, spacing, surfaces and drawers. Colours, the two typefaces and the icon set stay. No API, schema or agent change.
- **Visual authority:** The screens (PNG) and boards in the design handoff folder. Every UI unit below names the screens it must match. Where this plan and a screen differ, the screen wins unless a decision below overrides it.
- **Scope confirmed with the product owner on 2026-10-21** (session-settled, see KTDs): approved design + five proposed parts (P2, P3, P4, P5, P6). The rest is parked in `docs/plans/2026-10-21-003-deferred-paper-refinement-parts.md`.
- **Inherited constraints** (`docs/plans/2026-10-19-001-feat-web-app-redesign-plan.md`): CSP forbids inline styles and scripts; no `innerHTML`; light only; its "Existing States That Must Survive" table still holds. Where this plan changes a value set there (type scale, column widths), this plan wins.

---

## Product Contract

### Summary

Every flow stays. Hierarchy comes from type: larger Newsreader for titles, questions, answers and quoted passages; Geist for controls. Spacing follows a 4-pt rhythm. Cards, the composer and the right-hand drawer become warm "paper" surfaces with a soft layered shadow; on desktop the drawer floats inset from the window edge. The sidebar is reorganised: Ask and Add to notes at the top, date-grouped History, account and Settings gear at the foot.

### Requirements

Kept from the design handoff, with the product owner's amendments applied. IDs match the handoff.

**Look (all screens)**

- R1. Text uses the type roles in the Design Reference. This replaces the six-size scale (12/14/16/18/22/26).
- R2. General spacing uses only 4, 8, 12, 16, 20, 24, 28, 32, 40, 48, 64, 72 px. Exact insets and paddings the screens give (10 drawer inset, 18/22 composer and sheet padding, 34 sheet bottom) are exempt. Each UI unit converts the spacing of the elements it touches to the spacing variables from U1; it is checked by eye against the screens, not by a test.
- R3. Raised surfaces (composer, Add box, tag list, feedback panel, drawer) use paper `#fffdf8` and the shadow recipes below, not plain white.
- R4. Small section labels are 11 px Geist 500, uppercase, 0.08em tracking, muted. ("Sources" stays sentence case, see R17.)
- R5. Times, counts and numbers use tabular figures.
- R6. **Amended:** controls keep today's sizes: 32 px with a mouse, 44 px on touch, 28 px small row actions, the 36 px send button and today's Save height. No control grows to match the boards.

**Sidebar** (D1–D5, M6)

- R7. Desktop sidebar is 264 px wide (was 248).
- R8. Top row: app icon as a 26 px rounded tile, "Knowledge Agent" in Newsreader 17/520, collapse button right (close button on the phone drawer).
- R9. There is no "New question" button (removed 2026-10-22). Clicking Ask always opens a fresh question, from any screen or open conversation.
- R10. Nav items are "Ask" and "Add to notes" (was "Add"), 34 px tall, radius 9. The active item gets the ground colour plus a hairline and 1 px shadow (paper tab).
- R11. History is a section label with a fold chevron, not a nav item. Conversations group under "Today", "This week", "Earlier". The current one uses the paper-tab treatment.
- R12. **Amended:** the foot, above a hairline, shows the avatar circle, the label "Account" (no name or email, see KTD5), and a Settings gear. The avatar still opens the sign-out menu; the gear opens Settings and shows active while Settings is open.
- R13. Phone drawer is 316 px wide with a rounded right edge (radius 24).

**Ask** (D1–D5, M1–M6)

- R14. New question: a date line ("Tuesday, 21 October", label style), the heading "Ask your notes anything.", the existing sub-line, then the composer, all centred. No starter questions (P1 parked).
- R15. Each turn shows a meta line "Today, 9:41 · 3 sources" (12 px muted) above the question. Question 27 px Newsreader, balanced wrap; answer 18.5/1.66 with 16 px between paragraphs.
- R16. Footnote markers stay superscript accent numbers. **Amended:** the marker for the source open in the drawer becomes a filled accent tag with white text, radius 5, weight no heavier than 500.
- R17. The sources row stays as today (caption, 24 px numbered circles, gap chip with icon and count, thumbs-down at the far end). **Amended:** the active number fills with the accent while its drawer is open, and the gap chip fills with the accent (white icon and count) while "What's missing" is open. No ring.
- R18. A still-answering follow-up shows "Reading your notes · 6s" with a three-dot accent indicator and three skeleton lines (11 px, radius 6, chip colour).
- R19. The composer is pinned to the bottom over a fade from transparent to ground; max 680 px (640 with a drawer open), 56 px tall, radius 22, paper surface; the send button keeps today's 36 px and is dimmed when empty. Desktop hint below: "Enter to ask · Shift + Enter for a new line" (11.5 px muted). Placeholders "Ask a question" / "Ask a follow-up".
- R20. Inline feedback (desktop) is a paper card: "What was wrong?" (Newsreader 19), the existing explainer, the note field (hairline inset while focused, as today), then Cancel and Send feedback on the right.
- R21. Phone feedback sheet adds a full-width "Not now" text button under Send feedback.

**Drawers and sheets** (D3, D4, S2, M3–M5, SM2)

- R22. Desktop drawer: 432 px, floating 10 px from the top, right and bottom edges, radius 18, paper surface, drawer shadow. Pushes the page at 1200 px and wider, overlays below that.
- R23. Drawer head: label-style kicker left (accent for gaps), 32 px close button right, both always visible on desktop.
- R24. Source drawer: title in Newsreader 26; folder and "See in Obsidian" (accent, underlined) separated by a dot; hairline; "Cited passage" label; each passage on a ground card (radius 14) with a large accent opening quote at 55% opacity. The number buttons move to a foot bar above a hairline.
- R25. What's missing: title, helper line "Your notes didn't cover these. Add a note and the next answer can use it.", gaps between hairlines numbered 01/02 (tabular, muted, desktop), gap text Newsreader 18.5, link "Add a note to fill this gap" with a + icon.
- R26. Phone sheets: radius 28 top corners, 36×5 handle, 24 px side padding, scrim unchanged, 44 px hit targets including the source numbers.

**Add** (A1, A2, A4, AM1, AM2)

- R27. Column 560 px, 72 px top padding on desktop. Title "Add to your notes", sub-line "Text, a link, a screenshot or a Markdown file. It's filed in the right folder for you." (phone drops the second sentence).
- R28. The box is a paper card, radius 20, placeholder "Paste or type here…". The detection row sits inside the box (36 px icon tile, title, subtitle). YouTube subtitle "Transcript saved to Transcripts"; other types "Filed in the right folder for you".
- R29. "Title and tags · optional" fold under a hairline; fields 40 px (44 phone), radius 11, ground colour.
- R30. Actions row: attach button left with "or drop a file anywhere" beside it (desktop, empty box); Save right, dimmed while nothing can be saved.
- R31. **Amended:** while a file is dragged, an inset rounded target covers the view with a soft accent tint fill and no ring; it holds an icon tile, "Drop to add" and "Screenshots, .md and .txt files"; the page behind fades to 35%.
- R32. Recent becomes a ledger: label, rows between hairlines. Each row: state icon, 14.5 px title, meta line (type pill, See in Obsidian, status text, Retry), right-aligned time column. States and copy unchanged. (Time column is P6, see R-P6.)

**Settings · Tags** (S1–S3, SM1, SM2)

- R33. Title "Settings" (page-title role) above underline tabs (1.5 px ink underline on the active tab, a tab underline and not a field outline). Column 600 px.
- R34. Intro sentence above the list: "When several notes point at a topic your tag list doesn't have, the agent suggests a new tag. Approve it to use it on notes you add from now on; block it to stop it coming back." Shortened on phone as in SM1.
- R35. "Suggested · N" label and "Approve all" chip on one line, then the list on a paper card, radius 16.
- R36. Row: tag name (15/500) and count, then two small round icon buttons: block (✕, muted) and approve (✓, accent fill), approve always on the right. 28 px on desktop, 44 px touch area on phone (**amended**, today's sizes). No text labels in rows.
- R37. Opening a tag lights its row and swaps its buttons for a chevron. Drawer: kicker, tag name Newsreader 32, "N notes proposed it", note cards. Foot bar with compact Block and Approve pills at 32 px (phone sheet keeps full-width buttons at today's sizes).
- R38. Empty state: hairline-bordered panel, accent check in a mint circle, "All caught up", "New suggestions appear here when your notes point at a topic the tag list doesn't cover." The caption about older notes keeps its place.

**Promoted parts**

- R-P2. Source stepping: back and forward arrows in the source drawer foot, ← → keys to step, swipe to step on phone (D3, M3).
- R-P3. Fill a gap: "Add a note to fill this gap" opens Add in a mode with a "Back to the answer" link, the gap shown in a mint panel above the box, and "Save note" in place of Save (A3).
- R-P4. Tags in Add are removable chips typed into one field (A2, AM2). The "approved tags" suggestion row is **not** built (the browser has no list of approved tags; P9 is parked).
- R-P5. On phone, a "Saving…" toast after Save (AM3).
- R-P6. Recent shows a right-aligned time column ("now", "12 min", "1 h", then days) and "N this week" beside the label (A1, A2). Uses each capture's existing `created_at`.

### Key Technical Decisions

- KTD1. (session-settled: user-directed — chosen over keeping the six-size scale: "follow design") The type table in the Design Reference is the new scale. `tests/api/test_static.py` is rewritten to allow exactly those sizes; `AGENTS.md` "Web app type scale" is rewritten to match.
- KTD2. (session-settled: user-directed — chosen over enlarging controls to the boards' 36–50 px: "keep current button sizes") R6 as amended.
- KTD3. (session-settled: user-directed — chosen over the boards' accent rings: "no outlines") Open, active and drag states are marked with fills. Keyboard focus on buttons and links stays a thin dark ring; text boxes keep today's inset hairline while typing. The Add box loses its focus ring.
- KTD4. (session-settled: user-directed — "no bold numbers") Footnote markers and number chips are weight 500 at most. No button text is bold.
- KTD5. (session-settled: user-directed — chosen over showing name and email: "name only") The browser holds neither a name nor an email after sign-in and no API change is allowed, so R12 shows "Account" until a name source exists. Resolved 2026-10-21 (OQ1).
- KTD6. (session-settled: user-directed — "explore the proposed parts") P2, P3, P4, P5, P6 are built; P1 and P9 (need server changes) and P7, P8, P10 are parked in `docs/plans/2026-10-21-003-deferred-paper-refinement-parts.md`.
- KTD7. Placeholder colour is `#6f6b63` (about 5.2:1 on paper and 4.8:1 on ground, where Add and feedback fields sit), not the board's `#8a857c` (about 3.5:1), so the existing contrast test can cover it at AA. A small deviation from the screens; resolved 2026-10-21 (OQ3).
- KTD8. Fill a gap (P3) leaves the box empty (as in A3) and shows the gap in the panel above it. Today the gap text is pre-filled into the box and saved with the note; after this change only what the user types is saved. Resolved 2026-10-21 (OQ2).
- KTD9. No new typefaces or icon styles. Phosphor Light is the only icon source; the one new icon (forward caret for P2) is copied from the Phosphor Light set (MIT) into the sprite in `app/web/index.html`, mirroring the existing `i-back` path, never hand-drawn.
- KTD10. Time and grouping logic live in small pure functions (`formatAge`, `groupByAge`, tag-chip parsing) so tests can run them in Node the way existing tests do.

### Scope Boundaries

- Kept as is: the share target, polling and retry logic, sign-in screens, all copy not named above, the API.
- Parked, not built: P1, P7, P8, P9, P10 (see the deferred doc). Also not built: the ⌘↵ hints and the email line that appear on some screens.
- Not changed: dark mode (none), red for failed states (stay neutral grey), app name.

### Success Criteria

- The product owner compares each built screen with its PNG and finds no unintended difference in type, spacing or surface (apart from the amendments above).
- Every row of the 2026-10-19 plan's "Existing States That Must Survive" table still works.
- Contrast test passes with the new `paper` ground; no font size outside the Design Reference table.
- No parked part ships.

### Open Questions

- OQ1 (resolved 2026-10-21): KTD5, the foot shows "Account".
- OQ2 (resolved 2026-10-21): KTD8, Fill a gap saves only the typed text.
- OQ3 (resolved 2026-10-21): KTD7, darker placeholder than the board.
- OQ4 (design smell, no action): the table has near-duplicate sizes (13.5/14/14.5, 17/17.5, 18/18.5). Built as designed; collapsing them is a later clean-up.

---

## Design Reference

### Colours (existing tokens unchanged; new marked)

| Token | Value | Use |
|---|---|---|
| ground | `#f6f4ef` | page, passage cards, field backgrounds |
| sidebar | `#ede9e0` | sidebar, phone drawer |
| paper (new) | `#fffdf8` | composer, Add box, tag list, feedback card, drawer, sheets |
| ink / ink-soft / muted | `#1d1c1a` / `#4e4b45` / `#625e57` | text |
| placeholder (new) | `#6f6b63` | placeholders (KTD7) |
| dot (new) | `#a8a398` | 3 px separator dots (decorative) |
| chip / pill / handle | `#ece8df` / `#e6e1d6` / `#d9d4c9` | circles and pills / avatar and icon tiles / sheet handle |
| accent / hover | `#2f5d50` / `#264c42` | actions, citations |
| gap-bg | `#dfeae5` | gap chip, gap panel, check circle |
| row-active (new) | `#f1eee6` | the tag row open in the drawer |
| toast (new) | ink background, `#a9cbbd` icon and action, `#c9c4ba` sub-line | phone toast |
| hairline (new) | `rgba(29,28,26,0.09)` dividers / `0.08` bars / `0.07` list rows | |

### Type roles

| Role | Font | Desktop | Phone |
|---|---|---|---|
| Display (new question) | Newsreader 480 | 46 / 1.05, −0.028em | 36 / 1.08, −0.026em |
| Page title | Newsreader 480 | 40 / 1.08, −0.026em | 32 / 1.1, −0.024em |
| Question | Newsreader 500 | 27 / 1.2, −0.018em, balance | same |
| Answer | Newsreader 400 | 18.5 / 1.66, −0.002em, pretty | same |
| Drawer title | Newsreader 500 | 26 / 1.18, −0.018em | 24 / 1.18 |
| Tag name (drawer) | Newsreader 500 | 32 / 1.1, −0.022em | 30 / 1.1 |
| Gap text | Newsreader 400 | 18.5 / 1.45 | 18 / 1.42 |
| Passage | Newsreader 400 | 17.5 / 1.62 | 17 / 1.6 |
| Card title (notes) | Newsreader 500 | 17.5 / 1.25 | 17 / 1.25 |
| Panel heading (feedback) | Newsreader 500 | 19, −0.01em | 24 (sheet title) |
| Brand | Newsreader 520 | 17, −0.012em | same |
| Label | Geist 500 | 11, uppercase, 0.08em | same |
| Body UI | Geist 400 | 14 (nav, lists, fields), 13.5 (history, chips, secondary buttons) | 15–16 |
| Sub-line | Geist 400 | 15 (page sub), 14–14.5 (intro, helper) | 14–14.5 |
| Meta | Geist 400 | 12–12.5 | same |
| Inputs | Geist 400 | 16 | 16 |
| Footnote marker | Geist 500 (KTD4) | 11 (active: 10.5 on an accent tag, radius 5) | same |

The allowed `font-size` set for the test is every size in this table, plus 11.5 (composer hint), 14.5 (Recent title, helper), 15 (tag name, page sub), 17 (phone card title and passage), 18 and 24 (phone gap text and sheet title), 30 and 32.

### Layout and shadows

Desktop: sidebar 264; answer column 640, top 72; Add column 560; Settings column 600, top 64; drawer 432 + 10 inset. Composer max 680, min-height 56, radius 22, padding 8 8 8 22; phone 54 tall, radius 27, side margin 12. Phone: menu or back button 44 at top left, content padding 20–22, sheets radius 28, sheet padding 18 24 34. Radii: nav 9–10, fields 11, cards 14, list/Add/feedback 16–20, composer 22, drawer 18, sheets 28, pills 999.

- Paper card: `0 0 0 1px rgba(29,28,26,.07), 0 1px 2px rgba(29,28,26,.05), 0 14px 36px -16px rgba(29,28,26,.2)`.
- List card: `0 0 0 1px rgba(29,28,26,.06), 0 1px 2px rgba(29,28,26,.05), 0 10px 30px -14px rgba(29,28,26,.18)`.
- Drawer: `0 0 0 1px rgba(29,28,26,.07), 0 2px 4px rgba(29,28,26,.04), 0 24px 60px -24px rgba(29,28,26,.28)`.
- Paper tab: `0 0 0 1px rgba(29,28,26,.05), 0 1px 2px rgba(29,28,26,.05)`.
- Phone sheet: `0 -12px 40px rgba(29,28,26,.16)`. Phone drawer: `12px 0 40px rgba(29,28,26,.18)`.

(The 1 px spread in these recipes is the card edge from the boards, not a focus or state outline.)

### Screen map

Screens are in the design handoff folder under `screens/`: `ask-D1…D5`, `ask-M1…M6`, `add-A1…A4`, `add-AM1…AM3`, `tags-S1…S3`, `tags-SM1…SM3`. Live boards are in `boards/` (`Ask v2`, `Add v2`, `Settings Tags v2`); open them for exact values when a screen is ambiguous.

---

## Planning Contract

### Context and Research

Local research only; the existing web app is the grounding (no external research: strong local patterns, no new technology).

- `app/web/index.html` (one sheet element for Source/Gaps/Feedback/Tags; sprite icons; `<template id="turn-template">`), `app/web/styles.css` (tokens, `--ctl`, desktop ≥900 and phone <900 blocks, drawer at 1200), `app/web/app.js` (`renderTurn`, `renderRow`, `fillSource`, `fillGaps`, `buildFeedback`, `openSheet`/`closeSheet`/`teardownSheet`, `setNavOpen`, `refreshConversations`), `app/web/capture.js` (`classify`/`detect`, `requestBody`, `renderCapture`, `openWith`, drag handlers at the end), `app/web/settings.js` (`renderRow`, `fillTag`), `app/web/sw.js` (shell cache `ka-shell-v16`).
- Data already in the browser: each turn's `created_at`; each conversation's `last_activity_at`; each capture's `created_at`. The signed-in email and any name are **not** (OQ1). `/api/tags` returns suggested tags only.
- Existing tests that this work must update on purpose: `test_text_uses_only_the_agreed_type_scale`, `test_shell_cache_is_bumped…` (cache name), `test_navigation_is_two_tabs…` (the New question button is gone; Ask starts a new question), `test_settings_opens_from_the_account_menu…` (the gear opens Settings), `test_text_colours_meet_wcag_aa…` (adds `paper`). Everything else in `tests/api/test_static.py` must keep passing unchanged.
- `tests/api/test_release_c.py` runs the shipped JS in Node with small stubs and constrains the build; each unit below says where. (1) The Recent-row test slices `capture.js` from `const KINDS` to `function pollCapture`: new helpers (`formatAge`) live inside that slice, and the time column sits outside the `meta` element so the asserted meta order stays. (2) The gap-button test calls `fillGaps` with only `createElement`, `textElement` and `closeSheet` stubbed and reads `item.children[1]` as the add button: no `icon()` call, no element before the button (a CSS or text "+"). (3) The Settings test stubs elements without `classList` and reads the row children as open, block, approve: keep that order and use `className`. (4) `AppNameTests` asserts the exact brand markup `<span class="brand label">Knowledge Agent</span>`: keep it or update the test deliberately. (5) The `.capture-state`, `.retry-capture` and `.capture-item {` CSS rules stay findable by those selectors. Read the file before each unit that touches these areas and update a test only on purpose.
- Process rules: `AGENTS.md` "Web app type scale" is updated in U1; `$finish-task` reconciles `FOUNDATION.md#current-progress` and the web-app system doc at the end.

### Implementation Units

Dependency order: U1 → U2 → (U3, U4, U5, U6 in any order after U2) → U7, U8, U10, U11 after U6; U9 after U5 and U6; U12 after U1 and U5; U14 after U5; U13 last. The per-unit dependency lines are authoritative; the numbering is not the build order.

#### U1. Tokens, type roles and the new type test

**Goal:** One stylesheet foundation every later unit uses: new tokens, the type-role classes/variables, 4-pt spacing variables, shadow variables, tabular figures, the allowed-size test.

**Requirements:** R1, R2, R3, R4, R5, KTD1, KTD4, KTD7

**Dependencies:** none

**Files:** `app/web/styles.css`, `AGENTS.md`, `tests/api/test_static.py`

**Approach:** Add `--paper`, `--placeholder`, `--dot`, `--row-active`, `--toast*`, `--hairline*` and the shadow recipes as variables. Replace the single-size `.small`, `.caption` and `.kicker` with role classes from the type table, and make the existing `.section-title` class the 11 px uppercase label role (folding `.kicker` into it). Leave `.label` alone: it is the hook that hides sidebar text in the collapsed rail. Keep `--ctl` at 32/44. Rewrite the type-scale test to read decimals (`\d+(?:\.\d+)?px`) and compare against the allowed set in the Design Reference. Rewrite the `AGENTS.md` "Web app type scale" section to list the roles and sizes and say the test enforces it. Mark the 2026-10-19 plan's scale as superseded in `AGENTS.md` (one line), not by editing the old plan.

**Patterns to follow:** existing token block and `ReleaseCTests` helpers.

**Test scenarios:**
- Happy path: every `font-size` in `styles.css` is in the allowed set; a size outside it (for example `13px`) is rejected by the test regex.
- Contrast: ink, ink-soft, muted and accent each meet 4.5:1 on `paper`; `placeholder` meets 4.5:1 on `paper` and `ground`; `accent-ink` on `accent` still passes. The accent fill (active number, marker, gap chip) meets 3:1 against the chip and pill fills it replaces, since state is shown by fill alone.
- Edge: no `font-weight` of 600 or higher appears on `button`, `.marker` or `.source-number` rules.

**Verification:** `python -m unittest discover -s tests/api -p 'test_*.py'` passes; `AGENTS.md` type-scale text matches the test's set.

#### U2. Sidebar and phone drawer

**Goal:** The new sidebar on both widths.

**Requirements:** R7–R13, KTD5

**Dependencies:** U1

**Compare against:** `ask-D1-new-question.png`, `ask-D2-answer.png`, `ask-M6-nav-drawer.png`.

**Files:** `app/web/index.html`, `app/web/styles.css`, `app/web/app.js`, `tests/api/test_static.py`

**Approach:** Markup order: brand row (26 px icon tile from `/static/icons/icon-192.png`, name, collapse or close), Ask and "Add to notes" nav, History label with chevron (keep `#sidebar-history` and its fold behaviour), grouped list, foot (avatar → account menu, "Account" label, gear button with `id="open-settings"` and an accessible name "Settings"; the menu keeps only the two sign-out items). Add a pure `groupByAge(items, now)`: Today = same local calendar day, This week = the previous six days, Earlier = the rest; empty groups are not drawn; `refreshConversations` renders group captions. The gear shows `aria-current` while Settings is open (set where `applyView` handles views). Collapsed sidebar (rail) keeps working as today: text labels hidden (the `.label` hook), History and its groups hidden, gear stays. History is a button with `aria-expanded`; group captions are headings over their lists; the "Account" text is not focusable; tab order follows the visual order with the gear last. Keep the brand markup `<span class="brand label">Knowledge Agent</span>` (existing `AppNameTests`).

**Test scenarios:**
- `groupByAge` in Node: items at 09:00 today, yesterday, 5 days ago, 8 days ago, around local midnight → Today / This week / This week / Earlier; empty input draws no groups.
- Page: no `id="new-question"`; the Ask tab handler calls `newQuestion()`; `id="open-settings"` has an accessible name; the account menu no longer lists Settings; the nav button reads "Add to notes" and still has `data-tab="add"` once.
- Every icon-only button still has an `aria-label` (existing test).
- Integration: the popstate/back-button tests for the phone drawer still pass unchanged.

**Verification:** existing nav, back-button and drawer tests pass; demo-server walk-through on both widths against the three screens.

#### U3. Ask: new question screen and turns

**Goal:** Date line and heading, meta line, new question and answer type, active marker, reading state.

**Requirements:** R14–R18, KTD4

**Dependencies:** U2

**Compare against:** `ask-D1`, `ask-D2`, `ask-M1`, `ask-M2`.

**Files:** `app/web/index.html` (turn template: meta line, skeleton lines), `app/web/styles.css`, `app/web/app.js`, `tests/api/test_static.py`

**Approach:** `setEmpty` fills the date line from the local date. `renderTurn` writes the meta line from `turn.created_at` ("Today, 9:41" / "Yesterday, …" / weekday) plus the source count once known, using one pure `formatTurnTime`. The running state shows "Reading your notes · Ns", the three-dot indicator (CSS animation; static under reduced motion) and three skeleton lines. Active state is owned by one `syncActive()` helper in `app.js`, called from both the fresh-open and in-place-swap branches of `openSheet` and from `teardownSheet` (without adding a literal `teardownSheet()` call). It recomputes `is-active` and `aria-current="true"` on markers, source numbers, gap chips and (U12) tag rows from the open sheet and item, so stepping and swaps never leave a stale fill. Every marker in the turn whose number equals the open source's number is active (one answer can cite a source several times); a source opened from a number or the foot bar activates its markers too. All states are fills, no rings. The skeleton lines and dots are `aria-hidden`; the pending text is announced once through the turn's live region, and the seconds counter is not announced. Relative times and the date line are recomputed when a view is shown.

**Test scenarios:**
- `formatTurnTime` in Node: same day → "Today, 9:41"; previous day → "Yesterday, …"; older → weekday and time; invalid date → empty string.
- Running turn renders the skeleton and dots, and a finished turn removes them; a failed turn keeps the neutral-grey message with Retry.
- Opening a source marks its number and every marker for that source (and no others) active; switching to another source in place moves the fill; closing the sheet clears all; opening the gaps sheet marks the chip, not a number.
- The answer row still shows for a finished answer with no sources (existing test).

**Verification:** `ask-D1/D2/M1/M2` match by eye on the demo server; `test_every_finished_answer_keeps_its_feedback_button` passes.

#### U4. Composer

**Goal:** Pinned composer with fade, new size and hint.

**Requirements:** R19, KTD2, KTD3

**Dependencies:** U2

**Compare against:** `ask-D1`, `ask-D2`, `ask-D3`, `ask-M1`.

**Files:** `app/web/index.html` (hint line), `app/web/styles.css`

**Approach:** Composer wrapper gains the fade background; width 680 (640 while `[data-sheet]` is set); radius 22, min-height 56, paper surface and shadow; send button stays 36 px and goes to reduced opacity while the box is empty (a class toggled in `autosize`/input handling). The hint shows only with a mouse (`(pointer: fine)`), matching the existing Enter-to-send rule at `app/web/app.js` keydown handling. Placeholder switches between "Ask a question" and "Ask a follow-up" in `setEmpty`. Phone keeps today's phone composer proportions (the screens: 54 tall, radius 27, 12 px side margin). The dimmed send button sets `aria-disabled="true"` (still focusable, activation does nothing). Keep both layouts working: the centred empty state (`.view-ask.is-empty`) and the pinned composer over a thread, including the thread's bottom padding so the last line is not hidden under the fade. When a drawer is open at 1200 px and wider the page margin includes the 10 px drawer inset.

**Test scenarios:**
- Placeholder is "Ask a question" on a new question and "Ask a follow-up" once turns exist.
- The hint element is hidden under `(pointer: coarse)` rules and present otherwise (CSS text check).
- No outline or `box-shadow` ring is declared for `.composer-box:focus-within` beyond today's hairline (guards KTD3).
- Existing: Enter sends, Shift+Enter inserts a line (keydown handler untouched).

**Verification:** visual match with D2/D3; the page behind a long thread scrolls under the fade.

#### U5. Drawer and phone sheets (source, gaps, feedback)

**Goal:** The floating paper drawer and the new source, gaps and feedback content, on desktop and phone. (Stepping through sources is U14.)

**Requirements:** R20–R26, KTD2, KTD3

**Dependencies:** U2

**Compare against:** `ask-D3`, `ask-D4`, `ask-D5`, `ask-M3`, `ask-M4`, `ask-M5`.

**Files:** `app/web/index.html` (`i-forward` icon), `app/web/styles.css`, `app/web/app.js`, `tests/api/test_static.py`

**Approach:** Restyle `.sheet` per layout (desktop 432 wide, 10 px inset, radius 18; phone radius 28, handle, 24 px padding). `fillSource` writes the kicker "Source N of M", title, folder · See in Obsidian, hairline, "Cited passage" label (hidden when there are no passages), passage cards with the quote glyph via CSS, then a foot bar with the number buttons (stepping controls are added by U14). `fillGaps` gets the helper line, numbered 01/02 rows (desktop) and the "+ Add a note to fill this gap" link with a plain "+" character, not an `icon()` call; the add button stays `item.children[1]` (the existing gap-button test). `buildFeedback` heading becomes "What was wrong?" at the panel-heading role; the phone sheet adds "Not now" (closes the sheet via the existing close path). Do not change `openSheet`, `teardownSheet` or `closeSheet` call counts (guarded by existing tests).

Focus and modality: the sheet keeps `role="dialog"` with an accessible name. Below 1200 px the drawer is modal (`aria-modal`, background inert, focus trapped, Esc and scrim close); at 1200 px and wider it is non-modal (page pushed aside, no trap). In both, focus moves into the sheet on open and returns to the opening control on close. The 1200 px push amount includes the 10 px inset (432 + 10). The close button and foot controls show the thin dark keyboard focus ring (KTD3).

**Test scenarios:**
- Source sheet: a source with no passages hides the "Cited passage" label; the kicker reads "Source 2 of 3".
- Modality: below 1200 px the open sheet makes the page inert and traps Tab; at 1200 px and wider it does not.
- Gaps sheet: two gaps render "01" and "02"; the link text is "Add a note to fill this gap".
- Feedback sheet: "Not now" closes without sending; existing feedback save/clear behaviour unchanged.
- Existing tests that count `teardownSheet()` and `history.back()` still pass.

**Verification:** visual match with the six screens; keyboard and phone walk-through on the demo server.

#### U6. Add screen: box, detection row, actions

**Goal:** The new Add layout and copy.

**Requirements:** R27–R30, KTD2, KTD3

**Dependencies:** U1, U2

**Compare against:** `add-A1-ready`, `add-A2-youtube-tags-open`, `add-AM1-ready`.

**Files:** `app/web/index.html`, `app/web/styles.css`, `app/web/capture.js`, `tests/api/test_static.py`

**Approach:** Column 560, title and sub-line (phone drops sentence two via a hidden span), paper card radius 20, placeholder "Paste or type here…", detection row with 36 px tile and the new subtitles in `DETECTED` ("Transcript saved to Transcripts" for YouTube, "Filed in the right folder for you" otherwise). Fold under a hairline; fields 40/44 px radius 11. Actions row: attach, then "or drop a file anywhere" (hidden on touch and when the box has content or a file), Save right (dimmed state already exists). Remove the Add box focus ring (`.capture-form:focus-within`).

**Test scenarios:**
- `DETECTED` copy: YouTube → "Transcript saved to Transcripts"; text, link, markdown, textfile → "Filed in the right folder for you"; the existing classification probe still passes.
- Hint "or drop a file anywhere" is hidden when the box has text or a screenshot is pending.
- `.capture-form` declares no focus ring (guards KTD3).
- Existing: Save disabled until there is something to save; `test_add_tab_detects_kinds_and_has_a_visible_attach_button`.

**Verification:** A1/A2/AM1 match on the demo server.

#### U7. Recent ledger and time column (P6)

**Goal:** Recent as a ledger with times and a weekly count.

**Requirements:** R32, R-P6, R5

**Dependencies:** U6

**Compare against:** `add-A1`, `add-A2`.

**Files:** `app/web/capture.js`, `app/web/styles.css`, `app/web/index.html`, `tests/api/test_static.py`

**Approach:** Pure `formatAge(iso, now)`, defined inside the `const KINDS` … `function pollCapture` slice of `capture.js`: under a minute "now", then "N min", "N h", "N d" for 1 to 6 days, a short date from 7 days on. `renderCapture` adds the time column (tabular figures) from `job.created_at` as a sibling of the body, outside the `meta` element, so the row stays [state, body, time]; locally created rows use the current time. Times recompute whenever the Add view is shown and once a minute while it is visible. The Recent heading shows "N this week" (captures in the last seven days) updated whenever a row is added or the list loads; it is hidden at zero and also hidden when the list holds 20 rows, because `/api/captures` returns at most the 20 most recent and the count would be incomplete. State icons, copy, Retry, See in Obsidian and polling stay as they are; failed rows stay neutral grey.

**Test scenarios:**
- `formatAge` in Node: 20 s → "now"; 12 min → "12 min"; 61 min → "1 h"; 26 h → "1 d"; 6 days → "6 d"; 7 days and 9 days → a date; future timestamp (clock skew) → "now"; invalid → empty.
- Week count counts only captures from the last seven days and ignores invalid dates; it is hidden at zero and at 20 rows.
- The existing Recent-row test in `tests/api/test_release_c.py` still passes (row shape and meta order unchanged).
- A running row shows "now" and updates to a relative time on the next render.
- Existing Recent states (running, indexed, queued, failed with Retry) still render.

**Verification:** A1/A2 ledger match; failed and Retry rows checked on the demo server.

#### U8. Drag-over state

**Goal:** The view-wide drop target without a ring.

**Requirements:** R31, KTD3

**Dependencies:** U6

**Compare against:** `add-A4-drag-over`.

**Files:** `app/web/index.html`, `app/web/styles.css`, `app/web/capture.js`

**Approach:** An inset rounded target element (icon tile, "Drop to add", "Screenshots, .md and .txt files") shown while `dragging` is set on the Add view; fill is a soft accent tint over paper with the paper-card shadow, no border or ring; the page content fades to 35%. Drag handlers stay as in `capture.js` (dragover, dragleave, drop). The target is `aria-hidden`, `pointer-events: none` and fixed in place (`#capture` scrolls), so it never becomes a drag-event target and cannot flicker; the state also clears on `dragend`, on Escape and when the pointer leaves the window. The visible attach button remains the keyboard and touch path. The 35% fade has no transition under reduced motion.

**Test scenarios:**
- Dragging over shows the target; leaving or dropping hides it; dropping a screenshot, `.md` and `.txt` file still goes through `takeFile` (existing flow).
- The target's CSS declares no `border` or `outline`.

**Verification:** A4 match.

#### U9. Fill a gap mode (P3)

**Goal:** Add opens in a gap mode from "What's missing".

**Requirements:** R-P3, KTD8

**Dependencies:** U5, U6

**Compare against:** `add-A3-fill-a-gap`.

**Files:** `app/web/index.html`, `app/web/styles.css`, `app/web/capture.js`, `app/web/app.js`, `tests/api/test_static.py`

**Approach:** `renderTurn` stores `question` and `conversationId` in `turnData`; `fillGaps` reads them from `data` (tolerating their absence, as the existing gap-button test calls it with only `gaps`) and calls a new `captureScreen.openGap({ gap, question, conversationId })` instead of `openWith(gap)`. If nothing else calls `openWith` afterwards, remove it. Gap mode shows "← Back to the answer" (returns to Ask and reopens that conversation), the title "Fill a gap", a mint panel ("Missing from your notes" label, the gap in Newsreader, "From “<question>”"), the box empty, "Save note" instead of Save, and the line "Once it's searchable, ask again and the answer can use it." On entry focus goes to the box; the mint panel is a labelled region ("Gap to fill"); Back returns focus to the gap link that opened it. A long gap wraps without truncation, and a missing question hides the "From" line. A failed Save keeps the mode, the panel and the typed text and shows the usual inline error. Leaving gap mode (Back, Save, switching tab from the sidebar, sign-out, `reset()`) restores the normal title, button and copy. `openWith` stays for any other caller. Add the new fields to `reset()` so sign-out clears them (the existing sign-out test lists fields; extend it).

**Test scenarios:**
- Opening from a gap shows the gap text and question; the box is empty; the button reads "Save note".
- Back returns to the conversation; after a Save the mode resets and the next Add visit shows the normal screen.
- A draft already in the box is kept when gap mode opens (as `openWith` keeps it today).
- Sign-out clears gap mode.
- A double tap on the gap link does not open it twice (existing `add.disabled` guard kept).

**Verification:** A3 match; end-to-end on the demo server: ask, open gaps, fill, save, Recent row appears.

#### U10. Tag chips in Add (P4)

**Goal:** Tags typed as removable chips.

**Requirements:** R-P4, R29

**Dependencies:** U6

**Compare against:** `add-A2`, `add-AM2`.

**Files:** `app/web/index.html`, `app/web/styles.css`, `app/web/capture.js`, `tests/api/test_static.py`

**Approach:** The tags input becomes a chip field: Enter, comma or leaving the field turns the typed text into a chip; Backspace on an empty field removes the last chip; each chip has a remove button (✕, 28 px target). `requestBody` builds `tags` from the chips plus any pending text, so the request shape is unchanged. A pure `parseTags(text)` trims, drops empties and duplicates (case-insensitive). The "Approved tags" suggestion row is not built (KTD6). Accessibility: the chip container is a group labelled "Tags" holding the input; each chip is a list item with a remove button named "Remove tag <name>" (28 px drawn, 44 px touch area on phone, thin dark focus ring); adds and removals are announced through a polite live region; Enter creates a chip and never submits the form; pending text commits on blur. Chips wrap onto new lines; a duplicate briefly changes the existing chip's fill instead of vanishing; long chips truncate with an ellipsis and keep the full text in their accessible name. `reset()` and the post-save clear also clear chips.

**Test scenarios:**
- `parseTags` in Node: "a, b" → [a, b]; " A,a " → [A]; ",,," → []; a 60-character tag is kept as typed.
- Enter and comma create a chip; Backspace on empty removes the last; remove button removes its chip.
- The request body's `tags` array equals the chips in order, with pending typed text included.
- Pasting "a, b, c" creates three chips.
- Sign-out and a successful save clear chips.

**Verification:** A2/AM2 match; a save with chips shows the tags in the created note path as before (API untouched).

#### U11. Phone saving toast (P5)

**Goal:** Toast after Save on phone.

**Requirements:** R-P5

**Dependencies:** U6

**Compare against:** `add-AM3-saving`.

**Files:** `app/web/index.html`, `app/web/styles.css`, `app/web/capture.js`, `tests/api/test_static.py`

**Approach:** A toast element (live region, ink background, tokens from U1) shows "Saving…" with the sub-line from the screen after a successful send, auto-hides after about four seconds, and never shows on desktop. On phone the inline success message under the box is hidden (the toast replaces it; decided 2026-10-21, an intended change to an existing state) and the new Recent row is scrolled into view; on desktop the inline message stays. Failures use the inline message only; no toast for a failed send. The live-region container is always in the DOM and its text is set on show; no action link is built, so the `toast` action colour token is not used; the toast never takes focus and fades without movement (no transition under reduced motion).

**Test scenarios:**
- A successful send shows the toast with `role="status"`; it clears itself; a second save restarts the timer.
- A failed send shows the inline message and no toast.
- The toast is `display: none` at ≥ 900 px (CSS text check).
- Toast text and action colours meet 4.5:1 on the toast background (contrast test).

**Verification:** AM3 match at phone width.

#### U12. Settings · Tags

**Goal:** The refined Tags screen and drawer.

**Requirements:** R33–R38, KTD2, KTD3

**Dependencies:** U1, U5

**Compare against:** `tags-S1`, `tags-S2`, `tags-S3`, `tags-SM1`, `tags-SM2`.

**Files:** `app/web/index.html`, `app/web/styles.css`, `app/web/settings.js`, `tests/api/test_static.py`

**Approach:** Title and tabs per R33; intro sentence (short form on phone); label row with "Suggested · N" and "Approve all"; the list on a paper card; rows with name and count and two icon buttons in the existing 28 px size with today's touch area; the open row uses `--row-active` and swaps its buttons for a chevron; drawer content (kicker, 32 px tag name, "N notes proposed it", note cards at the card-title role) and a foot bar with compact Block and Approve pills at 32 px; phone sheet keeps full-width buttons. Empty state panel with the mint check circle. Decide/approve-all logic, focus handling (`keepFocus`) and the `/api/tags` calls stay unchanged. Row buttons get names "Block <tag>" and "Approve <tag>"; the open row exposes `aria-current` through `syncActive()` (U3). Keep the row child order [open, block, approve] and set classes with `className`, because the existing Settings test stubs elements without `classList`. An empty note-card title falls back to its path.

**Test scenarios:**
- The count in the label equals the rows; "Approve all" shows only with two or more suggestions (today's rule); with none, the "All caught up" panel shows and the list is hidden.
- Opening a row marks it active and shows the chevron; closing restores the buttons.
- Approve and Block still call `/api/tags/<name>/<verdict>` (existing assertion) and focus lands on the next row.
- No rule in the tags styles declares a `border` or `outline` on buttons other than the shared `:focus-visible` ring (guards KTD3); the tab underline and the empty-state panel hairline (R38) are the only borders.

**Verification:** S1–S3, SM1, SM2 match; existing Settings tests pass.

#### U14. Source stepping (P2)

**Goal:** Step through the cited sources without closing the drawer.

**Requirements:** R-P2, KTD3

**Dependencies:** U5

**Compare against:** `ask-D3-source-drawer`, `ask-M3-source-sheet`.

**Files:** `app/web/index.html` (`i-forward` icon), `app/web/styles.css`, `app/web/app.js`, `tests/api/test_static.py`

**Approach:** The source sheet foot bar gets back and forward arrow buttons around the number buttons, and on desktop the hint "← → to step · Esc to close". Stepping reuses `openSheet("source", null, fill)` exactly as the number buttons do, adds no history entry and no `teardownSheet()` call. At the ends the arrows use `aria-disabled` (still focusable, so focus is not dropped) and do not wrap. Focus stays on the pressed arrow; the kicker "Source N of M" sits in a polite live region and the active number has `aria-current`. ← and → are handled in the existing document keydown handler only while a source sheet is open, with no modifier key, not already `defaultPrevented`, focus inside the sheet or on the page body (never in a text field), and they do not override arrow keys on a focused control that already uses them. On phone the arrows stay visible as the primary path (44 px touch area); the numbers row scrolls horizontally rather than shrinking below 44 px; a horizontal swipe (≥ 48 px, mostly horizontal, not starting within 24 px of the screen edge, `touch-action: pan-y` on the body) is an extra.

**Test scenarios:**
- Stepping in Node: with 3 sources, forward from the last stays on the last and back from the first stays on the first (no wrap); the arrows report `aria-disabled` at the ends.
- Keys: ← → step only when a source sheet is open and focus is not in an input or textarea; modifier keys are ignored; Esc still closes.
- Swipe: a 60 px horizontal swipe steps; a mostly vertical swipe and one starting at the screen edge do not.
- Stepping adds no history entry (the existing `history.back()` and `teardownSheet()` count tests still pass).
- After a step exactly one number has `aria-current` and the markers of that source are active (U3 `syncActive()`).

**Verification:** D3 and M3 match; keyboard and phone walk-through on the demo server.

#### U13. Shell cache and finish

**Goal:** Ship-ready: cache bump, docs, progress.

**Requirements:** all

**Dependencies:** U1–U12, U14

**Files:** `app/web/sw.js`, `tests/api/test_static.py`, `FOUNDATION.md`, the web-app system doc named by `SYSTEM.md`, `AGENTS.md` (status line if the web-app wording changes), `docs/plans/2026-10-21-003-deferred-paper-refinement-parts.md`

**Approach:** Bump `ka-shell-v16` to `ka-shell-v17` and the test that checks it. Run `$finish-task`: reconcile the progress tracker and the one relevant system doc. Add the parked-parts doc link where the redesign plan is referenced.

**Test scenarios:** `test_shell_cache_is_bumped…` passes with v17; every linked static file is still in the shell list (existing test).

**Verification:** all suites below green.

### Verification Contract

- `python -m unittest discover -s tests/api -p 'test_*.py'` after every unit; the full `ingestion`, `models`, `storage`, `agent`, `api`, `capture`, `evals` suites once at the end (database suites need their disposable URL; if unavailable, say which did not run).
- `python .local\demo_server.py` walk-through by the product owner on desktop and phone width against the screen named in each unit, before any PR.
- Full `/ce-code-review` with all reviewers before the PR; `/ce-doc-review` of this plan on request.
- No inline style, `innerHTML`, remote font or inline script introduced (existing tests).

### Definition of Done

- Every unit's named screens match, apart from the documented amendments.
- All "Existing States That Must Survive" rows exercised once on the demo server.
- Type, contrast, navigation and shell tests updated and green; no server, schema or prompt file changed.
- Nothing from the parked list shipped; deferred doc is current.
- `AGENTS.md` type scale, `FOUNDATION.md#current-progress` and the system doc updated through `$finish-task`.
- No personal data, private folder names or design screens committed.

### Risks

- **Type test is looser.** 23 allowed sizes protect less than six did. Mitigation: the test lists the table explicitly and also bans weights ≥ 600 on controls.
- **Drawer stepping and history.** Stepping must not add history entries or double-teardown the sheet. Mitigation: reuse `openSheet`; existing back-button tests guard it.
- **Gap mode changes saved content** (KTD8). Mitigation: resolved as OQ2.
- **Visual drift** (two earlier UI builds drifted). Mitigation: every unit names its screens, and the product owner compares each before the PR.

### Deferred to Implementation

- Exact hairline/colour of the quote glyph and the skeleton animation timing: read from the boards while building.
- The exact "Yesterday / weekday" wording of the turn meta line beyond what the screens show.
