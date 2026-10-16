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
| `SUPABASE_URL` | `https://<project-ref>.supabase.co` |
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
