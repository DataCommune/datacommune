import { describe, it, expect, vi } from 'vitest';
import {
	getCommuneReferentiel,
	getDepartementFiches,
	getDepartementSynthese,
	listDepartements,
	searchCommunes,
	type CommuneSearchResult
} from './communes';

// The real `api()` client runs; only the fetch it is handed is a double, so the URL the
// browser would request is observed exactly as sent.

const RESULT: CommuneSearchResult = {
	code_insee: '79211',
	nom: 'Le Pin',
	code_postal: null,
	departement: '79',
	population: 1000,
	population_annee: null,
	population_source: 'geo_communes',
	hebergement: { etat: 'hors_couverture', motif: 'territoire_hors_perimetre', departements: [] }
};

function fetchReturning(donnees: unknown, meta?: unknown) {
	const body = meta === undefined ? { statut: 'ok', donnees } : { statut: 'ok', donnees, meta };
	return vi.fn(
		async (_input: RequestInfo | URL, _init?: RequestInit) =>
			new Response(JSON.stringify(body), {
				status: 200,
				headers: { 'Content-Type': 'application/json' }
			})
	);
}

function requested(fetchImpl: ReturnType<typeof fetchReturning>): URL {
	expect(fetchImpl).toHaveBeenCalledTimes(1);
	return new URL(String(fetchImpl.mock.calls[0][0]), 'http://origin.invalid');
}

describe('searchCommunes()', () => {
	it('sends q as typed to /v1/communes/search, with the departement filter and limit', async () => {
		const fetchImpl = fetchReturning([RESULT]);

		const res = await searchCommunes(
			'saint étienne-de',
			{ departement: '2A', limit: 8 },
			fetchImpl
		);

		const url = requested(fetchImpl);
		expect(url.pathname).toBe('/v1/communes/search');
		// No normalization in the browser: accents, hyphens and spaces reach the API untouched.
		expect(url.searchParams.get('q')).toBe('saint étienne-de');
		expect(url.searchParams.get('departement')).toBe('2A');
		expect(url.searchParams.get('limit')).toBe('8');
		expect(res.donnees).toEqual([RESULT]);
		// A search whose sources are all hosted is served without meta.
		expect(res.meta).toBeUndefined();
	});

	it('returns the envelope: the meta carries the state of a postal-code search (CR-01)', async () => {
		const meta = {
			hebergement: {
				ban: { etat: 'hors_couverture', motif: 'territoire_hors_perimetre', departements: [] }
			}
		};
		const fetchImpl = fetchReturning([], meta);

		const res = await searchCommunes('33000', {}, fetchImpl);

		expect(res).toEqual({ donnees: [], meta });
	});

	it('omits the departement and limit parameters when not given', async () => {
		const fetchImpl = fetchReturning([]);

		await searchCommunes('le pin', {}, fetchImpl);

		const url = requested(fetchImpl);
		expect([...url.searchParams.keys()]).toEqual(['q']);
	});

	it('hands the abort signal to fetch', async () => {
		const fetchImpl = fetchReturning([]);
		const controller = new AbortController();

		await searchCommunes('le pin', {}, fetchImpl, controller.signal);

		expect(fetchImpl.mock.calls[0][1]?.signal).toBe(controller.signal);
	});

	it('throws on a server failure instead of returning an empty list', async () => {
		const fetchImpl = vi.fn(async () => new Response('{}', { status: 503 }));

		await expect(searchCommunes('le pin', {}, fetchImpl)).rejects.toMatchObject({ status: 503 });
	});
});

describe('getCommuneReferentiel()', () => {
	it('asks /v1/communes for one code_insee and returns its row', async () => {
		const row = { code_insee: '79211', nom: 'Le Pin', departement: '79', population: 1000, code_postal: null };
		const fetchImpl = fetchReturning({
			results: [row],
			total_count: 1,
			page: 1,
			per_page: 50,
			has_next: false
		});

		const found = await getCommuneReferentiel('79211', fetchImpl);

		const url = requested(fetchImpl);
		expect(url.pathname).toBe('/v1/communes');
		expect([...url.searchParams.entries()]).toEqual([['code_insee', '79211']]);
		expect(found).toEqual(row);
	});

	it('returns null when no commune carries the code (unknown INSEE, not out of coverage)', async () => {
		const fetchImpl = fetchReturning({
			results: [],
			total_count: 0,
			page: 1,
			per_page: 50,
			has_next: false
		});

		expect(await getCommuneReferentiel('00000', fetchImpl)).toBeNull();
	});
});

