// @vitest-environment node
// Link preview of a commune fiche: a 1200 x 630 PNG of its served figures, a plain 404 for an unknown
// code, the generic image when no figure is served or the source failed (then briefly cached), one
// rendering per fiche however many requests ask, and a 304 on a matching ETag.
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { COMMUNE_85191_OBSERVED, COMMUNE_85191_SOURCES } from '$lib/data/__fixtures__/commune-85191-20261012';
import { viderCacheOg } from '$lib/server/og/service';
import { GET } from './+server';

const GENERIQUE = readFileSync(new URL('../../../../../static/og-image.png', import.meta.url));

function reponse(status: number, body: unknown) {
	return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

const FICHE = () => reponse(200, { donnees: [COMMUNE_85191_OBSERVED], meta: { source: COMMUNE_85191_SOURCES } });

/** A fetch that answers the synthesis with *synthese* and the referential with *referentiel*. */
function api(synthese: () => Response, referentiel: () => Response = () => reponse(200, { donnees: { results: [] } })) {
	return vi.fn(async (input: RequestInfo | URL) => (String(input).includes('/v1/commune?') ? synthese() : referentiel())) as unknown as typeof fetch & {
		mock: { calls: unknown[] };
	};
}

async function demander(fetch: typeof globalThis.fetch, code = '85191', entetes: Record<string, string> = {}) {
	const event = { params: { code }, fetch, request: new Request(`https://datacommune.fr/og/commune/${code}.png`, { headers: entetes }) };
	return (GET as unknown as (e: typeof event) => Promise<Response>)(event);
}

/** Width and height read from the IHDR chunk of a PNG. */
function dimensions(octets: Uint8Array): { largeur: number; hauteur: number } {
	expect(Array.from(octets.slice(0, 8))).toEqual([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
	const vue = new DataView(octets.buffer, octets.byteOffset, octets.byteLength);
	return { largeur: vue.getUint32(16), hauteur: vue.getUint32(20) };
}

beforeEach(() => viderCacheOg());
afterEach(() => vi.restoreAllMocks());

describe('/og/commune/[code].png', () => {
	it('draws a 1200 x 630 PNG of the fiche, cached a day, with its ETag and its cost', async () => {
		const res = await demander(api(FICHE));
		expect(res.status).toBe(200);
		expect(res.headers.get('content-type')).toBe('image/png');
		expect(res.headers.get('cache-control')).toBe('public, max-age=86400, stale-while-revalidate=604800');
		expect(res.headers.get('etag')).toMatch(/^"[\w-]{22}"$/);
		expect(res.headers.get('server-timing')).toMatch(/^og;desc="rendu";dur=\d+(\.\d)?$/);
		const png = new Uint8Array(await res.arrayBuffer());
		expect(dimensions(png)).toEqual({ largeur: 1200, hauteur: 630 });
		expect(Buffer.from(png).equals(GENERIQUE)).toBe(false);
	});

	it('reads and draws a fiche once for concurrent requests, then serves it from the cache', async () => {
		const fetch = api(FICHE);
		const [a, b] = await Promise.all([demander(fetch), demander(fetch)]);
		const c = await demander(fetch, '85191');
		expect(fetch.mock.calls).toHaveLength(1);
		expect(c.headers.get('server-timing')).toBe('og;desc="cache"');
		const [pa, pb, pc] = await Promise.all([a, b, c].map(async (r) => Buffer.from(await r.arrayBuffer())));
		expect(pa.equals(pb) && pa.equals(pc)).toBe(true);
	});

	it('answers 304 to a request that holds the same image', async () => {
		const premiere = await demander(api(FICHE));
		const res = await demander(api(FICHE), '85191', { 'if-none-match': premiere.headers.get('etag')! });
		expect(res.status).toBe(304);
		expect(await res.text()).toBe('');
	});

	it('404 for a malformed code, before any request', async () => {
		const fetch = api(FICHE);
		const res = await demander(fetch, 'ABC');
		expect(res.status).toBe(404);
		expect(res.headers.get('content-type')).toBe('text/plain; charset=utf-8');
		expect(await res.text()).toContain('ABC');
		expect(fetch.mock.calls).toHaveLength(0);
	});

	it('404 for a code the referential does not know', async () => {
		const res = await demander(api(() => reponse(200, { donnees: [] })), '99999');
		expect(res.status).toBe(404);
		expect(res.headers.get('content-type')).not.toBe('image/png');
	});

	it('the generic image when the fiche serves no figure, never a card of zeros', async () => {
		const sansChiffre = { ...COMMUNE_85191_OBSERVED, indicateurs_dvf: null, prix_m2_median: null, prix_m2_median_maison: null, prix_m2_median_appartement: null, population: null };
		const res = await demander(api(() => reponse(200, { donnees: [sansChiffre], meta: { source: COMMUNE_85191_SOURCES } })));
		expect(res.status).toBe(200);
		expect(Buffer.from(await res.arrayBuffer()).equals(GENERIQUE)).toBe(true);
		expect(res.headers.get('cache-control')).toBe('public, max-age=86400, stale-while-revalidate=604800');
	});

	it('the generic image, cached five minutes, when the synthesis fails; the card once it answers again', async () => {
		const panne = await demander(api(() => reponse(503, { detail: 'indisponible' })));
		expect(panne.status).toBe(200);
		expect(panne.headers.get('cache-control')).toBe('public, max-age=300');
		expect(Buffer.from(await panne.arrayBuffer()).equals(GENERIQUE)).toBe(true);

		// The outage is kept a minute in this process, not a day.
		vi.useFakeTimers({ toFake: ['Date'] });
		vi.setSystemTime(Date.now() + 61_000);
		const retour = await demander(api(FICHE));
		vi.useRealTimers();
		expect(Buffer.from(await retour.arrayBuffer()).equals(GENERIQUE)).toBe(false);
	});
});
