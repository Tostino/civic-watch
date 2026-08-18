"""Two front-ends over one archive, each specialised for its job.

    /          research  - search, read transcripts, jump to the video
    /speakers  workbench - inspect voice groups, name/split/merge them

They are separate pages because they are separate tasks: reading wants a big
player and continuous transcript, labelling wants dense lists, multi-select and
keyboard flow. Bolting the second onto the first produced a tool that could
neither list existing speakers nor split a mixed group.

A third surface arrived with the tool server: /mcp serves the same five tools
the agent calls to a model somebody else is driving (web/mcp_server.py). It is
mounted inside the reading app rather than proxied to, which is the reason
this file is ASGI at all - the previous BaseHTTPRequestHandler could not hold
an ASGI app without a loopback hop between the two.

    bin/serve.sh                 # and see that script for why, not this
    python3 web/server.py [--port 8765]
"""
import argparse
import asyncio
import contextlib
import functools
import gzip
import json
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote

import anyio
import uvicorn
from starlette.applications import Starlette
from starlette.responses import RedirectResponse, Response, StreamingResponse
from starlette.routing import Route

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))   # retrieve/ask live with the pipeline
HERE = os.path.dirname(os.path.abspath(__file__))

import psycopg                                    # noqa: E402

import admin                                     # noqa: E402
import answers                                   # noqa: E402
import archive                                   # noqa: E402
import db                                        # noqa: E402
import limits                                    # noqa: E402
import mcp_server                                # noqa: E402
import tools                                     # noqa: E402
from wire import jsonable                        # noqa: E402

def connect(write=False):
    return db.connect(autocommit=not write)


# ------------------------------------------------------------------ search
def search(con, q, kind=None, speaker=None, limit=50, offset=0):
    """Keyword search over utterances.

    websearch_to_tsquery accepts what people actually type - quoted phrases,
    OR, a leading minus to exclude - and never raises on malformed input, so
    the FTS5 escape/retry dance this replaced is simply gone.

    Ranking here is ts_rank_cd, not the BM25 used for agent retrieval. This is
    a browse-by-keyword tab where the query is usually a phrase the reader
    already has in mind; BM25's document-frequency weighting is what matters
    for the retrieval path, and that lives in bin/bm25.sql.
    """
    if not (q or "").strip():
        return {"total": 0, "results": []}
    return _search(con, q, kind, speaker, limit, offset)


def _search(con, match, kind, speaker, limit, offset):
    where, args = ["u.tsv @@ q.tsq"], [match]
    if kind and kind != "all":
        where.append("v.kind = %s")
        args.append(kind)
    if speaker:
        where.append("us.name = %s")
        args.append(speaker)
    clause = " AND ".join(where)
    # The agenda item comes along because a hit often cannot be placed without
    # it - "all in favor say aye" is meaningless until you know what was being
    # voted on, and the words themselves never say.
    base = """FROM utterances u
              CROSS JOIN (SELECT websearch_to_tsquery('english', %s) AS tsq) q
              JOIN videos v ON v.id = u.video_id
              JOIN utterance_speaker us
                     ON us.video_id = u.video_id AND us.idx = u.idx
              LEFT JOIN item_spans sp
                     ON sp.video_id = u.video_id
                    AND u.idx BETWEEN sp.start_idx AND sp.end_idx
              LEFT JOIN agenda_items s ON s.id = sp.agenda_item_id"""
    total = con.execute(f"SELECT COUNT(*) {base} WHERE {clause}",
                        args).fetchone()[0]
    # An unnamed voice is shown by its CLUSTER, not its per-meeting diarization
    # label: "Speaker 9" here and "Speaker 15" there are the same person, and
    # showing both implies they are not. The cluster id is stable across the
    # archive and is what the workbench operates on, so it doubles as a link.
    rows = con.execute(f"""
        SELECT u.video_id, u.start, u.idx, u.cluster,
               -- Name from the resolver, so this page agrees with the rebuilt
               -- one. The 'Group N' fallback stays HERE and only here: this is
               -- a curation surface where the cluster id is the thing you act
               -- on. No reader-facing page may render it (R6.2.1).
               COALESCE(us.name, 'Group ' || u.cluster, u.speaker) AS speaker,
               (us.name IS NOT NULL) AS named,
               ts_headline('english', u.text, q.tsq,
                   'StartSel=<mark>, StopSel=</mark>, MaxWords=28, MinWords=10,'
                   ' ShortWord=2, MaxFragments=2, FragmentDelimiter=" … "')
                   AS snip,
               s.title AS item, s.phase,
               s.id AS agenda_item_id, s.code, s.case_id, s.outcome,
               v.title, v.upload_date, v.kind, v.duration
        {base} WHERE {clause}
        ORDER BY ts_rank_cd(u.tsv, q.tsq) DESC, u.video_id, u.idx
        LIMIT %s OFFSET %s""",
        args + [limit, offset]).fetchall()
    return {"total": total, "results": [dict(r) for r in rows]}


