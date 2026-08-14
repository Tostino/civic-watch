"""The LLM client, and the RETIRED fixed question-answering pipeline.

**`ask()` below is superseded by `web/agent.py` and is no longer served.**
`/api/ask` calls the agent, which sequences `web/tools.py` itself (D9). Do not
add stages or arguments here; that is the thing D9 says to stop doing. It is
kept, running, for two reasons: `bin/eval_votes.py --agent` still measures it,
which makes it the baseline the agent is compared against, and `chat()` is the
shared LLM client that `segment.py` and `name_speakers.py` depend on.

The pipeline's own failure is the argument for what replaced it. Four stages,
because no single retrieval pass answers the questions people actually ask:

  PLAN      one call turns the question into several differently-worded
            queries plus filters. "What was the sentiment of public comment"
            needs different search text than the words in the question.
  RETRIEVE  hybrid search per query, unioned. Cross-meeting questions get
            `spread` so the timeline is covered rather than one loud meeting.
  READ      batches of passages go to the model in parallel, which keeps only
            what genuinely bears on the question and quotes it. This is the
            step that makes 27k passages tractable - most are discarded here.
  ANSWER    one call writes the answer from surviving evidence only.

Every claim carries a citation to (meeting, timestamp, speaker), so the answer
is checkable against the recording rather than taken on trust.

What it cannot do, and why the agent exists: the planner emits its queries once
and the rest executes them blindly. A vote passage contains no topic words, so
the planner's own wording put the school-zone vote at rank 33-58 while READ saw
only the top 30 - and `decisions_in_play()` is a hard-coded patch over that one
case. The agent reaches the same vote by calling `get_item` once a search puts
the item in play, which is a decision it makes rather than a stage somebody
wired in.
"""
import argparse
import concurrent.futures as cf
import http.client
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.request

import db
import retrieve

API_BASE = os.environ.get("INFERENCE_API_BASE", "https://api.deepseek.com")
MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-flash")
MODEL_HEAVY = os.environ.get("LLM_MODEL_HEAVY", "deepseek-v4-flash")
READ_BATCH = 8
MAX_WORKERS = 6


class MissingKey(RuntimeError):
    """No inference key in the environment.

    A RuntimeError and NOT SystemExit, which is what this raised for months.
    SystemExit does not inherit from Exception, so `except Exception` - the
    guard around every request in web/server.py - does not catch it. In a
    ThreadingHTTPServer it unwound the request thread silently instead: the
    SSE connection stayed open, the reader watched "thinking" for ever, and
    the server logged nothing. A library imported by a server may not decide
    to exit the process; it reports, and the caller decides.
    """


def api_key():
    for k in ("LLM_API_KEY", "INFERENCE_API_KEY", "DEEPSEEK_API_KEY"):
        if os.environ.get(k):
            return os.environ[k]
    raise MissingKey(
        "No inference key. Start this process with the archive's env file "
        "sourced:  source env.local.sh  (or run it through bin/_env.sh), "
        "which defines LLM_API_KEY.")


# Prefix-cache accounting. Cache hits are an order of magnitude cheaper, but
# only when the prefix is byte-identical, so this is tracked rather than
# assumed - a stray timestamp in a system prompt silently costs full price on
# every call and nothing in the output would reveal it.
USAGE = {"calls": 0, "cache_hit": 0, "cache_miss": 0, "completion": 0,
         "reasoning": 0}


# The response is not streamed, so the socket sits silent for the whole
# generation and `timeout` is really "how long may one call take". A whole-day
# segmentation prompt is ~35k tokens in and a long structured plan out: the
# MEDIAN measured call is 158s. The old 180s default was therefore tuned to the
# happy path - half of all meeting-days timed out, retried three times at 180s
# each, and failed after nine minutes having thrown away three paid-for
# completions. Short calls pass a smaller timeout rather than the reverse.
TIMEOUT = 600
_USAGE_LOCK = threading.Lock()
SLOW_CALL = 240   # log anything slower, so a creeping model never hides again

