"""Land the published agendas into the domain tables, and bind the transcript.

Three passes, each idempotent:

  MEETINGS      A meeting is the real-world event. It comes from the portal,
                which knows the body and the date; recordings attach to it,
                one per session. Recordings whose meeting has no portal entry
                still get a meeting row, because the archive predates and
                outlives the portal's coverage of any single body.

  ITEMS         Parsed agenda items become agenda_items, with the case number
                promoted into `cases` so a rezoning can be followed across
                bodies and years.

  SPANS         The transcript's LLM-derived segments are matched to published
                items by agenda code and become item_spans. Segments that
                match nothing are not discarded - a recess or a call to order
                is real, it is just not an agenda item - so they are kept as
                agenda_items with source='transcript' and no code.

The binding is deliberately conservative: a code must appear in the segment
title AND exist on that meeting's published agenda. A near-miss is left
unbound rather than guessed, because a wrong bind silently attributes one
item's discussion to another item's case history.
"""
import argparse
import re
import sys

import db
import parse_agenda as pa
import segment as seg

# The portal's body names against the `kind` we parsed out of YouTube titles.
BODY_KIND = {
    "Board of County Commissioners": "bcc",
    "Planning Commission": "planning",
    "Metropolitan Planning Organization": "mpo",
}
KIND_BODY = {v: k for k, v in BODY_KIND.items()}

CODE_IN_TITLE = re.compile(r"\b([A-Z]{1,3})\s?-?\s?(\d{1,3})\b")

# The published agenda names its sections in prose ("Public Hearings"); the
# transcript segmenter uses a fixed vocabulary ("public_hearing"). Storing both
# in one column gives a filter that matches half its rows and reports no error:
# phase='public_comment' would have found 104 transcript items and missed 9,762
# published hearings. Everything is normalised to segment.PHASES on the way in.
SECTION_PHASE = {
    "consent": "consent",
    "regular": "regular",
    "work session": "regular",
    "public hearing": "public_hearing",
    "public hearings": "public_hearing",
    "resolution": "proclamation",
    "resolutions": "proclamation",
    "proclamation": "proclamation",
    "proclamations": "proclamation",
    "public comment": "public_comment",
    "board report": "board_reports",
    "board reports": "board_reports",
    "county administrator": "staff_report",
    "county attorney": "staff_report",
    "staff report": "staff_report",
    "call to order": "call_to_order",
    "roll call": "call_to_order",
    "invocation": "call_to_order",
    "pledge of allegiance": "call_to_order",
    "recess": "recess",
    "adjourn": "adjourn",
    "adjournment": "adjourn",
}


def canonical_phase(section):
    """An agenda section name -> the one phase vocabulary the app filters on."""
    key = " ".join((section or "").lower().split()).strip(" .:")
    return SECTION_PHASE.get(key, "other")


