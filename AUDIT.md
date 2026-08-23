# Front-end audit, 23 August 2026

Every page template, mobile and desktop, measured rather than sampled. What
follows is the evidence first and the plan second, because two of the six
findings are the same cause wearing different hats and that is only obvious
once the numbers are side by side.

## What has been done since, 23 August 2026

Items 1, 2, 3 and 4 of the plan below are done, and most of 5. **The sixteen
runs have been repeated against the deployed site** with the same tool, the same
flags and the same eight URLs, so every number below is a like-for-like
comparison rather than an estimate.

| plan item | state | what was verified |
|---|---|---|
| 1. Defer the YouTube player | done | `/about`, `/search`, `/`, `/meeting/:id` load with **0 YouTube requests and no iframe**. Clicking a citation builds the player and the recording plays: no autoplay was lost. |
| 2. Stop the admin probe | done | `/search` with hits, `/meeting/:id` and `/ask` make **no request to `/api/admin/*`** and log no console error. |
| 3. Caching | done | `bf-cache` scores **1 with zero blocking reasons on all sixteen runs**, against 0 with two reasons on all sixteen before. Documents and RSC payloads no longer carry `no-store`; `/admin` still does; `/_next/static` keeps `immutable`. |
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

One thing looked as though it had not taken. A hand-rolled back-button test
said the cache was still refused, on production and locally both. It was wrong,
and finding 2 below says how: Lighthouse, re-run against the deployed site,
scores the audit as passing on all sixteen.

### The sixteen runs again, 23 August 2026

Same tool, same flags, same URLs. `bf` is the `bf-cache` audit with its count of
blocking reasons; `agent` is the Agentic Browsing category, which did not exist
when the first sweep ran.

| page | ff | perf | a11y | best prac | agent | CLS | KiB | bf |
|---|---|---|---|---|---|---|---|---|
| browse | mobile | 97 | 97 → **100** | 96 → **100** | 100 | 0 | 1,432 → **388** | 0 (2) → **1 (0)** |
| browse | desktop | 100 | 94 → **100** | 96 → **100** | 100 | 0 | 1,434 → **390** | 0 (2) → **1 (0)** |
| about | mobile | 100 → 99 | 100 | 96 → **100** | 100 | 0 | 1,378 → **334** | 0 (2) → **1 (0)** |
| about | desktop | 100 | 100 | 96 → **100** | 100 | 0 | 1,377 → **334** | 0 (2) → **1 (0)** |
| search | mobile | 90 | 100 | 92 → **100** | 100 | 0 | 1,404 → **360** | 0 (2) → **1 (0)** |
| search | desktop | 100 | 100 | 92 → **100** | 100 | 0 | 1,411 → **369** | 0 (2) → **1 (0)** |
| ask | mobile | 99 | 100 | 96 → **100** | 100 | 0 | 1,393 → **348** | 0 (2) → **1 (0)** |
| ask | desktop | 100 | 100 | 96 → **100** | 100 | 0 | 1,393 → **348** | 0 (2) → **1 (0)** |
| meeting | mobile | 96 → 99 | 93 → **97** | 92 → **100** | 100 | 0 | 1,486 → **444** | 0 (2) → **1 (0)** |
| meeting | desktop | 99 → 100 | 93 → **97** | 92 → **100** | 100 | 0 | 1,486 → **442** | 0 (2) → **1 (0)** |
| item | mobile | 99 | 100 | 96 → **100** | 100 | 0 | 1,392 → **349** | 0 (2) → **1 (0)** |
| item | desktop | 100 | 100 | 96 → **100** | 100 | 0 | 1,394 → **352** | 0 (2) → **1 (0)** |
| case | mobile | 99 | 100 | 96 → **100** | 100 | 0 | 1,387 → **346** | 0 (2) → **1 (0)** |
| case | desktop | 100 | 100 | 96 → **100** | 100 | 0 | 1,391 → **349** | 0 (2) → **1 (0)** |
| answer | mobile | 97 | 96 | 92 → **100** | 100 | 0 | 1,442 → **397** | 0 (2) → **1 (0)** |
| answer | desktop | 100 | 100 | 92 → **100** | 100 | 0 | 1,442 → **397** | 0 (2) → **1 (0)** |

**Best practices is 100 on all sixteen**, from 92 and 96. **CLS is still 0 on
all sixteen**, which was the thing to protect. **Page weight is down by about a
megabyte everywhere**, 1,377-1,486 KiB to 334-444.

