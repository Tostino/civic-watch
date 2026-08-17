#!/usr/bin/env python3
"""Anchor voice clusters to named commissioners using the published roster.

The archive had no verified voiceprint for a single board member. Every
commissioner name came from the handoff announcement - "Commissioner Starkey?"
- which names whoever speaks NEXT and is wrong whenever the floor does not go
where the chair said (gotcha 34). With no anchor to correct it, the names
drifted into mixtures: 223 voices under "Mariano" resolved into 15 internally
coherent groups, and 76% of the pairs between them sat below 0.35, where no
verified same-person pair has ever been observed.

This builds the missing anchor out of two published facts and no voice model
at all:

  * `meeting_roster` records who held the CHAIR at each meeting. It is parsed
    from the roster block printed on the county's own agenda.
  * The presiding officer reads a fixed script - "now is the time for public
    comment", "all in favor say aye", "is there a motion".

So: whichever voice cluster speaks the chair's script, in the meetings where
the agenda says X chaired, is X. The office rotates annually, which is what
makes this discriminating rather than circular - the same person chairs for a
year, then somebody else does, and the cluster that follows the gavel is the
one that changes.

Measured on this archive, it disagrees with the stored names on the two largest
commissioner clusters and agrees on the rest:

    cluster 231   Oakley  89.2% of 74 chair-script lines   was labelled Mariano
    cluster 192   Starkey 92.1% of 63                      was labelled Mariano
    cluster  44   Mariano 77.8% of 482                     was labelled Mariano

An entirely independent method - the lift of each commissioner's name in the
line immediately BEFORE a cluster speaks, solved as a global assignment -
reaches the same three conclusions and agrees with the stored labels on five
more clusters. Two methods, no shared machinery, same answer.

Writes to `speaker_identity` (the DERIVED layer), never to `speaker_label`.
This is a machine inference from published facts, and it must not claim the
authority of a person having listened. A human label still outranks it, and
re-running this recomputes it from scratch.
"""
import argparse
import collections
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

# Lines only a presiding officer says. Kept literal and few: the point is
# precision, not recall - one unambiguous phrase in 74 meetings settles a
# cluster, while a loose pattern would drag in everyone who ever said "motion".
CHAIR_SCRIPT = (
    "now is the time for public comment",
    "call the meeting to order",
    "we'll call this meeting to order",
    "all in favor say aye",
    "is there a motion",
    "do i have a motion",
    "the motion carries",
    "please rise for the pledge",
)

# A cluster needs this much evidence before its name is rewritten, and the
# dominant chair needs this share of it. Both are deliberately conservative:
# the vice-chair presides when the chair is absent, so a real chair's cluster
# shows a minority of meetings under somebody else's chairmanship.
MIN_LINES = 20
MIN_SHARE = 0.70

# A cluster may only be rewritten wholesale if it is measurably ONE person.
# 25 of the 120 largest clusters are not, and stamping a chair's name across
# one of those would bury whoever else shares the bucket.
MAX_MIXED = 0.02


def evidence(con):
    """cluster -> Counter(chair surname -> chair-script lines under them)."""
    like = " OR ".join(["lower(u.text) LIKE %s"] * len(CHAIR_SCRIPT))
    rows = con.execute(f"""
        SELECT u.cluster, p.surname, COUNT(*) AS n
        FROM utterances u
        JOIN videos v ON v.id = u.video_id
        JOIN meeting_roster mr ON mr.meeting_id = v.meeting_id AND mr.office = 'chair'
        JOIN people p ON p.id = mr.person_id
        WHERE u.cluster IS NOT NULL AND ({like})
        GROUP BY 1, 2""", tuple(f"%{c}%" for c in CHAIR_SCRIPT)).fetchall()
    out = collections.defaultdict(collections.Counter)
    for r in rows:
        out[r[0]][r[1]] += r[2]
    return out


def is_one_person(con, cluster, cache):
    """Does this cluster hold a single voice, or several?

    Rewriting a whole cluster is only correct if the cluster IS one person.
    Measured over the 120 largest clusters, 95 are (under 2% of internal pairs
    below 0.35) and **25 are not** - so this cannot be assumed, and a mixed
    cluster with chair evidence would otherwise have the presiding officer's
    name stamped over everyone else sharing their bucket.
    """
    rows = con.execute(
        "SELECT video_id, local_label FROM speaker_identity WHERE cluster = %s",
        (cluster,)).fetchall()
    V = [e for r in rows if (e := embedding(r[0], r[1], cache)) is not None]
    if len(V) < 3:
        return False, 1.0
    M = np.stack(V)
    S = M @ M.T
    iu = np.triu_indices(len(V), 1)
    frac = float((S[iu] < 0.35).mean())
    return frac < MAX_MIXED, frac


