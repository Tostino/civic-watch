# Pulling the images onto Unraid

Postgres is already up from `postgres-unraid.md`. This is the other two.

Images, built by `.github/workflows/images.yml` on every push to `main`:

    ghcr.io/tostino/civic-watch-api:latest
    ghcr.io/tostino/civic-watch-ui:latest

## 1. Let Unraid pull them

The packages inherit the repository's visibility, and the repo is private, so
Unraid cannot pull them yet. Two ways, and the first is much less trouble:

**Make the packages public.** github.com → Packages → each package → Package
settings → Change visibility → Public. The repository can stay private; package
visibility is a separate setting. Nothing secret is in an image — the config all
arrives as environment variables at run time.

**Or authenticate the box.** On the Unraid console, with a PAT carrying
`read:packages`:

```
docker login ghcr.io -u Tostino
```

That writes credentials to `/root/.docker/config.json`, which does not survive
a reboot on Unraid unless you persist it. The first option avoids the problem.

## 2. Deploy with Compose Manager

Use `deploy/docker-compose.yml`. Two containers need to talk to each other by
name, and Unraid's default `bridge` has no DNS — the compose file creates a
network where `http://api:8765` resolves. Doing this as two Docker templates
means either a hand-made custom network or publishing the API on the LAN, and
`/api/admin` lives behind that port.

Install **Compose Manager** from Community Applications, add a stack, paste the
compose file, and put this beside it as the stack's `.env`:

```
PASCO_DSN=postgresql://pasco:PASSWORD@10.0.0.6:5432/pasco_meetings
SITE_URL=https://pasco.watch
SITE_CONTACT=contact@pasco.watch
UI_BIND=10.0.0.6
MODEL_CACHE=/mnt/user/appdata/civicwatch-api/models
TZ=America/New_York

# /api/ask only. Everything else runs locally.
LLM_API_KEY=...
INFERENCE_API_BASE=...
LLM_MODEL_AGENT=...

# 1 because NPM is in front. With 0, client_ip() sees the proxy for every
# visitor and the whole internet shares one rate-limit bucket (gotcha 89).
ASK_TRUST_PROXY=1
ASK_DAILY_MAX=400
ASK_PER_IP=6
```

## 3. The model cache, and the permission that silently breaks search

The API image runs as `nobody` (uid **65534**) and caches the 0.6B embedding
model in `/models`. A freshly created Unraid host directory is root-owned, so
the container cannot write to it — and the failure is quiet, because
`tools.warm()` treats a model that will not load as a degraded arm rather than a
crash. You get a healthy container serving BM25-only search.

Before the first start:

```
mkdir -p /mnt/user/appdata/civicwatch-api/models && chown -R 65534:65534 /mnt/user/appdata/civicwatch-api/models
```

Without this the model re-downloads on every container recreation at best, and
never loads at all at worst.

## 4. Point NPM at it

One proxy host, per `nginx-proxy-manager.md`:

- Forward Hostname/IP **`10.0.0.6`**, Forward Port **`3000`** — the UI. Never
  the API: Next reaches that itself, and `/api/admin` is behind it.
- Advanced tab: `location /api/admin { return 404; }` and
  `location /admin { return 404; }`
- Custom Location `/api/ask`: `proxy_buffering off; proxy_read_timeout 300s;`

The UI is bound to `10.0.0.6:3000` rather than loopback because NPM is itself a
container and cannot reach the host's `127.0.0.1`.

## 5. Verify, in this order

```
docker logs civicwatch-api 2>&1 | grep "dense retrieval"
```

**You want to SEE a line, not an absence:**

    [tools] dense retrieval READY - microsoft/harrier-oss-v1-0.6b on cpu in 4.7s

The failure reads:

    [tools] dense retrieval UNAVAILABLE - <the actual exception>
    [tools] search will answer on BM25 alone; paraphrase queries will find nothing

— almost always the `/models` ownership above. This used to print nothing at
all on success, so the check was "grep for the absence of a failure", which is
a check nobody runs and nobody trusts. It is the one degradation in the stack
that does not announce itself: search keeps answering, on BM25 alone, and looks
healthy until someone notices paraphrase queries stopped working.

```
curl -s "http://10.0.0.6:3000/api/tool/search_transcript?query=school%20zone%20speed%20cameras&limit=3"
```

Expect hits with `"degraded": null`. Then the same query for `zzzznothing`
should return **zero** hits — that proves the dense floor is armed, which it
cannot be if the dense arm never loaded.

```
curl -s https://pasco.watch/robots.txt
```

Must say `Sitemap: https://pasco.watch/sitemap.xml`. If it says `localhost:3000`,
`SITE_URL` did not reach the container.

Then, from the workstation with `PASCO_DSN` repointed:

```
./emb-venv/bin/python bin/audit.py
```

All 47, from both machines.
