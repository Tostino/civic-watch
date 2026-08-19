"""Hybrid retrieval over the passage index, in Postgres.

Three signals, fused with reciprocal rank fusion:

  BM25    exact terms - proper nouns, case numbers, names. Beats every dense
          model on meeting-transcript benchmarks, and is the only thing that
          reliably finds "PDE-260022" or "Orange Belt Trail". Implemented in
          SQL over materialised postings; see bin/bm25.sql for why not
          ts_rank_cd.
  DENSE   harrier-0.6b embeddings under an HNSW index - finds passages that
          never use the query's words, which is most of what natural-language
          questions need.
  THREAD  curated topic/case/project keys - pulls a case's whole history even
          when the wording drifts across years.

Passages hang off `agenda_items`, so a result carries the county's own account
of what it belongs to: item code, case number, section, staff recommendation,
and the outcome recorded in the minutes. That is published fact rather than
something inferred from audio, and `case=` turns "has this come up before"
into a filter instead of a hope that the wording matched.

Ranking by relevance alone is wrong for "how did this evolve" questions: the
top hits pile into whichever meeting discussed it most and the earliest
occurrence never surfaces. `spread` caps hits per meeting so the timeline is
covered instead.
"""
import os
import re

import numpy as np

import db
import threads

MODEL_ID = "microsoft/harrier-oss-v1-0.6b"

# Where the query encoder runs. `cuda:1` is this workstation; the reader
# container sets `PASCO_EMBED_DEVICE=cpu` and has no GPU at all.
#
# This exists because the default used to be the literal string "cuda:1" in
# four signatures, and `PASCO_EMBED_DEVICE` was read only by web/admin.py -
# which is not in the read path. So a CPU deployment asked for cuda:1, failed,
# and `tools.warm()` swallowed it by design ("a failure here is not fatal, it
# costs the dense arm"). The server then served BM25-only search for ever,
# having printed one line to stderr. Half the retrieval product, silently
# absent, on a box that looked healthy. Measured on CPU before choosing the
# default: 72 ms per query at float16, which is why the dtype is unchanged.
DEVICE = os.environ.get("PASCO_EMBED_DEVICE") or "cuda:1"
# HNSW is approximate, so this is the dial that trades recall for latency.
# Measured against an exact scan over 65k passages, at the depths that actually
# reach the reader: ef=500 gives 97.5% recall@40, ef=1000 gives 98.6% at 19 ms
# against 84 ms for exact. 1000 is pgvector 0.8's ceiling.
EF_SEARCH = 1000

# Nearest-neighbour search always returns neighbours. Asked for "zzzznothing"
# the index dutifully hands back its 300 closest passages and the reader is
# shown twelve results for a word that does not exist - which is worse than an
# empty page, because it says the archive contains something it does not.
#
# So when NOTHING matched lexically - no BM25 hit, no thread key - cosine
# similarity is the only evidence there is, and it has to clear a bar.
# Measured over this corpus: nonsense queries top out at 0.52 ("zzzznothing")
# and 0.50 ("qwertyuiop asdfgh"), while real queries lead at 0.62-0.65. The
# floor sits between them. It applies ONLY in the no-lexical-match case, so a
# genuine query's weaker tail - "Orange Belt Trail" runs down to 0.51 - is
# untouched, because BM25 already vouched for that query.
DENSE_FLOOR = 0.55

_model = None


def model(device=None):
    global _model
    device = device or DEVICE
    if _model is None:
        import torch
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_ID, device=device,
                                     model_kwargs={"torch_dtype": torch.float16})
    return _model


def encode(query, device=None):
    return model(device or DEVICE).encode([query], prompt_name="web_search_query",
                                convert_to_numpy=True, normalize_embeddings=True,
                                show_progress_bar=False)[0].astype(np.float32)


def bm25(con, query, limit=300):
    return [r[0] for r in con.execute(
        "SELECT passage_id FROM bm25(%s, %s)", (query, limit))]


def dense(con, vec, limit=300):
    """Approximate nearest neighbours under the HNSW index.

    Embeddings are L2-normalised, so cosine distance ranks identically to
    inner product; `<=>` is used because that is the operator class the index
    was built with.
    """
    # SET takes no bound parameters; set_config is its function form. Scoped to
    # the SESSION, not the transaction: is_local=true would be discarded before
    # the next statement on an autocommit connection, dropping ef_search back to
    # its default of 40 and quietly gutting recall on a 300-row fetch.
    con.execute("SELECT set_config('hnsw.ef_search', %s, false)",
                (str(EF_SEARCH),))
    return [r[0] for r in con.execute(
        "SELECT id FROM passages WHERE embedding IS NOT NULL "
        "ORDER BY embedding <=> %s LIMIT %s", (vec, limit))]


