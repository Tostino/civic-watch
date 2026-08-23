import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { MeetingView } from "@/components/meeting/MeetingView";
import { ApiError, getMeeting } from "@/lib/api";
import { meetingDate } from "@/lib/format";

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
  return (
    <MeetingView
      data={await load(id)}
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
  );
}
