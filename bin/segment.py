"""Phase and agenda-item segmentation for meeting transcripts.

Why this exists: a vote reads "All in favor say aye. Aye. Any opposed, nay."
It contains no topic words at all, so BM25 has nothing to match and its
embedding sits beside every other vote in the archive rather than beside its
subject. The moment the board actually DECIDES something is therefore
unreachable by search - which is exactly what "what was decided about X"
questions need. No amount of better reading fixes that; the reader never sees
the passage.

Segmentation fixes it structurally. Every utterance gets the agenda item it
belongs to, and a vote inherits its item's subject.

ONE CALL PER MEETING-DAY. Agenda structure is a global property, so the model
is shown the whole day at once rather than a sliding window: the largest day in
the archive renders to ~73k tokens of outline and the model accepts ~194k
(measured, not assumed). Windowing bought nothing and cost the thing that
matters - a model that cannot see the whole agenda has to be told what item it
is in the middle of, and guesses at boundaries near the seam.

The day, not the video, is the unit because roughly half of these meetings run
as a morning and an afternoon session on one continuous agenda. The afternoon
recording opens mid-item with no announcement of what the item is; only the
morning says. Sessions are joined only when their order is unambiguous - see
day_groups().

THE PUBLISHED AGENDA IS PART OF THE PROMPT. It was not, for a long time, and
the model was asked to recover "R-58" from ASR output of somebody saying "R
fifty eight" while the exact string sat in `agenda_items` - landed before this
stage runs. It now returns `code` alongside the title, checked against that
day's real codes.

The two fields answer different questions and are grounded differently:
`title` is what was SAID and is verified against the transcript; `code` is
which county item it WAS and is verified against the published list. Reading a
code out of the title with a regex conflated them.

Division of labour, the same one that makes name_speakers.py safe:

  CODE  assembles the day, maps returned line numbers back to utterances,
        forces spans to be monotonic and to cover the day, splits items across
        the session break, VERIFIES that a title's words were actually spoken
        inside the span it names, and VERIFIES that a returned code exists on
        that day's agenda.
  LLM   reads the agenda's shape and says where items begin, what they are,
        and which published item each one is.

Verification is load-bearing here in a way it is not elsewhere: titles are
injected into the search index, so a single hallucinated subject would create
false hits for that subject across the whole archive. Words that were never
spoken are struck from the indexed form of the title - the display title stays
natural, but nothing invented can enter the index.

No regex vocabulary matching. It was tried: "commissioner reports" appears in
1 of 187 meetings. These transcripts have no reliable announcement phrasing,
and afternoon sessions have no announcement at all.
"""
import argparse
import concurrent.futures as cf
import itertools
import json
import os
import pathlib
import re
import sys

import ask                     # reuse the chat() client and its cache accounting
import db
import threads

OUTLINE_WORDS = 60    # per-line cap; above the 31-word mean, so it only clips
                      # long monologues, whose openings carry the subject anyway
TOKEN_BUDGET = 150_000
MAX_WORKERS = 12      # a call is minutes of model reasoning, so throughput is
                      # entirely a matter of running days side by side

PHASES = {
    "call_to_order", "proclamation", "public_comment", "consent", "regular",
    "public_hearing", "staff_report", "board_reports", "recess", "adjourn",
    "other",
}

