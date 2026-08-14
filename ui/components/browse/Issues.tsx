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
export function Issues({ d }: { d: IssuesData }) {
  if (!d.issues.length) return null;
  const yrs = d.span;

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
            <span aria-hidden className={s.swNone} />
            no recording of that year
          </span>
          <span className={s.scaleNote}>pale → deep is fewer → more, within each row</span>
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

        {d.issues.map((i) => (
          <Row key={i.slug} i={i} heardFrom={d.heard_from} />
        ))}
      </div>

      <p className={s.note}>
        We find each subject by its words. The record is matched in the published title
        of an item. The room is matched in the transcript, which is made by machine and
        can be wrong. An item the county titled in other words is not counted. Each row
        is shaded against its own busiest year, so a shade compares years within one
        subject and never one subject against another.
      </p>
    </section>
  );
}

function Row({ i, heardFrom }: { i: Issue; heardFrom: string }) {
  /* Scaled to the row, not to the grid. The time axis shares one scale across
   * its whole grid because the 2015 → 2025 ramp IS its finding. Here the rows
   * are different subjects: rezoning peaks at 213 items in a year and the
   * Orange Belt Trail at 4, so one scale would draw fourteen of these rows as
   * blank paper and say nothing about any of them. Each row answers "when was
   * this busy", and the totals column answers "how big is it". */
  const peakItems = Math.max(1, ...i.years.map((y) => y.items));
  const peakLines = Math.max(1, ...i.years.map((y) => y.lines));

  const href = (y?: IssueYear) =>
    `/search?q=${encodeURIComponent(i.q)}` +
    (y ? `&since=${y.year}-01-01&until=${y.year}-12-31` : "");

  return (
    <div className={s.row}>
      <span className={s.label}>
        {/* The counts in this row are of titles that name the issue; the link
            runs a search, which ranks the whole archive and will report its
            own total. Saying which words it searches for keeps the two from
            reading as the same claim. */}
        <Link href={href()} className={s.name} title={`Search the archive for “${i.q}”`}>
          {i.label}
        </Link>
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
            href={href(y)}
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
      </span>
    </div>
  );
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
  return parts.join("");
}
