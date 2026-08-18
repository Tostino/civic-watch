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

TWO HALVES, AND THEY ARE NOT THE SAME JOB.

A researcher calls the tools, and a writer turns what it found into the answer
a reader gets. They were one prompt and one conversation, which put the rules
for writing at position zero and the writing itself up to 200,000 characters of
tool output later - furthest away exactly when they were needed. Worse, the
half that wrote the answer was reading the research transcript: rejected calls,
failed calls, truncation notices, and every passage that was looked at and set
aside.

So the handover is `brief()`, built from `Seen` and not from the conversation.
The writer gets the question, the evidence, and the researcher's own notes -
and every id in front of it is one the tools really returned, which makes a
fabricated citation something it would have to invent from nothing rather than
misremember from context. `check()` still strikes them either way.

WHAT IT COSTS, measured head to head on four questions against the single
prompt it replaced: a mean of 104s per question against 31s. A run that hits
the evidence or step budget saves a call - it stops and hands over rather than
spending a final call asking for prose it no longer has room to receive - but
a run that finishes of its own accord pays for one, because the researcher's
closing turn is now a handover and the answer is written after it. The worst
of the four came in at 151s against a 420s deadline.

Two things were traded away on the way across and had to be put back, and both
were instructions the single prompt had been carrying implicitly:

  - The researcher stopped early, because "stop when you can answer" is a
    lower bar than "write the answer" - the old prompt made it produce the
    prose, and needing to write what was ARGUED forced it to go and get that.
    It gathered 12 passages where the old one gathered 180, skipped the one
    hearing that held the discussion, and told the reader there had been no
    substantive debate. Hence the completeness test in RESEARCH, which is
    about coverage and not about confidence.
  - The writer did not stop, because a brief reads like a checklist in a way
    a conversation does not. Given 101 pieces of evidence it wrote 2,268 words
    in a 348-second call. Hence LENGTH in COMPOSE, which says in as many words
    that the brief is what it MAY use rather than what it must get through.

Three further properties are load-bearing:

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

# How many times round the loop. Measured on the questions in eval_agent, a
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
# The hard stop, and the reason there are two numbers.
#
# A budget checked between TURNS cannot stop a turn, and a turn is not one
# call: the tool-calling API lets one assistant message carry as many as it
# likes, and this model fans out exactly where it hurts most. Measured across
# four questions: 1.8 to 2.5 calls per turn on the narrow ones, and 8.0 on
# "how have commissioners argued about growth and impact fees, by year" -
# eight lookups chosen blind of each other, any of which can be a get_item
# rendering 250 transcript lines. The second of those eight can spend the
# whole allowance and the remaining six still run.
#
# So the soft cap is where the researcher is TOLD, and given a round to fetch
# anything it must have; the hard cap is where calls stop being executed at
# all and the rest of the batch is refused. 30% is what one more round of a
# fan-out that size costs.
EVIDENCE_HARD = int(MAX_EVIDENCE * 1.3)
# Transcript lines one `get_item` may show. A long public hearing runs to
# hundreds and the cap on the tool itself is 2,000, which is the whole budget
# in one call.
LINES_SHOWN = 250

MODEL = os.environ.get("LLM_MODEL_AGENT") or llm.MODEL_HEAVY

# How hard each stage may think, which is the ONLY dial here that moves the
# wall clock. Measured on "what was decided about the school zone speed
# cameras", 533 seconds end to end:
#
#     think    196s  37%   14 rounds   16,280 reasoning tok
#     compose  143s  27%    1 call     17,320 reasoning tok
#     verify   187s  35%    1 call     22,037 reasoning tok
#     tools      6s   1%   31 calls
#
# 93% of everything generated was reasoning, and the research loop's 411,746
# prompt tokens were a 92% cache hit - so shrinking prompts, which is where
# this investigation started, would have bought almost nothing. Generation is
# serial and uncached; it is the whole bill.
#
# None means "send nothing", the behaviour before the dial existed. Anything
# else has to be earned against bin/eval_agent.py, because the cheapest run
# here is one that answers a resident wrongly.
# Research and compose are left alone deliberately. Every attempt to make
# either think less either did nothing or cost accuracy: at 'low' the writer
# invented "authorized by state law" and a camera vendor's name, and wrote
# that the word "gambling" never appears while citing a passage containing
# "gambling boat". Its deliberation is load-bearing and it is not for sale.
EFFORT_RESEARCH = os.environ.get("ASK_EFFORT_RESEARCH") or None
EFFORT_COMPOSE = os.environ.get("ASK_EFFORT_COMPOSE") or None
# Verify is different, and 'medium' is measured: 187s at full effort against
# 20-45s here, with the same failing-check count over a full eval. It is a
# narrow question asked about one sentence at a time.
#
# It is also on notice. Since the sentence-grouping rewrite it has moved ZERO
# citations across ten consecutive questions, while the judge went on finding
# a claim or two per answer that it structurally cannot reach - those are
# details added on top of a correct bracket, not brackets on the wrong
# passage. The misattachment it exists for stopped happening when COMPOSE
# stopped contradicting itself, which is the better place to have fixed it.
# It stays as insurance, cheaply, and if it is still finding nothing after a
# few weeks of real questions it should go.
EFFORT_VERIFY = os.environ.get("ASK_EFFORT_VERIFY") or "medium"

# OFF for the reader, ON for the eval, and the numbers are why.
#
# Counted over 32 question-runs since the sentence-grouping rewrite: 27 found
# nothing at all; 5 flagged something that stage two then declined to replace,
# leaving the answer untouched; and ONE changed a published answer, which was
# the double-bracket defect that wrote `[[176611]]` onto the page. Zero correct
# corrections, one incorrect one. It cost 12.7% of the wall clock of runs D and
# E - 192s of 1,511s, mean 32s a question - and 27,382 of its 27,452 output
# tokens were reasoning nobody reads. One question spent 67 seconds and 9,845
# reasoning tokens establishing that nothing was wrong.
#
# The class it was built for is real; both instances are recorded below. Both
# were written by the OLD `COMPOSE`, the one that said "cite the clearest one"
# beside "the citation must be the one that holds the fact". Fixing that
# contradiction removed the cause, and this is insurance against a fire whose
# source was plumbed out.
#
# It is not deleted, because it is a genuine REGRESSION DETECTOR: if COMPOSE
# ever drifts back, this number climbs. But a tripwire is worth its price only
# where somebody reads it, and nobody reads it on a reader's request path.
# bin/eval_agent.py sets this and prints the count; that is where it belongs.
#
# `check()` is a different thing and is NOT gated: it strikes ids this run
# never saw, it is a regular expression rather than a model, and it costs
# nothing.
VERIFY_ON = (os.environ.get("ASK_VERIFY") or "").strip().lower() not in (
    "", "0", "no", "off", "false")

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
# What is held back for everything that happens AFTER the last tool call, and
# that is TWO calls now rather than one: the researcher's handover, then the
# writer. The reserve used to be exactly MIN_CALL because the closing call was
# the only one left, and splitting the agent quietly broke that arithmetic -
# the writer would have been guaranteed its floor only by taking the handover's
# time out of it.
#
# A handover is a few sentences, so it is priced as such rather than at a
# second MIN_CALL, which would hold back eight minutes of a seven-minute
# budget and start every run against the floor.
HANDOVER_GRACE = 60
ANSWER_GRACE = MIN_CALL + HANDOVER_GRACE

# The one thing BOTH halves have to know, said once. The researcher needs it to
# search both sources; the writer needs it to keep SAID and DECIDED apart in
# the prose. Copy-pasting it into two prompts is how they would come apart.
SOURCES = """THE ARCHIVE HAS TWO SOURCES AND THEY ARE NOT INTERCHANGEABLE.

  THE RECORD: agendas the county published and the dispositions its approved
  minutes recorded. Authoritative for what was DECIDED. Covers 2015-2026
  whether or not anyone filmed it. Addressed as [item:N].

  THE TRANSCRIPT: machine transcription of 1,036 hours of recordings, 2018
  onward. Authoritative for what was SAID and argued, and roughly for who said
  it. Only 9% of decided items have one. Addressed as [N].

A transcript can show a vote being taken and never its result. Nobody reads
the tally into the microphone. So an OUTCOME comes from the record. An
ARGUMENT comes from the transcript. Answering "what was decided" from
transcript alone is the single most common way to get this wrong."""

