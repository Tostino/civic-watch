"""Speaker diarization stage. Loads pyannote once, then loops over videos.

Run one per GPU. This is the slowest stage (~12x realtime), so it dictates the
wall clock for the whole archive.
"""
import argparse
import json
import os
import sys
import time

import torch

import db


class MissingInput(Exception):
    """An upstream artefact is absent; the fix is upstream."""

CHECKPOINT = "pyannote/speaker-diarization-3.1"


def read_token():
    for env in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        if os.environ.get(env):
            return os.environ[env]
    p = os.path.expanduser("~/.cache/huggingface/token")
    return open(p).read().strip() if os.path.exists(p) else None


def load_pipeline(gpu):
    from pyannote.audio import Pipeline
    token = read_token()
    try:
        pipe = Pipeline.from_pretrained(CHECKPOINT, token=token)
    except TypeError:
        pipe = Pipeline.from_pretrained(CHECKPOINT, use_auth_token=token)
    if pipe is None:
        raise RuntimeError("pipeline is None - HF token lacks gated access")
    return pipe.to(torch.device(f"cuda:{gpu}"))


def diarize(pipe, audio_path, out_path, emb_path):
    result = pipe(audio_path)
    # pyannote 4.x returns DiarizeOutput; the "exclusive" annotation drops
    # overlapping speech, which is what word-to-speaker assignment needs.
    ann = getattr(result, "exclusive_speaker_diarization", None)
    if ann is None:
        ann = getattr(result, "speaker_diarization", result)
    turns = [{"start": round(t.start, 3), "end": round(t.end, 3), "speaker": s}
             for t, _, s in ann.itertracks(yield_label=True)]
    with open(out_path, "w") as f:
        json.dump({"turns": turns}, f)

    # One centroid embedding per speaker, ordered by speaker_diarization
    # labels(). These are what makes a voice identifiable ACROSS meetings -
    # recomputing them later would mean re-diarizing the whole archive, so
    # they are saved even though nothing consumes them yet.
    emb = getattr(result, "speaker_embeddings", None)
    labels = getattr(getattr(result, "speaker_diarization", None), "labels",
                     None)
    if emb is not None and labels is not None:
        import numpy as np
        np.savez_compressed(emb_path, labels=np.array(labels(), dtype=object),
                            embeddings=emb)
    return turns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gpu", type=int, required=True)
    args = ap.parse_args()
    worker = f"diar-{args.gpu}"

    pipe = load_pipeline(args.gpu)
    print(f"[{worker}] pyannote loaded on cuda:{args.gpu}", flush=True)

    con = db.connect()
    db.reclaim(con, worker)
    done = 0
    while True:
        row = db.claim(con, "diarize", worker)
        if row is None:
            # Nothing claimable: either downloads are still catching up, or the
            # run is genuinely finished.
            if db.work_remaining(con, "diarize"):
                time.sleep(20)
                continue
            print(f"[{worker}] no work left ({done} done)", flush=True)
            return 0

        vid = row["id"]
        d = db.video_dir(vid)
        audio = os.path.join(d, "audio.flac")
        t0 = time.time()
        try:
            if not os.path.exists(audio) or os.path.getsize(audio) < 1024:
                raise MissingInput("audio missing or empty")
            turns = diarize(pipe, audio, os.path.join(d, "diarization.json"),
                            os.path.join(d, "embeddings.npz"))
            spk = len({t["speaker"] for t in turns})
            db.release(con, vid, diarized=True, speakers=spk)
            done += 1
            rt = row["duration"] / max(time.time() - t0, 1)
            print(f"[{worker}] {vid} {len(turns)} turns, {spk} speakers, "
                  f"{time.time()-t0:.0f}s ({rt:.1f}x realtime)", flush=True)
        except MissingInput as e:
            # The audio is gone or unreadable: that is the download stage's
            # problem, not something diarization can retry its way out of.
            retired = db.rewind(con, vid, "download", f"diarize: {e}")
            print(f"[{worker}] {'RETIRED' if retired else 're-download'} "
                  f"{vid}: {e}", file=sys.stderr, flush=True)
        except Exception as e:
            retired = db.fail(con, vid, f"diarize: {e}")
            print(f"[{worker}] {'RETIRED' if retired else 'retry later'} "
                  f"{vid}: {str(e)[:160]}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    sys.exit(main())
