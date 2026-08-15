"""Data integrity checks over the whole archive.

Every bug found in this project so far was found by spot-checking one record
and noticing it looked wrong - the bulk consent sentence that marked 180
approved items withdrawn, the roster that credited a commissioner with 14,148
utterances from before she took office, the retrieval query that omitted
`segment_id` so an entire expansion step silently did nothing. Each of those
was invisible in the summary statistics and obvious against an invariant.

So the invariants are written down here instead, and checked in bulk. A check
states something that must be true, counts the rows where it is not, and shows
a few. Nothing is repaired: the point is to know, and a repair that runs before
anyone has looked at the failure is how a data bug becomes permanent.

Every check also reports HOW MANY ROWS IT EXAMINED, and that is not decoration.
This audit once printed "18/18 ok" when two of its checks were ranging over an
empty set: both filter on `passages.agenda_item_id IS NOT NULL`, and at that
moment not one passage had been bound to an agenda item, so both passed by
examining nothing. A green board meant only that the rebuild had not run yet.
A check with no rows under it proves nothing and now says so.

    bin/audit.py            run everything
    bin/audit.py --only x   run checks whose name contains x
"""
import argparse
import sys

import db
import roster

CHECKS = []


def check(name, why, review=False):
    """Register an invariant.

    The function returns (n_bad, sql_for_examples, sql_for_population). The
    third is the denominator: the set the invariant ranges over. Without it a
    check cannot distinguish "nothing is broken" from "nothing is there".

    review=True marks a check that surfaces WORK rather than a defect - cases
    needing human judgement, where a non-zero count is expected and not a bug.
    These are counted and shown separately, because a check that can never
    reach zero would leave the board permanently red and teach everyone to
    ignore it, which costs more than the check is worth.
    """
    def deco(fn):
        CHECKS.append((name, why, fn, review))
        return fn
    return deco


def count(con, sql, args=()):
    return con.execute(sql, args).fetchone()[0]


# ---------------------------------------------------------------- structure
@check("passages.item_fk", "every passage points at an agenda item that exists")
def _(con):
    return count(con, """SELECT COUNT(*) FROM passages p
        WHERE p.agenda_item_id IS NOT NULL AND NOT EXISTS
          (SELECT 1 FROM agenda_items ai WHERE ai.id = p.agenda_item_id)"""), """
        SELECT id, video_id, agenda_item_id FROM passages p
        WHERE p.agenda_item_id IS NOT NULL AND NOT EXISTS
          (SELECT 1 FROM agenda_items ai WHERE ai.id = p.agenda_item_id) LIMIT 5""", \
        "SELECT COUNT(*) FROM passages WHERE agenda_item_id IS NOT NULL"


@check("spans.same_meeting",
       "a span's video and its agenda item belong to the SAME meeting")
def _(con):
    q = """FROM item_spans sp
           JOIN agenda_items ai ON ai.id = sp.agenda_item_id
           JOIN videos v ON v.id = sp.video_id
           WHERE v.meeting_id IS DISTINCT FROM ai.meeting_id"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT sp.video_id, v.meeting_id AS video_meeting,
               ai.meeting_id AS item_meeting, ai.code {q} LIMIT 5""", \
        "SELECT COUNT(*) FROM item_spans"


@check("spans.no_overlap", "two item spans never cover the same utterance")
def _(con):
    q = """FROM item_spans a JOIN item_spans b
           ON a.video_id = b.video_id AND a.id < b.id
          AND a.start_idx <= b.end_idx AND b.start_idx <= a.end_idx"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT a.video_id, a.start_idx, a.end_idx, b.start_idx, b.end_idx {q} LIMIT 5""", \
        "SELECT COUNT(*) FROM item_spans"


@check("spans.ordered", "start_idx <= end_idx on every span")
def _(con):
    return count(con, "SELECT COUNT(*) FROM item_spans WHERE start_idx > end_idx"), """
        SELECT id, video_id, start_idx, end_idx FROM item_spans
        WHERE start_idx > end_idx LIMIT 5""", \
        "SELECT COUNT(*) FROM item_spans"


@check("spans.tile",
       "spans cover a video with no gap between one item and the next")
def _(con):
    # index_passages locates a passage's item by bisecting span starts. If the
    # spans leave a hole, every passage in that hole is silently filed under
    # whichever item happens to precede it - a wrong agenda item on real
    # evidence, invisible in any count. The lookup now range-checks too, but
    # a gap still means those passages get no item at all, so it stays wrong.
    q = """FROM (SELECT video_id, end_idx,
                        LEAD(start_idx) OVER (PARTITION BY video_id
                                              ORDER BY start_idx) nxt
                 FROM item_spans) t
           WHERE nxt IS NOT NULL AND nxt <> end_idx + 1"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT video_id, end_idx, nxt, nxt - end_idx - 1 AS gap {q} LIMIT 6""", \
        "SELECT COUNT(*) FROM item_spans"


@check("items.no_duplicate_transcript",
       "one transcript-only item per span, not one per land_agenda run")
def _(con):
    q = """FROM (SELECT sp.video_id, sp.start_idx, COUNT(*) n
                 FROM item_spans sp JOIN agenda_items ai ON ai.id=sp.agenda_item_id
                 WHERE ai.source='transcript'
                 GROUP BY sp.video_id, sp.start_idx HAVING COUNT(*) > 1) t"""
    return count(con, f"SELECT COALESCE(SUM(n),0) {q}"), f"SELECT * {q} LIMIT 5", \
        """SELECT COUNT(*) FROM item_spans sp
           JOIN agenda_items ai ON ai.id=sp.agenda_item_id
           WHERE ai.source='transcript'"""


@check("items.source_shape",
       "published items carry a code; transcript items never do")
def _(con):
    q = ("FROM agenda_items WHERE (source='agenda' AND code IS NULL) "
         "OR (source='transcript' AND code IS NOT NULL)")
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT id, meeting_id, source, code, left(title,44) {q} LIMIT 5""", \
        "SELECT COUNT(*) FROM agenda_items"


@check("utterances.contiguous", "utterance idx runs 0..n-1 with no holes")
def _(con):
    q = """FROM (SELECT video_id, COUNT(*) n, MIN(idx) lo, MAX(idx) hi
                 FROM utterances GROUP BY video_id) t
           WHERE lo <> 0 OR hi <> n - 1"""
    return count(con, f"SELECT COUNT(*) {q}"), f"SELECT * {q} LIMIT 5", \
        "SELECT COUNT(DISTINCT video_id) FROM utterances"


@check("queue.no_stranded_claims",
       "no video is held by a worker that is no longer running")
def _(con):
    # `claim` only considers `claimed_by IS NULL`, so a worker killed mid-item
    # leaves a row nothing will ever pick up again - not errored, not pending,
    # just gone from the queue. The longest meeting in the archive is 8.2h and
    # diarization runs ~14x realtime, so nothing legitimately holds a claim for
    # six hours. Workers now call db.reclaim() at startup; this catches the
    # case where the worker never comes back at all.
    q = ("FROM videos WHERE claimed_by IS NOT NULL "
         "AND updated_at < now() - interval '6 hours'")
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT id, claimed_by, updated_at, downloaded, diarized, transcribed
        {q} LIMIT 6""", "SELECT COUNT(*) FROM videos"


# ---------------------------------------------------------------- semantics
# The one phase vocabulary the app filters on. Published agendas name their
# sections in prose and the segmenter uses these; both must land here or a
# filter matches half its rows and reports nothing wrong.
PHASES = ("call_to_order", "proclamation", "public_comment", "consent",
          "regular", "public_hearing", "staff_report", "board_reports",
          "recess", "adjourn", "other")


