"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { SpeakerChip, voiceTags } from "@/components/SpeakerChip";
import { groupTurns } from "@/lib/turns";
import type { Line, Office } from "@/lib/types";
import { usePlayer } from "./PlayerProvider";
import s from "./Captions.module.css";

/**
 * What is being said, under the recording saying it.
 *
 * THE ARCHIVE'S OWN WORDS, not YouTube's. The county's recordings carry
 * auto-generated captions that get this county's vocabulary wrong in the
 * specific ways bin/lexicon.py exists to fix - Withlacoochee, Aripeka,
 * DeCubellis, severability - and they are burned over the picture where
 * nothing can be done about them. These lines came out of the archive's own
 * transcription, with that lexicon boosted into it, and they are the same
 * lines the rest of the site quotes and cites. So the embed's captions are
 * turned off (see PlayerProvider) and this stands in their place.
 *
 * A SHORT RUN OF LINES, FOLLOWING THE PLAYHEAD. The first cut of this was a
 * list and it did not survive being looked at: seventeen lines of small grey
 * text in a six-rem box, no line separated from the next, the one being
 * spoken marked so faintly it was lost among them, and the box's own
 * scrollbar and scroll arrows inside a card that narrows to eighteen rems. It
 * was a wall. The second cut was a single line, which was legible and held
 * too little to follow an argument with.
 *
 * This is the list again with the things that were wrong with it fixed: room
 * to be a list, the line being spoken marked in the archive's own live
 * colour, and a rule that the reading is scrolled to with context ABOVE it
 * rather than flush against the top edge - so the sentence that led into the
 * current one is still there to be read.
 *
 * WHY THIS IS NOT `<Turns>`, which is the archive's transcript renderer and
 * was the first thing to reach for. Everything that must not diverge is
 * shared with it - `groupTurns` for where a turn ends, `voiceTags` for what
 * an unnamed voice may be called, `SpeakerChip` for how sure the archive is
 * of a name - and all three are imported here rather than reimplemented. What
 * is not shared is the layout, because `Turns` lays a turn out as an 11rem
 * speaker column beside body-sized text, and below 46rem it stacks and turns
 * its sticky name OFF. The dock is routinely narrower than 46rem and needs
 * the name pinned at every width, which is the opposite rule. Two layouts,
 * one set of claims.
 *
 * AND IT STOPS FOLLOWING WHEN THE READER TAKES IT. Scrolling back through
 * what was just said is the reason to have a scrollbar at all, and a box that
 * yanked itself back to the playhead every few seconds would make that
 * impossible. It follows again the moment the line being spoken is back in
 * view, which is the reader saying they are done looking.
 */

/**
 * HOW MUCH IS HELD, in seconds either side of the playhead.
 *
 * Behind is small because it is context - a reader who wants what was said
 * five minutes ago has the meeting page for that. Ahead is larger because it
 * is what the strip is about to need, and because a fetch that lands before
 * the playhead arrives is a fetch nobody sees.
 */
const BEHIND = 60;
const AHEAD = 240;

/**
 * How close the playhead may get to the edge of the window before the next
 * one is asked for. Wide enough that the request has time to land at any
 * plausible speed; narrower than AHEAD, or the window would be replaced the
 * moment it arrived.
 */
const MARGIN = 60;

/**
 * How much of what came before stays on screen when the box scrolls to the
 * line being spoken, in pixels - about one line of it.
 *
 * Scrolled flush to the top edge, the current line arrives with nothing in
 * front of it and the sentence that led into it has just vanished. This is
 * the difference between a caption and a transcript you are reading along
 * with.
 */
const LEAD = 26;

/**
 * How far the box may sit from where following would put it and still count
 * as following, in pixels. A line and a half: enough that a browser rounding
 * a scroll position, or a line of a different height arriving, does not read
 * as the reader grabbing the box.
 */
const NEAR = 40;

/** Where the box rests, so that the spoken line sits under its lead-in. */
const parkAt = (box: HTMLElement, now: HTMLElement) =>
  Math.max(0, now.offsetTop - box.offsetTop - LEAD);

type Held = {
  videoId: string;
  from: number;
  to: number;
  lines: Line[];
  offices: Record<string, Office>;
};

