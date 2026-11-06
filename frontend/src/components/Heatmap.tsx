"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { hierarchy, treemap, treemapSquarify } from "d3-hierarchy";
import type { Position } from "@/lib/types";
import { pnlColor } from "@/lib/portfolio";
import { fmtPct, fmtUsd } from "@/lib/format";

interface Node {
  name: string;
  value?: number;
  pos?: Position;
  children?: Node[];
}

export function Heatmap({ positions, onSelect }: { positions: Position[]; onSelect: (t: string) => void }) {
  const el = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });

  useEffect(() => {
    if (!el.current) return;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize({ w: Math.floor(width), h: Math.floor(height) });
    });
    ro.observe(el.current);
    return () => ro.disconnect();
  }, []);

  const leaves = useMemo(() => {
    if (!size.w || !size.h || positions.length === 0) return [];
    const root = hierarchy<Node>({
      name: "root",
      children: positions.map((p) => ({ name: p.ticker, value: Math.max(p.weight, 0), pos: p })),
    })
      .sum((d) => d.value ?? 0)
      .sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
    return treemap<Node>().tile(treemapSquarify).size([size.w, size.h]).paddingInner(2).round(true)(root).leaves();
  }, [positions, size]);

  return (
    <div ref={el} className="relative h-full w-full" data-testid="heatmap">
      {positions.length === 0 && (
        <div className="absolute inset-0 flex flex-col items-center justify-center text-xs text-faint gap-1">
          <span>No positions</span>
          <span className="text-[10px]">Buy something to see your allocation</span>
        </div>
      )}
      {leaves.map((leaf) => {
        const p = leaf.data.pos!;
        const w = leaf.x1 - leaf.x0;
        const h = leaf.y1 - leaf.y0;
        const big = w > 70 && h > 40;
        return (
          <button
            key={p.ticker}
            data-testid={`heatmap-cell-${p.ticker}`}
            data-pnl={p.unrealized_pnl_percent.toFixed(4)}
            onClick={() => onSelect(p.ticker)}
            title={`${p.ticker}  ${fmtUsd(p.market_value)}  ${fmtPct(p.unrealized_pnl_percent)}`}
            className="absolute overflow-hidden rounded-[2px] border border-white/5 text-left p-1.5 hover:brightness-125 transition-[filter,background-color] duration-300"
            style={{
              left: leaf.x0,
              top: leaf.y0,
              width: w,
              height: h,
              backgroundColor: pnlColor(p.unrealized_pnl_percent),
            }}
          >
            {w > 34 && h > 18 && (
              <div className="num text-[11px] font-semibold text-white leading-tight">{p.ticker}</div>
            )}
            {big && (
              <>
                <div className="num text-[10px] text-white/85 leading-tight">{fmtPct(p.unrealized_pnl_percent)}</div>
                <div className="num text-[10px] text-white/60 leading-tight">{(p.weight * 100).toFixed(1)}%</div>
              </>
            )}
          </button>
        );
      })}
    </div>
  );
}
