"""The archive's tool surface, served over MCP at /mcp.

PUBLIC AND METERED. The data is a public record and the tools only read it, so
there is no authentication. The budget is MCP_* in web/limits.py, deliberately
not Ask's: a tool call spends CPU rather than tokens, and the two must not be
able to close each other.
"""
import json
import os
import sys

import anyio

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))

import mcp_types as mt
from mcp.server.lowlevel.server import Server
from mcp.shared.exceptions import MCPError
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

import db                                            # noqa: E402
import limits                                        # noqa: E402
import tools                                         # noqa: E402
from wire import jsonable                            # noqa: E402

NAME = "pasco-meeting-archive"
VERSION = "1.0"

# Handed to every client at initialize, so it is in front of the model whether
# or not anyone asked for a prompt. Deliberately SHORT: this is the part that
# cannot be skipped, so it holds only the mistakes that make an answer wrong
# rather than thin, and the long form lives in the prompts.
INSTRUCTIONS = """This archive holds Pasco County, Florida government meetings.

TWO SOURCES, NOT INTERCHANGEABLE. The RECORD (agendas the county published,
outcomes its approved minutes recorded) is authoritative for what was DECIDED,
and covers {first_year} to {last_year} whether or not anyone filmed it. The
TRANSCRIPT (machine transcription of {hours} hours of recordings,
{first_rec_year} onward) is authoritative for what was SAID, and exists for only
{pct_transcript}% of decided items. An
outcome comes from the record. An argument comes from the transcript.

THREE THINGS THAT MAKE AN ANSWER WRONG RATHER THAN THIN:

1. Every row carrying a speaker says how sure that name is, in `name_state`.
   A `weak` or `several` row also carries `name_note` spelling out why: the
   QUOTE is sound and the NAME is not, because it is the name that voice goes
   by across the archive rather than evidence about this meeting. Say "a
   commissioner" or "a resident" and never put the name on it. `confirmed`
   was checked by a person and may be stated plainly. `inferred` carries no
   note and is most of the archive.
2. Never report how a NAMED person voted from a transcript. Speaker labels
   come from automated voice matching and roll calls land on the wrong name.
   A COUNT the transcript states ("four nays, three ayes") is reportable.
3. Never say a word or a subject does not appear in the archive. A search that
   came back empty is evidence about that search, not about the archive: it
   holds hundreds of thousands of passages and you have seen a few hundred.

A LONG RESULT ARRIVES IN WINDOWS. get_item, get_case, get_meeting and
get_document all hand back `next_offset` when they could not fit everything:
pass it back as `offset` and keep going until it comes back null. Make the
FIRST call with no offset, because that is the one carrying the record - the
outcome, the case thread, the census, the roster - and a continuation carries
only the part you asked to continue. A NEGATIVE offset counts from the end,
which on an item is where a motion and its vote sit, and reaching for it beats
paging to it. Only the total is named for what it counts: `turns` for speech,
`agenda_items` for an agenda, `text_chars` for a document.

SAY WHERE EACH THING CAME FROM. Every result carries an id - a transcript
passage is [N] and a published item is [item:N], and they are different
namespaces, so [22216] and [item:22216] are not the same object. Put the id of
every result a sentence rests on next to that sentence. Never cite an id no
tool in this conversation returned, and cite nothing at all for a claim about
what is NOT there: say what you searched for and what came back empty, and let
the sentence stand on the searching.

Most results also carry a `url`, which opens the archive at that exact moment
or item. Where your client renders links, make the citation the link; a reader
who cannot check a quote has to take your word for it, and the point of this
archive is that nobody has to.

A TRANSCRIPT CITATION HAS TWO EDGES, and the archive knows both: every hit
carries `start` and `end` in seconds, and its `url` carries them as `t` and
`end`, so following one plays the cited stretch and STOPS there instead of
running on into the rest of a six-hour meeting. When you name a time in your
own prose, name the range - "from 1:57:52 to 2:00:14" - not the start alone.
Cite the passages the claim actually rests on and no more: consecutive
passages are consecutive seconds of recording, so a citation you padded is a
reader you made sit through the padding.

Where the tools do not settle a question, say so. "The archive does not show
this" is a complete answer here."""

# ------------------------------------------------------------------ prompts
# Written from the same rules web/agent.py gives its own composer, because the
# job is the same job. SOURCES is imported from there rather than restated,
# so the two cannot drift; the rest is the composer's ruleset reduced to what
# survives being handed to a model this project does not control.

