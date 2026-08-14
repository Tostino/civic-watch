"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { getOps, startJob, stopJob, type JobState, type OpsStatus } from "@/lib/admin";
import s from "./OpsScreen.module.css";

/**
 * Every pipeline operation, as the flow it actually is (bin/job.py):
 *
 *   discover -> ingest -> fold in -> identity
 *
 * Each stage's prerequisite is measured from the database and shown beside
 * its button, and the SERVER refuses an out-of-order run with the same
 * measurement - the page greys things out to be kind, not to be the guard.
 * One job at a time; paid jobs are marked and require a second, explicit
 * click that says what it costs.
 *
 * The page's other job is to answer "is it stuck?", which a spinner cannot.
 * A run shows four things a pid does not: which step of how many is running,
 * how long that step has run, how long since anything was written to the log,
 * and - inside a step that runs for half an hour - which stage it announced
 * last. When the answer is yes, Stop is here rather than in a terminal.
 */
/** How long "Starting…" may stand before the runner has to have said so. */
const STARTING_MS = 12_000;

export function OpsScreen() {
  const qc = useQueryClient();
  // What was just asked for, and when. The runner writes its status within a
  // second, so this only bridges the gap between the click and the first
  // status - long enough that a button which did nothing visible for five
  // seconds read as broken.
  const [asked, setAsked] = useState<{ name: string; at: number } | null>(null);
  const { data: ops, isPending, dataUpdatedAt, isFetching, refetch } = useQuery({
    queryKey: ["admin", "ops"],
    queryFn: getOps,
    // A job that runs for two minutes is watched, not glanced at. The idle
    // page must not poll like that, and the window between "Run" clicked and
    // the runner writing its first status is the one place a second matters.
    // This reads `asked` rather than the derived `starting` below it: the
    // callback runs during useQuery, where a const declared after it is not
    // yet initialised.
    refetchInterval: (q) => {
      const d = q.state.data;
      if (asked && !d?.running && Date.now() - asked.at < STARTING_MS) return 1000;
      if (!d) return 5000;
      if (d.running) return 2000;
      if (d.fleet_workers) return 5000;
      return 30_000;
    },
    refetchOnWindowFocus: true,
  });
  const run = useMutation({
    mutationFn: ({ name, paid }: { name: string; paid?: boolean }) => startJob(name, paid),
    onMutate: ({ name }) => setAsked({ name, at: Date.now() }),
    onError: () => setAsked(null),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["admin"] }),
  });
  const stop = useMutation({
    mutationFn: stopJob,
    onSettled: () => void qc.invalidateQueries({ queryKey: ["admin"] }),
  });

  const now = useTick(Boolean(ops?.running || ops?.fleet_workers || asked));
  // Derived, not stored: "Starting…" ends when the runner says it started,
  // and in any case after twelve seconds. A button that says Starting for
  // ever is a worse lie than one that says nothing.
  const starting = asked && !ops?.running && now - asked.at < STARTING_MS ? asked.name : null;

  if (isPending || !ops) return <div className={s.state}>Loading operations…</div>;

  const g = ops.gates;
  const jobRunning = kindOf(ops) === "job" && ops.last?.state === "running";
  const busy = Boolean(ops.running) || ops.fleet_workers > 0 || Boolean(starting);
  const live = busy;
  const err = run.error ?? stop.error;
  // `fleet` is the marker for the whole newer half of this endpoint. Say what
  // to do about it rather than quietly showing less than the page promises.
  const stale = !ops.fleet;

  return (
    <div className={s.wrap}>
      <header className={s.head}>
        <div>
          <h1>Operations</h1>
          <p>
            The pipeline as a flow: discover, ingest, fold in, identity. One job runs at a time;
            each step is enabled by what the database says is waiting for it, and refused
            otherwise.
          </p>
        </div>
        <button
          type="button"
          className={s.refresh}
          onClick={() => void refetch()}
          data-busy={isFetching}
          title="Read the pipeline's state again now"
        >
          <span className={s.pip} data-live={live} aria-hidden />
          {isFetching ? "Reading…" : `Read ${brief(secondsSince(dataUpdatedAt, now))}`}
        </button>
      </header>

      {err ? (
        <p className={s.err} role="alert">
          {err.message}
        </p>
      ) : null}

      {stale ? (
        <p className={s.notice}>
          This page reads more than the archive API is sending. Restart{" "}
          <code>web/server.py</code> for step-by-step progress, the stuck check and Stop.
        </p>
      ) : null}

      {jobRunning ? (
        <RunPanel
          ops={ops}
          now={now}
          at={dataUpdatedAt}
          onStop={() => stop.mutate()}
          stopping={stop.isPending}
        />
      ) : starting ? (
        <section className={s.runPanel} aria-label="Starting">
          <p className={s.starting}>
            <span className={s.spinner} aria-hidden /> Starting{" "}
            <strong>{ops.jobs[starting]?.title ?? starting}</strong>…
          </p>
        </section>
      ) : kindOf(ops) === "rederive" ? (
        <section className={s.runPanel} aria-label="Running now">
          <p className={s.starting}>
            <span className={s.spinner} aria-hidden /> The label propagation is running. It holds
            the one-job-at-a-time lock. Watch it on the <Link href="/admin">queues page</Link>.
          </p>
        </section>
      ) : ops.last ? (
        <LastRun ops={ops} />
      ) : null}

      {ops.fleet_workers > 0 && ops.fleet ? (
        <FleetPanel fleet={ops.fleet} pending={g.ingest_pending} now={now} at={dataUpdatedAt} />
      ) : null}

      <ol className={s.stages}>
        <li className={s.stage} data-n="1">
          <h2>Discover</h2>
          <div className={s.cards}>
            {/* Both discover steps are always safe, so their line carries
                what is WAITING rather than whether they may run. The portal
                can measure that; the channel sweep cannot know what is new
                until it looks, and says what it holds instead of inventing a
                number. */}
            <JobCard
              name="portal_sweep"
              ops={ops}
              busy={busy}
              starting={starting}
              gate={{
                ok: true,
                line: g.portal
                  ? g.portal.no_agenda > 0
                    ? `${count(g.portal.no_agenda, "meeting")} scheduled ahead with no agenda posted yet. The county posts them days before.`
                    : `Every one of the ${count(g.portal.upcoming, "meeting")} scheduled ahead already has its agenda.`
                  : "Always safe to run. The county posts agendas days ahead.",
              }}
              onRun={(n, p) => run.mutate({ name: n, paid: p })}
            />
            <JobCard
              name="video_sweep"
              ops={ops}
              busy={busy}
              starting={starting}
              gate={{
                ok: true,
                line: g.catalog
                  ? `${count(g.catalog.videos, "recording")} catalogued`
                    + (g.catalog.unplaced
                      ? `, ${g.catalog.unplaced} of them not placed on a meeting. `
                      : ". ")
                    + "The sweep adds whatever the channel has posted since."
                  : "Always safe to run. Cataloging does not download anything.",
              }}
              onRun={(n, p) => run.mutate({ name: n, paid: p })}
            />
          </div>
        </li>

        <li className={s.stage} data-n="2">
          <h2>Ingest</h2>
          <div className={s.cards}>
            <article className={s.card} data-state={ops.fleet_workers ? "running" : undefined}>
              <header className={s.cardHead}>
                <h3>Run the ingest fleet</h3>
                {ops.fleet_workers ? (
                  <span className={s.runningPill}>
                    <span className={s.spinner} aria-hidden />
                    {ops.fleet_workers} workers up
                  </span>
                ) : null}
              </header>
              <p className={s.why}>
                Download, diarize and transcribe whatever the video sweep found. Multi-hour on the
                GPUs; workers survive this page closing, and re-running is always safe.
              </p>
              <p className={s.gateLine} data-tone={g.ingest_pending.total > 0 ? "ok" : "idle"}>
                <span className={s.dot} aria-hidden />
                {g.ingest_pending.total > 0
                  ? `Waiting: ${g.ingest_pending.to_download} to download, ` +
                    `${g.ingest_pending.to_diarize} to diarize, ` +
                    `${g.ingest_pending.to_transcribe} to transcribe.`
                  : "Nothing is pending ingest. The video sweep (step 1) is what feeds this."}
                {/* An errored recording is not pending and never will be
                    without a person: it is excluded from every queue, so
                    without this line it is invisible on the one page that
                    should account for the fleet. */}
                {ops.fleet?.counts.errors
                  ? ` ${count(ops.fleet.counts.errors, "recording")} stopped with an error and no queue will retry them.`
                  : null}
              </p>
              <div className={s.actions}>
                <button
                  type="button"
                  className={s.run}
                  disabled={busy || g.ingest_pending.total === 0 || run.isPending}
                  onClick={() => run.mutate({ name: "fleet" })}
                >
                  {starting === "fleet" ? "Starting…" : "Start the fleet"}
                </button>
              </div>
            </article>
          </div>
        </li>

        <li className={s.stage} data-n="3">
          <h2>Fold into the archive</h2>
          <div className={s.cards}>
            <JobCard
              name="fold_in"
              ops={ops}
              busy={busy}
              starting={starting}
              /* The count is what TRIGGERS this job, not what it does. It
                 runs bin/catch_up.sh, a fixed chain, and only the segment
                 stage is scoped to the pending recording: a run with one
                 recording waiting took 23 minutes, 15 of them in the paid
                 naming pass over 150 voices archive-wide. Reporting only the
                 trigger on a button marked "calls the paid model" understates
                 the money as well as the time. */
              gate={{
                ok: g.fold_pending > 0 && g.llm_key,
                line:
                  g.fold_pending === 0
                    ? "Every transcribed recording is already folded in. Ingest (step 2) is what feeds this."
                    : !g.llm_key
                      ? `${count(g.fold_pending, "recording")} ${be(g.fold_pending)} waiting, but the server holds no inference key.`
                      : `${count(g.fold_pending, "transcribed recording")} ${be(g.fold_pending)} waiting to be segmented. `
                        + "The rest of the chain re-runs over the whole archive — identity, up to 150 paid naming calls, then the index — and takes most of the time.",
              }}
              onRun={(n, p) => run.mutate({ name: n, paid: p })}
            />
          </div>
        </li>

        <li className={s.stage} data-n="4">
          <h2>Identity</h2>
          <div className={s.cards}>
            <article className={s.card}>
              <header className={s.cardHead}>
                <h3>Propagate your labels</h3>
              </header>
              <p className={s.why}>
                Free and local, with a measured diff and a revert. Lives on the{" "}
                <Link href="/admin">queues page</Link>, next to the labels it propagates.
              </p>
              {/* The one step here with a number available and none shown. It
                  runs from the queues page, but a page that accounts for the
                  pipeline should say what is waiting for every step of it,
                  including the step it does not host. */}
              {g.labels_pending !== undefined ? (
                <p className={s.gateLine} data-tone={g.labels_pending > 0 ? "ok" : "idle"}>
                  <span className={s.dot} aria-hidden />
                  {g.labels_pending > 0
                    ? `${count(g.labels_pending, "label")} written since the last run, waiting to be propagated.`
                    : "Every label has been propagated since it was written."}
                </p>
              ) : null}
            </article>
            <JobCard
              name="name_chain"
              ops={ops}
              busy={busy}
              starting={starting}
              gate={{
                ok: g.llm_key,
                line: g.llm_key
                  ? `${count(g.unnamed_voices, "voice")} ${g.unnamed_voices === 1 ? "carries" : "carry"} no name; the model reads the transcript for the ones the text can reach.`
                  : "The server holds no inference key. Restart it with env.local.sh sourced.",
              }}
              onRun={(n, p) => run.mutate({ name: n, paid: p })}
            />
          </div>
        </li>
      </ol>
    </div>
  );
}

