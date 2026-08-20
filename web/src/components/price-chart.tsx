"use client";

import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  createChart,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { Bar } from "@/lib/api";
import { useTheme } from "@/store/theme";

/**
 * TradingView's own renderer, which is why price gets Lightweight Charts and everything else
 * gets ECharts — neither replaces the other.
 *
 * The Streamlit version fought this exact problem for four attempts: a chart inside a flex item
 * measured its container as 0px and vanished, and a fixed pixel height clipped a 5-digit price.
 * Here the chart owns a plain block with an explicit height and a ResizeObserver, which is the
 * arrangement the library actually documents.
 */
function toTime(date: string): UTCTimestamp {
  return (Date.parse(`${date}T00:00:00Z`) / 1000) as UTCTimestamp;
}

export function PriceChart({
  bars,
  height = 420,
  fill = false,
}: {
  bars: Bar[];
  height?: number;
  /** take the parent's height instead of a fixed one — for the terminal layout, where the
   *  chart owns whatever is left between the toolbar and the bottom panel */
  fill?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const theme = useTheme((s) => s.theme);

  useEffect(() => {
    if (!box.current) return;
    const dark = theme === "dark";
    const css = getComputedStyle(document.documentElement);
    const read = (v: string, fallback: string) => css.getPropertyValue(v).trim() || fallback;

    const c = createChart(box.current, {
      height: fill ? Math.max(160, box.current.clientHeight) : height,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: read("--muted-foreground", dark ? "#9aa4b2" : "#5b6472"),
        fontFamily: read("--font-geist-mono", "monospace"),
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: dark ? "rgba(255,255,255,.05)" : "rgba(0,0,0,.05)" },
        horzLines: { color: dark ? "rgba(255,255,255,.05)" : "rgba(0,0,0,.05)" },
      },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.26 } },
      timeScale: { borderVisible: false, rightOffset: 4 },
      crosshair: { mode: 0 },
      autoSize: false,
    });
    chart.current = c;

    // The theme's own --up/--down, so candles cannot drift away from every other green and red
    // on the page. Read from the computed style rather than duplicated here for the same reason.
    const up = read("--up", dark ? "#3fb68b" : "#12946a");
    const down = read("--down", dark ? "#e0564e" : "#cf4436");

    const candles = c.addSeries(CandlestickSeries, {
      upColor: up,
      downColor: down,
      borderUpColor: up,
      borderDownColor: down,
      wickUpColor: up,
      wickDownColor: down,
    });
    const volume = c.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
    });
    // volume sits in the bottom quarter on its own scale, so it never rescales price
    c.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

    candles.setData(
      bars.map((b) => ({
        time: toTime(b.date),
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      })),
    );
    volume.setData(
      bars.map((b) => ({
        time: toTime(b.date),
        value: b.volume,
        color: b.close >= b.open ? `${up}55` : `${down}55`,
      })),
    );
    c.timeScale().fitContent();

    // The library measures once at creation. Without this, any layout change after mount — the
    // sidebar collapsing, a window resize — leaves the canvas at its old width.
    const ro = new ResizeObserver(([e]) => {
      c.applyOptions({
        width: Math.floor(e.contentRect.width),
        ...(fill ? { height: Math.max(160, Math.floor(e.contentRect.height)) } : {}),
      });
    });
    ro.observe(box.current);

    return () => {
      ro.disconnect();
      c.remove();
      chart.current = null;
    };
  }, [bars, height, theme, fill]);

  // In fill mode the box must be a real flex child with min-h-0, or it reports its content
  // height (the canvas it is trying to size) and the two feed each other.
  return (
    <div
      ref={box}
      className={fill ? "min-h-0 w-full flex-1" : "w-full"}
      style={fill ? undefined : { height }}
    />
  );
}
