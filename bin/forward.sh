#!/bin/bash
# Catch agendas for meetings that have NOT HAPPENED YET.
#
# The county publishes its calendar months out and the agenda days out. Right
# now that gap is the whole story: 35 meetings are on the books through
# 2027-01-14 and not one of them has an agenda, because every fetch we have
# ever done predates the documents. Nothing is broken - we have simply never
# looked again.
#
# HOW OFTEN, from the archive rather than a guess. Days between an agenda being
# published and its meeting, over 1,161 agendas:
#
#     median 3 · p90 7 · 710 of them land 1-14 days ahead · only 17 earlier
#
# So a DAILY run catches everything, and the tightest real case - published the
# day before - still has a full cycle of slack. Anything slower than daily
# starts missing the short ones.
#
# This is the cheap door of the three in UI_REQUIREMENTS §5.9. It needs no new
# extractor and no model: `parse_agenda`, the item rows and the coverage chips
# already work and have nothing to learn. It is also the only one of the three
# that lets a resident ACT rather than check.
#
# Nothing here touches the derived layers. It adds portal events, adds file
# text, and lands meetings and published items. Segments, spans, speaker names
# and the search index are all somebody else's job and are left alone.
set -euo pipefail
cd "$(dirname "$0")/.."

source bin/_env.sh

# Re-ask about the recent past as well as the future. An agenda is often
# REVISED after it is first posted - a continuance added, an item pulled - and
# a window that starts today would take the first version and keep it.
SINCE=$(date -d '21 days ago' +%Y-%m-%d 2>/dev/null || date -v-21d +%Y-%m-%d)

echo "=== $(date '+%F %T')  forward fetch, events since $SINCE ==="
$PY bin/civicclerk.py --events --since "$SINCE" | tail -4

echo
echo "=== fetching text for any newly published file ==="
$PY bin/civicclerk.py --text | tail -4

# --no-spans, and it is load-bearing. Plain `land_agenda.py` re-runs bind_spans,
# which is NOT idempotent: two runs on unchanged data added 447 then 262
# transcript-derived items and stranded the originals without spans. A nightly
# job doing that would grow the table forever. Not --redo either - that deletes
# every span in the archive.
#
# Nothing is lost by skipping it here. This job exists for meetings that have
# not happened yet; they have no recording, so no segments, so nothing for
# bind_spans to do.
echo
echo "=== landing meetings and published items (no span binding) ==="
$PY bin/land_agenda.py --no-spans | tail -6

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