@check("phase.one_vocabulary",
       "agenda_items.phase only ever holds a canonical phase")
def _(con):
    q = "FROM agenda_items WHERE phase IS NOT NULL AND NOT (phase = ANY(%s))"
    return count(con, f"SELECT COUNT(*) {q}", (list(PHASES),)), f"""
        SELECT phase, source, COUNT(*) n {q.replace('%s', "'{" + ",".join(PHASES) + "}'")}
        GROUP BY phase, source ORDER BY n DESC LIMIT 8""", \
        "SELECT COUNT(*) FROM agenda_items WHERE phase IS NOT NULL"


@check("passages.phase_agrees",
       "a passage's phase is the one its agenda item carries")
def _(con):
    q = """FROM passages p JOIN agenda_items ai ON ai.id = p.agenda_item_id
           WHERE p.phase IS DISTINCT FROM ai.phase"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT p.phase AS passage_phase, ai.phase AS item_phase, COUNT(*) n {q}
        GROUP BY 1,2 ORDER BY n DESC LIMIT 6""", \
        """SELECT COUNT(*) FROM passages p
           JOIN agenda_items ai ON ai.id = p.agenda_item_id"""


@check("outcome.matches_disposition",
       "outcome is not contradicted by the sentence it came from")
def _(con):
    q = """FROM agenda_items WHERE outcome IS NOT NULL AND disposition IS NOT NULL
           AND ((outcome='approved' AND disposition ~* 'withdraw|denied')
             OR (outcome='approved' AND disposition ~* 'continue(d|s)? (the|this|to)')
             OR (outcome='withdrawn' AND disposition !~* 'withdraw'))"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT code, outcome, outcome_source, left(disposition,74) {q} LIMIT 6""", \
        """SELECT COUNT(*) FROM agenda_items
           WHERE outcome IS NOT NULL AND disposition IS NOT NULL"""


@check("minutes.no_subsidiary_disposition",
       "an item's recorded outcome is not a motion about something else")
def _(con):
    """A public hearing's minutes hold several motions and only one disposes
    of the item. The rest accept a member of the public's exhibits into the
    record, or decide to hear the item at all - and the parser used to keep
    whichever came FIRST, so 106 items (88 of them public hearings) read
    "Approved" where what was approved was somebody's paperwork. Two were
    outright denials shown as approvals.

    The pattern is `parse_minutes.SUBSIDIARY_SQL`, imported rather than
    restated so this check and the parser cannot drift into blessing exactly
    what the parser broke.
    """
    import parse_minutes
    rx = parse_minutes.SUBSIDIARY_SQL
    # Scoped to the items parse_minutes actually governs - those whose meeting
    # has a minutes document we can still read. 964 items on 10 meetings hold an
    # outcome derived from minutes that have since been re-linked to a same-day
    # sibling meeting; the parser cannot reach them to correct or clear them,
    # and `minutes.orphaned_outcomes` below is where that is reported. Mixing
    # the two would leave this permanently red for a reason it does not test.
    readable = """EXISTS (SELECT 1 FROM portal_events pe
                          JOIN portal_files pf ON pf.event_id = pe.id
                         WHERE pe.meeting_id = agenda_items.meeting_id
                           AND pf.kind = 'Minutes' AND pf.chars > 2000)"""
    q = f"""FROM agenda_items
            WHERE disposition IS NOT NULL AND {readable} AND disposition ~* %s"""
    # The examples query is run without parameters by main(), so the pattern
    # goes in as a literal and its apostrophe has to be doubled. The count
    # binds it properly and must NOT be.
    lit = q.replace("%s", "'" + rx.replace("'", "''") + "'")
    return count(con, f"SELECT COUNT(*) {q}", (rx,)), \
        f"SELECT code, outcome, outcome_source, left(disposition,80) {lit} LIMIT 6", \
        f"SELECT COUNT(*) FROM agenda_items WHERE disposition IS NOT NULL AND {readable}"


@check("minutes.orphaned_outcomes",
       "an outcome whose minutes are no longer attached to its meeting",
       review=True)
def _(con):
    """These items hold a disposition that `parse_minutes` can no longer see.

    Their meeting has no readable minutes document, because the portal event
    carrying it is now linked to a DIFFERENT meeting record for the same day -
    every one of the 10 meetings involved has exactly one same-day sibling and
    two or three recordings. So the day was split into two meeting rows and the
    agenda items went to one while the minutes went to the other.

    Review rather than failure, and deliberately not repaired: the stored
    outcomes came from real minutes and are mostly right, so deleting them
    would lose the record to tidy up a bookkeeping error. Fixing the LINKAGE
    is the actual repair, and it belongs in land_agenda, not here.
    """
    q = """FROM agenda_items ai
           WHERE ai.outcome IS NOT NULL AND NOT EXISTS (
             SELECT 1 FROM portal_events pe JOIN portal_files pf ON pf.event_id = pe.id
              WHERE pe.meeting_id = ai.meeting_id
                AND pf.kind = 'Minutes' AND pf.chars > 2000)"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT ai.meeting_id, m.date, COUNT(*) AS items
        {q.replace('FROM agenda_items ai', 'FROM agenda_items ai JOIN meetings m ON m.id = ai.meeting_id')}
        GROUP BY 1, 2 ORDER BY items DESC LIMIT 5""", \
        "SELECT COUNT(*) FROM agenda_items WHERE outcome IS NOT NULL"


@check("minutes.one_sentence_one_item",
       "two items in different sections never share one minutes sentence")
def _(con):
    """`code` is not unique within a meeting, and the writer treated it as if
    it were.

    39 (meeting_id, code) pairs carry more than one row and 25 sit in DIFFERENT
    sections - meeting 27's C1 is a Consent resolution AND a Public Hearings
    rezoning. `parse_minutes` resolved to a code and then updated
    `WHERE meeting_id=%s AND code=%s`, so one sentence landed on both: 58 rows
    across 28 pairs held a disposition parsed for a genuinely different item,
    and which sentence it was depended on the query plan (task #33).

    Restricted to rows in DIFFERENT sections deliberately. Two items in the
    SAME section really can share one sentence - "Approved the Consent Agenda"
    disposes of all of them at once - and 4 rows do, correctly. Across a
    section boundary there is no sentence that legitimately covers both.
    """
    q = """FROM agenda_items a
           JOIN agenda_items b ON b.meeting_id = a.meeting_id
                              AND b.code = a.code AND b.id <> a.id
           WHERE a.disposition IS NOT NULL
             AND b.disposition IS NOT DISTINCT FROM a.disposition
             AND lower(COALESCE(a.section,'')) <> lower(COALESCE(b.section,''))"""
    return count(con, f"SELECT COUNT(DISTINCT a.id) {q}"), f"""
        SELECT a.meeting_id, a.code, a.section, b.section AS other_section,
               left(a.disposition, 60) {q} LIMIT 6""", \
        """SELECT COUNT(*) FROM agenda_items a WHERE a.disposition IS NOT NULL
             AND EXISTS (SELECT 1 FROM agenda_items b
                          WHERE b.meeting_id = a.meeting_id AND b.code = a.code
                            AND b.id <> a.id)"""


@check("roster.terms_respected",
       "no voice is attributed to a commissioner outside their board term")
