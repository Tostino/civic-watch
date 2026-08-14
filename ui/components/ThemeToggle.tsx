"use client";

import { useCallback, useSyncExternalStore } from "react";
import s from "./SiteHeader.module.css";

type Theme = "light" | "dark" | "system";

const EVENT = "themechange";

function subscribe(cb: () => void) {
  window.addEventListener(EVENT, cb);
  return () => window.removeEventListener(EVENT, cb);
}

/* The theme lives on <html>, put there before first paint by the boot script
 * in the layout. This reads it rather than keeping a second copy in React
 * state - two sources of truth for one attribute is how a theme toggle ends up
 * disagreeing with the page it is toggling. */
const read = (): Theme =>
  (document.documentElement.getAttribute("data-theme") as Theme | null) ?? "system";

/** R8.4. System preference is the default; an explicit choice overrides it and
 *  persists across visits. */
export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, read, () => "system" as Theme);

  const apply = useCallback((next: Theme) => {
    if (next === "system") {
      localStorage.removeItem("theme");
      document.documentElement.removeAttribute("data-theme");
    } else {
      localStorage.setItem("theme", next);
      document.documentElement.setAttribute("data-theme", next);
    }
    window.dispatchEvent(new Event(EVENT));
  }, []);

  const next: Theme = theme === "system" ? "light" : theme === "light" ? "dark" : "system";
  const label = { system: "Match the system theme", light: "Light theme", dark: "Dark theme" }[theme];

  return (
    <button
      type="button"
      className={s.theme}
      onClick={() => apply(next)}
      title={`${label}. Click for ${next === "system" ? "system" : next}.`}
      aria-label={`Theme: ${label}. Switch to ${next}.`}
    >
      <span aria-hidden>{theme === "system" ? "◐" : theme === "light" ? "☀" : "☾"}</span>
    </button>
  );
}
