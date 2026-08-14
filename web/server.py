"""Two front-ends over one archive, each specialised for its job.

    /          research  - search, read transcripts, jump to the video
    /speakers  workbench - inspect voice groups, name/split/merge them

They are separate pages because they are separate tasks: reading wants a big
player and continuous transcript, labelling wants dense lists, multi-select and
keyboard flow. Bolting the second onto the first produced a tool that could
neither list existing speakers nor split a mixed group.

Standard library only, so it runs from any venv:

    python3 web/server.py [--port 8765]
"""
import argparse
import datetime
import decimal
import gzip
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))   # retrieve/ask live with the pipeline
HERE = os.path.dirname(os.path.abspath(__file__))

import psycopg                                    # noqa: E402

import admin                                     # noqa: E402
import archive                                   # noqa: E402
import db                                        # noqa: E402
import limits                                    # noqa: E402
import tools                                     # noqa: E402

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


def jsonable(o):
    """Postgres returns real types where SQLite returned strings.

    timestamptz arrives as datetime and numeric as Decimal, neither of which
    json.dumps will touch. Handled here rather than at each call site, so a
    column added later cannot silently 500 an endpoint.
    """
    if isinstance(o, (datetime.datetime, datetime.date)):
        return o.isoformat()
    if isinstance(o, decimal.Decimal):
        return float(o)
    raise TypeError(f"{type(o).__name__} is not JSON serializable")


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


