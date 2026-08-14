# Pasco Meetings — cohesive UI plan

How the pieces fit. Reads on top of `UI_REQUIREMENTS.md` (what must be true)
and `PRIOR_ART.md` (what others got right). This document is the *design
argument*: one organising idea, and every feature as an expression of it.

---

## 1. The organising idea

> **One object graph, seen at five zoom levels, cut by two threads, where every
> statement traces back to one of two sources.**

That single sentence is the whole product. Everything below is a consequence of
it, and anything that cannot be derived from it should not be built.

The current app has no organising idea, which is exactly why it feels like five
unrelated pages: search returns utterances, Ask returns passages, the Workbench
returns voices, and nothing agrees on what the unit of the archive *is*.

### The zoom axis

```
Archive  →  Body · Year  →  Meeting  →  Agenda item  →  Moment
1,249       2 bodies        432          26,428          298,737
meetings    12 years        recordings   items           utterances
```

Each level is a real object with a URL. Zooming in narrows; zooming out never
loses your place. **The moment is the bottom of every path** — it is where the
recording plays, and it is the only thing that is primary evidence.

### The two threads that cut across it

- **A case** — one matter followed through many meetings. `PDE-25-7738` was
  heard twelve times over ten months, alternating Planning Commission and
  Board, continued five times. 1,377 cases span more than one meeting.
- **A person** — one voice followed through many meetings.

Threads are the horizontal axis. Zoom is vertical. Every screen sits at an
intersection of the two, and that is the entire information architecture.

### The two sources

| | the record | the transcript |
|---|---|---|
| is | agendas + approved minutes | ASR + voice matching |
| says | what was **decided** | what was **said**, and by whom |
| authority | the county's own words | inferred, plausibly wrong |
| fails by | being absent | being wrong |
| bottoms out at | the source PDF on the county portal | the recording at a timestamp |

**Neither is complete.** A transcript can show a vote being taken and never its
result; the minutes record the result and never the argument. Only 9% of
decided items have a recording at all. The UI's job, at every zoom level, is to
show both and never blur them.

---

## 2. Four verbs

Every feature — including all the good ideas from prior art — is one of exactly
four things. If a proposed feature is none of them, it does not belong.

| verb | what it does | features |
|---|---|---|
| **Enter** | get into the graph | browse timeline · search · Ask · "where they disagreed" · a shared link |
| **Move** | travel within it | agenda spine · case thread · prev/next speaker · zoom in/out · player seek |
| **Verify** | reach the source | citation string · provenance badge · upstream portal link · embedded source PDF · play the moment |
| **Improve** | fix what is wrong | speaker correction at utterance range · admin workbench |

This is the cohesion test. Not "is this feature good" but "which verb is it,
and does it work at every level it should".

---

## 3. Why the whole beats the parts

Cohesion is not a style; it is measurable as **traversals that only work
because the parts share a spine.** Three real ones, on real archive data:

**A resident asks a question and ends at primary evidence.**
Ask *"what was decided about the school zone speed cameras"* → the answer leads
with the official disposition and cites `[item:…]` → the item is `R-58`, with
the minutes text verbatim → the minutes link to the county's own PDF → the item
knows its span in the recording → one click plays Mariano calling the vote at
10:09. **Question → decision → county document → the moment it happened.**
Four features, one unbroken chain of custody, no dead ends.

**An objector follows a rezoning they care about.**
Search `Evans County Line 80` → an item → its case thread `PDE-25-7738` → twelve
appearances as a timeline, five continuances, both bodies → the two meetings
with recordings offer the argument; the ten without still show the disposition
and the case has a URL, so it can be handed to a neighbour or cited in an
objection. **Twelve unrelated portal events become one story.** This is
impossible in CivicClerk by construction.

**A reporter checks who said something.**
A passage in Ask evidence → the speaker chip says *Girardi, Vice Chairman* (the
office he held *at that meeting*) → it is marked inferred, not confirmed → play
it and hear that it is wrong → correct that range in place → it enters the
queue. **The archive gets better because it was read.** Improve is not an admin
afterthought; it is a reading affordance.

Each traversal crosses three or four features and none of them required special
integration work — they compose because they share the object graph, the two
sources, and the four verbs.

---

## 4. The screens

Each screen is a zoom level or a thread. Nothing else earns a route.

### `/` — Archive
**Enter.** Not a search box on a photo. The collection as an object: 12 years,
two bodies, 1,036 hours, and what fraction has agenda / minutes / recording.
Time is the spine — a year→month→meeting axis, filterable by body. Meeting rows
carry their coverage state so you know what you will get before clicking.
Curated entry points sit here: *where the board disagreed*, *most-continued
matters*, *recent decisions*.

### `/meeting/:id` — Meeting
**Move.** The missing centre. The **agenda spine** is the hero: items in
published order, each with code, title, outcome, and its offset into the
recording. It is simultaneously a table of contents, a chapter track and a seek
bar — three jobs that are the same job. Playing scrolls it; clicking it seeks.
The roster shows who was present, from the published roster, with offices.
Transcript reads alongside, virtualised, synced.

