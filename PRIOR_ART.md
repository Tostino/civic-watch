# Prior art — civic record UIs

Reviewed while planning the UI rebuild. Companion to `UI_REQUIREMENTS.md`.
Screenshots were taken live; what follows is what is worth taking, what is
worth avoiding, and which of our open requirements each one answers.

Two passes so far. §§1–5 are the original review of civic record UIs, and its
requirements are built. §6 is a later pass on clipping and quoting *(added
2026-08-14)*, whose requirements are pending and blocked on a §10 non-goal.

---

## 1. Chicago Councilmatic (DataMade) — the closest analogue

`chicago.councilmatic.org` · open source · tracks a city council's legislation,
committees, meetings and members.

This is the nearest thing to what we are building and the most directly
useful. Nav: About · Notes · Find Your Ward · Compare Alders · Divided Votes ·
Committees · Meetings · Legislation.

### Take

**Teach the search box by example.** Placeholder: `police, zoning, O2015-7825,
etc.` — in six words it tells you that topics AND identifiers both work. Ours
says "Ask anything about these meetings…", which teaches nothing. We have
exactly this duality (subject words vs `PDE-25-7738`, `R-58`) and never say so.

**A promise in the masthead.** "Chicago City Council, demystified." Then, in
plain prose, what the body *is*: how many members, how often it meets, who the
officers are. A resident arriving cold is oriented before they search. We
currently assume the reader knows what a Board of County Commissioners does.

**Result cards anchored on identifier + status.** The heading is
`Ordinance O2026-0027486` with an `Active` badge; the plain-language title sits
underneath; then a metadata row of date + action, sponsor, and topic tags.
Maps directly onto our `code` + `outcome` + `title`.

**Faceted left rail** — Status, Type, Topic, Controlling Body, Sponsor,
Session — with an explicit `Order by: Date | Title | Relevance`. Answers
R5.6.2, and settles that filters belong in a rail rather than a menu.

**RSS feed for a *search*, not just a page.** Subscribe to a query. For us:
"tell me when anything happens on Orange Belt Trail" — which is exactly what an
applicant, an objector or a reporter wants, and it costs almost nothing given
we already thread cases.

**A History table on the item itself** — `Date | Legislative body | Action`.
Note they put the thread *on* the item rather than in a separate view. We split
`/item` and `/case`; theirs argues the case history belongs inline on the item,
with the dedicated view for depth. R5.3.3 already says "show the case thread
inline" — this confirms it.

**Locations mentioned, on a map.** They extract addresses from the ordinance
and plot them. **This is the strongest idea I found that is not in our
requirements.** Our land-use items are saturated with geography — "Located
South of County Line Road North and East of Lake Iola Road", "approximately 80
acres". A map of what is being rezoned, and a "what is happening near me" view,
would be the single most compelling thing a resident could be given. It is also
achievable: the addresses are in the published titles we already parse.

**The source document, embedded.** The actual ordinance PDF renders inline,
with a download. We hold every agenda and minutes PDF in `portal_files` and
show none of them. For a project whose entire thesis is "the published record
is authoritative", showing the county's own document is the strongest possible
provenance.

**"View on the Chicago City Clerk website".** A link back to the authoritative
upstream. We have `portal_event_id` and never link out. Every item and meeting
should offer "see this on the county's portal" — it is both honest and a
trust-builder.

**Divided Votes** as a first-class section — the contested decisions,
separated from the unanimous ones. We can approximate this today: the minutes
record dissent verbatim ("with Commissioner Weightman absent from the vote",
"with Ms. Pearson voting nay"). A "where the board disagreed" view is a genuine
story surface and needs no new data.

### Avoid

Routine and non-routine legislation are visually identical, so a page of
residential-permit-parking ordinances looks exactly like a page of consequential
rezonings. They classify it (`Routine` / `Non-Routine` tags) and then do not use
the classification to shape the page. We have the same problem in sharper form —
consent-agenda items vs regular items — and should let it drive hierarchy, not
just a chip.

---

## 2. TheyWorkForYou (mySociety) — speech-level records

