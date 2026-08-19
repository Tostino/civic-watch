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
