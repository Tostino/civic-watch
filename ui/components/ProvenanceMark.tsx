import s from "./ProvenanceMark.module.css";

export type Provenance = "agenda" | "minutes" | "transcript" | "derived";

const COPY: Record<Provenance, { label: string; why: string }> = {
  agenda: {
    label: "Published agenda",
    why: "From the agenda Pasco County published for this meeting. The county's own words.",
  },
  minutes: {
    label: "Approved minutes",
    why: "From the minutes the board approved. The authoritative record of what was decided.",
  },
  transcript: {
    label: "Transcript",
    why: "Machine transcription of the recording. It shows what was said, not what was decided, and it can be wrong.",
  },
  derived: {
    label: "Inferred",
    why: "Derived by this archive from the recording. Not part of the county's published record.",
  },
};

/**
 * R6.4, and the load-bearing primitive of the whole UI.
 *
 * §2 of the requirements - that there are two kinds of truth here and they
 * must never be blurred - is only real if it is visible. This marks which one
 * a block came from, without interaction (R2.1): a filled square for the
 * published record, a hollow one for anything inferred.
 *
 * The mark is a reinforcement, not the mechanism. The primary signal is
 * typographic and set in tokens.css: the record is a serif on warm ground,
 * derived content is a sans on cool ground. That survives colour-blindness,
 * high-contrast mode and print, none of which a badge does.
 */
export function ProvenanceMark({
  kind,
  compact = false,
}: {
  kind: Provenance;
  compact?: boolean;
}) {
  const { label, why } = COPY[kind];
  const official = kind === "agenda" || kind === "minutes";
  return (
    <span
      className={`${s.mark} ${official ? s.official : s.inferred} ${compact ? s.compact : ""}`}
      title={why}
    >
      <span aria-hidden className={s.glyph} />
      <span className={s.label}>{label}</span>
    </span>
  );
}