SYS = """You segment transcripts of county government meetings into their
agenda structure.

You get numbered transcript lines: LINE, TIME, SPEAKER, text. One meeting day
may be recorded as several sessions (morning, afternoon); they run one
continuous agenda and the line numbers run straight through them.

Return the points where a NEW agenda item or meeting phase begins.

You are also given THE COUNTY'S PUBLISHED AGENDA for that day: the real item
codes and titles, in the order they were printed. Use it. Deciding which
published item a stretch of talk IS, is a lookup against that list - not
something to reconstruct from the sound of a code being read aloud.

Return JSON:
{"segments": [{"line": <line number where it begins>,
               "phase": "<exactly one of the phases below>",
               "code": "<the published code from the agenda, or null>",
               "title": "<what this item is ABOUT, 3-10 words>"}]}

PHASES - use these strings exactly:
  call_to_order    gavel, roll call, pledge, invocation, approval of minutes
  proclamation     awards, recognitions, proclamations, ceremonial presentations
  public_comment   the general public-comment period (not comment on one item)
  consent          the consent agenda: block approval, and items pulled from it
  regular          a regular-agenda item: staff presentation, discussion, vote
  public_hearing   a noticed hearing - rezoning, ordinance, comp plan amendment
  staff_report     county administrator, county attorney, department updates
  board_reports    individual commissioners' own reports and requests
  recess           break, lunch, moving to another session
  adjourn          closing the meeting
  other            fits none of the above

CODE - which published item this is. SET IT whenever the segment is one of the
items on the list. Most public hearings, regular items and consent blocks are,
so most segments should carry a code.
- Match on SUBJECT, not on wording. The agenda is written in legal prose and
  nobody speaks that way: "Conditional Use Request - Steel Residential
  Helicopter Landing Pad" and a board discussing "the helicopter pad" are the
  same item. Match them.
- Copy the code EXACTLY as the agenda spells it. Never transcribe it from
  speech - "R fifty eight" is "R-58" if that is how the agenda writes it.
- Use null only for the two real cases: the segment is not a published item
  (call to order, public comment, a recess, an off-agenda discussion, the
  adjournment), or two published items are so alike you cannot tell which was
  being discussed.
- The agenda is the county's plan, not a record of what happened. Items get
  continued, pulled, taken out of order, or never reached, and the list is not
  a running order. Match what the transcript shows.

TITLES are what make this useful. Rules:
- Say what the item is ABOUT, in the words the speakers themselves use:
  "school zone speed cameras and ticketing", not "Item R-58".
- Never put a word in a title that was not spoken in that span. This includes
  words from the published agenda: its titles are legal prose nobody said out
  loud, and the code field is where that identification belongs.
- If you cannot tell what an item is about, title it honestly from what WAS
  said.
- Use only these lines. No outside knowledge about Pasco County or its staff.

BOUNDARIES:
- A boundary is where the SUBJECT changes, not where the speaker changes.
- A motion, second and vote belong to the item being decided. Never start a
  new segment at a vote.
- An item interrupted by a recess and resumed in the next session is ONE
  segment spanning the break, not two.
- MOST PUBLISHED ITEMS ARE NEVER DISCUSSED. About half of them are consent,
  approved as one block in a single motion with no debate, and a day with 191
  published items typically has 20-40 segments. The consent block is ONE
  segment, not one per item in it. Do not manufacture a segment for an item
  just because it is on the list.
- Do not split every exchange. A staff presentation, the board's questions and
  the vote are one item, not three.
- A NOTICE RECITAL IS NOT THE ITEM BEING TAKEN UP. "Item P-112 was published in
  the Tampa Times on November third" is the statutory advertising declaration,
  and these are read for several items in a row long before any of them is
  heard. Do not start a segment at one and do not give one a code. The item is
  taken up where the board turns to it - "we are now going to P-116", a staff
  presentation, a speaker called to the podium.
- Cover the whole day. The last segment runs to the final line."""


def hms(t):
    return f"{int(t // 3600)}:{int(t // 60) % 60:02d}:{int(t % 60):02d}"


def load(con, video_id):
    """Utterances with resolved speaker names - who is talking is a strong cue
    for where staff presentations end and the board takes an item up."""
    return con.execute("""
        SELECT u.idx, u.start, u.end, u.text,
               COALESCE(cn.name, u.speaker) AS speaker
        FROM utterances u
        LEFT JOIN voice_name cn
               ON cn.video_id = u.video_id AND cn.cluster = u.cluster
        WHERE u.video_id = %s ORDER BY u.idx""", (video_id,)).fetchall()


AGENDA_TITLE_WORDS = 14   # a zoning title runs past 60 words of legal prose;
                          # the opening clause is what identifies it


def agenda_for(con, meeting_id):
    """The county's published agenda for a day, as the model should see it.

    This was the missing input. The model was asked to recover "R-58" from ASR
    output of somebody saying "R fifty eight", when the exact string is on
    disk. Measured before adding it: 15% of published items in recorded
    meetings were located in the recording at all, and only 47% of segments in
    meetings WITH an agenda carried a code that matched a real item - though
    when a code was emitted it was right 94% of the time. The model was not
    guessing badly, it was guessing at all.

    Titles are clipped because they are legal prose - "An Ordinance By The
    Pasco County Board Of County Commissioners Amending The Pasco County
    Comprehensive Plan..." - and the identifying part is the front of it.
    Sending 191 of those in full would cost more prompt than the transcript.
    """
    if meeting_id is None:
        return "", frozenset()
    rows = con.execute("""
        SELECT code, phase, section, title FROM agenda_items
         WHERE meeting_id = %s AND source = 'agenda' AND code IS NOT NULL
         ORDER BY seq""", (meeting_id,)).fetchall()
    if not rows:
        return "", frozenset()
    out = []
    for r in rows:
        title = " ".join((r["title"] or "").split()[:AGENDA_TITLE_WORDS])
        out.append(f"  {r['code']:<6} {(r['phase'] or ''):<14} {title}")
    return ("PUBLISHED AGENDA for this day, in printed order "
            f"({len(rows)} items):\n" + "\n".join(out) + "\n\n",
            frozenset(r["code"] for r in rows))


