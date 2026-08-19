"use client";

import Link from "next/link";

import type { TranscriptHit } from "@/lib/types";
import { disputeHref } from "./useDispute";
import { useOperator } from "./useOperator";
import s from "./DisputePassage.module.css";

/**
 * "That name is wrong", raised from a search result or a citation.
*/
export function DisputePassage({ hit }: { hit: TranscriptHit }) {
  const operator = useOperator();
  if (!operator || hit.start_idx == null || hit.end_idx == null) return null;
  /* `who.name` is null for BOTH things this offer has to distinguish — a
   * passage crossing several speakers, and one the archive cannot name at all
   * — so the test is the same either way, and neither the label nor this has
   * to know that `(exchange)` exists. It used to test the raw key here, which
   * was the last copy of a rule the design wants in one place; web/archive.py's
   * `who()` is that place now. */
  const named = hit.who.name !== null;
  return (
    <Link
      className={s.dispute}
      href={disputeHref(hit.video_id, hit.start_idx, hit.end_idx)}
      title={`Open utterances ${hit.start_idx}–${hit.end_idx} of this recording in the review screen`}
    >
      {named ? "Correct this name" : "Identify these voices"}
    </Link>
  );
}
