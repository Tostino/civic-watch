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
 * R6.1. One player for the whole app.
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
  /** Load a video and jump to a point in it. The one entry point (R6.1).
   *  `autoplay: false` loads and holds - used when restoring a shared link,
   *  where starting audio unbidden is hostile (and blocked anyway). */
  play(source: Source, seconds?: number, autoplay?: boolean): void;
  /** Move within whatever is already loaded. */
  seek(seconds: number): void;
  toggle(): void;
  close(): void;
  setExpanded(v: boolean): void;
  /** Re-attempt after a failure. YouTube is someone else's service (D2). */
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
export const usePlayhead = (): { videoId: string | null; position: number; playing: boolean } => {
  const p = usePlayer();
  return { videoId: p.source?.videoId ?? null, position: p.position, playing: p.playing };
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
  // what the transcript follows.
  useEffect(() => {
    if (!playing) return;
    const id = window.setInterval(() => {
      const p = playerRef.current;
      if (!p) return;
      setPosition(p.getCurrentTime());
      const d = p.getDuration();
      if (d) setDuration(d);
    }, 250);
    return () => window.clearInterval(id);
  }, [playing]);

  const play = useCallback((next: Source, seconds = 0, autoplay = true) => {
    setSource((cur) => (cur?.videoId === next.videoId ? cur : next));
    setPosition(seconds);
    setExpanded(true);
    if (next.duration) setDuration(next.duration);
    const p = playerRef.current;
    if (!p) {
      pendingRef.current = { source: next, seconds, autoplay };
      return;
    }
    // Same video already loaded: seek rather than reload, or the video
    // restarts from the buffer and drops a second of audio for no reason.
    if (source?.videoId === next.videoId && ready) {
      p.seekTo(seconds, true);
      if (autoplay) p.playVideo();
      return;
    }
    if (autoplay) p.loadVideoById({ videoId: next.videoId, startSeconds: seconds });
    else p.cueVideoById({ videoId: next.videoId, startSeconds: seconds });
  }, [ready, source?.videoId]);

  const seek = useCallback((seconds: number) => {
    playerRef.current?.seekTo(Math.max(0, seconds), true);
    setPosition(Math.max(0, seconds));
  }, []);

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
  }, []);

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
      play,
      seek,
      toggle,
      close,
      setExpanded,
      retry,
    }),
    [source, position, duration, playing, ready, failed, expanded, play, seek, toggle, close, retry],
  );

  return (
    <Ctx.Provider value={value}>
      {children}
      {/* The dock is the iframe's permanent home. It is always in the tree -
          moving the iframe between containers would remount it, which is
          exactly what R6.1 forbids - and collapsing only changes its size. */}
      <PlayerDock hostRef={hostRef} />
    </Ctx.Provider>
  );
}