def embedding(video_id, local_label, cache):
    if video_id not in cache:
        path = os.path.join(db.video_dir(video_id), "embeddings.npz")
        if not os.path.exists(path):
            cache[video_id] = None
        else:
            z = np.load(path, allow_pickle=True)
            E = z["embeddings"].astype(np.float32)
            E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-9
            cache[video_id] = {str(l): E[i] for i, l in enumerate(z["labels"])}
    m = cache[video_id]
    return None if m is None else m.get(local_label)


def decide(con, ev):
    """Clusters whose chair-script evidence names one person clearly."""
    calls, cache = [], {}
    for cluster, tally in ev.items():
        total = sum(tally.values())
        if total < MIN_LINES:
            continue
        who, n = tally.most_common(1)[0]
        share = n / total
        if share < MIN_SHARE:
            continue
        pure, frac = is_one_person(con, cluster, cache)
        if not pure:
            print(f"   skipping cluster {cluster}: {frac:.1%} of its internal pairs "
                  f"are below 0.35, so it holds more than one person")
            continue
        calls.append({"cluster": cluster, "name": who, "lines": total,
                      "share": share, "mixed": frac})
    return sorted(calls, key=lambda c: -c["lines"])


def _claim_chair(con, cluster, name):
    """The same anchor, as evidence, per contiguous run of each voice.

    Cluster-wide is how this stage WRITES - one name across every voice in a
    cluster - and it is exactly the operation utterance_speaker's header warns
    merges two people. A claim covers a span, so the same decision is recorded
    per voice and per run, which is what it always meant.
    """
    try:
        import speaker_claims
    except ImportError:
        return
    for r in con.execute("""
            WITH marked AS (
                SELECT u.video_id, u.local_label, u.idx,
                       u.idx - ROW_NUMBER() OVER (PARTITION BY u.video_id,
                                                               u.local_label
                                                  ORDER BY u.idx) AS island
                  FROM utterances u
                 WHERE u.cluster = %s AND u.local_label IS NOT NULL)
            SELECT video_id, local_label, MIN(idx) lo, MAX(idx) hi
              FROM marked GROUP BY video_id, local_label, island""", (cluster,)):
        speaker_claims.append(con, r["video_id"], r["lo"], r["hi"], name,
                              "chair", label=r["local_label"])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                    help="apply; without it, only report what would change")
    args = ap.parse_args()

    con = db.connect(autocommit=False)
    calls = decide(con, evidence(con))
    if not calls:
        print("no cluster has enough chair-script evidence to anchor")
        return 0

    changed = kept = 0
    for c in calls:
        cur = con.execute("""SELECT name, COUNT(*) FROM speaker_identity
                             WHERE cluster = %s AND name IS NOT NULL
                             GROUP BY 1 ORDER BY 2 DESC LIMIT 1""",
                          (c["cluster"],)).fetchone()
        was = cur[0] if cur else None
        n_voices = con.execute(
            "SELECT COUNT(*) FROM speaker_identity WHERE cluster = %s",
            (c["cluster"],)).fetchone()[0]
        n_lines = con.execute(
            "SELECT COUNT(*) FROM utterances WHERE cluster = %s",
            (c["cluster"],)).fetchone()[0]
        c.update(was=was, voices=n_voices, utterances=n_lines)
        if was == c["name"]:
            kept += 1
        else:
            changed += 1
        flag = "  " if was == c["name"] else "->"
        print(f"{flag} cluster {c['cluster']:5} {str(was):12} => {c['name']:12} "
              f"{c['share']:5.1%} of {c['lines']:4} chair-script lines · "
              f"{n_voices:4} voices · {n_lines:6} utterances")

    print(f"\n{kept} clusters confirmed, {changed} contradicted")
    if not args.write:
        print("dry run — pass --write to apply")
        return 0

    with con.cursor() as cur:
        for c in calls:
            # The derived layer only. A human label on any of these voices
            # still wins, because utterance_speaker consults speaker_label
            # first (R5.8.7).
            _claim_chair(con, cluster, name)
            cur.execute("""UPDATE speaker_identity SET name = %s, confidence = %s,
                                  source = 'chair'
                            WHERE cluster = %s
                              AND NOT EXISTS (SELECT 1 FROM speaker_label sl
                                   WHERE sl.video_id = speaker_identity.video_id
                                     AND sl.local_label = speaker_identity.local_label)""",
                        (c["name"], round(c["share"], 3), c["cluster"]))
    con.commit()
    print(f"applied to {len(calls)} clusters in speaker_identity (derived layer)")
    print("now re-run:  bin/affinity.py  &&  bin/index_passages.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
