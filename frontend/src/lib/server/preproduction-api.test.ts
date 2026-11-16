import { describe, expect, it, vi } from 'vitest';
import {
	createPreproductionFetch,
	proxyPreproductionRequest,
	selectAuthCookie
} from './preproduction-api';

const env = { CF_ACCESS_CLIENT_ID: 'server-id', CF_ACCESS_CLIENT_SECRET: 'server-secret' };

describe('preproduction API isolation', () => {
	it('selects only the dev cookie when prod and dev coexist', () => {
		expect(selectAuthCookie('access_token=prod; dev_access_token=dev; theme=x')).toBe(
			'dev_access_token=dev'
		);
	});

	it('fails closed when Access secrets are absent without exposing a value', () => {
		try {
			createPreproductionFetch(vi.fn(), {}, null);
			expect.fail('missing secrets should fail closed');
		} catch (cause) {
			expect(cause).toMatchObject({ status: 503 });
			expect(JSON.stringify(cause)).not.toContain('server-secret');
		}
	});

	it('uses the fixed dev upstream and replaces browser Access headers', async () => {
		const direct = vi.fn().mockResolvedValue(new Response('{}'));
		const secured = createPreproductionFetch(
			direct,
			env,
			'access_token=prod; dev_access_token=dev'
		);
		await secured('/__api/v1/health', {
			headers: {
				'CF-Access-Client-Id': 'attacker',
				'CF-Access-Client-Secret': 'attacker',
				'X-API-Key': 'dc_anon_browser'
			}
		});
		expect(String(direct.mock.calls[0][0])).toBe('https://dev-api.datacommune.fr/v1/health');
		const headers = new Headers(direct.mock.calls[0][1].headers);
		expect(headers.get('cookie')).toBe('dev_access_token=dev');
		expect(headers.get('cf-access-client-id')).toBe('server-id');
		expect(headers.get('cf-access-client-secret')).toBe('server-secret');
		expect(headers.get('x-api-key')).toBe('dc_anon_browser');
	});

	it('is disabled in production and rejects unsafe origin and traversal', async () => {
		const base = new Request('https://dev.datacommune.fr/__api/auth/logout', {
			method: 'POST',
			headers: { origin: 'https://evil.example' }
		});
		await expect(
			proxyPreproductionRequest({ request: base, path: 'auth/logout', env, preproduction: false })
		).rejects.toMatchObject({ status: 404 });
		await expect(
			proxyPreproductionRequest({ request: base, path: 'auth/logout', env, preproduction: true })
		).rejects.toMatchObject({ status: 403 });
		await expect(
			proxyPreproductionRequest({
				request: new Request('https://dev.datacommune.fr/__api/x'),
				path: 'v1/%2e%2e/auth',
				env,
				preproduction: true
			})
		).rejects.toMatchObject({ status: 404 });
	});

	it('rewrites only the dev Set-Cookie as host-only and never returns Access secrets', async () => {
		const upstreamHeaders = new Headers({ 'content-type': 'application/json' });
		upstreamHeaders.append(
			'set-cookie',
			'dev_access_token=fresh; Path=/; Domain=.datacommune.fr; HttpOnly; Secure; SameSite=Lax'
		);
		const direct = vi.fn().mockResolvedValue(
			new Response('{}', { status: 200, headers: upstreamHeaders })
		);
		const response = await proxyPreproductionRequest({
			request: new Request('https://dev.datacommune.fr/__api/auth/login', {
				method: 'POST',
				headers: { origin: 'https://dev.datacommune.fr' },
				body: '{}'
			}),
			path: 'auth/login',
			env,
			fetchDirect: direct,
			preproduction: true
		});
		const cookie = response.headers.get('set-cookie') ?? '';
		expect(cookie).toContain('dev_access_token=fresh');
		expect(cookie).not.toMatch(/domain=/i);
		expect([...response.headers.values()].join(' ')).not.toContain('server-secret');
	});
});
