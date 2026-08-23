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
 * One TIME the board took an item up — spans merged when they are
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
  outcome_text: string | null;
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
 * How a name was arrived at. These were four values and are now the resolver's
 * METHOD, which says strictly more: `self` is the
 * speaker naming themselves, `chair` is somebody else naming them in the room,
 * `read_aloud` means the words belong to a person who was never there.
 * SpeakerChip maps these to how sure the page looks, in one place.
*/
/**
 * WHO SAID THIS. One object, sent by every surface that names a speaker, built
 * once by web/archive.py's `who()`. Three shapes used to spell these four
 * facts three ways and the UI had to know which; guessing wrong read as "no
 * name" rather than raising.
 *
 * `(exchange)` never appears here. The API cannot emit it: a passage spanning
 * several people arrives as `several: true` with no name, which is what
 * has always required and what two separate files used to enforce by
 * hand.
 */
export interface Speaker {
  /** The KEY, and what a correction is written against. A board member's surname. */
  name: string | null;
  /** What to print. Falls back to the key. */
  display_name: string | null;
  basis: SpeakerBasis;
  human: boolean;
  contested: boolean;
  /** More than one person speaks here. `name` is null when this is true. */
  several: boolean;
}

export type SpeakerBasis =
  | "override" | "human" | "label"
  | "self" | "self_weak" | "rollcall" | "chair" | "llm"
  | "voice" | "cluster" | "read_aloud"
  | null;

/**
 * One line of transcript. Speaker identity arrives as FIELDS, never as a
 * rendered string, so exactly one component decides how to show it
 * and a future redaction rule has one place to act.
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
  /**
   * The resolved name, and the KEY. A board member is keyed by surname
   * throughout — the roster, the filters and every correction are written
   * against it — so this is what to send back, never what to print.
   */
  name: string | null;
  /**
   * What to print: `name` with a board member's surname expanded to the full
   * name on the county's published roster. Everyone else is unchanged. Always
   * render through SpeakerChip, which prefers this and falls back to `name`.
   */
  display_name: string | null;
  /** The same facts as the loose fields above, in the shape SpeakerChip takes. */
  who: Speaker;
  /** A person stated this. Outranks everything derived. */
  human: boolean;
  /**
   * How the name was arrived at. These are four very different claims and the
   * UI must not render them identically:
   *   override  a person, about this stretch of speech
   *   human     a person, about this whole voice
   *   voice     the pipeline, about this voice in THIS meeting
   *   cluster   the pipeline, about this voice archive-wide — the weakest,
   *             and the one that put two different women under one name
   */
  basis: SpeakerBasis;
  /** A correction is pending. Show it as disputed, take no side. */
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
  /** surname -> the office they held AT THIS MEETING. */
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
  outcome_text: string | null;
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
   *. `url` is still the county's direct link and stays offered.
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
  /** Every appearance of this case, including this one. */
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
  outcome_text: string | null;
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
 * item up twice in a day, possibly two for one meeting.
 */
export interface CaseHearing extends ItemRun {
  item_id: number;
  code: string | null;
  meeting_id: number;
  date: string;
  body: string;
  phase: string;
  outcome: Outcome | null;
  outcome_text: string | null;
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
  /** The full official title, said once. */
  title: string | null;
  steps: CaseStep[];
  bodies: string[];
  first: string;
  last: string;
  /**
   * The last appearance that actually decided something. A continuance is the
   * board saying "not today", never a conclusion, so a case whose final step
   * was continued has no terminal outcome and is still open.
   */
  terminal: {
    id: number;
    date: string;
    body: string;
    outcome: Outcome;
    outcome_text: string | null;
  } | null;
  continuances: number;
  recorded: number;
  /**
   * Everything said about this application, across every meeting that took it
   * up, in the order it happened. Empty for the 92% of cases with no
   * recording behind any appearance.
   */
  heard: CaseHearing[];
  /** Per MEETING, because offices rotate and a case can span years. */
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

/* ------------------------------------------------------- browse */

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
  outcome_text: string;
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
  speaker_display: string;
  /** How the name was established — one utterance, so no reduction. See Line. */
  human: boolean;
  basis: SpeakerBasis;
  who: Speaker;
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
  /** Of `items`, how many the approved minutes record any outcome for. The
   *  denominator `pushed` is read against: 0 pushed out of 0 decided is the
   *  minutes being silent, not the board being unanimous. */
  decided: number;
  /**
   * Of `decided`, how many the board did not simply pass — continued, denied,
   * no action, or an outcome naming a nay vote.
  */
  pushed: number;
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
  /**
   * The `slug` of the subject this one narrows, or null for a top-level one.
  */
  parent: string | null;
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
   *  is a different fact from an issue nobody discussed. */
  heard_from: string;
  issues: Issue[];
}

