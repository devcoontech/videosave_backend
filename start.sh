#!/bin/sh
set -e

BGUTIL_MAIN="/opt/bgutil/server/build/main.js"

if [ -f "$BGUTIL_MAIN" ]; then
  echo "[start] Launching bgutil PO token server on 127.0.0.1:4416..."
  node "$BGUTIL_MAIN" --host 127.0.0.1 &
  i=0
  while [ "$i" -lt 15 ]; do
    if curl -sf "http://127.0.0.1:4416/ping" >/dev/null 2>&1; then
      echo "[start] bgutil is ready"
      break
    fi
    i=$((i + 1))
    sleep 1
  done
  if ! curl -sf "http://127.0.0.1:4416/ping" >/dev/null 2>&1; then
    echo "[start] WARNING: bgutil did not respond on /ping — YouTube will use cookies/android_vr fallback"
  fi
else
  echo "[start] WARNING: bgutil not built at $BGUTIL_MAIN — skipping PO token server"
fi

echo "[start] Starting uvicorn on port ${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
