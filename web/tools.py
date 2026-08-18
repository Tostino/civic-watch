"""The retrieval surface, as callable tools (UI_REQUIREMENTS D9).

`bin/ask.py` runs a fixed pipeline - `plan() → retrieve() → read() → answer()`.
The planner emits its queries once and the pipeline executes them blindly, so
nothing downstream can notice a bad result and try again. This corpus punishes
that specifically: a vote passage contains no topic words, so the wording that
finds an item's *discussion* puts its *decision* at rank 33-58, below any depth
worth reading. `retrieve.decisions_in_play()` is a hard-coded patch over that
one case, and there are others.

The general fix is a caller that can look, notice it found nothing useful, and
search again with different words or a different tool. That requires the
retrieval surface to be a set of named, described, schema'd operations rather
than a sequence, which is what this module is.

**One surface, two callers.** The `/search` page and the agent call exactly
these tools with exactly these arguments. That is deliberate: it is the only
way to be sure that what a reader can find by hand, the agent can also find -
and when a search behaves oddly on the page, the same call reproduces it.

Schemas are JSON Schema, so the manifest below can be handed to a model as
tool definitions unchanged.

Nothing here decides how a result LOOKS. Tools return the archive's own
structure - ids, codes, outcomes, offsets - and one component decides how to
render each kind (D3, and the same rule as web/archive.py).
"""
import sys
import time

import archive

# `retrieve` needs torch and a GPU for its dense arm, and pulling that in at
# import time would cost the API server 6 seconds and 2 GB whether or not
# anybody searches. Bound on first use instead.
_retrieve = None

# Whether the dense arm is usable. A search that silently drops to keywords is
# a search that quietly got worse, so this is reported in the result rather
# than swallowed (R3.2).
_dense_error = None


def retrieve():
    global _retrieve, _dense_error
    if _retrieve is None:
        import retrieve as r
        _retrieve = r
    return _retrieve


def warm(device=None):
    """Load the embedding model. Called at startup so the first reader does
    not pay for it; a failure here is not fatal, it costs the dense arm.

    It says so EITHER WAY, on purpose. A healthy server used to print nothing
    here, so the only way to know the dense arm was alive was to grep for the
    absence of the failure line - and "no output is the pass" is a check nobody
    runs and nobody trusts. This is the one degradation in the whole stack that
    does not announce itself: search keeps answering, on BM25 alone, and looks
    fine until someone notices paraphrase queries stopped working.
    """
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
           "description": "The disposition the approved minutes recorded."}


def _clean(d):
    """Drop absent arguments so a tool's own defaults apply."""
    return {k: v for k, v in d.items() if v not in (None, "", [])}


def canonical_speaker(con, name):
    """Fold a board member's full name back to the surname the index holds.

    `passages.speaker` is keyed by surname and the filter is an equality test,
    so "Kathryn Starkey" matches nothing at all - it returns zero hits and
    looks exactly like "she never said that", which is the worst failure this
    archive can produce. The agent now READS full names in every passage line
    it is shown, so it will reach for one here; so will a reader who types the
    name they just saw on a chip.

    The inverse of display_name, and deliberately not its mirror image: this
    accepts either form, because both are now in circulation.
    """
    if not name:
        return name
    r = con.execute(
        "SELECT surname FROM people WHERE lower(full_name) = lower(%s) "
        "AND lower(surname) <> lower(%s)", (name, name)).fetchone()
    return r[0] if r else name


# -------------------------------------------------------- search_transcript
def search_transcript(con, query, limit=12, spread=None, speaker=None,
                      phase=None, case=None, body=None, since=None,
                      until=None, outcome=None):
    """What was SAID. Hybrid retrieval over the passage index."""
    speaker = canonical_speaker(con, speaker)
    r = retrieve()
    hits, degraded = [], None
    try:
        hits = r.search(query, limit=limit, spread=spread, speaker=speaker,
                        phase=phase, case=case, body=body, since=since,
                        until=until, outcome=outcome, con=con)
    except Exception as e:                                   # noqa: BLE001
        # The dense arm is a GPU and someone else's library. Losing it costs
        # recall on paraphrase; it must not cost the reader their search, and
        # it must not pretend the search was as good as usual.
        degraded = f"{type(e).__name__}: {e}"
        ranked = r.rrf(r.bm25(con, query, 300), r.thread_hits(con, query, 200))
        hits = _plain(con, ranked, limit, speaker=speaker, phase=phase,
                      case=case, body=body, since=since, until=until,
                      outcome=outcome, spread=spread)
    # Here rather than in either arm's projection, so both arms describe a
    # speaker the same way and neither pays for the 575 candidates it threw
    # out. This is the one place a hit becomes a hit.
    return {"query": query, "hits": speaker_sure(con, hits), "count": len(hits),
            "degraded": degraded or _dense_error}


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


