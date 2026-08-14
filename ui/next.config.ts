import type { NextConfig } from "next";

// The archive's data layer is the Python server in ../web. Proxying /api
// through Next keeps one origin in the browser, so there is no CORS surface
// and no absolute API host baked into client bundles.
const API = process.env.ARCHIVE_API ?? "http://127.0.0.1:8765";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      // The surfaces this rebuild has not reached yet (search, ask) still
      // serve from the old pages. Proxying them keeps one origin, so the
      // player and the API calls behave the same on both sides.
      { source: "/legacy/:path*", destination: `${API}/:path*` },
    ];
  },
};

export default nextConfig;
