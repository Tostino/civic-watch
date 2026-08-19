"""Which utterances and voices are worth trying to identify."""
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
    """Words of identifying speech in one diarization speaker's lines."""
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
