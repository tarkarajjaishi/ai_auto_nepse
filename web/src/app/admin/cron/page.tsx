"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "motion/react";
import { CircleAlert, CircleCheck, CircleDashed, Clock, Loader2, RotateCw } from "lucide-react";
import { useState } from "react";

import { api, qk, type RebuildStatus } from "@/lib/api";
import { ALL_NAV_ITEMS } from "@/lib/nav";
import { cn } from "@/lib/utils";

const LABEL: Record<string, string> = Object.fromEntries(
  ALL_NAV_ITEMS.filter((i) => i.board).map((i) => [i.board!, i.label]),
);

/** The equivalent shell command, shown on the button as a title so it can still be copied. */
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

  // Poll only while something is running. A freshness page that hammers the API every second is
  // its own kind of dishonest — it looks busy and tells you nothing new.
  const rb = useQuery({
    queryKey: qk.rebuild,
    queryFn: ({ signal }) => api.rebuildStatus(signal),
    refetchInterval: (query) =>
      query.state.data?.running || query.state.data?.busy ? 2000 : false,
  });

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
        the pipeline on the server daily, after the close — but{" "}
        <strong>not every board is in that pipeline</strong>. The Schedule column says which, and
        the ones marked <em>manual</em> change only when somebody rebuilds them. You can do that
        here.
      </p>

      {/* The archive's OWN age. Every row below compares a board to the archive, so all of them
          can read "current" while the archive itself is a week stale — boards agreeing with each
          other says nothing about whether the market has moved on. */}
      <ArchiveAge missed={q.data?.missed_sessions} session={q.data?.archive_session} />

      <LastRun status={rb.data} />

      <StoreTable />

      <div className="overflow-hidden rounded-lg border border-border bg-card">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="border-b border-border font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              <th className="px-4 py-2 text-left font-medium">Board</th>
              <th className="px-4 py-2 text-right font-medium">Rows</th>
              <th className="px-4 py-2 text-left font-medium">Session</th>
              <th className="px-4 py-2 text-left font-medium">State</th>
              <th className="px-4 py-2 text-left font-medium">Schedule</th>
              <th className="px-4 py-2 text-right font-medium">Rebuild</th>
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
                <td className="px-4 py-2">
                  <Schedule board={name} status={rb.data} />
                </td>
                <td className="px-4 py-2 text-right">
                  <RebuildButton board={name} status={rb.data} />
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

/**
 * Does the nightly timer rebuild this board, or does somebody have to?
 *
 * The distinction was invisible and the page implied the first for everything. Four boards have
 * no automatic rebuild at all, so they sat at whatever date they were last built while the
 * archive moved on underneath — and the row above still ticked green, because a board is
 * compared to the archive and nothing was comparing the SCHEDULE to reality.
 */
function Schedule({ board, status }: { board: string; status?: RebuildStatus }) {
  if (!status) return <span className="font-mono text-[11px] text-muted-foreground">—</span>;
  const auto = status.auto[board];
  if (auto) {
    return (
      <span className="inline-flex items-center gap-1.5 font-mono text-[11px] text-muted-foreground">
        <Clock className="size-3.5" /> nightly
      </span>
    );
  }
  return (
    <span
      title={status.manual[board] ?? "not in the nightly pipeline"}
      className="inline-flex items-center gap-1.5 font-mono text-[11px] text-primary"
    >
      <CircleAlert className="size-3.5" /> manual only
    </span>
  );
}

/**
 * Start a rebuild. The only write call this app makes.
 *
 * A refusal is an ANSWER, not an error: 409 means the cross-process lock is held — by the systemd
 * timer, by the Streamlit page, or by another tab — and the reader needs to know that rather than
 * see a red box. Two rebuilds writing the same .txt files at once is the collision that lock
 * exists to prevent.
 */
function RebuildButton({ board, status }: { board: string; status?: RebuildStatus }) {
  const qc = useQueryClient();
  const [note, setNote] = useState<string | null>(null);
  const m = useMutation({
    mutationFn: () => api.rebuild(board),
    onSuccess: (r) => {
      setNote(r.started ? null : (r.reason ?? "already running"));
      qc.invalidateQueries({ queryKey: qk.rebuild });
    },
    onError: (e: Error) => setNote(e.message),
  });

  const runningThis = status?.running === board;
  const somethingElse = Boolean(status?.busy) && !runningThis;

  return (
    <div className="flex items-center justify-end gap-2">
      {note && <span className="max-w-[220px] truncate text-[11px] text-primary">{note}</span>}
      <button
        onClick={() => {
          setNote(null);
          m.mutate();
        }}
        disabled={m.isPending || runningThis || somethingElse}
        title={
          somethingElse
            ? `Waiting — ${status?.busy?.what} is rebuilding`
            : (REBUILD[board] ?? "rebuild this board")
        }
        className={cn(
          "inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-[12px] transition-colors",
          m.isPending || runningThis
            ? "text-muted-foreground"
            : somethingElse
              ? "cursor-not-allowed text-muted-foreground/50"
              : "hover:bg-accent",
        )}
      >
        {runningThis || m.isPending ? (
          <>
            <Loader2 className="size-3 animate-spin" /> running
          </>
        ) : (
          <>
            <RotateCw className="size-3" /> rebuild
          </>
        )}
      </button>
    </div>
  );
}

