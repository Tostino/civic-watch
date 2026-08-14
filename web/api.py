"""Data layer shared by both front-ends.

The labelling model is deliberately simple: a label attaches a NAME to a voice,
where a voice is (video_id, local_label) from diarization. Everything the
workbench does is one operation on that mapping.

    assign  name N to voices V          -> V all become N
    split   name M to a subset of V     -> that subset leaves N and becomes M
    merge   name B to every voice of A   -> A disappears into B

So split and merge are not special cases; they are the same call with a
different selection. Cluster ids are never stored, because re-clustering
reshuffles them (measured: 2% stable).
"""
import collections

SAMPLE_MIN_CHARS = 45

# Mirrors bin/triage.py. A line that is four words or fewer and under two
# seconds cannot identify anyone ("okay.", "yeah."), so it is never offered as
# evidence for naming a voice. Procedural words are excluded from the noise
# list on purpose - "aye", "second", "here" are short but they are the votes,
# and they stay fully present in transcripts and search either way.
SUBSTANTIVE = """
    (LENGTH(TRIM(text)) - LENGTH(REPLACE(TRIM(text),' ','')) + 1) > 4
    AND ("end" - start) >= 2.0
"""


def resolved_name_sql(alias="cn"):
    # Keyed on (video, cluster), not cluster alone: a name has to be one this
    # meeting's roster supports. See the voice_name view in bin/schema.sql.
    return (f"LEFT JOIN voice_name {alias} "
            f"ON {alias}.video_id = u.video_id AND {alias}.cluster = u.cluster")


# ------------------------------------------------------------------ roster
def roster(con):
    """Every known identity, with reach and how it was established."""
    rows = con.execute("""
        WITH voice AS (
            SELECT si.name, si.video_id, si.local_label, si.cluster,
                   si.confidence,
                   (sl.name IS NOT NULL) AS human
            FROM speaker_identity si
            LEFT JOIN speaker_label sl
                   ON sl.video_id = si.video_id
                  AND sl.local_label = si.local_label
            WHERE si.name IS NOT NULL
        )
        SELECT name,
               COUNT(*)                       AS voices,
               COUNT(DISTINCT video_id)       AS meetings,
               COUNT(*) FILTER (WHERE human)  AS human_labels,
               AVG(confidence)                AS confidence,
               COUNT(DISTINCT cluster)        AS clusters
        FROM voice GROUP BY name ORDER BY meetings DESC, voices DESC""")
    out = [dict(r) for r in rows]
    lines = {r[0]: r[1] for r in con.execute("""
        SELECT si.name, COUNT(*) FROM utterances u
        JOIN speaker_identity si
          ON si.video_id = u.video_id AND si.cluster = u.cluster
        WHERE si.name IS NOT NULL GROUP BY si.name""")}
    for r in out:
        r["lines"] = lines.get(r["name"], 0)
        # The roster is ~90% one-off public commenters, which bury the handful
        # of recurring people who actually need curating. Tiering lets the UI
        # separate "curate" work from "confirm a name" work.
        r["tier"] = ("recurring" if r["meetings"] >= 3
                     else "occasional" if r["meetings"] == 2 else "oneoff")
    return out


# ------------------------------------------------------------------- queue
def unidentified(con, limit=60, offset=0):
    """Unnamed voice groups, highest impact first."""
    rows = con.execute("""
        WITH agg AS (
            SELECT cluster, COUNT(*) AS lines,
                   COUNT(DISTINCT video_id) AS meetings
            FROM utterances WHERE cluster IS NOT NULL GROUP BY cluster
        ),
        named AS (
            SELECT cluster FROM speaker_identity
            WHERE name IS NOT NULL GROUP BY cluster
        ),
        ignored AS (
            SELECT DISTINCT si.cluster FROM speaker_ignore ig
            JOIN speaker_identity si ON si.video_id = ig.video_id
                                    AND si.local_label = ig.local_label
        )
        SELECT agg.cluster, agg.lines, agg.meetings
        FROM agg
        WHERE agg.cluster NOT IN (SELECT cluster FROM named)
          AND agg.cluster NOT IN (SELECT cluster FROM ignored)
        ORDER BY agg.meetings * agg.lines DESC
        LIMIT %s OFFSET %s""", (limit, offset)).fetchall()
    return [dict(r) for r in rows]


