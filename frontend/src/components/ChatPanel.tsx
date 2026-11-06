"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError } from "@/lib/api";
import type { ChatActions, ChatMessage } from "@/lib/types";
import { fmtPrice } from "@/lib/format";

type Entry =
  | { kind: "msg"; msg: ChatMessage }
  | { kind: "failed"; localId: string; content: string; error: string };

interface Props {
  open: boolean;
  onToggle: () => void;
  onActions: (actions: ChatActions) => void;
}

export function ChatPanel({ open, onToggle, onActions }: Props) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [pending, setPending] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [historyError, setHistoryError] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api
      .chatHistory()
      .then((r) => setEntries(r.messages.map((m) => ({ kind: "msg", msg: m }))))
      .catch((e: ApiError) => setHistoryError(e.message));
  }, []);

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries, pending, open]);

  const send = async (text: string, replaceLocalId?: string) => {
    const content = text.trim();
    if (!content || pending) return;
    setPending(content);
    if (replaceLocalId) setEntries((es) => es.filter((e) => !(e.kind === "failed" && e.localId === replaceLocalId)));
    try {
      const reply = await api.chat(content);
      const userMsg: ChatMessage = {
        id: `local-${Date.now()}`,
        role: "user",
        content,
        created_at: Date.now() / 1000,
        actions: null,
      };
      setEntries((es) => [...es, { kind: "msg", msg: userMsg }, { kind: "msg", msg: reply }]);
      const a = reply.actions;
      if (a && (a.trades?.length || a.watchlist_changes?.length)) onActions(a);
    } catch (e) {
      const err = e instanceof ApiError ? e.message : "Something went wrong";
      setEntries((es) => [...es, { kind: "failed", localId: `f-${Date.now()}`, content, error: err }]);
    } finally {
      setPending(null);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!input.trim() || pending) return;
    const text = input;
    setInput("");
    send(text);
  };

  if (!open) {
    return (
      <aside className="flex flex-col items-center w-9 bg-panel border border-line rounded-sm py-2">
        <button
          data-testid="chat-toggle"
          onClick={onToggle}
          className="text-accent text-xs [writing-mode:vertical-rl] tracking-[0.2em] uppercase py-2 hover:text-text"
          title="Open AI assistant"
        >
          ◂ FinAlly AI
        </button>
      </aside>
    );
  }

  return (
    <aside className="flex flex-col h-full min-h-0 w-full bg-panel border border-line rounded-sm" data-testid="chat-panel">
      <div className="flex items-center justify-between px-3 py-2 border-b border-line">
        <div className="flex items-center gap-2">
          <span className="h-1.5 w-1.5 rounded-full bg-accent" />
          <span className="text-[10px] uppercase tracking-[0.18em] text-muted">FinAlly AI</span>
        </div>
        <button
          data-testid="chat-toggle"
          onClick={onToggle}
          className="text-faint hover:text-text text-xs"
          title="Collapse"
        >
          ▸
        </button>
      </div>

      <div ref={scroller} className="flex-1 min-h-0 overflow-y-auto px-3 py-3 space-y-3" data-testid="chat-messages">
        {entries.length === 0 && !pending && (
          <div className="text-xs text-faint leading-relaxed">
            {historyError ? (
              <span className="text-down">Couldn&apos;t load history: {historyError}</span>
            ) : (
              <>
                Ask about your portfolio, request analysis, or tell me to trade.
                <div className="mt-2 space-y-1 text-muted">
                  {["How is my portfolio doing?", "Buy 5 NVDA", "Watch PYPL"].map((s) => (
                    <button
                      key={s}
                      onClick={() => send(s)}
                      className="block text-left hover:text-accent"
                    >
                      › {s}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        )}

        {entries.map((e) =>
          e.kind === "msg" ? (
            <Message key={e.msg.id} msg={e.msg} />
          ) : (
            <div key={e.localId} className="flex flex-col items-end gap-1" data-testid="chat-message-failed">
              <div className="max-w-[90%] rounded-sm px-2.5 py-1.5 text-[13px] bg-blue/10 border border-down/50 text-text whitespace-pre-wrap">
                {e.content}
              </div>
              <div className="text-[11px] text-down flex items-center gap-2" data-testid="chat-error">
                ✗ {e.error}
                <button
                  onClick={() => send(e.content, e.localId)}
                  className="underline text-muted hover:text-text"
                  disabled={!!pending}
                >
                  Retry
                </button>
              </div>
            </div>
          ),
        )}

        {pending && (
          <>
            <div className="flex justify-end">
              <div className="max-w-[90%] rounded-sm px-2.5 py-1.5 text-[13px] bg-blue/10 border border-blue/30 text-text whitespace-pre-wrap">
                {pending}
              </div>
            </div>
            <div className="typing flex gap-1 text-accent text-lg leading-none" data-testid="chat-loading">
              <span>●</span>
              <span>●</span>
              <span>●</span>
            </div>
          </>
        )}
      </div>

      <form onSubmit={submit} className="p-2 border-t border-line flex gap-1">
        <input
          data-testid="chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask FinAlly…"
          className="flex-1 min-w-0 bg-bg border border-line rounded-sm px-2 py-1.5 text-[13px] placeholder:text-faint focus:outline-none focus:border-purple"
        />
        <button
          data-testid="chat-send"
          type="submit"
          disabled={!input.trim() || !!pending}
          className="px-3 py-1.5 text-xs font-semibold rounded-sm bg-purple text-white hover:bg-purple-hi disabled:opacity-40"
        >
          Send
        </button>
      </form>
    </aside>
  );
}

function Message({ msg }: { msg: ChatMessage }) {
  if (msg.role === "user") {
    return (
      <div className="flex justify-end" data-testid="chat-message-user">
        <div className="max-w-[90%] rounded-sm px-2.5 py-1.5 text-[13px] bg-blue/10 border border-blue/30 text-text whitespace-pre-wrap">
          {msg.content}
        </div>
      </div>
    );
  }
  const a = msg.actions;
  return (
    <div className="flex flex-col gap-1" data-testid="chat-message-assistant">
      <div className="max-w-[95%] text-[13px] text-text leading-relaxed whitespace-pre-wrap border-l-2 border-accent/60 pl-2.5">
        {msg.content}
      </div>
      {a && (a.trades?.length > 0 || a.watchlist_changes?.length > 0) && (
        <ul className="pl-3 space-y-0.5" data-testid="chat-actions">
          {a.watchlist_changes?.map((w, i) => (
            <li
              key={`w${i}`}
              data-testid="chat-action"
              data-status={w.status}
              className={`num text-[11px] ${w.status === "executed" ? "text-up" : "text-down"}`}
            >
              {w.status === "executed" ? "✓" : "✗"} {w.action === "add" ? "Watch" : "Unwatch"} {w.ticker}
              {w.error && <span className="text-muted"> — {w.error}</span>}
            </li>
          ))}
          {a.trades?.map((t, i) => (
            <li
              key={`t${i}`}
              data-testid="chat-action"
              data-status={t.status}
              className={`num text-[11px] ${t.status === "executed" ? "text-up" : "text-down"}`}
            >
              {t.status === "executed" ? "✓" : "✗"} {t.side === "buy" ? "Buy" : "Sell"} {t.quantity} {t.ticker}
              {t.status === "executed" && t.price != null && <span className="text-muted"> @ {fmtPrice(t.price)}</span>}
              {t.error && <span className="text-muted"> — {t.error}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
