import { test, expect, type APIRequestContext } from '@playwright/test';
import { DEPARTEMENTS_LISTE, FICHES_44, FICHES_75 } from '../../src/lib/data/__fixtures__/departement';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';

// 04.7-10 — the sitemap the site serves itself, at the request, from the API:
// `/sitemap.xml` (index), `/sitemaps/pages.xml`, `/sitemaps/departements.xml` and
// `/sitemaps/communes-[code].xml`.
//
// What this spec locks, on the synthetic source under exact expectations (fail-closed journal checked
// after each test; the source cache of the served sitemap is off for the e2e, SITEMAP_CACHE_TTL_MS=0,
// its hour being covered by the unit tests):
//   - the index lists the pages, the departements and one file per INDEXABLE departement, read from
//     `GET /v1/departements` alone; the files list what the API gives, each URL with the day its data
//     changed (AAAA-MM-JJ), none when the API gives none;
//   - 29 (hosted, not imported), 22 (not hosted), an unknown or lower-case code: 404, and the fiches
//     of the departement are never asked;
//   - a failing API is a 503 with the served `Retry-After` and `no-store`, never a partial or empty
//     document, and nothing of the failure is kept: the next request is served;
//   - the answers are XML, public for an hour, with no internal marker.
// The XML is not a screen: there is no viewport, no contrast and no layout to check here.

type Reply = { status: number; body: unknown; headers?: Record<string, string> };

const ok = (donnees: unknown): Reply => ({ status: 200, body: { statut: 'ok', donnees } });
const panne = (retryAfter?: string): Reply => ({
	status: 503,
	body: { detail: 'Service indisponible' },
	...(retryAfter ? { headers: { 'Retry-After': retryAfter } } : {})
});

function attendu(pathname: string, responses: Reply[]) {
	return { method: 'GET', pathname, query: {}, minCalls: responses.length, maxCalls: responses.length, responses };
}

async function configure(request: APIRequestContext, expectations: unknown[]) {
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, { data: { reset: true, expectations } });
	expect(res.ok()).toBe(true);
}

async function expectSourceContractMet(request: APIRequestContext) {
	const res = await request.get(`${E2E_SOURCE_ORIGIN}/__requests`);
	expect(res.ok()).toBe(true);
	const audit = (await res.json()) as { violations: unknown[]; unmetExpectations: unknown[] };
	expect(audit.violations, JSON.stringify(audit.violations)).toEqual([]);
	expect(audit.unmetExpectations, JSON.stringify(audit.unmetExpectations)).toEqual([]);
}

const locs = (xml: string) => [...xml.matchAll(/<loc>([^<]*)<\/loc>/g)].map((m) => m[1]);
const lastmods = (xml: string) => [...xml.matchAll(/<lastmod>([^<]*)<\/lastmod>/g)].map((m) => m[1]);
const SITE = 'https://datacommune.fr';

test.afterEach(async ({ request }) => {
	await expectSourceContractMet(request);
});

test.describe('sitemap.xml (index)', () => {
	test('liste les pages, les départements et un fichier par département indexable, en un seul appel', async ({ request }) => {
		await configure(request, [attendu('/v1/departements', [ok(DEPARTEMENTS_LISTE)])]);

		const res = await request.get('/sitemap.xml');

		expect(res.status()).toBe(200);
		expect(res.headers()['content-type']).toBe('application/xml; charset=utf-8');
		expect(res.headers()['cache-control']).toBe('public, max-age=3600');
		const xml = await res.text();
		expect(xml).toMatch(/^<\?xml version="1.0" encoding="UTF-8"\?>\n<sitemapindex xmlns="http:\/\/www.sitemaps.org\/schemas\/sitemap\/0.9">/);
		// 85 and 44 are indexable; 29 (not imported), 22, 2A and 2B are not.
		expect(locs(xml)).toEqual([
			`${SITE}/sitemaps/pages.xml`,
			`${SITE}/sitemaps/departements.xml`,
			`${SITE}/sitemaps/communes-85.xml`,
			`${SITE}/sitemaps/communes-44.xml`
		]);
		expect(lastmods(xml)).toEqual(['2026-10-30']);
	});

	test('une API en 503 donne un 503 avec Retry-After et no-store, sans corps de sitemap ; la reprise est servie', async ({ request }) => {
		await configure(request, [attendu('/v1/departements', [panne('45'), ok(DEPARTEMENTS_LISTE)])]);

		const refus = await request.get('/sitemap.xml');
		expect(refus.status()).toBe(503);
		expect(refus.headers()['retry-after']).toBe('45');
		expect(refus.headers()['cache-control']).toBe('no-store');
		expect(refus.headers()['content-type']).toMatch(/^text\/plain/);
		expect(await refus.text()).not.toMatch(/<urlset|<sitemapindex|<\?xml/);

		const reprise = await request.get('/sitemap.xml');
		expect(reprise.status()).toBe(200);
		expect(locs(await reprise.text())).toHaveLength(4);
	});

	test('une API en 429 sans délai servi : 503 et une minute', async ({ request }) => {
		await configure(request, [attendu('/v1/departements', [{ status: 429, body: { detail: 'Trop de requêtes' } }])]);

		const res = await request.get('/sitemap.xml');

		expect(res.status()).toBe(503);
		expect(res.headers()['retry-after']).toBe('60');
		expect(res.headers()['cache-control']).toBe('no-store');
	});
});

