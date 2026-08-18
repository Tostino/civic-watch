#!/usr/bin/env python3
"""Stand up a small, isolated copy of the archive to test the pipeline on.

`bin/rebuild.sh --yes` truncates every derived table in the real database and
builds it again. That is the right thing to do eventually and the wrong thing
to do first: it takes hours, it costs money at two stages, and if the code is
wrong you find out at the end with nothing to compare against.

So: copy a handful of meetings into their own database, run the identical
pipeline there, and check the result. Nothing in this script writes to the real
archive - it only reads.

    bin/sandbox.py --build          create it and copy the fixtures in
    bin/sandbox.py --compare        diff the derived layers against production
    bin/sandbox.py --drop           remove it

    PASCO_DSN="$(bin/sandbox.py --dsn)" bash bin/rebuild.sh --yes

WHAT IS COPIED, and it is exactly `rebuild.sh`'s KEEP list restricted to the
fixture meetings: utterances, videos, the human corrections, and the county's
documents. Everything else is what we are testing, so it starts empty.

`vec_cache` is deliberately NOT copied. It is 2.5 GB keyed by content hash, and
the fixtures need a few thousand vectors that take seconds to compute. Copying
it would make the sandbox cost more disk than the thing it is testing.

The diarization turns and voice centroids in data/*.json are read by path, so
they are shared with production automatically and need no copying at all.

THE FIXTURES are chosen to exercise the paths that break, not to be
representative. Each one is here because something specific goes wrong on it.
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db                                                    # noqa: E402

SANDBOX_DB = "pasco_sandbox"

# meeting_id -> why it is in the set. If a fixture ever stops being the case it
# describes, the note is how you find that out.
FIXTURES = {
    701:  "2026-01-06 BCC — the full path: 139 published items, 138 disposed "
          "of, two recordings, and FOUR items with a nay vote in the minutes",
    428:  "2026-08-11 BCC — a recording with NO published agenda, so every "
          "item is transcript-derived. Also carries the public-comment queue "
          "and the roll-call/answer merge that speaker_id was just fixed for",
    661:  "2026-03-24 — 86 items, all disposed of, NO recording at all: the "
          "91% case, where the published record is the only evidence",
    979:  "2026-08-06 Planning Commission — a different body, so the roster "
          "and the body guards get exercised rather than assumed",
    1040: "2026-07-14 BCC — 191 items on one day across two sessions, which "
          "is what the meeting-day digest and the session labelling are for",
    220:  "2020-06-04 Planning Commission — the county reuses PC1..PC5 in BOTH "
          "the Consent and the Public Hearings sections, so (meeting_id, code) "
          "is not unique. This is the meeting the idempotency audit proved "
          "parse_minutes non-deterministic on by toggling enable_indexscan",
    27:   "2019-08-06 BCC — C1 is both a Consent resolution and a Public "
          "Hearings rezoning. 157 items, minutes, and NO recording: the "
          "duplicate-code case with nothing else going on to hide it",
    712:  "2020-08-19 BCC — carries the one human speaker_ignore row (video "
          "T-fN-fVcYJM / SPEAKER_10, 'not a person'), which is what makes the "
          "retraction path in speaker_id testable at all",
}


def dsn_for(name):
    """The production DSN with the database name swapped."""
    d = db.dsn()
    for sep in (" dbname=", "dbname="):
        if sep in d:
            head, _, tail = d.partition(sep)
            rest = tail.split(" ", 1)
            return f"{head}{sep}{name}" + (f" {rest[1]}" if len(rest) > 1 else "")
    # URL form: postgresql://user:pw@host/db
    return d.rsplit("/", 1)[0] + "/" + name


def admin():
    """A connection to `postgres`, for CREATE/DROP DATABASE."""
    import psycopg
    return psycopg.connect(dsn_for("postgres"), autocommit=True)


def build():
    src = db.connect()
    ids = sorted(FIXTURES)
    vids = [r[0] for r in src.execute(
        "SELECT id FROM videos WHERE meeting_id = ANY(%s) ORDER BY id", (ids,))]
    events = [r[0] for r in src.execute(
        "SELECT id FROM portal_events WHERE meeting_id = ANY(%s)", (ids,))]
    print(f"fixtures: {len(ids)} meetings, {len(vids)} videos, "
          f"{len(events)} portal events")
    for m, why in FIXTURES.items():
        print(f"  {m:5d}  {why}")
    if not vids:
        sys.exit("no videos for those meetings — is this the right database?")

    with admin() as a:
        a.execute(f'DROP DATABASE IF EXISTS "{SANDBOX_DB}"')
        a.execute(f'CREATE DATABASE "{SANDBOX_DB}"')
    print(f"\ncreated database {SANDBOX_DB}")

    dst = db.connect(dsn=dsn_for(SANDBOX_DB)) if _takes_dsn() else _connect_to(
        dsn_for(SANDBOX_DB))
    for path in (db.SCHEMA_SQL, db.BM25_SQL):
        with open(path) as f:
            dst.execute(f.read())
        print(f"  applied {os.path.basename(path)}")

    # Only rebuild.sh's KEEP list, restricted to the fixtures. Everything the
    # pipeline derives starts empty, which is the point.
    COPY = [
        ("videos", "WHERE id = ANY(%(v)s)"),
        ("utterances", "WHERE video_id = ANY(%(v)s)"),
        ("speaker_label", "WHERE video_id = ANY(%(v)s)"),
        ("speaker_ignore", "WHERE video_id = ANY(%(v)s)"),
        ("speaker_override", "WHERE video_id = ANY(%(v)s)"),
        # Without this the sandbox holds redacted transcripts and no rows
        # saying so, which leaves audit.py's three redaction invariants
        # examining nothing and reporting EMPTY - a check that proves nothing
        # while looking like one that passed.
        ("redaction", "WHERE video_id = ANY(%(v)s)"),
        ("portal_events", "WHERE id = ANY(%(e)s)"),
        ("portal_files", "WHERE event_id = ANY(%(e)s)"),
    ]
    # The curated subject vocabulary is KEEP too, and it is NOT scoped to the
    # fixtures - it is archive-wide wording that a person kept. It is copied
    # below rather than here because `subject.parent` references `subject`,
    # so the rows have to arrive parents-first and this loop has no way to
    # say that. `speaker_method` needs no copy at all: schema.sql seeds it,
    # which is exactly why KEEP protects a tuned rank rather than the table.
    # `answers` is deliberately not copied - the sandbox rebuilds agenda item
    # ids from nothing, which is the very hazard rebuild.sh now refuses on.
    args = {"v": vids, "e": events}
    print()
    for table, where in COPY:
        # Generated columns are excluded: `utterances.tsv` is a stored
        # tsvector, and Postgres rejects an INSERT that names it at all. It is
        # recomputed from the text on arrival anyway, so there is nothing to
        # carry over.
        cols = [r[0] for r in src.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s "
            "AND is_generated = 'NEVER' "
            "ORDER BY ordinal_position", (table,))]
        # Quoted, because utterances and item_spans both have a column called
        # `end`, which is a reserved word and a syntax error unquoted.
        q = ", ".join(f'"{c}"' for c in cols)
        rows = src.execute(
            f"SELECT {q} FROM {table} {where}", args).fetchall()
        if rows:
            # `meeting_id` on videos and portal_events is DERIVED by
            # land_agenda. Dropping it is what makes this a test of the
            # pipeline rather than a test that a copy is a copy - and it has to
            # happen HERE, not in an UPDATE afterwards, because `meetings` is
            # derived too, so it does not exist yet and the foreign key refuses
            # the insert.
            blank = (cols.index("meeting_id")
                     if table in ("videos", "portal_events")
                     and "meeting_id" in cols else None)
            ph = ", ".join(["%s"] * len(cols))
            with dst.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO {table} ({q}) VALUES ({ph}) "
                    f"ON CONFLICT DO NOTHING",
                    [tuple(_bind(r[i], i == blank) for i in range(len(cols)))
                     for r in rows])
        print(f"  {table:20s}{len(rows):>10,d}"
              + ("   (meeting_id cleared - land_agenda re-derives it)"
                 if table in ("videos", "portal_events") else ""))

    # ---- the subject vocabulary, parents before children ----------------
    # Two passes rather than a topological sort: insert every row with no
    # parent, then restore the parents once every slug exists. Three levels
    # today, any number tomorrow, and no ordering to get wrong.
    srows = src.execute(
        "SELECT slug, label, q, blurb, status, proposer, sort, created_at, parent"
        "  FROM subject").fetchall()
    with dst.cursor() as cur:
        cur.executemany(
            "INSERT INTO subject (slug,label,q,blurb,status,proposer,sort,"
            "created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            [tuple(r[i] for i in range(8)) for r in srows])
        cur.executemany("UPDATE subject SET parent=%s WHERE slug=%s",
                        [(r[8], r[0]) for r in srows if r[8]])
    tcols = [r[0] for r in src.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name='subject_term'"
        " AND is_generated='NEVER' ORDER BY ordinal_position")]
    tq = ", ".join(f'"{c}"' for c in tcols)
    trows = src.execute(f"SELECT {tq} FROM subject_term").fetchall()
    with dst.cursor() as cur:
        cur.executemany(
            f"INSERT INTO subject_term ({tq}) VALUES "
            f"({', '.join(['%s']*len(tcols))}) ON CONFLICT DO NOTHING",
            [tuple(r[i] for i in range(len(tcols))) for r in trows])
    print(f"  {'subject':20s}{len(srows):>10,d}   (parents restored in a second pass)")
    print(f"  {'subject_term':20s}{len(trows):>10,d}")
    dst.commit()

    print(f"\nReady. Run the pipeline against it with:\n\n"
          f'    PASCO_DSN="$(bin/sandbox.py --dsn)" bash bin/rebuild.sh --yes\n')


def _bind(value, blank):
    """One value on its way into the sandbox.

    psycopg reads a jsonb column back as a dict and will not send a dict back
    without being told it is json - portal_events carries the county's raw API
    payload, so the copy fails on the first row without this.
    """
    if blank:
        return None
    if isinstance(value, (dict, list)):
        from psycopg.types.json import Jsonb
        return Jsonb(value)
    return value


def _takes_dsn():
    import inspect
    return "dsn" in inspect.signature(db.connect).parameters


def _connect_to(dsn):
    import psycopg
    from pgvector.psycopg import register_vector
    con = psycopg.connect(dsn, autocommit=True)
    try:
        con.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(con)
    except Exception:                                        # noqa: BLE001
        pass
    return con


# What "is it right" means, stated as numbers rather than left to a glance.
COMPARE = [
    ("published items", "SELECT count(*) FROM agenda_items WHERE source='agenda'"),
    ("transcript items", "SELECT count(*) FROM agenda_items WHERE source='transcript'"),
    ("items with an outcome", "SELECT count(*) FROM agenda_items WHERE outcome IS NOT NULL"),
    ("items with a nay vote",
     "SELECT count(*) FROM agenda_items WHERE outcome_text ~* 'vot(ing|ed) nay'"),
    ("cases", "SELECT count(*) FROM cases"),
    ("segments", "SELECT count(*) FROM segments"),
    ("item spans", "SELECT count(*) FROM item_spans"),
    ("passages", "SELECT count(*) FROM passages"),
    ("passages bound to an item",
     "SELECT count(*) FROM passages WHERE agenda_item_id IS NOT NULL"),
    ("named utterances",
     "SELECT count(*) FROM utterance_speaker WHERE name IS NOT NULL"),
    ("distinct speaker names",
     "SELECT count(DISTINCT name) FROM utterance_speaker WHERE name IS NOT NULL"),
    ("roster rows", "SELECT count(*) FROM meeting_roster"),
]


def compare():
    ids = sorted(FIXTURES)
    vids = [r[0] for r in db.connect().execute(
        "SELECT id FROM videos WHERE meeting_id = ANY(%s)", (ids,))]
    prod, sand = db.connect(), _connect_to(dsn_for(SANDBOX_DB))

    # Production has to be restricted to the same meetings, or every row is a
    # difference and the comparison says nothing.
    def scope(sql):
        if " FROM agenda_items" in sql:
            return sql + f" AND meeting_id = ANY(ARRAY{ids})" if " WHERE " in sql \
                else sql + f" WHERE meeting_id = ANY(ARRAY{ids})"
        if " FROM cases" in sql:
            return ("SELECT count(DISTINCT case_id) FROM agenda_items "
                    f"WHERE case_id IS NOT NULL AND meeting_id = ANY(ARRAY{ids})")
        for t in ("segments", "passages", "utterance_speaker"):
            if f" FROM {t}" in sql:
                return sql + (" AND " if " WHERE " in sql else " WHERE ") + \
                    f"video_id = ANY(ARRAY{vids}::text[])"
        if " FROM item_spans" in sql:
            return sql + f" WHERE video_id = ANY(ARRAY{vids}::text[])"
        if " FROM meeting_roster" in sql:
            return sql + f" WHERE meeting_id = ANY(ARRAY{ids})"
        return sql

    print(f"{'':32s}{'production':>12s}{'sandbox':>10s}{'':>4s}")
    bad = 0
    for label, sql in COMPARE:
        try:
            p = prod.execute(scope(sql)).fetchone()[0]
            s = sand.execute(sql).fetchone()[0]
        except Exception as e:                               # noqa: BLE001
            print(f"{label:32s}{'?':>12s}{'?':>10s}   {type(e).__name__}")
            continue
        # Not an assertion of equality: the sandbox re-derives from scratch and
        # small differences are expected where a stage samples or an LLM is
        # involved. A LARGE gap means the pipeline is not doing the same thing.
        gap = abs(p - s) / max(p, 1)
        mark = "ok" if gap <= 0.02 else ("~" if gap <= 0.15 else "DIFFERS")
        bad += mark == "DIFFERS"
        print(f"{label:32s}{p:>12,d}{s:>10,d}   {mark}")
    print(f"\n{bad} of {len(COMPARE)} differ by more than 15%")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--build", action="store_true")
    g.add_argument("--compare", action="store_true")
    g.add_argument("--drop", action="store_true")
    g.add_argument("--dsn", action="store_true", help="print the sandbox DSN")
    a = ap.parse_args()
    if a.dsn:
        print(dsn_for(SANDBOX_DB))
    elif a.build:
        build()
    elif a.compare:
        compare()
    elif a.drop:
        with admin() as c:
            c.execute(f'DROP DATABASE IF EXISTS "{SANDBOX_DB}"')
        print(f"dropped {SANDBOX_DB}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
