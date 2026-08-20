"use client";

import { useState } from "react";

import { BoardPage } from "@/components/board-page";

/** The script's own defaults, so an untouched page shows exactly what `python swing_master.py` did. */
const DEFAULT_CAPITAL = 100_000;
const DEFAULT_RISK = 1.0;

export default function SwingMasterPage() {
  const [capital, setCapital] = useState(DEFAULT_CAPITAL);
  const [risk, setRisk] = useState(DEFAULT_RISK);

  // Only send parameters once they differ from the script's defaults. Untouched, the page asks
  // for the plain board and shows the file as written — no recomputation, nothing to reconcile.
  const params =
    capital === DEFAULT_CAPITAL && risk === DEFAULT_RISK
      ? undefined
      : { capital, risk };

  return (
    <BoardPage
      board="swing_master"
      rebuildHint="python swing_master.py"
      params={params}
      priority={["symbol", "verdict", "entry", "stop", "target1", "target2", "qty", "cost_rs", "risk_rs", "risk_pct", "rr", "edge_oos", "hold_bars"]}
      emptyMessage="Nothing qualifies. Cash is a position — the rule only fires on volume confirmation."
      blurb={
        <>
          The same rule as Master signal, sized for a book. Quantity is set so a stop-out costs the
          same rupees on every trade — with a 44% base win rate, a fixed loss is the only reliable
          control. <strong>cost_rs</strong> is the position value, and it is shown because risk-based
          sizing fixes the <em>loss</em>, not the <em>position</em>: a 0.23% stop once sized 4.4x
          the whole account while reporting a tidy Rs 1,000 of risk. The size is now capped at the
          cash available. <strong>hold_bars 20</strong> is a real exit — the median NEPSE 20-day
          return is negative, so time in a trade is a cost, not a neutral.
        </>
      }
    >
      <BookSize
        capital={capital}
        risk={risk}
        onCapital={setCapital}
        onRisk={setRisk}
        onReset={() => {
          setCapital(DEFAULT_CAPITAL);
          setRisk(DEFAULT_RISK);
        }}
        changed={params !== undefined}
      />
    </BoardPage>
  );
}

/**
 * Your book, and what you will risk of it per trade.
 *
 * Changing either re-sizes the sheet on the server, through the same `swing_master.size()` the
 * script calls — including its cap at the cash available, which is the part that is easy to get
 * wrong. Nothing is written and no job is spawned: position size is a pure function of the book
 * and the row's own entry and stop, so re-sizing is a recomputation of a read.
 */
function BookSize({
  capital,
  risk,
  onCapital,
  onRisk,
  onReset,
  changed,
}: {
  capital: number;
  risk: number;
  onCapital: (n: number) => void;
  onRisk: (n: number) => void;
  onReset: () => void;
  changed: boolean;
}) {
  return (
    <section className="flex flex-wrap items-end gap-x-6 gap-y-3 rounded-lg border border-border bg-card px-4 py-3">
      <Field label="Book size" hint="Rs">
        <input
          type="number"
          min={10_000}
          max={100_000_000}
          step={10_000}
          value={capital}
          onChange={(e) => onCapital(clamp(Number(e.target.value), 10_000, 100_000_000))}
          className="h-8 w-36 rounded-md border border-border bg-background px-2 font-mono text-[13px] tabular-nums outline-none focus:border-primary/60"
        />
      </Field>

      <Field label="Risk per trade" hint="% of book">
        <input
          type="number"
          min={0.25}
          max={5}
          step={0.25}
          value={risk}
          onChange={(e) => onRisk(clamp(Number(e.target.value), 0.25, 5))}
          className="h-8 w-24 rounded-md border border-border bg-background px-2 font-mono text-[13px] tabular-nums outline-none focus:border-primary/60"
        />
      </Field>

      <div className="flex items-center gap-3 pb-0.5">
        <span className="font-mono text-[12px] text-muted-foreground">
          ≈ Rs {Math.round((capital * risk) / 100).toLocaleString()} at risk per trade
        </span>
        {changed && (
          <button
            onClick={onReset}
            className="rounded-md border border-border px-2 py-0.5 text-[12px] transition-colors hover:bg-accent"
          >
            Back to the file
          </button>
        )}
      </div>

      <p className="basis-full text-[12px] leading-snug text-muted-foreground">
        {changed ? (
          <>
            Quantities, risk and position value are recomputed for this book by the same Python the
            script runs. Every other column is the analysis, which does not depend on how much
            money you have — and the file on disk is untouched.
          </>
        ) : (
          <>
            Showing the sheet exactly as <span className="font-mono">swing_master.py</span> wrote
            it: a Rs {DEFAULT_CAPITAL.toLocaleString()} book at {DEFAULT_RISK}%. Change either
            figure to re-size it.
          </>
        )}
      </p>
    </section>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label} <span className="normal-case tracking-normal">({hint})</span>
      </span>
      {children}
    </label>
  );
}

function clamp(n: number, lo: number, hi: number) {
  return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : lo;
}