SESSIONS = ("morning", "afternoon", "evening")


def session_rank(title):
    t = (title or "").lower()
    for i, w in enumerate(SESSIONS):
        if f"{w} session" in t:
            return i
    return None


def day_groups(vids):
    """Split meetings into groups that can be read as one continuous agenda.

    Only sessions whose order is certain get joined. A day may also carry a
    workshop or a budget hearing, which are separate meetings that happen to
    share a date, and occasionally two recordings claim the same session
    because a stream dropped and restarted - in both cases the order is a
    guess, and a wrong order would hand the model a scrambled agenda. Those
    are segmented on their own instead.
    """
    by_day = {}
    for v in vids:
        by_day.setdefault((v["upload_date"], v["kind"]), []).append(v)

    out = []
    for day in by_day.values():
        ranked = {}
        for v in day:
            ranked.setdefault(session_rank(v["title"]), []).append(v)
        ordered = {r: vs[0] for r, vs in ranked.items()
                   if r is not None and len(vs) == 1}
        alone = [v for r, vs in ranked.items() for v in vs
                 if r is None or len(vs) > 1]
        if len(ordered) > 1:
            out.append([ordered[r] for r in sorted(ordered)])
        else:
            alone += list(ordered.values())
        out += [[v] for v in alone]
    return out


def render(sessions, rows, words):
    """The day as numbered lines, plus the map back to real utterances.

    Line numbers run straight through the sessions so the model only ever
    returns one integer; putting the day back together is code's job.
    """
    lines, index = [], []
    for k, v in enumerate(sessions):
        if len(sessions) > 1:
            lines.append(f"===== SESSION {k + 1} of {len(sessions)}: "
                         f"{v['title']} =====")
        for r in rows[v["id"]]:
            w = r["text"].split()
            txt = " ".join(w[:words]) + (" ..." if len(w) > words else "")
            lines.append(f"{len(index)}\t{hms(r['start'])}\t"
                         f"{r['speaker'] or '?'}\t{txt}")
            index.append((v["id"], r))
    return "\n".join(lines), index


def fit(sessions, rows):
    """Render at the most detail that fits; only enormous days are trimmed."""
    for words in (OUTLINE_WORDS, 30, 16, 8):
        body, index = render(sessions, rows, words)
        if len(body) // 4 <= TOKEN_BUDGET:
            return body, index
    return body, index


def propose(sessions, body, n_lines, agenda=""):
    label = sessions[0]["title"]
    if len(sessions) > 1:
        label += f"  (+{len(sessions) - 1} more session"                        \
                 f"{'s' if len(sessions) > 2 else ''} the same day)"
    # The agenda goes FIRST and the transcript after it: the stable half of the
    # prompt in front gives the cache something to hit, and the model reads the
    # list before the thing it has to match against the list.
    user = (f"MEETING DAY: {label}  ({sessions[0]['upload_date']})\n\n"
            f"{agenda}"
            f"TRANSCRIPT, lines 0-{n_lines - 1}:\n{body}\n\n"
            f"Return every boundary in lines 0-{n_lines - 1}. "
            f"The first boundary MUST be line 0. "
            f"Set `code` only where you can point at the published item; "
            f"null everywhere else.")
    raw = ask.chat([{"role": "system", "content": SYS},
                    {"role": "user", "content": user}], as_json=True)
    if os.environ.get("SEGMENT_DEBUG"):
        pathlib.Path(os.environ["SEGMENT_DEBUG"]).write_text(raw or "")
    try:
        segs = json.loads(raw).get("segments", [])
    except (json.JSONDecodeError, AttributeError):
        return []
    out = []
    for s in segs:
        try:
            line = int(s["line"])
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 <= line < n_lines:
            continue
        phase = str(s.get("phase") or "other").strip().lower()
        # `code` is copied through explicitly. This dict is rebuilt from a
        # whitelist rather than passed along, which is the right instinct - the
        # model's output is untrusted input - but it silently drops any field
        # added to the schema later. `code` was parsed correctly, validated
        # correctly by assemble(), and thrown away here, one line before use.
        out.append({"line": line,
                    "phase": phase if phase in PHASES else "other",
                    "code": (str(s.get("code")).strip()
                             if s.get("code") else None),
                    "title": " ".join(str(s.get("title") or "").split())})
    return out


