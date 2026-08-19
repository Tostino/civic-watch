"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { usePlayer } from "@/components/player/PlayerProvider";
import {
  decideProposal,
  getAdminState,
  getQueues,
  getRederive,
  labelVoice,
  rederive,
  undoCorrection,
  type OverrideRow,
  type RederiveStatus,
} from "@/lib/admin";
import { clock, meetingDate } from "@/lib/format";
import s from "./Dashboard.module.css";

/**
 * The queues, ordered by impact — utterances a decision fixes — because a
 * review list is only workable if its head is the row worth fixing first
 *. The old check emitted an unordered list into a void; this is that
 * list with an ordering, evidence one click away, and somewhere to act.
 */
export function Dashboard() {
  const qc = useQueryClient();
  const player = usePlayer();
  const { data: state } = useQuery({ queryKey: ["admin", "state"], queryFn: getAdminState });
  const { data, isPending, isError } = useQuery({
    queryKey: ["admin", "queues"],
    queryFn: getQueues,
    staleTime: 60_000,
  });

  const refresh = () => void qc.invalidateQueries({ queryKey: ["admin"] });
  const decide = useMutation({
    mutationFn: ({ id, d }: { id: number; d: "accept" | "reject" }) => decideProposal(id, d),
    onSuccess: refresh,
  });
  const undo = useMutation({ mutationFn: undoCorrection, onSuccess: refresh });
  const clearLabel = useMutation({
    mutationFn: (m: [string, string]) => labelVoice({ members: [m], name: null }),
    onSuccess: refresh,
  });

  if (isPending) return <div className={s.state}>Loading the queues…</div>;
  if (isError || !data)
    return (
      <div className={s.state} role="alert">
        We could not load the queues.
      </div>
    );

  const b = state?.basis ?? {};
  const total = state?.utterances ?? 0;

  return (
    <div className={s.wrap}>
      {/* pipeline health without a terminal. */}
      <section className={s.health} aria-label="Attribution health">
        <h2>Speaker attribution, right now</h2>
        <div className={s.meter} role="img" aria-label={basisLabel(b, total)}>
          {(["override", "human", "voice", "cluster", "unnamed"] as const).map((k) =>
            b[k] ? (
              <span
                key={k}
                className={s[`m_${k}`]}
                style={{ width: `${((b[k] ?? 0) / Math.max(total, 1)) * 100}%` }}
                title={`${k}: ${(b[k] ?? 0).toLocaleString()}`}
              />
            ) : null,
          )}
        </div>
        <p className={s.legend}>
          {total.toLocaleString()} utterances —{" "}
          {(["override", "human", "voice", "cluster", "unnamed"] as const)
            .filter((k) => b[k])
            .map((k) => `${(b[k] ?? 0).toLocaleString()} ${BASIS_WORDS[k]}`)
            .join(" · ")}
          . {state?.labels ?? 0} whole-voice labels held. A name a person confirmed outranks every
          inferred name and survives every rebuild.
        </p>
      </section>

      <RederivePanel />

      <section className={s.queue} aria-label="Split voices">
        <h2>
          Split voices <span className={s.count}>{data.splits.length}</span>
        </h2>
        <p className={s.why}>
          One board member attached to two voices in one meeting. Some are true diarization
          splits. Some are another person wearing the name. A person must decide, so this is a
          review queue. Ordered by how many utterances each row fixes.
        </p>
        {data.splits.length === 0 ? (
          <p className={s.empty}>Nothing to review.</p>
        ) : (
          <table className={s.table}>
            <thead>
              <tr>
                <th scope="col">Utterances</th>
                <th scope="col">Name</th>
                <th scope="col">Meeting</th>
                <th scope="col">Voices</th>
                <th scope="col" />
              </tr>
            </thead>
            <tbody>
              {data.splits.map((r) => (
                <tr key={`${r.video_id}:${r.name}`}>
                  <td className={s.num}>{r.utts}</td>
                  <td>{r.name}</td>
                  <td>
                    {r.upload_date ? meetingDate(r.upload_date, "short") : "—"}{" "}
                    <span className={s.kind}>{r.kind === "bcc" ? "BCC" : "Planning"}</span>
                  </td>
                  <td className={s.num}>{r.voices}</td>
                  <td>
                    <Link
                      className={s.go}
                      href={`/admin/review/${r.video_id}?name=${encodeURIComponent(r.name)}`}
                    >
                      Review →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className={s.queue} aria-label="Proposed corrections">
        <h2>
          Proposed corrections <span className={s.count}>{data.proposals.length}</span>
        </h2>
        <p className={s.why}>
          A public proposal changes nothing a reader sees until it is accepted here.
        </p>
        {data.proposals.length === 0 ? (
          <p className={s.empty}>No proposals waiting.</p>
        ) : (
          <ul className={s.cards}>
            {data.proposals.map((p) => (
              <li key={p.id} className={s.card}>
                <ProposalLine p={p} />
                <span className={s.actions}>
                  <Link
                    className={s.go}
                    href={`/admin/review/${p.video_id}?sel=${p.start_idx}-${p.end_idx}`}
                  >
                    Context
                  </Link>
                  <button
                    type="button"
                    onClick={() => decide.mutate({ id: p.id, d: "accept" })}
                    disabled={decide.isPending}
                  >
                    Accept
                  </button>
                  <button
                    type="button"
                    className={s.no}
                    onClick={() => decide.mutate({ id: p.id, d: "reject" })}
                    disabled={decide.isPending}
                  >
                    Reject
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className={s.queue} aria-label="Unnamed voices">
        <h2>
          Unnamed voices, by reach <span className={s.count}>{data.voices.length}</span>
        </h2>
        <p className={s.why}>
          Voice groups no name has reached, ordered by lines × meetings. One listen can name a
          voice across its whole reach — or mark it as not a person.
        </p>
        <table className={s.table}>
          <thead>
            <tr>
              <th scope="col">Reach</th>
              <th scope="col">What it sounds like</th>
              <th scope="col" />
            </tr>
          </thead>
          <tbody>
            {data.voices.map((v) => (
              <tr key={v.cluster}>
                <td className={s.reach}>
                  {v.lines.toLocaleString()} lines
                  <br />
                  <span className={s.kind}>{v.meetings} meetings</span>
                </td>
                <td className={s.sample}>
                  {v.sample ? (
                    <>
                      <button
                        type="button"
                        className={s.play}
                        onClick={() =>
                          player.play(
                            { videoId: v.sample!.video_id, title: "Voice sample" },
                            v.sample!.start,
                          )
                        }
                        title={`Play the sample at ${clock(v.sample.start)}`}
                      >
                        ▶ {clock(v.sample.start)}
                      </button>{" "}
                      <span className={s.quote}>“{v.sample.text}…”</span>
                    </>
                  ) : (
                    <span className={s.kind}>no substantive sample</span>
                  )}
                </td>
                <td>
                  {v.sample ? (
                    <Link
                      className={s.go}
                      href={`/admin/review/${v.sample.video_id}?label=${encodeURIComponent(v.sample.local_label)}`}
                    >
                      Review →
                    </Link>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className={s.queue} aria-label="Whole-voice labels">
        <h2>
          Whole-voice labels, newest first{" "}
          <span className={s.count}>{state?.labels ?? data.labels.length}</span>
        </h2>
        <p className={s.why}>
          A queue forgets a row the moment you decide it. This ledger does not: every label stays
          visible here, so a wrong one can be found and cleared.
        </p>
        {data.labels.length === 0 ? (
          <p className={s.empty}>No labels yet.</p>
        ) : (
          <ul className={s.cards}>
            {data.labels.map((l) => (
              <li key={`${l.video_id}:${l.local_label}`} className={s.card}>
                <span className={s.line}>
                  <strong>{l.name}</strong> · {l.utts} line{l.utts === 1 ? "" : "s"}
                  {l.upload_date ? ` · ${meetingDate(l.upload_date, "short")}` : null}{" "}
                  <span className={s.kind}>{l.kind === "bcc" ? "BCC" : "Planning"}</span>
                  {l.note ? <span className={s.note}> — {l.note}</span> : null}
                </span>
                <span className={s.actions}>
                  <Link
                    className={s.go}
                    href={`/admin/review/${l.video_id}?label=${encodeURIComponent(l.local_label)}`}
                  >
                    Review
                  </Link>
                  <button
                    type="button"
                    className={s.no}
                    onClick={() => clearLabel.mutate([l.video_id, l.local_label])}
                    disabled={clearLabel.isPending}
                    title="Remove the label. The voice goes back to what the pipeline says."
                  >
                    Clear
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className={s.queue} aria-label="Recent corrections">
        <h2>Recent corrections</h2>
        {data.recent.length === 0 ? (
          <p className={s.empty}>
            None yet. Corrections made here are permanent until undone, and reach search in
            seconds.
          </p>
        ) : (
          <ul className={s.cards}>
            {data.recent.map((o) => (
              <li key={o.id} className={s.card}>
                <ProposalLine p={o} />
                <span className={s.actions}>
                  <Link
                    className={s.go}
                    href={`/admin/review/${o.video_id}?sel=${o.start_idx}-${o.end_idx}`}
                  >
                    Context
                  </Link>
                  {o.status !== "rejected" ? (
                    <button
                      type="button"
                      className={s.no}
                      onClick={() => undo.mutate(o.id)}
                      disabled={undo.isPending}
                    >
                      Undo
                    </button>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/**
 * The "propagate my labels" job. Human labels are the verified
 * reference set the matcher and the affinity gate score against, so a batch
 * of new labels is when re-derivation pays. Three properties the panel
 * states because they are what make the button safe to press: labels are
 * only read, never changed; the run ends with a measured diff and a full
 * audit; and Revert restores the pre-run state if the diff looks wrong.
 */
function RederivePanel() {
  const qc = useQueryClient();
  const { data: st } = useQuery({
    queryKey: ["admin", "rederive"],
    queryFn: getRederive,
    refetchInterval: (q) =>
      q.state.data?.state === "running" || q.state.data?.state === "reverting" ? 5000 : false,
    staleTime: 10_000,
  });
  const act = useMutation({
    mutationFn: rederive,
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["admin", "rederive"] }),
  });
  if (!st) return null;

  const busy = st.state === "running" || st.state === "reverting";
  return (
    <section className={s.queue} aria-label="Propagate labels">
      <h2>
        Propagate labels <span className={s.count}>{st.labels_since}</span>
      </h2>
      <p className={s.why}>
        Your labels are the reference set the voice matcher scores against. This re-derives who
        each voice is, puts back the names the published roster supports, re-measures affinity,
        and re-bakes the index — about 30 minutes, all local.
        The paid naming stage does not run. Labels are only read; the run ends with a measured
        diff and a full audit, and Revert restores the pre-run state.
      </p>

      {busy ? (
        <div>
          <p className={s.runState} role="status">
            {st.state === "running" ? "Running" : "Reverting"} — step:{" "}
            <strong>{st.step ?? "…"}</strong>, started {st.started_at}
          </p>
          <pre className={s.log}>{st.log_tail.join("")}</pre>
        </div>
      ) : (
        <>
          {st.state !== "never_run" ? <RunSummary st={st} /> : null}
          <div className={s.actions}>
            <button
              type="button"
              onClick={() => act.mutate("start")}
              disabled={act.isPending}
              title="speaker_id, chair_anchor, affinity, index_passages, audit — local, no paid calls"
            >
              Re-derive identity from the labels
            </button>
            {st.can_revert ? (
              <button
                type="button"
                className={s.no}
                onClick={() => act.mutate("revert")}
                disabled={act.isPending}
                title="Restore speaker_identity and voice_affinity as they were before the last run, then re-index"
              >
                Revert the last run
              </button>
            ) : null}
          </div>
          {act.isError ? (
            <p className={s.err} role="alert">
              {act.error.message}
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}

function RunSummary({ st }: { st: RederiveStatus }) {
  if (st.state === "died") {
    return (
      <p className={s.runState} role="alert">
        The last run died without finishing — check <code>logs/rederive.log</code>. Revert is
        available.
      </p>
    );
  }
  if (st.state === "failed") {
    return (
      <p className={s.runState} role="alert">
        The last run failed at <strong>{st.step}</strong> ({st.finished_at}) — check{" "}
        <code>logs/rederive.log</code>. Revert is available.
      </p>
    );
  }
  if (st.state === "reverted") {
    return (
      <p className={s.runState}>
        The last run was reverted ({st.finished_at}). The derived state is back to before it.
      </p>
    );
  }
  if (!st.diff || !st.before || !st.after) {
    return (
      <p className={s.runState}>
        Last run finished {st.finished_at}.
      </p>
    );
  }
  return (
    <div className={s.runState}>
      <p>
        Last run finished {st.finished_at}: <strong>{st.diff.changed.toLocaleString()}</strong>{" "}
        utterances changed name ({st.diff.gained.toLocaleString()} newly named,{" "}
        {st.diff.lost.toLocaleString()} un-named). Named {st.before.named.toLocaleString()} →{" "}
        {st.after.named.toLocaleString()}; split reviews {st.before.splits} → {st.after.splits}.
      </p>
      {st.diff.movers.length ? (
        <ul className={s.movers}>
          {st.diff.movers.slice(0, 6).map((m) => (
            <li key={`${m.from}→${m.to}`}>
              {m.from} → {m.to} <span className={s.kind}>({m.n.toLocaleString()})</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

const BASIS_WORDS = {
  override: "corrected by range",
  human: "confirmed by a person",
  voice: "matched this meeting",
  cluster: "cluster majority only",
  unnamed: "unidentified",
} as const;

function basisLabel(b: Record<string, number>, total: number) {
  return `Of ${total} utterances: ${Object.entries(b)
    .map(([k, v]) => `${v} ${k}`)
    .join(", ")}`;
}

function ProposalLine({ p }: { p: OverrideRow }) {
  return (
    <span className={s.line}>
      <span className={s.action} data-action={p.action}>
        {p.action}
      </span>{" "}
      {p.name ? <strong>{p.name}</strong> : <em>no name — “not who it says”</em>} · lines{" "}
      {p.start_idx}–{p.end_idx}
      {p.upload_date ? ` · ${meetingDate(p.upload_date, "short")}` : null}
      {p.status ? <span className={s.status}> · {p.status}</span> : null}
      {p.note ? <span className={s.note}> — {p.note}</span> : null}
    </span>
  );
}
