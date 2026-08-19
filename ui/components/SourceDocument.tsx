"use client";

import { useState } from "react";

import { ProvenanceMark } from "./ProvenanceMark";
import type { SourceFile } from "@/lib/types";
import s from "./SourceDocument.module.css";

/**
 * the county's own document, inline.
 *
 * Two things it must be honest about:
 *
 * **The document is the whole meeting, not this item.** A BCC agenda is 200
 * items in one PDF, and we do not know which page an item is on. So it is
 * labelled as the meeting's document rather than the item's, and it opens
 * closed - a reader who wants the source asks for it.
*/
export function SourceDocument({ file, meetingLabel }: { file: SourceFile; meetingLabel: string }) {
  const [open, setOpen] = useState(false);
  const kind = (file.kind ?? "Document").toLowerCase();

  return (
    <section className={s.wrap}>
      <header className={s.head}>
        <ProvenanceMark kind={file.kind === "Minutes" ? "minutes" : "agenda"} />
        <div className={s.what}>
          <h3 className={s.title}>
            The county&rsquo;s {kind} for {meetingLabel}
          </h3>
          <p className={s.note}>
            {file.extracted
              ? `The published PDF, as served by the county. This item is one entry in it.`
              : `The published PDF. Its text could not be extracted, because it is an
                 image-only scan, so nothing in it is searchable here. It still
                 reads normally.`}
          </p>
        </div>
        <div className={s.actions}>
          <button
            type="button"
            className={s.toggle}
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-controls={open ? `doc-${file.file_id}` : undefined}
          >
            {open ? "Hide the document" : "Read the document"}
          </button>
          {/* The county's direct link, kept alongside the proxied one: a
              reader must always be able to go to the source themselves rather
              than take our copy of it on trust. */}
          <a className={s.out} href={file.url} target="_blank" rel="noreferrer">
            Download from the county ↗
          </a>
        </div>
      </header>

      {open ? (
        <div className={s.frameWrap} id={`doc-${file.file_id}`}>
          {/* Served through our own origin only to change Content-Disposition
              from `attachment` to `inline`; the bytes are the county's. */}
          <iframe
            className={s.frame}
            src={file.inline}
            title={`${file.kind ?? "Document"} published by Pasco County for ${meetingLabel}`}
          />
          <p className={s.fallback}>
            If the document does not appear, your browser may not render PDFs inline.{" "}
            <a href={file.inline} target="_blank" rel="noreferrer">
              open it in a new tab
            </a>
            .
          </p>
        </div>
      ) : null}
    </section>
  );
}
