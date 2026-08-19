"use client";

import { SpeakerChip } from "@/components/SpeakerChip";
import { useDispute } from "@/components/admin/useDispute";
import { clock } from "@/lib/format";
import { groupTurns } from "@/lib/turns";
import type { Line, Office } from "@/lib/types";
import s from "./Turn.module.css";

/**
 * Speech, attributed. One speaker's consecutive lines, each seekable.
 *
 * What must not diverge is how a claim about who spoke is presented, and that
 * is SpeakerChip's job in every one of them, and how a correction
 * is raised, which is `useDispute`'s.
*/
export function Turns({
  lines,
  tags,
  offices,
  activeIdx = -1,
  onSeek,
}: {
  lines: Line[];
  /** voice cluster -> a short human-facing tag, from `voiceTags`. */
  tags: Map<number, string>;
  offices: Record<string, Office>;
  /** `idx` of the line the recording is inside, or -1. */
  activeIdx?: number;
  onSeek: (line: Line) => void;
}) {
  /* The console bridge. Only the operator ever sees the
   * affordance: for a reader the probe answers false and nothing renders. */
  const dispute = useDispute();

  return (
    <div className={s.turns}>
      {groupTurns(lines).map((turn) => (
        <Turn
          key={turn[0].idx}
          lines={turn}
          tags={tags}
          offices={offices}
          activeIdx={activeIdx}
          onSeek={onSeek}
          onDispute={dispute ? () => dispute(turn) : undefined}
        />
      ))}
    </div>
  );
}

function Turn({
  lines,
  tags,
  offices,
  activeIdx: active,
  onSeek,
  onDispute,
}: {
  lines: Line[];
  tags: Map<number, string>;
  offices: Record<string, Office>;
  activeIdx: number;
  onSeek: (line: Line) => void;
  onDispute?: () => void;
}) {
  const first = lines[0];
  // The office held AT THIS MEETING. The roster keys on surname.
  const office = first.name
    ? (offices[first.name] ?? offices[first.name.split(" ").pop() ?? ""])
    : null;

  return (
    <div className={s.turn}>
      <div className={s.who}>
        <SpeakerChip
          who={first.who}
          office={office ?? null}
          voiceTag={first.voice != null ? (tags.get(first.voice) ?? null) : null}
          size="sm"
          onDispute={onDispute}
        />
      </div>
      <div className={s.spoke}>
        {lines.map((l) => (
          <p key={l.idx} className={`${s.line} ${l.idx === active ? s.lineActive : ""}`}>
            <button
              type="button"
              className={s.at}
              onClick={() => onSeek(l)}
              title={`Play from ${clock(l.start)}`}
            >
              {clock(l.start)}
            </button>
            <span className={s.text}>{l.text}</span>
          </p>
        ))}
      </div>
    </div>
  );
}
