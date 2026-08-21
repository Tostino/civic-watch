#!/usr/bin/env python
"""The words this county uses that nothing else does.

    bin/lexicon.py check              # what the archive holds vs what is here
    bin/lexicon.py mine               # propose entries from the record
    bin/lexicon.py never              # words never once spelled correctly
    bin/lexicon.py phrases --out bin/phrases.txt   # refresh the boost list

TWO CONSUMERS, ONE LIST, AND THEY NEED DIFFERENT THINGS FROM IT.

The ASR needs SPELLINGS. It has the audio and no idea that "Anclote" is a
river, so it writes "anclope"; giving it the word is enough. What it needs is
mined from the archive's own transcripts (`bin/lexicon.py mine`), because the
mistakes it actually makes on real meeting audio are a fact about this
archive, not something to be guessed at: `severability` is written four
different wrong ways 73 times, and `DeCubellis` has never once been written
correctly in 1,036 hours.

The TTS needs PRONUNCIATIONS, which is a different kind of knowledge and one
the archive does not contain. Nobody has ever recorded that Pinellas is
"pih-NELL-us". So `ipa` is filled in only where the pronunciation is public
knowledge, and `bin/lexicon.py check` prints what each one actually sounds
like so it is never taken on trust.

WHY IPA AND NOT A RESPELLING. The first version of this file held things like
"pih-nellus" and "with-la-coochee" - text rewritten to trick the phonemiser
into the right sound. That works until it does not: no respelling of Aripeka
produced air-ih-PEE-kuh, because the trick has to route through English
spelling rules that have their own opinion. Saying it in IPA says it exactly,
and it turns out the synthesiser accepts phonemes directly. A sentence
phonemised whole and a sentence rendered from text produce byte-identical
audio, so nothing is risked by going through phonemes always.

WHY THERE ARE ALMOST NO PEOPLE HERE. Person names are where the round-trip
test fails most (110 of 126 failures) and where this file can help least. The
archive knows how a name is SPELLED and not how its owner says it, and a
confidently wrong pronunciation of a real person's name is the exact failure
this project is most careful about everywhere else. Names get `heard` entries,
which are evidence, and no `say` entry, which would be invention.
"""
import argparse
import difflib
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "web"))

