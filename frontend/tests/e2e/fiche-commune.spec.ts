import AxeBuilder from '@axe-core/playwright';
import { test, expect, type Locator, type Page } from '@playwright/test';
import { getExplication, type CoveredFieldId } from '../../src/lib/data/explications';
import { LIBELLES_ETATS } from '../../src/lib/data/etats-libelles';
import { sansDeveloppement } from '../../src/lib/utils/sigles';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { configureSsrSource, type SourceExpectation, type SsrSourceProbe } from './fixtures/bien-ssr-source';
import {
	COMMUNE_FLAT_85047,
	COMMUNE_FLAT_85047_WITH_DVF_WINDOW,
	COMMUNE_85191_OBSERVED,
	COMMUNE_INDICATORS_SYNTHETIC,
	COMMUNE_85191_SOURCES,
	REPONSE_PANNE,
	REPONSE_SANS_LIGNE,
	reponseHorsCouverture,
	reponseReferentiel
} from './fixtures/commune-indicators';

// Fiche Commune (COM-02, UI-SPEC « /commune/[code] », D-46, D-47, D-52).
//
// Invariants proven on the rendered page: the synthesis in the validated order ① ② ③ ④ (DOM and
// tab order), each KPI with its size, window and source, the source panel opened in one action
// (<dialog>), an absent KPI worded by its served sub-state and never by a 0, then the rows of the five
// themes in the three categories of the fiche (4.9), reached by their old anchor,
// each row with its served value or declared state; the four page states kept apart
// (out of coverage ≠ outage ≠ unknown INSEE), the CSV export of the theme rows with their states
// and caveats, printing with the questions open and the caveats under each value, Corsican codes, and no horizontal overflow at 320
// and 375px.
//
// The first load is SSR: every case configures the local HTTP source with an exact method, path
// and query, then checks its fail-closed journal (an unexpected request is a violation).

const MOCK_USER = {
	id: 2,
	email: 'test@datacommune.fr',
	name: 'Test User',
	is_verified: true,
	is_admin: false,
	created_at: '2026-01-01T00:00:00Z'
};

// Public definition of a field, read from `EXPLICATIONS` as the source panel renders it (03-17 F66),
// never the technical description of the contract.
const definition = (fieldId: CoveredFieldId) => getExplication(fieldId).definition;

// Challans with the dated DVF / DPE blocks (synthetic): the synthesis reads the blocks.
const MOCK_COMMUNE_85047 = {
	statut: 'ok',
	donnees: [{ ...COMMUNE_FLAT_85047_WITH_DVF_WINDOW, ...COMMUNE_INDICATORS_SYNTHETIC }],
	meta: { source: [{ nom: 'Démographie' }, { nom: 'Loyers' }, { nom: 'Zonage ABC' }] }
};

// The tiles of a fixture: « L'essentiel » shows what is known (4.9, lot 3), in the order ① ② ③ ④.
const KPI_85047 = ['prix-maisons', 'tendance', 'dpe-dominant'];
const KPI_85191 = ['prix-maisons', 'prix-appartements', 'dpe-dominant'];
// The former theme anchors: each is an empty span inside the group that now holds its rows (4.9).
const ANCRES_THEMES = ['immobilier', 'energie-bati', 'services-transports', 'contexte-communal', 'risques-urbanisme'];
// The three categories of the fiche: cards above 1080 px, closed folds under it (Categorie.svelte).
const CATEGORIES = ['q-prix', 'q-logement', 'q-quotidien'] as const;
const CATEGORY_PANELS = CATEGORIES.map((id) => `details.cat#${id}`).join(', ');
// Under 1081 px a category is a closed fold: a test that reads its rows opens it first, as a reader does.
async function ouvrirCategories(page: Page) {
	for (const id of CATEGORIES) {
		const categorie = page.locator(`details.cat#${id}`);
		if (!(await categorie.evaluate((node: HTMLDetailsElement) => node.open))) await categorie.locator(':scope > summary').click();
	}
}
const maisonsLibelle = 'Prix médian maison (hors ventes sur plan)';
const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] as const;

function indicatorsEvidencePath(testInfo: { outputPath(name: string): string }, name: string): string {
	const directory = process.env.DC_INDICATORS_EVIDENCE_DIR;
	return directory ? `${directory.replace(/[\\/]$/, '')}/${name}` : testInfo.outputPath(name);
}

function communeEvidencePath(testInfo: { outputPath(name: string): string }, name: string): string {
	const directory = process.env.DC_PUBLIC_MESSAGES_EVIDENCE_DIR ?? process.env.DC_COMMUNE_EVIDENCE_DIR;
	return directory ? `${directory.replace(/[\\/]$/, '')}/${name}` : testInfo.outputPath(name);
}

async function mockAuth(page: Page) {
	await page.route('**/auth/me', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_USER) })
	);
}

function communeRead(code: string, responses: NonNullable<SourceExpectation['responses']>): SourceExpectation {
	return { method: 'GET', pathname: '/v1/commune', query: { code_insee: code }, minCalls: 1, maxCalls: responses.length, responses };
}

function referentielRead(code: string, lignes: Parameters<typeof reponseReferentiel>[0], calls = 1): SourceExpectation {
	return {
		method: 'GET', pathname: '/v1/communes', query: { code_insee: code }, minCalls: calls, maxCalls: calls,
		responses: Array.from({ length: calls }, () => ({ status: 200, body: reponseReferentiel(lignes) }))
	};
}

async function mockCommune(page: Page, codeInsee: string, status: number, body: unknown): Promise<SsrSourceProbe> {
	return configureSsrSource(page.request, [communeRead(codeInsee, [{ status, body }])]);
}

async function hydrate(page: Page, url: string) {
	const hydrated = page.waitForResponse((response) => response.url().endsWith('/auth/me'));
	const response = await page.goto(url);
	await hydrated;
	return response;
}

async function noHorizontalOverflow(page: Page) {
	const layout = await page.evaluate(() => ({ viewport: innerWidth, scrollWidth: document.documentElement.scrollWidth }));
	expect(layout.scrollWidth, `document ${layout.scrollWidth}px > viewport ${layout.viewport}px`).toBeLessThanOrEqual(layout.viewport);
}

/** The KPI ids of the strip grouped by visual row (same top edge), in DOM order. */
async function rangeesDesReperes(page: Page): Promise<string[][]> {
	const boites = await page.locator('dl.kpi-strip .kpi').evaluateAll((noeuds) =>
		noeuds.map((noeud) => ({ id: noeud.getAttribute('data-kpi') ?? '', haut: Math.round(noeud.getBoundingClientRect().top) }))
	);
	const rangees: { haut: number; ids: string[] }[] = [];
	for (const boite of boites) {
		const rangee = rangees.find((r) => Math.abs(r.haut - boite.haut) <= 1);
		if (rangee) rangee.ids.push(boite.id);
		else rangees.push({ haut: boite.haut, ids: [boite.id] });
	}
	return rangees.sort((a, b) => a.haut - b.haut).map((r) => r.ids);
}

/** No text of a KPI card is cut or overflows: scrollWidth ≤ clientWidth on each card and each of its texts. */
async function aucunTexteTronque(page: Page, largeur: number) {
	const debords = await page.locator('dl.kpi-strip .kpi').evaluateAll((cartes) =>
		cartes.flatMap((carte) =>
			[carte, ...carte.querySelectorAll('dt, p, .state-value, .state-badge, .cle-note')]
				.filter((e) => e.scrollWidth > e.clientWidth)
				.map((e) => `${carte.getAttribute('data-kpi')} ${e.tagName}.${e.className} ${e.scrollWidth} > ${e.clientWidth}`)
		)
	);
	expect(debords, `texte tronqué ou débordant dans une carte à ${largeur} px`).toEqual([]);
}

/** The text of each landmark of the strip as its face shows it: the card without its Source dialog. */
async function facesDesReperes(page: Page): Promise<string[]> {
	return page.locator('dl.kpi-strip > .cle').evaluateAll((cartes) =>
		cartes.map((carte) => {
			const face = carte.cloneNode(true) as HTMLElement;
			for (const dialogue of face.querySelectorAll('dialog')) dialogue.remove();
			return face.textContent?.replace(/\s+/g, ' ').trim() ?? '';
		})
	);
}

async function motsEntiersDesReperes(page: Page, largeur: number) {
	const coupes = await page.locator('dl.kpi-strip').evaluate((root) => {
		const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
		const failures: string[] = [];
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
		return failures;
	});
	expect(coupes, `mots coupés dans les repères à ${largeur} px`).toEqual([]);
}

async function captureSousEntete(
	page: Page,
	block: Locator,
	width: 1440 | 375,
	testInfo: { outputPath(name: string): string }
) {
	const baseHeight = width === 1440 ? 900 : 800;
	await page.setViewportSize({ width, height: baseHeight });
	const header = page.locator('header.site-header');
	const [headerBox, blockBox] = await Promise.all([header.boundingBox(), block.boundingBox()]);
	expect(headerBox && blockBox, `repères et en-tête mesurables à ${width} px`).toBeTruthy();
	const height = Math.max(baseHeight, Math.ceil(headerBox!.height + blockBox!.height + 32));
	await page.setViewportSize({ width, height });
	await block.evaluate((element, headerHeight) => {
		const documentTop = element.getBoundingClientRect().top + window.scrollY;
		window.scrollTo({ top: Math.max(0, documentTop - Number(headerHeight) - 16), behavior: 'instant' });
	}, headerBox!.height);
	const [positionedHeader, positionedBlock] = await Promise.all([
		header.boundingBox(),
		block.boundingBox()
	]);
	expect(positionedHeader && positionedBlock, `repères positionnés à ${width} px`).toBeTruthy();
	expect(positionedBlock!.y).toBeGreaterThanOrEqual(
		positionedHeader!.y + positionedHeader!.height
	);
	expect(positionedBlock!.y + positionedBlock!.height).toBeLessThanOrEqual(height);
	await block.screenshot({
		path: communeEvidencePath(testInfo, `reperes-85191-${width}.png`),
		animations: 'disabled'
	});
}

