"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { useState } from "react";

import { api, type Row, type Setup } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * One candidate's evidence, laid out the way the Streamlit page lays it out.
 *
 * The point of this card is not the verdict — it is that every claim shows its arithmetic. A
 * screen that says "PROVEN" and nothing else is asking to be believed; this one says which three
 * tests were run, what each threshold is, and what this stock actually scored, so a reader can
 * disagree with it.
 *
 * Nothing here is computed from prices. Every number is a column of `operator_scan.txt`, written
 * by `operator_scan.py`; this file formats them and decides nothing.
 */

function n(row: Row, key: string): number {
  const v = row[key];
  if (typeof v === "number") return v;
  const f = parseFloat(String(v ?? ""));
  return Number.isFinite(f) ? f : 0;
}

function has(row: Row, key: string): boolean {
  const v = row[key];
  return v !== null && v !== undefined && String(v).trim() !== "" && String(v) !== "None";
}

const int = (v: number) =>
  v.toLocaleString(undefined, { maximumFractionDigits: 0, signDisplay: "exceptZero" });
const plain = (v: number) => Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 });
const fmtPx = (v: number | null | undefined) =>
  v == null ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: 2 });

/** A "show the math" row: what was measured, the arithmetic, and the result against its threshold. */
function Calc({
  rows,
  tone,
}: {
  rows: { label: string; formula: string; result: string; ok?: boolean; note?: string }[];
  tone: "pass" | "fail" | "neutral";
}) {
  return (
    <div
      className={cn(
        "ml-5 mt-1 space-y-1 border-l-2 py-1.5 pl-3 font-mono text-[11px] leading-relaxed",
        tone === "pass"
          ? "border-up/60 bg-up/5"
          : tone === "fail"
            ? "border-down/60 bg-down/5"
            : "border-border bg-muted/30",
      )}
    >
      {rows.map((r) => (
        <div key={r.label} className="flex items-baseline gap-2.5">
          <span className="min-w-[104px] shrink-0 text-muted-foreground">{r.label}</span>
          <span className="flex-1 truncate text-muted-foreground/80">{r.formula}</span>
          <span
            className={cn(
              "shrink-0 text-right font-semibold",
              r.ok === undefined ? "" : r.ok ? "text-up" : "text-down",
            )}
          >
            {r.result}
          </span>
          {r.note && <span className="w-12 shrink-0 text-right text-muted-foreground">{r.note}</span>}
        </div>
      ))}
    </div>
  );
}

function Leg({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <p className="flex gap-2 text-[13px] leading-relaxed">
      <span className={cn("shrink-0", ok ? "text-up" : "text-muted-foreground/50")}>
        {ok ? "✓" : "▫"}
      </span>
      <span>{children}</span>
    </p>
  );
}