/* --------------------------------------------------------------- the run
 *
 * What is running, how far in, and whether it is moving. The three clocks are
 * seeded from the server's measurement and ticked here, so they read as live
 * without ever showing the difference between two machines' clocks.
 */
function RunPanel({
  ops,
  now,
  at,
  onStop,
  stopping,
}: {
  ops: OpsStatus;
  now: number;
  at: number;
  onStop: () => void;
  stopping: boolean;
}) {
  const job = ops.last!;
  const [confirmStop, setConfirmStop] = useState(false);
  const plan = ops.jobs[job.job]?.steps ?? [];
  const done = job.steps ?? [];
  // step_index is written before the step starts; older status files predate
  // it, and there the number of finished steps is the same answer.
  const at_step = job.step_index ?? done.length;
  const total = plan.length || job.step_count || done.length + 1;
  const elapsed = tick(ops.elapsed, at, now);
  const stepElapsed = tick(ops.step_elapsed, at, now);
  const quiet = tick(ops.log_age, at, now);

  return (
    <section className={s.runPanel} data-state="running" aria-label="Running now">
      <header className={s.runHead}>
        <h2>
          <span className={s.spinner} aria-hidden />
          {ops.jobs[job.job]?.title ?? job.job}
        </h2>
        <span className={s.clock} role="status">
          {clock(elapsed)}
        </span>
        {confirmStop ? (
          <span className={s.confirmRow}>
            <button type="button" className={s.stop} onClick={onStop} disabled={stopping}>
              {stopping ? "Stopping…" : "Yes — stop it"}
            </button>
            <button type="button" className={s.ghost} onClick={() => setConfirmStop(false)}>
              Keep running
            </button>
          </span>
        ) : (
          <button type="button" className={s.ghost} onClick={() => setConfirmStop(true)}>
            Stop
          </button>
        )}
      </header>

      {confirmStop ? (
        <p className={s.warn}>
          Stopping ends the step that is running now. Work already written stays written; the rest
          of the job does not run. Every job is safe to run again.
        </p>
      ) : null}

      {/* Done, then the step in flight. A one-step job would otherwise show an
          empty bar for half an hour, which reads as nothing happening. */}
      <div className={s.bar} aria-hidden>
        <span style={{ width: `${(done.length / Math.max(total, 1)) * 100}%` }} />
        <span className={s.inFlight} style={{ width: `${(1 / Math.max(total, 1)) * 100}%` }} />
      </div>
      <p className={s.progressLine}>
        Step {Math.min(at_step + 1, total)} of {total}
        {job.step_say || job.step ? " · " : null}
        {job.step_say ?? null}
      </p>

      <ol className={s.steps}>
        {plan.map((st, i) => {
          const rec = done[i];
          const state: StepState = rec
            ? rec.rc === 0
              ? "done"
              : "noted"
            : i === at_step
              ? "running"
              : "waiting";
          return (
            <li key={st.cmd + i} className={s.step} data-state={state}>
              <span className={s.mark} aria-hidden />
              <span className={s.stepSay}>
                {st.say}
                <span className={s.cmd}>{st.cmd}</span>
                {state === "running" && ops.log_phase ? (
                  <span className={s.phase}>now: {ops.log_phase}</span>
                ) : null}
              </span>
              <span className={s.stepTime}>
                {rec ? (
                  <>
                    {secs(rec.seconds)}
                    {rec.rc !== 0 ? <span className={s.rc}> rc {rec.rc}</span> : null}
                  </>
                ) : state === "running" ? (
                  clock(stepElapsed)
                ) : null}
              </span>
            </li>
          );
        })}
      </ol>

      <Heartbeat quiet={quiet} />
      <LogView lines={ops.log_tail} />
    </section>
  );
}

