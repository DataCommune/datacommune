import { test, expect, type Page } from '@playwright/test';
import { configureSsrSource } from './fixtures/bien-ssr-source';
import { expectNoUnexpectedOffLocalRequest, installLocalNetworkGuard } from './fixtures/local-network';
import { ROUTES_PHASE4, type RoutePhase4 } from './fixtures/surfaces-phase4';

// Plan 04-08 Task 3 — rule `accent-unique` of the design guard (04-UI-SPEC « Garde de design »,
// UI-01): at most ONE element with the `--color-accent` background per rendered public route of
// phase 4 (« un seul bouton accent par écran »). Checked here, on the rendered page, because the
// static design gate cannot see which component ends up on which route.
//
// Counted: every visible element (non-zero box, `checkVisibility`) whose computed background colour
// equals the computed value of `--color-accent`, read from a probe element of the same page (the
// token is never hard-coded here). Not counted, as the UI-SPEC says: the link-buttons « Retirer » and
// « Placer en premier » of the comparator, and the comparison counter of the header. Not counted
// either: the brand mark of the header, a decorative square of the logo, not an action; nor, since
// 04.8 (amendment 5), the plain button of the header search, site chrome present on every page.
//
// Non-vacuity: the home page and the address page each have their primary action in accent, so
// the measure must find exactly one element there; a broken probe or selector would find zero.

// 04.8-07 (UI-SPEC § 6.6): the v4 h1 of the home; its one plain button is the large field's.
const ACCUEIL: RoutePhase4 = { id: 'accueil', chemin: '/', source: () => [], titre: 'Une adresse, en clair' };
const ROUTES = [ACCUEIL, ...ROUTES_PHASE4];
const AVEC_UNE_ACTION_PRIMAIRE = new Set(['accueil', 'adresse']);

async function ouvrir(page: Page, route: RoutePhase4) {
	await page.route('**/auth/me', (r) =>
		r.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ detail: 'Non authentifié' }) })
	);
	const source = await configureSsrSource(page.request, route.source());
	const hydrated = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
	await page.goto(route.chemin);
	await hydrated;
	if (route.id === 'adresse') {
		// D-33: the street heading and the postal identity are rendered separately.
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('12 rue des Lilas', { timeout: 15_000 });
		await expect(page.locator('.adresse-identity')).toContainText('85000 La Roche-sur-Yon');
	} else {
		await expect(page.getByRole('heading', { level: 1 })).toContainText(route.titre, { timeout: 15_000 });
	}
	await source.assert();
}

/** Visible elements painted with the accent background, outside the exclusions of the UI-SPEC. */
function fondsAccent(page: Page): Promise<{ accent: string; elements: string[] }> {
	return page.evaluate(() => {
		const sonde = document.createElement('div');
		sonde.style.background = 'var(--color-accent)';
		// Public theme is scoped so account screens keep the legacy tokens.
		document.querySelector('.public-site')!.append(sonde);
		const accent = getComputedStyle(sonde).backgroundColor;
		sonde.remove();
		const elements = [...document.querySelectorAll('body *')].filter((el) => {
			if (getComputedStyle(el).backgroundColor !== accent) return false;
			const boite = el.getBoundingClientRect();
			if (boite.width === 0 || boite.height === 0) return false;
			if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
			// Site chrome present on every page: brand mark, Comparer counter and the button of the header
			// search (amendment 5 of 04.8, UI-SPEC § 2.6 n° 5), not the rest of the search.
			if (el.closest('.site-brand-mark') || el.closest('.site-nav-count') || el.closest('.site-menu-count') || el.closest('.recherche-en-tete-bouton')) return false;
			return !/^(Retirer|Placer en premier)/.test((el.textContent ?? '').trim());
		});
		return {
			accent,
			elements: elements.map(
				(el) => `${el.tagName.toLowerCase()}.${String(el.className).slice(0, 60)} « ${(el.textContent ?? '').trim().slice(0, 40)} »`
			)
		};
	});
}

test.beforeEach(async ({ page }) => {
	await installLocalNetworkGuard(page);
});

