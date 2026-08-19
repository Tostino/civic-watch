"use client";

import { useRouter } from "next/navigation";

import type { Body } from "@/lib/types";
import s from "./BodyPicker.module.css";

/**
 * Which board the whole page is about.
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
