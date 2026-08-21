"use client";

import { useQuery } from "@tanstack/react-query";
import { Radio } from "lucide-react";
import { useEffect, useState } from "react";

import { api, type BookLevel, type Depth } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * The live book off NAASA's socket.
 *
 * The socket itself is not here and cannot be: NAASA allows **one session per account**, so
 * whichever process connects second takes the feed away from the first. The process holding it
 * publishes what it sees to `Master_data/feed_snapshot.txt` and this reads that — which is why
 * every number below arrives with an age attached.
 *
 * **The age is the feature.** A snapshot from Thursday's close renders exactly like a live one,
 * and a quote presented as current when it is two days old is the failure this project keeps
 * finding in slower forms. So a stale snapshot is labelled and dimmed rather than drawn as a live
 * price, and "no ticks" is read against the session clock — at 09:00 that is a closed market, at
 * 12:00 it is a broken feed, and a panel that cannot tell them apart sends the reader hunting a
 * problem they do not have.
 */
export function LiveDepth({ symbol }: { symbol: string }) {
  const [draft, setDraft] = useState(symbol);
  const [watch, setWatch] = useState(symbol);

  const q = useQuery({
    queryKey: ["depth", watch],
    queryFn: ({ signal }) => api.depth(watch, signal),
    // 1s, matching the rest of the terminal. It reads one small file, so this is cheap — and
    // unlike the account panels it touches no authenticated broker call.
    refetchInterval: 1000,
    retry: false,
  });

  const d = q.data;
  const quote = d?.quote ?? null;
  const live = Boolean(d?.fresh && quote);
  const chg =
    quote?.ltp != null && quote?.close ? ((quote.ltp - quote.close) / quote.close) * 100 : null;

  return (
    <section className="rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-4 py-2.5">
        <Radio className={cn("size-3.5", live ? "text-down" : "text-muted-foreground")} />
        <h3 className="text-[13px] font-semibold tracking-tight">Live socket feed</h3>
        {d && <SessionBadge session={d.session} minutes={d.minutes_to_next} />}
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value.toUpperCase())}
          onKeyDown={(e) => e.key === "Enter" && setWatch(draft.trim().toUpperCase())}
          onBlur={() => setWatch(draft.trim().toUpperCase())}
          className="h-7 w-28 rounded-md border border-border bg-background px-2 font-mono text-[12px] outline-none focus:border-primary/60"
          aria-label="Instrument to watch"
        />
        {quote && (
          <span className="font-mono text-[12px] text-muted-foreground">
            LTP{" "}
            <b className={cn(chg == null ? "" : chg >= 0 ? "text-up" : "text-down")}>
              {fmt(quote.ltp)}
            </b>
            {chg != null && ` (${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%)`}
          </span>
        )}
        <Freshness d={d} />
      </div>

      {!quote ? (
        <NoTicks q={q} d={d} watch={watch} />
      ) : (
        <>
          <div className={cn("grid gap-px bg-border lg:grid-cols-3", !live && "opacity-60")}>
            <Ladder title="Top 5 buy" levels={quote.bids} tone="up" orderFirst />
            <Ladder title="Top 5 sell" levels={quote.asks} tone="down" />
            <Info quote={quote} />
          </div>
          <Clocks stamp={quote.stamp} instruments={d?.instruments ?? 0} />
        </>
      )}
    </section>
  );
}

/**
 * Nothing came through. Say which of the two reasons it is.
 *
 * During LIVE the overwhelmingly likely cause is the one-session rule: a browser tab signed in on
 * the same account is holding the feed, and the two keep evicting each other. Outside LIVE it is
 * just a closed market, and offering eviction advice there sends people hunting a non-problem.
 */
function NoTicks({
  q,
  d,
  watch,
}: {
  q: { isError: boolean; error: unknown };
  d?: Depth;
  watch: string;
}) {
  if (q.isError) {
    return (
      <p className="px-4 py-3 text-[12px] leading-snug text-muted-foreground">
        {(q.error as Error).message}
      </p>
    );
  }
  if (!d) {
    return <p className="px-4 py-3 text-[12px] text-muted-foreground">Reading the snapshot…</p>;
  }
  const trading = d.session === "LIVE" || d.session.startsWith("PRE-OPEN");
  return (
    <div className="space-y-2 px-4 py-3 text-[12px] leading-snug text-muted-foreground">
      {d.instruments === 0 ? (
        <p>
          No snapshot on the server. The socket runs in whichever process holds the NAASA
          session — start the feed there and this fills in.
        </p>
      ) : (
        <p>
          {watch} is not in the snapshot. The feed carries the instruments it was subscribed to;{" "}
          {d.instruments} are present.
        </p>
      )}
      {trading && (
        <p>
          The market is <strong>{d.session}</strong>, so this should be ticking. The usual cause is
          that a <strong>browser tab on the same account</strong> is holding the session — NAASA
          allows one live session per account, so the server socket and your browser evict each
          other. Close your NAASA tabs, or sign the server feed in with a dedicated feed account.
        </p>
      )}
    </div>
  );
}

