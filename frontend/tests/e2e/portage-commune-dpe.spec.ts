// Port of the commune fiche and of the DPE document to the direction A (plan 04.8-04, UI-SPEC v4 §§ 3.2,
// 3.13, 6.2, 6.7, 8). Headless runner, isolated context, every SSR read served by the local fail-closed
// source (AGENTS.md).
//
// What this spec locks:
//   - the exports and structured data of the commune do not move with its form: the CSV of the export
//     dialog, the open JSON (`/commune/{code}.json`, `lu_le` checked apart: present, an ISO date) and the
//     text of every JSON-LD script equal a reference taken on the page before the port
//     (`fixtures/commune-reference.json`), the test origin being replaced by `{ORIGINE}` on both sides
//     (D-35). `DC_MAJ_REFERENCE_COMMUNE=1` rewrites the reference instead of comparing: only on the state
//     before a port, never to turn a failure green;
//   - the form of the mock-up: « L'essentiel » in tiles, the caveats as anchor links, one
//     category of the address fiche (4.9) in `dl.tab`, the questions in `details.det`; every value keeps
//     its `data-field-id`;
//   - at 1440, 375 and 320 px: axe (WCAG 2.2 AA) without violation, details closed then open; no overflow,
//     not even under the text spacing of WCAG 1.4.12; the 14 px floor, the locked weights, the 60ch
//     measure; no error of the page or of its console; a 200 % zoom lays out as a phone; the A4 print
//     opens every detail and drops the navigation.
// Captures at 1440 and 375 px are kept outside the repository (`DC_PORTAGE_EVIDENCE_DIR` when set).
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import AxeBuilder from '@axe-core/playwright';
import { test, expect, type Page, type TestInfo } from '@playwright/test';
import { configureSsrSource, type SourceExpectation } from './fixtures/bien-ssr-source';
import { COMMUNE_FLAT_85047_WITH_DVF_WINDOW, COMMUNE_INDICATORS_SYNTHETIC } from './fixtures/commune-indicators';
import { DOCUMENT_REEL, REEL } from './fixtures/dpe-documents';
import { E2E_APP_ORIGIN } from './fixtures/ports';
import { verifierGraisses, verifierMesure, verifierPlancherTypo } from './fixtures/quality';

const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] as const;
const LARGEURS = [1440, 375, 320] as const;
const LECTURES = 12;
const ORIGINE = '{ORIGINE}';
const REFERENCE_URL = new URL('./fixtures/commune-reference.json', import.meta.url);
const MAJ_REFERENCE = process.env.DC_MAJ_REFERENCE_COMMUNE === '1';

const MOCK_USER = { id: 2, email: 'test@datacommune.fr', name: 'Test User', is_verified: true, is_admin: false, created_at: '2026-01-01T00:00:00Z' };

// Challans (synthetic: dated DVF / DPE blocks) and the 12th arrondissement of Paris, served with the same
// synthetic figures under its own code: the page never adds a figure of Paris to an arrondissement.
const SYNTHESES: Record<string, unknown> = {
	'85047': {
		statut: 'ok',
		donnees: [{ ...COMMUNE_FLAT_85047_WITH_DVF_WINDOW, ...COMMUNE_INDICATORS_SYNTHETIC }],
		meta: { source: [{ nom: 'Démographie' }, { nom: 'Loyers' }, { nom: 'Zonage ABC' }] }
	},
	'75112': {
		statut: 'ok',
		donnees: [
			{
				...COMMUNE_FLAT_85047_WITH_DVF_WINDOW,
				...COMMUNE_INDICATORS_SYNTHETIC,
				code_insee: '75112',
				nom_commune: 'Paris 12e Arrondissement',
				code_departement: '75',
				code_region: '11',
				type_zone: 'arrondissement',
				commune_parent: '75056'
			}
		],
		meta: { source: [{ nom: 'Démographie' }, { nom: 'Loyers' }, { nom: 'Zonage ABC' }] }
	}
};
const CODES = Object.keys(SYNTHESES);
// Since 4.9 the rows of the five themes are read in the categories of the address fiche.
const CATEGORIES = ['q-prix', 'q-logement', 'q-quotidien'];
const TITRES_CATEGORIES = ['Prix et ventes', 'Logement et DPE', 'Vivre dans la commune'];
const KPI_ORDRE = ['prix-maisons', 'tendance', 'dpe-dominant'];

