"""Does the voice say what the answer says?

    bin/eval_voice.py render          # emb-venv: speak the corpus
    bin/eval_voice.py score           # asr-venv: listen to it and report
    bin/eval_voice.py score --diff last.json    # against a previous run

TWO PROCESSES BECAUSE TWO VIRTUALENVS. Kokoro lives with the web server in
emb-venv and Parakeet lives in asr-venv, and they disagree about torch - the
same reason this project has three of them. The rendered wavs on disk are the
handoff.

WHY THE ARCHIVE'S OWN ASR IS THE JUDGE. A rule in web/say.py is a claim about
what a sentence will SOUND like, and the only way to test a claim about sound
is to listen. Parakeet already transcribes this archive's recordings, so it is
the same ear the rest of the pipeline trusts, and it is not the model being
tested - which is what makes it a check rather than a mirror.

WHAT IT MEASURES: whether every number and every initialism in a sentence is
still recoverable after being spoken and heard again. Not whether the audio is
pleasant. A voice that reads "163.3184" as "163.318" is the failure this
exists to catch, because it is the one that makes the archive say something
false, and it is silent.

WHAT IT CANNOT MEASURE. The ASR is an instrument with its own limits, and two
of them show up here as failures that are not failures: it transcribes spelled
initialisms poorly ("H-O-A's" comes back as "eight shows" though the phonemes
are right), and it runs adjacent numbers together on lines with no sentence
structure - purchase orders, parcel numbers - which no narration reads anyway.
Check a new failure against `tokenizer.phonemize` before writing a rule for
it. A rule against a phantom is a change to shipped behaviour for nothing.
"""
import argparse
import glob
import json
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "web"))

import db                                                     # noqa: E402


# ------------------------------------------------------------------ corpus
# What makes a sentence worth spending a render on. Each is a construct that
# has to survive being said out loud, and the name is what gets reported when
# it does not.
CONSTRUCTS = {
    "acronym": r"\b[A-Z]{2,6}s?\b",
    "code": r"\b[A-Z]{1,6}-\d+[A-Z]?\b",
    "decimal": r"\d+\.\d+",
    "money": r"\$[\d,]+",
    "thousands": r"\b\d{1,3},\d{3}\b",
    "vote": r"\b\d-\d\b",
    "hyphen-range": r"\b\d{2,}-\d{2,}\b",
    "ordinal": r"\b\d+(?:st|nd|rd|th)\b",
    "clock": r"\b\d{1,2}:\d{2}\b",
    "abbrev": r"\b[A-Z][a-z]{1,4}\.(?=\s|$)",
    "percent": r"\d+(?:\.\d+)?\s?%",
    "parens": r"\([^)]{1,40}\)",
    "quoted": r"[\"“]",
    "fraction": r"\b\d+/\d+\b",
    "plusminus": r"[+±]",
    "degrees": r"\b\d+\s?(?:degrees|°)",
}


def constructs(s):
    return sorted(n for n, p in CONSTRUCTS.items() if re.search(p, s))


def sentences(text):
    """Rough sentence split. Only has to be good enough to render one."""
    text = re.sub(r"\[(?:item:)?\d+\]", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    out, start = [], 0
    for m in re.finditer(r"(?<=[.!?])\s+(?=[A-Z0-9\"“])", text):
        out.append(text[start:m.start()].strip())
        start = m.end()
    if start < len(text):
        out.append(text[start:].strip())
    return [s for s in out if 25 <= len(s) <= 260]


def build(limit_per_construct=6, plain=25, seed=7):
    rnd = random.Random(seed)
    pool = []
    with db.connect() as con:
        for r in con.execute("SELECT answer FROM answers"):
            for s in sentences(r["answer"]):
                pool.append(("answer", s))
        # The same language, in bulk. Titles are one sentence by construction
        # and are dense in exactly the constructs answers use sparingly.
        for r in con.execute(
                "SELECT title, outcome_text FROM agenda_items "
                "WHERE title IS NOT NULL ORDER BY id LIMIT 6000"):
            for field in ("title", "outcome_text"):
                for s in sentences(r[field]):
                    pool.append(("record", s))

    seen, chosen, by = set(), [], {}
    # Answers first, so a construct that appears in a real answer beats the
    # same construct in a title nobody will ever hear read aloud.
    for source, s in sorted(pool, key=lambda p: p[0] != "answer"):
        key = s.lower()
        if key in seen:
            continue
        cs = constructs(s)
        if not cs:
            continue
        if all(len(by.get(c, [])) >= limit_per_construct for c in cs):
            continue
        seen.add(key)
        chosen.append({"text": s, "source": source, "has": cs})
        for c in cs:
            by.setdefault(c, []).append(s)

    # And ordinary prose, which is what most of an answer is. Without these a
    # rule that wrecks plain sentences to fix a code would look like a win.
    plainish = [s for src, s in pool if not constructs(s) and src == "answer"]
    plainish += [s for src, s in pool if not constructs(s)]
    for s in rnd.sample(plainish, min(plain, len(plainish))):
        if s.lower() in seen:
            continue
        seen.add(s.lower())
        chosen.append({"text": s, "source": "plain", "has": []})

    return chosen, by


# ----------------------------------------------------------------- the ear
UNITS = {"zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4,
         "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
         "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
         "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
         "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
        "seventy": 70, "eighty": 80, "ninety": 90}
SCALE = {"hundred": 100, "thousand": 1000, "million": 10 ** 6,
         "billion": 10 ** 9, "trillion": 10 ** 12}
DIGITS = {w: str(v) for w, v in UNITS.items() if v < 10}
ORDINAL = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
           "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
           "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
           "fifteenth": 15, "sixteenth": 16, "seventeenth": 17,
           "eighteenth": 18, "nineteenth": 19, "twentieth": 20,
           "thirtieth": 30, "fortieth": 40, "fiftieth": 50}


