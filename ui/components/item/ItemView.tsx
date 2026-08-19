"use client";

import Link from "next/link";
import { Fragment, useCallback, useMemo } from "react";

import { Citation } from "@/components/Citation";
import { OutcomeBadge } from "@/components/OutcomeBadge";
import { ProvenanceMark } from "@/components/ProvenanceMark";
import { SourceDocument } from "@/components/SourceDocument";
import { voiceTags } from "@/components/SpeakerChip";
import { Turns } from "@/components/Turn";
import { CaseThread } from "@/components/case/CaseThread";
import { usePlayer, usePlayhead } from "@/components/player/PlayerProvider";
import {
  clock,
  duration,
  meetingDate,
  outcomeSourceLabel,
  phaseLabel,
  sameThing,
  sessionLabel,
} from "@/lib/format";
import type { Facts, ItemDetail, ItemRun, Line, Video } from "@/lib/types";
import s from "./ItemView.module.css";

/**
 * `/item/:id`. The **Verify** surface: the place a claim bottoms out in
 * something the county wrote or something a microphone caught.
 *
 * The order of this page is an argument, not a layout preference. The record
 * leads — code, official title, staff recommendation, and the minutes
 * outcome verbatim — because it is authoritative and because for 91% of
 * decided items in this archive it is *all there is*. Then the county's own
 * PDF. Then the case thread, because an item is rarely the whole story. Only
 * then what was said, marked as the weaker source it is.
 *
 * Nothing here merges the two. A transcript can show a vote being taken and can
 * never show its result; the minutes record the result and never the argument.
 * A page that blended them would be more readable and would be lying.
 */
