"use client";

import Link from "next/link";
import { useState } from "react";

import type { MonthCell } from "@/lib/types";
import s from "./TimeAxis.module.css";

/* Numbers, not initials. "J F M A M J J A S O N D" has J three times and M
 * twice, so half the columns could not be told apart without counting along
 * the row - and the row underneath is what a reader is trying to read. The
 * full name is still on every cell's label for a screen reader and a hover. */
const MONTHS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"];
const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/**
 * meetings on a time axis, scanned year → month → meeting. the design asks
 * for time to be a first-class visual affordance rather than a list with dates
 * on it, and this collection is twelve years of a recurring event, so the axis
 * is the natural spine of the whole page.
 *
 * Drawn together, the shape that emerges is the honest one: twelve years of
 * published record and a fraction of that in recordings. The early years hold
 * almost none - a reader looking at 2016 should be able to see that the
 * recording is not missing, it never existed. (The first recorded year is
 * FOUND, at `firstRecorded`, and it moved from 2018 to 2017 the day a
 * mis-dated workshop was attached to its meeting.) A count alone would
 * hide that, and a site-wide disclaimer would be ignored.
*/
/**
 * How many years of month rows stand open before the rest fold away: the most
 * recent year that actually held a meeting, and the four before it.
 *
 * Counted from the DATA, never from `new Date()`. This renders on the server
 * and the result is cached; a window measured against the wall clock would
 * quietly disagree with the grid it is drawn over the moment a cached page
 * outlived the new year, and the failure would be invisible.
 */
const OPEN_YEARS = 5;