def _(con):
    q = """FROM speaker_identity si
           JOIN videos v ON v.id = si.video_id
           JOIN people p ON lower(p.surname) = lower(si.name)
           WHERE v.upload_date IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM board_terms bt
                             WHERE bt.person_id = p.id
                               AND v.upload_date::date
                                   BETWEEN bt.first_seen - 120 AND bt.last_seen + 400)
             AND NOT EXISTS (SELECT 1 FROM speaker_label sl
                             WHERE sl.video_id=si.video_id
                               AND sl.local_label=si.local_label)"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT si.name, v.upload_date, si.video_id {q}
        ORDER BY si.name LIMIT 6""", \
        """SELECT COUNT(*) FROM speaker_identity si
           JOIN videos v ON v.id = si.video_id
           JOIN people p ON lower(p.surname) = lower(si.name)
           WHERE v.upload_date IS NOT NULL"""


@check("speaker.rollcall_merged",
       "the clerk's roll call and the member's answer are one utterance",
       review=True)
def _(con):
    """Why the clerk wears a commissioner's name, measured.

    "District three, Commissioner Starkey. Aye." is TWO people in one
    utterance: the clerk calling the roll and the member answering it. No
    per-utterance attribution can be right about that row, and the name
    signal - a surname adjacent to a voice - lands on whichever of the two the
    diarizer gave the segment to. Archive-wide, 170 voices that read the roll
    carry a board member's name, 134 of them Starkey.

    It is deliberately NOT repaired by a naming rule. "A voice that reads the
    roll is not a commissioner" would be right for the clerk and wrong for the
    134 meetings where the merged label is mostly the member, and it would
    strip a correct name on evidence that is itself contaminated. The fix is
    upstream, in how these utterances are cut; §5.8's utterance-range
    correction is what expresses it meanwhile.
    """
    q = r"""FROM utterances u
            WHERE u.text ~* 'district \w+,? commissioner \w+\.?[[:space:]]+(aye|nay|here)'"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT u.video_id, u.idx, left(u.text, 90) AS text {q}
        ORDER BY length(u.text) DESC LIMIT 6""", \
        """SELECT COUNT(*) FROM utterances WHERE text ~* 'call the roll|district'"""


@check("speaker.queue_unreadable",
       "a clerk's queue announcement the parser can find no lead name in",
       review=True)
def _(con):
    """The residual, after the off-by-one fix.

    `speaker_id.ANNOUNCE` used to match only "followed by X", which is the
    speaker AFTER next: on 1OmEmpL-7qY Elaine Lance's turn was labelled Anthony
    Sikhenes, Anthony's was labelled Nancy Hazelwood, and Nancy - the only one
    the announcement could have named correctly - spoke unattributed.
    `speaker_id.queue_names()` now reads the announcement as the queue it is
    and takes the head of it.

    What this counts is what is LEFT: an announcement with no name in front of
    the first "followed by" for the parser to take. Usually the clerk gave a
    count rather than a name ("I have four individuals signed up"), so the
    person about to speak is genuinely unnamed and only self-identification can
    reach them.
    """
    # `~*` throughout: Postgres ARE has no inline (?i:...) group, so the
    # case-insensitive flag has to come from the operator.
    # Unreadable means no name the parser can take EITHER side of the split:
    # "Followed by Janet Gibbs." on its own is perfectly readable - the queue
    # head is simply the first name after it.
    q = """FROM utterances u
           WHERE u.text ~* '\\yfollowed by\\y'
             AND u.text !~ '[A-Z][a-z]+ [A-Z][a-z]+,?[[:space:]]+[Ff]ollowed [Bb]y'
             AND u.text !~ '[Ff]ollowed [Bb]y[[:space:]]+[A-Z][a-z]+ [A-Z][a-z]+'"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT u.video_id, u.idx, left(u.text, 90) AS text {q}
        ORDER BY u.video_id, u.idx LIMIT 8""", \
        """SELECT COUNT(*) FROM utterances WHERE text ~* '\\yfollowed by\\y'"""


@check("people.surname_unambiguous",
       "no surname is claimed by two different boards")
def _(con):
    # `people` is UNIQUE(surname), so two boards are sharing one namespace.
    # Today no surname collides, but the day a Planning Commissioner shares a
    # surname with a County Commissioner they silently become ONE person - and
    # every roster and term check downstream would then agree, because as far
    # as the schema is concerned they are the same human.
    q = """FROM (SELECT person_id FROM board_terms
                 GROUP BY person_id HAVING COUNT(DISTINCT body) > 1) t"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT p.surname, p.full_name,
               string_agg(bt.body || ' ' || bt.first_seen || '..' || bt.last_seen,
                          ' | ') bodies
        FROM board_terms bt JOIN people p ON p.id = bt.person_id
        WHERE bt.person_id IN (SELECT person_id {q})
        GROUP BY 1,2 LIMIT 5""", "SELECT COUNT(*) FROM people"


@check("people.full_name_is_a_name",
       "no stored full_name carries an honorific")
def _(con):
    # full_name stopped being reference data the day display_name() started
    # expanding a board surname into it: it is now what a reader sees on every
    # speaker chip, search hit and citation for that person. The older Planning
    # Commission agendas write "Mr. Calvin Branche" and roster.py kept the
    # whole matched string, so 11 of 28 people carried one and 33,122
    # utterances were a deploy away from being spoken by "Mr. Jaimie Girardi".
    #
    # roster.clean_name strips them on the way in now. This is the assertion
    # that the parser and the stored rows have not drifted apart again - and
    # the pattern is roster.HONORIFIC_SQL, which is where to change it.
    q = f"""FROM people WHERE full_name ~* '{roster.HONORIFIC_SQL}'"""
    return count(con, f"SELECT COUNT(*) {q}"), \
        f"SELECT surname, full_name {q} LIMIT 5", \
        "SELECT COUNT(*) FROM people WHERE full_name IS NOT NULL"


@check("cases.dates_sane", "a case's first_seen never follows its last_seen")
def _(con):
    return count(con, "SELECT COUNT(*) FROM cases WHERE first_seen > last_seen"), """
        SELECT id, first_seen, last_seen FROM cases WHERE first_seen > last_seen LIMIT 5""", \
        "SELECT COUNT(*) FROM cases"


@check("meetings.date_agrees",
       "a video's upload_date matches the meeting it is attached to")
def _(con):
    q = """FROM videos v JOIN meetings m ON m.id = v.meeting_id
           WHERE v.upload_date IS DISTINCT FROM m.date"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT v.id, v.upload_date, m.date, m.body {q} LIMIT 5""", \
        "SELECT COUNT(*) FROM videos WHERE meeting_id IS NOT NULL"


# ---------------------------------------------------------------- speakers
@check("speaker.labels_honoured",
       "every human label still resolves to that name in speaker_identity")
def _(con):
    q = """FROM speaker_label sl LEFT JOIN speaker_identity si
             ON si.video_id=sl.video_id AND si.local_label=sl.local_label
           WHERE si.name IS DISTINCT FROM sl.name"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT sl.video_id, sl.local_label, sl.name AS human,
               si.name AS stored {q} LIMIT 6""", \
        "SELECT COUNT(*) FROM speaker_label"


@check("speaker.ignore_honoured",
       "a human 'not a person' leaves no derived name behind, anywhere")
