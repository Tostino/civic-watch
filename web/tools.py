"""The retrieval surface, as callable tools."""
import os
import re
import sys
import textwrap
import threading
import time
from urllib.parse import quote

import archive

# `retrieve` needs torch and a GPU for its dense arm, and pulling that in at
# import time would cost the API server 6 seconds and 2 GB whether or not
# anybody searches. Bound on first use instead.
_retrieve = None

# Whether the dense arm is usable. A search that silently drops to keywords is
# a search that quietly got worse, so this is reported in the result rather
# than swallowed.
_dense_error = None


def retrieve():
    global _retrieve, _dense_error
    if _retrieve is None:
        import retrieve as r
        _retrieve = r
    return _retrieve


def warm(device=None):
    """Load the embedding model. Called at startup so the first reader does
    not pay for it; a failure here is not fatal, it costs the dense arm."""
    global _dense_error
    t0 = time.time()
    try:
        r = retrieve()
        r.model(device)
        _dense_error = None
        print(f"[tools] dense retrieval READY - {r.MODEL_ID} on "
              f"{device or r.DEVICE} in {time.time() - t0:.1f}s",
              file=sys.stderr)
    except Exception as e:                                   # noqa: BLE001
        _dense_error = f"{type(e).__name__}: {e}"
        print(f"[tools] dense retrieval UNAVAILABLE - {_dense_error}\n"
              f"[tools] search will answer on BM25 alone; paraphrase queries "
              f"will find nothing", file=sys.stderr)
    return _dense_error


# ------------------------------------------------------------------- shared
BODY = {"type": "string",
        "description": "Restrict to one body, e.g. 'Board of County "
                       "Commissioners' or 'Planning Commission'."}
SINCE = {"type": "string",
         "description": "Earliest meeting date, YYYY-MM-DD inclusive."}
UNTIL = {"type": "string",
         "description": "Latest meeting date, YYYY-MM-DD inclusive."}
CASE = {"type": "string",
        "description": "Restrict to one case/application id, e.g. "
                       "'PDE-25-7738'. Use this to follow a matter across "
                       "meetings rather than hoping the wording matched."}
PHASE = {"type": "string",
         "enum": ["consent", "public_hearing", "regular", "public_comment",
                  "board_reports", "staff_report", "proclamation",
                  "call_to_order", "adjourn", "recess", "other"],
         "description": "Part of the meeting. RARELY what you want, and NOT "
                        "the way to find what residents said: they speak "
                        "inside public hearings and regular business too. "
                        "Measured on one such question, the evidence sat 13 "
                        "in 'other', 8 in 'public_comment' and 7 in "
                        "'board_reports' — filtering to the podium would have "
                        "kept 8 of 28 passages and lost the county's reply "
                        "entirely. Search without it and read who spoke off "
                        "the results."}
OUTCOME = {"type": "string",
           "enum": ["approved", "adopted", "denied", "withdrawn", "continued",
                    "no_action"],
           "description": "The outcome the approved minutes recorded."}


def _clean(d):
    """Drop absent arguments so a tool's own defaults apply."""
    return {k: v for k, v in d.items() if v not in (None, "", [])}


def canonical_speaker(con, name):
    """Fold a board member's full name back to the surname the index holds."""
    if not name:
        return name
    r = con.execute(
        "SELECT surname FROM people WHERE lower(full_name) = lower(%s) "
        "AND lower(surname) <> lower(%s)", (name, name)).fetchone()
    return r[0] if r else name


EXCHANGE = "(exchange)"


# -------------------------------------------------------- search_transcript
def search_transcript(con, query, limit=12, offset=0, spread=None,
                      speaker=None, phase=None, case=None, body=None,
                      since=None, until=None, outcome=None, meeting_id=None,
                      agenda_item_id=None):
    """What was SAID. Hybrid retrieval over the passage index.

    PAGED, and it was the last tool that was not. Both arms rank the whole
    archive and then truncate, so the second window was simply unreachable:
    /search asked for offset 25 and got moments 1-25 again, beside a correctly
    paged list of published items. That is what a reader saw when they clicked
    for more.

    THERE IS NO TOTAL HERE and one is not invented. `search_record` counts its
    matches in SQL and can say `total` honestly; ranking cannot - a hit is a
    passage that survived RRF over a bounded candidate pool, so any number
    would describe the pool rather than the archive, and a count in this file
    is measured or absent. What IS knowable exactly is whether another window
    exists: ask for one row more than the caller wanted and see if it arrives.
    """
    speaker = canonical_speaker(con, speaker)
    r = retrieve()
    offset = max(0, int(offset or 0))
    # The extra row is the whole trick, and it is nearly free: both arms rank
    # and project a fixed candidate pool whatever the limit, and the per-hit
    # work below runs on the window rather than on the pool.
    want = offset + limit + 1
    hits, degraded = [], None
    try:
        hits = r.search(query, limit=want, spread=spread, speaker=speaker,
                        phase=phase, case=case, body=body, since=since,
                        until=until, outcome=outcome, meeting_id=meeting_id,
                        agenda_item_id=agenda_item_id, con=con)
    except Exception as e:                                   # noqa: BLE001
        # The dense arm is a GPU and someone else's library. Losing it costs
        # recall on paraphrase; it must not cost the reader their search, and
        # it must not pretend the search was as good as usual.
        degraded = f"{type(e).__name__}: {e}"
        ranked = r.rrf(r.bm25(con, query, 300), r.thread_hits(con, query, 200))
        hits = _plain(con, ranked, want, speaker=speaker, phase=phase,
                      case=case, body=body, since=since, until=until,
                      outcome=outcome, spread=spread, meeting_id=meeting_id,
                      agenda_item_id=agenda_item_id)
    more = len(hits) > offset + limit
    hits = hits[offset:offset + limit]
    # Here rather than in either arm's projection, so both arms describe a
    # speaker the same way and neither pays for the 575 candidates it threw
    # out. This is the one place a hit becomes a hit.
    # `returned` and not `count`: they were the same number under two names,
    # which is what this file removes from get_case eight hundred lines down
    # for the same reason. `returned` is the one every windowed tool uses.
    return {"query": query, "hits": turns(con, speaker_sure(con, hits)),
            "offset": offset, "returned": len(hits),
            "next_offset": offset + len(hits) if more else None,
            "truncated": more, "degraded": degraded or _dense_error}


# One passage, as a hit. Shared rather than written twice because two callers
# now read it by two different keys - the fallback search below by passage id,
# and web/answers.py by the range a passage covers - and a saved answer that
# described the same row differently from a live one would be a quiet lie about
# what the archive said.
PASSAGE_HIT = """
        SELECT p.id, p.video_id, p.start, p."end", p.speaker,
               -- `speaker` is the key the `speaker` facet filters on; this is
               -- the same person as the reader should see them. A board member
               -- is keyed by surname on purpose (bin/schema.sql, display_name).
               display_name(p.speaker) AS speaker_display,
               p.text,
               -- The passage's NATURAL key, and the only durable way to name
               -- one: bin/index_passages reassigns `id` on every rebuild.
               p.start_idx, p.end_idx,
               p.phase, p.agenda_item_id,
               ai.title AS item, ai.code, ai.case_id, ai.section,
               ai.outcome, ai.recommendation, ai.department,
               ai.source AS item_source,
               v.title, v.upload_date, v.kind,
               -- WHICH RECORDING the passage's clock is on. About half of all
               -- meeting-days are two videos on one continuous agenda, so
               -- "1:57:52" and "5:41" can both be Aug 11 2026 and a reader
               -- given the bare clocks has no way to see that. `sessions` is
               -- what makes `session_seq` sayable: a null seq is common on a
               -- two-video day and a 0 appears on a one-video one, so neither
               -- means anything alone. Counted here rather than derived from
               -- what an answer happened to cite, because a printed answer is
               -- read away from the archive. 3.6 ms for 600 rows, measured.
               v.session_seq,
               (SELECT count(*)::int FROM videos v2
                 WHERE v2.meeting_id = v.meeting_id) AS sessions,
               v.meeting_id, mt.date AS meeting_date, mt.body
        FROM passages p
        JOIN videos v ON v.id = p.video_id
        LEFT JOIN meetings mt ON mt.id = v.meeting_id
        LEFT JOIN agenda_items ai ON ai.id = p.agenda_item_id"""


