"use client";

import { Collapsible } from "@base-ui/react/collapsible";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ChevronRight, CircleAlert, Inbox } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, api, qk, type SqSection } from "@/lib/api";
import { TONE_CLASS, type Tone } from "@/lib/format";
import { cn } from "@/lib/utils";

/** The one command that rebuilds this board. Printed in every state that needs it — stale,
 *  never-computed — so the reader is never told a number is wrong without being told the fix. */
const REBUILD = "python -m swing_quantam";

/**
 * The eight verdicts this board can print, and only those.
 *
 * Deliberately NOT `decisionTone` from format.ts: that matches whole words like "BUY ZONE" and
 * would tone "DISTRIBUTION WATCH" and "STRONG EXIT / INVALIDATION" as neutral — a sell verdict
 * rendered in the same grey as NEUTRAL. An unknown string falls through to no colour rather than
 * guessing, so a new verdict added in Python shows up uncoloured instead of miscoloured.
 */
const SIGNAL_TONE: Record<string, Tone> = {
  "STRONG BUY ZONE": "up",
  "BUY ZONE": "up",
  "WATCH / BUILDING": "warn",
  NEUTRAL: "none",
  "HOLD / MONITOR": "none",
  "DISTRIBUTION WATCH": "warn",
  "SELL / REDUCE ZONE": "down",
  "STRONG EXIT / INVALIDATION": "down",
};

