/** Shapes served by ../../web/archive.py. */

export type Outcome =
  | "approved"
  | "adopted"
  | "denied"
  | "withdrawn"
  | "continued"
  | "received"
  | "no_action"
  | "tabled"
  | "other";

/** Where a claim comes from. The one distinction the whole UI hangs on (R2). */
export type Source = "agenda" | "transcript";

export interface Span {
  video_id: string;
  part: number;
  start: number;
  end: number;
  start_idx: number;
  end_idx: number;
}

/**
 * One TIME the board took an item up (R5.2.7) — spans merged when they are
 * close enough to be a single discussion. `archive._runs` is the definition;
 * the threshold and the measurement behind it live there.
 */
export interface Run {
  video_id: string;
  session_seq: number | null;
  start: number;
  end: number;
  start_idx: number;
  end_idx: number;
  /** How many raw spans merged into this one. */
  parts: number;
  nth: number;
  of: number;
}

export interface ItemRun extends Run {
  lines: Line[];
}

export interface Item {
  id: number;
  seq: number;
  code: string | null;
  section: string | null;
  phase: string;
  title: string | null;
  case_id: string | null;
  department: string | null;
  recommendation: string | null;
  disposition: string | null;
  outcome: Outcome | null;
  source: Source;
  districts: string | null;
  file_number: string | null;
  spans: Span[];
}

export interface Video {
  id: string;
  title: string;
  duration: number;
  session_seq: number | null;
  upload_date: string | null;
  kind: string | null;
  words: number | null;
}

export interface RosterEntry {
  person_id: number;
  surname: string;
  full_name: string | null;
  district: number | null;
  office: "chair" | "vice_chair" | "second_vice_chair" | null;
}

export interface PortalFile {
  file_id: number;
  kind: string | null;
  name: string | null;
  published_at: string | null;
  chars: number | null;
  /** false = the county's PDF is an image-only scan we cannot read. */
  extracted: boolean;
  event_id: number;
  /** The county's own document. The strongest provenance we can offer. */
  url: string;
}

export interface Coverage {
  items: number;
  derived_items: number;
  decided: number;
  bound: number;
  videos: number;
  seconds: number;
  roster: number;
  agenda_file: boolean;
  minutes_file: boolean;
}

export interface Meeting {
  id: number;
  date: string;
  body: string;
  title: string | null;
}

export interface MeetingDetail {
  meeting: Meeting;
  videos: Video[];
  roster: RosterEntry[];
  items: Item[];
  files: PortalFile[];
  portal: { id: number; name: string; body: string; event_date: string; url: string } | null;
  coverage: Coverage;
  prev: { id: number; date: string } | null;
  next: { id: number; date: string } | null;
}

/**
 * One line of transcript. Speaker identity arrives as FIELDS, never as a
 * rendered string, so exactly one component decides how to show it (R6.2.1)
 * and a future redaction rule has one place to act (D3).
 */
export interface Line {
  /** The recording this line is from. What the console bridge keys on. */
  video_id: string;
  idx: number;
  start: number;
  end: number;
  text: string;
  /** Diarization cluster. Groups lines within a page. NOT a name, NOT durable. */
  voice: number | null;
  local_label: string | null;
  name: string | null;
  confidence: number | null;
  /** A person stated this. Outranks everything derived (R5.8.7). */
  human: boolean;
  /**
   * How the name was arrived at. These are four very different claims and the
   * UI must not render them identically (R2.3):
   *   override  a person, about this stretch of speech
   *   human     a person, about this whole voice
   *   voice     the pipeline, about this voice in THIS meeting
   *   cluster   the pipeline, about this voice archive-wide — the weakest,
   *             and the one that put two different women under one name
   */
  basis: "override" | "human" | "voice" | "cluster" | null;
  /** A correction is pending. Show it as disputed, take no side (R5.8.10). */
  contested: boolean;
  agenda_item_id: number | null;
}

export interface Office {
  office: RosterEntry["office"];
  district: number | null;
  full_name: string | null;
}

export interface Transcript {
  video: Video & { meeting_id: number };
  lines: Line[];
  /** surname -> the office they held AT THIS MEETING (R5.2.5). */
  offices: Record<string, Office>;
}

/* ------------------------------------------------------------------- item */

/** One appearance of a case, wherever the thread is drawn. */
export interface ThreadStep {
  id: number;
  code: string | null;
  title: string | null;
  phase: string;
  outcome: Outcome | null;
  disposition: string | null;
  meeting_id: number;
  date: string;
  body: string;
  recorded: boolean;
}

export interface SourceFile extends PortalFile {
  /**
   * Same document, served through our own origin with `Content-Disposition:
   * inline`. CivicClerk marks every file `attachment`, which makes a browser
   * download it instead of rendering it, so a cross-origin frame shows nothing
   * (R5.3.5). `url` is still the county's direct link and stays offered.
   */
  inline: string;
}