# HOW SURE THE NAME IS, in the archive's own four states. SpeakerChip's, and
# web/agent.py's, read off the same two fields in the same order - a reader
# following a citation from an answer to the page must not be told two
# different things about one name, and neither must a stranger's client. Not a
# confidence threshold: a number would be a second precedence rule about a
# question the utterance_speaker view owns.
#
# THIS LIVES HERE, in the tool surface, and the other two read it. It used to
# live only in agent.py, which is why /ask warned about an unsound name and
# MCP did not: the instructions handed to every client said 'a passage marked
# "NAME NOT CONFIRMED"' and no tool result had ever carried those words, or
# any other form of the warning. 15,787 passages are in that state.
def name_state(who, human, basis):
    """The state, from the three fields that decide it."""
    if who == EXCHANGE:
        return "several"
    if not who:
        return "unknown"
    if human:
        return "confirmed"
    # A row whose fields were never filled in reads as the ordinary case,
    # which is what it was before any of this existed.
    return "weak" if basis == "cluster" else "inferred"


# What each state MEANS, in the words a caller should act on. Only the states
# that change what may be written get one: 'inferred' is 89% of the archive,
# so a note on it would warn on almost every row and teach a reader to skip
# them all. An unmarked name is an inferred one.
NAME_NOTE = {
    "confirmed": "NAME CONFIRMED by a person: you may state it plainly",
    "weak": ("NAME NOT CONFIRMED: this is the name the voice goes by across "
             "the archive, not evidence about this meeting. Do not attribute "
             "anything here to them by name."),
    "several": ("NAMES NOT CONFIRMED: the names written into this exchange "
                "are archive-wide voice matches, not evidence about this "
                "meeting. Do not attribute anything here by name."),
}


# The two states that carry a WARNING, as opposed to a permission. Only these
# put a sentence on the row: 'confirmed' means you may use the name, which is
# what a caller was going to do anyway, and spelling that out on 37 of one
# window's 80 turns cost 6 KB to say nothing that changes an answer. The state
# is still on every row for a caller that wants to act on it.
_WARNED = ("weak", "several")


def name_fields(who, human, basis):
    """`name_state` for every row, `name_note` only where a name is unsound.

    The note is why this is not just a machine-readable enum. A model that
    never read the handshake instructions - or read them a hundred thousand
    tokens ago - still gets the rule in the row it is about to quote, and this
    is the one rule in the archive whose cost is a real person's name on words
    they may not have said.
    """
    state = name_state(who, human, basis)
    out = {"name_state": state}
    # The exchange warning is only true when the names in it are archive-wide
    # guesses; an exchange of confirmed names needs no warning.
    if state in _WARNED and (state != "several" or basis == "cluster"):
        out["name_note"] = NAME_NOTE[state]
    return out


def speaker_sure(con, rows):
    """Fill in HOW WELL each passage's speaker name is known, in place."""
    # A row that cannot be keyed still gets the fields, set to null. Absent
    # and null are the same fact to a reader and two different shapes to
    # everything downstream, and this is what a hit's shape IS.
    for r in rows:
        r["name_human"] = r["name_basis"] = None
    want = [r for r in rows if r.get("video_id")
            and r.get("start_idx") is not None
            and r.get("end_idx") is not None]
    if not want:
        return rows
    keys = {(r["video_id"], r["start_idx"], r["end_idx"]) for r in want}
    vids, starts, ends = zip(*keys)
    sure = {(r["video_id"], r["start_idx"], r["end_idx"]): r
            for r in con.execute("""
        SELECT k.v AS video_id, k.s AS start_idx, k.e AS end_idx,
               sp.name_human, sp.name_basis
          FROM unnest(%s::text[], %s::int[], %s::int[]) AS k(v, s, e)
          LEFT JOIN LATERAL passage_speaker(k.v, k.s, k.e) sp ON TRUE""",
        (list(vids), list(starts), list(ends)))}
    for r in want:
        got = sure.get((r["video_id"], r["start_idx"], r["end_idx"])) or {}
        r["name_human"] = got.get("name_human")
        r["name_basis"] = got.get("name_basis")
    # The one shape (archive.who). A hit's own `speaker` stays exactly as it
    # is - it is the facet key and every saved citation is written against it -
    # and `who` is what gets rendered.
    for r in rows:
        r["who"] = archive.who(r.get("speaker"), r.get("speaker_display"),
                               r.get("name_basis"), r.get("name_human"))
        r.update(name_fields(r.get("speaker"), r.get("name_human"),
                             r.get("name_basis")))
    return rows



def turns(con, rows):
    """Break each multi-speaker passage into who actually said what, in place.

    95,196 passages - 57% of the corpus - carry `(exchange)` where a name goes,
    and `passages.text` renders them as one string with inline labels:
    "Weightman: That's it. He didn't budge. That was a Oakley: budge." A reader
    cannot reliably say where one turn stops, and a model quoting from it
    attributes words to whoever it guessed, which is the one error this archive
    treats as unforgivable.

    `(exchange)` IS TWO DIFFERENT FACTS, and only one of them is an exchange.
    bin/index_passages emits it for a run of short turns - motions, seconds,
    votes, crosstalk - and ALSO for a single speaker's run that fell under the
    word floor and was kept as context. 29,253 of them genuinely cross
    speakers; the other 65,943 are one person whose name the label is hiding.
    So this does not only disambiguate a blob: for two thirds of them it hands
    back an attributable name that `passages.speaker` threw away.

    Nothing is re-derived. `utterances` already holds the turns one row each,
    with the resolved name and its basis; the blob is a lossy rendering of data
    the database has properly. So this is a join, and like speaker_sure it runs
    on the hits that SURVIVED rather than on 600 candidates: measured, 10 ms for
    25 passages.

    THE PARENT PASSAGE ID TRAVELS ON EVERY TURN, and there is no such thing as
    a turn-level citation. web/agent.py's `check()` verifies `[N]` against the
    passage ids a run really saw and strikes everything else, so a token like
    `[512#2]` would be deleted from the answer as a fabrication. A turn is how
    a model knows WHO to attribute and WHERE to seek; `[passage_id]` is how it
    cites, exactly as before.
    """
    for r in rows:
        r["turns"] = None
    want = [r for r in rows
            if r.get("speaker") == EXCHANGE and r.get("video_id")
            and r.get("start_idx") is not None
            and r.get("end_idx") is not None]
    if not want:
        return rows
    keys = {(r["video_id"], r["start_idx"], r["end_idx"]) for r in want}
    vids, starts, ends = zip(*keys)
    got = {}
    for u in con.execute("""
        SELECT k.v AS video_id, k.s AS start_idx, k.e AS end_idx,
               u.idx, u.start, u."end", u.text, u.local_label,
               us.name, us.display_name, us.human, us.basis
          FROM unnest(%s::text[], %s::int[], %s::int[]) AS k(v, s, e)
          JOIN utterances u
            ON u.video_id = k.v AND u.idx BETWEEN k.s AND k.e
          JOIN utterance_speaker us
            ON us.video_id = u.video_id AND us.idx = u.idx
         ORDER BY k.v, k.s, u.idx""",
            (list(vids), list(starts), list(ends))):
        got.setdefault((u["video_id"], u["start_idx"], u["end_idx"]),
                       []).append(u)

    for r in want:
        rows_ = got.get((r["video_id"], r["start_idx"], r["end_idx"]))
        if not rows_:
            continue
        out, letters = [], {}
        for u in rows_:
            # LETTERED WITHIN THIS PASSAGE when nobody is named, which is the
            # convention bin/index_passages already bakes into the text. Two
            # unnamed people in one exchange are "Unidentified A" and
            # "Unidentified B"; calling both of them "Unidentified" would lose
            # the one thing the reader still has, which is that they differ.
            display = u["display_name"]
            if not display:
                key = u["local_label"]
                if key not in letters:
                    letters[key] = chr(ord("A") + len(letters) % 26)
                display = f"Unidentified {letters[key]}"
            # A turn is a contiguous run by ONE speaker, so consecutive
            # utterances by the same one are merged - splitting on every
            # utterance would hand back the ASR's breath pauses as if somebody
            # else were talking. Keyed on the VOICE as well as the name,
            # because an unnamed speaker's name is NULL and two different
            # unnamed people would otherwise merge into a single turn.
            if (out and out[-1]["speaker"] == u["name"]
                    and out[-1]["_voice"] == u["local_label"]):
                t = out[-1]
                t["end_idx"], t["end"] = u["idx"], u["end"]
                t["text"] = f"{t['text']} {u['text']}".strip()
                continue
            out.append({
                "n": len(out) + 1,
                "passage_id": r.get("id"),
                "video_id": r["video_id"],
                "speaker": u["name"],
                "speaker_display": display,
                "who": archive.who(u["name"], u["display_name"],
                                   u["basis"], u["human"]),
                "start_idx": u["idx"], "end_idx": u["idx"],
                "start": u["start"], "end": u["end"],
                "text": u["text"],
                "_voice": u["local_label"],
            })
        for t in out:
            t.pop("_voice", None)
        r["turns"] = out
    return rows


