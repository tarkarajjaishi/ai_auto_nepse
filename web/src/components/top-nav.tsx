"use client";

import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { ChevronDown, CircleAlert, CircleCheck, CircleDashed } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { ThemeToggle } from "@/components/theme-toggle";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api, qk } from "@/lib/api";
import { NAV } from "@/lib/nav";
import { cn } from "@/lib/utils";

/**
 * The terminal's top bar: brand, section tabs, live state.
 *
 * The navigation moved up here from the left rail so the rail can hold the instrument list —
 * which is the arrangement every trading terminal converges on, for the reason that you change
 * symbol constantly and section rarely. Sixteen screens do not fit as sixteen tabs, so each of
 * the four groups is one tab that opens its own menu; that is the same shape as the reference's
 * "More" overflow, applied uniformly instead of only to the leftovers.
 */
export function TopNav() {
  const pathname = usePathname();

  return (
    // 56px and bg-background, both measured off the reference: its chrome is TRANSPARENT over
    // one flat #0e1419 ground, separated only by rules. Panels here were bg-card (#161e26),
    // which lifts the header and rail out of the page and is the single biggest reason the two
    // did not look alike. #161e26 belongs to cards, not to chrome.
    <header className="flex h-14 shrink-0 items-center gap-1 border-b border-border bg-background px-3">
      <Link href="/admin" className="mr-2 flex shrink-0 items-center gap-2">
        <span className="grid size-6 place-items-center rounded-md bg-primary font-mono text-[12px] font-bold text-primary-foreground">
          N
        </span>
        <span className="text-[14px] font-semibold tracking-tight">Nepse_ai</span>
      </Link>

      <nav className="flex items-center gap-0.5">
        {NAV.map((group) => {
          // Plain prefix match: every nav route is now at least two segments deep, so none is a
          // prefix of another. Overview used to sit on bare "/admin", which prefixes every admin
          // route, and needed an exact-match special case to stop it lighting up everywhere.
          const active = group.items.some((i) => pathname.startsWith(i.href));
          return (
            <DropdownMenu key={group.group}>
              <DropdownMenuTrigger
                render={
                  <button
                    className={cn(
                      // text-sm / px-3 / py-1.5 / rounded-md — their exact tab metrics (32px tall)
                      "relative flex items-center gap-1 rounded-md px-3 py-1.5 text-sm transition-colors",
                      active
                        ? "font-semibold text-primary"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  />
                }
              >
                {active && (
                  <motion.span
                    layoutId="topnav-active"
                    transition={{ type: "spring", stiffness: 500, damping: 40 }}
                    // brass at 15%, not a neutral accent — the active tab reads as the accent
                    // colour on theirs, and a grey pill was the wrong signal entirely
                    className="absolute inset-0 -z-10 rounded-md bg-primary/15"
                  />
                )}
                {group.group}
                <ChevronDown className="size-3 opacity-60" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-60">
                {group.items.map((item) => {
                  const Icon = item.icon;
                  const here = pathname.startsWith(item.href);
                  return (
                    <DropdownMenuItem key={item.href} render={<Link href={item.href} />}>
                      <Icon
                        className={cn(
                          "size-[15px] shrink-0",
                          here ? "text-primary" : "text-muted-foreground",
                        )}
                      />
                      <div className="min-w-0">
                        <div className={cn("text-[13px]", here && "text-primary")}>
                          {item.label}
                        </div>
                        <div className="truncate text-[11px] text-muted-foreground">
                          {item.hint}
                        </div>
                      </div>
                      <StaleDot board={item.board} />
                    </DropdownMenuItem>
                  );
                })}
              </DropdownMenuContent>
            </DropdownMenu>
          );
        })}
      </nav>

      <div className="ml-auto flex items-center gap-2">
        <ArchivePill />
        <ThemeToggle />
      </div>
    </header>
  );
}

function StaleDot({ board }: { board?: string }) {
  const { data } = useQuery({
    queryKey: qk.boards,
    queryFn: ({ signal }) => api.boards(signal),
    staleTime: 30_000,
  });
  if (!board) return null;
  const info = data?.boards?.[board as keyof NonNullable<typeof data>["boards"]];
  // undated counts as "not known to be current" — a board that cannot say which session
  // it holds must not render as a clean one.
  if (!info || (!info.stale && !info.missing && !info.session_unknown)) return null;
  return (
    <span
      className={cn(
        "ml-auto size-1.5 shrink-0 rounded-full",
        info.missing ? "bg-muted-foreground" : "bg-primary",
      )}
    />
  );
}

/**
 * Session + freshness, in the slot the reference gives its account balance.
 *
 * It earns the position: every number on every screen is downstream of which session the archive
 * holds, and whether the boards have caught up to it. The reference shows equity there because
 * equity is the thing you glance at constantly; here it is the date.
 */
function ArchivePill() {
  const { data, isPending, isError } = useQuery({
    queryKey: qk.boards,
    queryFn: ({ signal }) => api.boards(signal),
    staleTime: 30_000,
  });

  if (isPending) {
    return <CircleDashed className="size-3.5 animate-spin text-muted-foreground" />;
  }
  if (isError || !data) {
    return (
      <Tooltip>
        <TooltipTrigger
          render={
            <span className="flex items-center gap-1.5 rounded-md border border-destructive/40 bg-destructive/10 px-2 py-1 font-mono text-[11px] text-destructive" />
          }
        >
          <CircleAlert className="size-3.5" />
          api offline
        </TooltipTrigger>
        <TooltipContent side="bottom">
          The Python API is not reachable. Start it: <code>python -m api</code>
        </TooltipContent>
      </Tooltip>
    );
  }

  const behind = Object.entries(data.boards)
    .filter(([, b]) => b.stale || b.session_unknown)
    .map(([n]) => n);

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <span className="flex items-center gap-2 rounded-md border border-border bg-background px-2 py-1" />
        }
      >
        <span className="font-mono text-[11px] text-muted-foreground">archive</span>
        <span className="font-mono text-[11px] text-foreground">
          {data.archive_session ?? "none"}
        </span>
        {behind.length ? (
          <span className="flex items-center gap-1 rounded bg-primary/15 px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide text-primary">
            <CircleAlert className="size-3" />
            {behind.length} behind
          </span>
        ) : (
          <span className="flex items-center gap-1 rounded bg-up/15 px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide text-up">
            <CircleCheck className="size-3" />
            synced
          </span>
        )}
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-xs">
        {behind.length
          ? `Archive is at ${data.archive_session}. These boards were computed earlier and need a rebuild: ${behind.join(", ")}.`
          : `Every board matches the archive at ${data.archive_session}.`}
      </TooltipContent>
    </Tooltip>
  );
}
