#!/usr/bin/env python
"""Does a change to the boost list help or hurt? Measure, do not guess.

    asr-venv/bin/python bin/eval_phrases.py build   --work /tmp/ph
    asr-venv/bin/python bin/eval_phrases.py compare --work /tmp/ph \\
        --a old-phrases.txt --b bin/phrases.txt
    asr-venv/bin/python bin/eval_phrases.py compare --work /tmp/ph \\
        --b bin/phrases.txt --baseline /tmp/ph/base.json   # gate a change

Everything runs under asr-venv, which has the database, ffmpeg and Parakeet.

THREE SETS OF CLIPS, ANSWERING THREE DIFFERENT QUESTIONS.

TARGETS are clips whose stored transcript holds a mishearing the lexicon knows
about - "anclope" where the river is Anclote. They measure whether a change
fixes what it was meant to fix.

CONTROLS are clips with no lexicon word anywhere near them, and they are the
half that matters. Boosting is a thumb on the scale for every word that starts
like a boosted one, so the damage never appears where you are looking. Adding
`Wellhead` to the list once turned "Barbara Wellheit" into "Barbara Wellhead",
and no amount of staring at the targets would have shown it.

BYSTANDERS hold a boosted word that the ASR already gets right. Nothing should
happen to them, and a change that moves one is a change reaching further than
it was asked to.

A CHANGED CONTROL IS NOT AUTOMATICALLY A REGRESSION. Some are the new list
fixing a clip that was never chosen as a target - which is why they are sorted
into three piles rather than counted. A clip that GAINED a boosted word is
scored as a fix, one whose only change is a respelling of the same sound
("all right" for "alright") as cosmetic, and everything else as a regression.

Both runs see byte-identical audio and the same model object in one process,
so any difference between them is the phrase list and nothing else.
"""
import argparse
import difflib
import json
import os
import random
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import db                                                     # noqa: E402
import lexicon                                                # noqa: E402

# A clip has to be long enough to carry context and short enough that one bad
# word does not vanish into a paragraph.
MIN_S, MAX_S = 2.0, 22.0
# A second either side: an utterance cut exactly at its boundaries loses the
# coarticulation the model needs to start.
PAD = 1.0


def flat(s):
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split()


def spoken_same(a, b):
    """Whether two transcripts say the same thing, ignoring how numbers were
    written down.

    "September 17th, 2024, 130" and "September seventeenth, twenty twenty
    four, one thirty" are the same words heard the same way, and counting the
    second as a regression made the harness pessimistic about every change
    that happened to shift a date. bin/eval_voice.py already has to solve
    this - it compares a spoken date against a written one - so its parser is
    the one used here rather than a second opinion about English numbers.
    """
    try:
        from eval_voice import to_digits
    except Exception:                                         # noqa: BLE001
        return flat(a) == flat(b)
    norm = lambda s: re.sub(r"[^a-z0-9]", "", to_digits(" ".join(flat(s))))
    return norm(a) == norm(b)


# ------------------------------------------------------------------- build

def _cut(u, dst):
    src = os.path.join(os.path.dirname(HERE), "data", u["video_id"], "audio.flac")
    if not os.path.exists(src):
        return False
    if os.path.exists(dst):
        return True
    start = max(0.0, float(u["start"]) - PAD)
    dur = float(u["end"]) - float(u["start"]) + 2 * PAD
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
                    "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", src,
                    "-ac", "1", "-ar", "16000", dst], check=False)
    return os.path.exists(dst)


