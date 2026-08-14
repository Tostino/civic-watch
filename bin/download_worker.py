"""Download audio and precompute silence points. CPU/network only.

Runs ahead of the GPU stages so they never wait on the network. Audio is kept
as 16 kHz mono FLAC - what both models want, at roughly half the size of WAV,
and lossless so re-running a stage never needs a re-download.
"""
import argparse
import os
import subprocess
import sys
import time

import db

YTDLP = "/home/user/.local/bin/yt-dlp"


def run(cmd, timeout):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def download(video_id, outdir):
    audio = os.path.join(outdir, "audio.flac")
    if not os.path.exists(audio):
        r = run([YTDLP, "--js-runtimes", "node",
                 "-f", "bestaudio/best", "-x", "--audio-format", "flac",
                 "--postprocessor-args", "-ar 16000 -ac 1",
                 "--no-progress", "-o", os.path.join(outdir, "audio.%(ext)s"),
                 f"https://www.youtube.com/watch?v={video_id}"], 3600)
        if r.returncode != 0 or not os.path.exists(audio):
            time.sleep(20)          # extraction failures usually clear at once
            r = run([YTDLP, "--js-runtimes", "node",
                     "-f", "bestaudio/best", "-x", "--audio-format", "flac",
                     "--postprocessor-args", "-ar 16000 -ac 1",
                     "--no-progress", "-o", os.path.join(outdir, "audio.%(ext)s"),
                     f"https://www.youtube.com/watch?v={video_id}"], 3600)
        if r.returncode != 0 or not os.path.exists(audio):
            raise RuntimeError(f"yt-dlp failed: {r.stderr[-400:]}")

    silences = os.path.join(outdir, "silences.txt")
    if not os.path.exists(silences):
        r = run(["ffmpeg", "-i", audio, "-af",
                 "silencedetect=noise=-32dB:d=0.6", "-f", "null", "-"], 3600)
        pts = [ln.split()[-1] for ln in r.stderr.splitlines()
               if "silence_start" in ln]
        with open(silences, "w") as f:
            f.write("\n".join(pts) + ("\n" if pts else ""))
    return audio


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", default="dl-0")
    ap.add_argument("--max-ahead", type=int, default=8,
                    help="stop downloading if this many videos already wait")
    args = ap.parse_args()

    con = db.connect()
    db.reclaim(con, args.worker)
    done = 0
    while True:
        waiting = con.execute(
            "SELECT COUNT(*) FROM videos WHERE downloaded AND NOT transcribed"
        ).fetchone()[0]
        con.commit()      # never sleep inside a transaction: it pins locks on
                          # `videos` and blocks every DDL until this loop wakes
        if waiting >= args.max_ahead:
            time.sleep(30)
            continue

        row = db.claim(con, "download", args.worker)
        if row is None:
            print(f"[{args.worker}] nothing left to download ({done} done)",
                  flush=True)
            return 0

        vid = row["id"]
        outdir = db.video_dir(vid, create=True)
        t0 = time.time()
        try:
            download(vid, outdir)
            db.release(con, vid, downloaded=True)
            done += 1
            print(f"[{args.worker}] {vid} downloaded in {time.time()-t0:.0f}s "
                  f"({row['title'][:60]})", flush=True)
        except Exception as e:
            retired = db.fail(con, vid, f"download: {e}")
            print(f"[{args.worker}] {'RETIRED' if retired else 'retry later'} "
                  f"{vid}: {str(e)[:160]}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    sys.exit(main())
