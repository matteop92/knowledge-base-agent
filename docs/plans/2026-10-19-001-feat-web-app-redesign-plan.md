---
title: Knowledge Agent Web App Redesign - Plan
type: feat
date: 2026-10-19
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# Knowledge Agent Web App Redesign - Plan

## Goal Capsule

- **Objective:** Replace the web app's look and layout with the design Matt agreed on 2026-10-19: two tabs (Ask, Add), a Paper look, answers whose sources and gaps open in a bottom sheet, and one smart Add box. The change is frontend only.
- **Authority:** KTD1-KTD9 are Matt's settled decisions and are not reopened by executors. KTD10 onward are planning decisions an executor may revisit with evidence and a note in this plan. The design reference is a Claude Design canvas ("Paper" boards: Ask, Source, Add, Desktop, Feedback, Gaps) and two mock pages that live in the project thread, not in this repo; the executor asks Matt for them when a measurement is missing. Existing contracts in `SYSTEM.md` and `FOUNDATION.md` still hold: no API, schema or agent change.
- **Design boards are required input:** before building any unit, read the source of the matching "A Paper" boards in the design canvas (its link is in the project thread and project memory) and take colours, type sizes, spacing, radii and layout from them; this plan's wording alone is not enough (Release A was rebuilt after missing this). Boards per unit: U1-U4 `Main.dc.html` (phone Ask), `A-Add.dc.html`, `A-Desktop.dc.html`; U5 `A-Add.dc.html`; U6 `Main.dc.html`, `A-Source.dc.html`, `A-Desktop.dc.html`; U7 `A-Gaps.dc.html`, `A-Desktop.dc.html`; U8 `A-Feedback.dc.html`, `A-Desktop.dc.html`; U9 `Main.dc.html`. Where a board and this plan differ, the board wins and the difference is noted here.
- **Execution profile:** Three releases, each its own PR to `main`. Release A (look and navigation shell, U1-U4) is merged. Release B (Add screen, answers, sources, gaps, feedback, U5-U9) is built on `claude/web-redesign-release-b`, was reviewed twice and has had Matt's design review applied (see "Changes agreed after Release B review"); it waits for Matt's local check before its PR. Release C is what is left after that (see "Release C" below). Each release leaves a working app.
- **Stop conditions:** Stop and ask Matt when a design measurement needed for a unit is missing from the canvas; when a font cannot be self-hosted under its licence; when the gap sentences the agent returns cannot be shown in plain words without losing meaning; or when a change would need a server or database change.
- **Process rule:** Matt does the visual and device checks himself, on his phone and desktop. The executor runs only the automated tests and static checks, never opens or screenshots the app on his behalf, and hands him each release and waits. The manual checks below are Matt's. The executor opens each release PR but does not merge it, or start the next release, until Matt has checked the branch and accepted it; if a check fails after merge, revert the PR.
- **Tail ownership:** Each release ends with `$finish-task`, which reconciles `FOUNDATION.md#current-progress` and the one relevant system doc.

---

## Product Contract

### Summary

The app keeps every capability it has today and changes how it looks and is organised. Ask and Add become two equal tabs. Past conversations move into a History panel. The question box is pinned to the bottom. Sources become tiny footnote numbers in the text plus a row under each answer; gaps and thumbs-down get their own sheets; Add becomes one smart box.

### Problem Frame

Matt finds the current UI "a mess". The Add screen replaces the thread but keeps the old question's title and buttons, so he cannot tell where he is. The phone top bar crowds four controls. Add shows five equal-weight fields. Sources are small chips that push the answer around when opened. Gaps and the thumbs-down are easy to miss. There is one plain colour treatment, no type hierarchy and no desktop-specific layout.

### Requirements

**Structure and navigation**

- R1. Ask and Add are two top-level entries at the top of one sidebar. On desktop the sidebar sits beside the page; on phones the same sidebar is a drawer opened by one menu icon at the top left (no bottom bar, no top bar title). The active entry is always visible and each screen has its own title.
- R2. History is an item in the sidebar; its chats list opens in place directly under it (a fold, no card or popover). A "New question" pill and an account circle sit at the foot of the sidebar, and the account circle opens the sign-out options. When the desktop sidebar is collapsed, History expands it again.
- R3. The desktop sidebar is a slightly darker warm beige than the main area with no border line, and collapses to icons only with a small button; the choice is remembered on that device. When collapsed, History and New question each keep an icon, so past conversations stay reachable.
- R4. The question and follow-up box is pinned to the bottom of the Ask tab on phone and desktop.
- R5. Android back closes an open sheet before it leaves the app. (The phone navigation drawer does not yet take part in Back; see Release C.)

**Add**

