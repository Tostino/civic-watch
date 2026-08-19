#!/usr/bin/env python3
"""The console's operations: every pipeline job the UI can run, one place.

Each job is a named sequence of the DOCUMENTED commands - the same ones
the README and bin/refresh.sh define - never a new
composition invented here. Order inside a job is the load-bearing order those
files document (roster before speakers, land before minutes, affinity before
index), and jobs that would spend money say so and are refused without an
explicit paid_ok."""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

PY = os.path.join(ROOT, "emb-venv", "bin", "python")
LOGDIR = os.path.join(ROOT, "logs")
STATUS = os.path.join(LOGDIR, "job.json")
LOG = os.path.join(LOGDIR, "job.log")

# argv lists relative to ROOT. `paid` marks a job that calls the inference
# API and therefore costs money per run. `say` is what the step does, in the
# console's voice - the argv stays visible beside it, because on this page the
# command IS the honest label.
JOBS = {
    "portal_sweep": {
        "title": "Fetch county documents",
        "why": "New agendas and minutes from the county portal, folded into "
               "items, rosters and outcomes. Cheap; the county posts agendas "
               "days before a meeting.",
        "paid": False,
        "steps": [
            {"say": "Read the county portal for new agendas and minutes",
             "cmd": [PY, "bin/civicclerk.py", "--events", "--text"]},
            # Both bodies - a bare call is BCC only (see refresh.sh).
            {"say": "Update the Board of County Commissioners roster",
             "cmd": [PY, "bin/roster.py", "--write"]},
            {"say": "Update the Planning Commission roster",
             "cmd": [PY, "bin/roster.py", "--write",
                     "--body", "Planning Commission"]},
            {"say": "Land the agendas into items, and bind them to the "
                    "recordings",
             "cmd": [PY, "bin/land_agenda.py"]},
            {"say": "Read the minutes for the outcome of every item",
             "cmd": [PY, "bin/parse_minutes.py", "--write"]},
            # THE ONLY STEP THAT REBUILDS A PUBLIC SURFACE RATHER THAN A
            # DERIVED LAYER. The front page's subject strip reads
            # `subject_year`, a table, because computing it live costs 163
            # seconds - and the two steps directly above are exactly what
            # moves it: landing agendas changes which items a subject
            # matches, and parsing minutes changes every decided, continued,
            # refused and divided count inside it.
            {"say": "Recount each subject's years for the front page",
             "cmd": [PY, "bin/subjects.py", "--rollup"]},
            {"say": "Check the archive for integrity",
             "cmd": [PY, "bin/audit.py"]},
        ],
    },
    "video_sweep": {
        "title": "Look for new recordings",
        "why": "Scrapes the county's YouTube channel - /streams AND /videos, "
               "which do not overlap - and catalogs anything new. Ingest is "
               "a separate step.",
        "paid": False,
        "steps": [
            {"say": "Scan both channel tabs, and catalog every new recording",
             "cmd": [PY, "bin/catalog.py"]},
            {"say": "Check the archive for integrity",
             "cmd": [PY, "bin/audit.py"]},
        ],
    },
    "fold_in": {
        "title": "Fold new recordings into the archive",
        "why": "Identity, LLM naming, segmentation, agenda binding and the "
               "index for videos transcribed since the last fold "
               "(bin/catch_up.sh). Two stages call the paid model.",
        "paid": True,
        "steps": [
            {"say": "Identify the voices, name them, segment the meetings, "
                    "bind the agenda, rebuild the index",
             "cmd": ["bash", "bin/catch_up.sh"]},
        ],
    },
    "name_chain": {
        "title": "Name speakers with the model",
        "why": "The LLM pass with verbatim-quote verification, for voices the "
               "text signal could not reach - then affinity and the index, "
               "which must follow it.",
        "paid": True,
        "steps": [
            {"say": "Ask the model to name up to 150 voices the text missed",
             "cmd": [PY, "bin/name_speakers.py", "--write",
                     "--limit", "150"]},
            {"say": "Measure each voice against the person it is named for",
             "cmd": [PY, "bin/affinity.py"]},
            {"say": "Rebuild the search index over the new names",
             "cmd": [PY, "bin/index_passages.py"]},
            {"say": "Check the archive for integrity",
             "cmd": [PY, "bin/audit.py"]},
        ],
    },
}


def label_of(cmd):
    """The argv as the console and the log both print it: relative to ROOT."""
    return " ".join(os.path.relpath(c, ROOT) if os.path.isabs(c) else c
                    for c in cmd)


def plan(name):
    """The steps a job will run, for the console to show before it runs."""
    return [{"say": st["say"], "cmd": label_of(st["cmd"])}
            for st in JOBS[name]["steps"]]

# Steps that may report review items without that being a job failure.
TOLERATED = {"bin/audit.py", "bin/eval_agent.py"}


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


def run(name):
    job = JOBS[name]
    os.makedirs(LOGDIR, exist_ok=True)
    status = {"job": name, "state": "running", "pid": os.getpid(),
              "started_at": now(), "step": None, "step_say": None,
              "step_index": None, "step_started_at": None,
              "step_count": len(job["steps"]), "steps": []}
    write_status(status)
    log = open(LOG, "w")

    def say(msg):
        log.write(msg + "\n")
        log.flush()

    # The runner's own banners are prefixed `job:`. Scripts we call print
    # banners of their own - catch_up.sh announces each of its stages - and
    # the console reads those as sub-step progress, so the two must not be
    # confusable.
    say(f"=== job: {name} started {status['started_at']} ===")
    for i, step in enumerate(job["steps"]):
        cmd = step["cmd"]
        label = label_of(cmd)
        # Written BEFORE the step starts: the console reads this file to say
        # which step is running and for how long, and a step that announces
        # itself only on completion is a step that looks hung while it works.
        status.update(step=label, step_say=step["say"], step_index=i,
                      step_started_at=now())
        write_status(status)
        say(f"\n=== job: {label} · {now()} ===")
        t0 = time.time()
        rc = subprocess.call(cmd, stdout=log, stderr=subprocess.STDOUT,
                             cwd=ROOT)
        status["steps"].append({"cmd": label, "say": step["say"],
                                "seconds": round(time.time() - t0), "rc": rc})
        if rc != 0 and not any(t in label for t in TOLERATED):
            status.update(state="failed", finished_at=now(),
                          step_started_at=None)
            write_status(status)
            say(f"\n=== job: FAILED at {label} (rc {rc}) ===")
            return 1
    status.update(state="done", step=None, step_say=None, step_index=None,
                  step_started_at=None, finished_at=now())
    write_status(status)
    say(f"\n=== job: {name} done {status['finished_at']} ===")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?", help="job to run; omit to print status")
    args = ap.parse_args()
    if not args.name:
        print(json.dumps(read_status(), indent=2))
        return 0
    if args.name not in JOBS:
        print(f"unknown job {args.name!r}; one of {sorted(JOBS)}",
              file=sys.stderr)
        return 2
    return run(args.name)


if __name__ == "__main__":
    sys.exit(main())
