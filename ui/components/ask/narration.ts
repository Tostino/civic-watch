"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { usePlayer } from "@/components/player/PlayerProvider";

/**
 * Reading an answer aloud, with the archive's own voices in it.
 *
 * THE POINT IS THE SPLICE, not the speech. A synthesised voice reading a
 * paragraph is a convenience; a synthesised voice that stops mid-argument,
 * plays nineteen seconds of a resident actually saying the thing, and then
 * carries on, is the archive making its own case. Everything here exists to
 * make that seam land in the right place and not drift.
 *
 * It is possible at all because the two halves already line up. The prose is
 * parsed into text and citations for rendering, and a citation already
 * resolves to a stretch of recording with both edges known - so the running
 * order is just those parts, in the order they were written.
 *
 * WHAT THIS DOES NOT DO: read the evidence quotes underneath. Those are the
 * same words as the clips, and a narration that read them would say
 * everything twice.
 */

/** One thing to do, in the order the answer was written. */
export type Step =
  | { kind: "say"; part: number; text: string }
  | {
      kind: "hear";
      part: number;
      /** The reference number, so a repeat can be recognised and skipped. */
      n: number;
      videoId: string;
      title: string;
      href?: string;
      from: number;
      to: number;
      /**
       * The citation runs on past where this clip stops.
       *
       * SAID OUT LOUD rather than trimmed quietly. A narration that plays 45
       * seconds of a two-minute exchange and presents it as the citation is
       * telling the reader they have heard the evidence when they have heard
       * the start of it. The marker in the prose still plays the whole thing.
       */
      trimmed: boolean;
      /** Who and when, for the line that says what is playing. */
      what: string;
    };

/**
 * CHUNK LENGTH, in characters, and it decides three things at once.
 *
 * It is what the archive renders per request, so it bounds how long the first
 * word takes to arrive - about a second and a half at this length, cold, and
 * nothing at all once the next chunk is being fetched while the current one
 * plays. It is what the page highlights, so it is the granularity at which a
 * reader can see where the reading has got to. And it is the cache key, so
 * two answers that share a sentence share its audio.
 *
 * A sentence is the natural unit for all three, and 180 characters is the
 * longest sentence this archive's answers actually write. Anything longer
 * gets cut at a clause boundary rather than becoming one slow request whose
 * highlight covers half a paragraph.
 */
const CHUNK = 180;

/** Something worth saying out loud: a run of whitespace is not. */
const SPEAKABLE = /[\p{L}\p{N}]/u;

/**
 * Full stops that end a word rather than a sentence.
 *
 * Every one of these is something county business says constantly, and each
 * was breaking a chunk in the middle of a phrase: "Case No." then "24-118",
 * "Fla." then "Stat." then "163.3184". The voice put a full stop in the
 * middle of a statute number, which sounds like two facts instead of one.
 *
 * A capitalised word after a full stop is the usual test for a sentence
 * boundary and it fails on all of these, because the next word is a proper
 * noun or a number either way.
 */
const NOT_A_STOP = new Set([
  "no", "nos", "fla", "stat", "sec", "ch", "art", "ord", "res", "dept", "div",
  "st", "rd", "ave", "blvd", "ln", "ct", "hwy", "ste", "apt", "pkwy",
  "mr", "mrs", "ms", "dr", "jr", "sr", "st.", "rev", "hon",
  "inc", "corp", "co", "llc", "ltd", "assn", "est",
  "approx", "etc", "vs", "v", "al", "cf", "eg", "ie", "am", "pm", "figs", "fig",
  "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sept", "sep", "oct", "nov", "dec",
]);

/** Whether the full stop at `i` is part of a word rather than the end of one. */
function abbreviation(text: string, i: number): boolean {
  if (text[i] !== ".") return false;
  let a = i;
  while (a > 0 && /[\p{L}]/u.test(text[a - 1])) a--;
  const word = text.slice(a, i);
  // A single letter is an initial - "J. Smith", and the tail of "U.S." and
  // "a.m." once their earlier stops have been skipped for the same reason.
  return word.length === 1 || NOT_A_STOP.has(word.toLowerCase());
}

