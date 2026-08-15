# Pasco Meetings — UI rebuild requirements

Status: draft for build. Supersedes the five hand-written pages in `web/`.
Companion to `STATE.md`, which describes the pipeline that produces this data.

---

## 1. What this is

A public, searchable, citable record of Pasco County government meetings —
1,036 hours of video across the Board of County Commissioners and the Planning
Commission, joined to the county's own published agendas and minutes, with an
LLM research assistant on top that answers questions and shows its evidence.

Two audiences, and they are **not** the same product:

| | who | needs |
|---|---|---|
| **Reading** | residents, reporters, applicants, staff | find what happened, see the official outcome, play the moment, share a link |
| **Curation** | the archive maintainer | fix speaker attribution, confirm identities, spot pipeline damage |

The current UI puts a data-curation tool ("Workbench") in the same navigation
as the reading surfaces. That is the single largest reason it feels incoherent,
and this rebuild separates them: **curation moves behind authentication and
gets its own shell.** A reader must never see it.

---

## 2. The one idea the whole UI hangs on

**There are two kinds of truth here and they must never be blurred.**

| | source | authority | error mode |
|---|---|---|---|
| **The record** | county agendas + approved minutes | authoritative. The county's own words. | missing (not wrong) |
| **The derived layer** | ASR transcript, voice-matched names, LLM segmentation and answers | inferred | plausibly wrong |

A transcript can show a vote being *taken* — "all in favor say aye" — and can
never show its *result*, because nobody reads the tally into the microphone.
The result is in the minutes. Conversely, the minutes never show what was
argued, who objected, or what a resident said at the podium. **Neither layer is
complete and the UI's job is to make clear, at every point, which one the
reader is looking at.**

**R2.1** Every fact displayed MUST be attributable to one of the two layers, and
that attribution MUST be visible without interaction (a badge, a border
treatment, a typographic register — not a tooltip).

**R2.2** The two layers MUST be visually distinguishable at a glance, using a
consistent treatment across every surface. Record content reads as document;
derived content reads as transcript.

**R2.3** Derived content MUST NOT be styled to look more certain than it is. No
confident-looking speaker name without an indication of how it was established.

**R2.4** Where the two disagree, the UI MUST show both and MUST NOT resolve it
silently.

---

## 3. What the data actually supports

Measured, not assumed. These numbers are the reason for several requirements
below; re-derive them before trusting this section. **Re-derived 2026-08-13**;
the figures this section was written against are kept alongside where they
moved, because a requirement argued from a number should show when the number
shifts under it.

| | | as first written |
|---|---|---|
| meetings | 1,251 (2015-01-13 → 2027-01-14) | 1,249 |
| ...with a recording | 283 | 283 |
| videos / hours | 432 / 1,036 | same |
| agenda items | 27,138 (23,123 published) | 26,428 (23,122) |
| ...with a recorded outcome | 17,531 | 17,988 |
| **...of those, bound to a recording** | **9%** | 1,622 (9%) |
| cases (applications) | 20,275 — 1,377 span more than one meeting | same |
| utterances | 298,737 | same |
| ...with a resolved speaker name | **204,146 (68.3%)** | 235,397 (78.8%) |
| passages (search/answer unit) | 167,174 | 167,083 |
| board members (2 bodies) | 28 | same |

The speaker-name row is the one that moved far enough to matter, and it is not
a regression in the method: `bin/name_speakers.py` calls a paid model and the
inference account is empty, so that stage has not run against the current
archive. STATE.md records the whole progression (84.9% → 78.8% → 53.5% →
68.3%) and why each step moved.

Three consequences the design MUST absorb:

**R3.1 Most decisions have no video.** Only 9% of decided items are bound to a
recording. A UI built around "play the moment" fails for 91% of the record. The
record page MUST be complete and useful with no video at all, and MUST say
plainly when no recording exists rather than rendering an empty player.

**R3.2 Coverage is uneven per object, not globally.** A meeting may have any
combination of agenda, minutes and recording. An item may have a disposition,
a recording, both or neither. **Each object MUST carry its own coverage state.**
A single site-wide disclaimer is not acceptable — it trains readers to ignore it.

**R3.3 A third of utterances have no speaker name** — 31.7% as of 2026-08-13,
24% when this was written, and the difference is the unrun paid naming stage
rather than a change of method. Either way the requirement is the same, and the
larger number only makes it sharper: unnamed is the normal case, not an error
state, and MUST be designed for rather than papered over.

---

## 4. Information architecture

### 4.1 Entities that get a URL

Every one of these is a thing a person refers to, so every one is addressable,
shareable and deep-linkable.

The middle column is the state that MOTIVATED each requirement — the app as it
was before the rebuild — and is kept as written so the reasoning still reads.
The last column is where it stands.

| route | entity | before the rebuild | now |
|---|---|---|---|
| `/` | the archive — browse by time and body | a bare search box | **built** (slice 5) |
| `/ask` | research assistant | exists, flat output | **built** (slice 4) |
| `/ask/:id` | one answer the archive gave, kept | **missing** — `?q=` re-ran the agent on the recipient | **built** |
| `/search` | search across record + transcript | searches transcript only | **built** (slice 3) |
| `/meeting/:id` | one meeting: agenda spine, roster, recording, transcript | **missing** — trapped in a modal inside search | **built** (slice 1) |
| `/item/:id` | one agenda item | a stopgap page nothing linked to | **built** (slice 2) |
| `/case/:id` | one application across meetings | a stopgap page nothing linked to | **built** (slice 2) |
| `/person/:id` | a board member: terms, attendance, participation | **missing** | not built |

A body (BCC / Planning Commission) does **not** get its own route. Everything
such a page would show — members over time, the meeting list — is Browse
filtered by body (R5.1.2), and a separate route would fork the zoom axis for no
new capability. People are listed from Browse rather than from a body page.

**R4.1** Every entity MUST be reachable without knowing a URL — through the
navigation, or through an index that the navigation reaches. Being linked only
from a sibling page is not enough: `/item` and `/case` existed before the
rebuild and appeared in no navigation and no index, which is why nobody found
them. (Now satisfied via the meeting record and the case thread: one BCC
meeting carries 186 item links and 185 case links. Consent items count —
they are 150 of a 200-item agenda, and leaving them unlinked would re-create
the same hole.)

