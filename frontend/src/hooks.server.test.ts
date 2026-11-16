import { describe, it, expect } from 'vitest';
import type { RequestEvent } from '@sveltejs/kit';
import { handle, internalApiFetch, outageResponse, relayDevApi, securityHeaders } from './hooks.server';
import { API_URL } from '$lib/config';
import {
	DEFAULT_RETRY_AFTER_SECONDS,
	MAX_RETRY_AFTER_SECONDS,
	OUTAGE_RETRY_HEADER,
	PAGE_STATE_HEADER
} from '$lib/utils/etat-page';
import { SECURITY_HEADERS } from '$lib/utils/security-headers.js';

/** Minimal event: `securityHeaders` only forwards it to `resolve`. */
const event = {} as RequestEvent;

function run(response: Response) {
	return securityHeaders({ event, resolve: async () => response }) as Promise<Response>;
}

describe('hooks.server securityHeaders', () => {
	it('sets every canonical security header on a rendered response', async () => {
		const res = await run(new Response('<html></html>'));
		for (const [name, value] of Object.entries(SECURITY_HEADERS)) {
			expect(res.headers.get(name)).toBe(value);
		}
	});

	it('never overrides a header the response already carries', async () => {
		const res = await run(
			new Response('<html></html>', {
				headers: { 'X-Frame-Options': 'SAMEORIGIN' }
			})
		);
		expect(res.headers.get('X-Frame-Options')).toBe('SAMEORIGIN');
		expect(res.headers.get('X-Content-Type-Options')).toBe('nosniff');
	});

	it('returns the same response instance, body untouched', async () => {
		const original = new Response('payload');
		const res = await run(original);
		expect(res).toBe(original);
		expect(await res.text()).toBe('payload');
	});
});

describe('hooks.server KI-P12 noindex on /bien', () => {
	function runAt(pathname: string, response: Response) {
		const e = { url: new URL(`https://datacommune.fr${pathname}`) } as RequestEvent;
		return securityHeaders({ event: e, resolve: async () => response }) as Promise<Response>;
	}

	it('marks every answer under /bien, a redirect included, noindex and nofollow', async () => {
		for (const path of ['/bien', '/bien/12-rue-des-lilas-85000-la-roche-sur-yon']) {
			const res = await runAt(path, new Response(null, { status: 308, headers: { location: '/' } }));
			expect(res.headers.get('x-robots-tag'), path).toBe('noindex, nofollow');
		}
	});

	it('leaves the other pages without that header', async () => {
		for (const path of ['/', '/bienvenue', '/commune/44109']) {
			const res = await runAt(path, new Response('<html></html>'));
			expect(res.headers.get('x-robots-tag'), path).toBeNull();
		}
	});
});

