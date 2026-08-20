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
          Every instrument in the archive, scanned with the settings frozen into{" "}
          <span className="font-mono">scan.py</span> — ALMA wave, structure break, and swing
          confirmation. Those are fixed here; the sliders that tune them live in the Streamlit app.
          A swing is only confirmed a number of bars <em>after</em> it happens, so these are
          historical marks rather than live calls. Note the universe is <em>not</em> filtered:
          debentures and mutual funds appear, and their structure means little because they trade
          on NAV and coupons. Click a row to chart it.
        </>
      }
    />
  );
}