interface ReferenceCommune {
	csv: string;
	json: unknown;
	jsonLd: string[];
}

function lecture(code: string, body: unknown = SYNTHESES[code]): SourceExpectation {
	return {
		method: 'GET',
		pathname: '/v1/commune',
		query: { code_insee: code },
		minCalls: 0,
		maxCalls: LECTURES,
		responses: Array.from({ length: LECTURES }, () => ({ status: 200, body }))
	};
}

async function servirCommune(page: Page, code: string) {
	return configureSsrSource(page.request, [lecture(code)]);
}

async function ouvrirCommune(page: Page, code: string, largeur: number, hauteur = 900) {
	await page.setViewportSize({ width: largeur, height: hauteur });
	const hydratee = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
	await page.goto(`/commune/${code}`);
	await hydratee;
	await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
}

const canonique = (texte: string) => texte.split(E2E_APP_ORIGIN).join(ORIGINE);

function collecterErreurs(page: Page): string[] {
	const erreurs: string[] = [];
	page.on('pageerror', (err) => erreurs.push(`pageerror: ${err.message}`));
	page.on('console', (message) => {
		if (message.type() === 'error' && !message.text().startsWith('Failed to load resource')) erreurs.push(`console: ${message.text()}`);
	});
	return erreurs;
}

function preuve(testInfo: TestInfo, nom: string): string {
	const dossier = process.env.DC_PORTAGE_EVIDENCE_DIR;
	if (!dossier) return testInfo.outputPath(nom);
	mkdirSync(dossier, { recursive: true });
	return join(dossier, nom);
}

async function sansDebordement(page: Page, contexte: string) {
	const { large, vue, fautifs } = await page.evaluate(() => {
		const vue = window.innerWidth;
		const fautifs = [...document.querySelectorAll('body *')]
			.filter((el) => el.getBoundingClientRect().right > vue + 1 && ![...el.children].some((c) => c.getBoundingClientRect().right > vue + 1))
			.slice(0, 5)
			.map((el) => `${el.tagName.toLowerCase()}.${[...el.classList].join('.')} → ${Math.round(el.getBoundingClientRect().right)}`);
		return { large: document.documentElement.scrollWidth, vue, fautifs };
	});
	expect(large, `aucun défilement horizontal (${contexte}) : ${fautifs.join(' ; ')}`).toBeLessThanOrEqual(vue);
}

async function sansViolationAxe(page: Page, contexte: string) {
	const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
	expect(axe.passes.length, 'axe n’a rien inspecté').toBeGreaterThan(0);
	expect(axe.violations, `${contexte} : ${JSON.stringify(axe.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))}`).toEqual([]);
}

const deplier = (page: Page, portee: string, ouvert: boolean) =>
	page.evaluate(([p, o]) => document.querySelectorAll(`${p} details`).forEach((d) => ((d as HTMLDetailsElement).open = o as boolean)), [portee, ouvert] as const);

/** WCAG 1.4.12: line height 1.5, letter spacing 0.12em, word spacing 0.16em, paragraph spacing 2em. */
const ESPACEMENT_DU_TEXTE = '*{line-height:1.5 !important;letter-spacing:0.12em !important;word-spacing:0.16em !important}p{margin-bottom:2em !important}';