test.describe('pages.xml', () => {
	test('liste les pages statiques indexables, sans date et sans appel à l’API', async ({ request }) => {
		await configure(request, []);

		const res = await request.get('/sitemaps/pages.xml');

		expect(res.status()).toBe(200);
		expect(res.headers()['content-type']).toBe('application/xml; charset=utf-8');
		const xml = await res.text();
		expect(xml).toContain('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">');
		// Written by hand and in full, the legal pages included: the list is not computed from the code.
		expect(locs(xml)).toEqual([
			'https://datacommune.fr/',
			'https://datacommune.fr/communes',
			'https://datacommune.fr/datasets',
			'https://datacommune.fr/couverture',
			'https://datacommune.fr/departements',
			'https://datacommune.fr/developpeurs',
			'https://datacommune.fr/ce-que-nous-ne-montrons-pas',
			'https://datacommune.fr/cgu',
			'https://datacommune.fr/confidentialite',
			'https://datacommune.fr/mentions-legales'
		]);
		expect(lastmods(xml)).toEqual([]);
		// No query string, no address of a fiche, no departement-filtered directory.
		for (const loc of locs(xml)) expect(loc).not.toMatch(/\?|\/commune\/|\/departement\//);
	});
});

test.describe('departements.xml', () => {
	test('liste les départements indexables, chacun avec le jour de ses données', async ({ request }) => {
		await configure(request, [attendu('/v1/departements', [ok(DEPARTEMENTS_LISTE)])]);

		const res = await request.get('/sitemaps/departements.xml');

		expect(res.status()).toBe(200);
		const xml = await res.text();
		expect(locs(xml)).toEqual([`${SITE}/departement/85`, `${SITE}/departement/44`]);
		expect(lastmods(xml)).toEqual(['2026-10-30', '2026-10-30']);
		for (const jour of lastmods(xml)) expect(jour).toMatch(/^\d{4}-\d{2}-\d{2}$/);
	});

	test('une date que l’API ne donne pas n’est pas inventée', async ({ request }) => {
		const sansDate = DEPARTEMENTS_LISTE.map((d) => ({ ...d, derniere_importation: null }));
		await configure(request, [attendu('/v1/departements', [ok(sansDate)])]);

		const xml = await (await request.get('/sitemaps/departements.xml')).text();

		expect(locs(xml)).toHaveLength(2);
		expect(lastmods(xml)).toEqual([]);
	});

	test('une ligne sans « indexable » booléen refuse toute la liste (503), au lieu d’un index réduit', async ({ request }) => {
		const sansIndexable = DEPARTEMENTS_LISTE.map(({ indexable: _, ...ligne }) => ligne);
		await configure(request, [attendu('/v1/departements', [ok(sansIndexable)])]);

		const res = await request.get('/sitemaps/departements.xml');

		expect(res.status()).toBe(503);
		expect(res.headers()['cache-control']).toBe('no-store');
	});

	test('aucun département indexable : le fichier est un 404, comme l’index qui ne le liste pas', async ({ request }) => {
		const aucun = DEPARTEMENTS_LISTE.map((d) => ({ ...d, indexable: false }));
		await configure(request, [attendu('/v1/departements', [ok(aucun), ok(aucun)])]);

		const fichier = await request.get('/sitemaps/departements.xml');
		expect(fichier.status()).toBe(404);
		expect(fichier.headers()['cache-control']).toBe('no-store');

		const index = await request.get('/sitemap.xml');
		expect(index.status()).toBe(200);
		expect(locs(await index.text())).toEqual([`${SITE}/sitemaps/pages.xml`]);
	});

	test('une API en 503 donne un 503 avec Retry-After et no-store', async ({ request }) => {
		await configure(request, [attendu('/v1/departements', [panne('20')])]);

		const res = await request.get('/sitemaps/departements.xml');

		expect(res.status()).toBe(503);
		expect(res.headers()['retry-after']).toBe('20');
		expect(res.headers()['cache-control']).toBe('no-store');
	});
});

test.describe('communes-[code].xml', () => {
	test('liste les fiches d’un département indexable, avec le jour de leurs données', async ({ request }) => {
		await configure(request, [attendu('/v1/departements', [ok(DEPARTEMENTS_LISTE)]), attendu('/v1/departements/44/fiches', [ok(FICHES_44)])]);

		const res = await request.get('/sitemaps/communes-44.xml');

		expect(res.status()).toBe(200);
		expect(res.headers()['cache-control']).toBe('public, max-age=3600');
		const xml = await res.text();
		expect(locs(xml)).toEqual([`${SITE}/commune/44020`, `${SITE}/commune/44109`, `${SITE}/commune/44184`]);
		expect(lastmods(xml)).toEqual(['2026-10-29', '2026-10-29', '2026-10-29']);
	});

	test('Paris : la commune et ses arrondissements, chacun une fois', async ({ request }) => {
		const paris = { ...DEPARTEMENTS_LISTE[1], code: '75', nom: 'Paris', region: 'Île-de-France' };
		await configure(request, [
			attendu('/v1/departements', [ok([...DEPARTEMENTS_LISTE, paris])]),
			attendu('/v1/departements/75/fiches', [ok(FICHES_75)])
		]);

		const xml = await (await request.get('/sitemaps/communes-75.xml')).text();

		expect(locs(xml)).toEqual([`${SITE}/commune/75056`, `${SITE}/commune/75101`, `${SITE}/commune/75112`]);
	});

	test('29 (hébergé, pas importé) : 404, et ses fiches ne sont jamais demandées', async ({ request }) => {
		await configure(request, [attendu('/v1/departements', [ok(DEPARTEMENTS_LISTE)])]);

		const res = await request.get('/sitemaps/communes-29.xml');

		expect(res.status()).toBe(404);
		expect(res.headers()['cache-control']).toBe('no-store');
		expect(await res.text()).not.toMatch(/<urlset/);
	});

	test('22 (non hébergé) et un département inconnu : 404', async ({ request }) => {
		for (const code of ['22', '99']) {
			await configure(request, [attendu('/v1/departements', [ok(DEPARTEMENTS_LISTE)])]);
			const res = await request.get(`/sitemaps/communes-${code}.xml`);
			expect(res.status(), code).toBe(404);
			await expectSourceContractMet(request);
		}
	});

	test('un code mal formé ou en minuscule : 404 sans aucun appel à l’API', async ({ request }) => {
		await configure(request, []);

		for (const code of ['4A', 'abc', '044', '2a']) {
			const res = await request.get(`/sitemaps/communes-${code}.xml`);
			expect(res.status(), code).toBe(404);
		}
	});

	test('une fiche au code invalide refuse tout le fichier (503), sans rien garder : la réponse réparée est servie aussitôt', async ({ request }) => {
		const melange = { ...FICHES_44, fiches: [{ code_insee: '44109', type_zone: 'commune' }, { code_insee: '../bad', type_zone: 'commune' }] };
		// The cache of the e2e is off (0 ms): each request reads the list again, hence two reads of it.
		await configure(request, [
			attendu('/v1/departements', [ok(DEPARTEMENTS_LISTE), ok(DEPARTEMENTS_LISTE)]),
			attendu('/v1/departements/44/fiches', [ok(melange), ok(FICHES_44)])
		]);

		const refus = await request.get('/sitemaps/communes-44.xml');
		expect(refus.status()).toBe(503);
		expect(refus.headers()['retry-after']).toBe('60');
		expect(refus.headers()['cache-control']).toBe('no-store');
		expect(await refus.text()).not.toMatch(/<urlset|44109/);

		const reprise = await request.get('/sitemaps/communes-44.xml');
		expect(reprise.status()).toBe(200);
		expect(locs(await reprise.text())).toEqual([`${SITE}/commune/44020`, `${SITE}/commune/44109`, `${SITE}/commune/44184`]);
	});

	test('les fiches en 503 : 503 avec Retry-After, jamais un fichier vide ou partiel', async ({ request }) => {
		await configure(request, [attendu('/v1/departements', [ok(DEPARTEMENTS_LISTE)]), attendu('/v1/departements/44/fiches', [panne('90')])]);

		const res = await request.get('/sitemaps/communes-44.xml');

		expect(res.status()).toBe(503);
		expect(res.headers()['retry-after']).toBe('90');
		expect(res.headers()['cache-control']).toBe('no-store');
		expect(await res.text()).not.toMatch(/<urlset|<\?xml/);
	});
});
