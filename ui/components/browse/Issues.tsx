import Link from "next/link";

import type { Issue, IssueYear, Issues as IssuesData } from "@/lib/types";
import s from "./Issues.module.css";

/**
 * R5.1.4 — what the county keeps coming back to.
 *
 * The rest of browse is structural or recent. The coverage bars say how much
 * of the record we hold, the time axis says how many meetings there were, and
 * the three entryways below say what happened lately. None of them says what
 * any of it was ABOUT, so a page whose header claims twelve years spent all
 * twelve of them counting meetings.
 *
 * One strip of years per issue, because the totals are the least interesting
 * thing about these. Opioid settlement money arrives in 2021 and Moffitt in
 * 2020; the county has argued about impact fees since the first meeting the
 * archive holds; school zone speed cameras exist only in the recordings and
 * only in the last two years. A number cannot say that. Twelve cells can.
 *
 * TWO LANES, NOT ONE CELL WITH TWO THINGS IN IT.
 *
 * The first version drew the published record as the cell's tint and the
 * speech as a 3px bar along its bottom edge — two measures nested in one
 * 24px box, each normalised to its OWN peak. That is the dual-axis mistake
 * in miniature: the alignment of the two scales is arbitrary, so the picture
 * implies a relationship the data does not contain. A full-strength cell
 * meant "7 items and 122 lines" on Homelessness and "176 items and 1,155
 * lines" on Rezoning, and nothing said so.
 *
 * So each year is two thin lanes sharing one column: what the county
 * PUBLISHED above, what was SAID below. Same grammar in both — tint is
 * magnitude — and each lane scaled to its own row so a subject is compared
 * against its own history. The interesting shape is the disagreement between
 * the lanes: a subject the record goes quiet on while the room does not.
 * That was the hairline before.
 *
 * Every cell is a link into `/search` for that issue in that year (R4.2, R4.3)
 * — the counts here are found by wording, and the search page is where a
 * reader goes to see the items they were found in.
 */
