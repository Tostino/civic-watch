/** The curation console's API. Same-origin through the Next
 *  rewrite, so the httpOnly session cookie rides along on every call and no
 *  token ever touches client code, a URL or storage. */

export interface AdminState {
  authenticated: boolean;
  utterances?: number;
  named?: number;
  /** override / human / voice / cluster / unnamed → count. */
  basis?: Record<string, number>;
  labels?: number;
  ignores?: number;
  overrides?: Record<string, number>;
  queues?: { splits: number; proposals: number };
}

export interface SplitRow {
  video_id: string;
  name: string;
  voices: number;
  utts: number;
  title: string;
  upload_date: string | null;
  kind: string;
  meeting_id: number | null;
}

export interface OverrideRow {
  id: number;
  video_id?: string;
  start_idx: number;
  end_idx: number;
  action: "reassign" | "detach" | "identify" | "split";
  name: string | null;
  note: string | null;
  author: string | null;
  status: "applied" | "pending" | "rejected";
  created_at: string;
  span?: number;
  title?: string;
  upload_date?: string | null;
}

export interface VoiceQueueRow {
  cluster: number;
  lines: number;
  meetings: number;
  sample: { video_id: string; local_label: string; start: number; text: string } | null;
}

export interface LabelRow {
  video_id: string;
  local_label: string;
  name: string;
  note: string | null;
  labeled_at: string;
  upload_date: string | null;
  kind: string;
  utts: number;
}

export interface Queues {
  splits: SplitRow[];
  proposals: OverrideRow[];
  voices: VoiceQueueRow[];
  recent: OverrideRow[];
  labels: LabelRow[];
}

export interface ReviewVoice {
  local_label: string;
  cluster: number | null;
  /** The pipeline's per-meeting call — an inference, rendered as one. */
  name: string | null;
  confidence: number | null;
  source: string | null;
  labeled: boolean;
  label_name: string | null;
  label_note: string | null;
  ignored: boolean;
  utts: number;
  first_at: number | null;
  affinity: { name: string; similarity: number }[];
  samples: { idx: number; start: number; end: number; text: string }[];
  elsewhere: {
    video_id: string;
    start: number;
    text: string;
    name: string | null;
    upload_date: string | null;
    title: string;
  }[];
}

export interface Review {
  video: {
    id: string;
    title: string;
    upload_date: string | null;
    kind: string;
    duration: number | null;
    meeting_id: number | null;
    date: string | null;
    body: string | null;
  };
  voices: ReviewVoice[];
  roster: { surname: string; full_name: string | null; office: string | null; district: number | null }[];
  overrides: OverrideRow[];
}

export interface RangeLine {
  idx: number;
  start: number;
  name: string | null;
  basis: string | null;
  human: boolean;
  contested: boolean;
  text: string;
}

export interface CorrectResult {
  id: number;
  status: "applied" | "pending";
  utterances: number;
  lines: RangeLine[];
  /** Set when the server stored a different form of the name (surname rule). */
  normalized?: string;
  reindexed?: number;
  reindex_error?: string;
}

export class AdminApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { cache: "no-store", ...init });
  if (!res.ok) {
    let msg = `${res.status}`;
    try {
      msg = ((await res.json()) as { error?: string }).error ?? msg;
    } catch {
      /* the status is the message */
    }
    throw new AdminApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body: unknown) =>
  req<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export interface RederiveStatus {
  state: "never_run" | "running" | "reverting" | "done" | "failed" | "reverted" | "died";
  started_at?: string;
  finished_at?: string;
  step?: string | null;
  steps?: { name: string; seconds: number; rc: number }[];
  labels_at_start?: number;
  labels_since: number;
  can_revert: boolean;
  before?: { named: number; splits: number };
  after?: { named: number; splits: number };
  diff?: {
    changed: number;
    gained: number;
    lost: number;
    movers: { from: string; to: string; n: number }[];
  };
  log_tail: string[];
}

/** One command in a job, and what it does in words. */
export interface JobStep {
  say: string;
  cmd: string;
}

export type JobState = "running" | "done" | "failed" | "died" | "stopped";

/* The fields marked "newer" are absent when web/server.py has not been
 * restarted since this page was built. The Python server is restarted by
 * hand, `next dev` reloads itself, and so the two DO get out of step - a page
 * that white-screens on that is a page that hides the restart it needs. */
export interface OpsStatus {
  jobs: Record<
    string,
    { title: string; why: string; paid: boolean; steps?: JobStep[] /* newer */ }
  >;
  last: {
    job: string;
    state: JobState;
    started_at?: string;
    finished_at?: string;
    step?: string | null;
    step_say?: string | null;
    step_index?: number | null;
    step_started_at?: string | null;
    step_count?: number;
    steps?: { cmd: string; say?: string; seconds: number; rc: number }[];
  } | null;
  log_tail: string[];
  /** Seconds since anything was written to the log — the stuck signal.
   *  Every `*_age`/`elapsed` here is measured on the SERVER, against the clock
   *  that wrote the timestamps; the page ticks its own second hand from them
   *  rather than subtracting the browser's clock from the server's. (newer) */
  log_age?: number | null;
  /** The banner the current step last printed for itself, if it prints any. */
  log_phase?: string | null;
  /** Who holds the one-at-a-time lock, or null. */
  running: string | null;
  running_kind?: "job" | "rederive" | null /* newer */;
  elapsed?: number | null /* newer */;
  step_elapsed?: number | null /* newer */;
  fleet_workers: number;
  /** newer. Its absence is what the page tests to know the API is behind. */
  fleet?: {
    workers: { name: string; kind: "download" | "diarize" | "transcribe" }[];
    in_flight: {
      worker: string;
      video_id: string;
      title: string;
      duration: number | null;
      held_for: number | null;
    }[];
    log_age: number | null;
    counts: {
      total: number;
      downloaded: number;
      diarized: number;
      transcribed: number;
      errors: number;
    };
  };
  gates: {
    ingest_pending: {
      to_download: number;
      to_diarize: number;
      to_transcribe: number;
      total: number;
    };
    fold_pending: number;
    unnamed_voices: number;
    /** newer. What the two discover steps have waiting, where that can
     *  honestly be measured before the step runs. */
    portal?: { upcoming: number; no_agenda: number };
    catalog?: { videos: number; unplaced: number };
    /** Labels written since the propagation last ran. */
    labels_pending?: number;
    llm_key: boolean;
  };
}

