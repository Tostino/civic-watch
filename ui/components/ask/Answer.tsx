"use client";

import { Fragment } from "react";
import Link from "next/link";

import { OutcomeBadge } from "@/components/OutcomeBadge";
import { ProvenanceMark } from "@/components/ProvenanceMark";
import { SpeakerChip } from "@/components/SpeakerChip";
import { DisputePassage } from "@/components/admin/DisputePassage";
import { usePlayer } from "@/components/player/PlayerProvider";
import {
  clock,
  meetingDate,
  phaseLabel,
  recordingName,
  sessionLabel,
  shortBody,
  shortTitle,
} from "@/lib/format";
import type { AskResult, RecordHit, TranscriptHit } from "@/lib/types";
import s from "./Answer.module.css";

/**
 * An answer and the evidence under it.5, and the only description of what
 * one looks like.
 *
 * It renders in two places and must be the same thing in both: live on `/ask`
 * as the stream closes, and on `/ask/<id>`, which is a run the server kept so
 * that it could be sent to somebody. A reader following a shared link is being
 * shown what the sender saw, so a second implementation of this — even a
 * simplified "read-only" one — would quietly make that untrue.
*/
export function Answer({ r }: { r: AskResult }) {
  const byId = new Map(r.evidence.map((e) => [e.id, e]));
  const items = new Map(r.record.map((i) => [i.id, i]));
  const empty = !r.evidence.length && !r.record.length;
  const missing = (r.missing?.passages ?? 0) + (r.missing?.items ?? 0);
  /* Numbered once for the whole answer, because the prose and the two lists
     under it have to agree about what `[4]` is. */
  const marks = marksOf(r.answer, byId, items);

  return (
    <div className={s.answer}>
      <article className={s.prose}>
        <Prose marks={marks} />
      </article>

      {/* no answer without evidence, and the empty result is designed
          rather than treated as a failure. */}
      {empty ? (
        <p className={s.nothing}>
          Nothing in the archive was cited for that, which means it did not find
          evidence it was willing to stand behind. It looked at{" "}
          {r.looked_at.items.toLocaleString()} published items and{" "}
          {r.looked_at.passages.toLocaleString()} passages.
        </p>
      ) : null}

      {/* the published record is its own block, above the transcript. */}
      {r.record.length ? (
        <section className={s.block} aria-labelledby="ev-record">
          <header className={s.blockHead}>
            <h2 id="ev-record" className={s.blockTitle}>
              What the county recorded
            </h2>
            {/* `agenda`, not `minutes`, and the same mark /search puts on the
                same block. A row here is an agenda title WITH the minutes
                outcome under it, which is what the agenda mark means; marking
                it `minutes` claimed the title was minuted too. */}
            <ProvenanceMark kind="agenda" compact />
          </header>
          {/* The sentence /search runs under this head, verbatim. Both blocks
              on both pages now say what they are: this one had no line at
              all while the transcript block beside it carried its caveat. */}
          <p className={s.blockWhy}>
            Published agendas and the outcomes the approved minutes recorded, whether
            or not a camera was running.
          </p>
          {/* In the order the answer refers to them: a numbered list that does
              not ascend is a list somebody has to search. */}
          <ul className={s.list}>
            {[...r.record]
              .sort(
                (a, b) =>
                  (marks.item.get(a.id) ?? 1e9) - (marks.item.get(b.id) ?? 1e9),
              )
              .map((i) => (
                <li key={i.id}>
                  <RecordCite item={i} n={marks.item.get(i.id)} />
                </li>
              ))}
          </ul>
        </section>
      ) : null}

      {r.evidence.length ? <Evidence hits={r.evidence} said={marks.said} anchor={marks.anchor} /> : null}

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
            : the answer referred to {r.struck.join(", ")}, which this search never
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
            : what {missing === 1 ? "it pointed" : "they pointed"} at has changed in
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
 * What the agent actually did. Shared for the same reason as the
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

/** One `[N]` or `[item:N]` as the writer typed it. */
type Ref = { item: boolean; id: number };

/** A maximal run of citations with nothing but whitespace between them, and
 *  the punctuation that came after it. */
type Run = { refs: Ref[]; tail: string };

type Part = string | Run | { bold: string };

/** One place in a recording. See `moments`. */
type Moment = { at: TranscriptHit; of: TranscriptHit[] };

/** A numbered reference, as it appears in the prose and in the list below. */
type Mark =
  | { n: number; kind: "item"; item: RecordHit }
  | { n: number; kind: "said"; moment: Moment; where: string }
  | { n: number; kind: "dead"; id: number };

/** Every reference in one run, resolved and numbered. */
type Plan = { marks: Mark[]; tail: string };

/** The whole answer's references: the prose split around them, what each one
 *  resolved to, and the number to print — on the marker AND on the row it
 *  points at, which is the only thing that makes a number worth reading. */
type Marks = {
  parts: Part[];
  plans: Map<Run, Plan>;
  /** Published item id → its number. */
  item: Map<number, number>;
  /** Passage id → the number of the moment it belongs to. */
  said: Map<number, number>;
  /**
   * The one passage per moment that carries the anchor. A moment can fold
   * three passages and they all print its number, but only one of them may
   * BE `#ref-7` — duplicate ids are invalid and a marker following one lands
   * on whichever the document happens to reach first.
   */
  anchor: Set<number>;
};

const isRun = (p: Part | undefined): p is Run =>
  typeof p === "object" && p !== null && "refs" in p;

/**
 * the two citation types are distinct. `[item:N]` reveals the
 * published record; `[N]` seeks the player to that moment.
*/
function marksOf(
  text: string,
  byId: Map<number, TranscriptHit>,
  items: Map<number, RecordHit>,
): Marks {
  /* Citations and `**bold**` in one pass. The agent is told to write plain
   * prose, and mostly does; when it does not, the asterisks used to reach the
   * page literally — "**What the record shows.**" — which reads as a bug in
   * the archive rather than a slip by the model. Bold is the only markdown
   * honoured: anything more would let the answer style the page. */
  const re = /\[(item:)?(\d{1,7})\]|\*\*(.+?)\*\*/g;
  const parts: Part[] = [];

  /* Text after a run gives up its leading punctuation to it. The full stop
   * belongs to the sentence, not to the gap after a marker: left in the prose
   * it is a floating "." after a space, and — measured on a printed answer — a
   * line can break in that gap and start the next line with it. */
  const pushText = (str: string) => {
    const last = parts[parts.length - 1];
    if (isRun(last) && !last.tail) {
      const stop = /^[.,;:!?)\]}"'’”…]+/.exec(str);
      if (stop) {
        last.tail = stop[0];
        str = str.slice(stop[0].length);
      }
    }
    if (str) parts.push(str);
  };

  let at = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    const between = text.slice(at, m.index);
    at = m.index + m[0].length;
    if (m[3] !== undefined) {
      pushText(between);
      parts.push({ bold: m[3] });
      continue;
    }
    const ref = { item: Boolean(m[1]), id: Number(m[2]) };
    const last = parts[parts.length - 1];
    if (isRun(last) && !last.tail && /^[ \t]*$/.test(between)) last.refs.push(ref);
    else {
      pushText(between);
      parts.push({ refs: [ref], tail: "" });
    }
  }
  pushText(text.slice(at));

  return number(parts, byId, items);
}

