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
   carrying a forwarding header (gotcha 94), so the edge is the *second* lock
   rather than the only one. Belt and braces on the one door that writes to
   human judgement.
2. **`web/limits.py` counts questions per address.** Without `X-Forwarded-For`
   it counts them per PROXY, so the whole internet shares one bucket
   (gotcha 89). NPM sends the header by default — set `ASK_TRUST_PROXY=1` to go
   with it, and **only** once NPM is really in front. It is a trust switch:
   turned on with nothing forwarding, a caller can forge their own address and
   bypass the limit entirely.

## The proxy host

**Details tab**

| field | value | why |
|---|---|---|
| Domain Names | `pasco.watch` | plus `www.` only if you want the redirect |
| Scheme | `http` | TLS terminates at NPM; nothing behind it has a cert |
| Forward Hostname/IP | the **UI** container | see below — not the Python API |
| Forward Port | `3000` | |
| Cache Assets | **off** | Next already sets immutable headers on `/_next/static` and correct ones elsewhere. NPM's asset cache is a second, dumber policy layered over a correct one. |
| Block Common Exploits | on | |
| Websockets Support | on | harmless. `/api/ask` is SSE, not websockets — the setting that actually matters for it is below. |

**Nginx only ever talks to Next.** The Python API on :8765 is reached by Next's
own `/api` rewrite, inside the deployment. Do not publish :8765 to the LAN and
do not point a proxy host at it — `/api/admin` is behind it, and the whole
admin story assumes that port is unreachable.

**SSL tab**: request a certificate, Force SSL on, HTTP/2 on, HSTS on.

The HTTP-01 challenge needs :80 reachable from the internet. If you would rather
not open :80 at all, NPM can do **DNS-01**, and Porkbun is in its provider list —
that also gets you a wildcard and renews without any inbound port.

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
entry, with this in its own advanced box:

```nginx
proxy_buffering off;
proxy_read_timeout 300s;
```

Both are required and NPM's defaults are wrong for this route:

- **`proxy_read_timeout` defaults to 60s.** The agent takes 30–90s, so a
  question that thinks hard gets cut off mid-stream — and it would look like the
  archive breaking under exactly the questions worth asking.
- **`proxy_buffering` defaults on**, which holds the event stream until it
  completes. The entire point of streaming the tool calls is that progress
  arrives *while* it works; buffered, the page sits silent and then paints
  everything at once, which is strictly worse than not streaming at all.

Ask is bounded in the **app**, not here: `web/limits.py` refuses before the model
is called and can explain itself *inside* the event stream, which a proxy cannot.

## What is deliberately not at the edge

- **Rate limiting and the money ceiling** — `ASK_PER_IP`, `ASK_DAILY_MAX`, both
  app-side, for the reason directly above.
- **Media.** Playback is a YouTube iframe (`youtube-nocookie.com`), so the 111 GB
  under `data/` is never served by anything and never needs to be reachable.
