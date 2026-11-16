// The commune fiche reads like the address fiche (4.9, decision of 06/11): the same summary « Sur cette
// fiche » in the margin above the two-column threshold and as one compact control under it, the same names
// of categories in the same order of reading (prices, housing and DPE, daily life), each category folded
// under the threshold and a card above it. Headless runner, isolated context, every SSR read served by the
// local fail-closed source (AGENTS.md).
import { test, expect, type Page } from '@playwright/test';
import { configureSsrSource } from './fixtures/bien-ssr-source';
import { COMMUNE_FLAT_85047_WITH_DVF_WINDOW, COMMUNE_INDICATORS_SYNTHETIC } from './fixtures/commune-indicators';

const MOCK_USER = { id: 2, email: 'test@datacommune.fr', name: 'Test User', is_verified: true, is_admin: false, created_at: '2026-01-01T00:00:00Z' };
const SYNTHESE = {
	statut: 'ok',
	donnees: [{ ...COMMUNE_FLAT_85047_WITH_DVF_WINDOW, ...COMMUNE_INDICATORS_SYNTHETIC }],
	meta: { source: [{ nom: 'Démographie' }, { nom: 'Loyers' }, { nom: 'Zonage ABC' }] }
};
const CATEGORIES = [
	{ id: 'q-prix', titre: 'Prix et ventes' },
	{ id: 'q-logement', titre: 'Logement et DPE' },
	{ id: 'q-quotidien', titre: 'Vivre dans la commune' }
];

async function ouvrir(page: Page, largeur: number, chemin = '/commune/85047') {
	await configureSsrSource(page.request, [
		{
			method: 'GET',
			pathname: '/v1/commune',
			query: { code_insee: '85047' },
			minCalls: 0,
			maxCalls: 6,
			responses: Array.from({ length: 6 }, () => ({ status: 200, body: SYNTHESE }))
		}
	]);
	await page.setViewportSize({ width: largeur, height: largeur >= 1024 ? 900 : 800 });
	const hydratee = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
	await page.goto(chemin);
	await hydratee;
	await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
}

test.beforeEach(async ({ page }) => {
	await page.route('**/auth/me', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_USER) }));
});

