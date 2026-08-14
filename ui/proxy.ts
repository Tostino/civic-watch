import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * The hard block on the curation console, for the unified container.
 *
 * `admin.loopback()` asks whether a request's TCP peer is this machine. While
 * the API and the UI were separate containers that was a strong guarantee: the
 * peer was always the UI's container address, never loopback, so admin refused
 * outright no matter what any header said.
 *
 * Unified, Next proxies `/api` from INSIDE the same container, so the peer IS
 * 127.0.0.1 and that guarantee softens into gotcha 94's forwarding-header
 * check — which holds, but only because every public request arrives through
 * NPM carrying `x-forwarded-for`. That is a guarantee about somebody else's
 * config, and `next.config.ts` has warned in a comment for a while that
 * `/api/:path*` forwards `/api/admin/*` too.
 *
 * So the block moves here, where it does not depend on a peer address or on a
 * header a caller controls. `proxy` runs before rewrites (routing step 3, and
 * `beforeFiles` is step 4), so nothing reaches the rewrite that would forward
 * it. 404 rather than 403, for the same reason as at the edge: a refusal that
 * distinguishes "exists but forbidden" from "not here" tells a scanner which
 * hosts are worth a second look.
 *
 * Off by default so the operator's console still works under `next dev` on the
 * workstation, which is the only place it is supposed to work. The image sets
 * ADMIN_DISABLED=1; see deploy/Dockerfile.
 */
export function proxy(request: NextRequest) {
  if (process.env.ADMIN_DISABLED === "1") {
    return new NextResponse(null, { status: 404 });
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/admin/:path*", "/admin", "/api/admin/:path*", "/api/admin"],
};
