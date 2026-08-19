import { AskView } from "@/components/ask/AskView";
import { askExamples, askPlaceholder } from "@/components/ask/examples";
import { siteUrl } from "@/lib/site";
import s from "./ask.module.css";

/*
 *  `/ask`. Enter, at speed.
*/

type Props = { searchParams: Promise<{ q?: string | string[] }> };

export async function generateMetadata({ searchParams }: Props) {
  const raw = (await searchParams).q;
  const q = Array.isArray(raw) ? raw[0] : raw;
  return { title: q ? `${q} · ask` : "Ask" };
}

export default async function AskPage({ searchParams }: Props) {
  const raw = (await searchParams).q;
  const q = (Array.isArray(raw) ? raw[0] : raw) ?? "";
  const examples = askExamples();
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
      {/* Drawn here rather than in the view: AskView is a client component,
          and a random pick made during its render disagrees with the pick
          already in the server HTML, which React reports as a hydration
          error. The page is dynamic (it awaits searchParams), so this is a
          fresh four on every arrival. */}
      <AskView
        key={q}
        q={q}
        origin={siteUrl()}
        examples={examples}
        placeholder={askPlaceholder(examples)}
      />
    </div>
  );
}
