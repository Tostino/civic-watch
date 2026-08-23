"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Citation } from "@/components/Citation";
import { ProvenanceMark } from "@/components/ProvenanceMark";
import { usePlayer, usePlayhead } from "@/components/player/PlayerProvider";
import { duration, meetingDate, officeLabel, sessionLabel } from "@/lib/format";
import type { Item, MeetingDetail, Span } from "@/lib/types";
import { AgendaSpine } from "./AgendaSpine";
import { RecordView } from "./RecordView";
import { TranscriptView } from "./TranscriptView";
import s from "./MeetingView.module.css";

/**
 * `/meeting/:id` - the page the old UI most conspicuously lacked, and the one
 * a reader most wants.
*/

/** Where the reader is, restored from the URL. */
export interface MeetingLocation {
  videoId?: string;
  t?: number;
  /**
   * Where the recording stops on its own, in seconds.
   *
   * `t` is where a link was aimed; this is how much of the recording the link
   * was about. It arrives on citations from the agent and from MCP, where the
   * whole point is that a stranger's client can hand a reader forty seconds of
   * a hearing and give them the archive back afterwards rather than leaving a
   * county meeting running in a tab. Arrival state only: the player disarms it
   * as it fires, and a reader who scrubs has already said they want more.
   */
  end?: number;
  item?: number;
  /** Utterance range to mark, inclusive. From a search hit's passage. */
  focus?: [number, number];
}

/**
 * An explicit instruction to go somewhere, as opposed to the transcript
 * passively following playback.
 *
 * These are different things and conflating them was a real bug: the follow
 * effect was gated on `following`, so once a reader had scrolled the
 * transcript by hand, clicking an item on the spine moved the recording and
 * left the transcript where it was. A click is not a preference about
 * auto-scrolling - it is "take me there" - and it must always be obeyed.
 *
 * The counter is what makes it a signal rather than a value: clicking the same
 * item twice must scroll twice.
 */
export interface Cue {
  videoId: string;
  seconds: number;
  /** Where to stop, when the cue came from a citation. Null for "play on". */
  until: number | null;
  n: number;
}

