#!/bin/sh
# One-time sign-in and setup for Obsidian Sync on the server. Run it in a shell on the server:
#   sh deploy/ob-setup.sh
# Passwords are typed at the prompts here; they are never passed as arguments or stored in this repo.
set -eu

VAULT="${KNOWLEDGE_SOURCE_ROOT:-/data/vault}"
export HOME="${KNOWLEDGE_HOME:-/data/home}"
mkdir -p "$HOME" "$VAULT"
chown -R agent:agent /data 2>/dev/null || true

# Run as the app user so the sign-in files are owned by the account the app runs as.
as_agent() { if [ "$(id -u)" = "0" ]; then runuser -u agent -- env HOME="$HOME" "$@"; else "$@"; fi; }

echo "Step 1 of 4: sign in to Obsidian (email, password, and a code if you use two-factor sign-in)"
as_agent ob login

echo
echo "Step 2 of 4: your synced vaults"
as_agent ob sync-list-remote
printf "Type the name of the vault to sync (for example: New Vault): "
read -r VAULT_NAME

echo
echo "Step 3 of 4: connect the vault (type the vault encryption password if it has one)"
as_agent ob sync-setup --vault "$VAULT_NAME" --path "$VAULT" --device-name kb-server

echo
echo "Step 4 of 4: settings (download only; skip every folder except Knowledge; notes only: no plugins, settings, images, audio, PDFs or video)"
# The folder names are typed here and kept only on the volume, so none of them are in the repository.
EXTRA="${KNOWLEDGE_EXCLUDED_FILE:-/data/excluded-folders-extra.txt}"
echo "Type or paste the names of your other top-level vault folders, one per line, then an empty line."
echo "(Anything missed is removed from the server within a minute anyway.)"
: > "$EXTRA"
while IFS= read -r line; do
  [ -z "$line" ] && break
  printf '%s
' "$line" >> "$EXTRA"
done
chown agent:agent "$EXTRA" 2>/dev/null || true
EXCLUDED="$(paste -sd, "$EXTRA")"
as_agent ob sync-config --path "$VAULT" --mode pull-only --conflict-strategy conflict \
  --excluded-folders "$EXCLUDED"

# The tool's command line cannot turn attachments (images, audio, PDFs, video) off, but its settings file can:
# an empty list of allowed attachment types means notes only.
as_agent python deploy/notes_only.py "$HOME/.config/obsidian-headless/sync"

echo
echo "The sync tool's settings (the mode must say pull-only):"
as_agent ob sync-config --path "$VAULT"

echo
echo "Will the cleanup delete anything? (it must say it will delete outside the allowed folders)"
as_agent python -m app.ingestion.vault_allowlist --root "$VAULT" --check

echo
echo "First download (this can take a minute)..."
as_agent ob sync --path "$VAULT"

echo
echo "Done. Notes now in the Knowledge folder:"
find "$VAULT/Knowledge" -name '*.md' | wc -l
echo "Files that are not notes (this should be 0):"
find "$VAULT/Knowledge" -type f ! -name '*.md' | wc -l
