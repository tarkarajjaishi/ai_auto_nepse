import type { Cell } from "@/lib/api";

/** Rs, grouped, no decimals — turnover and position values are never read to the paisa. */
export function rupees(v: unknown): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  return `Rs ${v.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

/** A price. Two decimals because NEPSE ticks in paisa and a rounded stop is the wrong stop. */
export function price(v: unknown): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function pct(v: unknown, dp = 2): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(dp)}%`;
}

export function num(v: unknown, dp = 2): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  return v.toLocaleString(undefined, { maximumFractionDigits: dp });
}

export function compact(v: unknown): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  return Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(v);
}

/* ── heatmap colour ─────────────────────────────────────────────────────────────────────── */

/**
 * The tile colour for a percent move, on the ramp every market heatmap uses: pale at a small
 * move, deep and saturated at a large one, grey at flat.
 *
 * Deliberately NOT the terminal's --up/--down. Those are two fixed colours for text, where the
 * number beside them carries the magnitude. A treemap has no number to lean on at a glance — the
 * shade IS the magnitude — so it needs a ramp, and a single green would make +0.1% and +5% look
 * identical.
 *
 * `sat` is where the ramp reaches full depth. 3% for sector indices, which rarely move further;
 * ~5% for individual scrips, which routinely do. Passing NEPSE's ±15% circuit would render an
 * ordinary session as thirteen shades of grey.
 */
const GREEN: [string, string] = ["#4ade80", "#166534"]; // pale → deep
const RED: [string, string] = ["#f87171", "#7f1d1d"];
const FLAT = "#6b7280";

function lerpHex(a: string, b: string, t: number): string {
  const p = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [ar, ag, ab] = p(a);
  const [br, bg, bb] = p(b);
  const m = (x: number, y: number) => Math.round(x + (y - x) * t);
  return `rgb(${m(ar, br)},${m(ag, bg)},${m(ab, bb)})`;
}

export function heatColour(pctChange: number | null | undefined, sat = 3): string {
  if (typeof pctChange !== "number" || !Number.isFinite(pctChange)) return FLAT;
  // A scrip that did not move is grey, not faint green. 0.00% is its own state.
  if (Math.abs(pctChange) < 0.05) return FLAT;
  const t = Math.min(Math.abs(pctChange) / sat, 1);
  const [pale, deep] = pctChange > 0 ? GREEN : RED;
  return lerpHex(pale, deep, t);
}

export type Tone = "up" | "down" | "flat" | "warn" | "none";

/**
 * The colour a verdict gets.
 *
 * "AVOID" is the reason this is a function and not `startsWith("A")`. The Streamlit board tinted
 * any grade beginning with "A" green, so AVOID rendered in the success chip, identical to
 * "A+ ELITE". Match whole words.
 */
export function decisionTone(v: Cell): Tone {
  const s = String(v ?? "").trim().toUpperCase();
  if (s === "AVOID" || s === "SELL" || s === "STRONG SELL" || s === "TREND BREAKDOWN") {
    return "down";
  }
  if (s === "BUY" || s === "STRONG BUY" || s === "BUY ZONE" || s === "HEALTHY PULLBACK") {
    return "up";
  }
  if (s.startsWith("BUY ON")) return "up";
  if (s === "WAIT" || s === "WATCH" || s === "NO TRADE") return "warn";
  if (s === "DANGEROUS") return "down";
  return "none";
}

/** Grades: A+/A green, B amber, C muted, AVOID red. Whole-token match, same trap as above. */
export function gradeTone(v: Cell): Tone {
  const s = String(v ?? "").trim().toUpperCase();
  if (s === "AVOID") return "down";
  if (s.startsWith("A+") || s.startsWith("A ") || s === "A") return "up";
  if (s.startsWith("B")) return "warn";
  if (s.startsWith("C")) return "flat";
  return "none";
}

export const TONE_CLASS: Record<Tone, string> = {
  up: "text-up",
  down: "text-down",
  flat: "text-flat",
  warn: "text-primary",
  none: "",
};

/**
 * A volume multiple. Below 0.1x is NOT "dry" — it means the thing barely traded, which is a
 * liquidity fact rather than a healthy one. That distinction cost a real bug in the retest line.
 */
export function volumeNote(v: unknown): { text: string; tone: Tone } {
  if (typeof v !== "number" || !Number.isFinite(v)) return { text: "—", tone: "none" };
  if (v < 0.1) return { text: `${v.toFixed(2)}x barely traded`, tone: "flat" };
  if (v >= 2) return { text: `${v.toFixed(2)}x very strong`, tone: "up" };
  if (v >= 1.5) return { text: `${v.toFixed(2)}x strong`, tone: "up" };
  if (v >= 1) return { text: `${v.toFixed(2)}x above avg`, tone: "none" };
  return { text: `${v.toFixed(2)}x below avg`, tone: "flat" };
}

/** Columns whose meaning is a price, so the table can right-align and 2dp them without a schema. */
const PRICE_COLS = new Set([
  "close", "entry", "stop", "target1", "target2", "target3", "sl", "tp", "ltp",
  "zone_lo", "zone_hi", "near_res", "major_res", "near_support", "major_support",
]);
const PCT_COLS = new Set([
  "risk_pct", "dist_pct", "change_pct", "ret5", "ret20", "ret60", "chg",
  "price_chg", "regular", "persist_pct", "share_in", "share_out", "block_pct",
]);

const MONEY_COLS = new Set([
  "turnover", "value_rs", "cost_rs", "risk_rs",
  "net_amt", "gross_amt", "top_symbol_amt",
]);

// brokers.txt's ratios are 0-1 FRACTIONS, unlike PCT_COLS which are already 0-100. Left to
// the num() default they were rounded to 2dp of the raw fraction, which printed "0" for a
// broker with a Rs 1.4b book and collapsed 91 distinct market shares onto 6.
const RATIO_COLS = new Set(["conviction", "breadth", "top_symbol_share",
  // probability.txt: first-passage frequencies, 0-1
  "p_up", "p_down", "p_none", "p_up2", "sym_up", "sym_down", "sym_none"]);

// probability.txt again: these are ALREADY in percent of entry, so pct() must not
// multiply them again. Kept separate from RATIO_COLS for exactly that reason.
const PCT_OF_ENTRY = new Set(["net_edge", "expectancy", "open_return", "dist_to_entry",
  "up1_pct", "up2_pct", "down_pct"]);

export function formatCell(col: string, v: Cell): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v !== "number") return String(v);
  if (PRICE_COLS.has(col)) return price(v);
  if (PCT_COLS.has(col)) return pct(v);
  if (RATIO_COLS.has(col)) return pct(v * 100);
  if (PCT_OF_ENTRY.has(col)) return pct(v);
  // Rupee columns, compacted. brokers.txt's three are market-wide 30-session sums and run
  // to ten figures — printed in full they push every other column off a laptop screen.
  if (MONEY_COLS.has(col)) return compact(v);
  return num(v);
}

export function isNumericCol(col: string, rows: Record<string, Cell>[]): boolean {
  return rows.some((r) => typeof r[col] === "number");
}
