"use client";

import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { CircleAlert, CircleCheck, CircleDashed } from "lucide-react";

import { api, qk } from "@/lib/api";
import { ALL_NAV_ITEMS } from "@/lib/nav";
import { cn } from "@/lib/utils";

const LABEL: Record<string, string> = Object.fromEntries(
  ALL_NAV_ITEMS.filter((i) => i.board).map((i) => [i.board!, i.label]),
);

const REBUILD: Record<string, string> = {
  swing_quantam: "python -m swing_quantam",
  swing_pro: "python swing_pro.py",
  supply_demand: "python supply_demand.py",
  scan: "python scan.py",
  volume_spike: "python volume_spike.py",
  operator_scan: "python operator_scan.py",
  operator_now: "python operator_now.py",
  operator_verdict: "python operator_verdict.py",
  master_signal: "python master_signal.py",
  swing_master: "python swing_master.py",
  backtest: "python backtest.py",
};

export default function PipelinePage() {
  const q = useQuery({ queryKey: qk.boards, queryFn: ({ signal }) => api.boards(signal) });
  const entries = Object.entries(q.data?.boards ?? {});

  return (
    <div className="space-y-4 p-4 md:p-6">
      <p className="max-w-4xl text-[13px] leading-relaxed text-muted-foreground">
        Every board is a <strong>pre-computed table</strong>, so its numbers are only as fresh as
        the last time its script ran. This page is the one place that states, for all of them at
        once, whether that is still true. The archive is at{" "}
        <span className="font-mono text-foreground">{q.data?.archive_session ?? "—"}</span>. Before
        the 15:00 NPT close the newest bar is a <strong>partial</strong> session, so rebuilding
        mid-day scores an unfinished candle. Rebuilds are not triggered from this page — but
        they do happen on their own: <span className="font-mono">chukul-update.timer</span> runs
        the pipeline on the server daily, after the close. This page reports freshness; it does not
        control it.
      </p>

      {/* The archive's OWN age. Every row below compares a board to the archive, so all of them
          can read "current" while the archive itself is a week stale — boards agreeing with each
          other says nothing about whether the market has moved on. */}
      <ArchiveAge missed={q.data?.missed_sessions} session={q.data?.archive_session} />

      <div className="overflow-hidden rounded-lg border border-border bg-card">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="border-b border-border font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              <th className="px-4 py-2 text-left font-medium">Board</th>
              <th className="px-4 py-2 text-right font-medium">Rows</th>
              <th className="px-4 py-2 text-left font-medium">Session</th>
              <th className="px-4 py-2 text-left font-medium">State</th>
              <th className="px-4 py-2 text-left font-medium">Rebuild</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([name, b], i) => (
              <motion.tr
                key={name}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: i * 0.02 }}
                className="border-b border-border/50"
              >
                <td className="px-4 py-2">{LABEL[name] ?? name}</td>
                <td className="px-4 py-2 text-right font-mono tabular-nums">{b.rows || "—"}</td>
                <td className="px-4 py-2 font-mono text-muted-foreground">{b.session ?? "—"}</td>
                <td className="px-4 py-2">
                  {/* FOUR states, not three. An undated board has stale=false, which this cell
                      used to read as "current" and tick green — the freshness page itself
                      asserting a board is up to date when it cannot know. "Cannot tell" is a
                      different answer from "fine", and this is the one screen that must never
                      collapse them. */}
                  <span
                    className={cn(
                      "inline-flex items-center gap-1.5 font-mono text-[11px]",
                      b.missing
                        ? "text-muted-foreground"
                        : b.stale || b.session_unknown
                          ? "text-primary"
                          : "text-up",
                    )}
                  >
                    {b.missing ? (
                      <>
                        <CircleDashed className="size-3.5" /> never built
                      </>
                    ) : b.stale ? (
                      <>
                        <CircleDashed className="size-3.5" /> behind the archive
                      </>
                    ) : b.session_unknown ? (
                      <>
                        <CircleAlert className="size-3.5" /> undated · cannot tell
                      </>
                    ) : (
                      <>
                        <CircleCheck className="size-3.5" /> current
                      </>
                    )}
                  </span>
                </td>
                <td className="px-4 py-2 font-mono text-[11px] text-muted-foreground">
                  {REBUILD[name] ?? "—"}
                </td>
              </motion.tr>
            ))}
          </tbody>
        </table>
        {q.isPending && <p className="p-4 text-[13px] text-muted-foreground">Loading…</p>}
      </div>
    </div>
  );
}

/**
 * How far behind the market the ARCHIVE is, in trading sessions.
 *
 * One missed session is normal — the 15:15 NPT job may simply not have run yet. Two means a real
 * session was lost. The count comes from `market_hours.missed_sessions` on the server, the same
 * function the Streamlit caption uses, so the two surfaces cannot drift on what "behind" means.
 * `null` is "cannot tell", which is louder than stale rather than quieter.
 */
function ArchiveAge({ missed, session }: { missed?: number | null; session?: string | null }) {
  if (missed === undefined) return null;
  const bad = missed === null || missed > 1;
  return (
    <div
      className={cn(
        "mt-4 flex items-start gap-2.5 rounded-lg border px-4 py-3 text-[13px]",
        bad ? "border-down/40 bg-down/10" : "border-border bg-card",
      )}
    >
      {bad ? (
        <CircleAlert className="mt-0.5 size-4 shrink-0 text-down" />
      ) : (
        <CircleCheck className="mt-0.5 size-4 shrink-0 text-up" />
      )}
      <div className="text-muted-foreground">
        {missed === null ? (
          <>
            The archive has no readable session date, so nothing below can be dated. Check that
            the daily fetch ran at all.
          </>
        ) : bad ? (
          <>
            The archive is{" "}
            <strong className="text-foreground">{missed} trading sessions</strong> behind the
            market — its newest bar is{" "}
            <span className="font-mono text-foreground">{session}</span>. Every board below is at
            least that old, however current it reads against the archive.
          </>
        ) : (
          <>
            The archive is current at{" "}
            <span className="font-mono text-foreground">{session}</span>
            {missed === 1 && " — one session is pending, which is normal before the 15:15 NPT job"}.
          </>
        )}
      </div>
    </div>
  );
}
