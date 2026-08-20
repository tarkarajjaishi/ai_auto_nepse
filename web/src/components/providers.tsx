"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ApiError } from "@/lib/api";

/**
 * `useState` rather than a module-level client: on the server a module singleton is shared
 * between requests, which leaks one user's cache into another's. This is the pattern TanStack
 * documents for the app router.
 */
export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // The archive updates once a session, so aggressive refetching buys nothing and
            // costs a re-render of every table. Freshness that matters is the STALE flag the
            // API computes, not how recently we polled.
            staleTime: 30_000,
            gcTime: 5 * 60_000,
            refetchOnWindowFocus: false,
            retry: (count, error) => {
              // A 404 means the symbol has no data — retrying cannot help and just delays the
              // message. Only retry transport failures.
              if (error instanceof ApiError && error.status >= 400) return false;
              return count < 2;
            },
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={client}>
      {/* Base UI calls it `delay`, not Radix's `delayDuration` — shadcn 4.x ships Base UI */}
      <TooltipProvider delay={200}>{children}</TooltipProvider>
      <Toaster richColors closeButton position="bottom-right" />
    </QueryClientProvider>
  );
}