def build(args):
    """Choose the clips and cut them. Slow once, then reused by every run."""
    clips = os.path.join(args.work, "clips")
    os.makedirs(clips, exist_ok=True)
    rnd = random.Random(args.seed)
    meta, n = [], 0

    pairs = [(h, e["term"]) for e in lexicon.ENTRIES for h in e.get("heard") or []]
    terms = [t.lower() for t in lexicon.phrases()]
    with db.connect() as con:
        # TARGETS: the ASR wrote the wrong thing here, and we know what it
        # should have written.
        for heard, term in pairs:
            rows = con.execute(
                'SELECT video_id, idx, start, "end", text FROM utterances '
                'WHERE text ~* %s AND "end" - start BETWEEN %s AND %s '
                'ORDER BY video_id, idx LIMIT %s',
                (r"\y" + heard + r"\y", MIN_S, MAX_S, args.per_variant)).fetchall()
            for u in rows:
                dst = os.path.join(clips, f"t{n:04d}.wav")
                if _cut(u, dst):
                    meta.append({"wav": os.path.basename(dst), "kind": "target",
                                 "term": term, "heard": heard,
                                 **{k: str(v) for k, v in u.items()}})
                    n += 1

        # BYSTANDERS: a boosted word the ASR already spells correctly. These
        # should not move at all.
        for e in lexicon.ENTRIES:
            t = e["term"]
            if " " in t:
                continue
            rows = con.execute(
                'SELECT video_id, idx, start, "end", text FROM utterances '
                'WHERE text ~* %s AND "end" - start BETWEEN %s AND %s '
                'ORDER BY video_id, idx LIMIT %s',
                (r"\y" + re.escape(t) + r"\y", MIN_S, MAX_S,
                 args.per_bystander)).fetchall()
            for u in rows:
                dst = os.path.join(clips, f"b{n:04d}.wav")
                if _cut(u, dst):
                    meta.append({"wav": os.path.basename(dst), "kind": "bystander",
                                 "term": t, **{k: str(v) for k, v in u.items()}})
                    n += 1

        # CONTROLS: nothing to do with any of it.
        rows = con.execute(
            'SELECT video_id, idx, start, "end", text FROM utterances '
            'WHERE "end" - start BETWEEN %s AND %s AND length(text) > 60 '
            'ORDER BY video_id, idx LIMIT 60000', (MIN_S + 2, MAX_S)).fetchall()
        clean = [r for r in rows
                 if not any(t in (r["text"] or "").lower() for t in terms)
                 and not any(h in (r["text"] or "").lower() for h, _ in pairs)]
        for u in rnd.sample(clean, min(args.controls * 3, len(clean))):
            if len([m for m in meta if m["kind"] == "control"]) >= args.controls:
                break
            dst = os.path.join(clips, f"c{n:04d}.wav")
            if _cut(u, dst):
                meta.append({"wav": os.path.basename(dst), "kind": "control",
                             **{k: str(v) for k, v in u.items()}})
                n += 1

    with open(os.path.join(args.work, "clips.json"), "w") as f:
        json.dump(meta, f, indent=1)
    kinds = {k: sum(1 for m in meta if m["kind"] == k)
             for k in ("target", "bystander", "control")}
    print(f"{len(meta)} clips in {clips}: " +
          ", ".join(f"{v} {k}" for k, v in kinds.items()))


# ----------------------------------------------------------------- compare

def transcribe(model, paths, phrases_file, alpha):
    from omegaconf import open_dict
    cfg = model.cfg.decoding
    with open_dict(cfg):
        cfg.strategy = "greedy_batch"
        cfg.greedy.use_cuda_graph_decoder = False
        cfg.greedy.boosting_tree = {"key_phrases_file": phrases_file,
                                    "context_score": 1.0,
                                    "depth_scaling": 2.0, "use_triton": False}
        cfg.greedy.boosting_tree_alpha = alpha
    model.change_decoding_strategy(cfg)
    import torch
    out = {}
    for s in range(0, len(paths), 8):
        batch = paths[s:s + 8]
        with torch.no_grad():
            hyps = model.transcribe(batch, batch_size=len(batch), verbose=False)
        for p, h in zip(batch, hyps):
            out[os.path.basename(p)] = (h.text if hasattr(h, "text")
                                        else str(h)).strip()
    return out


# Respellings of the same sound. A change that is only one of these is not a
# difference in what the model heard.
COSMETIC = ({"all", "right", "and", "no", "know", "ok", "okay", "because",
             "cause", "'cause"},
            {"alright", "all", "right", "then", "know", "no", "okay", "ok",
             "cause", "because"})


