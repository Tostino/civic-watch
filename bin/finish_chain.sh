#!/bin/bash
# Wait for the segment+land run and the name_speakers run to finish, then do
# the rest of the rebuild. Both of those are long, independent, and already
# running detached; this exists so the remaining stages start the moment they
# are ready instead of whenever someone next looks.
#
# The order below is the one refresh.sh documents. minutes writes outcomes onto
# the items land created; index bakes the item subject AND the resolved speaker
# name into what gets embedded, so it must be last.
cd "$(dirname "$0")/.."
source bin/_env.sh

wait_for () {           # $1 = log file, $2 = marker, $3 = label
    echo "waiting for $3 ..."
    while ! grep -q "$2" "$1" 2>/dev/null; do
        # If the process is gone and the marker never appeared, it died.
        if ! pgrep -f "$4" > /dev/null 2>&1; then
            sleep 5
            grep -q "$2" "$1" 2>/dev/null && break
            echo "  $3 exited without finishing - see $1" >&2
            return 1
        fi
        sleep 30
    done
    echo "  $3 done"
}

wait_for logs/refresh.log "=== chain complete ===" "segment+land" "segment.py|land_agenda.py" || exit 1
wait_for logs/names.log   "=== done ==="            "name_speakers" "name_speakers.py" || exit 1

# The segments table was rebuilt wholesale (--redo), so the spans bound to the
# OLD segment boundaries are stale. A plain re-land only upserts on
# (video_id, start_idx) and would leave every span whose boundary moved behind
# as an orphan, breaking the tiling that index_passages relies on.
echo; echo "=== land_agenda --redo ==="; date "+    started %H:%M:%S"
$PY bin/land_agenda.py --redo || exit 1

echo; echo "=== parse_minutes ==="; date "+    started %H:%M:%S"
$PY bin/parse_minutes.py --write || exit 1

echo; echo "=== index_passages ==="; date "+    started %H:%M:%S"
$PY bin/index_passages.py || exit 1

echo; echo "=== audit ==="; date "+    started %H:%M:%S"
$PY bin/audit.py

echo; echo "=== eval_votes ==="; date "+    started %H:%M:%S"
$PY bin/eval_votes.py

echo; echo "=== ALL DONE ==="; date
