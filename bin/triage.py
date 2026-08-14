"""Which utterances and voices are worth trying to identify.

Roughly a quarter of the corpus is four words or fewer and under two seconds -
"okay.", "yeah.", "thank you." Those cannot be attributed by ear or by
embedding, they pollute the samples a human judges a voice by, and a diarization
speaker made only of them is not identifiable at all.

But length alone is the wrong test. "aye.", "second.", "here." are among the
SHORTEST lines in the archive and the most consequential - they are the votes.
Discarding them as noise would delete exactly the evidence vote attribution
needs. So procedural words are exempt: never treated as noise, even when they
are one word long.

Nothing here removes anything from the transcript or from search. It only
governs what is used to IDENTIFY a speaker.
"""
import re

TRIVIAL_WORDS = 4
TRIVIAL_SECONDS = 2.0

# Short, but decisive: votes, seconds, roll-call responses, points of order.
PROCEDURAL = {
    "aye", "nay", "yes", "no", "here", "second", "seconded", "opposed",
    "abstain", "present", "carried", "unanimous",
}
# Multi-word procedural utterances. Anchored to the WHOLE line: "second",
# "present" and "here" are ordinary English, so matching them anywhere would
# classify "the second item on the agenda" as a vote.
PROCEDURAL_RE = re.compile(
    r"^(so moved|point of order|motion carries|motion fails|all in favou?r|"
    r"i (?:second|move)(?: that| it)?|(?:aye|nay|yes|no|here|second|seconded|"
    r"opposed|abstain|present)(?:[ ,]+(?:sir|ma'?am|madam chair|mr chair))?)$",
    re.I)
PROCEDURAL_MAX_WORDS = 4     # beyond this a line is substance, not a formality

# A voice needs at least this much real speech before it is worth clustering,
# anchoring or putting in front of a human.
MIN_VOICE_WORDS = 30
MIN_VOICE_SECONDS = 10.0


def is_procedural(text):
    """True only when the whole utterance IS a formality, not when it merely
    contains a procedural word."""
    t = " ".join(text.strip().strip(".,!?").lower().split())
    if len(t.split()) > PROCEDURAL_MAX_WORDS:
        return False
    return t in PROCEDURAL or bool(PROCEDURAL_RE.match(t))


def is_substantive(text, duration=None):
    """Enough content to help identify who is speaking."""
    if is_procedural(text):
        return False        # meaningful, but not identifying
    words = len(text.split())
    if words <= TRIVIAL_WORDS:
        return False
    if duration is not None and duration < TRIVIAL_SECONDS:
        return False
    return True


def substantive_words(lines):
    """Words of identifying speech in one diarization speaker's lines.

    `lines` is an iterable of text, or of (text, duration) pairs. Duration is
    optional because callers usually have the voice's total seconds already
    from SQL, and passing None per line would otherwise make every voice look
    silent.
    """
    total = 0
    for item in lines:
        text, dur = item if isinstance(item, (tuple, list)) else (item, None)
        if is_substantive(text, dur):
            total += len(text.split())
    return total


def voice_is_identifiable(lines, seconds=None):
    words = substantive_words(lines)
    if words < MIN_VOICE_WORDS:
        return False
    return seconds is None or seconds >= MIN_VOICE_SECONDS


# NOTE: web/api.py carries its own SQL form of these thresholds, because the
# web layer does not import from bin/. Keep the two in step if they change.
