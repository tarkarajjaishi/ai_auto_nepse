"use client";

import { create } from "zustand";

export type Theme = "dark" | "light";

/**
 * Theme lives in Zustand rather than `next-themes`.
 *
 * shadcn's own docs reach for next-themes, but Zustand is already the stack's client-state
 * choice and this is ~20 lines — adding a dependency that duplicates one we have is exactly
 * what CLAUDE.md forbids. The one thing next-themes really buys you is no flash of the wrong
 * theme before hydration, and that is solved by the inline script in `layout.tsx`, not by the
 * library.
 *
 * Dark is the default and the fallback: this is a terminal, and a white flash at 6am is a
 * defect, not a preference.
 */
export const STORAGE_KEY = "chukul-theme";

type ThemeState = {
  theme: Theme;
  setTheme: (t: Theme) => void;
  toggle: () => void;
};

function apply(t: Theme) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.classList.toggle("dark", t === "dark");
  root.style.colorScheme = t;
  try {
    localStorage.setItem(STORAGE_KEY, t);
  } catch {
    // private mode / storage disabled — the theme still applies for this session
  }
}

/** What the inline script already put on <html>, so the store starts in agreement with the DOM. */
function initial(): Theme {
  if (typeof document === "undefined") return "dark";
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

export const useTheme = create<ThemeState>((set, get) => ({
  theme: initial(),
  setTheme: (t) => {
    apply(t);
    set({ theme: t });
  },
  toggle: () => get().setTheme(get().theme === "dark" ? "light" : "dark"),
}));

/**
 * Runs before paint, inlined into <head>. Reads the saved choice and sets the class on <html>
 * so the first frame is already correct — without this the page paints light, then snaps to
 * dark once React hydrates.
 */
export const NO_FLASH_SCRIPT = `
(function(){try{
  var t=localStorage.getItem(${JSON.stringify(STORAGE_KEY)});
  if(t!=="light"){t="dark"}
  document.documentElement.classList.toggle("dark",t==="dark");
  document.documentElement.style.colorScheme=t;
}catch(e){
  document.documentElement.classList.add("dark");
  document.documentElement.style.colorScheme="dark";
}})();
`.trim();
