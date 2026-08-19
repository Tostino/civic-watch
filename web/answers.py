"""Kept answers, so that a run of the agent has a URL.

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