/**
 * Prose to chunks, losslessly.
 *
 * LOSSLESS IS THE CONTRACT: the page renders these same chunks back as spans
 * so that the sentence being spoken can be shown, and `white-space: pre-wrap`
 * means a swallowed newline is a lost paragraph break. Concatenating the
 * result must give back exactly what went in.
 *
 * The split is by sentence, and deliberately conservative about what ends
 * one. A full stop followed by a lowercase letter is a decimal point or an
 * abbreviation ("Fla. Stat.", "3.5 acres", "No. 24-118"), all of which turn
 * up constantly in county business, and breaking there would put a hard stop
 * in the middle of a figure.
 */
export function splitSentences(text: string): string[] {
  const out: string[] = [];
  let start = 0;

  const cut = (end: number) => {
    if (end <= start) return;
    for (const piece of clauses(text.slice(start, end))) out.push(piece);
    start = end;
  };

  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    // A blank line is a paragraph, and a paragraph is always its own chunk.
    if (c === "\n") {
      let j = i + 1;
      while (j < text.length && /\s/.test(text[j])) j++;
      cut(j);
      i = j - 1;
      continue;
    }
    if (c !== "." && c !== "!" && c !== "?") continue;
    if (abbreviation(text, i)) continue;
    // Closing punctuation belongs to the sentence it closes: `said it."` ends
    // after the quote mark, not before it.
    let j = i + 1;
    while (j < text.length && /[.!?)\]"'’”…]/.test(text[j])) j++;
    if (j < text.length && !/\s/.test(text[j])) continue;
    const next = /^\s*(\S)/.exec(text.slice(j));
    // A lowercase letter after a full stop is a decimal point or an
    // abbreviation this list has not learned yet. A DIGIT is the number the
    // abbreviation in front of it was introducing: "Ordinance No. 23-15".
    if (next && /[a-z0-9]/.test(next[1])) continue;
    cut(j);
    i = j - 1;
  }
  cut(text.length);
  return out;
}

/** A sentence too long for one utterance, cut at the best seam available. */
function clauses(text: string): string[] {
  if (text.length <= CHUNK) return [text];
  const out: string[] = [];
  let rest = text;
  while (rest.length > CHUNK) {
    const head = rest.slice(0, CHUNK);
    // A comma or a colon is a place the voice would pause anyway. A bare
    // space is the fallback, and only a word with no seam in it at all - a
    // URL, usually - gets cut mid-word.
    const seam = Math.max(head.lastIndexOf(", "), head.lastIndexOf("; "),
                          head.lastIndexOf(": "));
    const at = seam > CHUNK / 2 ? seam + 2 : (head.lastIndexOf(" ") + 1 || CHUNK);
    out.push(rest.slice(0, at));
    rest = rest.slice(at);
  }
  if (rest) out.push(rest);
  return out;
}

/** Whether a chunk has anything in it to say. */
export const speakable = (text: string) => SPEAKABLE.test(text);

/* ------------------------------------------------------------------ voice */

/**
 * THE ARCHIVE READS ITSELF. See web/say.py for the model and why it is local.
 *
 * This used to be the browser's own `speechSynthesis`, which is free and
 * everywhere and, on an ordinary Linux desktop, espeak. Nobody follows a
 * ninety-second argument in that voice, and a narration nobody follows is not
 * a feature. So the audio comes from the archive now, one chunk at a time.
 *
 * WHAT THE SERVER WILL VOICE is only text already inside the answer being
 * read: `answer` goes with every request and web/server.py checks the chunk
 * against it. That is what stops this being a free text-to-speech service on
 * a public host, and it is why the narration needs a SAVED answer - an
 * unsaved run has nothing to check against and the control does not offer
 * itself.
 */
const SAY = "/api/say";

/** Rendered chunks, as object URLs, for this page's lifetime. Re-listening,
 *  and stepping back over a sentence after a pause, cost nothing. */
