"""Kept answers, so that a run of the agent has a URL.

`/ask?q=...` looks like a link to an answer and is not one. It is an
instruction to spend money: sending it to somebody makes them sit through a
fresh run - minutes, at ASK_DEADLINE, and one out of the daily cap in
web/limits.py - for an answer that is not the one being shown to them, but a
different model sample over an archive that has gained meetings since. What a
person wants to send is *this* answer.

So every completed run is written here, keyed by an opaque id, and `/ask/<id>`
serves it back. The link costs nothing and arrives in one round trip.

**What is kept is the answer and what it CITED, never the words it quoted.**
The evidence is read back out of the archive when the page renders. That is
the whole design and it is worth being explicit about what it buys:

    a redaction applied since  is already in `passages.text`
    a corrected speaker name   is already on the row
    a re-parsed outcome        is already on the item

None of it needs anything to go back and find old copies, because there are no
old copies. The archive is the record; a saved answer is a reading of it, and a
reading that froze the words would slowly start disagreeing with the thing it
claims to be quoting.

Three decisions worth the words:

  the server writes it, not the page
      The alternative is a POST that takes the answer from the browser, and
      that is a public endpoint which mints a permanent URL on this domain
      from attacker-supplied content. There is no version of that which is
      not a defacement vector. The row is written in the same process that
      produced the answer, from the object it produced, and the id comes back
      to the page in the stream's `answer` event.

  the id is random, not a hash of the question
      A hash would make two askers share one row, which is a cache of
      questions rather than a link to an answer: the second reader would be
      shown, with no way to tell, what the archive said to somebody else last
      spring. Two runs of one question are two answers and get two links.

  a passage is named by its RANGE, not by its id
      `index_passages.rebuild_video` reassigns passage ids on every rebuild and
      states that nothing outside the index stores one. `(video_id, start_idx,
      end_idx)` is the natural key, unique across all 166,998 passages, and it
      survives every rebuild that does not move boundaries. Boundaries move for
      one reason - a redaction shortened a line - and then the citation is
      genuinely gone, which the page says rather than papering over.

The one thing that cannot be read back is the prose. It is generated text that
quotes the transcript, so it is the only copied text here and the only
redaction surface left. Nothing deletes it: bin/redact.py replaces the span
with its marker, in place, exactly as it does in the transcript, so the reading
survives and the address does not. `redaction.gone_from_answers` proves that
happened, and `redaction.answers_quoting_a_redacted_line` lists for a person
the one case no string search can settle - an answer that cited the line and
paraphrased the address rather than quoting it.
"""
import json
import secrets

import tools

# 9 bytes -> 12 URL-safe characters. Long enough that the ids are not
# enumerable (a shared link is not a secret, but the set of questions people
# have asked is not something to hand out by counting), short enough to sit in
# a URL somebody reads out.
ID_BYTES = 9

# Nothing here expires, and there is deliberately no knob to make it. A saved
# answer is a URL somebody may have put in an email or a news story, and a link
# that stops resolving is a worse outcome than the disk it costs - which is
# about four kilobytes a run, measured, since what is stored is a question, a
# paragraph and a list of keys. At ASK_DAILY_MAX that is a megabyte a day with
# the endpoint saturated every day, and it will not be.
#
# The other thing that used to delete rows was bin/redact.py, and it does not
# any more either: it scrubs the address out of the prose and leaves the answer
# standing.


def save(con, result):
    """Keep this run. Returns the id its URL is built from.

    The caller is expected to treat a failure here as non-fatal: the reader has
    already paid for this answer and is owed it whether or not it could be
    filed.
    """
    aid = secrets.token_urlsafe(ID_BYTES)
    con.execute(
        "INSERT INTO answers (id, question, answer, cites, run) "
        "VALUES (%s, %s, %s, %s::jsonb, %s::jsonb)",
        (aid,
         (result.get("question") or "").strip(),
         result.get("answer") or "",
         json.dumps(_cites(con, result)),
         json.dumps({
             "looked_at": result.get("looked_at") or {},
             "struck": result.get("struck") or [],
             "stopped": result.get("stopped"),
             "trace": result.get("trace") or [],
         })))
    return aid


def _cites(con, result):
    """The run's citations, as handles that outlive it.

    The passage ids are looked up rather than read off the hits, because a hit
    reaches the agent from four different tools with four different projections
    and only `id` is common to all of them. One query, in the same request that
    produced them, while they are all still valid.
    """
    ids = [p["id"] for p in result.get("evidence") or [] if p.get("id")]
    ranges = {}
    if ids:
        ranges = {r["id"]: r for r in con.execute(
            "SELECT id, video_id, start_idx, end_idx FROM passages "
            "WHERE id = ANY(%s) AND start_idx IS NOT NULL", (ids,))}
    passages = []
    for i in ids:
        r = ranges.get(i)
        if not r:
            # Cited a passage the index no longer holds, in the seconds between
            # the run and this line - a re-index landing mid-question. Dropping
            # it loses the quote, not the answer, and the page renders the
            # marker as a citation it cannot resolve. Logged because it should
            # be vanishingly rare, and if it is not, that is worth knowing.
            print(f"answer cited passage {i}, which has no range to keep",
                  flush=True)
            continue
        # `n` is the number the PROSE uses. It was this passage's id during the
        # run and will not be after the next rebuild, so it is kept as a label
        # and the range is what gets resolved.
        passages.append({"n": i, "video_id": r["video_id"],
                         "start_idx": r["start_idx"], "end_idx": r["end_idx"]})
    return {"passages": passages,
            "items": [i["id"] for i in result.get("record") or [] if i.get("id")]}


def load(con, aid):
    """The stored run, rendered against the archive as it stands now, or None.

    The id arrives from a URL path. It is parameterised, so the length bound is
    not about injection - it is that an unbounded path segment becomes an
    unbounded index probe, on an endpoint anybody can call.
    """
    if not aid or len(aid) > 64:
        return None
    row = con.execute(
        "SELECT id, question, answer, cites, run, created_at "
        "FROM answers WHERE id = %s", (aid,)).fetchone()
    if not row:
        return None

    cites, run = row["cites"] or {}, row["run"] or {}
    kept = cites.get("passages") or []
    live = tools.passages_at(
        con, [(p["video_id"], p["start_idx"], p["end_idx"]) for p in kept])
    evidence = []
    for p in kept:
        hit = live.get((p["video_id"], p["start_idx"], p["end_idx"]))
        if hit:
            # Re-labelled with the id the PROSE cites. The live row's own id is
            # whatever the last rebuild assigned and means nothing to a reader
            # or to the `[N]` in the sentence above it.
            evidence.append({**hit, "id": p["n"]})

    item_ids = cites.get("items") or []
    items = tools.items_at(con, item_ids)
    record = [items[i] for i in item_ids if i in items]

    return {
        "id": row["id"],
        "asked_at": row["created_at"],
        "question": row["question"],
        "answer": row["answer"],
        "evidence": evidence,
        "record": record,
        "looked_at": run.get("looked_at") or {"passages": 0, "items": 0},
        "struck": run.get("struck") or [],
        "stopped": run.get("stopped"),
        "trace": run.get("trace") or [],
        # Cited then, not in the archive now. Almost always a redaction: it is
        # the one thing that moves passage boundaries. Reported rather than
        # silently shown as a shorter answer, because "four quotes" and "six
        # quotes, two of which the archive no longer stands behind" are
        # different statements and the reader is owed the second one.
        "missing": {"passages": len(kept) - len(evidence),
                    "items": len(item_ids) - len(record)},
    }
