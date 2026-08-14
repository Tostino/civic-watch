"""Catalog, work queue and search index for the meeting archive, on Postgres.

One database now holds what used to be three stores: the SQLite catalog, an
FTS5 index, and a 257 MB passages.npy that had to be read into RAM whole and
scanned in full for every query. Vectors live next to the rows they describe,
under an HNSW index.

Two things here are deliberately not a straight transcription of the SQLite
version:

  claim()  is a single UPDATE ... FROM (SELECT ... FOR UPDATE SKIP LOCKED).
           SQLite needed BEGIN IMMEDIATE and workers serialised behind the
           write lock; here they never block each other, and a worker that
           dies mid-claim releases its row when its transaction rolls back.
  Row      supports both r[0] and r["col"], the way sqlite3.Row did. psycopg's
           dict_row would have forced hundreds of unrelated edits across the
           pipeline for no benefit.

The connection string lives in ./env.local.sh, which is gitignored and never
read from source. Nothing in this repository contains a password.
"""
import os
from collections.abc import Mapping

import psycopg
from pgvector.psycopg import register_vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SCHEMA_SQL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
BM25_SQL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bm25.sql")


class MissingConfig(RuntimeError):
    """Required configuration is absent from the environment.

    RuntimeError and not SystemExit, for the reason ask.MissingKey documents
    at length: SystemExit is not an Exception, so a server's `except
    Exception` cannot catch it, and the request thread dies without answering.
    This module is imported by web/server.py on every request.
    """


def dsn():
    d = os.environ.get("PASCO_DSN")
    if not d:
        raise MissingConfig(
            "PASCO_DSN is not set. Source the local env file first:\n"
            "  source ./env.local.sh")
    return d


class Row(Mapping):
    """A result row addressable by position or by name, like sqlite3.Row."""
    __slots__ = ("_cols", "_vals")

    def __init__(self, cols, vals):
        self._cols, self._vals = cols, vals

    def __getitem__(self, k):
        if isinstance(k, int):
            return self._vals[k]
        try:
            return self._vals[self._cols.index(k)]
        except ValueError:
            raise KeyError(k) from None

    # Mapping over the column names, so dict(row) yields {name: value}.
    def __iter__(self):
        return iter(self._cols)

    def __len__(self):
        return len(self._cols)

    def keys(self):
        return list(self._cols)

    def __repr__(self):
        return f"Row({dict(self)!r})"


def row_factory(cursor):
    cols = [c.name for c in cursor.description] if cursor.description else []
    return lambda vals: Row(cols, vals)


def connect(autocommit=False):
    con = psycopg.connect(dsn(), autocommit=autocommit, row_factory=row_factory)
    register_vector(con)
    # The cluster default of 4.0 assumes spinning disks. At that cost the
    # planner priced the HNSW index above a parallel sequential scan and never
    # used it - 128 ms per vector search instead of 10, with nothing in the
    # results to reveal it. Session-scoped, so no other database is affected.
    con.execute("SET random_page_cost = 1.1")
    if not autocommit:
        con.commit()
    return con


def init():
    """Apply the schema. Safe to re-run - every statement is IF NOT EXISTS or
    CREATE OR REPLACE."""
    con = connect(autocommit=True)
    for path in (SCHEMA_SQL, BM25_SQL):
        with open(path) as f:
            con.execute(f.read())
    return con


def video_dir(video_id, create=False):
    d = os.path.join(DATA, video_id)
    if create:
        os.makedirs(d, exist_ok=True)
    return d


# What must already be true for a stage to be claimable.
PREREQ = {"download": "NOT downloaded",
          "diarize": "downloaded AND NOT diarized",
          "asr": "diarized AND NOT transcribed"}
FLAG = {"download": "downloaded", "diarize": "diarized", "asr": "transcribed"}


def claim(con, stage, worker):
    """Atomically take the next video needing `stage`. Returns a row or None.

    Newest first, so the index becomes useful early and recent meetings - the
    ones most likely to be searched - land before the historical backlog.

    SKIP LOCKED is what lets the fleet scale: a worker looking for work steps
    over rows another worker is already claiming instead of queueing behind it.
    """
    row = con.execute(f"""
        UPDATE videos SET claimed_by = %s, updated_at = now() WHERE id = (
            SELECT id FROM videos
            WHERE {PREREQ[stage]} AND claimed_by IS NULL AND error IS NULL
            ORDER BY upload_date IS NULL, upload_date DESC, duration DESC
            LIMIT 1 FOR UPDATE SKIP LOCKED)
        RETURNING *""", (worker,)).fetchone()
    con.commit()
    return row


