#!/bin/bash
# Launch the ingest fleet. Safe to re-run: workers claim rows transactionally,
# so this doubles as the resume.
cd "$(dirname "$0")"
ROOT=..

source "$ROOT/env.local.sh"
mkdir -p $ROOT/logs $ROOT/run

# One worker per name, enforced by the kernel. `pgrep -f "$name"` missed the
# GPU workers entirely (the name is only in the download workers' argv, so a
# re-run started duplicates) and over-matched anything merely MENTIONING a
# worker - the launching shell, an editor, a grep. flock holds for the
# worker's life and releases however it dies. The probe is for the message
# only; the guarantee is the second flock.
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
