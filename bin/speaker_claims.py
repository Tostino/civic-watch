#!/usr/bin/env python3
"""Resolve a speaker's name once, from evidence rather than from a verdict.

A shadow build. Nothing here is read by any page, and `--compare` is the point:
it puts the new resolution beside the old one and says what changed.

    bin/speaker_claims.py --backfill   existing tables -> claims
    bin/speaker_claims.py --extract    transcripts -> self / read_aloud claims
    bin/speaker_claims.py --resolve    claims -> speaker_resolved
    bin/speaker_claims.py --compare    speaker_resolved vs utterance_speaker
    bin/speaker_claims.py --all        all four, in order

Run it against a sandbox first (`bin/sandbox.py --build`). It only ever writes
the four new tables, but a full extract over 299k utterances is not something to
try on production for the first time.

`speaker_method` holds the ranking as data, so whether an isolated self-ID
should outrank a voiceprint is a single UPDATE rather than a code change:

    UPDATE speaker_method SET rank = 9 WHERE method = 'self_weak';   -- strict

It ships lenient because 8% of self-IDs are isolated and demoting them throws
away names that are probably right.
"""
import argparse
import collections
import re
import sys

import db

# "My name is X" and the Florida podium convention, which SELF_ID never
# matched: a name, an address, and "I have been sworn". 581 utterances use the
# second form, which is how a voice saying "Shelley Johnson, 6400 Madison
# Street, and I have been sworn" is currently named "What".
# The trigger is case-insensitive, the NAME is not, and that must be scoped
# rather than global: with re.I on the whole pattern `[A-Z]` matches lowercase
# and every name comes out with a trailing conjunction.
# The name pattern is borrowed from speaker_id.QUEUE_NAME rather than rewritten,
# because an internal capital is PART of a surname (McBride, DeSantis, O'Neil)
# and a pattern that stops at one truncates the name instead of failing loudly.
# Written fresh here it produced "Barbara Mc" and "Cheryl Mc", as CORROBORATED
# claims outranking a correct archive name.
_WORD = r"[A-Z][a-z']+(?:[A-Z][a-z']+)*"
# A name particle that CARRIES A FULL STOP and is not the end of the sentence.
# Without these the name ended at the period: "my name is Margaret St. James"
# stored `Margaret St`, which is a street, and "Martin Luther King Jr." lost
# the Jr. Deliberately a closed list - a general "word followed by a period"
# would swallow the sentence boundary and turn "My name is John. I live at"
# into a two-word name.
_PARTICLE = r"(?:St|Jr|Sr|Dr|Mt)\."
NAME = (_WORD + r"(?:\s+(?:" + _PARTICLE + r"|" + _WORD + r")){0,2}")
# Filler is stripped wherever a name is expected, because it is capitalised
# where a name is capitalised and the pattern cannot tell them apart. Both
# halves of this were live: "Uh Shelley Johnson, [address removed], and I have
# been sworn" stored `Uh Shelley Johnson` at the start of a turn, and "My name
# is Um Mike Peters" stored `Um Mike Peters` in the middle of one.
_FILLER = r"(?:(?i:uh|um|er|okay|well|so|hi|hello|yeah|yes|and|thank you)[,.]?\s+)*"
SAYS_NAME = re.compile(r"(?i:\bmy name is)\s+" + _FILLER + r"(" + NAME + r")")
# Anchored at the START of what the person says, because that is where the
# convention puts it, and required to be followed by a comma and an address.
# Unanchored it matched street names out of the address itself - "Margaret St"
# from "1234 Margaret Street" - and invented a speaker for every one.
PODIUM = re.compile(
    r"^" + _FILLER + r"(?:(?i:good (?:morning|afternoon|evening)),?\s+)?" + _FILLER +
    r"(" + NAME + r"),\s+"
    r"(?:\d{2,6}\s|\[address removed\])")
SWORN = re.compile(r"\b(?:i have been sworn|been duly sworn|i was sworn)\b", re.I)

# SOMEBODY ASKING FOR A NAME, which means the answer that follows is not the
# asker's. When the diarizer merges the chair's request and the commenter's
# answer into one utterance, the self-introduction sits inside the CHAIR's turn:
# 24 of 1,668 self-ID utterances have this shape, each putting a member of the
# public's name on a commissioner's voice.
PROMPTED = re.compile(
    r"(?:say|state|give|need|with|proceed with)\s+(?:us\s+)?(?:your|the)\s+name"
    r"|name\s+and\s+address"
    r"|your\s+name\s+and", re.I)

