"""Bake off retrieval methods on the real corpus.

The MTEB leaderboard does not cover county land-use meetings, and its two
closest legal benchmarks disagree by 41 points, so the model is chosen by
measurement here rather than by published score.

Design note on fairness: each query is written in natural language that
deliberately AVOIDS the anchor term defining its ground truth ("automatic
tracking of vehicles" for the Flock/ALPR topic). If queries contained the
anchor word, BM25 would win by construction and tell us nothing.

Ground truth is topic-level: a passage is relevant if it mentions the anchor.
That is generous, so absolute numbers matter less than the ranking between
methods, which all face the identical labels.
"""
import json
import os
import re
import sys
import time

import db

PASSAGE_WORDS = 120
TOP_K = 10

# (name, natural-language query, anchor regex defining relevance)
QUERIES = [
    ("alpr", "residents objecting to cameras that automatically record vehicles on public roads",
     r"\bflock\b|license plate|\balpr\b|plate reader"),
    ("schoolcam", "automated enforcement of speeding near schools",
     r"school zone"),
    ("impactfee", "who pays for new roads and infrastructure when development happens",
     r"impact fee"),
    ("orangebelt", "the proposed recreational trail alignment through the county",
     r"orange belt"),
    ("stormwater", "flooding and drainage problems after heavy rain",
     r"stormwater|storm water"),
    ("compplan", "amending the long range land use policy document",
     r"comprehensive plan|comp plan"),
    ("millage", "setting the property tax rate for next year",
     r"millage|ad valorem|tentative budget"),
    ("shelter", "care and adoption of stray animals",
     r"animal (shelter|services)"),
    ("row", "land the county needs to acquire alongside a road for widening",
     r"right.of.way"),
    ("variance", "a request to depart from the normal dimensional requirements",
     r"\bvariance\b"),
    ("wetland", "protecting marshy environmentally sensitive land from development",
     r"wetland"),
    ("housing", "making homes attainable for lower income working families",
     r"affordable housing"),
    ("fire", "emergency medical response and station staffing",
     r"fire rescue|\bems\b|ambulance"),
    ("water", "sewer capacity and drinking water service rates",
     r"wastewater|potable water|utility rate"),
]

MODELS = [
    ("harrier-0.6b", "microsoft/harrier-oss-v1-0.6b", "web_search_query"),
    ("qwen3-0.6b", "Qwen/Qwen3-Embedding-0.6B", "query"),
]


def build_passages(con):
    """Group consecutive utterances into ~PASSAGE_WORDS chunks."""
    rows = con.execute(
        'SELECT video_id, idx, start, "end", speaker, text FROM utterances '
        "ORDER BY video_id, idx").fetchall()
    passages, cur, words, vid = [], [], 0, None
    def flush():
        if cur:
            passages.append({
                "video_id": cur[0]["video_id"], "start": cur[0]["start"],
                "end": cur[-1]["end"],
                "text": " ".join(r["text"] for r in cur)})
    for r in rows:
        if r["video_id"] != vid or words >= PASSAGE_WORDS:
            flush()
            cur, words, vid = [], 0, r["video_id"]
        cur.append(r)
        words += len(r["text"].split())
    flush()
    return passages


def relevance(passages, pat):
    rx = re.compile(pat, re.I)
    return {i for i, p in enumerate(passages) if rx.search(p["text"])}


def metrics(ranked, relevant, k=TOP_K):
    top = ranked[:k]
    p_at_k = sum(1 for i in top if i in relevant) / k
    mrr = 0.0
    for pos, i in enumerate(ranked[:100], 1):
        if i in relevant:
            mrr = 1.0 / pos
            break
    return p_at_k, mrr


