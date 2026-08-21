"""The data layer for the rebuilt UI."""
import re

# The county's public portal. Every meeting and item should be able to point at
# the authoritative upstream; three of the three civic archives reviewed
# in the design notes do this and we held the id and linked nowhere.
PORTAL = "https://pascocofl.portal.civicclerk.com/event/{event_id}/overview"

# The county's own PDF, served by the same API bin/civicclerk.py mirrors text
# from - `plainText=false` returns the document itself. For a project whose
# thesis is that the published record is authoritative, the actual document is
# the strongest provenance available and it costs a URL.
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
    """The archive as a list, newest first."""
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
    """The collection as an object, and the shape of it over time."""
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
# ------------------------------------------------------------- disagreement
# "Where the board disagreed", the one entryway that cannot be assembled from
# counts. It reads BOTH sources, because each is blind where the other sees:
# the record names dissent authoritatively but is published weeks late, and the
# transcript catches division the minutes never record, since a debate that
# produced no motion leaves no outcome. Kept apart in the result and marked,
# never merged: the record lane is quotable, the transcript lane is ASR.

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

# ------------------------------------------------------- who said this, once
#
# `(exchange)` NEVER CROSSES THIS BOUNDARY. It is an internal token for a
# passage spanning several people and no reader may see it. It used to be
# filtered in the browser, in two different files. An API that cannot emit it
# is a stronger guarantee than a UI that remembers to strip it.
EXCHANGE = "(exchange)"

def line(r):
    """One row of LINES, with the speaker claim assembled into `who`."""
    d = dict(r)
    d["who"] = who(d["name"], d["display_name"], d["basis"], d["human"],
                   d["contested"])
    return d

def who(name=None, display=None, basis=None, human=False, contested=False):
    """Who said this, in the only shape the UI accepts. ."""
    several = name == EXCHANGE
    return {
        "name": None if several else name,
        # Falls back to the key, so a caller that has no display name degrades
        # to the surname rather than to nothing.
        "display_name": None if several else (display or name),
        "basis": basis,
        "human": bool(human),
        "contested": bool(contested),
        "several": several,
    }

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
       us.name AS speaker, us.display_name AS speaker_display,
       -- HOW SURE THE NAME IS, and this is the surface that most needs to
       -- say. Everything here is a named board member attached to a split
       -- vote or an objection - the two claims a person is least willing to
       -- have wrong about them - printed on the front page in bold. It said
       -- them all in the same voice whether a human had confirmed the name or
       -- a voice model had guessed it. SpeakerChip draws the difference.
       us.human, us.basis,
       said.text AS quote,
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
    # Items heard together share one motion and one outcome sentence verbatim -
    # six consent items in February 2016 carry the same sentence about
    # Commissioner Mariano. Six identical rows is not six disagreements, so
    # the motion is the unit and the rest are counted, not listed.
    rows = [dict(r) for r in con.execute(f"""
        SELECT DISTINCT ON (ai.meeting_id, ai.outcome_text)
               ai.id, ai.code, ai.title, ai.outcome, ai.outcome_text,
               ai.case_id, m.id AS meeting_id, m.date, m.body,
               COUNT(*) OVER (PARTITION BY ai.meeting_id, ai.outcome_text) AS items
          FROM agenda_items ai JOIN meetings m ON m.id = ai.meeting_id
         WHERE ai.outcome_text ~* '{NAY_SQL}'
         ORDER BY ai.meeting_id, ai.outcome_text, ai.seq""")]
    for r in rows:
        r["source"] = "record"
        r["dissent"] = NAY_NAMES.findall(r["outcome_text"] or "")
    rows.sort(key=lambda r: (r["date"], r["id"]), reverse=True)
    return rows[:limit]

def _divided_room(con, limit, seen=()):
    """Division the recording caught. Inferred, and marked so on the page."""
    hits = {}
    for r in con.execute(ROOM_SQL, ROOM_ARGS):
        d = dict(r)
        d["who"] = who(d["speaker"], d["speaker_display"], d["basis"], d["human"])
        hits[d["id"]] = d
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
    # \b is a BACKSPACE. Python is the other way round, so reusing
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

