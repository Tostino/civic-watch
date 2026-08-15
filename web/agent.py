"""Ask: a loop over the tool surface, not a pipeline (UI_REQUIREMENTS D9).

`bin/ask.py` runs `plan() → gather() → read() → answer()`. The planner emits
its queries once and the rest executes them blindly, so nothing downstream can
notice a bad result and try again. This corpus punishes that specifically: the
moment a board decides something carries no topic words ("all in favor say
aye"), so the wording that finds an item's discussion puts its decision at rank
33-58 — below any depth worth reading. `retrieve.decisions_in_play()` is a
hard-coded patch over that one case, and there are others behind it.

Here the model sequences the tools itself. It searches, sees what came back,
and searches again with different words or a different tool. The stages are
whatever it decides to do, which is why the UI streams the actual calls rather
than four fixed captions: the reader watches the archive being worked, and can
see when the agent went looking somewhere and found nothing.

Three properties are load-bearing:

**One surface with `/search`.** These are the same five tools `web/tools.py`
serves to the page, with the same arguments. The agent cannot reach anything a
reader cannot, and vice versa — so a bad answer reproduces as a search.

**Every citation is checked.** A model asked to cite will cite; whether the id
exists is a separate question. Every `[N]` and `[item:N]` in the answer is
verified against what the tools actually returned in this run, and anything
else is struck out and counted. An unverifiable citation is worse than none,
because it looks exactly like a real one (R5.5.5).

**Nothing is invented for the sake of an answer.** No evidence means no answer;
the empty result is a designed outcome, not a failure.
"""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))

import ask as llm                                    # noqa: E402  the chat client
import tools as toolkit                              # noqa: E402

# How many times round the loop. Measured on the questions in eval_votes, a
# good run uses 2-4 tool turns; the cap exists for the pathological case where
# the model keeps re-searching rather than committing to an answer.
#
# Raised from 8 because 8 was measured against one KIND of question. "What was
# decided about the school zone cameras" is four lookups and done. "How does
# each commissioner argue, by year" is a lookup per commissioner per year
# before the answer can begin, and it spent all eight steps still gathering.
# Both are questions this archive should answer, and only one of them fits in
# a budget shaped like the other.
MAX_STEPS = 16
# Total characters of tool output the model may accumulate. Past this it is
# told to answer with what it has, because more evidence stops helping long
# before the context runs out and every extra passage is paid for twice - once
# to send, once as the answer gets slower.
#
# 200k chars is ~50k tokens, and ask.py's segmentation prompt establishes that
# this model is fine with 35k in; the diminishing return is the real ceiling
# here, not the context window. A question spanning five commissioners and
# eight years does not diminish at 90k - it has not finished reading.
MAX_EVIDENCE = 200_000
# Transcript lines one `get_item` may show. A long public hearing runs to
# hundreds and the cap on the tool itself is 2,000, which is the whole budget
# in one call.
LINES_SHOWN = 250

MODEL = os.environ.get("LLM_MODEL_AGENT") or llm.MODEL_HEAVY

# Wall clock for the WHOLE question, and it is a different quantity from the
# batch timeout in ask.py. That one is 600s because a whole-day segmentation
# prompt legitimately takes ten minutes; here a person is watching a page. The
# run ends the way the evidence budget ends it - stop calling tools, answer
# from what is already gathered - so a slow model costs a shorter answer and
# not a blank one.
#
# It also bounds what one reader can hold: a request occupies a server thread
# and a concurrency slot for its whole life, so "how long may this take" and
# "how many can run at once" together decide how a public endpoint behaves
# when someone is unkind to it.
#
# 150 was set when nothing could survive being slow anyway: the stream went
# quiet during a model call and the first proxy in the chain killed it, so a
# long budget only bought a longer wait for the same dropped connection.
# web/server.py's HEARTBEAT removed that constraint - these are inactivity
# timers and the stream is never idle now - which leaves the question of how
# long a reader will WATCH, and a reader watching real lookups scroll past
# will give a hard question minutes. What they will not give it is a spinner,
# and that is not what this is.
DEADLINE = int(os.environ.get("ASK_DEADLINE") or 420)
# Never let one call eat the entire budget, and never leave so little that the
# closing answer cannot be written.
#
# MIN_CALL is the one that was actually wrong, and not by a little. Reaching
# the closing answer by the TIME budget means the deadline has already passed
# - that is what the check tests - so `left()` is always the floor, and the
# floor was 20 seconds. ask.py measures the median call of exactly this shape,
# a large prompt in and a long structured answer out, at 158s. Every hard
# question was therefore guaranteed to time out while writing its answer, and
# `retries=1` meant it got one try at an impossible number. 240 is ask.py's
# own SLOW_CALL threshold: past this the model is not slow, it is broken.
MIN_CALL = 240
# The reserve and the floor are the same quantity said twice - what the closing
# answer is held back for is exactly what it is guaranteed - so say it once.
# They came apart before (45 held back, 20 guaranteed), which reads like a
# considered pair of numbers and is really a promise the floor did not keep.
ANSWER_GRACE = MIN_CALL

