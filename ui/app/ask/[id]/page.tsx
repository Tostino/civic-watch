import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { Answer, Lookups, type Lookup } from "@/components/ask/Answer";
import { ApiError, getAnswer } from "@/lib/api";
import { meetingDate } from "@/lib/format";
import type { AskResult } from "@/lib/types";
import page from "../ask.module.css";
import s from "./saved.module.css";

/* `/ask/<id>` — an answer somebody was sent.
 *
 * `/ask?q=…` is not a link to an answer. It is an instruction to run the agent
 * again: minutes of waiting at ASK_DEADLINE, a paid run against the daily cap
 * in web/limits.py, and — because the model is sampled and the archive gains
 * meetings — a different answer than the one the sender is talking about. Every
 * completed run is kept (web/answers.py) and this reads the row.
 *
 * So this page is a server component with no stream, no effect and no client
 * state: the answer is a row, and a reader who was sent one should have it
 * rendered on arrival rather than watch it be re-derived.
 *
 * The page is deliberately NOT indexed. It is a machine-written reading of the
 * archive, and the archive's own pages — the meeting, the item, the case — are
 * what a search engine should be sending people to. Nothing here is secret;
 * it simply is not the record, and the record is what this site is for. */

type Props = { params: Promise<{ id: string }> };

async function load(id: string) {
  try {
    return await getAnswer(id);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }
}

/** a shared link should say what it is before it loads — and here what
 *  it is IS the question, so the card leads with it and previews the answer. */
export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  try {
    const a = await load(id);
    return {
      title: `${a.question} · an answer from the archive`,
      description: preview(a.answer),
      robots: { index: false, follow: true },
    };
  } catch {
    return { title: "An answer from the archive", robots: { index: false } };
  }
}

export default async function SavedAnswerPage({ params }: Props) {
  const { id } = await params;
  const a = await load(id);
  const lookups: Lookup[] = (a.trace ?? []).map((t) => ({
    name: t.name,
    args: t.args,
    ok: t.ok,
  }));

  return (
    <div className={page.page}>
      <div className={s.wrap}>
        <header className={s.head}>
          <p className={s.kicker}>A question put to the archive</p>
          <h1 className={s.question}>{a.question}</h1>
          {/* Two different things, and the reader is owed both. The WORDING is
              from that day and is not re-run. The quotes and records under it
              are read out of the archive now — the answer stored which
              passages and items it cited, never the text — so a redaction or a
              correction since then is already here. Saying only "answered on
              the 14th" would imply the whole page is that old; saying only
              "current" would imply the reasoning had been revisited. */}
          <p className={s.when}>
            Answered on{" "}
            <time dateTime={a.asked_at}>{meetingDate(a.asked_at.slice(0, 10))}</time>.
            The answer is not re-run when this page is opened, so the wording is that
            day&rsquo;s. What it cites is read from the archive as it stands now, so a
            correction or a redaction made since appears here.
          </p>
        </header>

        {lookups.length ? (
          <section className={s.trace} aria-label="What the agent did">
            <h2 className={s.traceHead}>
              {lookups.length} lookup{lookups.length === 1 ? "" : "s"}
            </h2>
            <div className={s.traceList}>
              <Lookups lookups={lookups} />
            </div>
          </section>
        ) : null}

        <Answer r={a as AskResult} />

        <footer className={s.after}>
          <Link className={s.again} href={`/ask?q=${encodeURIComponent(a.question)}`}>
            Ask this again
          </Link>
          <span className={s.sep} aria-hidden>
            ·
          </span>
          <Link className={s.again} href="/ask">
            Ask something else
          </Link>
          <p className={s.limits}>
            Asking runs the agent over the archive as it stands now, which takes
            about a minute.
          </p>
        </footer>
      </div>
    </div>
  );
}

/** The answer's opening, without its citation markers — `[item:41203]` in a
 *  link preview is noise to a person and looks like a defect. */
function preview(answer: string): string {
  const plain = answer
    .replace(/\[(item:)?\d{1,7}\]/g, "")
    .replace(/\*\*/g, "")
    .replace(/\s+/g, " ")
    // A citation sits between the last word and the full stop, so taking it
    // out leaves "…about them ." in the card that represents this link.
    .replace(/\s+([.,;:!?])/g, "$1")
    .trim();
  return plain.length > 200 ? `${plain.slice(0, 197).trimEnd()}…` : plain;
}
