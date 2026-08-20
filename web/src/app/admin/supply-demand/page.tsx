"use client";

import { useRouter } from "next/navigation";

import { BoardPage } from "@/components/board-page";

export default function SupplyDemandPage() {
  const router = useRouter();
  return (
    <BoardPage
      board="supply_demand"
      rebuildHint="python supply_demand.py"
      onRowClick={(r) => router.push(`/admin/chart?symbol=${String(r.symbol)}`)}
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
          vendor: across 7,700 replayed trades, volume confirmation on a confirmed uptrend was the
          single ingredient that kept its edge out of sample. Read the rest as{" "}
          <em>where the levels are</em>, not as a tested edge.
        </>
      }
    />
  );
}