type Rendered = Map<string, Promise<string>>;

async function fetchChunk(answer: string, text: string, signal: AbortSignal) {
  const res = await fetch(SAY, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answer, text }),
    signal,
  });
  if (!res.ok) {
    /* The server's own sentence, not a status code. Every refusal on this
     * endpoint is written to be read by a person - the rate limiter says how
     * long to wait, the missing model says the archive has no voice loaded -
     * and inventing a replacement here would throw that away. */
    let why = `The archive could not read that aloud (${res.status}).`;
    try {
      const body = await res.json();
      if (body?.error) why = body.error as string;
    } catch {
      /* not JSON; the status sentence above stands */
    }
    throw new Error(why);
  }
  return URL.createObjectURL(await res.blob());
}

/**
 * HOW LONG A STEP MAY TAKE TO MAKE A SOUND before it is treated as stuck, in
 * milliseconds.
 *
 * NOTHING HERE HAD A DEADLINE, and two of the ways it waits can wait for
 * ever. Found by watching it in a browser rather than by reading it:
 *
 *  - A sentence is started with `HTMLAudioElement.play()`, whose promise
 *    simply never settles if the media cannot begin. Not an error, no
 *    rejection, no event - so the control sat on "Getting the voice"
 *    indefinitely with nothing to press but Stop.
 *  - A clip is waited on with the player's stop point, and that is announced
 *    only when the head REACHES it. A recording that never starts never
 *    arrives anywhere, so the bar went on saying "Playing the recording" over
 *    a stopped video, offering to pause something that was not running, and
 *    the narration behind it was wedged for good.
 *
 * Both are reachable with nothing broken: an embed the browser declines to
 * autoplay, a tab left in the background, a network that drops the video and
 * not the page. Generous, because the point is to catch a wait that will
 * never end rather than to hurry a slow one - a cold voice is well under two
 * seconds and a video that has not begun in twelve is not going to.
 */
const START_BY = { voice: 10_000, clip: 12_000 };

export type Phase = "off" | "fetching" | "reading" | "listening";

export interface Narration {
  /** Whether the control can render. Needs a saved answer to read from. */
  supported: boolean;
  /**
   * Why it stopped, in the words to show the reader, or null.
   *
   * SAID OUT LOUD rather than degraded. There is no browser voice to fall
   * back to by design, so a rate limit or an unloaded model is the end of the
   * narration, and a reader who pressed play is owed the reason rather than a
   * button that quietly does nothing.
   */
  trouble: string | null;
  phase: Phase;
  paused: boolean;
  /** Index into `steps`, or -1. The page highlights what this points at. */
  at: number;
  step: Step | null;
  start(): void;
  stop(): void;
  /** Past the current step. On a clip, that means "I have heard enough". */
  skip(): void;
  /** Back one step. Onto a clip, that means play it again. */
  back(): void;
  /**
   * Read from a PART of the answer - an index into what the page renders,
   * not into the running order. That is what a reader points at.
   */
  readFrom(part: number): void;
  togglePause(): void;
}

/**
 * The running order, performed.
 *
 * A state machine with two ways to wait: on an audio element ending, and on
 * the player reaching the stop point a citation armed. Both are callbacks
 * from someone else's code arriving whenever they arrive, so every one of
 * them is stamped with the run it belongs to and dropped if the reader has
 * since stopped, skipped, or started again. Without that, pressing stop and
 * then start leaves two narrations racing each other through the same answer.
 */
