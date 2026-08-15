# Rewriting the derive pipeline — plan, measurements, and traps

Written 2026-08-13, from two research passes and a night of measuring the
running system. Nothing here has been built. It is a plan to pick from, not a
specification to follow.

Read `STATE.md` first for the invariants; this file assumes them.

**Provenance.** Facts marked **[measured]** were taken from this archive or
its logs and, where they were load-bearing, re-checked by hand. Facts marked
**[reported]** came from a research agent and were not independently verified.
Facts marked **[unverified]** are stated as open.

---

## 1. Why touch it at all

A `fold_in` run on 2026-08-13 took **23m 18s with exactly one recording
pending** [measured, `logs/job.json`]:

| stage | wall | scope |
|---|---|---|
| `speaker_id --write` | 2m17s | all 432 recordings |
| **`name_speakers --limit 150`** | **15m02s** | **paid, archive-wide, unrelated to arrivals** |
| `segment` (incremental) | 2m24s | **the 1 pending recording** — the only correctly-scoped stage |
| `bind_spans(redo)` | 1m00s | archive-wide |
| `index_passages` | 1m36s | all 167,225 passages, 4,352 re-embedded |
| `audit` + `eval_agent` | 59s | archive-wide |

One of seven stages matched the work that was pending. The console reported
"1 transcribed recording is waiting", which described the *trigger*, not the
job — on a button marked "calls the paid model".

Arrival rate for context: **~6 recordings and ~15 meetings a month**, roughly
one meeting-day a week [measured]. The pipeline re-derives the whole archive
for it.

**The cost is not the problem.** The paid stages of that run cost about
**15 cents** [reported]. What it costs is twenty minutes of an operator
waiting, a global lock held, and an archive mid-rewrite.

---

## 2. Structural problems, in order of how much they hurt

1. **`fold_in` and `name_chain` are the same paid work under two names.** Both
   run `name_speakers --write --limit 150`. Running one then the other spends
   twice on the same queue.
2. **`name_speakers` is a backlog drain wired into the arrival path.** Its
   candidate query is archive-wide, ordered by impact, and `MIN_MEETINGS >= 2`
   structurally refuses any voice heard in only one meeting. A new recording
   does not create work for it. It does not belong in a fold.
3. **Eight orderings, not four** [reported]: `catch_up.sh`, `refresh.sh`,
   `respeak.sh`, `rebuild.sh`, `forward.sh`, `finish_chain.sh`, `reindex.sh`,
   `rederive.py`. Two have been patched for the same class of omission —
   `affinity` (gotcha 64) and `chair_anchor` (gotcha 86, 69,596 utterances
   under a contradicted name). Both were "a chain written from what the last
   failure needed rather than from what the layer is made of."
4. **Gates measure triggers, not work.** `_gates()` returns eligibility counts.
   The operator needs rows-to-be-read, model calls, and cents.
5. **The naming gate is wrong by ~90×** [measured]. It reports
   `COUNT(*) FROM speaker_identity WHERE name IS NULL` = 5,086. Three
   different quantities are in play: **5,086 unnamed rows**, **1,773 wholly
   unnamed clusters**, **56 LLM-addressable clusters** (`lines >= 60`,
   `mtgs >= 2`). The page shows the least meaningful one.
6. **Rejections are discarded** [measured]. `name_speakers` keeps
   `rejected` in a local list, prints eight, throws the rest away. 42 of 150
   were rejected on the last run; they will be re-proposed and re-billed on
   every future run for ever. No rejection table exists.
7. **`passages.id` is a positional `enumerate()`** [reported], so any change
   in passage *count* forces `TRUNCATE` + full HNSW rebuild + full BM25
   rebuild. A stable key `(video_id, start_idx, end_idx)` already exists and
   is already indexed; it simply is not the primary key. ~85% of
   `index_passages` is fixed cost independent of the delta.
8. **Nothing detects a changed county document** [reported]. `civicclerk.py
   --text` fetches `WHERE body_text IS NULL` and the manifest upsert never
   touches `body_text`, so a minutes PDF republished under the same `fileId`
   is never re-read. Minutes arrive weeks after the meeting. This is a
   correctness hole, not an efficiency one.
9. **The one-job lock is a JSON file plus a `/proc` check**, not a database
   lock. Gotcha 85 records the incident it permits.
10. **Nothing is scheduled** [measured]. `crontab -l` is empty. `forward.sh`
    is written, idempotent, and installed nowhere.

---

## 3. The three designs

### A — Two registers, two verbs
The atom is the **meeting-day** (`(upload_date, kind)`); 45% of days are two
recordings on one continuous agenda, so a *recording* is not a thing anyone
can reason about. Four operator actions: fetch the county's record; take up a
meeting-day; re-derive identity; name the backlog. Weekly increment **~2m45s**
against today's 23m. No new tables. Smallest change that fixes the misfire.

