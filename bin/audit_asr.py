"""Did ASR silently drop speech?"""
import argparse
import json
import os
import sys

import db

# A turn must lose at least this much to count. Diarization edges are fuzzy by
# a few hundred ms and ASR trims leading/trailing silence, so smaller holes are
# disagreement about boundaries rather than lost speech.
MIN_HOLE = 2.0


def turns_for(video_id):
    path = os.path.join(db.video_dir(video_id), "diarization.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f).get("turns", [])


def uncovered(turns, words):
    """Speech-time diarization found that no utterance covers.

    Both sides are merged into flat intervals first: turns overlap each other
    constantly (two people talking), and treating them individually would
    count the same second several times over.
    """
    def merge(spans):
        out = []
        for s, e in sorted(spans):
            if out and s <= out[-1][1]:
                out[-1][1] = max(out[-1][1], e)
            else:
                out.append([s, e])
        return out

    speech = merge([(t["start"], t["end"]) for t in turns])
    said = merge(words)
    holes, i = [], 0
    for s, e in speech:
        # Walk the transcript intervals that touch this speech interval and
        # collect whatever is left uncovered.
        cur = s
        while i < len(said) and said[i][1] <= s:
            i += 1
        j = i
        while j < len(said) and said[j][0] < e:
            if said[j][0] > cur:
                holes.append((cur, min(said[j][0], e)))
            cur = max(cur, said[j][1])
            j += 1
        if cur < e:
            holes.append((cur, e))
    return [(s, e) for s, e in holes if e - s >= MIN_HOLE]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    con = db.connect(autocommit=True)

    where = "AND id = %s" % "%s" if args.video else ""
    rows = con.execute(
        f"SELECT id, title, duration, gap_seconds FROM videos "
        f"WHERE transcribed {where} ORDER BY gap_seconds DESC NULLS LAST",
        (args.video,) if args.video else ()).fetchall()
    if args.limit:
        rows = rows[:args.limit]

    tot_speech = tot_lost = 0.0
    worst = []
    for v in rows:
        turns = turns_for(v["id"])
        if turns is None:
            continue
        words = [(r["start"], r["end"]) for r in con.execute(
            'SELECT start, "end" FROM utterances WHERE video_id=%s ORDER BY idx',
            (v["id"],))]
        speech = sum(e - s for s, e in
                     [(t["start"], t["end"]) for t in turns]) if turns else 0
        holes = uncovered(turns, words)
        lost = sum(e - s for s, e in holes)
        tot_speech += speech
        tot_lost += lost
        worst.append((lost, v, holes))
        if args.video:
            print(f"{v['title'][:60]}  duration {v['duration']/60:.0f}m")
            print(f"  diarized speech {speech/60:.1f}m · "
                  f"unspoken-for {lost/60:.1f}m in {len(holes)} holes")
            for s, e in sorted(holes, key=lambda h: h[1] - h[0],
                               reverse=True)[:20]:
                print(f"    {int(s)//60:>3}m{int(s)%60:02d}s  "
                      f"{e - s:>6.1f}s lost")

    if not args.video:
        worst.sort(reverse=True, key=lambda x: x[0])
        print(f"{'video':<14} {'lost':>8} {'holes':>6}  title")
        for lost, v, holes in worst[:25]:
            print(f"{v['id']:<14} {lost/60:>7.1f}m {len(holes):>6}  "
                  f"{v['title'][:44]}")
        print(f"\n{len(worst)} videos · diarized speech "
              f"{tot_speech/3600:.1f}h · not transcribed {tot_lost/3600:.2f}h "
              f"({100 * tot_lost / tot_speech if tot_speech else 0:.2f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
