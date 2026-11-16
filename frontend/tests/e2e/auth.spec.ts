import { test, expect } from '@playwright/test';

test.describe('Auth — Register page', () => {
	test('register page renders form', async ({ page }) => {
		await page.goto('/register');
		await expect(page).toHaveTitle('Inscription — DataCommune');
		await expect(page.getByRole('heading', { name: 'Créer un compte' })).toBeVisible();
		await expect(page.getByPlaceholder('votre@email.com')).toBeVisible();
		await expect(page.getByPlaceholder('8 caractères minimum')).toBeVisible();
		await expect(page.getByRole('button', { name: "S'inscrire" })).toBeVisible();
		await expect(page.getByRole('link', { name: 'Se connecter' })).toBeVisible();
	});

	test('register shows validation error on password mismatch', async ({ page }) => {
		await page.goto('/register');
		await page.getByPlaceholder('votre@email.com').fill('test@example.com');
		await page.getByPlaceholder('8 caractères minimum').fill('password123');
		// Confirm field has different placeholder
		const confirmInput = page.locator('input[type="password"]').nth(1);
		await confirmInput.fill('different456');
		await page.getByRole('button', { name: "S'inscrire" }).click();

		// Should show mismatch error
		await expect(page.getByText('Les mots de passe ne correspondent pas')).toBeVisible({ timeout: 5000 });
	});

	test('register navigates to login', async ({ page }) => {
		await page.goto('/register');
		await page.getByRole('link', { name: 'Se connecter' }).click();
		await expect(page).toHaveURL(/\/login/);
	});
});

test.describe('Auth — Forgot password page', () => {
	test('forgot-password page renders form', async ({ page }) => {
		await page.goto('/forgot-password');
		await expect(page).toHaveTitle('Mot de passe oublié — DataCommune');
		await expect(page.getByRole('heading', { name: 'Mot de passe oublié' })).toBeVisible();
		await expect(page.getByPlaceholder('votre@email.com')).toBeVisible();
		await expect(page.getByRole('button', { name: 'Envoyer le lien' })).toBeVisible();
		await expect(page.getByRole('link', { name: /connexion/i })).toBeVisible();
	});

	test('forgot-password shows confirmation after submit', async ({ page }) => {
		// Mock the API to return success
		await page.route('**/auth/forgot-password', (route) =>
			route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'ok' }) })
		);

		await page.goto('/forgot-password');
		await page.getByPlaceholder('votre@email.com').fill('test@example.com');
		await page.getByRole('button', { name: 'Envoyer le lien' }).click();

		await expect(page.getByText(/email envoyé/i)).toBeVisible({ timeout: 5000 });
	});
});

test.describe('Auth — Reset password page', () => {
	test('reset-password page renders form with token', async ({ page }) => {
		await page.goto('/reset-password?token=fake-token');
		await expect(page).toHaveTitle('Nouveau mot de passe — DataCommune');
		await expect(page.getByRole('heading', { name: 'Nouveau mot de passe' })).toBeVisible();
		await expect(page.getByPlaceholder('8 caractères minimum')).toBeVisible();
		await expect(page.getByRole('button', { name: /réinitialiser/i })).toBeVisible();
	});
});

test.describe('Auth — Verify page', () => {
	test('verify page shows success on valid token', async ({ page }) => {
		await page.route('**/auth/verify*', (route) =>
			route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'ok' }) })
		);

		await page.goto('/verify?token=valid-token');
		await expect(page.getByText(/email vérifié/i)).toBeVisible({ timeout: 10000 });
		await expect(page.getByRole('link', { name: /connecter/i })).toBeVisible();
	});

	test('verify page shows error on invalid token', async ({ page }) => {
		await page.route('**/auth/verify*', (route) =>
			route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ detail: 'Invalid token' }) })
		);

		await page.goto('/verify?token=bad-token');
		await expect(page.getByText(/invalide|expiré/i)).toBeVisible({ timeout: 10000 });
	});
});

test.describe('Auth — Protected route redirects', () => {
	// NOTE: /explore and /workspace are gone (legacy removed in 09-05). They are no longer
	// protected routes — they now render a 404 no-redirect (ADR D-09), asserted in
	// shell-legacy.spec.ts. Only /settings remains an (app) guarded route here.
	test('unauthenticated user is redirected from /settings', async ({ page }) => {
		await page.route('**/auth/me', (route) =>
			route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ detail: 'Not authenticated' }) })
		);
		await page.goto('/settings');
		await expect(page).toHaveURL(/\/login/, { timeout: 10000 });
	});
});
