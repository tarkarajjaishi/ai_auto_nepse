"use client";

import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { AlertTriangle, CircleAlert, Inbox } from "lucide-react";
import { useMemo, useState } from "react";

import { BoardTable } from "@/components/board-table";
import { Skeleton } from "@/components/ui/skeleton";
import { api, qk, type BoardName, type Row } from "@/lib/api";
import { cn } from "@/lib/utils";

export type BoardFilter = { label: string; test: (r: Row) => boolean };

export type BoardPageProps = {
  board: BoardName;
  /** what the numbers mean, in the reader's terms — never optional */
  blurb: React.ReactNode;
  filters?: BoardFilter[];
  priority?: string[];
  hide?: string[];
  rebuildHint: string;
  onRowClick?: (row: Row) => void;
  emptyMessage?: string;
  /** extra chrome between the blurb and the table (stat tiles, a chart) */
  children?: React.ReactNode;
  /**
   * Query parameters for boards whose numbers depend on something the reader chooses. The server
   * recomputes from the same Python the script runs; this is still a GET and still a read.
   */
  params?: Record<string, string | number>;
};

/**
 * Every board screen has the same skeleton, and one of its parts is not decoration: a board is
 * only as fresh as the last time its script ran, so it must state its session and warn when the
 * archive has moved past it. Baking that into the shared component means a new page cannot
 * forget it — which is exactly how the Streamlit Swing Trader Pro page ended up printing
 * "Session 2026-08-19" while 339 symbols already had an 08-20 bar.
 */
