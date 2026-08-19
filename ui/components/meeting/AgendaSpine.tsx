"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { ItemCard } from "@/components/ItemCard";
import { OutcomeBadge } from "@/components/OutcomeBadge";
import { Timeline, type TimelineEvent } from "@/components/Timeline";
import { clock, outcomeTone, phaseLabel, sessionLabel } from "@/lib/format";
import type { Item, Span, Video } from "@/lib/types";
import s from "./AgendaSpine.module.css";

/**
 * The agenda spine - a table of contents, a chapter track and a seek
 * control, which on this material are one job. Playing moves it;
 * clicking it moves the recording.
 *
 * Three things the data forces:
 *
 * **Consent blocks collapse.** A BCC agenda is routinely 200 items of which
 * 150 are consent, approved en bloc in a single motion with no discussion.
 * Listing them flat buries the four items anyone came to read. Councilmatic
 * has this problem in milder form - it tags routine legislation and then does
 * not let the tag shape the page - and the design notes flags it as the thing to do
 * better. So a consent run collapses to what it actually was: one motion,
 * N items, one outcome.
 *
 * **Most items are not in the recording.** Only 9% of decided items anywhere
 * in the archive are bound to a span. An unbound item still belongs on the
 * spine, drawn as present-but-not-playable rather than hidden.
 *
 * **Published order is not the order things happened**. This spine
 * sorted by `seq` and it was wrong three ways at once, all of them visible on
 * one screen of 2026-07-14:
 *
 *   - Transcript-derived stretches are appended after the published items, so
 *     "Call to order, 0:01" sat below 191 rows. That is not a quirk of one
 *     meeting: in ALL 234 meetings holding both kinds, every derived item
 *     sorts after every published one.
 *   - 3,798 of the 5,500 located items in the archive - 69%, across 224 of
 *     283 recorded meetings - sit at a rail position that disagrees with when
 *     they were actually heard. On this day the board took the millage
 *     resolution, published 77th, at 3:07 in the afternoon after every
 *     rezoning. Published order hides that; it is also the interesting part.
 *   - Two sessions means offsets restart, so a rail sorted by anything but
 *     session-then-offset reads as scrambled: 2:09:53 followed by 1:01:10.
 *
 * And the rail follows the playhead, which cannot mean anything if the rail is
 * not in time order.
 *
 * So: TWO LANES. What we can place in a recording, in the order it happened;
 * then the rest of the agenda in published order. Not one list sorted by time
 * with the unlocated items slotted in among them - we do not know when those
 * happened, and putting them between two timestamps would say that we do.
 */
