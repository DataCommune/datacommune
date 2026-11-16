import { test, expect } from '@playwright/test';
import { COMMUNE_85191_OBSERVED, COMMUNE_85191_SOURCES } from '../../src/lib/data/__fixtures__/commune-85191-20261012';
import { configureSsrSource } from './fixtures/bien-ssr-source';

const MOBILE = { width: 390, height: 844 };

// NOTE: the legacy "Mobile responsive — workspace" describe block was removed with the
// /workspace route (09-05). Mobile coverage of the v3.0 surface lives in shell-legacy.spec.ts
// (hub) and fiche-commune.spec.ts. Only the auth-free mobile checks remain here.

test.describe('Mobile responsive — no auth required', () => {
	test('login page renders without console errors on mobile', async ({ page }) => {
		const errors: string[] = [];
		page.on('pageerror', (err) => errors.push(err.message));

		await page.setViewportSize(MOBILE);
		await page.goto('/login');
		await page.waitForLoadState('networkidle');

		// No Svelte infinite loop errors
		const hasInfiniteLoop = errors.some((e) => e.includes('effect_update_depth_exceeded'));
		expect(hasInfiniteLoop).toBe(false);

		// Form elements visible and usable
		await expect(page.getByPlaceholder('votre@email.com')).toBeVisible();
		await expect(page.getByPlaceholder('••••••••')).toBeVisible();
		await expect(page.getByRole('button', { name: 'Se connecter' })).toBeVisible();
	});

	test('login page layout fits mobile viewport (no horizontal overflow)', async ({ page }) => {
		await page.setViewportSize(MOBILE);
		await page.goto('/login');
		await page.waitForLoadState('networkidle');

		const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
		expect(bodyWidth).toBeLessThanOrEqual(MOBILE.width + 1); // +1 for rounding
	});

	test('register page renders on mobile without errors', async ({ page }) => {
		const errors: string[] = [];
		page.on('pageerror', (err) => errors.push(err.message));

		await page.setViewportSize(MOBILE);
		await page.goto('/register');
		await page.waitForLoadState('networkidle');

		const hasInfiniteLoop = errors.some((e) => e.includes('effect_update_depth_exceeded'));
		expect(hasInfiniteLoop).toBe(false);
	});
});

// 4.9, A1 (replaces UI-004, R2 « garder-quatre »): on a phone, the trend that this site does not publish
// takes no tile; on the frozen 85191 answer, at 375 × 812, its state is on its row of prices and its
// cause in the panel, opened at the keyboard.
// `TENDANCE_NON_PUBLIEE` of commune-kpi.ts, whose `$lib` imports the e2e runner does not resolve.
const TENDANCE_NON_PUBLIEE = "Ce site ne publie pas l'évolution des prix pour cette commune.";

test.describe('Mobile responsive — tendance non publiée (4.9, A1)', () => {
	test('375 px : pas de tuile, état sur sa ligne, cause dans le volet', async ({ page }) => {
		await page.route('**/auth/me', (route) => route.fulfill({ status: 401, contentType: 'application/json', body: '{"detail":"Not authenticated"}' }));
		const source = await configureSsrSource(page.request, [{
			method: 'GET', pathname: '/v1/commune', query: { code_insee: '85191' }, minCalls: 1, maxCalls: 1,
			responses: [{ status: 200, body: { statut: 'ok', donnees: [COMMUNE_85191_OBSERVED], meta: { source: COMMUNE_85191_SOURCES } } }]
		}]);
		await page.setViewportSize({ width: 375, height: 812 });
		const hydrated = page.waitForResponse((response) => response.url().endsWith('/auth/me'));
		await page.goto('/commune/85191');
		await hydrated;

		await expect(page.locator('[data-kpi="tendance"]')).toHaveCount(0);
		// Under 1081 px the category of prices is a closed fold: the reader opens it to reach its row.
		await page.locator('details.cat#q-prix > summary').click();
		const ligne = page.locator('#critere-prix_commune [data-ligne="dvf-tendance"]');
		await expect(ligne.locator('.tab-v .state-badge')).toHaveText('Non publiée');

		const bouton = ligne.getByRole('button', { name: 'Détail : Tendance du prix médian' });
		await bouton.focus();
		await page.keyboard.press('Enter');
		const volet = page.getByRole('dialog', { name: 'Tendance du prix médian' });
		await expect(volet).toBeVisible();
		await expect(volet).toContainText(TENDANCE_NON_PUBLIEE);
		await page.keyboard.press('Escape');
		await expect(bouton).toBeFocused();
		await source.assert();
	});
});
