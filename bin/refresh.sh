#!/bin/bash
# Rebuild the derived layers, in the only order that is correct: roster feeds
# speaker_id's per-meeting candidates, chair_anchor settles which cluster holds
# which commissioner before affinity scores voices against those names, segment
# binds before land, and land writes the items minutes and index depend on.
# Every stage is idempotent, so this is also the resume.
set -euo pipefail
cd "$(dirname "$0")/.."
source bin/_env.sh

stage () { echo; echo "=== $* ==="; date "+    started %H:%M:%S"; }

for want in "$@"; do
  case "$want" in
    # Both bodies - see the note in rebuild.sh. A bare call is BCC only.
    roster)    stage roster;    $PY bin/roster.py --write &&
                                $PY bin/roster.py --write --body "Planning Commission" ;;
    speakers)  stage speaker_id; $PY bin/speaker_id.py --write ;;
    names)     stage name_speakers; $PY bin/name_speakers.py --write --limit 150 ;;
    # chair BEFORE affinity: it decides which cluster carries which
    # commissioner's name, and affinity scores voices against the names that
    # exist - a reference set full of the wrong name teaches it the wrong thing.
    chair)     stage chair_anchor; $PY bin/chair_anchor.py --write ;;
    # affinity BEFORE index: the resolver refuses to hand a cluster's name to a
    # voice measured not to be that person, and index bakes the resolved name
    # into every passage.
    affinity)  stage affinity;   $PY bin/affinity.py ;;
    segment)   stage segment;   $PY bin/segment.py --write --jobs 12 ;;
    land)      stage land_agenda; $PY bin/land_agenda.py ;;
    minutes)   stage parse_minutes; $PY bin/parse_minutes.py --write ;;
    index)     stage index_passages; $PY bin/index_passages.py ;;
    eval)      stage eval_agent; $PY bin/eval_agent.py --agent ;;
    *) echo "unknown stage: $want" >&2; exit 2 ;;
  esac
done

# subjects --rollup: the front page reads `subject_year` rather than computing
# it, because the live join costs 163 seconds, and only this stage writes it.
# Unconditional because 12 seconds against hour-long stages is not worth
# remembering, and it is one transaction, so readers never see a gap.
echo; echo "=== subjects --rollup ==="; date "+    started %H:%M:%S"
$PY bin/subjects.py --rollup

echo; echo "=== refresh complete ==="
