/**
 * Where this archive is served from, for the handful of places that need an
 * absolute URL: the sitemap, robots.txt, the canonical link and the Open
 * Graph tags a shared link renders from - and what its tool endpoint calls
 * itself.
 *
 * SERVER ONLY, all of it. These read environment variables that Next does not
 * expose to the browser, so every one of them is resolved in a server
 * component and passed down as a prop. A client component that called one
 * would render the deployed value on the server and nothing in the browser,
 * which is a hydration mismatch.
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

/**
 * The name the tool endpoint answers to, which /about prints in the commands a
 * reader is meant to paste. It must be the name the handshake announces, so
 * this and web/mcp_server.py `NAME` read the same variable and apply the same
 * slug rule: a client alias lands in a shell command, a TOML table header and
 * a JSON key, and a name with a space in it hands the reader something that
 * does not run.
 *
 * The fallback is the code's own name rather than any archive's, so a
 * deployment that never set it says something true.
 */
export function mcpName(): string {
  const slug = (process.env.MCP_NAME ?? "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "civic-watch";
}
