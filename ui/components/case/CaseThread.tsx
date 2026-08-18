"use client";

import Link from "next/link";

import { OutcomeBadge } from "@/components/OutcomeBadge";
import { meetingDate, shortBody } from "@/lib/format";
import type { Facts, ThreadStep } from "@/lib/types";
import s from "./CaseThread.module.css";

/**
 * R5.3.3 — the case thread, on the item.
 *
 * An item is rarely the whole story. A rezoning is heard by the Planning
 * Commission, transmitted by the Board and adopted months later; read one of
 * those twelve appearances alone and it looks like a continuance that went
 * nowhere. Councilmatic puts the legislative history *on* the ordinance for
 * this reason and it is right to: by the time a reader has found the item,
 * "has this come up before" is the next question they have.
 *
 * Compact by design. The dedicated view is one click away and does the work of
 * showing what changed between appearances (R5.4.2); this only has to show
 * that the sequence exists and where in it the reader is standing.
 */
export function CaseThread({
  caseId,
  steps,
  currentId,
  facts,
}: {
  caseId: string;
  steps: ThreadStep[];
  currentId: number;
  /** Measured, so the share-of-items clause cannot go stale. Absent when
   *  /api/facts failed, and the clause is then simply not made. */
  facts?: Facts | null;
}) {
  const here = steps.findIndex((x) => x.id === currentId);
  const decided = steps.filter((x) => x.outcome && x.outcome !== "continued");
  const continuances = steps.filter((x) => x.outcome === "continued").length;

  return (
    <section className={s.wrap} aria-labelledby="thread-head">
      <header className={s.head}>
        <h2 id="thread-head" className={s.title}>
          This case, across meetings
        </h2>
        <Link href={`/case/${encodeURIComponent(caseId)}`} className={s.full}>
          {caseId} in full →
        </Link>
      </header>

      <p className={s.summary}>
        {steps.length === 1 ? (
          <>
            Heard once. <span className={s.mono}>{caseId}</span> appears on no other agenda in
            the archive.
          </>
        ) : (
          <>
            <span className={s.mono}>{caseId}</span> was taken up{" "}
            <strong>{steps.length} times</strong> between {meetingDate(steps[0].date, "short")} and{" "}
            {meetingDate(steps[steps.length - 1].date, "short")}
            {continuances ? `, continued ${continuances} ${continuances === 1 ? "time" : "times"}` : ""}
            {here >= 0 ? <> — this is appearance {here + 1}.</> : "."}
          </>
        )}
      </p>

      <ol className={s.list}>
        {steps.map((step) => {
          const current = step.id === currentId;
          const inner = (
            <>
              <span className={s.when}>{meetingDate(step.date, "short")}</span>
              <span className={s.body}>{shortBody(step.body)}</span>
              {step.code ? <span className={s.code}>{step.code}</span> : null}
              <span className={s.spacer} />
              {step.recorded ? (
                <span
                  className={s.rec}
                  role="img"
                  aria-label="In a recording"
                  title="This appearance is in a recording"
                >
                  ▶
                </span>
              ) : null}
              <OutcomeBadge outcome={step.outcome} size="sm" />
            </>
          );
          return (
            <li key={step.id} className={`${s.step} ${current ? s.current : ""}`}>
              {current ? (
                <div className={s.rowHere} aria-current="true">
                  {inner}
                  <span className={s.youAreHere}>this item</span>
                </div>
              ) : (
                <Link href={`/item/${step.id}`} className={s.row} title={step.title ?? undefined}>
                  {inner}
                </Link>
              )}
            </li>
          );
        })}
      </ol>

      {decided.length === 0 && steps.length > 1 ? (
        <p className={s.open}>
          No appearance of this case has a final outcome in the minutes. It was continued,
          or the minutes do not record one in
          writing{facts ? ` — which is true for ${facts.pct_no_outcome}% of items.` : "."}
        </p>
      ) : null}
    </section>
  );
}