- R6. Add is one box that accepts typed or pasted text, links, YouTube links, dropped or pasted screenshots, and `.md`/`.txt` files, with an Attach button for files.
- R7. Before saving, the app shows what it detected (text, web link, YouTube video, screenshot, Markdown file, text file). Title and tags are folded away.
- R8. Recent saves stay visible on the Add tab with their states (saving, saved and searchable, searchable after the next pass, failed with Retry), and a share from Android still opens Add with the item filled in.

**Answers**

- R9. Source numbers inside the answer text are tiny footnote marks; a row of small numbers under each answer lists the same sources.
- R10. Tapping a number opens a bottom sheet on phone and, on desktop, a full-height panel from the right edge (it pushes the page at 1200 pixels and wider and overlays it below that). It shows the note title, its folder path with a "See in Obsidian" link beside it, and every passage cited from that note in answer order. Clicking or tapping the page outside it, Escape, or Back closes it.
- R11. When an answer has gaps, a soft green chip with an icon and the gap count follows the source numbers; tapping it opens a "What's missing" panel listing each gap in plain words between hairlines, each with a plain "+ Add a note to fill this gap" link that opens Add with a note started. Run problems (the agent could not finish checking) are not gaps: they show as a plain line under the answer, with no count and no link.
- R12. Thumbs-down is the only feedback. Tapping it opens a note box and Send (sheet on phone, inline panel on desktop); the saved state is visible and can be removed.
- R13. Answering, failed (with Retry), answered-with-gaps, spend-cap, empty and signed-out states remain and are restyled; an answer with no citations still shows its feedback control, and "Answered with gaps" stays in the answer row's accessible text.

**Look**

- R14. The look is Paper: warm off-white ground, near-black ink, one dark green accent, two typefaces (Newsreader for question titles, answers and quoted passages; Geist for everything else), six text sizes (12, 14, 16, 18, 22, 26), Phosphor Light icons, soft round corners, no boxes around answers. Button text is never bold, and text boxes show no outline.
- R15. Light only. Dark mode is out of scope.

**Quality**

- R16. Controls are compact with a mouse (32 pixels) and finger-sized (44 pixels) on touch screens; the small source numbers and gap chip keep a larger touch area than they draw. Everything is keyboard reachable, with a thin visible focus ring on buttons and links; sheets and the account menu close on Escape and return focus (the phone drawer hides the page behind it; on desktop the Source and Gaps panel sits beside the page and does not trap Tab); text meets WCAG AA contrast.
- R17. No behaviour behind the screens changes: same API calls, same stored data, same share target, same offline shell.

### Scope Boundaries

**Deferred to Follow-Up Work**

- Dark mode.
- The suggested-tags page (first item of Phase 3). The sidebar entries are plain markup so a third entry is a small addition later.

**Outside this change**

- Any server, database, prompt or agent change, including new fields on the receipt.
- Thumbs-up feedback or reason chips.

### Success Criteria

Matt can use every flow he uses today on his phone and desktop, and calls the result acceptable. No existing capability is lost (U-level checklist in the Verification Contract). The complaints in the Problem Frame are gone: he can say which screen he is in (the page title says it); opening a source does not move the answer text; he notices the gap icon on an answer that has gaps without being told; the phone's top area holds only the menu icon.

---

## Planning Contract

### Context and Research

- **Files in play:** `app/web/index.html` (shell, one `<template>` per turn), `app/web/styles.css` (103 lines, one flat sheet), `app/web/app.js` (sign-in, conversations, polling, answer rendering, feedback, layout), `app/web/capture.js` (capture overlay), `app/web/sw.js` (shell cache `ka-shell-v2`, share target), `app/web/manifest.webmanifest`.
- **Security constraint that shapes the build:** the server sends `style-src 'self'` and `script-src 'self'` (`app/api/main.py`). Inline `style` attributes, `setAttribute("style", ...)`, `cssText` and inline scripts are blocked, so all styling is classes in `styles.css`; setting a single property from script (`element.style.height`, as `autosize` does) is allowed. `font-src` falls back to `default-src 'self'`, so fonts must be served from `/static`.
- **Existing tests:** `tests/api/test_static.py` asserts no `innerHTML`/`outerHTML`/`insertAdjacentHTML`/`document.write`/`eval(` in the three scripts, no inline script or `onclick=` in the page, the share-target manifest, and static routes with the CSP. There is no JavaScript test runner; new checks are static-source assertions plus the manual checklist below.
- **What the receipt provides** (`app/api/answers.py`, `build_receipt`): `answer`, `gaps` (a list of free-text strings), `citations` (title, `relative_path`, `obsidian_url`, `text`), `needs` (id to description). `app/agent/harness.py` writes two kinds of gap strings: the model's own sentences, and `Unresolved need N<k>: <description>`.
- **Feedback API:** `POST /api/turns/{id}/feedback` with `{note}` stores verdict `down` plus the note; `DELETE` removes it. No change needed.
- **Prior plan:** `docs/plans/2026-10-16-002-feat-go-live-web-app-plan.md` built the current app and holds its decisions.

