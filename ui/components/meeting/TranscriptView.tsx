"use client";

import { useQuery } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ProvenanceMark } from "@/components/ProvenanceMark";
import { SpeakerChip, voiceTags } from "@/components/SpeakerChip";
import { useDispute } from "@/components/admin/useDispute";
import { usePlayhead } from "@/components/player/PlayerProvider";
import { getTranscript } from "@/lib/api";
import { clock, shortTitle } from "@/lib/format";
import { sameTurn } from "@/lib/turns";
import type { Item, Line, Video } from "@/lib/types";
import type { Cue } from "./MeetingView";
import s from "./TranscriptView.module.css";

/**
 * R5.2.3: the transcript, synchronised to the player. Clicking a line seeks;
 * playing scrolls.
 *
 * Virtualised because a meeting-day runs to 2,252 utterances and a four-hour
 * afternoon session is the normal case, not the outlier (R8.1, R7.5). Lines
 * are grouped into turns - consecutive lines from one voice - so the speaker
 * is named once per turn rather than once per line, which is both how a
 * transcript is read and a large reduction in visual noise.
 *
 * Two kinds of movement, and they are NOT the same thing:
 *
 *   a cue      an explicit "take me there" - a click on the spine, a band on
 *              the chapter track, a restored link. Always obeyed.
 *   following  passive drift with the playhead. The reader turns this off by
 *              scrolling, and a cue turns it back on.
 *
 * Collapsing the two is what made clicking an agenda item move the recording
 * and leave the transcript behind.
 *
 * Both of them move to a LINE and not to a row. See `target`: a row is a turn,
 * and a turn can be a quarter of an hour long.
 *
 * Every name here is an inference and is drawn as one. See SpeakerChip.
 */

/** The share of the pane kept clear at each edge. A line resting on the
 *  boundary is in view and is not really readable. */
const EDGE = 0.12;

/** Frames to keep watching a line after aiming at it. The aim itself takes two
 *  or three; the rest is for measurement, which arrives late and moves what is
 *  already on screen - a font swapping in on a cold load re-measures every row
 *  and shifted a centred line 231px, out of the pane it had just been put in.
 *  Watching is a `querySelector` and two rects a frame, which is cheap enough
 *  to do for two thirds of a second and be sure. */
const SETTLE = 40;

