// Reading of a commune fiche, arrondissement part (04.7-09): an arrondissement is served by
// `/v1/commune` at its exact code, its identity falls back on the referential asked WITH
// `inclure_arrondissements`, and a commune keeps its former requests.
import { describe, expect, it, vi } from 'vitest';
import { COMMUNE_85047_CHALLANS } from './__fixtures__/commune-85';
import { lireFicheCommune, parentDeLaZone } from './fiche-commune';

type Reponse = { status: number; body: unknown };

function source(reponses: Record<string, Reponse>) {
	const appels: URL[] = [];
	const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
		const url = new URL(String(input), 'http://api.test');
		appels.push(url);
		const reponse = reponses[url.pathname];
		if (!reponse) throw new TypeError(`unexpected request ${url.pathname}`);
		return new Response(JSON.stringify(reponse.body), { status: reponse.status, headers: { 'Content-Type': 'application/json' } });
	});
	return { fetchImpl: fetchImpl as unknown as typeof fetch, appels };
}

const ARRONDISSEMENT = {
	...COMMUNE_85047_CHALLANS,
	code_insee: '75112',
	nom_commune: 'Paris 12e Arrondissement',
	code_departement: '75',
	type_zone: 'arrondissement' as const,
	commune_parent: '75056'
};

const vide = { statut: 'ok', donnees: [], meta: { source: [] } };
const referentiel = (results: unknown[]) => ({ statut: 'ok', donnees: { results } });

describe('lireFicheCommune — arrondissement (04.7-09)', () => {
	it('serves the synthesis of the arrondissement with its commune as parent', async () => {
		const { fetchImpl, appels } = source({ '/v1/commune': { status: 200, body: { statut: 'ok', donnees: [ARRONDISSEMENT], meta: { source: [] } } } });

		const fiche = await lireFicheCommune('75112', fetchImpl);

		expect(fiche.etat).toBe('fiche');
		expect(fiche.parent).toEqual({ code: '75056', nom: 'Paris' });
		expect(appels.map((u) => u.pathname)).toEqual(['/v1/commune']);
		expect(appels[0].searchParams.get('code_insee')).toBe('75112');
	});

	it('a commune has no parent, even in the divided communes (75056 is Paris, not an arrondissement)', async () => {
		const paris = { ...COMMUNE_85047_CHALLANS, code_insee: '75056', nom_commune: 'Paris', type_zone: 'commune' as const, commune_parent: null };
		const { fetchImpl } = source({ '/v1/commune': { status: 200, body: { statut: 'ok', donnees: [paris], meta: { source: [] } } } });
		const fiche = await lireFicheCommune('75056', fetchImpl);
		expect(fiche.etat).toBe('fiche');
		expect(fiche.parent).toBeNull();
	});

	it('without a synthesis, the identity comes from the referential asked for arrondissements (the old fallback 404ed)', async () => {
		const { fetchImpl, appels } = source({
			'/v1/commune': { status: 200, body: vide },
			'/v1/communes': {
				status: 200,
				body: referentiel([{ code_insee: '75112', nom: 'Paris 12e Arrondissement', departement: '75', type_zone: 'arrondissement', commune_parent: '75056' }])
			}
		});

		const fiche = await lireFicheCommune('75112', fetchImpl);

		expect(fiche.etat).toBe('non-renseignee');
		if (fiche.etat !== 'non-renseignee') return;
		expect(fiche.identite.nom).toBe('Paris 12e Arrondissement');
		expect(fiche.parent).toEqual({ code: '75056', nom: 'Paris' });
		const ref = appels.find((u) => u.pathname === '/v1/communes')!;
		expect(ref.searchParams.get('code_insee')).toBe('75112');
		expect(ref.searchParams.get('inclure_arrondissements')).toBe('true');
	});

	it('an arrondissement the referential does not list (outside the hosted perimeter) is still a 404', async () => {
		const { fetchImpl } = source({ '/v1/commune': { status: 200, body: vide }, '/v1/communes': { status: 200, body: referentiel([]) } });
		await expect(lireFicheCommune('75112', fetchImpl)).rejects.toMatchObject({ status: 404 });
	});

	it('the referential request of a plain commune is unchanged (no inclure_arrondissements)', async () => {
		const { fetchImpl, appels } = source({
			'/v1/commune': { status: 200, body: vide },
			'/v1/communes': { status: 200, body: referentiel([{ code_insee: '44109', nom: 'Nantes', departement: '44' }]) }
		});
		const fiche = await lireFicheCommune('44109', fetchImpl);
		expect(fiche.etat).toBe('non-renseignee');
		expect(fiche.parent).toBeNull();
		expect(appels.find((u) => u.pathname === '/v1/communes')!.searchParams.has('inclure_arrondissements')).toBe(false);
	});

	it('an outage keeps the parent: the page still links Paris', async () => {
		const { fetchImpl } = source({ '/v1/commune': { status: 503, body: {} }, '/v1/communes': { status: 503, body: {} } });
		const fiche = await lireFicheCommune('75112', fetchImpl);
		expect(fiche.etat).toBe('panne');
		expect(fiche.parent).toEqual({ code: '75056', nom: 'Paris' });
	});
});

