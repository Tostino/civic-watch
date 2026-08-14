import type { Metadata } from "next";
import Link from "next/link";

import { getOverview } from "@/lib/api";
import { meetingDate } from "@/lib/format";
import s from "./about.module.css";

/**
 * What this archive is, where each part of it comes from, and what can be
 * wrong with it.
 *
 * The page exists because of one asymmetry: this site puts a named county
 * commissioner's name against words spoken in a public meeting, and some of
 * those names are our inference rather than the county's record. A reader has
 * to be able to find out which, and to tell us when we have it wrong.
 *
 * Every count on it is measured at request time. COPY.md: numbers are
 * concrete and load-bearing, never "many" where a count is available - and a
 * count baked into prose in August is wrong by September.
 */
export const revalidate = 3600;

export const metadata: Metadata = {
  title: "About",
  description:
    "What this archive holds, where each part of it comes from, what is missing from it, " +
    "and how to tell us when a name or a transcript is wrong.",
  alternates: { canonical: "/about" },
};

/** Where a correction goes. Unset until the maintainer chooses an address;
 *  the section then states the promise without offering a dead link. */
const CONTACT = process.env.SITE_CONTACT?.trim() || null;

export default async function AboutPage() {
  let o = null;
  try {
    o = await getOverview();
  } catch {
    // The page is worth showing without its counts. It is not worth failing.
  }
  const noRecording = o ? o.meetings - o.recorded : null;
  const noDisposition =
    o?.items != null && o.decided != null ? o.items - o.decided : null;
  const hours = o ? Math.round(o.seconds / 3600) : null;

  return (
    <article className={s.wrap}>
      <header className={s.head}>
        <h1>About this archive</h1>
        <p className={s.lead}>
          This is the public meeting record of Pasco County, in one place you can search, read
          and cite. It covers the Board of County Commissioners and the Planning Commission
          {o ? (
            <>
              , from {meetingDate(o.first, "short")} to {meetingDate(o.last, "short")}
            </>
          ) : null}
          .
        </p>
      </header>

      {o ? (
        <section aria-label="What the archive holds">
          <h2>What it holds</h2>
          <dl className={s.figures}>
            <div>
              <dt>Meetings</dt>
              <dd>{o.meetings.toLocaleString()}</dd>
            </div>
            <div>
              <dt>With a recording</dt>
              <dd>{o.recorded.toLocaleString()}</dd>
            </div>
            {hours ? (
              <div>
                <dt>Hours of recording</dt>
                <dd>{hours.toLocaleString()}</dd>
              </div>
            ) : null}
            {o.items != null ? (
              <div>
                <dt>Agenda items</dt>
                <dd>{o.items.toLocaleString()}</dd>
              </div>
            ) : null}
          </dl>
        </section>
      ) : null}

      <section aria-label="The two kinds of information here">
        <h2>Two kinds of information, and they are not equal</h2>
        <p>
          Everything on this site is one of two things, and the page always shows you which.
        </p>
        <div className={s.registers}>
          <div className={s.record}>
            <h3>The county&apos;s published record</h3>
            <p>
              Agendas and minutes, as the county published them. This is the official record. We
              reproduce it; we do not correct it.
            </p>
          </div>
          <div className={s.derived}>
            <h3>What we derived from the recordings</h3>
            <p>
              The transcript, the speaker names, and where an item sits in a video. We produced
              this from the recordings by machine. It can be wrong.
            </p>
          </div>
        </div>
      </section>

      <section aria-label="What is missing">
        <h2>What is missing</h2>
        {o ? (
          <ul className={s.gaps}>
            <li>
              <strong>{noRecording?.toLocaleString()}</strong> of {o.meetings.toLocaleString()}{" "}
              meetings have no recording. They are in the published record only, so this archive
              can say what was on the agenda but not what was said.
            </li>
            <li>
              <strong>{o.with_agenda.toLocaleString()}</strong> meetings have a published agenda
              and <strong>{o.with_minutes.toLocaleString()}</strong> have published minutes. The
              county did not publish both for every meeting.
            </li>
            {noDisposition ? (
              <li>
                <strong>{noDisposition.toLocaleString()}</strong> of{" "}
                {o.items?.toLocaleString()} agenda items have no disposition in the minutes. We
                show that as no disposition recorded, and we do not infer one.
              </li>
            ) : null}
          </ul>
        ) : (
          <p>
            Not every meeting has a recording, an agenda and minutes. Each page states which of
            the three it has.
          </p>
        )}
      </section>

      <section aria-label="What can be wrong">
        <h2>What can be wrong</h2>
        <p>
          <strong>The transcript is machine transcription of the recording.</strong> It shows
          what was said, not what was decided, and it can be wrong. Where a decision matters,
          read the minutes.
        </p>
        <p>
          <strong>A speaker name is usually our inference, not the county&apos;s.</strong> We
          match a voice to a name. Where a person has confirmed a name, the page says so. Where
          the match is weak, the page says that too — and those are the ones most likely to be
          wrong.
        </p>
        <p>
          <strong>Nothing here replaces the official record.</strong> For an authoritative copy
          of an agenda or the minutes, go to the county.
        </p>
      </section>

      <section aria-label="Reporting an error">
        <h2>Tell us when it is wrong</h2>
        <p>
          A wrong name against a person&apos;s words is the error we most want to hear about. We
          correct names by hand, the correction outranks every machine result, and it survives
          every later rebuild of the archive.
        </p>
        {CONTACT ? (
          <p>
            Write to <a href={`mailto:${CONTACT}`}>{CONTACT}</a>. Send the address of the page
            and the time in the recording, if you have it.
          </p>
        ) : null}
      </section>

      <footer className={s.foot}>
        <Link href="/">Browse the meetings</Link>
        <Link href="/search">Search the record</Link>
        <Link href="/ask">Ask a question</Link>
      </footer>
    </article>
  );
}