# term:  as the record spells it, and what a reader should see
# ipa:   how it is said, in the phonemes the synthesiser speaks, or None.
#        Spliced into the sentence in place of whatever the phonemiser would
#        have guessed. Only where the pronunciation is actually known.
# heard: what the ASR writes instead, observed in this archive's own
#        transcripts. The number beside each is how often, at the time it was
#        mined, and is a note rather than a threshold.
ENTRIES = [
    {"term": 'Weightman', "ipa": None},
    {"term": 'Seth Weightman', "ipa": None},
    {"term": 'Commissioner Weightman', "ipa": None},
    {"term": 'Oakley', "ipa": None},
    {"term": 'Ron Oakley', "ipa": None},
    {"term": 'Commissioner Oakley', "ipa": None},
    {"term": 'Starkey', "ipa": None},
    {"term": 'Kathryn Starkey', "ipa": None},
    {"term": 'Commissioner Starkey', "ipa": None},
    {"term": 'Yeager', "ipa": None},
    {"term": 'Lisa Yeager', "ipa": None},
    {"term": 'Commissioner Yeager', "ipa": None},
    {"term": 'Mariano', "ipa": None},
    {"term": 'Jack Mariano', "ipa": None},
    {"term": 'Chairman Mariano', "ipa": None},
    {"term": 'Pasco', "ipa": None},
    {"term": 'Pasco County', "ipa": None},
    {"term": 'New Port Richey', "ipa": None},
    {"term": 'Port Richey', "ipa": None},
    {"term": 'Zephyrhills', "ipa": None},
    {"term": "Land O' Lakes", "ipa": None},
    {"term": 'Wesley Chapel', "ipa": None},
    {"term": 'Dade City', "ipa": None},
    {"term": 'Shady Hills', "ipa": None},
    {"term": 'Bayonet Point', "ipa": None},
    {"term": 'Hudson', "ipa": None},
    {"term": 'Odessa', "ipa": 'oʊdˈɛsə',
     "note": 'stress on the second syllable, not the first'},
    {"term": 'Trinity', "ipa": None},
    {"term": 'Elfers', "ipa": None},
    {"term": 'Saint Leo', "ipa": None},
    {"term": 'Lutz', "ipa": None},
    {"term": 'Board of County Commissioners', "ipa": None},
    {"term": 'County Administrator', "ipa": None},
    {"term": 'County Attorney', "ipa": None},
    {"term": 'Madam Clerk', "ipa": None},
    {"term": 'Penny for Pasco', "ipa": None},
    {"term": 'Land Development Code', "ipa": None},
    {"term": 'comprehensive plan', "ipa": None},
    {"term": 'quasi-judicial', "ipa": None},
    {"term": 'right-of-way', "ipa": None},
    {"term": 'impact fee', "ipa": None},
    {"term": 'millage', "ipa": None},
    {"term": 'ad valorem', "ipa": None, "heard": ['ad valorum']},
    {"term": 'ordinance', "ipa": None},
    {"term": 'resolution', "ipa": None},
    {"term": 'proclamation', "ipa": None},
    {"term": 'consent agenda', "ipa": None},
    {"term": 'public hearing', "ipa": None},
    {"term": 'rezoning', "ipa": None},
    {"term": 'easement', "ipa": None},
    {"term": 'variance', "ipa": None},
    {"term": 'stormwater', "ipa": None},
    {"term": 'wastewater', "ipa": None},
    {"term": 'Brusselback', "ipa": None},
    {"term": 'Adam Brusselback', "ipa": None},
    {"term": 'Anclote', "ipa": None, "heard": ['anclope']},
    {"term": 'Aripeka', "ipa": 'ˌæɹɪpˈiːkə',
     "note": "the phonemiser said ARR-eye-pka; it is air-ih-PEE-kuh. No "
             "respelling reached this, which is why the field is phonemes"},
    {"term": 'Blanton', "ipa": None, "heard": ['blandon']},
    {"term": 'DeCubellis', "ipa": None, "heard": ['decubelus', 'decubelis'],
     "note": 'never once written correctly in the transcripts'},
    {"term": 'Fasano', "ipa": None, "heard": ['fassano']},
    {"term": 'Gassaway', "ipa": None, "heard": ['gasway']},
    {"term": 'Hayswood', "ipa": None, "heard": ['hayeswood']},
    {"term": 'Hazelwood', "ipa": None, "heard": ['hazewood']},
    {"term": 'Hillsborough', "ipa": None, "heard": ['hillsboro']},
    {"term": 'Pinellas', "ipa": 'pɪnˈɛləs', "heard": ['penellas'],
     "note": 'the phonemiser said pie-NELL-us'},
    {"term": 'Pontlitz', "ipa": None, "heard": ['pontletz']},
    {"term": 'Pulte', "ipa": 'pˈʊlti', "heard": ['palt'],
     "note": "the phonemiser said 'pult'"},
    {"term": 'Saddlebrook', "ipa": None, "heard": ['saddlebook']},
    {"term": 'Tanglewood', "ipa": None, "heard": ['tangowood']},
    {"term": 'Wilhite', "ipa": None, "heard": ['wilheit', 'wilhide']},
    {"term": 'Withlacoochee', "ipa": 'wˌɪθləkˈuːtʃi',
     "note": 'the phonemiser stressed LACK; it is with-luh-COO-chee'},
    {"term": 'plat', "ipa": None, "heard": ['platt']},
    {"term": 'severability', "ipa": None,
     "heard": ['serverability', 'servability', 'severality', 'separability'],
     "note": '73 wrong against 251 right: the worst in the archive'},
    {"term": 'WebEx', "ipa": None},
    {"term": 'Richey', "ipa": None},
    {"term": 'Hernando', "ipa": None},
    {"term": 'Wiregrass', "ipa": None},
    {"term": 'Epperson', "ipa": None},
    {"term": 'MPUDs', "ipa": None},
    {"term": 'Connerton', "ipa": None},
    {"term": 'AmSkills', "ipa": None},
    {"term": 'Moffitt', "ipa": None},
    {"term": 'Trilby', "ipa": None},
    {"term": 'Talavera', "ipa": None},
    {"term": 'Lakeshore', "ipa": None},
    {"term": 'Intergovernmental', "ipa": None},
    {"term": 'Caliente', "ipa": None},
    {"term": 'Schrader', "ipa": None},
    {"term": 'McCabe', "ipa": None},
    {"term": 'Aristida', "ipa": None},
    {"term": 'Alvernon', "ipa": None},
    {"term": 'ELAMP', "ipa": None},
    {"term": 'Stantec', "ipa": None},
    {"term": 'Chasco', "ipa": None},
    {"term": 'Deerbrook', "ipa": None},
    {"term": 'Eagleston', "ipa": None},
    {"term": 'Lanier', "ipa": None},
    {"term": 'Hadlock', "ipa": None},
    {"term": 'Hagman', "ipa": None},
    {"term": 'Mabry', "ipa": None},
    {"term": 'Lindrick', "ipa": None},
    {"term": 'Lakeview', "ipa": None},
    {"term": 'Gulfside', "ipa": None},
    {"term": 'Lacoochee', "ipa": 'lˌækˈuːtʃi',
     "heard": ['lacouche', 'lakouche', 'lakouchee', 'lacuchee', 'lakuchi',
               'likuchi'],
     "note": "a community in the north of the county; same root as "
             "Withlacoochee and said the same way"},
    {"term": 'Sakelson', "ipa": None, "heard": ['sackleson', 'sackelson', 'sacelson']},
    {"term": 'Eiland', "ipa": None, "heard": ['eland', 'elant']},
    {"term": 'Malacos', "ipa": None,
     "heard": ['melicose', 'malicose', 'mellicos', 'melicos', 'malauca']},
    {"term": 'Ardurra', "ipa": None, "heard": ['ardura', 'arduro']},
    {"term": 'NaphCare', "ipa": None, "heard": ['nafcare', 'nafcar']},
    {"term": 'Mishorim', "ipa": None, "heard": ['mishram']},
    {"term": 'Kokolakis', "ipa": None, "heard": ['kokalakis', 'cocalakis']},
    {"term": 'Pomello', "ipa": None, "heard": ['pomelo']},
    {"term": 'Neamataud', "ipa": None,
     "heard": ['nematod', 'nimatao', 'niamatau', 'mimataud']},
    {"term": 'Scoyoc', "ipa": None, "heard": ['scoyck', 'skoyak']},
    {"term": 'Ryals', "ipa": None, "heard": ['ryles']},
    {"term": 'Schaer', "ipa": None,
     "heard": ['scheer', 'scherer', 'schurer', 'schreur']},
    {"term": 'Perrine', "ipa": None, "heard": ['praine', 'perrone', 'pirine']},
    {"term": 'Blount', "ipa": None, "heard": ['blant', 'blanta']},
    {"term": 'Broeck', "ipa": None, "heard": ['broaks', 'brecke']},
    {"term": 'Merion', "ipa": None, "heard": ['merriman', 'merrima', 'merrion']},
    {"term": 'Speros', "ipa": None, "heard": ['sprouse', 'spros']},
    {"term": 'Volanti', "ipa": None, "heard": ['volunte']},
]

