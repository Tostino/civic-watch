/**
 * The archive's mark: the published record as stacked lines, with the
 * recording as the last one.
 *
 * Inline SVG rather than an image file, for three reasons: it costs no
 * request, it inherits the theme through the same custom properties as
 * everything else (so light and dark need no second asset), and it stays sharp
 * at any size. `app/icon.svg` carries the same geometry on a paper tile, since
 * a favicon has to survive a browser chrome we do not control.
*/
export function Mark({ size = 22 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
    >
      <rect x="3.5" y="3" width="17" height="2.6" rx="1.3" fill="var(--accent)" />
      <rect x="3.5" y="8" width="17" height="2.6" rx="1.3" fill="var(--accent)" opacity="0.68" />
      <rect x="3.5" y="13" width="11" height="2.6" rx="1.3" fill="var(--accent)" opacity="0.44" />
      <rect x="3.5" y="18.4" width="17" height="2.9" rx="1.45" fill="var(--live)" />
    </svg>
  );
}
