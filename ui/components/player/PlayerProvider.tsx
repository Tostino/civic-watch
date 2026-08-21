"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { PlayerDock } from "./PlayerDock";

/**
 * One player for the whole app.
 *
 * The old pages implemented the YouTube embed four times and each one
 * remounted on every render, so a seek restarted the video. Here the iframe is
 * created once, mounted in the root layout, and never unmounted; everything
 * else - pages, transcripts, spines - talks to it through this context by
 * `(video_id, seconds)`, which is the only address a moment in this archive
 * has.
 *
 * D2 accepts YouTube embeds with their risk: we do not control these videos.
 * So `failed` is part of the public state, and the surfaces that use it are
 * required to degrade to the transcript and the published record - both held
 * locally - rather than to a broken frame.
 */

type YTPlayer = {
  playVideo(): void;
  pauseVideo(): void;
  seekTo(seconds: number, allowSeekAhead: boolean): void;
  getCurrentTime(): number;
  getDuration(): number;
  loadVideoById(o: { videoId: string; startSeconds?: number }): void;
  cueVideoById(o: { videoId: string; startSeconds?: number }): void;
  destroy(): void;
};

declare global {
  interface Window {
    YT?: {
      Player: new (el: HTMLElement | string, opts: Record<string, unknown>) => YTPlayer;
      PlayerState: { PLAYING: number; PAUSED: number; ENDED: number; BUFFERING: number };
    };
    onYouTubeIframeAPIReady?: () => void;
  }
}

/**
 * One armed stop point, and what became of it.
 *
 * `until` alone can say WHERE playback stops. It cannot say WHY it stopped,
 * and something reading an answer aloud has to know the difference: a clip
 * that ran to its end is a cue to carry on reading, and a clip the reader
 * interrupted is a cue to get out of the way. Both leave the player paused
 * with nothing armed, so both look identical from the outside.
 *
 * `token` is what makes it safe to act on. It is minted per arming, so a
 * narrator can tell the segment IT started from one the reader started by
 * clicking a different citation halfway through - which is the moment the
 * narration must stand down rather than fight the reader for the player.
 */
export interface Segment {
  token: number;
  /** Where the cited stretch begins. */
  from: number;
  /** Where it stops, in seconds. */
  at: number;
  state: "armed" | "reached" | "cancelled";
}

export interface Source {
  videoId: string;
  /** What is playing, for the dock's own label. */
  title: string;
  /** Where it came from, so the dock can link back. */
  href?: string;
  duration?: number;
}

interface PlayerState {
  source: Source | null;
  /** Current position in seconds. Updates ~4x/sec while playing. */
  position: number;
  duration: number;
  playing: boolean;
  ready: boolean;
  failed: boolean;
  expanded: boolean;
  /**
   * Where playback stops on its own, in seconds, or null for "play on".
   *
   * A CITATION IS A STRETCH, not a moment. Every passage in this archive has
   * a start and an end, and a reader sent to 1:57:52 to hear a two-minute
   * exchange has, up to now, been left running into the next forty minutes of
   * an unrelated agenda item. Armed by whoever opened the recording, cleared
   * the moment it fires or the moment the reader takes the wheel: this is the
   * citation's claim about where its evidence ends, not a lock on the player.
   *
   * Derived from `segment` and kept because most callers only want the
   * number: the dock draws it on the bar and never asks how it ended.
   */
  until: number | null;
  /** The stop point WITH its outcome. See Segment. */
  segment: Segment | null;
  /**
   * Called when an armed stop point ENDS, whichever way it ended. Returns
   * its own unsubscribe.
   *
   * A callback rather than a state to watch, because the caller that needs
   * this is waiting on the player the way it would wait on any other
   * external system: the interval below notices the recording has reached
   * the mark and says so. Watching `segment` from an effect would work and
   * would be the wrong shape - React would be re-rendering a component in
   * order to discover something that already happened, and the discovery
   * has to be exact. A missed transition is a narration that never
   * continues.
   */
  onSegmentEnd(cb: (segment: Segment) => void): () => void;
  /** Load a video and jump to a point in it. The one entry point.
   *  `autoplay: false` loads and holds - used when restoring a shared link,
   *  where starting audio unbidden is hostile (and blocked anyway).
   *  `until` stops it again at the end of what was cited.
   *
   *  Returns the token of the segment it armed, or null if it armed none.
   *  A caller that intends to WAIT for the clip needs this: `segment` is
   *  state and is therefore a render behind, so reading it back here would
   *  hand out the previous clip's token and the narration would advance on
   *  the wrong one. */
  play(source: Source, seconds?: number, autoplay?: boolean, until?: number | null): number | null;
  /** Move within whatever is already loaded. Clears any stop point: a reader
   *  who scrubs has left the cited stretch behind, whichever way they went. */
  seek(seconds: number): void;
  toggle(): void;
  close(): void;
  setExpanded(v: boolean): void;
  /** Re-attempt after a failure. YouTube is someone else's service. */
  retry(): void;
}

