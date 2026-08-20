"use client";

import { useQuery } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, CircleDashed } from "lucide-react";

import { ThemeToggle } from "@/components/theme-toggle";
import { Separator } from "@/components/ui/separator";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api, qk } from "@/lib/api";
import { ALL_NAV_ITEMS } from "@/lib/nav";
import { cn } from "@/lib/utils";
import { usePathname } from "next/navigation";

/** Archive state, always visible. The one number that decides whether anything else is worth
 *  reading — every board is downstream of it. */
function ArchiveBadge() {
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
          render={<span className="flex items-center gap-1.5 font-mono text-[11px] text-destructive" />}
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
    .filter(([, b]) => b.stale)
    .map(([n]) => n);

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <span
            className={cn(
              "flex items-center gap-1.5 font-mono text-[11px]",
              behind.length ? "text-primary" : "text-muted-foreground",
            )}
          />
        }
      >
        {behind.length ? (
          <CircleAlert className="size-3.5" />
        ) : (
          <CircleCheck className="size-3.5 text-up" />
        )}
        {data.archive_session ?? "no archive"}
        {behind.length > 0 && ` · ${behind.length} behind`}
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-xs">
        {behind.length
          ? `Archive is at ${data.archive_session}. These boards were computed earlier and need a rebuild: ${behind.join(", ")}.`
          : `Every board matches the archive at ${data.archive_session}.`}
      </TooltipContent>
    </Tooltip>
  );
}

export function AppHeader() {
  const pathname = usePathname();
  const current =
    ALL_NAV_ITEMS.find((i) => i.href !== "/admin" && pathname.startsWith(i.href)) ??
    ALL_NAV_ITEMS[0];

  return (
    <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-3 border-b border-border bg-background/80 px-4 backdrop-blur-md">
      <div className="min-w-0">
        <h1 className="truncate text-[13px] font-semibold tracking-tight">{current.label}</h1>
        <p className="truncate text-[11px] text-muted-foreground">{current.hint}</p>
      </div>
      <div className="ml-auto flex items-center gap-3">
        <ArchiveBadge />
        <Separator orientation="vertical" className="h-5" />
        <ThemeToggle />
      </div>
    </header>
  );
}
