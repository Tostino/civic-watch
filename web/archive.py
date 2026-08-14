"""The data layer for the rebuilt UI.

Separate from api.py on purpose. api.py grew around the five hand-written pages
and its shapes are a stopgap (UI_REQUIREMENTS D7); these endpoints are designed
from what a surface actually renders, and the two coexist until the old pages
are retired.

Two things it fixes outright:

* **One key per entity.** `/api/meeting/<id>` used to take a VIDEO id while
  `/api/agenda/<id>` took a MEETING id - two different keys behind near-
  identical names. Everything here is keyed on the meeting, and a recording is
  addressed as a child of it.
* **No display strings.** The old search returned `'Group ' || cluster` as a
  speaker NAME, so a diarization id reached the page as though it were a
  person. Speaker identity leaves here as structured fields and one component
  decides how to render them (R6.2.1), which is also the single place a future
  redaction rule can act (D3).
"""
import re

# The county's public portal. Every meeting and item should be able to point at
# the authoritative upstream (R4.4); three of the three civic archives reviewed
# in PRIOR_ART.md do this and we held the id and linked nowhere.
PORTAL = "https://pascocofl.portal.civicclerk.com/event/{event_id}/overview"

# The county's own PDF, served by the same API bin/civicclerk.py mirrors text
# from - `plainText=false` returns the document itself. For a project whose
# thesis is that the published record is authoritative, the actual document is
# the strongest provenance available (R5.3.5) and it costs a URL.
FILE = ("https://pascocofl.api.civicclerk.com/v1/Meetings/"
        "GetMeetingFileStream(fileId={file_id},plainText=false)")

# A published agenda PDF that extracts to less than this is an image-only scan:
# 404 of 1,161 of them. The meeting has an agenda in the world, and we have no
# text of it, which is a different state from having no agenda at all.
SUBSTANTIVE_CHARS = 2000


def _portal_url(event_id):
    return PORTAL.format(event_id=event_id) if event_id else None


# ------------------------------------------------------------------- index
def meetings(con, body=None, year=None, has_recording=None, when="past",
             limit=200, offset=0, month=None):
    """The archive as a list, newest first.

    Carries each meeting's own coverage state (R3.2, R5.1.3) so a reader can
    tell what they will get before clicking. A site-wide disclaimer would train
    them to ignore it.

    `when` defaults to PAST because the portal publishes its forward calendar:
    meetings are announced months ahead and land here as rows with no agenda,
    no minutes and no recording, because they have not happened. Sorted newest
    first they occupy the whole first screen and every one of them reads as a
    hole in the archive. CivicClerk splits Past / Coming Up and is right to.
    """
    where, args = ["TRUE"], []
    if when == "past":
        where.append("m.date <= to_char(now(), 'YYYY-MM-DD')")
    elif when == "upcoming":
        where.append("m.date > to_char(now(), 'YYYY-MM-DD')")
    if body:
        where.append("m.body = %s")
        args.append(body)
    if year:
        where.append("m.date LIKE %s")
        args.append(f"{year}-%")
    # YYYY-MM, from clicking a cell on the time axis. Narrower than `year` and
    # applied on top of it, so a stale year in the URL cannot widen a month.
    if month:
        where.append("m.date LIKE %s")
        args.append(f"{month}-%")
    if has_recording is not None:
        where.append(("EXISTS" if has_recording else "NOT EXISTS") +
                     " (SELECT 1 FROM videos v WHERE v.meeting_id = m.id"
                     "    AND v.transcribed)")
    clause = " AND ".join(where)

    total = con.execute(
        f"SELECT COUNT(*) FROM meetings m WHERE {clause}", args).fetchone()[0]
    rows = con.execute(f"""
        SELECT m.id, m.date, m.body, m.title,
               (SELECT COUNT(*) FROM agenda_items a
                 WHERE a.meeting_id = m.id AND a.source = 'agenda')   AS items,
               (SELECT COUNT(*) FROM agenda_items a
                 WHERE a.meeting_id = m.id AND a.outcome IS NOT NULL) AS decided,
               (SELECT COUNT(*) FROM videos v
                 WHERE v.meeting_id = m.id AND v.transcribed)         AS videos,
               (SELECT COALESCE(SUM(v.duration), 0) FROM videos v
                 WHERE v.meeting_id = m.id AND v.transcribed)         AS seconds,
               EXISTS (SELECT 1 FROM meeting_roster r
                        WHERE r.meeting_id = m.id)                    AS roster
        FROM meetings m WHERE {clause}
        ORDER BY m.date DESC, m.id
        LIMIT %s OFFSET %s""", args + [limit, offset]).fetchall()
    return {"total": total, "meetings": [dict(r) for r in rows]}


def overview(con, body=None):
    """The collection as an object (R5.1.1), and the shape of it over time.

    Browse opened on a search box, which answers nothing about what is here.
    This is what a reader needs before they can ask anything: how far back it
    goes, how much of it there is, and - the part that matters most - how much
    of it is actually covered. The three coverage fractions are not decoration:
    the archive holds twelve years of the published record and only seven of
    recordings, and a reader who assumes otherwise will conclude the video is
    missing rather than that it never existed.

    `months` is the histogram the time axis is drawn from: one row per calendar
    month that has meetings in it, which is 145 of the 149 in the span.

    Every count except `scheduled` is of meetings that have HAPPENED. The
    county posts its calendar months ahead - 30 meetings are on the books for
    September 2026 through January 2027 - and counting those as archive would
    claim coverage of events nobody has attended yet.
    """
    past = "m.date <= to_char(now(), 'YYYY-MM-DD')"
    where, args = [past], []
    if body:
        where.append("m.body = %s")
        args.append(body)
    clause = " AND ".join(where)
    # The histogram wants the future months too, as a distinct state; only the
    # body filter applies to it.
    span = " AND ".join(where[1:]) or "TRUE"

    rec = """EXISTS (SELECT 1 FROM videos v
                      WHERE v.meeting_id = m.id AND v.transcribed)"""
    ag = """EXISTS (SELECT 1 FROM agenda_items a
                     WHERE a.meeting_id = m.id AND a.source = 'agenda')"""
    # Minutes are held as a portal FILE, not as a column, and an image-only
    # scan is not coverage - see SUBSTANTIVE_CHARS.
    mins = """EXISTS (SELECT 1 FROM portal_events pe
                      JOIN portal_files pf ON pf.event_id = pe.id
                     WHERE pe.meeting_id = m.id AND pf.kind = 'Minutes'
                       AND pf.chars >= %s)"""

    r = con.execute(f"""
        SELECT COUNT(*)                       AS meetings,
               MIN(m.date)                    AS first,
               MAX(m.date)                    AS last,
               COUNT(*) FILTER (WHERE {rec})  AS recorded,
               COUNT(*) FILTER (WHERE {ag})   AS with_agenda,
               COUNT(*) FILTER (WHERE {mins}) AS with_minutes,
               COALESCE(SUM((SELECT SUM(v.duration) FROM videos v
                              WHERE v.meeting_id = m.id AND v.transcribed)), 0)
                                              AS seconds
        FROM meetings m WHERE {clause}""",
        [SUBSTANTIVE_CHARS] + args).fetchone()
    out = dict(r)

    # Item and case totals are archive-wide facts, not per-body ones, and the
    # body filter would make them quietly mean something else.
    if not body:
        out["items"] = con.execute(
            "SELECT COUNT(*) FROM agenda_items WHERE source = 'agenda'").fetchone()[0]
        out["decided"] = con.execute(
            "SELECT COUNT(*) FROM agenda_items WHERE outcome IS NOT NULL").fetchone()[0]
        out["cases"] = con.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        # How many of them actually RECUR. Browse printed the 20,275 under the
        # words "followed across meetings", and 18,898 of those were heard once
        # and never again - so the page advertised a thing the archive does
        # well on a number that mostly does not do it. 1,377 is the number that
        # makes "has this come up before" a join rather than a search, and the
        # continued-cases card below is drawn from that 1,377.
        out["cases_recurring"] = con.execute(
            "SELECT COUNT(*) FROM cases WHERE meetings > 1").fetchone()[0]

    # `meetings` counts what happened; `scheduled` counts what is on the books
    # and has not. A month with 10 scheduled and 0 held is not an empty month,
    # and the axis draws the two differently - the whole point of the axis is
    # that a gap in the calendar must not read as a gap in our coverage.
    out["months"] = [dict(x) for x in con.execute(f"""
        SELECT left(m.date, 7)                              AS month,
               COUNT(*) FILTER (WHERE {past})                AS meetings,
               COUNT(*) FILTER (WHERE {past} AND {rec})      AS recorded,
               COUNT(*) FILTER (WHERE {past} AND {ag})       AS with_agenda,
               COUNT(*) FILTER (WHERE NOT ({past}))          AS scheduled
        FROM meetings m WHERE {span}
        GROUP BY 1 ORDER BY 1""", args[-1:] if body else [])]
    return out


