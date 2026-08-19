#!/bin/bash
# Rebuild passages against the current item_spans, then prove it. Its own file
# rather than an edit to a running one: bash reads a script by byte offset, so
# editing one mid-run skips or repeats whatever the edit shifted.
set -euo pipefail
cd "$(dirname "$0")/.."
source bin/_env.sh

echo "=== index_passages ==="; date "+    started %H:%M:%S"
$PY bin/index_passages.py

echo; echo "=== audit ==="; date "+    started %H:%M:%S"
$PY bin/audit.py || true

echo; echo "=== eval_agent ==="; date "+    started %H:%M:%S"
# --agent: a rebuild reassigns every passage id and the answer checks exist to
# prove citations survive it. Tolerated, since the index is already written.
$PY bin/eval_agent.py --agent || true

echo; echo "=== REINDEX DONE ==="; date