/** One h1, an outline without a skipped level, every `#…` link with its target. */
async function planDesTitres(page: Page) {
	const niveaux = await page.evaluate(() =>
		[...document.querySelectorAll('main h1, main h2, main h3, main h4, main h5, main h6')]
			.filter((h) => h.checkVisibility({ checkOpacity: false, checkVisibilityCSS: true }))
			.map((h) => ({ niveau: Number(h.tagName.slice(1)), texte: (h.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 40) }))
	);
	expect(niveaux.filter((h) => h.niveau === 1), 'un seul h1').toHaveLength(1);
	expect(niveaux[0].niveau, 'le premier titre est le h1').toBe(1);
	let precedent = 0;
	for (const h of niveaux) {
		expect(h.niveau, `saut de niveau avant « ${h.texte} »`).toBeLessThanOrEqual(precedent + 1);
		precedent = h.niveau;
	}
	const sansCible = await page.evaluate(() =>
		[...document.querySelectorAll<HTMLAnchorElement>('a[href^="#"]')]
			.map((a) => a.getAttribute('href')!)
			.filter((href) => href.length > 1 && !document.getElementById(decodeURIComponent(href.slice(1))))
	);
	expect(sansCible, 'ancres sans cible').toEqual([]);
}

/** Details printed closed: those on paper (the closed source dialogs are not printed). */
const detailsFermesImprimes = (page: Page) =>
	page.evaluate(() =>
		[...document.querySelectorAll('main details:not([open])')]
			.filter((d) => d.checkVisibility({ checkOpacity: false, checkVisibilityCSS: true }))
			.map((d) => (d.querySelector('summary')?.textContent ?? '').trim().slice(0, 40))
	);

async function exportCsv(page: Page): Promise<string> {
	const telechargement = page.waitForEvent('download');
	await page.getByRole('button', { name: 'Télécharger (CSV)' }).click();
	await page.getByRole('dialog').getByRole('button', { name: /^Télécharger \(\d+\)$/ }).click();
	const csv = await readFile((await (await telechargement).path())!, 'utf8');
	await page.keyboard.press('Escape');
	return csv;
}

test.beforeEach(async ({ page }) => {
	await page.route('**/auth/me', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_USER) }));
});