/** One side of the book, five levels and a total. */
function Ladder({
  title,
  levels,
  tone,
  orderFirst = false,
}: {
  title: string;
  levels: BookLevel[];
  tone: "up" | "down";
  orderFirst?: boolean;
}) {
  const total = levels.reduce((a, l) => a + (l.qty ?? 0), 0);
  const cols = orderFirst ? (["Order", "Qty", "Price"] as const) : (["Price", "Qty", "Order"] as const);
  const cell = (l: BookLevel) =>
    orderFirst
      ? [fmt(l.orders, 0), fmt(l.qty, 0), fmt(l.price)]
      : [fmt(l.price), fmt(l.qty, 0), fmt(l.orders, 0)];

  return (
    <div className="bg-card">
      <div
        className={cn(
          "px-3 py-1.5 text-center text-[11px] font-semibold uppercase tracking-wide",
          tone === "up" ? "bg-up/15 text-up" : "bg-down/15 text-down",
        )}
      >
        {title}
      </div>
      <table className="w-full text-[12px]">
        <thead>
          <tr className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            {cols.map((c) => (
              <th key={c} className="px-3 py-1 text-right font-medium">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {/* Exactly five rows: a book two deep still shows five, because the empty rows are the
              information — they say nobody is quoting past that level. */}
          {Array.from({ length: 5 }).map((_, i) => {
            const l = levels[i];
            const vals = l ? cell(l) : ["–", "–", "–"];
            return (
              <tr key={i}>
                {vals.map((v, j) => (
                  <td key={j} className="px-3 py-1 text-right font-mono tabular-nums">
                    {v}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr className="border-t border-border">
            <td className="px-3 py-1 text-[11px] text-muted-foreground">Total</td>
            <td className="px-3 py-1 text-right font-mono font-semibold tabular-nums">
              {fmt(total, 0)}
            </td>
            <td />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function Info({ quote }: { quote: NonNullable<Depth["quote"]> }) {
  const rows: [string, string][] = [
    ["Avg price", fmt(quote.avg_price)],
    ["D. high", fmt(quote.high)],
    ["D. low", fmt(quote.low)],
    ["P. close", fmt(quote.close)],
    ["Volume", fmt(quote.volume, 0)],
    ["Turnover", fmt(quote.turnover, 0)],
  ];
  return (
    <div className="bg-card">
      <div className="bg-muted px-3 py-1.5 text-center text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        Stock information
      </div>
      <table className="w-full text-[12px]">
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k}>
              <td className="px-3 py-1 text-muted-foreground">{k}</td>
              <td className="px-3 py-1 text-right font-mono font-semibold tabular-nums">{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Two clocks, deliberately.
 *
 * "Last tick" is when THIS scrip last traded; "drawn" is when the panel last re-rendered. Without
 * the second one a quiet stock and a frozen page look identical — which is exactly the confusion
 * that once hid a real freeze.
 */
function Clocks({ stamp, instruments }: { stamp: string | null; instruments: number }) {
  const [drawn, setDrawn] = useState<string>("");
  useEffect(() => {
    const tick = () =>
      setDrawn(
        new Date().toLocaleTimeString("en-GB", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        }),
      );
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <p className="border-t border-border px-4 py-2 font-mono text-[11px] text-muted-foreground">
      last tick {stamp ?? "—"} · drawn {drawn} · {instruments} instruments in the snapshot
    </p>
  );
}

function SessionBadge({ session, minutes }: { session: string; minutes: number | null }) {
  const open = session === "LIVE";
  const pre = session.startsWith("PRE-OPEN");
  return (
    <span
      title={minutes == null ? undefined : `${minutes} minutes to the next change`}
      className={cn(
        "rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide",
        open ? "bg-up/15 text-up" : pre ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground",
      )}
    >
      {session}
    </span>
  );
}

/** How old the snapshot is, stated plainly — the one thing a live panel must never omit. */
function Freshness({ d }: { d?: Depth }) {
  if (!d) return null;
  if (d.age == null) {
    return (
      <span className="ml-auto rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
        no snapshot
      </span>
    );
  }
  return (
    <span
      className={cn(
        "ml-auto rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide",
        d.fresh ? "bg-down/15 text-down" : "bg-muted text-muted-foreground",
      )}
    >
      {d.fresh ? "live" : "stale"} · {age(d.age)}
    </span>
  );
}

function fmt(v: number | null | undefined, dp = 2) {
  return v == null
    ? "–"
    : v.toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

function age(s: number) {
  if (s < 90) return `${Math.round(s)}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  if (s < 172800) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}