const Ctx = createContext<PlayerState | null>(null);

export const usePlayer = (): PlayerState => {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("usePlayer must be used inside <PlayerProvider>");
  return ctx;
};

/** Position updates would re-render every transcript row 4x a second. Anything
 *  that only needs to *follow* the playhead subscribes to this instead. */
export const usePlayhead = (): {
  videoId: string | null;
  position: number;
  playing: boolean;
  until: number | null;
} => {
  const p = usePlayer();
  return {
    videoId: p.source?.videoId ?? null,
    position: p.position,
    playing: p.playing,
    until: p.until,
  };
};

let apiPromise: Promise<void> | null = null;

function loadApi(): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  if (window.YT?.Player) return Promise.resolve();
  if (apiPromise) return apiPromise;
  apiPromise = new Promise<void>((resolve, reject) => {
    const prior = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => {
      prior?.();
      resolve();
    };
    const tag = document.createElement("script");
    tag.src = "https://www.youtube.com/iframe_api";
    tag.async = true;
    tag.onerror = () => reject(new Error("YouTube IFrame API failed to load"));
    document.head.appendChild(tag);
  });
  /* A cached REJECTION is permanent: one dropped connection to YouTube - which
   * does happen - would otherwise leave the player dead for the rest of the
   * session, every retry resolving instantly to the same stale failure.
   * Forgetting the failed attempt is what makes "try again" mean anything. */
  apiPromise.catch(() => {
    apiPromise = null;
  });
  return apiPromise;
}