test.describe('Fiche commune — exports et données structurées égaux à la référence (D-35)', () => {
	test('CSV, JSON (lu_le à part) et JSON-LD de 85047 et 75112', async ({ page }) => {
		const releve: Record<string, ReferenceCommune> = {};
		for (const code of CODES) {
			const source = await servirCommune(page, code);
			const reponse = await page.request.get(`/commune/${code}.json`);
			expect(reponse.status()).toBe(200);
			const json = (await reponse.json()) as Record<string, unknown>;
			// The reading date is the time of the export: present and an ISO date, never compared.
			expect(typeof json.lu_le).toBe('string');
			expect(json.lu_le as string).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/);
			expect(Number.isNaN(Date.parse(json.lu_le as string))).toBe(false);
			delete json.lu_le;

			await ouvrirCommune(page, code, 1440);
			const jsonLd = (await page.locator('script[type="application/ld+json"]').allTextContents()).map(canonique);
			expect(jsonLd.length, 'JSON-LD présents').toBeGreaterThanOrEqual(2);
			const csv = await exportCsv(page);
			releve[code] = { csv: canonique(csv), json: JSON.parse(canonique(JSON.stringify(json))), jsonLd };
			await source.assert();
		}
		if (MAJ_REFERENCE) {
			writeFileSync(REFERENCE_URL, `${JSON.stringify(releve, null, '\t')}\n`, 'utf8');
			return;
		}
		const reference = JSON.parse(readFileSync(REFERENCE_URL, 'utf8')) as Record<string, ReferenceCommune>;
		expect(Object.keys(reference).sort()).toEqual([...CODES].sort());
		for (const code of CODES) {
			// Declared change C1 (inventory of 2026-11-10, arbitrated by Ianis on 2026-11-10: « oui aux trois catégories
			// de la fiche commune, export CSV compris », which amends D-35): the CSV groups its rows by the three
			// categories of the page, no longer by the five former themes. The reference is not rewritten: the header
			// stays exact, every row is compared without its first column (indicator, value, state, grain and caveats
			// are those of the reference, none lost, none added), and the first column must name the categories.
			const lignesDe = (csv: string) => csv.split(/\r?\n/).filter(Boolean);
			const [enTete, ...lignes] = lignesDe(releve[code].csv);
			const [enTeteReference, ...lignesReference] = lignesDe(reference[code].csv);
			const sansGroupe = (rangees: string[]) => rangees.map((ligne) => ligne.slice(ligne.indexOf(',') + 1)).sort();
			const groupes = (rangees: string[]) => [...new Set(rangees.map((ligne) => ligne.slice(0, ligne.indexOf(','))))];
			expect(enTete, `CSV de ${code}, en-tête`).toBe(enTeteReference);
			expect(groupes(lignesReference), `CSV de ${code}, thèmes de la référence`).toEqual(['Immobilier', 'Énergie & bâti', 'Services & transports', 'Contexte communal', 'Risques & urbanisme']);
			expect(groupes(lignes), `CSV de ${code}, catégories`).toEqual(['Prix et ventes', 'Logement et DPE', 'Vivre dans la commune']);
			expect(sansGroupe(lignes), `CSV de ${code}, lignes hors première colonne`).toEqual(sansGroupe(lignesReference));
			expect(releve[code].json, `JSON de ${code}`).toEqual(reference[code].json);
			// Declared change C2 (SEO of 2026-11-14, asked by Ianis): `Dataset.description` is the page's distinctive
			// meta description (served prices, elided name), no longer the template of the reference. Every other
			// field of every JSON-LD script stays equal to the reference; the new description is bounded on its own.
			// Same change, second text fix of 2026-11-14: `Dataset.name` contracts « de » with the arrondissement
			// (« Données publiques du 12e arrondissement de Paris »); only the reference name gets that contraction,
			// so a page that went back to « de » still fails and every other name stays exact.
			const sansDescription = (scripts: string[], contracter = false) =>
				scripts.map((texte) => {
					const objet = JSON.parse(texte) as Record<string, unknown>;
					if (objet['@type'] === 'Dataset') {
						delete objet.description;
						if (contracter) objet.name = String(objet.name).replace(/^Données publiques de (\d+(e|er) arrondissement\b)/, 'Données publiques du $1');
					}
					return objet;
				});
			expect(sansDescription(releve[code].jsonLd), `JSON-LD de ${code}`).toEqual(sansDescription(reference[code].jsonLd, true));
			const dataset = releve[code].jsonLd.map((texte) => JSON.parse(texte) as Record<string, unknown>).find((objet) => objet['@type'] === 'Dataset');
			const description = String(dataset?.description ?? '');
			expect(description.length, `JSON-LD de ${code}, description`).toBeGreaterThan(0);
			expect(description.length, `JSON-LD de ${code}, description`).toBeLessThanOrEqual(160);
			expect(description, `JSON-LD de ${code}, description`).not.toMatch(/^Fiche publique de /);
		}
	});
});

