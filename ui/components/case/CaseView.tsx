"use client";

import Link from "next/link";
import { Fragment, useCallback, useMemo, useRef } from "react";

import { Citation } from "@/components/Citation";
import { OutcomeBadge } from "@/components/OutcomeBadge";
import { ProvenanceMark } from "@/components/ProvenanceMark";
import { Timeline, type TimelineEvent } from "@/components/Timeline";
import { voiceTags } from "@/components/SpeakerChip";
import { Turns } from "@/components/Turn";
import { usePlayer, usePlayhead } from "@/components/player/PlayerProvider";
import {
  clock,
  duration,
  elide,
  meetingDate,
  outcomeLabel,
  outcomeTone,
  phaseLabel,
  redlineTitle,
  shortBody,
} from "@/lib/format";
import type { CaseDetail, CaseHearing, CaseStep, Line, Office } from "@/lib/types";
import s from "./CaseView.module.css";

/**
 * `/case/:id` (§5.4) — the sleeper feature.
 *
 * A rezoning is heard by the Planning Commission, transmitted by the Board and
 * adopted months later. `PDE-25-7738` was taken up twelve times across ten
 * months, alternating bodies, continued five times, before it passed. On the
 * county's own portal those are twelve unrelated calendar events with no
 * connection between them, and no flat search can show the shape. Here it is
 * one object with one URL, which is the single most compelling thing this
 * archive can do that the incumbent cannot.
 *
 * The design problem is boilerplate. The official title is 62 words of legal
 * prose and it is *nearly* identical at every appearance, so printing it twelve
 * times buries the sequence — and the small part that does change is the most
 * informative thing on the page, because it is where the application itself
 * changed. So the title is stated once and each step is redlined against it
 * (R5.4.2, `redlineTitle`).
 */
