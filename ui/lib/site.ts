/**
 * Where this archive is served from, for the handful of places that need an
 * absolute URL: the sitemap, robots.txt, the canonical link and the Open
 * Graph tags a shared link renders from.
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
