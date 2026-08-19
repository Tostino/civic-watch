#!/usr/bin/env python3
"""Re-derive speaker identity from the human labels, and measure what changed."""
import argparse
import datetime
import gzip
import json
import os
import subprocess
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))
sys.path.insert(0, os.path.join(ROOT, "web"))

import db      # noqa: E402
import admin   # noqa: E402  (SPLITS_FROM: one definition of the queue, not two)

PY = os.path.join(ROOT, "emb-venv", "bin", "python")
LOGDIR = os.path.join(ROOT, "logs")
STATUS = os.path.join(LOGDIR, "rederive.json")
LOG = os.path.join(LOGDIR, "rederive.log")
SNAP = os.path.join(LOGDIR, "rederive.before.gz")
# What --revert restores: the two tables the chain rewrites. Small (a few
# thousand rows each), and together with the views they ARE the derived
# speaker state, so restoring them and re-indexing puts every reader-visible
# name back. Overwritten by the next run, so revert covers the LATEST run.
BACKUP = os.path.join(LOGDIR, "rederive.backup.gz")
TABLES = {
    "speaker_identity": "video_id, local_label, cluster, name, confidence, source",
    "voice_affinity": "video_id, local_label, cluster, name, similarity",
}

STEPS = [
    ("speaker_id", [PY, "bin/speaker_id.py", "--write"]),
    ("chair_anchor", [PY, "bin/chair_anchor.py", "--write"]),
    ("affinity", [PY, "bin/affinity.py"]),
    ("index_passages", [PY, "bin/index_passages.py"]),
    ("audit", [PY, "bin/audit.py"]),
]


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def write_status(d):
    tmp = STATUS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, STATUS)


