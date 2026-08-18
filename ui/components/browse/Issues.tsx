"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

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
  initialOpen = [],
}: {
  d: IssuesData;
  /** Slugs open on arrival, from `?open=` so a link still carries the view. */
  initialOpen?: string[];
}) {
  /* A CLIENT COMPONENT, for one reason: opening a theme must not reload the
   * page. It was links into `?open=`, which is shareable and works with no
   * script, and which also threw the whole document away and rebuilt it to
   * reveal four rows.
   *
   * So both. The control is still an `<a href>` with a real URL - without
   * script it navigates, exactly as before - and with script the click is
   * intercepted, the state moves locally, and `replaceState` writes the same
   * URL without a navigation. The link stays copyable and the view stays
   * instant.
   *
   * `replaceState` rather than `pushState`: expanding a row is not somewhere
   * you were, and making Back undo an indent one step at a time would bury
   * the page a reader actually came from. */
  const [open, setOpen] = useState<string[]>(initialOpen);
  const toggle = useCallback((slug: string) => {
    setOpen((was) => {
      const now = was.includes(slug) ? was.filter((v) => v !== slug) : [...was, slug];
      if (typeof window !== "undefined") {
        const u = new URL(window.location.href);
        if (now.length) u.searchParams.set("open", now.join(","));
        else u.searchParams.delete("open");
        window.history.replaceState(null, "", u);
      }
      return now;
    });
  }, []);

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
  /* Three levels now — eight themes over twenty-seven subjects over twelve
   * sub-subjects — so this walks rather than looking one deep. Opening a row
   * reveals its WHOLE subtree at once: a reader who asks what land use is
   * made of wants to see it, not to open four more things, and the deepest
   * level is the one they were looking for. */
  const shown: { i: (typeof d.issues)[number]; level: number }[] = [];
  const walk = (row: (typeof d.issues)[number], level: number) => {
    shown.push({ i: row, level });
    if (!open.includes(row.slug)) return;
    for (const k of [...(kids.get(row.slug) ?? [])].sort((a, b) => b.items - a.items)) {
      walk(k, level + 1);
    }
  };
  for (const i of d.issues) if (!i.parent) walk(i, 0);

  return (
    <section className={s.wrap} aria-labelledby="issues-head">
      <header className={s.head}>
        <h2 id="issues-head" className={s.title}>
          What the county keeps coming back to
        </h2>
        {/* The scale is stated BEFORE the grid, not in a note under it. The
            tint is the only thing carrying magnitude, and a reader who meets
            it unexplained has already misread the first row by the time the
            footnote arrives. */}
        {/* One clause. Four keys and a two-sentence scale note were the price
            of three encodings; one encoding costs the single thing height
            cannot say for itself, which is what it is measured against. */}
        <p className={s.legend}>
          Bars are published items per year, each row against its own busiest year.
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

        {shown.map(({ i, level }) => (
          <Row
            key={i.slug}
            i={i}
            heardFrom={d.heard_from}
            level={level}
            kids={kids.get(i.slug)?.length ?? 0}
            open={open.includes(i.slug)}
            onToggle={toggle}
          />
        ))}
      </div>

      {/* ONE line. This was three paragraphs - who chose the subjects, how the
          words were arrived at, what the shading may and may not be compared
          against, and that the rows overlap - 158px of defending the method to
          a reader who has not yet doubted it. The claims were all true and
          none of them were what somebody came here to read. What survives is
          the part a reader needs to interpret the numbers at all; the rest is
          in `bin/subjects.py`, where somebody who does doubt it will look. */}
      <p className={s.note}>
        Subjects and the words that find them are derived from the county&rsquo;s own
        agenda titles. Matching is literal, so these counts are exact. Rows overlap
        and do not sum.
      </p>
    </section>
  );
}