/**
 * The stuck test, in one line. A running pid proves nothing; a log that has
 * not moved in twenty minutes proves something. It stays a measurement rather
 * than a verdict, because several steps are legitimately silent for minutes -
 * the model naming pass writes nothing between batches - and a page that
 * cried stuck at those would be ignored by the time it was right.
 */
function Heartbeat({ quiet }: { quiet: number | null }) {
  if (quiet == null) return null;
  const tone = quiet < 90 ? "ok" : quiet < 600 ? "wait" : "no";
  return (
    <p className={s.heartbeat} data-tone={tone} role="status">
      <span className={s.dot} aria-hidden />
      {quiet < 90 ? (
        <>Output {ago(quiet)}.</>
      ) : quiet < 600 ? (
        <>No output for {span(quiet)}. Some steps are quiet while they work.</>
      ) : (
        <>No output for {span(quiet)}. Stop the job if it is stuck.</>
      )}
    </p>
  );
}

/** The log, pinned to the bottom while it is running - which is what anyone
 *  watching a log wants, and what this page did not do. Scrolling up releases
 *  the pin, so a line you are reading stays where you are reading it. */
function LogView({ lines }: { lines: string[] }) {
  const ref = useRef<HTMLPreElement>(null);
  const [follow, setFollow] = useState(true);
  const [tall, setTall] = useState(false);
  const [copied, setCopied] = useState(false);
  const text = lines.join("");

  useEffect(() => {
    const el = ref.current;
    if (el && follow) el.scrollTop = el.scrollHeight;
  }, [text, follow, tall]);

  if (!lines.length) return null;
  return (
    <div className={s.logWrap}>
      <div className={s.logBar}>
        <span className={s.logTitle}>logs/job.log</span>
        <button
          type="button"
          className={s.ghost}
          data-on={follow}
          aria-pressed={follow}
          onClick={() => setFollow((f) => !f)}
          title="Keep the newest line in view"
        >
          {follow ? "Following" : "Follow"}
        </button>
        <button type="button" className={s.ghost} onClick={() => setTall((t) => !t)}>
          {tall ? "Shorter" : "Taller"}
        </button>
        <button
          type="button"
          className={s.ghost}
          onClick={() => {
            void navigator.clipboard?.writeText(text).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 2000);
            });
          }}
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre
        ref={ref}
        className={s.log}
        data-tall={tall}
        tabIndex={0}
        onScroll={(e) => {
          const el = e.currentTarget;
          setFollow(el.scrollHeight - el.scrollTop - el.clientHeight < 24);
        }}
      >
        {text}
      </pre>
    </div>
  );
}