export function BoardPage({
  board,
  blurb,
  filters,
  priority,
  hide = [],
  rebuildHint,
  onRowClick,
  emptyMessage,
  children,
  params,
}: BoardPageProps) {
  const [active, setActive] = useState(0);
  const q = useQuery({
    queryKey: qk.board(board, params),
    queryFn: ({ signal }) => api.board(board, signal, params),
    // Re-sizing changes every quantity on screen; dropping to a spinner between books reads as
    // the sheet emptying out. Keep the previous numbers until the new ones land.
    placeholderData: (prev) => prev,
  });

  // Live prices, per CLAUDE.md's live-data rule. A board is analysis computed at a session's
  // close — but a `close` or `ltp` column on one is still a price, and during a session a stored
  // close under a bare heading is the same lie as a stale board.
  //
  // Declared BEFORE the error return below: a hook after a conditional return changes hook order
  // between renders, which React rejects outright.
  const symbols = useMemo(
    () =>
      (q.data?.rows ?? [])
        .map((r) => String(r.symbol ?? ""))
        .filter(Boolean)
        .slice(0, 400),          // the route's cap; no board is near it
    [q.data],
  );
  const live = useQuery({
    queryKey: ["quotes", "board", board],
    queryFn: ({ signal }) => api.quotes(symbols, signal),
    enabled: symbols.length > 0,
    refetchInterval: 500,
    retry: false,
  });
  const quotes = live.data?.fresh ? live.data.quotes : undefined;
  // The overlay keeps working after the close — today's closing price IS more current than a
  // board built last night — but it must stop CALLING it live. Only the session decides that.
  const marketOpen =
    live.data?.session === "LIVE" || Boolean(live.data?.session?.startsWith("PRE-OPEN"));

  if (q.isError) {
    return (
      <div className="p-4 md:p-6">
        <div className="flex items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/10 p-4">
          <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" />
          <div className="text-[13px]">
            <div className="font-medium">Could not load this board.</div>
            <p className="mt-1 text-muted-foreground">{(q.error as Error).message}</p>
          </div>
        </div>
      </div>
    );
  }

  // Only the columns that ARE a spot price. Everything else on the row is what a script decided
  // at the close and stays exactly as written.
  const rows = !quotes
    ? (q.data?.rows ?? [])
    : (q.data?.rows ?? []).map((r) => {
        const t = quotes[String(r.symbol ?? "")];
        if (!t) return r;
        const out = { ...r };
        if ("close" in r) out.close = t.ltp;
        if ("ltp" in r) out.ltp = t.ltp;
        if ("change_pct" in r && t.pct != null) out.change_pct = t.pct;
        return out;
      });
  const chosen = filters?.[active];
  const shown = chosen ? rows.filter(chosen.test) : rows;

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 p-4 md:p-6">
      <p className="max-w-4xl text-[13px] leading-relaxed text-muted-foreground">{blurb}</p>

      {q.data?.missing && (
        <Banner tone="muted" icon={Inbox}>
          This board has never been built. Run its script once and it will appear —{" "}
          <span className="font-mono text-foreground">{rebuildHint}</span>
        </Banner>
      )}

      {q.data?.session_unknown && (
        <Banner tone="primary" icon={AlertTriangle}>
          This board carries no date, so there is no way to tell which session it was computed on
          — it may be today&apos;s or it may be weeks old. Treat every number below as undated
          until the script that writes it emits a <span className="font-mono">date</span> column.
        </Banner>
      )}

      {q.data?.stale && (
        <Banner tone="primary" icon={AlertTriangle}>
          Computed on <span className="font-mono text-foreground">{q.data.session}</span>, but the
          archive already reaches{" "}
          <span className="font-mono text-foreground">{q.data.archive_session}</span>. Every number
          below is from the earlier session — rebuild with{" "}
          <span className="font-mono text-foreground">{rebuildHint}</span>. Before the 15:00 NPT
          close the newest bar is a partial session, so rebuilding mid-day scores an unfinished
          candle.
        </Banner>
      )}

      {children}

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-border bg-card">
        <div className="flex flex-wrap items-center gap-1.5 border-b border-border px-3 py-2">
          {filters?.map((f, i) => {
            const n = rows.filter(f.test).length;
            return (
              <button
                key={f.label}
                onClick={() => setActive(i)}
                className={cn(
                  "relative rounded-md px-2.5 py-1 text-[12px] transition-colors",
                  i === active
                    ? "text-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {i === active && (
                  <motion.span
                    layoutId={`board-filter-${board}`}
                    transition={{ type: "spring", stiffness: 500, damping: 40 }}
                    className="absolute inset-0 -z-10 rounded-md bg-accent"
                  />
                )}
                {f.label}{" "}
                <span className="font-mono text-[11px] text-muted-foreground">{n}</span>
              </button>
            );
          })}
          <span className="ml-auto flex items-center gap-2">
            {quotes && (
              <span
                title={
                  (marketOpen
                    ? "close and change% are ticking off the NAASA socket"
                    : "close and change% are today's FINAL numbers off the socket, not live") +
                  (live.data?.age != null ? `, ${Math.round(live.data.age)}s old` : "") +
                  ". Entry, stop, target and risk stay as this board computed them — so once " +
                  "close moves, risk% no longer matches close-to-stop. That is the setup's " +
                  "risk as decided, not a rounding error."
                }
                className={cn(
                  "rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide",
                  marketOpen ? "bg-down/15 text-down" : "bg-muted text-muted-foreground",
                )}
              >
                {marketOpen ? "live price" : "today's close"}
              </span>
            )}
            <span className="font-mono text-[11px] text-muted-foreground">
              {q.data?.session ?? "—"}
            </span>
          </span>
        </div>

        {q.isPending ? (
          <div className="space-y-2 p-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-7 w-full" />
            ))}
          </div>
        ) : (
          <BoardTable
            rows={shown}
            columns={q.data?.columns ?? []}
            priority={priority}
            hide={hide}
            onRowClick={onRowClick}
            emptyMessage={emptyMessage}
          />
        )}
      </div>
    </div>
  );
}

function Banner({
  tone,
  icon: Icon,
  children,
}: {
  tone: "primary" | "muted";
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
      className={cn(
        "flex items-start gap-3 rounded-lg border p-3.5 text-[13px]",
        tone === "primary"
          ? "border-primary/40 bg-primary/10"
          : "border-border bg-muted/40",
      )}
    >
      <Icon
        className={cn(
          "mt-0.5 size-4 shrink-0",
          tone === "primary" ? "text-primary" : "text-muted-foreground",
        )}
      />
      <div className="text-muted-foreground">{children}</div>
    </motion.div>
  );
}
