# civic-watch - Searchable archive of local government meetings

`civic-watch` mirrors what a county publishes about its meetings, transcribes
the recordings, and lets you ask questions against both. The first instance is
**Pasco Watch**, covering Pasco County, Florida, at
[pasco.watch](https://pasco.watch). `civic-watch` stays the name of the code
and of the container image; Pasco Watch is what the site calls itself.

Two different things go into the archive. The county publishes agendas and
minutes, and that's the authority on what was decided. It covers every meeting
whether or not anyone filmed it. Then there's the transcript, which is the
authority on what was said, and it only exists where there's a recording, which
reaches a minority of the decided items.

The transcript side is machine made from end to end. Audio comes down as 16 kHz
mono FLAC, pyannote works out who spoke when without having any idea who
anyone is, and Parakeet transcribes the voiced windows. Getting names onto
those anonymous voices is the hard part, and it happens in stages: cluster the
voices across the whole archive, anchor a few clusters using the published
chair roster, match the rest against whoever the roster says was in the room
that day, then run an LLM pass over the text that has to find a verbatim quote
before it's allowed to claim a name. Anything a person fixes by hand outranks
all of that and survives a full rebuild.

None of it is exact, and the archive tries not to pretend otherwise. Every
passage carries how well its name is known, which is a different question from
whether it has one.

This has only ever run against one county, and Pasco is baked in deeper than it
should be. The catalog stage knows about a YouTube channel, the portal
stage speaks CivicClerk, and the roster and naming code carry local
assumptions. So moving it somewhere else is a real port and not a config
change. It's the kind of port a model is good at, though. Hand an LLM this repo
as the worked example, along with your own county's documents and its video
archive, and most of the job is rewriting the catalog and portal stages to
match what your municipality actually publishes. The schema, the retrieval and
the agent should carry over.

# Table of Contents
  * [How this was built](#how-this-was-built)
  * [Layout](#layout)
  * [Pipeline](#pipeline)
  * [Running it](#running-it)
  * [Pages](#pages)
  * [Keeping it current](#keeping-it-current)
  * [A rant on reading votes out of a transcript](#a-rant-on-reading-votes-out-of-a-transcript)
  * [What it gets wrong](#what-it-gets-wrong)
  * [Who is asking](#who-is-asking)
  * [Redaction](#redaction)
  * [Documents](#documents)
  * [Not the official record](#not-the-official-record)

## How this was built

I built this with Claude Code over about two weeks. Almost all of the code was
written by the model. My own work went in elsewhere: steering the design, spot
checking the data coming out of each stage, labelling speakers by hand, and
testing the site the way a reader would use it. I haven't gone through the
whole codebase line by line, and mostly opened the code when the model was
making obviously bad choices.

## Layout

```
bin/      the pipeline, plus schema.sql and bm25.sql
web/      the reader API: retrieval, the tool surface, the agent, admin, routing
ui/       the front end (Next 16, React 19, TypeScript)
deploy/   Dockerfile, compose, and by-hand notes for Postgres and the edge
data/     per-video audio, diarization and embeddings, all gitignored
eval/     eval fixtures; the labelled ground truth is gitignored, see Redaction
```

Everything a reader touches comes out of Postgres 18 with pgvector. Retrieval
is a SQL implementation of BM25, an HNSW vector index, and curated thread keys,
all of it over passages that never cross a speaker boundary. The site doesn't
serve any media itself, since playback is just a YouTube iframe, which is nice
because it means the audio and video sitting on disk never have to be reachable
from the internet.

There are three virtualenvs, which looks silly until you try it with one. NeMo
and pyannote don't agree on which torch to pin, so `asr-venv` handles ASR,
`diar-venv` handles diarization, and `emb-venv` handles embeddings, the agent
and the web server.

## Pipeline

| stage | script | what it does |
|---|---|---|
| catalog | `catalog.py` | finds the county's published recordings |
| download | `download_worker.py` | 16 kHz mono FLAC plus silence points |
| diarize | `diarize_worker.py` | pyannote, saving speaker centroids |
| ASR | `asr_worker.py` | Parakeet over VAD windows, then audit and repair |
| speakers | `speaker_id.py` | clustering, anchors, per-meeting matching |
| naming | `name_speakers.py` | an LLM pass with verbatim-quote verification |
| corrections | `correct.py` | a human over an utterance range, outranking every derived layer |
| segments | `segment.py` | phase and subject boundaries, one LLM call per meeting-day |
| portal | `civicclerk.py` | mirrors the county's agendas and minutes |
| agendas | `parse_agenda.py` | agenda text into items, codes and case numbers |
| minutes | `parse_minutes.py` | minutes text into the outcome for each item |
| rosters | `roster.py` | who sat on the board, in what office, by date |
| domain | `land_agenda.py` | meetings, items and cases; binds transcript spans to items |
| chair | `chair_anchor.py` | anchors clusters to commissioners from the chair roster |
| affinity | `affinity.py` | whether a voice really sounds like the person its cluster names |
| passages | `index_passages.py` | retrieval units and their embeddings |
| agent | `ask.py` | plan, retrieve, read through several lenses, answer |
| check | `eval_agent.py` | whether the answer reached the evidence it needed |
| audit | `audit.py` | data invariants, in bulk, repairing nothing |

One nice surprise: the county publishes its agendas and minutes through an
unauthenticated CivicClerk OData endpoint, and it'll hand back its own
extraction of the PDF text if you ask for it. So there's no PDF parsing in here
at all, which is usually the grim part of a project like this.

## Running it

```bash
source ./env.local.sh                  # PASCO_DSN and LLM_API_KEY, gitignored, mode 600

bash bin/run.sh                        # ingest fleet: download, diarize, ASR
./emb-venv/bin/python bin/status.py    # progress

bin/serve.sh                           # reader API on :8765
npm --prefix ui run dev                # UI on :3000, proxying /api to :8765
```

Start the API through `bin/serve.sh` instead of calling `server.py` yourself.
If you call it directly it comes up fine, serves the whole archive correctly,
and then refuses every question, because the inference key only reaches the
process through `bin/_env.sh`. It ran that way here for weeks before anyone
noticed.

`PASCO_DSN` is what decides which database is real, and `bin/db.py` won't read
anything else, so a script run without it raises instead of quietly finding
some local cluster and doing the work there.

## Pages

```
/              browse: the collection, a year by month time axis, ways in
/meeting/:id   the meeting: agenda spine, roster, transcript, player
/item/:id      one agenda item: the record, the county's PDF, what was said
/case/:id      one application across every meeting that took it up
/search        both sources at once
/ask           the agent, streaming its real tool calls
/ask/:id       one kept answer, so a run can be sent to somebody
/about         what the archive holds and where it falls short, counted live
/admin         curation console: queues, corrections, redactions, ops
```

The admin console isn't reachable from the internet, and what does that is
which port answered rather than anything in the request itself. Curation binds
its own loopback listener, the public listener 404s every `/api/admin` path,
and the UI only proxies to the curation port when `ADMIN_API` is set, which the
production image doesn't set. There's also a peer check in `web/admin.py` on
top of that, and the comment above it goes on at some length about why you
can't trust a header for this. The version before this one locked the console
out of its own front end, and the obvious repair after that turned out to be
forgeable, so read the comment before you touch any of it. Reach the console
over an SSH tunnel.

## Keeping it current

```bash
./emb-venv/bin/python bin/civicclerk.py --events --text
./emb-venv/bin/python bin/catalog.py && bash bin/run.sh
bash bin/refresh.sh roster speakers names chair affinity segment land index eval
./emb-venv/bin/python bin/audit.py
```

The order `refresh.sh` runs in matters, and the script explains each dependency
right where it relies on it. Every stage is idempotent, so re-running one after
a failure just resumes. If you want to be sure of which code produced which
rows, `bin/rebuild.sh` drops all the derived tables and builds them again from
the same inputs in about twenty minutes, without re-downloading a video or
re-running ASR.

## A rant on reading votes out of a transcript

Say someone asks whether the board approved a particular rezoning. There are
two ways to answer that, and they are not equally good.

The first is to search the transcript, find the chair calling the roll, and
read off the names and the ayes. This works. It gives you a confident answer
with a quote and a timestamp attached, and it's the obvious thing to build.

The second is to read the outcome out of the minutes the board approved, and go
to the transcript only for what people said about the thing before they voted
on it.

I think the first one is wrong often enough that you shouldn't build it. Names
in this archive come out of diarization, then voice matching, then sometimes a
model reading the surrounding text, and every one of those steps has an error
rate. A roll call is close to the worst case for all three: it's fast, the
names are read by one person while the votes come from several others, and
people talk over each other. So the roll call is exactly the passage where
attribution lands on the wrong name, and it's also exactly where being wrong
does the most damage, because "Commissioner So-and-so voted against it" is the
sort of sentence that gets repeated.

The minutes just don't have that problem. The county wrote them, the board
approved them, and they say who voted which way.

So the rule here is that an outcome comes from the record and an argument comes
from the transcript. That isn't only a convention, either.
`tools.speaker_sure()` tags every passage with how well its name is actually
known, and the three levels are quite different: a person stated this name, or
a voice was matched at that meeting, or all we have is the name that voice goes
by across the whole archive. Before that existed, every surface printed all
three identically, which meant an answer could say "Oakley moved" on the
strength of the weakest one. A count the transcript states out loud, something
like "four nays, three ayes", is fine to repeat, because that's a thing that
was said rather than a name this project attached to a voice.

## What it gets wrong

`/about` reports the current numbers, and `bin/audit.py` will tell you which
invariants are failing today. Here's the shape of the problem.

* The audit checks consistency, not correctness. It'll tell you a span is
orphaned, or that an outcome disagrees with the sentence it came from. It can't
tell you a boundary is in the right place, or that a voice belongs to the
person named on it.
* Speaker precision hasn't been measured recently, and it's genuinely hard to
measure, because the human labels and the published rosters are both inputs to
the assignment. Scoring against either one is circular. What gets measured
instead is coverage: how many utterances cluster, how many resolve to a name,
and how many land outside the named person's term of office.
* A large share of utterances resolve to no name at all and show up as a group
label. It's the first thing most people notice.
* Some of the older agendas are image-only scans and the portal can't extract
text from them, so those meetings have no published agenda and lean entirely on
items derived from the transcript.
* Binding transcript spans to agenda items works well on public hearings and
resolutions, less well on the consent agenda, and worst on the regular agenda.
Board reports carry no agenda code at all and never will.

Everything the agent says is bounded by that list, which is why the pages tell
you what they're showing you and where it came from.

## Who is asking

Two tables come out of `/ask`. `answers` holds a run so that a shared link
still resolves years later, and it holds nothing that could tell two people
apart. `asks` is the operator's, and no route serves it: one row per arrival
at the endpoint, including the ones that were turned away, because a ceiling
set too low used to look exactly like nobody asking.

The only identity in it is an HMAC of the caller's address and the local date.
That counts people within a day and cannot be joined across two, so it says
how many asked on Tuesday and can't tell you that Tuesday's visitor came back
on Friday. The key is what keeps it a token rather than a thin disguise: an
IPv4 address is 32 bits, so a bare hash of one is undone by trying all four
billion. With `ASK_ASKER_KEY` unset, the arrivals are still counted and that
column stays empty. Nothing else is kept per visitor anywhere: no cookie, no
session, no access log.

```bash
./emb-venv/bin/python bin/asks.py               # a line a day
./emb-venv/bin/python bin/asks.py --questions   # what was actually asked
```

A count of tokens is a count of addresses, so a household behind one router is
one person and somebody on a phone and a laptop is two. It is a floor with a
wobble, not a headcount, and it is as far as a server that keeps no accounts
can honestly go.

## Redaction

People read their home address into public comment at the podium. That's public
record, but an address that turns up as the top search result is a different
kind of exposure from the same address sitting in a PDF nobody indexes.
`bin/redact.py` proposes removals and a person approves or rejects each one at
`/admin/redactions`.

An approved removal layers rather than deletes. `utterances.text_raw` is what
the recognizer produced and never gets indexed, and `utterances.text` is what
the archive publishes. There's a family of checks in `audit.py` for the rest of
it: that a removal actually reached the transcript, the passages, the search
index and any kept answers, that the text is genuinely unfindable afterwards,
that the raw column still holds what was said, and that the span taken out
wasn't wider than it needed to be.

No real name or address appears anywhere in this repository. The examples in
the docs are made up, and each one was checked for zero occurrences in the
corpus before it got used. The labelled ground-truth files stay out of git.

## Documents

* `COPY.md` has the copy conventions, but only the numbered list at the top is
mine. The rest got reverse-engineered by an assistant from copy an assistant
wrote, and it's been wrong more than once.
* `deploy/postgres-unraid.md` and `deploy/nginx-proxy-manager.md` cover the
database and the edge, both of which are set up by hand.

## Not the official record

Pasco County publishes the authoritative agendas and minutes. This archive
mirrors those documents and adds a transcript layer the county didn't publish
and doesn't vouch for, and every page that makes a claim tells you which of the
two it came from.
