# Pasco County Meeting Archive — project state

Searchable, speaker-attributed archive of Pasco County government meetings with
an LLM agent that answers natural-language questions with playable citations.

Source: <https://www.youtube.com/@PascoCountyGovernment>

---

## Layout

```
pasco-meetings/
  env.local.sh          PASCO_DSN. Gitignored, mode 600. NEVER commit.
  bin/schema.sql        the whole schema, one file
  bin/bm25.sql          Okapi BM25 in SQL (this cluster has no pg_search)
  data/<video_id>/      audio.flac, silences.txt, diarization.json,
                        embeddings.npz, transcript.{json,txt,srt}
  bin/                  pipeline
  web/                  archive.py  the API the rebuilt UI reads
                        tools.py    the five retrieval tools (D9), also /api/tools
                        agent.py    the tool loop behind /api/ask
                        admin.py    the console's data layer and auth
                        limits.py   what a public paid endpoint may spend
                        server.py   routing for all of the above
                        (serves NO html: see gotcha 95)
  ui/                   the rebuilt front-end (Next 16, React 19, TypeScript)
  logs/                 worker logs
  asr-venv/             NeMo + Parakeet          (ASR)
  diar-venv/            pyannote                 (diarization)
  emb-venv/             sentence-transformers    (embeddings, agent, WEB SERVER)

  (no catalog.sqlite, passages.npy or vec_cache.sqlite — deleted 2026-08-13,
   811 MB of pre-Postgres stores. Nothing read them; see gotcha 95.)
```

**Storage is Postgres 18.6 + pgvector 0.8.6**, database `pasco_meetings` on
**10.0.0.6:5432** — the containerised cluster on the Unraid box, not this
workstation. It replaced three separate stores: a SQLite catalog, an FTS5
index, and a 257 MB `passages.npy` that had to be read into RAM whole and
scanned in full on every query. Embeddings now sit in `passages.embedding`
under an HNSW index.

**`PASCO_DSN` in `env.local.sh` is the only thing that says which database is
real**, and `bin/db.py` reads nothing else: with it unset every script raises
`MissingConfig` rather than falling back to a localhost cluster. Keep it that
way. A `pasco_meetings` from before the move is still sitting on this
workstation's local cluster at 127.0.0.1:5432, reachable as
`LOCAL_DSN_PREMIGRATION`, and it is **stale** — as of 2026-08-14 it still
holds the honorific-prefixed `full_name` rows and has no `display_name()`
function. It is not a backup and nothing reads it. A shell that forgets to
source `env.local.sh` fails loudly; one that points at that DSN on purpose
gets a plausible-looking archive that is months behind.

**Three venvs is deliberate** — NeMo and pyannote conflict on torch/lightning
pins. The web server runs from `emb-venv` because the agent needs the embedding
model in-process. All three carry `psycopg[binary]` and `pgvector`.

## Running it

**The public domain is `pasco.watch`** (registered 2026-08-13, at Porkbun; not
yet pointed anywhere). `SITE_URL` is set to `https://pasco.watch` in
**`ui/.env.local`** — `ui/lib/site.ts` reads it and the sitemap, `robots.txt`,
the canonical link and the Open Graph tags all derive from that one function.
Nothing else should hardcode a host.

It lived in `env.local.sh` until 2026-08-14 and **never reached the app**: that
file is a shell script for the Python side, `npm --prefix ui run dev` sources
nothing, and the running dev server's `/proc/<pid>/environ` held no `SITE_*` at
all. The docs called it set for a day while every canonical link and all 1,255
sitemap URLs still said `localhost:3000`. `SITE_CONTACT` and `ARCHIVE_API` moved
with it for the same reason. **Config belongs in a file the consuming process
reads by itself** — Next reads `.env.local` under dev, build and start alike,
and a container's real environment still overrides it. Verify a variable at
`/proc/<pid>/environ`, never by reading the file you think supplies it.

Deliberately NOT `pascocountyfl.net`-shaped: that is the county's own domain,
and this archive says on its own About page that it is not the official record.

Two things change on the day it actually serves from there, both already built
and neither automatic:

- **`/admin` is unreachable from the internet, by design and now in two
  layers** (gotcha 94): `admin.py` refuses any request whose peer is not
  loopback OR which carries a forwarding header, and the edge returns 404 for
  `/admin` and `/api/admin` (`deploy/nginx-proxy-manager.md`, Advanced tab). Reach the console over
  an SSH tunnel - `ssh -N -L 3000:127.0.0.1:3000 <host>`, then
  http://localhost:3000/admin - never by opening the port.
- **Set `ASK_TRUST_PROXY=1` when a proxy goes in front**, or every request
  arrives from 127.0.0.1, the per-address rate limit applies to the proxy, and
  the whole internet shares one bucket (gotcha 89).

**`ASK_DAILY_MAX` is 400, and it is a MONEY number rather than a traffic one.**
Set by the maintainer on 2026-08-14, keeping the value that was already live.
Priced against `deepseek-v4-flash` (2026-08-13: $0.0028/M cache-hit input,
$0.14/M cache-miss, $0.28/M output), one question runs from about **$0.009 to
$0.075** depending on how much the model reasons and how well the conversation
prefix caches:

| per question | $/q | 400 questions costs | holds $10/day at |
|---|---|---|---|
| typical — few turns, 2k output/turn, 80% cached | 0.009 | **$3.60** | 1,085/day |
| heavy reasoning — 8k output/turn | 0.028 | $11.20 | 361/day |
| heavy, and caching goes badly (50%) | 0.041 | $16.40 | 243/day |
| pathological — 16k output/turn, 50% cached | 0.075 | $30.00 | 133/day |

**So 400 is a ~$3.60/day expectation with a $30/day worst case, not a $10
ceiling**, and that is the accepted trade: it buys a launch that does not tell
visitors the archive is out of funding by noon. The row that would have held
$10 at the top of the range is 130. Read the top row as what this actually
costs and the bottom row as the exposure, and if the bill ever arrives near the
bottom row, the fix is `max_tokens` rather than a smaller number — see below.

An earlier version of this file said the value was 130. It never was; the
change was reasoned about and never applied, so the document described an
intention while `env.local.sh` and the running server both said 400. **A number
in prose is not a setting.** Read it out of the environment.

**The spread is 8x wide for one reason: nothing sets `max_tokens`.** Neither
`ask.py` nor `agent.py` bounds output, and output is the expensive side. Cap it
and the pathological row disappears, which is the move that makes 400 cheap at
the top of the range as well as the bottom — the one lever that improves the
worst case without serving fewer people. It is left alone deliberately
- this is a REASONING model, `max_tokens` counts the reasoning as well as the
answer, and a cap set too low buys a question that thinks and then never
answers. Measure a real run's completion tokens before picking a number.

Two smaller things, neither urgent:
- Only ACCEPTED runs count against the daily total (gotcha 89), so a script
  that keeps knocking cannot burn the budget with refusals.
- `ASK_PER_IP=6` per 10 minutes lets ONE address take the whole day's 130 in
  about three and a half hours. Fine while nobody knows the site exists; worth
  a per-address daily cap if that changes.
- From 2026-08-16 DeepSeek prices off-peak at half rate, and peak is
  01:00-04:00 and 06:00-10:00 UTC - which is 9pm-midnight and 2-6am in Florida.
  Almost all local daytime traffic is therefore half price. Upside, not a thing
  to budget against: a ceiling must not depend on when the traffic arrives.

```bash
source ./env.local.sh              # PASCO_DSN — every command below needs it

# ingest fleet (resumable; safe to re-run — workers claim rows transactionally)
bash bin/run.sh
bin/status.py                      # progress

# new meetings later
bin/catalog.py && bash bin/run.sh

# API + the old pages. Source THIS repo's env.local.sh, not another project's -
# the server needs LLM_API_KEY in its environment and started without one for
# weeks when the file was split across two repos (gotchas 87, 88).
source ./env.local.sh
./emb-venv/bin/python web/server.py --port 8765
#   JSON only. The five hand-written pages this used to serve are deleted
#   (gotcha 95); "/" answers 404 saying so.

# the rebuilt UI. Needs the API above running; it proxies /api to :8765.
npm --prefix ui run dev            # http://localhost:3000
#   /              browse — the collection, a year × month time axis, ways in
#   /meeting/:id   the meeting: agenda spine, roster, transcript, player
#   /item/:id      one agenda item: the record, the county's PDF, what was said
#   /case/:id      one application across every meeting that took it up
#   /search        both sources, over the web/tools.py surface (slice 3)
#   /ask           the agent, streaming its real tool calls (slice 4)
#   /ask/:id       one kept answer, so a run can be sent to somebody
#   /admin         curation console: queues, corrections, ops (slice 6)
#   /person/:id    NOT BUILT, and blocked on the roll-call split

# pull new agendas/minutes from the county portal (cheap, run often)
./emb-venv/bin/python bin/civicclerk.py --events --text

# the same fetch, aimed at meetings that have NOT happened yet. Belongs in cron
# at daily; it is installed nowhere. See "Three doors" for why daily is enough.
bash bin/forward.sh

# rebuild the derived layers. THE ORDER IS LOAD-BEARING - see bin/refresh.sh
bash bin/refresh.sh roster speakers names chair affinity segment land index eval
bash bin/refresh.sh index eval          # or just the tail of it
./emb-venv/bin/python bin/parse_minutes.py --write   # outcomes (independent)
./emb-venv/bin/python bin/audit.py                   # 45 invariants, no repair

# fix a wrong speaker over a range of utterances. Outranks every derived layer
# and survives a full rebuild. `show` first; `--detach` says "not this person,
# and I do not know who", which no whole-voice operation could express.
bin/correct.py show 840x-PTQXfc --at 55:05-55:20
bin/correct.py set  840x-PTQXfc --at 55:05-55:20 --name Mariano --note "one sentence, one speaker"
```

Why that order, from `bin/refresh.sh`:

- **roster → speakers** — the matcher draws its per-meeting candidates from it
- **speakers → names** — `speaker_id`'s upsert used to reset `source='llm'` and
  `source='chair'`, so the reverse order silently discarded every name the LLM
  found and one bare `refresh.sh speakers` erased the chair anchor outright
  (measured: 0 rows survived with `source='chair'`). It now updates only the
  rows it left NULL, plus any a human has since labelled, so the order is a
  preference and no longer a trap. Task #31
- **names → chair → affinity → index** — `chair_anchor` fixes which cluster
  carries which commissioner's name; `affinity` then scores voices against
  those names, so running it first teaches it a reference set full of the wrong
  name; and `index` bakes the resolved name into every passage, so running it
  first republishes what the two stages were about to correct
- **segment → land → index** — items must be bound before their subject can be
  baked into what gets embedded

Every stage is idempotent, so this doubles as the resume. `segment.py --reground`
re-verifies stored titles against the transcripts without calling the model.

## Pipeline stages

| stage | script | notes |
|---|---|---|
| catalog | `bin/catalog.py` | scrapes **/streams AND /videos** — see gotchas |
| download | `bin/download_worker.py` | 16 kHz mono FLAC + silence points |
| diarize | `bin/diarize_worker.py` | pyannote; saves speaker centroid embeddings |
| ASR | `bin/asr_worker.py` | Parakeet on VAD windows, audit, repair, index |
| speakers | `bin/speaker_id.py` | clustering + anchors + per-meeting matching |
| correct | `bin/correct.py` | human corrections over an utterance range; outranks everything |
| LLM naming | `bin/name_speakers.py` | second pass with verbatim-quote verification |
| segments | `bin/segment.py` | phase/subject boundaries, one LLM call per meeting-day |
| portal | `bin/civicclerk.py` | mirrors the county's published agendas + minutes |
| agendas | `bin/parse_agenda.py` | agenda text → items, codes, case numbers |
| minutes | `bin/parse_minutes.py` | minutes text → per-item disposition + outcome |
| rosters | `bin/roster.py` | who sat on the board, and in what office, by date |
| domain | `bin/land_agenda.py` | meetings, agenda_items, cases; binds transcript spans |
| chair | `bin/chair_anchor.py` | anchors clusters to commissioners from the published chair roster |
| affinity | `bin/affinity.py` | does a voice really sound like the person its cluster names? |
| passages | `bin/index_passages.py` | retrieval units + embeddings; bakes the resolved speaker |
| retrieval | `bin/retrieve.py` | SQL BM25 + pgvector HNSW + thread-key fusion |
| agent | `bin/ask.py` | plan → retrieve → multi-lens read → answer |
| check | `bin/eval_agent.py` | pass/fail: did the answer reach the evidence it needed? |
| audit | `bin/audit.py` | 35 data invariants, in bulk, repairs nothing |

**Not ported to Postgres:** `eval_anchors.py`, `eval_chunking.py`,
`eval_clustering.py`, `eval_embed.py`. These are one-off experiments that have
already been run and whose conclusions are recorded under *Measured facts*;
they still speak SQLite and will not run as-is. Port one only if you intend to
re-open the question it settled.

## The domain model (added after the schema modelled artifacts, not events)

The original schema stored a video file, a span of a video, and a name string.
Everything anyone actually asks about is a meeting, an agenda item, a person or
a case, so those are now rows:

```
meetings (date, body)  1─n  videos (session_seq)
    └─n agenda_items (seq, code, section, department, case_id, recommendation,
                      disposition, outcome, source)  1─n  item_spans → passages
cases (PREFIX-YY-SEQ)  ─── the thing that recurs, across bodies and years
people ─n board_terms / meeting_roster ─── who sat, when, and who chaired
portal_events / portal_files ─── the raw CivicClerk payloads, kept untouched
```

**The county publishes all of this.** CivicClerk exposes an unauthenticated
OData v4 API at `pascocofl.api.civicclerk.com/v1`; `plainText=true` returns the
server's own PDF text extraction, so nothing here parses PDFs. That source is
the spine, and the transcript is the discussion layer bound to it - which is
the right way round: the agenda is a published fact and the voice matching
bound to it is an inference.

`agenda_items.source` is `'agenda'` (published fact) or `'transcript'` (the
procedural stretches - call to order, recess, adjourn - that no agenda lists).
Never let the UI blur the two.

**The old `segments` table is now write-only.** `bin/segment.py` fills it and
`land_agenda.py` reads it to build `item_spans`; nothing else should. Retire it
once binding is trusted, or it will drift.

## Measured facts (don't re-derive)

- **Embedding model**: `microsoft/harrier-oss-v1-0.6b`. Beat Qwen3-0.6B on this
  corpus; Qwen3-**4B** *lost* to the 0.6B. Bigger is not better here.
- **Hybrid > dense alone.** BM25 wins on proper nouns and case numbers; dense
  wins on paraphrase. RRF fusion of both, always.
- **Chunking**: speaker-bounded + 35-word floor won (MRR 0.762 vs 0.655
  speaker-blind). Plus cross-speaker "exchange" passages — see gotchas.
- **Speaker ID**: held-out recall 0.60, precision **0.78** — measured BEFORE
  the roster work, the Postgres migration and ~200 meetings of growth, and
  never re-measured. Treat it as historical. Good enough for browsing, NOT for
  vote attribution. See Honest limits for what is actually measured now.
- **Blind voice clustering degrades with scale** — 4 → 14.8 clusters per
  commissioner from 20 → 119 meetings. No threshold fixes it (purity collapses
  before fragmentation resolves). Anchor-based assignment replaced it.
- **Prefix caching**: ~70–76% of prompt tokens cached, persists across runs.
  Cache hits cost ~1/10–1/20. Put stable content FIRST, varying instruction
  LAST. Instrumented in `ask.USAGE` / `ask.usage_report()`.
- **BM25 is hand-rolled in SQL** (`bin/bm25.sql`) because this cluster has no
  `pg_search`, and `ts_rank_cd` is *not* BM25 — no real IDF, no length
  saturation, which is precisely what earns its keep on case numbers and
  proper nouns. Postings, doc lengths and DFs are materialised; k1=1.2, b=0.75
  match FTS5's defaults so the ranking measurements still apply. ~26 ms per
  query. (Postings and term counts scale with the corpus - 2.44M over 32.5k
  terms at 65k passages - so treat those as a size reference, not a constant.)
- **Both sides of BM25 go through `to_tsvector('english', …)`** — the analyzer
  that builds the postings is the same one that parses the query. Nothing
  tokenises by hand. An index and a query that disagree about what a token is
  fail silently and look like bad relevance.
