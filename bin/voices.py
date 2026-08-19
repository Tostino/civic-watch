#!/usr/bin/env python3
"""Separate the people hiding under one name, and let a human name them.

A name in this archive is not one voice. Measured over the voiceprints stored
for each name, with an edge at cosine 0.79 - the p10 of human-verified
same-person pairs:

    Barbara Wilhite  111 voices ->  10 groups, largest 102 (92%)   43 human labels
    Girardi          150 voices ->   6 groups, largest 103 (69%)    no labels
    Mariano          223 voices ->  15 groups, largest  69 (31%)    no labels
    Oakley           275 voices ->  16 groups, largest 103 (37%)    no labels
    Starkey          261 voices ->  18 groups, largest 103 (39%)    no labels

Every group is internally coherent at 0.88-0.91, which is the same-person
regime; pairs ACROSS groups sit under 0.35, where no verified same-person pair
has ever been observed. So these are genuinely different people wearing one
name, and the difference between Wilhite and the rest is not the method - it is
that somebody once labelled her voice and nobody ever labelled a commissioner's.

The machine can separate them. It cannot name them: `anchors.py` needs an
anchor, and for commissioners the only anchor was the handoff announcement
("Commissioner Starkey?"), which names whoever speaks NEXT and is wrong
whenever the floor does not go where the chair said.

So this does the half a machine can do - clustering the voices and pulling a
playable sample from each - and asks a person for the half it cannot. One
listen per group labels every voice in it, and a human label outranks the
entire pipeline and survives every rebuild.

    bin/voices.py groups Mariano            # what is actually under the name
    bin/voices.py groups Mariano --play     # with a YouTube link per group
    bin/voices.py assign Mariano --group 1 --as Mariano
    bin/voices.py assign Mariano --group 3 --as Oakley
    bin/voices.py assign Mariano --group 7 --clear     # not a commissioner
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

# p10 of human-verified same-person pairs (0.796). Different-person pairs top
# out at 0.342 in the same ground truth, so anywhere in 0.35-0.79 is empty and
# the threshold is not a delicate trade-off. See bin/anchors.py.
LINK = 0.79
SAMPLE_MIN_CHARS = 60


def load(video_id, cache):
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


def groups_for(con, name):
    """The distinct people currently sharing one name, largest first."""
    rows = con.execute("""
        SELECT si.video_id, si.local_label FROM speaker_identity si
        WHERE si.name = %s AND si.cluster IS NOT NULL
        ORDER BY si.video_id, si.local_label""", (name,)).fetchall()
    cache = {}
    voices = [(r[0], r[1], e) for r in rows
              if (e := (load(r[0], cache) or {}).get(r[1])) is not None]
    if not voices:
        return []

    M = np.stack([v[2] for v in voices])
    adj = (M @ M.T) >= LINK
    n = len(voices)
    comp = [-1] * n
    out = []
    for i in range(n):
        if comp[i] >= 0:
            continue
        gid = len(out)
        stack, members = [i], []
        comp[i] = gid
        while stack:
            u = stack.pop()
            members.append(u)
            for v in np.nonzero(adj[u])[0]:
                if comp[v] < 0:
                    comp[v] = gid
                    stack.append(int(v))
        sub = (M[members] @ M[members].T)
        iu = np.triu_indices(len(members), 1)
        out.append({
            "voices": [(voices[m][0], voices[m][1]) for m in members],
            "coherence": float(sub[iu].mean()) if len(members) > 1 else 1.0,
        })
    out.sort(key=lambda g: len(g["voices"]), reverse=True)
    return out


# No name-mining from the text here, deliberately.


def describe(con, g):
    """Reach and a playable sample, so a person can judge the group in one listen."""
    pairs = g["voices"]
    n_lines = 0
    for vid, lab in pairs:
        n_lines += con.execute(
            "SELECT COUNT(*) FROM utterances WHERE video_id=%s AND local_label=%s",
            (vid, lab)).fetchone()[0]
    span = con.execute("""
        SELECT MIN(v.upload_date), MAX(v.upload_date)
        FROM videos v WHERE v.id = ANY(%s)""", ([p[0] for p in pairs],)).fetchone()
    # The longest thing anyone in the group said: the most identifiable clip.
    best = None
    for vid, lab in pairs[:40]:
        r = con.execute("""
            SELECT video_id, start, text FROM utterances
            WHERE video_id=%s AND local_label=%s AND LENGTH(text) >= %s
            ORDER BY LENGTH(text) DESC LIMIT 1""",
            (vid, lab, SAMPLE_MIN_CHARS)).fetchone()
        if r and (best is None or len(r[2]) > len(best[2])):
            best = (r[0], r[1], r[2])
    g.update(lines=n_lines, first=span[0], last=span[1], sample=best)
    return g


def _claim_label(con, members, name):
    """A human label, recorded once as evidence rather than twice as data."""
    try:
        import speaker_claims
    except ImportError:
        return
    runs = speaker_claims.runs_by_voice(con)
    cur = con.cursor()
    for vid, lab in members:
        # The label for a voice is REPLACED, never added to - the same as
        # speaker_label itself - so the previous answer goes first. Without
        # this, relabelling a voice leaves the old name as a live claim of
        # equal rank and the resolver picks between them on recency.
        cur.execute("DELETE FROM speaker_claim WHERE method = 'label' "
                    "AND video_id = %s AND local_label = %s", (vid, lab))
        for lo, hi in runs.get((vid, lab), []):
            speaker_claims.append(con, vid, lo, hi, name, "label", label=lab)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("groups", help="the distinct people under one name")
    p.add_argument("name")
    p.add_argument("--play", action="store_true", help="print a YouTube link per group")
    p.add_argument("--limit", type=int, default=12)

    p = sub.add_parser("assign", help="label every voice in a group (human, permanent)")
    p.add_argument("name")
    p.add_argument("--group", type=int, required=True, help="the number shown by `groups`")
    p.add_argument("--as", dest="new", help="who this group really is")
    p.add_argument("--clear", action="store_true", help="not this person; leave unidentified")
    p.add_argument("--note")

    args = ap.parse_args()
    con = db.connect(autocommit=False)
    gs = groups_for(con, args.name)
    if not gs:
        sys.exit(f"no voiceprints stored for {args.name!r}")

    if args.cmd == "groups":
        total = sum(len(g["voices"]) for g in gs)
        print(f"{args.name}: {total} voices in {len(gs)} distinct groups "
              f"(edge at cosine {LINK})\n")
        for i, g in enumerate(gs[:args.limit], 1):
            describe(con, g)
            print(f"  [{i}] {len(g['voices']):4} voices · {g['lines']:6,} utterances · "
                  f"{g['first']} .. {g['last']} · internal {g['coherence']:.3f}")
            if g["sample"]:
                vid, start, text = g["sample"]
                print(f"       \"{text[:96]}\"")
                if args.play:
                    print(f"       https://www.youtube.com/watch?v={vid}&t={int(start)}s")
        if len(gs) > args.limit:
            rest = sum(len(g["voices"]) for g in gs[args.limit:])
            print(f"  … {len(gs) - args.limit} smaller groups, {rest} voices")
        print(f"\nListen to one sample per group, then:")
        print(f"  bin/voices.py assign {args.name} --group N --as \"Real Name\"")
        print(f"  bin/voices.py assign {args.name} --group N --clear")
        return 0

    if args.group < 1 or args.group > len(gs):
        sys.exit(f"group {args.group} does not exist (1..{len(gs)})")
    if args.clear == bool(args.new):
        sys.exit("give exactly one of --as NAME or --clear")

    g = describe(con, gs[args.group - 1])
    members = g["voices"]
    print(f"group {args.group}: {len(members)} voices · {g['lines']:,} utterances · "
          f"{g['first']} .. {g['last']}")
    if g["sample"]:
        print(f'  "{g["sample"][2][:96]}"')

    with con.cursor() as cur:
        if args.new:
            # A human label, keyed on (video_id, local_label) - the voice, and
            # the only identifier that survives re-clustering.
            cur.executemany(
                "INSERT INTO speaker_label (video_id, local_label, name, note) "
                "VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (video_id, local_label) DO UPDATE SET "
                "name=EXCLUDED.name, note=EXCLUDED.note, labeled_at=now()",
                [(v, l, args.new, args.note) for v, l in members])
            cur.executemany(
                "UPDATE speaker_identity SET name=%s, confidence=1.0 "
                "WHERE video_id=%s AND local_label=%s",
                [(args.new, v, l) for v, l in members])
            _claim_label(con, members, args.new)
        else:
            cur.executemany("DELETE FROM speaker_label WHERE video_id=%s AND local_label=%s",
                            members)
            cur.executemany(
                "UPDATE speaker_identity SET name=NULL, confidence=NULL "
                "WHERE video_id=%s AND local_label=%s", members)
    con.commit()
    print(f"\n{'labelled as ' + args.new if args.new else 'cleared'} — "
          f"{len(members)} voices, {g['lines']:,} utterances")
    print("This is a human label: it outranks every derived layer and survives "
          "a full pipeline rebuild.")
    print("Re-run bin/affinity.py and bin/index_passages.py to carry it into "
          "search and the agent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
