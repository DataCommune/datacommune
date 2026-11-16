// @vitest-environment node
// Link preview of a departement page: a 1200 x 630 PNG of its served figures, a 404 for an unknown code,
// the generic image on an outage.
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DEPARTEMENT_44 } from '$lib/data/__fixtures__/departement';
import { viderCacheOg } from '$lib/server/og/service';
import { GET } from './+server';

const GENERIQUE = readFileSync(new URL('../../../../../static/og-image.png', import.meta.url));
const SOURCES = [{ nom: 'Demandes de valeurs foncières (DVF)', producteur: 'DGFiP / Etalab', licence: 'Licence Ouverte 2.0' }];

function reponse(status: number, body: unknown) {
	return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function api(synthese: () => Response) {
	return vi.fn(async (input: RequestInfo | URL) =>
		/\/v1\/departements\/[^/?]+/.test(String(input)) ? synthese() : reponse(200, { statut: 'ok', donnees: [] })
	) as unknown as typeof fetch;
}

async function demander(fetch: typeof globalThis.fetch, code = '44') {
	const event = { params: { code }, fetch, request: new Request(`https://datacommune.fr/og/departement/${code}.png`) };
	return (GET as unknown as (e: typeof event) => Promise<Response>)(event);
}

beforeEach(() => viderCacheOg());
afterEach(() => vi.restoreAllMocks());

describe('/og/departement/[code].png', () => {
	it('draws a 1200 x 630 PNG of the departement', async () => {
		const res = await demander(api(() => reponse(200, { donnees: DEPARTEMENT_44, meta: { source: SOURCES } })));
		expect(res.status).toBe(200);
		expect(res.headers.get('content-type')).toBe('image/png');
		const png = Buffer.from(await res.arrayBuffer());
		expect([png.readUInt32BE(16), png.readUInt32BE(20)]).toEqual([1200, 630]);
		expect(png.equals(GENERIQUE)).toBe(false);
	});

	it('404 for a malformed code, and for a code the API does not know', async () => {
		expect((await demander(api(() => reponse(200, {})), '4X4')).status).toBe(404);
		const inconnu = await demander(api(() => reponse(404, { detail: 'inconnu' })), '99');
		expect(inconnu.status).toBe(404);
		expect(inconnu.headers.get('content-type')).toBe('text/plain; charset=utf-8');
	});

	it('the generic image, briefly cached, on an outage', async () => {
		const res = await demander(api(() => reponse(503, { detail: 'indisponible' })));
		expect(res.status).toBe(200);
		expect(res.headers.get('cache-control')).toBe('public, max-age=300');
		expect(Buffer.from(await res.arrayBuffer()).equals(GENERIQUE)).toBe(true);
	});
});
