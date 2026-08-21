"use client";

import { useCallback, useEffect, useRef } from "react";

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
 * How far under the bar a passage too tall for the band is parked, in pixels.
 * Enough to read as sitting below the control rather than jammed against it.
 */
const PEEK = 8;

/**
 * The keys, named once. Every control below prints the one it answers to, so
 * a reader who has never read a shortcut list still learns them by using the
 * bar - which is the only way anybody learns a shortcut.
 */
const KEYS = { hold: "k", back: "p", on: "n", rew: "j", ff: "l" } as const;

/** The key a control answers to, printed on it. Hidden where there is no
 *  keyboard to press it with - see the coarse-pointer rules in the CSS. */
function Key({ is }: { is: string }) {
  return (
    <kbd className={s.hint} aria-hidden>
      {is}
    </kbd>
  );
}

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
  /** Measured rather than assumed: this bar is two rows tall on a phone. */
  const barRef = useRef<HTMLDivElement | null>(null);

  /**
   * FOLLOW THE READING. A narration that scrolls off the top of the window is
   * a podcast, and the reader has lost the one thing this offers over one:
   * seeing which sentence the archive is standing on.
   *
   * INTO THE BAND THEY CAN SEE, not into the middle of the window. This was
   * `scrollIntoView({block: "center"})`, which centres in the VIEWPORT - and
   * the viewport is not what is visible here. This bar is stuck to the top of
   * it and the player takes the foot of it, and on a 812px phone showing a
   * recording those two leave 227 pixels between them. A sentence centred in
   * the window at 406 began below the middle of that band and ran on behind
   * the player, so the reader was being shown the top half of what was being
   * read to them.
   */
  const place = useCallback(() => {
    if (!running || n.at < 0) return;
    const el = document.getElementById(`say-${n.step?.part ?? -1}`);
    if (!el) return;
    /* WHERE THE BAR SITS ONCE STUCK, not where it happens to be. Measuring
       its current rect looks right and is wrong in the one case that matters:
       before the page has scrolled down to the answer the bar is still far
       below the fold, the band comes out negative, and the reading never gets
       scrolled to at all. Sticky elements report their offset in `top`, so
       this is the bar's own contract with the page rather than a guess. */
    const bar = barRef.current;
    const top = bar
      ? (parseFloat(getComputedStyle(bar).top) || 0) + bar.offsetHeight
      : 0;
    /* What the player says it is standing in front of. Zero when it is a lane
       beside the page rather than a sheet across the foot of it, which is
       exactly right: a lane obstructs width, and `--dock-lane` is how the
       page is already told about that. */
    const room =
      parseFloat(
        getComputedStyle(document.documentElement).getPropertyValue("--dock-h"),
      ) || 0;
    const band = window.innerHeight - room - top;
    if (band <= 0) return;
    const r = el.getBoundingClientRect();
    /* Centred in the band while it fits. Against the top of it when it does
       not, because a passage too tall to show at once is read from its
       beginning - centring one of those hides its first line and its last. */
    const want = r.height < band ? top + (band - r.height) / 2 : top + PEEK;
    const by = Math.round(r.top - want);
    if (Math.abs(by) < 4) return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollBy({ top: by, behavior: still ? "auto" : "smooth" });
  }, [running, n.at, n.step?.part]);

  useEffect(() => {
    place();
  }, [place]);

  /**
   * AND AGAIN WHENEVER THE BAND MOVES.
   *
   * Placing the reading once, when the step changes, was placing it against a
   * window whose shape was about to change: the player opens, its caption
   * strip arrives from a fetch a moment later, and the room it stands in
   * front of goes from 257 pixels to 393. The sentence had been centred
   * perfectly in a band that no longer existed, and its last two lines were
   * behind the player.
   *
   * The dock publishes that room as `--dock-h` on the root element, so
   * watching the root's style attribute is watching the obstruction itself
   * rather than guessing at what might have moved it - and a resize covers
   * the other way it changes, which is the reader turning the phone over.
   */
  useEffect(() => {
    if (!running) return;
    const root = document.documentElement;
    const mo = new MutationObserver(place);
    mo.observe(root, { attributes: true, attributeFilter: ["style"] });
    window.addEventListener("resize", place);
    return () => {
      mo.disconnect();
      window.removeEventListener("resize", place);
    };
  }, [running, place]);

  /**
   * The keys, and NOT ONE THE PAGE ALREADY USES TO MOVE.
   *
   * This is the third mapping and the first two both got it wrong in the same
   * way. The first gave the narration its own set and left the player's
   * `j k l` live underneath, so two vocabularies were aimed at one transport.
   * The second put the transport on the arrow keys, which was worse: the
   * arrows and the space bar are how a document is scrolled, and a reader who
   * wants to look ahead while the voice talks is doing something entirely
   * reasonable that the page had quietly stopped letting them do. That the
   * narration scrolls itself is not an argument for taking them - it is the
   * reason a reader would want them back.
   *
   * So this takes letters and `esc`, and nothing else. Arrows, space, page up
   * and down, home and end all still belong to the page, running or not.
   *
   * The letters are the ones the dock already taught - `k` holds, `j` and `l`
   * move ten seconds - extended by two that say what they do: `n` for the
   * next sentence and `p` for the previous one.
   *
   * CAPTURE, and it stops the event dead. The dock has its own `j k l` on
   * this same window, and `k` there paused the video WITHOUT telling the
   * narration, which left the bar describing a recording that was not
   * running. While a narration runs it is the only thing steering, and the
   * dock's keys go back to being the dock's the moment it stops.
   *
   * A FOCUSED BUTTON KEEPS ITS OWN KEYS. Pressing a control with the mouse
   * leaves it focused, and a shortcut that fired on top of the button's own
   * activation would run the same action twice.
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
        /* IMMEDIATE, because the dock's own `j k l` are on this same window.
         * Plain `stopPropagation` separates them only by phase - this listens
         * in the capture phase and the dock's listens in the bubble one - and
         * that distinction disappears whenever `window` is itself the event's
         * target, where every listener on it runs at AT_TARGET regardless of
         * the phase it registered for. Then both handlers fire and `k` both
         * holds the narration and starts the recording under it. */
        e.stopImmediatePropagation();
        e.stopPropagation();
      };
      if (e.key === KEYS.hold) { take(); n.togglePause(); }
      else if (e.key === KEYS.on) { take(); n.skip(); }
      else if (e.key === KEYS.back) { take(); n.back(); }
      else if (e.key === "Escape") { take(); n.stop(); }
      /* SEEKING ONLY WHERE THERE IS A TIMELINE. While the voice is reading
         there is nothing to move along, so these are left to the dock, whose
         own `j` and `l` behave the same way they do on every other page. */
      else if (n.phase === "listening" && e.key === KEYS.rew) {
        take();
        player.seek(player.position - SEEK);
      } else if (n.phase === "listening" && e.key === KEYS.ff) {
        take();
        player.seek(player.position + SEEK);
      }
    }
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [running, n, player]);

  /**
   * WHAT AN ASSISTIVE VOICE IS TOLD, which is much less than what is on
   * screen.
   *
   * The line naming what is playing used to BE the live region, and it
   * changes on every step - so a screen reader, or macOS's Speak
   * Announcements with no screen reader at all, read the whole reference out
   * loud every time a recording started: "Playing the recording, unidentified
   * speaker, January 24 2023 morning, 41:10 to 41:55, the citation runs on
   * past this" - on top of the recording, which had just begun saying the
   * same thing in the speaker's own voice.
   *
   * THE RULE IS THAT THIS SPEAKS ONLY WHEN NOTHING ELSE IS. Reading aloud and
   * playing a recording both announce themselves, in the most direct way
   * there is: sound is coming out. Talking over either one to say that sound
   * is coming out is worse than saying nothing. What is left is the states
   * with no sound of their own - a hold, a stop, and anything that went
   * wrong - and those are exactly the ones a reader cannot otherwise tell
   * apart, because all three are silence.
   *
   * The line on screen keeps every word of its detail. It is still there to
   * be read by anyone who goes looking for it; it just no longer shouts.
   */
  const announce = n.trouble
    ? n.trouble
    : n.paused
      ? "Paused."
      : "";

  if (!n.supported) return null;

  return (
    <div
      className={s.bar}
      ref={barRef}
      data-running={running || undefined}
      /* WHETHER THE PLAYER IS SAYING ALL THIS ALREADY. With the picture open
         the strip under it names the speaker and the transport carries the
         clock, so on a phone this bar was the third place to print them - and
         the third place cost 80 pixels of the answer. Collapsed, the strip is
         not rendered and this is the only place left, so it stays. */
      data-captioned={player.expanded && player.source ? "" : undefined}
    >
      <p className="sr-only" aria-live="polite">
        {announce}
      </p>
      {running ? (
        <>
          {/* Transport order, so the pair either side of pause reads as one
              thing: back a sentence, hold, on to the next. */}
          <button type="button" className={s.step} onClick={n.back}
                  aria-label="Back one sentence" aria-keyshortcuts={KEYS.back}
                  title="Back one sentence. Onto a recording, plays it again.">
            <span aria-hidden>◀</span>
            <Key is={KEYS.back} />
          </button>
          <button type="button" className={s.key} onClick={n.togglePause}
                  aria-keyshortcuts={KEYS.hold}
                  title={n.paused ? "Resume" : "Pause"}>
            <span aria-hidden>{n.paused ? "▶" : "▮▮"}</span>
            {n.paused ? "Resume" : "Pause"}
            <Key is={KEYS.hold} />
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
                        aria-keyshortcuts={KEYS.rew}
                        title={`Back ${SEEK} seconds in the recording`}>
                  −{SEEK}s
                  <Key is={KEYS.rew} />
                </button>
                <button type="button" className={s.nudge}
                        onClick={() => player.seek(player.position + SEEK)}
                        aria-label={`Forward ${SEEK} seconds in the recording`}
                        aria-keyshortcuts={KEYS.ff}
                        title={`Forward ${SEEK} seconds in the recording`}>
                  +{SEEK}s
                  <Key is={KEYS.ff} />
                </button>
              </span>
            ) : null}
          {/* NOT A LIVE REGION. See `announce` above for why. */}
          <p className={s.doing}>
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
                <span className={s.who}>{n.step.what}</span>
                {/* OUTSIDE `.who`, which a phone hides. Never silently: the
                    citation is longer than this clip, and the reader is owed
                    that before they conclude they have heard it all. Its
                    marker in the prose plays the whole stretch. */}
                {n.step.trimmed ? (
                  <span className={s.part}>the citation runs on past this</span>
                ) : null}
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
                  aria-keyshortcuts={KEYS.on}
                  title={n.phase === "listening"
                    ? "Stop the recording and read on"
                    : "On to the next sentence"}>
            {n.phase === "listening" ? "Heard enough" : "Skip"}
            <Key is={KEYS.on} />
          </button>
          <button type="button" className={`${s.minor} ${s.stop}`} onClick={n.stop}
                  aria-keyshortcuts="Escape" title="Stop reading">
            Stop
            <Key is="esc" />
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
                {/* Worth a line on a machine that has a keyboard, noise on one
                    that does not. Once it is running every control prints its
                    own key, so this only has to get a reader as far as
                    pressing play. */}
                <span className={s.forKeys}>
                  Each control carries the key it answers to:{" "}
                  <kbd>{KEYS.hold}</kbd> holds it, <kbd>{KEYS.back}</kbd> and{" "}
                  <kbd>{KEYS.on}</kbd> step a sentence, <kbd>esc</kbd> stops,
                  and <kbd>{KEYS.rew}</kbd> and <kbd>{KEYS.ff}</kbd> move ten
                  seconds inside a recording. Nothing the page scrolls with is
                  taken.
                </span>
              </span>
            </>
          )}
        </p>
      )}
    </div>
  );
}