def read_status():
    try:
        with open(STATUS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def counts(con):
    named = con.execute(
        "SELECT COUNT(*) FROM utterance_speaker WHERE name IS NOT NULL"
    ).fetchone()[0]
    splits = con.execute(f"SELECT COUNT(*) {admin.SPLITS_FROM}").fetchone()[0]
    return {"named": named, "splits": splits}


def snapshot(con):
    with gzip.open(SNAP, "wt") as f:
        for r in con.execute("SELECT video_id, idx, name FROM utterance_speaker"):
            f.write(f"{r[0]}\t{r[1]}\t{r[2] or ''}\n")


def backup(con):
    with gzip.open(BACKUP, "wt") as f:
        for table, cols in TABLES.items():
            # Positional, never list(row): db.Row is a Mapping, so iterating
            # it yields column NAMES - which this backup did, and
            # only the preflight restore caught it.
            n = len(cols.split(","))
            for r in con.execute(f"SELECT {cols} FROM {table}"):
                f.write(json.dumps({"table": table,
                                    "row": [r[i] for i in range(n)]},
                                   default=str) + "\n")


def restore_tables(con):
    """Put speaker_identity and voice_affinity back as the backup holds them.

    Runs inside the caller's transaction, so the preflight can prove the
    write path against a rollback before the real thing runs (the lesson of
    a syntax error in an INSERT nobody exercised is an empty table
    behind a log full of successes).
    """
    rows = {t: [] for t in TABLES}
    with gzip.open(BACKUP, "rt") as f:
        for line in f:
            d = json.loads(line)
            rows[d["table"]].append(d["row"])
    with con.cursor() as cur:
        for table, cols in TABLES.items():
            ph = ", ".join(["%s"] * len(cols.split(",")))
            cur.execute(f"TRUNCATE {table}")
            cur.executemany(
                f"INSERT INTO {table} ({cols}) VALUES ({ph})", rows[table])
    return {t: len(v) for t, v in rows.items()}


def diff(con):
    """What the run actually changed, utterance by utterance."""
    before = {}
    with gzip.open(SNAP, "rt") as f:
        for line in f:
            vid, idx, name = line.rstrip("\n").split("\t", 2)
            before[(vid, int(idx))] = name
    changed = gained = lost = 0
    movers = Counter()
    for r in con.execute("SELECT video_id, idx, name FROM utterance_speaker"):
        old = before.get((r[0], r[1]), "")
        new = r[2] or ""
        if old == new:
            continue
        changed += 1
        if not old:
            gained += 1
        elif not new:
            lost += 1
        movers[(old or "(unnamed)", new or "(unnamed)")] += 1
    return {
        "changed": changed,
        "gained": gained,
        "lost": lost,
        "movers": [{"from": a, "to": b, "n": n}
                   for (a, b), n in movers.most_common(10)],
    }


def run():
    os.makedirs(LOGDIR, exist_ok=True)
    con = db.connect(autocommit=True)
    started = now()
    labels = con.execute("SELECT COUNT(*) FROM speaker_label").fetchone()[0]
    status = {"state": "running", "pid": os.getpid(), "started_at": started,
              "labels_at_start": labels, "step": "snapshot", "steps": []}
    write_status(status)

    log = open(LOG, "w")

    def say(msg):
        log.write(msg + "\n")
        log.flush()

    say(f"=== rederive started {started} · {labels} human labels ===")
    before = counts(con)
    snapshot(con)
    backup(con)
    # Prove the revert path BEFORE changing anything: restore from the backup
    # just written, inside a rolled-back transaction. If revert cannot work,
    # the run must not start - a safety net is only one if it has been load
    # tested before somebody falls into it.
    pre = db.connect(autocommit=False)
    try:
        n = restore_tables(pre)
    finally:
        pre.rollback()
        pre.close()
    say(f"before: {before['named']:,} named · {before['splits']} split reviews")
    say(f"backup verified restorable: {n}")

    for name, cmd in STEPS:
        status["step"] = name
        write_status(status)
        say(f"\n=== {name} · {now()} ===")
        t0 = time.time()
        rc = subprocess.call(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
        status["steps"].append({"name": name, "seconds": round(time.time() - t0),
                                "rc": rc})
        # The audit reporting review items is information, not a failure.
        if rc != 0 and name != "audit":
            status.update(state="failed", step=name, finished_at=now())
            write_status(status)
            say(f"\n=== FAILED at {name} (rc {rc}) ===")
            return 1

    status["step"] = "diff"
    write_status(status)
    after = counts(con)
    status.update(state="done", step=None, finished_at=now(),
                  before=before, after=after, diff=diff(con))
    write_status(status)
    os.remove(SNAP)
    d = status["diff"]
    say(f"\nafter: {after['named']:,} named · {after['splits']} split reviews")
    say(f"changed {d['changed']:,} utterances "
        f"({d['gained']:,} newly named, {d['lost']:,} un-named)")
    say(f"=== rederive done {status['finished_at']} ===")
    return 0


def revert():
    """Undo the latest run: restore both tables, then re-bake the index."""
    if not os.path.exists(BACKUP):
        print("no backup to revert to - nothing has run since it was removed",
              file=sys.stderr)
        return 1
    con = db.connect(autocommit=False)
    log = open(LOG, "a")

    def say(msg):
        log.write(msg + "\n")
        log.flush()

    say(f"\n=== revert started {now()} ===")
    n = restore_tables(con)
    con.commit()
    say(f"restored {n}")
    status = read_status() or {}
    status.update(state="reverting", step="index_passages", pid=os.getpid())
    write_status(status)
    rc = subprocess.call([PY, "bin/index_passages.py"],
                         stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
    subprocess.call([PY, "bin/audit.py"],
                    stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
    status.update(state="reverted" if rc == 0 else "failed",
                  step=None, finished_at=now())
    write_status(status)
    say(f"=== revert {'done' if rc == 0 else 'FAILED at index_passages'} {now()} ===")
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true",
                    help="run the chain (otherwise print the last status)")
    ap.add_argument("--revert", action="store_true",
                    help="restore the pre-run tables and re-index")
    args = ap.parse_args()
    if args.revert:
        return revert()
    if not args.run:
        print(json.dumps(read_status(), indent=2))
        return 0
    return run()


if __name__ == "__main__":
    sys.exit(main())
