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
    concurrency       runs hold a thread and a paid call for ~40s each

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
MAX_CHARS = int(os.environ.get("ASK_MAX_CHARS") or 500)
# Off by default: with no proxy in front, X-Forwarded-For is entirely
# attacker-supplied and trusting it hands out a fresh quota per request.
# Turn it on ONLY when a reverse proxy terminates connections for this server.
TRUST_PROXY = os.environ.get("ASK_TRUST_PROXY") or "".lower() in ("1", "true", "yes")
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


def client_ip(handler):
    """The address to hold responsible.

    Behind a reverse proxy every peer is 127.0.0.1, so the limit would apply
    to the proxy rather than to anyone. X-Forwarded-For fixes that and is
    forgeable, so it is read ONLY from a loopback peer and ONLY when the
    operator has said a proxy is there - and then the LAST hop is taken, not
    the first: our proxy appends the address it actually saw, and everything
    to its left was supplied by the client and can say anything.
    """
    peer = handler.client_address[0]
    if TRUST_PROXY and (peer == "::1" or peer.startswith("127.")):
        xff = handler.headers.get("X-Forwarded-For", "")
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
                "trust_proxy": TRUST_PROXY}
