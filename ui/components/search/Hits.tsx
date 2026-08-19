import { Fragment } from "react";
import type { Facts } from "@/lib/types";

import Link from "next/link";

import { ItemCard } from "@/components/ItemCard";
import { ProvenanceMark } from "@/components/ProvenanceMark";
import { SpeakerChip } from "@/components/SpeakerChip";
import { DisputePassage } from "@/components/admin/DisputePassage";
import { clock, highlight, meetingDate, phaseLabel, shortBody } from "@/lib/format";
import type { RecordHit, TranscriptHit } from "@/lib/types";
import s from "./Hits.module.css";

/**
 * The two kinds of hit, and they are drawn as two kinds.
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
          What the county recorded
        </h2>
        <ProvenanceMark kind="agenda" compact />
        <p className={s.count}>
          {total.toLocaleString()} {total === 1 ? "item" : "items"}
        </p>
      </header>
      <p className={s.about}>
        Published agendas and the outcomes the approved minutes recorded,
        whether or not a camera was running.
      </p>

      {loosened ? (
        /* a widened search must say it widened. Silently loosening is
           how a reader ends up believing an exact phrase was found. */
        <p className={s.notice}>
          No item matched every word, so this matched <b>any</b> of them, most
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
              {/* The fourth context the design notes promised for this component:
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
  facts,
}: {
  hits: TranscriptHit[];
  query: string;
  degraded: string | null;
  /** Measured, for the register note. Absent when /api/facts failed, and the
   *  note then says the shape of the gap without its size. */
  facts?: Facts | null;
}) {
  return (
    <section className={s.block} aria-labelledby="tr-head">
      <header className={s.head}>
        <h2 id="tr-head" className={s.title}>
          What was said
        </h2>
        <ProvenanceMark kind="transcript" compact />
        <p className={s.count}>
          {hits.length.toLocaleString()} {hits.length === 1 ? "moment" : "moments"}
        </p>
      </header>
      <p className={s.about}>
        {facts ? (
          <>
            {`Machine transcription of ${facts.recorded} recorded meetings. ${facts.pct_transcript}% of decided items have one.`}
          </>
        ) : (
          <>Machine transcription of the meetings that were recorded, which is a
            minority of them.</>
        )}{" "}
        Speaker names are inferred from voice and can be wrong.
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

              {/* a passage without its item is frequently unreadable —
                  "all in favor say aye" means nothing on its own. */}
              {h.agenda_item_id ? (
                <Link href={`/item/${h.agenda_item_id}`} className={s.under}>
                  {h.code ? <span className={s.code}>{h.code}</span> : null}
                  <span className={s.underTitle}>{h.item ?? "(untitled item)"}</span>
                </Link>
              ) : (
                <span className={s.underNone}>Not located in an agenda item</span>
              )}

              <p className={s.said}>
                {/* Its own line, not an inline prefix. A margin puts space on
                    screen and none in the text, so an inline name copied and
                    pasted as "MARIANOI object" — and read aloud that way. */}
                <span className={s.who}>
                  <SpeakerChip who={h.who} size="sm" />
                </span>
                {/* from the other end: an error is often noticed in a
                    LIST, not while reading one meeting. Readers see nothing. */}
                <DisputePassage hit={h} />{" "}
                {/* A passage that crosses speakers is stored as one string
                    with the names inside it, and the seams are not visible:
                    "That was a Oakley: budge" gives a reader no way to say
                    where Weightman stopped. `turns` is the same words split
                    back by the utterances they came from. Null when the
                    passage is one person, where the chip above already says
                    who. */}
                {h.turns
                  ? h.turns.map((t) => (
                      <span key={t.n} className={s.turn}>
                        <span className={s.turnWho}>
                          {`${t.speaker_display ?? t.speaker ?? "Unidentified"}: `}
                        </span>
                        {highlight(t.text, query).map((run, i) => (
                          <Fragment key={i}>
                            {run.hit ? <mark className={s.mark}>{run.s}</mark> : run.s}
                          </Fragment>
                        ))}
                      </span>
                    ))
                  : highlight(h.text, query).map((run, i) => (
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

