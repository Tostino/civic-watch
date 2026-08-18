import { duration, meetingDate } from "@/lib/format";
import type { Overview } from "@/lib/types";
import s from "./Collection.module.css";

/**
 * R5.1.1 - the shape of the collection, on arrival.
 *
 * ONE LINE OF FIGURES AND ONE LINE OF COVERAGE. This was a five-cell grid
 * beside three labelled bars and a paragraph, 230px of panel to say eight
 * numbers - and it spent most of that on air, because every cell carried a
 * caption that wrapped to three lines ("17,531 with an outcome in the
 * minutes") while the cell beside it carried none. Ragged captions are what
 * made it read as unconsidered rather than dense.
 *
 * The figures are the same figures. What changed is that they sit on a shared
 * baseline in one strip, each with a caption short enough not to wrap, so the
 * eye reads across a row instead of hunting a grid.
 *
 * The three coverage bars are still the load-bearing part and are deliberately
 * unflattering: of 1,214 meetings, 457 have a published agenda we can read,
 * 769 have minutes and 283 have a recording. A reader who assumes otherwise
 * reads every gap as something lost rather than something never published, and
 * R3.2 says each object carries its own coverage state precisely so nobody has
 * to guess.
 */
export function Collection({
  o,
  body,
  picker,
}: {
  o: Overview;
  body?: string;
  /**
   * The board selector, rendered into this panel's own heading row.
   *
   * It used to sit above the panel as a labelled form control of its own, a
   * row of furniture between the search box and the numbers. Inside the
   * heading it reads as what it is - the scope these figures are for - and
   * costs no vertical space at all, because the heading row existed anyway.
   */
  picker?: React.ReactNode;
}) {
  const pct = (n: number) => (o.meetings ? Math.round((n / o.meetings) * 100) : 0);
  const bars = [
    { label: "agenda", n: o.with_agenda, tone: s.agenda },
    { label: "minutes", n: o.with_minutes, tone: s.minutes },
    { label: "recording", n: o.recorded, tone: s.recording },
  ];
  const facts: { k: string; v: string; sub?: string }[] = [
    { k: "meetings", v: o.meetings.toLocaleString() },
    { k: "years", v: `${o.first.slice(0, 4)}–${o.last.slice(0, 4)}` },
    { k: "recorded", v: duration(o.seconds), sub: `${o.recorded} meetings` },
  ];
  if (o.items != null) {
    facts.push({ k: "agenda items", v: o.items.toLocaleString(),
                 sub: `${(o.decided ?? 0).toLocaleString()} decided` });
  }
  if (o.cases != null) {
    facts.push({ k: "cases", v: o.cases.toLocaleString(),
                 sub: o.cases_recurring != null
                   ? `${o.cases_recurring.toLocaleString()} recurring` : undefined });
  }

  return (
    <section className={s.wrap} aria-label="What this archive holds">
      {/* The picker IS the heading. Parked beside one it was a 29px control
          against a 21px line of type, sitting five pixels low and reading as
          a form bolted to a title - and the title said the board's name while
          the control beside it said the same thing again. One element says
          what this panel is about and changes it. */}
      <div className={s.top}>{picker}</div>

      <dl className={s.facts}>
        {facts.map((f) => (
          <div key={f.k} className={s.fact}>
            <dt className={s.label}>{f.k}</dt>
            <dd className={s.value}>
              <span className={s.num}>{f.v}</span>
              {f.sub ? <span className={s.sub}>{f.sub}</span> : null}
            </dd>
          </div>
        ))}
      </dl>

      <ul className={s.bars}>
        {bars.map((b) => (
          <li key={b.label} className={s.barRow}>
            <span className={s.barLabel}>{b.label}</span>
            <span className={s.track}>
              <span className={`${s.fill} ${b.tone}`} style={{ width: `${pct(b.n)}%` }} />
            </span>
            <span className={s.barN}>
              {b.n.toLocaleString()}
              <span className={s.barPct}>{pct(b.n)}%</span>
            </span>
          </li>
        ))}
      </ul>

      {/* Plain words. "A missing agenda is usually a scan this archive cannot
          read" asks a reader to know what an image-only scan is and why that
          stops a computer; saying the county posted a picture instead of text
          says the same thing to anybody. */}
      <p className={s.note}>
        {o.meetings.toLocaleString()} meetings, {meetingDate(o.first, "short")} to{" "}
        {meetingDate(o.last, "short")}. When an agenda is missing, it is usually because
        the county posted a picture of it instead of text we can read. Nothing was
        recorded on video before 2018.
      </p>
    </section>
  );
}