def _word(s):
    return re.compile(rf"\b{re.escape(s)}\b", re.IGNORECASE)


_SAID = {e["term"].lower(): e["ipa"] for e in ENTRIES if e.get("ipa")}
_HEARD = [(_word(h), e["term"]) for e in ENTRIES for h in e.get("heard") or []]
# Longest first, so "Withlacoochee River" cannot be claimed by a shorter term
# that happens to sit inside it.
_SAYS = re.compile("|".join(re.escape(t) for t in
                            sorted(_SAID, key=len, reverse=True)) or r"(?!)",
                   re.IGNORECASE)


def phonemes(text, phonemise):
    """The sentence in phonemes, with this county's own words said properly.

    `phonemise` turns ordinary text into phonemes; it is passed in because the
    phonemiser belongs to the synthesiser and this file must not import one.

    Everything OUTSIDE a lexicon term goes through it untouched, which is what
    makes this safe to apply to every sentence: a sentence with no local word
    in it comes out exactly as it would have anyway, byte for byte.
    """
    out, at = [], 0
    for m in _SAYS.finditer(text):
        if m.start() > at:
            out.append(phonemise(text[at:m.start()]))
        out.append(_SAID[m.group(0).lower()])
        at = m.end()
    if at < len(text):
        out.append(phonemise(text[at:]))
    return " ".join(p for p in out if p)