`theyworkforyou.com` · Hansard made navigable · speaker-attributed, per-speech.

The closest prior art for *attributed speech*, which is our hardest problem.

### Take

**Every speech has its own URL.** Not the debate — the individual contribution.
With `« Previous speaker · See the whole debate · Next speaker »` navigation, so
you can walk a debate one turn at a time or zoom out. Our passages have ids and
no addressable page.

**An official citation line.** `(Citation: HC Deb, 15 January 2024, c559)` — a
formal, quotable reference. We should mint the equivalent: body, date, item
code, timestamp, so a journalist or an attorney can cite a moment in a form that
survives our URLs. This matters more for us than for them: our transcript is
machine-generated, so the citation must point at the *recording*, which is the
primary source.

**Speaker shown with their role at the time** — "Minister of State (Home
Office) (Security)", not just a name. We have `meeting_roster` with offices per
meeting, so we can say "Chair" or "Vice Chair" *as of that meeting* rather than
a bare surname. Cheap, and it makes procedural exchanges legible.

**Search scoped to an entity.** The person page carries "Search this person's
speeches". Generalises well: search within this meeting, this case, this
person. A scoped search box on an entity page is a small feature with a large
effect on how usable the archive feels.

**A person page that explains the job.** Photo, party, constituency, then
plain-English "MPs split their time between Parliament and their constituency
…", then "What you can do". Sub-nav: Overview · Speeches · Committees ·
Voting Summary · Recent Votes · Register of Interests. Directly shapes
`/person` (§5.7).

**"Get email updates" on a person; "Alert me about debates like this."**
Subscription attached to entities, not just to a search.

---

## 3. CourtListener / RECAP (Free Law Project) — dockets over time

`courtlistener.com` · federal court dockets, filings, oral argument audio.

### Take

**Personal annotation on a public record** — `Add Note`, `Tags`. A researcher
following a rezoning across two years wants to leave themselves notes. Cheap to
add once READERS have accounts — the D1 token behind `/admin` is operator auth
on a loopback-only interface and is not a step towards this.

**`Get Alerts` per docket**, and **`View on PACER`** — again, the link to the
authoritative upstream. Two independent sources converging on "always link out
to the source of record" is a strong signal.

**A citation line**, same as TheyWorkForYou. Three of three serious civic
archives publish a canonical citation string. We should treat that as table
stakes rather than a nicety.

---

## 4. CivicClerk — the incumbent we are replacing

`pascocofl.portal.civicclerk.com` · the county's own portal, and the source of
our agendas and minutes.

Worth studying honestly, because it is what a Pasco resident uses today.

### It does fine

Event cards are legible: a bold date block, the body as a chip, the location,
a download affordance. `Past Events` / `Coming Up` split. Calendar and filters.
The date-block card is a reasonable reference for our browse view.

### What it cannot do — and this is our whole opportunity

- **"Search all content" searches documents, not speech.** Nothing anyone said
  is searchable. 1,036 hours are effectively unindexed.
- **A meeting is a folder, not a structure.** Four tabs — Overview, Media,
  Files, Share. To learn what happened you open a PDF and read it.
- **Outcomes are buried in prose.** No item carries its disposition; the
  minutes must be read to find out whether something passed.
- **No threading.** A rezoning that appears at twelve meetings over ten months
  is twelve unrelated events.
- **No speaker attribution anywhere.**
- **Video lives elsewhere** (YouTube), unjoined to the agenda.

**CivicClerk is a filing cabinet. We are building a record.** That sentence is
probably the positioning for the whole product, and it is worth putting on the
home page in some form.

---

## 5. Synthesis — what to actually adopt

Ranked by value against effort, given what we already hold.