test.describe('Fiche Commune — synthèse et volet source (contrat mocké, déterministe)', () => {
	test.beforeEach(async ({ page }) => {
		await mockAuth(page);
	});

	test('KPI ① : valeur, effectif, fenêtre et source ; le bouton i ouvre la définition du contrat', async ({ page }) => {
		const source = await mockCommune(page, '85047', 200, MOCK_COMMUNE_85047);
		await hydrate(page, '/commune/85047');

		await expect(page.getByRole('heading', { level: 1, name: 'Challans' })).toBeVisible();
		await expect(page.getByRole('navigation', { name: "Fil d'Ariane" }).getByRole('link', { name: 'Accueil' })).toHaveAttribute('href', '/');
		await expect(page.getByText('Fiche commune', { exact: true })).toBeVisible();
		// The copy button sits inline after the INSEE code (03-17 F9): the line reads in two pieces.
		await expect(page.locator('.fiche-sous')).toContainText('Vendée (85) · INSEE 85047');
		await expect(page.locator('.fiche-sous')).toContainText('· 21\u202f516 habitants (2021, INSEE)');
		const kpi = page.locator('[data-kpi="prix-maisons"]');
		// The label of a tile is its `dt` (UI-SPEC v4 § 3.2), no longer a heading.
		await expect(kpi.locator('dt.kpi-label')).toHaveText('Prix médian des maisons');
		await expect(kpi.locator('dt.kpi-label')).toBeVisible();
		await expect(kpi.locator('.state-value .value')).toHaveText('2\u202f000');
		await expect(kpi.locator('.state-value .unit')).toHaveText('€/m²');
		// One short line under the value; definition, source and qualification are in the panel (4.9, lot 3).
		await expect(kpi.locator('.kpi-line .kpi-meta')).toHaveText('12 ventes · 2025');
		await expect(kpi.locator('.kpi-source')).toHaveCount(0);
		await expect(kpi.locator('.cle-note')).toHaveCount(0);

		const bouton = kpi.getByRole('button', { name: 'Détail : Prix médian des maisons' });
		await expect(bouton).not.toHaveAttribute('title');
		await bouton.click();
		const volet = page.getByRole('dialog', { name: 'Prix médian des maisons' });
		await expect(volet).toBeVisible();
		await expect(volet.getByRole('heading', { name: 'Prix médian des maisons' })).toBeFocused();
		const entree = (terme: string) => volet.locator('.source-panel-entry', { has: page.locator('dt', { hasText: new RegExp(`^${terme}$`) }) }).locator('dd');
		await expect(entree('Définition')).toHaveText(definition('dvf.indicateurs.maison.mediane_courante'));
		await expect(entree('Période')).toHaveText('2025-01-01 → 2026-01-01 (fin exclue)');
		await expect(entree('Unité')).toHaveText('€/m²');
		await expect(entree('Source')).toHaveText('DVF synthétique');
		await expect(entree('Effectif')).toHaveText('12 ventes');
		await volet.getByText("Voir la donnée telle que l'API la renvoie").click();
		await expect(volet.locator('.source-panel-raw')).toContainText('dvf.indicateurs.maison.mediane_courante');
		await expect(volet.locator('.source-panel-raw')).toContainText('2000');

		await page.keyboard.press('Escape');
		await expect(volet).toBeHidden();
		await expect(bouton).toBeFocused();
		await source.assert();
	});

	// N-2 (plan 04.5-03): the served inclusive window is spelled on the card, its served end included;
	// the panel keeps it as served, without « fin exclue ».
	test('KPI historique : fenêtre DVF inclusive en toutes lettres sur la carte (fin incluse), servie telle quelle dans Source, valeur et effectif inchangés', async ({ page }, testInfo) => {
		const source = await mockCommune(page, '85047', 200, {
			...MOCK_COMMUNE_85047,
			donnees: [{
				...COMMUNE_FLAT_85047_WITH_DVF_WINDOW,
				prix_m2_median_maison: 2000,
				nb_mutations_12m_maison: 12
			}]
		});
		await hydrate(page, '/commune/85047');

		const kpi = page.locator('[data-kpi="prix-maisons"]');
		await expect(kpi.locator('.state-value .value')).toHaveText('2\u202f000');
		await expect(kpi.locator('.state-value .unit')).toHaveText('€/m²');
		await expect(kpi.locator('.kpi-line .kpi-meta')).toHaveText('12 ventes · du 31 décembre 2024 au 31 décembre 2025');
		const periode = kpi.locator('dialog .source-panel-entry', { has: page.locator('dt', { hasText: /^Période$/ }) }).locator('dd');
		await expect(periode).toHaveText('2024-12-31 → 2025-12-31');
		// On screen the tile reads as its count, the period is behind the « i »; it stays on paper, whole.
		const lu = await kpi.locator('.kpi-line .kpi-meta').evaluate((el) => (el as HTMLElement).innerText.replace(/\s+/g, ' ').trim());
		expect(lu, 'la tuile à l’écran').toBe('12 ventes');
		await page.setViewportSize({ width: 375, height: 900 });
		const sourceButton = await kpi.getByRole('button', { name: 'Détail : Prix médian maison (hors ventes sur plan)' }).boundingBox();
		expect(sourceButton, 'bouton i du repère mesuré à 375 px').toBeTruthy();
		expect(sourceButton!.width, 'largeur de la cible du i à 375 px').toBeGreaterThanOrEqual(44);
		expect(sourceButton!.height, 'hauteur de la cible du i à 375 px').toBeGreaterThanOrEqual(44);
		await page.emulateMedia({ media: 'print' });
		for (const width of [1440, 375]) {
			await page.setViewportSize({ width, height: 900 });
			const meta = kpi.locator('.kpi-line .kpi-meta');
			await expect(meta).toHaveText('12 ventes · du 31 décembre 2024 au 31 décembre 2025');
			for (const date of ['31 décembre 2024', '31 décembre 2025']) {
				const tops = await meta.evaluate((element, isoDate) => {
					const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
					let textNode: Text | null = null;
					let offset = -1;
					while (walker.nextNode()) {
						const candidate = walker.currentNode as Text;
						const found = candidate.data.indexOf(isoDate);
						if (found >= 0) {
							textNode = candidate;
							offset = found;
							break;
						}
					}
					if (!textNode) throw new Error(`Date absente des nœuds texte : ${isoDate}`);
					const range = document.createRange();
					range.setStart(textNode, offset);
					range.setEnd(textNode, offset + isoDate.length);
					return [...range.getClientRects()].map((rect) => rect.top);
				}, date);
				expect(tops.length, `rectangles de ${date} à ${width} px`).toBeGreaterThan(0);
				const topDelta = Math.max(...tops) - Math.min(...tops);
				console.log(`RELEVE date=${date} largeur=${width}px tops=${tops.join(',')} delta=${topDelta}`);
				expect(topDelta, `${date} coupée sur plusieurs lignes à ${width} px`).toBeLessThanOrEqual(1);
			}
			const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
			expect(axe.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual([]);
			await kpi.screenshot({
				path: testInfo.outputPath(`prix-historique-periode-${width}.png`),
				animations: 'disabled'
			});
		}
		await source.assert();
	});

	// N-2 (plan 04.5-03, M1 of the counter-review): the face of a landmark is its card without its
	// Source dialog, which is mounted inside the card and keeps the exact served dates.
	test('N-2 : aucune date ISO sur la face des repères de /commune/85191, la forme servie reste dans Source', async ({ page }) => {
		for (const width of [1440, 375]) {
			const source = await mockCommune(page, '85191', 200, MOCK_COMMUNE_85191);
			await page.setViewportSize({ width, height: 900 });
			await hydrate(page, '/commune/85191');
			await expect(page.locator('[data-kpi]')).toHaveCount(KPI_85191.length);
			const faces = await facesDesReperes(page);
			expect(faces.length).toBe(KPI_85191.length);
			for (const face of faces) expect(face, `face d'un repère à ${width} px`).not.toMatch(/\d{4}-\d{2}-\d{2}/);
			await expect(page.locator('[data-kpi="prix-maisons"] .kpi-line .kpi-meta')).toHaveText('460 ventes · du 31 décembre 2024 au 31 décembre 2025');
			for (const id of ['prix-maisons', 'prix-appartements']) {
				const periode = page.locator(`[data-kpi="${id}"] dialog .source-panel-entry`, { has: page.locator('dt', { hasText: /^Période$/ }) }).locator('dd');
				await expect(periode).toHaveText('2024-12-31 → 2025-12-31');
			}
			await source.assert();
		}
	});

	test('KPI ① : fenêtre DVF absente, valeur et effectif inchangés, période explicitement inconnue', async ({ page }) => {
		// The legacy fixture deliberately has no `fenetre_mediane_dvf`: absence remains accepted,
		// but the interface must not infer a year from another DVF block.
		const communeSansFenetre = {
			...COMMUNE_FLAT_85047,
			prix_m2_median_maison: 2000,
			nb_mutations_12m_maison: 12
		};
		const source = await mockCommune(page, '85047', 200, {
			...MOCK_COMMUNE_85047,
			donnees: [communeSansFenetre]
		});
		await hydrate(page, '/commune/85047');

		const kpi = page.locator('[data-kpi="prix-maisons"]');
		await expect(kpi.locator('.state-value .value')).toHaveText('2\u202f000');
		await expect(kpi.locator('.state-value .unit')).toHaveText('€/m²');
		await expect(kpi.locator('.kpi-line .kpi-meta')).toHaveText('12 ventes · Période de mesure non renseignée');
		await expect(kpi.locator('.kpi-line .kpi-meta')).not.toContainText(/2024|2025|2026|12 mois glissants/);
		await source.assert();
	});

	test('KPI ① : blocs datés non publiés par l’instance, le prix plat reste servi', async ({ page }) => {
		// Todo 2026-11-01: a closed rollout gate serves both blocks as an explicit absence; the fiche
		// reads them as no block and keeps the flat price, never « Non qualifié » in its place.
		const nonPublies = { etat: 'not_qualified', motif: 'indicateurs_communaux_non_publies' };
		const commune = {
			...COMMUNE_FLAT_85047,
			prix_m2_median_maison: 2000,
			nb_mutations_12m_maison: 12,
			indicateurs_dvf: nonPublies,
			indicateurs_dpe: nonPublies
		};
		const source = await mockCommune(page, '85047', 200, { ...MOCK_COMMUNE_85047, donnees: [commune] });
		await hydrate(page, '/commune/85047');

		const kpi = page.locator('[data-kpi="prix-maisons"]');
		await expect(kpi.locator('.state-value .value')).toHaveText('2 000');
		await expect(kpi.locator('.state-value .unit')).toHaveText('€/m²');
		await expect(page.locator('[data-kpi]')).not.toContainText([LIBELLES_ETATS.not_qualified]);
		await source.assert();
	});

	test('ordre ① ② ③ ④ dans le DOM et au clavier, quelles que soient les valeurs', async ({ page }) => {
		const source = await mockCommune(page, '85047', 200, MOCK_COMMUNE_85047);
		await hydrate(page, '/commune/85047');

		const ids = await page.locator('[data-kpi]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-kpi')));
		expect(ids).toEqual(KPI_85047);
		const boutons = page.locator('.kpi .source-button');
		await boutons.first().focus();
		for (const id of KPI_85047.slice(1)) {
			await page.keyboard.press('Tab');
			await expect(page.locator(`[data-kpi="${id}"] .source-button`)).toBeFocused();
		}
		await source.assert();
	});

	test('KPI absent : badge du sous-état servi et sa phrase, jamais 0 ; tendance servie ; DPE A→G', async ({ page }) => {
		const source = await mockCommune(page, '85047', 200, MOCK_COMMUNE_85047);
		await hydrate(page, '/commune/85047');

		// ② flats: the median is withheld (insufficient_sample, 0 sale kept in the window). No tile: the
		// state, its sentence and its panel are on the row of Immobilier (4.9, lot 3; F56).
		await expect(page.locator('[data-kpi="prix-appartements"]')).toHaveCount(0);
		const appartements = page.locator('[data-ligne="dvf-appartement-mediane-courante"]');
		await expect(appartements.locator('.tab-v .state-badge')).toHaveText('Effectif insuffisant');
		await expect(appartements.locator('.tab-v .state-value')).toHaveCount(0);
		await expect(appartements.locator('.tab-v .state-badge')).not.toHaveText(/introuvable|^0$/);
		await appartements.getByRole('button', { name: 'Détail : Prix médian des appartements' }).click();
		const voletAppartements = page.getByRole('dialog', { name: 'Prix médian des appartements' });
		await expect(voletAppartements.locator('.source-panel-entry', { has: page.locator('dt', { hasText: /^État et motif$/ }) }))
			.toContainText('Aucune vente sur la période : pas de prix médian.');
		await page.keyboard.press('Escape');
		await expect(voletAppartements).toBeHidden();

		// ③ trend: the served 0 is a value, not an absence; the flats keep their state on their row.
		const tendance = page.locator('[data-kpi="tendance"]');
		await expect(tendance.locator('.kpi-sub')).toHaveText(['Maisons']);
		await expect(tendance.locator('.kpi-line').first().locator('.state-value')).toHaveText('0 %');
		await expect(tendance.locator('.state-badge')).toHaveCount(0);
		await expect(page.locator('[data-ligne="dvf-appartement-variation"] .tab-v .state-badge')).toHaveText('Effectif insuffisant');
		// The served qualification is read in the panel, never on the card (03-17 F28).
		await expect(tendance.locator('.kpi-line').first()).not.toContainText('synthetic');
		await tendance.getByRole('button', { name: 'Détail : Tendance du prix médian' }).click();
		const voletTendance = page.getByRole('dialog', { name: 'Tendance du prix médian' });
		await expect(voletTendance.locator('.source-panel-entry', { has: page.locator('dt', { hasText: /^Qualification$/ }) }).locator('dd'))
			.toHaveText('synthetic');
		await page.keyboard.press('Escape');
		await expect(voletTendance).toBeHidden();

		// ④ dominant DPE: the served modes, tied, in A→G order, then the ordinal median.
		const dpe = page.locator('[data-kpi="dpe-dominant"]');
		// The label of the theme row; 04.8-08 (D-31): the first DPE of the page is spelled out in the sub-label.
		await expect(dpe.locator('dt.kpi-label')).toHaveText('Classes les plus fréquentes, ex aequo');
		await expect(dpe.locator('.kpi-sub')).toHaveText(['Classe DPE (diagnostic de performance énergétique) médiane']);
		await expect(dpe.locator('[data-field-id="dpe.indicateurs.modes"]')).toHaveCount(1);
		await expect(dpe.locator('[data-field-id="dpe.indicateurs.mediane_ordinale"]')).toHaveCount(1);
		await expect(dpe.locator('.kpi-line').first().locator('.dpe-tag')).toHaveText(['Classe C', 'Classe E']);
		await expect(dpe.locator('.kpi-line').first()).toContainText('sur 5 diagnostics de classe connue');
		await expect(dpe.locator('.kpi-line').nth(1).locator('.dpe-tag')).toHaveText(['Classe D']);

		// Caveats of the synthesis, each anchored to its value.
		const reserves = page.locator('section', { has: page.getByRole('heading', { name: 'Ce qui mérite une lecture attentive' }) }).locator('li');
		expect(await reserves.count()).toBeGreaterThan(0);
		expect(await reserves.count()).toBeLessThanOrEqual(4);
		for (const lien of await reserves.getByRole('link').all()) {
			const cible = (await lien.getAttribute('href'))!;
			// A tile, or the group of a tile the page does not show (4.9: prices or DPE).
			expect(cible).toMatch(/^#(?:kpi-|critere-prix_commune$|critere-dpe$)/);
			await expect(page.locator(cible)).toHaveCount(1);
			// The link names the landmark it leads to (`.lien-t`), then the caveat (`.lien-n`).
			if (cible.startsWith('#kpi-')) {
				// 04.8-08 (D-31): the label of the tile may carry the full form of its acronym, said once on the page.
				const libelle = (await page.locator(cible).locator('dt.kpi-label').textContent())!.trim().split(' (diagnostic de performance énergétique)').join('');
				await expect(lien.locator('.lien-t')).toHaveText(libelle);
			}
			await expect(lien.locator('.lien-n')).not.toHaveText('');
		}
		await source.assert();
	});

	test('DPE médian plat manquant : pas de tuile, état servi sur sa ligne et aucune fausse classe', async ({ page }) => {
		const commune = {
			...COMMUNE_85191_OBSERVED,
			dpe_median: null,
			etats: {
				...COMMUNE_85191_OBSERVED.etats,
				dpe_median: { etat: 'not_reported', motif: 'Aucun diagnostic.' }
			}
		};
		const source = await mockCommune(page, '85191', 200, {
			statut: 'ok',
			donnees: [commune],
			meta: { source: COMMUNE_85191_SOURCES }
		});
		await hydrate(page, '/commune/85191');

		expect(await page.locator('[data-kpi]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-kpi')))).toEqual(['prix-maisons', 'prix-appartements']);
		const dpe = page.locator('#critere-dpe [data-field-id="dpe.dpe_median"]');
		await expect(dpe.locator('.tab-v .state-badge')).toHaveText('Non renseigné');
		await expect(dpe.locator('.tab-v .state-value, .tab-v .dpe-tag')).toHaveCount(0);
		await expect(dpe.locator('.tab-v').getByText('0', { exact: true })).toHaveCount(0);
		await dpe.getByRole('button', { name: 'Détail : Classe DPE médiane' }).click();
		const panel = page.getByRole('dialog', { name: 'Classe DPE médiane' });
		await expect(panel.locator('.source-panel-entry', { has: page.locator('dt', { hasText: /^État et motif$/ }) }))
			.toContainText('Aucun diagnostic.');
		await page.keyboard.press('Escape');
		await source.assert();
	});

	test('#immobilier atteint son groupe de prix, dans sa catégorie ouverte ; chaque ligne porte libellé, valeur ou état, bouton Source', async ({ page }) => {
		const source = await mockCommune(page, '85047', 200, MOCK_COMMUNE_85047);
		await hydrate(page, '/commune/85047#immobilier');

		// The old anchor lands on its group of prices, inside the category that holds it (4.9): above 1080 px
		// the card is open, nothing to unfold.
		const groupe = page.locator('#critere-prix_commune');
		await expect(groupe).toBeVisible();
		await expect(groupe).toBeInViewport();
		await expect(page.locator('details.cat#q-prix > summary .cat-titre')).toHaveText('Prix et ventes');
		const theme = page.locator('details.cat#q-prix');
		const lignes = theme.locator('dl.tab > .tab-l');
		const n = await lignes.count();
		expect(n).toBeGreaterThan(5);
		for (const ligne of await lignes.all()) {
			await expect(ligne.locator(':scope > dt')).not.toHaveText('');
			expect(await ligne.locator(':scope > dd > .tab-v > .state-value, :scope > dd > .tab-v > .state-absence').count()).toBe(1);
			await expect(ligne.getByRole('button', { name: /^Détail : / })).not.toHaveAttribute('title');
		}
		// A served value with its unit and its period (then its source, the note of the row); the object
		// is read in the panel of the row; a declared absence by its badge.
		const maisons = theme.locator('[data-field-id="dvf.prix_m2_median_maison"]');
		await expect(maisons.locator('.tab-v > .state-value .value')).toHaveText('2 480');
		await expect(maisons.locator('.tab-v > .state-value .unit')).toHaveText('€/m²');
		await expect(maisons.locator('.tab-note-texte')).toHaveText('2024-12-31 → 2025-12-31');
		await expect(maisons.locator('.source-panel-entry', { has: page.locator('dt', { hasText: /^Objet$/ }) }).locator('dd')).toHaveText('Commune');
		const appartements = theme.locator('[data-field-id="dvf.prix_m2_median_appartement"]');
		await expect(appartements.locator('.tab-v > .state-absence .state-badge')).toHaveText('Effectif insuffisant');
		await expect(appartements.locator('.tab-v > .state-value')).toHaveCount(0);
		// The Source button opens the definition served by the contract, in one action.
		await maisons.getByRole('button', { name: `Détail : ${maisonsLibelle}` }).click();
		const volet = page.getByRole('dialog', { name: maisonsLibelle });
		await expect(volet).toBeVisible();
		await expect(volet.locator('.source-panel-entry', { has: page.locator('dt', { hasText: /^Définition$/ }) }).locator('dd'))
			.toHaveText(definition('dvf.prix_m2_median_maison'));
		await page.keyboard.press('Escape');
		await source.assert();
	});

	test('trois catégories ouvertes dans leur ordre ; états servis par cellule', async ({ page }) => {
		// A source out of coverage for one cell, a missing value in another: both served states.
		const commune = {
			...COMMUNE_FLAT_85047, ...COMMUNE_INDICATORS_SYNTHETIC, nb_monuments: null,
			etats: { ...COMMUNE_FLAT_85047.etats, nb_monuments: { etat: 'out_of_coverage', motif: 'dataset_non_heberge' } }
		};
		const source = await mockCommune(page, '85047', 200, { ...MOCK_COMMUNE_85047, donnees: [commune] });
		await hydrate(page, '/commune/85047');

		// The three categories in their order, after « L’essentiel » and before the questions; each an open
		// card above 1080 px (4.9, lot 3). The rows of the five themes sit in their groups `#critere-…`.
		expect(await page.locator('section.panneau[id], details.cat[id]').evaluateAll((nodes) => nodes.map((node) => node.id)))
			.toEqual(['essentiel', ...CATEGORIES, 'questions', 'autour']);
		const categories = page.locator(CATEGORY_PANELS);
		await expect(categories.locator(':scope > summary > .cat-titre')).toHaveText([
			'Prix et ventes', 'Logement et DPE', 'Vivre dans la commune'
		]);
		await expect(page.locator('details.theme-section')).toHaveCount(0);
		for (const categorie of await categories.all()) {
			await expect(categorie.locator('dl.tab').first()).toBeVisible();
			expect(await categorie.locator('dl.tab > .tab-l').count()).toBeGreaterThan(0);
		}
		const energie = page.locator('#critere-dpe');
		await expect(energie.locator('[data-field-id="dpe.dpe_passoires_pct"] .tab-v > .state-value')).toBeVisible();

		// Out of coverage: warn badge with the label of its reason; a missing value stays neutral.
		const monumentsGroupe = page.locator('#critere-monuments');
		const monuments = monumentsGroupe.locator('[data-field-id="monuments.nb_monuments"] .tab-v > .state-absence .state-badge');
		await expect(monuments).toBeVisible();
		await expect(monuments).toHaveText('Hors couverture · jeu non installé');
		await expect(monuments).toHaveClass(/state-badge-warn/);
		await expect(page.locator('#critere-zonage_abc [data-field-id="zonage_abc.zone"] .tab-v > .state-value')).toHaveText('B2');
		const fibreGroupe = page.locator('#critere-fibre');
		const fibre = fibreGroupe.locator('[data-field-id="connectivite.fibre_pct"] .tab-v > .state-absence .state-badge');
		await expect(fibre).toBeVisible();
		await expect(fibre).toHaveText('Non renseigné');
		await expect(fibre).not.toHaveClass(/state-badge-warn/);
		await expect(fibreGroupe.locator('[data-field-id="connectivite.fibre_pct"] .tab-v > .state-value')).toHaveCount(0);
		// The label is the definition the API serves (E10), never the mock-up's « écoles ».
		await expect(page.locator('#critere-ecoles [data-field-id="scolaire.nb_etablissements_scolaires"] > dt')).toContainText('Collèges référencés par la sectorisation');
		// No row of a field the API does not serve (structural placeholders are gone).
		await expect(page.locator('[data-field-id="commune.risques.risques_naturels"], [data-field-id="commune.propriete.coproprietes"]')).toHaveCount(0);
		await expect(page.locator('main')).not.toContainText('Q1');
		await source.assert();
	});

	test('85191 historique : énergie, services et deux vacances gardent leur propre temps sans reprendre la date d’import', async ({ page }, testInfo) => {
		test.setTimeout(60_000);
		const source = await mockCommune(page, '85191', 200, {
			statut: 'ok',
			donnees: [COMMUNE_85191_OBSERVED],
			meta: { source: COMMUNE_85191_SOURCES }
		});
		await hydrate(page, '/commune/85191');

		await expect(page.getByRole('heading', { level: 1, name: 'La Roche-sur-Yon' })).toBeVisible();
		// The blocks read below: the group of energy, the group of schools, the group of vacancies (4.9).
		const energie = page.locator('#critere-dpe');
		const services = page.locator('#critere-ecoles');
		const vacances = page.locator('#critere-parc');
		const themes = [energie, services, vacances];
		const evidenceDir = process.env.DC_INDICATORS_EVIDENCE_DIR;
		if (evidenceDir) await mkdir(evidenceDir, { recursive: true });
		const communeEvidenceDir = process.env.DC_COMMUNE_EVIDENCE_DIR;
		if (communeEvidenceDir) await mkdir(communeEvidenceDir, { recursive: true });
		const strip = page.locator('dl.kpi-strip');
		const dpeRepere = page.locator('[data-kpi="dpe-dominant"]');

		expect(await page.locator('[data-kpi]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-kpi')))).toEqual(KPI_85191);
		// 04.8-08 (D-31): this label is the first DPE of the page.
		await expect(dpeRepere.locator('.kpi-label')).toHaveText('Classe DPE (diagnostic de performance énergétique) médiane');
		await expect(dpeRepere.locator('.kpi-sub')).toHaveCount(0);
		await expect(dpeRepere.locator('.kpi-label, .kpi-sub').filter({ hasText: /^Classe DPE \(diagnostic de performance énergétique\) médiane$/ })).toHaveCount(1);
		await expect(dpeRepere.locator('.dpe-tag')).toHaveText('Classe C');
		await expect(dpeRepere.locator('.kpi-meta')).toHaveText(
			'Effectif non renseigné · Dates des diagnostics non renseignées'
		);
		const dpeRepereButton = dpeRepere.getByRole('button', {
			name: 'Détail : Classe DPE médiane'
		});
		await dpeRepereButton.click();
		const dpeReperePanel = page.getByRole('dialog', { name: 'Classe DPE médiane' });
		const panelEntry = (term: string) =>
			dpeReperePanel.locator('.source-panel-entry', {
				has: page.locator('dt', { hasText: new RegExp(`^${term}$`) })
			}).locator('dd');
		await expect(panelEntry('Période')).toHaveText('Dates des diagnostics non renseignées');
		await expect(panelEntry('Source')).toHaveText('Diagnostics de performance énergétique (DPE)');
		await expect(panelEntry('Effectif')).toHaveText('Effectif non renseigné');
		await page.keyboard.press('Escape');
		await expect(dpeRepereButton).toBeFocused();

		const dpe = energie.locator('[data-field-id="dpe.dpe_median"]');
		const passoires = energie.locator('[data-field-id="dpe.dpe_passoires_pct"]');
		const construction = page.locator('#critere-construction [data-field-id="dpe.annee_construction_mediane"]');
		const scolaire = services.locator('[data-field-id="scolaire.nb_etablissements_scolaires"]');
		const fibre = page.locator('#critere-fibre [data-field-id="connectivite.fibre_pct"]');
		const logements = vacances.locator('[data-field-id="demographie.nb_logements"]');
		const recensement = vacances.locator('[data-field-id="demographie.nb_logements_vacants_recensement"]');
		const lovac = vacances.locator('[data-field-id="vacance.taux_vacance"]');
		const longVacants = vacances.locator('[data-field-id="vacance.nb_vacants_plus_2ans"]');
		const mutations = page.locator('#critere-prix_commune [data-field-id="dvf.nb_mutations_12m"]');
		const loyerYear = page.locator('#critere-loyers [data-field-id="loyers.annee"]');

		const captureBlock = async (block: Locator, width: number, name: string) => {
			const header = page.locator('header.site-header');
			const [headerBox, blockBox] = await Promise.all([header.boundingBox(), block.boundingBox()]);
			expect(headerBox, `en-tête mesurable avant capture ${name} à ${width} px`).toBeTruthy();
			expect(blockBox, `bloc mesurable avant capture ${name} à ${width} px`).toBeTruthy();
			const height = Math.max(900, Math.ceil(headerBox!.height + blockBox!.height + 32));
			await page.setViewportSize({ width, height });
			await block.evaluate((element, headerHeight) => {
				const documentTop = element.getBoundingClientRect().top + window.scrollY;
				window.scrollTo({ top: Math.max(0, documentTop - Number(headerHeight) - 16), behavior: 'instant' });
			}, headerBox!.height);
			const [positionedHeader, positionedBlock] = await Promise.all([header.boundingBox(), block.boundingBox()]);
			expect(positionedHeader, `en-tête mesurable après scroll ${name} à ${width} px`).toBeTruthy();
			expect(positionedBlock, `bloc mesurable après scroll ${name} à ${width} px`).toBeTruthy();
			expect(positionedBlock!.y, `bloc ${name} placé sous l’en-tête à ${width} px`).toBeGreaterThanOrEqual(
				positionedHeader!.y + positionedHeader!.height
			);
			expect(positionedBlock!.y + positionedBlock!.height, `bloc ${name} entièrement dans la fenêtre à ${width} px`).toBeLessThanOrEqual(height);
			await block.screenshot({
				path: indicatorsEvidencePath(testInfo, `85191-${name}-${width}.png`),
				animations: 'disabled'
			});
		};

		for (const width of [1440, 375] as const) {
			const viewportHeight = width === 1440 ? 900 : 800;
			await page.setViewportSize({ width, height: viewportHeight });
			await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
			await expect(page.getByRole('heading', { level: 1, name: 'La Roche-sur-Yon' })).toBeVisible();
			await expect(strip).toBeVisible();
			await page.screenshot({
				path: communeEvidencePath(testInfo, `premier-ecran-85191-${width}.png`),
				animations: 'disabled'
			});
			for (const id of KPI_85191) {
				const button = page.locator(`[data-kpi="${id}"]`).getByRole('button', { name: /^Détail : / });
				const box = await button.boundingBox();
				expect(box, `cible Source ${id} présente à ${width} px`).toBeTruthy();
				expect(box!.width, `largeur cible Source ${id} à ${width} px`).toBeGreaterThanOrEqual(44);
				expect(box!.height, `hauteur cible Source ${id} à ${width} px`).toBeGreaterThanOrEqual(44);
			}
			await motsEntiersDesReperes(page, width);
			await aucunTexteTronque(page, width);
			await noHorizontalOverflow(page);
			const repereAxe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
			expect(repereAxe.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual([]);
			await captureSousEntete(page, strip, width, testInfo);
			await page.setViewportSize({ width, height: 900 });
			// Under 1081 px the categories are folds: the reader opens them to read the rows of each block.
			await ouvrirCategories(page);
			for (const theme of themes) await expect(theme.locator('dl.tab').first()).toBeVisible();

			await captureBlock(energie, width, 'energie');
			await captureBlock(services, width, 'services');
			await captureBlock(vacances, width, 'vacances');
			await page.setViewportSize({ width, height: 900 });

			await expect.soft(passoires.locator('.tab-v .state-value')).toHaveText('3,3 %');
			await expect.soft(dpe.locator('.tab-v .state-value')).toHaveText('C');
			await expect.soft(construction.locator('.tab-v .state-value')).toHaveText('1997');
			await expect.soft(passoires.locator('.tab-note-texte')).toHaveText('Dates des diagnostics non renseignées');
			await expect.soft(dpe.locator('.tab-note-texte')).toHaveText('Dates des diagnostics non renseignées');
			await expect.soft(construction.locator('.tab-note-texte')).toHaveText('Dates des diagnostics non renseignées');
			await expect.soft(dpe.locator('.tab-n')).not.toContainText(/2026-10-08|08\/10\/2026/);

			await expect.soft(scolaire.locator(':scope > dt')).toContainText('Collèges référencés par la sectorisation');
			await expect.soft(scolaire.locator('.tab-v .state-value')).toHaveText('4');
			await expect.soft(scolaire.locator('.tab-note-texte')).toHaveText('Période de la sectorisation non renseignée');
			await expect.soft(fibre.locator('.tab-v .state-value')).toHaveText('98 %');
			await expect.soft(fibre.locator('.tab-note-texte')).toHaveText('Période source : T2 2026');

			await expect.soft(recensement.locator(':scope > dt')).toContainText('Logements vacants au recensement');
			await expect.soft(recensement.locator('.tab-v .state-value')).toHaveText('1\u202f870');
			await expect.soft(recensement.locator('.tab-note-texte')).toHaveText('Millésime 2022');
			await expect.soft(logements.locator('.tab-v .state-value')).toHaveText('31\u202f207');
			await expect.soft(mutations.locator('.tab-v .state-value')).toHaveText('835');
			await expect.soft(lovac.locator(':scope > dt')).toContainText('Taux de vacance du parc privé');
			await expect.soft(lovac.locator('.tab-v .state-value')).toHaveText('7,59 %');
			await expect.soft(lovac.locator('.tab-note-texte')).toHaveText('Vacants : 2026 · année du parc privé non renseignée');
			await expect.soft(longVacants.locator('.tab-v .state-value')).toHaveText('296');
			await expect.soft(longVacants.locator('.tab-note-texte')).toHaveText('Vacants : 2026');
			await expect.soft(loyerYear.locator('.tab-note-texte')).toHaveText('Millésime 2025');

			for (const row of [passoires, dpe, construction, scolaire, fibre, logements, recensement, lovac, longVacants, mutations, loyerYear]) {
				const box = await row.getByRole('button', { name: /^Détail : / }).boundingBox();
				expect(box, `cible Source présente à ${width} px`).toBeTruthy();
				expect(box!.width, `largeur cible Source à ${width} px`).toBeGreaterThanOrEqual(44);
				expect(box!.height, `hauteur cible Source à ${width} px`).toBeGreaterThanOrEqual(44);
			}
			await noHorizontalOverflow(page);
			const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
			expect(axe.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual([]);
		}

		const assertImportOnlyInPanel = async (row: typeof dpe, title: string, expected: string) => {
			const button = row.getByRole('button', { name: `Détail : ${title}`, exact: true });
			await button.focus();
			await expect(button).toBeFocused();
			await button.press('Enter');
			const panel = row.locator('dialog.source-panel');
			await expect(panel).toBeVisible();
			const heading = panel.getByRole('heading');
			await expect(heading).toHaveText(title);
			await expect(heading).toBeFocused();
			const importEntry = panel.locator('.source-panel-entry', {
				has: page.locator('dt', { hasText: /^Dernière importation réussie$/ })
			}).locator('dd');
			await expect(importEntry).toHaveText(expected);
			await page.keyboard.press('Escape');
			await expect(panel).toBeHidden();
			await expect(button).toBeFocused();
		};
		await assertImportOnlyInPanel(dpe, 'Classe DPE médiane', '08/10/2026');
		await assertImportOnlyInPanel(scolaire, 'Collèges référencés par la sectorisation', '24/09/2026');
		await assertImportOnlyInPanel(fibre, 'Fibre (immeubles observés)', '26/09/2026');
		await assertImportOnlyInPanel(recensement, 'Logements vacants au recensement', '24/09/2026');
		await assertImportOnlyInPanel(lovac, 'Taux de vacance du parc privé', '25/09/2026');

		await source.assert();
	});

	test('export CSV accessible depuis la synthèse ; cellules qualifiées au CSV et à l’impression', async ({ page }, testInfo) => {
		const source = await mockCommune(page, '85047', 200, MOCK_COMMUNE_85047);
		await page.setViewportSize({ width: 375, height: 900 });
		await hydrate(page, '/commune/85047');

		const allDownload = page.waitForEvent('download');
		await page.getByRole('button', { name: 'Télécharger (CSV)' }).click();
		await page.getByRole('dialog').getByRole('button', { name: /^Télécharger \(\d+\)$/ }).click();
		const allCsv = await readFile((await (await allDownload).path())!, 'utf8');
		await writeFile(testInfo.outputPath('commune-85047-export.csv'), allCsv);
		expect(allCsv.startsWith('\uFEFF')).toBe(true);
		expect(allCsv).toContain('Prix médian des maisons (fenêtre courante)');
		expect(allCsv).toContain('Zone ABC');

		// Every row, under the three categories of the page, in their order, with its served state and caveats
		// (C1, arbitrated by Ianis on 2026-11-10: the CSV follows the categories; it listed the five former themes).
		const lignes = allCsv.split(/\r?\n/);
		expect(lignes[0]).toBe('\uFEFFThème,Indicateur,Valeur ou motif,État,Maille,"Période, population, source et réserves"');
		const themesExportes = [...new Set(lignes.slice(1).filter(Boolean).map((ligne) => ligne.split(',')[0]))];
		expect(themesExportes).toEqual(['Prix et ventes', 'Logement et DPE', 'Vivre dans la commune']);
		expect(allCsv).toContain('2025-01-01');
		expect(allCsv).toContain('2026-01-01');
		expect(allCsv).toContain('12 mutations');
		expect(allCsv).toContain('Fixture synthétique : aucune qualification réelle.');
		// A declared absence exports its served state and reason, never a 0 nor a blank.
		expect(lignes.some((ligne) => ligne.startsWith('Prix et ventes,Prix médian appartement (hors ventes sur plan),Effectif insuffisant,insufficient_sample,'))).toBe(true);
		expect(allCsv).toContain('Motif\u00a0: 3 ventes d’appartements sur 12 mois, sous le seuil de publication de 5.');
		expect(allCsv).toContain('Classe DPE médiane,D');
		expect(allCsv).toContain('Classes les plus fréquentes, ex aequo');
		expect(allCsv).toContain('C, E');
		expect(allCsv).toContain('Population : synthetic-e2e');
		expect(allCsv).toContain('DPE synthétique');

		// One category only: its group checkbox exports its rows and nothing else.
		const themeDownload = page.waitForEvent('download');
		await page.getByRole('button', { name: 'Télécharger (CSV)' }).click();
		const dialog = page.getByRole('dialog', { name: 'Exporter les données' });
		await dialog.getByRole('button', { name: 'Tout décocher' }).click();
		await dialog.getByRole('checkbox', { name: 'Logement et DPE' }).check();
		await dialog.getByRole('button', { name: /^Télécharger \(\d+\)$/ }).click();
		const energyCsv = await readFile((await (await themeDownload).path())!, 'utf8');
		await writeFile(testInfo.outputPath('commune-85047-energie.csv'), energyCsv);
		expect(energyCsv.split(/\r?\n/).slice(1).filter(Boolean).every((ligne) => ligne.startsWith('Logement et DPE,'))).toBe(true);
		expect(energyCsv).toContain('Classe DPE médiane,D');

		// Every category is open on screen (under 1081 px the reader opens it); printing opens the questions
		// (beforeprint), shows the caveats of each row under its value and hides the chrome.
		await ouvrirCategories(page);
		for (const id of CATEGORIES) await expect(page.locator(`details.cat#${id} dl.tab`).first()).toBeVisible();
		const questions = page.locator('[data-faq] details.det');
		expect(await questions.count()).toBeGreaterThan(0);
		for (const question of await questions.all()) await expect(question).not.toHaveAttribute('open', '');
		// The caveats of the rows are said by their source panel on screen and printed under their value.
		const reserves = page.locator('details.cat .tab-reserve');
		expect(await reserves.count()).toBeGreaterThan(0);
		await expect(reserves.first()).toBeHidden();
		await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
		await page.emulateMedia({ media: 'print' });
		await expect(page.locator('[data-kpi="prix-maisons"] .state-value')).toBeVisible();
		await expect(page.locator('#critere-prix_commune .theme-note').getByText('Fixture synthétique : aucune qualification réelle.').first()).toBeVisible();
		await expect(reserves.first()).toBeVisible();
		for (const id of CATEGORIES) await expect(page.locator(`details.cat#${id} dl.tab`).first()).toBeVisible();
		for (const question of await questions.all()) {
			await expect(question).toHaveAttribute('open', '');
			await expect(question.locator('p')).toBeVisible();
		}
		await expect(page.locator('.source-button').first()).toBeHidden();
		await expect(page.getByRole('button', { name: 'Télécharger (CSV)' })).toBeHidden();
		await expect(page.getByRole('caption').filter({ hasText: 'Sources et millésimes des données de cette fiche' })).toBeVisible();
		await page.pdf({ path: testInfo.outputPath('commune-qualified-a4.pdf'), format: 'A4', printBackground: true });
		// After printing, the questions get their state back (closed); the theme panels stay open.
		await page.evaluate(() => window.dispatchEvent(new Event('afterprint')));
		for (const question of await questions.all()) await expect(question).not.toHaveAttribute('open', '');
		for (const id of CATEGORIES) await expect(page.locator(`details.cat#${id}`)).toBeAttached();
		await source.assert();
	});

	test('320 et 375 px : aucun débordement, aucun position: fixed hors en-tête et <dialog>', async ({ page }, testInfo) => {
		const source = await mockCommune(page, '85047', 200, MOCK_COMMUNE_85047);
		await hydrate(page, '/commune/85047');
		for (const width of [320, 375, 1280, 1440]) {
			await page.setViewportSize({ width, height: 900 });
			await expect(page.locator('[data-kpi="prix-maisons"] .state-value')).toBeVisible();
			await noHorizontalOverflow(page);
			const fixes = await page.evaluate(() =>
				[...document.querySelectorAll('body *')]
					.filter((el) => getComputedStyle(el).position === 'fixed')
					// The skip link predates this plan (app.css, 03-03): off-screen until focused.
					.filter((el) => el.tagName !== 'DIALOG' && !el.classList.contains('skip-link'))
					.map((el) => `${el.tagName}.${el.className}`)
			);
			expect(fixes).toEqual([]);
			await page.screenshot({ path: testInfo.outputPath(`commune-kpi-${width}.png`), fullPage: true });
			if (width === 375 || width === 1440) {
				await page.locator('[data-kpi="prix-maisons"]').screenshot({
					path: testInfo.outputPath(`prix-maisons-periode-${width}.png`),
					animations: 'disabled'
				});
			}
			// Tiles of the direction A (UI-SPEC v4 § 3.2, § 6.2): four columns above 1080 px, two at
			// 1080 px and under, one at 400 px and under (DOM order kept); no text of a tile is cut.
			const rangees = await rangeesDesReperes(page);
			expect(rangees, `rangées des repères à ${width} px`).toEqual(width > 1080
				? [KPI_85047]
				: width > 400
					? [['prix-maisons', 'tendance'], ['dpe-dominant']]
					: KPI_85047.map((id) => [id]));
			await aucunTexteTronque(page, width);
			if (width !== 375) continue;

			// At 375 × 800 (a phone) on the densest synthetic fixture (a trend of two lines): under each
			// value its short line « effectif · période », which may wrap but is never cut; the tiles are
			// one column (UI-SPEC v4 § 3.2, which replaces the two rows of two of 03-17 R-1 option (c1)
			// and their half-window cap); « appartements » is not cut, the copy button is a 44 px target.
			await page.setViewportSize({ width: 375, height: 800 });
			await aucunTexteTronque(page, 375);
			const metas = await page.locator('dl.kpi-strip .kpi-meta').evaluateAll((noeuds) =>
				noeuds.map((noeud) => ({
					texte: noeud.textContent?.replace(/\s+/g, ' ').trim(),
					hauteur: noeud.getBoundingClientRect().height,
					ligne: parseFloat(getComputedStyle(noeud).lineHeight)
				}))
			);
			const cartes = await page.locator('dl.kpi-strip .kpi').evaluateAll((noeuds) =>
				noeuds.map((noeud) => `${noeud.getAttribute('data-kpi')}:${Math.round(noeud.getBoundingClientRect().height)}x${Math.round(noeud.getBoundingClientRect().width)}`)
			);
			const strip = await page.locator('dl.kpi-strip').boundingBox();
			const moitie = await page.evaluate(() => window.innerHeight / 2);
			console.log(`RELEVE 375px reperes=${strip!.height} plafond=${moitie} cartes=${cartes.join(',')} metas=${JSON.stringify(metas)}`);
			expect(metas.length, 'une ligne effectif · période sous les valeurs').toBeGreaterThan(0);
			const rectangles = await page.locator('[data-kpi="prix-maisons"] dt.kpi-label').evaluate((titre) => {
				const noeud = [...titre.childNodes].find((n) => n.nodeType === Node.TEXT_NODE && n.textContent!.includes('maisons'))!;
				const debut = noeud.textContent!.indexOf('maisons');
				const range = document.createRange();
				range.setStart(noeud, debut);
				range.setEnd(noeud, debut + 'maisons'.length);
				return range.getClientRects().length;
			});
			expect(rectangles, '« maisons » sur une seule ligne').toBe(1);
			const copier = page.locator('.fiche-sous').getByRole('button', { name: 'Copier le code INSEE' });
			expect((await copier.boundingBox())!.height, 'zone tactile de « Copier le code INSEE »').toBeGreaterThanOrEqual(44);
			// The clipboard of a headless browser is not the subject: a stub answers, the page announces.
			await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { value: { writeText: async () => {} }, configurable: true }));
			await copier.click();
			await expect(page.locator('.fiche-sous').getByRole('status')).toHaveText('Code INSEE copié');
		}
		await source.assert();
	});
});

