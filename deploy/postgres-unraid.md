# Postgres 18 on Unraid — by hand

Everything here was verified against the real image and the live database on
2026-08-14, not recalled. Where a number is measured, it says so.

**Image: `pgvector/pgvector:pg18`.** Verified contents:

| | in the image | live today |
|---|---|---|
| server | PostgreSQL **18.6** (Debian 12) | 17.10 (Ubuntu 22.04) |
| `vector` | **0.8.6** | 0.8.6 — identical, no extension upgrade |
| `pg_trgm` | 1.6 | 1.6 |
| locale | `en_US.utf8`, libc provider | `en_US.UTF-8`, libc provider |

`bm25(text,int)` is a plain plpgsql function and travels inside the dump, so
nothing else needs installing.

---

## 1. The one thing that will bite you

**Postgres 18 moved the data directory, and every guide on the internet still
says the old path.**

    PGDATA  = /var/lib/postgresql/18/docker      <- 18
    VOLUME  = /var/lib/postgresql

On 17 and earlier it was `/var/lib/postgresql/data`, and that path **does not
exist in the 18 image at all** — verified by running it: `ls
/var/lib/postgresql/data` → *No such file or directory*.

**So map the host share to `/var/lib/postgresql`.** Not `/var/lib/postgresql/data`.

Map the old path and nothing errors, which is the dangerous part: `PGDATA` still
sits under the declared `VOLUME`, so Docker quietly creates an **anonymous**
volume for it. The database works, your appdata share stays empty, and the data
lives somewhere unmanaged. On Unraid that is a data-loss trap specifically,
because editing a template and hitting Apply **recreates the container** — and a
recreated container gets a *new* anonymous volume. The database comes back empty
and the old one is orphaned with a hash for a name.

**Put the share on the cache pool, not the array.** Postgres on parity-protected
spinning disks is punishing, and it writes constantly.

---

## 2. Add Container

Unraid → DOCKER → ADD CONTAINER, Advanced view.

| field | value |
|---|---|
| Name | `civicwatch-postgres` |
| Repository | `pgvector/pgvector:pg18` |
| Network Type | `bridge` |
| Port | container `5432` → host `5432` |
| **Path** | container **`/var/lib/postgresql`** → host `/mnt/user/appdata/civicwatch-postgres` |
| Variable | `POSTGRES_PASSWORD` = *a real password* |
| Variable | `POSTGRES_USER` = `pasco` |
| Variable | `POSTGRES_DB` = `pasco_meetings` |
| Variable | `TZ` = `America/New_York` |
| Extra Parameters | `--shm-size=4g` |
| Post Arguments | `-c shared_buffers=2GB -c effective_cache_size=4GB -c maintenance_work_mem=2GB -c work_mem=32MB -c timezone=America/New_York -c log_timezone=America/New_York` |

**`--shm-size=4g`, and it must be >= `maintenance_work_mem`.** Docker's default
is 64 MB and Postgres uses POSIX shared memory for parallel workers, so too
small gives `could not resize shared memory segment`.

**Measured the hard way, 2026-08-14:** `1g` alongside `maintenance_work_mem=2GB`
looks fine and restores 156 of 156 TOC entries, then fails on exactly one
object - the parallel HNSW build asked for 2,144,374,752 bytes of shared memory
and got `No space left on device`. `pg_restore` reports it as
*errors ignored: 2* and exits, so every row count matches and the archive has
no vector index. **A restore that "finished" is not a restore that built your
indexes** - count them (`hnsw=1`), do not read the summary line.

Recovering without touching the container is one statement, because taking the
parallel workers out removes the shared segment from the picture entirely:

    SET max_parallel_maintenance_workers = 0;
    SET maintenance_work_mem = '2GB';
    CREATE INDEX passages_embedding_hnsw ON public.passages
      USING hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64');

That took **81 seconds** serially and produced the same 1,303 MB index. Raise
the shm anyway: `bin/index_passages.py` sets `maintenance_work_mem='2GB'` per
session, so any future rebuild from the workstation hits the same wall.

**Post Arguments work because of how the entrypoint is written**: an argument
starting with `-` makes it run `postgres "$@"`, so these become server flags.

