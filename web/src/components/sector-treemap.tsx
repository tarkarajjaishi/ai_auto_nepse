"use client";

import ReactECharts from "echarts-for-react";
import { useMemo } from "react";

import { compact, heatColour, pct } from "@/lib/format";
import { useTheme } from "@/store/theme";

/**
 * The one treemap. Both heatmaps render through this — the drawer under the chart and the full
 * /admin/heatmap page — because two copies of an echarts option is precisely how the two drifted
 * into looking different and then into one of them silently rendering no labels at all.
 *
 * Two echarts behaviours are load-bearing here and neither is guessable from the docs:
 *
 *  - `label.show` must be set explicitly. Without it v6 draws treemap leaves as anonymous
 *    coloured blocks.
 *  - `formatter` must live on the SERIES label, never on a data item. A per-item
 *    `label: { formatter }` replaces the whole series label object, so `{n|…}` rich tags lose the
 *    `rich` block that defines them and echarts renders nothing — silently, no console warning.
 */
export type TreemapItem = {
  /** what the tile is labelled and identified by */
  name: string;
  /** today's move, drives the colour ramp */
  pct: number | null;
  /** drives the tile's area */
  turnover: number;
  /** tooltip detail line */
  note?: string;
};

export function SectorTreemap({
  items,
  height,
  sat = 3,
  onSelect,
}: {
  items: TreemapItem[];
  /** a number of pixels, or "100%" to fill the parent */
  height: number | string;
  /** where the colour ramp reaches full depth — 3 for sector indices, ~5 for single scrips */
  sat?: number;
  onSelect?: (name: string) => void;
}) {
  const theme = useTheme((s) => s.theme);

  const option = useMemo(
    () => ({
      backgroundColor: "transparent",
      tooltip: {
        formatter: (p: { name: string }) => {
          const it = items.find((x) => x.name === p.name);
          if (!it) return p.name;
          return `<b>${it.name}</b><br/>${pct(it.pct)}<br/>turnover ${compact(it.turnover)}${
            it.note ? `<br/>${it.note}` : ""
          }`;
        },
      },
      series: [
        {
          type: "treemap",
          // Square-root the area. Turnover spans three orders of magnitude across NEPSE's
          // sectors — Hydro Power alone is ~5x the next — and a linear map leaves everything
          // else as unreadable slivers.
          data: items.map((it) => ({
            name: it.name,
            value: Math.sqrt(Math.max(it.turnover, 1e4)),
            itemStyle: { color: heatColour(it.pct, sat) },
          })),
          roam: false,
          nodeClick: false,
          breadcrumb: { show: false },
          animationDuration: 300,
          width: "100%",
          height: "100%",
          top: 0,
          left: 0,
          label: {
            show: true,
            // The label sits centred horizontally and anchored near the top of its tile, and
            // that is as far as echarts 6 will go. Measured, not assumed:
            //
            //   position: ["50%","50%"]              -> renders, centred across, top-anchored
            //   position: "inside"                   -> leaf labels vanish entirely
            //   ["50%","50%"] + labelLayout(centre)  -> leaf labels vanish entirely
            //
            // Both failures are silent — no console warning, just blank tiles — so anyone
            // "improving" this should re-check the screen, not just the diff. Top-anchored also
            // matches how the reference heatmap labels its sector blocks.
            position: ["50%", "50%"] as [string, string],
            align: "center" as const,
            verticalAlign: "middle" as const,
            // White on every shade of the ramp: pale green and deep red differ hugely in
            // luminance, so a theme-coloured label is unreadable at one end or the other.
            color: "#ffffff",
            overflow: "truncate",
            formatter: (p: { name: string }) => {
              const it = items.find((x) => x.name === p.name);
              return it ? `{n|${it.name}}\n{v|${pct(it.pct)}}` : p.name;
            },
            rich: {
              n: { fontSize: 11, fontWeight: 600, color: "#ffffff", lineHeight: 16 },
              v: {
                fontSize: 13,
                fontWeight: 700,
                color: "#ffffff",
                lineHeight: 17,
                fontFamily: "var(--font-geist-mono), monospace",
              },
            },
          },
          itemStyle: {
            borderColor: theme === "dark" ? "#0e1419" : "#f4f6f9",
            // borderWidth and gapWidth STACK. 2 + 2 + 3 put ~7px of dead background between
            // neighbours, which reads as a grid of separate cards rather than one map. 1 + 1
            // leaves a 3px seam: enough to separate two same-coloured tiles, no more.
            borderWidth: 1,
            gapWidth: 1,
            borderRadius: 5,
          },
        },
      ],
    }),
    [items, sat, theme],
  );

  return (
    <ReactECharts
      option={option}
      style={{ height, width: "100%" }}
      opts={{ renderer: "canvas" }}
      notMerge
      onEvents={
        onSelect ? { click: (e: { name: string }) => onSelect(e.name) } : undefined
      }
    />
  );
}
