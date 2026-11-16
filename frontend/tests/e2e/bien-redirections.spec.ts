import { test, expect, type APIRequestContext, type Page, type Response } from '@playwright/test';
import { avecVentesDeLaParcelle } from './fixtures/adresse-ventes';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { cheminRapport, enRapport, REQUETE_RAPPORT } from './fixtures/rapport';
import { expectNoUnexpectedOffLocalRequest, installLocalNetworkGuard } from './fixtures/local-network';

// Plan 04-08 Task 1 (D-101, D-102; T-04-08-01, T-04-08-02) — legacy `/bien` links.
//
// What this spec locks:
//   - `/bien/{slug}` whose label matches exactly one served address → 308 `/adresse/{ban_id}`, the
//     address page rendered (h1), never an intermediate page;
//   - a slug that matches zero or several addresses → 308 `/?q={label}`, the field of `/` prefilled
//     with the label once hydrated, no search launched;
//   - a legacy coordinate token → 308 `/`, without any lookup: no coordinate in the final URL nor
//     in any `Location` of the redirect chain;
//   - `/bien` → 308 `/`; `?dpe=` changes nothing; no « Plusieurs logements possibles » page;
//   - ported from `fiche-bien.spec.ts`: an empty slug sends no request. KI-P12: `robots.txt` no
//     longer disallows `/bien/` and every answer under `/bien` carries `x-robots-tag: noindex,
//     nofollow`, so a crawler can read it.
// Every API call reaches the synthetic source under exact expectations: an unexpected search (a
// coordinate lookup, a second search) fails the test.

type Reply = { status: number; body: unknown };
type Expectation = {
	method: 'GET';
	pathname: string;
	query: Record<string, string>;
	minCalls: number;
	maxCalls: number;
	responses: Reply[];
};

const COORDONNEES = /-?\d{1,3}\.\d+,-?\d{1,3}\.\d+/;
const SLUG = '12-rue-des-lilas-85000-la-roche-sur-yon';
const LIBELLE = '12 rue des lilas 85000 la roche sur yon';
const BAN_ID = '85191_0420_00012';

const LILAS = {
	type_resultat: 'adresse',
	ban_id: BAN_ID,
	numero: '12',
	rep: null,
	nom_voie: 'Rue des Lilas',
	code_postal: '85000',
	code_commune: '85191',
	nom_commune: 'La Roche-sur-Yon',
	departement: { code: '85', nom: 'Vendée' }
};

const IDENTITE_LILAS = {
	etat: 'etablie',
	ban_id: BAN_ID,
	libelle: '12 Rue des Lilas, 85000 La Roche-sur-Yon',
	numero: '12',
	rep: null,
	nom_voie: 'Rue des Lilas',
	code_postal: '85000',
	commune: { code_insee: '85191', nom: 'La Roche-sur-Yon' },
	departement: { code: '85', nom: 'Vendée' },
	parcelle: { etat: 'unique', id_parcelle: '85191000AB0012', nombre_candidats: 1, rattachement: 'parcelle_du_point', motif: null }
};

const SOURCE_BAN = { nom: 'Base Adresse Nationale', url: 'https://adresse.data.gouv.fr', licence: 'Licence Ouverte 2.0', producteur: 'IGN' };

const SANS_DOCUMENT: Reply = {
	status: 200,
	body: {
		statut: 'ok',
		donnees: {
			etat: 'value', motif: null, total: 0, page: 1, pages: 1, par_page: 10, generation: '0123456789abcdef',
			distribution: { A: 0, B: 0, C: 0, D: 0, E: 0, F: 0, G: 0, unknown: 0 }, date_import: '2026-09-30', documents: [],
			selection: { etat: 'indeterminee', motif: 'aucun_document', date: null, document: null, documents_ex_aequo: [] },
			signal_plusieurs_logements: null,
			source: { cle: 'dpe', nom: 'Diagnostics de performance énergétique (DPE)', producteur: 'ADEME', url: 'https://data.ademe.fr/datasets/dpe03existant' }
		}
	}
};

function okAdresses(resultats: unknown[]): Reply {
	return {
		status: 200,
		body: {
			statut: 'ok',
			donnees: { ordre_types: ['adresse', 'commune'], resultats, hebergement: { ban: { etat: 'heberge', motif: null, departements: ['85'] } } },
			meta: { source: SOURCE_BAN }
		}
	};
}