SYS = """You research questions about Pasco County government meetings by
calling tools, then answer from what they return.

THE ARCHIVE HAS TWO SOURCES AND THEY ARE NOT INTERCHANGEABLE.

  THE RECORD — agendas the county published and the dispositions its approved
  minutes recorded. Authoritative for what was DECIDED. Covers 2015-2026
  whether or not anyone filmed it. Cite as [item:N].

  THE TRANSCRIPT — machine transcription of 1,036 hours of recordings, 2018
  onward. Authoritative for what was SAID and argued, and roughly for who said
  it. Only 9% of decided items have one. Cite as [N].

A transcript can show a vote being taken and never its result — nobody reads
the tally into the microphone. So an OUTCOME comes from the record. An
ARGUMENT comes from the transcript. Answering "what was decided" from
transcript alone is the single most common way to get this wrong.

HOW TO WORK

1. Search BOTH sources before concluding anything. A question that finds
   nothing in the transcript has very likely been decided at one of the many
   meetings with no recording.
2. Read what came back before searching again. If the results are about the
   wrong thing, search again with the words the speakers themselves would use,
   not the words in the question.
3. When a search puts an item in play, call get_item on it. That is how you
   get the minutes disposition verbatim, and the discussion of that item
   specifically. Ranking finds an item's discussion easily and reliably misses
   its motion and its vote, because those carry no topic words — get_item does
   not have that problem.
4. If the matter has a case id, call get_case. It reaches every meeting that
   took the case up, INCLUDING ones with no recording, which searching the
   transcript can never do.
5. Stop searching when you can answer. Three or four tool calls is normal.
   If two differently-worded searches of the record both come back with
   nothing on point, the record does not have it — that IS your finding, and
   searching a third and fourth way will not change it. "The county published
   no disposition for this" is a real, useful, complete answer.

WHAT YOU MAY CITE

Every id the tools show you in square brackets is citable, and nothing else:
`[3050]` from search_transcript, `[item:22216]` from search_record, and BOTH
kinds from get_item — its transcript lines carry the id of the passage they
belong to, so the motion and the vote you find there are citable exactly like
any other passage. Write `[3050]`, never "passage 3050" or a line number. One id per bracket:
`[3069] [3070]`, never `[3069, 3070]` and never a range like `[3069-3071]`.

Write PLAIN PROSE. No markdown, no `**bold**`, no headings, no bullet lists —
paragraphs only. Anything else arrives on the page as literal asterisks.

THEN ANSWER

Write prose for someone who has not seen any of this. Rules:

- Lead with the answer. No preamble, no restating the question.
- Every factual claim carries a citation: [N] for something said, [item:N] for
  the published record. A claim with no citation will be treated as unsupported.
- Cite ONLY ids the tools actually returned to you in this conversation.
  Inventing one, or reusing an id from memory, is the worst thing you can do
  here — it is indistinguishable from a real citation to the reader.
- If the record disposes of the matter, lead with that and give the meeting
  date. Then use the transcript for what was argued and by whom.
- Never contradict a recorded disposition with an inference from the
  transcript. If they disagree, say so and give both.
- If an item has no recorded disposition, say the published record shows no
  outcome. Do NOT infer one from a vote being called.
- If what you found does not settle the question, say so plainly and say what
  IS established. Never fill a gap with plausible inference. "The archive does
  not show this" is a complete and acceptable answer.
- Speaker names come from automated voice matching, not from the record, and
  their accuracy is not measured. Never quote a figure for it. If a claim turns
  on exactly who spoke, say the attribution is automated and unverified.
  "Several speakers" or "unidentified" means the archive does not know — never
  guess.
- Distinguish what was SAID from what was DECIDED, in those words.
"""

STOP = ("You have gathered enough. Answer the question now from what the tools "
        "returned, and make no further tool calls.")


