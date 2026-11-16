import AxeBuilder from '@axe-core/playwright';
import { test, expect, type Page } from '@playwright/test';
import { E2E_APP_ORIGIN } from './fixtures/ports';
import { mockAuthAnonyme } from './fixtures/mocks-matrice';

// Phase 03-03 (D-50) — the single site header, shared by the public pages and the connected space.
//
// What this spec locks (UI-SPEC « En-tête et pied », « Réactivité »; since 04.8-02 the public
// header has its v4 form, UI-SPEC v4 § 3.11, the connected one keeps its form, cadre-site.spec.ts):
//   - 375 px (04.8 D-41): the brand and « Menu », then the compact search alone on its row; « Menu »
//     opens the three main links, every one a 44 × 44 px target, and the account; it carries the
//     counter of « Comparer »; the document does not overflow;
//   - from 721 to 999 px: brand and search, then the links and the account; the counter of
//     « Comparer » never changes the height of the header (03-17 H-01);
//   - « Comparer » · « Données & méthode » · « Développeurs » at every width (A-03: the « Rechercher »
//     link is replaced by the field);
//   - anonymous → « Se connecter »; signed-in non-admin → « Mon espace » without « Administration »;
//     admin → « Administration »; an unreadable /auth/me ends as anonymous with a usable navigation;
//   - no native tooltip (`title`) on an interactive element of the site chrome;
//   - axe WCAG 2.1 AA without violation on `/` and `/settings`.
// The « Administration » link is display only: authorisation stays in the API and in the (app)
// layout guard (T-03-03-01), so nothing here asserts access control.

const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] as const;

const MOCK_USER = {
	id: 2,
	email: 'test@datacommune.fr',
	name: 'Test User',
	is_verified: true,
	is_admin: false,
	created_at: '2026-01-01T00:00:00Z'
};

async function mockSession(page: Page, user: typeof MOCK_USER) {
	await page.route('**/auth/me', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(user) })
	);
	// /settings reads the keys and the quota; same shape as settings.spec.ts.
	await page.route('**/auth/keys', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ keys: [], account_quota: 500, account_usage: 0 })
		})
	);
}

async function openSettings(page: Page) {
	await page.goto('/settings');
	await expect(page.getByRole('heading', { name: 'Profil' })).toBeVisible({ timeout: 10_000 });
}

function siteHeader(page: Page) {
	return page.getByRole('banner');
}

function mainNav(page: Page) {
	return siteHeader(page).getByRole('navigation', { name: 'Navigation principale' });
}

// Four communes set aside in the tab, as the comparison store writes them (03-17 H-01).
const QUATRE_COMMUNES = ['44991', '44992', '85991', '44993'];

async function hauteurEnTete(page: Page): Promise<number> {
	const box = await page.locator('header.site-header').boundingBox();
	expect(box, 'en-tête mesuré').not.toBeNull();
	return box!.height;
}

// The three main links of the public header (UI-SPEC v4 § 3.11, A-03).
const LIENS_PRINCIPAUX = ['Comparer', 'Données & méthode', 'Développeurs'];

/** The boxes overlap vertically: they share one row. */
function memeRangee(boites: ({ y: number; height: number } | null)[], nom: string) {
	for (const box of boites) expect(box, 'boîte mesurée').not.toBeNull();
	const haut = Math.max(...boites.map((b) => b!.y));
	const bas = Math.min(...boites.map((b) => b!.y + b!.height));
	expect(bas, `recouvrement vertical : ${nom}`).toBeGreaterThan(haut);
}

/**
 * From 721 to 1120 px on a page with the header search (4.9 harmonisation, amends UI-SPEC v4 § 3.11):
 * one row, the brand, « Menu » and the search; the links and the account are the panel of « Menu ». The
 * second row of links under an empty left half was the defect Ianis saw on 06/11.
 */
