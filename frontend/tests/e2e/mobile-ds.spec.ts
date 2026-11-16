import AxeBuilder from '@axe-core/playwright';
import { test, expect, type Page } from '@playwright/test';
import { CODE_INSEE, mockAuthAnonyme, mockCommune } from './fixtures/mocks-matrice';

// Source panel of the commune fiche (UI-SPEC « Bouton i et volet », E6; 03-07). It replaced the
// ℹ️ tooltip of Phase 56 (BUG-03 / D-12 / D-13): the defect it fixed — a panel floating over the
// content and trapping the scroll on a phone — is still what this file guards, on the <dialog>:
//   - at 375px the panel is full screen, scrolls inside itself, and the page under it does not
//     move (no scroll trap, no content covered without a way out);
//   - focus goes to its title, stays in the dialog, Escape closes it and gives the focus back to
//     the `i` button that opened it;
//   - at 1440px it is a side panel of at most 552px;
//   - no `i` button carries a native tooltip, and two neighbouring `i` buttons keep their 44px
//     hit areas apart.
// Since 04.8-04 the themes are open panels: the in-flow invariant of Phase 56 is checked on the
// questions of « Questions fréquentes », the one disclosure left in the fiche.
//
// Hermetic: every route is mocked (`mockAuthAnonyme` + `mockCommune`, shared with
// matrix-qualite.spec.ts). `critical` class: no conditional skip, no fixed pause.

const MOBILE = { width: 375, height: 812 } as const;
const DESKTOP = { width: 1440, height: 900 } as const;
const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] as const;

async function naviguerFicheCommune(page: Page, viewport: { width: number; height: number }) {
	await page.setViewportSize(viewport);
	await mockAuthAnonyme(page);
	await mockCommune(page);
	const hydrated = page.waitForResponse((response) => response.url().endsWith('/auth/me'));
	await page.goto(`/commune/${CODE_INSEE}`);
	await hydrated;
	await expect(page.getByRole('heading', { name: 'Challans', exact: true })).toBeVisible();
}

function boutonKpi(page: Page, kpi = 'prix-maisons') {
	return page.locator(`[data-kpi="${kpi}"] .source-button`);
}

function voletOuvert(page: Page) {
	return page.locator('dialog.source-panel[open]');
}

/** The 150ms opening motion is over: boxes and colours are measured at rest, never mid-motion. */
async function auRepos(volet: ReturnType<typeof voletOuvert>) {
	await expect(volet).toBeVisible();
	await volet.evaluate((el) => Promise.all(el.getAnimations({ subtree: true }).map((animation) => animation.finished)));
}

