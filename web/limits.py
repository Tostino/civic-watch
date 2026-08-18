"""Cost and abuse control for the one endpoint that spends money.

`/api/ask` is public, unauthenticated, and one call is up to `MAX_STEPS` turns
of a paid model plus every tool call it decides to make. Measured: a normal
question is 5 model turns and 11 tool calls over 38 seconds. Nothing stopped
anyone from running that in a loop, which on a public URL is not a threat
model, it is a Tuesday.

Four bounds, cheapest checked first:

    question length   a long question inflates every prompt in the run
    per IP            a reader asks a few questions; a script asks thousands
    per day, global   the money ceiling. Set it from your cost per run.
    concurrency       runs hold a thread and a paid call for their whole life,
                      which ASK_DEADLINE now puts at up to seven minutes

The daily cap is the one that actually protects the account, because the
per-IP window bounds one client and a botnet is many clients. It is global
and deliberately blunt: when it trips, Ask is closed until the window rolls,
and the archive still reads and searches normally without it.

Everything here is in-process and forgotten on restart, like the admin
sessions. That is the right trade for a single-server archive: no dependency
to run, no state to migrate, and the failure mode of a restart is that one
window resets - not that the endpoint opens up.

Counting only ACCEPTED runs is deliberate. If refusals counted too, a script
that keeps knocking would extend its own lockout for ever and the message it
is shown ("try again in 4 minutes") would be a lie.
"""
import os
import threading
import time
from collections import deque

WINDOW = int(os.environ.get("ASK_WINDOW") or 600)          # seconds
PER_IP = int(os.environ.get("ASK_PER_IP") or 6)            # runs per window
DAILY_MAX = int(os.environ.get("ASK_DAILY_MAX") or 400)    # runs per 24h, global
MAX_CONCURRENT = int(os.environ.get("ASK_MAX_CONCURRENT") or 2)
# 500 was sized against one-sentence questions. The ones worth the agent's new
# budget are longer - they name the people, the years and the shape of the
# answer they want - and the bound exists to stop a question inflating every
# prompt in the run, which 1,000 characters does not do next to MAX_EVIDENCE.
MAX_CHARS = int(os.environ.get("ASK_MAX_CHARS") or 1000)
# Off by default: with no proxy in front, X-Forwarded-For is entirely
# attacker-supplied and trusting it hands out a fresh quota per request.
# Turn it on ONLY when a reverse proxy terminates connections for this server.
TRUST_PROXY = (os.environ.get("ASK_TRUST_PROXY") or "").lower() \
    in ("1", "true", "yes")
# A public endpoint is also a way to make a server allocate memory. One entry
# per IP is small, but "one per source address" is unbounded by definition.
MAX_TRACKED_IPS = 4096

DAY = 86_400

_lock = threading.Lock()
_hits = {}            # ip -> deque[monotonic seconds]
_day = deque()        # monotonic seconds of every accepted run in 24h
_running = 0


class Throttled(Exception):
    """Refused before any paid work happened. `retry_after` is in seconds."""

    def __init__(self, message, retry_after=None, kind="rate"):
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after
        self.kind = kind


def client_ip(request):
    """The address to hold responsible.

    Behind a reverse proxy every peer is 127.0.0.1, so the limit would apply
    to the proxy rather than to anyone. X-Forwarded-For fixes that and is
    forgeable, so it is read ONLY from a loopback peer and ONLY when the
    operator has said a proxy is there - and then the LAST hop is taken, not
    the first: our proxy appends the address it actually saw, and everything
    to its left was supplied by the client and can say anything.

    `request` is a Starlette Request. `request.client` is None for a scope
    with no peer at all, which ASGI permits and a test client produces; an
    address that cannot be read is not one that can be excused, so it falls
    back to a constant that shares one quota rather than to no limit.
    """
    peer = request.client.host if request.client else "unknown"
    if TRUST_PROXY and (peer == "::1" or peer.startswith("127.")):
        xff = request.headers.get("X-Forwarded-For", "")
        if xff.strip():
            return xff.split(",")[-1].strip()
    return peer


def _prune(now):
    """Caller holds the lock."""
    while _day and now - _day[0] > DAY:
        _day.popleft()
    dead = [ip for ip, q in _hits.items()
            if not q or now - q[-1] > WINDOW]
    for ip in dead:
        del _hits[ip]
    if len(_hits) > MAX_TRACKED_IPS:
        # Oldest last-seen first. Evicting a tracked IP only forgives it.
        for ip in sorted(_hits, key=lambda i: _hits[i][-1])[
                :len(_hits) - MAX_TRACKED_IPS]:
            del _hits[ip]


