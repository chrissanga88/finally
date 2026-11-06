"use client";

import { useEffect, useState } from "react";
import type { ConnectionStatus } from "@/lib/types";
import { fmtSigned, fmtUsd, signClass } from "@/lib/format";

const STATUS: Record<ConnectionStatus, { color: string; label: string }> = {
  connected: { color: "#2fbf71", label: "LIVE" },
  reconnecting: { color: "#ecad0a", label: "RECONNECTING" },
  disconnected: { color: "#f0524f", label: "DISCONNECTED" },
};

export function Header({
  totalValue,
  cash,
  unrealized,
  status,
}: {
  totalValue: number | null;
  cash: number | null;
  unrealized: number | null;
  status: ConnectionStatus;
}) {
  const s = STATUS[status];
  const [clock, setClock] = useState("");
  useEffect(() => {
    const tick = () => setClock(new Date().toLocaleTimeString("en-US", { hour12: false }));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <header className="flex items-center gap-6 px-4 h-12 border-b border-line bg-panel/80 backdrop-blur">
      <div className="flex items-baseline gap-2">
        <span className="text-lg font-bold tracking-tight">
          Fin<span className="text-accent">Ally</span>
        </span>
        <span className="hidden md:inline text-[10px] uppercase tracking-[0.2em] text-faint">AI Trading Workstation</span>
      </div>

      <div className="flex items-center gap-6 ml-auto">
        <Stat label="Portfolio" testId="total-value" value={fmtUsd(totalValue)} big />
        <Stat
          label="Unrealized"
          testId="unrealized-pnl"
          value={unrealized == null ? "—" : fmtSigned(unrealized)}
          className={signClass(unrealized)}
        />
        <Stat label="Cash" testId="cash-balance" value={fmtUsd(cash)} />
        <div className="hidden sm:block num text-[11px] text-faint w-16 text-right">{clock}</div>
        <div
          className="flex items-center gap-2"
          data-testid="connection-status"
          data-status={status}
          title={`Price stream: ${status}`}
        >
          <span
            data-testid="connection-dot"
            className={`h-2.5 w-2.5 rounded-full ${status === "connected" ? "dot-pulse" : ""}`}
            style={{ backgroundColor: s.color, color: `${s.color}88` }}
          />
          <span className="num text-[10px] tracking-wider" style={{ color: s.color }}>
            {s.label}
          </span>
        </div>
      </div>
    </header>
  );
}

function Stat({
  label,
  value,
  testId,
  big,
  className = "",
}: {
  label: string;
  value: string;
  testId: string;
  big?: boolean;
  className?: string;
}) {
  return (
    <div className="flex flex-col items-end leading-tight">
      <span className="text-[9px] uppercase tracking-[0.18em] text-faint">{label}</span>
      <span data-testid={testId} className={`num ${big ? "text-base font-semibold text-text" : "text-sm"} ${className}`}>
        {value}
      </span>
    </div>
  );
}
