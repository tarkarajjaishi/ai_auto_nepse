"use client";

import { BoardPage } from "@/components/board-page";

export default function MasterOperatorPage() {
  return (
    <BoardPage
      board="operator_verdict"
      rebuildHint="python operator_verdict.py"
      priority={["symbol", "verdict", "passes", "side", "broker", "share_in", "share_out", "ratio", "persist_pct", "counterparties", "value_rs"]}
      filters={[
        { label: "Operator engaged", test: (r) => Number(r.passes ?? 0) >= 4 },
        { label: "Likely (3 of 4)", test: (r) => Number(r.passes ?? 0) === 3 },
        { label: "Accumulating", test: (r) => r.side === "BUY" },
        { label: "Distributing", test: (r) => r.side === "SELL" },
        { label: "Everything", test: () => true },
      ]}
      blurb={
        <>
          Four independent tests, each with an innocent explanation on its own; passing{" "}
          <strong>all four at once</strong> does not have one. <strong>1 Concentration</strong> —
          the broker&apos;s share of this stock against its own share of every other stock, so
          &ldquo;it is a big broker&rdquo; cannot explain it. <strong>2 Size</strong> — clips
          against the rest of the tape. <strong>3 Persistence</strong> — the share of sessions it
          was actually on that side. <strong>4 Breadth</strong> — distinct counterparties, which
          excludes a block or a cross. Floorsheet only: no OHLC, no fundamentals.
        </>
      }
    />
  );
}
