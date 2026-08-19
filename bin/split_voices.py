#!/usr/bin/env python3
"""Find one person cut into two voices, using the names as the evidence.

A name is assigned PER VOICE CLUSTER, so two different names can never share a
cluster - measured, 0 of 1,601 candidate pairs do. That makes near-identical
names a free and high-precision detector for the opposite problem: the diarizer
split one person in two, and each half was named independently from its own
self-introduction.

    'Girardi' (51,138 utterances)  and  'Gerardi' (28)
    'Kathy Julian' (72)            and  'Kathryn Julian' (7)
    'Julia Bartunik' (167)         and  'Julia Bartonick' (11)

...and the reason the voice signal is not optional: 'Christopher Poole' (1,604)
and 'Christopher Pohl' (43) look like the same obvious ASR miss and are 0.896
apart acoustically, which is further than a random pair of strangers. On names
alone this tool would have merged two people.

TWO SIGNALS, because neither is enough alone.

  NAME    cheap, and wrong in both directions. difflib scores
          'Linda Bell'/'Linda Snell' exactly as high as
          'Christopher Poole'/'Christopher Pohl', and the first pair is
          probably two residents. It also calls 'Michael Racor'/'Mike Razor'
          two people, which is a nickname plus an ASR miss.

  MEETINGS  free, and informative in one direction only. Two different people who both
          speak regularly will eventually sit in the same room; one person cut
          in two never co-occurs with himself. 67 of the 77 candidate pairs
          never share a meeting, and the ones that do are the genuinely
          different people.

          The clearest form is a lopsided pair: a name with one meeting
          against a name with a hundred. 'Christopher Pohl' speaks 43 times at
          the Planning Commission on 2020-10-08 and nowhere else in twelve
          years; 'Christopher Poole' speaks at 102 meetings, is on the
          published roster as Christopher B. Poole, was seated from
          2020-01-23 - and says NOTHING at all on 2020-10-08. A seated member
          silent at a meeting his near-namesake talks through is the same man
          on a different microphone. One of Pohl's 43 lines is "I'm being told
          that your microphone is on."

  VOICE   cosine between the two clusters' centroids, from the same pyannote
          embeddings speaker_id.py clusters over. WEAK, and it was trusted too
          far in the first version of this tool. Across the 71 name-similar
          pairs the medians barely separate - 0.866 against 0.913 for random
          pairs - and Poole/Pohl sit 0.896 apart, further than two strangers,
          because a different microphone moves a centroid further than a
          different person does. Cosine breaks ties. It does not decide.

Nothing here writes. It prints a ranked queue for a human or the admin console,
because merging two voices renames every utterance in one of them - 51,138 in
the Girardi case - and that is not a decision to take on a string ratio.
"""
import argparse
import difflib
import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np                                           # noqa: E402
import db                                                    # noqa: E402
import speaker_id as sid                                     # noqa: E402

NAME_MIN = 0.78      # below this the names are not evidence of anything
LOPSIDED = 3         # one side appears in <= this many meetings, the other in
                     # many more: the signature of a one-off mis-transcription
PART_MIN = 0.55      # EVERY part of the name must survive, not just the whole.
                     # 'Dan Mcdonald' and 'Leanne Mcdonald' score 0.81 overall
                     # on a shared surname and are two different people;
                     # 'Christopher Poole' and 'Christopher Pohl' agree on the
                     # first name AND rhyme on the second, which is what an ASR
                     # miss looks like.

# Never merge these. A role is not a person, and two of them are not one person
# with a mis-heard name.
ROLES = {"county commissioner", "commissioner", "planning commission",
         "planning commissioner", "board member", "county attorney",
         "county administrator", "clerk", "madam clerk", "chairman", "chair",
         "staff", "county staff", "applicant", "speaker"}


def load(con):
    """Named clusters, their members, and their utterance weight."""
    mem, name = {}, {}
    for r in con.execute("""SELECT cluster, video_id, local_label, name
                              FROM speaker_identity WHERE cluster IS NOT NULL"""):
        mem.setdefault(r[0], []).append((r[1], str(r[2])))
        if r[3]:
            name[r[0]] = r[3]
    weight = {r[0]: r[1] for r in con.execute(
        """SELECT cluster, count(*) FROM utterances
            WHERE cluster IS NOT NULL GROUP BY cluster""")}
    return mem, name, weight


def centroids(mem, name):
    """Unit-norm mean embedding per named cluster, from data/*/embeddings.npz."""
    need = {v for c in name for v, _ in mem.get(c, ())}
    emb = {}
    for v in sorted(need):
        m = sid.load_meeting(v)
        if m:
            emb[v] = dict(zip(m["labels"], m["emb"]))
    out = {}
    for c in name:
        vs = [emb[v][l] for v, l in mem.get(c, ()) if v in emb and l in emb[v]]
        if not vs:
            continue
        x = np.mean(np.vstack(vs), axis=0)
        n = np.linalg.norm(x)
        if n:
            out[c] = x / n
    return out


