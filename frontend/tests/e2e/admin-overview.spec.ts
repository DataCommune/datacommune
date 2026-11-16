import { readFileSync } from 'node:fs';
import { test, expect } from '@playwright/test';
import { installLocalNetworkGuard, expectNoUnexpectedOffLocalRequest } from './fixtures/local-network';

// Actual HTTP response captured after inserting a free Pro grant in disposable PostGIS.
// The auth identity is synthetic; no provider, shared DB or admin write is contacted.
const payload = JSON.parse(readFileSync(new URL('../../../docs/audit-stabilisation-2026-09-08/lot-08/mrr-api-fixture.json', import.meta.url), 'utf8'));

for (const width of [375, 1280]) {
	test(`admin overview keeps MRR unavailable with its explanation at ${width}px`, async ({ page }, testInfo) => {
		await page.setViewportSize({ width, height: 900 });
		await installLocalNetworkGuard(page);
		await page.route('**/auth/me', route => route.fulfill({
			status: 200, contentType: 'application/json', body: JSON.stringify({
				id: 99001, email: 'admin@example.com', name: 'Admin', is_verified: true,
				is_admin: true, created_at: '2026-01-01T00:00:00Z'
			})
		}));
		let overviewCalls = 0;
		await page.route('**/v1/admin/**', route => {
			if (new URL(route.request().url()).pathname !== '/v1/admin/overview' || route.request().method() !== 'GET') {
				return route.abort('blockedbyclient');
			}
			overviewCalls += 1;
			return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload) });
		});
		await page.goto('/admin');
		await expect(page.getByRole('heading', { name: 'Administration', exact: true })).toBeVisible();
		const card = page.locator('.ds-bloc-kpi').filter({ hasText: /^MRR/ });
		await expect(card).toContainText('Indisponible');
		await expect(card).not.toContainText('€');
		await expect(page.getByText(/montants et la cadence de facturation/)).toBeVisible();
		await expect(page.getByText(/ne mesure pas les encaissements/)).toBeVisible();
		await expect(page.locator('.ds-bloc-kpi').filter({ hasText: 'Utilisateurs' })).toContainText(String(payload.users_count));
		await expect(page.locator('.ds-bloc-kpi').filter({ hasText: 'Clés actives' })).toContainText(String(payload.active_keys_count));
		await expect(page.locator('.ds-bloc-kpi').filter({ hasText: 'Req/24h' })).toContainText(String(payload.requests_24h));
		expect(overviewCalls).toBe(1);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
		expectNoUnexpectedOffLocalRequest(page);
		await testInfo.attach(`mrr-${width}px`, { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
	});
}
