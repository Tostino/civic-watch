#!/bin/bash
# Rebuild the derived layers, in the only order that is correct.
#
# The order is not a style preference:
#   roster        must precede speaker_id, which now draws its per-meeting
#                 candidates from it
#   speaker_id    still precedes name_speakers and chair_anchor, but NOT
#                 because it would destroy them any more. Its upsert used to
#                 write source = NULL over every row, and it is the only
#                 writer of that column, so a bare `refresh.sh speakers`
#                 reverted both - measured: 0 rows survived with
#                 source='chair'. It now updates only the rows it left NULL,
#                 so the order is a preference (name the voices once the
#                 clusters are final) rather than a trap
#   segment       must precede land_agenda, which binds segments to published
#                 items, which must precede index_passages, which bakes the
#                 item's subject into what gets embedded
#   minutes       must follow land_agenda (it writes outcomes onto the items
#                 land creates) and precede index_passages. Leaving it out of
#                 the chain, as it was, meant a re-land silently reverted every
#                 item to no recorded outcome.
#
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

# WHAT THE FRONT PAGE READS IS A TABLE, and no stage above rebuilds it. The
# issues strip reads `subject_year` rather than computing it, because the live
# join costs 163 seconds once sub-subjects exist - and only bin/subjects.py
# writes that table, from passes that are all CURATION. So landing agendas or
# parsing minutes changes what every subject matches while the public page
# keeps yesterday's numbers, rendering perfectly and saying nothing wrong.
# That is the failure mode subjects.rollup() names: worse than slow.
#
# Unconditional rather than a stage to remember, for the same reason. It is
# 12 seconds against stages measured in hours, it is a no-op on a database
# with no kept vocabulary, and it is one transaction - readers see the old
# rows until it commits, never an empty table.
#
# Time-dependent as well as data-dependent: the record lane counts meetings
# with `date <= now()`, so a meeting whose agenda landed a week ago enters
# the counts on the day it is held, with no ingest involved at all.
echo; echo "=== subjects --rollup ==="; date "+    started %H:%M:%S"
$PY bin/subjects.py --rollup

echo; echo "=== refresh complete ==="