# ------------------------------------------------------------- disagreement
# "Where the board disagreed" (R5.1.4). PRIOR_ART §1 found Councilmatic's
# Divided Votes to be the strongest story surface in any archive reviewed, and
# it is the one entryway that cannot be assembled from counts.
#
# It has to read BOTH sources, because each is blind where the other sees:
#
#   the record       names dissent formally and authoritatively, and is
#                    published weeks late, so the most recent contested
#                    meetings are always missing from it
#   the transcript   catches division the minutes never record at all - a
#                    debate that produced no motion leaves no disposition,
#                    which is exactly how the August 2026 argument over Flock
#                    licence-plate cameras came to be invisible here
#
# They are kept apart in the result and marked, never merged into one claim
# (UI_PLAN §2). The record lane is quotable; the transcript lane is ASR and
# says so.

# Dissent in the minutes is `voting nay` / `voted nay`, and NOTHING else.
# "with Commissioner Weightman absent from the vote" is on 556 items against
# 114 for real dissent, and it is an ABSENCE - the member was not there to
# disagree. Counting it would have made the board look five times more divided
# than it was, on the one view whose entire purpose is to show where it was.
NAY_SQL = r"vot(?:ing|ed) nay"

# Who dissented. The minutes are formulaic enough to name them: "with
# Commissioner Oakley and Commissioner Starkey voting nay".
NAY_NAMES = re.compile(
    r"(?:Chair(?:man|woman)?|Vice[- ]Chair(?:man|woman)?|Commissioner|Mr\.|Ms\.|Mrs\.)\s+"
    r"([A-Z][A-Za-z'\-]+)(?=(?:[,\s]+(?:and\s+)?(?:Chair|Vice|Commissioner|Mr\.|Ms\.|Mrs\.)"
    r"\s+[A-Z][A-Za-z'\-]+)*\s+vot(?:ing|ed)\s+nay)")

# Division in the room, in descending order of how little it asks you to
# believe. Rank 1 is a VOTE - the chair announcing a split tally, or a motion
# that died - which is a fact about the meeting, not a reading of anyone's
# tone. Rank 2 is a member saying plainly that they are against it.
ROOM_TALLY = (r"\y(?:motion|vote|it)\s+(?:pass\w*|carri\w*|fail\w*|di\w*)\s+"
              r"(?:by\s+)?(?:a\s+vote\s+of\s+)?"
              r"(?:two|three|four|five|[2-5])\s*(?:to|-|,)?\s*(?:one|two|1|2)\y")
ROOM_FAIL = r"\ymotion\s+(?:fail\w*|die[sd])\y"
ROOM_OBJECT = (
    r"\yI(?:'m| am)?\s*(?:strongly |certainly |respectfully |totally |completely |really )?"
    r"(?:object\y|opposed?\y|disagree\y|not in favor\y|uncomfortable\y)"
    r"|\yI (?:can(?:'|no)?t|cannot|won'?t|will not) support\y"
    r"|\yI(?:'m| am|'ll| will)?\s*(?:be )?vot\w+ (?:no\y|against\y)")
# "I'm not saying I disagree" is agreement; "I voted no on that one" is a
# member recalling a DIFFERENT meeting. Both read as dissent to the pattern
# above and are neither.
ROOM_NOT = (r"\yI(?:'m| am) not (?:saying|sure)\y|\ynot saying I\y"
            r"|\ynot sure (?:that )?I\y|\yI voted (?:no|against)\y")

# The regexes above cost ~2.3s across 299k utterances. This gate is a GIN
# index probe that cuts the candidate set to ~23k in 30ms, and every stem the
# patterns can match is in it - if you add a branch, add its stem here too, or
# the branch silently never fires. English stopwords cannot be gated: "I'm a
# no" is a clear statement of a vote and is unreachable this way, so it is not
# a branch.
ROOM_GATE = ("fail | dies | died | passes | carries | motion | object | oppose "
             "| opposed | disagree | favor | uncomfortable | support | vote "
             "| votes | voting | voted")

ROOM_SQL = """
WITH said AS MATERIALIZED (
  SELECT u.video_id, u.idx, u.start, u.text
    FROM utterances u
   WHERE u.tsv @@ to_tsquery('english', %(gate)s)
     AND (u.text ~* %(fail)s OR u.text ~* %(tally)s
          OR (u.text ~* %(object)s AND u.text !~* %(negated)s))),
board AS (SELECT DISTINCT p.surname AS s
            FROM board_terms bt JOIN people p ON p.id = bt.person_id)
SELECT DISTINCT ON (sp.agenda_item_id)
       sp.agenda_item_id AS id, said.video_id, said.start AS seconds,
       us.name AS speaker, said.text AS quote,
       CASE WHEN said.text ~* %(fail)s OR said.text ~* %(tally)s
            THEN 'vote' ELSE 'objection' END AS kind
  FROM said
  JOIN utterance_speaker us
    ON us.video_id = said.video_id AND us.idx = said.idx
  JOIN board b ON b.s = us.name
  JOIN item_spans sp
    ON sp.video_id = said.video_id
   AND said.idx BETWEEN sp.start_idx AND sp.end_idx
 ORDER BY sp.agenda_item_id, kind, said.idx
"""

ROOM_ARGS = {"gate": ROOM_GATE, "fail": ROOM_FAIL, "tally": ROOM_TALLY,
             "object": ROOM_OBJECT, "negated": ROOM_NOT}


