"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Inbox } from "lucide-react";
import { useMemo } from "react";

import { BoardTable } from "@/components/board-table";
import { api, qk, type Board, type Row } from "@/lib/api";

/**
 * The four cross-sectional screens behind this page's tabs.
 *
 * The rest of the page answers "what does the engine say about ONE symbol". These answer
 * "which symbols, and which brokers, is it saying it about" — the same pre-computed
 * `board.txt` and `brokers.txt`, filtered and ranked, never recomputed here.
 *
 * Two rules every screen on this page obeys.
 *
 * **A screen is not a signal.** The backtest digest this engine ships says its own zone
 * entries do not beat random entry on this archive, and the repo's own operator search
 * killed six broker "accumulation" families. So each tab states what it selected on and
 * what that does NOT mean, in the panel, not in a tooltip.
 *
 * **Rank on the score that can actually rank.** `score` is the SWING score, a blend: a
 * symbol falling hard scores low on it, not high. A sell screen sorted by `score` would
 * be the buy screen upside down. Strong Sell sorts on `exit_score`, Strong Buy on
 * `entry_score` — sections 91 and 92, now on the board for exactly this reason.
 */
export const SCREENS = ["buy", "sell", "operator", "brokers", "probability"] as const;
export type ScreenId = (typeof SCREENS)[number];

export const SCREEN_LABEL: Record<ScreenId, string> = {
  buy: "Strong Buy",
  sell: "Strong Sell",
  operator: "Strong Operator",
  brokers: "Strong Brokers",
  probability: "Probability",
};

const BUY_SIGNALS = new Set(["STRONG BUY ZONE", "BUY ZONE"]);
const SELL_SIGNALS = new Set(["SELL / REDUCE ZONE", "STRONG EXIT / INVALIDATION"]);

function num(r: Row, k: string): number | null {
  const v = r[k];
  return typeof v === "number" ? v : null;
}

/**
 * How much MORE dominant this symbol's top broker is than its liquidity implies.
 *
 * Raw `top_broker_share` cannot be ranked across the board: measured on the shipped 481
 * rows it correlates −0.62 with the broker count, so sorting by it returns the thinnest
 * stocks in thinness order. Two brokers traded it, one of them bought — that is arithmetic,
 * not an operator.
 *
 * So each symbol is scored against symbols of SIMILAR liquidity: rows are split into
 * broker-count BANDS and the share is z-scored inside its own band. The confound goes with
 * it — the z correlates −0.01 with broker count — and the top of the list becomes NTC
 * (90 brokers), CGH (85), EBL (88) instead of five-broker shells.
 *
 * Bands, not fixed-size slices. Seven broker counts straddled a fixed-stride boundary on
 * the shipped board, so two symbols with the SAME 89 brokers landed in different peer
 * groups and their screen membership was decided by where the alphabet put them. A band
 * swallows its whole tie, and a leftover tail shorter than a band joins the one below it —
 * a fixed stride left an eleventh bucket of one row holding the market's most-traded name,
 * whose sd of 0 was rescued to 1 and produced z === 0 exactly, so the single most
 * concentrated stock on the board could never be screened at any threshold.
 */
export function dominance(rows: Row[]): Map<string, number> {
  const usable = rows
    .filter((r) => num(r, "brokers") !== null && num(r, "top_broker_share") !== null)
    .sort((a, b) => (num(a, "brokers") ?? 0) - (num(b, "brokers") ?? 0));
  const out = new Map<string, number>();
  const size = Math.max(1, Math.floor(usable.length / 10));
  for (let i = 0; i < usable.length; ) {
    let end = Math.min(i + size, usable.length);
    // extend past the boundary so an equal broker count is never split between two bands
    const edge = num(usable[end - 1], "brokers");
    while (end < usable.length && num(usable[end], "brokers") === edge) end++;
    // and absorb a tail too short to have an sd of its own
    if (usable.length - end < size) end = usable.length;
    const bucket = usable.slice(i, end);
    i = end;

    const vals = bucket.map((r) => num(r, "top_broker_share") ?? 0);
    if (vals.length < 2) continue;
    const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
    const sd = Math.sqrt(vals.reduce((a, b) => a + (b - mean) ** 2, 0) / vals.length);
    // No `|| 1` rescue. A band whose members all share one value has an UNDEFINED z, and
    // substituting 1 turns that into a confident 0. Leaving the symbol out of the map is
    // the honest answer; the `?? -9` at the call site already drops it from the screen.
    if (!sd) continue;
    for (const r of bucket) {
      out.set(String(r.symbol), ((num(r, "top_broker_share") ?? 0) - mean) / sd);
    }
  }
  return out;
}

