import { fileCount, fileUrl } from "@/lib/sitemaps";
import { siteUrl } from "@/lib/site";

/**
 * THE ONE FILE THAT POINTS AT THE OTHERS, and it answers at `/sitemap.xml`.
 *
 * Next's metadata route splits the archive across `/sitemap/0.xml` and its
 * neighbours and publishes no index over them, so `/sitemap.xml` - the path
 * every crawler probes by convention - simply 404'd. robots.txt listed all
 * twelve instead, which worked as a list and not as a fix: a sitemap in
 * `/sitemap/` scopes to `/sitemap/`, and lib/sitemaps.ts sets out what that
 * cost.
 *
 * A route handler cannot be mounted at `/sitemap.xml` while app/sitemap.ts
 * exists - Turbopack refuses the build with "Conflicting route and metadata
 * at /sitemap.xml", which was checked rather than assumed - so this answers at
 * `/sitemap-index.xml` and next.config.ts rewrites the conventional path onto
 * it. A rewrite and not a redirect, so a crawler is handed the index rather
 * than a hop.
 *
 * `force-dynamic` is the same hazard app/robots.ts documents at length: a
 * metadata-ish route with no dynamic API is evaluated during `docker build`,
 * where SITE_URL is deliberately unset, and it would ship an index full of
 * `http://localhost:3000` links into a production image. The fetch below is
 * `no-store` and would opt out on its own; this says so out loud instead of
 * depending on it.
 */
export const dynamic = "force-dynamic";

/** `&` is the only character a URL of ours can carry that XML must not see
 *  raw. Escaped anyway rather than argued about: the base comes from an
 *  environment variable somebody else sets. */
const xml = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
   .replace(/"/g, "&quot;");

export async function GET() {
  const base = siteUrl();
  const n = await fileCount();
  const body =
    `<?xml version="1.0" encoding="UTF-8"?>\n`
    + `<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n`
    + Array.from({ length: n }, (_, i) =>
        `<sitemap>\n<loc>${xml(fileUrl(base, i))}</loc>\n</sitemap>\n`).join("")
    + `</sitemapindex>\n`;
  return new Response(body, {
    headers: {
      "content-type": "application/xml",
      /* The same instruction next.config.ts gives every other public page.
         Set here because a route handler answers for itself and is one of the
         paths that config excludes. */
      "cache-control": "private, no-cache, max-age=0, must-revalidate",
    },
  });
}