def speaker_sure(con, rows):
    """Fill in HOW WELL each passage's speaker name is known, in place.

    `passages.speaker` is baked at index time and arrives looking equally
    certain whether a person confirmed it or a voice model guessed. It is not:
    of 234,000 named utterances, 2,786 were stated by a person, 208,495 were
    matched to a voice at that meeting, and 22,682 carry nothing but the name
    their voice goes by across the whole archive. An answer that says
    "Commissioner Oakley moved" off the last of those has invented a person's
    vote, which is the worst thing this archive can do to somebody - and until
    this existed, every surface printed all three identically.

    The reduction from utterances to a passage lives in the database
    (bin/schema.sql, `passage_speaker`), so the page and the agent cannot come
    to different views of the same row. Two fields come back, and they are
    the two SpeakerChip draws: `name_human` and `name_basis`.

    SEPARATE FROM PASSAGE_HIT ON PURPOSE, and this is the whole reason it is a
    function rather than two more columns. `utterance_speaker` resolves a name
    through four levels, one of which recomputes the archive-wide cluster
    majority, and Postgres runs that per row: measured, 620 ms for 600
    passages against 2 ms without it. Both search paths rank 600 candidates
    and return 25, so joining in the projection tripled the cost of every
    search - a whole search's worth of time again - to describe 575 rows
    nobody would see. Called on what SURVIVED, it is 16 ms.
    """
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
#
# A saved answer stores WHAT it cited and not the words (web/answers.py). These
# two read the words back at render time, which is what makes a shared answer
# quote the archive as it stands rather than as it stood: a redaction applied
# since is already in `passages.text`, a corrected speaker name is already on
# the row, and neither needs anything to go back and find old copies.
def passages_at(con, ranges):
    """Passages by the utterance range each covers, keyed by that range.

    Keyed on `(video_id, start_idx, end_idx)` rather than on the passage id
    because `index_passages.rebuild_video` reassigns ids on every rebuild and
    says so in as many words: *nothing outside the index stores one*. The range
    is the passage's natural key - unique across all 166,998 of them - and it
    survives every rebuild that does not move boundaries.

    A range that no longer resolves is not an error. Boundaries move for
    exactly one reason: a redaction shortened a line and the passage fell under
    the indexing floor. The citation is then genuinely gone, and the caller
    says so rather than showing text from before it went.
    """
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
    speaker_sure(con, list(out.values()))
    return out