def reserve(ip, question):
    """Claim a slot, or raise Throttled. Returns a release() to call in a
    finally: the concurrency count is only meaningful if it always comes
    back down."""
    q = (question or "").strip()
    if not q:
        raise Throttled("Ask a question first.", kind="empty")
    if len(q) > MAX_CHARS:
        raise Throttled(
            f"That question is {len(q):,} characters and the limit is "
            f"{MAX_CHARS:,}. Ask a shorter one.", kind="length")

    now = time.monotonic()
    with _lock:
        global _running
        _prune(now)

        # Zero is the off switch: it closes the paid endpoint without a
        # deploy, and the archive keeps reading and searching. Handled before
        # the window check because that one reads seen[0] to say how long to
        # wait, and at a limit of zero there is no first hit to read.
        if PER_IP <= 0 or DAILY_MAX <= 0:
            raise Throttled(
                "The archive is not answering questions at the moment. "
                "Search and the published record are unaffected.",
                kind="closed")

        seen = _hits.setdefault(ip, deque())
        while seen and now - seen[0] > WINDOW:
            seen.popleft()
        if len(seen) >= PER_IP:
            wait = int(WINDOW - (now - seen[0])) + 1
            raise Throttled(
                f"That is {PER_IP} question{'' if PER_IP == 1 else 's'} in "
                f"{_plain(WINDOW)} from this address, which is the limit. "
                f"Try again in {_plain(wait)}.",
                retry_after=wait, kind="rate")

        if len(_day) >= DAILY_MAX:
            wait = int(DAY - (now - _day[0])) + 1
            raise Throttled(
                "The archive has answered as many questions today as it is "
                "funded to answer. Search and the record are unaffected. "
                f"Ask again in {_plain(wait)}.",
                retry_after=wait, kind="daily")

        if _running >= MAX_CONCURRENT:
            raise Throttled(
                "The archive is answering as many questions as it can at "
                "once. Try again in a minute.", retry_after=60, kind="busy")

        seen.append(now)
        _day.append(now)
        _running += 1

    done = False

    def release():
        nonlocal done
        with _lock:
            global _running
            if not done:
                done = True
                _running = max(0, _running - 1)

    return release


# ------------------------------------------------------------- MCP tools
# A different bill, so a different budget.
#
# An MCP tool call spends no model tokens at all. It is one indexed query,
# and for search_transcript one pass of the 0.6B query encoder on the CPU.
# Metering it out of ASK_DAILY_MAX would let an MCP client close the endpoint
# a reader pays for, and letting it through the door unmetered would hand
# anyone a way to make this server encode queries all day.
#
# So: a rate and a concurrency ceiling, and deliberately NO daily cap. The
# daily one exists over there because model calls cost money that runs out.
# Nothing here does. What it protects is the CPU and the connection pool.
MCP_WINDOW = int(os.environ.get("MCP_WINDOW") or 60)          # seconds
MCP_PER_IP = int(os.environ.get("MCP_PER_IP") or 60)          # calls per window
# search_transcript gets its own, lower ceiling. It is the expensive tool -
# it encodes the query before it can rank anything - and it is the one worth
# pulling in bulk, because what it returns is passage text rather than a
# count. The other four are indexed reads of the published record, which is a
# document the county publishes anyway.
MCP_SEARCH_PER_IP = int(os.environ.get("MCP_SEARCH_PER_IP") or 20)
MCP_MAX_CONCURRENT = int(os.environ.get("MCP_MAX_CONCURRENT") or 8)

# Tools metered against the tighter ceiling as well as the general one.
MCP_HEAVY = {"search_transcript"}

_mcp_lock = threading.Lock()
_mcp_hits = {}        # ip -> deque[monotonic seconds], every tool call
_mcp_heavy = {}       # ip -> deque[monotonic seconds], the heavy ones only
_mcp_running = 0


