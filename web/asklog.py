"""Who asked, counted, for the operator and nobody else.

`answers` keeps a run so that its URL resolves, and it deliberately holds
nothing that could group two runs into one person - web/answers.py says the
set of questions people have asked is not something to hand out by counting,
and that table is what the public /ask/<id> page reads.

That sentence was about the public surface. Whoever pays for the endpoint
still has to know whether anyone is using it, whether the ceilings are sized
right and how much of the demand is being turned away, and none of it was
answerable: a refused question left no trace at all, and a kept one left
nothing to count people by. So the counting lives HERE, in a table no public
route reads, carrying a token that answers "how many people" without
answering "which person":

    HMAC-SHA256(ASK_ASKER_KEY, the address, a newline, the local date)

Rotating on the date is what keeps it a count rather than a history. Two days
cannot be joined, so the token says how many people asked today and cannot
follow one of them across a week. The key is what makes it a token rather
than a thin disguise: an IPv4 address is 32 bits, so a bare hash of one is
undone by trying all four billion, which is seconds of work on any laptop.
Without the key in hand a row cannot be tested against a candidate address.

The date is the LOCAL one, because the query that counts people per day will
group by `at::date` and Postgres reads that in the cluster's timezone. Both
the container and the cluster are set to America/New_York
(deploy/unraid-civicwatch.xml, deploy/postgres-unraid.md), so the two agree.
They have to: rotating on a different day boundary than the query groups on
would split one person's evening across two dates and count them twice.

NULL is a real value here, and it means "this one cannot be attributed" -
which is not the same as "nobody". It happens when ASK_ASKER_KEY is unset,
and when the address is loopback: behind the UI's rewrite EVERY caller
arrives as 127.0.0.1 until ASK_TRUST_PROXY is on (LAUNCH.md 3.5), and hashing
that would file the whole internet under one very busy person. A wrong count
is worse than no count, so it declines to guess rather than quietly inventing
a single visitor. `SELECT count(DISTINCT asker)` skips NULLs, so a
misconfigured day reads as "0 people, N questions" and is obvious.

Writing happens on a background thread behind a bounded queue, and that is
not tidiness. /api/ask is unauthenticated, so a refusal that opened a
database connection would hand anyone who has already been throttled a way to
make the server open one per request - the endpoint's cheapest path would
become its most expensive. Nothing here is allowed to fail the request
either: the reader is owed their answer whether or not it could be counted.
"""
import hashlib
import hmac
import os
import queue
import sys
import threading
import time
from collections import deque

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))

import db                                         # noqa: E402

# Unset means the counting still happens and the identity does not. That is
# the deliberate default: a fresh deployment records how many questions were
# asked and refused without holding anything derived from an address at all,
# and turning on `asker` is a decision somebody makes on purpose.
KEY = (os.environ.get("ASK_ASKER_KEY") or "").encode()

# An address that tells us nothing. "unknown" is what limits.client_ip()
# returns when there is no peer at all.
BLIND = {"unknown", "127.0.0.1", "::1", "localhost"}

# The question, as filed. Refusals are recorded too and the `length` refusal
# is by definition longer than the endpoint's own bound, so this is a second
# ceiling rather than a duplicate of ASK_MAX_CHARS.
KEEP_CHARS = 1000

# Deep enough that no real burst reaches it (the endpoint is bounded at
# ASK_DAILY_MAX runs a day and a handful of concurrent ones), shallow enough
# that a flood of refusals cannot grow it without bound.
QUEUE_MAX = 1000

# Kept runs are bounded by ASK_DAILY_MAX, which is the money ceiling. REFUSALS
# ARE NOT BOUNDED BY ANYTHING - being turned away costs the caller nothing, so
# a caller who has been throttled can go on being throttled all day. That is
# rows on a disk somebody pays for, so the writer stops filing refusals past
# this many in a rolling hour and says how many it dropped. Successes are
# never dropped.
REFUSALS_PER_HOUR = 2000

HOUR = 3600

_q = queue.Queue(maxsize=QUEUE_MAX)
_start = threading.Lock()
_thread = None
_dropped = 0          # rows the queue refused, because it was full


def asker(ip):
    """The day's token for this address, or None if it cannot be one."""
    if not KEY or not ip or ip in BLIND:
        return None
    day = time.strftime("%Y-%m-%d")               # local, see the docstring
    return hmac.new(KEY, f"{ip}\n{day}".encode(),
                    hashlib.sha256).hexdigest()[:32]


def record(ip, outcome, question=None, answer_id=None, ms=None):
    """File one arrival at /api/ask. Never raises, never blocks.

    `outcome` is 'kept', 'error', 'gone' (the reader left mid-run) or one of
    limits.Throttled.kind - 'rate', 'daily', 'busy', 'closed', 'empty',
    'length'.

    The token is computed HERE rather than on the writer thread, so the
    address itself never goes into the queue and is not held anywhere after
    this call returns. It is also the only correct place: the queue can lag,
    and a row filed at 23:59 must carry the day it ARRIVED on."""
    global _dropped
    try:
        q = (question or "").strip()
        row = (asker(ip), outcome, answer_id,
               len(q) or None, ms, q[:KEEP_CHARS] or None)
        _ensure()
        try:
            _q.put_nowait(row)
        except queue.Full:
            _dropped += 1
            print(f"ask not counted: the queue is full ({_dropped} so far)",
                  flush=True)
    except Exception as e:                                    # noqa: BLE001
        # Counting is the least important thing this endpoint does.
        print(f"ask not counted: {type(e).__name__}: {e}", flush=True)


def _ensure():
    """Start the writer on first use, so importing this costs nothing."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    with _start:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_writer, name="asklog",
                                       daemon=True)
            _thread.start()


INSERT = ("INSERT INTO asks (asker, outcome, answer_id, chars, ms, question) "
          "VALUES (%s, %s, %s, %s, %s, %s)")


def _writer():
    """One connection, reopened when it dies, for the life of the process."""
    con = None
    refusals = deque()                            # monotonic, last hour
    dropped = 0
    while True:
        row = _q.get()
        outcome = row[1]
        if outcome != "kept":
            now = time.monotonic()
            while refusals and now - refusals[0] > HOUR:
                refusals.popleft()
            if len(refusals) >= REFUSALS_PER_HOUR:
                dropped += 1
                if dropped % 100 == 1:
                    print(f"refusals not counted: {REFUSALS_PER_HOUR}/hour is "
                          f"the cap ({dropped} dropped)", flush=True)
                continue
            refusals.append(now)
        for attempt in (1, 2):
            try:
                if con is None or con.closed:
                    con = db.connect(autocommit=True)
                con.execute(INSERT, row)
                break
            except Exception as e:                            # noqa: BLE001
                # The ordinary failure is a connection that went idle while
                # Postgres restarted, and it only shows up on use. One retry
                # on a fresh connection covers it; a second failure is the
                # database being down, which is not this thread's problem to
                # solve and is already loud elsewhere.
                if con is not None:
                    try:
                        con.close()
                    except Exception:                         # noqa: BLE001
                        pass
                    con = None
                if attempt == 2:
                    print(f"ask not counted: {type(e).__name__}: {e}",
                          flush=True)


def state():
    """For the operator: whether this is on, and what it has lost."""
    return {"key": bool(KEY), "queued": _q.qsize(), "dropped": _dropped}