RESEARCH = f"""You research questions about Pasco County government meetings by
calling tools. You do NOT write the answer: somebody else does that from what
you gather, so gather it properly and stop.

{SOURCES}

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
   its motion and its vote, because those carry no topic words. get_item does
   not have that problem.
4. If the matter has a case id, call get_case. It reaches every meeting that
   took the case up, INCLUDING ones with no recording, which searching the
   transcript can never do.
5. When a search misses, change the AIM and not the words. Both arms of the
   transcript search run on every call, so the paraphrase has already been
   tried; and the record search stems what you give it and, when no item
   holds all your words, matches ANY of them, so shortening and reordering
   have been done for you too. ONE genuinely different term is worth a try:
   the county's vocabulary rather than the question's. A third and a fourth
   wording are not. Aim it instead, with a facet from the next section. If
   an aimed search finds nothing either, the record does not have it, which
   IS a finding and is worth handing over. "The county published no
   disposition for this" is a real, useful answer. This is about a search you
   have already run; it is not a reason to stop before you have both halves.

AIM THE SEARCH

Both searches take far more than a query. Used well, the facets are how you
stop paying for breadth you did not ask for. Two of them are how you lose the
answer instead, and they are the two that sound most useful:

  NOT phase="public_comment" for "what did people say". It reads like exactly
      the right filter and it is measured to be wrong: the evidence behind
      that answer for the license-plate cameras sat 13 in `other`, 8 in
      public_comment and 7 in board_reports. The filter keeps 8 of 28 and
      loses the Sheriff's Office reply completely. Residents speak inside
      public hearings, and the board answers them somewhere else again.

  NOT speaker= for "how has X argued". 67% of passages carry no usable
      speaker key - every cross-speaker exchange is '(exchange)', and an
      exchange is where an argument actually happens - so it drops two thirds
      of the corpus, keyed on a name this archive infers rather than knows.
      Search the subject and read the names off what comes back.

Both of those fail SILENTLY, which is what makes them worse than a wasted
call: a filter that excludes the answer returns an empty result that looks
exactly like an archive that holds nothing.

The ones that earn their place:

  "by year", "over time", "how did this change"
      since and until, ONE WINDOW AT A TIME: 2021, then 2023, then 2025. A
      decade does not arrive in a single search, and asking for one with a
      large limit buys a pile sorted by relevance rather than a chronology.
      spread=2-3 with it, or the hits bunch into whichever meeting talked
      about it longest.

  "what was decided", "did it pass", "was it ever approved"
      order="decided" on search_record, which SORTS rather than filters and so
      cannot hide anything: settled items float, continued ones sink, and what
      is missing is still on the page. decided=true and outcome= do filter, so
      reach for them second and read an empty result as "not among the
      decided ones" rather than as "not there".

  one application or case
      case="PDE-25-7738" is an exact filter. The same string in `query` is a
      text match, and it also returns every meeting that mentioned it in
      passing.

  one body
      body="Planning Commission". The same case is heard by both bodies and
      they can decide differently, so "what happened at the Planning
      Commission" is a facet and not a wording.

`limit` defaults to 12 deliberately. Raise it where breadth IS the point, not
from habit: every extra hit is paid for twice, once when you read it and again
in the room it leaves for everything after it.

BEFORE YOU STOP

"I could answer this now" is NOT the test, and it is the wrong instinct to
trust: the writer can only use what you actually gathered, and it cannot go
back for more. The test is whether the writer could say, from your evidence
alone, both what was DECIDED and what people ARGUED about it.

So check you have both halves:

  - THE OUTCOME, from the record. If an item disposed of the matter, open it
    with get_item. The disposition is there verbatim, and ranking does not
    reach it.
  - WHAT WAS ARGUED, and by whom, from the transcript. If an item was taken up
    at a meeting that WAS recorded and you have not opened that item with
    get_item, you do not have this yet. Do not conclude from an empty search
    that nothing was said. Search does not reach a discussion that carries no
    topic words, and get_item does.

If a half genuinely is not there (no recording, or nothing on point), that
is a finding, and saying so is what stops the writer inventing it.

THEN HAND OVER

Everything the tools showed you is kept automatically, so you do not have to
repeat it, quote it, or write an answer from it. Reply with a few short
sentences: what you established, what you looked for and did not find, and
anything the writer would otherwise get wrong. Do not write the reader's
answer.

PUT THE ID BESIDE EVERY THING YOU ESTABLISH: `[176584]`, `[item:21129]`, in
the bracket form, not "item 21129" in words. This is the most valuable
part of the handover and the easiest for you to write, because you have just
read the passage and you know which one it was.

The writer does NOT know. It gets the same evidence you did, in a heap of
hundreds of passages, with no record of which one told you what, and the
citation errors it makes are where it guessed. A note reading "the motion to
recommend approval failed [176584]" spares it the guess.

A note carries only what its passage carries. Round nothing, complete no
lists, and add no name the passage did not say. "several other counties
[69110]" is a good note where "Hillsborough, Polk and Sumter" is a bad one
if the passage named two of them. If you are unsure which id something came
from, say so rather than guessing: an untagged note is a small loss, and a
note tagged with the wrong id is a citation error with your name on it.

A SPEAKER NAME MARKED ⚠ IS NOT CONFIRMED. It is the name that voice goes by
across the whole archive rather than anything known about this meeting, so it
belongs in no note: write "a commissioner", "a resident", "a member of staff"
and give the id. Names come from automated voice matching in any case, so if
which person spoke is load-bearing for the answer, say in your note that the
attribution is automated.
"""