// 04.7-10, decision 11: an outage is posted as an internal header (read by hooks.server.ts, which
// answers 503 with Retry-After); the four other states post nothing, and the JSON export, which has
// no setHeaders, reads the same function unchanged.
describe('lireFicheCommune — outage marker (04.7-10)', () => {
	const marque = { 'x-dc-etat-page': 'panne' };

	it('posts the marker and the served Retry-After when the synthesis is rate limited', async () => {
		const limite = vi.fn(async () => new Response('{}', { status: 429, headers: { 'Content-Type': 'application/json', 'Retry-After': '45' } }));
		const setHeaders = vi.fn();
		const fiche = await lireFicheCommune('44109', limite as unknown as typeof fetch, { setHeaders });
		expect(fiche.etat).toBe('panne');
		expect(setHeaders).toHaveBeenCalledTimes(1);
		expect(setHeaders).toHaveBeenCalledWith({ ...marque, 'x-dc-retry-after': '45' });
	});

	it('posts the marker alone on a plain 503 (no Retry-After served)', async () => {
		const { fetchImpl } = source({ '/v1/commune': { status: 503, body: {} }, '/v1/communes': { status: 503, body: {} } });
		const setHeaders = vi.fn();
		const fiche = await lireFicheCommune('44109', fetchImpl, { setHeaders });
		expect(fiche.etat).toBe('panne');
		expect(setHeaders).toHaveBeenCalledTimes(1);
		expect(setHeaders).toHaveBeenCalledWith(marque);
	});

	it('posts it also when the referential is the one that fails after an empty synthesis', async () => {
		const { fetchImpl } = source({ '/v1/commune': { status: 200, body: vide }, '/v1/communes': { status: 503, body: {} } });
		const setHeaders = vi.fn();
		const fiche = await lireFicheCommune('44109', fetchImpl, { setHeaders });
		expect(fiche.etat).toBe('panne');
		expect(setHeaders).toHaveBeenCalledTimes(1);
		expect(setHeaders).toHaveBeenCalledWith(marque);
	});

	it('posts nothing for a fiche, an unread commune or a commune out of coverage', async () => {
		const setHeaders = vi.fn();
		const fiche = source({ '/v1/commune': { status: 200, body: { statut: 'ok', donnees: [COMMUNE_85047_CHALLANS], meta: { source: [] } } } });
		expect((await lireFicheCommune('85047', fiche.fetchImpl, { setHeaders })).etat).toBe('fiche');

		const nonRenseignee = source({
			'/v1/commune': { status: 200, body: vide },
			'/v1/communes': { status: 200, body: referentiel([{ code_insee: '44109', nom: 'Nantes', departement: '44' }]) }
		});
		expect((await lireFicheCommune('44109', nonRenseignee.fetchImpl, { setHeaders })).etat).toBe('non-renseignee');

		const horsCouverture = source({
			'/v1/commune': { status: 200, body: { statut: 'ok', donnees: [], meta: { source: [], hebergement: { commune: { etat: 'hors_couverture', motif: 'hors_perimetre' } } } } },
			'/v1/communes': { status: 200, body: referentiel([{ code_insee: '29019', nom: 'Brest', departement: '29' }]) }
		});
		expect((await lireFicheCommune('29019', horsCouverture.fetchImpl, { setHeaders })).etat).toBe('hors-couverture');
		expect(setHeaders).not.toHaveBeenCalled();
	});

	it('reads the same without setHeaders (the JSON export)', async () => {
		const { fetchImpl } = source({ '/v1/commune': { status: 503, body: {} }, '/v1/communes': { status: 503, body: {} } });
		expect((await lireFicheCommune('44109', fetchImpl)).etat).toBe('panne');
	});
});

describe('parentDeLaZone', () => {
	it('trusts the served commune_parent, falls back on the code, and obeys an explicit « commune »', () => {
		expect(parentDeLaZone('69383', { type_zone: 'arrondissement', commune_parent: '69123' })).toEqual({ code: '69123', nom: 'Lyon' });
		expect(parentDeLaZone('13203')).toEqual({ code: '13055', nom: 'Marseille' });
		expect(parentDeLaZone('13203', { type_zone: 'commune' })).toBeNull();
		expect(parentDeLaZone('44109')).toBeNull();
	});
});

// Review 2026-11-10, R-20: a 200 whose body is not the contract (a proxy page, a version skew during a
// deploy) is an outage like any other failed read, never an exception that ends on the root error page.
describe('lireFicheCommune — réponse 200 hors contrat (R-20)', () => {
	for (const [nom, body] of [
		['an empty object', {}],
		['a donnees that is not a list', { statut: 'ok', donnees: {} }],
		['a null body', null]
	] as const) {
		it(`is an outage marked for the 503 when the synthesis is ${nom}`, async () => {
			const { fetchImpl } = source({ '/v1/commune': { status: 200, body }, '/v1/communes': { status: 200, body: referentiel([]) } });
			const setHeaders = vi.fn();
			const fiche = await lireFicheCommune('85047', fetchImpl, { setHeaders });
			expect(fiche.etat).toBe('panne');
			expect(setHeaders).toHaveBeenCalledTimes(1);
			expect(setHeaders).toHaveBeenCalledWith(expect.objectContaining({ 'x-dc-etat-page': 'panne' }));
		});
	}
});