Works with no recording, and with a recording but no published agenda. Both
must look deliberate.

### `/item/:id` — Agenda item
**Verify.** The record first: code, official title, case, department, staff
recommendation, and the minutes disposition *verbatim* with its outcome. Then
the source document inline, and the link to the county portal. Then the
recording span. Then what was said. The case thread sits inline — an item is
rarely the whole story — with the dedicated view one click away.

### `/case/:id` — Case
**Move, across time.** A timeline of appearances: date, body, outcome, minutes
text. Full legal title once at the top; each step shows only what varies, or the
sequence drowns in boilerplate. The terminal outcome must be findable among the
continuances that precede it.

### `/person/:id` — Person
**Move, across voices.** Published facts first — body, district, term, offices,
attendance, all from the roster. Derived participation clearly marked as
inferred. Scoped search: *search this person's speeches*.

### `/search`
**Enter.** Searches **both sources** and says which each hit is. A record hit
and a transcript hit are different objects and must look it. Faceted rail:
body, date, phase, outcome, case, speaker. The placeholder teaches the duality
by example — a topic *and* an identifier.

### `/ask`
**Enter, at speed.** The agent is not a separate product; it is an automated
traversal of this same graph, which is why its two citation types are the two
sources and its evidence groups meeting → item. Streams its stages. Never
answers without evidence.

### `/admin/*`
**Improve.** Behind the startup token. The workbench queue, the correction
review, and pipeline health.

---

## 5. Shared vocabulary

Cohesion is mostly this: **the same object always looks the same.** Six
primitives, used everywhere, defined once.

- **ItemCard** — an agenda item. Appears in search results, on the meeting
  spine, in a case timeline, and in Ask evidence. Identifier + outcome badge,
  plain title, metadata row. *One component, four contexts.*
- **SpeakerChip** — who said this. Named-confirmed / named-inferred /
  unidentified / several speakers, plus the office held at that meeting. It is
  also the entry point for corrections and the single choke point for a future
  redaction rule.
- **OutcomeBadge** — one vocabulary, one colour semantics, everywhere.
  Including the distinct state *no disposition recorded*, which is not the same
  as "no outcome".
- **ProvenanceMark** — which source this block came from. The load-bearing
  primitive; §2's distinction is only real if it is visible.
- **Citation** — a canonical, copyable reference. Three of three serious civic
  archives publish one; it is table stakes for being quotable.
- **Timeline** — case, meeting and person all render events on a date axis.

Plus one global **Player**, mounted at the shell, seekable by
`(video_id, seconds)` from anywhere, surviving navigation.

**Scoped search** is a behaviour rather than a component: the same search input
inherits the current object as its scope. Search on `/` searches everything; on
a meeting, that meeting; on a person, their speech. One control, contextual.

---

## 6. Layout

One frame, so the app feels like one thing:

- **Context rail (left)** — where you are on the zoom axis, and the way out.
  On a meeting it becomes the agenda spine; on a person, their sub-nav.
- **Main column** — the object.
- **Player dock** — persistent, collapsible, never remounts.
- **Command palette** (`/` or `⌘K`) — search and jump, from anywhere.

Density is a feature: this audience wants information, not whitespace. Long
lists virtualise rather than paginate.

---

## 7. Deliberately not built

Cohesion is as much refusal as addition.

- **No conversational chat.** Ask is a research tool that cites. Memory and
  persona would make it a different product and undermine every citation.
- **No dashboard of metrics.** Counts of meetings are not insight; the archive
  is a record, not analytics.
- **No separate "video" section.** Video is an attribute of a meeting, not a
  destination. A video library would fork the zoom axis in two.
- **No AI summaries presented as the record.** Derived text never occupies the
  position the minutes occupy.
- **No infinite feed.** Entry points are curated or queried, never a scroll.

### Deferred, deliberately

Not refusals — decisions to build the reading surfaces first. Both are recorded
so they are not rediscovered as novel ideas later.

- **Subscription** ("tell me when this changes"). Cut from this build. It was
  originally a fifth verb, *Watch*; removing it is why §2 has four. RSS would
  have been nearly free given cases are already threaded, and email is a real
  project — an address store, confirmation, unsubscribe, deliverability and a
  privacy commitment on a public-records site. Revisit once people are actually
  reading the thing and ask to be told when a case moves.
- **A map of land-use items.** The strongest idea found in prior art
  (`PRIOR_ART.md` §1) and the one that would matter most to a resident: our
  rezoning titles are saturated with geography. It needs a geocoding stage in
  the pipeline, and the addresses are prose rather than postal
  ("Located South of County Line Road North and East of Lake Iola Road"), so
  coverage is unknown until measured. A day-long spike answers whether it is
  worth building — do that before committing, not after.

---

## 8. Slice 1

Shell · tokens · the six primitives · player · routing · **`/meeting`**.

`/meeting` is chosen because it exercises the entire idea at once: both sources
(agenda spine + transcript), the zoom axis in both directions (out to the
archive, in to an item and a moment), the player, the SpeakerChip, provenance,
and virtualization. If the organising idea is wrong, it will be obvious here
while it is still cheap.

