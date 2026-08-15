import Link from "next/link";

import { OutcomeBadge } from "@/components/OutcomeBadge";
import { SpeakerChip } from "@/components/SpeakerChip";
import { clock, duration, meetingDate, shortBody, shortTitle } from "@/lib/format";
import type { DecidedDay, Divided, Highlights } from "@/lib/types";
import s from "./Entryways.module.css";

/**
 * R5.1.4 - curated entry points, so arriving does not require already having a
 * question. A search box alone assumes the reader knows what to ask, and most
 * of them do not; PRIOR_ART §1 found Councilmatic's "Divided Votes" to be the
 * strongest story surface in any of the archives reviewed.
 *
 * All of these are saved queries with names, and every row links to a real
 * object, so none of it is a dead end (R4.3). When /search lands it should
 * absorb them as named filters rather than reimplement them.
 */
export function Entryways({ h }: { h: Highlights }) {
  return (
    <>
      <DividedSection d={h.divided} />

      <section className={s.wrap} aria-label="More ways in">
        <div className={s.card}>
          <header className={s.head}>
            <h2 className={s.title}>Cases the board continued again and again</h2>
            <p className={s.why}>
              Cases continued three times or more. A rezoning heard twelve times over
              ten months is one matter. The county&rsquo;s portal shows it as twelve
              unrelated events.
            </p>
          </header>
          <ul className={s.list}>
            {h.continued.map((c) => (
              <li key={c.case_id}>
                <Link href={`/case/${encodeURIComponent(c.case_id)}`} className={s.row}>
                  <span className={s.caseId}>{c.case_id}</span>
                  <span className={s.what}>{shortTitle(c.title, 58)}</span>
                  <span className={s.tally} title={`${c.appearances} appearances in total`}>
                    {c.continuances}&times; continued
                  </span>
                </Link>
              </li>
            ))}
            {!h.continued.length ? <li className={s.none}>Nothing found.</li> : null}
          </ul>
        </div>

        <DecidedCard days={h.decided} />
      </section>
    </>
  );
}

/* ------------------------------------------------------------ disagreement */

/**
 * The two sources, side by side and never blurred (UI_PLAN §2). Each lane is
 * blind where the other sees: the minutes name dissent formally but are
 * published weeks late, and a debate that produced no motion leaves no
 * disposition to name at all. Reading only the minutes is how the August 2026
 * argument over licence-plate cameras came to be missing from this page.
 */
function DividedSection({ d }: { d: Divided }) {
  const empty = !d.record.length && !d.room.length;
  return (
    <section className={s.divided} aria-labelledby="divided-head">
      <header className={s.dividedHead}>
        <h2 id="divided-head" className={s.dividedTitle}>
          Where the board disagreed
        </h2>
        <p className={s.why}>
          Nearly all county business passes unopposed, so the exceptions are the ones worth
          reading. Both sources are searched, because each misses what the other catches.
        </p>
      </header>

      {empty ? (
        <p className={s.none}>Nothing found.</p>
      ) : (
        <div className={s.lanes}>
          <div className={s.lane}>
            <h3 className={s.laneHead}>
              <span className={s.laneWhat}>In the minutes</span>
              <span className={s.laneWhy}>the county&rsquo;s own words, weeks later</span>
            </h3>
            <ul className={s.list}>
              {d.record.map((r) => (
                <li key={r.id}>
                  <Link href={`/item/${r.id}`} className={s.dRow}>
                    <span className={s.dTop}>
                      <span className={s.when}>{meetingDate(r.date, "short")}</span>
                      {r.code ? <span className={s.code}>{r.code}</span> : null}
                      <OutcomeBadge outcome={r.outcome} size="sm" />
                    </span>
                    <span className={s.dTitle}>
                      {shortTitle(r.title, 96) || "(no title published)"}
                    </span>
                    <span className={s.dWhy}>
                      {r.dissent.length ? (
                        <>
                          <b className={s.nay}>{list(r.dissent)}</b> voted nay
                        </>
                      ) : (
                        "the minutes call this a divided vote"
                      )}
                      {r.items > 1 ? ` · one motion carrying ${r.items} items` : ""}
                    </span>
                  </Link>
                </li>
              ))}
              {!d.record.length ? <li className={s.none}>Nothing found.</li> : null}
            </ul>
          </div>

          <div className={s.lane}>
            <h3 className={s.laneHead}>
              <span className={s.laneWhat}>In the room</span>
              {/* R3.1: the transcript is machine-made and this lane is a
                  reading of it, so it says so where the claim is made rather
                  than in a footnote nobody reaches. */}
              <span className={`${s.laneWhy} ${s.inferred}`}>
                from the recording, transcribed automatically
              </span>
            </h3>
            <ul className={s.list}>
              {d.room.map((r) => (
                <li key={r.id}>
                  <Link href={`/item/${r.id}`} className={s.dRow}>
                    <span className={s.dTop}>
                      <span className={s.when}>{meetingDate(r.date, "short")}</span>
                      {r.code ? <span className={s.code}>{r.code}</span> : null}
                      <span className={r.kind === "vote" ? s.kindVote : s.kindObj}>
                        {r.kind === "vote" ? "split vote" : "objected"}
                      </span>
                    </span>
                    <span className={s.dTitle}>
                      {shortTitle(r.title, 96) || "(no title published)"}
                    </span>
                    <span className={s.quote}>
                      {/* A named member, against a split vote or an objection.
                          Drawn with the certainty behind the name, because
                          this is the claim a person is least willing to have
                          wrong about them (R2.3, R6.2). */}
                      <span className={s.who}>
                        <SpeakerChip
                          name={r.speaker}
                          displayName={r.speaker_display}
                          human={r.human}
                          basis={r.basis}
                          size="sm"
                        />
                      </span>
                      <q>{r.quote}</q>
                    </span>
                    <span className={s.dWhy}>{clock(r.seconds)} into the recording</span>
                  </Link>
                </li>
              ))}
              {!d.room.length ? <li className={s.none}>Nothing found.</li> : null}
            </ul>
          </div>
        </div>
      )}
    </section>
  );
}

