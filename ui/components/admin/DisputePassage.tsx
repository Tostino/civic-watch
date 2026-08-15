"use client";

import Link from "next/link";

import type { TranscriptHit } from "@/lib/types";
import { disputeHref } from "./useDispute";
import { useOperator } from "./useOperator";
import s from "./DisputePassage.module.css";

/**
 * "That name is wrong", raised from a search result or a citation.
 *
 * A reading surface offers this on the speaker's own chip, because it holds
 * the LINE and everything claimed about it - the basis, the confidence,
 * whether someone has already disputed it. A hit holds a PASSAGE: one
 * summarising `speaker` over a run of utterances, literally `(exchange)` when
 * several people speak, and no basis, no confidence, nothing contested.
 *
 * So this is deliberately NOT a SpeakerChip. There is no state here to draw
 * honestly, and drawing one anyway would be the archive asserting a kind of
 * claim it cannot support (R2.3, D3). It asks the question and lets the review
 * screen, which reads the lines, show the four possible answers.
 *
 * Nothing renders for a reader (R9.1), and nothing renders for a passage that
 * cannot say which utterances it covers.
 */
export function DisputePassage({ hit }: { hit: TranscriptHit }) {
  const operator = useOperator();
  if (!operator || hit.start_idx == null || hit.end_idx == null) return null;
  /* `speaker` decides, not `speaker_display`: `(exchange)` is a value only the
   * key carries. Same test as the label beside it — see `speakerOf`. */
  const named = hit.speaker && hit.speaker !== "(exchange)";
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