def _plain(con, ranked, limit, spread=None, **f):
    """Keyword-only fallback. Same shape as retrieve.search, no `score`."""
    if not ranked:
        return []
    meta = {r["id"]: dict(r) for r in con.execute(
        f"{PASSAGE_HIT} WHERE p.id = ANY(%s)", (ranked[:600],))}
    out, per = [], {}
    for i in ranked:
        m = meta.get(i)
        if not m:
            continue
        when = m["meeting_date"] or m["upload_date"] or ""
        if ((f.get("speaker") and m["speaker"] != f["speaker"])
                or (f.get("meeting_id") and m["meeting_id"] != f["meeting_id"])
                or (f.get("agenda_item_id")
                    and m["agenda_item_id"] != f["agenda_item_id"])
                or (f.get("phase") and m["phase"] != f["phase"])
                or (f.get("case") and m["case_id"] != f["case"])
                or (f.get("outcome") and m["outcome"] != f["outcome"])
                or (f.get("body") and m["body"] != f["body"])
                or (f.get("since") and when < f["since"])
                or (f.get("until") and when > f["until"])):
            continue
        if spread:
            n = per.get(m["video_id"], 0)
            if n >= spread:
                continue
            per[m["video_id"]] = n + 1
        m["score"] = 0.0
        out.append(m)
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------- resolving citations
def passages_at(con, ranges):
    """Passages by the utterance range each covers, keyed by that range."""
    if not ranges:
        return {}
    vids, starts, ends = zip(*ranges)
    rows = con.execute(f"""
        {PASSAGE_HIT}
        JOIN unnest(%s::text[], %s::int[], %s::int[]) AS k(v, s, e)
          ON p.video_id = k.v AND p.start_idx = k.s AND p.end_idx = k.e""",
        (list(vids), list(starts), list(ends))).fetchall()
    out = {}
    for r in rows:
        d = dict(r)
        # Relevance is a property of the search that found it, not of the
        # passage. A saved answer is not a search, so there is no score to
        # honestly report; the field stays for shape parity with a live hit.
        d["score"] = 0.0
        out[(d["video_id"], d["start_idx"], d["end_idx"])] = d
    # A saved answer's evidence is drawn by the same components as a live
    # hit's, so it has to carry the same fields or the two pages would make
    # different claims about one passage. Cheap here: this is a handful of
    # cited passages, not 600 candidates.
    turns(con, speaker_sure(con, list(out.values())))
    return out


def items_at(con, ids):
    """Published items by id, keyed by id."""
    if not ids:
        return {}
    rows = con.execute("""
        SELECT ai.id, ai.seq, ai.code, ai.title, ai.search_title,
               ai.case_id, ai.section, ai.phase, ai.department,
               ai.recommendation, ai.outcome_text, ai.outcome,
               ai.outcome_source, ai.source, ai.districts, ai.file_number,
               m.id AS meeting_id, m.date, m.body, m.title AS meeting_title,
               EXISTS (SELECT 1 FROM item_spans sp
                       WHERE sp.agenda_item_id = ai.id) AS has_recording
        FROM agenda_items ai
        JOIN meetings m ON m.id = ai.meeting_id
        WHERE ai.id = ANY(%s)""", (list(ids),)).fetchall()
    return {r["id"]: {**dict(r), "score": 0.0} for r in rows}


# ------------------------------------------------------------ search_record
def search_record(con, query, limit=12, offset=0, body=None, outcome=None,
                  phase=None, case=None, since=None, until=None, decided=None,
                  order="relevance"):
    """What was DECIDED. Full text over the published agendas and minutes."""
    r = retrieve().search_items(con, query, limit=limit, offset=offset,
                                body=body, outcome=outcome, phase=phase,
                                case=case, since=since, until=until,
                                decided=decided, order=order)
    r["query"] = query
    r["by_code"] = retrieve().looks_like_code(query)
    return r


# ------------------------------------------------------------ the manifest
#
# Descriptions are written for a MODEL to read, not for documentation. Each one
# says what the tool reaches, what it cannot reach, and when to prefer another
# - because the failure this whole design exists to prevent is a caller that
# searched the wrong source and concluded the archive holds nothing.
# ------------------------------------------------------------------- facts
# THE NUMBERS IN THESE DESCRIPTIONS ARE MEASURED, NEVER TYPED.
FACTS_TTL = 3600

_FACTS = None
_FACTS_AT = 0.0
_FACTS_LOCK = threading.Lock()


def _measure(con):
    """Every number the tool surface quotes, in one round trip."""
    r = con.execute("""
        SELECT (SELECT round((sum(duration) / 3600)::numeric)
                  FROM videos WHERE transcribed)                    AS hours,
               (SELECT count(*) FROM agenda_items
                 WHERE source = 'agenda')                           AS items,
               -- Meetings that have HAPPENED. The county posts its calendar
               -- months ahead, and counting those would claim coverage of
               -- events nobody has attended (archive.overview says the same).
               (SELECT count(*) FROM meetings
                 WHERE date <= to_char(now(), 'YYYY-MM-DD'))        AS meetings,
               (SELECT count(*) FROM meetings m
                 WHERE m.date <= to_char(now(), 'YYYY-MM-DD')
                   AND EXISTS (SELECT 1 FROM videos v
                                WHERE v.meeting_id = m.id
                                  AND v.transcribed))               AS recorded,
               -- The gap the item and case pages name: published, and the
               -- minutes never say what became of it.
               (SELECT round(100.0 * count(*) FILTER (WHERE outcome IS NULL)
                        / nullif(count(*), 0))
                  FROM agenda_items WHERE source = 'agenda')        AS pct_no_outcome,
               (SELECT count(*) FROM cases WHERE meetings > 1)      AS recurring,
               (SELECT round(100.0 * count(*) FILTER (
                          WHERE EXISTS (SELECT 1 FROM item_spans s
                                         WHERE s.agenda_item_id = ai.id))
                        / nullif(count(*), 0))
                  FROM agenda_items ai
                 WHERE ai.outcome IS NOT NULL)                      AS pct_transcript,
               (SELECT round(100.0 * count(*) FILTER (
                          WHERE speaker IS NULL OR speaker = '(exchange)')
                        / nullif(count(*), 0))
                  FROM passages)                                    AS pct_no_name,
               (SELECT min(left(date, 4)) FROM meetings
                 WHERE date <= to_char(now(), 'YYYY-MM-DD'))        AS first_year,
               (SELECT max(left(date, 4)) FROM meetings
                 WHERE date <= to_char(now(), 'YYYY-MM-DD'))        AS last_year,
               (SELECT min(left(m.date, 4)) FROM meetings m
                 WHERE EXISTS (SELECT 1 FROM videos v
                                WHERE v.meeting_id = m.id
                                  AND v.transcribed))               AS first_rec_year
        """).fetchone()
    f = {k: r[k] for k in r.keys()}
    # The deepest case in the archive, named rather than remembered. A worked
    # example teaches the id format and the scale in one clause, and picking it
    # by measurement means it is always a real case and always the extreme one.
    d = con.execute("""SELECT id, meetings FROM cases
                        ORDER BY meetings DESC, id LIMIT 1""").fetchone()
    f["deep_case"] = d["id"] if d else "PDE-25-7738"
    f["deep_case_meetings"] = f"{d['meetings']:,}" if d else "several"
    # Thousands separators here rather than in every template, so a placeholder
    # is only ever a name.
    for k in ("hours", "items", "recurring", "meetings", "recorded"):
        f[k] = f"{int(f[k] or 0):,}"
    for k in ("pct_transcript", "pct_no_name", "pct_no_outcome"):
        f[k] = str(int(f[k] or 0))
    return f


