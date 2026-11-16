import AxeBuilder from '@axe-core/playwright';
import { test, expect, type Page } from '@playwright/test';
import { E2E_APP_ORIGIN } from './fixtures/ports';
import { installLocalNetworkGuard, expectNoUnexpectedOffLocalRequest } from './fixtures/local-network';
import { mockAuthAnonyme } from './fixtures/mocks-matrice';

// Phase 03-02 (D-39) — minimal developer page.
//
// `/developpeurs` replaces /docs, /reference and /playground. What this spec locks:
//   - no third-party script: every `<script src>` of the served HTML resolves to the app origin
//     (the former /docs loaded Scalar from a CDN; prerendered pages get no security headers, KI-P3);
//   - no `/v1` request while the page loads (static page, nothing to fetch);
//   - one h1, then what answers without a key first (2026-11-11): the JSON export of a commune as a
//     command that works as written, its copy button, an extract of the answer folded;
//   - last panel, the API with a key: its address in a <code>, a link to the OpenAPI document served by
//     the backend, the key and its example (this build has accounts);
//   - no hard-coded quota figure (the value lives in Settings and comes from the API);
//   - at 375 px the document does not overflow and only the named wrapper of the curl <pre> scrolls
//     horizontally (04.8-06);
//   - axe WCAG 2.1 AA: no violation.
// /auth/me is mocked (anonymous) because the root layout asks for the session on mount; the local
// network guard fails the test on any request that leaves the loopback test environment.

const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] as const;

async function ouvrirDeveloppeurs(page: Page): Promise<{ html: string; cheminsV1: string[] }> {
	await installLocalNetworkGuard(page);
	await mockAuthAnonyme(page);
	const cheminsV1: string[] = [];
	page.on('request', (req) => {
		const chemin = new URL(req.url()).pathname;
		if (chemin.startsWith('/v1')) cheminsV1.push(chemin);
	});
	const reponse = await page.goto('/developpeurs');
	expect(reponse?.status(), 'statut HTTP de /developpeurs').toBe(200);
	await page.waitForLoadState('networkidle');
	await expect(page.getByRole('heading', { level: 1, name: 'Développeurs' })).toBeVisible();
	return { html: (await reponse?.text()) ?? '', cheminsV1 };
}