/* ------------------------------------------------------------------ search
 * Served by ../../web/tools.py, which is the same surface the agent calls
 *. Two sources, two shapes, never merged into one ranked list: "this was
 * approved" and "somebody said this" are not comparable. */

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

/**
 * One speaker's contiguous run inside a multi-speaker passage.
 *
 * `passages.speaker` says `(exchange)` for 57% of the corpus, and the passage
 * text renders those as one string with the names buried in it. These are the
 * same words split back into who actually said them, resolved from
 * `utterances`, where they have always been one row per speaker.
 *
 * THERE IS NO TURN-LEVEL CITATION. `passage_id` is the citable unit and the
 * only one web/agent.py's check() will verify; a turn says who and where, not
 * what to cite. `start` is seconds into `video_id`, for seeking.
 */
export interface Turn {
  /** Position within the passage, from 1. */
  n: number;
  /** The passage this turn belongs to, and the id any citation must use. */
  passage_id: number | null;
  video_id: string;
  /** The key the `speaker` facet filters on. A board member's surname. */
  speaker: string | null;
  /** What to print. See Line.display_name. */
  speaker_display: string | null;
  /** The same shape every other surface draws a speaker from. */
  who: Speaker;
  /** Utterance range, the durable key within the recording. */
  start_idx: number;
  end_idx: number;
  /** Seconds into the recording. */
  start: number;
  end: number;
  text: string;
}

export interface TranscriptHit {
  id: number;
  video_id: string;
  start: number;
  end: number;
  /** The key the `speaker` facet filters on. A board member's surname. */
  speaker: string | null;
  /** What to print. See Line.display_name. */
  speaker_display: string | null;
  /**
   * Who said what, when this passage crosses speakers. Null when it does not,
   * because a single-speaker passage already answers the question in
   * `speaker`. Never renders a citation of its own - see Turn.
   */
  turns: Turn[] | null;
  /**
   * The utterances this passage covers: its NATURAL key - `id` is reassigned
   * by every rebuild - and the range a correction is raised against, which is
   * what lets a hit reach the review screen at all.
  */
  start_idx: number | null;
  end_idx: number | null;
  /**
   * How well the speaker name is known — the same two fields as `Line`, and
   * for the same reason: a passage carries a name that may have been stated
   * by a person, matched to a voice at that meeting, or merely inherited from
   * what that voice is called across the whole archive, and those are three
   * different claims. Reduced from the passage's utterances by
   * bin/schema.sql's `passage_speaker`, WORST case, so that one shaky line
   * makes the whole attribution shaky.
   *
   * Null when the passage has no named utterance to be sure about, and on any
   * row that predates the field. Render through SpeakerChip, never by hand.
   */
  name_human: boolean | null;
  name_basis: SpeakerBasis;
  /** The rendering shape. `speaker` above stays the facet key. */
  who: Speaker;
  text: string;
  phase: string | null;
  agenda_item_id: number | null;
  /** The item this sits under — without it a hit is often unreadable. */
  item: string | null;
  code: string | null;
  case_id: string | null;
  outcome: Outcome | null;
  item_source: Source | null;
  title: string | null;
  upload_date: string | null;
  meeting_id: number | null;
  meeting_date: string | null;
  /**
   * WHICH RECORDING the `start` above is a time on, and how many that meeting
   * has. Half of all meeting-days are two videos on one continuous agenda, so
   * a bare clock does not say which tape it is on — and neither field means
   * anything without the other: `session_seq` is null on 48 videos that share
   * a meeting with another, and 0 on three that do not. Read through
   * `recordingName`, never by hand.
   */
  session_seq: number | null;
  sessions: number;
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
    /** Where this window starts, and where the next one does. There is no
     *  total: the record arm counts its matches in SQL and can say one, and
     *  ranking cannot without describing its own candidate pool instead of
     *  the archive. `next_offset` is null when the ranked results run out. */
    offset: number;
    returned: number;
    next_offset: number | null;
    truncated: boolean;
    /** Non-null when the semantic arm was unavailable: keywords only. */
    degraded: string | null;
  };
}