def items_at(con, ids):
    """Published items by id, keyed by id.

    Agenda item ids ARE durable - `/item/<id>` is a public URL and the archive
    already depends on it - so unlike a passage there is nothing cleverer to
    key on. The columns are `retrieve.search_items`' minus its ranking score;
    if that projection gains a field a reader needs, this is the other place
    to add it.
    """
    if not ids:
        return {}
    rows = con.execute("""
        SELECT ai.id, ai.seq, ai.code, ai.title, ai.search_title,
               ai.case_id, ai.section, ai.phase, ai.department,
               ai.recommendation, ai.disposition, ai.outcome,
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
SPECS = [
    {
        "name": "search_transcript",
        "description":
            "Search what people SAID, across 1,036 hours of recorded meetings. "
            "Hybrid: exact terms, semantic similarity, and curated case "
            "threads. Use it for argument, reasoning, objection and public "
            "comment.\n"
            "It CANNOT reach any meeting with no recording, which is most of "
            "them: only 9% of decided items have one. It also under-serves "
            "votes - the moment a board decides something contains no topic "
            "words ('all in favor say aye'), so it ranks far below the "
            "discussion of the same item. If you want the DECISION, use "
            "search_record or get_item; if you want the ARGUMENT, use this.",
        "run": search_transcript,
        "parameters": {
            "type": "object",
            "required": ["query"],
            "properties": {
                # ORDER IS PART OF THE INTERFACE. A model reads this list top
                # to bottom and reaches for what it read first, so it runs:
                # what to look for, WHERE to look (the aiming facets, which
                # cannot hide anything), how much to take back, and only then
                # the filters that exclude silently. It used to open with
                # limit, spread, speaker, phase - two shaping knobs and the
                # two traps - with since/until, the pair measured to work,
                # buried at eighth and ninth.
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
                "spread": {"type": "integer",
                           "description": "Max hits per meeting. Set it (2-3) "
                                          "for 'how did this evolve' "
                                          "questions, or the top hits pile "
                                          "into whichever meeting discussed "
                                          "it most and the earliest "
                                          "occurrence never surfaces."},
                "case": CASE, "body": BODY,
                "outcome": OUTCOME, "phase": PHASE,
                "speaker": {"type": "string",
                            "description": "A speaker name as it appears on "
                                           "the passages you have been shown, "
                                           "e.g. 'Jack Mariano' or 'Mariano' "
                                           "- board members match on either. "
                                           "RARELY worth it, and never for "
                                           "'how has X argued': 67% of "
                                           "passages carry no usable name - "
                                           "every cross-speaker exchange is "
                                           "one of them, and an exchange is "
                                           "where an argument happens - so "
                                           "this drops two thirds of the "
                                           "corpus on a name inferred from "
                                           "voice. Search the subject and "
                                           "read the names off the results."},
            },
        },
    },
    {
        "name": "search_record",
        "description":
            "Search what the county PUBLISHED: 23,122 agenda items and the "
            "dispositions its approved minutes recorded for them. This is the "
            "authoritative source for whether something passed, and it covers "
            "twelve years regardless of whether a camera was running.\n"
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
                                           "disposed of; false for items with "
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
            "staff recommendation, the minutes disposition VERBATIM, the "
            "county's own PDF, its place in a case thread, and - if the "
            "meeting was recorded - the transcript of the item itself. "
            "Call this after a search puts an item in play; it is how you get "
            "from 'this looks relevant' to what actually happened.",
        "run": lambda con, item_id: archive.item(con, item_id),
        "parameters": {
            "type": "object", "required": ["item_id"],
            "properties": {"item_id": {"type": "integer",
                                       "description": "As returned by any "
                                                      "search, in `id`."}},
        },
    },
    {
        "name": "get_case",
        "description":
            "One matter followed through every meeting that took it up, in "
            "order, with what each one decided. 1,377 cases span more than "
            "one meeting; PDE-25-7738 was heard twelve times over ten months "
            "and continued five times. Use this instead of searching again "
            "when you already have a case id - it reaches meetings with no "
            "recording, which searching the transcript cannot.",
        "run": lambda con, case_id: archive.case(con, case_id),
        "parameters": {
            "type": "object", "required": ["case_id"],
            "properties": {"case_id": {"type": "string",
                                       "description": "e.g. 'PDE-25-7738'."}},
        },
    },
    {
        "name": "get_meeting",
        "description":
            "One meeting's agenda in published order, with each item's outcome "
            "and its offset into the recording. Use it to see what else "
            "happened around an item, or to establish what a meeting covered.",
        "run": lambda con, meeting_id: archive.meeting(con, meeting_id),
        "parameters": {
            "type": "object", "required": ["meeting_id"],
            "properties": {"meeting_id": {"type": "integer"}},
        },
    },
]

BY_NAME = {s["name"]: s for s in SPECS}

# What a model gets handed. `run` is ours, not theirs.
MANIFEST = [{k: v for k, v in s.items() if k != "run"} for s in SPECS]


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
    return spec["run"](con, **args)


# --------------------------------------------------------------- the page
def search(con, query, limit=25, offset=0, **facets):
    """Both sources at once, for /search (R5.6.1).

    Two tool calls, not a third code path. The page runs the same surface the
    agent does - which is the point of D9 and the only way the two stay honest
    about each other.

    The record is not paginated together with the transcript: they are
    different objects with different totals, and interleaving them into one
    ranked list would force a comparison between "this was approved" and
    "somebody said this", which are not comparable (UI_PLAN §2).
    """
    query = (query or "").strip()
    if not query:
        return {"query": "", "record": {"total": 0, "items": []},
                "transcript": {"hits": [], "count": 0, "degraded": None}}

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
    heard = call(con, "search_transcript", dict(
        query=query, limit=limit, **only("search_transcript")))
    return {"query": query, "record": record, "transcript": heard,
            "by_code": record.get("by_code", False)}
