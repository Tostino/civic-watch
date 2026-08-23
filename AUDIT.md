# Front-end audit, 23 August 2026

Every page template, mobile and desktop, measured rather than sampled. What
follows is the evidence first and the plan second, because two of the six
findings are the same cause wearing different hats and that is only obvious
once the numbers are side by side.

## What has been done since, 23 August 2026

Items 1, 2 and 4 of the plan below are implemented, and most of 5. Item 3 is
implemented at the wire and is **not confirmed to have worked**; the paragraph
under finding 2 says exactly what was and was not established. Everything here
was verified against a production build of this branch, served by `next start`,
not against the dev server, whose headers and bundles are both different.

| plan item | state | what was verified |
|---|---|---|
| 1. Defer the YouTube player | done | `/about`, `/search`, `/`, `/meeting/:id` load with **0 YouTube requests and no iframe**. Clicking a citation builds the player and the recording plays: no autoplay was lost. |
| 2. Stop the admin probe | done | `/search` with hits, `/meeting/:id` and `/ask` make **no request to `/api/admin/*`** and log no console error. |
| 3. Caching | shipped, did not work | Documents and RSC payloads no longer carry `no-store`; `/admin` still does; `/_next/static` keeps `immutable`. The back/forward cache is still refused. See below. |
| 4. Contrast and ARIA | done | `.sessionLen` measures **5.87:1 light and 5.93:1 dark** on the selected chip, from 4.46. The `aria-expanded` is off the grid row. |
| 5. Touch targets | part | The time axis clears 24px at every width. The timeline bands and the answer's inline controls are untouched and still need the design decision. |
| 6. Cleanup | not started | |

**Measured on production after the deploy**, in a real browser rather than in
the harness: `/about` is **326 KiB across 16 requests**, against 1,378 KiB in
the table below. No iframe and no request to any Google host on `/`, `/about`,
`/search` or a meeting page; none to `/api/admin/*` anywhere; no console error
on any of them. Pressing a citation builds the player and the recording plays,
at 375px as well as at desktop width. The month cells and the fold row measure
24px, and the length of a recording on a selected chip measures 5.93:1.

The one thing that did NOT take: **the back/forward cache is still refused on
production**, tested the same way and with the same result as locally. The
header was necessary and is not sufficient.

**PageSpeed Insights, on the live site, 23 August 2026 at 01:45**, which is the
number that counts because it runs on Google's hardware under Google's
throttling rather than on this workstation:

| | before | after |
|---|---|---|
| mobile performance | 88 | **98** |
| mobile TBT | 370 ms | **30 ms** |
| desktop performance | — | 99 |
| accessibility, both | 94 desktop | **100 and 100** |
| best practices, both | 96 | **100 and 100** |
| SEO, both | 100 | 100 |
| CLS, both | 0 | **0** |
| total payload | 1,432 KiB | **387 KiB** |

The third-party cookie audit and the font-display failure are gone from both
form factors, as expected: they were YouTube's and YouTube is no longer there.
Contrast and touch targets pass. What remains under Performance is exactly
item 6 and nothing else — render-blocking requests, 13 KiB of legacy
JavaScript, 27 KiB of unused JavaScript, 14 KiB of unused CSS.

**PSI does not run the back/forward-cache audit at all.** It is not among the
136 audits on that report, passing, failing or not-applicable. So PSI cannot
settle item 3 however good the scores look, and the local Lighthouse container
remains the only instrument that can.

## How this was measured, and what the numbers are worth

Lighthouse 13.4.1 in a container, against production, eight templates by two
form factors — sixteen runs:

```bash
docker run --rm femtopixel/google-lighthouse <url> \
  --chrome-flags="--headless=new --no-sandbox --disable-dev-shm-usage" \
  --output=json --form-factor=mobile        # or --preset=desktop
```

**These scores are more generous than PageSpeed Insights and should not be
quoted as if they were PSI's.** PSI throttles to a slow device on Google's
hardware; this ran on a workstation with a Lighthouse benchmark index of 4078.
PSI scored the browse page 88 on mobile with 370 ms of blocking time; the same
page here scored 97 with 70 ms. Use PSI for "how fast is it really" and this
table for comparing pages against each other and for the audit details, which
do not depend on the machine.

