"use client";

import { useEffect, useRef, useState } from "react";

import { SpeakerChip } from "@/components/SpeakerChip";
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
 * ONE LINE, WHICH IS WHAT A CAPTION IS. This was a scrolling list of the
 * window either side of the playhead, and it did not survive being looked at:
 * seventeen lines of small grey text in a six-rem box, with its own scrollbar
 * and its own scroll arrows inside a card the reader can narrow to eighteen
 * rems. A list needs room to read as a list, the dock has none, and what it
 * produced was a wall.
 *
 * So the strip shows the sentence being spoken and the person speaking it,
 * and nothing else. It is the same amount of information a caption has always
 * been, it works at any width the dock can be dragged to, and it cannot
 * scroll, jump or grow under the pointer. What came before is above it in the
 * page or a click away on the meeting; what is coming is about to be said.
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

type Held = {
  videoId: string;
  from: number;
  to: number;
  lines: Line[];
  offices: Record<string, Office>;
};

export function Captions() {
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

  const line = now >= 0 ? lines[now] : null;

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
          {/* The name goes above the words rather than in front of them. In a
              row they wrapped together, so a long name pushed the sentence
              down a line and a short one let it start halfway across - the
              text began somewhere different on every change of speaker. */}
          {line ? (
            <SpeakerChip
              who={line.who}
              office={line.name ? held?.offices[line.name] : null}
              voiceTag={line.local_label}
              size="sm"
            />
          ) : null}
          {/* HELD OPEN whether or not there is a line. Transcripts have gaps -
              silence, a redaction, a stretch nobody transcribed - and a strip
              that collapsed through them would resize the dock, and everything
              the dock is sitting next to, several times a minute. */}
          <p className={s.said} title={line?.text ?? undefined}>
            {line?.text ?? ""}
          </p>
        </>
      )}
    </div>
  );
}