# Words that identify no subject. A title made only of these ("Public hearing",
# "Consent agenda item") gets a phase but never enters the search index - it
# would match every hearing in the archive.
GENERIC = {
    "item", "items", "meeting", "board", "county", "commission", "commissioner",
    "commissioners", "public", "hearing", "hearings", "discussion", "approval",
    "approve", "request", "regular", "agenda", "report", "reports", "update",
    "updates", "presentation", "order", "consent", "motion", "vote", "session",
    "comment", "comments", "staff", "general", "business", "other", "pasco",
    "continued", "continuance", "morning", "afternoon", "matters",
}
WORD = re.compile(r"[A-Za-z0-9']+")


def is_content(word):
    """Does this word carry subject identity, and so need to be verified?

    All-caps short tokens count: PDE, PD, MPUD, LDC are case prefixes, and
    dropping them turns "PDE 260033" into a bare number that matches nothing
    and joins to no thread.
    """
    w = word.lower()
    if w in GENERIC:
        return False
    return (len(w) >= 4 or (w.isdigit() and len(w) >= 2)
            or (word.isupper() and len(w) >= 2))


def spoken(word, hay):
    w = word.lower()
    return w in hay or (w.endswith("s") and w[:-1] in hay) or (w + "s") in hay


def ground(title, span_text):
    """Strike from the title any subject word that was not spoken in the span.

    Returns (search_title, coverage). Editing the title in place rather than
    rebuilding it from surviving tokens is what keeps "R-58" and "PDE 260033"
    intact - a rebuild yields "58" and "260033", which BM25 cannot match and
    threads.global_keys() cannot recognise as a case number.

    A title may stay abstractive and still be safe to index: the words that
    were never said are simply gone, so a hallucinated subject cannot become a
    searchable claim about the archive.
    """
    if not (title or "").strip():
        return None, 0.0
    hay = re.sub(r"[^a-z0-9' ]+", " ", threads.normalize_numbers(span_text).lower())
    total = kept = 0

    def check(m):
        nonlocal total, kept
        if not is_content(m.group(0)):
            return m.group(0)
        total += 1
        if spoken(m.group(0), hay):
            kept += 1
            return m.group(0)
        return ""

    out = " ".join(WORD.sub(check, title).split()).strip(" -,/:;")
    if not total:
        return None, 0.0        # a title of nothing but generic words
    # Two surviving subject words is the floor: one alone is as likely to be an
    # accident of vocabulary as a real subject.
    return (out if kept >= 2 else None), kept / total


def assemble(index, marks, valid_codes=frozenset()):
    """Turn proposed boundaries into validated, contiguous, titled spans.

    An item that runs across the session break becomes one row per recording -
    spans are per-video - but both rows carry the title, and the title is
    verified against the WHOLE item. That is the point of reading the day as a
    unit: the afternoon half of an item never says what the item is.
    """
    if not index:
        return []
    best = {}
    for m in marks:
        best.setdefault(m["line"], m)       # first proposal for a line wins
    if 0 not in best:
        # The day starts at line 0 whether or not the model said so.
        head = dict(best[min(best)]) if best else \
            {"phase": "other", "title": None}
        head["line"] = 0
        best[0] = head
    lines = sorted(best)

    segs, seq = [], {}
    for si, g0 in enumerate(lines):
        g1 = (lines[si + 1] - 1) if si + 1 < len(lines) else len(index) - 1
        if g1 < g0:
            continue
        span = index[g0:g1 + 1]
        m = best[g0]
        search_title, cov = ground(m["title"],
                                   " ".join(r["text"] for _, r in span))
        # The code is CHECKED, not trusted. It is only useful because it comes
        # from a list we hold, so a value that is not on that list is the one
        # thing it cannot be - a model paraphrasing a code it heard, which is
        # exactly the failure the agenda was added to remove.
        code = (m.get("code") or "").strip() or None
        if code and code not in valid_codes:
            code = None
        for part, (vid, chunk) in enumerate(
                itertools.groupby(span, key=lambda x: x[0])):
            rows = [r for _, r in chunk]
            segs.append({"video_id": vid, "seq": seq.get(vid, 0),
                         "start_idx": rows[0]["idx"], "end_idx": rows[-1]["idx"],
                         "start": rows[0]["start"], "end": rows[-1]["end"],
                         "phase": m["phase"], "title": m["title"] or None,
                         "code": code,
                         "search_title": search_title, "continued": part > 0,
                         "coverage": cov})
            seq[vid] = seq.get(vid, 0) + 1
    return segs


