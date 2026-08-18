import s from "./SearchBox.module.css";

/**
 * A plain GET form. No client component, no state, no JavaScript: the query
 * and every filter live in the URL (R4.2), so a search is a link somebody can
 * send and the page works with script disabled.
 *
 * The placeholder is doing real work (R5.6.4). PRIOR_ART §1: Councilmatic's
 * `police, zoning, O2015-7825, etc.` teaches in six words that topics AND
 * identifiers both work. This archive has exactly that duality — subject words
 * against 23,122 published titles, and `PDE-25-7738` or `R-58` matched as
 * identifiers rather than as words — and nothing anywhere said so.
 */
export function SearchBox({
  q,
  hidden = {},
  compact = false,
  id = "q",
}: {
  q: string;
  /** Facets to carry through the submit, so searching again keeps them. */
  hidden?: Record<string, string | undefined>;
  /**
   * Browse arrives already explaining itself — a title, a lede and a coverage
   * panel all saying what is here — so the hint would be the fourth thing on
   * screen saying it again. Dropped there and kept on `/search`, where the box
   * IS the page and the duality it teaches has nothing else to lean on.
   */
  compact?: boolean;
  /**
   * The input's id, because two of these can be in one document.
   *
   * The header carries one above 48rem and browse carries its own below that
   * - never both visible, always both rendered, since which one shows is a
   * media query rather than a branch. Two `id="q"` in a document is invalid
   * and, worse, silently points the second label at the first field.
   */
  id?: string;
}) {
  return (
    <form className={s.form} action="/search" method="get" role="search">
      <label className={compact ? "sr-only" : s.label} htmlFor={id}>
        Search the record and the recordings
      </label>
      {/* ONE CONTROL, not two objects with a gap between them. The input and
          the button were separately bordered and separately rounded with 8px
          of page showing through, which reads as two things that happen to be
          next to each other; a search field is one thing. The border is on
          the wrapper now, the parts are flush inside it, and the focus ring
          goes round the whole via :focus-within. */}
      <div className={`${s.row} ${compact ? s.rowCompact : ""}`}>
        {/* THE GLYPH IS THE SUBMIT BUTTON in the compact field. A solid
            accent "Search" slab sat inches from the nav links and made two
            competing clusters at that end of the bar, in the loudest colour
            on the page, for a control every reader already submits with
            Enter. As a button it keeps the affordance and stops shouting.
            /search keeps the worded button: there the field is the page. */}
        <button
          type="submit"
          className={s.glyphGo}
          tabIndex={compact ? 0 : -1}
          aria-label="Search"
        >
          <svg className={s.glyph} viewBox="0 0 16 16" aria-hidden focusable="false">
            <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
            <path d="M10.5 10.5 L14 14" stroke="currentColor" strokeWidth="1.6"
                  strokeLinecap="round" />
          </svg>
        </button>
        <input
          id={id}
          name="q"
          type="search"
          className={s.input}
          defaultValue={q}
          autoComplete="off"
          placeholder="impact fees, Orange Belt Trail, PDE-25-7738, R-58…"
          {...(compact ? {} : { "aria-describedby": `${id}-hint` })}
        />
        {compact ? null : (
          <button type="submit" className={s.go}>
            Search
          </button>
        )}
      </div>
      {compact ? null : (
        <p id={`${id}-hint`} className={s.hint}>
          Subject words search 23,122 published agenda items and 1,036 hours of
          recordings. An item code or case number is matched as an identifier.
        </p>
      )}
      {Object.entries(hidden).map(([k, v]) =>
        v ? <input key={k} type="hidden" name={k} value={v} /> : null,
      )}
    </form>
  );
}