def fingerprint():
    """What the pronunciations are, as one short string.

    Goes into the audio cache key in web/say.py. Without it, correcting a
    pronunciation here would leave every sentence that uses that word serving
    the old recording for ever - the cache is content-addressed on the text,
    and the text does not change when this file does.
    """
    import hashlib
    h = hashlib.sha256()
    for term in sorted(_SAID):
        h.update(f"{term}={_SAID[term]}\0".encode())
    return h.hexdigest()[:12]


def corrections(text):
    """Every correction this lexicon would make, as (start, end, was, now).

    RETURNS THE EDITS, NOT A CORRECTED STRING, and that is the whole point of
    the shape. A transcript is the archive's evidence for what was said; a
    correction is a claim ABOUT that evidence. Handing back a rewritten string
    invites a caller to store it over the original, and then the archive has
    quietly changed what somebody said with nothing left to check against.

    The caller keeps both, shows the corrected text, and can always put the
    ASR's own words back in front of a reader.
    """
    out = []
    for pattern, term in _HEARD:
        for m in pattern.finditer(text or ""):
            was = m.group(0)
            now = term if was[:1].isupper() or term[:1].isupper() else term.lower()
            out.append((m.start(), m.end(), was, now))
    return sorted(out)


def phrases():
    """Every term, in file order, for an ASR that can be told what words exist.

    THIS IS THE ONLY LIST. bin/phrases.txt used to be a second one, edited by
    hand beside this file, and within a day the two had 82 terms between them
    that the other had never heard of - the exact drift the header of this
    file warns about. bin/asr_worker.py builds its boost file from here now,
    so there is nothing left to keep in step.

    File order, not sorted: the entries are grouped by what they are, and a
    person reading the boost list should see the same grouping.
    """
    seen, out = set(), []
    for e in ENTRIES:
        k = e["term"].lower()
        if k not in seen:
            seen.add(k)
            out.append(e["term"])
    return out


# The old name. Kept because `hotwords` is what an ASR calls these.
hotwords = phrases


def write_phrases(path):
    """Materialise the boost list. Returns the number written."""
    terms = phrases()
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(terms) + "\n")
    return len(terms)


# ------------------------------------------------------------------ tools

def check(args):
    """Phonemise each `say` against its term, so a respelling is never taken
    on trust. Needs emb-venv."""
    import espeakng_loader
    import say as tts
    from kokoro_onnx import EspeakConfig, Kokoro
    k = Kokoro(os.path.join(tts.MODEL_DIR, "kokoro-v1.0.onnx"),
               os.path.join(tts.MODEL_DIR, "voices-v1.0.bin"),
               espeak_config=EspeakConfig(
                   lib_path=espeakng_loader.get_library_path(),
                   data_path=espeakng_loader.get_data_path()))
    print(f"{len(ENTRIES)} entries, "
          f"{sum(1 for e in ENTRIES if e.get('ipa'))} with a pronunciation "
          f"(fingerprint {fingerprint()})\n")
    for e in ENTRIES:
        raw = k.tokenizer.phonemize(e["term"], lang="en-us")
        if e.get("ipa"):
            print(f"  {e['term']:<16} was {raw}")
            print(f"  {'':<16} now {e['ipa']}")
        else:
            print(f"  {e['term']:<16} {raw}")
        if e.get("note"):
            print(f"  {'':<16} ({e['note']})")
        if e.get("heard"):
            print(f"  {'':<16} heard as: {', '.join(e['heard'])}")
        print()