test.describe('Fiche Commune — états de la page (hors couverture ≠ panne ≠ INSEE inconnu)', () => {
	test.beforeEach(async ({ page }) => {
		await mockAuth(page);
	});

	test('commune hors couverture : fiche titrée par le référentiel, motif, aucun KPI ni thème (statut 200)', async ({ page }, testInfo) => {
		const source = await configureSsrSource(page.request, [
			communeRead('35238', [
				{ status: 200, body: reponseHorsCouverture('territoire_hors_perimetre') },
				{ status: 200, body: reponseHorsCouverture('territoire_hors_perimetre') }
			]),
			referentielRead(
				'35238',
				[{ code_insee: '35238', nom: 'Rennes', departement: '35', population: 222485, code_postal: '35000' }],
				2
			)
		]);
		for (const width of [1440, 375] as const) {
			await page.setViewportSize({ width, height: 900 });
			const response = await page.goto('/commune/35238');
			expect(response?.status()).toBe(200);
			await expect(page.getByRole('heading', { level: 1, name: 'Rennes' })).toBeVisible();
			await expect(page.locator('.fiche-sous')).toContainText('35000 · Ille-et-Vilaine (35) · INSEE 35238');
			const encart = page.getByRole('status').filter({ hasText: 'Commune hors couverture' });
			await expect(encart).toContainText('Ce site ne couvre pas ce département. Une absence de couverture ne vaut pas zéro.');
			await expect(encart.getByRole('link', { name: 'Voir la couverture des données' })).toHaveAttribute('href', '/datasets');
			await expect(encart).not.toContainText(/\binstance\b/i);
			await expect(encart.getByRole('link', { name: 'Revenir aux résultats' })).toHaveAttribute('href', '/communes?q=Rennes');
			await expect(page.locator('[data-kpi]')).toHaveCount(0);
			await expect(page.locator(CATEGORY_PANELS)).toHaveCount(0);
			await expect(page.getByText('Période de mesure non renseignée', { exact: true })).toHaveCount(0);
			await expect(page.getByText('2024-12-31 → 2025-12-31', { exact: true })).toHaveCount(0);
			await expect(page.getByText('Les données de cette commune sont momentanément indisponibles')).toHaveCount(0);
			await noHorizontalOverflow(page);
			await page.screenshot({
				path: communeEvidencePath(testInfo, `commune-hors-couverture-${width}.png`),
				animations: 'disabled'
			});
		}
		await source.assert();
	});

	test('motif de hors couverture hors contrat : la page reste servie, sans phrase de motif inventée', async ({ page }) => {
		const reponse = reponseHorsCouverture('territoire_hors_perimetre');
		reponse.meta.hebergement.commune.motif = 'motif_inconnu' as never;
		const source = await configureSsrSource(page.request, [
			communeRead('35238', [{ status: 200, body: reponse }]),
			referentielRead('35238', [{ code_insee: '35238', nom: 'Rennes', departement: '35', population: 222485, code_postal: '35000' }])
		]);
		const response = await page.goto('/commune/35238');
		expect(response?.status()).toBe(200);
		const encart = page.getByRole('status').filter({ hasText: 'Commune hors couverture' });
		await expect(encart.locator('p').first()).toHaveText('Une absence de couverture ne vaut pas zéro.');
		await expect(page.getByText('motif_inconnu')).toHaveCount(0);
		await source.assert();
	});

	test('panne : encart global, h1 conservé, aucune valeur ; « Recharger la fiche » relance une seule fois', async ({ page }) => {
		// Anonymous visitor: the reload is a browser read, sent with the public key of the bundle.
		await page.route('**/auth/me', (route) =>
			route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ detail: 'Non authentifié' }) })
		);
		await page.setViewportSize({ width: 375, height: 900 });
		const source = await configureSsrSource(page.request, [
			communeRead('85047', [
				{ status: 503, body: REPONSE_PANNE, headers: { 'Retry-After': '45' } },
				{ status: 200, body: MOCK_COMMUNE_85047 }
			]),
			referentielRead('85047', [{ code_insee: '85047', nom: 'Challans', departement: '85', population: 21516, code_postal: '85300' }])
		]);
		const reponse = await hydrate(page, '/commune/85047');
		// 04.7-10 (T-10, Opus P3-8): the page stays readable but is answered 503, with the delay the API served,
		// so that a robot retries instead of indexing an « unavailable » page; the internal marker never leaves.
		expect(reponse?.status()).toBe(503);
		expect(reponse?.headers()['retry-after']).toBe('45');
		expect(reponse?.headers()['x-dc-etat-page']).toBeUndefined();
		expect(reponse?.headers()['x-dc-retry-after']).toBeUndefined();
		await expect(page.getByRole('heading', { level: 1, name: 'Challans' })).toBeVisible();
		const encart = page.getByRole('alert').filter({ hasText: 'Les données de cette commune sont momentanément indisponibles' });
		await expect(encart).toContainText("Le service n'a pas répondu. Aucune valeur n'est affichée plutôt qu'une valeur incomplète.");
		await expect(page.locator('[data-kpi]')).toHaveCount(0);
		await expect(page.locator(CATEGORY_PANELS)).toHaveCount(0);
		await expect(page.getByText('Commune hors couverture')).toHaveCount(0);
		await noHorizontalOverflow(page);

		// A double activation sends one request: the source allows exactly two reads in all.
		const recharger = encart.getByRole('button', { name: 'Recharger la fiche' });
		await recharger.dblclick();
		await expect(page.locator('[data-kpi]')).toHaveCount(KPI_85047.length);
		await expect(page.locator(CATEGORY_PANELS)).toHaveCount(CATEGORIES.length);
		await expect(page.getByRole('heading', { level: 1, name: 'Challans' })).toBeVisible();
		await source.assert();
	});

	test('INSEE inconnu du référentiel : page 404 du contrat, distincte du hors couverture', async ({ page }) => {
		const source = await configureSsrSource(page.request, [
			communeRead('00000', [{ status: 200, body: REPONSE_SANS_LIGNE }]),
			referentielRead('00000', [])
		]);
		const response = await page.goto('/commune/00000');
		expect(response?.status()).toBe(404);
		await expect(page.getByText('Aucune commune ne porte le code INSEE 00000 dans le référentiel.')).toBeVisible();
		// 04.8-07 (UI-SPEC § 6.10): the root 404 renders this case; its way back is « L’accueil » of
		// « Pour repartir », next to the large field (« Revenir à la recherche » is no longer written).
		await expect(page.getByRole('main').getByRole('combobox', { name: 'Chercher une adresse ou une commune' })).toBeVisible();
		await expect(page.getByRole('region', { name: 'Pour repartir' }).getByRole('link', { name: 'L’accueil', exact: true })).toHaveAttribute('href', '/');
		await expect(page.getByText('Commune hors couverture')).toHaveCount(0);
		await expect(page.getByRole('main')).not.toContainText(/\binstance\b/i);
		await source.assert();
	});

	test('code malformé : page 404 sans aucun appel à l’API', async ({ page }) => {
		const source = await configureSsrSource(page.request, []);
		const response = await page.goto('/commune/4410A');
		expect(response?.status()).toBe(404);
		await expect(page.getByText('Aucune commune ne porte le code INSEE 4410A dans le référentiel.')).toBeVisible();
		const audit = await source.assert();
		expect(audit.requests.filter((request) => request.pathname.startsWith('/v1/'))).toEqual([]);
	});

	test('code INSEE corse : /commune/2A004 ouvre sa fiche', async ({ page }) => {
		const ajaccio = { ...COMMUNE_FLAT_85047, code_insee: '2A004', nom_commune: 'Ajaccio', code_departement: '2A', code_region: '94' };
		const source = await mockCommune(page, '2A004', 200, { statut: 'ok', donnees: [ajaccio], meta: { source: [] } });
		await hydrate(page, '/commune/2A004');
		await expect(page.getByRole('heading', { level: 1, name: 'Ajaccio' })).toBeVisible();
		await expect(page.locator('.fiche-sous')).toContainText('Corse-du-Sud (2A) · INSEE 2A004');
		await expect(page.locator('[data-kpi]')).toHaveCount(2);
		await source.assert();
	});
});

