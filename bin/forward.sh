#!/bin/bash
# Catch agendas for meetings that have NOT HAPPENED YET. The county publishes
# its calendar months out and the agenda days out: median 3 days, p90 7, over
# 1,161 agendas. So a daily run catches everything and anything slower misses
# the short ones. Nothing here touches the derived layers.
set -euo pipefail
cd "$(dirname "$0")/.."

source bin/_env.sh

# Re-ask about the recent past too: agendas get revised after they are posted,
# and a window starting today would keep the first version forever.
SINCE=$(date -d '21 days ago' +%Y-%m-%d 2>/dev/null || date -v-21d +%Y-%m-%d)

echo "=== $(date '+%F %T')  forward fetch, events since $SINCE ==="
$PY bin/civicclerk.py --events --since "$SINCE" | tail -4

echo
echo "=== fetching text for any newly published file ==="
$PY bin/civicclerk.py --text | tail -4

# --no-spans is load-bearing: bind_spans is NOT idempotent and a nightly job
# running it would grow the table forever. Not --redo either, which deletes
# every span. Meetings that have not happened have no recording to bind.
echo
echo "=== landing meetings and published items (no span binding) ==="
$PY bin/land_agenda.py --no-spans | tail -6

# subjects --rollup: the front page reads `subject_year` rather than computing
# it, because the live join costs 163 seconds, and only this stage writes it.
# Unconditional because 12 seconds against hour-long stages is not worth
# remembering, and it is one transaction, so readers never see a gap.
echo
echo "=== subjects --rollup ==="
$PY bin/subjects.py --rollup

echo
echo "=== what a resident can now read that they could not yesterday ==="
$PY - <<'PYEOF'
import sys
sys.path.insert(0, "bin")
import db
con = db.connect()
rows = con.execute("""
    SELECT m.date, m.body,
           (SELECT count(*) FROM agenda_items a
             WHERE a.meeting_id = m.id AND a.source = 'agenda') AS items
      FROM meetings m
     WHERE m.date > to_char(now(), 'YYYY-MM-DD')
     ORDER BY m.date""").fetchall()
ready = [r for r in rows if r[2]]
print(f"{len(rows)} meetings scheduled · {len(ready)} now have a published agenda")
for r in ready[:10]:
    print(f"   {r[0]}  {r[1][:38]:38} {r[2]:>4} items")
if not ready:
    nearest = rows[0] if rows else None
    if nearest:
        print(f"   none yet - the nearest is {nearest[0]} {nearest[1]}, and the "
              f"county typically posts about 3 days out")
PYEOF
