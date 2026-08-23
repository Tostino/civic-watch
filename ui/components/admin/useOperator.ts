"use client";

import { useQuery } from "@tanstack/react-query";

import { getAdminSession } from "@/lib/admin";

/**
 * The name of the mark, and what it is not.
 *
 * NOT the session. The session is `civic_admin`, it is httpOnly, script
 * cannot read it and nothing here tries: every admin route checks that cookie
 * on a loopback-only listener, and this file changes none of that. This is a
 * second cookie carrying one bit - "there is a console session on this
 * machine" - whose entire job is to let a page know it is worth ASKING.
 *
 * Forging it buys nothing. The answer still comes from the server, over the
 * session cookie, and a reader who sets this by hand gets a 404 from a public
 * origin that has no admin API behind it at all.
 */
const MARK = "civic_operator";

/** Twelve hours. Longer than a sitting at the console, shorter than a stale
 *  mark is worth carrying; and a wrong one costs a single request that clears
 *  it. */
const KEEP = 12 * 60 * 60;

function hasMark(): boolean {
  if (typeof document === "undefined") return false;
  return document.cookie.split("; ").some((c) => c === `${MARK}=1`);
}

/** Set when the console confirms a session, cleared the moment anything
 *  learns there is not one. Called from Gate, and from the probe below when it
 *  is refused. */
export function markOperator(on: boolean): void {
  if (typeof document === "undefined") return;
  document.cookie = on
    ? `${MARK}=1; Path=/; SameSite=Lax; Max-Age=${KEEP}`
    : `${MARK}=; Path=/; SameSite=Lax; Max-Age=0`;
}

/**
 * True only while the operator's admin session cookie is live.
 *
 * ASKED ONLY WHEN THERE IS SOMETHING TO ASK ABOUT. This used to probe
 * /api/admin/session on every render of every surface that offers to correct a
 * name, which is /search, a transcript and an answer - so every reader of
 * those pages sent a request to an admin path and got a 404 back, because in
 * production the admin API is not routed at any origin the public can reach.
 * It was the only error in the console on the whole site, and it was a request
 * per page view that could never once have returned true.
 *
 * The mark is what makes the difference between a reader and an operator
 * visible to a page BEFORE it asks. A reader has no mark and sends nothing. An
 * operator signed in at the console has one, and the probe behind it is a
 * single cached query however many turns are on screen.
 */
export function useOperator(): boolean {
  const { data } = useQuery({
    queryKey: ["admin", "session"],
    queryFn: async () => {
      try {
        const res = await getAdminSession();
        /* A restart empties the session table and the mark outlives it. This
           is where that is noticed from a reading surface, without a trip to
           the console. */
        if (!res.authenticated) markOperator(false);
        return res;
      } catch (e) {
        markOperator(false);
        throw e;
      }
    },
    enabled: hasMark(),
    staleTime: 10 * 60_000,
    retry: false,
  });
  return data?.authenticated ?? false;
}