/* --------------------------------------------------------- what was decided */

/**
 * The meeting-day is the unit, not the item. 113 things were decided on 14
 * July 2026; listing eight of them showed an arbitrary sample, chosen by
 * sequence number, under a heading that implied a summary - and eight rows
 * repeating one date and one body said nothing eight times.
 *
 * A day says how much business was done, what shape it had, and names the
 * part that was not routine. The routine remainder is one click away on the
 * meeting's own spine, which is the view built for reading 113 items.
 */
function DecidedCard({ days }: { days: DecidedDay[] }) {
  const peak = Math.max(1, ...days.map((d) => d.decided));
  return (
    <div className={s.card}>
      <header className={s.head}>
        <h2 className={s.title}>What the board has been deciding</h2>
        <p className={s.why}>
          Each meeting&rsquo;s business, most recent first. The bar is how much was disposed
          of, to the same scale across days &mdash; most of it consent, passed in one motion.
        </p>
      </header>
      <ul className={s.list}>
        {days.map((d) => (
          <li key={d.meeting_id}>
            <Link href={`/meeting/${d.meeting_id}`} className={s.dayRow}>
              <span className={s.dTop}>
                <span className={s.when}>{meetingDate(d.date, "short")}</span>
                <span className={s.bodyTag}>{shortBody(d.body)}</span>
                <span className={s.dayN}>
                  {d.decided.toLocaleString()} decided
                  {d.heard ? <span className={s.dayHeard}>{d.heard} heard</span> : null}
                </span>
              </span>

              <span
                aria-hidden
                className={s.bar}
                style={{ "--of-peak": (d.decided / peak).toFixed(3) } as React.CSSProperties}
              >
                {seg(s.segOk, d.passed, d.decided)}
                {seg(s.segWait, d.continued, d.decided)}
                {seg(s.segNo, d.refused + d.withdrawn, d.decided)}
              </span>

              <span className={s.dWhy}>
                {parts(d).join(" · ")}
                {d.seconds ? ` · ${duration(d.seconds)} recorded` : ""}
              </span>

              {d.notable.map((n) => (
                <span key={n.id} className={s.notable}>
                  <OutcomeBadge outcome={n.outcome} size="sm" />
                  {n.code ? <span className={s.code}>{n.code}</span> : null}
                  {shortTitle(n.title, 54) || "(no title published)"}
                </span>
              ))}
            </Link>
          </li>
        ))}
        {!days.length ? <li className={s.none}>Nothing found.</li> : null}
      </ul>
    </div>
  );
}

const seg = (cls: string, n: number, total: number) =>
  n > 0 ? (
    <span className={cls} style={{ "--share": (n / total).toFixed(4) } as React.CSSProperties} />
  ) : null;

/** Only the counts that are not zero — a row of zeroes is noise, not honesty. */
function parts(d: DecidedDay): string[] {
  const out: string[] = [];
  if (d.passed) out.push(`${d.passed} passed`);
  if (d.continued) out.push(`${d.continued} continued`);
  if (d.refused) out.push(`${d.refused} denied`);
  if (d.withdrawn) out.push(`${d.withdrawn} withdrawn`);
  if (d.divided) out.push(`${d.divided} on a divided vote`);
  return out;
}

/** "Oakley and Starkey", "Weightman, Yeager and Mariano". */
function list(names: string[]): string {
  if (names.length < 2) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")}, and ${names[names.length - 1]}`;
}
