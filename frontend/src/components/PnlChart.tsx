"use client";

import { useEffect, useRef } from "react";
import { BaselineSeries, createChart, type IChartApi, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import type { Snapshot } from "@/lib/types";
import { baseChartOptions, C, localTickFormatterMinutes } from "./charts";

/** Snapshots → strictly ascending whole-second points (last value wins per second). */
export function snapshotPoints(snapshots: Snapshot[]) {
  const out: { time: UTCTimestamp; value: number }[] = [];
  for (const s of snapshots) {
    const t = Math.floor(s.timestamp) as UTCTimestamp;
    const last = out[out.length - 1];
    if (last && t < last.time) continue;
    if (last && t === last.time) last.value = s.total_value;
    else out.push({ time: t, value: s.total_value });
  }
  return out;
}

export function PnlChart({ snapshots }: { snapshots: Snapshot[] }) {
  const el = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Baseline"> | null>(null);

  useEffect(() => {
    if (!el.current) return;
    const chart = createChart(el.current, {
      ...baseChartOptions(),
      timeScale: { borderColor: C.border, timeVisible: true, secondsVisible: false, rightOffset: 2, tickMarkFormatter: localTickFormatterMinutes },
    });
    const series = chart.addSeries(BaselineSeries, {
      baseValue: { type: "price", price: 10000 },
      topLineColor: C.up,
      topFillColor1: "rgba(47, 191, 113, 0.25)",
      topFillColor2: "rgba(47, 191, 113, 0.02)",
      bottomLineColor: C.down,
      bottomFillColor1: "rgba(240, 82, 79, 0.02)",
      bottomFillColor2: "rgba(240, 82, 79, 0.25)",
      lineWidth: 2,
      priceLineVisible: false,
    });
    chartRef.current = chart;
    seriesRef.current = series;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    const pts = snapshotPoints(snapshots);
    if (pts.length) series.applyOptions({ baseValue: { type: "price", price: pts[0].value } });
    series.setData(pts);
    chartRef.current?.timeScale().fitContent();
  }, [snapshots]);

  return (
    <div className="relative h-full w-full" data-testid="pnl-chart" data-points={snapshots.length}>
      <div ref={el} className="absolute inset-0" />
      {snapshots.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center text-xs text-faint">No history yet</div>
      )}
    </div>
  );
}