test.describe('Fiche commune — forme de la maquette (UI-SPEC § 6.2)', () => {
	test('L’essentiel en tuiles, réserves en ancres, les catégories de la fiche adresse en dl.tab, FAQ en détails', async ({ page }) => {
		const source = await servirCommune(page, '85047');
		await ouvrirCommune(page, '85047', 1440);
		const essentiel = page.locator('section.panneau-essentiel').filter({ has: page.getByRole('heading', { level: 2, name: 'L’essentiel' }) });
		await expect(essentiel).toHaveCount(1);
		await expect(essentiel).toContainText('Commune');
		await expect(essentiel).toContainText('Toutes les valeurs de cette page valent pour la commune entière, jamais pour une adresse.');
		expect(await essentiel.locator('dl.cles > .cle').evaluateAll((els) => els.map((e) => e.getAttribute('data-kpi')))).toEqual(KPI_ORDRE);
		// Every value keeps its field id; the definition and the source are in the panel (4.9, lot 3).
		await expect(essentiel.locator('dl.cles [data-field-id]').first()).toBeAttached();
		await expect(essentiel.locator('.cle-note')).toHaveCount(0);
		await expect(essentiel.locator('[data-kpi="prix-maisons"] .source-button svg')).toBeVisible();
		await expect(essentiel.locator('[data-repartition-dpe]')).toBeVisible();

		expect(await page.locator('details.cat').evaluateAll((ds) => ds.map((d) => d.id))).toEqual(CATEGORIES);
		for (const [index, cle] of CATEGORIES.entries()) {
			const panneau = page.locator(`details.cat#${cle}`);
			await expect(panneau.locator('.cat-titre')).toHaveText(TITRES_CATEGORIES[index]);
			await expect(panneau.locator('dl.tab > .tab-l').first()).toBeVisible();
			// Every row keeps its key; a value read from a field keeps its field id (the unit test compares it to the model).
			expect(await panneau.locator('dl.tab > .tab-l').evaluateAll((ls) => ls.every((l) => l.hasAttribute('data-ligne')))).toBe(true);
			expect(await panneau.locator('dl.tab > .tab-l[data-field-id]').count()).toBeGreaterThan(0);
		}
		await expect(page.locator('details.theme-section')).toHaveCount(0);
		await expect(page.getByRole('link', { name: /Afficher tout/ })).toHaveCount(0);
		// A declared absence is its served state, never a zero.
		const appartement = page.locator('#q-prix .tab-l', { hasText: 'Prix médian appartement' }).first();
		await expect(appartement.locator('.tab-v .state-badge')).toHaveText('Effectif insuffisant');

		const reserves = page.locator('section.panneau').filter({ has: page.getByRole('heading', { name: 'Ce qui mérite une lecture attentive' }) });
		if (await reserves.count()) {
			const fleches = await reserves.locator('ul.liste-liens a .fleche').allTextContents();
			expect(fleches.every((f) => f === '↓')).toBe(true);
		}
		const faq = page.locator('section.panneau').filter({ has: page.getByRole('heading', { level: 2, name: 'Questions fréquentes' }) });
		await expect(faq.locator('details.det').first()).toBeAttached();
		await expect(page.getByText('Chercher une adresse à')).toHaveCount(0);
		await planDesTitres(page);
		await source.assert();
	});

	test('une ancre de thème d’avant le portage atteint son panneau', async ({ page }) => {
		await servirCommune(page, '85047');
		await ouvrirCommune(page, '85047', 1440);
		await page.goto('/commune/85047#energie-bati');
		await expect(page.locator('details.cat#q-logement #critere-dpe')).toBeInViewport();
	});
});

