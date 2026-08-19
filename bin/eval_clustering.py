"""Does voice clustering actually improve as meetings accumulate?"""
import collections
import glob
import os
import sys

import db
import speaker_id as S


def name_map(con, meetings):
    """(video_id, local_label) -> commissioner, from the text-side assignment."""
    local_votes = collections.defaultdict(collections.Counter)
    local_gavel = collections.Counter()
    for vid, m in meetings.items():
        rows = con.execute(
            'SELECT idx, start, "end", text FROM utterances WHERE video_id=%s '
            "ORDER BY idx", (vid,)).fetchall()
        lab_of = {r["idx"]: S.label_for_time(m["turns"],
                                             (r["start"] + r["end"]) / 2)
                  for r in rows}
        for r in rows:
            lab = lab_of[r["idx"]]
            if lab and S.GAVEL.search(r["text"]):
                local_gavel[(vid, lab)] += 1
        for i, r in enumerate(rows):
            if i == 0:
                continue
            lab, prev = lab_of[r["idx"]], rows[i - 1]
            if lab is None or lab_of[prev["idx"]] == lab:
                continue
            tail = prev["text"][-160:]
            names = [g for mm in S.ADDRESS.finditer(tail)
                     for g in mm.groups() if g] + S.ROLLCALL.findall(tail)
            for nm in names:
                if nm.lower() not in S.TITLES:
                    local_votes[(vid, lab)][nm.title()] += 1
    pm = S.assign_per_meeting(local_votes, local_gavel)
    return {k: v[0] for k, v in pm.items()}


def score(assign, truth):
    """Fragmentation and purity for the five commissioners."""
    by_name = collections.defaultdict(set)      # name -> clusters it occupies
    by_cluster = collections.defaultdict(collections.Counter)
    for key, nm in truth.items():
        c = assign.get(key)
        if c is None:
            continue
        by_name[nm].add(c)
        by_cluster[c][nm] += 1

    frag = {nm: len(cs) for nm, cs in by_name.items()}
    # purity: of the labelled voices in a cluster, the share held by its
    # dominant name. Below 1.0 means two people share a cluster.
    impure = 0
    total = 0
    for c, ctr in by_cluster.items():
        total += sum(ctr.values())
        impure += sum(ctr.values()) - ctr.most_common(1)[0][1]
    purity = 1 - impure / max(total, 1)

    # consolidation: share of a commissioner's voices in their biggest cluster
    cons = []
    for nm, cs in by_name.items():
        counts = [sum(1 for k, v in truth.items()
                      if v == nm and assign.get(k) == c) for c in cs]
        cons.append(max(counts) / max(sum(counts), 1))
    return frag, purity, sum(cons) / max(len(cons), 1)


def main():
    con = db.connect()
    vids = sorted(os.path.basename(os.path.dirname(p))
                  for p in glob.glob(os.path.join(S.DATA, "*", "embeddings.npz")))
    allm = {v: m for v in vids if (m := S.load_meeting(v))}
    print(f"{len(allm)} meetings with embeddings\n", flush=True)

    sizes = [n for n in (20, 40, 60, 80, 100, 120, len(allm))
             if n <= len(allm)]
    sizes = sorted(set(sizes))
    print(f"{'meetings':>9}{'voices':>8}{'clusters':>10}{'frag/comm':>11}"
          f"{'purity':>9}{'consol':>9}")
    print("-" * 56)
    for n in sizes:
        sub = {v: allm[v] for v in vids[:n]}
        assign, k = S.cluster_voices(sub)
        truth = name_map(con, sub)
        frag, purity, cons = score(assign, truth)
        avg_frag = sum(frag.values()) / max(len(frag), 1)
        print(f"{n:>9}{len(assign):>8}{k:>10}{avg_frag:>11.2f}"
              f"{purity:>9.3f}{cons:>9.2f}", flush=True)
    print("\nfrag/comm = clusters per commissioner (1.0 is perfect)")
    print("purity    = share of a cluster's voices belonging to its main person")
    print("consol    = share of a commissioner's voices in their largest cluster")


if __name__ == "__main__":
    sys.exit(main())