// 04.7-10, decision 11 (threat T-10): a page whose data source failed is still rendered, but a robot
// must read « try again later », not index an « unavailable » page served as a 200. The load posts an
// internal header; the hook gives the answer its 503 and Retry-After, and never lets the header out.
describe('hooks.server outage page → 503 (04.7-10)', () => {
	const INTERNAL = [PAGE_STATE_HEADER, OUTAGE_RETRY_HEADER];
	const page = (headers: Record<string, string> = {}, init: ResponseInit = {}) =>
		new Response('<html>Les données de cette commune sont momentanément indisponibles</html>', {
			headers: { 'content-type': 'text/html', ...headers },
			...init
		});

	it('answers 503 with the served Retry-After, the body kept and no internal header', async () => {
		const res = await run(page({ [PAGE_STATE_HEADER]: 'panne', [OUTAGE_RETRY_HEADER]: '120' }));
		expect(res.status).toBe(503);
		expect(res.headers.get('retry-after')).toBe('120');
		for (const name of INTERNAL) expect(res.headers.has(name), name).toBe(false);
		expect(await res.text()).toContain('momentanément indisponibles');
		expect(res.headers.get('content-type')).toBe('text/html');
	});

	it('still carries the canonical security headers', async () => {
		const res = await run(page({ [PAGE_STATE_HEADER]: 'panne' }));
		expect(res.status).toBe(503);
		for (const [name, value] of Object.entries(SECURITY_HEADERS)) expect(res.headers.get(name)).toBe(value);
	});

	it('falls back on a default Retry-After when none, or an unusable one, was served', async () => {
		for (const servi of [undefined, '', 'abc', '0', '-5', '1.5', '12s']) {
			const res = await run(page({ [PAGE_STATE_HEADER]: 'panne', ...(servi === undefined ? {} : { [OUTAGE_RETRY_HEADER]: servi }) }));
			expect(res.status, String(servi)).toBe(503);
			expect(res.headers.get('retry-after'), String(servi)).toBe(String(DEFAULT_RETRY_AFTER_SECONDS));
		}
	});

	it('caps an extreme Retry-After instead of asking a robot to wait for years', async () => {
		const res = await run(page({ [PAGE_STATE_HEADER]: 'panne', [OUTAGE_RETRY_HEADER]: '99999999' }));
		expect(res.headers.get('retry-after')).toBe(String(MAX_RETRY_AFTER_SECONDS));
	});

	it('leaves the status of a normal page unchanged and the response untouched', async () => {
		const original = page();
		const res = await run(original);
		expect(res.status).toBe(200);
		expect(res).toBe(original);
		expect(res.headers.has('retry-after')).toBe(false);
	});

	it('never turns another state or another status into a 503, and still removes the internal header', async () => {
		const autre = await run(page({ [PAGE_STATE_HEADER]: 'autre', [OUTAGE_RETRY_HEADER]: '30' }));
		expect(autre.status).toBe(200);
		expect(autre.headers.has('retry-after')).toBe(false);
		for (const name of INTERNAL) expect(autre.headers.has(name), name).toBe(false);

		const introuvable = await run(page({ [PAGE_STATE_HEADER]: 'panne' }, { status: 404 }));
		expect(introuvable.status).toBe(404);
		expect(introuvable.headers.has('retry-after')).toBe(false);
		for (const name of INTERNAL) expect(introuvable.headers.has(name), name).toBe(false);

		const redirection = await run(new Response(null, { status: 307, headers: { location: '/', [PAGE_STATE_HEADER]: 'panne' } }));
		expect(redirection.status).toBe(307);
		expect(redirection.headers.has(PAGE_STATE_HEADER)).toBe(false);
	});

	it('is exported as one function, so that the department page uses the same mechanism', () => {
		expect(outageResponse(page({ [PAGE_STATE_HEADER]: 'panne' })).status).toBe(503);
	});
});

describe('hooks.server permanent redirects (SEO audit 2026-11-14)', () => {
	function at(pathname: string) {
		let resolved = false;
		const e = { url: new URL(`https://datacommune.fr${pathname}`) } as RequestEvent;
		const res = securityHeaders({
			event: e,
			resolve: async () => {
				resolved = true;
				return new Response('page');
			}
		}) as Promise<Response>;
		return res.then((r) => ({ res: r, resolved }));
	}

	it.each([
		['/guides/achat-immobilier', '/developpeurs'],
		['/docs', '/developpeurs'],
		['/playground', '/developpeurs'],
		['/reference/communes', '/developpeurs'],
		['/roadmap', '/couverture'],
		['/departement/2a', '/departement/2A'],
		['/commune/2b033', '/commune/2B033']
	])('answers %s with a 301 to %s, without resolving the route, headers still set', async (chemin, cible) => {
		const { res, resolved } = await at(chemin);
		expect(res.status).toBe(301);
		expect(res.headers.get('location')).toBe(cible);
		expect(resolved).toBe(false);
		expect(res.headers.get('X-Content-Type-Options')).toBe('nosniff');
	});

	it('lets the replacement and canonical pages through to their route', async () => {
		for (const chemin of ['/developpeurs', '/couverture', '/commune/44109', '/departement/2A']) {
			const { res, resolved } = await at(chemin);
			expect(resolved, chemin).toBe(true);
			expect(res.status, chemin).toBe(200);
		}
	});
});

describe('hooks.server handle', () => {
	it('is the bare header pass, with nothing wrapping the request path', () => {
		// Unconditional since the error-tracking SDK was removed: there is no
		// environment in which `handle` becomes a wrapping sequence. Identity, not
		// behaviour, is the assertion — it is what rougit if any middleware is
		// reintroduced around the request path without a decision.
		expect(handle).toBe(securityHeaders);
	});
});