export function CaseView({ data }: { data: CaseDetail }) {
  const { steps, terminal, title } = data;
  const player = usePlayer();
  const playhead = usePlayhead();
  const stepRefs = useRef(new Map<number, HTMLLIElement>());

  /* Seek into a hearing rather than to the head of its item: the speech is
     interleaved now, so a line clicked five minutes into an appearance must
     start there. */
  const play = useCallback(
    (h: CaseHearing, seconds: number) =>
      player.play(
        {
          videoId: h.video_id,
          title: `${h.body} · ${meetingDate(h.date, "short")}`,
          href: `/meeting/${h.meeting_id}?item=${h.item_id}`,
        },
        seconds,
        true,
      ),
    [player],
  );

  const seek = useCallback(
    (step: CaseStep) => {
      if (!step.span) return;
      player.play(
        {
          videoId: step.span.video_id,
          title: `${step.body} · ${meetingDate(step.date, "short")}`,
          href: `/meeting/${step.meeting_id}?item=${step.id}`,
        },
        step.span.start,
        true,
      );
    },
    [player],
  );

  // The axis is in epoch days: a case runs for months, and rendering it on a
  // real date axis rather than as evenly-spaced rows is what shows the five
  // months of silence in the middle of this one (R7.1).
  const axis = useMemo(() => {
    const at = (d: string) => Math.round(Date.parse(`${d}T00:00:00Z`) / 86_400_000);
    const days = steps.map((x) => at(x.date));
    const lo = Math.min(...days);
    const hi = Math.max(...days);
    // Pad, or the first and last points sit half-off the ends of the axis.
    const pad = Math.max(7, (hi - lo) * 0.05);
    return { at, from: lo - pad, to: hi + pad };
  }, [steps]);

  const events: TimelineEvent[] = useMemo(
    () =>
      steps.map((step) => ({
        at: axis.at(step.date),
        label: `${meetingDate(step.date, "short")} · ${shortBody(step.body)} · ${outcomeLabel(step.outcome)}`,
        tone: outcomeTone(step.outcome),
        onSelect: () =>
          stepRefs.current
            .get(step.id)
            ?.scrollIntoView({ block: "center", behavior: "smooth" }),
      })),
    [axis, steps],
  );

  const recorded = steps.filter((x) => x.span).length;
  const anyReworded = useMemo(
    () => steps.some((x) => !redlineTitle(title, x.title).identical),
    [steps, title],
  );

  /* The speech, filed under the appearance it belongs to (R5.4.4, R5.4.7).
   * `heard` arrives as its own chronological list and used to render as one,
   * which meant the same seven meetings were printed twice — once as a step
   * with its date, body, code and disposition, and again as a transcript
   * header carrying the same four things. Keyed on item id; a step can hold
   * more than one hearing because a board does take an item up twice in a day
   * (R5.2.7), and `nth/of` is what says so. */
  const spokenAt = useMemo(() => {
    const by = new Map<number, CaseHearing[]>();
    for (const h of data.heard) {
      const at = by.get(h.item_id);
      if (at) at.push(h);
      else by.set(h.item_id, [h]);
    }
    return by;
  }, [data.heard]);

  /* Computed over ALL the speech on the page at once, never per hearing: the
   * same voice in November and in March must get the same tag or the page
   * invents two people out of one. */
  const tags = useMemo(() => voiceTags(data.heard.flatMap((h) => h.lines)), [data.heard]);

  const namedLines = useMemo(
    () => data.heard.reduce((n, h) => n + h.lines.filter((l) => l.name).length, 0),
    [data.heard],
  );

  return (
    <article className={s.page}>
      <header className={s.masthead}>
        <div className={s.inner}>
          <nav className={s.crumbs} aria-label="Breadcrumb">
            <Link href="/">Archive</Link>
            <span aria-hidden>/</span>
            <span>Case</span>
          </nav>

          <div className={s.idRow}>
            <h1 className={s.id}>{data.case_id}</h1>
            <ProvenanceMark kind="agenda" />
          </div>

          <p className={s.span}>
            {steps.length === 1 ? (
              <>
                Heard once, at the {data.bodies[0]} on {meetingDate(data.first, "short")}.
              </>
            ) : (
              <>
                <strong>{steps.length} appearances</strong> between{" "}
                {meetingDate(data.first, "short")} and {meetingDate(data.last, "short")}
                {data.bodies.length > 1 ? `, at ${data.bodies.length} boards` : ""}
                {data.continuances
                  ? ` — continued ${data.continuances} ${data.continuances === 1 ? "time" : "times"}`
                  : ""}
                .
              </>
            )}
          </p>

          {/* R5.4.3: the terminal outcome, findable at a glance among the
              procedural steps that precede it. A continuance is never one —
              it is the board saying "not today". */}
          <div className={`${s.verdict} ${terminal ? s[outcomeTone(terminal.outcome)] : s.openCase}`}>
            {terminal ? (
              <>
                <span className={s.verdictLabel}>Outcome</span>
                <span className={s.verdictWhat}>
                  <strong>{outcomeLabel(terminal.outcome)}</strong> by the {terminal.body} on{" "}
                  {meetingDate(terminal.date, "short")}
                </span>
                {terminal.disposition ? (
                  <blockquote className={s.verdictQuote}>{terminal.disposition}</blockquote>
                ) : null}
              </>
            ) : (
              <>
                <span className={s.verdictLabel}>No final outcome</span>
                <span className={s.verdictWhat}>
                  {data.continuances
                    ? `Every appearance was continued or left undisposed. Nothing in this
                       archive shows it decided.`
                    : `The minutes show no disposition for any appearance of this case. That is a
                       gap in the record, not a decision.`}
                </span>
              </>
            )}
          </div>

          {/* R5.4.2: the full official title, once. */}
          {title ? (
            <div className={s.official}>
              <h2 className={s.officialLabel}>Official title</h2>
              <p className={s.officialText}>{title}</p>
            </div>
          ) : null}
        </div>
      </header>

      <div className={s.body}>
        <div className={s.inner}>
          {/* The thread, and the calendar pinned over it. The wrapper is what
              BOUNDS that pin: a sticky element sticks for as long as its parent
              is on screen, so an axis parented to the whole column stayed
              locked over 81,000px of speech it says nothing about. Inside the
              transcript the useful marker is which hearing you are in, and that
              is sticky instead. */}
          <div className={s.thread}>
            {steps.length > 1 ? (
              <section className={s.axisBlock} aria-labelledby="axis-head">
                <h2 id="axis-head" className={s.blockHead}>
                  On a calendar
                </h2>
                <Timeline
                  from={axis.from}
                  to={axis.to}
                  events={events}
                  label={`${steps.length} appearances of ${data.case_id} between ${meetingDate(
                    data.first,
                    "short",
                  )} and ${meetingDate(data.last, "short")}`}
                  format={(v) =>
                    meetingDate(new Date(v * 86_400_000).toISOString().slice(0, 10), "short")
                  }
                />
                <p className={s.axisNote}>
                  {meetingDate(data.first, "short")} → {meetingDate(data.last, "short")} · click a
                  mark to jump to that appearance
                </p>
              </section>
            ) : null}

            <section aria-labelledby="steps-head">
              <h2 id="steps-head" className={s.blockHead}>
                Every appearance
                <span className={s.headNote}>
                  {recorded
                    ? `${recorded} of ${steps.length} are in a recording`
                    : "none of these are in a recording"}
                </span>
              </h2>

              {/* Said once, so it does not have to be said per step (R5.4.2). */}
              {anyReworded ? (
                <p className={s.legend}>
                  Each appearance carries the official title above. Where the county&rsquo;s
                  wording changed, the change is marked:{" "}
                  <ins className={s.ins}>added</ins> and <del className={s.del}>removed</del>.
                  Steps with no marks were worded identically.
                </p>
              ) : null}

              {/* Said once for the same reason the redline legend is: the
                  transcript now runs THROUGH the list rather than sitting in a
                  section of its own, so its limits belong here, before the
                  first word of it. */}
              {data.heard.length ? (
                <p className={s.heardCaveat}>
                  <ProvenanceMark kind="transcript" />
                  What was said at each appearance is below it. Machine transcription, with
                  speaker names inferred from voice matching.{" "}
                  {data.heard_lines
                    ? `${Math.round((namedLines / data.heard_lines) * 100)}% of ${data.heard_lines.toLocaleString()} lines carry a name. `
                    : ""}
                  It shows what was said, not what was decided, and both the words and the names
                  can be wrong.
                  {/* An appearance the county held with no recording is not a
                      silence in the argument — it is a hole in our evidence,
                      and the difference matters on a page claiming to hold
                      everything said (R3.2). */}
                  {recorded < steps.length ? (
                    <>
                      {" "}
                      {steps.length - recorded} of the {steps.length} appearances are not in any
                      recording, so what was said at those is not here.
                    </>
                  ) : null}
                </p>
              ) : null}

              <ol className={s.steps}>
                {steps.map((step, n) => (
                  <Step
                    key={step.id}
                    step={step}
                    n={n + 1}
                    canonical={title}
                    terminal={terminal?.id === step.id}
                    onSeek={() => seek(step)}
                    hearings={spokenAt.get(step.id) ?? []}
                    tags={tags}
                    offices={data.offices[String(step.meeting_id)] ?? {}}
                    playhead={playhead}
                    onPlay={play}
                    ref={(el) => {
                      if (el) stepRefs.current.set(step.id, el);
                      else stepRefs.current.delete(step.id);
                    }}
                  />
                ))}
              </ol>

              {data.heard_truncated ? (
                <p className={s.heardTruncated}>
                  This case has more speech than the page shows. Each appearance is complete on
                  its own item page.
                </p>
              ) : null}
            </section>
          </div>

          <footer className={s.foot}>
            <Citation
              spec={{
                body: data.bodies.join(" and "),
                date: data.last,
                caseId: data.case_id,
                portalUrl: null,
              }}
              label="Cite this case"
            />
          </footer>
        </div>
      </div>
    </article>
  );
}

