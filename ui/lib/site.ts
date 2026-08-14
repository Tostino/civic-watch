/**
 * Where this archive is served from, for the handful of places that need an
 * absolute URL: the sitemap, robots.txt, the canonical link and the Open
 * Graph tags a shared link renders from.
 *
 * It is one function rather than a constant read in five files because
 * getting it wrong is silent: a wrong host produces a sitemap full of URLs
 * that resolve to nothing and share cards that point at localhost, and
 * nothing on the site itself looks any different.
 *
 * `SITE_URL` is read on the server at request/build time. The default is the
 * dev origin, which is the right default: a deployment that forgets to set it
 * produces obviously-local URLs rather than plausible wrong ones.
 */
const FALLBACK = "http://localhost:3000";

export function siteUrl(): string {
  const raw = process.env.SITE_URL?.trim();
  if (!raw) return FALLBACK;
  try {
    // Normalised so `https://host/` and `https://host` cannot produce
    // `https://host//sitemap.xml` depending on who wrote the env file.
    return new URL(raw).origin;
  } catch {
    return FALLBACK;
  }
}
