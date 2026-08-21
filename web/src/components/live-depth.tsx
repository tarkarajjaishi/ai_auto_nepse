"use client";

import { useQuery } from "@tanstack/react-query";
import { Radio } from "lucide-react";
import { useState } from "react";

import { api, type Depth } from "@/lib/api";
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
 * price, and a missing one says the feed is not running instead of showing dashes that look like
 * a quiet market.
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
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value.toUpperCase())}
          onKeyDown={(e) => e.key === "Enter" && setWatch(draft.trim().toUpperCase())}
          onBlur={() => setWatch(draft.trim().toUpperCase())}
          className="h-7 w-28 rounded-md border border-border bg-background px-2 font-mono text-[12px] outline-none focus:border-primary/60"
          aria-label="Instrument to watch"
        />
        <Freshness d={d} />
      </div>

      {!quote ? (
        <p className="px-4 py-3 text-[12px] leading-snug text-muted-foreground">
          {/* An error must not render as "Reading…". A panel that says it is still loading while
              the API is unreachable is a spinner standing in for a fault, and this is the one
              panel whose whole job is to be honest about what it does and does not know. */}
          {q.isError
            ? (q.error as Error).message
            : d && d.instruments === 0
              ? "No snapshot on the server. The socket runs in whichever process holds the NAASA session — start the feed there, and this fills in."
              : d
                ? `${watch} is not in the snapshot. The feed carries the instruments it was subscribed to; ${d.instruments} are present.`
                : "Reading the snapshot…"}
        </p>
      ) : (
        <>
          <div className={cn("grid grid-cols-2 gap-px bg-border sm:grid-cols-4", !live && "opacity-60")}>
            <Tile
              label="LTP"
              value={fmt(quote.ltp)}
              tone={chg == null ? undefined : chg >= 0 ? "up" : "down"}
              sub={chg == null ? undefined : `${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%`}
            />
            <Tile label="Open" value={fmt(quote.open)} />
            <Tile label="High" value={fmt(quote.high)} />
            <Tile label="Low" value={fmt(quote.low)} />
            <Tile label="Bid" value={fmt(quote.bid)} tone="up" sub={qty(quote.bid_qty)} />
            <Tile label="Ask" value={fmt(quote.ask)} tone="down" sub={qty(quote.ask_qty)} />
            <Tile label="Volume" value={fmt(quote.volume, 0)} />
            <Tile label="Prev close" value={fmt(quote.close)} />
          </div>

          <p className="border-t border-border px-4 py-2 font-mono text-[11px] text-muted-foreground">
            {quote.stamp ? <>feed stamp {quote.stamp} · </> : null}
            {d?.instruments} instruments in the snapshot
          </p>
        </>
      )}
    </section>
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

function Tile({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "up" | "down";
}) {
  return (
    <div className="bg-card px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div
        className={cn(
          "font-mono text-[14px] font-semibold tabular-nums",
          tone === "up" && "text-up",
          tone === "down" && "text-down",
        )}
      >
        {value}
      </div>
      {sub && <div className="font-mono text-[10.5px] text-muted-foreground">{sub}</div>}
    </div>
  );
}

function fmt(v: number | null | undefined, dp = 2) {
  return v == null
    ? "—"
    : v.toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

function qty(v: number | null | undefined) {
  return v == null ? undefined : `${v.toLocaleString(undefined, { maximumFractionDigits: 0 })} qty`;
}

function age(s: number) {
  if (s < 90) return `${Math.round(s)}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  if (s < 172800) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}
