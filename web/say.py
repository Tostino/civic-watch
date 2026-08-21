"""Reading an answer aloud, in a voice this archive runs itself.

WHY A LOCAL MODEL AND NOT THE BROWSER'S. The browser's own synthesiser costs
nothing and is available everywhere, which is why this started there. It is
also, on a plain Linux desktop, espeak: a 1980s formant synthesiser that makes
a county commissioner's argument sound like a fire alarm reading a receipt.
The point of narrating an answer is that the reader follows it, and nobody
follows that voice for ninety seconds.

WHY KOKORO. It is 82M parameters under Apache 2.0, and the decisive property
is not its size but its runtime: `kokoro-onnx` needs onnxruntime and numpy and
NOT torch. This project keeps three virtualenvs precisely because NeMo and
pyannote cannot agree on a torch pin, so a fourth model that dragged in a
fourth opinion about torch would have been a new venv and a new conflict
surface. This one drops into emb-venv, which already runs this server.
Measured on this box: 0.5s to load, and 7x faster than real time on the CPU,
so it never touches the GPU the query encoder is on.

WHY IT IS CACHED ON DISK AND NOT SYNTHESISED PER LISTEN. An answer is
immutable and already has a public URL. The audio for a sentence is therefore
a pure function of that sentence, and the second listener to any answer pays
nothing. Content-addressed rather than keyed by answer id, because the same
sentence in two answers is the same audio, and because a re-rendered answer
must not serve a stale recording of a sentence it no longer contains.
"""
import hashlib
import io
import os
import re
import sys
import threading
import wave

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))

# The county's own words. Kept in bin/ with the pipeline rather than here,
# because the ASR needs the same list and for the opposite reason: this file
# uses it to SAY a word correctly, and the transcription side uses it to
# recognise one. One list, or the two halves drift and the archive says a
# name one way and writes it another.
import lexicon                                        # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Where the weights are. Not bundled in the image: 338 MB that changes never,
# mounted like HF_HOME beside it, and FETCHED THE SAME WAY - on first use,
# into the volume, by the process that needs it.
#
# That last part is the whole design and it was wrong at first. The weights
# arrived by a script somebody had to remember to run, which meant a correct
# deploy with an empty volume produced an archive that could not read aloud
# and gave no hint why. Every other model in this project downloads itself:
# HF_HOME exists to CACHE the embedding model, not to be filled by hand.
MODEL_DIR = os.environ.get("SAY_MODEL_DIR") or os.path.join(ROOT, "models", "kokoro")

# Where they come from, and the opt-out. Set SAY_AUTOFETCH=0 on a host that
# must not reach the network; the surface then reports itself unavailable
# rather than hanging on a connection it will never get.
WEIGHTS = ("https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
           "model-files-v1.0")
FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")
AUTOFETCH = (os.environ.get("SAY_AUTOFETCH") or "1").lower() not in ("0", "false", "no")

# Rendered audio, kept between restarts. Under data/ with everything else this
# archive derives rather than authors.
CACHE_DIR = os.environ.get("SAY_CACHE_DIR") or os.path.join(ROOT, "data", "said")

# `af_heart` is Kokoro's one grade-A voice and reads like a newsreader, which
# is the register this archive already writes in. Changeable without a deploy
# because it is the kind of thing that has to be listened to to be judged.
VOICE = os.environ.get("SAY_VOICE") or "af_heart"
SPEED = float(os.environ.get("SAY_SPEED") or 1.0)

# 64 kbit mono. A sentence is about four seconds, so a chunk is roughly 32 KB
# and a whole answer well under a megabyte - against 4.3 MB for the same
# answer as the 24 kHz WAV the model emits. MP3 rather than Opus because the
# runtime image has no ffmpeg and `lameenc` is a self-contained wheel; the
# difference at this bitrate is inaudible under a synthesised voice.
BITRATE = int(os.environ.get("SAY_BITRATE") or 64)

