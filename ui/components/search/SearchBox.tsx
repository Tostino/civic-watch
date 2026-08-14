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
}: {
  q: string;
  /** Facets to carry through the submit, so searching again keeps them. */
  hidden?: Record<string, string | undefined>;
}) {
  return (
    <form className={s.form} action="/search" method="get" role="search">
      <label className={s.label} htmlFor="q">
        Search the record and the recordings
      </label>
      <div className={s.row}>
        <input
          id="q"
          name="q"
          type="search"
          className={s.input}
          defaultValue={q}
          autoComplete="off"
          placeholder="impact fees, Orange Belt Trail, PDE-25-7738, R-58…"
          aria-describedby="q-hint"
        />
        <button type="submit" className={s.go}>
          Search
        </button>
      </div>
      <p id="q-hint" className={s.hint}>
        Subject words search 23,122 published agenda items and 1,036 hours of
        recordings. An item code or case number is matched as an identifier.
      </p>
      {Object.entries(hidden).map(([k, v]) =>
        v ? <input key={k} type="hidden" name={k} value={v} /> : null,
      )}
    </form>
  );
}
