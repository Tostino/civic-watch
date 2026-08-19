import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * The hard block on the curation console, for the unified container.
 *
 * `admin.loopback()` asks whether a request's TCP peer is this machine. While
 * the API and the UI were separate containers that was a strong guarantee: the
 * peer was always the UI's container address, never loopback, so admin refused
 * outright no matter what any header said.
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
