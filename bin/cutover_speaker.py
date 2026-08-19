#!/usr/bin/env python3
"""Make the claims resolver THE speaker resolver, and put it back if not.

WHAT THIS SWAPS. `utterance_speaker` is a view resolving four levels per row
at read time. `utterance_speaker_next` is a view over `speaker_resolved`, the
materialised claims resolution. They carry identical columns and identical
types - checked below, not assumed - so renaming one over the other changes
what 58 call sites in 15 files mean without editing any of them.

WHY A REBUILD FOLLOWS. Passages are speaker-bounded: `index_passages` starts a
new passage when the speaker changes, so re-resolving does not relabel
passages, it RE-SEGMENTS them. Passage ids change. Anything holding a passage
id - the `[123456]` citations in saved answers - goes stale, which is why
--apply drops them; they are cached agent runs and reproduce by asking again.

WHY IT WAS REVERSIBLE, past tense. Nothing was dropped except the saved
answers; the old view was renamed aside rather than deleted so --rollback
could rename it back. `utterance_speaker_old` was retired on 2026-08-18,
so that way back is closed and --rollback now refuses instead of failing
somewhere less obvious. `git show e9b9451^:bin/schema.sql` has the
definition if it is ever wanted again.
Both resolutions are computed from `speaker_claim` and `speaker_override`,
neither of which this touches.

THE ORDER MATTERS. The rename is instant and takes effect for live readers on
the next query: a transcript page resolves through the view. Passages are
baked, so search and the agent keep serving the old names until their
recording is rebuilt. Between the rename and the end of the rebuild the
archive is mixed, and --one leaves it that way on purpose so a person can look
at one meeting before committing to 432.

    bin/cutover_speaker.py --one <video_id>   rename, rebuild one, show the diff
    bin/cutover_speaker.py --all              rebuild the rest, drop saved answers
    bin/cutover_speaker.py --rollback         rename back, rebuild everything
    bin/cutover_speaker.py --status           what state the archive is in
"""
import argparse
import sys
import time

import db
import index_passages

OLD = "utterance_speaker_old"
NEW = "utterance_speaker_next"
LIVE = "utterance_speaker"

COLS = """SELECT column_name, data_type FROM information_schema.columns
           WHERE table_name = %s ORDER BY ordinal_position"""


def _views(con):
    return {r["table_name"] for r in con.execute(
        """SELECT table_name FROM information_schema.tables
            WHERE table_type = 'VIEW'
              AND table_name IN (%s, %s, %s)""", (OLD, NEW, LIVE))}


def status(con):
    """Which resolver is live, said from the database rather than from hope."""
    v = _views(con)
    if OLD in v and NEW not in v:
        state = "CUT OVER - utterance_speaker is the claims resolver"
    elif NEW in v and OLD not in v:
        state = "shadow - utterance_speaker is the old read-time resolver"
    elif LIVE in v and OLD not in v and NEW not in v:
        state = ("CUT OVER - utterance_speaker is the claims resolver, and the "
                 "old one is retired")
    else:
        state = f"UNEXPECTED: views present = {sorted(v)}"
    print(f"  {state}")
    stale = con.execute("""
        SELECT COUNT(*) AS n FROM passages p
         WHERE NOT EXISTS (SELECT 1 FROM utterances u
                            JOIN utterance_speaker us
                              ON us.video_id = u.video_id AND us.idx = u.idx
                           WHERE u.video_id = p.video_id
                             AND u.idx BETWEEN p.start_idx AND p.end_idx
                             AND us.name IS NOT DISTINCT FROM p.speaker)
           AND p.speaker <> '(exchange)'""").fetchone()["n"]
    print(f"  passages whose baked speaker matches no line under them: {stale:,}")


def _check(con):
    """Refuse to swap two views that are not interchangeable."""
    v = _views(con)
    if NEW not in v:
        sys.exit(f"  {NEW} is not there. Nothing to cut over to.")
    if OLD in v:
        sys.exit(f"  {OLD} already exists - the archive is already cut over.")
    a = [(r["column_name"], r["data_type"]) for r in con.execute(COLS, (LIVE,))]
    b = [(r["column_name"], r["data_type"]) for r in con.execute(COLS, (NEW,))]
    if a != b:
        print("  column shapes differ, refusing:")
        print(f"    only in {LIVE}: {[x for x in a if x not in b]}")
        print(f"    only in {NEW} : {[x for x in b if x not in a]}")
        sys.exit(1)
    print(f"  shapes match on {len(a)} columns")


