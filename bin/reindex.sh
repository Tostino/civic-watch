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

echo; echo "=== eval_votes ==="; date "+    started %H:%M:%S"
$PY bin/eval_votes.py || true

echo; echo "=== REINDEX DONE ==="; date
