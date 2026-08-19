"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  applyAllRedactions,
  decideRedactions,
  getRedactionJob,
  getRedactions,
  type RedactionDecision,
  type RedactionRow,
} from "@/lib/admin";
import { clock, meetingDate } from "@/lib/format";
import s from "./RedactionScreen.module.css";

/**
 * The address queue.
*/
/** The three lists, and what each row's buttons mean in each. A decision is
 *  never a dead end: a removal can be put back and a keep can be reconsidered,
 *  because the queue is one-way only if you build it that way — the same
 *  mistake the split-voice queue made before it grew a ledger. */
const TABS = [
  { status: "proposed", label: "To review", verb: "Remove", other: "Keep" },
  { status: "applied", label: "Removed", verb: null, other: "Put back" },
  { status: "rejected", label: "Kept", verb: null, other: "Reconsider" },
] as const;

export function RedactionScreen() {
  const qc = useQueryClient();
  const [status, setStatus] = useState<"proposed" | "applied" | "rejected">("proposed");
  const [offset, setOffset] = useState(0);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const LIMIT = 25;

  const tab = TABS.find((t) => t.status === status) ?? TABS[0];

  const { data, isLoading } = useQuery({
    queryKey: ["admin", "redactions", status, offset],
    queryFn: () => getRedactions({ status, limit: LIMIT, offset }),
    staleTime: 5_000,
  });

  const { data: job } = useQuery({
    queryKey: ["admin", "redactionJob"],
    queryFn: getRedactionJob,
    // Only while something is running: a console that polls for ever is a
    // console that keeps a laptop awake.
    refetchInterval: (q) => (q.state.data?.state === "running" ? 3000 : false),
    staleTime: 2_000,
  });

  const refresh = useCallback(() => {
    setPicked(new Set());
    void qc.invalidateQueries({ queryKey: ["admin", "redactions"] });
    void qc.invalidateQueries({ queryKey: ["admin", "redactionJob"] });
  }, [qc]);

  const decide = useMutation({
    mutationFn: ({ ids, decision }: { ids: number[]; decision: RedactionDecision }) =>
      decideRedactions(ids, decision),
    onSuccess: refresh,
  });


  const applyAll = useMutation({ mutationFn: applyAllRedactions, onSuccess: refresh });

  const rows = data?.rows ?? [];
  const running = job?.state === "running";
  /* Accepting starts the background job rather than doing the work in the
     request, so the button's job is to hand over to the progress panel. Keep /
     Put back / Reconsider still return immediately - they write no transcript
     and re-index nothing. */
  const removing = decide.isPending || applyAll.isPending || running;
  const allOnPage = rows.length > 0 && rows.every((r) => picked.has(r.id));
  const someOnPage = rows.some((r) => picked.has(r.id));

  /* Whole page on or off. The page holds 25 and the per-row cap is 25, so
     "select all" lands exactly on what a batch may contain - which is why
     changing page clears the selection below rather than accumulating past
     the cap into a batch the server will refuse. */
  const toggleAll = () =>
    setPicked((p) => {
      const n = new Set(p);
      if (allOnPage) rows.forEach((r) => n.delete(r.id));
      else rows.forEach((r) => n.add(r.id));
      return n;
    });
  const toggle = (id: number) =>
    setPicked((p) => {
      const n = new Set(p);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  return (
    <div className={s.page}>
      <header className={s.head}>
        <div>
          <h1>Addresses</h1>
          <p className={s.why}>
            Speakers at the podium are asked for their address, so the archive recorded
            thousands of them. They were always public; this made them searchable, which is a
            different fact about somebody&rsquo;s home. Removing one rewrites the transcript and
            re-indexes the recording, so search cannot find it either.
          </p>
        </div>
        <div className={s.counts}>
          <span className={s.big}>{data?.counts.proposed ?? job?.proposed ?? 0}</span>
          <span className={s.countLabel}>proposed</span>
          {data?.counts.applied ? (
            <span className={s.done}>{data.counts.applied} removed</span>
          ) : null}
          {data?.counts.rejected ? (
            <span className={s.kept}>{data.counts.rejected} kept</span>
          ) : null}
        </div>
      </header>

      {/* Which list. Without this the page could only ever show what had not
          been decided, which meant no way to check your own work or undo it. */}
      <nav className={s.tabs} aria-label="Which redactions">
        {TABS.map((t) => (
          <button
            key={t.status}
            type="button"
            className={`${s.tab} ${status === t.status ? s.tabOn : ""}`}
            aria-current={status === t.status}
            onClick={() => {
              setStatus(t.status);
              setOffset(0);
              setPicked(new Set());
            }}
          >
            {t.label}
            <span className={s.tabN}>{data?.counts[t.status] ?? 0}</span>
          </button>
        ))}
      </nav>

      {/* ---------------------------------------------------- bulk apply */}
      {status === "proposed" || running ? (
      <section className={s.bulk} aria-label="Apply every proposal">
        {running ? (
          <div className={s.progress} role="status">
            <p>
              Removing addresses — <strong>{job.done_recordings ?? 0}</strong> of{" "}
              <strong>{job.recordings ?? 0}</strong> recordings done,{" "}
              {job.applied ?? 0} of {job.total ?? 0} addresses removed
              {job.failed ? `, ${job.failed} failed` : ""}.
            </p>
            <p className={s.nowAt}>
              Each recording is rebuilt once, however many addresses came out of it —
              the text, the search postings and the embeddings. That is the slow part,
              a few seconds each.
            </p>
            <div className={s.bar}>
              <span
                className={s.fill}
                style={{
                  width: `${Math.round(
                    ((job.done_recordings ?? 0) / Math.max(1, job.recordings ?? 1)) * 100,
                  )}%`,
                }}
              />
            </div>
            {job.video ? <p className={s.nowAt}>re-indexing {job.video}</p> : null}
          </div>
        ) : (
          <>
            <p className={s.bulkWhy}>
              {job?.state === "done" ? (
                <>
                  Last run removed {job.applied ?? 0} across {job.recordings ?? 0} recordings
                  {job.seconds ? ` in ${Math.round(job.seconds / 60)} minutes` : ""}
                  {job.failed ? `, ${job.failed} failed and are still proposed` : ""}.{" "}
                </>
              ) : null}
              Applying everything takes about four seconds a recording, so it runs in the
              background and this page follows it.
            </p>
            <button
              type="button"
              className={s.applyAll}
              disabled={applyAll.isPending || !(job?.proposed ?? 0)}
              onClick={() => applyAll.mutate()}
            >
              Remove all {job?.proposed ?? 0} addresses
            </button>
          </>
        )}
        {applyAll.error ? (
          <p className={s.error}>{(applyAll.error as Error).message}</p>
        ) : null}
      </section>
      ) : null}

      {/* ------------------------------------------------------ the queue */}
      {picked.size ? (
        <div className={s.picked} role="status">
          <span>
            {picked.size} selected
            {picked.size > 25 ? " — more than 25 must go through Remove all" : ""}
          </span>
          {tab.verb ? (
            <button
              type="button"
              disabled={removing}
              onClick={() => decide.mutate({ ids: [...picked], decision: "accept" })}
            >
              {running ? "Removing…" : `${tab.verb} ${picked.size}`}
            </button>
          ) : null}
          <button
            type="button"
            className={s.no}
            disabled={decide.isPending}
            onClick={() =>
              decide.mutate({
                ids: [...picked],
                decision:
                  status === "proposed"
                    ? "reject"
                    : status === "applied"
                      ? "revert"
                      : "reconsider",
              })
            }
          >
            {tab.other} {picked.size}
          </button>
          <button type="button" className={s.plain} onClick={() => setPicked(new Set())}>
            Clear
          </button>
        </div>
      ) : null}

      {rows.length ? (
        <div className={s.toolbar}>
          <label className={s.all}>
            <input
              type="checkbox"
              checked={allOnPage}
              ref={(el) => {
                // Some-but-not-all is a third state, and a checkbox that showed
                // only on/off would claim the page was untouched while 12 rows
                // were selected.
                if (el) el.indeterminate = !allOnPage && someOnPage;
              }}
              onChange={toggleAll}
            />
            {allOnPage ? `Clear all ${rows.length}` : `Select all ${rows.length} on this page`}
          </label>
        </div>
      ) : null}

      {decide.error ? (
        <p className={s.error} role="alert">
          {(decide.error as Error).message}
        </p>
      ) : null}

      {isLoading ? <p className={s.empty}>Reading the queue…</p> : null}
      {!isLoading && !rows.length ? (
        <p className={s.empty}>
          {status === "proposed" ? (
            <>
              Nothing left to review. Run <code>bin/redact.py --propose --write</code> to look
              again.
            </>
          ) : status === "applied" ? (
            "No addresses have been removed yet."
          ) : (
            "Nothing has been kept yet."
          )}
        </p>
      ) : null}

      <ol className={s.rows}>
        {rows.map((r) => (
          <Row
            key={r.id}
            row={r}
            checked={picked.has(r.id)}
            onToggle={() => toggle(r.id)}
            busy={removing}
            verb={tab.verb}
            other={tab.other}
            onDecide={(decision) => decide.mutate({ ids: [r.id], decision })}
          />
        ))}
      </ol>

      {data && data.total > LIMIT ? (
        <nav className={s.pager} aria-label="Pages">
          <button
            type="button"
            disabled={offset === 0}
            onClick={() => {
              setOffset(Math.max(0, offset - LIMIT));
              setPicked(new Set());
            }}
          >
            ← previous
          </button>
          <span>
            {offset + 1}–{Math.min(offset + LIMIT, data.total)} of {data.total}
          </span>
          <button
            type="button"
            disabled={offset + LIMIT >= data.total}
            onClick={() => {
              setOffset(offset + LIMIT);
              setPicked(new Set());
            }}
          >
            next →
          </button>
        </nav>
      ) : null}
    </div>
  );
}

function Row({
  row,
  checked,
  onToggle,
  busy,
  verb,
  other,
  onDecide,
}: {
  row: RedactionRow;
  checked: boolean;
  onToggle: () => void;
  busy: boolean;
  /** The affirmative action for this list, or null where there isn't one —
   *  an already-removed address has nothing to remove again. */
  verb: string | null;
  other: string;
  onDecide: (d: RedactionDecision) => void;
}) {
  // Marked by OFFSET, not by searching the text again: a line that says the
  // address twice would otherwise highlight whichever came first.
  const before = row.at >= 0 ? row.text.slice(0, row.at) : row.text;
  const after = row.at >= 0 ? row.text.slice(row.at + row.span.length) : "";

  return (
    <li className={s.row}>
      <label className={s.pick}>
        <input type="checkbox" checked={checked} onChange={onToggle} />
        <span className="sr-only">Select this address</span>
      </label>

      <div className={s.body}>
        <div className={s.meta}>
          {row.meeting_date ? (
            <span className={s.date}>{meetingDate(row.meeting_date, "short")}</span>
          ) : null}
          {row.meeting_body ? <span>{row.meeting_body}</span> : null}
          {row.phase ? <span className={s.phase}>{row.phase.replace(/_/g, " ")}</span> : null}
          {row.href ? (
            <Link href={row.href} className={s.listen} target="_blank">
              ▶ {row.start != null ? clock(row.start) : "listen"}
            </Link>
          ) : null}
        </div>

        {/* The line before is the evidence. "Please state your name and address
            for the record" settles most of these without playing anything. */}
        {row.prev_text ? <p className={s.prev}>{row.prev_text}</p> : null}

        <p className={s.text}>
          {before}
          <mark className={s.span}>{row.span}</mark>
          {after}
        </p>
        {row.status === "applied" ? (
          <p className={s.applied}>
            Removed from the published transcript. The line above is the recording as
            transcribed, which is kept.
          </p>
        ) : null}
        {row.at < 0 ? (
          <p className={s.moved}>
            This line no longer contains that text — applying will skip it.
          </p>
        ) : null}
      </div>

      <div className={s.actions}>
        {verb ? (
          <button type="button" disabled={busy} onClick={() => onDecide("accept")}>
            {verb}
          </button>
        ) : null}
        <button
          type="button"
          className={s.no}
          disabled={busy}
          onClick={() =>
            onDecide(
              row.status === "proposed"
                ? "reject"
                : row.status === "applied"
                  ? "revert"
                  : "reconsider",
            )
          }
        >
          {other}
        </button>
      </div>
    </li>
  );
}