def _divided_record(con, limit):
    """Dissent as the minutes recorded it. Authoritative, and always behind."""
    # Items heard together share one motion and one disposition verbatim -
    # six consent items in February 2016 carry the same sentence about
    # Commissioner Mariano. Six identical rows is not six disagreements, so
    # the motion is the unit and the rest are counted, not listed.
    rows = [dict(r) for r in con.execute(f"""
        SELECT DISTINCT ON (ai.meeting_id, ai.disposition)
               ai.id, ai.code, ai.title, ai.outcome, ai.disposition,
               ai.case_id, m.id AS meeting_id, m.date, m.body,
               COUNT(*) OVER (PARTITION BY ai.meeting_id, ai.disposition) AS items
          FROM agenda_items ai JOIN meetings m ON m.id = ai.meeting_id
         WHERE ai.disposition ~* '{NAY_SQL}'
         ORDER BY ai.meeting_id, ai.disposition, ai.seq""")]
    for r in rows:
        r["source"] = "record"
        r["dissent"] = NAY_NAMES.findall(r["disposition"] or "")
    rows.sort(key=lambda r: (r["date"], r["id"]), reverse=True)
    return rows[:limit]


def _divided_room(con, limit, seen=()):
    """Division the recording caught. Inferred, and marked so on the page.

    `seen` is what the record lane is already showing. The two sources DO
    corroborate - February 2026's P59 is "Commissioner Oakley voting nay" in
    the minutes and "motion passes three to one" in the room - but with six
    slots a side, spending one on a matter the page has already made is a poor
    trade against the ones only the recording knows about.
    """
    hits = {r["id"]: dict(r) for r in con.execute(ROOM_SQL, ROOM_ARGS)}
    for dup in seen:
        hits.pop(dup, None)
    if not hits:
        return []
    # A motion that fails at 5:03pm lands in whatever item the clock was in,
    # which on 11 August 2026 was "meeting adjourned" - so the page would have
    # announced that the board was divided about going home. Procedure is not
    # a matter; the phases below carry no question anyone can disagree about.
    rows = [dict(r) for r in con.execute("""
        SELECT ai.id, ai.code, ai.title, ai.outcome, ai.case_id,
               m.id AS meeting_id, m.date, m.body
          FROM agenda_items ai JOIN meetings m ON m.id = ai.meeting_id
         WHERE ai.id = ANY(%s)
           AND ai.phase NOT IN ('adjourn', 'call_to_order', 'recess',
                                'proclamation')
         ORDER BY m.date DESC, ai.seq DESC
         LIMIT %s""", (list(hits), limit))]
    for r in rows:
        r.update(hits[r["id"]], source="transcript")
        r["quote"] = _tighten(r["quote"], r["kind"])
    return rows


# A transcript utterance is a ~40-second block of speech and reads as a wall.
# The sentence that matched is the evidence; the rest is context the item page
# already carries, so the quote is cut to the sentence and its neighbour.
def _tighten(text, kind, want=240):
    pat = ROOM_FAIL + "|" + ROOM_TALLY if kind == "vote" else ROOM_OBJECT
    # The patterns are written for Postgres, where the word boundary is \y and
    # \b is a BACKSPACE (gotcha 58). Python is the other way round, so reusing
    # one of these here without translating is a `bad escape \y`, and reusing
    # a \b one silently matches nothing at all - which is the dangerous half.
    m = re.search(pat.replace(r"\y", r"\b"), text, re.I)
    if not m:
        return text[:want]
    start = text.rfind(".", 0, m.start()) + 1
    end = text.find(".", m.end())
    end = len(text) if end < 0 else end + 1
    out = text[start:end].strip()
    if len(out) > want:
        out = out[:want].rstrip() + "…"
    # Utterances are cut on a timer, not on a sentence, so the first one often
    # begins mid-thought. Saying so beats quoting a fragment as if it were the
    # start of what the member said.
    return ("…" + out) if start == 0 and out[:1].islower() else out


def highlights(con, limit=6):
    """R5.1.4 - curated entry points, so arriving does not require a question.

    Three named queries over data already held, which is the test PRIOR_ART
    sets: none of this needs a new pipeline stage.
    """
    record = _divided_record(con, limit)
    divided = {"record": record,
               "room": _divided_room(con, limit, [r["id"] for r in record])}

    # A case the board could not settle. Ordered by how many times it came
    # back, because that IS the story - five continuances over ten months is
    # a different object from a rezoning heard once.
    continued = [dict(r) for r in con.execute("""
        SELECT ai.case_id,
               COUNT(*) FILTER (WHERE ai.outcome = 'continued') AS continuances,
               COUNT(*)        AS appearances,
               MIN(m.date)     AS first,
               MAX(m.date)     AS last,
               COUNT(DISTINCT m.body) AS bodies,
               MAX(ai.title)   AS title
        FROM agenda_items ai JOIN meetings m ON m.id = ai.meeting_id
        WHERE ai.case_id IS NOT NULL
        GROUP BY ai.case_id
        HAVING COUNT(*) FILTER (WHERE ai.outcome = 'continued') >= 3
        ORDER BY continuances DESC, appearances DESC, last DESC
        LIMIT %s""", (limit,))]

    # The MEETING is the unit here, not the item. 113 things were decided on
    # 14 July 2026; a list of items showed eight of them, chosen by sequence
    # number, which is an arbitrary sample presented as a summary - and eight
    # rows repeating one date and one body taught the reader nothing they
    # could not read once. A meeting-day says how much business was done and
    # names the part of it that was not routine, which is the only part a
    # reader can act on. The routine remainder is one click away on the spine.
    decided = [dict(r) for r in con.execute("""
        SELECT m.id AS meeting_id, m.date, m.body,
               COUNT(*) FILTER (WHERE ai.outcome IS NOT NULL)          AS decided,
               COUNT(*) FILTER (WHERE ai.outcome IN ('approved','adopted'))
                                                                       AS passed,
               COUNT(*) FILTER (WHERE ai.outcome IN ('denied','no_action'))
                                                                       AS refused,
               COUNT(*) FILTER (WHERE ai.outcome = 'withdrawn')        AS withdrawn,
               COUNT(*) FILTER (WHERE ai.outcome = 'continued')        AS continued,
               COUNT(*) FILTER (WHERE ai.disposition ~* %s)            AS divided,
               COUNT(*) FILTER (WHERE ai.outcome IS NOT NULL
                                  AND ai.phase IN ('public_hearing','regular'))
                                                                       AS heard,
               (SELECT COALESCE(SUM(v.duration), 0) FROM videos v
                 WHERE v.meeting_id = m.id AND v.transcribed)          AS seconds
          FROM agenda_items ai JOIN meetings m ON m.id = ai.meeting_id
         WHERE m.date <= to_char(now(), 'YYYY-MM-DD')
         GROUP BY m.id, m.date, m.body
        HAVING COUNT(*) FILTER (WHERE ai.outcome IS NOT NULL) > 0
         ORDER BY m.date DESC, decided DESC
         LIMIT %s""", (NAY_SQL, limit))]

    # What was NOT routine, so the row can name it rather than only count it.
    for d in decided:
        d["notable"] = [dict(r) for r in con.execute("""
            SELECT ai.id, ai.code, ai.title, ai.outcome
              FROM agenda_items ai
             WHERE ai.meeting_id = %s
               AND (ai.outcome IN ('denied','no_action')
                    OR ai.disposition ~* %s)
             ORDER BY (ai.outcome IN ('denied','no_action')) DESC, ai.seq
             LIMIT 2""", (d["meeting_id"], NAY_SQL))]

    return {"divided": divided, "continued": continued, "decided": decided}


