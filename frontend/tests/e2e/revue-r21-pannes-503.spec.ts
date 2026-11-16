import { test, expect } from '@playwright/test';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';

// Review 2026-11-10, finding R-21: an outage page is answered 503 with `Retry-After` (04.7-10, decision 11),
// not 200 (`/datasets`, `/communes`) and not 503 without the header (`/dpe/{numero}`).
const NUMERO = '2485E0000003C';

for (const [url, pathname] of [
	['/datasets', '/v1/datasets'],
	['/communes', '/v1/communes/annuaire'],
	[`/dpe/${NUMERO}`, `/v1/dpe/${NUMERO}`]
] as const) {
	test(`R-21 — ${url} : une panne est un 503 avec Retry-After`, async ({ page, request }) => {
		const configure = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
			data: {
				reset: true,
				expectations: [{ method: 'GET', pathname, query: {}, minCalls: 0, maxCalls: 9, responses: Array(9).fill({ status: 503, body: {} }) }]
			}
		});
		expect(configure.ok()).toBe(true);
		const reponse = await page.goto(url);
		expect(reponse?.status(), 'outage pages answer 503').toBe(503);
		expect(reponse?.headers()['retry-after'], 'a robot is told when to come back').toMatch(/^\d+$/);
		expect(reponse?.headers()['x-dc-etat-page'], 'the internal marker never reaches a client').toBeUndefined();
	});
}
