"""Does respecting speaker boundaries improve retrieval?"""
import re
import sys
import time

import db
from eval_embed import QUERIES, TOP_K, bm25_rank, metrics, relevance, rrf

MAX_WORDS = 140
MIN_WORDS = 25


def chunk_speaker_blind(rows, max_words=120):
    out, cur, words, vid = [], [], 0, None
    def flush():
        if cur:
            out.append({"video_id": cur[0]["video_id"], "start": cur[0]["start"],
                        "speaker": None,
                        "text": " ".join(r["text"] for r in cur)})
    for r in rows:
        if r["video_id"] != vid or words >= max_words:
            flush(); cur, words, vid = [], 0, r["video_id"]
        cur.append(r); words += len(r["text"].split())
    flush()
    return out


def chunk_speaker_bounded(rows, max_words=MAX_WORDS, min_words=MIN_WORDS):
    """One speaker per passage. Long turns split at sentence boundaries."""
    out = []
    cur, words, key = [], 0, None
    def flush():
        nonlocal cur, words
        if not cur:
            return
        text = " ".join(r["text"] for r in cur)
        base = {"video_id": cur[0]["video_id"], "start": cur[0]["start"],
                "speaker": cur[0]["speaker"]}
        if len(text.split()) <= max_words:
            out.append({**base, "text": text})
        else:
            # split a long monologue at sentence ends, keeping one speaker
            sents = re.split(r"(?<=[.?!])\s+", text)
            buf, n = [], 0
            for s in sents:
                buf.append(s); n += len(s.split())
                if n >= max_words:
                    out.append({**base, "text": " ".join(buf)}); buf, n = [], 0
            if buf:
                if n < min_words and out and out[-1]["speaker"] == base["speaker"]:
                    out[-1]["text"] += " " + " ".join(buf)
                else:
                    out.append({**base, "text": " ".join(buf)})
        cur, words = [], 0
    for r in rows:
        k = (r["video_id"], r["speaker"])
        if k != key or words >= max_words:
            flush(); key = k
        cur.append(r); words += len(r["text"].split())
    flush()
    return out


def chunk_speaker_floor(rows, max_words=MAX_WORDS, floor=35):
    """Speaker-bounded, but fragments below `floor` words are not indexed.

    Pure speaker-bounding shatters the corpus into thousands of "Here" and
    "Second" turns: attributable but semantically empty, and they crowd out
    real content in the top-k. Those moments stay in the transcript and remain
    reachable by timestamp; they are simply not retrieval units.
    """
    return [p for p in chunk_speaker_bounded(rows, max_words, floor)
            if len(p["text"].split()) >= floor]


def evaluate(passages, label, model):
    import torch
    gt = {n: relevance(passages, pat) for n, _, pat in QUERIES}
    bm = {n: bm25_rank(passages, q) for n, q, _ in QUERIES}
    texts = [p["text"] for p in passages]
    doc = model.encode(texts, batch_size=64, convert_to_tensor=True,
                       normalize_embeddings=True, show_progress_bar=False)
    qe = model.encode([q for _, q, _ in QUERIES], prompt_name="web_search_query",
                      convert_to_tensor=True, normalize_embeddings=True,
                      show_progress_bar=False)
    sims = (qe @ doc.T).cpu()
    dense = [sims[i].argsort(descending=True)[:100].tolist()
             for i in range(len(QUERIES))]
    hyb = [metrics(rrf(dense[i], bm[n]), gt[n])
           for i, (n, _, _) in enumerate(QUERIES)]
    den = [metrics(dense[i], gt[n]) for i, (n, _, _) in enumerate(QUERIES)]
    lex = [metrics(bm[n], gt[n]) for n, _, _ in QUERIES]
    del doc, sims
    torch.cuda.empty_cache()
    avg = lambda v, j: sum(x[j] for x in v) / len(v)
    print(f"\n{label}: {len(passages)} passages, "
          f"median {sorted(len(p['text'].split()) for p in passages)[len(passages)//2]} words")
    print(f"  {'method':<12} {'P@10':>7} {'MRR':>7}")
    for nm, v in (("bm25", lex), ("dense", den), ("hybrid", hyb)):
        print(f"  {nm:<12} {avg(v,0):>7.3f} {avg(v,1):>7.3f}")
    return {"hybrid_p": avg(hyb, 0), "hybrid_mrr": avg(hyb, 1)}


def main():
    import torch
    from sentence_transformers import SentenceTransformer

    con = db.connect()
    rows = con.execute(
        'SELECT video_id, idx, start, "end", speaker, text FROM utterances '
        "ORDER BY video_id, idx").fetchall()
    print(f"{len(rows)} utterances")

    a = chunk_speaker_blind(rows)
    b = chunk_speaker_bounded(rows)
    single = sum(1 for p in b if p["speaker"])
    print(f"A speaker-blind : {len(a)} passages (speakers mixed)")
    print(f"B speaker-bound : {len(b)} passages, {single} attributable")

    model = SentenceTransformer("microsoft/harrier-oss-v1-0.6b", device="cuda:1",
                                model_kwargs={"torch_dtype": torch.float16})
    c = chunk_speaker_floor(rows)
    print(f"C speaker+floor : {len(c)} passages, all attributable")

    ra = evaluate(a, "A speaker-blind", model)
    rb = evaluate(b, "B speaker-bounded", model)
    rc = evaluate(c, "C speaker+floor", model)
    print(f"\nhybrid vs A:  B {rb['hybrid_p']-ra['hybrid_p']:+.3f} P@10 / "
          f"{rb['hybrid_mrr']-ra['hybrid_mrr']:+.3f} MRR")
    print(f"              C {rc['hybrid_p']-ra['hybrid_p']:+.3f} P@10 / "
          f"{rc['hybrid_mrr']-ra['hybrid_mrr']:+.3f} MRR")


if __name__ == "__main__":
    sys.exit(main())
