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
 */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: ["/admin", "/api", "/legacy"],
    },
    sitemap: `${siteUrl()}/sitemap.xml`,
  };
}
