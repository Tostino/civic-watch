"use client";

import s from "./Timeline.module.css";

export interface TimelineEvent {
  /** Position on the axis, in the axis's own units. */
  at: number;
  /** For events with extent (an item's span). Omitted for points (a hearing). */
  to?: number;
  label: string;
  tone?: "ok" | "no" | "wait" | "neutral" | "none" | "live";
  onSelect?: () => void;
}

/**
 * One time axis, shared by the meeting (seconds through a session), the
 * case (months across appearances) and the person (years across terms).
 *
 * the design asks for time to be a first-class visual affordance rather than a list
 * with dates on it. This is that affordance: a meeting-day is 8 hours with 200
 * items in it, and its shape - where the consent block ended, where the long
 * hearing sat, where the board reports ran to the adjournment - is legible in
 * one glance here and in no list.
 */
export function Timeline({
  from,
  to,
  events,
  marker,
  label,
  height = "md",
  onScrub,
  format = String,
}: {
  from: number;
  to: number;
  events: TimelineEvent[];
  /** The playhead, or today, or whatever "now" means on this axis. */
  marker?: number | null;
  label: string;
  height?: "sm" | "md";
  onScrub?: (at: number) => void;
  format?: (v: number) => string;
}) {
  const span = Math.max(1, to - from);
  const pct = (v: number) => ((v - from) / span) * 100;

  return (
    <div
      className={`${s.wrap} ${height === "sm" ? s.sm : ""}`}
      role={onScrub ? "slider" : "img"}
      aria-label={label}
      aria-valuemin={onScrub ? from : undefined}
      /* role="slider" requires a value even before anything is playing;
       * omitting it leaves the control unannounceable. */
      aria-valuemax={onScrub ? to : undefined}
      aria-valuenow={onScrub ? Math.round(marker ?? from) : undefined}
      aria-valuetext={onScrub ? format(marker ?? from) : undefined}
      tabIndex={onScrub ? 0 : undefined}
      onKeyDown={
        onScrub
          ? (e) => {
              const step = (e.shiftKey ? 0.1 : 0.02) * span;
              if (e.key === "ArrowRight") { e.preventDefault(); onScrub((marker ?? from) + step); }
              if (e.key === "ArrowLeft") { e.preventDefault(); onScrub((marker ?? from) - step); }
            }
          : undefined
      }
      onPointerDown={
        onScrub
          ? (e) => {
              const r = e.currentTarget.getBoundingClientRect();
              onScrub(from + ((e.clientX - r.left) / r.width) * span);
            }
          : undefined
      }
    >
      <div className={s.axis} />
      {events.map((ev, i) => {
        const left = pct(ev.at);
        const width = ev.to != null ? Math.max(0.35, pct(ev.to) - left) : null;
        const style = { left: `${left}%`, ...(width != null ? { width: `${width}%` } : {}) };
        const cls = `${width != null ? s.band : s.point} ${s[ev.tone ?? "neutral"]}`;
        return ev.onSelect ? (
          <button
            key={i}
            type="button"
            className={cls}
            style={style}
            title={ev.label}
            aria-label={ev.label}
            onClick={(e) => {
              e.stopPropagation();
              ev.onSelect?.();
            }}
            onPointerDown={(e) => e.stopPropagation()}
          />
        ) : (
          <span key={i} className={cls} style={style} title={ev.label} />
        );
      })}
      {marker != null ? (
        <span className={s.marker} style={{ left: `${Math.min(100, Math.max(0, pct(marker)))}%` }}>
          <span className={s.markerLabel}>{format(marker)}</span>
        </span>
      ) : null}
    </div>
  );
}