def _(con):
    """The veto that did nothing.

    `speaker_ignore` is a person writing "this voice is not a person". The
    pipeline held those voices out of clustering and then never retracted what
    an earlier run had decided about them, because `speaker_id` only ever
    inserted and updated: the single human veto in this archive - video
    T-fN-fVcYJM / SPEAKER_10 - was still stored as **Oakley, confidence 0.954**
    and still displayed on 17 utterances. R5.8.7 exactly inverted, and on the
    surface the admin console is being built against.

    Both layers are checked, because clearing either one alone still leaves a
    name on the page: `speaker_identity` names the voice directly, and a stale
    `utterances.cluster` hands it a name through `voice_name`.

    A HUMAN name is not a violation. If someone both vetoed a voice and
    labelled it, that is two people's statements disagreeing and not something
    the pipeline gets to resolve silently - so `us.human` is excluded and what
    is counted is only what the machine put back.
    """
    q = """FROM speaker_ignore sg
           WHERE EXISTS (SELECT 1 FROM speaker_identity si
                          WHERE si.video_id = sg.video_id
                            AND si.local_label = sg.local_label
                            AND si.name IS NOT NULL)
              OR EXISTS (SELECT 1 FROM utterance_speaker us
                          WHERE us.video_id = sg.video_id
                            AND us.local_label = sg.local_label
                            AND us.name IS NOT NULL AND NOT us.human)"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT sg.video_id, sg.local_label, sg.reason,
               (SELECT si.name FROM speaker_identity si
                 WHERE si.video_id = sg.video_id
                   AND si.local_label = sg.local_label) AS stored,
               (SELECT COUNT(*) FROM utterance_speaker us
                 WHERE us.video_id = sg.video_id
                   AND us.local_label = sg.local_label
                   AND us.name IS NOT NULL AND NOT us.human) AS displayed
        {q} LIMIT 6""", \
        "SELECT COUNT(*) FROM speaker_ignore"


@check("speaker.chair_anchor_intact",
       "no cluster is stored under a name the county's own roster contradicts")
def _(con):
    """`speaker_id` erased the chair anchor, and nothing noticed.

    `chair_anchor` decides which voice cluster belongs to which commissioner
    from two published facts - the roster block on the county's agenda names
    who CHAIRED, and the presiding officer reads a fixed script - and records
    it as `source='chair'`. `speaker_id` is the only writer of that column and
    its upsert wrote NULL over every row, so one bare `refresh.sh speakers`
    reverted the lot: measured, ZERO rows carried source='chair' and three
    clusters covering 69,596 utterances held a name the anchor contradicts
    (task #31).

    Recomputed here rather than compared against a stored flag, because the
    erasure destroyed the flag - a check reading `source` would have gone green
    on an archive with no anchor left in it at all. The thresholds are imported
    from chair_anchor so the two cannot drift.

    chair_anchor also refuses to rewrite a cluster that holds more than one
    person, and that gate needs the voice embeddings; it is not applied here.
    So this is a SUPERSET: it reports a contradiction chair_anchor might
    decline to act on. It has no false positives on this archive today - the
    one cluster it examines and does not flag is the one the anchor confirms.
    """
    import chair_anchor
    bad = []
    for cluster, tally in chair_anchor.evidence(con).items():
        total = sum(tally.values())
        if total < chair_anchor.MIN_LINES:
            continue
        who, n = tally.most_common(1)[0]
        if n / total < chair_anchor.MIN_SHARE:
            continue
        stored = con.execute("""SELECT name FROM speaker_identity
                                WHERE cluster = %s AND name IS NOT NULL
                                GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1""",
                             (cluster,)).fetchone()
        if stored and stored[0] and stored[0].lower() != who.lower():
            utts = con.execute("SELECT COUNT(*) FROM utterances WHERE cluster = %s",
                               (cluster,)).fetchone()[0]
            bad.append(f"cluster {cluster}: the chair script says {who}, "
                       f"{total} lines at {n/total:.0%} - stored as "
                       f"{stored[0]}, on {utts:,} utterances")
    lit = lambda s: "'" + s.replace("'", "''") + "'"
    return len(bad), "SELECT " + (
        " UNION ALL SELECT ".join(f"{lit(b)} AS contradicted" for b in bad[:6])
        if bad else "'ok' AS note"), \
        """SELECT COUNT(DISTINCT u.cluster) FROM utterances u
             JOIN videos v ON v.id = u.video_id
             JOIN meeting_roster mr ON mr.meeting_id = v.meeting_id
                                   AND mr.office = 'chair'
            WHERE u.cluster IS NOT NULL"""


@check("speaker.cluster_known",
       "every clustered utterance belongs to a cluster speaker_identity knows")
def _(con):
    q = """FROM utterances u WHERE u.cluster IS NOT NULL AND NOT EXISTS
             (SELECT 1 FROM speaker_identity si WHERE si.cluster = u.cluster)"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT u.video_id, u.cluster, COUNT(*) n {q}
        GROUP BY u.video_id, u.cluster LIMIT 5""", \
        "SELECT COUNT(*) FROM utterances WHERE cluster IS NOT NULL"


@check("speaker.one_name_per_voice",
       "voice_name resolves each (meeting, voice) to exactly one name")
def _(con):
    q = """FROM (SELECT video_id, cluster, COUNT(*) n FROM voice_name
                 GROUP BY video_id, cluster HAVING COUNT(*) > 1) t"""
    return count(con, f"SELECT COUNT(*) {q}"), f"SELECT * {q} LIMIT 5", \
        "SELECT COUNT(*) FROM voice_name"


@check("speaker.voice_coheres",
       "a recurring name is carried by a voice, not by being mentioned a lot")
def _(con):
    # A real person's voice consolidates into a few clusters however many
    # meetings they attend: the commissioners run 0.06-0.13 distinct clusters
    # per meeting over 100-260 meetings. A name approaching 1.0 has a brand-new
    # voice every time, which means it was never matched by voice at all - it
    # was attached by someone SAYING it nearby. Barbara Wilhite sat at 1.07
    # across 294 meetings, Justin Grant at 0.48, and both had unrelated
    # people's testimony filed under their name.
    #
    # 0.40 sits in the empty gap between the two regimes; every legitimate
    # recurring speaker measured is below 0.25.
    q = """FROM (SELECT name, COUNT(DISTINCT video_id) m,
                        COUNT(DISTINCT cluster)::float / COUNT(DISTINCT video_id) r
                 FROM speaker_identity WHERE name IS NOT NULL
                 GROUP BY name HAVING COUNT(DISTINCT video_id) >= 8) t
           WHERE r > 0.40"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT name, m AS meetings, ROUND(r::numeric, 2) AS clusters_per_meeting
        {q} ORDER BY r DESC LIMIT 6""", \
        """SELECT COUNT(*) FROM (SELECT name FROM speaker_identity
           WHERE name IS NOT NULL GROUP BY name
           HAVING COUNT(DISTINCT video_id) >= 8) t"""


@check("speaker.one_voice_per_meeting",
       "review: a board member attached to two voices in one meeting",
       review=True)
def _(con):
    # Reported as a REVIEW list, not a defect: diarization genuinely splits one
    # person across two labels when they move mic or the audio shifts, so some
    # of these are correct. But it is also how a wrong attribution looks - in
    # wSkGsd74JPc, utterances 116 and 118 are SPEAKER_04 and 117 is SPEAKER_05,
    # all three shown as Mariano, and one of them is somebody else. Nothing in
    # the UI could express "not this stretch", which is why it stayed.
    # Ordered by how many utterances the split actually touches, not by how
    # many voices it splits into. A review list is only workable if its head is
    # the row worth fixing first; this one used to be sorted by voice count,
    # which put a 3-utterance stutter above a 500-utterance misattribution.
    q = """FROM (SELECT si.video_id, si.name,
                        COUNT(DISTINCT si.local_label) voices,
                        (SELECT COUNT(*) FROM utterance_speaker us
                          WHERE us.video_id = si.video_id
                            AND us.name = si.name) utts
                 FROM speaker_identity si
                 JOIN people p ON lower(p.surname) = lower(si.name)
                 WHERE si.name IS NOT NULL
                 GROUP BY 1, 2 HAVING COUNT(DISTINCT si.local_label) > 1) t"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT name, video_id, voices, utts {q}
        ORDER BY utts DESC, voices DESC LIMIT 8""", \
        """SELECT COUNT(*) FROM (SELECT si.video_id, si.name
           FROM speaker_identity si
           JOIN people p ON lower(p.surname) = lower(si.name)
           WHERE si.name IS NOT NULL GROUP BY 1, 2) t"""


