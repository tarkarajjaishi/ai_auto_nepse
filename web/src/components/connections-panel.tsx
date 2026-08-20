"use client";

import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, CircleDashed, KeyRound, Loader2 } from "lucide-react";
import { useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { api, qk, type Connection } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Are the two saved broker logins still working?
 *
 * Status only — this panel cannot sign you in, and says so instead of showing a form that would
 * not work. The logins live on the server (`Master_data/*_login.txt`), so a browser has nothing
 * to contribute; and the API has no write verb at all, deliberately (see api/auth.py).
 *
 * Testing is a button rather than automatic: a NAASA probe is a full OAuth login, several HTTP
 * calls deep, and running it on every page load would make this screen feel hung. Untested is
 * shown as "not tested", never as a tick — an untested login that has silently lapsed is exactly
 * the thing this panel exists to catch.
 */
export function ConnectionsPanel() {
  const [probe, setProbe] = useState(false);
  const q = useQuery({
    queryKey: qk.auth(probe),
    queryFn: ({ signal }) => api.auth(probe, signal),
    retry: false,
  });

  return (
    <section className="rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2.5">
        <KeyRound className="size-3.5 text-muted-foreground" />
        <h3 className="text-[13px] font-semibold tracking-tight">Broker connections</h3>
        <button
          onClick={() => setProbe(true)}
          disabled={q.isFetching}
          className={cn(
            "ml-auto rounded-md border border-border px-2 py-0.5 text-[12px] transition-colors",
            q.isFetching
              ? "text-muted-foreground"
              : "text-foreground hover:bg-accent",
          )}
        >
          {q.isFetching && probe ? (
            <span className="flex items-center gap-1.5">
              <Loader2 className="size-3 animate-spin" /> Testing…
            </span>
          ) : (
            "Test both"
          )}
        </button>
      </div>

      <div className="grid gap-px bg-border md:grid-cols-2">
        <Row name="NAASA" label="Trading account, live feed and order book" c={q.data?.naasa}
             loading={q.isPending} />
        <Row name="SmartWealthPro" label="Corporate actions, lock-ins, fundamentals"
             c={q.data?.swp} loading={q.isPending} />
      </div>

      <p className="border-t border-border px-4 py-2.5 text-[12px] text-muted-foreground">
        Signing in is not possible from here. Both logins are stored on the server, not in your
        browser — that is what lets the scheduled jobs and the live feed share one session — and
        this API has no write route by design. To change a login, sign in on the Streamlit app
        under <span className="font-mono">NAASA account</span> or{" "}
        <span className="font-mono">SmartWealthPro</span> with Remember me.
      </p>
    </section>
  );
}

function Row({
  name,
  label,
  c,
  loading,
}: {
  name: string;
  label: string;
  c?: Connection;
  loading: boolean;
}) {
  if (loading || !c) {
    return (
      <div className="bg-card p-4">
        <Skeleton className="h-5 w-40" />
      </div>
    );
  }
  // Three states, never two: working / broken / not tested. Collapsing "not tested" into a tick
  // is how a lapsed login goes unnoticed for weeks.
  const state = !c.configured ? "none" : !c.probed ? "unknown" : c.ok ? "ok" : "bad";
  const Icon = state === "ok" ? CheckCircle2 : state === "bad" ? CircleAlert : CircleDashed;
  const tone =
    state === "ok" ? "text-up" : state === "bad" ? "text-down" : "text-muted-foreground";

  return (
    <div className="bg-card p-4">
      <div className="flex items-center gap-2">
        <Icon className={cn("size-4 shrink-0", tone)} />
        <span className="text-[13px] font-medium">{name}</span>
        <span className={cn("ml-auto font-mono text-[11px]", tone)}>
          {state === "ok"
            ? "working"
            : state === "bad"
              ? "not working"
              : state === "none"
                ? "no login saved"
                : "not tested"}
        </span>
      </div>
      <p className="mt-1 text-[12px] text-muted-foreground">{label}</p>
      <p className="mt-2 text-[12px]">{c.detail}</p>
      {c.session_saved && c.session_age_s != null && (
        <p className="mt-1 font-mono text-[11px] text-muted-foreground">
          cached session {age(c.session_age_s)} old
        </p>
      )}
    </div>
  );
}

function age(s: number) {
  if (s < 90) return `${s}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  if (s < 172800) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}
