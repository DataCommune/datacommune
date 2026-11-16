import { test, expect } from '@playwright/test';
import {
	E2E_APP_ORIGIN,
	E2E_SOURCE_ORIGIN,
	validateDistinctOrigins,
	validateLocalOrigin
} from './fixtures/ports';

test('E2E origin configuration refuses every non-loopback target', () => {
	for (const origin of [
		'https://api.datacommune.fr',
		'http://example.test:4174',
		'file:///tmp/source',
		'http://localhost',
		'http://localhost:0',
		'http://user:password@localhost:4174',
		'http://localhost:4174/path',
		'http://localhost:4174?query=1'
	]) {
		expect(() => validateLocalOrigin('SYNTHETIC_E2E_ORIGIN', origin)).toThrow(
			'SYNTHETIC_E2E_ORIGIN must be an exact loopback HTTP origin with an explicit valid port'
		);
	}
	expect(validateLocalOrigin('SYNTHETIC_E2E_ORIGIN', 'http://localhost:5273')).toBe('http://localhost:5273');
	expect(validateLocalOrigin('SYNTHETIC_E2E_ORIGIN', 'http://127.0.0.1:4274')).toBe('http://127.0.0.1:4274');
	expect(() => validateDistinctOrigins('http://localhost:5273', 'http://127.0.0.1:5273')).toThrow(
		'E2E app and source origins must use distinct loopback ports'
	);
});

test.describe('Smoke tests', () => {
	test('root page is the direct commune search (no redirect)', async ({ page }) => {
		await page.goto('/');
		// Root is the commune search itself (D-38), not a redirect nor a marketing landing.
		await expect(page).toHaveURL(`${E2E_APP_ORIGIN}/`);
		// 04.8-07 (UI-SPEC § 6.6, § 3.13): the v4 h1 and the large field.
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(
			'Une adresse, en clair'
		);
		await expect(page.getByRole('combobox', { name: 'Adresse, commune ou code postal' })).toBeVisible();
		await expect(page.getByRole('link', { name: 'Voir la couverture des données' })).toHaveAttribute(
			'href',
			'/datasets#couverture-titre'
		);
	});

	test('login page renders correctly', async ({ page }) => {
		await page.goto('/login');

		// Title
		await expect(page).toHaveTitle('Connexion — DataCommune');

		// Form elements
		await expect(page.getByRole('heading', { name: 'Connexion' })).toBeVisible();
		await expect(page.getByPlaceholder('votre@email.com')).toBeVisible();
		await expect(page.getByPlaceholder('••••••••')).toBeVisible();
		await expect(page.getByRole('button', { name: 'Se connecter' })).toBeVisible();

		// Links
		await expect(page.getByRole('link', { name: 'Mot de passe oublié ?' })).toBeVisible();
		await expect(page.getByRole('link', { name: 'Créer un compte' })).toBeVisible();
	});

	test('login form shows error on invalid credentials', async ({ page }) => {
		await page.route('**/auth/login', async (route) => {
			expect(route.request().method()).toBe('POST');
			await route.fulfill({
				status: 401,
				contentType: 'application/problem+json',
				body: JSON.stringify({ title: 'Identifiants invalides', status: 401 })
			});
		});
		await page.goto('/login');
		// Dev-mode SvelteKit hydrates via many unbundled ES module requests;
		// without this wait the submit click can race ahead of the handler
		// attaching, silently no-opping the click (flaky, not an app bug).
		await page.waitForLoadState('networkidle');

		await page.getByPlaceholder('votre@email.com').fill('test@example.com');
		await page.getByPlaceholder('••••••••').fill('wrongpassword');
		await page.getByRole('button', { name: 'Se connecter' }).click();

		// The invalid-credential response is local and deterministic.
		await expect(
			page.getByText(/Email ou mot de passe incorrect|Erreur de connexion/)
		).toBeVisible({ timeout: 15000 });
	});

	test('navigate to register page', async ({ page }) => {
		await page.goto('/login');
		await page.getByRole('link', { name: 'Créer un compte' }).click();
		await expect(page).toHaveURL(/\/register/);
	});
});

// Without JavaScript the home page form is a plain `GET /communes` (q, departement): the visitor
// still reaches the results, rendered server-side from this instance's API (answered here by the
// synthetic source under an exact expectation).
test.describe('Home page without JavaScript', () => {
	test.use({ javaScriptEnabled: false });

	test('the search form reaches the results page', async ({ page, request }) => {
		const configured = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
			data: {
				reset: true,
				expectations: [
					{
						method: 'GET',
						pathname: '/v1/communes/search',
						query: { q: 'le pin', limit: '20', inclure_arrondissements: 'true' },
						minCalls: 1,
						maxCalls: 1,
						responses: [
							{
								status: 200,
								body: {
									statut: 'ok',
									donnees: [
										{
											code_insee: '35999',
											nom: 'Le Pin',
											code_postal: '35170',
											departement: '35',
											population: 750,
											population_annee: 2022,
											population_source: 'demographie',
											hebergement: { etat: 'heberge', motif: null, departements: ['35'] }
										}
									]
								}
							}
						]
					},
					// The results page reads the addresses of the same query too (04-17).
					{
						method: 'GET',
						pathname: '/v1/adresses/recherche',
						query: { q: 'le pin', limite: '8' },
						minCalls: 1,
						maxCalls: 1,
						responses: [{ status: 200, body: { statut: 'ok', donnees: { ordre_types: ['commune', 'adresse'], resultats: [], hebergement: { ban: { etat: 'heberge', motif: null, departements: ['35'] } } } } }]
					}
				]
			}
		});
		expect(configured.ok()).toBe(true);

		await page.goto('/');
		// 04.8-07 (UI-SPEC § 3.13): the large field and its plain button « Rechercher ».
		await page.getByRole('combobox', { name: 'Adresse, commune ou code postal' }).fill('le pin');
		await page.getByRole('search', { name: 'Rechercher un lieu' }).getByRole('button', { name: 'Rechercher', exact: true }).click();

		await expect(page).toHaveURL(/\/communes\?/);
		const url = new URL(page.url());
		expect(url.pathname).toBe('/communes');
		expect(url.searchParams.get('q')).toBe('le pin');
		// 04.8-07 (UI-SPEC § 6.5): the commune is a row `li.resultat`, its name the title of the row link.
		const resultat = page.locator('li.resultat[data-code-insee="35999"]');
		await expect(resultat).toBeVisible();
		await expect(resultat.locator('a.resultat-lien .lien-t')).toHaveText('Le Pin');

		const audit = await request.get(`${E2E_SOURCE_ORIGIN}/__requests`);
		expect(audit.ok()).toBe(true);
		const { violations, unmetExpectations } = (await audit.json()) as {
			violations: unknown[];
			unmetExpectations: unknown[];
		};
		expect(violations, JSON.stringify(violations)).toEqual([]);
		expect(unmetExpectations, JSON.stringify(unmetExpectations)).toEqual([]);
	});
});