test.describe('Fiche commune — qualité aux trois largeurs (UI-SPEC § 8)', () => {
	for (const largeur of LARGEURS) {
		test(`${largeur} px : axe fermé puis ouvert, débordement, espacement du texte, plancher, graisses, mesure`, async ({ page }, testInfo) => {
			test.setTimeout(120_000);
			const erreurs = collecterErreurs(page);
			await servirCommune(page, '85047');
			await ouvrirCommune(page, '85047', largeur, largeur >= 1024 ? 900 : 800);
			await sansViolationAxe(page, `${largeur} px, détails fermés`);
			await sansDebordement(page, `${largeur} px`);
			if (largeur !== 320) {
				await page.screenshot({ path: preuve(testInfo, `commune-85047-${largeur}-ecran.jpg`), quality: 70, animations: 'disabled' });
				await page.screenshot({ path: preuve(testInfo, `commune-85047-${largeur}-page.jpg`), quality: 70, fullPage: true, animations: 'disabled' });
			}
			await deplier(page, 'main', true);
			await sansViolationAxe(page, `${largeur} px, détails ouverts`);
			await sansDebordement(page, `${largeur} px, tout déplié`);
			expect.soft(await verifierPlancherTypo(page, 'main'), `plancher de 14 px à ${largeur} px`).toEqual([]);
			expect.soft(await verifierGraisses(page, 'main'), `graisses à ${largeur} px`).toEqual([]);
			expect.soft(await verifierMesure(page, 'main', ['p']), `mesure à ${largeur} px`).toEqual([]);
			await page.addStyleTag({ content: ESPACEMENT_DU_TEXTE });
			await sansDebordement(page, `${largeur} px, espacement du texte WCAG 1.4.12`);
			await deplier(page, 'main', false);
			await sansDebordement(page, `${largeur} px, espacement du texte, fermé`);
			expect(erreurs).toEqual([]);
		});
	}

	test('arrondissement PLM à 320 px : aucun débordement, titre de l’arrondissement', async ({ page }) => {
		await servirCommune(page, '75112');
		await ouvrirCommune(page, '75112', 320, 800);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('12e arrondissement de Paris');
		await sansDebordement(page, 'arrondissement, 320 px');
	});

	test('zoom 200 % (720 × 450) : tuiles sur deux colonnes, aucun débordement', async ({ page }, testInfo) => {
		const erreurs = collecterErreurs(page);
		await servirCommune(page, '85047');
		await ouvrirCommune(page, '85047', 720, 450);
		await sansDebordement(page, 'zoom 200 %');
		const colonnes = await page.locator('section.panneau-essentiel dl.cles').evaluate((dl) => getComputedStyle(dl).gridTemplateColumns.split(' ').length);
		expect(colonnes, 'deux colonnes de tuiles sous 1080 px').toBe(2);
		await sansViolationAxe(page, 'zoom 200 %');
		await page.screenshot({ path: preuve(testInfo, 'commune-85047-zoom200.jpg'), quality: 70, animations: 'disabled' });
		expect(erreurs).toEqual([]);
	});

	test('1440 px : autant de colonnes que de tuiles, jusqu’à quatre, sans colonne vide', async ({ page }) => {
		await servirCommune(page, '85047');
		await ouvrirCommune(page, '85047', 1440);
		const { colonnes, tuiles } = await page.locator('section.panneau-essentiel dl.cles').evaluate((dl) => ({
			colonnes: getComputedStyle(dl).gridTemplateColumns.split(' ').length,
			tuiles: dl.children.length
		}));
		expect(tuiles, 'la référence sert au moins deux tuiles').toBeGreaterThan(1);
		expect(colonnes, `${tuiles} tuiles sur 1440 px`).toBe(Math.min(tuiles, 4));
	});

	test('impression A4 : détails ouverts, navigation et actions absentes', async ({ page }, testInfo) => {
		await servirCommune(page, '85047');
		await ouvrirCommune(page, '85047', 1024);
		await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
		await page.emulateMedia({ media: 'print' });
		expect(await detailsFermesImprimes(page), 'aucun détail fermé à l’impression').toEqual([]);
		const navigationsVisibles = await page.evaluate(() =>
			[...document.querySelectorAll('nav')].filter((n) => n.checkVisibility({ checkOpacity: false, checkVisibilityCSS: true })).map((n) => n.getAttribute('aria-label') ?? n.className)
		);
		expect(navigationsVisibles.filter((n) => n !== "Fil d'Ariane"), 'navigation imprimée').toEqual([]);
		await expect(page.getByRole('button', { name: 'Télécharger (CSV)' })).toBeHidden();
		await expect(page.locator('#q-prix dl.tab').first()).toBeVisible();
		await page.pdf({ path: preuve(testInfo, 'commune-85047-impression.pdf'), format: 'A4', printBackground: true });
		await page.emulateMedia({ media: 'screen' });
		await page.evaluate(() => window.dispatchEvent(new Event('afterprint')));
	});
});

// ---- DPE document (UI-SPEC v4 § 6.7) and its error page (§ 6.10) ----------------------------------------

async function servirDocument(page: Page, numero: string, reponse: { status: number; body: unknown }) {
	return configureSsrSource(page.request, [
		{ method: 'GET', pathname: `/v1/dpe/${numero}`, query: {}, minCalls: 0, maxCalls: LECTURES, responses: Array.from({ length: LECTURES }, () => reponse) }
	]);
}