# Everything that means "the network let us down", which is worth another
# attempt. The list is wider than it looks it should be because the body is
# read lazily by json.load(): a connection dropped mid-response surfaces from
# deep inside http.client as IncompleteRead or ChunkedEncodingError, neither of
# which is a URLError. Catching only URLError killed a whole name_speakers run
# on one truncated response.
RETRYABLE = (urllib.error.URLError, TimeoutError, ConnectionError, OSError,
             http.client.HTTPException, json.JSONDecodeError, KeyError)


def chat(messages, model=MODEL, temperature=0.2, as_json=False, retries=3,
         timeout=TIMEOUT):
    """The text of one reply. Four callers depend on this exact signature."""
    return chat_raw(messages, model, temperature, as_json, retries,
                    timeout).get("content") or ""


def chat_raw(messages, model=MODEL, temperature=0.2, as_json=False, retries=3,
             timeout=TIMEOUT, tools=None, tool_choice=None):
    """The whole reply MESSAGE, so a caller can see `tool_calls`.

    Added for the agent (web/agent.py): a tool-calling loop needs the message
    back, not the string, because the interesting turns have no content at all.
    `chat()` stays as it was - segment.py and name_speakers.py call it several
    thousand times a run and neither wants a dict.
    """
    body = {"model": model, "messages": messages, "temperature": temperature}
    if as_json:
        body["response_format"] = {"type": "json_object"}
    if tools:
        body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice
    payload = json.dumps(body).encode()
    last = None
    for attempt in range(retries):
        # Rebuild per attempt: a Request that has already been opened carries
        # host/redirect state, and reusing it across retries is not defined.
        req = urllib.request.Request(
            f"{API_BASE}/v1/chat/completions", data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key()}"})
        began = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.load(r)
            u = d.get("usage") or {}
            with _USAGE_LOCK:      # 12 segment threads share this dict
                USAGE["calls"] += 1
                USAGE["cache_hit"] += u.get("prompt_cache_hit_tokens", 0)
                USAGE["cache_miss"] += u.get("prompt_cache_miss_tokens", 0)
                USAGE["completion"] += u.get("completion_tokens", 0)
                USAGE["reasoning"] += (
                    (u.get("completion_tokens_details") or {})
                    .get("reasoning_tokens", 0))
            spent = time.monotonic() - began
            if spent > SLOW_CALL:
                print(f"  slow LLM call: {spent:.0f}s  "
                      f"{u.get('completion_tokens', 0)} completion tok",
                      file=sys.stderr, flush=True)
            return d["choices"][0]["message"]
        except urllib.error.HTTPError as e:
            # A 4xx is a statement about the request, so retrying sends the
            # identical bytes and gets the identical refusal. Only 429 and the
            # 5xx range are worth another attempt. The body says WHY - the old
            # code discarded it and reported a bare "LLM call failed".
            detail = e.read(2000).decode("utf-8", "replace")
            last = f"HTTP {e.code}: {detail}"
            if e.code != 429 and e.code < 500:
                raise RuntimeError(f"LLM call refused: {last}") from None
        except RETRYABLE as e:
            last = f"{type(e).__name__}: {e} after {time.monotonic()-began:.0f}s"
        if attempt < retries - 1:
            # Backoff with jitter: without it, 12 threads that hit the same
            # rate limit retry in lockstep and trip it again together.
            time.sleep(min(30, 2 ** attempt * 5) * (0.5 + random.random()))
    raise RuntimeError(f"LLM call failed after {retries} attempts: {last}")


def usage_report():
    """What the run actually cost, both sides of it.

    This reported PROMPT TOKENS ONLY, and `completion` was counted into USAGE
    and then never printed. On a reasoning model that is the wrong half to
    watch: output is billed several times higher than input, and most of it is
    reasoning the caller never sees. A whole-archive estimate built on the
    printed number was low by the entire output cost.
    """
    hit, miss = USAGE["cache_hit"], USAGE["cache_miss"]
    tot = hit + miss
    if not tot and not USAGE["completion"]:
        return f"{USAGE['calls']} calls"
    out = USAGE["completion"]
    think = USAGE["reasoning"]
    return (f"{USAGE['calls']} calls · prompt {tot:,} tok "
            f"({hit/tot*100:.0f}% cached) · completion {out:,} tok"
            + (f" (of which {think:,} reasoning, "
               f"{think/out*100:.0f}%)" if think else ""))


