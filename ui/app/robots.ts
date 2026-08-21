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

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: ["/admin", "/api", "/ask/", "/search"],
    },
    sitemap: `${siteUrl()}/sitemap.xml`,
  };
}
