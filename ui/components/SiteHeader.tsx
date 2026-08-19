"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { SearchBox } from "./search/SearchBox";
import { Mark } from "./Mark";
import { ThemeToggle } from "./ThemeToggle";
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
          <Mark size={22} />
          <span className={s.wordmark}>
            <span className={s.county}>Pasco County</span>
            <span className={s.what}>meeting record</span>
          </span>
        </Link>

        {/* Search on every page rather than only on two of them. the design asks
            for a command palette so the deeper entities stay reachable from
            anywhere without growing the nav bar; a field in the bar is the
            same answer with no keyboard shortcut to discover, no focus trap
            to get wrong, and it works with script off.

            Hidden below 48rem, where the bar is already a nowrap row that
            overflowed at 320px before anything was added to it. Browse keeps
            its own field for those widths. */}
        <div className={s.find}>
          <SearchBox q="" compact id="q-nav" />
        </div>

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

        <ThemeToggle />
      </div>
    </header>
  );
}