export function Captions({ onResize }: { onResize?: () => void }) {
  const p = usePlayer();
  const [held, setHeld] = useState<Held | null>(null);
  /**
   * Whether the archive has these words at all, which is a real state rather
   * than a failure. 1,036 hours are transcribed and the recordings go back
   * further than that, so a video with no lines is ordinary - and the strip
   * has to say which of "nothing said here" and "nothing transcribed here" it
   * is looking at, because they are not the same fact.
   */
  const [missing, setMissing] = useState(false);
  const asked = useRef<string | null>(null);
  const box = useRef<HTMLDivElement | null>(null);
  /**
   *  WHOSE NAME BELONGS AT THE TOP EDGE, when it is not on screen itself.
   *
   * `null` means the top turn's own name is still visible in the box and is
   * doing the job; a number means it has scrolled above the edge and this
   * strip has to say who is still talking.
   *
   * A ref beside the state because this is read on every scroll event and
   * only worth a render when the answer CHANGES, which is once per turn
   * rather than once per frame.
   */
  const pinned = useRef<number | null>(null);
  const pinnedEl = useRef<HTMLParagraphElement | null>(null);
  const [pinnedTurn, setPinnedTurn] = useState<number | null>(null);
  /* Always rendered rather than only once the list's own name has gone past.
     Appearing and disappearing was a second thing that could be wrong at a
     boundary, and it buys nothing: at the top of the list this sits exactly
     over the row it is repeating, which is the same pixels either way. */
  /** Whether the box is still following the recording, or the reader has it. */
  const follow = useRef(true);

  const videoId = p.source?.videoId ?? null;
  const at = p.position;

  /* WHAT IS NEEDED, decided from the playhead alone, so that a seek and
   * ordinary playback go down the same path. A window is wanted when there is
   * none, when it belongs to another recording, or when the head has come
   * within MARGIN of an edge that is not the end of the recording. */
  const want =
    videoId == null
      ? null
      : !held || held.videoId !== videoId
        ? Math.max(0, at - BEHIND)
        : at > held.to - MARGIN || at < held.from
          ? Math.max(0, at - BEHIND)
          : null;

  useEffect(() => {
    if (videoId == null || want == null) return;
    const to = want + BEHIND + AHEAD;
    /* One request per window, and never the same one twice: `want` is
     * recomputed on every tick of the playhead, and without this the strip
     * would re-ask four times a second for as long as the head sat inside the
     * margin. */
    const key = `${videoId}@${Math.round(want)}`;
    if (asked.current === key) return;
    asked.current = key;
    const stop = new AbortController();
    fetch(`/api/transcript/${videoId}?from=${Math.round(want)}&to=${Math.round(to)}`,
          { signal: stop.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: { lines?: Line[]; offices?: Record<string, Office>; span?: number[] }) => {
        setHeld({
          videoId,
          from: d.span?.[0] ?? want,
          to: d.span?.[1] ?? to,
          lines: d.lines ?? [],
          offices: d.offices ?? {},
        });
        setMissing((d.lines ?? []).length === 0);
      })
      .catch(() => {
        /* Left to the next tick to try again. A caption that cannot be
         * fetched is not worth a message: the recording is still playing and
         * the strip simply has nothing to show. */
        if (!stop.signal.aborted) asked.current = null;
      });
    return () => stop.abort();
  }, [videoId, want]);

  const lines = held?.videoId === videoId ? held.lines : [];
  /* The line being spoken. Not a `find` on containment: transcripts have
   * gaps - silence, a redaction, a stretch nobody transcribed - and during
   * one of those no line contains the playhead at all. The last line that has
   * STARTED is what is on screen, which is also what a reader would say is
   * "where we are". */
  let now = -1;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].start <= at + 0.25) now = i;
    else break;
  }

  /* The last turn whose own name has gone above the top edge. Same content
     coordinates as `parkAt`: a name's `offsetTop` less the box's is its
     distance down the scrolling content, which is what `scrollTop` counts.
     Stable, so the effects below can depend on it and the scroll handler can
     call it without rebuilding anything. */
  const readPinned = useCallback(() => {
    const b = box.current;
    if (!b) return;
    /*
     *  AND NO NAME IS EVER HALF OF ITSELF.
     *
     * A row passing under the pinned name is partly covered for as long as it
     * takes to clear it - which for a text line is ordinary scrolling and for
     * a name is a fragment of somebody's name, the thing this whole change is
     * about. Emerging looks exactly as broken as leaving did.
     *
     * So a name row that intersects the band the pinned one occupies is
     * hidden outright. `visibility`, not `display`: it keeps its space, so
     * nothing under it moves as it passes, and it arrives whole.
     */
    const band = pinnedEl.current?.offsetHeight ?? 0;
    let found: number | null = null;
    for (const el of b.querySelectorAll<HTMLElement>("[data-turn]")) {
      const top = el.offsetTop - b.offsetTop;
      const hidden = top < b.scrollTop + band && top + el.offsetHeight > b.scrollTop;
      if (hidden) el.setAttribute("data-under", "");
      else el.removeAttribute("data-under");
    }
    for (const el of b.querySelectorAll<HTMLElement>("[data-turn]")) {
      /*
       * `<=`, and it is the whole correctness of this thing. The name that
       * owns the top edge is the last one at OR above it. Written `<`, the
       * pixel where a new speaker's name lands exactly on the edge is a pixel
       * where this covers that name and goes on showing the speaker before
       * them - a name over somebody else's words, which is the one mistake
       * this archive is most careful about. Eight positions in five hundred,
       * every one of them a turn boundary.
       */
      if (el.offsetTop - b.offsetTop <= b.scrollTop + 0.5) {
        found = Number(el.dataset.turn);
      } else break;
    }
    if (found !== pinned.current) {
      pinned.current = found;
      setPinnedTurn(found);
    }
  }, []);

  /* FOLLOW THE PLAYHEAD, inside this box only, and with room above.
   *
   * `scrollIntoView` is the obvious call and the wrong one twice over: it
   * scrolls the PAGE to bring the dock into view, which on /ask means yanking
   * the reader off the answer they are reading, and it puts the line against
   * an edge. Setting `scrollTop` directly moves nothing but this box, and
   * LEAD keeps the sentence that led into this one on screen.
   */
  useEffect(() => {
    const b = box.current;
    const el = b?.querySelector<HTMLElement>("[data-now]");
    if (!b || !el || !follow.current) return;
    b.scrollTop = parkAt(b, el);
    /* This moved the box without a scroll event the reader caused, and the
       name at the edge is a function of where the box is, not of who did it. */
    readPinned();
  }, [now, videoId, lines.length, readPinned]);

  /* A NEW RECORDING IS A FRESH START. Whatever the reader was reading in the
   * last one, they are not reading it now, and carrying "they have taken the
   * box" across a change of video would leave the strip inert on arrival. */
  useEffect(() => {
    follow.current = true;
  }, [videoId]);

  /* Whether the box is still the recording's to move, or the reader has it.
   *
   * MEASURED AGAINST WHERE FOLLOWING WOULD PUT IT, not against whether the
   * spoken line happens to be visible. Those sound equivalent and are not:
   * the line can leave the visible area for reasons that have nothing to do
   * with the reader - a new window of transcript arrives and every offset
   * above it moves - and the visibility test read that as "they have taken
   * the box", stopped following, and then waited for a reader who did not
   * know they had to do anything.
   *
   * Comparing with the intended position is self-correcting instead: after an
   * automatic scroll the two agree exactly, so following stays on; a reader
   * who scrolls away disagrees with it and takes over; and scrolling roughly
   * back to the line hands it over again. It is read from scrollTop rather
   * than from wheel or drag events because the OUTCOME is what matters, and
   * there are half a dozen ways to produce it.
   */
  const onScroll = () => {
    const b = box.current;
    readPinned();
    const el = b?.querySelector<HTMLElement>("[data-now]");
    if (!b || !el) return;
    follow.current = Math.abs(b.scrollTop - parkAt(b, el)) < NEAR;
  };


  /* THE ARCHIVE'S OWN GROUPING, not another one. `sameTurn` also compares
   * `basis` - how the name was arrived at - so two runs a hand-rolled
   * name-and-voice test would weld together are correctly two turns, with two
   * different claims about who is talking. Getting that wrong here would have
   * put one speaker chip over words the archive attributes two ways. */
  const turns = groupTurns(lines);
  /* Page-local letters for the voices with no name, which is the only thing
   * that may stand in for one. A cluster id and the diarizer's own label are
   * both reshuffled by every clustering run and both read as names - see
   * SpeakerChip. This strip was handing `local_label` straight to the chip. */
  const tags = voiceTags(lines);

  /**
   * TELL THE DOCK IT HAS CHANGED SIZE.
   *
   * The dock publishes the room it takes at the foot of the window so pages
   * can lay themselves out clear of it, and it watched itself with a
   * ResizeObserver to know when. That missed this strip: the lines arrive
   * from a fetch, so the growth happens in a re-render of THIS component and
   * the dock never renders at all. It was publishing 257px while standing in
   * front of 393, and the difference was 136px of answer hidden behind it on
   * a phone.
   */
  useEffect(() => {
    onResize?.();
    /* `held` rather than `lines`, which is a conditional recomputed on every
       tick of the playhead - this only needs to fire when a window of
       transcript actually lands, which is the only thing that changes the
       strip's height. */
  }, [onResize, held, missing]);

  /* A new window of transcript moves every offset in the box, so whoever was
     at the edge may not be any more. */
  useEffect(() => {
    readPinned();
  }, [readPinned, held, missing]);

  /* One speaker's chip, built the same way whether it is sitting in the list
     or pinned to the edge - so the pinned one cannot drift into saying
     something slightly different from the line it stands for. */
  const chipFor = (l: Line) => (
    <SpeakerChip
      who={l.who}
      /* The roster keys on surname, and a full name has to fall back to it -
         the same two-step every other view does. */
      office={
        (l.name
          ? (held?.offices[l.name] ?? held?.offices[l.name.split(" ").pop() ?? ""])
          : null) ?? null
      }
      voiceTag={l.voice != null ? (tags.get(l.voice) ?? null) : null}
      size="sm"
    />
  );

  if (!p.source) return null;

  return (
    <div className={s.strip} aria-label="What is being said">
      {missing && !lines.length ? (
        <p className={s.none}>
          This recording has not been transcribed, so the archive has no words
          for it.
        </p>
      ) : (
        <>
          {/*
            THE ONE NAME AT THE EDGE.
            Shown only once the top turn's own name has scrolled above it, so
            the two are never both on screen saying the same thing - and being
            one element that changes its text rather than two sticky rows
            handing over, there is no moment where a name is half of itself.
            `aria-hidden`: the name it repeats is still in the list below, and
            a screen reader following the transcript should hear it once.
          */}
          {pinnedTurn != null && turns[pinnedTurn] ? (
            <p className={s.pinned} ref={pinnedEl} aria-hidden>
              {chipFor(turns[pinnedTurn][0])}
            </p>
          ) : null}
          <div className={s.box} ref={box} onScroll={onScroll}>
          {turns.map((run, i) => (
            <div key={run[0].idx} className={s.turn}>
              {/* STUCK TO THE TOP FOR AS LONG AS THEY ARE TALKING. A name on
                  every line is a column of chips, so it is drawn once per
                  turn - but drawn once and left there, it scrolled away the
                  moment somebody said more than fits in the box, leaving the
                  reader watching words with nobody attached to them. Sticky
                  inside its own turn, it stays until the next speaker's name
                  pushes it out, which also means scrolling back through the
                  transcript always says whose words are on screen. */}
              <p className={s.who} data-turn={i}>
                {chipFor(run[0])}
              </p>
              {run.map((l) => (
                /* A line is a place in the recording, so pressing it goes
                   there. Flat, with only a tint under the pointer: a
                   transcript set in buttons reads as a menu, which is what
                   the underline on these used to make of it. */
                <button
                  key={l.idx}
                  type="button"
                  className={s.said}
                  data-now={l.idx === lines[now]?.idx || undefined}
                  onClick={() => {
                    /* Pressing a line is the reader saying "put me here",
                       which is also them handing the box back: whatever they
                       had scrolled off to look at, this is where they want to
                       be now. */
                    follow.current = true;
                    p.seek(l.start);
                  }}
                >
                  {l.text}
                </button>
              ))}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
