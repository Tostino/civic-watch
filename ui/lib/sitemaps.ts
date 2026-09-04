import { getIndexable } from "./api";

/**
 * HOW MANY SITEMAP FILES THERE ARE, in the one place that decides it.
 *
 * Three surfaces need this number and they must not be able to disagree:
 * app/sitemap.ts generates the files, app/sitemap-index.xml lists them, and
 * app/robots.ts points at the index. It used to live in two of those as
 * copied code with a comment promising they matched, which is a promise a
 * comment cannot keep.
 */

/** URLs per file. One file may hold 50,000 and the archive is already at
 *  48,199, so a single file would be one growth spurt from truncating. */
export const CHUNK = 5_000;

export const pages = (n: number) => Math.max(1, Math.ceil(n / CHUNK));

/** The endpoint caps `limit`; ask for one row to learn the total. */
export async function totals() {
  const [items, cases] = await Promise.all([
    getIndexable("item", 1, 0).catch(() => ({ total: 0 })),
    getIndexable("case", 1, 0).catch(() => ({ total: 0 })),
  ]);
  return { items: items.total, cases: cases.total };
}

/** File 0 is the entry points and every meeting; then the items; then the
 *  cases. */
export async function fileCount() {
  const { items, cases } = await totals();
  return 1 + pages(items) + pages(cases);
}

/**
 * WHERE A SITEMAP FILE ANSWERS, AND WHY IT IS AT THE ROOT.
 *
 * Google: "Unless you submit your sitemap through Search Console, a sitemap
 * affects only descendants of the parent directory." Next's metadata route
 * publishes these at `/sitemap/<n>.xml`, whose parent directory is
 * `/sitemap/` - and every URL inside them is `/meeting/...`, `/item/...`,
 * `/case/...` or `/`. Not one is a descendant. Through robots.txt, which is
 * how every crawler that is not Google-via-Search-Console finds them, all
 * 48,199 URLs were out of scope.
 *
 * The exemption for a Search Console submission is documented, and it did not
 * save us either: all twelve read as "Sitemap could not be read", 0 discovered
 * pages, on 23 August and again on 4 September after a resubmission. The
 * server was exonerated first - all twelve serve 200 as valid `<urlset>` in
 * under a second, concurrently, over one reused connection, and with
 * Googlebot's own headers - so the location is the remaining difference
 * between this archive and one that works.
 *
 * `next.config.ts` rewrites this root path onto the metadata route, so the
 * files answer at the root without a catch-all segment that would swallow
 * every 404 on the site. The old `/sitemap/<n>.xml` keeps working: it is what
 * Search Console already holds, and a redirect on a sitemap URL is one more
 * thing for a crawler to be unsure about.
 */
export const fileUrl = (base: string, n: number) => `${base}/sitemap-${n}.xml`;
