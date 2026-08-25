#!/bin/bash
# Launch the ingest fleet. Safe to re-run: workers claim rows transactionally,
# so this doubles as the resume.
cd "$(dirname "$0")"
ROOT=..

source "$ROOT/env.local.sh"
mkdir -p $ROOT/logs $ROOT/run

#
#  ONE WORKER PER NAME, ENFORCED BY THE KERNEL RATHER THAN BY A GREP.
#
# This used to ask `pgrep -f "$name"`, and that question cannot do either half
# of its job.
#
# It MISSED. `dl-0` appears in the download worker's own arguments
# (`--worker dl-0`) so that one matched, but the GPU workers are launched as
# `diarize_worker.py --gpu 0` and the string `diar-0` is nowhere in it. Four of
# the six had no guard at all: a second run started a second diar-0 and a
# second asr-0, so two GPUs carried four workers. Observed rather than
# theorised - it happened on 25 August 2026, while recovering a stalled
# download, and the only reason it cost nothing is that the queue was two
# recordings deep instead of two hundred.
#
# And it OVER-MATCHED, which is the half that cannot be fixed by improving the
# pattern. `pgrep -f` reads every process's whole command line, so anything
# that merely MENTIONS a worker counts as one: the shell that launched it, an
# editor with the file open, a `grep diarize_worker` in another terminal. A
# sharper regular expression narrows both failures and removes neither, because
# the process table is the wrong thing to be asking.
#
# So ask the kernel. `flock` holds an advisory lock for exactly as long as the
# worker lives, and releases it however the worker dies - clean exit, kill -9,
# power cut. Nothing to clean up, no stale pid file to age, and no way for an
# unrelated process to be mistaken for a worker.
#
# The probe is for the MESSAGE only. Two simultaneous runs could both find the
# lock free and both launch; only one can hold it, and the loser exits at once.
# The guarantee is the second flock, never the first.
start () {
    local name=$1; shift
    local lock="$ROOT/run/$name.lock"
    if ! flock -n "$lock" true 2>/dev/null; then
        echo "  $name already running, skipping"
        return
    fi
    # setsid, not bare nohup: a worker in the launching shell's process group
    # dies with that shell, which once cost the whole fleet mid-run.
    setsid nohup flock -n "$lock" "$@" >> "$ROOT/logs/$name.log" 2>&1 < /dev/null &
    echo "  $name started"
}

echo "starting ingest fleet:"
start dl-0  $ROOT/asr-venv/bin/python download_worker.py --worker dl-0 --max-ahead 10
start dl-1  $ROOT/asr-venv/bin/python download_worker.py --worker dl-1 --max-ahead 10

# Diarization is the bottleneck (~14x realtime), so it gets both GPUs; ASR is
# ~10x faster and mostly idles, draining the queue as diarization fills it.
start diar-0 $ROOT/diar-venv/bin/python diarize_worker.py --gpu 0
start diar-1 $ROOT/diar-venv/bin/python diarize_worker.py --gpu 1

start asr-0 $ROOT/asr-venv/bin/python asr_worker.py --gpu 0
start asr-1 $ROOT/asr-venv/bin/python asr_worker.py --gpu 1

echo
echo "logs:     $ROOT/logs/"
echo "progress: bin/status.py"