def highlights(con, limit=6, divided_limit=None):
    """- curated entry points, so arriving does not require a question."""
    record = _divided_record(con, divided_limit or limit)
    divided = {"record": record,
               "room": _divided_room(con, divided_limit or limit,
                                     [r["id"] for r in record])}

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

        # The MEETING is the unit here, not the item. 113 things were decided on one
        # day in July 2026, and a list of items showed eight of them by sequence
        # number, which is an arbitrary sample presented as a summary. A meeting-day
        # says how much business was done and names the part that was not routine.
    decided = [dict(r) for r in con.execute("""
        SELECT m.id AS meeting_id, m.date, m.body,
               COUNT(*) FILTER (WHERE ai.outcome IS NOT NULL)          AS decided,
               COUNT(*) FILTER (WHERE ai.outcome IN ('approved','adopted'))
                                                                       AS passed,
               COUNT(*) FILTER (WHERE ai.outcome IN ('denied','no_action'))
                                                                       AS refused,
               COUNT(*) FILTER (WHERE ai.outcome = 'withdrawn')        AS withdrawn,
               COUNT(*) FILTER (WHERE ai.outcome = 'continued')        AS continued,
               COUNT(*) FILTER (WHERE ai.outcome_text ~* %s)            AS divided,
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
                    OR ai.outcome_text ~* %s)
             ORDER BY (ai.outcome IN ('denied','no_action')) DESC, ai.seq
             LIMIT 2""", (d["meeting_id"], NAY_SQL))]

    return {"divided": divided, "continued": continued, "decided": decided}

# --------------------------------------------------------------- the issues
# --------------------------------------------------------------- the issues
# What the county keeps coming back to. Everything else browse shows is either
# structural or recent, so the page could say the county met 1,214 times and not
# one thing it met ABOUT.
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

def _issue_specs(con):
    """The subjects to draw, and the SQL each one matches with."""
    try:
        import subjects
        live = subjects.patterns(con)
    except Exception:
        live = {}
    if live:
        out = []
        for slug, d in live.items():
            # A sub-subject whose parent was dropped would render as a
            # top-level row of a narrowing nobody asked for, so it is orphaned
            # back to the top rather than hidden - hiding it would lose items
            # from the page with nothing saying so.
            out.append({
                "slug": slug, "label": d["label"], "q": d["q"],
                "parent": d["parent"],
                                # A CHILD IS COUNTED INSIDE ITS PARENT. Its vocabulary
                                # is a different
                                # list, not a smaller one, so without this a sub-subject
                                # reaches items
                                # the subject it narrows never matched. It is also what
                                # keeps a parent
                                # the sum-or-more of its parts.
                "record_in": d["record_in"],
                "room_in": d["room_in"],
                "record": d["record"], "record_not": d["record_not"],
                "room": d["room"], "room_not": d["room_not"]})
        return out
    return [{"slug": i["slug"], "label": i["label"], "q": i["q"],
             "parent": None, "record_in": None, "room_in": None,
             "record": i["record"], "record_not": None,
             "room": i["room"], "room_not": None} for i in ISSUES]

