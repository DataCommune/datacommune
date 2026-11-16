// Findings R-16, R-19 and R-21 of the review of 2026-11-10, on the loaders of the address fiche. The
// weekly allowance is the real one here (`$lib/server/fiche-quota`, not mocked): each test uses its
// own session id, the counter being one per process.
import type { Cookies } from '@sveltejs/kit';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { admettreFiche, QUOTA_COOKIE } from '$lib/server/fiche-quota';
import { load as loadIdentite } from './+layout.server';
import { load as loadListes } from './+page.server';

const ID = '85191_0420_00012';
let numeroSession = 0;

function nouvelleSession(): string {
	numeroSession += 1;
	return `${'ab'.repeat(12)}${String(numeroSession).padStart(8, '0')}`;
}

function bocal(initial?: string) {
	const jar = new Map<string, string>(initial ? [[QUOTA_COOKIE, initial]] : []);
	const set = vi.fn((name: string, value: string) => void jar.set(name, value));
	return { cookies: { get: (name: string) => jar.get(name), set } as unknown as Cookies, set };
}

function evenement(id: string, search = '', cookies: Cookies = bocal().cookies) {
	const url = new URL(`https://site.invalid/adresse/${id}${search}`);
	return {
		params: { id },
		url,
		request: new Request(url, { headers: { 'user-agent': 'Mozilla/5.0 Firefox/140.0' } }),
		platform: undefined,
		cookies,
		getClientAddress: () => '192.0.2.1',
		setHeaders: vi.fn(),
		depends: vi.fn()
	};
}

const chargerListes = (e: ReturnType<typeof evenement>) => (loadListes as unknown as (e: unknown) => Promise<unknown>)(e);
const chargerIdentite = (e: ReturnType<typeof evenement>) => (loadIdentite as unknown as (e: unknown) => Promise<unknown>)(e);

/** Spends the whole allowance of *session* on other fiches. */
async function epuiser(session: string) {
	const { cookies } = bocal(session);
	for (let i = 0; i < 100; i++) {
		const fiche = `44109_0001_${String(i).padStart(5, '0')}`;
		const admission = await admettreFiche({ cookies, request: new Request('https://site.invalid/'), getClientAddress: () => '192.0.2.1' }, fiche);
		expect(admission.admise).toBe(true);
	}
}

