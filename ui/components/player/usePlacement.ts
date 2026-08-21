"use client";

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

/**
 * Where the player sits on the screen, and who decides.
 *
 * The choice, the width and the floating position are the reader's preference
 * about their own screen, so they live in localStorage and never in the URL: a
 * shared link carries a moment in a meeting, not the furniture.
*/

export type DockMode = "lane" | "float";
export type Placement = DockMode | "sheet";

const KEY = "civicwatch.player.dock";
/* What the key was called when the code was named after one county. Read, never
   written: a reader who had floated the dock keeps it where they put it, and
   the next drag writes it back under the current name. */
const WAS = "pasco.player.dock";

/** Under this, a column is more than the window can spare: the meeting page
 *  spends 21rem of its width on the agenda spine before the transcript gets
 *  any, and a lane on top of that leaves a column too narrow to read. The
 *  breakpoint is declared here and nowhere else - the stylesheet reacts to
 *  `data-mode`, which this sets, so the two cannot drift apart. */
const WIDE = "(min-width: 72rem)";

/** The reader's range for the card's width. The stylesheet clamps the top end
 *  against the viewport as well (42vw), so on a small screen the lane can
 *  never eat the page. */
export const MIN_W = 288;
export const MAX_W = 640;

/** Until the reader says otherwise, the lane is a share of the window rather
 *  than a constant: 24rem is a quarter of a 1440px screen and a third of a
 *  1152px one, and the difference between those two is the difference between
 *  a transcript and a column of two-word lines. */
const SHARE = 0.26;
const DEFAULT_W = 384;

/** The gap the card keeps from any edge it sits against. Matches --sp-4, which
 *  is what the stylesheet uses for the same gap. */
const GAP = 16;

/** Which corner a floating card is being sent to. Ordered as the button walks
 *  them, anticlockwise from where the card starts. */
const CORNERS = ["bottom right", "bottom left", "top left", "top right"] as const;
export type Corner = (typeof CORNERS)[number];

interface Stored {
  mode: DockMode;
  /** Null until the reader sets one, so that the default can follow the
   *  window instead of being frozen at whatever it was on first sight. */
  width: number | null;
  /** Viewport coordinates of the floating card's top-left corner, once the
   *  reader has moved it. Null means "wherever the stylesheet parks it". */
  x: number | null;
  y: number | null;
  /** Which corner it is in, for the button that walks them. */
  corner: Corner;
}

const DEFAULTS: Stored = { mode: "lane", width: null, x: null, y: null, corner: "bottom right" };

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), Math.max(lo, hi));

/** A token's value in pixels. --header is declared in rem and this needs to
 *  compare it against a pointer position, which is not. */
function tokenPx(name: string): number {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const n = parseFloat(raw);
  if (!Number.isFinite(n)) return 0;
  if (raw.endsWith("rem")) return n * parseFloat(getComputedStyle(document.documentElement).fontSize);
  return n;
}

/** The first row the floating card may occupy: clear of the sticky header,
 *  which it would otherwise cover (the header is z-50, the dock z-60). */
const ceiling = () => tokenPx("--header") + GAP;

/*
 *  ------------------------------------------------------------------ store
*/

let snapshot: Stored = DEFAULTS;
let snapshotRaw: string | null = null;
let loaded = false;
let flush = 0;
const listeners = new Set<() => void>();

function parse(raw: string | null): Stored {
  if (!raw) return DEFAULTS;
  try {
    const v = JSON.parse(raw) as Partial<Stored>;
    return {
      mode: v.mode === "float" ? "float" : "lane",
      width: typeof v.width === "number" ? clamp(v.width, MIN_W, MAX_W) : null,
      x: typeof v.x === "number" ? v.x : null,
      y: typeof v.y === "number" ? v.y : null,
      corner: CORNERS.includes(v.corner as Corner) ? (v.corner as Corner) : "bottom right",
    };
  } catch {
    return DEFAULTS;
  }
}

