"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { ItemCard } from "@/components/ItemCard";
import { OutcomeBadge } from "@/components/OutcomeBadge";
import { ProvenanceMark } from "@/components/ProvenanceMark";
import { shortTitle } from "@/lib/format";
import type { Item, Meeting } from "@/lib/types";
import s from "./RecordView.module.css";

/** Items the board actually stopped on. Everything else was taken en bloc. */
const SUBSTANTIVE = new Set(["public_hearing", "regular", "proclamation", "staff_report"]);

/**
 * The published record for this meeting: what the county put on the agenda and
 * what the minutes say became of it.
 *
 * This is the half of the page that works for the 91% of decided items with no
 * recording (R3.1), and on a meeting with no video it IS the page. It never
 * renders an empty player and never implies a recording exists.
 *
 * Hierarchy follows the record's own structure rather than flattening it: the
 * items that were heard get cards, and the consent agenda - which was approved
 * in one motion, without discussion - gets a table. Councilmatic classifies
 * routine legislation and then draws it identically to everything else, so a
 * page of permit-parking ordinances looks exactly like a page of rezonings.
 * That is the mistake this avoids.
 */
export function RecordView({
  meeting,
  items,
  hasAgenda,
  hasRecording,
  activeItem,
  onSeek,
}: {
  meeting: Meeting;
  items: Item[];
  hasAgenda: boolean;
  hasRecording: boolean;
  activeItem: number | null;
  onSeek: (videoId: string, seconds: number) => void;
}) {
  const [showRoutine, setShowRoutine] = useState(false);
  const activeRef = useRef<HTMLDivElement | null>(null);
  const activeRowRef = useRef<HTMLTableRowElement | null>(null);
  const derivedRef = useRef<HTMLLIElement | null>(null);

  // Selecting an item in the spine that has no recording brings it here, so
  // the click still goes somewhere (R4.3: no dead ends). It may be a card, a
  // row of the consent table, or a derived stretch.
  useEffect(() => {
    const el = activeRef.current ?? activeRowRef.current ?? derivedRef.current;
    el?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [activeItem]);

  const { heard, routine, derived } = useMemo(() => {
    const heard: Item[] = [];
    const routine: Item[] = [];
    const derived: Item[] = [];
    for (const i of items) {
      if (i.source === "transcript") derived.push(i);
      else if (SUBSTANTIVE.has(i.phase)) heard.push(i);
      else routine.push(i);
    }
    return { heard, routine, derived };
  }, [items]);

  /* Selecting a consent item from the spine must reveal it even when it sits
   * past the fold of the collapsed table. Derived rather than an effect that
   * flips state - the table simply cannot be collapsed over the item someone
   * just asked for. */
  const expandRoutine =
    showRoutine || routine.findIndex((i) => i.id === activeItem) >= 12;

  if (!hasAgenda) {
    return (
      <div className={s.wrap}>
        <div className={s.empty}>
          <h2 className={s.emptyTitle}>No published agenda for this meeting</h2>
          <p>
            The county publishes an agenda and minutes for its meetings through its portal.
            This archive has neither for this meeting. Either they were never posted, or the
            PDF is an image-only scan and this archive cannot read its text. 404 of the
            1,161 agendas here are such scans. So there is no official list of what the
            board took up, and no outcome in the minutes for anything.
          </p>
          {hasRecording ? (
            <p>
              What follows is derived from the recording alone: stretches this archive
              identified as separate matters. It is <strong>not</strong> the county&rsquo;s
              record and no part of it is authoritative.
            </p>
          ) : null}
        </div>

        {derived.length ? (
          <section className={s.section}>
            <h3 className={s.sectionHead}>
              <ProvenanceMark kind="derived" />
              <span className={s.sectionCount}>{derived.length} stretches</span>
            </h3>
            <div className={s.cards}>
              {derived.map((i) => (
                <ItemCard
                  key={i.id}
                  item={i}
                  showPhase
                  href={`/item/${i.id}`}
                  onSeek={i.spans.length ? (v, sec) => onSeek(v, sec) : undefined}
                />
              ))}
            </div>
          </section>
        ) : null}
      </div>
    );
  }

  return (
    <div className={s.wrap}>
      {heard.length ? (
        <section className={s.section}>
          <h2 className={s.sectionHead}>
            Heard and decided
            <span className={s.sectionCount}>{heard.length}</span>
          </h2>
          <p className={s.sectionNote}>
            Public hearings, regular business, and presentations — the items the board took
            up one at a time.
          </p>
          <div className={s.cards}>
            {heard.map((i) => (
              <div
                key={i.id}
                ref={i.id === activeItem ? activeRef : undefined}
                className={i.id === activeItem ? s.activeCard : undefined}
              >
                <ItemCard
                  item={i}
                  showPhase
                  /* Slice 2: the identifiers are links now. This is what turns
                     the record from a page into a graph — item → case →
                     item — and it is why ItemCard took these props unset. */
                  href={`/item/${i.id}`}
                  caseHref={i.case_id ? `/case/${encodeURIComponent(i.case_id)}` : undefined}
                  onSeek={i.spans.length ? (v, sec) => onSeek(v, sec) : undefined}
                />
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {routine.length ? (
        <section className={s.section}>
          <h2 className={s.sectionHead}>
            Taken together
            <span className={s.sectionCount}>{routine.length}</span>
          </h2>
          <p className={s.sectionNote}>
            The consent agenda and other items decided in a single motion, without
            discussion. They were approved without discussion, so there is nothing said about them to find.
          </p>
          <table className={s.table}>
            <caption className="sr-only">
              Items decided together at the {meeting.body} meeting
            </caption>
            <thead>
              <tr>
                <th scope="col">Item</th>
                <th scope="col">Title</th>
                <th scope="col">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {(expandRoutine ? routine : routine.slice(0, 12)).map((i) => (
                <tr
                  key={i.id}
                  ref={i.id === activeItem ? activeRowRef : undefined}
                  className={i.id === activeItem ? s.activeRow : undefined}
                >
                  <td className={s.tCode}>{i.code ?? "—"}</td>
                  <td className={s.tTitle}>
                    {/* A consent item is still an item with a URL. These are
                        most of the record — 150 of a 200-item agenda — and
                        leaving them unlinked is how /item ends up reachable
                        only from the four things anyone already knew about
                        (R4.1). */}
                    <Link href={`/item/${i.id}`} title={i.title ?? undefined}>
                      {shortTitle(i.title, 150)}
                    </Link>
                    {i.case_id ? (
                      <Link
                        className={s.tCase}
                        href={`/case/${encodeURIComponent(i.case_id)}`}
                      >
                        {i.case_id}
                      </Link>
                    ) : null}
                  </td>
                  <td>
                    <OutcomeBadge outcome={i.outcome} size="sm" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {routine.length > 12 ? (
            <button type="button" className={s.more} onClick={() => setShowRoutine((v) => !v)}>
              {expandRoutine ? "Show fewer" : `Show all ${routine.length}`}
            </button>
          ) : null}
        </section>
      ) : null}

      {derived.length ? (
        <section className={s.section}>
          <h2 className={s.sectionHead}>
            Not on the published agenda
            <span className={s.sectionCount}>{derived.length}</span>
          </h2>
          <p className={s.sectionNote}>
            Stretches of the recording this archive identified — the call to order, recesses,
            adjournment, and anything taken up that the agenda does not list. Inferred, not
            published.
          </p>
          <ul className={s.derivedList}>
            {derived.map((i) => (
              <li key={i.id} ref={i.id === activeItem ? derivedRef : undefined}>
                <ItemCard
                  item={i}
                  density="row"
                  active={i.id === activeItem}
                  onSelect={
                    i.spans.length ? () => onSeek(i.spans[0].video_id, i.spans[0].start) : undefined
                  }
                />
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {!heard.length && !routine.length && !derived.length ? (
        <div className={s.empty}>
          <h2 className={s.emptyTitle}>Nothing recorded for this meeting</h2>
          <p>
            The county lists this meeting but we hold no agenda items for it and no
            recording. A gap like this means the record is missing, not that nothing
            happened.
          </p>
        </div>
      ) : null}
    </div>
  );
}
