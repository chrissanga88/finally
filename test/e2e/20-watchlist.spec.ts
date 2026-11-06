import { test, expect } from '@playwright/test';
import { getWatchlist, openApp, removeFromWatchlistIfPresent, tid } from './helpers';

test.describe('Watchlist UI', () => {
  test('add and remove a ticker', async ({ page, request }) => {
    const ticker = 'AMD';
    await removeFromWatchlistIfPresent(request, ticker);
    await openApp(page);
    await expect(tid(page, `watchlist-row-${ticker}`)).toHaveCount(0);

    await tid(page, 'watchlist-input').fill(ticker.toLowerCase());
    await tid(page, 'watchlist-add').click();

    await expect(tid(page, `watchlist-row-${ticker}`)).toBeVisible();
    // New ticker gets simulated prices via the stream.
    await expect(tid(page, `price-${ticker}`)).toHaveText(/\d+\.\d{2}/, { timeout: 15_000 });
    expect(await getWatchlist(request)).toContain(ticker);

    // Survives reload (persisted).
    await page.reload();
    await expect(tid(page, `watchlist-row-${ticker}`)).toBeVisible();

    await tid(page, `remove-${ticker}`).click();
    await expect(tid(page, `watchlist-row-${ticker}`)).toHaveCount(0);
    expect(await getWatchlist(request)).not.toContain(ticker);
  });

  test('invalid ticker shows an error and adds no row', async ({ page }) => {
    await openApp(page);
    const rows = tid(page, 'watchlist').locator('[data-testid^="watchlist-row-"]');
    const count = await rows.count();
    await tid(page, 'watchlist-input').fill('123');
    await tid(page, 'watchlist-input').press('Enter');
    await expect(tid(page, 'watchlist-error')).toBeVisible();
    await expect(rows).toHaveCount(count);
  });
});