### B — The reconciler
One `derivation(target_kind, target_id, stage, input_fingerprint, …)` table.
A single idempotent verb computes staleness **from the database** and runs
exactly that. The eight orderings collapse into one declared graph, which is
the only design that *structurally* prevents shipping a chain missing a stage.
Makes staleness a visible first-class state ("identity is 3 recordings out of
date") instead of re-deriving everything on every press. Rebuilds become
resumable. Risk: a fingerprint wrong in the "fresh" direction silently skips
work — this archive's worst-detected failure class. Mitigation is the pattern
the repo already invented: every fingerprint gets an audit invariant that
recomputes it from evidence, as `speaker.chair_anchor_intact` does.

### C — Derive work as queue rows
Extend `db.claim()` — already correct, already in production for the ingest
fleet, with `FOR UPDATE SKIP LOCKED`, stranded-claim reclaim, and an audit
invariant — to the derive stages. Workers grouped by **resource class**: cpu,
one gpu worker holding the embedding model resident (removing the ~6s model
load from every correction), a paid pool with a **daily spend ceiling enforced
inside the claim query**. Weekly operator time becomes **zero**: a recording
finishes ASR at 03:12 and is segmented, bound and indexed by 03:15. A rebuild
becomes a queue fill rather than a truncate — resumable, interruptible,
observable per row. Risk: emergent ordering, which produced gotcha 78; it must
be constrained by claim predicates the way `db.PREREQ` already constrains
ingest.

### The synthesis, and why
**A's atom, C's mechanism, B's fingerprints.**

Prompt caching forces the atom. DeepSeek caches on a **strict prefix from
token 0, including the system message** [reported, from DeepSeek docs]. So
reuse requires one worker to hold one meeting-day and issue all of its model
questions back to back — which is A's atom and literally `claim()`'s
semantics. B earns its place for a reason neither A nor C covers: **applying a
redaction rewrites `utterances.text`, invalidating that day's cached prefix as
a side effect of the pipeline's own output.** Only a fingerprint catches that.

---

## 4. One warm body, many questions

The maintainer's insight — a second question about an already-sent transcript
is nearly free — is **half right, and the half that is wrong changes the
design**.

- Cache hit is ~1/50 of a miss on input [reported].
- **Output is billed at twice the cache-miss input rate, and these completions
  are 98% reasoning tokens** [reported]. A *fully cached* segmentation call
  still costs ~39% of a cold one; once warm, **output is ~97% of the bill**.

> **A follow-up on a warm day costs essentially only its own output.** The
> lever is therefore *ask questions whose answers are short*, not *ask more
> questions*.

**Scheduling is a barrier, not a chain.** Follow-ups share a prefix with the
**prime**, not with each other, so after one barrier they run concurrently.
The barrier costs **zero wall-clock whenever pending days >= workers** (always
true on a rebuild) and about **+77s** on the 1–2 day arrival path, where it
saves under a cent. Rule: take the barrier on campaigns, skip it on arrivals.

**Many calls sharing a prefix, not one merged call.** Reasons, strongest
first: a merged call that returns bad JSON loses every task *and* a full
reasoning generation, while a split retry is a cache hit; merging saves no
output, and output is the entire warm-case bill; each task here has its own
verifier with its own reject granularity (segment rejects a whole day, redact
rejects a span, naming rejects a cluster) and they do not compose; prompt
tuning here is measurably fragile (gotcha 70: adding one field returned null
for everything on the first attempt). The repo already contains the working
pattern — `ask.LENSES` / `read_batch()` — with a *quality* rationale, not just
a cost one.

**Questions worth asking of a warm day**: segmentation (the prime, unchanged);
address redaction (moves coverage from 53% of a day's text to 100%);
per-meeting speaker naming from self-ID and clerk announcements (feeds the
free path; `speaker_id.SELF_ID` is a regex and reportedly misses 81% of
announcement lead names); read-aloud correspondence detection (gotcha 72 —
1,169 lines look like clerk-read letters attributed to a commissioner).
**Not** vote/motion extraction — the minutes are authoritative and already
parsed into 17,531 dispositions, and a transcript-derived outcome would be a
second, weaker source for a fact the county publishes.

---

## 5. Order of operations

Ranked by value per unit of work. **The first four are not the redesign.**

1. **Schedule backlog campaigns off-peak.** DeepSeek pricing changes
   2026-08-16 16:00 UTC with peak windows 01:00–04:00 and 06:00–10:00 UTC
   [reported — verify against the pricing page before acting]. The last full
   segmentation run started 08:13 UTC, inside the new peak. Reportedly worth
   more than the entire cache redesign, for one `at` command.
2. **Delete the `name_speakers` line from `catch_up.sh`.** Removes 15 of 23
   minutes and the fold/name duplication. One line.
3. **`name_speakers.MAX_WORKERS` 4 → 12**, matching `segment`. 15m → ~5m.
4. **Fix the naming gate** to report the stage's own candidate count (56), and
   **persist rejections** so 42 clusters are not re-billed for ever.
5. **Per-call cache accounting.** `ask.USAGE` is a single global aggregate;
   `redact.propose_sections()` and `name_speakers.main()` never call
   `usage_report()` at all. You cannot manage a cache you cannot see, and its
   failure mode is silent by construction.
6. **Stable passage ids** — `UNIQUE (video_id, start_idx)`, sequence id,
   upsert instead of `TRUNCATE`. Unlocks per-meeting indexing.
7. **Lift the 60-word line cap** — required *before* any redaction question
   can ride the day prefix (see traps).
8. **Then the redesign**: shared task-neutral system prompt, per-question
   trailing instructions, day-major claim, prime → barrier → parallel
   follow-ups conditional on `pending_days >= workers`.

Migration is additive in every design: build beside the chains (a **new file**
— gotcha 30), run both and diff `audit.py`, then delete the chain's line when
the new path lands. No flag day.

---

## 6. Traps

- **The 60-word cap hides addresses.** `segment.render()` truncates each line
  to 60 words. **232 address-bearing lines have their address beyond word 60**
  [measured]. Riding the segment prefix for redaction without lifting the cap
  is a *recall regression* on the one task where a miss is the harm.
- **A different system prompt defeats the cache entirely.** Prefixes diverge at
  token 0. Each task's rules must move verbatim from its system prompt into
  its trailing user message.
- **Never merge the lexical and contextual redaction passes.**
  `redact.cross_check()` measures recall by *disagreement* between them. It is
  the only recall signal on the archive's most sensitive task, and it works
  precisely because the two are independent. It has already caught real
  long-context misses the section pass made silently.
- **Applying redactions invalidates a day's prefix** — fingerprints must
  include redaction state.
- **Concurrent same-prefix calls probably do not warm each other**
  [unverified — DeepSeek's docs are silent; assume they do not].
- **`MIN_MEETINGS >= 2` is a corroboration gate, not an exclusion.** A
  day-scoped naming question must *feed* it, never bypass it. Naming
  single-meeting voices without corroboration manufactures the Mariano
  failure at scale, on private citizens.

---

## 7. Invariants any rewrite must not regress

- **>= 653 contextual-only redaction proposals.** The section pass produced
  3,121 proposals, of which **653 the pattern pass would never have flagged**
  [measured]. A redesign producing fewer has regressed the contextual pass's
  entire reason for existing.
- **Byte-identical segmentation on the fixtures.** Gotcha 71 measured three
  runs of one meeting-day returning identical codes at identical line numbers.
  Re-segment the 5 `bin/sandbox.py` fixtures under any new prompt shape and
  require identical `code` and `line` values. Reportedly ~4¢.
- **`audit.py` at 0 failing checks**, including the three redaction
  invariants.
- **Human decisions survive everything**: `speaker_label`, `speaker_override`,
  `redaction` are in `rebuild.sh`'s KEEP list and must stay there.
- **Per-verifier acceptance rates should be stored per run and diffed** —
  `name_speakers` accepted/rejected, `segment`'s indexable and matched counts,
  `redact.verify()`'s moved/dropped. Today they are printed and thrown away.

---

## 8. What is already right — do not "improve" it

- `bm25_refresh(ids)` + `refresh_video()`: a correction reaching the vector
  and the postings in ~8s, with a boundary-move assertion that refuses rather
  than corrupting. Everything above is downstream of it.
- `vec_cache` keyed on content hash — why a full re-index is 96 seconds.
- `rebuild.sh`'s KEEP/DROP exhaustiveness check, and its inference preflight
  before the truncate.
- `rederive.py`'s snapshot → verified-restorable backup → measured diff →
  revert. Every job that rewrites a derived layer should have this.
- Prerequisites measured from the database; refusals that state the
  measurement.
- Human decisions guarded in each stage's own SQL *and* independently in the
  `utterance_speaker` view, with an audit invariant on top. Do not consolidate.
- `segment.py`'s meeting-day grouping and its `fit()`-instead-of-chunk
  approach.
- **Parsers stay parsers.** `parse_agenda`, `parse_minutes`, `speaker_id`,
  `chair_anchor`, `roster` call no model. Keep it that way.
- The two registers stay separate. Scheduling is orthogonal to that.
