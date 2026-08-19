#!/usr/bin/env python3
"""Propose, apply and revert redactions of members of the public's addresses.

1. ADDRESSES THE COUNTY PUBLISHED ARE PROTECTED BY CONSTRUCTION. Anything
   appearing in an agenda item title or a segment title is the matter under
   discussion - 1,411 item titles carry one - and the detector may not touch
   it, whatever the model thinks.
2. NOTHING IS REDACTED WITHOUT A PERSON ACCEPTING IT. The pass writes
   proposals. A detector that altered the transcript of a public meeting on
   its own judgement is not a thing this archive should own."""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import ask as llm                                            # noqa: E402
import db                                                    # noqa: E402
import index_passages                                        # noqa: E402

MARKER = "[address removed]"
DEVICE = os.environ.get("PASCO_EMBED_DEVICE") or "cuda:1"

SUFFIX = (r"(street|st|road|rd|avenue|ave|drive|dr|lane|ln|boulevard|blvd"
          r"|court|ct|circle|cir|way|trail|trl|place|pl|highway|hwy"
          r"|terrace|ter|loop)")
# House number, up to four words, a street suffix. Loose on purpose: this is
# the CANDIDATE pass, and its job is to miss nothing that a person would
# recognise as a street address. The model decides what each one IS; a person
# decides whether it goes.
ADDRESS = re.compile(rf"\b\d{{2,6}}\s+([A-Za-z0-9'.\-]+\s+){{0,4}}{SUFFIX}\b",
                     re.I)
# The same shape for Postgres, which speaks POSIX ARE: `\b` there is a
# BACKSPACE, not a word boundary, so the Python pattern handed to `~*`
# silently matches nothing at all. `\m` and `\M` are the word edges.
ADDRESS_SQL = (rf"\m\d{{2,6}}\s+([A-Za-z0-9'.-]+\s+){{0,4}}{SUFFIX}\M")

# THE HOUSE NUMBER IS USUALLY NOT A NUMBER.
#
# This is a machine transcript of people speaking, and the ASR writes what it
# hears: "I reside at one four three eight two Ashmont Drive". A digits-only
# pattern finds none of those, and they are not an edge case - 1,480 lines
# carry a spelled-out number in front of a street name, and 45% of every line
# containing "I live at" had no digit-address in it at all. Recall is capped
# by CANDIDATE GENERATION, because the model only ever adjudicates what the
# regex hands it: a miss here is a miss that nothing downstream can rescue.
NUMWORD = (r"(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
           r"|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen"
           r"|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred"
           r"|thousand|zero|oh)")
SPOKEN_SQL = (rf"\m{NUMWORD}(\s+{NUMWORD}){{1,6}}\s+"
              rf"([A-Za-z0-9'.-]+\s+){{0,3}}{SUFFIX}\M")
# And the phrase itself, for the ones neither pattern reaches - "I live on the
# corner of Foxfire and Colony". The model decides; this only gets it looked
# at, which is the whole job of this stage.
SELF_SQL = (r"\m(my (home |business )?address is|i live at|i reside at"
            r"|residing at)\M")

SYS = """You read transcripts of Pasco County government meetings and decide
whether a street address in one line is the HOME ADDRESS OF A MEMBER OF THE
PUBLIC, which must be removed, or something else, which must be kept.

REMOVE only an address that identifies where a private individual lives.
Typically they state it about themselves at the podium ("My name is X, 1234
Elm Street"), or a clerk reads it from a comment they submitted.

KEEP everything else. In particular KEEP:
- the property, site, parcel or road that the meeting is discussing, however
  it is phrased ("access to the site is from Clinton Avenue");
- a business or firm address given by someone appearing in a professional
  capacity - an attorney, engineer, planner, consultant or applicant's agent
  ("Vance Kirby with Halverson Engineering, 12363 Thornbury Boulevard");
- a public building, county facility, school or park;
- an address of a company, church or organisation rather than a person.

If a speaker is a private resident objecting to a development NEXT DOOR, the
address of their own home is still a home address: REMOVE it. The development
itself is the matter: KEEP that.

This is a machine transcript of speech, so a house number is often SPELLED
OUT: "I reside at one four three eight two Ashmont Drive" is the address
14382 Ashmont Drive. Include the spoken digits in the span.

Return JSON: {"results": [{"n": <number>, "span": <exact text to remove, or
null>}]}. `span` must be copied CHARACTER FOR CHARACTER from the line, and
must contain only the address itself - not the speaker's name, not the
surrounding sentence.

If a line plainly identifies where someone lives but you cannot isolate a
span, return null and it will be looked at by a person. Prefer null over a
guessed span; do NOT prefer null over an address you can see."""