def transcript(con, video_id):
    v = con.execute("SELECT * FROM videos WHERE id=%s", (video_id,)).fetchone()
    if not v:
        return None
    rows = con.execute("""
        SELECT u.idx, u.start, u."end", u.text, u.cluster,
               COALESCE(us.name, 'Group ' || u.cluster, u.speaker) AS speaker,
               (us.name IS NOT NULL) AS named
        FROM utterances u
        JOIN utterance_speaker us
          ON us.video_id = u.video_id AND us.idx = u.idx
        WHERE u.video_id=%s ORDER BY u.idx""", (video_id,)).fetchall()
    # Agenda structure, so a reader can see where they are and jump by item
    # rather than scrolling a four-hour wall of text.
    segs = con.execute("""
        SELECT sp.part, sp.start_idx, sp.end_idx, sp.start, sp."end",
               ai.phase, ai.title, ai.code, ai.case_id, ai.section,
               ai.outcome, ai.recommendation, ai.source,
               (sp.part > 0) AS continued
        FROM item_spans sp JOIN agenda_items ai ON ai.id = sp.agenda_item_id
        WHERE sp.video_id=%s ORDER BY sp.start_idx""", (video_id,)).fetchall()
    return {"video": dict(v), "utterances": [dict(r) for r in rows],
            "segments": [dict(r) for r in segs]}


def stats(con):
    r = con.execute("""
        SELECT COUNT(*) total, COUNT(*) FILTER (WHERE transcribed) done,
               SUM(duration)/3600.0 hours,
               SUM(duration) FILTER (WHERE transcribed)/3600.0 dh
        FROM videos""").fetchone()
    named = con.execute(
        "SELECT COUNT(*) FROM utterances u JOIN voice_name cn "
        "ON cn.video_id=u.video_id AND cn.cluster=u.cluster").fetchone()[0]
    return {"total": r["total"], "done": r["done"] or 0,
            "hours": r["hours"] or 0, "done_hours": r["dh"] or 0,
            "utterances": con.execute(
                "SELECT COUNT(*) FROM utterances").fetchone()[0],
            "named_utterances": named,
            "speakers": con.execute(
                "SELECT COUNT(DISTINCT name) FROM speaker_identity "
                "WHERE name IS NOT NULL").fetchone()[0],
            "kinds": [dict(k) for k in con.execute(
                "SELECT kind, COUNT(*) n, "
                "COUNT(*) FILTER (WHERE transcribed) done FROM videos "
                "GROUP BY kind ORDER BY n DESC")]}


# Archive-wide aggregates, held for a while.
#
# Two endpoints scan most of the archive to answer a question about all of it,
# and both are on the critical path of a page opening:
#
#   facets   five aggregates, four of them 30ms between them. The fifth counts
#            lines per speaker, and utterance_speaker is the resolution view -
#            override, then label, then identity, then cluster vote, checked
#            against voice affinity - so it walks all 298,737 utterances
#            through that chain. Measured at 3.0s, and /search paid it on
#            every open, which was the whole of that page's three-second load.
#   issues   eighteen regexes over 23,123 published titles and eighteen
#            tsqueries over the utterance index, ~330ms, on browse.
#
# Cached rather than precomputed into a table by bin/refresh.sh, because both
# of those functions are right to derive their answers: a phase the parser
# learns tomorrow appears in the rail by itself, and a meeting landed tonight
# reaches the issue strip the same way. A table someone has to remember to
# rebuild is exactly the drift they avoid. A TTL keeps the self-updating
# property and bounds the staleness, and nothing either one returns is a
# number a reader acts on to the minute.
class Held:
    """One derived value, rebuilt at most once every `ttl` seconds.

    The lock spans the query, so a cold cache under concurrent load runs the
    expensive call once and the rest wait for it, rather than each opening its
    own. One lock per value, not one shared: /search waiting three seconds for
    facets must not also stall a reader on browse.
    """

    def __init__(self, ttl, build):
        self.ttl, self.build = ttl, build
        self.at, self.value = 0.0, None
        self.lock = threading.Lock()

    def get(self, con):
        with self.lock:
            if self.value is None or time.monotonic() - self.at > self.ttl:
                self.value = self.build(con)
                self.at = time.monotonic()
            return self.value


# Named for the endpoint, and NOT bound as bare `facets`/`issues`: do_GET
# assigns a local called `facets` in the /api/find branch, which would make a
# module-level function of that name unreachable from the whole method -
# UnboundLocalError on the request that did not go through find, and a dict
# called as a function on the one that did.
FACETS_CACHE = Held(600, archive.facets)
ISSUES_CACHE = Held(600, archive.issues)


# Transcripts are the one big payload here: a six-hour afternoon session is
# 2,252 utterances and 665 KB of JSON, which gzips to 150 KB. The archive is
# meant to be read by residents, including on a phone on mobile data, so the
# 4.4x is worth ten lines. Below this size the compression costs more than the
# bytes it saves.
GZIP_OVER = 4096

# Seconds of silence /api/ask may go before it says something anyway.
#
# The gap between two events on that stream is one model turn, and a hard
# question's turn is minutes of nothing on the wire. Every hop between here
# and the browser arms an idle timer on that quiet: Next's rewrite proxy at 30s
# (experimental.proxyTimeout, ui/next.config.ts), nginx at 60s unless told
# otherwise (deploy/nginx-proxy-manager.md), and whatever a tunnel or CDN adds
# if one is ever put in front. Raising each of them is necessary and is not
# sufficient - the next hop somebody adds has its own default, and the failure
# it produces is silent and looks like the archive breaking under exactly the
# questions worth asking.
#
# So the stream stops being quiet. A line beginning with ':' is a comment in
# the SSE grammar, invisible to EventSource, and it resets every idle timer in
# the chain at once. Ten seconds is well inside the tightest default and costs
# three bytes a beat. It is also what let ASK_DEADLINE go to seven minutes:
# with the stream never idle, how LONG a run takes stopped being a proxy's
# business at all.
HEARTBEAT = 10

