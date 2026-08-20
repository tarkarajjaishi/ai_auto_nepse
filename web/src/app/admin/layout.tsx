import { Suspense } from "react";

import { InstrumentRail } from "@/components/instrument-rail";
import { TopNav } from "@/components/top-nav";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    // Terminal frame: chrome fixed, content scrolls. h-dvh not h-vh so mobile browser chrome
    // cannot push the header off screen.
    //
    // Nav on top, instruments down the left — you change symbol constantly and section rarely,
    // so the rail belongs to the thing that changes.
    <div className="flex h-dvh flex-col overflow-hidden">
      <TopNav />
      <div className="flex min-h-0 flex-1">
        {/* useSearchParams inside the rail makes it a client boundary that must be suspended,
            or every page under this layout is forced out of static rendering. */}
        <Suspense fallback={<div className="hidden w-56 shrink-0 border-r border-border bg-sidebar lg:block" />}>
          <InstrumentRail />
        </Suspense>
        <main className="min-h-0 min-w-0 flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}