# ------------------------------------------------------------- what it saw
class Seen:
    """Every id the tools actually put in front of the model, and its context.

    This is the ONLY thing citations are checked against. It is deliberately
    not "everything in the database" - the question is not whether an id exists
    but whether this run saw it, because an answer citing a real passage it
    never read is still fabricated.
    """

    def __init__(self):
        self.passages = {}
        self.items = {}
        self.chars = 0

    def passage(self, p):
        self.passages.setdefault(p["id"], p)

    def item(self, i):
        self.items.setdefault(i["id"], i)


# ------------------------------------------------------- rendering results
#
# Tool output goes to the model as text, not JSON. JSON of the raw rows costs
# roughly three times the tokens for the same content, and the model reads the
# laid-out version more reliably - `get_item` alone can carry 2,000 transcript
# lines, which is the whole context window in braces and quotes.
def _clip(s, n):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n].rstrip() + "…"


def _passage_line(p, width=420):
    # The display name, so the model writes the name the reader will see under
    # the citation. Shown "Starkey", it wrote "Starkey said" while the chip
    # beneath said Kathryn Starkey, and the answer read like it was about
    # somebody else. `speaker` is still what the speaker facet takes, and
    # tools.canonical_speaker accepts the full name back.
    who = p.get("speaker_display") or p.get("speaker") or "unidentified"
    if who == "(exchange)":
        who = "several speakers"
    where = p.get("meeting_date") or p.get("upload_date") or "?"
    head = f"[{p['id']}] {where} · {p.get('body') or ''} · {who}"
    under = p.get("item")
    if under:
        # The item's ID, not just its title. Without it the model can SEE that
        # a passage belongs to R-58 and has no way to call get_item on it - so
        # the most important traversal in the design ("a search puts an item in
        # play, then open it") was unreachable, and the first run instead
        # searched the record six times and spent its whole budget.
        ident = f"item:{p['agenda_item_id']}" if p.get("agenda_item_id") else "?"
        head += f"\n  under [{ident}]: {_clip(under, 90)}"
    return f"{head}\n  {_clip(p.get('text'), width)}"


def _item_block(i, full=False):
    head = " · ".join(x for x in (f"[item:{i['id']}]", i.get("date"),
                                  i.get("body"), i.get("code"),
                                  i.get("case_id")) if x)
    out = [head, f"  {_clip(i.get('title'), 220)}"]
    if full and i.get("department"):
        out.append(f"  department: {i['department']}")
    if full and i.get("recommendation"):
        out.append(f"  staff recommendation: {_clip(i['recommendation'], 200)}")
    if i.get("disposition"):
        out.append(f"  MINUTES: {_clip(i['disposition'], 320)}"
                   f"  (recorded outcome: {i.get('outcome')})")
    else:
        out.append("  MINUTES: no disposition recorded for this item")
    # Said plainly, or the model reads "no transcript quotes" as "this did not
    # happen". The meeting that finally decides a case is frequently one this
    # archive holds no video of.
    if i.get("has_recording") is False:
        out.append("  (no recording of this meeting here - the published "
                   "record is the only evidence of it)")
    return "\n".join(out)


def _cover(con, item_id):
    """Which passage each utterance of an item falls inside.

    `get_item` returns utterance LINES, and the first version rendered them as
    `[385] Yeager: so my motion is...` — an id-shaped token that is a line
    index, not a passage id. The model did exactly what that invites: it wrote
    "([item:31314] passages 2, 59-60)" in prose, so the motion and the vote it
    had correctly found could not be cited at all, and the citation check
    counted zero transcript citations for an answer built on them.

    Lines are not citable and passages are, so a line is rendered with the id
    of the passage CONTAINING it. That is the honest reference anyway: a
    citation points at a moment in the recording, and a passage is exactly
    that moment.
    """
    rows = con.execute("""
        SELECT p.id, p.video_id, p.start, p."end", p.speaker,
               -- The same person as the reader will see them, for the same
               -- reason _passage_line takes it: these passages go into `seen`
               -- and become the answer's evidence. Without it a citation the
               -- agent reached through get_item printed the surname on /ask
               -- while the SAME citation printed the full name on /ask/<id>,
               -- which re-reads it from tools.PASSAGE_HIT. Measured: 'Grey'
               -- against 'Charles Grey', same passage, two pages.
               display_name(p.speaker) AS speaker_display, p.text,
               p.start_idx, p.end_idx, p.phase, p.agenda_item_id,
               ai.title AS item, ai.code, ai.case_id, ai.outcome,
               v.title, v.upload_date, v.meeting_id,
               m.date AS meeting_date, m.body
          FROM passages p
          JOIN videos v ON v.id = p.video_id
          LEFT JOIN meetings m ON m.id = v.meeting_id
          LEFT JOIN agenda_items ai ON ai.id = p.agenda_item_id
         WHERE p.agenda_item_id = %s
         ORDER BY p.video_id, p.start_idx""", (item_id,)).fetchall()
    return [dict(r) for r in rows]


