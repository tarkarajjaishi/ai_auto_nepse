"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, LineChart, Search } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useDeferredValue, useMemo, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { api, qk, type HeatSymbol } from "@/lib/api";
import { pct, price } from "@/lib/format";
import { useRail } from "@/store/rail";
import { cn } from "@/lib/utils";

/**
 * The instrument rail, two levels deep: indices first, then the scrips inside one.
 *
 * A flat list of 295 symbols is a scroll, not a navigation — you cannot see the market in it.
 * The sector indices ARE the market's own summary, so the top level is the fourteen of them and
 * clicking one opens the constituents of that sector. Search cuts across both levels, because
 * when you know the symbol you should not have to know its sector first.
 *
 * Both levels come from /api/heatmap and /api/indices, which already tail-read the last bar of
 * every symbol and group them by sector — the same numbers the heatmap page draws, so the rail
 * cannot disagree with it about what anything did today.
 */

/** Which pages take a symbol, and under which query key. */
const SYMBOL_PARAM: Record<string, string> = {
  "/admin/chart": "symbol",
  "/admin/floorsheet": "symbol",
  "/admin/broker-flow": "symbol",
};

/**
 * The rail only exists on screens where picking an instrument means something.
 *
 * The reference does the same and it is worth copying: its rail is on Terminal and nowhere else
 * — Portfolio, Tools and Wallet are full-width. A permanent rail beside a backtest table or the
 * pipeline page is 180px of decoration that pushes the actual content sideways.
 */
const SYMBOL_PAGES = [
  "/admin/chart",
  "/admin/floorsheet",
  "/admin/broker-flow",
  "/admin/swing-pro",
];

type Row = HeatSymbol & { sector: string };

/**
 * The rail exists on four routes; the other thirteen render nothing at all.
 *
 * That decision is made here, from `usePathname` alone, BEFORE anything touches
 * `useSearchParams`. usePathname does not force a client bailout, so the routes without a
 * rail never mount the suspended body and never paint its fallback. Previously the layout
 * suspended the whole rail, so all seventeen admin routes painted a 224px placeholder and
 * thirteen of them then collapsed it — a layout shift on every one of those pages.
 */
export function InstrumentRail() {
  const pathname = usePathname();
  if (!SYMBOL_PAGES.some((p) => pathname.startsWith(p))) return null;
  return (
    <Suspense fallback={<div className="hidden w-56 shrink-0 border-r border-border bg-sidebar lg:block" />}>
      <RailInner />
    </Suspense>
  );
}

