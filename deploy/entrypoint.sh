#!/usr/bin/env bash
# Two processes, one container. The whole job of this script is to make that
# honest: if either half dies, the CONTAINER dies, so Docker's restart policy
# handles it and a half-dead stack is visible as a restart loop rather than as
# a healthy container serving errors.
#
# That is the one real cost of unifying, and it is the one thing worth spending
# code on. A supervisor that restarts a dead child in place would hide exactly
# what you need to see.
set -uo pipefail

API_PORT="${API_PORT:-8765}"
UI_PORT="${PORT:-3000}"

log() { echo "[supervisor] $*"; }

# The API listens on LOOPBACK ONLY. Nothing outside this container can reach it,
# which is what keeps /api/admin off the network; Next reaches it over 127.0.0.1
# and the console itself is 404'd in ui/proxy.ts.
log "starting api on 127.0.0.1:${API_PORT} (loopback only)"
python -u /app/web/server.py --host 127.0.0.1 --port "${API_PORT}" \
  > >(sed -u 's/^/[api] /') 2>&1 &
api=$!

# Wait for the API before starting Next, so the first page render does not race
# a database connection that is still opening.
for i in $(seq 1 120); do
  if ! kill -0 "$api" 2>/dev/null; then
    log "api exited during startup; see [api] lines above"
    exit 1
  fi
  if python -c "
import socket,sys
s=socket.socket(); s.settimeout(1)
sys.exit(0 if s.connect_ex(('127.0.0.1',${API_PORT}))==0 else 1)" 2>/dev/null; then
    log "api is listening after ${i}s"
    break
  fi
  sleep 1
done

log "starting ui on 0.0.0.0:${UI_PORT}"
HOSTNAME=0.0.0.0 PORT="${UI_PORT}" node /app/ui/server.js \
  > >(sed -u 's/^/[ui]  /') 2>&1 &
ui=$!

shutdown() {
  log "signal received; stopping both"
  kill -TERM "$api" "$ui" 2>/dev/null
  wait "$api" "$ui" 2>/dev/null
  exit 0
}
trap shutdown TERM INT

# Poll rather than `wait -n`: this is unambiguous about WHICH half died, and
# that sentence in the log is the whole point of the exercise.
while true; do
  kill -0 "$api" 2>/dev/null || { log "THE API DIED - stopping the container"; kill -TERM "$ui" 2>/dev/null; exit 1; }
  kill -0 "$ui"  2>/dev/null || { log "THE UI DIED - stopping the container";  kill -TERM "$api" 2>/dev/null; exit 1; }
  sleep 2
done