// Reading redesign, plan 04.5-02 (D45-07, D45-10): the commune fiche captured at 1440 × 900 and
// 375 × 812, on the frozen 85191 answer, with and without a theme in the URL. The same step runs at
// the starting SHA (« avant ») and after the change (« après »), on the same inputs; the directory
// is `DC_REFONTE_D_EVIDENCE_DIR`, the test output otherwise.
function refonteEvidencePath(testInfo: { outputPath(name: string): string }, name: string): string {
	const directory = process.env.DC_REFONTE_D_EVIDENCE_DIR;
	return directory ? `${directory.replace(/[\/]$/, '')}/${name}` : testInfo.outputPath(name);
}

test.describe('Fiche Commune — captures de la refonte D (04.5-02)', () => {
	test.beforeEach(async ({ page }) => {
		await mockAuth(page);
	});

	for (const [suffixe, recherche] of [['', ''], ['-theme-risques-urbanisme', '?theme=risques-urbanisme']] as const) {
		test(`captures 1440 et 375 de /commune/85191${recherche}`, async ({ page }, testInfo) => {
			test.setTimeout(60_000);
			if (process.env.DC_REFONTE_D_EVIDENCE_DIR) await mkdir(process.env.DC_REFONTE_D_EVIDENCE_DIR, { recursive: true });
			for (const [width, height] of [[1440, 900], [375, 812]] as const) {
				const source = await mockCommune(page, '85191', 200, { statut: 'ok', donnees: [COMMUNE_85191_OBSERVED], meta: { source: COMMUNE_85191_SOURCES } });
				await page.setViewportSize({ width, height });
				await hydrate(page, `/commune/85191${recherche}`);
				await expect(page.getByRole('heading', { level: 1, name: 'La Roche-sur-Yon' })).toBeVisible();
				await page.screenshot({ path: refonteEvidencePath(testInfo, `commune-85191${suffixe}-${width}.png`), animations: 'disabled' });
				await page.screenshot({ path: refonteEvidencePath(testInfo, `commune-85191${suffixe}-${width}-page.png`), animations: 'disabled', fullPage: true });
				await source.assert();
			}
		});
	}
});