# ------------------------------------------------------------------ replies
def _json(request, body, code=200, headers=None):
    """A JSON response, gzipped over `GZIP_OVER` if the caller can take it.

    Hand-rolled rather than GZipMiddleware, and deliberately. The middleware
    compresses everything it is given, including the event stream - and a
    gzip encoder buffers until it has a block, which is the exact failure
    /api/ask spent a week on (see the note on Cache-Control below). This
    touches one response at a time and cannot reach the streaming one.
    """
    if isinstance(body, (dict, list)):
        body = json.dumps(body, default=jsonable).encode()
    elif isinstance(body, str):
        body = body.encode()
    out = dict(headers or {})
    if (len(body) > GZIP_OVER
            and "gzip" in request.headers.get("Accept-Encoding", "")):
        body = gzip.compress(body, 6)
        out["Content-Encoding"] = "gzip"
        out["Vary"] = "Accept-Encoding"
    return Response(body, status_code=code, media_type="application/json",
                    headers=out)


def _not_found(request, exc=None):
    return _json(request, {"error": "not found"}, 404)


def _not_allowed(request, exc=None):
    return _json(request, {"error": "not found"}, 405)


def reads(fn):
    """An endpoint that needs the database, with the connection closed after.

    EVERY READ ENDPOINT LEAKED ITS CONNECTION WITHOUT THIS, and the database
    is what ran out. Measured on the deployed container: 91 backends in state
    'idle', all from one client address, opened about one every 25 seconds and
    never released, until 100 of the server's 100 connections were taken and
    it began refusing new ones with "sorry, too many clients already". Every
    one of them had the same last query - the passage projection from
    retrieve.search - because /api/find is a GET and the search page is what a
    reader uses most.

    A missed close does not break the request that missed it; it breaks some
    other request, minutes later, on a different endpoint, once the server as
    a whole runs out - and by then the leaking path is the only one that looks
    innocent. So no endpoint opens its own: this decorator is the only place
    a read connection comes from, which is what makes forgetting impossible
    rather than merely unlikely.

    Refcounting is not the backstop it appears to be. A psycopg connection and
    its cursors reference each other, so a dropped connection is a CYCLE: it
    is freed by the cyclic collector on its own schedule rather than the
    moment the frame goes, which is exactly how this leaked slowly enough to
    look like something else. Closing it here is deterministic and costs
    nothing.

    Endpoints stay `def`, not `async def`, on purpose. Starlette runs a sync
    endpoint on a worker thread, so psycopg keeps blocking exactly as it did
    under the threaded server and no query has to be rewritten.
    """
    @functools.wraps(fn)
    def endpoint(request):
        con = None
        try:
            con = connect()
            return fn(request, con)
        except psycopg.errors.DataError as e:
            return _json(request, {"error": str(e)}, 400)
        except Exception as e:                                # noqa: BLE001
            return _json(request, {"error": str(e)}, 500)
        finally:
            if con is not None:
                con.close()
    return endpoint


def _one(request):
    """`?k=v` reader with a default. Query values are strings; tools.call and
    the endpoints do the coercion, exactly as they did off parse_qs.

    FIRST value, not last, where a caller repeats a parameter. That is what
    `parse_qs(...)[k][0]` did here for years, and Starlette's mapping returns
    the last - so `?q=a&q=b` would quietly start searching for something else
    after this rewrite. Nobody writes that URL on purpose; a form or a proxy
    that appends rather than replaces writes it by accident, which is exactly
    the case where the answer should not depend on which version is running.
    """
    def one(k, d=None):
        got = request.query_params.getlist(k)
        return got[0] if got else d
    return one


# ------------------------------------------------------- reading (public)
# Old bookmarks to ?id= URLs are still redirected: they cost two lines and
# they were real links.
def redirect_item(request):
    i = request.query_params.get("id")
    return RedirectResponse(f"/item/{int(i)}" if i else "/", status_code=302)


def redirect_case(request):
    c = request.query_params.get("id")
    return RedirectResponse(f"/case/{quote(c, safe='')}" if c else "/",
                            status_code=302)


# NOT a redirect to "/" - this server has no "/" to send them to, and
# pointing it at itself is an infinite loop. It answers plainly that it is
# the API.
def what_this_is(request):
    return _json(request, {"error": "this is the archive's JSON API; the "
                                    "site is served by the UI"}, 404)


@reads
def api_search(request, con):
    one = _one(request)
    return _json(request, search(con, one("q", ""), one("kind"),
                                 one("speaker"),
                                 min(int(one("limit", 50)), 200),
                                 int(one("offset", 0))))


# Keyed on a VIDEO id. It was called /api/meeting/<id> while /api/agenda/<id>
# took a MEETING id - two keys, near-identical names, and a trap that should
# not survive the rebuild (D7).
@reads
def api_video(request, con):
    d = transcript(con, request.path_params["video_id"])
    return _json(request, d) if d else _json(request, {}, 404)


# ------------------------------------------------------------ rebuilt UI
@reads
def api_meetings(request, con):
    one = _one(request)
    hr = one("recording")
    return _json(request, archive.meetings(
        con, one("body"), one("year"), None if hr is None else hr == "1",
        one("when", "past"), min(int(one("limit", 200)), 500),
        int(one("offset", 0)), one("month")))


