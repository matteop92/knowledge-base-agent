#!/bin/sh
# Railway sets PORT and ends TLS at its proxy. The app reads the proxy-appended address itself,
# so uvicorn must not rewrite the client address from headers the client can set.
set -eu

VAULT="${KNOWLEDGE_SOURCE_ROOT:-/data/vault}"
# Obsidian's sign-in and settings live under HOME, which sits on the volume so they survive redeploys.
export HOME="${KNOWLEDGE_HOME:-/data/home}"
mkdir -p "$HOME" "$VAULT"

if [ "${KNOWLEDGE_SYNC_ENABLED:-false}" = "true" ]; then
  # Keep the vault copy in step with Obsidian Sync; restart the tool if it ever stops.
  (
    while true; do
      ob sync --continuous --path "$VAULT" || echo "obsidian sync stopped; restarting in 30 seconds" >&2
      sleep 30
    done
  ) &
fi

exec uvicorn app.api.main:build --factory --host 0.0.0.0 --port "${PORT:-8000}" \
  --no-proxy-headers --no-server-header