def segment_day(sessions, rows, agenda="", valid_codes=frozenset()):
    if os.environ.get("SEGMENT_DEBUG"):
        print(f"  [debug] agenda {len(agenda)} chars, "
              f"{len(valid_codes)} valid codes", file=sys.stderr, flush=True)
    body, index = fit(sessions, rows)
    marks = propose(sessions, body, len(index), agenda)
    # The failure mode of one long call is stopping partway: the model segments
    # the first hour and returns, leaving a single span over everything after
    # it. That looks like a valid answer, so it is checked for explicitly -
    # a real reading puts its last boundary near the end of the day.
    reach = (max(m["line"] for m in marks) / len(index)) if marks and index else 0
    if len(index) > 400 and reach < 0.5:
        return []
    segs = assemble(index, marks, valid_codes)
    # A day split into one span, or into hundreds, means the model did not read
    # it. Better to leave it unsegmented than to index nonsense.
    if len(segs) < 2 or len(segs) > max(4, len(index) // 3):
        return []
    return segs


def preflight(con):
    """Prove the write path works before spending a single LLM call.

    Segmentation had a syntax error in its INSERT - an unquoted `end` - and it
    cost a whole run to notice. Nothing looked wrong: each day printed its
    segment count and only then tried to store them, and because the raise
    happened inside `with ThreadPoolExecutor`, Python sat waiting for all 133
    outstanding calls to drain before surfacing it. An hour of API spend, a log
    full of success lines, and an empty table.

    So the statement is exercised against a real transaction that is then
    rolled back. It costs one round trip and fails in the first second.
    """
    # A real video id: `segments.video_id` is a foreign key, so a made-up one
    # would fail for a reason that has nothing to do with the statement.
    row = con.execute("SELECT id FROM videos LIMIT 1").fetchone()
    if row is None:
        return
    with con.transaction(force_rollback=True):
        write(con, [{"id": row[0]}],
              [{"video_id": row[0], "seq": 0, "start_idx": 0,
                "end_idx": 1, "start": 0.0, "end": 1.0, "phase": "other",
                "title": "t", "search_title": "t", "continued": False}],
              commit=False)


def write(con, sessions, segs, commit=True):
    cur = con.cursor()
    cur.executemany("DELETE FROM segments WHERE video_id=%s",
                    [(v["id"],) for v in sessions])
    cur.executemany(
        # `end` is a reserved word and MUST be quoted here. It parses unquoted
        # when qualified (`u.end` in load()), which is exactly why this went
        # unnoticed: every read worked and only the write was a syntax error.
        'INSERT INTO segments (video_id, seq, start_idx, end_idx, start, "end", '
        "phase, title, search_title, code, continued) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        [(s["video_id"], s["seq"], s["start_idx"], s["end_idx"], s["start"],
          s["end"], s["phase"], s["title"], s["search_title"],
          s.get("code"), s["continued"])
         for s in segs])
    if commit:
        con.commit()


def reground(con, write_it):
    """Recompute every stored search_title from its display title.

    The grounding rules are the part of this most likely to need tightening,
    and they are pure code over data already on disk. Re-deriving them costs
    nothing, where re-segmenting costs an LLM pass over the whole archive.
    """
    texts = {}
    for r in con.execute("SELECT video_id, idx, text FROM utterances"):
        texts.setdefault(r["video_id"], {})[r["idx"]] = r["text"]
    rows = con.execute("SELECT id, video_id, start_idx, end_idx, title, "
                       "search_title FROM segments").fetchall()
    changed = []
    for r in rows:
        span = texts.get(r["video_id"], {})
        body = " ".join(span.get(i, "")
                        for i in range(r["start_idx"], r["end_idx"] + 1))
        new, _ = ground(r["title"], body)
        if new != r["search_title"]:
            changed.append((new, r["id"]))
            print(f"  {r['search_title']!r}\n    -> {new!r}")
    print(f"\n{len(changed)}/{len(rows)} segment titles change")
    if write_it and changed:
        with con.cursor() as cur:
            cur.executemany("UPDATE segments SET search_title=%s WHERE id=%s",
                            changed)
        con.commit()
        print("written")
    elif changed:
        print("(dry run - pass --write to store)")