SECTION_SYS = """You read one section of a Pasco County government meeting
transcript and find every place a MEMBER OF THE PUBLIC'S HOME ADDRESS is
spoken, so it can be removed before the archive publishes it.

Public hearings and public comment work the same way: each speaker steps up,
says their name, and states where they live. That address is what must be
removed.

REMOVE an address that identifies where a private individual lives - their
own, or a neighbour's or relative's that they name.

KEEP, and never return:
- the property, site, parcel or road the meeting is deciding about, however
  it is described. Staff and the applicant describe it constantly, and it is
  the subject of the record;
- a business or firm address from someone appearing in a professional
  capacity - an attorney, engineer, planner, consultant or the applicant's
  agent. You can usually tell from how they introduce themselves;
- a public building, county facility, school or park;
- a road named as a direction or a boundary ("the corner of 54 and Starkey").

You have the whole section, so use it. If a speaker introduced themselves as
representing a firm, their address is a business address. If an address was
named earlier as the property under application, it is the subject and stays.
A resident objecting to a development next door still has their own home
address removed, while the development itself stays.

This is a machine transcript of speech, so a house number is usually SPELLED
OUT: "I reside at one four three eight two Ashmont Drive" is an address.
Include the spoken digits, and any spoken ZIP that runs on from it.

Lines are given as [idx] speaker: text. Return JSON:
{"redactions": [{"idx": <the line's idx>, "span": <exact text to remove>}]}

`span` must be copied CHARACTER FOR CHARACTER out of that line, and hold only
the address - not the speaker's name, not the rest of the sentence. Return
every address you find; a section often has several, one per speaker. Return
an empty list if the section has none."""

# One call per section. The average section is ~3,300 tokens, which is a
# comfortable single call; only a handful run long enough to need splitting.
SECTION_CHARS = 60_000
SECTION_PHASES = ("public_hearing", "public_comment")


def sections(con, phases=SECTION_PHASES, limit=None, video=None):
    """Every public-hearing and public-comment section, with its lines."""
    sql = """
        SELECT s.id, s.video_id, s.phase, s.start_idx, s.end_idx, s.title
          FROM segments s
         WHERE s.phase = ANY(%s)
    """
    args = [list(phases)]
    if video:
        sql += " AND s.video_id = %s"
        args.append(video)
    sql += " ORDER BY s.video_id, s.start_idx"
    out = []
    for r in con.execute(sql, tuple(args)).fetchall():
        lines = con.execute("""
            SELECT u.idx, u.text, us.name
              FROM utterances u
              LEFT JOIN utterance_speaker us
                     ON us.video_id = u.video_id AND us.idx = u.idx
             WHERE u.video_id = %s AND u.idx BETWEEN %s AND %s
             ORDER BY u.idx
        """, (r[1], r[3], r[4])).fetchall()
        if not lines:
            continue
        out.append({"id": r[0], "video_id": r[1], "phase": r[2],
                    "title": r[5],
                    "lines": [{"idx": x[0], "text": x[1], "name": x[2]}
                              for x in lines]})
        if limit and len(out) >= limit:
            break
    return out


def _chunks(lines, budget=SECTION_CHARS):
    """Split a long section on line boundaries. Four sections in the archive
    are big enough to need it; the rest go in one call."""
    out, cur, n = [], [], 0
    for ln in lines:
        if cur and n + len(ln["text"]) > budget:
            out.append(cur)
            cur, n = [], 0
        cur.append(ln)
        n += len(ln["text"])
    if cur:
        out.append(cur)
    return out