export function TimeAxis({
  months,
  year,
  month,
  body,
  initialExpanded = false,
}: {
  months: MonthCell[];
  /** Currently selected, from the URL. */
  year?: string;
  month?: string;
  /** Carried through every cell link, so picking a month keeps the board. */
  body?: string;
  /** Every year row drawn on arrival, from `?axis=all`. */
  initialExpanded?: boolean;
}) {
  /*
   *  A CLIENT COMPONENT for the same reason the subject strip is one: opening
   * the earlier years threw the whole document away and rebuilt it to add
   * seven rows. The URL still says what is open, and the control is still a
   * real link that works without script - the click is simply intercepted
   * when there is script to intercept it with.
  */
  const [expanded, setExpanded] = useState(initialExpanded);

  const href = (next: { year?: string; month?: string; axis?: string }) => {
    const p = new URLSearchParams();
    if (body) p.set("body", body);
    const y = "year" in next ? next.year : year;
    const m = "month" in next ? next.month : month;
    if (y) p.set("year", y);
    if (m) p.set("month", m);
    if (next.axis) p.set("axis", next.axis);
    const qs = p.toString();
    return qs ? `/?${qs}` : "/";
  };

  const toggle = (open: boolean) => {
    setExpanded(open);
    if (typeof window === "undefined") return;
    const u = new URL(window.location.href);
    if (open) u.searchParams.set("axis", "all");
    else u.searchParams.delete("axis");
    window.history.replaceState(null, "", u);
  };

  if (!months.length) return null;

  const byMonth = new Map(months.map((m) => [m.month, m]));
  const years: string[] = [];
  for (const m of months) {
    const y = m.month.slice(0, 4);
    if (years[years.length - 1] !== y) years.push(y);
  }
  years.sort();

  const totals = (ys: string[]) =>
    ys.reduce(
      (a, y) => {
        for (let i = 1; i <= 12; i++) {
          const c = byMonth.get(`${y}-${String(i).padStart(2, "0")}`);
          a.meetings += c?.meetings ?? 0;
          a.recorded += c?.recorded ?? 0;
          a.scheduled += c?.scheduled ?? 0;
        }
        return a;
      },
      { meetings: 0, recorded: 0, scheduled: 0 },
    );

  /* The window is anchored to the last year that HELD something, not to the
   * last row: the calendar runs ahead of the record - 2027 exists with four
   * scheduled meetings and nothing in it - and anchoring on that would spend
   * one of five open rows on a year with no archive behind it. */
  const heldYears = years.filter((y) => totals([y]).meetings > 0);
  const latest = heldYears[heldYears.length - 1] ?? years[years.length - 1];
  const from = String(Number(latest) - (OPEN_YEARS - 1));

  /* Earlier years fold; later ones never do. A year AHEAD of the record is one
   * row carrying the only forward-looking thing on this page, and burying it
   * under a control labelled with older years would be the wrong shape as well
   * as the wrong claim. */
  /* The summary row is ALWAYS the control, in one place. It used to vanish on
   * expanding and be replaced by a "show the last five years only" link under
   * the whole grid, so the thing you had just pressed moved to the far side of
   * seven new rows - and pressing it again moved it back. A control that
   * relocates as a result of being used is one a reader has to re-find every
   * time. It stays at the head of the year rows and only its verb changes. */
  const earlier = years.filter((y) => y < from);
  const shown = expanded ? years : years.filter((y) => !earlier.includes(y));
  const fold = totals(earlier);
  /* Stated rather than implied. The gradient used to carry "there is no video
   * before 2018" on its own, and folding those rows takes that with them - so
   * the summary says it in words, and finds the year rather than being told
   * it. */
  const firstRecorded = years.find((y) => totals([y]).recorded > 0);

  // One scale for the whole grid, so a busy month in 2016 and a busy month in
  // 2025 are the same weight. Per-year scaling would flatten the ramp, which
  // is the most informative thing here.
  const held = months.filter((m) => m.meetings > 0).map((m) => m.meetings);
  const peak = Math.max(1, ...held);
  const floor = Math.min(...held);

  /* Scaled across the range the data actually occupies, not across 0..peak.
   *
   * No month in twelve years has fewer than 3 meetings or more than 20, so
   * measuring from zero spends most of the scale on counts that never happen.
   * Against 0..20 a square root put every month between 0.39 and 1.00 with a
   * median of 0.59 - monotonic, honest, and visually flat, which is how the
   * grid came to read as blank paper twice for opposite reasons.
   *
   * Measuring from 3..20 instead uses the whole ramp: the 2015 average lands
   * near 0.20 and the 2024 average near 0.59, so the doubling of county
   * business over the decade is legible as a gradient rather than inferred
   * from the totals column. LOW keeps the quietest month clearly tinted,
   * because a quiet month must never look like an empty one. */
  const LOW = 0.14;
  const fill = (n: number) =>
    LOW + (1 - LOW) * (peak === floor ? 1 : (n - floor) / (peak - floor));

  return (
    <section className={s.wrap} aria-labelledby="axis-head">
      <div className={s.head}>
        {/* Derived, not written. The count was hardcoded as "twelve" and the
            grid grew a thirteenth row the moment the calendar ran into 2027. */}
        <h2 id="axis-head" className={s.title}>
          {years[0]}&ndash;{years[years.length - 1]}, by month
        </h2>
        <p className={s.legend}>
          <span className={s.key}>
            <span aria-hidden className={`${s.swatch} ${s.swLow}`} />
            <span aria-hidden className={`${s.swatch} ${s.swMid}`} />
            <span aria-hidden className={`${s.swatch} ${s.swHigh}`} />
            meetings held
          </span>
          <span className={s.key}>
            <span aria-hidden className={s.swRec} />
            of those, recorded
          </span>
          <span className={s.key}>
            <span aria-hidden className={`${s.swatch} ${s.swAhead}`} />
            scheduled, not yet held
          </span>
        </p>
      </div>

      <div className={s.grid} role="grid" aria-label="Meetings by year and month">
        <div className={s.row} role="row">
          <span className={s.corner} role="columnheader" />
          {MONTHS.map((m, i) => (
            <span key={i} className={s.colHead} role="columnheader" aria-label={MONTH_NAMES[i]}>
              {m}
            </span>
          ))}
          <span className={s.rowTotal} role="columnheader">
            all
          </span>
        </div>

        {/*
          *  A ROW THAT IS A CONTROL IS STILL A ROW.
          *
          * This was one `<a role="row">`, which is two claims that do not sit
          * together: a grid owns rows, and a link is not one. It carried
          * `aria-expanded` as well, which belongs to a treegrid and not to a
          * grid, and the note that used to be here reasoned about that
          * attribute alone while leaving the role it hung on in place.
          *
          * So: a real row, holding one cell that spans the width of the
          * others, holding the link. Every level says what it is, the grid
          * still owns nothing but rows, and the whole strip is still one
          * click. `aria-colspan` because the other rows have fourteen cells
          * and this one covers them all; without it the row reads as a
          * fourteen-column row with one column filled.
          *
          * NO `aria-expanded` anywhere: the label says both the action and,
          * by saying it, the state.
          */}
        {earlier.length ? (
          <div className={s.foldRow} role="row">
          <span className={s.foldCell} role="gridcell" aria-colspan={14}>
          <a
            href={href({ axis: expanded ? undefined : "all" })}
            className={`${s.row} ${s.fold}`}
            onClick={(e) => {
              if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button) return;
              e.preventDefault();
              toggle(!expanded);
            }}
          >
            {/*
              *  THE NAME CONTAINS WHAT THE ROW SAYS, because it is built out
              * of it.
              *
              * This was an `aria-label`, which REPLACES the content rather
              * than adding to it, and the label it wrote said "2015 to 2021"
              * where the row says "2015-2021" and left out "none before 2017"
              * altogether. So the accessible name did not contain the visible
              * text, which is WCAG 2.5.3, and matters most to somebody driving
              * the page by voice: they say what they can see, and nothing here
              * answered to it.
              *
              * It went unnoticed because the anchor used to claim
              * `role="row"`, and the rule only applies to a control. Making
              * the markup honest is what exposed it.
              *
              * A prefix instead: the verb and the count, then the row's own
              * words. "Show 7 earlier years: 2015-2021 507 meetings, 88
              * recorded, none before 2017".
              */}
            <span className="sr-only">
              {expanded ? "Hide" : "Show"} {earlier.length} earlier years:{" "}
            </span>
            <span className={s.foldYears}>
              {earlier[0]}&ndash;{earlier[earlier.length - 1]}
            </span>
            <span className={s.foldSays}>
              {fold.meetings.toLocaleString()} meetings
              {fold.recorded ? `, ${fold.recorded} recorded` : ""}
              {/* Explicit: JSX strips the newline between two expressions, so
                  without it this reads "recorded· none before". */}
              {firstRecorded && firstRecorded > earlier[0] ? (
                <>
                  {" "}
                  {/* "none before 2017" rather than "nothing recorded before
                      2017": the clause before it already says "recorded", and
                      saying it twice in one line reads as a stutter. */}
                  <span className={s.foldNone}>
                    &middot; none before {firstRecorded}
                  </span>
                </>
              ) : null}
            </span>
            <span className={s.foldGo} aria-hidden>
              {expanded ? "hide" : "show"}
            </span>
          </a>
          </span>
          </div>
        ) : null}

        {shown.map((y) => {
          const cells = MONTHS.map((_, i) =>
            byMonth.get(`${y}-${String(i + 1).padStart(2, "0")}`),
          );
          const total = cells.reduce((n, c) => n + (c?.meetings ?? 0), 0);
          const rec = cells.reduce((n, c) => n + (c?.recorded ?? 0), 0);
          const ahead = cells.reduce((n, c) => n + (c?.scheduled ?? 0), 0);
          return (
            <div key={y} className={s.row} role="row">
              {/* STRUCTURE OUTSIDE, APPEARANCE INSIDE, all the way down this
                  grid. A link is allowed to take on a handful of roles -
                  button, tab, option, treeitem - and the roles a grid is built
                  from are not among them: `rowheader` and `gridcell` on an
                  `<a>` say "this is a cell" over the top of "this is a link",
                  and the second one is the true statement. Every structural
                  role here is therefore on a span that does nothing else, and
                  the link, or the coloured block, sits inside it. */}
              <span className={s.headCell} role="rowheader">
                <Link
                  href={href({ year: year === y ? undefined : y, month: undefined })}
                  className={`${s.yearHead} ${year === y ? s.on : ""}`}
                  aria-current={year === y ? "true" : undefined}
                >
                  {y}
                </Link>
              </span>

              {cells.map((c, i) => {
                const key = `${y}-${String(i + 1).padStart(2, "0")}`;
                const label = `${MONTH_NAMES[i]} ${y}`;
                /* Three states, not two. A month with meetings on the county's
                 * calendar that have not happened yet is not an empty month,
                 * and drawing it as one told the reader that nothing was
                 * scheduled for the rest of 2026 when 30 meetings were - the
                 * exact error this axis exists to prevent, committed by the
                 * axis itself. It is not a link: there is nothing to read. */
                if (c && !c.meetings && c.scheduled) {
                  return (
                    <span
                      key={key}
                      role="gridcell"
                      className={s.holder}
                      title={`${label}: ${c.scheduled} scheduled, not yet held`}
                      aria-label={`${label}, ${c.scheduled} meetings scheduled, not yet held`}
                    >
                      <span className={`${s.cell} ${s.ahead}`} />
                    </span>
                  );
                }
                if (!c || !c.meetings) {
                  return (
                    <span
                      key={key}
                      role="gridcell"
                      className={s.holder}
                      title={`${label}: no meetings`}
                      aria-label={`${label}, no meetings`}
                    >
                      <span className={`${s.cell} ${s.empty}`} />
                    </span>
                  );
                }
                const on = month === key;
                // August 2026 is 8 held and 5 still to come. The reader is
                // standing inside that month, and the cell should say so.
                const also = c.scheduled ? `, ${c.scheduled} not yet held` : "";
                return (
                  <span key={key} role="gridcell" className={s.holder}>
                    <Link
                      href={href({ year: undefined, month: on ? undefined : key })}
                      className={`${s.cell} ${on ? s.on : ""} ${c.scheduled ? s.part : ""}`}
                      aria-current={on ? "true" : undefined}
                      /* A number per cell would be 4px tall at this density, so
                         the count is in the label where a screen reader and a
                         hover both reach it. */
                      aria-label={`${label}, ${c.meetings} meetings, ${c.recorded} recorded${also}`}
                      title={`${label}: ${c.meetings} meetings, ${c.recorded} recorded${also}`}
                      style={{ "--fill": fill(c.meetings).toFixed(3) } as React.CSSProperties}
                    >
                      {c.recorded ? (
                        <span
                          aria-hidden
                          className={s.rec}
                          style={{ "--rec": (c.recorded / c.meetings).toFixed(3) } as React.CSSProperties}
                        />
                      ) : null}
                    </Link>
                  </span>
                );
              })}

              <span
                className={s.rowTotal}
                title={
                  `${total} meetings held in ${y}, ${rec} recorded` +
                  (ahead ? ` · ${ahead} scheduled` : "")
                }
              >
                {total || <span className={s.aheadTotal}>{ahead}</span>}
              </span>
            </div>
          );
        })}
      </div>

    </section>
  );
}
