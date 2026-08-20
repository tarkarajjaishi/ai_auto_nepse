"use client";

import { BoardPage } from "@/components/board-page";

export default function BacktestPage() {
  return (
    <BoardPage
      board="backtest"
      rebuildHint="python backtest.py"
      priority={["variant", "oos_avg", "oos_win", "oos_n", "is_avg", "is_win", "is_n"]}
      hide={["trades", "split", "from", "to", "cost_pct", "max_hold"]}
      blurb={
        <>
          Every <span className="font-mono">trade_setup</span> buy trigger replayed across the whole
          archive, split in half. <strong>Read the oos_avg column and ignore the rest.</strong> A
          variant that looks best in-sample and collapses out of it was fitted to the past — ADX and
          breakout both do exactly that. Entry is the <em>next</em> bar&apos;s open, the stop wins
          ties, and a round-trip cost is charged. The float-turn variants are ranked{" "}
          <em>within each day</em>, because the archive has no point-in-time free float and an
          absolute threshold made them a filter on the calendar rather than on the stock.
        </>
      }
    />
  );
}