// Plan 04.5-03 (UI-004, N-2): the essentials alone, at 1440 × 900 and 375 × 812, on the frozen 85191
// answer (flat window, trend not published) and on Challans with the dated blocks (trend published).
// No frozen /v1/commune answer of Nantes (44109) exists: Challans stands for a published trend.
// 04.7-15: each marker is « value + definition + base » (since 04.8-04 the definition is the note of its
// tile at every width, UI-SPEC v4 § 6.2), the served DPE distribution with its
// reading sentence, and one computed FAQPage whose JSON-LD is the visible text.
test.describe('Fiche Commune — chiffres définis et lus (04.7-15)', () => {
	test.beforeEach(async ({ page }) => {
		await mockAuth(page);
	});

	for (const [largeur, hauteur] of [[1440, 900], [375, 800], [320, 700]] as const) {
		test(`${largeur} px : définitions, répartition des DPE, une FAQPage égale au texte visible`, async ({ page }, testInfo) => {
			await page.setViewportSize({ width: largeur, height: hauteur });
			const source = await mockCommune(page, '85047', 200, MOCK_COMMUNE_85047);
			await hydrate(page, '/commune/85047');

			// The definition of each marker is in its panel, at every width (4.9, lot 3).
			for (const id of KPI_85047) {
				await expect(page.locator(`[data-kpi="${id}"] .cle-note`)).toHaveCount(0);
				await expect(page.locator(`[data-kpi="${id}"] .source-button`), `bouton du repère ${id} à ${largeur} px`).toBeVisible();
				await expect(page.locator(`[data-kpi="${id}"] dialog .source-panel-entry`).filter({ hasText: 'Définition' })).toHaveCount(1);
			}
			await expect(page.locator('[data-definitions]')).toHaveCount(0);
			expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(largeur);

			const figure = page.locator('[data-repartition-dpe]');
			await expect(figure.locator('[data-classe]')).toHaveText([/^A/, /^B/, /^C/, /^D/, /^E/, /^F/, /^G/]);
			await expect(figure.locator('[data-lecture]')).toContainText('de classe connue');

			const visibles = await page.locator('[data-faq-question]').evaluateAll((items) =>
				items.map((item) => [item.querySelector('summary')!.textContent, item.querySelector('p')!.textContent])
			);
			expect(visibles.length).toBeGreaterThan(0);
			const faqs = await page.locator('script[type="application/ld+json"]').evaluateAll((scripts) =>
				scripts.map((script) => JSON.parse(script.textContent ?? '{}')).filter((objet) => objet['@type'] === 'FAQPage')
			);
			expect(faqs).toHaveLength(1);
			expect(faqs[0].mainEntity.map((item: { name: string; acceptedAnswer: { text: string } }) => [item.name, item.acceptedAnswer.text])).toEqual(visibles);

			const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
			expect(axe.violations.map((violation) => violation.id)).toEqual([]);
			await page.screenshot({ path: indicatorsEvidencePath(testInfo, `04.7-15-commune-${largeur}.png`), fullPage: true });
			await source.assert();
		});
	}
	// Codex 15 pass 2: under 360 px a card is about 119 px wide; a long spelled day (« 26 septembre 2026 »,
	// with « 1er » at its start) wraps inside the card rather than overflowing it.
	test('320 px, sur papier : une période en toutes lettres reste dans sa carte', async ({ page }) => {
		await page.setViewportSize({ width: 320, height: 700 });
		const source = await mockCommune(page, '85191', 200, {
			statut: 'ok',
			donnees: [{ ...COMMUNE_85191_OBSERVED, fenetre_mediane_dvf: '2025-09-01 → 2026-09-26' }],
			meta: { source: COMMUNE_85191_SOURCES }
		});
		await hydrate(page, '/commune/85191');

		await page.emulateMedia({ media: 'print' });
		const date = page.locator('dl.kpi-strip .kpi-date', { hasText: '26 septembre 2026' }).first();
		await expect(date).toBeVisible();
		await expect(page.locator('dl.kpi-strip .kpi-date', { hasText: '1er septembre 2025' }).first()).toBeVisible();
		const debordements = await page.locator('dl.kpi-strip .kpi-meta').evaluateAll((metas) =>
			metas.filter((meta) => meta.scrollWidth > meta.clientWidth + 1).map((meta) => meta.textContent)
		);
		expect(debordements).toEqual([]);
		await source.assert();
	});
	// 04.7-15 recette, fixed in 04.7-18: at 320 px a card state badge wraps between its words, never
	// inside one, and neither the badge nor the Source button leaves the card, for every label of the
	// glossary (the longest words, « Indisponible », are wider than « insuffisant »).
});