export function Issues({
  d,
  open = [],
  href,
}: {
  d: IssuesData;
  /** Slugs whose sub-subjects are showing, from the URL. */
  open?: string[];
  href?: (next: { open?: string }) => string;
}) {
  if (!d.issues.length) return null;
  const yrs = d.span;

  /* Top-level rows in the order the API ranked them, each followed by its own
   * sub-subjects when open. Regrouped here rather than ordered in SQL because
   * the ranking is by how much county business a subject is, and a child
   * sorted on that measure lands nowhere near its parent.
   *
   * A subject narrows only when it grew too broad to answer anything - three
   * of twenty-seven did, at 2,109 items and up against 798 for the fourth -
   * so most rows have no children and nothing about them changes. */
  const kids = new Map<string, typeof d.issues>();
  for (const i of d.issues) {
    if (!i.parent) continue;
    const b = kids.get(i.parent);
    if (b) b.push(i);
    else kids.set(i.parent, [i]);
  }
  const shown: { i: (typeof d.issues)[number]; child: boolean }[] = [];
  for (const i of d.issues) {
    if (i.parent) continue;
    shown.push({ i, child: false });
    if (open.includes(i.slug)) {
      for (const k of kids.get(i.slug) ?? []) shown.push({ i: k, child: true });
    }
  }

  return (
    <section className={s.wrap} aria-labelledby="issues-head">
      <header className={s.head}>
        <h2 id="issues-head" className={s.title}>
          What the county keeps coming back to
        </h2>
        <p className={s.why}>
          These subjects return year after year. Each row is one subject, and each cell
          is one year of it, in both sources.
        </p>
        {/* The scale is stated BEFORE the grid, not in a note under it. The
            tint is the only thing carrying magnitude, and a reader who meets
            it unexplained has already misread the first row by the time the
            footnote arrives. */}
        <p className={s.legend}>
          <span className={s.key}>
            <span aria-hidden className={`${s.ramp} ${s.rampRecord}`} />
            items the county published
          </span>
          <span className={s.key}>
            <span aria-hidden className={`${s.ramp} ${s.rampSaid}`} />
            lines said in the recordings
          </span>
          <span className={s.key}>
            <span aria-hidden className={`${s.ramp} ${s.rampPushed}`} />
            the share of them the board did not simply pass
          </span>
          <span className={s.key}>
            <span aria-hidden className={s.swNone} />
            no recording, or no outcome recorded
          </span>
          {/* Two sentences because there are now two grammars, and conflating
              them is the whole risk of a third lane: the tints may only be
              read along a row, the bar may be read down the column. */}
          <span className={s.scaleNote}>
            pale &rarr; deep is fewer &rarr; more, within each row. The third lane is a
            proportion, so it alone compares across rows.
          </span>
        </p>
      </header>

      <div className={s.grid} style={{ "--years": yrs.length } as React.CSSProperties}>
        <div className={`${s.row} ${s.headRow}`} aria-hidden>
          <span />
          {yrs.map((y) => (
            <span
              key={y}
              className={`${s.colHead} ${y === d.heard_from ? s.gate : ""}`}
            >
              {y.slice(2)}
            </span>
          ))}
          <span className={s.colHead}>totals</span>
        </div>

        {shown.map(({ i, child }) => (
          <Row
            key={i.slug}
            i={i}
            heardFrom={d.heard_from}
            child={child}
            kids={kids.get(i.slug)?.length ?? 0}
            open={open.includes(i.slug)}
            href={href}
          />
        ))}
      </div>

      {/* R2.1 applies to the METHOD as much as to the numbers. The old note
          said "we find each subject by its words" and left a reader to assume
          the archive had discovered the subjects; a person had written
          eighteen of them down. Saying who chose them, and how the words were
          arrived at, is what makes the counts checkable rather than merely
          stated. */}
      <p className={s.note}>
        These subjects were not chosen by hand. A model read a sample of the county&rsquo;s
        own agenda titles and proposed the ones that recur; for each, it proposed the
        words this county actually uses &mdash; programme names, acronyms, the spoken
        wording &mdash; and every one of those was counted against this archive, with a
        real example, before a person kept or dropped it.
      </p>
      <p className={s.note}>
        Matching itself is literal: an item is counted when its published title contains
        one of those phrases, so these numbers are exact and repeatable rather than a
        judgement about each item. The room is matched the same way in the transcript,
        which is made by machine and can be wrong. An item the county titled in words no
        phrase covers is still not counted. Each row is shaded against its own busiest
        year, so a shade compares years within one subject and never one subject against
        another &mdash; except the third lane, which is a proportion.
      </p>
      <p className={s.note}>
        These rows are not a division of the archive and do not add up to it. One item
        can be two subjects at once &mdash; a comprehensive plan text amendment is a
        change to the plan and a land-use amendment both &mdash; and it is counted under
        each. Nothing on this page sums them.
      </p>
    </section>
  );
}

