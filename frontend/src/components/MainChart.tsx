"use client";

import { useEffect, useRef } from "react";
import { AreaSeries, createChart, LineStyle, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import { priceStore, usePriceVersion } from "@/lib/usePriceStream";
import { fmtPct, fmtPrice, signClass } from "@/lib/format";
import { baseChartOptions, C, toSeries } from "./charts";

const FIT_UNTIL = 240;

export function MainChart({ ticker }: { ticker: string | null }) {
  const el = useRef<HTMLDivElement>(null);
  usePriceVersion();
  const tick = ticker ? priceStore.latest[ticker] : undefined;

  useEffect(() => {
    if (!el.current || !ticker) return;
    const chart = createChart(el.current, baseChartOptions());
    const series: ISeriesApi<"Area"> = chart.addSeries(AreaSeries, {
      lineColor: C.blue,
      topColor: "rgba(32, 157, 215, 0.28)",
      bottomColor: "rgba(32, 157, 215, 0.0)",
      lineWidth: 2,
      priceLineColor: C.accent,
      priceLineStyle: LineStyle.Dashed,
    });

    // Full buffer on selection, then incremental update() per new point.
    const buf = priceStore.buffers[ticker] ?? [];
    series.setData(toSeries(buf));
    chart.timeScale().fitContent();
    let lastTime = buf[buf.length - 1]?.time ?? -1;
    let lastValue = buf[buf.length - 1]?.value;

    const unsub = priceStore.subscribe(() => {
      const b = priceStore.buffers[ticker];
      const p = b?.[b.length - 1];
      if (!p) return;
      if (p.time === lastTime && p.value === lastValue) return;
      if (p.time < lastTime) return;
      series.update({ time: p.time as UTCTimestamp, value: p.value });
      lastTime = p.time;
      lastValue = p.value;
      // Spread early points across the pane; once there is enough history, let it scroll.
      if (b.length <= FIT_UNTIL) chart.timeScale().fitContent();
    });
    return () => {
      unsub();
      chart.remove();
    };
  }, [ticker]);

  const sessionPct = tick?.session_change_percent;

  return (
    <section className="flex flex-col h-full min-h-0 bg-panel border border-line rounded-sm" data-testid="main-chart">
      <div className="flex items-baseline gap-4 px-3 py-2 border-b border-line">
        <span className="text-[10px] uppercase tracking-[0.18em] text-muted">Chart</span>
        {ticker ? (
          <>
            <span className="num text-accent text-lg font-semibold" data-testid="main-chart-ticker">
              {ticker}
            </span>
            <span className="num text-lg text-text">{fmtPrice(tick?.price)}</span>
            <span className={`num text-sm ${signClass(sessionPct)}`}>{fmtPct(sessionPct)}</span>
            <span className="text-[10px] text-faint">session</span>
          </>
        ) : (
          <span className="text-muted text-sm">Select a ticker</span>
        )}
      </div>
      <div className="relative flex-1 min-h-0">
        <div ref={el} className="absolute inset-0" />
        {ticker && !(priceStore.buffers[ticker]?.length) && (
          <div className="absolute inset-0 flex items-center justify-center text-xs text-faint">
            Waiting for prices…
          </div>
        )}
      </div>
    </section>
  );
}
