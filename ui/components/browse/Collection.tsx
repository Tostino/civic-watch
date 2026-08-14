import { duration, meetingDate } from "@/lib/format";
import type { Overview } from "@/lib/types";
import s from "./Collection.module.css";

/**
 * R5.1.1 - the shape of the collection, on arrival.
 *
 * Browse used to open on a search box, which answers nothing about what is
 * here and asks the reader to already know what to ask. This says how far back
 * the record goes, how much of it there is, and how much of it is covered.
 *
 * The three coverage bars are the load-bearing part and they are deliberately
 * unflattering: 456 of 1,214 meetings have a published agenda we can read, 758
 * have minutes, and 283 have a recording. A reader who assumes otherwise will
 * read every gap as something we lost rather than something that was never
 * published, and R3.2 says each object carries its own coverage state
 * precisely so nobody has to guess.
 */
export function Collection({ o, body }: { o: Overview; body?: string }) {
  const pct = (n: number) => (o.meetings ? Math.round((n / o.meetings) * 100) : 0);
  const bars = [
    { label: "a published agenda", n: o.with_agenda, tone: s.agenda },
    { label: "approved minutes", n: o.with_minutes, tone: s.minutes },
    { label: "a recording", n: o.recorded, tone: s.recording },
  ];

  return (
    <section className={s.wrap} aria-label="What this archive holds">
      <dl className={s.facts}>
        <div className={s.fact}>
          <dt className={s.label}>Span</dt>
          <dd className={s.value}>
            {o.first.slice(0, 4)}&ndash;{o.last.slice(0, 4)}
            <span className={s.sub}>
              {meetingDate(o.first, "short")} to {meetingDate(o.last, "short")}
            </span>
          </dd>
        </div>
        <div className={s.fact}>
          <dt className={s.label}>Meetings held</dt>
          <dd className={s.value}>
            {o.meetings.toLocaleString()}
            {body ? <span className={s.sub}>{body}</span> : null}
          </dd>
        </div>
        <div className={s.fact}>
          <dt className={s.label}>Recorded</dt>
          <dd className={s.value}>
            {duration(o.seconds)}
            <span className={s.sub}>{o.recorded} meetings on video</span>
          </dd>
        </div>
        {o.items != null ? (
          <div className={s.fact}>
            <dt className={s.label}>Agenda items</dt>
            <dd className={s.value}>
              {o.items.toLocaleString()}
              <span className={s.sub}>
                {(o.decided ?? 0).toLocaleString()} with an outcome in the minutes
              </span>
            </dd>
          </div>
        ) : null}
        {o.cases != null ? (
          <div className={s.fact}>
            <dt className={s.label}>Cases</dt>
            <dd className={s.value}>
              {o.cases.toLocaleString()}
              {/* "followed across meetings" was said of all 20,275, and 18,898
                  of them were heard once. The claim belongs to the 1,377 that
                  recur, which is the number that makes a case a thread.
                  Absent rather than reworded when the count is: replacing a
                  number with a vaguer sentence is how the old line got there. */}
              {o.cases_recurring != null ? (
                <span className={s.sub}>
                  {o.cases_recurring.toLocaleString()} heard at more than one meeting
                </span>
              ) : null}
            </dd>
          </div>
        ) : null}
      </dl>

      <div className={s.coverage}>
        <h3 className={s.coverHead}>How much of it this archive holds</h3>
        <ul className={s.bars}>
          {bars.map((b) => (
            <li key={b.label} className={s.barRow}>
              <span className={s.barLabel}>{b.label}</span>
              <span className={s.track}>
                <span
                  className={`${s.fill} ${b.tone}`}
                  style={{ width: `${pct(b.n)}%` }}
                />
              </span>
              <span className={s.barN}>
                {b.n.toLocaleString()}
                <span className={s.barPct}>{pct(b.n)}%</span>
              </span>
            </li>
          ))}
        </ul>
        <p className={s.note}>
          Of {o.meetings.toLocaleString()} meetings held. A missing agenda usually means the
          county&rsquo;s PDF is an image-only scan and this archive cannot read its text. 404 of
          1,161 agendas are such scans. There are no recordings at all before 2018.
        </p>
      </div>
    </section>
  );
}
