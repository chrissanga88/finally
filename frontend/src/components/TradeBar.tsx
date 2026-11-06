"use client";

import { useEffect, useState } from "react";
import { fmtPrice, fmtUsd } from "@/lib/format";
import { priceStore, usePriceVersion } from "@/lib/usePriceStream";

interface Props {
  ticker: string | null; // selected ticker pre-fills the field
  onTrade: (ticker: string, side: "buy" | "sell", qty: number) => Promise<string | null>;
}

export function TradeBar({ ticker, onTrade }: Props) {
  usePriceVersion();
  const [sym, setSym] = useState(ticker ?? "");
  const [qty, setQty] = useState("1");
  const [busy, setBusy] = useState<"buy" | "sell" | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (ticker) setSym(ticker);
  }, [ticker]);

  const t = sym.trim().toUpperCase();
  const q = Number(qty);
  const px = t ? priceStore.latest[t]?.price : undefined;
  const est = px && Number.isFinite(q) ? px * q : undefined;

  const submit = async (side: "buy" | "sell") => {
    if (!t) {
      setMsg({ ok: false, text: "Enter a ticker" });
      return;
    }
    if (!qty.trim() || !Number.isFinite(q)) {
      setMsg({ ok: false, text: "Enter a valid quantity" });
      return;
    }
    setBusy(side);
    setMsg(null);
    const err = await onTrade(t, side, q);
    setBusy(null);
    setMsg(err ? { ok: false, text: err } : { ok: true, text: `${side === "buy" ? "Bought" : "Sold"} ${q} ${t}` });
  };

  return (
    <div className="flex flex-wrap items-center gap-2 px-3 py-2 bg-panel border border-line rounded-sm" data-testid="trade-bar">
      <span className="text-[10px] uppercase tracking-[0.18em] text-muted mr-1">Order</span>
      <input
        data-testid="trade-ticker"
        value={sym}
        onChange={(e) => setSym(e.target.value.toUpperCase())}
        placeholder="TICKER"
        maxLength={6}
        className="num w-24 bg-bg border border-line rounded-sm px-2 py-1 text-sm uppercase focus:outline-none focus:border-blue"
      />
      <input
        data-testid="trade-quantity"
        value={qty}
        onChange={(e) => setQty(e.target.value)}
        inputMode="decimal"
        placeholder="Qty"
        className="num w-20 bg-bg border border-line rounded-sm px-2 py-1 text-sm focus:outline-none focus:border-blue"
        onKeyDown={(e) => {
          if (e.key === "Enter") submit("buy");
        }}
      />
      <button
        data-testid="trade-buy"
        onClick={() => submit("buy")}
        disabled={busy !== null}
        className="px-4 py-1 text-sm font-semibold rounded-sm bg-up/15 text-up border border-up/50 hover:bg-up/25 disabled:opacity-40"
      >
        {busy === "buy" ? "…" : "BUY"}
      </button>
      <button
        data-testid="trade-sell"
        onClick={() => submit("sell")}
        disabled={busy !== null}
        className="px-4 py-1 text-sm font-semibold rounded-sm bg-down/15 text-down border border-down/50 hover:bg-down/25 disabled:opacity-40"
      >
        {busy === "sell" ? "…" : "SELL"}
      </button>
      <span className="num text-[11px] text-faint ml-1">
        {px ? `@ ${fmtPrice(px)}` : ""} {est ? `≈ ${fmtUsd(est)}` : ""}
      </span>
      {msg && (
        <span
          data-testid={msg.ok ? "trade-success" : "trade-error"}
          className={`text-[11px] ml-auto ${msg.ok ? "text-up" : "text-down"}`}
        >
          {msg.ok ? "✓ " : "✗ "}
          {msg.text}
        </span>
      )}
    </div>
  );
}
