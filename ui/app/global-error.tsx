"use client";

/**
 * The last resort: an error in the ROOT layout, which `error.tsx` cannot
 * catch because it sits inside that layout. This file replaces the whole
 * document when it renders.
 *
 * So it is deliberately self-contained — its own <html> and <body>, its own
 * styles inline. None of the app's global CSS, fonts or theme attribute reach
 * this page (the framework's docs are explicit about it), so a stylesheet
 * import here would be a link to something that never arrives. The colours
 * are the archive's, hard-coded to the two themes, keyed off the OS
 * preference because the app's toggle is not running either.
 *
 * `metadata` is not supported in a client component, so the title is set with
 * React's own <title>.
 */
export default function GlobalError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "grid",
          placeItems: "center",
          padding: "2rem",
          background: "#faf8f4",
          color: "#1b1917",
          fontFamily: "ui-sans-serif, system-ui, sans-serif",
          lineHeight: 1.6,
        }}
      >
        <title>Something went wrong · Pasco County meeting record</title>
        <style>{`
          @media (prefers-color-scheme: dark) {
            body { background: #14161a !important; color: #e9e7e2 !important; }
            .m { color: #b9b6af !important; }
            .b { background: #79b8dc !important; color: #10222d !important; }
            code { background: #22262d !important; border-color: #2b3037 !important; }
          }
        `}</style>
        <main style={{ maxWidth: "34rem" }}>
          <h1
            style={{
              margin: "0 0 0.75rem",
              fontSize: "1.75rem",
              fontFamily: "ui-serif, Georgia, serif",
              lineHeight: 1.25,
            }}
          >
            The archive did not load
          </h1>
          <p className="m" style={{ margin: "0 0 1.5rem", color: "#4a453e" }}>
            This is a fault in the site, not in the county&apos;s record. Try again; if it keeps
            happening the archive is down and we will know.
          </p>
          <button
            type="button"
            className="b"
            onClick={() => retry()}
            style={{
              font: "inherit",
              fontWeight: 650,
              padding: "0.5rem 1rem",
              borderRadius: 6,
              border: "1px solid transparent",
              background: "#1c5170",
              color: "#ffffff",
              cursor: "pointer",
            }}
          >
            Try again
          </button>
          {error.digest ? (
            <p className="m" style={{ marginTop: "1.5rem", fontSize: "0.8125rem", color: "#5f5a53" }}>
              If you report this, quote{" "}
              <code
                style={{
                  fontFamily: "ui-monospace, monospace",
                  background: "#f7f5f0",
                  border: "1px solid #e3ded3",
                  borderRadius: 3,
                  padding: "1px 4px",
                }}
              >
                {error.digest}
              </code>
              . It identifies this failure in our logs.
            </p>
          ) : null}
        </main>
      </body>
    </html>
  );
}