def thread_hits(con, query, limit=200):
    """Passages sharing a topic/case/project key mentioned in the query."""
    keys = threads.global_keys(query)
    # A bare topic word in the question ("cameras", "impact fees") should also
    # match the curated threads, which is what makes precedent questions work.
    for name, rx in threads.TOPIC_RE.items():
        if rx.search(query):
            keys.append(("topic", name))
    if not keys:
        return []
    out, seen = [], set()
    for kind, key in keys:
        for r in con.execute("SELECT passage_id FROM passage_keys "
                             "WHERE kind = %s AND key = %s LIMIT %s",
                             (kind, key, limit)):
            if r[0] not in seen:
                seen.add(r[0])
                out.append(r[0])
    return out


def rrf(*rankings, k=60):
    score = {}
    for ranking in rankings:
        for pos, i in enumerate(ranking, 1):
            score[i] = score.get(i, 0.0) + 1.0 / (k + pos)
    return sorted(score, key=lambda i: -score[i])


def search(query, limit=40, spread=None, speaker=None, kind=None,
           since=None, until=None, phase=None, case=None, outcome=None,
           body=None, device=None, con=None):
    """Return ranked passages with their meeting metadata.

    spread: max hits per meeting. Set it for "over time" questions so the
    answer sees the whole timeline rather than one dominant meeting.
    phase:  restrict to a part of the meeting ("public_comment" to hear the
    podium rather than the dais). See segment.py for the vocabulary.
    """
    own = con is None
    con = con or db.connect()
    try:
        vec = encode(query, device)
        lex, keyed = bm25(con, query, 300), thread_hits(con, query, 200)
        ranked = rrf(lex, dense(con, vec, 300), keyed)
        if not ranked:
            return []
        head = ranked[:600]
        floor = 0.0 if (lex or keyed) else DENSE_FLOOR

        # Cosine similarity is computed here, for the candidates only. It is
        # reported as `score` but is NOT the ranking - RRF above is.
        # meeting_id/date/body are joined here rather than resolved by the
        # caller: a hit is unreadable without the item it sits under,
        # and the item is addressed through its meeting.
        meta = {r["id"]: dict(r) for r in con.execute("""
            SELECT p.id, p.video_id, p.start, p."end", p.speaker,
                   -- The key stays `speaker`; this is the same person as a
                   -- reader should see them (bin/schema.sql, display_name).
                   display_name(p.speaker) AS speaker_display, p.text,
                   -- The passage's NATURAL key - `id` is reassigned by every
                   -- rebuild - and the range a correction is raised against.
                   -- tools.PASSAGE_HIT and the agent's own projection have
                   -- carried these for a while; this one did not, so which
                   -- arm answered decided whether a hit knew where it was.
                   p.start_idx, p.end_idx,
                   p.phase, p.agenda_item_id,
                   ai.title AS item, ai.code, ai.case_id, ai.section,
                   ai.outcome, ai.recommendation, ai.department,
                   ai.source AS item_source,
                   v.title, v.upload_date, v.kind,
                   v.meeting_id, mt.date AS meeting_date, mt.body,
                   -- NOT how sure the speaker's NAME is, deliberately. This
                   -- runs over 600 candidates to return 25, and resolving a
                   -- name walks four precedence levels per utterance - 620 ms
                   -- for 600 passages, against 2 ms without it, which is a
                   -- whole search's worth of time spent describing 575 rows
                   -- nobody will see. tools.speaker_sure fills it in on the
                   -- hits that SURVIVE, for 16 ms, and it does it for both
                   -- retrieval arms at once. Do not move it in here.
                   1 - (p.embedding <=> %s) AS score
            FROM passages p
            JOIN videos v ON v.id = p.video_id
            LEFT JOIN meetings mt ON mt.id = v.meeting_id
            LEFT JOIN agenda_items ai ON ai.id = p.agenda_item_id
            WHERE p.id = ANY(%s)""", (vec, head))}

        out, per_meeting = [], {}
        for i in ranked:
            m = meta.get(i)
            if not m:
                continue
            if speaker and m["speaker"] != speaker:
                continue
            if phase and m["phase"] != phase:
                continue
            if case and m["case_id"] != case:
                continue
            if outcome and m["outcome"] != outcome:
                continue
            if kind and kind != "all" and m["kind"] != kind:
                continue
            if body and m["body"] != body:
                continue
            # The MEETING's date, falling back to the upload date for the 17
            # recordings that never got joined to one (see STATE, honest
            # limits) - filtering those out entirely would hide them.
            when = m["meeting_date"] or m["upload_date"] or ""
            if since and when < since:
                continue
            if until and when > until:
                continue
            if spread:
                n = per_meeting.get(m["video_id"], 0)
                if n >= spread:
                    continue
                per_meeting[m["video_id"]] = n + 1
            m["score"] = float(m["score"] or 0.0)
            if m["score"] < floor:
                continue
            out.append(m)
            if len(out) >= limit:
                break
        return out
    finally:
        if own:
            con.close()


