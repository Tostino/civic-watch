import type { MetadataRoute } from "next";

import { siteUrl } from "@/lib/site";

/**
 * What a crawler may read. This archive exists to make a public record
 * findable, so the reading surfaces are open on purpose.
 *
 * `/search` IS NOT ONE OF THEM, and that is about cost rather than secrecy.
 * Every query runs the embedding model, which in the container is on the CPU
 * and measured at 1.28 seconds of CPU time per search - and the query space is
 * unbounded, so one indexed search link becomes as many as a crawler cares to
 * invent. Measured on the box: the API held about 4.4 cores for five hours,
 * which is roughly two searches a second and nothing like a reader.
 *
 * Nothing findable is lost. A result page is a list of pointers to
 * /meeting, /item and /case, and those are what the sitemap offers and what
 * carries the actual words. Search result pages are thin duplicates of them.
*/
/**
 * Rendered per request, which this file needs and its neighbours get for free.
 *
 * A metadata route with no dynamic API and no fetch is fully prerendered at
 * BUILD time, so `siteUrl()` was evaluated inside `docker build` — where
 * SITE_URL is deliberately unset, because the image must serve any county.
 * The result shipped `Sitemap: http://localhost:3000/sitemap.xml` in a
 * production image while every other surface was correct. Caught by running
 * the built image with a different domain and reading what came out.
 *
 * `sitemap.ts` and `/about` avoid this only because they fetch with
 * `cache: "no-store"`, which opts them out of static rendering as a side
 * effect. Do not add caching to those fetches without setting this there too.
 */
export const dynamic = "force-dynamic";

/**
 * ONE SITEMAP LINE, NOT TWELVE.
 *
 * This used to derive the file count here and print a `Sitemap:` line per
 * file, and it carried a comment promising the arithmetic matched
 * app/sitemap.ts. Two problems, and the comment was the smaller one.
 *
 * A sitemap scopes to its own parent directory when a crawler finds it this
 * way - Google states it plainly, and nothing here is submitted through
 * anybody's console - so twelve files under `/sitemap/` listing `/meeting/`,
 * `/item/` and `/case/` URLs put all 48,199 of them out of scope for every
 * crawler that reads this file. `/sitemap.xml` is at the root, so its scope is
 * the whole site, and the files it points at answer at the root too.
 *
 * It is also the path a crawler probes without being told, and it 404'd.
 */
export default function robots(): MetadataRoute.Robots {
  const base = siteUrl();
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      /* `/ask/` IS NOT HERE, AND ITS ABSENCE IS THE POINT.
       *
       * app/ask/[id] already answers `robots: { index: false }` in its
       * metadata, which is the right instruction: an answer the model wrote
       * is not the record, and it should not sit in an index beside pages
       * that are. Blocking the path here made that instruction unreadable -
       * a crawler refused the page never sees the tag - so Google indexed the
       * shared links it found anyway, with no title and no description, and
       * reported them back as "Indexed, though blocked by robots.txt": 17
       * pages, first seen 29 August 2026. Reading one costs a single row
       * (`/api/answer/<id>`, no model, no search), so there was never a cost
       * argument for the block either. Crawlable, and refused by the page
       * itself, which is the only combination that actually keeps them out.
       *
       * `/search` STAYS, for the reason above: the block there is about CPU,
       * not about the index, and a `noindex` cannot be read by a crawler that
       * has been told not to look. What kept /search out of this report is
       * that nothing links to it without `rel="nofollow"` any more. */
      disallow: ["/admin", "/api", "/search"],
    },
    sitemap: `${base}/sitemap.xml`,
  };
}