# Written by the half that has no tools, from the brief and nothing else.
COMPOSE = f"""You write the final answer for a public archive of Pasco County
government meetings. Somebody else did the looking; you are given everything
they found, and you write what the reader gets.

{SOURCES}

WHO IS READING THIS

Somebody who lives in Pasco County, was not at the meeting, and does not
follow local government. Assume they know none of the procedure and none of
the acronyms. They are reading on a phone and they want the answer, not an
education. Write at the level of a well-written local newspaper.

  - Short sentences, one idea each. Break up anything you would have to read
    twice.
  - Ordinary words. Where a term from the record has to appear (ordinance,
    variance, consent agenda, continuance, quasi-judicial), use it and say
    what it means in the same breath, in plain words.
  - Say what a decision MEANS for a person, not only what it was called.
    "Approved on the consent agenda" is what happened; "approved together with
    dozens of routine items, with no discussion" is what it means. This is
    translation, not addition: you may always explain what a procedure IS,
    because that is what the words mean and not a fact about this county.
    What you may not do is add facts about this case, this board or this
    county that the brief does not carry.
  - No throat-clearing, no "it is worth noting", no summarising what you are
    about to say.

PLAIN IS NOT VAGUE. Simple words, exact facts. Never round a vote, never
soften a disposition, never drop a qualification the record makes, and never
reach for a general phrase because the specific one needs explaining. If
something is uncertain, the plain words for that are "the archive does not
show", not a hedge.

LENGTH. Answer the question and stop. Three or four short paragraphs is the
normal size of an answer, and a question spanning many years or many people is
still answered in a few hundred words.

The brief is what you MAY use. It is not a list of things you must mention,
and working through it is not answering. Where a person said the same thing at
four meetings, say so once and cite it once. A reader who wants all of it can
follow the citations. That is what they are for.

THE NOTES ARE AN INDEX, NOT A SOURCE. The researcher tagged what it
established with the id that established it, which saves you the search. It
does not settle the words: never write a sentence from a note alone. A note
is one line summarising something longer, and a summary drops exactly the
specifics you are about to put your name to. A note about other counties'
programmes became "Hillsborough, Polk, and Sumter" in an answer whose passage
named Hillsborough and Manatee. Take the id from the note, read that passage,
write from the passage.

WHAT YOU MAY CITE

Only the ids in the brief you are given: `[3050]` for something said,
`[item:22216]` for the published record. Copy the form the brief uses: an id
printed there as `[item:22216]` is written `[item:22216]` every time it
appears, because `[22216]` means a transcript passage, there is no passage
with that number, and the citation will be struck out of your answer along
with the support for whatever it was backing. Write `[3050]`, never
"passage 3050".
One id per bracket: `[3069] [3070]`, never `[3069, 3070]` and never a range
like `[3069-3071]`. There is nothing else in front of you and nothing else is
citable. An id from memory is indistinguishable from a real one to the
reader, which makes inventing one the worst thing you can do here.

Write PLAIN PROSE. No markdown, no `**bold**`, no headings, no bullet lists.
Paragraphs only. Anything else arrives on the page as literal asterisks.

NO EM DASHES. Not the mark itself, not an en dash doing its job, not two
hyphens standing in for one. It is the surest tell that a machine wrote the
sentence, and this archive is asking the reader to trust what it says. Every
aside an em dash sets off is better as one of four plainer things: a full stop
and a short new sentence, a pair of commas, a colon in front of the part that
explains, or round brackets around a true aside. Write "approved on the
consent agenda: dozens of routine items together, with no discussion", not the
same sentence with a dash in the middle of it.
A hyphen inside a word is not an em dash and is fine: quasi-judicial,
mixed-use, license-plate cameras. A span takes the word "to", never a dash:
"from 2015 to 2026", "four to three".
Where a passage you are quoting has a dash in it, quote it as the record has
it. The rule is about the punctuation you choose, not the county's.

DO NOT AUDIT YOURSELF AT THE END. You are not the last step: a checker reads
this answer afterwards with each sentence beside the full text of its own
citations, and a separate pass strikes any id that is not real. Spend your
care as you write each sentence, on the passage in front of you and the
words taken from it, and when the last paragraph is done, stop.

THE RULES

- Lead with the answer. No preamble, no restating the question.
- Every factual claim carries a citation: [N] for something said, [item:N] for
  the published record. A claim with no citation will be treated as unsupported.
- A CLAIM ABOUT WHAT IS NOT THERE IS SUPPORTED BY THE SEARCHING, NOT BY A
  PASSAGE. No passage can hold one up, because a passage is something that WAS said,
  and putting the nearest id after one makes it look like that passage says a
  thing it could not: measured, "no tally was read into the microphone"
  arrived citing the roll call itself, the one piece of evidence against it.
  What you have instead is LOOKED FOR AND NOT FOUND at the end of the brief,
  which lists every search that came back empty. Say what was looked for and
  did not turn up ("no agenda item mentioning a casino appears in searches
  of the record from 2015 to 2026"), and that sentence needs no bracket, because
  the searches are its evidence and they are listed there.
  Claim no more than the looking covered. "The searches run here found none"
  is honest; "the archive contains nothing about X" says you read all of it,
  and one sentence of that kind ("the closest the word 'gambling' comes is
  the surname Gamble") was published beside a passage containing "gambling
  boat". Never say a WORD does not appear. You saw a few hundred passages of
  an archive of hundreds of thousands.
- WRITE EACH SENTENCE FROM THE PASSAGES IN FRONT OF YOU, AND CITE ALL OF
  THEM. Not the clearest one: all of them, because a reader clicks a bracket
  and gets that passage alone. If a sentence needs the company's name from one
  passage, the speed from a second and the penalty from a third, it carries
  three brackets. If it would carry more than three or four, it is doing too
  much: split it, and let each part sit beside its own evidence.
- WHAT IS NOT IN THEM DOES NOT GO IN THE SENTENCE, however likely it is. A
  passage reading "to eliminate the opaque wall option" says nothing about
  what replaced the wall, so neither do you. If no passage carries the point,
  say it plainly with no citation, or leave it out. Never move it onto the
  nearest bracket.
- NEVER SAY HOW A NAMED PERSON VOTED from a transcript. Speaker labels come
  from automated voice matching and votes land on the wrong name. In one roll
  call the same label carries both the "Nay" and the next member's "Aye". Who
  voted which way is the one thing a garbled roll call cannot tell you.
  The COUNT is different: if the transcript says "four nays, three ayes", that
  is the transcript speaking and you may report it as such and cite it, even
  where the labels around it are plainly scrambled. What you may not do is
  add up individual votes yourself to reach a number nobody said.
- If the record disposes of the matter, lead with that and give the meeting
  date. Then use the transcript for what was argued and by whom.
- Never contradict a recorded disposition with an inference from the
  transcript. If they disagree, say so and give both.
- If an item has no recorded disposition, say the published record shows no
  outcome. Do NOT infer one from a vote being called.
- If the brief does not settle the question, say so plainly and say what IS
  established. Never fill a gap with plausible inference. "The archive does not
  show this" is a complete and acceptable answer.
- SPEAKER NAMES ARE NOT ALL THE SAME KIND OF CLAIM, and the passage says
  which kind it is. The archive knows how each name was arrived at and marks
  the two ends:
    "⚠ NAME NOT CONFIRMED": DO NOT ATTRIBUTE THIS BY NAME AT ALL. Say "a
      commissioner", "a resident", "a member of staff", and cite the passage
      as normal: the quote is sound, only the name on it is not. Putting a
      real person's name on a vote or an objection they may never have made is
      the worst mistake available here, and it is the one a reader cannot
      check.
    "✓ NAME CONFIRMED": a person established this name. Write it plainly and
      do not hedge it.
    unmarked: matched to a voice at that meeting. Usable, and the ordinary
      case. If a claim turns on exactly who spoke, say the attribution is
      automated and unverified.
  "Several speakers" or "unidentified" means the archive does not know who
  spoke. Never guess. Never quote an accuracy figure.
- Distinguish what was SAID from what was DECIDED, in those words.
- NOTHING FROM OUTSIDE THE BRIEF. Not what you know about Pasco County, not
  what was in the news, not what usually happens at a county commission. An
  answer that reaches past its evidence is making the archive vouch for
  something the archive never saw, and a reader cannot tell which sentence
  that was. Observed: an otherwise careful answer about a casino added "the
  casino-entertainment complex reported in the news in 2024-2025", which is
  uncitable here and may not even be true. If the archive is silent, the
  finding is the silence.
"""

# There is no STOP nudge any more. It existed to make the model write an answer
# after the budget ran out; the writer does that now from `seen`, so a run that
# has run out of room stops calling tools and hands over what it has instead of
# spending its last seconds asking for prose it is too late to get.
#
# What replaces it is smaller and is not about the answer at all: the one round
# a researcher gets after it is told the evidence is spent. Without it, "out of
# budget" lands between two of the eight calls in a fan-out and the run ends
# holding whichever half of the question it happened to have reached.
GRACE = (
    "You have used the evidence budget for this question. You are given a "
    "little more, to finish with rather than to carry on: if there is "
    "something you must have before the answer is written (a disposition you "
    "have not opened, a discussion you know is there), get it in this round. "
    "Otherwise hand over now. Nothing after this round will be fetched."
)


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
        # Items whose get_item output has actually been RENDERED, which is not
        # the same question as whether the id is in `items`: a search puts an
        # item there as a summary row with no verbatim disposition and no
        # transcript, and opening that one is the traversal the whole design
        # asks for. This is only about opening the same item twice.
        self.opened = set()
        # (kind, id) -> times rendered AT FULL WIDTH into the conversation, so
        # a list can say "you have this already" instead of printing it again.
        self.shown = {}
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


def _who(p):
    """Who spoke, as the model should read it.

    `(exchange)` is the key's value for a passage crossing several speakers,
    and it is an internal token: the page refuses to print it (Hits.tsx) and so
    does this. Shared with the reference form below, because the same passage
    reading "several speakers" in one line and "(exchange)" forty lines later
    is the same defect wearing a different hat.
    """
    who = p.get("speaker_display") or p.get("speaker") or "unidentified"
    return "several speakers" if who == "(exchange)" else who


# HOW SURE THE NAME IS, in the archive's own four states.
#
# These are SpeakerChip's (ui/components/SpeakerChip.tsx, R6.2), read off the
# same two fields in the same order, because a reader who follows a citation
# from an answer to the page must not be told two different things about one
# name. That is the whole reason this is not a confidence threshold: it was
# written as `conf >= 0.6` first, and a number is a second precedence rule
# about a question that already has an owner - bin/schema.sql's
# utterance_speaker view, whose own header says a precedence rule stated twice
# is one that will disagree with itself. R5.5.6 forbids showing the number to
# a reader anyway, and a brief is a display like any other.
#
#   confirmed  a person stated this name. 2,786 utterances.
#   inferred   matched to a voice AT THIS MEETING. 208,495, the ordinary case.
#   weak       'cluster': the name this voice goes by across the archive, and
#              no evidence at all about this meeting. 22,682. This is the one
#              that put two different women under one name.
#   several    an exchange - the passage crosses speakers.
#   unknown    no name resolved.
#
# Worst case over the passage's utterances; the reduction is in the database
# (bin/schema.sql, `passage_speaker`) so that every surface reduces alike.
#
# KNOWN AND ACCEPTED: this marks fewer passages than the threshold it replaced
# - 11.7% of personally-named passages against 27.3% - because a voice match
# is one claim here whatever its score, and 12.4% of them score under 0.6.
# Those go unmarked now. That is R5.5.6 applied rather than a gap: the scores
# have not been re-measured since the roster work, so a line drawn through
# them would assert an accuracy this project cannot currently support, and it
# would draw it in a place the page does not. If the measurement is ever
# redone, the place to spend it is the chip - and this follows.
def _name_state(p):
    """SpeakerChip's state for this passage's speaker."""
    who = p.get("speaker")
    if who == "(exchange)":
        return "several"
    if not who:
        return "unknown"
    if p.get("name_human"):
        return "confirmed"
    # A passage whose fields were never filled in reads as the ordinary case,
    # which is what it was before any of this existed.
    return "weak" if p.get("name_basis") == "cluster" else "inferred"


