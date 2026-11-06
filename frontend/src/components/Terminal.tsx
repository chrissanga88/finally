"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { computeLivePortfolio } from "@/lib/portfolio";
import type { ChatActions, Portfolio, Snapshot, WatchlistItem } from "@/lib/types";
import { priceStore, usePriceStream, usePriceVersion } from "@/lib/usePriceStream";
import { Header } from "./Header";
import { Watchlist } from "./Watchlist";
import { MainChart } from "./MainChart";
import { Heatmap } from "./Heatmap";
import { PnlChart } from "./PnlChart";
import { PositionsTable } from "./PositionsTable";
import { TradeBar } from "./TradeBar";
import { ChatPanel } from "./ChatPanel";

const REFRESH_MS = 30_000;

function errMsg(e: unknown): string {
  return e instanceof ApiError ? e.message : "Unexpected error";
}

export default function Terminal() {
  const status = usePriceStream();
  const priceVersion = usePriceVersion();

  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [chatOpen, setChatOpen] = useState(true);

  // Portfolio + P&L history are always fetched together (PLAN §11).
  const refreshPortfolio = useCallback(async () => {
    const [p, h] = await Promise.allSettled([api.portfolio(), api.history()]);
    if (p.status === "fulfilled") setPortfolio(p.value);
    if (h.status === "fulfilled") setSnapshots(h.value.snapshots);
  }, []);

  const refreshWatchlist = useCallback(async () => {
    try {
      const r = await api.watchlist();
      setWatchlist(r.tickers);
      setSelected((cur) => cur ?? r.tickers[0]?.ticker ?? null);
    } catch {
      /* keep last good list */
    }
  }, []);

  useEffect(() => {
    refreshPortfolio();
    refreshWatchlist();
    const id = setInterval(refreshPortfolio, REFRESH_MS);
    return () => clearInterval(id);
  }, [refreshPortfolio, refreshWatchlist]);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const live = useMemo(() => computeLivePortfolio(portfolio, (t) => priceStore.price(t)), [portfolio, priceVersion]);

  const onTrade = useCallback(
    async (ticker: string, side: "buy" | "sell", qty: number) => {
      try {
        await api.trade(ticker, side, qty);
        await Promise.all([refreshPortfolio(), refreshWatchlist()]);
        return null;
      } catch (e) {
        // A failed buy may still have added the ticker to the watchlist (PLAN §9).
        if (side === "buy") refreshWatchlist();
        return errMsg(e);
      }
    },
    [refreshPortfolio, refreshWatchlist],
  );

  const onAdd = useCallback(
    async (ticker: string) => {
      try {
        await api.addWatch(ticker);
        await refreshWatchlist();
        setSelected(ticker);
        return null;
      } catch (e) {
        return errMsg(e);
      }
    },
    [refreshWatchlist],
  );

  const onRemove = useCallback(
    async (ticker: string) => {
      try {
        await api.removeWatch(ticker);
        await refreshWatchlist();
        setSelected((cur) => (cur === ticker ? null : cur));
        return null;
      } catch (e) {
        await refreshWatchlist();
        return errMsg(e);
      }
    },
    [refreshWatchlist],
  );

  const onChatActions = useCallback(
    (a: ChatActions) => {
      refreshPortfolio();
      if (a.watchlist_changes?.length || a.trades?.length) refreshWatchlist();
    },
    [refreshPortfolio, refreshWatchlist],
  );

  // If the selected ticker disappears from the watchlist, fall back to the first row.
  useEffect(() => {
    if (!selected && watchlist.length) setSelected(watchlist[0].ticker);
  }, [selected, watchlist]);

  const positions = live?.positions ?? [];

  return (
    <div className="h-screen flex flex-col terminal-bg overflow-hidden">
      <Header
        totalValue={live?.total_value ?? null}
        cash={live?.cash_balance ?? null}
        unrealized={live?.unrealized_pnl ?? null}
        status={status}
      />

      <main
        className={`flex-1 min-h-0 grid gap-1.5 p-1.5 ${
          chatOpen
            ? "grid-cols-[17rem_minmax(0,1fr)] xl:grid-cols-[18rem_minmax(0,1fr)_22rem]"
            : "grid-cols-[17rem_minmax(0,1fr)_2.25rem] xl:grid-cols-[18rem_minmax(0,1fr)_2.25rem]"
        }`}
      >
        <Watchlist items={watchlist} selected={selected} onSelect={setSelected} onAdd={onAdd} onRemove={onRemove} />

        <div className="min-h-0 grid grid-rows-[minmax(0,1.15fr)_auto_minmax(0,1fr)] gap-1.5">
          <MainChart ticker={selected} />
          <TradeBar ticker={selected} onTrade={onTrade} />
          <div className="min-h-0 grid grid-cols-1 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] gap-1.5">
            <Panel title="Positions" right={`${positions.length}`}>
              <PositionsTable positions={positions} selected={selected} onSelect={setSelected} />
            </Panel>
            <div className="min-h-0 grid grid-rows-2 gap-1.5">
              <Panel title="Allocation · P&L heatmap">
                <div className="h-full p-1.5">
                  <Heatmap positions={positions} onSelect={setSelected} />
                </div>
              </Panel>
              <Panel title="Portfolio value" right={`${snapshots.length} pts`}>
                <PnlChart snapshots={snapshots} />
              </Panel>
            </div>
          </div>
        </div>

        {/* One chat instance: a grid column on xl, an overlay drawer below xl when open. */}
        <div
          className={
            chatOpen
              ? "flex min-h-0 fixed right-1.5 top-[3.4rem] bottom-1.5 w-[22rem] max-w-[90vw] z-30 shadow-2xl shadow-black/60 xl:static xl:w-auto xl:max-w-none xl:shadow-none"
              : "flex min-h-0"
          }
        >
          <ChatPanel open={chatOpen} onToggle={() => setChatOpen((o) => !o)} onActions={onChatActions} />
        </div>
      </main>
    </div>
  );
}

function Panel({ title, right, children }: { title: string; right?: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col min-h-0 bg-panel border border-line rounded-sm">
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-line">
        <span className="text-[10px] uppercase tracking-[0.18em] text-muted">{title}</span>
        {right && <span className="num text-[10px] text-faint">{right}</span>}
      </div>
      <div className="flex-1 min-h-0 relative">{children}</div>
    </section>
  );
}
