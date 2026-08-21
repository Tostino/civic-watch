"""Cost and abuse control for the one endpoint that spends money."""
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
    """The address to hold responsible."""
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
# WHOEVER SETS ASK_TRUST_PROXY DECIDES WHETHER ANY OF THIS WORKS. Every
# ceiling below is per address, and the address comes from client_ip() above,
# which reads X-Forwarded-For only when TRUST_PROXY is on. In the deployed
# container the API listens on loopback with the UI's rewrite in front of it,
# so EVERY caller looks like 127.0.0.1 and the whole internet shares one
# bucket unless it is set. The variable is named for /api/ask and LAUNCH.md
# §3.5 explains it as Ask's problem; it is equally this file's. Verified both
# ways with MCP_PER_IP=2: at 1 three addresses get three buckets, at 0 they
# get one between them.
MCP_WINDOW = int(os.environ.get("MCP_WINDOW") or 60)          # seconds
# RAISED FROM 60 when the tools learned to page (2026-08-19). Sixty was chosen
# when one call returned the whole of whatever was asked for; a window is 80
# turns now, so reading the longest item end to end is 16 calls, the longest
# case 16 and a 272-item agenda 4. A client walking one long item and one long
# case spent 32 of its 60 on work that used to cost 2, and hit the ceiling
# doing exactly what the tool descriptions tell it to do.
#
# Raising it does not raise peak load, which is what MCP_MAX_CONCURRENT bounds
# and what it stays bounded by: a continuation is an indexed read of rows the
# first call already located, measured at ~110 ms. The rate ceiling is here to
# stop a caller monopolising the archive over minutes, and 180 still does that.
MCP_PER_IP = int(os.environ.get("MCP_PER_IP") or 180)         # calls per window
# search_transcript gets its own, lower ceiling, and it does NOT move with the
# one above. It is the expensive tool - it encodes the query before it can
# rank anything - and paging it costs a fresh encode per window, so the tool
# that got cheaper to page is not the tool this ceiling is protecting. The
# other five are indexed reads of the published record, which is a document
# the county publishes anyway.
MCP_SEARCH_PER_IP = int(os.environ.get("MCP_SEARCH_PER_IP") or 20)
MCP_MAX_CONCURRENT = int(os.environ.get("MCP_MAX_CONCURRENT") or 8)

# Tools metered against the tighter ceiling as well as the general one.
MCP_HEAVY = {"search_transcript"}

_mcp_lock = threading.Lock()
_mcp_hits = {}        # ip -> deque[monotonic seconds], every tool call
_mcp_heavy = {}       # ip -> deque[monotonic seconds], the heavy ones only
_mcp_running = 0


def _window(book, ip, now, limit, one, many):
    """One sliding window. Caller holds `_mcp_lock`."""
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
    """Claim a slot for one MCP tool call, or raise Throttled."""
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


# ------------------------------------------------------------------- say
#
# Reading an answer aloud, one sentence per call. A DIFFERENT SHAPE from the
# two ceilings above: a narration is intrinsically chatty - twenty-odd calls
# for one answer, because the chunk a voice speaks is the chunk the page
# highlights - so the per-call cost has to be low and the count high. It is,
# after the first listener: web/say.py caches every rendered sentence on disk
# and the second request for one is a file read.
#
# What this bounds is therefore the FIRST listener's CPU, and the way to spend
# it is not rate but concurrency, which is why the concurrent ceiling is the
# tight one. Synthesis measured at 7x real time on one core; two at once
# leaves the search path its cores.
SAY_WINDOW = int(os.environ.get("SAY_WINDOW") or 60)
SAY_PER_IP = int(os.environ.get("SAY_PER_IP") or 120)
SAY_MAX_CONCURRENT = int(os.environ.get("SAY_MAX_CONCURRENT") or 2)

_say_lock = threading.Lock()
_say_hits = {}
_say_running = 0


def say_reserve(ip):
    """Claim a slot for one sentence of narration, or raise Throttled."""
    now = time.monotonic()
    with _say_lock:
        global _say_running
        for dead in [i for i, q in _say_hits.items()
                     if not q or now - q[-1] > SAY_WINDOW]:
            del _say_hits[dead]
        if len(_say_hits) > MAX_TRACKED_IPS:
            for i in sorted(_say_hits, key=lambda i: _say_hits[i][-1])[
                    :len(_say_hits) - MAX_TRACKED_IPS]:
                del _say_hits[i]

        # Zero closes the surface without a deploy, as everywhere else here.
        if SAY_PER_IP <= 0:
            raise Throttled("This archive is not reading answers aloud at the "
                            "moment.", kind="closed")

        seen = _say_hits.setdefault(ip, deque())
        while seen and now - seen[0] > SAY_WINDOW:
            seen.popleft()
        if len(seen) >= SAY_PER_IP:
            wait = int(SAY_WINDOW - (now - seen[0])) + 1
            raise Throttled(
                f"That is {SAY_PER_IP} sentences read aloud in "
                f"{_plain(SAY_WINDOW)} from this address, which is the limit. "
                f"Try again in {_plain(wait)}.", retry_after=wait, kind="rate")
        if _say_running >= SAY_MAX_CONCURRENT:
            raise Throttled("This archive is reading as much as it can at "
                            "once. Try again in a moment.",
                            retry_after=3, kind="busy")
        seen.append(now)
        _say_running += 1

    done = False

    def release():
        nonlocal done
        with _say_lock:
            global _say_running
            if not done:
                done = True
                _say_running = max(0, _say_running - 1)

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
    """The MCP ceilings, for the page that tells a reader how to connect."""
    return {"per_ip": MCP_PER_IP, "heavy_per_ip": MCP_SEARCH_PER_IP,
            "window": MCP_WINDOW, "heavy": sorted(MCP_HEAVY)}


