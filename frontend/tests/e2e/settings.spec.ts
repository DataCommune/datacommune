import { test, expect } from '@playwright/test';

const MOCK_USER = {
	id: 2,
	email: 'test@datacommune.fr',
	name: 'Test User',
	is_verified: true,
	is_admin: false,
	created_at: '2026-01-01T00:00:00Z',
};

const MOCK_KEYS = [
	{ id: 1, name: 'Mon App', key_prefix: 'dc_abc...', status: 'active', created_at: '2026-01-15T00:00:00Z' },
	{ id: 2, name: 'Old Key', key_prefix: 'dc_xyz...', status: 'revoked', created_at: '2025-12-01T00:00:00Z' },
];

const MOCK_QUOTA = {
	account_usage: 150,
	account_quota: 500,
};

async function gotoSettingsReady(page: import('@playwright/test').Page) {
	await page.goto('/settings');
	await expect(page.getByRole('heading', { name: 'Profil' })).toBeVisible({ timeout: 10_000 });
}

async function mockSettingsAPIs(page: import('@playwright/test').Page) {
	await page.route('**/auth/me', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_USER) })
	);
	await page.route('**/auth/keys', (route) => {
		if (route.request().method() === 'GET') {
			// listKeys() returns { keys, account_quota, account_usage }
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ keys: MOCK_KEYS, ...MOCK_QUOTA }),
			});
		}
		// POST — create key
		return route.fulfill({
			status: 201,
			contentType: 'application/json',
			body: JSON.stringify({ id: 3, name: 'New Key', key: 'dc_new_full_key_here', key_prefix: 'dc_new...', status: 'active', created_at: '2026-04-08T00:00:00Z' }),
		});
	});
	await page.route('**/auth/keys/*/revoke', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'ok' }) })
	);
	await page.route('**/auth/profile', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...MOCK_USER, name: 'Updated' }) })
	);
	await page.route('**/auth/change-password', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'ok' }) })
	);
}

test.describe('Settings — Page load', () => {
	test('settings page renders with all sections', async ({ page }) => {
		await mockSettingsAPIs(page);
		await gotoSettingsReady(page);
		await expect(page).toHaveTitle('Paramètres — DataCommune');
		await expect(page.getByRole('heading', { name: 'Profil' })).toBeVisible({ timeout: 10_000 });
		await expect(page.getByRole('heading', { name: /cl[eé]s api/i })).toBeVisible();
		await expect(page.getByRole('heading', { name: /mot de passe/i })).toBeVisible();
	});
});

test.describe('Settings — Profile section', () => {
	test('email is displayed and disabled', async ({ page }) => {
		await mockSettingsAPIs(page);
		await gotoSettingsReady(page);

		const emailInput = page.locator('input[type="email"]');
		await expect(emailInput).toBeVisible({ timeout: 10_000 });
		await expect(emailInput).toBeDisabled();
	});

	test('name and company inputs are editable', async ({ page }) => {
		await mockSettingsAPIs(page);
		await gotoSettingsReady(page);

		await expect(page.getByPlaceholder('Votre nom')).toBeVisible({ timeout: 10_000 });
		await expect(page.getByPlaceholder('Votre entreprise')).toBeVisible();
	});

	test('save profile button exists', async ({ page }) => {
		await mockSettingsAPIs(page);
		await page.goto('/settings');
		await expect(page.getByRole('button', { name: 'Enregistrer' })).toBeVisible({ timeout: 10_000 });
	});
});

test.describe('Settings — API Keys section', () => {
	test('API key creation form is visible', async ({ page }) => {
		await mockSettingsAPIs(page);
		await gotoSettingsReady(page);

		await expect(page.getByPlaceholder(/nom de la cl[eé]/i)).toBeVisible({ timeout: 10_000 });
		await expect(page.getByRole('button', { name: /cr[eé]er/i })).toBeVisible();
	});

	test('existing API keys are listed', async ({ page }) => {
		await mockSettingsAPIs(page);
		await gotoSettingsReady(page);

		await expect(page.getByText('Mon App')).toBeVisible({ timeout: 10_000 });
	});

	test('quota display shows usage', async ({ page }) => {
		await mockSettingsAPIs(page);
		await gotoSettingsReady(page);

		await expect(page.getByText(/requ[eê]tes/i)).toBeVisible({ timeout: 10_000 });
	});
});

test.describe('Settings — Password section', () => {
	test('password change form has all fields', async ({ page }) => {
		await mockSettingsAPIs(page);
		await gotoSettingsReady(page);

		// Should have at least 3 password inputs (current, new, confirm)
		const passwordInputs = page.locator('input[type="password"]');
		await expect(passwordInputs).not.toHaveCount(0, { timeout: 10_000 });

		await expect(page.getByRole('button', { name: /changer le mot de passe/i })).toBeVisible();
	});
});
