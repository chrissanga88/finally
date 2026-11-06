import { test, expect } from '@playwright/test';
import { closePosition, ensureOnWatchlist, expectError, getPortfolio, removeFromWatchlistIfPresent, waitForPrice } from './helpers';

test.describe('API: health & shapes', () => {
  test('GET /api/health', async ({ request }) => {
    const res = await request.get('/api/health');
    expect(res.status()).toBe(200);
    expect(await res.json()).toEqual({ status: 'ok' });
  });

  test('unknown /api/* path returns JSON 404 NOT_FOUND, not the HTML page', async ({ request }) => {
    const res = await request.get('/api/does-not-exist');
    expect(res.headers()['content-type']).toContain('application/json');
    await expectError(res, 404, 'NOT_FOUND');
  });

  test('GET /api/portfolio shape', async ({ request }) => {
    const pf = await getPortfolio(request);
    for (const k of ['cash_balance', 'total_value', 'positions_value', 'unrealized_pnl']) {
      expect(typeof pf[k], k).toBe('number');
    }
    expect(Array.isArray(pf.positions)).toBe(true);
  });

  test('GET /api/watchlist shape', async ({ request }) => {
    await waitForPrice(request, 'AAPL');
    const body = await (await request.get('/api/watchlist')).json();
    const aapl = body.tickers.find((t: any) => t.ticker === 'AAPL');
    expect(aapl).toEqual({
      ticker: 'AAPL',
      price: expect.any(Number),
      session_change_percent: expect.any(Number),
      added_at: expect.any(Number),
    });
    // Unix seconds, not ms or ISO.
    expect(aapl.added_at).toBeGreaterThan(1.6e9);
    expect(aapl.added_at).toBeLessThan(1e11);
  });

  test('GET /api/portfolio/history shape', async ({ request }) => {
    const body = await (await request.get('/api/portfolio/history')).json();
    expect(body.snapshots.length).toBeGreaterThanOrEqual(1);
    for (const s of body.snapshots) {
      expect(s).toEqual({ timestamp: expect.any(Number), total_value: expect.any(Number) });
    }
    const ts = body.snapshots.map((s: any) => s.timestamp);
    expect([...ts].sort((a, b) => a - b)).toEqual(ts); // oldest first
  });

  test('SSE stream emits a map of tracked tickers', async ({ page, baseURL }) => {
    await page.goto('/api/health'); // same-origin context for EventSource
    const data = await page.evaluate(
      (url) =>
        new Promise<any>((resolve, reject) => {
          const es = new EventSource(url);
          const timer = setTimeout(() => { es.close(); reject(new Error('no SSE event in 10s')); }, 10_000);
          es.onmessage = (e) => { clearTimeout(timer); es.close(); resolve(JSON.parse(e.data)); };
        }),
      `${baseURL}/api/stream/prices`,
    );
    expect(data.AAPL).toEqual(
      expect.objectContaining({
        ticker: 'AAPL',
        price: expect.any(Number),
        previous_price: expect.any(Number),
        timestamp: expect.any(Number),
        change: expect.any(Number),
        change_percent: expect.any(Number),
        direction: expect.stringMatching(/^(up|down|flat)$/),
        session_open: expect.any(Number),
        session_change_percent: expect.any(Number),
      }),
    );
  });
});

test.describe('API: watchlist', () => {
  test('add (201), add again (200), remove (204), remove again (404)', async ({ request }) => {
    await removeFromWatchlistIfPresent(request, 'PYPL');
    const add = await request.post('/api/watchlist', { data: { ticker: ' pypl ' } });
    expect(add.status()).toBe(201);
    expect(await add.json()).toEqual(expect.objectContaining({ ticker: 'PYPL', added_at: expect.any(Number) }));

    const again = await request.post('/api/watchlist', { data: { ticker: 'PYPL' } });
    expect(again.status()).toBe(200);

    const del = await request.delete('/api/watchlist/PYPL');
    expect(del.status()).toBe(204);

    await expectError(await request.delete('/api/watchlist/PYPL'), 404, 'NOT_FOUND');
  });

  test('invalid tickers are rejected with INVALID_TICKER', async ({ request }) => {
    for (const bad of ['123', 'TOOLONGX', 'A-B', '']) {
      await expectError(await request.post('/api/watchlist', { data: { ticker: bad } }), 400, 'INVALID_TICKER');
    }
  });

  test('malformed body returns VALIDATION_ERROR', async ({ request }) => {
    await expectError(await request.post('/api/watchlist', { data: {} }), 400, 'VALIDATION_ERROR');
  });
});