def upsert_meetings(con):
    """One row per real meeting, from the portal where it exists."""
    con.execute("""
        INSERT INTO meetings (date, body, title)
        SELECT DISTINCT to_char(pe.event_date,'YYYY-MM-DD'), pe.body, pe.name
        FROM portal_events pe
        WHERE NOT EXISTS (SELECT 1 FROM meetings m
                          WHERE m.date = to_char(pe.event_date,'YYYY-MM-DD')
                            AND m.body = pe.body)""")
    # Same ambiguity, same fix: 187 portal events sit on a date+body that
    # matches several meeting rows. Here the event's own NAME disambiguates,
    # because it is what the meeting title was created from in the first place.
    con.execute("""
        WITH candidate AS (
            SELECT pe.id AS event_id, m.id AS meeting_id,
                   row_number() OVER (
                       PARTITION BY pe.id
                       ORDER BY (m.title IS NOT DISTINCT FROM pe.name) DESC,
                                m.id) AS pick
              FROM portal_events pe
              JOIN meetings m
                ON m.date = to_char(pe.event_date,'YYYY-MM-DD')
               AND m.body = pe.body
             WHERE pe.meeting_id IS NULL
                OR NOT EXISTS (SELECT 1 FROM meetings cur
                                WHERE cur.id = pe.meeting_id
                                  AND cur.date = to_char(pe.event_date,'YYYY-MM-DD')
                                  AND cur.body = pe.body)
                -- ...or it is on a sibling that is NOT the meeting it names,
                -- while the meeting it names exists. Sticky alone would freeze
                -- that: a wrong sibling still matches date and body, so the
                -- link looks valid and never moves. 112 events sat like this -
                -- a "Pasco County Commission Workshop" attached to the plain
                -- "Pasco County Commission" of the same day, a Bicycle and
                -- Pedestrian Advisory Committee attached to the Citizens'
                -- Advisory Committee. Their MINUTES then hang off the wrong
                -- meeting, which is what minutes.orphaned_outcomes has been
                -- reporting. Preferring the exact name converges: it moves an
                -- event once and then leaves it alone.
                OR (EXISTS (SELECT 1 FROM meetings better
                             WHERE better.date = to_char(pe.event_date,'YYYY-MM-DD')
                               AND better.body = pe.body
                               AND better.title IS NOT DISTINCT FROM pe.name)
                    AND NOT EXISTS (SELECT 1 FROM meetings cur
                                     WHERE cur.id = pe.meeting_id
                                       AND cur.title IS NOT DISTINCT FROM pe.name)))
        UPDATE portal_events pe SET meeting_id = c.meeting_id
          FROM candidate c
         WHERE c.event_id = pe.id AND c.pick = 1
           AND pe.meeting_id IS DISTINCT FROM c.meeting_id""")

    # Recordings we hold whose meeting the portal does not list (other bodies,
    # workshops, or dates outside its coverage) still need a meeting to hang on.
    con.execute("""
        INSERT INTO meetings (date, body, title)
        SELECT DISTINCT v.upload_date, COALESCE(k.body, v.kind), min(v.title)
        FROM videos v
        LEFT JOIN (VALUES ('bcc','Board of County Commissioners'),
                          ('planning','Planning Commission'),
                          ('mpo','Metropolitan Planning Organization'))
             AS k(kind, body) ON k.kind = v.kind
        WHERE v.upload_date IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM meetings m
                          WHERE m.date = v.upload_date
                            AND m.body = COALESCE(k.body, v.kind))
        GROUP BY v.upload_date, COALESCE(k.body, v.kind)""")
    # STICKY, and deterministic when it has to choose. Keying a link on
    # (date, body) alone is ambiguous: 74 date+body pairs in this archive carry
    # several meeting rows, and every one of them is a DIFFERENT committee of
    # the same parent - MPO's Technical, Citizens and Bicycle & Pedestrian
    # advisory committees all meet on 2027-01-11. There are zero true
    # duplicates; the `body` column simply cannot tell them apart.
    #
    # The old form ended `AND v.meeting_id IS DISTINCT FROM m.id`, which does
    # not mean "fix wrong links" - it means "relink whenever the current
    # meeting is not THIS sibling", so with several siblings matching, the
    # winner is whichever row Postgres happens to yield. 32 of 432 transcribed
    # recordings sit on such a date, and they migrated on every run. That is
    # the engine behind the stranded transcript items in gotcha 77: the video
    # moves, bind_spans no longer finds the item under the new meeting_id, and
    # creates another.
    #
    # So: only link a video that has no meeting, or whose meeting no longer
    # matches its own date and body. A video already sitting on a valid sibling
    # is left where it is. The tie-break for a genuinely new link is the
    # sibling holding the most published items - the one the county's agenda
    # actually landed on - and then the lowest id, so it never depends on plan
    # order.
    con.execute("""
        WITH candidate AS (
            SELECT v.id AS video_id, m.id AS meeting_id,
                   row_number() OVER (
                       PARTITION BY v.id
                       ORDER BY (SELECT count(*) FROM agenda_items a
                                  WHERE a.meeting_id = m.id
                                    AND a.source = 'agenda') DESC,
                                m.id) AS pick
              FROM videos v
              JOIN (VALUES ('bcc','Board of County Commissioners'),
                           ('planning','Planning Commission'),
                           ('mpo','Metropolitan Planning Organization'))
                   AS k(kind, body) ON k.kind = v.kind
              JOIN meetings m ON m.date = v.upload_date AND m.body = k.body
             WHERE v.meeting_id IS NULL
                OR NOT EXISTS (SELECT 1 FROM meetings cur
                                WHERE cur.id = v.meeting_id
                                  AND cur.date = v.upload_date
                                  AND cur.body = k.body))
        UPDATE videos v SET meeting_id = c.meeting_id
          FROM candidate c
         WHERE c.video_id = v.id AND c.pick = 1
           AND v.meeting_id IS DISTINCT FROM c.meeting_id""")
    con.execute("""
        UPDATE videos v SET meeting_id = m.id FROM meetings m
        WHERE v.meeting_id IS NULL AND m.date = v.upload_date AND m.body = v.kind""")

    # Session order within the day, so morning precedes afternoon.
    for r in con.execute("SELECT id, title FROM videos WHERE meeting_id IS NOT NULL"):
        con.execute("UPDATE videos SET session_seq=%s WHERE id=%s",
                    (seg.session_rank(r["title"]), r["id"]))
    con.commit()
    return con.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]


