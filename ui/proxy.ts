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
  /*
   * A REQUEST CARRYING `next-action` IS NOT FROM THIS SITE.
   *
   * That header asks Next to run a Server Action. This app has none - no file
   * under app/, components/ or lib/ contains "use server", and every form here
   * is method="get" or an onSubmit that preventDefaults - so the only thing
   * that sends it is something probing for Next's action endpoint.
   *
   * Next already refuses. With no actions registered it answers 404 "Server
   * action not found", but it warns on the way out, once per probe:
   *
   *   Error: The Server Reference ID did not match the expected format.
   *   Received "x".
   *
   * The 404 is right and the log line is noise about somebody else's scanner.
   * Answering here gives the same 404 without the render path being involved
   * at all, so the container's log stays about this archive. The matcher below
   * is what keeps this cheap: it runs on requests that carry the header and on
   * nothing else.
   */
  if (request.headers.has("next-action")) {
    return new NextResponse(null, { status: 404 });
  }
  if (process.env.ADMIN_DISABLED === "1") {
    return new NextResponse(null, { status: 404 });
  }
  return NextResponse.next();
}

export const config = {
  matcher: [
    "/admin/:path*",
    "/admin",
    "/api/admin/:path*",
    "/api/admin",
    /* Every path, but ONLY when the probe header is on it. A plain reader's
       request never matches this and never pays for it. */
    { source: "/:path*", has: [{ type: "header", key: "next-action" }] },
  ],
};
