#!/bin/bash
# Re-derive speaker identity and rebuild everything that bakes a name into it,
# in the order refresh.sh documents. affinity and chair_anchor are both
# required here and were both missing once: re-deriving identity moves names
# between clusters, so every stored similarity is measured against a name that
# may have moved, and chair_anchor is the only stage that can outvote the
# archive-wide cluster majority about a commissioner.
set -euo pipefail
cd "$(dirname "$0")/.."
source bin/_env.sh

echo "=== speaker_id ==="; date "+    started %H:%M:%S"
$PY bin/speaker_id.py --write

echo; echo "=== name_speakers ==="; date "+    started %H:%M:%S"
$PY bin/name_speakers.py --write --limit 150

echo; echo "=== chair_anchor ==="; date "+    started %H:%M:%S"
$PY bin/chair_anchor.py --write

echo; echo "=== affinity ==="; date "+    started %H:%M:%S"
$PY bin/affinity.py

echo; echo "=== index_passages ==="; date "+    started %H:%M:%S"
$PY bin/index_passages.py

echo; echo "=== audit ==="; date "+    started %H:%M:%S"
$PY bin/audit.py || true

echo; echo "=== RESPEAK DONE ==="; date