function repondre(selon: (chemin: string) => number) {
	const stub = vi.fn(async (input: RequestInfo | URL) => {
		const status = selon(new URL(String(input), 'http://o.invalid').pathname);
		const body = status === 409 ? { title: 'x', status, code: 'generation_changee' } : { detail: 'x' };
		return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
	});
	vi.stubGlobal('fetch', stub);
	return stub;
}

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('R-16 — the lists of a fiche are read under the same allowance as its identity', () => {
	it('a list request for a new fiche past the allowance → 429 with the date, nothing read', async () => {
		const session = nouvelleSession();
		await epuiser(session);
		const fetchStub = repondre(() => 500);
		await expect(chargerListes(evenement(ID, '?page=2', bocal(session).cookies))).rejects.toMatchObject({
			status: 429,
			body: { message: expect.stringMatching(/100 fiches d'adresse différentes cette semaine.*de nouveau le \d+ \S+/) }
		});
		expect(fetchStub).not.toHaveBeenCalled();
	});

	it('witness: a page change of a fiche already opened stays free past the allowance', async () => {
		const session = nouvelleSession();
		const { cookies } = bocal(session);
		await admettreFiche({ cookies, request: new Request('https://site.invalid/'), getClientAddress: () => '192.0.2.1' }, ID);
		await epuiser(session).catch(() => undefined);
		const fetchStub = repondre(() => 500);
		await expect(chargerListes(evenement(ID, '?page=2', cookies))).resolves.toBeDefined();
		expect(fetchStub).toHaveBeenCalled();
	});

	it('a key of a form the API refuses → 404 « Adresse inconnue » from the lists too, nothing read, nothing counted', async () => {
		const fetchStub = repondre(() => 500);
		const { cookies, set } = bocal();
		await expect(chargerListes(evenement('xyz', '', cookies))).rejects.toMatchObject({ status: 404, body: { message: 'Adresse inconnue' } });
		expect(fetchStub).not.toHaveBeenCalled();
		expect(set).not.toHaveBeenCalled();
	});

	it('witness: the two loaders of one first visit open one session, not two (cookies.get reads the cookie set earlier in the request)', async () => {
		repondre(() => 500);
		const { cookies, set } = bocal();
		const e = evenement(ID, '', cookies);
		await Promise.allSettled([chargerIdentite(e), chargerListes(e)]);
		expect(set).toHaveBeenCalledTimes(1);
	});
});

describe('R-19 — back to the first page of the documents, the other parameters kept', () => {
	const params = (location: string) => Object.fromEntries(new URL(location, 'https://site.invalid').searchParams);

	it('a malformed ?page= keeps the criteria of the similar sales and the sales page', async () => {
		const fetchStub = repondre(() => 500);
		const e = evenement(ID, '?page=abc&type=maison&rayon=1000&ventes_page=2', bocal(nouvelleSession()).cookies);
		const refus = await chargerListes(e).catch((r: unknown) => r as { status: number; location: string });
		expect(refus).toMatchObject({ status: 303 });
		expect(params((refus as { location: string }).location)).toEqual({ page: '1', type: 'maison', rayon: '1000', ventes_page: '2' });
		expect(fetchStub).not.toHaveBeenCalled();
	});

	it('a stale generation of the documents drops `g` only', async () => {
		repondre((chemin) => (chemin.endsWith('/dpe') ? 409 : 500));
		const e = evenement(ID, '?page=2&g=ffffffffffffffff&type=maison&ventes_page=2&vg=0123456789abcdef', bocal(nouvelleSession()).cookies);
		const refus = await chargerListes(e).catch((r: unknown) => r as { status: number; location: string });
		expect(refus).toMatchObject({ status: 303 });
		expect(params((refus as { location: string }).location)).toEqual({ page: '1', type: 'maison', ventes_page: '2', vg: '0123456789abcdef' });
	});
});

describe('R-21 — an outage of the fiche is a 503 with Retry-After (04.7-10, decision 11)', () => {
	it.each([500, 503])('the identity answered %i → 503 and Retry-After: 60', async (status) => {
		repondre(() => status);
		const e = evenement(ID, '', bocal(nouvelleSession()).cookies);
		await expect(chargerIdentite(e)).rejects.toMatchObject({ status: 503 });
		expect(e.setHeaders).toHaveBeenCalledWith({ 'retry-after': '60' });
	});

	it('witness: an unknown address is a 404 without Retry-After', async () => {
		vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ title: 'x', status: 404, code: 'adresse_inconnue' }), { status: 404, headers: { 'Content-Type': 'application/json' } })));
		const e = evenement(ID, '', bocal(nouvelleSession()).cookies);
		await expect(chargerIdentite(e)).rejects.toMatchObject({ status: 404 });
		expect(e.setHeaders).not.toHaveBeenCalledWith(expect.objectContaining({ 'retry-after': expect.anything() }));
	});
});