def _issues_rolled(con, specs):
    """The strip, read back out of `subject_year`."""
    rows = [dict(r) for r in con.execute("""
        SELECT slug, year, items, meetings, decided, continued, refused,
               divided, lines, heard, first, last
          FROM subject_year ORDER BY slug, year""")]
    if not rows:
        return None
    span = [r[0] for r in con.execute("""
        SELECT left(m.date, 4) FROM meetings m
         WHERE m.date <= to_char(now(), 'YYYY-MM-DD')
         GROUP BY 1 ORDER BY 1""")]
    heard_from = con.execute("""
        SELECT MIN(left(m.date, 4)) FROM meetings m
         WHERE EXISTS (SELECT 1 FROM videos v
                        WHERE v.meeting_id = m.id AND v.transcribed)""").fetchone()[0]
    by = {}
    for r in rows:
        by.setdefault(r["slug"], {})[r["year"]] = r
    out = []
    for spec in specs:
        got = by.get(spec["slug"])
        if not got:
            continue
        first = min(r["first"] for r in got.values() if r["first"])
        last = max(r["last"] for r in got.values() if r["last"])
        out.append({
            "slug": spec["slug"], "label": spec["label"], "q": spec["q"],
            "parent": spec.get("parent"),
            "items": sum(r["items"] for r in got.values()),
            "meetings": sum(r["meetings"] for r in got.values()),
            "continued": sum(r["continued"] for r in got.values()),
            "refused": sum(r["refused"] for r in got.values()),
            "divided": sum(r["divided"] for r in got.values()),
            "lines": sum(r["lines"] for r in got.values()),
            "heard": sum(r["heard"] for r in got.values()),
            "first": first, "last": last,
            "years": [{"year": y,
                       "items": got[y]["items"] if y in got else 0,
                       "meetings": got[y]["meetings"] if y in got else 0,
                       "decided": got[y]["decided"] if y in got else 0,
                       "pushed": (got[y]["continued"] + got[y]["refused"]
                                  + got[y]["divided"]) if y in got else 0,
                       "continued": got[y]["continued"] if y in got else 0,
                       "refused": got[y]["refused"] if y in got else 0,
                       "divided": got[y]["divided"] if y in got else 0,
                       "lines": got[y]["lines"] if y in got else 0,
                       "heard": got[y]["heard"] if y in got else 0}
                      for y in span],
        })
    out.sort(key=lambda i: (-i["meetings"], -i["heard"], i["label"]))
    return {"span": span, "heard_from": heard_from, "issues": out}

