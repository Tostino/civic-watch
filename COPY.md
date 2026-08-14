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

## Derived conventions, observable in the shipped copy

- **Say what is missing, not just what is present.** "In the published record;
  not located in any recording." Absence is information.
- **State the limit in the same breath as the claim.** "Machine transcription
  of the recording. It shows what was said, not what was decided, and it can
  be wrong."
- **Numbers are concrete and load-bearing.** "84% of 235 lines carry a name."
  Never "many" or "most" where a count is available.
- **Attribute every claim to its source.** "From the minutes the board
  approved." / "Derived by this archive from the recording."
- **First person plural for our own limits** ("we could not place these"),
  never for the county's record.
- **Sentence case** for notes and labels. Title Case only for proper names.
- **Em dash for the aside**, spaced or unspaced consistently per component.
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
