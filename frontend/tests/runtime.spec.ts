import { expect, test } from '@playwright/test';

test('inspect a verified fixture and download its patch', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  if (process.env.ATLAS_E2E_API_KEY) {
    await page.addInitScript(
      (key) => sessionStorage.setItem('atlas.key', key),
      process.env.ATLAS_E2E_API_KEY,
    );
  }
  await page.goto('/');
  await expect(page.getByText('Runtime connected')).toBeAttached();
  await page.getByRole('button', { name: 'Run authentication demo' }).click();
  await page.getByRole('button', { name: 'Verification', exact: true }).click();
  await expect(page.getByText('Final checks passed')).toBeVisible();
  await expect(page.getByText('original tests preserved')).toBeVisible();
  await page.getByRole('button', { name: 'Patch', exact: true }).click();
  await expect(page.locator('pre.diff')).toContainText('test_regression.py');
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download .patch' }).click();
  expect((await downloaded).suggestedFilename()).toMatch(/^atlas-.*\.patch$/);
  await page.getByRole('button', { name: /Overview/ }).click();
  await expect(page.getByRole('heading', { name: 'Every step, accounted for.' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});
