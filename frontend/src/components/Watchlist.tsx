"use client";

import { useState, type FormEvent } from "react";
import type { WatchlistItem } from "@/lib/types";
import { priceStore, usePriceVersion } from "@/lib/usePriceStream";
import { fmtPct, fmtPrice, signClass } from "@/lib/format";
import { Sparkline } from "./Sparkline";

interface Props {
  items: WatchlistItem[];
  selected: string | null;
  onSelect: (t: string) => void;
  onAdd: (t: string) => Promise<string | null>; // returns error message or null
  onRemove: (t: string) => Promise<string | null>;
}

export function Watchlist({ items, selected, onSelect, onAdd, onRemove }: Props) {
  usePriceVersion();
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const t = input.trim().toUpperCase();
    if (!t) return;
    setBusy(true);
    const err = await onAdd(t);
    setBusy(false);
    setError(err);
    if (!err) setInput("");
  };

  return (
    <section className="flex flex-col h-full min-h-0 bg-panel border border-line rounded-sm" data-testid="watchlist">
      <div className="flex items-center justify-between px-3 py-2 border-b border-line">
        <span className="text-[10px] uppercase tracking-[0.18em] text-muted">Watchlist</span>
        <span className="num text-[10px] text-faint">{items.length}</span>
      </div>

      <div className="grid grid-cols-[3.6rem_1fr_4.6rem_3.6rem_1rem] gap-x-2 px-3 py-1 text-[9px] uppercase tracking-wider text-faint border-b border-line">
        <span>Sym</span>
        <span>Trend</span>
        <span className="text-right">Last</span>
        <span className="text-right">Sess%</span>
        <span />
      </div>

      <ul className="flex-1 min-h-0 overflow-y-auto">
        {items.map((it) => {
          const tick = priceStore.latest[it.ticker];
          const price = tick?.price ?? it.price;
          const pct = tick?.session_change_percent ?? it.session_change_percent;
          const flash = priceStore.flashes[it.ticker];
          const isSel = selected === it.ticker;
          return (
            <li
              key={it.ticker}
              data-testid={`watchlist-row-${it.ticker}`}
              onClick={() => onSelect(it.ticker)}
              className={`group grid grid-cols-[3.6rem_1fr_4.6rem_3.6rem_1rem] gap-x-2 items-center px-3 py-1 cursor-pointer border-b border-line/60 border-l-2 transition-colors ${
                isSel ? "bg-panel-2 border-l-accent" : "border-l-transparent hover:bg-panel-2/60"
              }`}
            >
              <span className={`num text-[13px] font-semibold ${isSel ? "text-accent" : "text-text"}`}>
                {it.ticker}
              </span>
              <Sparkline ticker={it.ticker} />
              <span
                key={flash?.seq ?? 0}
                data-testid={`price-${it.ticker}`}
                className={`num text-[13px] text-right rounded-sm px-1 -mx-1 ${
                  flash ? (flash.dir === "up" ? "flash-up" : "flash-down") : ""
                }`}
              >
                {fmtPrice(price)}
              </span>
              <span
                data-testid={`change-${it.ticker}`}
                className={`num text-[11px] text-right ${signClass(pct)}`}
              >
                {fmtPct(pct)}
              </span>
              <button
                data-testid={`remove-${it.ticker}`}
                aria-label={`Remove ${it.ticker}`}
                title={`Remove ${it.ticker}`}
                onClick={async (e) => {
                  e.stopPropagation();
                  setError(await onRemove(it.ticker));
                }}
                className="text-faint hover:text-down opacity-40 group-hover:opacity-100 text-sm leading-none"
              >
                ×
              </button>
            </li>
          );
        })}
        {items.length === 0 && <li className="px-3 py-4 text-xs text-faint">Watchlist is empty</li>}
      </ul>

      <form onSubmit={submit} className="p-2 border-t border-line">
        <div className="flex gap-1">
          <input
            data-testid="watchlist-input"
            value={input}
            onChange={(e) => setInput(e.target.value.toUpperCase())}
            placeholder="Add ticker…"
            maxLength={6}
            className="num flex-1 min-w-0 bg-bg border border-line rounded-sm px-2 py-1 text-xs uppercase placeholder:normal-case placeholder:text-faint focus:outline-none focus:border-blue"
          />
          <button
            data-testid="watchlist-add"
            type="submit"
            disabled={busy || !input.trim()}
            className="px-2 py-1 text-xs rounded-sm bg-blue/15 text-blue border border-blue/40 hover:bg-blue/25 disabled:opacity-40"
          >
            Add
          </button>
        </div>
        {error && (
          <div data-testid="watchlist-error" className="mt-1 text-[11px] text-down">
            {error}
          </div>
        )}
      </form>
    </section>
  );
}
