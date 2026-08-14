"""One-shot migration of the archive from SQLite + passages.npy into Postgres.

Run once, verify, then the SQLite file is history. It is idempotent - every
table is truncated before load - so a failed run can simply be repeated.

Three things need care and get it:

  BOOLEANS   SQLite stored 0/1 integers in columns that are genuinely flags.
             Postgres has a real boolean, so they are converted rather than
             carried over as integers, and the queue predicates read as English.
  VECTORS    passages.npy is positional: row i IS passage id i. That invariant
             is asserted, not assumed, because if it ever slipped every search
             result would be subtly and silently wrong.
  IDENTITY   segments.id came from a SQLite rowid. The identity sequence is
             advanced past the migrated maximum, or the next insert collides.
"""
import os
import sqlite3
import sys
import time

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQLITE = os.path.join(ROOT, "catalog.sqlite")
VECS = os.path.join(ROOT, "passages.npy")

# (table, columns, per-row transform). Order matters: videos first, because
# everything else references it.
BOOL = lambda v: bool(v)

TABLES = [
    ("videos",
     ["id", "title", "duration", "upload_date", "kind", "source", "downloaded",
      "diarized", "transcribed", "claimed_by", "words", "speakers",
      "gap_seconds", "error", "updated_at"],
     {"downloaded": BOOL, "diarized": BOOL, "transcribed": BOOL}),
    ("utterances",
     ["video_id", "idx", "start", "end", "speaker", "text", "cluster",
      "local_label"], {}),
    ("segments",
     ["id", "video_id", "seq", "start_idx", "end_idx", "start", "end", "phase",
      "title", "search_title", "continued"], {"continued": BOOL}),
    ("passage_keys", ["passage_id", "kind", "key"], {}),
    ("speaker_identity",
     ["video_id", "local_label", "cluster", "name", "confidence", "source"], {}),
    ("speaker_label",
     ["video_id", "local_label", "name", "note", "labeled_at"], {}),
    ("speaker_ignore", ["video_id", "local_label", "reason", "at"], {}),
]

PASSAGE_COLS = ["id", "video_id", "start", "end", "speaker", "cluster", "text",
                "search_text", "segment_id", "phase"]


def quoted(cols):
    """`end` is reserved in SQL; everything else passes through unchanged."""
    return ", ".join(f'"{c}"' if c == "end" else c for c in cols)


def copy_table(lite, pg, table, cols, casts):
    rows = lite.execute(f"SELECT {', '.join(chr(34)+c+chr(34) for c in cols)} "
                        f"FROM {table}").fetchall()
    with pg.cursor() as cur:
        cur.execute(f"TRUNCATE {table} CASCADE")
        with cur.copy(f"COPY {table} ({quoted(cols)}) FROM STDIN") as cp:
            for r in rows:
                cp.write_row([casts[c](r[c]) if c in casts and r[c] is not None
                              else r[c] for c in cols])
    return len(rows)


def copy_passages(lite, pg, vecs):
    rows = lite.execute(
        f"SELECT {', '.join(chr(34)+c+chr(34) for c in PASSAGE_COLS)} "
        f"FROM passages ORDER BY id").fetchall()
    ids = [r["id"] for r in rows]
    # Positional invariant: passages.npy row i is passage id i. If this ever
    # drifts, every vector search silently returns the wrong passages.
    assert ids == list(range(len(ids))), "passage ids are not 0..n-1 contiguous"
    assert len(vecs) == len(ids), f"{len(vecs)} vectors vs {len(ids)} passages"

    with pg.cursor() as cur:
        cur.execute("TRUNCATE passages CASCADE")
        cur.executemany(
            f"INSERT INTO passages ({quoted(PASSAGE_COLS)}, embedding) "
            f"VALUES ({', '.join(['%s'] * len(PASSAGE_COLS))}, %s)",
            [tuple(r[c] for c in PASSAGE_COLS) + (vecs[r["id"]],) for r in rows])
    return len(rows)


def main():
    if not os.path.exists(SQLITE):
        sys.exit(f"no {SQLITE}")
    dsn = os.environ.get("PASCO_DSN")
    if not dsn:
        sys.exit("PASCO_DSN not set - source ./env.local.sh first")

    lite = sqlite3.connect(SQLITE)
    lite.row_factory = sqlite3.Row
    vecs = np.load(VECS)
    print(f"{VECS}: {vecs.shape} {vecs.dtype}")

    with psycopg.connect(dsn, autocommit=False) as pg:
        register_vector(pg)
        t0 = time.time()
        for table, cols, casts in TABLES:
            n = copy_table(lite, pg, table, cols, casts)
            print(f"  {table:<18}{n:>9,}", flush=True)
        n = copy_passages(lite, pg, vecs)
        print(f"  {'passages':<18}{n:>9,}  (with embeddings)", flush=True)

        with pg.cursor() as cur:
            cur.execute("SELECT setval(pg_get_serial_sequence('segments','id'),"
                        " COALESCE((SELECT MAX(id) FROM segments), 1))")
        pg.commit()
        print(f"loaded in {time.time() - t0:.0f}s\n")

        # Index builds want room; the default 64MB makes HNSW crawl and spill.
        with pg.cursor() as cur:
            cur.execute("SET maintenance_work_mem = '2GB'")
            cur.execute("SET max_parallel_maintenance_workers = 4")
            print("building HNSW index over 1024-dim embeddings ...", flush=True)
            t0 = time.time()
            cur.execute("DROP INDEX IF EXISTS passages_embedding_hnsw")
            cur.execute("CREATE INDEX passages_embedding_hnsw ON passages "
                        "USING hnsw (embedding vector_cosine_ops) "
                        "WITH (m = 16, ef_construction = 64)")
            print(f"  built in {time.time() - t0:.0f}s", flush=True)
            print("building BM25 postings ...", flush=True)
            t0 = time.time()
            cur.execute("CALL bm25_rebuild()")
            print(f"  built in {time.time() - t0:.0f}s", flush=True)
            cur.execute("ANALYZE")
        pg.commit()

        # Verify against the source rather than trusting the loop above.
        print("\nverification (sqlite -> postgres):")
        ok = True
        for table, _, _ in TABLES + [("passages", None, None)]:
            a = lite.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            with pg.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                b = cur.fetchone()[0]
            ok &= a == b
            print(f"  {table:<18}{a:>9,} -> {b:>9,}  {'ok' if a == b else 'MISMATCH'}")
        with pg.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM passages WHERE embedding IS NULL")
            missing = cur.fetchone()[0]
            cur.execute("SELECT n_docs, avgdl FROM bm25_stats")
            n_docs, avgdl = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM passage_terms")
            postings = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM term_df")
            terms = cur.fetchone()[0]
            # Round-trip one vector to prove the transfer was lossless.
            cur.execute("SELECT embedding FROM passages WHERE id = 0")
            v = cur.fetchone()[0]
            got = np.asarray(v.to_numpy() if hasattr(v, "to_numpy") else v,
                             dtype=np.float32)
        drift = float(np.abs(got - vecs[0]).max())
        print(f"  embeddings missing {missing}")
        print(f"  bm25: {postings:,} postings over {terms:,} terms, "
              f"{n_docs:,} docs, avgdl {avgdl:.1f}")
        print(f"  vector round-trip max abs drift: {drift:.2e}")
        ok &= missing == 0 and drift < 1e-6
    lite.close()
    print("\n" + ("MIGRATION OK" if ok else "MIGRATION HAD MISMATCHES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
