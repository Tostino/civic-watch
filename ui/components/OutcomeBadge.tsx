import { outcomeLabel, outcomeTone } from "@/lib/format";
import type { Outcome } from "@/lib/types";
import s from "./OutcomeBadge.module.css";

/**
 * R6.3. One vocabulary and one colour semantics for an outcome, everywhere
 * it appears.
 *
 * The state that matters most is the one that is not an outcome: 8,440 items
 * carry no outcome, because the minutes simply do not record one for them in
 * writing. That is a gap in the record, not a decision, and it is drawn as an
 * absence - dashed, unfilled, muted - so it can never read as a quiet "no".
 */
export function OutcomeBadge({
  outcome,
  size = "md",
  title,
}: {
  outcome: Outcome | null;
  size?: "sm" | "md";
  title?: string;
}) {
  const tone = outcomeTone(outcome);
  return (
    <span
      className={`${s.badge} ${s[tone]} ${size === "sm" ? s.sm : ""}`}
      title={title ?? (outcome ? undefined : "The minutes record no outcome for this item")}
    >
      {outcome ? <span aria-hidden className={s.dot} /> : null}
      {outcomeLabel(outcome)}
    </span>
  );
}
