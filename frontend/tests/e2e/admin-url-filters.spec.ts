import { test, expect, type Page } from '@playwright/test';

// Phase 50 / Plan 50-04 — UI-06 (D-16..D-19): a shared /admin/users URL must restore the
// full screen state (3 filter selects, search, page, sort, open row) AND every state change
// must be written back into the URL.
//
// ⚠ These tests MUST live in this dedicated file. `admin.spec.ts` is classified `manual` in
// `e2e-manifest.json` (pre-existing red tests, out of this phase's scope): any test added
// there silently inherits `manual` and never runs in the CI critical gate (D-06).
//
// The admin API is fully mocked (`route.fulfill` pattern of the existing gate) — the real
// backend is never contacted.

const MOCK_ADMIN = {
	id: 1,
	email: 'admin@datacommune.fr',
	name: 'Admin',
	is_verified: true,
	is_admin: true,
	created_at: '2026-01-01T00:00:00Z'
};

const MOCK_ROWS = [
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
		subscription_end_date: null
	},
	{
		id: 7,
		email: 'seconduser@datacommune.fr',
		name: 'Second User',
		is_admin: false,
		email_verified: false,
		created_at: '2026-02-02T00:00:00Z',
		last_login: null,
		plan: 'pro',
		plan_name: 'pro',
		quota_used: 300,
		quota_monthly: 5000,
		api_keys_count: 2,
		subscription_source: null,
		subscription_status: null,
		subscription_end_date: null
	}
];

// `total` > page_size so that pagination (page 2) is reachable — the non-regression tests
// below click « Suivant ».
const MOCK_LIST = { rows: MOCK_ROWS, total: 120, page: 1, page_size: 50 };

function detailBody(id: number) {
	const row = MOCK_ROWS.find((r) => r.id === id) ?? MOCK_ROWS[0];
	return { ...row, subscription: null, api_keys: [], recent_activity: [] };
}

function json(body: unknown, status = 200) {
	return { status, contentType: 'application/json', body: JSON.stringify(body) };
}

interface AdminMocks {
	/** Every intercepted `/v1/admin/users` LIST request, in order. */
	listUrls: URL[];
	/** Every intercepted `/v1/admin/users/...` request (list, detail, heatmap). */
	allUrls: string[];
}

/**
 * Mock `/auth/me` (admin) plus the whole `/v1/admin/users` surface with a single route so
 * the dispatch is explicit and never depends on Playwright glob precedence.
 */
async function mockAdminApi(page: Page): Promise<AdminMocks> {
	const mocks: AdminMocks = { listUrls: [], allUrls: [] };

	await page.route('**/auth/me', (route) => route.fulfill(json(MOCK_ADMIN)));

	await page.route('**/v1/admin/users**', (route) => {
		const raw = route.request().url();
		mocks.allUrls.push(raw);
		const url = new URL(raw);

		if (/\/v1\/admin\/users$/.test(url.pathname)) {
			mocks.listUrls.push(url);
			return route.fulfill(json(MOCK_LIST));
		}
		if (/\/v1\/admin\/users\/\d+\/heatmap$/.test(url.pathname)) {
			return route.fulfill(json({ days: [], retention_days: 7 }));
		}
		const detail = url.pathname.match(/\/v1\/admin\/users\/(\d+)$/);
		if (detail) {
			return route.fulfill(json(detailBody(Number(detail[1]))));
		}
		// Anything else (e.g. `/v1/admin/users/NaN`) is a defect: answer 404 and let the
		// assertions on `detailRequests()` name it.
		return route.fulfill(json({ detail: 'not found' }, 404));
	});

	return mocks;
}

/** The field that filters the list: the header keeps its own search of the site beside it from 1200 px. */
function rechercheUtilisateurs(page: Page) {
	return page.getByRole('main').getByRole('searchbox');
}

/** Detail-endpoint calls only: `/v1/admin/users/<something>` without further segment. */
function detailRequests(mocks: AdminMocks): string[] {
	return mocks.allUrls.filter((u) => /\/v1\/admin\/users\/[^/?]+$/.test(new URL(u).pathname));
}

/** Wait until a LIST request carrying `key=value` has been emitted, then return its params. */
async function listParamsCarrying(
	mocks: AdminMocks,
	key: string,
	value: string
): Promise<URLSearchParams> {
	await expect
		.poll(() => mocks.listUrls.at(-1)?.searchParams.get(key) ?? null, { timeout: 10_000 })
		.toBe(value);
	return mocks.listUrls.at(-1)!.searchParams;
}

