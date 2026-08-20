"use client";

import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import Link from "next/link";
import { AlertTriangle, ArrowRight, CircleAlert } from "lucide-react";

import { StatTile } from "@/components/stat-tile";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { api, qk, type BoardName } from "@/lib/api";
import { ALL_NAV_ITEMS } from "@/lib/nav";
import { cn } from "@/lib/utils";

const BOARD_LABEL: Record<string, string> = Object.fromEntries(
  ALL_NAV_ITEMS.filter((i) => i.board).map((i) => [i.board!, i.label]),
);
const BOARD_HREF: Record<string, string> = Object.fromEntries(
  ALL_NAV_ITEMS.filter((i) => i.board).map((i) => [i.board!, i.href]),
);

export default function OverviewPage() {
  const boards = useQuery({
    queryKey: qk.boards,
    queryFn: ({ signal }) => api.boards(signal),
  });
  const swing = useQuery({
    queryKey: qk.board("swing_pro"),
    queryFn: ({ signal }) => api.board("swing_pro", signal),
  });
  const signals = useQuery({
    queryKey: qk.board("master_signal"),
    queryFn: ({ signal }) => api.board("master_signal", signal),
  });

  if (boards.isError) {
    return (
      <div className="p-6">
        <div className="flex items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/10 p-4">
          <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" />
          <div className="text-[13px]">
            <div className="font-medium">The API is not reachable.</div>
            <p className="mt-1 text-muted-foreground">
              {(boards.error as Error).message} Nothing on this terminal is computed in the
              browser, so there is nothing to show until the backend answers.
            </p>
          </div>
        </div>
      </div>
    );
  }

  const rows = swing.data?.rows ?? [];
  const actionable = rows.filter((r) => String(r.decision ?? "").startsWith("BUY")).length;
  const avoid = rows.filter((r) => r.decision === "AVOID").length;
  const buys = (signals.data?.rows ?? []).filter((r) =>
    String(r.verdict ?? "").startsWith("BUY"),
  );
  const behind = Object.entries(boards.data?.boards ?? {}).filter(
    ([, b]) => b.stale || b.session_unknown,
  );

  return (
    <div className="space-y-5 p-4 md:p-6">
      {behind.length > 0 && (
        <motion.div
          initial={{ opacity: 0, y: -6 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex items-start gap-3 rounded-lg border border-primary/40 bg-primary/10 p-3.5"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-primary" />
          <div className="text-[13px]">
            <span className="font-medium">
              {behind.length} board{behind.length > 1 ? "s were" : " was"} computed before the
              bars now on disk.
            </span>{" "}
            <span className="text-muted-foreground">
              The archive is at {boards.data?.archive_session}. Until each is rebuilt its numbers
              are from an earlier session — and before the 15:00 NPT close the newest bar is a
              partial one, so rebuilding mid-day scores an unfinished candle.
            </span>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {behind.map(([name, b]) => (
                <Link key={name} href={BOARD_HREF[name] ?? "/admin"}>
                  <Badge
                    variant="outline"
                    className="border-primary/40 font-mono text-[10px] hover:bg-primary/15"
                  >
                    {BOARD_LABEL[name] ?? name} · {b.session}
                  </Badge>
                </Link>
              ))}
            </div>
          </div>
        </motion.div>
      )}

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          index={0}
          label="Archive"
          value={boards.data?.archive_session ?? "—"}
          note="Newest daily bar on disk"
          loading={boards.isPending}
        />
        <StatTile
          index={1}
          label="Scored"
          value={rows.length || "—"}
          note={`${avoid} score AVOID — most of the market, most days`}
          loading={swing.isPending}
        />
        <StatTile
          index={2}
          label="Actionable"
          value={actionable}
          tone={actionable ? "up" : "default"}
          note="Passes liquidity, 1:2 R:R and the pullback gate"
          loading={swing.isPending}
        />
        <StatTile
          index={3}
          label="Volume signals"
          value={buys.length}
          tone={buys.length ? "primary" : "default"}
          note="Master signal — a different rule with different gates"
          loading={signals.isPending}
        />
      </section>

      {/* The two frameworks disagree by design. Showing them side by side is the honest
          presentation: Swing Trader Pro can say 0 while Master signal says 4, and a reader who
          only sees one will think the other is broken. */}
      <section className="rounded-lg border border-border bg-card">
        <div className="flex items-baseline justify-between border-b border-border px-4 py-3">
          <h2 className="text-[13px] font-semibold tracking-tight">Volume-confirmed candidates</h2>
          <Link
            href="/admin/master-signal"
            className="flex items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
          >
            Master signal <ArrowRight className="size-3" />
          </Link>
        </div>
        {signals.isPending ? (
          <div className="space-y-2 p-4">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : buys.length === 0 ? (
          <p className="p-4 text-[13px] text-muted-foreground">
            Nothing qualifies today. The rule only fires on volume confirmation, and an empty
            list is a normal result rather than a broken scan.
          </p>
        ) : (
          <div className="divide-y divide-border">
            {buys.map((r, i) => (
              <motion.div
                key={String(r.symbol)}
                initial={{ opacity: 0, x: -6 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.05 + i * 0.04, duration: 0.25 }}
                className="grid grid-cols-2 items-center gap-2 px-4 py-2.5 text-[13px] sm:grid-cols-6"
              >
                <div className="font-mono font-semibold">{String(r.symbol)}</div>
                <div className="text-up font-mono text-[11px] font-semibold uppercase">
                  {String(r.verdict)}
                </div>
                <Num label="entry" v={r.entry} />
                <Num label="stop" v={r.stop} tone="down" />
                <Num label="target" v={r.target1} tone="up" />
                <Num label="edge%" v={r.edge_oos} />
              </motion.div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function Num({
  label,
  v,
  tone,
}: {
  label: string;
  v: unknown;
  tone?: "up" | "down";
}) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <span
        className={cn(
          "font-mono tabular-nums",
          tone === "up" && "text-up",
          tone === "down" && "text-down",
        )}
      >
        {typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—"}
      </span>
    </div>
  );
}
