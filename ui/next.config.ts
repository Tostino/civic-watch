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

  async rewrites() {
    return [
      // BEFORE the general rule, because the first match wins. Absent entirely
      // when ADMIN_API is unset, which is what makes production safe.
      ...(ADMIN_API
        ? [{ source: "/api/admin/:path*",
             destination: `${ADMIN_API}/api/admin/:path*` }]
        : []),
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
