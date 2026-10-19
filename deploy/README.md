# Deploy

One container runs the API and web app. It talks to Supabase only through Postgres as the
restricted runtime login. Secrets live in the host's secret store, never in the image or repo.

## Railway setup

1. Create a Railway project from this GitHub repository; Railway builds the `Dockerfile`.
2. Add the variables below under **Variables**.
3. Under **Settings → Networking**, generate a domain. Set the health check path to `/healthz`.
4. In Supabase **Authentication → URL Configuration**, set the Site URL to that domain and remove other redirect URLs.
5. In Supabase **Authentication → Emails → Magic Link**, include `{{ .Token }}` in the template so the email carries the six-digit code.

| Variable | Value |
| --- | --- |
| `KNOWLEDGE_DATABASE_URL` | Runtime login connection string ending in `?sslmode=verify-full&sslrootcert=system` (use the Supavisor session pooler host if the direct IPv6 host is unreachable) |
| `OPENROUTER_API_KEY` | OpenRouter key |
| `SUPABASE_URL` | `https://ielkvsfcnwtaljnemxua.supabase.co` |
| `SUPABASE_PUBLISHABLE_KEY` | The project's publishable (anon) key |
| `SUPABASE_JWT_SECRET` | Only if the project still uses the legacy shared JWT secret |
| `KNOWLEDGE_ALLOWED_USER_ID` | Your Supabase Auth user id |
| `KNOWLEDGE_ALLOWED_EMAIL` | Your sign-in email |
| `KNOWLEDGE_VAULT_NAME` | The Obsidian vault name used in `obsidian://` links (default `New Vault`) |
| `KNOWLEDGE_DAILY_CAP_USD` | Daily cap on all model spend (default `2`) |

## Check after each deploy

- `https://<domain>/healthz` returns `{"status": "ok"}`, including after a restart.
- Sign in on the phone, ask a question, open a citation.

## Roll back

In Railway **Deployments**, choose the last good deployment and **Redeploy**. Database
migrations deploy separately through the Supabase GitHub integration and are not rolled back
by this; a bad migration needs a new forward migration.

## Vault sync and indexing (Phase 1b)

The server keeps a read-only copy of the vault's `Knowledge` folder through Obsidian Sync and indexes new,
edited and deleted notes every five minutes.

How it fits together:

- `deploy/run.sh` starts `deploy/sync-loop.sh` beside the web server when `KNOWLEDGE_SYNC_ENABLED=true`. Every minute the loop
  (1) removes anything outside the allowed folders from the server's copy, then (2) runs one `ob sync` (Obsidian Headless,
  pinned in the `Dockerfile`). One download per cycle means the settings are re-read each time and nothing runs while files
  are being removed.
- **Only the allowed folders stay on the server.** Obsidian's tool can skip folders by name but cannot say "only this one", so
  a folder you create later in the vault would be copied. `app/ingestion/vault_allowlist.py` removes every folder and loose
  note outside `KNOWLEDGE_INCLUDED_FOLDERS` from the server's copy and adds the folder to the tool's skip list
  (remembered in `/data/excluded-folders-extra.txt`, which also holds the names pasted at setup). A new folder can therefore sit on the server for up to a minute.
  Entries starting with a dot are left alone.
- **What the cleanup may delete depends on the sync mode.** While download-only (`KNOWLEDGE_SYNC_MODE` is `pull-only`, the
  default, **and** `ob sync-config` itself reports `pull-only`) it deletes everything outside the allowed folders. In two-way
  mode (`bidirectional` in both places) a delete on the server could reach the real vault, so it first adds new folders to
  the skip list and then deletes only folders that the tool's own settings file (`ignoreFolders` in
  `$HOME/.config/obsidian-headless/sync/*/config.json`) lists as skipped; loose notes and links stay. Any mismatch or
  unreadable setting deletes nothing and only updates the skip list.
- `app/api/scheduler.py` runs the indexer (`app/ingestion/vault_sync.py`) every `KNOWLEDGE_INDEX_INTERVAL_SECONDS` (default 300).
  It waits until the first sync has produced notes, never overlaps two passes, records the last result for `/health`
  (counts and error types only), and **retires nothing on the first pass after a start** so a half-downloaded vault cannot
  look like deleted notes. The indexer's own guard still refuses to retire more than 10% or 20 files at once. The indexer
  reads only `Knowledge`, so it is unaffected by the cleanup.
- The vault copy and Obsidian's sign-in live on the Railway volume at `/data` (`/data/vault`, `/data/home`). Run snapshots
  live on the container's disk and are cleared on every restart.
