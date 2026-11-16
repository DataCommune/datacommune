import { test, expect, type Page, type ConsoleMessage } from '@playwright/test';

// Exploration suite — hunt for bugs across the full frontend surface.
// Philosophy: visit everything, assert minimal invariants, capture console/network errors.

const MARKETING_ROUTES = [
	'/',
	'/datasets',
	'/cgu',
	'/confidentialite',
	'/mentions-legales'
];

const AUTH_ROUTES = ['/login', '/register', '/forgot-password'];

// /workspace and /explore removed in 09-05 (legacy) — now 404 no-redirect (ADR D-09),
// covered by shell-legacy.spec.ts. Remaining (app) guarded routes redirect anonymous users.
const PROTECTED_ROUTES = ['/settings', '/billing', '/admin'];

const INVALID_ROUTES = [
	'/does-not-exist',
	'/datasets/not-a-real-dataset',
	'/reset-password?token=invalid',
	'/verify?token=bogus'
];

function captureConsole(page: Page): { errors: string[]; warnings: string[] } {
	const errors: string[] = [];
	const warnings: string[] = [];
	page.on('console', (msg: ConsoleMessage) => {
		const txt = msg.text();
		// Ignore noisy third-party / dev HMR warnings
		if (/\[vite\]|HMR|DevTools|ServiceWorker/.test(txt)) return;
		if (msg.type() === 'error') errors.push(txt);
		if (msg.type() === 'warning') warnings.push(txt);
	});
	page.on('pageerror', (err) => errors.push(`pageerror: ${err.message}`));
	return { errors, warnings };
}

test.describe('Marketing — all public pages load', () => {
	for (const route of MARKETING_ROUTES) {
		test(`loads ${route} without console errors`, async ({ page }) => {
			const { errors } = captureConsole(page);
			const response = await page.goto(route);
			expect(response?.status(), `HTTP status for ${route}`).toBeLessThan(400);
			await expect(page.locator('body')).toBeVisible();
			// No fatal JS errors
			expect(errors, `Console errors on ${route}: ${errors.join(' | ')}`).toHaveLength(0);
		});
	}
});

test.describe('Marketing — French accents are present', () => {
	test('homepage contains proper French accents', async ({ page }) => {
		await page.goto('/');
		await page.waitForLoadState('networkidle');
		await expect(page.locator('h1')).toBeVisible();
		const body = await page.locator('main, body').first().innerText();
		expect(body).toBeTruthy();
		expect(body).toMatch(/[éèêàôùûç]/);
	});

	test('datasets page has accented descriptions', async ({ page }) => {
		await page.goto('/datasets');
		await page.waitForLoadState('networkidle');
		await expect(page.locator('h1, h2').first()).toBeVisible();
		const body = await page.locator('main, body').first().innerText();
		expect(body).toMatch(/[éèêàôùûç]/);
	});
});