PLAN_SYS = """You plan retrieval over transcripts of county government meetings
(Board of County Commissioners, Planning Commission), 2018-2026.

Return JSON:
{"queries": ["...", "..."],        // 2-4 SHORT search phrases, differently worded.
                                    // Use the vocabulary speakers would actually
                                    // use, not the vocabulary of the question.
 "spread": true|false,              // true if the answer needs coverage ACROSS
                                    // meetings/time rather than the single best
                                    // passage (history, trends, "has this come
                                    // up before", "how did X vote over time")
 "speaker": null|"Surname",         // only if the question is about one person
 "since": null|"YYYY-MM-DD",
 "until": null|"YYYY-MM-DD",
 "what_to_look_for": "one sentence telling a reader what counts as relevant"}

Board members: Mariano, Oakley, Starkey, Weightman, Yeager."""

READ_SYS = """You are gathering evidence from county meeting transcripts.

You get a question and numbered passages. Keep every passage that BEARS ON the
question. A passage does not have to answer it. Keep it if it supplies any of:

  - a direct answer
  - what was proposed, presented, moved, seconded or voted
  - an argument, objection or question about the subject
  - context a reader would need: costs, timelines, who was involved
  - a related earlier or later development on the same subject

Drop a passage only when it is about something else entirely. Being partial,
inconclusive, or "just discussion" is NOT a reason to drop it - discussion is
often exactly what the question is about, and the synthesis step decides what
the evidence does and does not establish.

Return JSON:
{"keep": [{"id": <passage id>,
           "quote": "<= 35 words, verbatim from the passage",
           "why": "one clause on what it shows"}]}

Return {"keep": []} only if genuinely nothing relates.

WORKED EXAMPLES. These are the judgement calls that go wrong most often, so
they are spelled out rather than left to inference.

Question: "What was decided about the school zone speed cameras?"

  [1801] Traffic Operations: "We're gonna have a discussion today about the
         school zone speed cameras. Hillsborough adopted an ordinance..."
  -> KEEP. It is a presentation, not a decision, but it establishes what was
     before the board. "Only discussion" is not a reason to drop.

  [1446] "Yeager: so my motion is to adopt both programs. And as per the
         sheriff, they patrol the schools as much as they can."
  -> KEEP. The motion itself. This is the single most important kind of
     passage for a "what was decided" question.

  [1610] "Mariano: we have a motion and a second. All in favor say aye. Aye.
         Any opposed, nay."
  -> KEEP. The vote. Short procedural exchanges carry the outcome; never drop
     one for being brief.

  [2904] Public commenter: "I live at 3636 Halloran Loop and I'm asking
         you not to approve more license plate cameras."
  -> DROP. Cameras, but the wrong ones - this is ALPR surveillance, not school
     zone speed enforcement. Topic adjacency is not relevance.

Question: "What was the sentiment of public comment on the cameras?"

  [7115] "Yeager: I trust our sheriff's office. That stuff is going on in
         other counties."
  -> DROP for this question. Yeager is a commissioner deliberating, not a
     member of the public. Who is speaking, and in what part of the meeting,
     changes whether a passage answers the question asked."""

