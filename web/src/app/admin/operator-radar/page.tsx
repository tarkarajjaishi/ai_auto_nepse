"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { BoardPage } from "@/components/board-page";
import { OperatorCard } from "@/components/operator-card";
import { api, qk, type Row } from "@/lib/api";

/** How many of the unproven candidates to show before asking. Streamlit stops at ten. */
const PARTIAL_SHOWN = 10;

export default function OperatorRadarPage() {
  return (
    <BoardPage
      board="operator_scan"
      rebuildHint="python operator_scan.py"
      priority={["symbol", "side", "verdict", "proof", "broker", "regular_pct", "vol_dom", "net_1m", "net_3w", "net_3d", "pct_float", "float_pct"]}
      emptyMessage="No symbol currently passes the cascade."
      filters={[
        { label: "Proven (3 of 3)", test: (r) => Number(r.proof ?? 0) === 3 },
        { label: "Accumulating", test: (r) => r.side === "BUY" },
        { label: "Distributing", test: (r) => r.side === "SELL" },
        { label: "Everything", test: () => true },
      ]}
      blurb={
        <>
          A stock is <strong>PROVEN</strong> only when <strong>three independent sources agree</strong>:{" "}
          <strong>(1) Floorsheet</strong> — one broker is the dominant net buyer or seller,
          regularly, still going across 1 month → 3 weeks → last 3 days;{" "}
          <strong>(2) Chart</strong> — price and volume alone (A/D line, up-vs-down volume, no
          broker data) show the same accumulation or distribution; <strong>(3) Position</strong> —
          that broker&apos;s net is a real slice of the free float. The chart leg is a true
          cross-check, so a false broker reading cannot fake a PROVEN. A 60-day total is misleading
          on its own: one name was +103,838 over a month but only +2,622 in the last three days, so
          the buying had already died. Strong candidate, <em>not</em> legal proof — the floorsheet
          shows the broker, never the client.
        </>
      }
    >
      <Evidence />
    </BoardPage>
  );
}

/**
 * The evidence, split by how much of it there is.
 *
 * The Streamlit page leads with this and puts the table underneath, because the table is 43
 * columns wide and answers no question on its own. Splitting proven from partial is the point:
 * "three sources agree" and "one source agrees" are different claims, and a single ranked list
 * silently presents them as the same one with different scores.
 */
function Evidence() {
  const [showAll, setShowAll] = useState(false);
  const q = useQuery({
    queryKey: qk.board("operator_scan"),
    queryFn: ({ signal }) => api.board("operator_scan", signal),
  });

  const { proven, partial } = useMemo(() => {
    const rows = q.data?.rows ?? [];
    const by = (r: Row) => Number(r.proof ?? 0);
    return {
      proven: rows.filter((r) => by(r) === 3),
      partial: rows.filter((r) => by(r) < 3).sort((a, b) => by(b) - by(a)),
    };
  }, [q.data]);

  if (q.isPending || !q.data || !q.data.rows.length) return null;

  // A board written before the evidence columns existed cannot be read this way, and filtering on
  // an absent column would render as "nothing qualifies" — a claim about the market made from a
  // missing field.
  if (!q.data.columns.includes("proof")) {
    return (
      <section className="rounded-lg border border-primary/40 bg-primary/10 px-4 py-3 text-[13px]">
        <p className="font-medium">The evidence cards need a rebuilt board.</p>
        <p className="mt-1 text-muted-foreground">
          This <span className="font-mono">operator_scan.txt</span> predates the three-source
          columns, so the cards cannot be built — which is not the same as nothing qualifying.
          Rebuild it from the Cron page.
        </p>
      </section>
    );
  }

  const shown = showAll ? partial : partial.slice(0, PARTIAL_SHOWN);

  return (
    <section className="space-y-4">
      <div>
        <h3 className="text-[13px] font-semibold tracking-tight">
          Strong lead — all 3 sources agree{" "}
          <span className="font-mono text-muted-foreground">({proven.length})</span>
        </h3>
        {proven.length === 0 ? (
          <p className="mt-1 text-[12px] text-muted-foreground">
            None this session — no stock has floorsheet, chart and position all agreeing. That is
            the normal answer.
          </p>
        ) : (
          <div className="mt-2 space-y-2">
            {proven.map((r) => (
              <OperatorCard key={`${r.symbol}-${r.broker}`} row={r} defaultOpen />
            ))}
          </div>
        )}
      </div>

      <div>
        <h3 className="text-[13px] font-semibold tracking-tight">
          Only some sources agree — not proven{" "}
          <span className="font-mono text-muted-foreground">({partial.length})</span>
        </h3>
        <p className="mt-1 text-[12px] leading-snug text-muted-foreground">
          Usually the floorsheet shows a broker buying or selling but the{" "}
          <strong>chart does not confirm it</strong> — the broker bought while price fell, so the
          tape is not backing them. Look, but do not trust.
        </p>
        <div className="mt-2 space-y-2">
          {shown.map((r) => (
            <OperatorCard key={`${r.symbol}-${r.broker}`} row={r} defaultOpen={false} />
          ))}
        </div>
        {partial.length > PARTIAL_SHOWN && (
          <button
            onClick={() => setShowAll((v) => !v)}
            className="mt-2 rounded-md border border-border px-2.5 py-1 text-[12px] transition-colors hover:bg-accent"
          >
            {showAll
              ? `Show only the first ${PARTIAL_SHOWN}`
              : `Show all ${partial.length}`}
          </button>
        )}
      </div>
    </section>
  );
}