async function uneRangee(page: Page) {
	const header = siteHeader(page);
	await expect(header.getByRole('link', { name: 'Se connecter' })).toBeHidden();
	await expect(mainNav(page).getByRole('link')).toHaveCount(0);
	const marque = await header.getByRole('link', { name: 'DataCommune', exact: true }).boundingBox();
	const menu = await header.locator('summary').boundingBox();
	const recherche = await header.getByRole('search').boundingBox();
	memeRangee([marque, menu, recherche], 'la marque, « Menu » et la recherche');
	expect(recherche!.x, '« Menu » avant la recherche : l’ordre du clavier est l’ordre à l’écran').toBeGreaterThanOrEqual(menu!.x + menu!.width);
	const inner = await page.locator('.site-header-inner').evaluate((el) => {
		const boite = el.getBoundingClientRect();
		return boite.right - Number.parseFloat(getComputedStyle(el).paddingRight);
	});
	expect(Math.abs(recherche!.x + recherche!.width - inner), 'la recherche va jusqu’au bord droit').toBeLessThanOrEqual(1);
}

test.describe('En-tête unique — 375 px', () => {
	test.use({ viewport: { width: 375, height: 800 } });

	test('marque et « Menu », puis la recherche seule ; le menu ouvre les trois liens de 44 px et le compte ; aucun débordement', async ({ page }) => {
		await mockAuthAnonyme(page);
		await page.goto('/developpeurs');
		const header = siteHeader(page);
		const menu = header.locator('summary');
		await expect(menu).toBeVisible();

		// Folded (04.8 D-41, which replaces the « no burger » of 03-17 for the public header): the brand
		// and « Menu » on the first line, the search alone under them.
		const brand = await header.getByRole('link', { name: 'DataCommune', exact: true }).boundingBox();
		const bouton = await menu.boundingBox();
		const search = await header.getByRole('search').boundingBox();
		expect(brand && bouton && search, 'marque, « Menu » et recherche mesurés').toBeTruthy();
		expect(bouton!.y).toBeLessThan(brand!.y + brand!.height);
		expect(bouton!.height, 'hauteur de cible').toBeGreaterThanOrEqual(44);
		expect(search!.y, 'la recherche sous la marque et « Menu »').toBeGreaterThanOrEqual(Math.max(brand!.y + brand!.height, bouton!.y + bouton!.height));
		await expect(mainNav(page).getByRole('link')).toHaveCount(0);
		// The one button of the folded header is the submit of its search; « Menu » is the summary of a disclosure.
		await expect(header.getByRole('button', { name: 'Rechercher une adresse ou une commune', exact: true })).toHaveAttribute('type', 'submit');

		// Open: the three links, 44 × 44 px targets, one under the other under the search, then the account.
		await menu.click();
		await expect(header.getByRole('link', { name: 'Se connecter' })).toBeVisible();
		const links = mainNav(page).getByRole('link');
		await expect(links).toHaveText(LIENS_PRINCIPAUX);
		const boxes = await Promise.all((await links.all()).map((l) => l.boundingBox()));
		const account = await header.getByRole('link', { name: 'Se connecter' }).boundingBox();
		const searchOuverte = await header.getByRole('search').boundingBox();
		for (const [i, box] of boxes.entries()) {
			expect(box, 'lien principal mesuré').not.toBeNull();
			expect(box!.width, 'largeur de cible').toBeGreaterThanOrEqual(44);
			expect(box!.height, 'hauteur de cible').toBeGreaterThanOrEqual(44);
			// The panel opens under the search (D-41: the order on screen is the order of the keyboard).
			expect(box!.y).toBeGreaterThanOrEqual(searchOuverte!.y + searchOuverte!.height);
			if (i > 0) expect(box!.y, 'un lien sous le précédent').toBeGreaterThanOrEqual(boxes[i - 1]!.y + boxes[i - 1]!.height);
		}
		expect(account!.y, 'le compte sous les liens').toBeGreaterThanOrEqual(boxes[2]!.y + boxes[2]!.height);
		// « Développeurs » is a main link at every width since 04.8-02 (A-03).
		await expect(header.getByRole('link', { name: 'Développeurs' })).toBeVisible();

		const width = await page.evaluate(() => ({
			scroll: document.documentElement.scrollWidth,
			inner: window.innerWidth
		}));
		expect(width.scroll, 'largeur du document à 375 px').toBeLessThanOrEqual(width.inner);
	});

	test("la sélection de quatre communes ne change pas la hauteur de l'en-tête", async ({ page }) => {
		await mockAuthAnonyme(page);
		await page.goto('/developpeurs');
		// The session is read (the account, folded behind « Menu », is no longer the reserved placeholder).
		await expect(siteHeader(page).locator('.site-account-button')).toHaveText('Se connecter');
		const sans = await hauteurEnTete(page);

		await page.evaluate((codes) => sessionStorage.setItem('dc:comparaison', JSON.stringify(codes)), QUATRE_COMMUNES);
		await page.reload();
		await expect(siteHeader(page).locator('.site-account-button')).toHaveText('Se connecter');
		// Folded, « Menu » carries the counter of « Comparer » and says it.
		await expect(page.locator('.site-menu-count')).toHaveText('4');
		await expect(page.locator('.site-menu-count')).toBeVisible();
		await expect(siteHeader(page).locator('summary')).toHaveAccessibleName('Menu (4 communes sélectionnées)');
		const avec = await hauteurEnTete(page);

		expect(Math.abs(avec - sans), `hauteur sans sélection ${sans} px, avec quatre communes ${avec} px`).toBeLessThanOrEqual(1);
		// 04.8-02: 112 px before (two lines); D-41, two rows of 44 px: 4 + 44 + 8 + 44 + 4 px and the 1 px line.
		expect(avec, "hauteur de l'en-tête à 375 px").toBeLessThanOrEqual(105);

		await siteHeader(page).locator('summary').click();
		await expect(page.locator('.site-nav-count')).toHaveText('4');
		await expect(page.locator('.site-nav-count')).toBeVisible();
		const boxes = await Promise.all((await mainNav(page).getByRole('link').all()).map((l) => l.boundingBox()));
		expect(boxes).toHaveLength(3);
		for (const box of boxes) {
			expect(box, 'lien principal mesuré').not.toBeNull();
			expect(box!.height, 'hauteur de cible').toBeGreaterThanOrEqual(44);
		}
	});
});