function readStored(): Stored {
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(KEY) ?? window.localStorage.getItem(WAS);
  } catch {
    /* A browser that refuses storage still gets a working player. */
  }
  // Identity has to hold across renders or useSyncExternalStore spins, so the
  // snapshot is re-parsed only when the stored text is not the one it came
  // from - which includes another tab writing its own.
  if (!loaded || raw !== snapshotRaw) {
    loaded = true;
    snapshotRaw = raw;
    snapshot = parse(raw);
  }
  return snapshot;
}

function writeStored(next: Stored) {
  snapshot = next;
  listeners.forEach((l) => l());
  window.clearTimeout(flush);
  flush = window.setTimeout(() => {
    try {
      const raw = JSON.stringify(next);
      window.localStorage.setItem(KEY, raw);
      /* Only once it is actually stored. Claiming it first would leave the
       * cache describing a write that a private window had just refused, and
       * the next read would then "correct" the reader's own choice back to
       * what is on disk. */
      snapshotRaw = raw;
    } catch {
      /* ignored: see above */
    }
  }, 300);
}

function subscribeStored(cb: () => void) {
  listeners.add(cb);
  // Another tab moving the player is a change to the same reader's preference.
  window.addEventListener("storage", cb);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("storage", cb);
  };
}

function subscribeWide(cb: () => void) {
  const m = window.matchMedia(WIDE);
  m.addEventListener("change", cb);
  /* Belt as well as braces. The snapshot is a boolean, so a resize that does
   * not cross the breakpoint re-reads it and changes nothing - and when the
   * change event does not arrive (an emulated viewport is one case), the dock
   * is still the right shape for the window it is in. */
  window.addEventListener("resize", cb);
  return () => {
    m.removeEventListener("change", cb);
    window.removeEventListener("resize", cb);
  };
}

function subscribeSize(cb: () => void) {
  window.addEventListener("resize", cb);
  return () => window.removeEventListener("resize", cb);
}

/** The window's width, read the same way as the breakpoint: through a store,
 *  so that the server's render and the client's first pass agree and React
 *  corrects it after mount instead of warning about it. Zero means "not on a
 *  client yet". */
const useViewport = () =>
  useSyncExternalStore(
    subscribeSize,
    () => window.innerWidth,
    () => 0,
  );

/** Server-rendered as the wide case. The dock is off-screen and inert until a
 *  recording is loaded, which cannot happen before this has corrected itself,
 *  so the reader never sees the guess. */
const useWide = () =>
  useSyncExternalStore(
    subscribeWide,
    () => window.matchMedia(WIDE).matches,
    () => true,
  );

export interface PlacementApi {
  /** How the dock is actually arranged right now. */
  placement: Placement;
  /** What the reader chose, which `sheet` overrides on a narrow window. */
  mode: DockMode;
  /** Whether a column is on offer at all, which is what the reader is choosing
   *  between when they are not floating: a lane or a sheet. */
  wide: boolean;
  width: number;
  /** True once the floating card has been put somewhere by hand. */
  moved: boolean;
  /** --dock-w, plus left/top once the card has been moved. */
  style: React.CSSProperties;
  gesture: "move" | "size" | null;
  setMode: (m: DockMode) => void;
  /** Send the floating card to the next corner. This is the keyboard's way of
   *  moving it; dragging is the pointer's. */
  toCorner: () => void;
  /** The corner `toCorner` would go to, for the button's label. */
  nextCorner: Corner;
  /** Spread on the transport bar: drag it to move a floating card. */
  moveProps: {
    onPointerDown: (e: React.PointerEvent) => void;
    onPointerMove: (e: React.PointerEvent) => void;
    onPointerUp: (e: React.PointerEvent) => void;
  };
  /** Spread on the separator at the card's leading edge: drag to resize. */
  sizeProps: {
    onPointerDown: (e: React.PointerEvent) => void;
    onPointerMove: (e: React.PointerEvent) => void;
    onPointerUp: (e: React.PointerEvent) => void;
    onKeyDown: (e: React.KeyboardEvent) => void;
  };
}