def facts(con):
    """The measured counts, at most an hour old.

    Locked across the query so a cold cache under a burst of handshakes runs
    it once and the rest wait, rather than each opening its own.
    """
    global _FACTS, _FACTS_AT
    with _FACTS_LOCK:
        if _FACTS is None or time.monotonic() - _FACTS_AT > FACTS_TTL:
            _FACTS = _measure(con)
            _FACTS_AT = time.monotonic()
        return _FACTS


# A line that carries its own structure and must not be joined to its
# neighbours: a bullet, a numbered step, or a heading that ends in a colon.
_STRUCTURED = re.compile(r"^\s*(?:[-*\u2022]\s|\d+[.)]\s)|:\s*$")


def reflow(text, width=79):
    """Rewrap prose paragraphs after substitution."""
    out = []
    for para in text.split("\n\n"):
        lines = para.split("\n")
        indent = lines[0][:len(lines[0]) - len(lines[0].lstrip())]
        if (any(_STRUCTURED.search(ln) for ln in lines)
                or any(ln[:len(ln) - len(ln.lstrip())] != indent
                       for ln in lines if ln.strip())):
            out.append(para)
            continue
        out.append(textwrap.fill(" ".join(ln.strip() for ln in lines),
                                 width=width, initial_indent=indent,
                                 subsequent_indent=indent) if para.strip()
                   else para)
    return "\n\n".join(out)


def fill(node, f):
    """Substitute measured facts into every template string in `node`."""
    if isinstance(node, str):
        return node.format(**f) if "{" in node else node
    if isinstance(node, dict):
        return {k: fill(v, f) for k, v in node.items()}
    if isinstance(node, list):
        return [fill(v, f) for v in node]
    return node


def _outline(row, words=12):
    """An item stripped to what it costs to open: who spoke, when, and the
    opening of each turn.

    `get_item` is all or nothing, and the whole item is sometimes a fifth of an
    evidence budget - measured at 39,695 characters for one of them. A caller
    with no way to price the call either pays it blind or avoids the right tool
    and approximates with searches, which is lossier and costs more round
    trips. This is the price tag: the same record and the same turns, with the
    text cut to its first few words.
    """
    # The speech lives under `item`, not at the top level: archive.item returns
    # {item, meeting, offices, prev, next}. Reducing the wrapper instead of the
    # item is a silent no-op, which is what the first version of this did.
    inner = row.get("item")
    if not isinstance(inner, dict):
        return row
    out = dict(row)
    item = {k: v for k, v in inner.items() if k not in ("lines", "runs")}
    out["item"] = item
    seen = {}
    runs = []
    for r in inner.get("runs") or []:
        turns = []
        for ln in r.get("lines") or []:
            who = ln.get("display_name") or ln.get("name") or "unidentified"
            seen[who] = seen.get(who, 0) + 1
            text = (ln.get("text") or "").split()
            turns.append({"idx": ln.get("idx"), "start": ln.get("start"),
                          "speaker": who,
                          "opens": " ".join(text[:words])
                                   + ("…" if len(text) > words else "")})
        runs.append({**{k: v for k, v in r.items() if k != "lines"},
                     "turns": turns})
    item["runs"] = runs
    out["outline"] = True
    out["census"] = {
        "turns": sum(len(r["turns"]) for r in runs),
        "speakers": sorted(seen.items(), key=lambda kv: -kv[1]),
    }
    return out


ITEM_WINDOW = 80


def _census(runs, key):
    """Who is in an item and how much of it there is, over the WHOLE item.

    Computed before any window is applied, because its whole job is to answer
    "is the person I am asking about in here at all" - and a census of the
    window answers a question nobody asked.
    """
    seen = {}
    for r in runs:
        for ln in r.get(key) or []:
            # ONE SPELLING, because the census sits directly above lines that
            # use it: agent._line falls back to bare "unidentified", and a
            # census saying "(unidentified) 5" over lines saying
            # "unidentified:" reads as two different speakers.
            who = (ln.get("speaker") or ln.get("display_name")
                   or ln.get("name") or "unidentified")
            seen[who] = seen.get(who, 0) + 1
    return {"turns": sum(len(r.get(key) or []) for r in runs),
            "speakers": sorted(seen.items(), key=lambda kv: -kv[1])}


def _window_turns(entries, key, offset, limit):
    """Slice a turn list that is spread across entries, in place.

    ONE IMPLEMENTATION FOR BOTH TOOLS. An item's entries are its runs and a
    case's are its hearings, but the unit is the same turn and the contract a
    model has to learn should be the same too.

    Entries survive the window even when they hold none of it. A run is one
    appearance and a hearing is one meeting; dropping the empty ones would
    tell a caller paging through February that the case was never heard in
    May. Each keeps `count` for its own length and `returned` for how much of
    it is in this window.
    """
    total = sum(len(e.get(key) or []) for e in entries)
    limit = max(1, int(limit or ITEM_WINDOW))
    # A NEGATIVE OFFSET COUNTS FROM THE END, and it is not a convenience. A
    # motion and its vote sit at the end and carry no topic words, which is
    # the whole reason these tools exist where search does not reach. A window
    # that only ever opens at the front would put that end behind however many
    # round trips the thing is long.
    offset = int(offset or 0)
    offset = max(0, total + offset) if offset < 0 else offset
    at = returned = 0
    for e in entries:
        got = e.get(key) or []
        lo = min(max(offset - at, 0), len(got))
        hi = min(max(offset + limit - at, 0), len(got))
        e[key] = got[lo:hi]
        e["count"], e["returned"] = len(got), hi - lo
        at += len(got)
        returned += hi - lo
    return offset, returned, total


def _paging(offset, returned, total, unit="turns"):
    """The cursor fields, in the one spelling every windowed tool uses.

    `next_offset` is here so nobody has to do the arithmetic. A caller adding
    offset and returned itself gets it right until the first window that comes
    back short, and then it silently re-reads or skips. None means no more.

    Only the TOTAL is named for what it counts - `turns` for speech,
    `text_chars` for a document, `agenda_items` for a meeting - because a
    caller has to know what an offset is denominated in. The cursor itself is
    the same four fields everywhere, so learning one tool teaches the rest.
    """
    more = offset + returned < total
    return {unit: total, "offset": offset, "returned": returned,
            "next_offset": offset + returned if more else None,
            "truncated": more}


