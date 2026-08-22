"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { Inbox, Mail, MessageCircle, Phone, Search } from "lucide-react";

import { api, qk, type AccessRequest } from "@/lib/api";

/** `+977` + `9801234567` -> `+9779801234567`, which is what a dialler and wa.me both want. */
function e164(code: string, number: string) {
  return `${code}${number}`.replace(/[^\d+]/g, "");
}

/**
 * Leads from the landing page's Request-For-Access form.
 *
 * Not a BoardPage: those are the eleven pre-computed .txt boards, and every part of that
 * component is about a trading session — freshness banners, a rebuild button keyed into
 * jobs.SCRIPTS, a live-quote overlay on a `symbol` column. None of that means anything for a
 * list of people who filled in a form. This is the plain-table shape the cron and account
 * pages use.
 */
export default function AccessRequestsPage() {
  const [q, setQ] = useState("");

  const query = useQuery({
    queryKey: qk.accessRequests,
    queryFn: ({ signal }) => api.accessRequests(signal),
    // A form nobody is watching does not need a poll; a minute is enough that the page is
    // never obviously wrong while somebody has it open during a campaign.
    refetchInterval: 60_000,
  });

  const rows = useMemo(() => {
    // `n` is the row's ordinal in the append-only file — read_all() reverses, so it counts down.
    // It is the only identity that survives a PREPEND: a new lead shifts the array index of
    // every existing row, and an index in the key unmounts the whole table instead of
    // inserting one <tr>.
    const all = (query.data?.rows ?? []).map((r, i, a) => ({ ...r, n: a.length - i }));
    const needle = q.trim().toLowerCase();
    if (!needle) return all;
    // Every column, AND the numbers in the form the table actually shows. The filter used to
    // join the raw `phone` only, so copying "+9779801234567" out of the row you are looking at
    // and pasting it into the box returned nothing — the one search anybody would actually try.
    return all.filter((r) =>
      [
        r.full_name,
        r.email,
        r.place,
        r.received_at,
        r.whatsapp,
        e164(r.whatsapp_code, r.whatsapp),
      ]
        .join(" ")
        .toLowerCase()
        .includes(needle),
    );
  }, [query.data, q]);

  return (
    <div className="p-4 sm:p-6">
      <header className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <Inbox className="size-[18px] text-primary" />
          <h1 className="font-heading text-[17px] font-semibold tracking-tight">
            Access Requests
          </h1>
        </div>

        <span className="font-mono text-[12px] text-muted-foreground">
          {query.data ? `${query.data.count} total` : "—"}
          {q && query.data ? ` · ${rows.length} shown` : ""}
        </span>

        <div className="relative w-full sm:ml-auto sm:w-auto">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-[14px] -translate-y-1/2 text-muted-foreground" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search name, email, city, number"
            className="h-9 w-full rounded-md border border-border bg-background pl-8 pr-3 text-[13px] outline-none focus:border-primary sm:h-8 sm:w-64"
          />
        </div>
      </header>

      <p className="mb-4 max-w-[70ch] text-[12px] leading-relaxed text-muted-foreground">
        Submitted from the public landing page. Newest first. These are the only rows in this
        terminal a stranger can create, so treat the contents as untrusted text — they are
        rendered, never executed, and the phone numbers are stored as digits.
      </p>

      {/* ONE markup tree, two layouts. Below lg the table parts are re-declared as blocks and
          each row becomes a card, so the same <td> carries the value on both surfaces and there
          is no second copy of the data to drift. Measured first: at 375px the five columns made
          the table 851px wide and the whole page scrolled sideways to read one lead. */}
      {/* The switch is lg (1024), not md. Measured at exactly 768: the table comes back and
          immediately needs 876px, so a tablet got a table that scrolled the page sideways —
          the very thing this was meant to fix. Cards read perfectly well at 768.
          overflow-x-auto is the backstop above that: a freakishly long value scrolls inside
          this box instead of widening the page. */}
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-[13px]">
          <thead className="hidden lg:table-header-group">
            <tr className="border-b border-border font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              <th className="px-4 py-2 text-left font-medium">Received</th>
              <th className="px-4 py-2 text-left font-medium">Name</th>
              <th className="px-4 py-2 text-left font-medium">Living in</th>
              <th className="px-4 py-2 text-left font-medium">Email</th>
              <th className="px-4 py-2 text-left font-medium">WhatsApp</th>
            </tr>
          </thead>
          <tbody className="block lg:table-row-group">
            {rows.map((r, i) => (
              <Row key={r.n} r={r} i={i} />
            ))}
          </tbody>
        </table>

        {query.isPending && (
          <p className="p-4 text-[13px] text-muted-foreground">Loading…</p>
        )}
        {query.isError && (
          <p className="p-4 text-[13px] text-muted-foreground">
            {/* A 401 here means the session lapsed, and nginx answers it with an HTML body — so
                api.ts finds no JSON `error` and falls back to the status line. Printing that
                raw gave "401 Unauthorized" in the same muted grey as "Loading…", which reads
                as an empty table rather than as "sign in again". */}
            {(query.error as { status?: number }).status === 401
              ? "Your session has expired. Reload the page to sign in again."
              : (query.error as Error).message}
          </p>
        )}
        {!query.isPending && !query.isError && rows.length === 0 && (
          <p className="p-4 text-[13px] text-muted-foreground">
            {query.data?.count
              ? "Nothing matches that search."
              : "No requests yet. They appear here the moment somebody submits the form."}
          </p>
        )}
      </div>
    </div>
  );
}

