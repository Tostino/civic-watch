"use client";

import { useState } from "react";

import s from "./CopyButton.module.css";

/**
 * Puts one string on the clipboard and says so.
 *
 * `value` is never rendered. That is the point on /ask, where the address is
 * not on screen: what a reader wants there is the string in the other
 * program, not the string in front of them.
*/
export function CopyButton({
  value,
  label,
  done = "Copied",
  className,
}: {
  value: string;
  label: string;
  done?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      return; // clipboard blocked; the title attribute still carries the text
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  }

  return (
    <button
      type="button"
      className={className ?? s.copy}
      onClick={copy}
      title={value}
      data-copied={copied || undefined}
    >
      <span aria-hidden className={s.icon}>
        {copied ? "✓" : "⧉"}
      </span>
      {copied ? done : label}
    </button>
  );
}
