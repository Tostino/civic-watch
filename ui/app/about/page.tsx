import type { Metadata } from "next";
import Link from "next/link";

import { CopyButton } from "@/components/CopyButton";
import { getBodies, getOverview, getTools } from "@/lib/api";
import { installs, mcpUrl } from "@/lib/mcp";
import { meetingDate } from "@/lib/format";
import { siteUrl } from "@/lib/site";
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
 * Everything measurable on this page IS measured, at request time - not only
 * the counts but the sentences that read like prose and are really counts:
 * which bodies were recorded, and which tools the endpoint serves. The lead
 * used to say the archive covered two bodies while it held sixteen, because
 * that sentence was typed once and never checked again. COPY.md: numbers are
 * concrete and load-bearing, and a fact baked into prose in August is wrong
 * by September.
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

/** What each tool is for, in a reader's terms. The manifest carries its own
 *  descriptions and they are written for a model - a paragraph each, about
 *  when to call it. The names come from the server, so a tool added there
 *  appears here whether or not anyone writes it a line. */
const GLOSS: Record<string, string> = {
  search_transcript: "what people said, in the meetings that were recorded",
  search_record: "the agendas and minutes the county published",
  get_item: "one agenda item, with the outcome the minutes recorded for it",
  get_case: "one case, across every meeting that took it up",
  get_meeting: "one meeting, with its agenda in order",
};

/** A window in words. The server sends seconds, and "every 60 seconds" is not
 *  how anyone says a minute. */
function seconds(n: number): string {
  if (n === 60) return "minute";
  if (n % 60 === 0) return `${n / 60} minutes`;
  return `${n.toLocaleString()} seconds`;
}

/** Names in a sentence. Two bodies were recorded today; the third one is not
 *  a rewrite of this page. */
function and(xs: string[]): string {
  return xs.length < 2
    ? (xs[0] ?? "")
    : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;
}

