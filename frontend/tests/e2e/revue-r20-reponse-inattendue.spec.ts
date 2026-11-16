import { test, expect } from '@playwright/test';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';

// Review 2026-11-10, finding R-20 (red on purpose, no fix here).
//
// A 200 whose body is not the contract (an empty object here: a proxy page, a version skew during a
// deploy) is read as an outage by `/communes`, `/comparer`, `/departements`, `/datasets`, `/dpe` and
// `/adresse`: typed alert, reload button. On `/commune/{code}` and `/departement/{code}` the load
// throws instead: HTTP 500, the root error page « Une erreur est survenue », no alert and nothing to
// reload with (`routes/+error.svelte` has no retry control).

for (const [url, pathname, query] of [
	['/commune/85047', '/v1/commune', { code_insee: '85047' }],
	['/departement/85', '/v1/departements/85', {}]
] as const) {
	test(`R-20 — ${url} : une réponse 200 hors contrat est une panne avec relance, pas une page 500`, async ({ page, request }) => {
		const configure = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
			data: { reset: true, expectations: [{ method: 'GET', pathname, query, minCalls: 0, maxCalls: 9, responses: Array(9).fill({ status: 200, body: {} }) }] }
		});
		expect(configure.ok()).toBe(true);
		const reponse = await page.goto(url);
		await page.waitForLoadState('networkidle');
		expect.soft(reponse?.status(), 'outage pages answer 503, never 500').toBe(503);
		await expect.soft(page.getByRole('alert')).toBeVisible();
		await expect(page.getByRole('button', { name: /Recharger/ })).toBeVisible();
	});
}