def ignore_voices(con, members, reason=None, undo=False):
    """Mark voices as not worth naming, or restore them."""
    with con.cursor() as cur:
        if undo:
            cur.executemany(
                "DELETE FROM speaker_ignore WHERE video_id=%s AND "
                "local_label=%s", members)
        else:
            cur.executemany(
                "INSERT INTO speaker_ignore (video_id, local_label, reason) "
                "VALUES (%s,%s,%s) "
                "ON CONFLICT (video_id, local_label) DO UPDATE SET "
                "reason=EXCLUDED.reason, at=now()",
                [(v, l, reason) for v, l in members])
    con.commit()
    return {"ignored": 0 if undo else len(members), "restored":
            len(members) if undo else 0}


# ---------------------------------------------------------------- details
def group_detail(con, cluster=None, name=None, sample_per_voice=2):
    """Every member voice of a cluster or identity, with playable samples.

    The old UI showed three samples for a whole group, which is not enough to
    tell whether the group is one person. This returns every constituent voice
    so a mixed group can be seen and split.
    """
    if name is not None:
        members = con.execute("""
            SELECT si.video_id, si.local_label, si.cluster, si.confidence,
                   (sl.name IS NOT NULL) AS human,
                   v.title, v.upload_date, v.kind
            FROM speaker_identity si
            JOIN videos v ON v.id = si.video_id
            LEFT JOIN speaker_label sl
                   ON sl.video_id = si.video_id
                  AND sl.local_label = si.local_label
            WHERE si.name = %s
            ORDER BY v.upload_date DESC""", (name,)).fetchall()
    else:
        members = con.execute("""
            SELECT si.video_id, si.local_label, si.cluster, si.confidence,
                   (sl.name IS NOT NULL) AS human,
                   si.name, v.title, v.upload_date, v.kind
            FROM speaker_identity si
            JOIN videos v ON v.id = si.video_id
            LEFT JOIN speaker_label sl
                   ON sl.video_id = si.video_id
                  AND sl.local_label = si.local_label
            WHERE si.cluster = %s
            ORDER BY v.upload_date DESC""", (cluster,)).fetchall()

    out = []
    for m in members:
        d = dict(m)
        d["samples"] = [dict(s) for s in con.execute(f"""
            SELECT start, "end", text FROM utterances
            WHERE video_id = %s AND local_label = %s AND {SUBSTANTIVE}
            ORDER BY LENGTH(text) DESC LIMIT %s""",
            (m["video_id"], m["local_label"], sample_per_voice)).fetchall()]
        d["lines"] = con.execute(
            "SELECT COUNT(*) FROM utterances WHERE video_id=%s AND cluster=%s",
            (m["video_id"], m["cluster"])).fetchone()[0]
        out.append(d)
    return out


def voice_samples(con, video_id, local_label, limit=40):
    """Every line of ONE diarization speaker in one meeting.

    Filtered by local_label, not by cluster: in a small number of meetings two
    diarization speakers land in the same cluster, and selecting by cluster
    silently merges two people's lines into one voice's sample list.
    """
    return [dict(r) for r in con.execute("""
        SELECT start, "end", text FROM utterances
        WHERE video_id = %s AND local_label = %s
        ORDER BY start LIMIT %s""", (video_id, local_label, limit))]


# ------------------------------------------------------------------ writes
def apply_label(con, members, name, note=None):
    """Assign `name` to the given [(video_id, local_label), ...].

    This one call covers assign, split and merge - the only difference is
    which voices the caller selected. An empty name clears the label.
    """
    with con.cursor() as cur:
        if name:
            cur.executemany(
                "INSERT INTO speaker_label (video_id, local_label, name, note) "
                "VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (video_id, local_label) DO UPDATE SET "
                "name=EXCLUDED.name, note=EXCLUDED.note, labeled_at=now()",
                [(v, l, name, note) for v, l in members])
            cur.executemany(
                "UPDATE speaker_identity SET name=%s, confidence=1.0 "
                "WHERE video_id=%s AND local_label=%s",
                [(name, v, l) for v, l in members])
        else:
            cur.executemany(
                "DELETE FROM speaker_label WHERE video_id=%s AND "
                "local_label=%s", members)
            cur.executemany(
                "UPDATE speaker_identity SET name=NULL, confidence=NULL "
                "WHERE video_id=%s AND local_label=%s", members)
    con.commit()
    return {"name": name, "voices": len(members)}


def rename(con, old, new):
    con.execute("UPDATE speaker_label SET name=%s WHERE name=%s", (new, old))
    con.execute("UPDATE speaker_identity SET name=%s WHERE name=%s", (new, old))
    con.commit()
    return {"from": old, "to": new}