/**
 * The raw archive underneath the boards.
 *
 * A board is a table a script wrote; a store is the files it read. Every board can agree with
 * every other and still be built on a store that stopped updating — the board table cannot see
 * that, because it only ever compares a board to the archive.
 *
 * `behind` is a prompt to look, not an error. A symbol that simply did not trade counts as
 * behind, and on NEPSE most of them do on any given session.
 */
function StoreTable() {
  const q = useQuery({ queryKey: qk.stores, queryFn: ({ signal }) => api.stores(signal) });
  const stores = Object.entries(q.data?.stores ?? {});
  const newest = q.data?.archive_session ?? null;

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      <div className="border-b border-border px-4 py-2.5">
        <h3 className="text-[13px] font-semibold tracking-tight">The archive underneath</h3>
        <p className="mt-1 text-[12px] leading-snug text-muted-foreground">
          Every board above is compared to the archive, so all of them can read{" "}
          <em>current</em> while a store beneath them has stopped updating. <strong>Behind</strong>{" "}
          counts members short of the newest session any member reached — a prompt to look, not an
          error, since a symbol that did not trade counts as behind.
        </p>
      </div>
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-b border-border font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            <th className="px-4 py-2 text-left font-medium">Store</th>
            <th className="px-4 py-2 text-left font-medium">Last data</th>
            <th className="px-4 py-2 text-right font-medium">Behind</th>
            <th className="px-4 py-2 text-right font-medium">Members</th>
          </tr>
        </thead>
        <tbody>
          {stores.map(([name, v]) => {
            const lagging = Boolean(newest && v.newest && v.newest < newest);
            return (
              <tr key={name} className="border-b border-border/50 last:border-0">
                <td className="px-4 py-2">{name}</td>
                <td
                  className={cn(
                    "px-4 py-2 font-mono",
                    lagging ? "text-primary" : "text-muted-foreground",
                  )}
                >
                  {v.newest ?? "—"}
                  {lagging && " · behind the archive"}
                </td>
                <td className="px-4 py-2 text-right font-mono tabular-nums text-muted-foreground">
                  {v.behind.toLocaleString()}
                </td>
                <td className="px-4 py-2 text-right font-mono tabular-nums text-muted-foreground">
                  {v.total.toLocaleString()}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {q.isPending && <p className="p-4 text-[13px] text-muted-foreground">Reading the archive…</p>}
      {q.isError && (
        <p className="p-4 text-[13px] text-muted-foreground">
          {(q.error as Error).message}
        </p>
      )}
    </div>
  );
}

/**
 * How the last rebuild went: which board, how long, and what the script actually printed.
 *
 * The Streamlit button showed a status box and the last twelve lines of output. A button that
 * starts a job and then goes quiet is worse than the shell command it replaced — the reader
 * loses the only thing that told them whether it worked.
 */
function LastRun({ status }: { status?: RebuildStatus }) {
  const [open, setOpen] = useState(false);
  const last = status?.last;
  if (!last) return null;

  const failed = !last.ok && !last.skipped;
  const tail = (last.steps ?? []).flatMap((st) =>
    st.tail.map((line) => `${st.script}  ${line}`),
  );

  return (
    <div
      className={cn(
        "rounded-lg border px-4 py-3 text-[13px]",
        failed ? "border-down/40 bg-down/10" : "border-border bg-card",
      )}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {failed ? (
          <CircleAlert className="size-4 shrink-0 text-down" />
        ) : (
          <CircleCheck className="size-4 shrink-0 text-up" />
        )}
        <span>
          Last rebuild: <strong>{last.board}</strong>{" "}
          {last.skipped ? "was skipped — another was already running" : failed ? "FAILED" : "ok"}
        </span>
        {last.seconds != null && (
          <span className="font-mono text-[12px] text-muted-foreground">
            {last.seconds < 90
              ? `${last.seconds}s`
              : `${Math.round(last.seconds / 60)}m ${Math.round(last.seconds % 60)}s`}
          </span>
        )}
        {tail.length > 0 && (
          <button
            onClick={() => setOpen((v) => !v)}
            className="ml-auto rounded-md border border-border px-2 py-0.5 text-[12px] transition-colors hover:bg-accent"
          >
            {open ? "Hide output" : "Show output"}
          </button>
        )}
      </div>

      {open && tail.length > 0 && (
        <pre className="mt-3 max-h-56 overflow-auto rounded-md border border-border bg-background p-3 font-mono text-[11.5px] leading-relaxed">
          {tail.join("\n")}
        </pre>
      )}
    </div>
  );
}