def adjudicate_section(lines, model=None):
    """One model call over a whole section. Returns [(idx, span), ...]."""
    body = "\n".join(
        f"[{ln['idx']}] {ln['name'] or 'speaker'}: {ln['text']}"
        for ln in lines)
    raw = llm.chat([{"role": "system", "content": SECTION_SYS},
                    {"role": "user", "content": body}],
                   as_json=True, temperature=0,
                   **({"model": model} if model else {}))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out = []
    for r in data.get("redactions") or []:
        try:
            out.append((int(r["idx"]), r["span"]))
        except (TypeError, ValueError, KeyError):
            continue
    return out


def verify(found, lines):
    """Keep only spans that are really in the line they were attributed to."""
    by_idx = {ln["idx"]: ln["text"] for ln in lines}
    kept, moved, dropped = [], 0, 0
    for idx, span in found:
        if not span or not span.strip():
            dropped += 1
            continue
        if idx in by_idx and span in by_idx[idx]:
            kept.append((idx, span))
            continue
        hits = [i for i, t in by_idx.items() if span in t]
        if len(hits) == 1:
            kept.append((hits[0], span))
            moved += 1
        else:
            dropped += 1
    return kept, moved, dropped


def protected(con):
    """Addresses the county itself published, which are the matter and are
    never redacted. Normalised to bare lowercase alphanumerics so that "1234
    Elm St." and "1234 elm street" are the same protected thing."""
    out = set()
    for q in ("SELECT title FROM agenda_items WHERE title IS NOT NULL",
              "SELECT title FROM segments WHERE title IS NOT NULL"):
        # r[0], never `for (title,) in ...`: db.Row is a Mapping, so
        # unpacking a row yields its COLUMN NAMES. This function
        # silently protected nothing at all until that was fixed, which is
        # the worst way for a guard to fail - it fails open.
        for r in con.execute(q).fetchall():
            for m in ADDRESS.finditer(r[0]):
                out.add(norm(m.group(0)))
    return out


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def candidates(con, limit=None, video=None):
    """Utterances carrying something shaped like a street address, minus the
    ones already proposed or applied, minus anything the county published."""
    keep_out = protected(con)
    sql = """
        SELECT u.video_id, u.idx, u.text
        FROM utterances u
        WHERE (u.text ~* %s OR u.text ~* %s OR u.text ~* %s)
          AND NOT EXISTS (SELECT 1 FROM redaction r
                          WHERE r.video_id = u.video_id AND r.idx = u.idx)
    """
    args = [ADDRESS_SQL, SPOKEN_SQL, SELF_SQL]
    if video:
        sql += " AND u.video_id = %s"
        args.append(video)
    sql += " ORDER BY u.video_id, u.idx"
    rows = con.execute(sql, tuple(args)).fetchall()

    out = []
    for r in rows:                                    # positional: a db.Row unpacks to column names
        video_id, idx, text = r[0], r[1], r[2]
        found = [m.group(0) for m in ADDRESS.finditer(text)]
        if found and all(norm(f) in keep_out for f in found):
            continue          # every address in the line is the matter itself
        out.append({"video_id": video_id, "idx": idx, "text": text})
        if limit and len(out) >= limit:
            break
    return out, keep_out


def context(con, video_id, idx):
    """The line before, and what the meeting was doing here. A staff
    presentation and a public comment read very differently, and the phase is
    the cheapest way to tell the model which it is looking at."""
    prev = con.execute(
        "SELECT text FROM utterances WHERE video_id = %s AND idx = %s",
        (video_id, idx - 1)).fetchone()
    ph = con.execute("""
        SELECT phase FROM passages
        WHERE video_id = %s AND start_idx <= %s AND end_idx >= %s LIMIT 1
    """, (video_id, idx, idx)).fetchone()
    return (prev[0] if prev else None), (ph[0] if ph else None)


