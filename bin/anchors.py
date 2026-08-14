"""Anchor-based speaker assignment.

Blind clustering fails on this corpus: per-recording centroids are dominated by
mic/seat/room, so the same person lands further apart across meetings than two
different people within one meeting. Measured, it fragments each commissioner
across ~15 clusters and no threshold fixes it (purity collapses before
fragmentation resolves).

This inverts the dependency. Text supervises voice:

  ANCHORS   voices whose identity is already known - from the per-meeting
            handoff assignment ("Commissioner Starkey?" then she speaks) and
            from human labels, which are authoritative.
  REFERENCE the set of anchor voiceprints for one person. Similarity is the
            MAX over that set, not the distance to their mean: a person's
            voice across 60 recordings is a cloud, not a point, and averaging
            it blurs exactly the variation that matters.
  ASSIGN    every remaining voice goes to its best-matching person above a
            similarity floor, with per-meeting matching so one person cannot
            occupy two voices in the same meeting.

Anchors then grow with each round (EM-style), so a person recognised in one
meeting becomes findable in meetings where nobody said their name.

Growth is the point and it is also the danger, so the two thresholds do
different jobs: SIM_FLOOR decides what may be REPORTED as a match, and the
higher TRUST_FLOOR decides what may become EVIDENCE for the next round. Letting
one number do both is what turned this loop into a drift machine - see the
constants below for what it cost.
"""
import collections

import numpy as np

# Measured against the 59 human-labelled voices (938 same-person pairs, 773
# different-person pairs), raw cosine separates the two cases completely:
#
#     same person      mean 0.898   p10 0.796   p25 0.872
#     different person mean 0.104   p90 0.179   p99 0.251   MAX 0.342
#
# There is nothing between 0.35 and 0.79, so the floor is not a delicate
# trade-off. At 0.70, 98.6% of same-person pairs survive and no different-
# person pair in the ground truth passes.
SIM_FLOOR = 0.70
TOP_K = 3              # average the k best anchor matches, to resist outliers
ROUNDS = 3

# A derived assignment may only become a REFERENCE for the next round if it
# scores in the same-person regime. This is the difference between growing
# anchors and drifting.
#
# Without it the loop eats itself: round 1 admits a handful of borderline
# voices, they enter the reference matrix, and in round 2 a stranger who
# resembles those strangers clears the floor against them. It compounds - the
# rounds GREW, 3507 -> 4117 -> 5035 - and "Barbara Wilhite" finished with 664
# voices across 316 clusters, of which only 48 (7%) actually resemble her 43
# confirmed voiceprints. The median assigned voice scored 0.382 against her:
# squarely a different person.
TRUST_FLOOR = 0.85


def unit(v):
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def collect_vectors(meetings):
    """(video_id, local_label) -> unit voiceprint."""
    vecs = {}
    for vid, m in meetings.items():
        for i, lab in enumerate(m["labels"]):
            v = unit(m["emb"][i].astype(np.float32))
            if np.any(v):
                vecs[(vid, str(lab))] = v
    return vecs


def build_references(vecs, anchors):
    """name -> matrix of that person's known voiceprints."""
    by_name = collections.defaultdict(list)
    for key, name in anchors.items():
        v = vecs.get(key)
        if v is not None:
            by_name[name].append(v)
    return {n: np.vstack(vs) for n, vs in by_name.items() if vs}


def similarity(vecs, keys, refs):
    """keys x names similarity, as the mean of each person's top-k matches."""
    names = sorted(refs)
    X = np.vstack([vecs[k] for k in keys])
    out = np.zeros((len(keys), len(names)), dtype=np.float32)
    for j, n in enumerate(names):
        sims = X @ refs[n].T                       # cosine, all unit vectors
        k = min(TOP_K, sims.shape[1])
        # top-k mean: robust to one bad anchor without washing out a genuine
        # match the way a full mean would
        out[:, j] = np.sort(sims, axis=1)[:, -k:].mean(axis=1)
    return names, out


def assign(vecs, refs, restrict_unique=None, floor=SIM_FLOOR):
    """Assign every voice to its best person above `floor`.

    restrict_unique: set of names that may appear at most once per meeting
    (the commissioners). Public commenters are unconstrained - several
    different people speak in one meeting and none of them recur.
    """
    from scipy.optimize import linear_sum_assignment

    if not refs:
        return {}
    keys = sorted(vecs)
    names, sims = similarity(vecs, keys, refs)

    by_meeting = collections.defaultdict(list)
    for i, (vid, lab) in enumerate(keys):
        by_meeting[vid].append(i)

    out = {}
    uniq = [j for j, n in enumerate(names)
            if restrict_unique is None or n in restrict_unique]
    free = [j for j in range(len(names)) if j not in set(uniq)]

    for vid, idxs in by_meeting.items():
        # Constrained names: optimal one-to-one matching within this meeting.
        if uniq:
            sub = sims[np.ix_(idxs, uniq)]
            cost = -sub.astype(np.float64)
            r, c = linear_sum_assignment(cost)
            for ri, ci in zip(r, c):
                if sub[ri, ci] >= floor:
                    out[keys[idxs[ri]]] = (names[uniq[ci]], float(sub[ri, ci]))
        # Unconstrained names: plain best match for anything still unassigned.
        for i in idxs:
            if keys[i] in out or not free:
                continue
            j = max(free, key=lambda j: sims[i, j])
            if sims[i, j] >= floor:
                out[keys[i]] = (names[j], float(sims[i, j]))
    return out


def refine(vecs, seed_anchors, restrict_unique=None, rounds=ROUNDS,
           floor=SIM_FLOOR):
    """Grow anchors iteratively: assign, re-reference, repeat."""
    anchors = dict(seed_anchors)
    history = []
    for _ in range(rounds):
        refs = build_references(vecs, anchors)
        got = assign(vecs, refs, restrict_unique, floor)
        history.append(len(got))
        merged = dict(seed_anchors)          # seeds always win
        for k, (name, sim) in got.items():
            # Only a match in the same-person regime earns the right to speak
            # for this person next round. A merely-acceptable match is good
            # enough to REPORT and not good enough to become evidence.
            if sim >= TRUST_FLOOR:
                merged.setdefault(k, name)
        if merged == anchors:
            break
        anchors = merged
    refs = build_references(vecs, anchors)
    return assign(vecs, refs, restrict_unique, floor), history
