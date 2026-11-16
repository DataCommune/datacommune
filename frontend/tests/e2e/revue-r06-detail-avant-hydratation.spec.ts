import { readFileSync } from 'node:fs';
import { test, expect, type APIRequestContext } from '@playwright/test';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { cheminRapport, REQUETE_RAPPORT } from './fixtures/rapport';

// Review 2026-11-10, finding R-06 (red on purpose, no fix here).
//
// Same family as « Repli ouvert avant l’hydratation » (2026-11-06), another occurrence. The categories
// of the address fiche got their guard (`Categorie.svelte` reads the server-rendered `open`), but the
// detail of a row is still `<details class="det" bind:open={ouvert}>` with `ouvert = false`
// (adresse/question/LigneCritere.svelte:43 and :130): `bind:open` writes `false` on the element when
// the component hydrates. Above 1080 px the categories are open cards, so the « i » of a row is the
// first thing a reader can tap while the scripts load; on a slow phone the detail they opened closes
// under their finger.

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

test('R-06 — le détail d’une ligne ouvert avant l’hydratation reste ouvert', async ({ page, request }) => {
	const a = structuredClone(BASE);
	await configurer(request, a);
	await page.setViewportSize({ width: 1440, height: 900 });
	let liberer!: () => void;
	const libere = new Promise<void>((resolve) => (liberer = resolve));
	// The scripts wait: the server render is all there is when the reader taps.
	await page.route(/\.(js|ts|svelte|mjs)(\?|$)|\/@fs\/|\/@vite\/|\/\.svelte-kit\//, async (route) => {
		await libere;
		await route.fallback();
	});
	const hydratee = page.waitForResponse((reponse) => new URL(reponse.url()).pathname === '/auth/me', { timeout: 30_000 });
	await page.goto(`/adresse/${a.banId}`, { waitUntil: 'commit' });
	const detail = page.locator('#critere-loyers details.det');
	await detail.locator(':scope > summary').click();
	await expect(detail).toHaveJSProperty('open', true);
	liberer();
	await hydratee;
	await expect(detail, 'closed by the hydration of bind:open').toHaveJSProperty('open', true);
});