@check("speaker.body_respected",
       "no voice is shown as a member of a board that meeting does not belong to")
def _(con):
    # The invariant the whole roster apparatus exists to enforce, asserted on
    # what is actually DISPLAYED rather than on what speaker_id decided. Those
    # were different things: the per-meeting guard blanked the assignment and a
    # global cluster->name view handed the name straight back, putting County
    # Commissioners in 10,715 Planning Commission utterances.
    q = """FROM utterances u
           JOIN voice_name vn ON vn.video_id = u.video_id
                             AND vn.cluster = u.cluster
           JOIN videos v   ON v.id = u.video_id
           JOIN people p   ON lower(p.surname) = lower(vn.name)
           WHERE NOT EXISTS (
               SELECT 1 FROM board_terms bt
               WHERE bt.person_id = p.id
                 AND bt.body = CASE v.kind WHEN 'planning'
                                   THEN 'Planning Commission'
                                   ELSE 'Board of County Commissioners' END)"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT vn.name, v.kind, COUNT(*) n {q}
        GROUP BY 1,2 ORDER BY n DESC LIMIT 6""", \
        """SELECT COUNT(*) FROM utterances u
           JOIN voice_name vn ON vn.video_id=u.video_id AND vn.cluster=u.cluster
           JOIN people p ON lower(p.surname)=lower(vn.name)"""


# ---------------------------------------------------------------- index
@check("bm25.in_step", "BM25 postings describe the passages that exist now")
def _(con):
    n = count(con, "SELECT COUNT(*) FROM passages")
    docs = count(con, "SELECT COALESCE(MAX(n_docs),0) FROM bm25_stats")
    orphan = count(con, """SELECT COUNT(*) FROM passage_len pl
        WHERE NOT EXISTS (SELECT 1 FROM passages p WHERE p.id = pl.passage_id)""")
    return orphan + (0 if abs(docs - n) <= 1 else 1), f"""
        SELECT {n} AS passages, {docs} AS bm25_docs, {orphan} AS orphan_postings""", \
        "SELECT COUNT(*) FROM passage_len"


@check("embeddings.present", "every passage has a vector")
def _(con):
    return count(con, "SELECT COUNT(*) FROM passages WHERE embedding IS NULL"), """
        SELECT id, video_id, left(text,50) FROM passages
        WHERE embedding IS NULL LIMIT 5""", \
        "SELECT COUNT(*) FROM passages"


# ------------------------------------------------------ corrections (§5.8)
@check("override.in_range",
       "every correction addresses utterances that exist in its recording")
def _(con):
    # An override is keyed on (video_id, idx range). A range that runs past the
    # end of a transcript silently corrects nothing, which is worse than
    # failing: the operator believes the fix landed.
    q = """FROM speaker_override o
           WHERE NOT EXISTS (SELECT 1 FROM utterances u
                              WHERE u.video_id = o.video_id AND u.idx = o.start_idx)
              OR NOT EXISTS (SELECT 1 FROM utterances u
                              WHERE u.video_id = o.video_id AND u.idx = o.end_idx)"""
    return count(con, f"SELECT COUNT(*) {q}"), \
        f"SELECT o.id, o.video_id, o.start_idx, o.end_idx, o.action {q} LIMIT 5", \
        "SELECT COUNT(*) FROM speaker_override"


@check("label.surname_form",
       "a human statement names a board member by SURNAME, not full name",
       review=True)
def _(con):
    # "Mike Wells" does not join people.surname = 'Wells', so a full-name
    # label or override bypasses the roster guard AND the split-voice review
    # check, and search holds two speakers where there is one. Observed on the
    # console's first day of use: the operator labeled a voice "Mike Wells",
    # the queue row vanished, and nothing on any surface could show the
    # mistake again. The console now canonicalises on write
    # (admin.canonical_name); this catches what predates it, what the CLI
    # writes, and any path the canonicaliser does not cover.
    q = """FROM (SELECT 'label' AS kind, video_id, local_label AS place, name
                 FROM speaker_label
                 UNION ALL
                 SELECT 'override', video_id, start_idx::text, name
                 FROM speaker_override WHERE name IS NOT NULL) h
           JOIN people p ON lower(p.full_name) = lower(h.name)
                        AND lower(p.surname) <> lower(h.name)"""
    return count(con, f"SELECT COUNT(*) {q}"), \
        f"SELECT h.kind, h.video_id, h.place, h.name, p.surname AS store_as {q} LIMIT 5", \
        "SELECT COUNT(*) FROM speaker_label"


@check("override.human_outranks_machine",
       "a human correction is what the reader sees, at every granularity")
def _(con):
    # R5.8.7 / R9.5. If a range is corrected and the view still shows the
    # derived name, the correction is decorative. Nothing is more corrosive
    # than a fix that appears to have been accepted and was not.
    q = """FROM speaker_override o
           JOIN utterance_speaker us ON us.video_id = o.video_id
                AND us.idx BETWEEN o.start_idx AND o.end_idx
           WHERE o.status = 'applied' AND us.basis <> 'override'"""
    return count(con, f"SELECT COUNT(*) {q}"), \
        f"SELECT o.id, us.idx, us.name, us.basis {q} LIMIT 5", \
        """SELECT COUNT(*) FROM speaker_override o
           JOIN utterance_speaker us ON us.video_id = o.video_id
                AND us.idx BETWEEN o.start_idx AND o.end_idx
           WHERE o.status = 'applied'"""


@check("override.pending_changes_nothing",
       "an unreviewed proposal never alters what a reader is shown")
def _(con):
    # R5.8.8. A public submission is untrusted input; it may mark a name as
    # contested and must not replace it.
    q = """FROM speaker_override o
           JOIN utterance_speaker us ON us.video_id = o.video_id
                AND us.idx BETWEEN o.start_idx AND o.end_idx
           WHERE o.status = 'pending' AND us.basis = 'override'
             AND NOT EXISTS (SELECT 1 FROM speaker_override a
                              WHERE a.status = 'applied' AND a.video_id = o.video_id
                                AND a.start_idx <= us.idx AND a.end_idx >= us.idx)"""
    return count(con, f"SELECT COUNT(*) {q}"), f"SELECT o.id, us.idx {q} LIMIT 5", \
        "SELECT COUNT(*) FROM speaker_override WHERE status = 'pending'"


@check("speaker.name_supported",
       "no one is shown speaking at a meeting their body and term do not place them at")
def _(con):
    # The guard that took cross-body misattribution from 54,000 to 0. It is
    # asserted against the RESOLVED name, so it covers the per-voice path and
    # the cluster fallback together - the two used to be gated separately and
    # that is how the last leak happened.
    q = """FROM utterance_speaker us
           WHERE us.name IS NOT NULL AND us.basis <> 'override'
             AND NOT name_supported(us.video_id, us.name)"""
    return count(con, f"SELECT COUNT(*) {q}"), \
        f"SELECT us.video_id, us.idx, us.name, us.basis {q} LIMIT 5", \
        "SELECT COUNT(*) FROM utterance_speaker WHERE name IS NOT NULL"