ANSWER_SYS = """Answer a question about county government meetings using ONLY
the evidence given.

You get evidence of two kinds, and the difference between them is the whole
point:

  OFFICIAL RECORD - agenda items as published by the county, with the
    disposition recorded in the approved minutes. This is the authoritative
    statement of what the board DECIDED. Cite it as [item:N].
  TRANSCRIPT - what was actually said, with speaker and timestamp. This is
    the authoritative statement of what was SAID and ARGUED, and of who said
    it. Cite it as [N].

A transcript can show a vote being taken and never its result: nobody reads
the tally into the microphone. So an outcome comes from the official record.

Rules:
- Cite with [id] or [item:id] after the claim it supports. Every factual claim
  needs one.
- If the official record disposes of the item, LEAD WITH THAT and say the
  meeting date. Then use the transcript for what was argued and by whom.
- Never contradict a recorded disposition with an inference from the
  transcript. If they disagree, say so and give both.
- If an item has no recorded disposition, say the published record does not
  show an outcome - do NOT infer one from a vote being called.
- If the evidence does not settle the question, say so plainly and say what IS
  established. Never fill a gap with plausible inference.
- Distinguish what was SAID from what was DECIDED. Discussion is not a vote.
- Speaker names come from automated voice matching, not from the record. Their
  accuracy is NOT currently measured, so never quote a figure for it; if a
  claim depends on exactly who spoke, say the attribution is automated and
  unverified. A speaker shown as "Group N" or "(exchange)" is unidentified -
  never guess who it was.
- Be direct and concrete. Lead with the answer, not a preamble."""


def plan(question):
    raw = chat([{"role": "system", "content": PLAN_SYS},
                {"role": "user", "content": question}], as_json=True)
    try:
        p = json.loads(raw)
    except json.JSONDecodeError:
        p = {}
    p.setdefault("queries", [question])
    if not p["queries"]:
        p["queries"] = [question]
    return p


def gather(p, per_query=30, device="cuda:1"):
    """Returns (passages, items) - what was said, and what was decided."""
    seen, passages = set(), []
    con = db.connect()          # one connection for the whole plan, not four
    try:
        for q in p["queries"][:4]:
            for r in retrieve.search(q, limit=per_query,
                                     spread=2 if p.get("spread") else None,
                                     speaker=p.get("speaker"),
                                     since=p.get("since"), until=p.get("until"),
                                     device=device, con=con):
                if r["id"] not in seen:
                    seen.add(r["id"])
                    passages.append(r)
        # Ranking reliably surfaces an item's discussion and reliably misses
        # its motion and vote, which carry no topic words. Pull those in by
        # structure once the item is in play.
        extra = retrieve.decisions_in_play(con, passages)
        passages.extend(e for e in extra if e["id"] not in seen)
        # The published record for whatever the passages landed on. Fetched
        # AFTER the expansion above, so an item pulled in by structure still
        # brings its minutes disposition with it.
        items = retrieve.items_for(con, passages)
        # ...and the record searched on its own terms, because 91% of decided
        # items have no transcript to be found through.
        seen_items = {i["id"] for i in items}
        for q in p["queries"][:4]:
            for i in retrieve.search_items(con, q)["items"]:
                if i["id"] not in seen_items:
                    seen_items.add(i["id"])
                    i["passages"] = 0
                    items.append(i)
    finally:
        con.close()
    return passages, items


def who(speaker):
    """How to write a speaker that the archive could not identify.

    passages.speaker is NULL when no name survived resolution - it used to be
    a rendered stand-in like "Group 465", a diarization id that reads as a
    name and is reshuffled on every clustering run. Nothing downstream may
    print that None, and one call site sliced it, which would have raised.
    """
    return speaker or "unidentified speaker"


def official_record(items):
    """The published items, rendered for the answer step."""
    out = []
    for i in items:
        if i["source"] != "agenda":
            continue            # no official record to state
        head = " · ".join(x for x in (
            f"[item:{i['id']}]", i["date"], i["body"], i["code"],
            i["case_id"]) if x)
        line = f"{head}\n  {' '.join((i['title'] or '').split())[:200]}"
        if i.get("department"):
            line += f"\n  department: {i['department']}"
        if i.get("recommendation"):
            line += f"\n  staff recommendation: {i['recommendation'][:160]}"
        if i.get("disposition"):
            line += (f"\n  MINUTES: {' '.join(i['disposition'].split())[:280]}"
                     f"  (recorded outcome: {i['outcome']})")
        else:
            line += "\n  MINUTES: no disposition recorded for this item"
        # Said plainly, because otherwise the answer treats "no transcript
        # quotes for this item" as "this did not happen". The meeting that
        # finally decides a case is frequently one we hold no video of.
        if not i.get("has_recording"):
            line += ("\n  (no recording of this meeting in the archive - the "
                     "published record is the only evidence of it here)")
        out.append(line)
    return "\n\n".join(out)