def reclaim(con, worker):
    """Release rows this worker name still holds from a previous life.

    `claim` only ever considers `claimed_by IS NULL`, so a worker killed
    mid-item leaves that video claimed by a process that no longer exists and
    NOTHING will ever pick it up again - it is not an error, it is not pending,
    it simply stops existing as far as the queue is concerned. A crash that
    took down the fleet left three videos in exactly that state.

    Reclaiming by worker NAME rather than by age is what makes this safe: the
    names are fixed (dl-0, diar-1, asr-0) and run.sh refuses to start a second
    worker under a live name, so any row still carrying this name is by
    definition abandoned. An age-based sweep would race a healthy worker that
    is simply slow - a four-hour meeting is not a hung one.
    """
    n = con.execute("UPDATE videos SET claimed_by = NULL WHERE claimed_by = %s",
                    (worker,)).rowcount
    con.commit()
    if n:
        print(f"[{worker}] reclaimed {n} row(s) abandoned by a previous run",
              flush=True)
    return n


def work_remaining(con, stage):
    """Count videos that could still reach `stage`, including in-flight ones.

    Lets a worker distinguish "nothing to do yet, upstream is still working"
    from "the run is finished" - without this an ASR worker exits immediately,
    before diarization has produced anything.
    """
    n = con.execute(
        f"SELECT COUNT(*) FROM videos WHERE error IS NULL AND NOT {FLAG[stage]}"
    ).fetchone()[0]
    # Callers poll this in a loop and then sleep. Leaving the read's implicit
    # transaction open would pin locks on `videos` for the whole nap.
    con.commit()
    return n


def release(con, video_id, **fields):
    sets = ", ".join(f"{k} = %s" for k in fields)
    con.execute(
        f"UPDATE videos SET {sets}, claimed_by = NULL, updated_at = now() "
        f"WHERE id = %s", list(fields.values()) + [video_id])
    con.commit()


# YouTube says these when a video really is gone. Everything else - network
# blips, throttling, a player that momentarily needs JS deciphering - is
# transient and worth another attempt later.
PERMANENT = ("video unavailable", "private video", "removed by the uploader",
             "members-only", "account associated with this video has been "
             "terminated", "video has been removed", "not available in your "
             "country", "sign in to confirm your age")
MAX_ATTEMPTS = 5


def fail(con, video_id, message, max_attempts=MAX_ATTEMPTS):
    """Record a stage failure. Returns True if the video was retired.

    A transient failure only bumps the attempt counter and releases the claim,
    so the video returns to the queue. `error` is written - which removes it
    from the queue for good - only when the message says the video is actually
    gone, or when it has failed too many times to be worth another slot.
    """
    msg = (message or "")[:500]
    permanent = any(p in msg.lower() for p in PERMANENT)
    n = con.execute(
        "UPDATE videos SET attempts = attempts + 1 WHERE id = %s RETURNING attempts",
        (video_id,)).fetchone()[0]
    retire = permanent or n >= max_attempts
    con.execute(
        "UPDATE videos SET error = %s, claimed_by = NULL, updated_at = now() "
        "WHERE id = %s", (msg if retire else None, video_id))
    con.commit()
    return retire


def rewind(con, video_id, stage, message, max_attempts=MAX_ATTEMPTS):
    """Send a video back to an earlier stage because its inputs are missing.

    A GPU worker that cannot find its audio or its diarization has not hit a
    transient GPU fault - the upstream artefact is gone, and retrying the same
    stage five times will fail five times. Clearing the upstream flag puts the
    video back in front of the worker that can actually produce the file.

    Attempts are still counted, so a video whose download keeps evaporating is
    eventually retired instead of cycling between two stages forever.
    """
    col = FLAG[stage]
    n = con.execute(
        "UPDATE videos SET attempts = attempts + 1 WHERE id = %s RETURNING attempts",
        (video_id,)).fetchone()[0]
    if n >= max_attempts:
        con.execute("UPDATE videos SET error = %s, claimed_by = NULL, "
                    "updated_at = now() WHERE id = %s",
                    ((message or "")[:500], video_id))
        con.commit()
        return True
    con.execute(f"UPDATE videos SET {col} = false, claimed_by = NULL, "
                f"updated_at = now() WHERE id = %s", (video_id,))
    con.commit()
    return False


def index_video(con, video_id, utterances):
    """Replace all stored utterances for a video.

    No FTS table to keep in step: utterances.tsv is a generated column, so the
    keyword index cannot drift from the text the way an explicitly maintained
    one could.

    Both text columns are written here, with the same value, and this is the
    ONLY place `text_raw` is ever written. It is the recogniser's output;
    `text` is what the archive publishes, and after a redaction is applied the
    two differ by the addresses that came out (bin/redact.py: republish). A
    re-transcribe therefore resets both - which is correct, it is new ASR -
    and `redaction.gone_from_transcript` in the audit is what catches any
    applied redaction that a re-transcribe undid.
    """
    with con.cursor() as cur:
        cur.execute("DELETE FROM utterances WHERE video_id = %s", (video_id,))
        with cur.copy('COPY utterances (video_id, idx, start, "end", speaker, '
                      'text, text_raw) FROM STDIN') as cp:
            for i, u in enumerate(utterances):
                cp.write_row((video_id, i, u["start"], u["end"],
                              u.get("speaker"), u["text"], u["text"]))
    con.commit()


if __name__ == "__main__":
    con = init()
    n = con.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    print(f"schema applied · {n} videos")
