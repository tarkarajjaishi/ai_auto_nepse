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

export function formatCell(col: string, v: Cell): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v !== "number") return String(v);
  if (PRICE_COLS.has(col)) return price(v);
  if (PCT_COLS.has(col)) return pct(v);
  if (col === "turnover" || col === "value_rs" || col === "cost_rs" || col === "risk_rs") {
    return compact(v);
  }
  return num(v);
}

export function isNumericCol(col: string, rows: Record<string, Cell>[]): boolean {
  return rows.some((r) => typeof r[col] === "number");
}