test.describe('Fiche commune — sommaire et catégories de la fiche adresse', () => {
	test('1440 px : sommaire en marge, catégories dans l’ordre de lecture, chaque ligne dans sa catégorie', async ({ page }) => {
		await ouvrir(page, 1440);
		const sommaire = page.locator('nav.sommaire-large');
		await expect(sommaire).toBeVisible();
		await expect(sommaire).toHaveAccessibleName('Sur cette fiche');
		// The categories of the summary, then the reading aids set apart, as on the address fiche.
		const parties = sommaire.locator('ol.sommaire-parties > li > a');
		expect(await parties.evaluateAll((liens) => liens.map((a) => [a.getAttribute('href'), a.textContent?.trim()]))).toEqual(
			CATEGORIES.map((c) => [`#${c.id}`, c.titre])
		);
		await expect(sommaire.locator('ul.sommaire-fin a')).toHaveText(['Questions fréquentes']);
		// Every entry under a category lands in that category.
		for (const { id } of CATEGORIES) {
			const lieux = await sommaire.locator(`ol.sommaire-parties > li:has(> a[href="#${id}"]) ol.sommaire-lieux a`).evaluateAll((liens) => liens.map((a) => a.getAttribute('href')!));
			expect(lieux.length, `entrées sous ${id}`).toBeGreaterThan(0);
			for (const href of lieux) await expect(page.locator(`details.cat#${id} ${href}`), `${href} dans ${id}`).toHaveCount(1);
		}

		// The categories, in that order, open as cards with their title.
		const cats = page.locator('details.cat');
		expect(await cats.evaluateAll((ds) => ds.map((d) => d.id))).toEqual(CATEGORIES.map((c) => c.id));
		for (const { id, titre } of CATEGORIES) {
			await expect(page.locator(`details.cat#${id}`)).toHaveJSProperty('open', true);
			await expect(page.locator(`details.cat#${id} .cat-titre`)).toHaveText(titre);
		}
		// Prices, rents and DPE first: each served value in the category of the address fiche.
		await expect(page.locator('#q-prix [data-field-id="dvf.prix_m2_median_maison"]')).toHaveCount(1);
		await expect(page.locator('#q-prix [data-field-id="loyers.loyer_m2_appartement"]')).toHaveCount(1);
		await expect(page.locator('#q-prix [data-field-id="fiscalite.taux_tfpb"]')).toHaveCount(1);
		await expect(page.locator('#q-logement [data-field-id="dpe.dpe_median"]')).toHaveCount(1);
		await expect(page.locator('#q-logement [data-field-id="demographie.nb_logements"]')).toHaveCount(1);
		await expect(page.locator('#q-quotidien [data-field-id="equipements.nb_equipements"]')).toHaveCount(1);
		await expect(page.locator('#q-quotidien [data-field-id="revenus.revenu_median"]')).toHaveCount(1);
		// No row outside a category, none twice; the panels of the former themes are gone.
		const lignes = await page.locator('main [data-ligne]').evaluateAll((ls) => ls.filter((l) => !l.closest('.panneau-essentiel')).map((l) => [l.getAttribute('data-ligne'), Boolean(l.closest('details.cat'))]));
		expect(lignes.length).toBeGreaterThan(20);
		expect(lignes.filter(([, dedans]) => !dedans)).toEqual([]);
		expect(new Set(lignes.map(([id]) => id)).size).toBe(lignes.length);
		await expect(page.locator('section.panneau-simple#immobilier')).toHaveCount(0);
	});

	test('375 px : sommaire compact, catégories repliées, une entrée ouvre sa catégorie', async ({ page }) => {
		await ouvrir(page, 375);
		await expect(page.locator('nav.sommaire-large')).toBeHidden();
		const compact = page.locator('nav.sommaire-compact');
		await expect(compact).toBeVisible();
		for (const { id } of CATEGORIES) await expect(page.locator(`details.cat#${id}`)).toHaveJSProperty('open', false);
		await compact.locator('summary').click();
		await compact.getByRole('link', { name: 'Logement et DPE' }).click();
		await expect(page.locator('details.cat#q-logement')).toHaveJSProperty('open', true);
		await expect(page.locator('details.cat#q-prix')).toHaveJSProperty('open', false);
	});

	test('une ancre de thème d’avant (#energie-bati) ouvre sa catégorie et y mène', async ({ page }) => {
		await ouvrir(page, 375, '/commune/85047#energie-bati');
		await expect(page.locator('details.cat#q-logement')).toHaveJSProperty('open', true);
		await expect(page.locator('#q-logement [data-field-id="dpe.dpe_median"]')).toBeInViewport();
	});

	// Integration of 09/11: content above the anchor that grows after the jump (the header actions once the
	// session is known, a caption reflowed) must not push the anchor down: a smooth scroll keeps the target it
	// computed when it started, and the rows of the anchor fell out of view under load.
	test('une ancre de thème reste sous l’en-tête quand le haut de la page grandit après le saut', async ({ page }) => {
		// Deterministic stand-in for that late growth: 120 px inserted once at the top of main while the jump
		// is still moving the page (its first scroll event). Once the page is still, the browser's own scroll
		// anchoring absorbs such a growth; during the smooth scroll it does not (anchor left at 200 px).
		await page.addInitScript(() => {
			const sauter = Element.prototype.scrollIntoView;
			let arme = false;
			Element.prototype.scrollIntoView = function (...options: unknown[]) {
				if (!arme) {
					arme = true;
					addEventListener(
						'scroll',
						() => {
							const bloc = document.createElement('div');
							bloc.style.height = '120px';
							bloc.dataset.croissance = 'oui';
							document.querySelector('main')?.prepend(bloc);
						},
						{ once: true }
					);
				}
				return sauter.apply(this, options as []);
			};
		});
		await ouvrir(page, 375, '/commune/85047#energie-bati');
		await expect(page.locator('details.cat#q-logement')).toHaveJSProperty('open', true);
		await expect(page.locator('main > [data-croissance]')).toHaveCount(1);
		// Settled: two reads 300 ms apart give the same scroll position.
		await expect.poll(async () => {
			const avant = await page.evaluate(() => scrollY);
			await new Promise((resolve) => setTimeout(resolve, 300));
			return (await page.evaluate(() => scrollY)) === avant;
		}).toBe(true);
		const haut = await page.evaluate(() => document.getElementById('energie-bati')!.getBoundingClientRect().top);
		const marge = await page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop));
		expect(Math.abs(haut - marge), `ancre à ${haut} px, attendue à ${marge} px`).toBeLessThanOrEqual(2);
		await expect(page.locator('#q-logement [data-field-id="dpe.dpe_median"]')).toBeInViewport();
	});
});
