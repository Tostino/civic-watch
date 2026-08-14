"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { AdminApiError, adminLogin, getAdminState } from "@/lib/admin";
import s from "./Gate.module.css";

/**
 * D1, the console's front door. The startup token lives in a mode-600 file
 * beside env.local.sh and is pasted here ONCE; the POST exchanges it for an
 * httpOnly cookie, so it is never in a URL, never in history, never readable
 * by script. A server restart mints a new token and this gate reappears.
 */
export function Gate({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const { data, isPending, isError } = useQuery({
    queryKey: ["admin", "state"],
    queryFn: getAdminState,
    staleTime: 60_000,
    retry: false,
  });
  const [token, setToken] = useState("");
  const login = useMutation({
    mutationFn: adminLogin,
    onSuccess: () => {
      setToken("");
      void qc.invalidateQueries({ queryKey: ["admin"] });
    },
  });

  if (isPending) {
    return (
      <div className={s.state} role="status">
        Checking the session…
      </div>
    );
  }
  if (isError) {
    return (
      <div className={s.state} role="alert">
        We could not reach the archive API. Start <code>web/server.py</code> and reload. Admin
        answers only on this machine.
      </div>
    );
  }
  if (!data.authenticated) {
    return (
      <form
        className={s.gate}
        onSubmit={(e) => {
          e.preventDefault();
          if (token.trim()) login.mutate(token.trim());
        }}
      >
        <h1>Operator sign-in</h1>
        <p>
          Paste the token from <code>.admin_token</code>, next to <code>env.local.sh</code>. The
          server writes a new token each time it starts.
        </p>
        <div className={s.row}>
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="startup token"
            aria-label="Admin startup token"
            autoComplete="off"
          />
          <button type="submit" disabled={login.isPending || !token.trim()}>
            {login.isPending ? "Signing in…" : "Sign in"}
          </button>
        </div>
        {login.isError ? (
          <p className={s.err} role="alert">
            {login.error instanceof AdminApiError && login.error.status === 403
              ? "That token does not match. The server may have restarted since the file was written. Read it again."
              : `Sign-in failed: ${login.error.message}`}
          </p>
        ) : null}
      </form>
    );
  }
  return <>{children}</>;
}