/**
 * MOST OF A REFERENCE'S "SEVERAL MOMENTS" ARE ONE MOMENT.
*/
const ONE_PLACE = 15;

function moments(hits: TranscriptHit[]): Moment[] {
  const out: Moment[] = [];
  for (const h of hits) {
    const run = out[out.length - 1];
    const prev = run?.of[run.of.length - 1];
    // Sorted ascending, so a negative gap is an overlap and folds too.
    if (prev && h.start - prev.end < ONE_PLACE) run.of.push(h);
    else out.push({ at: h, of: [h] });
  }
  return out;
}

/**
 * Numbers, in the order a reader meets them.
*/
function number(
  parts: Part[],
  byId: Map<number, TranscriptHit>,
  items: Map<number, RecordHit>,
): Marks {
  const plans = new Map<Run, Plan>();
  const item = new Map<number, number>();
  const said = new Map<number, number>();
  const anchor = new Set<number>();
  let next = 1;
  for (const p of parts) {
    if (!isRun(p)) continue;
    const marks: Mark[] = [];
    const dead: number[] = [];
    const hits: TranscriptHit[] = [];
    const recs: RecordHit[] = [];
    for (const r of p.refs) {
      if (r.item) {
        const it = items.get(r.id);
        if (it && !recs.some((x) => x.id === it.id)) recs.push(it);
        continue;
      }
      const h = byId.get(r.id);
      if (h) hits.push(h);
      else dead.push(r.id);
    }
    /* The record before the recording, always, whichever order they were
       typed in: the county's own minutes are the authority and the transcript
       is what was said around them. Numbers are handed out in that
       same order, so they only ever ascend in the prose. */
    for (const it of recs) {
      const n = item.get(it.id) ?? next;
      if (!item.has(it.id)) item.set(it.id, next++);
      marks.push({ n, kind: "item", item: it });
    }
    /* In the order the recording plays, not the order the writer happened to
       type: it cited `[363214] [363212]`, which would have numbered the later
       moment first. */
    hits.sort((a, b) => a.start - b.start);
    for (const mo of moments(hits)) {
      /* A PASSAGE KEEPS THE FIRST NUMBER IT WAS GIVEN, and a moment takes the
         number of any passage in it that already has one. Folding happens per
         sentence — it has to, because a sentence's citations are what tell us
         which stretch that sentence rests on — so the same passage can be
         folded here and standalone there, and keying reuse on the fold's
         first passage alone let a later, longer fold mint a fresh number and
         overwrite what its members already carried: one number in the prose
         with no row left showing it, and a row carrying somebody else's.
         Doing it globally instead was worse in a way that matters more than
         either — it merged a three-and-a-half-minute stretch of one speaker
         into one reference, so a sentence about its last claim dropped the
         reader three minutes before the claim. */
      const seen =
        said.get(mo.at.id) ?? mo.of.map((h) => said.get(h.id)).find((x) => x !== undefined);
      const n = seen ?? next;
      if (seen === undefined) {
        next += 1;
        // The moment's first passage is the row that bears the number and the
        // anchor; the rest are that reference's other quotes and print none of
        // their own. A numbered list in which 9 appears three times is a list
        // somebody has to check twice.
        anchor.add(mo.at.id);
      }
      for (const h of mo.of) if (!said.has(h.id)) said.set(h.id, n);
      marks.push({ n, kind: "said", moment: mo, where: recordingName(mo.at) });
    }
    for (const id of dead) marks.push({ n: 0, kind: "dead", id });
    plans.set(p, { marks, tail: p.tail });
  }
  return { parts, plans, item, said, anchor };
}