def issues(con, live=False):
    """Each issue's twelve years, in both sources and never merged."""
    specs = _issue_specs(con)
    if not specs:
        return {"span": [], "heard_from": None, "issues": []}
    # The front page reads the precomputed table. The join below costs 163s
    # once sub-subjects exist, because a child evaluates its own alternation
    # and its parent's against every published title and `~*` takes no index -
    # so it runs at curation time (`bin/subjects.py` rollup) and never on a
    # request. `live=True` is that rebuild asking for the real thing.
    if not live:
        got = _issues_rolled(con, specs)
        if got:
            return got
    vals = ", ".join(["(%s, %s, %s, %s)"] * len(specs))

    # Two counts of meetings, one per source, and both are summed per year
    # later. That is exact rather than approximate: a meeting has one date, so
    # it falls in exactly one year and cannot be counted in two.
    record = [dict(r) for r in con.execute(f"""
        WITH t(slug, rx, rx_not, rx_in) AS (VALUES {vals})
        SELECT t.slug, left(m.date, 4) AS year,
               COUNT(*)                                          AS items,
               COUNT(DISTINCT m.id)                              AS meetings,
               -- The denominator the `pushed` lane needs. Without it a year
               -- with no approved minutes and a year the board passed
               -- everything both read as zero pushed back, and refuses
               -- exactly that: no outcome RECORDED is not `no_action`.
               COUNT(*) FILTER (WHERE ai.outcome IS NOT NULL)    AS decided,
               COUNT(*) FILTER (WHERE ai.outcome = 'continued')  AS continued,
               COUNT(*) FILTER (WHERE ai.outcome
                                      IN ('denied','no_action')) AS refused,
               COUNT(*) FILTER (WHERE ai.outcome_text
                                      ~* '{NAY_SQL}')            AS divided,
               MIN(m.date) AS first, MAX(m.date) AS last
          FROM t
          JOIN agenda_items ai
            ON ai.source = 'agenda' AND ai.title ~* t.rx
           -- A negative phrase excludes. The archive genuinely needs it: bare
           -- "license plate" takes in the county's spay/neuter specialty-plate
           -- grant, which is not a camera.
           AND (t.rx_not IS NULL OR ai.title !~* t.rx_not)
           -- A sub-subject is counted INSIDE the subject it narrows.
           AND (t.rx_in IS NULL OR ai.title ~* t.rx_in)
          JOIN meetings m ON m.id = ai.meeting_id
         WHERE m.date <= to_char(now(), 'YYYY-MM-DD')
         GROUP BY 1, 2""",
        [v for s in specs
         for v in (s["slug"], s["record"], s["record_not"], s["record_in"])])]

    room = [dict(r) for r in con.execute(f"""
        WITH t(slug, tsq, tsq_not, tsq_in) AS (VALUES {vals})
        SELECT t.slug, left(m.date, 4) AS year,
               COUNT(*)             AS lines,
               COUNT(DISTINCT m.id) AS meetings,
               MIN(m.date) AS first, MAX(m.date) AS last
          FROM t
          JOIN utterances u
            ON u.tsv @@ to_tsquery('english', t.tsq)
           AND (t.tsq_not IS NULL
                OR NOT (u.tsv @@ to_tsquery('english', t.tsq_not)))
           AND (t.tsq_in IS NULL OR u.tsv @@ to_tsquery('english', t.tsq_in))
          JOIN videos v ON v.id = u.video_id
          JOIN meetings m ON m.id = v.meeting_id
         GROUP BY 1, 2""",
        [v for s in specs
         for v in (s["slug"], s["room"], s["room_not"], s["room_in"])])]

    # The axis is the archive's own span, so a thirteenth year needs no edit
    # here - the same reason TimeAxis derives its heading rather than printing
    # "twelve".
    span = [r[0] for r in con.execute("""
        SELECT left(m.date, 4) FROM meetings m
         WHERE m.date <= to_char(now(), 'YYYY-MM-DD')
         GROUP BY 1 ORDER BY 1""")]
    # Before this year the room lane is not quiet, it does not exist. The page
    # has to draw that difference or every issue reads as new.
    heard_from = con.execute("""
        SELECT MIN(left(m.date, 4)) FROM meetings m
         WHERE EXISTS (SELECT 1 FROM videos v
                        WHERE v.meeting_id = m.id AND v.transcribed)""",
        ).fetchone()[0]

    out = []
    for spec in specs:
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
            # The row this one narrows, or null. A parent still counts
            # everything its own vocabulary matches, children included: the
            # page shows the whole subject and then what it is made of, which
            # only reads if the top row is the whole.
            "parent": spec.get("parent"),
            "items": sum(r["items"] for r in rec.values()),
            "meetings": sum(r["meetings"] for r in rec.values()),
            "continued": sum(r["continued"] for r in rec.values()),
            "refused": sum(r["refused"] for r in rec.values()),
            "divided": sum(r["divided"] for r in rec.values()),
            "lines": sum(r["lines"] for r in rm.values()),
            "heard": sum(r["meetings"] for r in rm.values()),
            "first": min(dates), "last": max(dates),
                        # `pushed` is continued + denied/no_action + an outcome naming a
                        # nay vote:
                        # the item did not simply pass. Previously summed away, so the
                        # strip could
                        # say when a subject was BUSY and never when it was HARD. It
                        # comes from the
                        # approved minutes, which cover all twelve years whether or not
                        # a camera
                        # ran, so unlike anything measured against the room it is not
                        # shaped by
                        # what we can hear.
            "years": [{"year": y,
                       "items": rec[y]["items"] if y in rec else 0,
                       "meetings": rec[y]["meetings"] if y in rec else 0,
                       "decided": rec[y]["decided"] if y in rec else 0,
                       "pushed": (rec[y]["continued"] + rec[y]["refused"]
                                  + rec[y]["divided"]) if y in rec else 0,
                       "continued": rec[y]["continued"] if y in rec else 0,
                       "refused": rec[y]["refused"] if y in rec else 0,
                       "divided": rec[y]["divided"] if y in rec else 0,
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

# Names the naming stage produced that are not people, which the rail must not
# publish as people. Written down against the rule below, deliberately: there is
# no structural test separating `Connected City` from `Chris Williams`. Neither
# is on the board, neither is in `people`, both arrived the same way, and one is
# a development off SR-52.
#
# Scoped to the rail on purpose. A transcript line attributed to one of these is
# a claim the resolver made and the chip already draws as inferred; a rail is the
# site saying "these are the people you may filter by", which is editorial and
# ours to be right about. audit.py's `speaker.rail_is_people` fails when a NEW
# value reaches the rail, so this list cannot silently go stale.
NOT_A_PERSON = frozenset({
    "Connected City",   # a development off SR-52
    "Dade City",        # a city
    "Pasco County",     # the county itself
    "Sun Coast",        # the Suncoast Parkway
    "What",             # an ASR fragment; 601 lines of it
})

def facets(con):
    """The values a search rail may offer."""
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
        # `speaker` is what the filter is written against and what the URL
        # carries; `speaker_display` is only the label. Sending one string for
        # both would mean either a filter that no longer matches
        # passages.speaker or a rail that reads "Starkey".
        "speakers": [dict(r) for r in con.execute("""
            SELECT name AS speaker, display_name AS speaker_display,
                   COUNT(*) AS lines
              FROM utterance_speaker
             WHERE name IS NOT NULL AND NOT (name = ANY(%s))
             GROUP BY name, display_name HAVING COUNT(*) >= 500
             ORDER BY lines DESC LIMIT 40""", (list(NOT_A_PERSON),))],
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
    of magnitude larger; the spine has to paint first.
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

    # The published roster, not an inference. Offices rotate annually,
    # so chair/vice-chair are per-meeting facts and come from this table rather
    # than from anything about the person.
    out["roster"] = [dict(r) for r in con.execute("""
        SELECT p.id AS person_id, p.surname, p.full_name, r.district, r.office
        FROM meeting_roster r JOIN people p ON p.id = r.person_id
        WHERE r.meeting_id = %s
        ORDER BY r.district NULLS LAST, p.surname""", (meeting_id,))]

    # The spine. Items in PUBLISHED order, each with where it occurs
    # in the recordings. An item interrupted by the lunch break is one row with
    # two spans, which is why spans are aggregated rather than joined flat.
    out["items"] = [dict(r) for r in con.execute("""
        SELECT ai.id, ai.seq, ai.code, ai.section, ai.phase, ai.title,
               ai.case_id, ai.department, ai.recommendation, ai.outcome_text,
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
    # is owed the distinction. `extracted` is false rather than null for a row
    # whose text is not held at all, so a caller can treat it as a boolean
    # instead of guessing what null meant. Only Agenda and Minutes are ever
    # listed - bin/civicclerk.py's WANT skips the Agenda Packet, which runs to
    # 100MB - so this guards a fetch that failed rather than a kind that is
    # skipped by policy.
    out["files"] = [{**dict(r), "url": FILE.format(file_id=r["file_id"])}
                    for r in con.execute("""
        SELECT f.file_id, f.kind, f.name, f.published_at, f.chars,
               (f.body_text IS NOT NULL AND f.chars >= %s) AS extracted,
               f.event_id
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
    # and stepping through a series does not require going back to an
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
_LINES = """
    SELECT u.video_id, u.idx, u.start, u."end", u.text,
           u.cluster AS voice, u.local_label,
           us.name, us.display_name, us.human,
           -- How the name was arrived at, so the page can say so rather than
           -- presenting four very different kinds of claim identically
           --. 'cluster' is the weakest: it is the archive-wide
           -- majority for this voice, not evidence about this meeting.
           us.basis, us.contested,
           sp.agenda_item_id
    FROM utterances u
    JOIN utterance_speaker us
      ON us.video_id = u.video_id AND us.idx = u.idx
    LEFT JOIN item_spans sp
           ON sp.video_id = u.video_id
          AND u.idx BETWEEN sp.start_idx AND sp.end_idx
    WHERE u.video_id = %s AND {span}
    ORDER BY u.idx"""

# By position in the recording, which is what a caption under the player has:
# a clock, not an index. Overlap rather than containment, so the line being
# spoken across the edge of the window is in it.
LINES = _LINES.format(span="u.idx BETWEEN %s AND %s")
LINES_BETWEEN = _LINES.format(span='u."end" >= %s AND u.start <= %s')

def _offices(con, meeting_id):
    """surname -> the office they held AT THIS MEETING."""
    return {r[0]: {"office": r[1], "district": r[2], "full_name": r[3]}
            for r in con.execute("""
        SELECT p.surname, r.office, r.district, p.full_name
        FROM meeting_roster r JOIN people p ON p.id = r.person_id
        WHERE r.meeting_id = %s""", (meeting_id,))}

# How wide a window of a recording anybody may ask for in one request, in
# seconds. A whole meeting is up to 390kB of text - /meeting wants exactly
# that, because it is showing all of it, and asks with no window at all. The
# strip under the player wants about a minute, and paying a third of a
# megabyte to caption ninety seconds of a citation is the kind of thing that
# is invisible on a desk and ruinous on a phone.
MAX_SPAN = 1800

def transcript(con, video_id, span=None):
    """One recording's lines: all of them, or the ones inside a time window.

    `span` is `(from, to)` in seconds, and a line is in it if it OVERLAPS it -
    the sentence being spoken as the window opens is part of what is being
    said, and a rule about containment would drop precisely the line a caption
    exists to show.
    """
    v = con.execute(
        "SELECT id, title, duration, session_seq, meeting_id, upload_date "
        "FROM videos WHERE id = %s", (video_id,)).fetchone()
    if not v:
        return None
    if span is None:
        rows = con.execute(LINES, (video_id, 0, 2 ** 31 - 1))
    else:
        a, b = span
        rows = con.execute(LINES_BETWEEN, (video_id, a, b))
    out = {"video": dict(v), "lines": [line(r) for r in rows],
           "offices": _offices(con, v["meeting_id"])}
    # WHAT WAS ACTUALLY ASKED FOR, handed back, so the caller knows what it
    # holds rather than inferring it from the lines it got - which is
    # unanswerable when a window is silent and returns none.
    if span is not None:
        out["span"] = [span[0], span[1]]
    return out

# --------------------------------------------------------------- agenda item
MAX_ITEM_LINES = 2000

# Everything ever said about one case, across every meeting that took it up.
# Median 32 lines, p90 221, p99 783, largest in the archive 2,349 (PDD-23-0009,
# four meetings). Same reasoning as MAX_ITEM_LINES, one order of magnitude up.
MAX_CASE_LINES = 4000

# --------------------------------------------------------------- appearances
ONE_APPEARANCE = 60

def _runs(spans):
    """Spans -> the distinct times the item was taken up, in order."""
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

# A NOTE ON WIDE SPANS, so nobody re-derives the wrong conclusion. 110 of 5,587
# spans cover more than half their recording against a median of 3 minutes, and
# that looks like the binder running to the tape. It is not:

def item(con, item_id):
    """One agenda item: the published record, then what was said.

    Ordered the way the requirements are: the county's record leads, because
    it is authoritative and because it is the only thing 91% of decided items
    have. The transcript follows as a second, weaker source, and the two are
    never merged into one narrative.
    """
    r = con.execute("""
        SELECT ai.id, ai.meeting_id, ai.seq, ai.code, ai.section, ai.phase,
               ai.title, ai.case_id, ai.department, ai.recommendation,
               ai.outcome_text, ai.outcome, ai.outcome_source, ai.source,
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

        # What was said, in the same field-not-string form the transcript uses, so the
        # chip stays the only thing deciding how a speaker is displayed. Grouped by
        # APPEARANCE, not poured into one list: this item's speech used to be
        # concatenated across spans, so an item argued at 18:05 and taken up again at
        # 3:38:04 read as one exchange with hours of unrelated business removed from
        # the middle of it.
    row["runs"] = _runs(spans)
    budget = MAX_ITEM_LINES
    for r in row["runs"]:
        got = [line(x) for x in con.execute(
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

    # the case thread, inline. An item is rarely the whole story, and
    # Councilmatic puts the history ON the item for exactly this reason. The
    # dedicated view is one click away; this is the shape of the sequence.
    row["thread"] = [dict(x) for x in con.execute("""
        SELECT ai.id, ai.code, ai.title, ai.phase, ai.outcome, ai.outcome_text,
               m.id AS meeting_id, m.date, m.body,
               EXISTS (SELECT 1 FROM item_spans sp
                        WHERE sp.agenda_item_id = ai.id) AS recorded
        FROM agenda_items ai JOIN meetings m ON m.id = ai.meeting_id
        WHERE ai.case_id = %s
        ORDER BY m.date, ai.seq""",
        (row["case_id"],))] if row["case_id"] else []

    # the county's own document and the county's own page. The
    # PDF is proxied rather than linked directly - CivicClerk serves it with
    # `Content-Disposition: attachment`, which makes a cross-origin frame
    # download the file instead of rendering it (see server._file).
    row["files"] = [{**dict(x), "url": FILE.format(file_id=x["file_id"]),
                     "inline": f"/api/file/{x['file_id']}"}
                    for x in con.execute("""
        SELECT f.file_id, f.kind, f.name, f.published_at, f.chars,
               (f.body_text IS NOT NULL AND f.chars >= %s) AS extracted,
               f.event_id
        FROM portal_files f
        JOIN portal_events pe ON pe.id = f.event_id
        WHERE pe.meeting_id = %s
        ORDER BY f.kind, f.published_at""", (SUBSTANTIVE_CHARS, meeting["id"]))]

    ev = con.execute(
        "SELECT id FROM portal_events WHERE meeting_id = %s ORDER BY id LIMIT 1",
        (meeting["id"],)).fetchone()
    row["portal"] = _portal_url(ev["id"]) if ev else None

    # Neighbours in published order, so an item is never a dead end and
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
    """One application, every meeting that took it up, in order."""
    steps = [dict(r) for r in con.execute("""
        SELECT ai.id, ai.seq, ai.code, ai.section, ai.phase, ai.title,
               ai.department, ai.recommendation, ai.outcome_text, ai.outcome,
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

        # EVERYTHING SAID ABOUT THIS CASE, over every meeting that took it up: the
        # only place in the site a reader can follow one argument across a year. Not a
        # flat transcript. A hearing is bounded by its meeting, by which session, and
        # by the fact that a board can take an item up twice in one day, and those
        # boundaries are where the reader's sense of "and then, months later" lives.
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
            got = [line(x) for x in con.execute(
                LINES, (r["video_id"], r["start_idx"], r["end_idx"])
            )][:max(budget, 0)]
            budget -= len(got)
            heard.append({**r, "lines": got,
                          "item_id": st["id"], "code": st["code"],
                          "meeting_id": st["meeting_id"], "date": st["date"],
                          "body": st["body"], "phase": st["phase"],
                          "outcome": st["outcome"],
                          "outcome_text": st["outcome_text"]})
    # Offices rotate annually and this case may span years, so the lookup that
    # turns a surname into "Starkey, Chairman" is per MEETING, not per case.
    offices = {m: _offices(con, m) for m in {h["meeting_id"] for h in heard}}

    c = con.execute(
        "SELECT id, prefix, first_seen, last_seen, meetings, bodies "
        "FROM cases WHERE id = %s", (case_id,)).fetchone()

    # the terminal outcome must be findable among the procedural steps
    # that precede it. A continuance is not a conclusion - it is the board
    # saying "not today" - so it never counts as one, and a case whose last
    # appearance was a continuance is OPEN rather than resolved.
    terminal = next((s for s in reversed(steps)
                     if s["outcome"] and s["outcome"] != "continued"), None)

    # The full official title, once. The most-repeated wording wins;
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
                      "outcome_text": terminal["outcome_text"]}
                     if terminal else None),
        "continuances": sum(1 for s in steps if s["outcome"] == "continued"),
        "recorded": sum(1 for s in steps if s["span"]),
        "heard": heard,
        "offices": offices,
        # Says so when the cap bites, rather than trailing off. At the
        # archive's largest case this is 2,349 of 4,000, so it never has.
        "heard_lines": sum(len(h["lines"]) for h in heard),
        "heard_truncated": sum(h["end_idx"] - h["start_idx"] + 1
                               for h in heard) > sum(len(h["lines"])
                                                     for h in heard),
    }
