"""Build the passage + vector index that natural-language search runs on.

Passages are speaker-bounded with a word floor - the shape that measured best
(MRR 0.762 vs 0.655 for speaker-blind chunks). Every passage belongs to exactly
one speaker, which is what lets an answer say who said something.

Retrieval is hybrid by design: BM25 beats every dense model on meeting-
transcript benchmarks and is unbeatable on proper nouns ("Orange Belt Trail",
"PDE-260022"), while embeddings find the passages that never use the query's
words. Neither alone is enough.

What is indexed is NOT what is displayed. A passage carries its agenda item's
subject in `search_text` and its verbatim words in `text`. Without that a vote
("All in favor say aye. Aye.") is unreachable: it contains no topic words, so
neither signal can find it, and the decision a question is actually asking
about goes missing from the answer. See segment.py.
"""
import argparse
import bisect
import hashlib
import re
import sys

import numpy as np

import db
import threads

MODEL_ID = "microsoft/harrier-oss-v1-0.6b"
MAX_WORDS = 140
FLOOR = 35
BATCH = 64

# The shortest passage worth indexing, measured on what actually GETS indexed.
#
# This used to be applied inside emit(), to the raw words, before the agenda
# subject was attached - and it discarded 29,261 chunks, 1,672 of them carrying
# motion or vote language. Among them, verbatim:
#
#     "Starkey: District One, Commissioner Oakley. Moore: Aye."
#
# That is the roll call. Exchange passages exist precisely so a vote survives
# being made of five two-word turns, and then a second floor threw the result
# away anyway - the same failure the exchange mechanism was built to fix,
# re-introduced one function later.
#
# Applied to search_text instead, 21,752 of those come back, including 1,373 of
# the 1,672 votes: "Aye." is not retrievable, but "R-58 school zone speed
# cameras. Mariano: Aye." is, and that is the string the index holds. The 7,509
# that still fall short carry no subject and no substance, and are the noise
# the floor was meant to catch.
MIN_INDEXED = 12

# How long an utterance has to be to count as a TURN rather than a beat in a
# back-and-forth. This decides GROUPING - whether a line starts a speaker's run
# or joins a run of short turns - and never whether anything is dropped. It
# shares a value with MIN_INDEXED and nothing else; naming it separately is
# cheap next to confusing a grouping rule with a floor again.
TURN_WORDS = 12


def indexable(passages):
    """Drop passages with too little retrievable signal, judged on search_text."""
    return [p for p in passages
            if len(p["search_text"].split()) >= MIN_INDEXED]


