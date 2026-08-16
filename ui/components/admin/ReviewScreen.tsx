"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useMemo, useRef, useState } from "react";

import { SpeakerChip } from "@/components/SpeakerChip";
import { speakerOf } from "@/lib/speaker";
import { usePlayer, usePlayhead } from "@/components/player/PlayerProvider";
import {
  correct,
  getReview,
  ignoreVoice,
  labelVoice,
  undoCorrection,
  type CorrectResult,
  type ReviewVoice,
} from "@/lib/admin";
import { getTranscript } from "@/lib/api";
import { clock, meetingDate } from "@/lib/format";
import type { Line } from "@/lib/types";
import s from "./ReviewScreen.module.css";

/**
 * One recording's contested voices, with everything needed to decide (§5.8):
 * the lines themselves, the audio at the offset (the global player), what
 * else each voice says — here and in other meetings — and WHY the pipeline
 * proposed each name (`basis`, four very different claims). Selection is
 * direct: click a line, shift-click to extend (R5.8.4). The four verbs of
 * R5.8.2 write to speaker_override at utterance grain; whole-voice label and
 * ignore write at voice grain (R9.3).
 */
export function ReviewScreen({
  video,
  name,
  label,
  sel,
}: {
  video: string;
  name?: string;
  label?: string;
  sel?: string;
}) {
  const qc = useQueryClient();
  const player = usePlayer();
  const playhead = usePlayhead();

  const review = useQuery({
    queryKey: ["admin", "review", video, name ?? null, label ?? null],
    queryFn: () => getReview(video, { name, label }),
  });
  const transcript = useQuery({
    queryKey: ["transcript", video],
    queryFn: () => getTranscript(video),
  });

  const lines = useMemo(() => transcript.data?.lines ?? [], [transcript.data]);
  const focusLabels = useMemo(
    () => new Set((review.data?.voices ?? []).map((v) => v.local_label)),
    [review.data],
  );
  /* Each voice under review gets a stable color, shown on its rail card and
   * on every one of its transcript lines. The review task is "which of these
   * interleaved lines is which voice", and that must be answerable at a
   * glance, not by comparing S10·c29 against S09·c1000 by eye. */
  const voiceColors = useMemo(
    () =>
      new Map((review.data?.voices ?? []).map((v, i) => [v.local_label, i % VOICE_HUES])),
    [review.data],
  );
  /** How often each name is heard in this meeting, for the name picker. */
  const nameCounts = useMemo(() => {
    const by = new Map<string, number>();
    for (const l of lines) if (l.name) by.set(l.name, (by.get(l.name) ?? 0) + 1);
    return by;
  }, [lines]);

  /**
   * A key, as it should be READ.
   *
   * The surname is what this screen writes and what every guard joins on, so
   * it stays in every value and every count above. It was also what the screen
   * PRINTED, which is the defect: a reader clicks "Correct this name" on
   * Kathryn Starkey and arrives here to be asked about Starkey. Same rule the
   * diarization id already follows further down — an id is shown AS an id, in
   * mono and labelled, never where a name goes.
   *
   * Both sources are already fetched: the roster the county published for this
   * meeting, then display_name on the lines for anyone else the pipeline has
   * resolved. A name from another meeting's voice match is in neither, and
   * falls back to the key rather than to a guess.
   */
  const printed = useMemo(() => {
    const by = new Map<string, string>();
    for (const r of review.data?.roster ?? []) if (r.full_name) by.set(r.surname, r.full_name);
    for (const l of lines) if (l.name && l.display_name) by.set(l.name, l.display_name);
    return (key: string | null) => (key ? (by.get(key) ?? key) : key);
  }, [review.data, lines]);

  const [onlyFocus, setOnlyFocus] = useState(Boolean(name || label));
  const shown = useMemo(
    () =>
      onlyFocus && focusLabels.size
        ? lines.filter((l) => l.local_label && focusLabels.has(l.local_label))
        : lines,
    [lines, onlyFocus, focusLabels],
  );

  /* What ties the transcript to the audio. The line the recording is inside
   * is marked as sounding, and the list follows it while `following` holds —
   * the same discipline as the reading view (gotcha 39): manual scroll turns
   * following off, any explicit play turns it back on. */
  const [following, setFollowing] = useState(true);
  const activePos = useMemo(() => {
    if (playhead.videoId !== video || !shown.length) return -1;
    let lo = 0;
    let hi = shown.length - 1;
    let best = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (shown[mid].start <= playhead.position) {
        best = mid;
        lo = mid + 1;
      } else hi = mid - 1;
    }
    // A filtered view may hide the sounding line; marking the previous
    // visible one as sounding would claim the audio is somewhere it is not.
    if (best >= 0 && playhead.position > shown[best].end + 1) return -1;
    return best;
  }, [shown, playhead.videoId, playhead.position, video]);
  const soundingIdx = activePos >= 0 ? shown[activePos].idx : -1;

  /* Selection is a contiguous idx range on the FULL transcript — that is the
   * unit the override table stores (R5.8.1) — even when the view is filtered.
   * The action bar states what the range actually contains, so a filtered
   * view cannot hide that a range swept up somebody else's lines. */
  const [anchor, setAnchor] = useState<number | null>(null);
  const [head, setHead] = useState<number | null>(null);
  const range = anchor != null && head != null
    ? ([Math.min(anchor, head), Math.max(anchor, head)] as const)
    : null;

  // ?sel=lo-hi from a dashboard "context" link: preselect that range.
  useEffect(() => {
    if (!sel) return;
    const m = /^(\d+)-(\d+)$/.exec(sel);
    if (m) {
      setAnchor(Number(m[1]));
      setHead(Number(m[2]));
    }
  }, [sel]);

  const [result, setResult] = useState<string | null>(null);
  const done = (msg: string) => {
    setResult(msg);
    setAnchor(null);
    setHead(null);
    void qc.invalidateQueries({ queryKey: ["transcript", video] });
    void qc.invalidateQueries({ queryKey: ["admin"] });
  };

  const write = useMutation({
    mutationFn: correct,
    onSuccess: (r: CorrectResult) => done(outcome(r)),
  });
  const undo = useMutation({
    mutationFn: undoCorrection,
    onSuccess: (r) =>
      done(
        `Correction removed. The range is back to what the pipeline says.` +
          (r.reindexed != null ? ` Search is updated (${r.reindexed} passages).` : "") +
          (r.reindex_error ? ` ${r.reindex_error}` : ""),
      ),
  });

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const virtual = useVirtualizer({
    count: shown.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 58,
    overscan: 10,
    getItemKey: (i) => shown[i].idx,
    /* Gotchas 38/40: batch instead of flushSync, measure outside commit. */
    useFlushSync: false,
    useAnimationFrameWithResizeObserver: true,
  });
  const virtualRef = useRef(virtual);
  virtualRef.current = virtual;

  // Passive drift with the playhead, exactly as the reading view does it.
  useEffect(() => {
    if (!following || activePos < 0 || !playhead.playing) return;
    virtualRef.current.scrollToIndex(activePos, { align: "center", behavior: "smooth" });
  }, [activePos, following, playhead.playing]);

  /* The one way audio starts: play there, and the transcript follows. */
  const listen = (vid: string, at: number) => {
    player.play({ videoId: vid, title: review.data?.video.title ?? "" }, at);
    if (vid === video) setFollowing(true);
  };

  /* Clicking a voice card walks its lines: first click goes to the voice's
   * first line, each further click to the next, wrapping at the end. */
  const walked = useRef(new Map<string, number>());
  const jumpToVoice = (label: string) => {
    const at = walked.current.get(label) ?? -1;
    let pos = shown.findIndex((l, i) => i > at && l.local_label === label);
    if (pos < 0) pos = shown.findIndex((l) => l.local_label === label);
    if (pos < 0) return;
    walked.current.set(label, pos);
    virtualRef.current.scrollToIndex(pos, { align: "center" });
  };

  // Bring a preselected range into view once the lines exist.
  const scrolledTo = useRef<string | null>(null);
  useEffect(() => {
    if (!range || !shown.length || scrolledTo.current === `${range[0]}`) return;
    const pos = shown.findIndex((l) => l.idx >= range[0]);
    if (pos >= 0) {
      scrolledTo.current = `${range[0]}`;
      virtualRef.current.scrollToIndex(pos, { align: "center" });
    }
  }, [range, shown]);

  if (review.isPending || transcript.isPending) {
    return <div className={s.state}>Loading the evidence…</div>;
  }
  if (review.isError || !review.data || transcript.isError) {
    return (
      <div className={s.state} role="alert">
        We could not load this recording.
      </div>
    );
  }
  const v = review.data.video;
  const inRange = range ? lines.filter((l) => l.idx >= range[0] && l.idx <= range[1]) : [];
  // Candidates for the selected range: the measured matches for the voices
  // actually inside it, then the day's roster, then this meeting's names.
  const rangeLabels = new Set(inRange.map((l) => l.local_label));
  const barCandidates = rankedCandidates(
    review.data.voices.filter((vc) => rangeLabels.has(vc.local_label)).flatMap((vc) => vc.affinity),
    review.data.roster,
    nameCounts,
    printed,
  );

  const click = (l: Line, shift: boolean) => {
    if (shift && anchor != null) setHead(l.idx);
    else {
      setAnchor(l.idx);
      setHead(l.idx);
    }
  };

  const extendToVoiceEnd = () => {
    if (!range) return;
    const at = lines.find((l) => l.idx === range[1]);
    if (!at?.local_label) return;
    const rest = lines.filter((l) => l.idx > range[1] && l.local_label === at.local_label);
    if (rest.length) setHead(rest[rest.length - 1].idx);
  };

  return (
    <div className={s.wrap}>
      <header className={s.head}>
        <div>
          <h1>
            {v.body ?? (v.kind === "bcc" ? "Board of County Commissioners" : "Planning Commission")}
            {v.date ?? v.upload_date ? `, ${meetingDate((v.date ?? v.upload_date)!, "long")}` : null}
          </h1>
          <p className={s.sub}>
            {name ? (
              <>
                Reviewing the voices attributed to <strong>{printed(name)}</strong> ·{" "}
              </>
            ) : label ? (
              <>
                Reviewing one unnamed voice ·{" "}
              </>
            ) : null}
            <span className={s.mono}>{video}</span>
            {v.meeting_id ? (
              <>
                {" · "}
                <Link href={`/meeting/${v.meeting_id}`}>reading view</Link>
              </>
            ) : null}
            {" · "}
            <Link href="/admin">← queues</Link>
          </p>
        </div>
        <label className={s.filter}>
          <input
            type="checkbox"
            checked={onlyFocus}
            onChange={(e) => setOnlyFocus(e.target.checked)}
            disabled={!focusLabels.size}
          />
          only the voices under review
        </label>
      </header>

      {result ? (
        <p className={s.result} role="status">
          {result}
          <button type="button" onClick={() => setResult(null)} aria-label="Dismiss">
            ×
          </button>
        </p>
      ) : null}

      <div className={s.cols}>
        <aside className={s.rail} aria-label="Voices under review">
          {review.data.voices.map((voice) => (
            <VoiceCard
              key={voice.local_label}
              video={video}
              voice={voice}
              candidates={rankedCandidates(voice.affinity, review.data.roster, nameCounts, printed)}
              color={voiceColors.get(voice.local_label)}
              printed={printed}
              onJump={() => jumpToVoice(voice.local_label)}
              onPlay={listen}
              onDone={done}
            />
          ))}

          {review.data.overrides.length ? (
            <section className={s.overrides}>
              <h3>Corrections on this recording</h3>
              <ul>
                {review.data.overrides.map((o) => (
                  <li key={o.id}>
                    <span className={s.mono}>
                      {o.start_idx}–{o.end_idx}
                    </span>{" "}
                    {o.action} {o.name ?? "(no name)"} · {o.status}
                    <button
                      type="button"
                      className={s.small}
                      onClick={() => undo.mutate(o.id)}
                      disabled={undo.isPending}
                    >
                      undo
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </aside>

        <section className={s.reader} aria-label="Transcript">
          <div className={s.hintRow}>
            <p className={s.hint}>
              Click a line to start a selection, shift-click to extend it. The time plays the
              recording at that moment — the recording is the evidence, the transcript is the
              claim.
            </p>
            {playhead.videoId === video && playhead.playing ? (
              following ? (
                <span className={s.nowPlaying} role="status">
                  ▶ {clock(playhead.position)} — following
                </span>
              ) : (
                <button
                  type="button"
                  className={s.follow}
                  onClick={() => {
                    setFollowing(true);
                    if (activePos >= 0) {
                      virtualRef.current.scrollToIndex(activePos, { align: "center" });
                    }
                  }}
                >
                  Follow the recording
                </button>
              )
            ) : null}
          </div>
          <div
            className={s.scroll}
            ref={scrollRef}
            tabIndex={0}
            onWheel={() => setFollowing(false)}
            onTouchMove={() => setFollowing(false)}
          >
            <div style={{ height: virtual.getTotalSize(), position: "relative" }}>
              {virtual.getVirtualItems().map((it) => {
                const l = shown[it.index];
                const selected = range && l.idx >= range[0] && l.idx <= range[1];
                const sounding = l.idx === soundingIdx;
                const vc = l.local_label != null ? voiceColors.get(l.local_label) : undefined;
                return (
                  <div
                    key={it.key}
                    data-index={it.index}
                    ref={virtual.measureElement}
                    className={s.rowBox}
                    style={{ transform: `translateY(${it.start}px)` }}
                  >
                    <div
                      role="button"
                      tabIndex={0}
                      aria-pressed={Boolean(selected)}
                      data-vc={vc}
                      className={`${s.row} ${selected ? s.rowSel : ""} ${sounding ? s.rowSounding : ""}`}
                      onClick={(e) => click(l, e.shiftKey)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          click(l, e.shiftKey);
                        }
                      }}
                    >
                      <button
                        type="button"
                        className={`${s.at} ${sounding ? s.atSounding : ""}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          listen(video, l.start);
                        }}
                        title={`Play from ${clock(l.start)}`}
                      >
                        {sounding ? "▶ " : ""}
                        {clock(l.start)}
                      </button>
                      <span className={s.meta}>
                        <SpeakerChip
                          {...speakerOf(l)}
                          size="sm"
                        />
                        {/* A curation surface shows the diarization id AS an
                            id — mono, labelled, never where a name goes. */}
                        <span className={s.voiceId} title="Diarization voice / cluster — internal ids, not names">
                          {short(l.local_label)}
                          {l.voice != null ? `·c${l.voice}` : ""}
                        </span>
                        <span className={s.idx}>#{l.idx}</span>
                      </span>
                      <span className={s.text}>{l.text}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </section>
      </div>

      {range ? (
        <CorrectionBar
          video={video}
          range={range}
          inRange={inRange}
          candidates={barCandidates}
          busy={write.isPending}
          error={write.isError ? write.error.message : null}
          onExtend={extendToVoiceEnd}
          onClear={() => {
            setAnchor(null);
            setHead(null);
          }}
          onSubmit={(action, nm, note) =>
            write.mutate({
              video_id: video,
              start_idx: range[0],
              end_idx: range[1],
              action,
              name: nm,
              note,
            })
          }
        />
      ) : null}
    </div>
  );
}

function outcome(r: CorrectResult) {
  let base = `${r.utterances} utterance${r.utterances === 1 ? "" : "s"} corrected. A correction outranks every inferred name and survives every rebuild.`;
  if (r.normalized) base = `${base} ${r.normalized}`;
  if (r.reindex_error) return `${base} ${r.reindex_error}`;
  return `${base} Search is updated (${r.reindexed ?? 0} passages).`;
}

function short(label: string | null) {
  return label ? label.replace("SPEAKER_", "S") : "—";
}

/** Distinct hues available for voices under review — see the --vc* pairs in
 *  the module CSS. Past six, hues repeat; six interleaved voices in one
 *  review has not been observed (the queue's worst row holds two). */
const VOICE_HUES = 6;

interface Candidate {
  /** The KEY. What gets written, and what the option's value carries. */
  name: string;
  /** The same person, as the operator reads them everywhere else. */
  label: string;
  /** Why this name is offered — the evidence, stated beside the choice. */
  hint: string;
}

/**
 * The names worth offering, best evidence first. The right answer is usually
 * already on screen — the affinity measurement, the roster — and making the
 * operator re-type it was bad UX, reported as such. Order: measured voice
 * matches, then the people the agenda seats that day, then anyone already
 * named in this meeting. Anything below 0.35 similarity is withheld — on this
 * corpus that is measured to be a different person, not a weak match.
 */
function rankedCandidates(
  affinity: { name: string; similarity: number }[],
  roster: { surname: string; office: string | null }[],
  nameCounts: Map<string, number>,
  printed: (key: string | null) => string | null,
): Candidate[] {
  const out = new Map<string, string>();
  for (const a of [...affinity].sort((x, y) => y.similarity - x.similarity)) {
    if (a.similarity < 0.35 || out.has(a.name)) continue;
    out.set(a.name, `voice match ${a.similarity.toFixed(2)}`);
  }
  for (const r of roster) {
    if (out.has(r.surname)) continue;
    out.set(r.surname, r.office ? `${officeWord(r.office)} that day` : "on the roster that day");
  }
  for (const [nm] of [...nameCounts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12)) {
    if (!out.has(nm)) out.set(nm, "named in this meeting");
  }
  return [...out.entries()].map(([name, hint]) => ({
    name,
    label: printed(name) ?? name,
    hint,
  }));
}

const officeWord = (o: string) =>
  o === "chair"
    ? "chair"
    : o === "vice_chair"
      ? "vice chair"
      : o === "second_vice_chair"
        ? "second vice chair"
        : "member";

/**
 * A dropdown of the ranked candidates, with free text as the escape hatch.
 * Picking a listed name is one click; "someone else" opens the input.
 */
function NamePicker({
  candidates,
  value,
  onChange,
  disabled = false,
  ariaLabel,
}: {
  candidates: Candidate[];
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  ariaLabel: string;
}) {
  const [typing, setTyping] = useState(false);
  const known = candidates.some((c) => c.name === value);
  const other = typing || (value !== "" && !known);
  return (
    <>
      <select
        value={other ? "__other__" : value}
        onChange={(e) => {
          const v = e.target.value;
          if (v === "__other__") {
            setTyping(true);
            onChange("");
          } else {
            setTyping(false);
            onChange(v);
          }
        }}
        disabled={disabled}
        aria-label={ariaLabel}
      >
        <option value="">who is it?</option>
        {/* The label reads; the VALUE is the key that gets written. */}
        {candidates.map((c) => (
          <option key={c.name} value={c.name}>
            {c.label} — {c.hint}
          </option>
        ))}
        <option value="__other__">someone else — type the name</option>
      </select>
      {other && !disabled ? (
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="type the name"
          aria-label={`${ariaLabel} — typed`}
          autoFocus
        />
      ) : null}
    </>
  );
}

/* ------------------------------------------------------------- voice card */
function VoiceCard({
  video,
  voice,
  candidates,
  color,
  printed,
  onJump,
  onPlay,
  onDone,
}: {
  video: string;
  voice: ReviewVoice;
  candidates: Candidate[];
  color: number | undefined;
  /** A key, as it should be read. See the note where it is built. */
  printed: (key: string | null) => string | null;
  onJump: () => void;
  onPlay: (video: string, at: number) => void;
  onDone: (msg: string) => void;
}) {
  const [labelName, setLabelName] = useState("");
  const doLabel = useMutation({
    mutationFn: labelVoice,
    onSuccess: (r) =>
      onDone(
        r.name
          ? `Voice labeled ${r.name}. A person said so, and that outranks every inferred name.` +
              (r.normalized ? ` ${r.normalized}` : "")
          : "Label cleared.",
      ),
  });
  const doIgnore = useMutation({
    mutationFn: ignoreVoice,
    onSuccess: (r) =>
      onDone(r.ignored ? "Voice marked as not a person (noise/crosstalk)." : "Voice restored."),
  });

  return (
    <article className={s.card} data-vc={color}>
      <h3>
        <span className={s.swatch} data-vc={color} aria-hidden />
        <span className={s.mono}>{short(voice.local_label)}</span>
        {voice.cluster != null ? <span className={s.dim}> · cluster {voice.cluster}</span> : null}
        <span className={s.dim}> · {voice.utts} lines</span>
        <button
          type="button"
          className={s.small}
          onClick={onJump}
          title="Go to this voice's next line in the transcript"
        >
          its lines ↓
        </button>
      </h3>
      <p className={s.claim}>
        {voice.labeled ? (
          <>
            Human label: <strong>{printed(voice.label_name)}</strong>
            {voice.label_note ? ` — ${voice.label_note}` : null}
          </>
        ) : voice.name ? (
          <>
            Pipeline says <strong>{printed(voice.name)}</strong>
            <span className={s.dim}>
              {" "}
              ({voice.source ?? "matched"}
              {voice.confidence != null ? `, ${voice.confidence.toFixed(2)}` : ""}) — an inference
            </span>
          </>
        ) : (
          <>Unidentified</>
        )}
        {voice.ignored ? <span className={s.dim}> · ignored</span> : null}
      </p>

      {voice.affinity.length ? (
        <ul className={s.affinity}>
          {voice.affinity.map((a) => (
            <li key={a.name} data-verdict={a.similarity >= 0.7 ? "ok" : a.similarity < 0.35 ? "no" : "unsure"}>
              sounds like {printed(a.name)}? <strong>{a.similarity.toFixed(3)}</strong>
              <span className={s.dim}>
                {a.similarity >= 0.7
                  ? " — consistent"
                  : a.similarity < 0.35
                    ? " — measured: a different person"
                    : " — ambiguous zone (rare)"}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className={s.dim}>No affinity measurement for this voice.</p>
      )}

      {voice.samples.length ? (
        <div className={s.samples}>
          <h4>Longest lines here</h4>
          {voice.samples.map((sm) => (
            <button
              key={sm.idx}
              type="button"
              className={s.sampleBtn}
              onClick={() => onPlay(video, sm.start)}
              title={`Play from ${clock(sm.start)}`}
            >
              ▶ {clock(sm.start)} <span className={s.quote}>“{sm.text}…”</span>
            </button>
          ))}
        </div>
      ) : null}

      {voice.elsewhere.length ? (
        <div className={s.samples}>
          <h4>Same cluster, other meetings</h4>
          {voice.elsewhere.map((e) => (
            <button
              key={`${e.video_id}${e.start}`}
              type="button"
              className={s.sampleBtn}
              onClick={() => onPlay(e.video_id, e.start)}
              title={e.title}
            >
              ▶ {e.upload_date ? meetingDate(e.upload_date, "short") : e.video_id}
              {e.name ? ` · as ${printed(e.name)}` : " · unnamed"}{" "}
              <span className={s.quote}>“{e.text}…”</span>
            </button>
          ))}
        </div>
      ) : null}

      <form
        className={s.voiceActs}
        onSubmit={(e) => {
          e.preventDefault();
          if (!labelName.trim()) return;
          doLabel.mutate({
            members: [[video, voice.local_label]],
            name: labelName.trim(),
          });
        }}
      >
        <NamePicker
          candidates={candidates}
          value={labelName}
          onChange={setLabelName}
          ariaLabel={`Name for voice ${voice.local_label}`}
        />
        <button type="submit" disabled={doLabel.isPending || !labelName.trim()}>
          Label voice
        </button>
        <button
          type="button"
          className={s.quiet}
          onClick={() =>
            doIgnore.mutate({
              members: [[video, voice.local_label]],
              reason: "not a person (admin console)",
              undo: voice.ignored,
            })
          }
          disabled={doIgnore.isPending}
          title="Music, noise, crosstalk — removes it from the triage queues; changes no name"
        >
          {voice.ignored ? "Un-ignore" : "Not a person"}
        </button>
      </form>
    </article>
  );
}

/* --------------------------------------------------------- correction bar */
const VERBS = [
  ["reassign", "a different named person"],
  ["identify", "was unidentified; I know who"],
  ["split", "this voice is two people from here"],
  ["detach", "not who it says — unknown"],
] as const;

function CorrectionBar({
  video: _video,
  range,
  inRange,
  candidates,
  busy,
  error,
  onExtend,
  onClear,
  onSubmit,
}: {
  video: string;
  range: readonly [number, number];
  inRange: Line[];
  candidates: Candidate[];
  busy: boolean;
  error: string | null;
  onExtend: () => void;
  onClear: () => void;
  onSubmit: (action: "reassign" | "detach" | "identify" | "split", name: string | null, note: string | null) => void;
}) {
  const [action, setAction] = useState<(typeof VERBS)[number][0]>("reassign");
  const [nm, setNm] = useState("");
  const [note, setNote] = useState("");

  /* What the range actually contains — the guard against a filtered view
   * sweeping up lines the operator never looked at. */
  const contents = useMemo(() => {
    const by = new Map<string, number>();
    for (const l of inRange) {
      const k = l.name ?? "unidentified";
      by.set(k, (by.get(k) ?? 0) + 1);
    }
    return [...by.entries()].sort((a, b) => b[1] - a[1]);
  }, [inRange]);

  const needsName = action !== "detach";

  return (
    <div className={s.barWrap}>
      <form
        className={s.bar}
        onSubmit={(e) => {
          e.preventDefault();
          if (needsName && !nm.trim()) return;
          onSubmit(action, needsName ? nm.trim() : null, note.trim() || null);
        }}
      >
        <div className={s.barInfo}>
          <strong>
            Lines {range[0]}–{range[1]}
          </strong>{" "}
          · {inRange.length} utterance{inRange.length === 1 ? "" : "s"}
          {inRange.length ? ` · ${clock(inRange[0].start)}–${clock(inRange[inRange.length - 1].start)}` : null}
          <span className={s.dim}>
            {" — "}
            {contents.map(([who, n]) => `${n} ${who}`).join(", ")}
          </span>
          <button type="button" className={s.small} onClick={onExtend}>
            extend to this voice&rsquo;s last line
          </button>
          <button type="button" className={s.small} onClick={onClear}>
            clear
          </button>
        </div>
        <div className={s.barActs}>
          <fieldset className={s.verbs}>
            <legend className="sr-only">Correction</legend>
            {VERBS.map(([k, why]) => (
              <label key={k} title={why}>
                <input
                  type="radio"
                  name="verb"
                  checked={action === k}
                  onChange={() => setAction(k)}
                />
                {k}
              </label>
            ))}
          </fieldset>
          <NamePicker
            candidates={candidates}
            value={nm}
            onChange={setNm}
            disabled={!needsName}
            ariaLabel="Who it actually is"
          />
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="why (for whoever reads this later)"
            aria-label="Note"
          />
          <button type="submit" disabled={busy || (needsName && !nm.trim())}>
            {busy ? "Writing…" : "Apply correction"}
          </button>
        </div>
        {error ? (
          <p className={s.err} role="alert">
            {error}
          </p>
        ) : null}
      </form>
    </div>
  );
}
