import { expect, test } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { id, inspection, retrievalInspection, supportInspection } from '../src/testFixture';

test('direct form, guarded synthetic data, local validation and narrow layout', async ({ page }) => {
  if (process.env.INCIDENTCLUE_FORM_SCREENSHOT) {
    await page.goto('/'); await page.screenshot({ path: process.env.INCIDENTCLUE_FORM_SCREENSHOT, fullPage: true });
  }
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/'); await expect(page.getByRole('heading', { name: 'New incident' })).toBeVisible();
  await page.getByLabel('Incident ID', { exact: true }).fill('retained');
  await page.getByRole('button', { name: 'Load synthetic example' }).click();
  await expect(page.getByLabel('Incident ID', { exact: true })).toHaveValue('retained');
  await page.getByRole('button', { name: 'Replace input' }).click();
  await page.getByLabel('Health snapshot · JSON').fill('{bad');
  await page.getByRole('button', { name: 'Create incident' }).click();
  await expect(page.getByRole('alert')).toHaveText('Health snapshot: invalid JSON.');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('lost resume response retains UUID and requires manual inspection', async ({ page }) => {
  let posts = 0;
  await page.route('**/api/investigations/**', async route => {
    if (route.request().method() === 'POST') { posts++; await route.abort(); }
    else await route.fulfill({ json: inspection() });
  });
  await page.goto('/#/investigations/' + id);
  await page.getByRole('button', { name: 'Resume', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Response outcome unknown' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Resume', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Inspect state', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Resume', exact: true })).toBeEnabled();
  await expect(page.locator('.identity>code')).toHaveText(id); expect(posts).toBe(1);
});

test('partial state and untrusted evidence remain readable text', async ({ page }) => {
  await page.route('**/api/investigations/**', route => route.fulfill({ json: inspection({
    compatible: false, state: { evidence: [{ id: 'E1', kind: 'log', timestamp: '2026-11-01T00:00:00Z', content: '<script>window.bad=true</script>' }] },
  }) }));
  await page.goto('/#/investigations/' + id);
  await expect(page.getByText('<script>window.bad=true</script>', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Resume', exact: true })).toBeDisabled();
  await expect(page.getByText('No validated diagnosis saved.')).toBeVisible();
});

test('saved runbook citations focus exact excerpts and remain readable on narrow screens', async ({ page }) => {
  await page.route('**/api/investigations/**', route => route.fulfill({ json: retrievalInspection() }));
  await page.goto('/#/investigations/' + id);
  await page.getByRole('button', { name: 'Dependency name resolution · Checks', exact: true }).click();
  await expect(page.locator('article[id^="source-"]')).toBeFocused();
  await expect(page.locator('article[id^="source-"]').getByText(/<script>window.bad=true<\/script>/)).toBeVisible();
  expect(await page.evaluate(() => 'bad' in window)).toBe(false);
  await page.getByText('Source identity and location', { exact: true }).click();
  for (const width of [1280, 375]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    if (process.env.INCIDENTCLUE_RAG_SCREENSHOT_DIR)
      await page.screenshot({ path: resolve(process.env.INCIDENTCLUE_RAG_SCREENSHOT_DIR, `runbooks-${width}.png`), fullPage: true });
  }
});

test('support explanations retain full evidence context on wide and narrow screens', async ({ page }) => {
  await page.route('**/api/investigations/**', route => route.fulfill({ json: supportInspection() }));
  await page.goto('/#/investigations/' + id);
  const support = page.locator('.support-assessment');
  await expect(support.getByRole('heading', { name: 'Evidence support' })).toBeVisible();
  await support.getByText('Full observation E1').click();
  await expect(support.getByText(/Request mapping is unknown. <script>/)).toBeVisible();
  expect(await page.evaluate(() => 'bad' in window)).toBe(false);
  for (const width of [1280, 375]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await expect(support.getByText('The affected worker and request mapping is unknown.')).toBeVisible();
    if (process.env.INCIDENTCLUE_SUPPORT_SCREENSHOT_DIR)
      await page.screenshot({ path: resolve(process.env.INCIDENTCLUE_SUPPORT_SCREENSHOT_DIR, `support-${width}.png`), fullPage: true });
  }
});

test('real API create, cooperative pause during resume, refresh, diagnosis and saved result', async ({ page, request }) => {
  test.skip(!process.env.INCIDENTCLUE_BROWSER_DATABASE_URL, 'Requires isolated PostgreSQL browser fixture.');
  await page.goto('/'); await page.getByRole('button', { name: 'Load synthetic example' }).click();
  await page.getByRole('button', { name: 'Create incident' }).click();
  await expect(page.getByText('Created paused. No attempts charged. Resume starts execution.')).toBeVisible();
  const uuid = await page.locator('.identity>code').innerText();
  const cli = (...args: string[]) => JSON.parse(execFileSync(process.env.INCIDENTCLUE_PYTHON || 'python',
    ['-m', 'incidentclue.cli', ...args], { encoding: 'utf8', windowsHide: true,
      env: { ...process.env, INCIDENTCLUE_DATABASE_URL: process.env.INCIDENTCLUE_BROWSER_DATABASE_URL,
        GROQ_API_KEY: '', OPENAI_API_KEY: '' } }));
  expect(cli('inspect', uuid).charged).toEqual({ model: 0, tool: 0 });
  await page.getByRole('button', { name: 'Resume', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Resuming…' })).toBeDisabled();
  await page.getByRole('button', { name: 'Request pause', exact: true }).click();
  await expect(page.locator('.status')).toHaveText('paused', { timeout: 15_000 });
  await expect(page.getByText('1 / 3', { exact: true })).toBeVisible();
  await page.reload(); await expect(page.locator('.identity>code')).toHaveText(uuid);
  await page.getByLabel('Pause after').selectOption('execute');
  await page.getByRole('button', { name: 'Resume', exact: true }).click();
  await expect(page.getByText('1 / 2', { exact: true })).toBeVisible();
  await page.getByLabel('Pause after').selectOption('');
  await page.getByRole('button', { name: 'Resume', exact: true }).click();
  await expect(page.locator('.status')).toHaveText('completed', { timeout: 20_000 });
  await page.getByRole('button', { name: 'H1', exact: true }).click();
  await expect(page.locator('#evidence-H1')).toBeFocused();
  await expect(page.getByText('Hypothesis · unconfirmed')).toBeVisible();
  const before = await (await request.get('/api/investigations/' + uuid)).json();
  const saved = await request.post('/api/investigations/' + uuid + '/resume');
  expect(saved.status()).toBe(200);
  const after = await (await request.get('/api/investigations/' + uuid)).json();
  expect(after.charged).toEqual(before.charged); expect(after.attempts).toEqual(before.attempts);
  expect(cli('resume', uuid).charged).toEqual(before.charged);
  if (process.env.INCIDENTCLUE_SCREENSHOT) await page.screenshot({ path: process.env.INCIDENTCLUE_SCREENSHOT, fullPage: true });
  const started = cli('start', resolve('../data/synthetic/checkout-startup.json'),
    '--health-file', resolve('../data/synthetic/checkout-health.json'), '--paused');
  await page.goto('/#/investigations/' + started.investigation_id);
  await expect(page.locator('.identity>code')).toHaveText(started.investigation_id);
  await expect(page.getByText('0 / 3', { exact: true })).toBeVisible();
});
