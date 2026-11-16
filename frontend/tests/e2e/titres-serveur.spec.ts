// The <title> of the public pages as the server renders it, the HTML a search engine reads before any
// script runs (bug seen in production on 2026-11-12: /commune/44109 served `<title>DataCommune</title>`
// while its og:title said « Nantes — DataCommune », so every commune showed the same title in results).
//
// The cause was in Svelte's server renderer: a page holding a `bind:` on a child component is rendered in
// a settle loop on a copied renderer, whose path in the tree is wrong, so its <title> lost to the default of
// the root layout. Only the built server showed it: the dev server these specs run on, and the browser after
// hydration, had the right title. The built server is proven by scripts/served-titles.test.mjs and the cause
// is guarded at compile time by src/routes/titre-serveur.test.ts. These specs lock, on the server HTML (never
// `document.title`), the searchable title of a served commune fiche and one own <title> on every public page.
import { test, expect } from '@playwright/test';
import { configureSsrSource } from './fixtures/bien-ssr-source';
import { COMMUNE_FLAT_85047 } from './fixtures/commune-indicators';
import { PAGES_PUBLIQUES } from './fixtures/pages-publiques';

const titres = (html: string) => [...html.matchAll(/<title>([^<]*)<\/title>/g)].map((m) => m[1]);

const NANTES = { ...COMMUNE_FLAT_85047, code_insee: '44109', nom_commune: 'Nantes', code_departement: '44', code_region: '52' };

test.describe('titres au rendu serveur', () => {
	test('/commune/44109 : le HTML serveur porte le titre de la commune, une seule fois', async ({ page }) => {
		const source = await configureSsrSource(page.request, [
			{
				method: 'GET',
				pathname: '/v1/commune',
				query: { code_insee: '44109' },
				minCalls: 1,
				maxCalls: 1,
				responses: [{ status: 200, body: { statut: 'ok', donnees: [NANTES], meta: { source: [] } } }]
			}
		]);
		const reponse = await page.request.get('/commune/44109');
		expect(reponse.status()).toBe(200);
		expect(titres(await reponse.text())).toEqual(['Nantes (44) : prix immobilier, loyers, DPE et services — DataCommune']);
		await source.assert();
	});

	// Every public page: one <title> of its own in the server HTML, never the bare default of the layout.
	for (const publique of PAGES_PUBLIQUES.filter((p) => p.id !== 'introuvable')) {
		test(`${publique.id} : un seul <title>, propre à la page`, async ({ page }) => {
			await configureSsrSource(page.request, publique.source());
			const reponse = await page.request.get(publique.chemin);
			const lus = titres(await reponse.text());
			expect(lus, publique.chemin).toHaveLength(1);
			expect(lus[0], publique.chemin).not.toBe('DataCommune');
		});
	}
});
