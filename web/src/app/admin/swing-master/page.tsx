"use client";

import { BoardPage } from "@/components/board-page";

export default function SwingMasterPage() {
  return (
    <BoardPage
      board="swing_master"
      rebuildHint="python swing_master.py"
      priority={["symbol", "verdict", "entry", "stop", "target1", "target2", "qty", "cost_rs", "risk_rs", "risk_pct", "rr", "edge_oos", "hold_bars"]}
      emptyMessage="Nothing qualifies. Cash is a position — the rule only fires on volume confirmation."
      blurb={
        <>
          The same rule as Master signal, sized for a book. Quantity is set so a stop-out costs the
          same rupees on every trade — with a 44% base win rate, a fixed loss is the only reliable
          control. <strong>cost_rs</strong> is the position value, and it is shown because risk-based
          sizing fixes the <em>loss</em>, not the <em>position</em>: a 0.23% stop once sized 4.4x
          the whole account while reporting a tidy Rs 1,000 of risk. The size is now capped at the
          cash available. <strong>hold_bars 20</strong> is a real exit — the median NEPSE 20-day
          return is negative, so time in a trade is a cost, not a neutral.
        </>
      }
    />
  );
}