# --------------------------------------------------------------- the issues
# What the county keeps coming back to (R5.1.4) - the subject matter of twelve
# years, on the one page that had none of it.
#
# Everything else browse shows is either STRUCTURAL - how many meetings, how
# much of each we hold, which months - or RECENT: the last six divided votes,
# the last six meeting-days, the sixty newest meetings. So the page could tell
# a reader that the county met 1,214 times and not one thing it met ABOUT, and
# the twelve-year span in its own header was twelve years of counting meetings.
#
# This list is WRITTEN DOWN, which nothing else in this file is. facets()
# derives its vocabulary so that a phase the parser learns tomorrow appears by
# itself, and that is right for a rail whose job is to enumerate what a column
# contains. It is the wrong tool here, because no column contains this: the
# archive holds 147 titles that happen to say "impact fee", not a field where
# somebody wrote down what the argument was. Deriving the list was tried - the
# phrases that recur in these titles are "General Commercial", "High Density
# Residential" and "Planned Unit Development", which is the zoning code's
# vocabulary rather than the county's.
#
# Three rules keep the curation honest:
#
#   1. Every NUMBER is derived. The list says what to look for; the archive
#      says what it found, and an issue nothing was found for is dropped
#      rather than drawn as a row of zeroes - which is the failure facets()
#      warns about, a reader concluding the county never discussed it.
#   2. TWO patterns each, because there are two sources and they are not
#      alike. A published title is drafted prose with stable wording, matched
#      by regex. Speech is neither, and the only index over 299k utterances is
#      the tsvector, so the room is matched by tsquery - which also keeps this
#      off the 2.3s sequential scan the note above ROOM_GATE describes.
#   3. bin/threads.py holds a TOPICS list that looks like this one and is
#      deliberately not shared with it. Those keys are written into
#      passage_keys at INDEX time, so a topic added there does nothing until
#      bin/index_passages.py runs again. These are matched at read time and
#      take effect at once. One list serving both would be half stale, with
#      nothing on either surface to show which half.
ISSUES = [
    {"slug": "rezoning",
     "label": "Rezoning and planned developments",
     "q": "rezoning MPUD",
     "record": r"rezon|\mMPUD\M|\mPUD\M|planned unit development",
     "room": "rezoning | rezone | mpud | pud"},
    {"slug": "roads-52-54",
     "label": "State Road 52 and State Road 54",
     "q": "State Road 54",
     "record": r"\m(S\.?R\.?|State Road)[ -]?5[24]\M",
     "room": "(state <-> road | sr) <-> (52 | 54)"},
    {"slug": "comprehensive-plan",
     "label": "The comprehensive plan",
     "q": "comprehensive plan",
     "record": r"comprehensive plan|comp plan",
     "room": "(comprehensive | comp) <-> plan"},
    {"slug": "stormwater",
     "label": "Stormwater and flooding",
     "q": "stormwater",
     "record": r"stormwater|flooding|flood control",
     "room": "stormwater | flooding"},
    {"slug": "utilities",
     "label": "Water and sewer utilities",
     "q": "wastewater",
     "record": r"wastewater|water and sewer|utility rate|reclaimed water",
     "room": "wastewater | (water <-> and <-> sewer) | (reclaimed <-> water)"},
    {"slug": "ridge-road",
     "label": "The Ridge Road extension",
     "q": "Ridge Road",
     "record": r"ridge road",
     "room": "ridge <-> road"},
    {"slug": "impact-fees",
     "label": "Impact fees and mobility fees",
     "q": "impact fees",
     "record": r"impact fee|mobility fee",
     "room": "(impact | mobility) <-> fee"},
    {"slug": "incentives",
     "label": "Economic development incentives",
     "q": "economic development incentive",
     "record": r"economic development|incentive agreement|job creation",
     "room": "(economic <-> development) | (incentive <-> agreement)"
             " | (job <-> creation)"},
    {"slug": "budget-millage",
     "label": "The budget and the millage rate",
     "q": "millage rate",
     "record": r"millage|ad valorem|tentative budget",
     "room": "millage | valorem | (tentative <-> budget)"},
    {"slug": "penny-for-pasco",
     "label": "Penny for Pasco",
     "q": "Penny for Pasco",
     "record": r"penny for pasco|surtax",
     "room": "surtax | (penny <-> for <-> pasco)"},
    {"slug": "opioid",
     "label": "Opioid settlement money",
     "q": "opioid",
     "record": r"opioid",
     "room": "opioid"},
    {"slug": "connected-city",
     "label": "Connected City",
     "q": "Connected City",
     "record": r"connected city",
     "room": "connected <-> city"},
    {"slug": "homelessness",
     "label": "Homelessness",
     "q": "homelessness",
     "record": r"homeless",
     "room": "homeless"},
    {"slug": "housing",
     "label": "Affordable housing",
     "q": "affordable housing",
     "record": r"affordable housing|workforce housing",
     "room": "(affordable | workforce) <-> housing"},
    {"slug": "orange-belt-trail",
     "label": "The Orange Belt Trail",
     "q": "Orange Belt Trail",
     "record": r"orange belt",
     "room": "orange <-> belt"},
    {"slug": "moffitt",
     "label": "Moffitt Cancer Center",
     "q": "Moffitt",
     "record": r"moffitt",
     "room": "moffitt"},
    # Both halves are qualified, and both had to be. Bare "license plate"
    # takes in the county's spay/neuter specialty plate grant, and `flock`
    # stems to the same token as "flocking" - investors flocking into
    # treasuries, people flocking here from out of state - which put 35 lines
    # and a 2020 start date on an issue that reached this county in 2026.
    {"slug": "alpr",
     "label": "License plate cameras",
     "q": "license plate cameras",
     "record": r"license plate (reader|camera|recognition|detection)"
               r"|plate reader|\mALPR\M|flock (camera|safety|group)",
     "room": "((license <-> plate) & (camera | reader | detection"
             " | recognition)) | alpr | (plate <-> reader) | (flock & camera)"},
    {"slug": "school-zone-cameras",
     "label": "School zone speed cameras",
     "q": "school zone speed cameras",
     "record": r"school zone.{0,30}(camera|speed)",
     "room": "(school <-> zone) & (camera | speed)"},
]