test.describe("Fiche Commune — captures de l'essentiel (04.5-03)", () => {
	test.beforeEach(async ({ page }) => {
		await mockAuth(page);
	});

	const COMMUNES_ESSENTIEL = [
		['85191', { statut: 'ok', donnees: [COMMUNE_85191_OBSERVED], meta: { source: COMMUNE_85191_SOURCES } }],
		['85047', MOCK_COMMUNE_85047]
	] as const;

	for (const [code, reponse] of COMMUNES_ESSENTIEL) {
		test(`essentiel de /commune/${code} à 1440 et 375`, async ({ page }, testInfo) => {
			if (process.env.DC_REFONTE_D_EVIDENCE_DIR) await mkdir(process.env.DC_REFONTE_D_EVIDENCE_DIR, { recursive: true });
			for (const [width, height] of [[1440, 900], [375, 812]] as const) {
				const source = await mockCommune(page, code, 200, reponse);
				await page.setViewportSize({ width, height });
				await hydrate(page, `/commune/${code}`);
				await expect(page.locator('[data-kpi]')).toHaveCount(3);
				await page.locator('dl.kpi-strip').screenshot({ path: refonteEvidencePath(testInfo, `essentiel-commune-${code}-${width}.png`), animations: 'disabled' });
				await source.assert();
			}
		});
	}
});

