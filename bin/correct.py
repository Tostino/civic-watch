#!/usr/bin/env python3
"""Correct who said something, over a range of utterances.

The admin surface that does this by clicking is /admin. This exists so the
archive is fixable from a shell too, and because
the operation is worth having outside a browser anyway: it is the one write
that outranks the entire pipeline, and it should be scriptable.

The unit is a contiguous range of utterances in one recording, keyed
on (video_id, idx) and never on a cluster id - only ~2% of cluster ids survive
a re-clustering run. A correction therefore survives every rebuild of the
derived layers, which is the whole point.

    # look before you write - show what is there now
    bin/correct.py show 840x-PTQXfc --at 1:06:30-1:08:00

    # this stretch is a different person
    bin/correct.py set 840x-PTQXfc --at 1:06:30-1:08:00 --name Yeager

    # this stretch is NOT who it says, and I do not know who it is.
    # The operation the whole-voice model could not express at all.
    bin/correct.py set 840x-PTQXfc --idx 300-312 --detach

    # a name for a voice the pipeline never identified
    bin/correct.py set 840x-PTQXfc --at 2:14:00-2:15:30 --name "Jane Rodriguez"

    bin/correct.py list 840x-PTQXfc
    bin/correct.py undo 41
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402


def seconds(text):
    """Accept 92, 1:32, or 1:06:30."""
    parts = [float(p) for p in str(text).split(":")]
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def hhmmss(t):
    t = int(t or 0)
    return f"{t // 3600}:{(t % 3600) // 60:02d}:{t % 60:02d}"


def resolve_range(con, video_id, args):
    """Turn --idx or --at into a concrete (start_idx, end_idx)."""
    if args.idx:
        lo, _, hi = args.idx.partition("-")
        return int(lo), int(hi or lo)
    if not args.at:
        sys.exit("give --idx A-B or --at MM:SS-MM:SS")
    lo, _, hi = args.at.partition("-")
    t0, t1 = seconds(lo), seconds(hi or lo)
    # Any utterance that overlaps the window at all, so a range typed off the
    # timestamps shown in the UI does not silently clip its first line.
    r = con.execute(
        'SELECT MIN(idx), MAX(idx) FROM utterances '
        'WHERE video_id = %s AND "end" > %s AND start < %s',
        (video_id, t0, t1)).fetchone()
    if r[0] is None:
        sys.exit(f"no utterances between {hhmmss(t0)} and {hhmmss(t1)}")
    return r[0], r[1]


def show(con, video_id, lo, hi):
    rows = con.execute("""
        SELECT u.idx, u.start, us.name, us.basis, us.human, us.contested,
               LEFT(u.text, 74) AS text
        FROM utterances u
        JOIN utterance_speaker us
          ON us.video_id = u.video_id AND us.idx = u.idx
        WHERE u.video_id = %s AND u.idx BETWEEN %s AND %s
        ORDER BY u.idx""", (video_id, lo, hi)).fetchall()
    if not rows:
        print("  (nothing in that range)")
        return
    for r in rows:
        mark = "*" if r["human"] else " "
        flag = " !" if r["contested"] else ""
        print(f"  {r['idx']:5} {hhmmss(r['start']):>9} {mark}"
              f"{str(r['name'] or '—')[:22]:24} {str(r['basis'] or ''):9}{flag} {r['text']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("show", help="what the archive currently says")
    p.add_argument("video_id")
    p.add_argument("--idx")
    p.add_argument("--at")

    p = sub.add_parser("set", help="correct a range")
    p.add_argument("video_id")
    p.add_argument("--idx")
    p.add_argument("--at")
    p.add_argument("--name", help="who it actually is")
    p.add_argument("--detach", action="store_true",
                   help="not who it says, and I do not know who")
    p.add_argument("--note", help="why, for whoever reads this later")
    p.add_argument("--author", default=os.environ.get("USER") or "admin")
    p.add_argument("--pending", action="store_true",
                   help="record as a proposal that changes nothing until reviewed")
    p.add_argument("--no-index", action="store_true",
                   help="skip re-indexing; search keeps the old name until it runs")
    p.add_argument("--device", default="cuda:1")

    p = sub.add_parser("list", help="corrections on a recording, or all of them")
    p.add_argument("video_id", nargs="?")

    p = sub.add_parser("undo", help="remove a correction by id")
    p.add_argument("id", type=int)
    p.add_argument("--device", default="cuda:1")

    args = ap.parse_args()
    con = db.connect(autocommit=False)

    if args.cmd == "list":
        where, params = ("WHERE o.video_id = %s", (args.video_id,)) if args.video_id else ("", ())
        rows = con.execute(f"""
            SELECT o.id, o.video_id, o.start_idx, o.end_idx, o.action, o.name,
                   o.status, o.author, o.note, o.created_at
            FROM speaker_override o {where}
            ORDER BY o.created_at DESC LIMIT 100""", params).fetchall()
        if not rows:
            print("no corrections recorded")
            return 0
        for r in rows:
            print(f"  #{r['id']:<5} {r['video_id']} {r['start_idx']}-{r['end_idx']:<6} "
                  f"{r['action']:<9} {str(r['name'] or '—')[:22]:24} {r['status']:<8} "
                  f"{r['author'] or ''} {r['note'] or ''}")
        return 0

    if args.cmd == "undo":
        r = con.execute("DELETE FROM speaker_override WHERE id = %s RETURNING video_id, "
                        "start_idx, end_idx", (args.id,)).fetchone()
        if not r:
            sys.exit(f"no correction #{args.id}")
        con.commit()
        print(f"removed #{args.id}; {r[0]} {r[1]}-{r[2]} is back to what the pipeline says")
        show(con, r[0], r[1], r[2])
        # Withdrawing a correction has to reach the index for the same reason
        # making one does, or search keeps answering with a name the archive
        # no longer claims.
        # RESOLUTION FIRST, THEN THE INDEX. speaker_resolved is a table, not
        # a view: an override written straight to speaker_override changes
        # nothing a reader sees until it is resolved into that table. Rebuild
        # the passages without this and they are rebuilt from the OLD names -
        # the correction is stored, re-indexed, and invisible. web/admin.py
        # has always done both; this path did only the second.
        import speaker_claims
        speaker_claims.refresh_video(con, r[0])
        import index_passages
        try:
            index_passages.refresh_video(con, r[0], device=args.device)
        except Exception as e:
            print(f"\nthe correction is removed, but re-indexing failed: {e}")
            print("run bin/index_passages.py to bring search back in step")
            return 1
        return 0

    lo, hi = resolve_range(con, args.video_id, args)

    if args.cmd == "show":
        print(f"{args.video_id} idx {lo}-{hi}   (* = a person said so, ! = disputed)")
        show(con, args.video_id, lo, hi)
        return 0

    if args.detach == bool(args.name):
        sys.exit("give exactly one of --name NAME or --detach")

    print(f"before — {args.video_id} idx {lo}-{hi}")
    show(con, args.video_id, lo, hi)

    con.execute("""
        INSERT INTO speaker_override
            (video_id, start_idx, end_idx, action, name, note, author, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (args.video_id, lo, hi,
         "detach" if args.detach else "reassign",
         None if args.detach else args.name,
         args.note, args.author, "pending" if args.pending else "applied"))
    con.commit()

    print(f"\nafter — {'recorded as a proposal (changes nothing yet)' if args.pending else 'applied'}")
    show(con, args.video_id, lo, hi)
    n = con.execute("SELECT COUNT(*) FROM utterances WHERE video_id=%s AND idx BETWEEN %s AND %s",
                    (args.video_id, lo, hi)).fetchone()[0]
    print(f"\n{n} utterances corrected. This outranks every derived layer and "
          f"survives a full pipeline rebuild.")

    # The speaker's name is inside the embedding and the BM25 postings, so a
    # correction that stops at the transcript leaves search answering with the
    # old name - retrieving the passage for someone who never spoke, and
    # missing it for the person who did. Only the affected recording is
    # re-posted, and only its changed passages, so this costs about a second.
    if args.pending:
        print("Not indexed: a proposal changes nothing a reader sees until it "
              "is reviewed.")
    elif args.no_index:
        print("Skipped the index. Search and the agent still say the old name "
              "until bin/index_passages.py runs.")
    else:
        # RESOLUTION FIRST, THEN THE INDEX. speaker_resolved is a table, not
        # a view: an override written straight to speaker_override changes
        # nothing a reader sees until it is resolved into that table. Rebuild
        # the passages without this and they are rebuilt from the OLD names -
        # the correction is stored, re-indexed, and invisible. web/admin.py
        # has always done both; this path did only the second.
        import speaker_claims
        speaker_claims.refresh_video(con, args.video_id)
        import index_passages
        try:
            index_passages.refresh_video(con, args.video_id, device=args.device)
        except Exception as e:
            # Never let an indexing problem look like a failed correction: the
            # correction is committed and authoritative either way.
            print(f"\nthe correction is saved, but re-indexing failed: {e}")
            print("run bin/index_passages.py to bring search back in step")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