def decisions_in_play(con, passages, max_segments=8, per_segment=4):
    """Fetch the decision moments of the agenda items already retrieved.

    Ranking finds an item's *discussion* easily - it is long and dense with
    topic words. The motion and the vote are neither, so they sit below the
    cut even when their own item ranks first: measured, the school-zone vote
    lands at rank 33-58 while the agent reads only the top 30 per query.

    Segmentation makes this recoverable without a deeper, more expensive
    sweep. Once an item is in play, its terse cross-speaker exchanges are
    fetched directly rather than competed for. Ordered from the END of the
    item, because that is where a board decides things.

    Only items with more than one hit are expanded, so a single glancing match
    does not drag a whole agenda item into the reader.
    """
    hits = {}
    for p in passages:
        if p.get("agenda_item_id"):
            hits[p["agenda_item_id"]] = hits.get(p["agenda_item_id"], 0) + 1
    live = [s for s, n in sorted(hits.items(), key=lambda kv: -kv[1])
            if n > 1][:max_segments]
    if not live:
        return []
    have = {p["id"] for p in passages}
    rows = con.execute("""
        SELECT * FROM (
            SELECT p.id, p.video_id, p.start, p."end", p.speaker,
                   display_name(p.speaker) AS speaker_display, p.text,
                   p.start_idx, p.end_idx,
                   p.phase, p.agenda_item_id,
                   ai.title AS item, ai.code, ai.case_id, ai.outcome,
                   v.title, v.upload_date, v.kind,
                   ROW_NUMBER() OVER (PARTITION BY p.agenda_item_id
                                      ORDER BY p.start DESC) AS rn
            FROM passages p
            JOIN videos v ON v.id = p.video_id
            LEFT JOIN agenda_items ai ON ai.id = p.agenda_item_id
            WHERE p.agenda_item_id = ANY(%s) AND p.speaker = '(exchange)'
        ) t WHERE rn <= %s""", (live, per_segment)).fetchall()
    out = []
    for r in rows:
        if r["id"] in have:
            continue
        d = dict(r)
        d.pop("rn", None)
        d["score"] = 0.0        # fetched by structure, not ranked by relevance
        out.append(d)
    return out


ITEM_FTS = """to_tsvector('english',
    coalesce(ai.title,'') || ' ' || coalesce(ai.case_id,'') || ' ' ||
    coalesce(ai.department,'') || ' ' || coalesce(ai.outcome_text,''))"""


# An identifier, not a topic. `PDE-25-7738`, `R-58`, `C10`, `CPAL-2206`. The
# record is searchable both ways and a code put through
# websearch_to_tsquery is torn into fragments that match nothing, so it takes a
# different query entirely.
CODE_RE = re.compile(r"^\s*([A-Z]{1,5})[\s\-]?(\d{1,3})(?:[\s\-](\d{1,5}))?\s*$", re.I)


def looks_like_code(query):
    """Is this an identifier the reader already holds, rather than a subject?"""
    m = CODE_RE.match(query or "")
    return bool(m) and not (query or "").strip().isdigit()


ORDERS = {
    # What a reader means by "search": the best match first.
    "relevance": "{rank}, m.date DESC",
    # What an agent asking "what was decided" means. A case carries five
    # continuances and one approval, and the approval is the answer, so a
    # terminal outcome outranks a better word match. Wrong for a human
    # search box - it puts "Trench Plate Build-A-Box" above "License Plate
    # Detection Systems" because the trench plate was approved.
    "decided": ("(ai.outcome IN ('approved','adopted','denied','withdrawn'))"
                " DESC NULLS LAST, (ai.outcome IS NOT NULL) DESC,"
                " {rank}, m.date DESC"),
    "recent": "m.date DESC, {rank}",
}


