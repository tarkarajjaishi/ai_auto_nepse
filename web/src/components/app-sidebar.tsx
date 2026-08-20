"use client";

import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { ScrollArea } from "@/components/ui/scroll-area";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api, qk } from "@/lib/api";
import { NAV } from "@/lib/nav";
import { cn } from "@/lib/utils";

/**
 * A dot next to any screen whose board was computed before the bars now on disk.
 *
 * This is the whole staleness lesson made ambient: the Streamlit sidebar knew the archive had
 * moved while the Swing Trader Pro page cheerfully printed an older session as current. Here you
 * can see which screens are behind without opening them.
 */
function StaleDot({ board }: { board?: string }) {
  const { data } = useQuery({
    queryKey: qk.boards,
    queryFn: ({ signal }) => api.boards(signal),
    staleTime: 30_000,
  });
  if (!board) return null;
  const info = data?.boards?.[board as keyof typeof data.boards];
  if (!info || (!info.stale && !info.missing)) return null;
  const missing = info.missing;
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <span
            className={cn(
              "ml-auto size-1.5 shrink-0 rounded-full",
              missing ? "bg-muted-foreground" : "bg-primary",
            )}
          />
        }
      />
      <TooltipContent side="right">
        {missing
          ? "Never built — open it and rebuild"
          : `Computed on ${info.session}; the archive has newer bars`}
      </TooltipContent>
    </Tooltip>
  );
}

export function AppSidebar() {
  const pathname = usePathname();

  return (
    <aside className="hidden w-60 shrink-0 flex-col border-r border-sidebar-border bg-sidebar md:flex">
      <div className="flex h-14 items-center gap-2.5 px-4">
        <div className="grid size-7 place-items-center rounded-md bg-primary font-mono text-[13px] font-bold text-primary-foreground">
          C
        </div>
        <div className="leading-tight">
          <div className="text-[13px] font-semibold tracking-tight">Chukul</div>
          <div className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
            Terminal
          </div>
        </div>
      </div>

      <ScrollArea className="flex-1 px-2 pb-4">
        {NAV.map((group) => (
          <div key={group.group} className="mb-4">
            <div className="px-2.5 pb-1.5 font-mono text-[10px] uppercase tracking-widest text-muted-foreground/70">
              {group.group}
            </div>
            <nav className="space-y-0.5">
              {group.items.map((item) => {
                // exact match for /admin, prefix for the rest — otherwise the Overview link
                // stays highlighted on every child route
                const active =
                  item.href === "/admin"
                    ? pathname === "/admin"
                    : pathname.startsWith(item.href);
                const Icon = item.icon;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={cn(
                      "relative flex items-center gap-2.5 rounded-md px-2.5 py-[7px] text-[13px] transition-colors",
                      active
                        ? "text-sidebar-accent-foreground"
                        : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
                    )}
                  >
                    {active && (
                      // one shared element across items: the pill slides between links instead
                      // of fading out and in, which is what makes the nav feel continuous
                      <motion.span
                        layoutId="nav-active"
                        transition={{ type: "spring", stiffness: 500, damping: 40 }}
                        className="absolute inset-0 -z-10 rounded-md bg-sidebar-accent"
                      />
                    )}
                    <Icon
                      className={cn(
                        "size-[15px] shrink-0",
                        active ? "text-primary" : "text-muted-foreground",
                      )}
                      strokeWidth={2}
                    />
                    <span className="truncate">{item.label}</span>
                    <StaleDot board={item.board} />
                  </Link>
                );
              })}
            </nav>
          </div>
        ))}
      </ScrollArea>
    </aside>
  );
}
