"use client";

import { useQuery } from "@tanstack/react-query";
import { CircleAlert } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import { PriceChart } from "@/components/price-chart";
import { StatTile } from "@/components/stat-tile";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api, qk } from "@/lib/api";
import { pct, price } from "@/lib/format";

function ChartInner() {
  const params = useSearchParams();
  const router = useRouter();
  const symbol = (params.get("symbol") ?? "NABIL").toUpperCase();
  const [draft, setDraft] = useState(symbol);

  const symbols = useQuery({ queryKey: qk.symbols, queryFn: ({ signal }) => api.symbols(signal) });
  const bars = useQuery({
    queryKey: qk.bars(symbol, 500),
    queryFn: ({ signal }) => api.bars(symbol, 500, signal),
  });

  const last = bars.data?.bars.at(-1);
  const prev = bars.data?.bars.at(-2);
  const change = last && prev ? ((last.close / prev.close - 1) * 100) : null;

  // Indices are navigable here too, so they have to pass the guard that stops a typo navigating
  // to a 404. Without this the rail can link to NEPSE and the input refuses to.
  const known = useMemo(
    () => new Set([...(symbols.data?.symbols ?? []), ...(symbols.data?.indices ?? [])]),
    [symbols.data],
  );

  function go(next: string) {
    const s = next.trim().toUpperCase();
    if (s && known.has(s)) router.push(`/admin/chart?symbol=${encodeURIComponent(s)}`);
  }

  return (
    <div className="space-y-4 p-4 md:p-6">
      <div className="flex flex-wrap items-center gap-3">
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && go(draft)}
          onBlur={() => go(draft)}
          list="symbols"
          className="h-8 w-40 font-mono text-[13px] uppercase"
          placeholder="Symbol"
        />
        <datalist id="symbols">
          {[...(symbols.data?.indices ?? []), ...(symbols.data?.symbols ?? [])].map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
        {bars.data && (
          <span className="font-mono text-[11px] text-muted-foreground">
            {bars.data.bars.length} bars ·{" "}
            {bars.data.kind === "index" ? (
              <span className="text-primary">index · unadjusted</span>
            ) : (
              "corporate-action adjusted"
            )}
          </span>
        )}
      </div>

      {bars.isError ? (
        <div className="flex items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-[13px]">
          <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" />
          <div className="text-muted-foreground">{(bars.error as Error).message}</div>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <StatTile index={0} label="Last" value={price(last?.close)} loading={bars.isPending} />
            <StatTile
              index={1}
              label="Change"
              value={pct(change)}
              tone={change == null ? "default" : change >= 0 ? "up" : "down"}
              loading={bars.isPending}
            />
            <StatTile index={2} label="High" value={price(last?.high)} loading={bars.isPending} />
            <StatTile index={3} label="Low" value={price(last?.low)} loading={bars.isPending} />
            <StatTile
              index={4}
              label="Session"
              value={<span className="text-base">{last?.date ?? "—"}</span>}
              loading={bars.isPending}
            />
          </div>

          <div className="rounded-lg border border-border bg-card p-2">
            {bars.isPending ? (
              <Skeleton className="h-[420px] w-full" />
            ) : (
              <PriceChart bars={bars.data?.bars ?? []} />
            )}
          </div>
        </>
      )}

      {bars.data?.kind === "index" && (
        <p className="max-w-4xl text-[13px] leading-relaxed text-muted-foreground">
          An index is shown <strong>unadjusted, and always will be</strong>. It has no corporate
          actions — it is a continuous series by construction — so running one through the
          adjuster could not correct anything, only invent a split factor out of an ordinary large
          move. Volume on an index is the constituent total the exchange publishes, not a traded
          quantity.
        </p>
      )}

      <p className="max-w-4xl text-[13px] leading-relaxed text-muted-foreground">
        Prices are <strong>corporate-action adjusted on read</strong> — a bonus or rights ex-date
        prints a gap that never traded, and leaving it in invents a swing low out of arithmetic.
        The detector identifies one by <em>where</em> the fall sits (already in the open, and it
        stays) rather than by how large it is, because NEPSE&apos;s circuit is ±15% and an ordinary
        limit-down is not a corporate action.
      </p>
    </div>
  );
}

export default function ChartPage() {
  return (
    <Suspense fallback={<div className="p-6"><Skeleton className="h-[520px] w-full" /></div>}>
      <ChartInner />
    </Suspense>
  );
}