def swap(con):
    """The cutover itself. One transaction, so there is no instant where
    `utterance_speaker` does not exist."""
    _check(con)
    cur = con.cursor()
    cur.execute(f"ALTER VIEW {LIVE} RENAME TO {OLD}")
    cur.execute(f"ALTER VIEW {NEW} RENAME TO {LIVE}")
    con.commit()
    print(f"  {LIVE} is now the claims resolver; the old one is {OLD}")


def unswap(con):
    v = _views(con)
    if OLD not in v:
        sys.exit(f"  {OLD} is not there - nothing to roll back to.")
    cur = con.cursor()
    cur.execute(f"ALTER VIEW {LIVE} RENAME TO {NEW}")
    cur.execute(f"ALTER VIEW {OLD} RENAME TO {LIVE}")
    con.commit()
    print(f"  rolled back; {LIVE} is the old read-time resolver again")


def rebuild(con, videos, label):
    t0 = time.time()
    for i, v in enumerate(videos, 1):
        index_passages.rebuild_video(con, v, verbose=False)
        con.commit()
        if i % 25 == 0 or i == len(videos):
            per = (time.time() - t0) / i
            print(f"    {label} {i}/{len(videos)}  {per:.1f}s each, "
                  f"{per * (len(videos) - i) / 60:.0f} min left")
    print(f"  rebuilt {len(videos)} recording(s) in {(time.time()-t0)/60:.1f} min")


def diff_one(con, video_id, before):
    after = {r["id"]: r for r in con.execute(
        """SELECT id, speaker, start_idx, end_idx, left(text, 70) AS head
             FROM passages WHERE video_id = %s ORDER BY start_idx""", (video_id,))}
    print(f"\n  passages: {len(before)} before, {len(after)} after")
    was = {}
    for r in before:
        was.setdefault((r["start_idx"], r["end_idx"]), r["speaker"])
    moved = shown = 0
    for r in after.values():
        old = was.get((r["start_idx"], r["end_idx"]), "(boundary moved)")
        if old == r["speaker"]:
            continue
        moved += 1
        if shown < 15:
            shown += 1
            print(f"    {str(old)[:24]:<24} -> {str(r['speaker'])[:24]:<24} "
                  f"{r['head'][:46]}")
    print(f"  {moved} passages differ")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--status", action="store_true")
    g.add_argument("--one", metavar="VIDEO_ID",
                   help="rename, rebuild this one recording, print the diff")
    g.add_argument("--all", action="store_true",
                   help="rebuild every recording and drop the saved answers")
    g.add_argument("--rollback", action="store_true")
    args = ap.parse_args()
    con = db.connect()

    if args.status:
        return status(con)

    if args.rollback:
        # utterance_speaker_old is gone (2026-08-18), so there is nothing to
        # rename back. Said here rather than discovered inside unswap(), which
        # would half-do it.
        if OLD not in _views(con):
            sys.exit("  utterance_speaker_old was retired - there is no view to\n"
                     "  rename back. Recreate it from\n"
                     "      git show e9b9451^:bin/schema.sql\n"
                     "  before running this.")
        unswap(con)
        vids = [r["id"] for r in con.execute(
            "SELECT DISTINCT video_id AS id FROM passages ORDER BY 1")]
        return rebuild(con, vids, "rollback")

    if args.one:
        before = [dict(r) for r in con.execute(
            """SELECT id, speaker, start_idx, end_idx, left(text, 70) AS head
                 FROM passages WHERE video_id = %s ORDER BY start_idx""", (args.one,))]
        if not before:
            sys.exit(f"  no passages for {args.one}")
        swap(con)
        rebuild(con, [args.one], "dry-run")
        diff_one(con, args.one, before)
        print("\n  The archive is now MIXED: this recording is rebuilt, the rest\n"
              "  are not. --all to finish, --rollback to undo.")
        return

    # --all
    if OLD not in _views(con):
        swap(con)
    n = con.execute("SELECT COUNT(*) AS n FROM answers").fetchone()["n"]
    con.cursor().execute("DELETE FROM answers")
    con.commit()
    print(f"  dropped {n} saved answer(s): their [id] citations name passages "
          f"that re-segmentation replaces")
    vids = [r["id"] for r in con.execute(
        "SELECT DISTINCT video_id AS id FROM passages ORDER BY 1")]
    rebuild(con, vids, "cutover")
    status(con)


if __name__ == "__main__":
    sys.exit(main())
