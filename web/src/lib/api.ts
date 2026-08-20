/**
 * The only door between TypeScript and the Python backend.
 *
 * Nothing in `web/` computes an indicator, a score, a gate or an R-multiple — if a number
 * reaches the screen, a Python function produced it from the archive. This file is deliberately
 * the single place a network call happens, so that rule is enforceable by reading one file.
 *
 * Every board response carries `session`, `archive_session` and `stale`. Show them. A board is
 * only as fresh as the last time its script ran, and this project has already published
 * yesterday's analysis as today's more than once.
 */

/**
 * Empty means same-origin: in production nginx puts the Python API on `/api` of the very host
 * serving this page, so `/api/health` resolves itself and there is no CORS exchange and no
 * hostname baked into the bundle. Only dev needs an absolute URL, because the Next dev server
 * (3000) and the API (8600) are different origins.
 */
export const API_BASE = (
  process.env.NEXT_PUBLIC_API_BASE ??
  (process.env.NODE_ENV === "production" ? "" : "http://127.0.0.1:8600")
).replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly url: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const url = `${API_BASE}${path}`;
  let res: Response;
  try {
    res = await fetch(url, { signal, headers: { Accept: "application/json" } });
  } catch (cause) {
    // A dead backend is the single most likely failure in dev, and "Failed to fetch" tells the
    // reader nothing. Name the thing that is not running.
    throw new ApiError(
      `Cannot reach the API at ${API_BASE || "this origin"}. Is it running?  python -m api`,
      0,
      url,
    );
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(body?.error ?? `${res.status} ${res.statusText}`, res.status, url);
  }
  return body as T;
}

/* ── shapes ─────────────────────────────────────────────────────────────────────────────── */

export type Health = { ok: boolean; archive_session: string | null; symbols: number };

/** One saved broker login. Never carries the password, cookie or session token — only status. */
export type Connection = {
  /** a login is saved on the SERVER (not in this browser) */
  configured: boolean;
  session_saved: boolean;
  /** how old the cached session file is, in seconds */
  session_age_s: number | null;
  /** whether the broker was actually called; without a probe `ok` is null, meaning "not tested" */
  probed: boolean;
  ok: boolean | null;
  detail: string;
};

export type AuthStatus = {
  naasa: Connection;
  swp: Connection;
  /** always false — this surface cannot sign you in, and says so rather than offering a dead form */
  sign_in_here: boolean;
};

export type BoardName =
  | "swing_pro"
  | "supply_demand"
  | "scan"
  | "volume_spike"
  | "operator_scan"
  | "operator_now"
  | "operator_verdict"
  | "master_signal"
  | "swing_master"
  | "backtest";

/** A cell is already parsed server-side: numbers arrive as numbers, blanks as null. */
export type Cell = string | number | null;
export type Row = Record<string, Cell>;

export type Board = {
  rows: Row[];
  columns: string[];
  /** newest date the BOARD carries — not the archive's */
  session: string | null;
  /** newest daily bar on disk */
  archive_session: string | null;
  /** the board was computed before bars now on disk: say so, loudly */
  stale: boolean;
  /**
   * the board has rows but carries no date at all, so its freshness is UNKNOWN — which is not
   * the same as fresh. `stale` is false in both cases, which is exactly how four boards came to
   * report themselves current forever. Render this as a warning, never as silence.
   */
  session_unknown?: boolean;
  missing: boolean;
};

export type BoardsIndex = {
  archive_session: string | null;
  boards: Record<
    BoardName,
    { rows: number; session: string | null; stale: boolean; missing: boolean }
  >;
};

