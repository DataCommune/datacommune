// Reading of the departement pages (04.7-10, decisions 6 and 7): `/departement/[code]` reads the key
// figures and the list of departements, `/departements` reads the list. A malformed code is a 404
// before any request, an unknown one a 404, a failed read an outage that the hook turns into a 503.
import { describe, expect, it, vi } from 'vitest';
import { DEPARTEMENT_44, DEPARTEMENTS_LISTE } from './__fixtures__/departement';
import { groupesParRegion, lireDepartement, lireDepartements } from './departement';

type Reponse = { status: number; body: unknown; headers?: Record<string, string> };

/** A fetch that answers by path, and keeps what was asked. */
function source(reponses: Record<string, Reponse>) {
	const appels: string[] = [];
	const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
		const url = new URL(String(input), 'http://api.test');
		appels.push(url.pathname);
		const reponse = reponses[url.pathname];
		if (!reponse) throw new TypeError(`unexpected request ${url.pathname}`);
		return new Response(JSON.stringify(reponse.body), {
			status: reponse.status,
			headers: { 'Content-Type': 'application/json', ...reponse.headers }
		});
	});
	return { fetchImpl: fetchImpl as unknown as typeof fetch, appels };
}

const SYNTHESE_44: Reponse = {
	status: 200,
	body: {
		statut: 'ok',
		donnees: DEPARTEMENT_44,
		meta: { source: [{ nom: 'DVF', licence: 'Licence Ouverte 2.0', url: 'https://example.invalid/dvf' }] }
	}
};
const LISTE: Reponse = { status: 200, body: { statut: 'ok', donnees: DEPARTEMENTS_LISTE } };

describe('lireDepartement', () => {
	it('reads the figures and the list of departements, and keeps the sources as a list', async () => {
		const { fetchImpl, appels } = source({ '/v1/departements/44': SYNTHESE_44, '/v1/departements': LISTE });
		const page = await lireDepartement('44', fetchImpl);
		expect(page.etat).toBe('synthese');
		if (page.etat !== 'synthese') return;
		expect(page.code).toBe('44');
		expect(page.synthese.nom).toBe('Loire-Atlantique');
		expect(page.departements?.map((d) => d.code)).toEqual(DEPARTEMENTS_LISTE.map((d) => d.code));
		expect(page.sources).toEqual([{ nom: 'DVF', licence: 'Licence Ouverte 2.0', url: 'https://example.invalid/dvf' }]);
		expect([...appels].sort()).toEqual(['/v1/departements', '/v1/departements/44', '/v1/departements/44/fiches']);
		// No fiches served by this double: the directory is left out, never an empty list shown as a fact.
		expect(page.fiches).toBeNull();
	});

	it('reads the pages of the departement for its A-Z directory', async () => {
		const fiches = [
			{ code_insee: '44109', nom: 'Nantes', type_zone: 'commune' },
			{ code_insee: '44162', nom: 'Saint-Herblain', type_zone: 'commune' }
		];
		const { fetchImpl } = source({
			'/v1/departements/44': SYNTHESE_44,
			'/v1/departements': LISTE,
			'/v1/departements/44/fiches': { status: 200, body: { statut: 'ok', donnees: { code: '44', derniere_importation: null, fiches } } }
		});
		const page = await lireDepartement('44', fetchImpl);
		expect(page.etat === 'synthese' && page.fiches).toEqual(fiches);
	});

	it('keeps the page when only the pages of the departement fail: no directory, no outage', async () => {
		for (const reponse of [{ status: 503, body: {} }, { status: 200, body: { statut: 'ok', donnees: { code: '44' } } }]) {
			const { fetchImpl } = source({ '/v1/departements/44': SYNTHESE_44, '/v1/departements': LISTE, '/v1/departements/44/fiches': reponse });
			const setHeaders = vi.fn();
			const page = await lireDepartement('44', fetchImpl, { setHeaders });
			expect(page.etat).toBe('synthese');
			expect(page.etat === 'synthese' && page.fiches).toBeNull();
			expect(setHeaders).not.toHaveBeenCalled();
		}
	});

	it('reads a single source object as a list of one, and no source as an empty list', async () => {
		const seule = { ...SYNTHESE_44, body: { ...(SYNTHESE_44.body as object), meta: { source: { nom: 'DVF' } } } };
		const sans = { ...SYNTHESE_44, body: { statut: 'ok', donnees: DEPARTEMENT_44 } };
		for (const [reponse, attendu] of [
			[seule, [{ nom: 'DVF' }]],
			[sans, []]
		] as const) {
			const { fetchImpl } = source({ '/v1/departements/44': reponse, '/v1/departements': LISTE });
			const page = await lireDepartement('44', fetchImpl);
			expect(page.etat === 'synthese' && page.sources).toEqual(attendu);
		}
	});

	it('reads 2a as 2A, the way the API does', async () => {
		const { fetchImpl, appels } = source({
			'/v1/departements/2A': { status: 200, body: { statut: 'ok', donnees: { ...DEPARTEMENT_44, code: '2A', nom: 'Corse-du-Sud' } } },
			'/v1/departements': LISTE
		});
		const page = await lireDepartement('2a', fetchImpl);
		expect(page.code).toBe('2A');
		expect(appels).toContain('/v1/departements/2A');
	});

	it('answers a 404 to a malformed code before any request', async () => {
		for (const code of ['4', '444', '4A', '2C', 'abc', '44/../x', '']) {
			const { fetchImpl, appels } = source({});
			await expect(lireDepartement(code, fetchImpl), code).rejects.toMatchObject({ status: 404 });
			expect(appels, code).toEqual([]);
		}
	});

	it('answers a 404 to a departement the referential does not know', async () => {
		const { fetchImpl } = source({
			'/v1/departements/99': { status: 404, body: { detail: 'Département inconnu' } },
			'/v1/departements': LISTE
		});
		await expect(lireDepartement('99', fetchImpl)).rejects.toMatchObject({ status: 404 });
	});

	it('keeps the page when only the list of departements fails: its links are left out, nothing else', async () => {
		const { fetchImpl } = source({ '/v1/departements/44': SYNTHESE_44, '/v1/departements': { status: 503, body: {} } });
		const setHeaders = vi.fn();
		const page = await lireDepartement('44', fetchImpl, { setHeaders });
		expect(page.etat === 'synthese' && page.departements).toBeNull();
		expect(setHeaders).not.toHaveBeenCalled();
	});

	it('is an outage when the figures fail, marked for the 503 with the served Retry-After', async () => {
		const { fetchImpl } = source({
			'/v1/departements/44': { status: 429, body: {}, headers: { 'Retry-After': '45' } },
			'/v1/departements': LISTE
		});
		const setHeaders = vi.fn();
		const page = await lireDepartement('44', fetchImpl, { setHeaders });
		expect(page.etat).toBe('panne');
		if (page.etat !== 'panne') return;
		expect(page.code).toBe('44');
		expect(page.nom).toBe('Loire-Atlantique');
		expect(page.detail.statut).toBe(429);
		expect(page.retryAfter).toBe(45);
		expect(setHeaders).toHaveBeenCalledTimes(1);
		expect(setHeaders).toHaveBeenCalledWith({ 'x-dc-etat-page': 'panne', 'x-dc-retry-after': '45' });
	});

	it('is also an outage for a network failure, and reads without setHeaders', async () => {
		const reseau = vi.fn(async () => {
			throw new TypeError('fetch failed');
		}) as unknown as typeof fetch;
		const page = await lireDepartement('44', reseau);
		expect(page.etat === 'panne' && page.detail.statut).toBeNull();
		expect(page.etat === 'panne' && page.retryAfter).toBeNull();
	});
});