export interface ItemRecord extends Omit<Item, "spans"> {
  outcome_source: string | null;
  date: string;
  body: string;
  spans: Span[];
  /** The item's speech grouped by appearance, which is how it is read. */
  runs: ItemRun[];
  /** The same lines poured flat, for callers that do not care about breaks. */
  lines: Line[];
  /** True when the item ran past the server's line cap. */
  truncated: boolean;
  videos: Video[];
  /** Every appearance of this case, including this one (R5.3.3). */
  thread: ThreadStep[];
  files: SourceFile[];
  portal: string | null;
}

export interface ItemDetail {
  item: ItemRecord;
  meeting: Meeting;
  offices: Record<string, Office>;
  prev: { id: number; code: string | null; title: string | null } | null;
  next: { id: number; code: string | null; title: string | null } | null;
}

/* ------------------------------------------------------------------- case */

export interface CaseStep {
  id: number;
  seq: number;
  code: string | null;
  section: string | null;
  phase: string;
  title: string | null;
  department: string | null;
  recommendation: string | null;
  disposition: string | null;
  outcome: Outcome | null;
  source: Source;
  districts: string | null;
  file_number: string | null;
  meeting_id: number;
  date: string;
  body: string;
  span: { video_id: string; start: number; end: number } | null;
}

/**
 * One hearing of a case: a stretch of one meeting's recording, with what was
 * said in it. A case that ran twelve appearances over ten months has one of
 * these per appearance that was recorded — and, because a board can take an
 * item up twice in a day (R5.2.7), possibly two for one meeting.
 */
export interface CaseHearing extends ItemRun {
  item_id: number;
  code: string | null;
  meeting_id: number;
  date: string;
  body: string;
  phase: string;
  outcome: Outcome | null;
  disposition: string | null;
}

export interface CaseDetail {
  case: {
    id: string;
    prefix: string | null;
    first_seen: string | null;
    last_seen: string | null;
    meetings: number | null;
    bodies: number | null;
  };
  case_id: string;
  /** The full official title, said once (R5.4.2). */
  title: string | null;
  steps: CaseStep[];
  bodies: string[];
  first: string;
  last: string;
  /**
   * The last appearance that actually decided something. A continuance is the
   * board saying "not today", never a conclusion, so a case whose final step
   * was continued has no terminal outcome and is still open (R5.4.3).
   */
  terminal: {
    id: number;
    date: string;
    body: string;
    outcome: Outcome;
    disposition: string | null;
  } | null;
  continuances: number;
  recorded: number;
  /**
   * Everything said about this application, across every meeting that took it
   * up, in the order it happened (R5.4.4). Empty for the 92% of cases with no
   * recording behind any appearance.
   */
  heard: CaseHearing[];
  /** Per MEETING, because offices rotate and a case can span years (R5.2.5). */
  offices: Record<string, Record<string, Office>>;
  heard_lines: number;
  heard_truncated: boolean;
}

export interface MeetingRow extends Meeting {
  items: number;
  decided: number;
  videos: number;
  seconds: number;
  roster: boolean;
}

/* ------------------------------------------------------- browse (§5.1) */

/** One calendar month of the archive. The unit the time axis is drawn from. */
export interface MonthCell {
  /** YYYY-MM */
  month: string;
  /** Meetings that HAPPENED. */
  meetings: number;
  recorded: number;
  with_agenda: number;
  /** On the county's calendar and not yet held — a different state from none. */
  scheduled: number;
}

export interface Overview {
  meetings: number;
  first: string;
  last: string;
  recorded: number;
  with_agenda: number;
  with_minutes: number;
  seconds: number;
  /** Archive-wide, so omitted when a body filter is applied. */
  items?: number;
  decided?: number;
  cases?: number;
  /** Of those, the ones heard at more than one meeting — 1,377 of 20,275. The
   *  rest were heard once, and calling all of them "followed across meetings"
   *  claimed the archive does something to 20,275 cases that it does to 7%. */
  cases_recurring?: number;
  months: MonthCell[];
}

interface DividedBase {
  id: number;
  code: string | null;
  title: string | null;
  outcome: Outcome | null;
  case_id: string | null;
  meeting_id: number;
  date: string;
  body: string;
}

/** Dissent as the approved minutes recorded it. Quotable. */
export interface DividedInRecord extends DividedBase {
  source: "record";
  disposition: string;
  /** Who voted nay, from the minutes' own wording. */
  dissent: string[];
  /** Items carried on this one motion — six consent items can share it. */
  items: number;
}

/** Division the recording caught, which the minutes may never record. ASR. */
export interface DividedInRoom extends DividedBase {
  source: "transcript";
  /** `vote` is a tally or a failed motion; `objection` is a member saying so. */
  kind: "vote" | "objection";
  speaker: string;
  quote: string;
  video_id: string;
  seconds: number;
}

export interface Divided {
  record: DividedInRecord[];
  room: DividedInRoom[];
}

export interface ContinuedCase {
  case_id: string;
  continuances: number;
  appearances: number;
  first: string;
  last: string;
  bodies: number;
  title: string | null;
}

/** One meeting-day of decisions, not one decision (see archive.highlights). */
export interface DecidedDay {
  meeting_id: number;
  date: string;
  body: string;
  decided: number;
  passed: number;
  refused: number;
  withdrawn: number;
  continued: number;
  divided: number;
  /** Of the decided, how many were heard rather than taken on consent. */
  heard: number;
  seconds: number;
  /** What was not routine: refusals first, then divided votes. */
  notable: { id: number; code: string | null; title: string | null; outcome: Outcome }[];
}

