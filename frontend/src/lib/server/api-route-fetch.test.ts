import { describe, expect, it, vi } from 'vitest';
import { creerFetchApiRoute, ipVisiteur, versApiInterne } from './api-route-fetch';

const API = 'https://api.datacommune.fr';
const INTERNE = 'http://api:8000';

function recorder() {
	const calls: { input: RequestInfo | URL; headers: Headers }[] = [];
	const fetchDirect = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
		calls.push({ input, headers: new Headers(init?.headers) });
		return new Response('{}');
	}) as unknown as typeof fetch;
	return { calls, fetchDirect };
}

describe('ipVisiteur', () => {
	it('prefers the socket address (ADDRESS_HEADER in production) over the header', () => {
		const request = new Request('http://x/', { headers: { 'cf-connecting-ip': '6.6.6.6' } });
		expect(ipVisiteur(request, () => '203.0.113.7')).toBe('203.0.113.7');
	});

	it('falls back to cf-connecting-ip, then to null', () => {
		const withHeader = new Request('http://x/', { headers: { 'cf-connecting-ip': ' 203.0.113.7 ' } });
		const throwing = () => {
			throw new Error('no address');
		};
		expect(ipVisiteur(withHeader, throwing)).toBe('203.0.113.7');
		expect(ipVisiteur(new Request('http://x/'), throwing)).toBeNull();
		expect(ipVisiteur(new Request('http://x/'))).toBeNull();
	});
});

describe('versApiInterne', () => {
	it('rewrites a call to the public API URL to the internal one and sends the visitor address', async () => {
		const { calls, fetchDirect } = recorder();
		await versApiInterne(fetchDirect, { apiUrl: API, internalUrl: INTERNE, ip: '203.0.113.7' })(
			`${API}/v1/communes?q=nantes`,
			{ headers: { 'X-API-Key': 'dc_anon_unit', 'cf-connecting-ip': '6.6.6.6', 'x-forwarded-for': '6.6.6.6' } }
		);
		expect(calls[0].input).toBe(`${INTERNE}/v1/communes?q=nantes`);
		expect(calls[0].headers.get('cf-connecting-ip')).toBe('203.0.113.7');
		expect(calls[0].headers.get('x-forwarded-for')).toBe('203.0.113.7');
		expect(calls[0].headers.get('x-api-key')).toBe('dc_anon_unit');
	});

	it('rewrites a Request input too', async () => {
		const { calls, fetchDirect } = recorder();
		await versApiInterne(fetchDirect, { apiUrl: API, internalUrl: `${INTERNE}/`, ip: '203.0.113.7' })(
			new Request(`${API}/v1/datasets`, { headers: { 'X-API-Key': 'k' } })
		);
		expect(calls[0].input).toBe(`${INTERNE}/v1/datasets`);
		expect(calls[0].headers.get('x-api-key')).toBe('k');
		expect(calls[0].headers.get('cf-connecting-ip')).toBe('203.0.113.7');
	});

	it('touches nothing when API_INTERNAL_URL is unset', () => {
		const { fetchDirect } = recorder();
		expect(versApiInterne(fetchDirect, { apiUrl: API, internalUrl: undefined, ip: '203.0.113.7' })).toBe(fetchDirect);
		expect(versApiInterne(fetchDirect, { apiUrl: API, internalUrl: '  ', ip: '203.0.113.7' })).toBe(fetchDirect);
	});

	it('leaves other URLs untouched, look-alike hosts included', async () => {
		const { calls, fetchDirect } = recorder();
		const f = versApiInterne(fetchDirect, { apiUrl: API, internalUrl: INTERNE, ip: '203.0.113.7' });
		await f('https://example.org/x');
		await f(`${API}.evil.test/v1/x`);
		expect(calls.map((c) => c.input)).toEqual(['https://example.org/x', `${API}.evil.test/v1/x`]);
		expect(calls[0].headers.get('cf-connecting-ip')).toBeNull();
	});

	it('goes through the cookie policy of the route without losing the address', async () => {
		const { calls, fetchDirect } = recorder();
		const f = versApiInterne(creerFetchApiRoute(fetchDirect, 'access_token=abc; other=1'), {
			apiUrl: API,
			internalUrl: INTERNE,
			ip: '203.0.113.7'
		});
		await f(`${API}/v1/x`, { headers: { 'X-API-Key': 'k' } });
		expect(calls[0].input).toBe(`${INTERNE}/v1/x`);
		expect(calls[0].headers.get('cf-connecting-ip')).toBe('203.0.113.7');
		expect(calls[0].headers.get('cookie')).toBe('access_token=abc');
	});
});