export type Bar = {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

/**
 * `kind` matters for reading the chart, not just for labelling it. A stock's bars are
 * corporate-action adjusted; an index's are raw and always will be, because an index has no
 * corporate actions and "adjusting" one could only invent a factor out of a large ordinary move.
 */
export type Bars = {
  symbol: string;
  kind: "stock" | "index";
  adjusted: boolean;
  bars: Bar[];
  /**
   * period -> one value per bar, `null` during the warm-up. Computed by `indicators.ema` on the
   * server. Nothing here recomputes it: a second implementation of a seeded EMA is a second
   * answer, and the one on screen would be the untested one.
   */
  ema?: Record<string, (number | null)[]>;
};

export type Scorecard = {
  symbol: string;
  total: number;
  grade: string;
  parts: { name: string; got: number; max: number }[];
};

export type Question = {
  n: number;
  question: string;
  answer: boolean | string;
  kind: "bool" | "text";
};

export type Questions = {
  symbol: string;
  /** Q9 is inverted on purpose: YES means "already too extended", i.e. BAD. Render it as a
   *  warning, never as a tick, or the whole point of the question is lost. */
  warning_questions: number[];
  answers: Question[];
};

export type Report = { symbol: string; text: string; fields: Record<string, unknown> };

export type Trade = {
  buyer: string;
  seller: string;
  quantity: number;
  rate: number;
  amount: number;
  transaction: string;
};

export type BrokerNet = { broker: string; bought: number; sold: number; net: number };

export type FloorsheetDates = { symbol: string; sessions: string[]; latest: string };

export type Floorsheet = {
  symbol: string;
  date: string;
  trades: Trade[];
  brokers: BrokerNet[];
  /** rows in `trades` vs rows in the session — a capped table must say it is capped */
  shown: number;
  trades_total: number;
  totals: {
    trades: number;
    shares: number;
    turnover: number;
    avg_trade: number | null;
    brokers: number;
    unparsed_rows: number;
  };
};

export type BrokerFlow = {
  symbol: string;
  sessions: number;
  from: string | null;
  to: string | null;
  accumulating: BrokerNet[];
  distributing: BrokerNet[];
  top5_buy_share: number | null;
  source: "broker_flow" | "floorsheet";
};

export type HeatSymbol = {
  symbol: string;
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number;
  pct: number | null;
  volume: number;
  turnover: number;
};

export type HeatSector = {
  sector: string;
  index: string | null;
  pct: number;
  turnover: number;
  count: number;
  symbols: HeatSymbol[];
  /** true = the sector's own published index; false = turnover-weighted from its members */
  official: boolean;
};

export type Heatmap = {
  session: string | null;
  archive_session: string | null;
  nepse: (HeatSymbol & { index: string }) | null;
  sectors: HeatSector[];
  symbols: number;
};

export type Indices = {
  session: string | null;
  rows: (HeatSymbol & { index: string })[];
};

export type Holding = {
  symbol: string;
  quantity: number | null;
  ltp: number | null;
  close: number | null;
  value: number | null;
  day_change: number | null;
  wacc: number | null;
};

export type Order = {
  symbol: string;
  side: string | null;
  quantity: number | null;
  remaining: number | null;
  price: number | null;
  status: string | null;
  time: string | null;
};

export type Collateral = { configured: boolean; fields: Record<string, number | null> };

/* ── calls ──────────────────────────────────────────────────────────────────────────────── */

export const api = {
  health: (signal?: AbortSignal) => get<Health>("/api/health", signal),
  boards: (signal?: AbortSignal) => get<BoardsIndex>("/api/boards", signal),
  board: (name: BoardName, signal?: AbortSignal) => get<Board>(`/api/board/${name}`, signal),
  symbols: (signal?: AbortSignal) =>
    get<{ symbols: string[]; indices: string[] }>("/api/symbols", signal),
  bars: (symbol: string, limit = 500, signal?: AbortSignal, ema?: number[]) =>
    get<Bars>(
      `/api/bars/${encodeURIComponent(symbol)}?limit=${limit}` +
        (ema?.length ? `&ema=${ema.join(",")}` : ""),
      signal,
    ),
  report: (symbol: string, signal?: AbortSignal) =>
    get<Report>(`/api/report/${encodeURIComponent(symbol)}`, signal),
  scorecard: (symbol: string, signal?: AbortSignal) =>
    get<Scorecard>(`/api/scorecard/${encodeURIComponent(symbol)}`, signal),
  questions: (symbol: string, signal?: AbortSignal) =>
    get<Questions>(`/api/questions/${encodeURIComponent(symbol)}`, signal),

  floorsheetDates: (symbol: string, signal?: AbortSignal) =>
    get<FloorsheetDates>(`/api/floorsheet/${encodeURIComponent(symbol)}`, signal),
  floorsheet: (symbol: string, date: string, signal?: AbortSignal) =>
    get<Floorsheet>(`/api/floorsheet/${encodeURIComponent(symbol)}?date=${date}`, signal),
  brokerFlow: (symbol: string, n = 20, signal?: AbortSignal) =>
    get<BrokerFlow>(`/api/brokerflow/${encodeURIComponent(symbol)}?n=${n}`, signal),
  heatmap: (signal?: AbortSignal) => get<Heatmap>("/api/heatmap", signal),
  indices: (signal?: AbortSignal) => get<Indices>("/api/indices", signal),

  /** Read-only. There is deliberately no place/modify/cancel here, and none on the server. */
  holdings: (signal?: AbortSignal) =>
    get<{ configured: boolean; rows: Holding[]; count: number }>("/api/account/holdings", signal),
  orderbook: (signal?: AbortSignal) =>
    get<{ configured: boolean; rows: Order[]; count: number }>("/api/account/orderbook", signal),
  collateral: (signal?: AbortSignal) => get<Collateral>("/api/account/collateral", signal),
  /** Status of the saved broker logins. `probe` actually calls the broker — slow, so opt-in. */
  auth: (probe = false, signal?: AbortSignal) =>
    get<AuthStatus>(`/api/auth${probe ? "?probe=1" : ""}`, signal),
};

/* ── query keys ─────────────────────────────────────────────────────────────────────────── */

export const qk = {
  health: ["health"] as const,
  boards: ["boards"] as const,
  board: (n: BoardName) => ["board", n] as const,
  symbols: ["symbols"] as const,
  bars: (s: string, limit: number, ema?: number[]) =>
    ["bars", s, limit, (ema ?? []).join(",")] as const,
  report: (s: string) => ["report", s] as const,
  scorecard: (s: string) => ["scorecard", s] as const,
  questions: (s: string) => ["questions", s] as const,
  floorsheetDates: (s: string) => ["floorsheet-dates", s] as const,
  floorsheet: (s: string, d: string) => ["floorsheet", s, d] as const,
  brokerFlow: (s: string, n: number) => ["brokerflow", s, n] as const,
  heatmap: ["heatmap"] as const,
  indices: ["indices"] as const,
  holdings: ["holdings"] as const,
  orderbook: ["orderbook"] as const,
  collateral: ["collateral"] as const,
  auth: (probe: boolean) => ["auth", probe] as const,
};