Concretely done when: a meeting with a published agenda and a recording renders
its spine, seeks the video, scrolls the transcript in sync, names speakers with
their office and confidence, links out to the county portal, and reads well on
a phone — and a meeting with *no* recording, and one with *no* agenda, both look
deliberate rather than broken.

---

## 9. What is built, and what the building taught

Status lives in `STATE.md`; this section records only what changed the *plan*.

**Built:** `/meeting` (slice 1), `/item` and `/case` (slice 2), `/` on a time
axis (slice 5, taken early at the maintainer's request), `/search` (slice 3),
`/ask` (slice 4), `/admin` (slice 6, the curation console — queues ordered by
impact, evidence beside the write, D1 auth). **Not built:** `/person`.

Six things the material taught that §1–§7 did not anticipate:

**The zoom axis needed a diff, not just a list.** §4 says a case shows "only
what varies" per step. In practice the official title is 62 words repeated
twelve times with two clauses moving, so "only what varies" is a *word-level
redline* against a title stated once — additions marked, deletions struck,
unchanged stretches elided. Presented as a list of fields it would have shown
nothing; presented as a redline it shows exactly where the application changed.

**Provenance is a pair, not a property.** §5 lists ProvenanceMark as one of six
primitives, which implied "define it once and it is right everywhere". It is a
*contrast* relationship: the mark passed AA on slice 1's surfaces and failed
the moment it was placed on the record's warm paper, because it borrowed a
border colour for text. A shared primitive is only correct against the
backgrounds it is actually used on.

**Coverage is the story, not the caveat.** §4 asks `/` to state "what fraction
has agenda / minutes / recording" as orientation. Drawn on a time axis it turns
out to be the most informative thing on the page: there are no recordings at
all before 2018 against twelve years of published record, and that shape
answers "where is the video for 2016" before anyone asks it. Coverage deserved
a visual axis, not a statistic.

**A source can be blind in one direction and not the other.** §2's table says
each source *fails* differently — the record by being absent, the transcript by
being wrong. Building "where the board disagreed" showed the sharper version:
they are blind in different directions, and neither gap is the other's. The
minutes name dissent formally and arrive weeks late, so the most recent
contested meeting is always missing from them. The recording is immediate and
catches division that produces no motion at all — an hour of argument over
licence-plate cameras that ended without a vote left no disposition to find,
and so was invisible on a page whose entire subject is disagreement. Every
"where is X" surface should be built as two queries from the start, not one
query with the other source as a fallback.

**The unit of a summary is not always the object.** §4 lists the zoom levels
and it is tempting to treat the finest one as the unit of every list. "Recent
decisions" as a list of items showed eight of the 113 things decided on one
day, chosen by sequence number — an arbitrary sample under a heading that
implied a summary, and eight rows repeating one date and one body. The right
unit was a level up: the meeting-day, with counts and the exceptions named. A
list is a summary only when its unit is coarser than what it summarises.

**Showing the work beat describing it.** §4 says Ask "streams its stages", and
the requirement named four: planning → retrieving → reading → answering. Under
D9 there are no fixed stages to name — the agent decides what to call and in
what order — so the page streams the calls themselves. That turned out to be
the better surface by a distance, for a reason the plan did not anticipate: it
shows the agent looking somewhere and finding nothing. "Searched the published
record for 'school zone speed cameras' → 0 items" is the evidence for an answer
that says the county published no outcome, and no summary of it is as
convincing as watching it happen. A rejected call renders as rejected, so the
reader also sees the thing correct itself, which is the argument for D9 made
without a word of explanation.

**The archive's shape is not the reader's shape.** §1 says the product is one
object graph seen at five zoom levels, and that is still the right description
of what is here. It is not a description of how anyone arrives. A resident does
not have a question about the archive; they have a question about their own
situation — is something being built near me, how did my commissioner vote,
what did this cost, what is coming up. Of those four plus "what has the county
said about X", the site as built serves exactly one, because every entry point
it has is archive-shaped: browse by month, search by words, ask a question.

The correction, from the maintainer: **decide what a resident needs to see, then
work out what to mine for it.** Reversing that — cataloguing what is extractable
and looking for a use — is precisely how §7's refused "dashboard of metrics"
gets built, one defensible attribute at a time. UI_REQUIREMENTS §5.9 specifies
three of the missing doors on that basis; a fourth, place, is left unspecified
until the geocoding spike says whether it can be honest.

**A pane sized by a constant is a pane that does not fit.** §6 describes the
meeting layout as a sticky rail beside a virtualised transcript and says
nothing about how either is measured, so both ended up sized by a guess at how
much chrome sits above them — `14rem` in one, a spacing token in the other.
The real answer is 452px, it varies with the masthead, and being wrong by a
constant means being wrong at every screen size at once. The masthead is the
part that varies, so the panes have to be measured against it rather than
against `100vh`. Two rules that came out of it and generalise past this page:
a scrolling box only clips descendants it is the containing block for, and a
`minmax()` floor is a floor — below it the track stops fitting the screen.
STATE.md gotchas 92 and 93 carry the measurements.