| | pattern | source | why us | state |
|---|---|---|---|---|
| 1 | **Link to the upstream source** on every item and meeting | Councilmatic, CourtListener | we hold `portal_event_id`; honest, and free | **built** |
| 2 | **Embed the source PDF** (agenda / minutes) | Councilmatic | we hold every file; strongest possible provenance for R2 | **built** |
| 3 | **Canonical citation string** per passage and item | TWFY, CourtListener | makes the archive quotable; three of three do it | **built** |
| 4 | **Teach the search box by example** | Councilmatic | one placeholder change; we have the same topic/identifier duality | **built** |
| 5 | **Entity-scoped search** | TWFY | small feature, large effect on navigability | not built |
| 6 | **Speaker's office as of that meeting** | TWFY | `meeting_roster` already holds it | **built** |
| — | ~~Subscribe to a case or a query~~ | Councilmatic, CourtListener | **deferred** — see UI_PLAN §7 | — |
| 8 | **"Where the board disagreed"** | Councilmatic (Divided Votes) | dissent is verbatim in the minutes today | **built** |
| — | ~~Map of land-use items~~ | Councilmatic (Locations) | **deferred, spike first** — UI_PLAN §7 | — |
| 10 | **Orientation prose — what this body is** | Councilmatic, TWFY | a resident arrives cold | **built** |
| 11 | **Citation, verified** | none of them | a model that cites will cite; whether the id exists is a separate question | **built** (slice 4) |

1, 2, 3, 6, 8 and 10 landed in slices 1, 2 and 5; 4 landed in slice 3; 11 is
ours rather than anybody's — none of the archives reviewed publishes a
machine-written answer, so none of them had to face what happens when the thing
writing the citations is also the thing that can invent them.
Subscription and the map are deferred by decision — the reasoning is in
`UI_PLAN.md` §7, including the spike that should precede any commitment to the
map. Only 5 is left, and it is cheap now: scoped search is the same tool call
with `case=` or `speaker=` already bound.

Three notes from having built the rest:

- **Embedding the PDF needed a proxy.** CivicClerk serves every file with
  `Content-Disposition: attachment`, so a cross-origin frame downloads it and
  shows an empty box. `web/server.py:_file()` re-serves the same bytes as
  `inline`. That is the whole trick, and nothing in Councilmatic's version
  hints at it.
- **"Where the board disagreed" is `voting nay` and nothing else — in the
  minutes.** "absent from the vote" appears on 556 items against 114 for real
  dissent, and it is an absence, not a disagreement. Counting it would have
  made the board look five times more divided than it was. But the minutes are
  only half of it: they are published weeks late and they record no
  disagreement at all when an argument produces no motion. Councilmatic has
  one source and can stop here; we have two, so the section reads both and
  keeps them in separate lanes.

- **Teaching the search box by example is the cheap half.** The expensive half
  is making the identifier case actually work. `websearch_to_tsquery` tears
  `R-58` into fragments that match nothing, so a placeholder promising it
  would have been a lie; a code needs its own query against `code` and
  `case_id`. Councilmatic's placeholder implies this and does not say it.

### Requirements this produced

Now in `UI_REQUIREMENTS.md`, not pending:

- **R4.4** link every item and meeting to its record on the county portal
- **R5.3.5** show the source agenda/minutes document inline on an item
- **R6.7** a `Citation` primitive — canonical, copyable, pointing at the
  recording
- **R5.6.4** a search placeholder that demonstrates a topic *and* an identifier
- **R5.2.5** speaker names carrying the office held *at that meeting*
- **R5.1.4** curated entry points, including *where the board disagreed*

---

## 6. Clipping and quoting — a second pass *(2026-08-14)*

The question: how a reader assembles a set of moments — **not necessarily from
one meeting** — into something that has a URL and can be sent to somebody.
`UI_REQUIREMENTS.md` §10 says "Not a video editor or clipping tool (for now)",
and this pass is about whether the "(for now)" has expired.

Three of the five sources below could not be used as intended, which is itself
most of the finding: the two closest analogues are a paywall and a set of dead
links, and the platform we depend on for playback has retired its own version
of this feature. Recorded in detail so the next pass does not spend the time
again.

### 6.1 Internet Archive TV News — the whole loop, live and worth copying

`archive.org/details/tv` · 4,354,000 broadcasts since 2009 · captions
searchable; clips are called **quotes**.

