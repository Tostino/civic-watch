#!/bin/bash
# Start the server through bin/_env.sh, which is the only path that puts the
# DSN, the inference key and the ask limits into the process. Started any other
# way it comes up, serves the archive perfectly, reports `llm_key: false`, and
# refuses every question. No `set -e`: _env.sh ends in a false conditional.
#
#   bin/serve.sh                 # the archive on :8765
#   bin/serve.sh --port 8799     # a second one, for a check
cd "$(dirname "$0")/.."
source bin/_env.sh

if [ -z "${LLM_API_KEY:-}" ]; then
    echo "warning: no LLM_API_KEY after sourcing env.local.sh." >&2
    echo "         The archive will read and search; /api/ask will refuse." >&2
fi
echo "serving  db=$(db_target)  ask: ${ASK_PER_IP:-6}/address per $(( ${ASK_WINDOW:-600} / 60 ))min, ${ASK_DAILY_MAX:-400}/day"
echo "         mcp: ${MCP_PER_IP:-60} tool calls/address per ${MCP_WINDOW:-60}s (${MCP_SEARCH_PER_IP:-20} of them transcript searches)"
exec $PY web/server.py "$@"
