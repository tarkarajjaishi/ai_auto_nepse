"use client";

import { BoardPage } from "@/components/board-page";

export default function OperatorRadarPage() {
  return (
    <BoardPage
      board="operator_scan"
      rebuildHint="python operator_scan.py"
      priority={["symbol", "side", "broker", "score", "regular", "net_1m", "net_3w", "net_3d", "pct_1m", "pub_shares"]}
      emptyMessage="No symbol currently passes the cascade."
      blurb={
        <>
          One broker <strong>regularly</strong> buying more than they sell in a low-float stock,
          confirmed across three windows — <strong>1 month → 3 weeks → last 3 days</strong> — rather
          than by a single total. A 60-day total is misleading on its own: one name was +103,838
          over a month but only +2,622 in the last three days, so the buying had already died. The
          3-day leg is what proves it is still happening. This describes what a broker did; it does
          not predict price, and every predictive version of it was tested and died.
        </>
      }
    />
  );
}