test.afterEach(async ({ page }) => {
	expectNoUnexpectedOffLocalRequest(page);
});

for (const largeur of [375, 1280]) {
	for (const route of ROUTES) {
		test(`accent-unique — ${route.id} à ${largeur} px : au plus un fond accent`, async ({ page }) => {
			await page.setViewportSize({ width: largeur, height: 900 });
			await ouvrir(page, route);
			const { accent, elements } = await fondsAccent(page);
			expect(accent, 'jeton --color-accent non résolu : la mesure serait vacuous').not.toBe('rgba(0, 0, 0, 0)');
			if (AVEC_UNE_ACTION_PRIMAIRE.has(route.id)) {
				expect(elements, `${route.chemin} : l’action primaire n’est pas comptée, la mesure serait vacuous`).toHaveLength(1);
			}
			expect(elements.length, `${route.chemin} porte plusieurs fonds accent :\n  ${elements.join('\n  ')}`).toBeLessThanOrEqual(1);
		});
	}
}

// Amendment 5 (04.8 UI-SPEC § 2.6 n° 5): the plain button of the header search (RechercheEnTete) is site
// chrome, present on every page, and is not counted, like the brand mark and the Comparer counter. Plain
// since 04.8-02, it is measured as rendered on the address page, whose body already carries its primary
// action (« Comparer avec une autre adresse »). The count stays « at most one » for the rest of the page:
// a second plain button in the body is still reported.
test('accent-unique — adresse à 1280 px : le bouton plein de la recherche d’en-tête n’est pas compté', async ({ page }) => {
	const adresse = ROUTES_PHASE4.find((route) => route.id === 'adresse');
	expect(adresse, 'route adresse absente des surfaces de la phase 4').toBeDefined();
	await page.setViewportSize({ width: 1280, height: 900 });
	await ouvrir(page, adresse!);
	const bouton = page.locator('.recherche-en-tete-bouton');
	await expect(bouton, 'la recherche d’en-tête doit être affichée à 1280 px').toBeVisible();
	const { accent, elements } = await fondsAccent(page);
	// Non-vacuity: the header button really carries the accent background, with no test stylesheet.
	expect(await bouton.evaluate((el) => getComputedStyle(el).backgroundColor)).toBe(accent);
	expect(elements, `fonds accent comptés :\n  ${elements.join('\n  ')}`).toHaveLength(1);

	await page.evaluate(() => {
		const second = document.createElement('button');
		second.type = 'button';
		second.textContent = 'Second bouton plein';
		second.style.background = 'var(--color-accent)';
		second.style.padding = '12px';
		const main = document.querySelector('main');
		if (!main) throw new Error('aucun <main> sur la fiche adresse');
		main.append(second);
	});
	const apres = await fondsAccent(page);
	expect(apres.elements, 'un second bouton plein dans le corps doit encore être compté').toHaveLength(2);
});

// The exclusion is the button only (Opus, P3-13): any other element of the header search painted in accent,
// here the search form itself, is counted with the primary action of the body.
test('accent-unique — adresse à 1280 px : un autre élément de la recherche d’en-tête en accent est compté', async ({ page }) => {
	const adresse = ROUTES_PHASE4.find((route) => route.id === 'adresse');
	expect(adresse, 'route adresse absente des surfaces de la phase 4').toBeDefined();
	await page.setViewportSize({ width: 1280, height: 900 });
	await ouvrir(page, adresse!);
	await page.addStyleTag({ content: 'form.recherche-en-tete { background: var(--color-accent) !important; }' });
	const formulaire = page.locator('form.recherche-en-tete');
	await expect(formulaire, 'la recherche d’en-tête doit être affichée à 1280 px').toBeVisible();
	const { accent, elements } = await fondsAccent(page);
	// Non-vacuity: the form really carries the accent background.
	expect(await formulaire.evaluate((el) => getComputedStyle(el).backgroundColor)).toBe(accent);
	expect(elements, `fonds accent comptés :\n  ${elements.join('\n  ')}`).toHaveLength(2);
	expect(elements.some((el) => el.startsWith('form.recherche-en-tete')), elements.join('\n')).toBe(true);
});
