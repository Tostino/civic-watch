import Link from "next/link";

import { outcomeLabel, phaseLabel } from "@/lib/format";
import type { Facets, Outcome } from "@/lib/types";
import s from "./FilterRail.module.css";

export type Query = {
  q: string;
  body?: string;
  outcome?: string;
  phase?: string;
  case?: string;
  speaker?: string;
  since?: string;
  until?: string;
  decided?: string;
};

/**
 * R5.6.2 — body, date range, speaker, phase, outcome and case, all in the URL.
 * PRIOR_ART §1 settles the shape: Councilmatic puts facets in a left rail
 * rather than behind a menu, and a rail is right here too because the counts
 * are part of the information — "Planning Commission 9,918" tells a reader
 * what this archive is before they narrow anything.
 *
 * Every control is a link. No client state: the rail is readable, shareable,
 * and works with script off, and back does what back should.
 *
 * The rail deliberately does NOT hide filters that only one source honours.
 * `speaker` narrows speech and leaves the record untouched, and the section
 * headings say so — pretending a filter applies to both would be worse than
 * a reader noticing that the record count did not move.
 */
export function FilterRail({
  facets,
  query,
  href,
}: {
  facets: Facets;
  query: Query;
  href: (next: Partial<Query>) => string;
}) {
  const any =
    query.body || query.outcome || query.phase || query.case ||
    query.speaker || query.since || query.until || query.decided;

  return (
    <aside className={s.rail} aria-label="Narrow these results">
      <div className={s.head}>
        <h2 className={s.title}>Narrow</h2>
        {any ? (
          <Link
            className={s.clear}
            href={href({
              body: undefined, outcome: undefined, phase: undefined,
              case: undefined, speaker: undefined, since: undefined,
              until: undefined, decided: undefined,
            })}
          >
            Clear all
          </Link>
        ) : null}
      </div>

      {query.case ? (
        <Group name="Case">
          <Row on href={href({ case: undefined })} label={query.case} />
        </Group>
      ) : null}

      <Group name="Board or commission" note="in the record only">
        {facets.bodies.slice(0, 6).map((b) => (
          <Row
            key={b.body}
            on={query.body === b.body}
            href={href({ body: query.body === b.body ? undefined : b.body })}
            label={b.body}
            n={b.items}
          />
        ))}
      </Group>

      <Group name="Outcome" note="from the approved minutes">
        <Row
          on={query.decided === "1"}
          href={href({ decided: query.decided === "1" ? undefined : "1", outcome: undefined })}
          label="Anything decided"
        />
        {facets.outcomes.map((o) => (
          <Row
            key={o.outcome}
            on={query.outcome === o.outcome}
            href={href({
              outcome: query.outcome === o.outcome ? undefined : o.outcome,
              decided: undefined,
            })}
            label={outcomeLabel(o.outcome as Outcome)}
            n={o.items}
          />
        ))}
        {/* Not an outcome — the absence of one. 8,440 items have a published
            agenda entry and no outcome, which means the minutes are
            missing or unparsed, NOT that the board did nothing (R2.4). */}
        <Row
          on={query.decided === "0"}
          href={href({ decided: query.decided === "0" ? undefined : "0", outcome: undefined })}
          label="No outcome in the minutes"
        />
      </Group>

      <Group name="Part of the meeting">
        {facets.phases.slice(0, 7).map((p) => (
          <Row
            key={p.phase}
            on={query.phase === p.phase}
            href={href({ phase: query.phase === p.phase ? undefined : p.phase })}
            label={phaseLabel(p.phase)}
            n={p.items}
          />
        ))}
      </Group>

      <Group name="Year">
        <div className={s.years}>
          {facets.years.map((y) => {
            const on = query.since === `${y.year}-01-01`;
            return (
              <Link
                key={y.year}
                className={`${s.year} ${on ? s.yearOn : ""}`}
                aria-current={on ? "true" : undefined}
                href={href(
                  on
                    ? { since: undefined, until: undefined }
                    : { since: `${y.year}-01-01`, until: `${y.year}-12-31` },
                )}
                title={`${y.meetings} meetings in ${y.year}`}
              >
                {y.year.slice(2)}
              </Link>
            );
          })}
        </div>
      </Group>

      <Group name="Speaker" note="the recordings only; names are inferred">
        {facets.speakers.slice(0, 12).map((v) => (
          <Row
            key={v.speaker}
            on={query.speaker === v.speaker}
            // The href carries the KEY and the label shows the name. A board
            // member is filtered by surname, so sending the full name here
            // would return nothing and read as "she never spoke".
            href={href({ speaker: query.speaker === v.speaker ? undefined : v.speaker })}
            label={v.speaker_display ?? v.speaker}
            n={v.lines}
          />
        ))}
      </Group>
    </aside>
  );
}

function Group({
  name,
  note,
  children,
}: {
  name: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={s.group}>
      <h3 className={s.groupName}>
        {name}
        {note ? <span className={s.note}>{note}</span> : null}
      </h3>
      {children}
    </section>
  );
}

function Row({
  on,
  href,
  label,
  n,
}: {
  on?: boolean;
  href: string;
  label: string;
  n?: number;
}) {
  return (
    <Link className={`${s.row} ${on ? s.on : ""}`} href={href} aria-current={on ? "true" : undefined}>
      <span className={s.rowLabel}>{label}</span>
      {n === undefined ? null : <span className={s.n}>{n.toLocaleString()}</span>}
    </Link>
  );
}
