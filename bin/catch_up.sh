#!/bin/bash
# Bring the 83 meetings that finished transcribing AFTER the last rebuild into
# the derived layers. The fleet completed while segmentation was running, so
# those videos have no clusters, no names, no segments and no agenda binding -
# 45,528 utterances whose votes are structurally unretrievable.
#
# Same order refresh.sh documents, for the same reasons. speaker_id must run
# before name_speakers (its upsert resets source='llm'), and segment before
# the span rebuild, before index.
#
# A NEW file, not an edit to a running one - see gotcha 30.
set -euo pipefail
cd "$(dirname "$0")/.."
source bin/_env.sh

echo "=== speaker_id ==="; date "+    started %H:%M:%S"
$PY bin/speaker_id.py --write

echo; echo "=== name_speakers ==="; date "+    started %H:%M:%S"
$PY bin/name_speakers.py --write --limit 150

# Incremental: no --redo, so only meeting-days with no segments are read.
echo; echo "=== segment (incremental) ==="; date "+    started %H:%M:%S"
$PY bin/segment.py --write --jobs 12

# redo, because span boundaries are rebuilt wholesale from `segments` and the
# ON CONFLICT key (video_id, start_idx) will not collide with a moved boundary.
echo; echo "=== rebuild spans ==="; date "+    started %H:%M:%S"
$PY -c "
import db, land_agenda
con = db.connect(autocommit=False)
print('spans ... %d bound, %d transcript-only, %d unmatched'
      % land_agenda.bind_spans(con, redo=True))
"

echo; echo "=== index_passages ==="; date "+    started %H:%M:%S"
$PY bin/index_passages.py

echo; echo "=== audit ==="; date "+    started %H:%M:%S"
$PY bin/audit.py || true

echo; echo "=== eval_agent ==="; date "+    started %H:%M:%S"
# Tolerated: the ingest has landed by here, and an answer regression is
# a thing to be told about rather than a reason to fail a catch-up.
$PY bin/eval_agent.py --agent || true

echo; echo "=== CATCH UP DONE ==="; date
