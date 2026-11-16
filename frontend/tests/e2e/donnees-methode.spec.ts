import AxeBuilder from '@axe-core/playwright';
import { GROUPES_CATALOGUE, groupeDuJeu } from '../../src/lib/data/catalogue-groupes';
import { sansDeveloppement } from '../../src/lib/utils/sigles';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { test, expect, type APIRequestContext, type Locator, type Page, type TestInfo } from '@playwright/test';
import { E2E_APP_ORIGIN, E2E_SOURCE_ORIGIN } from './fixtures/ports';
import {
	expectNoUnexpectedOffLocalRequest,
	installLocalNetworkGuard
} from './fixtures/local-network';

// Phase 03-11 (D-40, UI-01, COM-02) — « Données & méthode », served by `GET /v1/datasets`.
//
// What this spec locks:
//   - the page is rendered on the server from the API answer of this instance (the synthetic
//     source answers `/v1/datasets` under an exact expectation) and no browser request leaves
//     the local environment;
//   - since the review of 2026-11-11 (Ianis, internal note 2026-11-11-page-donnees-methode) three
//     entries « Les sources », « Lire une fiche » and « Couverture » come first, then three panels in this
//     order: the catalogue, grouped in the reading order of the address fiche; the one glossary « Lire une
//     fiche », which holds the four states; the coverage, which lists the departements served by the
//     API, whatever they are: nothing in the screen knows which ones the instance hosts (D-34, D-35);
//   - every row gives name, producer and state; the detail of the source read (`?jeu=<key>`, plan
//     04.5-11, B7) gives published licence and link, period, cadence, last import, coverage, reuse
//     conditions; the DVF reuse restrictions sit in their own block, apart from the limits of the
//     source (LIC-01, LIC-02); `encadrement_loyers` and `permis` carry « Non qualifié » and the
//     reason served by the API (COM-02: a quarantine is never hidden);
//   - chips « Tout » and one per group of the catalogue with their counts (2026-11-11; they replace the
//     state chips Tous / Disponibles / Hors couverture / Non qualifiés), a chip at 0 hidden unless it is
//     active (lot 8 of 4.9), the empty filter and its return to « Tout »; a row says its freshness in plain
//     text and keeps a badge for the exceptions (not qualified, out of coverage, not imported yet);
//   - an outage of `/v1/datasets` shows one `warn` notice and keeps the glossary and the scope of
//     objects, and « Recharger la page » reads the API again;
//   - the former detail pages answer 308 towards their card, an unknown slug 404 (H4);
//   - 375 px without horizontal overflow; axe WCAG 2.1 AA without violation.

const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] as const;

type Jeu = {
	nom: string;
	statut: string;
	provenance: {
		nom: string;
		producteur: string;
		url: string;
		periode?: { statut: string; label?: string | null };
		derniere_importation_reussie?: string | null;
	} | null;
	hebergement: { etat: string; motif: string | null; departements: string[] } | null;
	reutilisation: {
		licence: string;
		licence_url: string;
		conditions?: string;
		restrictions: string[];
	} | null;
	limites_source: { chapeau: string; puces: string[] } | null;
	quarantaine: string | null;
};
type Reponse = { statut: string; donnees: Jeu[] };
type Reply = { status: number; body: unknown };

const FIXTURE = JSON.parse(
	readFileSync(new URL('../../src/lib/data/__fixtures__/datasets-api.json', import.meta.url), 'utf8')
) as Reponse;
const jeu = (reponse: Reponse, nom: string) => reponse.donnees.find((item) => item.nom === nom)!;

/** The same answer with every hosted dataset served on other departements. */
function servieSur(departements: string[]): Reponse {
	return {
		...FIXTURE,
		donnees: FIXTURE.donnees.map((item) =>
			item.hebergement?.etat === 'heberge'
				? { ...item, hebergement: { ...item.hebergement, departements } }
				: item
		)
	};
}

/** Chip of a served entry, read as the page reads it: the title of its group of the catalogue. */
function puce(item: Jeu): string {
	return GROUPES_CATALOGUE.find((groupe) => groupe.id === groupeDuJeu(item.nom))!.titre;
}

/** The chips of the groups, in the order of the page. */
const TITRES_GROUPES = GROUPES_CATALOGUE.map((groupe) => groupe.titre);
const duGroupe = (reponse: Reponse, titre: string) => reponse.donnees.filter((item) => puce(item) === titre);

function compteSources(n: number): string {
	return `${n} ${n <= 1 ? 'source' : 'sources'}`;
}

async function configure(request: APIRequestContext, reponses: Reply[], minCalls = reponses.length) {
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
		data: {
			reset: true,
			expectations: [
				{
					method: 'GET',
					pathname: '/v1/datasets',
					query: {},
					minCalls,
					maxCalls: reponses.length,
					responses: reponses
				}
			]
		}
	});
	expect(res.ok()).toBe(true);
}

async function expectSourceContractMet(request: APIRequestContext) {
	const res = await request.get(`${E2E_SOURCE_ORIGIN}/__requests`);
	expect(res.ok()).toBe(true);
	const audit = (await res.json()) as { violations: unknown[]; unmetExpectations: unknown[] };
	expect(audit.violations, JSON.stringify(audit.violations)).toEqual([]);
	expect(audit.unmetExpectations, JSON.stringify(audit.unmetExpectations)).toEqual([]);
}

/** Navigates and waits for hydration (the root layout reads the session once hydrated). */
async function open(page: Page, url: string) {
	const hydrated = page.waitForResponse(
		(response) => new URL(response.url()).pathname === '/auth/me'
	);
	await page.goto(url);
	await hydrated;
}

function section(page: Page, name: string) {
	return page.getByRole('region', { name, exact: true });
}

function carte(page: Page, nom: string) {
	return page.locator(`article[id="${nom}"]`);
}

function chip(page: Page, libelle: string) {
	return page
		.getByRole('group', { name: 'Filtrer les sources par thème' })
		.getByRole('button', { name: new RegExp(`^${libelle} · `) });
}

/** The one rendering of the metadata of the chosen source (DatasetDetail). */
function detail(page: Page) {
	return page.locator('main .dataset-detail');
}

/** The detail of DVF: every fact, its links and its blocks, in place under its row at every width (lot 8 of 4.9). */
async function expectDetailDvf(page: Page) {
	const dvf = jeu(FIXTURE, 'dvf');
	const corps = detail(page);
	await expect(corps).toHaveCount(1);
	await expect(corps).toBeVisible();
	await expect(corps.locator('dt')).toHaveText([
		'Licence',
		'Source',
		'Période amont',
		'Dernière importation réussie',
		'Cadence',
		'Couverture'
	]);
	await expect(corps.getByRole('link', { name: dvf.reutilisation!.licence })).toHaveAttribute('href', dvf.reutilisation!.licence_url);
	// 04.8-08 (D-31): the served conditions, read behind the full form the page adds to an acronym first said there.
	expect(sansDeveloppement(await corps.getByRole('region', { name: 'Conditions de réutilisation' }).innerText())).toContain(
		dvf.reutilisation!.conditions!
	);
	await expect(corps.getByRole('region', { name: 'Restrictions de réutilisation' }).getByRole('listitem')).toHaveText(
		dvf.reutilisation!.restrictions
	);
	await expect(corps.getByRole('region', { name: 'Limites de la source' }).getByRole('listitem')).toHaveText(dvf.limites_source!.puces);
	// Both boxes read at once: a scroll between two reads (reload, restoration) would skew them.
	const { ligne, boite } = await page.evaluate(() => {
		const rect = (element: Element) => {
			const { x, y, width, height } = element.getBoundingClientRect();
			return { x, y, width, height };
		};
		return { ligne: rect(document.querySelector('main article#dvf')!), boite: rect(document.querySelector('main .dataset-detail')!) };
	});
	// Directly under its row, with no second fold (review of 09/11); nothing beside the list.
	await expect(page.locator('main .dataset-ouvert > .dataset-detail')).toBeVisible();
	await expect(page.locator('main .dataset-ouvert details, main .dataset-ouvert summary, main details.dataset-ouvert')).toHaveCount(0);
	expect(boite.y, JSON.stringify({ ligne, boite })).toBeGreaterThanOrEqual(ligne.y + ligne.height - 1);
	expect(Math.abs(boite.x - ligne.x), JSON.stringify({ ligne, boite })).toBeLessThanOrEqual(24);
}

const ACCES_DIRECTS = [
	{ label: 'Les sources', href: '#catalogue-titre', target: 'catalogue-titre' },
	{ label: 'Lire une fiche', href: '#lire-titre', target: 'lire-titre' },
	{ label: 'Couverture', href: '#couverture-titre', target: 'couverture-titre' }
] as const;

function evidencePath(name: string): string {
	const directory = process.env.DC_METHOD_NAV_EVIDENCE_DIR;
	if (!directory) return test.info().outputPath(name);
	mkdirSync(directory, { recursive: true });
	return join(directory, name);
}

function catalogueEvidencePath(name: string): string {
	const directory = process.env.DC_CATALOGUE_EVIDENCE_DIR;
	if (!directory) return test.info().outputPath(name);
	mkdirSync(directory, { recursive: true });
	return join(directory, name);
}

function publicMessagesEvidencePath(testInfo: TestInfo, name: string): string {
	const directory = process.env.DC_PUBLIC_MESSAGES_EVIDENCE_DIR;
	if (!directory) return testInfo.outputPath(name);
	mkdirSync(directory, { recursive: true });
	return join(directory, name);
}

function accesDirects(page: Page) {
	return page.getByRole('navigation', { name: /accès direct/i });
}

