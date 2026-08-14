"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";

import { OutcomeBadge } from "@/components/OutcomeBadge";
import { ProvenanceMark } from "@/components/ProvenanceMark";
import { usePlayer } from "@/components/player/PlayerProvider";
import { clock, meetingDate, phaseLabel, shortBody, shortTitle } from "@/lib/format";
import type { AskResult, AskStage, RecordHit, TranscriptHit } from "@/lib/types";
import s from "./AskView.module.css";

/**
 * `/ask` — §5.5. The agent is not a separate product; it is an automated
 * traversal of the same graph the rest of the site walks, which is why its two
 * citation types are the two sources and its evidence renders with the same
 * components (UI_PLAN §4).
 *
 * What streams is the agent's ACTUAL tool calls (R5.5.1). Four fixed captions
 * would have been easier and would have been a lie: under D9 there is no fixed
 * pipeline to caption. "search_record: school zone speed cameras → 0 items"
 * tells a reader something a progress bar cannot — that the archive was asked,
 * and did not have it.
 *
 * One state object, not five. The reset when a new question is asked has to be
 * atomic, and the page is mounted with `key={q}` so arriving at a different
 * question remounts rather than reconciling — which is what keeps the effect
 * below free of any synchronous setState.
 */
type Run = {
  stages: AskStage[];
  result: AskResult | null;
  error: string | null;
  running: boolean;
};

const fresh = (running: boolean): Run => ({
  stages: [], result: null, error: null, running,
});

export function AskView({ q }: { q: string }) {
  const [question, setQuestion] = useState(q);
  const [run, setRun] = useState<Run>(() => fresh(Boolean(q.trim())));
  const es = useRef<EventSource | null>(null);

  /** Opens the stream. Every state change from here is asynchronous, in an
   *  event handler — the effect itself sets nothing. */
  const open = useCallback((text: string) => {
    es.current?.close();
    const src = new EventSource(`/api/ask?q=${encodeURIComponent(text)}`);
    es.current = src;
    src.addEventListener("stage", (e) => {
      const stage = JSON.parse((e as MessageEvent).data) as AskStage;
      setRun((prev) => ({ ...prev, stages: [...prev.stages, stage] }));
    });
    src.addEventListener("answer", (e) => {
      const result = JSON.parse((e as MessageEvent).data) as AskResult;
      setRun((prev) => ({ ...prev, result, running: false }));
      src.close();
    });
    src.addEventListener("error", (e) => {
      // Two different failures arrive here: our own `error` event, which has a
      // body, and the browser's connection error, which does not.
      const data = (e as MessageEvent).data;
      const error = data
        ? ((JSON.parse(data).error as string) ?? "failed")
        : "The connection dropped.";
      setRun((prev) => (prev.result ? prev : { ...prev, error, running: false }));
      src.close();
    });
    return src;
  }, []);

  useEffect(() => {
    if (!q.trim()) return;
    const src = open(q);
    return () => src.close();
  }, [q, open]);

  const { stages, result, error, running } = run;

  return (
    <div className={s.wrap}>
      <form
        className={s.form}
        onSubmit={(e) => {
          e.preventDefault();
          const t = question.trim();
          if (!t || running) return;
          // The URL is the question (R4.2), so an answer can be sent to
          // somebody. replaceState rather than a route change: navigating
          // would remount and tear down the stream we are about to open.
          window.history.replaceState(null, "", `/ask?q=${encodeURIComponent(t)}`);
          setRun(fresh(true));
          open(t);
        }}
      >
        <label className={s.label} htmlFor="ask-q">
          Ask the archive
        </label>
        <div className={s.row}>
          <input
            id="ask-q"
            className={s.input}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="What was decided about the school zone speed cameras?"
            autoComplete="off"
            aria-describedby="ask-hint"
          />
          <button type="submit" className={s.go} disabled={running || !question.trim()}>
            {running ? "Working…" : "Ask"}
          </button>
        </div>
        <p id="ask-hint" className={s.hint}>
          Answers come only from what the archive holds, and it puts a reference on every
          claim. It searches the published record and the recordings, and it will say
          when they do not settle the question.
        </p>
      </form>

      {stages.length ? <Trace stages={stages} running={running} /> : null}

      {error ? (
        <p className={s.error}>
          {error}{" "}
          <button
            type="button"
            className={s.retry}
            onClick={() => {
              setRun(fresh(true));
              open(question);
            }}
          >
            Try again
          </button>
        </p>
      ) : null}

      {result ? <Answer r={result} /> : null}

      {!stages.length && !result && !error ? <Examples /> : null}
    </div>
  );
}