def pairs(name, cent, weight, roster, name_min, total, seen):
    """Candidate splits, ranked by how much they would fix."""
    out = []
    for a, b in itertools.combinations(sorted(cent), 2):
        na, nb = name[a], name[b]
        if na == nb:
            continue
        la, lb = na.lower(), nb.lower()
        # Two roles, or a role and a person, are never the same voice split.
        if la in ROLES or lb in ROLES:
            continue
        # Two people the county published on the same board are two people.
        if la in roster and lb in roster:
            continue
        r = difflib.SequenceMatcher(None, la, lb).ratio()
        if r < name_min:
            continue
        # A shared surname with a different given name is a family, not a
        # transcription error. Compare the parts, not just the string.
        ta, tb = la.split(), lb.split()
        if len(ta) > 1 and len(tb) > 1:
            first = difflib.SequenceMatcher(None, ta[0], tb[0]).ratio()
            last = difflib.SequenceMatcher(None, ta[-1], tb[-1]).ratio()
            if min(first, last) < PART_MIN:
                continue
        d = float(1 - np.dot(cent[a], cent[b]))
        # ORDER OF EVIDENCE: co-occurrence, then lopsidedness, then voice.
        # Sharing a room is close to proof of being two people; a centroid is
        # about the microphone as much as the throat.
        # NO VERDICT. Four thresholds were tried here and each one had a clean
        # counterexample in this archive:
        ma, mb = seen.get(na, set()), seen.get(nb, set())
        band = "co-occurs" if ma & mb else (
            "one-off" if min(len(ma), len(mb)) <= LOPSIDED else "recurring")
        # The bigger cluster's spelling wins: it is the one more speech agrees
        # on, and it is the one already used everywhere else in the archive.
        wa, wb = weight.get(a, 0), weight.get(b, 0)
        keep, drop = (a, b) if wa >= wb else (b, a)
        out.append({"band": band, "cos": d, "name_ratio": r,
                    "keep": keep, "drop": drop,
                    "keep_name": name[keep], "drop_name": name[drop],
                    "keep_n": max(wa, wb), "drop_n": min(wa, wb)})
    # AGGREGATE BY NAME PAIR, not by cluster pair. One person legitimately
    # holds many clusters - Mariano has 14, Oakley 17 - because a per-recording
    # centroid is dominated by mic, seat and room (see speaker_id.py). Emitting
    # a row per cluster combination printed 'Girardi / Gerardi' four times with
    # four different weights and buried the actual queue.
    best = {}
    for p in out:
        k = tuple(sorted((p["keep_name"], p["drop_name"])))
        if k not in best or p["cos"] < best[k]["cos"]:
            best[k] = p
    merged = list(best.values())
    # ...and the weight is the whole NAME's speech, since merging renames all
    # of it, not just the one cluster that matched.
    for p in merged:
        p["keep_n"] = total.get(p["keep_name"], p["keep_n"])
        p["drop_n"] = total.get(p["drop_name"], p["drop_n"])
        if p["drop_n"] > p["keep_n"]:
            p["keep_name"], p["drop_name"] = p["drop_name"], p["keep_name"]
            p["keep_n"], p["drop_n"] = p["drop_n"], p["keep_n"]
    # Rank by what it FIXES - the utterances that would change name - not by
    # how confident the match is. A 0.38 match on 3 utterances is not the work.
    merged.sort(key=lambda p: (p["band"] != "same", -p["drop_n"], p["cos"]))
    return merged


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name-min", type=float, default=NAME_MIN)
    ap.add_argument("--band", choices=("co-occurs", "one-off", "recurring", "all"),
                    default="all")
    ap.add_argument("--limit", type=int, default=40)
    a = ap.parse_args()

    con = db.connect()
    mem, name, weight = load(con)
    roster = {r[0].lower() for r in con.execute(
        "SELECT DISTINCT surname FROM people WHERE surname IS NOT NULL")}
    print(f"{len(name):,} named clusters; loading centroids...", flush=True)
    cent = centroids(mem, name)
    print(f"  centroids for {len(cent):,}\n")

    # utterances per NAME, across every cluster that carries it
    total = {}
    for r in con.execute('''
        WITH nc AS (SELECT DISTINCT name, cluster FROM speaker_identity
                     WHERE name IS NOT NULL AND cluster IS NOT NULL),
             w  AS (SELECT cluster, count(*)::int n FROM utterances
                     WHERE cluster IS NOT NULL GROUP BY cluster)
        SELECT nc.name, sum(w.n)::int FROM nc JOIN w ON w.cluster = nc.cluster
         GROUP BY 1'''):
        total[r[0]] = int(r[1])
    # which meetings each NAME is heard in - the co-occurrence signal
    seen = {}
    for r in con.execute('''
        SELECT si.name, v.meeting_id FROM speaker_identity si
          JOIN utterances u ON u.cluster = si.cluster
          JOIN videos v ON v.id = u.video_id
         WHERE si.name IS NOT NULL AND v.meeting_id IS NOT NULL
         GROUP BY 1,2'''):
        seen.setdefault(r[0], set()).add(r[1])
    ps = pairs(name, cent, weight, roster, a.name_min, total, seen)
    counts = {}
    for p in ps:
        counts[p["band"]] = counts.get(p["band"], 0) + 1
    print(f"{len(ps)} candidate pairs   "
          + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("  co-occurs = seen in a shared meeting · one-off = one side speaks "
          "at <=3 meetings")
    print("  NO VERDICT IS ASSERTED. Every threshold tried had a counterexample "
          "- see the module docstring.\n")

    show = [p for p in ps if a.band in ("all", p["band"])][:a.limit]
    print(f"{'band':<10}{'cos':>6}{'name':>6}  {'keep':<26}{'':>7}  "
          f"{'merge in':<26}{'':>7}")
    for p in show:
        print(f"{p['band']:<10}{p['cos']:>6.3f}{p['name_ratio']:>6.2f}  "
              f"{p['keep_name'][:26]:<26}{p['keep_n']:>7,d}  "
              f"{p['drop_name'][:26]:<26}{p['drop_n']:>7,d}")
    print(f"\n{sum(p['drop_n'] for p in ps):,} utterances sit under the smaller "
          f"name of a pair - the ceiling on what adjudication could correct.")
    print("Nothing was written, and nothing is decided. Merging renames every "
          "utterance in the dropped cluster.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