def _thin_turns(runs):
    """Drop the two fields on a turn that carry nothing the turn does not.

    NOT a compression of the speaker. Who said it stays on every line, spelled
    out, because that is what a model attributes from - an index into a table
    of speakers would save bytes and buy a join, and a join is exactly where
    attribution goes wrong in an archive whose whole discipline is that a
    quote belongs to the right person.

    So only these two go, and neither is information:

    - `who`, which archive.who() computes from `name`, `display_name`,
      `basis`, `human` and `contested` - the five fields sitting beside it on
      the same line. It is a shape the UI wants, restated on every turn, and
      it measured 19.2% of an 80-turn window.
    - `agenda_item_id`, which is the item that was asked for, on all of them.
    """
    for r in runs:
        for ln in r.get("lines") or []:
            ln.pop("who", None)
            ln.pop("agenda_item_id", None)
            # A turn carries the same risk a search hit does and used to carry
            # none of the warning: `basis` and `human` sit right there, and
            # nothing said what they mean. Same fields under the turn's own
            # spelling.
            ln.update(name_fields(ln.get("name"), ln.get("human"),
                                  ln.get("basis")))


def get_item(con, item_id, outline=False, offset=None, limit=ITEM_WINDOW):
    """One agenda item: the record whole, the speech in a window.

    The record half is 7 KB and it is the authoritative half, so it always
    comes back entire. The transcript half is what runs away. Turns per item
    are a median of 18, a p99 of 474 and a maximum of 1,225, and that longest
    item serialised whole was 1.5 MB - about 387k tokens, more context than
    any caller has. Half of that was duplication: `archive.item` also flattens
    every turn into `lines` for the page's older callers, and a model reading
    `runs` never needed the second copy. It is dropped here.

    THE WINDOW COUNTS TURNS, NOT CHARACTERS, which is the one place this
    deliberately differs from `get_document`. A turn is a passage that never
    crosses a speaker boundary, so a cut between turns is the only cut that
    leaves every quote verbatim - and a quote that is not verbatim is the one
    failure this archive cannot afford. A PDF has no such seam, so that tool
    windows by character; this one has, so it does not.

    Runs survive the window even when they hold none of it. A run is one
    appearance, and an item argued at 18:05 and taken up again at 3:38:04 is
    two of them - dropping the empty one would tell a caller paging through
    the first appearance that there had never been a second.

    `census` is deliberately OUTSIDE the window: it counts the whole item and
    names everyone in it, so `outline=true` still answers "is the person I am
    asking about in here at all" in one call, whatever the window holds.

    A CONTINUATION SENDS THE TURNS AND NOTHING ELSE. The record, the case
    thread, the files, the census and the meeting are the same bytes on every
    window of the same item - 9.0 KB of them, which over the sixteen calls
    the longest item takes is 144 KB of pure repeat, and repeat is the thing
    a window exists to stop. So a call that passes a non-zero `offset` is
    resuming, and gets back the turns, the paging fields and the id to hang
    them on. Passing no offset, or zero, means "from the start" and is the
    call that carries the record.

    What is NOT thinned is who spoke. See `_thin_turns`: the speaker stays
    spelled out on every line, and only the two fields that restate something
    already on the line or on the response are dropped.
    """
    row = archive.item(con, item_id)
    if row is None:
        raise ToolError(
            f"no agenda item with id {item_id}. The id comes from `id` on any "
            f"search result, and is not the county's `file_number`.")
    if outline:
        row = _outline(row)
    item = row.get("item")
    if not isinstance(item, dict):
        return row
    # `lines` is a flat copy of exactly what `runs` already holds, and it is
    # half the payload. _outline has already dropped it; the full form has not.
    item.pop("lines", None)
    # The turn list is called `lines` in the full form and `turns` in the
    # outline, so the per-run counts below must not be named either of them.
    key = "turns" if outline else "lines"
    runs = item.get("runs") or []
    # _outline has already counted the whole item; the full form has not, and
    # it needs the same answer for the same reason.
    row.setdefault("census", _census(runs, key))
    # A non-zero offset is a resume. Zero and absent both mean "from the
    # start", because a model that means the beginning should not have to know
    # which of the two spellings costs it the record. The negative-offset rule
    # lives in _window_turns, which is where it is applied.
    continued = bool(offset)
    offset, returned, total = _window_turns(runs, key, offset, limit)
    # archive.item clips at MAX_ITEM_LINES before this ever sees the item, and
    # that is a DIFFERENT truncation from this one: no offset reaches past it.
    # It cannot fire today - 2,000 against a longest item of 1,225 - but a flag
    # that says so costs less than a caller paging forever into nothing.
    if item.pop("truncated", False):
        item["clipped"] = True
    if not outline:
        _thin_turns(runs)
    item.update(_paging(offset, returned, total))
    if continued:
        keep = ("id", "runs", "turns", "offset", "returned", "next_offset",
                "truncated", "clipped")
        row = {"item": {k: item[k] for k in keep if k in item}}
        # Said rather than implied, because the one caller this could hurt is
        # the one that jumped straight to the end of an item it had never
        # opened, and it cannot tell a record that is absent from a record
        # that does not exist.
        row["item"]["record"] = (
            f"omitted: this is a continuation. get_item item_id={item_id} "
            f"with no offset for the record, the case thread and the census.")
    return row


# get_case windows on the same number, deliberately. One window size across
# the tool surface is one thing for a model to hold, and a case's turns are
# the same turns an item's are.
CASE_WINDOW = ITEM_WINDOW


def get_case(con, case_id, offset=None, limit=CASE_WINDOW):
    """One case, every meeting that took it up, with the speech in a window.

    THE RECORD IS THE POINT OF THIS TOOL and it always comes back whole: the
    appearances, their outcomes, the terminal one and the continuances reach
    meetings with no recording, which searching the transcript can never do.
    It is 14 KB.

    `heard` is the other 98%. The archive's largest case is 2,349 turns over
    four meetings and served whole it was 802 KB - about 187k tokens for one
    call, and cases are worse than items here because a case accumulates:
    median 32 turns, p90 221, p99 783.

    Same contract as `get_item`, down to the field names, because it is the
    same unit and a model should not have to learn it twice. Pass no offset
    for the record and the first window; pass back `next_offset` for the rest;
    negative counts from the end. A continuation carries the hearings alone.
    """
    row = archive.case(con, case_id)
    if row is None:
        raise ToolError(
            f"no case with id {case_id}. Case ids look like 'PDE-25-7738' and "
            f"come from `case_id` on a search result or an item.")
    heard = row.get("heard") or []
    _thin_turns(heard)
    row.setdefault("census", _census(heard, "lines"))
    continued = bool(offset)
    offset, returned, total = _window_turns(heard, "lines", offset, limit)
    # `heard_lines` was this same total under another name, and two names for
    # one number is one of them going stale. `heard_truncated` is the
    # MAX_CASE_LINES clip, which is a different truncation from this one: no
    # offset reaches past it. At 2,349 against 4,000 it has never fired.
    row.pop("heard_lines", None)
    if row.pop("heard_truncated", False):
        row["clipped"] = True
    row.update(_paging(offset, returned, total))
    if continued:
        keep = ("case_id", "heard", "turns", "offset", "returned",
                "next_offset", "truncated", "clipped")
        row = {k: row[k] for k in keep if k in row}
        row["record"] = (
            f"omitted: this is a continuation. get_case case_id={case_id} "
            f"with no offset for the appearances, their outcomes and the "
            f"census.")
    return row


# The same number again. get_meeting's unit is an agenda item rather than a
# turn, which is a different thing to count and the same thing to page.
MEETING_WINDOW = ITEM_WINDOW