test.describe('API: trades', () => {
  test('validation errors use 400 VALIDATION_ERROR', async ({ request }) => {
    const cases = [
      { ticker: 'AAPL', quantity: 1 },                       // missing side
      { ticker: 'AAPL', side: 'short', quantity: 1 },        // bad side
      { ticker: 'AAPL', side: 'buy', quantity: 'abc' },      // non-number qty
      { side: 'buy', quantity: 1 },                          // missing ticker
    ];
    for (const data of cases) {
      await expectError(await request.post('/api/portfolio/trade', { data }), 400, 'VALIDATION_ERROR');
    }
  });

  test('domain errors', async ({ request }) => {
    await expectError(
      await request.post('/api/portfolio/trade', { data: { ticker: '12X', side: 'buy', quantity: 1 } }),
      400, 'INVALID_TICKER');
    for (const q of [0, -1, 0.00001]) {
      await expectError(
        await request.post('/api/portfolio/trade', { data: { ticker: 'AAPL', side: 'buy', quantity: q } }),
        400, 'INVALID_QUANTITY');
    }
    await waitForPrice(request, 'AAPL');
    await expectError(
      await request.post('/api/portfolio/trade', { data: { ticker: 'AAPL', side: 'buy', quantity: 1_000_000 } }),
      400, 'INSUFFICIENT_CASH');
    await closePosition(request, 'NFLX');
    await expectError(
      await request.post('/api/portfolio/trade', { data: { ticker: 'NFLX', side: 'sell', quantity: 1 } }),
      400, 'INSUFFICIENT_SHARES');
  });

  test('buy then sell updates cash with cent rounding and records snapshots', async ({ request }) => {
    const ticker = 'JPM';
    await ensureOnWatchlist(request, ticker);
    await waitForPrice(request, ticker);
    await closePosition(request, ticker);

    const before = await getPortfolio(request);
    const histBefore = (await (await request.get('/api/portfolio/history')).json()).snapshots.length;

    const buy = await request.post('/api/portfolio/trade', { data: { ticker, side: 'buy', quantity: 2.5 } });
    expect(buy.status()).toBe(200);
    const b = await buy.json();
    expect(b.trade).toEqual({
      id: expect.any(String), ticker, side: 'buy', quantity: 2.5,
      price: expect.any(Number), executed_at: expect.any(Number),
    });
    const cost = Math.round(2.5 * b.trade.price * 100) / 100;
    // JS Math.round vs Python round() can differ by a cent on half-cent values.
    expect(Math.abs(b.cash_balance - (before.cash_balance - cost))).toBeLessThanOrEqual(0.0101);

    const mid = await getPortfolio(request);
    const pos = mid.positions.find((p: any) => p.ticker === ticker);
    expect(pos).toEqual(expect.objectContaining({
      ticker, quantity: 2.5, avg_cost: b.trade.price,
      current_price: expect.any(Number), market_value: expect.any(Number),
      unrealized_pnl: expect.any(Number), unrealized_pnl_percent: expect.any(Number), weight: expect.any(Number),
    }));
    expect(pos.weight).toBeGreaterThan(0);
    expect(pos.weight).toBeLessThan(1);

    const histAfter = (await (await request.get('/api/portfolio/history')).json()).snapshots.length;
    expect(histAfter).toBeGreaterThan(histBefore);

    const sell = await request.post('/api/portfolio/trade', { data: { ticker, side: 'sell', quantity: 2.5 } });
    expect(sell.status()).toBe(200);
    const s = await sell.json();
    const proceeds = Math.round(2.5 * s.trade.price * 100) / 100;
    expect(Math.abs(s.cash_balance - (b.cash_balance + proceeds))).toBeLessThanOrEqual(0.0101);

    const after = await getPortfolio(request);
    expect(after.positions.find((p: any) => p.ticker === ticker)).toBeUndefined();
  });

  test('buying an untracked ticker adds it to the watchlist', async ({ request }) => {
    const ticker = 'ORCL';
    await closePosition(request, ticker);
    await removeFromWatchlistIfPresent(request, ticker);
    const res = await request.post('/api/portfolio/trade', { data: { ticker, side: 'buy', quantity: 1 } });
    // Simulator may not have priced it yet on the very first attempt -> NO_PRICE is allowed once.
    if (res.status() !== 200) {
      await expectError(res, 400, 'NO_PRICE');
      await waitForPrice(request, ticker);
      expect((await request.post('/api/portfolio/trade', { data: { ticker, side: 'buy', quantity: 1 } })).status()).toBe(200);
    }
    const wl = (await (await request.get('/api/watchlist')).json()).tickers.map((t: any) => t.ticker);
    expect(wl).toContain(ticker);

    // Removing from watchlist while held keeps it priced in the stream.
    expect((await request.delete(`/api/watchlist/${ticker}`)).status()).toBe(204);
    const pf = await getPortfolio(request);
    expect(pf.positions.find((p: any) => p.ticker === ticker)).toBeTruthy();
    await closePosition(request, ticker);
  });
});

test.describe('API: chat (LLM_MOCK=true)', () => {
  test('plain message returns mock greeting and persists in history', async ({ request }) => {
    const res = await request.post('/api/chat', { data: { message: 'hello there' } });
    expect(res.status()).toBe(200);
    const msg = await res.json();
    expect(msg).toEqual(expect.objectContaining({
      id: expect.any(String), role: 'assistant',
      content: "Mock: I'm FinAlly, your AI trading assistant.",
      created_at: expect.any(Number),
    }));
    const actions = msg.actions;
    const noActions = actions === null || ((actions.trades ?? []).length === 0 && (actions.watchlist_changes ?? []).length === 0);
    expect(noActions).toBe(true);

    const hist = await (await request.get('/api/chat/history')).json();
    const last2 = hist.messages.slice(-2);
    expect(last2[0]).toEqual(expect.objectContaining({ role: 'user', content: 'hello there', actions: null }));
    expect(last2[1]).toEqual(expect.objectContaining({ role: 'assistant', id: msg.id }));
  });

  test('"watch" adds PYPL via watchlist_changes', async ({ request }) => {
    await removeFromWatchlistIfPresent(request, 'PYPL');
    const msg = await (await request.post('/api/chat', { data: { message: 'please watch something' } })).json();
    expect(msg.content).toBe('Mock: adding PYPL to your watchlist.');
    expect(msg.actions.watchlist_changes).toEqual([
      expect.objectContaining({ ticker: 'PYPL', action: 'add', status: 'executed' }),
    ]);
    const wl = (await (await request.get('/api/watchlist')).json()).tickers.map((t: any) => t.ticker);
    expect(wl).toContain('PYPL');
    await removeFromWatchlistIfPresent(request, 'PYPL');
  });

  test('missing message is a VALIDATION_ERROR', async ({ request }) => {
    await expectError(await request.post('/api/chat', { data: {} }), 400, 'VALIDATION_ERROR');
  });
});
