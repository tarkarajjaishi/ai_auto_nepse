"use client";

import { useRouter } from "next/navigation";

import { BoardPage } from "@/components/board-page";

export default function ScannerPage() {
  const router = useRouter();
  return (
    <BoardPage
      board="scan"
      rebuildHint="python scan.py"
      onRowClick={(r) => router.push(`/admin/chart?symbol=${String(r.symbol)}`)}
      priority={["symbol", "signal", "close", "change_pct", "stop", "target1", "target2", "risk_pct", "trend", "structure"]}
      filters={[
        { label: "Buy", test: (r) => r.signal === "BUY" },
        { label: "Sell", test: (r) => r.signal === "SELL" },
        { label: "Watch", test: (r) => r.signal === "WATCH" },
        { label: "All", test: () => true },
      ]}
      blurb={
        <>
          Every stock scanned with your current personal-indicator settings — ALMA wave, structure
          break, and swing confirmation. A swing is only confirmed a number of bars <em>after</em>
          it happens, so these are historical marks rather than live calls. Mutual funds and
          debentures are excluded: they trade on NAV and coupons, not on the structure this reads.
          Click a row to chart it.
        </>
      }
    />
  );
}
