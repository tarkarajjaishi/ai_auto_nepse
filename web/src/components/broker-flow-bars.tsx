"use client";

import * as echarts from "echarts";
import { useEffect, useMemo, useRef } from "react";

import type { LedgerDay } from "@/lib/api";
import { compact } from "@/lib/format";
import { useTheme } from "@/store/theme";

/**
 * One broker's daily net flow in one stock, as bars.
 *
 * BARS, not an area fill, and that is the whole point of the form. A trading session is a
 * DISCRETE period — a broker bought 6,995 shares on the 24th and nothing on the 25th. An area
 * chart draws a continuous surface between those two facts and invents a slope across a day that
 * did not exist; the eye then reads the shaded area as an accumulating quantity. One bar per
 * session says exactly what happened and nothing more.
 *
 * Colour encodes POLARITY, not identity — a day is net buying or net selling — so it takes the
 * project's two semantic market tokens as a diverging pair with zero as the neutral midpoint.
 * `--up`/`--down` are read off the document rather than hardcoded, because a hardcoded green is
 * the thing globals.css exists to prevent, and because they differ between light and dark.
 *
 * There is one series, so there is no legend: the title names it. Values are not printed on every
 * bar — the axis and the hover carry them, and a number on all 30 bars is noise, not information.
 */
export function BrokerFlowBars({
  sessions,
  broker,
  symbol,
  height = 200,
}: {
  sessions: LedgerDay[];
  broker: string;
  symbol: string;
  height?: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const theme = useTheme((s) => s.theme);

  const option = useMemo(() => {
    // Read the semantic tokens from the live document so light/dark and any future retheme are
    // picked up for free. Falling back to the token names themselves would render nothing, so the
    // fallbacks are the current dark values.
    const css = getComputedStyle(document.documentElement);
    const up = css.getPropertyValue("--up").trim() || "#3fb68b";
    const down = css.getPropertyValue("--down").trim() || "#e0564e";
    const ink = css.getPropertyValue("--muted-foreground").trim() || "#8b8b8b";
    const line = css.getPropertyValue("--border").trim() || "#2a2a2a";

    return {
      animation: false,
      grid: { left: 54, right: 12, top: 10, bottom: 24 },
      xAxis: {
        type: "category",
        data: sessions.map((s) => s.date),
        axisLabel: {
          color: ink,
          fontSize: 10,
          fontFamily: "var(--font-mono)",
          // Every session labelled would collide at 30+ bars; echarts drops what will not fit.
          hideOverlap: true,
          formatter: (d: string) => d.slice(5),
        },
        axisLine: { lineStyle: { color: line } },
        axisTick: { show: false },
      },
      yAxis: {
        type: "value",
        axisLabel: {
          color: ink,
          fontSize: 10,
          fontFamily: "var(--font-mono)",
          formatter: (v: number) => compact(v),
        },
        splitLine: { lineStyle: { color: line, type: "dashed" } },
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (ps: { dataIndex: number }[]) => {
          const s = sessions[ps[0]?.dataIndex ?? 0];
          if (!s) return "";
          const sign = s.net > 0 ? "+" : "";
          return [
            `<div style="font-family:var(--font-mono);font-size:11px">`,
            `<b>${s.date}</b> &nbsp; broker ${broker} &nbsp; ${symbol}`,
            `<br/>bought ${compact(s.bought)} &nbsp; sold ${compact(s.sold)}`,
            `<br/>net <b>${sign}${compact(s.net)}</b>`,
            `</div>`,
          ].join("");
        },
      },
      series: [
        {
          type: "bar",
          data: sessions.map((s) => ({
            value: s.net,
            // Polarity, per bar. A rounded end on the data side only, anchored to the baseline,
            // so a negative bar rounds downward and the zero line stays a hard edge.
            itemStyle: {
              color: s.net >= 0 ? up : down,
              borderRadius: s.net >= 0 ? [4, 4, 0, 0] : [0, 0, 4, 4],
            },
          })),
          barMaxWidth: 22,
          // A 2px gap of surface between neighbours — the bars must not fuse into a block.
          barCategoryGap: "28%",
        },
      ],
    };
  }, [sessions, broker, symbol, theme]);

  // Init and the ResizeObserver run ONCE; setOption lives in its own effect below. Doing both in
  // one effect keyed on `option` froze the browser: every option change built a NEW observer and
  // called setOption inside it, so the relayout re-fired the observer, which resized, which
  // relaid out. Chrome's renderer locked hard enough that screenshots and script injection both
  // timed out on a freshly opened tab. sector-treemap.tsx already had the correct split; this now
  // matches it exactly rather than inventing a second shape.
  useEffect(() => {
    if (!box.current) return;
    const c = echarts.init(box.current, undefined, { renderer: "canvas" });
    chart.current = c;
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
