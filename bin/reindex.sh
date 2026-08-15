#!/bin/bash
# Rebuild passages against the current item_spans, then prove it.
# Written as its own file rather than edited into a running one: bash reads a
# script incrementally by byte offset, so editing a script that is already
# executing silently skips or repeats whatever the edit shifted. That is how
# the land_agenda --redo step got dropped from finish_chain.sh mid-run.
set -euo pipefail
cd "$(dirname "$0")/.."
source bin/_env.sh

echo "=== index_passages ==="; date "+    started %H:%M:%S"
$PY bin/index_passages.py

echo; echo "=== audit ==="; date "+    started %H:%M:%S"
$PY bin/audit.py || true

echo; echo "=== eval_agent ==="; date "+    started %H:%M:%S"
# --agent as well: a rebuild reassigns every passage id, and the point
# of the answer checks is that they survive it. Tolerated here - the
# index is already written by this point and worth knowing about, not
# worth unwinding.
$PY bin/eval_agent.py --agent || true

echo; echo "=== REINDEX DONE ==="; date