export function AgendaSpine({
  items,
  videos,
  activeVideo,
  activeItem,
  playhead,
  onSelect,
  onSeek,
  onSelectVideo,
}: {
  items: Item[];
  videos: Video[];
  activeVideo: string | null;
  activeItem: number | null;
  playhead: number | null;
  /** `span` says WHICH appearance was clicked, for an item heard more than once. */
  onSelect: (item: Item, span?: Span | null) => void;
  onSeek: (videoId: string, seconds: number) => void;
  onSelectVideo: (videoId: string) => void;
}) {
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const listRef = useRef<HTMLDivElement | null>(null);
  const activeRef = useRef<HTMLDivElement | null>(null);

  const lanes = useMemo(() => buildSpine(items, videos), [items, videos]);

  /* WHICH ROW is active, not which item. An item taken up twice is two rows,
   * and marking both would light up the rail in two places and scroll it to
   * whichever mounted last. The playhead settles it: the row whose stretch
   * contains it. Falling back to the first appearance covers the reader who
   * clicked a row without starting playback - and the moment they do start,
   * seeking lands inside a stretch and this agrees with them. */
  const activeKey = useMemo(() => {
    if (activeItem == null) return null;
    const mine = lanes.flatMap((l) => l.groups.flatMap((g) => g.rows)).filter((r) => r.item.id === activeItem);
    if (mine.length < 2) return mine[0]?.key ?? null;
    const playing = mine.find(
      (r) =>
        r.span &&
        r.span.video_id === activeVideo &&
        playhead != null &&
        playhead >= r.span.start &&
        playhead < r.end,
    );
    return (playing ?? mine[0]).key;
  }, [lanes, activeItem, activeVideo, playhead]);

  const query = filter.trim().toLowerCase();
  const matches = useMemo(() => {
    if (!query) return null;
    return new Set(
      items
        .filter(
          (i) =>
            i.title?.toLowerCase().includes(query) ||
            i.code?.toLowerCase().includes(query) ||
            i.case_id?.toLowerCase().includes(query),
        )
        .map((i) => i.id),
    );
  }, [items, query]);

  // Follow the recording. Scrolls the rail, never the page.
  useEffect(() => {
    const el = activeRef.current;
    const box = listRef.current;
    if (!el || !box) return;
    const e = el.getBoundingClientRect();
    const b = box.getBoundingClientRect();
    if (e.top < b.top + 8 || e.bottom > b.bottom - 8) {
      box.scrollTo({ top: box.scrollTop + (e.top - b.top) - b.height / 3, behavior: "smooth" });
    }
  }, [activeKey]);

  const current = videos.find((v) => v.id === activeVideo) ?? null;
  const bands: TimelineEvent[] = useMemo(() => {
    if (!current) return [];
    return items
      .flatMap((i) => i.spans.filter((sp) => sp.video_id === current.id).map((sp) => ({ i, sp })))
      .map(({ i, sp }) => ({
        at: sp.start,
        to: sp.end,
        label: `${i.code ? `${i.code} · ` : ""}${i.title ?? ""} (${clock(sp.start)})`,
        tone: outcomeTone(i.outcome),
        onSelect: () => onSelect(i),
      }));
  }, [current, items, onSelect]);

  return (
    <div className={s.spine}>
      {videos.length > 0 ? (
        <div className={s.track}>
          {/* A radiogroup, not a tablist: these choose which recording is
              loaded, they do not reveal a panel. */}
          {videos.length > 1 ? (
            <div className={s.sessions} role="radiogroup" aria-label="Recordings of this meeting">
              {videos.map((v) => (
                <button
                  key={v.id}
                  role="radio"
                  type="button"
                  aria-checked={v.id === activeVideo}
                  className={s.session}
                  onClick={() => onSelectVideo(v.id)}
                >
                  {sessionLabel(v.session_seq, videos.length)}
                  <span className={s.sessionLen}>{clock(v.duration)}</span>
                </button>
              ))}
            </div>
          ) : null}
          {current ? (
            <>
              <Timeline
                from={0}
                to={current.duration || 1}
                events={bands}
                marker={playhead}
                label={`Items across the ${sessionLabel(current.session_seq, videos.length).toLowerCase()}, and the playhead`}
                format={clock}
                onScrub={(at) => onSeek(current.id, at)}
              />
              <p className={s.trackNote}>
                {bands.length
                  ? `${bands.length} items located in this recording. Click a band to play it.`
                  : "No agenda items are located in this recording"}
              </p>
            </>
          ) : null}
        </div>
      ) : null}

      <div className={s.tools}>
        <label className={s.search}>
          <span className="sr-only">Filter this agenda</span>
          <input
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter this agenda: sidewalks, PDE-25-7738…"
          />
        </label>
        <span className={s.count}>
          {matches ? `${matches.size} of ${items.length}` : `${items.length} items`}
        </span>
      </div>

      <div className={s.list} ref={listRef}>
        {lanes.map((lane) => {
          const groups = lane.groups
            .map((g) => ({ g, visible: matches ? g.rows.filter((r) => matches.has(r.item.id)) : g.rows }))
            .filter(({ visible }) => visible.length);
          if (!groups.length) return null;
          const shown = groups.reduce((n, { visible }) => n + visible.length, 0);

          return (
            <section key={lane.key} className={s.lane}>
              {/* Only when there is something to distinguish. A meeting with no
                  recording is one lane and gets the plain agenda it always had, not a heading explaining an absent alternative. */}
              {lanes.length > 1 ? (
                <div className={s.laneHead}>
                  <h3 className={s.laneTitle}>{lane.label}</h3>
                  <span className={s.laneCount}>{shown}</span>
                </div>
              ) : null}
              {lanes.length > 1 && lane.note ? <p className={s.laneNote}>{lane.note}</p> : null}

              {groups.map(({ g, visible }) => {
                // A long routine run collapses to what it was: one motion.
                const collapsible = !matches && g.routine && g.rows.length > 8;
                const isOpen = open[g.key] ?? !collapsible;

                return (
                  <section key={g.key} className={s.group}>
                    {g.label ? (
                      <h4 className={s.groupHead}>
                        {collapsible ? (
                          <button
                            type="button"
                            className={s.groupToggle}
                            onClick={() => setOpen((o) => ({ ...o, [g.key]: !isOpen }))}
                            aria-expanded={isOpen}
                          >
                            <span aria-hidden className={s.chev} data-open={isOpen}>
                              ▸
                            </span>
                            {g.label}
                            <span className={s.groupCount}>{g.rows.length}</span>
                          </button>
                        ) : (
                          <span className={s.groupLabel}>
                            {g.label}
                            <span className={s.groupCount}>{visible.length}</span>
                          </span>
                        )}
                      </h4>
                    ) : null}

                    {collapsible && !isOpen ? (
                      <button
                        type="button"
                        className={s.enBloc}
                        onClick={() => setOpen((o) => ({ ...o, [g.key]: true }))}
                      >
                        <span className={s.enBlocText}>
                          {g.rows.length} items taken together
                        </span>
                        <span className={s.enBlocOutcomes}>
                          {summarise(g.rows.map((r) => r.item)).map(([outcome, n]) => (
                            <span key={String(outcome)} className={s.enBlocOutcome}>
                              <OutcomeBadge outcome={outcome} size="sm" />
                              <span className={s.enBlocN}>{n}</span>
                            </span>
                          ))}
                        </span>
                      </button>
                    ) : (
                      <div className={s.rows}>
                        {visible.map((row) => (
                          <div
                            key={row.key}
                            ref={row.key === activeKey ? activeRef : undefined}
                            className={s.rowWrap}
                          >
                            <ItemCard
                              item={row.item}
                              density="row"
                              span={row.span}
                              nth={row.nth}
                              of={row.of}
                              times={row.times}
                              active={row.key === activeKey}
                              activeVideo={activeVideo}
                              onSelect={() => onSelect(row.item, row.span)}
                            />
                          </div>
                        ))}
                      </div>
                    )}
                  </section>
                );
              })}
            </section>
          );
        })}
      </div>
    </div>
  );
}

