import Link from "next/link";

import { FilterRail, type Query } from "@/components/search/FilterRail";
import { RecordHits, TranscriptHits } from "@/components/search/Hits";
import { SearchBox } from "@/components/search/SearchBox";
import { ApiError, find, getFacets } from "@/lib/api";
import s from "./search.module.css";

/* `/search` — §5.6. Enter, by asking rather than by browsing.
 *
 * Two properties are the whole design:
 *
 *   BOTH SOURCES (R5.6.1). The old search read utterances only, which cannot
 *   reach the 91% of decided items that were never recorded — so a matter the
 *   county decided in 2017 returned nothing at all, and read as "the archive
 *   does not have this".
 *
 *   ONE SURFACE (D9). Everything here is `web/tools.py`, which is the same set
 *   of tools the agent calls, with the same arguments. Not a parallel
 *   implementation that drifts: what a reader can find by hand, the agent can
 *   find too, and a bad result on this page reproduces as a tool call.
 *
 * No client component anywhere. The query and every facet are in the URL
 * (R4.2), the search box is a plain GET form, and the rail is links — so the
 * page is shareable, back works, and none of it needs script.
 */

/* Both sources have to be VISIBLE, not merely present. At 25 record cards the
 * transcript section sat two screens below the fold, so a page whose entire
 * claim is that it searches two sources showed one. Twelve each, and a summary
 * that names both counts and links down to the second. */
const LIMIT = 12;

type Props = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) || undefined;

export async function generateMetadata({ searchParams }: Props) {
  const q = one((await searchParams).q);
  // The layout already appends " · Pasco County meeting record" (its title
  // template), so repeating it here doubled the archive's name in the tab.
  return { title: q ? `${q} — search` : "Search" };
}

export default async function SearchPage({ searchParams }: Props) {
  const sp = await searchParams;
  const query: Query = {
    q: one(sp.q) ?? "",
    body: one(sp.body),
    outcome: one(sp.outcome),
    phase: one(sp.phase),
    case: one(sp.case),
    speaker: one(sp.speaker),
    since: one(sp.since),
    until: one(sp.until),
    decided: one(sp.decided),
  };
  const page = Math.max(0, Number(one(sp.page) ?? 0) || 0);

  const href = (next: Partial<Query>) => {
    const merged = { ...query, ...next };
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(merged)) if (v) p.set(k, v);
    return `/search?${p}`;
  };

  /* Arriving with no query renders <Empty>, which has no rail — so fetching
   * the facets there bought a three-second wait for data the page then threw
   * away, on the one route a reader opens before they have typed anything.
   * The rail's data is fetched when there is going to be a rail. */
  const searching = query.q.trim() !== "";

  /* Every filter is in the URL, so every filter can be typed by hand or
   * survive a rename. A value the tools reject is a bad request, not a broken
   * archive, and it must not take the whole route down with it. */
  let rejected: string | null = null;
  const [facets, result] = await Promise.all([
    searching ? getFacets() : null,
    searching
      ? find({
          q: query.q,
          limit: LIMIT,
          offset: page * LIMIT,
          body: query.body,
          outcome: query.outcome,
          phase: query.phase,
          case: query.case,
          speaker: query.speaker,
          since: query.since,
          until: query.until,
          decided: query.decided === undefined ? undefined : query.decided === "1",
        }).catch((e: unknown) => {
          if (e instanceof ApiError && e.status === 400) {
            rejected = "This link uses a filter the archive does not have.";
            return null;
          }
          throw e;
        })
      : null,
  ]);

  const shown = (page + 1) * LIMIT;
  const more =
    result && result.record.total > shown
      ? `${href({})}&page=${page + 1}`
      : null;

  return (
    <div className={s.page}>
      <header className={s.top}>
        <SearchBox
          q={query.q}
          hidden={{
            body: query.body, outcome: query.outcome, phase: query.phase,
            case: query.case, speaker: query.speaker, since: query.since,
            until: query.until, decided: query.decided,
          }}
        />
      </header>

      {rejected ? (
        <p className={s.dead}>
          {rejected}{" "}
          <Link href={`/search?q=${encodeURIComponent(query.q)}`}>
            Search without the filters
          </Link>
          .
        </p>
      ) : null}

      {!result ? (
        rejected ? null : <Empty />
      ) : (
        <div className={s.body}>
          {/* Non-null whenever a result is: both are fetched together, under
              the same condition. The check is for the type, not the case. */}
          {facets ? <FilterRail facets={facets} query={query} href={href} /> : null}

          <main className={s.results}>
            <p className={s.summary}>
              <b>{result.record.total.toLocaleString()}</b> in the published record,{" "}
              <a href="#tr-head" className={s.jump}>
                <b>{result.transcript.count.toLocaleString()}</b> in the recordings
              </a>
              {/* An identifier was recognised as one. Worth saying: it is the
                  thing the placeholder promised, and a reader who typed R-58
                  and got six R-58s across twelve years should know why. */}
              {result.by_code ? (
                <span className={s.mode}>matched as an identifier, not as words</span>
              ) : null}
            </p>

            <RecordHits
              hits={result.record.items}
              query={result.query}
              total={result.record.total}
              loosened={result.record.loosened}
              more={more}
            />

            <TranscriptHits
              hits={result.transcript.hits}
              query={result.query}
              degraded={result.transcript.degraded}
            />

            {!result.record.total && !result.transcript.count ? (
              <p className={s.dead}>
                Nothing matched. The archive holds 23,122 published items and 283
                recorded meetings &mdash;{" "}
                <Link href="/">browse it by month</Link> if a search does not help.
              </p>
            ) : null}
          </main>
        </div>
      )}
    </div>
  );
}

/**
 * Arriving with no query. Not a blank page: R5.6.4 says the box must teach the
 * duality, and an example somebody can click teaches it better than a
 * placeholder they have to read.
 */
function Empty() {
  const tries: [string, string][] = [
    ["impact fees", "a subject, across twelve years"],
    ["Orange Belt Trail", "a place"],
    ["PDE-25-7738", "a case, heard twelve times"],
    ["R-58", "an item code"],
    ["license plate cameras", "argued in August 2026, decided in 2024"],
  ];
  return (
    <div className={s.empty}>
      <h1 className={s.emptyHead}>Two sources, one search</h1>
      <p className={s.emptyWhy}>
        The <b>record</b> is what the county published &mdash; every agenda item and the
        disposition its approved minutes recorded, twelve years deep. The{" "}
        <b>room</b> is what people said &mdash; 1,036 hours of recordings, beginning in
        2018. Searching only one of them is how you conclude the archive holds nothing.
      </p>
      <ul className={s.tries}>
        {tries.map(([q, why]) => (
          <li key={q}>
            <Link href={`/search?q=${encodeURIComponent(q)}`} className={s.try}>
              {q}
            </Link>
            <span className={s.tryWhy}>{why}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
