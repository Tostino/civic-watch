import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { CaseView } from "@/components/case/CaseView";
import { ApiError, getCase } from "@/lib/api";
import { meetingDate, outcomeLabel } from "@/lib/format";

/* Next 16: params and searchParams are Promises. */
type Props = { params: Promise<{ id: string }> };

async function load(idParam: string) {
  /* Case ids are free text lifted from a PDF, so the segment is decoded and
   * bounded before it becomes a query. */
  const id = decodeURIComponent(idParam).trim().toUpperCase();
  if (!id || id.length > 64) notFound();
  try {
    return await getCase(id);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }
}

/** For a case the headline fact is the sequence and how it ended. */
export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  try {
    const c = await load(id);
    return {
      title: `${c.case_id} · ${c.steps.length} appearance${c.steps.length === 1 ? "" : "s"}`,
      description: [
        c.terminal ? outcomeLabel(c.terminal.outcome) : "No final outcome recorded",
        `${meetingDate(c.first, "short")} – ${meetingDate(c.last, "short")}`,
        c.continuances ? `${c.continuances} continuances` : null,
      ]
        .filter(Boolean)
        .join(" · "),
    };
  } catch {
    return { title: "Case" };
  }
}

export default async function CasePage({ params }: Props) {
  const { id } = await params;
  return <CaseView data={await load(id)} />;
}