test.describe('Admin /admin/users — état partagé par URL (UI-06)', () => {
	test('une URL pré-filtrée restaure les 3 selects, la recherche, la page, le tri et le tiroir', async ({
		page
	}) => {
		const mocks = await mockAdminApi(page);

		await page.goto(
			'/admin/users?search=alice&plan=pro&is_admin=true&email_verified=false&page=2&sort=email_asc&selected_user_id=42'
		);

		// 1. The outgoing API request carries every parameter of the shared URL.
		const params = await listParamsCarrying(mocks, 'plan', 'pro');
		expect(params.get('search')).toBe('alice');
		expect(params.get('is_admin')).toBe('true');
		expect(params.get('email_verified')).toBe('false');
		expect(params.get('page')).toBe('2');
		expect(params.get('sort')).toBe('email_asc');

		// 2. The 3 selects and the search box display the shared values.
		await expect(page.getByLabel('Filtre plan')).toHaveValue('pro');
		await expect(page.getByLabel('Filtre administrateur')).toHaveValue('true');
		await expect(page.getByLabel('Filtre email vérifié')).toHaveValue('false');
		await expect(rechercheUtilisateurs(page)).toHaveValue('alice');

		// 3. The detail drawer is open on user 42.
		await expect(page.getByRole('heading', { name: 'targetuser@datacommune.fr' })).toBeVisible({
			timeout: 10_000
		});
	});

	test('un clic de ligne écrit selected_user_id dans l’URL (D-18)', async ({ page }) => {
		const mocks = await mockAdminApi(page);

		await page.goto('/admin/users');
		await listParamsCarrying(mocks, 'page', '1');

		const secondRow = page.getByText('seconduser@datacommune.fr');
		await expect(secondRow).toBeVisible({ timeout: 10_000 });
		await secondRow.click();

		await expect(page).toHaveURL(/[?&]selected_user_id=7(&|$)/, { timeout: 10_000 });
		await expect(page.getByRole('heading', { name: 'seconduser@datacommune.fr' })).toBeVisible();
	});

	test('la fermeture du tiroir retire selected_user_id de l’URL sans perdre les filtres (D-18)', async ({
		page
	}) => {
		const mocks = await mockAdminApi(page);

		await page.goto('/admin/users?plan=pro&selected_user_id=42');
		await listParamsCarrying(mocks, 'plan', 'pro');

		const heading = page.getByRole('heading', { name: 'targetuser@datacommune.fr' });
		await expect(heading).toBeVisible({ timeout: 10_000 });

		await page.getByRole('button', { name: 'Fermer', exact: true }).click();

		await expect(heading).not.toBeVisible();
		await expect(page).not.toHaveURL(/selected_user_id/, { timeout: 10_000 });
		// The other filters survive the drawer closing.
		await expect(page).toHaveURL(/[?&]plan=pro(&|$)/);
	});

	test('un selected_user_id non entier positif n’ouvre rien et n’appelle aucun détail (T-50-08)', async ({
		page
	}) => {
		for (const bad of ['abc', '-1', '0']) {
			const mocks = await mockAdminApi(page);

			await page.goto(`/admin/users?selected_user_id=${bad}`);
			await listParamsCarrying(mocks, 'page', '1');
			await expect(page.getByText('seconduser@datacommune.fr')).toBeVisible({ timeout: 10_000 });

			expect(detailRequests(mocks), `selected_user_id=${bad} must not trigger a detail call`).toEqual(
				[]
			);
			await expect(
				page.getByRole('heading', { name: 'targetuser@datacommune.fr' })
			).not.toBeVisible();

			await page.unrouteAll();
		}
	});

	test('non-régression : la recherche synchronise URL et requête sortante', async ({ page }) => {
		const mocks = await mockAdminApi(page);

		await page.goto('/admin/users');
		await listParamsCarrying(mocks, 'page', '1');

		await rechercheUtilisateurs(page).fill('alice');

		// Debounce (300 ms) is awaited semantically through the URL assertion, never a sleep.
		await expect(page).toHaveURL(/[?&]search=alice(&|$)/, { timeout: 10_000 });
		await listParamsCarrying(mocks, 'search', 'alice');
	});

	test('non-régression : la pagination synchronise URL et requête sortante', async ({ page }) => {
		const mocks = await mockAdminApi(page);

		await page.goto('/admin/users');
		await listParamsCarrying(mocks, 'page', '1');

		await page.getByRole('button', { name: 'Suivant' }).click();

		await expect(page).toHaveURL(/[?&]page=2(&|$)/, { timeout: 10_000 });
		await listParamsCarrying(mocks, 'page', '2');
	});

	test('non-régression : le tri synchronise URL et requête sortante', async ({ page }) => {
		const mocks = await mockAdminApi(page);

		await page.goto('/admin/users');
		await listParamsCarrying(mocks, 'page', '1');

		await page.getByRole('columnheader', { name: /^Email/ }).click();

		await expect(page).toHaveURL(/[?&]sort=email_desc(&|$)/, { timeout: 10_000 });
		await listParamsCarrying(mocks, 'sort', 'email_desc');
	});
});
