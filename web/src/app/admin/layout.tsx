import { getServerSession } from "next-auth";

import { InstrumentRail } from "@/components/instrument-rail";
import { AuthProvider } from "@/components/session-provider";
import { TopNav } from "@/components/top-nav";
import { authOptions } from "@/lib/auth";

export default async function AdminLayout({ children }: { children: React.ReactNode }) {
  const session = await getServerSession(authOptions);

  /* Signed out, the only reachable child is /admin/login (middleware guarantees it), and it
     draws its own full-screen layout. Wrapping it in the terminal chrome would put a nav bar
     full of links it cannot follow above the sign-in card. */
  if (!session) return <AuthProvider>{children}</AuthProvider>;

  return (
    <AuthProvider>
      {/* Terminal frame: chrome fixed, content scrolls. h-dvh not h-vh so mobile browser chrome
          cannot push the header off screen.

          Nav on top, instruments down the left — you change symbol constantly and section
          rarely, so the rail belongs to the thing that changes. */}
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
    </AuthProvider>
  );
}