describe('hooks.server relayDevApi (dev relay of the owner acceptance, 03-13)', () => {
	const page = { url: new URL('http://127.0.0.1:5173/commune/44109') } as RequestEvent;

	async function relayed(target: string | undefined, input: string, init?: RequestInit) {
		const seen: Request[] = [];
		const fetch = (async (request: Request) => {
			seen.push(request);
			return new Response('ok');
		}) as typeof globalThis.fetch;
		await relayDevApi(target)({ event: page, request: new Request(input, init), fetch });
		return seen;
	}

	it('sends a server-side fetch of the page origin under /v1 to the relay target, key kept', async () => {
		const [request] = await relayed('http://api.test:8000', 'http://127.0.0.1:5173/v1/communes/44109?x=1', {
			headers: { 'X-API-Key': 'dc_anon_unit' }
		});
		expect(request.url).toBe('http://api.test:8000/v1/communes/44109?x=1');
		expect(request.headers.get('X-API-Key')).toBe('dc_anon_unit');
	});

	it('relays /auth and /billing the same way, bodies included', async () => {
		const [auth] = await relayed('http://api.test:8000', 'http://127.0.0.1:5173/auth/me');
		expect(auth.url).toBe('http://api.test:8000/auth/me');
		const [billing] = await relayed('http://api.test:8000', 'http://127.0.0.1:5173/billing/x', { method: 'POST', body: 'b' });
		expect(billing.url).toBe('http://api.test:8000/billing/x');
		expect(await billing.text()).toBe('b');
	});

	it('leaves the request untouched without a target, on another origin or another path', async () => {
		for (const [target, input] of [
			[undefined, 'http://127.0.0.1:5173/v1/communes/44109'],
			['http://api.test:8000', 'http://elsewhere.test/v1/communes/44109'],
			['http://api.test:8000', 'http://127.0.0.1:5173/v1x'],
			['http://api.test:8000', 'http://127.0.0.1:5173/commune/44109']
		] as const) {
			const [request] = await relayed(target, input);
			expect(request.url).toBe(input);
		}
	});
});

describe('hooks.server internalApiFetch (visitor address on server-side API calls)', () => {
	const event = {
		request: new Request('https://datacommune.fr/commune/44109'),
		getClientAddress: () => '203.0.113.7'
	} as unknown as RequestEvent;

	async function run(internalUrl: string | undefined, url: string) {
		const seen: { url: string; headers: Headers }[] = [];
		const fetch = (async (input: Request | string, init?: RequestInit) => {
			const headers = new Headers(input instanceof Request ? input.headers : undefined);
			new Headers(init?.headers).forEach((value, name) => headers.set(name, value));
			seen.push({ url: input instanceof Request ? input.url : input, headers });
			return new Response('ok');
		}) as unknown as typeof globalThis.fetch;
		await internalApiFetch(internalUrl)({ event, request: new Request(url, { headers: { 'X-API-Key': 'k' } }), fetch });
		return seen[0];
	}

	it('sends the API call to the internal URL with the client address', async () => {
		const seen = await run('http://api:8000', `${API_URL}/v1/communes/44109`);
		expect(seen.url).toBe('http://api:8000/v1/communes/44109');
		expect(seen.headers.get('cf-connecting-ip')).toBe('203.0.113.7');
		expect(seen.headers.get('x-api-key')).toBe('k');
	});

	it('is a no-op without API_INTERNAL_URL and for other hosts', async () => {
		expect((await run(undefined, `${API_URL}/v1/x`)).url).toBe(`${API_URL}/v1/x`);
		const other = await run('http://api:8000', 'https://example.org/v1/x');
		expect(other.url).toBe('https://example.org/v1/x');
		expect(other.headers.get('cf-connecting-ip')).toBeNull();
	});
});

// 04.7-10: during server rendering SvelteKit refuses to let a universal load read a response header
// that `filterSerializedResponseHeaders` does not list (it throws « Failed to get response header »).
// `api()` reads the `Retry-After` of a refusal: without it a 429 or a 503 of the API was no longer an
// `ApiError` in the SSR of the pages, its served delay lost (and the generic outage shown for a rate
// limit). Only that header is listed: it is serialised into the page for hydration, nothing else is.
describe('hooks.server filterSerializedResponseHeaders (04.7-10)', () => {
	async function optionsPassees() {
		let options: { filterSerializedResponseHeaders?: (name: string, value: string) => boolean } | undefined;
		const resolve = (async (_event: RequestEvent, opts?: typeof options) => {
			options = opts;
			return new Response('<html></html>');
		}) as unknown as Parameters<typeof securityHeaders>[0]['resolve'];
		await securityHeaders({ event, resolve });
		return options;
	}

	it('lets a universal load read Retry-After and nothing else', async () => {
		const options = await optionsPassees();
		expect(options?.filterSerializedResponseHeaders).toBeTypeOf('function');
		const filtre = options!.filterSerializedResponseHeaders!;
		expect(filtre('retry-after', '45')).toBe(true);
		for (const nom of ['content-type', 'set-cookie', 'x-api-key', 'authorization', 'cache-control', 'x-ratelimit-remaining']) {
			expect(filtre(nom, 'x'), nom).toBe(false);
		}
	});
});
