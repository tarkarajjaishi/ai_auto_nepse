"use client";

import { Construction } from "lucide-react";

export default function FloorsheetPage() {
  return <Pending title="Floorsheet" note="Broker-level trades per session, with the net-shares chart. Needs a floorsheet endpoint on the API — the archive holds 307,969 session files, so it is paginated server-side rather than shipped whole." />;
}

export function Pending({ title, note }: { title: string; note: string }) {
  return (
    <div className="p-4 md:p-6">
      <div className="flex max-w-2xl items-start gap-3 rounded-lg border border-border bg-muted/40 p-4">
        <Construction className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <div className="text-[13px]">
          <div className="font-medium">{title} is not ported yet.</div>
          <p className="mt-1 text-muted-foreground">{note}</p>
          <p className="mt-2 text-muted-foreground">
            The Streamlit version is still live and correct at{" "}
            <a
              href="https://ai.tarkarajjaishi.com.np"
              className="text-primary underline-offset-2 hover:underline"
            >
              ai.tarkarajjaishi.com.np
            </a>{" "}
            until this replaces it.
          </p>
        </div>
      </div>
    </div>
  );
}