/**
 * One row of the spine. Not one item: an item the board takes up, sets aside
 * and returns to later is TWO rows, because a chronological rail can only put
 * a row at one time and both times are real.
 */
interface Row {
  key: string;
  item: Item;
  /** The stretch of recording this row plays. null in the published lane. */
  span: Span | null;
  /** Where the run this row covers ends, for deciding which row is playing. */
  end: number;
  /** 1-based, out of `of`. Both are 1 for an item heard once or not at all. */
  nth: number;
  of: number;
  /** Every time this item is taken up, for the row's tooltip. */
  times: number[];
}

interface Group {
  key: string;
  /** null draws no header: a single session needs no label above its rows. */
  label: string | null;
  rows: Row[];
  /** Consent and similar: taken en bloc, no discussion, collapse by default. */
  routine: boolean;
}

interface Lane {
  key: string;
  label: string;
  note?: string;
  groups: Group[];
}

/**
 * Two spans of one item closer together than this are one appearance.
 *
 * Not a round number picked for looking reasonable. Across the archive, the
 * gaps between consecutive spans of the same item fall in two clumps with
 * nothing between them:
 *
 *     0s x6, 2s x2, 4s, 5s   |   64s, 65s, 67s, 74s, 86s, ... 207m
 *
 * The first ten are the span-binder cutting one continuous discussion in two;
 * the rest are the board genuinely leaving an item and coming back. Any
 * threshold in that trough gives the same answer, and this one has 55 seconds
 * of slack on both sides. Erring high would merge a real return into silence;
 * erring low only shows a row the reader can see and dismiss, so if the trough
 * ever fills in, move this DOWN.
 */
const ONE_APPEARANCE = 60;

/**
 * The spine, in the two orders this material actually supports.
 *
 * An item is in the first lane if we can say WHEN it happened, and in the
 * second if we cannot. That is the only line available: 5,500 items in the
 * archive are bound to a stretch of recording and roughly 17,600 are not, and
 * nothing about an unbound item tells us where in the day it fell. The board
 * routinely takes published item 77 after published item 94.
 *
 * Deriving a time for the unbound ones from their published neighbours would
 * fill the rail out nicely and would be a guess rendered as a timestamp - the
 * same trade refuses everywhere else.
 *
 * Neither lane re-sorts anything it has no basis to re-sort: lane one is
 * ordered by the clock, lane two arrives in published `seq` and stays in it.
 */