def get_meeting(con, meeting_id, offset=None, limit=MEETING_WINDOW):
    """One meeting's agenda in published order, in a window.

    The meeting itself - its date, body, roster, recordings, coverage and the
    county's files - always comes back whole. It is 1.9 KB. The AGENDA is the
    other 98.6%: a Board meeting runs to 272 items against a median of 17, and
    that one served whole was 160 KB, about 37k tokens to answer "what else
    happened that day".

    The total is `agenda_items` and the window of them is `items`, because the
    list had the obvious name first. Everything else is the cursor `get_item`
    and `get_case` use, down to the negative offset - the end of an agenda is
    board reports and adjournment, which is where a meeting's loose ends get
    raised.
    """
    row = archive.meeting(con, meeting_id)
    if row is None:
        raise ToolError(
            f"no meeting with id {meeting_id}. The id comes from `meeting_id` "
            f"on any search result or item.")
    continued = bool(offset)
    # The row itself is the one entry holding the list, so the same windowing
    # serves a flat agenda and an item's runs.
    offset, returned, total = _window_turns([row], "items", offset, limit)
    row.pop("count", None)
    row.pop("returned", None)
    row.update(_paging(offset, returned, total, "agenda_items"))
    if continued:
        keep = ("items", "agenda_items", "offset", "returned", "next_offset",
                "truncated")
        kept = {k: row[k] for k in keep if k in row}
        kept["meeting"] = {"id": meeting_id}
        kept["record"] = (
            f"omitted: this is a continuation. get_meeting "
            f"meeting_id={meeting_id} with no offset for the date, the body, "
            f"the roster, the recordings and the county's files.")
        row = kept
    return row


DOC_WINDOW = 20_000