def _value(words):
    """A run of number words as one number, or None if it is not one."""
    total = run = 0
    seen = False
    for w in words:
        if w in UNITS:
            run += UNITS[w]
        elif w in TENS:
            run += TENS[w]
        elif w in ORDINAL:
            run += ORDINAL[w]
        elif w == "hundred":
            run = (run or 1) * 100
        elif w in SCALE:
            total += (run or 1) * SCALE[w]
            run = 0
        else:
            return None
        seen = True
    return total + run if seen else None


def _is_num_word(w):
    return w in UNITS or w in TENS or w in SCALE or w in ORDINAL


def to_digits(text, years=True):
    """A transcript with its number words turned back into numbers.

    Tokenised rather than split, and the punctuation is DROPPED rather than
    preserved: everything downstream squeezes the result anyway, and keeping
    it was the source of the worst bug in this file. A full stop glued to the
    last word of a number - "two thousand twenty four." - ended the run one
    word early and read as 2020, so a year that had been said perfectly was
    reported lost.

    Four readings have to survive, because the ASR produces all of them:

      "forty five"           one number, 45
      "three one eight four" a digit string, 3184 - how this archive says the
                             tail of a statute number
      "twenty twenty one"    a year in pairs, 2021, which an ordinary parse
                             reads as 40 and 1
      "twenty fourth"        a compound ordinal, 24, not 20 and then 4
    """
    # Punctuation is kept as its own token because it BREAKS a run, even
    # though it is never emitted. Dropped entirely, "april twenty, two
    # thousand twenty one" merged across the comma into 22021 - a date and a
    # year read as one impossible number.
    words = re.findall(r"[a-z]+|\d[\d.,]*|[^\sa-z0-9]", text.lower())
    BREAK = set(",;:!?()\"'")
    out, i = [], 0
    while i < len(words):
        if not _is_num_word(words[i]):
            if words[i] not in BREAK and words[i] != "-":
                out.append(words[i])
            i += 1
            continue
        run, j = [], i
        while j < len(words):
            t = words[j]
            # A joiner inside a run does not end it: "one hundred AND seventy"
            # is one number.
            if run and (t == "and" or t == "-"):
                j += 1
                continue
            if t in BREAK:
                break
            if t in ORDINAL:
                # An ordinal closes a run. It may complete one - "twenty
                # fourth" is the 24th - but nothing follows it into the same
                # number.
                if run and _value(run) is not None and _value(run) % 10 == 0:
                    run.append(t)
                    j += 1
                elif not run:
                    run.append(t)
                    j += 1
                break
            if _is_num_word(t) or (t == "point" and run):
                run.append(t)
                j += 1
            else:
                break
        while run and run[-1] == "point":
            run.pop()
            j -= 1
        if not run:
            out.append(words[i])
            i += 1
            continue

        if len(run) >= 2 and all(r in DIGITS for r in run):
            out.append("".join(DIGITS[r] for r in run))
        elif "point" in run:
            at = run.index("point")
            whole, frac = _value(run[:at]), run[at + 1:]
            tail = "".join(DIGITS[f] if f in DIGITS else str(_value([f]) or "")
                           for f in frac)
            out.append(f"{whole if whole is not None else ''}.{tail}")
        else:
            v = _value(run)
            # Never on a compound ordinal: "twenty fourth" is the 24th of the
            # month, and the year reading turns it into 2004.
            pair = _year(run) if years and run[-1] not in ORDINAL else None
            out.append(str(pair if pair is not None else v if v is not None else ""))
        i = j
    return " ".join(out)