/** The column header, repeated per cell — but only where the header row is hidden. */
function Label({ children }: { children: React.ReactNode }) {
  return (
    <span className="mr-2 inline-block w-[74px] shrink-0 align-top font-mono text-[10px] uppercase tracking-wider text-muted-foreground lg:hidden">
      {children}
    </span>
  );
}

function Row({ r, i }: { r: AccessRequest; i: number }) {
  const wa = e164(r.whatsapp_code, r.whatsapp);
  return (
    <motion.tr
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ delay: Math.min(i, 20) * 0.02 }}
      /* flex column on a phone so `order` can put the NAME first — the table's own order leads
         with the timestamp, which is the least useful thing to read first on a card */
      className="flex flex-col gap-1 border-b border-border/50 px-4 py-3.5 last:border-0 lg:table-row lg:gap-0 lg:px-0 lg:py-0"
    >
      <td className="order-2 block font-mono text-[11px] text-muted-foreground lg:order-none lg:table-cell lg:whitespace-nowrap lg:px-4 lg:py-2 lg:text-[12px]">
        {r.received_at}
      </td>

      <td className="order-1 block text-[15px] font-medium lg:order-none lg:table-cell lg:px-4 lg:py-2 lg:text-[13px] lg:font-normal">
        {r.full_name}
      </td>

      <td className="order-3 mt-1.5 block text-muted-foreground lg:order-none lg:mt-0 lg:table-cell lg:px-4 lg:py-2">
        <Label>Living in</Label>
        {r.place}
      </td>

      <td className="order-4 block lg:order-none lg:table-cell lg:px-4 lg:py-2">
        <Label>Email</Label>
        {/* mailto/wa.me, because the only thing anyone does with a lead is contact it */}
        <a
          href={`mailto:${r.email}`}
          className="inline-flex min-w-0 items-center gap-1.5 hover:text-primary"
        >
          <Mail className="size-[13px] shrink-0 text-muted-foreground" />
          {/* wraps on a card, truncates in the column. An address has no spaces, so break-all
              is the only thing that will wrap it — and truncating it on the surface that has
              room to show it is the wrong way round. */}
          <span className="break-all lg:truncate">{r.email}</span>
        </a>
      </td>

      <td className="order-5 block lg:order-none lg:table-cell lg:whitespace-nowrap lg:px-4 lg:py-2">
        <Label>WhatsApp</Label>
        <a
          href={`https://wa.me/${wa.replace("+", "")}`}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 font-mono tabular-nums hover:text-primary"
        >
          <MessageCircle className="size-[13px] shrink-0 text-muted-foreground" />
          {wa}
        </a>
      </td>
    </motion.tr>
  );
}