# One chunk of an answer. Well past the 180 characters the reader splits on,
# and short of anything that could be used to render a book.
MAX_CHARS = int(os.environ.get("SAY_MAX_CHARS") or 400)

_lock = threading.Lock()
_voice = None
_error = None


def have_weights():
    return all(os.path.exists(os.path.join(MODEL_DIR, f)) for f in FILES)


def available():
    """Whether this archive can speak, without loading anything to find out.

    True when the weights are merely FETCHABLE, not only when they are already
    here: an empty volume on a host with a network is a slow first request,
    not an archive without a voice.
    """
    if _voice is not None:
        return True
    if _error is not None:
        return False
    return have_weights() or AUTOFETCH


def fetch():
    """Put the weights where MODEL_DIR says. Caller holds `_lock`.

    Written beside and renamed, so a container killed mid-download leaves no
    half a model behind for the next boot to load and fail on.
    """
    import shutil
    import urllib.request
    os.makedirs(MODEL_DIR, exist_ok=True)
    for name in FILES:
        dst = os.path.join(MODEL_DIR, name)
        if os.path.exists(dst):
            continue
        tmp = f"{dst}.{os.getpid()}.part"
        print(f"[say] fetching {name} into {MODEL_DIR}", flush=True)
        with urllib.request.urlopen(f"{WEIGHTS}/{name}", timeout=120) as r, \
                open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        os.replace(tmp, dst)
    print("[say] weights ready", flush=True)


def voice():
    """The model, loaded once. Raises Unavailable if it cannot be."""
    global _voice, _error
    with _lock:
        if _voice is not None:
            return _voice
        if _error is not None:
            raise Unavailable(_error)
        try:
            if not have_weights():
                if not AUTOFETCH:
                    raise RuntimeError(
                        f"no weights in {MODEL_DIR} and SAY_AUTOFETCH is off")
                fetch()
            import espeakng_loader
            from kokoro_onnx import EspeakConfig, Kokoro
            # espeak-ng ships as a WHEEL here, not as a system package. That is
            # what keeps this off the container's apt list: the phonemiser is a
            # shared library inside site-packages, found the same way in the
            # image as on this workstation.
            _voice = Kokoro(
                os.path.join(MODEL_DIR, "kokoro-v1.0.onnx"),
                os.path.join(MODEL_DIR, "voices-v1.0.bin"),
                espeak_config=EspeakConfig(
                    lib_path=espeakng_loader.get_library_path(),
                    data_path=espeakng_loader.get_data_path()))
            return _voice
        except Exception as e:                                # noqa: BLE001
            # Remembered, so a missing model is one log line at first use and
            # not one per sentence per reader for the life of the process.
            _error = f"{type(e).__name__}: {e}"
            print(f"[say] unavailable - {_error}", flush=True)
            raise Unavailable(_error) from None


class Unavailable(Exception):
    """No voice. The caller reports it; it does not degrade to silence."""


# ------------------------------------------------------------- what it says
#
# EVERY RULE HERE IS A MEASURED FAILURE, not a precaution. The phonemes each
# one fixes were read off `tokenizer.phonemize` against real sentences from
# this archive, and the comment on each says what it said before.

# Abbreviations the county's own writing is full of, and which the phonemiser
# reads as words. "Fla. Stat." came out "flah stat".
_WORDS = [
    (r"\bFla\.\s*Stat\.", "Florida Statutes"),
    (r"\bFla\.", "Florida"),
    (r"\bStat\.", "Statutes"),
    (r"\bOrd\.", "Ordinance"),
    (r"\bRes\.", "Resolution"),
    (r"\bSec\.", "Section"),
    (r"\bDept\.", "Department"),
    (r"\bSt\.\s*Rd\.", "State Road"),
    # Hyphen as well as space: the county writes both "SR 54" and "SR-54",
    # and the hyphen form would otherwise fall to the zoning-code rule below
    # and be spelled "S.R. 54" rather than said.
    (r"\bSR[-\s]+(\d)", r"State Road \1"),
    (r"\bCR[-\s]+(\d)", r"County Road \1"),
    (r"\bUS[-\s]+(\d)", r"U.S. \1"),
    # "No. 23-15" was read as the word "no". It is a number, and the reader
    # hearing "no twenty-three" has been told the opposite of the sentence.
    (r"\bNos?\.\s*(?=\d)", "Number "),
]