def classify(before, after, boosted):
    """fix | cosmetic | regression, for a clip that changed."""
    d = [x for x in difflib.ndiff(flat(before), flat(after)) if x[0] in "+-"]
    plus = [x[2:] for x in d if x[0] == "+"]
    minus = [x[2:] for x in d if x[0] == "-"]
    if any(p in boosted for p in plus):
        return "fix", plus, minus
    if set(minus) <= COSMETIC[0] and set(plus) <= COSMETIC[1]:
        return "cosmetic", plus, minus
    if spoken_same(before, after):
        return "cosmetic", plus, minus
    return "regression", plus, minus


def compare(args):
    meta = json.load(open(os.path.join(args.work, "clips.json")))
    clips = os.path.join(args.work, "clips")
    paths = [os.path.join(clips, m["wav"]) for m in meta]
    boosted = {l.strip().lower() for l in open(args.b) if l.strip()}

    import nemo.collections.asr as nemo_asr
    model = nemo_asr.models.ASRModel.from_pretrained(
        "nvidia/parakeet-tdt-0.6b-v3").to(args.device).eval()

    if args.baseline and os.path.exists(args.baseline):
        a = json.load(open(args.baseline))
        print(f"a: {len(a)} clips from {args.baseline}")
    else:
        a = transcribe(model, paths, args.a, args.alpha)
        print(f"a: {len(a)} clips with {args.a}")
    b = transcribe(model, paths, args.b, args.alpha)
    print(f"b: {len(b)} clips with {args.b}\n")
    # Both runs kept, so the scoring can be reworked without paying for the
    # transcription again.
    for run, label in ((a, "a"), (b, "b")):
        with open(os.path.join(args.work, f"run-{label}.json"), "w") as f:
            json.dump(run, f, indent=1)

    tally = {k: {"fix": 0, "cosmetic": 0, "regression": 0, "same": 0}
             for k in ("target", "bystander", "control")}
    notes = []
    for m in meta:
        wav, kind = m["wav"], m["kind"]
        if wav not in a or wav not in b:
            continue
        if kind == "target":
            t = m["term"].lower()
            ha, hb = t in a[wav].lower(), t in b[wav].lower()
            if hb and not ha:
                tally[kind]["fix"] += 1
            elif ha and not hb:
                tally[kind]["regression"] += 1
                notes.append(("target LOST", m["term"], a[wav], b[wav]))
            else:
                tally[kind]["same"] += 1
            continue
        if flat(a[wav]) == flat(b[wav]):
            tally[kind]["same"] += 1
            continue
        if spoken_same(a[wav], b[wav]):
            tally[kind]["cosmetic"] += 1
            continue
        what, plus, minus = classify(a[wav], b[wav], boosted)
        tally[kind][what] += 1
        if what == "regression":
            notes.append((f"{kind} worse", " ".join(minus)[:44],
                          a[wav], b[wav]))

    print(f"{'':<12}{'clips':>7}{'fixed':>8}{'cosmetic':>10}{'worse':>8}{'same':>7}")
    print("-" * 54)
    for kind in ("target", "bystander", "control"):
        t = tally[kind]
        n = sum(t.values())
        if n:
            print(f"{kind:<12}{n:>7}{t['fix']:>8}{t['cosmetic']:>10}"
                  f"{t['regression']:>8}{t['same']:>7}")
    for label, what, x, y in notes[:20]:
        print(f"\n  {label}: {what}")
        print(f"    a: {x[:120]}")
        print(f"    b: {y[:120]}")

    if args.save:
        with open(args.save, "w") as f:
            json.dump(b, f, indent=1)
        print(f"\nrun saved as a baseline: {args.save}")
    worse = sum(tally[k]["regression"] for k in tally)
    gained = sum(tally[k]["fix"] for k in tally)
    print(f"\n{gained} improved, {worse} worse across {len(meta)} clips")
    if args.gate and worse > args.gate:
        sys.exit(f"more than {args.gate} regressions")