test.describe('Page développeurs (D-39)', () => {
	test('contenu : export sans clé d’abord, puis l’API : adresse, référence OpenAPI, clés, quotas sans chiffre, exemple curl', async ({ page }) => {
		const erreurs: string[] = [];
		page.on('pageerror', (err) => erreurs.push(err.message));
		const { cheminsV1 } = await ouvrirDeveloppeurs(page);

		await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);

		await expect(page.getByRole('heading', { level: 2 })).toHaveText([
			'Une commune en JSON, sans clé',
			'Une adresse',
			'Réutiliser les données',
			'API avec clé'
		]);

		// The export: a command on a real commune (no text to replace, no key), copiable; the extract is folded.
		const exportCommune = page.getByRole('region', { name: 'Une commune en JSON, sans clé' });
		const commande = exportCommune.getByRole('group', { name: "Commande curl de l'export d'une commune" });
		await expect(commande.locator('pre')).toHaveText(/^curl "https?:\/\/[^"]+\/commune\/[0-9AB]{5}\.json"$/);
		await expect(commande.getByRole('button', { name: 'Copier' })).toBeVisible();
		const pli = exportCommune.locator('details.depliant');
		await expect(pli).not.toHaveAttribute('open', /.*/);
		await pli.locator('summary').click();
		await expect(pli.locator('pre')).toContainText('"format": "datacommune.commune.v1"');

		const api = page.getByRole('region', { name: 'API avec clé' });
		await expect(api.locator('code.dev-url')).toHaveText(/^https?:\/\/\S+$/);

		const openapi = page.getByRole('link', { name: 'Spécification OpenAPI (JSON)' });
		await expect(openapi).toHaveAttribute('href', /\/openapi\.json$/);
		await expect(page.getByRole('link', { name: "Référence interactive servie par l'API" })).toHaveAttribute(
			'href',
			/\/docs$/
		);

		await expect(api).toContainText('X-API-Key');
		await expect(api.getByRole('link', { name: 'Obtenir une clé API' })).toHaveAttribute('href', '/register');

		const quotas = api.locator('p', { hasText: 'quota de requêtes' });
		await expect(quotas).toContainText('429');
		// Only the HTTP status is a number in this paragraph: no quota, price or counter is written here.
		const texteQuotas = (await quotas.innerText()).replace('429', '');
		expect(texteQuotas, 'aucun chiffre écrit en dur dans le paragraphe des quotas').not.toMatch(/\d/);

		const exempleApi = api.getByRole('region', { name: 'Exemple de requête curl' }).locator('pre');
		await expect(exempleApi).toContainText('X-API-Key: VOTRE_CLE');
		await expect(exempleApi).toContainText('/v1/commune?code_insee=');

		expect(cheminsV1, 'aucune requête /v1 au chargement').toEqual([]);
		expect(erreurs, 'erreurs de page').toEqual([]);
		expectNoUnexpectedOffLocalRequest(page);
	});

	test('aucun script tiers dans le HTML servi', async ({ page }) => {
		const { html } = await ouvrirDeveloppeurs(page);
		const sources = [...html.matchAll(/<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["']/gi)].map((m) => m[1]);
		const tiers = sources.filter((src) => new URL(src, E2E_APP_ORIGIN).origin !== E2E_APP_ORIGIN);
		expect(tiers, 'scripts chargés depuis une autre origine que l’application').toEqual([]);
		// Non-vacuity: the served HTML is the real page, not an empty shell.
		expect(html).toContain('Une commune en JSON, sans clé');
		expectNoUnexpectedOffLocalRequest(page);
	});

	test('375 px : aucun débordement du document, la commande passe à la ligne au lieu de défiler', async ({ page }) => {
		await page.setViewportSize({ width: 375, height: 800 });
		await ouvrirDeveloppeurs(page);
		await page.locator('details.depliant summary').click();
		await expect(page.locator('details.depliant pre')).toBeVisible();

		const mesure = await page.evaluate(() => {
			const defilants = [...document.querySelectorAll<HTMLElement>('body *')]
				.filter((el) => {
					const ox = getComputedStyle(el).overflowX;
					return (ox === 'auto' || ox === 'scroll') && el.scrollWidth > el.clientWidth;
				})
				.map((el) => el.tagName.toLowerCase());
			return {
				scrollWidth: document.documentElement.scrollWidth,
				innerWidth: window.innerWidth,
				defilants
			};
		});
		expect(mesure.scrollWidth, 'largeur du document à 375 px').toBeLessThanOrEqual(mesure.innerWidth);
		// I7 (2026-11-10, option A): the curl line is longer than 375 px; it wraps, so nothing scrolls and the
		// wrapper needs no keyboard stop. Non-vacuous: the command is long, and it is whole in the <pre>.
		// Measured with the extract unfolded (2026-11-11): its long lines wrap too.
		expect(mesure.defilants).toEqual([]);
		const code = page.getByRole('region', { name: 'Exemple de requête curl' });
		await expect(code).not.toHaveAttribute('tabindex', /.*/);
		await expect(code.locator('pre')).toHaveCount(1);
		const pre = code.locator('pre');
		expect(await pre.evaluate((el) => getComputedStyle(el).whiteSpace)).toBe('pre-wrap');
		expect(await pre.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
		expect((await pre.innerText()).length).toBeGreaterThan(60);
		expect(await pre.evaluate((el) => el.getBoundingClientRect().height > 2 * parseFloat(getComputedStyle(el).lineHeight))).toBe(true);
		expectNoUnexpectedOffLocalRequest(page);
	});

	for (const largeur of [1280, 375] as const) {
		test(`axe WCAG 2.1 AA @ ${largeur}px : aucune violation`, async ({ page }) => {
			await page.setViewportSize({ width: largeur, height: 900 });
			await ouvrirDeveloppeurs(page);
			const resultats = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
			expect(resultats.passes.length, 'scan axe vide').toBeGreaterThan(0);
			expect(
				resultats.violations.map((v) => ({
					id: v.id,
					impact: v.impact,
					noeuds: v.nodes.map((n) => n.target.join(' '))
				})),
				`violations axe à ${largeur}px`
			).toEqual([]);
		});
	}
});
