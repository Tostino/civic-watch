import type { Line } from "./types";

/**
 * Do two consecutive lines belong to the same turn?
 *
 * Stated once here because the meeting transcript and the item page both group
 * lines and must agree about where a turn ends.
*/
export const sameTurn = (a: Line, b: Line): boolean =>
  a.voice === b.voice && a.name === b.name && a.basis === b.basis;

/** Consecutive lines from one voice, in order. */
export function groupTurns(lines: Line[]): Line[][] {
  const turns: Line[][] = [];
  for (const l of lines) {
    const last = turns[turns.length - 1];
    if (last && sameTurn(last[last.length - 1], l)) last.push(l);
    else turns.push([l]);
  }
  return turns;
}
