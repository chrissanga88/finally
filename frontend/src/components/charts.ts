import {
  ColorType,
  CrosshairMode,
  LineStyle,
  type DeepPartial,
  type ChartOptions,
  type UTCTimestamp,
} from "lightweight-charts";
import type { ChartPoint } from "@/lib/priceStore";

export const C = {
  bg: "#10161f",
  grid: "rgba(47, 59, 77, 0.35)",
  text: "#7d8a9c",
  border: "#222c3a",
  blue: "#209dd7",
  accent: "#ecad0a",
  up: "#2fbf71",
  down: "#f0524f",
};

export function baseChartOptions(): DeepPartial<ChartOptions> {
  return {
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: "transparent" },
      textColor: C.text,
      fontFamily: '"JetBrains Mono", "SF Mono", ui-monospace, Menlo, monospace',
      fontSize: 10,
      attributionLogo: false,
    },
    grid: {
      vertLines: { color: C.grid, style: LineStyle.Dotted },
      horzLines: { color: C.grid, style: LineStyle.Dotted },
    },
    rightPriceScale: { borderColor: C.border, scaleMargins: { top: 0.12, bottom: 0.08 } },
    timeScale: {
      borderColor: C.border,
      timeVisible: true,
      secondsVisible: true,
      rightOffset: 4,
      tickMarkFormatter: localTickFormatter,
    },
    crosshair: {
      mode: CrosshairMode.Magnet,
      vertLine: { color: "rgba(236, 173, 10, 0.4)", labelBackgroundColor: "#2f3b4d" },
      horzLine: { color: "rgba(236, 173, 10, 0.4)", labelBackgroundColor: "#2f3b4d" },
    },
    localization: {
      timeFormatter: (t: number) => new Date(t * 1000).toLocaleTimeString(),
    },
  };
}

export function toSeries(points: ChartPoint[]) {
  return points.map((p) => ({ time: p.time as UTCTimestamp, value: p.value }));
}

/** Axis labels in the viewer's local time (Lightweight Charts defaults to UTC). */
export function localTickFormatter(time: unknown): string {
  const d = new Date((time as number) * 1000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

export function localTickFormatterMinutes(time: unknown): string {
  const d = new Date((time as number) * 1000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}