### Key Technical Decisions

- KTD1. Ask and Add are two equal tabs with a History panel. (session-settled: user-directed — chosen over tidying the current layout and over one combined box: the tab bar always shows where you are.) Revised 2026-10-20 at Matt's request: Ask and Add are entries in one sidebar, which on phones is a drawer opened by a menu icon.
- KTD2. Add is one smart box with a detected-type label, optional Title and tags folded away, and Recent saves visible. (session-settled: user-directed — chosen over type-first forms and a tidied copy of today's form.)
- KTD3. Footnote-sized numbers in the text, a small row of numbers under the answer, and a bottom sheet for the passage with only an Obsidian link (now the underlined words "See in Obsidian" beside the folder path). (session-settled: user-directed — chosen over inline chips with an inline passage and over always-visible quotes: Matt asked for much smaller numbers and a bottom row.)
- KTD4. The look is Paper, with the desktop sidebar a slightly darker warm beige and collapsible. (session-settled: user-directed — chosen over the Graphite and Clear directions.)
- KTD5. Gaps use a dark green icon plus a number, no word label, and open a "What's missing" sheet in plain words. (session-settled: user-directed — chosen over an icon with the word "gap": Matt chose icon and number only.)
- KTD6. Thumbs-down is the only feedback: a note box and Send, no reason chips, no thumbs-up. (session-settled: user-directed — matches what the app already stores.)
- KTD7. Three releases: look and navigation, then Add and answers, then accessibility and polish. (session-settled: user-directed — chosen over one big release and over Add first.)
- KTD8. The question box is pinned to the bottom on phone and desktop. (session-settled: user-directed.)
- KTD9. No dark mode for now. (session-settled: user-directed.)
- KTD10. Stay with plain HTML, CSS and JavaScript, no framework or build step. The app is about 900 lines, the CSP forbids inline code, and the existing no-`innerHTML` rule is enforced by tests.
- KTD11. Self-host Newsreader and Geist as variable woff2 files under `app/web/fonts/`, Latin subset, and add them to the service worker's shell list. External font hosts are blocked by the CSP and would leak requests to a third party.
- KTD12. Views are one page with an in-memory view state (`ask`, `add`); only sheets push a browser history entry, marked with a state value, and one `popstate` handler closes the open sheet. Switching tab closes any open sheet first and pops its entry. Back from the Add tab is not guaranteed to return to the previous answer. The share-target redirect to `/?share=1` is a fresh load with no sheet open, so it is unaffected; a 401 closes any open sheet. `popstate` is the only closer: a programmatic close calls `history.back()` and the handler does the teardown, and any follow-up action (tab switch, `openWith`, opening another sheet) runs after the handler completes.
- KTD13. One sheet component serves Source, Gaps and Feedback and is built in U2. On phone it is a bottom sheet; at 900 pixels and wider Source and Gaps become a full-height panel from the right edge and Feedback is an inline panel under the answer. History is not part of the sheet: it lives in the sidebar. One Escape handler, owned by `app.js`, closes the topmost sheet.
- KTD14. Gap sentences are cleaned in the browser: strip a leading `Unresolved need N<k>:` or `N<k>:` and show the sentence alone. The agent's own run-problem messages from `stopped()` in `app/agent/harness.py` ("Hybrid retrieval failed.", "Document find failed.", "Document find provenance failed.", "Original source read failed.", "Model stopped: <reason>." matched by prefix, "Investigation budget ended before a valid grounded answer.") are not knowledge gaps: each shows as one plain line under the answer, is not counted on the gap icon and has no Add button. Only the budget message reaches a done-with-gaps turn; the others arrive on failed turns, which already show their own error. Every other gap string is a knowledge gap and is listed. There is no "Looked for" line: after the prefix is stripped it would repeat the gap text.

### Assumptions

- The Paper boards in the canvas are the pixel reference for spacing, radii and type sizes; where they disagree with the principles in this plan, the canvas wins and the executor notes it.
- The desktop right-hand panel is 400 pixels wide and the sidebar 248 (56 collapsed); the panel pushes the page at 1200 pixels and wider and overlays it between 900 and 1200.

### Release A decisions (made during the build)

- Colours are the canvas's. Measured ratios: ink on ground 15.5, muted `#6B675F` on ground 5.1 and on white 5.6, sidebar text `#4E4B45` on the sidebar `#EDE9E0` 7.2, accent on ground 6.8, white on accent 7.5, danger on ground 6.9. Muted text is not used on the sidebar.
- The sheet closes through one browser Back per sheet; a repeat trigger before the Back finishes is ignored, and a reload on a leftover sheet entry clears it.
- The service worker now fetches its shell files with `cache: "reload"` on install so an update never mixes old and new files from the browser's HTTP cache.

### Matched to the design canvas (Release A)

*Superseded in part on 2026-10-20: the phone no longer has a bottom tab bar or top bar title, History is not a popover, and sizes and fonts changed (next sections). The canvas boards remain the reference for colours, radii and layout feel.*

- The first Release A build followed this plan's wording and did not match the canvas; it was rebuilt from the "A Paper" boards' own values. Where the canvas and this plan differ, the canvas won: the phone top bar shows the word "Knowledge" and a History button, and the question itself is the page heading; the desktop sidebar (`#EDE9E0`, 232 px, 68 px collapsed) holds Ask, Add and History as three items, and History opens a panel beside it rather than a list always shown (R2); New question and sign-out live in that History panel; muted text is the canvas's `#6B675F` and sidebar text `#4E4B45`.
- Built in Release B (U5): the canvas's Add box with the detected-type row and folded Title and tags (its Geist Mono was dropped later, see below).
- Newsreader keeps its optical-size axis so large headings match the canvas: the font pair is now 162 KB (OQ2's 150 KB guide is exceeded on purpose; italics stay dropped).

### Known gaps carried out of the Release A review

- Fixed in Release A: sign-out used to say "signed out" even when the request failed; it now waits for the server and shows "Could not sign out" otherwise.
- Fixed in Release B: the daily-limit banner and the "answer still running" notice now sit above the page and show on both tabs.
- Kept on purpose: the Android share target stores the shared item in the phone's browser storage until the app is opened and signed in, then deletes it (see `takeShared` in `capture.js`), so a share made while signed out is not lost. If the app is never signed in again, the item stays in that browser storage.

### Release B decisions (made during the build)

- Built from the "A Paper" boards' source plus the gaps and feedback mock. Where they differ from each other or from this plan: the gap icon is the mock's soft green chip with the accent text (the Ask board draws it amber; KTD5 and R11 say green, so green wins); the board's "Looked for:" line is not built (KTD14: nothing to show after the code is stripped, and the receipt has no such field); the Add board's "(filled in for you)" is "(optional)" because the app does not fill title and tags itself; the detected-type subtitle only says what is true ("Saved to Transcripts" for YouTube, otherwise "The app files it in the right folder").
- Type: two typefaces, Newsreader and Geist (Matt chose this over keeping Geist Mono for the Add box, 2026-10-20), on a six-step size scale: 12 labels, 14 controls and lists, 16 inputs, 18 answer text, 22 question titles, 26 page titles. Button text is never bold. The service worker cache is `ka-shell-v6`.
- Source numbers in the row are drawn 24 px; their touch area is larger than what is drawn (about 44 px tall on touch screens).
- The spending-cap and "answer still running" messages moved out of the Ask tab into one banner above the page, so they show on both tabs; the cap message stays, the other fades after 8 seconds.
- Thumbs-down opens the bottom sheet on phones and the board's inline panel under the answer at 900 px and wider. On desktop the Source and Gaps panels sit on the right, push the page at 1200 px and wider, overlay it below that, and swap in place when another number or the gap icon is tapped.

### Changes agreed after Release B review (2026-10-20)

Matt reviewed the Release B build and asked for these; they override the earlier wording where they differ.

- **One sidebar for navigation.** Phones get a menu icon at the top left that opens the sidebar as a drawer (scrim, Escape and a choice close it); the bottom tab bar and the History sheet are gone. History's chats list sits under the History item. The foot of the sidebar has the account circle (opens sign out and sign out everywhere) on the left and a "New question" pill on the right.
- **Right-edge panel on desktop.** Source and Gaps open as a full-height panel from the right edge; clicking outside closes it. Feedback is inline under the answer on desktop and a bottom sheet on phones.
- **Gaps look** follows Matt's screenshot: green "N gaps in this answer" line, "What's missing", entries between hairlines, a plain "+ Add a note to fill this gap" link under each. The Obsidian link is the words "See in Obsidian", underlined, beside the folder path.
- **Compact controls.** 32 px with a mouse (44 px on touch), smaller headings, a 48 px question bar; no thick or coloured outlines on text boxes; button text is never bold.
- **Type and icons.** Two typefaces and six sizes (12, 14, 16, 18, 22, 26); Geist Mono dropped (the Add box uses Geist); Phosphor Light icons with a meh face for feedback and the YouTube logo for video. The service worker cache is `ka-shell-v6`.
- **Empty conversation:** the heading and the question bar sit together in the middle of the page; the bar drops to the bottom once a question is asked. The Add column is 480 px wide and centred on desktop.

### Release C (open, to be re-scoped by Matt)

U10 below was written before the design review; much of its polish was folded into Release B. What is genuinely left, in the order Matt may want it:

1. Real-device checks Matt has not yet done: installed Android app (drawer, safe areas, share target), landscape phone, 200 percent text zoom, reduced motion.
2. The accessibility checklist in U10 (contrast pairs, tab and focus order in the drawer, account menu and panel, screen-reader announcements).
3. Small hardening items found in review and left on purpose: polling loops retry forever on a non-sign-in error, the sheet closes only when the browser's Back event fires (no fallback), `afterClose` callbacks can queue twice on a double tap.
4. More JavaScript runner coverage: gap clean-up already runs under node in `tests/api/test_static.py`; source numbering and add-box classification still need it.

Not in Release C: dark mode and the suggested-tags page (separate work). If Matt's device checks find nothing, Release C can shrink to items 2 and 4 or be dropped.

### Open Questions (deferred)

- OQ1. Exact starter text when "Add a note to fill this gap" opens Add; default is the cleaned gap sentence on the first line so Matt can answer it.
- OQ2. Final font file sizes after subsetting; if the pair exceeds about 150 KB total, drop Newsreader italics. Resolved in Release A: Latin subset, Newsreader with its optical-size axis (weights 200-800, no italics), 162 KB for the pair.
- OQ3. How History, New question and the account menu appear when the desktop sidebar is collapsed; default is one icon each, History opening the conversation list as a popover and the account icon at the sidebar's foot opening a menu. Superseded 2026-10-20: no History popover; History expands in place in the sidebar (clicking it on a collapsed sidebar expands the sidebar), and the account circle opens its own sign-out menu.

### Existing States That Must Survive

| State | Where it lives today | Covered by |
|---|---|---|
| Email, code entry, resend, auth errors, 429 | `index.html` signin, `app.js` | U3 |
| Draft question restored after sign-out; conversation restored | `app.js` `DRAFT_KEY`, `CONVERSATION_KEY` | U2 |
| Empty conversation list; new question | `app.js` | U2, U9 |
| Answering timer; failed answer with Retry; done with gaps | `renderTurn` | U6, U7 |
| Daily spend-cap banner, input disabled | `showCap` | U9 |
| Capture disabled message | `capture.js` | U5 |
| Capture running, indexed, queued, failed with Retry; polling | `capture.js` | U5 |
| Drop, paste, file picker, `.md` text into the box; screenshot downscale and preview | `capture.js` | U5 |
| Android share target and the cached share item | `sw.js`, `capture.js` `takeShared` | U4, U5 |
| Shell cache version and offline fallback | `sw.js` | U4 |
| Obsidian deep links, `obsidian://` check | `toggleExcerpt` (`app.js`), `obsidianLink` (`capture.js`, Recent list) | U6, U5 |
| Sign out, sign out everywhere | sidebar footer | U2 |

---

## Implementation Units

### U1. Paper tokens, fonts and base styles

**Goal:** Replace the flat stylesheet with a token-based one that every later unit uses.

**Requirements:** R14, R15, R16, R17

**Dependencies:** none

**Files:** `app/web/styles.css`, `app/web/fonts/` (new woff2 files and their licence text), `app/web/index.html` (font preload), `tests/api/test_static.py`

**Approach:**
- Define colour tokens from the Paper palette (ground `#F6F4EF`, ink `#1D1C1A`, muted `#6B675F`, chip `#ECE8DF`, accent `#2F5D50`, sidebar a slightly darker warm beige) and a spacing scale of 4, 8, 12, 16, 24, 32, 48 pixels, one type scale, and radius tokens.
- Declare `@font-face` for the two families with `font-display: swap`, served from `/static/fonts/`.
- Base rules for buttons, inputs, focus ring (accent, 3 pixels), `[hidden]`, and the reduced-motion media query.
- Keep selectors flat and class-based, in the order tokens, base, layout, components, so later units do not fight specificity.

**Patterns to follow:** the current `styles.css` conventions (`[hidden]` override, `sr-only`, 44-pixel targets).

**Test scenarios:**
- Static: `styles.css` declares both families and references only `/static/fonts/` URLs, never an `http` URL.
- Static: the CSS contains no `@import` of a remote stylesheet.
- Manual: muted ink, accent and chip text on the Paper ground each reach 4.5:1 (compute the ratios and adjust the muted token if one fails) before Release A is accepted.
- Covers the CSP: a test requests `/static/fonts/<file>` through the static route test client and expects status 200 (the content type depends on the platform's MIME table, so it is not asserted).

**Verification:** the sign-in screen and thread render in the new fonts and colours with no console CSP errors.

### U2. App shell and navigation

**Goal:** Build the two-tab structure, History, the desktop sidebar and the pinned question box.

**Requirements:** R1, R2, R3, R4, R5, R17

**Dependencies:** U1

**Files:** `app/web/index.html`, `app/web/styles.css`, `app/web/app.js`, `app/web/capture.js`, `tests/api/test_static.py`

**Approach:**
- Build the shared sheet first (KTD12, KTD13): one component with focus trap, Escape, focus return, scrim, the history-entry marker and the `popstate` handler. History is its first user; U6-U8 reuse it.
- Replace the top bar with: a title row (the Ask title is the conversation title; the Add title is "Add to notes") and one History button on phone.
- Phone: (superseded 2026-10-20) a menu icon at the top left opens the sidebar as a drawer; see "Changes agreed after Release B review".
- Desktop (900 pixels and wider): sidebar with the two tabs, History list, New question and an account menu; a button collapses it to icons and the state is stored under a new `localStorage` key, wrapped in try/catch as the existing helpers are.
- The question box stays in the Ask tab's last grid row so it is pinned on both layouts.
- View state `ask`/`add` replaces the `capturing` body class; `newQuestion()` and `openConversation()` switch to the Ask tab. The Add tab shows the existing capture form, restyled, until U5 replaces it; the old `#add`, `#sidebar-add` and `#capture-close` bindings and the `closeCapture()` calls are removed here, and `capture.js` exposes `show()`, `hide()` and, for U7, `openWith(text)`, so Release A never throws at start-up. The Escape handler in `capture.js` is removed here too, so `app.js` owns Escape from Release A. `takeShared` switches to the Add tab through the view state before filling the existing form and still deletes the cached share item in `finally`.
- Keep draft and conversation restore exactly as today. Sign-out, sign-out everywhere and a 401 clear the Add box, attachment and preview and empty any open sheet; only the question draft that is restored on purpose survives.
- In the collapsed desktop sidebar, History expands the sidebar; the account circle and the New question button stay as icons at its foot.

**Technical design:** view state is one variable; each tab owns one container shown with `hidden`, so tab switches keep the other tab's contents (a half-typed question or note survives).

**Patterns to follow:** current `openSidebar`/`closeSidebar` scrim handling, `store`/`load` helpers.

**Test scenarios:**
- Static: `index.html` has no inline `<script>`, no `onclick=` and no `style=` attribute.
- Static: `app.js` and `capture.js` contain none of the banned HTML-insertion calls and no `setAttribute("style"` or `cssText`.
- Manual (phone): tab switch keeps a typed question; History opens and closes with the scrim, Escape and Android back; selecting a conversation switches to Ask; opening History and then signing out leaves no stale back entry; open a sheet, switch tab, press Back, and open the gap sheet then Back at once, each ending in the right place.
- Manual (Android): sharing a link from another app opens the Add tab with the link filled in.
- Manual (desktop): the sidebar collapses to icons and stays collapsed after reload; the question box stays at the bottom with a long thread.

**Verification:** every navigation path in the "Existing States" table row for conversations works on both layouts.

### U3. Sign-in screen

**Goal:** Restyle email and code entry in Paper without changing its behaviour.

**Requirements:** R13, R14, R16

**Dependencies:** U1

**Files:** `app/web/index.html`, `app/web/styles.css`

**Approach:** Same two forms and messages as `sendCode`/`verifyCode`; centre one column, serif title, one primary button, `autocomplete` attributes unchanged. Error text uses the danger token and `role="alert"`.

**Test scenarios:**
- Manual: wrong code, expired code, 429, 403 and 502 messages still appear unchanged.
- Manual: the one-time-code field offers the SMS/email autofill on Android.

**Verification:** a full sign-in on phone and desktop.

### U4. Service worker, manifest and shell list

**Goal:** Make the new shell and fonts available offline and keep the share target working.

**Requirements:** R8, R17

**Dependencies:** U1, U2

**Files:** `app/web/sw.js`, `app/web/manifest.webmanifest`, `app/web/index.html`, `tests/api/test_static.py`

**Approach:** Bump `CACHE` to `ka-shell-v3`, add the font files and any new script or stylesheet to `SHELL`, and set `background_color` and `theme_color` (and the page's `theme-color` meta) to the Paper ground. Leave the share handler untouched.

**Test scenarios:**
- Static: every `/static/...` path linked from `index.html`, and every `url(/static/...)` in `styles.css`, appears in `SHELL` (one missing font would fail the whole worker install).
- Static: the manifest still declares the `/share` POST target with the same file types.
- Manual: install through Chrome's "Install app", turn off the network, and confirm the shell opens.

**Verification:** an installed app picks up the new version without a manual cache clear (network-first fetch already handles this).

### U5. Add tab: one smart box

**Goal:** Turn the capture overlay into the Add tab with one box.

**Requirements:** R6, R7, R8, R13, R16, R17

**Dependencies:** U2

**Files:** `app/web/capture.js`, `app/web/index.html`, `app/web/styles.css`, `tests/api/test_static.py`

**Approach:**
- One textarea with an Attach button (a visible button that triggers the hidden file input) and a Save button; drop and paste keep working on the whole tab.
- A detection chip updates as the content changes, reusing the existing rules: YouTube pattern on a single-token link, plain web link, `.md`/`.markdown` file, `.txt`, image, otherwise text. A screenshot shows its preview exactly as it will be saved.
- Title and tags sit in a closed `<details>`; the request body is built as today.
- Recent saves list under the box with the existing status strings and Retry; `STATUS` wording is kept unless the canvas copy differs.
- `takeShared` opens the Add tab, then fills the box as it does now, and still deletes the cached share item in `finally`.
- Keep the `show()`, `hide()` and `openWith(text)` surface from U2; `openWith` replaces the box contents and focuses it.

**Patterns to follow:** `requestBody`, `takeFile`, `pollCapture`, `renderCapture`.

**Test scenarios:**
- Covers detection: pasting `https://youtu.be/<id>` shows "YouTube video"; a normal link shows "Web link"; two words show "Text"; a `.md` file shows "Markdown file" and a `.txt` file shows "Text file", each with its text in the box; a PNG shows "Screenshot" and a preview.
- Error path: an unsupported file type shows the existing message and saves nothing.
- Edge: Save is disabled when the box is empty and no file is attached.
- Integration (manual): share a YouTube link from Android; the Add tab opens with the link filled in and the share cache is empty afterwards.
- Static: `capture.js` contains none of the banned HTML-insertion calls.

**Verification:** save one item of each kind and watch it move through Recent to "Saved and searchable".

### U6. Answer rendering and the source sheet

**Goal:** Show sources as footnote numbers plus a bottom row, with the passage in a sheet.

**Requirements:** R9, R10, R13, R16, R5

**Dependencies:** U2

**Files:** `app/web/app.js`, `app/web/index.html` (turn template, sheet container), `app/web/styles.css`, `tests/api/test_static.py`

**Approach:**
- `renderAnswer` keeps the per-file numbering and emits a `sup` button per citation; an answer row under the answer lists each number once, then the gap icon (U7), then the thumbs-down (U8). The row renders whenever there are sources, gaps or a feedback control, so an answer with no citations still has its feedback button.
- Turn states keep their meaning: Answering with seconds, failed with Retry, done. The cap banner sits above the pinned box.
- The sheet from U2 shows the note title and folder path once, then each passage cited from that note in answer order in the serif, and one icon button with an accessible name "Open in Obsidian"; the link is shown only when the URL starts with `obsidian://`.
- Opening a sheet pushes one history entry; Back or Escape closes it and returns focus to the number that opened it.
- Inline marks keep a small hit area (R16 exempts them); the 44-pixel target is met by the bottom row of numbers.

**Technical design:** `openSheet(kind, payload)` builds the sheet body with `textContent` and elements only; `closeSheet()` pops the history entry once.

**Patterns to follow:** current `renderAnswer`, `toggleExcerpt`, `announce`.

**Test scenarios:**
- Covers numbering: two passages from the same file share one number; passages from two files get two numbers.
- Edge: a number cited three times opens one sheet listing three passages under one title.
- Edge: an answer with no citations shows no numbers and no source sheet trigger, but still shows its feedback button.
- Error path: a failed turn shows its message and Retry, and the question is re-asked.
- Static: `app.js` keeps the `url.startsWith("obsidian://")` check the existing test asserts.
- Manual: Back closes the sheet before leaving the conversation; focus returns to the number.

**Verification:** compare one real answer on phone and desktop against the canvas Ask, Source and Desktop boards.

### U7. Gaps icon and the "What's missing" sheet

**Goal:** Make gaps visible and actionable.

**Requirements:** R11, R13

**Dependencies:** U5, U6

**Files:** `app/web/app.js`, `app/web/styles.css`, `tests/api/test_static.py`

**Approach:**
- When `receipt.gaps` is non-empty, add a dark green icon button with the gap count after the source numbers; remove the old grey list under the answer.
- Count and list only knowledge gaps (KTD14), as sentences after the clean-up; a run-problem message shows as a plain line under the answer instead.
- "Add a note to fill this gap" closes the sheet (popping its history entry), switches to the Add tab and calls `captureScreen.openWith` with the gap sentence (OQ1).
- The turn-level "Answered with gaps" wording is dropped from the header line if the icon makes it redundant; keep it in the accessible text.

**Test scenarios:**
- Happy path: gaps `["Unresolved need N2: Find a named person's advice on hiring"]` render as "Find a named person's advice on hiring" with no `N2`.
- Happy path: the model's own sentence renders unchanged.
- Happy path: the investigation-budget message shows as a plain line under the answer, adds nothing to the gap count and has no Add button.
- Edge: a gaps list holding only run problems shows no gap icon.
- Edge: three gaps show count 3 and three entries; an empty `gaps` list shows no icon.
- Edge: a gap that is only a code prefix and no sentence is skipped rather than shown empty.
- Integration (manual): the button opens Add with the starter text and the box focused and no sheet left open; Back from Add does not reopen the sheet.

**Verification:** an answer that came back with gaps shows the icon and the sheet matches the canvas Gaps board.

### U8. Thumbs-down note sheet

**Goal:** Replace the thumbs toggle and inline form with the agreed note sheet.

**Requirements:** R12, R13

**Dependencies:** U6

**Files:** `app/web/app.js`, `app/web/index.html`, `app/web/styles.css`, `tests/api/test_static.py`

**Approach:**
- A thumbs-down icon button (accessible name "This answer was not good") sits at the end of the answer row on done and done-with-gaps turns.
- Tapping opens the note sheet (phone) or an inline panel under the answer (desktop). Send does one `POST` with the note; the icon shows its filled state.
- When feedback already exists, the sheet shows the saved note and a Remove link that calls `DELETE`.
- Failures use `announce`; a 401 shows sign-in as everywhere else.

**Test scenarios:**
- Happy path: Send with a note stores it; reopening shows the note; the icon is filled after reload (`turn.feedback` is present).
- Edge: Send with an empty note still stores a `down` verdict.
- Error path: a failed request keeps the sheet open and shows the error.
- Remove: the icon returns to its outline and the stored feedback is gone.

**Verification:** one thumbs-down saved and removed on phone and desktop.

### U9. Empty state, new-question state and thread polish

**Goal:** Finish the remaining Ask-tab states in the new look.

**Requirements:** R13, R4

**Dependencies:** U2, U6

**Files:** `app/web/index.html`, `app/web/styles.css`, `app/web/app.js`

**Approach:** Restyle the empty state ("Ask your notes anything."), the Answering line, the spend-cap banner and the composer disabled state; keep the multiline autosize and the desktop Enter-to-send rule. Question titles use the serif; spacing comes from the scale.

**Test scenarios:**
- Manual: new question shows the empty state; the cap banner appears when a 429 is returned and the box is disabled; Shift+Enter makes a new line on desktop.

**Verification:** walk the "Existing States" table end to end.

### U10. Accessibility and polish pass

**Goal:** Check and fix the whole app against the quality requirement.

**Requirements:** R16, R17

**Dependencies:** U1-U9

**Files:** `app/web/styles.css`, `app/web/app.js`, `app/web/index.html`, `tests/api/test_static.py`

**Approach:** Check each text and control colour pair against WCAG AA (the muted ink on the Paper ground is the pair most at risk), tab order and focus order in every sheet, focus trap and return, `aria-live` announcements for answer-ready and failures, 200 percent text zoom, landscape phone, reduced-motion, and the installed Android app's safe areas. Fix what fails.

**Test scenarios:**
- Static: no `style=` attributes in `index.html`; every icon-only button has an accessible name.
- Manual: tab through Ask, Add, a source sheet, the gap sheet and the feedback sheet using only the keyboard; Escape closes each and returns focus.
- Manual: a screen reader announces the answer when ready and names the tabs and the active one.

**Verification:** a recorded pass of the checklist with each failure fixed or listed for Matt.

---

## Verification Contract

- **Automated:** `python -m unittest discover -s tests/api -p 'test_static.py'` after every unit that changes static files; the full `tests/api` suite before each release PR (database tests need a disposable `KNOWLEDGE_TEST_DATABASE_URL`).
- **Manual, by Matt, on the release branch before it merges:** desktop browser first, then the installed Android app once the branch is deployed (the hosted app deploys from `main`, see `deploy/README.md`, so an Android-only failure found after merge is fixed by reverting the PR). Release A covers sign-in, tabs, History, sidebar collapse, pinned box, offline open, and sharing a link from Android into Add. Release B adds one save of each Add kind, a shared Android item, a source sheet, a gap sheet (use a question that returns gaps), and one thumbs-down saved and removed. Release C adds the accessibility checklist. There is no JavaScript test runner, so every test scenario not marked Static is a manual check; adding a runner for the pure helpers is deferred.
- **Release gate:** Matt sees no console CSP errors; the share target still opens Add.

## Definition of Done

- All requirements R1-R17 are met on phone and desktop and Matt has accepted each release.
- Every row of "Existing States That Must Survive" has been exercised once.
- No server, schema or prompt file changed.
- Abandoned experiments, unused CSS and unused font files are removed from the diff.
- `FOUNDATION.md#current-progress` and the relevant system doc are updated through `$finish-task`, and the "redesign the web app" item is closed.
