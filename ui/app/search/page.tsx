import Link from "next/link";

import { Examples } from "@/components/Examples";
import { FilterRail, type Query } from "@/components/search/FilterRail";
import { RecordHits, TranscriptHits } from "@/components/search/Hits";
import { SearchBox } from "@/components/search/SearchBox";
import { ApiError, find, getFacets, getFacts } from "@/lib/api";
import s from "./search.module.css";

/*
 *  `/search`. Enter, by asking rather than by browsing.
 *
 *   BOTH SOURCES. The old search read utterances only, which cannot
 *   reach the 91% of decided items that were never recorded — so a matter the
 *   county decided in 2017 returned nothing at all, and read as "the archive
 *   does not have this".
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
  // The layout already appends " · Pasco Watch" (its title template), so
  // repeating it here doubled the archive's name in the tab.
  // `·` is the separator layout.tsx templates with, and what /item already
  // uses. This route was the odd one out on an em dash.
  return { title: q ? `${q} · search` : "Search" };
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
  const [facts, facets, result] = await Promise.all([
    // Always: <Empty> quotes it too, and it is the one call on this route that
    // is cheap whether or not anybody has typed anything. A failure costs the
    // clauses that quote a number, not the page.
    getFacts().catch(() => null),
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
              facts={facts}
              hits={result.transcript.hits}
              query={result.query}
              degraded={result.transcript.degraded}
            />

            {!result.record.total && !result.transcript.count ? (
              <p className={s.dead}>
                Nothing matched.{" "}
                {facts
                  ? `The archive holds ${facts.items} published items and ${facts.recorded} recorded meetings.`
                  : "The archive holds more than this search reached."}{" "}
                <Link href="/">Browse it by month</Link> if a search does not help.
              </p>
            ) : null}
          </main>
        </div>
      )}
    </div>
  );
}

/**
 * Twenty examples, four at a time, drawn fresh on every request. One fixed
 * five taught the duality but also taught that the archive is five things;
 * a reader who comes back sees a different four and learns it is not.
 *
 * ONE PER KIND, never two. Four subjects in a row reads as a list of topics
 * and hides the half of the box that takes identifiers, which is the thing
 * the examples exist to teach. The draw takes one from each pool and then
 * shuffles: pool order alone pinned the case number to the third line on
 * every load, which looks broken to anyone who refreshes twice.
 *
 * Every query here was checked against the API and returns hits in both
 * sources. The note says what KIND of thing the query is, and carries no
 * counts and no dates: those go stale silently, and browse already counts.
 */
const EXAMPLES: [string, string][][] = [
  [
    ["impact fees", "a subject"],
    ["affordable housing", "a subject"],
    ["short term rentals", "a subject"],
    ["stormwater", "a subject"],
    ["sinkhole", "a subject"],
    ["Penny for Pasco", "a local name for the sales tax"],
    ["license plate cameras", "argued long after it was decided"],
    ["ex parte communication", "a disclosure before a land-use vote"],
    ["impact fee credits", "the other side of impact fees"],
    ["gopher tortoise relocation", "a permit condition"],
    ["eminent domain", "the county taking land"],
    ["code enforcement lien", "where a code case ends"],
    // Whole questions. They ride in this pool rather than getting a fifth
    // line of their own, because four examples is the shape and a question
    // is still words rather than an identifier.
    //
    // A sentence nearly always loosens the record side, so its total is
    // meaningless and only the top of the two lists decides whether the
    // example is worth showing. Each of these was checked by reading the
    // first five FULL titles, which is the whole trick: truncated to a
    // hundred characters, five straight impact fee ordinances all read as
    // "An Ordinance By The Pasco County Board Of County Commissioners
    // Amending The Pasco County Land Development Code" and look like noise.
    // Two questions that read well but answered badly were cut here: a lot
    // mowing one, where "lot" pulled in every lot size variance, and a
    // water utility one, whose second hit was the best tasting water award.
    ["what is the county doing about flooding in my neighborhood", "a whole question"],
    ["does the county allow backyard chickens", "a whole question"],
    ["is the county raising the stormwater rate", "a whole question"],
    ["did the county raise impact fees for new homes", "a whole question"],
    ["how do I get a sidewalk on my street", "a whole question"],
  ],
  [
    ["Orange Belt Trail", "a trail"],
    ["Ridge Road extension", "a road project"],
    ["Suncoast Parkway", "a highway"],
    ["Starkey Ranch", "a development"],
    ["Dade City", "a city"],
  ],
  [
    ["PDE-25-7738", "a case number"],
    ["PDD-18-7277", "a case number"],
    ["PDD-17-7213", "a case number"],
    ["PDE-25-7831", "a case number"],
  ],
  [
    ["R-58", "an item code"],
    ["P58", "an item code"],
    ["R-12", "an item code"],
    ["P2", "an item code"],
  ],
];

const oneOf = <T,>(pool: readonly T[]): T => pool[Math.floor(Math.random() * pool.length)];

/** Fisher-Yates, in place, on a copy the caller owns. */
function shuffled<T>(xs: readonly T[]): T[] {
  const a = [...xs];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

/**
 * Arriving with no query. Not a blank page: the design says the box must teach the
 * duality, and an example somebody can click teaches it better than a
 * placeholder they have to read.
 */
function Empty() {
  const tries = shuffled(EXAMPLES.map(oneOf));
  return (
    <div className={s.empty}>
      <h1 className={s.emptyHead}>What you can search</h1>
      {/* One sentence each, naming the source and nothing else. Coverage used
          to be here too, which is what made this hard to read: a concession
          on the first source, a caveat on the second, and `recorded` in both
          its senses two words apart. The rail counts both and the examples
          show both, so the sentence does not have to. */}
      <p className={s.emptyWhy}>
        <b>What the county recorded</b> is every agenda item and what the minutes
        say was decided. <b>What was said</b> is the transcript of the recording.
      </p>
      <Examples
        label="Examples"
        mono
        items={tries.map(([q, why]) => ({
          tag: why,
          text: q,
          href: `/search?q=${encodeURIComponent(q)}`,
        }))}
      />
    </div>
  );
}
