"use client";

import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { api, qk, type BrokerNet } from "@/lib/api";
import { num } from "@/lib/format";
import { cn } from "@/lib/utils";

/** How many sessions the accumulation view aggregates. Matches the Streamlit slider's default. */
export const WINDOWS = [5, 10, 20, 60, 120];

/**
 * Net broker flow for one symbol over a window of sessions.
 *
 * Lives here rather than inside a page because two surfaces show it — its own Broker flow page
 * and a section of the Floorsheet page — and two copies would be two behaviours the moment one
 * is edited. `layoutId` only has to be unique among panels rendered at the same time; it drives
 * the sliding pill behind the selected window.
 */
export function BrokerFlowPanel({
  symbol,
  layoutId = "bf-window",
  initialWindow = 20,
}: {
  symbol: string;
  layoutId?: string;
  initialWindow?: number;
}) {
  const [window, setWindow] = useState(initialWindow);
  const flow = useQuery({
    queryKey: qk.brokerFlow(symbol, window),
    queryFn: ({ signal }) => api.brokerFlow(symbol, window, signal),
  });

  return (
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
                  layoutId={layoutId}
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
        . Read it as a description of the session, <em>not</em> a signal — the concentration family
        was tested out of sample and turned out to be a liquidity proxy. A broker being net-positive
        is true of essentially every symbol-day, so &quot;someone is accumulating&quot; on its own
        means nothing.
      </p>
    </section>
  );
}

export function BrokerList({
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
