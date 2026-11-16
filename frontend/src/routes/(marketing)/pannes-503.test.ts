// Review 2026-11-10, R-21: an outage page is answered 503 with `Retry-After` (04.7-10, decision 11). The
// commune and departement pages did it; `/datasets`, `/communes` and `/dpe/[numero]` answered 200 (the first
// two) or a 503 without `Retry-After` (the last). A load marks its outage through `setHeaders`
// (`markOutage`); `hooks.server.ts` turns the mark into the status and the header.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { load as loadCommunes } from './communes/+page';
import { load as loadDatasets } from './datasets/+page';
import { load as loadDpe } from './dpe/[numero]/+page.server';

const NUMERO = '2485E0000003C';
const MARQUE_PANNE = { 'x-dc-etat-page': 'panne' };

function reponse(status: number, body: unknown = {}, headers: Record<string, string> = {}) {
	return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } });
}
const fetchPanne = (retryAfter?: string) =>
	vi.fn(async () => reponse(503, { detail: 'texte serveur' }, retryAfter ? { 'Retry-After': retryAfter } : {})) as unknown as typeof fetch;

afterEach(() => vi.restoreAllMocks());

describe('/datasets — panne', () => {
	const appeler = (fetch: typeof globalThis.fetch, setHeaders = vi.fn()) =>
		(loadDatasets as unknown as (e: object) => Promise<{ panne: unknown }>)({ fetch, setHeaders });

	it('marks a failed catalogue for the 503, with the served Retry-After', async () => {
		const setHeaders = vi.fn();
		const data = await appeler(fetchPanne('45'), setHeaders);
		expect(data.panne).not.toBeNull();
		expect(setHeaders).toHaveBeenCalledTimes(1);
		expect(setHeaders).toHaveBeenCalledWith({ ...MARQUE_PANNE, 'x-dc-retry-after': '45' });
	});

	it('marks a rate limit too: the page is not served as a 200 to a robot', async () => {
		const setHeaders = vi.fn();
		await appeler(vi.fn(async () => reponse(429, {}, { 'Retry-After': '60' })) as unknown as typeof fetch, setHeaders);
		expect(setHeaders).toHaveBeenCalledWith({ ...MARQUE_PANNE, 'x-dc-retry-after': '60' });
	});

	it('leaves a served catalogue a 200', async () => {
		const setHeaders = vi.fn();
		await appeler(vi.fn(async () => reponse(200, { statut: 'ok', donnees: [] })) as unknown as typeof fetch, setHeaders);
		expect(setHeaders).not.toHaveBeenCalled();
	});
});

describe('/communes — panne', () => {
	const appeler = (url: string, fetch: typeof globalThis.fetch, setHeaders = vi.fn()) =>
		(loadCommunes as unknown as (e: object) => Promise<{ panne: unknown }>)({ url: new URL(url, 'https://site.invalid'), fetch, setHeaders });

	it('marks a failed directory for the 503, with the served Retry-After', async () => {
		const setHeaders = vi.fn();
		const data = await appeler('/communes', fetchPanne('30'), setHeaders);
		expect(data.panne).not.toBeNull();
		expect(setHeaders).toHaveBeenCalledTimes(1);
		expect(setHeaders).toHaveBeenCalledWith({ ...MARQUE_PANNE, 'x-dc-retry-after': '30' });
	});

	it('marks a failed commune search for the 503', async () => {
		const setHeaders = vi.fn();
		const data = await appeler('/communes?q=nantes', fetchPanne('30'), setHeaders);
		expect(data.panne).not.toBeNull();
		expect(setHeaders).toHaveBeenCalledTimes(1);
		expect(setHeaders).toHaveBeenCalledWith({ ...MARQUE_PANNE, 'x-dc-retry-after': '30' });
	});

	it('leaves a search whose only address group failed a 200: the communes are served', async () => {
		const setHeaders = vi.fn();
		const fetch = vi.fn(async (input: RequestInfo | URL) =>
			String(input).includes('/v1/communes/search')
				? reponse(200, { statut: 'ok', donnees: [], meta: {} })
				: reponse(503)
		) as unknown as typeof globalThis.fetch;
		const data = await appeler('/communes?q=nantes', fetch, setHeaders);
		expect(data.panne).toBeNull();
		expect(setHeaders).not.toHaveBeenCalled();
	});

	it('does nothing for a query of one character (no request)', async () => {
		const setHeaders = vi.fn();
		await appeler('/communes?q=n', fetchPanne(), setHeaders);
		expect(setHeaders).not.toHaveBeenCalled();
	});
});

describe('/dpe/[numero] — panne', () => {
	function evenement(setHeaders = vi.fn()) {
		return {
			params: { numero: NUMERO },
			request: new Request(`https://site.invalid/dpe/${NUMERO}`),
			platform: undefined,
			setHeaders
		} as unknown as Parameters<typeof loadDpe>[0];
	}

	it('an outage is a 503 that tells a robot when to come back', async () => {
		vi.spyOn(globalThis, 'fetch').mockResolvedValue(reponse(503, { title: 'x', status: 503 }));
		const setHeaders = vi.fn();
		await expect(loadDpe(evenement(setHeaders))).rejects.toMatchObject({ status: 503 });
		expect(setHeaders).toHaveBeenCalledWith({ 'retry-after': '60' });
	});

	it('a typed refusal (429) stays a 200 rendered by the page: no Retry-After header', async () => {
		vi.spyOn(globalThis, 'fetch').mockResolvedValue(reponse(429, { title: 'x', status: 429 }));
		const setHeaders = vi.fn();
		await loadDpe(evenement(setHeaders));
		expect(setHeaders).not.toHaveBeenCalledWith(expect.objectContaining({ 'retry-after': expect.anything() }));
	});

	it('an unknown number is a 404: no Retry-After header', async () => {
		vi.spyOn(globalThis, 'fetch').mockResolvedValue(reponse(404, { title: 'DPE inconnu', status: 404, code: 'dpe_inconnu', couverture: { departements: ['44'] } }));
		const setHeaders = vi.fn();
		await expect(loadDpe(evenement(setHeaders))).rejects.toMatchObject({ status: 404 });
		expect(setHeaders).not.toHaveBeenCalledWith(expect.objectContaining({ 'retry-after': expect.anything() }));
	});
});
