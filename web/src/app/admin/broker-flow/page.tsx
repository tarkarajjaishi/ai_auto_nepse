"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import { BrokerFlowPanel } from "@/components/broker-flow-panel";
import { Input } from "@/components/ui/input";
import { api, qk } from "@/lib/api";

/**
 * Broker flow — who has been accumulating or distributing a symbol over a window of sessions.
 *
 * Streamlit carries this as its own page, so it is one here too. The panel itself is shared with
 * the Floorsheet page rather than copied: the same numbers rendered by two components is two
 * behaviours waiting to drift.
 */
function BrokerFlowInner() {
  const params = useSearchParams();
  const router = useRouter();
  const symbol = (params.get("symbol") ?? "NABIL").toUpperCase();

  // Re-seed from the URL when the rail changes the symbol — same reason as the chart and
  // floorsheet pages: otherwise the input keeps the old symbol while the panel shows the new one.
  const [draft, setDraft] = useState(symbol);
  const [seenSymbol, setSeenSymbol] = useState(symbol);
  if (symbol !== seenSymbol) {
    setSeenSymbol(symbol);
    setDraft(symbol);
  }

  const symbols = useQuery({ queryKey: qk.symbols, queryFn: ({ signal }) => api.symbols(signal) });
  const known = useMemo(() => new Set(symbols.data?.symbols ?? []), [symbols.data]);

  function go(next: string) {
    const s = next.trim().toUpperCase();
    if (!s || !known.has(s)) return;
    router.push(`/admin/broker-flow?symbol=${encodeURIComponent(s)}`);
  }

  return (
    <div className="space-y-4 p-4 md:p-6">
      <div className="flex flex-wrap items-center gap-3">
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && go(draft)}
          onBlur={() => go(draft)}
          list="bf-symbols"
          className="h-8 w-40 font-mono text-[13px] uppercase"
          placeholder="Symbol"
        />
        <datalist id="bf-symbols">
          {(symbols.data?.symbols ?? []).map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
        <span className="font-mono text-[13px] text-muted-foreground">{symbol}</span>
      </div>

      <BrokerFlowPanel symbol={symbol} layoutId="bf-page-window" />
    </div>
  );
}

export default function BrokerFlowPage() {
  return (
    <Suspense fallback={<div className="p-6 text-[13px] text-muted-foreground">Loading…</div>}>
      <BrokerFlowInner />
    </Suspense>
  );
}
