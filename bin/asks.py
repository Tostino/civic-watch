#!/usr/bin/env python3
"""How much /api/ask is being used, and by how many people.

Reads `asks`, which web/asklog.py writes and nothing public reads. Every
arrival is in there, including the ones that were turned away, which is the
half `answers` never had.

  bin/asks.py                the last 30 days, a line a day
  bin/asks.py --days 7       a shorter window
  bin/asks.py --questions    what was actually asked, most recent first

ON "PEOPLE". The count is of distinct `asker` tokens in a day, and a token is
an address. Two people behind one household router are one; one person on a
phone and a laptop is two; anyone whose address the server could not see at
all is none. So it is a floor with a wobble, not a headcount, and it is the
best a server that keeps no accounts and sets no cookies can do. `unattributed`
is the honest remainder and it is printed rather than hidden - if it is not
zero, either ASK_ASKER_KEY is unset or nothing is forwarding the real address
(LAUNCH.md 3.5), and the people column is undercounting by that much.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import db      # noqa: E402

# Everything that is not a finished, filed run. Kept in one place so the
# summary and the per-day line cannot disagree about what counts as refused.
REFUSED = ("rate", "daily", "busy", "closed", "empty", "length")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--questions", action="store_true",
                    help="list the questions instead of the daily counts")
    args = ap.parse_args()

    con = db.connect(autocommit=True)
    if not con.execute("SELECT to_regclass('public.asks')").fetchone()[0]:
        print("no `asks` table yet - apply bin/schema.sql", file=sys.stderr)
        return 1

    if args.questions:
        return _questions(con, args.days)
    return _daily(con, args.days)


def _daily(con, days):
    rows = con.execute(f"""
        SELECT at::date                                        AS day,
               count(*)                                        AS arrived,
               count(*) FILTER (WHERE outcome = 'kept')        AS kept,
               count(*) FILTER (WHERE outcome = 'gone')        AS gone,
               count(*) FILTER (WHERE outcome = 'error')       AS failed,
               count(*) FILTER (WHERE outcome IN {REFUSED})    AS refused,
               count(DISTINCT asker)                           AS people,
               count(*) FILTER (WHERE asker IS NULL)           AS blind,
               round(avg(ms) FILTER (WHERE outcome = 'kept'))  AS ms
          FROM asks
         WHERE at >= now() - make_interval(days => %s)
      GROUP BY 1 ORDER BY 1
    """, (days,)).fetchall()

    if not rows:
        print(f"nothing in the last {days} days")
        return 0

    print(f"{'day':<12}{'people':>7}{'asked':>7}{'kept':>6}{'left':>6}"
          f"{'failed':>8}{'refused':>9}{'avg s':>7}")
    for r in rows:
        secs = f"{r['ms'] / 1000:.0f}" if r["ms"] else "-"
        print(f"{r['day'].isoformat():<12}{r['people']:>7}{r['arrived']:>7}"
              f"{r['kept']:>6}{r['gone']:>6}{r['failed']:>8}"
              f"{r['refused']:>9}{secs:>7}")

    total = con.execute(f"""
        SELECT count(*)                                     AS arrived,
               count(DISTINCT asker)                        AS people,
               count(*) FILTER (WHERE asker IS NULL)         AS blind,
               count(*) FILTER (WHERE outcome IN {REFUSED}) AS refused
          FROM asks
         WHERE at >= now() - make_interval(days => %s)
    """, (days,)).fetchone()
    # NOT the sum of the People column: a token is per day, so somebody who
    # came back on Tuesday is two of these and one visitor. Said plainly
    # because a total that looks addable and is not is worse than no total.
    print(f"\n{total['arrived']} questions over {days} days, "
          f"{total['people']} person-days (somebody who returned counts once "
          f"per day), {total['refused']} refused")
    if total["blind"]:
        print(f"{total['blind']} of them could not be attributed to anyone, "
              f"so the people counts above are short by up to that much")
    return 0


def _questions(con, days):
    rows = con.execute("""
        SELECT at, outcome, answer_id, ms, question
          FROM asks
         WHERE at >= now() - make_interval(days => %s)
      ORDER BY at DESC
    """, (days,)).fetchall()
    for r in rows:
        when = r["at"].strftime("%m-%d %H:%M")
        secs = f"{r['ms'] / 1000:.0f}s" if r["ms"] else ""
        print(f"{when}  {r['outcome']:<7}{secs:>5}  "
              f"{(r['question'] or '')[:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
