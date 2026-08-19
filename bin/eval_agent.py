"""Pass/fail: did the answer reach the evidence it needed?

The target is addressed by (meeting, timestamp), never by passage id: ids are
reassigned on every index rebuild, so a hard-coded id silently starts pointing
at a different passage as the archive grows."""
import argparse
import json
import os
import sys
import time

import db
import retrieve

DEPTH = 200

# --------------------------------------------------------------- the answers
#
# Moments are addressed by (video, seconds) for the same reason the retrieval
# targets below are - `bin/index_passages` reassigns every passage id on each
# rebuild, so a literal id in this file silently starts pointing somewhere
# else as the archive grows.
ANSWERS = [
    {
        "q": "What was decided about the school zone speed cameras?",
        # The decision moment carries no topic words ("all in favor say aye"),
        # so no wording reaches it and only opening the item does.
        "moments": [("Icp0s5wWMeI", 609.6, "the voice vote"),
                    ("Icp0s5wWMeI", 446.4, "the motion")],
        "catches": "a run that answers from search alone and never opens the "
                   "item, which reads as 'no decision was recorded'",
    },
    {
        "q": "What happened to the Evans County Line 80 rezoning?",
        # COVERAGE. The discussion that explains why staff flipped from denial
        # to approval is under a DIFFERENT agenda item (39779) than the
        # rezoning itself (20439, which holds no passages at all), so it is
        # reachable only by working the case rather than the obvious item.
        #
        # ANY of these, not all: they are two ways of stating the same
        # load-bearing fact, and an answer is not wrong for choosing one.
        "moments_any": [("aiVFfYBkZIk", 1566.0),
                        ("aiVFfYBkZIk", 1781.0)],
        "moments_any_what": "the reversal explained — staff on the reworked "
                            "plan that took the multifamily out",
        # THE RECORD ALONE. The decision is item 21129, which has zero spans
        # and zero passages: the meeting was recorded, the item is not bound to
        # any point in it, and that is true of 91% of decided items. An agent
        # that only searches the transcript concludes the archive is silent
        # about an outcome the county published.
        "record": [(21129, "the July 2025 approval, which exists only in the "
                           "published record")],
        "catches": "a researcher that stops before opening the discussion - "
                   "the run that gathered 12 passages instead of 180 and told "
                   "a reader there had been no substantive debate",
    },
    {
        # THE NEGATIVE CASE, and the one this design is proudest of: "the
        # archive does not show this" is a designed outcome, not a failure.
        # Every other check here asserts something was FOUND, so an agent that
        # confidently invented an answer to an unanswerable question would
        # have scored full marks.
        "q": "What did the county decide about building a casino in Pasco?",
        # It asserted "no agenda item may be cited" first, and that was wrong
        # about the AGENT rather than about the archive. The answer cited four
        # items to show why the only word-matches are not casinos - two 2025
        # items settling a lawsuit against a couple named Gamble, a sports
        # sponsorship, a playground purchase - which is how an absence is
        # actually demonstrated. Citing nothing would have been the weaker
        # answer: "trust me, there is nothing."
        "must_say_what": "that this archive holds no such decision",
        "expect_stopped": None,
        "catches": "an agent that answers from the nearest thing the index "
                   "returned rather than from what the county decided",
    },
    {
        # RULES THAT CHANGED WHILE THEY WERE BEING MADE. The other questions have
        # a settled thing to find; this one punishes an agent for reporting an
        # EARLIER state of the record as the current one. A reader asking it is
        # deciding whether to buy four hens.
        #
        #   PDE-25-0469  [item:21646]  Planning Commission, 18 Sep 2025. No
        #                recorded outcome; the recommendation exists only on tape.
        #   PDE-26-0001  [item:21812]  BCC 21 Oct, `no_action`
        #                [item:21922]  BCC 12 Nov, `adopted`.  WHERE hens may
        #                be kept.
        #   PDE-26-0028  [item:21923]  BCC 12 Nov, `adopted`.  HOW they may be
        #                kept, and THESE ARE THE RULES THE QUESTION ASKS FOR. A
        #                different case decided at the same meeting, so get_case
        #                on the ordinance found first cannot reach them.
        #
        # THREE TRAPS, and an agent can fall in each separately:
        #
        # 1. `no_action` on 21 October reads as a rejection. The minutes say it
        #    was the first of two hearings, with adoption set for 12 November.
        # 2. The obvious search misses half the answer: search_record
        #    ("backyard chickens") never returns [item:21923], whose title says
        #    "Conditions For The Keeping Of Chickens".
        # 3. THE DRAFT IS NOT THE ORDINANCE. A county permit and a mandatory
        #    education course were both written, discussed at length and then
        #    taken out. An agent that reads the earlier discussion and stops
        #    would tell a resident to go and get a permit that does not exist.
        "q": "What rules did Pasco County adopt for backyard chickens?",
        # The permit coming out. EVERY passage that carries it: written with
        # three, this failed a right answer that cited staff confirming the
        # removal, and the judge passed the same answer in the same run, so the
        # harness contradicted itself about one fact. The rule for this field is
        # EVERY way of saying the load-bearing thing, or it tests which phrasing
        # the writer happened to choose.
        "moments_any": [("R0bQZ5v6ubg", 848.9),    # staff, at adoption
                        ("R0bQZ5v6ubg", 1144.5),   # staff, again
                        ("lqYZTbWi9_4", 1016.1),   # staff, the earlier draft
                        ("lqYZTbWi9_4", 1028.1),   # staff, why it went
                        ("lqYZTbWi9_4", 1072.4)],  # Yeager, for no permit
        "moments_any_what": "the permit requirement being taken out",
        "record": [(21922, "the zoning ordinance adopted 12 November 2025"),
                   (21923, "the keeping rules — a separate case, adopted the "
                           "same day, and the half the obvious search misses")],
        # The single fact a resident most needs and the record most easily
        # gets wrong, since two earlier drafts carried a permit and staff say
        # twice on the record that it was removed.
        "must_say_what": "that no county permit is required to keep backyard "
                         "chickens",
        "catches": "an agent that reports a draft as the adopted rule, or "
                   "reads the October 'no action' as a refusal, or answers "
                   "from the zoning ordinance alone and never finds the rules",
    },
]

