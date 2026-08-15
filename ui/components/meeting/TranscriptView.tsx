"use client";

import { useQuery } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useRouter } from "next/navigation";

import { ProvenanceMark } from "@/components/ProvenanceMark";
import { SpeakerChip, voiceTags } from "@/components/SpeakerChip";
import { useOperator } from "@/components/admin/useOperator";
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
 * Every name here is an inference and is drawn as one. See SpeakerChip.
 */
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
   * fix starts here. Readers never see it — the probe answers false. */
  const operator = useOperator();
  const router = useRouter();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  /** Set when the reader scrolls by hand: following stops until they resume. */
  const [following, setFollowing] = useState(true);

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
    estimateSize: (i) => (rows[i]?.kind === "item" ? 44 : 84),
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
    virtualRef.current.scrollToIndex(row < 0 ? rows.length - 1 : row, { align: "center" });
  }, [cue, lines, rows]);

  // Passive drift with the playhead.
  useEffect(() => {
    if (!following || activeRow < 0 || !playhead.playing) return;
    virtualRef.current.scrollToIndex(activeRow, { align: "center", behavior: "smooth" });
  }, [activeRow, following, playhead.playing]);

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
      <header className={s.head}>
        <ProvenanceMark kind="transcript" />
        <p className={s.caveat}>
          Machine transcription with speaker names inferred from voice matching.{" "}
          {Math.round((named / lines.length) * 100)}% of {lines.length.toLocaleString()} lines
          carry a name; the rest are unidentified. Both can be wrong — the recording is the
          source.
        </p>
        {!following && playhead.playing ? (
          <button
            type="button"
            className={s.resume}
            onClick={() => {
              setFollowing(true);
              if (activeRow >= 0) {
                virtualRef.current.scrollToIndex(activeRow, { align: "center" });
              }
            }}
          >
            Follow the recording
          </button>
        ) : null}
      </header>

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
                style={{ transform: `translateY(${v.start}px)` }}
              >
                {row.kind === "item" ? (
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
                    onDispute={
                      operator
                        ? () => {
                            const f = row.lines[0];
                            const last = row.lines[row.lines.length - 1];
                            const q = new URLSearchParams({ sel: `${f.idx}-${last.idx}` });
                            if (f.local_label) q.set("label", f.local_label);
                            router.push(`/admin/review/${encodeURIComponent(video.id)}?${q}`);
                          }
                        : undefined
                    }
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
          name={first.name}
          displayName={first.display_name}
          human={first.human}
          basis={first.basis}
          contested={first.contested}
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
            <p key={l.idx} className={`${s.line} ${on ? s.lineActive : ""}`}>
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
  const rows: Row[] = [];
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
