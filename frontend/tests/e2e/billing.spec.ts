import { test, expect } from '@playwright/test';
import { installLocalNetworkGuard, expectNoUnexpectedOffLocalRequest } from './fixtures/local-network';

const MOCK_USER = {
	id: 2,
	email: 'test@datacommune.fr',
	name: 'Test User',
	is_verified: true,
	is_admin: false,
	created_at: '2026-01-01T00:00:00Z',
	plan: 'free',
};

async function mockBillingAPIs(page: import('@playwright/test').Page) {
	await page.route('**/auth/me', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_USER) })
	);
}

// /billing is shadowed by Vite dev proxy. Navigate via the account menu of the site header.
async function gotoBilling(page: import('@playwright/test').Page, returnQuery = '') {
	await page.goto('/settings');
	await expect(page.getByRole('heading', { name: /param[eè]tres/i })).toBeVisible({ timeout: 10_000 });
	// Single site header (03-03, D-50): the same « Mon espace » menu at every width, no burger.
	await page.getByRole('banner').getByRole('button', { name: 'Mon espace' }).click();
	const link = page.getByRole('link', { name: 'Facturation', exact: true });
	// Route the synthetic return through SvelteKit; Vite reserves /billing for its API proxy.
	if (returnQuery) await link.evaluate((element, query) => element.setAttribute('href', `/billing?${query}`), returnQuery);
	await link.click();
	await expect(page.getByRole('heading', { name: 'Abonnement' })).toBeVisible({ timeout: 10_000 });
}

test.describe('Billing — Page load', () => {
	test('billing page renders with heading', async ({ page }) => {
		await mockBillingAPIs(page);
		await gotoBilling(page);
		await expect(page.getByText('Choisissez le plan adapté à vos besoins')).toBeVisible();
	});
});

test.describe('Billing — Plan cards', () => {
	test('all plan cards are displayed with real names', async ({ page }) => {
		await mockBillingAPIs(page);
		await gotoBilling(page);

		await expect(page.getByText('Découverte')).toBeVisible({ timeout: 5_000 });
		await expect(page.getByText('Pro').first()).toBeVisible();
		// Plan card heading h2 elements contain plan names
		await expect(page.getByRole('heading', { name: 'Business' })).toBeVisible({ timeout: 5_000 });
		await expect(page.getByRole('heading', { name: 'Enterprise' })).toBeVisible();
	});

	test('current plan (free/Découverte) shows "Plan actuel" button', async ({ page }) => {
		await mockBillingAPIs(page);
		await gotoBilling(page);
		await expect(page.getByRole('button', { name: 'Plan actuel' })).toBeVisible({ timeout: 5_000 });
	});

	test('upgrade buttons are visible for non-current plans', async ({ page }) => {
		await mockBillingAPIs(page);
		await gotoBilling(page);
		await expect(page.getByRole('button', { name: /passer au pro/i })).toBeVisible({ timeout: 5_000 });
		await expect(page.getByRole('button', { name: /passer au business/i })).toBeVisible();
		await expect(page.getByRole('button', { name: /passer au enterprise/i })).toBeVisible();
	});

	test('pricing and quota information is displayed', async ({ page }) => {
		await mockBillingAPIs(page);
		await gotoBilling(page);

		await expect(page.getByText('39€')).toBeVisible({ timeout: 5_000 });
		await expect(page.getByText('99€')).toBeVisible();
		await expect(page.getByText(/requ[eê]tes\/mois/).first()).toBeVisible();
	});

	test('Pro plan has "Recommandé" badge', async ({ page }) => {
		await mockBillingAPIs(page);
		await gotoBilling(page);
		await expect(page.getByText('Recommandé')).toBeVisible({ timeout: 5_000 });
	});
});

test.describe('Billing — Portal link', () => {
	test('manage subscription link is visible', async ({ page }) => {
		await mockBillingAPIs(page);
		await gotoBilling(page);
		await expect(page.getByRole('button', { name: /g[eé]rer mon abonnement/i })).toBeVisible({ timeout: 5_000 });
	});
});


for (const width of [375, 1280]) {
	test(`unverified Checkout return cannot claim payment or upgrade at ${width}px`, async ({ page }, testInfo) => {
		await page.setViewportSize({ width, height: 900 });
		await installLocalNetworkGuard(page);
		let accountReads = 0;
		const paymentRequests: string[] = [];
		page.on('request', request => {
			if (new URL(request.url()).pathname.startsWith('/billing/checkout')) paymentRequests.push(request.url());
		});
		await page.route('**/auth/me', route => {
			if (new URL(page.url()).pathname === '/billing') accountReads += 1;
			return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_USER) });
		});
		await gotoBilling(page, 'session_id=cs_test_synthetic_unverified');
		await expect(page.getByText('Retour de Checkout reçu. Vos droits seront actualisés après vérification de votre abonnement.')).toBeVisible();
		await expect(page.locator('main')).not.toContainText(/paiement (?:confirmé|réussi|validé)/i);
		await expect(page.getByRole('button', { name: 'Plan actuel', exact: true })).toHaveCount(1);
		await expect(page.getByRole('button', { name: /passer au pro/i })).toBeVisible();
		await expect.poll(() => accountReads).toBeGreaterThanOrEqual(1);
		await expect(page.getByRole('button', { name: /passer au pro/i })).toBeVisible();
		await expect(page.getByText(/Les refus d'authentification, de débit ou de quota ne sont pas décomptés/)).toBeVisible();
		expect(paymentRequests).toEqual([]);
		expectNoUnexpectedOffLocalRequest(page);
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
		await page.screenshot({ path: testInfo.outputPath(`checkout-unverified-${width}.png`), fullPage: true });
	});
}
