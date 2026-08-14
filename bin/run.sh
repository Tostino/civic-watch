#!/bin/bash
# Launch the ingest fleet. Safe to re-run: workers claim rows transactionally,
# and anything already finished is skipped, so this doubles as a resume.
cd "$(dirname "$0")"
ROOT=..

# Workers need PASCO_DSN; sourcing it here means run.sh works from any
# shell rather than failing obscurely in a nohup'd child.
source "$ROOT/env.local.sh"
mkdir -p $ROOT/logs

start () {
    local name=$1; shift
    if pgrep -f "$name" > /dev/null 2>&1; then
        echo "  $name already running, skipping"
        return
    fi
    # setsid, not bare nohup: a worker started from an editor/agent shell is
    # otherwise in that shell's process group and dies with it. A multi-hour
    # ingest must outlive whatever launched it - we lost the whole fleet once
    # to exactly this, mid-run, with 3 videos left stranded in `claimed_by`.
    setsid nohup "$@" >> "$ROOT/logs/$name.log" 2>&1 < /dev/null &
    echo "  $name started (pid $!)"
}

echo "starting ingest fleet:"
# Downloads run ahead of the GPUs but throttle themselves so the disk does not
# fill with audio waiting on the slow diarization stage.
start dl-0  $ROOT/asr-venv/bin/python download_worker.py --worker dl-0 --max-ahead 10
start dl-1  $ROOT/asr-venv/bin/python download_worker.py --worker dl-1 --max-ahead 10

# Diarization is the bottleneck (~14x realtime), so it gets both GPUs.
start diar-0 $ROOT/diar-venv/bin/python diarize_worker.py --gpu 0
start diar-1 $ROOT/diar-venv/bin/python diarize_worker.py --gpu 1

# ASR is ~10x faster than diarization; these mostly idle waiting for work,
# draining the queue as diarization produces it.
start asr-0 $ROOT/asr-venv/bin/python asr_worker.py --gpu 0
start asr-1 $ROOT/asr-venv/bin/python asr_worker.py --gpu 1

echo
echo "logs:     $ROOT/logs/"
echo "progress: bin/status.py"
