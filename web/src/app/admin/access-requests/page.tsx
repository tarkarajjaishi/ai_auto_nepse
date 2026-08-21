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
        r.phone,
        r.whatsapp,
        e164(r.country_code, r.phone),
        e164(r.whatsapp_code, r.whatsapp),
      ]
        .join(" ")
        .toLowerCase()
        .includes(needle),
    );
  }, [query.data, q]);

  return (
    <div className="p-6">
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

        <div className="relative ml-auto">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-[14px] -translate-y-1/2 text-muted-foreground" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search name, email, city, number"
            className="h-8 w-64 rounded-md border border-border bg-background pl-8 pr-3 text-[13px] outline-none focus:border-primary"
          />
        </div>
      </header>

      <p className="mb-4 max-w-[70ch] text-[12px] leading-relaxed text-muted-foreground">
        Submitted from the public landing page. Newest first. These are the only rows in this
        terminal a stranger can create, so treat the contents as untrusted text — they are
        rendered, never executed, and the phone numbers are stored as digits.
      </p>

      <div className="rounded-lg border border-border">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="border-b border-border font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              <th className="px-4 py-2 text-left font-medium">Received</th>
              <th className="px-4 py-2 text-left font-medium">Name</th>
              <th className="px-4 py-2 text-left font-medium">Living in</th>
              <th className="px-4 py-2 text-left font-medium">Email</th>
              <th className="px-4 py-2 text-left font-medium">Phone</th>
              <th className="px-4 py-2 text-left font-medium">WhatsApp</th>
            </tr>
          </thead>
          <tbody>
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

function Row({ r, i }: { r: AccessRequest; i: number }) {
  const phone = e164(r.country_code, r.phone);
  const wa = e164(r.whatsapp_code, r.whatsapp);
  return (
    <motion.tr
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ delay: Math.min(i, 20) * 0.02 }}
      className="border-b border-border/50"
    >
      <td className="whitespace-nowrap px-4 py-2 font-mono text-[12px] text-muted-foreground">
        {r.received_at}
      </td>
      <td className="px-4 py-2">{r.full_name}</td>
      <td className="px-4 py-2 text-muted-foreground">{r.place}</td>
      <td className="px-4 py-2">
        {/* mailto/tel/wa.me, because the only thing anyone does with a lead is contact it */}
        <a href={`mailto:${r.email}`} className="inline-flex items-center gap-1.5 hover:text-primary">
          <Mail className="size-[13px] shrink-0 text-muted-foreground" />
          {r.email}
        </a>
      </td>
      <td className="whitespace-nowrap px-4 py-2">
        <a href={`tel:${phone}`} className="inline-flex items-center gap-1.5 font-mono tabular-nums hover:text-primary">
          <Phone className="size-[13px] shrink-0 text-muted-foreground" />
          {phone}
        </a>
      </td>
      <td className="whitespace-nowrap px-4 py-2">
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