@check("override.roundtrip",
       "a correction actually takes effect, proved by making one and rolling it back")
def _(con):
    """Exercise the correction path instead of reporting EMPTY about it.

    The three checks above range over a table that is empty until someone makes
    a correction, so they proved nothing - which is the failure gotcha 27 is
    about. This one proves the mechanism itself, the same way segment.preflight
    proves the INSERT before an hour of LLM calls is spent against it: do the
    write, assert the reader sees it, roll it back. Nothing is left behind.

    Guards the two properties that matter and cannot be checked statically:
    a named correction replaces the derived name, and `detach` - the operation
    the old whole-voice model could not express at all - clears it without
    falling through to the machine's answer.
    """
    row = con.execute("""SELECT video_id, idx FROM utterances
                         ORDER BY video_id, idx LIMIT 1""").fetchone()
    if not row:
        return 0, "SELECT 'no utterances to test against' AS note", \
            "SELECT COUNT(*) FROM utterances"
    vid, idx = row[0], row[1]
    bad = []
    with con.transaction(force_rollback=True):
        con.execute("""INSERT INTO speaker_override
            (video_id, start_idx, end_idx, action, name, note, author)
            VALUES (%s,%s,%s,'reassign','__audit_probe__','audit roundtrip','audit')""",
            (vid, idx, idx))
        r = con.execute("""SELECT name, basis, human FROM utterance_speaker
                           WHERE video_id=%s AND idx=%s""", (vid, idx)).fetchone()
        if not r or r[0] != "__audit_probe__" or r[1] != "override" or not r[2]:
            bad.append(f"reassign did not reach the reader: {tuple(r) if r else None}")

        con.execute("""INSERT INTO speaker_override
            (video_id, start_idx, end_idx, action, name, note, author)
            VALUES (%s,%s,%s,'detach',NULL,'audit roundtrip','audit')""",
            (vid, idx, idx))
        r = con.execute("""SELECT name, basis FROM utterance_speaker
                           WHERE video_id=%s AND idx=%s""", (vid, idx)).fetchone()
        if not r or r[0] is not None or r[1] != "override":
            bad.append(f"detach fell through to a derived name: {tuple(r) if r else None}")

    left = count(con, "SELECT COUNT(*) FROM speaker_override WHERE author = 'audit'")
    if left:
        bad.append(f"{left} probe rows survived the rollback")
    lit = lambda s: "'" + s.replace("'", "''") + "'"
    return len(bad), "SELECT " + (
        " UNION ALL SELECT ".join(f"{lit(b)} AS failure" for b in bad)
        if bad else "'ok' AS note"), \
        "SELECT 2 AS assertions"


@check("speaker.no_disproved_names",
       "nobody is shown as a person their own voice was measured not to be")
def _(con):
    # The gate that bin/affinity.py exists to enforce. Cluster inheritance
    # assumes cluster membership means same person; measured, that is wrong for
    # 26.5% of the voices it names, and wrong by a mile - 510 of 521 failures
    # sit below 0.35, where no same-person pair has ever been observed.
    q = """FROM utterance_speaker us
           JOIN voice_affinity va ON va.video_id = us.video_id
                AND va.local_label = us.local_label AND va.name = us.name
           WHERE us.basis = 'cluster' AND va.similarity < 0.70"""
    return count(con, f"SELECT COUNT(*) {q}"), \
        f"SELECT us.video_id, us.idx, us.name, va.similarity {q} LIMIT 5", \
        "SELECT COUNT(*) FROM utterance_speaker WHERE basis = 'cluster'"


@check("speaker.affinity_measured",
       "voices that could inherit a cluster name but have never been scored",
       review=True)
def _(con):
    # Not a failure: the gate withholds a name only where there is evidence
    # AGAINST it, so an unmeasured voice keeps its inherited name. This counts
    # how much of the cluster fallback is still resting on the assumption
    # rather than on a measurement. Run bin/affinity.py to shrink it.
    q = """FROM (SELECT DISTINCT u.video_id, u.local_label, vn.name
                 FROM utterances u
                 JOIN voice_name vn ON vn.video_id = u.video_id
                                   AND vn.cluster = u.cluster
                 WHERE vn.name IS NOT NULL) v
           WHERE NOT EXISTS (SELECT 1 FROM voice_affinity va
                              WHERE va.video_id = v.video_id
                                AND va.local_label = v.local_label
                                AND va.name = v.name)"""
    return count(con, f"SELECT COUNT(*) {q}"), f"SELECT * {q} LIMIT 5", f"""
        SELECT COUNT(*) FROM (SELECT DISTINCT u.video_id, u.local_label
                              FROM utterances u
                              JOIN voice_name vn ON vn.video_id = u.video_id
                                                AND vn.cluster = u.cluster
                              WHERE vn.name IS NOT NULL) t"""


@check("passages.speaker_agrees",
       "the name baked into a passage is the name the transcript gives")
def _(con):
    # passages.speaker is a denormalised copy that feeds search, the speaker
    # filter and every quote the agent prints. When it drifts from the
    # transcript the archive contradicts itself, and the agent is the half
    # nobody is reading closely enough to notice.
    # Membership, not majority. Utterances OVERLAP in time - two people talk at
    # once - so a passage's time window catches speech from voices that are not
    # its own, and picking the modal name off that window mislabels short
    # passages on a tie. The invariant that actually holds is that the baked
    # name is one the transcript gives somewhere inside the passage.
    # Joined on the INDEX range, not the time window. A passage is built from a
    # contiguous run of utterances and stores their idx bounds exactly, while
    # `start` and `end` are doubles that do not always round-trip: three of the
    # eight violations this check first reported had their utterance sitting a
    # float hair outside `u.start >= p.start`, so the check could not tell a
    # stale name from its own boundary arithmetic. Integers cannot drift.
    q = """FROM passages p
           WHERE p.speaker <> '(exchange)'
             AND NOT EXISTS (
                 SELECT 1 FROM utterances u
                 JOIN utterance_speaker us
                   ON us.video_id = u.video_id AND us.idx = u.idx
                 WHERE u.video_id = p.video_id
                   AND u.idx BETWEEN p.start_idx AND p.end_idx
                   AND us.name IS NOT DISTINCT FROM p.speaker)"""
    return count(con, f"SELECT COUNT(*) {q}"), \
        f"SELECT p.id, p.video_id, p.speaker, p.start {q} LIMIT 5", \
        "SELECT COUNT(*) FROM passages WHERE speaker <> '(exchange)'"


@check("schema.matches_definition",
       "the live database has the columns bin/schema.sql defines")
