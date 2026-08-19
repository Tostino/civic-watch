import type { Item, Outcome, RosterEntry } from "./types";

/** Media time. Hours only when there are hours - most meetings run past one. */
export function clock(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "";
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return `${h ? `${h}:` : ""}${mm}:${String(sec).padStart(2, "0")}`;
}

/** A duration to read, not to seek to.
 *
 * Empty below half a minute rather than "0m", which is what it used to return
 * and which is never a thing anyone wants to read. Two call sites already had
 * `duration(x) || "under a minute"` written in the expectation that it behaved
 * this way; neither worked, because "0m" is truthy. */
export function duration(seconds: number | null | undefined): string {
  if (!seconds || seconds < 30) return "";
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  if (h && m) return `${h}h ${m}m`;
  if (h) return `${h}h`;
  return `${m}m`;
}

/** Dates arrive as YYYY-MM-DD. Parsing them as UTC avoids the off-by-one-day
 *  that `new Date('2021-12-07')` gives west of Greenwich. */
export function meetingDate(iso: string, style: "long" | "short" = "long"): string {
  /* An empty string used to reach `Date.UTC(0, 0, 1)` and print "Monday,
     January 1, 1900" — a date this archive invented, on the evidence heading
     for the 17 recordings that have no date at all. Nothing here may make one
     up: callers with no date get nothing back and say so in their own words. */
  if (!/^\d{4}-\d{2}-\d{2}/.test(iso ?? "")) return "";
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(Date.UTC(y, (m ?? 1) - 1, d ?? 1));
  return dt.toLocaleDateString("en-US", {
    weekday: style === "long" ? "long" : undefined,
    year: "numeric",
    month: style === "long" ? "long" : "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export function isoWeekday(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, (m ?? 1) - 1, d ?? 1)).toLocaleDateString("en-US", {
    weekday: "short",
    timeZone: "UTC",
  });
}

const OUTCOME_LABEL: Record<Outcome, string> = {
  approved: "Approved",
  adopted: "Adopted",
  denied: "Denied",
  withdrawn: "Withdrawn",
  continued: "Continued",
  received: "Received",
  no_action: "No action",
  tabled: "Tabled",
  other: "Other",
};

export const outcomeLabel = (o: Outcome | null): string =>
  o ? (OUTCOME_LABEL[o] ?? o) : "No outcome in the minutes";

/** How an outcome reads semantically. `none` is deliberately its own state:
 *  "the minutes recorded nothing for this" is not an outcome. */
export function outcomeTone(o: Outcome | null): "ok" | "no" | "wait" | "neutral" | "none" {
  switch (o) {
    case "approved":
    case "adopted":
      return "ok";
    case "denied":
      return "no";
    case "continued":
      return "wait";
    case null:
      return "none";
    default:
      return "neutral";
  }
}

const PHASE_LABEL: Record<string, string> = {
  consent: "Consent agenda",
  public_hearing: "Public hearing",
  regular: "Regular business",
  proclamation: "Proclamation",
  board_reports: "Board reports",
  call_to_order: "Call to order",
  public_comment: "Public comment",
  staff_report: "Staff report",
  adjourn: "Adjournment",
  recess: "Recess",
  other: "Other",
};

export const phaseLabel = (p: string): string =>
  PHASE_LABEL[p] ?? p.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

/**
 * Where this item's outcome came from, in words rather than in the
 * pipeline's token. `bulk_consent` is an internal value and printing it is the
 * same mistake as rendering `Group 465` where a name goes — it reads
 * as a fact about the record and it is a fact about our parser.
 *
 * It is worth saying at all because these are materially different claims: the
 * minutes named this item and its outcome, or the minutes approved a block
 * and this item was in the block.
 */
const OUTCOME_SOURCE_LABEL: Record<string, string> = {
  item: "the minutes, which name this item and its outcome",
  bulk_consent: "the consent agenda, which the board approved as one block",
  bulk_included: "a block motion that named this item",
  bulk_exception: "a block motion that removed this item from the block",
};

export const outcomeSourceLabel = (s: string | null): string | null =>
  s ? (OUTCOME_SOURCE_LABEL[s] ?? null) : null;

const OFFICE_LABEL: Record<string, string> = {
  chair: "Chair",
  vice_chair: "Vice Chair",
  second_vice_chair: "Second Vice Chair",
};

export const officeLabel = (o: RosterEntry["office"]): string | null =>
  o ? (OFFICE_LABEL[o] ?? o) : null;

/**
 * Agenda titles are legal prose and routinely run past 60 words. The spine
 * needs the shape of the sequence, so it gets a first clause; the item page
 * keeps the whole thing. Cuts at a sentence or clause boundary where there is
 * one nearby, never mid-word.
 */
