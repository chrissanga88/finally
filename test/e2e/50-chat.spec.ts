import { test, expect } from '@playwright/test';
import { ensureOnWatchlist, getPortfolio, numOf, openApp, tid, waitForPrice } from './helpers';

test.describe('AI chat (LLM_MOCK=true)', () => {
  test('"buy" gets mock reply, executed trade inline and in positions', async ({ page, request }) => {
    await ensureOnWatchlist(request, 'AAPL');
    await waitForPrice(request, 'AAPL');
    const before = (await getPortfolio(request)).positions.find((p: any) => p.ticker === 'AAPL')?.quantity ?? 0;

    await openApp(page);
    await expect(tid(page, 'chat-panel')).toBeVisible();
    const assistantMsgs = tid(page, 'chat-message-assistant');
    const n = await assistantMsgs.count();

    await tid(page, 'chat-input').fill('Please buy some apple');
    await tid(page, 'chat-send').click();

    await expect(tid(page, 'chat-message-user').last()).toContainText('Please buy some apple');
    await expect(assistantMsgs).toHaveCount(n + 1, { timeout: 15_000 });
    const reply = assistantMsgs.last();
    await expect(reply).toContainText('Mock: buying 1 share of AAPL.');
    const action = reply.getByTestId('chat-action').first();
    await expect(action).toHaveAttribute('data-status', 'executed');
    await expect(action).toContainText('AAPL');
    await expect(tid(page, 'chat-loading')).toHaveCount(0);

    // Positions table reflects the executed trade.
    await expect(tid(page, 'position-row-AAPL')).toBeVisible();
    await expect.poll(() => numOf(tid(page, 'position-qty-AAPL'))).toBeCloseTo(before + 1, 4);

    // History survives reload.
    await page.reload();
    await expect(tid(page, 'chat-message-assistant').filter({ hasText: 'Mock: buying 1 share of AAPL.' }).first()).toBeVisible();
  });

  test('"sell" beyond holdings shows a failed action inline', async ({ page, request }) => {
    // Ensure no AAPL held so the mock sell fails.
    const pf = await getPortfolio(request);
    const held = pf.positions.find((p: any) => p.ticker === 'AAPL');
    if (held) {
      expect((await request.post('/api/portfolio/trade', { data: { ticker: 'AAPL', side: 'sell', quantity: held.quantity } })).status()).toBe(200);
    }
    await openApp(page);
    await tid(page, 'chat-input').fill('sell it');
    await tid(page, 'chat-input').press('Enter');
    const reply = tid(page, 'chat-message-assistant').filter({ hasText: 'Mock: selling 1 share of AAPL.' }).last();
    await expect(reply).toBeVisible({ timeout: 15_000 });
    await expect(reply.getByTestId('chat-action').first()).toHaveAttribute('data-status', 'failed');
  });

  test('chat panel is collapsible', async ({ page }) => {
    await openApp(page);
    await expect(tid(page, 'chat-input')).toBeVisible();
    await tid(page, 'chat-toggle').click();
    await expect(tid(page, 'chat-input')).toBeHidden();
    await tid(page, 'chat-toggle').click();
    await expect(tid(page, 'chat-input')).toBeVisible();
  });
});
