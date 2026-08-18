"use client";

import { useRouter } from "next/navigation";

import type { Body } from "@/lib/types";
import s from "./BodyPicker.module.css";

/**
 * Which board the whole page is about.
 *
 * This was seven pills wrapping onto two rows - "All meetings", "Planning
 * Commission 240", up to "Village of Pasadena Hills Planning and Policy 59" -
 * which is a lot of furniture for a control where exactly one value is ever
 * active. Chips are right when several can be on at once and a reader is
 * assembling a set. Here they were a radio group drawn as a toolbar, 66px of
 * it, above the thing they filter.
 *
 * A select says the same thing in one line, and says it in the shape a reader
 * already knows means "pick one of these".
 *
 * It stays a FORM. Without script the reader picks and presses the button;
 * with script the change navigates and the button is not rendered, so nothing
 * on screen is dead. `useRouter` rather than a link so the page transitions
 * rather than reloading - the same standard the calendar and the subject strip
 * are now held to.
 */
export function BodyPicker({ bodies, body }: { bodies: Body[]; body?: string }) {
  const router = useRouter();

  /* Built from the LIVE URL, not from the props the server rendered with.
   * The calendar and the subject strip now open in place and record it with
   * `replaceState`, so by the time somebody picks a board the address bar can
   * hold state this component was never re-rendered for - and starting from
   * `hidden` silently dropped it. Picking Planning Commission threw away an
   * expanded calendar in the URL while leaving it expanded on screen, so a
   * copied link no longer matched the page it came from. */
  const go = (next: string) => {
    const p = new URLSearchParams(
      typeof window === "undefined" ? "" : window.location.search,
    );
    if (next) p.set("body", next);
    else p.delete("body");
    const qs = p.toString();
    router.push(qs ? `/?${qs}` : "/", { scroll: false });
  };

  return (
    <form className={s.wrap} action="/" method="get">
      <label className="sr-only" htmlFor="body">
        Board or commission
      </label>
      <select
        id="body"
        name="body"
        className={s.select}
        defaultValue={body ?? ""}
        onChange={(e) => go(e.target.value)}
      >
        <option value="">All meetings</option>
        {bodies.map((b) => (
          <option key={b.body} value={b.body}>
            {b.body} &middot; {b.meetings.toLocaleString()}
          </option>
        ))}
      </select>
      {/* Only for a reader with no script. `onChange` has already navigated
          for everybody else, and a button that does nothing is worse than no
          button. */}
      <noscript>
        <button type="submit" className={s.go}>
          Show
        </button>
      </noscript>
    </form>
  );
}