def adjudicate(con, batch, model=None, ctx=None):
    """One model call for a batch of lines. Returns {n: span or None}.

    `ctx` is the per-line (previous line, phase) pairs, read on the caller's
    thread when this runs in a pool — `con` is then None and must not be
    touched, because one psycopg connection is not safe to share."""
    lines = []
    ctx = ctx or [context(con, c["video_id"], c["idx"]) for c in batch]
    for n, c in enumerate(batch, 1):
        prev, phase = ctx[n - 1]
        lines.append(
            f"--- line {n}"
            + (f" (during: {phase.replace('_', ' ')})" if phase else "")
            + " ---\n"
            + (f"previous speaker said: {prev[:200]}\n" if prev else "")
            + f"LINE: {c['text'][:1200]}")
    msg = [{"role": "system", "content": SYS},
           {"role": "user", "content": "\n\n".join(lines)}]
    raw = llm.chat(msg, as_json=True, temperature=0,
                   **({"model": model} if model else {}))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    out = {}
    for r in data.get("results") or []:
        try:
            out[int(r.get("n"))] = r.get("span")
        except (TypeError, ValueError):
            continue
    return out


def propose(con, limit=None, video=None, batch_size=8, write=False,
            model=None, jobs=8):
    """The pattern pass. Parallel like every other LLM loop in this repo:
    this was the one that was not, and 200 serial calls is an hour of waiting
    for work that finishes in seven minutes. Context is read on this thread
    before the pool starts, and results are written on it after each batch
    lands - one connection is not safe to share across threads."""
    import concurrent.futures as cf

    cands, keep_out = candidates(con, limit=limit, video=video)
    print(f"{len(cands)} candidate lines · {jobs} calls at a time")
    made = skipped_verbatim = skipped_protected = kept = 0

    batches = [cands[i:i + batch_size]
               for i in range(0, len(cands), batch_size)]
    # Every database read the prompts need, taken up front.
    prepared = [(b, [context(con, c["video_id"], c["idx"]) for c in b])
                for b in batches]
    done = 0
    with cf.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(adjudicate, None, b, model, ctx): b
                   for b, ctx in prepared}
        for fut in cf.as_completed(futures):
            batch = futures[fut]
            done += 1
            try:
                verdicts = fut.result()
            except Exception as e:                            # noqa: BLE001
                print(f"  a batch failed: {type(e).__name__}: {e}",
                      file=sys.stderr)
                continue
            for n, c in enumerate(batch, 1):
                span = verdicts.get(n)
                if not span:
                    kept += 1
                    continue
                # Verbatim or nothing. A span the model reworded is a span it
                # did not actually read, and applying it would cut the wrong
                # text.
                if span not in c["text"]:
                    skipped_verbatim += 1
                    continue
                if norm(span) in keep_out:
                    skipped_protected += 1
                    continue
                made += 1
                if write:
                    store(con, c["video_id"], c["idx"], span,
                          author="redact.py")
            if write:
                con.commit()
            if done % 5 == 0 or done == len(batches):
                print(f"  {done}/{len(batches)} batches  proposed {made}",
                      flush=True)

    print(f"\nproposed {made}"
          f"  ·  kept {kept}"
          f"  ·  dropped {skipped_verbatim} not verbatim"
          f"  ·  dropped {skipped_protected} in the published record")
    if not write:
        print("(dry run - nothing written; pass --write)")
    return made


