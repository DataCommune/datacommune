// 04-08 (D-101, D-102): `/bien/[token]` renders nothing any more. Every legacy link is a 308 to an
// internal target only:
//   - a legacy coordinate token → `/`, without any lookup and without the coordinate in the target
//     (T-04-08-02);
//   - an address slug whose label matches exactly one served address → `/adresse/{ban_id}`, the
//     key re-checked against the 04-02 pattern;
//   - anything else (zero, several, a street, an outage, a malformed key) → `/?q={label}`;
//   - an empty label → `/`, without any request.
// `?dpe=` and `?origine=` are ignored; the label is never logged (T-47-02).
import { afterEach, describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';

const TOKEN = '12-rue-des-lilas-85000-la-roche-sur-yon';
const LIBELLE = '12 rue des lilas 85000 la roche sur yon';

const LILAS = {
	type_resultat: 'adresse',
	ban_id: '85191_0420_00012',
	numero: '12',
	rep: null,
	nom_voie: 'Rue des Lilas',
	code_postal: '85000',
	code_commune: '85191',
	nom_commune: 'La Roche-sur-Yon',
	departement: { code: '85', nom: 'Vendée' }
};

function reponse(resultats: unknown[], status = 200): Response {
	const body =
		status === 200
			? { statut: 'ok', donnees: { ordre_types: ['adresse', 'commune'], resultats, hebergement: { ban: { etat: 'heberge', motif: null } } } }
			: { title: 'x', status, code: 'geocodage_indisponible' };
	return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function stubFetch(impl: () => Promise<Response>) {
	const fetchStub = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => impl());
	vi.stubGlobal('fetch', fetchStub);
	return fetchStub;
}

/** The redirect thrown by the loader (SvelteKit `Redirect`), or a failure if it returned. */
async function redirection(token: string, query = ''): Promise<{ status: number; location: string }> {
	const url = new URL('https://site.invalid/bien/' + token + query);
	const event = { params: { token }, url, request: new Request(url), platform: undefined };
	try {
		await (load as unknown as (e: unknown) => Promise<unknown>)(event);
	} catch (thrown) {
		const r = thrown as { status?: number; location?: string };
		if (typeof r.status === 'number' && typeof r.location === 'string') return { status: r.status, location: r.location };
		throw thrown;
	}
	throw new Error('the loader returned instead of redirecting');
}

function searched(fetchStub: ReturnType<typeof stubFetch>): URL[] {
	return fetchStub.mock.calls.map((call) => new URL(String(call[0]), 'http://o.invalid'));
}

afterEach(() => {
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

describe('/bien/[token] — 308 redirects only (D-101, D-102)', () => {
	it('a slug resolved to exactly one address goes to its page', async () => {
		const fetchStub = stubFetch(async () => reponse([LILAS]));

		expect(await redirection(TOKEN)).toEqual({ status: 308, location: '/adresse/85191_0420_00012' });

		const [appel] = searched(fetchStub);
		expect(fetchStub).toHaveBeenCalledTimes(1);
		expect(appel.pathname).toBe('/v1/adresses/recherche');
		expect(appel.searchParams.get('q')).toBe(LIBELLE);
		expect(appel.searchParams.get('limite')).toBe('2');
	});

	it('one exact address next to a nearby one still goes to the exact one', async () => {
		stubFetch(async () => reponse([LILAS, { ...LILAS, ban_id: '85191_0420_00012_bis', rep: 'bis' }]));

		expect(await redirection(TOKEN)).toEqual({ status: 308, location: '/adresse/85191_0420_00012' });
	});

	it('zero, several, a street, an inexact label or a malformed key go to the search with the label', async () => {
		const cas: unknown[][] = [
			[],
			[LILAS, { ...LILAS, ban_id: '85191_0420_00099' }],
			[{ ...LILAS, type_resultat: 'voie', ban_id: null, numero: null }],
			[{ ...LILAS, numero: '14', ban_id: '85191_0420_00014' }],
			[{ ...LILAS, ban_id: 'https://evil.example/x' }],
			[{ ...LILAS, ban_id: null }]
		];
		for (const resultats of cas) {
			stubFetch(async () => reponse(resultats));
			expect(await redirection(TOKEN), JSON.stringify(resultats)).toEqual({
				status: 308,
				location: '/?q=' + encodeURIComponent(LIBELLE)
			});
		}
	});

	it('an outage (503, network) is never a page: the search with the label, in a temporary 307 (C17)', async () => {
		// A permanent 308 would stay cached and send the link to the search after the outage.
		stubFetch(async () => reponse([], 503));
		expect(await redirection(TOKEN)).toEqual({ status: 307, location: '/?q=' + encodeURIComponent(LIBELLE) });

		stubFetch(async () => {
			throw new TypeError('fetch failed');
		});
		expect(await redirection(TOKEN)).toEqual({ status: 307, location: '/?q=' + encodeURIComponent(LIBELLE) });
	});

	it('a legacy coordinate token goes to the home page, unresolved and without the coordinate', async () => {
		for (const token of ['47.2,-1.55', '46.670345,-1.426123', '200,300']) {
			const fetchStub = stubFetch(async () => reponse([LILAS]));
			const cible = await redirection(token, '?origine=gps');
			expect(cible).toEqual({ status: 308, location: '/' });
			expect(fetchStub).not.toHaveBeenCalled();
		}
	});

	it('a slug with no letter nor digit goes home without any request', async () => {
		const fetchStub = stubFetch(async () => reponse([LILAS]));
		expect(await redirection('---')).toEqual({ status: 308, location: '/' });
		expect(fetchStub).not.toHaveBeenCalled();
	});

	it('a label the search refuses by its length is not sent: the search page gets it', async () => {
		const fetchStub = stubFetch(async () => reponse([LILAS]));
		expect(await redirection('a')).toEqual({ status: 308, location: '/?q=a' });
		const long = 'x'.repeat(201);
		expect(await redirection(long)).toEqual({ status: 308, location: '/?q=' + long });
		expect(fetchStub).not.toHaveBeenCalled();
	});

	it('?dpe= is ignored: same target, nothing of it sent nor carried', async () => {
		const fetchStub = stubFetch(async () => reponse([LILAS]));
		const cible = await redirection(TOKEN, '?dpe=2285E0123456X&type_bien=maison');
		expect(cible).toEqual({ status: 308, location: '/adresse/85191_0420_00012' });
		expect(String(fetchStub.mock.calls[0][0])).not.toContain('2285E0123456X');
	});

	it('every target is internal: a path starting with a single slash', async () => {
		stubFetch(async () => reponse([]));
		const cible = await redirection('//evil.example');
		expect(cible.location).toMatch(/^\/(?!\/)/);
		expect(cible.location).not.toContain('//');
	});

	it('the label is never logged', async () => {
		const espions = (['log', 'info', 'warn', 'error', 'debug'] as const).map((m) => vi.spyOn(console, m).mockImplementation(() => {}));
		stubFetch(async () => reponse([], 503));
		await redirection(TOKEN);
		stubFetch(async () => {
			throw new TypeError('fetch failed');
		});
		await redirection(TOKEN);
		for (const espion of espions) expect(espion).not.toHaveBeenCalled();
	});
});
