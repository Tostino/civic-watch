import Link from "next/link";

import s from "./status.module.css";

/**
 * A meeting, item or case id that is not in the archive. All three dynamic
 * pages call `notFound()` for a bad id and for a 404 from the API, so this is
 * the page a mistyped or stale URL lands on.
 *
 * It says which of the two things happened, because they are different facts
 * and only one of them is about us: the county may never have published it,
 * or we may not hold it. Absence is information (COPY.md), and a 404 that
 * says only "not found" throws that information away.
*/
export default function NotFound() {
  return (
    <div className={s.wrap}>
      <p className={s.kicker}>Not in the archive</p>
      <h1>We do not hold this page</h1>
      <p className={s.lead}>
        The address is not a meeting, an agenda item or a case that this archive holds. It may
        never have been published, or the link may be out of date.
      </p>
      {/* 2015 is the first meeting the archive holds, measured — the /about
          page derives the same range live and the two agree. */}
      <p className={s.lead}>
        The archive covers the Board of County Commissioners and the Planning Commission since
        2015. Search finds an item by its words; browse finds a meeting by its date.
      </p>
      <div className={s.actions}>
        <Link href="/search" className={s.primary}>
          Search the record
        </Link>
        <Link href="/" className={s.secondary}>
          Browse by meeting
        </Link>
      </div>
    </div>
  );
}