@reads
def api_bodies(request, con):
    return _json(request, archive.bodies(con))


@reads
def api_overview(request, con):
    return _json(request, archive.overview(con, _one(request)("body")))


@reads
def api_highlights(request, con):
    one = _one(request)
    return _json(request, archive.highlights(
        con, min(int(one("limit", 6)), 120),
        divided_limit=min(int(one("divided", 6)), 120)))


@reads
def api_issues(request, con):
    return _json(request, ISSUES_CACHE.get(con))


# --------------------------------------------------------- retrieval (D9)
# The tool surface, and the ways in. /api/tools is the manifest a model gets
# handed; /api/tool/<name> invokes one; /mcp serves the same five to a model
# somebody else is driving (web/mcp_server.py). /api/find is the page's call,
# and it is nothing but two of these tools - the page and the agent share one
# surface on purpose, so what a reader can find by hand the agent can too.
# The same measured numbers the tool descriptions and the system prompts quote
# (tools.facts). Page copy reads them from here rather than typing them:
# "23,122 published agenda items" was 23,130 and "283 recorded meetings" was
# 290 while both sat in JSX, which is the defect this endpoint exists to end.
@reads
def api_facts(request, con):
    return _json(request, tools.facts(con))


@reads
def api_tools(request, con):
    # `mcp` is here so /about can tell a reader what the tool endpoint will
    # refuse without that number being typed into the copy twice. The manifest
    # needs a connection for the same reason: the counts inside those
    # descriptions are measured, not typed (tools.facts).
    return _json(request, {"tools": tools.manifest(con),
                           "dense": tools._dense_error,
                           "mcp": {"path": "/mcp", **limits.mcp_public()}})


@reads
def api_tool(request, con):
    one = _one(request)
    args = {k: one(k) for k in request.query_params}
    try:
        return _json(request, tools.call(con, request.path_params["name"],
                                         args))
    except tools.ToolError as e:
        return _json(request, {"error": str(e)}, 400)


@reads
def api_find(request, con):
    one = _one(request)
    facets = {k: one(k) for k in ("body", "outcome", "phase", "case",
                                  "speaker", "since", "until") if one(k)}
    if one("decided"):
        facets["decided"] = one("decided") == "1"
    try:
        return _json(request, tools.search(
            con, one("q", ""), min(int(one("limit", 25)), 100),
            int(one("offset", 0)), **facets))
    except tools.ToolError as e:
        return _json(request, {"error": str(e)}, 400)


@reads
def api_facets(request, con):
    return _json(request, FACETS_CACHE.get(con))


@reads
def api_meeting(request, con):
    d = archive.meeting(con, request.path_params["meeting_id"])
    return _json(request, d) if d else _json(request, {}, 404)


@reads
def api_transcript(request, con):
    d = archive.transcript(con, request.path_params["video_id"])
    return _json(request, d) if d else _json(request, {}, 404)


# A kept run of the agent (web/answers.py), which is what a shared /ask/<id>
# link reads. Free, unlike the endpoint that produced it.
@reads
def api_answer(request, con):
    d = answers.load(con, request.path_params["answer_id"])
    if not d:
        return _json(request, {"error": "no such answer"}, 404)
    # A minute, and deliberately not `immutable`. The ROW barely changes, but
    # this response is not the row: every quote in it is read out of
    # `passages` on the way past, which is the whole mechanism by which a
    # redaction reaches a saved answer. Any cache lifetime is therefore a
    # window in which an address somebody has removed is still being served,
    # so this is as short as is worth having - the endpoint is two indexed
    # queries and no model.
    return _json(request, d, headers={"Cache-Control": "public, max-age=60"})


@reads
def api_stats(request, con):
    return _json(request, stats(con))


@reads
def api_item(request, con):
    d = archive.item(con, request.path_params["item_id"])
    return _json(request, d) if d else _json(request, {}, 404)


@reads
def api_case(request, con):
    d = archive.case(con, request.path_params["case_id"])
    return _json(request, d) if d else _json(request, {}, 404)


