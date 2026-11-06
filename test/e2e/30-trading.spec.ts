import { test, expect, Page } from '@playwright/test';
import { closePosition, ensureOnWatchlist, getPortfolio, numOf, openApp, tid, waitForPrice } from './helpers';

async function trade(page: Page, ticker: string, qty: string, side: 'buy' | 'sell') {
  await tid(page, 'trade-ticker').fill(ticker);
  await tid(page, 'trade-quantity').fill(qty);
  const resp = page.waitForResponse((r) => r.url().includes('/api/portfolio/trade') && r.request().method() === 'POST');
  await tid(page, side === 'buy' ? 'trade-buy' : 'trade-sell').click();
  return (await resp).json();
}

test.describe('Trading UI', () => {
  const ticker = 'MSFT';

  test.beforeEach(async ({ request }) => {
    await ensureOnWatchlist(request, ticker);
    await waitForPrice(request, ticker);
    await closePosition(request, ticker);
  });

  test('buy shares: cash decreases and position appears', async ({ page, request }) => {
    await openApp(page);
    const cashBefore = (await getPortfolio(request)).cash_balance;
    await expect.poll(() => numOf(tid(page, 'cash-balance'))).toBeCloseTo(cashBefore, 2);

    const res = await trade(page, ticker, '3', 'buy');
    expect(res.trade.side).toBe('buy');

    await expect.poll(() => numOf(tid(page, 'cash-balance'))).toBeCloseTo(res.cash_balance, 2);
    expect(res.cash_balance).toBeLessThan(cashBefore);

    const row = tid(page, `position-row-${ticker}`);
    await expect(row).toBeVisible();
    expect(await numOf(tid(page, `position-qty-${ticker}`))).toBe(3);
    const pos = (await getPortfolio(request)).positions.find((p: any) => p.ticker === ticker);
    expect(pos.avg_cost).toBeCloseTo(res.trade.price, 2);
    await expect(tid(page, 'trade-error')).toHaveCount(0);
  });

  test('sell shares: cash increases, position updates then disappears', async ({ page, request }) => {
    const buy = await request.post('/api/portfolio/trade', { data: { ticker, side: 'buy', quantity: 4 } });
    expect(buy.status()).toBe(200);
    await openApp(page);
    await expect(tid(page, `position-row-${ticker}`)).toBeVisible();
    expect(await numOf(tid(page, `position-qty-${ticker}`))).toBe(4);
    const cashBefore = await numOf(tid(page, 'cash-balance'));

    const r1 = await trade(page, ticker, '1', 'sell');
    expect(r1.cash_balance).toBeGreaterThan(cashBefore);
    await expect.poll(() => numOf(tid(page, 'cash-balance'))).toBeCloseTo(r1.cash_balance, 2);
    await expect.poll(() => numOf(tid(page, `position-qty-${ticker}`))).toBe(3);

    const r2 = await trade(page, ticker, '3', 'sell');
    expect(r2.cash_balance).toBeGreaterThan(r1.cash_balance);
    await expect(tid(page, `position-row-${ticker}`)).toHaveCount(0);
  });

  test('errors are shown inline', async ({ page }) => {
    await openApp(page);
    await trade(page, ticker, '1000000', 'buy');
    await expect(tid(page, 'trade-error')).toBeVisible();
    await expect(tid(page, 'trade-error')).toContainText(/insufficient cash/i);

    await trade(page, ticker, '5', 'sell');
    await expect(tid(page, 'trade-error')).toContainText(/insufficient|shares/i);
    await expect(tid(page, `position-row-${ticker}`)).toHaveCount(0);
  });
});