export default async function AboutPage() {
  // Separately caught, so a failure on any one costs only its own section.
  // The page is worth showing without its counts; it is not worth failing.
  const [o, bodies, t] = await Promise.all([
    getOverview().catch(() => null),
    getBodies().catch(() => null),
    getTools().catch(() => null),
  ]);

  const noOutcome =
    o?.items != null && o.decided != null ? o.items - o.decided : null;
  const hours = o ? Math.round(o.seconds / 3600) : null;
  // Which bodies a camera actually reached. All 283 recordings are of two of
  // the sixteen, and that is the single most load-bearing gap here.
  const filmed = bodies?.filter((b) => b.recorded > 0).map((b) => b.body) ?? [];
  const names = t?.tools.map((x) => x.name) ?? Object.keys(GLOSS);

  return (
    <article className={s.wrap}>
      <header className={s.head}>
        <h1>About this archive</h1>
        <p className={s.lead}>
          The public meeting record of Pasco County, in one place you can search, read and cite
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
            {bodies ? (
              <div>
                <dt>Boards and committees</dt>
                <dd>{bodies.length.toLocaleString()}</dd>
              </div>
            ) : null}
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
          <ul className={s.list}>
            <li>
              Of those meetings, <strong>{o.with_agenda.toLocaleString()}</strong> have an agenda
              we can read, <strong>{o.with_minutes.toLocaleString()}</strong> have minutes, and{" "}
              <strong>{o.recorded.toLocaleString()}</strong> have a recording.
            </li>
            {filmed.length && bodies ? (
              <li>
                Only the {and(filmed)} were recorded. The other{" "}
                <strong>{(bodies.length - filmed.length).toLocaleString()}</strong> bodies are in
                the published record only.
              </li>
            ) : null}
            {noOutcome ? (
              <li>
                <strong>{noOutcome.toLocaleString()}</strong> agenda items have no outcome in
                the minutes. We show that, and we do not infer one.
              </li>
            ) : null}
          </ul>
        </section>
      ) : (
        <p>
          Not every meeting has an agenda, minutes and a recording. Each page states which of the
          three it has.
        </p>
      )}

      <section aria-label="The two kinds of information here">
        <h2>Two kinds of information, and they are not equal</h2>
        <p>Every page marks which of the two it is showing.</p>
        <div className={s.registers}>
          <div className={s.record}>
            <h3>The county&apos;s published record</h3>
            <p>
              Agendas and minutes, as the county published them. We reproduce it; we do not
              correct it.
            </p>
          </div>
          <div className={s.derived}>
            <h3>What we derived from the recordings</h3>
            <p>
              The transcript, the speaker names, and where an item sits in a video. Made by
              machine. It can be wrong.
            </p>
          </div>
        </div>
      </section>

      <section aria-label="What can be wrong">
        <h2>What can be wrong</h2>
        <ul className={s.list}>
          <li>
            <strong>The transcript shows what was said, not what was decided.</strong> Where a
            decision matters, read the minutes.
          </li>
          <li>
            <strong>A speaker name is usually our inference, not the county&apos;s.</strong> Every
            name carries how we got it: confirmed by a person, or a weak match. The weak ones are
            the ones most likely to be wrong.
          </li>
          <li>
            <strong>Nothing here replaces the published record itself.</strong> For an authoritative copy
            of an agenda or the minutes, go to the county.
          </li>
        </ul>
      </section>

      {/* Bounded, and the only bounded thing on the page. Everything else here
          describes the archive; this hands the reader an address to paste into
          another program, which is a different kind of offer and was reading as
          six more paragraphs of the same essay. `id` is the anchor /ask points
          at, so the badge on that page lands here and not at the top. */}
      <section id="connect" className={s.connect} aria-label="Connecting your own assistant">
        <h2>Connect your own assistant</h2>
        <p>
          <Link href="/ask">Ask</Link> runs one model and each question costs money, so it is
          limited. An assistant that supports MCP reads this archive itself, through the same
          tools this site uses. There is no sign-in and no key: the address is the whole of it.
        </p>

        {/* The address, and the button that does what a reader was going to do
            with it anyway. The string stays visible beside the button rather
            than hiding behind it: this is the one page that explains the
            endpoint, a reader here may be writing it into a config file by
            hand, and a control that copies something you cannot see is a
            worse offer than a string you can select. */}
        <div className={s.endpointRow}>
          <p className={s.endpoint}>{mcpUrl(siteUrl())}</p>
          <CopyButton className={s.copy} value={mcpUrl(siteUrl())} label="Copy" />
        </div>

        {/* Four ways in, in descending order of how little the reader has to
            do. The first two hand the server to the program over a URL scheme
            and it asks them to confirm; the third is a command because Claude
            Code has no scheme to hand it to; the fourth is a settings pane and
            a paste, which is still where most readers will end up. The note on
            each one says what happens NEXT, because the failure this block
            invites is a reader clicking a button, seeing their editor come to
            the front, and not knowing whether anything was saved. */}
        <p className={s.installsLead}>Add it to</p>
        <ul className={s.installs}>
          {installs(siteUrl()).map((x) => (
            <li key={x.id} className={s.installRow}>
              {x.kind === "link" ? (
                <a className={s.install} href={x.href}>
                  {x.client}
                </a>
              ) : x.kind === "copy" ? (
                <CopyButton
                  className={s.install}
                  value={x.value!}
                  label={x.client}
                  done="Command copied"
                />
              ) : (
                <span className={s.installPlain}>{x.client}</span>
              )}
              <span className={s.installNote}>{x.note}</span>
            </li>
          ))}
        </ul>

        {/* The tool list needs a lead of its own now. Directly under four
            install rows and with none of its own, it read as a fifth one. */}
        <p className={s.installsLead}>What it can read</p>
        <ul className={s.list}>
          {names.map((n) => (
            <li key={n}>
              <code>{n}</code>
              {GLOSS[n] ? `: ${GLOSS[n]}` : null}
            </li>
          ))}
        </ul>
        {t ? (
          <p>
            They only read; nothing here can be changed. One address may make{" "}
            <strong>{t.mcp.per_ip.toLocaleString()}</strong> tool calls every{" "}
            {seconds(t.mcp.window)}, <strong>{t.mcp.heavy_per_ip.toLocaleString()}</strong> of them
            searches of the transcript. Past that it refuses the call and says so.
          </p>
        ) : (
          <p>
            They only read; nothing here can be changed. The endpoint is limited per address, and
            searches of the transcript have the lower limit.
          </p>
        )}
        <p>
          <strong>What your assistant writes is written by your assistant.</strong> This archive
          hands it passages and published items. It does not check what is written from them.
        </p>
      </section>

      <section aria-label="Reporting an error">
        <h2>Tell us when it is wrong</h2>
        <p>
          A wrong name against a person&apos;s words is the error we most want to hear about. We
          correct names by hand. The correction outranks every machine result, and it survives
          every later rebuild of the archive.
        </p>
        {CONTACT ? (
          <p>
            Write to <a href={`mailto:${CONTACT}`}>{CONTACT}</a>. Send the address of the page,
            and the time in the recording if you have it.
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