# R5.3.5 asks for the county's own document, inline. It cannot simply be
# framed from source: CivicClerk serves every file with
#
#     content-disposition: attachment; filename=851.pdf
#
# which makes a browser download it rather than render it, so a cross-origin
# <iframe> shows nothing at all. This re-serves the identical bytes with
# `inline`, changing the disposition and nothing else - the document is still
# the county's, unaltered, and the direct link is offered alongside so a
# reader can go to the source themselves.
@reads
def api_file(request, con):
    file_id = request.path_params["file_id"]
    row = con.execute(
        "SELECT file_id, kind, name FROM portal_files WHERE file_id = %s",
        (file_id,)).fetchone()
    if not row:
        return _json(request, {"error": "no such file"}, 404)
    req = urllib.request.Request(
        archive.FILE.format(file_id=file_id),
        headers={"Accept": "application/pdf",
                 "User-Agent": "pasco-meeting-archive/1.0 "
                               "(research; contact via repo)"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            blob = r.read()
    except (urllib.error.URLError, TimeoutError) as e:
        # The upstream is someone else's server (D2's argument, applied to
        # documents): say so plainly rather than rendering a broken frame.
        return _json(request, {"error": f"the county's portal did not serve "
                                        f"this file: {e}"}, 502)
    name = f"{(row['kind'] or 'document').lower()}-{file_id}.pdf"
    return Response(blob, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="{name}"',
        "Cache-Control": "public, max-age=86400"})


# ------------------------------------------------------------------- ask
# Headers every event-stream response here carries.
#
# `no-transform` is the load-bearing half. Next's dev proxy sits in front of
# this and gzips anything whose client sent Accept-Encoding, which every
# browser does - and a gzip stream buffers until it has enough input to emit a
# block. Measured: `curl -N` saw every event instantly, `curl -N -H
# "Accept-Encoding: gzip"` through the same proxy saw nothing, and the page sat
# blank for ninety seconds and then reported the connection dropped.
# no-transform is the standard way to tell an intermediary to leave a body
# alone; X-Accel-Buffering is nginx's version of the same instruction, for
# production.
#
# `Connection: close` is gone with the old handler and is not missed. It was
# there because BaseHTTPRequestHandler defaults to HTTP/1.0, where a response
# with no Content-Length is terminated by closing the socket. uvicorn speaks
# HTTP/1.1 and frames a streaming body as chunked, which terminates it
# properly without giving up the connection.
SSE_HEADERS = {"Cache-Control": "no-cache, no-transform",
               "X-Accel-Buffering": "no"}

# 2 KB of comment before anything real. Something between here and the
# EventSource holds a small response back until its buffer fills: `curl -N`
# saw every event the instant it was written and the browser saw nothing for
# ninety seconds, then reported the connection dropped. Padding past the
# threshold is the standard remedy and the only one that does not depend on
# knowing which layer is at fault. A line beginning with ':' is a comment in
# the SSE grammar, so this is invisible to the client.
PAD = b":" + b" " * 2048 + b"\n\n"


def _event(name, payload):
    return (f"event: {name}\n"
            f"data: {json.dumps(payload, default=jsonable)}\n\n").encode()


def api_ask(request):
    """Bound what a public, unauthenticated, PAID endpoint can be made to
    spend, before it spends any of it.

    Two ways to say no, because the two callers cannot both be served by one.
    A browser reaches this through EventSource, which exposes neither the
    status code nor the body of a failed response to the page - all it gets is
    a bare `error`, so a 429 would show a reader "something went wrong" and
    never the sentence telling them to come back in four minutes. Anything
    else (curl, a script, a monitor, a WAF counting 429s) wants the real
    status. EventSource always sends `Accept: text/event-stream`, which is a
    clean way to tell them apart.

    Either way no model is called and nothing is paid for.
    """
    question = request.query_params.get("q", "")
    try:
        release = limits.reserve(limits.client_ip(request), question)
    except limits.Throttled as t:
        if "text/event-stream" not in request.headers.get("Accept", ""):
            headers = ({"Retry-After": str(t.retry_after)}
                       if t.retry_after else None)
            code = 400 if t.kind in ("empty", "length") else 429
            return _json(request, {"error": t.message}, code, headers)
        # Refusals must happen HERE, before the stream starts: once the 200
        # and the event-stream headers are out, the only way left to say no is
        # inside the stream, where a proxy cannot see it and a status code
        # cannot be set.
        body = PAD + _event("error", {"error": t.message,
                                      "retry_after": t.retry_after})
        return Response(body, media_type="text/event-stream",
                        headers=SSE_HEADERS)
    return StreamingResponse(_ask_stream(question, release),
                             media_type="text/event-stream",
                             headers=SSE_HEADERS)


def _ask_stream(question, release):
    """Server-sent events: the agent takes minutes on a hard question, so what
    it is DOING is streamed rather than leaving the page on a bare spinner
    (R5.5.1).

    What streams is the agent's actual tool calls, not four fixed captions.
    The stages are whatever it decides to do - that is the point of D9 - and a
    reader watching "search_record: school zone speed cameras -> 0 items"
    learns something a progress bar cannot tell them.

    ONE WRITER, which the threaded version could not manage. There, the run
    and the heartbeat were two threads writing the same socket under a lock,
    because a comment spliced into the middle of an event corrupts the framing
    of both. Here the run is the only producer, it produces into a queue, and
    the heartbeat is what this generator emits when that queue has said
    nothing for HEARTBEAT seconds. The lock is gone because the hazard is.

    Starlette iterates a sync generator on a worker thread, so `q.get` blocks
    the way it always did without holding the event loop.
    """
    q = queue.Queue()
    # Set when the reader is gone. `on_event` raises on it, which unwinds
    # agent.ask from inside whatever call it is in - the same effect the old
    # BrokenPipeError had, and the reason this is not merely tidy: without it
    # a reader who closes the tab leaves a paid run going to completion for
    # nobody.
    gone = threading.Event()

    def on_event(stage, detail):
        if gone.is_set():
            raise BrokenPipeError("the reader went away")
        q.put(("stage", {"stage": stage, **detail}))

    def work():
        con = None
        try:
            import agent
            import ask as llm
            # Check the key BEFORE the first stage event. Otherwise the reader
            # is told the agent is thinking and only then that it never could.
            llm.api_key()
            con = connect()
            result = agent.ask(question, con, on_event=on_event)
            # Filed before it is sent, because the id travels IN the answer
            # event and the page navigates to /ask/<id> the moment it arrives.
            # A failure here is NOT fatal: the reader has waited minutes for
            # this and is owed it whether or not it could be kept - without an
            # id the page simply stays put and renders the answer itself.
            # `con` is autocommit (connect(write=False)), so the row lands on
            # its own.
            try:
                result["id"] = answers.save(con, result)
            except Exception as e:                            # noqa: BLE001
                print(f"answer not saved: {type(e).__name__}: {e}", flush=True)
            q.put(("answer", result))
        except BrokenPipeError:
            pass                          # the reader left; nothing to report
        # SystemExit is caught EXPLICITLY because it is not an Exception, and
        # a library that calls sys.exit() inside a request thread otherwise
        # kills the thread with the stream still open - the reader waits for
        # ever on a connection nothing will ever write to again. That is not
        # hypothetical: ask.api_key() did exactly this, and the symptom was a
        # permanent "thinking" spinner with a silent server. Fixed at the
        # source too; this is the guard that makes the class of bug loud.
        except (Exception, SystemExit) as e:                  # noqa: BLE001
            q.put(("error", {"error": str(e) or e.__class__.__name__}))
        finally:
            if con is not None:
                con.close()
            q.put(None)

    threading.Thread(target=work, daemon=True).start()
    try:
        yield PAD
        while True:
            try:
                item = q.get(timeout=HEARTBEAT)
            except queue.Empty:
                yield b":\n\n"
                continue
            if item is None:
                return
            yield _event(*item)
    finally:
        # Reached on the normal end AND on the generator being closed under
        # us, which is what a reader navigating away looks like from here.
        gone.set()
        release()


# ------------------------------------------------------------ admin (D1, §9)
# THE PORT IS THE BOUNDARY. Curation binds its own listener that the edge
# proxy never routes, and these routes are registered on THAT app and no
# other - so a request for /api/admin/* arriving on the public port is not
# refused, it is not served: there is nothing there to reach. It 404s like
# any other unknown path, which is the point. The public surface should not
# admit that an admin API exists.
#
# The loopback check below stays as depth. It is free, and it still rules out
# anyone reaching the admin port directly across a network.
def guard(fn):
    """Loopback, then a session. Every admin route but login and state."""
    @functools.wraps(fn)
    def endpoint(request):
        if not admin.loopback(request):
            return _json(request, {"error": "admin answers only on loopback"},
                         403)
        if admin.session_of(request) is None:
            # A restart invalidates sessions, but the browser still holds the
            # dead httpOnly cookie - and JS cannot replace an httpOnly cookie,
            # so a wedged client stays wedged until the SERVER clears it.
            # Every admin 401 does.
            return _json(request, {"error": "not authenticated"}, 401,
                         {"Set-Cookie": admin.clear_cookie_header()})
        return fn(request)
    return endpoint


def _admin_open(fn):
    """Loopback only. For the two reads that answer before sign-in."""
    @functools.wraps(fn)
    def endpoint(request):
        if not admin.loopback(request):
            return _json(request, {"error": "admin answers only on loopback"},
                         403)
        return fn(request)
    return endpoint


@_admin_open
def admin_session(request):
    # No DB work. The reading views ask this on load to decide whether to
    # offer the operator the console bridge - it must cost nothing, and it
    # says nothing but yes or no.
    return _json(request, {"authenticated": admin.session_of(request)
                           is not None})


@_admin_open
@reads
def admin_state(request, con):
    # The one unauthenticated admin read, so the console can show its gate
    # instead of an error. It says nothing but "you are not logged in".
    return _json(request, admin.state(con,
                                      admin.session_of(request) is not None))


@guard
@reads
def admin_queues(request, con):
    return _json(request, admin.queues(con))


@guard
@reads
def admin_rederive_get(request, con):
    return _json(request, admin.rederive_status(con))


@guard
@reads
def admin_ops(request, con):
    return _json(request, admin.ops_status(con))


@guard
@reads
def admin_redactions(request, con):
    one = _one(request)
    return _json(request, admin.redactions(con, one("status", "proposed"),
                                           one("limit", 50), one("offset", 0),
                                           one("video")))


@guard
@reads
def admin_redaction_job(request, con):
    return _json(request, admin.redaction_job_status(con))


@guard
@reads
def admin_review(request, con):
    one = _one(request)
    d = admin.review(con, one("video"), one("name"), one("label"))
    return _json(request, d) if d else _json(request, {}, 404)


async def _body(request):
    """The POST body, parsed. Async because reading it is the one genuinely
    IO-bound thing an endpoint here does."""
    raw = await request.body()
    return json.loads(raw or b"{}")


def admin_post(fn):
    """A curation write: loopback, a session, a WRITE connection, closed.

    `fn(request, body, con)` runs on a worker thread. AdminError is the
    module's way of saying the operator asked for something impossible, which
    is a 400 and not a 500.
    """
    @functools.wraps(fn)
    async def endpoint(request):
        if not admin.loopback(request):
            return _json(request, {"error": "admin answers only on loopback"},
                         403)
        if admin.session_of(request) is None:
            return _json(request, {"error": "not authenticated"}, 401,
                         {"Set-Cookie": admin.clear_cookie_header()})
        try:
            body = await _body(request)
        except ValueError as e:
            return _json(request, {"error": str(e)}, 400)

        def run():
            con = connect(write=True)
            try:
                return fn(request, body, con)
            except admin.AdminError as e:
                return _json(request, {"error": str(e)}, 400)
            except Exception as e:                            # noqa: BLE001
                return _json(request, {"error": str(e)}, 500)
            finally:
                con.close()
        return await anyio.to_thread.run_sync(run)
    return endpoint


async def admin_login(request):
    if not admin.loopback(request):
        return _json(request, {"error": "admin answers only on loopback"}, 403)
    try:
        body = await _body(request)
    except ValueError as e:
        return _json(request, {"error": str(e)}, 400)
    sid = admin.login(body.get("token"))
    if not sid:
        return _json(request, {"error": "that token does not match this "
                                        "server"}, 403)
    return _json(request, {"authenticated": True},
                 headers={"Set-Cookie": admin.cookie_header(sid)})


async def admin_logout(request):
    if not admin.loopback(request):
        return _json(request, {"error": "admin answers only on loopback"}, 403)
    sid = admin.session_of(request)
    if not sid:
        return _json(request, {"error": "not authenticated"}, 401,
                     {"Set-Cookie": admin.clear_cookie_header()})
    admin.logout(sid)
    return _json(request, {"authenticated": False},
                 headers={"Set-Cookie": admin.clear_cookie_header()})


@admin_post
def admin_correct(request, body, con):
    return _json(request, admin.correct(con, body))


@admin_post
def admin_undo(request, body, con):
    return _json(request, admin.undo(con, int(body["id"])))


@admin_post
def admin_proposal(request, body, con):
    return _json(request, admin.decide(con, int(body["id"]),
                                       body.get("decision")))


@admin_post
def admin_redaction(request, body, con):
    return _json(request, admin.redaction_decide(con, body))


@admin_post
def admin_redaction_apply_all(request, body, con):
    return _json(request, admin.redaction_apply_all(con, body))


@admin_post
def admin_label(request, body, con):
    return _json(request, admin.label(con, body))


@admin_post
def admin_ignore(request, body, con):
    return _json(request, admin.ignore(con, body))


@admin_post
def admin_rederive_post(request, body, con):
    act = body.get("action")
    if act == "start":
        return _json(request, admin.rederive_start())
    if act == "revert":
        return _json(request, admin.rederive_revert())
    return _json(request, {"error": "action must be start or revert"}, 400)


@admin_post
def admin_job(request, body, con):
    return _json(request, admin.job_start(con, body.get("name"),
                                          bool(body.get("paid_ok"))))


@admin_post
def admin_job_stop(request, body, con):
    return _json(request, admin.job_stop())


# The /api/speakers/* writes are gone with web/api.py. They wrote names,
# ignores and renames straight onto the speaker tables for the workbench page,
# and /admin does all three now through web/admin.py - which orders its queues
# by impact, shows the evidence beside the write, canonicalises a name to the
# surname and re-indexes the passages per write. None of that was true here.
# Two write paths onto human judgement was one too many.
#
# /api/agenda/* and /api/speakers/* reads are gone with web/api.py and the
# workbench page they fed. The rebuilt UI never called either of them -
# checked before deleting.

EXCEPTION_HANDLERS = {404: _not_found, 405: _not_allowed}


def public_app():
    """The reading API, and the tool surface mounted inside it.

    No admin route is registered here at all. That is the boundary, not a
    check that could be got wrong.
    """
    manager, mcp_asgi = mcp_server.build()

    @contextlib.asynccontextmanager
    async def lifespan(app):
        # The session manager owns the task group every MCP request runs
        # inside. Without this the mount answers by hanging, which is a worse
        # failure than a 500 and a harder one to read.
        async with manager.run():
            yield

    return Starlette(
        routes=[
            Route("/item", redirect_item), Route("/item.html", redirect_item),
            Route("/case", redirect_case), Route("/case.html", redirect_case),
            Route("/", what_this_is), Route("/index.html", what_this_is),
            Route("/search", what_this_is), Route("/speakers", what_this_is),

            Route("/api/ask", api_ask),
            Route("/api/search", api_search),
            Route("/api/video/{video_id}", api_video),

            Route("/api/meetings", api_meetings),
            Route("/api/bodies", api_bodies),
            Route("/api/overview", api_overview),
            Route("/api/highlights", api_highlights),
            Route("/api/issues", api_issues),

            Route("/api/tools", api_tools),
            Route("/api/facts", api_facts),
            Route("/api/tool/{name}", api_tool),
            Route("/api/find", api_find),
            Route("/api/facets", api_facets),
            Route("/api/meeting/{meeting_id:int}", api_meeting),
            Route("/api/transcript/{video_id}", api_transcript),
            Route("/api/answer/{answer_id}", api_answer),
            Route("/api/stats", api_stats),
            Route("/api/item/{item_id:int}", api_item),
            Route("/api/case/{case_id}", api_case),
            Route("/api/file/{file_id:int}", api_file),

            # A Route holding a raw ASGI app, NOT a Mount. A Mount treats
            # "/mcp" as a prefix and 307s it to "/mcp/" before the app sees
            # anything - and an MCP client POSTs to the exact path it was
            # given, so the handshake became a redirect that curl did not
            # follow and a client should not have to. No `methods`: the
            # transport answers POST, GET and DELETE itself and 405s the
            # rest, which is the spec's business and not this table's.
            Route("/mcp", mcp_asgi),
        ],
        exception_handlers=EXCEPTION_HANDLERS,
        lifespan=lifespan,
    )


def admin_app():
    """Curation, on its own loopback listener. Nothing else is here."""
    return Starlette(
        routes=[
            Route("/api/admin/session", admin_session),
            Route("/api/admin/state", admin_state),
            Route("/api/admin/queues", admin_queues),
            Route("/api/admin/rederive", admin_rederive_get),
            Route("/api/admin/rederive", admin_rederive_post,
                  methods=["POST"]),
            Route("/api/admin/ops", admin_ops),
            Route("/api/admin/redactions", admin_redactions),
            Route("/api/admin/redaction/job", admin_redaction_job),
            Route("/api/admin/review", admin_review),

            Route("/api/admin/login", admin_login, methods=["POST"]),
            Route("/api/admin/logout", admin_logout, methods=["POST"]),
            Route("/api/admin/correct", admin_correct, methods=["POST"]),
            Route("/api/admin/undo", admin_undo, methods=["POST"]),
            Route("/api/admin/proposal", admin_proposal, methods=["POST"]),
            Route("/api/admin/redaction", admin_redaction, methods=["POST"]),
            Route("/api/admin/redaction/apply-all", admin_redaction_apply_all,
                  methods=["POST"]),
            Route("/api/admin/label", admin_label, methods=["POST"]),
            Route("/api/admin/ignore", admin_ignore, methods=["POST"]),
            Route("/api/admin/job", admin_job, methods=["POST"]),
            Route("/api/admin/job/stop", admin_job_stop, methods=["POST"]),
        ],
        exception_handlers=EXCEPTION_HANDLERS,
    )


def _server(app, host, port):
    """One uvicorn, quiet, with the socket already bound.

    `access_log=False` keeps the console what it was: the old handler
    overrode `log_message` to print nothing, and the startup lines plus
    whatever the agent says are the whole of what belongs on stdout here.
    """
    config = uvicorn.Config(app, host=host, port=port, log_level="warning",
                            access_log=False, lifespan="on")
    return uvicorn.Server(config), config.bind_socket()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    # A SEPARATE PORT FOR CURATION, and it is the whole security
    # model. `loopback()` used to prove "this request is local" from
    # the TCP peer plus the absence of a forwarding header. The peer
    # is useless behind a proxy - every request looks like 127.0.0.1
    # - and Next 16 now sets x-forwarded-for on EVERY request
    # (base-server.js: `req.headers['x-forwarded-for'] ??=
    # socket.remoteAddress`), so the second half stopped
    # distinguishing anything and locked the console out of its own
    # front end instead.
    #
    # A port the edge never routes cannot be reached from outside at
    # all, whatever headers anyone sends. ui/next.config.ts only adds
    # the /api/admin rewrite when ADMIN_API is set, which the
    # production image does not set - so out there the route does
    # not exist rather than being refused.
    ap.add_argument("--admin-port", type=int,
                    default=int(os.environ.get("ADMIN_PORT", 8766)))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-dense", action="store_true",
                    help="skip loading the embedding model; search falls back "
                         "to keywords and says so")
    args = ap.parse_args()
    try:
        with db.connect(autocommit=True) as c:
            n = c.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
        print(f"catalog: {n} videos")
    except Exception as e:                                    # noqa: BLE001
        print(f"cannot reach the database: {e}\n"
              f"  source ./env.local.sh first", file=sys.stderr)
        return 1
    # Load the embedding model before the port opens, not on the first search:
    # it costs ~6s and the reader who happens to be first should not pay it.
    if not args.no_dense:
        tools.warm()
    else:
        print("[tools] dense retrieval DISABLED by --no-dense; search is "
              "BM25 only", file=sys.stderr)
    # Bind the ports BEFORE writing the token. A second launch against a busy
    # port used to write its token first and then die on the bind, leaving a
    # file whose token no running process holds - and a sign-in that can only
    # say "does not match" while the operator holds the freshest file.
    # `bind_socket` is what keeps that order available under uvicorn, which
    # otherwise binds inside `serve()` long after this point.
    try:
        public, public_sock = _server(public_app(), args.host, args.port)
    except OSError as e:
        print(f"cannot bind {args.host}:{args.port}: {e}", file=sys.stderr)
        return 1
    # Loopback ALWAYS, whatever --host says for the public one. There is no
    # deployment in which curation should answer on a network interface.
    try:
        curation, curation_sock = _server(admin_app(), "127.0.0.1",
                                          args.admin_port)
    except OSError as e:
        public_sock.close()
        print(f"cannot bind 127.0.0.1:{args.admin_port}: {e}", file=sys.stderr)
        return 1
    # D1: fresh admin token per process start. NEVER printed and never logged -
    # only the path is announced; the operator reads the file themselves.
    token_path = admin.init(ROOT)
    print(f"research  → http://{args.host}:{args.port}/")
    print(f"tools     → http://{args.host}:{args.port}/mcp (MCP, "
          f"{limits.MCP_PER_IP}/address per {limits.MCP_WINDOW}s)")
    print(f"admin     → 127.0.0.1:{args.admin_port} (loopback only, its own "
          f"listener) — paste the token from")
    print(f"            {token_path} (mode 600, regenerated each start)")

    async def both():
        # One event loop, two listeners. If either stops the process stops:
        # deploy/entrypoint.sh watches this pid and takes the container down
        # with it, and a half-dead API serving only curation is exactly the
        # state that script exists to make visible.
        await asyncio.gather(public.serve(sockets=[public_sock]),
                             curation.serve(sockets=[curation_sock]))

    try:
        asyncio.run(both())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