export function shortTitle(title: string | null, max = 110): string {
  if (!title) return "";
  const t = title.replace(/\s+/g, " ").trim();
  if (t.length <= max) return t;
  const window = t.slice(0, max);
  const brk = Math.max(window.lastIndexOf(" – "), window.lastIndexOf(" - "), window.lastIndexOf("; "));
  if (brk > max * 0.5) return t.slice(0, brk).trim() + "…";
  const sp = window.lastIndexOf(" ");
  return t.slice(0, sp > 0 ? sp : max).trim() + "…";
}

/* ------------------------------------------------------- redlined
 *
 * A case's official title is legal prose that runs to 400 characters and is
 * repeated verbatim at every appearance. The archive's longest case says this
 * twelve times:
 *
 *   "Zoning Amendment (Continuance) – Evans County Line 80 MPUD Master Planned
 *    Unit Development – Evans Properties, Inc. – A Rezoning Petition from A-C
 *    Agricultural District to an MPUD … on Approximately 80 Acres, Located
 *    South of County Line Road North and East of Lake Iola Road"
 *
 * and the only thing that moves across those twelve is `(Continuance)` becoming
 * `(Regular)`, and a clause about support commercial appearing halfway through.
 * Printed twelve times, that difference is invisible; it is also the single
 * most informative thing on the page, because it is where the application
 * actually changed.
 *
 * So the case states the title once and redlines each step against it. A
 * redline is the right idiom rather than a clever one: this audience reads
 * marked-up ordinances and planning documents already, and it shows additions
 * AND deletions, where a "what's new" diff would render a step that dropped a
 * clause identically to one that changed nothing.
 */
export type Op = "same" | "add" | "cut";
export interface TitleRun {
  text: string;
  op: Op;
}

/** Longest common subsequence over words, backtracked into runs. */
export function redlineTitle(canonical: string | null, title: string | null): {
  runs: TitleRun[];
  identical: boolean;
} {
  const a = (canonical ?? "").split(/\s+/).filter(Boolean);
  const b = (title ?? "").split(/\s+/).filter(Boolean);
  if (!a.length || !b.length || a.join(" ") === b.join(" ")) {
    return { runs: [{ text: b.join(" "), op: "same" }], identical: true };
  }

  // (a.length+1) × (b.length+1). Titles run to ~70 words, so this is a few
  // thousand cells per step and not worth being clever about.
  const L: number[][] = Array.from({ length: a.length + 1 }, () =>
    new Array<number>(b.length + 1).fill(0),
  );
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      L[i][j] = a[i] === b[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
    }
  }

  const ops: { word: string; op: Op }[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      ops.push({ word: b[j], op: "same" });
      i++;
      j++;
    } else if (L[i + 1][j] >= L[i][j + 1]) {
      ops.push({ word: a[i++], op: "cut" });
    } else {
      ops.push({ word: b[j++], op: "add" });
    }
  }
  while (i < a.length) ops.push({ word: a[i++], op: "cut" });
  while (j < b.length) ops.push({ word: b[j++], op: "add" });

  const runs: TitleRun[] = [];
  for (const { word, op } of ops) {
    const last = runs[runs.length - 1];
    if (last && last.op === op) last.text += ` ${word}`;
    else runs.push({ text: word, op });
  }
  return { runs, identical: !runs.some((r) => r.op !== "same") };
}

/** Unchanged stretches longer than this collapse to an ellipsis: the point of
 *  a redline here is the difference, not a second copy of the title. */
export const CONTEXT_WORDS = 4;

export function elide(text: string, words = CONTEXT_WORDS): string | null {
  const w = text.split(/\s+/).filter(Boolean);
  if (w.length <= words * 2) return text;
  return `${w.slice(0, words).join(" ")} … ${w.slice(-words).join(" ")}`;
}

/**
 * Are these two strings the same identifier or heading, differing only in the
 * punctuation, case and spacing the agenda happens to use?
 *
 * The published agenda supplies several fields that overlap, and printing both
 * halves of an overlap is how a dense page turns into a noisy one:
 * `file_number` "PDE25-7721" is `case_id` "PDE-25-7721" before normalisation,
 * and `section` "PUBLIC HEARINGS" is the same fact as phase "Public hearing".
 */
export function sameThing(a: string | null, b: string | null): boolean {
  if (!a || !b) return false;
  const norm = (v: string) => v.toLowerCase().replace(/[^a-z0-9]/g, "");
  const [x, y] = [norm(a), norm(b)];
  if (!x || !y) return false;
  // Containment, not equality: "public hearings" vs "public hearing".
  return x === y || x.startsWith(y) || y.startsWith(x);
}

/**
 * "Board of County Commissioners" is 29 characters and appears on every row of
 * a case thread and every entry point on Browse.
 *
 * Lives here rather than beside its first caller: it started life exported
 * from CaseThread, which is a `"use client"` module, and importing a plain
 * function out of one into a server component fails at render with "Attempted
 * to call shortBody() from the server". A pure helper belongs in a module with
 * no directive, where either side can have it.
 */