# Somebody reading another person's words. The claim then names the AUTHOR and
# covers only the read span; the reader keeps every claim either side of it.
# Without this a staffer reading "My name is Corey Ward and I live at..." is
# confidently named Corey Ward, above the voiceprint.
# WHO IS BEING READ, and where their letter starts. A clerk reads a stack of
# correspondence in one go and announces each item, so the announcements cut the
# run into letters. READING below finds the run; this finds the seams inside it.
# AN ANNOUNCEMENT, NOT A MENTION, and the determiner tells them apart. "Next
# email is from Michael Killian" hands over the floor; "there was also a letter
# from BayCare" is somebody talking ABOUT a letter mid-remark. Without the
# ordinal both matched, each taking 14 to 16 following utterances of the
# speaker's own words with it.
READ_FROM = re.compile(
    r"(?i:\b(?:next|last|first|second|third|fourth|fifth|final|another|following)\s+"
    r"(?:\w+\s+){0,2}?(?:e-?mails?|letters?|correspondence|comment cards?)\b"
    r"[^.]{0,20}?\b(?:is\s+|was\s+)?from"
    r"|\bsent\s+in\s+by)"
    r"[\s,]+" + _FILLER + r"(" + NAME + r"(?:\s+and\s+" + NAME + r")?)")

READING = re.compile(
    r"\b(?:email|e-mail|letter|correspondence|comment card)\b[^.]{0,40}"
    r"\b(?:is )?from\b|\bread (?:it |this )?into the record\b"
    r"|\bi am writing\b|\bwrites\b", re.I)

# How many of the 13 utterances around a self-ID share its diarization label.
# The risk a self-ID carries is never the NAME - nobody misstates their own -
# it is that the utterance landed on the wrong voice. Measured over the
# corpus: 83% sit in a coherent run, 8% are isolated.
NEAR = 6
COHERENT = 4


# A voice's utterances are INTERLEAVED with everyone else's, so MIN(idx) to
# MAX(idx) for one local_label is not "that voice's run" - it is the whole
# meeting, swallowing every other speaker in between. Measured the hard way:
# that span model made 89% of utterances contested and turned Commissioner
# Yeager into James Navarro. A claim covers one CONTIGUOUS run, and a voice
# that speaks four times in a meeting makes four claims.
RUNS = """
    WITH marked AS (
        SELECT video_id, local_label, idx,
               idx - ROW_NUMBER() OVER (PARTITION BY video_id, local_label
                                        ORDER BY idx) AS island
          FROM utterances
         WHERE local_label IS NOT NULL)
    SELECT video_id, local_label, MIN(idx) AS lo, MAX(idx) AS hi
      FROM marked GROUP BY video_id, local_label, island"""


def runs_by_voice(con):
    """(video_id, local_label) -> [(lo, hi), ...], every contiguous run."""
    out = collections.defaultdict(list)
    for r in con.execute(RUNS):
        out[(r["video_id"], r["local_label"])].append((r["lo"], r["hi"]))
    return out


def runs_by_cluster(con):
    out = collections.defaultdict(list)
    for r in con.execute("""
            WITH marked AS (
                SELECT video_id, cluster, idx,
                       idx - ROW_NUMBER() OVER (PARTITION BY video_id, cluster
                                                ORDER BY idx) AS island
                  FROM utterances WHERE cluster IS NOT NULL)
            SELECT video_id, cluster, MIN(idx) AS lo, MAX(idx) AS hi
              FROM marked GROUP BY video_id, cluster, island"""):
        out[(r["video_id"], r["cluster"])].append((r["lo"], r["hi"]))
    return out


# A claim mirrors how its producer behaves. `speaker_override` keeps history,
# so an override is an event and accumulates; every other table - including
# `speaker_label`, which is deleted and re-inserted - holds one current answer,
# so its claims are written once. See the partial unique index in schema.sql.
EVENTS = ("override",)