test.describe('En-tête unique — 768 px', () => {
	test.use({ viewport: { width: 768, height: 1024 } });

	test('marque, « Menu » et recherche sur une rangée, sans débordement ; le menu ouvre les liens et le compte', async ({ page }) => {
		await mockAuthAnonyme(page);
		await page.goto('/developpeurs');
		await uneRangee(page);
		const hauteur = await hauteurEnTete(page);
		// 4.9 harmonisation: 12 + 44 + 12 px and the 1 px line (two rows and 125 px before).
		expect(hauteur, "hauteur de l'en-tête à 768 px").toBeLessThanOrEqual(70);
		await siteHeader(page).locator('summary').click();
		await expect(mainNav(page).getByRole('link')).toHaveText(LIENS_PRINCIPAUX);
		await expect(siteHeader(page).getByRole('link', { name: 'Se connecter' })).toBeVisible();
		const width = await page.evaluate(() => ({
			scroll: document.documentElement.scrollWidth,
			inner: window.innerWidth
		}));
		expect(width.scroll, 'largeur du document à 768 px').toBeLessThanOrEqual(width.inner);
	});
});

for (const largeur of [721, 999, 1024, 1120] as const) {
	test.describe(`En-tête unique — ${largeur} px`, () => {
		test.use({ viewport: { width: largeur, height: 900 } });

		test('marque, « Menu » et recherche : une rangée de 44 px', async ({ page }) => {
			await mockAuthAnonyme(page);
			await page.goto('/developpeurs');
			await uneRangee(page);
			const hauteur = await hauteurEnTete(page);
			// 4.9 harmonisation: 12 + 44 + 12 px and the line (two rows and 125 px before).
			expect(hauteur, `hauteur de l'en-tête à ${largeur} px`).toBeGreaterThanOrEqual(69);
			expect(hauteur, `hauteur de l'en-tête à ${largeur} px`).toBeLessThanOrEqual(70);
			const width = await page.evaluate(() => ({
				scroll: document.documentElement.scrollWidth,
				inner: window.innerWidth
			}));
			expect(width.scroll, `largeur du document à ${largeur} px`).toBeLessThanOrEqual(width.inner);
		});
	});
}

