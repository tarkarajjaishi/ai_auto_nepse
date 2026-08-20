"use client";

import { BoardPage } from "@/components/board-page";

export default function MasterSignalPage() {
  return (
    <BoardPage
      board="master_signal"
      rebuildHint="python master_signal.py"
      priority={["symbol", "verdict", "close", "vol_x", "entry", "stop", "target1", "target2", "rr", "risk_pct", "edge_oos"]}
      emptyMessage="Nothing qualifies today. The rule only fires on volume confirmation, and cash is a position."
      blurb={
        <>
          Volume-confirmed continuation — the one filter that held out of sample across the
          whole replay. The trade count is deliberately not quoted here: it changes with every
          rebuild, and this blurb had drifted to a figure the archive no longer matched. Read the
          sample size off <span className="font-mono">is_n</span> +{" "}
          <span className="font-mono">oos_n</span> on the Backtest page — that page hides{" "}
          <span className="font-mono">trades</span>, which is the same constant on every row.{" "}
          <strong>edge_oos</strong> is the measured out-of-sample average for that
          volume band, read from <span className="font-mono">backtest.txt</span> rather than
          transcribed: the two sheets and the backtest once carried three different values for the
          same &ldquo;measured&rdquo; number. Base rate for context: a random NEPSE 20-day hold
          wins 44% of the time with a <strong>negative median</strong>. This rule won ~58% out of
          sample and still lost money in 7 of the last 13 years.
        </>
      }
    />
  );
}