_CITE = """CITING WHAT YOU USED. Every tool result carries ids. A transcript
passage is [N] and a published item is [item:N], and the two are different
namespaces: [22216] and [item:22216] are not the same object. Put the id of
every result a sentence rests on next to that sentence. If a sentence needs a
name from one passage and a figure from another, it carries both.

Never cite an id you did not receive from a tool in this conversation. Do not
cite anything for a claim about what is NOT there; say what you searched for
and what came back empty, and let that sentence stand on the searching.

A passage is a STRETCH of recording, not a moment in one: it carries `start`
and `end`, and its `url` carries both, so a reader who follows it hears the
quoted exchange and no more. Where you write a time, write the range. Where
you cite a run of passages, cite the ones the sentence rests on - the last
one's `end` is where the reader is let go."""

_PLAIN = """HOW TO WRITE IT. Somebody who lives in the county, was not at the
meeting, and does not follow local government. Short sentences, ordinary
words, no procedural jargon left unexplained. Say what a decision MEANS for a
person, not only what it was called. Lead with the answer. No preamble.

Plain is not vague: never round a vote, soften an outcome, or drop a
qualification the record makes.

No em dashes. Use a full stop, a comma pair, a colon, or brackets instead."""


def _sources():
    """The two-source rule, in the composer's own words."""
    import agent
    return agent.SOURCES


PROMPTS = [
    {
        "name": "answer_from_archive",
        "description":
            "Answer a question about Pasco County government from this "
            "archive alone, with the record and the transcript kept apart "
            "and every claim carrying the id that supports it.",
        "arguments": [("question", "The question to answer.", True)],
        "render": lambda a: f"""{_sources()}

{_CITE}

{_PLAIN}

WHAT YOU MAY USE. This archive and nothing else. Not what you know about
Pasco County, not what was in the news, not what usually happens at a county
commission. An answer that reaches past these tools makes the archive vouch
for something it never saw, and the reader cannot tell which sentence that
was. If the archive is silent, the finding is the silence.

Search before you conclude. search_record for what was decided,
search_transcript for what was argued, then get_item, get_case or get_meeting
to read the thing itself rather than the summary of it.

THE QUESTION: {a['question']}""",
    },
    {
        "name": "what_happened_with",
        "description":
            "Trace one case, application or agenda item: what the county "
            "decided, when, and what was argued about it beforehand.",
        "arguments": [("subject",
                       "A case number, an agenda code, an address, a "
                       "project name, or a plain description.", True)],
        "render": lambda a: f"""{_sources()}

{_CITE}

{_PLAIN}

THE ORDER MATTERS HERE. Establish the DISPOSITION first, from the record:
search_record, then get_item or get_case for the item itself. Lead your answer
with what the county decided and the date it decided it.

Only then use search_transcript for what was argued and by whom. Never
contradict a recorded outcome with an inference from the transcript; if
they disagree, say so and give both. If the record shows no outcome, say the
published record shows no outcome. Do NOT infer one from a vote being called,
and do not infer one from the discussion sounding settled.

THE SUBJECT: {a['subject']}""",
    },
    {
        "name": "what_was_said_about",
        "description":
            "Gather what people actually said about a subject in recorded "
            "meetings, with the limits of the recordings stated.",
        "arguments": [("topic", "The subject to search for.", True),
                      ("years", "Optional range, e.g. '2020 to 2024'.",
                       False)],
        "render": lambda a: f"""{_sources()}

{_CITE}

{_PLAIN}

THIS IS A TRANSCRIPT QUESTION, so state the limit in the same breath as the
finding. Recordings start in {first_rec_year} and cover {pct_transcript}% of
decided items, so what you
gather is what was said AT THE MEETINGS THAT WERE FILMED, and that is how to
describe it.

Search more than once and more than one way before you summarise: the words a
resident uses and the words an ordinance uses are rarely the same words. Where
one person made the same point at four meetings, say so once and cite it once.

Attribute carefully. A passage marked NAME NOT CONFIRMED gets "a resident" or
"a commissioner", never a name.

THE TOPIC: {a['topic']}{chr(10) + 'YEARS: ' + a['years'] if a.get('years') else ''}""",
    },
]

BY_NAME = {p["name"]: p for p in PROMPTS}


# -------------------------------------------------------------- the handlers
async def _list_tools(ctx, params):
    """The five, straight off the manifest the agent is handed."""
    specs = await anyio.to_thread.run_sync(_manifest)
    return mt.ListToolsResult(tools=[
        mt.Tool(name=s["name"], description=s["description"],
                input_schema=s["parameters"],
                # Read-only and safe to retry, which is worth telling a client
                # that decides for itself whether to ask a human first.
                annotations=mt.ToolAnnotations(readOnlyHint=True,
                                               idempotentHint=True,
                                               openWorldHint=False))
        for s in specs])


def _manifest():
    """The tool specs, with their counts measured. Off the event loop."""
    con = db.connect(autocommit=True)
    try:
        return tools.manifest(con)
    finally:
        con.close()


