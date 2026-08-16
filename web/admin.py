"""The curation console's data layer and its authentication (§9, §5.8, D1).

Everything here exists to close one loop: the review checks in bin/audit.py
report misattributions, and until now they reported into a void - an unordered
list, no queue, nowhere to act. This module serves the queues ORDERED BY
IMPACT (utterances a decision fixes, R9.2), the evidence to decide with, and
the writes at both grains the model supports:

    whole voice   -> speaker_label / speaker_ignore   (video_id, local_label)
    a range       -> speaker_override                 (video_id, idx range)

Every write that changes a resolved name is followed by
index_passages.refresh_video, because the name lives inside the embedding and
the BM25 postings (gotcha 46): a correction that stops at the transcript
leaves search answering with the old name.

Auth is D1, the Jupyter model, with one deviation demanded by the operating
rules: the startup token is never printed and never logged. It is written to
a mode-600 gitignored file next to env.local.sh, and only the PATH is
announced. POSTing it to /api/admin/login exchanges it for an httpOnly
SameSite=Lax session cookie, so the secret is never in a URL, a referrer or
browser history. Sessions live in this process; a restart invalidates them
along with the token.

Admin routes refuse non-loopback clients outright. This server has no TLS,
and D1 forbids a bearer token over plain HTTP on a network - so rather than
gate on "without TLS", the condition that cannot occur here, loopback is the
only interface admin will ever answer on.
"""
import datetime
import hmac
import os
import re
import secrets
import sys
import threading
import time
from http.cookies import SimpleCookie

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

COOKIE = "pasco_admin"
# Where corrections re-embed. The web server already holds the embedding model
# on this device (tools.warm), and vec_cache means most corrections re-embed
# nothing at all.
DEVICE = os.environ.get("PASCO_EMBED_DEVICE") or "cuda:1"

_token = None
_token_path = None
_sessions = {}          # sid -> created_at. Process-lifetime, like the token.
_lock = threading.Lock()


