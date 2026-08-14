#!/usr/bin/env python3
"""Apply a batch of proposed redactions, as a job the console can watch.

`bin/redact.py --apply-all` already does the work. It cannot be what the
console calls, for one reason: applying 3,439 proposals touches 370 recordings
and every one of them has to be re-indexed, because the address is in the
passage text, the BM25 postings and the embedding as well as the utterance. At
roughly four seconds a recording that is about twenty-five minutes of work, and
a request that takes twenty-five minutes is a request that has already timed
out somewhere between here and the browser.

So this is the detached runner, in the shape `bin/rederive.py` established:
status to logs/redact.json, output to logs/redact.log, and the pid in the
status so a crash reads as "died" rather than as work still in progress
(gotchas 50/51).

Progress is reported PER RECORDING rather than per redaction. That is the unit
the time is actually spent in - one re-index covers however many addresses were
removed from that recording - and a bar that moves in 370 steps tells the
operator something true, where one that moves in 3,439 would stall visibly at
each re-index for no reason a reader could see.

    bin/redact_job.py --apply-all           every proposal
    bin/redact_job.py --apply-all --ids 1 2 3   only these
"""
import argparse
import datetime
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import db       # noqa: E402
import redact   # noqa: E402

LOGDIR = os.path.join(ROOT, "logs")
STATUS = os.path.join(LOGDIR, "redact.json")
LOG = os.path.join(LOGDIR, "redact.log")


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


def run(ids=None):
    os.makedirs(LOGDIR, exist_ok=True)
    con = db.connect(autocommit=True)

    where = "status = 'proposed'"
    args = []
    if ids:
        where += " AND id = ANY(%s)"
        args.append(list(ids))
    rows = con.execute(
        f"SELECT id, video_id FROM redaction WHERE {where} ORDER BY video_id, id",
        args).fetchall()

    # Grouped so each recording is re-indexed once no matter how many addresses
    # came out of it. redact.apply() already dedupes internally; doing it here
    # too is what makes the progress count meaningful.
    by_video = {}
    for r in rows:                                      # positional: gotcha 13
        by_video.setdefault(r[1], []).append(r[0])

    # Recordings already carrying an APPLIED redaction that never reached the
    # index. That is the half-state a failed run leaves - the transcript is
    # redacted and search can still find the address - and it happened for real
    # the first time a batch was applied from the console, because the
    # re-indexer refused a boundary change and the status flip had already
    # committed. Repairing it here means the fix is "run it again" rather than
    # someone noticing.
    stranded = [r[0] for r in con.execute("""
        SELECT DISTINCT r.video_id FROM redaction r
         WHERE r.status = 'applied'
           AND EXISTS (SELECT 1 FROM passages p
                        WHERE p.video_id = r.video_id
                          AND position(r.span in p.text) > 0)""")]
    for v in stranded:
        by_video.setdefault(v, [])

    status = {"state": "running", "pid": os.getpid(), "started_at": now(),
              "total": len(rows), "recordings": len(by_video),
              "applied": 0, "done_recordings": 0, "failed": 0,
              "repaired": 0, "video": None}
    write_status(status)
    log = open(LOG, "w")

    def say(msg):
        log.write(msg + "\n")
        log.flush()

    say(f"=== redact apply started {status['started_at']} · "
        f"{len(rows)} proposals over {len(by_video)} recordings"
        + (f", {len(stranded)} to repair" if stranded else "") + " ===")

    began = time.time()
    for video_id, batch in sorted(by_video.items()):
        status["video"] = video_id
        write_status(status)
        try:
            if batch:
                status["applied"] += redact.apply(con, batch)
            else:
                # Nothing new to apply here - this recording is in the list
                # because its index fell behind. Re-index and move on.
                import index_passages
                index_passages.rebuild_video(con, video_id, verbose=False)
                status["repaired"] = status.get("repaired", 0) + 1
        except Exception as e:
            # One bad recording must not strand the other 369. The proposals
            # stay 'proposed', so a retry picks them up.
            status["failed"] += len(batch)
            say(f"  FAILED {video_id}: {type(e).__name__}: {e}")
        status["done_recordings"] += 1
        write_status(status)
        say(f"  {status['done_recordings']}/{len(by_video)}  {video_id}  "
            f"{status['applied']} applied")

    status.update(state="done", finished_at=now(), video=None,
                  seconds=round(time.time() - began))
    write_status(status)
    say(f"=== done {status['finished_at']} · {status['applied']} applied, "
        f"{status['failed']} failed, {status['seconds']}s ===")
    return 0 if not status["failed"] else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply-all", action="store_true")
    ap.add_argument("--ids", nargs="+", type=int)
    args = ap.parse_args()
    if not args.apply_all and not args.ids:
        print(__doc__)
        return 2
    return run(args.ids)


if __name__ == "__main__":
    sys.exit(main())
