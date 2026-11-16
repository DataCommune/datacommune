import { afterEach, describe, expect, it, vi } from 'vitest';

describe('deployment configuration', () => {
	afterEach(() => {
		vi.unstubAllEnvs();
		vi.resetModules();
	});

	it('preserves production defaults', async () => {
		vi.stubEnv('VITE_DEPLOYMENT_ENV', '');
		vi.stubEnv('VITE_DEPLOYMENT_SHA', '');
		const config = await import('./config');
		expect(config.DEPLOYMENT_ENV).toBe('production');
		expect(config.DEPLOYMENT_SHA).toBe('unknown');
		expect(config.SITE_URL).toBe('https://datacommune.fr');
		expect(config.IS_PREPRODUCTION).toBe(false);
	});

	it('selects the isolated preproduction site', async () => {
		vi.stubEnv('VITE_DEPLOYMENT_ENV', 'preproduction');
		vi.stubEnv('VITE_DEPLOYMENT_SHA', 'abcdef123456');
		const config = await import('./config');
		expect(config.SITE_URL).toBe('https://dev.datacommune.fr');
		expect(config.DEPLOYMENT_SHA.slice(0, 7)).toBe('abcdef1');
		expect(config.IS_PREPRODUCTION).toBe(true);
	});

	it('enables the account space by default', async () => {
		vi.stubEnv('VITE_ACCOUNTS_ENABLED', '');
		const config = await import('./config');
		expect(config.ACCOUNTS_ENABLED).toBe(true);
	});

	it('disables the account space only on an explicit "false"', async () => {
		vi.stubEnv('VITE_ACCOUNTS_ENABLED', ' False ');
		expect((await import('./config')).ACCOUNTS_ENABLED).toBe(false);
		vi.resetModules();
		vi.stubEnv('VITE_ACCOUNTS_ENABLED', 'true');
		expect((await import('./config')).ACCOUNTS_ENABLED).toBe(true);
	});
});
