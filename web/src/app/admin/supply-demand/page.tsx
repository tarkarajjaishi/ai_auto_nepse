"use client";

import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Loader2, Search, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { BoardPage } from "@/components/board-page";
import { ZoneChart } from "@/components/zone-chart";
import { api, qk } from "@/lib/api";
import { cn } from "@/lib/utils";

/** The vendor's settings panel has exactly these toggles; the strong zone is always drawn. */
const TOGGLES = ["untested", "weak", "turncoat"] as const;

export default function SupplyDemandPage() {
  const [picked, setPicked] = useState<string | null>(null);
  return (
    <BoardPage
      board="supply_demand"
      rebuildHint="python supply_demand.py"
      onRowClick={(r) => setPicked(String(r.symbol))}
      priority={["symbol", "signal", "order", "entry", "sl", "tp", "confirmed", "vol_x", "trend", "in_zone", "state", "age"]}
      filters={[
        { label: "Confirmed", test: (r) => r.confirmed === "yes" },
        { label: "At a zone", test: (r) => r.signal !== "WATCH" },
        { label: "Closed inside", test: (r) => r.in_zone === "yes" },
        { label: "Everything", test: () => true },
      ]}
      blurb={
        <>
          A port of Indicator Vault&apos;s <em>Supply Demand Dashboard</em> onto the NEPSE archive —
          their engine, our market. A fractal swing high prints a <strong>supply</strong> zone, a
          swing low a <strong>demand</strong> zone, the stop sits an ATR beyond the far edge.{" "}
          <strong>confirmed</strong> is the only column backed by measurement rather than by the
          vendor: across the full replay, volume confirmation on a confirmed uptrend was the
          single ingredient that kept its edge out of sample. (The trade count is not quoted — this
          page, Master signal and ui.py had each transcribed a different number for the same run.) Read the rest as{" "}
          <em>where the levels are</em>, not as a tested edge.
        </>
      }
    >
      {picked && <Zones symbol={picked} onClose={() => setPicked(null)} />}
      <Timeframes />
    </BoardPage>
  );
}

/**
 * The chart the vendor draws: candles, every zone extended to the right edge, and the levels.
 *
 * Opened by clicking a row, which is what the Streamlit page does — the row click there does NOT
 * navigate away, because the whole point is to see the levels in the row against the price that
 * produced them.
 */
function Zones({ symbol, onClose }: { symbol: string; onClose: () => void }) {
  // "strong" is not a toggle: a zone price has respected is the one you always want to see.
  const [states, setStates] = useState<Set<string>>(new Set(["strong", "untested"]));
  const q = useQuery({
    queryKey: ["zones", symbol],
    queryFn: ({ signal }) => api.zones(symbol, 180, signal),
    retry: false,
  });

  const toggle = (k: string) =>
    setStates((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });

  const lv = q.data?.levels;
  const shown = q.data?.zones.filter((z) => states.has(z.state)).length ?? 0;

  return (
    <section className="rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-4 py-2.5">
        <h3 className="text-[13px] font-semibold tracking-tight">{symbol} · supply and demand</h3>
        {lv && (
          <span
            className={cn(
              "rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide",
              lv.signal === "BUY"
                ? "bg-up/15 text-up"
                : lv.signal === "SELL"
                  ? "bg-down/15 text-down"
                  : "bg-muted text-muted-foreground",
            )}
          >
            {lv.signal}
            {lv.signal !== "WATCH" && (lv.in_zone ? " · closed in zone" : " · wick only")}
          </span>
        )}

        <span className="ml-auto flex items-center gap-1.5">
          {TOGGLES.map((k) => (
            <button
              key={k}
              onClick={() => toggle(k)}
              className={cn(
                "rounded-md border px-2 py-0.5 font-mono text-[11px] transition-colors",
                states.has(k)
                  ? "border-primary/40 bg-primary/10 text-primary"
                  : "border-border text-muted-foreground hover:bg-accent",
              )}
            >
              {k}
            </button>
          ))}
        </span>

        <Link
          href={`/admin/chart?symbol=${encodeURIComponent(symbol)}`}
          className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-0.5 text-[12px] transition-colors hover:bg-accent"
        >
          <ExternalLink className="size-3" /> Chart
        </Link>
        <button
          onClick={onClose}
          aria-label="Close the zone chart"
          className="rounded-md border border-border p-1 transition-colors hover:bg-accent"
        >
          <X className="size-3" />
        </button>
      </div>

      {q.isPending ? (
        <p className="px-4 py-6 text-[12px] text-muted-foreground">Reading zones for {symbol}…</p>
      ) : q.isError ? (
        <p className="px-4 py-6 text-[12px] text-muted-foreground">{(q.error as Error).message}</p>
      ) : q.data ? (
        <>
          <ZoneChart data={q.data} states={states} />
          <p className="border-t border-border px-4 py-2.5 text-[12px] leading-snug text-muted-foreground">
            {shown} of {q.data.zones.length} zones drawn, each running from the candle that formed
            it to the right edge — a zone stays live until price closes through it.{" "}
            <strong>Strong</strong> is always shown; a <strong>turncoat</strong> is a zone price
            has already closed through, kept because old supply often becomes new demand.
            {lv && (
              <>
                {" "}
                The dotted lines are this board&apos;s entry, stop and target for {symbol}.
              </>
            )}
          </p>
        </>
      ) : null}
    </section>
  );
}

