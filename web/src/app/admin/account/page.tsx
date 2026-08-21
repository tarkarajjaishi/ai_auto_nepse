"use client";

import { useQuery } from "@tanstack/react-query";
import { CircleAlert, Eye, KeyRound, PlugZap } from "lucide-react";

import { ConnectionsPanel } from "@/components/connections-panel";
import { LiveDepth } from "@/components/live-depth";
import { StatTile } from "@/components/stat-tile";
import { Skeleton } from "@/components/ui/skeleton";
import { api, qk, ApiError, type Holding, type Order } from "@/lib/api";
import { num, price, rupees } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Poll while the tab is open. Matches ACCT_POLL in ui.py so the two screens cannot disagree. */
const POLL = 15_000;

export default function AccountPage() {
  const holdings = useQuery({
    queryKey: qk.holdings,
    queryFn: ({ signal }) => api.holdings(signal),
    refetchInterval: POLL,
    retry: false,
  });
  const orders = useQuery({
    queryKey: qk.orderbook,
    queryFn: ({ signal }) => api.orderbook(signal),
    refetchInterval: POLL,
    retry: false,
  });
  const coll = useQuery({
    queryKey: qk.collateral,
    queryFn: ({ signal }) => api.collateral(signal),
    refetchInterval: POLL,
    retry: false,
  });

  // Two different 503s, and conflating them would send someone to re-enter a password that was
  // never the problem. `configured: false` is a setup step; anything else 503 is NAASA itself.
  const blocked = [holdings, orders, coll].find(
    (q) => q.error instanceof ApiError && q.error.status === 503,
  )?.error as ApiError | undefined;
  const notConfigured = blocked?.message.includes("No NAASA login") ?? false;
  const upstreamGone = Boolean(blocked) && !notConfigured;
  const f = coll.data?.fields ?? {};
  const value = holdings.data?.rows.reduce((a, r) => a + (r.value ?? 0), 0) ?? null;
  const day = holdings.data?.rows.reduce((a, r) => a + (r.day_change ?? 0), 0) ?? null;

  return (
    <div className="space-y-4 p-4 md:p-6">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-[15px] font-semibold tracking-tight">NAASA account</h2>
        <span className="inline-flex items-center gap-1.5 rounded-md border border-border bg-muted/50 px-2 py-0.5 font-mono text-[11px] text-muted-foreground">
          <Eye className="size-3" /> read-only
        </span>
        <span className="ml-auto font-mono text-[11px] text-muted-foreground">
          polling {POLL / 1000}s
        </span>
      </div>

      {/* Status of BOTH saved logins, above the account panels — when the numbers below are
          empty this is the screen that says whether that means "flat" or "the login is gone". */}
      <LiveDepth symbol="NABIL" />

      <ConnectionsPanel />

      {upstreamGone ? (
        <div className="flex max-w-3xl items-start gap-3 rounded-lg border border-primary/40 bg-primary/10 p-4 text-[13px]">
          <PlugZap className="mt-0.5 size-4 shrink-0 text-primary" />
          <div>
            <div className="font-medium">NAASA changed their app — this data is unreachable.</div>
            <p className="mt-1 text-muted-foreground">{blocked?.message}</p>
            <p className="mt-2 text-muted-foreground">
              Everything else in this terminal is unaffected: the boards, the floorsheet and the
              heatmap all read the archive, and the archive is still being filled from chukul.com.
              This screen is the only one that needed a broker session.
            </p>
          </div>
        </div>
      ) : notConfigured ? (
        <div className="flex max-w-2xl items-start gap-3 rounded-lg border border-border bg-muted/40 p-4 text-[13px]">
          <KeyRound className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <div>
            <div className="font-medium">No NAASA login is saved on the server.</div>
            <p className="mt-1 text-muted-foreground">
              Sign in once on the Streamlit app under <strong>NAASA account</strong> with
              &ldquo;Remember me&rdquo;. The session lives on the box, not in your browser, which is
              what lets the socket feed and these reports share one login — NAASA evicts a second
              session on the same account.
            </p>
          </div>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <StatTile
              index={0}
              label="Available collateral"
              value={rupees(f.GrossAvalibleExposure)}
              loading={coll.isPending}
            />
            <StatTile
              index={1}
              label="Used collateral"
              value={rupees(f.GrossUsedExposure)}
              loading={coll.isPending}
            />
            <StatTile
              index={2}
              label="Holdings value"
              value={rupees(f.TotalHoldingAmount ?? value)}
              loading={coll.isPending && holdings.isPending}
            />
            <StatTile
              index={3}
              label="Day change"
              value={rupees(day)}
              tone={day == null ? "default" : day >= 0 ? "up" : "down"}
              loading={holdings.isPending}
            />
            {/* WORKING, not count. The order book returns today's orders with the filled ones
                in it, so `count` under an "Open orders" label counted a trade that executed at
                lunchtime as still live. The sub-line keeps the completed ones visible rather
                than hiding them — they are the day's activity. */}
            <StatTile
              index={4}
              label="Open orders"
              value={num(orders.data?.working, 0)}
              note={
                orders.data && orders.data.done > 0
                  ? `${orders.data.done} completed today`
                  : undefined
              }
              loading={orders.isPending}
            />
          </div>

          <Panel
            title="Holdings"
            count={holdings.data?.count}
            error={holdings.error}
            loading={holdings.isPending}
            empty="No holdings in this account."
            head={["Symbol", "Qty", "WACC", "LTP", "Value", "Day"]}
            rows={holdings.data?.rows ?? []}
            render={(r: Holding) => (
              <tr key={r.symbol} className="border-b border-border/50 hover:bg-accent/40">
                <td className="px-3 py-1.5 font-mono font-medium">{r.symbol}</td>
                <td className="px-3 py-1.5 text-right font-mono tabular-nums">
                  {num(r.quantity, 0)}
                </td>
                <td className="px-3 py-1.5 text-right font-mono tabular-nums text-muted-foreground">
                  {price(r.wacc)}
                </td>
                <td className="px-3 py-1.5 text-right font-mono tabular-nums">{price(r.ltp)}</td>
                <td className="px-3 py-1.5 text-right font-mono tabular-nums">{rupees(r.value)}</td>
                <td
                  className={cn(
                    "px-3 py-1.5 text-right font-mono tabular-nums",
                    (r.day_change ?? 0) >= 0 ? "text-up" : "text-down",
                  )}
                >
                  {rupees(r.day_change)}
                </td>
              </tr>
            )}
          />

          <Panel
            title="Order book"
            count={orders.data?.count}
            error={orders.error}
            loading={orders.isPending}
            empty="No orders today."
            head={["Symbol", "Side", "Qty", "Remaining", "Price", "Status", "Time"]}
            rows={orders.data?.rows ?? []}
            render={(r: Order, i: number) => (
              <tr key={`${r.symbol}-${i}`} className="border-b border-border/50 hover:bg-accent/40">
                <td className="px-3 py-1.5 font-mono font-medium">{r.symbol}</td>
                <td
                  className={cn(
                    "px-3 py-1.5 font-mono",
                    /^b/i.test(r.side ?? "") ? "text-up" : "text-down",
                  )}
                >
                  {r.side ?? "—"}
                </td>
                <td className="px-3 py-1.5 text-right font-mono tabular-nums">
                  {num(r.quantity, 0)}
                </td>
                <td className="px-3 py-1.5 text-right font-mono tabular-nums text-muted-foreground">
                  {num(r.remaining, 0)}
                </td>
                <td className="px-3 py-1.5 text-right font-mono tabular-nums">{price(r.price)}</td>
                <td className="px-3 py-1.5 text-[11px] text-muted-foreground">{r.status ?? "—"}</td>
                <td className="px-3 py-1.5 text-right font-mono text-[11px] text-muted-foreground">
                  {r.time ?? "—"}
                </td>
              </tr>
            )}
          />
        </>
      )}

      <p className="max-w-4xl text-[13px] leading-relaxed text-muted-foreground">
        This screen reads and never writes. There is no place, modify or cancel control here, and
        no endpoint behind one — the API serves <code className="font-mono">GET</code> only and the
        account module does not import NAASA&apos;s order functions at all, which{" "}
        <code className="font-mono">test_ops.py</code> checks by walking the call graph rather than
        by trusting the file to stay that way. Placing an order is a decision for a person at a
        keyboard, not something that should become possible as a side effect of porting a page.
      </p>
    </div>
  );
}

