import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { ALL_NAV_ITEMS, NAV } from "./nav";

/**
 * Every sidebar entry must have a route, and every route must be in the sidebar.
 *
 * In Streamlit a renamed page whose `if page == "..."` body did not follow it rendered a BLANK
 * screen and raised nothing — you only found it by clicking. Next.js at least 404s, but a link
 * to nowhere in your own nav is still a defect you should not ship, so it is pinned here.
 */
const APP = join(process.cwd(), "src", "app");

/** A page that only calls redirect() is a doorway, not a destination — there is nothing on it
 *  to reach, so the sidebar has no reason to link it. Detected from the source rather than
 *  hardcoding a path, so a real page added at the same route is still caught as an orphan. */
function isRedirectOnly(dir: string): boolean {
  const page = readdirSync(dir).find((f) => f === "page.tsx" || f === "page.ts");
  if (!page) return false;
  const src = readFileSync(join(dir, page), "utf8");
  // Plain string tests on purpose. The obvious regex for "renders no JSX" has to contain an
  // angle bracket followed by a slash, and a .ts transform mis-parses that inside a regex
  // literal: the helper returned false under vitest while behaving correctly under plain
  // node. Anything that renders contains an angle bracket; a doorway page has none.
  return src.includes("redirect(") && !src.includes("<");
}

function routesUnder(dir: string, prefix = ""): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (!statSync(full).isDirectory()) continue;
    // route groups (x) and private folders _x do not appear in the URL
    if (entry.startsWith("_")) continue;
    const seg = entry.startsWith("(") ? "" : `/${entry}`;
    const here = `${prefix}${seg}`;
    if (readdirSync(full).some((f) => /^page\.tsx?$/.test(f)) && !isRedirectOnly(full)) {
      out.push(here || "/");
    }
    out.push(...routesUnder(full, here));
  }
  return out;
}

describe("navigation", () => {
  const routes = new Set(routesUnder(APP));

  it("every sidebar link resolves to a page", () => {
    const missing = ALL_NAV_ITEMS.filter((i) => !routes.has(i.href)).map((i) => i.href);
    expect(missing).toEqual([]);
  });

  it("every /admin route is reachable from the sidebar", () => {
    const linked = new Set(ALL_NAV_ITEMS.map((i) => i.href));
    const orphans = [...routes].filter(
      (r) =>
        r.startsWith("/admin") &&
        !linked.has(r) &&
        // dynamic segments are reached by clicking a row, not from the nav
        !r.includes("["),
    );
    expect(orphans).toEqual([]);
  });

  it("has no duplicate hrefs", () => {
    const seen = ALL_NAV_ITEMS.map((i) => i.href);
    expect(new Set(seen).size).toBe(seen.length);
  });

  it("every item carries a hint, because a bare label explains nothing", () => {
    expect(ALL_NAV_ITEMS.filter((i) => !i.hint.trim())).toEqual([]);
  });

  it("groups are non-empty", () => {
    expect(NAV.filter((g) => g.items.length === 0)).toEqual([]);
  });
});