def _squeeze(text):
    """Drop the column alignment the PDF extractor turned into runs of spaces."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def get_document(con, file_id, offset=0, limit=DOC_WINDOW):
    """The county's own agenda or minutes, as text.

    WINDOWED, because these are not small: the 90th percentile is 44,556
    characters for an agenda and 57,522 for a set of minutes, and the largest
    is 115,681. Returned whole, one document could take half an evidence
    budget, which is the same way `get_item` used to bite.

    There is nothing structural to offset against. CivicClerk's `plainText=true`
    returns flat text: the printed page footers survive it, so a caller can
    read "Page 41 of 41" and search for one, but nothing marks a page boundary
    to the tool. A window is a character range.

    LAYOUT PADDING IS COLLAPSED FIRST, and it is 29% of these documents:
    measured over the 40 largest, 3,233,311 characters of extraction hold
    2,307,435 of text. The county's PDF is laid out in columns and the
    extractor keeps the alignment as runs of spaces, so a window spent raw is
    nearly a third padding. No word is changed, and `offset` counts the text a
    caller actually receives - `chars` stays the raw figure the archive stores,
    so the two do not agree and `text_chars` is the one to page against.
    """
    r = con.execute("""
        SELECT f.file_id, f.kind, f.name, f.published_at, f.chars,
               f.body_text, pe.meeting_id
        FROM portal_files f
        JOIN portal_events pe ON pe.id = f.event_id
        WHERE f.file_id = %s""", (file_id,)).fetchone()
    if r is None:
        raise ToolError(
            f"no document with file_id {file_id}. The id comes from the "
            f"`files` list on get_item or get_meeting, and is not an item's "
            f"`file_number`.")
    out = {k: r[k] for k in ("file_id", "kind", "name", "published_at",
                             "meeting_id", "chars")}
    body = r["body_text"]
    if not body:
        # Only Agenda and Minutes are ever listed, so this is a fetch that has
        # not happened rather than a kind that is skipped.
        out.update(text=None, held=False, truncated=False, offset=0,
                   returned=0, next_offset=None)
        return out
    body = _squeeze(body)
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or DOC_WINDOW), 100_000))
    window = body[offset:offset + limit]
    # The unit here is characters and in get_item it is turns, but the way you
    # ask for the rest should not change with the tool.
    out.update(text=window, held=True,
               **_paging(offset, len(window), len(body), "text_chars"))
    return out


SPECS = [
    {
        "name": "search_transcript",
        "description":
            "Search what people SAID, across {hours} hours of recorded meetings. "
            "Hybrid: exact terms, semantic similarity, and curated case "
            "threads. Use it for argument, reasoning, objection and public "
            "comment.\n"
            "It CANNOT reach any meeting with no recording, which is most of "
            "them: only {pct_transcript}% of decided items have one. It also "
            "under-serves "
            "votes - the moment a board decides something contains no topic "
            "words ('all in favor say aye'), so it ranks far below the "
            "discussion of the same item. If you want the DECISION, use "
            "search_record or get_item; if you want the ARGUMENT, use this.",
        "run": search_transcript,
        "parameters": {
            "type": "object",
            "required": ["query"],
            "properties": {
                # ORDER IS PART OF THE INTERFACE. A model reads this list top to
                # bottom and reaches for what it read first, so it runs: what to
                # look for, WHERE to look, how much to take back, and only then
                # the filters that exclude silently.
                "query": {"type": "string",
                          "description": "Natural language, or exact terms. "
                                         "Both arms run on every call, so a "
                                         "reworded query has already been "
                                         "tried for you."},
                "since": SINCE, "until": UNTIL,
                "spread": {"type": "integer",
                           "description": "Max hits per meeting. Set it (2-3) "
                                          "for 'how did this evolve' "
                                          "questions, or the top hits pile "
                                          "into whichever meeting discussed "
                                          "it most and the earliest "
                                          "occurrence never surfaces."},
                # The reasoning used to live in this comment, where the model
                # that keeps raising it to 30 and 60 could not read it. It is
                # in the description now; the comment is here to stop it being
                # "tidied" back out.
                "limit": {"type": "integer", "default": 12, "maximum": 100,
                          "description": "Leave it alone unless breadth IS "
                                         "the point. The default is sized to "
                                         "an agent's evidence budget, and "
                                         "every extra hit is paid for twice: "
                                         "once to read, and again in the room "
                                         "it leaves for everything after it."},
                "offset": {"type": "integer", "default": 0,
                           "description": "Hit to start at, in rank order. "
                                          "Pass back `next_offset` for the "
                                          "next window, the way get_item and "
                                          "get_case are paged. Prefer it to a "
                                          "bigger `limit`.\n"
                                          "There is no total, because ranking "
                                          "cannot honestly give one: a number "
                                          "here would describe the candidate "
                                          "pool and not the archive. "
                                          "`next_offset` goes null when the "
                                          "ranked results run out, which is "
                                          "the end of what these WORDS "
                                          "reached, never the end of what the "
                                          "archive holds."},
                "case": CASE, "body": BODY,
                "meeting_id": {
                    "type": "integer",
                    "description": "Only passages from this meeting, as "
                                   "returned in `meeting_id`. It NARROWS what "
                                   "was already found rather than searching "
                                   "inside the meeting, so an empty result "
                                   "means nothing here was among the best "
                                   "matches archive-wide, not that the meeting "
                                   "is silent on it. To read a meeting whole, "
                                   "use get_meeting."},
                "agenda_item_id": {
                    "type": "integer",
                    "description": "Only passages under this item, as returned "
                                   "in `agenda_item_id`. Narrows rather than "
                                   "searches, exactly as meeting_id does. "
                                   "get_item is what reads one item whole."},
                "outcome": OUTCOME, "phase": PHASE,
                "speaker": {"type": "string",
                            "description": "A speaker name as it appears on "
                                           "the passages you have been shown, "
                                           "e.g. 'Jack Mariano' or 'Mariano' "
                                           "- board members match on either. "
                                           "IT IS ONE-SIDED. What comes back "
                                           "really is theirs; what does NOT "
                                           "come back proves nothing, because "
                                           "{pct_no_name}% of passages carry no "
                                           "usable name and every "
                                           "cross-speaker exchange is one of "
                                           "them. So use it to confirm that "
                                           "somebody spoke to a subject, or to "
                                           "thin a broad result - never to "
                                           "establish that they did not, and "
                                           "not for 'how has X argued', since "
                                           "an argument mostly happens in the "
                                           "exchanges this cannot see. For "
                                           "that, search the subject and read "
                                           "the names off the results."},
            },
        },
    },
    {
        "name": "search_record",
        "description":
            "Search what the county PUBLISHED: {items} agenda items and the "
            "outcomes its approved minutes recorded for them. This is the "
            "authoritative source for whether something passed, and it covers "
            "{first_year}-{last_year} regardless of whether a camera was "
            "running.\n"
            "It holds no speech at all - it will never tell you why anyone "
            "voted as they did. An identifier ('R-58', 'PDE-25-7738') is "
            "matched as an identifier rather than as words, so pass it "
            "verbatim.",
        "run": search_record,
        "parameters": {
            "type": "object",
            "required": ["query"],
            "properties": {
                # Same ordering rule as search_transcript above: what to look
                # for, then where, then how much, then the filters that can
                # exclude the answer without saying so.
                "query": {"type": "string",
                          "description": "Subject words, or an item code or "
                                         "case number verbatim. Stemmed, and "
                                         "when no item holds all your words "
                                         "it matches ANY of them - so "
                                         "shortening or reordering a query "
                                         "has already been tried for you."},
                "since": SINCE, "until": UNTIL,
                "order": {"type": "string",
                          "enum": ["relevance", "decided", "recent"],
                          "default": "relevance",
                          "description": "'decided' floats items the minutes "
                                         "settled above ones they only "
                                         "continued - use it when you are "
                                         "asking what HAPPENED to a matter, "
                                         "because a case typically carries "
                                         "five continuances and one approval "
                                         "and the approval is the answer. It "
                                         "SORTS rather than filters, so it "
                                         "can never hide the thing you were "
                                         "looking for. Use 'relevance' when "
                                         "you are still looking for the "
                                         "matter itself."},
                "case": CASE, "body": BODY,
                "limit": {"type": "integer", "default": 12, "maximum": 100,
                          "description": "Leave it alone unless breadth IS "
                                         "the point. The default is sized to "
                                         "an agent's evidence budget, and "
                                         "every extra hit is paid for twice: "
                                         "once to read, and again in the room "
                                         "it leaves for everything after it."},
                "offset": {"type": "integer", "default": 0,
                           "description": "Skip this many before the first "
                                          "result. For paging through a long "
                                          "list you have already read, not "
                                          "for a second attempt at one."},
                "decided": {"type": "boolean",
                            "description": "true for items the minutes "
                                           "recorded an outcome for; false for "
                                           "items with "
                                           "no recorded outcome - which means "
                                           "the minutes are missing, not that "
                                           "nothing happened. It FILTERS, so "
                                           "read an empty result as 'not "
                                           "among the decided ones' and not "
                                           "as 'not there'."},
                "outcome": OUTCOME, "phase": PHASE,
            },
        },
    },
    {
        "name": "get_item",
        "description":
            "Everything about one agenda item: its official title, department, "
            "staff recommendation, the outcome and the minutes' own "
            "sentence recording it VERBATIM, the "
            "county's own PDF, its place in a case thread, and - if the "
            "meeting was recorded - the transcript of the item itself. "
            "Call this after a search puts an item in play; it is how you get "
            "from 'this looks relevant' to what actually happened.\n"
            "Call it with no offset and the record comes back whole. The "
            "SPEECH comes back in a window of turns: most items are short "
            "enough to arrive complete, and a long one hands you "
            "`next_offset` - pass that back as `offset` for the turns after "
            "it, and again, until `next_offset` is null. A continuation "
            "carries the turns alone, because the record it would repeat is "
            "the record you already have. `turns` is the total, so you can "
            "skip ahead instead of walking.",
        "run": get_item,
        "parameters": {
            "type": "object", "required": ["item_id"],
            "properties": {
                "item_id": {"type": "integer",
                            "description": "As returned by any search, in "
                                           "`id`."},
                "outline": {"type": "boolean", "default": False,
                            "description": "Return the record in full but the "
                                           "transcript as one line per turn: "
                                           "who spoke, when, and the first "
                                           "few words. Use it when an item "
                                           "might be long and you want to "
                                           "know what opening it costs before "
                                           "you spend it. `census` says how "
                                           "many turns there are and who did "
                                           "the talking, and it counts the "
                                           "WHOLE item rather than the window, "
                                           "so it answers 'is the person I am "
                                           "asking about even in here' without "
                                           "reading it."},
                "offset": {"type": "integer",
                           "description": "Turn to start at. LEAVE IT OFF THE "
                                          "FIRST CALL: that is the one that "
                                          "carries the record, the case "
                                          "thread and the census. After that, "
                                          "pass back the `next_offset` you "
                                          "were handed, and keep going until "
                                          "it comes back null.\n"
                                          "You are not held to walking. "
                                          "`turns` is the whole length, so an "
                                          "offset of your own skips ahead - "
                                          "and a NEGATIVE ONE COUNTS FROM THE "
                                          "END: -80 is the last 80 turns, "
                                          "which is where a motion and its "
                                          "vote are. Reach for that rather "
                                          "than paging to it, because the "
                                          "opening of an item is usually "
                                          "staff presenting.\n"
                                          "`census` is whole-item whatever "
                                          "the window holds, so it already "
                                          "says who is in the part you have "
                                          "not read. Every appearance is "
                                          "listed either way, each with "
                                          "`count` for its own length and "
                                          "`returned` for how much of it is "
                                          "in this window."},
                "limit": {"type": "integer", "default": ITEM_WINDOW,
                          "maximum": archive.MAX_ITEM_LINES,
                          "description": "Turns to return. Leave it alone "
                                         "unless you have read the window and "
                                         "need the rest. The maximum is the "
                                         "longest item the archive holds, so "
                                         "asking for it is asking for the "
                                         "whole thing - which for the longest "
                                         "item is around 387k tokens, and is "
                                         "the cost the default exists to "
                                         "avoid."},
            },
        },
    },
    {
        "name": "get_case",
        "description":
            "One matter followed through every meeting that took it up, in "
            "order, with what each one decided. {recurring} cases span more "
            "than one meeting; {deep_case} was heard at {deep_case_meetings} "
            "of them. Use this instead of searching again "
            "when you already have a case id - it reaches meetings with no "
            "recording, which searching the transcript cannot.\n"
            "The appearances and their outcomes always come back whole - that "
            "is what this tool is for. What was SAID across them windows "
            "exactly as get_item's does: pass back `next_offset` until it is "
            "null, or a negative offset for the end.",
        "run": get_case,
        "parameters": {
            "type": "object", "required": ["case_id"],
            "properties": {
                "case_id": {"type": "string",
                            "description": "e.g. 'PDE-25-7738'."},
                "offset": {"type": "integer",
                           "description": "Turn to start at, across every "
                                          "hearing in order. LEAVE IT OFF THE "
                                          "FIRST CALL: that is the one "
                                          "carrying the appearances, the "
                                          "outcomes and the census. Then pass "
                                          "back `next_offset` until it comes "
                                          "back null. NEGATIVE COUNTS FROM "
                                          "THE END. Every hearing stays "
                                          "listed whatever the window holds, "
                                          "so a case heard five times still "
                                          "looks like five."},
                "limit": {"type": "integer", "default": CASE_WINDOW,
                          "maximum": archive.MAX_CASE_LINES,
                          "description": "Turns to return. Leave it alone "
                                         "unless you have read the window and "
                                         "need the rest."},
            },
        },
    },
    {
        "name": "get_document",
        "description":
            "The county's own agenda or minutes for a meeting, as text - the "
            "document the record was parsed FROM, rather than this archive's "
            "reading of it. Use it when an item's title and outcome are not "
            "enough and you need the county's own wording.\n"
            "Only agendas and minutes are held. The agenda PACKET, which is "
            "where a contract, a staff memo or an exhibit would be, is not "
            "collected and cannot be fetched here.",
        "run": get_document,
        "parameters": {
            "type": "object", "required": ["file_id"],
            "properties": {
                "file_id": {"type": "integer",
                            "description": "From the `files` list on get_item "
                                           "or get_meeting. NOT an item's "
                                           "`file_number` - that is the "
                                           "county's case identifier, like "
                                           "'PDD24-0129', and means something "
                                           "else entirely."},
                "offset": {"type": "integer", "default": 0,
                           "description": "Character to start at, counted "
                                          "against `text_chars` - what you "
                                          "receive - and not against `chars`, "
                                          "which is the raw extraction before "
                                          "its column padding is collapsed. A "
                                          "long document comes back in "
                                          "windows: pass back `next_offset` "
                                          "until it comes back null, exactly "
                                          "as get_item and get_case do. There "
                                          "is no page to ask for: the "
                                          "extraction is flat text, though "
                                          "the printed page footers survive "
                                          "it and can be searched."},
                "limit": {"type": "integer", "default": 20000,
                          "description": "Characters to return. Leave it "
                                         "alone unless you have read the "
                                         "window and need the rest."},
            },
        },
    },
    {
        "name": "get_meeting",
        "description":
            "One meeting's agenda in published order, with each item's outcome "
            "and its offset into the recording. Use it to see what else "
            "happened around an item, or to establish what a meeting covered.\n"
            "The meeting comes back whole; its AGENDA comes in a window, "
            "because a Board meeting runs to hundreds of items against a "
            "median of 17. `agenda_items` is how many it has and `items` is "
            "the window you were given. Pass back `next_offset` until it is "
            "null, the same way get_item and get_case page.",
        "run": get_meeting,
        "parameters": {
            "type": "object", "required": ["meeting_id"],
            "properties": {
                "meeting_id": {"type": "integer"},
                "offset": {"type": "integer",
                           "description": "Agenda item to start at, in "
                                          "published order, counted against "
                                          "`agenda_items`. LEAVE IT OFF THE "
                                          "FIRST CALL: that one carries the "
                                          "date, the body, the roster, the "
                                          "recordings and the files. NEGATIVE "
                                          "COUNTS FROM THE END, which on an "
                                          "agenda is board reports and "
                                          "adjournment."},
                "limit": {"type": "integer", "default": MEETING_WINDOW,
                          "maximum": 500,
                          "description": "Agenda items to return. Most of a "
                                         "long agenda is consent, so read the "
                                         "window before asking for more."},
            },
        },
    },
]

BY_NAME = {s["name"]: s for s in SPECS}

# What a model gets handed. `run` is ours, not theirs.
def manifest(con):
    """The specs a model is handed: no callable, and every number measured.

    A function and not a constant, because a constant is exactly how the old
    counts went stale - it was built at import and nothing could ever move it.
    """
    f = facts(con)
    return [fill({k: v for k, v in s.items() if k != "run"}, f) for s in SPECS]


# The archive's own public address, and the reason a tool result can be
# clicked. READ FROM THE CONTAINER, not baked in: the UI and the API are two
# processes in one container (deploy/entrypoint.sh) and SITE_URL is already
# set there for the sitemap and the canonical tags, so the API gets it free
# and one image still serves any county.
SITE = (os.environ.get("SITE_URL") or "").rstrip("/")


def _url(path):
    """An absolute URL, or nothing at all.

    NOTHING, rather than a relative path or a localhost guess. A tool result
    crosses into somebody else's client and is read by a model that cannot
    resolve a relative link and will happily print a dead one - and a citation
    a reader cannot open is worse than a citation that does not pretend to be
    a link. `ui/lib/site.ts` makes the opposite choice for the same reason:
    it falls back to localhost so a misconfigured DEPLOY is obvious on sight,
    where here the audience is a stranger's assistant.
    """
    return f"{SITE}{path}" if SITE else None


def _attach_urls(name, out):
    """Put the reader's address on every row a caller might cite.

    ONE PLACE, at the single entry point, because the alternative is six
    projections each remembering to do it and one of them not. Only rows that
    are actually cited get one: a transcript hit, a published item, a case, a
    meeting. Not the individual turns inside get_item - they are utterances
    rather than citable passages, and a URL on each would put back the
    per-row repetition the window exists to remove.

    The query shape is the meeting page's own contract (`?v=` and `?t=`, read
    in ui/app/meeting/[id]/page.tsx), so a link lands on the words rather than
    at the top of a six-hour recording.
    """
    if not SITE or not isinstance(out, dict):
        return out

    def moment(h):
        if h.get("meeting_id") and h.get("video_id") and h.get("start") is not None:
            return _url(f"/meeting/{h['meeting_id']}?v={quote(str(h['video_id']))}"
                        f"&t={int(h['start'])}")
        return None

    for h in out.get("hits") or []:
        if isinstance(h, dict):
            h["url"] = moment(h)
    for i in out.get("items") or []:
        # get_meeting's items are agenda rows; search_record's are too.
        if isinstance(i, dict) and i.get("id"):
            i["url"] = _url(f"/item/{i['id']}")
    item = out.get("item")
    if isinstance(item, dict) and item.get("id"):
        item["url"] = _url(f"/item/{item['id']}")
    for st in out.get("steps") or []:
        if isinstance(st, dict) and st.get("id"):
            st["url"] = _url(f"/item/{st['id']}")
    if out.get("case_id"):
        out["url"] = _url(f"/case/{quote(str(out['case_id']), safe='')}")
    meeting = out.get("meeting")
    if isinstance(meeting, dict) and meeting.get("id"):
        meeting["url"] = _url(f"/meeting/{meeting['id']}")
    if name == "get_document" and out.get("meeting_id"):
        out["url"] = _url(f"/meeting/{out['meeting_id']}")
    return out


class ToolError(Exception):
    """A bad call, as opposed to a call that legitimately found nothing."""


def call(con, name, args):
    """Invoke one tool by name. The single entry point, for both callers."""
    spec = BY_NAME.get(name)
    if not spec:
        raise ToolError(f"no such tool: {name}")
    props = spec["parameters"]["properties"]
    args = _clean(args or {})
    unknown = set(args) - set(props)
    if unknown:
        raise ToolError(f"{name}: unknown argument(s) {sorted(unknown)}")
    for req in spec["parameters"].get("required", []):
        if req not in args:
            raise ToolError(f"{name}: {req} is required")
    # Query strings arrive from a URL, where everything is a string. Coerce to
    # the declared type rather than letting "30" reach a LIMIT.
    for k, v in list(args.items()):
        t = props[k].get("type")
        try:
            if t == "integer":
                args[k] = int(v)
            elif t == "boolean" and isinstance(v, str):
                args[k] = v.lower() in ("1", "true", "yes")
        except (TypeError, ValueError):
            raise ToolError(f"{name}: {k} must be {t}") from None
        if props[k].get("enum") and args[k] not in props[k]["enum"]:
            raise ToolError(f"{name}: {k} must be one of "
                            f"{', '.join(props[k]['enum'])}")
        if props[k].get("maximum") is not None:
            args[k] = min(args[k], props[k]["maximum"])
    return _attach_urls(name, spec["run"](con, **args))


# --------------------------------------------------------------- the page
def search(con, query, limit=25, offset=0, **facets):
    """Both sources at once, for /search."""
    query = (query or "").strip()
    if not query:
        return {"query": "", "record": {"total": 0, "items": []},
                "transcript": {"hits": [], "offset": 0, "returned": 0,
                               "next_offset": None, "truncated": False,
                               "degraded": None}}

    # Each tool takes the facets it can honour and no others. `speaker` reaches
    # speech and has no meaning for a published agenda item; `decided` is the
    # other way round. Passing everything to both is a 400 on a URL a reader
    # can perfectly reasonably construct, and forcing the page to know which
    # facet belongs to which tool would put that knowledge in two places.
    # The schemas already say. The rail tells the reader which is which.
    def only(name):
        allowed = BY_NAME[name]["parameters"]["properties"]
        return {k: v for k, v in facets.items() if k in allowed}

    record = call(con, "search_record", dict(
        query=query, limit=limit, offset=offset, **only("search_record")))
    # OFFSET REACHES BOTH ARMS. It used to reach only the record, so /search
    # page 2 renewed the published items and re-served the same 25 moments
    # beside them.
    heard = call(con, "search_transcript", dict(
        query=query, limit=limit, offset=offset, **only("search_transcript")))
    return {"query": query, "record": record, "transcript": heard,
            "by_code": record.get("by_code", False)}
