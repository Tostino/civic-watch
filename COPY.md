# Copy conventions — Pasco County meeting record

Supplied by the maintainer 2026-08-13 and applied to the shipped copy from
that date. The final section arrived cut off mid-sentence; the truncation is
marked rather than guessed at.

## What the owner asked for, in his words

1. Copy across the whole site must be **professional, consistent, and in my
   voice (how I type)**.
2. Use **ASD-STE100 (Simplified Technical English)** so it is readable by the
   residents of this county.
3. If we need technical words, **have a glossary section**.
4. **Only allow the specific technical words we need to make the site work** —
   the glossary is a controlled vocabulary, not a dumping ground.
5. On copy that described the system's own verification machinery — "and
   citations it cannot support are removed before you see the answer" — the
   response was *"Should it even say that?"* The answer was no. **Do not
   narrate your own plumbing to the reader.**

## ASD-STE100, as it applies here

- **One word, one meaning.** Pick a term and never vary it for elegance. An
  item is "located in the recording" everywhere, or nowhere.
- **Descriptive sentences ≤ 25 words.** Procedural sentences ≤ 20.
- **Active voice.** "We could not place these in the recording", not "these
  could not be placed".
- **One instruction per sentence.**
- **Use the simplest verb form.** No "in order to", no "utilise".
- **Technical Names are allowed** where they are the real name of a real thing
  — an agenda code, a case number, a body's name. They are not jargon.
- **No noun stacks.** "The published agenda for this meeting", not "meeting
  published agenda coverage state".

## The controlled vocabulary (owner's rule 4)

One name per thing. Changing one means changing it everywhere in the same
commit, because two names for one thing is how a reader concludes they are two
things.

| The thing | Say | Never, and why |
|---|---|---|
| what the minutes recorded for an item | **outcome** (`agenda_items.outcome`); the county's sentence itself is the **outcome text** | "disposition". Of 791 substantive minutes files, 19 use that word and every one means DISPOSAL: "Records Disposition Request", "Disposition Of Animals". It was our coinage, colliding with the documents we link to as authoritative. |
| the video of a meeting | **recording** | "video". Was 29 uses against 7 before it was settled. |
| what the county published | **the published record** | "the official record". Published states what the county did; official is an assessment of it. |
| the two sources, as headings | **What the county recorded** and **What was said** | "In the record" / "In the room". Browse's disagreement lanes say "What the minutes recorded", because that lane really is minutes-only: the shared thing is the form, not the string. |
| an item's place in a recording | **located in the recording** | "bound to", "matched to". `bound` is the pipeline's word for an `item_spans` row and is an internal token by the last rule below. **NOT YET DONE: six sites still say "bound" or "matched"** (TranscriptView, MeetingView, AgendaSpine, ItemView, Hits). |


## Derived conventions, observable in the shipped copy

- **Say what is missing, not just what is present.** "In the published record;
  not located in any recording." Absence is information.
- **State the limit in the same breath as the claim.** "Machine transcription
  of the recording. It shows what was said, not what was decided, and it can
  be wrong."
- **Numbers are concrete, load-bearing, and MEASURED.** "84% of 235 lines
  carry a name." Never "many" or "most" where a count is available. A count is
  only "available" if the page reads it from the archive at request time:
  seven counts were typed into JSX and four had gone wrong by the time anyone
  checked, including one that went wrong the same afternoon a parser fix
  landed. `/api/facts` serves them now, and `tools.facts()` serves the same set
  to the model, so a page and an answer cannot disagree. Two rules follow from
  it, both added 2026-08-18:
  - **A count belongs in ONE place.** Browse carries the coverage panel; no
    other page repeats hours, year spans or archive totals. A count that
    survives elsewhere has to be about the thing in front of the reader, like
    how thin the transcript is where transcript results are listed.
  - **If it cannot be measured, do not state it.** Every sentence that quotes a
    number has a second form without one, used when the measurement fails.
    That is the one place "a minority of decided items" is right rather than a
    hedge.
- **Attribute every claim to its source.** "From the minutes the board
  approved." / "Derived by this archive from the recording."
- **First person plural for our own limits** ("we could not place these"),
  never for the county's record.
- **Sentence case** for notes and labels. Title Case only for proper names.
- **No em dash in reader-facing copy.** This said "em dash for the aside,
  spaced or unspaced consistently per component" until 2026-08-18, when the
  owner said he disliked them. A comma, a colon or a full stop does the same
  work. Code comments, commit messages and this file keep theirs: the rule is
  about what a reader sees. That carve-out is an inference, not something the
  owner said; ask if it matters.
  **PARTLY DONE: /search and browse are clean; 40 remain in 19 other files**,
  and most are in `title` and `aria-label` attributes rather than in body
  text, which is why a first sweep of the rendered pages missed them
  (SpeakerChip 5, Issues 4, Answer 4, SourceDocument 3, ItemCard 3, TimeAxis
  3, RecordView 3). Two of those are a lone "—" standing for "no value" in a
  table cell, which is a typographic convention rather than an aside, and
  wants a decision rather than a substitution.
  The county's own agenda titles are full of them ("PVAS No. 3354 (Regular)
  ... Board of County Commissioners"). Those are quoted verbatim and stay, so
  a page can show one that is not ours.
- **American spelling.** "license plate cameras", "cataloged". (British
  spellings crept in from Claude and were corrected.)
- **No marketing register.** No "powerful", "seamless", "simply". The archive
  states facts about a public record.
- **Never print an internal token where a name goes.** A diarization cluster id
  is not a name; `bulk_consent` is a pipeline value, not English.

## Still unbuilt

The **glossary page** was specified and never built. The STE pass produced 12
definitions grouped by where a reader first meets each term. One open question
was left undecided: whether to define `MPUD`, which appears only inside titles
the county wrote and w— *[the source document was cut off here. The 12
definitions were not in what arrived; recover them before building the page,
or the glossary will be a re-derivation rather than the reviewed set.]*