def _(con):
    """Nothing has ever compared the two, and they have drifted before.

    Gotcha 63: `schema.sql` had fallen behind the live database, and replaying
    it silently dropped a guard from `utterance_speaker`, handing disproved
    names back to 8,795 utterances. Gotcha 68: the same file could not create
    the schema from scratch at all, because two views referenced tables defined
    below them - which nobody noticed because the production database was built
    statement by statement and never from the file.

    Both directions bite. A column added by hand with ALTER TABLE and not
    written into schema.sql vanishes on the next rebuild; a column added to
    schema.sql and not applied breaks the code that expects it. This compares
    the CREATE TABLE statements in the file against information_schema and
    reports either way, so the drift is caught by a run rather than by a
    surprise months later.

    Views are deliberately not compared. They are `CREATE OR REPLACE` and are
    reapplied wholesale, so their text is the definition; it is the tables that
    accumulate hand-edits.

    BOTH forms count. This file adds 19 columns with
    `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` rather than editing the original
    CREATE - which is what lets it be replayed against a live database - so a
    parser that reads only CREATE bodies reports every one of them as drift.
    The first version of this check did exactly that and called 18 false
    positives a finding.
    """
    import re
    want = {}
    sql = open(db.SCHEMA_SQL).read()
    for m in re.finditer(
            r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\n\);", sql, re.S):
        table, body = m.group(1), m.group(2)
        cols = set()
        for line in body.split("\n"):
            line = line.strip()
            if not line or line.startswith("--"):
                continue
            first = line.split()[0].strip('"')
            if first.upper() in ("UNIQUE", "PRIMARY", "FOREIGN", "CHECK",
                                 "CONSTRAINT", "EXCLUDE"):
                continue
            cols.add(first)
        want[table] = cols
    for m in re.finditer(
            r"ALTER TABLE\s+(\w+)\s+ADD COLUMN(?:\s+IF NOT EXISTS)?\s+(\w+)", sql):
        want.setdefault(m.group(1), set()).add(m.group(2))

    live = {}
    for r in con.execute("""SELECT table_name, column_name
                              FROM information_schema.columns
                             WHERE table_schema = 'public'"""):
        live.setdefault(r[0], set()).add(r[1])

    drift = []
    for table, cols in sorted(want.items()):
        if table not in live:
            drift.append((table, "TABLE MISSING from the database"))
            continue
        for c in sorted(cols - live[table]):
            drift.append((table, f"{c}: in schema.sql, NOT in the database"))
        for c in sorted(live[table] - cols):
            drift.append((table, f"{c}: in the database, NOT in schema.sql"))

    # Reported through the same machinery as every other check, so it fails a
    # run rather than printing a note somebody scrolls past.
    if drift:
        vals = ", ".join(
            "(%s, %s)" % (_lit(t), _lit(d)) for t, d in drift)
        return len(drift), \
            f"SELECT * FROM (VALUES {vals}) AS t(relation, drift)", None
    return 0, None, None


def _lit(s):
    return "'" + str(s).replace("'", "''") + "'"


@check("speaker.cluster_only_names",
       "how much of the archive is named ONLY by the archive-wide cluster majority",
       review=True)
def _(con):
    # Not a defect - it is how most speakers get a name at all - but it is the
    # weakest basis in the precedence and the one that put two different women
    # under a single name. Worth watching, and worth curating down.
    q = "FROM utterance_speaker WHERE basis = 'cluster'"
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT name, COUNT(*) n, COUNT(DISTINCT video_id) recordings
        {q} AND name IS NOT NULL GROUP BY name ORDER BY n DESC LIMIT 8""", \
        "SELECT COUNT(*) FROM utterance_speaker WHERE name IS NOT NULL"


# ---------------------------------------------------------------- redaction
#
# A redaction is only worth anything if the address is gone from EVERY surface
# a reader can reach, and there are six of them holding the same words: the
# transcript, the passage text, the passage's search_text, the BM25 postings,
# the full-text vector, and the prose of a saved answer. bin/redact.py removes
# it at the source and re-indexes, so the first five should follow - but
# "should follow" is exactly the assumption that leaves an address in the
# search index and nowhere else, and a spot check of a few rows cannot find
# that. The sixth is the odd one out and gets its own check below: it is the
# only copy in the archive, because generated prose cannot be recomputed.
#
# So the invariant is stated over every applied redaction at once: no span we
# took out may still be found anywhere. This is the check that makes the
# feature true rather than intended.
@check("redaction.gone_from_transcript",
       "no redacted address is still in the transcript it was removed from")
def _(con):
    q = """FROM redaction r JOIN utterances u
             ON u.video_id = r.video_id AND u.idx = r.idx
           WHERE r.status = 'applied' AND position(r.span in u.text) > 0"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT r.id, r.video_id, r.idx, left(r.span, 40) AS span {q} LIMIT 5""", \
        "SELECT COUNT(*) FROM redaction WHERE status = 'applied'"


@check("redaction.gone_from_index",
       "no redacted address survives in the passages search reads")
def _(con):
    # Both columns: `text` is what a result shows a reader and `search_text`
    # is what the ranking reads. They are built from the same utterances and
    # they have drifted apart before.
    q = """FROM redaction r JOIN passages p
             ON p.video_id = r.video_id
            AND r.idx BETWEEN p.start_idx AND p.end_idx
           WHERE r.status = 'applied'
             AND (position(r.span in p.text) > 0
                  OR position(r.span in coalesce(p.search_text, '')) > 0)"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT r.id, p.id AS passage, r.video_id, left(r.span, 40) AS span
        {q} LIMIT 5""", \
        "SELECT COUNT(*) FROM redaction WHERE status = 'applied'"


@check("redaction.unfindable",
       "a redacted address cannot be found by searching for it")
def _(con):
    # The end-to-end statement, made the way a person would make it: take the
    # words out of the span, put them through the same full-text query search
    # uses, and assert nothing comes back. It catches what the two checks
    # above cannot - a stale `tsv`, a posting left in the BM25 tables - by
    # asking the question a reader would ask.
    q = """FROM redaction r
           WHERE r.status = 'applied' AND EXISTS (
             SELECT 1 FROM utterances u
             WHERE u.video_id = r.video_id
               AND u.tsv @@ phraseto_tsquery('english', r.span))"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT r.id, r.video_id, left(r.span, 40) AS span {q} LIMIT 5""", \
        "SELECT COUNT(*) FROM redaction WHERE status = 'applied'"


# A saved answer's citations, one row each, expanded ONCE so the two checks
# below can hash-join against `redaction` instead of re-expanding every answer's
# jsonb for every applied redaction. The CASE is the guard that keeps a
# malformed row from raising: jsonb_array_elements refuses anything that is not
# an array, and a check that errors is a check that stops being run.
#
# Two shapes because the two checks ask different questions of the same data.
# CITED_VIDEOS is "which recordings does this answer quote from", and must NOT
# drop a citation with a malformed range - the answer's PROSE is what is being
# tested and a bad index cannot excuse it. CITED_RANGES is "which utterances
# does it cover", where a range that is not a number cannot be compared at all
# and is dropped.
_CITED = """
      FROM answers a
      CROSS JOIN LATERAL jsonb_array_elements(
            CASE WHEN jsonb_typeof(a.cites -> 'passages') = 'array'
                 THEN a.cites -> 'passages' ELSE '[]'::jsonb END) p"""

CITED_VIDEOS = f"""SELECT DISTINCT a.id AS answer_id, a.answer, a.question,
                          p ->> 'video_id' AS video_id {_CITED}"""

CITED_RANGES = f"""SELECT DISTINCT a.id AS answer_id, a.question,
                          p ->> 'video_id' AS video_id,
                          (p ->> 'start_idx')::int AS lo,
                          (p ->> 'end_idx')::int   AS hi {_CITED}
                    WHERE jsonb_typeof(p -> 'start_idx') = 'number'
                      AND jsonb_typeof(p -> 'end_idx')   = 'number'"""


@check("redaction.gone_from_answers",
       "no saved answer still carries an address a redaction removed")