// 03-17 D-15 G3: from 721 to 999px the counter of « Comparer » (8px of margin, 20px wide at least)
// must not push the account zone to another line. 739px is the upper bound of the deduced
// overflow at 721px; 768 and 999 frame the band. Since 4.9 the counter stands on « Menu », on the one
// row of the header: it changes neither the row nor the height.
for (const largeur of [721, 739, 768, 999] as const) {
	test.describe(`En-tête unique — ${largeur} px, une commune sélectionnée`, () => {
		test.use({ viewport: { width: largeur, height: 900 } });

		test('le compteur de « Comparer » ne change ni les rangées ni la hauteur de l’en-tête', async ({ page }) => {
			await mockAuthAnonyme(page);
			await page.goto('/developpeurs');
			await uneRangee(page);
			const sans = await hauteurEnTete(page);
			await page.evaluate((codes) => sessionStorage.setItem('dc:comparaison', JSON.stringify(codes)), QUATRE_COMMUNES.slice(0, 1));
			await page.reload();
			await expect(page.locator('.site-menu-count')).toHaveText('1');
			await expect(page.locator('.site-menu-count')).toBeVisible();
			await uneRangee(page);
			const hauteur = await hauteurEnTete(page);
			expect(Math.abs(hauteur - sans), `hauteur sans sélection ${sans} px, avec le compteur ${hauteur} px`).toBeLessThanOrEqual(1);
			// 4.9 harmonisation: one row (two rows of 44 px before).
			expect(hauteur, `hauteur de l'en-tête à ${largeur} px avec le compteur`).toBeGreaterThanOrEqual(69);
			expect(hauteur, `hauteur de l'en-tête à ${largeur} px avec le compteur`).toBeLessThanOrEqual(70);
			const width = await page.evaluate(() => ({
				scroll: document.documentElement.scrollWidth,
				inner: window.innerWidth
			}));
			expect(width.scroll, `largeur du document à ${largeur} px`).toBeLessThanOrEqual(width.inner);
		});
	});
}

// Just above « Menu »: everything fits one row, the counter of « Comparer » included.
test.describe('En-tête unique — 1121 px', () => {
	test.use({ viewport: { width: 1121, height: 900 } });

	test('marque, recherche, trois liens et « Se connecter » sur une rangée, avec le compteur', async ({ page }) => {
		await mockAuthAnonyme(page);
		await page.goto('/developpeurs');
		await page.evaluate((codes) => sessionStorage.setItem('dc:comparaison', JSON.stringify(codes)), QUATRE_COMMUNES.slice(0, 1));
		await page.reload();
		await expect(page.locator('.site-nav-count')).toBeVisible();
		const header = siteHeader(page);
		await expect(header.locator('summary')).toBeHidden();
		const boites = [
			await header.getByRole('link', { name: 'DataCommune', exact: true }).boundingBox(),
			await header.getByRole('search').boundingBox(),
			...(await Promise.all((await mainNav(page).getByRole('link').all()).map((l) => l.boundingBox()))),
			await header.getByRole('link', { name: 'Se connecter' }).boundingBox()
		];
		expect(boites).toHaveLength(6);
		memeRangee(boites, 'tout l’en-tête');
		expect(await hauteurEnTete(page), 'une rangée').toBeLessThanOrEqual(70);
	});
});

