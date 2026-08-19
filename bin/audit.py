"""Data integrity checks over the whole archive.

Every bug found in this project so far was found by spot-checking one record
and noticing it looked wrong - the bulk consent sentence that marked 180
approved items withdrawn, the roster that credited a commissioner with 14,148
utterances from before she took office, the retrieval query that omitted
`segment_id` so an entire expansion step silently did nothing. Each of those
was invisible in the summary statistics and obvious against an invariant.

Every check also reports HOW MANY ROWS IT EXAMINED, and that is not decoration.
This audit once printed "18/18 ok" when two of its checks were ranging over an
empty set: both filter on `passages.agenda_item_id IS NOT NULL`, and at that
moment not one passage had been bound to an agenda item, so both passed by
examining nothing. A green board meant only that the rebuild had not run yet.
A check with no rows under it proves nothing and now says so."""
import argparse
import os
import sys

import db
import roster
import speaker_claims

CHECKS = []

def check(name, why, review=False):
    """Register an invariant."""
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
        # leaves a row nothing will pick up again. Nothing legitimately holds a claim
        # for six hours: the longest meeting is 8.2h and diarization runs ~14x.
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

# Was `outcome.matches_disposition` until the column was renamed.
@check("outcome.matches_text",
       "outcome is not contradicted by the sentence it came from")
def _(con):
    q = """FROM agenda_items WHERE outcome IS NOT NULL AND outcome_text IS NOT NULL
           AND ((outcome='approved' AND outcome_text ~* 'withdraw|denied')
             OR (outcome='approved' AND outcome_text ~* 'continue(d|s)? (the|this|to)')
             OR (outcome='withdrawn' AND outcome_text !~* 'withdraw'))"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT code, outcome, outcome_source, left(outcome_text,74) {q} LIMIT 6""", \
        """SELECT COUNT(*) FROM agenda_items
           WHERE outcome IS NOT NULL AND outcome_text IS NOT NULL"""

# Was `minutes.no_subsidiary_disposition`; same rename.
@check("minutes.no_subsidiary_outcome",
       "an item's recorded outcome is not a motion about something else")
def _(con):
    """A public hearing's minutes hold several motions and only one disposes
    of the item. The rest accept a member of the public's exhibits into the
    record, or decide to hear the item at all - and the parser used to keep
    whichever came FIRST, so 106 items (88 of them public hearings) read
    "Approved" where what was approved was somebody's paperwork. Two were
    outright denials shown as approvals."""
    import parse_minutes
    rx = parse_minutes.SUBSIDIARY_SQL
        # Scoped to the items parse_minutes actually governs. 964 items hold an outcome
        # from minutes since re-linked to a same-day sibling meeting, which the parser
        # cannot reach; `minutes.orphaned_outcomes` reports those instead.
    readable = """EXISTS (SELECT 1 FROM portal_events pe
                          JOIN portal_files pf ON pf.event_id = pe.id
                         WHERE pe.meeting_id = agenda_items.meeting_id
                           AND pf.kind = 'Minutes' AND pf.chars > 2000)"""
    q = f"""FROM agenda_items
            WHERE outcome_text IS NOT NULL AND {readable} AND outcome_text ~* %s"""
    # The examples query is run without parameters by main(), so the pattern
    # goes in as a literal and its apostrophe has to be doubled. The count
    # binds it properly and must NOT be.
    lit = q.replace("%s", "'" + rx.replace("'", "''") + "'")
    return count(con, f"SELECT COUNT(*) {q}", (rx,)), \
        f"SELECT code, outcome, outcome_source, left(outcome_text,80) {lit} LIMIT 6", \
        f"SELECT COUNT(*) FROM agenda_items WHERE outcome_text IS NOT NULL AND {readable}"

@check("minutes.orphaned_outcomes",
       "an outcome whose minutes are no longer attached to its meeting",
       review=True)