test.describe('Auth — form validation', () => {
	for (const route of AUTH_ROUTES) {
		test(`${route} renders and has no console errors`, async ({ page }) => {
			const { errors } = captureConsole(page);
			await page.goto(route);
			await expect(page.locator('form')).toBeVisible();
			expect(errors, `Console errors on ${route}: ${errors.join(' | ')}`).toHaveLength(0);
		});
	}

	test('register rejects invalid email format', async ({ page }) => {
		await page.goto('/register');
		const emailInput = page.locator('input[type="email"]').first();
		await emailInput.fill('not-an-email');
		const submit = page.getByRole('button', { name: /inscrire|créer|s'inscrire/i }).first();
		await submit.click();
		// Browser native validation OR app error — at minimum should not navigate away to /chat etc.
		await expect(page).toHaveURL(/\/register/);
	});

	test('register with empty form does not submit', async ({ page }) => {
		await page.goto('/register');
		const submit = page.getByRole('button', { name: /inscrire|créer|s'inscrire/i }).first();
		await submit.click();
		await expect(page).toHaveURL(/\/register/);
	});

	test('login with empty password does not submit', async ({ page }) => {
		await page.goto('/login');
		await page.getByPlaceholder('votre@email.com').fill('test@example.com');
		await page.getByRole('button', { name: 'Se connecter' }).click();
		await expect(page).toHaveURL(/\/login/);
	});
});

test.describe('Protected routes — redirect when unauthenticated', () => {
	for (const route of PROTECTED_ROUTES) {
		test(`${route} redirects or blocks anonymous access`, async ({ page }) => {
			await page.goto(route);
			await page.waitForLoadState('networkidle');
			// Give the client guard up to 3s to redirect or render login prompt
			await page.waitForFunction(
				() => /\/login/.test(location.pathname) || !!document.querySelector('input[type="password"]') || /connexion|non autoris|se connecter/i.test(document.body.innerText),
				undefined,
				{ timeout: 6000 }
			).catch(() => {});
			const url = page.url();
			const redirectedToLogin = /\/login/.test(url);
			const showsLoginForm = await page.locator('input[type="password"]').count() > 0;
			const showsAuthRequired = await page.getByText(/connexion|non autorisé|se connecter/i).count() > 0;
			expect(
				redirectedToLogin || showsLoginForm || showsAuthRequired,
				`Route ${route} exposed to anonymous user (url: ${url})`
			).toBe(true);
		});
	}
});

test.describe('404 / invalid routes — graceful handling', () => {
	for (const route of INVALID_ROUTES) {
		test(`${route} does not crash page`, async ({ page }) => {
			const { errors } = captureConsole(page);
			await page.goto(route);
			await expect(page.locator('body')).toBeVisible();
			// Should not throw uncaught JS errors even on 404
			const fatalErrors = errors.filter((e) => !/404|not found/i.test(e));
			expect(fatalErrors, `Fatal errors on ${route}: ${fatalErrors.join(' | ')}`).toHaveLength(0);
		});
	}
});

test.describe('Responsive — mobile viewport', () => {
	test.use({ viewport: { width: 375, height: 667 } });

	test('homepage renders on iPhone SE viewport', async ({ page }) => {
		await page.goto('/');
		await expect(page.locator('h1')).toBeVisible();
		// No horizontal scroll
		const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
		expect(bodyWidth, 'body wider than viewport (horizontal scroll)').toBeLessThanOrEqual(376);
	});

	test('datasets page renders on mobile', async ({ page }) => {
		await page.goto('/datasets');
		await expect(page.locator('body')).toBeVisible();
		const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
		expect(bodyWidth).toBeLessThanOrEqual(376);
	});

	test('login page renders on mobile', async ({ page }) => {
		await page.goto('/login');
		await expect(page.getByPlaceholder('votre@email.com')).toBeVisible();
		const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
		expect(bodyWidth).toBeLessThanOrEqual(376);
	});
});

test.describe('Network — broken resources', () => {
	test('homepage loads all linked assets (no 4xx/5xx)', async ({ page }) => {
		const failed: string[] = [];
		page.on('response', (resp) => {
			const status = resp.status();
			if (status >= 400 && !resp.url().includes('favicon')) {
				failed.push(`${status} ${resp.url()}`);
			}
		});
		await page.goto('/');
		await page.waitForLoadState('networkidle');
		expect(failed, `Broken resources on /: ${failed.join(' | ')}`).toHaveLength(0);
	});
});

test.describe('Fuzzing — SearchBar / inputs', () => {
	test('datasets detail handles XSS-like slug', async ({ page }) => {
		await page.goto('/datasets/%3Cscript%3Ealert(1)%3C%2Fscript%3E');
		// Page must not execute script — if it does, dialog handler would fire
		let alertFired = false;
		page.on('dialog', async (d) => {
			alertFired = true;
			await d.dismiss();
		});
		await expect(page.locator('body')).toBeVisible();
		expect(alertFired, 'XSS fired via URL slug').toBe(false);
	});

	test('very long URL param does not crash', async ({ page }) => {
		const long = 'a'.repeat(5000);
		await page.goto(`/datasets?q=${long}`);
		await expect(page.locator('body')).toBeVisible();
	});
});
