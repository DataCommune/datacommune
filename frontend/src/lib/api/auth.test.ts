import { describe, it, expect, vi, beforeEach } from 'vitest';
import { login, register, logout, getMe, forgotPassword, resetPassword } from './auth';

beforeEach(() => {
	vi.restoreAllMocks();
});

function mockFetch(response: unknown, status = 200) {
	vi.stubGlobal(
		'fetch',
		vi.fn().mockResolvedValue({
			ok: status >= 200 && status < 300,
			status,
			statusText: 'OK',
			json: () => Promise.resolve(response)
		})
	);
}

describe('auth API functions', () => {
	it('login sends email and password, returns token', async () => {
		mockFetch({ access_token: 'jwt123', token_type: 'bearer' });

		const result = await login('test@example.com', 'password123');

		expect(result.access_token).toBe('jwt123');
		expect(result.token_type).toBe('bearer');

		const call = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
		expect(call[0]).toContain('/auth/login');
		expect(JSON.parse(call[1].body)).toEqual({
			email: 'test@example.com',
			password: 'password123'
		});
	});

	it('register sends POST to /auth/register', async () => {
		mockFetch(null, 202);
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
			ok: true,
			status: 202,
			json: () => Promise.resolve(null)
		}));

		await register('new@example.com', 'securepass');

		const call = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
		expect(call[0]).toContain('/auth/register');
		expect(JSON.parse(call[1].body)).toEqual({
			email: 'new@example.com',
			password: 'securepass'
		});
	});

	it('logout sends POST to /auth/logout', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
			ok: true,
			status: 204,
			json: () => Promise.reject()
		}));

		await logout();

		const call = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
		expect(call[0]).toContain('/auth/logout');
		expect(call[1].method).toBe('POST');
	});

	it('getMe returns user info', async () => {
		const user = {
			id: 1,
			email: 'test@example.com',
			name: 'Test',
			is_verified: true,
			created_at: '2026-01-01T00:00:00Z'
		};
		mockFetch(user);

		const result = await getMe();

		expect(result.id).toBe(1);
		expect(result.email).toBe('test@example.com');
	});

	it('forgotPassword sends email', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
			ok: true,
			status: 202,
			json: () => Promise.resolve(null)
		}));

		await forgotPassword('user@example.com');

		const call = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
		expect(call[0]).toContain('/auth/forgot-password');
		expect(JSON.parse(call[1].body)).toEqual({ email: 'user@example.com' });
	});

	it('resetPassword sends token and new password', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
			ok: true,
			status: 204,
			json: () => Promise.reject()
		}));

		await resetPassword('reset-token-123', 'newpassword');

		const call = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
		expect(call[0]).toContain('/auth/reset-password');
		expect(JSON.parse(call[1].body)).toEqual({
			token: 'reset-token-123',
			new_password: 'newpassword'
		});
	});
});
