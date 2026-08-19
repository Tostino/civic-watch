"use client";

import Link from "next/link";
import { clock, phaseLabel, shortTitle } from "@/lib/format";
import type { ItemLike, Span } from "@/lib/types";
import { OutcomeBadge } from "./OutcomeBadge";
import { ProvenanceMark } from "./ProvenanceMark";
import s from "./ItemCard.module.css";

/**
 * An agenda item, wherever one appears: the meeting spine, a case timeline,
 * search results, the agent's evidence. One component, four contexts - which
 * is most of what cohesion actually is.
 *
 * Two densities. `row` is the spine: an item every 1.6rem, scannable at 200
 * items, doubling as a chapter track. `card` is the item standing on its own,
 * where the official title gets a serif and a readable measure.
 *
 * The anatomy is Councilmatic's and it is the right one: **identifier and
 * outcome first**, then the plain-language title, then metadata. A reader
 * scanning for "what happened to C36" should never have to read a 60-word
 * legal title to find out.
 *
 * `href` is optional on purpose. /item and /case arrive in slice 2; until they
 * do, an identifier renders as an identifier rather than as a link to a 404.
 */
export function ItemCard({
  item,
  density = "card",
  href,
  caseHref,
  onSeek,
  onSelect,
  active = false,
  showPhase = false,
  activeVideo,
  span: only,
  nth = 1,
  of = 1,
  times,
}: {
  item: ItemLike;
  density?: "row" | "card";
  href?: string;
  caseHref?: string;
  /** Given when the item is bound to a recording. Seeks the global player. */
  onSeek?: (videoId: string, seconds: number) => void;
  /**
   * Row variant, from a chronological rail. WHICH stretch this row stands for,
   * when the board took the item up more than once. Without it the
   * row falls back to the first, which is right for every other caller.
   */
  span?: Span | null;
  nth?: number;
  of?: number;
  /** Every time the item is taken up, so a row can point at the others. */
  times?: number[];
  /**
   * Row variant. Always makes the row a control, whether or not the item is
   * in a recording: 91% of decided items are not, and a spine whose rows do
   * nothing for them is a table of contents that cannot be used as one. With
   * a recording it plays; without, it reveals the item in the record.
   */
  onSelect?: () => void;
  active?: boolean;
  showPhase?: boolean;
  /** Which recording is on screen, so a time in the OTHER session says so. */
  activeVideo?: string | null;
}) {
  /* Optional: a search result carries the whole record but not the
     recording spans, which are a per-item fetch. Absent spans mean "not
     looked up", never "no recording" - the caller says which. */
  // `undefined` is "the caller did not say"; `null` is "this row is not in the
  // recording". Only the first falls back to the item's own first stretch.
  const span = only !== undefined ? only : (item.spans?.[0] ?? null);
  const title = item.title ?? "(no title published)";
  /* A transcript-derived stretch was never on an agenda, so the minutes had
   * nothing to decide. Showing "no outcome recorded" would report a
   * gap in the record where there is no record entry at all. */
  const published = item.source === "agenda";

  if (density === "row") {
    // About half of all meeting-days are two recordings on one continuous
    // agenda, so an item's offset is meaningless without knowing which.
    const elsewhere = Boolean(span && activeVideo && span.video_id !== activeVideo);
    /* An item taken up, set aside and returned to is one row per appearance
     *. Marking BOTH matters, not only the later ones: a reader who
     * finds the first stretch and hears it end with no decision has to be able
     * to tell that the answer comes later in the day. */
    const again = of > 1;
    const others = (times ?? []).filter((_, i) => i !== nth - 1).map(clock);
    const Inner = (
      <>
        <span className={`${s.rowTime} ${elsewhere ? s.otherSession : ""}`}>
          {span ? (
            <span title={elsewhere ? "In the other session of this meeting. Playing it switches session." : undefined}>
              {clock(span.start)}
            </span>
          ) : (
            <span className={s.unbound} title="In the published record; not located in any recording">
              ·
            </span>
          )}
        </span>
        <span className={s.rowMain}>
          {item.code ? <span className={s.code}>{item.code}</span> : null}
          <span className={s.rowTitle}>{shortTitle(title, 96)}</span>
        </span>
        {again ? (
          <span className={s.again} aria-label={`Taken up ${of} times; this is number ${nth}`}>
            {nth}/{of}
          </span>
        ) : null}
        {published ? <OutcomeBadge outcome={item.outcome} size="sm" /> : null}
      </>
    );
    const cls = `${s.row} ${active ? s.active : ""} ${item.source === "transcript" ? s.derived : ""}`;
    const why = !span
      ? "Show this item in the record"
      : again
        ? `Play from ${clock(span.start)}. Taken up ${of} times, also at ${others.join(" and ")}.`
        : `Play from ${clock(span.start)}`;
    return onSelect ? (
      <button
        type="button"
        className={`${cls} ${s.seekable}`}
        onClick={onSelect}
        aria-current={active ? "true" : undefined}
        title={why}
      >
        {Inner}
      </button>
    ) : (
      <div className={cls} aria-current={active ? "true" : undefined}>
        {Inner}
      </div>
    );
  }

  return (
    <article className={`${s.card} ${item.source === "transcript" ? s.derivedCard : ""}`}>
      <header className={s.head}>
        {item.code ? <span className={s.code}>{item.code}</span> : null}
        {published ? <OutcomeBadge outcome={item.outcome} /> : null}
        {showPhase ? <span className={s.phase}>{phaseLabel(item.phase)}</span> : null}
        <span className={s.spacer} />
        <ProvenanceMark kind={item.source === "agenda" ? "agenda" : "derived"} compact />
      </header>

      <h3 className={s.title}>
        {href ? (
          <Link href={href} className={s.titleLink}>
            {title}
          </Link>
        ) : (
          title
        )}
      </h3>

      {(item.department || item.case_id || item.districts) && (
        <p className={s.meta}>
          {item.department ? <span>{item.department}</span> : null}
          {item.case_id ? (
            caseHref ? (
              <Link href={caseHref} className={s.case}>
                {item.case_id}
              </Link>
            ) : (
              <span className={s.case}>{item.case_id}</span>
            )
          ) : null}
          {item.districts ? <span>District {item.districts}</span> : null}
        </p>
      )}

      {/* The minutes, verbatim. This is the authoritative answer to
          "what was decided" and it is set as the document it is. */}
      {item.outcome_text ? (
        <blockquote className={s.outcomeText}>
          <ProvenanceMark kind="minutes" />
          <p>{item.outcome_text}</p>
        </blockquote>
      ) : null}

      {span && onSeek ? (
        <button type="button" className={s.playLine} onClick={() => onSeek(span.video_id, span.start)}>
          <span aria-hidden>▶</span> Play this item from {clock(span.start)}
        </button>
      ) : null}
    </article>
  );
}
