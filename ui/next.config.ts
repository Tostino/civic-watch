import type { NextConfig } from "next";

// The archive's data layer is the Python server in ../web. Proxying /api
// through Next keeps one origin in the browser, so there is no CORS surface
// and no absolute API host baked into client bundles.
const API = process.env.ARCHIVE_API ?? "http://127.0.0.1:8765";

// CURATION IS A DIFFERENT ORIGIN AND, IN PRODUCTION, NO ORIGIN AT ALL.
// The note below used to say the hole "remains one line above" - that
// `/api/:path*` forwarded /api/admin/* through this proxy, so the whole
// admin API answered on the public origin and only a header check stood
// in front of it. That check broke on Next 16 (see web/admin.py), so the
// boundary is structural now: admin has its own loopback listener, the
// public API 404s every admin path, and the rewrite that reaches the
// admin port EXISTS ONLY WHEN ADMIN_API IS SET.
const ADMIN_API = process.env.ADMIN_API
  ?? (process.env.NODE_ENV === "production" ? null : "http://127.0.0.1:8766");

const nextConfig: NextConfig = {
  // Traces the server's real imports into .next/standalone, so the runtime
  // image carries no node_modules. Harmless in dev — it only changes what
  // `next build` emits. See deploy/Dockerfile.ui.
  output: "standalone",

  experimental: {
    // A rewrite to an external destination is proxied by httpxy, which arms
    // `setTimeout(proxyTimeout)` on the socket to the API — an INACTIVITY
    // timer, defaulting to 30s. See
    // node_modules/next/dist/server/lib/router-utils/proxy-request.js.
    //
    // Thirty seconds is shorter than two things this API legitimately does.
    // /api/ask is an event stream whose gap between events is one model turn,
    // which on a question that thinks hard is well past thirty seconds of
    // silence; the socket was destroyed mid-run, EventSource got a bare error,
    // and the page said "The connection dropped." after a trace of 21
    // completed lookups. /api/document also waits up to 90s on CivicClerk.
    // This hop only became load-bearing when the API and the UI merged into
    // one image and stopped being separately routable from the edge.
    //
    // 900s, the SAME number as `proxy_read_timeout` at the edge
    // (deploy/nginx-proxy-manager.md). Two proxies with two ceilings is a trap:
    // the tighter one wins silently, so the config an operator reads is not the
    // one deciding. One number, both places.
    proxyTimeout: 900_000,
  },

  /*
   *  WHAT THE BACK BUTTON COSTS, and the one token that decides it.
   *
   * Every page here is dynamically rendered - each one reads search params, or
   * fetches from the Python API with `cache: "no-store"`, or both - and Next
   * gives a dynamically rendered page `private, no-cache, no-store, max-age=0,
   * must-revalidate`. `no-store` in that list is the reason a reader who opens
   * an item from a meeting and presses BACK re-fetches and re-renders the whole
   * meeting, transcript and all, instead of getting it back instantly: Chrome
   * refuses the back/forward cache outright for a main resource stored with
   * `no-store`, and it was refused on all sixteen runs of the audit.
   *
   * Nothing else in that header is doing harm, and nothing else changes here.
   * `no-cache` still means the browser revalidates before it shows a stored
   * copy on an ordinary navigation, so the archive is as fresh as it was.
   * `private` still keeps it out of any shared cache. What goes is the
   * instruction not to keep a copy at all - which is a promise about DISK, and
   * the back/forward cache is memory, and this site has no accounts, no session
   * and nothing in a page that belongs to the reader looking at it.
   *
   * The console is the exception and keeps `no-store`: it is the one surface
   * that renders the archive's unpublished working state.
   */
  async headers() {
    return [
      { source: "/admin/:path*",
        headers: [{ key: "Cache-Control",
                    value: "private, no-store, max-age=0, must-revalidate" }] },
      // Everything the public reads. The exclusions are the paths that set
      // their own and must keep it: `/_next/static` is content-addressed and
      // immutable, and the API and the tool endpoint answer for themselves.
      { source: "/((?!admin|api|mcp|_next).*)",
        headers: [{ key: "Cache-Control",
                    value: "private, no-cache, max-age=0, must-revalidate" }] },
    ];
  },

  async rewrites() {
    return [
      // BEFORE the general rule, because the first match wins. Absent entirely
      // when ADMIN_API is unset, which is what makes production safe.
      ...(ADMIN_API
        ? [{ source: "/api/admin/:path*",
             destination: `${ADMIN_API}/api/admin/:path*` }]
        : []),
      /*
       *  THE SITEMAPS ANSWER AT THE ROOT, and these two lines are how.
       *
       * A sitemap only affects descendants of its parent directory. Next's
       * metadata route publishes ours at `/sitemap/<n>.xml`, whose parent is
       * `/sitemap/`, while every URL inside is `/meeting/...`, `/item/...`,
       * `/case/...` or `/` - so all 48,199 were out of scope, and Search
       * Console read all twelve as "Sitemap could not be read", 0 discovered
       * pages, twice. lib/sitemaps.ts carries the full argument and what was
       * ruled out first.
       *
       * A rewrite rather than a route: a dynamic segment at the root would
       * have to be `/[file]`, which swallows every 404 on the site. And
       * rather than a redirect: a sitemap URL that hops is one more thing for
       * a crawler to be unsure about. The old `/sitemap/<n>.xml` keeps
       * answering, because that is what Search Console already holds.
       *
       * `/sitemap.xml` cannot be a route handler while app/sitemap.ts exists
       * - Turbopack refuses with "Conflicting route and metadata at
       * /sitemap.xml", checked rather than assumed - so the index lives at
       * `/sitemap-index.xml` and the conventional path is rewritten onto it.
       * The `\\d+` is what keeps that name out of the numeric rule below it.
       */
      { source: "/sitemap.xml", destination: "/sitemap-index.xml" },
      { source: "/sitemap-:n(\\d+).xml", destination: "/sitemap/:n.xml" },
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      // The tool surface (web/mcp_server.py), which is public on purpose:
      // an MCP client asks the archive its own questions, and the answer it
      // composes is composed from the same six tools /search and the agent
      // use. It lives OUTSIDE /api because that is the path an MCP client is
      // given, and it needs no separate timeout note: the transport answers
      // in JSON rather than SSE, so a tool call is one short request and
      // never the minutes-long silence /api/ask taught this proxy about.
      //
      // Metered by MCP_* in web/limits.py, which is deliberately not Ask's
      // budget: a tool call spends CPU and the two must not be able to close
      // each other.
      { source: "/mcp", destination: `${API}/mcp` },
      // The `/legacy/:path*` rewrite is deleted, not commented out. It existed
      // because "the surfaces this rebuild has not reached yet (search, ask)
      // still serve from the old pages" - both shipped, as slices 3 and 4, so
      // the reason was gone. What it left behind was a public door onto the
      // whole Python server: /legacy/speakers, /legacy/search and /legacy/ask
      // all answered 200 through this origin, and so did
      // /legacy/api/admin/session.
    ];
  },
};

export default nextConfig;