def land_items(con, redo=False):
    """Parsed agenda items -> agenda_items, with case numbers promoted."""
    if redo:
        con.execute("DELETE FROM agenda_items WHERE source='agenda'")
    rows = con.execute("""
        SELECT pe.id ev, pe.meeting_id, pe.event_date::date d, pf.body_text
        FROM portal_files pf
        JOIN portal_events pe ON pe.id = pf.event_id
        WHERE pf.kind='Agenda' AND pf.chars > 2000 AND pe.meeting_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM agenda_items ai
                          WHERE ai.portal_event_id = pe.id)
        ORDER BY pe.event_date""").fetchall()
    con.commit()
    n_items = n_cases = 0
    for r in rows:
        items, _ = pa.parse(r["body_text"])
        if not items:
            continue
        for seq, it in enumerate(items):
            case = pa.normalise_case(it["file_number"])
            if case:
                con.execute("""
                    INSERT INTO cases (id, prefix, first_seen, last_seen, meetings)
                    VALUES (%s,%s,%s,%s,1)
                    ON CONFLICT (id) DO UPDATE SET
                        first_seen = LEAST(cases.first_seen, EXCLUDED.first_seen),
                        last_seen  = GREATEST(cases.last_seen, EXCLUDED.last_seen)""",
                    (case, case.split("-")[0], r["d"], r["d"]))
                n_cases += 1
            m = re.match(r"^([A-Z]+)(\d+)$", it["code"] or "")
            con.execute("""
                INSERT INTO agenda_items
                    (meeting_id, seq, phase, title, code, code_num, section,
                     department, file_number, case_id, districts, recommendation,
                     portal_event_id, source)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'agenda')
                ON CONFLICT (meeting_id, seq) DO NOTHING""",
                (r["meeting_id"], seq, canonical_phase(it["section"]),
                 it["title"], it["code"], int(m.group(2)) if m else None,
                 it["section"], it["department"], it["file_number"], case,
                 it["districts"], it["recommendation"], r["ev"]))
            n_items += 1
        con.commit()
    # Recompute reach per case rather than accumulating it, so re-runs are safe.
    con.execute("""
        UPDATE cases c SET meetings = s.m, bodies = s.b
        FROM (SELECT ai.case_id, COUNT(DISTINCT ai.meeting_id) m,
                     COUNT(DISTINCT mt.body) b
              FROM agenda_items ai JOIN meetings mt ON mt.id = ai.meeting_id
              WHERE ai.case_id IS NOT NULL GROUP BY ai.case_id) s
        WHERE s.case_id = c.id""")
    con.commit()
    return n_items, n_cases


def code_of(title, valid):
    """The agenda code a segment title refers to, if it names a real one."""
    for m in CODE_IN_TITLE.finditer(title or ""):
        c = f"{m.group(1)}{int(m.group(2))}"
        if c in valid:
            return c
    return None


