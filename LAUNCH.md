# Launch plan

State as of 2026-08-14. `audit.py`: **0 failing of 47**, 3 examined nothing,
6 with items to review — run live on 2026-08-14, not quoted.

Target architecture: **persistent services on the Unraid box (64 GB, Docker),
GPU workers on this workstation.** Everything below was measured on this machine
today; nothing is from memory.

---

## 1. The split

### Unraid — always on, holds everything a reader touches

| service | notes |
|---|---|
| **Postgres 17 + pgvector** | 5.8 GB. `pgvector/pgvector:pg17` covers it. |
| **Reader API** (`web/server.py`) | CPU-only. `PASCO_EMBED_DEVICE=cpu`. |
| **Next.js UI** (`next start`) | Pure Node, no native deps. |
| **Nginx Proxy Manager** | Already running (:80, :81, Let's Encrypt store in appdata). TLS + the public hostname — `deploy/nginx-proxy-manager.md`. |

Budget ~6 GB RAM: Postgres, the 0.6B embedding model (~2.5 GB resident), Node.

### This workstation — on when you're working, never in the read path

ASR, diarization, bulk embedding, `bin/job.py` campaigns, and the `/admin` ops
page. `PASCO_DSN` points at Unraid over the LAN. `data/` (111 GB of source
recordings) stays here.

### Why this works

- **The reader API touches no content on disk.** Its only `open()` serves a
  static file from `web/` itself; all 167,225 passages come from Postgres.
- **The site serves no media.** Playback is a YouTube iframe
  (`youtube-nocookie.com`, `PlayerProvider.tsx:150`). Those 111 GB never need to
  be reachable from the internet.
- **No exotic Postgres.** pgvector 0.8.6, pg_trgm, and `bm25(text,int)` as a
  plain plpgsql function that travels inside a `pg_dump`.
- **Query encoding does not need a GPU.** Measured: **~50 ms on CPU.** The GPU is
  for bulk-embedding 167k passages, not for one query.

### The one thing that changes: query-side float precision

Bulk embedding stays GPU/fp16, so the corpus stays internally consistent. Only
the query encoder moves to CPU/fp32. Same model, same weights, same
`prompt_name`, nothing re-embedded. Measured over 20 queries:

| change | top-10 set | exact order | worst cosine |
|---|---|---|---|
| dtype only (gpu fp16 → gpu fp32) | 99.0% | 50% | 0.999912 |
| **device only** (gpu fp32 → cpu fp32) | **98.0%** | 50% | 0.999891 |
| both (today → proposed) | 98.5% | 50% | 0.999913 |

Control: re-encoding the same query twice is **6/6 bit-identical**, and HNSW
returns **6/6 identical order** — so the pipeline is deterministic and those
numbers are real, not jitter.

Moving to CPU perturbs results *no more than flipping a dtype flag on the GPU you
already have*. The top-10 **set** is 98% stable; what moves is ordering among
passages already tied within noise.

**Bit-exact across machines is not achievable.** CPU fp16 vs GPU fp16 is 0/4
identical — different kernels accumulate in different orders. "Identical
everywhere" can only mean "one machine," which would make a desktop load-bearing
for public search.

---

## 2. Migration

1. **Dump, freshly, on the day you cut over.** `pg_dump "$PASCO_DSN" -Fc -f
   pasco-$(date +%F).dump`. The 2026-08-13 file is a *backup*; it is not the
   migration input. Everything decided since then — redaction decisions,
   speaker labels, whatever the ops page has run — exists only in the live
   database, and restoring the older file would quietly roll back human
   curation that cannot be regenerated at any price. Take the dump with nothing
   writing: no fleet, no `bin/job.py`, no open `/admin` session. Then compare
   row counts on both sides, which step 8 does properly.
2. **Postgres container** on Unraid from `pgvector/pgvector:pg17`. Give it a real
   password: it is about to listen on the LAN, not a Unix socket.
3. **Restore.** Raise `maintenance_work_mem` first — the restore rebuilds
   `passages_embedding_hnsw`, **1,304 MB over 167,225 × 1024-dim vectors**, and
   that is the slow step by a wide margin.
4. **Watch for a collation warning.** If the container's glibc/ICU differs from
   this host's, Postgres will say so on first connect. Do not ignore it —
   `REINDEX` the text indexes if warned, or text comparisons go subtly wrong.
5. **Reader API container.** Install **CPU-only torch**
   (`--index-url https://download.pytorch.org/whl/cpu`) — a fraction of the 5.3 GB
   `emb-venv` here, which carries CUDA it will never use. Mount a volume for the
   HuggingFace cache so the 0.6B model downloads once.
6. **UI container.** `next build` then `next start`. Pass `ARCHIVE_API`,
   `SITE_URL` and `SITE_CONTACT` as real environment variables — `ui/.env.local`
   is gitignored and will not be in the image, and a real env var overrides it
   anyway (§3.3).
7. **One NPM proxy host** for `pasco.watch`, pointed at the UI container — the
   settings, the two `/admin` 404s and the `/api/ask` streaming overrides are in
   `deploy/nginx-proxy-manager.md`. Then `ASK_TRUST_PROXY=1`, **only** once NPM
   is really in front and setting `X-Forwarded-For` (§3.5).
8. **Repoint this workstation.** `PASCO_DSN` → the Unraid host. Then run
   `bin/audit.py` from *both* machines; all 47 checks should pass from each.

Bulk embedding now writes vectors over the LAN. Fine at ~4–8 new recordings
twice a week; it would not be fine for a full rebuild.

---

## 3. Blockers

### 3.1 Backup and version control — two steps left

- **Code:** `git init` + pushed to `Tostino/pasco-info` (public). `.gitignore`
  covers `env.local.sh`, `.admin_token`, `data/`, `logs/`, `*-venv/`,
  `node_modules/`, `.next/`.
- **Data:** `pasco-2026-08-13.dump` — 2.7 GB custom-format, 156 TOC entries,
  `.sha256` alongside, 4m45s to produce. Written to `/media/user/Data`
  (nvme1n1p1) because PGDATA is on `/` (nvme0n1p3), so one drive failure cannot
  take both.

**Still to do: get that dump off this machine.** A second copy on a second disk
in the same box is not a backup. It belongs on the Unraid array — which is also
step 1 of the migration, so it pays for itself.

**A dump has two jobs and they are not the same job.** As a *backup* it wants
to be off this machine today and refreshed on a schedule. As the *migration
input* it must be taken immediately before the restore (§2 step 1), because
every hour between the two is human curation the restore would discard. Do not
migrate from the 08-13 file because it is already sitting on the array.

**The code backup has a hole: only the initial commit is pushed.** `git status`
shows 26 files modified or deleted on top of `beafa0f` — the whole legacy
teardown, the redaction layering (gotcha 97), `bin/redact_job.py`, the
`/admin/redactions` screen, `deploy/nginx-proxy-manager.md`. `Tostino/pasco-info` has none
of it, so today's work is as unbacked as the database was. Commit and push
before the migration, not after.

The database is ~1,036 GPU-hours of transcription, 432 recordings, 298,737
utterances, 72 human speaker labels, 3,439 redaction decisions. The human
curation cannot be regenerated at any price.

Worth adding once Postgres lives on Unraid: a scheduled `pg_dump`, since
nothing schedules one today.

### 3.2 Domain — registered; DNS not automated

**`pasco.watch`**, registered. `SITE_URL` is set to `https://pasco.watch`, which
is what `robots.ts`, `sitemap.ts` (1,255 URLs) and every canonical + OpenGraph
tag read. Point the A record at the WAN address in front of NPM, and see §4
for what keeps it current — nothing does today.

The site name and the codebase name are deliberately different: readers here are
Pasco residents and the site should say so, while the code is generic
(`civic-watch`). That separation is the multi-county architecture — one codebase,
per-instance config — and it costs nothing as long as the two are never
conflated. `hillsborough.watch` needs no new repo.

### 3.3 Rebuild the UI — and the reason it was emitting localhost

**The env file the UI needs was never read by the UI.** `SITE_URL`,
`SITE_CONTACT` and `ARCHIVE_API` lived in `env.local.sh`, which is a shell
script for the Python side; Next is started by `npm --prefix ui run dev` with
nothing sourcing anything in front of it, and `/proc/<pid>/environ` on the
running dev server held none of the three. So §3.2's "`SITE_URL` is set" was
true of the file and false of the app: every canonical link, all 1,255 sitemap
URLs and every Open Graph tag still said `localhost:3000`, and `/about` showed
no contact because `SITE_CONTACT` was undefined rather than empty. **A variable
is not set until the process that reads it can see it** — check
`/proc/<pid>/environ`, not the file.

Fixed 2026-08-14: the three moved to `ui/.env.local`, which Next reads itself
under dev, build and start alike, and which a container's real environment
overrides. `env.local.sh` now carries a pointer where they were. Verified live
against the running dev server: `/about` renders the mailto, `/about`'s
canonical is `https://pasco.watch/about`, and `robots.txt` advertises
`https://pasco.watch/sitemap.xml`.

**The rebuild is still required.** Metadata is baked at build time, `ui/.next`
was built when the value was localhost, and that build is stale for other
reasons (17 source files newer than `BUILD_ID`). `npm run build`, confirm it
compiles, then switch off `next dev` (migration step 6). `.env.local` is
gitignored, so the deploy must pass all three as real environment variables.

### 3.4 `SITE_CONTACT` — DONE

`adambrusselback@gmail.com`, set 2026-08-14. `/about` renders it as a plain
`mailto:` with the address in the link text, so it will be harvested once the
site is public. If that becomes a problem, a forwarding alias on `pasco.watch`
is a one-line swap in `ui/.env.local` and no code change.

### 3.5 `ASK_TRUST_PROXY=0` behind any proxy collapses rate limiting

With any proxy in front — a tunnel, nginx, Caddy, even on this same box — and
this at `0`, `client_ip()` sees one address for
**every** visitor, so "6 questions per 10 minutes per person" becomes 6 per 10
minutes for the entire internet. Set it to `1` only once the NPM proxy host is
really in front — it is a trust switch, and turning it on with nothing in front lets a
caller forge their address and bypass the limit entirely.

### 3.6 Nothing is supervised

Docker restart policies cover Unraid. This box needs nothing — if it is off, the
site stays up. That is the point of the split.

---

## 4. Decide on purpose

- **DONE, 2026-08-14: all 3,440 removals applied.** 3,237 lines changed across
  370 recordings, re-indexed as it went, 1,413s. `redaction.gone_from_transcript`,
  `raw_preserved` and `utterances.published_is_derived` all pass over the full
  3,440.

  **One address is still findable, and it is the case no per-line detector can
  reach.** `14720 Bluestone Lane` (video `OiEdE83k8HA`): the speaker says it
  twice. The first time is one utterance and it is redacted. The second time the
  ASR splits it — utterance 158 ends `...located at 14720`, utterance 159 begins
  `Bluestone Lane in Odessa, Florida` — so no single line contains the string and
  `redact.py` never saw it. `passages` concatenates 157–159, which reassembles it,
  and `redaction.gone_from_index` catches it there.

  This is not an index fault: `build_passages` reads the published column and a
  re-index of that recording correctly changes nothing. The fix is a decision on
  the queue about the split occurrence, not a repair. LAUNCH.md already predicted
  the shape of it — `truth_6680` has an address split across lines 72/73.

  The other five `gone_from_index` violations are benign: the span appears in a
  different, correctly-unredacted line of the same passage (`Green Key`,
  `Frontier Drive`, `Embassy Boulevard`, and a one-character span `A`). Of the 84
  `unfindable` violations, five are address-shaped and read as business
  addresses — "owner of North American Towers", "for the applicant", "I work for
  Soho Builders" — which §4 above already calls correct keeps.

### Reviewed 2026-08-14 — what the proposals actually look like

Structural invariants, all 3,439: **0** spans that are not verbatim in their
line, **0** empty spans, avg span 39 chars, max 138.

Recall, measured against an independent signal (lines saying "I live at" /
"my address is"): 1,130 such lines, **1,064 carry a proposal (94.2%)**. Of the
24 uncovered lines that still show an address, 9 of the 10 sampled are
*correctly* kept — two planning firms and one speaker saying "my **business**
address is" — professionals stating a firm address, not a home.

Two real defect classes, both to fix BEFORE applying:

1. **The 24 bare-city spans are NOT a defect.** Each is paired with its own
   street-address span on the same line, both from the section pass:
   `[13738 Wexford Avenue]` + `[Hudson Beach]`, `[3633 Keswick Road]` +
   `[Palm Harbor]`. The line reads "13738 Wexford Avenue **in** Hudson Beach",
   and the prompt requires a span copied character-for-character holding *only*
   the address — so one contiguous span would have to swallow the connective
   "in". Splitting is the only correct move available. Applying both gives
   `[address removed] in [address removed]`. Clumsy, correct, leave them.
2. **The misses are RUN-TO-RUN VARIANCE, not prompt wording.** This was measured,
   and it overturned the first diagnosis. Ten sections (5 known-issue, 5
   known-good), each adjudicated four times — old prompt ×2, revised prompt ×2:

   - `31251 Ashmont Road` (the worst miss found — production kept the second property
     and dropped the primary home) is **absent from the stored proposals but
     caught by BOTH re-runs of the unchanged prompt.**
   - Every other known miss — Halverson Way, Pinehaven Drive, Dunmore Drive,
     Ellerby Avenue — is caught by old and revised alike.

   The revised prompt gained recall on **zero** targets, and cost: it fragmented
   `[9926 Halverson Way in Port Ritchie]` into two spans (yielding
   `[address removed] in [address removed]`), emitted a junk span `[New]`, and in
   seg 3666 removed neighbourhood names (`Longleaf`, `Meadow Point`,
   `Timberlake Estates`) while dropping a real address. **Reverted.**

3. **MEASURE SPANS BY POSITION, NOT BY STRING.** Comparing proposal spans as
   exact strings said a re-run reproduced only 147 of 164 — a 10% variance floor.
   Comparing them by character-range overlap in the line says **161 of 164, 1.8%**.
   The difference was trailing periods: stored `[3204 Ravenswood Drive.]` vs
   re-run `[3204 Ravenswood Drive]`. One of the surviving three is
   `[128 East Wexford Avenue]`, the planner's *business* address, which the re-run
   correctly declined — so the true disagreement is 2 spans in 164.

   Two fresh runs surface 6 spans production missed, deduping to 4 distinct
   findings, of which **2 are real** (`31251 Ashmont Road`, `4055 Ellerby Parkway`);
   the others are a number fragment and a community name.

4. **Ground truth: `eval/truth_6680.json`.** Section 6680 (94 lines, public
   comment on dredging/short-term rentals/speed limits) labelled by hand from the
   full text — **32 residences**, plus an explicit `must_not_be_proposed` list
   (GHWA, Ellerby Parkway, Dunmore Harbours, the statute number). Every span verified
   verbatim. Chosen because it is the hardest section available: the clerk reads
   a 13-person roster at lines 47–48, the mic fails, and he reads the whole
   roster AGAIN at 61–63 with different ASR renderings (`4651-Ellerbee Drive` →
   `4651 Ellerby Drive`, `Dunmore Rose` → `Dunmore Road`). One address is even split
   across lines 72/73.

   Scored by how much of each address is actually removed, not by string match:

   | run | fully removed | connective left | untouched | false pos |
   |---|---|---|---|---|
   | production (stored) | 31/32 | 0 | 1 | **0** |
   | one run | 30/32 | 0 | 2 | 0 |
   | **two runs unioned** | **32/32** | 0 | 0 | 1 |
   | revised prompt | 26/32 | 6 | 0 | 0 |

   - **Production is at 96.9% with ZERO false positives** on the hardest section.
     Its single miss is Dunmore/Keswick on the roster's *first* reading — caught
     on the second reading, so that household is redacted elsewhere in the line.
   - **Two runs unioned reach 32/32.** Cost: one false positive, `[Dunmore Harbours]`
     — a community name for a person to reject in one glance. That is the trade,
     and it is the right one.
   - The revised prompt loses nothing to privacy (only ` in `, ` at `, ` uh `
     survive) but yields `[address removed] in [address removed]`. Stays reverted.

After stripping every proposed span from its line, an address still survives on
**29 of 3,236** lines; roughly half of those are correct keeps (roads under
discussion, business addresses). Estimated real miss rate on the order of
**1.5%** of residential identifications.

Also worth noting: **56 spans exceed 90 characters** and nobody has looked at
them.

None of this is urgent for launch — only 1 proposal is applied, so all 3,439
addresses are live either way. It is a gate on the *apply* step.
- **`ASK_DAILY_MAX=400` — DECIDED, 2026-08-14.** It stays. Measured against
  `deepseek-v4-flash` pricing that is **~$3.60/day at the typical per-question
  cost and ~$30/day at the pathological one**; the value that would have held
  $10/day at the top of that range is 130. The trade is accepted deliberately:
  130 means a launch spike tells visitors the archive is out of funding by
  noon, on the day you most want it working. STATE.md "Running it" carries the
  full table.

  What to watch, since the ceiling is now an expectation rather than a
  guarantee: the worst case is driven entirely by unbounded output, and the
  lever that fixes it is `max_tokens` — which is unset on purpose, because this
  is a reasoning model and a cap counts the reasoning too. Measure a real run's
  completion tokens before picking a number. Until then the daily total is the
  only brake, and only accepted runs count against it (gotcha 89).

### The edge — DECIDED: Nginx Proxy Manager, no Cloudflare

**No CDN and no tunnel.** NPM is already running on the Unraid box, publishing
:80 and :81 with a Let's Encrypt store at
`/mnt/user/appdata/Nginx-Proxy-Manager-Official/letsencrypt`. The edge exists,
so the work is one proxy host, not a new service.

Nothing this site does needs a CDN, and the reasons are worth stating because
they are also the reasons the edge stays thin:

- **It serves no media.** Playback is a YouTube iframe, so the 111 GB under
  `data/` never leaves the workstation. The bytes on the wire are HTML and JSON.
- **The expensive endpoint is bounded in the app, not at the edge.**
  `web/limits.py` refuses before the model is called and can explain itself
  *inside* the event stream, which a proxy cannot.
- **Caching buys little.** 432 meetings and 23k items are mostly cold
  long-tail reads.

The trade being accepted is that `pasco.watch` resolves to a residential IP and
:443 is open to a box on the LAN — which is already true of what is on that box,
so this reveals nothing new. The one way this site differs from the rest of it:
this one is *meant* to be found, linked and indexed, and Plex is not. That is a
real difference in exposure and it is a fair thing to accept knowingly.

**`deploy/nginx-proxy-manager.md` is the config**, and it replaces the old
hand-written `deploy/nginx.conf` — which described a `server` block NPM would
never read, since NPM generates its own. Three things there are load-bearing and
none of them are NPM defaults:

- `location /api/admin { return 404; }` and `location /admin { return 404; }` in
  the **Advanced** tab. Under the old file these were plain nginx; they are the
  second lock on the one door that writes to human judgement (gotcha 94), and a
  change of edge must not silently remove the belt.
- `proxy_buffering off` and `proxy_read_timeout 300s` as a **Custom Location**
  on `/api/ask`. NPM defaults to 60s and to buffering — the first cuts off any
  question that thinks hard, the second holds the whole event stream until it is
  finished, which turns live progress into a silence and then a wall of text.
- **Forward to the UI container, never to :8765.** Next reaches the Python API
  through its own `/api` rewrite. `/api/admin` lives behind that port.

**Then `ASK_TRUST_PROXY=1`** (§3.5), and only once NPM is really in front.

**Two loose ends this screenshot surfaced:**

1. **Nothing updates this domain's DNS.** The running container is
   `Namecheap-DDNS`, which serves other domains; `pasco.watch` is at **Porkbun**
   (confirmed by the maintainer, 2026-08-14), and that container cannot touch a
   Porkbun record. So the A record is manual today.

   Porkbun publishes a DDNS-suitable API (`/api/json/v3/dns/editByNameType`,
   with an API key and secret enabled per-domain in the portal), and
   `qdm12/ddns-updater` supports it directly — that is the smallest addition.
   The alternative is to confirm the WAN address is effectively static and keep
   the record manual, which is defensible on most cable service but should be a
   decision rather than an assumption.

   **Either way this is a launch blocker in disguise:** a stale A record is a
   site that is simply gone, and nothing in any log on this side would say why.
   The same Porkbun API credentials also unlock NPM's DNS-01 challenge, which
   would let you keep :80 closed and get a wildcard — so one set of keys settles
   both.
2. **`Backblaze_Personal_Backup` mounts `/mnt/user/`**, which is the cheapest
   possible answer to §3.1's "get that dump off this machine": a `pg_dump`
   written anywhere under `/mnt/user/` is off-site without another moving part.
   Confirm the backup selection actually includes the path you choose — a
   container that *could* back it up is not a backup either.

---

## 5. Handoff facts

- **Restarting `web/server.py` rotates `.admin_token`** and drops every admin
  session. New value in `.admin_token`, mode 600. Expect one lockout mid-deploy.
- **`/admin` cannot move to Unraid.** `web/admin.py` launches `bin/job.py` against
  the GPUs, the venvs, and local `data/`. It stays on this workstation — which
  means the ops surface is never at the public edge.
- **Don't run pipeline campaigns during launch.** Batch LLM jobs and `/api/ask`
  share one DeepSeek account; a campaign competing with readers rate-limits them.
- **`env.local.sh` is gitignored** (mode 600, correct) — so it is the one file
  with no copy anywhere once git exists. It holds `PASCO_DSN` and `LLM_API_KEY`,
  and it now has to diverge per machine.
- **`bin/_env.sh` sources nothing outside this repo.** The old cross-repo
  dependency is gone.

---

## 6. Not blocking

`PIPELINE_PLAN.md` holds the ingest rewrite, including four free fixes worth
taking in any quiet hour (delete the dead `name_speakers` line from
`catch_up.sh`, `MAX_WORKERS` 4→12, fix the naming gate to 56 speakers, persist
rejections). None of it is needed to launch.

**The redaction few-shot examples are weaker than the data they face.** Every
example in `redact.py`'s `SYS` prompt is a clean canonical address
("14382 Ashmont Drive"). Sampling the 3,439 real proposals shows what actually
arrives: hyphenated house numbers from ASR (`21-127 Alder Creek Drive`), mangled
street names (`200 Pinehaven Boulevard`, `five three four one Keswick Carey
Road`), spelled digits with no lead-in phrase (`What one two zero five zero, we
just came up on`), ZIP-only identifications (`Dana Halloran, 34110`), and
speakers correcting themselves mid-address (`2645 Ravenswood. That's all one
word`). Teaching those shapes should raise recall on future ingests.

Do it as a measured change, not a hand-wave: the existing 3,439 proposals were
produced by the current prompt and are the baseline. Re-run adjudication over
the same candidate pool with the revised prompt and diff. Detection has already
run for launch, so this only affects the ~2×/week ingest going forward.

All example names and addresses in the repo are fabricated and verified at zero
occurrences in the corpus. Keep it that way — the originals were lifted from
real testimony, which put three residents' home addresses in a public repo.