function Prose({ marks }: { marks: Marks }) {
  return (
    <div className={s.paras}>
      {marks.parts.map((p, i) =>
        typeof p === "string" ? (
          <Fragment key={`t${i}`}>{p}</Fragment>
        ) : isRun(p) ? (
          <Refs key={`c${i}`} plan={marks.plans.get(p)!} />
        ) : (
          <strong key={`b${i}`}>{p.bold}</strong>
        ),
      )}
    </div>
  );
}

/** One reference: everything the writer cited in one breath, as numbers. */
function Refs({ plan }: { plan: Plan }) {
  const player = usePlayer();
  const els: React.ReactNode[] = [];
  for (const mk of plan.marks) {
    if (mk.kind === "item")
      els.push(
        <a
          key={`i${mk.item.id}`}
          href={`#ref-${mk.n}`}
          className={s.mark}
          title={`${mk.n}. ${shortTitle(mk.item.title, 90) || "the published record"}: the county's record`}
        >
          [{mk.n}]
        </a>,
      );
    else if (mk.kind === "said") {
      const hit = mk.moment.at;
      /* EVERYONE in the stretch, not whoever starts it. A fold is 15 seconds
         of silence or less, which can span a change of speaker, and one name
         over two people is the kind of quiet misattribution this archive is
         careful about everywhere else. */
      const who = [
        ...new Set(
          mk.moment.of.map((h) => h.speaker_display ?? h.speaker ?? "Unidentified speaker"),
        ),
      ];
      const said =
        who.length === 1 ? who[0]
        : who.length === 2 ? `${who[0]} and ${who[1]}`
        : `${who[0]} and ${who.length - 1} others`;
      const ran = mk.moment.of[mk.moment.of.length - 1].end - hit.start;
      const long = ran >= 90 ? `, ${Math.round(ran / 60)} min` : "";
      const what = `${mk.n}. ${said} · ${mk.where ? `${mk.where}, ` : ""}${clock(hit.start)}${long} · play`;
      els.push(
        <button
          key={`s${hit.id}`}
          type="button"
          className={`${s.mark} ${s.markSaid}`}
          title={what}
          aria-label={what}
          onClick={() =>
            player.play(
              {
                videoId: hit.video_id,
                title: hit.title ?? "",
                href: hit.meeting_id ? `/meeting/${hit.meeting_id}` : undefined,
              },
              hit.start,
              true,
            )
          }
        >
          {/* THE MARK, not the colour. Which of the two kinds a reference is
              matters most where colour is not available — printed, in high
              contrast, or to a reader who does not see the difference between
              the record's blue and the player's orange — and this archive
              says so about itself in ProvenanceMark: the primary signal is
              never a colour. ▸ is what "play" looks like everywhere else on
              this site, and one character says "this is a recording" in a way
              a number cannot. */}
          {"["}
          <span aria-hidden className={s.markPlay}>
            ▸
          </span>
          {mk.n}
          {"]"}
        </button>,
      );
    } else
      /* Should never render: check() strips unverifiable citations before the
         answer leaves the server. Kept visible rather than silent so that if
         one ever does get through, it is obvious rather than disguised. */
      els.push(
        <span key={`d${mk.id}`} className={s.citeDead}>
          [{mk.id}]
        </span>,
      );
  }

  /* The whole run rides with the punctuation that ends its sentence: markers
     are two or three characters, so there is no reason to let a line break
     inside one reference or before its full stop. */
  return (
    <span className={s.refs}>
      {els}
      {plan.tail}
    </span>
  );
}

