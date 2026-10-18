#!/bin/sh
# Railway sets PORT and ends TLS at its proxy. The app reads the proxy-appended address itself,
# so uvicorn must not rewrite the client address from headers the client can set.
set -eu
exec uvicorn app.api.main:build --factory --host 0.0.0.0 --port "${PORT:-8000}" \
  --no-proxy-headers --no-server-header
