import { readFileSync } from 'node:fs';
import { test, expect, type Page } from '@playwright/test';
import { configureSsrSource } from './fixtures/bien-ssr-source';
import { E2E_APP_ORIGIN } from './fixtures/ports';

// Public navigation crawl (T2.5 / D-UX-01).
//
// Guarantee: from the homepage, every public route is reachable in <= 2 clicks via the
// header or footer, and none of them renders the SPA error page. Footer links are always
// visible (no hover needed), so each target below is a single click — well within budget.
//
// The 404 boundary (src/routes/+error.svelte) renders the heading "Page introuvable";
// asserting its absence proves we landed on a real route, not the error page. The first test
// proves the marker is not vacuous.
// No networkidle waits: local dev has no backend, so data-dependent pages may show their
// own empty/error state — that is still the route rendering, not the router 404.

const BASE = E2E_APP_ORIGIN;
const ERROR_MARKER = 'Page introuvable';

// « Données & méthode » is read from `/v1/datasets` (03-11, D-40): its crawl serves that answer
// once and checks the page rendered its coverage, not an outage or an empty catalogue.
const DATASETS_API = JSON.parse(
	readFileSync(new URL('../../src/lib/data/__fixtures__/datasets-api.json', import.meta.url), 'utf8')
);

async function serveDatasets(page: Page, href: string) {
	if (!href.startsWith('/datasets')) return null;
	return configureSsrSource(page.request, [
		{
			method: 'GET',
			pathname: '/v1/datasets',
			query: {},
			minCalls: 1,
			maxCalls: 1,
			responses: [{ status: 200, body: DATASETS_API }]
		}
	]);
}

async function expectDatasetsRendered(page: Page, href: string) {
	if (!href.startsWith('/datasets')) return;
	await expect(page.getByRole('heading', { level: 1 })).toHaveText('Données & méthode');
	// 04.8-06: the anchor of the footer link has its target, the legend.
	if (href.endsWith('#lire')) await expect(page.locator('#lire').getByRole('heading', { level: 2, name: 'Lire une fiche' })).toBeVisible();
	// Lot 8 of 4.9: the list of the departements is folded: its items are in the DOM, not in the a11y tree.
	await expect(page.getByRole('region', { name: 'Couverture', exact: true }).locator('details li')).not.toHaveCount(0);
	await expect(page.getByRole('alert')).toHaveCount(0);
}

test('une route inconnue rend la page 404 avec le lien « Revenir à la recherche »', async ({ page }) => {
	const response = await page.goto('/route-inconnue-03-03');
	expect(response?.status()).toBe(404);
	await expect(page.getByRole('heading', { level: 1, name: ERROR_MARKER })).toBeVisible();
	// 04.8-07 (UI-SPEC § 6.10): « Revenir à la recherche » is gone; the way back to the search is the first
	// row of « Pour repartir », « L’accueil » (/), with the large field on the page itself.
	await expect(
		page.getByRole('region', { name: 'Pour repartir' }).getByRole('link').first()
	).toHaveAccessibleName(/^L’accueil/);
	await expect(page.getByRole('region', { name: 'Pour repartir' }).getByRole('link').first()).toHaveAttribute('href', '/');
	await expect(page.getByRole('main').getByRole('combobox', { name: 'Chercher une adresse ou une commune' })).toBeVisible();
	// The error page wears the single site chrome too (D-50).
	await expect(page.getByRole('banner')).toBeVisible();
	await expect(page.getByRole('contentinfo')).toBeVisible();
});

// Every destination of the single site footer (03-03, D-50).
const FOOTER_ROUTES = [
	{ label: 'Données & méthode', href: '/datasets' },
	// 04.8-06: the legend of the scales and states, an anchor of « Données & méthode » that exists.
	{ label: 'Lire une fiche', href: '/datasets#lire' },
	{ label: 'Développeurs', href: '/developpeurs' },
	{ label: 'Mentions légales', href: '/mentions-legales' },
	{ label: 'Confidentialité', href: '/confidentialite' },
	{ label: 'CGU', href: '/cgu' }
];

test.describe('Navigation publique — crawl footer (<= 1 clic)', () => {
	for (const route of FOOTER_ROUTES) {
		test(`accueil → ${route.href} via le footer`, async ({ page }) => {
			const source = await serveDatasets(page, route.href);
			await page.goto('/');

			const link = page.locator(`footer a[href="${route.href}"]`);
			await expect(link).toBeVisible();
			await link.click();

			await expect(page).toHaveURL(BASE + route.href);
			// Not the SPA 404 boundary.
			await expect(page.getByText(ERROR_MARKER)).toHaveCount(0);
			await expectDatasetsRendered(page, route.href);
			await source?.assert();
		});
	}
});

test.describe('Navigation publique — header (<= 1 clic)', () => {
	// Single site header (03-03, D-50; v4 form since 04.8-02, UI-SPEC § 3.11): « Comparer »,
	// « Données & méthode » and « Développeurs », at every width. `/` is reached by the brand, the
	// « Rechercher » link being replaced by the header field (A-03), so its crawl starts from another
	// public page.
	for (const route of [
		{ label: 'DataCommune', href: '/', from: '/developpeurs' },
		{ label: 'Comparer', href: '/comparer', from: '/' },
		{ label: 'Données & méthode', href: '/datasets', from: '/' },
		{ label: 'Développeurs', href: '/developpeurs', from: '/' }
	]) {
		test(`${route.from} → ${route.href} via le header`, async ({ page }) => {
			const source = await serveDatasets(page, route.href);
			await page.goto(route.from);

			const link = page.getByRole('banner').getByRole('link', { name: route.label, exact: true });
			await expect(link).toHaveAttribute('href', route.href);
			await expect(link).toBeVisible();
			await link.click();

			await expect(page).toHaveURL(BASE + route.href);
			await expect(page.getByText(ERROR_MARKER)).toHaveCount(0);
			await expectDatasetsRendered(page, route.href);
			await source?.assert();
		});
	}
});