**There is no field data.** Search Console reports "No Data" for real-user
metrics, because the site does not yet have the traffic to produce any. Core
Web Vitals only feed ranking through field data, so none of this is currently
affecting search position. It is worth doing for readers, not for Google.

## Scores

| page | form factor | perf | a11y | best prac | SEO | LCP | TBT | CLS |
|---|---|---|---|---|---|---|---|---|
| browse | mobile | 97 | 97 | 96 | 100 | 2.4s | 70ms | 0 |
| browse | desktop | 100 | 94 | 96 | 100 | 0.6s | 0ms | 0 |
| about | mobile | 100 | 100 | 96 | 100 | 1.7s | 26ms | 0 |
| about | desktop | 100 | 100 | 96 | 100 | 0.4s | 0ms | 0 |
| search | mobile | 90 | 100 | 92 | 63 | 2.1s | 24ms | 0 |
| search | desktop | 100 | 100 | 92 | 63 | 0.5s | 0ms | 0 |
| ask | mobile | 99 | 100 | 96 | 100 | 2.0s | 20ms | 0 |
| ask | desktop | 100 | 100 | 96 | 100 | 0.5s | 0ms | 0 |
| meeting | mobile | 96 | 93 | 92 | 100 | 2.7s | 40ms | 0 |
| meeting | desktop | 99 | 93 | 92 | 100 | 0.8s | 0ms | 0 |
| item | mobile | 99 | 100 | 96 | 100 | 2.0s | 20ms | 0 |
| item | desktop | 100 | 100 | 96 | 100 | 0.4s | 0ms | 0 |
| case | mobile | 99 | 100 | 96 | 100 | 2.1s | 20ms | 0 |
| case | desktop | 100 | 100 | 96 | 100 | 0.5s | 0ms | 0 |
| answer | mobile | 97 | 96 | 92 | 63 | 2.6s | 28ms | 0 |
| answer | desktop | 100 | 100 | 92 | 63 | 0.6s | 0ms | 0 |

**Cumulative Layout Shift is 0 on all sixteen.** That is unusual and worth
protecting; every fix below should be re-measured against it.

## The findings

### 1. The YouTube player loads on every page — the single largest problem

Nine requests, **987 KiB**, on every template measured, including `/about` and
`/search`, where no recording can be opened at all:

| page | total weight | of which YouTube |
|---|---|---|
| browse | 1,432 KiB | 987 KiB (69%) |
| about | 1,378 KiB | 987 KiB (72%) |
| search | 1,404 KiB | 987 KiB (70%) |
| meeting | 1,486 KiB | 987 KiB (66%) |
| answer | 1,442 KiB | 987 KiB (68%) |

The cause is in `ui/components/player/PlayerProvider.tsx`: the setup effect
calls `loadApi()` and constructs `new window.YT.Player(...)` unconditionally on
mount. It is not gated on whether a recording exists. The comment above it is
about something else and is correct — the iframe is deliberately never torn
down, so that switching recordings does not remount it — but *never tearing it
down* does not require *creating it immediately*.

Three other audits are the same cause and will resolve with it:

- **"Issues were logged in the Issues panel"**, failing 16/16. The issue is a
  third-party Cookie warning, and its URL is the `youtube-nocookie.com/embed/`
  iframe.
- **Font display**, failing 16/16. The font is Roboto from `fonts.gstatic.com`.
  This site does not use Roboto — `layout.tsx` loads Inter, Source Serif 4 and
  JetBrains Mono through `next/font/google`, self-hosted, already with
  `display: "swap"`, and there is no gstatic request in our own HTML. The
  Roboto is YouTube's.
- Most of **unused JavaScript**. PSI attributed 547 KiB of the 575 KiB to
  YouTube; our own bundles contributed 27 KiB.

**Fix.** Defer `loadApi()` and player construction until a recording is first
requested, then keep it for the life of the session exactly as now. Gate the
setup effect on a "has ever been asked for a video" flag rather than on
`source`, so the persistence the current comment protects is unchanged.

**Cost.** The first press of play pays the API load, a few hundred
milliseconds before the video starts. Everything after it is as now.

**Beyond page weight**, this is a privacy improvement. Every reader's browser
currently contacts Google's player for an agenda they are only reading. For an
archive that keeps no cookie, no session and no access log, loading a
third-party player for someone who never asked for one is out of step with the
rest of the design.

