"use client";

import { useQuery } from "@tanstack/react-query";
import { CircleAlert } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo } from "react";

import { BoardPage } from "@/components/board-page";
import { api, qk, type Row } from "@/lib/api";
import { cn } from "@/lib/utils";

const TOP = 20;

export default function MasterOperatorPage() {
  const router = useRouter();
  const go = (s: string) => router.push(`/admin/chart?symbol=${encodeURIComponent(s)}`);
  return (
    <BoardPage
      board="operator_verdict"
      rebuildHint="python operator_verdict.py"
      onRowClick={(r) => go(String(r.symbol))}
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
    >
      <WhatItMeans />
      <GripScan onPick={go} />
    </BoardPage>
  );
}

/**
 * The two things the verdict does NOT say.
 *
 * Not decoration, and not optional. The tables below rank names by how concentrated their order
 * flow is, and a screen that ranks without stating its ceiling reads as an accusation. The
 * ceiling here is a missing column, not a weak signal: NEPSE's floorsheet publishes broker IDs
 * and never client IDs, so no amount of arithmetic on this dataset can tell one operator from
 * four hundred retail clients who share a broker.
 */
function WhatItMeans() {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <div className="rounded-lg border border-border bg-card p-4 text-[13px]">
        <h3 className="text-[13px] font-semibold">What the verdict means</h3>
        <p className="mt-1.5 text-muted-foreground">
          One decision-maker is behind the flow. Hundreds of independent retail clients cannot
          produce a 35× concentration in a single broker while trading many times the market&apos;s
          block rate.
        </p>
        <h3 className="mt-3 text-[13px] font-semibold">What it does not mean</h3>
        <p className="mt-1.5 text-muted-foreground">
          Anything about motive or direction. A manipulator, a fund building a position and a prop
          desk are <em>identical</em> here, and none of it predicts price — every predictive
          version was tested and died.
        </p>
      </div>

      <div className="rounded-lg border border-down/40 bg-down/10 p-4 text-[13px]">
        <div className="flex items-center gap-2">
          <CircleAlert className="size-4 shrink-0 text-down" />
          <h3 className="text-[13px] font-semibold">The hard limit, and it does not close</h3>
        </div>
        <p className="mt-1.5 text-muted-foreground">
          The floorsheet publishes <strong className="text-foreground">broker IDs, never client
          IDs</strong>. A broker is a <em>pipe</em>: 44% of EBL through broker 92 could be one
          operator, or four hundred unrelated retail clients who happen to share a broker. No
          calculation on this dataset separates those two — it is a missing column, not a weak
          signal.
        </p>
        <p className="mt-2 text-muted-foreground">
          So the strongest honest claim is{" "}
          <strong className="text-foreground">&ldquo;unusually concentrated order flow&rdquo;</strong>
          , not &ldquo;an operator is accumulating&rdquo;.
        </p>
      </div>
    </div>
  );
}

/**
 * The live grip scan — `operator_now`, which until now was served by the API, typed on the
 * client and reachable from nowhere.
 *
 * Read `regular` first: it answers *are they buying every day, or was it one block?* 90% is a
 * daily campaign; 25% is one big trade with a quiet fortnight around it. `grip20 → grip3` is the
 * same broker's share of volume over shrinking windows, so rising left-to-right means they are
 * taking more lately.
 */
