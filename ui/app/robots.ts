import type { MetadataRoute } from "next";

import { siteUrl } from "@/lib/site";

/**
 * What a crawler may read. This archive exists to make a public record
 * findable, so the reading surfaces are open on purpose.
 *
 * Two exclusions, for two different reasons:
 *
 *   /admin  the curation console. It already refuses every non-loopback
 *           client and carries `robots: noindex` in its own metadata; this
 *           is the third lock on a door that should not be in an index.
 *   /api    machine surfaces. /api/ask in particular is PAID, and a crawler
 *           walking it would spend money for nobody's benefit. The rate
 *           limiter stops the bill either way (web/limits.py); this stops
 *           the well-behaved ones from queueing up against it at all.
 *   /ask/   saved answers. NOT /ask, which is the page and is in the sitemap:
 *           a Disallow matches by prefix, so the trailing slash is what makes
 *           this the answers and not the surface that produces them. They are
 *           machine-written readings of the archive, and the pages a search
 *           engine should be sending people to are the record itself — the
 *           meeting, the item, the case. Each one also carries `noindex` in
 *           its own metadata, which is the half that works on a crawler
 *           following a shared link from somewhere else.
 *
 * /legacy is no longer listed: the rewrite that served it is deleted and the
 * pages behind it no longer exist, so naming the path here would only tell a
 * crawler about a door that was bricked up.
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
      disallow: ["/admin", "/api", "/ask/"],
    },
    sitemap: `${siteUrl()}/sitemap.xml`,
  };
}