### 2. Every page is `no-store`, so the back button is a full reload

`Cache-Control: private, no-cache, no-store, max-age=0, must-revalidate` on
every response. Back/forward cache is refused for exactly this reason, and
Lighthouse reports it on all sixteen runs with two reasons, both naming
`no-store`:

- `MainResourceHasCacheControlNoStore`
- `JsNetworkRequestReceivedCacheControlNoStoreResource`

The practical effect is that a reader who opens an item from a meeting and
presses back re-fetches and re-renders the meeting, transcript and all, instead
of having it restored instantly.

**Fix, as shipped.** `headers()` in `next.config.ts` now sets
`private, no-cache, max-age=0, must-revalidate` on everything the public reads,
and keeps `private, no-store` on `/admin/:path*`. Nothing about freshness
changes: `no-cache` still makes the browser revalidate before it shows a stored
copy, and the archive is exactly as current as it was. What goes is the
instruction not to keep a copy at all, which is a promise about disk, where the
back/forward cache is memory.

Verified at the wire on a production build: documents, RSC payloads and
`Next-Router-Prefetch` responses all carry the new header, `/admin` keeps
`no-store`, and `/_next/static` keeps `public, max-age=31536000, immutable`.
The one response still stamped `no-store` is Next's own 404, which it writes
itself and `headers()` does not reach.

**And it is not enough. This has now been confirmed on production**, not only
locally: `/search` marked, `/about`, back, and the mark is gone.
Tested by marking `window` on a page, navigating away, and going back: if the
mark survives, the document came out of the back/forward cache. On the same
server, on the same origin, under the same header, a plain HTML file in
`public/` **is** restored and a Next-rendered page **is not** — including a page
stripped down to a paragraph, with the providers, the header and the player all
removed from the root layout. So at least one blocker remains and it is in the
framework's own output rather than in this site's code. The built client bundles
contain no `unload` handler, no `WebSocket`, no `BroadcastChannel`, no
`navigator.locks` and no `indexedDB`, so it is none of the usual ones.

Chrome would not say which: `notRestoredReasons` reports `masked` in this
browser even for the page it restored. Lighthouse names reasons outright, and
against production it named only the two `no-store` ones — both now gone — so
**re-running it after this deploys is the check that settles whether the back
button is fixed or only unblocked.** Docker was not running on this machine and
there is no local Chrome, so that run could not be done here.

### 3. The public site probes an admin endpoint on three templates

`https://pasco.watch/api/admin/session` returns **404 on every page load** of
`/search`, `/meeting/:id` and `/ask/:id`. It is the only console error on the
site, and it appears in all six of those runs.

The path is `DisputePassage` → `useOperator()` → `getAdminSession()`. That
component renders beside search hits, transcript lines and answer passages, so
every public reader of those pages asks whether they are an operator, and is
told 404. Browse, about, item and case do not render it and are clean.

**Fix.** Do not ask on the public site. The console error is the visible part;
the request itself is waste on every view, and probing an admin path from a
reader's browser is noise nobody benefits from.

### 4. Accessibility, all page-specific

| page | audit | detail |
|---|---|---|
| meeting | colour contrast | `.sessionLen` at **4.46:1**, needs 4.5:1. Foreground `#726c64`. One token away. |
| meeting | touch targets | 9 failures. `Timeline` bands are **10px tall** against a 24px minimum; widths vary from 11px to 174px. |
| browse (desktop) | touch targets | `TimeAxis` cells **88.8 × 21.6px** — 2.4px short. |
| answer (mobile) | touch targets | `.itemHead` 19.2px tall, `.at` 17.6px. |
| browse | ARIA | `aria-expanded` on an element with `role="row"` inside a `grid`. Valid on `treegrid` rows, not `grid` rows. Either the container becomes a `treegrid` or the attribute goes. |

The contrast and ARIA ones are small and unambiguous. The touch targets are a
design question as much as a fix: the timeline bands are deliberately thin, and
making them 24px tall changes what that component looks like. Spacing counts
toward the same criterion, so there may be a way through without resizing.

### 5. Small and cheap

