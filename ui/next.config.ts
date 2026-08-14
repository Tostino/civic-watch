import type { NextConfig } from "next";

// The archive's data layer is the Python server in ../web. Proxying /api
// through Next keeps one origin in the browser, so there is no CORS surface
// and no absolute API host baked into client bundles.
const API = process.env.ARCHIVE_API ?? "http://127.0.0.1:8765";

const nextConfig: NextConfig = {
  // Traces the server's real imports into .next/standalone, so the runtime
  // image carries no node_modules. Harmless in dev — it only changes what
  // `next build` emits. See deploy/Dockerfile.ui.
  output: "standalone",

  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      // The `/legacy/:path*` rewrite is deleted, not commented out. It existed
      // because "the surfaces this rebuild has not reached yet (search, ask)
      // still serve from the old pages" - both shipped, as slices 3 and 4, so
      // the reason was gone. What it left behind was a public door onto the
      // whole Python server: /legacy/speakers, /legacy/search and /legacy/ask
      // all answered 200 through this origin, and so did
      // /legacy/api/admin/session.
      //
      // NOTE the same hole remains one line above. `/api/:path*` forwards
      // /api/admin/* too, and admin.loopback() reads the TCP peer - which for
      // any proxied request is 127.0.0.1. The guard that document says makes
      // admin "answer only on loopback" does not survive a reverse proxy.
      // Verified: POST /api/admin/login through this origin reaches the
      // handler and validates the token. Only the token's entropy is holding
      // that door, and the session cookie deliberately has no Secure flag
      // because the code assumes loopback. Block /api/admin at the edge before
      // this is served publicly.
    ];
  },
};

export default nextConfig;
