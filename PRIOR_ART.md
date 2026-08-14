# Prior art — civic record UIs

Reviewed while planning the UI rebuild. Companion to `UI_REQUIREMENTS.md`.
Screenshots were taken live; what follows is what is worth taking, what is
worth avoiding, and which of our open requirements each one answers.

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

## 6. Not yet reviewed

- **C-SPAN video library** — the best-known video/transcript sync and clip
  sharing in civic media. Blocked by their CDN during this pass; worth
  retrying, because it is the closest prior art for `/meeting` (§5.2), which is
  the page we are building first.
- **Documenters.org** (City Bureau) — human note-takers at civic meetings;
  relevant to how notes and transcript coexist.
- **A transcript editor** (Descript, Otter) — the speaker-correction
  interaction in §5.8 is a solved problem in that category and we should copy a
  working one rather than invent.
- **GovTrack** — bill status timelines, for `/case` (§5.4).
