# Running civic-watch on Unraid

Postgres is already up from `postgres-unraid.md`. This is the application, and
it is **one container**:

    ghcr.io/tostino/civic-watch:latest

Built by `.github/workflows/images.yml` on every push to `main`. The API and the
reading surfaces are in it together; the UI reaches the API over `127.0.0.1`
inside the container, so there is no second container, no custom network, and no
Compose Manager — a plain Unraid template runs it.

**This is a NEW package.** `civic-watch-api` and `civic-watch-ui` are
superseded; a fresh GHCR package starts **private**, so make `civic-watch`
public the same way you did those two, or Unraid cannot pull it.

## 1. Add Container

Unraid → DOCKER → ADD CONTAINER, Advanced view.

| field | value |
|---|---|
| Name | `civicwatch` |
| Repository | `ghcr.io/tostino/civic-watch:latest` |
| Network Type | `bridge` |
| Port | container `3000` → host `3000` |
| Path | container `/models` → host `/mnt/user/appdata/civicwatch/models` |
| Extra Parameters | `--init` |
| Variable | `PASCO_DSN` = `postgresql://pasco:PASSWORD@10.0.0.6:5432/pasco_meetings` |
| Variable | `SITE_URL` = `https://pasco.watch` |
| Variable | `SITE_CONTACT` = `contact@pasco.watch` |
| Variable | `ASK_TRUST_PROXY` = `1` |
| Variable | `ASK_DAILY_MAX` = `400` |
| Variable | `ASK_PER_IP` = `6` |
| Variable | `LLM_API_KEY` = *your key* |
| Variable | `INFERENCE_API_BASE` = *the base URL* |
| Variable | `LLM_MODEL_AGENT` = *the model name* |
| Variable | `TZ` = `America/New_York` |

`--init` gives the container a real PID 1 for signal handling and zombie
reaping, which matters because this image runs two processes.

**`ASK_TRUST_PROXY=1` because NPM is in front.** At `0`, `client_ip()` sees the
proxy for every visitor and the whole internet shares one rate-limit bucket
(gotcha 89). Only set it with a proxy actually in front — it is a trust switch,
and turning it on with nothing forwarding lets a caller forge an address.

**Only `LLM_API_KEY` and friends are for `/api/ask`.** Everything else — search,
browse, the record — is local and works without them.

## 2. The model cache, and the permission that silently breaks search

The image runs as `nobody` (uid **65534**) and caches the 0.6B embedding model in
`/models`. A freshly created Unraid host directory is root-owned, so the
container cannot write to it. Before the first start:

```
mkdir -p /mnt/user/appdata/civicwatch/models && chown -R 65534:65534 /mnt/user/appdata/civicwatch/models
```

Get this wrong and the model re-downloads on every recreation at best, and never
loads at all at worst. The startup line in §5 tells you which.

## 3. Point NPM at it

One proxy host, per `nginx-proxy-manager.md`:

- Forward Hostname/IP **`10.0.0.6`**, Forward Port **`3000`**
- Advanced tab: `location /api/admin { return 404; }` and
  `location /admin { return 404; }`
- Custom Location `/api/ask`: `proxy_buffering off; proxy_read_timeout 300s;`

## 4. What is not reachable, and why it is three locks rather than one

The curation console cannot be reached through this container, and that does not
depend on getting NPM right:

1. **The API binds `127.0.0.1` only** (`entrypoint.sh`), so `/api/admin` is not
   on the network at all. Verified: the only listening sockets in the container
   are `0.0.0.0:3000` and `127.0.0.1:8765`, and only 3000 is published.
2. **`ui/proxy.ts` 404s `/admin` and `/api/admin`** when `ADMIN_DISABLED=1`,
   which the image sets. It runs before rewrites, so nothing reaches the `/api`
   proxy that would forward it.
3. **The edge 404s both** as well.

The second lock exists *because* of unification. While the API and the UI were
separate containers, `admin.loopback()` was a hard guarantee — the peer was the
UI's container address, never loopback. Proxying from inside one container makes
the peer 127.0.0.1, which would have softened that into gotcha 94's
forwarding-header check: still correct, but dependent on every public request
arriving through a proxy that sets the header. A guarantee about somebody else's
config is not a guarantee.

**The console runs on the workstation**, against the same database — which is
where `bin/job.py`, the GPUs and `data/` are anyway.

## 5. Verify, in this order

```
docker logs civicwatch 2>&1 | grep "dense retrieval"
```

**You want to SEE a line, not an absence:**

    [api] [tools] dense retrieval READY - microsoft/harrier-oss-v1-0.6b on cpu in 52.2s

The failure reads:

    [api] [tools] dense retrieval UNAVAILABLE - <the actual exception>
    [api] [tools] search will answer on BM25 alone; paraphrase queries will find nothing

— almost always the `/models` ownership in §2. This is the one degradation in
the stack that does not announce itself: search keeps answering, on BM25 alone,
and looks healthy until someone notices paraphrase queries stopped working.

The log carries both halves, prefixed:

    [supervisor] starting api on 127.0.0.1:8765 (loopback only)
    [api] [tools] dense retrieval READY - ...
    [supervisor] api is listening after 53s
    [supervisor] starting ui on 0.0.0.0:3000
    [ui]  ▲ Next.js 16.3.0

**If either half dies the container exits**, so Docker restarts it and a real
fault shows as a restart loop rather than a healthy container serving half a
stack. The supervisor names which half:

    [supervisor] THE API DIED - stopping the container

Then:

```
curl -s "http://10.0.0.6:3000/api/tool/search_transcript?query=school%20zone%20speed%20cameras&limit=3"
```

Hits, with `"degraded": null`. The same query for `zzzznothing` must return
**zero** hits — that proves the dense floor is armed, which it cannot be if the
dense arm never loaded.

```
curl -s https://pasco.watch/robots.txt
```

Must say `Sitemap: https://pasco.watch/sitemap.xml`. `localhost:3000` means
`SITE_URL` did not reach the container.

Then, from the workstation with `PASCO_DSN` repointed:

```
./emb-venv/bin/python bin/audit.py
```

The bar is that it returns **the same result as the source**, not zero — today
that is 2 failing of 47, both the redaction residue in `LAUNCH.md` §4.
