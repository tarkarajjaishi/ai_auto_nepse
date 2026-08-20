"use client";

import { useRouter } from "next/navigation";

import { BoardPage } from "@/components/board-page";

export default function SwingProPage() {
  const router = useRouter();
  return (
    <BoardPage
      board="swing_pro"
      rebuildHint="python swing_pro.py"
      onRowClick={(r) => router.push(`/admin/swing-pro/${String(r.symbol)}`)}
      priority={[
        "symbol", "decision", "grade", "score", "entry", "stop", "target1", "rr", "risk_pct",
        "setup", "performer",
      ]}
      emptyMessage="Nothing in this bucket today. An empty Actionable list is a normal result for this framework, not a broken scan."
      filters={[
        { label: "Actionable", test: (r) => String(r.decision ?? "").startsWith("BUY") },
        { label: "A + B grade", test: (r) => /^(A\+|A |B)/.test(String(r.grade ?? "")) },
        { label: "Watch", test: (r) => r.decision === "WAIT" },
        { label: "Everything", test: () => true },
      ]}
      blurb={
        <>
          The full professional swing checklist, run as <strong>deterministic Python over the
          daily archive</strong> — the same numbers every time, every point traceable to a stated
          rule rather than an opinion. <strong>Daily bars only</strong>, by design. Three gates
          veto a setup however good the chart looks — liquidity, <strong>risk/reward below
          1:2</strong>, and a dangerous pullback — which is why most of the market scores AVOID on
          any given day. Click a row for its 22-section report. Nothing here is backtested on
          NEPSE; it is a disciplined framework, not a proven edge.
        </>
      }
    />
  );
}
