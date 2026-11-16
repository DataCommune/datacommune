import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';

// Keyboard audit of 2026-11-10 (internal note 2026-11-10-audit-perf-mobile-clavier, branch
// `claude/audit-perf-mobile-clavier-988f0c`), findings C-2 and C-3: Escape folds what the keyboard opened,
// from where the focus is, and gives the focus back to the control that opened it.
//
// The search field keeps its own Escape first (close the list, then clear the field: CommuneSearch's
// contract); only an Escape it has nothing left to do with goes on to the menu or the zone around it.
// Headless runner, isolated context, the empty comparator reads nothing from the source (AGENTS.md).

async function configure(request: APIRequestContext) {
	expect((await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, { data: { reset: true, expectations: [] } })).ok()).toBe(true);
}

async function ouvrirComparateur(page: Page, request: APIRequestContext, largeur: number) {
	await page.setViewportSize({ width: largeur, height: largeur === 1440 ? 900 : 812 });
	await configure(request);
	const hydratee = page.waitForResponse((reponse) => new URL(reponse.url()).pathname === '/auth/me');
	await page.goto('/comparer');
	await hydratee;
}

test.describe('C-2 — menu étroit : Échap depuis le champ de recherche de l’en-tête', () => {
	test.use({ contextOptions: { reducedMotion: 'reduce' } });

	test('375 px : la recherche garde ses deux Échap, le troisième replie le menu et rend le focus à « Menu »', async ({ page, request }) => {
		await ouvrirComparateur(page, request, 375);
		const entete = page.getByRole('banner');
		const menu = entete.locator('summary');
		const details = entete.locator('details.site-menu-etroit');
		await menu.focus();
		await page.keyboard.press('Enter');
		await expect(details).toHaveAttribute('open', '');
		await page.keyboard.press('Tab');
		const champ = entete.getByRole('searchbox', { name: 'Rechercher une adresse ou une commune' });
		await expect(champ).toBeFocused();

		// A typed field: the first Escape clears it (one letter: below the two of a search, no list, no request), the menu stays open.
		await page.keyboard.type('z');
		await page.keyboard.press('Escape');
		await expect(champ).toHaveValue('');
		await expect(details).toHaveAttribute('open', '');
		await expect(champ).toBeFocused();

		// Nothing left for the field: Escape folds the menu and gives the focus back to « Menu ».
		await page.keyboard.press('Escape');
		await expect(details).not.toHaveAttribute('open', /.*/);
		await expect(menu).toBeFocused();
	});
});

for (const largeur of [1440, 375] as const) {
	test.describe(`C-3 — comparateur à ${largeur} px : Échap replie la recherche ouverte par « Ajouter une commune »`, () => {
		test.use({ contextOptions: { reducedMotion: 'reduce' } });

		test('depuis le champ vide, Échap replie la zone et rend le focus au bouton', async ({ page, request }) => {
			await ouvrirComparateur(page, request, largeur);
			const ajouter = page.getByRole('button', { name: 'Ajouter une commune' });
			await ajouter.focus();
			await page.keyboard.press('Enter');
			const champ = page.getByRole('combobox', { name: 'Rechercher une commune' });
			await expect(champ).toBeFocused();
			await expect(ajouter).toHaveAttribute('aria-expanded', 'true');

			// The field keeps its own Escape while it has text.
			await page.keyboard.type('z');
			await page.keyboard.press('Escape');
			await expect(champ).toHaveValue('');
			await expect(champ).toBeVisible();

			await page.keyboard.press('Escape');
			await expect(champ).toHaveCount(0);
			await expect(ajouter).toHaveAttribute('aria-expanded', 'false');
			await expect(ajouter).toBeFocused();
		});
	});
}
