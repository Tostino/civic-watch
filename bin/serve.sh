#!/bin/bash
# Start the reading + curation server the way it has to be started: through
# bin/_env.sh, so the process holds the DSN, the inference key and the limits
# that bound what /api/ask may spend.
#
# This script exists because starting it any other way is a silent failure and
# not a loud one. `python web/server.py` from a plain shell comes up, serves
# the archive perfectly, reports `llm_key: false` in the console, and answers
# every question with an error - which is how the server ran for weeks while
# the CLI worked (gotchas 87, 88).
#
# No `set -e`: bin/_env.sh ends in a conditional that is false on the normal
# path, and exec is the last thing here anyway.
#
#   bin/serve.sh                 # the archive on :8765
#   bin/serve.sh --port 8799     # a second one, for a check
cd "$(dirname "$0")/.."
source bin/_env.sh

if [ -z "${LLM_API_KEY:-}" ]; then
    echo "warning: no LLM_API_KEY after sourcing env.local.sh." >&2
    echo "         The archive will read and search; /api/ask will refuse." >&2
fi
echo "serving  db=$(pasco_target)  ask: ${ASK_PER_IP:-6}/address per $(( ${ASK_WINDOW:-600} / 60 ))min, ${ASK_DAILY_MAX:-400}/day"
exec $PY web/server.py "$@"