/**
 * The same engine run across every timeframe for ONE scrip.
 *
 * The board holds the daily row for the whole market. This answers the question the board cannot:
 * *and what do the other frames say?* It is computed on demand — about a second for one symbol,
 * which would be six minutes for all 340 — so it is a button, not a poll.
 *
 * Disagreement between frames is the normal result and is not a fault: the short frames turn
 * first. The longer frame is the context, the shorter one the timing.
 */
function Timeframes() {
  const [draft, setDraft] = useState("");
  const [symbol, setSymbol] = useState<string | null>(null);

  const universe = useQuery({ queryKey: qk.symbols, queryFn: ({ signal }) => api.symbols(signal) });
  const q = useQuery({
    queryKey: ["timeframes", symbol],
    queryFn: ({ signal }) => api.timeframes(symbol!, signal),
    enabled: Boolean(symbol),
    retry: false,
  });

  const known = new Set(universe.data?.symbols ?? []);
  const go = () => {
    const s = draft.trim().toUpperCase();
    if (s && known.has(s)) setSymbol(s);
  };

  return (
    <section className="rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-4 py-2.5">
        <h3 className="text-[13px] font-semibold tracking-tight">Across timeframes</h3>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value.toUpperCase())}
          onKeyDown={(e) => e.key === "Enter" && go()}
          list="sd-symbols"
          placeholder="SYMBOL"
          className="h-8 w-32 rounded-md border border-border bg-background px-2 font-mono text-[13px] outline-none focus:border-primary/60"
        />
        <datalist id="sd-symbols">
          {(universe.data?.symbols ?? []).map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
        <button
          onClick={go}
          disabled={!known.has(draft.trim().toUpperCase()) || q.isFetching}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-[12px] transition-colors",
            known.has(draft.trim().toUpperCase()) && !q.isFetching
              ? "hover:bg-accent"
              : "cursor-not-allowed text-muted-foreground/60",
          )}
        >
          {q.isFetching ? (
            <>
              <Loader2 className="size-3 animate-spin" /> scanning
            </>
          ) : (
            <>
              <Search className="size-3" /> Scan timeframes
            </>
          )}
        </button>
        {symbol && draft.trim().toUpperCase() !== symbol && (
          // The Streamlit version carried this exact warning: the box moved on since the scan ran,
          // so the table below is about a different scrip than the one now typed.
          <span className="text-[12px] text-primary">
            Showing <strong>{symbol}</strong> — press Scan to run{" "}
            {draft.trim().toUpperCase() || "another"}.
          </span>
        )}
      </div>

      {!symbol ? (
        <p className="px-4 py-3 text-[12px] text-muted-foreground">
          The board above is the daily frame for the whole market. Enter one scrip to see what
          5m, 15m, 30m, 1h, 1D, 1W and 1M each say about it.
        </p>
      ) : q.isError ? (
        <p className="px-4 py-3 text-[12px] text-muted-foreground">{(q.error as Error).message}</p>
      ) : q.data ? (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="border-b border-border font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                  {["timeframe", "direction", "state", "age", "signal", "close", "entry", "sl", "tp", "risk %", "dist %"].map((h) => (
                    <th
                      key={h}
                      className={cn("px-3 py-1.5 font-medium", h === "timeframe" || h === "direction" || h === "state" || h === "signal" ? "text-left" : "text-right")}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {q.data.timeframes.map((r) => (
                  <tr key={r.timeframe} className="border-b border-border/50 last:border-0">
                    <td className="px-3 py-1.5 font-mono font-medium">{r.timeframe}</td>
                    <td
                      className={cn(
                        "px-3 py-1.5",
                        r.direction === "Bullish" ? "text-up" : "text-down",
                      )}
                    >
                      {r.direction}
                    </td>
                    <td className="px-3 py-1.5 text-muted-foreground">{r.state}</td>
                    <td className="px-3 py-1.5 text-right font-mono tabular-nums">{r.age}</td>
                    <td
                      className={cn(
                        "px-3 py-1.5 font-medium",
                        r.signal === "BUY" ? "text-up" : r.signal === "SELL" ? "text-down" : "text-muted-foreground",
                      )}
                    >
                      {r.signal}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono tabular-nums">{r.close}</td>
                    <td className="px-3 py-1.5 text-right font-mono tabular-nums">{r.entry}</td>
                    <td className="px-3 py-1.5 text-right font-mono tabular-nums text-down">{r.sl}</td>
                    <td className="px-3 py-1.5 text-right font-mono tabular-nums text-up">{r.tp}</td>
                    <td className="px-3 py-1.5 text-right font-mono tabular-nums">{r.risk_pct}</td>
                    <td className="px-3 py-1.5 text-right font-mono tabular-nums">{r.dist_pct}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="border-t border-border px-4 py-2.5 text-[12px] leading-snug text-muted-foreground">
            {q.data.agree ? (
              <>
                Every timeframe agrees — <strong>the cleanest read this board gives</strong>.
              </>
            ) : (
              <>
                Timeframes disagree, which is normal: the short frames turn first. The longer frame
                is the context, the shorter one the timing.
              </>
            )}
          </p>
        </>
      ) : (
        <p className="px-4 py-3 text-[12px] text-muted-foreground">Scanning {symbol}…</p>
      )}
    </section>
  );
}
