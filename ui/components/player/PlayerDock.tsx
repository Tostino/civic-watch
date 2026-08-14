"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef } from "react";
import { clock } from "@/lib/format";
import { usePlayer } from "./PlayerProvider";
import s from "./PlayerDock.module.css";

/**
 * The persistent player dock. Collapsing hides the picture and keeps the
 * iframe mounted and playing - a meeting is often something you listen to
 * while reading the record.
 *
 * D2: when the embed fails there is no broken frame. The dock says the
 * recording is unavailable and points at the transcript, which is held here
 * and does not depend on YouTube.
 */
export function PlayerDock({ hostRef }: { hostRef: React.RefObject<HTMLDivElement | null> }) {
  const p = usePlayer();
  const barRef = useRef<HTMLDivElement | null>(null);

  const scrub = useCallback(
    (clientX: number) => {
      const el = barRef.current;
      if (!el || !p.duration) return;
      const r = el.getBoundingClientRect();
      p.seek(((clientX - r.left) / r.width) * p.duration);
    },
    [p],
  );

  // R8.2: the player must be fully keyboard-operable.
  const onBarKey = useCallback(
    (e: React.KeyboardEvent) => {
      const step = e.shiftKey ? 60 : 10;
      if (e.key === "ArrowRight") { e.preventDefault(); p.seek(p.position + step); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); p.seek(p.position - step); }
      else if (e.key === "Home") { e.preventDefault(); p.seek(0); }
      else if (e.key === "End" && p.duration) { e.preventDefault(); p.seek(p.duration - 5); }
      else if (e.key === " " || e.key === "Enter") { e.preventDefault(); p.toggle(); }
    },
    [p],
  );

  // A global shortcut, but never while someone is typing.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== "k" || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      if (!p.source) return;
      e.preventDefault();
      p.toggle();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [p]);

  const pct = p.duration ? Math.min(100, (p.position / p.duration) * 100) : 0;
  const active = Boolean(p.source);

  /* Publish the room this dock actually occupies, so a page that fills the
   * window can keep its last lines reachable above it. The --dock token says
   * 4.5rem and the expanded dock measures 289px, so anything sizing itself
   * against the token was wrong by a factor of four. Measured, not declared:
   * the height changes when the video is collapsed. */
  const dockRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = dockRef.current;
    const root = document.documentElement;
    if (!el) return;
    /* What it OCCUPIES, not what it measures. The dock is active whenever a
     * recording is loaded, but it is slid out of the viewport until it is
     * shown, so keying on `active` padded the transcript by 305px for a dock
     * nobody could see. The overlap with the viewport is true in every state,
     * including mid-transition, which is why transitionend re-publishes. */
    const publish = () => {
      const room = Math.max(0, Math.round(window.innerHeight - el.getBoundingClientRect().top));
      root.style.setProperty("--dock-h", `${room}px`);
    };
    publish();
    const ro = new ResizeObserver(publish);
    ro.observe(el);
    el.addEventListener("transitionend", publish);
    window.addEventListener("resize", publish);
    return () => {
      ro.disconnect();
      el.removeEventListener("transitionend", publish);
      window.removeEventListener("resize", publish);
      root.style.removeProperty("--dock-h");
    };
  }, [active]);

  return (
    <div
      ref={dockRef}
      className={s.dock}
      data-active={active}
      data-expanded={p.expanded}
      role="region"
      aria-label="Recording player"
      aria-hidden={!active}
    >
      <div className={s.frame}>
        {/* Always rendered. Never remounted. Collapsing resizes it. */}
        <div className={s.stage}>
          <div ref={hostRef} className={s.host} />
        </div>
        {p.failed ? (
          <div className={s.failed}>
            <p>
              This recording will not load from YouTube right now. The transcript and the
              published record do not depend on it.
            </p>
            <button type="button" className={s.retry} onClick={p.retry}>
              Try the recording again
            </button>
          </div>
        ) : null}
      </div>

      <div className={s.transport}>
        <button
          type="button"
          className={s.play}
          onClick={p.toggle}
          aria-label={p.playing ? "Pause" : "Play"}
          disabled={!active || p.failed}
        >
          <span aria-hidden>{p.playing ? "▮▮" : "▶"}</span>
        </button>

        <div className={s.middle}>
          <div className={s.label}>
            {p.source?.href ? (
              <Link href={p.source.href} className={s.title}>
                {p.source.title}
              </Link>
            ) : (
              <span className={s.title}>{p.source?.title ?? ""}</span>
            )}
          </div>
          <div
            ref={barRef}
            className={s.bar}
            role="slider"
            tabIndex={active ? 0 : -1}
            aria-label="Move through the recording"
            aria-valuemin={0}
            aria-valuemax={Math.round(p.duration)}
            aria-valuenow={Math.round(p.position)}
            aria-valuetext={`${clock(p.position)} of ${clock(p.duration)}`}
            onKeyDown={onBarKey}
            onPointerDown={(e) => {
              e.currentTarget.setPointerCapture(e.pointerId);
              scrub(e.clientX);
            }}
            onPointerMove={(e) => e.buttons === 1 && scrub(e.clientX)}
          >
            <div className={s.track} />
            <div className={s.fill} style={{ width: `${pct}%` }} />
            <div className={s.knob} style={{ left: `${pct}%` }} />
          </div>
        </div>

        <div className={s.time}>
          <span className={s.now}>{clock(p.position)}</span>
          <span className={s.sep}>/</span>
          <span>{clock(p.duration)}</span>
        </div>

        <button
          type="button"
          className={s.icon}
          onClick={() => p.setExpanded(!p.expanded)}
          aria-label={p.expanded ? "Collapse the video" : "Show the video"}
          aria-expanded={p.expanded}
        >
          <span aria-hidden>{p.expanded ? "▾" : "▴"}</span>
        </button>
        <button type="button" className={s.icon} onClick={p.close} aria-label="Close the player">
          <span aria-hidden>✕</span>
        </button>
      </div>
    </div>
  );
}