/* ------------------------------------------------------------- what it did */

const TOOL_LABEL: Record<string, string> = {
  search_transcript: "searched the recordings",
  search_record: "searched the published record",
  get_item: "opened an agenda item",
  get_case: "followed a case across meetings",
  get_meeting: "opened a meeting’s agenda",
};

function Trace({ stages, running }: { stages: AskStage[]; running: boolean }) {
  const calls = stages.filter((x) => x.stage === "tool");
  const done = new Map(
    stages.filter((x) => x.stage === "tool_done").map((x) => [x.id, x]),
  );
  const last = stages[stages.length - 1];
  return (
    <section className={s.trace} aria-label="What the agent did" aria-live="polite">
      <h2 className={s.traceHead}>
        {running ? <span className={s.pulse} aria-hidden /> : null}
        {running ? currently(last) : `${calls.length} lookups`}
      </h2>
      <ol className={s.calls}>
        {calls.map((c, i) => {
          const d = done.get(c.id);
          return (
            <li key={c.id ?? i} className={`${s.call} ${d ? "" : s.pending}`}>
              <span className={s.callWhat}>{TOOL_LABEL[c.name ?? ""] ?? c.name}</span>
              <code className={s.callArgs}>{args(c.args)}</code>
              {d ? (
                <span className={d.ok ? s.callOk : s.callBad}>
                  {d.ok ? "✓" : "rejected"}
                </span>
              ) : (
                <span className={s.callWait}>…</span>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function currently(x: AskStage | undefined): string {
  switch (x?.stage) {
    case "tool":
      return TOOL_LABEL[x.name ?? ""] ?? "looking something up";
    case "answering":
      return "writing the answer";
    case "checking":
      return "checking the references";
    default:
      return "deciding what to look up";
  }
}

const args = (a: Record<string, unknown> | undefined) =>
  Object.entries(a ?? {})
    .map(([k, v]) => (k === "query" ? `“${v}”` : `${k}=${v}`))
    .join(" · ");

/* -------------------------------------------------------------- the answer */

function Answer({ r }: { r: AskResult }) {
  const byId = new Map(r.evidence.map((e) => [e.id, e]));
  const items = new Map(r.record.map((i) => [i.id, i]));
  const empty = !r.evidence.length && !r.record.length;

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
        {r.stopped ? <> It stopped searching early, so this may not be everything.</> : null}
      </footer>
    </div>
  );
}

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
      title={`${hit.speaker ?? "Unidentified speaker"} · ${clock(hit.start)} — play`}
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
  const who =
    !hit.speaker || hit.speaker === "(exchange)" ? "Several speakers" : hit.speaker;
  return (
    <>
      <div className={s.quoteTop}>
        <span className={s.who}>{who}</span>
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

function Examples() {
  const qs = [
    "What was decided about the school zone speed cameras?",
    "What happened to the Evans County Line 80 rezoning?",
    "What did people say about the license plate cameras?",
    "How has the board handled impact fees since 2023?",
  ];
  return (
    <div className={s.examples}>
      <h2 className={s.examplesHead}>Questions this can answer</h2>
      {/* This used to end "…and citations it cannot support are removed before
          you see the answer", which is true and does not belong here. It is a
          failure mode dressed as a feature: before the reader has seen
          anything it tells them the thing invents citations, which invites the
          obvious next question about everything else in the answer. The check
          is real and stays; where it belongs is the footer, stated when it has
          actually fired and removed something. */}
      <ul className={s.tries}>
        {qs.map((x) => (
          <li key={x}>
            <Link href={`/ask?q=${encodeURIComponent(x)}`} className={s.try}>
              {x}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
