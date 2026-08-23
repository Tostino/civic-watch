import type { MetadataRoute } from "next";

import { getIndexable, getMeetings } from "@/lib/api";
import { siteUrl } from "@/lib/site";

/**
 * EVERYTHING THE ARCHIVE HOLDS A PAGE FOR, split across several files.
 *
 * This used to list the meetings and stop, on the reasoning that a crawler
 * reaches the rest by following links from them. It could not: the panel that
 * carries those links was only rendered when its tab was the current one, so
 * /meeting/<id> served no `/item/` href at all. Twenty-six thousand items and
 * twenty thousand cases were reachable from nothing. The panel is in the page
 * now, and they are listed here as well - a crawler should not have to find
 * the one route in to forty-six thousand pages.
 *
 * SPLIT because one file may hold 50,000 URLs and this is already 48,000, so
 * a single file would be one growth spurt from silently truncating.
 */
export const revalidate = 3600;

const CHUNK = 5_000;

/** The endpoint caps `limit`; ask for one row to learn the total. */
async function totals() {
  const [items, cases] = await Promise.all([
    getIndexable("item", 1, 0).catch(() => ({ total: 0 })),
    getIndexable("case", 1, 0).catch(() => ({ total: 0 })),
  ]);
  return { items: items.total, cases: cases.total };
}

const pages = (n: number) => Math.max(1, Math.ceil(n / CHUNK));

export async function generateSitemaps() {
  const { items, cases } = await totals();
  /* 0 is the entry points and every meeting; then the items; then the cases. */
  const n = 1 + pages(items) + pages(cases);
  return Array.from({ length: n }, (_, id) => ({ id }));
}

export default async function sitemap(
  { id }: { id: number | string | Promise<number | string> },
): Promise<MetadataRoute.Sitemap> {
  const base = siteUrl();
  /*
   *  AWAITED, AND THEN COERCED, and both were needed.
   *
   * Next hands this one a PROMISE - the same async-params change that made
   * page `params` awaitable. Used directly it is an object, `Number(...)` of
   * it is NaN, and every one of the twelve files fell to the guard and served
   * the three entry points. Twelve valid sitemaps agreeing that this archive
   * holds three pages, all of them 200, which is the kind of wrong that does
   * not announce itself: it was only visible by counting the <loc> elements.
   *
   * `Number` on top of the await because what is inside is a string from the
   * URL, and `"0" === 0` is false.
   */
  const n = Number(await id);
  if (!Number.isFinite(n) || n < 0) return entryPoints(base);
  if (n === 0) return [...entryPoints(base), ...(await meetings(base))];

  const { items } = await totals();
  const itemPages = pages(items);
  const which = n - 1;
  return which < itemPages
    ? rows(base, "item", which)
    : rows(base, "case", which - itemPages);
}

function entryPoints(base: string): MetadataRoute.Sitemap {
  return [
    { url: base, changeFrequency: "daily", priority: 1 },
    /* NOT /search. robots.txt disallows it - a search endpoint has an
       unbounded query space and each query runs the embedding model - and a
       URL that is both submitted here and refused there is a contradiction a
       crawler reports back as an error against the whole sitemap. */
    { url: `${base}/ask`, changeFrequency: "weekly", priority: 0.8 },
    { url: `${base}/about`, changeFrequency: "monthly", priority: 0.5 },
  ];
}

async function meetings(base: string): Promise<MetadataRoute.Sitemap> {
  // The endpoint caps `limit` at 500, so this pages rather than asking for
  // everything and silently getting the first 500 - which is exactly what a
  // sitemap of 504 URLs for a 1,251-meeting archive looked like, and nothing
  // about the output said it had been truncated.
  const PAGE = 500;
  const all: { id: number; date: string | null }[] = [];
  try {
    for (let offset = 0; ; offset += PAGE) {
      // `when: "all"` deliberately: a meeting scheduled for next month is a
      // published agenda and a real page.
      const res = await getMeetings({ when: "all", limit: PAGE, offset });
      all.push(...res.meetings);
      if (all.length >= res.total || res.meetings.length < PAGE) break;
      if (all.length >= 45_000) break;
    }
  } catch {
    /* Whatever arrived is still a sitemap; an empty one is not. */
  }
  return all.map((m) => ({
    url: `${base}/meeting/${m.id}`,
    lastModified: m.date ? new Date(`${m.date}T00:00:00Z`) : undefined,
    changeFrequency: "yearly" as const,
    priority: 0.6,
  }));
}

async function rows(
  base: string,
  kind: "item" | "case",
  page: number,
): Promise<MetadataRoute.Sitemap> {
  try {
    const res = await getIndexable(kind, CHUNK, page * CHUNK);
    return res.rows.map((r) => ({
      /* Case ids are free text from a PDF and one would otherwise become a
         path segment of its own. The same encoding every link to them uses. */
      url: `${base}/${kind}/${encodeURIComponent(r.id)}`,
      lastModified: r.date ? new Date(`${r.date}T00:00:00Z`) : undefined,
      changeFrequency: "yearly" as const,
      priority: kind === "item" ? 0.5 : 0.4,
    }));
  } catch {
    return [];
  }
}
