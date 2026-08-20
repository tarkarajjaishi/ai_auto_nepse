"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { BoardPage } from "@/components/board-page";
import { api, qk, type Row } from "@/lib/api";
import { cn } from "@/lib/utils";

/** A signal older than this has been on screen long enough that the move is probably gone. */
const FRESH_DEFAULT = 15;

export default function ScannerPage() {
  const router = useRouter();
  return (
    <BoardPage
      board="scan"
      rebuildHint="python scan.py"
      onRowClick={(r) => router.push(`/admin/chart?symbol=${String(r.symbol)}`)}
      priority={[
        "symbol",
        "signal",
        "badge",
        "close",
        "change_pct",
        "stop",
        "target1",
        "target2",
        "risk_pct",
        "badge_age",
        "trend",
        "structure",
      ]}
      filters={[
        { label: "Buy", test: (r) => r.signal === "BUY" },
        { label: "Sell", test: (r) => r.signal === "SELL" },
        { label: "Watch", test: (r) => r.signal === "WATCH" },
        { label: "All", test: () => true },
      ]}
      blurb={
        <>
          Every instrument in the archive, scanned with the settings frozen into{" "}
          <span className="font-mono">scan.py</span> — ALMA wave 21, structure sensitivity 7, swing
          sensitivity 10, the same defaults the Streamlit sidebar opens on. Two columns say
          &ldquo;BUY&rdquo; and they are <em>not</em> the same claim:{" "}
          <span className="font-mono">signal</span> is the verdict — trend and structure both
          pointing one way, otherwise WATCH — while <span className="font-mono">badge</span> is the
          chart&rsquo;s last swing mark, which is always on one side or the other. The stop and
          targets belong to the badge. A swing is only confirmed a number of bars{" "}
          <em>after</em> it happens, so these are historical marks rather than live calls. The
          universe is <em>not</em> filtered: debentures and mutual funds appear, and their
          structure means little because they trade on NAV and coupons. Click a row to chart it.
        </>
      }
    >
      <StrongSignals onPick={(s) => router.push(`/admin/chart?symbol=${encodeURIComponent(s)}`)} />
    </BoardPage>
  );
}

/**
 * The shortlist: the handful where every part of the indicator agrees and the trade is still
 * worth taking. Streamlit puts this under the full table; here it sits above it, because a
 * 340-row dump is the reference and this is the answer.
 *
 * "Strong" is three agreements plus two sanity checks, exactly as `conviction()` has it:
 *
 *   signal === side   the verdict, which already means trend AND structure agree
 *   badge  === side   the chart's swing mark pointing the same way
 *   badge_age <= n    still fresh — an old mark is a move that has already happened
 *   1.5 <= risk <= 20 the stop is far enough away to clear costs, near enough to be a trade
 *
 * `signal` is not redundant with trend/structure: scan.py computes it as exactly that
 * conjunction, so testing it is the same test with one comparison instead of three.
 */
