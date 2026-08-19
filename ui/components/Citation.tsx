"use client";

import { useState } from "react";
import { clock, meetingDate } from "@/lib/format";
import s from "./Citation.module.css";

export interface CitationSpec {
  body: string;
  date: string;
  code?: string | null;
  caseId?: string | null;
  videoId?: string | null;
  seconds?: number | null;
  portalUrl?: string | null;
}

/**
 * A canonical, copyable reference.
 *
 * All three serious civic archives reviewed publish one - Hansard's
 * "(Citation: HC Deb, 15 January 2024, c559)", CourtListener's docket
 * citation, Councilmatic's ordinance number - and it is what makes an archive
 * quotable in a filing or a news story rather than merely browsable.
 *
 * Ours must point at the RECORDING, not at this page. The transcript is
 * machine-generated and this site's URLs are ours to break; the county's
 * recording and the county's portal are the primary sources, and a citation
 * that outlives us has to name them.
 */
export function citationText(c: CitationSpec): string {
  const parts = [
    `Pasco County ${c.body}`,
    meetingDate(c.date, "short"),
    c.code ? `item ${c.code}` : null,
    c.caseId ? `case ${c.caseId}` : null,
    c.videoId != null && c.seconds != null ? `recording at ${clock(c.seconds)}` : null,
  ].filter(Boolean);

  const link =
    c.videoId && c.seconds != null
      ? `https://www.youtube.com/watch?v=${c.videoId}&t=${Math.floor(c.seconds)}s`
      : c.portalUrl ?? null;

  return link ? `${parts.join(", ")}. ${link}` : `${parts.join(", ")}.`;
}

export function Citation({ spec, label = "Cite" }: { spec: CitationSpec; label?: string }) {
  const [copied, setCopied] = useState(false);
  const text = citationText(spec);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      return; // clipboard blocked; the title attribute still carries the text
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  }

  return (
    <button type="button" className={s.cite} onClick={copy} title={text}>
      <span aria-hidden className={s.icon}>
        {copied ? "✓" : "❝"}
      </span>
      {copied ? "Reference copied" : label}
    </button>
  );
}