const okFiche: Reply = {
	status: 200,
	body: { statut: 'ok', donnees: { identite: IDENTITE_LILAS, couverture: { etat: 'heberge', motif: null } }, meta: { source: [SOURCE_BAN] } }
};

function attente(pathname: string, query: Record<string, string>, replies: Reply[], minCalls = replies.length): Expectation {
	return { method: 'GET', pathname, query, minCalls, maxCalls: replies.length, responses: replies };
}

/** The search of the legacy label, as the `/bien` loader sends it (`limite=2`). */
const recherche = (replies: Reply[], minCalls = replies.length) =>
	attente('/v1/adresses/recherche', { q: LIBELLE, limite: '2' }, replies, minCalls);

/** The address page of Lilas: identity and its companion reads, none beyond `max`. */
function pageLilas(max: number): Expectation[] {
	return [
		attente(cheminRapport(BAN_ID), REQUETE_RAPPORT, Array.from({ length: max }, () => enRapport(okFiche)), 1),
		attente(`/v1/adresse/${BAN_ID}/dpe`, { page: '1' }, Array.from({ length: max }, () => SANS_DOCUMENT), 0)
	];
}

async function configure(request: APIRequestContext, expectations: Expectation[]) {
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
		data: { reset: true, expectations: avecVentesDeLaParcelle(expectations) }
	});
	expect(res.ok()).toBe(true);
}

async function expectSourceContractMet(request: APIRequestContext) {
	const res = await request.get(`${E2E_SOURCE_ORIGIN}/__requests`);
	expect(res.ok()).toBe(true);
	const audit = (await res.json()) as { violations: unknown[]; unmetExpectations: unknown[] };
	expect(audit.violations, JSON.stringify(audit.violations)).toEqual([]);
	expect(audit.unmetExpectations, JSON.stringify(audit.unmetExpectations)).toEqual([]);
}

/** Records every response of the page: the redirect chain is read from their `Location`. */
function journal(page: Page): Response[] {
	const reponses: Response[] = [];
	page.on('response', (response) => reponses.push(response));
	return reponses;
}

/** The `Location` headers of the 3xx answers seen by the page. */
function locations(reponses: Response[]): string[] {
	return reponses
		.filter((response) => response.status() >= 300 && response.status() < 400)
		.map((response) => response.headers()['location'] ?? '');
}

async function sansRedirection(request: APIRequestContext, chemin: string) {
	const response = await request.get(chemin, { maxRedirects: 0 });
	return { status: response.status(), location: response.headers()['location'] ?? null, body: await response.text() };
}

// 04.8-07 (UI-SPEC § 3.13, § 6.6): the home carries the large field.
function champ(page: Page) {
	return page.getByRole('combobox', { name: 'Adresse, commune ou code postal' });
}

test.beforeEach(async ({ page }) => {
	await installLocalNetworkGuard(page);
});

test.afterEach(async ({ page, request }) => {
	await expectSourceContractMet(request);
	expectNoUnexpectedOffLocalRequest(page);
});

