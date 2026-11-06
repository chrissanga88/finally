"use client";

import type { Position } from "@/lib/types";
import { fmtPct, fmtPrice, fmtQty, fmtSigned, fmtUsd, signClass } from "@/lib/format";

export function PositionsTable({
  positions,
  selected,
  onSelect,
}: {
  positions: Position[];
  selected: string | null;
  onSelect: (t: string) => void;
}) {
  return (
    <div className="h-full overflow-auto" data-testid="positions-table">
      <table className="w-full text-[12px]">
        <thead className="sticky top-0 bg-panel z-10">
          <tr className="text-[9px] uppercase tracking-wider text-faint border-b border-line">
            <th className="text-left font-normal px-3 py-1.5">Ticker</th>
            <th className="text-right font-normal px-2">Qty</th>
            <th className="text-right font-normal px-2">Avg Cost</th>
            <th className="text-right font-normal px-2">Last</th>
            <th className="text-right font-normal px-2">Mkt Value</th>
            <th className="text-right font-normal px-2">Unrl P&amp;L</th>
            <th className="text-right font-normal px-3">%</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr
              key={p.ticker}
              data-testid={`position-row-${p.ticker}`}
              onClick={() => onSelect(p.ticker)}
              className={`num border-b border-line/50 cursor-pointer hover:bg-panel-2 ${
                selected === p.ticker ? "bg-panel-2" : ""
              }`}
            >
              <td className="px-3 py-1.5 font-semibold text-accent">{p.ticker}</td>
              <td className="px-2 text-right" data-testid={`position-qty-${p.ticker}`}>
                {fmtQty(p.quantity)}
              </td>
              <td className="px-2 text-right text-muted">{fmtPrice(p.avg_cost)}</td>
              <td className="px-2 text-right">{fmtPrice(p.current_price)}</td>
              <td className="px-2 text-right">{fmtUsd(p.market_value)}</td>
              <td className={`px-2 text-right ${signClass(p.unrealized_pnl)}`} data-testid={`position-pnl-${p.ticker}`}>
                {fmtSigned(p.unrealized_pnl)}
              </td>
              <td className={`px-3 text-right ${signClass(p.unrealized_pnl_percent)}`}>
                {fmtPct(p.unrealized_pnl_percent)}
              </td>
            </tr>
          ))}
          {positions.length === 0 && (
            <tr>
              <td colSpan={7} className="px-3 py-6 text-center text-faint text-xs" data-testid="positions-empty">
                No open positions
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