function Step({
  step,
  n,
  canonical,
  terminal,
  onSeek,
  hearings,
  tags,
  offices,
  playhead,
  onPlay,
  ref,
}: {
  step: CaseStep;
  n: number;
  canonical: string | null;
  terminal: boolean;
  onSeek: () => void;
  /** What was said at THIS appearance. Usually one; two when the board took
   *  the item up twice in a day (R5.2.7). Empty when it was not recorded. */
  hearings: CaseHearing[];
  tags: Map<number, string>;
  offices: Record<string, Office>;
  playhead: { videoId: string | null; position: number };
  onPlay: (h: CaseHearing, seconds: number) => void;
  ref: React.Ref<HTMLLIElement>;
}) {
  const { runs, identical } = redlineTitle(canonical, step.title);
  const procedural = step.outcome === "continued";

  return (
    <li
      ref={ref}
      className={`${s.step} ${terminal ? s.isTerminal : ""} ${procedural ? s.isProcedural : ""}`}
    >
      <div className={s.when}>
        <span className={s.n}>{n}</span>
        <time className={s.date} dateTime={step.date}>
          {meetingDate(step.date, "short")}
        </time>
      </div>

      <div className={s.what}>
        <div className={s.stepHead}>
          <Link href={`/meeting/${step.meeting_id}`} className={s.stepBody}>
            {step.body}
          </Link>
          {step.code ? <span className={s.code}>{step.code}</span> : null}
          <span className={s.phase}>{phaseLabel(step.phase)}</span>
          <span className={s.spacer} />
          <OutcomeBadge outcome={step.outcome} size="sm" />
        </div>

        {/* R5.4.2. Redlined against the title stated once above: additions in
            the step's own wording, deletions struck. Unchanged stretches
            collapse to an ellipsis — a second copy of the title is exactly
            what this view exists to avoid. */}
        {/* Nothing at all when the wording is unchanged. Seven rows each
            saying "same as above" is the boilerplate this view exists to
            remove, in a smaller font; the legend above the list carries the
            explanation once. */}
        {identical ? null : (
          <p className={s.redline} title={step.title ?? undefined}>
            {runs.map((r, i) => (
              <Fragment key={i}>
                {/* The separator is a real space in the text, not a CSS
                    ::before — generated content does not join inline text and
                    the result was "Multi-FamilyUnits,Units (Platted". */}
                {i > 0 ? " " : null}
                {r.op === "same" ? (
                  <span className={s.ctx}>{elide(r.text)}</span>
                ) : r.op === "add" ? (
                  <ins className={s.ins}>{r.text}</ins>
                ) : (
                  <del className={s.del}>{r.text}</del>
                )}
              </Fragment>
            ))}
          </p>
        )}

        {step.disposition ? (
          <blockquote className={s.disposition}>
            <ProvenanceMark kind="minutes" />
            <p>{step.disposition}</p>
          </blockquote>
        ) : null}

        <div className={s.actions}>
          <Link href={`/item/${step.id}`} className={s.open}>
            Open this item →
          </Link>
          {step.span ? (
            <button type="button" className={s.play} onClick={onSeek}>
              <span aria-hidden>▶</span> Play from {clock(step.span.start)}
            </button>
          ) : (
            <span className={s.noRec} title="This appearance is not located in any recording">
              No recording
            </span>
          )}
        </div>

      </div>

      {/* What was said here, under the record of what was decided here — and
          spanning BOTH columns of the step rather than sitting inside the
          record's column. Nested in the narrower column it read at 602px
          against the 756px it had as its own section, which wrapped every line
          harder and made the page 16% TALLER than the layout it replaced. The
          speech is the longest thing here; it gets the full measure.

          The head is deliberately thin — how long it ran and where it starts —
          because the date, body, item code and outcome are stated directly
          above. Repeating them is what the separate transcript section did for
          seven of the twelve appearances. */}
      {hearings.map((h) => (
        <div key={`${h.item_id}-${h.nth}`} className={s.spoken}>
          <div className={s.spokenHead}>
            <button type="button" className={s.hearingPlay} onClick={() => onPlay(h, h.start)}>
              <span aria-hidden>▶</span> {clock(h.start)}
            </button>
            <span className={s.hearingLen}>{duration(h.end - h.start) || "under a minute"}</span>
            {h.of > 1 ? (
              <span className={s.hearingNth} title={`Taken up ${h.of} times at this meeting`}>
                {h.nth}/{h.of}
              </span>
            ) : null}
          </div>
          <Turns
            lines={h.lines}
            tags={tags}
            offices={offices}
            activeIdx={
              playhead.videoId === h.video_id ? activeIdx(h.lines, playhead.position) : -1
            }
            onSeek={(l) => onPlay(h, l.start)}
          />
        </div>
      ))}
    </li>
  );
}

/* The `Heard` section that used to live here is gone. It rendered every
 * hearing end to end below the thread, which printed the same seven
 * meetings twice — once as a step carrying date, body, code and
 * disposition, and again as a transcript header carrying the same four.
 * The speech is now filed under the appearance it belongs to, in `Step`,
 * which keeps one continuous read down the page (R5.4.4) and drops the
 * duplicate headers. */

/** `idx` of the last line at or before `seconds`, or -1. */
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
