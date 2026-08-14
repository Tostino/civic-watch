import type { Line } from "./types";

/**
 * Do two consecutive lines belong to the same turn?
 *
 * A transcript is read as turns - a run of lines from one voice, named once -
 * not as a list of utterances, because diarization splits a single sentence
 * across several rows and naming each one produces "Mariano: We have no one
 * online / Mariano: for / Mariano: this item."
 *
 * `basis` breaks a turn as surely as the name does. A corrected stretch inside
 * an otherwise machine-named voice is a *different kind of claim* about who is
 * speaking (R5.8.7), and absorbing it into the turn around it would show a
 * human's statement under a machine's attribution.
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