function GripScan({ onPick }: { onPick: (symbol: string) => void }) {
  const q = useQuery({
    queryKey: qk.board("operator_now"),
    queryFn: ({ signal }) => api.board("operator_now", signal),
  });

  const { buy, sell } = useMemo(() => {
    const rows = q.data?.rows ?? [];
    const side = (want: string) =>
      rows
        .filter((r) => r.side === want)
        .sort((a, b) => (num(b.score) ?? 0) - (num(a.score) ?? 0))
        .slice(0, TOP);
    return { buy: side("BUY"), sell: side("SELL") };
  }, [q.data]);

  if (q.isPending || !q.data || !q.data.rows.length) return null;

  return (
    <section className="rounded-lg border border-border bg-card">
      <div className="border-b border-border px-4 py-2.5">
        <h3 className="text-[13px] font-semibold tracking-tight">
          Grip scan · today&apos;s dominant brokers
        </h3>
        <p className="mt-1 text-[12px] leading-snug text-muted-foreground">
          Read <span className="font-mono">regular</span> first — the share of the last 20 sessions
          that broker was actually net on that side. <strong>90% is a daily campaign; 25% is one
          big trade</strong> with a quiet fortnight around it. Each side is z-scored against its
          own cross-section on the same date, because a 40% buy-grip and a 40% sell-grip are not
          comparable. Stale names and leaders facing fewer than 5 counterparties are filtered —
          promoter shares and debentures trade as single negotiated blocks and would otherwise
          read as 100% grip.
        </p>
      </div>

      <div className="grid gap-px bg-border xl:grid-cols-2">
        <GripTable
          title="Accumulating"
          note="One hand taking supply off a crowd of sellers."
          tone="up"
          rows={buy}
          onPick={onPick}
        />
        <GripTable
          title="Distributing"
          note="The mirror image — one hand feeding stock out to many buyers. A symbol can appear on both lists, which just means two large desks are on opposite sides of it."
          tone="down"
          rows={sell}
          onPick={onPick}
        />
      </div>
    </section>
  );
}

const COLS: { key: string; label: string; title: string }[] = [
  { key: "score", label: "score", title: "cross-sectional z of grip + tightening + persistence, scaled by regularity — ranked against every other stock the same day" },
  { key: "broker", label: "broker", title: "NEPSE member number" },
  { key: "regular", label: "regular %", title: "% of the last 20 sessions this broker was net on this side. High = a persistent daily campaign, low = one-off blocks." },
  { key: "grip20", label: "grip20", title: "share of volume over the last 20 sessions" },
  { key: "grip15", label: "grip15", title: "share of volume over the last 15 sessions" },
  { key: "grip7", label: "grip7", title: "share of volume over the last 7 sessions" },
  { key: "grip3", label: "grip3", title: "share of volume over the last 3 sessions" },
  { key: "tighten", label: "tighten", title: "3d grip minus 20d grip; positive = tightening recently" },
  { key: "persist", label: "persist", title: "distinct leaders across the 4 windows; 1 = the same broker throughout" },
  { key: "counterparties", label: "ctp", title: "distinct brokers on the other side; under 5 is a block and is filtered" },
];

function GripTable({
  title,
  note,
  tone,
  rows,
  onPick,
}: {
  title: string;
  note: string;
  tone: "up" | "down";
  rows: Row[];
  onPick: (symbol: string) => void;
}) {
  return (
    <div className="min-w-0 bg-card">
      <div className="px-4 pb-1.5 pt-3">
        <span className={cn("text-[12px] font-semibold", tone === "up" ? "text-up" : "text-down")}>
          {title}
        </span>
        <p className="mt-0.5 text-[11.5px] leading-snug text-muted-foreground">{note}</p>
      </div>
      <div className="max-h-[340px] overflow-auto">
        <table className="w-full text-[12px]">
          <thead className="sticky top-0 bg-card">
            <tr className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              <th className="px-4 py-1.5 text-left font-medium">Symbol</th>
              {COLS.map((c) => (
                <th key={c.key} title={c.title} className="px-2 py-1.5 text-right font-medium">
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr
                key={`${String(r.symbol)}-${i}`}
                onClick={() => onPick(String(r.symbol))}
                className="cursor-pointer border-t border-border/50 hover:bg-accent"
              >
                <td className="px-4 py-1.5 font-medium">{String(r.symbol)}</td>
                {COLS.map((c) => (
                  <td key={c.key} className="px-2 py-1.5 text-right font-mono tabular-nums">
                    {fmt(r[c.key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function fmt(v: unknown): string {
  const n = num(v);
  if (n !== null) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return v === null || v === undefined || v === "" ? "—" : String(v);
}