def propose_sections(con, limit=None, video=None, write=False, model=None,
                     jobs=8, passes=1):
    """The section pass. Model calls run in parallel; the database is only
    touched before and after, on this thread - one connection shared across
    threads is not a thing psycopg promises to survive.

    IT SATURATES AT TWO. Six more proposals for a person to read buys the
    last four addresses; a third pass adds nothing and a fourth adds only
    review. That trade is the right way round here and only here: NOTHING IS
    APPLIED WITHOUT A PERSON ACCEPTING IT, so a miss is the harm this exists
    to prevent while a wrong span costs one glance."""
    import concurrent.futures as cf

    passes = max(1, passes)
    secs = sections(con, limit=limit, video=video)
    once = [(s, ch) for s in secs for ch in _chunks(s["lines"])]
    work = once * passes
    print(f"{len(secs):,} sections · {len(once):,} calls"
          + (f" × {passes} passes = {len(work):,}" if passes > 1 else "")
          + f" · {jobs} at a time")
    if not work:
        return 0

    done = made = moved = dropped = 0
    results = set()
    with cf.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(adjudicate_section, ch, model): (s, ch)
                   for s, ch in work}
        for fut in cf.as_completed(futures):
            s, ch = futures[fut]
            done += 1
            try:
                found = fut.result()
            except Exception as e:                            # noqa: BLE001
                print(f"  {s['video_id']} #{s['id']}: "
                      f"{type(e).__name__}: {e}", file=sys.stderr)
                continue
            kept, mv, dr = verify(found, ch)
            moved += mv
            dropped += dr
            # Written HERE, on this thread, as each section lands. Buffering
            # two thousand calls to the end means an interrupted run - a
            # dropped connection, a rate limit, a laptop lid - throws away
            # every model call it already paid for.
            # `made` counts DISTINCT proposals, not model returns: with
            # passes > 1 the same address comes back every pass, and store()
            # rejects the repeat. Counting returns instead would report three
            # passes as three times the work.
            if write:
                for idx, span in kept:
                    if store(con, s["video_id"], idx, span,
                             author="redact.py:section"):
                        made += 1
                con.commit()
            else:
                results.update((s["video_id"], i, sp) for i, sp in kept)
                made = len(results)
            if done % 25 == 0 or done == len(work):
                print(f"  {done}/{len(work)}  found {made}", flush=True)
    print(f"\nfound {made}"
          f"  ·  {moved} re-attached to the right line"
          f"  ·  {dropped} dropped, not found verbatim")
    if not write:
        print("(dry run - nothing written; pass --write)")
    return made


def store(con, video_id, idx, span, author="redact.py", kind="residence",
          note=None):
    """One proposal, unless that exact span is already proposed or applied on
    that line. The two passes overlap by design and must not each queue the
    same address for a person to read twice."""
    dup = con.execute("""
        SELECT 1 FROM redaction
         WHERE video_id = %s AND idx = %s AND span = %s
           AND status IN ('proposed', 'applied')
    """, (video_id, idx, span)).fetchone()
    if dup:
        return False
    before = con.execute(
        "SELECT text FROM utterances WHERE video_id = %s AND idx = %s",
        (video_id, idx)).fetchone()
    if not before or span not in before[0]:
        return False
    con.execute("""
        INSERT INTO redaction (video_id, idx, span, before_text, kind,
                               author, note, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'proposed')
    """, (video_id, idx, span, before[0], kind, author, note))
    return True


def cross_check(con, write=False):
    """Where the two detectors disagree, inside the sections both can see."""
    rows = con.execute("""
        SELECT u.video_id, u.idx, left(u.text, 160) AS text, s.phase
          FROM utterances u
          JOIN segments s ON s.video_id = u.video_id
           AND u.idx BETWEEN s.start_idx AND s.end_idx
         WHERE s.phase = ANY(%s)
           AND (u.text ~* %s OR u.text ~* %s OR u.text ~* %s)
           AND NOT EXISTS (SELECT 1 FROM redaction r
                           WHERE r.video_id = u.video_id AND r.idx = u.idx)
         ORDER BY u.video_id, u.idx
    """, (list(SECTION_PHASES), ADDRESS_SQL, SPOKEN_SQL, SELF_SQL)).fetchall()
    print(f"{len(rows):,} lines the pattern pass flags inside a "
          f"hearing or comment section that the section pass left alone.")
    print("Each is a candidate miss or a call the model made deliberately.\n")
    for r in rows[:15]:
        print(f"  {r[0]} #{r[1]}  {r[2]}")
    if len(rows) > 15:
        print(f"  … and {len(rows) - 15:,} more")
    return len(rows)


def republish(con, pairs):
    """Recompute the PUBLISHED text of each (video_id, idx) from the ASR."""
    for video_id, idx in sorted(set(pairs)):
        raw = con.execute(
            "SELECT text_raw FROM utterances WHERE video_id = %s AND idx = %s",
            (video_id, idx)).fetchone()
        if not raw or raw[0] is None:
            continue
        text = raw[0]
        for r in con.execute(
                "SELECT span FROM redaction WHERE video_id = %s AND idx = %s"
                " AND status = 'applied' ORDER BY id", (video_id, idx)):
            text = text.replace(r[0], MARKER)
        con.execute(
            "UPDATE utterances SET text = %s WHERE video_id = %s AND idx = %s",
            (text, video_id, idx))


