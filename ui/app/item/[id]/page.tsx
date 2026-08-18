import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ItemView } from "@/components/item/ItemView";
import { ApiError, getFacts, getItem } from "@/lib/api";
import { meetingDate, outcomeLabel, shortTitle } from "@/lib/format";

/* Next 16: params and searchParams are Promises. */
type Props = { params: Promise<{ id: string }> };

async function load(idParam: string) {
  const id = Number(idParam);
  if (!Number.isInteger(id) || id < 1) notFound();
  try {
    return await getItem(id);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }
}

/** R8.6: a shared link should say what it is before it loads. For an item the
 *  useful summary is the decision, so it leads with the outcome. */
export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  try {
    const { item, meeting } = await load(id);
    const label = shortTitle(item.title, 90) || item.code || "Agenda item";
    return {
      title: `${item.code ? `${item.code} · ` : ""}${label}`,
      description: [
        item.source === "agenda" ? outcomeLabel(item.outcome) : "Not on the published agenda",
        `${meeting.body}, ${meetingDate(meeting.date, "short")}`,
        item.case_id ? `case ${item.case_id}` : null,
      ]
        .filter(Boolean)
        .join(" · "),
    };
  } catch {
    return { title: "Agenda item" };
  }
}

export default async function ItemPage({ params }: Props) {
  const { id } = await params;
  // Separately caught: the item is the page, and a failed count is a clause
  // this page does not make rather than a page it does not render.
  const [data, facts] = await Promise.all([load(id), getFacts().catch(() => null)]);
  return <ItemView data={data} facts={facts} />;
}
