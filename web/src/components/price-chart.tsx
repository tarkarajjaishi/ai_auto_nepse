"use client";

import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
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
  visibleBars = 120,
  ema,
  mode = "candles",
}: {
  bars: Bar[];
  height?: number;
  /** take the parent's height instead of a fixed one — for the terminal layout, where the
   *  chart owns whatever is left between the toolbar and the bottom panel */
  fill?: boolean;
  /**
   * How many of the most recent sessions to OPEN on. The series always holds the entire
   * archive; this only sets the window. 0 shows everything.
   *
   * Autoranging price across 1,600+ bars squashes the recent action into a flat band, which is
   * why the Streamlit chart settled on 120 and why the range buttons here move this rather than
   * refetch — panning back must always reveal the rest without a round trip.
   */
  visibleBars?: number;
  /** period -> series, straight from the API. Drawn as-is; never computed here. */
  ema?: Record<string, (number | null)[]>;
  /** candles, or a filled line for reading long spans */
  mode?: "candles" | "line";
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
        // The pane divider defaults to a solid #2B2B43 rule across the full width, which reads
        // as a chart artifact rather than a boundary — price and volume are obviously separate
        // without a line drawn between them. Transparent, and not draggable: a stray drag
        // resizing the volume pane is an accident, not a feature anyone wanted here.
        panes: {
          enableResize: false,
          separatorColor: "transparent",
          separatorHoverColor: "transparent",
        },
      },
      grid: {
        vertLines: { color: dark ? "rgba(255,255,255,.05)" : "rgba(0,0,0,.05)" },
        horzLines: { color: dark ? "rgba(255,255,255,.05)" : "rgba(0,0,0,.05)" },
      },
      // No bottom reserve for volume any more — volume has its own pane now. Keeping the 0.26
      // margin here is what put NEGATIVE prices on the axis: the scale maps the series into the
      // top 74% of the pane and then labels the whole height, so a series ranging 50-1000 got
      // ticks at -100 and -200. Prices cannot be negative and a chart must not imply they can.
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.1, bottom: 0.08 } },
      timeScale: { borderVisible: false, rightOffset: 4 },
      crosshair: { mode: 0 },
      autoSize: false,
    });
    chart.current = c;

    // The theme's own --up/--down, so candles cannot drift away from every other green and red
    // on the page. Read from the computed style rather than duplicated here for the same reason.
    const up = read("--up", dark ? "#3fb68b" : "#12946a");
    const down = read("--down", dark ? "#e0564e" : "#cf4436");

    if (mode === "candles") {
      const candles = c.addSeries(CandlestickSeries, {
        upColor: up,
        downColor: down,
        borderUpColor: up,
        borderDownColor: down,
        wickUpColor: up,
        wickDownColor: down,
      });
      candles.setData(
        bars.map((b) => ({
          time: toTime(b.date),
          open: b.open,
          high: b.high,
          low: b.low,
          close: b.close,
        })),
      );
    } else {
      const brass = read("--primary", "#c39a4e");
      const area = c.addSeries(AreaSeries, {
        lineColor: brass,
        lineWidth: 2,
        topColor: `${brass}44`,
        bottomColor: `${brass}00`,
      });
      area.setData(bars.map((b) => ({ time: toTime(b.date), value: b.close })));
    }

    // EMA overlays, drawn exactly as the API sent them. Nulls during the warm-up are dropped
    // rather than zero-filled — a 200 EMA that starts at 0 draws a line from the floor.
    const EMA_COLOR: Record<string, string> = {
      "20": read("--chart-3", "#5b8fd6"),
      "50": read("--primary", "#c39a4e"),
      "200": read("--chart-5", "#9b7ad6"),
    };
    for (const [period, series] of Object.entries(ema ?? {})) {
      const line = c.addSeries(LineSeries, {
        color: EMA_COLOR[period] ?? read("--muted-foreground", "#8a97a3"),
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      line.setData(
        series
          .map((v, i) => (v == null ? null : { time: toTime(bars[i]!.date), value: v }))
          .filter((p): p is { time: UTCTimestamp; value: number } => p !== null),
      );
    }
    // Volume in its own pane (v5's third argument), not overlaid on the price scale. Separate
    // panes mean neither series can rescale the other and each axis only ever shows values its
    // own data actually takes.
    const volume = c.addSeries(HistogramSeries, { priceFormat: { type: "volume" } }, 1);
    c.priceScale("right", 1).applyOptions({
      borderVisible: false,
      scaleMargins: { top: 0.15, bottom: 0 },
    });

    volume.setData(
      bars.map((b) => ({
        time: toTime(b.date),
        value: b.volume,
        color: b.close >= b.open ? `${up}55` : `${down}55`,
      })),
    );
    // Volume takes a fixed slice of the height rather than an even split of the two panes.
    const sizePanes = () => {
      const total = fill ? (box.current?.clientHeight ?? height) : height;
      const panes = c.panes();
      if (panes.length > 1) panes[1].setHeight(Math.max(48, Math.round(total * 0.2)));
    };
    sizePanes();

    // NOT fitContent(): that frames all 1,600 bars and flattens the last three months into a
    // line. Open on the recent window; the whole series is loaded and one drag reaches it.
    if (visibleBars > 0 && bars.length > visibleBars) {
      c.timeScale().setVisibleLogicalRange({
        from: bars.length - visibleBars,
        to: bars.length + 2,
      });
    } else {
      c.timeScale().fitContent();
    }

    // The library measures once at creation. Without this, any layout change after mount — the
    // sidebar collapsing, a window resize — leaves the canvas at its old width.
    const ro = new ResizeObserver(([e]) => {
      c.applyOptions({
        width: Math.floor(e.contentRect.width),
        ...(fill ? { height: Math.max(160, Math.floor(e.contentRect.height)) } : {}),
      });
      sizePanes();
    });
    ro.observe(box.current);

    return () => {
      ro.disconnect();
      c.remove();
      chart.current = null;
    };
  }, [bars, height, theme, fill, visibleBars, ema, mode]);

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