# Bounds, not targets. They exist because both were breached today: a writer
# handed 101 pieces of evidence wrote 2,268 words in a single 348-second call,
# which on a page is a reader watching a spinner past the deadline.
MAX_ANSWER_WORDS = 1200
# The time bound is NOT here: it is derived from agent.DEADLINE in main(),
# where the agent module has been imported. The flat 300 it replaces was
# measured when one agent did both jobs and answers took 90-160s. Splitting
# research from writing cost 3-4x and runs now land at 299-319s, so the bound
# began failing runs the agent itself considered on time - a stale number
# reporting a regression that was not there. Tied to the deadline it cannot
# drift out of true again.
RUN_SECONDS_SLACK = 120
# Terms of art a resident could be stopped by. Not zero: an answer about a
# rezoning cannot avoid saying 'rezoning'. Four is where it stops being an
# answer and starts being a document.
MAX_JARGON = 3

# ------------------------------------------------------------------- judging
#
#   IT IS NEVER ASKED WHETHER THE ANSWER IS GOOD. Only narrow, quotable
#   questions with a right answer. "Rate this out of five" drifts with the
#   weather; "does this passage support this sentence" does not.
#
#   IT CALIBRATES ITSELF FIRST. FIXTURES below are three hand-made cases with
#   known verdicts. A judge that misses the planted fabrication, or flags the
#   clean one, is reported as UNFIT and its findings about the agent are
#   discarded for that run. Otherwise a drifting judge silently becomes the
#   thing being measured, and the first sign is people turning the eval off.
JUDGE_MODEL = os.environ.get("LLM_MODEL_JUDGE") or ""

