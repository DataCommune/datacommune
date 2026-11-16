import { test, expect } from '@playwright/test';

const MOCK_ADMIN = {
	id: 1,
	email: 'admin@datacommune.fr',
	name: 'Admin',
	is_verified: true,
	is_admin: true,
	created_at: '2026-01-01T00:00:00Z',
};

const MOCK_NON_ADMIN = {
	id: 2,
	email: 'user@datacommune.fr',
	name: 'User',
	is_verified: true,
	is_admin: false,
	created_at: '2026-01-01T00:00:00Z',
};

const MOCK_DATASETS_RESPONSE = {
	statut: 'ok',
	donnees: [
		{
			nom: 'demographie',
			statut: 'active',
			nombre_lignes: 35000,
			fraicheur: 'fresh',
			derniere_maj: '2026-04-01T00:00:00Z',
			duree_import_secondes: 45,
			cron_schedule: '0 3 * * 1',
			derniere_run: {
				statut: 'success',
				debut: '2026-04-01T03:00:00Z',
				duree_secondes: 45,
				lignes_chargees: 35000,
				erreur: null,
			},
		},
		{
			nom: 'dvf',
			statut: 'active',
			nombre_lignes: 420000,
			fraicheur: 'stale',
			derniere_maj: '2026-02-15T00:00:00Z',
			duree_import_secondes: 120,
			cron_schedule: '0 3 1 * *',
			derniere_run: {
				statut: 'success',
				debut: '2026-02-15T03:00:00Z',
				duree_secondes: 120,
				lignes_chargees: 420000,
				erreur: null,
			},
		},
		{
			nom: 'bdnb',
			statut: 'active',
			nombre_lignes: 0,
			fraicheur: 'proxy',
			derniere_maj: null,
			duree_import_secondes: null,
			cron_schedule: null,
			derniere_run: null,
		},
	],
};

async function mockAuth(page: import('@playwright/test').Page, isAdmin = true) {
	const user = isAdmin ? MOCK_ADMIN : MOCK_NON_ADMIN;
	await page.route('**/auth/me', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(user) })
	);
}

async function mockDatasets(page: import('@playwright/test').Page) {
	await page.route('**/v1/datasets', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_DATASETS_RESPONSE) })
	);
}

test.describe('Admin — Access control', () => {
	test('non-admin user is redirected away from /admin', async ({ page }) => {
		await mockAuth(page, false);
		await page.goto('/admin');
		await expect(page).toHaveURL(/\/\?admin_required=1/, { timeout: 10_000 });
	});

	test('admin user can access /admin', async ({ page }) => {
		await mockAuth(page, true);
		await page.goto('/admin');
		await expect(page).toHaveTitle('Admin — DataCommune');
		await expect(page.getByRole('heading', { name: 'Administration' })).toBeVisible({ timeout: 10_000 });
		await expect(page.getByRole('link', { name: 'Utilisateurs' })).toBeVisible();
		await expect(page.getByRole('link', { name: 'Journal d\'audit' })).toBeVisible();
		await expect(page.getByRole('link', { name: 'Datasets' })).toBeVisible();
	});
});

