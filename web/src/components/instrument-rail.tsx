"use client";

import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useDeferredValue, useMemo, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { api, qk, type HeatSymbol } from "@/lib/api";
import { pct, price } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * The instrument rail: every listed scrip, its last close and today's move.
 *
 * One `/api/heatmap` call feeds it — that endpoint already tail-reads the final bar of every
 * symbol and groups them by sector, which is exactly this list plus the grouping. Reusing it
 * means the rail costs nothing extra and can never disagree with the heatmap page about what a
 * symbol did today.
 *
 * Clicking a row keeps you on the page you are on and swaps the symbol, rather than always
 * jumping to the chart: on Floorsheet you want that symbol's floorsheet. Pages that are not
 * symbol-scoped fall through to the chart.
 */

/** Which pages take a symbol, and under which query key. */
const SYMBOL_PARAM: Record<string, string> = {
  "/admin/chart": "symbol",
  "/admin/floorsheet": "symbol",
};

/** Its own bucket, not a sector — an index is not a scrip you can hold. */
const INDICES = "Indices";

type Row = HeatSymbol & { sector: string };

export function InstrumentRail() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const [query, setQuery] = useState("");
  const [sector, setSector] = useState<string>("All");
  // Typing filters ~300 rows on every keystroke. Deferring keeps the input itself responsive
  // and lets React drop intermediate renders instead of queueing all of them.
  const deferred = useDeferredValue(query);

  const hm = useQuery({
    queryKey: qk.heatmap,
    queryFn: ({ signal }) => api.heatmap(signal),
    staleTime: 60_000,
  });
  const idx = useQuery({
    queryKey: qk.indices,
    queryFn: ({ signal }) => api.indices(signal),
    staleTime: 60_000,
  });

  // Indices first, the way the reference rail leads with its own index bucket. NEPSE is the
  // number you check before any individual scrip means anything.
  const sectors = useMemo(
    () => ["All", INDICES, ...(hm.data?.sectors ?? []).map((s) => s.sector)],
    [hm.data],
  );

  const rows = useMemo(() => {
    const indexRows: Row[] =
      sector === "All" || sector === INDICES
        ? (idx.data?.rows ?? []).map((r) => ({ ...r, symbol: r.index, sector: INDICES }))
        : [];
    const stockRows: Row[] =
      sector === INDICES
        ? []
        : (hm.data?.sectors ?? []).flatMap((s) =>
            sector === "All" || s.sector === sector
              ? s.symbols.map((m) => ({ ...m, sector: s.sector }))
              : [],
          );

    const q = deferred.trim().toUpperCase();
    const keep = (r: Row) => !q || r.symbol.includes(q);
    // Turnover order within each block, not alphabetical: what traded today is what you are
    // looking for. Indices stay pinned above the stocks rather than competing on turnover —
    // NEPSE's turnover is the sum of the market's and would swamp the sort.
    const byTurnover = (a: Row, b: Row) => (b.turnover ?? 0) - (a.turnover ?? 0);
    return [
      ...indexRows.filter(keep).sort(byTurnover),
      ...stockRows.filter(keep).sort(byTurnover),
    ];
  }, [hm.data, idx.data, sector, deferred]);

  const loading = hm.isPending || idx.isPending;

  const activeSymbol = (
    params.get(SYMBOL_PARAM[pathname] ?? "symbol") ??
    (pathname.startsWith("/admin/swing-pro/") ? decodeURIComponent(pathname.split("/").pop()!) : "")
  ).toUpperCase();

  function open(symbol: string, isIndex: boolean) {
    // An index has no floorsheet and no swing_pro row — there is no broker tape for a synthetic
    // series and the framework scores instruments you can actually buy. Send it to the chart,
    // which does serve indices, instead of to a page that would answer 404.
    if (isIndex) {
      router.push(`/admin/chart?symbol=${encodeURIComponent(symbol)}`);
      return;
    }
    const key = SYMBOL_PARAM[pathname];
    if (key) {
      const next = new URLSearchParams(params.toString());
      next.set(key, symbol);
      router.push(`${pathname}?${next}`);
      return;
    }
    if (pathname.startsWith("/admin/swing-pro")) {
      router.push(`/admin/swing-pro/${encodeURIComponent(symbol)}`);
      return;
    }
    router.push(`/admin/chart?symbol=${encodeURIComponent(symbol)}`);
  }

  return (
    <aside className="hidden w-56 shrink-0 flex-col border-r border-border bg-sidebar lg:flex">
      <div className="p-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search"
            className="h-8 w-full rounded-md border border-border bg-background pl-7 pr-2 text-[13px] outline-none placeholder:text-muted-foreground focus:border-primary/60"
          />
        </div>
      </div>

      <div className="flex flex-wrap gap-1 px-2 pb-2">
        {sectors.map((s) => (
          <button
            key={s}
            onClick={() => setSector(s)}
            className={cn(
              "rounded px-1.5 py-0.5 text-[11px] transition-colors",
              s === sector
                ? "bg-primary/15 text-primary"
                : "text-muted-foreground hover:text-foreground",
            )}
            // Sector names run long ("Manufacturing and Processing"); the chip shows the first
            // word, the title shows all of it.
            title={s}
          >
            {s === "All" ? "All" : s.split(" ")[0]}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto border-t border-border">
        {loading
          ? Array.from({ length: 14 }).map((_, i) => (
              <div key={i} className="px-2 py-1.5">
                <Skeleton className="h-7 w-full" />
              </div>
            ))
          : rows.map((r) => (
              <InstrumentRow
                key={`${r.sector}:${r.symbol}`}
                row={r}
                active={r.symbol === activeSymbol}
                onClick={() => open(r.symbol, r.sector === INDICES)}
              />
            ))}
        {!loading && !rows.length && (
          <p className="p-3 text-[12px] text-muted-foreground">
            {hm.isError || idx.isError ? "Instrument list unavailable." : "Nothing matches."}
          </p>
        )}
      </div>

      <div className="border-t border-border px-2.5 py-2">
        <div className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
          {rows.length} scrip · {hm.data?.session ?? "—"}
        </div>
      </div>
    </aside>
  );
}

function InstrumentRow({
  row,
  active,
  onClick,
}: {
  row: Row;
  active: boolean;
  onClick: () => void;
}) {
  const up = (row.pct ?? 0) >= 0;
  const isIndex = row.sector === INDICES;
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-2 border-l-2 px-2.5 py-1.5 text-left transition-colors",
        active
          ? "border-primary bg-accent"
          : "border-transparent hover:bg-accent/50",
      )}
    >
      <div className="min-w-0 flex-1">
        <div
          className={cn(
            "truncate font-mono text-[12px] font-medium",
            isIndex && "text-primary",
          )}
        >
          {row.symbol}
        </div>
        <div className="truncate text-[10px] text-muted-foreground">
          {isIndex ? "index" : row.sector}
        </div>
      </div>
      <div className="shrink-0 text-right">
        <div className="font-mono text-[12px] tabular-nums">{price(row.close)}</div>
        <div className={cn("font-mono text-[10px] tabular-nums", up ? "text-up" : "text-down")}>
          {pct(row.pct)}
        </div>
      </div>
    </button>
  );
}
