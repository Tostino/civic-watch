# The edge: Nginx Proxy Manager

Decided 2026-08-14. **NPM is already running on the Unraid box** — 10.0.0.6,
publishing :80 and :81, with a Let's Encrypt store at
`/mnt/user/appdata/Nginx-Proxy-Manager-Official/letsencrypt`. So the edge exists,
TLS is already solved there, and there is no cloudflared and no need for one.

This file replaces `deploy/nginx.conf`, which was a hand-written `server` block
that NPM would never have read: NPM generates its own from the database behind
its UI. What NPM cannot generate is everything below, and every item here is
load-bearing.

## Why any of this is needed

Two things in the archive stop being true the moment a proxy sits in front, and
both fail silently:

1. **`admin.loopback()` reads the TCP peer**, which becomes 127.0.0.1 for every
   request on earth once anything proxies. The app also refuses any request
   carrying a forwarding header, so the edge is the *second* lock
   rather than the only one. Belt and braces on the one door that writes to
   human judgement.
2. **`web/limits.py` counts questions per address.** Without `X-Forwarded-For`
   it counts them per PROXY, so the whole internet shares one bucket. NPM sends the header by default — set `ASK_TRUST_PROXY=1` to go
   with it, and **only** once NPM is really in front. It is a trust switch:
   turned on with nothing forwarding, a caller can forge their own address and
   bypass the limit entirely.

## The proxy host

**Details tab**

| field | value | why |
|---|---|---|
| Domain Names | `pasco.watch` | add `www.pasco.watch` **only if that A record exists** — a name in this list with no DNS is a failed certificate request, not a skipped one |
| Scheme | `http` | TLS terminates at NPM; nothing behind it has a cert |
| Forward Hostname/IP | `10.0.0.6` | the Unraid host |
| Forward Port | **`3100`** | the civicwatch container. NOT 3000 — something else already holds :3000 on that box |
| Cache Assets | **off** | Next already sets immutable headers on `/_next/static` and correct ones elsewhere. NPM's asset cache is a second, dumber policy layered over a correct one. |
| Block Common Exploits | on | |
| Websockets Support | on | harmless. `/api/ask` is SSE, not websockets — the setting that actually matters for it is below. |

**Point it at the container, never at the API.** The Python API listens on
127.0.0.1 *inside* the container and is not on the network at all; Next reaches
it over loopback. `/api/admin` lives behind that port, and the whole admin story
assumes it is unreachable.

## SSL

Same dialog, **SSL tab**:

| field | value |
|---|---|
| SSL Certificate | **Request a new SSL Certificate** |
| Force SSL | on |
| HTTP/2 Support | on |
| HSTS Enabled | **off for now** |
| Email Address | yours |
| I Agree to the Let's Encrypt Terms of Service | on |

**Leave HSTS off until the site is confirmed working.** It tells every browser
that visited to refuse plain HTTP for the max-age, and that instruction is
already cached on their machine — so if you need to fall back, you cannot. Turn
it on afterwards, when there is nothing to fall back from.

**Which challenge.** HTTP-01 is the default and needs :80 reachable from the
internet. Measured 2026-08-14: a request to `https://pasco.watch` from outside
reached NPM and was refused with `TLSV1_ALERT_UNRECOGNIZED_NAME` — which is NPM
saying it holds no host for that name, and proves :443 is forwarded. :80 was not
separately confirmed; if the request fails, that is the first thing to check.

If you would rather not open :80, tick **Use a DNS Challenge**:

- DNS Provider: **Porkbun**
- Credentials:

      dns_porkbun_key=pk1_...
      dns_porkbun_secret=sk1_...

- Propagation Seconds: **120** (the default is often too short for Porkbun and
  the failure looks like a wrong key rather than a slow record)

DNS-01 needs the API key **and** the per-domain API ACCESS toggle on
`pasco.watch` in the Porkbun panel — the key authenticates without it and the
domain still errors. Those are the same credentials the DDNS updater wants, so
one setup covers both, and it renews with no inbound port at all.

## Advanced tab — paste exactly this

```nginx
location /api/admin { return 404; }
location /admin     { return 404; }
```

The curation console and its API are not on the internet. **404 rather than
403**: a refusal that distinguishes "exists but forbidden" from "not here" tells
a scanner which hosts are worth a second look.

The operator reaches the console over an SSH tunnel instead —

```bash
ssh -N -L 3000:127.0.0.1:3000 <the box running the UI>
```

— and then `http://localhost:3000/admin`, which is the one path with no proxy in
it, so `loopback()` is telling the truth.

## Custom Locations tab — `/api/ask`

Add a location `/api/ask`, forwarding to the same host and port as the main
entry (`10.0.0.6` : `3100`), with this in its own advanced box:

```nginx
proxy_buffering off;
proxy_read_timeout 900s;
```

Both are required and NPM's defaults are wrong for this route:

- **`proxy_read_timeout` defaults to 60s.** A hard question runs for minutes
  (`ASK_DEADLINE`, `web/agent.py`), so it gets cut off mid-stream — and it would
  look like the archive breaking under exactly the questions worth asking.
- **`proxy_buffering` defaults on**, which holds the event stream until it
  completes. The entire point of streaming the tool calls is that progress
  arrives *while* it works; buffered, the page sits silent and then paints
  everything at once, which is strictly worse than not streaming at all.

**NPM is no longer the only proxy on this route.** Since the API and the UI
became one image, `/api/ask` arrives at Next and is rewritten to the API on
loopback — and Next proxies external rewrite destinations through httpxy, which
arms a 30s *inactivity* timer on that socket. It is stricter than anything set
here, and it fails the same way: silence, then a socket destroyed mid-run and a
page that says the connection dropped. Measured: a 45s gap died at 30.1s with no
error event. `experimental.proxyTimeout` in `ui/next.config.ts` raises it, and
it is set to **900s — the same number as above, deliberately**. Two proxies with
two ceilings is a trap: the tighter one wins silently, so the config an operator
reads is not the one deciding. Change one, change both.

Ask is bounded in the **app**, not here: `web/limits.py` refuses before the model
is called and can explain itself *inside* the event stream, which a proxy cannot.
The app also stops the stream going quiet at all — `HEARTBEAT` in
`web/server.py` writes an SSE comment every 10s — which is what makes the next
proxy somebody puts in front of this a non-event rather than another outage.

## What is deliberately not at the edge

- **Rate limiting and the money ceiling** — `ASK_PER_IP`, `ASK_DAILY_MAX`, both
  app-side, for the reason directly above.
- **Media.** Playback is a YouTube iframe (`youtube-nocookie.com`), so the 111 GB
  under `data/` is never served by anything and never needs to be reachable.
