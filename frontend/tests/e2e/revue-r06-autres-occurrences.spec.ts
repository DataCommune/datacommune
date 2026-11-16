import { readFileSync } from 'node:fs';
import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { cheminRapport, REQUETE_RAPPORT } from './fixtures/rapport';

// Review 2026-11-10, finding R-06, the two other occurrences of the address fiche that the review
// deduced without replaying them: `<details bind:open={x}>` with `x = false` writes `false` on the
// server-rendered element when the component hydrates (Svelte 5 `bind_property` assigns
// `element.open = get()`), so a fold the reader opened while the scripts loaded closes under their
// finger. One test per occurrence: the sources fold of the page, the compact summary (under 1081 px).

interface Entree {
	banId: string;
	rapport: unknown;
	documents: unknown;
	ventes: unknown;
	ventesAdresse: unknown;
}
const BASE = JSON.parse(readFileSync(new URL('./fixtures/rapport-43-themes.json', import.meta.url), 'utf8')) as Entree;
const LECTURES = 12;
const attente = (pathname: string, query: Record<string, string>, body: unknown) => ({
	method: 'GET', pathname, query, minCalls: 0, maxCalls: LECTURES,
	responses: Array.from({ length: LECTURES }, () => ({ status: 200, body }))
});

async function configurer(request: APIRequestContext, a: Entree) {
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
		data: {
			reset: true,
			expectations: [
				attente(cheminRapport(a.banId), REQUETE_RAPPORT, a.rapport),
				attente(`/v1/adresse/${a.banId}/dpe`, { page: '1' }, a.documents),
				attente(`/v1/adresse/${a.banId}/ventes`, { page: '1' }, a.ventes),
				attente(`/v1/adresse/${a.banId}/ventes-declarees`, { page: '1' }, a.ventesAdresse)
			]
		}
	});
	expect(res.ok()).toBe(true);
}

/** Opens the fiche with its scripts held: the server render is all there is when the reader taps. */
async function ouvrirSansScripts(page: Page, banId: string) {
	let liberer!: () => void;
	const libere = new Promise<void>((resolve) => (liberer = resolve));
	await page.route(/\.(js|ts|svelte|mjs)(\?|$)|\/@fs\/|\/@vite\/|\/\.svelte-kit\//, async (route) => {
		await libere;
		await route.fallback();
	});
	const hydratee = page.waitForResponse((reponse) => new URL(reponse.url()).pathname === '/auth/me', { timeout: 30_000 });
	await page.goto(`/adresse/${banId}`, { waitUntil: 'commit' });
	return async () => {
		liberer();
		await hydratee;
	};
}

test('R-06 — le volet des sources ouvert avant l’hydratation reste ouvert', async ({ page, request }) => {
	const a = structuredClone(BASE);
	await configurer(request, a);
	await page.setViewportSize({ width: 1440, height: 900 });
	const hydrater = await ouvrirSansScripts(page, a.banId);
	const repli = page.locator('#sources details.sources-repli');
	await repli.locator(':scope > summary').click();
	await expect(repli).toHaveJSProperty('open', true);
	await hydrater();
	await expect(repli, 'closed by the hydration of bind:open').toHaveJSProperty('open', true);
});

test('R-06 — le sommaire compact ouvert avant l’hydratation reste ouvert', async ({ page, request }) => {
	const a = structuredClone(BASE);
	await configurer(request, a);
	await page.setViewportSize({ width: 375, height: 812 });
	const hydrater = await ouvrirSansScripts(page, a.banId);
	const menu = page.locator('nav.sommaire-compact > details');
	await menu.locator(':scope > summary').click();
	await expect(menu).toHaveJSProperty('open', true);
	await hydrater();
	await expect(menu, 'closed by the hydration of bind:open').toHaveJSProperty('open', true);
});
