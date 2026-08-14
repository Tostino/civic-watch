import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { MeetingView } from "@/components/meeting/MeetingView";
import { ApiError, getMeeting } from "@/lib/api";
import { meetingDate } from "@/lib/format";

/* Next 16: params and searchParams are Promises. */
type Props = {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ v?: string; t?: string; item?: string }>;
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

/** R8.6: a shared link should say what it is before it loads. */
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
    };
  } catch {
    return { title: "Meeting" };
  }
}

export default async function MeetingPage({ params, searchParams }: Props) {
  const [{ id }, q] = await Promise.all([params, searchParams]);
  // R4.2: the URL carries enough to reproduce the view, including the moment
  // in the recording. Parsed here rather than in the client so a shared link
  // is right on the server-rendered first paint.
  const t = q.t != null ? Number(q.t) : NaN;
  const item = q.item != null ? Number(q.item) : NaN;
  return (
    <MeetingView
      data={await load(id)}
      location={{
        videoId: q.v,
        t: Number.isFinite(t) && t >= 0 ? t : undefined,
        item: Number.isInteger(item) ? item : undefined,
      }}
    />
  );
}