function buildSpine(items: Item[], videos: Video[]): Lane[] {
  /* Offsets restart at zero in each recording, so an offset alone does not
   * order anything on a two-session day - and 126 of 283 recorded meetings are
   * two sessions or three. Ordering on the video's POSITION in this list rather
   * than on session_seq: the API already returns them in the right order, and
   * session_seq is null on many older recordings, where two nulls and a zero
   * would collide into one session. */
  const at = new Map(videos.map((v, i) => [v.id, i]));

  /** Every distinct time this item is taken up, in order. */
  const appearances = (item: Item) => {
    const spans = (item.spans ?? [])
      // A span into a recording this page is not showing cannot be played, so
      // the item belongs with the ones we cannot place rather than at a time
      // the reader has no way to reach.
      .filter((sp) => at.has(sp.video_id))
      .sort((a, b) => at.get(a.video_id)! - at.get(b.video_id)! || a.start - b.start);

    const runs: { span: Span; v: number; start: number; end: number }[] = [];
    for (const sp of spans) {
      const v = at.get(sp.video_id)!;
      const last = runs[runs.length - 1];
      // Same recording and close enough to be one discussion: extend it. The
      // binder cuts a continuous stretch in two often enough that drawing both
      // halves would report a return to an item nobody left.
      if (last && last.v === v && sp.start - last.end <= ONE_APPEARANCE) {
        last.end = Math.max(last.end, sp.end);
      } else {
        runs.push({ span: sp, v, start: sp.start, end: sp.end });
      }
    }
    return runs;
  };

  const heard: (Row & { v: number; start: number })[] = [];
  const rest: Item[] = [];
  for (const item of items) {
    const runs = appearances(item);
    if (!runs.length) {
      rest.push(item);
      continue;
    }
    const times = runs.map((r) => r.start);
    runs.forEach((r, i) =>
      heard.push({
        key: `${item.id}-${i}`,
        item,
        span: r.span,
        end: r.end,
        nth: i + 1,
        of: runs.length,
        times,
        v: r.v,
        start: r.start,
      }),
    );
  }
  heard.sort((a, b) => a.v - b.v || a.start - b.start || a.item.seq - b.item.seq);

  const lanes: Lane[] = [];

  if (heard.length) {
    const groups: Group[] = [];
    for (const row of heard) {
      // One recording needs no divider; two or more do, or the rail appears to
      // run 2:09:53 then 1:01:10 for no reason a reader can see.
      const label =
        videos.length > 1 ? sessionLabel(videos[row.v]?.session_seq ?? row.v, videos.length) : null;
      const last = groups[groups.length - 1];
      if (last && last.label === label) last.rows.push(row);
      else groups.push({ key: `heard-${groups.length}`, label, rows: [row], routine: false });
    }
    lanes.push({ key: "heard", label: "As it happened", groups });
  }

  if (rest.length) {
    const groups: Group[] = [];
    for (const item of rest) {
      const label = item.section?.trim() || phaseLabel(item.phase);
      const row: Row = { key: `${item.id}`, item, span: null, end: 0, nth: 1, of: 1, times: [] };
      const last = groups[groups.length - 1];
      if (last && last.label === label) last.rows.push(row);
      else
        groups.push({
          key: `rest-${groups.length}`,
          label,
          rows: [row],
          routine: item.phase === "consent",
        });
    }
    lanes.push({
      key: "rest",
      label: "Also on the agenda",
      /* Says why the order changes half way down the rail. Not "mostly
       * consent", which was the obvious thing to write and is untrue: the
       * 12,357 unlocated items on recorded days are 49% consent and 48%
       * public hearings. */
      note: "We could not place these in the recording, so they stay in the order the agenda lists them.",
      groups,
    });
  }

  return lanes;
}

function summarise(items: Item[]): [Item["outcome"], number][] {
  const counts = new Map<Item["outcome"], number>();
  for (const i of items) counts.set(i.outcome, (counts.get(i.outcome) ?? 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}