The only complete, working, publicly-usable clipping tool over a civic-adjacent
archive that this pass could reach. Everything below was seen live.

#### Take

**The clip renders *inside* the broadcast, not on a page of its own.** A quote
appears as an outlined card sitting in the programme's filmstrip at the segment
it covers, with the surrounding captions still readable on both sides. The card
carries `Quoted by <origin> on <date>` and `Edit this quote`.

This is the strongest idea in the pass. The failure mode of clipping a public
proceeding is decontextualisation, and their answer to it is not a disclaimer —
it is a layout. You cannot look at the clip without seeing what came before and
after it. That is the same instinct as scoring coverage rather than counting
strings, and it costs us nothing: `TranscriptView` already renders the
surrounding rows.

**Selection is made on the text, not on a timeline.** The editor says *"Edit
your quote by selecting from the captions below"* and you drag across caption
text. There is no scrub handle and no mark-in/mark-out. The clip is a range of
captions, and the player follows.

**The 60-second cap looks structural rather than editorial.** The broadcast is
laid out as a filmstrip of 60-second segments — thumbnail above, captions below
— so the cap on a quote is the unit the archive is already built from. They do
not say this anywhere; the layout does, and it is an inference. Either way the
lesson holds: our equivalent unit exists and is finer, the utterance,
`(video_id, idx)`.

**Clips are a browsable layer over the archive** — `Recent Quotes` and
`Trending Yesterday` shelves on the front page, each card showing source
programme, station, exact datetime, who quoted it, and the caption text.

#### Avoid

**The counters.** Each quote shows plays, views, stars and reposts. That turns
a record into an engagement surface and rewards clips that travel over clips
that are true. It also requires storing every reel server-side and counting
readers, neither of which we want.

**The share row.** Eight third-party buttons — Twitter, Facebook, Reddit,
Tumblr, Pinterest, email, embed, Bluesky. A page whose point is that the
county's record is readable without being watched should not ship a row of
social SDKs. One copy-link and R6.7 is the whole requirement.

### 6.2 Reduct — the multi-source half

`reduct.video` · transcript-based video editing · customers include the
Colorado State Public Defender, which is the same shape of problem: building an
argument out of cited moments in a recorded proceeding.

Reviewed from their product pages, **not from the running application**, which
is behind a login. The screens below are their own marketing captures of real
UI, so the interaction is trustworthy and the behaviour under load is not
attested.

#### Take

**Selection pops a menu, and the menu shows the duration.** Selecting
transcript text opens `22s selection` — the length, live, while you are still
choosing — over a keyboard-first menu:

```
h   Highlight and label
r   Add to reel
d   Download
```

The live duration is what makes a length cap legible as a fact of the interface
rather than as an error message afterwards. `r → Add to reel` is one keystroke
from a transcript selection, and it is exactly the affordance §5.10 needs.

**"Stitch together multiple highlights from different recordings to compose a
narrative. Just drag and drop relevant highlights into a reel."** Their words.
The reel renders as stacked cards — each with its source and its own transcript
text — reorderable by drag. This is the requirement, already built by somebody.

#### Avoid

**Its other half.** The neighbouring feature deletes words from inside a clip
to cut tangents and filler. That is the line for us: reorder and juxtapose
whole utterance ranges, never elide *within* one. A compilation, not an edit.
A tool that can cut a word out of a commissioner's sentence is a tool that will
eventually be used to.

### 6.3 C-SPAN — still unreviewable, and now for a different reason

The not-yet-reviewed list carried "blocked by their CDN, worth retrying" from
the first pass until this one. It is worth being precise about what that block
is now, because it read as a retryable failure and is not one:

- `www.c-span.org` returns `302` to `tollbit.c-span.org`, which answers
  `402 Payment Required` to automated fetches. Tollbit is a paywall for bots.
- The same URLs in a real browser get CloudFront's
  `ERROR: The request could not be satisfied`.