- **Render-blocking CSS**: three files, 22 KiB, 151 ms.
- **Legacy JavaScript**: 13.8 KiB in `chunks/354-*.js` — transpilation for
  browsers this project does not target.
- **Unused CSS** ~14 KiB and **unused JavaScript** ~26 KiB in our own bundles,
  once YouTube is excluded.

Worth doing after the first three, not before. Together they are smaller than
one of YouTube's nine requests.

## Things that look like problems and are not

- **SEO 63 on `/search` and `/ask/:id`.** Both fail one audit: "Page is blocked
  from indexing". Both are blocked deliberately — `/search` is disallowed in
  robots.txt because each query runs the embedding model, and saved answers are
  `noindex` on purpose. The score is the audit doing its job. Every indexable
  template scores 100.
- **A 6,580 ms server response on `/search` mobile.** This appeared once, in
  one Lighthouse run, and drove that run's 10.3 s Speed Index. It did not
  reproduce: three uncached queries against production immediately afterwards
  returned in 0.55, 0.55 and 0.63 s, and item and meeting pages in 0.07 and
  0.08 s. Most likely contention during the sixteen-run sweep. Worth watching,
  not worth acting on.

### A new one, from a category that did not exist when this was written

PSI now scores **Agentic Browsing**, "high-quality, browsable websites for AI
agents", and the archive scored **1 of 2**. The failure was
`ARIA role should be appropriate for the element`, and the element was the
fold on the time axis: `<a role="row">`.

That is the same element as finding 4's ARIA item, and the first fix only went
half way. Removing `aria-expanded` answered the attribute and left the claim it
hung on standing: a grid owns rows, and a link is not a row. It is now a real
`<div role="row">` holding one `role="gridcell"` with `aria-colspan={14}`,
holding the link. Every level says what it is, the grid owns nothing but rows,
and the strip is still one click of the same size in the same place.

Worth saying why this category is worth passing here rather than treating as a
curiosity: this archive publishes an MCP endpoint and an /ask agent that use
its own tools. A record built to be read by machines should not fail the audit
that asks whether machines can read it.

## Noticed while verifying, and not acted on

**The wordmark is clipped mid-word on a phone.** At 375px the brand link is
39px wide against a 56px name, so the header reads "Pasc / Wat" over two lines.
This is half a deliberate decision: `SiteHeader.module.css` gives the brand
`overflow: hidden` below 40rem on purpose, because the row cannot wrap and the
brand is the one item that still says what it is when shortened. The comment
there describes the endpoint as "clipped to the mark alone", which is a clean
outcome; a name broken across two lines and cut on both is not the same thing,
and is most likely a wrap the author did not expect. Lighthouse cannot see this,
which is why it is not among the findings above. What to give up instead at that
width — the repo link, nav padding, the second line of the lockup — is a design
call rather than a fix.

## Plan, in order

1. ~~**Defer the YouTube player until first play.**~~ Done. `PlayerProvider`
   builds the player on the first `play()` rather than on mount, and still
   never tears it down. Removes ~987 KiB from every page view, and with it the
   Cookie issue and the font-display failure. CLS still wants re-measuring.
2. ~~**Stop the admin-session probe on public pages.**~~ Done. The console
   writes a readable `civic_operator` mark beside the httpOnly session, and the
   reading surfaces ask only when the mark is there. A reader sends nothing.
   The session cookie, the loopback listener and the port boundary are
   unchanged: the mark says whether it is worth asking, never who is asking.
3. **Decide the caching story per route.** Header shipped; back/forward cache
   not yet proven to be restored. Finish this by re-running Lighthouse against
   production once this deploys, and by finding the framework-level blocker if
   it names one.
4. ~~**Contrast and ARIA** on meeting and browse.~~ Done.
5. **Touch targets.** The time axis is done at all three widths. The timeline
   bands (10px) and the answer's `.itemHead` and `.at` are not, and both are a
   design decision rather than a fix: the bands are deliberately thin, and the
   answer's two are inline controls in prose, which WCAG 2.5.8 exempts and
   Lighthouse flags anyway.
6. **Render-blocking CSS, legacy JS, unused bundles.** Cleanup.

Re-run the sixteen-run sweep after 1–3 and compare against the table above.
Then run PSI separately for the absolute numbers, since this harness is
optimistic.