test.describe('Redirections /bien (D-101, D-102)', () => {
	test('slug résolu en une adresse : 308 vers /adresse/{id}, la page de l’adresse rendue', async ({ page, request }) => {
		await configure(request, [recherche([okAdresses([LILAS]), okAdresses([LILAS])]), ...pageLilas(2)]);

		const brute = await sansRedirection(request, `/bien/${SLUG}`);
		expect(brute.status).toBe(308);
		expect(brute.location).toBe(`/adresse/${BAN_ID}`);
		// A redirect renders nothing: no intermediate page.
		expect(brute.body).not.toContain('<h1');

		const reponses = journal(page);
		await page.goto(`/bien/${SLUG}`);
		await expect(page).toHaveURL(new RegExp(`/adresse/${BAN_ID}$`));
		// D-33 keeps the postal code and commune on the identity line below the street heading.
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('12 rue des Lilas');
		await expect(page.locator('.adresse-identity')).toContainText('85000 La Roche-sur-Yon');
		expect(locations(reponses)).toEqual([`/adresse/${BAN_ID}`]);
		await expect(page.getByText('Plusieurs logements possibles')).toHaveCount(0);
	});

	test('?dpe= est sans effet : même cible, rien de lui dans l’URL ni dans la recherche', async ({ page, request }) => {
		await configure(request, [recherche([okAdresses([LILAS])]), ...pageLilas(1)]);

		await page.goto(`/bien/${SLUG}?dpe=2285E0123456X`);
		await expect(page).toHaveURL(new RegExp(`/adresse/${BAN_ID}$`));
		expect(page.url()).not.toContain('2285E0123456X');
		// D-33 keeps the postal code and commune on the identity line below the street heading.
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('12 rue des Lilas');
		await expect(page.locator('.adresse-identity')).toContainText('85000 La Roche-sur-Yon');
	});

	test('slug ambigu : 308 vers /?q=, le champ prérempli, aucune recherche lancée', async ({ page, request }) => {
		const deux = okAdresses([LILAS, { ...LILAS, ban_id: '85191_0420_00099' }]);
		await configure(request, [recherche([deux, deux])]);

		const brute = await sansRedirection(request, `/bien/${SLUG}`);
		expect(brute.status).toBe(308);
		expect(brute.location).toBe('/?q=' + encodeURIComponent(LIBELLE));

		const hydrated = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
		await page.goto(`/bien/${SLUG}`);
		await hydrated;
		await expect(page).toHaveURL('/?q=' + encodeURIComponent(LIBELLE));
		await expect(champ(page)).toHaveValue(LIBELLE);
		// Prefilled, not searched: no listbox opens and the source saw one search (the loader's).
		await expect(page.getByRole('listbox')).toHaveCount(0);
		await expect(page.getByText('Plusieurs logements possibles')).toHaveCount(0);
	});

	test('sans JavaScript, /?q= garde le champ vide', async ({ browser, request }) => {
		await configure(request, []);
		const context = await browser.newContext({ javaScriptEnabled: false });
		try {
			const page = await context.newPage();
			await page.goto('/?q=' + encodeURIComponent(LIBELLE));
			await expect(champ(page)).toHaveValue('');
		} finally {
			await context.close();
		}
	});

	test('jeton de coordonnées : 308 vers /, aucune résolution, aucune coordonnée dans la cible ni l’URL finale', async ({ page, request }) => {
		// No expectation: a lookup of the coordinate would be a contract violation.
		await configure(request, []);

		const brute = await sansRedirection(request, '/bien/47.2,-1.55?origine=gps');
		expect(brute.status).toBe(308);
		expect(brute.location).toBe('/');

		const reponses = journal(page);
		await page.goto('/bien/47.2,-1.55');
		await expect(page).toHaveURL(/\/$/);
		expect(page.url()).not.toMatch(COORDONNEES);
		expect(locations(reponses)).toEqual(['/']);
		for (const location of locations(reponses)) expect(location).not.toMatch(COORDONNEES);
	});

	test('/bien : 308 vers /', async ({ page, request }) => {
		await configure(request, []);

		const brute = await sansRedirection(request, '/bien');
		expect(brute.status).toBe(308);
		expect(brute.location).toBe('/');

		await page.goto('/bien');
		await expect(page).toHaveURL(/\/$/);
		// 04.8-07 (UI-SPEC § 6.6): the v4 h1 of the home.
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('Une adresse, en clair');
	});

	test('slug sans lettre ni chiffre : 308 vers /, aucune requête', async ({ request }) => {
		await configure(request, []);

		const brute = await sansRedirection(request, '/bien/---');
		expect(brute.status).toBe(308);
		expect(brute.location).toBe('/');
	});

	test('robots.txt ne bloque pas /bien/ et la redirection porte noindex (KI-P12)', async ({ request }) => {
		await configure(request, []);
		const response = await request.get('/robots.txt');
		expect(response.ok()).toBe(true);
		expect(await response.text()).not.toMatch(/^Disallow:\s*\/bien/m);

		const redirection = await request.get('/bien/---', { maxRedirects: 0 });
		expect(redirection.status()).toBe(308);
		expect(redirection.headers()['x-robots-tag']).toBe('noindex, nofollow');
	});
});
