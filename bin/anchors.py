"""Anchor-based speaker assignment."""
import collections

import numpy as np

# Measured against the 59 human-labelled voices (938 same-person pairs, 773
# different-person pairs), raw cosine separates the two cases completely:
SIM_FLOOR = 0.70
TOP_K = 3              # average the k best anchor matches, to resist outliers
ROUNDS = 3

# A derived assignment may only become a REFERENCE for the next round if it
# scores in the same-person regime. This is the difference between growing
# anchors and drifting.
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
    """Assign every voice to its best person above `floor`."""
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