def _run(name, args):
    """One tool call, on a thread, with its own connection."""
    con = db.connect(autocommit=True)
    try:
        return tools.call(con, name, args)
    finally:
        con.close()


async def _call_tool(ctx, params):
    """Meter it, run it off the event loop, hand back the JSON."""
    name = params.name
    args = dict(params.arguments or {})
    if name not in tools.BY_NAME:
        return mt.CallToolResult(
            content=[mt.TextContent(text=f"no such tool: {name}")],
            is_error=True)

    # `ctx.request` is the Starlette request the transport framed this message
    # with, which is the only place the caller's address exists by the time a
    # handler runs.
    ip = limits.client_ip(ctx.request) if ctx.request is not None else "unknown"
    try:
        release = limits.mcp_reserve(ip, name)
    except limits.Throttled as t:
        # An error RESULT, not a protocol error: the call was well formed and
        # the refusal is about this caller's budget, which is something the
        # model should read and act on rather than something its client should
        # treat as a broken server.
        return mt.CallToolResult(
            content=[mt.TextContent(text=t.message)], is_error=True)
    try:
        # Every tool is blocking: an indexed query, and for search_transcript
        # a pass of the query encoder. On the event loop it would stall every
        # other request in the process, including the reading API.
        out = await anyio.to_thread.run_sync(_run, name, args)
    except tools.ToolError as e:
        return mt.CallToolResult(
            content=[mt.TextContent(text=str(e))], is_error=True)
    finally:
        release()
    return mt.CallToolResult(content=[mt.TextContent(
        text=json.dumps(out, default=jsonable, ensure_ascii=False))])


async def _list_prompts(ctx, params):
    return mt.ListPromptsResult(prompts=[
        mt.Prompt(name=p["name"], description=p["description"],
                  arguments=[mt.PromptArgument(name=n, description=d,
                                               required=r)
                             for n, d, r in p["arguments"]])
        for p in PROMPTS])


async def _get_prompt(ctx, params):
    """A bad prompt request is a bad REQUEST, not a server fault."""
    p = BY_NAME.get(params.name)
    if not p:
        raise MCPError(mt.INVALID_PARAMS, f"no such prompt: {params.name}")
    args = dict(params.arguments or {})
    for n, _, required in p["arguments"]:
        if required and not (args.get(n) or "").strip():
            raise MCPError(mt.INVALID_PARAMS,
                           f"{params.name}: {n} is required")
    return mt.GetPromptResult(
        description=p["description"],
        messages=[mt.PromptMessage(
            role="user", content=mt.TextContent(text=p["render"](args)))])


def build():
    """The MCP server and the ASGI app that speaks to it."""
    # Measured at startup. The MCP handshake takes `instructions` as a plain
    # string on the Server object, so unlike the tool list this one cannot be
    # re-read per request - it is as fresh as the process. That is a real
    # bound and a much smaller one than the source file: a redeploy moves it,
    # where a typed number moved only when somebody remembered.
    con = db.connect(autocommit=True)
    try:
        instructions = tools.reflow(INSTRUCTIONS.format(**tools.facts(con)))
    finally:
        con.close()

    server = Server(
        NAME,
        version=VERSION,
        title="Pasco Watch",
        instructions=instructions,
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
        on_list_prompts=_list_prompts,
        on_get_prompt=_get_prompt,
    )
    manager = StreamableHTTPSessionManager(
        app=server,
        stateless=True,
        json_response=True,
        security_settings=_security(),
    )
    return manager, _PostOnly(manager.asgi_app)


class _PostOnly:
    """Refuse GET, and say why.

    A CLASS, not a closure, and that is load-bearing. Starlette's `Route` decides
    what it was handed with `inspect.isfunction`, so a plain function is wrapped
    as a request handler and this answered 405 to POST and 500 to GET. An
    instance with `__call__` is treated as the ASGI app it is.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("method") == "GET":
            body = json.dumps({
                "error": "this endpoint answers POST. It opens no event "
                         "stream: every reply arrives on the request that "
                         "asked for it."}).encode()
            await send({"type": "http.response.start", "status": 405,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"allow", b"POST, DELETE"),
                                    (b"content-length",
                                     str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def _security():
    """Host and Origin checking for the mounted endpoint."""
    hosts = [h for h in (os.environ.get("MCP_ALLOWED_HOSTS") or "").split(",")
             if h.strip()]
    origins = [o for o in
               (os.environ.get("MCP_ALLOWED_ORIGINS") or "").split(",")
               if o.strip()]
    if not hosts and not origins:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False,
                                         allowed_hosts=[], allowed_origins=[])
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[h.strip() for h in hosts],
        allowed_origins=[o.strip() for o in origins])