function Row({
  i,
  heardFrom,
  child = false,
  kids = 0,
  open = false,
  href,
}: {
  i: Issue;
  heardFrom: string;
  /** This row narrows the one above it. */
  child?: boolean;
  /** How many sub-subjects this row has, 0 for most. */
  kids?: number;
  open?: boolean;
  href?: (next: { open?: string }) => string;
}) {
  /* Scaled to the row, not to the grid. The time axis shares one scale across
   * its whole grid because the 2015 → 2025 ramp IS its finding. Here the rows
   * are different subjects: rezoning peaks at 213 items in a year and the
   * Orange Belt Trail at 4, so one scale would draw fourteen of these rows as
   * blank paper and say nothing about any of them. Each row answers "when was
   * this busy", and the totals column answers "how big is it". */
  const peakItems = Math.max(1, ...i.years.map((y) => y.items));
  const peakLines = Math.max(1, ...i.years.map((y) => y.lines));
  /* NOT a third tint ramp, and the reason is measurable: --live sits at hue
   * 22 and --no at hue 3, so two adjacent 10px bars carrying the same grammar
   * nineteen degrees apart are one bar with a gradient in it. Every other
   * palette entry is worse - green reads as approval for a lane that means
   * the opposite, amber is closer to --live still.
   *
   * So the third lane changes CHANNEL instead of hue: a proportional bar, its
   * width the share of that year's decided items the board did not simply
   * pass. That is honest rather than merely legible. Tint here is magnitude
   * scaled to a row, which is why a shade may never be read across rows; a
   * width is a rate, which may. Drawing a rate as a tint beside two
   * magnitudes would have been the dual-axis mistake this file already
   * refuses once, in the same 33px box. */

  /* Renamed off `href`, which is now the prop that opens a narrowing. Two
   * different link builders under one name in one component is how the wrong
   * one gets called. */
  const hrefQ = (y?: IssueYear) =>
    `/search?q=${encodeURIComponent(i.q)}` +
    (y ? `&since=${y.year}-01-01&until=${y.year}-12-31` : "");

  return (
    <div className={`${s.row} ${child ? s.childRow : ""}`}>
      <span className={s.label}>
        {/* The counts in this row are of titles that name the issue; the link
            runs a search, which ranks the whole archive and will report its
            own total. Saying which words it searches for keeps the two from
            reading as the same claim. */}
        <Link href={hrefQ()} className={s.name} title={`Search the archive for “${i.q}”`}>
          {i.label}
        </Link>
        {/* Only on a row that HAS sub-subjects, which is three of them. The
            control says how many rather than drawing a bare chevron, because
            the number is the reason to press it. */}
        {kids && href ? (
          <Link
            href={href({ open: open ? "" : i.slug })}
            className={s.narrow}
            aria-expanded={open}
          >
            {open ? "hide" : `${kids} kinds`}
          </Link>
        ) : null}
        {/* One line, and it does not wrap. Five facts wrapping to three lines
            gave every row a different height, which is most of what stopped
            the grid reading as a grid. The rest is on the hover and on the
            search page the title links to. */}
        <span className={s.sub} title={summary(i)}>
          {shortSummary(i)}
        </span>
      </span>

      {i.years.map((y) => {
        const said = y.lines > 0;
        const on = y.items > 0;
        const edge = y.year === heardFrom ? s.gate : "";
        // No recording of that year exists at all - a different claim from a
        // year nobody mentioned it, and the only one the said lane hatches.
        const unheard = y.year < heardFrom;
        /* Three states, and the third is the one that matters. `pushed` is 0
         * both when the board passed everything and when the minutes record
         * no outcome at all, and those are opposite facts (R6.3). So the lane
         * hatches when nothing that year was decided, exactly as the said
         * lane hatches for a year with no recording. */
        const undecided = on && !y.decided;
        const body = (
          <>
            <span
              aria-hidden
              className={`${s.lane} ${s.laneRecord} ${on ? "" : s.empty}`}
              style={{ "--fill": fill(y.items, peakItems) } as React.CSSProperties}
            />
            <span
              aria-hidden
              className={`${s.lane} ${s.laneSaid} ${unheard ? s.none : said ? "" : s.empty}`}
              style={{ "--fill": fill(y.lines, peakLines) } as React.CSSProperties}
            />
            <span
              aria-hidden
              className={`${s.lane} ${s.lanePushed} `
                + `${undecided ? s.none : y.decided ? "" : s.empty}`}
            >
              {y.pushed ? (
                <span
                  className={s.pushedFill}
                  style={{ "--share": (y.pushed / y.decided).toFixed(3) } as React.CSSProperties}
                />
              ) : null}
            </span>
          </>
        );
        // Nothing in either source. Not a link: there is nothing to open, and
        // a cell that navigates to an empty search is a dead end (R4.3).
        if (!on && !said) {
          return (
            <span
              key={y.year}
              className={`${s.cell} ${edge}`}
              title={`${i.label} — ${y.year}: nothing found`}
            >
              {body}
            </span>
          );
        }
        return (
          <Link
            key={y.year}
            href={hrefQ(y)}
            className={`${s.cell} ${edge}`}
            aria-label={cell(i, y, heardFrom)}
            title={cell(i, y, heardFrom)}
          >
            {body}
          </Link>
        );
      })}

      {/* Each number wears the lane's colour as a mark beside it, not as its
          own ink - the figure stays readable text either way. */}
      <span className={s.totals}>
        <span className={s.tRow} title={`${i.items.toLocaleString()} published items`}>
          <span aria-hidden className={`${s.dot} ${s.dotRecord}`} />
          {i.items.toLocaleString()}
        </span>
        <span className={s.tRow} title={`${i.lines.toLocaleString()} lines of speech`}>
          <span aria-hidden className={`${s.dot} ${s.dotSaid}`} />
          {i.lines ? i.lines.toLocaleString() : "—"}
        </span>
        {/* A RATE, and the only figure in this section that may honestly be
            read down the column: every tint here is scaled to its own row, so
            comparing two rows' shading says nothing, while "12% pushed back"
            against "0%" says exactly what it looks like. */}
        <span className={s.tRow} title={pushedSays(i)}>
          <span aria-hidden className={`${s.dot} ${s.dotPushed}`} />
          {decided(i) ? `${Math.round((pushed(i) / decided(i)) * 100)}%` : "—"}
        </span>
      </span>
    </div>
  );
}