test.describe('Admin — Datasets dashboard', () => {
	test('refresh and compare buttons are visible', async ({ page }) => {
		await mockAuth(page);
		await mockDatasets(page);
		await page.goto('/admin/datasets');
		await expect(page.getByRole('heading', { name: 'Validation des Donnees' })).toBeVisible({ timeout: 10_000 });
		await expect(page.getByRole('button', { name: 'Rafraichir' })).toBeVisible({ timeout: 10_000 });
		await expect(page.getByRole('button', { name: /comparer avec les sources/i })).toBeVisible();
	});

	test('summary cards show dataset count and freshness', async ({ page }) => {
		await mockAuth(page);
		await mockDatasets(page);
		await page.goto('/admin/datasets');
		await expect(page.getByRole('button', { name: 'Rafraichir' })).toBeVisible({ timeout: 10_000 });
		await expect(page.locator('text=Datasets').first()).toBeVisible({ timeout: 10_000 });
		await expect(page.locator('.text-xs.uppercase:has-text("Fresh")').first()).toBeVisible();
		await expect(page.locator('.text-xs.uppercase:has-text("Stale")').first()).toBeVisible();
	});

	test('dataset cards are rendered with names', async ({ page }) => {
		await mockAuth(page);
		await mockDatasets(page);
		await page.goto('/admin/datasets');
		await expect(page.getByRole('button', { name: 'Rafraichir' })).toBeVisible({ timeout: 10_000 });
		await expect(page.getByText('demographie').first()).toBeVisible({ timeout: 10_000 });
		await expect(page.getByText('dvf').first()).toBeVisible();
		await expect(page.getByText('bdnb').first()).toBeVisible();
	});

	test('clicking a dataset card opens detail modal', async ({ page }) => {
		await mockAuth(page);
		await mockDatasets(page);
		await page.goto('/admin/datasets');
		await expect(page.getByRole('button', { name: 'Rafraichir' })).toBeVisible({ timeout: 10_000 });
		await page.locator('button:has-text("demographie")').first().click();

		await expect(page.getByText('Donnees locales')).toBeVisible({ timeout: 5_000 });
		await expect(page.locator('button').filter({ hasText: '×' }).first()).toBeVisible();
	});

	test('detail modal can be closed', async ({ page }) => {
		await mockAuth(page);
		await mockDatasets(page);
		await page.goto('/admin/datasets');
		await expect(page.getByRole('button', { name: 'Rafraichir' })).toBeVisible({ timeout: 10_000 });

		await page.locator('button:has-text("demographie")').first().click();
		await expect(page.getByText('Donnees locales')).toBeVisible({ timeout: 5_000 });

		await page.locator('button').filter({ hasText: '×' }).first().click();
		await expect(page.getByText('Donnees locales')).not.toBeVisible({ timeout: 3_000 });
	});
});

test.describe('Admin — Audit filter dropdown (Phase 20-01)', () => {
	// Regression guard for the v2.2 milestone audit gap: late-phase admin actions
	// must stay discoverable from the /admin/audit action dropdown, not only via
	// free-text URL fallback. Both a legacy (grant_plan) and a late (trigger_etl)
	// action are asserted to prevent future drift.

	async function mockAuditEmpty(page: import('@playwright/test').Page) {
		await page.route('**/v1/admin/audit*', (route) =>
			route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ rows: [], total: 0, page: 1, page_size: 50 }),
			})
		);
	}

	test('action dropdown exposes legacy + late actions and updates URL on select', async ({ page }) => {
		await mockAuth(page, true);
		await mockAuditEmpty(page);

		await page.goto('/admin/audit');
		await expect(page.getByRole('heading', { name: "Journal d'audit" })).toBeVisible({ timeout: 10_000 });

		// The <select> exists and all 9 current actions are rendered as <option>.
		const actionSelect = page.locator('select').filter({ has: page.locator('option[value="grant_plan"]') });
		await expect(actionSelect).toBeVisible();

		// Assert both a Phase 15 action and a late Phase 19 action are selectable options.
		await expect(actionSelect.locator('option[value="grant_plan"]')).toHaveCount(1);
		await expect(actionSelect.locator('option[value="trigger_etl"]')).toHaveCount(1);

		// Defensive: every current admin action must be present.
		const expected = [
			'grant_plan',
			'toggle_is_admin',
			'reset_quota',
			'revoke_api_keys',
			'resend_verification',
			'revoke_api_key',
			'update_key_quota',
			'sync_subscription',
			'trigger_etl',
		];
		for (const value of expected) {
			await expect(actionSelect.locator(`option[value="${value}"]`)).toHaveCount(1);
		}

		// Selecting trigger_etl propagates into the URL (?action=trigger_etl).
		await actionSelect.selectOption('trigger_etl');
		await expect(page).toHaveURL(/[?&]action=trigger_etl\b/, { timeout: 5_000 });
	});
});