# Reading the same batch through several narrow lenses beats one broad pass:
# a generic "is this relevant" question reliably under-weights terse
# procedural lines ("Aye.", "so my motion is...") against verbose discussion,
# which is how a vote goes missing from an answer about what was decided.
#
# This is affordable because the batch text sits in the CACHED PREFIX and only
# the trailing instruction changes, so passes after the first cost roughly a
# tenth. Message order matters and is load-bearing: stable content first,
# varying instruction last.
LENSES = [
    ("decisions",
     "Motions, seconds, votes, roll calls, outcomes, continuances, directions "
     "to staff. Terse procedural lines matter MOST here - 'so moved', "
     "'second', 'all in favor', 'aye', 'motion carries', 'we'll bring it "
     "back'. Never skip one for being short."),
    ("positions",
     "Stated views, arguments, objections, reasoning and questions - who is "
     "for or against and why."),
    ("public",
     "Statements by members of the public: commenters at the podium, their "
     "asks and their reasoning. NOT commissioners or staff deliberating."),
    ("facts",
     "Concrete specifics: costs, dates, counts, locations, contract terms, "
     "staff findings, what a program does."),
]


def read_batch(question, focus, batch):
    """Read one batch through every lens; union what survives."""
    # The agenda item is shown because a passage often cannot be judged without
    # it: "All in favor say aye" is either the answer or noise depending
    # entirely on which item was being voted, and the words alone never say.
    listing = "\n\n".join(
        f"[{p['id']}] {p['upload_date']} · {who(p['speaker'])} · {p['title'][:50]}"
        + (f"\nagenda item: {p['item']}" if p.get("item") else "")
        + f"\n{p['text'][:900]}" for p in batch)
    # Stable prefix: system rules, then the question, then the passages.
    prefix = [{"role": "system", "content": READ_SYS},
              {"role": "user",
               "content": f"Question: {question}\nLooking for: {focus}\n\n"
                          f"Passages:\n{listing}"}]
    kept, seen = [], set()
    for name, lens in LENSES:
        raw = chat(prefix + [{"role": "user",
                              "content": f"This pass: keep only passages "
                                         f"containing {name.upper()}.\n{lens}\n"
                                         f"Ignore passages that belong to a "
                                         f"different pass."}], as_json=True)
        try:
            for k in json.loads(raw).get("keep", []):
                if k.get("id") not in seen:
                    seen.add(k["id"])
                    k["lens"] = name
                    kept.append(k)
        except json.JSONDecodeError:
            continue
    return kept