**R4.2** A URL MUST encode enough state to reproduce the view, including
active filters, and — where a recording is in play — the timestamp.

**R4.3** There MUST be no dead ends. Every entity links to its neighbours: item
→ meeting, item → case, item → speakers heard; case → items; meeting → items,
→ roster; person → meetings, → items.

**R4.4** Every item and meeting MUST link to its record on the county portal.
Three of the three civic archives reviewed do this (`PRIOR_ART.md`); we hold
`portal_event_id` and currently link nowhere. For a project whose thesis is
that the published record is authoritative, refusing to point at it is
indefensible.

**R4.5** A command palette (`⌘K` / `/`) MUST provide search and jump-to-entity
from any surface. With a three-item navigation this is what keeps the deeper
entities reachable (R4.1) without growing the nav bar.

### 4.2 Navigation

Public shell: **Browse · Search · Ask · About**, plus a persistent player.
Nothing else.

*About joined the nav on 2026-08-13, at the maintainer's direction, and the
site footer that used to carry it was deleted.* It was a footer on the reasoning
that a reader looks for "what is this, and can I trust it" at the foot of the
page. Against that: it cost every page 231px of flow beneath the content, which
on the meeting page turned into a page scrollbar that the reading panes ate the
wheel for; and its blanket "this is not the official record", repeated on every
page, is the single site-wide disclaimer R3.2 already refuses. The per-object
coverage states R3.2 requires are what carry that weight.

Admin shell: separate layout, visibly different chrome, authenticated. Never
linked from the public shell.

---

## 5. Surfaces

### 5.1 Browse (`/`) — the archive as an object

Currently a search box, which answers nothing about what is in the archive.

**R5.1.1** MUST show the shape of the collection on arrival: date range, the two
bodies, meeting counts, hours, and what fraction has agenda / minutes /
recording.

**R5.1.2** MUST present meetings on a **time axis** — this collection is 12
years of a recurring event and time is its natural spine. Support scanning by
year → month → meeting, and filtering by body.

**R5.1.3** Each meeting in the list MUST show its coverage state (agenda /
minutes / recording) and its item count, so a reader can tell what they will
get before clicking.

**R5.1.4** MUST offer curated entry points alongside the time axis — *where the
board disagreed*, *most-continued matters*, *recent decisions*. These are
saved queries with names, and all three are derivable from data already held:
dissent is recorded verbatim in the minutes ("with Ms. Pearson voting nay"),
and continuances are counted per case. A search box alone requires the reader
to already know what to ask.

### 5.2 Meeting (`/meeting/:id`) — the missing centre of the app

This was the page the old UI most conspicuously lacked, and it is the one a
reader most wants. Built in slice 1.

**R5.2.1** MUST render the **agenda as a spine**: every item with its code,
title, outcome and — where a recording exists — its time offset and duration.
This doubles as a table of contents and as a seek control. Ordering is R5.2.6.

**R5.2.2** MUST show who was present, from `meeting_roster` (the published
roster, not inferred), with officers marked.

**R5.2.3** MUST offer a transcript reading view synchronised to the player:
clicking a line seeks; playing scrolls. The transcript MUST be virtualised — a
four-hour meeting is thousands of utterances.

**R5.2.4** MUST work with no recording (agenda + minutes only) and with a
recording but no published agenda (transcript-derived items only), and MUST
look deliberate in both cases.

**R5.2.5** A speaker MUST be shown with the office they held **at that meeting**
where `meeting_roster` records one — "Girardi, Vice Chairman", not a bare
surname. Offices rotate annually, so this is a per-meeting fact, and it is what
makes a procedural exchange legible.

**R5.2.6** The spine MUST be ordered by **when things happened where we know
when, and by published order where we do not**, in that order, with the break
between the two labelled.

The spine originally sorted on `seq` alone, which is the published agenda's
order, and that was wrong three ways at once — all three visible on one screen
of 2026-07-14:

- Transcript-derived stretches carry a `seq` above every published item, so
  "Call to order, 0:01" rendered below 191 rows. This is not a quirk of one
  meeting: in **all 234** meetings holding both kinds, every derived item sorts
  after every published one.
- **3,798 of the 5,500 located items** in the archive — 69%, across **224 of
  283** recorded meetings — sat at a position that disagrees with when they were
  heard. The board takes items out of order routinely: on 2026-07-14 it took the
  millage resolution, published 77th, at 3:07 in the afternoon after every
  rezoning. Published order hides that, and it is the interesting part.
- Offsets restart in each recording and **126 of 283** recorded meetings have
  two sessions or three, so a rail that does not group by session reads as
  scrambled — 2:09:53 followed by 1:01:10.

A spine that follows the playhead (R7.2) cannot mean anything if the rail is not
in the order the playhead moves through.

MUST NOT interpolate a time for an item that has none. Roughly 17,600 published
items on recorded days are bound to no span; placing one between two timestamps
would state a time we do not have. They keep published order in a second lane,
labelled with why.

The break MUST NOT appear when there is nothing to break: one recording needs no
session dividers, a meeting with no recording is one lane of published agenda
(R5.2.4), and a recording with no published agenda is one lane of transcript.

**R5.2.7** An item the board takes up, sets aside and returns to MUST appear
**once per appearance**, wherever its speech or its position is shown — the
spine, the item page, and the case page.

A chronological rail can only put a row at one time, and both times are real. On
2023-02-02 the Planning Commission argued PC8 at 18:05 and again at 3:38:04;
listing it once meant the rail had a hole where an hour of argument happened,
the playhead scrolled backwards on reaching the second stretch, and clicking the
row seeked three hours away from what the reader pointed at. On the item page
the two stretches were concatenated, so the transcript read as continuous speech
with three and a half hours of unrelated business removed from the middle of it.

Appearances MUST be derived by merging spans closer together than a threshold
inside the measured gap in the data. Across the archive the gaps between
consecutive spans of one item are `0s ×6, 2s ×2, 4s, 5s | 64s, 65s, 67s, 74s …
207m` — below the trough is the binder cutting one discussion in two, above it
the board genuinely leaving and coming back. 5,587 spans reduce to 5,566
appearances, and 76 items are taken up more than once. `archive._runs` is the
single definition; a second implementation would drift.

Each appearance MUST say which of how many it is, and MUST be able to point at
the others. A reader who finds the first stretch and hears it end with no
decision has to be able to tell that the answer comes later in the day.