# An acronym is not a word. "BOCC" phonemised to /bˈɑːk/ - "bock" - which is
# the single worst thing on this list, because the Board of County
# Commissioners is the subject of most sentences in this archive.
#
# Two to six letters: longer runs of capitals are shouting rather than an
# initialism, and this archive's own copy rules forbid those anyway.
_ACRONYM = re.compile(r"\b([A-Z]{2,6})(s?)\b")

# EXCEPT the ones that ARE words. Spelling these is as wrong as saying "bock"
# for BOCC, in the other direction: nobody has ever said "F.E.M.A." out loud.
# A list rather than a rule, because whether an initialism has become a word
# is a fact about English usage and not something a regex can be told. Add to
# it when one is heard being spelled that should not be.
_SAID_AS_A_WORD = {"FEMA", "NASA", "NOAA", "OSHA", "HUD", "FDOT", "SWAT",
                   "COVID"}

# HOW to spell one, and both forms were measured. Full stops are the only
# separator that gets the last letter right: "C R A" ends in a schwa, because
# a lone "A" between spaces is the article, so the Community Redevelopment
# Agency came out "see-ar-uh". "C.R.A." is /sˈiː.ˈɑːɹ.ˈeɪ./ and correct.
#
# The plural is the other way round. "C.R.A.s" ends in /ˈɛs/ - it spells the
# s - and "C-R-A's" is /sˈiːˈɑːɹɹˈeɪz/, which is what a person says.
def _spell(m):
    letters, plural = m.group(1), m.group(2)
    if letters in _SAID_AS_A_WORD:
        return m.group(0)
    if plural:
        return "-".join(letters) + "'s"
    return ".".join(letters) + "."

# A ZONING CODE IS LETTERS AND THEN A NUMBER, and the hyphen between them is
# not spoken at all: "AR-1" is "A-R one", the way it is said at the podium.
#
# This one exists because the acronym rule above BROKE it. Left alone, "AR-1"
# phonemised correctly on its own; spelled to "A.R.-1" it became "A-R MINUS
# one", because a hyphen after a full stop is arithmetic rather than
# punctuation. The fix is to take the whole code as one thing rather than
# letting two rules each have half of it.
#
# Matching the number as well as the letters is what makes it safe. It is also
# an improvement on the raw text for the longer codes: "MPUD-2" alone is
# /ˈɛmpˈʌd/, "em-pud", and spelled it is "M-P-U-D two".
_CODE = re.compile(r"\b([A-Z]{1,6})-(\d+[A-Z]?)\b")


def _code(m):
    letters, number = m.group(1), m.group(2)
    if letters in _SAID_AS_A_WORD:
        return f"{letters} {number}"
    return ".".join(letters) + f". {number}"


# Read as sentence ends, every time. "3.5 acres" became "three. five acres",
# and the statute number 163.3184 became two numbers with a full stop between
# them - which in a synthesised voice is a dropped pitch and a pause, so it
# does not sound like a mistake, it sounds like a different fact.
#
# The digits AFTER the point are spelled out, which is the difference between
# "one sixty-three point three one eight four" and "one sixty-three point
# three THOUSAND one hundred eighty-four". Correct for ordinary decimals too:
# "1.25 million" wants "one point two five", never "point twenty-five".
_DECIMAL = re.compile(r"(\d)\.(\d+)")

