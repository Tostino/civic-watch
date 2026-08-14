import Link from "next/link";

import { Collection } from "@/components/browse/Collection";
import { Entryways } from "@/components/browse/Entryways";
import { Issues } from "@/components/browse/Issues";
import { TimeAxis } from "@/components/browse/TimeAxis";
import { ApiError, getBodies, getHighlights, getIssues, getMeetings, getOverview } from "@/lib/api";
import { duration, isoWeekday, meetingDate } from "@/lib/format";
import s from "./browse.module.css";

/* `/` - the archive as an object (§5.1), not a search box on a photo.
 *
 * Four things in order, and the order is the argument: what is here (R5.1.1),
 * its shape over twelve years (R5.1.2), what those twelve years were ABOUT,
 * and somewhere to go if you arrived without a question (R5.1.4). The third
 * was missing until it was not: every other panel here counts meetings,
 * coverage or the last six of something, so the page could describe the
 * collection completely and name nothing the county ever argued about.
 *
 * The meeting list is last, because a list of 1,214 rows is the least
 * informative view of a collection this size and was the whole page in
 * slice 1.
 *
 * Every filter is in the URL (R4.2), so a month of the archive is a link
 * somebody can send. */

type Props = {
  searchParams: Promise<{ body?: string; year?: string; month?: string }>;
};

const YEAR = /^\d{4}$/;
const MONTH = /^\d{4}-\d{2}$/;

