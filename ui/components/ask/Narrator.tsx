"use client";

import { useEffect } from "react";

import { usePlayer } from "@/components/player/PlayerProvider";
import type { Narration } from "./narration";
import s from "./Narrator.module.css";

/**
 * How far one press moves along a recording, in seconds.
 *
 * The same ten the dock's own keys move, and the same ten the player it is
 * embedding moves: a reader who has learned the number anywhere has learned
 * it here. It is also comfortably inside the grace the provider allows in
 * front of a cited stretch, so nudging back at the head of a clip stays
 * inside the clip rather than ending the narration.
 */
const SEEK = 10;

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
  const player = usePlayer();
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

  /**
   * The keys. ONE TRANSPORT, on two axes.
   *
   * DOWN THE PAGE IS THROUGH THE ANSWER and ACROSS IS ALONG THE RECORDING.
   * That is the whole of the mapping, and it is the second one this control
   * had. The first gave the narration its own set - arrows stepped a sentence
   * - and left the player's `j k l` live underneath, so a reader steering a
   * clip had two vocabularies for one transport and had to know which object
   * a key was aimed at. Worse, `←` meant "back a sentence" here and "rewind"
   * in every video anybody has ever watched, so the reflex that follows a
   * missed word threw the reader out of the clip instead of back into it.
   *
   * Split by axis, each key does the thing its direction already means:
   * `↑ ↓` move between the sentences and the recordings, `← →` move within
   * whichever recording is playing. Nothing is aimed at the wrong object,
   * because the two axes cannot address the same one.
   *
   * WHAT IT COSTS is scrolling the page with the keyboard while a narration
   * runs, which is close to free: the narration scrolls the page to follow
   * itself, so a reader scrolling away by hand is already being dragged back
   * every sentence. PageUp and PageDown are untouched for anyone who wants
   * it anyway.
   *
   * CAPTURE, and it stops the event dead. The dock has its own `j k l` on
   * this same window, and `k` there paused the video WITHOUT telling the
   * narration - which left this bar saying "Playing the recording" over a
   * stopped one. Claimed in the capture phase, a running narration is the
   * only thing steering, and the dock's keys go back to being the dock's the
   * moment it stops.
   *
   * A FOCUSED BUTTON KEEPS ITS OWN SPACE BAR. Pressing Pause with the mouse
   * leaves that button focused, and without this the next press of space both
   * clicked it and ran the shortcut - which paused and resumed in the same
   * keystroke and looked like a control that did nothing.
   */
  useEffect(() => {
    if (!running) return;
    function onKey(e: KeyboardEvent) {
      if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.isContentEditable
                || /^(INPUT|TEXTAREA|SELECT|BUTTON|A)$/.test(t.tagName))) return;
      const take = () => {
        e.preventDefault();
        e.stopPropagation();
      };
      /* `k` alongside space, rather than left to the dock, so that one key
         does one thing: whichever of the two a reader reaches for, the
         narration is what hears it and the bar cannot fall out of step. */
      if (e.key === " " || e.key === "k") { take(); n.togglePause(); }
      else if (e.key === "ArrowDown") { take(); n.skip(); }
      else if (e.key === "ArrowUp") { take(); n.back(); }
      else if (e.key === "Escape") { take(); n.stop(); }
      /* ACROSS ONLY WHERE THERE IS A TIMELINE. While the voice is reading
         there is nothing to move along - the recording under the dock, if one
         is still loaded, is a clip that finished several sentences ago - so
         these are not claimed at all rather than claimed and made to do
         nothing. */
      else if (n.phase === "listening" && e.key === "ArrowLeft") {
        take();
        player.seek(player.position - SEEK);
      } else if (n.phase === "listening" && e.key === "ArrowRight") {
        take();
        player.seek(player.position + SEEK);
      }
    }
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [running, n, player]);

  if (!n.supported) return null;

  return (
    <div className={s.bar} data-running={running || undefined}>
      {running ? (
        <>
          {/* Transport order, so the pair either side of pause reads as one
              thing: back a sentence, hold, on to the next. */}
          <button type="button" className={s.step} onClick={n.back}
                  aria-label="Back one sentence" aria-keyshortcuts="ArrowUp"
                  title="Back one sentence (↑). Onto a recording, plays it again.">
            <span aria-hidden>▲</span>
          </button>
          <button type="button" className={s.key} onClick={n.togglePause}
                  aria-keyshortcuts="Space K"
                  title={`${n.paused ? "Resume" : "Pause"} (space or k)`}>
            <span aria-hidden>{n.paused ? "▶" : "▮▮"}</span>
            {n.paused ? "Resume" : "Pause"}
          </button>
          {/* THE RECORDING'S OWN CONTROLS, kept with the line that names it.
              On a narrow screen this whole group drops to a second row, so
              the two seek buttons travel with the speaker and the clocks they
              move between rather than stranding themselves among the
              narration's buttons. */}
          <div className={s.middle}>
            {n.phase === "listening" ? (
              <span className={s.nudges}>
                <button type="button" className={s.nudge}
                        onClick={() => player.seek(player.position - SEEK)}
                        aria-label={`Back ${SEEK} seconds in the recording`}
                        aria-keyshortcuts="ArrowLeft"
                        title={`Back ${SEEK} seconds (←)`}>
                  −{SEEK}s
                </button>
                <button type="button" className={s.nudge}
                        onClick={() => player.seek(player.position + SEEK)}
                        aria-label={`Forward ${SEEK} seconds in the recording`}
                        aria-keyshortcuts="ArrowRight"
                        title={`Forward ${SEEK} seconds (→)`}>
                  +{SEEK}s
                </button>
              </span>
            ) : null}
          <p className={s.doing} aria-live="polite">
            {n.phase === "listening" && n.step?.kind === "hear" ? (
              <>
                {/* A CLIP CAN BE HELD TOO. This line said "Playing the
                    recording" whatever the player was doing, which was a lie
                    the moment anybody paused - and pausing is now reachable
                    from the bar, the dock and two keys. The speaker and the
                    clocks under it stay either way: they say WHAT is loaded,
                    which is still true of a recording that is stopped. */}
                <span className={s.playing}>
                  {n.paused ? "The recording is paused" : "Playing the recording"}
                </span>
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
            {/* WHAT WENT WRONG, WHILE IT CARRIES ON. A recording that would
                not start is stepped over rather than dying on, so the only
                place the reason can be said is here, in the running bar - the
                idle line below never gets a chance to show it. */}
            {n.trouble ? <span className={s.trouble}>{n.trouble}</span> : null}
          </p>
          </div>
          {/* Skipping a clip is the pressure valve. A cited stretch can run
              for minutes, and without a way past it the only escape from a
              long one is to abandon the whole narration. */}
          <button type="button" className={s.minor} onClick={n.skip}
                  aria-keyshortcuts="ArrowDown"
                  title={n.phase === "listening"
                    ? "Stop the recording and read on (↓)"
                    : "On to the next sentence (↓)"}>
            {n.phase === "listening" ? "Heard enough" : "Skip"}
          </button>
          <button type="button" className={`${s.minor} ${s.stop}`} onClick={n.stop}
                  aria-keyshortcuts="Escape" title="Stop reading (esc)">
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
            <>
              Reads the answer aloud and plays each recording it cites, where it
              is cited.{" "}
              {/* THE SHORTCUTS ARE SAID HERE, in the one state with room for
                  them. A reader who has not started yet is the reader who can
                  still take a sentence in; while it is running the bar is
                  three buttons and a line saying what is playing, and a legend
                  under all that would make the thing that follows the reading
                  down the page twice as tall as it needs to be. */}
              <span className={s.keys}>
                Tap or click any sentence to read from there.{" "}
                {/* The keys are worth a line on a machine that has them and
                    are noise on one that does not, where every one of these
                    actions is a button on the bar instead. */}
                <span className={s.forKeys}>
                  While it reads, <kbd>space</kbd> holds it, <kbd>↑</kbd> and{" "}
                  <kbd>↓</kbd> step back and on a sentence, and <kbd>esc</kbd>{" "}
                  stops. Inside a recording, <kbd>←</kbd> and <kbd>→</kbd> move
                  ten seconds.
                </span>
              </span>
            </>
          )}
        </p>
      )}
    </div>
  );
}