# Whether a span could LOCATE somebody's home on its own, as opposed to being
# a fragment of one. A number and a place-name together locate a house: '9641
# Jerome', '2027 Essex Drive'. Neither half alone does - '34110' is a ZIP,
# 'Palm Harbor' is a town, 'Florida' is a state - and the applied set is full
# of those halves, because a reviewer accepting a redaction accepts whatever
# the detector proposed, fragments included.
#
# The length arm is for the addresses the recogniser SPELLED OUT: 'Sixty three,
# twenty seven Grand Boulevard' has no digit in it and is a home address. No
# fragment in this archive is that long.
LOCATING = ("((%(span)s ~ '[0-9]' AND %(span)s ~ '[A-Za-z]{3}')"
            " OR length(%(span)s) >= 20)")


def scrub_answers(con, pairs):
    """Take these (video_id, span) out of saved answers' prose. Returns how
    many rows changed."""
    n = 0
    for video_id, span in {(v, s) for v, s in pairs if v and s}:
        # Containment rather than expanding the array per row, for two reasons.
        # It is what `answers_cited_video` indexes - measured over 5,000 saved
        # answers, 10.1 ms per span expanding, 1.3 ms through the index, and
        # this runs once per span inside the apply. And `@>` simply returns
        # false on a row whose `cites` is malformed, where jsonb_array_elements
        # RAISES: inside the transaction that applies a redaction, that
        # difference is between "this answer did not match" and "removing this
        # address failed", which would leave the address published.
        n += con.execute(f"""
            UPDATE answers a
               SET answer = replace(a.answer, %(span)s, %(marker)s)
             WHERE {LOCATING}
               AND strpos(a.answer, %(span)s) > 0
               AND a.cites -> 'passages' @> jsonb_build_array(
                       jsonb_build_object('video_id', %(video)s::text))
        """, {"video": video_id, "span": span, "marker": MARKER}).rowcount
    return n


def _settle(con, rows, device, verb):
    """Republish every line the decision touched, then re-index its recording.

    The re-index is not optional: the address is in the passage text, the BM25
    postings and the embedding as well as the utterance, and a redaction that
    stopped at `utterances` would leave search able to find it.
    """
    pairs = {(r[1], r[2]) for r in rows}
    republish(con, pairs)
    con.commit()
    videos = sorted({v for v, _ in pairs})
    for v in videos:
        # rebuild_video, not refresh_video: removing an address shortens the
        # line, so a passage can fall under the indexing floor and cease to
        # exist. refresh_video asserts the boundaries have NOT moved and
        # refuses when they have - correct for a name correction, fatal here.
        index_passages.rebuild_video(con, v, device=device, verbose=False)
        con.commit()
    print(f"{verb} {len(rows)}; re-indexed {len(videos)} recording(s)")
    return len(rows)


def apply(con, ids, device=DEVICE):
    """Mark these applied, then let republish() express it."""
    rows = con.execute("""
        SELECT id, video_id, idx, span FROM redaction
        WHERE id = ANY(%s) AND status = 'proposed' ORDER BY id
    """, (list(ids),)).fetchall()
    if not rows:
        print("nothing to apply")
        return 0
    kept = []
    for r in rows:                                      # positional: a db.Row unpacks to column names
        rid, video_id, idx, span = r[0], r[1], r[2], r[3]
        raw = con.execute(
            "SELECT text_raw FROM utterances WHERE video_id = %s AND idx = %s",
            (video_id, idx)).fetchone()
        # A span the recogniser never produced cannot be removed from it. This
        # is checked against text_raw rather than text, so a line carrying two
        # addresses does not look "already gone" because the other one went.
        if not raw or span not in (raw[0] or ""):
            print(f"  {rid}: span not in the ASR line, skipped")
            continue
        con.execute("""UPDATE redaction
                          SET status = 'applied', applied_at = now(),
                              before_text = %s
                        WHERE id = %s""", (raw[0], rid))
        kept.append(r)
    if not kept:
        return 0
    # In the same transaction as the status change and the republish, so the
    # archive cannot end up in the state where the address is out of the
    # transcript and still readable at a saved answer's URL. Only on apply: a
    # revert puts the text back, and there is nothing to scrub - an answer
    # quoting it could not have been produced while it was redacted. A revert
    # does NOT restore a scrubbed answer, which is the one thing here that is
    # not recomputable; the reading survives, the address does not come back.
    scrubbed = scrub_answers(con, {(r[1], r[3]) for r in kept})   # a known failure
    if scrubbed:
        print(f"  scrubbed the span from {scrubbed} saved answer(s)")
    return _settle(con, kept, device, "applied")