export function ItemView({
  data,
  facts,
}: {
  data: ItemDetail;
  /** Measured counts for the two gap notices. Absent when /api/facts failed,
   *  and each notice then states the gap without a share. */
  facts?: Facts | null;
}) {
  const { item, meeting, offices, prev, next } = data;
  const player = usePlayer();
  const playhead = usePlayhead();

  const published = item.source === "agenda";
  const span = item.spans[0] ?? null;
  const video = span ? (item.videos.find((v) => v.id === span.video_id) ?? null) : null;

  const source = useCallback(
    (videoId: string) => {
      const v = item.videos.find((x) => x.id === videoId);
      return {
        videoId,
        title: `${meeting.body} · ${meetingDate(meeting.date, "short")}${
          v && item.videos.length > 1 ? ` · ${sessionLabel(v.session_seq, item.videos.length)}` : ""
        }`,
        href: `/meeting/${meeting.id}`,
        duration: v?.duration,
      };
    },
    [item.videos, meeting.body, meeting.date, meeting.id],
  );

  const seek = useCallback(
    (videoId: string, seconds: number) => player.play(source(videoId), seconds, true),
    [player, source],
  );

  const tags = useMemo(() => voiceTags(item.lines), [item.lines]);
  const named = item.lines.filter((l) => l.name).length;
  const heardSeconds = item.spans.reduce((n, sp) => n + (sp.end - sp.start), 0);

  /* Hoisted out of the render loop: the playhead ticks four times a second and
   * this is a bisect over up to 1,225 lines. Inside the map it would run once
   * per turn per tick, for one answer that is the same every time. */
  const active = useMemo(
    () =>
      span && playhead.videoId === span.video_id
        ? activeIdx(item.lines, playhead.position)
        : -1,
    [item.lines, playhead.position, playhead.videoId, span],
  );

  /* The deep link back into the meeting, which is where this item is readable
   * in context — with the spine either side of it and the transcript running
   * on past its end. */
  const inMeeting = `/meeting/${meeting.id}?item=${item.id}${
    span ? `&v=${span.video_id}&t=${Math.floor(span.start)}` : ""
  }`;

  return (
    <article className={s.page}>
      <header className={s.masthead}>
        <div className={s.inner}>
          <nav className={s.crumbs} aria-label="Breadcrumb">
            <Link href="/">Archive</Link>
            <span aria-hidden>/</span>
            <Link href={`/?body=${encodeURIComponent(meeting.body)}`}>{meeting.body}</Link>
            <span aria-hidden>/</span>
            <Link href={`/meeting/${meeting.id}`}>{meetingDate(meeting.date, "short")}</Link>
          </nav>

          <div className={s.idRow}>
            {item.code ? <span className={s.code}>{item.code}</span> : null}
            {published ? (
              <OutcomeBadge outcome={item.outcome} />
            ) : (
              <span className={s.derivedTag}>Not on the published agenda</span>
            )}
            <span className={s.phase}>{phaseLabel(item.phase)}</span>
            {/* The agenda's own section heading, but only when it says
                something the phase does not: "Public hearing · PUBLIC
                HEARINGS" is one fact printed twice. */}
            {item.section && !sameThing(item.section, phaseLabel(item.phase)) ? (
              <span className={s.section}>{item.section}</span>
            ) : null}
            <span className={s.spacer} />
            <ProvenanceMark kind={published ? "agenda" : "derived"} />
          </div>

          {/* The county's own words, set as the document they are.
              Zoning titles are legal prose and run past 60 words — at the page
              title's natural size that is seven lines of 28px serif, which
              reads as shouting rather than as a heading. Long titles step down
              a size; short ones keep the full display treatment. */}
          <h1
            className={`${s.title} ${published ? "" : s.derivedTitle} ${
              (item.title?.length ?? 0) > 160 ? s.longTitle : ""
            }`}
          >
            {item.title ?? "(no title recorded)"}
          </h1>

          {published || item.case_id || item.department ? (
            <p className={s.meta}>
              {item.department ? <span>{item.department}</span> : null}
              {item.case_id ? (
                <Link className={s.caseLink} href={`/case/${encodeURIComponent(item.case_id)}`}>
                  {item.case_id}
                </Link>
              ) : null}
              {/* `file_number` is the raw string off the agenda and `case_id`
                  is that string normalised, so they are usually the same
                  identifier twice — "PDE-25-7721 · PDE25-7721". */}
              {item.file_number && !sameThing(item.file_number, item.case_id) ? (
                <span className={s.mono}>{item.file_number}</span>
              ) : null}
              {item.districts ? <span>District {item.districts}</span> : null}
            </p>
          ) : null}
        </div>
      </header>

      <div className={s.split}>
        <main className={s.main}>
          {/* ------------------------------------------- the record */}
          <section className={s.block} aria-labelledby="record-head">
            <h2 id="record-head" className={s.blockHead}>
              What the county recorded
            </h2>

            {published ? (
              <>
                {item.recommendation ? (
                  <div className={s.record}>
                    <ProvenanceMark kind="agenda" />
                    <h3 className={s.recordLabel}>Staff recommendation</h3>
                    <p className={s.recordText}>{item.recommendation}</p>
                  </div>
                ) : null}

                {item.outcome_text ? (
                  <div className={s.record}>
                    <ProvenanceMark kind="minutes" />
                    <h3 className={s.recordLabel}>
                      Outcome
                      <OutcomeBadge outcome={item.outcome} size="sm" />
                    </h3>
                    {/* Verbatim, and the authoritative answer to "what was
                        decided". The classified outcome sits beside it rather
                        than replacing it, because the classification is ours
                        and the sentence is the county's. */}
                    <blockquote className={s.recordQuote}>{item.outcome_text}</blockquote>
                  </div>
                ) : (
                  <div className={s.gap}>
                    <h3 className={s.gapTitle}>No outcome in the minutes</h3>
                    <p>
                      The approved minutes do not say what became of this item. That is a gap
                      in the record, not a decision. It is the normal
                      state{facts ? ` for ${facts.pct_no_outcome}% of items.` : "."}{" "}
                      Most of those are regular business and board reports that the minutes do
                      not record in writing. This archive never infers an outcome from the
                      fact that someone called a vote.
                    </p>
                  </div>
                )}
              </>
            ) : (
              <div className={s.gap}>
                <h3 className={s.gapTitle}>This item is not from the published agenda</h3>
                <p>
                  This archive found it in the recording: a call to order, a recess, or
                  business the board never listed. The county recorded nothing about it,
                  so everything below is our reading.
                </p>
              </div>
            )}
          </section>

          {/* ----------------------------- the county's own document */}
          {item.files.length ? (
            <section className={s.block} aria-labelledby="source-head">
              <h2 id="source-head" className={s.blockHead}>
                The source document
              </h2>
              <div className={s.docs}>
                {item.files.map((f) => (
                  <SourceDocument
                    key={f.file_id}
                    file={f}
                    meetingLabel={`${meeting.body}, ${meetingDate(meeting.date, "short")}`}
                  />
                ))}
              </div>
            </section>
          ) : null}

          {/* ------------------------------------- the case thread */}
          {item.case_id && item.thread.length ? (
            <section className={s.block}>
              <CaseThread
                caseId={item.case_id}
                steps={item.thread}
                currentId={item.id}
                facts={facts}
              />
            </section>
          ) : null}

          {/* ------------------------------------- what was said */}
          <section className={s.block} aria-labelledby="said-head">
            <h2 id="said-head" className={s.blockHead}>
              What was said
            </h2>

            {!span ? (
              <div className={s.gap}>
                <h3 className={s.gapTitle}>
                  {item.videos.length
                    ? "This item is not located in the recording"
                    : "There is no recording of this meeting"}
                </h3>
                <p>
                  {item.videos.length ? (
                    <>
                      The meeting was recorded, but this item could not be located in
                      it. Binding is complete on public hearings and resolutions, 80% on consent
                      and 58% on regular business; board reports carry no agenda code at all.
                      The{" "}
                      <Link href={`/meeting/${meeting.id}`}>full transcript of the meeting</Link>{" "}
                      is still readable.
                    </>
                  ) : (
                    <>
                      {facts
                        ? `Only ${facts.recorded} of the ${facts.meetings} meetings in this archive have a recording, and only ${facts.pct_transcript}% of decided items are located in one.`
                        : "Most meetings in this archive have no recording, and only a minority of decided items are located in one."}{" "}
                      The record above is the whole record for this item.
                    </>
                  )}
                </p>
              </div>
            ) : (
              <div className={s.transcript}>
                <div className={s.said}>
                  <ProvenanceMark kind="transcript" />
                  <p className={s.caveat}>
                    Machine transcription of {duration(heardSeconds) || "under a minute"} of
                    recording, with speaker names inferred from voice matching.{" "}
                    {item.lines.length
                      ? `${Math.round((named / item.lines.length) * 100)}% of ${item.lines.length.toLocaleString()} lines carry a name.`
                      : ""}{" "}
                    It shows what was said, not what was decided, and both the words and the
                    names can be wrong.
                  </p>
                  <div className={s.playRow}>
                    <button
                      type="button"
                      className={s.play}
                      onClick={() => seek(span.video_id, span.start)}
                    >
                      <span aria-hidden>▶</span> Play this item
                      {video && item.videos.length > 1
                        ? ` · ${sessionLabel(video.session_seq, item.videos.length)}`
                        : ""}{" "}
                      from {clock(span.start)}
                    </button>
                    <Link href={inMeeting} className={s.inContext}>
                      Read it in the meeting →
                    </Link>
                  </div>
                  {/* `runs`, not `spans`: two spans five seconds apart are the
                      binder cutting one discussion, not the board leaving and
                      returning, and this line used to claim otherwise. */}
                  {item.runs.length > 1 ? (
                    <p className={s.parts}>
                      Taken up {item.runs.length} times, not once through. It was set aside and
                      returned to. Each stretch is below, in the order it happened.
                    </p>
                  ) : null}
                </div>

                {item.lines.length ? (
                  <div className={s.lines}>
                    {item.runs.map((run) =>
                      run.lines.length ? (
                        <Fragment key={`${run.video_id}-${run.start_idx}`}>
                          {/* The break is the point. Poured into one list, an
                              item argued at 18:05 and again at 3:38:04 read as
                              continuous speech with three and a half hours
                              silently removed from the middle. */}
                          {item.runs.length > 1 ? (
                            <ResumedAt run={run} videos={item.videos} />
                          ) : null}
                          <Turns
                            lines={run.lines}
                            tags={tags}
                            offices={offices}
                            activeIdx={active}
                            onSeek={(l) => seek(run.video_id, l.start)}
                          />
                        </Fragment>
                      ) : null,
                    )}
                    {item.truncated ? (
                      <p className={s.truncated}>
                        This item runs longer than the page shows.{" "}
                        <Link href={inMeeting}>Read the rest in the meeting transcript</Link>.
                      </p>
                    ) : null}
                  </div>
                ) : (
                  <p className={s.gapTitle}>
                    Nothing was transcribed inside this item&rsquo;s span.
                  </p>
                )}
              </div>
            )}
          </section>
        </main>

        {/* ------------------------------------------------------------ rail */}
        <aside className={s.rail}>
          <div className={s.railInner}>
            <section className={s.railBlock}>
              <h2 className={s.railHead}>Where this is</h2>
              <Link href={`/meeting/${meeting.id}`} className={s.meetingLink}>
                <span className={s.meetingDate}>{meetingDate(meeting.date)}</span>
                <span className={s.meetingBody}>{meeting.body}</span>
              </Link>
              <ul className={s.facts}>
                <li>Item {item.seq + 1} on the agenda</li>
                {span ? <li>{duration(heardSeconds)} of recording</li> : <li>No recording</li>}
                {outcomeSourceLabel(item.outcome_source) ? (
                  <li>Outcome read from {outcomeSourceLabel(item.outcome_source)}</li>
                ) : null}
              </ul>
            </section>

            <section className={s.railBlock}>
              <h2 className={s.railHead}>Cite and verify</h2>
              <Citation
                spec={{
                  body: meeting.body,
                  date: meeting.date,
                  code: item.code,
                  caseId: item.case_id,
                  videoId: span?.video_id ?? null,
                  seconds: span?.start ?? null,
                  portalUrl: item.portal,
                }}
                label="Cite this item"
              />
              {item.portal ? (
                <a className={s.portal} href={item.portal} target="_blank" rel="noreferrer">
                  See this meeting on the county portal ↗
                </a>
              ) : null}
            </section>

            {/* no dead ends. Reading an agenda through should not mean
                going back to the meeting between every item. */}
            {prev || next ? (
              <nav className={s.railBlock} aria-label="Adjacent agenda items">
                <h2 className={s.railHead}>Next and previous</h2>
                {prev ? (
                  <Link href={`/item/${prev.id}`} className={s.step}>
                    <span className={s.stepDir}>← Previous</span>
                    <span className={s.stepWhat}>
                      {prev.code ? <span className={s.mono}>{prev.code}</span> : null}
                      {prev.title ?? "(untitled)"}
                    </span>
                  </Link>
                ) : null}
                {next ? (
                  <Link href={`/item/${next.id}`} className={s.step}>
                    <span className={s.stepDir}>Next →</span>
                    <span className={s.stepWhat}>
                      {next.code ? <span className={s.mono}>{next.code}</span> : null}
                      {next.title ?? "(untitled)"}
                    </span>
                  </Link>
                ) : null}
              </nav>
            ) : null}
          </div>
        </aside>
      </div>
    </article>
  );
}

/** The last line starting at or before `seconds`, as an utterance idx. */
function activeIdx(lines: Line[], seconds: number): number {
  let lo = 0;
  let hi = lines.length - 1;
  let best = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (lines[mid].start <= seconds) {
      best = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return best < 0 ? -1 : lines[best].idx;
}

/**
 * The seam between one appearance and the next.
 *
 * Drawn rather than implied. Without it, an item argued at 18:05, set down and
 * taken up again at 3:38:04 renders as one continuous exchange with three and
 * a half hours of unrelated county business deleted from the middle of it —
 * which reads as speech nobody made.
 */
function ResumedAt({ run, videos }: { run: ItemRun; videos: Video[] }) {
  const v = videos.find((x) => x.id === run.video_id) ?? null;
  const where = videos.length > 1 ? `${sessionLabel(v?.session_seq ?? null, videos.length)}, ` : "";
  return (
    <p className={s.resumed}>
      <span className={s.resumedN}>
        {run.nth}/{run.of}
      </span>
      {run.nth === 1 ? "Taken up at " : "Taken up again at "}
      {where}
      {clock(run.start)}
    </p>
  );
}
