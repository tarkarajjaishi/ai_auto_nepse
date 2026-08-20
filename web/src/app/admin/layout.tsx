import { AppHeader } from "@/components/app-header";
import { AppSidebar } from "@/components/app-sidebar";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    // h-dvh + overflow-hidden on the frame, scrolling only in <main>: a terminal keeps its
    // chrome fixed and moves the content. dvh rather than vh so mobile browser chrome does not
    // push the header off screen.
    <div className="flex h-dvh overflow-hidden">
      <AppSidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <AppHeader />
        <main className="min-h-0 flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}