test.describe('Volet source en <dialog> — 375 px (UI-01, E6)', () => {
	test('plein écran, défilement interne, la page dessous ne bouge pas', async ({ page }) => {
		await naviguerFicheCommune(page, MOBILE);
		await page.evaluate(() => window.scrollTo(0, 0));
		await boutonKpi(page).click();
		const volet = voletOuvert(page);
		await auRepos(volet);

		const boite = await volet.boundingBox();
		expect(boite).not.toBeNull();
		expect(Math.round(boite!.x)).toBe(0);
		expect(Math.round(boite!.y)).toBe(0);
		expect(Math.round(boite!.width)).toBe(MOBILE.width);
		expect(Math.round(boite!.height)).toBe(MOBILE.height);

		// The panel scrolls inside itself; the wheel over it never scrolls the page underneath.
		const interieur = volet.locator('.source-panel-inner');
		await interieur.evaluate((el) => {
			(el as HTMLElement).style.paddingBottom = '2000px'; // long content, measured, not a pause
		});
		const avant = await page.evaluate(() => window.scrollY);
		await page.mouse.move(MOBILE.width / 2, MOBILE.height / 2);
		await page.mouse.wheel(0, 600);
		await expect.poll(() => interieur.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
		expect(await page.evaluate(() => window.scrollY)).toBe(avant);
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(MOBILE.width);
	});

	test('focus sur le titre, gardé dans le dialogue, Échap ferme et rend le focus au bouton i', async ({ page }) => {
		await naviguerFicheCommune(page, MOBILE);
		const bouton = boutonKpi(page);
		await bouton.click();
		const volet = voletOuvert(page);
		await expect(volet.getByRole('heading', { level: 2 })).toBeFocused();

		for (let i = 0; i < 6; i++) {
			await page.keyboard.press('Tab');
			expect(await page.evaluate(() => Boolean(document.activeElement?.closest('dialog[open]')))).toBe(true);
		}
		await page.keyboard.press('Escape');
		await expect(voletOuvert(page)).toHaveCount(0);
		await expect(bouton).toBeFocused();
	});

	test('« Fermer » mesure 44 px et rend le focus ; un seul volet ouvert à la fois', async ({ page }) => {
		await naviguerFicheCommune(page, MOBILE);
		const bouton = boutonKpi(page, 'dpe-dominant');
		await bouton.click();
		await expect(voletOuvert(page)).toHaveCount(1);
		const fermer = voletOuvert(page).getByRole('button', { name: 'Fermer', exact: true });
		const taille = await fermer.boundingBox();
		expect(taille!.height).toBeGreaterThanOrEqual(44);
		await fermer.click();
		await expect(voletOuvert(page)).toHaveCount(0);
		await expect(bouton).toBeFocused();
	});

	test('un clic sur le fond referme le volet et rend le focus ; un clic dans le volet ne le referme pas', async ({ page }) => {
		// Owner acceptance, 2026-09-28: the panel closes on a click outside it, not only by its button.
		await naviguerFicheCommune(page, DESKTOP);
		const bouton = boutonKpi(page);
		await bouton.click();
		await expect(voletOuvert(page)).toHaveCount(1);
		await auRepos(voletOuvert(page));

		await voletOuvert(page).locator('.source-panel-title').click();
		const boite = (await voletOuvert(page).boundingBox())!;
		await page.mouse.click(boite.x + boite.width / 2, boite.y + boite.height - 8);
		await expect(voletOuvert(page)).toHaveCount(1);

		// The backdrop is the dimmed page on the left of the 552px side panel.
		expect(boite.x).toBeGreaterThan(100);
		await page.mouse.click(40, DESKTOP.height / 2);
		await expect(voletOuvert(page)).toHaveCount(0);
		await expect(bouton).toBeFocused();
	});

	test('volet ouvert : aucune violation axe WCAG 2.1 AA serious/critical', async ({ page }) => {
		await naviguerFicheCommune(page, MOBILE);
		await boutonKpi(page).click();
		await auRepos(voletOuvert(page));
		// The page under the modal is inert and dimmed by the backdrop: axe measures the panel the
		// visitor reads. The page at rest is measured by matrix-qualite.spec.ts.
		const resultats = await new AxeBuilder({ page }).include('dialog.source-panel[open]').withTags([...TAGS_WCAG]).analyze();
		expect(resultats.passes.length, 'axe inspected nothing').toBeGreaterThan(0);
		expect(resultats.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([]);
	});

	test('aucun title sur un bouton i ; deux boutons i voisins gardent chacun 44 px sans recouvrement', async ({ page }) => {
		await naviguerFicheCommune(page, MOBILE);
		// The button box is the hit area; the « i » inside it is what the eye sees.
		const zones = await page.locator('.kpi .source-button').evaluateAll((boutons) =>
			boutons.map((bouton) => {
				const rect = bouton.getBoundingClientRect();
				const icone = bouton.querySelector('svg')!.getBoundingClientRect();
				return {
					title: bouton.getAttribute('title'),
					largeur: rect.width, hauteur: rect.height,
					icone: icone.width,
					x1: rect.left, y1: rect.top, x2: rect.right, y2: rect.bottom
				};
			})
		);
		// 4.9 lot 3: « L'essentiel » shows the known markers only, two on this fixture.
		expect(zones).toHaveLength(2);
		for (const zone of zones) {
			expect(zone.title).toBeNull();
			expect(zone.largeur).toBeGreaterThanOrEqual(44);
			expect(zone.hauteur).toBeGreaterThanOrEqual(44);
			expect(zone.icone).toBe(20);
		}
		for (let i = 0; i < zones.length; i++) {
			for (let j = i + 1; j < zones.length; j++) {
				const a = zones[i];
				const b = zones[j];
				const recouvre = a.x1 < b.x2 && b.x1 < a.x2 && a.y1 < b.y2 && b.y1 < a.y2;
				expect(recouvre, `zones ${i} et ${j} se recouvrent`).toBe(false);
			}
		}
	});

	test('détails de la fiche (04.8-04) : ouvrir une question la déplie en flux et pousse le contenu', async ({ page }) => {
		await naviguerFicheCommune(page, MOBILE);
		// Since 04.8-04 (UI-SPEC v4 § 6.2) the five themes are open panels; what unfolds in the page is a
		// question of « Questions fréquentes » (a row's definition opens in the <dialog> source panel,
		// tested above). It must stay in the flow and never overlay the page.
		const question = page.locator('[data-faq] details').first();
		await expect(question, 'aucune question repliée — mesure vacuous').toBeAttached();
		await expect(question).not.toHaveAttribute('open');
		const repere = page.locator('footer.site-footer');
		const yAvant = await repere.evaluate((el) => el.getBoundingClientRect().top + window.scrollY);
		await question.locator(':scope > summary').click();
		await expect(question).toHaveAttribute('open', '');
		const reponse = question.locator(':scope > p');
		await expect(reponse).toBeVisible();
		expect(await reponse.evaluate((el) => getComputedStyle(el).position)).toBe('static');
		const hauteur = await reponse.evaluate((el) => el.getBoundingClientRect().height);
		const yApres = await repere.evaluate((el) => el.getBoundingClientRect().top + window.scrollY);
		expect(yApres - yAvant).toBeGreaterThanOrEqual(hauteur - 1);
		expect(await page.evaluate(() => document.body.scrollWidth)).toBeLessThanOrEqual(MOBILE.width + 1);
	});

	// Codex review of the fixes of 04.5-08: a theme closed by the reader had to reopen from its link.
	// Since 4.9 the rows of a theme sit in a category, a closed fold at this width: the former anchor of
	// the theme lands on its group, opens its category, at any moment of the reading, and a navigation
	// never folds a category the reader opened.
	test('un thème atteint par son ancre est ouvert, à l’écran, et ne se replie pas', async ({ page }) => {
		await naviguerFicheCommune(page, MOBILE);
		const immobilier = page.locator('#critere-prix_commune');
		const services = page.locator('#critere-equipements');
		const atteints = new Set<typeof services>();
		for (const [ancre, panneau] of [['services-transports', services], ['immobilier', immobilier], ['services-transports', services]] as const) {
			await page.evaluate((id) => { window.location.hash = id; }, ancre);
			await expect(page).toHaveURL(new RegExp(`#${ancre}$`));
			// The smooth scroll settles with the top of the panel under the sticky header (scroll-padding-top),
			// before the next jump: a jump in the middle of a smooth scroll measures nothing.
			await expect
				.poll(() => panneau.evaluate(async (el) => {
					// At rest: the same top over two frames, under the header.
					const top = el.getBoundingClientRect().top;
					await new Promise((fin) => requestAnimationFrame(() => requestAnimationFrame(fin)));
					return top === el.getBoundingClientRect().top && top >= 0 && top <= 120;
				}), {
					message: `${ancre} posé sous l’en-tête`
				})
				.toBe(true);
			await expect(panneau).toBeInViewport();
			await expect(panneau.locator('dl.tab > .tab-l').first()).toBeVisible();
			// The category that holds the group is open; the one reached before stays open (a navigation
			// never folds what the reader opened).
			await expect(panneau.locator('xpath=ancestor::details[contains(concat(" ", @class, " "), " cat ")]')).toHaveJSProperty('open', true);
			atteints.add(panneau);
			for (const atteint of atteints) await expect(atteint.locator('dl.tab').first()).toBeVisible();
		}
	});
});

test.describe('Volet source en <dialog> — 1 440 px', () => {
	test('volet latéral de 552 px au plus, calé à droite, sans débordement de la page', async ({ page }) => {
		await naviguerFicheCommune(page, DESKTOP);
		await boutonKpi(page).click();
		const volet = voletOuvert(page);
		await auRepos(volet);
		const boite = await volet.boundingBox();
		expect(boite!.width).toBeLessThanOrEqual(552);
		expect(Math.round(boite!.x + boite!.width)).toBe(DESKTOP.width);
		expect(Math.round(boite!.height)).toBe(DESKTOP.height);
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(DESKTOP.width);
		await page.keyboard.press('Escape');
		await expect(voletOuvert(page)).toHaveCount(0);
		await expect(boutonKpi(page)).toBeFocused();
	});
});
