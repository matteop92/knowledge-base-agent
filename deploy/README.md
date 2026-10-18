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

- `deploy/run.sh` starts `ob sync --continuous` (Obsidian Headless, pinned in the `Dockerfile`) next to the web server when
  `KNOWLEDGE_SYNC_ENABLED=true`, and restarts it if it stops.
- `app/api/scheduler.py` runs the indexer (`app/ingestion/vault_sync.py`) every `KNOWLEDGE_INDEX_INTERVAL_SECONDS` (default 300).
  It waits until the first sync has produced notes, never overlaps two passes, records the last result for `/health`
  (counts and error types only), and **retires nothing on the first pass after a start** so a half-downloaded vault cannot
  look like deleted notes. The indexer's own guard still refuses to retire more than 10% or 20 files at once.
- The vault copy and Obsidian's sign-in live on the Railway volume at `/data` (`/data/vault`, `/data/home`). Run snapshots
  live on the container's disk and are cleared on every restart.
- The setup below uses `--mode pull-only`, so the server can read notes but never change them. Phase 2 captures need
  two-way sync: switch with `ob sync-config --path /data/vault --mode bidirectional` in a server shell. The sign-in and the
  volume stay as they are.

Variables (all optional except the switch):

| Variable | Default | Meaning |
| --- | --- | --- |
| `KNOWLEDGE_SYNC_ENABLED` | `false` | Turn on the sync process and the scheduler. Set to `true` only after the one-time setup below. |
| `KNOWLEDGE_SOURCE_ROOT` | `/data/vault` | Where the vault copy lives. |
| `KNOWLEDGE_INCLUDED_FOLDERS` | `Knowledge` | Top-level vault folders to index. |
| `KNOWLEDGE_INDEX_INTERVAL_SECONDS` | `300` | Time between index passes. |
| `KNOWLEDGE_INDEX_MAX_USD_PER_PASS` | `0.10` | Spend limit for one pass; the daily cap still applies. |

### One-time setup

1. In Railway, open the service, then **Settings → Volumes → Add Volume**, mount path `/data`. Redeploy.
2. Open a shell on the server: install the Railway CLI (`npm install -g @railway/cli`), then `railway login`, `railway link`
   (pick this project and service) and `railway ssh`.
3. In that shell run `sh deploy/ob-setup.sh`. It asks for your Obsidian email, password (and a two-factor code), the vault name
   and the vault's encryption password if it has one. It skips every top-level folder except `Knowledge` (the list is in
   `deploy/excluded-folders.txt`; add any folder you create later), syncs download-only, and prints how many notes arrived.
4. In Railway Variables set `KNOWLEDGE_SYNC_ENABLED` to `true`. Railway redeploys.
5. Check: sign in to the app, open `/health` and look at `index`: `status` should read `ok`, and `last_result` shows
   `files_seen` equal to the number of `Knowledge` notes.

Known limits: Obsidian's tool also syncs notes that sit directly in the vault root (not in a folder), because only folders
can be excluded. They are stored on the volume but never indexed. A new top-level folder syncs to the volume until it is
added to `deploy/excluded-folders.txt` and to `ob sync-config --excluded-folders`.