def mine(args):
    """Propose entries: words the ASR writes that are near a word it should.

    Nothing here is added automatically. A near-miss is a candidate and the
    judgement about whether two spellings are the same word belongs to a
    person who knows the county.
    """
    import difflib
    from collections import Counter
    import db


    with open("/usr/share/dict/american-english", encoding="utf-8",
              errors="ignore") as f:
        known = {w.strip().lower() for w in f if w.strip()}

    def unknown(w):
        w = w.lower().strip("'")
        if not w or w in known:
            return False
        return not any(len(w) > c and (w[:-c] + a) in known
                       for c, a in ((1, ""), (2, ""), (1, "y"), (2, "e"), (3, "")))

    have = {t.lower() for t in hotwords()}
    with db.connect() as con:
        names = {r["surname"].lower() for r in con.execute(
            "SELECT surname FROM people WHERE surname IS NOT NULL")
            if r["surname"] and len(r["surname"]) > 3}
        seen = Counter()
        for r in con.execute("SELECT text FROM utterances"):
            for w in re.findall(r"[A-Za-z']{4,}", r["text"] or ""):
                if unknown(w):
                    seen[w.lower()] += 1

    target = (names | have) & set(seen) | names
    out = []
    for tok, n in seen.most_common(args.scan):
        if tok in target or n < args.floor:
            continue
        best, score = None, 0.0
        for t in target:
            if abs(len(t) - len(tok)) > 3 or t[0] != tok[0]:
                continue
            s = difflib.SequenceMatcher(None, t, tok).ratio()
            if s > score:
                best, score = t, s
        if best and score >= 0.85:
            out.append((n, tok, best, seen.get(best, 0), score))
    out.sort(reverse=True)
    print(f"{len(out)} candidates. Already in this file: "
          f"{len([o for o in out if o[2] in have])}\n")
    print(f"  {'the ASR wrote':<20} {'x':>6}  {'probably':<20} {'x':>6}  sim")
    for n, tok, best, bn, score in out[:args.top]:
        mark = " " if best in have else "+"
        print(f" {mark}{tok:<20} {n:>6}  {best:<20} {bn:>6}  {score:.2f}")


# ------------------------------------------------- the ones never spelled right

def _english():
    for path in ("/usr/share/dict/american-english", "/usr/share/dict/words"):
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                return {w.strip().lower() for w in f if w.strip()}
        except OSError:
            continue
    return set()


_KNOWN = None


def ordinary(w):
    """English has this word, allowing for the obvious inflections."""
    global _KNOWN
    if _KNOWN is None:
        _KNOWN = _english()
    w = w.lower().strip("'")
    if w in _KNOWN:
        return True
    return any(len(w) > c and (w[:-c] + a) in _KNOWN
               for c, a in ((1, ""), (2, ""), (1, "y"), (2, "e"), (3, "")))


# Consonants that trade places in a mishearing, collapsed to one symbol each.
_FOLD = str.maketrans({"k": "c", "q": "c", "s": "c", "z": "c", "x": "c",
                       "g": "j", "v": "f", "w": "f", "y": "i", "d": "t",
                       "b": "p", "m": "n"})