def _(con):
    """These items hold an outcome that `parse_minutes` can no longer see."""
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
    it were."""
    q = """FROM agenda_items a
           JOIN agenda_items b ON b.meeting_id = a.meeting_id
                              AND b.code = a.code AND b.id <> a.id
           WHERE a.outcome_text IS NOT NULL
             AND b.outcome_text IS NOT DISTINCT FROM a.outcome_text
             AND lower(COALESCE(a.section,'')) <> lower(COALESCE(b.section,''))"""
    return count(con, f"SELECT COUNT(DISTINCT a.id) {q}"), f"""
        SELECT a.meeting_id, a.code, a.section, b.section AS other_section,
               left(a.outcome_text, 60) {q} LIMIT 6""", \
        """SELECT COUNT(*) FROM agenda_items a WHERE a.outcome_text IS NOT NULL
             AND EXISTS (SELECT 1 FROM agenda_items b
                          WHERE b.meeting_id = a.meeting_id AND b.code = a.code
                            AND b.id <> a.id)"""

@check("roster.terms_respected",
       "no voice is attributed to a commissioner outside their board term")
def _(con):
    q = """FROM speaker_identity si
           JOIN videos v ON v.id = si.video_id
           JOIN people p ON p.kind = 'board' AND lower(p.surname) = lower(si.name)
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
           JOIN people p ON p.kind = 'board' AND lower(p.surname) = lower(si.name)
           WHERE v.upload_date IS NOT NULL"""

@check("speaker.rollcall_merged",
       "the clerk's roll call and the member's answer are one utterance",
       review=True)
def _(con):
    """Why the clerk wears a commissioner's name, measured."""
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
    """The residual, after the off-by-one fix."""
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

@check("speaker.rail_is_people",
       "every name the public speaker rail offers is a person, or is known to "
       "not be one",
       review=True)
def _(con):
    """What the site PUBLISHES as a list of people.

    A written list goes stale silently, so this is the thing that notices. It
    counts rail values that are neither on the board nor in `people` and are
    not already named in that list. A non-zero count is WORK, not a defect:
    most of what it catches will be a real member of the public the roster has
    never heard of, and the judgement of which is which is a person's."""
    # Inlined rather than passed as a parameter: the runner calls
    # `con.execute(sample)` with no arguments, so the example query and the
    # count query have to be self-contained to be the same query.
    q = f"""FROM (SELECT name, COUNT(*) AS lines FROM utterance_speaker
                   WHERE name IS NOT NULL
                   GROUP BY name HAVING COUNT(*) >= 500
                   ORDER BY lines DESC LIMIT 40) r
            WHERE NOT (r.name = ANY({_rail_exempt()}))
              AND NOT EXISTS (SELECT 1 FROM people p
                               WHERE lower(p.surname) = lower(r.name)
                                  OR lower(p.full_name) = lower(r.name))"""
    return count(con, f"SELECT COUNT(*) {q}"), \
        f"SELECT r.name, r.lines {q} ORDER BY r.lines DESC LIMIT 12", \
        """SELECT COUNT(*) FROM (SELECT 1 FROM utterance_speaker
            WHERE name IS NOT NULL GROUP BY name
            HAVING COUNT(*) >= 500 LIMIT 40) x"""

def _rail_exempt():
    """`archive.NOT_A_PERSON` as a SQL array literal.

    Read from the module the site actually serves, never copied. A second copy
    here would give the audit its own opinion of what the rail suppresses, and
    the two would drift - which is the failure this check exists to catch,
    reproduced inside the check.
    """
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"))
    import archive
    names = ",".join("'" + n.replace("'", "''") + "'"
                     for n in sorted(archive.NOT_A_PERSON))
    return f"ARRAY[{names}]::text[]"

@check("subjects.terms_are_live",
       "a kept subject phrase that finds nothing in either source",
       review=True)