def claim(cur, video_id, lo, hi, name, method, quote=None, corroborated=False,
          label=None):
    """Append one claim. Idempotent for derived methods, append-only for human."""
    if method in EVENTS:
        cur.execute("""INSERT INTO speaker_claim
                         (video_id, start_idx, end_idx, local_label, name_text,
                          method, quote, corroborated)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (video_id, lo, hi, label, name, method, quote, corroborated))
        return
    cur.execute("""INSERT INTO speaker_claim
                     (video_id, start_idx, end_idx, local_label, name_text,
                      method, quote, corroborated)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   -- The WHERE repeats the index predicate, which is how
                   -- Postgres infers a PARTIAL unique index. Without it the
                   -- statement cannot see the constraint at all.
                   ON CONFLICT (video_id, start_idx, end_idx, method, name_text)
                     WHERE method <> 'override'
                     DO UPDATE SET local_label  = EXCLUDED.local_label,
                                   quote        = COALESCE(EXCLUDED.quote,
                                                           speaker_claim.quote),
                                   corroborated = EXCLUDED.corroborated""",
                (video_id, lo, hi, label, name, method, quote, corroborated))


def append(con, video_id, lo, hi, name, method, quote=None,
           corroborated=False, label=None):
    """`claim` for a caller that has a connection rather than a cursor."""
    cur = con.cursor()
    claim(cur, video_id, lo, hi, name, method, quote, corroborated, label)
    return cur


# ------------------------------------------------------------------ backfill
def backfill(con, video_id=None, commit=True):
    """Everything the archive already decided, restated as evidence."""
    cur = con.cursor()
    only, arg = ("AND video_id = %s", (video_id,)) if video_id else ("", ())
    cur.execute("DELETE FROM speaker_claim WHERE method IN "
                f"('override','label','voice','cluster','llm','chair') {only}", arg)

    n = collections.Counter()
    # A human, about a span. The only producer that is already append-only.
    for r in con.execute(f"""SELECT video_id, start_idx, end_idx, name
                              FROM speaker_override WHERE status='applied'
                               {only}""", arg):
        claim(cur, r["video_id"], r["start_idx"], r["end_idx"], r["name"],
              "override")
        n["override"] += 1

    # A human, about a whole voice. The span is that voice's run in that
    # meeting, which is what `speaker_label` means and has never been able to
    # say.
    voice_runs, cluster_runs = runs_by_voice(con), runs_by_cluster(con)
    for r in con.execute(f"SELECT video_id, local_label, name FROM speaker_label WHERE true {only}", arg):
        for lo, hi in voice_runs.get((r["video_id"], r["local_label"]), []):
            claim(cur, r["video_id"], lo, hi, r["name"], "label", label=r["local_label"])
            n["label"] += 1

    # The pipeline, about this voice in this meeting. `source` says which of
    # three methods produced it for two of them and NULL for the largest
    # bucket, so NULL becomes `voice` - the honest floor. The extractor puts
    # better-evidenced `self` claims on top rather than rewriting these.
    for r in con.execute(f"""SELECT video_id, local_label, name, source
                              FROM speaker_identity WHERE name IS NOT NULL {only}""", arg):
        m = {"llm": "llm", "chair": "chair"}.get(r["source"], "voice")
        for lo, hi in voice_runs.get((r["video_id"], r["local_label"]), []):
            claim(cur, r["video_id"], lo, hi, r["name"], m, label=r["local_label"])
            n[m] += 1

    # The archive-wide cluster majority: the weakest thing here and the one
    # carrying two vetoes that are easy to lose. They do NOT live in
    # `voice_name`, they are conditions on utterance_speaker's join to it, so
    # reading the view directly hands back names the live path refuses.
    for r in con.execute(f"""
            SELECT DISTINCT u.video_id, u.local_label, vn.name
              FROM utterances u
              JOIN voice_name vn ON vn.video_id = u.video_id
                                AND vn.cluster = u.cluster
             WHERE u.local_label IS NOT NULL {only.replace('video_id', 'u.video_id')}
               AND NOT EXISTS (SELECT 1 FROM voice_affinity va
                                WHERE va.video_id = u.video_id
                                  AND va.local_label = u.local_label
                                  AND va.name = vn.name
                                  AND va.similarity < 0.70)
               AND NOT EXISTS (SELECT 1 FROM speaker_identity si2
                                WHERE si2.video_id = u.video_id
                                  AND si2.name = vn.name
                                  AND si2.local_label <> u.local_label)""", arg):
        for lo, hi in voice_runs.get((r["video_id"], r["local_label"]), []):
            claim(cur, r["video_id"], lo, hi, r["name"], "cluster", label=r["local_label"])
            n["cluster"] += 1

    if commit:
        con.commit()
    return n


# ------------------------------------------------------------------- extract
def _letters(by_run):
    """Cut each reading run into one span per letter, keyed to its author.

    THE SPAN IS THE LETTER, not the line the author's name happens to fall on.
    This claimed a single utterance - whichever one contained "my name is" -
    so a resident's letter was attributed to its author for one line out of
    six and to the commissioner reading it for the other five, and a letter
    whose author never says their own name was not attributed at all. Most do
    not: of the correspondence read into BTQQU-4nOq8, every item is announced
    and only some introduce themselves."""
    out = []
    for key, utts in by_run.items():
        vid = key[0]
        if not any(READING.search(t) for _, t in utts):
            continue
        marks = []
        for i, t in utts:
            m = READ_FROM.search(t)
            if not m:
                continue
            nm = m.group(1).strip()
            cap = 6 if " and " in nm else 3      # a couple gets both names
            if len(nm.split()) > cap or len(nm) < 4:
                continue
            marks.append((i, nm, t[max(0, m.start() - 20):m.end() + 60]))
        for k, (lo, nm, quote) in enumerate(marks):
            hi = marks[k + 1][0] - 1 if k + 1 < len(marks) else utts[-1][0]
            if hi >= lo:
                out.append((vid, lo, hi, nm, quote))
    return out


def extract(con, video_id=None, commit=True):
    """What the transcript says outright, which nothing has been reading."""
    cur = con.cursor()
    only, arg = ("AND video_id = %s", (video_id,)) if video_id else ("", ())
    cur.execute("DELETE FROM speaker_claim WHERE method IN "
                f"('self','self_weak','read_aloud') {only}", arg)

    n = collections.Counter()
    voice_runs = runs_by_voice(con)
    rows = con.execute(f"""SELECT video_id, idx, local_label, text
                            FROM utterances
                           WHERE (text ILIKE '%%my name is%%'
                              OR text ~* '\\yi have been sworn|been duly sworn\\y')
                             {only}
                           ORDER BY video_id, idx""", arg).fetchall()

    # One pass over the transcript, serving both the corroboration flag and the
    # read-aloud segmentation, rather than a query per claim.
    heard = collections.defaultdict(list)
    texts = collections.defaultdict(dict)
    for r in con.execute(f"SELECT video_id, idx, text FROM utterances "
                         f"WHERE true {only}", arg):
        t = " ".join(r["text"].split())
        heard[r["video_id"]].append(t.lower())
        texts[r["video_id"]][r["idx"]] = t

    # Grouped by CONTIGUOUS RUN, which is the one boundary a letter may not
    # cross: past the end of the run somebody else is speaking.
    by_run = {}
    for (vid, label), spans in voice_runs.items():
        if vid not in texts:
            continue
        for k, (lo, hi) in enumerate(spans):
            utts = [(i, texts[vid][i]) for i in range(lo, hi + 1) if i in texts[vid]]
            if utts:
                by_run[(vid, label, k)] = utts

    # The letters, whole. Written before the self-introduction loop below so
    # that a self-introduction INSIDE a letter can claim the letter rather
    # than the line it sits on.
    letter_of = {}
    for vid, lo, hi, nm, quote in _letters(by_run):
        claim(cur, vid, lo, hi, nm, "read_aloud", quote)
        n["read_aloud"] += 1
        for i in range(lo, hi + 1):
            letter_of[(vid, i)] = (lo, hi)

    for r in rows:
        text = " ".join(r["text"].split())
        m = SAYS_NAME.search(text)
        if not m and SWORN.search(text):
            m = PODIUM.search(text)
        if not m:
            continue
        name = m.group(1).strip()
        if len(name.split()) > 3 or len(name) < 4:
            continue
        # READ-ALOUD IS TESTED FIRST, and the order is the decision. A
        # commissioner reading a resident's letter must be attributed to its
        # author, and the board-voice guard below would otherwise swallow it,
        # because the voice IS a commissioner's and the name IS somebody else's.
        # Putting that guard first took read_aloud from 29 claims to 9.
        # Reading somebody else's words: the name belongs to the author and
        # the claim covers only this utterance, so the reader keeps her own
        # name either side of it.
        reading_run = con.execute("""
            SELECT EXISTS (SELECT 1 FROM utterances u
                            WHERE u.video_id = %s
                              AND u.idx BETWEEN %s AND %s
                              AND u.text ~* '(email|letter) is from|next (email|letter)'
                                          '|read (it|this) into the record'
                                          '|i am writing|to whom it may') AS x""",
            (r["video_id"], *next(
                (sp for sp in voice_runs.get((r["video_id"], r["local_label"]), [])
                 if sp[0] <= r["idx"] <= sp[1]), (r["idx"], r["idx"])))).fetchone()["x"]
        # The words that carry the name. Assigned HERE, above every branch that
        # records a claim: assigned below them, the read_aloud branch wrote
        # whichever quote the PREVIOUS iteration had left in the variable, so a
        # letter was filed with evidence naming a different member of the public.
        # Found by audit.py claims.quotes_are_verbatim; nothing else looks.
        quote = text[max(0, m.start() - 30):m.end() + 40]

        if READING.search(text) or reading_run:
            # The whole letter, when the announcements said where it starts
            # and ends. Falling back to the single utterance is for
            # correspondence read without being announced - "I am writing to
            # you today" with no "next email is from" anywhere - where the
            # only thing the archive knows is that THIS line is somebody
            # else's words.
            lo, hi = letter_of.get((r["video_id"], r["idx"]),
                                   (r["idx"], r["idx"]))
            claim(cur, r["video_id"], lo, hi, name, "read_aloud", quote)
            n["read_aloud"] += 1
            continue

        # The ask and the answer merged into one utterance: see PROMPTED.
        if PROMPTED.search(text[:m.start()]):
            n["prompted_skipped"] += 1
            continue

        # A BOARD MEMBER'S VOICE DOES NOT INTRODUCE ITSELF AS SOMEBODY ELSE.
        # Where the archive already has a commissioner on this voice and the
        # self-introduction names a different person, that is diarization merging
        # a commenter's turn into the chair's, not a correction. Twelve distinct
        # names arrived this way, every one a member of the public landing on a
        # commissioner.
        held = con.execute("""
            SELECT si.name FROM speaker_identity si
             WHERE si.video_id = %s AND si.local_label = %s
               AND si.name IS NOT NULL
               AND EXISTS (SELECT 1 FROM people p
                            WHERE lower(p.surname) = lower(si.name))""",
            (r["video_id"], r["local_label"])).fetchone()
        if held and held["name"].lower() not in name.lower():
            n["board_voice_skipped"] += 1
            continue

        # Is this utterance attributable? Not "is the name right" - it is
        # whether the utterance landed on the right voice.
        near = con.execute("""SELECT count(*) c FROM utterances
                               WHERE video_id=%s AND local_label=%s
                                 AND idx BETWEEN %s AND %s""",
                           (r["video_id"], r["local_label"],
                            r["idx"] - NEAR, r["idx"] + NEAR)).fetchone()["c"]
        method = "self" if near >= COHERENT else "self_weak"

        surname = name.split()[-1].lower()
        corrob = sum(1 for t in heard[r["video_id"]] if surname in t) > 1

        # The span is the contiguous run CONTAINING this line - the trip to
        # the podium happening now - and not every line this voice speaks in
        # the meeting.
        span = next((s for s in voice_runs.get((r["video_id"], r["local_label"]), [])
                     if s[0] <= r["idx"] <= s[1]), (r["idx"], r["idx"]))
        claim(cur, r["video_id"], span[0], span[1], name, method, quote, corrob,
              label=r["local_label"])
        n[method] += 1
        n["corroborated"] += bool(corrob)

    if commit:
        con.commit()
    return n


# ---------------------------------------------------------------------- link
def link(con, video_id=None, commit=True):
    """Claims about one voice in one meeting are claims about one person."""
    cur = con.cursor()
    # Re-runnable: let go of the people this step created before removing
    # them, or the foreign key from the claims refuses. Board members are
    # never touched - they come from the county's roster, not from here.
    if video_id:
        # One recording: let go of only its own links. The people it created
        # may be cited by other recordings, so they stay.
        cur.execute("UPDATE speaker_claim SET person_id = NULL WHERE video_id = %s",
                    (video_id,))
        cur.execute("UPDATE speaker_resolved SET person_id = NULL WHERE video_id = %s",
                    (video_id,))
    else:
        cur.execute("UPDATE speaker_claim SET person_id = NULL")
        cur.execute("UPDATE speaker_resolved SET person_id = NULL")
        cur.execute("DELETE FROM person_alias WHERE person_id IN "
                    "(SELECT id FROM people WHERE kind = 'public')")
        cur.execute("DELETE FROM people WHERE kind = 'public'")
    if commit:
        con.commit()

    voices = collections.defaultdict(list)
    only, arg = ("AND video_id = %s", (video_id,)) if video_id else ("", ())
    for r in con.execute(f"""SELECT video_id, local_label, name_text, corroborated
                              FROM speaker_claim
                             WHERE method IN ('self', 'self_weak')
                               AND local_label IS NOT NULL {only}""", arg):
        voices[(r["video_id"], r["local_label"])].append(
            (r["name_text"], r["corroborated"]))

    # A SELF-ID DECIDES WHO, NOT HOW IT IS SPELLED. The archive's name comes
    # from a vote over every time the room said it; a self-ID is one utterance
    # of ASR. Where the two are near-identical the better-attested SPELLING wins
    # rather than the better-ranked METHOD. Without this, `self` outranking
    # `voice` turned "Skip Geiger" into "Ski Geiger", 55 times.
    import difflib
    for r in con.execute(f"""SELECT video_id, local_label, name FROM speaker_identity
                             WHERE name IS NOT NULL {only}""", arg):
        k = (r["video_id"], r["local_label"])
        if k not in voices:
            continue
        for nm, _ in list(voices[k]):
            if difflib.SequenceMatcher(None, nm.lower(),
                                       r["name"].lower()).ratio() > 0.75:
                voices[k].append((r["name"], True))
                break

    made = aliased = 0
    for (vid, label), names in voices.items():
        seen = collections.Counter(n for n, _ in names)
        corrob = {n for n, c in names if c}
        # How often the meeting itself uses each rendering. The spelling the
        # room said most is the one to show.
        said = {n: con.execute(
            "SELECT count(*) c FROM utterances WHERE video_id=%s AND text ILIKE %s",
            (vid, f"%{n}%")).fetchone()["c"] for n in seen}
        best = sorted(seen, key=lambda n: (said[n], n in corrob, seen[n], len(n)),
                      reverse=True)[0]
        # ONE WORD IS NOT AN IDENTIFICATION. "Henry", "Cindy", "Alvarez" - and
        # "Good", out of "Good Morning" - are what the extractor produces when
        # it catches half a name or none at all, and a `people` row asserts
        # that a person has been identified. The claim keeps its name_text and
        # the resolver still renders it; what it does not get is a person.
        if len(best.split()) < 2:
            continue
        # NEVER match on a surname alone. This linked "Sean Poole", the
        # managing director of a camera vendor, onto Christopher B. Poole, a
        # county commissioner, because both end in Poole - the very defect the
        # maintainer objected to, reintroduced here by hand and caught by the
        # shadow diff eight minutes later.
        #
        # A full name matches a full name. A bare surname - which is how the
        # roster stores board members - matches a surname only when the claim
        # is itself a single token, which a self-introduction almost never is.
        if len(best.split()) > 1:
            row = con.execute("SELECT id FROM people WHERE lower(full_name) = lower(%s)",
                              (best,)).fetchone()
        else:
            row = con.execute("SELECT id FROM people WHERE lower(surname) = lower(%s)",
                              (best,)).fetchone()
        if row:
            pid = row["id"]
        else:
            # surname is NULL for a member of the public: `people` is
            # UNIQUE (surname), so storing "Poole" for a resident is refused
            # because a commissioner already owns it. A surname is the roster's
            # key for board members and nothing else's. Postgres lets any number
            # of rows share a NULL, so the constraint keeps protecting the roster
            # and stops obstructing everybody else.
            # A surname is kept for everybody - it is in the transcript and
            # it is part of the record. It is an attribute here, not a key:
            # 228 people already share one.
            pid = cur.execute(
                "INSERT INTO people (surname, full_name, kind) "
                "VALUES (%s, %s, 'public') "
                "ON CONFLICT (lower(full_name)) WHERE kind = 'public' "
                "  DO UPDATE SET surname = EXCLUDED.surname "
                "RETURNING id",
                (best.split()[-1], best)).fetchone()["id"]
            made += 1
        for n in seen:
            cur.execute("INSERT INTO person_alias (alias, person_id) VALUES (%s, %s) "
                        "ON CONFLICT (alias) DO NOTHING", (n, pid))
            aliased += 1
        cur.execute("""UPDATE speaker_claim SET person_id = %s
                        WHERE video_id = %s AND local_label = %s
                          AND method IN ('self','self_weak')""", (pid, vid, label))
    # `people` rows are keyed by full name, so a second run finds the row it
    # made last time rather than making another.
    if commit:
        con.commit()
    return {"people_created": made, "aliases": aliased, "voices": len(voices)}


# ------------------------------------------------------------------- resolve
# rank, then corroboration promoting the two unquoted methods, then span
# specificity (narrower is more specific), then recency. Written once, here.
# The resolution ITSELF, as a bare SELECT, so that the audit can recompute it
# and diff rather than re-describing it. A check written against a paraphrase
# of this query would pass while the two drifted, which is the failure it is
# there to catch.
RESOLUTION = """
SELECT u.video_id, u.idx, w.name_text, w.person_id, w.method, w.contested
  FROM utterances u
  JOIN LATERAL (
      -- The PERSON'S chosen display name wins over the string this particular
      -- claim happened to carry. That is the whole point of linking: the man
      -- who says his own name twice, ASR rendering it two ways, resolves to
      -- one name everywhere instead of to whichever rendering owned that span.
      SELECT COALESCE(pe.full_name, c.name_text) AS name_text, c.person_id,
             c.method,
             -- two unvetoed methods asserting different names for one span:
             -- a fact the pipeline already computes and prints away
             (COUNT(DISTINCT lower(c2.name_text)) > 1) AS contested
        FROM speaker_claim c
        JOIN speaker_method m ON m.method = c.method
        LEFT JOIN people pe ON pe.id = c.person_id
        LEFT JOIN speaker_claim c2
               ON c2.video_id = c.video_id
              AND u.idx BETWEEN c2.start_idx AND c2.end_idx
              AND c2.name_text IS NOT NULL
              -- A READ-ALOUD CLAIM DOES NOT CONTEST A VOICE CLAIM, and the
              -- two overlap on every letter ever read into the record -
              -- that is what reading aloud IS. They answer different
              -- questions: whose words these are, and whose voice is saying
              -- them. Counted as a disagreement, attributing letters over
              -- their whole span put 4,905 utterances behind a `Disputed`
              -- badge, nearly all of them correspondence the archive
              -- understands perfectly well. So read_aloud is compared with
              -- read_aloud, and everything else with everything else.
              AND (c2.method = 'read_aloud') = (c.method = 'read_aloud')
       WHERE c.video_id = u.video_id
         AND u.idx BETWEEN c.start_idx AND c.end_idx
         -- A DETACH IS A CLAIM THAT THERE IS NO NAME, and it has to be able
         -- to win. `AND c.name_text IS NOT NULL` removed it from the running
         -- entirely, so detaching a span did not blank it - it handed the
         -- span to whatever derived guess ranked next, which is worse than
         -- doing nothing and is what the operator was trying to stop.
         -- Only `override` may say it: for a derived method a NULL name
         -- means the producer had nothing to offer, not that it asserts
         -- nobody.
         AND (c.name_text IS NOT NULL OR c.method = 'override')
         -- Tested against the name this will EMIT, not the name the claim
         -- happens to carry. The two differ exactly when linking succeeds:
         -- a claim reading "Ron Oakley" passes a surname-keyed roster guard
         -- because no board member is called that, then links to person 10
         -- and emits `Oakley` at a meeting the roster does not place him at.
         -- One utterance in 235,199, and precisely the thing the guard is
         -- for.
         AND (c.name_text IS NULL
              OR name_supported(c.video_id,
                   CASE WHEN pe.kind = 'board' THEN pe.surname
                        ELSE c.name_text END))
       GROUP BY c.id, c.name_text, pe.full_name, c.person_id, c.method,
                m.rank, c.corroborated, c.start_idx, c.end_idx
       ORDER BY m.rank - CASE WHEN c.corroborated AND m.rank >= 7
                              THEN 1 ELSE 0 END,
                c.end_idx - c.start_idx,
                c.id DESC
       LIMIT 1) w ON TRUE"""

RESOLVE = ("INSERT INTO speaker_resolved "
           "(video_id, idx, name_text, person_id, method, contested)\n" + RESOLUTION)


def resolve(con, video_id=None, commit=True):
    """Materialise the resolution, for one meeting or for all of them."""
    cur = con.cursor()
    if video_id:
        cur.execute("DELETE FROM speaker_resolved WHERE video_id = %s", (video_id,))
        cur.execute(RESOLVE + " WHERE u.video_id = %s", (video_id,))
    else:
        cur.execute("DELETE FROM speaker_resolved")
        cur.execute(RESOLVE)
    if commit:
        con.commit()
    where = "WHERE video_id = %s" if video_id else ""
    return con.execute("SELECT count(*) n, count(*) FILTER (WHERE contested) c "
                       f"FROM speaker_resolved {where}",
                       (video_id,) if video_id else ()).fetchone()


def refresh_video(con, video_id, commit=True):
    """Bring one recording's resolution up to date, end to end."""
    backfill(con, video_id, commit=commit)
    extract(con, video_id, commit=commit)
    link(con, video_id, commit=commit)
    return resolve(con, video_id, commit=commit)


# ------------------------------------------------------------------- compare
def compare(con):
    """The gate. What changed, bucketed, and nothing asserted."""
    print(f"\n{'=' * 70}\nnew resolution against the live one\n{'=' * 70}")
    t = con.execute("""
        SELECT count(*) FILTER (WHERE us.name IS NOT NULL) AS old_named,
               count(*) FILTER (WHERE sr.name_text IS NOT NULL) AS new_named,
               count(*) FILTER (WHERE us.name IS NOT NULL
                                  AND sr.name_text IS NOT NULL
                                  AND lower(us.name) = lower(sr.name_text)) AS same,
               count(*) FILTER (WHERE us.name IS NULL
                                  AND sr.name_text IS NOT NULL) AS gained,
               count(*) FILTER (WHERE us.name IS NOT NULL
                                  AND sr.name_text IS NULL) AS lost,
               count(*) FILTER (WHERE us.name IS NOT NULL
                                  AND sr.name_text IS NOT NULL
                                  AND lower(us.name) <> lower(sr.name_text)) AS changed
          FROM utterance_speaker us
          LEFT JOIN speaker_resolved sr
                 ON sr.video_id = us.video_id AND sr.idx = us.idx""").fetchone()
    for k in ("old_named", "new_named", "same", "gained", "lost", "changed"):
        print(f"  {k:<12} {t[k]:>8,}")

    print("\n  what the changes are, by (old basis -> new method):")
    for r in con.execute("""
            SELECT us.basis AS old, sr.method AS new, count(*) n
              FROM utterance_speaker us
              JOIN speaker_resolved sr
                ON sr.video_id = us.video_id AND sr.idx = us.idx
             WHERE us.name IS NOT NULL AND sr.name_text IS NOT NULL
               AND lower(us.name) <> lower(sr.name_text)
             GROUP BY 1, 2 ORDER BY n DESC LIMIT 12"""):
        print(f"    {str(r['old']):<10} -> {str(r['new']):<12} {r['n']:>7,}")

    print("\n  names GAINED where the archive had none, by method:")
    for r in con.execute("""
            SELECT sr.method, count(*) n
              FROM utterance_speaker us
              JOIN speaker_resolved sr
                ON sr.video_id = us.video_id AND sr.idx = us.idx
             WHERE us.name IS NULL AND sr.name_text IS NOT NULL
             GROUP BY 1 ORDER BY n DESC"""):
        print(f"    {str(r['method']):<12} {r['n']:>7,}")

    print("\n  a sample of the changes, to read by hand:")
    for r in con.execute("""
            SELECT us.video_id, us.idx, us.name AS old, us.basis,
                   sr.name_text AS new, sr.method
              FROM utterance_speaker us
              JOIN speaker_resolved sr
                ON sr.video_id = us.video_id AND sr.idx = us.idx
             WHERE us.name IS NOT NULL AND sr.name_text IS NOT NULL
               AND lower(us.name) <> lower(sr.name_text)
             ORDER BY random() LIMIT 8"""):
        print(f"    {r['video_id']} {r['idx']:>6}  "
              f"{r['old'][:20]:<20} ({r['basis']:<7}) -> "
              f"{r['new'][:20]:<20} ({r['method']})")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for f in ("backfill", "extract", "link", "resolve", "compare", "all"):
        ap.add_argument(f"--{f}", action="store_true")
    ap.add_argument("--video", help="resolve one recording rather than all")
    args = ap.parse_args()
    con = db.connect()

    if args.backfill or args.all:
        print("backfill:", dict(backfill(con)))
    if args.extract or args.all:
        print("extract: ", dict(extract(con)))
    if args.link or args.all:
        print("link:    ", link(con))
    if args.resolve or args.all:
        r = resolve(con, args.video)
        print(f"resolve:  {r['n']:,} utterances, {r['c']:,} contested")
    if args.compare or args.all:
        compare(con)
    return 0


if __name__ == "__main__":
    sys.exit(main())
