"""Pass/fail check: can search reach the moment the board actually decided?

A vote is the shortest, least distinctive text in a meeting -

    "All right, we have a motion to have a second. All in favor say aye.
     Aye. Any opposed, nay."

- and it is what "what was decided about X" questions are really asking for.
It contains no topic words, so BM25 has nothing to match and its embedding
sits beside every other vote in the archive. Before agenda segmentation this
passage was unreachable at any depth, for any phrasing.

The target is addressed by (meeting, timestamp), never by passage id: ids are
reassigned on every index rebuild, so a hard-coded id silently starts pointing
at a different passage as the archive grows.

    bin/eval_votes.py            rank of each target under several phrasings
    bin/eval_votes.py --agent    also run the full agent and print its answer
"""
import argparse
import sys

import db
import retrieve

DEPTH = 200

# (video, seconds, what it is, queries that ought to find it)
TARGETS = [
    ("Icp0s5wWMeI", 609.6, "school-zone camera vote",
     ["what was decided about the school zone speed cameras",
      "vote on school zone camera program",
      "did the board approve the school zone speed cameras"]),
    ("Icp0s5wWMeI", 446.4, "school-zone camera motion",
     ["what was decided about the school zone speed cameras"]),
]


def locate(con, video_id, at):
    """The passage covering a moment, addressed by time rather than by id."""
    r = con.execute("""SELECT id, start, text FROM passages
                       WHERE video_id=%s ORDER BY ABS(start-%s) LIMIT 1""",
                    (video_id, at)).fetchone()
    return dict(r) if r else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", action="store_true", help="also run bin/ask.py")
    ap.add_argument("--device", default="cuda:1")
    args = ap.parse_args()

    con = db.connect()
    failures = 0
    for video_id, at, what, queries in TARGETS:
        p = locate(con, video_id, at)
        if not p or abs(p["start"] - at) > 30:
            print(f"MISSING  {what}: no passage near {at}s in {video_id}")
            failures += 1
            continue
        seg = con.execute("""SELECT ai.title, ai.code, ai.case_id, ai.outcome,
                                    ai.phase, ai.source
                             FROM passages p LEFT JOIN agenda_items ai
                               ON ai.id = p.agenda_item_id WHERE p.id=%s""",
                          (p["id"],)).fetchone()
        print(f"\n{what}  ->  passage {p['id']} @ {p['start']:.0f}s")
        print(f"  text : {p['text'][:100]}")
        print(f"  item : {(seg['title'] if seg else None) or '(unsegmented)'}")
        if seg and seg["code"]:
            print(f"         code {seg['code']}  case {seg['case_id']}  "
                  f"outcome {seg['outcome']}  ({seg['source']})")
        for q in queries:
            hits = retrieve.search(q, limit=DEPTH, device=args.device)
            rank = next((i for i, h in enumerate(hits, 1)
                         if h["id"] == p["id"]), None)
            ok = rank is not None
            failures += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  rank "
                  f"{rank if ok else f'>{DEPTH}'}   {q!r}")

    if args.agent:
        # The check that actually matters. Retrieval rank at depth 200 is a
        # diagnostic, NOT a pass condition: the agent reads only the top 30 per
        # query, so this eval once reported PASS while the agent was answering
        # "no decision" because the vote sat at rank 56. Assert on the
        # evidence the agent really assembled.
        import ask
        q = TARGETS[0][3][0]
        vote = locate(con, *TARGETS[0][:2])
        print(f"\n{'=' * 70}\nagent: {q}\n{'=' * 70}")
        r = ask.ask(q, device=args.device, verbose=False)
        print(r["answer"])
        print("-" * 70)
        ids = {c["id"] for c in r["evidence"]}
        for label, pid in (("vote", vote["id"]),
                           ("motion", locate(con, *TARGETS[1][:2])["id"])):
            ok = pid in ids
            failures += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  {label} passage {pid} "
                  f"{'reached' if ok else 'MISSING FROM'} the agent's evidence")
        for c in r["evidence"][:15]:
            # speaker is NULL when nobody was identified - it used to be a
            # rendered stand-in ("Group 465"), so nothing downstream expected
            # None and this line sliced it. The eval's assertions had already
            # passed by the time it raised, which is the worst way to fail.
            print(f"[{c['id']}] {c['date']} {int(c['start'] // 60):>4}m "
                  f"{ask.who(c['speaker'])[:18]:<18} {c['quote'][:58]}")

    print(f"\n{failures} failing checks")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
