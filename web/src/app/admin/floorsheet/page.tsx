"use client";

import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { CircleAlert } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import { BrokerFlowPanel } from "@/components/broker-flow-panel";
import { StatTile } from "@/components/stat-tile";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api, qk, type BrokerNet } from "@/lib/api";
import { compact, num, price, rupees } from "@/lib/format";
import { cn } from "@/lib/utils";


function FloorsheetInner() {
  const params = useSearchParams();
  const router = useRouter();
  const symbol = (params.get("symbol") ?? "NABIL").toUpperCase();

  // Re-seed from the URL when the rail changes the symbol — see the same note on the chart page.
  // Without it the input keeps the old symbol while the tape below belongs to the new one.
  const [draft, setDraft] = useState(symbol);
  const [seenSymbol, setSeenSymbol] = useState(symbol);
  if (symbol !== seenSymbol) {
    setSeenSymbol(symbol);
    setDraft(symbol);
  }

  const symbols = useQuery({ queryKey: qk.symbols, queryFn: ({ signal }) => api.symbols(signal) });
  const dates = useQuery({
    queryKey: qk.floorsheetDates(symbol),
    queryFn: ({ signal }) => api.floorsheetDates(symbol, signal),
    retry: false,
  });

  const urlDate = params.get("date");
  const date = urlDate && dates.data?.sessions.includes(urlDate) ? urlDate : dates.data?.latest;

  const sheet = useQuery({
    queryKey: qk.floorsheet(symbol, date ?? ""),
    queryFn: ({ signal }) => api.floorsheet(symbol, date!, signal),
    enabled: Boolean(date),
  });

  const known = useMemo(() => new Set(symbols.data?.symbols ?? []), [symbols.data]);

  function go(nextSymbol: string, nextDate?: string) {
    const s = nextSymbol.trim().toUpperCase();
    if (!s || !known.has(s)) return;
    const q = new URLSearchParams({ symbol: s });
    if (nextDate) q.set("date", nextDate);
    router.push(`/admin/floorsheet?${q}`);
  }

  const t = sheet.data?.totals;
  // Top 15 by absolute net — the biggest movers on either side, which is what the Streamlit
  // bar chart showed. Sorting by net alone would hide the largest seller behind small buyers.
  const top = (sheet.data?.brokers ?? []).slice(0, 15);
  const scale = Math.max(1, ...top.map((b) => Math.abs(b.net)));

  return (
    <div className="space-y-4 p-4 md:p-6">
      <div className="flex flex-wrap items-center gap-3">
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && go(draft)}
          onBlur={() => go(draft)}
          list="fs-symbols"
          className="h-8 w-40 font-mono text-[13px] uppercase"
          placeholder="Symbol"
        />
        <datalist id="fs-symbols">
          {(symbols.data?.symbols ?? []).map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>

        {dates.data && (
          <select
            value={date ?? ""}
            onChange={(e) => go(symbol, e.target.value)}
            className="h-8 rounded-md border border-border bg-card px-2 font-mono text-[13px]"
          >
            {dates.data.sessions.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        )}

        {dates.data && (
          <span className="font-mono text-[11px] text-muted-foreground">
            {dates.data.sessions.length.toLocaleString()} sessions on file
          </span>
        )}
      </div>

      {dates.isError && (
        <div className="flex items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-[13px]">
          <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" />
          <div className="text-muted-foreground">{(dates.error as Error).message}</div>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <StatTile index={0} label="Trades" value={num(t?.trades, 0)} loading={sheet.isPending} />
        <StatTile index={1} label="Shares" value={num(t?.shares, 0)} loading={sheet.isPending} />
        <StatTile index={2} label="Turnover" value={rupees(t?.turnover)} loading={sheet.isPending} />
        <StatTile
          index={3}
          label="Avg trade"
          value={t?.avg_trade ? `${num(t.avg_trade, 0)} sh` : "—"}
          loading={sheet.isPending}
        />
        <StatTile index={4} label="Brokers" value={num(t?.brokers, 0)} loading={sheet.isPending} />
      </div>

      {/* ── who did what, this session ─────────────────────────────────────────────────── */}
      <section className="rounded-lg border border-border bg-card">
        <h3 className="border-b border-border px-4 py-2.5 text-[13px] font-semibold tracking-tight">
          Net shares per broker · {date ?? "—"}
        </h3>
        <div className="space-y-1.5 p-4">
          {sheet.isPending
            ? Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-5" />)
            : top.map((b, i) => <BrokerBar key={b.broker} row={b} scale={scale} index={i} />)}
          {!sheet.isPending && !top.length && (
            <p className="text-[13px] text-muted-foreground">No trades in this session.</p>
          )}
        </div>
        <p className="border-t border-border px-4 py-2 text-[12px] text-muted-foreground">
          Positive is a net buyer that session. Codes are NEPSE member numbers — a broker, not a
          client, which is the whole reason this cannot identify an operator.
        </p>
      </section>

      {/* ── accumulation over a window — shared with the Broker flow page ─────────────── */}
      <BrokerFlowPanel symbol={symbol} layoutId="fs-window" />

      {/* ── the raw tape ────────────────────────────────────────────────────────────────── */}
      <section className="rounded-lg border border-border bg-card">
        <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
          <h3 className="text-[13px] font-semibold tracking-tight">Trades</h3>
          <span className="font-mono text-[11px] text-muted-foreground">
            {sheet.data
              ? sheet.data.shown < sheet.data.trades_total
                ? `showing ${sheet.data.shown.toLocaleString()} of ${sheet.data.trades_total.toLocaleString()}`
                : `${sheet.data.trades_total.toLocaleString()} rows`
              : "—"}
          </span>
          {!!t?.unparsed_rows && (
            <span className="font-mono text-[11px] text-primary">
              {t.unparsed_rows} unparsed
            </span>
          )}
        </div>
        <div className="max-h-[420px] overflow-auto">
          <table className="w-full text-[12px]">
            <thead className="sticky top-0 bg-card">
              <tr className="border-b border-border text-muted-foreground">
                {["Buyer", "Seller", "Quantity", "Rate", "Amount", "Transaction"].map((h, i) => (
                  <th
                    key={h}
                    className={cn("px-3 py-1.5 font-medium", i < 2 ? "text-left" : "text-right")}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sheet.isPending
                ? Array.from({ length: 10 }).map((_, i) => (
                    <tr key={i}>
                      <td colSpan={6} className="px-3 py-1">
                        <Skeleton className="h-4 w-full" />
                      </td>
                    </tr>
                  ))
                : sheet.data?.trades.map((r) => (
                    <tr
                      key={r.transaction}
                      className="border-b border-border/50 hover:bg-accent/40"
                    >
                      <td className="px-3 py-1 font-mono">{r.buyer}</td>
                      <td className="px-3 py-1 font-mono">{r.seller}</td>
                      <td className="px-3 py-1 text-right font-mono tabular-nums">
                        {num(r.quantity, 0)}
                      </td>
                      <td className="px-3 py-1 text-right font-mono tabular-nums">
                        {price(r.rate)}
                      </td>
                      <td className="px-3 py-1 text-right font-mono tabular-nums">
                        {compact(r.amount)}
                      </td>
                      <td className="px-3 py-1 text-right font-mono text-[11px] text-muted-foreground">
                        {r.transaction}
                      </td>
                    </tr>
                  ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function BrokerBar({ row, scale, index }: { row: BrokerNet; scale: number; index: number }) {
  const up = row.net >= 0;
  return (
    <div className="flex items-center gap-2 text-[12px]">
      <span className="w-10 shrink-0 font-mono text-muted-foreground">{row.broker}</span>
      <div className="relative h-4 flex-1 overflow-hidden rounded bg-muted/50">
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${(Math.abs(row.net) / scale) * 100}%` }}
          transition={{ delay: index * 0.02, duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
          className={cn("h-full rounded", up ? "bg-up/70" : "bg-down/70")}
        />
      </div>
      <span
        className={cn(
          "w-24 shrink-0 text-right font-mono tabular-nums",
          up ? "text-up" : "text-down",
        )}
      >
        {up ? "+" : ""}
        {num(row.net, 0)}
      </span>
    </div>
  );
}

export default function FloorsheetPage() {
  return (
    <Suspense
      fallback={
        <div className="p-6">
          <Skeleton className="h-[520px] w-full" />
        </div>
      }
    >
      <FloorsheetInner />
    </Suspense>
  );
}