const pushed = (i: Issue) => i.continued + i.refused + i.divided;
const decided = (i: Issue) => i.years.reduce((n, y) => n + y.decided, 0);

/** The percentage, said in full, because the figure alone hides its size:
 *  100% of one item and 12% of 147 are not the same claim. */
function pushedSays(i: Issue): string {
  const d = decided(i);
  if (!d) return "The minutes record no outcome for any of these items";
  const p = pushed(i);
  if (!p) return `All ${d.toLocaleString()} decided items passed — none continued, denied or split`;
  const bits = [];
  if (i.continued) bits.push(`${i.continued} continued`);
  if (i.refused) bits.push(`${i.refused} denied or no action`);
  if (i.divided) bits.push(`${i.divided} on a divided vote`);
  return `${p} of ${d.toLocaleString()} decided items — ${bits.join(", ")}`;
}

/* LOW keeps a year with one item clearly tinted: the quietest year an issue
 * appeared in must never look like a year it did not. */
const LOW = 0.16;
const fill = (n: number, peak: number) =>
  n <= 0 ? "0" : (LOW + (1 - LOW) * (n / peak)).toFixed(3);

/** How much of a subject's own activity the printed range has to cover. */
const ACTIVE_SHARE = 0.8;

/**
 * The years the subject was actually live.
 *
 * `first`–`last` is the first and last time it was EVER mentioned, and for
 * any recurring subject that is the archive's own span restated: ten of
 * eighteen rows read "2015–2026", which is true, identical, and tells a
 * reader nothing. One stray 2015 committee resolution stretched Connected
 * City across a decade it was not being argued about.
 *
 * So: the SHORTEST run of consecutive years holding 80% of the subject's
 * activity. Opioid settlement money comes out 2021–2025 and Moffitt
 * 2020–2025, which are the arrivals this component's own header claims and
 * could not previously show.
 *
 * MEASURED ON THE PUBLISHED RECORD, and on the room only when a subject has
 * no published items at all. Counting both together measures coverage as
 * much as subject: the room begins in 2018 and reaches 23% of meetings, so
 * every window it touches is dragged forward into the years a camera
 * existed. Stormwater is the case that shows it - its record lane is at its
 * darkest in 2015-2017 and the two-source answer called it "mostly
 * 2018-2026", contradicting the strip beside it. The API's own ranking
 * refuses to mix them for the same reason.
 */
