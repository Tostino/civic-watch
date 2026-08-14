import { AskView } from "@/components/ask/AskView";
import s from "./ask.module.css";

/* `/ask` — §5.5. Enter, at speed.
 *
 * A thin server shell around a client view, because the answer arrives over
 * SSE and takes anywhere up to ASK_DEADLINE — seven minutes on a question that
 * reads widely. The question is in the URL (R4.2) so an answer can be sent to
 * somebody.
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