export function useNarration(steps: Step[], answer: string | undefined): Narration {
  const player = usePlayer();
  const [phase, setPhase] = useState<Phase>("off");
  const [paused, setPaused] = useState(false);
  const [trouble, setTrouble] = useState<string | null>(null);
  const [at, setAt] = useState(-1);

  /** Bumped by anything that invalidates callbacks already in flight. */
  const run = useRef(0);
  const atRef = useRef(-1);
  /**
   * The player segment this narration armed, and may therefore act on.
   *
   * NON-NULL MEANS WAITING ON A CLIP. That is the whole of the listening
   * state as far as the player's callback is concerned, which is why the
   * callback below does not read `phase`: a ref is current the instant it is
   * set, and `phase` is a render behind.
   */
  const token = useRef<number | null>(null);

  /** One element, reused. A new Audio per sentence leaves the finished ones
   *  to the garbage collector holding decoded audio, and Safari keeps them. */
  const sound = useRef<HTMLAudioElement | null>(null);
  const rendered = useRef<Rendered>(new Map());
  const abort = useRef<AbortController | null>(null);

  const shush = useCallback(() => {
    run.current += 1;
    abort.current?.abort();
    abort.current = null;
    const a = sound.current;
    if (a) {
      a.pause();
      a.removeAttribute("src");
      a.load();
    }
  }, []);

  // Leaving the page must not leave a voice reading to an empty room, and
  // must not leave its audio in memory either.
  useEffect(() => {
    const cache = rendered.current;
    return () => {
      run.current += 1;
      abort.current?.abort();
      sound.current?.pause();
      for (const url of cache.values()) url.then(URL.revokeObjectURL).catch(() => {});
      cache.clear();
    };
  }, []);

  /** Render a chunk, or hand back the one already rendered. */
  const chunk = useCallback(
    (text: string) => {
      const cache = rendered.current;
      const had = cache.get(text);
      if (had) return had;
      if (!abort.current) abort.current = new AbortController();
      const job = fetchChunk(answer ?? "", text, abort.current.signal);
      cache.set(text, job);
      /* A FAILED render must not be remembered as a render. Left in the map,
       * a rejected promise makes every later attempt at that sentence fail
       * instantly with a stale reason - including the retry the reader makes
       * by pressing play again. */
      job.catch(() => cache.delete(text));
      return job;
    },
    [answer],
  );

  /**
   * Render whatever is spoken NEXT, whether or not it is the next step.
   *
   * "Next step" was not enough. A clip sits between two sentences, so the
   * sentence after a clip was never warmed and every citation in a narration
   * was followed by a two-second silence with the control saying "Getting
   * the voice" - which is the seam this whole feature exists to make
   * seamless, and a clip is fifteen to forty-five seconds of cover for a
   * request that takes one.
   */
  const warm = useCallback(
    (from: number) => {
      const next = steps.slice(from).find((st) => st.kind === "say");
      if (next?.kind === "say") chunk(next.text).catch(() => {});
    },
    [chunk, steps],
  );

  /**
   * Recursion through a ref, because a step's completion is what starts the
   * next one and a useCallback cannot name itself. Assigned in an effect
   * below, which is always before any callback can fire: nothing here runs
   * until a reader presses a button.
   */
  const performRef = useRef<(i: number) => void>(() => {});

  const halt = useCallback(() => {
    shush();
    token.current = null;
    atRef.current = -1;
    setPhase("off");
    setPaused(false);
    setAt(-1);
  }, [shush]);

  const perform = useCallback(
    (i: number) => {
      const mine = run.current;
      if (i >= steps.length) {
        halt();
        return;
      }
      atRef.current = i;
      setAt(i);
      const step = steps[i];

      if (step.kind === "say") {
        setPhase("fetching");
        chunk(step.text)
          .then((url) => {
            if (run.current !== mine) return;
            const a = sound.current ?? new Audio();
            sound.current = a;
            a.onended = () => {
              if (run.current === mine) performRef.current(i + 1);
            };
            a.onerror = () => {
              if (run.current !== mine) return;
              setTrouble("That sentence would not play in this browser.");
              halt();
            };
            a.src = url;
            return a.play().then(() => {
              if (run.current !== mine) return;
              setPhase("reading");
              /* THE NEXT SENTENCE IS FETCHED WHILE THIS ONE PLAYS, which is
                 the whole of the latency story: only the first chunk of a
                 narration is ever waited for, and after that the archive is
                 always a sentence ahead of the voice. */
              warm(i + 1);
            });
          })
          .catch((e: unknown) => {
            if (run.current !== mine) return;
            if (e instanceof DOMException && e.name === "AbortError") return;
            setTrouble(
              e instanceof DOMException && e.name === "NotAllowedError"
                ? "This browser blocked the audio. Press play again."
                : e instanceof Error
                  ? e.message
                  : "The archive could not read that aloud.",
            );
            halt();
          });
        return;
      }

      setPhase("listening");
      // Cover for the sentence on the far side of the clip.
      warm(i + 1);
      token.current = player.play(
        { videoId: step.videoId, title: step.title, href: step.href },
        step.from,
        true,
        step.to,
      );
      /* No token means nothing was armed - the stop point was behind the
       * start, which only happens if a passage's own times are inverted.
       * Rather than wait for an end that will never be announced, treat the
       * clip as heard and read on. */
      if (token.current == null) performRef.current(i + 1);
    },
    [chunk, halt, player, steps, warm],
  );

  useEffect(() => {
    performRef.current = perform;
  }, [perform]);

  /* The clip ended, or the reader ended it. Subscribed once: the player says
   * so, rather than this watching state and inferring it. */
  useEffect(
    () =>
      player.onSegmentEnd((seg) => {
        const mine = token.current;
        if (mine == null) return;
        if (seg.token !== mine || seg.state === "cancelled") {
          /* Either the reader clicked a different citation, or they scrubbed
           * out of this one. Both mean the same thing: they are driving now,
           * and a narration that carried on would be fighting them for the
           * player. */
          halt();
          return;
        }
        token.current = null;
        performRef.current(atRef.current + 1);
      }),
    [player, halt],
  );

  /**
   * THE BAR MUST NOT DISAGREE WITH THE PLAYER.
   *
   * A clip can be held from two places: this control, and the dock's own
   * button under the picture the reader is actually looking at. Only the
   * first ever told the narration, so pausing at the dock left the line above
   * the answer reading "Playing the recording" over a stopped video - and
   * then the next press of space, which the bar thought was a pause, played
   * it instead.
   *
   * MIRRORED ONLY ONCE THE CLIP HAS BEEN SEEN TO PLAY. The iframe reports
   * itself playing a few hundred milliseconds after it is told to, and a bar
   * that believed that gap flashed "Paused" at the top of every recording.
   */
  const rolling = useRef(false);
  useEffect(() => {
    if (phase !== "listening") {
      rolling.current = false;
      return;
    }
    if (player.playing) rolling.current = true;
    if (rolling.current) setPaused(!player.playing);
  }, [phase, player.playing]);

  const start = useCallback(() => {
    shush();
    token.current = null;
    setPaused(false);
    setTrouble(null);
    perform(0);
  }, [perform, shush]);

  /**
   * Move the reading to a step, from wherever it is - including from a stop.
   *
   * EVERY WAY OF MOVING GOES THROUGH HERE, because two things have to be
   * undone before the next step can start and it is easy to remember only
   * one: the audio in flight together with the callbacks that would fire
   * after it (`shush`, which invalidates the run they are stamped with), and
   * the clip the player is still holding. Skipping the second left a
   * recording talking underneath the voice.
   */
  const jump = useCallback(
    (i: number) => {
      const wasListening = token.current != null;
      shush();
      token.current = null;
      setPaused(false);
      setTrouble(null);
      /* Leaving the recording where the reader stopped listening, paused. The
       * alternative - closing it - takes away the one thing they might want
       * next, which is to hear the rest of what they just cut short. */
      if (wasListening && player.playing) player.toggle();
      perform(Math.max(0, i));
    },
    [perform, player, shush],
  );

  const skip = useCallback(() => {
    if (atRef.current < 0) return;
    jump(atRef.current + 1);
  }, [jump]);

  /** For the deadline below, which is declared before `skip` exists. */
  const skipRef = useRef<() => void>(() => {});
  useEffect(() => {
    skipRef.current = skip;
  }, [skip]);

  /**
   * Back one step, OVER CLIPS AS WELL AS SENTENCES - and stepping back into a
   * clip plays it again.
   *
   * That is not a case to be avoided. "Say that again" is the likeliest
   * reason anybody reaches for this at all, and a rule that stepped politely
   * over recordings would make the one thing worth re-hearing the one thing
   * there is no way back to.
   */
  const back = useCallback(() => {
    if (atRef.current < 0) return;
    jump(atRef.current - 1);
  }, [jump]);

  /**
   * Read from a part of the answer, whether or not it is reading now.
   *
   * A PART IS NOT A STEP. The page knows where a reader pointed as a position
   * in the prose, and plenty of parts have no step of their own: a citation
   * already heard earlier in the answer, a run of whitespace between two
   * paragraphs. So this takes the first step at or after the part, which is
   * the sentence a reader pointing at any of those meant.
   */
  const readFrom = useCallback(
    (part: number) => {
      const i = steps.findIndex((st) => st.part >= part);
      if (i < 0) return;
      jump(i);
    },
    [jump, steps],
  );

  /**
   * THE DEADLINE. See START_BY for the two ways this used to wait for ever.
   *
   * The two failures are treated differently, because they are different:
   *
   *  - THE VOICE NOT STARTING ENDS THE NARRATION. There is no second voice to
   *    fall back to - that is a decision, not an omission - so the honest
   *    thing is to say so and stop.
   *  - A RECORDING NOT STARTING DOES NOT END IT. The answer is still worth
   *    hearing, and the archive's argument should not be held hostage by one
   *    embed a browser would not play, so the reading carries on past the
   *    clip and says what it went without.
   *
   * A CLIP THE READER PAUSED IS NOT A CLIP THAT FAILED, which is what
   * `rolling` is doing here: once a recording has been seen to play, holding
   * it is the reader's business and no deadline applies to it.
   */
  useEffect(() => {
    if (paused) return;
    if (phase === "listening" && (player.playing || rolling.current)) return;
    if (phase !== "fetching" && phase !== "listening") return;
    const voice = phase === "fetching";
    const mine = run.current;
    const late = window.setTimeout(() => {
      if (run.current !== mine) return;
      if (voice) {
        setTrouble("The archive's voice would not start playing here. Nothing"
                   + " was lost - the answer is above, in full, to be read.");
        halt();
        return;
      }
      /* AFTER the skip, never before: moving on clears whatever the last
         attempt had to say, so a message set first would be wiped by the very
         step it was explaining. */
      skipRef.current();
      setTrouble("That recording would not start, so the reading carried on"
                 + " without it. Its citation still opens the whole stretch.");
    }, voice ? START_BY.voice : START_BY.clip);
    return () => window.clearTimeout(late);
  }, [phase, at, paused, player.playing, halt]);

  const togglePause = useCallback(() => {
    if (phase === "off") return;
    if (phase === "listening") {
      /* The clip is the player's to hold. Nothing here has to remember
       * anything: the stop point stays armed across a pause, so pressing play
       * again finishes the clip and the narration picks up from its end. */
      player.toggle();
      setPaused(player.playing);
      return;
    }
    const a = sound.current;
    if (paused) {
      setPaused(false);
      /* Resumes mid-sentence, where the browser voice could not. An audio
         element holds its position, so there is no reason to re-read a
         sentence the reader has already heard most of. */
      if (a && a.src && !a.ended) a.play().catch(() => perform(atRef.current));
      else perform(atRef.current);
      return;
    }
    a?.pause();
    setPaused(true);
  }, [phase, paused, perform, player]);

  const step = at >= 0 && at < steps.length ? steps[at] : null;
  const supported = Boolean(answer);

  return useMemo(
    () => ({ supported, trouble, phase, paused, at, step,
             start, stop: halt, skip, back, readFrom, togglePause }),
    [supported, trouble, phase, paused, at, step, start, halt, skip, back,
     readFrom, togglePause],
  );
}