export const getOps = () => req<OpsStatus>("/api/admin/ops");
export const startJob = (name: string, paid_ok = false) =>
  post<{ started: string }>("/api/admin/job", { name, paid_ok });
export const stopJob = () => post<{ stopped: string }>("/api/admin/job/stop", {});

export const getRederive = () => req<RederiveStatus>("/api/admin/rederive");
export const rederive = (action: "start" | "revert") =>
  post<{ started: string }>("/api/admin/rederive", { action });

export const getAdminState = () => req<AdminState>("/api/admin/state");
/** Cookie check only — no database work. Safe to call from reading views. */
export const getAdminSession = () => req<{ authenticated: boolean }>("/api/admin/session");
export const getQueues = () => req<Queues>("/api/admin/queues");

export function getReview(video: string, opts: { name?: string; label?: string } = {}) {
  const q = new URLSearchParams({ video });
  if (opts.name) q.set("name", opts.name);
  if (opts.label) q.set("label", opts.label);
  return req<Review>(`/api/admin/review?${q}`);
}

export const adminLogin = (token: string) =>
  post<{ authenticated: boolean }>("/api/admin/login", { token });

export const correct = (body: {
  video_id: string;
  start_idx: number;
  end_idx: number;
  action: OverrideRow["action"];
  name?: string | null;
  note?: string | null;
}) => post<CorrectResult>("/api/admin/correct", body);

export const undoCorrection = (id: number) =>
  post<{ removed: number; lines: RangeLine[]; reindexed?: number; reindex_error?: string }>(
    "/api/admin/undo",
    { id },
  );

export const decideProposal = (id: number, decision: "accept" | "reject") =>
  post<{ id: number; decision: string }>("/api/admin/proposal", { id, decision });

export const labelVoice = (body: {
  members: [string, string][];
  name: string | null;
  note?: string | null;
}) => post<{ name: string | null; voices: number; normalized?: string }>("/api/admin/label", body);

export const ignoreVoice = (body: { members: [string, string][]; reason?: string; undo?: boolean }) =>
  post<{ ignored: number; restored: number }>("/api/admin/ignore", body);

/* ----------------------------------------------------------- redaction
 *
 * The queue of members-of-the-public addresses proposed for removal.
 * A row carries the whole line and the offset of the span inside it, not just
 * the span: the page marks it in place, and a line that states an address
 * twice must not have the wrong one highlighted. */

export interface RedactionRow {
  id: number;
  video_id: string;
  idx: number;
  span: string;
  kind: string;
  status: "proposed" | "applied" | "rejected";
  author: string | null;
  /** The whole utterance, and where `span` starts in it (-1 if it has moved). */
  text: string;
  at: number;
  start: number | null;
  speaker: string | null;
  /** The line before. Usually the clerk asking for name and address, which is
   *  what distinguishes a residence from the matter under discussion. */
  prev_text: string | null;
  phase: string | null;
  video_title: string | null;
  meeting_id: number | null;
  meeting_date: string | null;
  meeting_body: string | null;
  /** Where to hear it said. The only way to settle an ambiguous one. */
  href: string | null;
}

export interface RedactionQueue {
  total: number;
  limit: number;
  offset: number;
  rows: RedactionRow[];
  counts: Partial<Record<"proposed" | "applied" | "rejected", number>>;
}

export interface RedactionJob {
  state: "never_run" | "running" | "done" | "died";
  pid?: number;
  started_at?: string;
  finished_at?: string;
  total?: number;
  recordings?: number;
  applied?: number;
  done_recordings?: number;
  failed?: number;
  seconds?: number;
  /** The recording being re-indexed right now. */
  video?: string | null;
  proposed: number;
  log_tail: string[];
}

export function getRedactions(
  opts: { status?: string; limit?: number; offset?: number; video?: string } = {},
) {
  const q = new URLSearchParams();
  if (opts.status) q.set("status", opts.status);
  if (opts.limit) q.set("limit", String(opts.limit));
  if (opts.offset) q.set("offset", String(opts.offset));
  if (opts.video) q.set("video", opts.video);
  return req<RedactionQueue>(`/api/admin/redactions?${q}`);
}

export type RedactionDecision = "accept" | "reject" | "revert" | "reconsider";

export const decideRedactions = (ids: number[], decision: RedactionDecision) =>
  post<{
    applied?: number;
    rejected?: number;
    reverted?: number;
    reconsidering?: number;
  }>("/api/admin/redaction", { ids, decision });

export const getRedactionJob = () =>
  req<RedactionJob>("/api/admin/redaction/job");
export const applyAllRedactions = () =>
  post<{ started: string; proposals: number }>(
    "/api/admin/redaction/apply-all", {});