# ---------------------------------------------------------------- the record
#
# An agenda item is the unit a county meeting is actually organised around, and
# until now it existed only in the database. These two views make it navigable:
# an item shows what the county published, what the minutes recorded, and what
# was said; a case follows one application across every meeting that touched it
# - which for a rezoning is routinely a Planning Commission hearing, a BCC
# transmittal and a BCC adoption, months apart.

def item_detail(con, item_id):
    """SUPERSEDED and no longer routed. See archive.item().

    `/api/item/<id>` now serves the rebuilt shape. This is kept for its SQL,
    which archive.py drew on, and must not be wired back up: it returns a
    denormalised `passages.speaker` string where the rebuild returns speaker
    identity as fields (R6.2.1, D3). Delete with web/*.html.

    One agenda item: the published record, the minutes, the transcript.
    """
    r = con.execute("""
        SELECT ai.*, m.date, m.body, m.title AS meeting_title
        FROM agenda_items ai JOIN meetings m ON m.id = ai.meeting_id
        WHERE ai.id = %s""", (item_id,)).fetchone()
    if not r:
        return None
    item = dict(r)

    # Where in the recordings this item was taken up. `part` is non-zero when
    # an item was interrupted by a session break and resumed after it.
    item["spans"] = [dict(x) for x in con.execute("""
        SELECT sp.video_id, sp.part, sp.start, sp."end", sp.start_idx,
               sp.end_idx, v.title AS video_title, v.session_seq
        FROM item_spans sp JOIN videos v ON v.id = sp.video_id
        WHERE sp.agenda_item_id = %s
        ORDER BY v.session_seq NULLS FIRST, sp.start""", (item_id,))]

    item["passages"] = [dict(x) for x in con.execute("""
        SELECT p.id, p.video_id, p.start, p."end", p.speaker, p.text, p.phase
        FROM passages p WHERE p.agenda_item_id = %s
        ORDER BY p.video_id, p.start LIMIT 400""", (item_id,))]

    # Same case, other meetings - the thread this item sits in.
    item["related"] = [dict(x) for x in con.execute("""
        SELECT ai.id, ai.code, ai.title, ai.outcome, m.date, m.body
        FROM agenda_items ai JOIN meetings m ON m.id = ai.meeting_id
        WHERE ai.case_id = %s AND ai.id <> %s
        ORDER BY m.date""", (item["case_id"], item_id))] if item["case_id"] else []
    return item


def case_detail(con, case_id):
    """SUPERSEDED and no longer routed. See archive.case().

    One application, every meeting that took it up, in order.
    """
    rows = [dict(x) for x in con.execute("""
        SELECT ai.id, ai.code, ai.title, ai.section, ai.phase, ai.department,
               ai.recommendation, ai.disposition, ai.outcome, ai.source,
               m.id AS meeting_id, m.date, m.body,
               EXISTS (SELECT 1 FROM item_spans sp
                       WHERE sp.agenda_item_id = ai.id) AS has_recording
        FROM agenda_items ai JOIN meetings m ON m.id = ai.meeting_id
        WHERE ai.case_id = %s
        ORDER BY m.date, ai.seq""", (case_id,))]
    if not rows:
        return None
    c = con.execute("SELECT * FROM cases WHERE id = %s", (case_id,)).fetchone()
    return {"case_id": case_id, "case": dict(c) if c else None,
            "items": rows,
            "bodies": sorted({r["body"] for r in rows}),
            "first": rows[0]["date"], "last": rows[-1]["date"]}


def meeting_agenda(con, meeting_id):
    """A meeting's agenda in order, with what each item was disposed as."""
    m = con.execute("SELECT * FROM meetings WHERE id = %s",
                    (meeting_id,)).fetchone()
    if not m:
        return None
    items = [dict(x) for x in con.execute("""
        SELECT ai.id, ai.seq, ai.code, ai.title, ai.section, ai.phase,
               ai.case_id, ai.outcome, ai.source,
               (SELECT sp.video_id FROM item_spans sp
                WHERE sp.agenda_item_id = ai.id LIMIT 1) AS video_id,
               (SELECT MIN(sp.start) FROM item_spans sp
                WHERE sp.agenda_item_id = ai.id) AS start
        FROM agenda_items ai WHERE ai.meeting_id = %s
        ORDER BY ai.seq""", (meeting_id,))]
    return {"meeting": dict(m), "items": items,
            "videos": [dict(x) for x in con.execute(
                "SELECT id, title, duration, session_seq FROM videos "
                "WHERE meeting_id = %s ORDER BY session_seq NULLS FIRST",
                (meeting_id,))]}
