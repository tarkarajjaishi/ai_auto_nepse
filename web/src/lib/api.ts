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

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") ?? "http://127.0.0.1:8600";

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
      `Cannot reach the API at ${API_BASE}. Is it running?  python -m api`,
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

export type Bars = { symbol: string; adjusted: boolean; bars: Bar[] };

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

/* ── calls ──────────────────────────────────────────────────────────────────────────────── */

export const api = {
  health: (signal?: AbortSignal) => get<Health>("/api/health", signal),
  boards: (signal?: AbortSignal) => get<BoardsIndex>("/api/boards", signal),
  board: (name: BoardName, signal?: AbortSignal) => get<Board>(`/api/board/${name}`, signal),
  symbols: (signal?: AbortSignal) => get<{ symbols: string[] }>("/api/symbols", signal),
  bars: (symbol: string, limit = 500, signal?: AbortSignal) =>
    get<Bars>(`/api/bars/${encodeURIComponent(symbol)}?limit=${limit}`, signal),
  report: (symbol: string, signal?: AbortSignal) =>
    get<Report>(`/api/report/${encodeURIComponent(symbol)}`, signal),
  scorecard: (symbol: string, signal?: AbortSignal) =>
    get<Scorecard>(`/api/scorecard/${encodeURIComponent(symbol)}`, signal),
  questions: (symbol: string, signal?: AbortSignal) =>
    get<Questions>(`/api/questions/${encodeURIComponent(symbol)}`, signal),
};

/* ── query keys ─────────────────────────────────────────────────────────────────────────── */

export const qk = {
  health: ["health"] as const,
  boards: ["boards"] as const,
  board: (n: BoardName) => ["board", n] as const,
  symbols: ["symbols"] as const,
  bars: (s: string, limit: number) => ["bars", s, limit] as const,
  report: (s: string) => ["report", s] as const,
  scorecard: (s: string) => ["scorecard", s] as const,
  questions: (s: string) => ["questions", s] as const,
};
