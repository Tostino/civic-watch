"use client";

import Link from "next/link";
import { useEffect } from "react";

import s from "./status.module.css";

/**
 * What a reader sees when a page throws (R8.x, and the release checklist).
 * Without this file a production build shows the framework's blank
 * "Application error", which tells a resident of this county nothing and
 * tells us nothing either.
*/
export default function RouteError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className={s.wrap} role="alert">
      <p className={s.kicker}>Something went wrong</p>
      <h1>This page did not load</h1>
      <p className={s.lead}>
        The archive is here, but this page failed. The fault is ours, not the county&apos;s
        record. Try again. A page that failed once often loads on a second attempt.
      </p>
      <div className={s.actions}>
        <button type="button" className={s.primary} onClick={() => retry()}>
          Try again
        </button>
        <Link href="/" className={s.secondary}>
          Go to the archive
        </Link>
      </div>
      {error.digest ? (
        <p className={s.detail}>
          If you report this, quote <code>{error.digest}</code>. It identifies this exact
          failure in our logs.
        </p>
      ) : null}
    </div>
  );
}
