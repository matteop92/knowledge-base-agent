#!/bin/sh
# Railway sets PORT; TLS ends at the platform proxy, so trust its forwarded headers.
set -eu
exec uvicorn app.api.main:build --factory --host 0.0.0.0 --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips="*" --no-server-header