def build_passages(con, video_id=None):
    """Speaker-bounded passages, plus exchange passages for rapid back-and-forth.

    A single-speaker passage with a word floor is the right retrieval unit for
    substance, but votes are not substance-shaped: "I move...", "Second.",
    "All in favor?", "Aye.", "Opposed.", "Motion carries." are a dozen short
    turns across five people. Every one falls under the floor, so a
    floor-only index silently loses roughly three quarters of the moments where
    the board actually DECIDED something - the exact thing "what was decided"
    questions need.

    So runs of short turns are emitted as one cross-speaker exchange passage
    with inline speaker labels, keeping the decision intact and attributable.
    """
    # Resolved through utterance_speaker, not voice_name: the display path used
    # to read the archive-wide cluster majority and it contradicted the
    # per-meeting assignment on 10.7% of named lines. A name baked in here
    # reaches search, the agent's citations and every quote it prints, so it
    # has to be the same answer the transcript gives.
    rows = con.execute(f"""
        SELECT u.video_id, u.idx, u.start, u."end", u.text, u.cluster,
               u.local_label, us.name AS speaker
        FROM utterances u
        JOIN utterance_speaker us
          ON us.video_id = u.video_id AND us.idx = u.idx
        {'WHERE u.video_id = %s' if video_id else ''}
        ORDER BY u.video_id, u.idx""",
        (video_id,) if video_id else ()).fetchall()

    out = []

    def emit(chunk, speaker=None, exchange=False):
        if not chunk:
            return
        if exchange:
            # Label each turn, so a retrieved vote still says who said what.
            #
            # Unnamed turns are lettered WITHIN THIS PASSAGE. They used to be
            # written as "Group 465", a diarization id that reads as a name,
            # is reshuffled by every re-clustering run, and - because this text
            # is what gets embedded and indexed - was sitting inside 19,457
            # vectors and their BM25 postings. A passage-local letter claims
            # nothing durable while still distinguishing two people saying
            # "aye".
            letters, parts, last = {}, [], object()
            for r in chunk:
                who = r["speaker"]
                if not who:
                    key = r["local_label"]
                    if key not in letters:
                        letters[key] = chr(ord("A") + len(letters) % 26)
                    who = f"Unidentified {letters[key]}"
                # Label only when the speaker CHANGES. Diarization splits one
                # person's sentence across several utterances, so labelling
                # every turn produced "Mariano: We have no one online Mariano:
                # for Mariano: this item." - which reads badly and, because
                # this text is what gets embedded, repeats the surname four
                # times and drags the vector toward the name and away from
                # what was said.
                parts.append(f"{who}: {r['text']}" if who != last else r["text"])
                last = who
            text = " ".join(parts).strip()
        else:
            text = " ".join(r["text"] for r in chunk).strip()
        out.append({"video_id": chunk[0]["video_id"], "start": chunk[0]["start"],
                    "end": chunk[-1]["end"], "idx": chunk[0]["idx"],
                    # The utterances this passage is made of. A correction over
                    # an utterance range maps to exactly the passages it
                    # touches, so only those need re-rendering.
                    "start_idx": chunk[0]["idx"], "end_idx": chunk[-1]["idx"],
                    # NULL, not a stand-in, when nobody is identified: a
                    # rendered placeholder in a data column is how "Group 465"
                    # ended up looking like a person's name in search results.
                    "speaker": speaker if not exchange else "(exchange)",
                    "cluster": chunk[0]["cluster"] if not exchange else None,
                    "text": text})

    by_video = {}
    for r in rows:
        by_video.setdefault(r["video_id"], []).append(r)

    for vrows in by_video.values():
        i = 0
        while i < len(vrows):
            r = vrows[i]
            long_turn = len(r["text"].split()) >= TURN_WORDS
            if long_turn:
                # Accumulate this VOICE's run up to MAX_WORDS. Bounded by
                # local_label rather than cluster: 30 (video, cluster) pairs
                # hold two diarization labels, so the cluster is not the voice
                # and grouping by it merges two people into one passage.
                chunk, words, voice = [], 0, r["local_label"]
                while (i < len(vrows) and vrows[i]["local_label"] == voice
                       and words < MAX_WORDS):
                    chunk.append(vrows[i])
                    words += len(vrows[i]["text"].split())
                    i += 1
                if words >= FLOOR:
                    emit(chunk, speaker=chunk[0]["speaker"])
                else:
                    emit(chunk, exchange=True)   # too thin alone; keep as context
            else:
                # a run of short turns: motions, seconds, votes, crosstalk
                chunk, words = [], 0
                while (i < len(vrows) and len(vrows[i]["text"].split()) < TURN_WORDS
                       and words < MAX_WORDS):
                    chunk.append(vrows[i])
                    words += len(vrows[i]["text"].split())
                    i += 1
                emit(chunk, exchange=True)
    return out


# Words that appear in every ordinance title and identify none of them. A
# clause built only from these is boilerplate; one with real subject words in
# it is not, even when it opens with "Providing For".
LEGALESE = {
    "providing", "provide", "repealer", "severability", "effective", "date",
    "dates", "applicability", "inclusion", "consistency", "internal",
    "necessary", "additional", "amendments", "sections", "section", "other",
    "thereto", "hereby", "ordinance", "resolution", "board", "county",
    "commissioners", "commission", "pasco", "florida", "an", "a", "the", "for",
    "and", "of", "to", "as", "by", "in", "into", "or", "with", "which", "shall",
    "certain", "related", "matters", "purposes", "codified",
}
SUBJECT_WORDS = 30


def subject(title, search_title, code, case_id):
    """The compact subject a passage should be findable by.

    Published titles are legal prose - "An Ordinance Amending The Pasco County
    Comprehensive Plan; Providing For A Comprehensive Plan Text Amendment to
    Subarea Policy FLU 7.1.34-Palmetto Ridge; And Providing For Additional Text
    Amendments As Necessary For Internal Consistency; Providing For A Repealer,
    Severability, And An Effective Date." Prepending that whole thing to every
    passage in the item would drown the passage's own words in the embedding.

    Truncating positionally is worse than useless here: the distinctive part
    ("Palmetto Ridge") is in the SECOND clause, so keeping the first would
    discard exactly what makes the item findable. Clauses are therefore dropped
    on whether they carry any subject words at all, not on where they sit.
    """
    parts = []
    if code:
        parts.append(code)
    if case_id:
        parts.append(case_id)
    body = (title or "").strip()
    if body:
        keep = []
        for clause in re.split(r"[;]", body):
            words = [w for w in re.findall(r"[A-Za-z0-9.'-]+", clause)
                     if w.lower().strip(".") not in LEGALESE]
            if len(words) >= 2:
                keep.append(clause.strip())
        body = "; ".join(keep) or body
    else:
        body = (search_title or "").strip()
    if body:
        parts.append(" ".join(body.split()[:SUBJECT_WORDS]))
    return " ".join(parts).strip()


