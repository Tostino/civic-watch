"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { Answer, Lookups, TOOL_LABEL, type Lookup } from "./Answer";
import type { AskResult, AskStage } from "@/lib/types";
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
  const router = useRouter();

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

  /* The answer has a URL of its own, so go and be at it: the address bar then
   * holds the thing a reader would send somebody, which is the whole feature —
   * no button to find, no second way to get the link, nothing to explain.
   *
   * REPLACE, never push. `?q=` behind the Back button makes Back a paid agent
   * run against the daily cap, which is not what Back is for.
   *
   * Deliberately its OWN effect rather than a line in the `answer` handler.
   * Doing it there put `router` in `open`'s dependency list, and `open` is a
   * dependency of the effect above — so any render that changed the router's
   * identity would tear down the stream and open a new one, spending another
   * paid run. Reacting to the id instead keeps that list empty, and re-running
   * this is free.
   *
   * The answer stays rendered below while the navigation is in flight, and
   * stays for good if `id` is absent: a save that failed costs the reader a
   * permalink and must not also cost them the answer they waited out a run
   * for. */
  const answerId = run.result?.id;
  useEffect(() => {
    if (answerId) router.replace(`/ask/${answerId}`);
  }, [answerId, router]);

  const { stages, result, error, running } = run;

  return (
    <div className={s.wrap}>
      <form
        className={s.form}
        onSubmit={(e) => {
          e.preventDefault();
          const t = question.trim();
          if (!t || running) return;
          // The URL holds the question for as long as the run lasts, so a
          // reload part way through asks the same thing again rather than
          // losing it (R4.2). It is not the URL anyone shares — when the
          // answer lands we go to /ask/<id>, which is.
          //
          // replaceState rather than a route change: navigating would remount
          // and tear down the stream we are about to open.
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

      {/* Usually on screen for the length of one navigation: the `answer`
          event sends us to /ask/<id>, which renders the same answer from the
          row. It is not dead code — it is what a reader sees when the save
          failed and there is no row to go to. */}
      {result ? <Answer r={result} /> : null}

      {!stages.length && !result && !error ? <Examples /> : null}
    </div>
  );
}

/* ------------------------------------------------------------- what it did */

function Trace({ stages, running }: { stages: AskStage[]; running: boolean }) {
  const calls = stages.filter((x) => x.stage === "tool");
  const done = new Map(
    stages.filter((x) => x.stage === "tool_done").map((x) => [x.id, x]),
  );
  const lookups: Lookup[] = calls.map((c) => ({
    id: c.id,
    name: c.name ?? "",
    args: c.args,
    // Undefined and false are not the same answer: a call with no `tool_done`
    // yet is still running, one that came back with ok=false was rejected.
    ok: done.has(c.id) ? (done.get(c.id)!.ok ?? false) : null,
  }));
  const last = stages[stages.length - 1];
  return (
    <section className={s.trace} aria-label="What the agent did" aria-live="polite">
      <h2 className={s.traceHead}>
        {running ? <span className={s.pulse} aria-hidden /> : null}
        {running
          ? currently(last)
          : `${calls.length} lookup${calls.length === 1 ? "" : "s"}`}
      </h2>
      <div className={s.traceList}>
        <Lookups lookups={lookups} />
      </div>
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
      {/* Offered here because this is the page where a reader is already
          asking the archive a question, and the answer to "can I do this from
          my own tools" is yes. The instructions live on /about with the rest
          of what this archive is; one sentence is what belongs on this page. */}
      <p className={s.own}>
        You can also point your own assistant at this archive and search it from
        there. <Link href="/about">How to connect one</Link>.
      </p>
    </div>
  );
}