function Row({
  i,
  heardFrom,
  level = 0,
  kids = 0,
  open = false,
  onToggle,
}: {
  i: Issue;
  heardFrom: string;
  /** 0 for a theme, 1 for a subject under it, 2 for a sub-subject. */
  level?: number;
  /** How many rows narrow this one, 0 for a leaf. */
  kids?: number;
  open?: boolean;
  onToggle?: (slug: string) => void;
}) {
  /* ONE BAR PER YEAR, AND ITS HEIGHT IS THE WHOLE ENCODING.
   *
   * This was three lanes in a 33px cell: the published record as a tint, what
   * was said as a second tint, and the share the board did not simply pass as
   * a proportional bar. Every one of them was defensible on its own and the
   * cell was unreadable without the legend - two grammars, three colours, and
   * a rule that shades compare along a row but the third lane compares down
   * the column. Nobody arrives willing to learn that.
   *
   * Height needs no key. Taller is more, and a reader already knows it.
   *
   * The bar counts PUBLISHED ITEMS and nothing else, which is what makes one
   * bar honest. Adding the speech in would put a step at 2018 in every row on
   * this page - not because the county got busier, but because that is when a
   * camera first ran - and the strip would be drawing our coverage while
   * appearing to draw the county's. The speech is still here, as a number, in
   * words, in the totals.
   *
   * Scaled to its own row, so a subject with 39 items is legible beside one
   * with 5,835. That is the one thing height cannot say for itself, and it is
   * the only clause left in the legend. */
  const peak = Math.max(1, ...i.years.map((y) => y.items));

  const hrefQ = (y?: IssueYear) =>
    `/search?q=${encodeURIComponent(i.q)}` +
    (y ? `&since=${y.year}-01-01&until=${y.year}-12-31` : "");

  return (
    <div
      className={`${s.row} ${level ? s.childRow : ""}`}
      style={{ "--level": level } as React.CSSProperties}
    >
      <span className={s.label}>
        <span className={s.nameRow}>
          {/* The counts are of titles that name the subject; the link runs a
              ranked search over the whole archive and reports its own total.
              The hover says which words, so the two do not read as one claim. */}
          <Link href={hrefQ()} className={s.name} title={`Search the archive for “${i.q}”`}>
            {i.label}
          </Link>
          {kids && onToggle ? (
            <a
              href={`?open=${encodeURIComponent(i.slug)}`}
              className={s.narrow}
              aria-expanded={open}
              onClick={(e) => {
                if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button) return;
                e.preventDefault();
                onToggle(i.slug);
              }}
            >
              {open ? "hide" : `show ${kids}`}
            </a>
          ) : null}
        </span>
        <span className={s.sub} title={summary(i)}>
          {shortSummary(i)}
        </span>
      </span>

      {i.years.map((y) => {
        const label = cell(i, y, heardFrom);
        if (!y.items) {
          return (
            <span
              key={y.year}
              className={s.cell}
              title={`${i.label} — ${y.year}: nothing published`}
            />
          );
        }
        return (
          <Link key={y.year} href={hrefQ(y)} className={s.cell}
                aria-label={label} title={label}>
            <span aria-hidden className={s.bar}
                  style={{ "--h": height(y.items, peak) } as React.CSSProperties} />
          </Link>
        );
      })}

      {/* Words, not a colour key. Three dots each standing for a lane meant
          the totals could only be read by somebody who had decoded the
          legend, which is what this row was asked to stop requiring. */}
      <span className={s.totals}>
        <span className={s.tRow}>
          <b className={s.tN}>{i.items.toLocaleString()}</b> filed
        </span>
        <span className={s.tRow}>
          <b className={s.tN}>{i.lines ? i.lines.toLocaleString() : "\u2014"}</b> said
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

/* LOW keeps a year with one item visible: the quietest year a subject
 * appeared in must never look like a year it did not appear at all. At a 22px
 * cell that floor is about 4px, which reads as a mark rather than as dust. */
const LOW = 0.18;
const height = (n: number, peak: number) =>
  n <= 0 ? "0" : (LOW + (1 - LOW) * (n / peak)).toFixed(3);

/**
 * How often the board did not simply pass, as a phrase rather than a percent.
 *
 * "16%" makes a reader do the work of turning a rate back into a thing that
 * happens; "about 1 in 6" is the same number already turned. Rounded to a
 * whole ratio on purpose - the precision was never real, since it rests on
 * which items the minutes recorded an outcome for at all.
 */
function contested(i: Issue): string {
  const d = decided(i);
  if (!d) return "no outcomes recorded";
  const p = pushed(i);
  if (!p) return "none contested";
  const n = Math.round(d / p);
  return n <= 1 ? "nearly all contested" : `1 in ${n} contested`;
}

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
  return `${when} · ${i.meetings} ${i.meetings === 1 ? "meeting" : "meetings"}`
    + ` · ${contested(i)}`;
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
  const bits = [`${i.label} — ${y.year}: ${y.items} published `
    + `${y.items === 1 ? "item" : "items"} in ${y.meetings} `
    + `${y.meetings === 1 ? "meeting" : "meetings"}`];
  if (y.lines) {
    bits.push(`, ${y.lines} ${y.lines === 1 ? "line" : "lines"} said about it`);
  } else if (y.year < heardFrom) {
    bits.push(", and no recording of that year exists");
  }
  if (y.pushed) {
    bits.push(`; the board continued, denied or split over ${y.pushed} of the `
      + `${y.decided} it decided`);
  } else if (y.decided) {
    bits.push(`; all ${y.decided} it decided passed`);
  }
  return bits.join("");
}
