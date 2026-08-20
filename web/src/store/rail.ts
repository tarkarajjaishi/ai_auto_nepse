"use client";

import { create } from "zustand";

/**
 * Which sector index the instrument rail has open, held outside the component.
 *
 * It cannot be `useState`. The rail reads `useSearchParams`, so it sits inside a Suspense
 * boundary; navigating to a symbol re-suspends that boundary and remounts the rail, which threw
 * the open sector away. The effect was that drilling into Hydro Power and clicking a scrip
 * bounced you back to the index list — exactly when you were most likely to want the next scrip
 * in the same sector.
 *
 * Zustand rather than lifting it into the URL: the open sector is a view preference, not part of
 * what the page is showing, and putting it in the query string would make Back walk through
 * every rail expansion.
 */
type RailState = {
  /** index ticker whose sector is open, or null at the top level */
  drill: string | null;
  setDrill: (t: string | null) => void;
};

export const useRail = create<RailState>((set) => ({
  drill: null,
  setDrill: (drill) => set({ drill }),
}));
