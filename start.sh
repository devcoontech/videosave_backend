#!/bin/sh

export HOME="${HOME:-/tmp}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-/tmp/bgutil-cache}"
export NODE_PATH="/opt/bgutil-app/node_modules"
mkdir -p "$XDG_CACHE_HOME"

BGUTIL_ROOT="/opt/bgutil-app"
BGUTIL_MAIN="$BGUTIL_ROOT/build/main.js"
BGUTIL_SCRIPT="$BGUTIL_ROOT/build/generate_once.js"
BGUTIL_LOG="/tmp/bgutil.log"

if [ -f "$BGUTIL_SCRIPT" ]; then
  if node -e "require('canvas');" >/dev/null 2>&1; then
    echo "[start] node canvas module OK"
  else
    echo "[start] WARNING: node canvas module failed to load"
    node -e "require('canvas');" 2>&1 | tail -n 5 || true
  fi
  if node "$BGUTIL_SCRIPT" --version >/dev/null 2>&1; then
    echo "[start] bgutil script OK (version $(node "$BGUTIL_SCRIPT" --version 2>/dev/null))"
  else
    echo "[start] WARNING: bgutil script failed --version check"
    node "$BGUTIL_SCRIPT" --version 2>&1 | tail -n 5 || true
  fi
fi

if [ -f "$BGUTIL_MAIN" ]; then
  echo "[start] Launching bgutil PO token server from $BGUTIL_MAIN"
  cd "$BGUTIL_ROOT" || exit 1
  # bgutil 1.3.x only supports --port (no --host); it binds [::] then falls back to 0.0.0.0
  node build/main.js --port 4416 >"$BGUTIL_LOG" 2>&1 &
  BGUTIL_PID=$!
  i=0
  while [ "$i" -lt 25 ]; do
    if curl -sf "http://127.0.0.1:4416/ping" >/dev/null 2>&1; then
      echo "[start] bgutil PO token server is ready (pid $BGUTIL_PID)"
      break
    fi
    if ! kill -0 "$BGUTIL_PID" 2>/dev/null; then
      echo "[start] ERROR: bgutil process exited early"
      tail -n 20 "$BGUTIL_LOG" 2>/dev/null || true
      break
    fi
    i=$((i + 1))
    sleep 1
  done
  if ! curl -sf "http://127.0.0.1:4416/ping" >/dev/null 2>&1; then
    echo "[start] WARNING: bgutil HTTP server not responding on :4416/ping"
    if [ -f "$BGUTIL_SCRIPT" ]; then
      echo "[start] Script fallback available at $BGUTIL_SCRIPT (yt-dlp will spawn node per request)"
    else
      echo "[start] Script fallback missing: $BGUTIL_SCRIPT"
    fi
    echo "[start] Or add a separate Coolify service: brainicism/bgutil-ytdlp-pot-provider:1.3.2-node"
    echo "[start] Then set BGUTIL_POT_BASE_URL=http://<that-service-hostname>:4416"
  fi
else
  echo "[start] ERROR: missing $BGUTIL_MAIN"
  ls -la "$BGUTIL_ROOT" 2>/dev/null || echo "[start] /opt/bgutil-app not found — Docker build may have failed"
fi

echo "[start] Starting uvicorn on port ${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
