#!/bin/sh
# Every minute: remove anything outside the allowed folders from the server's copy of the vault, then download changes.
# One download per cycle (not a continuous watcher), so the settings are re-read each time and nothing runs
# while files are being removed.
set -u
VAULT="${KNOWLEDGE_SOURCE_ROOT:-/data/vault}"
INTERVAL="${KNOWLEDGE_SYNC_INTERVAL_SECONDS:-60}"
while true; do
  python -m app.ingestion.vault_allowlist --root "$VAULT" || echo "allowlist check failed; the next cycle retries" >&2
  ob sync --path "$VAULT" || echo "obsidian sync failed; the next cycle retries" >&2
  sleep "$INTERVAL"
done