def bind_spans(con, redo=False):
    """Match transcript segments to published items; keep the rest as phases."""
    if redo:
        con.execute("DELETE FROM item_spans")
        con.execute("DELETE FROM agenda_items WHERE source='transcript'")
        con.commit()

    meetings = con.execute("""
        SELECT DISTINCT v.meeting_id FROM videos v JOIN segments s ON s.video_id=v.id
        WHERE v.meeting_id IS NOT NULL""").fetchall()
    con.commit()
    bound = phase_items = unmatched = 0

    for mrow in meetings:
        mid = mrow["meeting_id"]
        pub = {r["code"]: r["id"] for r in con.execute(
            "SELECT id, code FROM agenda_items WHERE meeting_id=%s "
            "AND source='agenda' AND code IS NOT NULL", (mid,))}
        segs = con.execute("""
            SELECT s.*, v.session_seq FROM segments s JOIN videos v ON v.id=s.video_id
            WHERE v.meeting_id=%s ORDER BY v.session_seq NULLS LAST, s.seq""",
            (mid,)).fetchall()
        nxt = (con.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM agenda_items "
                           "WHERE meeting_id=%s", (mid,)).fetchone()[0])
        for s in segs:
            # The code the model matched against the published list, verified
            # by segment.assemble against that same list. Falls back to reading
            # one out of the title, which is how this worked before the agenda
            # was in the prompt and is still right for segments produced then.
            code = (s.get("code") if s.get("code") in pub else None) if pub else None
            if not code and pub:
                code = code_of(s["title"], pub)
            if code:
                item_id, bound = pub[code], bound + 1
            else:
                # Real, but not an agenda item: a recess, a call to order, or a
                # meeting whose agenda we do not hold. Kept and marked.
                #
                # Reused if one already exists for this span. Without the
                # lookup a second run appends a fresh row at the next free seq
                # instead of matching the old one, so every re-run duplicates
                # every transcript-only item in the archive.
                existing = con.execute(
                    "SELECT ai.id FROM agenda_items ai JOIN item_spans sp "
                    "ON sp.agenda_item_id = ai.id WHERE ai.meeting_id=%s "
                    "AND ai.source='transcript' AND sp.video_id=%s "
                    "AND sp.start_idx=%s", (mid, s["video_id"],
                                            s["start_idx"])).fetchone()
                item_id = existing or con.execute("""
                    INSERT INTO agenda_items
                        (meeting_id, seq, phase, title, search_title, source)
                    VALUES (%s,%s,%s,%s,%s,'transcript')
                    ON CONFLICT (meeting_id, seq) DO NOTHING RETURNING id""",
                    (mid, nxt, s["phase"], s["title"], s["search_title"])).fetchone()
                if item_id is None:
                    unmatched += 1
                    continue
                reused = existing is not None
                item_id = item_id[0]
                if not reused:
                    nxt += 1
                    phase_items += 1
                unmatched += bool(pub)
            con.execute("""
                INSERT INTO item_spans
                    (agenda_item_id, video_id, part, start_idx, end_idx, start, "end")
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (video_id, start_idx) DO UPDATE
                    SET agenda_item_id = EXCLUDED.agenda_item_id""",
                (item_id, s["video_id"], 1 if s["continued"] else 0,
                 s["start_idx"], s["end_idx"], s["start"], s["end"]))
        con.commit()
    return bound, phase_items, unmatched


def report(con):
    q = lambda s: con.execute(s).fetchone()
    print("\n" + "=" * 66)
    r = q("SELECT COUNT(*) n, COUNT(*) FILTER (WHERE id IN "
          "(SELECT meeting_id FROM videos WHERE meeting_id IS NOT NULL)) rec "
          "FROM meetings")
    print(f"meetings        {r['n']:,}  ({r['rec']:,} with a recording)")
    r = q("""SELECT COUNT(*) n, COUNT(*) FILTER (WHERE source='agenda') pub,
                    COUNT(*) FILTER (WHERE case_id IS NOT NULL) wc
             FROM agenda_items""")
    print(f"agenda_items    {r['n']:,}  ({r['pub']:,} published, {r['wc']:,} with a case)")
    r = q("SELECT COUNT(*) n, COUNT(*) FILTER (WHERE meetings>1) multi, "
          "COUNT(*) FILTER (WHERE bodies>1) cross FROM cases")
    print(f"cases           {r['n']:,}  ({r['multi']:,} across >1 meeting, "
          f"{r['cross']:,} across both bodies)")
    r = q("""SELECT COUNT(*) n,
                    COUNT(*) FILTER (WHERE ai.source='agenda') pub
             FROM item_spans sp JOIN agenda_items ai ON ai.id=sp.agenda_item_id""")
    print(f"item_spans      {r['n']:,}  ({r['pub']:,} bound to a published item)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--redo", action="store_true", help="rebuild items and spans")
    # For callers that only want the county's published agenda. bind_spans
    # derives transcript items from `segments`, and a meeting that has not
    # happened has no recording, so no segments, so nothing for it to do.
    #
    # It also USED to be unsafe to re-run - two runs added 447 then 262 rows -
    # which is how this flag came to exist. That cause is fixed (gotcha 78);
    # the flag is kept because skipping work a caller does not need is right on
    # its own terms, not as a guard against a bug.
    ap.add_argument("--no-spans", action="store_true",
                    help="land meetings and published items only; skip the "
                         "transcript binding, which needs segments that a "
                         "not-yet-held meeting does not have")
    args = ap.parse_args()
    con = db.connect(autocommit=False)
    print(f"meetings ... {upsert_meetings(con):,}", flush=True)
    print("items    ... %d items, %d case references" % land_items(con, args.redo),
          flush=True)
    if args.no_spans:
        print("spans    ... skipped (--no-spans)", flush=True)
    else:
        print("spans    ... %d bound, %d transcript-only phases, %d unmatched"
              % bind_spans(con, args.redo), flush=True)
    report(con)
    return 0


if __name__ == "__main__":
    sys.exit(main())
