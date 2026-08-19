"""Transcription stage: everything from diarization output to indexed text."""
import argparse
import bisect
import json
import os
import sys
import time

import soundfile as sf
import torch

import db


class MissingInput(Exception):
    """An upstream artefact is absent; the fix is upstream."""
import names

SR = 16000
MAX_WIN = 24.0    # seconds; long enough for context, short enough that the
                  # TDT duration head does not skip ahead and drop speech
PAD = 0.25
BATCH = 16
REPAIR_PAD = 2.0
REPAIR_EDGE = 0.75
GAP_SPLIT = 2.0
MAX_UTT = 30.0
BOOST_ALPHA = 1.0  # chosen by sweep: fixes target names, leaves the rest intact

BIN = os.path.dirname(os.path.abspath(__file__))
PHRASES = os.path.join(BIN, "phrases.txt")


# ---------------------------------------------------------------- windowing
def build_windows(turns, silences, duration):
    merged = []
    for t in sorted(turns, key=lambda t: t["start"]):
        if merged and t["start"] <= merged[-1][1] + 0.4:
            merged[-1][1] = max(merged[-1][1], t["end"])
        else:
            merged.append([t["start"], t["end"]])

    windows = []
    for a, b in merged:
        a, b = max(0.0, a - PAD), min(duration, b + PAD)
        while b - a > MAX_WIN:
            lo, hi = a + MAX_WIN * 0.5, a + MAX_WIN
            i, j = bisect.bisect_left(silences, lo), bisect.bisect_right(silences, hi)
            cut = silences[(i + j) // 2] if j > i else a + MAX_WIN
            windows.append((a, cut))
            a = cut
        if b - a > 0.2:
            windows.append((a, b))
    return windows


# ------------------------------------------------------------------- model
def load_model(gpu):
    import nemo.collections.asr as nemo_asr
    from omegaconf import open_dict

    model = nemo_asr.models.ASRModel.from_pretrained("nvidia/parakeet-tdt-0.6b-v3")
    model = model.to(torch.device(f"cuda:{gpu}")).eval()
    cfg = model.cfg.decoding
    with open_dict(cfg):
        cfg.strategy = "greedy_batch"
        # CUDA-graph TDT decoding throws illegal memory accesses on this driver
        cfg.greedy.use_cuda_graph_decoder = False
        cfg.greedy.boosting_tree = {
            "key_phrases_file": PHRASES,
            "context_score": 1.0,
            "depth_scaling": 2.0,   # documented value for TDT
            "use_triton": False,
        }
        cfg.greedy.boosting_tree_alpha = BOOST_ALPHA
    model.change_decoding_strategy(cfg)
    return model


def transcribe_clips(model, paths, offsets):
    """Transcribe wav clips, returning words/segments on the global timeline."""
    words, segments = [], []
    for s in range(0, len(paths), BATCH):
        batch = paths[s:s + BATCH]
        # no_grad, not inference_mode: the boosting tree mutates cached state
        # that inference tensors refuse to version-track.
        with torch.no_grad():
            hyps = model.transcribe(batch, timestamps=True, batch_size=len(batch))
        for k, h in enumerate(hyps):
            off = offsets[s + k]
            ts = getattr(h, "timestamp", None) or {}
            for w in ts.get("word", []):
                words.append({"word": w.get("word", w.get("char", "")),
                              "start": round(float(w["start"]) + off, 3),
                              "end": round(float(w["end"]) + off, 3)})
            for sg in ts.get("segment", []):
                text = sg.get("segment", sg.get("text", "")).strip()
                if text:
                    segments.append({"text": text,
                                     "start": round(float(sg["start"]) + off, 3),
                                     "end": round(float(sg["end"]) + off, 3)})
    return words, segments


# -------------------------------------------------------------------- audit
def find_gaps(words, turns):
    """Regions pyannote heard as speech but the ASR barely transcribed.

    Catches near-total dropouts only; partial skips inside an otherwise
    transcribed passage do not show up here.
    """
    merged = []
    for t in sorted(turns, key=lambda t: t["start"]):
        if merged and t["start"] <= merged[-1][1] + 0.3:
            merged[-1][1] = max(merged[-1][1], t["end"])
        else:
            merged.append([t["start"], t["end"]])

    starts = [w["start"] for w in words]
    gaps = []
    for a, b in merged:
        i = bisect.bisect_left(starts, a - 5)
        inside = [w for w in words[i:] if w["start"] < b and w["end"] > a]
        cov = sum(min(w["end"], b) - max(w["start"], a) for w in inside)
        if b - a >= 2.0 and cov / (b - a) < 0.35:
            gaps.append({"start": a, "end": b, "coverage": cov / (b - a)})
    return gaps


def repair(model, audio, gaps, words, segments, tmpdir):
    """Re-transcribe dropped regions alone, where the model recovers them."""
    if not gaps:
        return words, segments, 0
    os.makedirs(tmpdir, exist_ok=True)
    paths, spans = [], []
    dur = len(audio) / SR
    for i, g in enumerate(gaps):
        a = max(0.0, g["start"] - REPAIR_PAD)
        b = min(dur, g["end"] + REPAIR_PAD)
        p = os.path.join(tmpdir, f"r_{i:03d}.wav")
        sf.write(p, audio[int(a * SR):int(b * SR)], SR)
        paths.append(p)
        spans.append((a, g["start"], g["end"]))

    new_w, new_s = [], []
    for s in range(0, len(paths), 8):
        batch = paths[s:s + 8]
        with torch.no_grad():
            hyps = model.transcribe(batch, timestamps=True, batch_size=len(batch))
        for k, h in enumerate(hyps):
            off, gs, ge = spans[s + k]
            # A short gap re-transcribed with padding rarely lands its words
            # exactly inside the span, so allow a margin - and use the same
            # margin when deleting, so the swap cannot duplicate edge words.
            lo, hi = gs - REPAIR_EDGE, ge + REPAIR_EDGE
            ts = getattr(h, "timestamp", None) or {}
            got = [{"word": w.get("word", w.get("char", "")),
                    "start": round(float(w["start"]) + off, 3),
                    "end": round(float(w["end"]) + off, 3)}
                   for w in ts.get("word", [])
                   if lo <= (float(w["start"]) + float(w["end"])) / 2 + off <= hi]
            if not got:
                continue
            gotseg = [{"text": sg.get("segment", sg.get("text", "")).strip(),
                       "start": round(float(sg["start"]) + off, 3),
                       "end": round(float(sg["end"]) + off, 3)}
                      for sg in ts.get("segment", [])
                      if lo <= (float(sg["start"]) + float(sg["end"])) / 2 + off <= hi
                      and sg.get("segment", sg.get("text", "")).strip()]
            words = [w for w in words
                     if not lo <= (w["start"] + w["end"]) / 2 <= hi]
            segments = [x for x in segments
                        if not lo <= (x["start"] + x["end"]) / 2 <= hi]
            new_w.extend(got)
            new_s.extend(gotseg)

    words = sorted(words + new_w, key=lambda w: w["start"])
    segments = sorted(segments + new_s, key=lambda s: s["start"])
    for p in paths:
        os.remove(p)
    return words, segments, len(new_w)


# -------------------------------------------------------- speakers + output
def assign_speakers(words, turns):
    turns = sorted(turns, key=lambda t: t["start"])
    if not turns:
        return [{**w, "speaker": None} for w in words]
    out, j = [], 0
    for w in words:
        while j < len(turns) - 1 and turns[j]["end"] < w["start"] - 30:
            j += 1
        best, best_ov = None, 0.0
        for t in turns[j:]:
            if t["start"] > w["end"] + 30:
                break
            ov = min(w["end"], t["end"]) - max(w["start"], t["start"])
            if ov > best_ov:
                best, best_ov = t["speaker"], ov
        if best is None:
            mid = (w["start"] + w["end"]) / 2
            best = min(turns, key=lambda t: min(abs(t["start"] - mid),
                                                abs(t["end"] - mid)))["speaker"]
        out.append({**w, "speaker": best})
    return out


def group(words):
    utts = []
    for w in words:
        if (utts and w["speaker"] == utts[-1]["speaker"]
                and w["start"] - utts[-1]["end"] < GAP_SPLIT
                and utts[-1]["end"] - utts[-1]["start"] < MAX_UTT):
            utts[-1]["_w"].append(w["word"])
            utts[-1]["end"] = w["end"]
        else:
            utts.append({"speaker": w["speaker"], "start": w["start"],
                         "end": w["end"], "_w": [w["word"]]})
    mapping, out = {}, []
    for u in utts:
        if u["speaker"] not in mapping:
            mapping[u["speaker"]] = f"Speaker {len(mapping) + 1}"
        out.append({"speaker": mapping[u["speaker"]], "start": u["start"],
                    "end": u["end"],
                    "text": names.fix(" ".join(u["_w"]).strip())})
    return out


def hhmmss(t):
    h, rem = divmod(float(t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d}"


def srt_ts(t):
    h, rem = divmod(float(t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}"


def write_outputs(d, row, utts, gap_seconds):
    with open(os.path.join(d, "transcript.json"), "w") as f:
        json.dump({"video_id": row["id"], "title": row["title"],
                   "duration": row["duration"], "utterances": utts}, f)
    with open(os.path.join(d, "transcript.txt"), "w") as f:
        f.write(f"{row['title']}\n"
                f"https://www.youtube.com/watch?v={row['id']}\n"
                f"{len(utts)} utterances | "
                f"{sum(len(u['text'].split()) for u in utts):,} words | "
                f"{gap_seconds:.0f}s low-confidence\n"
                "ASR: Parakeet TDT 0.6B v3 (context-biased) | "
                "Diarization: pyannote\n"
                "Speaker labels are voice clusters, not verified identities.\n"
                + "=" * 78 + "\n\n")
        for u in utts:
            f.write(f"[{hhmmss(u['start'])}] {u['speaker']}:\n{u['text']}\n\n")
    with open(os.path.join(d, "transcript.srt"), "w") as f:
        for i, u in enumerate(utts, 1):
            f.write(f"{i}\n{srt_ts(u['start'])} --> {srt_ts(u['end'])}\n"
                    f"{u['speaker']}: {u['text']}\n\n")


# -------------------------------------------------------------------- main
def process(model, con, row):
    vid = row["id"]
    d = db.video_dir(vid)
    audio_path = os.path.join(d, "audio.flac")
    # Named explicitly so a missing artefact routes to the stage that owns it,
    # instead of surfacing as an opaque FileNotFoundError and burning attempts.
    if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 1024:
        raise MissingInput("audio missing or empty")
    for f in ("diarization.json", "silences.txt"):
        if not os.path.exists(os.path.join(d, f)):
            raise MissingInput(f"{f} missing")
    turns = json.load(open(os.path.join(d, "diarization.json")))["turns"]
    silences = sorted(float(x) for x in open(os.path.join(d, "silences.txt"))
                      if x.strip())
    info = sf.info(audio_path)
    duration = info.duration

    windows = build_windows(turns, silences, duration)
    audio, _ = sf.read(audio_path, dtype="float32")
    tmp = os.path.join(d, "_win")
    os.makedirs(tmp, exist_ok=True)
    paths, offsets = [], []
    for i, (a, b) in enumerate(windows):
        p = os.path.join(tmp, f"w_{i:05d}.wav")
        sf.write(p, audio[int(a * SR):int(b * SR)], SR)
        paths.append(p)
        offsets.append(a)

    words, segments = transcribe_clips(model, paths, offsets)
    for p in paths:
        os.remove(p)

    gaps = find_gaps(words, turns)
    words, segments, recovered = repair(model, audio, gaps, words, segments, tmp)
    del audio
    os.rmdir(tmp) if not os.listdir(tmp) else None

    remaining = find_gaps(words, turns)
    gap_seconds = sum(g["end"] - g["start"] for g in remaining)

    utts = group(assign_speakers(sorted(words, key=lambda w: w["start"]), turns))
    write_outputs(d, row, utts, gap_seconds)
    db.index_video(con, vid, utts)
    return len(words), recovered, gap_seconds, len(utts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gpu", type=int, required=True)
    args = ap.parse_args()
    worker = f"asr-{args.gpu}"

    model = load_model(args.gpu)
    print(f"[{worker}] parakeet loaded on cuda:{args.gpu}", flush=True)

    con = db.connect()
    db.reclaim(con, worker)
    done = 0
    while True:
        row = db.claim(con, "asr", worker)
        if row is None:
            # Diarization is the slow stage; ASR spends most of the run waiting
            # on it rather than exiting.
            if db.work_remaining(con, "asr"):
                time.sleep(20)
                continue
            print(f"[{worker}] no work left ({done} done)", flush=True)
            return 0
        vid = row["id"]
        t0 = time.time()
        try:
            nw, rec, gs, nu = process(model, con, row)
            db.release(con, vid, transcribed=True, words=nw, gap_seconds=gs)
            done += 1
            rt = row["duration"] / max(time.time() - t0, 1)
            print(f"[{worker}] {vid} {nw} words, {nu} utts, +{rec} repaired, "
                  f"{gs:.0f}s weak, {time.time()-t0:.0f}s ({rt:.0f}x realtime)",
                  flush=True)
        except MissingInput as e:
            # Whichever stage owns the absent file gets the video back.
            # audio.flac AND silences.txt are both written by the download
            # stage; only diarization.json comes from diarization.
            stage = "diarize" if "diarization" in str(e) else "download"
            retired = db.rewind(con, vid, stage, f"asr: {e}")
            print(f"[{worker}] {'RETIRED' if retired else 'redo ' + stage} "
                  f"{vid}: {e}", file=sys.stderr, flush=True)
        except Exception as e:
            retired = db.fail(con, vid, f"asr: {e}")
            print(f"[{worker}] {'RETIRED' if retired else 'retry later'} "
                  f"{vid}: {str(e)[:160]}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    sys.exit(main())