export function shortBody(body: string): string {
  if (/board of county commissioners/i.test(body)) return "Board";
  if (/planning/i.test(body)) return "Planning";
  return body;
}

/** Where an item sits in the recordings, if anywhere. */
export const itemStart = (item: Item): { video_id: string; start: number } | null =>
  item.spans.length ? { video_id: item.spans[0].video_id, start: item.spans[0].start } : null;

/** "Morning session" / "Afternoon session" - about half of all meeting-days
 *  are two recordings on one continuous agenda. */
export function sessionLabel(seq: number | null, total: number): string {
  if (total <= 1) return "Recording";
  if (seq === 0) return "Morning session";
  if (seq === 1) return "Afternoon session";
  return `Session ${(seq ?? 0) + 1}`;
}

/**
 * WHICH RECORDING a timestamp is on, in as few words as it takes.
 *
 * A clock alone is only usable by somebody already looking at the right tape,
 * and an answer's citations are not: one paragraph of a live answer cited
 * seven recordings across five years, every one of them printed as a bare
 * `1:46:53`. Two of those were the morning and the afternoon of the same day,
 * where even the date does not separate them — hence the session word, which
 * is said only where `sessions` proves the meeting HAS more than one and
 * `session_seq` says which this is. An undated video falls back to its own
 * title, because "Constitutional Budgets Workshop" is still a recording a
 * reader can go and find and an empty string is not.
 */
export function recordingName(h: {
  meeting_date?: string | null;
  upload_date?: string | null;
  title?: string | null;
  session_seq?: number | null;
  sessions?: number | null;
}): string {
  const when = h.meeting_date || h.upload_date;
  // 17 recordings carry no date at all. What names those is their own title,
  // minus the boilerplate every title on the channel opens with: cut to length
  // from the front, "Pasco Board of County Commissioners Constitutional
  // Budgets Workshop" becomes "Pasco Board of County Commissioners…", which
  // names 432 recordings and this one no better than the bare clock did.
  if (!when)
    return h.title
      ? shortTitle(
          h.title.replace(
            /^\s*(?:pasco\s+)?(?:county\s+)?(?:board of county commissioners|planning commission|bcc)\b[\s\-\u2013\u2014:,]*/i,
            "",
          ) || h.title,
          36,
        )
      : "";
  const date = meetingDate(when, "short");
  if ((h.sessions ?? 0) < 2 || h.session_seq == null) return date;
  // The same naming the meeting page uses, in the lower case of a sentence:
  // one archive cannot call it "Afternoon session" in one place and "part 2"
  // in another and expect a reader to know they are the same tape.
  const which = sessionLabel(h.session_seq, h.sessions ?? 0)
    .replace(/ session$/, "")
    .toLowerCase();
  return `${date} ${which}`;
}

/**
 * Split text on the query's terms so a hit can show WHY it matched.
 *
 * Postgres matched "cameras" through the stem `camera`, so an exact-word
 * highlighter leaves the word that caused the hit unmarked, which reads as a
 * bug. This strips one common English ending and then allows up to three
 * characters back — enough for camera/cameras and license/licensed, and
 * bounded, because open-ended prefixes marked "platform" for a search for
 * "plate".
 */
export function highlight(text: string, query: string): { s: string; hit: boolean }[] {
  const terms = query
    .toLowerCase()
    .split(/[^\p{L}\p{N}'-]+/u)
    .filter((t) => t.length >= 3 && !STOP.has(t))
    .map(stem);
  if (!terms.length) return [{ s: text, hit: false }];

  const re = new RegExp(`\\b(${terms.map(escapeRe).join("|")})\\p{L}{0,3}\\b`, "giu");
  const out: { s: string; hit: boolean }[] = [];
  let at = 0;
  for (const m of text.matchAll(re)) {
    if (m.index > at) out.push({ s: text.slice(at, m.index), hit: false });
    out.push({ s: m[0], hit: true });
    at = m.index + m[0].length;
  }
  if (at < text.length) out.push({ s: text.slice(at), hit: false });
  return out;
}

/* `-` is deliberately NOT escaped. It is only special inside a character
 * class, and `\-` outside one is an *invalid escape* under the `u` flag — so
 * escaping it threw `SyntaxError` on the first query with a hyphen in it,
 * which is to say on `R-58`, the identifier the placeholder advertises. */
const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** One ending off, and only when what is left is still a word. */
function stem(t: string): string {
  for (const end of ["ies", "ing", "es", "ed", "s"]) {
    if (t.endsWith(end) && t.length - end.length >= 4) return t.slice(0, -end.length);
  }
  return t;
}

/** Postgres' english stop words, near enough — marking these marks nothing. */
const STOP = new Set([
  "the", "and", "for", "was", "were", "are", "with", "that", "this",
  "from", "have", "has", "had", "not", "but", "all", "any", "its", "our",
  "out", "who", "what", "when", "how", "why", "did", "does", "about",
]);
