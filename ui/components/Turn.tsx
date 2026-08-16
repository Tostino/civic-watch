"use client";

import { SpeakerChip, speakerOf } from "@/components/SpeakerChip";
import { useDispute } from "@/components/admin/useDispute";
import { clock } from "@/lib/format";
import { groupTurns } from "@/lib/turns";
import type { Line, Office } from "@/lib/types";
import s from "./Turn.module.css";

/**
 * Speech, attributed. One speaker's consecutive lines, each seekable.
 *
 * Lived inside ItemView until /case needed to show everything said about one
 * application across every meeting that took it up (R5.4.4). It is the item's
 * and the case's, and it is not everybody's - which is worth stating, because
 * this comment used to claim search as a third caller and search has never
 * rendered a turn in its life:
 *
 *   /item, /case        this component, in flow.
 *   /meeting            its own, because it is virtualised over as many as
 *                       2,252 utterances and sizes its column against the
 *                       player's lane rather than the window. Same chip, same
 *                       `useDispute`; a different layout on purpose.
 *   /search, /ask       neither. They show PASSAGES - a run of utterances
 *                       collapsed to one row with a single `speaker`, which is
 *                       literally `(exchange)` when several people speak - so
 *                       there is no per-line claim to draw and no turn to
 *                       render.
 *
 * What must not diverge is how a claim about who spoke is presented, and that
 * is SpeakerChip's job in every one of them (R6.2.1, D3), and how a correction
 * is raised, which is `useDispute`'s.
 *
 * `offices` maps surname -> the office held AT THAT MEETING (R5.2.5), so the
 * caller passes the right meeting's roster. A case spanning three years passes
 * a different one per hearing, which is the reason this is a parameter rather
 * than something looked up in here.
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
  /* The console bridge (R5.8.3, R6.2.2). Only the operator ever sees the
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
  // The office held AT THIS MEETING (R5.2.5). The roster keys on surname.
  const office = first.name
    ? (offices[first.name] ?? offices[first.name.split(" ").pop() ?? ""])
    : null;

  return (
    <div className={s.turn}>
      <div className={s.who}>
        <SpeakerChip
          {...speakerOf(first)}
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
