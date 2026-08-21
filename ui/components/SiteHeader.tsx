"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { SearchBox } from "./search/SearchBox";
import { Mark } from "./Mark";
import { ThemeToggle } from "./ThemeToggle";
import { REPO } from "@/lib/site";
import s from "./SiteHeader.module.css";

/**
 * The public shell: Browse · Search · Ask · About. Curation lives behind
 * authentication in its own shell and is never linked from here - putting the
 * workbench in the reader's navigation is the single largest reason the old UI
 * felt incoherent.
*/
const NAV = [
  { href: "/", label: "Browse", exact: true },
  { href: "/search", label: "Search" },
  { href: "/ask", label: "Ask" },
  { href: "/about", label: "About" },
] as const;

/**
 * The two routes whose whole job is the field. On /search and /ask the page
 * itself opens with a full-width one, so the bar was showing a second, smaller
 * copy of the control the reader is already looking at - and on /ask the two
 * take different input, which makes the small one a trap: type a question into
 * it and you get a keyword search back.
 *
 * EXACT, not `startsWith`. `/ask/<id>` is an answer somebody was sent and has
 * no field on it at all; that reader is one click from wanting one.
 *
 * Everywhere else keeps it, /about included: those pages have no field of
 * their own, and browse actively depends on this one - it hides its own above
 * 48rem and lets the bar take over.
 */
const OWNS_A_FIELD = new Set(["/search", "/ask"]);

/**
 * The code, which is a different offer from anything else in this bar.
 *
 * NOT IN `NAV`. Those are places inside the archive and this leaves it, so it
 * is an <a> rather than a Link, it sits with the theme toggle at the trailing
 * end rather than among the pages, and it opens in a new tab: a reader who is
 * three clicks into a meeting should not lose that to a detour into a
 * repository.
 *
 * AN ICON, NOT A LABEL, and that is a constraint rather than a preference. The
 * bar is a nowrap row of a fixed 3.25rem that /meeting measures its panes
 * against, and even as an icon it does not fit the narrowest tier: measured,
 * the row was at exactly 320 of 320 before this existed. It is hidden under
 * 23rem and /about's footer carries it there instead.
 */

export function SiteHeader() {
  const path = usePathname();
  /* The admin shell brings its own chrome. Hiding the public
   * header here — rather than restructuring every route into groups — keeps
   * the two shells from ever rendering together, and the public nav still
   * carries no link in. */
  if (path.startsWith("/admin")) return null;
  return (
    <header className={s.header}>
      <div className={s.inner}>
        <Link href="/" className={s.brand}>
          <Mark size={24} />
          <span className={s.wordmark}>
            <span className={s.name}>Pasco Watch</span>
            <span className={s.what}>meeting record</span>
          </span>
        </Link>

        {/* Search from the deep pages rather than only from the two that ask
            for typing. the design asks for a command palette so the deeper
            entities stay reachable from anywhere without growing the nav bar;
            a field in the bar is the same answer with no keyboard shortcut to
            discover, no focus trap to get wrong, and it works with script off.

            Hidden below 48rem, where the bar is already a nowrap row that
            overflowed at 320px before anything was added to it. Browse keeps
            its own field for those widths. */}
        {OWNS_A_FIELD.has(path) ? null : (
          <div className={s.find}>
            <SearchBox q="" compact id="q-nav" />
          </div>
        )}

        <nav className={s.nav} aria-label="Main">
          {NAV.map((n) => {
            const active = "exact" in n && n.exact ? path === n.href : path.startsWith(n.href);
            return (
              <Link
                key={n.href}
                href={n.href}
                className={s.link}
                aria-current={active ? "page" : undefined}
              >
                {n.label}
              </Link>
            );
          })}
        </nav>

        {/* The mark is GitHub's own, drawn at the size the toggle beside it
            uses and inheriting `currentColor` so it needs no second asset for
            dark. Labelled rather than titled: it is a control with no text in
            it, so the accessible name has to come from somewhere. */}
        <a
          className={s.repo}
          href={REPO}
          target="_blank"
          rel="noreferrer"
          title="The code for this archive, on GitHub. Opens in a new tab."
          aria-label="The code for this archive, on GitHub. Opens in a new tab."
        >
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden focusable="false" fill="currentColor">
            <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.42 7.42 0 0 1 2-.27c.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
          </svg>
        </a>

        <ThemeToggle />
      </div>
    </header>
  );
}