// 04.7-09: the municipal arrondissements of Paris, Lyon and Marseille, opt-in on the API.
describe('arrondissements (04.7-09)', () => {
	const PARIS = { ...RESULT, code_insee: '75056', nom: 'Paris', departement: '75', type_zone: 'commune' as const, commune_parent: null };
	const ARR_12 = { ...RESULT, code_insee: '75112', nom: 'Paris 12e Arrondissement', departement: '75', type_zone: 'arrondissement' as const, commune_parent: '75056' };

	it('searchCommunes() asks for the arrondissements only when told to', async () => {
		const sans = fetchReturning([PARIS]);
		await searchCommunes('paris', {}, sans);
		expect(requested(sans).searchParams.has('inclure_arrondissements')).toBe(false);

		const avec = fetchReturning([PARIS, ARR_12]);
		await searchCommunes('paris', { inclureArrondissements: true }, avec);
		expect(requested(avec).searchParams.get('inclure_arrondissements')).toBe('true');
	});

	it('searchCommunes() keeps type_zone and commune_parent as served', async () => {
		const res = await searchCommunes('paris', { inclureArrondissements: true }, fetchReturning([PARIS, ARR_12]));
		expect(res.donnees.map((r) => [r.code_insee, r.type_zone, r.commune_parent])).toEqual([
			['75056', 'commune', null],
			['75112', 'arrondissement', '75056']
		]);
	});

	it('searchCommunes() never lists Paris twice, even if the answer carries it twice', async () => {
		const res = await searchCommunes('paris', { inclureArrondissements: true }, fetchReturning([PARIS, ARR_12, { ...PARIS }]));
		expect(res.donnees.map((r) => r.code_insee)).toEqual(['75056', '75112']);
	});

	it('getCommuneReferentiel() reads an arrondissement with inclure_arrondissements, and not otherwise', async () => {
		const avec = fetchReturning({ results: [ARR_12] });
		const ligne = await getCommuneReferentiel('75112', avec, { inclureArrondissements: true });
		const url = requested(avec);
		expect(url.pathname).toBe('/v1/communes');
		expect(url.searchParams.get('code_insee')).toBe('75112');
		expect(url.searchParams.get('inclure_arrondissements')).toBe('true');
		expect(ligne).toMatchObject({ code_insee: '75112', type_zone: 'arrondissement', commune_parent: '75056' });

		const sans = fetchReturning({ results: [] });
		await getCommuneReferentiel('44109', sans);
		expect(requested(sans).searchParams.has('inclure_arrondissements')).toBe(false);
	});
});

// 04.7-10: the three reads of the departement pages and of the sitemap.
describe('departements (04.7-10)', () => {
	it('listDepartements() reads /v1/departements and keeps the page state of every row', async () => {
		const lignes = [
			{
				code: '44',
				nom: 'Loire-Atlantique',
				region: 'Pays de la Loire',
				derniere_importation: '2026-10-30',
				prix: { etat: 'heberge', motif: null, departements: ['44'] },
				indexable: true
			},
			{
				code: '29',
				nom: 'Finistère',
				region: 'Bretagne',
				derniere_importation: null,
				prix: { etat: 'heberge', motif: null, departements: ['29'] },
				indexable: false
			}
		];
		const fetchImpl = fetchReturning(lignes);
		expect(await listDepartements(fetchImpl)).toEqual(lignes);
		expect(requested(fetchImpl).pathname).toBe('/v1/departements');
	});

	it('getDepartementSynthese() asks the code in the path and returns the envelope with its sources', async () => {
		const donnees = { code: '2A', nom: 'Corse-du-Sud' };
		const meta = { source: [{ nom: 'DVF', licence: 'Licence Ouverte 2.0', url: 'https://example.invalid/dvf' }] };
		const fetchImpl = fetchReturning(donnees, meta);
		const res = await getDepartementSynthese('2A', fetchImpl);
		expect(requested(fetchImpl).pathname).toBe('/v1/departements/2A');
		expect(res.donnees).toEqual(donnees);
		expect(res.meta).toEqual(meta);
	});

	it('never lets a code leave the path: it is encoded', async () => {
		const fetchImpl = fetchReturning({});
		await getDepartementSynthese('44/../x?y#z', fetchImpl);
		const demande = String(fetchImpl.mock.calls[0][0]);
		expect(demande).toContain('/v1/departements/44%2F..%2Fx%3Fy%23z');
	});

	it('getDepartementFiches() reads /fiches of the departement and returns its data', async () => {
		const donnees = {
			code: '75',
			derniere_importation: '2026-10-30',
			fiches: [
				{ code_insee: '75056', type_zone: 'commune' },
				{ code_insee: '75101', type_zone: 'arrondissement' }
			]
		};
		const fetchImpl = fetchReturning(donnees);
		expect(await getDepartementFiches('75', fetchImpl)).toEqual(donnees);
		expect(requested(fetchImpl).pathname).toBe('/v1/departements/75/fiches');
	});

	it('passes a refusal on as an ApiError carrying the status and the served Retry-After', async () => {
		const fetchImpl = vi.fn(
			async () => new Response('{}', { status: 503, headers: { 'Content-Type': 'application/json', 'Retry-After': '45' } })
		) as unknown as typeof fetch;
		await expect(getDepartementSynthese('44', fetchImpl)).rejects.toMatchObject({ status: 503, retryAfter: 45 });
		await expect(getDepartementFiches('44', fetchImpl)).rejects.toMatchObject({ status: 503, retryAfter: 45 });
	});
});
