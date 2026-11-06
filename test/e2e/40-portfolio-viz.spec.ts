import { test, expect } from '@playwright/test';
import { ensureOnWatchlist, numOf, openApp, tid, waitForPrice } from './helpers';

function rgb(color: string): [number, number, number] {
  const m = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  if (!m) throw new Error(`unparseable color ${color}`);
  return [Number(m[1]), Number(m[2]), Number(m[3])];
}

test.describe('Portfolio visualizations', () => {
  test('heatmap tiles colored by P&L and P&L chart has data', async ({ page, request }) => {
    for (const t of ['NVDA', 'TSLA']) {
      await ensureOnWatchlist(request, t);
      await waitForPrice(request, t);
      const res = await request.post('/api/portfolio/trade', { data: { ticker: t, side: 'buy', quantity: 2 } });
      expect(res.status()).toBe(200);
    }
    await openApp(page);

    const heatmap = tid(page, 'heatmap');
    await expect(heatmap).toBeVisible();
    for (const t of ['NVDA', 'TSLA']) {
      const tile = tid(page, `heatmap-cell-${t}`);
      await expect(tile).toBeVisible();
      await expect(tile).toContainText(t);
      const box = await tile.boundingBox();
      expect(box!.width * box!.height).toBeGreaterThan(0);
    }

    // Colors follow P&L sign. Prices move, so wait until the tile has a clearly non-zero P&L.
    const tile = tid(page, 'heatmap-cell-NVDA');
    await expect
      .poll(async () => Math.abs(Number(await tile.getAttribute('data-pnl'))), { timeout: 20_000 })
      .toBeGreaterThan(0.01);
    const check = async () => {
      const pnl = Number(await tile.getAttribute('data-pnl'));
      const [r, g] = rgb(await tile.evaluate((el) => getComputedStyle(el).backgroundColor));
      return pnl > 0 ? g > r : r > g;
    };
    await expect.poll(check, { timeout: 5_000 }).toBe(true);

    // P&L chart: initial snapshot + snapshots from trades.
    const pnl = tid(page, 'pnl-chart');
    await expect(pnl).toBeVisible();
    await expect(pnl.locator('canvas').first()).toBeVisible();
    await expect.poll(async () => Number(await pnl.getAttribute('data-points'))).toBeGreaterThanOrEqual(2);
  });

  test('header total value updates live and matches cash + positions', async ({ page, request }) => {
    await openApp(page);
    const pf = await (await request.get('/api/portfolio')).json();
    test.skip(pf.positions.length === 0, 'needs a held position');
    const first = await numOf(tid(page, 'total-value'));
    expect(first).toBeCloseTo(pf.total_value, -1); // within ~$5 given live ticks
    await expect.poll(() => numOf(tid(page, 'total-value')), { timeout: 15_000 }).not.toBe(first);
  });
});