function StrongSignals({ onPick }: { onPick: (symbol: string) => void }) {
  const [fresh, setFresh] = useState(FRESH_DEFAULT);
  const q = useQuery({ queryKey: qk.board("scan"), queryFn: ({ signal }) => api.board("scan", signal) });

  const { buys, sells } = useMemo(() => {
    const rows = q.data?.rows ?? [];
    const pick = (side: "BUY" | "SELL") =>
      rows
        .filter((r) => {
          const age = num(r.badge_age);
          const risk = num(r.risk_pct);
          return (
            r.signal === side &&
            r.badge === side &&
            age !== null &&
            age <= fresh &&
            risk !== null &&
            risk >= 1.5 &&
            risk <= 20
          );
        })
        // freshest first, then by the size of the day's move
        .sort(
          (a, b) =>
            (num(a.badge_age) ?? 0) - (num(b.badge_age) ?? 0) ||
            Math.abs(num(b.change_pct) ?? 0) - Math.abs(num(a.change_pct) ?? 0),
        );
    return { buys: pick("BUY"), sells: pick("SELL") };
  }, [q.data, fresh]);

  if (q.isPending || !q.data) return null;

  // A board written before scan.py grew these columns has no `badge` at all. Filtering on a
  // field that is not there yields zero matches, and zero matches renders as "nothing
  // qualifies" — a confident statement about the market made from a missing column. Say what is
  // actually true instead.
  if (!q.data.columns.includes("badge")) {
    return (
      <section className="rounded-lg border border-primary/40 bg-primary/10 px-4 py-3 text-[13px]">
        <p className="font-medium">Strong signals need a rebuilt board.</p>
        <p className="mt-1 text-muted-foreground">
          This <span className="font-mono">scan.txt</span> was written before the badge and its
          trade levels existed, so the shortlist cannot be computed — that is not the same as
          nothing qualifying. Run <span className="font-mono">python scan.py</span> to rebuild it.
        </p>
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border px-4 py-2.5">
        <h3 className="text-[13px] font-semibold tracking-tight">Strong signals</h3>
        <label className="flex items-center gap-2 text-[12px] text-muted-foreground">
          no older than
          <input
            type="range"
            min={3}
            max={40}
            value={fresh}
            onChange={(e) => setFresh(Number(e.target.value))}
            className="h-1 w-28 cursor-pointer accent-primary"
            aria-label="Maximum signal age in bars"
          />
          <span className="w-14 font-mono tabular-nums text-foreground">{fresh} bars</span>
        </label>
        <p className="basis-full text-[12px] leading-snug text-muted-foreground">
          All three parts agree — the verdict, the chart&rsquo;s swing badge, and price on the
          right side of the wave — and the stop is 1.5–20% away, far enough to clear costs.
        </p>
      </div>

      <div className="grid gap-px bg-border md:grid-cols-2">
        <Side title="Strong buy" tone="up" rows={buys} onPick={onPick} />
        <Side title="Strong sell" tone="down" rows={sells} onPick={onPick} />
      </div>
    </section>
  );
}

function Side({
  title,
  tone,
  rows,
  onPick,
}: {
  title: string;
  tone: "up" | "down";
  rows: Row[];
  onPick: (symbol: string) => void;
}) {
  return (
    <div className="bg-card">
      <div className="flex items-baseline gap-2 px-4 py-2">
        <span className={cn("text-[12px] font-semibold", tone === "up" ? "text-up" : "text-down")}>
          {title}
        </span>
        <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
          {rows.length}
        </span>
      </div>
      {rows.length === 0 ? (
        <p className="px-4 pb-3 text-[12px] text-muted-foreground">
          Nothing qualifies right now. That is the normal answer — most days most of the market is
          not a trade.
        </p>
      ) : (
        <div className="max-h-[260px] overflow-auto">
          <table className="w-full text-[12px]">
            <thead className="sticky top-0 bg-card">
              <tr className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                <th className="px-4 py-1.5 text-left font-medium">Symbol</th>
                <th className="px-2 py-1.5 text-right font-medium">Close</th>
                <th className="px-2 py-1.5 text-right font-medium">Stop</th>
                <th className="px-2 py-1.5 text-right font-medium">T1</th>
                <th className="px-2 py-1.5 text-right font-medium">T2</th>
                <th className="px-2 py-1.5 text-right font-medium">Risk</th>
                <th className="px-4 py-1.5 text-right font-medium">Age</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={String(r.symbol)}
                  onClick={() => onPick(String(r.symbol))}
                  className="cursor-pointer border-t border-border/50 hover:bg-accent"
                >
                  <td className="px-4 py-1.5 font-medium">{String(r.symbol)}</td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums">{fmt(r.close)}</td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums text-down">
                    {fmt(r.stop)}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums text-up">
                    {fmt(r.target1)}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums text-up">
                    {fmt(r.target2)}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums">
                    {fmt(r.risk_pct)}%
                  </td>
                  <td className="px-4 py-1.5 text-right font-mono tabular-nums text-muted-foreground">
                    {fmt(r.badge_age)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function fmt(v: unknown): string {
  const n = num(v);
  return n === null ? "—" : n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}