def attach_items(con, passages):
    """Give every passage its agenda item's subject, phase and case.

    This is what makes a vote findable. The vote passage keeps its verbatim
    text for display; the copy that gets indexed and embedded also carries the
    subject of the item being voted on, which is the only thing a searcher
    has to go on.

    The subject now comes from the PUBLISHED agenda where one exists - official
    title, item code, case number - and falls back to the transcript-derived
    title for the procedural stretches that no agenda lists.
    """
    by_video = {}
    for r in con.execute("""
            SELECT sp.video_id, sp.start_idx, sp.end_idx, ai.id, ai.phase,
                   ai.title, ai.search_title, ai.code, ai.case_id, ai.source
            FROM item_spans sp JOIN agenda_items ai ON ai.id = sp.agenda_item_id
            ORDER BY sp.video_id, sp.start_idx"""):
        by_video.setdefault(r["video_id"], []).append(dict(r))
    starts = {v: [s["start_idx"] for s in lst] for v, lst in by_video.items()}

    n = 0
    for p in passages:
        p["agenda_item_id"] = p["phase"] = None
        p["search_text"] = p["text"]
        lst = by_video.get(p["video_id"])
        if not lst:
            continue
        j = bisect.bisect_right(starts[p["video_id"]], p["idx"]) - 1
        if j < 0:
            continue
        s = lst[j]
        # bisect finds the last span STARTING at or before this passage; that
        # is the right span only if the passage also falls inside it. Spans
        # happen to tile each video today, which is the only reason omitting
        # this was harmless - one gap and every passage in it would be filed
        # under whichever item happened to precede it. Do not infer the item
        # from position alone.
        if p["idx"] > s["end_idx"]:
            continue
        p["agenda_item_id"], p["phase"] = s["id"], s["phase"]
        subj = subject(s["title"] if s["source"] == "agenda" else None,
                       s["search_title"], s["code"], s["case_id"])
        if subj:
            p["search_text"] = f"{subj}. {p['text']}"
            n += 1
    return n


def refresh_video(con, video_id, device="cuda:1", verbose=True):
    """Bring one recording's passages back in step with the transcript.

    The speaker's name is part of what gets embedded and indexed, on purpose:
    "what did Starkey say about the trail" is a real question, and a passage
    stripped of its speaker cannot answer it. That makes a wrong name an INDEX
    defect rather than a display one - it pulls the passage toward the wrong
    person in vector space and puts the wrong surname in the postings, so it is
    retrieved for someone who never spoke and missed for the person who did.
    Resolving names at read time cannot repair any of that.

    So a correction has to reach the index, and reaching it has to be cheap or
    it will not happen. A full rebuild is 146k passages, 5.6M postings and a
    GPU pass; this touches only what actually changed, which for a typical
    correction is a handful of passages and usually zero new embeddings,
    because vec_cache is keyed on the exact string.

    Passage BOUNDARIES cannot move here: they are set by `local_label` and word
    counts, neither of which a name correction touches. That is asserted rather
    than assumed - if the ranges have shifted, something upstream changed and
    this refuses instead of writing mismatched rows over good ones.
    """
    fresh = build_passages(con, video_id)
    attach_items(con, fresh)
    fresh = indexable(fresh)

    stored = {(r["start_idx"], r["end_idx"]): r for r in con.execute(
        'SELECT id, start_idx, end_idx, speaker, text, search_text '
        'FROM passages WHERE video_id = %s', (video_id,))}
    made = {(p["start_idx"], p["end_idx"]): p for p in fresh}

    if set(stored) != set(made):
        raise RuntimeError(
            f"{video_id}: passage boundaries moved ({len(stored)} stored vs "
            f"{len(made)} rebuilt). A name correction cannot do that, so "
            f"something upstream changed - run bin/index_passages.py in full.")

    changed = [(stored[k], made[k]) for k in sorted(made)
               if stored[k]["speaker"] != made[k]["speaker"]
               or stored[k]["text"] != made[k]["text"]
               or stored[k]["search_text"] != made[k]["search_text"]]
    if not changed:
        if verbose:
            print("index already in step; nothing to re-post")
        return 0

    # Only text that actually changed needs a vector, and vec_cache serves the
    # ones that have been embedded before.
    reembed = [(old, new) for old, new in changed
               if old["search_text"] != new["search_text"]]
    vecs = embed(con, [n["search_text"] for _, n in reembed], device) \
        if reembed else []

    ids = [old["id"] for old, _ in changed]
    with con.cursor() as cur:
        cur.executemany(
            'UPDATE passages SET speaker=%s, text=%s, search_text=%s '
            'WHERE id=%s',
            [(n["speaker"], n["text"], n["search_text"], o["id"])
             for o, n in changed])
        for i, (old, _) in enumerate(reembed):
            cur.execute("UPDATE passages SET embedding=%s WHERE id=%s",
                        (vecs[i], old["id"]))
        # Thread keys are derived from search_text, so they move with it.
        cur.execute("DELETE FROM passage_keys WHERE passage_id = ANY(%s)", (ids,))
        rows = [(o["id"], kind, key) for o, n in changed
                for kind, key in threads.global_keys(n["search_text"])]
        if rows:
            cur.executemany("INSERT INTO passage_keys (passage_id, kind, key) "
                            "VALUES (%s,%s,%s)", rows)
        cur.execute("CALL bm25_refresh(%s)", (ids,))
    con.commit()
    if verbose:
        print(f"re-posted {len(changed)} passages "
              f"({len(reembed)} re-embedded) for {video_id}")
    return len(changed)