# What each state adds to the line the name is printed on. Said HERE and not
# once in the prompt, because it is true of some passages and not others and
# that difference is the entire point.
#
# Only the two ends are marked. 'inferred' is 89% of the archive and is what
# COMPOSE's standing rule about automated speaker names already describes, so
# marking it would put a warning on almost every line and leave the reader of
# this brief no way to see which ones matter. An unmarked name is an inferred
# one.
#
# An exchange is marked when the names inside it are weak, and it is not the
# same case as a single speaker: the passage prints "Clerk: ...", "Mariano:
# ..." inline in its own text, so there are names in front of the writer even
# though the header says several speakers.
_NAME_NOTE = {
    "confirmed": " ✓ NAME CONFIRMED by a person: you may state it plainly",
    "weak": (" ⚠ NAME NOT CONFIRMED: this is the name the voice goes by "
             "across the archive, not evidence about this meeting. Do not "
             "attribute anything here to them by name."),
}


def _name_mark(p):
    state = _name_state(p)
    if state == "several" and p.get("name_basis") == "cluster":
        return (" ⚠ NAMES NOT CONFIRMED: the names written into this "
                "exchange are archive-wide voice matches, not evidence about "
                "this meeting. Do not attribute anything here by name.")
    return _NAME_NOTE.get(state, "")


# A passage IN FULL, for everything that relies on its words rather than
# skimming them. The longest in the archive is 1,582 characters, so nothing is
# cut at this width and the writer, the citation checker and the eval's judge
# all see the same thing the passage actually says.
#
# 420 - the width for a list being SCANNED - was being used for all four, and
# it silently broke two of them. 42,006 of 166,998 passages (25.2%) are longer
# than that. Measured on one answer, all three of the judge's "unsupported
# claim" findings were the clip and not the answer:
#
#   "prohibits chickens in MF-1, MF-2, MF-3" cited [307332], 986 chars, which
#   says "excluding ER, ER two, MF1, MF two, and MF three" at character 500.
#   "coop size and height are regulated" and "the property must be occupied,
#   not a vacation rental" both cited [307333], 1,165 chars, which says both,
#   at characters 850 and 1,000.
#
# The answer was right about all three and was marked wrong, which is exactly
# what eval_agent.evidence_text's docstring says must never happen again - the
# fix there routed the judge through THIS renderer and stopped one level short
# of the number inside it. It also explains `verify_citations`: stage one
# flags a citation whose clipped text does not carry the sentence, then stage
# two, reading the same clip, can find nothing better and declines. Five flags,
# no moves, and the pass looked like noise when it was reading a quarter of a
# passage.
#
# It costs about 2,400 tokens on a 130-passage brief, on the prompt side,
# which prefix caching and a 92% hit rate make close to free. Generation is
# the bill (gotcha 98); this is not generation.
FULL = 1600