def issues(con):
    """Each issue's twelve years, in both sources and never merged.

    Per YEAR rather than as one total, because the totals are the least
    interesting thing about these: opioid money is a 2021 arrival, Moffitt a
    2020 one, and the county has argued about impact fees since the first
    meeting this archive holds. A count cannot say that and a strip of twelve
    cells can.

    The record lane counts published agenda items. The room lane counts lines
    of speech - a line is a ~40-second block, so it counts blocks that mention
    the issue, not sentences about it. Neither is a measure of importance and
    the page does not present them as one.
    """
    vals = ", ".join(["(%s, %s)"] * len(ISSUES))

    # Two counts of meetings, one per source, and both are summed per year
    # later. That is exact rather than approximate: a meeting has one date, so
    # it falls in exactly one year and cannot be counted in two.
    record = [dict(r) for r in con.execute(f"""
        WITH t(slug, rx) AS (VALUES {vals})
        SELECT t.slug, left(m.date, 4) AS year,
               COUNT(*)                                          AS items,
               COUNT(DISTINCT m.id)                              AS meetings,
               COUNT(*) FILTER (WHERE ai.outcome = 'continued')  AS continued,
               COUNT(*) FILTER (WHERE ai.outcome
                                      IN ('denied','no_action')) AS refused,
               COUNT(*) FILTER (WHERE ai.disposition
                                      ~* '{NAY_SQL}')            AS divided,
               MIN(m.date) AS first, MAX(m.date) AS last
          FROM t
          JOIN agenda_items ai ON ai.source = 'agenda' AND ai.title ~* t.rx
          JOIN meetings m ON m.id = ai.meeting_id
         WHERE m.date <= to_char(now(), 'YYYY-MM-DD')
         GROUP BY 1, 2""",
        [v for i in ISSUES for v in (i["slug"], i["record"])])]

    room = [dict(r) for r in con.execute(f"""
        WITH t(slug, tsq) AS (VALUES {vals})
        SELECT t.slug, left(m.date, 4) AS year,
               COUNT(*)             AS lines,
               COUNT(DISTINCT m.id) AS meetings,
               MIN(m.date) AS first, MAX(m.date) AS last
          FROM t
          JOIN utterances u ON u.tsv @@ to_tsquery('english', t.tsq)
          JOIN videos v ON v.id = u.video_id
          JOIN meetings m ON m.id = v.meeting_id
         GROUP BY 1, 2""",
        [v for i in ISSUES for v in (i["slug"], i["room"])])]

    # The axis is the archive's own span, so a thirteenth year needs no edit
    # here - the same reason TimeAxis derives its heading rather than printing
    # "twelve".
    span = [r[0] for r in con.execute("""
        SELECT left(m.date, 4) FROM meetings m
         WHERE m.date <= to_char(now(), 'YYYY-MM-DD')
         GROUP BY 1 ORDER BY 1""")]
    # Before this year the room lane is not quiet, it does not exist. The page
    # has to draw that difference or every issue reads as new (R3.2).
    heard_from = con.execute("""
        SELECT MIN(left(m.date, 4)) FROM meetings m
         WHERE EXISTS (SELECT 1 FROM videos v
                        WHERE v.meeting_id = m.id AND v.transcribed)""",
        ).fetchone()[0]

    out = []
    for spec in ISSUES:
        rec = {r["year"]: r for r in record if r["slug"] == spec["slug"]}
        rm = {r["year"]: r for r in room if r["slug"] == spec["slug"]}
        # Rule 1. A wording the county never used is not an issue it never
        # had; either way an empty row states something we did not find out.
        if not rec and not rm:
            continue
        dates = ([r["first"] for r in rec.values()] + [r["last"] for r in rec.values()]
                 + [r["first"] for r in rm.values()] + [r["last"] for r in rm.values()])
        out.append({
            "slug": spec["slug"], "label": spec["label"], "q": spec["q"],
            "items": sum(r["items"] for r in rec.values()),
            "meetings": sum(r["meetings"] for r in rec.values()),
            "continued": sum(r["continued"] for r in rec.values()),
            "refused": sum(r["refused"] for r in rec.values()),
            "divided": sum(r["divided"] for r in rec.values()),
            "lines": sum(r["lines"] for r in rm.values()),
            "heard": sum(r["meetings"] for r in rm.values()),
            "first": min(dates), "last": max(dates),
            "years": [{"year": y,
                       "items": rec[y]["items"] if y in rec else 0,
                       "meetings": rec[y]["meetings"] if y in rec else 0,
                       "lines": rm[y]["lines"] if y in rm else 0,
                       "heard": rm[y]["meetings"] if y in rm else 0}
                      for y in span],
        })
    # By how much county business each one is, in the source that covers all
    # twelve years. Ranking on the two sources added together would rank on
    # coverage as much as on subject: the room reaches 2018 onwards and 23% of
    # meetings, so anything argued before a camera existed would sink.
    out.sort(key=lambda i: (-i["meetings"], -i["heard"], i["label"]))
    return {"span": span, "heard_from": heard_from, "issues": out}


def facets(con):
    """The values a search rail may offer (R5.6.2).

    Derived, not written down. A hardcoded list of phases drifts the moment
    the parser learns a new one, and a filter that returns nothing because the
    vocabulary moved is worse than no filter - the reader reads it as "the
    archive holds none of those".

    Speakers are the ones with enough speech to be worth filtering by, which
    is a judgement, so the count comes with them and the page can show it.
    """
    return {
        "bodies": [dict(r) for r in con.execute("""
            SELECT m.body, COUNT(*) AS items
              FROM agenda_items ai JOIN meetings m ON m.id = ai.meeting_id
             WHERE ai.source = 'agenda'
             GROUP BY m.body ORDER BY items DESC""")],
        "phases": [dict(r) for r in con.execute("""
            SELECT phase, COUNT(*) AS items FROM agenda_items
             WHERE source = 'agenda' AND phase IS NOT NULL
             GROUP BY phase ORDER BY items DESC""")],
        "outcomes": [dict(r) for r in con.execute("""
            SELECT outcome, COUNT(*) AS items FROM agenda_items
             WHERE outcome IS NOT NULL
             GROUP BY outcome ORDER BY items DESC""")],
        "speakers": [dict(r) for r in con.execute("""
            SELECT name AS speaker, COUNT(*) AS lines
              FROM utterance_speaker WHERE name IS NOT NULL
             GROUP BY name HAVING COUNT(*) >= 500
             ORDER BY lines DESC LIMIT 40""")],
        "years": [dict(r) for r in con.execute("""
            SELECT left(m.date, 4) AS year, COUNT(*) AS meetings
              FROM meetings m
             WHERE m.date <= to_char(now(), 'YYYY-MM-DD')
             GROUP BY 1 ORDER BY 1 DESC""")],
    }


def bodies(con):
    """Which bodies exist, and how much of each we hold."""
    return [dict(r) for r in con.execute("""
        SELECT m.body,
               COUNT(*) FILTER (
                   WHERE m.date <= to_char(now(), 'YYYY-MM-DD'))  AS meetings,
               MIN(m.date)                                        AS first,
               MAX(m.date) FILTER (
                   WHERE m.date <= to_char(now(), 'YYYY-MM-DD'))  AS last,
               COUNT(*) FILTER (WHERE EXISTS (
                   SELECT 1 FROM videos v
                    WHERE v.meeting_id = m.id AND v.transcribed))  AS recorded,
               COUNT(*) FILTER (WHERE EXISTS (
                   SELECT 1 FROM agenda_items a
                    WHERE a.meeting_id = m.id AND a.source = 'agenda')) AS with_agenda
        FROM meetings m GROUP BY m.body
        HAVING COUNT(*) FILTER (WHERE m.date <= to_char(now(), 'YYYY-MM-DD')) > 0
        ORDER BY recorded DESC, meetings DESC""")]