export interface Highlights {
  divided: Divided;
  continued: ContinuedCase[];
  decided: DecidedDay[];
}

/** One issue in one year, in both sources. Zero-filled across the whole span,
 *  so a year the issue was absent is a cell rather than a gap in the array. */
export interface IssueYear {
  /** YYYY */
  year: string;
  /** Published agenda items whose title names the issue. */
  items: number;
  /** Meetings that took at least one of them up. */
  meetings: number;
  /** Lines of speech that mention it. A line is a ~40-second block. */
  lines: number;
  /** Recorded meetings those lines came from. */
  heard: number;
}

/** A subject the county returns to, counted per year in both sources. */
export interface Issue {
  slug: string;
  label: string;
  /** What to ask `/search` for this issue. */
  q: string;
  items: number;
  meetings: number;
  continued: number;
  refused: number;
  /** Items the minutes recorded a nay vote on — the tie to "where the board
   *  disagreed", which shows the same dissent one item at a time. */
  divided: number;
  lines: number;
  heard: number;
  /** Earliest and latest evidence, from EITHER source. */
  first: string;
  last: string;
  years: IssueYear[];
}

export interface Issues {
  /** Every year the archive holds, oldest first. Drawn, not assumed. */
  span: string[];
  /** The first year with a recording. Before it the room did not exist, which
   *  is a different fact from an issue nobody discussed (R3.2). */
  heard_from: string;
  issues: Issue[];
}

/* ------------------------------------------------------------------ search
 * Served by ../../web/tools.py, which is the same surface the agent calls
 * (D9). Two sources, two shapes, never merged into one ranked list: "this was
 * approved" and "somebody said this" are not comparable (UI_PLAN §2). */

/** An item as a search result: the whole record, minus the recording spans a
 *  hit list does not fetch. `Item` satisfies this, which is what lets one
 *  ItemCard serve the spine, the case, the evidence and the results. */
export type ItemLike = Omit<Item, "spans" | "seq"> & {
  seq?: number;
  spans?: Span[];
};

export interface RecordHit extends ItemLike {
  search_title: string | null;
  outcome_source: string | null;
  meeting_id: number;
  date: string;
  body: string;
  meeting_title: string | null;
  /** Whether the meeting that took this up was recorded at all. */
  has_recording: boolean;
  score: number;
}

export interface TranscriptHit {
  id: number;
  video_id: string;
  start: number;
  end: number;
  speaker: string | null;
  text: string;
  phase: string | null;
  agenda_item_id: number | null;
  /** The item this sits under — without it a hit is often unreadable (R5.6.3). */
  item: string | null;
  code: string | null;
  case_id: string | null;
  outcome: Outcome | null;
  item_source: Source | null;
  title: string | null;
  upload_date: string | null;
  meeting_id: number | null;
  meeting_date: string | null;
  body: string | null;
  score: number;
}

export interface FindResult {
  query: string;
  by_code: boolean;
  record: {
    total: number;
    items: RecordHit[];
    /** Every term matched nothing, so the search widened to any term. */
    loosened: boolean;
  };
  transcript: {
    hits: TranscriptHit[];
    count: number;
    /** Non-null when the semantic arm was unavailable: keywords only. */
    degraded: string | null;
  };
}

/* --------------------------------------------------------------------- ask
 * Served by ../../web/agent.py over SSE. The agent calls the same tools
 * /search calls (D9), so its evidence arrives in the same two shapes and this
 * page renders them with the same components. */

/** One progress event. `stage` is what the agent is doing, not a fixed step. */
export interface AskStage {
  stage:
    | "thinking"      // deciding what to call next
    | "tool"          // calling one
    | "tool_done"
    | "answering"
    | "checking"      // verifying its own citations
    | "done";
  step?: number;
  id?: string;
  name?: string;
  args?: Record<string, unknown>;
  ok?: boolean;
  passages?: number;
  items?: number;
  why?: string;
  cited?: number;
  struck?: number;
}

export interface AskResult {
  question: string;
  answer: string;
  /** Only what the answer CITED — see agent.ask. */
  evidence: TranscriptHit[];
  record: RecordHit[];
  trace: { name: string; args: Record<string, unknown>; ok: boolean; chars: number }[];
  /** Looked at but not used. The gap between searched and cited, stated. */
  looked_at: { passages: number; items: number };
  /** Citations removed because this run never saw them. */
  struck: string[];
  /** Non-null when the agent hit a cap rather than finishing on its own. */
  stopped: string | null;
}

export interface Facets {
  bodies: { body: string; items: number }[];
  phases: { phase: string; items: number }[];
  outcomes: { outcome: Outcome; items: number }[];
  speakers: { speaker: string; lines: number }[];
  years: { year: string; meetings: number }[];
}

export interface Body {
  body: string;
  meetings: number;
  first: string;
  last: string;
  recorded: number;
  with_agenda: number;
}
