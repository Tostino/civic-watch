"use client";

import { Fragment } from "react";
import Link from "next/link";

import { OutcomeBadge } from "@/components/OutcomeBadge";
import { ProvenanceMark } from "@/components/ProvenanceMark";
import { SpeakerChip } from "@/components/SpeakerChip";
import { speakerOf } from "@/lib/speaker";
import { DisputePassage } from "@/components/admin/DisputePassage";
import { usePlayer } from "@/components/player/PlayerProvider";
import { clock, meetingDate, phaseLabel, shortBody, shortTitle } from "@/lib/format";
import type { AskResult, RecordHit, TranscriptHit } from "@/lib/types";
import s from "./Answer.module.css";

/**
 * An answer and the evidence under it — §5.5, and the only description of what
 * one looks like.
 *
 * It renders in two places and must be the same thing in both: live on `/ask`
 * as the stream closes, and on `/ask/<id>`, which is a run the server kept so
 * that it could be sent to somebody. A reader following a shared link is being
 * shown what the sender saw, so a second implementation of this — even a
 * simplified "read-only" one — would quietly make that untrue.
 *
 * Client, not server, because a transcript citation seeks the player.
 */
export function Answer({ r }: { r: AskResult }) {
  const byId = new Map(r.evidence.map((e) => [e.id, e]));
  const items = new Map(r.record.map((i) => [i.id, i]));
  const empty = !r.evidence.length && !r.record.length;
  const missing = (r.missing?.passages ?? 0) + (r.missing?.items ?? 0);

  return (
    <div className={s.answer}>
      <article className={s.prose}>{cite(r.answer, byId, items)}</article>

      {/* R5.5.5: no answer without evidence, and the empty result is designed
          rather than treated as a failure. */}
      {empty ? (
        <p className={s.nothing}>
          Nothing in the archive was cited for that, which means it did not find
          evidence it was willing to stand behind. It looked at{" "}
          {r.looked_at.items.toLocaleString()} published items and{" "}
          {r.looked_at.passages.toLocaleString()} passages.
        </p>
      ) : null}

      {/* R5.5.4: the official record is its own block, above the transcript. */}
      {r.record.length ? (
        <section className={s.block} aria-labelledby="ev-record">
          <header className={s.blockHead}>
            <h2 id="ev-record" className={s.blockTitle}>
              What the county published
            </h2>
            <ProvenanceMark kind="minutes" compact />
          </header>
          <ul className={s.list}>
            {r.record.map((i) => (
              <li key={i.id}>
                <RecordCite item={i} />
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {r.evidence.length ? <Evidence hits={r.evidence} /> : null}

      <footer className={s.note}>
        Looked at {r.looked_at.items.toLocaleString()} items and{" "}
        {r.looked_at.passages.toLocaleString()} passages; cited{" "}
        {(r.record.length + r.evidence.length).toLocaleString()}.
        {r.struck.length ? (
          <>
            {" "}
            <b className={s.struck}>
              {r.struck.length} citation{r.struck.length === 1 ? "" : "s"} removed
            </b>{" "}
            — the answer referred to {r.struck.join(", ")}, which this search never
            returned.
          </>
        ) : null}
        {/* Only a saved answer can report this: it kept the citation and not
            the quote, and the passage it pointed at is no longer there to read
            back. Named as a change in the ARCHIVE rather than as a fault in
            the answer, because that is what it is. */}
        {missing ? (
          <>
            {" "}
            <b className={s.struck}>
              {missing} citation{missing === 1 ? "" : "s"} no longer resolve
              {missing === 1 ? "s" : ""}
            </b>{" "}
            — what {missing === 1 ? "it pointed" : "they pointed"} at has changed in
            the archive since this was answered, which most often means a redaction
            was applied to it.
          </>
        ) : null}
        {r.stopped ? <> It stopped searching early, so this may not be everything.</> : null}
      </footer>
    </div>
  );
}

/* ------------------------------------------------------------- what it did */

export const TOOL_LABEL: Record<string, string> = {
  search_transcript: "searched the recordings",
  search_record: "searched the published record",
  get_item: "opened an agenda item",
  get_case: "followed a case across meetings",
  get_meeting: "opened a meeting’s agenda",
};

/** One lookup the agent made. `ok: null` is one still in flight, which only
 *  the live view can produce — a kept run has finished by definition. */
export type Lookup = {
  id?: string;
  name: string;
  args?: Record<string, unknown>;
  ok: boolean | null;
};

/**
 * What the agent actually did (R5.5.1). Shared for the same reason as the
 * answer: a run that searched the record and found nothing tells a reader
 * something a summary cannot, and that is as worth sending as the prose is.
 */
export function Lookups({ lookups }: { lookups: Lookup[] }) {
  return (
    <ol className={s.calls}>
      {lookups.map((c, i) => (
        <li key={c.id ?? i} className={`${s.call} ${c.ok === null ? s.pending : ""}`}>
          <span className={s.callWhat}>{TOOL_LABEL[c.name] ?? c.name}</span>
          <code className={s.callArgs}>{args(c.args)}</code>
          {c.ok === null ? (
            <span className={s.callWait}>…</span>
          ) : (
            <span className={c.ok ? s.callOk : s.callBad}>
              {c.ok ? "✓" : "rejected"}
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}

export const args = (a: Record<string, unknown> | undefined) =>
  Object.entries(a ?? {})
    .map(([k, v]) => (k === "query" ? `“${v}”` : `${k}=${v}`))
    .join(" · ");

/* -------------------------------------------------------------- citations */

/**
 * R5.5.2: the two citation types render distinctly. `[item:N]` reveals the
 * published record; `[N]` seeks the player to that moment.
 */
function cite(
  text: string,
  byId: Map<number, TranscriptHit>,
  items: Map<number, RecordHit>,
) {
  /* Citations and `**bold**` in one pass. The agent is told to write plain
   * prose, and mostly does; when it does not, the asterisks used to reach the
   * page literally — "**What the record shows.**" — which reads as a bug in
   * the archive rather than a slip by the model. Bold is the only markdown
   * honoured: anything more would let the answer style the page. */
  const re = /\[(item:)?(\d{1,7})\]|\*\*(.+?)\*\*/g;
  const out: React.ReactNode[] = [];
  let at = 0;
  let m: RegExpExecArray | null;
  let n = 0;
  while ((m = re.exec(text))) {
    if (m.index > at) out.push(<Fragment key={`t${n}`}>{text.slice(at, m.index)}</Fragment>);
    if (m[3] !== undefined) {
      out.push(<strong key={`b${n}`}>{m[3]}</strong>);
    } else {
      const id = Number(m[2]);
      if (m[1]) {
        const i = items.get(id);
        out.push(
          <a
            key={`c${n}`}
            href={`#item-${id}`}
            className={s.citeRecord}
            title={i?.title ?? "the published record"}
          >
            {/* The item's own identifier where it has one. A transcript-derived
                item has no code, so the meeting date is the next most useful
                thing a reader can act on — "record" told them nothing. */}
            {i?.code ?? (i ? meetingDate(i.date, "short") : "record")}
          </a>,
        );
      } else {
        out.push(<PlayCite key={`c${n}`} hit={byId.get(id)} id={id} />);
      }
    }
    at = m.index + m[0].length;
    n += 1;
  }
  out.push(<Fragment key="tail">{text.slice(at)}</Fragment>);
  return <div className={s.paras}>{out}</div>;
}

function PlayCite({ hit, id }: { hit: TranscriptHit | undefined; id: number }) {
  const player = usePlayer();
  if (!hit) return <span className={s.citeDead}>[{id}]</span>;
  return (
    <button
      type="button"
      className={s.citePlay}
      title={`${hit.speaker_display ?? hit.speaker ?? "Unidentified speaker"} · ${clock(hit.start)} — play`}
      onClick={() =>
        player.play(
          { videoId: hit.video_id, title: hit.title ?? "", href: hit.meeting_id ? `/meeting/${hit.meeting_id}` : undefined },
          hit.start,
          true,
        )
      }
    >
      ▸ {clock(hit.start)}
    </button>
  );
}

function RecordCite({ item }: { item: RecordHit }) {
  return (
    <div className={s.recRow} id={`item-${item.id}`}>
      <div className={s.recTop}>
        <Link href={`/meeting/${item.meeting_id}`} className={s.when}>
          {meetingDate(item.date, "short")}
        </Link>
        <span className={s.bodyTag}>{shortBody(item.body)}</span>
        {item.code ? <span className={s.code}>{item.code}</span> : null}
        <OutcomeBadge outcome={item.outcome} size="sm" />
      </div>
      <Link href={`/item/${item.id}`} className={s.recTitle}>
        {shortTitle(item.title, 150) || "(no title published)"}
      </Link>
      {item.disposition ? (
        <p className={s.disposition}>{item.disposition}</p>
      ) : (
        <p className={s.noDisposition}>The minutes show no disposition for this item.</p>
      )}
    </div>
  );
}

/** R5.5.3: grouped meeting → agenda item, never a flat chronological list. */
function Evidence({ hits }: { hits: TranscriptHit[] }) {
  const meetings = new Map<string, { label: string; items: Map<string, TranscriptHit[]> }>();
  for (const h of hits) {
    const mk = String(h.meeting_id ?? h.video_id);
    const label = `${meetingDate(h.meeting_date ?? h.upload_date ?? "", "long")}${
      h.body ? ` · ${h.body}` : ""
    }`;
    const m = meetings.get(mk) ?? { label, items: new Map() };
    const ik = String(h.agenda_item_id ?? "none");
    (m.items.get(ik) ?? m.items.set(ik, []).get(ik)!).push(h);
    meetings.set(mk, m);
  }
  return (
    <section className={s.block} aria-labelledby="ev-said">
      <header className={s.blockHead}>
        <h2 id="ev-said" className={s.blockTitle}>
          What was said
        </h2>
        <ProvenanceMark kind="transcript" compact />
      </header>
      <p className={s.blockWhy}>
        Machine transcription. Speaker names are inferred from voice and can be wrong.
      </p>
      {[...meetings.entries()].map(([mk, m]) => (
        <div key={mk} className={s.meeting}>
          <h3 className={s.meetingHead}>
            {hits.find((h) => String(h.meeting_id ?? h.video_id) === mk)?.meeting_id ? (
              <Link href={`/meeting/${hits.find((h) => String(h.meeting_id) === mk)!.meeting_id}`}>
                {m.label}
              </Link>
            ) : (
              m.label
            )}
          </h3>
          {[...m.items.entries()].map(([ik, group]) => (
            <div key={ik} className={s.item}>
              {ik !== "none" ? (
                <Link href={`/item/${ik}`} className={s.itemHead}>
                  {group[0].code ? <span className={s.code}>{group[0].code}</span> : null}
                  {shortTitle(group[0].item, 90) || "(untitled item)"}
                </Link>
              ) : (
                <span className={s.itemNone}>Not matched to an agenda item</span>
              )}
              <ul className={s.quotes}>
                {group.map((h) => (
                  <li key={h.id} className={s.quote}>
                    <Quote hit={h} />
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      ))}
    </section>
  );
}

function Quote({ hit }: { hit: TranscriptHit }) {
  const player = usePlayer();
  return (
    <>
      <div className={s.quoteTop}>
        {/* Drawn with the certainty behind it, not as a flat label (R2.3),
            and the answer above was written under the same rule: web/agent.py
            marks the weak ones in the brief and COMPOSE refuses to attribute
            those by name. So the prose and the evidence agree about how much
            the archive knows, which they did not when this was a string. */}
        <span className={s.who}>
          <SpeakerChip {...speakerOf(hit)} size="sm" />
        </span>
        {/* A cited name is the one a reader is most likely to check, and the
            most costly to have wrong. Readers see nothing (R9.1). */}
        <DisputePassage hit={hit} />
        <button
          type="button"
          className={s.at}
          onClick={() =>
            player.play(
              { videoId: hit.video_id, title: hit.title ?? "", href: hit.meeting_id ? `/meeting/${hit.meeting_id}` : undefined },
              hit.start,
              true,
            )
          }
        >
          ▸ {clock(hit.start)}
        </button>
        {hit.phase ? <span className={s.phase}>{phaseLabel(hit.phase)}</span> : null}
      </div>
      <p className={s.said}>{hit.text}</p>
    </>
  );
}