### 5.3 Agenda item (`/item/:id`)

**R5.3.1** MUST lead with the official record: code, official title, case
number, department, staff recommendation, and the **minutes disposition
verbatim** with its classified outcome.

**R5.3.2** MUST show the recording span with a play control where one exists,
and state plainly when one does not.

**R5.3.3** MUST show the case thread inline — the other meetings that took this
matter up — because an item is rarely the whole story.

**R5.3.4** MUST separate "what the county recorded" from "what was said" with
the R2 treatment, and carry the provenance note on the transcript half.

**R5.3.5** MUST be able to show the source agenda or minutes document inline.
We hold every one in `portal_files`. Showing the county's own page for an item
is the strongest provenance available and costs nothing but layout.

### 5.4 Case (`/case/:id`) — the sleeper feature

A rezoning is heard by the Planning Commission, transmitted by the Board, and
adopted months later. 1,377 cases span more than one meeting; one measured
example ran to 12 appearances across 10 months and five continuances. **No flat
search can show this and it is the most compelling thing in the archive.**

**R5.4.1** MUST render as a timeline: each appearance with date, body, outcome
and the minutes text.

**R5.4.2** MUST show the full official title once, and per step show only what
varies (item type, body, outcome). Repeating a 60-word legal title per step
buries the shape of the sequence, which is the entire point of the view.

**R5.4.3** MUST make the terminal outcome findable at a glance among the
procedural steps that precede it.

