"use client";

import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { motion } from "motion/react";
import { Activity, CandlestickChart, CircleAlert, TrendingUp } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import { PriceChart } from "@/components/price-chart";
import { SectorTreemap } from "@/components/sector-treemap";
import { Skeleton } from "@/components/ui/skeleton";
import { api, qk, type Heatmap, type LiveBar, type Questions, type Report, type Scorecard } from "@/lib/api";
import {
  compact,
  decisionTone,
  gradeTone,
  heatColour,
  num,
  pct,
  price,
  TONE_CLASS,
} from "@/lib/format";
import { useRail } from "@/store/rail";
import { cn } from "@/lib/utils";

/**
 * The terminal screen, laid out the way a trading terminal is: instrument strip across the top,
 * chart taking everything it can, a tabbed drawer under it, and a context panel down the right.
 *
 * The right panel is where the reference puts its order ticket. This one cannot have an order
 * ticket — NEPSE orders go through NAASA, whose app was replaced and whose login no longer
 * works — so the same slot holds the trade PLAN instead: the levels, the R-multiples and the
 * risk that swing_pro already computed. Same information density, same position, and every
 * number in it came from Python rather than being recomputed here.
 */

/**
 * These move the VIEW, never the fetch.
 *
 * The whole 1D.txt is loaded every time — the archive runs to ~1,600 sessions and holding all of
 * it costs nothing — so changing range is instant and panning past the left edge always finds
 * more candles instead of a wall. Opening on 120 is the same default the Streamlit chart settled
 * on: autoranging price over the full history squashes the last three months into a flat band.
 */
const RANGES = [
  { label: "3M", bars: 63 },
  { label: "6M", bars: 126 },
  { label: "1Y", bars: 252 },
  { label: "2Y", bars: 504 },
  { label: "5Y", bars: 1260 },
  { label: "ALL", bars: 0 },
];
const DEFAULT_RANGE = 1; // 126 sessions ~ the 120 the Streamlit chart opens on

