#!/bin/sh
# Every minute: remove anything outside the allowed folders from the server's copy of the vault, then download changes.
# One download per cycle (not a continuous watcher), so the settings are re-read each time and nothing runs
# while files are being removed. If the cleanup fails, nothing is downloaded that cycle.
set -u
VAULT="${KNOWLEDGE_SOURCE_ROOT:-/data/vault}"
INTERVAL="${KNOWLEDGE_SYNC_INTERVAL_SECONDS:-60}"
MARKER="${KNOWLEDGE_SYNC_MARKER:-/data/last-sync}"
while true; do
  if python -m app.ingestion.vault_allowlist --root "$VAULT"; then
    # The sync tool gets only what it needs, not the app's database and model keys.
    if env -i PATH="$PATH" HOME="$HOME" timeout 300 ob sync --path "$VAULT"; then
      touch "$MARKER"
    else
      echo "obsidian sync failed; the next cycle retries" >&2
    fi
  else
    echo "allowlist check failed; skipping the download this cycle" >&2
  fi
  sleep "$INTERVAL"
done