def _year(run):
    """A year read as two pairs, or None."""
    for cut in range(1, len(run)):
        a, b = _value(run[:cut]), _value(run[cut:])
        if a is not None and b is not None and 10 <= a <= 99 and 0 <= b <= 99:
            return a * 100 + b
    return None


def squeeze(s):
    return re.sub(r"[^a-z0-9.]", "", s.lower())


def facts(src):
    """What has to come back: every number, and every initialism."""
    text = re.sub(r"\[(?:item:)?\d+\]", " ", src)
    nums = [n.replace(",", "") for n in
            re.findall(r"\d[\d,]*(?:\.\d+)?", text)]
    acr = [a for a in re.findall(r"\b([A-Z]{2,6})s?\b", text)]
    return nums, acr


def readings(heard):
    """Every canonical form of one transcript.

    SOME RUNS OF NUMBER WORDS ARE GENUINELY AMBIGUOUS. "twenty three fifteen"
    is ordinance 23-15 and it is also the year 2315, and no parse knows which
    without knowing what was in the source. Rather than guess and be wrong,
    every reading is generated and a fact counts as recovered if ANY of them
    holds it.

    That trades detection power for trustworthiness, deliberately. A false
    alarm here costs a rule written against a failure that never happened,
    which is worse than a miss: it is a change to shipped behaviour made for
    no reason.
    """
    return [squeeze(to_digits(heard, years=True)),
            squeeze(to_digits(heard, years=False)),
            squeeze(_as_sequence(heard)),
            squeeze(heard)]


def _as_sequence(text):
    """Number words read as a SEQUENCE of small numbers rather than as one.

    "twenty three fifteen" is ordinance 23-15 read the way a clerk reads it,
    and the arithmetic parse makes it 38 or the year 2018. Neither holds the
    two numbers that were in the source, so this reading keeps them apart:
    a tens word takes at most one unit after it, and everything else stands
    alone.
    """
    words = [w for w in re.split(r"[^a-z]+", text.lower()) if w]
    out, i = [], 0
    while i < len(words):
        w = words[i]
        if w in TENS and i + 1 < len(words) and words[i + 1] in UNITS \
                and UNITS[words[i + 1]] < 10:
            out.append(str(TENS[w] + UNITS[words[i + 1]]))
            i += 2
        elif w in UNITS:
            out.append(str(UNITS[w]))
            i += 1
        elif w in TENS:
            out.append(str(TENS[w]))
            i += 1
        elif w in ORDINAL:
            out.append(str(ORDINAL[w]))
            i += 1
        else:
            out.append(w)
            i += 1
    return " ".join(out)


def check(src, heard):
    """Numbers and initialisms in `src` that are not recoverable from `heard`."""
    nums, acr = facts(src)
    said = readings(heard)
    lost = []
    for n in nums:
        # A whole number, or the same digits with the decimal point spoken
        # away, or with a leading zero dropped.
        forms = {n, n.replace(".", ""), n.lstrip("0")}
        # "$13,950.00" said as "thirteen thousand nine hundred fifty dollars"
        # is CORRECT and complete - the cents are zero and nobody says them.
        # Without this the checker demands the digits "00" be audible and
        # marks the right reading as a loss.
        if re.fullmatch(r"\d+\.0{1,2}", n):
            forms.add(n.split(".")[0])
        # "$74,298.78" said as "...dollars and seventy eight cents" is the
        # correct reading and puts the two halves either side of two words.
        cents = re.fullmatch(r"(\d+)\.(\d{1,2})", n)
        if cents:
            forms.add(f"{cents.group(1)}dollarsand{cents.group(2).lstrip('0') or '0'}")
            forms.add(f"{cents.group(1)}dollarsand{cents.group(2)}")
        if not any(f and any(f in r for r in said) for f in forms):
            lost.append(("number", n))
    for a in acr:
        if not any(a.lower() in r for r in said):
            lost.append(("initials", a))
    return lost


# ------------------------------------------------------------------ render