/** Sort descending on a numeric column; rows missing it go last, never to the top. */
function byDesc(key: string) {
  return (a: Row, b: Row) => {
    const x = num(a, key);
    const y = num(b, key);
    if (x === null) return y === null ? 0 : 1;
    if (y === null) return -1;
    return y - x;
  };
}

function Note({ children }: { children: React.ReactNode }) {
  return (
    <p className="max-w-4xl px-1 text-[12px] leading-relaxed text-muted-foreground">
      {children}
    </p>
  );
}

function Banner({
  icon: Icon,
  children,
}: {
  icon: typeof AlertTriangle;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-primary/40 bg-primary/10 p-3.5 text-[13px]">
      <Icon className="mt-0.5 size-4 shrink-0 text-primary" />
      <div className="text-muted-foreground">{children}</div>
    </div>
  );
}

/** The freshness banners, in board-page.tsx's wording — one board, two ways to be wrong. */
function Freshness({ b, rebuild }: { b: Board | undefined; rebuild: string }) {
  if (!b) return null;
  return (
    <>
      {b.missing && (
        <Banner icon={Inbox}>
          This board has never been built — run{" "}
          <span className="font-mono text-foreground">{rebuild}</span>. The broker board is
          written only by a FULL run (no <span className="font-mono">--limit</span>), after
          the symbol board.
        </Banner>
      )}
      {b.session_unknown && (
        <Banner icon={AlertTriangle}>
          This board carries no date, so there is no way to tell which session it was computed
          on — it may be today&apos;s or it may be weeks old. Treat every number below as
          undated.
        </Banner>
      )}
      {b.stale && (
        <Banner icon={AlertTriangle}>
          Computed on <span className="font-mono text-foreground">{b.session}</span>, but the
          archive already reaches{" "}
          <span className="font-mono text-foreground">{b.archive_session}</span>. Every row
          below is from the earlier session — rebuild with{" "}
          <span className="font-mono text-foreground">{rebuild}</span>.
        </Banner>
      )}
    </>
  );
}