class Handler(BaseHTTPRequestHandler):
    # BaseHTTPRequestHandler defaults to HTTP/1.0, and under 1.0 a response
    # with no Content-Length is terminated by closing the connection - so a
    # browser buffers the whole thing and delivers nothing until the end. That
    # is fatal for /api/ask, whose entire point is that progress arrives while
    # the agent works: curl -N saw every event immediately and EventSource saw
    # none for ninety seconds, then "the connection dropped".
    # Safe to raise globally because every other response here sets a
    # Content-Length; the streaming one sends `Connection: close` instead.
    protocol_version = "HTTP/1.1"
    def _send(self, code, body, ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, default=jsonable).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if (len(body) > GZIP_OVER
                and "gzip" in self.headers.get("Accept-Encoding", "")):
            body = gzip.compress(body, 6)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # R5.3.5 asks for the county's own document, inline. It cannot simply be
    # framed from source: CivicClerk serves every file with
    #
    #     content-disposition: attachment; filename=851.pdf
    #
    # which makes a browser download it rather than render it, so a
    # cross-origin <iframe> shows nothing at all. This re-serves the identical
    # bytes with `inline`, changing the disposition and nothing else - the
    # document is still the county's, unaltered, and the direct link is offered
    # alongside so a reader can go to the source themselves.
    def _file(self, file_id, con):
        row = con.execute(
            "SELECT file_id, kind, name FROM portal_files WHERE file_id = %s",
            (file_id,)).fetchone()
        if not row:
            return self._send(404, {"error": "no such file"})
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
            return self._send(502, {"error": f"the county's portal did not "
                                             f"serve this file: {e}"})
        name = f"{(row['kind'] or 'document').lower()}-{file_id}.pdf"
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", f'inline; filename="{name}"')
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(blob)

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        one = lambda k, d=None: qs.get(k, [d])[0]
        try:
            # This server serves no HTML any more. The five hand-written pages
            # it used to answer with - search, speakers, ask, item, case - are
            # deleted along with web/api.py behind them; the rebuilt UI owns
            # every reading surface and /admin owns curation. What is left here
            # is the JSON API that UI reads, and nothing else.
            #
            # Old bookmarks to ?id= URLs are still redirected: they cost two
            # lines and they were real links.
            if u.path in ("/item", "/item.html"):
                i = one("id")
                return self._redirect(f"/item/{int(i)}" if i else "/")
            if u.path in ("/case", "/case.html"):
                c = one("id")
                return self._redirect(f"/case/{quote(c, safe='')}" if c else "/")
            # NOT a redirect to "/" - this server has no "/" to send them to,
            # and pointing it at itself is an infinite loop. It answers plainly
            # that it is the API.
            if u.path in ("/", "/index.html", "/search", "/speakers"):
                return self._send(404, {"error": "this is the archive's JSON "
                                        "API; the site is served by the UI"})
            if u.path == "/api/ask":
                return self._ask_guarded(one("q", ""))

            con = connect()
            # ------------------------------------------------- admin (D1, §9)
            # Loopback only: this server has no TLS, and a bearer session over
            # plain HTTP on a network is a giveaway, so the interface admin
            # will answer on is the one that never leaves the machine.
            if u.path.startswith("/api/admin/"):
                if not admin.loopback(self):
                    return self._send(403, {"error":
                                            "admin answers only on loopback"})
                authed = admin.session_of(self) is not None
                if u.path == "/api/admin/session":
                    # No DB work. The reading views ask this on load to decide
                    # whether to offer the operator the console bridge — it
                    # must cost nothing, and it says nothing but yes or no.
                    return self._send(200, {"authenticated": authed})
                if u.path == "/api/admin/state":
                    # The one unauthenticated admin read, so the console can
                    # show its gate instead of an error. It says nothing but
                    # "you are not logged in".
                    return self._send(200, admin.state(con, authed))
                if not authed:
                    # A restart invalidates sessions, but the browser still
                    # holds the dead httpOnly cookie - and JS cannot replace
                    # an httpOnly cookie, so a wedged client stays wedged
                    # until the SERVER clears it. Every admin 401 does.
                    return self._send(401, {"error": "not authenticated"},
                                      headers={"Set-Cookie":
                                               admin.clear_cookie_header()})
                if u.path == "/api/admin/queues":
                    return self._send(200, admin.queues(con))
                if u.path == "/api/admin/rederive":
                    return self._send(200, admin.rederive_status(con))
                if u.path == "/api/admin/ops":
                    return self._send(200, admin.ops_status(con))
                if u.path == "/api/admin/redactions":
                    return self._send(200, admin.redactions(
                        con, one("status", "proposed"), one("limit", 50),
                        one("offset", 0), one("video")))
                if u.path == "/api/admin/redaction/job":
                    return self._send(200, admin.redaction_job_status(con))
                if u.path == "/api/admin/review":
                    d = admin.review(con, one("video"), one("name"),
                                     one("label"))
                    return self._send(200, d) if d else self._send(404, {})
                return self._send(404, {"error": "not found"})
            if u.path == "/api/search":
                return self._send(200, search(
                    con, one("q", ""), one("kind"), one("speaker"),
                    min(int(one("limit", 50)), 200), int(one("offset", 0))))
            # Keyed on a VIDEO id. It was called /api/meeting/<id> while
            # /api/agenda/<id> took a MEETING id - two keys, near-identical
            # names, and a trap that should not survive the rebuild (D7).
            if u.path.startswith("/api/video/"):
                d = transcript(con, u.path.rsplit("/", 1)[-1])
                return self._send(200, d) if d else self._send(404, {})

            # ------------------------------------------------ rebuilt UI
            if u.path == "/api/meetings":
                hr = one("recording")
                return self._send(200, archive.meetings(
                    con, one("body"), one("year"),
                    None if hr is None else hr == "1", one("when", "past"),
                    min(int(one("limit", 200)), 500), int(one("offset", 0)),
                    one("month")))
            if u.path == "/api/bodies":
                return self._send(200, archive.bodies(con))
            if u.path == "/api/overview":
                return self._send(200, archive.overview(con, one("body")))
            if u.path == "/api/highlights":
                return self._send(200, archive.highlights(
                    con, min(int(one("limit", 6)), 30)))
            if u.path == "/api/issues":
                return self._send(200, ISSUES_CACHE.get(con))

            # --------------------------------------------- retrieval (D9)
            # The tool surface, and the two ways in. /api/tools is the
            # manifest a model gets handed; /api/tool/<name> invokes one.
            # /api/find is the page's call, and it is nothing but two of
            # these tools - the page and the agent share one surface on
            # purpose, so what a reader can find by hand the agent can too.
            if u.path == "/api/tools":
                return self._send(200, {"tools": tools.MANIFEST,
                                        "dense": tools._dense_error})
            if u.path.startswith("/api/tool/"):
                name = unquote(u.path.rsplit("/", 1)[-1])
                args = {k: v[0] for k, v in qs.items()}
                try:
                    return self._send(200, tools.call(con, name, args))
                except tools.ToolError as e:
                    return self._send(400, {"error": str(e)})
            if u.path == "/api/find":
                facets = {k: one(k) for k in
                          ("body", "outcome", "phase", "case", "speaker",
                           "since", "until") if one(k)}
                if one("decided"):
                    facets["decided"] = one("decided") == "1"
                try:
                    return self._send(200, tools.search(
                        con, one("q", ""),
                        min(int(one("limit", 25)), 100),
                        int(one("offset", 0)), **facets))
                except tools.ToolError as e:
                    return self._send(400, {"error": str(e)})
            if u.path == "/api/facets":
                return self._send(200, FACETS_CACHE.get(con))
            if u.path.startswith("/api/meeting/"):
                d = archive.meeting(con, int(u.path.rsplit("/", 1)[-1]))
                return self._send(200, d) if d else self._send(404, {})
            if u.path.startswith("/api/transcript/"):
                d = archive.transcript(con, u.path.rsplit("/", 1)[-1])
                return self._send(200, d) if d else self._send(404, {})
            if u.path == "/api/stats":
                return self._send(200, stats(con))
            if u.path.startswith("/api/item/"):
                d = archive.item(con, int(u.path.rsplit("/", 1)[-1]))
                return self._send(200, d) if d else self._send(404, {})
            if u.path.startswith("/api/case/"):
                d = archive.case(con, unquote(u.path.rsplit("/", 1)[-1]))
                return self._send(200, d) if d else self._send(404, {})
            if u.path.startswith("/api/file/"):
                return self._file(int(u.path.rsplit("/", 1)[-1]), con)
            # /api/agenda/* and /api/speakers/* are gone with web/api.py and
            # the workbench page they fed. /admin replaced the workbench (slice
            # 6) with its own data layer in web/admin.py, and the rebuilt UI
            # never called either of them - checked before deleting.
            return self._send(404, {"error": "not found"})
        except psycopg.errors.DataError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def _ask_guarded(self, question):
        """Bound what a public, unauthenticated, PAID endpoint can be made to
        spend, before it spends any of it.

        Two ways to say no, because the two callers cannot both be served by
        one. A browser reaches this through EventSource, which exposes neither
        the status code nor the body of a failed response to the page - all it
        gets is a bare `error`, so a 429 would show a reader "something went
        wrong" and never the sentence telling them to come back in four
        minutes. Anything else (curl, a script, a monitor, a WAF counting
        429s) wants the real status. EventSource always sends
        `Accept: text/event-stream`, which is a clean way to tell them apart.

        Either way no model is called and nothing is paid for.
        """
        try:
            release = limits.reserve(limits.client_ip(self), question)
        except limits.Throttled as t:
            wants_sse = "text/event-stream" in self.headers.get("Accept", "")
            if not wants_sse:
                headers = ({"Retry-After": str(t.retry_after)}
                           if t.retry_after else None)
                code = 400 if t.kind in ("empty", "length") else 429
                return self._send(code, {"error": t.message}, headers=headers)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            try:
                self.wfile.write(
                    b":" + b" " * 2048 + b"\n\nevent: error\ndata: "
                    + json.dumps({"error": t.message,
                                  "retry_after": t.retry_after}).encode()
                    + b"\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        try:
            return self._ask_stream(question)
        finally:
            release()

    def _ask_stream(self, question):
        """Server-sent events: the agent takes minutes on a hard question, so
        what it is DOING is streamed rather than leaving the page on a bare
        spinner (R5.5.1).

        Reached only through `_ask_guarded`, which has already claimed a slot
        from web/limits.py. Refusals must happen BEFORE this method: once the
        200 and the event-stream headers are out, the only way left to say no
        is inside the stream, where a proxy cannot see it and a status code
        cannot be set.

        What streams is the agent's actual tool calls, not four fixed captions.
        The stages are whatever it decides to do - that is the point of D9 -
        and a reader watching "search_record: school zone speed cameras -> 0
        items" learns something a progress bar cannot tell them."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        # `no-transform` is the load-bearing half. Next's dev proxy sits in
        # front of this and gzips anything whose client sent Accept-Encoding,
        # which every browser does - and a gzip stream buffers until it has
        # enough input to emit a block. Measured: `curl -N` saw every event
        # instantly, `curl -N -H "Accept-Encoding: gzip"` through the same
        # proxy saw nothing, and the page sat blank for ninety seconds and
        # then reported the connection dropped. no-transform is the standard
        # way to tell an intermediary to leave a body alone; X-Accel-Buffering
        # is nginx's version of the same instruction, for production.
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        # 2 KB of comment before anything real. Something between here and the
        # EventSource holds a small response back until its buffer fills:
        # `curl -N` saw every event the instant it was written and the browser
        # saw nothing for ninety seconds, then reported the connection
        # dropped. Padding past the threshold is the standard remedy and the
        # only one that does not depend on knowing which layer is at fault. A
        # line beginning with ':' is a comment in the SSE grammar, so this is
        # invisible to the client.

        # One lock over every write to this socket. The heartbeat below runs on
        # its own thread, and two writes interleaving would splice a comment
        # into the middle of an event and corrupt the framing of both.
        wlock = threading.Lock()

        def write(chunk):
            with wlock:
                self.wfile.write(chunk)
                self.wfile.flush()

        try:
            write(b":" + b" " * 2048 + b"\n\n")
        except (BrokenPipeError, ConnectionResetError):
            return

        def send(event, payload):
            try:
                write(f"event: {event}\n"
                      f"data: {json.dumps(payload, default=jsonable)}\n\n".encode())
            except (BrokenPipeError, ConnectionResetError):
                raise                    # client navigated away; stop working

        if not question.strip():
            return send("error", {"error": "empty question"})
        con = None

        # HEARTBEAT, above, for why. It has to be a thread: the run is a
        # sequence of blocking calls into the model, and the whole problem is
        # the time spent inside one of them with nothing to report.
        done = threading.Event()

        def beat():
            while not done.wait(HEARTBEAT):
                try:
                    write(b":\n\n")
                except Exception:                                 # noqa: BLE001
                    return    # gone. The run's own next write says so properly.

        pulse = threading.Thread(target=beat, daemon=True)
        pulse.start()
        try:
            import agent
            import ask as llm
            # Check the key BEFORE the first stage event. Otherwise the reader
            # is told the agent is thinking and only then that it never could.
            llm.api_key()
            con = connect()
            result = agent.ask(question, con,
                               on_event=lambda s, d: send("stage",
                                                          {"stage": s, **d}))
            send("answer", result)
        except (BrokenPipeError, ConnectionResetError):
            pass
        # SystemExit is caught EXPLICITLY because it is not an Exception, and
        # a library that calls sys.exit() inside a request thread otherwise
        # kills the thread with the stream still open - the reader waits for
        # ever on a connection nothing will ever write to again. That is not
        # hypothetical: ask.api_key() did exactly this, and the symptom was a
        # permanent "thinking" spinner with a silent server. Fixed at the
        # source too; this is the guard that makes the class of bug loud.
        except (Exception, SystemExit) as e:
            try:
                send("error", {"error": str(e) or e.__class__.__name__})
            except Exception:
                pass
        finally:
            # Before the socket goes, so nothing is still writing to it. The
            # join is bounded because a wedged write must not hold the request
            # thread for ever; if it does time out, `beat` is a daemon whose
            # next write raises and ends it.
            done.set()
            pulse.join(timeout=5)
            if con is not None:
                con.close()

    def do_POST(self):
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or "{}")

            # ------------------------------------------------- admin (D1, §9)
            if u.path.startswith("/api/admin/"):
                if not admin.loopback(self):
                    return self._send(403, {"error":
                                            "admin answers only on loopback"})
                if u.path == "/api/admin/login":
                    sid = admin.login(body.get("token"))
                    if not sid:
                        return self._send(403, {"error": "that token does not "
                                                         "match this server"})
                    return self._send(
                        200, {"authenticated": True},
                        headers={"Set-Cookie": admin.cookie_header(sid)})
                sid = admin.session_of(self)
                if not sid:
                    # Same reason as the GET side: only the server can clear
                    # a dead httpOnly cookie.
                    return self._send(401, {"error": "not authenticated"},
                                      headers={"Set-Cookie":
                                               admin.clear_cookie_header()})
                if u.path == "/api/admin/logout":
                    admin.logout(sid)
                    return self._send(
                        200, {"authenticated": False},
                        headers={"Set-Cookie": admin.clear_cookie_header()})
                con = connect(write=True)
                try:
                    try:
                        if u.path == "/api/admin/correct":
                            return self._send(200, admin.correct(con, body))
                        if u.path == "/api/admin/undo":
                            return self._send(200, admin.undo(
                                con, int(body["id"])))
                        if u.path == "/api/admin/proposal":
                            return self._send(200, admin.decide(
                                con, int(body["id"]), body.get("decision")))
                        if u.path == "/api/admin/redaction":
                            return self._send(200, admin.redaction_decide(
                                con, body))
                        if u.path == "/api/admin/redaction/apply-all":
                            return self._send(200, admin.redaction_apply_all(
                                con, body))
                        if u.path == "/api/admin/label":
                            return self._send(200, admin.label(con, body))
                        if u.path == "/api/admin/ignore":
                            return self._send(200, admin.ignore(con, body))
                        if u.path == "/api/admin/rederive":
                            act = body.get("action")
                            if act == "start":
                                return self._send(200, admin.rederive_start())
                            if act == "revert":
                                return self._send(200, admin.rederive_revert())
                            return self._send(400, {"error":
                                                    "action must be start or revert"})
                        if u.path == "/api/admin/job":
                            return self._send(200, admin.job_start(
                                con, body.get("name"),
                                bool(body.get("paid_ok"))))
                        if u.path == "/api/admin/job/stop":
                            return self._send(200, admin.job_stop())
                        return self._send(404, {"error": "not found"})
                    except admin.AdminError as e:
                        return self._send(400, {"error": str(e)})
                finally:
                    con.close()

            # The /api/speakers/* writes are gone with web/api.py. They wrote
            # names, ignores and renames straight onto the speaker tables for
            # the workbench page, and /admin does all three now through
            # web/admin.py - which orders its queues by impact, shows the
            # evidence beside the write, canonicalises a name to the surname
            # and re-indexes the passages per write. None of that was true
            # here. Two write paths onto human judgement was one too many.
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-dense", action="store_true",
                    help="skip loading the embedding model; search falls back "
                         "to keywords and says so")
    args = ap.parse_args()
    try:
        with db.connect(autocommit=True) as c:
            n = c.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
        print(f"catalog: {n} videos")
    except Exception as e:
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
    # Bind the port BEFORE writing the token. A second launch against a busy
    # port used to write its token first and then die on the bind, leaving a
    # file whose token no running process holds - and a sign-in that can only
    # say "does not match" while the operator holds the freshest file.
    try:
        httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as e:
        print(f"cannot bind {args.host}:{args.port}: {e}", file=sys.stderr)
        return 1
    # D1: fresh admin token per process start. NEVER printed and never logged -
    # only the path is announced; the operator reads the file themselves.
    token_path = admin.init(ROOT)
    print(f"research  → http://{args.host}:{args.port}/")
    print(f"workbench → http://{args.host}:{args.port}/speakers")
    print(f"admin     → http://localhost:3000/admin — paste the token from "
          f"{token_path} (mode 600, regenerated each start)")
    httpd.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
