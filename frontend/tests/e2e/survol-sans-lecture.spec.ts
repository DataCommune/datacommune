import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { cheminRapport } from './fixtures/rapport';
import { expectNoUnexpectedOffLocalRequest, installLocalNetworkGuard } from './fixtures/local-network';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';
import {
	A,
	SAISIE_A,
	configurer,
	contratTenu,
	documentRecent,
	lecture,
	ouvrir,
	pageA,
	RECENT,
	type Attente
} from './fixtures/recette-adresses';

// Review 2026-11-10, finding R-10: the body said `data-sveltekit-preload-data="hover"`, so resting
// the pointer on a link to `/adresse/…` loaded its `__data.json`, that is a whole report: dozens of
// reads from the producers when the address is cold, and one fiche counted against the visitor's
// quota, with no click. The data are now preloaded on tap only (`app.html`).
//
// What this spec locks, on the synthetic source of this instance: on a page that lists address
// links (the typed results of `/communes?q=`, the address link of `/dpe/{numero}`), resting the
// pointer on a link sends no `/adresse/*/__data.json` request and the source receives no read of
// the report; the click still navigates and the fiche is displayed (the source then receives the
// reads the contract of `pageA` requires).
//
// No fixed delay: once the pointer is on the link, the page is awaited until the network is idle
// (SvelteKit starts a hover preload 20 ms after the pointer moved, so a preload would be seen as
// a request before the network settles).

const DONNEES_ADRESSE = /^\/adresse\/[^/]+\/__data\.json$/;

const SOURCE_BAN = { nom: 'Base Adresse Nationale', url: 'https://adresse.data.gouv.fr', licence: 'Licence Ouverte 2.0', producteur: 'IGN' };
const HEBERGE = { etat: 'heberge', motif: null, departements: ['85'] };

/** The address A as a typed result of the search. */
const RESULTAT_A = {
	type_resultat: 'adresse',
	ban_id: A,
	numero: '12',
	rep: null,
	nom_voie: 'Rue des Lilas',
	code_postal: '85000',
	code_commune: '85191',
	nom_commune: 'La Roche-sur-Yon',
	departement: { code: '85', nom: 'Vendée' }
};

/** `/communes?q=` reads the communes and the addresses of the query, once per page load at most. */
function rechercheTypee(): Attente[] {
	return [
		lecture('/v1/communes/search', { q: SAISIE_A, limit: '20', inclure_arrondissements: 'true' }, { statut: 'ok', donnees: [] }, 2, 1),
		lecture(
			'/v1/adresses/recherche',
			{ q: SAISIE_A, limite: '8' },
			{
				statut: 'ok',
				donnees: { ordre_types: ['adresse', 'commune'], resultats: [RESULTAT_A], hebergement: { ban: HEBERGE } },
				meta: { source: SOURCE_BAN }
			},
			2,
			1
		)
	];
}

/** Requests of the browser for the data of an address page (`/adresse/{id}/__data.json`). */
function surveillerDonneesAdresse(page: Page): string[] {
	const vues: string[] = [];
	page.on('request', (requete) => {
		const { pathname } = new URL(requete.url());
		if (DONNEES_ADRESSE.test(pathname)) vues.push(requete.url());
	});
	return vues;
}

/** Reads of the report of the address A that the synthetic source received. */
async function lecturesDuRapport(request: APIRequestContext): Promise<number> {
	const res = await request.get(`${E2E_SOURCE_ORIGIN}/__requests`);
	expect(res.ok()).toBe(true);
	const audit = (await res.json()) as { requests: { pathname: string }[] };
	return audit.requests.filter((ligne) => ligne.pathname === cheminRapport(A)).length;
}

test.beforeEach(async ({ page }) => {
	await installLocalNetworkGuard(page);
});

test.afterEach(async ({ page, request }) => {
	await contratTenu(request);
	expectNoUnexpectedOffLocalRequest(page);
});

test('R-10 — résultats de `/communes?q=` : survoler un lien d’adresse ne charge pas la fiche, cliquer l’ouvre', async ({ page, request }) => {
	await configurer(request, [...rechercheTypee(), ...pageA(2, 1)]);
	const donnees = surveillerDonneesAdresse(page);
	await ouvrir(page, `/communes?q=${encodeURIComponent(SAISIE_A)}`);

	const lien = page.locator(`main a[href="/adresse/${A}"]`);
	await expect(lien).toHaveCount(1);
	await lien.hover();
	await page.waitForLoadState('networkidle');

	expect(donnees, 'data of the fiche loaded by a hover').toEqual([]);
	expect(await lecturesDuRapport(request), 'reads of the report at the source, no click yet').toBe(0);

	await lien.click();
	await expect(page).toHaveURL(new RegExp(`/adresse/${A}$`));
	await expect(page.getByRole('heading', { level: 1 })).toHaveText('12 rue des Lilas');
	expect(await lecturesDuRapport(request), 'reads of the report after the click').toBeGreaterThan(0);
});

test('R-10 — page d’un DPE : survoler « Voir l’adresse » ne charge pas la fiche, cliquer l’ouvre', async ({ page, request }) => {
	await configurer(request, [documentRecent(2, 1), ...pageA(2, 1)]);
	const donnees = surveillerDonneesAdresse(page);
	await ouvrir(page, `/dpe/${RECENT}`);

	const lien = page.getByRole('link', { name: "Voir l'adresse" });
	await expect(lien).toHaveAttribute('href', `/adresse/${A}`);
	await lien.hover();
	await page.waitForLoadState('networkidle');

	expect(donnees, 'data of the fiche loaded by a hover').toEqual([]);
	expect(await lecturesDuRapport(request), 'reads of the report at the source, no click yet').toBe(0);

	await lien.click();
	await expect(page).toHaveURL(new RegExp(`/adresse/${A}$`));
	await expect(page.getByRole('heading', { level: 1 })).toHaveText('12 rue des Lilas');
	expect(await lecturesDuRapport(request), 'reads of the report after the click').toBeGreaterThan(0);
});