def _at(cover, video_id, idx):
    for p in cover:
        if (p["video_id"] == video_id and p["start_idx"] is not None
                and p["start_idx"] <= idx <= p["end_idx"]):
            return p
    return None


def render(name, result, seen, con=None):
    """Tool result → text for the model, and everything it may now cite."""
    if name == "search_transcript":
        hits = result.get("hits", [])
        for h in hits:
            seen.passage(h)
        if not hits:
            return ("No passages matched. Most meetings were never recorded, "
                    "so this is often silence rather than absence - try "
                    "search_record.")
        note = ""
        if result.get("degraded"):
            note = ("(semantic matching unavailable; these are keyword matches "
                    "only)\n")
        return note + f"{len(hits)} passages:\n\n" + "\n\n".join(
            _passage_line(h) for h in hits)

    if name == "search_record":
        items = result.get("items", [])
        for i in items:
            seen.item(i)
        if not items:
            return "No published agenda item matches that."
        note = ""
        if result.get("loosened"):
            note = ("(no item contained every word, so this matched ANY of "
                    "them - the first ones match the most)\n")
        return (note + f"{result.get('total', len(items))} items, showing "
                f"{len(items)}:\n\n"
                + "\n\n".join(_item_block(i) for i in items))

    if name == "get_item":
        # `lines` and `thread` hang off the ITEM, not off the envelope.
        item = result.get("item") or {}
        item.setdefault("has_recording", bool(item.get("spans")))
        seen.item(item)
        out = [_item_block(item, full=True)]

        lines = item.get("lines") or []
        cover = _cover(con, item["id"]) if (con and lines) else []

        def shown(ln):
            p = _at(cover, ln.get("video_id"), ln.get("idx"))
            if p:
                seen.passage(p)
            return _line(ln, p)

        if lines:
            # The whole item, in order — this is the tool that recovers a
            # motion and a vote. They sit at the END of an item and carry no
            # topic words, so ranking never reaches them; here they are simply
            # the last few lines. This replaces `decisions_in_play()`.
            #
            # An item can run to 2,000 lines, which alone would exceed the
            # whole evidence budget. When it has to be cut, the END is kept:
            # that is where a board decides things, and it is the half that
            # retrieval could not have found by itself.
            out.append(f"\nWHAT WAS SAID — {len(lines)} lines, in order"
                       + (" (item truncated upstream)" if item.get("truncated")
                          else "") + ":")
            if len(lines) > LINES_SHOWN:
                head, tail = LINES_SHOWN // 3, LINES_SHOWN - LINES_SHOWN // 3
                out.extend(shown(ln) for ln in lines[:head])
                out.append(f"  … {len(lines) - LINES_SHOWN} lines omitted from "
                           f"the middle …")
                out.extend(shown(ln) for ln in lines[-tail:])
            else:
                out.extend(shown(ln) for ln in lines)
        else:
            out.append("\n(no recording of this item — the published record "
                       "above is the only evidence of it here)")

        thread = item.get("thread") or []
        if len(thread) > 1:
            out.append(f"\nSAME CASE ({item.get('case_id')}), "
                       f"{len(thread)} appearances: " + "; ".join(
                           f"{t.get('date')} {t.get('body') or ''} → "
                           f"{t.get('outcome') or 'no outcome recorded'}"
                           for t in thread))
        return "\n".join(out)

    if name == "get_case":
        steps = result.get("steps") or []
        for s in steps:
            s.setdefault("has_recording", bool(s.get("span")))
            seen.item(s)
        head = (f"Case {result.get('case_id')}: {len(steps)} appearances "
                f"{result.get('first')} to {result.get('last')}, "
                f"{result.get('continuances', 0)} continuances, "
                f"{result.get('recorded', 0)} of them recorded.")
        term = result.get("terminal")
        head += (f"\nFinal outcome: {term.get('outcome')} on {term.get('date')} "
                 f"[item:{term.get('id')}]" if term else
                 "\nNo terminal outcome recorded — it was continued every time, "
                 "or is still open.")
        return head + "\n\n" + "\n\n".join(_item_block(s) for s in steps)

    if name == "get_meeting":
        m = result.get("meeting") or {}
        items = result.get("items") or []
        for i in items:
            i["date"] = i.get("date") or m.get("date")
            i["body"] = i.get("body") or m.get("body")
            i.setdefault("has_recording", bool(result.get("videos")))
            seen.item(i)
        # A BCC meeting carries up to 189 items and most are consent. The cap
        # is stated rather than silent, so the model does not read a truncated
        # agenda as the whole one.
        shown = items[:60]
        more = ("" if len(shown) == len(items) else
                f"\n\n(+{len(items) - len(shown)} further items not shown; "
                f"use search_record with this date to reach them)")
        return (f"{m.get('date')} {m.get('body')} — {len(items)} agenda items:\n\n"
                + "\n\n".join(_item_block(i) for i in shown) + more)

    return _clip(json.dumps(result), 4000)