test.describe('En-tête unique — 1280 px', () => {
	test.use({ viewport: { width: 1280, height: 800 } });

	test('marque, recherche, trois liens et « Se connecter » pour un visiteur anonyme', async ({ page }) => {
		await mockAuthAnonyme(page);
		await page.goto('/developpeurs');
		const header = siteHeader(page);

		await expect(header.getByRole('link', { name: 'DataCommune', exact: true })).toHaveAttribute('href', '/');
		await expect(mainNav(page).getByRole('link')).toHaveText(LIENS_PRINCIPAUX);
		await expect(header.getByRole('link', { name: 'Développeurs' })).toBeVisible();
		await expect(header.getByRole('link', { name: 'Se connecter' })).toHaveAttribute('href', '/login');
		await expect(header.getByRole('button', { name: 'Mon espace' })).toHaveCount(0);

		// 04.8-02: 72 px at least before (min-height); one row since, 12 + 44 + 12 px and the 1 px line
		// (§ 3.11). Not sticky on the public site (A-01): no band hides the focus.
		const box = await header.boundingBox();
		expect(box!.height, "hauteur de l'en-tête").toBeGreaterThanOrEqual(69);
		expect(box!.height, "hauteur de l'en-tête : une rangée").toBeLessThanOrEqual(70);
		expect(await header.evaluate((el) => getComputedStyle(el).position)).toBe('static');
	});

	test('lien courant marqué aria-current', async ({ page }) => {
		await mockAuthAnonyme(page);
		await page.goto('/datasets');
		await expect(mainNav(page).getByRole('link', { name: 'Données & méthode' })).toHaveAttribute(
			'aria-current',
			'page'
		);
		await expect(mainNav(page).getByRole('link', { name: 'Comparer' })).not.toHaveAttribute(
			'aria-current',
			'page'
		);
	});

	test('connecté non admin : « Mon espace » sans « Administration »', async ({ page }) => {
		await mockSession(page, MOCK_USER);
		await openSettings(page);
		const header = siteHeader(page);

		const button = header.getByRole('button', { name: 'Mon espace' });
		await expect(button).toHaveAttribute('aria-expanded', 'false');
		await button.click();
		await expect(button).toHaveAttribute('aria-expanded', 'true');
		await expect(header.getByRole('link', { name: 'Réglages' })).toHaveAttribute('href', '/settings');
		await expect(header.getByRole('link', { name: 'Facturation' })).toHaveAttribute('href', '/billing');
		await expect(header.getByRole('button', { name: 'Se déconnecter' })).toBeVisible();
		await expect(header.getByRole('link', { name: 'Administration' })).toHaveCount(0);
		await expect(header.getByRole('link', { name: 'Se connecter' })).toHaveCount(0);

		await page.keyboard.press('Escape');
		await expect(button).toHaveAttribute('aria-expanded', 'false');
		await expect(button).toBeFocused();
	});

	test('admin : « Administration » dans le menu', async ({ page }) => {
		await mockSession(page, { ...MOCK_USER, is_admin: true });
		await openSettings(page);
		const header = siteHeader(page);

		await header.getByRole('button', { name: 'Mon espace' }).click();
		await expect(header.getByRole('link', { name: 'Administration' })).toHaveAttribute('href', '/admin');
	});

	test('session illisible : « Se connecter » et navigation utilisable', async ({ page }) => {
		await page.route('**/auth/me', (route) =>
			route.fulfill({ status: 200, contentType: 'application/json', body: '{not json' })
		);
		await page.goto('/developpeurs');
		const header = siteHeader(page);

		await expect(header.getByRole('link', { name: 'Se connecter' })).toBeVisible();
		await expect(header.getByRole('button', { name: 'Mon espace' })).toHaveCount(0);
		await mainNav(page).getByRole('link', { name: 'Données & méthode' }).click();
		await expect(page).toHaveURL(`${E2E_APP_ORIGIN}/datasets`);
	});
});

