"use client";

import { useEffect } from "react";

import type { Narration } from "./narration";
import s from "./Narrator.module.css";

/**
 * The control that reads an answer aloud and plays its citations in place.
 *
 * IT SAYS WHAT IT IS DOING, always. A synthesised voice that stops talking is
 * ambiguous - it could be finished, stuck, or about to play something - and
 * the reader's instinct in every one of those cases is to press a button and
 * find out, which is the one thing that breaks the sequence. So the line
 * beside the buttons names the current step, and while a clip is playing it
 * names the person in it.
 */
export function Narrator({ narration: n }: { narration: Narration }) {
  const running = n.phase !== "off";

  /* FOLLOW THE READING. A narration that scrolls off the top of the window is
   * a podcast, and the reader has lost the one thing this offers over one:
   * seeing which sentence the archive is standing on. */
  useEffect(() => {
    if (!running || n.at < 0) return;
    const el = document.getElementById(`say-${n.step?.part ?? -1}`);
    if (!el) return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ block: "center", behavior: still ? "auto" : "smooth" });
  }, [running, n.at, n.step?.part]);

  if (!n.supported) return null;

  return (
    <div className={s.bar} data-running={running || undefined}>
      {running ? (
        <>
          <button type="button" className={s.key} onClick={n.togglePause}>
            <span aria-hidden>{n.paused ? "▶" : "▮▮"}</span>
            {n.paused ? "Resume" : "Pause"}
          </button>
          <p className={s.doing} aria-live="polite">
            {n.phase === "listening" && n.step?.kind === "hear" ? (
              <>
                <span className={s.playing}>Playing the recording</span>
                <span className={s.who}>
                  {n.step.what}
                  {/* Never silently. The citation is longer than this clip,
                      and the reader is owed that before they conclude they
                      have heard it all. Its marker in the prose plays the
                      whole stretch. */}
                  {n.step.trimmed ? (
                    <span className={s.part}> · the citation runs on past this</span>
                  ) : null}
                </span>
              </>
            ) : (
              <span className={s.playing}>
                {n.paused
                  ? "Paused"
                  : n.phase === "fetching"
                    /* Only ever the first sentence: every one after it is
                       fetched while the one before it plays. Named anyway,
                       because a cold model is a second and a half and a
                       control that says "Reading" in silence is worse than
                       one that says what it is doing. */
                    ? "Getting the voice"
                    : "Reading the answer"}
              </span>
            )}
          </p>
          {/* Skipping a clip is the pressure valve. A cited stretch can run
              for minutes, and without a way past it the only escape from a
              long one is to abandon the whole narration. */}
          <button type="button" className={s.minor} onClick={n.skip}>
            {n.phase === "listening" ? "Heard enough" : "Skip"}
          </button>
          <button type="button" className={s.minor} onClick={n.stop}>
            Stop
          </button>
        </>
      ) : (
        <button type="button" className={s.key} onClick={n.start}>
          <span aria-hidden className={s.icon}>
            ▸
          </span>
          Play this answer
        </button>
      )}
      {running ? null : (
        <p className={s.what}>
          {n.trouble ? (
            /* THE SERVER'S OWN SENTENCE. Every refusal on that endpoint is
               written to be read by a person - how long to wait, or that the
               archive has no voice loaded - so it is shown rather than
               translated into something vaguer here. */
            <span className={s.trouble}>{n.trouble}</span>
          ) : (
            "Reads the answer aloud and plays each recording it cites, where it is cited."
          )}
        </p>
      )}
    </div>
  );
}
