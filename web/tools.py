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


def warm(device="cuda:1"):
    """Load the embedding model. Called at startup so the first reader does
    not pay for it; a failure here is not fatal, it costs the dense arm."""
    global _dense_error
    try:
        retrieve().model(device)
        _dense_error = None
    except Exception as e:                                   # noqa: BLE001
        _dense_error = f"{type(e).__name__}: {e}"
        print(f"[tools] dense retrieval unavailable - {_dense_error}",
              file=sys.stderr)
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
         "description": "Part of the meeting. 'public_comment' hears the "
                        "podium rather than the dais; 'consent' is business "
                        "passed in one motion without discussion."}
OUTCOME = {"type": "string",
           "enum": ["approved", "adopted", "denied", "withdrawn", "continued",
                    "no_action"],
           "description": "The disposition the approved minutes recorded."}


def _clean(d):
    """Drop absent arguments so a tool's own defaults apply."""
    return {k: v for k, v in d.items() if v not in (None, "", [])}


# -------------------------------------------------------- search_transcript
def search_transcript(con, query, limit=12, spread=None, speaker=None,
                      phase=None, case=None, body=None, since=None,
                      until=None, outcome=None):
    """What was SAID. Hybrid retrieval over the passage index."""
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
    return {"query": query, "hits": hits, "count": len(hits),
            "degraded": degraded or _dense_error}


def _plain(con, ranked, limit, spread=None, **f):
    """Keyword-only fallback. Same shape as retrieve.search, no `score`."""
    if not ranked:
        return []
    meta = {r["id"]: dict(r) for r in con.execute("""
        SELECT p.id, p.video_id, p.start, p."end", p.speaker, p.text,
               p.phase, p.agenda_item_id,
               ai.title AS item, ai.code, ai.case_id, ai.section,
               ai.outcome, ai.recommendation, ai.department,
               ai.source AS item_source,
               v.title, v.upload_date, v.kind,
               v.meeting_id, mt.date AS meeting_date, mt.body
        FROM passages p
        JOIN videos v ON v.id = p.video_id
        LEFT JOIN meetings mt ON mt.id = v.meeting_id
        LEFT JOIN agenda_items ai ON ai.id = p.agenda_item_id
        WHERE p.id = ANY(%s)""", (ranked[:600],))}
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
                "query": {"type": "string",
                          "description": "Natural language, or exact terms. "
                                         "Both arms run on every call."},
                # The DEFAULT is the agent's budget, not the page's: /search
                # passes its own limit, and an agent that omits it and gets 30
                # passages a call spends its whole evidence budget on breadth
                # it did not ask for. Ask for more when breadth is the point.
                "limit": {"type": "integer", "default": 12, "maximum": 100},
                "spread": {"type": "integer",
                           "description": "Max hits per meeting. Set it (2-3) "
                                          "for 'how did this evolve' "
                                          "questions, or the top hits pile "
                                          "into whichever meeting discussed "
                                          "it most and the earliest "
                                          "occurrence never surfaces."},
                "speaker": {"type": "string",
                            "description": "Exact speaker name as the archive "
                                           "holds it, e.g. 'Mariano'. Names "
                                           "are inferred from voice and can "
                                           "be wrong."},
                "phase": PHASE, "case": CASE, "body": BODY,
                "since": SINCE, "until": UNTIL, "outcome": OUTCOME,
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
                "query": {"type": "string",
                          "description": "Subject words, or an item code or "
                                         "case number verbatim."},
                "limit": {"type": "integer", "default": 12, "maximum": 100},
                "offset": {"type": "integer", "default": 0},
                "decided": {"type": "boolean",
                            "description": "true for items the minutes "
                                           "disposed of; false for items with "
                                           "no recorded outcome - which means "
                                           "the minutes are missing, not that "
                                           "nothing happened."},
                "order": {"type": "string",
                          "enum": ["relevance", "decided", "recent"],
                          "default": "relevance",
                          "description": "'decided' floats items the minutes "
                                         "settled above ones they only "
                                         "continued - use it when you are "
                                         "asking what HAPPENED to a matter, "
                                         "because a case typically carries "
                                         "five continuances and one approval "
                                         "and the approval is the answer. Use "
                                         "'relevance' when you are still "
                                         "looking for the matter itself."},
                "body": BODY, "outcome": OUTCOME, "phase": PHASE,
                "case": CASE, "since": SINCE, "until": UNTIL,
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