def _mcp_state():
    """The tool surface's own counters, which share none of Ask's budget."""
    with _mcp_lock:
        return {"running": _mcp_running, "max_concurrent": MCP_MAX_CONCURRENT,
                "tracked_ips": len(_mcp_hits), "per_ip": MCP_PER_IP,
                "heavy_per_ip": MCP_SEARCH_PER_IP, "window": MCP_WINDOW}


# ---------------------------------------------------------------- searching
#
# THE READING SURFACE THAT COSTS REAL CPU. /api/find runs the embedding model
# to rank paraphrase, and in the container that is on the CPU: measured at
# 1.28s of CPU time per query at torch's default thread count, 0.93s at the
# four the image now pins it to. Nothing else a reader can reach costs a
# fraction of that. /api/search is keyword-only and cheap by comparison, and
# is metered here only because an unbounded loop over it is still a loop.
#
# TWO CEILINGS, AND THE SECOND IS THE ONE THAT ALWAYS BITES.
#
# The per-address window is the fair one, and it can only work where the
# address is known. In the deployed container the UI renders /search on the
# server and calls this API over loopback WITHOUT the reader's address, so
# every page-load search arrives as 127.0.0.1. A per-address ceiling applied
# to that would not throttle a crawler, it would throttle everybody at once,
# together, in one bucket. So the page now forwards the reader's address and
# this refuses to meter anything that still arrives without one - see
# `search_reserve`.
#
# The concurrency ceiling has no such dependency: it bounds how much of the
# machine this endpoint can hold at any instant no matter who is asking or
# whether we can tell. Four concurrent searches against four torch threads is
# sixteen, which leaves a sixteen-core box able to do something else. That is
# the ceiling that turns "the API held 4.4 cores for five hours" into a bound.
SEARCH_WINDOW = int(os.environ.get("SEARCH_WINDOW") or 60)
SEARCH_PER_IP = int(os.environ.get("SEARCH_PER_IP") or 60)
SEARCH_MAX_CONCURRENT = int(os.environ.get("SEARCH_MAX_CONCURRENT") or 4)

_search_lock = threading.Lock()
_search_hits = {}
_search_running = 0


def search_reserve(ip):
    """Claim a slot for one search, or raise Throttled.

    Returns a release() to call in a finally, like the others here.
    """
    now = time.monotonic()
    # An address we were not given. The UI forwards the reader's, so this is
    # something calling the API directly on the loopback interface it binds -
    # and metering it would put unrelated callers in one bucket. The
    # concurrency ceiling below still applies, which is the one that bounds
    # the machine.
    anonymous = ip in ("127.0.0.1", "::1", "unknown")
    with _search_lock:
        global _search_running
        for dead in [i for i, q in _search_hits.items()
                     if not q or now - q[-1] > SEARCH_WINDOW]:
            del _search_hits[dead]
        if len(_search_hits) > MAX_TRACKED_IPS:
            for i in sorted(_search_hits, key=lambda i: _search_hits[i][-1])[
                    :len(_search_hits) - MAX_TRACKED_IPS]:
                del _search_hits[i]

        if not anonymous and SEARCH_PER_IP > 0:
            seen = _search_hits.setdefault(ip, deque())
            while seen and now - seen[0] > SEARCH_WINDOW:
                seen.popleft()
            if len(seen) >= SEARCH_PER_IP:
                wait = int(SEARCH_WINDOW - (now - seen[0])) + 1
                raise Throttled(
                    f"That is {SEARCH_PER_IP} searches in "
                    f"{_plain(SEARCH_WINDOW)} from this address, which is the "
                    f"limit. Try again in {_plain(wait)}.",
                    retry_after=wait, kind="rate")
            seen.append(now)

        if _search_running >= SEARCH_MAX_CONCURRENT:
            raise Throttled(
                "This archive is running as many searches at once as it can. "
                "Try again in a moment.", retry_after=2, kind="busy")
        _search_running += 1

    done = False

    def release():
        nonlocal done
        with _search_lock:
            global _search_running
            if not done:
                done = True
                _search_running = max(0, _search_running - 1)

    return release

