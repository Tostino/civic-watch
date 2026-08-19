"use client";

import { useRouter } from "next/navigation";

import type { Line } from "@/lib/types";
import { useOperator } from "./useOperator";

/**
 * The console bridge, in ONE place: what turns "that name is
 * wrong" on a reading surface into the review screen, opened on the very lines
 * the reader was looking at.
 *
 * Undefined for a reader, so a caller hands the result straight to
 * SpeakerChip's `onDispute` and never has to ask who is looking. The probe
 * behind `useOperator` is one cached query however many turns are on screen.
 *
 * It was written out twice - once in the shared turn, once again inside the
 * meeting transcript, which cannot use that component because it is
 * virtualised. Two copies of the URL that carries a correction is one copy too
 * many: `sel` is parsed as `lo-hi` at the other end (ReviewScreen), and a
 * surface that drifted on that would send an operator to the wrong utterances
 * and say nothing about it.
 *
 * The video comes from the LINE and never from the page: /case shows one
 * application across every meeting that took it up, so a turn there is not
 * necessarily from the recording the page is otherwise about.
 */
export function useDispute(): ((turn: Line[]) => void) | undefined {
  const operator = useOperator();
  const router = useRouter();
  if (!operator) return undefined;
  return (turn: Line[]) => {
    const first = turn[0];
    if (!first) return;
    router.push(
      // Which voice the review screen should open on. Absent on a line the
      // diarizer never clustered, and the screen copes: it falls back to the
      // whole recording rather than to an empty queue.
      disputeHref(first.video_id, first.idx, turn[turn.length - 1].idx, first.local_label),
    );
  };
}

/**
 * The same destination, as an href.
 *
 * A turn is reached by pushing, because the reader is already in the page it
 * belongs to. A search hit is not: it is one of fifty results, and an operator
 * working a list wants to open several and keep the results behind them - so
 * that one is a real link, and this is the rule both of them travel by.
 *
 * `from`/`to` are utterance indexes. On a passage they are its `start_idx` and
 * `end_idx`, which is the only thing a search hit knows about who spoke: the
 * index carries one summarising `speaker` per passage and no per-line claim,
 * so a hit can raise the QUESTION and only the review screen can show the four
 * kinds of answer.
 */
export function disputeHref(
  videoId: string,
  from: number,
  to: number,
  label?: string | null,
): string {
  const q = new URLSearchParams({ sel: `${from}-${to}` });
  if (label) q.set("label", label);
  return `/admin/review/${encodeURIComponent(videoId)}?${q}`;
}