**On the settings.** The live cluster runs almost stock — `shared_buffers` is
**128 MB** and everything else is a Debian default — so treat the numbers above
as an improvement, not a requirement; it has been fine as it is. Two are chosen
rather than copied:

- `maintenance_work_mem=2GB` is for the **restore**, which rebuilds the HNSW
  index: 1,304 MB over 167,225 × 1024-dim vectors, and by a wide margin the slow
  step. The app already sets this itself per-session for its own rebuilds
  (`index_passages.py:524`), so this setting exists only for `pg_restore`.
- `timezone=America/New_York` **matches the live cluster**, which is not a
  default — the container would otherwise come up UTC and shift every rendered
  `timestamptz`.

`hnsw.ef_search=1000` needs no configuration: `retrieve.py` sets it per
connection via `set_config`, so it travels with the code.

**Two things the live cluster has that this deliberately drops:** `ssl = on`
(snakeoil certs, and meaningless today because it only listens on loopback) and
the Debian cluster-management furniture. Once it listens on the LAN, the DSN
password crosses the wire — if you want that encrypted, that is a certificate
into the container and `sslmode=require` in `PASCO_DSN`, and it is a fair thing
to add later rather than now.

---

## 3. Dump, restore, verify

Take the dump **fresh, on the day you cut over**, with nothing writing — no
fleet, no `bin/job.py`, no open `/admin` session. Everything decided since
2026-08-13 (redactions, labels) exists only in the live database.

**Use the 18 binaries, which are already installed here.** `pg_dump` on `PATH`
resolves to 17.10 while `psql` resolves to 18.4, so name the path explicitly:

```bash
source ./env.local.sh
/usr/lib/postgresql/18/bin/pg_dump "$PASCO_DSN" -Fc -f pasco-$(date +%F).dump
```

Dumping a 17 server with an 18 client is the supported direction; the reverse
is not.

Restore, with the container up:

```bash
/usr/lib/postgresql/18/bin/pg_restore \
  -d "postgresql://pasco:PASSWORD@10.0.0.6:5432/pasco_meetings" \
  -j 4 --no-owner --no-privileges pasco-YYYY-MM-DD.dump
```

`-j 4` parallelises the index builds, which is where the time goes. Expect the
HNSW build to dominate.

Then verify, from the workstation:

```bash
psql "postgresql://pasco:PASSWORD@10.0.0.6:5432/pasco_meetings" -c "
  select (select count(*) from utterances)  as utterances,   -- 298,737
         (select count(*) from passages)    as passages,     -- 166,998
         (select count(*) from redaction)   as redactions,   -- 3,440
         (select extversion from pg_extension where extname='vector') as vector;"
```

Point `PASCO_DSN` at the new host and run the real check:

```bash
PASCO_DSN="$UNRAID_DSN" ./emb-venv/bin/python bin/audit.py
```

The bar is that the target returns **the same result as the source**, not that
it returns zero. On 2026-08-14 both report *2 failing of 47* — the redaction
residue in §4 of `LAUNCH.md`, left deliberately. A target that disagrees with
the source is a migration fault; a target that agrees is a faithful copy.

**On the collation warning** that §2 step 4 of `LAUNCH.md` tells you to watch
for: it is a smaller risk here than it sounds. The container is glibc 2.36
(Debian 12) against 2.35 on this host, and a glibc difference genuinely can
reorder text indexes — but that bites a *physical* copy or `pg_upgrade`. A
dump/restore rebuilds every index on the new server with the new glibc, so they
come out self-consistent and stamped with the new collation version. Read the
warning if it appears; do not expect one.

---

## 4. Tell Backblaze to skip it

`Backblaze_Personal_Backup` mounts `/mnt/user/`, so the moment Postgres lives in
appdata it starts backing up a **live data directory** — which is not a backup
of anything. A file-by-file copy of a running PGDATA is a torn snapshot that
will not restore, and it churns constantly against files Postgres rewrites.

**Exclude `/mnt/user/appdata/civicwatch-postgres/` and back up dumps instead.**
Write a scheduled `pg_dump` somewhere else under `/mnt/user/` and let Backblaze
carry that: a dump is consistent by construction, and it is the artifact you
would actually restore from. Nothing schedules one today (`LAUNCH.md` §3.1).