/** The number, printed where the thing it points at actually is.
 *
 *  The ANCHOR is the row, not this: `#ref-4` used to land on the number span
 *  inside the row, which meant `.recRow:target` — the highlight that shows a
 *  reader which row they were just sent to — could never match again. */
function RefNo({
  n,
  said = false,
  anchor = true,
}: {
  n: number | undefined;
  said?: boolean;
  anchor?: boolean;
}) {
  if (n === undefined) return null;
  return (
    <span className={`${s.refNo} ${said ? s.refNoSaid : ""}`}>
      {/* No ▸ here, unlike the marker in the prose. The mark is there to say
          which of two kinds a reference is where they are mixed mid-sentence;
          in the list the heading above the row has already said it, and the
          row's own play button carries a ▸ two words later.
          Blank on a row that CONTINUES a reference: one number, one row that
          bears it, and the quotes under it are that reference's own. */}
      {anchor ? n : null}
    </span>
  );
}

function RecordCite({ item, n }: { item: RecordHit; n: number | undefined }) {
  return (
    <div className={s.recRow} id={n === undefined ? undefined : `ref-${n}`}>
      <div className={s.recTop}>
        <RefNo n={n} />
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
      {item.outcome_text ? (
        <p className={s.outcomeText}>{item.outcome_text}</p>
      ) : (
        <p className={s.noOutcome}>The minutes record no outcome for this item.</p>
      )}
    </div>
  );
}

/** grouped meeting → agenda item, never a flat chronological list. */
function Evidence({
  hits,
  said,
  anchor,
}: {
  hits: TranscriptHit[];
  said: Map<number, number>;
  anchor: Set<number>;
}) {
  const meetings = new Map<string, { label: string; items: Map<string, TranscriptHit[]> }>();
  for (const h of hits) {
    const mk = String(h.meeting_id ?? h.video_id);
    /* 17 recordings in the archive have no date and no meeting. Their own
       title is what names them; the alternative was the date this printed
       before anyone looked, which was Monday, January 1, 1900. */
    const when = meetingDate(h.meeting_date ?? h.upload_date ?? "", "long");
    const label =
      (when || shortTitle(h.title, 70) || "Undated recording") +
      (when && h.body ? ` · ${h.body}` : "");
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
                <span className={s.itemNone}>Not located in an agenda item</span>
              )}
              <ul className={s.quotes}>
                {group.map((h) => (
                  <li
                    key={h.id}
                    className={s.quote}
                    /* Same rule as a record row: the anchor is the row a
                       reference resolves to, so landing on it can be seen. */
                    id={anchor.has(h.id) ? `ref-${said.get(h.id)}` : undefined}
                  >
                    <Quote hit={h} n={said.get(h.id)} anchor={anchor.has(h.id)} />
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

function Quote({
  hit,
  n,
  anchor,
}: {
  hit: TranscriptHit;
  n: number | undefined;
  anchor: boolean;
}) {
  const player = usePlayer();
  return (
    <>
      <div className={s.quoteTop}>
        <RefNo n={n} said anchor={anchor} />
        {/* Drawn with the certainty behind it, not as a flat label,
            and the answer above was written under the same rule: web/agent.py
            marks the weak ones in the brief and COMPOSE refuses to attribute
            those by name. So the prose and the evidence agree about how much
            the archive knows, which they did not when this was a string. */}
        <span className={s.who}>
          <SpeakerChip who={hit.who} size="sm" />
        </span>
        {/* A cited name is the one a reader is most likely to check, and the
            most costly to have wrong. Readers see nothing. */}
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
          {/* WHICH RECORDING, on the row a marker resolves to. Rows are
              grouped by MEETING and half of all meeting-days are two
              recordings, so without this a 5:41 from the afternoon session
              sits under the same heading as a 1:57:52 from the morning and
              reads like a mistake. Said only where `sessions` proves the
              meeting has more than one. */}
          ▸ {(hit.sessions ?? 0) > 1 && hit.session_seq != null ? (
            <>
              <span className={s.sess}>
                {sessionLabel(hit.session_seq, hit.sessions).replace(/ session$/, "").toLowerCase()}
              </span>{" "}
            </>
          ) : null}
          {clock(hit.start)}
        </button>
        {hit.phase ? <span className={s.phase}>{phaseLabel(hit.phase)}</span> : null}
      </div>
      <p className={s.said}>
        {hit.turns
          ? hit.turns.map((t) => (
              <span key={t.n} className={s.turn}>
                <span className={s.turnWho}>
                  {`${t.speaker_display ?? t.speaker ?? "Unidentified"}: `}
                </span>
                {t.text}
              </span>
            ))
          : hit.text}
      </p>
    </>
  );
}
