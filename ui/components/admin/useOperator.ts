"use client";

import { useQuery } from "@tanstack/react-query";

import { getAdminSession } from "@/lib/admin";

/**
 * True only while the operator's admin session cookie is live.
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