export function TranscriptView({
  video,
  items,
  activeItem,
  cue,
  onSeek,
  onSelectItem,
  onReading,
}: {
  video: Video;
  items: Item[];
  activeItem: number | null;
  cue: Cue | null;
  onSeek: (seconds: number) => void;
  onSelectItem: (item: Item) => void;
  /** Reports the item the reader has scrolled to, so the spine can follow. */
  onReading: (itemId: number | null) => void;
}) {
  const playhead = usePlayhead();
  /* The console bridge (R5.8.3): the error is noticed while reading, so the
   * fix starts here. Readers never see it — the probe answers false. Shared
   * with the item and the case views, which raise the same correction from
   * their own layout: components/admin/useDispute. */
  const dispute = useDispute();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  /** Set when the reader scrolls by hand: following stops until they resume. */
  const [following, setFollowing] = useState(true);
  /** Bumped every time they do. Anything already in flight stops on the spot,
   *  rather than at the next render. */
  const handled = useRef(0);

  const { data, isPending, isError, refetch } = useQuery({
    queryKey: ["transcript", video.id],
    queryFn: () => getTranscript(video.id),
  });

  const lines = data?.lines;
  const itemsById = useMemo(() => new Map(items.map((i) => [i.id, i])), [items]);
  const tags = useMemo(() => (lines ? voiceTags(lines) : new Map<number, string>()), [lines]);
  const rows = useMemo(() => (lines ? toRows(lines, itemsById) : []), [lines, itemsById]);

  const virtual = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (i) => {
      const kind = rows[i]?.kind;
      return kind === "item" ? 44 : kind === "caveat" ? 40 : 84;
    },
    overscan: 8,
    getItemKey: (i) => rows[i].key,
    /* Both of these exist because scrolling a 2,000-line transcript otherwise
     * fills the console with "flushSync was called from inside a lifecycle
     * method". The React adapter re-renders synchronously on every scroll
     * event, which React 19 refuses to do mid-render and drops; the measured
     * effect is lost scroll updates, not just noise. Batching normally is what
     * the option is for, and at this row count there is no visible difference.
     * The rAF flag moves ResizeObserver measurement out of the commit phase
     * for the same reason. */
    useFlushSync: false,
    useAnimationFrameWithResizeObserver: true,
  });

  /* useVirtualizer returns a fresh object every render, so depending on it
   * directly would re-run effects - and re-issue a smooth scroll - on every
   * one of the four position updates a second. */
  const virtualRef = useRef(virtual);
  virtualRef.current = virtual;

  /** Array position of the line under the playhead. Lines are ordered by
   *  start (verified across all 298,737 utterances), so this is a bisect. */
  const activePos = useMemo(() => {
    if (!lines?.length || playhead.videoId !== video.id) return -1;
    return posAt(lines, playhead.position);
  }, [lines, playhead.position, playhead.videoId, video.id]);

  /* The utterance's own idx, which is what a row carries. These happen to be
   * equal today - idx is the 0-based row position for every video in the
   * archive - but nothing enforces it, and conflating an array position with a
   * database key is the kind of thing that works until a re-transcription
   * drops one utterance. */
  const activeIdx = activePos >= 0 && lines ? lines[activePos].idx : -1;

  const activeRow = useMemo(
    () => (activePos < 0 ? -1 : rows.findIndex((r) => r.kind === "turn" && r.to >= activePos)),
    [activePos, rows],
  );

  /**
   * Where the pane has to be for a line to be read, or null when it is already
   * somewhere readable.
   *
   * The unit of movement is the LINE, and that is the whole of this. A row is
   * a turn, and a turn is not a small thing: the longest in the 2026-07-14
   * morning is 35 utterances - a quarter of an hour of one speaker - and
   * measures 3,987px against a 486px pane. A virtualizer can only address
   * rows, so following centred that row and left the words being spoken
   * 2,000px below the bottom of the pane, where they stayed for the whole
   * quarter hour because the row index never changed. "Follow the recording"
   * did exactly what it said and nothing appeared to happen.
   */
  const target = useCallback((line: HTMLElement, centre: boolean): number | null => {
    const box = scrollRef.current;
    if (!box) return null;
    /* clientHeight counts the padding that the dock's height puts at the foot
     * of the scroller, and that strip is covered by the player rather than
     * seen - so centring against it centres into the player. The floor is for
     * the phone, where a sheet 248px tall sits over a 320px pane: below a
     * third of the pane there is not enough left to place anything in, and a
     * dock taller than the pane would otherwise leave a negative one. */
    const covered = parseFloat(getComputedStyle(box).paddingBottom) || 0;
    const view = Math.max(box.clientHeight - covered, box.clientHeight / 3);
    const top = line.getBoundingClientRect().top - box.getBoundingClientRect().top;
    const edge = view * EDGE;
    /* Following must not mean the words move under the reader's eye every few
     * seconds. While the line is comfortably inside the pane it stays where it
     * is, and the pane moves only once it reaches an edge. */
    if (!centre && top >= edge && top + line.offsetHeight <= view - edge) return null;
    return box.scrollTop + top - (view - line.offsetHeight) / 2;
  }, []);

  /**
   * Go to utterance `idx`, which is in row `row`. Returns a cancel.
   *
   * Two steps, because neither half knows enough alone: where an unrendered
   * row starts is the virtualizer's answer, since it holds the measurements,
   * and where a line sits inside a rendered row is the DOM's. A turn arrives
   * whole - every line of it is in the one row - so once the first step lands
   * the second always has a box to measure.
   */
  const goTo = useCallback(
    (row: number, idx: number, centre: boolean) => {
      const box = scrollRef.current;
      if (!box || row < 0) return;
      if (idx < 0) {
        virtualRef.current.scrollToIndex(row, { align: "center" });
        return;
      }
      let raf = 0;
      let frames = 0;
      let force = centre;
      let sent: number | null = null;
      /* The reader's hand wins, and wins at once. `following` says so too, but
       * only on the next render and only for the passive caller - a cue is
       * obeyed whatever `following` says, and two thirds of a second of it
       * insisting is exactly the "the wheel does nothing" the panes were fixed
       * for once already. */
      const hand = handled.current;
      const run = () => {
        if (handled.current !== hand) return;
        const line = box.querySelector<HTMLElement>(`[data-line="${idx}"]`);
        if (!line) {
          /* Not rendered, so it has no box and the virtualizer is the only one
           * who knows where its row begins. It gets us there; the frames after
           * this one place the line inside it. */
          virtualRef.current.scrollToIndex(row, { align: "center" });
          force = true;
        } else {
          const to = target(line, force);
          force = false;
          /* Being right once is not being right - hence SETTLE - but only a
           * MOVED answer is worth acting on. Re-issuing the same offset every
           * frame restarts the smooth scroll from wherever it had got to,
           * which is how a glide becomes a crawl. */
          if (to === null) sent = null;
          else if (sent === null || Math.abs(to - sent) > 1) {
            sent = to;
            virtualRef.current.scrollToOffset(to, {
              align: "start",
              /* Smooth is for the small correction that following makes as one
               * line gives way to the next. A pane's worth is not a correction,
               * and a 2,000px slide is not a courtesy. */
              behavior: Math.abs(to - box.scrollTop) > box.clientHeight ? "auto" : "smooth",
            });
          }
        }
        if (++frames < SETTLE) raf = requestAnimationFrame(run);
      };
      run();
      return () => cancelAnimationFrame(raf);
    },
    [target],
  );

  // An explicit instruction. Runs when the cue changes, and again when the
  // lines arrive if the cue beat the fetch - which it does whenever a click
  // switches to the other session.
  const servedCue = useRef(-1);
  useEffect(() => {
    if (!cue || !lines?.length || !rows.length) return;
    if (servedCue.current === cue.n) return;
    servedCue.current = cue.n;
    const pos = posAt(lines, cue.seconds);
    const row = pos < 0 ? 0 : rows.findIndex((r) => r.kind === "turn" && r.to >= pos);
    setFollowing(true);
    return goTo(row < 0 ? rows.length - 1 : row, pos < 0 ? -1 : lines[pos].idx, true);
  }, [cue, lines, rows, goTo]);

  // Passive drift with the playhead. On `activeIdx` rather than `activeRow`:
  // inside a long turn the row does not change for minutes at a time, which
  // is exactly when the reader needs it to.
  useEffect(() => {
    if (!following || activeRow < 0 || !playhead.playing) return;
    return goTo(activeRow, activeIdx, false);
  }, [activeRow, activeIdx, following, playhead.playing, goTo]);

  /* When nothing is playing, what the reader has scrolled to is the best
   * answer to "which item are we on", so the spine follows their reading
   * rather than sitting on whatever was last clicked. */
  const report = useCallback(() => {
    if (!rows.length) return;
    const top = virtualRef.current.getVirtualItems()[0];
    if (!top) return;
    for (let i = top.index; i >= 0; i--) {
      const r = rows[i];
      if (r.kind === "item") {
        onReading(r.item?.id ?? null);
        return;
      }
    }
    onReading(null);
  }, [onReading, rows]);

  const onManualScroll = useCallback(() => {
    handled.current++;
    setFollowing(false);
    report();
  }, [report]);

  if (isPending) {
    return (
      <div className={s.state} role="status">
        <span className={s.spinner} aria-hidden />
        Loading the transcript…
      </div>
    );
  }
  if (isError || !lines) {
    return (
      <div className={s.state} role="alert">
        <p>The transcript could not be loaded.</p>
        <button type="button" className={s.retry} onClick={() => refetch()}>
          Try again
        </button>
      </div>
    );
  }
  if (!lines.length) {
    return (
      <div className={s.state}>
        <p>
          This recording has no transcript. It was cataloged but never transcribed, so
          nothing said in it is searchable.
        </p>
      </div>
    );
  }

  const named = lines.filter((l) => l.name).length;

  return (
    <section className={s.wrap} aria-label="Transcript">
      {/* Floats OVER the pane rather than costing it a row. It exists only
          while the reader is somewhere else in a recording that is playing,
          and it sets nothing but the flag: the effect above is what moves the
          pane, and it is the one place that knows where the line being spoken
          actually is. */}
      {!following && playhead.playing ? (
        <button type="button" className={s.resume} onClick={() => setFollowing(true)}>
          Follow the recording
        </button>
      ) : null}

      <div
        className={s.scroll}
        ref={scrollRef}
        onWheel={onManualScroll}
        onTouchMove={onManualScroll}
        tabIndex={0}
      >
        <div className={s.inner} style={{ height: virtual.getTotalSize() }}>
          {virtual.getVirtualItems().map((v) => {
            const row = rows[v.index];
            return (
              <div
                key={v.key}
                data-index={v.index}
                ref={virtual.measureElement}
                className={s.row}
                /* `top`, and NOT the usual `transform: translateY()`. A
                 * transformed ancestor is a containing block, and a sticky
                 * descendant inside one resolves against that rather than
                 * against the scroller - so the speaker's name computed its
                 * offset against a box that moves with it, pinned itself to
                 * the bottom of its own turn, and never stuck to anything.
                 * Measured: with the transform it tracked the row 1:1 through
                 * a 2,835px turn; with `top` it holds at 8px from the top of
                 * the pane. Seventeen rendered rows is not a number where the
                 * compositor's help is worth losing the behaviour. */
                style={{ top: v.start }}
              >
                {row.kind === "caveat" ? (
                  /* R2.3, said where it belongs: at the top of the words it
                     is about, as the first thing in the transcript rather
                     than a header above it. Every claim is here — what this
                     is, that the names are inferred, how many carry one, that
                     both can be wrong, and what to check instead. */
                  <p className={s.caveat}>
                    <ProvenanceMark kind="transcript" /> Machine transcription; names
                    inferred from voice — {Math.round((named / lines.length) * 100)}% of{" "}
                    {lines.length.toLocaleString()} lines carry one. Both can be wrong; the
                    recording is the source.
                  </p>
                ) : row.kind === "item" ? (
                  <ItemBreak
                    item={row.item}
                    active={row.item?.id === activeItem}
                    onSelect={row.item ? () => onSelectItem(row.item!) : undefined}
                  />
                ) : (
                  <Turn
                    lines={row.lines}
                    tags={tags}
                    offices={data.offices}
                    activeIdx={activeIdx}
                    onSeek={onSeek}
                    onSeekStart={() => setFollowing(true)}
                    onDispute={dispute ? () => dispute(row.lines) : undefined}
                  />
                )}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

/** Where the agenda changed. The transcript's only structure comes from the
 *  published record, which is exactly the join this archive exists to make -
 *  so it is a control, not a caption: it plays the item it names. */
function ItemBreak({
  item,
  active,
  onSelect,
}: {
  item: Item | null;
  active: boolean;
  onSelect?: () => void;
}) {
  if (!item) {
    return (
      <div className={`${s.break} ${s.breakNone}`}>
        <span className={s.breakLabel}>Not bound to an agenda item</span>
      </div>
    );
  }
  const cls = `${s.break} ${active ? s.breakActive : ""} ${item.source === "transcript" ? s.breakDerived : ""}`;
  const inner = (
    <>
      {item.code ? <span className={s.breakCode}>{item.code}</span> : null}
      <span className={s.breakLabel}>{shortTitle(item.title, 88)}</span>
    </>
  );
  return onSelect ? (
    <button
      type="button"
      className={`${cls} ${s.breakButton}`}
      onClick={onSelect}
      title={item.spans.length ? "Play this item from the start" : "Show this item in the record"}
    >
      {inner}
    </button>
  ) : (
    <div className={cls}>{inner}</div>
  );
}

/**
 * NOT components/Turn.tsx, and deliberately so.
 *
 * That one is laid out in flow, with every timestamp on show and a media
 * query for its narrow case. This one is one row of a virtualiser - it cannot
 * be a list, it is measured, and its column is sized against the player's
 * lane rather than the window - and it carries a transcript of up to 2,252
 * utterances, where a visible timestamp on every line is noise. Two layouts,
 * for two jobs. Both stick the speaker's name beside a long turn; getting
 * that to work here took `top` rather than a transform, below.
 *
 * What they share is what must never differ: SpeakerChip decides how the claim
 * about who spoke is presented, and `useDispute` decides what "that is wrong"
 * does.
 */
function Turn({
  lines,
  tags,
  offices,
  activeIdx,
  onSeek,
  onSeekStart,
  onDispute,
}: {
  lines: Line[];
  tags: Map<number, string>;
  offices: Record<string, { office: "chair" | "vice_chair" | "second_vice_chair" | null; district: number | null; full_name: string | null }>;
  activeIdx: number;
  onSeek: (seconds: number) => void;
  onSeekStart: () => void;
  onDispute?: () => void;
}) {
  const first = lines[0];
  // The office held AT THIS MEETING (R5.2.5). Names resolve by surname, which
  // is what the roster keys on.
  const office = first.name ? (offices[first.name] ?? offices[first.name.split(" ").pop() ?? ""]) : null;

  return (
    <div className={s.turn}>
      <div className={s.who}>
        <SpeakerChip
          who={first.who}
          office={office ?? null}
          voiceTag={first.voice != null ? (tags.get(first.voice) ?? null) : null}
          size="sm"
          onDispute={onDispute}
        />
      </div>
      <div className={s.said}>
        {lines.map((l) => {
          const on = l.idx === activeIdx;
          return (
            /* `data-line` is how following finds this line's box. The utterance
               id, not the array position: the two agree today and one of them
               is a database key. */
            <p key={l.idx} data-line={l.idx} className={`${s.line} ${on ? s.lineActive : ""}`}>
              <button
                type="button"
                className={s.at}
                onClick={() => {
                  onSeekStart();
                  onSeek(l.start);
                }}
                title={`Play from ${clock(l.start)}`}
              >
                {clock(l.start)}
              </button>
              <span className={s.text}>{l.text}</span>
            </p>
          );
        })}
      </div>
    </div>
  );
}

type Row =
  /** The pane's own caption, as row zero so that it scrolls away with the
   *  words rather than standing over them for the rest of the session. */
  | { kind: "caveat"; key: string }
  | { kind: "item"; key: string; item: Item | null }
  /** `from`/`to` are ARRAY POSITIONS into `lines`, not utterance ids. */
  | { kind: "turn"; key: string; lines: Line[]; from: number; to: number };

/** The last line starting at or before `seconds`, as an array position. */
function posAt(lines: Line[], seconds: number): number {
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
  return best;
}

/** Flattens the transcript into virtualisable rows: an agenda break whenever
 *  the item changes, then one row per turn (a run of lines from one voice). */
function toRows(lines: Line[], items: Map<number, Item>): Row[] {
  const rows: Row[] = [{ kind: "caveat", key: "caveat" }];
  let itemId: number | null | undefined;
  let turn: Line[] = [];
  let turnFrom = 0;

  const flush = (end: number) => {
    if (!turn.length) return;
    rows.push({
      kind: "turn",
      key: `t${turn[0].idx}`,
      lines: turn,
      from: turnFrom,
      to: end,
    });
    turn = [];
  };

  for (let pos = 0; pos < lines.length; pos++) {
    const l = lines[pos];
    if (l.agenda_item_id !== itemId) {
      flush(pos - 1);
      itemId = l.agenda_item_id;
      rows.push({
        kind: "item",
        key: `i${l.idx}`,
        item: l.agenda_item_id != null ? (items.get(l.agenda_item_id) ?? null) : null,
      });
    }
    const prev = turn[turn.length - 1];
    if (prev && !sameTurn(prev, l)) flush(pos - 1);
    if (!turn.length) turnFrom = pos;
    turn.push(l);
  }
  flush(lines.length - 1);
  return rows;
}