# "$45.2 million" came out "dollar forty-five point two million": the symbol
# is read where it is written, which is in front. Money is said after the
# amount in English, and the scale word goes with the number.
#
# The digits are matched in GROUPS OF THREE rather than as "digits and
# commas". `\d[\d,]*` is greedy over commas, so "$34,000, approaching" put
# the sentence's own comma inside the number and produced "34,000, dollars" -
# the amount, a pause, and then the unit, which is not how anybody says it.
#
# Cents are their own group because they are not a decimal. "$13,950.00" read
# as "thirteen thousand nine hundred fifty POINT ZERO ZERO dollars"; the
# amount is exact and the way to say it is to say nothing at all.
_MONEY = re.compile(r"\$\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?"
                    r"(\s+(?:hundred|thousand|million|billion|trillion))?")


def _money(m):
    whole, cents, scale = m.group(1), m.group(2), m.group(3) or ""
    # With a scale word the decimal is part of the QUANTITY and not cents:
    # "$45.2 million" is forty-five point two million, never forty-five
    # dollars and twenty cents. Left as a decimal for the rule below.
    if scale:
        return f"{whole}{'.' + cents if cents else ''}{scale} dollars"
    if not cents:
        return f"{whole} dollars"
    cents = cents.ljust(2, "0")
    if cents == "00":
        return f"{whole} dollars"
    return f"{whole} dollars and {cents} cents"


# A DAY IS AN ORDINAL. "August 10, 2021" phonemised to "August TEN", which is
# not how any English speaker reads a date, and the difference is audible on
# every answer in this archive: dates are how the record is addressed.
#
# Anchored on the month name rather than on "a number followed by a comma",
# which is what keeps it off "a 10 acre parcel", "the vote was 10 to 1" and
# every other bare number. Suffixing the digits is enough - espeak reads
# "10th" as "tenth" and "21st" as "twenty-first" - so the number itself is
# never rewritten and cannot be mistyped in the process.
_MONTHS = ("January|February|March|April|May|June|July|August|September|"
           "October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|"
           "Oct|Nov|Dec")
_DAY = re.compile(rf"\b({_MONTHS})\.?\s+(\d{{1,2}})\b(?!\s*[-/:]|\s*\d)")


