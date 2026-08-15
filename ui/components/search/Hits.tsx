import { Fragment } from "react";
import Link from "next/link";

import { ItemCard } from "@/components/ItemCard";
import { ProvenanceMark } from "@/components/ProvenanceMark";
import { clock, highlight, meetingDate, phaseLabel, shortBody } from "@/lib/format";
import type { RecordHit, TranscriptHit } from "@/lib/types";
import s from "./Hits.module.css";

/**
 * The two kinds of hit, and they are drawn as two kinds (R5.6.1, UI_PLAN §2).
 *
 * Merging them into one ranked list was the tempting design and it is wrong:
 * it would force a comparison between "this was approved" and "somebody said
 * this", which are not comparable and do not fail the same way. The record is
 * absent or it is authoritative; the transcript is present or it is wrong.
 */

export function RecordHits({
  hits,
  query,
  total,
  loosened,
  more,
}: {
  hits: RecordHit[];
  query: string;
  total: number;
  loosened: boolean;
  more: string | null;
}) {
  return (
    <section className={s.block} aria-labelledby="rec-head">
      <header className={s.head}>
        <h2 id="rec-head" className={s.title}>
          In the record
        </h2>
        <ProvenanceMark kind="agenda" compact />
        <p className={s.count}>
          {total.toLocaleString()} {total === 1 ? "item" : "items"}
        </p>
      </header>
      <p className={s.about}>
        Published agendas and the dispositions the approved minutes recorded.
        Twelve years, whether or not a camera was running.
      </p>

      {loosened ? (
        /* R3.2: a widened search must say it widened. Silently loosening is
           how a reader ends up believing an exact phrase was found. */
        <p className={s.notice}>
          No item matched every word, so this matched <b>any</b> of them &mdash; most
          words first.
        </p>
      ) : null}

      {hits.length ? (
        <ul className={s.list}>
          {hits.map((h) => (
            <li key={h.id} className={s.recRow}>
              <div className={s.where}>
                <Link href={`/meeting/${h.meeting_id}`} className={s.whereLink}>
                  {meetingDate(h.date, "short")}
                </Link>
                <span className={s.whereBody}>{shortBody(h.body)}</span>
                {h.has_recording ? (
                  <span className={s.heard} title="This archive located this item in a recording">
                    recorded
                  </span>
                ) : null}
              </div>
              {/* The fourth context UI_PLAN §5 promised for this component:
                  spine, case timeline, agent evidence, and here. */}
              <ItemCard
                item={h}
                href={`/item/${h.id}`}
                caseHref={h.case_id ? `/case/${encodeURIComponent(h.case_id)}` : undefined}
              />
            </li>
          ))}
        </ul>
      ) : (
        <p className={s.none}>
          Nothing in the published record matches <q>{query}</q>.
        </p>
      )}

      {more ? (
        <Link href={more} className={s.more}>
          More items &rarr;
        </Link>
      ) : null}
    </section>
  );
}

export function TranscriptHits({
  hits,
  query,
  degraded,
}: {
  hits: TranscriptHit[];
  query: string;
  degraded: string | null;
}) {
  return (
    <section className={s.block} aria-labelledby="tr-head">
      <header className={s.head}>
        <h2 id="tr-head" className={s.title}>
          In the room
        </h2>
        <ProvenanceMark kind="transcript" compact />
        <p className={s.count}>
          {hits.length.toLocaleString()} {hits.length === 1 ? "moment" : "moments"}
        </p>
      </header>
      <p className={s.about}>
        Machine transcription of 283 recorded meetings &mdash; 9% of decided items have
        one. Speaker names are inferred from voice and can be wrong.
      </p>

      {degraded ? (
        <p className={s.notice}>
          Matching on meaning is unavailable, so this searched <b>words only</b>. A
          passage that makes the point in different words will not appear.
        </p>
      ) : null}

      {hits.length ? (
        <ul className={s.list}>
          {hits.map((h) => (
            <li key={h.id} className={s.trRow}>
              <div className={s.where}>
                {h.meeting_id ? (
                  <Link href={`/meeting/${h.meeting_id}`} className={s.whereLink}>
                    {meetingDate(h.meeting_date ?? h.upload_date ?? "", "short")}
                  </Link>
                ) : (
                  <span className={s.whereLink}>
                    {meetingDate(h.upload_date ?? "", "short")}
                  </span>
                )}
                {h.body ? <span className={s.whereBody}>{shortBody(h.body)}</span> : null}
                <span className={s.at}>{clock(h.start)}</span>
                {h.phase ? <span className={s.phase}>{phaseLabel(h.phase)}</span> : null}
              </div>

              {/* R5.6.3: a passage without its item is frequently unreadable —
                  "all in favor say aye" means nothing on its own. */}
              {h.agenda_item_id ? (
                <Link href={`/item/${h.agenda_item_id}`} className={s.under}>
                  {h.code ? <span className={s.code}>{h.code}</span> : null}
                  <span className={s.underTitle}>{h.item ?? "(untitled item)"}</span>
                </Link>
              ) : (
                <span className={s.underNone}>Not matched to an agenda item</span>
              )}

              <p className={s.said}>
                {/* Its own line, not an inline prefix. A margin puts space on
                    screen and none in the text, so an inline name copied and
                    pasted as "MARIANOI object" — and read aloud that way. */}
                <span className={s.who}>{speakerOf(h)}</span>{" "}
                {highlight(h.text, query).map((run, i) => (
                  <Fragment key={i}>
                    {run.hit ? <mark className={s.mark}>{run.s}</mark> : run.s}
                  </Fragment>
                ))}
              </p>
            </li>
          ))}
        </ul>
      ) : (
        <p className={s.none}>
          Nothing in the recordings matches <q>{query}</q>. Most meetings have no
          recording, so a result of zero does not mean the county never discussed it.
        </p>
      )}
    </section>
  );
}

/**
 * Passages carry a display name, `(exchange)` for a cross-speaker stretch, or
 * nothing. A bare `(exchange)` on screen is a leaked internal token; an empty
 * name must not read as an unattributed quote (R6.2.1).
 */
function speakerOf(h: TranscriptHit): string {
  // `speaker` decides — it is the key, and `(exchange)` is a value only it
  // carries — while `speaker_display` is the only thing printed.
  if (!h.speaker || h.speaker === "(exchange)") return "Several speakers";
  return h.speaker_display ?? h.speaker;
}
