# Front-end audit, 23 August 2026

Every page template, mobile and desktop, measured rather than sampled. What
follows is the evidence first and the plan second, because two of the six
findings are the same cause wearing different hats and that is only obvious
once the numbers are side by side.

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

**Fix.** This needs care rather than a flag: the pages are dynamic because the
archive changes and because `/admin` must never be cached. The work is to
decide which routes can carry an ordinary `max-age`/`s-maxage` — the reading
surfaces, almost certainly — and to stop the `no-store` on API fetches from
propagating to the document. Chrome classes the reason as "Not actionable",
which means Lighthouse cannot fix it, not that we cannot.

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

## Plan, in order

1. **Defer the YouTube player until first play.** One component, one flag.
   Removes ~987 KiB from every page view, and with it the Cookie issue and the
   font-display failure. Re-measure CLS.
2. **Stop the admin-session probe on public pages.** Removes the site's only
   console error and a wasted request on three templates.
3. **Decide the caching story per route** and recover back/forward cache for
   the reading surfaces. Largest remaining win and the one needing the most
   thought.
4. **Contrast and ARIA** on meeting and browse. Two small, unambiguous fixes.
5. **Touch targets** on the timeline, time axis and answer. Needs a design
   decision first.
6. **Render-blocking CSS, legacy JS, unused bundles.** Cleanup.

Re-run the sixteen-run sweep after 1–3 and compare against the table above.
Then run PSI separately for the absolute numbers, since this harness is
optimistic.