def _(con):
    """A phrase that sounded right and matches nothing."""
        # PROPOSED, not kept. `--triage` never keeps a phrase that found nothing, so
        # scoping this to kept made it an invariant that could not fire. The queue is
        # also the only measure of how much of the proposal was invented: 143 of 488
        # phrases name wording this county does not use, including "trim costs", where
        # the model read Florida's TRIM notice as the English word.
    q = """FROM subject_term t JOIN subject s ON s.slug = t.slug
           WHERE t.status = 'proposed' AND s.status = 'kept'
             AND coalesce(t.n_items, 0) = 0
             AND coalesce(t.n_utterances, 0) = 0"""
    return count(con, f"SELECT COUNT(*) {q}"), \
        f"SELECT t.slug, t.phrase {q} ORDER BY t.slug LIMIT 12", \
        "SELECT COUNT(*) FROM subject_term"

@check("subjects.terms_are_specific",
       "a kept subject phrase broad enough to name most of the archive",
       review=True)
def _(con):
    """The `SHIP` failure, as an invariant."""
    total = count(con, "SELECT COUNT(*) FROM agenda_items WHERE source = 'agenda'")
    cap = max(50, total // 20)
    q = f"""FROM subject_term t JOIN subject s ON s.slug = t.slug
            WHERE t.status = 'kept' AND s.status = 'kept'
              AND NOT t.negative AND coalesce(t.n_items, 0) > {cap}"""
    return count(con, f"SELECT COUNT(*) {q}"), \
        f"""SELECT t.slug, t.phrase, t.n_items, left(t.sample, 60) AS sample {q}
            ORDER BY t.n_items DESC LIMIT 12""", \
        "SELECT COUNT(*) FROM subject_term WHERE status = 'kept' AND NOT negative"

@check("subjects.rollup_is_current",
       "the strip the front page reads was built from the vocabulary it has now")
def _(con):
    """A stale `subject_year` is worse than a slow one."""
    q = """FROM (
             SELECT s.slug FROM subject s
              WHERE s.status = 'kept'
                AND EXISTS (SELECT 1 FROM subject_term t
                             WHERE t.slug = s.slug AND t.status = 'kept'
                               AND NOT t.negative)
             EXCEPT SELECT DISTINCT slug FROM subject_year
             UNION
             SELECT DISTINCT y.slug FROM subject_year y
              WHERE NOT EXISTS (SELECT 1 FROM subject s
                                 WHERE s.slug = y.slug AND s.status = 'kept')
           ) x"""
    return count(con, f"SELECT COUNT(*) {q}"), \
        f"SELECT x.slug {q} ORDER BY 1 LIMIT 12", \
        "SELECT COUNT(DISTINCT slug) FROM subject_year"

@check("subjects.children_fit_their_parent",
       "no sub-subject counts more items than the subject it narrows")
def _(con):
    """An indented row that is bigger than the row above it is a lie."""
    q = """FROM subject c
           JOIN subject p ON p.slug = c.parent
           JOIN (SELECT slug, SUM(items) AS n FROM subject_year GROUP BY slug) cy
             ON cy.slug = c.slug
           JOIN (SELECT slug, SUM(items) AS n FROM subject_year GROUP BY slug) py
             ON py.slug = p.slug
           WHERE c.status = 'kept' AND p.status = 'kept' AND cy.n > py.n"""
    return count(con, f"SELECT COUNT(*) {q}"), \
        f"SELECT c.slug, cy.n AS child, p.slug AS parent, py.n {q} LIMIT 12", \
        "SELECT COUNT(*) FROM subject WHERE parent IS NOT NULL AND status = 'kept'"

@check("subjects.have_a_vocabulary",
       "every kept subject has kept phrases, or descendants that do")
def _(con):
    """A branch with no phrases anywhere beneath it is drawn as an empty row.

    Not a review item: `patterns()` silently drops such a subject, so the
    front page loses a row and nothing says why. That is a curation defect,
    and `--keep`-ing a subject whose phrases were all dropped produces it
    easily."""
    q = """FROM subject s
           WHERE s.status = 'kept'
             AND NOT EXISTS (
                   WITH RECURSIVE under(slug) AS (
                       SELECT s.slug
                       UNION ALL
                       SELECT c.slug FROM subject c
                         JOIN under u ON c.parent = u.slug
                        WHERE c.status = 'kept')
                   SELECT 1 FROM under
                     JOIN subject_term t ON t.slug = under.slug
                    WHERE t.status = 'kept' AND NOT t.negative)"""
    return count(con, f"SELECT COUNT(*) {q}"), \
        f"SELECT s.slug, s.label {q} ORDER BY s.slug LIMIT 12", \
        "SELECT COUNT(*) FROM subject WHERE status = 'kept'"

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
        # BOARD ONLY. full_name stopped being reference data the day display_name()
        # began expanding a board surname into it, so it is now what a reader sees on
        # every chip and citation. Older agendas write "Mr. Calvin Branche" and
        # roster.py kept the whole string. This asserts roster.clean_name and the
        # stored rows have not drifted; the pattern is roster.HONORIFIC_SQL.
        #
        # Members of the public are excluded: they really are introduced as
        # "Pastor Danny Fields", which is what the record says, not a parser defect.
    q = f"""FROM people WHERE kind = 'board'
              AND full_name ~* '{roster.HONORIFIC_SQL}'"""
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
        # A real person's voice consolidates into a few clusters however many meetings
        # they attend: commissioners run 0.06-0.13 distinct clusters per meeting. A
        # name approaching 1.0 has a brand-new voice every time, meaning it was never
        # matched by voice at all, only attached by someone SAYING it nearby. 0.40 sits
        # in the empty gap; every legitimate recurring speaker measured is below 0.25.
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
        # A REVIEW list, not a defect: diarization genuinely splits one person across
        # two labels when they move mic. It is also how a wrong attribution looks, and
        # nothing in the UI could express "not this stretch", which is why it stayed.
        # Ordered by how many utterances the split touches, not by how many voices it
        # splits into, so the head of the list is the row worth fixing first.
    q = """FROM (SELECT si.video_id, si.name,
                        COUNT(DISTINCT si.local_label) voices,
                        (SELECT COUNT(*) FROM utterance_speaker us
                          WHERE us.video_id = si.video_id
                            AND us.name = si.name) utts
                 FROM speaker_identity si
                 JOIN people p ON p.kind = 'board' AND lower(p.surname) = lower(si.name)
                 WHERE si.name IS NOT NULL
                 GROUP BY 1, 2 HAVING COUNT(DISTINCT si.local_label) > 1) t"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT name, video_id, voices, utts {q}
        ORDER BY utts DESC, voices DESC LIMIT 8""", \
        """SELECT COUNT(*) FROM (SELECT si.video_id, si.name
           FROM speaker_identity si
           JOIN people p ON p.kind = 'board' AND lower(p.surname) = lower(si.name)
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
           JOIN people p   ON p.kind = 'board' AND lower(p.surname) = lower(vn.name)
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
           JOIN people p ON p.kind = 'board' AND lower(p.surname)=lower(vn.name)"""

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

# ------------------------------------------------------ corrections
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
        # "Mike Wells" does not join people.surname = 'Wells', so a full-name label
        # bypasses the roster guard and the split-voice check, and search holds two
        # speakers where there is one. Observed on the console's first day: the queue
        # row vanished and no surface could show the mistake again. The console
        # canonicalises on write now; this catches what predates it and what the CLI
        # writes.
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
    # If a range is corrected and the view still shows the
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
    # A public submission is untrusted input; it may mark a name as
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
        # THE REAL PATH, not a shortcut. Resolution is materialised, so an override
        # reaches nobody until something resolves it. Reading utterance_speaker straight
        # after the INSERT tested a guarantee the archive stopped making.
    import speaker_claims
    with con.transaction(force_rollback=True):
        con.execute("""INSERT INTO speaker_override
            (video_id, start_idx, end_idx, action, name, note, author)
            VALUES (%s,%s,%s,'reassign','__audit_probe__','audit roundtrip','audit')""",
            (vid, idx, idx))
        speaker_claims.refresh_video(con, vid, commit=False)
        r = con.execute("""SELECT name, basis, human FROM utterance_speaker
                           WHERE video_id=%s AND idx=%s""", (vid, idx)).fetchone()
        if not r or r[0] != "__audit_probe__" or r[1] != "override" or not r[2]:
            bad.append(f"reassign did not reach the reader: {tuple(r) if r else None}")

        con.execute("""INSERT INTO speaker_override
            (video_id, start_idx, end_idx, action, name, note, author)
            VALUES (%s,%s,%s,'detach',NULL,'audit roundtrip','audit')""",
            (vid, idx, idx))
        speaker_claims.refresh_video(con, vid, commit=False)
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
        # passages.speaker is a denormalised copy feeding search, the speaker filter and
        # every quote the agent prints, and when it drifts the archive contradicts
        # itself where nobody is reading closely enough to notice.
        #
        # EVERY LINE, not merely one of them. Asking whether the baked name appears
        # SOMEWHERE in the passage is too weak: one passage was two speakers' letters
        # filed under the first, and it passed because the first line really is his.
        #
        # Joined on the INDEX range, not the time window. `start` and `end` are doubles
        # that do not always round-trip, so three of the first eight violations were the
        # check's own boundary arithmetic. Integers cannot drift.
    q = """FROM passages p
           WHERE p.speaker <> '(exchange)'
             AND EXISTS (
                 SELECT 1 FROM utterances u
                 JOIN utterance_speaker us
                   ON us.video_id = u.video_id AND us.idx = u.idx
                 WHERE u.video_id = p.video_id
                   AND u.idx BETWEEN p.start_idx AND p.end_idx
                   AND us.name IS DISTINCT FROM p.speaker)"""
    return count(con, f"SELECT COUNT(*) {q}"), \
        f"SELECT p.id, p.video_id, p.speaker, p.start {q} LIMIT 5", \
        "SELECT COUNT(*) FROM passages WHERE speaker <> '(exchange)'"

@check("schema.matches_definition",
       "the live database has the columns bin/schema.sql defines")
def _(con):
    """Nothing has ever compared the two, and they have drifted before.

    `schema.sql` had fallen behind the live database, and replaying
    it silently dropped a guard from `utterance_speaker`, handing disproved
    names back to 8,795 utterances. The same file could not create
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
    """COUNTS occurrences rather than looking for one, and the difference is
    the whole check.

    THAT CATCHES THE ONE THIS CHECK EXISTS FOR, which nothing else can see. An
    address split across an utterance boundary - idx 158 ending "located at
    14720", idx 159 opening "Bluestone Lane in Odessa" - matches no
    per-utterance test, because `position(span in u.text)` is evaluated one
    row at a time and the span is in neither row. The passage renderer joins
    those rows with a space and puts the address back together, whole, in the
    text search reads and the agent quotes. The transcript check passes. This
    one does not.
    """
    # occurrences of `needle` in `hay`, by how much shorter the string gets
    # when they are removed. Postgres has no count-substring; this is exact.
    def n_in(hay, needle):
        return (f"(length({hay}) - length(replace({hay}, {needle}, ''))) "
                f"/ nullif(length({needle}), 0)")
    live = f"""(SELECT COALESCE(SUM({n_in('u.text', 'r.span')}), 0)
                  FROM utterances u
                 WHERE u.video_id = p.video_id
                   AND u.idx BETWEEN p.start_idx AND p.end_idx)"""
        # Three characters cannot be a residence, and spans that short are the section
        # redactor's misfires, which `span_is_plausible` reports as over-redaction.
        # Left in, they fail on the agenda title and leave a privacy check permanently
        # red, which is a check nobody reads.
    q = f"""FROM redaction r JOIN passages p
              ON p.video_id = r.video_id
             AND r.idx BETWEEN p.start_idx AND p.end_idx
            WHERE r.status = 'applied' AND length(r.span) >= 4
              AND (GREATEST({n_in('p.text', 'r.span')},
                            {n_in("coalesce(p.search_text, '')", 'r.span')})
                   > {live})"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT r.id, p.id AS passage, r.video_id, left(r.span, 40) AS span,
               {n_in('p.text', 'r.span')} AS in_passage, {live} AS in_transcript
        {q} LIMIT 5""", \
        "SELECT COUNT(*) FROM redaction WHERE status = 'applied'"

@check("redaction.unfindable",
       "a redacted address cannot be found by searching for it")
def _(con):
        # The end-to-end statement: put the span through the same full-text query search
        # uses and assert the LINE IT WAS CUT FROM no longer comes back. It catches a
        # stale `tsv`, which the two checks above cannot.
        #
        # Scoped to that one line, and it was not: asking whether the phrase occurred
        # anywhere in the recording answered yes 84 times of 3,440. Spans are ordinary
        # English, and a meeting about a place says its name many times.
    q = """FROM redaction r
           WHERE r.status = 'applied' AND EXISTS (
             SELECT 1 FROM utterances u
             WHERE u.video_id = r.video_id AND u.idx = r.idx
               AND u.tsv @@ phraseto_tsquery('english', r.span))"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT r.id, r.video_id, left(r.span, 40) AS span {q} LIMIT 5""", \
        "SELECT COUNT(*) FROM redaction WHERE status = 'applied'"

# A saved answer's citations, one row each, expanded ONCE so the checks below can
# hash-join against `redaction` instead of re-expanding every answer's jsonb. The
# CASE keeps a malformed row from raising, and a check that errors is a check
# that stops being run.
#
# Two shapes, because the checks ask different questions. CITED_VIDEOS is "which
# recordings does this answer quote", and must NOT drop a citation with a
# malformed range, since the PROSE is what is being tested. CITED_RANGES is
# "which utterances does it cover", where an uncomparable range is dropped.
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

@check("redaction.span_is_plausible",
       "an applied redaction that removed an ordinary word, not an address",
       review=True)
def _(con):
    """OVER-redaction, which no other check here looks for."""
    q = """FROM redaction r
           WHERE r.status = 'applied' AND length(btrim(r.span)) < 4"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT r.id, r.video_id, r.idx, r.span, r.author {q}
        ORDER BY r.id LIMIT 5""", \
        "SELECT COUNT(*) FROM redaction WHERE status = 'applied'"

@check("redaction.gone_from_answers",
       "no saved answer still carries an address a redaction removed")
def _(con):
        # The sixth surface, and the only text here that is a COPY. A saved answer reads
        # its quotes back out of `passages`, so the checks above cover them, but its
        # prose cannot be read back from anywhere and sits at a URL somebody may have
        # circulated.
        #
        # LOCATING is load-bearing. The first version flagged a correct answer over the
        # word 'Florida' in "Florida Statute 163.31801(6) caps annual impact-fee
        # increases". A number and a place-name together locate a house; either half
        # alone is a ZIP, a town or a state. A length floor cannot separate them
        # either: '9641 Jerome' is eleven characters and is somebody's house. Keep this
        # identical to redact.LOCATING or the check and the fix describe different
        # things.
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
        # The one case a string search cannot settle, and therefore the one this file
        # refuses to settle alone. `scrub_answers` replaces a span it can find; it
        # cannot find a PARAPHRASE, and nothing can, so these are listed for a person.
    q = f"""FROM redaction r JOIN ({CITED_RANGES}) c
              ON c.video_id = r.video_id AND r.idx BETWEEN c.lo AND c.hi
            WHERE r.status = 'applied'"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT c.answer_id AS answer, left(c.question, 50) AS question,
               r.video_id, r.idx {q} LIMIT 5""", \
        "SELECT COUNT(*) FROM answers"

# The other half of the guarantee. The checks above say the address is gone from
# everything a reader can reach; these say it is still in the record and that
# what is published is derivable from it. That is the whole point of `text_raw`:
# if it were quietly rewritten too, a revert would have nothing to recompute
# from and the archive would have destroyed part of the record to protect
# somebody, which is not the trade anyone agreed to.

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
        # Stated over the lines with NO applied redaction, where the two columns must be
        # identical: those are the rows where a stray write to `text` would hide.
        # text_raw IS NULL is a violation too, meaning an ingest wrote the publication
        # without recording what it was derived from.
    q = """FROM utterances u
           WHERE NOT EXISTS (SELECT 1 FROM redaction r
                              WHERE r.video_id = u.video_id AND r.idx = u.idx
                                AND r.status = 'applied')
             AND (u.text_raw IS NULL OR u.text_raw <> u.text)"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT u.video_id, u.idx, left(u.text, 50) AS published,
               left(COALESCE(u.text_raw, '(null)'), 50) AS raw {q} LIMIT 5""", \
        "SELECT COUNT(*) FROM utterances"

# --------------------------------------------------------------- claims
# What materialising resolution costs: a view could not be stale, and a table
# can. These three guard the ways it goes wrong quietly.

@check("claims.resolution_is_current",
       "the resolved table says what the claims say right now")
def _(con):
    """THE check that materialisation made necessary."""
    cols = "video_id, idx, name_text, person_id, method, contested"
    # pg_temp-qualified on purpose: unqualified, this would find a permanent
    # table of the same name if the temp one did not exist, and drop THAT.
    con.execute("DROP TABLE IF EXISTS pg_temp._resolution_now")
    con.execute("CREATE TEMP TABLE _resolution_now AS " + speaker_claims.RESOLUTION)
    q = f"""FROM ((SELECT {cols} FROM speaker_resolved
                   EXCEPT SELECT {cols} FROM _resolution_now)
                  UNION ALL
                  (SELECT {cols} FROM _resolution_now
                   EXCEPT SELECT {cols} FROM speaker_resolved)) d"""
    return count(con, f"SELECT COUNT(*) {q}"), f"SELECT {cols} {q} LIMIT 5", \
        "SELECT COUNT(*) FROM speaker_resolved"

@check("claims.derived_are_not_duplicated",
       "a producer re-running re-asserts its claims instead of piling them up")
def _(con):
    """Guards claim_derived_identity, and it has already been needed once."""
    q = """FROM (SELECT video_id, start_idx, end_idx, method, name_text,
                        COUNT(*) AS copies
                   FROM speaker_claim WHERE method <> 'override'
                  GROUP BY 1, 2, 3, 4, 5 HAVING COUNT(*) > 1) d"""
    return count(con, f"SELECT COUNT(*) {q}"), f"SELECT * {q} ORDER BY copies DESC LIMIT 5", \
        "SELECT COUNT(*) FROM speaker_claim WHERE method <> 'override'"

@check("claims.people_are_real_people",
       "every person the extractor created has a full name and a claim behind them")
def _(con):
    """Both halves of this fired on the first archive-wide run."""
    q = """FROM people p
           WHERE p.kind = 'public'
             AND (array_length(regexp_split_to_array(btrim(p.full_name),
                                                     '[[:space:]]+'), 1) < 2
                  OR NOT EXISTS (SELECT 1 FROM speaker_claim c
                                  WHERE c.person_id = p.id))"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT p.id, p.full_name,
               CASE WHEN array_length(regexp_split_to_array(btrim(p.full_name),
                                      '[[:space:]]+'), 1) < 2
                    THEN 'one token' ELSE 'no claim' END AS why {q} LIMIT 5""", \
        "SELECT COUNT(*) FROM people WHERE kind = 'public'"

@check("claims.quotes_are_verbatim",
       "a claim's quote is still the words in the range it covers")
def _(con):
    """the design notes, and the reason it is not enough that
    name_speakers.py checks this at write time.

    A quote is the evidence a claim rests on. `self` claims quote the speaker
    naming themselves; `llm` claims quote the span the model was told to copy
    verbatim, and name_speakers.py refuses a proposal whose quote it cannot
    find. Nothing re-checks it afterwards, and A REDACTION CHANGES THE TEXT
    UNDERNEATH: the archive removed '14720 Bluestone Lane' from a line in
    August, and any claim quoting that line no longer quotes anything."""
    q = """FROM speaker_claim c
           WHERE c.quote IS NOT NULL AND btrim(c.quote) <> ''
             AND position(regexp_replace(btrim(c.quote), '[[:space:]]+', ' ', 'g')
                       in (SELECT COALESCE(regexp_replace(
                                     string_agg(u.text, ' ' ORDER BY u.idx),
                                     '[[:space:]]+', ' ', 'g'), '')
                             FROM utterances u
                            WHERE u.video_id = c.video_id
                              AND u.idx BETWEEN c.start_idx AND c.end_idx)) = 0"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT c.id, c.video_id, c.start_idx, c.end_idx, c.method,
               left(c.quote, 50) AS quote {q} ORDER BY c.id LIMIT 5""", \
        "SELECT COUNT(*) FROM speaker_claim WHERE quote IS NOT NULL"

@check("claims.spans_are_real",
       "a claim covers utterances that exist, in one recording")
def _(con):
    """the design notes The span is what makes a claim resolvable -
    specificity breaks ties, so a claim covering a range that is not there
    outranks better evidence over nothing at all."""
    q = """FROM speaker_claim c
           WHERE c.start_idx IS NULL OR c.end_idx IS NULL
              OR c.end_idx < c.start_idx
              OR NOT EXISTS (SELECT 1 FROM utterances u
                              WHERE u.video_id = c.video_id AND u.idx = c.start_idx)
              OR NOT EXISTS (SELECT 1 FROM utterances u
                              WHERE u.video_id = c.video_id AND u.idx = c.end_idx)"""
    return count(con, f"SELECT COUNT(*) {q}"), f"""
        SELECT c.id, c.video_id, c.start_idx, c.end_idx, c.method {q}
        ORDER BY c.id LIMIT 5""", \
        "SELECT COUNT(*) FROM speaker_claim"

@check("claims.evidence_coverage",
       "resolved utterances whose winning claim rests on no quotable evidence",
       review=True)
def _(con):
    """REPORTED, NOT ASSERTED - the design notes says so about the
    contested count, and the same argument covers this. Neither is a defect:
    they are the size of a job.

    `cluster`, `voice` and `chair` have no quote and mostly cannot have one.
    The number worth watching is not how many claims lack evidence but how
    much of what a READER SEES rests on a claim that does - if that share
    falls, the archive is asserting more than it can show, and nothing else
    here would say so."""
    q = """FROM speaker_resolved r
           WHERE r.method IN ('cluster', 'voice', 'chair', 'llm', 'label')"""
    return count(con, f"SELECT COUNT(*) {q}"), """
        SELECT method, COUNT(*) AS utterances,
               ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
          FROM speaker_resolved GROUP BY method ORDER BY utterances DESC""", \
        "SELECT COUNT(*) FROM speaker_resolved"

@check("claims.contested", "spans where two unvetoed methods name different people",
       review=True)
def _(con):
    """A WORKLOAD MEASURE, not a failure - the design notes is explicit."""
    q = "FROM speaker_resolved WHERE contested"
    return count(con, f"SELECT COUNT(*) {q}"), """
        SELECT video_id, COUNT(*) AS utterances FROM speaker_resolved
         WHERE contested GROUP BY video_id ORDER BY 2 DESC LIMIT 5""", \
        "SELECT COUNT(*) FROM speaker_resolved"

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
