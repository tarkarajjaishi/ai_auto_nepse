"use client";

import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { AlertTriangle, CircleAlert } from "lucide-react";
import { useMemo, useState } from "react";

import { SectorTreemap } from "@/components/sector-treemap";
import { Skeleton } from "@/components/ui/skeleton";
import { api, qk, type HeatSector } from "@/lib/api";
import { compact, num, pct, price } from "@/lib/format";
import { cn } from "@/lib/utils";

export default function HeatmapPage() {
  const [drill, setDrill] = useState<string | null>(null);
  // Live during a session, per CLAUDE.md's live-data rule: the server overlays the socket
  // snapshot onto both, so this only has to keep asking.
  const hm = useQuery({
    queryKey: qk.heatmap,
    queryFn: ({ signal }) => api.heatmap(signal),
    refetchInterval: 1000,
  });
  const idx = useQuery({
    queryKey: qk.indices,
    queryFn: ({ signal }) => api.indices(signal),
    refetchInterval: 1000,
  });

  const sectors = hm.data?.sectors ?? [];
  const open = useMemo(
    () => sectors.find((s) => s.sector === drill) ?? null,
    [sectors, drill],
  );

  // One shared treemap for both heatmaps — see components/sector-treemap.tsx. The option used
  // to be duplicated here and in the chart drawer, and the two drifted until this copy rendered
  // no labels at all.
  const items = useMemo(
    () =>
      open
        ? open.symbols.map((sym) => ({
            name: sym.symbol,
            pct: sym.pct,
            turnover: sym.turnover,
            note: `close ${price(sym.close)}`,
          }))
        : sectors.map((sec) => ({
            name: sec.sector,
            pct: sec.pct,
            turnover: sec.turnover,
            note: `${sec.count} scrip · ${sec.official ? "published index" : "turnover-weighted"}`,
          })),
    [open, sectors],
  );

  return (
    <div className="space-y-4 p-4 md:p-6">
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="text-[15px] font-semibold tracking-tight">
          {open ? open.sector : "NEPSE by sector"}
        </h2>
        {open && (
          <button
            onClick={() => setDrill(null)}
            className="rounded-md bg-accent px-2 py-0.5 text-[12px] text-muted-foreground transition-colors hover:text-foreground"
          >
            ← all sectors
          </button>
        )}
        {hm.data?.nepse && (
          <span className="font-mono text-[12px]">
            NEPSE {price(hm.data.nepse.close)}{" "}
            <span className={cn(hm.data.nepse.pct! >= 0 ? "text-up" : "text-down")}>
              {pct(hm.data.nepse.pct)}
            </span>
          </span>
        )}
        <span className="ml-auto font-mono text-[11px] text-muted-foreground">
          {hm.data?.symbols ?? 0} scrip · session {hm.data?.session ?? "—"}
        </span>
      </div>

      {hm.isError && (
        <div className="flex items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-[13px]">
          <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" />
          <div className="text-muted-foreground">{(hm.error as Error).message}</div>
        </div>
      )}

      {hm.data?.session && hm.data.archive_session && hm.data.session < hm.data.archive_session && (
        <motion.div
          initial={{ opacity: 0, y: -6 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex items-start gap-3 rounded-lg border border-primary/40 bg-primary/10 p-3.5 text-[13px]"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-primary" />
          <div className="text-muted-foreground">
            These tiles are the{" "}
            <span className="font-mono text-foreground">{hm.data.session}</span> session, but the
            archive already reaches{" "}
            <span className="font-mono text-foreground">{hm.data.archive_session}</span>.
          </div>
        </motion.div>
      )}

      <div className="rounded-lg border border-border bg-card p-2">
        {hm.isPending ? (
          <Skeleton className="h-[460px] w-full" />
        ) : (
          <SectorTreemap
            items={items}
            height={460}
            sat={open ? 5 : 3}
            onSelect={(name) => {
              if (!open && sectors.some((x) => x.sector === name)) setDrill(name);
            }}
          />
        )}
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <SectorTable sectors={sectors} onPick={setDrill} loading={hm.isPending} />

        <section className="rounded-lg border border-border bg-card">
          <h3 className="border-b border-border px-4 py-2.5 text-[13px] font-semibold tracking-tight">
            Index watchlist
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="px-3 py-1.5 text-left font-medium">Index</th>
                  {["Close", "High", "Low", "Turnover", "%Chg"].map((h) => (
                    <th key={h} className="px-3 py-1.5 text-right font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {idx.isPending
                  ? Array.from({ length: 8 }).map((_, i) => (
                      <tr key={i}>
                        <td colSpan={6} className="px-3 py-1">
                          <Skeleton className="h-4 w-full" />
                        </td>
                      </tr>
                    ))
                  : idx.data?.rows.map((r) => (
                      <tr key={r.index} className="border-b border-border/50 hover:bg-accent/40">
                        <td className="px-3 py-1 font-mono">{r.index}</td>
                        <td className="px-3 py-1 text-right font-mono tabular-nums">
                          {price(r.close)}
                        </td>
                        <td className="px-3 py-1 text-right font-mono tabular-nums text-muted-foreground">
                          {price(r.high)}
                        </td>
                        <td className="px-3 py-1 text-right font-mono tabular-nums text-muted-foreground">
                          {price(r.low)}
                        </td>
                        <td className="px-3 py-1 text-right font-mono tabular-nums text-muted-foreground">
                          {compact(r.turnover)}
                        </td>
                        <td
                          className={cn(
                            "px-3 py-1 text-right font-mono tabular-nums",
                            (r.pct ?? 0) >= 0 ? "text-up" : "text-down",
                          )}
                        >
                          {pct(r.pct)}
                        </td>
                      </tr>
                    ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <p className="max-w-4xl text-[13px] leading-relaxed text-muted-foreground">
        Built from the <strong>archive</strong>, not the live feed — so it works signed out, and it
        works when the market is shut, which is most of the day. A sector is coloured by its own
        published index where one exists and by a turnover-weighted average of its members where
        one does not; the tooltip says which. Percent change is the exchange&apos;s own figure,
        computed on raw closes, so on an ex-dividend date it shows the drop the tape actually
        printed rather than the adjusted series the charts use.
      </p>
    </div>
  );
}

function SectorTable({
  sectors,
  onPick,
  loading,
}: {
  sectors: HeatSector[];
  onPick: (s: string) => void;
  loading: boolean;
}) {
  return (
    <section className="rounded-lg border border-border bg-card">
      <h3 className="border-b border-border px-4 py-2.5 text-[13px] font-semibold tracking-tight">
        Sectors
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-[12px]">
          <thead>
            <tr className="border-b border-border text-muted-foreground">
              <th className="px-3 py-1.5 text-left font-medium">Sector</th>
              <th className="px-3 py-1.5 text-right font-medium">Scrip</th>
              <th className="px-3 py-1.5 text-right font-medium">Turnover</th>
              <th className="px-3 py-1.5 text-right font-medium">%Chg</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 8 }).map((_, i) => (
                  <tr key={i}>
                    <td colSpan={4} className="px-3 py-1">
                      <Skeleton className="h-4 w-full" />
                    </td>
                  </tr>
                ))
              : sectors.map((s) => (
                  <tr
                    key={s.sector}
                    onClick={() => onPick(s.sector)}
                    className="cursor-pointer border-b border-border/50 hover:bg-accent/40"
                  >
                    <td className="px-3 py-1">
                      {s.sector}
                      {!s.official && (
                        <span className="ml-1.5 font-mono text-[10px] text-muted-foreground">
                          weighted
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-1 text-right font-mono tabular-nums text-muted-foreground">
                      {num(s.count, 0)}
                    </td>
                    <td className="px-3 py-1 text-right font-mono tabular-nums text-muted-foreground">
                      {compact(s.turnover)}
                    </td>
                    <td
                      className={cn(
                        "px-3 py-1 text-right font-mono tabular-nums",
                        s.pct >= 0 ? "text-up" : "text-down",
                      )}
                    >
                      {pct(s.pct)}
                    </td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
