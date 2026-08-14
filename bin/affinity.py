#!/usr/bin/env python3
"""Does this voice actually sound like the person its cluster is named after?

Most speakers get a name only because their voice landed in a cluster that
someone else in it was named. That is a structural assumption - cluster
membership means same person - and clustering on this corpus is known not to
hold it: per-recording centroids are dominated by mic, seat and room, which is
the whole reason anchors replaced blind clustering (see bin/anchors.py).

So the assumption is measured instead of trusted. For every voice that could
inherit a name from its cluster, this scores it against the voices in that
cluster that carry the name, and stores the number.

Measured over all 1,836 such voices, the result is as sharply bimodal as the
human-labelled ground truth:

    below 0.20   416      the flat "different person" floor
    0.20-0.35     60
    0.35-0.70     10      the entire ambiguous zone
    0.70-0.85    130
    above 0.85  1220

486 of 1,836 (26.5%) fall below the floor, and 476 of those sit in the range
where no same-person pair has ever been observed. The fallback is not slightly
noisy; it is confidently wrong about a quarter of the voices it names, and the
transcripts say so out loud - one voice labelled "Development Review Director"
at similarity -0.069 opens with "Christina Cordon, Assistant Director of Parks,
Recreation".

    bin/affinity.py            # compute for every video
    bin/affinity.py --video X  # one recording
    bin/affinity.py --report   # the distribution, without writing
"""
import argparse
import collections
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
from anchors import SIM_FLOOR, TOP_K  # noqa: E402


def load(video_id, cache):
    """Unit-normalised speaker embeddings for one recording, by local_label."""
    if video_id in cache:
        return cache[video_id]
    path = os.path.join(db.video_dir(video_id), "embeddings.npz")
    if not os.path.exists(path):
        cache[video_id] = None
        return None
    z = np.load(path, allow_pickle=True)
    E = z["embeddings"].astype(np.float32)
    E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-9
    cache[video_id] = {str(lab): E[i] for i, lab in enumerate(z["labels"])}
    return cache[video_id]


def compute(con, only=None):
    """(video, local_label, cluster, name, similarity) for every inheritable voice."""
    # Who carries each name in each cluster - the reference set a candidate is
    # scored against.
    named = collections.defaultdict(list)
    for r in con.execute("""SELECT video_id, local_label, cluster, name
                            FROM speaker_identity
                            WHERE name IS NOT NULL AND cluster IS NOT NULL"""):
        named[(r[2], r[3])].append((r[0], r[1]))

    # The name a cluster would hand out, and to whom. voice_name already
    # applies the roster guard, so this asks only the question that is left:
    # is this the same person's voice?
    args = (only,) if only else ()
    rows = con.execute(f"""
        SELECT DISTINCT u.video_id, u.local_label, u.cluster, vn.name
        FROM utterances u
        JOIN voice_name vn ON vn.video_id = u.video_id AND vn.cluster = u.cluster
        WHERE u.cluster IS NOT NULL AND vn.name IS NOT NULL
        {'AND u.video_id = %s' if only else ''}""", args).fetchall()

    cache, out, skipped = {}, [], 0
    for r in rows:
        vid, lab, cl, name = r[0], r[1], r[2], r[3]
        m = load(vid, cache)
        v = m.get(lab) if m else None
        if v is None:
            skipped += 1
            continue
        # Never score a voice against itself, or every named voice trivially
        # passes at 1.0 and the check proves nothing.
        refs = []
        for rvid, rlab in named.get((cl, name), ()):
            if (rvid, rlab) == (vid, lab):
                continue
            rm = load(rvid, cache)
            e = rm.get(rlab) if rm else None
            if e is not None:
                refs.append(e)
        if not refs:
            skipped += 1
            continue
        # Max over the reference set, softened by averaging the best few: a
        # person's voice across 60 recordings is a cloud, not a point, and its
        # mean blurs exactly the variation that matters (bin/anchors.py).
        scores = np.stack(refs) @ v
        out.append((vid, lab, cl, name,
                    float(np.sort(scores)[-TOP_K:].mean())))
    return out, skipped


def report(rows):
    a = np.array([s for *_, s in rows])
    if not len(a):
        print("nothing to report")
        return
    print(f"{len(a):,} voices scored against the named voices of their own cluster\n")
    bands = [(-1, .2), (.2, .35), (.35, .5), (.5, .6), (.6, .7),
             (.7, .79), (.79, .85), (.85, .9), (.9, 1.01)]
    for lo, hi in bands:
        n = int(((a >= lo) & (a < hi)).sum())
        print(f"  {lo:5.2f}–{hi:4.2f}  {n:6}  {'█' * int(60 * n / len(a))}")
    bad = int((a < SIM_FLOOR).sum())
    print(f"\n  below SIM_FLOOR {SIM_FLOOR}: {bad:,} ({100 * bad / len(a):.1f}%)")
    print(f"  in the ground-truth different-person range (<0.35): "
          f"{int((a < 0.35).sum()):,}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video")
    ap.add_argument("--report", action="store_true",
                    help="show the distribution and write nothing")
    args = ap.parse_args()

    con = db.connect(autocommit=False)
    rows, skipped = compute(con, args.video)
    report(rows)
    if skipped:
        print(f"  ({skipped} voices had no embedding or no reference to score against)")
    if args.report:
        return 0

    with con.cursor() as cur:
        if args.video:
            cur.execute("DELETE FROM voice_affinity WHERE video_id = %s", (args.video,))
        else:
            cur.execute("TRUNCATE voice_affinity")
        cur.executemany(
            "INSERT INTO voice_affinity (video_id, local_label, cluster, name, similarity) "
            "VALUES (%s,%s,%s,%s,%s)", rows)
    con.commit()
    print(f"\nwrote {len(rows):,} rows to voice_affinity")
    print(f"the cluster fallback now applies only at or above {SIM_FLOOR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