export function MeetingView({
  data,
  location,
}: {
  data: MeetingDetail;
  location: MeetingLocation;
}) {
  const { meeting, videos, items, roster, coverage, portal, files } = data;
  const player = usePlayer();
  const playhead = usePlayhead();

  const hasRecording = videos.length > 0;
  const hasAgenda = coverage.items > 0;

  /* Where a shared link opens. An `?item=` may belong to either recording of a
   * two-session meeting-day, so it gets to pick the session when `?v=` does
   * not - otherwise a link to an afternoon item opens the morning. */
  const linkedSpan = useMemo(
    () =>
      location.item == null
        ? null
        : (items.find((i) => i.id === location.item)?.spans[0] ?? null),
    [items, location.item],
  );
  const initialVideo =
    (location.videoId && videos.some((v) => v.id === location.videoId)
      ? location.videoId
      : null) ?? linkedSpan?.video_id ?? videos[0]?.id ?? null;

  const [videoId, setVideoId] = useState<string | null>(initialVideo);
  const [view, setView] = useState<"transcript" | "record">(
    hasRecording ? "transcript" : "record",
  );
  /** Set by clicking the spine; the playhead takes over once audio rolls. */
  const [pinnedItem, setPinnedItem] = useState<number | null>(location.item ?? null);
  /** The item the reader has scrolled to, when nothing is playing. */
  const [readingItem, setReadingItem] = useState<number | null>(null);
  /* A restored link IS an explicit cue, so it is initial state rather than an
   * effect - the transcript must open at the shared moment, not scroll to it
   * after a render.
   *
   * `?t=` is exact. `?item=` alone still has to land somewhere, or a link to
   * an item silently opens at the top of a six-hour recording, so it falls
   * back to where that item begins. */
  const [cue, setCue] = useState<Cue | null>(() => {
    if (!initialVideo) return null;
    if (location.t != null) {
      return {
        videoId: initialVideo,
        seconds: location.t,
        until: location.end ?? null,
        n: 1,
      };
    }
    if (linkedSpan && linkedSpan.video_id === initialVideo) {
      /* An `?item=` alone gets no stop point, for the reason seek() gives
         below: an item is a place to start listening, not a claim that the
         answer ends there. Only an explicit `end` arms it. */
      return { videoId: initialVideo, seconds: linkedSpan.start, until: null, n: 1 };
    }
    return null;
  });

  const video = videos.find((v) => v.id === videoId) ?? videos[0] ?? null;

  const sourceFor = useCallback(
    (vid: string) => {
      const v = videos.find((x) => x.id === vid);
      return {
        videoId: vid,
        title: `${meeting.body} · ${meetingDate(meeting.date, "short")}${
          v && videos.length > 1 ? ` · ${sessionLabel(v.session_seq, videos.length)}` : ""
        }`,
        href: `/meeting/${meeting.id}`,
        duration: v?.duration,
      };
    },
    [meeting.body, meeting.date, meeting.id, videos],
  );

  /** Every explicit "take me to this moment" goes through here. */
  const seek = useCallback(
    /* No `until` on this path, and that is the design rather than an omission.
       Clicking a line of the transcript, or an item on the spine, is a reader
       settling in to LISTEN from there; a stop point belongs to a citation,
       which is somebody else's claim about which forty seconds answered a
       question. */
    (vid: string, seconds: number, autoplay = true) => {
      setVideoId(vid);
      setView("transcript");
      setCue((c) => ({ videoId: vid, seconds, until: null, n: (c?.n ?? 0) + 1 }));
      player.play(sourceFor(vid), seconds, autoplay);
    },
    [player, sourceFor],
  );

  // A shared link loads its recording at the moment it was taken, but
  // CUES rather than plays: arriving at a page that starts talking at you is
  // hostile, and browsers block it anyway.
  const restored = useRef(false);
  useEffect(() => {
    if (restored.current || !cue) return;
    restored.current = true;
    player.play(sourceFor(cue.videoId), cue.seconds, false, cue.until);
    // Runs once, for the cue the page was built with; later cues come from
    // seek(), which drives the player itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Which item is on screen: the one whose span contains the playhead. */
  const playingItem = useMemo(() => {
    if (!playhead.videoId) return null;
    for (const it of items) {
      for (const sp of it.spans) {
        if (sp.video_id === playhead.videoId && playhead.position >= sp.start && playhead.position < sp.end) {
          return it.id;
        }
      }
    }
    return null;
  }, [items, playhead.videoId, playhead.position]);

  // Playback is authoritative when it is running. Otherwise the reader is,
  // and where they have scrolled to is a better answer than what they last
  // clicked.
  const activeItem = playhead.playing
    ? (playingItem ?? pinnedItem)
    : (readingItem ?? pinnedItem ?? playingItem);

  // The URL is the state: a link must reproduce the view. replaceState
  // rather than the router, because this fires as the recording plays and a
  // history entry per second would make Back useless.
  const lastUrl = useRef("");
  useEffect(() => {
    if (!video) return;
    const q = new URLSearchParams();
    q.set("v", video.id);
    if (playhead.videoId === video.id && playhead.position > 1) {
      q.set("t", String(Math.floor(playhead.position)));
    }
    if (activeItem) q.set("item", String(activeItem));
    // The marked range survives the rewrite. This effect rebuilds the query
    // from the view's own state and drops anything it does not know about,
    // which silently ate `from`/`to` the first time the playhead moved - the
    // link worked, the transcript scrolled, and the highlight vanished before
    // a reader could see it. It is arrival state rather than playback state,
    // so it is carried rather than recomputed, and a shared link still marks
    // the same words.
    if (location.focus) {
      q.set("from", String(location.focus[0]));
      q.set("to", String(location.focus[1]));
    }
    /* Carried like `focus` - this effect rebuilds the query from scratch and
       drops whatever it does not know about - but only while it is still
       ahead of the playhead. Once the recording has run past the cited
       stretch, `end` is a lie about this view, and copying the address bar
       would hand somebody a link that stops before it starts. */
    if (
      location.end != null &&
      (playhead.videoId !== video.id || playhead.position < location.end)
    ) {
      q.set("end", String(location.end));
    }
    const url = `${window.location.pathname}?${q}`;
    if (url === lastUrl.current) return;
    lastUrl.current = url;
    window.history.replaceState(null, "", url);
  }, [video, playhead.videoId, playhead.position, activeItem, location.focus, location.end]);

  const selectItem = useCallback(
    /* `at` is the appearance the reader clicked. The spine lists an item once
     * per stretch it was discussed in, so "the item's first span" is
     * the wrong destination for the row that says 3:38 - it would answer a
     * click by seeking two hours backwards. */
    (item: Item, at?: Span | null) => {
      setPinnedItem(item.id);
      setReadingItem(null);
      const sp = at ?? item.spans.find((x) => x.video_id === videoId) ?? item.spans[0];
      if (sp) seek(sp.video_id, sp.start);
      else setView("record");
    },
    [seek, videoId],
  );

  /**
   * The masthead is this meeting's identity card, and it is worth its full
   * height exactly once: on arrival, before the reader knows what they are
   * looking at. After that it is 255px of a 900px window spent on six rows
   * that never change again, while the transcript they came for gets 46%.
   *
   * It cannot simply scroll away. The rail and the panel are their own
   * scrollers and chain to the page only at their ends, so a wheel over the
   * transcript never reaches the page and a masthead that left on page scroll
   * would never leave. The signal is ENGAGEMENT instead: the first wheel,
   * touch or key inside the panes, or the moment audio starts. Nothing is
   * lost - the coverage, the roster and the county's documents are all on
   * screen when the page opens, and one click brings them back.
  */
  const [brief, setBrief] = useState(false);

  /* How tall the panes may be, MEASURED rather than guessed. Two constants
   * stood in for this - 14rem in the transcript, --sp-6 in the rail - and
   * neither knows how tall this meeting's masthead is, so both panes hung off
   * the bottom of the window by a fixed amount at every screen size (176px and
   * 239px at 1040px tall). The masthead is what varies: a meeting with two
   * recordings and a roster is taller than one with neither. */
  const splitRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = splitRef.current;
    if (!el) return;
    const measure = () => {
      /* `top` is the split's BORDER box; the panes start inside its padding and
       * the article adds its own below. Reading those three insets is what
       * takes the page scroll to zero rather than to "nearly zero" - and a page
       * that scrolls 32px to reveal padding is the same defect as one that
       * scrolls 283px to reveal a footer, just quieter.
       *
       * These are read from boxes AROUND the panes, never from the panes
       * themselves, so setting --pane-h cannot change what this measures. That
       * is the difference between this and the convergence loop it replaced,
       * which fed its own output back in and oscillated. */
      const top = el.getBoundingClientRect().top + window.scrollY;
      const cs = getComputedStyle(el);
      const article = el.parentElement;
      const main = article?.parentElement ?? null;
      const inset =
        parseFloat(cs.paddingTop || "0") +
        parseFloat(cs.paddingBottom || "0") +
        (article ? parseFloat(getComputedStyle(article).paddingBottom || "0") : 0) +
        /* `main` reserves the dock's room now (globals.css), and it is one
           more box between these panes and the bottom of the window. Left
           out, a dock across the bottom would make every pane that much too
           tall and put the page back to scrolling to reveal nothing. */
        (main ? parseFloat(getComputedStyle(main).paddingBottom || "0") : 0);
      const room = window.innerHeight - top - inset;
      /*
       * A FLOOR NEEDS A CEILING. The 288 keeps a pane usable when a tall
       * masthead has eaten the window, and on its own it will happily return
       * a pane taller than the window can ever show: at 882x344 - a foldable
       * on its side, or any window dragged short - the room is 244, the floor
       * made it 288, and the 44 it did not have went UP behind the sticky
       * header once the page scrolled, taking the top of the transcript with
       * it. Measured 28px of it hidden.
       *
       * `most` is the tallest a pane could ever be and still be seen whole:
       * the window, less the header it would sit under, less the insets. Like
       * the rest of this measurement it is read from boxes AROUND the panes,
       * so it cannot feed its own output back in.
       */
      const probe = document.createElement("div");
      probe.style.cssText = "position:absolute;visibility:hidden;height:var(--header)";
      el.appendChild(probe);
      const headerH = probe.getBoundingClientRect().height;
      probe.remove();
      const most = window.innerHeight - headerH - inset;
      const pane = Math.max(0, Math.min(Math.max(288, room), most));
      el.style.setProperty("--pane-h", `${Math.round(pane)}px`);
    };
    measure();
    const ro = new ResizeObserver(measure);
    const masthead = el.parentElement?.querySelector("header");
    if (masthead) ro.observe(masthead);
    window.addEventListener("resize", measure);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, []);

  // The reader has started working the panes: give them the masthead's room.
  // Capture, because a scroll event does not bubble out of the box it scrolls,
  // and these are the same gestures the transcript already treats as "the
  // reader is driving now".
  useEffect(() => {
    const el = splitRef.current;
    if (!el || brief) return;
    const kinds = ["wheel", "touchmove", "keydown"] as const;
    const engage = () => setBrief(true);
    for (const kind of kinds) {
      el.addEventListener(kind, engage, { capture: true, passive: true, once: true });
    }
    return () => {
      for (const kind of kinds) el.removeEventListener(kind, engage, { capture: true });
    };
  }, [brief]);

  /*
   *  Pressing play is the same statement, made from the dock rather than the
   * page: what the reader wants now is the recording and the words under it.
  */
  const [wasPlaying, setWasPlaying] = useState(false);
  if (playhead.playing !== wasPlaying) {
    setWasPlaying(playhead.playing);
    if (playhead.playing) setBrief(true);
  }

  return (
    <article className={s.page}>
      <header className={s.masthead} data-brief={brief}>
        <div className={s.mastheadInner}>
          {/* The two navigations share a row: neither is about this meeting,
              they are both about which meeting you are on. */}
          <div className={s.topRow}>
            <nav className={s.crumbs} aria-label="Breadcrumb">
              <Link href="/">Archive</Link>
              <span aria-hidden>/</span>
              <Link href={`/?body=${encodeURIComponent(meeting.body)}`}>{meeting.body}</Link>
            </nav>
            <div className={s.step}>
              {data.prev ? (
                <Link href={`/meeting/${data.prev.id}`} className={s.stepLink} title={data.prev.date}>
                  ← Previous
                </Link>
              ) : null}
              {data.next ? (
                <Link href={`/meeting/${data.next.id}`} className={s.stepLink} title={data.next.date}>
                  Next →
                </Link>
              ) : null}
            </div>
          </div>

          {/* The date is the identity and the body is the qualifier, so they
              are one line and not two. */}
          <div className={s.titleRow}>
            <h1 className={s.date}>{meetingDate(meeting.date)}</h1>
            <p className={s.body}>{meeting.body}</p>
            <button
              type="button"
              className={s.detailToggle}
              onClick={() => setBrief((b) => !b)}
              aria-expanded={!brief}
              aria-controls="meeting-detail"
            >
              <span aria-hidden className={s.chev} data-open={!brief}>
                ▸
              </span>
              {brief ? "Coverage, roster and documents" : "Hide"}
            </button>
          </div>

          <div id="meeting-detail" className={s.detail} hidden={brief}>
          {/* this meeting's own coverage, not a site-wide disclaimer. */}
          <ul className={s.coverage}>
            <Fact
              on={hasAgenda}
              yes={`${coverage.items} agenda items`}
              no="No published agenda"
              why={
                hasAgenda
                  ? "From the agenda the county published for this meeting"
                  : "The county's agenda for this meeting is missing, or is an image-only scan we cannot read"
              }
            />
            <Fact
              on={coverage.decided > 0}
              yes={`${coverage.decided} with an outcome in the minutes`}
              no="No outcomes in the minutes"
              why="From the approved minutes"
            />
            <Fact
              on={hasRecording}
              yes={`${duration(coverage.seconds)} of recording`}
              no="No recording"
              why={
                hasRecording
                  ? `${coverage.bound} items are located in it`
                  : "This meeting was not recorded, or the recording is not in this archive"
              }
            />
            <Fact
              on={coverage.roster > 0}
              yes={`${coverage.roster} members seated`}
              no="No roster"
              why="From the roster printed on the published agenda"
            />
          </ul>

          {/* Who was seated sits on the same rule as what was covered: they
              are one statement about this meeting, and at any width that fits
              them side by side they cost one row instead of two. */}
          {roster.length > 0 ? (
            <div className={s.roster}>
              <ProvenanceMark kind="agenda" />
              <ul className={s.rosterList}>
                {roster.map((r) => (
                  <li key={r.person_id} className={s.member}>
                    <span className={s.memberName}>{r.full_name ?? r.surname}</span>
                    {r.district ? <span className={s.district}>District {r.district}</span> : null}
                    {/* Through lib/format, not inline. Same words today — the
                        point is that the label vocabulary lives in one place,
                        which is why SpeakerChip already goes there. */}
                    {r.office ? (
                      <span className={s.office}>{officeLabel(r.office)}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className={s.sources}>
            {portal ? (
              <a className={s.portal} href={portal.url} target="_blank" rel="noreferrer">
                See this meeting on the county portal ↗
              </a>
            ) : null}
            {/* The county's own documents. These looked like buttons and did
                nothing in the first pass; a chip that cannot be clicked is a
                worse affordance than no chip. */}
            {files.map((f) => (
              <a
                key={f.file_id}
                className={s.file}
                href={f.url}
                target="_blank"
                rel="noreferrer"
                title={
                  f.extracted
                    ? `Open the county's ${f.kind?.toLowerCase()} PDF`
                    : `Open the county's ${f.kind?.toLowerCase()} PDF. It is an image-only scan, so none of its text is searchable here.`
                }
              >
                {f.kind} PDF
                {f.extracted ? "" : " (scan)"}
              </a>
            ))}
            <Citation
              spec={{
                body: meeting.body,
                date: meeting.date,
                videoId: video?.id ?? null,
                seconds: playhead.videoId === video?.id ? playhead.position : null,
                portalUrl: portal?.url ?? null,
              }}
              label="Cite this meeting"
            />
          </div>
          </div>
        </div>
      </header>

      <div className={s.split} ref={splitRef} data-single={!hasRecording && !hasAgenda}>
        <aside className={s.rail} aria-labelledby="spine-heading">
          {/* The spine's group headings are h3s; without this the document
              jumps h1 → h3 and the outline is wrong for a screen reader. */}
          <h2 id="spine-heading" className="sr-only">
            Agenda
          </h2>
          <AgendaSpine
            items={items}
            videos={videos}
            activeVideo={video?.id ?? null}
            activeItem={activeItem}
            playhead={playhead.videoId === (video?.id ?? null) ? playhead.position : null}
            onSelect={selectItem}
            onSeek={seek}
            onSelectVideo={(id) => {
              setPinnedItem(null);
              setReadingItem(null);
              // Cue the other session at its start rather than starting audio:
              // choosing which recording to read is not the same as pressing
              // play on it.
              seek(id, 0, false);
            }}
          />
        </aside>

        <div className={s.main}>
          <div className={s.tabs} role="tablist" aria-label="What to read">
            <button
              type="button"
              role="tab"
              id="tab-record"
              aria-selected={view === "record"}
              /* Only the visible panel is in the DOM, so only the selected tab
                 may point at one - a dangling IDREF is an ARIA error. */
              aria-controls={view === "record" ? "panel-record" : undefined}
              className={s.tab}
              onClick={() => setView("record")}
            >
              The record
              <span className={s.tabNote}>agenda and minutes</span>
            </button>
            <button
              type="button"
              role="tab"
              id="tab-transcript"
              aria-selected={view === "transcript"}
              aria-controls={view === "transcript" && video ? "panel-transcript" : undefined}
              className={s.tab}
              onClick={() => setView("transcript")}
              disabled={!hasRecording}
              title={hasRecording ? undefined : "There is no recording of this meeting to transcribe"}
            >
              What was said
              <span className={s.tabNote}>{hasRecording ? "transcript" : "no recording"}</span>
            </button>
          </div>

          {/*
            *  BOTH PANELS EXIST; ONE IS HIDDEN.
            *
            * They used to be an either/or, and the record panel is the one
            * that carries a link to every item and every case on the page.
            * With a recording present the transcript is the default, so on
            * those meetings the record panel was never rendered at all - and
            * a crawler reading /meeting/428 found `tab-record` and not one
            * `/item/` href. Twenty-six thousand item pages and twenty
            * thousand case pages were reachable from nothing, here or in the
            * sitemap.
            *
            * `hidden` rather than unmounting, which is also what the tab
            * pattern asks for: a tabpanel that is not current is present and
            * hidden, not absent.
            *
            * The TRANSCRIPT still mounts only when it is showing. Its
            * virtualiser measures the element it scrolls, and measuring a
            * `display: none` box gives it a height of zero to render into.
            */}
          <div
            id="panel-transcript"
            role="tabpanel"
            aria-labelledby="tab-transcript"
            hidden={!(view === "transcript" && video)}
          >
            {view === "transcript" && video ? (
              <TranscriptView
                key={video.id}
                video={video}
                items={items}
                activeItem={activeItem}
                cue={cue && cue.videoId === video.id ? cue : null}
                focus={location.videoId === video.id ? (location.focus ?? null) : null}
                onSeek={(sec) => seek(video.id, sec)}
                onSelectItem={selectItem}
                onReading={setReadingItem}
              />
            ) : null}
          </div>

          <div
            id="panel-record"
            role="tabpanel"
            aria-labelledby="tab-record"
            hidden={view === "transcript" && !!video}
          >
            <RecordView
              meeting={meeting}
              items={items}
              hasAgenda={hasAgenda}
              hasRecording={hasRecording}
              activeItem={activeItem}
              onSeek={seek}
            />
          </div>
        </div>
      </div>
    </article>
  );
}

function Fact({ on, yes, no, why }: { on: boolean; yes: string; no: string; why: string }) {
  return (
    <li className={`${s.fact} ${on ? "" : s.absent}`} title={why}>
      <span aria-hidden className={s.tick}>
        {on ? "●" : "○"}
      </span>
      {on ? yes : no}
    </li>
  );
}