# ----------------------------------------------------------------- meeting
def meeting(con, meeting_id):
    """Everything /meeting/:id renders except the transcript itself.

    The transcript is a separate call because it is per-recording and an order
    of magnitude larger; the spine has to paint first (R8.1).
    """
    m = con.execute("SELECT id, date, body, title FROM meetings WHERE id = %s",
                    (meeting_id,)).fetchone()
    if not m:
        return None
    out = {"meeting": dict(m)}

    out["videos"] = [dict(r) for r in con.execute("""
        SELECT id, title, duration, session_seq, upload_date, kind, words
        FROM videos WHERE meeting_id = %s AND transcribed
        ORDER BY session_seq NULLS FIRST, upload_date""", (meeting_id,))]

    # The published roster, not an inference (R5.2.2). Offices rotate annually,
    # so chair/vice-chair are per-meeting facts and come from this table rather
    # than from anything about the person.
    out["roster"] = [dict(r) for r in con.execute("""
        SELECT p.id AS person_id, p.surname, p.full_name, r.district, r.office
        FROM meeting_roster r JOIN people p ON p.id = r.person_id
        WHERE r.meeting_id = %s
        ORDER BY r.district NULLS LAST, p.surname""", (meeting_id,))]

    # The spine (R5.2.1). Items in PUBLISHED order, each with where it occurs
    # in the recordings. An item interrupted by the lunch break is one row with
    # two spans, which is why spans are aggregated rather than joined flat.
    out["items"] = [dict(r) for r in con.execute("""
        SELECT ai.id, ai.seq, ai.code, ai.section, ai.phase, ai.title,
               ai.case_id, ai.department, ai.recommendation, ai.disposition,
               ai.outcome, ai.source, ai.districts, ai.file_number,
               COALESCE((
                   SELECT json_agg(json_build_object(
                              'video_id', sp.video_id, 'part', sp.part,
                              'start', sp.start, 'end', sp."end",
                              'start_idx', sp.start_idx, 'end_idx', sp.end_idx)
                          ORDER BY sp.part, sp.start)
                     FROM item_spans sp
                    WHERE sp.agenda_item_id = ai.id), '[]'::json) AS spans
        FROM agenda_items ai WHERE ai.meeting_id = %s
        ORDER BY ai.seq""", (meeting_id,))]

    # What the county published, and whether we can actually read it. An
    # image-only agenda is a coverage gap, not a parse failure, and the reader
    # is owed the distinction.
    out["files"] = [{**dict(r), "url": FILE.format(file_id=r["file_id"])}
                    for r in con.execute("""
        SELECT f.file_id, f.kind, f.name, f.published_at, f.chars,
               (f.chars >= %s) AS extracted, f.event_id
        FROM portal_files f
        JOIN portal_events pe ON pe.id = f.event_id
        WHERE pe.meeting_id = %s
        ORDER BY f.kind, f.published_at""", (SUBSTANTIVE_CHARS, meeting_id))]

    ev = con.execute(
        "SELECT id, name, body, event_date FROM portal_events "
        "WHERE meeting_id = %s ORDER BY id LIMIT 1", (meeting_id,)).fetchone()
    out["portal"] = ({**dict(ev), "url": _portal_url(ev["id"])} if ev else None)

    items = out["items"]
    published = [i for i in items if i["source"] == "agenda"]
    out["coverage"] = {
        "items": len(published),
        "derived_items": len(items) - len(published),
        "decided": sum(1 for i in items if i["outcome"]),
        "bound": sum(1 for i in items if i["spans"]),
        "videos": len(out["videos"]),
        "seconds": sum(v["duration"] or 0 for v in out["videos"]),
        "roster": len(out["roster"]),
        "agenda_file": any(f["kind"] == "Agenda" and f["extracted"]
                           for f in out["files"]),
        "minutes_file": any(f["kind"] == "Minutes" and f["extracted"]
                            for f in out["files"]),
    }

    # Neighbours on the time axis, same body - so a meeting is never a dead end
    # (R4.3) and stepping through a series does not require going back to an
    # index.
    nb = con.execute("""
        SELECT
          (SELECT json_build_object('id', x.id, 'date', x.date)
             FROM meetings x WHERE x.body = m.body AND x.date < m.date
             ORDER BY x.date DESC LIMIT 1) AS prev,
          (SELECT json_build_object('id', x.id, 'date', x.date)
             FROM meetings x WHERE x.body = m.body AND x.date > m.date
             ORDER BY x.date LIMIT 1) AS next
        FROM meetings m WHERE m.id = %s""", (meeting_id,)).fetchone()
    out["prev"], out["next"] = nb["prev"], nb["next"]
    return out


# -------------------------------------------------------------- transcript
#
# Speaker identity leaves here as FIELDS, never as a rendered string.
#
#   name        the resolved name, or null. Never a diarization label.
#   confidence  how the voice matched. Null when human-labelled.
#   human       a person stated this. Outranks everything derived (R5.8.7).
#   voice       the cluster id - stable enough to group by within a page, and
#               explicitly NOT a name (only ~2% survive a re-clustering run).
#
# The old endpoint collapsed all of that into `COALESCE(name, 'Group '||cluster)`
# so the page could not tell an inference from a fact, and printed a diarization
# id where a name goes.
LINES = """
    SELECT u.video_id, u.idx, u.start, u."end", u.text,
           u.cluster AS voice, u.local_label,
           us.name, us.confidence, us.human,
           -- How the name was arrived at, so the page can say so rather than
           -- presenting four very different kinds of claim identically
           -- (R2.3). 'cluster' is the weakest: it is the archive-wide
           -- majority for this voice, not evidence about this meeting.
           us.basis, us.contested,
           sp.agenda_item_id
    FROM utterances u
    JOIN utterance_speaker us
      ON us.video_id = u.video_id AND us.idx = u.idx
    LEFT JOIN item_spans sp
           ON sp.video_id = u.video_id
          AND u.idx BETWEEN sp.start_idx AND sp.end_idx
    WHERE u.video_id = %s AND u.idx BETWEEN %s AND %s
    ORDER BY u.idx"""


def _offices(con, meeting_id):
    """surname -> the office they held AT THIS MEETING (R5.2.5).

    "Girardi, Vice Chairman", not a bare surname - offices rotate annually, so
    this is a per-meeting fact. Sent once as a lookup rather than repeated on
    every one of up to 2,252 lines.
    """
    return {r[0]: {"office": r[1], "district": r[2], "full_name": r[3]}
            for r in con.execute("""
        SELECT p.surname, r.office, r.district, p.full_name
        FROM meeting_roster r JOIN people p ON p.id = r.person_id
        WHERE r.meeting_id = %s""", (meeting_id,))}


def transcript(con, video_id):
    v = con.execute(
        "SELECT id, title, duration, session_seq, meeting_id, upload_date "
        "FROM videos WHERE id = %s", (video_id,)).fetchone()
    if not v:
        return None
    lines = [dict(r) for r in con.execute(LINES, (video_id, 0, 2 ** 31 - 1))]
    return {"video": dict(v), "lines": lines,
            "offices": _offices(con, v["meeting_id"])}


# --------------------------------------------------------------- agenda item
#
# The longest item in the archive runs to 1,225 utterances; the median is 18
# and the 90th percentile 131. So an item's speech is sent whole rather than
# paged - R7.5 says do not paginate what can simply be rendered - with a cap
# that exists only so a pathological span cannot take the page down, and which
# says so when it bites rather than silently truncating.
MAX_ITEM_LINES = 2000

# Everything ever said about one case, across every meeting that took it up.
# Median 32 lines, p90 221, p99 783, largest in the archive 2,349 (PDD-23-0009,
# four meetings). Same reasoning as MAX_ITEM_LINES, one order of magnitude up.
MAX_CASE_LINES = 4000