So the best-known clip tool in civic media cannot be examined first-hand at
all, and the secondary descriptions are what we have: mark-in/mark-out over a
transcript-synced player, a permalink and an embed per clip, and clip pages
labelled **"User Clip"** so a reader can tell a citizen's cut from C-SPAN's own
programme. That last one is worth keeping even second-hand — it is the
provenance distinction R2 already makes, applied to authorship.

### 6.4 Hyperaudio — the idea outlived the software

The Hyperaudio Pad (~2012) is the closest thing anyone has built to what we
want: assemble a piece by copying blocks of *timed transcript* from **multiple
sources**, with the transcript as the timeline. Every running instance is gone:

- `happyworm.com/clientarea/hyperaudio/hap/v22/` — "No forwarding set"
- `hyperaudio.github.io/hyperaudio-remixer/pad.html` — GitHub Pages 404
- `hyper.audio` — now a gated marketing site behind "request a demo"

Recorded because it changes what this pass could deliver: there is no running
example of transcript-driven multi-source assembly left to copy, so §5.10 is
being specified from write-ups and from Reduct's adjacent version of it.

One thing to take from the write-ups and one to leave. Take: the transcript
selection *is* the clip. Leave: the word-processor framing — copy, paste and
delete at word level — which invites cutting speech into sentences nobody said.

### 6.5 YouTube — the platform retired its own version

**Viewer-made Clips were replaced by "Share at Timestamp" in April 2026.**
Existing clips still resolve; new ones cannot be made. D2 already accepts that
we do not control these videos; this is a second instance of the same risk,
against a *feature* rather than a recording. Nothing in §5.10 may depend on the
Clips feature, and the reel is built from seeks in our own player instead.

The mechanics we do depend on, measured against the IFrame API documentation:

- `loadVideoById({videoId, startSeconds, endSeconds})` supports `endSeconds`,
  but **it does not fire `ENDED` there**, and any later `seekTo()` cancels it.
  So advancing from one clip to the next MUST come off the position poll that
  `PlayerProvider` already runs at 250ms. There is no event to wait for.
- Crossing to another recording reloads the iframe — a real gap of a second or
  two. That gap is a seam between two different days and should be shown, not
  papered over.
- Required Minimum Functionality: viewport at least 200×200, no autoplay until
  more than half the player is visible, no more than one auto-playing player
  per page, and no overlay obscuring the controls. Seeking and auto-advance are
  not restricted.

### 6.6 What this pass concluded

**We have already built the reel and called it something else.** `answers.cites`
is this, exactly:

```
{"passages": [{"video_id": …, "start_idx": …, "end_idx": …}, …],
 "items": [17923, …]}
```

An ordered set of cited moments, spanning any number of meetings, resolved back
out of the live archive at render time, served at a permalink, deliberately not
indexed, with a page that already reports how many citations no longer resolve.
A saved answer is a reel whose prose happened to be written by the agent; a
reader's reel is the same row with the selection made by a person.

Everything expensive is therefore already paid for — `tools.passages_at`
resolves the ranges, redactions and speaker corrections reach the page for free
because no words are stored, `/ask/<id>` is the page pattern, and the "no old
copies" rule in `web/answers.py` holds without a new idea in it.

**Authorship is where a reel differs, and it is the only hard problem.**
`web/answers.py` refuses to expose a public POST that mints a permanent URL
from attacker-supplied content — "there is no version of that which is not a
defacement vector" — and dodges it by having the server write the row from an
object the server itself produced. A reader's reel cannot use that dodge,
because the selection is theirs.

The resolution is to make an anonymous reel contain **no reader-written text at
all**: no title, no notes, no commentary. A reel is then a list of utterance
ranges — a query, not content — and there is nothing in it to deface a domain
with. It is carried in the URL and stored nowhere. The page titles itself from
what is in it. The selection *is* the argument; the juxtaposition does the
work, which is what a Reduct reel demonstrates and what the Archive's shelves
are made of.

The moment somebody wants words around it — a title, a paragraph, a curated
entry point under R5.1.4 — that is an operator act through the existing admin
surface, at a keyboard, behind D1. Two tiers, one table, and the boundary falls
exactly where authored text begins.

