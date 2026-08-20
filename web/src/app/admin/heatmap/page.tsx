"use client";

import { Pending } from "@/app/admin/floorsheet/page";

export default function HeatmapPage() {
  return (
    <Pending
      title="Heatmap"
      note="Sector and index treemap in ECharts. Needs a live-quote endpoint: the sector map and quotes come from NAASA's authenticated feed rather than the archive, so it is the one screen that is not purely a projection of Master_data."
    />
  );
}