describe('lireDepartement — réponse 200 hors contrat (R-20)', () => {
	for (const [nom, body] of [
		['an empty object', {}],
		['a donnees that is not an object', { statut: 'ok', donnees: 'maintenance' }],
		['a null body', null]
	] as const) {
		it(`is an outage marked for the 503 when the figures are ${nom}`, async () => {
			const { fetchImpl } = source({ '/v1/departements/44': { status: 200, body }, '/v1/departements': LISTE });
			const setHeaders = vi.fn();
			const page = await lireDepartement('44', fetchImpl, { setHeaders });
			expect(page.etat).toBe('panne');
			expect(setHeaders).toHaveBeenCalledTimes(1);
			expect(setHeaders).toHaveBeenCalledWith(expect.objectContaining({ 'x-dc-etat-page': 'panne' }));
		});
	}
});

describe('groupesParRegion', () => {
	it('groups by region in alphabetical order, the departements of a region in the order of their codes', () => {
		const groupes = groupesParRegion(DEPARTEMENTS_LISTE);
		expect(groupes.map((g) => g.region)).toEqual(['Bretagne', 'Corse', 'Pays de la Loire']);
		expect(groupes.find((g) => g.region === 'Pays de la Loire')?.departements.map((d) => d.code)).toEqual(['44', '85']);
		expect(groupes.find((g) => g.region === 'Corse')?.departements.map((d) => d.code)).toEqual(['2A', '2B']);
	});

	it('puts a departement without region apart, last, never under another region', () => {
		const groupes = groupesParRegion([...DEPARTEMENTS_LISTE, { ...DEPARTEMENTS_LISTE[0], code: '76', nom: 'Seine-Maritime', region: null }]);
		expect(groupes.at(-1)).toMatchObject({ region: null });
		expect(groupes.at(-1)?.departements.map((d) => d.code)).toEqual(['76']);
	});

	it('is empty for an empty list', () => {
		expect(groupesParRegion([])).toEqual([]);
	});
});

describe('lireDepartements', () => {
	it('reads the list once and groups it', async () => {
		const { fetchImpl, appels } = source({ '/v1/departements': LISTE });
		const page = await lireDepartements(fetchImpl);
		expect(appels).toEqual(['/v1/departements']);
		expect(page.etat === 'liste' && page.total).toBe(DEPARTEMENTS_LISTE.length);
		expect(page.etat === 'liste' && page.groupes.length).toBe(3);
	});

	it('is an outage when the list fails, marked for the 503', async () => {
		const { fetchImpl } = source({ '/v1/departements': { status: 503, body: {} } });
		const setHeaders = vi.fn();
		const page = await lireDepartements(fetchImpl, { setHeaders });
		expect(page.etat).toBe('panne');
		expect(setHeaders).toHaveBeenCalledWith({ 'x-dc-etat-page': 'panne' });
	});

	it('posts no marker when the list is served', async () => {
		const { fetchImpl } = source({ '/v1/departements': LISTE });
		const setHeaders = vi.fn();
		await lireDepartements(fetchImpl, { setHeaders });
		expect(setHeaders).not.toHaveBeenCalled();
	});
});