/* ------------------------------------------------------------- the fleet
 *
 * Six workers on two GPUs, hours per recording. "6 workers up" says they
 * exist; what says they are working is which recording each one holds, for
 * how long, and the queue emptying behind them.
 */
function FleetPanel({
  fleet: f,
  pending,
  now,
  at,
}: {
  fleet: NonNullable<OpsStatus["fleet"]>;
  pending: OpsStatus["gates"]["ingest_pending"];
  now: number;
  at: number;
}) {
  const c = f.counts;
  const quiet = tick(f.log_age, at, now);
  const pct = (n: number) => (c.total ? (n / c.total) * 100 : 0);

  return (
    <section className={s.runPanel} data-state="fleet" aria-label="The ingest fleet">
      <header className={s.runHead}>
        <h2>
          <span className={s.spinner} aria-hidden />
          The ingest fleet is working
        </h2>
        <span className={s.workers}>
          {f.workers.map((w) => (
            <span key={w.name} className={s.worker} data-kind={w.kind}>
              {w.name}
            </span>
          ))}
        </span>
      </header>

      <div className={s.bar} data-stacked aria-hidden>
        <span className={s.tr} style={{ width: `${pct(c.transcribed)}%` }} />
        <span className={s.di} style={{ width: `${pct(c.diarized - c.transcribed)}%` }} />
        <span className={s.dl} style={{ width: `${pct(c.downloaded - c.diarized)}%` }} />
      </div>
      <p className={s.progressLine}>
        {c.transcribed.toLocaleString()} of {c.total.toLocaleString()} recordings transcribed ·{" "}
        {pending.to_download} to download, {pending.to_diarize} to diarize,{" "}
        {pending.to_transcribe} to transcribe
        {c.errors ? ` · ${c.errors} errored` : null}
      </p>

      {f.in_flight.length ? (
        <ul className={s.flight}>
          {f.in_flight.map((v) => (
            <li key={v.video_id}>
              <span className={s.worker}>{v.worker}</span>
              <span className={s.flightTitle}>{v.title}</span>
              <span className={s.stepTime}>
                {v.duration ? `${Math.round(v.duration / 60)} min` : null}
                {v.held_for != null ? ` · held ${span(tick(v.held_for, at, now) ?? 0)}` : null}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className={s.gateLine} data-tone="idle">
          <span className={s.dot} aria-hidden />
          No recording is claimed right now. The workers are idle or between items.
        </p>
      )}

      <Heartbeat quiet={quiet} />
    </section>
  );
}

/* --------------------------------------------------------- what just ran */
function LastRun({ ops }: { ops: OpsStatus }) {
  const last = ops.last!;
  const title = ops.jobs[last.job]?.title ?? last.job;
  const seconds = last.steps?.reduce((n, st) => n + st.seconds, 0) ?? 0;
  const tone: Record<JobState, "ok" | "no" | "wait"> = {
    done: "ok",
    running: "wait",
    failed: "no",
    died: "no",
    stopped: "wait",
  };
  return (
    <section className={s.lastRun} data-tone={tone[last.state] ?? "wait"} aria-label="Last run">
      <p role={last.state === "done" ? undefined : "alert"}>
        <span className={s.dot} aria-hidden />
        <strong>{title}</strong>{" "}
        {last.state === "done"
          ? "finished"
          : last.state === "failed"
            ? "failed"
            : last.state === "stopped"
              ? "was stopped"
              : last.state === "died"
                ? "stopped without finishing"
                : last.state}
        {last.finished_at ? ` ${when(last.finished_at)}` : null}
        {seconds ? ` · ${secs(seconds)}` : null}
        {last.state === "failed" && last.step ? (
          <>
            {" — at "}
            <span className={s.mono}>{last.step}</span>. See <code>logs/job.log</code>.
          </>
        ) : null}
        {last.state === "died" ? (
          <>
            {" "}
            See <code>logs/job.log</code>.
          </>
        ) : null}
      </p>
      {last.steps?.length ? (
        <ol className={s.ranSteps}>
          {last.steps.map((st, i) => (
            <li key={st.cmd + i} data-state={st.rc === 0 ? "done" : "noted"}>
              <span className={s.mark} aria-hidden />
              <span>
                {st.say ?? st.cmd}
                {/* Non-zero and the run went on: only the audit steps are
                    allowed that, and it means they found something to look
                    at. Saying the code is honest about which tick is which. */}
                {st.rc !== 0 ? <span className={s.rc}> reported items (rc {st.rc})</span> : null}
              </span>
              <span className={s.stepTime}>{secs(st.seconds)}</span>
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}

/* ------------------------------------------------------------- job cards */
function JobCard({
  name,
  ops,
  busy,
  starting,
  gate,
  onRun,
}: {
  name: string;
  ops: OpsStatus;
  busy: boolean;
  starting: string | null;
  gate: { ok: boolean; line: string };
  onRun: (name: string, paidOk?: boolean) => void;
}) {
  const j = ops.jobs[name];
  const [confirming, setConfirming] = useState(false);
  if (!j) return null;
  const steps = j.steps ?? [];
  const running = kindOf(ops) === "job" && ops.last?.state === "running" && ops.last.job === name;
  const ran = ops.last?.job === name && !running ? ops.last : null;

  return (
    <article
      className={s.card}
      data-state={running ? "running" : gate.ok ? "ready" : "blocked"}
      aria-busy={running}
    >
      <header className={s.cardHead}>
        <h3>{j.title}</h3>
        {j.paid ? <span className={s.paid}>calls the paid model</span> : null}
        {running ? (
          <span className={s.runningPill}>
            <span className={s.spinner} aria-hidden />
            running
          </span>
        ) : null}
      </header>
      <p className={s.why}>{j.why}</p>
      <p className={s.gateLine} data-tone={gate.ok ? "ok" : "idle"}>
        <span className={s.dot} aria-hidden />
        {gate.line}
      </p>

      {steps.length ? (
        <details className={s.plan}>
          <summary>
            {steps.length} {steps.length === 1 ? "step" : "steps"}
          </summary>
          <ol>
            {steps.map((st, i) => (
              <li key={st.cmd + i}>
                {st.say}
                <span className={s.cmd}>{st.cmd}</span>
              </li>
            ))}
          </ol>
        </details>
      ) : null}

      <div className={s.actions}>
        {j.paid && confirming ? (
          <span className={s.confirmRow}>
            <button
              type="button"
              className={s.spend}
              onClick={() => {
                setConfirming(false);
                onRun(name, true);
              }}
            >
              Yes — spend money on this run
            </button>
            <button type="button" className={s.ghost} onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </span>
        ) : (
          <button
            type="button"
            className={s.run}
            disabled={busy || !gate.ok}
            onClick={() => (j.paid ? setConfirming(true) : onRun(name))}
          >
            {starting === name ? "Starting…" : running ? "Running" : j.paid ? "Run…" : "Run"}
          </button>
        )}
        {ran ? (
          <span className={s.ranChip} data-tone={ran.state === "done" ? "ok" : "no"}>
            {ran.state === "done" ? "ran" : ran.state}{" "}
            {ran.finished_at ? when(ran.finished_at) : null}
          </span>
        ) : null}
      </div>
    </article>
  );
}

/* ------------------------------------------------------------- the clock */

type StepState = "done" | "noted" | "running" | "waiting";

/** Which of the two lock holders is running. The server says so; an API that
 *  predates this page does not, and there the sentence it always sent - "job
 *  portal_sweep", "the label propagation" - is the same answer. */
function kindOf(ops: OpsStatus): "job" | "rederive" | null {
  if (ops.running_kind !== undefined) return ops.running_kind;
  if (!ops.running) return null;
  return ops.running.startsWith("job ") ? "job" : "rederive";
}

/** One shared second hand. Every clock on this page reads the same tick, so
 *  they never disagree by a frame, and there is one timer rather than eight.
 *  It slows down rather than stopping when nothing is running - the header
 *  still has an "how long ago did this page last read" to keep honest. */
function useTick(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), active ? 1000 : 5000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

/** A server-measured age, carried forward to this second. `at` is when that
 *  measurement arrived here, so only the ELAPSED time is read from the
 *  browser's clock - never the absolute time, which may be minutes out. */
function tick(base: number | null | undefined, at: number, now: number): number | null {
  if (base == null) return null;
  return base + secondsSince(at, now);
}

const secondsSince = (at: number, now: number) => Math.max(0, Math.round((now - at) / 1000));

/** A running clock: 0:07, 4:31, 1:04:12. */
function clock(sec: number | null): string {
  if (sec == null) return "";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const ss = String(sec % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** A finished duration: 8s, 1m 17s, 2h 04m. */
function secs(sec: number): string {
  if (sec < 60) return `${sec}s`;
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m ${String(sec % 60).padStart(2, "0")}s`;
}

/** A length of time, in words: 40 seconds, 6 minutes, 2 hours. */
function span(sec: number): string {
  if (sec < 60) return `${sec} second${sec === 1 ? "" : "s"}`;
  if (sec < 3600) {
    const m = Math.round(sec / 60);
    return `${m} minute${m === 1 ? "" : "s"}`;
  }
  const h = Math.round(sec / 360) / 10;
  return `${h} hour${h === 1 ? "" : "s"}`;
}

const ago = (sec: number | null) => (sec == null ? "" : sec < 2 ? "just now" : `${span(sec)} ago`);

/** The header badge's own clock, in the fewest characters that stay true:
 *  "4s ago", "3m ago", "2h ago". The heartbeat still says it in words, where
 *  a reader is being told something rather than reassured; here the label
 *  changes every second and its width is the thing that matters. */
const brief = (sec: number) =>
  `${sec < 60 ? `${sec}s` : sec < 3600 ? `${Math.round(sec / 60)}m` : `${Math.round(sec / 3600)}h`} ago`;

/** "1 recording", "26 recordings". The counts here are measurements and are
 *  printed rather than rounded to "some", so they have to read right at one. */
const count = (n: number, thing: string) => `${n.toLocaleString()} ${thing}${n === 1 ? "" : "s"}`;
const be = (n: number) => (n === 1 ? "is" : "are");

/** A timestamp the server wrote, as a time of day. */
function when(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const today = new Date().toDateString() === d.toDateString();
  return d.toLocaleString("en-US", {
    month: today ? undefined : "short",
    day: today ? undefined : "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