Performance barely moves here, and that is the harness rather than the site:
these were already 90-100 on a fast machine before any of this. PSI, throttled
on Google's hardware, is where the same change reads 88 → 98 on mobile. LCP and
TBT wander in both directions between the two sweeps (about mobile LCP 1.66 s →
1.95 s, meeting mobile 2.72 s → 2.10 s, search mobile TBT 24 ms → 66 ms); the
runs are single samples and that spread is noise, not signal.

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

**It did work, and the way I established that it had not was wrong.**

The check that said otherwise was a hand-rolled one: mark `window` on a page,
navigate away, press back, see whether the mark survived. In the automated
browser used for that test, a plain HTML file on the same server was restored
and a Next-rendered page was not, even stripped to a paragraph, so the reading
was "the header is necessary and not sufficient, and the remaining blocker is
in the framework". Both halves of that were reported here as fact.

Lighthouse says the opposite, on the deployed site, on every one of the sixteen
runs: `bf-cache` scores **1, "Page didn't prevent back/forward cache
restoration", with zero blocking reasons**, where before it scored 0 with two,
both naming `no-store`. Lighthouse drives a clean Chrome profile and performs a
real back navigation, and it is the instrument the original finding came from,
so it is the one that settles this.

What the hand-rolled test was measuring was its own browser. Worth remembering
next time an instrument disagrees with a purpose-built tool: the tool was not
run because Docker was down, and rather than say "unknown until Docker is up" I
went and built a substitute and believed it.

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

**And that fixed one element out of eight hundred.** Re-run after deploying, the
category still scored 1 of 2, now naming `<a role="rowheader">` on the year
label. The audit reports ONE failing element, so it cannot be read as a to-do
list: it is a sample of a class, and the class here was every link in the time
axis that had been handed a grid role. `rowheader` on the year, `gridcell` on
each of the 12 months of each row.

A link may take on a small set of roles — button, tab, option, treeitem — and
none of the roles a grid is built from is among them. So the rule for this
component, written into it: **structure outside, appearance inside.** Each grid
role is on a span that does nothing else, and the link, or the coloured block,
sits inside that span. Verified on a build: no anchor in the grid carries a role
at all, every child of the grid is a row, every cell measures 24px, the month
filter and the fold both still work, and the axis is pixel-identical at desktop
and at 375px. A scan of every `<a>` and `<Link>` in the app says the time axis
was the only place this happened.

Deployed and re-run: **2 of 2, "Accessibility tree is well-formed, all audits
passed"**, and the category scores 100 on all sixteen Lighthouse runs as well.

**The sweep also caught a fault the sweep's own fix had introduced.** With the
anchor no longer claiming `role="row"`, it is a link again, and a rule that only
applies to controls started applying: `label-content-name-mismatch`, which
scored 1 before this work and 0 after it. The `aria-label` said "2015 to 2021"
where the row says "2015-2021", and omitted "none before 2017" entirely, so the
accessible name did not contain the visible text. That is WCAG 2.5.3, and it
matters most to somebody driving the page by voice: they say what they can see.

`aria-label` REPLACES the content rather than adding to it, which is what made
the two drift apart in the first place. It is a visually hidden prefix now, so
the name is built out of the row's own words: "Show 7 earlier years: 2015-2021
507 meetings, 88 recorded, none before 2017". Verified with Lighthouse against a
local production build: the audit scores 1, accessibility 100, agentic 100.

The audit carries zero weight in the accessibility score, so nothing would have
shown it except reading the audit list. Worth knowing that a score of 100 is not
the same as no findings.

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
3. ~~**Decide the caching story per route.**~~ Done. `headers()` sets
   `private, no-cache, max-age=0, must-revalidate` on everything the public
   reads and keeps `no-store` on `/admin`. Confirmed restored: `bf-cache` 1
   with zero reasons, sixteen runs out of sixteen.
4. ~~**Contrast and ARIA** on meeting and browse.~~ Done.
5. **Touch targets.** The time axis is done at all three widths. What the
   re-run still fails is **three of sixteen**: meeting at both form factors and
   answer on mobile. Those are the timeline bands (10px) and the answer's
   `.itemHead` and `.at`, and both are a design decision rather than a fix: the
   bands are deliberately thin, and the answer's two are inline controls in
   prose, which WCAG 2.5.8 exempts and Lighthouse flags anyway. It is what
   keeps meeting at 97 rather than 100.
6. **Render-blocking CSS, legacy JS, unused bundles.** Cleanup.

Re-run the sixteen-run sweep after 1–3 and compare against the table above.
Then run PSI separately for the absolute numbers, since this harness is
optimistic.