def _passage_line(p, width=420):
    # The display name, so the model writes the name the reader will see under
    # the citation. Shown "Starkey", it wrote "Starkey said" while the chip
    # beneath said Kathryn Starkey, and the answer read like it was about
    # somebody else. `speaker` is still what the speaker facet takes, and
    # tools.canonical_speaker accepts the full name back.
    where = p.get("meeting_date") or p.get("upload_date") or "?"
    head = f"[{p['id']}] {where} · {p.get('body') or ''} · {_who(p)}"
    head += _name_mark(p)
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
    # 220 is right for a row being SCANNED and wrong for the item itself. A
    # county title is not a name, it is the whole proposal in one sentence -
    # 21.3% of the 26,434 items run past 220 characters, and what sits after
    # the cut is what was being built, how big, and where. Item 21129 clipped
    # to "...to Allow 300", a number with no unit, and lost "Multi-Family
    # Units, 100,000 Square Feet of Support Commercial and 1,500,000 Square
    # Feet of Light Industrial Uses on Approximately 80 Acres, Located South
    # of County Line Road North and East of Lake Iola Road". An answer then
    # had to GUESS what the 300 counted, and said so out loud.
    #
    # 600 covers the 95th percentile; the longest in the archive is 2,720 and
    # is a consent block listing every parcel in a resurfacing contract, which
    # nothing is served by reprinting whole.
    out = [head, f"  {_clip(i.get('title'), 600 if full else 220)}"]
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
               -- Same two fields as tools.PASSAGE_HIT, for the same reason the
               -- comment above gives about display_name: these become the
               -- answer's evidence, and a citation reached through get_item
               -- that could not name its recording would print a bare clock
               -- beside one that named the meeting.
               v.session_seq,
               (SELECT count(*)::int FROM videos v2
                 WHERE v2.meeting_id = v.meeting_id) AS sessions,
               m.date AS meeting_date, m.body
          FROM passages p
          JOIN videos v ON v.id = p.video_id
          LEFT JOIN meetings m ON m.id = v.meeting_id
          LEFT JOIN agenda_items ai ON ai.id = p.agenda_item_id
         WHERE p.agenda_item_id = %s
         ORDER BY p.video_id, p.start_idx""", (item_id,)).fetchall()
    # These passages go into `seen` and become the answer's evidence, so they
    # have to carry how sure the name on them is - exactly like a search hit,
    # which is the other way a passage gets here. Without this a name reached
    # through get_item was printed as fact while the same name reached through
    # search was marked unconfirmed.
    return toolkit.speaker_sure(con, [dict(r) for r in rows])


def _at(cover, video_id, idx):
    for p in cover:
        if (p["video_id"] == video_id and p["start_idx"] is not None
                and p["start_idx"] <= idx <= p["end_idx"]):
            return p
    return None


# --------------------------------------------------------- second sightings
#
# A LIST that hands back something already in the conversation prints a
# reference to it instead of printing it twice. Two reasons, and the smaller
# one is the tokens: measured by replaying the recorded call sequences, repeats
# are 1.3% to 13.2% of everything rendered in a run, median about 4%.
#
# The bigger one is that repetition is currently INVISIBLE. On "what was
# decided about the school zone speed cameras" the researcher reworded the same
# record search six times and was handed the same items back 58 times without
# ever being told they were the same, which is a loop it had no way to see it
# was in. The count says so.
#
# Only in lists that are SCANNED - search hits, the steps of a case, a
# meeting's agenda. Never in `get_item`, where the block is the substance of
# what was asked for rather than a row to skim, and never in `brief()`, which
# calls the renderers directly because the writer must have the evidence
# itself. That is also why this lives at the call sites and not inside
# `_passage_line`/`_item_block`.
#
# It is honest only because `msgs` is append-only: "shown above" is true for
# the whole life of a run. Trim the history and these become dangling
# references to something the model can no longer see.
def _again(seen, kind, ident):
    """Times this was already rendered in full. Counts the sighting as it asks."""
    key = (kind, ident)
    n = seen.shown.get(key, 0)
    seen.shown[key] = n + 1
    return n


def _hit(p, seen):
    n = _again(seen, "p", p["id"])
    if not n:
        return _passage_line(p)
    when = p.get("meeting_date") or p.get("upload_date") or "?"
    return f"[{p['id']}] {when} · {_who(p)} · shown above (x{n + 1})"


def _row(i, seen, full=False):
    n = _again(seen, "i", i["id"])
    if not n:
        return _item_block(i, full)
    bits = " ".join(x for x in (i.get("date"), i.get("code")) if x)
    return f"[item:{i['id']}] {bits} · shown above (x{n + 1})"


def _echoes(rows, kind, seen, unit):
    """One line naming how much of a list the model already had."""
    again = sum(1 for r in rows if seen.shown.get((kind, r["id"]), 0) > 1)
    if not again:
        return ""
    out = f"\n\n({again} of {len(rows)} {unit} already shown above."
    # Only when it is most of them: at that point the wording is not the
    # problem and trying another one will not help.
    return out + (" Rewording is returning what you have. Aim the search "
                  "instead.)" if again * 2 >= len(rows) else ")")


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
        body = "\n\n".join(_hit(h, seen) for h in hits)
        return (note + f"{len(hits)} passages:\n\n" + body
                + _echoes(hits, "p", seen, "passages"))

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
        body = "\n\n".join(_row(i, seen) for i in items)
        return (note + f"{result.get('total', len(items))} items, showing "
                f"{len(items)}:\n\n" + body
                + _echoes(items, "i", seen, "items"))

    if name == "get_item":
        # `lines` and `thread` hang off the ITEM, not off the envelope.
        item = result.get("item") or {}
        # Opened already, so say so and stop. `get_item` takes an item_id and
        # nothing else (tools.MANIFEST), which makes a second call on the same
        # id deterministically the same 250 rendered lines - for item 11578,
        # measured, 39,695 characters of context and a fifth of the evidence
        # budget, to re-read something already in `seen`.
        #
        # Observed rather than imagined: on "how have commissioners argued
        # about growth and impact fees", the researcher opened item 11578 in a
        # batch of seven, then opened it again the next turn reasoning "I have
        # the argument from search but not its disposition" - which get_item
        # had already given it. The completeness check was right; it just paid
        # twice for the answer.
        #
        # A prompt line would be advisory against something deterministic, so
        # this is here instead. It returns before `_cover()`, which is the
        # second database round-trip in this branch.
        ident = item.get("id")
        if ident in seen.opened:
            # Name the SECTIONS of the earlier output rather than explain what
            # this tool does. The re-fetch that prompted this reasoned "I have
            # the argument from search but not its disposition" - and the
            # disposition was under a MINUTES: heading in the result it already
            # held. A pointer to that heading is the whole useful content of
            # this reply; the rest was the tool describing itself back to the
            # model that had just called it.
            #
            # Built from what this item actually has, so it can never send the
            # model looking for a WHAT WAS SAID that an unrecorded meeting
            # never produced.
            where = ["MINUTES: for the disposition"]
            if item.get("lines"):
                where.append("WHAT WAS SAID for the transcript")
            if len(item.get("thread") or []) > 1:
                where.append("SAME CASE for its other appearances")
            return f"[item:{ident}] is already above: see {'; '.join(where)}."
        if ident is not None:
            seen.opened.add(ident)
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
            # A census before the transcript, because the shape of a discussion
            # is worth knowing before reading 250 lines of it: how long it ran,
            # how many citable moments are in it, and who did the talking.
            #
            # The speaker counts are the useful part. "Mariano 35, Poole 25,
            # Montcallian 19" answers "is the person I am asking about even in
            # here" in one line, which is otherwise a read of the whole item -
            # and it does it WITHOUT the speaker= filter, which drops the 67%
            # of passages that carry no usable name.
            #
            # Not a phase census: phase is an attribute of the agenda item, so
            # every passage under one item carries the same value and the line
            # would always read "80 regular".
            secs = sum((s.get("end") or 0) - (s.get("start") or 0)
                       for s in (item.get("spans") or []))
            who = {}
            for ln in lines:
                nm = ln.get("display_name") or ln.get("name") or "unidentified"
                who[nm] = who.get(nm, 0) + 1
            top = sorted(who.items(), key=lambda kv: -kv[1])[:5]
            census = ", ".join(f"{nm} {n}" for nm, n in top)
            if len(who) > len(top):
                census += f", +{len(who) - len(top)} more"
            moments = len({p["id"] for p in cover}) if cover else 0
            out.append(
                f"\n{len(lines)} lines"
                + (f" over {secs / 60:.0f} min" if secs else "")
                + (f", {moments} citable moments" if moments else "")
                + f". Who spoke: {census}.")

            # The whole item, in order — this is the tool that recovers a
            # motion and a vote. They sit at the END of an item and carry no
            # topic words, so ranking never reaches them; here they are simply
            # the last few lines. This replaces `decisions_in_play()`.
            #
            # An item can run to 2,000 lines, which alone would exceed the
            # whole evidence budget. When it has to be cut, the END is kept:
            # that is where a board decides things, and it is the half that
            # retrieval could not have found by itself.
            out.append(f"\nWHAT WAS SAID, {len(lines)} lines, in order"
                       + (" (item truncated upstream)" if item.get("truncated")
                          else "") + ":")
            if len(lines) > LINES_SHOWN:
                head, tail = LINES_SHOWN // 3, LINES_SHOWN - LINES_SHOWN // 3
                out.extend(shown(ln) for ln in lines[:head])
                # Name who is in the omitted middle. "83 lines omitted" leaves
                # the researcher unable to tell a gap that matters from one
                # that does not; "omitted: Baird, Sickenes, 4 more" says
                # whether the person it came for is in there. Only 4.2% of
                # items are long enough for this to fire (229 of 5,498).
                gap = lines[head:len(lines) - tail]
                mid = {}
                for ln in gap:
                    nm = ln.get("display_name") or ln.get("name") or "unidentified"
                    mid[nm] = mid.get(nm, 0) + 1
                names = sorted(mid.items(), key=lambda kv: -kv[1])[:4]
                more = f", +{len(mid) - len(names)} more" if len(mid) > len(names) else ""
                # NOT "ask again": calling get_item twice returns this same
                # truncated render, and the repeat guard above refuses it. The
                # only real route to the middle is a transcript search for what
                # was said in it, so that is what it says.
                out.append(f"  … {len(gap)} lines omitted from the middle: "
                           + ", ".join(f"{nm} {n}" for nm, n in names) + more
                           + ". search_transcript for what they said if it "
                           "matters …")
                out.extend(shown(ln) for ln in lines[-tail:])
            else:
                out.extend(shown(ln) for ln in lines)
        else:
            out.append("\n(no recording of this item; the published record "
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
                 "\nNo terminal outcome recorded: it was continued every time, "
                 "or is still open.")
        body = "\n\n".join(_row(s, seen) for s in steps)
        return head + "\n\n" + body + _echoes(steps, "i", seen, "appearances")

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
        return (f"{m.get('date')} {m.get('body')}, {len(items)} agenda items:\n\n"
                + "\n\n".join(_row(i, seen) for i in shown)
                + _echoes(shown, "i", seen, "items") + more)

    return _clip(json.dumps(result), 4000)


def _line(ln, p=None):
    who = ln.get("display_name") or ln.get("name") or "unidentified"
    # The same claim the passage lines carry, one glyph wide. An item runs to
    # 250 rendered lines and cannot afford the sentence; nor does it need it,
    # because only the archive-wide guesses are marked and RESEARCH says once
    # what the mark means. A line has one speaker, so there is nothing to
    # reduce here - `human` and `basis` are the utterance's own.
    if ln.get("basis") == "cluster" and not ln.get("human"):
        who += " ⚠"
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
    """Strike every citation this run did not actually see - and repair the
    ones it did see and merely mislabelled.

    A model asked to cite will cite. Whether the id exists is a separate
    question, and to a reader a fabricated `[item:41203]` is indistinguishable
    from a real one - which makes it worse than no citation at all. So the
    answer is rewritten rather than annotated: an unverifiable citation is
    removed from the prose and reported alongside it.

    THE REPAIR is for a different animal, and telling them apart is the point.
    Measured on a run of the license-plate question: `[39293]`, `[39294]` and
    `[39327]` were struck - three ids the tools really did return, written in
    the wrong bracket. They are agenda items, the writer had seen every one of
    them, and it typed the transcript form. Striking them deleted the citation
    and left the claim standing with nothing behind it, which is the shape of
    failure this function exists to prevent, arriving by its own hand.

    The test is unambiguous because the pools are disjoint in practice and the
    right one is tried first: an id that is not a passage and IS a seen item
    can only be the item, written wrongly. Anything that is in neither pool is
    struck exactly as before.
    """
    bad, fixed = [], []

    def keep(m):
        is_item = bool(m.group(1))
        pool = seen.items if is_item else seen.passages
        other = seen.passages if is_item else seen.items
        out = []
        for tok in IDS.findall(m.group(2)):
            n = int(tok)
            if n in pool:
                out.append(f"[item:{n}]" if is_item else f"[{n}]")
            elif n in other:
                # Seen, mislabelled. Keep the evidence, fix the bracket.
                out.append(f"[{n}]" if is_item else f"[item:{n}]")
                fixed.append(f"[item:{tok}] → [{tok}]" if is_item
                             else f"[{tok}] → [item:{tok}]")
            else:
                bad.append(f"[item:{tok}]" if is_item else f"[{tok}]")
        return "".join(out)

    cleaned = CITE.sub(keep, answer)
    # `[248314] [248314]` reached a real answer twice. It arrives two ways -
    # the writer expanding a list it was told to split, and the repair above
    # moving a bracket onto an id already cited in the same breath - and both
    # look to a reader like the archive stuttering. One id, once.
    cleaned = re.sub(r"(\[(?:item:)?\d{1,7}\])(?:\s*\1)+", r"\1", cleaned)
    cleaned = re.sub(r" +([.,;:])", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    used = {"passages": sorted({int(m.group(2)) for m in CITE.finditer(cleaned)
                                if not m.group(1)}),
            "items": sorted({int(m.group(2)) for m in CITE.finditer(cleaned)
                             if m.group(1)})}
    # Everything reaching the page is now a single-id bracket, so nothing
    # downstream has to know that a list was ever possible.
    return cleaned.strip(), sorted(set(bad)), used, sorted(set(fixed))


# ---------------------------------------------------------------- handover
#
# Built from `seen` and NOT from the conversation, which is the whole point of
# there being two halves. The writer never sees the rejected calls, the failed
# ones, the truncation notices, or the hundreds of passages that were read and
# put aside - and, more importantly, every id in front of it is one the tools
# really returned. `check()` still strikes anything else, but fabrication now
# has to survive a context where there is nothing to fabricate FROM.
#
# It is also why this costs no extra model call in the paths that matter. The
# researcher's last turn is a few sentences of handover rather than a full
# answer, and the budget and step-limit paths lose their closing call entirely
# - they go straight here.
def _said(passages):
    """Transcript passages by meeting, in spoken order, continuations marked.

    They used to be listed in the order the searches happened to return them,
    which scrambles a conversation into a pile of quotations and costs the
    writer two things it cannot get back.

    A passage is a TURN, and a turn's meaning is often in the turn before it.
    Measured: [248469] "we're happy to work on a condition with staff ... on
    any perimeter areas adjacent on the west or north" is the applicant
    agreeing to a LIGHTING condition - the word does not appear because the
    lighting is three turns up. The writer read it correctly and got marked
    wrong, because a citation read alone had lost what it was about.

    And one person at the podium is often several rows. [248433] "I'm amazed
    ... I wanna congratulate staff" and [248434] "Also, as Ryan said, lights"
    are both Nancy Hazelwood, consecutive, one trip to the microphone. Listed
    apart, they read as two residents, and the answer said one neighbour
    praised the plan "while others" worried about lighting. There were no
    others. The evidence for that was already here - same speaker, adjacent
    utterance indices - and nothing in the brief said so.
    """
    rows, seen_vids = {}, []
    for p in passages:
        v = p.get("video_id") or "?"
        if v not in rows:
            rows[v], _ = [], seen_vids.append(v)
        rows[v].append(p)
    def num(x, fallback=None):
        """Ids and indices arrive as int from one tool and str from another,
        and sorting a mixed group raises rather than mis-ordering."""
        try:
            return float(x)
        except (TypeError, ValueError):
            return fallback

    out = []
    for v in seen_vids:                      # meetings keep research order:
        group = sorted(rows[v],              # first found is first shown
                       key=lambda p: (num(p.get("start"), 0.0) or 0.0,
                                      num(p.get("id"), 0.0) or 0.0))
        prev = None
        for p in group:
            # In full: the writer is told to write each sentence FROM the
            # passage in front of it, and a passage in front of it that stops
            # at 420 characters is a different passage.
            line = _passage_line(p, width=FULL)
            end, start = num(prev and prev.get("end_idx")), num(p.get("start_idx"))
            if (prev and _who(prev) == _who(p)
                    and end is not None and start is not None
                    and 0 <= start - end <= 1):
                line += (f"\n  ^ still {_who(p)}, speaking on from "
                         f"[{prev['id']}], one person, not two")
            out.append(line)
            prev = p
    return "\n\n".join(out)


def brief(question, seen, trace, notes):
    """Everything the writer may use, in the order the research met it."""
    out = [f"THE QUESTION\n\n{question}"]

    if notes:
        out.append("NOTES FROM THE RESEARCH\n\n" + "\n\n".join(notes))

    if seen.items:
        # The citation form is stated ON the section, not only in the prompt.
        # Measured: a writer with both sections in front of it wrote three
        # record ids as `[39293]` instead of `[item:39293]`, and the check
        # struck all three - so three supported claims about the published
        # record arrived at the reader with no support at all.
        out.append("THE PUBLISHED RECORD: agendas and minutes. "
                   "Cite anything here as [item:N], exactly as it is written "
                   "below.\n\n"
                   + "\n\n".join(_item_block(i, full=True)
                                 for i in seen.items.values()))
    else:
        out.append("THE PUBLISHED RECORD\n\nNothing from the record was found.")

    if seen.passages:
        out.append("WHAT WAS SAID: transcript passages, grouped by meeting "
                   "and in the order they were spoken. Cite anything here as "
                   "[N].\n\n" + _said(seen.passages.values()))
    else:
        out.append("WHAT WAS SAID\n\nNo transcript passage was found. Most "
                   "meetings were never recorded, so this is usually silence "
                   "rather than absence.")

    # What was tried and came back empty, because "the county published no
    # disposition for this" is a real answer and the writer can only give it
    # if it knows the looking was done.
    # Two different facts, and they used to be one line. A search that matched
    # NOTHING is the evidence behind "the archive does not show this" - the
    # only evidence there can be, since no passage proves an absence. A search
    # that matched things already seen is not: it succeeded. Listing them
    # together offered a successful search as proof that nothing is there.
    nil = [t for t in trace if t.get("ok") and t.get("found") == 0]
    dup = [t for t in trace if t.get("ok") and t.get("found")
           and not t.get("new")]
    if nil:
        out.append("SEARCHED FOR AND MATCHED NOTHING. This is what supports "
                   "a sentence saying the archive does not show something. "
                   "Say what was looked for; it needs no citation.\n\n"
                   + "\n".join(
                       f"  {t['name']}("
                       f"{json.dumps(t['args'], ensure_ascii=False)}) "
                       f"-> 0 matches" for t in nil))
    if dup:
        out.append("SEARCHED AND FOUND ONLY WHAT WAS ALREADY HELD. These "
                   "MATCHED. They say nothing about anything being absent."
                   "\n\n" + "\n".join(
                       f"  {t['name']}("
                       f"{json.dumps(t['args'], ensure_ascii=False)}) "
                       f"-> {t['found']} matches, none new" for t in dup))

    return "\n\n".join(out)


# ------------------------------------------------------- citations, checked
#
# A citation that points at a real passage which does not contain the claim is
# the failure this archive can least afford: the whole promise is that a reader
# clicks through and sees for themselves. `check()` cannot see it - the id was
# genuinely returned by a tool - and neither can any other structural test.
#
# Measured, twice, by hand: "ticket drivers going 11 mph or more over the
# limit" cited a speaker saying "I'm not familiar with it until today" while
# the passage that says "eleven over during the school zone hours" sat two
# sentences away, already cited; and a sentence about an $11m agreement "with a
# clawback" cited a passage that never mentions one, though another cited
# passage does.
#
# TWO PROMPT ATTEMPTS FAILED at this, which is what makes it structural rather
# than a wording problem. The writer is doing recall over thirty passages
# organised by source, and reaches for a plausible neighbour. So it gets a
# second look instead of firmer instructions - and the corrections are applied
# by code, not by asking for the answer again, because a rewrite would put
# every other sentence back in play to fix three brackets.
VERIFY = """Below is a numbered list. Each entry is ONE sentence from an
answer and EVERY citation it carries, with the text of each.

