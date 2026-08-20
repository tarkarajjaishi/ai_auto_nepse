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
        {/* The rail suspends its OWN body now. Suspending it from here painted the 224px
            fallback on all seventeen admin routes, including the thirteen with no rail, which
            then collapsed it. The rail decides from usePathname first and renders nothing. */}
        <InstrumentRail />
        <main className="min-h-0 min-w-0 flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}