def sweep(args):
    """How hard should the thumb press? One list, several alphas.

    BOOST_ALPHA was chosen by a sweep against 55 phrases. The list is 122 now
    and more than twice as much of it is arbitrary proper nouns, so the weight
    that balanced the old list is not obviously the one that balances this.

    Alpha 0 is the reference: the same list, no thumb at all. Targets and
    bystanders are scored absolutely - does the right word appear - and the
    damage is counted against that zero run, because a control that changes
    when boosting is off has changed for some other reason.
    """
    meta = json.load(open(os.path.join(args.work, "clips.json")))
    clips = os.path.join(args.work, "clips")
    paths = [os.path.join(clips, m["wav"]) for m in meta]
    boosted = {l.strip().lower() for l in open(args.b) if l.strip()}

    import nemo.collections.asr as nemo_asr
    model = nemo_asr.models.ASRModel.from_pretrained(
        "nvidia/parakeet-tdt-0.6b-v3").to(args.device).eval()

    runs = {}
    for alpha in args.alphas:
        runs[alpha] = transcribe(model, paths, args.b, alpha)
        with open(os.path.join(args.work, f"sweep-{alpha}.json"), "w") as f:
            json.dump(runs[alpha], f, indent=1)
        print(f"  alpha {alpha}: done", flush=True)

    zero = runs[args.alphas[0]]
    tgt = [m for m in meta if m["kind"] == "target"]
    bys = [m for m in meta if m["kind"] == "bystander"]
    ctl = [m for m in meta if m["kind"] == "control"]
    print(f"\n{'alpha':>6}{'targets':>12}{'bystanders':>14}"
          f"{'controls hurt':>15}{'net':>7}")
    print("-" * 56)
    best = None
    for alpha in args.alphas:
        r = runs[alpha]
        t = sum(1 for m in tgt if m["term"].lower() in r[m["wav"]].lower())
        b = sum(1 for m in bys if m["term"].lower() in r[m["wav"]].lower())
        hurt = 0
        for m in ctl + bys:
            w = m["wav"]
            if flat(zero[w]) == flat(r[w]) or spoken_same(zero[w], r[w]):
                continue
            what, _, _ = classify(zero[w], r[w], boosted)
            hurt += what == "regression"
        net = t + b - hurt
        flag = ""
        if best is None or net > best[1]:
            best, flag = (alpha, net), ""
        print(f"{alpha:>6}{t:>7}/{len(tgt):<4}{b:>9}/{len(bys):<4}"
              f"{hurt:>13}{net:>7}")
    print(f"\nbest net at alpha {best[0]}"
          f"  (net = targets + bystanders correct, minus controls hurt)")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="what", required=True)
    bl = sub.add_parser("build")
    bl.add_argument("--work", required=True)
    bl.add_argument("--per-variant", type=int, default=8)
    bl.add_argument("--per-bystander", type=int, default=2)
    bl.add_argument("--controls", type=int, default=250)
    bl.add_argument("--seed", type=int, default=11)
    cp = sub.add_parser("compare")
    cp.add_argument("--work", required=True)
    cp.add_argument("--a", help="the list to compare against")
    cp.add_argument("--b", required=True, help="the list under test")
    cp.add_argument("--baseline", help="a saved run to use instead of --a")
    cp.add_argument("--save", help="write this run out as a baseline")
    cp.add_argument("--alpha", type=float, default=1.0)
    cp.add_argument("--device", default="cuda:0")
    cp.add_argument("--gate", type=int, help="exit non-zero above this many")
    sw = sub.add_parser("sweep")
    sw.add_argument("--work", required=True)
    sw.add_argument("--b", required=True, help="the list to sweep alpha over")
    sw.add_argument("--alphas", type=float, nargs="+",
                    default=[0.0, 0.5, 1.0, 1.5, 2.0, 3.0])
    sw.add_argument("--device", default="cuda:0")
    args = ap.parse_args()
    if args.what == "build":
        build(args)
    elif args.what == "sweep":
        sweep(args)
    else:
        if not args.a and not args.baseline:
            sys.exit("compare needs --a or --baseline")
        compare(args)


if __name__ == "__main__":
    main()