def _line(ln, p=None):
    who = ln.get("display_name") or ln.get("name") or "unidentified"
    # The citable id is the containing PASSAGE's, never the line index. Lines
    # inside one passage repeat an id, which is correct: they are one moment.
    tag = f"[{p['id']}]" if p else "[not citable]"
    return f"  {tag} {who}: {_clip(ln.get('text'), 300)}"


# --------------------------------------------------------------- citations
# A bracket may arrive carrying several ids - "[69052, 69056]", "[3069-3071]"
# - because that is how people write citations and the model imitates it. Each
# one is checked and re-emitted on its own, so everything downstream (and the
# page) only ever sees a single-id bracket. A RANGE is expanded to its
# endpoints and no further: 3070 sitting between two real ids is not evidence
# that 3070 was seen, and inventing it is the exact failure this guards.
CITE = re.compile(r"\[(item:)?\s*(\d{1,7}(?:\s*[,;/]\s*\d{1,7}"
                  r"|\s*[-‐-―]\s*\d{1,7})*)\s*\]")
IDS = re.compile(r"\d{1,7}")


def check(answer, seen):
    """Strike every citation this run did not actually see.

    A model asked to cite will cite. Whether the id exists is a separate
    question, and to a reader a fabricated `[item:41203]` is indistinguishable
    from a real one - which makes it worse than no citation at all. So the
    answer is rewritten rather than annotated: an unverifiable citation is
    removed from the prose and reported alongside it.
    """
    bad = []

    def keep(m):
        is_item = bool(m.group(1))
        pool = seen.items if is_item else seen.passages
        out = []
        for tok in IDS.findall(m.group(2)):
            n = int(tok)
            if n in pool:
                out.append(f"[item:{n}]" if is_item else f"[{n}]")
            else:
                bad.append(f"[item:{tok}]" if is_item else f"[{tok}]")
        return "".join(out)

    cleaned = CITE.sub(keep, answer)
    cleaned = re.sub(r" +([.,;:])", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    used = {"passages": sorted({int(m.group(2)) for m in CITE.finditer(cleaned)
                                if not m.group(1)}),
            "items": sorted({int(m.group(2)) for m in CITE.finditer(cleaned)
                             if m.group(1)})}
    # Everything reaching the page is now a single-id bracket, so nothing
    # downstream has to know that a list was ever possible.
    return cleaned.strip(), sorted(set(bad)), used


# -------------------------------------------------------------- the loop
def ask(question, con, on_event=None, max_steps=MAX_STEPS, model=MODEL,
        deadline=DEADLINE):
    """Answer `question`. `on_event(kind, detail)` reports progress live."""
    def emit(kind, **detail):
        if on_event:
            on_event(kind, detail)

    ends_at = time.monotonic() + deadline

    def left(grace=0):
        """Seconds a call may take. Floored at MIN_CALL so the last one is
        given a fighting chance rather than a timeout it cannot meet."""
        return max(MIN_CALL, ends_at - time.monotonic() - grace)

    seen = Seen()
    msgs = [{"role": "system", "content": SYS},
            {"role": "user", "content": question}]
    trace, stopped = [], None

    for step in range(max_steps):
        emit("thinking", step=step + 1)
        # The per-call timeout is whatever is LEFT of the budget, less the
        # room the closing answer needs. Without that subtraction a single
        # slow call spends the whole allowance and the reader gets the
        # timeout instead of the answer it was gathering evidence for.
        reply = llm.chat_raw(msgs, model=model, temperature=0.2,
                             timeout=left(ANSWER_GRACE), retries=2,
                             tools=[{"type": "function", "function": t}
                                    for t in toolkit.MANIFEST])
        calls = reply.get("tool_calls") or []
        if not calls:
            answer = reply.get("content") or ""
            break

        # The assistant turn has to go back verbatim, tool_calls and all, or
        # the next turn's tool results have nothing to attach to.
        msgs.append({"role": "assistant",
                     "content": reply.get("content") or "",
                     "tool_calls": calls})

        for c in calls:
            fn = c.get("function") or {}
            name = fn.get("name") or ""
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            emit("tool", id=c.get("id"), name=name, args=args)
            try:
                result = toolkit.call(con, name, args)
                text = render(name, result, seen, con)
                ok = True
            except toolkit.ToolError as e:
                # Handed BACK to the model rather than raised. A wrong argument
                # is something it can fix on the next turn, and killing the run
                # over it would throw away the work already done.
                text, ok = f"That call was rejected: {e}", False
            except Exception as e:                            # noqa: BLE001
                text, ok = f"That call failed: {type(e).__name__}: {e}", False
            seen.chars += len(text)
            trace.append({"name": name, "args": args, "ok": ok,
                          "chars": len(text)})
            emit("tool_done", id=c.get("id"), name=name, ok=ok,
                 passages=len(seen.passages), items=len(seen.items))
            msgs.append({"role": "tool", "tool_call_id": c.get("id"),
                         "content": text})

        # Both budgets end the run the same way, and the time one is checked
        # here rather than at the top of the loop so a step that has already
        # paid for its evidence gets to contribute it.
        if seen.chars > MAX_EVIDENCE or time.monotonic() >= ends_at:
            stopped = ("evidence budget" if seen.chars > MAX_EVIDENCE
                       else "time budget")
            msgs.append({"role": "user", "content": STOP})
            emit("answering", why=stopped)
            answer = llm.chat_raw(msgs, model=model, temperature=0.3,
                                  timeout=left(), retries=1).get("content") or ""
            break
    else:
        # Ran out of steps still calling tools. Ask once more, without them, so
        # a run that spent its budget still produces an answer from what it
        # gathered instead of nothing.
        stopped = "step limit"
        msgs.append({"role": "user", "content": STOP})
        emit("answering", why=stopped)
        answer = llm.chat_raw(msgs, model=model, temperature=0.3,
                              timeout=left(), retries=1).get("content") or ""

    emit("checking")
    answer, struck, used = check(answer, seen)

    # Evidence is returned as the objects the UI already knows how to render,
    # not as text - the answer's citations are ids, and the page resolves them
    # (R5.5.2, R5.5.3). Only what was CITED: everything else was looked at and
    # not used, and showing it as evidence would overstate the answer.
    evidence = [seen.passages[i] for i in used["passages"]]
    record = [seen.items[i] for i in used["items"]]
    emit("done", cited=len(evidence) + len(record), struck=len(struck))
    return {
        "question": question,
        "answer": answer,
        "evidence": evidence,
        "record": record,
        "trace": trace,
        # What the agent looked at but did not cite. Honest about the gap
        # between "searched" and "used" without dressing one as the other.
        "looked_at": {"passages": len(seen.passages), "items": len(seen.items)},
        "struck": struck,
        "stopped": stopped,
    }


def main():
    import argparse
    import db
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("question", nargs="+")
    ap.add_argument("--steps", type=int, default=MAX_STEPS)
    args = ap.parse_args()
    con = db.connect()
    r = ask(" ".join(args.question), con, max_steps=args.steps,
            on_event=lambda k, d: print(f"[{k}] {d}", file=sys.stderr))
    print("\n" + r["answer"] + "\n" + "-" * 70)
    for e in r["evidence"]:
        who = e.get("speaker_display") or e.get("speaker") or "?"
        print(f"[{e['id']}] {e.get('meeting_date')} {who[:18]:<18} "
              f"{_clip(e.get('text'), 70)}")
    for i in r["record"]:
        print(f"[item:{i['id']}] {i.get('date')} {i.get('code') or '':5s} "
              f"{i.get('outcome') or '-':10s} {_clip(i.get('title'), 60)}")
    print(f"\nlooked at {r['looked_at']} · cited "
          f"{len(r['evidence'])}+{len(r['record'])} · struck {r['struck']} · "
          f"{llm.usage_report()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
