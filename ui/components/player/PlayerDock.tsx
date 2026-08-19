"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef } from "react";
import { clock } from "@/lib/format";
import { usePlayer } from "./PlayerProvider";
import { MAX_W, MIN_W, usePlacement } from "./usePlacement";
import s from "./PlayerDock.module.css";

/**
 * The persistent player dock. Collapsing hides the picture and keeps the
 * iframe mounted and playing - a meeting is often something you listen to
 * while reading the record.
*/
export function PlayerDock({ hostRef }: { hostRef: React.RefObject<HTMLDivElement | null> }) {
  const p = usePlayer();
  const barRef = useRef<HTMLDivElement | null>(null);
  const dockRef = useRef<HTMLDivElement>(null);
  const place = usePlacement(dockRef);

  const scrub = useCallback(
    (clientX: number) => {
      const el = barRef.current;
      if (!el || !p.duration) return;
      const r = el.getBoundingClientRect();
      p.seek(((clientX - r.left) / r.width) * p.duration);
    },
    [p],
  );

  // the player must be fully keyboard-operable.
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
  const { placement } = place;

  /*
   *  Publish the room this dock takes from the page, so a page can lay itself
   * out around it instead of underneath it.
   *
   * Measured from offsets and computed style rather than getBoundingClientRect:
   * the card slides in and out on a transform, and its rect during those 220ms
   * is a lie about the room it occupies. The --dock token said 4.5rem and the
   * expanded dock measures 289px, so a declared constant was never going to
   * work either.
  */
  const publish = useCallback(() => {
    const el = dockRef.current;
    if (!el) return;
    const cs = getComputedStyle(el);
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const gapRight = parseFloat(cs.right) || 0;

    const lane = active && placement === "lane" ? Math.round(w + 2 * gapRight) : 0;

    /* Only a card that is actually against the bottom of the window is worth
     * padding for. A floating one dragged to the middle covers text wherever
     * the page ends, and the answer to that is to move it, not to leave a
     * 300px hole at the foot of every pane. */
    let room = 0;
    if (active && placement !== "lane") {
      const top = Number.isFinite(parseFloat(cs.top))
        ? parseFloat(cs.top)
        : window.innerHeight - h - (parseFloat(cs.bottom) || 0);
      const below = window.innerHeight - (top + h);
      if (below < 64) room = Math.max(0, Math.round(window.innerHeight - top));
    }

    const root = document.documentElement;
    root.style.setProperty("--dock-lane", `${lane}px`);
    root.style.setProperty("--dock-h", `${room}px`);
  }, [active, placement]);

  useEffect(() => {
    const el = dockRef.current;
    if (!el) return;
    publish();
    const ro = new ResizeObserver(publish);
    ro.observe(el);
    el.addEventListener("transitionend", publish);
    window.addEventListener("resize", publish);
    return () => {
      ro.disconnect();
      el.removeEventListener("transitionend", publish);
      window.removeEventListener("resize", publish);
      const root = document.documentElement;
      root.style.removeProperty("--dock-lane");
      root.style.removeProperty("--dock-h");
    };
  }, [publish]);

  /* Moving the card changes what it covers without changing its size, which is
   * the one thing the observer above cannot see. */
  useEffect(() => {
    publish();
  }, [publish, place.style]);

  return (
    <div
      ref={dockRef}
      className={s.dock}
      style={place.style}
      data-active={active}
      data-expanded={p.expanded}
      data-mode={placement}
      data-placed={place.moved}
      data-gesture={place.gesture ?? undefined}
      role="region"
      aria-label="Recording player"
      aria-hidden={!active}
      /* Not just hidden: with nothing loaded the dock is off-screen, and its
       * controls must leave the tab order with it. */
      inert={!active}
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

      <div className={s.transport} {...place.moveProps}>
        {placement === "sheet" ? null : (
          /* The window-splitter pattern: a separator the reader drags, and
             which the arrow keys move when it has focus. */
          <div
            className={s.grab}
            role="separator"
            aria-orientation="vertical"
            aria-label="Player width"
            aria-valuenow={place.width}
            aria-valuemin={MIN_W}
            aria-valuemax={MAX_W}
            tabIndex={0}
            {...place.sizeProps}
          />
        )}

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
          {/* The clock sits with the title rather than beside the bar: the bar
              is the control being aimed at, and in a card the reader can
              narrow to 18rem it needs every pixel of that row. */}
          <div className={s.label}>
            {p.source?.href ? (
              <Link href={p.source.href} className={s.title}>
                {p.source.title}
              </Link>
            ) : (
              <span className={s.title}>{p.source?.title ?? ""}</span>
            )}
            <div className={s.time}>
              <span className={s.now}>{clock(p.position)}</span>
              <span className={s.sep}>/</span>
              <span>{clock(p.duration)}</span>
            </div>
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

        {placement === "float" ? (
          <button
            type="button"
            className={s.icon}
            onClick={place.toCorner}
            aria-label={`Move the player to the ${place.nextCorner}`}
            /* The button walks the corners; the bar itself drags anywhere.
               Said here because this is where someone looks for it. */
            title="Move to the next corner, or drag the bar anywhere"
          >
            <GripIcon />
          </button>
        ) : null}

        {/* Two placements, one button, and the label says where the click
            lands rather than where the player is. On a narrow window the
            choice is between the sheet and floating - the column is not on
            offer, so it is not what the button offers. */}
        <button
          type="button"
          className={s.icon}
          onClick={() => place.setMode(placement === "float" ? "lane" : "float")}
          aria-label={
            placement !== "float"
              ? "Float the player over the page"
              : place.wide
                ? "Give the player its own column"
                : "Dock the player across the bottom"
          }
        >
          {placement !== "float" ? <FloatIcon /> : place.wide ? <LaneIcon /> : <SheetIcon />}
        </button>

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

/* Drawn rather than set in a glyph, for the same reasons as the archive's own
 * mark: they inherit the theme, they cost no request, and a font that lacks the
 * character cannot turn one into a box. Each says what the page looks like
 * after the click, not what it looks like now. */

const box = { fill: "none", stroke: "currentColor", strokeWidth: 1.3 } as const;

function LaneIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden focusable="false">
      <rect x="1.4" y="2.6" width="13.2" height="10.8" rx="1.6" {...box} />
      <rect x="9.4" y="4.4" width="3.6" height="7.2" rx="0.8" fill="currentColor" />
    </svg>
  );
}

function SheetIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden focusable="false">
      <rect x="1.4" y="2.6" width="13.2" height="10.8" rx="1.6" {...box} />
      <rect x="3.2" y="9.4" width="9.6" height="2.4" rx="0.8" fill="currentColor" />
    </svg>
  );
}

function FloatIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden focusable="false">
      <rect x="1.4" y="2.6" width="13.2" height="10.8" rx="1.6" {...box} />
      <rect x="6.6" y="6.8" width="7" height="5.4" rx="1" fill="currentColor" />
    </svg>
  );
}

function GripIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden focusable="false">
      <circle cx="6" cy="4.6" r="1.15" fill="currentColor" />
      <circle cx="10" cy="4.6" r="1.15" fill="currentColor" />
      <circle cx="6" cy="8" r="1.15" fill="currentColor" />
      <circle cx="10" cy="8" r="1.15" fill="currentColor" />
      <circle cx="6" cy="11.4" r="1.15" fill="currentColor" />
      <circle cx="10" cy="11.4" r="1.15" fill="currentColor" />
    </svg>
  );
}