async function ouvrirDocument(page: Page, numero: string, largeur: number, hauteur = 900) {
	await page.setViewportSize({ width: largeur, height: hauteur });
	const hydratee = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
	const reponse = await page.goto(`/dpe/${numero}`);
	await hydratee;
	await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
	return reponse;
}

const DOCUMENT_OK = { status: 200, body: DOCUMENT_REEL };
const DPE_INCONNU = { status: 404, body: { title: 'DPE inconnu', status: 404, code: 'dpe_inconnu', couverture: { departements: ['44', '85'] } } };

test.describe('Document DPE — forme de la maquette (UI-SPEC § 6.7)', () => {
	test('titre, « N° … », Classes déclarées (deux échelles), tuiles, note, dl.tab et panneaux', async ({ page }) => {
		const source = await servirDocument(page, REEL, DOCUMENT_OK);
		await ouvrirDocument(page, REEL, 1440);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('Diagnostic de performance énergétique d’un immeuble');
		await expect(page.locator('.document-sous .document-numero')).toHaveText(`N° ${REEL}`);
		const classes = page.locator('section.panneau-essentiel').filter({ has: page.getByRole('heading', { level: 2, name: 'Classes déclarées' }) });
		await expect(classes.locator('.dpe-l')).toHaveCount(2);
		await expect(classes.locator('.dpe-l ol.echelle-dpe')).toHaveCount(2);
		for (const ligne of await classes.locator('.dpe-l').all()) {
			await expect(ligne.locator('li[aria-current="true"]')).toHaveCount(1);
		}
		await expect(classes.locator('dl.cles > .cle')).toHaveCount(4);
		// A building DPE (Opus 04-1, P1): the note names the building, never a dwelling.
		await expect(classes.locator('.dc-note')).toHaveText('Ce document décrit un immeuble à une date, tel que l’a saisi le diagnostiqueur.');
		const declare = page.locator('section.panneau').filter({ has: page.getByRole('heading', { level: 2, name: 'Ce que déclare le document' }) });
		await expect(declare.locator('dl.tab > .tab-l[data-field-id]').first()).toBeVisible();
		// P2 (2026-11-10): « Provenance et validité » is a fold of the socle, its h2 in the summary.
		for (const titre of ['Provenance et validité']) {
			await expect(page.locator('details.depliant-cadre').filter({ has: page.getByRole('heading', { level: 2, name: titre }) })).toHaveCount(1);
		}
		// Each value keeps its source action, which opens its panel in one action (UI-01).
		const premiere = declare.locator('dl.tab > .tab-l').first();
		await premiere.getByRole('button', { name: /^Source et définition : / }).click();
		await expect(premiere.locator('dialog.source-panel')).toBeVisible();
		await page.keyboard.press('Escape');
		await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', 'noindex, follow');
		await planDesTitres(page);
		await source.assert();
	});

	test('« Classes déclarées » en une colonne sous 720 px, cases de 38 px', async ({ page }) => {
		await servirDocument(page, REEL, DOCUMENT_OK);
		await ouvrirDocument(page, REEL, 375, 800);
		const ligne = page.locator('.dpe-l').first();
		expect(await ligne.evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(' ').length)).toBe(1);
		await ouvrirDocument(page, REEL, 1440);
		expect(await page.locator('.dpe-l').first().evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(' ').length)).toBe(3);
		const largeurCase = await page.locator('.dpe-l ol.echelle-dpe li').first().evaluate((li) => li.getBoundingClientRect().width);
		expect(largeurCase).toBeGreaterThanOrEqual(38);
	});
});