# ------------------------------------------------------------------- auth
def init(root):
    """Fresh token per process start (D1). Returns the path, never the token."""
    global _token, _token_path
    _token = secrets.token_urlsafe(32)
    _token_path = os.path.join(root, ".admin_token")
    # O_TRUNC + explicit chmod: O_CREAT's mode is ignored when the file
    # already exists, and a stale world-readable file would defeat the point.
    fd = os.open(_token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(_token + "\n")
    os.chmod(_token_path, 0o600)
    return _token_path


# Headers only a proxy adds. `x-forwarded-host` is deliberately NOT here: the
# app's own Next rewrite adds that one on every request, including the
# operator's on this machine, so treating it as evidence would lock the console
# out of its own front end. Measured, not assumed - a local Next rewrite sends
# x-forwarded-host and nothing else, and passes an incoming x-forwarded-for and
# x-real-ip straight through.
PROXIED = ("x-forwarded-for", "x-real-ip", "forwarded")


def loopback(handler):
    """Is this request genuinely from this machine?

    The peer address alone cannot answer that, and believing it was the whole
    of gotcha 94. `client_address` is the TCP peer, so ANY reverse proxy makes
    every request on earth look like 127.0.0.1 - and this app ships with one,
    the `/api/:path*` rewrite in ui/next.config.ts. Verified before the fix:
    `/api/admin/session` answered 200 through the public origin and
    `POST /api/admin/login` reached the handler and validated tokens.

    So two conditions, and they answer different questions. The peer must be
    loopback, which rules out anyone reaching the port directly. And the
    request must carry no forwarding header, which rules out anyone who came
    through a proxy that put itself in the peer slot. A genuinely local client
    sends neither.

    This is why it does not consult ASK_TRUST_PROXY: that flag says "believe
    the forwarded address for rate limiting", which is a statement about
    counting requests. Admin is not counting anything - for admin, the presence
    of the header is itself the disqualifying fact.

    The operator still reaches the console the documented way: on the machine,
    or over an SSH tunnel to it, where no proxy sits in between.
    """
    ip = handler.client_address[0]
    if not (ip == "::1" or ip.startswith("127.")):
        return False
    return not any(handler.headers.get(h) for h in PROXIED)


def session_of(handler):
    c = SimpleCookie()
    c.load(handler.headers.get("Cookie", ""))
    m = c.get(COOKIE)
    return m.value if m and m.value in _sessions else None


def login(token):
    """Exchange the startup token for a session id, or None."""
    if not _token or not hmac.compare_digest(str(token or ""), _token):
        return None
    sid = secrets.token_urlsafe(32)
    with _lock:
        _sessions[sid] = time.time()
    return sid


def logout(sid):
    with _lock:
        _sessions.pop(sid, None)


def cookie_header(sid):
    # No Secure flag: admin only ever answers on loopback (above), where the
    # flag would be a lie about the transport rather than a protection.
    return f"{COOKIE}={sid}; HttpOnly; SameSite=Lax; Path=/"


def clear_cookie_header():
    return f"{COOKIE}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"


# ------------------------------------------------------------------ health
def state(con, authed):
    """R9.4: pipeline health without a terminal. Cheap enough to serve on
    every console load; the one heavy scan (basis breakdown over the
    utterance_speaker view) is what the whole dashboard hangs on anyway."""
    if not authed:
        return {"authenticated": False}
    basis = {r[0] or "unnamed": r[1] for r in con.execute(
        "SELECT basis, COUNT(*) FROM utterance_speaker GROUP BY 1")}
    total = sum(basis.values())
    named = total - basis.get("unnamed", 0)
    ov = {f"{r[0]}/{r[1]}": r[2] for r in con.execute(
        "SELECT status, action, COUNT(*) FROM speaker_override GROUP BY 1, 2")}
    return {
        "authenticated": True,
        "utterances": total,
        "named": named,
        "basis": basis,
        "labels": con.execute(
            "SELECT COUNT(*) FROM speaker_label").fetchone()[0],
        "ignores": con.execute(
            "SELECT COUNT(*) FROM speaker_ignore").fetchone()[0],
        "overrides": ov,
        "queues": {
            "splits": con.execute(f"SELECT COUNT(*) {SPLITS_FROM}").fetchone()[0],
            "proposals": con.execute(
                "SELECT COUNT(*) FROM speaker_override WHERE status = 'pending'"
            ).fetchone()[0],
        },
    }


# ------------------------------------------------------------------ queues
# The audit's speaker.one_voice_per_meeting, verbatim in structure: a board
# member attached to two voices in one meeting. Ordered by utterances
# affected, NOT by id or voice count - a review list is only workable if its
# head is the row worth fixing first (the old check sorted by voice count and
# put a 3-utterance stutter above a 500-utterance misattribution).
SPLITS_FROM = """
    FROM (SELECT si.video_id, si.name,
                 COUNT(DISTINCT si.local_label) AS voices,
                 (SELECT COUNT(*) FROM utterance_speaker us
                   WHERE us.video_id = si.video_id
                     AND us.name = si.name) AS utts
          FROM speaker_identity si
          JOIN people p ON lower(p.surname) = lower(si.name)
          WHERE si.name IS NOT NULL
          GROUP BY 1, 2
          HAVING COUNT(DISTINCT si.local_label) > 1) t"""


def queues(con):
    splits = [dict(r) for r in con.execute(f"""
        SELECT t.video_id, t.name, t.voices, t.utts,
               v.title, v.upload_date, v.kind, v.meeting_id
        {SPLITS_FROM}
        JOIN videos v ON v.id = t.video_id
        ORDER BY t.utts DESC, t.voices DESC""")]

    proposals = [dict(r) for r in con.execute("""
        SELECT o.id, o.video_id, o.start_idx, o.end_idx, o.action, o.name,
               o.note, o.author, o.created_at,
               (o.end_idx - o.start_idx + 1) AS span,
               v.title, v.upload_date
        FROM speaker_override o JOIN videos v ON v.id = o.video_id
        WHERE o.status = 'pending'
        ORDER BY (o.end_idx - o.start_idx + 1) DESC, o.created_at""")]

    # R9.2: triage an unnamed voice, highest impact first. The unit is the
    # cluster - archive-wide reach is what makes one listen pay for many
    # meetings - and each row carries one playable sample so the queue itself
    # is auditionable.
    voices = []
    for r in con.execute("""
        WITH agg AS (SELECT cluster, COUNT(*) AS lines,
                            COUNT(DISTINCT video_id) AS meetings
                     FROM utterances WHERE cluster IS NOT NULL
                     GROUP BY cluster)
        SELECT agg.cluster, agg.lines, agg.meetings
        FROM agg
        WHERE NOT EXISTS (SELECT 1 FROM speaker_identity si
                          WHERE si.cluster = agg.cluster AND si.name IS NOT NULL)
          AND NOT EXISTS (SELECT 1 FROM speaker_ignore ig
                          JOIN speaker_identity si
                            ON si.video_id = ig.video_id
                           AND si.local_label = ig.local_label
                          WHERE si.cluster = agg.cluster)
        ORDER BY agg.meetings * agg.lines DESC
        LIMIT 40"""):
        d = dict(r)
        s = con.execute(f"""
            SELECT u.video_id, u.local_label, u.start, LEFT(u.text, 160) AS text
            FROM utterances u
            WHERE u.cluster = %s AND {SUBSTANTIVE}
            ORDER BY LENGTH(u.text) DESC LIMIT 1""", (r["cluster"],)).fetchone()
        d["sample"] = dict(s) if s else None
        voices.append(d)

    # Recent corrections, so what has been done is visible beside what is left
    # (R9.5: human statements are permanent, and they should look it).
    recent = [dict(r) for r in con.execute("""
        SELECT o.id, o.video_id, o.start_idx, o.end_idx, o.action, o.name,
               o.status, o.author, o.note, o.created_at
        FROM speaker_override o
        ORDER BY o.created_at DESC LIMIT 15""")]

    # Whole-voice labels, same reason - and learned the hard way. A queue is a
    # one-way to-do list: deciding a row makes it VANISH, so a wrong decision
    # is invisible the moment it is made. A mislabeled voice ("Mike Wells" for
    # "Wells") left the queue and had nowhere on the page to be seen again.
    # This ledger is where a decision stays visible after the queue forgets it.
    labels = [dict(r) for r in con.execute("""
        SELECT sl.video_id, sl.local_label, sl.name, sl.note, sl.labeled_at,
               v.upload_date, v.kind,
               (SELECT COUNT(*) FROM utterances u
                 WHERE u.video_id = sl.video_id
                   AND u.local_label = sl.local_label) AS utts
        FROM speaker_label sl JOIN videos v ON v.id = sl.video_id
        ORDER BY sl.labeled_at DESC LIMIT 15""")]

    return {"splits": splits, "proposals": proposals, "voices": voices,
            "recent": recent, "labels": labels}


# Mirrors web/api.py SUBSTANTIVE (which mirrors bin/triage.py): a sample that
# cannot identify anyone is not evidence.
SUBSTANTIVE = """
    (LENGTH(TRIM(u.text)) - LENGTH(REPLACE(TRIM(u.text),' ','')) + 1) > 4
    AND (u."end" - u.start) >= 2.0
"""


# ---------------------------------------------------------------- evidence
def review(con, video_id, name=None, label=None):
    """The evidence pack for one recording's contested voices.

    The transcript itself comes from /api/transcript/<id>, which already
    carries local_label, basis and contested per line; this returns what that
    cannot: the voices as objects, why the pipeline proposed each name
    (speaker_identity source + confidence = `basis` at display), whether the
    measurement agrees (voice_affinity), and what the same cluster sounds
    like in OTHER meetings - which is the strongest cheap evidence for
    "is this really the same person".
    """
    v = con.execute("""
        SELECT v.id, v.title, v.upload_date, v.kind, v.duration, v.meeting_id,
               m.date, m.body
        FROM videos v LEFT JOIN meetings m ON m.id = v.meeting_id
        WHERE v.id = %s""", (video_id,)).fetchone()
    if not v:
        return None

    where, params = "", [video_id]
    if name:
        where, params = "AND si.name = %s", [video_id, name]
    elif label:
        where, params = "AND si.local_label = %s", [video_id, label]

    voices = []
    for r in con.execute(f"""
        SELECT si.local_label, si.cluster, si.name, si.confidence, si.source,
               (sl.name IS NOT NULL) AS labeled, sl.name AS label_name,
               sl.note AS label_note,
               (ig.video_id IS NOT NULL) AS ignored,
               (SELECT COUNT(*) FROM utterances u
                 WHERE u.video_id = si.video_id
                   AND u.local_label = si.local_label) AS utts,
               (SELECT MIN(u.start) FROM utterances u
                 WHERE u.video_id = si.video_id
                   AND u.local_label = si.local_label) AS first_at
        FROM speaker_identity si
        LEFT JOIN speaker_label sl ON sl.video_id = si.video_id
                                  AND sl.local_label = si.local_label
        LEFT JOIN speaker_ignore ig ON ig.video_id = si.video_id
                                   AND ig.local_label = si.local_label
        WHERE si.video_id = %s {where}
        ORDER BY utts DESC""", params):
        d = dict(r)
        d["affinity"] = [dict(a) for a in con.execute("""
            SELECT name, similarity FROM voice_affinity
            WHERE video_id = %s AND local_label = %s
            ORDER BY similarity DESC""", (video_id, r["local_label"]))]
        # The three longest substantive lines of this voice, to listen to.
        d["samples"] = [dict(s) for s in con.execute(f"""
            SELECT u.idx, u.start, u."end", LEFT(u.text, 200) AS text
            FROM utterances u
            WHERE u.video_id = %s AND u.local_label = %s AND {SUBSTANTIVE}
            ORDER BY LENGTH(u.text) DESC LIMIT 3""",
            (video_id, r["local_label"]))]
        # The same cluster, elsewhere. si.name there is the pipeline's call
        # for that meeting; the console renders it as an inference, never as
        # a fact (R6.2.1 applies to admin too - a curation surface may show
        # the cluster NUMBER as an id, but not as a name).
        d["elsewhere"] = [dict(e) for e in con.execute(f"""
            SELECT DISTINCT ON (u.video_id)
                   u.video_id, u.start, LEFT(u.text, 160) AS text,
                   si2.name, v2.upload_date, v2.title
            FROM utterances u
            JOIN videos v2 ON v2.id = u.video_id
            LEFT JOIN speaker_identity si2 ON si2.video_id = u.video_id
                                          AND si2.local_label = u.local_label
            WHERE u.cluster = %s AND u.video_id <> %s AND {SUBSTANTIVE}
            ORDER BY u.video_id, LENGTH(u.text) DESC""",
            (r["cluster"], video_id)).fetchall()[:4]] if r["cluster"] is not None else []
        voices.append(d)

    roster = [dict(r) for r in con.execute("""
        SELECT p.surname, p.full_name, r.office, r.district
        FROM meeting_roster r JOIN people p ON p.id = r.person_id
        WHERE r.meeting_id = %s ORDER BY p.surname""", (v["meeting_id"],))] \
        if v["meeting_id"] else []

    overrides = [dict(r) for r in con.execute("""
        SELECT id, start_idx, end_idx, action, name, note, author, status,
               created_at
        FROM speaker_override WHERE video_id = %s
        ORDER BY created_at DESC""", (video_id,))]

    return {"video": dict(v), "voices": voices, "roster": roster,
            "overrides": overrides}


# ------------------------------------------------------------------ writes
class AdminError(Exception):
    pass


def canonical_name(con, video_id, name):
    """Board members are stored by SURNAME, and every guard keys on it.

    "Mike Wells" does not join people.surname = 'Wells', so a full-name label
    bypasses the roster guard AND the split-voice review check, and search
    holds two speakers where there is one. Observed in practice: an operator
    picked "Mike Wells" from the named-in-this-meeting candidates. If a
    supplied name exactly matches the full name of someone seated at THIS
    meeting, store the surname and say so - the one case this gets wrong is a
    member of the public sharing a seated member's exact full name, which the
    returned message makes visible and undo makes cheap.
    """
    if not name:
        return name, None
    r = con.execute("""
        SELECT p.surname FROM videos v
        JOIN meeting_roster mr ON mr.meeting_id = v.meeting_id
        JOIN people p ON p.id = mr.person_id
        WHERE v.id = %s AND lower(p.full_name) = lower(%s)
          AND lower(p.surname) <> lower(%s)""",
        (video_id, name, name)).fetchone()
    if not r:
        return name, None
    return r[0], (f'Stored as "{r[0]}": board members go by surname, and '
                  f'"{name}" is {r[0]}\'s full name on that day\'s roster.')


def _range_rows(con, video_id, lo, hi):
    return [dict(r) for r in con.execute("""
        SELECT u.idx, u.start, us.name, us.basis, us.human, us.contested,
               LEFT(u.text, 120) AS text
        FROM utterances u
        JOIN utterance_speaker us ON us.video_id = u.video_id AND us.idx = u.idx
        WHERE u.video_id = %s AND u.idx BETWEEN %s AND %s
        ORDER BY u.idx""", (video_id, lo, hi))]


def _refresh(con, video_id):
    """A correction must reach the index or search keeps the old name
    (gotcha 46). Failure here never looks like a failed correction: the
    override is committed and authoritative either way.

    TWO STEPS NOW, AND THE ORDER IS THE POINT. Resolution is being moved from
    a view to a materialised table (SPEAKER_PLAN.md), and the day that lands,
    a correction stops reaching the reader on its own: it writes the row it
    always wrote, and the page keeps showing the old name until something
    recomputes. So the resolution for this recording is refreshed FIRST, and
    only then are its passages re-rendered and re-embedded - because the index
    is built from the resolution and would otherwise bake in the name the
    correction just replaced.

    It runs today, while nothing reads the shadow tables, which is deliberate:
    it keeps them accurate as corrections happen, so the cutover is a rename
    rather than a rebuild. About 3 seconds against a re-embed that is already
    the slow part.
    """
    out = {}
    try:
        import speaker_claims
        r = speaker_claims.refresh_video(con, video_id)
        out["resolved"] = r["n"]
    except Exception as e:      # noqa: BLE001 - reported, not swallowed
        # Not fatal while the shadow is a shadow. It becomes fatal the day
        # utterance_speaker reads it, and the audit check for a stale
        # materialisation is what should catch that rather than this.
        out["resolve_error"] = str(e)
    try:
        import index_passages
        out["reindexed"] = index_passages.refresh_video(con, video_id,
                                                        device=DEVICE,
                                                        verbose=False)
    except Exception as e:      # noqa: BLE001 - reported, not swallowed
        out["reindex_error"] = (f"{e}. The correction is saved; run "
                                f"bin/index_passages.py to bring search "
                                f"back in step.")
    return out


ACTIONS = {"reassign", "detach", "identify", "split"}


def correct(con, body, author="admin"):
    """R5.8.1/R5.8.2: one write, four verbs, over a contiguous idx range."""
    video_id = body.get("video_id")
    action = body.get("action")
    name = (body.get("name") or "").strip() or None
    note = (body.get("note") or "").strip() or None
    try:
        lo, hi = int(body["start_idx"]), int(body["end_idx"])
    except (KeyError, TypeError, ValueError):
        raise AdminError("start_idx and end_idx are required integers")
    if action not in ACTIONS:
        raise AdminError(f"action must be one of {sorted(ACTIONS)}")
    if (action == "detach") != (name is None):
        raise AdminError("give a name for reassign/identify/split; "
                         "none for detach")
    if hi < lo:
        raise AdminError("end_idx before start_idx")
    # The audit's override.in_range failure mode, refused at the door: a range
    # past the end of a transcript corrects nothing while looking like it did.
    ends = con.execute("""
        SELECT COUNT(*) FILTER (WHERE idx = %s),
               COUNT(*) FILTER (WHERE idx = %s)
        FROM utterances WHERE video_id = %s""",
        (lo, hi, video_id)).fetchone()
    if not (ends[0] and ends[1]):
        raise AdminError(f"utterances {lo} and {hi} do not both exist "
                         f"in {video_id}")

    name, normalized = canonical_name(con, video_id, name)
    status = "pending" if body.get("pending") else "applied"
    row = con.execute("""
        INSERT INTO speaker_override
            (video_id, start_idx, end_idx, action, name, note, author, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (video_id, lo, hi, action, name, note, author, status)).fetchone()
    con.commit()

    out = {"id": row[0], "status": status, "utterances": hi - lo + 1,
           "lines": _range_rows(con, video_id, lo, hi)}
    if normalized:
        out["normalized"] = normalized
    if status == "applied":
        out.update(_refresh(con, video_id))
    return out


def undo(con, override_id):
    r = con.execute(
        "DELETE FROM speaker_override WHERE id = %s "
        "RETURNING video_id, start_idx, end_idx, status",
        (override_id,)).fetchone()
    if not r:
        raise AdminError(f"no correction #{override_id}")
    con.commit()
    out = {"removed": override_id,
           "lines": _range_rows(con, r["video_id"], r["start_idx"],
                                r["end_idx"])}
    # A withdrawn pending proposal never reached the index; a withdrawn
    # applied one did, so the index must follow it back.
    if r["status"] == "applied":
        out.update(_refresh(con, r["video_id"]))
    return out


def decide(con, override_id, decision):
    """R9.6: accept or reject a queued proposal. Accepting makes it what the
    reader sees - and therefore what the index says."""
    if decision not in ("accept", "reject"):
        raise AdminError("decision must be accept or reject")
    r = con.execute(
        "UPDATE speaker_override SET status = %s "
        "WHERE id = %s AND status = 'pending' RETURNING video_id",
        ("applied" if decision == "accept" else "rejected",
         override_id)).fetchone()
    if not r:
        raise AdminError(f"no pending proposal #{override_id}")
    con.commit()
    out = {"id": override_id, "decision": decision}
    if decision == "accept":
        out.update(_refresh(con, r[0]))
    return out


# ------------------------------------------------- the two legacy writers
#
# Recovered from web/api.py when that module was deleted (gotcha 95) and put
# here, because `label()` and `ignore()` below are their only callers and have
# been since /admin replaced the workbench. They are unchanged in behaviour -
# same SQL, same return shapes - so a caller cannot tell the difference.
#
# `apply_label` covers assign, split and merge in one call: the only thing that
# differs is which voices the caller selected. An empty name clears the label,
# which is how a wrong one is taken back.


def _apply_label(con, members, name, note):
    """Assign `name` to the given [(video_id, local_label), ...]."""
    with con.cursor() as cur:
        if name:
            cur.executemany(
                "INSERT INTO speaker_label (video_id, local_label, name, note)"
                " VALUES (%s,%s,%s,%s)"
                " ON CONFLICT (video_id, local_label) DO UPDATE SET"
                " name=EXCLUDED.name, note=EXCLUDED.note, labeled_at=now()",
                [(v, l, name, note) for v, l in members])
            cur.executemany(
                "UPDATE speaker_identity SET name=%s, confidence=1.0"
                " WHERE video_id=%s AND local_label=%s",
                [(name, v, l) for v, l in members])
        else:
            cur.executemany(
                "DELETE FROM speaker_label"
                " WHERE video_id=%s AND local_label=%s", members)
            cur.executemany(
                "UPDATE speaker_identity SET name=NULL, confidence=NULL"
                " WHERE video_id=%s AND local_label=%s", members)
    con.commit()
    return {"name": name, "voices": len(members)}


def _ignore_voices(con, members, reason, undo):
    """Mark voices as not worth naming, or restore them."""
    with con.cursor() as cur:
        if undo:
            cur.executemany(
                "DELETE FROM speaker_ignore"
                " WHERE video_id=%s AND local_label=%s", members)
        else:
            cur.executemany(
                "INSERT INTO speaker_ignore (video_id, local_label, reason)"
                " VALUES (%s,%s,%s)"
                " ON CONFLICT (video_id, local_label) DO UPDATE SET"
                " reason=EXCLUDED.reason, at=now()",
                [(v, l, reason) for v, l in members])
    con.commit()
    return {"ignored": 0 if undo else len(members),
            "restored": len(members) if undo else 0}


def label(con, body):
    """Whole-voice grain: speaker_label, the human statement about a
    (video_id, local_label). Writes the label, then does what the workbench
    never did - reach the index."""
    members = [tuple(m) for m in body.get("members", [])]
    if not members:
        raise AdminError("members required: [[video_id, local_label], ...]")
    name = (body.get("name") or "").strip() or None
    name, normalized = canonical_name(con, members[0][0], name)
    out = _apply_label(con, members, name, body.get("note"))
    if normalized:
        out["normalized"] = normalized
    for vid in dict.fromkeys(v for v, _ in members):
        out.setdefault("refresh", {})[vid] = _refresh(con, vid)
    return out


def ignore(con, body):
    """This voice is not a person worth naming (music, noise, crosstalk).
    Changes no resolved name - speaker_ignore only removes the voice from
    the triage queues - so no re-index is needed."""
    members = [tuple(m) for m in body.get("members", [])]
    if not members:
        raise AdminError("members required: [[video_id, local_label], ...]")
    return _ignore_voices(con, members, body.get("reason"),
                          bool(body.get("undo")))


# --------------------------------------------------- re-derivation (§9.2)
# The console's "propagate my labels" job: bin/rederive.py, the free local
# part of respeak.sh, run as a detached process with its status on disk.
def _alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def rederive_status(con):
    import rederive
    st = rederive.read_status() or {"state": "never_run"}
    # A crash leaves state "running" with a dead pid; say so rather than
    # showing a job that will never finish (gotchas 50/51: the stuck thing
    # that looks like work in progress).
    if st.get("state") in ("running", "reverting") and not _alive(st.get("pid")):
        st["state"] = "died"
    since = st.get("started_at")
    st["labels_since"] = con.execute(
        "SELECT COUNT(*) FROM speaker_label WHERE labeled_at > %s",
        (since,)).fetchone()[0] if since else con.execute(
        "SELECT COUNT(*) FROM speaker_label").fetchone()[0]
    st["can_revert"] = (os.path.exists(rederive.BACKUP)
                        and st.get("state") in ("done", "failed", "died"))
    try:
        with open(rederive.LOG) as f:
            st["log_tail"] = f.readlines()[-25:]
    except OSError:
        st["log_tail"] = []
    return st


def _rederive_spawn(flag):
    import subprocess
    import rederive
    st = rederive.read_status()
    if st and st.get("state") in ("running", "reverting") and _alive(st.get("pid")):
        raise AdminError("a run is already in progress")
    subprocess.Popen(
        [os.path.join(ROOT, "emb-venv", "bin", "python"),
         os.path.join(ROOT, "bin", "rederive.py"), flag],
        cwd=ROOT, start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"started": flag}


def rederive_start():
    _refuse_if_busy(starting="rederive")
    return _rederive_spawn("--run")


def rederive_revert():
    import rederive
    if not os.path.exists(rederive.BACKUP):
        raise AdminError("no backup to revert to")
    _refuse_if_busy(starting="rederive")
    return _rederive_spawn("--revert")


# ------------------------------------------------------------- operations
# Every pipeline job the UI can run (bin/job.py), plus the ingest fleet.
# Prerequisites are MEASURED from the database and enforced HERE, not only
# greyed out in the page: a gate that lives in the client is a gate that a
# stale tab walks straight through.
#
# The console also has to answer "is it stuck?", and a running pid does not
# answer it - a wedged download holds a pid for hours. Three measurements do:
# which step is running and since when, how long since anything was WRITTEN to
# the log, and, for the fleet, which recording each worker holds. All three
# are cheap, and all three are computed here rather than guessed at in the
# page, where the clock is not even the same one.
FLEET_PATTERN = "download_worker.py|diarize_worker.py|asr_worker.py"
WORKER_KINDS = (("download_worker.py", "download"),
                ("diarize_worker.py", "diarize"),
                ("asr_worker.py", "transcribe"))


def _age(path):
    """Seconds since anything was last written to `path`, or None."""
    try:
        return max(0, round(time.time() - os.stat(path).st_mtime))
    except OSError:
        return None


def _since(iso):
    """Seconds since an ISO timestamp this server wrote, or None. Measured
    here so the page never subtracts the browser's clock from ours."""
    if not iso:
        return None
    try:
        t = datetime.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.astimezone()
    return max(0, round(datetime.datetime.now().astimezone().timestamp()
                        - t.timestamp()))


def _fleet_procs():
    """The live workers, named the way they name themselves in claimed_by."""
    import subprocess
    try:
        out = subprocess.run(["pgrep", "-a", "-f", FLEET_PATTERN],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return []
    procs = []
    for line in out.stdout.splitlines():
        pid, _, cmd = line.partition(" ")
        kind = next((k for script, k in WORKER_KINDS if script in cmd), None)
        if not kind:
            continue
        args = cmd.split()
        name = None
        if "--worker" in args:
            name = args[args.index("--worker") + 1]
        elif "--gpu" in args:
            gpu = args[args.index("--gpu") + 1]
            name = f"{'diar' if kind == 'diarize' else 'asr'}-{gpu}"
        procs.append({"name": name or kind, "kind": kind})
    return sorted(procs, key=lambda p: p["name"])


def _fleet_workers():
    return len(_fleet_procs())


def _fleet(con):
    """What the fleet is doing right now: who is up, what each one holds, and
    how long since any of them wrote a line."""
    procs = _fleet_procs()
    # `updated_at` on a CLAIMED row is the moment it was claimed - db.claim
    # writes both together, and nothing else touches the row until the worker
    # finishes or fails it. bin/audit.py reads it the same way to find claims
    # a killed worker abandoned. So: how long this worker has held this one.
    in_flight = [
        {"worker": r[0], "video_id": r[1], "title": r[2], "duration": r[3],
         "held_for": _since(r[4].isoformat() if r[4] else None)}
        for r in con.execute(
            "SELECT claimed_by, id, title, duration, updated_at FROM videos "
            "WHERE claimed_by IS NOT NULL ORDER BY claimed_by").fetchall()
    ] if procs else []
    ages = [a for a in (_age(os.path.join(ROOT, "logs", f"{p['name']}.log"))
                        for p in procs) if a is not None]
    counts = con.execute("""
        SELECT COUNT(*) AS n,
               COUNT(*) FILTER (WHERE downloaded)  AS dl,
               COUNT(*) FILTER (WHERE diarized)    AS di,
               COUNT(*) FILTER (WHERE transcribed) AS tr,
               COUNT(*) FILTER (WHERE error IS NOT NULL) AS err
        FROM videos""").fetchone()
    return {
        "workers": procs,
        "in_flight": in_flight,
        "log_age": min(ages) if ages else None,
        "counts": {"total": counts[0], "downloaded": counts[1],
                   "diarized": counts[2], "transcribed": counts[3],
                   "errors": counts[4]},
    }


def _running_job():
    """Whoever holds the one-at-a-time lock, or None."""
    import job
    import rederive
    st = job.read_status()
    if st and st.get("state") == "running" and _alive(st.get("pid")):
        return f"job {st['job']}"
    rst = rederive.read_status()
    if rst and rst.get("state") in ("running", "reverting") and _alive(rst.get("pid")):
        return "the label propagation"
    return None


def _running_kind():
    """Which of the two lock holders it is, for a page that offers Stop on
    one of them and a link to the other's panel."""
    holder = _running_job()
    if not holder:
        return None
    return "job" if holder.startswith("job ") else "rederive"


def _refuse_if_busy(starting):
    holder = _running_job()
    if holder:
        raise AdminError(f"{holder} is running; one job at a time")
    if starting != "fleet" and _fleet_workers():
        raise AdminError("the ingest fleet is working; let it drain before "
                         "running anything that shares the GPUs")


def _gates(con):
    pending = con.execute("""
        SELECT COUNT(*) FILTER (WHERE NOT downloaded) AS to_download,
               COUNT(*) FILTER (WHERE downloaded AND NOT diarized) AS to_diarize,
               COUNT(*) FILTER (WHERE diarized AND NOT transcribed) AS to_transcribe
        FROM videos WHERE error IS NULL""").fetchone()
    fold = con.execute("""
        SELECT COUNT(*) FROM videos v
        WHERE v.transcribed AND v.upload_date IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM segments s WHERE s.video_id = v.id)
    """).fetchone()[0]
    unnamed = con.execute(
        "SELECT COUNT(*) FROM speaker_identity WHERE name IS NULL"
    ).fetchone()[0]
    # What is waiting for the DISCOVER steps, which had no measurement at all
    # and so said only "always safe to run".
    #
    # For the portal that is the meetings already on the county's calendar
    # with no agenda landed yet: the county posts an agenda days before each
    # one, so this is the work the next sweep collects. Deliberately NOT the
    # 757 past meetings with no agenda - re-running collects none of those,
    # because the county either never posted one or posted an image-only scan
    # this archive cannot read. A number a run cannot consume is not a
    # backlog, it is a coverage fact wearing a backlog's clothes.
    portal = con.execute("""
        SELECT COUNT(*) AS upcoming,
               COUNT(*) FILTER (
                 WHERE NOT EXISTS (SELECT 1 FROM agenda_items ai
                                   WHERE ai.meeting_id = m.id
                                     AND ai.source = 'agenda')) AS no_agenda
          FROM meetings m
         WHERE m.date > to_char(now(), 'YYYY-MM-DD')""").fetchone()
    # The channel sweep cannot know what is new until it looks, so there is no
    # honest pending count for it. What it CAN say is how much it holds, and
    # how much of that it could not place on a meeting.
    catalog = con.execute("""
        SELECT COUNT(*) AS videos,
               COUNT(*) FILTER (WHERE meeting_id IS NULL) AS unplaced
          FROM videos""").fetchone()
    # Labels written since the propagation last ran - the waiting work for the
    # one step on the page that had a number available and showed none. Same
    # measurement rederive_status() reports to the queues page; taken here too
    # so the operations page can state every step's backlog without the client
    # having to join two endpoints to find one of them.
    import rederive
    rst = rederive.read_status() or {}
    since = rst.get("started_at")
    labels = con.execute(
        "SELECT COUNT(*) FROM speaker_label WHERE labeled_at > %s", (since,)
    ).fetchone()[0] if since else con.execute(
        "SELECT COUNT(*) FROM speaker_label").fetchone()[0]
    return {
        # Positional: iterating a db.Row yields column NAMES (gotcha 13).
        "ingest_pending": {"to_download": pending[0], "to_diarize": pending[1],
                           "to_transcribe": pending[2],
                           "total": (pending[0] or 0) + (pending[1] or 0)
                                    + (pending[2] or 0)},
        "fold_pending": fold,
        "unnamed_voices": unnamed,
        "portal": {"upcoming": portal[0], "no_agenda": portal[1]},
        "catalog": {"videos": catalog[0], "unplaced": catalog[1]},
        "labels_pending": labels,
        "llm_key": bool(os.environ.get("LLM_API_KEY")),
    }


PHASE = re.compile(r"^=== (?!job:)(.+?) ===\s*$")


def _log_phase(lines):
    """The last banner the CURRENT step printed for itself - catch_up.sh
    announcing `=== segment (incremental) ===`. This is sub-step progress
    inside a step that runs for half an hour, which is where "is it stuck" is
    really asked. The scan stops at bin/job.py's own `job:` banner, so a
    banner from the previous step can never be reported as this one's."""
    for line in reversed(lines):
        if line.startswith("=== job:"):
            return None
        m = PHASE.match(line)
        if m:
            return m.group(1).strip()
    return None


def ops_status(con):
    import job
    g = _gates(con)
    st = job.read_status()
    if st and st.get("state") == "running" and not _alive(st.get("pid")):
        st["state"] = "died"
    try:
        with open(job.LOG) as f:
            log_tail = f.readlines()[-200:]
    except OSError:
        log_tail = []
    running = _running_job()
    # Only when THIS status file is the live one: rederive can hold the lock,
    # and then the newest job.json is a finished run whose start time means
    # nothing about what is happening now.
    live = bool(st and st.get("state") == "running" and running)
    return {
        "jobs": {name: {"title": j["title"], "why": j["why"],
                        "paid": j["paid"], "steps": job.plan(name)}
                 for name, j in job.JOBS.items()},
        "last": st,
        "log_tail": log_tail,
        "log_age": _age(job.LOG),
        "log_phase": _log_phase(log_tail),
        "running": running,
        "running_kind": _running_kind(),
        # Elapsed is measured HERE, against the clock that wrote the
        # timestamps. The page ticks its own second hand from these.
        "elapsed": _since(st.get("started_at")) if live else None,
        "step_elapsed": _since(st.get("step_started_at")) if live else None,
        "fleet_workers": _fleet_workers(),
        "fleet": _fleet(con),
        "gates": g,
    }


def job_stop():
    """Stop the running job: signal its whole process group, because the step
    doing the work is a CHILD of bin/job.py and a dead parent alone leaves the
    real work running. The pid is checked against /proc first - a status file
    can outlive its process, and a recycled pid is somebody else's."""
    import job
    import signal
    st = job.read_status()
    if not (st and st.get("state") == "running" and _alive(st.get("pid"))):
        raise AdminError("no job is running")
    pid = int(st["pid"])
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read().decode("utf-8", "replace")
    except OSError:
        cmdline = ""
    if "bin/job.py" not in cmdline:
        raise AdminError("the running job's process is gone; reload the page")
    try:
        pgid = os.getpgid(pid)
    except OSError:
        raise AdminError("the running job's process is gone; reload the page")
    os.killpg(pgid, signal.SIGTERM)
    for _ in range(20):                      # 2s to leave cleanly
        if not _alive(pid):
            break
        time.sleep(0.1)
    else:
        os.killpg(pgid, signal.SIGKILL)
    # The job cannot write its own ending now, so write it here. "stopped",
    # not "failed": a person stopped it, and that is a different fact.
    st = job.read_status() or st
    st.update(state="stopped", step_started_at=None,
              finished_at=datetime.datetime.now().astimezone()
              .isoformat(timespec="seconds"))
    job.write_status(st)
    return {"stopped": st.get("job")}


def job_start(con, name, paid_ok=False):
    import job
    import subprocess
    if name == "fleet":
        g = _gates(con)
        if not g["ingest_pending"]["total"]:
            raise AdminError("nothing is pending ingest - run the video "
                             "sweep first, and only start the fleet when it "
                             "finds something")
        _refuse_if_busy(starting="fleet")
        subprocess.Popen(["bash", os.path.join(ROOT, "bin", "run.sh")],
                         cwd=ROOT, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"started": "fleet"}
    if name not in job.JOBS:
        raise AdminError(f"unknown job {name!r}")
    j = job.JOBS[name]
    if j["paid"] and not paid_ok:
        raise AdminError("this job calls the paid model; confirm the spend "
                         "to run it")
    g = _gates(con)
    if name == "fold_in" and not g["fold_pending"]:
        raise AdminError("every transcribed recording is already folded into "
                         "the archive - nothing to do")
    if name in ("fold_in", "name_chain") and not g["llm_key"]:
        raise AdminError("the server holds no LLM_API_KEY - restart it with "
                         "env.local.sh sourced")
    _refuse_if_busy(starting=name)
    subprocess.Popen(
        [os.path.join(ROOT, "emb-venv", "bin", "python"),
         os.path.join(ROOT, "bin", "job.py"), name],
        cwd=ROOT, start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"started": name}


# --------------------------------------------------------------- redaction
#
# The queue for members of the public's home addresses (D3). `bin/redact.py`
# proposes them with a model and applies them safely; what it had no way to do
# was let a person LOOK before 3,439 edits land on the public record.
#
# Why a person has to be in this loop at all, when the classifier is good: the
# two ways it can be wrong are not symmetrical, and neither shows up in a count.
# Redacting too little exposes somebody's home. Redacting too much deletes the
# substance of a land-use hearing - "access to the site is from Clinton Avenue"
# is the matter under discussion, not a residence - and that damage is silent,
# because a reader cannot miss what is no longer there. So the queue shows the
# whole line with the span marked inside it, and the line before, because
# "3877 Alder Creek Loop" alone does not say which of the two it is and the
# sentence around it usually does.


def redactions(con, status="proposed", limit=50, offset=0, video=None):
    """One page of the queue, with enough around each span to judge it."""
    limit = max(1, min(int(limit), 200))
    where, args = ["r.status = %s"], [status]
    if video:
        where.append("r.video_id = %s")
        args.append(video)
    clause = " AND ".join(where)

    total = con.execute(
        f"SELECT COUNT(*) FROM redaction r WHERE {clause}", args).fetchone()[0]
    rows = con.execute(f"""
        SELECT r.id, r.video_id, r.idx, r.span, r.kind, r.status, r.author,
               COALESCE(u.text_raw, u.text)     AS text,
               u.start, u.speaker,
               COALESCE(prev.text_raw, prev.text) AS prev_text,
               v.title, v.meeting_id,
               mt.date                          AS meeting_date,
               mt.body                          AS meeting_body,
               p.phase
          FROM redaction r
          JOIN utterances u
            ON u.video_id = r.video_id AND u.idx = r.idx
          LEFT JOIN utterances prev
            ON prev.video_id = r.video_id AND prev.idx = r.idx - 1
          JOIN videos v ON v.id = r.video_id
          LEFT JOIN meetings mt ON mt.id = v.meeting_id
          LEFT JOIN passages p
            ON p.video_id = r.video_id
           AND p.start_idx <= r.idx AND p.end_idx >= r.idx
         WHERE {clause}
         ORDER BY r.video_id, r.idx, r.id
         LIMIT %s OFFSET %s""", args + [limit, int(offset)]).fetchall()

    out = []
    for r in rows:                                      # positional: gotcha 13
        # Indexed, never unpacked: db.Row is a Mapping, so tuple(r) hands back
        # the COLUMN NAMES and every field below silently becomes a string
        # (gotcha 13, which this walked into on the first run).
        rid, video_id, idx, span, kind, st, author = (
            r[0], r[1], r[2], r[3], r[4], r[5], r[6])
        text, start, speaker, prev_text = r[7], r[8], r[9], r[10]
        title, meeting_id, mdate, mbody, phase = (
            r[11], r[12], r[13], r[14], r[15])
        # Read from text_raw, not the published text. For a proposed row the two
        # are identical; for an applied one `text` is already the marker, and a
        # reviewer looking back at what was removed would be shown the removal
        # rather than the thing removed.
        #
        # The span's position is sent, not just the span: the UI marks it in
        # place rather than searching the text again and risking a different
        # answer for a line that says the address twice.
        at = text.find(span) if text and span else -1
        out.append({
            "id": rid, "video_id": video_id, "idx": idx, "span": span,
            "kind": kind, "status": st, "author": author,
            "text": text, "at": at, "start": start, "speaker": speaker,
            "prev_text": prev_text, "phase": phase,
            "video_title": title, "meeting_id": meeting_id,
            "meeting_date": mdate, "meeting_body": mbody,
            # Where a reviewer goes to hear it said, which is the only way to
            # settle an ambiguous one.
            "href": (f"/meeting/{meeting_id}?v={video_id}&t={int(start)}"
                     if meeting_id and start is not None else None),
        })

    counts = {r[0]: r[1] for r in con.execute(
        "SELECT status, COUNT(*) FROM redaction GROUP BY status").fetchall()}
    return {"total": total, "limit": limit, "offset": int(offset),
            "rows": out, "counts": counts}


def redaction_decide(con, body):
    """Accept or reject specific proposals, synchronously.

    Accepting re-indexes each affected recording, so this is for the handful a
    reviewer works through by hand - a page of fifty spanning fifty recordings
    would sit here for three minutes. The whole queue goes through the job
    below instead.
    """
    import redact
    ids = [int(i) for i in body.get("ids", [])]
    if not ids:
        raise AdminError("ids required")
    decision = (body.get("decision") or "").strip()
    if decision == "reject":
        with con.cursor() as cur:
            cur.execute("UPDATE redaction SET status = 'rejected'"
                        " WHERE id = ANY(%s) AND status = 'proposed'", (ids,))
            n = cur.rowcount
        con.commit()
        return {"rejected": n}
    if decision == "accept":
        # Never in the request. Applying re-indexes every recording the batch
        # touches, and a rebuild of one runs seconds - so even a single row can
        # outlive a browser's patience, and 25 rows across 4 recordings did
        # exactly that: the status flip committed, the re-index died with it,
        # and the archive was left with the address gone from the transcript
        # and still in the search index. The job owns the whole operation now,
        # and the page follows it.
        return _spawn_redact_job(con, ids)
    if decision == "revert":
        return {"reverted": redact.revert(con, ids)}
    if decision == "reconsider":
        # Back into the queue undecided. A rejected row cannot go straight to
        # applied - apply() only ever reads 'proposed' - and that is the right
        # shape: undoing a decision is not the same act as making the opposite
        # one, and the queue is where decisions get made.
        with con.cursor() as cur:
            cur.execute("UPDATE redaction SET status = 'proposed'"
                        " WHERE id = ANY(%s) AND status = 'rejected'", (ids,))
            n = cur.rowcount
        con.commit()
        return {"reconsidering": n}
    raise AdminError("decision must be accept, reject, revert or reconsider")


def redaction_job_status(con):
    """Progress of a bulk apply, in the shape the rederive panel uses."""
    import redact_job
    st = redact_job.read_status() or {"state": "never_run"}
    if st.get("state") == "running" and not _alive(st.get("pid")):
        st["state"] = "died"
    st["proposed"] = con.execute(
        "SELECT COUNT(*) FROM redaction WHERE status = 'proposed'").fetchone()[0]
    try:
        with open(redact_job.LOG) as f:
            st["log_tail"] = f.readlines()[-20:]
    except OSError:
        st["log_tail"] = []
    return st


def _spawn_redact_job(con, ids=None):
    """Start the detached runner, over `ids` or over everything proposed."""
    import redact_job
    import subprocess
    st = redact_job.read_status()
    if st and st.get("state") == "running" and _alive(st.get("pid")):
        raise AdminError("a redaction run is already in progress")
    if ids:
        n = con.execute("SELECT COUNT(*) FROM redaction"
                        " WHERE id = ANY(%s) AND status = 'proposed'",
                        (list(ids),)).fetchone()[0]
        if not n:
            raise AdminError("none of those are still proposed")
    else:
        n = con.execute("SELECT COUNT(*) FROM redaction"
                        " WHERE status = 'proposed'").fetchone()[0]
        if not n:
            raise AdminError("nothing is proposed - the queue is empty")
    _refuse_if_busy(starting="redact")
    cmd = [os.path.join(ROOT, "emb-venv", "bin", "python"),
           os.path.join(ROOT, "bin", "redact_job.py"), "--apply-all"]
    if ids:
        cmd += ["--ids", *[str(int(i)) for i in ids]]
    subprocess.Popen(cmd, cwd=ROOT, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"started": "redact", "proposals": n}


def redaction_apply_all(con, body=None):
    """Spawn the detached runner over every remaining proposal."""
    return _spawn_redact_job(con)