def _ordinal(m):
    day = int(m.group(2))
    if not 1 <= day <= 31:
        return m.group(0)
    suffix = ("th" if 11 <= day % 100 <= 13
              else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th"))
    return f"{m.group(1)} {day}{suffix}"


# A span of years, which is two numbers and not a subtraction. "FY 2014-2015"
# was read "two thousand fourteen DASH two thousand fifteen". Both years are
# in there, so nothing was lost, but no clerk reads it that way.
#
# Four-digit years on both sides, on purpose: it is what keeps this off
# ordinance 23-15, case 24-118 and the parcel numbers, which keep their
# hyphen because that is how they are read aloud.
_YEAR_SPAN = re.compile(r"\b((?:19|20)\d{2})-((?:19|20)\d{2})\b")

# "a 4-1 vote" was "four dash one". Single digit to single digit is a vote in
# this archive and nothing else: case numbers are 24-118, ordinances 23-15,
# and both keep their hyphen because "twenty-three dash fifteen" is how a
# clerk reads them aloud too.
_VOTE = re.compile(r"\b(\d)-(\d)\b")


# A citation is punctuation for the eye. "[item:11348]" read aloud is
# "bracket item colon eleven thousand three hundred and forty eight", which is
# not a thing anybody says. The reader strips these before it asks for audio,
# so the endpoint never sees one - but `bin/voice.py --answer` auditions a raw
# answer, and the whole point of that tool is to hear what a reader hears.
_CITATION = re.compile(r"\[(?:item:)?\d{1,7}\]")


def spoken(text):
    """The sentence as it should be READ, which is not how it is written."""
    out = _CITATION.sub(" ", text)
    for pattern, into in _WORDS:
        out = re.sub(pattern, into, out)
    out = _DAY.sub(_ordinal, out)
    out = _MONEY.sub(_money, out)
    out = _YEAR_SPAN.sub(r"\1 to \2", out)
    # BEFORE the plain acronym rule, which would otherwise take the letters
    # of a zoning code and leave its hyphen stranded against a full stop.
    out = _CODE.sub(_code, out)
    out = _ACRONYM.sub(_spell, out)
    # Twice: a version-like "1.2.3" holds two points, and one pass matching
    # from the left consumes the digit the next match needs to start from.
    for _ in range(2):
        out = _DECIMAL.sub(
            lambda m: f"{m.group(1)} point {' '.join(m.group(2))}", out)
    out = _VOTE.sub(r"\1 to \2", out)
    # An acronym at the end of a sentence brings its own full stop and meets
    # the sentence's: "the U.S.A.." No difference to the phonemes, but the
    # normalised text is logged and read by people.
    # Stripping a citation can leave a double space or a stranded space
    # before a full stop.
    out = re.sub(r"\s+([.,;:])", r"\1", re.sub(r"[ \t]{2,}", " ", out))
    return re.sub(r"\.\.+", ".", out)


# -------------------------------------------------------------- the audio

def key(text):
    """What this sentence's audio is called. Everything that changes the
    sound is in it, so a voice change does not serve yesterday's recording.

    THE LEXICON IS IN HERE TOO, as a fingerprint of its pronunciations.
    Correcting how Pinellas is said does not change any sentence's text, so
    without this every sentence already rendered would keep serving the old
    recording of the old mistake for ever."""
    h = hashlib.sha256()
    h.update(f"kokoro1\0{VOICE}\0{SPEED}\0{BITRATE}\0"
             f"{lexicon.fingerprint()}\0{text}".encode())
    return h.hexdigest()


def _path(k):
    # Two hex characters of fan-out: a flat directory of a hundred thousand
    # files is slow to list and unpleasant to look at on the host.
    return os.path.join(CACHE_DIR, k[:2], f"{k}.mp3")


def _encode(samples, rate):
    """float32 mono -> MP3 bytes."""
    import lameenc
    import numpy as np
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    enc = lameenc.Encoder()
    enc.set_bit_rate(BITRATE)
    enc.set_in_sample_rate(int(rate))
    enc.set_channels(1)
    enc.set_quality(2)
    # Or lameenc writes a stereo stream from mono input and every file is
    # twice the size it needs to be.
    enc.set_channels(1)
    return bytes(enc.encode(pcm.tobytes())) + bytes(enc.flush())


def render(text):
    """One chunk of an answer, as MP3 bytes. Cached on disk forever."""
    said = spoken(text)
    k = key(said)
    path = _path(k)
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        pass

    # THROUGH PHONEMES, ALWAYS, and not only for sentences holding a local
    # word. A sentence phonemised whole renders byte-identical to the same
    # sentence rendered from text - measured - so there is one path rather
    # than two, and the one path is the one the lexicon can reach into.
    v = voice()
    spoken_as = lexicon.phonemes(
        said, lambda t: v.tokenizer.phonemize(t, lang="en-us"))
    audio, rate = v.create(spoken_as, voice=VOICE, speed=SPEED,
                           lang="en-us", is_phonemes=True)
    blob = _encode(audio, rate)

    # Written beside and renamed, so a reader who arrives during a write never
    # gets half a file, and two readers asking for the same new sentence at
    # once cannot interleave into one.
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.part"
        with open(tmp, "wb") as f:
            f.write(blob)
        os.replace(tmp, path)
    except OSError as e:
        # A read-only or full disk costs the cache, not the feature.
        print(f"[say] could not cache {k[:12]}: {e}", flush=True)
    return blob


def wav(samples, rate):
    """The model's own output, for auditioning a voice from the shell."""
    buf = io.BytesIO()
    import numpy as np
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()