Read the citations of an entry together. A sentence may take one fact from one
passage and another from the next; that is correct, and each citation only has
to carry the part it is there for.

Name a citation only when it carries no part of its sentence: the passage is
about something else, or it is the wrong meeting, body or date. A weaker
source than its neighbour is not a fault.

Return JSON exactly:
{"bad": [{"n": <entry number>, "cite": "the citation as printed, e.g. [1234]"}]}

Empty list if every sentence is held up by its own citations."""

# Stage two, and it runs only for what stage one named. The two were one call
# and that call cost 187 seconds - 35% of a whole answer - because finding a
# replacement means searching the brief, and asking for one alongside every
# verdict made it search 40,000 tokens for every citation it had any doubt
# about. Most answers have nothing wrong with them, so most answers now never
# pay for this at all.
REPLACE = """Each entry below is a sentence, a citation in it that does not
support it, and the evidence available.

Give the id that does hold the claim, or null. Null is the normal answer: the
brief often does not contain the point at all, and a citation left where it is
can be checked again later, while a wrong one cannot.

Two items can have nearly the same title and be different meetings of
different bodies, so an id only counts if its body and date match the
sentence.

Return JSON exactly:
{"fix": [{"n": <entry number>, "right": "id, or null"}]}"""

CITE_TOKEN = re.compile(r"\[(item:)?(\d{1,7})\]")

# A sentence ends at .?! followed by space and a capital, or at the end of the
# answer. Splitting on a bare "." would cut "$1.5 million" in half - and money
# and rates are most of what gets argued about here - handing the checker the
# fragment "5 million ... [3050]" and inviting it to flag a citation that is
# fine. `Mr.` and friends are the same bug wearing a hat.
SENT_END = re.compile(r"[.?!][\"')\]]*\s+(?=[A-Z0-9\"'(])")
ABBREV = re.compile(r"\b(?:[A-Z]|Mr|Mrs|Ms|Dr|St|Ave|Rd|Blvd|Hwy|No|Inc|"
                    r"Co|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec|"
                    r"approx|est|vs)\.\s*$")


def _sentence(text, at, upto):
    """The sentence of `text` containing the span [at, upto). Half-open."""
    start, end = 0, len(text)
    for m in SENT_END.finditer(text):
        if ABBREV.search(text[max(0, m.start() - 12):m.end()]):
            continue
        if m.end() <= at:
            start = m.end()
        elif m.start() >= upto:
            end = m.start() + 1
            break
    return start, end


def _groups(answer, seen):
    """Each SENTENCE of the answer with all of its citations and their text.

    By sentence and not by citation, which is what this did first and which
    was wrong in a way worth recording. Asking "does this passage support this
    sentence" once per bracket sounds stricter and is simply unanswerable for
    a sentence carrying five of them: each passage holds a fifth of it, every
    one of them reads as unsupported alone, and the pass flagged all five.
    Measured on one answer: 21 flags, 18 of them that shape. A sentence is
    supported by its citations TOGETHER, so they have to be judged together.

    The text comes from the same renderers the brief uses. Building a shorter
    one here cost an answer its correctness: item 21129 (Board of County
    Commissioners, 15 July) and item 20439 (Planning Commission, 3 April)
    have near-identical titles, the ad-hoc text left out body and date, and
    the checker - shown two things it could not tell apart - "fixed" the right
    citation into the wrong one. Whatever the checker judges, it must see
    everything the writer saw.
    """
    out, by_span = [], {}
    for m in CITE_TOKEN.finditer(answer):
        n, is_item = int(m.group(2)), bool(m.group(1))
        src = (seen.items if is_item else seen.passages).get(n)
        if not src:
            continue                      # check() strikes it later
        span = _sentence(answer, m.start(), m.end())
        g = by_span.get(span)
        if g is None:
            g = {"span": span,
                 "sentence": " ".join(answer[span[0]:span[1]].split()),
                 "cites": []}
            by_span[span] = g
            out.append(g)
        if any(c["tok"] == m.group(0) for c in g["cites"]):
            continue                      # same id twice in one sentence
        # Whatever the checker judges, it must see everything the writer saw -
        # which is the paragraph above, and which the 900 below was quietly
        # undoing: `_passage_line` had already cut the text to 420, so raising
        # the outer limit did nothing at all for a passage.
        text = (_item_block(src, full=True) if is_item
                else _passage_line(src, width=FULL))
        g["cites"].append({"tok": m.group(0), "text": _clip(text, FULL + 400)})
    return out


def verify_citations(answer, seen, model, timeout, brief_text):
    """Two passes, the second only if the first found something.

    Returns the answer with misattached brackets moved, and a log of what was
    done. It MOVES and never deletes - see the note further down - and it
    only ever moves onto an id this run actually saw.
    """
    groups = _groups(answer, seen)
    if not groups:
        return answer, []
    listing = "\n\n".join(
        f"{i + 1}. SENTENCE: {g['sentence']}\n"
        + "\n".join(f"   CITATION {c['tok']} SAYS: {c['text']}"
                    for c in g["cites"])
        for i, g in enumerate(groups))
    try:
        raw = llm.chat(
            [{"role": "system", "content": VERIFY},
             {"role": "user", "content": listing}],
            model=model, temperature=0, as_json=True, retries=1,
            timeout=timeout, effort=EFFORT_VERIFY)
        bad = (json.loads(raw) or {}).get("bad") or []
    except Exception as e:                                    # noqa: BLE001
        print(f"citation verify skipped: {type(e).__name__}: {e}",
              file=sys.stderr)
        return answer, []
    if not bad:
        return answer, []

    # Only now is the brief worth sending, and only for the few entries that
    # need one.
    asked = []
    for b in bad:
        i = (b.get("n") or 0) - 1
        cite = str(b.get("cite") or "").strip()
        if 0 <= i < len(groups) and any(c["tok"] == cite
                                        for c in groups[i]["cites"]):
            asked.append({"n": i + 1, "cite": cite})
    if not asked:
        return answer, []
    want = "\n\n".join(
        f"{a['n']}. SENTENCE: {groups[a['n'] - 1]['sentence']}\n"
        f"   THIS CITATION DOES NOT SUPPORT IT: {a['cite']}"
        for a in asked)
    try:
        raw = llm.chat(
            [{"role": "system", "content": REPLACE},
             {"role": "user", "content": f"{want}\n\nEVIDENCE AVAILABLE\n\n"
                                         f"{brief_text}"}],
            model=model, temperature=0, as_json=True, retries=1,
            timeout=timeout, effort=EFFORT_VERIFY)
        found = {f.get("n"): f.get("right")
                 for f in ((json.loads(raw) or {}).get("fix") or [])}
    except Exception as e:                                    # noqa: BLE001
        print(f"citation replace skipped: {type(e).__name__}: {e}",
              file=sys.stderr)
        found = {}
    fixes = [{"n": a["n"], "cite": a["cite"], "right": found.get(a["n"])}
             for a in asked]

    applied = []
    # Latest span first, so replacing one does not move the others.
    for f in sorted(fixes, key=lambda f: -(f.get("n") or 0)):
        i = (f.get("n") or 0) - 1
        if not 0 <= i < len(groups):
            continue
        g, right = groups[i], f.get("right")
        bad = str(f.get("cite") or "").strip()
        if not any(c["tok"] == bad for c in g["cites"]):
            continue                      # not a citation this sentence has
        start, end = g["span"]
        sentence = answer[start:end]
        if bad not in sentence:
            continue
        # A replacement id has to be one this run really saw, or the repair
        # would be the very thing it exists to stop.
        #
        # Normalised first, because the id comes back in whatever shape the
        # model felt like: `69083`, `[69083]`, `item:21129`, `[item:21129]`.
        # Taken literally it produced `[[69083]]` on the page, and an
        # `[item:N]` reply failed its own startswith("item:") test - so it was
        # looked up among the PASSAGES, where that number is a different
        # record entirely. Both were live bugs; neither was visible in the
        # log, which printed the model's string and not what was written.
        r = str(right or "").strip().strip("[]")
        is_item = r.startswith("item:")
        n = int(re.sub(r"\D", "", r) or 0)
        ok = bool(n) and (n in seen.items if is_item else n in seen.passages)
        tok = f"[item:{n}]" if is_item else f"[{n}]"
        # Never onto an id the sentence already carries. That "move" changes
        # nothing a reader can see and the log reads `[item:21129] →
        # [item:21129]`, which is how this was noticed.
        if ok and tok in sentence:
            ok = False
        if not ok:
            # It MOVES a citation and never removes one. Deleting the bracket
            # was the first behaviour here and it is worse than the defect: a
            # citation on the wrong passage is a lead a reader can follow and
            # this check can flag again, while a deleted one leaves the claim
            # looking unsupported and takes the trail with it. Flagged and
            # left alone.
            applied.append(f"{bad} flagged, no verified replacement, left in place")
            continue
        answer = answer[:start] + sentence.replace(bad, tok) + answer[end:]
        applied.append(f"{bad} → {tok}")
    return re.sub(r"[ \t]{2,}", " ", answer), applied


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
    msgs = [{"role": "system", "content": RESEARCH},
            {"role": "user", "content": question}]
    # `stopped` is the page's "this may not be everything" flag and stays None
    # for a run that finished because it was finished; `why` is what the stream
    # says it is doing, which is never None.
    trace, stopped, why, notes = [], None, "gathered", []
    # The soft cap is offered once. Twice would not be a budget.
    graced = False
    # Where the wall clock went. Always on, because the answer to "why did
    # that take five minutes" was guesswork until this existed, and the
    # numbers cost nothing to keep. `think` is the research model deciding
    # what to do next, `tools` is the archive answering, and the gap between
    # their sum and the total is time nobody has claimed.
    spend = {"think": 0.0, "tools": 0.0, "brief": 0.0, "compose": 0.0,
             "verify": 0.0, "rounds": 0, "calls": 0, "by_tool": {},
             "tok": {}}
    t_start = time.monotonic()

    def meter(phase, t0, snap):
        """Seconds AND tokens for one phase, tokens split the way the bill is.

        Timing alone pointed at prompt size, which prefix caching mostly pays
        for already: this conversation is append-only, so every round after
        the first re-sends its history as a cache hit. What caching cannot
        touch is generation, and on a reasoning model most of the generated
        tokens are reasoning nobody ever sees. So the two numbers have to be
        read together - a phase that is slow on a small prompt is thinking,
        not reading.
        """
        spend[phase] = round(spend.get(phase, 0.0) + time.monotonic() - t0, 2)
        now = dict(llm.USAGE)
        d = {k: now.get(k, 0) - snap.get(k, 0)
             for k in ("cache_hit", "cache_miss", "completion", "reasoning")}
        got = spend["tok"].setdefault(phase, dict.fromkeys(d, 0))
        for k, v in d.items():
            got[k] += v

    def note(text):
        """The researcher's own words, kept for the writer. Free - it is
        already in the conversation - and it is the only thing the handover
        would otherwise lose: which of the evidence mattered, and why."""
        text = (text or "").strip()
        if text:
            notes.append(text)

    for step in range(max_steps):
        emit("thinking", step=step + 1)
        # The per-call timeout is whatever is LEFT of the budget, less the
        # room the closing answer needs. Without that subtraction a single
        # slow call spends the whole allowance and the reader gets the
        # timeout instead of the answer it was gathering evidence for.
        t0, snap = time.monotonic(), dict(llm.USAGE)
        reply = llm.chat_raw(msgs, model=model, temperature=0.2,
                             timeout=left(ANSWER_GRACE), retries=2,
                             effort=EFFORT_RESEARCH,
                             tools=[{"type": "function", "function": t}
                                    for t in toolkit.MANIFEST])
        meter("think", t0, snap)
        spend["rounds"] += 1
        calls = reply.get("tool_calls") or []
        if not calls:
            # Done looking, of its own accord. What comes back is a handover,
            # not an answer.
            #
            # `stopped` stays None here on purpose: the page reads it as "it
            # stopped searching early, so this may not be everything"
            # (Answer.tsx), which is true of the budget and step-limit exits
            # and false of this one. The reason still reaches the stream below.
            note(reply.get("content"))
            why = "gathered"
            break

        # The assistant turn has to go back verbatim, tool_calls and all, or
        # the next turn's tool results have nothing to attach to.
        note(reply.get("content"))
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
            before = len(seen.passages) + len(seen.items)
            # Checked HERE, between calls, because between turns is too late:
            # the rest of a batch runs whatever the totals say by then. A
            # refusal is a fact about this call and not a reason to lose the
            # run, so it takes the same shape as any other unusable result -
            # text back to the model, `ok` false, the loop carries on - and it
            # still gets a tool message, because every tool_call id must be
            # answered or the next turn has nothing to attach its results to.
            # The ceiling MOVES: the soft cap stops the batch that reaches it,
            # and only the grace round is allowed to spend up to the hard one.
            #
            # It was a single hard ceiling, and that made the soft cap dead
            # code on the only question that ever reached it. A batch of eight
            # get_item calls runs while it is under the hard cap and the call
            # that crosses it can add fifty thousand characters by itself, so
            # the batch ended PAST hard, the grace round was skipped as
            # pointless, and "you have a little more if you need it" was never
            # said to anybody. Measured: graced=False on the one question in
            # four that exits on the evidence budget.
            ceiling = EVIDENCE_HARD if graced else MAX_EVIDENCE
            spent = seen.chars >= ceiling or time.monotonic() >= ends_at
            t_call, found = time.monotonic(), None
            try:
                if spent:
                    # Not a ToolError: that type means "a bad call", and this
                    # call was never made. The distinction is the model's as
                    # much as the reader's - a rejected call invites a fixed
                    # one, and this one must not.
                    text = ("Not run: the budget for this question is spent. "
                            "Nothing further will be fetched. Hand over what "
                            "you have.")
                    ok = False
                else:
                    result = toolkit.call(con, name, args)
                    text = render(name, result, seen, con)
                    ok = True
                    # How many it MATCHED, which is not how many were new.
                    # An absence claim rests on this number being zero, and
                    # `new` cannot carry it: a search returning ten passages
                    # already seen also has new=0, and listing that under
                    # "looked for and not found" would offer a search that
                    # succeeded as proof that nothing is there.
                    found = len(result.get("hits") or result.get("items") or
                                ([result["item"]] if result.get("item") else []))
            except toolkit.ToolError as e:
                # Handed BACK to the model rather than raised. A wrong argument
                # is something it can fix on the next turn, and killing the run
                # over it would throw away the work already done.
                text, ok = f"That call was rejected: {e}", False
            except Exception as e:                            # noqa: BLE001
                text, ok = f"That call failed: {type(e).__name__}: {e}", False
            took = time.monotonic() - t_call
            spend["tools"] += took
            spend["calls"] += 1
            spend["by_tool"][name] = round(
                spend["by_tool"].get(name, 0.0) + took, 2)
            seen.chars += len(text)
            # `new` is what the call actually added to the citable pool, which
            # is how the handover can say a search was run and found nothing.
            trace.append({"name": name, "args": args, "ok": ok,
                          "chars": len(text), "seconds": round(took, 2),
                          "found": found,
                          "new": len(seen.passages) + len(seen.items) - before})
            emit("tool_done", id=c.get("id"), name=name, ok=ok,
                 passages=len(seen.passages), items=len(seen.items))
            msgs.append({"role": "tool", "tool_call_id": c.get("id"),
                         "content": text})

        # Both budgets end the run the same way, and the time one is checked
        # here rather than at the top of the loop so a step that has already
        # paid for its evidence gets to contribute it.
        # Both budgets end the run the same way, and the time one is checked
        # here rather than at the top of the loop so a step that has already
        # paid for its evidence gets to contribute it.
        #
        # Neither asks the model for anything now. The evidence is in `seen`
        # either way, so the run goes straight to the writer - which is one
        # fewer call than this used to make on exactly the questions that had
        # already run out of room.
        out_of_time = time.monotonic() >= ends_at
        # No gate on the hard cap here any more: the moving ceiling above stops
        # the batch AT the soft cap, so reaching this point over budget means
        # there is a grace round's worth of room left by construction.
        if (seen.chars >= MAX_EVIDENCE and not graced and not out_of_time):
            # The soft cap: say so, and give it one round to finish. Time gets
            # no grace round - the deadline is a promise to somebody watching
            # a page, and there is nothing to spend it on but waiting.
            #
            # `stopped` is set HERE rather than at the break below, and stays
            # set however the run ends. The reader's question is "might there
            # be more than this", and once the evidence budget has bitten the
            # answer is yes - including when the researcher takes its grace
            # round, decides it has enough and hands over neatly, which from
            # the outside is indistinguishable from a run that never ran out.
            graced = True
            stopped = "evidence budget"
            msgs.append({"role": "user", "content": GRACE})
            continue
        # `graced` alone ends it: reaching here with the flag set means the
        # grace round has had its batch, and GRACE promised in as many words
        # that nothing after it would be fetched. Letting the loop carry on to
        # the hard cap would spend two or three more rounds and make a liar of
        # the message - the hard cap's job is to stop a fan-out INSIDE the
        # grace round, not to hand out another one.
        if graced or seen.chars >= MAX_EVIDENCE or out_of_time:
            stopped = why = ("time budget" if out_of_time
                             else "evidence budget")
            break
    else:
        stopped = why = "step limit"

    # ------------------------------------------------------------ the writer
    # A fresh conversation. It gets the question, what was found, and the
    # researcher's notes - and none of the machinery that found it.
    emit("answering", why=why)
    t0 = time.monotonic()
    written = brief(question, seen, trace, notes)
    spend["brief"] = round(time.monotonic() - t0, 2)
    t0, snap = time.monotonic(), dict(llm.USAGE)
    answer = llm.chat_raw(
        [{"role": "system", "content": COMPOSE},
         {"role": "user", "content": written}],
        model=model, temperature=0.3, timeout=left(), retries=1,
        effort=EFFORT_COMPOSE,
    ).get("content") or ""
    meter("compose", t0, snap)

    # The second look, and only where somebody reads its verdict - see
    # VERIFY_ON. Skipped entirely rather than run and ignored: it is a model
    # call, and the reader is waiting for it.
    repaired_cites = []
    if VERIFY_ON:
        emit("checking")
        t0, snap = time.monotonic(), dict(llm.USAGE)
        answer, repaired_cites = verify_citations(answer, seen, model, left(),
                                                  written)
        meter("verify", t0, snap)

    answer, struck, used, fixed = check(answer, seen)
    spend["tools"] = round(spend["tools"], 2)
    spend["total"] = round(time.monotonic() - t_start, 2)
    # What no phase claimed. If this is ever large the instrument is lying and
    # should be believed less than the clock.
    spend["unaccounted"] = round(
        spend["total"] - sum(spend[k] for k in
                             ("think", "tools", "brief", "compose",
                              "verify")), 2)

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
        # Citations that were kept because the id was seen, having been written
        # in the wrong bracket. Reported and not silent: a rising number here
        # says the brief is confusing its two sections, which is a fixable
        # thing, and a silent repair would hide it.
        "repaired": fixed,
        # Brackets moved off a passage that did not hold their claim. Reported
        # rather than silent: if this climbs, the writer is guessing.
        "recited": repaired_cites,
        # Seconds per phase. Here so a slow answer can be diagnosed from a
        # run rather than reproduced under a profiler.
        "spend": spend,
        "stopped": stopped,
        # Whether the soft cap fired and the researcher was given its round.
        # Reported rather than inferred: "turns went up and so did the passage
        # count" is consistent with a grace round and with the model simply
        # taking another turn, and those are different things to be told.
        #
        # It is also the number worth watching over time. If most questions
        # start needing the grace round, the evidence budget is too small for
        # what readers are asking - which is a fact about the questions, and
        # nothing else in this system would report it.
        "graced": graced,
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
