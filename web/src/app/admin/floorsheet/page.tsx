"use client";

import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { CircleAlert } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import { StatTile } from "@/components/stat-tile";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api, qk, type BrokerNet } from "@/lib/api";
import { compact, num, price, rupees } from "@/lib/format";
import { cn } from "@/lib/utils";

/** How many sessions the accumulation view aggregates. Matches the Streamlit slider's default. */
const WINDOWS = [5, 10, 20, 60, 120];

function FloorsheetInner() {
  const params = useSearchParams();
  const router = useRouter();
  const symbol = (params.get("symbol") ?? "NABIL").toUpperCase();
  const [window, setWindow] = useState(20);

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
  const flow = useQuery({
    queryKey: qk.brokerFlow(symbol, window),
    queryFn: ({ signal }) => api.brokerFlow(symbol, window, signal),
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

      {/* ── accumulation over a window ──────────────────────────────────────────────────── */}
      <section className="rounded-lg border border-border bg-card">
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2.5">
          <h3 className="text-[13px] font-semibold tracking-tight">Net broker flow</h3>
          <div className="flex gap-1">
            {WINDOWS.map((w) => (
              <button
                key={w}
                onClick={() => setWindow(w)}
                className={cn(
                  "relative rounded-md px-2 py-0.5 text-[12px] transition-colors",
                  w === window ? "text-foreground" : "text-muted-foreground hover:text-foreground",
                )}
              >
                {w === window && (
                  <motion.span
                    layoutId="fs-window"
                    transition={{ type: "spring", stiffness: 500, damping: 40 }}
                    className="absolute inset-0 -z-10 rounded-md bg-accent"
                  />
                )}
                {w}
              </button>
            ))}
          </div>
          <span className="ml-auto font-mono text-[11px] text-muted-foreground">
            {flow.data?.from ?? "—"} → {flow.data?.to ?? "—"}
          </span>
        </div>

        <div className="grid gap-px bg-border md:grid-cols-2">
          <BrokerList
            title="Accumulating"
            rows={flow.data?.accumulating ?? []}
            loading={flow.isPending}
            tone="up"
          />
          <BrokerList
            title="Distributing"
            rows={flow.data?.distributing ?? []}
            loading={flow.isPending}
            tone="down"
          />
        </div>

        <p className="border-t border-border px-4 py-2.5 text-[12px] text-muted-foreground">
          Top-5 share of all buying:{" "}
          <span className="font-mono text-foreground">
            {flow.data?.top5_buy_share != null ? `${flow.data.top5_buy_share.toFixed(1)}%` : "—"}
          </span>
          . Read it as a description of the session, <em>not</em> a signal — the concentration
          family was tested out of sample and turned out to be a liquidity proxy. A broker being
          net-positive is true of essentially every symbol-day, so &quot;someone is accumulating&quot;
          on its own means nothing.
        </p>
      </section>

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

function BrokerList({
  title,
  rows,
  loading,
  tone,
}: {
  title: string;
  rows: BrokerNet[];
  loading: boolean;
  tone: "up" | "down";
}) {
  return (
    <div className="bg-card p-4">
      <div className="mb-2 text-[12px] font-medium">{title}</div>
      {loading ? (
        <div className="space-y-1.5">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-5" />
          ))}
        </div>
      ) : !rows.length ? (
        <p className="text-[12px] text-muted-foreground">Nobody, over this window.</p>
      ) : (
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-muted-foreground">
              <th className="pb-1 text-left font-medium">Broker</th>
              <th className="pb-1 text-right font-medium">Net</th>
              <th className="pb-1 text-right font-medium">Bought</th>
              <th className="pb-1 text-right font-medium">Sold</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.broker} className="border-t border-border/50">
                <td className="py-1 font-mono">{r.broker}</td>
                <td
                  className={cn(
                    "py-1 text-right font-mono tabular-nums",
                    tone === "up" ? "text-up" : "text-down",
                  )}
                >
                  {num(r.net, 0)}
                </td>
                <td className="py-1 text-right font-mono tabular-nums text-muted-foreground">
                  {num(r.bought, 0)}
                </td>
                <td className="py-1 text-right font-mono tabular-nums text-muted-foreground">
                  {num(r.sold, 0)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
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