def render(args):
    """Speak the corpus. Needs emb-venv."""
    import espeakng_loader
    import say
    from kokoro_onnx import EspeakConfig, Kokoro

    root = os.path.dirname(HERE)
    cases, _ = build()
    k = Kokoro(os.path.join(say.MODEL_DIR, "kokoro-v1.0.onnx"),
               os.path.join(say.MODEL_DIR, "voices-v1.0.bin"),
               espeak_config=EspeakConfig(
                   lib_path=espeakng_loader.get_library_path(),
                   data_path=espeakng_loader.get_data_path()))
    os.makedirs(args.out, exist_ok=True)
    for stale in glob.glob(os.path.join(args.out, "*.wav")):
        os.remove(stale)
    meta, spent, heard = [], 0.0, 0.0
    for i, c in enumerate(cases):
        said = say.spoken(c["text"])
        t0 = time.time()
        audio, sr = k.create(said, voice=say.VOICE, lang="en-us")
        spent += time.time() - t0
        heard += len(audio) / sr
        with open(os.path.join(args.out, f"{i:03d}.wav"), "wb") as f:
            f.write(say.wav(audio, sr))
        meta.append({"i": i, "wav": f"{i:03d}.wav", "text": c["text"],
                     "said": said, "has": c["has"], "source": c["source"]})
    with open(os.path.join(args.out, "meta.json"), "w") as f:
        json.dump(meta, f, indent=1)
    print(f"{len(cases)} sentences, {heard:.0f}s of audio in {spent:.0f}s "
          f"(RTF {spent / heard:.3f}) -> {args.out}")
    print(f"now: asr-venv/bin/python {sys.argv[0]} score")


# ------------------------------------------------------------------- score

def score(args):
    """Listen, and report what did not survive. Needs asr-venv."""
    meta = json.load(open(os.path.join(args.out, "meta.json")))
    cache = os.path.join(args.out, "heard.json")
    if os.path.exists(cache) and not args.again:
        heard = json.load(open(cache))
    else:
        import nemo.collections.asr as nemo_asr
        m = nemo_asr.models.ASRModel.from_pretrained(
            "nvidia/parakeet-tdt-0.6b-v3").to(args.device).eval()
        paths = [os.path.join(args.out, r["wav"]) for r in meta]
        outs = m.transcribe(paths, batch_size=8)
        heard = {r["wav"]: (o.text if hasattr(o, "text") else str(o))
                 for r, o in zip(meta, outs)}
        with open(cache, "w") as f:
            json.dump(heard, f, indent=1)

    bad, whole = [], []
    for r in meta:
        lost = check(r["text"], heard[r["wav"]])
        (bad if lost else whole).append((r, lost))
    n = len(meta)
    print(f"\n{len(whole)}/{n} sentences came back whole  "
          f"({100 * len(whole) / n:.0f}%)\n")
    for src in ("answer", "record", "plain"):
        rs = [r for r in meta if r["source"] == src]
        if rs:
            ok = sum(1 for r in rs if not check(r["text"], heard[r["wav"]]))
            print(f"  {src:<8} {ok:>3}/{len(rs):<3} {100 * ok / len(rs):>3.0f}%")

    for r, lost in bad:
        print(f"\n[{r['i']:03d}] {','.join(r['has']) or 'plain'}  LOST {lost}")
        print(f"  text : {r['text'][:160]}")
        print(f"  said : {r['said'][:160]}")
        print(f"  heard: {heard[r['wav']][:160]}")

    now = {r["text"]: bool(lost) for r, lost in bad + [(r, []) for r, _ in whole]}
    if args.diff and os.path.exists(args.diff):
        was = json.load(open(args.diff))
        broke = [t for t, failed in now.items() if failed and was.get(t) is False]
        fixed = [t for t, failed in now.items() if not failed and was.get(t) is True]
        print(f"\n{'=' * 70}\nagainst {args.diff}: "
              f"{len(fixed)} fixed, {len(broke)} REGRESSED")
        for t in broke:
            print(f"  REGRESSED: {t[:110]}")
        for t in fixed:
            print(f"  fixed:     {t[:110]}")
        if broke:
            sys.exit(1)
    if args.save:
        with open(args.save, "w") as f:
            json.dump(now, f, indent=1)
        print(f"\nbaseline written to {args.save}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["render", "score"])
    ap.add_argument("--out", default="/tmp/eval_voice",
                    help="where the wavs and transcripts live")
    ap.add_argument("--again", action="store_true", help="re-transcribe")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--diff", help="a saved baseline to compare against; "
                                   "exits non-zero if anything regressed")
    ap.add_argument("--save", help="write this run as the new baseline")
    args = ap.parse_args()
    (render if args.what == "render" else score)(args)


if __name__ == "__main__":
    main()