def _(con):
    # The sixth surface, and the only text in this archive that is a COPY. A
    # saved answer (web/answers.py) stores what it cited and reads its quotes
    # back out of `passages` at render time, so the five checks above cover
    # them. Its prose cannot be read back from anywhere - it is what the model
    # wrote, quoting what it cited - and it sits at a URL somebody may have
    # circulated. bin/redact.py takes the span out of that prose and leaves the
    # row standing; this is what says it actually happened.
    #
    # LOCATING is load-bearing, and it is here because of a real failure. The
    # first version of this check flagged a correct answer over the word
    # 'Florida' - an applied span - in the sentence "Florida Statute
    # 163.31801(6) caps annual impact-fee increases". A number and a place-name
    # together locate a house; either half alone is a ZIP, a town or a state,
    # and the applied set is full of halves. A length floor cannot separate
    # them either: '9641 Jerome' is eleven characters and is somebody's house.
    # The length arm catches the addresses the recogniser spelled out in words,
    # which carry no digit at all. Keep this identical to redact.LOCATING -
    # grep the name to find both, they have to agree or the check and the fix
    # are describing different things.
    #
    # jsonb_array_elements RAISES on a non-array, and a check that errors is a
    # check that stops being run. Same guard as redact.scrub_answers.
    #
    # CITED_VIDEOS expands each answer's citations ONCE and the join does the
    # rest. Written as an EXISTS it re-expanded every answer's jsonb for every
    # one of the 3,440 applied redactions: measured over 5,000 answers, 4.1s
    # here and 15.8s for the review check below, both growing linearly with the
    # table. This shape is 0.06s and 0.04s for identical counts, planted
    # violations included. An audit that runs after every rebuild has to stay
    # cheap or it stops being run, which is the same failure as one that errors.
    locating = ("((r.span ~ '[0-9]' AND r.span ~ '[A-Za-z]{3}')"
                " OR length(r.span) >= 20)")
    q = f"""FROM redaction r JOIN ({CITED_VIDEOS}) c
              ON c.video_id = r.video_id
             AND {locating} AND strpos(c.answer, r.span) > 0
            WHERE r.status = 'applied'"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT r.id, c.answer_id AS answer, left(r.span, 40) AS span,
               left(c.question, 40) AS question {q} LIMIT 5""", \
        "SELECT COUNT(*) FROM answers"


@check("redaction.answers_quoting_a_redacted_line",
       "saved answers that cited a line a redaction removed - read the wording",
       review=True)
def _(con):
    # The one case a string search cannot settle, and therefore the one this
    # file refuses to settle on its own.
    #
    # `scrub_answers` replaces a span it can find. It cannot find a PARAPHRASE:
    # an answer that cited the moment and wrote the address in its own words,
    # reordered or half of it. Nothing can, so nothing here pretends to - these
    # are listed for a person, which is bin/redact.py's own rule (a detector
    # proposes, a person decides) applied to the residue.
    #
    # review=True on purpose: a non-zero count is expected and is not a defect.
    # An answer citing a passage that happened to contain an address is normal,
    # and usually its prose says nothing about the address at all.
    #
    # The range guards are here rather than in CITED_RANGES' shared shape for a
    # reason: a citation whose start_idx is not a number cannot be compared, and
    # dropping it is right HERE but would be wrong in the text check above,
    # where a malformed range must not hide an answer whose prose carries the
    # span. Same data, two different questions.
    q = f"""FROM redaction r JOIN ({CITED_RANGES}) c
              ON c.video_id = r.video_id AND r.idx BETWEEN c.lo AND c.hi
            WHERE r.status = 'applied'"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT c.answer_id AS answer, left(c.question, 50) AS question,
               r.video_id, r.idx {q} LIMIT 5""", \
        "SELECT COUNT(*) FROM answers"


# The other half of the guarantee. The four checks above say the address is
# gone from everything a reader can reach; these two say it is still in the
# record, and that what is published is exactly derivable from it.
#
# That is the whole point of `text_raw`: a redaction removes an address from
# what the archive PUBLISHES without editing what the recogniser heard. If the
# raw column were quietly rewritten too, a revert would have nothing to
# recompute from and the archive would have destroyed part of the record to
# protect somebody - which is not the trade anyone agreed to.


@check("redaction.raw_preserved",
       "the ASR still holds what a redaction removed from the publication")
def _(con):
    q = """FROM redaction r JOIN utterances u
             ON u.video_id = r.video_id AND u.idx = r.idx
           WHERE r.status = 'applied'
             AND position(r.span in COALESCE(u.text_raw, '')) = 0"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT r.id, r.video_id, r.idx, left(r.span, 40) AS span {q} LIMIT 5""", \
        "SELECT COUNT(*) FROM redaction WHERE status = 'applied'"


@check("utterances.published_is_derived",
       "an utterance with nothing applied to it publishes its ASR unchanged")
def _(con):
    # Stated over the 295,000 lines that have NO applied redaction, where the
    # two columns must be identical. Those are the rows where a stray write to
    # `text` would hide - the ones with a redaction are covered by the three
    # checks above, and between them the pair pins down every line in the
    # archive. text_raw IS NULL is a violation too: it means an ingest wrote
    # the publication without recording what it was derived from.
    q = """FROM utterances u
           WHERE NOT EXISTS (SELECT 1 FROM redaction r
                              WHERE r.video_id = u.video_id AND r.idx = u.idx
                                AND r.status = 'applied')
             AND (u.text_raw IS NULL OR u.text_raw <> u.text)"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT u.video_id, u.idx, left(u.text, 50) AS published,
               left(COALESCE(u.text_raw, '(null)'), 50) AS raw {q} LIMIT 5""", \
        "SELECT COUNT(*) FROM utterances"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--verbose", action="store_true", help="show examples for OK too")
    args = ap.parse_args()
    con = db.connect(autocommit=True)

    bad = empty = ran = todo = 0
    for name, why, fn, review in CHECKS:
        if args.only and args.only not in name:
            continue
        ran += 1
        try:
            res = fn(con)
            n, sample = res[0], res[1]
            scope = count(con, res[2]) if len(res) > 2 and res[2] else None
        except Exception as e:
            print(f"  ERROR  {name:<32} {type(e).__name__}: {str(e)[:70]}")
            bad += 1
            continue
        if review:
            over = "" if scope is None else f"  [{scope:,} rows]"
            print(f"  {'todo' if n else 'ok  '}  {name:<32} {why}{over}")
            if n:
                todo += 1
                print(f"        {n:,} to review")
                if sample:
                    for r in con.execute(sample):
                        print("        " + "  ".join(
                            f"{k}={v}" for k, v in dict(r).items())[:150])
            continue
        # An invariant over an empty set is vacuously true and tells you
        # nothing. Reporting it as "ok" is how a stale rebuild reads as a
        # healthy one - so it gets its own word.
        if n:
            flag, bad = "FAIL", bad + 1
        elif scope == 0:
            flag, empty = "EMPTY", empty + 1
        else:
            flag = "ok  "
        over = "" if scope is None else f"  [{scope:,} rows]"
        print(f"  {flag}  {name:<32} {why}{over}")
        if flag == "EMPTY":
            print("        examined nothing - this check proved nothing")
        if n:
            print(f"        {n:,} violations")
        if (n or args.verbose) and sample:
            for r in con.execute(sample):
                print("        " + "  ".join(f"{k}={v}" for k, v in dict(r).items())[:150])

    tail = f" · {empty} examined nothing" if empty else ""
    tail += f" · {todo} with items to review" if todo else ""
    print(f"\n{bad} failing checks of {ran}{tail}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