async function expectWholeWords(locator: Locator, label: string) {
	const split = await locator.evaluateAll((roots) => {
		const failures: string[] = [];
		for (const root of roots) {
			const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
			for (let node = walker.nextNode(); node; node = walker.nextNode()) {
				const text = node.textContent ?? '';
				for (const match of text.matchAll(/[\p{L}\p{N}][\p{L}\p{M}\p{N}'’.-]*/gu)) {
					const range = document.createRange();
					range.setStart(node, match.index ?? 0);
					range.setEnd(node, (match.index ?? 0) + match[0].length);
					const tops = [...range.getClientRects()].map((rect) => Math.round(rect.top));
					if (new Set(tops).size > 1) failures.push(match[0]);
				}
			}
		}
		return failures;
	});
	expect(split, `${label} garde ses mots entiers`).toEqual([]);
}

async function expectNoHorizontalOverflow(page: Page) {
	const widths = await page.evaluate(() => ({
		document: document.documentElement.scrollWidth,
		viewport: window.innerWidth
	}));
	expect(widths.document, JSON.stringify(widths)).toBeLessThanOrEqual(widths.viewport);
}

async function expectTarget44(locator: Locator, label: string) {
	const box = await locator.boundingBox();
	expect(box, `${label} est mesurable`).toBeTruthy();
	expect(box!.height, `${label} atteint 44 px de haut`).toBeGreaterThanOrEqual(44);
	expect(box!.width, `${label} atteint 44 px de large`).toBeGreaterThanOrEqual(44);
}

async function reachCatalogueFromDirectLink(page: Page, width: number) {
	const link = accesDirects(page).getByRole('link', { name: 'Les sources', exact: true });
	await link.focus();
	await expect(link).toBeFocused();
	await link.press('Enter');
	await expect(page).toHaveURL(/#catalogue-titre$/);

	const target = page.locator('#catalogue-titre');
	await expect(target).toBeFocused();
	await expect(target).toHaveCSS('outline-style', 'solid');
	await expect(target).toHaveCSS('outline-width', '3px');
	await expect.poll(
		async () =>
			target.evaluate((element) => {
				const box = element.getBoundingClientRect();
				const headerBottom = document.querySelector('header.site-header')!.getBoundingClientRect().bottom;
				const style = getComputedStyle(element);
				const outlineWidth = Number.parseFloat(style.outlineWidth);
				const outlineOffset = Number.parseFloat(style.outlineOffset);
				const hit = document.elementFromPoint(box.left + Math.min(box.width / 2, 32), box.top + box.height / 2);
				return (
					element.matches(':focus-visible') &&
					box.top - outlineWidth - outlineOffset >= headerBottom &&
					box.bottom + outlineWidth + outlineOffset <= window.innerHeight &&
					!!hit &&
					(hit === element || element.contains(hit))
				);
			}),
		{ message: `le titre du catalogue et son anneau restent visibles sous l’en-tête à ${width} px` }
	).toBe(true);

	const label = page.getByText('Rechercher une source ou un producteur', { exact: true });
	const search = page.getByRole('searchbox', { name: 'Rechercher une source ou un producteur' });
	await expect(label).toBeVisible();
	await expect(search).toBeVisible();
	for (const [name, locator] of [
		['label', label],
		['champ', search]
	] as const) {
		const exposed = await locator.evaluate((element) => {
			const box = element.getBoundingClientRect();
			const headerBottom = document.querySelector('header.site-header')!.getBoundingClientRect().bottom;
			const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
			return {
				top: box.top,
				bottom: box.bottom,
				headerBottom,
				viewportHeight: window.innerHeight,
				hit: !!hit && (hit === element || element.contains(hit) || hit.contains(element))
			};
		});
		expect(exposed.top, `${name} à ${width} px : ${JSON.stringify(exposed)}`).toBeGreaterThanOrEqual(
			exposed.headerBottom
		);
		expect(exposed.bottom, `${name} à ${width} px : ${JSON.stringify(exposed)}`).toBeLessThanOrEqual(
			exposed.viewportHeight
		);
		expect(exposed.hit, `${name} à ${width} px : ${JSON.stringify(exposed)}`).toBe(true);
	}
}

test.beforeEach(async ({ page }) => {
	await installLocalNetworkGuard(page);
});

test.afterEach(async ({ page, request }) => {
	await expectSourceContractMet(request);
	expectNoUnexpectedOffLocalRequest(page);
});

test.describe('Données & méthode — servie par /v1/datasets', () => {
	test('trois accès directs : ancres uniques, clavier, focus visible et géométrie à 1 440 et 375 px', async ({
		page,
		request
	}) => {
		await configure(request, [{ status: 200, body: FIXTURE }, { status: 200, body: FIXTURE }]);
		await page.emulateMedia({ reducedMotion: 'reduce' });

		for (const width of [1440, 375] as const) {
			const height = width === 1440 ? 900 : 800;
			await page.setViewportSize({ width, height });
			await open(page, '/datasets');
			expect(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(true);

			const nav = accesDirects(page);
			await expect(nav).toBeVisible();
			const links = nav.getByRole('link');
			await expect(links).toHaveCount(3);
			for (const item of ACCES_DIRECTS) {
				const link = nav.getByRole('link', { name: item.label, exact: true });
				await expect(link).toHaveAttribute('href', item.href);
				const box = await link.boundingBox();
				expect(box, `${item.label} est mesurable à ${width} px`).toBeTruthy();
				expect(box!.height, `${item.label} cible de 44 px à ${width} px`).toBeGreaterThanOrEqual(44);
				expect(box!.width, `${item.label} cible de 44 px à ${width} px`).toBeGreaterThanOrEqual(44);
				expect(await page.locator(`#${item.target}`).count(), `${item.target} est unique`).toBe(1);

				await link.focus();
				await expect(link).toBeFocused();
				await link.press('Enter');
				await expect(page).toHaveURL(new RegExp(`#${item.target}$`));
				const target = page.locator(`#${item.target}`);
				await expect(target).toBeFocused();
				await expect(target).toHaveCSS('outline-style', 'solid');
				await expect(target).toHaveCSS('outline-width', '3px');
				const placement = await target.evaluate((element) => {
					const targetBox = element.getBoundingClientRect();
					const headerBox = document.querySelector('header.site-header')!.getBoundingClientRect();
					const style = getComputedStyle(element);
					const outlineWidth = Number.parseFloat(style.outlineWidth);
					const outlineOffset = Number.parseFloat(style.outlineOffset);
					const x = targetBox.left + Math.min(targetBox.width / 2, 24);
					const y = targetBox.top + Math.min(targetBox.height / 2, 12);
					const hit = document.elementFromPoint(x, y);
					return {
						top: targetBox.top,
						bottom: targetBox.bottom,
						ringTop: targetBox.top - outlineWidth - outlineOffset,
						ringBottom: targetBox.bottom + outlineWidth + outlineOffset,
						headerBottom: headerBox.bottom,
						viewportHeight: window.innerHeight,
						hit: !!hit && (hit === element || element.contains(hit)),
						focusVisible: element.matches(':focus-visible'),
						outlineStyle: style.outlineStyle,
						outlineWidth
					};
				});
				expect(placement.top, JSON.stringify(placement)).toBeGreaterThanOrEqual(placement.headerBottom);
				expect(placement.bottom, JSON.stringify(placement)).toBeLessThanOrEqual(placement.viewportHeight);
				expect(placement.hit, JSON.stringify(placement)).toBe(true);
				expect(placement.focusVisible, JSON.stringify(placement)).toBe(true);
				expect(placement.outlineStyle, JSON.stringify(placement)).not.toBe('none');
				expect(placement.outlineWidth, JSON.stringify(placement)).toBeGreaterThanOrEqual(3);
				expect(placement.ringTop, JSON.stringify(placement)).toBeGreaterThanOrEqual(placement.headerBottom);
				expect(placement.ringBottom, JSON.stringify(placement)).toBeLessThanOrEqual(placement.viewportHeight);
				if (item.target === 'lire-titre') {
					await page.screenshot({ path: evidencePath(`methode-cible-etats-${width}.png`), animations: 'disabled' });
				}
			}

			await expectWholeWords(nav, `accès directs à ${width} px`);
			await expectNoHorizontalOverflow(page);
			const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
			expect(axe.violations, JSON.stringify(axe.violations, null, 2)).toEqual([]);

			await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
			await page.screenshot({ path: evidencePath(`methode-header-${width}.png`), animations: 'disabled' });
			await nav.evaluate((element) => element.scrollIntoView({ block: 'center', behavior: 'instant' }));
			await nav.screenshot({ path: evidencePath(`methode-acces-${width}.png`), animations: 'disabled' });
		}
	});

	test('sans JavaScript : le HTML complet garde les trois liens et leurs cibles', async ({ browser, request }) => {
		await configure(request, [{ status: 200, body: FIXTURE }]);
		const context = await browser.newContext({ javaScriptEnabled: false, baseURL: E2E_APP_ORIGIN, viewport: { width: 375, height: 800 } });
		try {
			const page = await context.newPage();
			await installLocalNetworkGuard(page);
			const response = await page.goto('/datasets');
			expect(response?.status()).toBe(200);
			const nav = accesDirects(page);
			await expect(nav.getByRole('link')).toHaveCount(3);
			for (const item of ACCES_DIRECTS) {
				const link = nav.getByRole('link', { name: item.label, exact: true });
				await expect(link).toHaveAttribute('href', item.href);
				const target = page.locator(`#${item.target}`);
				await expect(target).toHaveCount(1);
				await link.focus();
				await expect(link).toBeFocused();
				await link.press('Enter');
				await expect(page).toHaveURL(new RegExp(`#${item.target}$`));
				await expect(target).toBeFocused();
				await expect.poll(async () => target.evaluate((element) => {
					const box = element.getBoundingClientRect();
					const headerBottom = document.querySelector('header.site-header')!.getBoundingClientRect().bottom;
					return box.top >= headerBottom && box.bottom <= window.innerHeight;
				}), { message: `${item.label} reste visible sous l’en-tête sans JavaScript` }).toBe(true);
				const placement = await target.evaluate((element) => ({
					top: element.getBoundingClientRect().top,
					bottom: element.getBoundingClientRect().bottom,
					headerBottom: document.querySelector('header.site-header')!.getBoundingClientRect().bottom,
					viewportHeight: window.innerHeight
				}));
				expect(placement.top, JSON.stringify(placement)).toBeGreaterThanOrEqual(placement.headerBottom);
				expect(placement.bottom, JSON.stringify(placement)).toBeLessThanOrEqual(placement.viewportHeight);
			}
			await expect(page.locator('article#dvf')).toBeVisible();
			await expectNoHorizontalOverflow(page);
		} finally {
			await context.close();
		}
	});

	test('sans JavaScript et en panne : Lire une fiche et Couverture restent, Les sources disparaît', async ({
		browser,
		request
	}) => {
		await configure(request, [{ status: 503, body: { detail: 'Service indisponible' } }]);
		const context = await browser.newContext({ javaScriptEnabled: false, baseURL: E2E_APP_ORIGIN, viewport: { width: 375, height: 800 } });
		try {
			const page = await context.newPage();
			await installLocalNetworkGuard(page);
			const response = await page.goto('/datasets');
			// R-21 (2026-11-10): an outage of the catalogue is answered 503 with Retry-After, the page still
			// rendered, so that a robot or a cache never keeps it as a 200 (04.7-10, decision 11).
			expect(response?.status()).toBe(503);
			expect(response?.headers()['retry-after']).toMatch(/^\d+$/);
			const nav = accesDirects(page);
			await expect(nav.getByRole('link')).toHaveCount(2);
			await expect(nav.getByRole('link', { name: 'Couverture', exact: true })).toHaveAttribute('href', '#couverture-titre');
			await expect(nav.getByRole('link', { name: 'Lire une fiche', exact: true })).toHaveAttribute('href', '#lire-titre');
			await expect(nav.getByRole('link', { name: 'Les sources', exact: true })).toHaveCount(0);
			await expect(page.locator('#catalogue-titre')).toHaveCount(0);
			await expect(page.locator('#couverture-titre')).toBeVisible();
			await expect(page.locator('#lire-titre')).toBeVisible();
			// The notice of the outage is read before the glossary.
			await expect(page.locator('main section.panneau > h2')).toHaveText(['Couverture', 'Lire une fiche']);
			await page.screenshot({ path: evidencePath('methode-panne-nojs-375.png'), animations: 'disabled' });
		} finally {
			await context.close();
		}
	});

	test('recherche locale : accents, casse, thèmes et groupes se combinent sans relire l’API', async ({
		page,
		request
	}) => {
		test.slow();
		await configure(request, [{ status: 200, body: FIXTURE }, { status: 200, body: FIXTURE }]);
		const energie = ['batiments', 'bdnb', 'dpe'];
		const jeuxEnergie = FIXTURE.donnees.filter((item) => energie.includes(item.nom));
		const compteEnergie = (libelle: string) =>
			libelle === 'Tout'
				? jeuxEnergie.length
				: jeuxEnergie.filter((item) => puce(item) === libelle).length;

		for (const width of [1440, 375] as const) {
			await page.setViewportSize({ width, height: width === 1440 ? 980 : 900 });
			await open(page, width === 1440 ? '/datasets' : '/datasets?viewport=375');
			const catalogue = section(page, 'Les sources');
			const search = page.getByRole('searchbox', {
				name: 'Rechercher une source ou un producteur'
			});
			await expect(search).toBeVisible();
			await expectTarget44(search, `recherche catalogue à ${width} px`);
			await reachCatalogueFromDirectLink(page, width);
			await page.screenshot({
				path: catalogueEvidencePath(`catalogue-tous-${width}.png`),
				animations: 'disabled'
			});

			await search.fill('energie');
			await expect(page.locator('main article')).toHaveCount(jeuxEnergie.length);
			for (const key of energie) await expect(carte(page, key)).toBeVisible();
			expect(TITRES_GROUPES.filter((titre) => compteEnergie(titre) > 0).length, 'un groupe au moins sous cette recherche').toBeGreaterThan(0);
			for (const libelle of ['Tout', ...TITRES_GROUPES]) {
				// Lot 8 of 4.9: a filter at 0 is hidden, and the list stays reachable by the others.
				if (compteEnergie(libelle) === 0) {
					await expect(chip(page, libelle)).toHaveCount(0);
					continue;
				}
				await expect(chip(page, libelle)).toHaveText(`${libelle} · ${compteEnergie(libelle)}`);
				await chip(page, libelle).click();
				await expect(page.locator('main article')).toHaveCount(compteEnergie(libelle));
			}
			await chip(page, 'Tout').click();
			await page.screenshot({
				path: catalogueEvidencePath(`catalogue-recherche-${width}.png`),
				animations: 'disabled'
			});

			await search.fill('ademe');
			await expect(page.locator('main article')).toHaveCount(1);
			await expect(carte(page, 'dpe')).toBeVisible();
			await search.fill('DIAGNOSTICS DE PERFORMANCE ENERGETIQUE');
			await expect(page.locator('main article')).toHaveCount(1);
			await expect(carte(page, 'dpe')).toBeVisible();

			await search.fill('jeu qui ne figure pas au catalogue');
			await expect(page.locator('main article')).toHaveCount(0);
			await expect(catalogue.getByText(/aucune source/i)).toBeVisible();
			await page.screenshot({
				path: catalogueEvidencePath(`catalogue-vide-${width}.png`),
				animations: 'disabled'
			});

			const clear = page.getByRole('button', { name: 'Effacer la recherche' });
			await expectTarget44(clear, `effacer la recherche à ${width} px`);
			await clear.focus();
			await expect(clear).toBeFocused();
			await clear.press('Enter');
			await expect(search).toHaveValue('');
			await expect(search).toBeFocused();
			await expect(page.locator('main article')).toHaveCount(FIXTURE.donnees.length);

			// The identity of each row is its name (h4, under the h3 of its group) and its producer line (p, direct
			// child of the article): counted first, so that the inspection cannot pass on an empty selection.
			const noms = page.locator('main article > h4');
			const producteurs = page.locator('main article > p');
			await expect(noms).toHaveCount(FIXTURE.donnees.length);
			await expect(producteurs).toHaveCount(FIXTURE.donnees.length);
			await expectWholeWords(
				catalogue.locator('.methode-recherche, .methode-filtres, .catalogue-groupe-tete').or(noms).or(producteurs),
				`commandes et identités du catalogue à ${width} px`
			);
			await expectNoHorizontalOverflow(page);
			const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
			expect(axe.violations, JSON.stringify(axe.violations, null, 2)).toEqual([]);
		}
	});

	// The server paints the field before the page is hydrated: on a slow phone the reader types into it
	// first. Held back here so the order is the same at every run.
	test('une saisie faite avant l’hydratation reste dans le champ, filtre la liste et entre dans l’URL', async ({
		page,
		request
	}) => {
		await configure(request, [{ status: 200, body: FIXTURE }]);
		let liberer = () => {};
		const retenue = new Promise<void>((resolve) => (liberer = resolve));
		await page.route('**/.svelte-kit/generated/client/app.js*', async (route) => {
			await retenue;
			await route.continue();
		});
		const hydrated = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
		await page.goto('/datasets');
		const search = page.getByRole('searchbox', { name: 'Rechercher une source ou un producteur' });
		await search.fill('energie');
		await expect(page.locator('main article')).toHaveCount(FIXTURE.donnees.length);

		liberer();
		await hydrated;
		await expect(page.locator('main article')).toHaveCount(3);
		await expect(search).toHaveValue('energie');
		await expect(page).toHaveURL(/[?&]q=energie(&|$)/);
	});

	// Plan 04.5-11 (B7) replaces « les détails natifs séparent licence, source, période amont et import
	// sans masquer le sens »: the card no longer folds four facts behind « Voir les détails du jeu »; the
	// detail of the chosen source shows them all. Kept: every fact and its links, the conditions apart
	// from the restrictions, the keyboard, whole words and no overflow, at both widths.
	test('le détail de la source choisie montre licence, source, période, import, cadence, couverture, conditions, restrictions et limites', async ({
		page,
		request
	}) => {
		test.slow();
		await configure(request, [{ status: 200, body: FIXTURE }, { status: 200, body: FIXTURE }]);
		const dvfData = jeu(FIXTURE, 'dvf');

		for (const width of [1440, 375] as const) {
			await page.setViewportSize({ width, height: width === 1440 ? 1100 : 1000 });
			// A second URL at 375: the same URL with a fragment would not load the page again.
			await open(page, width === 1440 ? '/datasets?jeu=dvf#dvf' : '/datasets?jeu=dvf&viewport=375#dvf');
			const ligne = carte(page, 'dvf');
			const corps = detail(page);
			await expectDetailDvf(page);
			await expect(ligne.getByRole('link', { name: dvfData.provenance!.nom })).toHaveAttribute('aria-current', 'true');
			for (const dt of await corps.locator('dt').all()) await expect(dt).toBeVisible();
			await expect(corps.getByRole('link', { name: 'Page du producteur' })).toHaveAttribute('href', dvfData.provenance!.url);
			await expect(corps).toContainText(dvfData.provenance!.periode!.label!);
			const conditions = corps.getByRole('region', { name: 'Conditions de réutilisation' });
			await expect(conditions).toBeVisible();
			for (const restriction of dvfData.reutilisation!.restrictions) {
				await expect(conditions).not.toContainText(restriction);
			}

			// Keyboard: from the link of its row, the next stops are those of its detail.
			const lien = ligne.getByRole('link', { name: dvfData.provenance!.nom });
			await lien.focus();
			await page.keyboard.press('Tab');
			await expect(corps.getByRole('link', { name: dvfData.reutilisation!.licence })).toBeFocused();

			await corps.evaluate((element) => element.scrollIntoView({ block: 'start', behavior: 'instant' }));
			await expectWholeWords(
				corps.locator('.dataset-head, .dataset-state, dt, .dataset-restrictions, .dataset-limits'),
				`libellés, restrictions et limites DVF à ${width} px`
			);
			await expectNoHorizontalOverflow(page);
			await page.screenshot({
				path: catalogueEvidencePath(`catalogue-details-${width}.png`),
				animations: 'disabled'
			});
		}
	});

	// Plan 04.5-11 (B7) replaces « sans JavaScript : toutes les cartes et leurs détails natifs restent dans
	// le HTML »: every row stays in the HTML, and the detail of a source is its real link away
	// (`?jeu=<key>`), rendered by the server beside the list at 1440 and open in its row at 375. An
	// unknown key renders no detail and is never reflected.
	test('sans JavaScript : toutes les lignes restent dans le HTML et le lien d’une ligne rend son détail', async ({
		browser,
		request
	}) => {
		test.slow();
		await configure(request, Array.from({ length: 6 }, () => ({ status: 200, body: FIXTURE })));
		for (const width of [1440, 375] as const) {
			// The page opens on an anchor: under `scroll-behavior: smooth` the link is « not stable », and without
			// JavaScript Playwright's retry then stalls 30 s. Motion is not what this test checks (patterns, 04.7-03).
			const context = await browser.newContext({
				javaScriptEnabled: false,
				reducedMotion: 'reduce',
				baseURL: E2E_APP_ORIGIN,
				viewport: { width, height: width === 1440 ? 900 : 812 }
			});
			try {
				const page = await context.newPage();
				await installLocalNetworkGuard(page);
				const response = await page.goto('/datasets#dvf');
				expect(response?.status()).toBe(200);
				await expect(page.locator('main article')).toHaveCount(FIXTURE.donnees.length);
				await expect(detail(page)).toHaveCount(0);
				await expect(carte(page, 'dvf')).toBeInViewport();

				// The real link of the row: without JavaScript, a request to the server.
				await carte(page, 'dvf').getByRole('link').click();
				await expect(page).toHaveURL(/\/datasets\?jeu=dvf#dvf$/);
				await expectDetailDvf(page);
				await expect(page.locator('main article')).toHaveCount(FIXTURE.donnees.length);
				await expect(carte(page, 'dvf')).toBeInViewport();
				await expectNoHorizontalOverflow(page);
				await page.screenshot({ path: catalogueEvidencePath(`catalogue-nojs-${width}.png`), animations: 'disabled' });

				const inconnu = await page.goto('/datasets?jeu=%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E');
				expect(inconnu?.status()).toBe(200);
				const html = await inconnu!.text();
				expect(html).not.toContain('onerror');
				expect(html).not.toContain('<img src=x');
				await expect(detail(page)).toHaveCount(0);
				await expect(page.locator('main article')).toHaveCount(FIXTURE.donnees.length);
			} finally {
				await context.close();
			}
		}
	});

	test('rendue côté serveur : le HTML porte déjà la couverture et les cartes', async ({ request }) => {
		await configure(request, [{ status: 200, body: FIXTURE }]);
		const res = await request.get('/datasets');
		expect(res.status()).toBe(200);
		const html = await res.text();
		expect(html).toContain('Ce site sert les données de');
		expect(html).toContain('<article id="dvf"');
		expect(html).toContain('<article id="encadrement_loyers"');
	});

	// Review of 2026-11-11: three panels. The catalogue comes first (04.5-11, B7, UI-016), grouped in the
	// reading order of the address fiche; « Lire une fiche » is the one glossary and holds the four states,
	// their labels folded; « Portée des objets » left, its two own sentences are under the scales.
	test('trois panneaux dans l’ordre, sous le h1 : les sources groupées, un seul lexique, la couverture', async ({ page, request }) => {
		await configure(request, [{ status: 200, body: FIXTURE }]);
		await open(page, '/datasets');

		await expect(
			page.getByText(`${FIXTURE.donnees.length} sources publiques. Pour chacune : son producteur, sa licence, sa date et ses limites.`, {
				exact: true
			})
		).toBeVisible();
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('Données & méthode');
		await expect(page.locator('main h2')).toHaveText(['Les sources', 'Lire une fiche', 'Couverture']);

		// The groups: the four parts of the fiche, then the territory; prices first, then the DPE.
		const catalogue = section(page, 'Les sources');
		const attendus = TITRES_GROUPES.filter((titre) => duGroupe(FIXTURE, titre).length > 0);
		await expect(catalogue.getByRole('heading', { level: 3 })).toHaveText(attendus);
		expect(attendus.slice(0, 2)).toEqual(['Prix et ventes', 'Logement et DPE']);
		await expect(catalogue.locator('article').first()).toHaveAttribute('id', 'dvf');
		for (const [index, titre] of attendus.entries()) {
			const groupe = catalogue.locator('.catalogue-groupe').nth(index);
			await expect(groupe.locator('article')).toHaveCount(duGroupe(FIXTURE, titre).length);
			await expect(groupe.locator('.catalogue-groupe-compte')).toHaveText(compteSources(duGroupe(FIXTURE, titre).length));
		}
		await expect(page.locator('main article')).toHaveCount(FIXTURE.donnees.length);

		const lire = section(page, 'Lire une fiche');
		await expect(lire.getByRole('heading', { level: 3 })).toHaveText(['Les échelles', 'Les états', 'Quand une valeur manque']);
		await expect(lire.getByRole('heading', { level: 4 })).toHaveText(['Zéro', 'Manque', 'Hors couverture', 'Panne']);
		await expect(lire).toContainText('Une valeur absente n’est jamais affichée comme un zéro. Quatre cas, toujours distingués :');
		// The labels are folded under their state, one click away.
		const replis = lire.locator('.methode-etat details');
		await expect(replis.locator('summary')).toHaveText(['Voir les 7 libellés', 'Voir les 3 libellés', 'Voir le libellé']);
		await expect(lire.locator('.methode-etat details dt').first()).toBeHidden();
		for (const resume of await replis.locator('summary').all()) await resume.click();
		for (const libelle of [
			'Non renseigné',
			'Effectif insuffisant',
			'Secret statistique',
			'Non publiée',
			'Sans base de calcul',
			'Sans objet',
			'Non qualifié',
			'Hors couverture · jeu non installé',
			'Hors couverture · département non hébergé',
			'Hors couverture · non couvert par la source',
			'Indisponible'
		]) {
			await expect(lire.locator('.methode-etat details').getByText(libelle, { exact: true })).toBeVisible();
		}
		// No technical key of a state is ever displayed.
		await expect(lire).not.toContainText(/not_reported|out_of_coverage|temporary_error/);

		// What « Portée des objets » said and the scales did not, once, under the scales.
		const rappel = lire.locator('.methode-rappel');
		await expect(rappel).toContainText('Une valeur ne se transpose pas d’une échelle à une autre');
		await expect(rappel).toContainText('est un document, pas un logement');
		await expect(page.getByRole('main')).not.toContainText('Portée des objets');
		await expect(page.getByRole('main')).not.toContainText('Le sens des quatre états');
		await expectNoHorizontalOverflow(page);
	});

	test('la couverture liste les départements servis par l’API, quels qu’ils soient', async ({
		page,
		request
	}) => {
		await configure(request, [{ status: 200, body: servieSur(['2A', '974']) }]);
		await open(page, '/datasets');

		const couverture = section(page, 'Couverture');
		await expect(couverture).toContainText('Ce site sert les données de 2 départements.');
		// Lot 8 of 4.9: the list is folded under the sentence, and one click away.
		await expect(couverture.getByRole('listitem')).toHaveCount(0);
		await couverture.getByText('Voir la liste des départements', { exact: true }).click();
		await expect(couverture.getByRole('listitem')).toHaveText(['Corse-du-Sud (2A)', 'La Réunion (974)']);
		await expect(couverture).not.toContainText('Loire-Atlantique');
		await expect(couverture).not.toContainText('Vendée');
	});

	// Plan 04.5-11 (B7): the facts the DVF card gave are in the detail of the source (`?jeu=dvf`); its
	// row keeps its name, producer and badge. Same guarantees as before the redesign.
	test('le détail DVF donne la licence publiée et les restrictions dans leur propre bloc', async ({
		page,
		request
	}) => {
		await configure(request, [{ status: 200, body: FIXTURE }]);
		await open(page, '/datasets?jeu=dvf#dvf');

		const dvf = jeu(FIXTURE, 'dvf');
		const ligne = carte(page, 'dvf');
		const card = detail(page);
		await expect(ligne).toBeVisible();
		// Review of 2026-11-11: the state of a dataset that is read is plain text, with the date of its import.
		await expect(ligne.locator('.dataset-etat')).toHaveText(/^Importé le \d{1,2} \S+ \d{4}$/);
		await expect(ligne.locator('.dataset-badge')).toHaveCount(0);
		await expect(ligne).toContainText(dvf.provenance!.producteur);
		await expect(card).toBeVisible();
		// Name, producer and badge are read in the row: the detail under it does not repeat them (review of 09/11).
		await expect(card.locator('.dataset-badge')).toHaveCount(0);
		await expect(card).not.toContainText(/\binstance\b/i);
		await expect(card).toHaveAccessibleName(dvf.provenance!.nom);
		await expect(card).not.toContainText(dvf.provenance!.producteur);
		for (const terme of ['Licence', 'Source', 'Période amont', 'Cadence', 'Dernière importation réussie', 'Couverture']) {
			await expect(card.locator('dt', { hasText: new RegExp(`^${terme}$`) })).toHaveCount(1);
		}
		await expect(card.getByRole('link', { name: dvf.reutilisation!.licence })).toHaveAttribute('href', dvf.reutilisation!.licence_url);
		await expect(card.getByRole('link', { name: 'Page du producteur' })).toHaveAttribute('href', dvf.provenance!.url);

		const restrictions = card.getByRole('region', { name: 'Restrictions de réutilisation' });
		await expect(restrictions.getByRole('listitem')).toHaveText(dvf.reutilisation!.restrictions);
		const limites = card.getByRole('region', { name: 'Limites de la source' });
		await expect(limites.getByRole('listitem')).toHaveText(dvf.limites_source!.puces);
		// Two blocks apart: no restriction is repeated among the limits, and conversely.
		for (const restriction of dvf.reutilisation!.restrictions) {
			await expect(limites).not.toContainText(restriction);
		}
		for (const puceLimite of dvf.limites_source!.puces) {
			await expect(restrictions).not.toContainText(puceLimite);
		}
	});

	test('une ligne hors catalogue (scoring, sans hebergement) n’est ni affichée ni comptée', async ({
		page,
		request
	}) => {
		// Served to the administration by the same route (migration 039); the public page lists
		// the datasets of the catalogue only (03-11, decision after the D-15 review).
		const scoring = { ...jeu(FIXTURE, 'bdnb'), nom: 'scoring', provenance: null, hebergement: null, reutilisation: null };
		const erreurs: string[] = [];
		page.on('console', (message) => {
			if (message.type() === 'error') erreurs.push(message.text());
		});
		await configure(request, [{ status: 200, body: { ...FIXTURE, donnees: [...FIXTURE.donnees, scoring] } }]);
		await open(page, '/datasets');

		await expect(carte(page, 'dvf')).toBeVisible();
		await expect(carte(page, 'scoring')).toHaveCount(0);
		await expect(page.locator('main article')).toHaveCount(FIXTURE.donnees.length);
		await expect(chip(page, 'Tout')).toHaveText(`Tout · ${FIXTURE.donnees.length}`);
		expect(erreurs.filter((texte) => texte.includes('DatasetCard'))).toEqual([]);
	});

	// Plan 04.5-11 (B7): the badges are in the rows; the quarantine reason, which the card showed beside
	// its badge, is in the detail of the source, opened here by the link of its row (no other API read).
	test('importé (en clair, daté), pas encore importé et non qualifié (en badge) restent publics et distincts', async ({
		page,
		request
	}, testInfo) => {
		await configure(request, [{ status: 200, body: FIXTURE }, { status: 200, body: FIXTURE }]);
		for (const width of [1440, 375] as const) {
			await page.setViewportSize({ width, height: 900 });
			await open(page, '/datasets');
			await expect(page.getByRole('main')).not.toContainText(/\binstance\b/i);
			const imported = carte(page, 'dvf');
			const notImported = carte(page, 'batiments');
			await expect(imported.locator('.dataset-etat')).toHaveText(/^Importé le /);
			await expect(imported.locator('.dataset-badge')).toHaveCount(0);
			await expect(notImported.locator('.dataset-badge')).toHaveText('Pas encore importé');
			await expect(notImported.locator('.dataset-etat')).toHaveCount(0);
			for (const card of [imported, notImported]) {
				await expect(card).not.toContainText(/\binstance\b/i);
			}
			for (const [name, card] of [
				['importe', imported],
				['pas-encore-importe', notImported]
			] as const) {
				await card.screenshot({
					path: publicMessagesEvidencePath(testInfo, `dataset-${name}-${width}.png`),
					animations: 'disabled'
				});
			}
			for (const nom of ['encadrement_loyers', 'permis']) {
				const ligne = carte(page, nom);
				await expect(ligne.locator('.dataset-badge-warn')).toHaveText('Non qualifié');
				await expect(ligne).not.toContainText('Importé');
				await ligne.getByRole('link').click();
				await expect(page).toHaveURL(new RegExp(`[?&]jeu=${nom}(#|&|$)`));
				const corps = detail(page);
				// A warning badge is an honesty fact: the detail carries it too, beside the quarantine sentence.
				await expect(corps.locator('.dataset-badge-warn').first()).toHaveText('Non qualifié');
				await expect(corps).toContainText(jeu(FIXTURE, nom).quarantaine!);
				await expect(corps).not.toContainText('Importé');
				await expect(corps).not.toContainText(/\binstance\b/i);
				if (nom === 'encadrement_loyers') {
					await corps.screenshot({
						path: publicMessagesEvidencePath(testInfo, `dataset-non-qualifie-${width}.png`),
						animations: 'disabled'
					});
				}
			}
			// The quarantine never hides the hosting reason (permis: source without coverage).
			await expect(detail(page)).toContainText('Hors couverture · non couvert par la source');
		}
	});

	test('pastilles : un compte par groupe, filtre, filtre vide et retour à « Tout »', async ({
		page,
		request
	}) => {
		// One dataset only in « Risques », then none once filtered out of the answer.
		const risques = duGroupe(FIXTURE, 'Risques');
		expect(risques.length).toBeGreaterThan(1);
		const unSeul: Reponse = {
			...FIXTURE,
			donnees: FIXTURE.donnees.filter((item) => puce(item) !== 'Risques' || item.nom === risques[0].nom)
		};
		const aucun: Reponse = {
			...FIXTURE,
			donnees: FIXTURE.donnees.filter((item) => puce(item) !== 'Risques')
		};
		await configure(request, [
			{ status: 200, body: FIXTURE },
			{ status: 200, body: unSeul },
			{ status: 200, body: aucun }
		]);

		await open(page, '/datasets');
		await expect(chip(page, 'Tout')).toHaveText(`Tout · ${FIXTURE.donnees.length}`);
		const groupesServis = TITRES_GROUPES.filter((titre) => duGroupe(FIXTURE, titre).length > 0);
		expect(groupesServis.length).toBeGreaterThan(3);
		for (const titre of groupesServis) {
			await expect(chip(page, titre)).toHaveText(`${titre} · ${duGroupe(FIXTURE, titre).length}`);
		}
		await expect(chip(page, 'Tout')).toHaveAttribute('aria-pressed', 'true');

		await chip(page, 'Risques').click();
		await expect(page).toHaveURL(/\/datasets\?filtre=risques$/);
		await expect(chip(page, 'Risques')).toHaveAttribute('aria-pressed', 'true');
		await expect(page.locator('main article')).toHaveCount(risques.length);
		await expect(section(page, 'Les sources').getByRole('heading', { level: 3 })).toHaveText(['Risques']);
		await expect(page.getByRole('status').filter({ hasText: 'Risques :' })).toHaveText(
			`Risques : ${compteSources(risques.length)}`
		);

		await open(page, '/datasets');
		await expect(chip(page, 'Risques')).toHaveText('Risques · 1');

		// Lot 8 of 4.9: at 0 the chip is hidden; reached by its URL it stays shown, active, with the way back.
		await open(page, '/datasets?filtre=risques');
		await expect(chip(page, 'Risques')).toHaveText('Risques · 0');
		await expect(chip(page, 'Risques')).toHaveAttribute('aria-pressed', 'true');
		await expect(page.getByText('Aucune source dans ce thème.')).toBeVisible();
		await expect(page.locator('main article')).toHaveCount(0);
		await page.getByRole('button', { name: 'Afficher toutes les sources' }).click();
		await expect(chip(page, 'Tout')).toHaveAttribute('aria-pressed', 'true');
		await expect(chip(page, 'Tout')).toBeFocused();
		await expect(page.locator('main article')).toHaveCount(aucun.donnees.length);
	});

	test('panne de /v1/datasets : encart warn dans « Couverture », le lexique reste, relance', async ({
		page,
		request
	}) => {
		await configure(request, [
			{ status: 503, body: { detail: 'Service indisponible' } },
			{ status: 200, body: FIXTURE }
		]);
		await open(page, '/datasets');

		const alerte = page.getByRole('alert');
		await expect(alerte).toContainText('La liste des jeux de données est momentanément indisponible');
		await expect(section(page, 'Couverture').getByRole('alert')).toHaveCount(1);
		await expect(section(page, 'Lire une fiche')).toBeVisible();
		await expect(section(page, 'Lire une fiche').getByRole('heading', { level: 4 })).toHaveText(['Zéro', 'Manque', 'Hors couverture', 'Panne']);
		await expect(section(page, 'Les sources')).toHaveCount(0);
		// No number is written under the title when the catalogue did not answer.
		await expect(page.getByText('Des sources publiques. Pour chacune : son producteur, sa licence, sa date et ses limites.', { exact: true })).toBeVisible();
		await expect(page.locator('main article')).toHaveCount(0);

		await alerte.getByRole('button', { name: 'Recharger la page' }).click();
		await expect(carte(page, 'dvf')).toBeVisible();
		await expect(page.getByRole('alert')).toHaveCount(0);
	});

	// Plan 04.5-11 (B7): a former detail page now leads to the metadata of its source
	// (`?jeu=<key>#<key>`), not only to its row (was: `Location: /datasets#<key>`). `/datasets#<key>`
	// still targets the row and, with JavaScript, opens its detail.
	test('les anciennes pages de détail répondent 308 vers leur source, un slug inconnu 404', async ({
		page,
		request
	}) => {
		const res = await request.get('/datasets/encadrement-loyers', { maxRedirects: 0 });
		expect(res.status()).toBe(308);
		expect(res.headers()['location']).toBe('/datasets?jeu=encadrement_loyers#encadrement_loyers');
		const inconnu = await request.get('/datasets/%3Cscript%3Ealert(1)%3C%2Fscript%3E', {
			maxRedirects: 0
		});
		expect(inconnu.status()).toBe(404);
		expect(await inconnu.text()).not.toContain('<script>alert(1)</script>');

		await configure(request, [{ status: 200, body: FIXTURE }, { status: 200, body: FIXTURE }]);
		await open(page, '/datasets/dvf');
		await expect(page).toHaveURL(/\/datasets\?jeu=dvf#dvf$/);
		await expect(carte(page, 'dvf')).toBeInViewport();
		await expect(detail(page).getByRole('link', { name: jeu(FIXTURE, 'dvf').reutilisation!.licence })).toBeVisible();

		await open(page, '/datasets#gares');
		await expect(page).toHaveURL(/\/datasets\?jeu=gares#gares$/);
		await expect(detail(page)).toContainText(jeu(FIXTURE, 'gares').reutilisation!.licence);
		await expect(carte(page, 'gares')).toBeInViewport();
	});

	test('375 px : aucun débordement horizontal', async ({ page, request }) => {
		await configure(request, [{ status: 200, body: FIXTURE }]);
		await page.setViewportSize({ width: 375, height: 800 });
		await open(page, '/datasets');
		await expect(carte(page, 'dvf')).toBeVisible();
		const { scrollWidth, innerWidth } = await page.evaluate(() => ({
			scrollWidth: document.documentElement.scrollWidth,
			innerWidth: window.innerWidth
		}));
		expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
	});

	// Plan 04.5-11 (B7, UI-030). The search, the filter and the source read are GET parameters, read
	// by the page: a GET form without JavaScript, a navigation without reading the API with it.
	test('sans JavaScript : le formulaire GET filtre, garde la source, se recharge et passe de 1440 à 375', async ({
		browser,
		request
	}) => {
		test.slow();
		await configure(request, Array.from({ length: 8 }, () => ({ status: 200, body: FIXTURE })));
		const context = await browser.newContext({ javaScriptEnabled: false, baseURL: E2E_APP_ORIGIN, viewport: { width: 1440, height: 900 } });
		try {
			const page = await context.newPage();
			await installLocalNetworkGuard(page);
			await page.goto('/datasets?jeu=dvf');
			// Replaces `toHaveCount(0)` of the former no-JS test: the search is a GET form rendered by the server.
			const search = page.getByRole('searchbox', { name: 'Rechercher une source ou un producteur' });
			await expect(search).toHaveCount(1);
			await search.fill('dvf');
			await search.press('Enter');
			await expect(page).toHaveURL(/\/datasets\?q=dvf&jeu=dvf$/);
			await expect(page.locator('main article')).toHaveCount(1);
			await expectDetailDvf(page);

			await search.fill('insee');
			await search.press('Enter');
			await expect(page).toHaveURL(/\/datasets\?q=insee&jeu=dvf$/);
			const insee = ['demographie', 'equipements', 'revenus', 'sirene'];
			await expect(page.locator('main article')).toHaveCount(insee.length);
			// dvf is not among them and several remain: no detail.
			await expect(detail(page)).toHaveCount(0);
			await expect(chip(page, 'Tout')).toHaveText(`Tout · ${insee.length}`);
			// Without JavaScript the search button is shown: nothing filters while typing.
			await expect(page.getByRole('button', { name: 'Rechercher', exact: true }).last()).toBeVisible();

			// Lot 8 of 4.9: no JavaScript needed to hide a filter at 0, a real chip still sends its filter.
			await expect(chip(page, 'Risques')).toHaveCount(0);
			await expect(chip(page, 'Prix et ventes')).toHaveCount(0);
			const territoire = insee.filter((nom) => groupeDuJeu(nom) === 'territoire');
			expect(territoire.length).toBeGreaterThan(0);
			expect(territoire.length).toBeLessThan(insee.length);
			await chip(page, 'Territoire et population').click();
			await expect(page).toHaveURL(/\/datasets\?q=insee&filtre=territoire&jeu=dvf$/);
			await expect(page.locator('main article')).toHaveCount(territoire.length);

			await page.goto('/datasets?q=insee&filtre=risques&jeu=dvf');
			await expect(chip(page, 'Risques')).toHaveAttribute('aria-pressed', 'true');
			await expect(page.locator('main article')).toHaveCount(0);
			await expect(section(page, 'Les sources').getByText(/aucune source/i)).toBeVisible();

			// The producer of one dataset only, in its group.
			await page.goto('/datasets?q=drihl&filtre=prix');
			// The filter leaves one dataset: its detail is shown.
			await expect(page.locator('main article')).toHaveCount(1);
			await expect(detail(page)).toContainText(jeu(FIXTURE, 'encadrement_loyers').quarantaine!);
			await expect(chip(page, 'Prix et ventes')).toHaveAttribute('aria-pressed', 'true');
			await expect(search).toHaveValue('drihl');
			await page.reload();
			await expect(detail(page)).toContainText(jeu(FIXTURE, 'encadrement_loyers').quarantaine!);
			await expect(search).toHaveValue('drihl');
			await page.setViewportSize({ width: 375, height: 812 });
			await expect(chip(page, 'Prix et ventes')).toHaveAttribute('aria-pressed', 'true');
			await expect(page.locator('main details.dataset-ouvert')).toHaveCount(0);
			await expect(detail(page)).toBeVisible();
			await expectNoHorizontalOverflow(page);

			// An unknown filter is « Tout », escaped search never interpreted.
			await page.goto('/datasets?filtre=%22%3E%3Cb%3Eintrus&q=%3Cimg%20src%3Dx%3E');
			await expect(chip(page, 'Tout')).toHaveAttribute('aria-pressed', 'true');
			await expect(search).toHaveValue('<img src=x>');
			await expect(page.locator('main img')).toHaveCount(0);
			expect(await page.content()).not.toContain('intrus');
		} finally {
			await context.close();
		}
	});

	test('le clic réel sur une ligne garde recherche et filtre, avec et sans JavaScript, puis Retour et rechargement', async ({
		browser,
		page,
		request
	}) => {
		test.slow();
		// With JavaScript: one load, one reload. Without: one request per page; Back may come from the
		// back/forward cache or ask the server again.
		await configure(request, Array.from({ length: 7 }, () => ({ status: 200, body: FIXTURE })), 6);
		// INSEE datasets of the group « Territoire et population »: demographie and revenus.
		const depart = '/datasets?q=insee&filtre=territoire';
		const versRevenus = /\/datasets\?q=insee&filtre=territoire&jeu=revenus#revenus$/;
		const versDemographie = /\/datasets\?q=insee&filtre=territoire&jeu=demographie#demographie$/;
		const search = (cible: Page) => cible.getByRole('searchbox', { name: 'Rechercher une source ou un producteur' });

		await page.setViewportSize({ width: 1440, height: 900 });
		await open(page, depart);
		await expect(page.locator('main article')).toHaveCount(2);
		await expect(detail(page)).toHaveCount(0);
		await carte(page, 'revenus').getByRole('link').click();
		await expect(page).toHaveURL(versRevenus);
		await expect(detail(page)).toHaveAccessibleName(jeu(FIXTURE, 'revenus').provenance!.nom);
		await expect(carte(page, 'revenus').getByRole('link')).toBeFocused();
		await carte(page, 'demographie').getByRole('link').click();
		await expect(page).toHaveURL(versDemographie);
		await expect(detail(page)).toHaveAccessibleName(jeu(FIXTURE, 'demographie').provenance!.nom);
		await page.goBack();
		await expect(page).toHaveURL(versRevenus);
		await expect(detail(page)).toHaveAccessibleName(jeu(FIXTURE, 'revenus').provenance!.nom);
		await page.goBack();
		await expect(page).toHaveURL(new RegExp(`${depart.replace('?', '\\?')}$`));
		await expect(detail(page)).toHaveCount(0);
		await expect(search(page)).toHaveValue('insee');
		await expect(chip(page, 'Territoire et population')).toHaveAttribute('aria-pressed', 'true');
		await page.goForward();
		await expect(page).toHaveURL(versRevenus);
		await page.reload();
		await expect(detail(page)).toHaveAccessibleName(jeu(FIXTURE, 'revenus').provenance!.nom);
		await expect(search(page)).toHaveValue('insee');
		await expect(page.locator('main article')).toHaveCount(2);

		// Same stall as above without reduced motion (30 s on the first click): motion is not checked here.
		const context = await browser.newContext({
			javaScriptEnabled: false,
			reducedMotion: 'reduce',
			baseURL: E2E_APP_ORIGIN,
			viewport: { width: 1440, height: 900 }
		});
		try {
			const sansJs = await context.newPage();
			await installLocalNetworkGuard(sansJs);
			await sansJs.goto(depart);
			await carte(sansJs, 'revenus').getByRole('link').click();
			await expect(sansJs).toHaveURL(versRevenus);
			await expect(detail(sansJs)).toHaveAccessibleName(jeu(FIXTURE, 'revenus').provenance!.nom);
			await expect(search(sansJs)).toHaveValue('insee');
			await carte(sansJs, 'demographie').getByRole('link').click();
			await expect(sansJs).toHaveURL(versDemographie);
			await sansJs.goBack();
			await expect(sansJs).toHaveURL(versRevenus);
			await expect(detail(sansJs)).toHaveAccessibleName(jeu(FIXTURE, 'revenus').provenance!.nom);
			await sansJs.reload();
			await expect(detail(sansJs)).toHaveAccessibleName(jeu(FIXTURE, 'revenus').provenance!.nom);
			await expect(sansJs.locator('main article')).toHaveCount(2);
		} finally {
			await context.close();
		}
	});

	test('Retour défini et une seule lecture de /v1/datasets après des changements de filtre, de recherche et de source', async ({
		page,
		request
	}) => {
		// maxCalls 1: a fourth change that read the API would break the contract of the source.
		await configure(request, [{ status: 200, body: FIXTURE }]);
		await page.setViewportSize({ width: 1440, height: 900 });
		await open(page, '/datasets');
		await chip(page, 'Logement et DPE').click();
		await expect(page).toHaveURL(/\/datasets\?filtre=logement$/);
		await expect(page.locator('main article')).toHaveCount(duGroupe(FIXTURE, 'Logement et DPE').length);
		await carte(page, 'permis').getByRole('link').click();
		await expect(page).toHaveURL(/\/datasets\?filtre=logement&jeu=permis#permis$/);
		await chip(page, 'Tout').click();
		await expect(page).toHaveURL(/\/datasets\?jeu=permis$/);
		await expect(page.locator('main article')).toHaveCount(FIXTURE.donnees.length);
		await expect(detail(page)).toContainText(jeu(FIXTURE, 'permis').quarantaine!);
		const search = page.getByRole('searchbox', { name: 'Rechercher une source ou un producteur' });
		await search.pressSequentially('ademe');
		await expect(page).toHaveURL(/\/datasets\?q=ademe&jeu=permis$/);
		await expect(page.locator('main article')).toHaveCount(1);
		// The only dataset left shows its detail.
		await expect(detail(page)).toHaveAccessibleName(jeu(FIXTURE, 'dpe').provenance!.nom);

		// Back: the search typed is one entry, then the filter, then the source, then the filter.
		await page.goBack();
		await expect(page).toHaveURL(/\/datasets\?jeu=permis$/);
		await expect(search).toHaveValue('');
		await expect(page.locator('main article')).toHaveCount(FIXTURE.donnees.length);
		await page.goBack();
		await expect(page).toHaveURL(/\/datasets\?filtre=logement&jeu=permis#permis$/);
		await expect(chip(page, 'Logement et DPE')).toHaveAttribute('aria-pressed', 'true');
		await expect(detail(page)).toContainText(jeu(FIXTURE, 'permis').quarantaine!);
		await page.goBack();
		await expect(page).toHaveURL(/\/datasets\?filtre=logement$/);
		await expect(detail(page)).toHaveCount(0);
		await page.goBack();
		await expect(page).toHaveURL(/\/datasets$/);
		await expect(chip(page, 'Tout')).toHaveAttribute('aria-pressed', 'true');
		await expect(page.locator('main article')).toHaveCount(FIXTURE.donnees.length);
	});

	test('mobile : ouvrir une source depuis sa ligne, recharger, puis passer à 1440', async ({ page, request }) => {
		await configure(request, [{ status: 200, body: FIXTURE }, { status: 200, body: FIXTURE }]);
		await page.setViewportSize({ width: 375, height: 812 });
		await open(page, '/datasets');
		await expect(detail(page)).toHaveCount(0);
		await carte(page, 'dvf').getByRole('link').click();
		await expect(page).toHaveURL(/\/datasets\?jeu=dvf#dvf$/);
		await expectDetailDvf(page);
		await page.reload();
		await expectDetailDvf(page);
		await expect(carte(page, 'dvf').getByRole('link')).toHaveAttribute('aria-current', 'true');
		await page.setViewportSize({ width: 1440, height: 900 });
		await expectDetailDvf(page);
		await expectNoHorizontalOverflow(page);
	});

	// Review of 09/11 (Ianis): the row opens the source; a second fold « Détail de la source » inside it
	// was a repeat. The facts are read at once, at every width, and the anchor of the row still leads there.
	test('le détail d’une source s’affiche sans second volet, à 375 et 1440 px', async ({ page, request }) => {
		await configure(request, [{ status: 200, body: FIXTURE }]);
		await page.setViewportSize({ width: 375, height: 812 });
		await open(page, '/datasets?jeu=dvf#dvf');
		// The anchor of the row still leads to the source read.
		await expect(page.locator('main article#dvf')).toBeInViewport();
		for (const [width, height] of [[375, 812], [1440, 900]] as const) {
			await page.setViewportSize({ width, height });
			await expectDetailDvf(page);
			await expect(page.getByText('Détail de la source', { exact: true })).toHaveCount(0);
			await expect(page.locator('main summary', { hasText: 'Détail' })).toHaveCount(0);
			await expect(detail(page).locator('dt', { hasText: /^Licence$/ })).toBeVisible();
			await expect(detail(page)).toHaveAccessibleName(jeu(FIXTURE, 'dvf').provenance!.nom);
		}
	});

	test('premier écran : les entrées, le champ de recherche et la première source à 375 × 812 et 1440 × 900', async ({ page, request }) => {
		await configure(request, [{ status: 200, body: FIXTURE }, { status: 200, body: FIXTURE }]);
		for (const [width, height] of [
			[375, 812],
			[1440, 900]
		] as const) {
			await page.setViewportSize({ width, height });
			await open(page, width === 375 ? '/datasets' : '/datasets?viewport=1440');
			const entrees = accesDirects(page);
			for (const cible of [
				entrees.getByRole('link', { name: 'Les sources', exact: true }),
				entrees.getByRole('link', { name: 'Lire une fiche', exact: true }),
				page.getByRole('searchbox', { name: 'Rechercher une source ou un producteur' })
			]) {
				await expect(cible).toBeInViewport({ ratio: 1 });
				await expectTarget44(cible, `entrée du premier écran à ${width} px`);
			}
			// Review of 2026-11-11: the list opens on the prices. At 1440 px its first source is read without
			// scrolling. At 375 px the chips wrap (no sideways scroll outside the comparator, UI-01), so it is not:
			// it stays above where the live page had it before the review (y = 972, under three stacked buttons
			// and three stacked chips), measured from the top of the page.
			await expect(page.locator('main article').first()).toHaveAttribute('id', 'dvf');
			const premierNom = page.locator('main article .dataset-ligne-nom').first();
			if (width === 1440) await expect(premierNom).toBeInViewport({ ratio: 1 });
			else expect(await premierNom.evaluate((el) => el.getBoundingClientRect().top + window.scrollY), 'haut de la première source à 375 px').toBeLessThan(972);
			// The three entries on one row, at 375 px too.
			const hauts = await entrees.getByRole('link').evaluateAll((liens) => liens.map((lien) => Math.round(lien.getBoundingClientRect().top)));
			expect(new Set(hauts).size, `entrées sur une rangée à ${width} px : ${JSON.stringify(hauts)}`).toBe(1);
			await expectNoHorizontalOverflow(page);
			await page.screenshot({ path: catalogueEvidencePath(`catalogue-premier-ecran-${width}.png`), animations: 'disabled' });
		}
	});

	// Lot 8 of 4.9. A reader opens a source where it stands; the method stays one click away, on paper too.
	test('navigation : trois entrées du même style à 1 440 et 375 px, sans JavaScript aussi', async ({ browser, request }) => {
		await configure(request, [{ status: 200, body: FIXTURE }, { status: 200, body: FIXTURE }]);
		for (const width of [1440, 375] as const) {
			const context = await browser.newContext({ javaScriptEnabled: false, baseURL: E2E_APP_ORIGIN, viewport: { width, height: 900 } });
			try {
				const page = await context.newPage();
				await installLocalNetworkGuard(page);
				await page.goto('/datasets');
				const styles = await accesDirects(page).getByRole('link').evaluateAll((liens) =>
					liens.map((lien) => {
						const style = getComputedStyle(lien);
						return [style.backgroundColor, style.color, style.borderTopColor, style.borderTopWidth, style.borderTopLeftRadius, style.fontWeight, style.height, style.textDecorationLine];
					})
				);
				expect(styles).toHaveLength(3);
				expect(styles[1], `à ${width} px`).toEqual(styles[0]);
				expect(styles[2], `à ${width} px`).toEqual(styles[0]);
			} finally {
				await context.close();
			}
		}
	});

	test('détail sur place : pas de panneau vide, la liste en pleine largeur, l’invite sans cadre', async ({ page, request }) => {
		await configure(request, [{ status: 200, body: FIXTURE }]);
		await page.setViewportSize({ width: 1440, height: 900 });
		await open(page, '/datasets');
		const invite = page.locator('.catalogue-invite');
		await expect(invite).toHaveCSS('border-top-width', '0px');
		const liste = (await page.locator('.methode-cards').first().boundingBox())!;
		// Since 2026-11-11 the list opens on the title of its first group: the group follows the invitation.
		const groupe = (await page.locator('.catalogue-groupe').first().boundingBox())!;
		const corps = (await page.locator('section[aria-labelledby="catalogue-titre"] .panneau-corps').boundingBox())!;
		const boiteInvite = (await invite.boundingBox())!;
		expect(liste.width, 'la liste occupe la largeur du panneau').toBeGreaterThan(corps.width * 0.85);
		expect(Math.abs(groupe.y - (boiteInvite.y + boiteInvite.height)), 'la liste suit l’invite').toBeLessThan(48);
		expect(liste.y - groupe.y, 'la première ligne suit le titre de son groupe').toBeLessThan(48);
	});

	test('sur place : changer de source garde la ligne choisie à l’écran, à 1 440 et 375 px', async ({ page, request }) => {
		await configure(request, [{ status: 200, body: FIXTURE }, { status: 200, body: FIXTURE }]);
		for (const width of [1440, 375] as const) {
			await page.setViewportSize({ width, height: 800 });
			await open(page, width === 1440 ? '/datasets?jeu=dvf#dvf' : '/datasets?jeu=dvf&viewport=375#dvf');
			await expect(detail(page)).toBeVisible();
			// A row after the open detail: closing that detail lifts the row above the screen.
			const suivante = carte(page, 'sirene');
			await suivante.getByRole('link').click();
			await expect(page).toHaveURL(/jeu=sirene#sirene$/);
			await expect(detail(page)).toHaveAccessibleName(jeu(FIXTURE, 'sirene').provenance!.nom);
			await expect(suivante).toBeInViewport();
		}
	});

	test('lecture directe : la ligne dit le mot court, le détail la phrase entière', async ({ page, request }) => {
		await configure(request, [{ status: 200, body: FIXTURE }]);
		await open(page, '/datasets');
		const ligne = carte(page, 'georisques');
		await expect(ligne.locator('.dataset-etat')).toHaveText('Lu en direct');
		await expect(ligne.locator('.dataset-badge')).toHaveCount(0);
		await ligne.getByRole('link').click();
		await expect(detail(page).locator('.dataset-badge')).toHaveText('Lu en direct chez le producteur, sans import');
	});

	test('couverture : phrase, liste repliée lisible au clavier et sans JavaScript, présente à l’impression, lien vers ses limites', async ({ page, browser, request }) => {
		await configure(request, [{ status: 200, body: FIXTURE }, { status: 200, body: FIXTURE }]);
		await open(page, '/datasets#couverture-titre');
		const couverture = section(page, 'Couverture');
		const resume = couverture.getByText('Voir la liste des départements', { exact: true });
		const liste = couverture.locator('details ul');
		await expect(page.locator('#couverture-titre')).toBeVisible();
		await expect(liste).toBeHidden();
		await expectTarget44(resume, 'résumé de la liste');
		// Review of 2026-11-11: the caution is read in the glossary, on the row « Hors couverture »; the limits
		// of each dataset are one link away, on their own page.
		await expect(section(page, 'Lire une fiche')).toContainText('Une absence de couverture ne vaut pas zéro.');
		const versLimites = couverture.getByRole('link', { name: 'Couverture et limites', exact: true });
		await expect(versLimites).toHaveAttribute('href', '/couverture');
		await expectTarget44(couverture.locator('.methode-suite'), 'lien vers la couverture et ses limites');

		await page.emulateMedia({ media: 'print' });
		await expect(couverture.locator('.methode-impression li')).toHaveText(['Loire-Atlantique (44)', 'Vendée (85)']);
		await expect(couverture.locator('.methode-impression')).toBeVisible();
		await expect(resume).toBeHidden();
		await page.emulateMedia({ media: 'screen' });

		await expect(couverture.locator('.methode-impression')).toBeHidden();
		await resume.focus();
		await page.keyboard.press('Enter');
		await expect(liste).toBeVisible();
		const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
		expect(axe.violations, JSON.stringify(axe.violations, null, 2)).toEqual([]);

		const context = await browser.newContext({ javaScriptEnabled: false, baseURL: E2E_APP_ORIGIN, viewport: { width: 375, height: 800 } });
		try {
			const sansJs = await context.newPage();
			await installLocalNetworkGuard(sansJs);
			await sansJs.goto('/datasets');
			await section(sansJs, 'Couverture').getByText('Voir la liste des départements', { exact: true }).click();
			await expect(section(sansJs, 'Couverture').locator('details ul li')).toHaveCount(2);
			await expect(section(sansJs, 'Couverture').locator('details ul')).toBeVisible();
		} finally {
			await context.close();
		}
	});

	test('quatre états en tableau : une ligne par état, libellés repliés, lisibles sans JavaScript et à l’impression', async ({ page, browser, request }) => {
		await configure(request, [{ status: 200, body: FIXTURE }, { status: 200, body: FIXTURE }, { status: 200, body: FIXTURE }]);
		// Ceilings under the height of the table of lot 8 with every label shown (about 1 000 px at 1440,
		// 1 650 px at 375), itself under the four cards it replaced (1 267 px and 1 949 px).
		for (const [width, plafond] of [[1440, 600], [375, 900]] as const) {
			await page.setViewportSize({ width, height: 900 });
			await open(page, width === 1440 ? '/datasets' : '/datasets?viewport=375');
			const etats = page.locator('main .methode-manques');
			const lignes = etats.locator('.methode-etat');
			await expect(lignes).toHaveCount(4);
			const boites = await lignes.evaluateAll((elements) => elements.map((el) => el.getBoundingClientRect().toJSON()));
			// One above the other: a table, never two cards side by side.
			for (let i = 1; i < boites.length; i += 1) expect(boites[i].y).toBeGreaterThanOrEqual(boites[i - 1].y + boites[i - 1].height - 1);
			expect((await etats.boundingBox())!.height, `hauteur à ${width} px`).toBeLessThan(plafond);
			for (const resume of await etats.locator('summary').all()) await expectTarget44(resume, `résumé des libellés à ${width} px`);
			// Opened, every label is read with its sentence, and nothing overflows.
			for (const resume of await etats.locator('summary').all()) {
				await resume.focus();
				await page.keyboard.press('Enter');
			}
			await expect(etats.locator('details dt')).toHaveCount(11);
			for (const dt of await etats.locator('details dt').all()) await expect(dt).toBeVisible();
			await expectNoHorizontalOverflow(page);
		}

		// Paper does not open a fold: the labels are written out for it.
		await page.emulateMedia({ media: 'print' });
		await expect(page.locator('main .methode-manques .methode-impression dt')).toHaveCount(11);
		for (const bloc of await page.locator('main .methode-manques .methode-impression').all()) await expect(bloc).toBeVisible();
		await expect(page.locator('main .methode-manques summary').first()).toBeHidden();
		await page.emulateMedia({ media: 'screen' });

		const context = await browser.newContext({ javaScriptEnabled: false, baseURL: E2E_APP_ORIGIN, viewport: { width: 375, height: 800 } });
		try {
			const sansJs = await context.newPage();
			await installLocalNetworkGuard(sansJs);
			await sansJs.goto('/datasets');
			const manque = sansJs.locator('main .methode-etat').nth(1);
			await manque.getByText('Voir les 7 libellés', { exact: true }).click();
			await expect(manque.locator('details dt')).toHaveCount(7);
			await expect(manque.locator('details').getByText('Non qualifié', { exact: true })).toBeVisible();
		} finally {
			await context.close();
		}
	});

	test('accessibilité, replis ouverts : axe à 1 440, 375, 320 et zoom 200 %, focus rendu sur les résumés', async ({ page, request }) => {
		test.slow();
		const tailles = [[1440, 900], [375, 812], [320, 700], [720, 450]] as const;
		await configure(request, tailles.map(() => ({ status: 200, body: FIXTURE })));
		for (const [width, height] of tailles) {
			await page.setViewportSize({ width, height });
			await open(page, `/datasets?jeu=dvf&viewport=${width}`);
			const couverture = section(page, 'Couverture');
			const resume = couverture.locator('details > summary');
			await resume.focus();
			await page.keyboard.press('Enter');
			await expect(couverture.locator('details ul')).toBeVisible();
			await expect(resume).toHaveCSS('outline-style', 'solid');
			// The folds of the glossary too, by the keyboard: their focus is drawn and their labels are scanned opened.
			for (const libelles of await page.locator('main .methode-etat summary').all()) {
				await libelles.focus();
				await page.keyboard.press('Enter');
				await expect(libelles).toHaveCSS('outline-style', 'solid');
			}
			await expect(page.locator('main .methode-etat details dt').last()).toBeVisible();
			await expect(detail(page)).toBeVisible();
			await expectNoHorizontalOverflow(page);
			const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
			expect(axe.violations, JSON.stringify(axe.violations, null, 2)).toEqual([]);
		}
	});

	// Plan 04.5-11 (B7): the same three entries at 1440 × 900 and 375 × 812, before and after the
	// redesign, with the height of the page (`DC_CATALOGUE_CAPTURES_DIR`).
	test('captures du catalogue aux mêmes entrées, avec la hauteur de page', async ({ page, request }, testInfo) => {
		test.slow();
		const entrees = [
			['catalogue', '/datasets'],
			['jeu-dvf', '/datasets?jeu=dvf'],
			['recherche-dvf', '/datasets?q=dvf&filtre=prix']
		] as const;
		const largeurs = [
			[1440, 900],
			[375, 812]
		] as const;
		await configure(request, Array.from({ length: entrees.length * largeurs.length }, () => ({ status: 200, body: FIXTURE })));
		const directory = process.env.DC_CATALOGUE_CAPTURES_DIR ?? testInfo.outputPath('captures');
		mkdirSync(directory, { recursive: true });
		const hauteurs: Record<string, number> = {};
		for (const [width, height] of largeurs) {
			await page.setViewportSize({ width, height });
			for (const [nom, url] of entrees) {
				await open(page, url);
				await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
				await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
				hauteurs[`${nom}-${width}`] = await page.evaluate(() => document.documentElement.scrollHeight);
				await page.screenshot({ path: join(directory, `${nom}-${width}-premier-ecran.png`), animations: 'disabled' });
				await page.screenshot({ path: join(directory, `${nom}-${width}.png`), animations: 'disabled', fullPage: true });
			}
		}
		writeFileSync(join(directory, 'hauteurs.json'), `${JSON.stringify(hauteurs, null, '\t')}\n`);
	});

	for (const largeur of [1280, 375]) {
		test(`axe WCAG 2.1 AA sans violation à ${largeur} px`, async ({ page, request }) => {
			await configure(request, [{ status: 200, body: FIXTURE }]);
			await page.setViewportSize({ width: largeur, height: 900 });
			await open(page, '/datasets');
			await expect(carte(page, 'dvf')).toBeVisible();
			const resultat = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
			expect(resultat.violations, JSON.stringify(resultat.violations, null, 2)).toEqual([]);
			// Plan 04.5-11 (B7): the detail of a source is scanned too — a quarantined one (warn colours),
			// opened by its row, without another API read.
			await carte(page, 'permis').getByRole('link').click();
			await expect(detail(page)).toContainText(jeu(FIXTURE, 'permis').quarantaine!);
			const choisi = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
			expect(choisi.violations, JSON.stringify(choisi.violations, null, 2)).toEqual([]);
		});
	}
});