- **HNSW build over 65k × 1024 takes 8 seconds** (scales with corpus) with
  `maintenance_work_mem`
  at 2 GB. `hnsw.ef_search` is 1000 (pgvector 0.8's ceiling) for 98.6%
  recall@40 at 19 ms; see the table below for the tradeoff.
- **The published agenda is the authoritative spine.** CivicClerk exposes an
  unauthenticated OData v4 API; `bin/civicclerk.py` mirrors it into
  `portal_events` / `portal_files` (1,270 meetings 2015-2027, 1,962 agenda and
  minutes files, 30 MB of text). `plainText=true` returns the server's own PDF
  text extraction, so nothing here parses PDFs.
- **Agenda parsing is a parser, not an LLM pass** (`bin/parse_agenda.py`). The
  document is machine-generated and regular; 96% of 22,499 items across
  614 BCC/Planning agendas yield a case id, and 100% of substantive agendas
  (>8k chars) yield at least one. Judgement here would hide layout drift
  rather than surface it.
- **Two agenda eras, one identifier.** Before ~2020 the case number is labelled
  `Memorandum CO17-194`; after, `File Number CO26-0183`. Same shape either way
  (PREFIX + 2-digit year + sequence), so `parse_agenda.CASE_ID` matches on the
  shape and treats the label loosely - PDF extraction mangles it into
  `Me morandum`, `Memorand`, `Mem`.
- **`deepseek-v4-flash` accepts ≥194k prompt tokens** — measured with real
  transcript text, not estimated. The largest meeting-day in the archive
  renders to ~43k. **Nothing here needs chunking**; the earlier 350-line
  sliding window was pure overhead and cost segmentation quality at the seams.
- **What is indexed ≠ what is displayed.** `passages.text` is verbatim;
  `passages.search_text` is what BM25, the embeddings and `threads.global_keys`
  see, and it carries the agenda item's subject. That split is the whole fix
  for votes.

## Gotchas that cost real time

Numbered so code and comments can cite them, which several do — grep
`gotcha \d+` across `bin/`, `web/` and `ui/` before renumbering anything.
**There is no 47**; the number was skipped, not lost.

1. **YouTube splits uploads across `/videos` and `/streams` with ZERO overlap.**
   All meetings are live-streamed, so scraping only `/videos` returns none of
   them. Cost: an entire wrong catalog.
2. **Cluster ids are unstable** — only ~2% survive a re-clustering run. Human
   labels are therefore anchored to `(video_id, local_label)`, never to a
   cluster id, and re-propagated. Never key anything durable on a cluster id.
3. **ASR spells numbers as words ~64% of the time** ("R fifty seven").
   `threads.normalize_numbers()` must run before any ID matching.
4. **Short agenda ids (C-2, PC-4, R-57) are POSITIONAL, not identity** — every
   agenda has a C-1, and "C 2" is also a commercial zoning district. Only long
   application numbers (PDE-260022) join across meetings.
5. **The chair rotates annually.** Any "who chairs" logic must be per-meeting.
6. **NeMo segfaults on Python 3.11.0rc1**; use managed 3.12. Its CUDA-graph TDT
   decoder throws illegal memory access — `use_cuda_graph_decoder=False`.
7. **Parakeet TDT silently deletes speech on long inputs.** Never feed it more
   than ~24s windows. `find_gaps()` + `repair()` catch what slips through.
8. **`serve_vllm.sh` in ../active-reading kills all GPU processes** on launch.
   It will kill the ingest fleet.
9. **Don't pipe python through `grep` and trust `$?`** — it reports grep's status.
   Cost: a "successful" run that had actually crashed.
10. **Passage ids are reassigned on every index rebuild** (they are the row
    order of `video_id, idx`, and new meetings land mid-alphabet). Never
    hard-code one. `eval_agent.py` addresses its targets by (video, second).
11. **A grounded title must be edited in place, not rebuilt from its surviving
    tokens.** Rebuilding turns "R-58" into "58" and drops "PDE" from
    "PDE 260033" — BM25 then matches neither, and `threads.global_keys()` no
    longer sees a case number. See `segment.ground()`.
12. **`end` is a reserved word in Postgres.** It is quoted (`u."end"`) rather
    than renamed, so every Python dict key that reads a row stays as it was.
    Forget the quotes and you get a syntax error, which is the good failure.
13. **`db.Row` maps column NAMES, where `sqlite3.Row` iterated VALUES.** So
    `dict(rows)` silently builds `{'name': 'count'}` instead of `{name: count}`.
    Build such maps positionally: `{r[0]: r[1] for r in ...}`.
14. **Postgres returns real types where SQLite returned strings.** `timestamptz`
    arrives as `datetime` and `numeric` as `Decimal`, neither of which
    `json.dumps` will serialise — `/api/meeting/<id>` 500'd on `videos.updated_at`
    alone. Handled once in `server.jsonable`, not per call site.
15. **A passage cannot be expanded to its agenda item unless the query selects
    `segment_id`.** (Now `agenda_item_id`.) `retrieve.search()` omitted it, so
    every passage carried a NULL and `decisions_in_play()` silently expanded nothing
    while appearing to work. Anything keyed on a column must select it.
16. **Postgres rejects a GROUP BY that SQLite waved through.** Selecting a
    joined table's column while grouping by another table's primary key is not
    functionally dependent. In `api.roster()` the GROUP BY was a no-op anyway,
    because the join is 1:1 on the primary key.
17. **Roughly half of all meeting-days are two recordings** on one continuous
    agenda, and the afternoon opens mid-item with no announcement of what the
    item is. Segment the DAY, not the video. Sessions are only joined when
    their order is certain — a workshop or budget hearing sharing the date, or
    two recordings both labelled "Morning Session", are segmented separately
    rather than guessed at.
18. **A published agenda item is not closed until the next item code appears**,
    so the department heading sitting between two items gets swallowed and
    every item silently inherits the previous item's department. Close the
    item as soon as its labelled fields have been seen.
19. **172 agenda PDFs extract to zero characters** - image-only scans the
    server cannot text-extract. They are a real coverage gap, not a parse bug,
    and need OCR if that history matters.
20. **A transient failure must never write `videos.error`.** `claim()` filters
    `error IS NULL`, so one bad yt-dlp extraction silently retired 7 meetings
    from the queue forever - all 7 downloaded fine on retry. Workers now call
    `db.fail()`, which bumps `videos.attempts` and only writes `error` when the
    message says the video is genuinely gone ("Video unavailable", "Private
    video", ...) or after 5 attempts.
21. **A GPU worker missing its input has an upstream problem, not a retryable
    one.** `db.rewind()` clears the upstream flag so the stage that produces
    the file redoes it. Note `silences.txt` is written by the DOWNLOAD stage,
    not diarization - routing it to the wrong worker retries forever.
22. **A roster is a per-meeting fact, not a constant.** `speaker_id.py` held a
    hardcoded list of the five CURRENT commissioners and matched it against
    every meeting in the archive. Measured against the rosters printed on the
    published agendas: 23% of commissioner voice assignments were to someone
    not seated that day (14,148 utterances credited to Yeager before she took
    office), and three commissioners who DID sit during the corpus - Moore,
    Fitzpatrick, Bradford - were unnameable, so their voices were handed to
    whichever current commissioner was nearest. `bin/roster.py` extracts
    `people` / `board_terms` / `meeting_roster` from the agendas; the matcher
    now draws candidates per meeting. Coverage looked fine the whole time -
    only precision was wrong, and nothing in the pipeline could see it.
23. **Never background work with `nohup ... &` inside a foreground tool call.**
    It dies with the parent when the call is interrupted. It had already
    committed that time, which was luck, not design.
24. **Two vocabularies in one column is a filter that silently matches half its
    rows.** `agenda_items.phase` held the agenda's prose section names
    ("public hearings") for published items and the segmenter's vocabulary
    ("public_hearing") for transcript ones, so `phase='public_comment'` would
    have found 104 rows and missed 9,762. Normalised on write in
    `land_agenda.canonical_phase()`; `bin/audit.py` asserts it.
25. **Spot-checking is not a method.** Every data bug in this project was
    invisible in the summary statistics and obvious against a stated invariant.
    `bin/audit.py` states 30 of them, counts violations in bulk, and repairs
    nothing - a repair that runs before anyone looks is how a data bug becomes
    permanent. Run it after every rebuild.
26. **`end` is a reserved word, and it only breaks on WRITE.** `u.end` parses
    fine when qualified, so every read worked; the bare `end` in `segment.py`'s
    INSERT was a syntax error. Segmentation therefore stored NOTHING for the
    whole Postgres era while logging a success line per meeting-day. Worse, the
    raise happened inside `with ThreadPoolExecutor`, so Python waited for all
    133 outstanding LLM calls to drain before surfacing it: an hour of spend, a
    log full of successes, an empty table. `segment.preflight()` now exercises
    the INSERT against a rolled-back transaction before the first call.
    `end` is the ONLY reserved-word column in this schema (item_spans,
    passages, segments, utterances) - the query that proves it is in gotcha 27.
27. **A green audit can mean "nothing there", not "nothing wrong".** Two checks
    filter on `passages.agenda_item_id IS NOT NULL`; before `index` ran, that
    was zero rows and both reported `ok`. Every check now reports the size of
    the set it examined and says `EMPTY` when that is zero.
28. **A global cluster->name map cannot respect a per-meeting roster.**
    `speaker_id` correctly refused to name commissioners at meetings they did
    not sit in - and the old `cluster_name` view, keyed on cluster alone, handed
    the name back at display time. 528 voice clusters appear under both bodies.
    Naming is now `voice_name`, keyed `(video_id, cluster)`, and a board
    member's name must be supported for THAT meeting by a roster or a
    same-body term.
29. **A crash strands work queue claims forever.** `db.claim()` only considers
    `claimed_by IS NULL`, so a worker killed mid-item leaves a video that is
    not errored, not pending, and will never be picked up again. Workers call
    `db.reclaim()` at startup; `bin/audit.py` checks for claims older than six
    hours. Related: `bin/run.sh` uses `setsid`, because a fleet started from an
    agent or editor shell otherwise dies with that shell.
30. **Never edit a bash script that is already running.** bash reads a script
    incrementally by byte offset, so inserting lines shifts everything after
    the current position: the step gets skipped, or a fragment of one line runs
    joined to another. A `land_agenda --redo` added to a running
    `finish_chain.sh` silently never executed, `item_spans` kept its stale rows
    alongside the new ones (the ON CONFLICT key is `(video_id, start_idx)`, and
    the new boundaries were off by one, so nothing collided), and the whole
    index was built on 1,006 overlapping spans. Write a NEW script instead.
31. **`passages.agenda_item_id` had no foreign key** — added by a bare
    `ALTER TABLE ADD COLUMN`, unlike `item_spans.agenda_item_id`. Rebuilding
    spans deletes the transcript-derived items, and every passage pointing at
    one was left dangling. Now `ON DELETE SET NULL`: losing a binding must not
    delete the only record of what was said.
32. **A timeout tuned to the median is a timeout that fires half the time.**
    `ask.chat()` had a 180s socket timeout; the median whole-day segmentation
    call measures 158s. Half of all days timed out, silently retried three
    times, and failed after nine minutes having discarded three paid-for
    completions. Now 600s by default, with backoff, and any call over 240s
    logs itself.

33. **One threshold cannot both report a match and license it as evidence.**
    `anchors.refine()` grows anchors EM-style: each round's assignments become
    the next round's reference voiceprints. With a single `SIM_FLOOR`, a few
    borderline matches entered the reference set, strangers then matched *those
    strangers*, and it compounded — the rounds GREW, 3507 → 4117 → 5035.
    "Barbara Wilhite" ended with 664 voices across 316 clusters, of which only
    48 (7%) resembled her 43 confirmed voiceprints; the median assigned voice
    scored 0.382 against her, squarely a different person. Now `SIM_FLOOR`
    (0.70) decides what may be REPORTED and `TRUST_FLOOR` (0.85) decides what
    may become EVIDENCE. The loop converges: [2886, 2905, 2905]. Ground truth
    from the 59 human labels shows why the floor was never the issue — same
    person mean 0.898 (p10 0.796), different person mean 0.104, **max 0.342**.
    Nothing lives between 0.35 and 0.79.
34. **A name carried by mention rather than by voice.** The handoff signal
    ("...next we'll hear from Justin Grant, Director of Public Infrastructure")
    names whoever speaks NEXT. Right at a podium, wrong for anyone whose name is
    simply said aloud often. Applied per-meeting with nothing checking it, a
    frequently-named staffer collects a different voice in every meeting they
    are mentioned in. A name spanning meetings must now be corroborated by the
    voice cluster; a voice heard in only ONE meeting keeps the text signal,
    which is the case it was built for. The diagnostic is clusters-per-meeting:
    a real recurring speaker runs 0.06–0.13, and `speaker.voice_coheres` fails
    above 0.40.

35. **The portal publishes a forward calendar, and it is not the archive.**
    35 of the 1,249 meetings have not happened yet - announced months ahead,
    with no agenda, no minutes and no recording *because there is nothing to
    have*. Sorted newest-first they filled the entire first screen of Browse,
    every row reading as a hole in the record. `archive.meetings()` therefore
    defaults to `when='past'`. A missing agenda for a meeting held in 2019 and
    a missing agenda for one scheduled in 2027 are not the same fact and must
    not render the same way.
36. **A theme declared twice has already drifted.** `tokens.css` first wrote
    the dark palette out twice - an explicit `[data-theme="dark"]` rule and a
    `prefers-color-scheme` block - and a single find-and-replace updated one
    and not the other, because the copies were indented differently. Both are
    now one declaration each via `light-dark(light, dark)`, with `color-scheme`
    doing the switching. Note `light-dark()` is a **colour** function: wrapping
    a whole `box-shadow` value in it makes the declaration invalid and the
    shadow silently vanishes, so only the colour varies by theme.
37. **Grey is where contrast quietly fails, and it fails on the text that
    carries the caveats.** The first token pass had `--ink-3` and `--ink-4` at
    2.7-4.4:1 - below WCAG AA - and they were exactly what "no recording",
    "Voice B", "no disposition recorded" and every timestamp were set in. The
    de-emphasised text in this UI is where the honesty lives, so it is the last
    thing that may be unreadable. Both now clear 4.5:1 against every background
    in both themes, and meaning-bearing borders clear 3:1. Measure it in the
    browser against the real computed values; do not eyeball it.
38. **`useVirtualizer()` returns a new object every render.** Putting it in a
    `useEffect` dependency array re-runs that effect on every one of the four
    playhead updates a second, which for the transcript meant re-issuing a
    smooth scroll continuously. Hold it in a ref and depend on the values that
    actually changed. Relatedly, the React Compiler declines to memoize any
    component using it and says so as a lint warning - that one is expected.

39. **An explicit "take me there" is not the same as passive following, and
    collapsing them breaks the page's central interaction.** The transcript
    auto-scrolls with the playhead, and a reader who scrolls by hand turns that
    off. But the same flag also gated *clicks*: once anyone had scrolled the
    transcript, clicking an agenda item moved the recording and left the
    transcript exactly where it was. A click is not a preference about
    auto-scrolling. `MeetingView` now issues a **cue** - `{videoId, seconds, n}`
    where `n` is a counter, so clicking the same item twice scrolls twice - and
    the transcript obeys a cue unconditionally, re-arming following. Passive
    drift stays gated. The two must not share a flag.
40. **TanStack Virtual re-renders with `flushSync` on every scroll event.**
    React 19 refuses to flush mid-render and drops the update, so scrolling a
    2,000-line transcript produced dozens of "flushSync was called from inside
    a lifecycle method" errors and lost scroll updates with them. `useFlushSync:
    false` batches normally and there is no visible difference at this row
    count; `useAnimationFrameWithResizeObserver: true` moves row measurement out
    of the commit phase for the same reason. Note the console keeps messages
    across navigations - `console.clear()` before testing or old errors read as
    current ones, and HMR mid-edit produces errors that a fresh load does not.
41. **A cached promise REJECTION is permanent.** `loadApi()` memoised the
    YouTube IFrame API load; one dropped connection - which happened - left the
    player dead for the rest of the session, with every retry resolving
    instantly to the same stale failure. The failed attempt is now forgotten,
    which is what makes the "try again" button mean anything. Any memoised
    async singleton needs this.

42. **The display path read the name from the wrong place, and it moved two
    different women under one name.** `voice_name` resolves a name from
    `cluster_pick` - the archive-wide majority name for a voice cluster - and
    applies it to every meeting that cluster appears in. `speaker_identity`
    already held a per-(video, local_label) name for the same voice, and the
    two disagreed on **24,445 utterances, 10.7% of every named line, across 215
    recordings**. Cluster 192 was labelled Starkey in 36 meetings and Yeager in
    10; every one displayed as Starkey. Cluster 89 carries Moore and
    Fitzpatrick; cluster 44 carries Starkey and Bradford. 35 of 1,251 named
    clusters carry more than one name, and they are the commissioners.
    Precedence is now override → human label → **per-meeting assignment** →
    cluster majority, in `utterance_speaker`. Checked against the published
    roster, the change never makes roster support worse (0 cases) and sometimes
    fixes it (180); for the 20,044 where both people were in the room the
    roster cannot adjudicate, which is the argument for the override, not
    against the change.
43. **Keying display on `(video_id, cluster)` is lossy.** 30 (video, cluster)
    pairs hold two diarization labels, so the cluster is not the voice. The
    voice is `(video_id, local_label)`, which is what `speaker_label` and
    `speaker_identity` key on, and what the resolver now keys on.
44. **`passages.speaker` is a denormalised name string**, and it fed search,
    the speaker filter and every quote the agent prints. It was baked from the
    same broken cluster path: 23,968 of 72,041 single-speaker passages carried
    a wrong or artificial name, 17,379 of them literally `Group N`. Worse, an
    exchange passage inlines its speaker labels INTO the text that gets
    embedded and indexed, so 19,457 vectors and their BM25 postings had a
    diarization id sitting inside them. Rebuilt: the indexer now resolves
    through `utterance_speaker`, bounds passages by `local_label` rather than
    cluster, writes NULL instead of a stand-in, and letters unnamed turns
    per-passage (`Unidentified A`) so nothing durable is implied.
    `passages.speaker_agrees` in the audit catches any drift. See gotcha 46 for
    why the name stays in the indexed text rather than being resolved at read
    time, and how a correction now reaches the index in about a second.
45. **Cluster inheritance is an assumption, and it is measurably wrong a
    quarter of the time.** Most speakers are named only because their voice
    landed in a cluster somebody else in it was named - which assumes cluster
    membership means same person. On this corpus it does not: per-recording
    centroids are dominated by mic, seat and room, which is why anchors
    replaced blind clustering in the first place. `bin/affinity.py` scores each
    inheritable voice against the voices actually carrying the name, and the
    result is as bimodal as the human-labelled ground truth:

        below 0.20   444        0.35-0.70    11   <- the whole ambiguous zone
        0.20-0.35     66        above 0.70 5,391

    **521 of 5,912 fail (8.8%), and 510 of those sit below 0.35**, where no
    same-person pair has ever been observed. The transcripts corroborate it out
    loud: a voice labelled "Development Review Director" at similarity -0.069
    opens with "Christina Cordon, Assistant Director of Parks, Recreation".
    `utterance_speaker` now withholds a cluster name where the voice has been
    measured not to be that person - 5,594 utterances, coverage 76.5% → 74.6%.
    Note the gate refuses only on **evidence against**: "could not be measured"
    is a different state from "measured and wrong", and only the second
    justifies discarding an attribution.

46. **The speaker belongs IN the embedding, which makes a wrong name an index
    defect rather than a display one.** The tempting fix for "a correction does
    not reach search until a re-index" is to strip names out of the indexed
    text and resolve them at read time. That is wrong. "What did Starkey say
    about the trail" is a real question, and a passage stripped of its speaker
    cannot answer it; worse, a stale name in the vector pulls the passage
    toward the wrong person and puts the wrong surname in the BM25 postings, so
    it is retrieved for someone who never spoke and missed for the person who
    did. Correct display repairs none of that - the reader never sees the
    result, because it never ranked. For a retrieval archive, ranking IS the
    product.

    So the answer is not to remove the name but to make correctness cheap to
    maintain. `passages.start_idx/end_idx` map an utterance range to exactly
    the passages it touches; `index_passages.refresh_video()` re-renders only
    those, re-embeds only the ones whose text actually changed (vec_cache
    serves the rest), and `bm25_refresh(ids)` re-posts those documents while
    adjusting `term_df` and `avgdl` instead of rebuilding 5.6M rows.
    `bin/correct.py` calls it automatically on `set` and on `undo`. Measured:
    **a correction lands in the transcript, the vector and the postings in
    about 8 seconds**, most of which is loading the embedding model.

    `refresh_video` asserts that passage boundaries did NOT move - they are set
    by `local_label` and word counts, which no name correction touches - and
    refuses rather than writing mismatched rows over good ones. That guard
    earned itself immediately: changing how exchange labels are rendered
    shortened some passages under the 12-word floor, 888 became 885, and it
    stopped instead of corrupting the recording.
48. **A floor applied to the wrong string threw away the roll call.** There are
    two word floors in `index_passages.py` and they do different jobs. `FLOOR`
    (35) routes a short single-speaker run into an *exchange* passage - that is
    the vote fix, and it loses nothing. The other was a hard DROP of anything
    under 12 words, applied inside `emit()` to the RAW text, before the agenda
    subject was attached. It discarded **29,261 chunks, 1,672 of them carrying
    motion or vote language**, verbatim:

        "Starkey: District One, Commissioner Oakley. Moore: Aye."

    Exchange passages exist precisely so a vote survives being five two-word
    turns - and then a second floor threw the result away one function later,
    re-creating the failure the first mechanism was built to fix. Applied to
    `search_text` instead (`indexable()`, `MIN_INDEXED`), **21,752 come back,
    including 1,373 of the 1,672 votes**. "Aye." is not retrievable; "R-58
    school zone speed cameras. Mariano: Aye." is, and that is the string the
    index actually holds. The 7,509 that still fall short have no subject and
    no substance. **Test a floor against the string you are indexing, not the
    one you are displaying** - the file's own header says the two differ.
49. **Label the speaker only when the speaker CHANGES.** Diarization splits one
    person's sentence across several utterances, so labelling every turn in an
    exchange passage produced "Mariano: We have no one online Mariano: for
    Mariano: this item." Because that text is what gets embedded, the surname
    appeared four times in one short passage and dragged the vector toward the
    name and away from what was said.
50. **`until ! pgrep -f X; do sleep; done` waits for ITSELF.** Three shells
    were left spinning forever by this. The `[i]ndex_passages.py` bracket trick
    stops the *pgrep* process matching its own pattern - but it does nothing
    about the **parent shell**, whose command line contains the unbracketed
    name because the same command both launched the job and then waited for it:

        setsid python bin/index_passages.py & \
          until ! pgrep -f "[i]ndex_passages.py"; do sleep 10; done

    That shell's own cmdline holds `bin/index_passages.py`, so pgrep matches it
    and the loop never exits. A second shell waiting on the same pattern then
    waits on the first. They sleep quietly and look exactly like work in
    progress. Same family as the `pkill -f` self-match (exit 144), which is
    also what killing them reports.

    Wait on the PID you started (`wait $!`), or on a completion marker in the
    log, not on a pattern that your own command line contains.

51. **A `tail -f` monitor never ends, even after the thing it watched has.**
    Four of them sat "running" for 40+ minutes after their jobs finished,
    looking like live work. `tail -f | grep` has no exit condition, so a watch
    armed for progress updates stays armed until its timeout. Use a monitor for
    a stream of events you want reported as they happen, and stop it explicitly
    when the job ends; for a single "tell me when this is done", wait on a
    condition that can become false instead. Distinct from gotcha 50 - that one
    is a loop that cannot exit, this one is a watch nobody disarmed.

52. **The county serves every document as an attachment, so it cannot be
    framed.** R5.3.5 wants the published PDF inline, and the obvious
    `<iframe src={civicclerk url}>` renders nothing at all - not an error, a
    blank box. CivicClerk sends

        content-type: application/pdf
        content-disposition: attachment; filename=851.pdf

    and `attachment` tells the browser to download rather than display, which
    for a cross-origin frame means it silently shows an empty frame.
    `server._file()` proxies the identical bytes with `inline`, changing that
    one header and nothing else, and the county's direct link is still offered
    beside it. Verified at the wire: 200, `application/pdf`, 1.28 MB, magic
    `%PDF-1.7`, `Content-Disposition: inline`.

    Note the embedded preview browser will not composite a PDF even when the
    response is correct, so "the frame is grey" there proves nothing either
    way - check it in a real browser. The component carries a visible fallback
    link for the browsers that genuinely cannot.

53. **The dev console is a cumulative log, not the current state — and
    `console.clear()` does not clear it.** Gotcha 40 says the console keeps
    messages across navigations. It is worse than that: the buffer survived
    `console.clear()`, a hard navigation AND a dev-server restart, so a page
    with zero errors kept reporting forty of them, all HMR debris from edits
    made minutes earlier (`Fragment is not defined` for an import that was
    already there). Every one of them was a ghost.

    **Open a fresh tab.** That is the only clean read, and it took one to show
    the page was actually silent. Judging a page by a dirty console is how you
    spend an hour fixing bugs that do not exist.

54. **Measuring colour right after flipping the theme samples mid-transition.**
    Checking contrast in both themes means stamping `data-theme` and reading
    computed styles - and `.link` carries `transition: color 120ms`, so
    measuring two animation frames later (~32 ms) reads a blend of the two
    themes. That reported the site navigation at **1.91:1** in dark, a severe
    failure that did not exist; waiting 500 ms reported the true value and zero
    failures. Two other traps in the same check: `getComputedStyle` returns
    `color(srgb 0.98 0.97 0.96 / 0.88)` for a translucent token, and reading
    those 0-1 components as 0-255 makes near-white look black; and a
    translucent background must be composited against what is behind it, not
    treated as opaque. All three produce false failures, which are more
    expensive than none, because they send you editing working code.

55. **A public hearing's minutes hold several motions, and only one of them
    disposes of the item.** The parser kept whichever came FIRST:

        P83  Zoning Amendment (Regular) - Evans County Line 80 MPUD ...
             Recommendation    Approval with Conditions

        Approved to receive and file documents submitted by Mr. William
        Vermillion.                                              <- kept
        Approved Staff's recommendation.                         <- discarded

    So 212 items read "Approved" where what was approved was somebody's
    paperwork, or a motion to hear the item at all, or the adjournment of the
    whole meeting. **Two were outright denials shown as approvals** - "Denied
    Staff's recommendation of approval with Chairman Starkey and Commissioner
    Weightman voting nay" replaced by an exhibits motion. It lands almost
    entirely on public hearings, which are the contested items, and it produces
    a confidently WRONG outcome rather than a missing one.

    `parse_minutes.choose()` now drops subsidiary motions - evidence accepted,
    a motion to take the item up, adjournment - and takes the LAST of what
    remains, because the minutes are chronological. **Except that a refusal is
    never overridden by what follows it**: taking the last unconditionally lost
    a denial or a withdrawal on 4 of the 6 items where it mattered, since
    "Denied Staff's recommendation" followed by "Approved to authorize the
    Chairman to sign a letter expressing their opposition" is a consequential
    action, not a reversal. When every motion is subsidiary the item is left
    with NO disposition, which is honest and already a designed state.

    The whole shape was found by measuring, not by reading the code: 556 items
    carry more than one motion, and printing the first-vs-last disagreements is
    what showed that no positional rule alone is right.

56. **Three parser bugs were hiding behind that one, all in the same swallow
    rule.** A disposition line stays open until the sentence ends, so that a
    wrapped exception list is not cut in half. But:

        Continued to June 20, 2017 in New Port Richey.  (3:29:46)

    ends with a video offset, not a period, so "has this sentence finished"
    said no and the buffer ate the next eight lines - the following item's
    heading, its File Number, its Recommendation. `cur` then never advanced and
    **every disposition after it was filed under the wrong item**. 86 stored
    dispositions contained a later item's heading; now 0.

    Fixing it needs three things and the middle one is a trap: strip the
    trailing offset before the test; refuse to swallow a line that begins the
    next item; and require a CAPITAL on a disposition's first word, because 132
    of 8,721 matches are wrapped fragments ("withdrawn.", "pulled for
    discussion. Agenda Items C12, C13, and C34 were withdrawn."). The trap is
    that "refuse to swallow a new item" must use a HEADING test, not `ITEM` -
    `C69 which were pulled` matches `ITEM` and is the middle of an exception
    list, and using `ITEM` re-created the exact bug the swallow rule exists to
    prevent. A following lowercase word is the discriminator: 29,996 real
    headings, 95 continuations.

57. **A derivation that only UPDATEs cannot un-say anything.** `parse_minutes`
    wrote with UPDATE and never cleared, so any item it no longer resolves kept
    whatever an older run decided - which is how a disposition the parser had
    just learned to reject survived the fix that rejected it. It now clears the
    meeting's three derived columns before writing.

    Two things that made the first attempt wrong. Guarding the clear on "did we
    get any hits" looks safer and is the bug: a meeting whose only motions are
    subsidiary correctly resolves to NOTHING, and the guard then preserved
    precisely the rows that decision had rejected. And iterating per FILE rather
    than per MEETING meant a meeting-day with two minutes documents had the
    second clear what the first wrote, so the result depended on the order of a
    query nobody thought of as ordering anything.

58. **`\b` is a BACKSPACE in a Postgres regex, not a word boundary.** A probe
    for `\bto\s+adjourn\b` returned 0 rows and read as "this does not happen";
    with `\y`, the real boundary, it returned 20 - all of them zoning
    amendments and ordinances whose recorded outcome was the motion to adjourn
    the meeting. A pattern that silently matches nothing is worse than no check
    at all, because it answers the question with a confident no.

    `parse_minutes.SUBSIDIARY_SQL` avoids boundaries entirely. It is kept
    beside the Python `SUBSIDIARY` so one file owns both, and they are asserted
    equal against every stored disposition rather than assumed equal - they
    disagreed twice while being written, once because the minutes spell it
    "receive and filed".

    The mirror image bites too: reusing one of those Postgres patterns from
    Python is a `bad escape \y` if it has boundaries - loud, so it gets fixed -
    but a Python `\b` pattern moved INTO SQL matches nothing at all, silently.
    `archive._tighten()` translates rather than duplicating.

59. **A CSS-Module `outline` deletes the global focus ring.** `TimeAxis.cell`
    set `outline: 2px solid transparent` so hover could animate the colour. The
    global `:focus-visible` in `globals.css` is a pseudo-class, specificity
    (0,1,0), exactly equal to a class - so the module wins on source order and
    the focused element computes `outline-color: rgba(0,0,0,0)`. 145
    keyboard-reachable calendar cells had no focus indicator at all (R8.2, WCAG
    2.4.7) and nothing in the build, the linter or the type checker had an
    opinion about it.

    Any component that sets `outline` unconditionally must restore its own
    `:focus-visible`. Checked the other two in the tree: `Timeline` sets it
    inside `:hover` only and `RecordView.activeCard` is not focusable, so
    neither is affected.

    Testing this is fiddly: `el.focus()` from a script does NOT satisfy
    `:focus-visible`, and neither does a synthetic `KeyboardEvent`. The check
    that works is a real click on a neighbour followed by a real Tab, then
    reading `getComputedStyle(document.activeElement).outlineColor`.

60. **Escaping `-` in a JavaScript regex is a syntax error under `/u`.** The
    search highlighter escaped it with the rest of the metacharacters. `-` is
    only special inside a character class, and `\-` outside one is an *invalid
    escape* that the Unicode flag rejects outright - so `new RegExp` threw on
    the first query containing a hyphen, which is to say on `R-58`, the exact
    identifier the page's placeholder advertises. It typechecked, it linted, it
    built, and it 500'd the route.

61. **A proxy gzipped the event stream, and gzip buffers.** `/api/ask` streams
    the agent's tool calls over SSE. `curl -N` saw every event the instant it
    was written; the browser saw nothing for ninety seconds and then reported
    the connection dropped. The Next dev server proxies `/api/*`, and it
    compresses any response whose client sent `Accept-Encoding` - which every
    browser does and curl does not. A gzip stream emits nothing until it has
    enough input for a block, so 50-byte events sat in the compressor.

    `Cache-Control: no-cache, no-transform` fixes it: `no-transform` is the
    standard instruction to an intermediary to leave a body alone.
    `X-Accel-Buffering: no` is nginx's version of the same thing and is sent
    for whatever ends up in front of this in production.

    Two traps around it. The diagnosis is only possible with the RIGHT curl:
    `curl -N` without `-H "Accept-Encoding: gzip"` reproduces nothing, because
    it is not asking for the compression that causes the bug. And
    `BaseHTTPRequestHandler` defaults to **HTTP/1.0**, under which a response
    with no Content-Length is framed by closing the connection - so the
    handler set `protocol_version = "HTTP/1.1"` and sent `Connection: close`
    on the one streaming route. *(Both retired with the handler itself in
    gotcha 105. uvicorn frames a streaming body as chunked. The pad,
    `no-transform` and `X-Accel-Buffering` above are all still load-bearing.)*

62. **An id-shaped token the reader cannot cite gets cited anyway.**
    `agent.render()` listed an item's transcript as `[385] Yeager: so my
    motion is...`, where 385 is a line index. The model cited it the only way
    it could - "([item:31314] passages 2, 59-60)", in prose - so the motion
    and the vote it had correctly found were uncitable, and the citation check
    counted zero. Lines now carry the id of the PASSAGE containing them, which
    is the honest reference anyway: a citation points at a moment, and a
    passage is exactly that moment. If a rendering shows a bracketed number,
    that number had better be citable.

63. **`schema.sql` had drifted from the live database, and replaying it
    reverted a guard.** The `utterance_speaker` view's cluster fallback carries
    an affinity condition - a cluster may not name a voice its own voiceprint
    was measured not to be. That condition existed in the RUNNING view and had
    never been written back to `bin/schema.sql`. Rebuilding the view from the
    file to add a different guard silently dropped it, and 8,795 utterances
    went back to carrying a disproved name.

    It was caught only because `speaker.no_disproved_names` failed - and it
    would not have failed a week earlier, because `voice_affinity` was stale
    and the check was passing by having almost nothing to measure. Two lessons,
    and the second is the bigger one: **`schema.sql` is the definition of
    record, so a change applied only to the database is a change that will be
    lost**; and a check that passes on an empty set is not passing (the audit's
    own docstring says this, which is why it prints the population).

64. **`respeak.sh` was missing the `affinity` stage.** `refresh.sh` has always
    had it, and its comment says why: re-deriving identity moves names between
    clusters, so every stored similarity is then measured against a name that
    may no longer be there. Running `respeak.sh` therefore left `voice_affinity`
    stale, which is how the state above went unnoticed. Now
    `speaker_id → name_speakers → affinity → index_passages`.

65. **The agenda spine sorted on `seq`, and `seq` is not time.** It is the
    published agenda's order, and the meeting spine is also the chapter track
    for the recording, so the two jobs disagreed wherever the board did not
    follow its own agenda — which is most of the time. Measured before fixing,
    because "the list looks wrong" is not a size:

    - **3,798 of 5,500** located items (69%), across **224 of 283** recorded
      meetings, sat at a rail position that disagrees with when they were heard.
    - Transcript-derived items get a `seq` above every published one, so in
      **all 234** meetings holding both, "Call to order, 0:01" rendered below
      the entire agenda.
    - **126 of 283** recorded meetings are two sessions or three, and offsets
      restart in each, so an unsegmented rail shows 2:09:53 then 1:01:10.

    Now two lanes: located items by (session, offset), then everything else in
    published order under a heading that says why. The tempting third option —
    one list, with unlocated items interpolated between their published
    neighbours — is the one to refuse: 17,600 items on recorded days have no
    time, and putting one between two timestamps asserts one. See R5.2.6.

66. **An item is not one place in a meeting.** The board takes something up,
    sets it aside, and comes back to it hours later — 76 items in 68 meetings,
    and the widest gap is three and a half hours (PC8, 2023-02-02, argued at
    18:05 and again at 3:38:04). Every surface assumed one item, one position:
    the spine listed it once, so the rail had a hole where an hour of argument
    happened and the playhead scrolled *backwards* on reaching the second
    stretch; `/item` concatenated the two, so the transcript read as continuous
    speech with three and a half hours of unrelated business deleted from the
    middle; `/case` took `LIMIT 1` per step and never showed the second at all.

    Merging is required first, because `item_spans` cuts on speaker turns and
    one discussion often arrives in pieces. The threshold is not a taste
    judgement — the gaps between consecutive spans of one item fall in two
    clumps with nothing between them:

        0s x6, 2s x2, 4s, 5s   |   64s, 65s, 67s, 74s, 86s, ... 207m

    So 60s sits in a 55-second-wide trough. 5,587 spans reduce to 5,566
    appearances; 17 of the 93 items with multiple spans turn out to have been
    the binder cutting, not the board returning. `archive._runs` is the one
    definition, used by /meeting, /item and /case. Audited across every span:
    0 invariant failures, 0 index coverage lost.

67. **A long hearing is not a broken one, and I shipped a warning saying it
    was.** While doing gotcha 66 I noticed that **110 of 5,587 spans cover more
    than half their recording** against a median span of 3 minutes, concluded
    that the binder was failing to find item ends and running to the tape,
    filed it as task #26, and — the actual damage — put a warning on /case and
    /item telling readers the stretch was probably mis-bounded.

    Every check refutes it:

    - No wide segment is the last in its video or the only one; each ends
      within a second or two of where the next begins. The bounds are
      deliberate, not defaulted. (My first query said otherwise and was wrong:
      the window function ran after the WHERE, so it counted only the wide
      segments.)
    - The affected meetings have a **median of 8 published items**. The widest
      of all, 96%, is a one-hour emergency meeting declaring a state of
      emergency for Tropical Storm Helene — one item, which is the meeting.
    - The 4h55m APC3 span, read at five points across it, is the same rezoning
      throughout. That meeting published three items.
    - An item ends with its vote, so a swallowed span would have one in the
      middle. Matching ROOM_TALLY/ROOM_FAIL inside every span: last vote at
      **97%** through a wide span, **96%** through a normal one, median two
      minutes of talk after it in both. Wide spans are slightly BETTER bounded.

    The four spans archive-wide with a vote more than 15 minutes before their
    end are all budget hearings and commissioner-business blocks, which vote
    several times by design.

    `share` and `loose` are gone from `_runs`; /case shows the hearing's
    **duration** instead, which is the thing a reader actually wanted. The
    lesson is the ordinary one: a ratio that looks alarming is a hypothesis,
    and this one cost a user-visible false claim because I wrote the warning
    before testing it. The reasoning is kept in `archive._runs` so the next
    person to notice the ratio does not re-derive it.

68. **`bin/schema.sql` could not create the schema from scratch.** `voice_name`
    references `meetings` and `people`; `utterance_speaker` references
    `voice_affinity`; all three were defined later in the file. Nothing ever
    caught it because production was built statement by statement during the
    SQLite migration and every run since has been against a database that
    already had the objects. `bin/sandbox.py` creating an empty database is
    what found it — which also means disaster recovery would have failed at
    exactly the moment it was needed. Both views now sit at the end of the
    file, under a banner saying why they are not beside the tables they
    describe.

69. **`bin/rebuild.sh` would have destroyed production, twice over.** Neither
    fired, because `--yes` had never been run.

    - It does `source ./env.local.sh`, which exports the production DSN
      unconditionally. So the invocation printed in `bin/sandbox.py`'s own
      docstring — `PASCO_DSN="$(bin/sandbox.py --dsn)" bash bin/rebuild.sh
      --yes` — set the sandbox DSN and had it overwritten one line later. The
      script whose entire purpose is to avoid touching production was the thing
      that would have truncated it. All six shell scripts now preserve an
      injected DSN, and rebuild.sh prints its target and shouts on production.
    - The TRUNCATE could never have run at all: `videos.meeting_id` and
      `portal_events.meeting_id` reference `meetings`, and Postgres refuses to
      truncate a foreign-key parent whether or not any row references it.
      `CASCADE` would have been a catastrophe — it reaches `utterances`, the
      1,036 GPU-hours the script exists to preserve. Both columns are derived
      and are now nulled first, and `meetings` is emptied with DELETE. The
      blocker query is exhaustive so a new foreign key cannot reintroduce it.

70. **The segmenter was never shown the county's published agenda**, and when
    it was, a whitelist threw the answer away.

    The model was being asked to recover "R-58" from ASR output of somebody
    saying "R fifty eight", with the exact string sitting in `agenda_items`,
    landed before `segment` runs. Adding the agenda to the prompt took two
    tries — the first version stacked three "return null when..." warnings
    against one positive instruction and got null for everything, on a meeting
    where its own titles named the right items.

    Then it still reported zero matches, because `propose()` rebuilds each mark
    from a whitelist of `line`/`phase`/`title`. Rebuilding from a whitelist is
    the right instinct — model output is untrusted input — but it silently
    drops any field added later. `code` was parsed correctly and validated
    correctly by `assemble()`, and discarded in between.

    **What it bought, measured on the five sandbox fixtures.** Modest, and
    worth being precise about: 42 published items bound before, 46 after. 40 of
    the 42 land in exactly the same place, none moved by more than two minutes,
    2 were lost and 6 gained. The gained ones are the interesting kind —
    `C48 Ranch Road sidewalk project`, `C50 Curley Road safe routes sidewalks`,
    `C69 public hearing time change`, `P86 Palmetto Ridge rezoning continuance`
    — items PULLED FROM THE CONSENT BLOCK for individual discussion, which is
    precisely where someone cared enough to pull them. Coverage is capped by
    what gets discussed aloud, not by the model's ability to identify it, so
    the agenda could never have moved the headline number much.

    The real win is that codes are now checked against a list we hold rather
    than read out of a title with a regex: 0 invalid codes across the fixtures,
    against 47% regex recovery before.

71. **Self-consistency would be wasted money here.** Before the agenda, the
    same meeting segmented twice gave 10 segments and then 7, which looked like
    a reason to run N times and keep only what agrees. Measured after the
    agenda, three runs of one meeting-day returned **the identical five codes
    at the identical line numbers**. All the residual variance is in uncoded
    procedural segments — whether the five-minute break is its own segment or
    part of new business — where a majority vote produces a different answer,
    not a better one. Spend the money on the deterministic checks the agenda
    makes possible instead.

72. **A voice reading somebody else's letter is not that person speaking, and
    "my name is X" is not evidence about the speaker.** I went looking for
    proof that `name_speakers.bundle()` merges people, because it draws
    evidence with `WHERE cluster=%s` and no meeting or body scoping. The test
    was: does one cluster contain two different people stating their own names?
    30 of 518 clusters did.

    The hypothesis was wrong twice over. 18 of the 30 are ASR spelling one
    person four ways — `Ed Zodi / Ed Zodian / Ed Zodie / Ed Zodin`. Of the
    rest, the two largest — cluster 126 (9,023 utterances, 151 meetings) and
    cluster 207 (8,053 utterances, 189 meetings) — turned out to be **one voice
    reading emailed public comments aloud**: "the next letter I have is from
    Doreen Alvarez", "it's signed Clay Witherspoon". The clustering is right.
    My detector was reading quoted speech.

    That leaves a real defect of a different kind, now task #27: **152
    utterances say "my name is <someone>" while attributed to a different named
    speaker**, and 1,169 look like read-aloud correspondence. The voice is
    correct and the authorship is not, so a reader searching a commissioner's
    record finds residents' opposition letters under his name. Peak 2021, still
    ~145 a year in 2025.

    Two things to carry forward. Cluster-level evidence is NOT contaminated in
    the way I assumed, so `bundle()` does not need scoping. And near-duplicate
    speaker NAMES are worth mining for a different reason — see gotcha 73.

73. **Two similar names are always two clusters, never one.** A name is
    assigned per voice cluster, so two different names cannot share a cluster
    by construction — 0 of 1,601 candidate pairs do. Every pair of near-
    identical names is therefore a SPLIT VOICE: the diarizer cut one person in
    two and each half was named independently from its own self-introduction.
    `Christopher Poole` (1,600 utterances) and `Christopher Pohl` (43) are
    clusters 249/2509 and 3025. `Girardi` (14,424) and `Gerardi` (28).

    So name similarity is a free, high-precision detector for the split-voice
    queue (task #21), and it wants a model rather than a threshold: `difflib`
    called `Michael Racor`/`Mike Razor` two people and `Janine Duffy`/`Janine
    Dombrowski` two people, while `Linda Bell`/`Linda Snell` scores the same as
    `Christopher Poole`/`Christopher Pohl` and may genuinely be two residents.

74. **The agenda prompt was tested across twelve years, and the old era is
    where it helps most.** Only 1 recorded meeting in 2018 and 7 in 2019 have
    a published agenda, and they are all Planning Commission, so a like-for-
    like BCC comparison cannot reach past 2020. Pairs chosen to hold the body
    constant:

        2018-12-13 Planning Commission,  11 items:  4 bound  ->  3 matched
        2021-12-07 BCC,                 186 items: 26 bound  -> 30 matched
        2026-07-14 BCC,                 191 items: 23 bound  -> 27 matched

    Agenda code formats are stable in shape across the whole span, so nothing
    era-specific was needed. The gain is largest on 2021, which is consistent
    with worse ASR on older recordings: recovering "P-116" from speech is
    hardest exactly where a lookup helps most.

75. **A publication notice is not the item being taken up.** The 2021 test
    returned six codes twice, and reading the transcript at those points showed
    why: Florida requires the advertising to be declared into the record, so a
    run of "Item P-112 was published in the Tampa Times on November third"
    precedes the hearings by hours. The model was marking both the recital and
    the hearing.

    This is a convention, not an era artifact — **888 utterances across 142
    recordings, every year from 2019 to 2026**, peaking 2021-2024. So the
    prompt now says a notice recital is not the item being taken up, and gets
    no code.

    It costs coverage and that is the right trade. On 2021-12-07 the rule took
    the distinct-item count from 34 to 30; the four lost were P113, P114, P115
    and P118, and **P114, P115 and P118 have zero mentions in the transcript
    beyond the recital** - all three are consent items approved without
    discussion. Binding them would have pointed a reader at an advertising
    declaration and called it the debate. The repeated code that SURVIVED the
    rule is P112, deferred at 0:34:14 and heard at 4:03:48, which is a real
    return (R5.2.7).

76. **Every threshold for "are these two names one person" has a
    counterexample in this archive.** `bin/split_voices.py` was written as a
    classifier four times and demoted to an evidence gatherer, which is where
    it should have started.

        cosine          'Christopher Poole' / 'Christopher Pohl' sit 0.896
                        apart, FURTHER than two random strangers, because a
                        different microphone moves a centroid more than a
                        different throat does.
        co-occurrence   'Girardi'/'Gerardi', 'Rob Park'/'Robert Park' and
                        'Diane Cobernic'/'Diane Cobernick' all share meetings.
                        One person picked up on two mics in one room does that,
                        so sharing a room is not proof of being two people.
        name ratio      'Dan Mcdonald'/'Leanne Mcdonald' scores 0.81 on a
                        shared surname and is plainly two people.
        name parts      requiring both the given name and the surname to
                        survive helps, and still admits 'Paul Thatcher' /
                        'Paul Butcher'.

    Poole/Pohl is the case worth remembering, and it was settled by the
    published record rather than by any signal above. Christopher B. Poole is
    on the roster, seated on the Planning Commission from 2020-01-23, and
    speaks at 102 meetings. 'Christopher Pohl' speaks 43 times at ONE meeting,
    2020-10-08 — at which Poole, though seated, says nothing at all.
    Complementary distribution plus a roster seat is near proof, and one of
    Pohl's 43 lines is "I'm being told that your microphone is on."

    So the tool ranks candidates and prints all four signals, asserts no
    verdict, and writes nothing. 40 pairs survive the filters and 476
    utterances sit under the smaller name of a pair - the ceiling on what
    adjudication could correct.

77. **`land_agenda.bind_spans` is not idempotent, and re-running it grows the
    archive.** Two runs on unchanged data added 447 then 262 transcript-derived
    agenda_items. I found it by building a nightly forward-fetch job around
    `land_agenda.py` and testing whether a second run was a no-op. It was not.

    The mechanism is a pre-existing defect this only exposed. `upsert_meetings`
    re-derives `videos.meeting_id` on every run, and this archive has duplicate
    meeting rows for the same date - `audit.py` already documents the
    same-day-sibling split. When a video migrates between siblings, the reuse
    lookup in `bind_spans`

        WHERE ai.meeting_id=%s AND ai.source='transcript'
          AND sp.video_id=%s AND sp.start_idx=%s

    no longer finds the item, because it searches by the NEW meeting_id. So a
    fresh item is created, the span INSERT reassigns the span to it, and the
    original is stranded with no span. Meeting 1196 is the clearest case: it
    holds orphaned transcript items and has no segments and no spans at all,
    because its videos now belong to its sibling.

    STATE LEFT BEHIND, to be repaired by the next full refresh rather than
    patched: 709 orphaned transcript items and 6,595 passages pointing at them.
    The live set is 3,306, which exactly matches the original count, so nothing
    was lost - there is surplus. `land_agenda --redo` followed by
    `index_passages` rebuilds both consistently and is already the order
    `rebuild.sh` uses. Task #28.

    `land_agenda.py --no-spans` now exists for callers that only want the
    county's published agenda, and `bin/forward.sh` uses it. Verified: two
    consecutive runs change nothing.

78. **The root cause of 77: a meeting is keyed on (date, body), and that is
    not unique.** 74 date+body pairs in this archive carry several meeting
    rows, and — measured — **every one of the 74 groups has entirely distinct
    titles**. There are ZERO true duplicates. They are different committees of
    the same parent body meeting on the same day: MPO's Technical, Citizens and
    Bicycle & Pedestrian advisory committees all sit on 2027-01-11 and all
    carry `body = 'Metropolitan Planning Organization'`. `audit.py` reads this
    as a "same-day sibling split" and treats it as a bookkeeping error; it is
    not, it is the `body` column being unable to name what the county actually
    convened.

    The linking statement then said

        WHERE k.kind = v.kind AND m.date = v.upload_date AND m.body = k.body
          AND v.meeting_id IS DISTINCT FROM m.id

    which reads as "fix wrong links" and means "relink whenever the current
    meeting is not THIS sibling". With several siblings matching, the winner is
    whichever row Postgres yields, and it can differ between runs. **32 of 432
    transcribed recordings** sit on such a date, along with **187 portal
    events**, and they migrated every run. That is the engine that stranded the
    items in 77.

    Both statements are now STICKY - they only touch a row whose meeting is
    null or no longer matches its own date and body - and DETERMINISTIC when
    they must choose: videos prefer the sibling holding the most published
    items, then the lowest id; portal events prefer the sibling whose title is
    the event's own name. Verified: two consecutive FULL runs (spans included)
    change nothing, and "transcript-only phases" went from 262 to 0.

    The 74 groups are not a defect to repair. The defect is any code that
    assumes (date, body) identifies a meeting.

79. **Sticky was not enough: 112 portal events were parked on the wrong
    sibling, and the minutes went with them.** Making the link sticky (78)
    stopped the churn and froze the existing state, which was partly wrong - a
    wrong sibling still matches date and body, so the link looks valid and
    never moves. Measured: a "Pasco County Commission Workshop" attached to the
    plain "Pasco County Commission" of the same day, a Bicycle and Pedestrian
    Advisory Committee attached to the Citizens' Advisory Committee, 112 in
    all.

    The event's own `name` is unambiguous evidence - it is what the meeting
    title was created from - so the link now PREFERS the meeting it names.
    That converges rather than churns: an event moves once and then matches.
    112 -> 0, stable on a second run, no new transcript items.

    The downstream effect is the part worth remembering, and it arrives as a
    NEW audit failure that is not a regression:

        minutes.orphaned_outcomes        964 -> 88   (-876)
        minutes.no_subsidiary_disposition  ok -> FAIL, pool 17,024 -> 17,900 (+876)

    Identical deltas. Those 876 items had minutes hanging off the sibling row,
    so no check could read them; now they are attached and 8 of them turn out
    to record a subsidiary motion as the item's disposition ("Approved to
    receive and file documents submitted by Ms. Cindy Fargo"). That is a
    `parse_minutes` defect that has always been there and was invisible behind
    the broken link. Fixing linkage does not only repair data, it restores the
    audit's reach.

80. **The sandbox existed and I tested against production anyway.** The 709
    orphaned items in 77 were not caused by the bug alone - they were caused by
    testing FOR the bug by running mutations against the live archive, twice,
    when `bin/sandbox.py` had been built that same day for exactly this. An
    idempotency test is "run it twice and compare", which is precisely the
    operation you must never perform on data you cannot restore.

    Rule: any check of the form "does running this again change anything" runs
    against the sandbox. If the sandbox cannot exercise the case, that is a
    reason to widen the fixtures, not to use production.

81. **Nothing compared the live schema to `bin/schema.sql`, so the drift in
    gotcha 63 could happen again - and did.** Adding `segments.code` this
    session was done by hand: ALTER TABLE against two databases, plus an edit
    to schema.sql. Nothing verified they agreed.

    `audit.py` now has `schema.matches_definition`, which reads both the
    `CREATE TABLE` bodies and the 19 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
    statements - that second form is what lets the file be replayed against a
    live database, and a parser that ignores it reports every one of those
    columns as drift. The first version of the check did exactly that and
    called 18 false positives a finding.

    Proved in both directions before being trusted: green on production, red on
    a column planted in the sandbox, green again once removed. A check nobody
    has watched fail is not evidence of anything.

82. **The sandbox told me about a real bug and I called it a fixture
    artifact.** `bin/sandbox.py --compare` reported `roster rows 16 -> 10
    DIFFERS`. That was written off as "a five-meeting sandbox cannot reproduce
    archive-wide speaker statistics". It was not that at all.

    `roster.py --body` defaults to the Board of County Commissioners, and both
    `rebuild.sh` and `refresh.sh` called it bare. Those scripts truncate
    `people`, `board_terms` and `meeting_roster` first — so a full rebuild
    DELETES the 18 Planning Commission board_terms and 1,003 Planning
    `meeting_roster` rows and never puts them back, degrading precisely the
    guard that took cross-body misattributions from 10,715 to 0. Confirmed in
    the sandbox: 0 Planning board_terms, 0 Planning roster rows. Both scripts
    now call roster.py for both bodies.

    The lesson is about reading, not about rosters. A `DIFFERS` line is a
    question. The sandbox did its job; the explanation was invented to make the
    number go away.

83. **What the pipeline idempotency audit found.** Every stage was checked;
    `chair_anchor`, `affinity`, `index_passages` and `eval_agent` are clean,
    `roster` has a latent tie-break with zero impact today. The rest, ranked by
    damage and each verified independently before being believed:

    - `speaker_id` upserts `source = NULL` over every voice, and it is the only
      writer of that column. **Zero rows carry `source='chair'`** — the chair
      anchor has been erased. Three clusters covering **69,596 utterances**
      currently hold a name `chair_anchor` contradicts. Task #31.
    - `speaker_id` never RETRACTS. Voices held out of clustering keep whatever
      the last run decided: 287 held out, 108 still named, 463 utterances still
      displayed. Including the one human `speaker_ignore` row — "not a person" —
      still shown as **Oakley, confidence 0.954**, on 17 utterances. A human
      veto that does nothing. Task #32.
    - `parse_minutes` resolves on `(meeting_id, code)`, which is not unique: 39
      duplicated pairs, 25 with different sections. Proven non-deterministic by
      toggling `enable_indexscan` alone — meeting 220's PC2 stores
      `('approved','bulk_included')` under one plan and nothing under the
      other. Its UPDATE also writes to every row sharing the code, so 58 rows
      carry a disposition parsed for a different item. Task #33.
    - `name_speakers` drains a backlog rather than being a no-op, and neither
      it nor `segment` has a free dry run — both bill the API without
      `--write`. `segment` also re-sends 2 permanently-rejected meeting-days on
      every run.

84. **The four audit defects are fixed in code and NOT applied to production.**
    Verified in the sandbox, which had to grow three fixtures to exercise them
    at all — 220 (the county reuses PC1..PC5 across two sections), 27 (C1 is
    both a consent resolution and a rezoning) and 712 (carries the one human
    `speaker_ignore` row). If a bug cannot be reproduced in the sandbox, widen
    the fixtures; do not reach for production.

    Three new checks guard the fixes, each proved RED before being trusted
    green: `speaker.ignore_honoured`, `speaker.chair_anchor_intact`,
    `minutes.one_sentence_one_item`. All three were red on production when this
    was written, correctly, waiting for the fixed stages to re-run there.

    **RESOLVED 2026-08-13. All three are green and the archive is at 0 failing
    of 42.** `minutes.one_sentence_one_item` cleared when the console's
    `portal_sweep` ran `parse_minutes --write`; the other two cleared when the
    identity chain was re-run with `chair_anchor` put back into it (gotcha 86).
    Do not read the paragraph above as a description of the live archive.

    `speaker.chair_anchor_intact` recomputes from `chair_anchor.evidence()`
    rather than reading `source`. A check that read `source` would have gone
    green on an archive where the anchor had been erased entirely — which was
    exactly the state production was in until the re-run.

    Two things worth carrying forward about how this went:

    - **The fix for gotcha 31 introduced a regression, caught during
      verification.** "Never overwrite a sourced row" locks out a human
      `speaker_label` written after `chair_anchor` ran, inverting R5.8.7.
      Exempting labelled voices fixed it. A fix that only satisfies the test it
      was written for is half a fix.
    - **Task #29's proposed fix was wrong.** The code already refused
      subsidiary motions and had since before the task was written; the 8 rows
      were stale, from a parser that predates `choose()`, unreachable while
      their minutes hung off a sibling row and re-exposed by the linkage fix in
      79. A task written from an audit reading is a hypothesis, not a
      specification.

85. **Another session is writing to production.** `bin/job.py` and
    `ui/components/admin/OpsScreen.tsx` — an operations job runner built by the
    admin-console work — ran `portal_sweep`, and with it
    `parse_minutes --write`, against the live archive at 08:15. That is what
    cleared the 8 subsidiary dispositions.

    Nothing wrong with it, but two sessions now mutate the same database and
    neither announces it. A measurement taken here can be invalidated between
    two commands by work happening elsewhere, and "the number changed and I did
    not change it" is now a normal outcome rather than evidence of a bug. Check
    `pg_stat_activity` and `bin/job.py` before concluding anything about a
    figure that moved.

86. **Both re-derivation chains left out `chair_anchor`, so neither could
    repair the thing identity was most wrong about.** `speaker_id` stopped
    erasing the anchor (83), but nothing put it BACK once erased, and
    `bin/rederive.py` - the console's "propagate labels" button - ran
    `speaker_id -> affinity -> index_passages` under the promise that it
    re-derives who each voice is. Production went through that chain at 08:04
    and still held three clusters, **69,596 utterances**, under a name the
    county's own published roster contradicts. `bin/respeak.sh` had the same
    hole.

    The stage is free - the roster plus the script the presiding officer
    reads, no model and no network - so there was never a reason to leave it
    out. It was left out the way `affinity` once was: by writing the chain from
    what the last failure needed rather than from what the layer is made of.

    Proved rather than argued, in the sandbox: drift cluster 36 to Oakley and
    `speaker.chair_anchor_intact` goes RED on 982 utterances; `chair_anchor
    --write` alone flips it back and the check goes green. Worth recording that
    `speaker_id --write` ALSO repaired it there, so the sandbox is not the
    whole proof - its cluster is not mixed the way production's three are, and
    a mixed cluster is precisely where the archive-wide majority outvotes the
    right name.

    Applied to production 2026-08-13 15:49: 291 Cox => Grey, 231 Mariano =>
    Oakley, 192 Mariano => Starkey. **69,596 utterances**, audit back to 0
    failing of 42, and naming coverage 159,892 -> **204,146** without a paid
    stage, because the affinity gate had been refusing to hand out a name three
    clusters were storing wrongly.

    **The consequence to expect, and it is not a regression: the split-voice
    queue went 3 -> 394 in the same run.** All 394 involve the chair-anchored
    cluster, and they trace to **23 distinct impostor clusters**, six of which
    account for 316 of them - cluster 126 alone collides in 130 meetings under
    two different commissioners' names, and 126 is the clerk this file already
    identified. Before the anchor the right name sat on the wrong voice, so the
    check saw one name per meeting; now the correct voice carries it and the
    impostor is exposed beside it. The misattribution moved from hidden to
    visible, which is the trade this archive takes every time (79). It is about
    six listening decisions, not 394.

87. **`SystemExit` in a library hangs a server thread silently, and that is
    how `/api/ask` was dead for weeks.** `ask.api_key()` raised `SystemExit`
    when no key was in the environment - reasonable for a CLI, fatal in a
    server. `SystemExit` inherits from `BaseException`, not `Exception`, so
    the `except Exception` around every request in `web/server.py` never saw
    it; `ThreadingHTTPServer` unwound the request thread quietly with the SSE
    connection still open. The reader watched **"thinking" for ever** - 70s
    observed, and it would have been all day - and the server logged nothing.
    A library imported by a server may not decide to exit the process.

    Fixed at the source (`ask.MissingKey`, `db.MissingConfig`, both
    `RuntimeError`), at the entry (`web/server.py` checks the key BEFORE
    emitting the first stage event, so the reader is never told the agent is
    thinking and only then that it never could), and with a guard that makes
    the whole class loud: the SSE handler catches `(Exception, SystemExit)`.
    Measured after: the keyless case answers `event: error` with the fix in
    one second. `db.dsn()` had the identical bug and would have hung every
    request the same way.

88. **The env file was split across two repos, and the seam is what removed
    the key.** `bin/_env.sh` sourced `./env.local.sh` and then, by ABSOLUTE
    path, `/media/user/Data/IdeaProjects/active-reading/env.local.sh` -
    another project's file, which is where `LLM_API_KEY` lived. Every
    pipeline script goes through `_env.sh` and was fine; `web/server.py` is
    started by hand and was not, so the server ran with `llm_key: false`
    while the CLI worked perfectly. That difference is why it looked like an
    Ask bug rather than a configuration one.

    Everything the project reads from the environment is now in this repo's
    `env.local.sh` (gitignored, mode 600): DSN, key, `INFERENCE_API_BASE`,
    the three model names, `PASCO_EMBED_DEVICE`, `ARCHIVE_API`. Nothing
    outside this directory is required to run the archive. The injected-DSN
    contract is unchanged and was re-proved: an exported `PASCO_DSN` still
    survives `_env.sh`, which is what `bin/sandbox.py` depends on.

89. **`/api/ask` was a public, unauthenticated endpoint that spends money,
    with nothing bounding it.** One question is up to eight turns of a paid
    model plus its tool calls - measured, 5 turns and 11 tool calls in 38s -
    and any address could run that in a loop. On a URL that is about to be
    public, that is not a threat model, it is a Tuesday.

    `web/limits.py` bounds four things, cheapest checked first: question
    length, questions per address per window, questions per day across ALL
    addresses, and how many may run at once. The daily cap is the one that
    protects the account, because a per-IP window bounds one client and a
    botnet is many clients. `ASK_PER_IP=0` or `ASK_DAILY_MAX=0` closes Ask
    without a deploy and the archive keeps reading and searching.

    Three details that are the whole difficulty:

    - **The refusal has to happen before the 200.** Once the event-stream
      headers are out, the only way left to say no is inside the stream,
      where a proxy cannot see it and no status code can be set.
    - **A browser cannot read a 429.** EventSource exposes neither status nor
      body to the page - only a bare `error` - so a 429 shows the reader
      "something went wrong" and never the sentence telling them when to come
      back. Requests that send `Accept: text/event-stream` therefore get 200
      plus an `event: error` carrying the message; everything else (curl, a
      monitor, a WAF counting 429s) gets the real status and `Retry-After`.
      Neither path calls the model: measured at 3ms.
    - **X-Forwarded-For is forgeable.** Behind a proxy every peer is
      127.0.0.1 and the limit would apply to the proxy; trusting XFF without
      a proxy hands every request a fresh quota. It is read only from a
      loopback peer, only when `ASK_TRUST_PROXY=1`, and then the LAST hop is
      taken - our proxy appends what it saw, and everything left of that came
      from the client. **Set `ASK_TRUST_PROXY=1` when you put nginx in
      front, or the whole internet shares one bucket.**

    Counting only ACCEPTED runs is deliberate: if refusals counted, a script
    that keeps knocking would extend its own lockout for ever and the message
    it is shown would be a lie.

90. **A reader now has a deadline the batch pipeline does not.**
    `ask.TIMEOUT` is 600s because a whole-day segmentation prompt honestly
    takes ten minutes; a person watching `/ask` must not inherit that, eight
    times over. `agent.DEADLINE` (150s) bounds the whole question, and each
    call gets whatever is LEFT of it less the room the closing answer needs -
    without that subtraction one slow call spends the entire allowance and
    the reader gets a timeout instead of the answer its evidence had already
    paid for. Expiry takes the same path the evidence budget takes: stop
    calling tools, answer from what is gathered, report `stopped: time
    budget`. A slow model costs a shorter answer, never a blank one.

91. **The surfaces a public release needs, none of which existed.** No
    `error.tsx` (a route that threw showed the framework's blank "Application
    error"), no `not-found.tsx`, no robots, no sitemap, no `metadataBase` -
    so every shared link rendered bare and every canonical URL resolved
    against localhost.

    - **`error.tsx` takes `retry`, not `reset`.** This version of Next
      renamed it; the old name silently gives you a dead button. The docs in
      `node_modules/next/dist/docs` are the source of truth, and reading them
      first is why this was not shipped broken. Verified against a real
      thrown route, digest and all.
    - **`not-found.tsx` covers `notFound()`**, which all three dynamic pages
      call for a bad id and for a 404 from the API. `global-not-found.js`
      would also cover unmatched URLs but is experimental and needs a config
      flag - not a switch to throw on release evening.
    - **The sitemap pages.** `/api/meetings` caps `limit` at 500, so asking
      for 20,000 returns 500 and says nothing about it: the first sitemap had
      504 URLs for a 1,251-meeting archive and looked perfectly healthy. It
      now pages to `total`, and `lastModified` is the MEETING DATE - stamping
      1,251 settled meetings with the build time tells a crawler that eight
      years of public record changed this morning.
    - **`/about` measures itself.** Every count is read at request time,
      because COPY.md wants counts rather than "many" and a number baked into
      prose in August is wrong by September. It leads with what is MISSING:
      931 of 1,214 meetings have no recording, 5,592 of 23,123 items have no
      disposition in the minutes.
    - ~~**About is linked from a footer, not the nav.**~~ **Reversed the same
      day, at the maintainer's direction: About is a header nav item and the
      site footer is deleted.** It cost every page 231px of flow under the
      content, which on the meeting page is what produced a page scrollbar the
      reading panes ate the wheel for. Its blanket claim was also the pattern
      R3.2 refuses in as many words — a single site-wide disclaimer "trains
      readers to ignore it" — and what carries that weight instead is
      per-object: the transcript states its own limits, an item states whether
      it has a disposition, a meeting states whether it was recorded. /about
      still says all of it in full, and is now one click from every page
      rather than one scroll to the bottom of every page.

    `SITE_URL` and `SITE_CONTACT` are in `ui/.env.local` (they were in
    `env.local.sh`, where Next never saw them — see "Running it"). The /about
    page states the correction promise whether or not an address is set, and
    never offers a dead `mailto:`. `SITE_CONTACT` is set as of 2026-08-14.

92. **The meeting page did not fit in a window, and every cause was a
    constant standing in for a measurement.** Reported by the maintainer as
    "the scroll positions are weird, things don't fit". Measured at 1280x1040
    and 1280x720, and the errors were identical at both sizes, which is the
    tell: they are constants, not a responsive failure.

    - **The panes were sized by guesswork.** `TranscriptView.scroll` said
      `height: calc(100vh - var(--header) - 14rem)` and the rail said
      `calc(100vh - var(--header) - var(--sp-6))`. Both are standing in for
      "how much chrome is above me", which measures **452px** on this page and
      varies with the masthead - a meeting with two recordings and a roster is
      taller than one with neither. So the transcript hung **176px** below the
      window and the rail **239px**, at every screen size, and the layout was
      only correct when the page happened to be scrolled to its bottom.
      `--pane-h` is now measured from the split's own offset.

    - **A scrolling box only clips what it is the containing block for.**
      Giving the record panel `overflow-y: auto` fixed nothing: the record
      carries **90 absolutely positioned `ProvenanceMark` labels** whose
      `offsetParent` was BODY, so they escaped the clip and staked out
      **18,894px of EMPTY document**. `elementFromPoint` at the bottom of that
      scroll returned bare `HTML` - you could scroll into nothing. Adding
      `position: relative` took the document from 19,934px to 1,092px.

    - **CSS Modules localises id selectors, not only class selectors.**
      `.main > #panel-record` compiled to a hashed id and matched zero
      elements while looking correct in the file. Selected by
      `[aria-labelledby="tab-record"]` now.

    - **`--dock: 4.5rem` describes a dock that measures 289px.** The token and
      the pages' `padding-bottom: 8rem` both under-reserve by 3-4x. The dock
      now publishes what it OCCUPIES - the overlap between its own rect and
      the viewport, republished on `transitionend` - because it is active
      whenever a recording is loaded but slid out of the viewport until shown,
      so keying on `active` padded the transcript for a dock nobody could see.

    Verified on all three layout states (867 agenda+recordings, 605 no
    recording, 835 no published agenda), both tabs, 1280x1040 / 1280x720 /
    375x812: page scroll 52px, nothing below the fold, no scroll into
    emptiness, no horizontal overflow, virtualisation still recycling
    (translateY 0 -> 14,319 with different rows). Mobile keeps the page as the
    scroller; the measured height is dropped below 68rem.

    The dock's SHOWN state could not be verified here - it hosts a YouTube
    iframe this environment cannot load, so the dock never leaves its hidden
    position. `--dock-h` reads 0px and adds no padding in that state, which is
    correct, but the padded state is unproven.

    **Sequel, same day, and it is the more useful half.** Filling the window
    made a second defect obvious that the measurements above had missed
    entirely: this page carries THREE scroll surfaces - spine, transcript, page
    - and all three were `overscroll-behavior: contain`. Containment means the
    wheel stops dead at a pane's edge instead of passing to the page, and the
    panes cover about 60% of the window, so the page's 283px of scroll (the
    footer) could not be driven from most of the screen. Reported by the
    maintainer twice, as "the wheel does nothing" and then as "multiple
    fighting scrollbars" - which is the same defect described from the outside.
    All three now chain. `overscroll-behavior` governs USER scroll chaining and
    not programmatic `scrollTo`, so following the playhead is unaffected.

    Two wrong turns before that, both worth recording because both looked
    principled at the time. **Reserving the footer** so the page would not
    scroll at all cost the panes 231px on top of a 255px masthead and left the
    agenda spine showing three of 197 items - correct by measurement, useless
    to read. **Then a convergence loop** that grew or shrank the panes by the
    document's own overflow: it oscillated against the ResizeObserver watching
    the article it was resizing, which the reader sees as scrollbars flickering
    in and out. A measurement that feeds back into the thing being measured
    needs a fixed point, and "observe the element whose height I am setting"
    does not have one. Both were reverted; what shipped is the chaining fix,
    which is three lines.

    **Then the maintainer removed the premise.** Deleting the site footer and
    making About a nav item took the meeting page's page-scroll from 283px to
    52px, and reading the last two insets properly - the split's own padding
    and the article's, which are boxes AROUND the panes and so cannot feed back
    into the measurement - took it to **0**. The page now has exactly two
    scrollable regions, the spine and the reading pane, each under the pointer
    that is over it. Nothing to fight.

    The lesson is not about CSS. Three of my four attempts were spent making a
    page scrollbar tolerable; the fix was to stop the page needing one. When a
    layout keeps producing "yes, but" the thing to question is what is on the
    page, not how it is measured.

93. **Mobile had been checked at 375px, and the failures live at 320px.**
    WCAG 1.4.10 Reflow is judged at **320 CSS px**, not at the width of the
    phone on your desk. At 375 the archive was clean except one page; at 320,
    four surfaces scrolled sideways, one of them site-wide.

    - **The site header, on every page.** brand 105 + nav 154 + toggle 32 +
      gaps and padding = 339px in a 320px row, so the theme toggle sat 7px off
      the edge everywhere. It cannot wrap: `--header` is a fixed 3.25rem and
      the meeting panes measure against it. The brand shrinks instead - it is
      the one item here that still says what it is when shortened.
    - **`minmax(20rem, 1fr)` is a FLOOR, not a preference.** `Entryways.lanes`
      kept a 320px track inside a 262px container and put 29px of sideways
      scroll on the front page. `minmax(min(20rem, 100%), 1fr)` lets the track
      give up its preferred size when there is less room than that.
    - **A pill cannot shrink below its own text.** `CaseThread.row` is a nowrap
      flex row and "No disposition in the minutes" sets 142px, so `/item/20973`
      ran 29px past the edge **at 375px**. The row wraps on small screens now
      and the badge keeps its shape.
    - **The admin bar** came to 367px at 320. Not its working width, but a
      control that leaves the screen is a defect at any width.
    - **156 tap targets under the minimum.** Every TimeAxis cell is a link, and
      the mobile rule made them SMALLER (1.1rem): 21.2 x 17.6 with a 2px gap is
      a 19.6px pitch, under 2.5.8's 24px and too tight to earn the spacing
      exception. Height is 1.375rem on coarse pointers now, making the vertical
      pitch exactly 24px. **Horizontal pitch is 23.2px and was left alone** -
      the last 0.8px would have to come out of the year label or the trailing
      column, and "the entire twelve-year axis fits on a phone" is a property
      somebody measured on purpose. That one is a design call.

    Two things that LOOKED like defects and are not, recorded so they are not
    "fixed" later: the `/` issues grid is far wider than the viewport and is
    inside `overflow-x: auto` (proved reachable - scrollLeft moves 131px), and
    `ItemCard.rowTitle` reports `scrollWidth > clientWidth` because it is an
    intentional `-webkit-line-clamp: 2`.

    Verified after: 10 routes x {320x640, 375x812}, plus 414 - zero document
    overflow and zero elements escaping their container on every one. The
    meeting page keeps its mobile model: rail static at 26rem, record in page
    flow, transcript scrolling itself at 20rem.

92. **Redacting members of the public's home addresses.** Florida's public
    comment convention has every speaker state a name and an address at the
    podium, and the clerk reads addresses out of emailed comments too: 2,476
    utterances of 298,737 carry a street-address shape. Those addresses were
    always on the public record. What this archive changed is that they became
    SEARCHABLE, and "obscure but public" and "findable by name in two seconds"
    are not the same fact about a person's home.

    Three categories, and the maintainer decided all three (2026-08-13):
    **a residence** is removed; **the matter under discussion** - a subject
    property, a road, a site - is kept, because removing it would gut the
    record; **a business address** given by an attorney, engineer or agent
    appearing professionally is kept, because which firm appeared on which
    application is part of the story. The county's own published agendas and
    minutes are NOT redacted (R2.2 - we reproduce the published record).

    **Redact at the source and rebuild; do not filter at read time.** The same
    words sit in `utterances.text`, `passages.text`, `passages.search_text`,
    the BM25 postings and the `tsv` vector, and the /ask agent reads
    `passages` and would otherwise quote an address straight into an answer.
    Five read paths, and one forgotten path is the leak the feature exists to
    prevent. So `bin/redact.py` rewrites the utterance and calls
    `index_passages.refresh_video`: `tsv` is a GENERATED column and follows by
    itself, and everything else is rebuilt from the redacted text. New readers
    are covered by construction.

    Two guards, because the failure modes are not symmetric. Addresses the
    county published are protected by construction - 931 of them, harvested
    from agenda item and segment titles - and the detector may not touch one
    whatever the model says. And nothing is redacted without a person
    accepting it: the pass writes proposals. The model must return the span
    VERBATIM or nothing (the `name_speakers.py` rule), so it cannot invent a
    redaction.

    **`protected()` failed OPEN on its first run and returned zero addresses**,
    because `for (title,) in con.execute(...)` unpacks a `db.Row`, which is a
    Mapping, and yields the COLUMN NAME - gotcha 13, walked into by the very
    guard whose job was to prevent over-redaction. The same query handed a
    PYTHON regex to Postgres, where `\b` is a backspace and not a word
    boundary (`\m`/`\M` are), so the candidate scan silently matched nothing.
    Both failures were quiet and both looked like "no work to do".

    **The first detector saw 60% of the problem, and passing its own review
    would never have revealed it.** Recall is capped by CANDIDATE
    GENERATION - the model only ever adjudicates what the regex hands it - and
    the regex required digits. This is a machine transcript of speech, so the
    ASR writes what it hears: "I reside at one four three eight two Ashmont
    Drive". Measured against an INDEPENDENT signal rather than the detector's
    own output: of 1,142 lines containing "I live at" or "my address is",
    **45% held no digit-address at all**. 1,480 lines carry a spelled-out
    number in front of a street name. Candidate generation now takes digits,
    spoken numbers, or the self-identification phrase itself, and the pool
    went 2,305 -> 3,932.

    The lesson is the one this file keeps relearning: a spot-check of what a
    detector FOUND cannot measure what it MISSED. The check that found this
    asked a different question - which lines announce an address in words -
    and compared answers.

    **The fix was to stop gating on a regex where it matters.** A SECTION
    pass feeds the model every line of each `public_hearing` and
    `public_comment` segment - 87% of all address lines live in those two
    phases, and hearings alone carry 68%, because that is where residents
    speak on rezonings. 2,018 sections, ~3,300 tokens each, about $2 for a
    full pass. It has no recall gate, and it has the context the line-by-line
    pass structurally cannot: who introduced themselves as an engineer, and
    which address was named as the property under application.

    Head to head on the densest recording: section 62 lines / 103 spans,
    pattern 56 lines, 55 in both. **7 the pattern pass could never reach** -
    including "ninety eight thirty four San Mante Oway", a spoken number with
    an ASR-mangled street name and no suffix - and 1 the section pass kept
    deliberately, a company president's business address. So the two are
    complementary and neither is redundant: the pattern pass still runs over
    the 13% of address lines outside those phases, and over everything, since
    `segment.py` assigns the phase and a hearing mislabelled `regular` would
    otherwise drop out silently.

    **`--cross-check` is the recall measurement, and it costs nothing.** One
    detector is lexical, the other contextual; a line one flags and the other
    does not is either a miss or a judgement worth a person's time. Neither
    can audit itself, but each audits the other. Two mechanical details earn
    their keep: a model reading 3,000 tokens attaches spans to the WRONG line
    number often enough to matter (23 of 101 on one recording), so a span
    that misses its own line is looked for in the rest of the section and
    taken only if exactly one line holds it; and results are written as each
    section lands, because buffering 2,000 calls to the end means an
    interruption discards every call already paid for.

    Still open, and worth doing before trusting the numbers: precision is
    unmeasured (the human review gate is what stands in for it), and a null
    from the model currently drops a line silently rather than routing it to
    review, which biases the whole pass toward misses on exactly the
    ambiguous cases a person should decide - an owner of the subject property
    stating their own address is both things at once.

    Proved end to end rather than sampled: one applied redaction, then
    measured to zero in the transcript, the passage text, the full-text vector
    and the BM25 postings, with `/api/search` returning nothing for either the
    full address or the street name alone. Three invariants in `bin/audit.py`
    (`redaction.gone_from_transcript`, `.gone_from_index`, `.unfindable`)
    state it over every applied redaction at once, because a spot check cannot
    find an address left in the index and nowhere else. Revert restores the
    original exactly and re-indexes from cache.

93. **"What the county keeps coming back to" drew two measures inside one
    cell, on two scales.** The tint was published items scaled to the row's
    peak and a 3px bar along the bottom edge was transcript lines scaled to a
    DIFFERENT peak. That is the dual-axis mistake in miniature: the alignment
    of the two scales is arbitrary, so the picture implies a relationship the
    data does not contain. A full-strength cell meant "7 items and 122 lines"
    on Homelessness and "176 items and 1,155 lines" on Rezoning.

    Now two thin lanes share each year column - published above, said below -
    with the same grammar in both, so the eye compares along a lane and down a
    column. The readable thing is the disagreement between them, which is the
    finding this section exists for and was previously the hairline.

    Three measurements, not opinions, behind the rest of it:

    - **The ramp ran 0-48% of `--accent`, and `--accent` is chroma 0.076.**
      Half of a low-chroma blue against white is a range of near-greys: the
      difference between a quiet year and the busiest one was in the data and
      not on the screen. The ceiling is 88% now.
    - **`--rule` measures 1.24:1 against the DARK surface.** Both marks for
      absence - the "nothing this year" ring and the "no recording exists"
      hatch - used it, so the distinction this grid is built on vanished
      entirely in dark mode. Both now derive from `--ink-4` at fixed alpha and
      land at 1.86/2.51 dark, 1.69/2.21 light.
    - **A border on every one of 216 cells** is what made a field of values
      read as a wall of boxes. The 2px gap already separates them.

    The label under each subject said `first`–`last`, which for any recurring
    subject is the archive's own span restated: ten of eighteen rows read
    "2015–2026". It is now the shortest run of consecutive years holding 80%
    of the subject's activity, prefixed "mostly" unless the run already covers
    every year the subject appears in. **Measured on the published record, and
    on the room only for a subject with no published items** - counting both
    measures coverage as much as subject, because the room starts in 2018, and
    the two-source answer called Stormwater "mostly 2018-2026" while its
    record lane sat at its darkest in 2015-2017. The API's ranking refuses to
    mix the two sources for exactly this reason; the label now agrees with it.

94. **`admin.loopback()` does not survive a reverse proxy, and the whole admin
    security model rests on it.** Found 2026-08-13 while getting ready to point
    `pasco.watch` at this. The guard is

        def loopback(handler):
            ip = handler.client_address[0]
            return ip == "::1" or ip.startswith("127.")

    `client_address` is the TCP peer. Put ANY proxy in front - and `ui` already
    is one, `next.config.ts` rewrites `/api/:path*` to the Python server - and
    the peer is 127.0.0.1 for every request on earth. Reproduced end to end
    against a real stack, not argued: through the web origin,
    `/api/admin/session` answers **200** and `POST /api/admin/login` reaches
    the handler and validates the token, refusing a wrong one with "that token
    does not match this server". The door the file says "never leaves the
    machine" opens onto the internet the moment DNS points here.

    What is actually holding it: the token is `secrets.token_urlsafe(32)`, so
    guessing is not the risk. The risks are that the documented invariant is
    false, and that the session cookie carries **no `Secure` flag on purpose** -
    the comment says "admin only ever answers on loopback, where the flag would
    be a lie about the transport". Behind a public proxy that reasoning
    inverts: the flag stops being a lie and starts being necessary.

    HALF FIXED. The `/legacy/:path*` rewrite is deleted - it forwarded the
    whole Python server, so `/legacy/speakers`, `/legacy/search`, `/legacy/ask`
    and `/legacy/api/admin/session` all answered 200 through the public origin.
    Its own comment justified it with "the surfaces this rebuild has not
    reached yet (search, ask)", and both shipped as slices 3 and 4, so the
    reason had been gone for a while. Verified after: all four now 404, and the
    reading surfaces are untouched.

    **RESOLVED 2026-08-13**, in two layers, because the app must not depend on
    a deploy step nobody can see from the code.

    `loopback()` now asks two questions rather than one. The peer must be
    loopback, which rules out reaching the port directly; AND the request must
    carry no `x-forwarded-for`, `x-real-ip` or `forwarded`, which rules out
    anyone who came through a proxy that put itself in the peer slot. A
    genuinely local client sends neither.

    **What made that safe was measuring what the app's own proxy sends.** The
    Next rewrite adds `x-forwarded-host` on every request INCLUDING the
    operator's, and passes an incoming `x-forwarded-for` and `x-real-ip`
    straight through. So `x-forwarded-host` is deliberately not in the list -
    treating it as evidence would have locked the console out of its own front
    end - and the other three cleanly separate "on the box" from "through
    nginx". Guessing at that list rather than testing it would have shipped
    either a hole or a lockout.

    Verified on all four paths: loopback with no headers 200; each of the three
    forwarding headers 403; the operator through the LOCAL Next proxy 200 for
    both `session` and `login`, with the console rendering; and the same
    request with `X-Forwarded-For` 403.

    The edge is the second lock, and `deploy/nginx-proxy-manager.md` is the
    first thing that has ever described this deployment: `/api/admin` and `/admin` return 404 (not 403 -
    a refusal that distinguishes "exists but forbidden" tells a scanner which
    hosts are worth another look), SSE buffering off for `/api/ask`, and the
    `X-Forwarded-For` that `ASK_TRUST_PROXY=1` needs. The operator reaches the
    console over an SSH tunnel, which is the one path with no proxy in it.

    Note this was the same class of error as gotcha 89's `ASK_TRUST_PROXY`:
    both are "the peer address stops meaning what it meant once something sits
    in front".

95. **The legacy UI and backend are deleted, and the thing keeping the biggest
    piece alive was a test pointing at the wrong code.** 2026-08-13, at the
    maintainer's direction: "any of the old legacy UI or backend need to go".

    What went, and why each was safe:

    - **`bin/ask.py`'s fixed pipeline** - `PLAN_SYS`/`READ_SYS`/`ANSWER_SYS`,
      `plan`, `gather`, `who`, `official_record`, `LENSES`, `read_batch`,
      `ask` and its CLI. 570 lines to 206. D9 retired it and slice 4 put
      `web/agent.py` behind `/api/ask`, but it had one caller left:
      **`bin/eval_agent.py --agent`**, whose help said "also run bin/ask.py".
      So the project's pass/fail check for "can we reach the moment the board
      decided" was measuring a code path no reader could reach, and STATE told
      every returning session to run it. It runs `web/agent.py` now, and the
      assertion got stricter with the change: the old pipeline returned
      everything it retrieved, the agent returns only what it CITED.
    - **`web/api.py` and five hand-written pages** - `search.html`,
      `speakers.html`, `ask.html`, `item.html`, `case.html` - with the routes
      that served them and `/api/agenda/*` and `/api/speakers/*` behind them.
      Checked first that the rebuilt UI calls none of those endpoints; it does
      not. `/admin` replaced the workbench in slice 6 with a data layer that
      orders queues by impact, shows the evidence beside the write,
      canonicalises a name to the surname and re-indexes per write - none of
      which the old write endpoints did. Two write paths onto human judgement
      was one too many.
    - **`/legacy` leftovers**: the rewrite (gotcha 94), the `robots.txt`
      disallow for it, and the `.away` CSS that marked the legacy nav links.

    What stayed, and it matters: **the chat client**. `api_key`, `chat`,
    `chat_raw` and `usage_report` are imported by `segment.py`,
    `name_speakers.py`, `redact.py`, `web/agent.py` and `web/server.py`, and
    the usage accounting is the only place this project measures what it
    spends. Every symbol was checked for external callers before cutting.

    **Three mistakes made doing it, all caught, all worth recording.**

    1. **An infinite redirect on the API root.** Collapsing the dead page
       routes left `if u.path in ("/", ...): return self._redirect("/")`. There
       is no "/" on this server to send anyone to. It answers 404 with "this is
       the archive's JSON API" now.

    2. **`web/api.py` was deleted while `web/admin.py` still used it**, and the
       grep that cleared it only looked at `server.py`. The console imports it
       LAZILY, inside `label()` and `ignore()` - `import api` on a line of its
       own, four levels indented, which no module-level import scan finds.
       **Check for function-local imports before deleting a module**;
       `grep -rn "import <name>"` catches them, `grep "^import"` does not.

       Recovered from `web/__pycache__/api.cpython-312.pyc`: bytecode keeps
       every string constant, so `dis` gave back the exact SQL, and the
       disassembly gave the control flow and both return shapes. The two
       functions live in `admin.py` now as `_apply_label` and `_ignore_voices`,
       which is where they belonged - the console has been their only caller
       since slice 6.

    3. **Test data was committed to production while proving the recovery.**
       The round-trip ran inside `db.connect(autocommit=False)` and ended with
       `con.rollback()`, which looked safe and was not: both writers call
       `con.commit()` themselves, so the rollback had nothing left to undo. It
       left one `speaker_label` row named "Testonly" and overwrote one
       `speaker_identity` row - `-lTQvMQ1GzQ / SPEAKER_08`, cluster 291.
       Removed, and the voice restored to `Grey / 1.0 / chair` from what all
       140 of its chair-anchored siblings carry, so the restore is a
       measurement rather than a guess. Audit back to 0 failing of 45.

       **A function that commits cannot be tested by wrapping it in a
       transaction.** Use the sandbox (gotcha 80), which exists for exactly
       this and which I did not reach for.

    **The pre-Postgres stores are deleted too** - 811 MB, at the maintainer's
    direction, after opening each one and comparing it to the live archive
    rather than trusting the word LEGACY in a comment:

    - `catalog.sqlite` + WAL (287 MB): 19 tables, and a snapshot from MID
      INGEST - 175,289 utterances against today's 298,737, 62,779 passages
      against 167,225. Every table a strict subset. The only irreplaceable rows
      in it were **59 human speaker labels, and all 59 are in Postgres**,
      checked by `(video_id, local_label, name)`.
    - `passages.npy` (245 MB): 62,779 x 1024 float32, positional - row i was
      passage id i. Doubly obsolete: Postgres holds 167,225 passages all
      embedded at the same width, and ids are reassigned on every rebuild
      (gotcha 10) so these could not be matched back even in principle.
    - `vec_cache.sqlite` (279 MB): **orphaned, and I had recommended keeping
      it.** Every `vec_cache` reference in the code is a POSTGRES query -
      `con.execute("SELECT h, v FROM vec_cache …")` with `%s` placeholders. The
      live cache is the PG table at 467,031 rows / 2,544 MB. Nothing had opened
      the file in a long time. A filename matching a live table name is not
      evidence that the file is the live thing.
    - `bin/migrate_to_pg.py`: the one-shot that did the cutover. Its inputs no
      longer exist, and a migration that has run is a script that can only do
      harm if it runs again.

    What made this safe to do rather than to defer: **all 432 videos still have
    `transcript.json`, `audio.flac` and `diarization.json` under `data/`**, 111
    GB of them. Postgres is not the only copy of the ASR, so the worst case is
    a rebuild rather than a loss. Audit still 0 failing of 45 afterwards.


96. **Two audits of the redaction detector said it was broken. Both were the
    audit.** 2026-08-14, reviewing 3,439 proposals before launch.

    - **"24 spans are just a city name, they leave the address behind."** They
      do not. Every one is PAIRED with its own street span on the same line -
      `[13738 Wexford Avenue]` + `[Hudson Beach]` - because the line reads
      "13738 Wexford Avenue **in** Hudson Beach" and the prompt demands a span
      holding only the address. One contiguous span would have to swallow the
      connective, so splitting is the only correct move available.
    - **"A re-run reproduces only 147 of 164 spans - a 10% variance floor."**
      Comparing spans as exact STRINGS. By character-range overlap it is 161
      of 164, **1.8%**, and one of the three survivors is the re-run correctly
      declining a business address the production run had wrongly proposed.
      The difference was trailing periods: `[3204 Ravenswood Drive.]` versus
      `[3204 Ravenswood Drive]`.

    A prompt rewrite was built on the first diagnosis and A/B'd against the
    old prompt, two runs each, over 5 known-issue and 5 known-good sections.
    It gained recall on **zero** of six known misses, and the worst miss found
    (`31251 Ashmont Road`, absent from production) was caught by BOTH re-runs of
    the UNCHANGED prompt - so that miss was variance, not wording. Reverted.

    **SCORE COVERAGE, NEVER STRING COUNTS.** A span set is right when the
    address is gone from the text, so measure the fraction of each labelled
    address the proposals actually cover. String equality punishes a trailing
    period; string COUNTING rewards fragmentation, because two spans covering
    one address count double. `redact.py --sections` prints `found N` as
    distinct strings and it is not a quality number.

    **Ground truth is `eval/truth_6680.json`** - section 6680 hand-labelled
    from the full text, 32 residences plus a `must_not_be_proposed` list, all
    spans verified verbatim. It is the hardest section in the archive: a clerk
    reads a 13-name roster, the mic fails, and he reads the whole roster again
    with different ASR (`4651-Ellerbee Drive` -> `4651 Ellerby Drive`), so every
    household must be caught twice; one address is split across lines 72/73.
    Production scores **31/32 with zero false positives** against it.

    That is what produced `--passes N`, now defaulting to 2: one pass leaves
    4 of 32 addresses in, two removes all 32, three adds nothing. Without a
    labelled section, each variant gets scored against the previous variant's
    output, which is how two wrong diagnoses survived in one evening.

**The address queue is built** (`/admin/redactions`, R9.7). `bin/redact.py` had
proposed **3,439** removals and applied **one**; the machinery was finished and
had no front door, so the addresses were still live and still searchable on a
site about to get a domain. What it needed was not more classifier - it was a
person able to look.

- **`web/admin.py: redactions()`** serves the queue with what a decision
  actually needs: the whole utterance, the span's OFFSET inside it (not the
  span alone - a line that states an address twice would otherwise highlight
  the wrong one), the line BEFORE, the phase, and a link to the moment. The
  line before is the workhorse: "Mr. Park, please state your name and address
  for the record" tells you this is a residence and not a road, without
  playing anything.
- **Bulk apply is a detached job** (`bin/redact_job.py`, the shape
  `rederive.py` established). Applying re-indexes each affected recording -
  370 of them at ~4s - so a request would have timed out long before it
  finished. Progress counts RECORDINGS, which is where the time actually goes;
  counting the 3,439 would have stalled visibly at every re-index. One bad
  recording is logged and skipped rather than stranding the other 369, and its
  proposals stay `proposed` so a retry picks them up.
- Per-row accept is capped at 25 and says why. Reject is instant - it writes no
  transcript.
- **Three lists, not one queue**: to review, removed, kept, each with its count,
  and every decision reversible from the page - a removal can be put back, a
  keep reconsidered. The first build showed only what was undecided, which
  meant no way to check your own work or undo it; that is the same defect the
  split-voice queue had before it grew a ledger (R9.5). A queue is one-way only
  if you build it that way.
- A removed row still shows the ASR line with the address marked, read from
  `text_raw` (gotcha 97). Showing it the published text would show the reviewer
  the marker instead of the thing the decision was about.

Verified against the live 3,439: queue renders with the span marked and the
prior line beneath the meeting, selection bar sticks, a reject round-trips and
the queue refills, counts update, 0 horizontal overflow at 375px, and the
highlight carries a border as well as a tint so it survives greyscale. The test
rejection was restored; the archive is as it was.

**Signing the browser in for that check needed care worth recording.** The
startup token is deliberately unreadable - `init()` returns the path, never the
value - and reading it was refused, correctly. The way through was not to work
around that: a throwaway server on a spare port seeded a KNOWN session id into
its own in-process table, and the browser was given that. The real token was
never handled. If you need to verify an authenticated screen, do that rather
than teaching yourself to read the secret.

97. **Redaction stopped destroying the transcript.** At the maintainer's
    direction: "I'd rather it layer on top of the transcription data rather
    than replace it." It used to `UPDATE utterances SET text = replace(...)`,
    so the address was gone from the archive and `redaction.before_text` was
    the only copy of what had been said.

    Now `utterances` carries two columns:

        text_raw   what the recogniser produced. Written once, by
                   db.index_video, and by nothing else ever.
        text       what the archive PUBLISHES: text_raw with the applied spans
                   replaced. A pure function of the two, recomputed by
                   redact.republish().

    **The first design attempt was wrong and the reason is worth keeping.** The
    obvious move is a view: keep `utterances.text` pristine and redact at read
    time. It cannot work, because `utterances.tsv` is `GENERATED ALWAYS AS
    (to_tsvector('english', text))` with a GIN index over it, and passages carry
    their own copy of the words plus BM25 postings and a 1024-dimension
    embedding. **You cannot overlay an index.** A vector computed from a string
    containing an address encodes it for ever, and no read-time filter reaches
    back into it. So the published value has to be a real column that
    everything derives from, and the raw one is simply never indexed and never
    read by a reader-facing query.

    A view would also have been fail-OPEN: ~35 places read utterance text, and
    missing one leaks an address silently. Two columns where the SHORT name is
    the safe one means a forgotten reader inherits the redacted value. That
    property is why the destructive design was defensible in the first place,
    and it survives the change.

    Three things got better on the way:

    - **Revert no longer trusts `before_text`.** It recomputes from `text_raw`,
      so the original is recovered rather than restored from a copy taken at
      apply time. `before_text` is now a record of what a decision changed.
    - **Redactions compose.** Two addresses on one line used to need applying
      and reverting in a set order to land on the right text. The answer no
      longer depends on order.
    - **A re-transcribe resets both columns**, which is correct - it is new ASR
      - and `redaction.gone_from_transcript` is what catches an applied
      redaction that a re-transcribe undid.

    **Two new invariants, and both were proved RED in the sandbox before being
    trusted green** (gotcha 81's rule, and gotcha 80's about where to prove it):

        redaction.raw_preserved           rewrite text_raw under an applied
                                          redaction -> FAIL, 1 violation
        utterances.published_is_derived   edit `text` on an unredacted line, or
                                          NULL its text_raw -> FAIL, 2

    The second is stated over the ~295,000 lines with NO applied redaction,
    where the columns must be identical - those are the rows where a stray
    write would hide, and the three older checks cover the redacted ones. 47
    checks now, 0 failing on production, which was never touched by the proof.

98. **What makes an `/ask` answer slow is GENERATION, not prompt size.** Half a
    day went into this from the wrong end, so the measurements are here to stop
    the next session repeating it. One question, instrumented end to end
    (`spend` in `web/agent.py`, printed by `bin/eval_agent.py`):

        think    196s  37%   14 rounds   prompt 411,746 tok (92% CACHED)
        compose  143s  27%    1 call     prompt  39,365 tok ( 5% cached)
        verify   187s  35%    1 call     prompt  40,818 tok ( 1% cached)
        tools      6s   1%   31 calls
        --------------------------------------------------------------
        completion 59,730 tok, of which 55,637 REASONING (93%)

    The research loop re-sends its whole history every round and that is nearly
    free: DeepSeek's prefix cache hits 92% of it. Shrinking the brief, trimming
    what `render()` returns, capping `MAX_EVIDENCE` - the obvious ideas - all
    chase the 8%. What costs wall clock is tokens the model GENERATES, and on a
    reasoning model almost all of them are reasoning nobody reads.

    So the dial that matters is `reasoning_effort`, which `bin/ask.py` had never
    sent. It does now (`effort=`), per phase, via `EFFORT_RESEARCH` /
    `EFFORT_COMPOSE` / `EFFORT_VERIFY`.

    **Turning it down is NOT free, and the failures are the dangerous kind.** At
    `compose=low` the writer invented "authorized by state law", named a camera
    vendor the archive never mentions, and wrote that the closest thing to the
    word "gambling" was the surname Gamble *while citing a passage containing
    "gambling boat"*. Research and compose are left at the model's own default
    for that reason. Only `verify` is dialled down, to `medium`: 187s to 20-45s
    at the same failing-check count.

    Two more things measured on the way, both counter-intuitive:

    - **The writer's thinking is mostly a self-audit, and it cannot be talked
      out of it.** Its reasoning trace drafts the answer, then enumerates every
      citation, confirms each id is in the brief, re-reads each passage against
      its claim, and closes "I'm comfortable. Write it out." - 46,000 of 74,000
      characters, having found nothing. Three separate attempts to stop it
      (deleting the self-check instruction, handing it a fact-to-id map from
      the researcher, telling it outright that a checker runs afterwards) all
      failed; one of them DOUBLED the reasoning. Do not spend another day here
      without a new idea.
    - **It is not the model.** Opus, given the identical brief and the identical
      COMPOSE prompt, wrote a longer answer and made ONE MORE unsupported-claim
      error than `deepseek-v4-flash` did - and made the same kind, attaching a
      claim to a passage that only introduces the speaker.

    **Read every eval difference against the noise.** The same question under
    the same configuration has produced 0 to 3 flagged claims, and compose
    reasoning on it has ranged 2,344 to 32,531 tokens. Nothing under a ~2-check
    difference means anything at n=1. Two conclusions in this session were
    announced off the first question to report and both were wrong.

    Every run of the three `ANSWERS` questions this session, so the spread is
    on the record rather than re-derived. The eval prints per-question seconds
    and words; these are the sums:

        run     total   words   checks
        eval2    854s    1033      3     before any of this
        eval4    509s    1008      3
        eval5    695s    1113      2
        eval6    512s    1051      1
        eval7    451s    1142      2
        eval8    452s    1065      2
        evalA    589s    1074      1     the configuration that shipped
        evalB    646s    1053      1
        evalD    829s    1250      0*    * judge unfit, structural checks only
        evalE    682s    1225      1
        ---- the fourth question (backyard chickens) joins here ----
        evalF    822s    1369      2     evidence still truncated (gotcha 101)
        evalG   1173s    1656      3†    † printed 4; one was the harness's
                                           own fault, see below

    From evalF the eval runs FOUR questions, so its totals are not comparable
    with the three-question runs above. `took` also stops being what a reader
    waits for at evalF, because the citation verify pass runs in the harness
    and not on the reader's path any more (agent.VERIFY_ON); the eval prints
    both numbers per question.

    evalG is the first run whose judge could see whole passages, and it is
    the honest baseline. Its three failures are all real and all the same
    class - a specific asserted onto a passage that does not carry it:
    "Red Speed proposed the citation-enforcement program" on a passage that
    never names Red Speed; "buffering and building-facade standards" on three
    passages carrying a berm, lighting and a warehouse cap; and the backyard
    chickens answer never reaching [item:21923]. The fourth printed failure
    was `moments_any` listing three of the five passages that carry the
    permit removal, so an answer citing one of the other two - staff
    confirming it at the adoption hearing, which is the best evidence there
    is - failed a check the judge passed in the same run. Widened; the rule
    for that field is EVERY way of saying the thing.

    Wall clock at n=1 is not a measurement here: 451s to 854s over runs whose
    checks range 0 to 3, with compose alone swinging 138s to 308s WITHIN one
    run. Only the check count is worth reading, and only across runs.

    The 3-to-1 is NOT from the effort dial. It is from `COMPOSE` no longer
    contradicting itself — it used to say "cite the clearest one" beside "the
    citation must be the one that holds the fact" (unsatisfiable for any
    sentence resting on two passages), and it demanded plain-English
    explanation of procedure beside "NOTHING FROM OUTSIDE THE BRIEF" while
    giving explanation as its own example of good work. The model spent its
    reasoning arbitrating, and the fabrications were where it lost. Both
    survivors are now marginal — "others still worried" where the worry was
    the same neighbour's, "Starkey seconding" where the transcript says "nice
    second" — not invention.

    The one failure class that outlived every fix, including Opus, is a
    specific asserted onto a passage that does not carry it. `verify_citations`
    cannot reach it: the bracket is right and the sentence has a word too many.
    Prompting has been tried from four directions. It likely needs a different
    mechanism.

    **VALIDATED (runs D and E).** Everything below was added after run B and
    is now measured: run E, judge fit, came in at **1 failing check** — the
    same as A and B — and run D at 0, though D's judge went unfit so only its
    structural checks count. The three changes hold the line, and one of them
    is confirmed on the exact case it was built for: the Hazelwood passages
    [248433] [248434] now arrive as one person in one sentence, where the
    earlier answer had "one neighbour praised the plan while others worried
    about lighting" and there were no others.

    The one surviving failure in E is the known class and not a new one:
    "continued several times over about ten months" against continuances
    spanning November 2024 to May 2025. A computed specific that no passage
    carries — see the last paragraph of this entry.

    **The cost is words.** D and E wrote 1,250 and 1,225 words against A and
    B's 1,074 and 1,053, about 16% more, consistently across both runs, and
    the wall clock follows the words. The likely cause is the absence
    reporting: an answer now spends a sentence on what was searched for and
    not found, which is the point of it. Worth watching against `LENGTH` in
    COMPOSE, which asks for three or four short paragraphs and got five.

    Sorting the failures by cause rather than treating them as one bucket found
    three, and the last of them is the live thread:

    1. *Absence claims.* "No tally was read into the microphone" arrived citing
       the roll call - the one passage that contradicts it - because COMPOSE
       demanded a citation for EVERY claim and no passage can prove an absence.
       At the maintainer's suggestion: the support for an absence is the SEARCH
       that came back empty. The brief now has two sections where it had one,
       because `empty` used to mean "returned nothing NEW" and so listed a
       search that matched ten already-held passages as evidence that nothing
       exists. `trace` carries `found` beside `new` for that.
    2. *Anaphora.* [248469] "we're happy to work on a condition ... on any
       perimeter areas adjacent on the west or north" IS the applicant agreeing
       to a lighting condition; the word is three turns up. Passages were
       listed in search order, which scatters a conversation. `_said()` now
       groups by meeting and sorts by spoken time.
    3. *One speaker read as several.* [248433] and [248434] are both Nancy
       Hazelwood, consecutive, one trip to the podium - and produced "one
       neighbour praised the plan while others worried about lighting". There
       were no others. `_said()` marks continuations from `speaker` plus
       adjacent `start_idx`/`end_idx`.

    Gotcha for whoever writes that SQL: **a literal per-cent sign anywhere in a
    psycopg query string is read as a placeholder, including inside an SQL
    comment.** "(19%)" in a comment took down every passage lookup.

99. **How sure a speaker's name is, said the same way on every surface.**
    `utterance_speaker` has carried `human`, `basis`, `confidence` and
    `contested` all along and nothing downstream said so: of 234,000 named
    utterances, 2,786 were stated by a person, 208,495 were matched to a voice
    at that meeting, and 22,682 carry nothing but the name their voice goes by
    across the whole archive - and every surface printed all three identically.
    The one that matters most is the answer text: "Commissioner Oakley moved",
    written off a name nobody confirmed, invents a person's vote.

    The rule is `ui/components/SpeakerChip.tsx`'s, and it is one rule now:
    confirmed / inferred / weak(`basis='cluster'`) / unknown, off `human` and
    `basis`, no number. It was first written in `web/agent.py` as
    `confidence >= 0.6`, which is that same precedence rule stated a second
    time - the defect `utterance_speaker`'s own header warns about - and R5.5.6
    forbids showing the number to a reader anyway, on the grounds that speaker
    precision has not been re-measured since the roster work. So the threshold
    went and the chip's states came in.

    Three things had to move for that to be true rather than merely intended:

    - **The reduction is in the database** (`passage_speaker`, bin/schema.sql).
      A passage is many utterances and every caller wants the same reduction:
      the WORST of them. Both obvious ways of writing it in a query were wrong
      and quiet about it. `BOOL_OR(human)` beside `MIN(basis)` reads the two
      fields off DIFFERENT utterances, so one passage came back saying both "a
      person confirmed this" and "archive-wide guess". And `MIN(basis)` is
      alphabetical, which is not strength: it puts `cluster` first by luck and
      then `human` ahead of `voice`, backwards. `ORDER BY <rank> LIMIT 1`
      returns one real row, so the fields cannot disagree.
    - **It is filled in on the hits that SURVIVE, not on the candidates**
      (`tools.speaker_sure`). Resolving a name walks four precedence levels per
      utterance and Postgres runs the archive-wide cluster majority per row:
      **620 ms for 600 passages against 2 ms without it**. Both retrieval arms
      rank 600 and return 25, so putting it in the projection - which is where
      it started - tripled the cost of every search to describe 575 rows nobody
      would see. On the survivors it is 16 ms. `bin/retrieve.py` carries a
      comment saying not to move it back.
    - **Four surfaces that drew a bare name now draw the chip**: `/search`
      hits, the answer's evidence, a saved answer's evidence, and the front
      page's "objected" and "split vote" rows - which is the one that most
      needed it, being a named member attached to the two claims a person is
      least willing to have wrong about them, in bold, above the fold.

    Two behaviour changes worth knowing, both deliberate:

    - **Fewer passages are marked than under the threshold** - 11.7% of
      personally-named passages against 27.3% - because a voice match is one
      claim here whatever it scored, and 12.4% of them score under 0.6. That is
      R5.5.6 applied, not a gap. If the precision measurement is ever redone,
      spend it on the chip and this follows.
    - **A passage with no speaker is no longer "Several speakers" on /search.**
      Both used to come out that way; the archive knows there were several for
      one of them and nothing at all for the other, which is 10.7% of passages.
      It now reads "Unidentified speaker", which is what /ask has always called
      it.

    The agent marks only the two ends - `⚠ NAME NOT CONFIRMED` and
    `✓ NAME CONFIRMED` - and leaves `inferred` bare, because inferred is 89% of
    the archive and marking it would put a warning on nearly every line of the
    brief. COMPOSE says in as many words that an unmarked name is an inferred
    one, and what to do with each of the three.

100. **`do_GET` leaked a database connection on every read, and the whole
    archive ran out.** Found because an eval died on `FATAL: sorry, too many
    clients already`, which is a message about the server and says nothing
    about who took them.

    `pg_stat_activity` said everything: 91 backends `idle`, all from one
    container address, opened about one every 25 seconds across 40 minutes and
    never released, 100 of 100 taken. Every one carried the same last query -
    `retrieve.search`'s passage projection - because `/api/find` is a GET and
    searching is what a reader does most.

    `web/server.py` opened a connection in `do_GET` and had no `finally` to
    close it. The other two request paths in the same file already did:
    `_ask_stream` and the admin branch of `do_POST`. Measured before and after
    the fix, counting this host's backends:

        old code   5 requests to /api/find    2 -> 4
        fixed     36 GETs across 3 endpoints  2 -> 2

    Two things made it hard to see and are the reason this is written down.

    **The failure is not local.** A missed close never breaks the request that
    missed it. It breaks a different request, on a different endpoint, minutes
    later, once the server as a whole runs out - and at that moment the path
    that is actually leaking is the one that still looks fine.

    **Refcounting is not the backstop it looks like.** A psycopg connection and
    its cursors reference each other, so a dropped connection is a cycle: it
    goes when the cyclic collector runs, not when the frame does. That is why
    5 requests leaked 2 rather than 5, why the production creep was slow enough
    to look like something else, and why "CPython closes it when it goes out of
    scope" is not a thing to rely on here.

    The 91 already open belong to the running container and will go when it is
    redeployed. If this is ever wanted as a backstop rather than a fix,
    `idle_session_timeout` is the setting - but scope it to the web read path.
    Set globally in `db.connect()` it would reach the pipeline workers, which
    legitimately hold an idle connection while a GPU is busy.

101. **The checker was reading a quarter of the passage, and failing honest
    work for it.** The new backyard-chickens eval question found this on its
    first run, which is the best argument for having written it.

    The judge returned three "unsupported claim" findings. All three were
    right about the evidence it was shown and wrong about the answer:

        "prohibits chickens in MF-1, MF-2, MF-3"   cited [307332],   986 chars
            ...which says "excluding ER, ER two, MF1, MF two, and MF three"
            at character 500.
        "coop size and height are regulated"       cited [307333], 1,165 chars
        "must be occupied, not a vacation rental"  cited [307333]
            ...which says both, at characters 850 and 1,000.

    `_passage_line`'s default width is **420** characters, chosen for a list
    being SKIMMED, and **42,006 of 166,998 passages (25.2%) are longer than
    that.** Four callers took the default: the research render, where it is
    right; `brief()`, where it means the writer composes from a truncated
    source; `_groups`, the citation checker; and `eval_agent.evidence_text`,
    the judge.

    The last one is the sharp part. `evidence_text`'s own docstring records
    this exact failure, from the first time it happened, and says "there is one
    renderer, it is the agent's, and this cannot drift from it again". The fix
    then routed the judge through `_passage_line` — and stopped one level short
    of the NUMBER inside it. Same defect, same symptom, one layer down, four
    months later. `_groups` had the same shape: it clipped to 900, which never
    once mattered, because 420 had already happened.

    **It also explains `verify_citations`.** Stage one flags a citation whose
    text does not carry its sentence; stage two, reading the same clipped text,
    finds nothing better and declines. Five flags and no moves over 32
    questions looked like a pass with nothing to say. It was a pass reading a
    quarter of a passage, and at least one of those five flags — [307333] — is
    independently confirmed by the judge to have been the truncation and not
    the citation. **The decision to gate it off (gotcha 100 is a different
    matter; this is `agent.VERIFY_ON`) was taken on that evidence and should be
    revisited now that both stages can see.**

    Fixed by `agent.FULL = 1600`, which is past the longest passage in the
    archive (1,582), used by the brief, the checker and the judge. It costs
    about 2,400 tokens on a 130-passage brief, all of it prompt, which prefix
    caching makes close to free — generation is the bill here, and this is not
    generation.

    **Every failing-check count in gotcha 98's table before evalG predates
    this**, so the "1 failing check" that A, B and E landed on is not a floor
    of real defects. Some unknown share of it was the clip.

    **What the first clean run said.** evalG, the same four questions with
    nothing truncated:

    - The three false findings on the chickens answer are gone, and that
      answer now cites [307333] for the occupancy rule and the coop
      dimensions - the facts it was marked wrong for - and passes.
    - It also got more careful in a way nothing asked it to be, which is what
      seeing whole passages buys: "Staff presented these rules as key points
      of the proposed measure, not its full text", and "The four-hen limit,
      for instance, comes from Chairman Starkey's remark at the first
      hearing." Both are the answer telling a reader how good its own
      evidence is.
    - `verify_citations` moved nothing and flagged nothing, on all four
      questions, with clean input - including on the two answers where the
      judge found a real misattached specific. That is not a failure of the
      pass; VERIFY's prompt says to name a citation only when it carries NO
      part of its sentence, and [69083] does carry part of its sentence. The
      vendor name hung on it is the problem, and the pass is told to ignore
      exactly that. **So gating it off (agent.VERIFY_ON) stands, and now for
      a reason that is measured rather than inferred: it cannot reach the
      class that is actually failing, by design.**

102. **Three things the speaker work left open, and where they went.** Recorded
    here because `HANDOFF.md` carried them and `HANDOFF.md` is temporary.

    - `_said()`'s continuation marker prints an unconfirmed name a second
      time without the ⚠ its head line carries. One line in `_said()`; not
      done mid-baseline, still not done.
    - `RESEARCH` has no rule telling the researcher that a named companion
      ordinance, case or code section is a thing to go and look up. That is
      why the backyard-chickens answer describes rules from a staff summary
      and reaches [item:21923] in only one run of three.
    - `calibrate()` gates a whole eval run's judged checks on ONE sample per
      fixture, from a model that is not deterministic. evalD lost its judge to
      a single miss; probed straight afterwards the same judge passed that
      fixture 3 times out of 3. Best-of-three would cost almost nothing.

103. **An address split across two utterances is invisible to every redaction
    detector, and the passage renderer puts it back together.** `redaction` is
    keyed `(video_id, idx, span)`: a span belongs to ONE line, and there is no
    way to write down one that crosses a boundary. Every detector and every
    check evaluates `position(span in u.text)` a row at a time, so a span in
    neither row is in nothing.

    Found in production on 2026-08-15. A man gives his address twice in
    `OiEdE83k8HA`. The first, at idx 157, was redacted in August and is gone.
    The second falls across the 158/159 boundary — idx 158 ends `located at
    14720`, idx 159 opens `Bluestone Lane in Odessa, Florida` — and
    `redaction.gone_from_transcript` passes, because it is true of both lines
    separately. `index_passages` joins the utterances of a passage with a
    space, which reconstitutes `14720 Bluestone Lane` exactly, in `p.text` and
    `p.search_text`: the columns search ranks on and `/ask` quotes from.

    **Only `redaction.gone_from_index` can see it**, and only since it started
    counting occurrences instead of looking for one — the passage held one copy
    where the transcript it is built from held none. That check is now the
    standing detector for this whole class, after the fact.

    Proposed as two half-spans (redactions 3442/3443, `author='audit.py'`),
    which is the only shape the schema allows; the note on both says apply
    both or neither. **The proposer itself is unfixed.** `redact.py`'s
    candidates come from per-utterance text, so it cannot propose what it
    cannot see, and nothing knows how many more of these there are. The fix is
    to scan the joined passage text; deferred at the maintainer's direction on
    2026-08-15 to get the speaker work deployed first.

    **Two other things fell out of the same review**, both of them the reason
    nobody had read these checks in months. `gone_from_index` asked whether the
    span appeared anywhere in a passage and `unfindable` asked whether it
    appeared anywhere in the recording — 6 and 84 violations, essentially all
    noise, because redaction spans are ordinary English. `Wesley Chapel` is a
    town of 65,000; the span `A` occurs in 273 other lines of its own meeting.
    Scoped properly they read 1 and 2, and one of the survivors was a line
    that read out four residents' addresses where the detector removed three
    (redaction 2549; the fourth proposed as 3444). A privacy check nobody
    reads is worth less than no check, because it is believed to be working.

    `redaction.span_is_plausible` is new and asks the opposite question: five
    applied `residence` redactions cut the words `one`, `two`, `A` and `L` out
    of the published record. Over-redaction had no check at all.

104. **An answer's citations were furniture in the way of its sentences, and
    its timestamps could not say which recording they were on.** Both showed
    up the same way: the maintainer read an answer as a PDF on 2026-08-18.

    `COMPOSE` tells the writer to cite every passage a sentence rests on and
    not the clearest one, which is right — a reader clicks a citation and gets
    that passage alone. Drawn one chip each it produced `▸ 1:55:11 ▸ 1:56:51
    ▸ 1:57:52 ▸ 5:41`, four things to look at for one supported sentence.
    **Four redesigns of the chip all failed the same way**, and they are worth
    listing because each looked like the answer at the time: fold the run into
    one pill (still a row of clocks); name the recording in it (`▸ Aug 11,
    2026 morning 53:54`); an item with no agenda code printed the meeting date
    instead, so a paragraph about one meeting carried that date three times in
    four lines, twice as a link; set the item's own title in the chip and the
    token just got bigger. The question was never what the chip should say. It
    was why a chip is in the sentence at all — everything it could carry is
    already on the page under two headings, with the recording, the speaker,
    the time, the quote and the disposition.

    **So a citation is a number** (R5.5.2a). `[4]` in the prose, the same `4`
    on its row below, numbered in the order a reader meets them, and a repeat
    citation reuses its number — which is what makes a repeated date
    impossible rather than merely fixed.

    The two kinds are told apart by a MARK and not by colour: `[▸4]` plays a
    recording, `[1]` reveals the published record. Colour was the first
    version and it is the one signal that does not survive being printed —
    which is where the answer that started this was read — or high contrast,
    or a reader who does not distinguish the record's blue from the player's
    orange. `ProvenanceMark` has said this about itself since slice 2 and the
    citations were not obeying it. The mark is not repeated on the row the
    number resolves to: the heading above it has already said which kind it
    is, and the row's own play button carries a ▸ two words later.

    **One number per PLACE in the recording, not per passage.** A passage is
    about a minute of one speaker, so a sentence drawing on two minutes of
    somebody talking cites two of them. Cited passages less than **15 seconds**
    apart fold to one, the first of them. The test is the SILENCE, not the
    passage boundary — an index-adjacency test on `(start_idx, end_idx)` was
    written first and is worse, because two passages of one person with a
    short interjection between them are not index-adjacent and are plainly one
    place to go. **The threshold is measured**: across the 28 passages that
    answer cited, the gaps are 0 seconds or 39 seconds and up with a single
    8-second case, so 15 sits in an empty band and nothing hinges on 14
    against 16. A fold can span a change of speaker, which is correct at that
    distance and would be a quiet misattribution if the reference kept naming
    only whoever starts the stretch — so it names everyone in it (R2.3).

    **The timestamps could not say which recording they were on.** Half of all
    meeting-days are two recordings on one continuous agenda, so `1:57:52` and
    `5:41` were the morning and the afternoon of the same meeting with nothing
    to tell them apart, and one paragraph cited seven recordings across five
    years as bare clocks. `tools.PASSAGE_HIT` and the `get_item` projection in
    `agent.py` now carry `session_seq` AND a count of the meeting's recordings,
    because neither means anything alone: `session_seq` is null on 48 videos
    that share a meeting with another and 0 on three that do not. The evidence
    rows say it — `▸ afternoon 5:41` — since that is where a number resolves.

    **Two bugs fell out of looking.** `meetingDate("")` reached
    `Date.UTC(0, 0, 1)` and printed **"Monday, January 1, 1900"** as the
    evidence heading for the 17 recordings that have no date, which is a date
    this archive invented; it returns "" now and the heading falls back to the
    recording's own title. And a folded moment put the same `id="ref-N"` on
    every row it covered — duplicate ids, so a marker landed on whichever the
    document reached first.

    Three things worth keeping from the fix. `Refs` worked out its running
    state (which recording was last, then what number comes next) **while
    rendering**, which React's lint caught: development renders twice, and the
    second pass reads what the first wrote. It is resolved as data now, over
    the whole answer, before anything is drawn. The new `.at` class for a time
    inside a reference collided with the evidence list's own `.at` in the same
    CSS module — same file, later in it, so it silently took the size and
    colour of every time on the page; `.citeNamed` sprang the identical trap
    an hour later. **CSS modules scope per FILE, not per component.**

    **THE NUMBERING SHIPPED WRONG, and the check that passed it was looking at
    the wrong thing.** Reported from production hours later: "why are there
    multiple 9s, and I don't see 4 in the list at all". Three faults, and the
    first is the one to remember.

    A folded moment printed its number on EVERY row it covered — `[16]` was
    Sean Poole's three consecutive passages, so 16 appeared three times, and 10
    of the 39 references were duplicated that way: 50 numbers printed for 39
    references. The verification had asked whether every `#ref-N` anchor
    existed and whether the ids were unique. They were — only one row per
    moment carries the anchor. **The numbers a reader SEES are printed by rows
    that mostly have no id at all**, so the check was blind to the only thing
    that was wrong. Check what is rendered, not what is addressable. The rows a
    reference covers now print a blank gutter and only the row bearing the
    number prints it.

    Second, a latent overwrite. Folding is per SENTENCE — it has to be, because
    a sentence's citations are what say which stretch it rests on — so the same
    passage can be folded here and standalone there, and reuse keyed on the
    fold's first passage alone let a later, longer fold mint a fresh number and
    overwrite what its members already held: a number in the prose with no row
    showing it, and a row carrying somebody else's. It never fired on the
    answer this was built against. A passage now keeps the first number it was
    given, and a moment takes the number of any passage in it that has one.
    **Folding globally instead was tried and is worse in a way that matters
    more than either bug** — over the whole answer the 15-second rule merged
    30:37 through 33:38, three and a half minutes of one speaker, into one
    reference, so a sentence about its last claim would have dropped the reader
    three minutes before the claim. A place in a recording is a fact about the
    recording; which stretch a SENTENCE rests on is not.

    Third, `#ref-N` was put on the number's own `<span>`, inside the row. So
    `.recRow:target` — the highlight that shows a reader which row they were
    just sent to — could not match anything, and had silently stopped working.
    The anchor is the row now, on both lists.

    Verified after: 39 references, 1–39, no gaps, each printed exactly once,
    every anchor a row, gutters aligned at 320px.

    **Left open.** The code-less items are `source = 'transcript'` — stretches
    this archive cut out of the recording, which `RecordView` elsewhere
    separates from the agenda under the words "it is **not** the county's
    record and no part of it is authoritative". On an answer they sit inside
    "What the county published" and are numbered alongside `C10`. That is the
    §2 blur, in the one place a reader is most likely to quote from. Raised
    2026-08-18 and deferred at the maintainer's direction.

105. **The reading API is ASGI now, because MCP could not be bolted to a
    `BaseHTTPRequestHandler`.** The archive serves its five tools over MCP at
    `/mcp` (`web/mcp_server.py`), so a reader with an MCP client can ask the
    archive its own questions without going through the paid `/api/ask`. The
    official SDK's streamable-HTTP transport is an ASGI app, and the server it
    had to live inside was a threaded `BaseHTTPRequestHandler`. The two do not
    compose.

    **The rejected shape was a proxy.** Run the SDK under uvicorn on a second
    loopback port, forward `/mcp` to it from the old handler. It is about
    thirty lines and it works. What it buys is a second listener, a hop, and a
    permanent answer of "the two halves of this server are different servers"
    to every future question about middleware, limits or logging. The
    maintainer's call on 2026-08-18 was to migrate instead.

    `web/server.py` is Starlette on uvicorn: two apps, two listeners, one
    process, one event loop. **Endpoints stayed `def` rather than `async
    def`**, which is what kept the migration to one file. Starlette runs a
    sync endpoint on a worker thread, so psycopg keeps blocking exactly as it
    did and not one query was rewritten. Handler coupling outside the
    transport was three functions (`limits.client_ip`, `admin.loopback`,
    `admin.session_of`), all reading `.client_address` and `.headers`; they
    take a Starlette `Request` now.

    **What had to be carried across by hand, and why each one.** The gzip in
    `_json` is still hand-rolled: `GZipMiddleware` compresses whatever it is
    handed, and a gzip encoder buffers until it has a block, which is gotcha
    61 exactly. A middleware would have reintroduced the bug the SSE headers
    exist to prevent. The 2 KB comment pad, `no-transform` and
    `X-Accel-Buffering: no` all stayed. `Connection: close` did NOT, and half
    of gotcha 61 is retired with it: uvicorn speaks HTTP/1.1 and frames a
    streaming body as chunked, so there is no longer a reason to give up the
    connection to terminate a response.

    **Two things got better rather than merely surviving.** The heartbeat is
    no longer a second thread writing the same socket under a lock. The run
    produces into a queue, the generator drains it, and a heartbeat is what
    the generator emits when the queue has said nothing for `HEARTBEAT`
    seconds. One writer, so the interleaving hazard the lock guarded is gone
    rather than guarded. And a reader who navigates away now STOPS THE PAID
    RUN: `on_event` raises once the generator is closed, which unwinds
    `agent.ask` from inside whatever call it is in. The old handler got that
    by accident, from `BrokenPipeError` on the socket write, and a queue would
    have silently lost it.

    **The admin boundary is structural now.** Curation routes are registered
    on the curation app and nowhere else, so `/api/admin/*` on the public
    listener is not refused by a check that could be got wrong, it is a path
    that does not exist. The loopback test stays as depth.

    **Two behaviour changes worth knowing.** `/api/item/abc` is a 404 rather
    than the 500 it used to be, because the route matches `{item_id:int}`
    instead of `int()`-ing whatever followed the last slash. And repeated
    query parameters take the FIRST value, which needed saying out loud:
    `parse_qs(...)[k][0]` took the first for years and Starlette's mapping
    takes the last, so `?q=a&q=b` would have quietly started searching for
    something else.

    **Found on the way.** `limits.TRUST_PROXY` parsed as `env_value or ("" in
    (...))` because `in` binds tighter than `or`, so it held the raw string
    and `"0"` and `"false"` were both truthy: the flag could not be turned
    off, and `deploy/unraid-app.md` documents `ASK_TRUST_PROXY=0` as the
    setting that disables it. The loopback guard kept it from being remotely
    exploitable. Fixed.

    **The MCP surface itself.** `tools/list` is projected off `tools.MANIFEST`
    rather than re-declared, so a tool whose arguments change in
    `web/tools.py` changes at `/mcp` in the same edit. Three prompts import
    `agent.SOURCES` for the same reason. It is stateless and answers in JSON
    rather than SSE, which means a tool call is one request and one response
    and there is no stream for a proxy to buffer. Its budget is `MCP_*` in
    `web/limits.py` and deliberately NOT `ASK_*`: a tool call spends CPU and
    the query encoder rather than tokens, and an MCP client must not be able
    to close the endpoint a reader pays for. `search_transcript` has its own
    lower ceiling, because it is the expensive tool and the one worth pulling
    in bulk.

    **Two traps in mounting it, both of which answered wrongly rather than
    loudly.** A `Mount("/mcp", ...)` treats the path as a prefix and 307s it
    to `/mcp/` before the app sees anything, and an MCP client POSTs to the
    exact address it was given, so the handshake became a redirect. It is a
    `Route` holding the ASGI app now, which is what the SDK does itself. And
    the gate that refuses GET (below) was first written as a closure: Starlette's
    `Route` decides what it was handed with `inspect.isfunction`, wrapped the
    plain function as a request handler, and the endpoint answered 405 to POST
    and 500 to GET. It is a class with `__call__` now.

    **GET is refused, 405.** A GET asks the transport to open a standalone SSE
    stream and hold it for server-initiated messages, and this server initiates
    nothing. Measured: `curl http://.../mcp` never returned. On a public
    endpoint that is a way to pin a connection for free, and the tool-call
    budget does not cover it, because that meters work and this costs a socket.

    Verified against the live archive: every reading endpoint, the admin
    boundary in both directions, one real `/api/ask` run (38 stage events, 12
    heartbeats, an answer saved and readable at `/api/answer/<id>`), a stream
    requested WITH `Accept-Encoding: gzip` arriving uncompressed and chunked,
    both throttles refusing at their limit without the refused call being
    charged to the other window, and a full MCP session through the SDK's own
    client over the PUBLIC origin - the Next rewrite included, which is the
    path a reader's assistant actually takes to the address printed on
    /about.

106. **The whole site rendered in Times New Roman, and had been.** Found while
    checking that the new endpoint block on /about was set in mono. It was
    not, and neither was anything else: `getComputedStyle(document.body)
    .fontFamily` came back `"Times New Roman"` on every page, in both themes.

    `next/font` declares `--font-sans`, `--font-serif` and `--font-mono-face`
    on whichever element carries the classes it returns, and `app/layout.tsx`
    put them on `<body>`. `app/tokens.css` builds `--font-ui`, `--font-record`
    and `--font-mono` out of those three ON `:root`. A custom property is
    substituted at computed-value time per element, so on `:root` the three
    tokens referenced variables that did not exist there. That does not fall
    back to the next font in the list. It makes the declaration INVALID, the
    token computes to the empty string, and every descendant inherits the
    empty string - so `font-family: var(--font-record)` resolved to nothing
    and the browser used its default serif.

    **It was invisible because the colour tokens beside it were fine.** They
    are declared in the same `:root` block and depend on nothing outside it,
    so the palette, the two registers, the rules and the shadows all worked.
    The page looked designed. It was simply set in the wrong typeface
    everywhere at once, which is the hardest kind of wrong to see: there is no
    correct version on screen to compare it against.

    The classes go on `<html>` now. Verified after: headings in Source Serif
    4, body in Inter, the endpoint block in JetBrains Mono.

## Postgres, and what to watch out for

Migrated off SQLite in one cutover. `bin/migrate_to_pg.py` did it, verifying
every row count and proving the 62,779 embeddings transferred with max absolute
drift of exactly 0. **The script is deleted** (2026-08-13) along with the stores
it read: a one-shot migration that has run, whose source is gone, cannot be
re-run by accident against an archive that is now three times the size.

The parts that needed judgement rather than translation:

- **`db.Row`** answers to both `r[0]` and `r["col"]`, the way `sqlite3.Row`
  did. psycopg's `dict_row` would have forced hundreds of unrelated edits.
  It is a Mapping, so it iterates KEYS — see gotcha 13.
- **`claim()` is now one statement**: `UPDATE ... WHERE id = (SELECT ...
  FOR UPDATE SKIP LOCKED)`. SQLite needed `BEGIN IMMEDIATE` and workers
  serialised behind the write lock; they now step over each other's rows.
- **`random_page_cost` is set to 1.1 per connection** in `db.connect()`. At
  the cluster default of 4.0 the planner priced the HNSW index above a
  parallel sequential scan and *never used it* — 128 ms per search instead of
  10, with nothing in the results to show for it. Verify with EXPLAIN, not
  with timings: an exact scan and an unused index look identical.
- **`hnsw.ef_search` is set via `set_config(..., is_local => false)`.**
  `true` scopes it to the transaction, which an autocommit connection ends
  immediately, silently dropping it back to 40.
- The research tab ranks with `ts_rank_cd`, not BM25. It is a browse-by-phrase
  UI; `websearch_to_tsquery` also accepts what users type and never raises, so
  the FTS5 escape-and-retry dance is gone.

Measured after the move, against an exact scan over 65k passages (the
recall/latency tradeoff is a property of the index, not of that corpus size):

| | recall@10 | recall@40 | latency |
|---|---|---|---|
| HNSW, ef_search=500 | 97.1% | 97.5% | 9 ms |
| HNSW, ef_search=1000 (in use) | 97.1% | 98.6% | 19 ms |
| exact | 100% | 100% | 84 ms |

## Current state (handoff)

**Ingest is COMPLETE and the archive is fully rebuilt.** 432/432 meetings
transcribed, 0 errors, 1,036 hours. A crash took down the fleet, the refresh
chain and the web server at once (gotcha 29); restarting it surfaced the
segmentation write bug, and fixing that invalidated every derived layer, so
everything below was rebuilt from `segment --redo`.

**The table below is a snapshot of THAT night (2026-08-12), kept because it is
the record of what the rebuild changed. It is not the live archive.** Two rows
have moved since and both are marked. For current figures see "Honest limits",
which was re-measured on 2026-08-13.

| | before that night | after it | live, 2026-08-13 |
|---|---|---|---|
| transcribed | 337/432 | **432/432**, 0 errors | unchanged |
| utterances | 243,225 | 298,737 | unchanged |
| clustered | 97% (of a smaller set) | **100%** | unchanged |
| named | 84.9% *(dishonest — see below)* | **78.8%** (anchored to the published chair roster) | **68.3%** — `name_speakers` has not run since; see the note below the slice-6 section |
| cross-body misattributions | **54,000** | **0** | unchanged |
| segments | 2,142 over 163 videos *(all stale)* | 5,756 over 430 | unchanged |
| item_spans | 2,142 | 5,587 | unchanged |
| passages | 65,592 | 167,083 (96.3% bound to an agenda item) | 167,174 |
| audit | 18 checks, 2 vacuous | **35 checks, 0 failing** | **45 checks, 0 failing** |

The naming number is not comparable to 84.9%: that figure included 10,715
utterances showing County Commissioners at Planning Commission meetings, and a
commissioner name that was, for the largest clusters, the wrong commissioner.
78.8% is what the published rosters, the chair anchor AND the paid naming pass
support together. The live 68.3% is the same method with the paid pass missing,
not a regression in the other three.

Only **2 of 432** videos are unsegmented, and both were *rejected* rather than
failed — a workshop and a hybrid meeting where the model returned a degenerate
split and `segment_day()` refused it. That is the intended behaviour.

Speaker attribution was then fixed twice more, after the table above was
written: the announcement signal now requires voice corroboration (gotcha 34)
and the anchor loop no longer drifts (gotcha 33). "Barbara Wilhite" went from
294 meetings / 316 clusters to **106 / 11**; "Justin Grant" from 209 / 100 to
**30 / 9**. Both now sit in the same regime as the commissioners. (Those two
figures were 108 and 32 when first written; `chair_anchor` has since moved a
voice apiece. Re-derive rather than quote them — every other number in this
section was re-verified against the live database on 2026-08-12.)

**Nothing is running unattended.** The API is on :8765 and the UI dev server
on :3000; the fleet is idle because ingest is complete. If a background job
ever *looks* stuck, see gotchas 50 and 51 - twice now the stuck thing was the
waiter, not the work.

**`bin/rebuild.sh` throws away every derived layer and builds it again**, from
the same ASR. Nothing is re-downloaded and nothing is re-transcribed. It exists
because the derived layers are where the bugs are, and after enough targeted
fixes nobody can say which rows came from which version of the code.

Kept, because each is unrecoverable or expensive: `utterances` (the ASR itself,
1,036 hours of GPU), `videos`, `speaker_label` / `speaker_override` /
`speaker_ignore` (human judgement), `portal_events` / `portal_files` (the
county's documents as fetched - re-pullable in principle, ~2,000 requests
against someone else's server that is free to withdraw a document), and
**`vec_cache`** - a POSTGRES table, 467,031 embeddings keyed by content hash
(the `vec_cache.sqlite` file of the same name was an orphaned older copy and is
deleted), which is the single
reason a full rebuild costs twenty minutes instead of hours.

Dropped and rebuilt: meetings, agenda items, cases, segments, spans, roster,
speaker identity, affinity, passages and the whole BM25 index.

Two things make it safe to run:

- **The KEEP and DROP lists must cover the schema exactly.** A table in neither
  stops the script. A table added later gets classified by a decision somebody
  makes, not by whichever default it falls into.
- **It is a dry run unless you pass `--yes`**, and the dry run prints every
  table with its row count and its fate.

`--no-llm` for when the inference account is empty. TWO stages call the model,
not one: `name_speakers`, and `segment` - which is what cuts a meeting into
items. So `--no-llm` PRESERVES `segments` rather than destroying something it
cannot put back, and says so in the output, because the spans are then bound
against the previous run's cut rather than a fresh one.

**Scripts.** `bin/refresh.sh` and `bin/run.sh` are the canonical ones. The
others are spent one-offs kept for their comments, not for re-running:
`finish_chain.sh` (waited on two parallel chains), `reindex.sh`,
`catch_up.sh` (folds late-arriving meetings in without a full `--redo` — the
one worth keeping as a pattern) and `respeak.sh` (re-derives identity after a
speaker-logic change; run it if you touch `speaker_id.py` or `anchors.py`).

**What changed tonight, and why it matters more than the numbers:**

1. **Segmentation had stored nothing since the Postgres migration** — unquoted
   `end` in its INSERT (gotcha 26). Every "N segments" line in every log was a
   lie. All 2,142 pre-existing spans were SQLite-era leftovers.
2. **21% of the archive had the wrong speaker.** Planning Commission meetings
   inherited the *County Commissioners'* roster, because the term fallback
   matched on date and ignored body — and then the global `cluster_name` view
   handed those names back even where the per-meeting guard had blocked them.
   54,000 utterances, now 0. See gotcha 28 and `voice_name` in `schema.sql`.
3. **`ask.chat()` had a 180s timeout against a 158s median call.** Half of all
   segmentation days timed out, retried three times and failed after nine
   minutes. The agent's own read calls measure 368-450s — they were *all* over
   the old limit.
4. Minutes classification called "received"/"heard"/"presented" an **approval**,
   and read "the exhibits that **failed** to upload" as a denial.
5. `parse_minutes` was never in `refresh.sh`, so a re-land silently reverted
   every outcome.

**Do this first when you come back:**
1. `bin/audit.py` — **45 invariants, 0 failing as of 2026-08-13** (the count
   only grows as checks are added — it went 42 → 45 inside one evening, so
   "0 failing" is the claim to read, not the total), with 3
   EMPTY and 6 carrying review items. Every check prints the number of rows it
   examined; **treat `EMPTY` as a failure to have tested anything**, not as a
   pass. The three EMPTY ones are the `override.*` family: production holds no
   `speaker_override` rows, so the correction path is currently proving
   nothing.
2. `bin/eval_agent.py --agent` — the vote must reach the agent's evidence, not
   merely rank inside depth 200.
3. `bin/audit_asr.py` — no single ASR hole above 45s anywhere in the archive;
   if that ever changes, Parakeet has started deleting again.

**Slice 1 of the UI rebuild is done** (`UI_PLAN.md` §8): the shell, the design
tokens, the six shared primitives, the global player, routing, `/meeting/:id`
and enough of `/` to reach it. Verified on three meetings chosen to cover the
layout's three states — **867** (agenda + two recordings + roster + outcomes),
**605** (agenda and minutes, no recording), **835** (a recording, no published
agenda). The traversal works end to end: click an item on the spine → the
player seeks → the session switches → the spine follows → the transcript lands
on the moment.

What that slice established, beyond the page:

- **`web/archive.py`** is the API for the rebuild, separate from `api.py`,
  which still serves the old pages. Everything is keyed on the MEETING;
  `/api/meeting/<id>` used to take a *video* id while `/api/agenda/<id>` took a
  *meeting* id, and that trap did not survive (the video-keyed one is now
  `/api/video/<id>`). Routes: `/api/meetings`, `/api/bodies`,
  `/api/meeting/<meeting_id>`, `/api/transcript/<video_id>`.
- **Speaker identity leaves the API as fields, never as a rendered string** -
  `name`, `confidence`, `human`, `voice` - so `SpeakerChip` is the only thing
  that decides how a speaker is displayed. That is what makes R6.2.1 (never
  render `Group 465`) enforceable in one place rather than four, and it is the
  single site a future redaction rule (D3) has to touch.
- **Two typographic registers carry the two sources.** The published record is
  set in a serif on warm ground; anything inferred is a sans on cool ground.
  It does more work than any badge and survives greyscale, high contrast and
  print.
- **Consent runs collapse to the single motion they were.** A BCC agenda is
  routinely 200 items of which 150 are consent, approved en bloc; meeting 867
  shows "95 items taken together · approved 92 · no disposition 3". Listing
  them flat buries the four items anyone came to read.
- **Accessibility was measured, not asserted.** Every text/background pair
  clears 4.5:1 and every meaning-bearing border clears 3:1, in both themes. The
  first pass did not: `--ink-3` and `--ink-4` ran 2.7-4.4:1 while carrying
  "no recording", "Voice B" and item offsets.
- **The URL is the state (R4.2).** `?v=<video>&t=<seconds>&item=<id>` is
  written as the recording plays (via `replaceState`, so Back still works) and
  restored on load. A shared link opens the right session, scrolls the
  transcript to the moment, highlights the item - and **cues rather than
  plays**, because a page that starts talking at you is hostile. `?item=` alone
  also picks the session that item is in, or a link to an afternoon item opens
  the morning recording.
- **Everything that names an item is a control.** The spine row, the agenda
  break inside the transcript, and the band on the chapter track all select the
  item. An item with no recording still selects - it reveals itself in the
  record, expanding the consent table if it is buried there - because 91% of
  decided items have no recording and a spine whose rows do nothing for them is
  not a table of contents.

**Slice 2 of the UI rebuild is done**: `/item/:id` and `/case/:id`
(`UI_REQUIREMENTS.md` §5.3, §5.4). Verified on an item with a case, a
disposition and a recording (20973), one with none of those (73), the archive's
longest case (`PDE-25-7738`, 12 appearances over 10 months), and the round trip
back into `/meeting/1059` at the exact second.

What that slice established, beyond the two pages:

- **`archive.item()` and `archive.case()`** serve `/api/item/<id>` and
  `/api/case/<id>`, which used to return the legacy `api.py` shapes. Those two
  functions are now dead and labelled SUPERSEDED in place, because dead code
  that still looks routed is a trap. Speaker identity leaves the item endpoint
  as fields, exactly as the transcript does, so `SpeakerChip` remains the only
  thing that renders a name (R6.2.1, D3).
- **The graph is navigable.** `ItemCard`'s `href`/`caseHref` are set, and the
  consent table links too - one BCC meeting now carries **186 item links and
  185 case links**, where before nothing linked anywhere. That matters more
  than it sounds: consent is 150 of a 200-item agenda, and R4.1 exists because
  `/item` and `/case` were previously reachable only by typing a URL.
- **A case is redlined against its own title** (R5.4.2). The official title is
  stated once and each appearance shows a word-level diff against it - so
  twelve repetitions of 62 words of legal prose become `(Continuance)` struck
  and `(Regular)` inserted, which is where the application actually changed.
  `redlineTitle()` in `ui/lib/format.ts`; a legend explains the marks once and
  steps that match show nothing at all.
- **A continuance is never a conclusion.** `terminal` is the last appearance
  that decided something, so a case whose final step was continued reads as
  open rather than as continued-and-therefore-finished (R5.4.3). The terminal
  step gets a raised surface and a solid rule; continuances recede without
  disappearing, because five in a row IS the story.
- **The county's PDF renders inline** (R5.3.5), which needed a proxy - see
  gotcha 52. Lazily mounted: these run past a megabyte and nobody asked for one
  by loading an item page.
- **`sameThing()` suppresses the archive's near-duplicate fields.** The agenda
  supplies `section` "PUBLIC HEARINGS" beside phase "Public hearing", and
  `file_number` "PDE25-7721" beside `case_id` "PDE-25-7721". Both were rendering.
- **No raw internal tokens.** `outcome_source` was printing `bulk_consent` at
  the reader; it now says "the consent agenda, approved as one block". Same rule
  as R6.2.1 and worth generalising: an internal value on screen reads as a fact
  about the record when it is a fact about our parser.
- **`ProvenanceMark` failed AA the moment it was put on warm paper.** It used
  `--record-rule` - a *border* token, held to 3:1 - as 11px uppercase text,
  which needs 4.5:1. Measured 3.87:1 on `--record-bg`, now 4.87:1, and it is a
  shared primitive so every surface got the fix. Gotcha 37, second occurrence:
  contrast is a property of a pair, and a primitive that passed on one
  background has not been tested on the next.
- Both new pages measure **0 contrast failures in both themes**, no horizontal
  scroll at 375px, and reserve the dock's space so it stops covering the rail.

**The minutes now record the right motion** (gotchas 55-58). A public hearing's
minutes hold several motions and the parser kept the first, so 212 items read
"Approved" where what was approved was a member of the public's exhibits, a
motion to hear the item at all, or the adjournment of the meeting — and two
were denials shown as approvals. It surfaced on `/case/PDE-25-7738`, whose
terminal-outcome banner is computed from these values, so a rezoning's headline
verdict was about somebody's paperwork. `parse_minutes.choose()` now drops
subsidiary motions and takes the last of what remains, except that a refusal is
never overridden by what follows it.

Fixing it uncovered three further parser bugs in the same swallow rule (86
dispositions contained a LATER item's heading, now 0) and a write that could
never un-say anything. Net: 442 items changed, 25 newly decided, 33 left with
no disposition because every motion recorded under them was subsidiary — which
is honest, and already a designed state. `minutes.no_subsidiary_disposition`
holds it, over 17,030 rows rather than vacuously.

No re-index was needed: `passages` does not carry `disposition` or `outcome`,
and `agenda_fts` is a Postgres expression index that Postgres maintains itself.

**Browse is rebuilt on a time axis** (§5.1, out of rollout order at the
maintainer's request — search is still slice 3 on paper). `/` was a list
grouped by year; it is now the archive as an object:

- **`archive.overview()`** carries the collection's shape and a per-month
  histogram; **`archive.highlights()`** carries the three curated entry points.
  `/api/overview`, `/api/highlights`, and `meetings(month=…)`.
- **The axis is a year × month grid**, one cell per calendar month, carrying
  two facts: fill for how many meetings, and a warm bar along the bottom edge
  for how many of them we can HEAR. Drawn together, the honest shape appears —
  **no recordings at all before 2018, three in it**, against twelve years of
  published record. A reader looking at 2016 can see the video is not missing,
  it never existed. A count alone hides that.
- **The fill is scaled across the range the data occupies, not from zero.**
  Two passes got this wrong in opposite directions. Linear over 0..peak ran
  0.15–1.0 with a MEDIAN of 0.35, so half the grid sat under a seventh of the
  tint and read as blank paper; a square root fixed the floor and flattened the
  top, giving 0.39–1.00 with a median of 0.59 — monotonic, honest, and still
  visually uniform. No month in twelve years has fewer than 3 meetings or more
  than 20, so measuring from zero spends most of the scale on counts that never
  occur. Scaling over 3..20 with a 0.14 floor uses the whole ramp and makes the
  decade's doubling of county business legible as a gradient.
- **A month has THREE states, not two.** Scheduled-and-not-yet-held is neither
  a held month nor an empty one, and drawing it as empty told the reader that
  nothing was on the calendar for the rest of 2026 when 30 meetings were — the
  exact error the axis exists to prevent, committed by the axis itself.
  `overview()` now returns `scheduled` alongside `meetings`; a future month is
  a dashed outline, an empty one is hatched, and August 2026 (8 held, 5 to
  come) is tinted with a dashed trailing edge.
- Every facet is in the URL and composes (`?body=&year=&month=`), values are
  validated rather than trusted before reaching a LIKE, and the three
  archive-wide entry points hide once a narrower filter is on screen.
- Measured: 0 contrast failures in both themes, no horizontal scroll at 375px,
  and the entire twelve-year axis fits on a phone.

Note `shortBody()` moved from `CaseThread` to `lib/format.ts`: it was exported
from a `"use client"` module, and importing a plain function out of one into a
server component fails at render with *"Attempted to call shortBody() from the
server"*. A pure helper belongs in a module with no directive.

**Slice 3 is done: `/search`, on a tool surface** (§5.6, D9). The maintainer's
direction was explicit: *"I want to use tool calls for search to allow the
model to do the search itself, rather than have that be hard coded in the
flow."* So the surface came first and the page is a consumer of it.

- **`web/tools.py` is the surface.** Five tools — `search_transcript`,
  `search_record`, `get_item`, `get_case`, `get_meeting` — each with a
  description written for a MODEL to read, a JSON Schema, and one `call(con,
  name, args)` entry point that validates, coerces and enum-checks. `GET
  /api/tools` serves the manifest; `GET /api/tool/<name>?...` invokes one. The
  descriptions say what each tool CANNOT reach, because the failure D9 exists
  to prevent is a caller that searched the wrong source and concluded the
  archive holds nothing.
- **`/search` is two of those calls and nothing else.** Not a parallel
  implementation that drifts: what a reader can find by hand the agent can
  find, and a bad result on the page reproduces as a tool call. Slice 4
  inherits this whole surface.
- **Facets route per tool, from the schemas.** `speaker` reaches speech and has
  no meaning for an agenda item; `decided` is the other way round. Passing
  everything to both 400s on a URL a reader can reasonably construct, and
  teaching the page which facet belongs to which tool would put that knowledge
  in two places — so `tools.search()` filters each call against that tool's own
  `properties`. The rail labels which source each group narrows.
- **No client component anywhere.** The box is a plain GET form, the rail is
  links, every facet is in the URL. Shareable, back works, no script needed.

Four retrieval defects surfaced only by building the page:

1. **`websearch_to_tsquery` ANDs every term**, so "license plate cameras"
   matched nothing while the county's own item is titled "License Plate
   Detection Systems". An empty result reads as "the archive holds none of
   this". `search_items` now loosens to ANY term when the strict query is
   empty, and the page says it widened.
2. **Under OR, `ts_rank_cd` inverts the answer.** It counts COVERS, so an item
   saying "Trench Plate" twice outranked one matching two of the three words
   typed. Ranking now leads with how many DISTINCT query terms an item matches.
   10 ms on 14 candidates, 280 ms on 1,733.
3. **An identifier is not words.** `R-58` through a text-search parser becomes
   fragments that match nothing, so the placeholder R5.6.4 asks for would have
   been a lie. A code now takes a different query entirely — equality against
   `code`, `case_id` and `file_number`, normalised for the county's
   `R-58`/`R 58`/`R58` inconsistency.
4. **Nearest-neighbour search always returns neighbours.** `zzzznothing`
   returned twelve passages, which asserts the archive contains something it
   does not. `DENSE_FLOOR = 0.55` now applies when NOTHING matched lexically:
   nonsense tops out at 0.52 against 0.62–0.65 for real queries. It does not
   apply when BM25 or a thread key already vouched for the query, so a real
   query's weak tail is untouched.

`retrieve.search_items()` returns `{total, items, loosened}` rather than a bare
list — `ask.py:283` was updated with it. `retrieve.search()` now also carries
`meeting_id`, `meeting_date` and `body`, and takes a `body=` filter.

**"Where the board disagreed" reads BOTH sources** (maintainer: it was missing
the August 2026 argument over Flock licence-plate cameras). The minutes lane is
`vot(ing|ed) nay` — 114 items, up from 104, because the past tense was missed —
deduplicated per motion, with the dissenting members' names parsed out. The
transcript lane is new: a divided tally announced from the chair ("motion
passes three, two"), a motion that failed, or a board member stating an
objection outright, each shown WITH THE QUOTE as evidence and marked inferred.
Procedural phases are excluded, or the page announces that the board was
divided about adjourning. Items already in the minutes lane are dropped from
the room lane; with six slots a side, corroboration is a poor trade against
what only the recording knows. 260 ms, gated by a GIN index probe that cuts
299k utterances to 23k before any regex runs.

**"Recently decided" is now a meeting-day digest.** 113 items were decided on
14 July 2026 and the section listed eight of them by sequence number — an
arbitrary sample under a heading implying a summary, eight rows repeating one
date and one body. The unit is the meeting now: counts, a composition bar in
the OutcomeBadge colours, and the non-routine items named.

**The time axis had no focus ring at all.** `.cell` sets `outline: 2px solid
transparent` to animate hover; that is a CSS-Module class and the global
`:focus-visible` is a pseudo-class, so they tie on specificity and the module
wins on source order — deleting the ring it inherits. Verified: a focused cell
computed `outline-color: rgba(0,0,0,0)`, leaving 145 keyboard-reachable links
with no focus indicator (R8.2, WCAG 2.4.7). See gotcha 59.

**Slice 4: `web/agent.py`, the tool loop.** *(Written while it was in progress.
It is DONE — see the note closing this section.)* The agent works end to end
from the CLI and is not yet wired to the server or the page.

- **The loop.** System prompt, then `llm.chat_raw(msgs, tools=MANIFEST)` until
  the model stops calling tools. Tool errors are handed BACK to the model
  rather than raised — a wrong argument is something it can fix next turn, and
  killing the run would discard the work already done. Caps: 8 steps, 90k
  characters of tool output, then one forced answer turn.
- **`ask.chat_raw()`** added to `bin/ask.py`, returning the whole reply message
  so `tool_calls` is visible. `chat()` is a wrapper over it and unchanged —
  `segment.py` and `name_speakers.py` call it thousands of times a run.
- **Citations are verified, not trusted.** Every `[N]` and `[item:N]` is checked
  against what the tools actually returned IN THIS RUN, and anything else is
  struck from the prose and counted. An unverifiable citation is worse than
  none: to a reader it looks exactly like a real one.
- **`decisions_in_play()` is now redundant.** The agent calls `get_item` once a
  search puts an item in play, and the motion and the vote are simply the last
  lines of it. `eval_agent`'s whole reason for existing, solved by the agent
  choosing to look rather than by a hard-coded patch.

Two rendering bugs, both worth remembering because both were invisible until a
real run:

1. **A passage line showed its item's TITLE and not its id**, so the model
   could see that a passage belonged to R-58 and had no way to open it. The
   most important traversal in the design was unreachable; the first run
   searched the record six times instead and spent its whole budget.
2. **`get_item` rendered utterance lines as `[385] Yeager: so my motion is…`** —
   an id-shaped token that is a line index, not a passage id. The model did
   what that invites and wrote "([item:31314] passages 2, 59-60)" in prose, so
   the motion and vote it had correctly found were uncitable and the check
   counted zero transcript citations. Lines now carry the id of the passage
   CONTAINING them, which is the honest reference anyway.

Measured after both fixes, "What was decided about the school zone speed
cameras?": 6 calls, 12 citations, 0 struck, correctly refusing to infer an
outcome the minutes do not record. "What happened to the Evans County Line 80
rezoning?": 4 calls, 23 citations, leads with the July 2025 approval and notes
that the deciding meeting was never recorded.

**Slice 4 is DONE.** Verified in the tree on 2026-08-13, because the paragraph
above said "in progress" long after it stopped being true and `UI_PLAN` §9 had
already recorded it as built:

- `/api/ask` is wired to the agent - `web/server.py` imports `agent` and calls
  `agent.ask(question, con, ...)`, streaming SSE. Wired is not the same as
  working: it was silently dead for weeks on a `SystemExit` in a library and a
  key that lived in another repo, and it is now rate-limited and deadlined.
  Gotchas 87 through 90 are that whole story and are worth reading before
  touching this path.
- The page exists and consumes it: `ui/app/ask/page.tsx` is a server shell over
  `ui/components/ask/AskView.tsx`, which opens
  `EventSource("/api/ask?q=…")` and renders the real tool calls.
- `ask.plan()` is reachable from nothing. Every remaining `import ask` -
  `web/agent.py`, `web/server.py`, `bin/segment.py`,
  `bin/name_speakers.py`, `bin/eval_agent.py` - takes the CHAT CLIENT
  (`import ask as llm`), not the pipeline. The fixed pipeline is retired in the
  sense that matters; the dead functions are still in the file and deleting
  them is tidying, not work.

The one item from the original list still outstanding is the legacy HTML pages.
`web/server.py` still serves `/search`, `/speakers` and `/ask` from
`search.html`, `speakers.html` and `ask.html` on :8765. Readers never meet them
- the Next app owns those paths on :3000 - but they are a second implementation
of three surfaces and they are listed as superseded under "Running it".

**Slice 6 is done: `/admin`, the curation console** (§9, §5.8, D1, D8).
`web/admin.py` is the data layer and auth; the shell is `ui/app/admin` with its
own chrome, and the public header returns null under `/admin` so the two shells
never render together. What it establishes:

- **Auth is D1 with one deviation the operating rules demanded: the token is
  never printed.** It is written to `.admin_token` (mode 600, gitignored,
  regenerated per process start) and only the PATH is announced. `POST
  /api/admin/login` exchanges it for an `httpOnly` `SameSite=Lax` cookie, so it
  is never in a URL, history or script reach. Admin routes refuse non-loopback
  clients outright — this server has no TLS, so the interface admin answers on
  is the one that never leaves the machine. The legacy `/api/speakers/*` WRITE
  endpoints now require the same session: they were open before there was an
  auth model to put them behind.
- **The queues order by impact** — utterances a decision fixes — because a
  review list is only workable if its head is the row worth fixing first.
  Three queues: the split-voice reviews (`speaker.one_voice_per_meeting`, 14
  when this was written and **394 as of 2026-08-13** — gotcha 86 explains the
  jump, and it is the queue doing its job rather than a regression), pending
  public proposals (R9.6), and unnamed voices by lines × meetings, each row
  with a playable sample.
- **The review screen is the evidence in one place**: the transcript
  (virtualised, same flags as the reading view — gotchas 38/40), each voice's
  `basis` rendered as the claim it is, `voice_affinity` with the verdict
  spelled out, the three longest lines here, and the same cluster speaking in
  OTHER meetings. The top queue row proved the design immediately: the second
  voice wearing "Mariano" measures **0.937 against Moore**.
- **All four §5.8.2 verbs write at utterance grain** (click, shift-click,
  apply), whole-voice label/ignore at voice grain, and every name-changing
  write calls `index_passages.refresh_video` — measured ~4s warm, so a
  correction lands in the transcript, the vector and the postings before the
  response returns. The bar states what a selected range actually contains
  ("5 Mariano, 5 unidentified"), so a filtered view cannot hide that a range
  swept up somebody else's lines.
- **Verified end to end, leaving the archive as found**: apply → basis
  `override` → 1 passage re-posted → undo → back to `voice`, via curl and again
  through the UI; pending proposals change nothing a reader sees but flag
  `contested` (R5.8.10), and accepting one applies and re-indexes. Contrast
  measured in both themes on both screens (all pairs ≥4.9:1 after moving the
  reader pane off the sunk ground); no horizontal scroll at 375px.
- **Each voice under review wears a stable color** — on its rail card and on
  every one of its transcript lines, with a "its lines ↓" walk that steps
  through them. The review task is "which interleaved line is which voice",
  and that must be answerable at a glance, not by comparing `S10·c29` against
  `S09·c1000` by eye. The six hues avoid the status colors on purpose (a
  voice's identity is not a verdict) and measure ≥5.6:1 light / ≥7.9:1 dark.
- **The transcript is tied to the audio.** The line the recording is inside
  wears a ▶ and an accent ring; the list follows the playhead exactly as the
  reading view does (manual scroll disarms, any play re-arms — gotcha 39);
  playing a rail sample takes the transcript to that moment; and the hint bar
  shows "▶ 1:22:00 — following" or the "Follow the recording" button. When
  the filter hides the sounding line the marker honestly disappears rather
  than pointing at the previous visible line. The ring is box-shadow, not
  outline, so the focus ring survives (gotcha 59).
- **The token is written only after the port binds.** A second launch against
  a busy port used to write `.admin_token` and then die, leaving a file whose
  token no process held - sign-in could only say "does not match" while the
  operator held the freshest file. Observed in practice within a day.
- **Every operation runs from the console, as the flow it is** (`/admin/ops`,
  `bin/job.py`). Four stages — discover (county documents · new recordings),
  ingest (the fleet), fold in (`catch_up.sh`), identity (label propagation ·
  the paid naming chain) — each a sequence of the DOCUMENTED commands, never
  a composition invented for the button. Prerequisites are MEASURED from the
  database and enforced server-side, with the refusal stating the
  measurement: the fleet is refused when nothing is pending ingest, fold-in
  when every transcribed recording is already in, the naming chain when the
  server holds no inference key. One job at a time, never while the fleet is
  working, and a paid job requires a second explicit click that says it
  spends money. Status and log stream to the page from `logs/job.json` /
  `job.log`; the runner survives the server restarting.
- **Every step states what is waiting for it, and the discover steps had to
  earn their number.** Ingest, fold-in and naming always measured theirs; the
  two discover steps said only "always safe to run", which is not a
  measurement. The portal now reports the meetings already on the county's
  calendar with no agenda landed - 37 - because the county posts an agenda
  days before each one and that is what the next sweep collects.
  Deliberately NOT the 757 past meetings without an agenda: re-running
  collects none of those, since the county either never posted one or posted
  an image-only scan this archive cannot read. **A number a run cannot
  consume is not a backlog, it is a coverage fact wearing a backlog's
  clothes.** The channel sweep gets no pending count at all, because it
  cannot know what is new until it looks; it reports what it holds and how
  much of that it could not place on a meeting. Two candidate metrics were
  dropped on measurement: 371 "unfetched" portal files are all fetched and
  empty (image-only scans, permanently), and "meetings with no minutes
  parsed" counted every meeting, because minutes are parsed into
  `disposition` on existing items and there is no `source='minutes'` row to
  be missing.
- **A running job answers "is it stuck?", which a spinner cannot.** The first
  console showed a pid and a log dump, and a wedged step and a working one
  looked identical. Four measurements make them different, all taken on the
  server, against the clock that wrote the timestamps: which step of how many
  is running (`step_index` / `step_count`, written BEFORE the step starts —
  a step that announces itself only on completion looks hung while it works),
  how long that step has run, **how long since anything was written to the
  log** (`log_age`, the actual stuck test), and the banner the current step
  last printed for itself (`log_phase` — `catch_up.sh` announcing `===
  segment (incremental) ===`, which is the only progress a half-hour single
  step has). The page seeds its clocks from those and ticks them locally, so
  it never subtracts the browser's clock from the server's. Each job carries
  a `say` line per step, so the plan reads as sentences before the run and
  ticks off during it; a tolerated non-zero exit (`audit.py`) is an amber
  tick that says `reported items (rc 1)` rather than a silent green one. The
  quiet line is a measurement, not a verdict — several steps are legitimately
  silent for minutes, and a page that cried stuck at those would be ignored
  by the time it was right.
- **Stop signals the process group, not the pid.** The work is a CHILD of
  `bin/job.py`; killing the parent alone leaves `civicclerk.py` running and
  the status file lying. `admin.job_stop` checks the pid against `/proc`
  first (a status file outlives its process, and a recycled pid is somebody
  else's), `SIGTERM`s the group, escalates after two seconds, then writes
  `state: "stopped"` itself — the job cannot write its own ending. "Stopped"
  is not "failed": a person stopped it, and that is a different fact.
- **The fleet reports what it holds, not that it exists.** "6 workers up" is
  not progress. `_fleet` names the live workers as they name themselves in
  `claimed_by`, and joins them to the recording each one holds and for how
  long — `updated_at` on a claimed row is the claim time, which is how
  `bin/audit.py` already reads it — beside the queue emptying behind them.
- **The page tolerates an API older than itself.** `web/server.py` is
  restarted by hand and `next dev` reloads itself, so the two do get out of
  step. The newer fields are optional in the client's type; their absence
  renders a line saying to restart the server, rather than a white screen
  that hides the restart it needs.
- **Labels propagate from the console, with a verified way back.**
  `bin/rederive.py` runs the FREE part of respeak — `speaker_id --write →
  affinity → index_passages → audit` — as a detached job the dashboard's
  "Propagate labels" panel starts, polls and summarises; `name_speakers` is
  deliberately absent because a button must not spend money. Before touching
  anything it snapshots `utterance_speaker` (for the measured diff the panel
  shows: utterances changed, newly named, un-named, top movers) and backs up
  `speaker_identity` + `voice_affinity`; "Revert the last run" restores both
  and re-indexes. The restore path is PROVEN before each run against a
  rolled-back transaction — and that preflight immediately earned itself by
  catching gotcha 13 in the backup writer (`list(row)` yields column names),
  which would otherwise have been discovered during a revert somebody
  actually needed. Snapshots go through gzip files, not scratch tables:
  `rebuild.sh` requires KEEP/DROP to cover the schema exactly.
- **Decisions stay visible after the queue forgets them.** A queue is a
  one-way to-do list: deciding a row makes it vanish, so a wrong decision is
  invisible the moment it is made — which is exactly how the "Mike Wells"
  mislabel hid. The dashboard now carries a whole-voice label LEDGER (newest
  first, with Review and Clear), which is what R9.5's "visibly permanent"
  actually requires; and `label.surname_form` in the audit catches the
  full-name shape across labels and overrides, including anything written
  before the canonicaliser existed.
- **A board member's name is stored as the surname, whatever was typed.**
  "Mike Wells" does not join `people.surname = 'Wells'`, so a full-name label
  bypasses the roster guard and the split-voice check, and search holds two
  speakers where there is one — observed in practice on the first day of use.
  `admin.canonical_name` now maps a name that exactly matches a seated
  member's full name to the surname on every console write, and the response
  says so. The one case it gets wrong — a member of the public sharing a
  seated member's exact full name — is visible in that message and cheap to
  undo.
- **Names are picked, not typed.** The correction bar and the whole-voice
  label offer a dropdown ranked by evidence — measured voice matches first
  ("Moore — voice match 0.94"), then the people the agenda seats that day,
  then anyone already named in this meeting — with "someone else — type the
  name" as the escape hatch. Affinity below 0.35 is withheld from the list:
  on this corpus that is measured to be a different person, not a weak match.
  The evidence was already on screen; making the operator re-type it was the
  reported bad UX.
- **The console is reachable from where the error is noticed** (R5.8.3).
  While the operator's session cookie is live, every SpeakerChip in the
  reading views grows its "Correct this name" affordance, landing on
  `/admin/review/<video>?label=…&sel=…` with the turn preselected and the
  voice focused. Readers never see it: the probe (`/api/admin/session`, no
  database work) answers false and nothing renders. One trap fixed along the
  way: a server restart leaves a dead `httpOnly` cookie the browser cannot
  replace from script, so every admin 401 now sends the clearing Set-Cookie —
  otherwise a wedged client stays wedged until someone finds devtools.

**The naming figures above are stale, and the reason is recorded, not a bug.**
`utterance_speaker` resolves a name for **159,892 of 298,737 utterances
(53.5%) as of 2026-08-13**, not the 78.8% in the table — the respeak ran while
the inference account was empty (see "The LLM account ran out of credit"), so
`name_speakers`' coverage is simply absent. The 155,029 this note first
recorded became 159,892 after the operator's 71 labels were propagated
(splits 457 → 14 → 3). Top up credit, run the naming chain from `/admin/ops`,
and re-measure before quoting any of these numbers.

Re-measured the same day at 15:54, after `chair_anchor` was put back into the
chain (gotcha 86): **204,146 of 298,737 (68.3%)**, still with no paid stage.
The remaining gap is `name_speakers`, and it still needs credit.

The split count in that parenthesis moved with it: **457 → 14 → 3 → 394**. The
last step is not a regression and the reason is in gotcha 86 — restoring the
anchor put the right name on the anchored cluster, which exposed the impostor
cluster sitting beside it in the same meeting. 394 rows, 23 clusters, about six
listening decisions.

## Solved: votes were unretrievable

A vote passage reads:

> "All right, we have a motion to have a second. All in favor say aye. Aye.
> Any opposed, nay."

It contains **no topic words**. BM25 had nothing to match and its embedding sat
near every other vote in the archive rather than near its subject. Three
consecutive fixes (richer reader prompt, few-shot examples, multi-lens reading)
all improved the *reader* and none helped, because the reader was never shown
the passage. The failure was in retrieval and was structural.

Segmentation fixed it: every utterance belongs to an item, and the item's
subject goes into the passage's `search_text`, so a vote is findable through
what it decided. That subject was originally the LLM's grounded title; it now
comes from the **published agenda** where one exists - official title, item
code, case number - falling back to the LLM title for procedural stretches.

Ranks measured at the time, on the SQLite build (62 / 44 / 34 / 28) and again
after the Postgres migration (56 / 35 / 31 / 29), against *not in top 200* for
every phrasing before. **Do not treat those as current** - the corpus, the
speaker names and the injected subject have all changed since. Re-run
`bin/eval_agent.py` for a live number.

The agent now answers *"The board voted to adopt both school zone safety
camera programs … with the sheriff patrolling school zones until the program is
implemented"*, citing the motion and the vote, where before it reported no
decision. `bin/eval_agent.py --agent` is that check, and it is pass/fail.

**Retrieval rank is a diagnostic, not the pass condition.** The eval originally
asserted the vote appeared within the top 200. The agent reads only the top 30
per query, so the eval reported PASS while the agent answered *"the evidence
does not confirm a final vote outcome"* — the vote sat at rank 33–58 for the
planner's own wording, which contains no decision words. The eval now asserts
on the evidence the agent actually assembled. **Test the depth the consumer
uses, not a depth that flatters the index.**

**`retrieve.decisions_in_play()` is what closes that gap.** Ranking finds an
item's discussion easily — it is long and dense with topic words — and reliably
misses the motion and the vote, which are neither. Rather than searching deeper
and paying for it on every question, an item that is already in play has its
terse cross-speaker exchanges fetched directly, from the end of the item
backwards. Costs one query and about five extra passages. Only possible
because segmentation exists.

## An answer can be sent to somebody (`/ask/:id`)

`/ask?q=…` looks like a link to an answer and is not one. It is an instruction
to spend money: the recipient sits through a fresh run — minutes, at
`ASK_DEADLINE` — takes one out of `ASK_DAILY_MAX`, and is shown a *different*
answer, sampled again over an archive that has gained meetings. Forwarded to
twenty people it is twenty paid runs and twenty different answers to the same
question, none of them the one the sender was talking about.

So every completed run is kept. `web/answers.py` writes it under a 12-character
`secrets.token_urlsafe(9)`; the id comes back to the page in the `answer` event,
and `/ask/<id>` is a server-rendered read of the row. Free, instant, and it says
the same thing tomorrow.

**What is stored is the answer and what it CITED — never the words it quoted.**

| | stored | read back at render |
|---|---|---|
| the prose | verbatim, markers intact | — nothing can reconstruct generated text |
| a transcript citation | `(video_id, start_idx, end_idx)` | `tools.passages_at` |
| a record citation | `agenda_items.id` | `tools.items_at` |
| the trace, `looked_at`, `struck` | verbatim | — numbers and tool arguments |

That is the whole design, and what it buys is that **a redaction applied since
is already in `passages.text`**, a corrected speaker name is already on the row,
a re-parsed disposition is already on the item. Nothing has to go back and find
old copies, because there are no old copies.

- **A passage is named by its range, not its id.** `index_passages.rebuild_video`
  reassigns ids on every rebuild and says in as many words that *nothing outside
  the index stores one*. Verified on the live archive: `(video_id, start_idx,
  end_idx)` is unique across all **166,998** passages, no nulls, and resolving a
  dozen of them takes **1.1 ms**. Agenda item ids *are* durable — `/item/<id>` is
  a public URL — so those are stored as plain ids.
- **A range that stops resolving is reported, not papered over.** Boundaries move
  for one reason: a redaction shortened a line and the passage fell under the
  indexing floor. The citation then renders struck-through and the footer says
  *"1 citation no longer resolves"*. Silently serving the text from before it
  went is the failure this design exists to avoid.
- **Measured on a real run** — "What was decided about the school zone speed
  cameras?", 8 tool calls in 26s, 20 passages and 2 items cited across 2
  recordings, 0 struck: **2,762 bytes stored**. The first version of this table,
  which copied the hit objects, was 6.3 KB for a *smaller* answer. `ASK_DAILY_MAX
  =400` bounds the table around a megabyte a day at full saturation, so **nothing
  expires and there is deliberately no knob to make it.** A saved answer is a URL
  somebody may have put in an email; a link that stops resolving is a worse
  outcome than the disk.
- **`bin/schema.sql` drops the superseded table on sight.** It is the only DROP
  in that file, guarded on a `result` column that only the dead shape has. The
  first shape existed for about an hour on 2026-08-14 and never shipped, but a
  dump taken in that window would otherwise survive re-applying the schema —
  every other statement is `IF NOT EXISTS` — and `web/answers.py` would fail
  against it complaining about a missing column rather than about the real
  problem. Verified both ways: it replaces the dead shape and is a no-op on the
  current one. Delete the block once no dump from before 2026-08-15 is in play.
- **The server writes the row, never the browser.** A POST that took the answer
  from the page would be a public endpoint minting permanent URLs on this domain
  out of attacker-supplied content.
- **The id is random, not a hash of the question.** A hash makes two askers share
  one row, which is a cache of questions and not a link to an answer: the second
  reader would be shown, with no way to tell, what the archive said to somebody
  else last spring.
- **The page says which half is which.** *"The answer is not re-run when this
  page is opened, so the wording is that day's. What it cites is read from the
  archive as it stands now."* Dating the whole page would imply the citations
  are that old; calling it current would imply the reasoning had been revisited.
- **Asking leaves you at `/ask/<id>`, and there is no share control.** The view
  `router.replace()`s to the answer's own URL the moment it arrives, so the
  address bar always holds the thing worth copying and the copy-link component
  that used to sit above the answer is deleted. `replace` and not `push`, on
  purpose: `?q=` behind the Back button makes Back a paid agent run. `?q=` still
  holds the question while the run is in flight so a reload does not lose it.
  **The navigation is its own effect, keyed on the answer's id, and that is
  load-bearing.** Doing it inside the SSE `answer` handler put `router` in
  `open`'s dependency list, and `open` is a dependency of the effect that opens
  the stream — so any render that changed the router's identity would tear the
  stream down and open a new one, which is another paid run. `useRouter()` is
  stable in practice, and "in practice" is not what should stand between a
  re-render and the bill. Verified by counting `EventSource` constructions
  across a whole ask: **1**.
- **Not indexed** — `noindex` on the page and `Disallow: /ask/` in robots.txt
  (which does *not* match `/ask`; a prefix rule needs the trailing slash). A
  machine-written reading of the archive is not the record, and the record is
  what a search engine should be sending people to.

### Measured on a table that is not empty

Everything above was built and checked against an empty `answers` table, which
is the condition under which every query looks fast. Loaded with **5,000
synthetic answers** it looked different, and three things came out of it.

**The two new audit checks were quadratic.** Written as `EXISTS (SELECT ...
jsonb_array_elements(...))`, each one re-expanded every answer's jsonb for every
one of the 3,440 applied redactions:

| | as written | expanded once, then joined |
|---|---|---|
| `redaction.gone_from_answers` | 4.06 s | **0.06 s** |
| `redaction.answers_quoting_a_redacted_line` | 15.83 s | **0.04 s** |

Identical counts, planted violations included. The fix is `CITED_VIDEOS` /
`CITED_RANGES` in `bin/audit.py` — one LATERAL expansion per answer, hash-joined
against `redaction`. An audit that runs after every rebuild has to stay cheap or
it stops being run, which is the same failure as one that errors.

**`answers_cited_video` is the only index anything needs.** "Which answers quote
this recording" is the only lookup in the table, and `bin/redact.py` asks it once
per span while applying a redaction. Containment (`@>`) against a GIN
`jsonb_path_ops` index: **10.1 ms → 1.3 ms**, for 168 kB at 5,000 rows. `@>` also
returns false on a malformed row where `jsonb_array_elements` raises, which
inside the apply transaction is the difference between "no match" and "removing
this address failed".

**The `cites` CHECK is VALIDATED, and the first attempt at it was not.** `NOT
VALID` skips existing rows but still enforces every later INSERT *and UPDATE* —
and the update that matters is `scrub_answers` taking an address out of an
answer. One legacy malformed row would have turned "apply this redaction" into a
CheckViolation rolling back the whole apply, archive-wide. Found by writing the
constraint that way and watching a test fail. It validates now: `web/answers.py`
cannot write a row that fails it, so if the scan ever does fail, that is the
finding — and it surfaces while applying the schema rather than mid-redaction.

**A constraint in `CREATE TABLE IF NOT EXISTS` reaches exactly one database.**
The column list is documentation everywhere else, so the CHECK is added by a
guarded `DO` block instead — stated once, so a fresh database and an existing
one cannot end up with different constraints. Caught by adding it, re-running
the file, and watching a malformed row insert happily.

### The prose is the sixth redaction surface, and the only copy in the archive

Five surfaces hold an address — the transcript, the passage text, its
`search_text`, the BM25 postings, the full-text vector — and all five are
*derived*, so `republish()` plus a re-index fixes them. A saved answer's
citations are now covered by exactly that, because they are read back out of
`passages` when the page renders.

Its **prose** is not. The agent's own sentences quote what they cite, they are
generated text, and nothing can recompute them — so they are the one copy of
transcript text this archive keeps, at a URL somebody may have circulated.
`redact.scrub_answers()` therefore replaces the span with `[address removed]`
— the same marker `republish()` puts in the transcript — in the same
transaction that applies the redaction, and `redaction.gone_from_answers` is
the check that it happened.

**Nothing is deleted, and that is deliberate.** An earlier version dropped the
whole row. That was the wrong instrument twice over: it destroyed a public link
over one string, and because the applied set is full of fragments it destroyed
*correct* answers. Replacing rather than removing makes the blast radius the
address itself, which is the only thing anyone objected to. The reading
survives, the link keeps working, and the archive stops publishing the address —
which is the same trade `republish()` already makes for the transcript, applied
to the one piece of text that cannot be recomputed.

There is also no retention sweep and deliberately no knob for one. A saved
answer is a URL somebody may have put in an email or a news story; a link that
stops resolving is a worse outcome than the four kilobytes it costs.

**Scoping it to the recording was not enough, and a real run is what proved
it.** The first version deleted a saved answer whose prose contained an applied
span, scoped to answers citing that recording. Run against a real answer — "How
has the board handled impact fees since 2023?", 28 citations, correct — the
invariant went red on the span **`Florida`**, matched inside the sentence
*"Florida Statute 163.31801(6) caps annual impact-fee increases."* A correct
answer, deleted by a garbage redaction.

The applied set is full of halves of addresses: of 3,440 spans, 70 are under 10
characters (`A`, `L`, `one`, `4314`, `34110`) and plenty more are bare town
names (`Palm Harbor`, `Hudson`, `Florida`). A reviewer accepting a redaction
accepts whatever the detector proposed, fragments included.

**A length floor cannot fix it** — `9641 Jerome` is eleven characters and is
somebody's house. What separates a fragment from an address is that an address
carries **a number AND a place-name**; either half alone is a ZIP, a town or a
state. So `LOCATING` is `(has a digit AND a 3-letter word) OR length >= 20`,
the length arm being for the addresses the recogniser spelled out (`Sixty
three, twenty seven Grand Boulevard` has no digit in it). Only a locating span
is scrubbed; scrubbing on the fragments would blank ordinary words out of
correct sentences. The same predicate is in `bin/redact.py` and `bin/audit.py`
— grep `LOCATING` to find both, and they have to agree or the check and the fix
are describing different things.

**What a string search cannot settle is left to a person.** An answer that
cited the redacted line and *paraphrased* the address — reordered, half of it —
matches nothing, and no rule can find it. `cites` keeps the passage RANGE, so
the question "did this answer cite the line that was redacted?" is answerable
exactly, without any text matching at all; those answers are listed by
`redaction.answers_quoting_a_redacted_line`, which is `review=True` because a
non-zero count is expected and is not a defect. An answer citing a passage that
happened to contain an address is normal, and usually its prose says nothing
about the address. That is this file's own rule — a detector proposes, a person
decides — applied to the residue.

Verified on the cases that matter: the `Florida` answer is untouched, an answer
quoting a real address has exactly that string replaced by the marker and stays
readable, an unrelated answer is untouched, no row is removed, the hard check
fires on an unscrubbed answer and passes after the scrub, and the review check
surfaces the answer that cited the redacted line.

### Run the real thing before believing a stub

The whole path was run twice against the paid endpoint rather than a stub, and
both runs found something no stub could.

- **8 tool calls, 26s, 20 transcript citations and 2 record ones.** Exercised
  `get_item` — the one tool whose passages reach the agent through `_cover()`
  rather than a search, and which nothing else had covered. Row: 2,762 bytes.
- **10 tool calls, 28 citations across several recordings.** Row: 4,635 bytes.
  This is the run that caught `Florida`.

A stub answers instantly, with evidence you chose, over text you wrote. It is
exactly the shape of test that agrees with whatever you already believed —
here, that scoping to the recording was enough.

**Noticed while doing this, and NOT fixed:** `redaction.gone_from_index` (6
violations) and `redaction.unfindable` (84) are currently failing, and were
before this work. Both look like the same degenerate-span problem.

## Next

The UI is being rebuilt from scratch — see `UI_REQUIREMENTS.md` (what must be
true), `UI_PLAN.md` (how it fits together) and `PRIOR_ART.md` (what other civic
archives got right). The pipeline work below is what remains independent of it.

1. ~~Wire the app to the domain model.~~ **Done**, and rebuilt properly in
   slices 1, 2, 5 and 3.
2. ~~Never render a raw `Speaker N`.~~ **Done**, and now R6.2.1.
3. **Measure retrieval, properly.** `eval_agent` is ONE case. There is no broad
   number for whether search got better or worse across the Postgres migration,
   the segmentation, the re-index or the agenda binding. Still the largest
   unknown in the project, and it now blocks something concrete: D9 turns
   retrieval into tools the agent drives, and there is no way to tell whether
   that helps without a measurement that is more than one case. Slice 3 raised
   the stakes: the record arm now loosens, re-ranks and gates on a similarity
   floor, and every one of those thresholds was set from a handful of probes
   rather than a benchmark.
4. **Retire `segments`** once binding is trusted; two sources of truth for
   "what item is this" will drift.
5. **Rejoin the last 10 orphan recordings** (23 hours of workshops,
   `meeting_id IS NULL`, `upload_date` NULL). Indexed and findable, but no
   meeting page reaches them — see Honest limits. Was 17; the seven whose
   titles carried a date anywhere in them rejoined on 2026-08-18 when
   `catalog.parse_date` stopped anchoring to the front of the string. These ten
   have no date in the title, so the only way in is a per-video `yt-dlp`
   metadata fetch for the real upload date — `catalog.fetch` uses
   `--flat-playlist`, which does not expose it.
6. **Rejoin the remaining split meeting-days** that put a day's agenda on one
   `meetings` row and its minutes on another. **88 stranded outcomes as of
   2026-08-13**, down from 964: making the portal-event link prefer the meeting
   an event names (gotcha 79) recovered 876 of them, so what is left is the
   tail rather than the original problem. `minutes.orphaned_outcomes` counts
   them. Note the root cause in gotcha 78 — `(date, body)` does not identify a
   meeting, and 74 date+body pairs in this archive carry several rows.
7. **Read the clerk's queue as a queue.** `speaker_id.ANNOUNCE` names the
   speaker after next, so every public commenter in a queued list wears the
   following one's name — see "Public commenters are named one position late".
   244 of 302 announcements. Needs `respeak.sh` after.
8. **Work the split-voice reviews — now 394 rows, and they are six decisions.**
   The 457 became 14 after the respeak, then 3, then 394 when the chair anchor
   was restored and exposed the impostors it had been masking (gotcha 86).
   Every row is the anchored cluster beside another one wearing the same name,
   over 23 clusters; Starkey vs 126 (86 meetings), Oakley vs 92 (90) and
   Oakley vs 126 (44) are the top three. `/admin` orders them by utterances
   affected and puts the evidence beside the write. What remains is a human
   listening: the affinity verdicts on screen say which voice is the impostor,
   but only an ear can say who it is. Start with 126 — this file already argues
   from its own words that it is the clerk.
9. **The three doors** the maintainer prioritised on 2026-08-12 — forward
   time, money, votes. Specified in `UI_REQUIREMENTS` §5.9, with the data side
   under "Three doors" above. Order: forward time (a cron job, not an
   extractor), then money (deterministic, do not sum), then votes (blocked on
   the roll-call split).

10. **Split the merged roll-call utterances.** 897 of them contain the clerk's
    call and the member's answer in one row. Blocks the votes table, and is
    why the clerk wears a commissioner's name in 170 voices.
11. Smaller: truncated meeting titles; 404 image-only agendas needing OCR; the
    ASR gap tail (median 62s/meeting, worst 967s).

Note the old entry "phase filter in `ask.plan()`" is deliberately dropped: D9
says stop adding stages and arguments to the fixed pipeline. A phase filter is
a tool parameter now, and as of slice 3 it literally is: `search_transcript`
takes `phase`.

One more, found while building search and NOT acted on unilaterally: **public
comment carries speakers' home addresses**, read aloud at the podium as the
county requires. They are public record and they are also now the top hit for
some searches, which is a different exposure from a PDF nobody indexes. D3
names `SpeakerChip` as the choke point for a redaction rule; there is no rule
yet, and whether to have one is the maintainer's call, not a bug to fix
quietly.

## Public commenters are named one position late

Found by the maintainer on `/item/31300`, 2026-08-12, and confirmed in the data
and in the code. Two separate defects sit on top of each other in the same
three lines of transcript, which is why the page looks so wrong there.

**The clerk queues speakers, and the queue is read one position late.**
`speaker_id.ANNOUNCE` matches only `followed by X`. But "followed by X" names
the speaker AFTER next; the person about to speak is the bare name in front of
it. On 11 August 2026:

| utterance | the clerk says | who actually speaks | shown as |
|---|---|---|---|
| 53 | "Elaine Lance, followed by Anthony Sikhenes. Followed by Nancy Hazelwood." | Elaine Lance | **Anthony Sikhenes** |
| 59 | "All right, Anthony Sickenes and uh followed by Nancy Hazelwood." | Anthony Sikhenes | **Nancy Hazelwood** |
| 65 | "And Nancy Hazelwood." | Nancy Hazelwood | **nobody** |

Each commenter wears the next one's name, and the one the announcement could
have named correctly is unattributed — because "And Nancy Hazelwood." has no
"followed by" in it and matches nothing. `speaker.queue_announcement_offset`
now counts it: **244 of 302** queue announcements carry a lead name that the
pattern never sees.

The fix is to capture the lead name and assign THAT to the next speaker, with
the `followed by` names held for the speakers after — the announcement is a
queue, so it should be read as one. Note the failure is silent in exactly the
way gotcha 58 warns about: the pattern matches, it just matches the wrong half,
so nothing anywhere reports a miss.

**The clerk is wearing a commissioner's name.** Utterances 53, 59 and 65 are
shown as **Starkey** at voice confidence 0.978. They are the clerk: the same
voice says "Mr. Chair, that's all that I have signed up", calls the roll
("District one, Commissioner Oakley") and reads legal advertisements. In that
one meeting "Starkey" sits on TWO clusters — 126 (54 utterances, the clerk) and
192 (52 utterances, presumably her). Archive-wide, cluster 126 is named Starkey
in 100 videos and **Oakley in 48**, so it is not one person; twelve clusters
carry two or more commissioner names, about 70,000 utterances between them.

`speaker.one_voice_per_meeting` has been reporting this the whole time — **457
to review** — and that is a defect in the CHECK, not in whoever read it. It
emitted an unordered list with no sense of which rows cost anything, and the
console for acting on them (§5.8, `/admin`) did not exist yet (it does now —
slice 6), so there was neither a queue nor anywhere to work one. A review
check that cannot be
actioned is a check that reports into a void. Both review lists now order by
how many utterances each row actually affects, so the top of the list is the
most expensive misattribution rather than an arbitrary one.

Neither is repaired here. Both would invalidate the derived layers, and the
naming fix in particular wants `respeak.sh` and a re-measure rather than a
patch — see `Next`.

## The LLM account ran out of credit

2026-08-12, mid-run. `HTTP 402 Insufficient Balance` from the inference API.
Two things stop until it is topped up, and both fail loudly rather than
silently, which is the one good thing about it:

- **`bin/name_speakers.py`** — the stage that names voices the text signal
  could not reach. `bin/respeak.sh` runs it between `speaker_id` and
  `index_passages`, and `set -euo pipefail` means the chain aborts there, so
  `index_passages` does not run either and the passage text keeps stale names.
  Recover by running `speaker_id --write`, then `index_passages`, then the
  audit; add `name_speakers` back once there is credit.
- **`/ask`** — `web/agent.py` cannot make a single call. The page surfaces the
  refusal rather than hanging.

Everything else in the project is local: Postgres, the embedding model on
cuda:1, retrieval, the whole UI. `/`, `/search`, `/meeting`, `/item` and
`/case` are unaffected.

~~The key lives in `../active-reading/env.local.sh` as `LLM_API_KEY`.~~
**Superseded by gotcha 88.** It lives in THIS repo's `env.local.sh` now
(gitignored, mode 600), along with the DSN, `INFERENCE_API_BASE`, the model
names, `PASCO_EMBED_DEVICE` and `ARCHIVE_API`. Splitting it across two repos is
what left `web/server.py` running without a key while every CLI worked, and
that is what made a configuration fault look like an Ask bug. Nothing outside
this directory is needed to run the archive.

## Three doors: forward time, money, votes

Prioritised by the maintainer on 2026-08-12. Specified as `UI_REQUIREMENTS`
§5.9; this is the data side. The framing is the maintainer's and it is a
correction to how the whole UI was scoped: **work out what a resident needs to
see, then work out what to mine for it** - not the reverse. Every surface built
so far is archive-shaped, and residents arrive with questions about their own
situation.

### Forward time - the work is a cron job, not an extractor

**35 meetings** *(37 when re-measured a day later — see below)* **are on the
county's calendar and NOT ONE has an agenda in this
archive.** The county posts agendas days before a meeting; the last
`bin/civicclerk.py` run predates all 35. Nothing downstream needs to learn
anything - `parse_agenda`, the item rows and the coverage chips already work.
The whole task is that fetch on a schedule.

This is the cheapest of the three and the only one that lets a resident ACT
rather than check.

**Re-measured 2026-08-13 15:35, immediately after a full portal sweep from
`/admin/ops`: 37 scheduled meetings, still 0 with a published agenda.** So the
sweep is not the fix - the SCHEDULE is, and the distinction is not academic.
The nearest scheduled meeting is 2026-08-20 and the county's median lead time
is 3 days, so nothing at all lands until about the 17th no matter how often
the button is pressed today.

Two consequences. `bin/forward.sh` is written, idempotent and verified, and it
is installed nowhere - `crontab -l` reports no crontab for this user, so the
job that the whole door depends on has never run unattended. And the surface
(R5.9.1) has nothing real to render yet: built against today's data it would
be 37 rows of "the county has not posted this agenda", which is R5.9.2's
honest state and is not a door anybody walks through. Install the schedule
first, let it catch a few agendas, then build the surface against real items.

### Money - deterministic, and one trap

**6,778 of 23,122 published items (29%) carry a dollar amount in the title.**
Regex-parseable, no model, no network, fully auditable.

**Do not sum them.** A grant received, revenue, a reimbursement and a purchase
all appear as a dollar figure in the same field, so any total is confidently
wrong while looking authoritative. Per-item is safe; an aggregate needs an
extractor that can say which direction the money moved. This is the one place
in the three where the obvious implementation is worse than none.

### Votes - blocked, and the block is real

The two sources are severely asymmetric, and this was verified rather than
assumed:

| | what it holds |
|---|---|
| the minutes | dissent ONLY - 114 items say "voting nay". No tallies. **Never** who moved or seconded: 0 across all 23,122 published items. |
| the transcript | the roll call read aloud **1,605** times, **612** motions, **823** seconds |

So a complete voting record is transcript-only and reaches at most the **9%**
of decided items with a recording. A `/person` page implying a full voting
history would be the largest overclaim the site has ever made.

**It must not ship before the roll-call segmentation fix.** 897 utterances
contain the clerk's call AND the member's answer in one row - "District three,
Commissioner Starkey. Aye." - so no per-utterance attribution can be right
about them (`speaker.rollcall_merged`). Building a votes table on that would
convert a transcription defect into structured data, which is far harder to
spot and far harder to reverse than the defect itself.

### Where a mined fact goes

Proposed and NOT yet built: an `item_facts` table rather than more columns on
`agenda_items` - `(item_id, kind, value, value_num, source, extractor,
evidence, confidence)`. `evidence` carries the substring the fact was parsed
from, which makes it checkable against the source PDF `/item` already renders.
The rest follows: provenance per fact rather than per table, one audit
invariant per kind, and a single extractor droppable and re-runnable by name.

Try it in `bin/sandbox.py` on five meetings before it touches 23,122 items.

**The admission rule**, so this does not become a properties table (§10 refuses
one): a fact earns a place only if it traces to a substring of a document we
hold, an audit invariant can say when it is wrong, AND a named surface changes
because of it. Applicant entities (43% of titles), dwelling units, square
footage and section/township all pass the first two and fail the third.

**Place is deliberately not on this list.** 21% of titles carry a road name and
9% a compass phrase ("South of County Line Road North and East of Lake Iola
Road"), but the addresses are prose and there are no coordinates. It needs the
geocoding spike `UI_PLAN` §7 already asks for. Above ~50% coverage it is the
best thing on the roadmap; below that it is a map with holes, which is worse
than no map.

## Honest limits

*Numbers here were re-measured against the live archive on 2026-08-13, after
the identity chain was re-run with `chair_anchor` restored (gotcha 86). Earlier
figures in this file — 84.9%, 78.8%, 53.5% named — are the record of how it got
here, not claims about the archive as it stands.*

- The audit checks **consistency, not correctness**. It cannot tell you a
  boundary is in the right place or that a voice really is Oakley. It now at
  least admits when it checked nothing (`EMPTY`), which is a different
  weakness it used to hide. **45 checks, 0 failing, 3 EMPTY, 6 carrying review
  items**, as of 2026-08-13.
- **Speaker precision has never been re-measured.** The 0.78 above predates the
  roster work, the Postgres migration and ~200 meetings of growth. Measuring it
  properly needs ground truth this project does not have: the human labels and
  the published rosters are both *inputs* to the assignment, so scoring against
  either is circular. What *is* now measured: **0 cross-body attributions**
  (was 54,000), 100% of utterances clustered, and **68.3% named** (204,146 of
  298,737).
- **4.6% of board-member-named utterances (7,961 of 174,161) fall outside the
  person's strict term** and are admitted only by the `-120/+400 day` widening
  in `voice_name` and `speaker_id.load_rosters`. Concentrated in two Planning
  Commission voices reaching about a year past their last agenda. Whether those
  names are wrong is unknown without ground truth.
- **94,591 utterances (31.7%) resolve to no name** and display as `Group N` or
  the raw diarization label. It is the next thing a reader will notice. This is
  NOT the floor of the method: `name_speakers` has never run against the
  current archive because the inference account is empty, and the last figure
  measured with it was 78.8%. The floor of the free stages is what is on screen
  now.
- **10 of 173 Planning Commission agendas (all 2015) still do not parse** —
  two members per line, roman-numeral districts, fields wrapped mid-value.
  Those meetings get no roster, which now means *unidentified* rather than
  *wrong*.
- **ASR is not silently deleting.** Checked against diarization as an
  independent witness across 351 videos / 701 h of speech: 1.68% of diarized
  speech has no transcript, in 11,811 holes under 15s, 40 between 15-30s, one
  above 30s, and **nothing above 45s**. The catastrophic Parakeet mode drops
  minutes at a time; it is absent. Sampled content in the holes is off-mic
  crosstalk that ASR correctly declines to invent words for. `bin/audit_asr.py`.
- **404 of 1,161 portal agendas are image-only** (under 2,000 chars extracted)
  and need OCR. 77 transcribed meetings therefore have no published agenda and
  rely entirely on transcript-derived items.
- Binding is 100% on public hearings and resolutions, 80% consent, 58% regular.
  Board reports have no agenda code at all and never will.
- 24% of agenda items have no disposition — mostly regular-agenda and board
  reports the minutes simply do not dispose of in writing.
- **`agenda_items.disposition` is `agenda_items.outcome_text`** (2026-08-18).
  The column holds the minutes' own sentence; `outcome` is that sentence
  classified and `outcome_source` says where it came from, so the family reads
  `outcome` / `outcome_text` / `outcome_source`. Renamed because the county
  uses "disposition" for disposal of records and property — 19 of 791 minutes
  files contain the word and every one means DISPOSAL — so the reader-facing
  copy dropped it first and the schema followed before launch. The rename lives
  in `bin/schema.sql` as a guarded `DO $$` block, and it MUST stay above the
  `ADD COLUMN IF NOT EXISTS outcome_text` line: the other order creates an empty
  column, skips the rename, and strands 17,532 sentences. The `agenda_fts`
  expression index followed the rename on its own, as Postgres does. Verified
  by md5 over the column before and after — identical. Two audit checks were
  renamed with it (`outcome.matches_text`, `minutes.no_subsidiary_outcome`);
  STATE.md entries above this line use the old names.

- **Numbers quoted to a model are measured, never typed** (2026-08-18).
  `tools.facts()` measures every count the tool descriptions and the two system
  prompts quote — hours, agenda items, recurring cases, the 9% of decided items
  the transcript reaches, the 67% of passages with no usable name, the year
  span — in one query cached for an hour, and `tools.fill()` / `tools.reflow()`
  substitute and rewrap them. `tools.MANIFEST` is gone; it is `manifest(con)`,
  because a constant built at import is exactly how the old numbers froze. They
  had drifted: "23,122 agenda items" was 23,130, "1,377 cases" was 1,378, and
  "recordings start in 2018" became 2017 the same afternoon the catalog parser
  fix attached a 2017 workshop to its meeting. **If you add a number to a
  prompt, add it to `_measure()` — or do not state it.** Two definitions are
  not the obvious query and are commented where they are used: `pct_transcript`
  counts decided items with a BOUND span (9%), not items whose meeting was
  filmed (65%); `pct_no_name` counts `(exchange)` passages as nameless
  alongside NULL, which is what makes it 67% rather than 10%. The MCP
  handshake's `instructions` is the one exception to the hourly refresh — the
  SDK takes it as a plain string on the Server object, so it is as fresh as the
  process.

- **10 transcribed recordings belong to no meeting, and that is the difference
  between the two hour figures.** The archive holds **1,036 hours** of
  transcribed video; `/` reports **1,013h**, because it counts only recordings
  attached to a meeting that has been held. The 23-hour gap is 10 videos with
  `meeting_id IS NULL` — all workshops, and all of them titled with no date at
  all ("Planning Commission Workshop: 2050 Comprehensive Plan" × 6, three BCC
  workshops named only by subject). `upload_date` is NULL for every one, which
  is why `segment.day_groups()` cannot place them: there is no date to group a
  meeting-day by.

  **This was 17 recordings and 39 hours until 2026-08-18.** `catalog.parse_date`
  read the date only at the START of a title, so the county's freehand workshop
  titles — "Pasco BCC Legislative Workshop (8.24.23)", "Board of County
  Commissioners Emergency Mtg 09-24-2024", "Pasco County BCC Workshop, October
  17, 2017" — all read as undated, and `upload_date` is the only thing
  land_agenda.py joins a recording to its meeting on. The parser now scans the
  whole title, accepts a spelled-out month, and takes the first VALID date
  rather than the first thing shaped like one (`0.7.08.2021` parses as
  2021-07-08). `bin/catalog.py --redate` re-parses catalogued titles without
  touching the channel, which is what the ON CONFLICT DO NOTHING insert cannot
  do. Seven recordings rejoined, one of them onto a meeting row upsert_meetings
  created for it. The remaining 10 carry no date in the title at all and need a
  real upload date from a per-video yt-dlp fetch — nothing in the title will
  reach them.

  The 10 are not lost. Their **6,930 utterances and 3,482 passages are
  indexed**, so the agent finds them and search will; they simply have no
  meeting page and no agenda. Both hour figures are correct — they measure
  different sets — and the reason to keep both is that a reader comparing them
  should find this note rather than a discrepancy.
- **88 items hold an outcome the pipeline can no longer see** — measured
  2026-08-13; it was 964 before the portal-event linkage was made to prefer the
  meeting an event names (gotcha 79), and that fix is what accounts for the
  drop. Meeting-days that exist as TWO `meetings` rows, with the agenda items
  on one and the county's minutes on the other. `parse_minutes` iterates from
  `portal_events.meeting_id`, so it can neither correct nor clear those items.
  Reported by `minutes.orphaned_outcomes` as review rather than failure, and
  deliberately not repaired: the stored dispositions came from real minutes and
  are mostly right, so deleting them would lose the record to tidy a
  bookkeeping error. The linkage is the actual defect.

## The commissioners have no verified voice, and it shows

The largest remaining data-quality problem, measured 2026-08-12.

**Still true on 2026-08-13, and the intervening day is worth reading before the
numbers below.** No commissioner has a human-verified voiceprint, which is the
whole subject of this section. What changed is that `chair_anchor` — the
published-roster method this section proposes — was erased from production by
`speaker_id` and then restored (gotchas 83, 86). So the "Result on 90,353
utterances" table further down describes the state the archive is in NOW, but
it was not the state it was in for most of 2026-08-13, and any measurement
taken in that window will disagree with it. The residue this section hands to
`voices.py` is now also visible as 394 rows in the split-voice queue: putting
the right name on the anchored cluster exposed the impostor sitting beside it.

`speaker.cluster_only_names` reports 65,796 utterances named on archive-wide
cluster majority alone, 44,082 of them "Mariano" across 259 recordings. The
affinity gate (gotcha 45) passed every one of those 678 voices at a mean
similarity of **0.932** - and that was wrong, for a reason worth recording.

**A reference set must be verified before it can verify anything.** Affinity
scores a voice against the named voices of its own cluster, taking the top-3
mean. If the reference set is itself a mixture, any voice resembling ANY member
clears the bar. That is exactly the failure `TRUST_FLOOR` was introduced to stop
in `anchors.py` (gotcha 33), reproduced one script later.

Checking the reference sets directly - every pairwise cosine within a name:

| name | voices | mean pair | pairs < 0.35 | groups at 0.79 | largest |
|---|---|---|---|---|---|
| **Barbara Wilhite** (43 human labels) | 111 | 0.764 | 12.4% | 10 | **102 (92%)** |
| Girardi | 150 | 0.580 | 42.5% | 6 | 103 (69%) |
| Moody | 106 | 0.394 | 61.9% | 4 | 59 (56%) |
| Starkey | 261 | 0.333 | 64.8% | 18 | 103 (39%) |
| Mariano | 223 | 0.281 | **76.0%** | **15** | 69 (31%) |
| Oakley | 275 | 0.253 | 75.2% | 16 | 103 (37%) |

The control rules out the obvious alternative explanation. Voices a HUMAN
labelled, paired across different recordings: Wilhite 43 voices mean **0.898**
(p10 0.796, **0% under 0.35**), Justin Grant 8 voices mean 0.878, Ebro Stevens
0.957. Different people in the same recording: mean 0.110, max 0.219. So the
embedding space survives mic, seat and room change perfectly well for verified
same-person pairs, and Mariano's 0.281 is not channel variance - **76% of the
pairs under his name are, by this project's own ground truth, different people.**

Each group is internally coherent at 0.88-0.91. These are real, separable
people. **The machine can already tell them apart; it cannot say which one is
Mariano**, because no commissioner has a single human-verified voiceprint. The
59 human labels cover Wilhite, Grant, Stevens, Navarro, Baird and Wittmar - not
one board member. Wilhite is 92% coherent for exactly that reason, and she is
the proof that the method works once anchored.

**`bin/chair_anchor.py` closes most of it automatically**, from published
facts and no voice model at all. `meeting_roster` records who held the CHAIR at
each meeting - parsed from the roster block on the county's own agenda - and
the presiding officer reads a fixed script ("now is the time for public
comment", "all in favor say aye"). Whichever cluster speaks that script in the
meetings the agenda says X chaired IS X. The office rotates annually, which is
what makes it discriminating rather than circular: the cluster that follows the
gavel is the one that changes.

    cluster  44   Mariano  77.8% of 482 chair-script lines   confirmed
    cluster 291   Grey    100.0% of 142                      was labelled Cox
    cluster 231   Oakley   89.2% of  74                      was labelled Mariano
    cluster 192   Starkey  92.1% of  63                      was labelled Mariano

An entirely independent method agrees - the lift of each commissioner's name in
the line immediately BEFORE a cluster speaks, solved as a global assignment,
reaches the same conclusions and matches the stored labels on five further
clusters. And in each corrected cluster the right name was **already present as
the runner-up** (231 held Oakley 53, 192 held Starkey 36); the drift had simply
outvoted it. Three signals, no shared machinery.

Result on 90,353 utterances:

| name | pairs < 0.35 before | after | largest group |
|---|---|---|---|
| Grey | — | **0.0%** | 100% (one group) |
| Cox | 41.3% | **0.0%** | 100% (one group) |
| Mariano | 76.0% | **30.9%** | 31% → **81%** |
| Oakley | 75.2% | 61.2% | 37% → 54% |
| Starkey | 64.8% | 50.6% | 39% → 54% |

Oakley and Starkey are improved but still mixed: their names also sit on other
clusters that have no chair evidence. That residue is what `voices.py` is for.

**The critical constraint, and the reason this is safe:** a whole cluster may
only be rewritten if the cluster is measurably ONE person. Of the 120 largest
clusters, 95 are (under 2% of internal pairs below 0.35) and **25 are not**, so
it cannot be assumed - `chair_anchor.is_one_person()` checks before writing and
skips the rest. Rewriting a mixed cluster would stamp the presiding officer's
name over everyone else sharing the bucket.

It writes to `speaker_identity` - the DERIVED layer - and never to
`speaker_label`. It is a machine inference from published facts and must not
claim the authority of a person having listened; a human label still outranks
it, and re-running recomputes it.

**`bin/voices.py` is the tool for the residue**, and it is deliberately a
human workflow:

```bash
bin/voices.py groups Mariano --play          # 15 groups, one YouTube link each
bin/voices.py assign Mariano --group 1 --as Mariano
bin/voices.py assign Mariano --group 6 --clear     # the budget officer, not a commissioner
```

One listen per group labels every voice in it. Seven listens cover 24,000 of
Mariano's utterances. Then re-run `affinity` and `index` - and with real
anchors in place, `respeak.sh` re-derives identity across the whole archive
from a reference set that is finally verified.

Two groups are identifiable from their sample alone, without listening: group
6 opens "I will now provide a summary of the nine voter-approved debt service
millage rates" (the budget officer) and group 5 "There are two rezoning
agendas, regular and consent. Staff will present each application" (planning
staff). Neither is a commissioner.

**Do not add regex name-mining to `voices.py`.** It was tried - people are
required to give their name at the podium, so reading a self-introduction out
of each group looks like free evidence. Measured against twelve real
introductions it caught **two**. The misses were "My name is Marcus Coe", "My
name is Andrew Kelly", "my name is uh JP Lyons, Jimmy Lyons": capitalisation,
filler words, initials, and beyond those, hyphenated names, titles, nicknames
and whatever the ASR did to an unfamiliar surname. The first, looser version
reported a person called "Madam" 107 times; tightening it until the junk
disappeared produced silence, and silence is not correctness. Name extraction
from transcript text already has an owner - `bin/name_speakers.py`, an LLM pass
with verbatim-quote verification.
