import { APIRequestContext, Locator, Page, expect } from '@playwright/test';

export const DEFAULT_TICKERS = ['AAPL', 'GOOGL', 'MSFT', 'AMZN', 'TSLA', 'NVDA', 'META', 'JPM', 'V', 'NFLX'];

/** Parse "$10,000.00", "-1.23%", "+0.5" etc. into a number. */
export function parseNum(text: string | null | undefined): number {
  const cleaned = (text ?? '').replace(/[^0-9.\-]/g, '');
  const n = Number.parseFloat(cleaned);
  if (Number.isNaN(n)) throw new Error(`Cannot parse number from "${text}"`);
  return n;
}

export async function numOf(loc: Locator): Promise<number> {
  return parseNum(await loc.innerText());
}

export const tid = (page: Page, id: string) => page.getByTestId(id);

/** Expect the standard error shape {error, code}. */
export async function expectError(res: { status(): number; json(): Promise<any> }, status: number, code: string) {
  const body = await res.json();
  expect(res.status(), `status for ${code}: ${JSON.stringify(body)}`).toBe(status);
  expect(body).toEqual(expect.objectContaining({ code, error: expect.any(String) }));
  expect(body.error.length).toBeGreaterThan(0);
}

export async function getPortfolio(request: APIRequestContext) {
  const res = await request.get('/api/portfolio');
  expect(res.status()).toBe(200);
  return res.json();
}

export async function getWatchlist(request: APIRequestContext): Promise<string[]> {
  const res = await request.get('/api/watchlist');
  expect(res.status()).toBe(200);
  const body = await res.json();
  return body.tickers.map((t: any) => t.ticker);
}

/** Wait until the backend has a cached price for the ticker (via /api/watchlist). */
export async function waitForPrice(request: APIRequestContext, ticker: string) {
  await expect
    .poll(async () => {
      const body = await (await request.get('/api/watchlist')).json();
      return body.tickers.find((t: any) => t.ticker === ticker)?.price ?? null;
    }, { timeout: 15_000 })
    .not.toBeNull();
}

export async function ensureOnWatchlist(request: APIRequestContext, ticker: string) {
  const res = await request.post('/api/watchlist', { data: { ticker } });
  expect([200, 201]).toContain(res.status());
}

export async function removeFromWatchlistIfPresent(request: APIRequestContext, ticker: string) {
  const res = await request.delete(`/api/watchlist/${ticker}`);
  expect([204, 404]).toContain(res.status());
}

/** Sell any held quantity of the ticker so a test starts with no position. */
export async function closePosition(request: APIRequestContext, ticker: string) {
  const pf = await getPortfolio(request);
  const pos = pf.positions.find((p: any) => p.ticker === ticker);
  if (pos) {
    const res = await request.post('/api/portfolio/trade', { data: { ticker, side: 'sell', quantity: pos.quantity } });
    expect(res.status()).toBe(200);
  }
}

/** Load the app and wait for the SSE connection to be live. */
export async function openApp(page: Page) {
  await page.goto('/');
  await expect(tid(page, 'connection-status')).toHaveAttribute('data-status', 'connected', { timeout: 15_000 });
}
