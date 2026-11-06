"use client";

import { useEffect, useRef } from "react";
import { createChart, LineSeries, type IChartApi, type ISeriesApi } from "lightweight-charts";
import { SPARKLINE_POINTS } from "@/lib/priceStore";
import { priceStore } from "@/lib/usePriceStream";
import { C, toSeries } from "./charts";

/** Last 300 points of a ticker's buffer, redrawn on each new point for that ticker. */
export function Sparkline({ ticker }: { ticker: string }) {
  const el = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!el.current) return;
    const chart: IChartApi = createChart(el.current, {
      autoSize: true,
      layout: { background: { color: "transparent" }, attributionLogo: false, textColor: C.text },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      rightPriceScale: { visible: false },
      leftPriceScale: { visible: false },
      timeScale: { visible: false, borderVisible: false },
      crosshair: { vertLine: { visible: false }, horzLine: { visible: false } },
      handleScroll: false,
      handleScale: false,
    });
    const series: ISeriesApi<"Line"> = chart.addSeries(LineSeries, {
      color: C.blue,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    let lastLen = -1;
    let lastTime = -1;
    let lastValue = NaN;
    const draw = () => {
      const buf = priceStore.buffers[ticker] ?? [];
      const tail = buf[buf.length - 1];
      if (buf.length === lastLen && tail?.time === lastTime && tail?.value === lastValue) return;
      lastLen = buf.length;
      lastTime = tail?.time ?? -1;
      lastValue = tail?.value ?? NaN;
      const pts = buf.slice(-SPARKLINE_POINTS);
      if (pts.length > 1) {
        const up = pts[pts.length - 1].value >= pts[0].value;
        series.applyOptions({ color: up ? C.up : C.down });
      }
      series.setData(toSeries(pts));
      chart.timeScale().fitContent();
    };
    draw();
    const unsub = priceStore.subscribe(draw);
    return () => {
      unsub();
      chart.remove();
    };
  }, [ticker]);

  return <div ref={el} className="h-7 w-full pointer-events-none" data-testid={`sparkline-${ticker}`} />;
}