- **Phase 2 (captures need two-way sync):** switch with `deploy/ob-two-way.sh`, never by flipping the existing copy.
  While download-only, the cleanup deleted loose root notes and not-yet-skipped folders from the server's copy, and the
  sync tool may remember those deletions; in two-way mode it could send them to the real vault. The script therefore
  keeps the current copy as a backup, connects a new empty copy with the skip list and "notes only" set before anything
  downloads, downloads everything while still download-only, checks every `Knowledge` note against the old copy, then
  switches to two-way and requires the first two-way sync to change nothing (otherwise it goes back to download-only).
  Steps: set `KNOWLEDGE_SYNC_ENABLED` to `false` in Railway and wait for the redeploy; in a server shell
  (`railway ssh`) run `sh deploy/ob-two-way.sh` and type the vault password when asked; then set
  `KNOWLEDGE_SYNC_MODE` to `bidirectional` and `KNOWLEDGE_SYNC_ENABLED` to `true`. From then on the cleanup deletes only
  folders the tool's own skip list names exactly. Keep the `/data/vault-pullonly-*` backup until a captured note has
  reached the devices. If a run stops after the download check (steps 1 to 4 done), `sh deploy/ob-two-way.sh finish`
  redoes the check and runs only steps 5 and 6, without downloading again or asking for the password.
  **Tested 2026-10-18 on a throwaway folder in the real vault:** after a folder was added to the skip list in two-way mode,
  deleting its local copy and syncing three times did not delete it from the vault. (Tool version 0.0.14; re-check after
  upgrading it.)
  The `(conflict)` copies that `--conflict-strategy conflict` can create are skipped by the indexer
  (`CONFLICT_COPY` in `app/ingestion/vault_sync.py`); the server only adds new capture notes, so conflicts should be rare.

Variables (all optional except the switch):

| Variable | Default | Meaning |
| --- | --- | --- |
| `KNOWLEDGE_SYNC_ENABLED` | `false` | Turn on the sync loop and the scheduler. Set to `true` only after the one-time setup below. |
| `KNOWLEDGE_SYNC_MODE` | `pull-only` | `pull-only` or `bidirectional`; must match the sync tool's mode. Captures are refused unless it is `bidirectional`. |
| `KNOWLEDGE_CAPTURE_FOLDER` | `Knowledge` | Folder whose `Transcripts`, `Articles`, `Book Highlights` and `Notes` subfolders receive new capture notes (new files only). |
| `GEMINI_API_KEY` | none | Google AI Studio key for YouTube transcripts. Use a **free-tier** key (a project without billing): its use is not recorded in the spend ledger. Without it, YouTube captures fail with a clear message. |
| `KNOWLEDGE_VISION_MODEL` | `openai/gpt-6-luna` | Model that reads screenshots; must be in the pinned zero-retention routes in `app/models/routes.py`. |
| `KNOWLEDGE_YOUTUBE_MODEL` | `gemini-3.8-flash` | Gemini model for YouTube transcripts. |
| `KNOWLEDGE_SOURCE_ROOT` | `/data/vault` | Where the vault copy lives. |
| `KNOWLEDGE_INCLUDED_FOLDERS` | `Knowledge` | Top-level vault folders to index and keep on the server. |
| `KNOWLEDGE_INDEX_INTERVAL_SECONDS` | `300` | Time between index passes. |
| `KNOWLEDGE_SYNC_INTERVAL_SECONDS` | `60` | Time between download cycles. |
| `KNOWLEDGE_INDEX_MAX_USD_PER_PASS` | `0.10` | Spend limit for one pass; the daily cap still applies. |

### One-time setup

1. In Railway, open the service, then **Settings → Volumes → Add Volume**, mount path `/data`. Redeploy.
2. Open a shell on the server: install the Railway CLI (`npm install -g @railway/cli`), then `railway login`, `railway link`
   (pick this project and service) and `railway ssh`.
3. In that shell run `sh deploy/ob-setup.sh`. It asks for your Obsidian email, password (and a two-factor code), the vault name
   and the vault's encryption password if it has one. It then asks you to paste the names of your other top-level folders (kept
   only on the volume, never in the repository; later folders are handled automatically) so they are skipped from the first
   download. It syncs download-only and notes only (`deploy/notes_only.py` empties the tool's attachment list, which its command line cannot do), prints the tool's settings (check that it says `pull-only` and that cleanup "will delete"),
   how many notes arrived, and how many non-note files (should be 0).
4. In Railway Variables set `KNOWLEDGE_SYNC_ENABLED` to `true`. Railway redeploys.
5. Check: sign in to the app, open `/health` and look at `index`: `status` should read `ok`, and `last_result` shows
   `files_seen` equal to the number of `Knowledge` notes.
