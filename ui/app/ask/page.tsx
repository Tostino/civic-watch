import { AskView } from "@/components/ask/AskView";
import { siteUrl } from "@/lib/site";
import s from "./ask.module.css";

/* `/ask`. Enter, at speed.
 *
 * A thin server shell around a client view, because the answer arrives over
 * SSE and takes anywhere up to ASK_DEADLINE — seven minutes on a question that
 * reads widely. The question is in the URL so a reload mid-run asks the
 * same thing rather than losing it — but this is not the URL anyone sends.
 * `?q=` is an instruction to spend money and, at that deadline, up to seven
 * minutes of somebody else's patience. When the answer lands the view replaces
 * it with `/ask/<id>`, which is the answer itself, so the address bar always
 * holds the right thing to copy and there is no share control to find.
 *
 * the design notes: no conversational chat here, deliberately. Memory and persona
 * would make this a different product and would undermine every citation —
 * the value of this page is that each claim traces to a document or a moment
 * in a recording, and a chat history is neither. */

type Props = { searchParams: Promise<{ q?: string | string[] }> };

export async function generateMetadata({ searchParams }: Props) {
  const raw = (await searchParams).q;
  const q = Array.isArray(raw) ? raw[0] : raw;
  return { title: q ? `${q} · ask` : "Ask" };
}

export default async function AskPage({ searchParams }: Props) {
  const raw = (await searchParams).q;
  const q = (Array.isArray(raw) ? raw[0] : raw) ?? "";
  return (
    <div className={s.page}>
      {/* Keyed on the question: arriving at a different one remounts with
          fresh state instead of reconciling, which is what lets the effect
          that opens the stream set no state of its own. */}
      {/* The origin is resolved here and not in the view. `siteUrl()` reads a
          server-only variable, and AskView is a client component: reaching for
          it there would put the deployed host in the server HTML and localhost
          in the browser, which React reports as a hydration error and a reader
          would experience as a copy button that hands over the wrong address. */}
      <AskView key={q} q={q} origin={siteUrl()} />
    </div>
  );
}
