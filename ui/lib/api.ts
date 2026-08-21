import type {
  AskResult,
  Body,
  CaseDetail,
  Facets,
  FindResult,
  Highlights,
  Issues,
  ItemDetail,
  MeetingDetail,
  MeetingRow,
  Facts,
  Overview,
  ToolsManifest,
  Transcript,
} from "./types";

/** Server components talk to the Python API directly; the browser goes through
 *  the rewrite in next.config.ts, so it stays same-origin. */
const ORIGIN = typeof window === "undefined"
  ? (process.env.ARCHIVE_API ?? "http://127.0.0.1:8765")
  : "";

async function get<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${ORIGIN}${path}`, { cache: "no-store", ...init });
  if (!res.ok) throw new ApiError(res.status, path);
  return res.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(readonly status: number, readonly path: string) {
    super(`${status} on ${path}`);
  }
}

export const getMeeting = (id: number) => get<MeetingDetail>(`/api/meeting/${id}`);

export const getTranscript = (videoId: string) =>
  get<Transcript>(`/api/transcript/${encodeURIComponent(videoId)}`);

export const getItem = (id: number) => get<ItemDetail>(`/api/item/${id}`);

/** Case ids carry no slashes today, but they are free text from a PDF and one
 *  would silently become a path segment. Encoded, always. */
export const getCase = (id: string) =>
  get<CaseDetail>(`/api/case/${encodeURIComponent(id)}`);

export const getBodies = () => get<Body[]>(`/api/bodies`);

/** The tool surface and what the MCP endpoint will refuse. Costs no database
 *  work, so /about can ask for it alongside the counts. */
export const getTools = () => get<ToolsManifest>(`/api/tools`);

/** The archive measured, for copy that would otherwise type a number. Cached
 *  for an hour on the server, so a page may ask on every render. */
export const getFacts = () => get<Facts>(`/api/facts`);

/**
 * A run of the agent the server kept, which is what a shared `/ask/<id>` link
 * resolves to. Not `/api/ask`: that one runs the agent and charges for it.
 * This is a row, and reading it costs nothing.
 */
export const getAnswer = (id: string) =>
  get<AskResult & { id: string; asked_at: string }>(
    `/api/answer/${encodeURIComponent(id)}`);

export const getOverview = (body?: string) =>
  get<Overview>(`/api/overview${body ? `?body=${encodeURIComponent(body)}` : ""}`);

/** All four lists scroll back through history now, so both limits are deep.
 *  They stayed separate because the server caps them differently. */
export const getHighlights = (limit = 6, divided = 60) =>
  get<Highlights>(`/api/highlights?limit=${limit}&divided=${divided}`);

/** What the county keeps coming back to, per year, in both sources. */
export const getIssues = () => get<Issues>(`/api/issues`);

export function getMeetings(params: {
  body?: string;
  year?: string;
  /** YYYY-MM, from a cell on the time axis. */
  month?: string;
  recording?: boolean;
  /** Defaults to past. The portal's forward calendar is not the archive. */
  when?: "past" | "upcoming" | "all";
  limit?: number;
  offset?: number;
} = {}) {
  const q = new URLSearchParams();
  if (params.body) q.set("body", params.body);
  if (params.year) q.set("year", params.year);
  if (params.month) q.set("month", params.month);
  if (params.when) q.set("when", params.when);
  if (params.recording !== undefined) q.set("recording", params.recording ? "1" : "0");
  if (params.limit) q.set("limit", String(params.limit));
  if (params.offset) q.set("offset", String(params.offset));
  return get<{ total: number; meetings: MeetingRow[] }>(`/api/meetings?${q}`);
}

/** The search facets a rail may offer, derived from the data. */
export const getFacets = () => get<Facets>("/api/facets");

/**
 * Both sources at once. This is `web/tools.py:search()`, which is two
 * calls to the same tools the agent uses — not a parallel implementation. When
 * a search behaves oddly on the page, the same tool call reproduces it.
 */
/**
 * `from` is the reader's address, and it only matters on the server.
 *
 * This route is rendered on the server, which means the request the Python
 * API sees comes from the UI over loopback and carries nobody's address at
 * all. Its search ceiling is per address, so without this every reader on the
 * site would share one bucket and a crawler would spend everyone's quota
 * rather than its own. Passed rather than read here: `headers()` is only
 * callable inside a request, and this module is imported by client components
 * too.
 */
export function find(params: {
  q: string;
  limit?: number;
  offset?: number;
  body?: string;
  outcome?: string;
  phase?: string;
  case?: string;
  speaker?: string;
  since?: string;
  until?: string;
  decided?: boolean;
}, from?: string) {
  const p = new URLSearchParams({ q: params.q });
  for (const k of ["body", "outcome", "phase", "case", "speaker", "since", "until"] as const) {
    if (params[k]) p.set(k, params[k]!);
  }
  if (params.limit) p.set("limit", String(params.limit));
  if (params.offset) p.set("offset", String(params.offset));
  if (params.decided !== undefined) p.set("decided", params.decided ? "1" : "0");
  return get<FindResult>(`/api/find?${p}`,
    from ? { headers: { "X-Forwarded-For": from } } : undefined);
}