function activeSpan(i: Issue): { label: string; whole: boolean } | null {
  const w = i.items
    ? i.years.map((y) => y.items)
    : i.years.map((y) => y.lines);
  const total = w.reduce((a, b) => a + b, 0);
  if (!total) return null;

  const need = total * ACTIVE_SHARE - 1e-9;
  let best: [number, number] | null = null;
  for (let a = 0; a < w.length; a++) {
    let sum = 0;
    for (let b = a; b < w.length; b++) {
      sum += w[b];
      if (sum >= need) {
        if (!best || b - a < best[1] - best[0]) best = [a, b];
        break;
      }
    }
  }
  if (!best) return null;
  const [a, b] = best;
  const from = i.years[a].year;
  const to = i.years[b].year;
  // "Whole" means the window already holds every year with anything in it,
  // not that it spans the archive. License plate cameras has one published
  // item, in 2024: there is no "mostly" about it, and saying so of a single
  // year reads as a distribution the row does not have.
  const outside = w.some((n, k) => n > 0 && (k < a || k > b));
  return { label: from === to ? from : `${from}–${to}`, whole: !outside };
}

/** The one line that fits beside the strip. The dispositions are real detail
 *  but they are five facts deep in an eleven-pixel line and they cost the
 *  grid its rhythm; `summary()` still supplies them, and the full extent, to
 *  the hover. */
function shortSummary(i: Issue): string {
  const active = activeSpan(i);
  // "mostly", because this is where the subject lives and not where it
  // begins and ends. Dropped when the run covers every year the archive
  // holds, since then there is no "mostly" about it.
  const when = active
    ? `${active.whole ? "" : "mostly "}${active.label}`
    : `${i.first.slice(0, 4)}–${i.last.slice(0, 4)}`;
  if (!i.items) {
    return `${when} · heard in ${i.heard} recorded `
      + `${i.heard === 1 ? "meeting" : "meetings"}, never in a published title`;
  }
  return `${when} · ${i.meetings} ${i.meetings === 1 ? "meeting" : "meetings"}`;
}

/** The row's own line, for the hover. This one keeps the FULL extent — the
 *  first and last time the subject was mentioned at all — because the visible
 *  line now shows where it lived instead, and a reader who wants to know
 *  whether it was ever raised in 2015 should still have somewhere to find
 *  out. Only what is not zero: a row of zeroes is noise. */
function summary(i: Issue): string {
  const out = [`first mentioned ${i.first.slice(0, 4)}, last ${i.last.slice(0, 4)}`];
  if (i.meetings) out.push(`${i.meetings} ${i.meetings === 1 ? "meeting" : "meetings"}`);
  if (i.continued) out.push(`${i.continued} continued`);
  if (i.refused) out.push(`${i.refused} denied`);
  if (i.divided) out.push(`${i.divided} on a divided vote`);
  // Nothing published at all: the whole of what we hold is speech, and the
  // row must say so rather than show an empty record lane and let a reader
  // read it as an issue nobody ever acted on.
  if (!i.items) {
    out.push(`heard in ${i.heard} recorded ${i.heard === 1 ? "meeting" : "meetings"},`
      + " never in a published title");
  }
  return out.join(" · ");
}

/** One cell, in a sentence, for a screen reader and for a hover. */
function cell(i: Issue, y: IssueYear, heardFrom: string): string {
  const parts = [`${i.label} — ${y.year}: `];
  parts.push(
    y.items
      ? `${y.items} published ${y.items === 1 ? "item" : "items"} in ${y.meetings} ${y.meetings === 1 ? "meeting" : "meetings"}`
      : "nothing in the published record",
  );
  if (y.lines) {
    parts.push(`, ${y.lines} ${y.lines === 1 ? "line" : "lines"} said in ${y.heard} recorded ${y.heard === 1 ? "meeting" : "meetings"}`);
  } else if (y.year < heardFrom) {
    parts.push(", and no recording of that year exists");
  }
  /* The rate the lane deliberately does not draw. Said against `decided`
   * rather than against `items`, because an item the minutes never disposed of
   * is not an item that passed. */
  if (y.items && !y.decided) {
    parts.push(", and the minutes record no outcome for any of them");
  } else if (y.pushed) {
    parts.push(
      `; the board continued, denied or split over ${y.pushed} of the ${y.decided} it decided`,
    );
  } else if (y.decided) {
    parts.push(`; all ${y.decided} it decided passed`);
  }
  return parts.join("");
}