/* --------------------------------------------------------------------- ask
 * Served by ../../web/agent.py over SSE. The agent calls the same tools
 * /search calls, so its evidence arrives in the same two shapes and this
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
  /** The kept run's id, from `web/answers.py` — what `/ask/<id>` reads and
   *  what makes an answer sendable. Absent if the row could not be written;
   *  the answer is still the answer, it just has no link. */
  id?: string;
  /** When the run happened, on a saved answer only. An answer is a reading of
   *  the archive on a particular day and an undated one would be claiming to
   *  be current. ISO 8601. */
  asked_at?: string;
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
  /**
   * Saved answers only. A saved answer stores what it CITED, not the words —
   * the quotes are read back out of the archive when this renders — so a
   * passage whose boundaries have moved since (which a redaction does) no
   * longer resolves. Counted rather than quietly dropped.
   */
  missing?: { passages: number; items: number };
  /** Non-null when the agent hit a cap rather than finishing on its own. */
  stopped: string | null;
}

export interface Facets {
  bodies: { body: string; items: number }[];
  phases: { phase: string; items: number }[];
  outcomes: { outcome: Outcome; items: number }[];
  /** `speaker` is the filter value and goes in the URL; `speaker_display` is the label. */
  speakers: { speaker: string; speaker_display: string; lines: number }[];
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

/**
 * The machine surface, as /api/tools describes it.
*/
/**
 * The archive measured, for copy that would otherwise quote a number.
*/
export interface Facts {
  /** Hours of transcribed recording, ALL of it - including the recordings
   *  that belong to no meeting, because search reaches those too. */
  hours: string;
  items: string;
  meetings: string;
  recorded: string;
  /** Published items the minutes never record an outcome for. */
  pct_no_outcome: string;
  recurring: string;
  /** Decided items whose discussion the transcript can actually reach. */
  pct_transcript: string;
  pct_no_name: string;
  first_year: string;
  last_year: string;
  /** The earliest year a recording exists for. */
  first_rec_year: string;
  deep_case: string;
  deep_case_meetings: string;
}

export interface ToolsManifest {
  tools: { name: string; description: string }[];
  /** What is wrong with the dense arm, and null when nothing is. */
  dense: string | null;
  /** What the dense arm is DOING, which `dense: null` cannot say: it reads the
   *  same for a model loaded before the port opened and for one nothing ever
   *  tried to load. Absent when web/server.py predates the field. */
  dense_state?: {
    state: "cold" | "loading" | "ready" | "failed";
    /** Seconds startup spent loading the weights, or null if startup did not.
     *  Null next to "ready" means a reader paid for the load instead. */
    warmed_in: number | null;
    error: string | null;
  };
  mcp: {
    path: string;
    /** Tool calls allowed from one address per `window` seconds. */
    per_ip: number;
    /** The lower ceiling, for the tools named in `heavy`. */
    heavy_per_ip: number;
    window: number;
    heavy: string[];
  };
}
