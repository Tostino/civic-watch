"""Hybrid retrieval over the passage index, in Postgres."""
import re

import numpy as np

import db
import threads

MODEL_ID = "microsoft/harrier-oss-v1-0.6b"

# Where the query encoder runs. `cuda:1` is this workstation; the reader
# container sets `CIVIC_EMBED_DEVICE=cpu` and has no GPU at all.
DEVICE = db.embed_device()
# HNSW is approximate, so this is the dial that trades recall for latency.
# Measured against an exact scan over 65k passages, at the depths that actually
# reach the reader: ef=500 gives 97.5% recall@40, ef=1000 gives 98.6% at 19 ms
# against 84 ms for exact. 1000 is pgvector 0.8's ceiling.
EF_SEARCH = 1000

# Nearest-neighbour search always returns neighbours. Asked for "zzzznothing"
# the index dutifully hands back its 300 closest passages and the reader is
# shown twelve results for a word that does not exist - which is worse than an
# empty page, because it says the archive contains something it does not.
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
    """Approximate nearest neighbours under the HNSW index."""
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
           body=None, meeting_id=None, agenda_item_id=None,
           device=None, con=None):
    """Return ranked passages with their meeting metadata."""
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
            # NARROWS WHAT WAS FOUND; it does not search inside the scope.
            # Ranking happens over the whole archive and these run against the
            # survivors, like every filter above them, so a scope holding no
            # passage in the global top 600 comes back empty. Read that as
            # "not among the best matches archive-wide", never as "this item
            # does not discuss it" - get_item is what reads an item whole.
            if meeting_id and m["meeting_id"] != meeting_id:
                continue
            if agenda_item_id and m["agenda_item_id"] != agenda_item_id:
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
    """Search the PUBLISHED RECORD directly, independent of any transcript."""
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

    def run(tsq, order_by=order_by, floor=None):
        cl = clause + (f" AND {floor}" if floor else "")
        n = con.execute(f"""
            SELECT COUNT(*) FROM agenda_items ai
            JOIN meetings m ON m.id = ai.meeting_id
            CROSS JOIN (SELECT {tsq} AS tsq) q
            WHERE {cl}""", args).fetchone()[0]
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
            WHERE {cl}
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
        # AND A FLOOR UNDER IT, counted over the words that DISCRIMINATE.
        #
        # OR with no floor answers a three-word question with items matching
        # one word, which is how a brewery conditional use came back for a
        # wellfield query. A raw count is not enough on its own either: this
        # county's own name is in most titles, so "east pasco wellfield
        # desalination" cleared a floor of two on "east" and "pasco" alone and
        # returned 1,067 items.
        #
        # So a term matching more than a fifth of the archive is dropped before
        # counting. What is left is the part of the query that actually names
        # the subject, and the floor is half of that, rounded up, never all of
        # it - all of it is the AND pass that just returned nothing. If every
        # word is common the query has no discriminating half, and the raw
        # terms stand rather than refusing everything.
        terms = args["terms"]
        if terms:
            n_items = con.execute(
                "SELECT count(*) FROM agenda_items").fetchone()[0] or 1
            # r[0], never `for t, n in ...`: db.Row is a Mapping, so unpacking
            # one yields its COLUMN NAMES. A dict built that way looks fine,
            # scores every term at zero, and drops nothing.
            #
            # One statement per term rather than a correlated subquery over
            # unnest, which the planner turns into 2.4 seconds. Four terms cost
            # 8ms this way, and only on the loosened path.
            ceiling = n_items * 0.2
            # A STOPWORD IS NOT A TERM. plainto_tsquery('english','not') is
            # empty and can never match, so counting one toward the floor
            # raises a bar nothing can clear. Both regressions this caused were
            # of that shape: "twenty is not that big" fell from 35 results to
            # 0 with two unmatchable words of four, and "motion second all in
            # favor" fell from 219 to 0 when every word was either a stopword
            # or too common and the fallback handed the stopwords back.
            #
            # So there are two lists. `matchable` is what CAN be counted;
            # `keep` is the part of it that also discriminates. Prefer keep,
            # fall back to matchable, and when nothing is matchable apply no
            # floor at all rather than one nothing can meet.
            matchable, keep = [], []
            for t in terms:
                r = con.execute(
                    f"SELECT ptq <> ''::tsquery, "
                    f"       (SELECT count(*) FROM agenda_items ai "
                    f"         WHERE {ITEM_FTS} @@ ptq) "
                    f"  FROM plainto_tsquery('english', %s) AS ptq",
                    (t,)).fetchone()
                if not r[0]:
                    continue
                matchable.append(t)
                if r[1] <= ceiling:
                    keep.append(t)
            args["terms"] = keep or matchable
        n_terms = len(args["terms"])
        args["floor"] = min(max(n_terms - 1, 1), max((n_terms + 1) // 2, 1))
        # No countable terms means nothing to hold a floor against, and
        # applying one would refuse every row.
        cap = f"{matched} >= %(floor)s" if n_terms else None
        total, rows = run(tsq, f"{matched} DESC, {order_by}", floor=cap)
        loosened = bool(total)

    return {"total": total, "items": rows, "loosened": loosened}


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "concerns about license plate cameras"
    for r in search(q, limit=8, spread=2):
        print(f"[{r['upload_date']} {r['start']:7.0f}s {(r['speaker'] or '')[:18]:<18}] "
              f"{r['text'][:110]}")