def revert(con, ids, device=DEVICE):
    """Un-apply, and recompute from the ASR rather than from a stored copy."""
    rows = con.execute("""
        SELECT id, video_id, idx FROM redaction
        WHERE id = ANY(%s) AND status = 'applied' ORDER BY id
    """, (list(ids),)).fetchall()
    if not rows:
        print("nothing to revert")
        return 0
    con.execute("UPDATE redaction SET status = 'rejected' WHERE id = ANY(%s)",
                ([r[0] for r in rows],))
    return _settle(con, rows, device, "reverted")


def show(con, status="proposed", limit=40):
    rows = con.execute("""
        SELECT r.id, r.video_id, r.idx, r.span, v.title, r.status
        FROM redaction r JOIN videos v ON v.id = r.video_id
        WHERE r.status = %s ORDER BY r.id LIMIT %s
    """, (status, limit)).fetchall()
    for r in rows:                                    # positional: a db.Row unpacks to column names
        rid, idx, span, title = r[0], r[2], r[3], r[4]
        print(f"{rid:>5}  {span[:46]:<46}  {(title or '')[:40]}  #{idx}")
    n = con.execute("SELECT COUNT(*) FROM redaction WHERE status = %s",
                    (status,)).fetchone()[0]
    print(f"\n{n} {status}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--propose", action="store_true",
                    help="the pattern pass, over the whole archive")
    ap.add_argument("--sections", action="store_true",
                    help="the section pass, over public hearings and comment")
    ap.add_argument("--cross-check", action="store_true",
                    help="lines one pass flags and the other does not")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--passes", type=int, default=2, metavar="N",
                    help="adjudicate each section N times and keep the union. "
                         "DEFAULT 2, which is where it saturates: 1 pass "
                         "leaves 4 of 32 addresses in, 2 removes all 32, and "
                         "3 adds nothing (eval/truth_6680.json). Pass 1 for "
                         "the old single-pass behaviour")
    ap.add_argument("--write", action="store_true",
                    help="with --propose, store the proposals")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--video")
    ap.add_argument("--model")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--status", default="proposed")
    ap.add_argument("--apply", nargs="+", type=int, metavar="ID")
    ap.add_argument("--apply-all", action="store_true")
    ap.add_argument("--revert", nargs="+", type=int, metavar="ID")
    ap.add_argument("--device", default=DEVICE)
    args = ap.parse_args()

    con = db.connect()
    if args.sections:
        propose_sections(con, limit=args.limit, video=args.video,
                         write=args.write, model=args.model, jobs=args.jobs,
                         passes=args.passes)
        return 0
    if args.cross_check:
        cross_check(con)
        return 0
    if args.propose:
        return 0 if propose(con, limit=args.limit, video=args.video,
                            write=args.write, model=args.model) >= 0 else 1
    if args.list:
        show(con, status=args.status)
        return 0
    if args.apply_all:
        ids = [r[0] for r in con.execute(
            "SELECT id FROM redaction WHERE status = 'proposed'").fetchall()]
        apply(con, ids, device=args.device)
        return 0
    if args.apply:
        apply(con, args.apply, device=args.device)
        return 0
    if args.revert:
        revert(con, args.revert, device=args.device)
        return 0
    show(con)
    return 0


if __name__ == "__main__":
    sys.exit(main())