export function Screen({
  id,
  rebuild,
  onSymbol,
}: {
  id: ScreenId;
  rebuild: string;
  /** clicking a row jumps the page's symbol input — screens feed the detail, they don't replace it */
  onSymbol: (symbol: string) => void;
}) {
  // Both boards are fetched by every screen so switching tabs is instant after the first
  // load; TanStack dedupes them and neither is large (481 and 91 rows).
  const board = useQuery({
    queryKey: qk.board("swing_quantam"),
    queryFn: ({ signal }) => api.board("swing_quantam", signal),
  });
  const brokers = useQuery({
    queryKey: qk.board("swing_quantam_brokers"),
    queryFn: ({ signal }) => api.board("swing_quantam_brokers", signal),
    enabled: id === "brokers",
  });
  const prob = useQuery({
    queryKey: qk.board("swing_quantam_probability"),
    queryFn: ({ signal }) => api.board("swing_quantam_probability", signal),
    enabled: id === "probability",
  });

  const q = id === "brokers" ? brokers : id === "probability" ? prob : board;
  const rows = useMemo(() => q.data?.rows ?? [], [q.data]);
  const dom = useMemo(
    () => (id === "operator" ? dominance(rows) : new Map<string, number>()),
    [id, rows],
  );

  const view = useMemo(() => {
    if (id === "buy") {
      return rows
        .filter((r) => BUY_SIGNALS.has(String(r.signal)) && r.confidence !== "low")
        .sort(byDesc("entry_score"));
    }
    if (id === "sell") {
      return rows.filter((r) => SELL_SIGNALS.has(String(r.signal))).sort(byDesc("exit_score"));
    }
    if (id === "operator") {
      return rows
        .filter((r) => (dom.get(String(r.symbol)) ?? -9) >= 1.5)
        .map((r) => ({ ...r, dominance: Number((dom.get(String(r.symbol)) ?? 0).toFixed(2)) }))
        .sort(byDesc("dominance"));
    }
    if (id === "probability") {
      // Rows with no p_up were never priced — no daily bar file, or an inverted
      // ladder. Dropping them is not hiding them: the panel says how many, and a
      // blank probability beside a real target reads as a low one.
      return rows.filter((r) => num(r, "p_up") !== null).sort(byDesc("net_edge"));
    }
    return [...rows].sort(byDesc("conviction"));
  }, [id, rows, dom]);

  const columns = useMemo(() => {
    const base = q.data?.columns ?? [];
    if (id === "operator") {
      // `dominance` is computed here, so it is not in the API's column list — put it first,
      // where the thing the table is sorted by belongs.
      return ["symbol", "dominance", ...base.filter((c) => c !== "symbol")];
    }
    return base;
  }, [q.data, id]);

  if (q.isPending) {
    return <div className="p-4 text-[13px] text-muted-foreground">Loading…</div>;
  }
  // A board that exists-but-is-empty returns 200 with `missing: true` — Freshness renders
  // that. Reaching here means the REQUEST failed, so say so rather than guessing at a cause.
  if (q.isError) {
    return <Banner icon={AlertTriangle}>{(q.error as Error).message}</Banner>;
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <Freshness b={q.data} rebuild={rebuild} />

      {id === "buy" && (
        <Note>
          <strong>{view.length}</strong> of {rows.length} symbols sit in an accumulation zone —
          signal <span className="font-mono">STRONG BUY ZONE</span> or{" "}
          <span className="font-mono">BUY ZONE</span>, with confidence above{" "}
          <span className="font-mono">low</span>. Ranked by <strong>entry score</strong>{" "}
          (section 91), not by the blended swing score. This is a shortlist of where the engine
          places price inside its own zone ladder — the shipped backtest digest on each symbol
          page reports that those entries did not beat a random entry date on this archive, so
          read it as a starting point for work, not as a call.
        </Note>
      )}

      {id === "sell" && (
        <Note>
          <strong>{view.length}</strong> of {rows.length} symbols are at or below their
          invalidation level — signal <span className="font-mono">SELL / REDUCE ZONE</span> or{" "}
          <span className="font-mono">STRONG EXIT / INVALIDATION</span>. That is a majority of
          the board, because most of NEPSE is below its 30-day accumulation zone right now: the
          label is a <em>state</em>, not a ranking. The ranking is the{" "}
          <strong>exit score</strong> (section 92), which is what puts the strongest exits at
          the top.
        </Note>
      )}

      {id === "operator" && (
        <Note>
          Symbols whose single largest net broker holds a bigger share of 30-day flow than
          symbols of <em>similar liquidity</em> do — <strong>{view.length}</strong> at
          1.5 standard deviations or more above their broker-count band. The banding is the
          whole point: raw top-broker share correlates −0.62 with how many brokers traded the
          stock, so ranking on it returns the thinnest names in thinness order rather than
          anything about an operator. This is an <strong>activity screen</strong>. The
          floorsheet carries broker IDs, not client IDs, so a dominant broker is one member
          firm&apos;s whole order book — it is not evidence of one buyer, and this repo&apos;s
          own operator-factor search found no edge in it.
        </Note>
      )}

      {id === "probability" && (
        <Note>
          For each symbol&apos;s own zone ladder: how often, in <em>its own price
          history</em>, did price touch <strong>target 1</strong> before the{" "}
          <strong>stop</strong>, within 20 sessions? A real barrier test on daily highs
          and lows, not a close-to-close guess. <strong>{view.length}</strong> of{" "}
          {rows.length} symbols could be priced — the rest have no daily bar file or an
          inverted ladder, and print nothing rather than a 50/50.
          <br />
          <br />
          <strong>This is a base rate, not a forecast.</strong> It is deliberately{" "}
          <em>not</em> conditioned on today&apos;s signal: the shipped backtest found
          this engine&apos;s buy zone significantly negative over 6,964 stock-days, so
          conditioning on it would attach a number to a rejected claim. What you are
          reading is geometry and volatility — how near the target is, how far the stop
          is, and how much this stock moves.
          <br />
          <br />
          Read the three outcomes together. <span className="font-mono">p none</span> is
          the share of windows that touched <em>neither</em> barrier in 20 sessions, and
          it is often the largest of the three — a high net edge sitting beside a{" "}
          <span className="font-mono">p none</span> of 80% is drift, not the ladder
          working. Sorted by <strong>net edge</strong>, never by{" "}
          <span className="font-mono">p up</span>: measured here, p&nbsp;up correlates
          −0.50 with reward:risk, so ranking on it just returns the nearest targets. Net
          edge charges the same <strong>0.8% round trip</strong> this repo&apos;s own
          backtest charges — and only <strong>62 of 323</strong> priced symbols clear it.
          <br />
          <br />
          <strong>Then check{" "}
          <span className="font-mono">dist to entry</span> before you read anything
          else.</strong>{" "}
          It is how far today&apos;s price sits from the entry zone the rest of the row
          describes, and the median row is <strong>−8.8%</strong>: the plan is not live,
          because you cannot buy at a zone the price is nowhere near. Only{" "}
          <strong>105 of 323</strong> ladders sit within 5% of price, and only{" "}
          <strong>13</strong> of those also clear the 0.8% cost. Sort on this column to
          see them. The ranking is not filtered to them — a number is easier to argue
          with than a row that was quietly removed. Two milder confounds worth the same
          suspicion: net edge correlates +0.30 with how far away target 1 is and +0.16
          with <span className="font-mono">p none</span>, so part of a high score is
          simply a distant target that rarely resolves either way.
        </Note>
      )}

      {id === "brokers" && (
        <Note>
          Every member firm&apos;s market-wide footprint over the last 30 sessions, ranked by{" "}
          <strong>conviction</strong> = net ÷ gross rupees. Conviction rather than net rupees
          because the biggest net is usually just the biggest broker. No size floor is applied
          and that is measured, not assumed: the thinnest broker here still traded 11 symbols,
          the median 217, and conviction is <em>stronger</em> among the larger firms — a floor
          would remove the readings, not the noise. There is no share-of-market column of
          either kind: share of net is 0÷0 (every share bought is sold, so the market's net
          is exactly zero), and share of gross is <span className="font-mono">gross_amt</span>{" "}
          divided by one constant — the same ranking under a second name.
        </Note>
      )}

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-border bg-card">
        <BoardTable
          rows={view}
          columns={columns}
          hide={["date"]}
          priority={
            id === "probability"
              ? ["symbol", "signal", "net_edge", "p_up", "p_down", "p_none",
                 "price", "entry", "target1", "target2", "stop", "rr1"]
              : id === "brokers"
              ? ["broker", "conviction", "net_amt", "gross_amt", "symbols_net_buy", "symbols"]
              : id === "operator"
                ? ["symbol", "dominance", "top_broker", "top_broker_share", "brokers", "signal"]
                : id === "sell"
                  ? ["symbol", "signal", "exit_score", "score", "confidence", "age"]
                  : ["symbol", "signal", "entry_score", "score", "confidence", "age"]
          }
          // brokers.txt has no `symbol` column, so there is nothing to navigate to —
          // passing a handler anyway painted 91 rows as clickable for a silent no-op.
          onRowClick={id === "brokers" ? undefined : (r) => onSymbol(String(r.symbol))}
          emptyMessage={
            id === "probability"
              ? "No symbol could be priced — the daily bar archive is missing."
              : id === "brokers"
              ? "No brokers on this board."
              : "Nothing matches this screen on the current session — which is an answer, not an error."
          }
        />
      </div>
    </div>
  );
}
