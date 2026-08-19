"""Catalog, work queue and search index for the meeting archive, on Postgres.

  claim()  is a single UPDATE ... FROM (SELECT ... FOR UPDATE SKIP LOCKED).
           SQLite needed BEGIN IMMEDIATE and workers serialised behind the
           write lock; here they never block each other, and a worker that
           dies mid-claim releases its row when its transaction rolls back.
  Row      supports both r[0] and r["col"], the way sqlite3.Row did. psycopg's
           dict_row would have forced hundreds of unrelated edits across the
           pipeline for no benefit."""
import os
from collections.abc import Mapping

import psycopg
from pgvector.psycopg import register_vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SCHEMA_SQL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
BM25_SQL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bm25.sql")


class MissingConfig(RuntimeError):
    """Required configuration is absent from the environment."""


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
    """Release rows this worker name still holds from a previous life."""
    n = con.execute("UPDATE videos SET claimed_by = NULL WHERE claimed_by = %s",
                    (worker,)).rowcount
    con.commit()
    if n:
        print(f"[{worker}] reclaimed {n} row(s) abandoned by a previous run",
              flush=True)
    return n


def work_remaining(con, stage):
    """Count videos that could still reach `stage`, including in-flight ones."""
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
    """Record a stage failure. Returns True if the video was retired."""
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
    """Send a video back to an earlier stage because its inputs are missing."""
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
    """Replace all stored utterances for a video."""
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
