#!/bin/bash
# Wait for the detached segment+land and name_speakers runs, then finish the
# rebuild in the order refresh.sh documents: minutes writes outcomes onto the
# items land created, and index bakes both the item subject and the resolved
# speaker name into what gets embedded, so it goes last.
cd "$(dirname "$0")/.."
source bin/_env.sh

wait_for () {           # $1 = log file, $2 = marker, $3 = label
    echo "waiting for $3 ..."
    while ! grep -q "$2" "$1" 2>/dev/null; do
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

# --redo: a plain re-land upserts on (video_id, start_idx) and would orphan
# every span whose boundary moved, breaking the tiling index_passages needs.
echo; echo "=== land_agenda --redo ==="; date "+    started %H:%M:%S"
$PY bin/land_agenda.py --redo || exit 1

echo; echo "=== parse_minutes ==="; date "+    started %H:%M:%S"
$PY bin/parse_minutes.py --write || exit 1

echo; echo "=== index_passages ==="; date "+    started %H:%M:%S"
$PY bin/index_passages.py || exit 1

# subjects --rollup: the front page reads `subject_year` rather than computing
# it, because the live join costs 163 seconds, and only this stage writes it.
# Unconditional because 12 seconds against hour-long stages is not worth
# remembering, and it is one transaction, so readers never see a gap.
echo; echo "=== subjects --rollup ==="; date "+    started %H:%M:%S"
$PY bin/subjects.py --rollup || exit 1

echo; echo "=== audit ==="; date "+    started %H:%M:%S"
$PY bin/audit.py

echo; echo "=== eval_agent ==="; date "+    started %H:%M:%S"
$PY bin/eval_agent.py --agent

echo; echo "=== ALL DONE ==="; date