def bm25_rank(passages, query, k=100):
    """Lexical baseline over the passages, using SQLite FTS5 (BM25)."""
    import sqlite3
    mem = sqlite3.connect(":memory:")
    mem.execute("CREATE VIRTUAL TABLE p USING fts5(text, tokenize='porter unicode61')")
    mem.executemany("INSERT INTO p (rowid, text) VALUES (?,?)",
                    [(i, p["text"]) for i, p in enumerate(passages)])
    terms = " OR ".join(f'"{t}"' for t in re.findall(r"\w+", query) if len(t) > 2)
    rows = mem.execute(
        f"SELECT rowid FROM p WHERE p MATCH ? ORDER BY rank LIMIT {k}", (terms,)
    ).fetchall()
    mem.close()
    return [r[0] for r in rows]


def rrf(*rankings, k=60):
    """Reciprocal rank fusion - the standard way to blend lexical and dense."""
    score = {}
    for ranking in rankings:
        for pos, i in enumerate(ranking, 1):
            score[i] = score.get(i, 0.0) + 1.0 / (k + pos)
    return [i for i, _ in sorted(score.items(), key=lambda x: -x[1])]


def main():
    con = db.connect()
    passages = build_passages(con)
    print(f"{len(passages)} passages from "
          f"{len(set(p['video_id'] for p in passages))} meetings\n", flush=True)

    gt = {name: relevance(passages, pat) for name, _, pat in QUERIES}
    for name, q, _ in QUERIES:
        print(f"  {name:<12} {len(gt[name]):>5} relevant passages")
    print(flush=True)

    # lexical baseline
    results = {}
    bm25 = {name: bm25_rank(passages, q) for name, q, _ in QUERIES}
    results["bm25"] = [metrics(bm25[n], gt[n]) for n, _, _ in QUERIES]

    import torch
    from sentence_transformers import SentenceTransformer

    texts = [p["text"] for p in passages]
    for label, model_id, qprompt in MODELS:
        t0 = time.time()
        m = SentenceTransformer(model_id, device="cuda:1",
                                model_kwargs={"torch_dtype": torch.float16})
        doc_emb = m.encode(texts, batch_size=64, convert_to_tensor=True,
                           normalize_embeddings=True, show_progress_bar=False)
        # Queries need the model's own prompt; documents get none. Getting this
        # wrong silently degrades retrieval, so it is set explicitly per model.
        q_emb = m.encode([q for _, q, _ in QUERIES], prompt_name=qprompt,
                         convert_to_tensor=True, normalize_embeddings=True,
                         show_progress_bar=False)
        sims = (q_emb @ doc_emb.T).cpu()
        dense = [sims[i].argsort(descending=True)[:100].tolist()
                 for i in range(len(QUERIES))]
        results[label] = [metrics(dense[i], gt[n])
                          for i, (n, _, _) in enumerate(QUERIES)]
        results[f"{label}+bm25"] = [
            metrics(rrf(dense[i], bm25[n]), gt[n])
            for i, (n, _, _) in enumerate(QUERIES)]
        print(f"{label}: encoded {len(texts)} passages in "
              f"{time.time()-t0:.0f}s", flush=True)
        del m, doc_emb, sims
        torch.cuda.empty_cache()

    print(f"\n{'method':<20} {'P@10':>7} {'MRR':>7}")
    print("-" * 36)
    summary = {}
    for method, vals in results.items():
        p = sum(v[0] for v in vals) / len(vals)
        mr = sum(v[1] for v in vals) / len(vals)
        summary[method] = (p, mr)
        print(f"{method:<20} {p:>7.3f} {mr:>7.3f}")

    print(f"\nper-query P@10")
    hdr = " ".join(f"{m[:11]:>12}" for m in results)
    print(f"{'query':<12}{hdr}")
    for i, (n, _, _) in enumerate(QUERIES):
        row = " ".join(f"{results[m][i][0]:>12.2f}" for m in results)
        print(f"{n:<12}{row}")

    json.dump({"summary": summary,
               "queries": [q[0] for q in QUERIES]},
              open("eval_results.json", "w"), indent=1)


if __name__ == "__main__":
    sys.exit(main())