export function PlayerProvider({ children }: { children: React.ReactNode }) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const playerRef = useRef<YTPlayer | null>(null);
  /** A play() that arrived before the API finished loading. */
  const pendingRef = useRef<{ source: Source; seconds: number; autoplay: boolean } | null>(null);

  const [source, setSource] = useState<Source | null>(null);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [expanded, setExpanded] = useState(true);
  const [segment, setSegment] = useState<Segment | null>(null);
  /* The poll below reads the stop point 4x a second and must not be the reason
   * the interval tears down and rebuilds. State is what the dock and the
   * narrator read; this is what the loop reads. */
  const segRef = useRef<Segment | null>(null);
  const tokenRef = useRef(0);
  /**
   * Whether the playhead has actually reached the stretch this segment is
   * about.
   *
   * A STOP POINT IS NOT LIVE UNTIL THE SEEK LANDS. Arming happens first,
   * synchronously; the seek is a message to an iframe, and `getCurrentTime()`
   * keeps reporting the old position for a tick or two afterwards. Playing a
   * clip at 1:40 while the recording sat at 33:20 therefore armed a stop at
   * 1:50, polled, saw 33:20, decided the stop had been passed, and ended the
   * clip before a word of it played. Anything waiting on that end - the
   * narration below all of this - saw a whole quote go by in 250ms.
   *
   * So the poll waits to see the head INSIDE the stretch before it will
   * accept that the stretch is over.
   */
  const entered = useRef(false);
  /* Told when a stop point ends. A Set in a ref: subscribing must not
   * re-render the provider, which owns the iframe every page shares. */
  const ended = useRef(new Set<(segment: Segment) => void>());
  const onSegmentEnd = useCallback((cb: (segment: Segment) => void) => {
    const set = ended.current;
    set.add(cb);
    return () => {
      set.delete(cb);
    };
  }, []);
  /* Only an ARMED segment can end, and it ends once. Everything that takes
   * the player somewhere else calls this, so there is exactly one place that
   * decides a stop point is over and exactly one that says why. */
  const disarm = useCallback((state: "reached" | "cancelled") => {
    const cur = segRef.current;
    if (!cur || cur.state !== "armed") return;
    const next: Segment = { ...cur, state };
    segRef.current = next;
    setSegment(next);
    for (const cb of ended.current) cb(next);
  }, []);

  const arm = useCallback((from: number, at: number) => {
    /* A stop point that is REPLACED has ended, and has to say so. Without
     * this, clicking a second citation while the first was still playing
     * left anything waiting on the first waiting for ever: its segment was
     * not cancelled, it was simply no longer the one the player held. */
    disarm("cancelled");
    const next: Segment = { token: ++tokenRef.current, from, at, state: "armed" };
    segRef.current = next;
    entered.current = false;
    setSegment(next);
    return next.token;
  }, [disarm]);
  const until = segment?.state === "armed" ? segment.at : null;
  /** Bumped by retry(); re-runs the setup effect below. */
  const [attempt, setAttempt] = useState(0);

  // Created once per attempt. Not tied to `source`, so switching recordings
  // reuses the same iframe and navigation never tears it down.
  useEffect(() => {
    let cancelled = false;
    loadApi()
      .then(() => {
        if (cancelled || !hostRef.current || playerRef.current || !window.YT) return;
        playerRef.current = new window.YT.Player(hostRef.current, {
          host: "https://www.youtube-nocookie.com",
          playerVars: { rel: 0, modestbranding: 1, playsinline: 1 },
          events: {
            onReady: () => {
              if (cancelled) return;
              setReady(true);
              const p = pendingRef.current;
              if (p && playerRef.current) {
                const load = p.autoplay
                  ? playerRef.current.loadVideoById
                  : playerRef.current.cueVideoById;
                load.call(playerRef.current, {
                  videoId: p.source.videoId,
                  startSeconds: p.seconds,
                });
                pendingRef.current = null;
              }
            },
            onStateChange: (e: { data: number }) => {
              if (cancelled || !window.YT) return;
              setPlaying(e.data === window.YT.PlayerState.PLAYING);
              const d = playerRef.current?.getDuration?.() ?? 0;
              if (d) setDuration(d);
            },
            onError: () => !cancelled && setFailed(true),
          },
        });
      })
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  // Poll while playing. The YouTube API has no timeupdate event, and this is
  // what the transcript follows, and what stops it at the end of a citation.
  useEffect(() => {
    if (!playing) return;
    const id = window.setInterval(() => {
      const p = playerRef.current;
      if (!p) return;
      const at = p.getCurrentTime();
      setPosition(at);
      const d = p.getDuration();
      if (d) setDuration(d);
      const seg = segRef.current?.state === "armed" ? segRef.current : null;
      /* PAUSE, never close. The dock is the reader's place in a six-hour
       * recording: closing it at the end of a quote would take the archive
       * away from somebody who had just been persuaded to look at it, and
       * leave them with no way to hear the sentence again. Pausing leaves the
       * picture, the clock and the transport where they were, one keypress
       * from playing on past the quote - which is exactly what a reader who
       * has heard the end of an argument most often wants to do.
       *
       * Disarmed as it fires: the next press of play is a decision to keep
       * going, and a stop point that re-fired would make that press do
       * nothing at all. */
      if (seg) {
        // A second of slack in front, because a seek lands near the mark
        // rather than on it: YouTube snaps to a keyframe.
        if (!entered.current) entered.current = at >= seg.from - 1 && at < seg.at;
        else if (at >= seg.at) {
          disarm("reached");
          p.pauseVideo();
        }
      }
    }, 250);
    return () => window.clearInterval(id);
  }, [playing, disarm]);

  const play = useCallback((next: Source, seconds = 0, autoplay = true, until: number | null = null) => {
    setSource((cur) => (cur?.videoId === next.videoId ? cur : next));
    setPosition(seconds);
    /* Armed before anything is loaded, so the poll has it whichever branch
     * below runs - including the pending one, where the API has not finished
     * loading and the stop point has to outlive the wait. An ordinary play()
     * passes nothing and therefore DISARMS: seeking from a cited stretch to
     * an uncited one must not inherit the citation's ending. */
    const armed = until != null && until > seconds ? arm(seconds, until) : null;
    if (armed == null) disarm("cancelled");
    /* Opening the player shows the picture. Moving within it does not: every
     * click on a transcript line comes through here, so a reader who had
     * collapsed the video to read got it reopened on top of them a few seconds
     * later, over and over. Collapsed is a decision about this session, not
     * about this seek. */
    if (!source) setExpanded(true);
    if (next.duration) setDuration(next.duration);
    const p = playerRef.current;
    if (!p) {
      pendingRef.current = { source: next, seconds, autoplay };
      return armed;
    }
    // Same video already loaded: seek rather than reload, or the video
    // restarts from the buffer and drops a second of audio for no reason.
    if (source?.videoId === next.videoId && ready) {
      p.seekTo(seconds, true);
      if (autoplay) p.playVideo();
      return armed;
    }
    if (autoplay) p.loadVideoById({ videoId: next.videoId, startSeconds: seconds });
    else p.cueVideoById({ videoId: next.videoId, startSeconds: seconds });
    return armed;
    // `source` rather than its id: setSource keeps the object identity when the
    // recording has not changed, so this is the same dependency, and it is also
    // what says whether anything is loaded at all.
  }, [arm, disarm, ready, source]);

  const seek = useCallback((seconds: number) => {
    // The reader has taken the wheel. Whatever a citation said about where
    // its evidence ended is no longer a statement about where they are.
    disarm("cancelled");
    playerRef.current?.seekTo(Math.max(0, seconds), true);
    setPosition(Math.max(0, seconds));
  }, [disarm]);

  const toggle = useCallback(() => {
    const p = playerRef.current;
    if (!p) return;
    if (playing) p.pauseVideo();
    else p.playVideo();
  }, [playing]);

  const close = useCallback(() => {
    playerRef.current?.pauseVideo();
    setSource(null);
    setPlaying(false);
    setPosition(0);
    disarm("cancelled");
  }, [disarm]);

  const retry = useCallback(() => {
    playerRef.current = null;
    setFailed(false);
    setReady(false);
    // Whatever was loaded should come back at the same moment, not from zero.
    if (source) pendingRef.current = { source, seconds: position, autoplay: false };
    setAttempt((n) => n + 1);
  }, [source, position]);

  const value = useMemo<PlayerState>(
    () => ({
      source,
      position,
      duration,
      playing,
      ready,
      failed,
      expanded,
      until,
      segment,
      onSegmentEnd,
      play,
      seek,
      toggle,
      close,
      setExpanded,
      retry,
    }),
    [source, position, duration, playing, ready, failed, expanded, until, segment,
     onSegmentEnd, play, seek, toggle, close, retry],
  );

  return (
    <Ctx.Provider value={value}>
      {children}
      {/* The dock is the iframe's permanent home. It is always in the tree -
          moving the iframe between containers would remount it, which is
          exactly what the design forbids - and collapsing only changes its size. */}
      <PlayerDock hostRef={hostRef} />
    </Ctx.Provider>
  );
}