export function OperatorCard({ row, defaultOpen }: { row: Row; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);

  const side = String(row.side ?? "");
  const buy = side === "BUY";
  const verb = buy ? "buying" : "selling";
  const proof = n(row, "proof");
  const fok = row.floor_ok === "yes";
  const cok = row.chart_ok === "yes";
  const pok = row.pos_ok === "yes";

  const reg = n(row, "regular_pct");
  const accel = n(row, "accel");
  const dom = n(row, "vol_dom");
  const pf = n(row, "pct_float");
  const n1 = n(row, "net_1m");
  const n2 = n(row, "net_3w");
  const n3 = n(row, "net_3d");
  const ad = n(row, "ad_trend");
  const ud = n(row, "updown_vol");
  const pchg = n(row, "price_chg");
  const pubsh = n(row, "pub_shares");
  const bbuy = n(row, "broker_buy");
  const tape = n(row, "tape_vol");
  const actd = n(row, "active_days");
  const samed = n(row, "same_days");
  const n3m = n(row, "net_3m");
  const pct3m = n(row, "pct_3m");
  const coord2 = n(row, "coord2_pct");
  const adDays = n(row, "accum_days");

  const longCampaign = Math.abs(pct3m) >= Math.abs(pf) * 1.5;
  const coordinated = Math.abs(coord2) >= Math.abs(pf) * 1.6 && Math.abs(coord2 - pf) >= 2;

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2.5 px-4 py-2.5 text-left transition-colors hover:bg-accent"
      >
        <ChevronRight
          className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-90")}
        />
        <span className={cn("size-2 shrink-0 rounded-full", buy ? "bg-up" : "bg-down")} />
        <span className="font-semibold">{String(row.symbol)}</span>
        <span className="font-mono text-[12px] text-muted-foreground">
          broker {String(row.broker)}
        </span>
        <span
          className={cn(
            "ml-auto rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide",
            proof === 3 ? "bg-up/15 text-up" : "bg-muted text-muted-foreground",
          )}
        >
          {String(row.verdict ?? "")}
        </span>
        <span className="font-mono text-[11px] text-muted-foreground">{proof}/3</span>
      </button>

      {open && (
        <div className="space-y-3 border-t border-border px-4 py-3">
          <p className="text-[13px] font-semibold">
            {String(row.symbol)}: {proof} of 3 independent sources agree
          </p>

          {/* ── 1 · who ─────────────────────────────────────────────────────────────── */}
          <div>
            <Leg ok={fok}>
              <strong>1 · Floorsheet (who)</strong> — broker {String(row.broker)} is the dominant
              net {buy ? "buyer" : "seller"}: <strong>{reg.toFixed(0)}%</strong> of days the same
              way, <strong>{dom.toFixed(0)}%</strong> of the whole tape, still going (
              {accel.toFixed(1)}× pace), 1m <strong>{int(n1)}</strong> → 3w{" "}
              <strong>{int(n2)}</strong> → 3d <strong>{int(n3)}</strong>
            </Leg>
            <Calc
              tone={fok ? "pass" : "fail"}
              rows={[
                { label: "buys same way", formula: `${samed}/${actd} days`, result: `${reg.toFixed(0)}%`, ok: reg >= 70, note: "≥70" },
                { label: "share of tape", formula: `${plain(bbuy)} ÷ ${plain(tape)}`, result: `${dom.toFixed(0)}%`, ok: dom >= 15, note: "≥15" },
                { label: "still going", formula: `(${int(n3)}/3) ÷ (${int(n1)}/20)`, result: `${accel.toFixed(2)}×`, ok: accel >= 0.7, note: "≥0.7" },
                { label: "cascade 1m·3w·3d", formula: `${int(n1)} · ${int(n2)} · ${int(n3)}`, result: fok ? "all one way" : "mixed", ok: fok },
              ]}
            />
          </div>

          {/* ── 2 · the tape ────────────────────────────────────────────────────────── */}
          <div>
            <Leg ok={cok}>
              <strong>2 · Chart / price + volume</strong> (the tape, no broker data) — A/D line{" "}
              <strong>{ad > 0 ? "rising" : "falling"}</strong> ({ad >= 0 ? "+" : ""}
              {ad.toFixed(2)}), up-day vs down-day volume <strong>{ud.toFixed(1)}×</strong>, price{" "}
              <strong>
                {pchg >= 0 ? "+" : ""}
                {pchg.toFixed(1)}%
              </strong>{" "}
              over the month → the tape {cok ? "confirms" : "does NOT confirm"} {verb}
            </Leg>
            <Calc
              tone={cok ? "pass" : "fail"}
              rows={[
                { label: "A/D slope", formula: ad > 0 ? "rising" : "falling", result: `${ad >= 0 ? "+" : ""}${ad.toFixed(2)}`, ok: ad > 0, note: ">0" },
                { label: "up ÷ down vol", formula: "up-day vs down-day", result: `${ud.toFixed(2)}×`, ok: ud >= 1, note: "≥1.0" },
                { label: "month price", formula: "close-to-close", result: `${pchg >= 0 ? "+" : ""}${pchg.toFixed(1)}%`, ok: pchg > -8, note: ">−8" },
              ]}
            />
          </div>

          {/* ── 3 · size ────────────────────────────────────────────────────────────── */}
          <div>
            <Leg ok={pok}>
              <strong>3 · Position (size)</strong> — the 1-month net is{" "}
              <strong>
                {pf >= 0 ? "+" : ""}
                {pf.toFixed(1)}%
              </strong>{" "}
              of the free float (the float is only {n(row, "float_pct").toFixed(0)}% of the company)
            </Leg>
            <Calc
              tone={pok ? "pass" : "fail"}
              rows={[
                { label: "net vs float", formula: `${int(n1)} ÷ ${plain(pubsh)}`, result: `${pf >= 0 ? "+" : ""}${pf.toFixed(1)}%`, ok: Math.abs(pf) >= 3, note: "≥3" },
                { label: "free float", formula: `${plain(pubsh)} shares`, result: `${n(row, "float_pct").toFixed(0)}% of co.` },
              ]}
            />
          </div>

          {/* ── context ─────────────────────────────────────────────────────────────── */}
          <div>
            <p className="text-[13px] leading-relaxed">
              <strong>Campaign length</strong> — over <strong>3 months</strong> this broker&apos;s
              net is <strong>{int(n3m)}</strong> ({pct3m >= 0 ? "+" : ""}
              {pct3m.toFixed(1)}% of float), against <strong>{int(n1)}</strong> in the last month.{" "}
              {longCampaign ? (
                <>This is a <strong>long-running</strong> campaign — it has been building for months.</>
              ) : (
                <>Most of it is <strong>recent</strong> — a fresh one-month move, not a long campaign.</>
              )}
            </p>
            <Calc
              tone="neutral"
              rows={[
                { label: "3-month net", formula: `${int(n3m)} ÷ ${plain(pubsh)}`, result: `${pct3m >= 0 ? "+" : ""}${pct3m.toFixed(1)}%` },
                { label: "vs 1-month", formula: int(n1), result: `3m ÷ 1m = ${(n1 ? n3m / n1 : 0).toFixed(1)}×` },
              ]}
            />
          </div>

          <div>
            <p className="text-[13px] leading-relaxed">
              <strong>Coordination</strong> — the top two brokers on this side hold{" "}
              <strong>
                {coord2 >= 0 ? "+" : ""}
                {coord2.toFixed(1)}%
              </strong>{" "}
              of float between them against{" "}
              <strong>
                {pf >= 0 ? "+" : ""}
                {pf.toFixed(1)}%
              </strong>{" "}
              for this one.{" "}
              {coordinated ? (
                <>A <strong>second broker</strong> is moving the same way — possibly split or coordinated.</>
              ) : (
                <>No meaningful second broker — this is a <strong>single-broker</strong> campaign.</>
              )}
            </p>
            <Calc
              tone="neutral"
              rows={[
                { label: "top-2 brokers", formula: "combined net ÷ float", result: `${coord2 >= 0 ? "+" : ""}${coord2.toFixed(1)}%` },
                { label: "this broker", formula: "alone", result: `${pf >= 0 ? "+" : ""}${pf.toFixed(1)}%  ·  gap ${(coord2 - pf) >= 0 ? "+" : ""}${(coord2 - pf).toFixed(1)}pp` },
              ]}
            />
          </div>

          <div>
            <p className="text-[13px] leading-relaxed">
              <strong>How hard they are cornering it</strong> — broker {String(row.broker)}&apos;s
              net equals <strong>{adDays.toFixed(1)} days</strong> of this stock&apos;s entire
              average volume:{" "}
              {adDays >= 5 ? (
                <>a <strong>heavy, aggressive</strong> grab</>
              ) : adDays >= 2 ? (
                <>a <strong>moderate</strong> position</>
              ) : (
                <>a <strong>light</strong> position</>
              )}
              . The higher this is, the more of the tradeable liquidity one hand is quietly holding.
            </p>
            <Calc
              tone={adDays >= 5 ? "pass" : "neutral"}
              rows={[
                { label: "days to grab", formula: `${plain(Math.abs(n1))} × 20 ÷ ${plain(tape)}`, result: `${adDays.toFixed(1)} days`, ok: adDays >= 5 ? true : undefined },
              ]}
            />
          </div>

          {has(row, "rel_str") && <RelativeStrength row={row} buy={buy} />}

          <TradeSetup symbol={String(row.symbol)} />
          <BrokerLedger symbol={String(row.symbol)} broker={String(row.broker)} />
          {has(row, "lockin_expiry") && (
            <p className="text-[12px] text-muted-foreground">
              Lock-in expires <span className="font-mono">{String(row.lockin_expiry)}</span>
              {has(row, "lockin_days") && <> · {n(row, "lockin_days").toFixed(0)} days away</>} —
              promoter shares becoming sellable is a supply event with a date on it.
            </p>
          )}
          {has(row, "profit_yoy") && (
            <p className="text-[12px] text-muted-foreground">
              Earnings ({String(row.earn_period ?? "latest quarter")}): net profit{" "}
              {int(n(row, "profit_yoy"))}% YoY
              {Math.abs(n(row, "profit_yoy")) > 300 &&
                " — but off a small or volatile base, so read the growth number with care"}
              .
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * What happened after the LAST trigger.
 *
 * Everything else on this panel is a forecast. This is the only part that reports a result, and it
 * reports the losses too — a screen that shows its predictions and hides its outcomes is not
 * evidence, it is advertising.
 */
function Outcome({ o }: { o: NonNullable<Setup["outcome"]> }) {
  const said: Record<string, { text: string; tone: string }> = {
    t2: { text: "Both targets reached — full winner", tone: "text-up" },
    t1: { text: "Target 1 reached", tone: "text-up" },
    stopped: { text: "Stopped out — the trade lost", tone: "text-down" },
    open: { text: "Still open — neither target nor stop hit yet", tone: "text-muted-foreground" },
  };
  const v = said[o.verdict] ?? said.open;
  return (
    <div className="border-t border-border px-3 py-2">
      <p className="text-[12px] leading-snug">
        <strong>Did it work?</strong> From that entry (Rs {fmtPx(o.entry)}) →{" "}
        <strong className={v.tone}>{v.text}</strong> · best move since{" "}
        <strong className={o.peak_pct >= 0 ? "text-up" : "text-down"}>
          {o.peak_pct >= 0 ? "+" : ""}
          {o.peak_pct.toFixed(1)}%
        </strong>
      </p>
      <Calc
        tone={o.verdict === "stopped" ? "fail" : o.verdict === "open" ? "neutral" : "pass"}
        rows={[
          { label: "target 1", formula: `Rs ${fmtPx(o.t1)}`, result: o.t1_date ?? "not reached", ok: Boolean(o.t1_date) },
          { label: "target 2", formula: `Rs ${fmtPx(o.t2)}`, result: o.t2_date ?? "not reached", ok: Boolean(o.t2_date) },
          { label: "stop", formula: `Rs ${fmtPx(o.stop)}`, result: o.sl_date ?? "not hit", ok: !o.sl_date },
        ]}
      />
    </div>
  );
}

/**
 * Is the move this stock's own, or is the whole sector doing it?
 *
 * The distinction is the difference between "someone is working this stock" and "banks were up
 * this month". A broker can look dominant in a name that is simply riding its sector.
 */
function RelativeStrength({ row, buy }: { row: Row; buy: boolean }) {
  const rs = n(row, "rel_str");
  const scg = n(row, "sector_chg");
  const sec = String(row.sector ?? "its sector");
  const pc = n(row, "price_chg");
  const idio = buy ? rs >= 3 : rs <= -3;
  const withSector = Math.abs(rs) < 3;

  return (
    <div>
      <p
        className={cn(
          "rounded-md border px-3 py-2 text-[12.5px] leading-relaxed",
          idio
            ? "border-up/40 bg-up/10"
            : withSector
              ? "border-border bg-muted/40"
              : "border-primary/40 bg-primary/10",
        )}
      >
        {idio ? (
          <>
            <strong>Moving on its own</strong> — this stock is {Math.abs(pc).toFixed(0)}%{" "}
            {pc >= 0 ? "up" : "down"} while its <strong>{sec}</strong> peers are{" "}
            {scg >= 0 ? "+" : ""}
            {scg.toFixed(0)}%, a {rs >= 0 ? "+" : ""}
            {rs.toFixed(0)}pp gap. The move is idiosyncratic rather than sector beta — someone is
            working <em>this</em> stock, which is what an operator looks like.
          </>
        ) : withSector ? (
          <>
            <strong>Moving with its sector</strong> — <strong>{sec}</strong> peers are{" "}
            {scg >= 0 ? "+" : ""}
            {scg.toFixed(0)}% and this is only {rs >= 0 ? "+" : ""}
            {rs.toFixed(0)}pp different. The move may be sector-wide beta rather than a
            single-stock operator — weaker confirmation.
          </>
        ) : (
          <>
            <strong>Going against the flow</strong> — {sec} peers are {scg >= 0 ? "+" : ""}
            {scg.toFixed(0)}% but this is {rs >= 0 ? "+" : ""}
            {rs.toFixed(0)}pp the other way. The broker&apos;s side is not pushing price past
            peers — treat the read with caution.
          </>
        )}
      </p>
      <Calc
        tone={idio ? "pass" : withSector ? "neutral" : "fail"}
        rows={[
          {
            label: "rel strength",
            formula: `${pc >= 0 ? "+" : ""}${pc.toFixed(1)}% − ${scg >= 0 ? "+" : ""}${scg.toFixed(1)}% (${sec})`,
            result: `${rs >= 0 ? "+" : ""}${rs.toFixed(1)}pp`,
            ok: idio,
          },
        ]}
      />
    </div>
  );
}

/**
 * The rows under the claim.
 *
 * "Broker 92 is the dominant net buyer, 95% of days" is a statement about twenty sessions, and a
 * reader should be able to check it session by session rather than take the summary's word. The
 * bars are drawn from the numbers in the same row, so the picture cannot disagree with the table.
 */
/** South-Asian readable form. 4,09,109 shares means nothing until it is "4.09 lakh". */
function words(v: number): string {
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a >= 1e7) return `${sign}${(a / 1e7).toFixed(2)} crore`;
  if (a >= 1e5) return `${sign}${(a / 1e5).toFixed(2)} lakh`;
  if (a >= 1e3) return `${sign}${(a / 1e3).toFixed(1)} thousand`;
  return `${sign}${a.toFixed(0)}`;
}

/** The windows the card's prose refers to — 1 month is 22 sessions, not 30 days. */
const WINDOWS: { label: string; days: number }[] = [
  { label: "7 days", days: 7 },
  { label: "15 days", days: 15 },
  { label: "1 month", days: 22 },
];

function BrokerLedger({ symbol, broker }: { symbol: string; broker: string }) {
  const [win, setWin] = useState(WINDOWS[0]);
  const days = win.days;
  const q = useQuery({
    queryKey: ["ledger", symbol, broker, days],
    queryFn: ({ signal }) => api.ledger(symbol, broker, days, signal),
    retry: false,
  });

  // Newest FIRST, and labelled by how many sessions ago it was: "1d" is today. The card's prose
  // talks about "the last 3 days" and "still going", which only reads against a newest-first list.
  const asc = q.data?.sessions ?? [];
  const rows = [...asc].reverse();
  const scale = Math.max(1, ...rows.map((r) => Math.max(Math.abs(r.net), r.sold)));
  const totals = q.data?.totals;

  return (
    <div className="rounded-md border border-border">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border px-3 py-2">
        <span className="text-[12.5px] font-semibold">
          Broker {broker} in {symbol}, day by day
        </span>
        <span className="ml-auto flex items-center gap-1">
          {WINDOWS.map((w) => (
            <button
              key={w.label}
              onClick={() => setWin(w)}
              className={cn(
                "rounded px-1.5 py-0.5 font-mono text-[11px] transition-colors",
                w.label === win.label
                  ? "bg-primary/15 text-primary"
                  : "text-muted-foreground hover:bg-accent",
              )}
            >
              {w.label}
            </button>
          ))}
        </span>
      </div>

      {q.isPending ? (
        <p className="px-3 py-2 text-[12px] text-muted-foreground">Reading the floorsheet…</p>
      ) : q.isError ? (
        <p className="px-3 py-2 text-[12px] text-muted-foreground">{(q.error as Error).message}</p>
      ) : (
        <>
          <div className="max-h-64 overflow-auto">
            <table className="w-full text-[11.5px]">
              <thead className="sticky top-0 bg-card">
                <tr className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                  <th className="px-3 py-1.5 text-left font-medium">Day</th>
                  <th className="px-3 py-1.5 text-left font-medium">Date</th>
                  <th className="px-2 py-1.5 text-right font-medium">Bought</th>
                  <th className="px-2 py-1.5 text-right font-medium">Sold</th>
                  <th className="px-2 py-1.5 text-right font-medium">Net</th>
                  <th className="px-2 py-1.5 text-right font-medium">vs prev</th>
                  <th className="px-2 py-1.5 text-right font-medium">vs prev %</th>
                  <th className="w-[34%] px-3 py-1.5 text-left font-medium">net · sold</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  // vs_prev arrives against the PREVIOUS session in file order; reversed, the
                  // comparison for row i is with row i+1. Recomputing here would be a second
                  // answer to the same question, so the sign is read from the row it belongs to.
                  const prev = rows[i + 1];
                  const pct =
                    prev && prev.net ? ((r.net - prev.net) / Math.abs(prev.net)) * 100 : null;
                  return (
                  <tr key={r.date} className="border-t border-border/50">
                    <td className="px-3 py-1 font-mono text-muted-foreground">{i + 1}d</td>
                    <td className="px-3 py-1 font-mono text-muted-foreground">{r.date}</td>
                    <td className="px-2 py-1 text-right font-mono tabular-nums">{plain(r.bought)}</td>
                    <td className="px-2 py-1 text-right font-mono tabular-nums">{plain(r.sold)}</td>
                    <td
                      className={cn(
                        "px-2 py-1 text-right font-mono tabular-nums font-semibold",
                        r.net > 0 ? "text-up" : r.net < 0 ? "text-down" : "",
                      )}
                    >
                      {int(r.net)}
                    </td>
                    <td className="px-2 py-1 text-right font-mono tabular-nums text-muted-foreground">
                      {prev ? int(r.net - prev.net) : "—"}
                    </td>
                    <td className="px-2 py-1 text-right font-mono tabular-nums text-muted-foreground">
                      {pct === null ? "—" : `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`}
                    </td>
                    <td className="px-3 py-1">
                      {/* two bars on one baseline: net above, sold below, same scale */}
                      <div className="space-y-0.5">
                        <div className="h-1.5 w-full overflow-hidden rounded-sm bg-muted/40">
                          <div
                            className={cn("h-full", r.net >= 0 ? "bg-up" : "bg-down")}
                            style={{ width: `${(Math.abs(r.net) / scale) * 100}%` }}
                          />
                        </div>
                        <div className="h-1.5 w-full overflow-hidden rounded-sm bg-muted/40">
                          <div
                            className="h-full bg-muted-foreground/50"
                            style={{ width: `${(r.sold / scale) * 100}%` }}
                          />
                        </div>
                      </div>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {totals && (
            <>
              <div className="grid grid-cols-3 gap-px border-t border-border bg-border">
                <Total label="Total buy quantity" v={totals.bought} />
                <Total label="Total sell quantity" v={totals.sold} />
                <Total label="Net kept (buy − sell)" v={totals.net} signed />
              </div>
              {/* "In words" is not decoration here. 4,09,109 shares is a number you skim past;
                  "4.09 lakh" is one you can hold, and this card is asking the reader to judge a
                  size against a company's free float. */}
              <p className="border-t border-border px-3 py-1.5 text-[11.5px] leading-snug text-muted-foreground">
                In words — over the last {win.label}, broker {broker} bought{" "}
                <strong className="text-foreground">{words(totals.bought)}</strong> and sold{" "}
                <strong className="text-foreground">{words(totals.sold)}</strong> — net{" "}
                <strong className={totals.net >= 0 ? "text-up" : "text-down"}>
                  {totals.net >= 0 ? "buying" : "selling"} {words(Math.abs(totals.net))}
                </strong>{" "}
                shares.
              </p>
            </>
          )}
        </>
      )}
    </div>
  );
}

function Total({ label, v, signed }: { label: string; v: number; signed?: boolean }) {
  return (
    <div className="bg-card px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div
        className={cn(
          "font-mono text-[13px] font-semibold tabular-nums",
          signed ? (v >= 0 ? "text-up" : "text-down") : "",
        )}
      >
        {signed ? int(v) : plain(v)} shares
      </div>
      <div className="font-mono text-[10.5px] text-muted-foreground">{words(v)}</div>
    </div>
  );
}

/**
 * What the price chart says about buying this today, independent of the broker evidence above.
 *
 * A separate engine (`trade_setup.py`) on separate inputs: the operator read says somebody is
 * accumulating, and this says whether the chart is anywhere worth entering. They can disagree,
 * and when they do that is information rather than a fault.
 */
function TradeSetup({ symbol }: { symbol: string }) {
  const q = useQuery({
    queryKey: ["setup", symbol],
    queryFn: ({ signal }) => api.setup(symbol, signal),
    retry: false,
  });

  if (q.isPending) {
    return (
      <p className="rounded-md border border-border px-3 py-2 text-[12px] text-muted-foreground">
        Reading the chart setup…
      </p>
    );
  }
  if (q.isError || !q.data) {
    return (
      <p className="rounded-md border border-border px-3 py-2 text-[12px] text-muted-foreground">
        {(q.error as Error)?.message ?? "No chart setup for this symbol."}
      </p>
    );
  }
  const d = q.data;

  return (
    <div className="rounded-md border border-border">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border px-3 py-2">
        <span className="text-[12.5px] font-semibold">Trade setup</span>
        <span
          className={cn(
            "rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide",
            d.signal === "BUY" ? "bg-up/15 text-up" : d.signal === "SELL" ? "bg-down/15 text-down" : "bg-muted text-muted-foreground",
          )}
        >
          {d.signal}
        </span>
        <span className="font-mono text-[11px] text-muted-foreground">
          {d.grade} · {d.score}/100
        </span>
        {d.buy_date && (
          <span
            className={cn(
              "rounded px-1.5 py-0.5 font-mono text-[10px]",
              d.still_buy ? "bg-up/10 text-up" : "bg-muted text-muted-foreground",
            )}
          >
            {d.still_buy ? "still entering" : "trigger passed"}
          </span>
        )}
        <span className="ml-auto font-mono text-[11px] text-muted-foreground">
          session {d.date}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-px bg-border sm:grid-cols-4">
        {[
          { k: "Entry (buy)", v: d.entry, tone: "" },
          { k: "Target 1", v: d.target1, tone: "text-up" },
          { k: "Target 2", v: d.target2, tone: "text-up" },
          { k: "Stop loss", v: d.stop, tone: "text-down" },
        ].map((t) => {
          // The distance from entry is the number a reader actually weighs — "Rs 653" says
          // nothing until you know it is +5.4% away.
          const away = t.v != null && d.entry ? (t.v / d.entry - 1) * 100 : null;
          return (
            <div key={t.k} className="bg-card px-3 py-2">
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{t.k}</div>
              <div className={cn("font-mono text-[13px] font-semibold tabular-nums", t.tone)}>
                Rs {t.v?.toLocaleString(undefined, { maximumFractionDigits: 2 }) ?? "—"}
              </div>
              {away != null && away !== 0 && (
                <div
                  className={cn(
                    "font-mono text-[10.5px] tabular-nums",
                    away >= 0 ? "text-up" : "text-down",
                  )}
                >
                  {away >= 0 ? "+" : ""}
                  {away.toFixed(1)}%
                </div>
              )}
            </div>
          );
        })}
      </div>

      <p className="border-t border-border px-3 py-1.5 font-mono text-[11px] text-muted-foreground">
        risk {d.risk_pct?.toFixed(2)}% per share · reward:risk to T2 ≈ {d.rr?.toFixed(2)}:1 ·
        support {fmtPx(d.support)} / resistance {fmtPx(d.resistance)} · ATR(14) {d.atr?.toFixed(2)}
      </p>

      {d.buy_date && (
        <p className="border-t border-border px-3 py-1.5 text-[12px] leading-snug">
          <strong>Buy signal date</strong> — triggered on{" "}
          <span className="font-mono">{d.buy_date}</span>
          {d.days_active != null &&
            ` (${d.days_active === 0 ? "today" : d.days_active === 1 ? "yesterday" : `${d.days_active} sessions ago`})`}
          {d.buy_close != null && <> at Rs {fmtPx(d.buy_close)}</>}
          {d.runup != null && (
            <>
              . Price is{" "}
              <strong className={d.runup >= 0 ? "text-up" : "text-down"}>
                {d.runup >= 0 ? "+" : ""}
                {d.runup.toFixed(1)}%
              </strong>{" "}
              from the trigger now
            </>
          )}
          .
        </p>
      )}

      {d.outcome && <Outcome o={d.outcome} />}

      {d.still_reason && (
        <p className="border-t border-border px-3 py-1.5 text-[12px] leading-snug text-muted-foreground">
          {d.still_reason}
        </p>
      )}
    </div>
  );
}