function SwingQuantamInner() {
  const params = useSearchParams();
  const router = useRouter();
  const symbol = (params.get("symbol") ?? "NABIL").toUpperCase();

  // Re-seed from the URL when something else changes the symbol — same reason as the floorsheet
  // page: without it the input keeps the old symbol while the panels below belong to the new one.
  const [draft, setDraft] = useState(symbol);
  const [seenSymbol, setSeenSymbol] = useState(symbol);
  if (symbol !== seenSymbol) {
    setSeenSymbol(symbol);
    setDraft(symbol);
  }

  const symbols = useQuery({ queryKey: qk.symbols, queryFn: ({ signal }) => api.symbols(signal) });
  const q = useQuery({
    queryKey: qk.swingQuantam(symbol),
    queryFn: ({ signal }) => api.swingQuantam(symbol, signal),
    // A 404 means "not computed yet", which is an answer. Retrying it three times just delays
    // the sentence that tells the reader how to build it.
    retry: false,
  });

  const known = useMemo(() => new Set(symbols.data?.symbols ?? []), [symbols.data]);

  function go(next: string) {
    const s = next.trim().toUpperCase();
    if (!s || !known.has(s)) return;
    router.push(`/admin/swing-quantam?${new URLSearchParams({ symbol: s })}`);
  }

  const d = q.data;
  const notComputed = q.error instanceof ApiError && q.error.status === 404;

  return (
    <div className="space-y-4 p-4 md:p-6">
      <div className="flex flex-wrap items-center gap-3">
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && go(draft)}
          onBlur={() => go(draft)}
          list="sq-symbols"
          className="h-8 w-40 font-mono text-[13px] uppercase"
          placeholder="Symbol"
        />
        <datalist id="sq-symbols">
          {(symbols.data?.symbols ?? []).map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
        {d && (
          <span className="font-mono text-[11px] text-muted-foreground">
            {d.sections.length} sections
          </span>
        )}
      </div>

      <p className="max-w-4xl text-[13px] leading-relaxed text-muted-foreground">
        Floorsheet only — 3D/7D/15D/30D broker windows, the zones they build, and what survived
        backtest. Every number is <strong>pre-computed</strong> by{" "}
        <span className="font-mono text-foreground">{REBUILD}</span>, so it is only as fresh as the
        last time that ran. Sections start collapsed; click one to open it.
      </p>

      {notComputed && (
        <div className="flex items-start gap-3 rounded-lg border border-border bg-muted/40 p-3.5 text-[13px]">
          <Inbox className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <div className="text-muted-foreground">
            <span className="font-mono text-foreground">{symbol}</span> has not been computed yet —
            there is no file for it, which is not the same as a neutral verdict. Build the board
            once and it will appear: <span className="font-mono text-foreground">{REBUILD}</span>.
          </div>
        </div>
      )}

      {q.isError && !notComputed && (
        <div className="flex items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-[13px]">
          <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" />
          <div>
            <div className="font-medium">Could not load swing_quantam.</div>
            <p className="mt-1 text-muted-foreground">{(q.error as Error).message}</p>
          </div>
        </div>
      )}

      {q.isPending && <LoadingShape />}

      {d && (
        <>
          {/* ── the verdict, and which session it belongs to ─────────────────────────────── */}
          <section className="rounded-lg border border-border bg-card">
            <div className="flex flex-wrap items-start gap-x-8 gap-y-3 p-4">
              <Stat label="Signal">
                <span
                  className={cn(
                    "font-mono text-[15px] font-semibold",
                    TONE_CLASS[SIGNAL_TONE[d.signal] ?? "none"],
                  )}
                >
                  {d.signal}
                </span>
              </Stat>
              <Stat label="Score">
                <span className="font-mono text-[15px] font-semibold tabular-nums">
                  {d.score === null ? "not computed" : `${d.score} / 100`}
                </span>
              </Stat>
              <Stat label="Confidence">
                <span className="font-mono text-[15px]">{d.confidence}</span>
              </Stat>
              <Stat label="Symbol">
                <span className="font-mono text-[15px] font-semibold">{d.symbol}</span>
              </Stat>
              <span className="ml-auto rounded-md bg-primary/15 px-2 py-1 font-mono text-[11px]">
                session {d.session_unknown ? "unknown" : d.session}
              </span>
            </div>
          </section>

          {/* Hard project rule: a pre-computed board that the archive has moved past must SAY so,
              naming both sessions. Presenting a stale read as current is the failure mode this
              project keeps hitting. Wording matches board-page.tsx on purpose. */}
          {/* Checked BEFORE `stale`, because `stale` is false for an undated detail file too and
              the two must not collapse. "I cannot tell" is its own answer, not "current". */}
          {d.session_unknown && (
            <div className="flex items-start gap-3 rounded-lg border border-primary/40 bg-primary/10 p-3.5 text-[13px]">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-primary" />
              <div className="text-muted-foreground">
                This detail file carries no session date, so there is no way to tell how old these
                numbers are — treat every figure below as undated. The archive reaches{" "}
                <span className="font-mono text-foreground">{d.archive_session}</span>. Rebuild with{" "}
                <span className="font-mono text-foreground">{REBUILD}</span>.
              </div>
            </div>
          )}

          {!d.session_unknown && d.stale && (
            <div className="flex items-start gap-3 rounded-lg border border-primary/40 bg-primary/10 p-3.5 text-[13px]">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-primary" />
              <div className="text-muted-foreground">
                Computed on <span className="font-mono text-foreground">{d.session}</span>, but the
                archive already reaches{" "}
                <span className="font-mono text-foreground">{d.archive_session}</span>. Every number
                below is from the earlier session — rebuild with{" "}
                <span className="font-mono text-foreground">{REBUILD}</span>. Before the 15:00 NPT
                close the newest bar is a partial session, so rebuilding mid-day scores an
                unfinished candle.
              </div>
            </div>
          )}

          {/* ── why, and what argues against it ──────────────────────────────────────────── */}
          <section className="rounded-lg border border-border bg-card">
            <h3 className="border-b border-border px-4 py-2.5 text-[13px] font-semibold tracking-tight">
              Why this signal
            </h3>
            <ul className="space-y-1.5 p-4 text-[13px] text-muted-foreground">
              {d.reasons.map((r, i) => (
                <li key={i} className="flex gap-2.5">
                  <span className="mt-[7px] size-1.5 shrink-0 rounded-full bg-primary" />
                  {r}
                </li>
              ))}
            </ul>
          </section>

          {d.warnings.length > 0 && (
            <section className="rounded-lg border border-down/40 bg-down/10">
              <h3 className="flex items-center gap-2 border-b border-down/40 px-4 py-2.5 text-[13px] font-semibold tracking-tight">
                <AlertTriangle className="size-4 shrink-0 text-down" />
                Contradictions and caveats
                <span className="font-mono text-[11px] font-normal text-muted-foreground">
                  {d.warnings.length}
                </span>
              </h3>
              <ul className="space-y-1.5 p-4 text-[13px] text-muted-foreground">
                {d.warnings.map((w, i) => (
                  <li key={i} className="flex gap-2.5">
                    <span className="mt-[7px] size-1.5 shrink-0 rounded-full bg-down" />
                    {w}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* ── the spec, section by section ─────────────────────────────────────────────── */}
          <div className="space-y-2">
            {d.sections.map((s) => (
              <Section key={s.n} section={s} />
            ))}
            {!d.sections.length && (
              <p className="text-[13px] text-muted-foreground">
                This symbol carries a verdict but no sections — the detail tables were not written.
                Rebuild with <span className="font-mono text-foreground">{REBUILD}</span>.
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="mt-1">{children}</div>
    </div>
  );
}

/**
 * One spec section, collapsed until clicked.
 *
 * Base UI's Collapsible rather than hand-rolled `useState`: it is the same primitive library every
 * component under `ui/` already wraps, and it wires the button semantics, `aria-expanded` and the
 * panel id itself. Nothing new is installed.
 */
function Section({ section: s }: { section: SqSection }) {
  return (
    <Collapsible.Root className="overflow-hidden rounded-lg border border-border bg-card">
      <Collapsible.Trigger className="group flex w-full cursor-pointer items-center gap-2 px-4 py-2.5 text-left text-[13px] font-semibold tracking-tight transition-colors hover:bg-accent/40">
        <ChevronRight className="size-3.5 shrink-0 text-muted-foreground transition-transform duration-150 ease-out group-data-panel-open:rotate-90" />
        <span className="font-mono text-[11px] font-normal text-muted-foreground">§{s.n}</span>
        {s.title}
        <span className="ml-auto font-mono text-[11px] font-normal text-muted-foreground">
          {s.rows.length}
        </span>
      </Collapsible.Trigger>
      <Collapsible.Panel className="h-[var(--collapsible-panel-height)] overflow-hidden transition-[height] duration-150 ease-out [&[hidden]:not([hidden='until-found'])]:hidden data-ending-style:h-0 data-starting-style:h-0">
        <div className="border-t border-border">
          {s.note && (
            <p className="border-b border-border/50 px-3 py-2 text-[12px] text-muted-foreground">
              {s.note}
            </p>
          )}
          <table className="w-full text-[13px]">
            <tbody>
              {s.rows.map((r, i) => (
                <tr key={`${r.metric}-${i}`} className="border-b border-border/50 last:border-b-0">
                  <td className="px-3 py-1.5 text-left">
                    {r.metric}
                    {r.note && (
                      <span className="ml-2 text-[11px] text-muted-foreground">{r.note}</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-3 py-1.5 text-right font-mono tabular-nums">
                    {r.value === null || r.value === "" ? "—" : r.value}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!s.rows.length && (
            <p className="px-3 py-3 text-[13px] text-muted-foreground">
              Nothing in this section.
            </p>
          )}
        </div>
      </Collapsible.Panel>
    </Collapsible.Root>
  );
}

/** Shaped like what lands, not a spinner — the header block, then a stack of collapsed bars. */
function LoadingShape() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-[86px] w-full rounded-lg" />
      <Skeleton className="h-[140px] w-full rounded-lg" />
      <div className="space-y-2">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-[42px] w-full rounded-lg" />
        ))}
      </div>
    </div>
  );
}

export default function SwingQuantamPage() {
  return (
    <Suspense
      fallback={
        <div className="p-4 md:p-6">
          <LoadingShape />
        </div>
      }
    >
      <SwingQuantamInner />
    </Suspense>
  );
}