def rebuild_video(con, video_id, device="cuda:1", verbose=True):
    """Rebuild one recording's passages when the BOUNDARIES may have moved.

    `refresh_video` above is the cheap path and REFUSES if the ranges shift,
    because for a name correction a shift means something upstream broke. A
    redaction shifts them legitimately: taking an address out shortens the
    line, and a passage that drops under the indexing floor stops existing.
    Measured on the first real batch - 576 stored against 574 rebuilt, and
    `refresh_video` correctly refused, which left the transcript redacted while
    the passages still carried the address. That half-state is the reason this
    exists.

    Passage ids are reassigned. That is already true of every full rebuild
    (gotcha 10) and nothing outside the index stores one; the BM25 postings for
    the old ids are dropped by passing them to bm25_refresh alongside the new.
    """
    fresh = build_passages(con, video_id)
    attach_items(con, fresh)
    fresh = indexable(fresh)

    old_ids = [r[0] for r in con.execute(
        "SELECT id FROM passages WHERE video_id = %s", (video_id,))]
    # vec_cache is keyed on the exact string, so the passages a redaction did
    # not touch cost nothing to re-embed.
    vecs = embed(con, [p["search_text"] for p in fresh], device) if fresh else []
    base = (con.execute(
        "SELECT COALESCE(MAX(id), 0) FROM passages").fetchone()[0] or 0) + 1

    rows, keyrows, new_ids = [], [], []
    for i, p in enumerate(fresh):
        pid = base + i
        new_ids.append(pid)
        rows.append((pid, p["video_id"], p["start"], p["end"], p["speaker"],
                     p["cluster"], p["text"], p["search_text"],
                     p["agenda_item_id"], p["phase"],
                     vecs[i] if vecs is not None and len(vecs) else None,
                     p["start_idx"], p["end_idx"]))
        for kind, key in threads.global_keys(p["search_text"]):
            keyrows.append((pid, kind, key))

    with con.cursor() as cur:
        if old_ids:
            # No foreign key on passage_keys, so nothing cascades.
            cur.execute("DELETE FROM passage_keys WHERE passage_id = ANY(%s)",
                        (old_ids,))
            cur.execute("DELETE FROM passages WHERE video_id = %s", (video_id,))
        if rows:
            cur.executemany(
                'INSERT INTO passages (id, video_id, start, "end", speaker, '
                'cluster, text, search_text, agenda_item_id, phase, embedding, '
                'start_idx, end_idx) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', rows)
            with cur.copy("COPY passage_keys (passage_id, kind, key) "
                          "FROM STDIN") as cp:
                for row in keyrows:
                    cp.write_row(row)
        cur.execute("CALL bm25_refresh(%s)", (old_ids + new_ids,))
    con.commit()
    if verbose:
        print(f"rebuilt {len(rows)} passages for {video_id} "
              f"(was {len(old_ids)})")
    return len(rows)