**R5.4.5** The timeline SHOULD stay on screen while the steps scroll past it
*(added 2026-08-13, at the maintainer's direction)*. It is the one thing on the
page that stays true for every step — where you are in the case — and a reader
five appearances down has otherwise lost the only view of the whole. It costs
about 20% of a phone screen with the header, which is the trade.

**R5.4.6** Something MUST say where the reader is for the whole length of the
page. The speech is roughly 97% of it — 81,295px of 84,083px on `PDE-25-7738` —
so scrolling with no marker at all is the default state, not an edge case.

*Revised the same day, when R5.4.7 changed the shape underneath it.* The first
answer was a handoff: the calendar pinned over the thread, and each hearing's
header took the top of the screen once the separate transcript section began.
Interleaving deleted that section, so there are no hearing headers left to hand
off to, and the calendar now pins across the whole list — which is no longer a
pin outliving what it describes, because the list IS the thread.

What is still missing is per-appearance orientation deep inside one hearing's
speech: the step's own header scrolls away above it. The step's date gutter is
the natural thing to pin, and it costs no vertical space, but it has to sit
below the pinned calendar and nothing in CSS knows that height. Left undone
rather than guessed at — a constant standing in for a measurement is gotcha 92.

**R5.4.7** What was said at an appearance SHOULD sit with that appearance, not
in a section of its own *(2026-08-13, at the maintainer's direction)*. A single
continuous transcript is right — it is the only place a year-long argument reads
as one argument, and it MUST remain readable straight through in one scroll.
Filing it under each step is what makes that compatible with the record: the
separate section printed the same seven meetings twice, once as a step carrying
date, body, code and disposition and again as a transcript header carrying the
same four.

Measured on `PDE-25-7738`: same 1,261 lines, page height 84,083px → 83,908px, so
the duplication came out at no cost to the read. The speech MUST span the step's
full width rather than its content column — nested in the narrower column it
wrapped harder and made the page 16% taller (97,816px) than the layout it
replaced.

The lines on this page are set to a **wider measure than the site's 42rem**, for
the same reason. A step is a 62-word legal title redlined against the official
one rather than prose, and the marks only read as a diff when enough of the
sentence sits on one line. Scoped to this surface; 42rem stays right everywhere
that is genuinely prose.

**R5.4.4** MUST offer **everything said about the case**, across every meeting
that took it up, in the order it happened.

The thread says a case was heard seven times; this is the seven hearings. It is
the only place in the archive where one argument can be followed across a year,
and it is what a resident wants when a project next door keeps coming back.

MUST keep the boundaries the record actually has — meeting, session, appearance
(R5.2.7) — rather than pouring the speech into one list. That these words are
from March and those from October, that the Planning Commission said one thing
and the Board another, and that nothing happened for five months in between, is
most of the information.

MUST say how many appearances are **not** in a recording. `PDE-25-7738` was
taken up twelve times and seven are recorded; a page headed "everything said"
that quietly omits five is claiming completeness it does not have (R3.2).

MUST send it with the page, not behind a second request. Median 32 lines per
case, 221 at the 90th percentile, largest in the archive 2,349 — an order of
magnitude below one meeting transcript, so a loading state would cost more than
it saves (R7.5).

MUST NOT treat a long hearing as a suspect one. This requirement briefly said
the opposite, and both views carried a warning on any stretch covering more than
half its recording — 110 of 5,587 spans. Investigated properly (task #26), those
are real: the affected meetings have a median of **8** published items, no wide
segment defaults to the end of the tape, and matching vote language inside every
span puts the last vote at **97%** of the way through a wide span against **96%**
through a normal one. A four-hour rezoning hearing is a four-hour rezoning
hearing. SHOULD show its **duration**, which is what a reader actually wants
from a stretch that size.

### 5.5 Ask (`/ask`)

Built in slice 4 as `web/agent.py`, a loop over the same `web/tools.py` the
search page calls (D9). There is no fixed pipeline left to caption.

**R5.5.1** MUST stream progress. The agent takes 30–90s and a spinner is not
acceptable. *Built by streaming the agent's ACTUAL tool calls rather than four
fixed stage names — under D9 the stages are whatever it decides to do, and
"searched the published record: 'school zone speed cameras' → 0 items" tells a
reader something a progress bar cannot: that the archive was asked and did not
have it. A rejected call shows as rejected, so the reader sees it correct
itself.*

**R5.5.2** MUST render two citation types distinctly: `[item:N]` → the published
record; `[N]` → a transcript passage with a timestamp. Clicking a record
citation reveals the record; clicking a transcript citation seeks the player.

**R5.5.3** Evidence MUST be grouped **meeting → agenda item**, never a flat
chronological list. A quote cannot be judged without knowing which item it
belongs to.

**R5.5.4** MUST surface the official record as its own block above the
transcript evidence, since it is the authoritative answer to "what was
decided".

**R5.5.5** MUST present the assistant as a research tool that cites, never as an
oracle. No answer without evidence; the empty result is a legitimate outcome and
MUST be designed, not treated as failure.

**R5.5.6** Every citation MUST be verified against what the run actually
retrieved, and any that cannot be MUST be removed from the answer and reported.
A fabricated `[item:41203]` is indistinguishable from a real one to a reader,
which makes it worse than no citation at all. `agent.check()` does this and the
footer states the count when it fires.

**R5.5.7** The verification MUST NOT be advertised in the interface's own
description of itself. Saying "citations it cannot support are removed before
you see the answer" before the reader has seen anything is a failure mode
dressed as a feature: it tells them the tool invents citations, which invites
the obvious question about everything else in the answer. State it where it
happened, not where it might.

**R5.5.8** An answer MUST have a URL that serves *that answer*, and asking MUST
NOT be the only way to reach one. `/ask?q=…` satisfies R4.2 — the URL
reproduces the view — but on this surface reproducing the view means running a
paid agent again and sampling a different answer over a changed archive, so the
one thing a reader wants to do with a good answer, send it to somebody, is the
one thing the URL cannot do. Every completed run is kept (`web/answers.py`) and
`/ask/:id` renders it.

**R5.5.8a** Asking MUST leave the reader at the answer's own URL. `?q=` holds
the question only while the run is in flight, so a reload does not lose it;
when the answer arrives the view replaces the URL with `/ask/:id`. A reader who
copies what is in front of them then sends the answer rather than an
instruction to re-run the agent, which is what a share control existed to work
around — so there is no share control. The replacement MUST NOT push a history
entry: `?q=` behind the Back button makes Back a paid run.

**R5.5.9** A saved answer MUST store what it cited and not the words it quoted,
and MUST read its evidence back out of the archive when it renders. The archive
is the record and an answer is a reading of it; a reading that froze the words
would slowly disagree with the thing it claims to quote, and — because a
redaction is a decision a person made about a real address — would keep
publishing what the archive had stopped publishing. *Passages are keyed on
`(video_id, start_idx, end_idx)`; `passages.id` is reassigned by every re-index
and MUST NOT be stored outside it.*

**R5.5.10** A citation that no longer resolves MUST be reported, never dropped
silently. The passage is gone because a redaction moved its boundaries, and
"four quotes" and "six quotes, two of which the archive no longer stands
behind" are different statements. *The generated prose is the one thing that
cannot be read back, so it is a redaction surface: `bin/redact.py` replaces a
removed span there with the same marker the transcript carries, and
`redaction.gone_from_answers` proves it happened.*

**R5.5.12** A saved answer MUST NOT be deleted — not by a redaction and not by
a retention sweep. It is a URL somebody may have circulated, and a link that
stops resolving is a worse outcome than the disk it costs. Where a redaction
reaches an answer, the address is removed from the wording and the answer
stands; what cannot be settled by removing a string is listed for a person
rather than resolved by destroying the row.

**R5.5.11** A saved answer MUST NOT be indexed. It is a machine-written reading
of the archive, and the pages a search engine should be sending people to are
the record itself — the meeting, the item, the case.

**R5.5.6** MUST NOT display a numeric confidence for speaker attribution. It is
not currently measured, and the previous UI asserted "~78% precise" from a
stale figure. This binds anything that DECIDES from the number as well as
anything that prints it: a threshold is the same assertion with the arithmetic
hidden, and `web/agent.py` briefly drew its line at `confidence >= 0.6` before
this was noticed. How sure a name is comes from `human` and `basis` (R6.2).

### 5.6 Search (`/search`)

Built in slice 3, on the tool surface D9 asks for (`web/tools.py`): the page
issues the same calls, with the same arguments, that the agent will.

**R5.6.1** MUST search **both** the published record and the transcript, and
label which kind each hit is. Before the rebuild it searched transcript only,
which cannot reach the 91% of decided items with no recording. *Built as two
labelled sections, never one merged ranking — "this was approved" and
"somebody said this" are not comparable and do not fail the same way.*

**R5.6.2** Filters MUST include body, date range, speaker, phase, outcome and
case. Filters MUST be reflected in the URL. *Built as a rail of links, values
derived from the data (`archive.facets`) rather than written down. Each facet
goes only to the tool that can honour it — `speaker` reaches speech, `decided`
reaches the record — and the rail labels which is which rather than pretending
both narrowed.*

**R5.6.3** Every hit MUST show the agenda item it sits under and link to it — a
result without that context is frequently unreadable ("all in favor say aye").

**R5.6.4** The placeholder MUST demonstrate both a topic and an identifier —
this archive is searchable by subject words *and* by `PDE-25-7738` or `R-58`,
and nothing currently says so. *Built, and the identifier case is now a
different query rather than the same one: a code goes to `code`/`case_id`
equality, because `websearch_to_tsquery` tears `R-58` into fragments.*

**R5.6.5** A search that could not be run as asked MUST say so. Two cases are
live and both were found by building it:

- Every term matching nothing widens to **any** term — "license plate cameras"
  matches no item that contains all three, while the county's own item is
  titled "License Plate Detection Systems". The page says the query was
  widened rather than presenting the loosened result as the exact one.
- Losing the embedding model leaves keyword matching, which is a materially
  worse search. The page says that too, rather than quietly returning less.

**R5.6.6** Semantic retrieval MUST NOT answer a query it has no evidence for.
Nearest-neighbour search always returns neighbours: asked for `zzzznothing` the
index returned its twelve closest passages, which reads as "the archive
contains this" and is the opposite of true. When nothing matches lexically,
a similarity floor applies (`retrieve.DENSE_FLOOR`); nonsense tops out at 0.52
against 0.62–0.65 for real queries, so the two separate cleanly.

### 5.7 Person (`/person/:id`)

**R5.7.1** MUST show published facts first: body, district, term dates, offices
held, meetings attended — all from the roster, not inferred.

**R5.7.2** MAY show participation derived from voice matching, but MUST mark it
as inferred and unverified, and MUST NOT present counts as authoritative.

**R5.7.3** Covers members of the two boards. Members of the public who spoke are
searchable (D3) but do NOT get a person page: a page aggregating a private
individual's appearances is a different artefact from their name being findable
in the transcript where they said it.

---

### 5.9 Three doors a resident arrives through

**Added 2026-08-12, at the maintainer's direction, and it is a correction to how
§5 was written.** Every surface above is shaped like the ARCHIVE — browse by
month, search by words, ask a question, walk the zoom axis. That is right for
the object graph and it is not how anybody arrives.

A resident does not have a question about the archive. They have a question
about their own situation, and there are about five:

| | the question they actually have | served today |
|---|---|---|
| **place** | is something being built near me? | no |
| **person** | how did my commissioner vote on this? | no |
| **money** | what did this cost, and who got paid? | no |
| **forward time** | what is coming up that I should show up for? | a footnote |
| **subject** | what has the county said about X? | yes — `/search` |

One of five. Three of the four missing ones are specified below; **place** is
deliberately not, because it needs the geocoding spike UI_PLAN §7 asks for and
committing to a map before that spike is how you get a map with holes in it.

The point of writing these as surfaces first is that each one then names its own
mining. The reverse — deciding what is extractable and looking for a use — is
how a properties table gets built, which §10 already refuses.

#### 5.9.1 Forward time — what the board takes up next

**R5.9.1** The upcoming meetings MUST carry their published agenda where the
county has posted one, so a reader can see what will be taken up rather than
only that a meeting exists. This is the only one of the three that helps a
resident ACT rather than check.

*What it needs, and it is not an extractor.* **37 meetings are on the calendar
and none of them has an agenda in this archive** (re-measured 2026-08-13,
immediately after a full portal sweep — it was 35 when this was written). The
county posts agendas days before a meeting, and the nearest scheduled meeting
is a week out, so a sweep run today legitimately returns nothing. The work is
operational: that fetch **on a schedule**, not on a button. `bin/forward.sh`
exists, is idempotent and is installed nowhere. Everything downstream —
`parse_agenda`, the item rows, the coverage chips — already works and has
nothing to learn.

Order matters here: install the schedule, let it catch a few agendas, then
build the surface. Built today it would render 37 rows of R5.9.2's
"not posted yet", which is the honest state and is not a door anyone walks
through.

**R5.9.2** A scheduled meeting whose agenda has NOT been posted yet MUST look
different from one whose agenda this archive simply does not hold. The first is
the county not having published; the second is our gap. Same rule as R2.4.

#### 5.9.2 Money — what it cost

**R5.9.3** Where the county's own item title states a dollar amount, the item
MUST be able to show it as a number rather than only as words inside a title.
Measured: **6,778 of 23,122 published items (29%)** carry one.

**R5.9.4** An amount MUST NOT be summed across items until its KIND is known.
This is the trap, and it is a bad one because the result looks authoritative:
a grant received, revenue, a reimbursement and a purchase all appear as a
dollar figure in the same field. A total built from them would be confidently
wrong. Per-item display is safe; any aggregate is not, until an extractor can
say which direction the money moved.

**R5.9.5** A money fact MUST carry the substring it was parsed from, so a
reader can check it against the source document the item page already renders.

#### 5.9.3 Votes — how each member voted

**R5.9.6** MUST make a member's vote on an item retrievable, which is what
`/person/:id` (§5.7) needs to exist at all and what would make "where the board
disagreed" (R5.1.4) exact rather than textual.

**R5.9.7** The coverage limit MUST lead, not follow. The two sources are not
symmetrical here and the asymmetry is severe:

- **The minutes record DISSENT ONLY.** 114 items say "voting nay". There are no
  tallies. And the minutes **never** record who moved or seconded — zero
  occurrences across all 23,122 published items, verified, not estimated.
- **The transcript has the whole thing**: the roll call is read aloud 1,605
  times, with 612 motions and 823 seconds spoken.

So a complete voting record is TRANSCRIPT-ONLY, and therefore reaches at most
the 9% of decided items that have a recording. A `/person` page that implies a
full voting history would be the largest overclaim on the site.

**R5.9.8** Vote extraction MUST NOT ship before the roll-call segmentation
defect is fixed. **897 utterances contain the clerk's call and the member's
answer in one row** ("District three, Commissioner Starkey. Aye."), so no
per-utterance attribution can be right about them. Building votes on top of
that would turn a transcription defect into structured data, which is much
harder to notice and much harder to undo. See `speaker.rollcall_merged`.

#### 5.9.4 How a mined fact is stored

**R5.9.9** A derived fact SHOULD be a row carrying its own provenance, not a
column on `agenda_items`. Proposed shape:

```
item_facts(item_id, kind, value, value_num,
           source,      -- title | disposition | transcript | model
           extractor,   -- name and version, so a re-run is identifiable
           evidence,    -- the exact substring it came from
           confidence)  -- null when the parse is deterministic
```

Four reasons, and the first is the one that matters: **`evidence` makes a fact
checkable by a reader**, and `/item` already renders the source PDF inline, so
it can be shown against the document. Then: provenance belongs per fact rather
than per table, because a money amount from a title and a mover from a
transcript are not the same kind of claim (§2); `audit.py` gets one invariant
per kind; and a single extractor can be dropped and re-run by name without
touching anything else, which after the rebuilds of 2026-08-12 is not a small
consideration.

**R5.9.10 — the admission rule.** A fact earns a place only if all three hold:

1. it traces to a substring of a document this archive holds,
2. an audit invariant can state when it is wrong, and
3. a named surface changes because of it.

Nothing is mined because it is minable. Applicant entities (43% of titles
carry an LLC, Trust or Family name), dwelling units, square footage and
section/township all pass (1) and fail (3), and are out of scope for that
reason alone.

## 6. Cross-cutting components

The current pages re-declare the theme five times and implement the YouTube
player four times, with three different renderings of an unnamed speaker. These
are shared primitives and MUST exist exactly once.

**R6.1 Player.** One global instance. Seek by `(video_id, seconds)` from
anywhere. Persistent across navigation within a meeting. Must not restart on
re-render.

**R6.2 SpeakerChip.** The single renderer for "who said this". States, derived
from `human` and `basis` and from nothing else:
- *named, human-confirmed* — strongest treatment
- *named, voice-matched at this meeting* — normal treatment, marked as inferred
- *named from the archive-wide cluster only* (`basis='cluster'`) — the weakest
  claim available and MUST be drawn as weaker than voice-matched. It is
  evidence about a voice, not about this meeting, and it is what put two
  different women under one name.
- *unidentified* — explicit and neutral
- *several speakers* (an exchange passage)

**R6.2.4** Every surface that names a speaker MUST render through this
component — search hits, an answer's evidence, a saved answer, the front
page's divided-in-the-room rows, a transcript line. A surface that prints the
name itself makes a claim with no certainty attached to it, and four of them
did: they showed a name a person had confirmed and a name inherited from a
cluster in the same bold type. *Unidentified* and *several speakers* are also
distinct claims and MUST NOT be collapsed into one label.

**R6.2.5** The agent's brief (`web/agent.py`) MUST describe a speaker in the
same states and draw its line in the same place, because the reader who
follows a citation from an answer to the page must not be told two different
things about one name. It marks only the ends — confirmed and weak — since
voice-matched is the ordinary case and marking it would warn on nearly every
line; a name with no mark is a voice-matched one, and the prompt says so.

**R6.2.1** MUST NEVER render a raw internal label. `Speaker 3`, `Group 465`,
`SPEAKER_00` are diarization ids that change between pipeline runs; they read
as names and are not.

**R6.2.2** MUST be the entry point for correction (§5.8): wherever a name is
rendered, the affordance to dispute it is on this component. One component
means one place to add it, and one place for a future redaction rule (D3).

**R6.2.3** MUST be able to render a *contested* state, for a name with a
pending correction.

**R6.3 OutcomeBadge.** One consistent vocabulary and colour semantics across
every surface: `approved`, `adopted`, `denied`, `withdrawn`, `continued`,
`received`, `no_action`, `tabled`, `other`, and the distinct state *no
disposition recorded* — which is **not** the same as "no outcome" and MUST NOT
be styled as one.

**R6.4 ProvenanceBadge.** Marks a block as published record / minutes /
transcript / AI-generated. Used everywhere R2.1 applies.

**R6.5 Timeline.** Shared by case, meeting and person. Dates on an axis,
events as steps, outcome encoded consistently.

**R6.6 Design tokens.** One source of truth for colour, type scale, spacing,
radius, elevation and motion. Light and dark. Defined once, imported
everywhere.

**R6.7 Citation.** A canonical, copyable reference — for a passage (body, date,
item, timestamp) and for an item (body, date, code). All three civic archives
reviewed publish one; it is what makes an archive quotable in a news story or a
filing. For us it MUST point at the *recording*, because the transcript is
machine-generated and the recording is the primary source.

**R6.8 ScopedSearch.** One search control that inherits the current object as
its scope — everything, this meeting, this case, this person. A behaviour, not
a separate component per surface.

---

## 7. Interaction and craft

The brief is a *beautiful and interactive* frontend. Concretely, for this
material:

**R7.1** Time is the organising dimension of the whole archive. Timelines and
duration should be first-class visual affordances, not lists with dates on them.

**R7.2** The agenda spine on a meeting page should behave like a chapter track:
scrubbing it moves the video, playing the video moves it.

**R7.3** Transitions between related entities (item → case → item) should
preserve context rather than reload a blank page.

**R7.4** Motion MUST be purposeful — showing relationship, continuity or state
change — and MUST respect `prefers-reduced-motion`.

**R7.5** Density is a feature for this audience. Prefer information-rich layouts
over generous whitespace that hides the shape of the data. Do not paginate what
can be virtualised.

---

## 8. Non-functional

**R8.1 Performance.** Meaningful content within 1s on a warm cache. Long
transcripts virtualised. Search results incremental. The Ask stream must render
progressively.

**R8.2 Accessibility.** WCAG 2.2 AA. Full keyboard operation including the
player and the transcript. Visible focus. Contrast verified in both themes.
Transcripts are text, not canvas. Captions/labels on all controls.

**R8.3 Responsive.** Must be usable on a phone — a resident following a hearing
is a real case. The player, the agenda spine and the transcript need explicit
small-screen designs, not a squeezed desktop layout.

**R8.4 Theming.** Light and dark, honouring system preference with a manual
override.

**R8.5 Resilience.** Every surface has designed empty, loading, partial and
error states. "No recording", "no disposition recorded" and "no speaker
identified" are *expected* states and must look intentional.

**R8.6 Shareability.** Record pages should render meaningful titles and
descriptions for link previews.

---

## 9. Admin surface (authenticated)

**R9.1** MUST be behind authentication and MUST NOT appear in public navigation.

**R9.2** Speaker curation is a queue-driven workflow, ordered by impact
(utterances × meetings): triage an unnamed voice → hear samples → assign /
split / merge / ignore → confirmed.

**R9.3** The underlying model is one operation on a mapping: a name attaches to
a span of speech. Assign, split and merge are the same call with different
selections, and the selection may be a whole voice or a range of utterances
within one (§5.8.1). The UI MUST reflect that rather than inventing separate
flows per operation.

**R9.3.1** MUST surface where the same person is attached to more than one
voice in a single meeting — 80 of 1,467 (meeting, person) pairs today. Some are
legitimate diarization splits, so this is a review queue, never an auto-fix.

**R9.4** MUST surface pipeline health — the audit invariants, coverage numbers,
and names whose voice does not cohere — so data damage is visible without a
terminal.

**R9.5** Human labels outrank everything derived and MUST be visibly permanent.

**R9.7** MUST provide a queue for the addresses `bin/redact.py` proposes
removing *(2026-08-13)*. The classifier is good and its two failure modes are
not symmetrical, which is the whole reason a person is in this loop: removing
too little leaves somebody's home address searchable, and removing too much
deletes the matter under discussion — "access to the site is from Clinton
Avenue" is a road, not a residence — and that damage is silent, because nobody
can miss what is no longer there.

So a row MUST show the whole line with the span marked **in place by offset**,
not by searching the text again, and the line BEFORE it: the clerk saying
"please state your name and address for the record" settles most of these
without playing anything. Where it does not, the row MUST link to the moment in
the recording.

Bulk apply MUST be a job rather than a request. Applying re-indexes every
affected recording, because the address is in the passage text, the BM25
postings and the embedding as well as the utterance — 3,439 proposals span 370
recordings at ~4s each, which is 25 minutes. Per-row accept is capped at 25 for
the same reason.

**R9.6** MUST review the public correction queue (§5.8): accept, reject, or
merge into an existing identity. Accepting writes a human label.

---

## 10. Non-goals

- Not a chatbot. No conversational memory, no persona, no answers without
  citations.
- No editing of the public record. Agendas and minutes are the county's; the
  app never rewrites them.
- No login to read.
- No inventing structure the pipeline does not produce. If an item has no
  disposition, the UI says so; it does not infer one from a vote being called.
- Not a video editor or clipping tool (for now).

---

## 11. Decisions

Settled during review:

**D1 Auth — a startup token, the Jupyter model.** The server generates a random
token on start and prints it where the operator will see it. Visiting the admin
surface with that token exchanges it for an `httpOnly` `SameSite=Lax` session
cookie, so the secret leaves the URL immediately and is not sitting in browser
history or a referrer header. No accounts, no roles, no password to store or
rotate. Three things that MUST hold:

- a fresh token per process start — a restart invalidates old sessions
- it is a secret, so it MUST NOT be written to a log file that gets shipped
  anywhere, or committed
- the admin surface MUST refuse to serve on a non-loopback interface without
  TLS, because a bearer token over plain HTTP on a network is a giveaway

If the whole thing ends up bound to localhost or behind a VPN, better still —
the best way to protect an endpoint is not to expose it.

**D2 Video — YouTube embeds, for now.** Accepted with its risk: the archive
does not control the videos, and a takedown or channel change breaks playback
retroactively. Two consequences the UI MUST honour:
- A player failure MUST degrade to the transcript and the published record,
  which are held locally, rather than to a broken frame.
- Nothing in the design may assume the video is the only path to a fact.

**D3 Public naming — commenters remain searchable**, redaction handled later.
The design MUST therefore make redaction cheap to add rather than retrofitted:
a person's display name resolves through one component (§6.2), so a future
suppression rule has exactly one place to act. Do not denormalise names into
cached strings that a later redaction cannot reach.

**D4 Corrections — readers can propose speaker fixes.** See §5.8.

**D5 Search-engine indexing — undecided, and deliberately not blocking.** If
record pages must be indexable later, `/meeting`, `/item` and `/case` need
server-rendered or pre-rendered HTML. D6 keeps that a configuration change
rather than a rewrite, so this can stay open.

**D6 Stack — React on Next.js (App Router).**

React over Svelte for two reasons that come straight out of the requirements
rather than from taste:
- **Virtualization.** R5.2.3 and R8.1 require rendering a four-hour meeting —
  thousands of utterances — without dying. TanStack Virtual and Virtuoso are
  more mature than anything equivalent elsewhere, and this is the single
  highest-risk piece of UI in the app.
- **Accessible primitives.** R8.2 asks for WCAG 2.2 AA. Radix / React Aria do
  real work on focus traps, dialogs, comboboxes and roving tabindex that would
  otherwise be hand-rolled and quietly wrong.

Next specifically because D5 is open: it can render static, server-side or
client-side per route, so "should this be indexable" stays a per-page decision.
If D5 lands on "no", a plain Vite SPA would have been lighter — but Next can
build static output too, so nothing is lost by starting here.

Supporting choices, so they are not relitigated per component:
- **Design tokens as CSS custom properties**, whatever else is used for
  authoring. R6.6 requires one source of truth and R8.4 requires two themes;
  custom properties give both, survive SSR, and do not depend on the framework.
- **TanStack Query** for server state — search, items, cases all want caching,
  deduping and stale-while-revalidate.
- **One hand-rolled YouTube IFrame wrapper**, mounted once at the app shell
  (R6.1). Not a per-page component and not a library.
- No component kit that imposes its own visual language. The look is bespoke;
  borrow behaviour, not appearance.

**D7 API — redesign after the UI shape is known.** The current endpoints were
shaped for the old pages and are a stopgap, not a contract. They will be
reworked once the first surfaces exist and their real data needs are visible.
Known problems, recorded now so they are not rediscovered:
- `/api/search` searches only transcript utterances. It cannot reach the 91% of
  decided items with no recording. `retrieve.search_items()` exists and is
  wired into the agent but not into search.
- `/api/meeting/:id` takes a **video** id while `/api/agenda/:id` takes a
  **meeting** id. Two different keys, near-identical names — a trap introduced
  while building the record pages, and it should not survive the rebuild.
- Nothing serves browse/timeline, `/person`, or corrections.
- `/api/ask` streams SSE and already carries `decisions`; that shape is worth
  keeping.

**D8 Corrections — admin-side first, public proposals later.** The utterance-
level override (R5.8.1, R5.8.6) is what makes the reported bug fixable at all,
so it lands with the admin surface rather than at the end. The public *proposal*
flow — untrusted submissions, rate limiting, a moderation queue — is a separate
and larger problem and follows after. The distinction is trust, not
granularity: the operator's correction applies immediately, a stranger's does
not. Both write to the same override table, so the storage design (R5.8.5,
R5.8.7) must be settled when the admin path is built.

**Status, 2026-08-13: the admin half shipped as slice 6 and the storage design
is settled** — one `speaker_override` table, `pending` for an unreviewed
proposal, precedence per R5.8.7, and `/admin` reviews the queue per R9.6. The
public *submission* flow is still unbuilt: `SpeakerChip` shows a "contested"
mark for a pending proposal, and there is no way for a reader to file one. That
is slice 8, and it is the last unbuilt piece besides `/person`.

**D9 Retrieval is a set of TOOLS the agent calls, not a fixed pipeline.**
Binds slices 3 and 4. `bin/ask.py` today runs `plan() → retrieve() →
multi-lens read() → answer()`: the planner emits its queries once and the
pipeline executes them blindly, so nothing can react to a bad result. This
corpus punishes that specifically — a vote passage contains no topic words,
which is why `eval_agent` exists, and the planner's own wording put the target
at rank 33–58 while the agent read only the top 30.
`retrieve.decisions_in_play()` is a hard-coded patch over that one case.

The general fix is an agent that can look, notice it found nothing useful, and
search again. So the retrieval surface is designed as callable tools — search
the transcript, search the record, expand an item, fetch a case thread — and
the model sequences them. Do not add further fixed stages to `ask.py`.
`retrieve.search()` and `retrieve.search_items()` are the natural first two,
and `/search` should expose the same surface the agent uses rather than a
parallel one.

**Status, 2026-08-13: done, and the paragraph above describes the old world.**
`web/tools.py` is the surface — five tools with JSON Schema behind one `call()`
— `/search` is two of those calls, and `web/agent.py` sequences them behind
`/api/ask`. `ask.plan()` is now reachable from nothing: every surviving
`import ask` takes the chat client, not the pipeline. `decisions_in_play()` is
redundant for the reason D9 predicted — the agent chooses to open the item
instead. What is NOT done is the measurement: there is still no broad number
for whether any of this retrieves better, and slice 3 set a loosening rule, a
re-ranker and a similarity floor from a handful of probes. See STATE "Next" 3.

### 5.8 Corrections

#### The granularity problem — this is the important part

The current model attaches a name to a **voice**, where a voice is
`(video_id, local_label)` from diarization. Assign, split and merge are all one
operation on that mapping. It is a clean model and it **cannot express the most
common error**, which is why the existing Workbench hits a wall.

A real observed case, meeting `wSkGsd74JPc`:

| utterance | voice | cluster | shown as |
|---|---|---|---|
| 116 | SPEAKER_04 | 44 | Mariano |
| **117** | **SPEAKER_05** | **192** | **Mariano** |
| 118 | SPEAKER_04 | 44 | Mariano |

Two different voices in one meeting, both labelled Mariano; one of them is not
him. A reader looking at utterance 117 can see it is wrong and has **no way to
say so**: renaming the voice renames every line it covers across the meeting,
and there is no operation that means "not this stretch". The same wall appears
when diarization merges two people into a single `local_label` — then the voice
is *partly* right, and no whole-voice operation can fix it.

Measured: a board member is attached to more than one voice in the same meeting
in **80 of 1,467 (meeting, person) pairs** (5.5%). Some of those are genuine —
diarization does split one person across two labels — which is exactly why this
needs a human affordance and not an automatic rule.

**R5.8.1 The finest addressable unit for a correction MUST be the utterance**,
and a correction MUST be expressible over a contiguous range of utterances.
Voice-level and cluster-level operations remain, as shortcuts over that unit —
not as the only vocabulary.

**R5.8.2** The correction vocabulary MUST include all four of:
- **reassign** — this range is a different named person
- **detach / clear** — this range is *not* who it currently says, and I do not
  know who it is. This MUST be available on its own. Being unable to say "not
  this" without also supplying a name is the specific dead end reported.
- **identify** — this range is unidentified and I know who it is
- **split the voice** — this `local_label` is two people; separate it from here

**R5.8.3** A correction MUST be possible **from wherever the name is shown** —
the transcript reading view, an item page, Ask evidence — not only from the
Workbench. The error is noticed while reading, and requiring the reader to
re-find the voice in a separate admin tool is why it never gets fixed.

**R5.8.4** Selecting a range MUST be direct: click a line, shift-click to
extend, correct the selection. No id entry, no separate lookup step.

#### Storage and precedence

**R5.8.5** Corrections MUST be stored against `(video_id, utterance range)`,
NOT against a cluster id — cluster ids are reshuffled on every re-clustering
run (measured: 2% survive). Whole-voice corrections continue to store against
`(video_id, local_label)`, which is stable.

**R5.8.6** This requires an utterance-level override the current schema does
not have. `speaker_label` keys on `(video_id, local_label)` and cannot express
a partial voice. Adding it is in scope for the rebuild.

**R5.8.7** Precedence MUST be: utterance override → voice label (human) →
derived assignment. A human statement at any granularity outranks everything
derived, and MUST survive every pipeline rebuild.

#### Trust

**R5.8.8** A public correction is a *proposal*. It enters a queue and changes
nothing a reader sees until an admin approves it (§9.6). Corrections made
inside the authenticated admin surface apply immediately.

**R5.8.9** Public submission MUST be possible without an account, and MUST
therefore be rate-limited and treated as untrusted input.

**R5.8.10** The UI MUST show when a name is contested — a pending correction
exists — without asserting which side is right.

---

## 12. Rollout

Build order that keeps the app usable throughout.

**Slice 1 — vertical, and the one that de-risks everything else.**
Shell, design tokens, the §6 components, the player, routing, and `/meeting`.
`/meeting` is chosen deliberately: it does not exist today, it carries the most
value per unit of work, and building it exercises the player, the SpeakerChip,
the provenance treatment, the agenda spine and virtualization all at once, on
real data. Whatever is wrong with the visual direction will be visible here,
while it is still cheap to change.

Then:

2. `/item` and `/case` onto the shared components.
3. `/search` — record hits and transcript hits (needs the D7 gap closed).
4. `/ask` — grouped evidence, dual citations, decision cards.
5. `/` browse with the time axis.
6. Admin shell behind the startup token (D1): the Workbench, plus the
   utterance-level correction that makes a misattributed stretch fixable (D8).
7. `/person`.
8. Public correction proposals and the moderation queue (§5.8, D8).

**Order actually taken: 1, 2, 5, 3, 4, 6.** Slice 6 (the admin shell and the
utterance-level correction console) is built: D1 auth with the token in a
mode-600 file rather than printed, loopback-only admin routes, the queues of
§9 ordered by utterances affected, and all four R5.8.2 verbs writing to
`speaker_override` with the index refreshed per write. Browse was pulled ahead
of `/search`
on the maintainer's call — it was unblocked where search is not, and a front
door argues the product better than a search box does. `/search` followed, then
`/ask` on the tool surface `/search` built.

**`/person` (slice 7) and the public correction queue (slice 8) are what
remain.** An earlier revision of this paragraph said "`/person` and `/admin`
remain" while the sentence above it described `/admin` as built; `/admin` is
slice 6 and it shipped. `/person` is additionally BLOCKED rather than merely
unbuilt — R5.9.6 needs a member's vote, votes need the roll-call split
(R5.9.8), and that has not been done.

The order above is otherwise unchanged, and the numbering is kept as written so
the slices keep the names everything else refers to them by.

API work happens per slice, driven by what the surface actually needs (D7), not
designed up front. The **schema** is the stable contract; the endpoint shapes
are not. `bin/schema.sql` and the audit invariants in `bin/audit.py` are the
source of truth for what the data means.

Slices 3 and 4 are additionally bound by **D9** (§11): retrieval is a set of
tools the agent calls, not a fixed pipeline. Slice 3 built that surface —
`web/tools.py`, five tools with JSON Schema, one `call()` entry point, and
`/api/tools` serving the manifest a model gets handed. `/search` is two of
those calls and nothing else, which is the property slice 4 depends on: the
agent cannot reach anything a reader cannot, and vice versa.