function RailInner() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const [query, setQuery] = useState("");
  // Outside the component: this boundary remounts on navigation — see store/rail.ts.
  const drill = useRail((s) => s.drill);
  const setDrill = useRail((s) => s.setDrill);
  // Typing filters ~300 rows per keystroke. Deferring keeps the input responsive and lets React
  // drop intermediate renders instead of queueing all of them.
  const deferred = useDeferredValue(query);
  const searching = deferred.trim().length > 0;

  const wanted = SYMBOL_PAGES.some((p) => pathname.startsWith(p));

  const hm = useQuery({
    queryKey: qk.heatmap,
    queryFn: ({ signal }) => api.heatmap(signal),
    staleTime: 60_000,
    enabled: wanted,
  });
  const idx = useQuery({
    queryKey: qk.indices,
    queryFn: ({ signal }) => api.indices(signal),
    staleTime: 60_000,
    enabled: wanted,
  });

  /** index ticker -> the sector it heads. NEPSE heads nothing; it is the whole market. */
  const sectorOfIndex = useMemo(() => {
    const m: Record<string, string> = {};
    for (const s of hm.data?.sectors ?? []) if (s.index) m[s.index] = s.sector;
    return m;
  }, [hm.data]);

  const allSymbols = useMemo<Row[]>(
    () =>
      (hm.data?.sectors ?? []).flatMap((s) =>
        s.symbols.map((m) => ({ ...m, sector: s.sector })),
      ),
    [hm.data],
  );

  const openSector = drill ? sectorOfIndex[drill] : null;

  const rows = useMemo<Row[]>(() => {
    const byTurnover = (a: Row, b: Row) => (b.turnover ?? 0) - (a.turnover ?? 0);

    // Search cuts across everything — indices included — and ignores which level you are on.
    if (searching) {
      const q = deferred.trim().toUpperCase();
      const indexHits: Row[] = (idx.data?.rows ?? [])
        .filter((r) => r.index.includes(q))
        .map((r) => ({ ...r, symbol: r.index, sector: "index" }));
      return [...indexHits, ...allSymbols.filter((r) => r.symbol.includes(q)).sort(byTurnover)];
    }

    if (openSector) {
      return allSymbols.filter((r) => r.sector === openSector).sort(byTurnover);
    }

    // Top level: the indices themselves. NEPSE first — it is the number that decides whether any
    // sector's move means anything — then the rest by turnover.
    return (idx.data?.rows ?? [])
      .map<Row>((r) => ({ ...r, symbol: r.index, sector: "index" }))
      .sort((a, b) =>
        a.symbol === "NEPSE" ? -1 : b.symbol === "NEPSE" ? 1 : (b.turnover ?? 0) - (a.turnover ?? 0),
      );
  }, [searching, deferred, openSector, allSymbols, idx.data]);

  // Live prices for the rows on screen. `rows` is already the visible set — fourteen indices at
  // the top level, one sector's scrips when drilled in — so this stays small whatever is open.
  // A sector can hold 110 scrips — Hydro Power does — and the API takes up to 400, so the
  // whole drilled-in list ticks rather than its first screenful.
  const visible = useMemo(() => rows.slice(0, 300).map((r) => r.symbol), [rows]);
  const live = useQuery({
    queryKey: ["quotes", visible.join(",")],
    queryFn: ({ signal }) => api.quotes(visible, signal),
    enabled: wanted && visible.length > 0,
    refetchInterval: 500,
    retry: false,
  });
  const quotes = live.data?.fresh ? live.data.quotes : undefined;

  const activeSymbol = (
    params.get(SYMBOL_PARAM[pathname] ?? "symbol") ??
    (pathname.startsWith("/admin/swing-pro/") ? decodeURIComponent(pathname.split("/").pop()!) : "")
  ).toUpperCase();

  const loading = hm.isPending || idx.isPending;

  function openChart(symbol: string) {
    router.push(`/admin/chart?symbol=${encodeURIComponent(symbol)}`);
  }

  function openSymbol(symbol: string) {
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
    openChart(symbol);
  }

  function onRow(r: Row) {
    if (r.sector !== "index") return openSymbol(r.symbol);
    // An index that heads a sector drills in. NEPSE heads no sector, so it can only be charted.
    const sec = sectorOfIndex[r.symbol];
    if (sec && !searching) {
      setDrill(r.symbol);
      return;
    }
    openChart(r.symbol);
  }

  if (!wanted) return null;

  return (
    // 224px and bg-background — both measured off the reference rail.
    <aside className="hidden w-56 shrink-0 flex-col border-r border-border bg-background lg:flex">
      <div className="p-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search all"
            className="h-[34px] w-full rounded-md border border-border bg-background pl-8 pr-2.5 text-sm outline-none placeholder:text-muted-foreground focus:border-primary/60"
          />
        </div>
      </div>

      {/* level indicator / back */}
      <div className="flex items-center gap-1 border-b border-border px-2 pb-2">
        {searching ? (
          <span className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
            {rows.length} match{rows.length === 1 ? "" : "es"}
          </span>
        ) : openSector ? (
          <>
            <button
              onClick={() => setDrill(null)}
              className="flex items-center gap-0.5 rounded px-1 py-0.5 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
            >
              <ChevronLeft className="size-3" />
              Indices
            </button>
            <span className="truncate text-[11px] text-foreground">{openSector}</span>
            <button
              onClick={() => openChart(drill!)}
              title={`Chart ${drill}`}
              className="ml-auto rounded p-0.5 text-muted-foreground transition-colors hover:text-primary"
            >
              <LineChart className="size-3.5" />
            </button>
          </>
        ) : (
          <span className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
            Indices
          </span>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading
          ? Array.from({ length: 14 }).map((_, i) => (
              <div key={i} className="px-2 py-1.5">
                <Skeleton className="h-7 w-full" />
              </div>
            ))
          : rows.map((r) => (
              <RailRow
                key={`${r.sector}:${r.symbol}`}
                row={r}
                live={quotes?.[r.symbol]}
                active={r.symbol === activeSymbol}
                drillable={r.sector === "index" && !searching && Boolean(sectorOfIndex[r.symbol])}
                onClick={() => onRow(r)}
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
          {openSector || searching ? `${rows.length} scrip` : `${rows.length} indices`} ·{" "}
          {hm.data?.session ?? idx.data?.session ?? "—"}
        </div>
      </div>
    </aside>
  );
}

function RailRow({
  row,
  live,
  active,
  drillable,
  onClick,
}: {
  row: Row;
  /** the live quote for this instrument, when the socket has one */
  live?: { ltp: number; close: number | null; pct: number | null };
  active: boolean;
  drillable: boolean;
  onClick: () => void;
}) {
  // The live quote wins when there is one; otherwise the stored bar, unchanged.
  const close = live?.ltp ?? row.close;
  const change = live?.pct ?? row.pct;
  const up = (change ?? 0) >= 0;
  const isIndex = row.sector === "index";
  return (
    // 48px rows divided by a rule, which is how theirs are built — not a left accent bar. The
    // active row keeps a brass left edge because unlike theirs, this rail marks the symbol the
    // page is actually showing.
    <button
      onClick={onClick}
      className={cn(
        "flex h-12 w-full items-center gap-1.5 border-b border-border/50 border-l-2 px-3 text-left transition-colors",
        active ? "border-l-primary bg-accent/60" : "border-l-transparent hover:bg-accent/40",
      )}
    >
      <div className="min-w-0 flex-1">
        <div className={cn("truncate text-xs font-semibold", isIndex && "text-primary")}>
          {row.symbol}
        </div>
        <div className="truncate text-[11px] text-muted-foreground">
          {isIndex ? "index" : row.sector}
        </div>
      </div>
      <div className="shrink-0 text-right">
        <div className="font-mono text-xs tabular-nums">{price(close)}</div>
        <div className={cn("font-mono text-[11px] tabular-nums", up ? "text-up" : "text-down")}>
          {pct(change)}
        </div>
      </div>
      {drillable && <ChevronRight className="size-3 shrink-0 text-muted-foreground" />}
    </button>
  );
}
