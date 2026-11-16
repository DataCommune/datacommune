import { test, expect } from './fixtures/quality';
import type { APIRequestContext } from '@playwright/test';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';

// Phase 46-05 (D-12/D-13, QA-01) — v3.5 critical Chromium gate, negative half: three
// `test.fail()` cases deliberately trigger each blocking condition of the quality fixture
// (console.error, pageerror, 5xx, unmocked API call) and assert the fixture DOES fail the test
// (Playwright's built-in "expected failure" semantics keep the overall run green while proving the
// gate is live, not just declared). The redaction guarantee (no leaked query string / API key in
// the violation report) is proven without a browser by the pure `redactUrl`/`redactMessage` unit
// tests of scripts/qa-gates.test.mjs.
//
// 04-08 (D-101, D-102): the positive half — the « Fiche Bien » journey on `/bien/{slug}` and the
// migrated DPE scenarios of phase 58.1 — left with the route, now a 308 redirect
// (bien-redirections.spec.ts). Each removed test is mapped to the spec that keeps its invariant in
// 04-08-SUMMARY.md (identity and INSEE: adresse-recherche; DVF minimisation of a recent isolated
// sale: adresse-ventes; out of coverage: adresse-recherche; DPE absent vs outage, no dwelling
// attribution: adresse-fiche). The positive journey under the quality fixture is matrix-qualite.

async function resetSourceContract(request: APIRequestContext) {
	const response = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
		data: { reset: true, expectations: [] }
	});
	expect(response.ok(), await response.text()).toBe(true);
}

async function expectSourceContract(request: APIRequestContext) {
	const response = await request.get(`${E2E_SOURCE_ORIGIN}/__requests`);
	expect(response.ok()).toBe(true);
	const audit = (await response.json()) as { violations: unknown[]; unmetExpectations: unknown[] };
	expect(audit.violations, `violations source: ${JSON.stringify(audit.violations)}`).toEqual([]);
	expect(audit.unmetExpectations, `appels source requis manquants: ${JSON.stringify(audit.unmetExpectations)}`).toEqual([]);
}

test.beforeEach(async ({ request }) => {
	await resetSourceContract(request);
});

test.afterEach(async ({ request }) => {
	await expectSourceContract(request);
});

test.describe('v3.5 critical — quality guard négatif (preuve fail-closed)', () => {
	test('appel /v1/resolve non mocké est avorté par le catch-all fail-closed (bloquant)', async ({
		page
	}) => {
		test.fail();
		await page.goto('/login');
		await page.evaluate(() => fetch('/v1/resolve').catch(() => null));
	});

	test('console.error déclenché par la page fait échouer le test (bloquant)', async ({ page }) => {
		test.fail();
		await page.goto('/login');
		await page.evaluate(() => console.error('synthetic sentinel error for gate proof'));
	});

	test('pageerror (exception non interceptée) fait échouer le test (bloquant)', async ({ page }) => {
		test.fail();
		await page.goto('/login');
		await page.evaluate(() => {
			setTimeout(() => {
				throw new Error('synthetic sentinel pageerror for gate proof');
			}, 0);
		});
		// Laisse le temps à la tâche planifiée d'émettre l'exception avant que la page se ferme.
		await expect(page.getByPlaceholder('votre@email.com')).toBeVisible();
	});

	test('réponse 5xx sur un appel mocké fait échouer le test (bloquant)', async ({ page }) => {
		test.fail();
		await page.route('**/v1/resolve**', (route) =>
			route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'down' }) })
		);
		await page.goto('/login');
		await page.evaluate(() => fetch('/v1/resolve').catch(() => null));
	});
});
