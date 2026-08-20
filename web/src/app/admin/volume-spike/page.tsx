"use client";

import { useRouter } from "next/navigation";

import { BoardPage } from "@/components/board-page";

export default function VolumeSpikePage() {
  const router = useRouter();
  return (
    <BoardPage
      board="volume_spike"
      rebuildHint="python volume_spike.py"
      onRowClick={(r) => router.push(`/admin/floorsheet?symbol=${String(r.symbol)}`)}
      priority={["symbol", "window", "spike_z", "vol_x", "net_churn", "top_buyer", "buyer_net", "top_seller", "seller_net", "kind", "flags"]}
      filters={[
        { label: "3 days", test: (r) => r.window === "3d" },
        { label: "2 weeks", test: (r) => r.window === "2w" },
        { label: "1 month", test: (r) => r.window === "1m" },
        { label: "Everything", test: () => true },
      ]}
      blurb={
        <>
          Unusual volume, with the heaviest brokers on each side.{" "}
          <strong>An activity screen, not a buy list, and not proof of an operator.</strong> Two
          things the data forced: a spike is <em>broad</em> — across 52k windows the top broker
          holds only ~8% of one, no more than on a quiet day — and the spike is <em>late</em>, since
          a flagged window has already run +22% over the prior 30 days. The one column with
          measured forward signal is <strong>net_churn</strong>, where <em>low</em> is the
          interesting reading. Read the rest as <em>look here</em>.
        </>
      }
    />
  );
}
