"""Held-out evaluation: does anchor assignment beat blind clustering?"""
import collections
import glob
import os
import sys

import numpy as np

import anchors
import db
import speaker_id as S
from eval_clustering import name_map


def split(vids, frac=0.6):
    train = vids[:int(len(vids) * frac)]
    test = vids[int(len(vids) * frac):]
    return train, test


def score(pred, truth, keys):
    """Precision/recall per name over the given key set."""
    tp = collections.Counter()
    fp = collections.Counter()
    fn = collections.Counter()
    for k in keys:
        t = truth.get(k)
        p = pred.get(k)
        p = p[0] if isinstance(p, tuple) else p
        if t and p == t:
            tp[t] += 1
        elif t and p != t:
            fn[t] += 1
            if p:
                fp[p] += 1
        elif p and not t:
            pass                # no ground truth here; not counted either way
    names = sorted(set(tp) | set(fn))
    rows = []
    for n in names:
        rec = tp[n] / max(tp[n] + fn[n], 1)
        prec = tp[n] / max(tp[n] + fp[n], 1)
        rows.append((n, tp[n], tp[n] + fn[n], rec, prec))
    return rows


def main():
    con = db.connect()
    vids = sorted(os.path.basename(os.path.dirname(p))
                  for p in glob.glob(os.path.join(S.DATA, "*", "embeddings.npz")))
    meetings = {v: m for v in vids if (m := S.load_meeting(v))}
    vids = sorted(meetings)
    print(f"{len(meetings)} meetings with embeddings")

    truth = name_map(con, meetings)
    vecs = anchors.collect_vectors(meetings)
    train, test = split(vids)
    print(f"train {len(train)} meetings / test {len(test)} meetings")

    seed = {k: v for k, v in truth.items() if k[0] in train}
    test_keys = [k for k in vecs if k[0] in test]
    truth_test = {k: v for k, v in truth.items() if k[0] in test}
    print(f"anchors from train: {len(seed)} | "
          f"test voices with ground truth: {len(truth_test)}\n")

    print(f"{'floor':>7}{'assigned':>10}{'recall':>9}{'precision':>11}")
    print("-" * 37)
    best = None
    for floor in (0.45, 0.50, 0.55, 0.60, 0.65, 0.70):
        pred, _ = anchors.refine(vecs, seed,
                                 restrict_unique=set(n.title() for n in
                                                     S.COMMISSIONERS),
                                 floor=floor)
        rows = score(pred, truth_test, test_keys)
        if not rows:
            continue
        rec = sum(r[3] for r in rows) / len(rows)
        prec = sum(r[4] for r in rows) / len(rows)
        n_assigned = sum(1 for k in test_keys if k in pred)
        print(f"{floor:>7.2f}{n_assigned:>10}{rec:>9.2f}{prec:>11.2f}")
        if best is None or rec + prec > best[1]:
            best = (floor, rec + prec, rows)

    print(f"\nper-person at floor {best[0]:.2f} (test meetings only):")
    print(f"  {'name':<14}{'correct':>9}{'true':>7}{'recall':>9}{'prec':>8}")
    for n, tp, tot, rec, prec in sorted(best[2], key=lambda r: -r[2]):
        print(f"  {n:<14}{tp:>9}{tot:>7}{rec:>9.2f}{prec:>8.2f}")

    print("\nbaseline for comparison - blind clustering on the same corpus:")
    print("  ~14.8 clusters per commissioner, 0.68 consolidation")


if __name__ == "__main__":
    sys.exit(main())