**Rendering an actual video file is refused for now**, and not on effort
grounds. An exported MP4 is a *copy*, and it is the only artifact a later
redaction cannot reach: `bin/redact.py` reaches the utterance, the passage, the
index and the answer's prose, and it cannot reach a file somebody downloaded in
March. No video is retained locally in any case — `data/<video_id>/` holds
`audio.flac`, the diarization and the transcript, and no picture — so the
honest artifact, if one is ever needed for a platform an embed cannot cross, is
an audiogram cut from that audio on the workstation by an operator. Re-cutting
the county's video would mean fetching it back from YouTube and re-hosting it,
which is the one thing the whole design has avoided. Not first, and possibly
not ever.

### Requirements this produced

**Pending — not yet in `UI_REQUIREMENTS.md`.** §10 currently forbids this
outright ("Not a video editor or clipping tool (for now)") and that line has to
be settled before any of these land.

A new surface, §5.10 The reel (`/reel`):

- **R5.10.1** A clip MUST be `(video_id, start_idx, end_idx)` — the same key
  `answers.cites` uses. Never seconds, never stored text. Seconds are derived
  at render, so a correction or a redaction reaches every reel that quotes it.
- **R5.10.2** Selection MUST snap to utterance boundaries. Word-level timings
  exist in `data/<video_id>/transcript.json` and were never loaded, so a handle
  offering finer precision would be claiming an accuracy the archive does not
  have.
- **R5.10.3** A reel MUST be assemblable across meetings. The tray is therefore
  app-global state, a sibling of the player, and not a page's state.
- **R5.10.4** A clip MUST render with its surrounding transcript, expandable in
  place. A clip shown alone is a quote with its context removed, and this is the
  requirement that keeps the tool honest.
- **R5.10.5** The reel page MUST be readable with the player dead — an ordered
  list of quotes, each with speaker, meeting, date and R6.7 citation (D2).
- **R5.10.6** An anonymous reel MUST carry no reader-written text, and MUST be
  carried in the URL rather than stored. See §6.6.
- **R5.10.7** A durable `/reel/<id>` MUST be an operator act behind D1, and MUST
  NOT be indexed — the archive's own pages are what a search engine should be
  sending people to, as `/ask/<id>` already decides.
- **R5.10.8** Playback MUST advance off the position poll, not off a player
  event, and the reload gap between two recordings MUST be shown rather than
  hidden (§6.5).
- **R5.10.9** A clip whose range no longer resolves MUST be reported and
  counted, never silently dropped — the behaviour `answers.load` already has.
- **R5.10.10** A clip MUST have a length cap, and the duration MUST be visible
  *while* selecting. The cap is what makes this a quoting tool rather than a
  re-hosting one, which is the lesson of §6.1. 90 seconds is the proposed
  number and is **a judgement, not a measurement**: an utterance averages 12.5
  seconds (298,737 of them across 1,036 hours), so 90 is about seven of them —
  long enough for a question and its answer, short enough that a reel of ten is
  still a reel. Revisit it once real reels exist rather than defending it now.

A new component under §6:

- **R6.9 ClipTray.** One global tray, persisted locally, that any surface
  rendering a `Line` or a `TranscriptHit` can add to — transcript, search hits,
  an item's runs, an answer's evidence. The affordance is keyboard-first, and
  is the one place a length cap and a boundary rule are enforced.

---

## 7. Not yet reviewed

- **Documenters.org** (City Bureau) — human note-takers at civic meetings;
  relevant to how notes and transcript coexist.
- **A transcript editor** (Descript, Otter) — the speaker-correction
  interaction in §5.8 is a solved problem in that category and we should copy a
  working one rather than invent.
- **GovTrack** — bill status timelines, for `/case` (§5.4).
- **Snipd** — capture-in-flow highlighting: one keystroke while listening saves
  a guessed boundary, refined later. Relevant to R6.9 and not reached in this
  pass.
- ~~**C-SPAN video library**~~ — moved to §6.3. Not retryable: it is a paywall
  now, not a rate limit.
