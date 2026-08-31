#!/bin/bash
# Bring meetings that finished transcribing after the last rebuild into the
# derived layers. Same order refresh.sh documents, for the same reasons.
# A NEW file rather than an edit to a running one: bash reads a script by byte
# offset, so editing one mid-run skips or repeats whatever the edit shifted.
set -euo pipefail
cd "$(dirname "$0")/.."
source bin/_env.sh

echo "=== speaker_id ==="; date "+    started %H:%M:%S"
$PY bin/speaker_id.py --write

echo; echo "=== name_speakers ==="; date "+    started %H:%M:%S"
$PY bin/name_speakers.py --write --limit 150

#
#  A HOME ADDRESS SAID OUT LOUD IS PUBLISHED THE MOMENT THE FOLD FINISHES,
# unless something proposes it for removal, and nothing did.
#
# `bin/redact.py` was in no chain at all. Not here, not refresh.sh, not
# respeak.sh, not rederive.py; rebuild.sh names it only to KEEP the table.
# It was an operator running a command by hand and remembering to, and on
# 25 August three recordings landed with 34 residences in the published text:
# "My name is Frida Forrester. I live at 13318 Starfish Drive, Hudson", and a
# man who gave his address from the podium twice, redacted in the meeting where
# he said "my name is" and in the clear in the one where he did not.
#
# It PROPOSES and does not apply, which is redact.py's own rule and the reason
# this is safe to automate: "NOTHING IS REDACTED WITHOUT A PERSON ACCEPTING IT.
# A detector that altered the transcript of a public meeting on its own
# judgement is not a thing this archive should own." The queue lands in the
# curation console; a person decides.
#
# Before segmentation rather than after, so that a fold which fails later still
# leaves the addresses queued rather than published and forgotten.
echo; echo "=== redact --propose ==="; date "+    started %H:%M:%S"
$PY bin/redact.py --propose --write

echo; echo "=== segment (incremental) ==="; date "+    started %H:%M:%S"
$PY bin/segment.py --write --jobs 12

# redo: boundaries are rebuilt wholesale, and ON CONFLICT (video_id, start_idx)
# will not collide with one that moved.
echo; echo "=== rebuild spans ==="; date "+    started %H:%M:%S"
$PY -c "
import db, land_agenda
con = db.connect(autocommit=False)
print('spans ... %d bound, %d transcript-only, %d unmatched'
      % land_agenda.bind_spans(con, redo=True))
"

echo; echo "=== index_passages ==="; date "+    started %H:%M:%S"
$PY bin/index_passages.py

# subjects --rollup: the front page reads `subject_year` rather than computing
# it, because the live join costs 163 seconds, and only this stage writes it.
# Unconditional because 12 seconds against hour-long stages is not worth
# remembering, and it is one transaction, so readers never see a gap.
echo; echo "=== subjects --rollup ==="; date "+    started %H:%M:%S"
$PY bin/subjects.py --rollup

echo; echo "=== audit ==="; date "+    started %H:%M:%S"
$PY bin/audit.py || true

echo; echo "=== eval_agent ==="; date "+    started %H:%M:%S"
$PY bin/eval_agent.py --agent || true

echo; echo "=== CATCH UP DONE ==="; date
