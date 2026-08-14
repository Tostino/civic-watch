# Launch plan

State as of 2026-08-13. `audit.py`: **0 failing of 45**.

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
| **cloudflared** | TLS + the public hostname. |

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

1. **Dump.** `pg_dump "$PASCO_DSN" -Fc -f pasco-$(date +%F).dump` — this is also
   the backup that does not currently exist anywhere (§3.1).
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
6. **UI container.** `next build` then `next start`. `ARCHIVE_API` points at the
   API container.
7. **cloudflared**, then `ASK_TRUST_PROXY=1` — **only** once the tunnel is really
   in front and setting `X-Forwarded-For` (§3.5).
8. **Repoint this workstation.** `PASCO_DSN` → the Unraid host. Then run
   `bin/audit.py` from *both* machines; 45 checks should pass from each.

Bulk embedding now writes vectors over the LAN. Fine at ~4–8 new recordings
twice a week; it would not be fine for a full rebuild.

---

## 3. Blockers

### 3.1 No backup, and no version control

    $ git rev-parse --is-inside-work-tree
    fatal: not a git repository

No repo, no remote, no history. And no `pg_dump` anywhere in `bin/` or any `.sh`
— the only backup-shaped file on disk is `logs/rederive.backup.gz`, 1.6 MB of
`speaker_identity` rows from one rederive. **One table.**

The database is ~1,036 GPU-hours of transcription, 432 recordings, 298,737
utterances, 72 human speaker labels, 3,439 redaction decisions. The human
curation cannot be regenerated at any price. `.gitignore` is already correct
(`env.local.sh`, `.admin_token`, `data/`, `logs/`, `*-venv/`) — written for a
repo that was never initialized.

Step 1 of the migration produces the dump. Do `git init` alongside it.

### 3.2 A domain is not registered yet

Needed before `SITE_URL`, TLS, or the sitemap mean anything. Do this first, not
last — DNS can lag.

### 3.3 `SITE_URL` is still localhost

    export SITE_URL="http://localhost:3000"

`robots.ts`, `sitemap.ts` (1,255 URLs), and every canonical + OpenGraph tag read
it. Launch as-is and you hand Google a sitemap of localhost URLs.

### 3.4 `SITE_CONTACT` is empty

No way for a person to report an error in the record — on an archive that still
contains 3,439 unredacted home addresses. An email address is enough.

### 3.5 `ASK_TRUST_PROXY=0` behind the tunnel collapses rate limiting

With cloudflared in front and this at `0`, `client_ip()` sees one address for
**every** visitor, so "6 questions per 10 minutes per person" becomes 6 per 10
minutes for the entire internet. Set it to `1` only once the tunnel is in
place — it is a trust switch, and turning it on with nothing in front lets a
caller forge their address and bypass the limit entirely.

### 3.6 Nothing is supervised

Docker restart policies cover Unraid. This box needs nothing — if it is off, the
site stays up. That is the point of the split.

---

## 4. Decide on purpose

- **3,439 addresses stay live and searchable** across 370 recordings; 1 applied.
  Defensible — public record, always been there — but make it the choice, not a
  leftover. Applying is a bulk update plus ~1–2h of re-indexing, and `audit.py`
  already has three invariants proving removal reaches the transcript, the search
  index, and the passages a reader gets.
- **`ASK_DAILY_MAX=400`** is ~$8/day at ~2¢ a question. A launch spike burns it by
  noon, and then every visitor is told the archive is out of funding — on the day
  you most want it working.

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