export function usePlacement(dockRef: React.RefObject<HTMLElement | null>): PlacementApi {
  const wide = useWide();
  const vw = useViewport();
  const place = useSyncExternalStore(subscribeStored, readStored, () => DEFAULTS);
  const [gesture, setGesture] = useState<"move" | "size" | null>(null);

  const update = useCallback((patch: Partial<Stored>) => writeStored({ ...readStored(), ...patch }), []);

  /* Floating is always available - it is a deliberate choice to put the player
   * on top of the page, and a small window is exactly where someone might make
   * it. It is the COLUMN that a narrow window cannot afford, so that is the
   * only thing the width decides. */
  const placement: Placement = place.mode === "float" ? "float" : wide ? "lane" : "sheet";
  const moved = placement === "float" && place.x != null && place.y != null;
  const width = place.width ?? (vw ? clamp(Math.round(vw * SHARE), MIN_W, MAX_W) : DEFAULT_W);

  /** Keeps the whole card on screen and out from under the header. */
  const fit = useCallback(
    (x: number, y: number) => {
      const el = dockRef.current;
      const w = el?.offsetWidth ?? width;
      const h = el?.offsetHeight ?? 0;
      return {
        x: clamp(x, GAP, window.innerWidth - w - GAP),
        y: clamp(y, ceiling(), window.innerHeight - h - GAP),
      };
    },
    [dockRef, width],
  );

  // A window that shrinks must not leave the card half outside it.
  useEffect(() => {
    if (!moved) return;
    const onResize = () => update(fit(place.x as number, place.y as number));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [moved, place.x, place.y, fit, update]);

  /* ---------------------------------------------------------------- move */

  const grab = useRef({ dx: 0, dy: 0 });

  const onMoveDown = useCallback(
    (e: React.PointerEvent) => {
      if (placement !== "float" || e.button !== 0) return;
      // The transport is mostly controls. Only its own background drags.
      const t = e.target as HTMLElement;
      if (t.closest("button, a, input, [role='slider'], [role='separator']")) return;
      const el = dockRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      grab.current = { dx: e.clientX - r.left, dy: e.clientY - r.top };
      e.currentTarget.setPointerCapture(e.pointerId);
      setGesture("move");
      e.preventDefault(); // or the drag selects the title text instead
    },
    [dockRef, placement],
  );

  const onMoveMove = useCallback(
    (e: React.PointerEvent) => {
      if (gesture !== "move") return;
      update(fit(e.clientX - grab.current.dx, e.clientY - grab.current.dy));
    },
    [gesture, fit, update],
  );

  /*
   *  Corners, so that moving the player is not pointer-only. Four
   * predictable places beat arrow-key nudging: a reader who cannot see the
   * card is not going to aim it a pixel at a time.
  */
  const cornerAt = useCallback(
    (c: Corner) => {
      const el = dockRef.current;
      const w = el?.offsetWidth ?? width;
      const h = el?.offsetHeight ?? 0;
      const right = window.innerWidth - w - GAP;
      const bottom = window.innerHeight - h - GAP;
      const top = ceiling();
      if (c === "bottom right") return { x: right, y: bottom };
      if (c === "bottom left") return { x: GAP, y: bottom };
      if (c === "top left") return { x: GAP, y: top };
      return { x: right, y: top };
    },
    [dockRef, width],
  );

  /** The corner a dragged card came to rest nearest, so that the button
   *  continues from where the pointer left off rather than from a corner the
   *  card is no longer in. */
  const nearestCorner = useCallback((): Corner => {
    const el = dockRef.current;
    const r = el?.getBoundingClientRect();
    if (!r) return "bottom right";
    const bottom = r.top + r.height / 2 > window.innerHeight / 2;
    const right = r.left + r.width / 2 > window.innerWidth / 2;
    if (bottom) return right ? "bottom right" : "bottom left";
    return right ? "top right" : "top left";
  }, [dockRef]);

  const endMove = useCallback(
    (e: React.PointerEvent) => {
      if (e.currentTarget.hasPointerCapture?.(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId);
      }
      if (gesture === "move") update({ corner: nearestCorner() });
      setGesture(null);
    },
    [gesture, nearestCorner, update],
  );

  const nextCorner = CORNERS[(CORNERS.indexOf(place.corner) + 1) % CORNERS.length];

  const toCorner = useCallback(
    () => update({ ...cornerAt(nextCorner), corner: nextCorner }),
    [update, cornerAt, nextCorner],
  );

  /* -------------------------------------------------------------- resize */

  const size = useRef({ x: 0, w: 0, left: 0 });

  const resizeTo = useCallback(
    (w: number) => {
      /* The ceiling is the stylesheet's, restated: a lane may take 42% of the
       * window, a floating card may be as wide as the window has room for.
       * Whole pixels, because the separator announces this and "604.8" is not
       * a thing to read out. */
      const cap =
        placement === "float" ? window.innerWidth - 2 * GAP : Math.round(window.innerWidth * 0.42);
      const width = Math.round(clamp(w, MIN_W, Math.min(MAX_W, cap)));
      // A floating card grows leftwards, so its right edge stays where the
      // reader put it; a lane card is right-anchored and does that already.
      if (placement === "float" && place.x != null) {
        const x = size.current.left + (size.current.w - width);
        update({ width, x: clamp(x, GAP, window.innerWidth - width - GAP) });
      } else {
        update({ width });
      }
    },
    [placement, place.x, update],
  );

  const onSizeDown = useCallback(
    (e: React.PointerEvent) => {
      if (e.button !== 0) return;
      const el = dockRef.current;
      const r = el?.getBoundingClientRect();
      size.current = { x: e.clientX, w: r?.width ?? width, left: r?.left ?? 0 };
      e.currentTarget.setPointerCapture(e.pointerId);
      setGesture("size");
      // preventDefault stops the drag selecting text, and takes the focus the
      // pointer would have given the separator with it - so hand it back, or
      // the arrow keys do nothing until the reader tabs to it.
      e.preventDefault();
      (e.currentTarget as HTMLElement).focus();
    },
    [dockRef, width],
  );

  const onSizeMove = useCallback(
    (e: React.PointerEvent) => {
      if (gesture !== "size") return;
      resizeTo(size.current.w + (size.current.x - e.clientX));
    },
    [gesture, resizeTo],
  );

  const onSizeKey = useCallback(
    (e: React.KeyboardEvent) => {
      const step = e.shiftKey ? 64 : 16;
      const el = dockRef.current;
      const w = el?.offsetWidth ?? width;
      size.current = { x: 0, w, left: el?.getBoundingClientRect().left ?? 0 };
      if (e.key === "ArrowLeft") resizeTo(w + step);
      else if (e.key === "ArrowRight") resizeTo(w - step);
      else if (e.key === "Home") resizeTo(MIN_W);
      else if (e.key === "End") resizeTo(MAX_W);
      else return;
      e.preventDefault();
    },
    [dockRef, width, resizeTo],
  );

  const style = useMemo<React.CSSProperties>(() => {
    const s: React.CSSProperties & Record<string, string> = { "--dock-w": `${width}px` };
    if (moved) {
      s.left = `${place.x}px`;
      s.top = `${place.y}px`;
    }
    return s;
  }, [width, place.x, place.y, moved]);

  return {
    placement,
    mode: place.mode,
    wide,
    width,
    moved,
    style,
    gesture,
    setMode: (mode) => update({ mode }),
    toCorner,
    nextCorner,
    moveProps: { onPointerDown: onMoveDown, onPointerMove: onMoveMove, onPointerUp: endMove },
    sizeProps: {
      onPointerDown: onSizeDown,
      onPointerMove: onSizeMove,
      onPointerUp: endMove,
      onKeyDown: onSizeKey,
    },
  };
}