def ask(question, device="cuda:1", verbose=True, on_event=None):
    """on_event(stage, detail) reports progress; the UI streams these, since
    the whole pipeline takes long enough that a bare spinner is unhelpful."""
    def emit(stage, **detail):
        if on_event:
            on_event(stage, detail)
        if verbose:
            print(f"[{stage}] {detail}", file=sys.stderr)

    emit("planning")
    p = plan(question)
    emit("planned", queries=p["queries"], spread=bool(p.get("spread")),
         speaker=p.get("speaker"))

    passages, items = gather(p, device=device)
    published = [i for i in items if i["source"] == "agenda"]
    if not passages and not published:
        emit("done")
        return {"answer": "Nothing in the indexed meetings matches that.",
                "evidence": [], "decisions": []}
    if not passages:
        # No transcript, but the county published a record of it. This is the
        # common case, not the edge one: 91% of the items the minutes dispose
        # of were decided at a meeting with no recording in this archive.
        emit("answering", evidence=0, items=len(published))
        answer = chat(
            [{"role": "system", "content": ANSWER_SYS},
             {"role": "user",
              "content": f"Question: {question}\n\nOFFICIAL RECORD:\n"
                         f"{official_record(items)}\n\nTRANSCRIPT:\n(none - no "
                         f"meeting recording in the archive covers this)"}],
            model=MODEL_HEAVY, temperature=0.3)
        emit("done")
        return {"answer": answer, "evidence": [], "plan": p,
                "decisions": [dict(i) for i in items]}
    emit("retrieved", passages=len(passages),
         meetings=len({x["video_id"] for x in passages}),
         items=sum(1 for i in items if i["source"] == "agenda"))

    by_id = {x["id"]: x for x in passages}
    batches = [passages[i:i + READ_BATCH]
               for i in range(0, len(passages), READ_BATCH)]
    focus = p.get("what_to_look_for", question)
    kept, done = [], 0
    emit("reading", batches=len(batches))
    with cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for res in ex.map(lambda b: read_batch(question, focus, b), batches):
            kept.extend(res)
            done += 1
            emit("read_progress", done=done, total=len(batches),
                 kept=len(kept))
    if not kept:
        emit("done")
        return {"answer": "The meetings touch on this but nothing in them "
                          "answers it directly.", "evidence": [],
                "decisions": []}
    emit("answering", evidence=len(kept))

    # Chronological: most of these questions are about how something developed.
    kept = [k for k in kept if k.get("id") in by_id]
    kept.sort(key=lambda k: (by_id[k["id"]]["upload_date"] or "",
                             by_id[k["id"]]["start"]))
    ev = "\n".join(
        f"[{k['id']}] {by_id[k['id']]['upload_date']} · "
        f"{who(by_id[k['id']]['speaker'])} · \"{k['quote']}\" ({k.get('why','')})"
        for k in kept)
    # The official record goes FIRST: it is the authoritative outcome, and
    # putting it after several hundred lines of transcript is how it gets
    # treated as a footnote to the discussion rather than the answer.
    record = official_record(items)
    body = (f"Question: {question}\n\n"
            + (f"OFFICIAL RECORD:\n{record}\n\n" if record else
               "OFFICIAL RECORD: none of the retrieved discussion belongs to a "
               "published agenda item, so there is no recorded disposition.\n\n")
            + f"TRANSCRIPT:\n{ev}")
    answer = chat([{"role": "system", "content": ANSWER_SYS},
                   {"role": "user", "content": body}],
                  model=MODEL_HEAVY, temperature=0.3)

    # Every field the UI needs to GROUP this evidence rather than list it flat.
    # These were all sitting in the retrieval rows already and were being
    # dropped here, which is why the page could only ever render a flat
    # chronological list of quotes with no sense of which meeting or which
    # agenda item they belonged to.
    cites = [{"id": k["id"], "video_id": (p_ := by_id[k["id"]])["video_id"],
              "start": p_["start"], "date": p_["upload_date"],
              "speaker": p_["speaker"], "title": p_["title"],
              "quote": k["quote"], "why": k.get("why"), "lens": k.get("lens"),
              "agenda_item_id": p_.get("agenda_item_id"),
              "item": p_.get("item"), "code": p_.get("code"),
              "case_id": p_.get("case_id"), "outcome": p_.get("outcome"),
              "phase": p_.get("phase"), "kind": p_.get("kind")}
             for k in kept]
    return {"answer": answer, "evidence": cites, "plan": p,
            "decisions": [dict(i) for i in items]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="+")
    ap.add_argument("--device", default="cuda:1")
    args = ap.parse_args()
    r = ask(" ".join(args.question), device=args.device)
    print("\n" + r["answer"] + "\n")
    print("-" * 70)
    for c in r["evidence"][:20]:
        print(f"[{c['id']}] {c['date']} {int(c['start']//60):>4}m "
              f"{who(c['speaker'])[:20]:<20} {c['quote'][:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
