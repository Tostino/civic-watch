import Link from "next/link";

import s from "./Examples.module.css";

/**
 * What to click before you have typed anything, on both pages that ask for
 * typing.
 *
 * ONE COMPONENT AND NOT TWO SETS OF RULES. /search and /ask each grew their
 * own; the drift was not a decision anybody made, it was two files. What
 * differs between them is one flag: a search example is a string to type and
 * is set in mono, a question is a sentence and is set in the record face.
 */
export type Example = {
  /** What kind of thing it is: "a subject", "an item code", "what was said". */
  tag: string;
  /** The query or the question, and the text of the link. */
  text: string;
  href: string;
};

export function Examples({
  label,
  items,
  mono = false,
}: {
  label: string;
  items: Example[];
  /** Set for literal strings a reader could have typed. */
  mono?: boolean;
}) {
  return (
    <div className={s.wrap}>
      <h2 className={s.head}>{label}</h2>
      <ul className={s.grid}>
        {items.map((x) => (
          <li key={x.text}>
            <Link href={x.href} className={s.card}>
              <span className={s.tag}>{x.tag}</span>
              <span className={`${s.text} ${mono ? s.mono : ""}`}>{x.text}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