# --------------------------------------------------------------- appearances
#
# THE definition of "a time the board took this item up", used by /meeting,
# /item and /case so all three break the record in the same places.
#
# A single discussion often arrives as several rows of item_spans, because the
# binder cuts on speaker turns. Merging the near-adjacent ones is not a
# smoothing choice - across the archive the gaps between consecutive spans of
# one item fall into two clumps with nothing in between:
#
#     0s x6, 2s x2, 4s, 5s   |   64s, 65s, 67s, 74s, 86s, ... 207m
#
# Below the trough is one discussion cut in two. Above it the board genuinely
# set the item down and came back - 93 items in 68 meetings do this, and the
# widest gap is three and a half hours. Any threshold inside the trough gives
# the same answer; this one has 55 seconds of slack either side.
#
# Getting it wrong in one direction hides that an item was returned to at all.
# In the other it shows a break the reader can see and dismiss. So if the
# trough ever fills in, move this DOWN.
ONE_APPEARANCE = 60


def _runs(spans):
    """Spans -> the distinct times the item was taken up, in order.

    `spans` are dicts carrying at least video_id, session_seq, start, end,
    start_idx and end_idx. Each run keeps the full index range it covers, so
    the caller can pull every line of it in one query.
    """
    out = []
    ordered = sorted(spans, key=lambda s: ((s.get("session_seq") is not None,
                                            s.get("session_seq") or 0),
                                           s["start"]))
    for sp in ordered:
        last = out[-1] if out else None
        if (last and last["video_id"] == sp["video_id"]
                and sp["start"] - last["end"] <= ONE_APPEARANCE):
            last["end"] = max(last["end"], sp["end"])
            last["end_idx"] = max(last["end_idx"], sp["end_idx"])
            last["parts"] += 1
            continue
        out.append({"video_id": sp["video_id"],
                    "session_seq": sp.get("session_seq"),
                    "start": sp["start"], "end": sp["end"],
                    "start_idx": sp["start_idx"], "end_idx": sp["end_idx"],
                    "duration": sp.get("duration"), "parts": 1})
    for i, r in enumerate(out):
        r["nth"], r["of"] = i + 1, len(out)
    return out

# A NOTE ON WIDE SPANS, so nobody re-derives the wrong conclusion I did.
#
# 110 of 5,587 spans cover more than half of their recording, against a median
# span of 3 minutes, and this looked like the binder failing to find an item's
# end and running to the tape. It is not. Every one of the following says so:
#
#   - No wide segment is the last in its video, and none is the only one. Each
#     ends within a second or two of where the next begins, so the boundaries
#     are contiguous and deliberate rather than defaulted.
#   - The affected meetings have a MEDIAN OF 8 published items. Most are
#     Planning Commission hearings or single-purpose emergency sessions, where
#     one item genuinely IS the meeting. The widest of all - 96% of its
#     recording - is a one-hour emergency meeting declaring a local state of
#     emergency for Tropical Storm Helene.
#   - Read at five points spread across it, the 4h55m APC3 span on 2025-02-06
#     is the same rezoning throughout, right down to "either way you'll be on
#     your way to the Board of County Commissioners" near the end. The meeting
#     published three items.
#   - An item ends with its vote, so a span that swallowed the next item would
#     have a vote in its middle. Matching ROOM_TALLY and ROOM_FAIL inside every
#     span: the last vote sits at 97% of the way through a WIDE span and 96%
#     through a normal one, with a median of two minutes of talk after it in
#     both. Wide spans are, if anything, slightly better bounded.
#
# The four spans archive-wide with a vote more than 15 minutes before their end
# are all multi-part discussions where that is correct - a budget hearing votes
# on tentative millage, final millage and adoption separately.
#
# So a long span is a long hearing. `share` and `loose` fields used to be
# computed here and rendered as a warning on /case and /item; they told readers
# that a genuine five-hour rezoning was probably mis-bounded. Removed.


def item(con, item_id):
    """One agenda item (§5.3): the published record, then what was said.

    Ordered the way the requirements are: the county's record leads, because
    it is authoritative and because it is the only thing 91% of decided items
    have. The transcript follows as a second, weaker source, and the two are
    never merged into one narrative.
    """
    r = con.execute("""
        SELECT ai.id, ai.meeting_id, ai.seq, ai.code, ai.section, ai.phase,
               ai.title, ai.case_id, ai.department, ai.recommendation,
               ai.disposition, ai.outcome, ai.outcome_source, ai.source,
               ai.districts, ai.file_number,
               m.date, m.body, m.title AS meeting_title
        FROM agenda_items ai JOIN meetings m ON m.id = ai.meeting_id
        WHERE ai.id = %s""", (item_id,)).fetchone()
    if not r:
        return None
    row = dict(r)
    meeting = {"id": row.pop("meeting_id"), "date": row["date"],
               "body": row["body"], "title": row.pop("meeting_title")}

    # Where the item physically occurs. `part` > 0 is the resumption after a
    # session break, so an item interrupted by lunch is one item with two
    # spans rather than two items.
    spans = [dict(x) for x in con.execute("""
        SELECT sp.video_id, sp.part, sp.start, sp."end", sp.start_idx,
               sp.end_idx, v.session_seq, v.duration
        FROM item_spans sp JOIN videos v ON v.id = sp.video_id
        WHERE sp.agenda_item_id = %s
        ORDER BY v.session_seq NULLS FIRST, sp.part, sp.start""", (item_id,))]
    row["spans"] = [{k: v for k, v in sp.items()
                     if k not in ("session_seq", "duration")} for sp in spans]

    # What was said, in the same field-not-string form the transcript uses, so
    # SpeakerChip stays the only thing that decides how a speaker is displayed
    # (R6.2.1, D3).
    #
    # Grouped by APPEARANCE, not poured into one list (R5.2.7). This item's
    # speech used to be concatenated across spans, so an item argued at 18:05,
    # set down, and taken up again at 3:38:04 read as one continuous exchange
    # with three and a half hours of unrelated business silently removed from
    # the middle of it. The page has to be able to draw that gap.
    row["runs"] = _runs(spans)
    budget = MAX_ITEM_LINES
    for r in row["runs"]:
        got = [dict(x) for x in con.execute(
            LINES, (r["video_id"], r["start_idx"], r["end_idx"]))][:max(budget, 0)]
        budget -= len(got)
        r["lines"] = got
    # Kept flat as well: /item's older callers read `lines`, and a consumer
    # that does not care about the breaks should not have to flatten.
    row["lines"] = [ln for r in row["runs"] for ln in r["lines"]]
    row["truncated"] = sum(s["end_idx"] - s["start_idx"] + 1
                           for s in spans) > len(row["lines"])

    # The videos this item touches, so the page can name a session ("Afternoon
    # session") rather than printing a YouTube id at the reader.
    row["videos"] = [dict(x) for x in con.execute("""
        SELECT id, title, duration, session_seq, upload_date, kind, words
        FROM videos WHERE meeting_id = %s AND transcribed
        ORDER BY session_seq NULLS FIRST, upload_date""", (meeting["id"],))]

    # R5.3.3: the case thread, inline. An item is rarely the whole story, and
    # Councilmatic puts the history ON the item for exactly this reason. The
    # dedicated view is one click away; this is the shape of the sequence.
    row["thread"] = [dict(x) for x in con.execute("""
        SELECT ai.id, ai.code, ai.title, ai.phase, ai.outcome, ai.disposition,
               m.id AS meeting_id, m.date, m.body,
               EXISTS (SELECT 1 FROM item_spans sp
                        WHERE sp.agenda_item_id = ai.id) AS recorded
        FROM agenda_items ai JOIN meetings m ON m.id = ai.meeting_id
        WHERE ai.case_id = %s
        ORDER BY m.date, ai.seq""",
        (row["case_id"],))] if row["case_id"] else []

    # R5.3.5 / R4.4: the county's own document and the county's own page. The
    # PDF is proxied rather than linked directly - CivicClerk serves it with
    # `Content-Disposition: attachment`, which makes a cross-origin frame
    # download the file instead of rendering it (see server._file).
    row["files"] = [{**dict(x), "url": FILE.format(file_id=x["file_id"]),
                     "inline": f"/api/file/{x['file_id']}"}
                    for x in con.execute("""
        SELECT f.file_id, f.kind, f.name, f.published_at, f.chars,
               (f.chars >= %s) AS extracted, f.event_id
        FROM portal_files f
        JOIN portal_events pe ON pe.id = f.event_id
        WHERE pe.meeting_id = %s
        ORDER BY f.kind, f.published_at""", (SUBSTANTIVE_CHARS, meeting["id"]))]

    ev = con.execute(
        "SELECT id FROM portal_events WHERE meeting_id = %s ORDER BY id LIMIT 1",
        (meeting["id"],)).fetchone()
    row["portal"] = _portal_url(ev["id"]) if ev else None

    # Neighbours in published order, so an item is never a dead end (R4.3) and
    # reading an agenda through does not mean going back to the meeting.
    nb = con.execute("""
        SELECT
          (SELECT json_build_object('id', x.id, 'code', x.code, 'title', x.title)
             FROM agenda_items x
            WHERE x.meeting_id = %(m)s AND x.seq < %(s)s
            ORDER BY x.seq DESC LIMIT 1) AS prev,
          (SELECT json_build_object('id', x.id, 'code', x.code, 'title', x.title)
             FROM agenda_items x
            WHERE x.meeting_id = %(m)s AND x.seq > %(s)s
            ORDER BY x.seq LIMIT 1) AS next""",
        {"m": meeting["id"], "s": row["seq"]}).fetchone()

    return {"item": row, "meeting": meeting,
            "offices": _offices(con, meeting["id"]),
            "prev": nb["prev"], "next": nb["next"]}