JUDGE_SYS = """You check one answer from a public archive of county meeting
records against the evidence it cites. You are NOT rating it and there is no
score. Report only these three, and nothing else:

UNSUPPORTED - a sentence carrying a citation whose quoted evidence does not
say what the sentence claims. Not "says less than" - contradicts, or is about
something else. A sentence that reasonably summarises its evidence is fine.

OUTSIDE - a statement of fact that no listed evidence could support: something
from the WORLD rather than from this archive. News coverage, what usually
happens at councils, background about a company.

Four things are NOT outside claims, and flagging them is a mistake:
  - SAYING WHAT A TERM OF ART MEANS. "MPUD, a master planned unit development,
    which lets a property be developed as one planned mix of uses"; "the
    Planning Commission, the advisory board that hears rezoning cases before
    the county commission does". The answer is REQUIRED to do this - it is the
    same rule as JARGON below, seen from the other side - and it is what the
    words mean rather than a fact about this county. Flagging it asks for an
    answer that cannot exist: unexplained the term is jargon, explained it is
    outside knowledge.
  - that the archive does not contain something. That is a finding.
  - how the archive itself works: that transcription is machine-made, that
    speaker names come from automated voice matching and are unverified, that
    a meeting has no recording. The archive is required to say these and they
    are true of every answer, not claims about the county.
  - that a meeting's minutes record no outcome.

JARGON - a term of art used without being put in plain words in the same
breath: ordinance, variance, consent agenda, quasi-judicial, MPUD,
continuance, interlocal. Only count it if a resident who does not follow local
government would be stopped by it.

Return JSON exactly:
{"unsupported": [{"claim": "<=15 words", "why": "<=20 words"}],
 "outside": ["<=15 words"], "jargon": ["the term"]}
Empty list where there is nothing. Quote, never paraphrase.

If the request ends with a STATES question, add "states": true or false for
it, judged on meaning and not on wording."""

# Known verdicts. Small on purpose: they are a calibration, not a corpus.
FIXTURES = [
    {"name": "planted fabrication",
     "answer": "The board voted five to nothing to approve the fee "
               "increase [3050].",
     "evidence": "[3050] 2026-01-06 · Mariano: We will take this one up at "
                 "the next meeting.",
     "expect": "unsupported"},
    {"name": "planted outside knowledge",
     "answer": "The board approved the plan [3050]. It was widely covered in "
               "the local press at the time.",
     "evidence": "[3050] 2026-01-06 · Mariano: Motion carries, the plan is "
                 "approved.",
     "expect": "outside"},
    {"name": "clean",
     "answer": "The board approved the plan [3050].",
     "evidence": "[3050] 2026-01-06 · Mariano: Motion carries, the plan is "
                 "approved.",
     "expect": None},
]


def _judge(llm, answer, evidence, model, states=None):
    """One judging call. Returns the parsed verdict, or None if it broke."""
    body = (f"THE ANSWER\n\n{answer}\n\nTHE EVIDENCE IT CITES\n\n{evidence}")
    if states:
        body += f"\n\nSTATES QUESTION: does the answer state, in any words, {states}?"
    try:
        raw = llm.chat([{"role": "system", "content": JUDGE_SYS},
                        {"role": "user", "content": body}],
                       model=model, temperature=0, as_json=True, retries=2)
        out = json.loads(raw)
    except Exception as e:                                    # noqa: BLE001
        print(f"    judge call failed: {type(e).__name__}: {e}")
        return None
    got = {k: out.get(k) or [] for k in ("unsupported", "outside", "jargon")}
    got["states"] = bool(out.get("states"))
    return got


def calibrate(llm, model):
    """Is the judge fit to judge today? Its own fixtures decide."""
    print(f"\n{'=' * 70}\njudge calibration ({model})\n{'=' * 70}")
    fit = True
    for f in FIXTURES:
        v = _judge(llm, f["answer"], f["evidence"], model)
        if v is None:
            fit = False
            print(f"  UNFIT  {f['name']}: no verdict")
            continue
        flagged = {k for k in ("unsupported", "outside") if v[k]}
        ok = (f["expect"] in flagged) if f["expect"] else not flagged
        fit = fit and ok
        print(f"  {'ok  ' if ok else 'UNFIT'}  {f['name']}: expected "
              f"{f['expect'] or 'nothing'}, flagged {sorted(flagged) or 'nothing'}")
    return fit