export default async function BrowsePage({ searchParams }: Props) {
  const q = await searchParams;
  const body = q.body || undefined;
  // Validated rather than trusted: these reach a LIKE pattern, and a junk
  // value should narrow to nothing visibly rather than widen silently.
  const year = q.year && YEAR.test(q.year) ? q.year : undefined;
  const month = q.month && MONTH.test(q.month) ? q.month : undefined;
  const filtered = Boolean(year || month);

  const [bodies, overview, issues, highlights, page, soon] = await Promise.all([
    getBodies(),
    getOverview(body),
    // Archive-wide, and so is the heading over it: these are the county's
    // subjects, not this month's. Dropped under a date filter for the same
    // reason the entryways are.
    //
    // A 404 here means the API is older than this page and has no /api/issues
    // - the UI and the Python server are deployed separately and restart
    // separately. That is a missing section, not a broken archive, and the
    // other five panels are unaffected, so it degrades rather than taking the
    // route down. Anything else is a real fault and still throws.
    filtered
      ? null
      : getIssues().catch((e: unknown) => {
          if (e instanceof ApiError && e.status === 404) return null;
          throw e;
        }),
    // Only when there is nothing narrower on screen - once a reader has picked
    // a month, three archive-wide lists are noise between them and the answer.
    filtered ? null : getHighlights(6),
    // Past only. The county announces meetings months ahead, so the newest
    // rows in the raw table are events that have not happened - no agenda, no
    // minutes, no recording - and sorting by date puts every one of them above
    // the actual record. CivicClerk splits Past / Coming Up and is right to.
    getMeetings({ body, year, month, when: "past", limit: filtered ? 400 : 60 }),
    filtered ? null : getMeetings({ body, when: "upcoming", limit: 4 }),
  ]);

  // Bodies this archive actually holds something for. The county's portal
  // lists sixteen, most of them advisory committees with nothing behind them.
  const listed = bodies.filter((b) => b.recorded > 0 || b.with_agenda > 0);

  /** Builds a URL that changes one facet and keeps the rest (R4.2). */
  const href = (next: { body?: string; year?: string; month?: string }) => {
    const p = new URLSearchParams();
    const merged = { body, year, month, ...next };
    if (merged.body) p.set("body", merged.body);
    if (merged.year) p.set("year", merged.year);
    if (merged.month) p.set("month", merged.month);
    const qs = p.toString();
    return qs ? `/?${qs}` : "/";
  };

  const years = new Map<string, typeof page.meetings>();
  for (const m of page.meetings) {
    const y = m.date.slice(0, 4);
    const bucket = years.get(y);
    if (bucket) bucket.push(m);
    else years.set(y, [m]);
  }

  return (
    <div className={s.page}>
      <header className={s.hero}>
        <h1 className={s.title}>The Pasco County meeting record</h1>
        <p className={s.lede}>
          What the county published &mdash; agendas and approved minutes &mdash; joined to the
          recordings, so a decision can be read, heard, and cited. The county&rsquo;s portal is a
          filing cabinet of PDFs. This is the record.
        </p>
      </header>

      <nav className={s.bodies} aria-label="Filter by board or commission">
        <Link href={href({ body: undefined })} className={`${s.chip} ${!body ? s.chipOn : ""}`}>
          All meetings
        </Link>
        {listed.map((b) => (
          <Link
            key={b.body}
            href={href({ body: body === b.body ? undefined : b.body })}
            className={`${s.chip} ${body === b.body ? s.chipOn : ""}`}
            title={`${b.meetings} meetings · ${b.recorded} on video · ${b.with_agenda} with a published agenda`}
          >
            {b.body}
            <span className={s.chipN}>{b.meetings}</span>
          </Link>
        ))}
      </nav>

      <Collection o={overview} body={body} />

      <TimeAxis months={overview.months} year={year} month={month} href={href} />

      {/* After the axis, because it is read against it: the axis says how much
          the county met, this says what about. Before the entryways, because
          those are the exceptions and this is the standing business. */}
      {issues ? <Issues d={issues} /> : null}

      {highlights ? <Entryways h={highlights} /> : null}

      {soon?.meetings.length ? (
        <aside className={s.soon}>
          <h2 className={s.soonHead}>Not yet held</h2>
          <ul className={s.soonList}>
            {soon.meetings
              .slice()
              .reverse()
              .map((m) => (
                <li key={m.id} className={s.soonRow}>
                  <span className={s.soonDate}>{meetingDate(m.date, "short")}</span>
                  <span className={s.soonBody}>{m.body}</span>
                  {m.items ? <span className={s.soonAgenda}>agenda published</span> : null}
                </li>
              ))}
          </ul>
          <p className={s.soonNote}>
            Scheduled but not yet held. The board decided nothing, and there is no recording.
          </p>
        </aside>
      ) : null}

      <div className={s.listHead}>
        <h2 className={s.listTitle}>
          {month
            ? meetingDate(`${month}-01`, "long").replace(/^\w+, /, "").replace(/ \d+,/, "")
            : year
              ? year
              : "Most recent meetings"}
        </h2>
        <p className={s.count}>
          {page.total.toLocaleString()} {page.total === 1 ? "meeting" : "meetings"}
          {body ? ` · ${body}` : ""}
          {page.total > page.meetings.length
            ? ` · showing the most recent ${page.meetings.length}`
            : ""}
        </p>
        {filtered ? (
          <Link href={href({ year: undefined, month: undefined })} className={s.clear}>
            Clear the date filter
          </Link>
        ) : null}
      </div>

      {[...years.entries()].map(([y, rows]) => (
        <section key={y} className={s.year}>
          {/* Redundant when the filter already names the year. */}
          {month ? null : <h3 className={s.yearHead}>{y}</h3>}
          <ul className={s.list}>
            {rows.map((m) => (
              <li key={m.id}>
                <Link href={`/meeting/${m.id}`} className={s.row}>
                  <span className={s.day}>
                    <span className={s.dow}>{isoWeekday(m.date)}</span>
                    <span className={s.dnum}>{Number(m.date.slice(8, 10))}</span>
                    <span className={s.mon}>{meetingDate(m.date, "short").split(" ")[0]}</span>
                  </span>

                  <span className={s.main}>
                    <span className={s.bodyName}>{m.body}</span>
                    {/* R5.1.3: what you will get, before you click. */}
                    <span className={s.cover}>
                      {m.items ? (
                        <span>{m.items} items</span>
                      ) : (
                        <span className={s.missing}>no agenda</span>
                      )}
                      {m.decided ? (
                        <span>{m.decided} decided</span>
                      ) : (
                        <span className={s.missing}>no minutes</span>
                      )}
                      {m.videos ? (
                        <span className={s.has}>{duration(m.seconds)} on video</span>
                      ) : (
                        <span className={s.missing}>no recording</span>
                      )}
                    </span>
                  </span>

                  <span aria-hidden className={s.go}>
                    →
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}

      {!page.meetings.length ? (
        <p className={s.none}>
          No meetings match that filter.{" "}
          <Link href={href({ year: undefined, month: undefined })}>Show all dates</Link>.
        </p>
      ) : null}
    </div>
  );
}