def search_items(con, query, limit=10, body=None, outcome=None, phase=None,
                 case=None, since=None, until=None, offset=0, decided=None,
                 order="decided"):
    """Search the PUBLISHED RECORD directly, independent of any transcript.

    91% of the items the minutes dispose of were decided at a meeting this
    archive holds no recording of. Retrieval that only ranks passages cannot
    reach any of them, so a question about one of those matters returned
    "nothing in the indexed meetings matches that" while the county's own
    minutes recorded the decision.

    Terminal outcomes rank above continuances: a case carries five
    deferrals and one approval, and the approval is the answer.

    Facets are applied in SQL rather than to the result page, so narrowing by
    body or outcome deepens the search instead of thinning what came back. `total` is the honest count behind them.
    """
    where = ["ai.source = 'agenda'"]
    args = {"q": query, "limit": limit, "offset": offset}
    tsq = "websearch_to_tsquery('english', %(q)s)"

    # `code = 'R58'` beats ranking the word "R58" every time, and the county
    # writes the same code as "R-58", "R 58" and "R58" across twelve years.
    if looks_like_code(query):
        m = CODE_RE.match(query)
        args["code"] = "".join(p for p in m.groups() if p)
        args["case"] = "-".join(p for p in m.groups() if p).upper()
        where.append("(upper(replace(replace(ai.code,'-',''),' ','')) = upper(%(code)s)"
                     " OR upper(ai.case_id) = %(case)s"
                     " OR upper(ai.file_number) = %(case)s)")
        rank = "ai.code"
    else:
        where.append(f"{ITEM_FTS} @@ q.tsq")
        rank = f"ts_rank_cd({ITEM_FTS}, q.tsq) DESC"
    order_by = ORDERS.get(order, ORDERS["decided"]).format(rank=rank)

    for col, key, val in (("m.body", "body", body), ("ai.outcome", "outcome", outcome),
                          ("ai.phase", "phase", phase), ("ai.case_id", "case", case)):
        if val:
            where.append(f"{col} = %({key})s")
            args[key] = val
    if since:
        where.append("m.date >= %(since)s")
        args["since"] = since
    if until:
        where.append("m.date <= %(until)s")
        args["until"] = until
    if decided is True:
        where.append("ai.outcome IS NOT NULL")
    elif decided is False:
        where.append("ai.outcome IS NULL")
    clause = " AND ".join(where)

    def run(tsq, order_by=order_by):
        n = con.execute(f"""
            SELECT COUNT(*) FROM agenda_items ai
            JOIN meetings m ON m.id = ai.meeting_id
            CROSS JOIN (SELECT {tsq} AS tsq) q
            WHERE {clause}""", args).fetchone()[0]
        if not n:
            return n, []
        return n, [dict(r) for r in con.execute(f"""
            SELECT ai.id, ai.seq, ai.code, ai.title, ai.search_title,
                   ai.case_id, ai.section, ai.phase, ai.department,
                   ai.recommendation, ai.outcome_text, ai.outcome,
                   ai.outcome_source, ai.source, ai.districts, ai.file_number,
                   m.id AS meeting_id, m.date, m.body, m.title AS meeting_title,
                   EXISTS (SELECT 1 FROM item_spans sp
                           WHERE sp.agenda_item_id = ai.id) AS has_recording,
                   ts_rank_cd({ITEM_FTS}, q.tsq) AS score
            FROM agenda_items ai
            JOIN meetings m ON m.id = ai.meeting_id
            CROSS JOIN (SELECT {tsq} AS tsq) q
            WHERE {clause}
            ORDER BY {order_by}
            LIMIT %(limit)s OFFSET %(offset)s""", args)]

    total, rows = run(tsq)

    # `websearch_to_tsquery` ANDs every term, so "license plate cameras"
    # demands all three and returns NOTHING - while the county's own item is
    # titled "License Plate Detection Systems". A reader reads an empty result
    # as "the archive holds none of this", which is the specific failure D9
    # exists to prevent, so the search loosens to ANY term rather than
    # stopping. ts_rank_cd still puts the items matching more terms first, and
    # the page says the query was widened.
    loosened = False
    if not total and not looks_like_code(query):
        # The text is already lexed by then, so swapping the operator is safe;
        # it cannot reintroduce user syntax.
        tsq = ("to_tsquery('english', "
               "nullif(replace(plainto_tsquery('english', %(q)s)::text,"
               " ' & ', ' | '), ''))")
        # Under OR, ts_rank_cd is the wrong measure and inverts the answer:
        # it counts COVERS, so an item saying "Trench Plate" twice outranks
        # "License Plate Detection Systems", which matches two of the three
        # words the reader typed. Rank by how many of those words the item
        # matches at all, and only then by density. Measured: 10ms on 14
        # candidates, 280ms on 1,733.
        args["terms"] = [w for w in re.findall(r"[\w'-]{3,}", query)][:8]
        matched = (f"(SELECT COUNT(*) FROM unnest(%(terms)s::text[]) tt "
                   f"WHERE {ITEM_FTS} @@ plainto_tsquery('english', tt))")
        total, rows = run(tsq, f"{matched} DESC, {order_by}")
        loosened = bool(total)

    return {"total": total, "items": rows, "loosened": loosened}


