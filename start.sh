#!/bin/sh

BGUTIL_MAIN=""
for candidate in \
  /opt/bgutil-app/server/build/main.js \
  /opt/bgutil/server/build/main.js \
  /opt/bgutil-app/build/main.js
do
  if [ -f "$candidate" ]; then
    BGUTIL_MAIN="$candidate"
    break
  fi
done

if [ -z "$BGUTIL_MAIN" ]; then
  BGUTIL_MAIN=$(find /opt/bgutil-app -path '*/build/main.js' 2>/dev/null | head -1)
fi

if [ -n "$BGUTIL_MAIN" ] && [ -f "$BGUTIL_MAIN" ]; then
  BGUTIL_DIR=$(dirname "$BGUTIL_MAIN")
  echo "[start] Launching bgutil from $BGUTIL_MAIN"
  (cd "$BGUTIL_DIR/.." && node "$BGUTIL_MAIN" --port 4416) &
  i=0
  while [ "$i" -lt 20 ]; do
    if curl -sf "http://127.0.0.1:4416/ping" >/dev/null 2>&1; then
      echo "[start] bgutil PO token server is ready"
      break
    fi
    i=$((i + 1))
    sleep 1
  done
  if ! curl -sf "http://127.0.0.1:4416/ping" >/dev/null 2>&1; then
    echo "[start] WARNING: bgutil not responding — YouTube uses android_vr without cookies"
  fi
else
  echo "[start] WARNING: bgutil main.js not found under /opt/bgutil-app"
  ls -la /opt/bgutil-app 2>/dev/null || true
fi

echo "[start] Starting uvicorn on port ${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