def show(segs):
    for s in segs:
        mark = "~" if not s["search_title"] else (">" if s["continued"] else " ")
        print(f"  {mark}{hms(s['start'])}  {s['phase']:<14} "
              f"{(s.get('code') or '-'):<6} "
              f"{(s['title'] or '')[:54]:<54} [{s['coverage']:.0%}]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", help="segment the meeting-day containing this id")
    ap.add_argument("--limit", type=int, default=0, help="0 = all pending")
    ap.add_argument("--redo", action="store_true", help="re-segment done ones")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--show", action="store_true", help="print the segments")
    ap.add_argument("--jobs", type=int, default=MAX_WORKERS)
    ap.add_argument("--reground", action="store_true",
                    help="re-verify stored titles without calling the model")
    args = ap.parse_args()

    con = db.connect()

    if args.reground:
        reground(con, args.write)
        return 0

    vids = [dict(r) for r in con.execute(
        "SELECT id, title, upload_date, kind, meeting_id "
        "FROM videos WHERE transcribed")]
    groups = day_groups(vids)
    if args.video:
        groups = [g for g in groups if any(v["id"] == args.video for v in g)]
    else:
        if not args.redo:
            done = {r[0] for r in con.execute(
                "SELECT DISTINCT video_id FROM segments")}
            groups = [g for g in groups if not all(v["id"] in done for v in g)]
        groups.sort(key=lambda g: (g[0]["upload_date"] or ""), reverse=True)
        if args.limit:
            groups = groups[:args.limit]
    groups = [g for g in groups if g]
    if not groups:
        print("nothing to segment")
        return 0

    if args.write:
        preflight(con)

    # SQLite handles are not shareable across threads, so every read happens
    # here and only the LLM calls are parallelised.
    rows = {v["id"]: load(con, v["id"]) for g in groups for v in g}
    # Read in the calling thread with everything else, for the same reason: the
    # connection is not shared across the pool.
    agendas = {tuple(v["id"] for v in g): agenda_for(con, g[0].get("meeting_id"))
               for g in groups}
    have = sum(1 for a, _ in agendas.values() if a)
    print(f"published agenda available for {have} of {len(groups)} meeting-days")
    con.commit()   # release the read snapshot before the LLM calls
    total = sum(len(r) for r in rows.values())
    print(f"segmenting {len(groups)} meeting-days / "
          f"{sum(len(g) for g in groups)} recordings ({total:,} utterances)\n",
          flush=True)

    ok = 0
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(segment_day, g, rows,
                          *agendas[tuple(v["id"] for v in g)]):
                tuple(v["id"] for v in g) for g in groups}
        index = {tuple(v["id"] for v in g): g for g in groups}
        for fut in cf.as_completed(futs):
            g = index[futs[fut]]
            tag = "+".join(v["id"] for v in g)
            try:
                segs = fut.result()
            except Exception as e:
                print(f"  FAIL   {tag}  {type(e).__name__}: {e}", flush=True)
                continue
            if not segs:
                print(f"  reject {tag}  {g[0]['title'][:52]}", flush=True)
                continue
            usable = sum(1 for s in segs if s["search_title"])
            coded = sum(1 for s in segs if s.get("code"))
            print(f"  {tag[:28]:<28} {len(segs):>3} segments, {usable:>3} "
                  f"indexable, {coded:>3} matched to a published item   "
                  f"{g[0]['title'][:34]}", flush=True)
            if args.show:
                show(segs)
            if args.write:
                try:
                    write(con, g, segs)
                except Exception as e:
                    # Do not let the pool drain first: every pending future is
                    # a paid call whose result is about to be thrown away.
                    print(f"  WRITE FAILED {tag}: {type(e).__name__}: {e}\n"
                          f"  cancelling {sum(not f.done() for f in futs)} "
                          f"pending calls", flush=True)
                    for f in futs:
                        f.cancel()
                    raise
            ok += 1

    print(f"\n{ok}/{len(groups)} meeting-days segmented · {ask.usage_report()}")
    if not args.write:
        print("(dry run - pass --write to store)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