// Phase 4.9, lot 3 (A9-11 to A9-16 of the summary): three levels on the commune fiche.
test.describe('Fiche Commune — trois niveaux (4.9, lot 3)', () => {
	const LARGEURS = [[1440, 900], [375, 812], [320, 700]] as const;

	for (const [largeur, hauteur] of LARGEURS) {
		test(`${largeur} px : L’essentiel sans état, une explication par ligne, un champ un libellé, axe volet ouvert`, async ({ page }, testInfo) => {
			await page.setViewportSize({ width: largeur, height: hauteur });
			const source = await mockCommune(page, '85047', 200, MOCK_COMMUNE_85047);
			await hydrate(page, '/commune/85047');
			// The words on screen are those of the rows: under 1081 px their categories are folds to open first.
			await ouvrirCategories(page);

			const essentiel = page.locator('#essentiel');
			await expect(essentiel.locator('.state-badge, .state-absence')).toHaveCount(0);
			for (const libelle of new Set(Object.values(LIBELLES_ETATS))) await expect(essentiel.getByText(libelle, { exact: true })).toHaveCount(0);

			// At most one explanation control per row, and the word « Source » is not one of them.
			for (const ligne of await page.locator('.tab-l, .kpi').all()) {
				expect(await ligne.locator('button.source-button').count()).toBeLessThanOrEqual(1);
				await expect(ligne.locator('.tab-note-source, .cle-note')).toHaveCount(0);
			}
			// The word « Source » as the plan counts it (reference: 40 on Nantes): at most one per row.
			for (const ligne of await page.locator('.tab-l, .kpi').all()) expect((await ligne.innerText()).match(/\bSource\b/g)?.length ?? 0).toBeLessThanOrEqual(1);
			const motsSource = (await page.locator('main').innerText()).match(/\bSource\b/g)?.length ?? 0;
			console.log(`RELEVE mot Source ${largeur}px=${motsSource}`);

			// Plain words (lot 4): no fiscal acronym read on the screen.
			expect(await page.locator('main').innerText()).not.toMatch(/\b(?:TFPB|TEOM|EPCI)\b|Taux TH|Gini \(revenu/);

			// One field, one label: a tile line says the label of the theme row of its field.
			const tuiles = await page.locator('[data-kpi] [data-field-id]').evaluateAll((lignes) =>
				lignes.map((ligne) => [ligne.getAttribute('data-field-id')!, ligne.getAttribute('data-libelle')!])
			);
			expect(tuiles.length).toBeGreaterThan(0);
			for (const [champ, libelle] of tuiles) {
				const dt = await page.locator(`.tab-l[data-field-id="${champ}"] > dt`).first().textContent();
				expect(sansDeveloppement(dt!.trim()), champ).toBe(libelle);
			}

			if (process.env.DC_COMMUNE_EVIDENCE_DIR && largeur !== 320) await page.screenshot({ path: communeEvidencePath(testInfo, `page-85047-${largeur}.png`), fullPage: true, animations: 'disabled' });

			// Panel open, from the keyboard: the focus enters it and comes back; no violation of any impact.
			const bouton = page.locator('[data-kpi="prix-maisons"] .source-button');
			await expect(bouton.locator('svg')).toBeVisible();
			await bouton.focus();
			await page.keyboard.press('Enter');
			const volet = page.locator('dialog.source-panel[open]');
			await expect(volet).toBeVisible();
			// Its 150 ms animation: axe measures the final contrast.
			await volet.evaluate((noeud) => Promise.all(noeud.getAnimations({ subtree: true }).map((animation) => animation.finished)));
			const ouvert = await new AxeBuilder({ page }).analyze();
			expect(ouvert.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual([]);
			await page.keyboard.press('Escape');
			await expect(bouton).toBeFocused();
			const ferme = await new AxeBuilder({ page }).analyze();
			expect(ferme.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual([]);
			await noHorizontalOverflow(page);
			await source.assert();
		});
	}

	test('tendance sans bloc daté : état, motif et explication sur sa ligne de prix, aucune tuile', async ({ page }) => {
		const source = await mockCommune(page, '85191', 200, { statut: 'ok', donnees: [COMMUNE_85191_OBSERVED], meta: { source: COMMUNE_85191_SOURCES } });
		await hydrate(page, '/commune/85191');
		await expect(page.locator('[data-kpi="tendance"]')).toHaveCount(0);
		const ligne = page.locator('#critere-prix_commune [data-ligne="dvf-tendance"]');
		await expect(ligne.locator(':scope > dt')).toHaveText('Tendance du prix médian');
		await expect(ligne.locator('.tab-v .state-badge')).toHaveText('Non publiée');
		await ligne.getByRole('button', { name: 'Détail : Tendance du prix médian' }).click();
		const volet = page.getByRole('dialog', { name: 'Tendance du prix médian' });
		await expect(volet.locator('.source-panel-entry', { has: page.locator('dt', { hasText: /^Définition$/ }) })).toHaveCount(1);
		await expect(volet.locator('.source-panel-entry', { has: page.locator('dt', { hasText: /^État et motif$/ }) })).toContainText("Ce site ne publie pas l'évolution des prix pour cette commune.");
		await page.keyboard.press('Escape');
		await source.assert();
	});

	test('impression : définitions et sources reprises, phrase descendue au volet comprise', async ({ page }) => {
		const source = await mockCommune(page, '85191', 200, { statut: 'ok', donnees: [COMMUNE_85191_OBSERVED], meta: { source: COMMUNE_85191_SOURCES } });
		await hydrate(page, '/commune/85191');
		const bloc = page.locator('#definitions-impression');
		await expect(bloc).toBeHidden();
		await page.emulateMedia({ media: 'print' });
		await expect(bloc).toBeVisible();
		await expect(bloc.getByRole('heading', { name: 'Définitions' })).toBeVisible();
		await expect(bloc).toContainText('Prix au m² qui partage en deux moitiés');
		await expect(bloc).toContainText("Ce site ne publie pas l'évolution des prix pour cette commune.");
		await expect(bloc).toContainText(/Source\s: /);
		await expect(page.locator('.source-button').first()).toBeHidden();
		await page.emulateMedia({ media: 'screen' });
		await source.assert();
	});

	test.describe('sans JavaScript', () => {
		test.use({ javaScriptEnabled: false });

		test('les définitions et les sources restent lisibles', async ({ page }) => {
			await configureSsrSource(page.request, [{
				method: 'GET', pathname: '/v1/commune', query: { code_insee: '85191' }, minCalls: 1, maxCalls: 2,
				responses: Array.from({ length: 2 }, () => ({ status: 200, body: { statut: 'ok', donnees: [COMMUNE_85191_OBSERVED], meta: { source: COMMUNE_85191_SOURCES } } }))
			}]);
			await page.goto('/commune/85191');
			const bloc = page.locator('.fiche-sans-js');
			await expect(bloc).toBeVisible();
			await expect(bloc).toContainText('Prix au m² qui partage en deux moitiés');
			await expect(bloc).toContainText("Ce site ne publie pas l'évolution des prix pour cette commune.");
		});
	});
});

// Theme panels (plan 04.5-02, D45-07, UI-024; 04.8-04, UI-SPEC v4 § 6.2; 4.9: the categories that hold them). Since 04.8-04 each theme is
// one open panel at every width, rendered by the server whatever the URL says: the reading by theme
// (`?theme=`, `?vue=tout`, the links of `nav.lecture-acces`, folded panels) is removed by decision. What
// stays proven: the five panels in their order with or without JavaScript, a query never reflected in
// the HTML, an old anchor that reaches its panel, and moving inside the fiche that reads nothing more
// from the API (the SSR source journal and the requests of the browser).
const MOCK_COMMUNE_85191 = { statut: 'ok', donnees: [COMMUNE_85191_OBSERVED], meta: { source: COMMUNE_85191_SOURCES } };
/** The ids of the theme panels rendered visible, in DOM order. */
async function panneauxVisibles(page: Page): Promise<string[]> {
	return page.locator(CATEGORY_PANELS).evaluateAll((nodes) =>
		nodes.filter((node) => (node as HTMLElement).offsetHeight > 0).map((node) => node.id)
	);
}

test.describe('Fiche Commune — thèmes sans JavaScript (D45-07)', () => {
	test.use({ javaScriptEnabled: false });

	test('rendu serveur : trois catégories ouvertes quel que soit ?theme= ou ?vue=, aucune requête reflétée dans le HTML', async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		let source = await mockCommune(page, '85191', 200, MOCK_COMMUNE_85191);
		await page.goto('/commune/85191?theme=risques-urbanisme');
		expect(await panneauxVisibles(page)).toEqual(CATEGORIES);
		await expect(page.locator('#critere-zonage_abc dl.tab')).toBeVisible();
		await expect(page.locator('#critere-prix_commune dl.tab')).toBeVisible();
		await expect(page.locator('details.theme-section, nav.lecture-acces')).toHaveCount(0);
		await source.assert();

		// The frozen answer itself carries the word « inconnu » (a source state, serialized with the
		// page data): the HTML of `?theme=inconnu` holds exactly as many as the HTML without theme,
		// and a value of its own is searched in the HTML as well.
		const mots = (html: string) => html.match(/(?<!\p{L})inconnu(?!\p{L})/gu)?.length ?? 0;
		source = await mockCommune(page, '85191', 200, MOCK_COMMUNE_85191);
		await page.goto('/commune/85191');
		const reference = mots(await page.content());
		expect(await panneauxVisibles(page)).toEqual(CATEGORIES);
		await source.assert();
		source = await mockCommune(page, '85191', 200, MOCK_COMMUNE_85191);
		const reponse = await page.goto('/commune/85191?theme=inconnu');
		expect(reponse?.status()).toBe(200);
		expect(await panneauxVisibles(page)).toEqual(CATEGORIES);
		const html = await page.content();
		expect(mots(html)).toBe(reference);
		expect(html).not.toContain('theme=inconnu');
		await source.assert();
		source = await mockCommune(page, '85191', 200, MOCK_COMMUNE_85191);
		await page.goto('/commune/85191?theme=zq7inconnu%3Cb%3E&vue=zq7vue');
		expect(await panneauxVisibles(page)).toEqual(CATEGORIES);
		expect(await page.content()).not.toContain('zq7');
		await source.assert();

		source = await mockCommune(page, '85191', 200, MOCK_COMMUNE_85191);
		await page.goto('/commune/85191?theme=energie-bati&vue=tout');
		expect(await panneauxVisibles(page)).toEqual(CATEGORIES);
		for (const id of CATEGORIES) await expect(page.locator(`details.cat#${id} dl.tab`).first()).toBeVisible();
		await expect(page.getByRole('link', { name: /Afficher tout|Revenir à la lecture par thème/ })).toHaveCount(0);
		await source.assert();
	});
});

test.describe('Fiche Commune — navigation des thèmes (D45-07, UI-024)', () => {
	test.beforeEach(async ({ page }) => {
		await mockAuth(page);
	});

	test('ancres de thème : Retour, rechargement, clavier ; aucune requête de plus à l’API', async ({ page }) => {
		const lectures: string[] = [];
		page.on('request', (request) => {
			if (new URL(request.url()).pathname.startsWith('/v1/')) lectures.push(request.url());
		});
		// One read of the commune for the first load, one for the reload: nothing else.
		const source = await configureSsrSource(page.request, [communeRead('85191', [
			{ status: 200, body: MOCK_COMMUNE_85191 },
			{ status: 200, body: MOCK_COMMUNE_85191 }
		])]);
		await page.setViewportSize({ width: 1440, height: 900 });
		await hydrate(page, '/commune/85191');
		expect(await panneauxVisibles(page)).toEqual(CATEGORIES);
		const avant = (await source.assert()).requests.length;
		expect(avant).toBe(1);

		// An anchor of a theme reaches its panel without a read.
		await page.goto('/commune/85191#risques-urbanisme');
		await expect(page).toHaveURL(/\/commune\/85191#risques-urbanisme$/);
		await expect(page.locator('#risques-urbanisme')).toHaveCount(1);
		await expect(page.locator('#critere-zonage_abc')).toBeInViewport();
		// Keyboard: a caveat link of the essentials is reached with Tab and followed with Enter.
		const liens = page.locator('section.panneau', { has: page.getByRole('heading', { level: 2, name: 'Ce qui mérite une lecture attentive' }) }).getByRole('link');
		if (await liens.count()) {
			const premier = liens.first();
			const cible = (await premier.getAttribute('href'))!;
			await premier.focus();
			await expect(premier).toBeFocused();
			await page.keyboard.press('Enter');
			await expect(page).toHaveURL(new RegExp(`${cible}$`));
			await expect(page.locator(cible)).toBeInViewport();
		}
		const urlAvant = page.url();
		await page.goto('/commune/85191#contexte-communal');
		await expect(page.locator('#critere-population')).toBeInViewport();

		// Moving inside the fiche: the SSR source journal and the browser read nothing more.
		expect((await source.assert()).requests.length).toBe(avant);
		expect(lectures).toEqual([]);

		// Back returns to the previous anchor, without a read either.
		await page.goBack();
		await expect(page).toHaveURL(urlAvant);
		expect(await panneauxVisibles(page)).toEqual(CATEGORIES);
		expect((await source.assert()).requests.length).toBe(avant);
		expect(lectures).toEqual([]);

		// A reload keeps every panel (one more server read: the page itself).
		const recharge = page.waitForResponse((response) => response.url().endsWith('/auth/me'));
		await page.reload();
		await recharge;
		expect(await panneauxVisibles(page)).toEqual(CATEGORIES);
		expect((await source.assert()).requests.length).toBe(avant + 1);
	});

	test('#immobilier et #contexte-communal atteignent toujours leur panneau', async ({ page }) => {
		const source = await configureSsrSource(page.request, [communeRead('85191', [
			{ status: 200, body: MOCK_COMMUNE_85191 },
			{ status: 200, body: MOCK_COMMUNE_85191 }
		])]);
		await page.setViewportSize({ width: 1440, height: 900 });
		await hydrate(page, '/commune/85191?theme=energie-bati#immobilier');
		await expect(page.locator('#critere-prix_commune')).toBeVisible();
		await expect(page.locator('#critere-prix_commune')).toBeInViewport();
		await hydrate(page, '/commune/85191#contexte-communal');
		await expect(page.locator('#critere-population')).toBeVisible();
		await expect(page.locator('#critere-population dl.tab')).toBeVisible();
		await expect(page.locator('#critere-population')).toBeInViewport();
		await source.assert();
	});

	for (const [width, height] of [[1440, 900], [375, 812]] as const) {
		test(`${width} px : un seul panneau par thème, titre écrit une fois, panneaux dans l’ordre de lecture`, async ({ page }) => {
			const source = await mockCommune(page, '85191', 200, MOCK_COMMUNE_85191);
			await page.setViewportSize({ width, height });
			await hydrate(page, '/commune/85191?theme=risques-urbanisme');
			// Each former theme anchor lands once, in its group; each category has its title written once.
			for (const id of ANCRES_THEMES) await expect(page.locator(`#${id}`)).toHaveCount(1);
			for (const id of CATEGORIES) {
				await expect(page.locator(`details.cat#${id}`)).toHaveCount(1);
				await expect(page.locator(`details.cat#${id} > summary > .cat-titre`)).toHaveCount(1);
			}
			// The access links and the theme read alone are removed (UI-SPEC v4 § 6.2): every panel is
			// visible at every width, one under the other in the mock-up order.
			await expect(page.locator('nav.lecture-acces')).toHaveCount(0);
			expect(await panneauxVisibles(page)).toEqual(CATEGORIES);
			let bas = -Infinity;
			for (const id of CATEGORIES) {
				const boite = (await page.locator(`details.cat#${id}`).boundingBox())!;
				expect(boite.y, `${id} sous le panneau précédent à ${width} px`).toBeGreaterThanOrEqual(bas);
				bas = boite.y + boite.height;
			}
			await noHorizontalOverflow(page);
			const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
			expect(axe.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual([]);
			await source.assert();
		});
	}
});