def evidence_text(agent, r, limit=60):
    """The answer's own citations, rendered EXACTLY as the writer saw them."""
    out = [agent._item_block(i, full=True)
           for i in (r.get("record") or [])[:limit]]
    out += [agent._passage_line(p, width=agent.FULL)
            for p in (r.get("evidence") or [])[:limit]]
    return "\n\n".join(out)

# (video, seconds, what it is, queries that ought to find it)
TARGETS = [
    ("Icp0s5wWMeI", 609.6, "school-zone camera vote",
     ["what was decided about the school zone speed cameras",
      "vote on school zone camera program",
      "did the board approve the school zone speed cameras"]),
    ("Icp0s5wWMeI", 446.4, "school-zone camera motion",
     ["what was decided about the school zone speed cameras"]),
]


def locate(con, video_id, at):
    """The passage covering a moment, addressed by time rather than by id."""
    r = con.execute("""SELECT id, start, text FROM passages
                       WHERE video_id=%s ORDER BY ABS(start-%s) LIMIT 1""",
                    (video_id, at)).fetchone()
    return dict(r) if r else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", action="store_true",
                    help="also run web/agent.py, the loop behind /api/ask")
    ap.add_argument("--device", default="cuda:1")
    ap.add_argument("--no-judge", action="store_true",
                    help="skip the model-read checks; structure only")
    args = ap.parse_args()

    con = db.connect()
    failures = 0
    for video_id, at, what, queries in TARGETS:
        p = locate(con, video_id, at)
        if not p or abs(p["start"] - at) > 30:
            print(f"MISSING  {what}: no passage near {at}s in {video_id}")
            failures += 1
            continue
        seg = con.execute("""SELECT ai.title, ai.code, ai.case_id, ai.outcome,
                                    ai.phase, ai.source
                             FROM passages p LEFT JOIN agenda_items ai
                               ON ai.id = p.agenda_item_id WHERE p.id=%s""",
                          (p["id"],)).fetchone()
        print(f"\n{what}  ->  passage {p['id']} @ {p['start']:.0f}s")
        print(f"  text : {p['text'][:100]}")
        print(f"  item : {(seg['title'] if seg else None) or '(unsegmented)'}")
        if seg and seg["code"]:
            print(f"         code {seg['code']}  case {seg['case_id']}  "
                  f"outcome {seg['outcome']}  ({seg['source']})")
        for q in queries:
            hits = retrieve.search(q, limit=DEPTH, device=args.device)
            rank = next((i for i, h in enumerate(hits, 1)
                         if h["id"] == p["id"]), None)
            ok = rank is not None
            failures += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  rank "
                  f"{rank if ok else f'>{DEPTH}'}   {q!r}")

    if args.agent:
        # The check that actually matters. Retrieval rank at depth 200 is a
        # diagnostic, NOT a pass condition: the agent reads only the top 30 per
        # query, so this eval once reported PASS while the agent was answering
        # "no decision" because the vote sat at rank 56. Assert on the
        # evidence the agent really assembled.
        import os
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"))
        # The citation verify pass is OFF for readers and ON here, which is
        # the whole point of it now: it corrects nothing and detects
        # regressions, so it belongs where its verdict is printed and read
        # rather than in front of somebody waiting for an answer. See
        # agent.VERIFY_ON. Set BEFORE the import - agent reads its environment
        # at module scope, so setting it afterwards would do nothing at all
        # and the `citation moves` line would quietly report zero for ever.
        os.environ.setdefault("ASK_VERIFY", "1")
        import agent
        import tools
        # The agent's tools encode queries too, so the embedding model has to
        # be on the device the caller asked for before the loop starts.
        tools.warm(args.device)
        import ask as llm
        judge_model = JUDGE_MODEL or llm.MODEL
        max_run_seconds = agent.DEADLINE + RUN_SECONDS_SLACK
        fit = False if args.no_judge else calibrate(llm, judge_model)
        if not args.no_judge and not fit:
            # Not counted as an agent failure, because it is not one. It is
            # reported loudly and separately: the instrument is out, so the
            # measurements it would have taken today do not exist.
            print("  the judge is UNFIT — its findings below are suppressed")
        for spec in ANSWERS:
            print(f"\n{'=' * 70}\nagent: {spec['q']}\n{'=' * 70}")
            began = time.monotonic()
            r = agent.ask(spec["q"], con)
            took = time.monotonic() - began
            print(r["answer"])
            print(f"  ({r['looked_at']['passages']} passages and "
                  f"{r['looked_at']['items']} items looked at, "
                  f"{len(r['evidence']) + len(r['record'])} cited"
                  + (f", stopped: {r['stopped']}" if r.get("stopped") else "")
                  + (", graced" if r.get("graced") else "") + ")")
            print("-" * 70)
            cited = {c["id"] for c in r["evidence"]}
            in_videos = {c.get("video_id") for c in r["evidence"]}
            items = {i["id"] for i in r["record"]}

            for vid, at, what in spec.get("moments", []):
                p = locate(con, vid, at)
                ok = bool(p) and p["id"] in cited
                failures += not ok
                print(f"  {'PASS' if ok else 'FAIL'}  cited {what}"
                      + ("" if ok else f" (passage {p['id'] if p else '?'} "
                                       f"@{at:.0f}s in {vid})"))

            # At least one of a set that say the same load-bearing thing. Not
            # "all of them", which would fail an answer for picking the other
            # phrasing of the same fact; not "anything from that recording",
            # which the broken run passed while getting the answer wrong.
            want = spec.get("moments_any") or []
            if want:
                got = [locate(con, v, a) for v, a in want]
                hit = [p for p in got if p and p["id"] in cited]
                failures += not hit
                print(f"  {'PASS' if hit else 'FAIL'}  cited "
                      f"{spec['moments_any_what']}"
                      + (f" [{hit[0]['id']}]" if hit
                         else f" (none of {[p['id'] for p in got if p]} cited)"))

            for iid, what in spec.get("record", []):
                ok = iid in items
                failures += not ok
                print(f"  {'PASS' if ok else 'FAIL'}  cited {what}"
                      + ("" if ok else f" (item {iid} not in the record cited)"))



            # Free on every question, and it is the alarm for the citation
            # layer itself: a struck citation is one the answer leaned on and
            # lost, taking the support for that claim with it.
            struck, fixed = r.get("struck") or [], r.get("repaired") or []
            failures += bool(struck)
            print(f"  {'PASS' if not struck else 'FAIL'}  citations verified"
                  + (f" — STRUCK {struck}" if struck else "")
                  + (f" (repaired {len(fixed)}: {fixed})" if fixed else ""))

            # Not an assertion - the count is not supposed to be zero, and a
            # run where the pass moves nothing is not thereby a better run.
            # It is here because without it the pass is invisible: the line
            # above reports check()'s bracket-form repairs under a name close
            # enough to `recited` to read as if the citation check had spoken,
            # and it had not. Printed so a rise is noticed.
            recited = r.get("recited") or []
            print(f"  ----  citation moves: {len(recited)}"
                  + (f" — {recited}" if recited else ""))

            # Not an assertion either - it is the answer to "where did the
            # five minutes go", which was guesswork before the agent kept
            # these. Printed on every run so a slowdown is dated.
            s = r.get("spend") or {}
            if s:
                print(f"  ----  spend: think {s.get('think')}s over "
                      f"{s.get('rounds')} rounds · tools {s.get('tools')}s "
                      f"over {s.get('calls')} calls · brief "
                      f"{s.get('brief')}s · compose {s.get('compose')}s · "
                      f"verify {s.get('verify')}s · unaccounted "
                      f"{s.get('unaccounted')}s")
                by = sorted((s.get("by_tool") or {}).items(),
                            key=lambda kv: -kv[1])
                print("  ----  tools: "
                      + " · ".join(f"{k} {v}s" for k, v in by))
                # Prompt tokens split by cache, and completion split by what
                # was reasoning. Read beside the seconds: prefix caching
                # already pays for a re-sent prompt, so a phase that is slow
                # on a cached prompt is thinking, and only fewer or shorter
                # calls will make it faster.
                for ph, t in (s.get("tok") or {}).items():
                    tot = t["cache_hit"] + t["cache_miss"]
                    pct = f"{t['cache_hit'] / tot * 100:.0f}%" if tot else "-"
                    print(f"  ----  {ph:8s} prompt {tot:>7,d} tok "
                          f"({pct} cached) · out {t['completion']:>6,d} "
                          f"(reasoning {t['reasoning']:,d})")

            # `stopped` is what the page prints as "it stopped searching early,
            # so this may not be everything". A run with little to find must
            # not claim it ran out of room - that was a real regression, set on
            # the ordinary path, which would have put the warning under every
            # answer the site ever gave.
            if "expect_stopped" in spec:
                ok = r.get("stopped") == spec["expect_stopped"]
                failures += not ok
                print(f"  {'PASS' if ok else 'FAIL'}  stopped flag is "
                      f"{spec['expect_stopped']!r}"
                      + ("" if ok else f" — got {r.get('stopped')!r}"))

            words = len((r.get("answer") or "").split())
            ok = words <= MAX_ANSWER_WORDS and took <= max_run_seconds
            failures += not ok
            # `took` is the time THIS harness spent, and since the citation
            # verify pass runs here and not for readers (agent.VERIFY_ON), it
            # is no longer the time a reader waits. Both are printed so the
            # eval's own number is never mistaken for the page's. The bound
            # stays on `took`, which is the conservative one.
            reader = took - float((s or {}).get("verify") or 0.0)
            print(f"  {'PASS' if ok else 'FAIL'}  {words} words in "
                  f"{took:.0f}s (bounds {MAX_ANSWER_WORDS}w / "
                  f"{max_run_seconds}s) — {reader:.0f}s of it on the reader's "
                  "path, the rest is this harness verifying citations")

            if fit:
                v = _judge(llm, r.get("answer") or "",
                           evidence_text(agent, r), judge_model,
                           states=spec.get("must_say_what"))
                if v is None:
                    print("  ----  judge did not return; nothing asserted")
                else:
                    # Correctness is a failure; readability is reported with a
                    # threshold. One unexplained term in a long answer is a
                    # quibble, four is a different document.
                    for kind in ("unsupported", "outside"):
                        bad = v[kind]
                        failures += bool(bad)
                        print(f"  {'PASS' if not bad else 'FAIL'}  judge: "
                              f"no {kind} claims"
                              + ("" if not bad else f" — {bad}"))
                    if "must_say_what" in spec:
                        ok = v["states"]
                        failures += not ok
                        print(f"  {'PASS' if ok else 'FAIL'}  judge: says "
                              + spec["must_say_what"])
                    jargon = v["jargon"]
                    ok = len(jargon) <= MAX_JARGON
                    failures += not ok
                    print(f"  {'PASS' if ok else 'FAIL'}  judge: "
                          f"{len(jargon)} unexplained terms "
                          f"(bound {MAX_JARGON})"
                          + (f" — {jargon}" if jargon else ""))

            # Nothing should be fetched twice: get_item takes only an item_id,
            # so a second call on one returns exactly what the first did.
            opened = [t["args"].get("item_id") for t in (r.get("trace") or [])
                      if t["name"] == "get_item"]
            twice = {i for i in opened if opened.count(i) > 1}
            failures += bool(twice)
            print(f"  {'PASS' if not twice else 'FAIL'}  no item fetched twice"
                  + (f" — {sorted(twice)} opened again" if twice else ""))

            for c in r["evidence"][:8]:
                # speaker is NULL when nobody was identified - it used to be a
                # rendered stand-in ("Group 465"), so nothing downstream
                # expected None and this line sliced it. The eval's assertions
                # had already passed by the time it raised, which is the worst
                # way to fail. `.get` throughout for the same reason: the agent
                # stores passages as its tools produced them, and get_item's
                # shape is not search's.
                speaker = c.get("speaker") or "unidentified"
                print(f"[{c['id']}] {c.get('meeting_date') or '?'} "
                      f"{int((c.get('start') or 0) // 60):>4}m "
                      f"{speaker[:18]:<18} {(c.get('text') or '')[:58]}")

    print(f"\n{failures} failing checks")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