def items_for(con, passages, limit=18):
    """The OFFICIAL record behind the passages that were retrieved.

    A transcript can only ever show a vote being taken - "all in favor say
    aye" - and never its result, because nobody in the room says the result
    out loud in a form ASR can attribute. The result is published, in the
    minutes, and this project already parses it. Until now nothing downstream
    read it: the agent inferred decisions from vote passages and routinely
    concluded "the evidence does not say whether it passed" while the county's
    own minutes recorded "Approved" for that item.

    So the items are returned as their own kind of evidence, ranked by how much
    of the retrieved discussion belongs to each. Published items come first:
    a transcript-derived item has no official record to add.
    """
    hits = {}
    for p in passages:
        if p.get("agenda_item_id"):
            hits[p["agenda_item_id"]] = hits.get(p["agenda_item_id"], 0) + 1
    if not hits:
        return []
    # Follow the case to every other meeting that took it up, INCLUDING
    # meetings we hold no recording of. A rezoning is heard by the Planning
    # Commission, transmitted by the Board and adopted months later, and the
    # meeting that finally decides it is often one we have no video for - so
    # the deciding item has no passages, and an items-from-passages rule can
    # never reach it. Asked what was decided about a rezoning that WAS
    # approved, the agent answered "continued, no final approval recorded",
    # because the two approvals were exactly the items it could not see.
    # The published record does not depend on whether a camera was running.
    rows = con.execute("""
        WITH seed AS (SELECT id FROM agenda_items WHERE id = ANY(%s)),
             threads AS (
                 SELECT DISTINCT ai.case_id FROM agenda_items ai
                 JOIN seed ON seed.id = ai.id WHERE ai.case_id IS NOT NULL)
        SELECT ai.id, ai.code, ai.title, ai.search_title, ai.case_id,
               ai.section, ai.phase, ai.department, ai.recommendation,
               ai.outcome_text, ai.outcome, ai.outcome_source, ai.source,
               m.id AS meeting_id, m.date, m.body, m.title AS meeting_title,
               EXISTS (SELECT 1 FROM item_spans sp
                       WHERE sp.agenda_item_id = ai.id) AS has_recording
        FROM agenda_items ai
        JOIN meetings m ON m.id = ai.meeting_id
        WHERE ai.id IN (SELECT id FROM seed)
           OR ai.case_id IN (SELECT case_id FROM threads)""",
        (list(hits),)).fetchall()
    out = [dict(r) for r in rows]
    for d in out:
        d["passages"] = hits.get(d["id"], 0)
    # What DECIDED the matter outranks what deferred it. A case routinely
    # carries five continuances and one approval; ranking by discussion volume
    # alone buries the approval under the continuances, which is the one thing
    # the reader actually asked for.
    TERMINAL = {"approved", "adopted", "denied", "withdrawn"}

    def rank(d):
        if d["source"] != "agenda":
            return 3
        if d["outcome"] in TERMINAL:
            return 0
        return 1 if d["outcome"] else 2

    out.sort(key=lambda d: (rank(d), -d["passages"], d["date"] or ""))
    return out[:limit]


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "concerns about license plate cameras"
    for r in search(q, limit=8, spread=2):
        print(f"[{r['upload_date']} {r['start']:7.0f}s {(r['speaker'] or '')[:18]:<18}] "
              f"{r['text'][:110]}")
