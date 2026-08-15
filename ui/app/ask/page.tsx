import { AskView } from "@/components/ask/AskView";
import s from "./ask.module.css";

/* `/ask` — §5.5. Enter, at speed.
 *
 * A thin server shell around a client view, because the answer arrives over
 * SSE and takes anywhere up to ASK_DEADLINE — seven minutes on a question that
 * reads widely. The question is in the URL (R4.2) so a reload mid-run asks the
 * same thing rather than losing it — but this is not the URL anyone sends.
 * `?q=` is an instruction to spend money and, at that deadline, up to seven
 * minutes of somebody else's patience. When the answer lands the view replaces
 * it with `/ask/<id>`, which is the answer itself, so the address bar always
 * holds the right thing to copy and there is no share control to find.
 *
 * UI_PLAN §7: no conversational chat here, deliberately. Memory and persona
 * would make this a different product and would undermine every citation —
 * the value of this page is that each claim traces to a document or a moment
 * in a recording, and a chat history is neither. */

type Props = { searchParams: Promise<{ q?: string | string[] }> };

export async function generateMetadata({ searchParams }: Props) {
  const raw = (await searchParams).q;
  const q = Array.isArray(raw) ? raw[0] : raw;
  return { title: q ? `${q} — ask` : "Ask" };
}

export default async function AskPage({ searchParams }: Props) {
  const raw = (await searchParams).q;
  const q = (Array.isArray(raw) ? raw[0] : raw) ?? "";
  return (
    <div className={s.page}>
      {/* Keyed on the question: arriving at a different one remounts with
          fresh state instead of reconciling, which is what lets the effect
          that opens the stream set no state of its own. */}
      <AskView key={q} q={q} />
    </div>
  );
}