test.describe('Admin — Modal focus trap (Phase 20-03)', () => {
	// Regression guard for the shared focusTrap action wired to every admin
	// dialog surface (10 total). Targets GrantPlanModal because it's central
	// to the admin user-management flow and exercises every focus-trap
	// responsibility:
	//   1. initial focus lands inside the dialog
	//   2. Tab cycles among dialog-internal tabbables and does NOT escape
	//   3. Escape closes the dialog cleanly
	//
	// Mocks the users list + detail endpoints so the test is hermetic and
	// deterministic; the assertion target is modal keyboard behavior, not
	// user-management API shape.

	const MOCK_USERS_LIST = {
		rows: [
			{
				id: 42,
				email: 'targetuser@datacommune.fr',
				name: 'Target User',
				is_admin: false,
				email_verified: true,
				created_at: '2026-01-01T00:00:00Z',
				last_login: '2026-04-10T10:00:00Z',
				plan: 'free',
				plan_name: 'free',
				quota_used: 12,
				quota_monthly: 500,
				api_keys_count: 1,
				subscription_source: null,
				subscription_status: null,
				subscription_end_date: null,
			},
		],
		total: 1,
		page: 1,
		page_size: 50,
	};

	const MOCK_USER_DETAIL = {
		...MOCK_USERS_LIST.rows[0],
		subscription: null,
		api_keys: [],
		recent_activity: [],
	};

	async function mockAdminUsers(page: import('@playwright/test').Page) {
		await page.route('**/v1/admin/users?*', (route) =>
			route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_USERS_LIST) })
		);
		await page.route('**/v1/admin/users/42', (route) =>
			route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_USER_DETAIL) })
		);
		await page.route('**/v1/admin/users/42/heatmap', (route) =>
			route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ days: [], retention_days: 7 }),
			})
		);
	}

	test('GrantPlanModal traps Tab focus inside the dialog and closes on Escape', async ({
		page,
	}) => {
		await mockAuth(page, true);
		await mockAdminUsers(page);

		await page.goto('/admin/users?selected_user_id=42');
		// Wait for users table + detail drawer heading (user email) to be visible.
		await expect(page.getByRole('heading', { name: 'targetuser@datacommune.fr' })).toBeVisible({
			timeout: 10_000,
		});

		// Open GrantPlanModal from the user detail drawer.
		await page.getByRole('button', { name: 'Grant plan', exact: true }).click();

		// Dialog visible + focus landed inside it.
		const dialog = page.getByRole('dialog').filter({ hasText: 'Grant Plan' });
		await expect(dialog).toBeVisible({ timeout: 5_000 });

		// `flexRender` of the focus-trap action focuses the first tabbable on
		// rAF tick; wait until the active element is inside the dialog.
		await expect
			.poll(
				async () =>
					page.evaluate(() => {
						const dlg = document.querySelector('[role="dialog"]');
						const active = document.activeElement;
						return !!(dlg && active && dlg.contains(active));
					}),
				{ timeout: 3_000 }
			)
			.toBe(true);

		// Tab enough times to prove focus cycles inside the dialog. The Grant
		// Plan modal has ~6 tabbable elements (close button + plan select +
		// reason textarea + end-date input + cancel + confirm). Tab 12 times
		// (2 full cycles) and confirm focus is still inside the dialog.
		for (let i = 0; i < 12; i += 1) {
			await page.keyboard.press('Tab');
		}

		const stillInside = await page.evaluate(() => {
			const dlg = document.querySelector('[role="dialog"]');
			const active = document.activeElement;
			return !!(dlg && active && dlg.contains(active));
		});
		expect(stillInside).toBe(true);

		// Shift+Tab from the first tabbable wraps to the last (still inside dialog).
		for (let i = 0; i < 3; i += 1) {
			await page.keyboard.press('Shift+Tab');
		}
		const stillInsideBackward = await page.evaluate(() => {
			const dlg = document.querySelector('[role="dialog"]');
			const active = document.activeElement;
			return !!(dlg && active && dlg.contains(active));
		});
		expect(stillInsideBackward).toBe(true);

		// Escape closes the dialog (existing onkeydown handler, not focusTrap).
		await page.keyboard.press('Escape');
		await expect(dialog).not.toBeVisible({ timeout: 3_000 });
	});
});
