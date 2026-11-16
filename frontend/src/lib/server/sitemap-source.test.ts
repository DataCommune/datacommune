// The served sitemap (04.7-10, decision 5): what each file lists, read from the API's departement
// list and fiches, never from a territory written here; the errors it answers; the calls it spends.
// The rules of the documents themselves are in `sitemap.test.ts`.
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DEPARTEMENTS_LISTE, FICHES_44, FICHES_75 } from '$lib/data/__fixtures__/departement';
import type { DepartementRow, FichesDepartement } from '$lib/api/communes';
import { GET as GET_COMMUNES } from '../../routes/sitemaps/communes-[code].xml/+server';
import { GET as GET_DEPARTEMENTS } from '../../routes/sitemaps/departements.xml/+server';
import { GET as GET_PAGES } from '../../routes/sitemaps/pages.xml/+server';
import { GET as GET_INDEX } from '../../routes/sitemap.xml/+server';
import {
	documentCommunes,
	documentDepartements,
	documentIndex,
	documentPages,
	entreesCommunes,
	entreesDepartements,
	entreesIndex,
	entreesPages,
	PAGES_STATIQUES,
	reponseSitemap,
	SitemapIntrouvable,
	SitemapSourceInvalide,
	ttlDepuisEnv,
	validerDepartements,
	viderCachesSitemap
} from './sitemap-source';
import { renderUrlset, SITEMAP_TTL_MS } from './sitemap';

const SITE = 'https://datacommune.fr';
const ROUTES = resolve(__dirname, '../../routes');

type Reponse = { status: number; body?: unknown; headers?: Record<string, string> };

/** A fetch that answers by path and counts the calls of each. */
function source(reponses: Record<string, Reponse | (() => Reponse)>) {
	const appels: string[] = [];
	const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
		const url = new URL(String(input), 'http://api.test');
		appels.push(url.pathname);
		const entree = reponses[url.pathname];
		if (!entree) throw new TypeError(`unexpected request ${url.pathname}`);
		const reponse = typeof entree === 'function' ? entree() : entree;
		return new Response(JSON.stringify(reponse.body ?? {}), {
			status: reponse.status,
			headers: { 'Content-Type': 'application/json', ...reponse.headers }
		});
	});
	return { fetch: fetchImpl as unknown as typeof fetch, appels };
}

const enveloppe = (donnees: unknown): Reponse => ({ status: 200, body: { statut: 'ok', donnees } });
const LISTE = enveloppe(DEPARTEMENTS_LISTE);
const urls = (xml: string) => [...xml.matchAll(/<loc>([^<]*)<\/loc>/g)].map((m) => m[1]);
const dates = (xml: string) => [...xml.matchAll(/<lastmod>([^<]*)<\/lastmod>/g)].map((m) => m[1]);

beforeEach(() => viderCachesSitemap());