function Panel<T>({
  title,
  count,
  error,
  loading,
  empty,
  head,
  rows,
  render,
}: {
  title: string;
  count?: number;
  error: unknown;
  loading: boolean;
  empty: string;
  head: string[];
  rows: T[];
  render: (row: T, i: number) => React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-border bg-card">
      <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
        <h3 className="text-[13px] font-semibold tracking-tight">{title}</h3>
        {count != null && (
          <span className="font-mono text-[11px] text-muted-foreground">{count}</span>
        )}
      </div>

      {error ? (
        <div className="flex items-start gap-3 p-4 text-[13px]">
          <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" />
          <div className="text-muted-foreground">{(error as Error).message}</div>
        </div>
      ) : loading ? (
        <div className="space-y-2 p-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-5 w-full" />
          ))}
        </div>
      ) : !rows.length ? (
        <p className="p-4 text-[13px] text-muted-foreground">{empty}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="border-b border-border text-muted-foreground">
                {head.map((h, i) => (
                  <th
                    key={h}
                    className={cn(
                      "px-3 py-1.5 font-medium",
                      i === 0 || h === "Side" || h === "Status" ? "text-left" : "text-right",
                    )}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>{rows.map(render)}</tbody>
          </table>
        </div>
      )}
    </section>
  );
}