describe('R-16, option B — a fiche that is not one is given back (orchestrator, 2026-11-10)', () => {
	const IDENTITE = {
		etat: 'etablie',
		libelle: '12 Rue des Lilas, 85000 La Roche-sur-Yon',
		numero: '12',
		rep: null,
		nom_voie: 'Rue des Lilas',
		code_postal: '85000',
		commune: { code_insee: '85191', nom: 'La Roche-sur-Yon' },
		departement: { code: '85', nom: 'Vendée' },
		parcelle: { etat: 'unique', id_parcelle: '85191000AB0012' }
	};
	const SERVIE = (banId: string) => ({
		status: 200,
		body: { statut: 'ok', donnees: { adresse: { ...IDENTITE, ban_id: banId }, couverture: { etat: 'hors_couverture', motif: 'territoire_hors_perimetre' } } }
	});
	const ISSUES = {
		inconnue: { status: 404, body: { title: 'x', status: 404, code: 'adresse_inconnue' } },
		voie: { status: 404, body: { title: 'x', status: 404, code: 'voie_sans_numero' } },
		panne: { status: 500, body: { detail: 'x' } },
		refus: { status: 409, body: { detail: 'x' } }
	};

	/** The identity of every key is served, except the keys of *autres*. */
	function identites(autres: Record<string, { status: number; body: unknown }>) {
		vi.stubGlobal(
			'fetch',
			vi.fn(async (input: RequestInfo | URL) => {
				const banId = /\/adresse\/([^/?]+)/.exec(String(input))?.[1] ?? '';
				const r = autres[banId] ?? SERVIE(banId);
				return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
			})
		);
	}

	async function remplir(session: string, n: number) {
		const { cookies } = bocal(session);
		for (let i = 0; i < n; i++) {
			await admettreFiche({ cookies, request: new Request('https://site.invalid/'), getClientAddress: () => '192.0.2.1' }, `44109_0004_${String(i).padStart(5, '0')}`);
		}
	}

	const ouvrir = (id: string, session: string) => chargerIdentite(evenement(id, '', bocal(session).cookies));

	it('witness, frozen: 200, 200, then 429 with the date — the proof of production (09/11)', async () => {
		identites({});
		const session = nouvelleSession();
		await remplir(session, 98);
		await expect(ouvrir('85191_0420_00101', session)).resolves.toBeDefined();
		await expect(ouvrir('85191_0420_00102', session)).resolves.toBeDefined();
		await expect(ouvrir('85191_0420_00103', session)).rejects.toMatchObject({
			status: 429,
			body: { message: expect.stringMatching(/100 fiches d'adresse différentes cette semaine.*de nouveau le \d+ \S+\.$/) }
		});
	});

	it.each([
		['inconnue', 404],
		['voie', 404],
		['panne', 503]
	] as const)('%s (%i) at 99: given back, the next fiche is served, the one after is refused', async (cas, statut) => {
		identites({ '85191_0420_00201': ISSUES[cas] });
		const session = nouvelleSession();
		await remplir(session, 99);
		await expect(ouvrir('85191_0420_00201', session)).rejects.toMatchObject({ status: statut });
		await expect(ouvrir('85191_0420_00202', session)).resolves.toBeDefined();
		await expect(ouvrir('85191_0420_00203', session)).rejects.toMatchObject({ status: 429 });
	});

	it('the same unknown key read twice never brings the count under what it was', async () => {
		identites({ '85191_0420_00301': ISSUES.inconnue });
		const session = nouvelleSession();
		await remplir(session, 99);
		await expect(ouvrir('85191_0420_00301', session)).rejects.toMatchObject({ status: 404 });
		await expect(ouvrir('85191_0420_00301', session)).rejects.toMatchObject({ status: 404 });
		await expect(ouvrir('85191_0420_00302', session)).resolves.toBeDefined();
		await expect(ouvrir('85191_0420_00303', session)).rejects.toMatchObject({ status: 429 });
	});

	it('a fiche already counted, now unknown, stays counted (only this request’s admission is given back)', async () => {
		const session = nouvelleSession();
		await remplir(session, 99);
		identites({});
		await expect(ouvrir('85191_0420_00401', session)).resolves.toBeDefined();
		identites({ '85191_0420_00401': ISSUES.panne });
		await expect(ouvrir('85191_0420_00401', session)).rejects.toMatchObject({ status: 503 });
		await expect(ouvrir('85191_0420_00402', session)).rejects.toMatchObject({ status: 429 });
	});

	it('witness: a typed refusal of the API is a served page, the fiche stays counted', async () => {
		identites({ '85191_0420_00501': ISSUES.refus });
		const session = nouvelleSession();
		await remplir(session, 99);
		await expect(ouvrir('85191_0420_00501', session)).resolves.toBeDefined();
		await expect(ouvrir('85191_0420_00502', session)).rejects.toMatchObject({ status: 429 });
	});
});
