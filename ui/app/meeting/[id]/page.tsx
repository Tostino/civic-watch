import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { MeetingView } from "@/components/meeting/MeetingView";
import { ApiError, getMeeting } from "@/lib/api";
import { duration, meetingDate, sessionLabel } from "@/lib/format";
import type { MeetingDetail } from "@/lib/types";

/* Next 16: params and searchParams are Promises. */
type Props = {
  params: Promise<{ id: string }>;
  searchParams: Promise<{
    v?: string;
    t?: string;
    end?: string;
    item?: string;
    from?: string;
    to?: string;
  }>;
};

async function load(idParam: string) {
  const id = Number(idParam);
  if (!Number.isInteger(id) || id < 1) notFound();
  try {
    return await getMeeting(id);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }
}

/** a shared link should say what it is before it loads. */
export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  try {
    const d = await load(id);
    const { coverage: c } = d;
    const bits = [
      c.items ? `${c.items} agenda items` : null,
      c.decided ? `${c.decided} with a recorded outcome` : null,
      c.videos ? `${c.videos} recording${c.videos > 1 ? "s" : ""}` : "no recording",
    ].filter(Boolean);
    return {
      title: `${d.meeting.body}, ${meetingDate(d.meeting.date, "short")}`,
      description: bits.join(" · "),
      /* Its own, because the root layout no longer speaks for it. */
      alternates: { canonical: `/meeting/${id}` },
      openGraph: { url: `/meeting/${id}` },
    };
  } catch {
    return { title: "Meeting" };
  }
}

/** Seconds to the form schema.org reads: 7713 becomes "PT2H8M33S". */
function iso8601(seconds: number): string {
  const whole = Math.round(seconds);
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const sec = whole % 60;
  if (!h && !m && !sec) return "PT0S";
  return `PT${h ? `${h}H` : ""}${m ? `${m}M` : ""}${sec ? `${sec}S` : ""}`;
}

/**
 * WHAT THE RECORDINGS ARE, for something that cannot watch them.
 *
 * Search Console reported "No thumbnail URL provided" against this site on
 * 24 August 2026. The finding is exactly right: the player embeds YouTube, so
 * Google sees a video on the page, and the archive emitted no structured data
 * of any kind - `grep -rn "application/ld+json"` over app/, components/ and
 * lib/ returned nothing - so every recording on 1,251 meeting pages was a
 * video Google knew was there and could say nothing about.
 *
 * Every field is read off the row rather than composed, which is the same
 * rule the rest of this archive keeps. `uploadDate` is a bare date because
 * `videos.upload_date` is a bare date; schema.org accepts one, and putting a
 * time on it would be inventing evidence about when the county published a
 * tape. A recording missing that date is LEFT OUT rather than dated from the
 * meeting beside it - the two are near each other and are not the same fact.
 * Measured before choosing that: 0 of 22 sampled recordings lack it, so the
 * strict reading costs approximately nothing.
 *
 * `name` prefers the county's own YouTube title over anything composed here,
 * for the reason `recordingName` gives: it is what the channel actually calls
 * the tape. The thumbnail is YouTube's for the id, which is the only picture
 * of a recording this archive has or should have.
 */
function recordings(d: MeetingDetail) {
  const when = meetingDate(d.meeting.date, "long");
  return d.videos
    .filter((v) => v.upload_date)
    .map((v) => {
      const bits = [
        `Pasco County's own recording of the ${d.meeting.body} meeting`
        + (when ? ` of ${when}` : ""),
      ];
      if (d.videos.length > 1) {
        bits.push(sessionLabel(v.session_seq, d.videos.length).toLowerCase());
      }
      const long = duration(v.duration);
      if (long) bits.push(long);
      const said = v.words
        ? ` Machine transcribed and searchable, ${v.words.toLocaleString("en-US")} words.`
        : "";
      return {
        "@context": "https://schema.org",
        "@type": "VideoObject",
        name: v.title || `${d.meeting.body}${when ? `, ${when}` : ""}`,
        description: `${bits.join(", ")}.${said}`,
        thumbnailUrl: `https://i.ytimg.com/vi/${v.id}/hqdefault.jpg`,
        uploadDate: v.upload_date,
        ...(v.duration ? { duration: iso8601(v.duration) } : {}),
        /* The host the player actually uses, so the two agree about what is
           embedded here. See components/player/PlayerProvider.tsx. */
        embedUrl: `https://www.youtube-nocookie.com/embed/${v.id}`,
        contentUrl: `https://www.youtube.com/watch?v=${v.id}`,
      };
    });
}

export default async function MeetingPage({ params, searchParams }: Props) {
  const [{ id }, q] = await Promise.all([params, searchParams]);
  // the URL carries enough to reproduce the view, including the moment
  // in the recording. Parsed here rather than in the client so a shared link
  // is right on the server-rendered first paint.
  const t = q.t != null ? Number(q.t) : NaN;
  /* Where the recording stops on its own. A citation from the agent or from
     an MCP client is a stretch of a meeting, not a moment in one, and this is
     the half of it the URL never used to carry. Only honoured with a `t` in
     front of it and only when it is actually later: an `end` on its own says
     nothing, and one behind the start would stop the player the instant it
     began. */
  const end = q.end != null ? Number(q.end) : NaN;
  const item = q.item != null ? Number(q.item) : NaN;
  // The utterance range a search hit came from, so following a result lands on
  // the passage rather than near it. Utterance idx, not seconds: it is the
  // durable key a passage is stored against, and `t` moves the player while
  // this marks the words.
  const from = q.from != null ? Number(q.from) : NaN;
  const to = q.to != null ? Number(q.to) : NaN;
  const focus: [number, number] | undefined =
    Number.isInteger(from) && Number.isInteger(to) && to >= from
      ? [from, to]
      : undefined;
  const data = await load(id);
  const videos = recordings(data);
  return (
    <>
      {/* Next's own guidance for this is a plain <script> with the payload
          scrubbed of "<", which is the XSS hole JSON.stringify does not close:
          a YouTube title is somebody else's text. See
          node_modules/next/dist/docs/01-app/02-guides/json-ld.md. */}
      {videos.length ? (
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify(videos).replace(/</g, "\\u003c"),
          }}
        />
      ) : null}
    <MeetingView
      data={data}
      location={{
        videoId: q.v,
        t: Number.isFinite(t) && t >= 0 ? t : undefined,
        end:
          Number.isFinite(t) && t >= 0 && Number.isFinite(end) && end > t
            ? end
            : undefined,
        item: Number.isInteger(item) ? item : undefined,
        focus,
      }}
    />
    </>
  );
}