def embed(con, texts, device="cuda:1"):
    """Embed, reusing vectors for any text seen in a previous run.

    A full rebuild is 60k+ embeddings, but re-indexing after a segmentation
    pass changes only the passages whose item gained a title. The cache is
    keyed on the exact indexed string, so unchanged passages cost nothing and
    the iteration loop stays minutes rather than an hour.
    """
    hashes = [hashlib.sha1(t.encode()).hexdigest() for t in texts]
    uniq = list(dict.fromkeys(hashes))
    have = {}
    for i in range(0, len(uniq), 5000):
        for r in con.execute("SELECT h, v FROM vec_cache WHERE h = ANY(%s)",
                             (uniq[i:i + 5000],)):
            v = r[1]
            have[r[0]] = v.to_numpy() if hasattr(v, "to_numpy") else np.asarray(v)

    todo = [h for h in uniq if h not in have]
    print(f"  {len(have):,} cached · {len(todo):,} to embed", flush=True)
    if todo:
        first = {}
        for h, t in zip(hashes, texts):
            first.setdefault(h, t)
        import torch
        from sentence_transformers import SentenceTransformer
        m = SentenceTransformer(MODEL_ID, device=device,
                                model_kwargs={"torch_dtype": torch.float16})
        for i in range(0, len(todo), 4096):
            part = todo[i:i + 4096]
            v = m.encode([first[h] for h in part], batch_size=BATCH,
                         convert_to_numpy=True, normalize_embeddings=True,
                         show_progress_bar=False).astype(np.float32)
            with con.cursor() as cur:
                cur.executemany("INSERT INTO vec_cache (h, v) VALUES (%s, %s) "
                                "ON CONFLICT (h) DO NOTHING",
                                [(h, v[k]) for k, h in enumerate(part)])
            con.commit()
            for k, h in enumerate(part):
                have[h] = v[k]
            print(f"  embedded {min(i + 4096, len(todo)):,}/{len(todo):,}",
                  flush=True)
        del m
        torch.cuda.empty_cache()
    return [have[h] for h in hashes]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="cuda:1")
    ap.add_argument("--skip-embed", action="store_true")
    args = ap.parse_args()

    con = db.connect()
    passages = build_passages(con)
    attach_items(con, passages)
    built = len(passages)
    passages = indexable(passages)
    # Counted after the prune, or it describes a set that is not being indexed.
    enriched = sum(1 for p in passages if p["agenda_item_id"] is not None)
    words = sorted(len(p["text"].split()) for p in passages)
    print(f"{len(passages)} passages (median {words[len(words) // 2]} words) · "
          f"{enriched} carry an agenda subject · "
          f"{built - len(passages)} below the {MIN_INDEXED}-word floor",
          flush=True)

    vecs = None
    if not args.skip_embed:
        print(f"embedding on {args.device} ...", flush=True)
        vecs = embed(con, [p["search_text"] for p in passages], args.device)

    # Thread keys let a question about a case or a policy pull its whole
    # history without depending on the wording matching. Built over search_text
    # so an item's case number reaches the votes and asides inside it too.
    keyrows = []
    for i, p in enumerate(passages):
        for kind, key in threads.global_keys(p["search_text"]):
            keyrows.append((i, kind, key))

    with con.cursor() as cur:
        # Loading 60k rows through a live HNSW index is far slower than
        # rebuilding it once at the end.
        cur.execute("DROP INDEX IF EXISTS passages_embedding_hnsw")
        cur.execute("TRUNCATE passages, passage_keys CASCADE")
        cur.executemany(
            'INSERT INTO passages (id, video_id, start, "end", speaker, '
            'cluster, text, search_text, agenda_item_id, phase, embedding, '
            'start_idx, end_idx) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            [(i, p["video_id"], p["start"], p["end"], p["speaker"], p["cluster"],
              p["text"], p["search_text"], p["agenda_item_id"], p["phase"],
              vecs[i] if vecs is not None else None,
              p["start_idx"], p["end_idx"])
             for i, p in enumerate(passages)])
        with cur.copy("COPY passage_keys (passage_id, kind, key) "
                      "FROM STDIN") as cp:
            for row in keyrows:
                cp.write_row(row)
    con.commit()
    print(f"{len(keyrows)} thread keys attached", flush=True)

    with con.cursor() as cur:
        cur.execute("SET maintenance_work_mem = '2GB'")
        cur.execute("SET max_parallel_maintenance_workers = 4")
        if not args.skip_embed:
            print("rebuilding HNSW index ...", flush=True)
            cur.execute("CREATE INDEX passages_embedding_hnsw ON passages "
                        "USING hnsw (embedding vector_cosine_ops) "
                        "WITH (m = 16, ef_construction = 64)")
        print("rebuilding BM25 postings ...", flush=True)
        cur.execute("CALL bm25_rebuild()")
        cur.execute("ANALYZE passages")
        cur.execute("ANALYZE passage_keys")
    con.commit()

    r = con.execute("SELECT n_docs, avgdl FROM bm25_stats").fetchone()
    print(f"bm25: {r['n_docs']:,} docs, avgdl {r['avgdl']:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