test.describe('Document DPE — qualité aux trois largeurs (UI-SPEC § 8)', () => {
	for (const largeur of LARGEURS) {
		test(`${largeur} px : document et page d’erreur, axe, débordement, espacement du texte, plancher, graisses, mesure`, async ({ page }, testInfo) => {
			test.setTimeout(120_000);
			const erreurs = collecterErreurs(page);
			await servirDocument(page, REEL, DOCUMENT_OK);
			await ouvrirDocument(page, REEL, largeur, largeur >= 1024 ? 900 : 800);
			await sansViolationAxe(page, `document, ${largeur} px`);
			await sansDebordement(page, `document, ${largeur} px`);
			if (largeur !== 320) {
				await page.screenshot({ path: preuve(testInfo, `dpe-${largeur}-ecran.jpg`), quality: 70, animations: 'disabled' });
				await page.screenshot({ path: preuve(testInfo, `dpe-${largeur}-page.jpg`), quality: 70, fullPage: true, animations: 'disabled' });
			}
			expect.soft(await verifierPlancherTypo(page, 'main'), `plancher de 14 px à ${largeur} px`).toEqual([]);
			expect.soft(await verifierGraisses(page, 'main'), `graisses à ${largeur} px`).toEqual([]);
			expect.soft(await verifierMesure(page, 'main', ['p']), `mesure à ${largeur} px`).toEqual([]);
			await page.addStyleTag({ content: ESPACEMENT_DU_TEXTE });
			await sansDebordement(page, `document, ${largeur} px, espacement du texte WCAG 1.4.12`);

			await servirDocument(page, REEL, DPE_INCONNU);
			const reponse = await ouvrirDocument(page, REEL, largeur, largeur >= 1024 ? 900 : 800);
			expect(reponse?.status()).toBe(404);
			await expect(page.getByRole('heading', { level: 1 })).toHaveText(`DPE n° ${REEL}`);
			await expect(page.locator('main .notice')).toHaveCount(1);
			await expect(page.getByRole('link', { name: 'Revenir à la recherche' })).toBeVisible();
			await sansViolationAxe(page, `erreur, ${largeur} px`);
			await sansDebordement(page, `erreur, ${largeur} px`);
			expect.soft(await verifierPlancherTypo(page, 'main'), `erreur : plancher à ${largeur} px`).toEqual([]);
			expect.soft(await verifierGraisses(page, 'main'), `erreur : graisses à ${largeur} px`).toEqual([]);
			if (largeur !== 320) await page.screenshot({ path: preuve(testInfo, `dpe-erreur-${largeur}-ecran.jpg`), quality: 70, animations: 'disabled' });
			expect(erreurs.filter((e) => !e.includes('404'))).toEqual([]);
		});
	}

	test('zoom 200 % (720 × 450) : une colonne pour les classes, aucun débordement', async ({ page }, testInfo) => {
		await servirDocument(page, REEL, DOCUMENT_OK);
		await ouvrirDocument(page, REEL, 720, 450);
		await sansDebordement(page, 'document, zoom 200 %');
		expect(await page.locator('.dpe-l').first().evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(' ').length)).toBe(1);
		await sansViolationAxe(page, 'document, zoom 200 %');
		await page.screenshot({ path: preuve(testInfo, 'dpe-zoom200.jpg'), quality: 70, animations: 'disabled' });
	});

	test('impression A4 du document : détails ouverts, navigation et actions absentes', async ({ page }, testInfo) => {
		await servirDocument(page, REEL, DOCUMENT_OK);
		await ouvrirDocument(page, REEL, 1024);
		await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
		await page.emulateMedia({ media: 'print' });
		expect(await detailsFermesImprimes(page), 'aucun détail fermé à l’impression').toEqual([]);
		const navigationsVisibles = await page.evaluate(() =>
			[...document.querySelectorAll('nav')].filter((n) => n.checkVisibility({ checkOpacity: false, checkVisibilityCSS: true })).map((n) => n.getAttribute('aria-label') ?? n.className)
		);
		expect(navigationsVisibles.filter((n) => n !== "Fil d'Ariane"), 'navigation imprimée').toEqual([]);
		await expect(page.getByRole('link', { name: 'Comparer avec un autre DPE' })).toBeHidden();
		await expect(page.locator('.dpe-l').first()).toBeVisible();
		await page.pdf({ path: preuve(testInfo, 'dpe-impression.pdf'), format: 'A4', printBackground: true });
		await page.emulateMedia({ media: 'screen' });
		await page.evaluate(() => window.dispatchEvent(new Event('afterprint')));
	});
});