describe('pages statiques', () => {
	// Written by hand, in full, the legal pages included (Codex 2 P3-4): an expected list computed from
	// `PAGES_STATIQUES` would follow the code it is meant to check, and a page dropped from it would pass.
	const PAGES_ATTENDUES = [
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
	];

	it('lists exactly the pages of the site that are indexable, absolute, in this order, with no date', () => {
		const entrees = entreesPages(SITE);
		expect(entrees.map((e) => e.loc)).toEqual(PAGES_ATTENDUES);
		for (const entree of entrees) expect(entree.lastmod ?? null).toBeNull();
	});

	it('only lists pages that exist as routes and are not noindex', () => {
		for (const chemin of PAGES_STATIQUES) {
			const dossier = chemin === '/' ? '(marketing)' : `(marketing)${chemin}`;
			const fichier = resolve(ROUTES, dossier, '+page.svelte');
			expect(existsSync(fichier), chemin).toBe(true);
			const source = readFileSync(fichier, 'utf8');
			// The directory is noindex only for a search query (`{#if data.q}`), never as a page.
			const sansRecherche = source.replace(/\{#if data\.q\}[\s\S]*?\{\/if\}/g, '');
			expect(/noindex/.test(sansRecherche), chemin).toBe(false);
		}
	});

	it('never lists a page that carries a query string or a placeholder', () => {
		for (const chemin of PAGES_STATIQUES) expect(chemin, chemin).toMatch(/^\/[a-z-]*$/);
	});
});

describe('departements.xml', () => {
	it('lists the indexable departements only, each with its own date, in the order served', () => {
		const entrees = entreesDepartements(SITE, DEPARTEMENTS_LISTE);
		expect(entrees).toEqual([
			{ loc: `${SITE}/departement/85`, lastmod: '2026-10-30' },
			{ loc: `${SITE}/departement/44`, lastmod: '2026-10-30' }
		]);
	});

	it('leaves the date out when the API gives none or an invalid one, and lists nothing else', () => {
		const lignes: DepartementRow[] = [
			{ ...DEPARTEMENTS_LISTE[1], derniere_importation: null },
			{ ...DEPARTEMENTS_LISTE[0], derniere_importation: '2026-02-30' }
		];
		const entrees = entreesDepartements(SITE, lignes);
		expect(entrees.map((e) => e.lastmod ?? null)).toEqual([null, null]);
	});

	it('lists a departement once even if the API served it twice', () => {
		expect(entreesDepartements(SITE, [DEPARTEMENTS_LISTE[1], DEPARTEMENTS_LISTE[1]])).toHaveLength(1);
	});

	it('has no file when no departement is indexable: 404 as the index lists none, never an empty urlset nor a 500 (Opus P3-2)', async () => {
		const { fetch } = source({ '/v1/departements': enveloppe(DEPARTEMENTS_LISTE.map((d) => ({ ...d, indexable: false }))) });
		await expect(documentDepartements({ site: SITE, fetch })).rejects.toBeInstanceOf(SitemapIntrouvable);
		const res = await reponseSitemap(documentDepartements({ site: SITE, fetch }));
		expect(res.status).toBe(404);
		expect(res.headers.get('cache-control')).toBe('no-store');
		expect(await res.text()).not.toMatch(/<urlset/);
		// The index and its departements file agree: no file listed, no file served.
		expect(entreesIndex(SITE, DEPARTEMENTS_LISTE.map((d) => ({ ...d, indexable: false }))).map((e) => e.loc)).toEqual([`${SITE}/sitemaps/pages.xml`]);
	});
});

describe('sitemap.xml (index)', () => {
	it('lists the pages, the departements and one file per indexable departement, in order', () => {
		const entrees = entreesIndex(SITE, DEPARTEMENTS_LISTE);
		expect(entrees.map((e) => e.loc)).toEqual([
			`${SITE}/sitemaps/pages.xml`,
			`${SITE}/sitemaps/departements.xml`,
			`${SITE}/sitemaps/communes-85.xml`,
			`${SITE}/sitemaps/communes-44.xml`
		]);
	});

	it('dates the departements file by its latest departement, and invents no other date', () => {
		const entrees = entreesIndex(SITE, DEPARTEMENTS_LISTE);
		expect(entrees.map((e) => e.lastmod ?? null)).toEqual([null, '2026-10-30', null, null]);
		const plusRecent = entreesIndex(SITE, [
			{ ...DEPARTEMENTS_LISTE[0], derniere_importation: '2026-11-01' },
			{ ...DEPARTEMENTS_LISTE[1], derniere_importation: '2026-10-02' }
		]);
		expect(plusRecent[1].lastmod).toBe('2026-11-01');
	});

	it('lists no file for a departement that is not indexable (29 hosted but not imported, 22, 2A, 2B)', () => {
		const loc = entreesIndex(SITE, DEPARTEMENTS_LISTE).map((e) => e.loc);
		for (const code of ['29', '22', '2A', '2B']) expect(loc).not.toContain(`${SITE}/sitemaps/communes-${code}.xml`);
	});

	it('lists only the pages when no departement is indexable, never a file that would be empty', () => {
		const rien = DEPARTEMENTS_LISTE.map((d) => ({ ...d, indexable: false }));
		expect(entreesIndex(SITE, rien).map((e) => e.loc)).toEqual([`${SITE}/sitemaps/pages.xml`]);
	});

	it('reads the list of departements once', async () => {
		const { fetch, appels } = source({ '/v1/departements': LISTE });
		const xml = await documentIndex({ site: SITE, fetch });
		expect(xml).toContain('<sitemapindex');
		expect(urls(xml)).toHaveLength(4);
		expect(appels).toEqual(['/v1/departements']);
	});
});

describe('communes-XX.xml', () => {
	it('lists the fiches of the departement with the date of its fiches, each once, in the order served', () => {
		const fiches: FichesDepartement = {
			code: '75',
			derniere_importation: '2026-10-29',
			fiches: [...FICHES_75.fiches, { code_insee: '75112', nom: 'Paris 12e Arrondissement', type_zone: 'arrondissement' }, { code_insee: '2A004', nom: 'Ajaccio', type_zone: 'commune' }]
		};
		expect(entreesCommunes(SITE, fiches)).toEqual([
			{ loc: `${SITE}/commune/75056`, lastmod: '2026-10-29' },
			{ loc: `${SITE}/commune/75101`, lastmod: '2026-10-29' },
			{ loc: `${SITE}/commune/75112`, lastmod: '2026-10-29' },
			{ loc: `${SITE}/commune/2A004`, lastmod: '2026-10-29' }
		]);
	});

	it('has no date when the API has none', () => {
		expect(entreesCommunes(SITE, { ...FICHES_44, derniere_importation: null }).map((e) => e.lastmod ?? null)).toEqual([null, null, null]);
	});

	it('builds the file of an indexable departement from two calls: the list, then its fiches', async () => {
		const { fetch, appels } = source({ '/v1/departements': LISTE, '/v1/departements/44/fiches': enveloppe(FICHES_44) });
		const xml = await documentCommunes({ site: SITE, fetch }, '44');
		expect(urls(xml)).toEqual([`${SITE}/commune/44020`, `${SITE}/commune/44109`, `${SITE}/commune/44184`]);
		expect(dates(xml)).toEqual(Array(3).fill('2026-10-29'));
		expect(appels).toEqual(['/v1/departements', '/v1/departements/44/fiches']);
	});

	it('answers not found for a departement that is not indexable, unknown, or written otherwise, without asking its fiches', async () => {
		for (const code of ['29', '22', '2A', '99', '2a', '4A', '', '../', '044']) {
			const { fetch, appels } = source({ '/v1/departements': LISTE });
			await expect(documentCommunes({ site: SITE, fetch }, code), code).rejects.toBeInstanceOf(SitemapIntrouvable);
			expect(appels.filter((a) => a.endsWith('/fiches')), code).toEqual([]);
		}
	});

	it('answers not found without any call for a code that is not shaped like a departement', async () => {
		const { fetch, appels } = source({});
		await expect(documentCommunes({ site: SITE, fetch }, '4A')).rejects.toBeInstanceOf(SitemapIntrouvable);
		expect(appels).toEqual([]);
	});

	it('answers not found when the API itself says it does not know the departement', async () => {
		const { fetch } = source({ '/v1/departements': LISTE, '/v1/departements/44/fiches': { status: 404 } });
		await expect(documentCommunes({ site: SITE, fetch }, '44')).rejects.toBeInstanceOf(SitemapIntrouvable);
	});

	it('spends no call on a second reading within the hour, the list being shared by every departement', async () => {
		const { fetch, appels } = source({
			'/v1/departements': LISTE,
			'/v1/departements/44/fiches': enveloppe(FICHES_44),
			'/v1/departements/85/fiches': enveloppe({ ...FICHES_44, code: '85' })
		});
		await documentCommunes({ site: SITE, fetch }, '44');
		await documentCommunes({ site: SITE, fetch }, '44');
		await documentCommunes({ site: SITE, fetch }, '85');
		expect(appels.filter((a) => a === '/v1/departements')).toHaveLength(1);
		expect(appels.filter((a) => a === '/v1/departements/44/fiches')).toHaveLength(1);
		expect(appels.filter((a) => a === '/v1/departements/85/fiches')).toHaveLength(1);
	});
});

// Revue Codex n° 2, constats 1 et 2, et Opus P3-1: a document is complete or it is an error. One
// invalid entry refuses the whole document (a 503 with Retry-After, no-store, nothing kept in memory);
// every read is validated by the loader of the cache, so that nothing invalid is stored and a repaired
// API is served at once, without anyone emptying the cache by hand.
describe('réponses invalides de l’API : tout le document est refusé', () => {
	const MELANGE: FichesDepartement = {
		code: '44',
		derniere_importation: '2026-10-29',
		fiches: [
			{ code_insee: '44109', nom: 'Nantes', type_zone: 'commune' },
			{ code_insee: '../bad', nom: 'Mauvais code', type_zone: 'commune' },
			{ code_insee: '44184', nom: 'Saint-Nazaire', type_zone: 'commune' }
		]
	};

	it('refuses the fiches of a departement as soon as one code is not an INSEE code, valid ones included', async () => {
		const { fetch } = source({ '/v1/departements': LISTE, '/v1/departements/44/fiches': enveloppe(MELANGE) });
		await expect(documentCommunes({ site: SITE, fetch }, '44')).rejects.toBeInstanceOf(SitemapSourceInvalide);
	});

	it('answers 503 with Retry-After and no-store, never a sitemap of the valid entries', async () => {
		const { fetch } = source({ '/v1/departements': LISTE, '/v1/departements/44/fiches': enveloppe(MELANGE) });
		const res = await reponseSitemap(documentCommunes({ site: SITE, fetch }, '44'));
		expect(res.status).toBe(503);
		expect(res.headers.get('retry-after')).toBe('60');
		expect(res.headers.get('cache-control')).toBe('no-store');
		expect(await res.text()).not.toMatch(/<urlset|<loc>|44109/);
	});

	it('refuses a fiche of another kind or of another departement than the one asked', async () => {
		const autreType = { ...FICHES_44, fiches: [{ code_insee: '44020', nom: 'Bouguenais', type_zone: 'quartier' }] };
		const autreDepartement = { ...FICHES_44, code: '85' };
		for (const [nom, corps] of [
			['type_zone', autreType],
			['code', autreDepartement],
			['not an object', { ...FICHES_44, fiches: ['44020'] }],
			['not a list', { ...FICHES_44, fiches: null }]
		] as const) {
			viderCachesSitemap();
			const { fetch } = source({ '/v1/departements': LISTE, '/v1/departements/44/fiches': enveloppe(corps) });
			await expect(documentCommunes({ site: SITE, fetch }, '44'), nom).rejects.toBeInstanceOf(SitemapSourceInvalide);
		}
	});

	it('keeps nothing of an invalid answer: the next reading asks the API again and is served, with no cache emptied by hand', async () => {
		let reponse: Reponse = enveloppe(MELANGE);
		const { fetch, appels } = source({ '/v1/departements': LISTE, '/v1/departements/44/fiches': () => reponse });
		expect((await reponseSitemap(documentCommunes({ site: SITE, fetch }, '44'))).status).toBe(503);
		reponse = enveloppe(FICHES_44);
		const reprise = await reponseSitemap(documentCommunes({ site: SITE, fetch }, '44'));
		expect(reprise.status).toBe(200);
		expect(urls(await reprise.text())).toEqual([`${SITE}/commune/44020`, `${SITE}/commune/44109`, `${SITE}/commune/44184`]);
		expect(appels.filter((a) => a === '/v1/departements/44/fiches')).toHaveLength(2);
	});

	it('keeps nothing of an empty list of fiches (500): the answer that follows, valid, is served', async () => {
		let reponse: Reponse = enveloppe({ ...FICHES_44, fiches: [] });
		const { fetch, appels } = source({ '/v1/departements': LISTE, '/v1/departements/44/fiches': () => reponse });
		const refus = await reponseSitemap(documentCommunes({ site: SITE, fetch }, '44'));
		expect(refus.status).toBe(500);
		reponse = enveloppe(FICHES_44);
		const reprise = await reponseSitemap(documentCommunes({ site: SITE, fetch }, '44'));
		expect(reprise.status).toBe(200);
		expect(urls(await reprise.text())).toHaveLength(3);
		expect(appels.filter((a) => a === '/v1/departements/44/fiches')).toHaveLength(2);
	});

	it('keeps nothing of a list the rendering refuses (past 50 000 URLs, 500): the answer that follows, valid, is served (Codex 3)', async () => {
		const trop = Array.from({ length: 50_001 }, (_, rang) => ({
			code_insee: String(rang).padStart(5, '0'),
			type_zone: 'commune'
		}));
		let reponse: Reponse = enveloppe({ ...FICHES_44, fiches: trop });
		const { fetch, appels } = source({ '/v1/departements': LISTE, '/v1/departements/44/fiches': () => reponse });
		const refus = await reponseSitemap(documentCommunes({ site: SITE, fetch }, '44'));
		expect(refus.status).toBe(500);
		reponse = enveloppe(FICHES_44);
		const reprise = await reponseSitemap(documentCommunes({ site: SITE, fetch }, '44'));
		expect(reprise.status).toBe(200);
		expect(urls(await reprise.text())).toHaveLength(3);
		expect(appels.filter((a) => a === '/v1/departements/44/fiches')).toHaveLength(2);
	});

	const LIGNE = DEPARTEMENTS_LISTE[1];

	it('refuses the list of departements as soon as one row has a code that is not a departement code', async () => {
		const liste = [...DEPARTEMENTS_LISTE, { ...LIGNE, code: '../bad', indexable: false }];
		for (const document of [documentIndex, documentDepartements]) {
			viderCachesSitemap();
			const { fetch } = source({ '/v1/departements': enveloppe(liste) });
			await expect(document({ site: SITE, fetch })).rejects.toBeInstanceOf(SitemapSourceInvalide);
		}
		viderCachesSitemap();
		const { fetch } = source({ '/v1/departements': enveloppe(liste), '/v1/departements/44/fiches': enveloppe(FICHES_44) });
		await expect(documentCommunes({ site: SITE, fetch }, '44')).rejects.toBeInstanceOf(SitemapSourceInvalide);
	});

	it('refuses the list of departements as soon as one row has no boolean « indexable »', async () => {
		for (const indexable of [undefined, null, 'true', 1, 'oui']) {
			const ligne = { ...LIGNE, indexable } as unknown as DepartementRow;
			viderCachesSitemap();
			const { fetch } = source({ '/v1/departements': enveloppe([ligne, DEPARTEMENTS_LISTE[0]]) });
			await expect(documentIndex({ site: SITE, fetch }), String(indexable)).rejects.toBeInstanceOf(SitemapSourceInvalide);
		}
	});

	it('answers 503 for an index whose list is invalid, rather than an index reduced to pages.xml', async () => {
		const sansIndexable = DEPARTEMENTS_LISTE.map(({ indexable: _, ...ligne }) => ligne);
		const { fetch } = source({ '/v1/departements': enveloppe(sansIndexable) });
		const res = await reponseSitemap(documentIndex({ site: SITE, fetch }));
		expect(res.status).toBe(503);
		expect(res.headers.get('cache-control')).toBe('no-store');
	});

	it('refuses a list that is not a list, and keeps nothing: the next valid answer is served', async () => {
		let reponse: Reponse = enveloppe({ results: [] });
		const { fetch } = source({ '/v1/departements': () => reponse });
		await expect(documentIndex({ site: SITE, fetch })).rejects.toBeInstanceOf(SitemapSourceInvalide);
		reponse = LISTE;
		expect(urls(await documentIndex({ site: SITE, fetch }))).toHaveLength(4);
	});

	it('does not refuse a list for a date it cannot write: the date is left out, as before', () => {
		expect(() => validerDepartements([{ ...LIGNE, derniere_importation: '2026-02-30' }])).not.toThrow();
	});
});

describe('pages.xml', () => {
	it('is a urlset of the static pages', () => {
		const xml = documentPages(SITE);
		expect(xml).toContain('<urlset');
		expect(urls(xml)).toEqual(entreesPages(SITE).map((e) => e.loc));
		expect(dates(xml)).toEqual([]);
	});
});

describe('reponseSitemap', () => {
	const xml = '<?xml version="1.0"?><urlset/>';

	it('serves the document as XML, public for an hour', async () => {
		const res = await reponseSitemap(Promise.resolve(xml));
		expect(res.status).toBe(200);
		expect(res.headers.get('content-type')).toBe('application/xml; charset=utf-8');
		expect(res.headers.get('cache-control')).toBe('public, max-age=3600');
		expect(await res.text()).toBe(xml);
	});

	it('answers 503 with the served Retry-After and no-store when the API is unavailable or limits, never a body of XML', async () => {
		for (const [status, retry, attendu] of [
			[503, undefined, '60'],
			[429, '45', '45'],
			[500, undefined, '60']
		] as const) {
			const { fetch } = source({ '/v1/departements': { status, headers: retry ? { 'Retry-After': retry } : {} } });
			const res = await reponseSitemap(documentIndex({ site: SITE, fetch }));
			expect(res.status, String(status)).toBe(503);
			expect(res.headers.get('retry-after'), String(status)).toBe(attendu);
			expect(res.headers.get('cache-control'), String(status)).toBe('no-store');
			expect(res.headers.get('content-type'), String(status)).toMatch(/^text\/plain/);
			expect(await res.text(), String(status)).not.toMatch(/<urlset|<sitemapindex|<\?xml/);
		}
	});

	it('answers 503 for a network failure too, with the default delay', async () => {
		const reseau = vi.fn(async () => {
			throw new TypeError('fetch failed');
		}) as unknown as typeof fetch;
		const res = await reponseSitemap(documentIndex({ site: SITE, fetch: reseau }));
		expect(res.status).toBe(503);
		expect(res.headers.get('retry-after')).toBe('60');
		expect(res.headers.get('cache-control')).toBe('no-store');
	});

	it('caps an extreme Retry-After', async () => {
		const { fetch } = source({ '/v1/departements': { status: 429, headers: { 'Retry-After': '99999999' } } });
		const res = await reponseSitemap(documentIndex({ site: SITE, fetch }));
		expect(res.headers.get('retry-after')).toBe('86400');
	});

	it('does not keep the failure: the next reading asks the API again and serves the file', async () => {
		let panne = true;
		const { fetch, appels } = source({ '/v1/departements': () => (panne ? { status: 503 } : LISTE) });
		const premiere = await reponseSitemap(documentIndex({ site: SITE, fetch }));
		expect(premiere.status).toBe(503);
		panne = false;
		const seconde = await reponseSitemap(documentIndex({ site: SITE, fetch }));
		expect(seconde.status).toBe(200);
		expect(appels).toHaveLength(2);
	});

	it('answers 404 for an unknown departement and 500 for a document the rules refuse, both uncached', async () => {
		const introuvable = await reponseSitemap(Promise.reject(new SitemapIntrouvable()));
		expect(introuvable.status).toBe(404);
		expect(introuvable.headers.get('cache-control')).toBe('no-store');
		// A document the rules refuse: here an empty one, as the rules of the documents say it.
		const refuse = await reponseSitemap(Promise.resolve().then(() => renderUrlset([])));
		expect(refuse.status).toBe(500);
		expect(refuse.headers.get('cache-control')).toBe('no-store');
		expect(await refuse.text()).not.toMatch(/<urlset/);
	});
});

describe('les quatre routes', () => {
	/** Calls a route handler with the two parts of the event it reads: the fetch and the params. */
	const appeler = (route: unknown, fetch: typeof globalThis.fetch, params: Record<string, string> = {}) =>
		(route as (event: unknown) => Promise<Response>)({ fetch, params });

	it('serves the index, pages.xml, departements.xml and communes-[code].xml from the API', async () => {
		const { fetch } = source({ '/v1/departements': LISTE, '/v1/departements/44/fiches': enveloppe(FICHES_44) });
		const pages = await appeler(GET_PAGES, fetch);
		expect(pages.status).toBe(200);
		expect(urls(await pages.text())).toContain(`${SITE}/departements`);

		const index = await appeler(GET_INDEX, fetch);
		expect(index.status).toBe(200);
		expect(urls(await index.text())).toContain(`${SITE}/sitemaps/communes-44.xml`);

		const departements = await appeler(GET_DEPARTEMENTS, fetch);
		expect(urls(await departements.text())).toEqual([`${SITE}/departement/85`, `${SITE}/departement/44`]);

		const communes = await appeler(GET_COMMUNES, fetch, { code: '44' });
		expect(communes.status).toBe(200);
		expect(urls(await communes.text())).toHaveLength(3);
	});

	it('answers 404 to communes-29.xml (hosted, not imported), and 503 with Retry-After when the API is down', async () => {
		const { fetch } = source({ '/v1/departements': LISTE });
		const introuvable = await appeler(GET_COMMUNES, fetch, { code: '29' });
		expect(introuvable.status).toBe(404);

		viderCachesSitemap();
		const { fetch: panne } = source({ '/v1/departements': { status: 503 } });
		for (const route of [GET_INDEX, GET_DEPARTEMENTS]) {
			const res = await appeler(route, panne);
			expect(res.status).toBe(503);
			expect(res.headers.get('retry-after')).toBe('60');
			expect(res.headers.get('cache-control')).toBe('no-store');
		}
		const res = await appeler(GET_COMMUNES, panne, { code: '44' });
		expect(res.status).toBe(503);
	});
});

describe('ttlDepuisEnv', () => {
	it('takes a whole number of milliseconds from the environment, zero included, else one hour', () => {
		expect(ttlDepuisEnv(undefined)).toBe(SITEMAP_TTL_MS);
		expect(ttlDepuisEnv('')).toBe(SITEMAP_TTL_MS);
		expect(ttlDepuisEnv('abc')).toBe(SITEMAP_TTL_MS);
		expect(ttlDepuisEnv('-5')).toBe(SITEMAP_TTL_MS);
		expect(ttlDepuisEnv('1.5')).toBe(SITEMAP_TTL_MS);
		expect(ttlDepuisEnv('0')).toBe(0);
		expect(ttlDepuisEnv('1000')).toBe(1000);
	});
});
