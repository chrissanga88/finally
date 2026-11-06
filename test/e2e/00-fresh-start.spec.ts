import { test, expect } from '@playwright/test';
import { DEFAULT_TICKERS, numOf, openApp, tid } from './helpers';

// Must run first, against a fresh DB volume.
test.describe('Fresh start', () => {
  test('API reports seeded default state', async ({ request }) => {
    const pf = await (await request.get('/api/portfolio')).json();
    expect(pf.cash_balance).toBe(10000);
    expect(pf.positions).toEqual([]);
    expect(pf.total_value).toBe(10000);

    const wl = await (await request.get('/api/watchlist')).json();
    expect(wl.tickers.map((t: any) => t.ticker).sort()).toEqual([...DEFAULT_TICKERS].sort());

    const hist = await (await request.get('/api/portfolio/history')).json();
    expect(hist.snapshots.length).toBeGreaterThanOrEqual(1);
    expect(hist.snapshots[0].total_value).toBe(10000);
  });

  test('UI shows 10 default tickers, $10,000 cash and streaming prices', async ({ page }) => {
    await openApp(page);

    for (const t of DEFAULT_TICKERS) {
      await expect(tid(page, `watchlist-row-${t}`)).toBeVisible();
    }
    await expect(tid(page, 'watchlist').locator('[data-testid^="watchlist-row-"]')).toHaveCount(10);

    await expect(tid(page, 'cash-balance')).toContainText('10,000');
    expect(await numOf(tid(page, 'cash-balance'))).toBe(10000);
    expect(await numOf(tid(page, 'total-value'))).toBeCloseTo(10000, 0);

    // Prices populate...
    for (const t of DEFAULT_TICKERS) {
      await expect(tid(page, `price-${t}`)).toHaveText(/\d+\.\d{2}/);
    }
    // ...and change over time (any ticker moving proves the stream is live).
    const snapshot = async () =>
      Promise.all(DEFAULT_TICKERS.map((t) => tid(page, `price-${t}`).innerText()));
    const first = (await snapshot()).join('|');
    await expect.poll(async () => (await snapshot()).join('|'), { timeout: 10_000 }).not.toBe(first);

    // AI chat panel open by default.
    await expect(tid(page, 'chat-panel')).toBeVisible();
    await expect(tid(page, 'chat-input')).toBeVisible();
  });

  test('clicking a ticker selects it in the main chart', async ({ page }) => {
    await openApp(page);
    await tid(page, 'watchlist-row-MSFT').click();
    await expect(tid(page, 'main-chart-ticker')).toContainText('MSFT');
    await expect(tid(page, 'main-chart').locator('canvas').first()).toBeVisible();
  });
});