# --------------------------------------------------------------------- case
def case(con, case_id):
    """One application, every meeting that took it up, in order (§5.4).

    The sleeper feature. A rezoning is heard by the Planning Commission,
    transmitted by the Board and adopted months later; 1,377 cases span more
    than one meeting and the longest in the archive ran to twelve appearances
    across ten months and five continuances. Twelve unrelated events on the
    county portal are one story here, and no flat search can show it.
    """
    steps = [dict(r) for r in con.execute("""
        SELECT ai.id, ai.seq, ai.code, ai.section, ai.phase, ai.title,
               ai.department, ai.recommendation, ai.disposition, ai.outcome,
               ai.source, ai.districts, ai.file_number,
               m.id AS meeting_id, m.date, m.body,
               (SELECT json_build_object('video_id', sp.video_id,
                                         'start', sp.start, 'end', sp."end")
                  FROM item_spans sp
                 WHERE sp.agenda_item_id = ai.id
                 ORDER BY sp.part, sp.start LIMIT 1) AS span
        FROM agenda_items ai JOIN meetings m ON m.id = ai.meeting_id
        WHERE ai.case_id = %s
        ORDER BY m.date, ai.seq""", (case_id,))]
    if not steps:
        return None

    # EVERYTHING SAID ABOUT THIS CASE, over every meeting that took it up
    # (R5.4.4). The thread above says the case was heard six times; this is the
    # six hearings themselves, end to end, which is the only place in the site
    # a reader can follow one argument across a year.
    #
    # Not a flat transcript. A hearing is bounded by the meeting it happened
    # in, by which session of that meeting, and by the fact that a board can
    # take an item up twice in one day (R5.2.7). Those boundaries are where the
    # reader's sense of "and then, months later" lives, so the shape carries
    # them and the page draws them.
    #
    # `steps` above is already ordered by date; `_runs` orders within a
    # meeting; so the appearances come out in the order they happened, which is
    # what the case view exists to show.
    heard, budget = [], MAX_CASE_LINES
    for st in steps:
        spans = [dict(x) for x in con.execute("""
            SELECT sp.video_id, sp.part, sp.start, sp."end", sp.start_idx,
                   sp.end_idx, v.session_seq, v.duration
            FROM item_spans sp JOIN videos v ON v.id = sp.video_id
            WHERE sp.agenda_item_id = %s
            ORDER BY v.session_seq NULLS FIRST, sp.part, sp.start""",
            (st["id"],))]
        for r in _runs(spans):
            got = [dict(x) for x in con.execute(
                LINES, (r["video_id"], r["start_idx"], r["end_idx"])
            )][:max(budget, 0)]
            budget -= len(got)
            heard.append({**r, "lines": got,
                          "item_id": st["id"], "code": st["code"],
                          "meeting_id": st["meeting_id"], "date": st["date"],
                          "body": st["body"], "phase": st["phase"],
                          "outcome": st["outcome"],
                          "disposition": st["disposition"]})
    # Offices rotate annually and this case may span years, so the lookup that
    # turns a surname into "Starkey, Chairman" is per MEETING, not per case.
    offices = {m: _offices(con, m) for m in {h["meeting_id"] for h in heard}}

    c = con.execute(
        "SELECT id, prefix, first_seen, last_seen, meetings, bodies "
        "FROM cases WHERE id = %s", (case_id,)).fetchone()

    # R5.4.3: the terminal outcome must be findable among the procedural steps
    # that precede it. A continuance is not a conclusion - it is the board
    # saying "not today" - so it never counts as one, and a case whose last
    # appearance was a continuance is OPEN rather than resolved.
    terminal = next((s for s in reversed(steps)
                     if s["outcome"] and s["outcome"] != "continued"), None)

    # The full official title, once (R5.4.2). The most-repeated wording wins;
    # ties go to the longest, because the fuller version is the one that
    # actually describes the application.
    counts = {}
    for s in steps:
        if s["title"]:
            counts[s["title"]] = counts.get(s["title"], 0) + 1
    title = max(counts, key=lambda t: (counts[t], len(t))) if counts else None

    return {
        "case": dict(c) if c else {"id": case_id, "prefix": None,
                                   "first_seen": None, "last_seen": None,
                                   "meetings": None, "bodies": None},
        "case_id": case_id,
        "title": title,
        "steps": steps,
        "bodies": sorted({s["body"] for s in steps}),
        "first": steps[0]["date"],
        "last": steps[-1]["date"],
        "terminal": ({"id": terminal["id"], "date": terminal["date"],
                      "body": terminal["body"], "outcome": terminal["outcome"],
                      "disposition": terminal["disposition"]}
                     if terminal else None),
        "continuances": sum(1 for s in steps if s["outcome"] == "continued"),
        "recorded": sum(1 for s in steps if s["span"]),
        "heard": heard,
        "offices": offices,
        # Says so when the cap bites, rather than trailing off (R2.4). At the
        # archive's largest case this is 2,349 of 4,000, so it never has.
        "heard_lines": sum(len(h["lines"]) for h in heard),
        "heard_truncated": sum(h["end_idx"] - h["start_idx"] + 1
                               for h in heard) > sum(len(h["lines"])
                                                     for h in heard),
    }