function ChartInner() {
  const params = useSearchParams();
  const router = useRouter();
  const symbol = (params.get("symbol") ?? "NABIL").toUpperCase();
  const [range, setRange] = useState(DEFAULT_RANGE);

  // The input is seeded from the URL and must RE-seed when the URL changes underneath it.
  //
  // useState(symbol) alone initialises once and never again, so picking a scrip in the rail left
  // the header reading NABIL above SAHAS's open, high, low and close — the label disagreeing with
  // the data, which is the exact failure this project keeps finding in other forms. Adjusting
  // state during render (React's documented pattern) rather than an effect: an effect would paint
  // one frame with the stale symbol first.
  const [draft, setDraft] = useState(symbol);
  const [seenSymbol, setSeenSymbol] = useState(symbol);
  if (symbol !== seenSymbol) {
    setSeenSymbol(symbol);
    setDraft(symbol);
  }

  const [mode, setMode] = useState<"candles" | "line">("candles");
  const [showEma, setShowEma] = useState(false);
  // The three the framework itself uses — swing_pro reads e20/e50/e200 and nothing else.
  const emaPeriods = useMemo(() => (showEma ? [20, 50, 200] : undefined), [showEma]);

  const symbols = useQuery({ queryKey: qk.symbols, queryFn: ({ signal }) => api.symbols(signal) });
  // limit 0 = the entire 1D.txt, always. The range buttons below change only what is framed.
  const bars = useQuery({
    queryKey: qk.bars(symbol, 0, emaPeriods),
    queryFn: ({ signal }) => api.bars(symbol, 0, signal, emaPeriods),
    // Toggling indicators refetches, and dropping to a spinner mid-read is worse than a beat of
    // stale candles. Keep the previous series on screen while the new one arrives.
    placeholderData: (prev) => prev,
  });
  const report = useQuery({
    queryKey: qk.report(symbol),
    queryFn: ({ signal }) => api.report(symbol, signal),
    // An index has no swing_pro row and never will; asking again on every render just fills the
    // console with 404s.
    enabled: bars.data?.kind !== "index",
    retry: false,
  });

  // Today's bar, straight off the socket, every second. Deliberately NOT part of `bars`: that
  // query loads the whole archive and re-running it at 1s would refetch five thousand candles and
  // reset the chart. This is one row, and the chart applies it with series.update().
  const live = useQuery({
    queryKey: ["live-bar", symbol],
    queryFn: ({ signal }) => api.liveBar(symbol, signal),
    refetchInterval: 500,
    retry: false,
  });
  const liveBar = live.data?.fresh ? (live.data.bar ?? null) : null;

  const archived = bars.data?.bars.at(-1);
  // Prefer the live bar for the header, but only when it is TODAY's and the archive has not
  // already caught up past it. live_1d writes the same row every 20s, so the two agree; this only
  // removes the latency between flushes.
  const last =
    liveBar && archived && liveBar.date >= archived.date
      ? { ...archived, ...liveBar }
      : archived;
  const prev = bars.data?.bars.at(liveBar && archived && liveBar.date > archived.date ? -1 : -2);
  const change = last && prev ? (last.close / prev.close - 1) * 100 : null;
  const isIndex = bars.data?.kind === "index";

  const known = useMemo(
    () => new Set([...(symbols.data?.symbols ?? []), ...(symbols.data?.indices ?? [])]),
    [symbols.data],
  );

  function go(next: string) {
    const s = next.trim().toUpperCase();
    if (s && known.has(s)) router.push(`/admin/chart?symbol=${encodeURIComponent(s)}`);
  }

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        {/* ── instrument strip ──────────────────────────────────────────────────────────── */}
        <div className="flex shrink-0 flex-wrap items-center gap-x-5 gap-y-2 border-b border-border px-4 py-2.5">
          <div className="flex items-center gap-2">
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value.toUpperCase())}
              onKeyDown={(e) => e.key === "Enter" && go(draft)}
              onBlur={() => go(draft)}
              list="symbols"
              // text-lg / font-bold — their instrument heading, measured at 18px / 700.
              // Width in ch, not px: 128px truncated MANUFACTUREIND to "MANUFACT". Index names
              // run to 14 characters where a scrip is 3-5, and ch scales with the face so this
              // cannot re-break if the font changes.
              className="h-8 w-[15ch] rounded-md border border-transparent bg-transparent px-1 text-lg font-bold tracking-tight outline-none hover:border-border focus:border-primary/60"
            />
            <datalist id="symbols">
              {[...(symbols.data?.indices ?? []), ...(symbols.data?.symbols ?? [])].map((s) => (
                <option key={s} value={s} />
              ))}
            </datalist>
            {isIndex && (
              <span className="rounded bg-primary/15 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-primary">
                index
              </span>
            )}
          </div>

          <LiveTag q={live.data} />

          <Field label="Change" value={pct(change)} tone={change == null ? undefined : change >= 0 ? "up" : "down"} loading={bars.isPending} />
          <Field label="Open" value={price(last?.open)} loading={bars.isPending} />
          <Field label="High" value={price(last?.high)} loading={bars.isPending} />
          <Field label="Low" value={price(last?.low)} loading={bars.isPending} />
          <Field label="Close" value={price(last?.close)} loading={bars.isPending} />
          <Field label="Volume" value={compact(last?.volume)} loading={bars.isPending} />
          <Field label="Session" value={last?.date ?? "—"} loading={bars.isPending} />
        </div>

        {/* ── toolbar ───────────────────────────────────────────────────────────────────── */}
        <div className="flex shrink-0 items-center gap-1 border-b border-border px-3 py-1.5">
          {RANGES.map((r, i) => (
            <button
              key={r.label}
              onClick={() => setRange(i)}
              className={cn(
                "relative rounded px-2 py-0.5 font-mono text-[11px] transition-colors",
                i === range ? "text-primary" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {i === range && (
                <motion.span
                  layoutId="chart-range"
                  transition={{ type: "spring", stiffness: 500, damping: 40 }}
                  className="absolute inset-0 -z-10 rounded bg-accent"
                />
              )}
              {r.label}
            </button>
          ))}

          <span className="mx-1.5 h-4 w-px bg-border" />

          <ToolButton
            active={mode === "candles"}
            onClick={() => setMode("candles")}
            title="Candles"
          >
            <CandlestickChart className="size-3.5" />
          </ToolButton>
          <ToolButton active={mode === "line"} onClick={() => setMode("line")} title="Line">
            <TrendingUp className="size-3.5" />
          </ToolButton>

          <span className="mx-1.5 h-4 w-px bg-border" />

          <button
            onClick={() => setShowEma((v) => !v)}
            className={cn(
              "flex items-center gap-1.5 rounded px-2 py-1 text-xs transition-colors",
              showEma
                ? "bg-primary/15 text-primary"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Activity className="size-3.5" />
            Indicators
            {bars.isFetching && showEma && <span className="text-[10px]">…</span>}
          </button>
          {showEma && (
            <span className="ml-1 flex items-center gap-2 font-mono text-[10px]">
              <span className="text-chart-3">EMA20</span>
              <span className="text-primary">EMA50</span>
              <span className="text-chart-5">EMA200</span>
            </span>
          )}

          <span className="ml-auto font-mono text-[11px] text-muted-foreground">
            {bars.data
              ? `${bars.data.bars.length.toLocaleString()} bars loaded · ${
                  bars.data.bars[0]?.date ?? "—"
                } → ${bars.data.bars.at(-1)?.date ?? "—"} · `
              : ""}
            {isIndex ? (
              <span className="text-primary">unadjusted</span>
            ) : (
              "corporate-action adjusted"
            )}
          </span>
        </div>

        {/* ── chart ─────────────────────────────────────────────────────────────────────── */}
        <div className="flex min-h-0 flex-1 flex-col px-1 py-1">
          {bars.isError ? (
            <div className="m-3 flex items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-[13px]">
              <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" />
              <div className="text-muted-foreground">{(bars.error as Error).message}</div>
            </div>
          ) : bars.isPending ? (
            <Skeleton className="m-2 min-h-0 flex-1" />
          ) : (
            <PriceChart
              bars={bars.data?.bars ?? []}
              fill
              visibleBars={RANGES[range].bars}
              ema={bars.data?.ema}
              mode={mode}
              liveBar={liveBar}
            />
          )}
        </div>

        <BottomDrawer />
      </div>

      <TradePlan
        symbol={symbol}
        report={report.data}
        loading={report.isPending && !isIndex}
        isIndex={isIndex}
      />
    </div>
  );
}

/**
 * Whether the numbers beside this are ticking, and what they are when they are not.
 *
 * A live price and a stored close look identical, and the whole point of this strip is that you
 * can tell. LIVE means the socket published within the last two minutes AND stamped today;
 * anything else names what you are actually looking at instead.
 */
function LiveTag({ q }: { q?: LiveBar }) {
  if (!q) return null;
  // LIVE means the market is OPEN and ticks are arriving — not merely that the snapshot is
  // recent. The publisher keeps writing after the close, so `fresh` stays true over numbers that
  // stopped moving; this read LIVE at 15:01 with the market shut.
  const open = q.session === "LIVE" || q.session.startsWith("PRE-OPEN");
  const ticking = open && q.fresh && Boolean(q.bar);
  const label = ticking ? "live" : open ? "feed quiet" : q.session.toLowerCase();
  return (
    <span
      title={
        ticking
          ? `Today's bar, ${Math.round(q.age ?? 0)}s old, off the NAASA socket`
          : open
            ? "The market is open but no tick has arrived — the last daily bar is shown"
            : q.bar
              ? "The market is closed. These are today's final numbers, not a live price."
              : "The market is closed; these are the last stored session's numbers"
      }
      className={cn(
        "rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide",
        ticking ? "bg-down/15 text-down" : "bg-muted text-muted-foreground",
      )}
    >
      {label}
    </span>
  );
}

function ToolButton({
  active,
  onClick,
  title,
  children,
}: {
  active: boolean;
  onClick: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={cn(
        "grid size-7 place-items-center rounded transition-colors",
        active ? "bg-primary/15 text-primary" : "text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function Field({
  label,
  value,
  tone,
  loading,
}: {
  label: string;
  value: string;
  tone?: "up" | "down";
  loading?: boolean;
}) {
  // Label 10px uppercase muted with NO extra tracking; value 14px semibold mono. Measured off
  // the reference's CHANGE / BID / ASK / SPREAD pairs — the tracking-widest here was mine, and
  // it made the labels read as small-caps signage rather than field names.
  return (
    <div className="leading-tight">
      <div className="text-[10px] uppercase text-muted-foreground">{label}</div>
      {loading ? (
        <Skeleton className="mt-0.5 h-5 w-16" />
      ) : (
        <div
          className={cn(
            "font-mono text-sm font-semibold tabular-nums",
            tone === "up" && "text-up",
            tone === "down" && "text-down",
          )}
        >
          {value}
        </div>
      )}
    </div>
  );
}

/**
 * The market under the chart. No tab bar: the drawer is the sector heatmap and nothing else.
 *
 * It used to carry Sectors / Brokers / Read, and the other two both duplicated a whole page —
 * broker accumulation is the Floorsheet screen, and the trend/stage/structure read is the
 * swing_pro report and the right panel's Checks tab. Two tabs of duplication were costing the
 * heatmap a 38px band and a third of its height.
 */
function BottomDrawer() {
  const heat = useQuery({
    queryKey: qk.heatmap,
    queryFn: ({ signal }) => api.heatmap(signal),
    staleTime: 60_000,
  });
  return (
    <div className="h-[210px] shrink-0 border-t border-border">
      <SectorStrip q={heat} />
    </div>
  );
}

/**
 * The reference's order ticket slot, holding the trade plan instead.
 *
 * Deliberately has no Buy or Sell control and never will from this screen. The numbers are
 * swing_pro's, unmodified — this panel formats them and does not decide anything.
 */
function TradePlan({
  symbol,
  report,
  loading,
  isIndex,
}: {
  symbol: string;
  report?: Report;
  loading: boolean;
  isIndex: boolean;
}) {
  const [view, setView] = useState<"Plan" | "Score" | "Checks">("Plan");
  // Fetched only when their tab is opened — the scorecard and the fifteen questions are two more
  // full analyses, and paying for them to render a panel nobody switched to is waste.
  const card = useQuery({
    queryKey: qk.scorecard(symbol),
    queryFn: ({ signal }) => api.scorecard(symbol, signal),
    enabled: !isIndex && view === "Score",
    retry: false,
  });
  const checks = useQuery({
    queryKey: qk.questions(symbol),
    queryFn: ({ signal }) => api.questions(symbol, signal),
    enabled: !isIndex && view === "Checks",
    retry: false,
  });
  const f = (report?.fields ?? {}) as Record<string, unknown>;
  const n = (k: string) => (typeof f[k] === "number" ? (f[k] as number) : null);
  const s = (k: string) => (f[k] == null ? "—" : String(f[k]));
  const decision = String(f.decision ?? "—");
  const grade = String(f.grade ?? "—");
  const score = n("score");
  const rr = n("rr");

  if (isIndex) {
    return (
      <aside className="hidden w-[312px] shrink-0 flex-col border-l border-border bg-background p-4 xl:flex">
        <div className="text-[13px] font-semibold tracking-tight">{symbol}</div>
        <p className="mt-2 text-[12px] leading-relaxed text-muted-foreground">
          Indices are not scored. The 22-section framework rates instruments you can buy, size and
          stop — an index has no float, no broker tape and no invalidation level, so a grade for
          one would be a number with nothing behind it.
        </p>
      </aside>
    );
  }

  return (
    <aside className="hidden w-[312px] shrink-0 flex-col overflow-y-auto border-l border-border bg-background xl:flex">
      {/* Their order-ticket header is a three-way segmented control (Market / Limit / Stop) in a
          bordered track. Same control, same metrics — the three things you can actually ask of a
          scrip here instead of three order types that do not exist. */}
      <div className="border-b border-border p-3">
        <div className="flex rounded-md border border-border p-0.5">
          {(["Plan", "Score", "Checks"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setView(t)}
              className={cn(
                "relative flex-1 rounded-[5px] px-3 py-1.5 text-sm transition-colors",
                view === t
                  ? "font-semibold text-primary"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {view === t && (
                <motion.span
                  layoutId="plan-seg"
                  transition={{ type: "spring", stiffness: 500, damping: 40 }}
                  className="absolute inset-0 -z-10 rounded-[5px] bg-primary/15"
                />
              )}
              {t}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="space-y-2 p-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-6" />
          ))}
        </div>
      ) : !report ? (
        <p className="p-4 text-[12px] text-muted-foreground">
          No swing_pro row for {symbol}. The 200 EMA needs about 210 sessions and is never
          fabricated, so a young listing has no plan rather than a guessed one.
        </p>
      ) : view === "Score" ? (
        <ScorePanel q={card} />
      ) : view === "Checks" ? (
        <ChecksPanel q={checks} />
      ) : (
        <div className="p-4">
          <div
            className={cn(
              "rounded-md border px-3 py-2 text-center",
              decisionTone(decision) === "up"
                ? "border-up/40 bg-up/10"
                : decisionTone(decision) === "down"
                  ? "border-down/40 bg-down/10"
                  : "border-border bg-muted/40",
            )}
          >
            <div
              className={cn(
                "text-[15px] font-semibold tracking-tight",
                TONE_CLASS[decisionTone(decision)],
              )}
            >
              {decision}
            </div>
            <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
              <span className={TONE_CLASS[gradeTone(grade)]}>{grade}</span>
              {score != null && ` · ${score}/100`}
            </div>
          </div>

          <div className="mt-3 space-y-1 rounded-md border border-border bg-background/50 p-3 text-xs">
            <PlanRow k="Entry" v={price(n("entry"))} />
            <PlanRow k="Stop" v={price(n("stop"))} tone="down" />
            <PlanRow k="Target 1" v={price(n("t1"))} tone="up" />
            <PlanRow k="Target 2" v={price(n("t2"))} tone="up" />
            <PlanRow
              k="R : R"
              v={rr == null ? "—" : rr.toFixed(2)}
              tone={rr == null ? undefined : rr >= 2 ? "up" : "down"}
            />
            <PlanRow
              k="Risk"
              v={n("risk_pct") == null ? "—" : `${n("risk_pct")!.toFixed(2)}%`}
            />
            <PlanRow k="ATR" v={price(n("atr"))} />
            <PlanRow k="Volume" v={n("vol_x") == null ? "—" : `${n("vol_x")!.toFixed(2)}x`} />
          </div>

          {rr != null && rr < 2 && (
            <p className="mt-3 rounded-md border border-primary/40 bg-primary/10 p-2.5 text-[11.5px] leading-snug text-muted-foreground">
              Reward-to-risk is {rr.toFixed(2)} — below the 1:2 the framework requires. The levels
              are shown because they are what the rules produced, not because the trade qualifies.
            </p>
          )}

          <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
            No order can be placed from this terminal. Every figure here was computed by Python
            from the daily archive; this panel formats them and decides nothing.
          </p>
        </div>
      )}
    </aside>
  );
}

function PlanRow({ k, v, tone }: { k: string; v: string; tone?: "up" | "down" }) {
  // `flex justify-between`, 12px, muted label against a mono value — their summary-row spec.
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-muted-foreground">{k}</span>
      <span
        className={cn(
          "font-mono tabular-nums",
          tone === "up" && "text-up",
          tone === "down" && "text-down",
        )}
      >
        {v}
      </span>
    </div>
  );
}

export default function ChartPage() {
  return (
    <Suspense
      fallback={
        <div className="p-6">
          <Skeleton className="h-[520px] w-full" />
        </div>
      }
    >
      <ChartInner />
    </Suspense>
  );
}

/** Section 20 — the ten scored parts, as bars. */
function ScorePanel({ q }: { q: UseQueryResult<Scorecard> }) {
  if (q.isPending) {
    return (
      <div className="space-y-2 p-4">
        {Array.from({ length: 10 }).map((_, i) => (
          <Skeleton key={i} className="h-6" />
        ))}
      </div>
    );
  }
  if (!q.data) {
    return <p className="p-4 text-xs text-muted-foreground">No scorecard for this symbol.</p>;
  }
  return (
    <div className="space-y-2.5 p-4">
      {q.data.parts.map((p, i) => (
        <div key={p.name} className="space-y-1">
          <div className="flex items-baseline justify-between gap-2 text-xs">
            <span className="truncate text-muted-foreground">{p.name}</span>
            <span className="shrink-0 font-mono tabular-nums">
              {p.got}
              <span className="text-muted-foreground">/{p.max}</span>
            </span>
          </div>
          <div className="h-1 overflow-hidden rounded-full bg-muted">
            <motion.div
              initial={{ width: 0 }}
              animate={{ width: `${(p.got / p.max) * 100}%` }}
              transition={{ delay: i * 0.03, duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
              className={cn(
                "h-full rounded-full",
                p.got / p.max >= 0.75
                  ? "bg-up"
                  : p.got / p.max >= 0.4
                    ? "bg-primary"
                    : "bg-muted-foreground/50",
              )}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

/** Section 21 — the fifteen questions. Q9 and its siblings invert: YES is the warning. */
function ChecksPanel({ q }: { q: UseQueryResult<Questions> }) {
  if (q.isPending) {
    return (
      <div className="space-y-2 p-4">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-5" />
        ))}
      </div>
    );
  }
  if (!q.data) {
    return <p className="p-4 text-xs text-muted-foreground">No checks for this symbol.</p>;
  }
  return (
    <div className="divide-y divide-border/60">
      {q.data.answers.map((a) => {
        const warn = q.data!.warning_questions.includes(a.n);
        const bad = warn && a.answer === true;
        const good = typeof a.answer === "boolean" && (warn ? !a.answer : a.answer);
        return (
          <div key={a.n} className="flex items-start gap-2 px-4 py-2 text-xs">
            <span
              className={cn(
                "mt-0.5 size-1.5 shrink-0 rounded-full",
                bad ? "bg-primary" : good ? "bg-up" : "bg-muted-foreground/50",
              )}
            />
            <div className="min-w-0 leading-snug">
              <span className="text-muted-foreground">{a.question}</span>
              {a.kind === "text" && <div className="mt-0.5">{String(a.answer)}</div>}
              {warn && <span className="ml-1 font-mono text-[10px] uppercase text-primary">yes = bad</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}


/**
 * The market under the chart. Renders through the shared SectorTreemap so it cannot drift from
 * the /admin/heatmap page — which is what happened when the option lived in both files.
 *
 * Clicking a rectangle loads that sector's INDEX into the chart and opens its constituents in
 * the rail: one click from "banks are up" to the bank index charted with every bank beside it.
 */
function SectorStrip({ q }: { q: UseQueryResult<Heatmap> }) {
  const router = useRouter();
  const setDrill = useRail((s) => s.setDrill);
  const sectors = q.data?.sectors ?? [];

  const items = useMemo(
    () =>
      sectors.map((s) => ({
        name: s.sector,
        pct: s.pct,
        turnover: s.turnover,
        note: `${s.count} scrip${s.index ? ` · click to open ${s.index}` : ""}`,
      })),
    [sectors],
  );

  if (q.isPending) return <Skeleton className="m-2 h-[calc(100%-1rem)]" />;
  if (!sectors.length) {
    return <p className="p-3 text-xs text-muted-foreground">Sector data unavailable.</p>;
  }

  return (
    <SectorTreemap
      items={items}
      height="100%"
      sat={3}
      onSelect={(name) => {
        const sec = sectors.find((x) => x.sector === name);
        if (!sec?.index) return;
        setDrill(sec.index);
        router.push(`/admin/chart?symbol=${encodeURIComponent(sec.index)}`);
      }}
    />
  );
}