def key(word):
    """What a word sounds like, roughly, with the vowels thrown away."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return ""
    w = w.replace("ph", "f").replace("ck", "c").replace("gh", "")
    head = w[0]
    body = re.sub(r"[aeiou]", "", w[1:])
    body = body.translate(_FOLD)
    body = re.sub(r"(.)\1+", r"\1", body)
    return head.translate(_FOLD) + body


def never(args):
    import db

    floor = args.floor
    with db.connect() as con:
        # THE CORRECT SPELLINGS, from what the county published and from the
        # roster. Neither has been through an ASR.
        written = Counter()
        for r in con.execute("SELECT title FROM agenda_items "
                             "WHERE title IS NOT NULL"):
            for m in re.finditer(r"\b[A-Z][a-zA-Z']{3,}\b", r["title"]):
                w = m.group(0)
                if not ordinary(w):
                    written[w] += 1
        people = set()
        for r in con.execute("SELECT surname, full_name FROM people"):
            for n in (r["surname"], r["full_name"]):
                for part in re.findall(r"[A-Za-z']{4,}", n or ""):
                    if not ordinary(part):
                        people.add(part)

        # WHAT THE TRANSCRIPTS ACTUALLY HOLD.
        spoken = Counter()
        for r in con.execute("SELECT text FROM utterances"):
            for w in re.findall(r"[A-Za-z']{4,}", r["text"] or ""):
                spoken[w.lower()] += 1

    # Every out-of-vocabulary token the ASR wrote, bucketed by sound.
    shadow = defaultdict(list)
    for w, n in spoken.items():
        if not ordinary(w):
            shadow[key(w)].append((w, n))

    # A VARIANT THAT IS ITSELF A REAL WORD IN THE RECORD IS NOT A MISHEARING.
    # Sounding alike is not enough: "Suncoast", "Caliente", "Kiefer" and
    # "Municode" all sound like something else on this list and all are
    # things the county actually writes down. Only tokens the record has
    # never heard of can be the wreckage of a word it has.
    real = {w.lower() for w in written} | {p.lower() for p in people}

    candidates = {w: n for w, n in written.items() if n >= 3}
    for p in people:
        candidates.setdefault(p, 0)

    found = []
    for term, n_written in candidates.items():
        tl = term.lower()
        if spoken.get(tl):
            continue                       # it gets written correctly sometimes
        near = [(w, c) for w, c in shadow.get(key(term), [])
                if w != tl and abs(len(w) - len(tl)) <= 4
                and w not in real
                # Sound alone matched "Keator" to "cetera" 522 times. A
                # mishearing keeps most of the letters as well as the shape.
                and difflib.SequenceMatcher(None, tl, w).ratio() >= 0.6]
        # A "mishearing" said far more often than the word it supposedly
        # mangles is a word in its own right that the dictionary happens to
        # lack. "Pervious" - as in pervious pavement - appeared 68 times
        # against 4 mentions of Purvis, and is ordinary stormwater vocabulary.
        near = [(w, c) for w, c in near
                if not (c > 20 and n_written and c > 10 * n_written)]
        heard = sum(c for _, c in near)
        if heard >= floor:
            found.append((heard, term, n_written,
                          sorted(near, key=lambda x: -x[1])))
    found.sort(reverse=True)

    print(f"{len(found)} words the transcripts never spell correctly "
          f"(at least {floor} mishearings each)\n")
    print(f"  {'the word':<20}{'in record':>10}{'heard':>7}   written instead as")
    print("  " + "-" * 86)
    for heard, term, n_written, near in found[:60]:
        variants = ", ".join(f"{w}:{c}" for w, c in near[:5])
        print(f"  {term:<20}{n_written:>10}{heard:>7}   {variants[:56]}")




def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="what", required=True)
    sub.add_parser("check")
    m = sub.add_parser("mine")
    m.add_argument("--scan", type=int, default=6000)
    m.add_argument("--floor", type=int, default=8)
    m.add_argument("--top", type=int, default=40)
    sub.add_parser("hotwords")
    w = sub.add_parser("phrases")
    w.add_argument("--out", help="write the boost list here")
    n = sub.add_parser("never")
    n.add_argument("--floor", type=int, default=3,
                   help="fewest mishearings to report a word on")
    args = ap.parse_args()
    if args.what == "hotwords":
        print("\n".join(phrases()))
    elif args.what == "phrases":
        if args.out:
            print(f"{write_phrases(args.out)} phrases -> {args.out}")
        else:
            print("\n".join(phrases()))
    elif args.what == "check":
        check(args)
    elif args.what == "never":
        never(args)
    else:
        mine(args)


if __name__ == "__main__":
    main()
