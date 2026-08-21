"use client";

import * as echarts from "echarts";
import { useEffect, useMemo, useRef } from "react";

import { type ZoneChart as ZoneData } from "@/lib/api";
import { useTheme } from "@/store/theme";

/**
 * The vendor's own chart: candles, every zone extended to the right edge, and the trade levels.
 *
 * ECharts rather than Lightweight Charts, which draws the price chart elsewhere. Lightweight
 * Charts has no filled rectangle — a zone would have to be two price lines with nothing between
 * them, which is the one thing a supply/demand chart is for. ECharts `markArea` is a filled band
 * over a value range and needs no custom primitive.
 *
 * A zone is a BAND OF PRICE, not a point: it starts at the candle that formed it and runs to the
 * right edge, because it stays live until price closes through it. `xAxis` is category, so the
 * band's x coordinates are the bar's date string and the last date — indices would drift the
 * moment the window length changed.
 */

/** fill / border per (kind, state) — supply reads as resistance, demand as support. */
const ZONE_COLOUR: Record<string, { fill: string; edge: string }> = {
  "supply:strong": { fill: "rgba(207, 68, 54, 0.20)", edge: "rgba(207, 68, 54, 0.75)" },
  "supply:untested": { fill: "rgba(207, 68, 54, 0.12)", edge: "rgba(207, 68, 54, 0.45)" },
  "supply:weak": { fill: "rgba(207, 68, 54, 0.07)", edge: "rgba(207, 68, 54, 0.28)" },
  "supply:turncoat": { fill: "rgba(150, 110, 200, 0.10)", edge: "rgba(150, 110, 200, 0.40)" },
  "demand:strong": { fill: "rgba(18, 148, 106, 0.20)", edge: "rgba(18, 148, 106, 0.75)" },
  "demand:untested": { fill: "rgba(18, 148, 106, 0.12)", edge: "rgba(18, 148, 106, 0.45)" },
  "demand:weak": { fill: "rgba(18, 148, 106, 0.07)", edge: "rgba(18, 148, 106, 0.28)" },
  "demand:turncoat": { fill: "rgba(150, 110, 200, 0.10)", edge: "rgba(150, 110, 200, 0.40)" },
};

export function ZoneChart({
  data,
  states,
  height = 460,
}: {
  data: ZoneData;
  /** which zone states to draw — the vendor's settings panel has exactly these toggles */
  states: Set<string>;
  height?: number;
}) {
  const theme = useTheme((s) => s.theme);
  const dark = theme === "dark";

  const option = useMemo(() => {
    const dates = data.bars.map((b) => b.date);
    const last = dates[dates.length - 1] ?? "";
    // ECharts candlestick wants [open, close, low, high] — not OHLC order, and getting it wrong
    // draws plausible-looking candles with the bodies inverted.
    const candles = data.bars.map((b) => [b.open, b.close, b.low, b.high]);

    const areas = data.zones
      .filter((z) => states.has(z.state))
      .map((z) => {
        const c = ZONE_COLOUR[`${z.kind}:${z.state}`] ?? ZONE_COLOUR["supply:weak"];
        return [
          {
            xAxis: z.from,
            yAxis: z.lo,
            itemStyle: { color: c.fill, borderColor: c.edge, borderWidth: 1 },
            name: `${z.kind} · ${z.state}`,
          },
          { xAxis: last, yAxis: z.hi },
        ];
      });

    const lv = data.levels;
    const lines = !lv
      ? []
      : [
          { yAxis: lv.tp, name: `Take profit ${lv.tp.toLocaleString()}`, colour: "#12946a" },
          { yAxis: lv.sl, name: `Stop loss ${lv.sl.toLocaleString()}`, colour: "#cf4436" },
          { yAxis: lv.entry, name: `Entry ${lv.entry.toLocaleString()}`, colour: "#d08b18" },
        ].map((l) => ({
          yAxis: l.yAxis,
          lineStyle: { color: l.colour, type: "dotted" as const, width: 1 },
          label: {
            formatter: l.name,
            position: "insideEndTop" as const,
            color: l.colour,
            fontSize: 10,
          },
        }));

    const axis = dark ? "#8a949a" : "#5b666c";
    const grid = dark ? "rgba(255,255,255,0.05)" : "rgba(0,0,0,0.06)";

    return {
      animation: false,
      backgroundColor: "transparent",
      grid: { left: 8, right: 64, top: 16, bottom: 24, containLabel: true },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        backgroundColor: dark ? "#171b1c" : "#ffffff",
        borderColor: grid,
        textStyle: { color: dark ? "#e7ebe5" : "#15191a", fontSize: 11 },
      },
      xAxis: {
        type: "category",
        data: dates,
        axisLine: { lineStyle: { color: grid } },
        axisLabel: { color: axis, fontSize: 10 },
        splitLine: { show: false },
      },
      yAxis: {
        scale: true,
        position: "right",
        axisLabel: { color: axis, fontSize: 10 },
        splitLine: { lineStyle: { color: grid } },
      },
      dataZoom: [
        { type: "inside", start: 55, end: 100 },
        { type: "slider", height: 14, bottom: 2, start: 55, end: 100, borderColor: grid },
      ],
      series: [
        {
          type: "candlestick",
          data: candles,
          itemStyle: {
            color: "#12946a",
            color0: "#cf4436",
            borderColor: "#12946a",
            borderColor0: "#cf4436",
          },
          markArea: { silent: true, data: areas },
          markLine: {
            silent: true,
            symbol: "none",
            data: lines,
          },
        },
      ],
    };
  }, [data, states, dark]);

  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!box.current) return;
    const c = echarts.init(box.current, undefined, { renderer: "canvas" });
    chart.current = c;
    // A ResizeObserver rather than a window listener: this panel opens and closes inside a
    // column, so its width changes without the window ever resizing.
    const ro = new ResizeObserver(() => c.resize());
    ro.observe(box.current);
    return () => {
      ro.disconnect();
      c.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(option, true);
  }, [option]);

  return <div ref={box} style={{ height, width: "100%" }} />;
}
