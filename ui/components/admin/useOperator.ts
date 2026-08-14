"use client";

import { useQuery } from "@tanstack/react-query";

import { getAdminSession } from "@/lib/admin";

/**
 * True only while the operator's admin session cookie is live.
 *
 * This is what lets a reading view offer "correct this name" without ever
 * showing it to a reader (R9.1): the probe costs no database work, a reader
 * gets `false` (or a refused connection off-loopback), and nothing renders.
 * It closes the loop R5.8.3 names — the error is noticed while READING, and
 * the fix should start from where it was noticed, not from re-finding the
 * voice in a separate tool.
 */
export function useOperator(): boolean {
  const { data } = useQuery({
    queryKey: ["admin", "session"],
    queryFn: getAdminSession,
    staleTime: 10 * 60_000,
    retry: false,
  });
  return data?.authenticated ?? false;
}
