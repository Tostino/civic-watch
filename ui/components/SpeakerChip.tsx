"use client";

import { officeLabel } from "@/lib/format";
import type { Line, Office } from "@/lib/types";
import s from "./SpeakerChip.module.css";

export interface SpeakerChipProps {
  name: string | null;
  /** True when a human stated this name. Outranks everything derived (R5.8.7). */
  human?: boolean;
  /** How the name was established. See Line.basis. */
  basis?: "override" | "human" | "voice" | "cluster" | null;
  /** The office held AT THIS MEETING, if the published roster records one. */
  office?: Office | null;
  /** Page-local letter for an unnamed voice. Never a cluster id (R6.2.1). */
  voiceTag?: string | null;
  /** A correction is pending: show it as contested, take no side (R5.8.10). */
  contested?: boolean;
  /** More than one person speaks in this block (an exchange passage). */
  several?: boolean;
  size?: "sm" | "md";
  /** Slice 6 (D8) passes a handler; the affordance appears then, here (R6.2.2). */
  onDispute?: () => void;
}

/**
 * R6.2. The single renderer for "who said this", and the reason it is a
 * component at all rather than a string.
 *
 * Three rules it exists to enforce:
 *
 * 1. **Never render a raw internal label** (R6.2.1). `Speaker 3`, `Group 465`,
 *    `SPEAKER_00` are diarization ids that get reshuffled on every clustering
 *    run - only ~2% survive one - and they read as names. An unnamed voice is
 *    *unidentified*, and if the page needs to distinguish two of them it uses
 *    a page-local letter that claims nothing.
 *
 * 2. **Never look more certain than it is** (R2.3). A voice-matched name is
 *    drawn as an inference. It is right most of the time and it has been wrong
 *    for whole meetings at a stretch: "Barbara Wilhite" once collected 664
 *    voices across 316 clusters, of which 7% actually resembled her.
 *
 * 3. **One place to change the name** (D3). Display resolves here and nowhere
 *    else, so a future redaction rule for members of the public has exactly one
 *    site to act on. Never denormalise a rendered name into a cached string.
 *
 * No numeric confidence is shown (R5.5.6): speaker precision has not been
 * re-measured since the roster work, and a number would assert accuracy the
 * project cannot currently support.
 */
export function SpeakerChip({
  name,
  human = false,
  basis = null,
  office = null,
  voiceTag = null,
  contested = false,
  several = false,
  size = "md",
  onDispute,
}: SpeakerChipProps) {
  const role = office ? officeLabel(office.office) : null;
  /* `cluster` is a materially weaker claim than `voice` and gets its own
   * treatment: it is the name this voice goes by across the whole archive, not
   * evidence about this meeting. Collapsing the two is what let one cluster be
   * shown as Starkey in 36 meetings and Yeager in 10 without anything saying
   * so. */
  const state = several
    ? "several"
    : !name
      ? "unknown"
      : human
        ? "confirmed"
        : basis === "cluster"
          ? "weak"
          : "inferred";

  const body = (
    <>
      <span aria-hidden className={s.glyph} data-state={state} />
      <span className={s.name}>
        {several ? "Several speakers" : (name ?? "Unidentified speaker")}
      </span>
      {role ? <span className={s.role}>{role}</span> : null}
      {!name && !several && voiceTag ? (
        <span className={s.voice} title="A distinct voice in this meeting that has not been identified">
          Voice {voiceTag}
        </span>
      ) : null}
      {contested ? (
        <span className={s.contested} title="Someone has proposed a correction to this name">
          Disputed
        </span>
      ) : null}
    </>
  );

  const label =
    state === "confirmed"
      ? `${name} — ${basis === "override" ? "corrected by a person for this passage" : "confirmed by a person"}`
      : state === "inferred"
        ? `${name} — matched by voice at this meeting. Inferred, and it can be wrong.`
        : state === "weak"
          ? `${name} — the name this voice goes by across the archive, not evidence about this meeting. It is the most likely to be wrong.`
          : undefined;

  const className = `${s.chip} ${s[state]} ${size === "sm" ? s.sm : ""} ${contested ? s.isContested : ""}`;

  if (!onDispute) {
    return (
      <span className={className} title={label} data-speaker-state={state}>
        {body}
      </span>
    );
  }
  return (
    <span className={className} title={label} data-speaker-state={state}>
      {body}
      <button
        type="button"
        className={s.dispute}
        onClick={onDispute}
        aria-label={name ? `Correct the speaker name ${name}` : "Identify this speaker"}
      >
        {name ? "Correct this name" : "Identify"}
      </button>
    </span>
  );
}

/**
 * Assigns page-local letters to the unnamed voices of one transcript, in order
 * of appearance. Scoped to the page on purpose: it must be impossible to
 * mistake for a durable identifier, because a cluster id is not one.
 */
export function voiceTags(lines: Line[]): Map<number, string> {
  const tags = new Map<number, string>();
  for (const l of lines) {
    if (l.name || l.voice == null || tags.has(l.voice)) continue;
    const n = tags.size;
    tags.set(
      l.voice,
      n < 26
        ? String.fromCharCode(65 + n)
        : `${String.fromCharCode(65 + Math.floor(n / 26) - 1)}${String.fromCharCode(65 + (n % 26))}`,
    );
  }
  return tags;
}