test.describe('Pied unique', () => {
	// 04.7-14: « Ce que nous ne montrons pas » joins the limits, after « Couverture et limites ».
	// 04.8-06 (UI-SPEC § 3.11): « Lire une fiche » (/datasets#lire) second.
	const FOOTER_LINKS = [
		'Données & méthode',
		'Lire une fiche',
		'Couverture et limites',
		'Ce que nous ne montrons pas',
		'Développeurs',
		// 05-07 lot 6 (D-137): the public source repository, after the developer page.
		'Code source',
		'Mentions légales',
		'Confidentialité',
		'CGU',
		// Socle (2026-11-09, I9): the contact link, last.
		'Signaler une erreur'
	];
	// 04.8-08 (D-34, UI-SPEC § 3.11): the mockup's sentence, once every public surface is ported.
	const FOOTER_SENTENCE =
		'DataCommune rassemble des données publiques ouvertes. Chaque valeur cite sa source, sa date et l’échelle à laquelle elle vaut.';

	test('375 px : liens à la ligne, cibles de 44 px, phrase sans débordement', async ({ page }) => {
		await page.setViewportSize({ width: 375, height: 800 });
		await mockAuthAnonyme(page);
		await page.goto('/developpeurs');
		const footer = page.getByRole('contentinfo');

		await expect(footer.getByRole('link')).toHaveText(FOOTER_LINKS);
		await expect(footer.getByText(FOOTER_SENTENCE, { exact: true })).toBeVisible();
		for (const link of await footer.getByRole('link').all()) {
			const box = await link.boundingBox();
			expect(box!.width, 'largeur de cible').toBeGreaterThanOrEqual(44);
			expect(box!.height, 'hauteur de cible').toBeGreaterThanOrEqual(44);
		}
		const width = await page.evaluate(() => ({
			scroll: document.documentElement.scrollWidth,
			inner: window.innerWidth
		}));
		expect(width.scroll).toBeLessThanOrEqual(width.inner);
	});

	test('le même pied sert l’espace connecté', async ({ page }) => {
		await mockSession(page, MOCK_USER);
		await openSettings(page);
		const footer = page.getByRole('contentinfo');
		await expect(footer.getByRole('link')).toHaveText(FOOTER_LINKS);
		await expect(footer.getByText(FOOTER_SENTENCE, { exact: true })).toBeVisible();
	});
});

test.describe('Chrome du site — infobulles natives et accessibilité', () => {
	const INTERACTIF_AVEC_TITLE = 'a[title], button[title], summary[title], [role="button"][title]';

	test('aucun élément interactif de l’en-tête ou du pied ne porte un attribut title', async ({ page }) => {
		await mockAuthAnonyme(page);
		await page.goto('/developpeurs');
		// Non-vacuity: the chrome renders interactive elements.
		await expect(siteHeader(page).getByRole('link').first()).toBeVisible();
		await expect(page.locator('header, footer').locator(INTERACTIF_AVEC_TITLE)).toHaveCount(0);

		await page.unrouteAll({ behavior: 'ignoreErrors' });
		await mockSession(page, { ...MOCK_USER, is_admin: true });
		await openSettings(page);
		await siteHeader(page).getByRole('button', { name: 'Mon espace' }).click();
		await expect(page.locator('header, footer').locator(INTERACTIF_AVEC_TITLE)).toHaveCount(0);
	});

	for (const largeur of [1280, 375] as const) {
		test(`axe WCAG 2.1 AA sur / et /settings @ ${largeur}px`, async ({ page }) => {
			await page.setViewportSize({ width: largeur, height: 900 });

			await mockAuthAnonyme(page);
			await page.goto('/');
			// At 375 px the links and the account are the panel of « Menu » (D-41): once the session is
			// read, it is opened, so axe reads them.
			await expect(siteHeader(page).locator('.site-account-button')).toHaveText('Se connecter');
			if (largeur === 375) await siteHeader(page).locator('summary').click();
			await expect(siteHeader(page).getByRole('link', { name: 'Se connecter' })).toBeVisible();
			const publique = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();

			await page.unrouteAll({ behavior: 'ignoreErrors' });
			await mockSession(page, MOCK_USER);
			await openSettings(page);
			await siteHeader(page).getByRole('button', { name: 'Mon espace' }).click();
			const connectee = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();

			for (const [nom, resultats] of [
				['/', publique],
				['/settings', connectee]
			] as const) {
				expect(resultats.passes.length, `scan axe vide sur ${nom}`).toBeGreaterThan(0);
				expect(
					resultats.violations.map((v) => ({
						id: v.id,
						impact: v.impact,
						noeuds: v.nodes.map((n) => n.target.join(' '))
					})),
					`violations axe sur ${nom} à ${largeur}px`
				).toEqual([]);
			}
		});
	}
});