def _window(book, ip, now, limit, one, many):
    """One sliding window. Caller holds `_mcp_lock`.

    Appends nothing: the caller commits only once every window has passed,
    so a call refused by the second limit does not count against the first.

    `one` and `many` are the noun, both ways. A limit of 1 is a real setting -
    it is how an operator throttles the expensive tool down to almost nothing
    without closing it - and "1 transcript searches" is the sentence a reader
    would then be shown.
    """
    seen = book.setdefault(ip, deque())
    while seen and now - seen[0] > MCP_WINDOW:
        seen.popleft()
    if len(seen) >= limit:
        wait = int(MCP_WINDOW - (now - seen[0])) + 1
        raise Throttled(
            f"That is {limit} {one if limit == 1 else many} in "
            f"{_plain(MCP_WINDOW)} from this address, which is the limit. "
            f"Try again in {_plain(wait)}.",
            retry_after=wait, kind="rate")
    return seen


def mcp_reserve(ip, tool):
    """Claim a slot for one MCP tool call, or raise Throttled.

    Returns a release() for the caller's `finally`, on the same contract as
    `reserve`: the concurrency count is only meaningful if it always comes
    back down.
    """
    now = time.monotonic()
    with _mcp_lock:
        global _mcp_running
        for book in (_mcp_hits, _mcp_heavy):
            for dead in [i for i, q in book.items()
                         if not q or now - q[-1] > MCP_WINDOW]:
                del book[dead]
            if len(book) > MAX_TRACKED_IPS:
                for i in sorted(book, key=lambda i: book[i][-1])[
                        :len(book) - MAX_TRACKED_IPS]:
                    del book[i]

        # Zero is the off switch here too: it closes the MCP surface without a
        # deploy, and every reading endpoint carries on.
        if MCP_PER_IP <= 0:
            raise Throttled(
                "This archive is not serving tool calls at the moment.",
                kind="closed")

        # BOTH windows are checked before EITHER is written to. Committing the
        # general one first and then refusing on the heavy one would charge a
        # caller for a call that never ran.
        general = _window(_mcp_hits, ip, now, MCP_PER_IP,
                          "tool call", "tool calls")
        heavy = (_window(_mcp_heavy, ip, now, MCP_SEARCH_PER_IP,
                         "transcript search", "transcript searches")
                 if tool in MCP_HEAVY else None)

        if _mcp_running >= MCP_MAX_CONCURRENT:
            raise Throttled(
                "This archive is running as many tool calls as it can at "
                "once. Try again in a moment.", retry_after=5, kind="busy")

        general.append(now)
        if heavy is not None:
            heavy.append(now)
        _mcp_running += 1

    done = False

    def release():
        nonlocal done
        with _mcp_lock:
            global _mcp_running
            if not done:
                done = True
                _mcp_running = max(0, _mcp_running - 1)

    return release


def _plain(seconds):
    """The wait, in words. A reader is being told to come back, so this says
    minutes rather than 247."""
    if seconds < 60:
        return f"{seconds} seconds"
    m = round(seconds / 60)
    if m < 60:
        return f"{m} minute{'' if m == 1 else 's'}"
    h = round(seconds / 3600)
    return f"{h} hour{'' if h == 1 else 's'}"


def state():
    """For the operator: what the limiter currently holds."""
    now = time.monotonic()
    with _lock:
        _prune(now)
        return {"running": _running, "max_concurrent": MAX_CONCURRENT,
                "today": len(_day), "daily_max": DAILY_MAX,
                "tracked_ips": len(_hits), "per_ip": PER_IP,
                "window": WINDOW, "max_chars": MAX_CHARS,
                "trust_proxy": TRUST_PROXY,
                "mcp": _mcp_state()}


def mcp_public():
    """The MCP ceilings, for the page that tells a reader how to connect.

    The CEILINGS only, not the counters. What is currently running is an
    operator's number; what a reader needs before pointing a client at this
    archive is what it will refuse. Served rather than written into the copy
    so the sentence on /about cannot drift from the setting behind it, which
    is the one way a stated number goes quietly wrong.
    """
    return {"per_ip": MCP_PER_IP, "heavy_per_ip": MCP_SEARCH_PER_IP,
            "window": MCP_WINDOW, "heavy": sorted(MCP_HEAVY)}


def _mcp_state():
    """The tool surface's own counters, which share none of Ask's budget."""
    with _mcp_lock:
        return {"running": _mcp_running, "max_concurrent": MCP_MAX_CONCURRENT,
                "tracked_ips": len(_mcp_hits), "per_ip": MCP_PER_IP,
                "heavy_per_ip": MCP_SEARCH_PER_IP, "window": MCP_WINDOW}
