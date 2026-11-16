import { describe, it, expect, vi, beforeEach } from 'vitest';
import { api, ApiError } from './client';

beforeEach(() => {
	vi.restoreAllMocks();
});

describe('api()', () => {
	it('preserves the address clarification title separately from its explanation', async () => {
		const fetchImpl = vi.fn().mockResolvedValue(
			new Response(JSON.stringify({
				title: 'Adresse à préciser',
				detail: 'Sélectionnez une adresse complète.',
				status: 422
			}), { status: 422 })
		);
		await expect(api('/v1/resolve', {}, fetchImpl)).rejects.toMatchObject({
			status: 422,
			detail: 'Sélectionnez une adresse complète.',
			problemTitle: 'Adresse à préciser'
		});
	});

	it('returns parsed JSON on success', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: true,
				status: 200,
				json: () => Promise.resolve({ donnees: [1, 2, 3] })
			})
		);

		const result = await api<{ donnees: number[] }>('/v1/test');
		expect(result).toEqual({ donnees: [1, 2, 3] });
		expect(fetch).toHaveBeenCalledWith(
			expect.stringContaining('/v1/test'),
			expect.objectContaining({ credentials: 'include' })
		);
	});

	it('throws ApiError with detail on failure', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: false,
				status: 401,
				statusText: 'Unauthorized',
				json: () => Promise.resolve({ detail: 'Token invalide' })
			})
		);

		await expect(api('/auth/me')).rejects.toThrow(ApiError);
		try {
			await api('/auth/me');
		} catch (e) {
			const err = e as ApiError;
			expect(err.status).toBe(401);
			expect(err.detail).toBe('Token invalide');
		}
	});

	it('throws ApiError with erreur field', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: false,
				status: 429,
				statusText: 'Too Many Requests',
				json: () => Promise.resolve({ erreur: 'Quota dépassé' })
			})
		);

		await expect(api('/v1/dvf')).rejects.toThrow('Quota dépassé');
	});

	it('flags an anonymous per-IP 429 (Retry-After present) with the D-11 message', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: false,
				status: 429,
				statusText: 'Too Many Requests',
				headers: new Headers({ 'Retry-After': '30' }),
				json: () => Promise.resolve({ erreur: 'Rate limit dépassé' })
			})
		);

		try {
			await api('/v1/dvf');
			expect.fail('expected ApiError');
		} catch (e) {
			const err = e as ApiError;
			expect(err.status).toBe(429);
			expect(err.isAnonymous429).toBe(true);
			expect(err.detail).toContain('clé publique partagée');
			expect(err.retryAfter).toBe(30);
			expect(err.anonyme).toBe(true);
		}
	});

	it('a 429 without Retry-After keeps its problem title and no delay (03-17 G1)', async () => {
		const fetchImpl = vi.fn().mockResolvedValue(
			new Response(JSON.stringify({ title: 'Quota mensuel epuise', status: 429 }), { status: 429 })
		);
		await expect(api('/v1/dvf', {}, fetchImpl)).rejects.toMatchObject({
			status: 429,
			isAnonymous429: false,
			problemTitle: 'Quota mensuel epuise',
			retryAfter: null,
			anonyme: true
		});
	});

	it('a Retry-After that is not a number of seconds is not read as a delay', async () => {
		const fetchImpl = vi.fn().mockResolvedValue(
			new Response('{}', { status: 429, headers: { 'Retry-After': 'Wed, 21 Oct 2026 07:28:00 GMT' } })
		);
		await expect(api('/v1/dvf', {}, fetchImpl)).rejects.toMatchObject({ status: 429, retryAfter: null });
	});

	it('throws ApiError with Problem Details title', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: false,
				status: 403,
				statusText: 'Forbidden',
				json: () => Promise.resolve({ title: 'Email non verifie', status: 403 })
			})
		);

		await expect(api('/auth/me')).rejects.toThrow('Email non verifie');
	});

	it('returns undefined for 204 No Content', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: true,
				status: 204,
				json: () => Promise.reject(new Error('no body'))
			})
		);

		const result = await api('/auth/logout');
		expect(result).toBeUndefined();
	});

	it('sends Content-Type json by default', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: true,
				status: 200,
				json: () => Promise.resolve({})
			})
		);

		await api('/test', { method: 'POST', body: '{}' });
		const callArgs = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
		expect(callArgs[1].headers['Content-Type']).toBe('application/json');
	});
});
