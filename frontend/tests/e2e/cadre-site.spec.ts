import AxeBuilder from '@axe-core/playwright';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, expect, type Locator, type Page, type TestInfo } from '@playwright/test';
import { DEPARTEMENT_44, DEPARTEMENTS_LISTE } from '../../src/lib/data/__fixtures__/departement';
import { COMMUNE_META_SOURCE_NOMS, COMMUNE_META_SOURCE_ORDER } from '../../src/lib/data/explications';
import { configureSsrSource, type SourceExpectation } from './fixtures/bien-ssr-source';
import { COMMUNE_FLAT_85047 } from './fixtures/commune-indicators';
import { expectNoUnexpectedOffLocalRequest, installLocalNetworkGuard } from './fixtures/local-network';
import { CODE_INSEE, mockAuthAnonyme, mockCommune } from './fixtures/mocks-matrice';
import { verifierGraisses, verifierMesure, verifierPlancherTypo } from './fixtures/quality';
import { ROUTES_PHASE4 } from './fixtures/surfaces-phase4';

// 04.8-02 Task 1 — the site frame in its v4 form (UI-SPEC § 3.11, § 8; D-27, D-34, D-35): header,
// footer and skip link of the public pages, and the header of the connected space, which keeps its
// form.
//
// What this spec locks:
//   - the skip link, on a public page and on /settings: at focus, its padding is read from the
//     `--s2` and `--s4` tokens of :root (8 and 16 px) and its radius from `--radius-ligne`; a sheet
//     that makes `--s2` invalid on :root makes that padding fail (D-27, red case on the real
//     consumer), its colours stay those of today;
//   - the header of /settings, an (app) route: its computed box equals the reference taken before
//     04.8-02 (`fixtures/cadre-reference.json`), at five widths;
//   - one search of the site per page (4.9 lot 7, C8.5): the large field of the content, else the header's;
//   - the public header: paper background, line, 1240 px inside, padding 12 and 24 px, not sticky,
//     the links « Comparer » · « Données & méthode » · « Développeurs », the compact search with its
//     help text, its 200-character limit and a plain button whose accessible name starts with its
//     visible text; the search is shown at every width except on /, /communes and the root error
//     page, alone on its row under 720 px, with no overflow even with 200 characters typed;
//   - under 720 px (D-41): two rows, the brand and « Menu », then the search; at most a fifth of a
//     375 × 812 window; the links and the account are the panel of « Menu », which opens with and
//     without JavaScript and folds with Escape;
//   - at 160 CSS px (a real 200 % zoom of a 320 px window) nothing of the header overlaps;
//   - the public footer: paper background, padding 32 and 24 px, 14 px, the eight links (« Lire une
//     fiche » since 04.8-06) and the
//     sentence of today (D-34);
//   - at 1440, 375 and 320 px: axe without violation on the header and the footer, no overflow, the
//     14 px floor, the locked weights and the 60ch measure;
//   - the JSON-LD BreadcrumbList of /departement/44, a commune fiche and a Paris arrondissement equals
//     the versioned reference (the breadcrumb itself is redrawn by task 3).
//
// Writing a section of the reference: `DC_CADRE_REFERENCE_WRITE=1 DC_CADRE_BASE_REVISION=<sha>`,
// with `src/` equal to <sha>. An existing section is compared, never rewritten.
// Captures go to `DC_CADRE_EVIDENCE_DIR` when set, else to the output folder of the run, never the
// repository.

const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] as const;
const LARGEURS = [1440, 375, 320] as const;
const NOM_RECHERCHE = 'Rechercher une adresse ou une commune';
const LIENS_PUBLICS = [
	{ nom: 'Comparer', href: '/comparer' },
	{ nom: 'Données & méthode', href: '/datasets' },
	{ nom: 'Développeurs', href: '/developpeurs' }
];
const LIENS_PIED = [
	'Données & méthode',
	// 04.8-06 (UI-SPEC § 3.11): the legend, second.
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
const PHRASE_PIED =
	'DataCommune rassemble des données publiques ouvertes. Chaque valeur cite sa source, sa date et l’échelle à laquelle elle vaut.';

// --- reference ----------------------------------------------------------------------------------

const CHEMIN_REFERENCE = fileURLToPath(new URL('./fixtures/cadre-reference.json', import.meta.url));
const RACINE_FRONTEND = fileURLToPath(new URL('../../', import.meta.url));
const ECRITURE = process.env.DC_CADRE_REFERENCE_WRITE === '1';
const REGLE =
	'Relevés du cadre pris avant le portage du plan 04.8-02 : boîte calculée de l’en-tête d’une route (app) et texte des ' +
	'JSON-LD BreadcrumbList. Une section existante ne se réécrit jamais ; un changement voulu se déclare dans cadre-site.spec.ts.';

interface Section {
	baseRevision: string;
	capturedAt: string;
	page: string;
	releve: unknown;
}
interface Reference {
	regle: string;
	baseRevision: string;
	capturedAt: string;
	sections: Record<string, Section>;
}

/**
 * Changes declared on purpose since the reference was taken: one property, its exact value before and after.
 * Applied to the reference as it is read (the file is never rewritten); every other property and every box
 * is still compared as recorded.
 */
const CHANGEMENTS_VOULUS: { propriete: string; avant: string; apres: string }[] = [
	// Audit of 2026-11-10, P-2 (2026-11-13): a fallback face sized on Inter, after it in the stack. Same boxes.
	{ propriete: 'font-family', avant: 'Inter, system-ui, sans-serif', apres: 'Inter, "Inter Repli", system-ui, sans-serif' }
];

function appliquerChangementsVoulus(valeur: unknown): unknown {
	if (Array.isArray(valeur)) return valeur.map(appliquerChangementsVoulus);
	if (!valeur || typeof valeur !== 'object') return valeur;
	return Object.fromEntries(
		Object.entries(valeur).map(([cle, v]) => {
			const changement = CHANGEMENTS_VOULUS.find((c) => c.propriete === cle && c.avant === v);
			return [cle, changement ? changement.apres : appliquerChangementsVoulus(v)];
		})
	);
}

function lireReference(): Reference | null {
	return existsSync(CHEMIN_REFERENCE) ? (JSON.parse(readFileSync(CHEMIN_REFERENCE, 'utf8')) as Reference) : null;
}

/** Compares a reading with its section; in writing mode, records a missing section, never rewrites one. */
function comparerALaReference(cle: string, chemin: string, releve: unknown) {
	const existante = lireReference();
	const section = existante?.sections[cle];
	if (!ECRITURE) {
		expect(section, `section ${cle} de cadre-reference.json`).toBeDefined();
		expect(releve, `${cle} : relevé différent de la référence prise avant 04.8-02`).toEqual(appliquerChangementsVoulus(section!.releve));
		return;
	}
	const base = process.env.DC_CADRE_BASE_REVISION ?? '';
	if (!/^[0-9a-f]{40}$/.test(base)) throw new Error('DC_CADRE_BASE_REVISION must be the full SHA of the recorded state');
	// The rendered code must be the code of the declared revision.
	execFileSync('git', ['diff', '--quiet', base, '--', 'src'], { cwd: RACINE_FRONTEND, stdio: 'inherit' });
	if (section) {
		expect(releve).toEqual(section.releve);
		return;
	}
	const capturedAt = new Date().toISOString();
	const reference: Reference = {
		regle: existante?.regle ?? REGLE,
		baseRevision: existante?.baseRevision ?? base,
		capturedAt: existante?.capturedAt ?? capturedAt,
		sections: { ...(existante?.sections ?? {}), [cle]: { baseRevision: base, capturedAt, page: chemin, releve } }
	};
	writeFileSync(CHEMIN_REFERENCE, `${JSON.stringify(reference, null, '\t')}\n`);
}

// --- pages --------------------------------------------------------------------------------------

type Reponse = { status: number; body: unknown };
const lecture = (pathname: string, query: Record<string, string>, reponse: Reponse): SourceExpectation => ({
	method: 'GET',
	pathname,
	query,
	minCalls: 1,
	maxCalls: 1,
	responses: [reponse]
});

const DATASETS_API = JSON.parse(readFileSync(new URL('../../src/lib/data/__fixtures__/datasets-api.json', import.meta.url), 'utf8'));
const SOURCES_COMMUNE = COMMUNE_META_SOURCE_ORDER.map((dataset) => ({
	nom: COMMUNE_META_SOURCE_NOMS[dataset],
	licence: 'Licence ouverte',
	url: 'https://www.data.gouv.fr/',
	producteur: 'Producteur public'
}));
const SOURCES_DEPARTEMENT = [{ nom: 'DVF', producteur: 'DGFiP / Etalab', licence: 'Licence Ouverte 2.0' }];
/** A Paris arrondissement, served as the API serves a PLM zone (04.7-09): its department is also its commune. */
const ARRONDISSEMENT_75112 = {
	...COMMUNE_FLAT_85047,
	code_insee: '75112',
	nom_commune: 'Paris 12e Arrondissement',
	code_departement: '75',
	code_region: '11',
	type_zone: 'arrondissement',
	commune_parent: '75056'
};

interface Cible {
	id: string;
	chemin: string;
	titre: string | RegExp;
	source: () => SourceExpectation[];
}

const COMMUNE: Cible = { id: 'commune', chemin: `/commune/${CODE_INSEE}`, titre: 'Challans', source: () => [] };
const DEPARTEMENT: Cible = {
	id: 'departement',
	chemin: '/departement/44',
	titre: 'Loire-Atlantique',
	source: () => [
		lecture('/v1/departements/44', {}, { status: 200, body: { statut: 'ok', donnees: DEPARTEMENT_44, meta: { source: SOURCES_DEPARTEMENT } } }),
		lecture('/v1/departements', {}, { status: 200, body: { statut: 'ok', donnees: DEPARTEMENTS_LISTE } })
	]
};
const DATASETS: Cible = {
	id: 'datasets',
	chemin: '/datasets',
	titre: 'Données & méthode',
	source: () => [lecture('/v1/datasets', {}, { status: 200, body: DATASETS_API })]
};
const ARRONDISSEMENT: Cible = {
	id: 'arrondissement',
	chemin: '/commune/75112',
	titre: /arrondissement/i,
	source: () => [
		lecture('/v1/commune', { code_insee: '75112' }, { status: 200, body: { statut: 'ok', donnees: [ARRONDISSEMENT_75112], meta: { source: SOURCES_COMMUNE } } })
	]
};
const DEPARTEMENTS: Cible = {
	id: 'departements',
	chemin: '/departements',
	titre: 'Départements',
	source: () => [lecture('/v1/departements', {}, { status: 200, body: { statut: 'ok', donnees: DEPARTEMENTS_LISTE } })]
};
// 04.8-07 (UI-SPEC § 6.6): the v4 h1 of the home.
const ACCUEIL: Cible = { id: 'accueil', chemin: '/', titre: 'Une adresse, en clair', source: () => [] };
const resultats = ROUTES_PHASE4.find((route) => route.id === 'resultats')!;
const RESULTATS: Cible = { id: 'resultats', chemin: resultats.chemin, titre: resultats.titre, source: resultats.source };
// 4.9 lot 7 (C8.5): both forms of the page without results: the directory (D4) and a query too short.
const ANNUAIRE: Cible = {
	id: 'annuaire',
	chemin: '/communes',
	titre: 'Annuaire des communes',
	source: () => [
		lecture(
			'/v1/communes/annuaire',
			{ couverture: 'servies', sort_by: 'population', order: 'desc', page: '1', per_page: '50', inclure_arrondissements: 'true' },
			{
				status: 200,
				body: {
					statut: 'ok',
					donnees: {
						results: [],
						total_count: 0,
						page: 1,
						per_page: 50,
						has_next: false,
						couverture: 'servies',
						departements_servis: ['35'],
						prix: { etat: 'servi', motif: null, fenetre_mediane_dvf: null }
					}
				}
			}
		)
	]
};
const TROP_COURT: Cible = { id: 'trop-court', chemin: '/communes?q=a', titre: NOM_RECHERCHE, source: () => [] };
const INTROUVABLE: Cible = { id: 'introuvable', chemin: '/route-inconnue-cadre-site', titre: 'Page introuvable', source: () => [] };
const MENTIONS: Cible = { id: 'mentions', chemin: '/mentions-legales', titre: 'Mentions légales', source: () => [] };

const MOCK_USER = {
	id: 2,
	email: 'test@datacommune.fr',
	name: 'Test User',
	is_verified: true,
	is_admin: false,
	created_at: '2026-01-01T00:00:00Z'
};

/**
 * Errors of the page and of its console on a public page; the browser's own line for a refused session
 * read (401) is not one. Not asserted on /settings: an (app) route raises a page error of the root
 * layout when it starts, outside the frame (noted in the handover of 04.8-02 task 1).
 */
function collecterErreurs(page: Page): string[] {
	const erreurs: string[] = [];
	page.on('pageerror', (err) => erreurs.push(`pageerror: ${err.message}`));
	page.on('console', (message) => {
		if (message.type() === 'error' && !message.text().startsWith('Failed to load resource')) erreurs.push(`console: ${message.text()}`);
	});
	return erreurs;
}

/** Opens a public page as an anonymous visitor, its source under exact expectations, once hydrated. */
async function ouvrir(page: Page, cible: Cible, largeur: number) {
	await page.setViewportSize({ width: largeur, height: 900 });
	await mockAuthAnonyme(page);
	const sonde = cible === COMMUNE ? await mockCommune(page) : await configureSsrSource(page.request, cible.source());
	const hydratee = page.waitForResponse((reponse) => new URL(reponse.url()).pathname === '/auth/me');
	await page.goto(cible.chemin);
	await hydratee;
	await expect(page.getByRole('heading', { level: 1 })).toContainText(cible.titre, { timeout: 15_000 });
	await sonde.assert();
}

/** Opens /settings with a signed-in session, once the account zone is read. */
async function ouvrirReglages(page: Page, largeur: number) {
	await page.setViewportSize({ width: largeur, height: 900 });
	await page.route('**/auth/me', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_USER) }));
	await page.route('**/auth/keys', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ keys: [], account_quota: 500, account_usage: 0 }) })
	);
	await page.goto('/settings');
	await expect(page.getByRole('heading', { name: 'Profil' })).toBeVisible({ timeout: 15_000 });
	await expect(page.getByRole('banner').getByRole('button', { name: 'Mon espace' })).toBeVisible();
}

const enTete = (page: Page) => page.getByRole('banner');

/** Where a capture goes: `DC_CADRE_EVIDENCE_DIR` when set, else the output folder of the run. */
function capture(testInfo: TestInfo, nom: string): string {
	const dossier = process.env.DC_CADRE_EVIDENCE_DIR;
	if (!dossier) return testInfo.outputPath(nom);
	mkdirSync(dossier, { recursive: true });
	return join(dossier, nom);
}

async function sansDebordement(page: Page, contexte: string) {
	const largeurs = await page.evaluate(() => ({ document: document.documentElement.scrollWidth, fenetre: window.innerWidth }));
	expect(largeurs.document, `aucun débordement horizontal : ${contexte}`).toBeLessThanOrEqual(largeurs.fenetre);
}

/** Computed colour of a token, read on a probe placed inside `parent` (the token is never written here). */
function couleurDuJeton(page: Page, parent: string, jeton: string): Promise<string> {
	return page.evaluate(
		({ parent, jeton }) => {
			const sonde = document.createElement('div');
			sonde.style.backgroundColor = `var(${jeton})`;
			document.querySelector(parent)!.append(sonde);
			const valeur = getComputedStyle(sonde).backgroundColor;
			sonde.remove();
			return valeur;
		},
		{ parent, jeton }
	);
}

type Boite = { x: number; y: number; largeur: number; hauteur: number };
const arrondi = (n: number) => Math.round(n * 10) / 10;

async function boite(locator: Locator): Promise<Boite> {
	const b = await locator.boundingBox();
	expect(b, 'boîte mesurée').not.toBeNull();
	return { x: arrondi(b!.x), y: arrondi(b!.y), largeur: arrondi(b!.width), hauteur: arrondi(b!.height) };
}

const seRecouvrent = (a: Boite, b: Boite) =>
	a.x < b.x + b.largeur - 0.5 && b.x < a.x + a.largeur - 0.5 && a.y < b.y + b.hauteur - 0.5 && b.y < a.y + a.hauteur - 0.5;

/** The controls of the public header, each with its box: brand, search, links and account. */
async function controlesEnTete(page: Page): Promise<{ nom: string; boite: Boite }[]> {
	const header = enTete(page);
	const controles: { nom: string; locator: Locator }[] = [
		{ nom: 'marque', locator: header.getByRole('link', { name: 'DataCommune', exact: true }) },
		...(await header.getByRole('navigation', { name: 'Navigation principale' }).getByRole('link').all()).map((lien, i) => ({
			nom: `lien ${i + 1}`,
			locator: lien
		})),
		...(await header.locator('form[role="search"]').all()).map((formulaire) => ({ nom: 'recherche', locator: formulaire })),
		...(await header.locator('.site-account').all()).map((compte) => ({ nom: 'compte', locator: compte })),
		...(await header.locator('summary').all()).map((menu) => ({ nom: 'menu', locator: menu }))
	];
	const resultat: { nom: string; boite: Boite }[] = [];
	for (const { nom, locator } of controles) {
		if (await locator.isVisible()) resultat.push({ nom, boite: await boitePeinte(locator) });
	}
	return resultat;
}

/**
 * The box of an element united with the boxes of its painted words: a word wider than its cell overflows
 * it. A word inside a visually hidden box (1 px, clipped) is not painted and does not count.
 */
async function boitePeinte(locator: Locator): Promise<Boite> {
	const b = await locator.evaluate((el) => {
		const cadre = el.getBoundingClientRect();
		let [gauche, haut, droite, bas] = [cadre.left, cadre.top, cadre.right, cadre.bottom];
		const marcheur = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
		for (let noeud = marcheur.nextNode(); noeud; noeud = marcheur.nextNode()) {
			if (!(noeud.textContent ?? '').trim()) continue;
			const parent = noeud.parentElement!;
			const boiteParent = parent.getBoundingClientRect();
			if (boiteParent.width <= 1 || boiteParent.height <= 1 || !parent.checkVisibility({ checkVisibilityCSS: true })) continue;
			const plage = document.createRange();
			plage.selectNodeContents(noeud);
			const mot = plage.getBoundingClientRect();
			[gauche, haut, droite, bas] = [Math.min(gauche, mot.left), Math.min(haut, mot.top), Math.max(droite, mot.right), Math.max(bas, mot.bottom)];
		}
		return { x: gauche, y: haut, largeur: droite - gauche, hauteur: bas - haut };
	});
	return { x: arrondi(b.x), y: arrondi(b.y), largeur: arrondi(b.largeur), hauteur: arrondi(b.hauteur) };
}

test.beforeEach(async ({ page }) => {
	await installLocalNetworkGuard(page);
});

test.afterEach(async ({ page }) => {
	expectNoUnexpectedOffLocalRequest(page);
});

// --- skip link (D-27) ---------------------------------------------------------------------------

for (const monde of ['page publique', '/settings'] as const) {
	test(`lien d’évitement, ${monde} : au focus, padding de 8 et 16 px lus sur :root, et un --s2 invalide le fait échouer`, async ({ page }) => {
		const erreurs = collecterErreurs(page);
		if (monde === '/settings') await ouvrirReglages(page, 1440);
		else await ouvrir(page, MENTIONS, 1440);

		const evitement = page.locator('a.skip-link');
		await expect(evitement, 'lien d’évitement absent : la mesure serait vacuous').toHaveCount(1);
		await expect(evitement).toHaveAttribute('href', '#main-content');
		await page.keyboard.press('Tab');
		await expect(evitement, 'le lien d’évitement est le premier arrêt').toBeFocused();
		const style = () =>
			evitement.evaluate((el) => {
				const s = getComputedStyle(el);
				const b = el.getBoundingClientRect();
				return {
					haut: s.paddingTop,
					droite: s.paddingRight,
					bas: s.paddingBottom,
					gauche: s.paddingLeft,
					rayon: s.borderTopLeftRadius,
					fond: s.backgroundColor,
					texte: s.color,
					visible: b.top >= 0 && b.bottom <= window.innerHeight && b.width > 0
				};
			});
		const auFocus = await style();
		expect(auFocus.visible, 'visible au focus').toBe(true);
		expect({ haut: auFocus.haut, droite: auFocus.droite, bas: auFocus.bas, gauche: auFocus.gauche }).toEqual({
			haut: '8px',
			droite: '16px',
			bas: '8px',
			gauche: '16px'
		});
		expect(auFocus.rayon, 'rayon --radius-ligne').toBe('6px');
		// The colours of today: ink background, paper text, as :root resolves them (the link is outside .public-site).
		expect(auFocus.fond).toBe(await couleurDuJeton(page, 'body', '--color-ink'));
		expect(auFocus.texte).toBe(await couleurDuJeton(page, 'body', '--color-paper'));

		// D-27, red case on the real consumer: once `--s2` is invalid on :root, the padding is no longer 8 px.
		await page.addStyleTag({ content: ':root { --s2: invalide !important; }' });
		await expect(evitement).toBeFocused();
		const sansJeton = await style();
		expect(sansJeton.haut, 'le padding vertical lit --s2 sur :root').not.toBe('8px');
		expect(sansJeton.haut).toBe('0px');
		if (monde === 'page publique') expect(erreurs).toEqual([]);
	});
}

// --- header of the connected space (invariance) -------------------------------------------------

const SELECTEURS_ENTETE = [
	'header.site-header',
	'.site-header-inner',
	'.site-brand',
	'.site-brand-label',
	'.site-nav',
	'.site-nav-link',
	'form.recherche-en-tete',
	'.site-end',
	'.site-dev-link',
	'.site-account',
	'.site-account-button'
];
const PROPRIETES_ENTETE = [
	'display',
	'position',
	'padding-top',
	'padding-right',
	'padding-bottom',
	'padding-left',
	'margin-left',
	'column-gap',
	'row-gap',
	'min-height',
	'max-width',
	'font-family',
	'font-size',
	'font-weight',
	'line-height',
	'color',
	'background-color',
	'border-bottom-width',
	'border-bottom-color'
];

/** Parts whose words are part of the form (the links and the account); the containers are measured by their box only. */
const AVEC_TEXTE = ['.site-brand', '.site-nav-link', '.site-dev-link', '.site-account-button'];

/** Computed box of every part of the header: geometry and the properties of its form. */
async function releveEnTete(page: Page) {
	await page.evaluate(() => document.fonts.ready.then(() => undefined));
	return page.evaluate(
		({ selecteurs, proprietes, avecTexte }) =>
			selecteurs.map((selecteur) => ({
				selecteur,
				elements: [...document.querySelectorAll(selecteur === 'header.site-header' ? selecteur : `header.site-header ${selecteur}`)].map((el) => {
					const b = el.getBoundingClientRect();
					const s = getComputedStyle(el);
					const r = (n: number) => Math.round(n * 10) / 10;
					return {
						...(avecTexte.includes(selecteur) ? { texte: (el.textContent ?? '').replace(/\s+/g, ' ').trim() } : {}),
						boite: [r(b.x), r(b.y), r(b.width), r(b.height)],
						style: Object.fromEntries(proprietes.map((p) => [p, s.getPropertyValue(p)]))
					};
				})
			})),
		{ selecteurs: SELECTEURS_ENTETE, proprietes: PROPRIETES_ENTETE, avecTexte: AVEC_TEXTE }
	);
}

test('en-tête de /settings (route (app)) : la boîte calculée égale la référence prise avant 04.8-02', async ({ page }, testInfo) => {
	await ouvrirReglages(page, 1440);
	for (const largeur of [1440, 999, 721, 375, 320]) {
		await page.setViewportSize({ width: largeur, height: 900 });
		await expect.poll(() => page.evaluate(() => window.innerWidth)).toBe(largeur);
		const releve = await releveEnTete(page);
		// Non-vacuity: the header, its inside, the brand, the links and the account were measured.
		for (const selecteur of ['header.site-header', '.site-header-inner', '.site-brand', '.site-nav-link', '.site-account-button']) {
			expect(releve.find((r) => r.selecteur === selecteur)?.elements.length, `${selecteur} mesuré à ${largeur} px`).toBeGreaterThan(0);
		}
		const police = releve.find((r) => r.selecteur === 'header.site-header')!.elements[0].style['font-family'];
		expect(police.split(',')[0].replace(/["']/g, '').trim(), 'police Inter').toBe('Inter');
		comparerALaReference(`en-tete-app-${largeur}`, '/settings', releve);
		await enTete(page).screenshot({ path: capture(testInfo, `settings-en-tete-${largeur}.png`), animations: 'disabled' });
	}
});

// The search of the connected header, inside its box (Codex 02-1 n° 1, Opus 02-1 P2-1): the box above
// measured the form only, which kept its size when its field and button changed. Two internal values
// are left out on purpose, without a visible change: the right padding of the field (92 px before, 72 px
// now, the tokens being a closed list; the « Ctrl K » hint covers what would run under it) and the
// background of that hint (transparent before, paper now: the paper of the field under it).
test('recherche de l’en-tête de /settings : champ, indice « Ctrl K » et loupe égaux à la référence prise avant 04.8-02', async ({ page }) => {
	await ouvrirReglages(page, 1440);
	await page.evaluate(() => document.fonts.ready.then(() => undefined));
	const releve = await page.evaluate(() => {
		const r = (n: number) => Math.round(n * 10) / 10;
		const formulaire = document.querySelector('header.site-header form.recherche-en-tete')!;
		const partie = (selecteur: string, proprietes: string[]) => {
			const el = formulaire.querySelector(selecteur);
			if (!el) return null;
			const b = el.getBoundingClientRect();
			const s = getComputedStyle(el);
			return { boite: [r(b.x), r(b.y), r(b.width), r(b.height)], style: Object.fromEntries(proprietes.map((p) => [p, s.getPropertyValue(p)])) };
		};
		const champ = formulaire.querySelector('input')!;
		return {
			nom: formulaire.getAttribute('aria-label'),
			aide: champ.getAttribute('placeholder'),
			indice: formulaire.querySelector('kbd')?.textContent ?? null,
			bouton: formulaire.querySelector('button')?.textContent?.trim() ?? null,
			loupe: formulaire.querySelector('button svg') !== null,
			champ: partie('input', ['padding-left', 'font-size', 'color', 'background-color', 'border-top-width', 'border-top-color', 'border-top-left-radius']),
			raccourci: partie('kbd', ['padding-left', 'padding-right', 'font-size', 'color', 'border-top-color', 'border-top-left-radius']),
			envoi: partie('button', ['width', 'height', 'color', 'background-color'])
		};
	});
	comparerALaReference('recherche-app-1440', '/settings', releve);
});

// --- public header (UI-SPEC § 3.11) -------------------------------------------------------------

test('en-tête public à 1440 px : forme v4, trois liens, recherche compacte à bouton plein, non collant', async ({ page }) => {
	const erreurs = collecterErreurs(page);
	await ouvrir(page, COMMUNE, 1440);
	const header = enTete(page);
	const papier = await couleurDuJeton(page, '.public-site', '--color-paper');
	const filet = await couleurDuJeton(page, '.public-site', '--color-line');
	const encre = await couleurDuJeton(page, '.public-site', '--color-ink');
	const accent = await couleurDuJeton(page, '.public-site', '--color-accent');

	const forme = await page.evaluate(() => {
		const h = getComputedStyle(document.querySelector('header.site-header')!);
		const i = getComputedStyle(document.querySelector('.site-header-inner')!);
		return {
			position: h.position,
			fond: h.backgroundColor,
			filet: `${h.borderBottomWidth} ${h.borderBottomStyle} ${h.borderBottomColor}`,
			largeur: i.maxWidth,
			padding: `${i.paddingTop} ${i.paddingRight} ${i.paddingBottom} ${i.paddingLeft}`,
			ecarts: `${i.rowGap} ${i.columnGap}`,
			flexWrap: i.flexWrap
		};
	});
	expect(forme).toEqual({
		position: 'static',
		fond: papier,
		filet: `1px solid ${filet}`,
		largeur: '1240px',
		padding: '12px 24px 12px 24px',
		ecarts: '12px 24px',
		flexWrap: 'wrap'
	});

	const marque = header.getByRole('link', { name: 'DataCommune', exact: true });
	await expect(marque).toHaveAttribute('href', '/');
	expect(await marque.evaluate((el) => [getComputedStyle(el).fontSize, getComputedStyle(el).fontWeight])).toEqual(['18px', '600']);

	const navigation = header.getByRole('navigation', { name: 'Navigation principale' });
	const liens = navigation.getByRole('link');
	await expect(liens).toHaveText(LIENS_PUBLICS.map((l) => l.nom));
	for (const [i, lien] of LIENS_PUBLICS.entries()) await expect(liens.nth(i)).toHaveAttribute('href', lien.href);
	await expect(header.getByRole('link', { name: 'Rechercher', exact: true }), 'le lien « Rechercher » est retiré (A-03)').toHaveCount(0);
	for (const lien of await liens.all()) {
		expect(await lien.evaluate((el) => [getComputedStyle(el).color, getComputedStyle(el).fontSize])).toEqual([encre, '16px']);
	}

	const recherche = header.getByRole('search', { name: NOM_RECHERCHE });
	await expect(recherche).toBeVisible();
	await expect(recherche).toHaveAttribute('action', '/communes');
	await expect(recherche).toHaveAttribute('method', 'get');
	const champ = recherche.getByRole('searchbox', { name: NOM_RECHERCHE });
	await expect(champ).toHaveAttribute('name', 'q');
	await expect(champ).toHaveAttribute('placeholder', 'Adresse ou commune');
	await expect(champ).toHaveAttribute('maxlength', '200');
	await expect(champ).toHaveAttribute('aria-keyshortcuts', 'Control+K Meta+K');
	expect(await champ.evaluate((el) => getComputedStyle(el).fontSize)).toBe('16px');
	// The label is the visually hidden one of the contract: the help text is the only visible word.
	const etiquette = recherche.locator(`label[for="${await champ.getAttribute('id')}"]`);
	await expect(etiquette).toHaveText(NOM_RECHERCHE);
	expect(await etiquette.evaluate((el) => el.getBoundingClientRect().width)).toBeLessThanOrEqual(1);
	// WCAG 2.5.3: the accessible name of the button starts with its visible text.
	const bouton = recherche.getByRole('button', { name: NOM_RECHERCHE, exact: true });
	await expect(bouton).toBeVisible();
	const visible = await bouton.evaluate((el) =>
		[...el.childNodes]
			.filter((n) => n.nodeType === Node.TEXT_NODE || (n instanceof Element && n.getBoundingClientRect().width > 1))
			.map((n) => n.textContent ?? '')
			.join('')
			.trim()
	);
	expect(visible).toBe('Rechercher');
	expect(NOM_RECHERCHE.startsWith(visible)).toBe(true);
	expect(await bouton.evaluate((el) => [getComputedStyle(el).fontWeight, getComputedStyle(el).fontSize])).toEqual(['600', '16px']);
	expect(await bouton.evaluate((el) => getComputedStyle(el).backgroundColor), 'bouton plein, fond accent').toBe(accent);

	// One row at 1440 px: every control shares the brand's line.
	const controles = await controlesEnTete(page);
	expect(controles.map((c) => c.nom)).toEqual(['marque', 'lien 1', 'lien 2', 'lien 3', 'recherche', 'compte']);
	const haut = Math.max(...controles.map((c) => c.boite.y));
	const bas = Math.min(...controles.map((c) => c.boite.y + c.boite.hauteur));
	expect(bas, `une seule rangée à 1440 px : ${JSON.stringify(controles)}`).toBeGreaterThan(haut);
	await sansDebordement(page, '1440 px');
	expect(erreurs).toEqual([]);
});

// 4.9 lot 7 (C8.5): one rule. A page has exactly one search of the site: the large field of its content
// when it is the action of the page, else the field of the header.
test('une seule recherche du site par page : celle du contenu sur l’accueil, /communes et la page introuvable, celle de l’en-tête ailleurs, aux trois largeurs', async ({ page }) => {
	for (const cible of [COMMUNE, DEPARTEMENT, DATASETS, ACCUEIL, RESULTATS, ANNUAIRE, TROP_COURT, INTROUVABLE]) {
		const avec = ![ACCUEIL, RESULTATS, ANNUAIRE, TROP_COURT, INTROUVABLE].includes(cible);
		await ouvrir(page, cible, LARGEURS[0]);
		for (const largeur of LARGEURS) {
			await page.setViewportSize({ width: largeur, height: 900 });
			await expect.poll(() => page.evaluate(() => window.innerWidth)).toBe(largeur);
			if (avec) {
				await expect(enTete(page).getByRole('search', { name: NOM_RECHERCHE }), `${cible.chemin} à ${largeur} px`).toBeVisible();
			} else {
				await expect(enTete(page).getByRole('search'), `${cible.chemin} à ${largeur} px`).toHaveCount(0);
				await expect(page.getByRole('main').locator('form[role="search"][action="/communes"]'), `${cible.chemin} à ${largeur} px`).toBeVisible();
			}
			await expect(page.locator('form[role="search"][action="/communes"]'), `une seule recherche du site sur ${cible.chemin} à ${largeur} px`).toHaveCount(1);
			await sansDebordement(page, `${cible.chemin} à ${largeur} px`);
		}
	}
});

/** No two controls overlap. */
function aucunRecouvrement(controles: { nom: string; boite: Boite }[]) {
	for (let i = 0; i < controles.length; i++) {
		for (let j = i + 1; j < controles.length; j++) {
			expect(seRecouvrent(controles[i].boite, controles[j].boite), `${controles[i].nom} et ${controles[j].nom} se recouvrent`).toBe(false);
		}
	}
}

for (const largeur of [375, 320] as const) {
	test(`${largeur} px : deux rangées, la marque et « Menu » puis la recherche seule, au plus un cinquième de l’écran ; le menu ouvre les liens et le compte (D-41)`, async ({ page }, testInfo) => {
		const erreurs = collecterErreurs(page);
		await ouvrir(page, COMMUNE, largeur);
		await page.setViewportSize({ width: largeur, height: 812 });
		await expect.poll(() => page.evaluate(() => window.innerHeight)).toBe(812);
		const header = enTete(page);
		const champ = header.getByRole('searchbox', { name: NOM_RECHERCHE });
		await expect(champ).toBeVisible();
		await champ.fill('a'.repeat(200));
		await expect(champ).toHaveValue('a'.repeat(200));

		// Folded: the brand and « Menu » on the first row, the search alone on the second.
		const menu = header.locator('summary');
		await expect(menu, '« Menu » sous 720 px').toBeVisible();
		await expect(menu).toHaveAccessibleName('Menu');
		const plie = await controlesEnTete(page);
		expect(plie.map((c) => c.nom), 'liens et compte repliés derrière « Menu »').toEqual(['marque', 'recherche', 'menu']);
		const marque = plie.find((c) => c.nom === 'marque')!.boite;
		const bouton = plie.find((c) => c.nom === 'menu')!.boite;
		const recherche = plie.find((c) => c.nom === 'recherche')!.boite;
		expect(bouton.y, '« Menu » sur la rangée de la marque').toBeLessThan(marque.y + marque.hauteur);
		expect(recherche.y, 'la recherche sous la marque et « Menu »').toBeGreaterThanOrEqual(Math.max(marque.y + marque.hauteur, bouton.y + bouton.hauteur));
		const interieur = await page.evaluate(() => {
			const el = document.querySelector('.site-header-inner')!;
			const s = getComputedStyle(el);
			return el.clientWidth - Number.parseFloat(s.paddingLeft) - Number.parseFloat(s.paddingRight);
		});
		expect(Math.abs(recherche.largeur - interieur), 'la recherche prend toute la rangée').toBeLessThanOrEqual(1);
		aucunRecouvrement(plie);
		expect((await boite(menu)).hauteur, 'cible de 44 px').toBeGreaterThanOrEqual(44);
		// H-01 (03-17) kept by D-41: at most a fifth of the window, the two rows of 44 px and their gaps.
		const hauteur = (await boite(page.locator('header.site-header'))).hauteur;
		console.log(`RELEVE ${largeur}px hauteur=${hauteur} ${JSON.stringify(plie)}`);
		expect(hauteur, 'au plus un cinquième de 812 px').toBeLessThanOrEqual(812 / 5);
		expect(hauteur, 'deux rangées de 44 px').toBeGreaterThanOrEqual(88);
		await sansDebordement(page, `${largeur} px, 200 caractères dans le champ`);
		await header.screenshot({ path: capture(testInfo, `commune-en-tete-${largeur}-200-caracteres.png`), animations: 'disabled' });

		// Keyboard order, folded: the brand, « Menu », then the search, as on screen (Codex 02-1 n° 2).
		await header.getByRole('link', { name: 'DataCommune', exact: true }).focus();
		await page.keyboard.press('Tab');
		await expect(menu).toBeFocused();
		await page.keyboard.press('Tab');
		await expect(champ).toBeFocused();

		// Open: the three links and the account under the search (the order of the DOM and of the keyboard).
		await menu.click();
		const navigation = header.getByRole('navigation', { name: 'Navigation principale' });
		await expect(navigation.getByRole('link')).toHaveText(LIENS_PUBLICS.map((l) => l.nom));
		await expect(header.getByRole('link', { name: 'Se connecter' })).toBeVisible();
		const ouvert = await controlesEnTete(page);
		expect(ouvert.map((c) => c.nom)).toEqual(['marque', 'lien 1', 'lien 2', 'lien 3', 'recherche', 'compte', 'menu']);
		const rechercheOuverte = ouvert.find((c) => c.nom === 'recherche')!.boite;
		for (const panneau of ouvert.filter((c) => c.nom.startsWith('lien') || c.nom === 'compte')) {
			expect(panneau.boite.y, `${panneau.nom} sous la recherche`).toBeGreaterThanOrEqual(rechercheOuverte.y + rechercheOuverte.hauteur);
		}
		// Keyboard order, open: « Menu », the search field and its button, then the three links and the account.
		await menu.focus();
		const suite = [champ, header.getByRole('button', { name: NOM_RECHERCHE, exact: true }), ...(await navigation.getByRole('link').all()), header.getByRole('link', { name: 'Se connecter' })];
		for (const cible of suite) {
			await page.keyboard.press('Tab');
			await expect(cible).toBeFocused();
		}
		aucunRecouvrement(ouvert);
		// The links of the header: 14 px under 720 px, 44 px targets.
		for (const lien of await navigation.getByRole('link').all()) {
			expect(await lien.evaluate((el) => getComputedStyle(el).fontSize)).toBe('14px');
			expect((await boite(lien)).hauteur, 'cible de 44 px').toBeGreaterThanOrEqual(44);
		}
		await sansDebordement(page, `${largeur} px, menu ouvert`);
		await header.screenshot({ path: capture(testInfo, `commune-en-tete-${largeur}-menu-ouvert.png`), animations: 'disabled' });

		// Escape from a link of the panel folds it and gives the focus back to « Menu ».
		await navigation.getByRole('link', { name: 'Comparer' }).focus();
		await page.keyboard.press('Escape');
		await expect(navigation.getByRole('link')).toHaveCount(0);
		await expect(menu).toBeFocused();

		// A navigation from the panel folds it: the next page opens with the menu closed.
		await menu.click();
		await navigation.getByRole('link', { name: 'Développeurs' }).click();
		await expect(page).toHaveURL(/\/developpeurs$/);
		await expect(header.locator('details')).not.toHaveAttribute('open', /.*/);
		expect(erreurs).toEqual([]);
	});
}

test('160 px, session ouverte : « Mon espace » ouvre son menu dans le panneau, sans débordement (Codex 02-1 n° 3)', async ({ page }) => {
	await page.setViewportSize({ width: 320, height: 900 });
	await page.route('**/auth/me', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_USER) }));
	const sonde = await mockCommune(page);
	await page.goto(COMMUNE.chemin);
	await expect(page.getByRole('heading', { level: 1 })).toContainText(COMMUNE.titre);
	await sonde.assert();
	await page.setViewportSize({ width: 160, height: 450 });
	await expect.poll(() => page.evaluate(() => window.innerWidth)).toBe(160);
	const header = enTete(page);
	await header.locator('summary').click();
	await header.getByRole('button', { name: 'Mon espace' }).click();
	const reglages = header.getByRole('link', { name: 'Réglages' });
	await expect(reglages).toBeVisible();
	const liste = await boite(header.locator('.site-menu-list'));
	expect(liste.x + liste.largeur, 'menu du compte dans la fenêtre').toBeLessThanOrEqual(160.5);
	expect(liste.x, 'menu du compte dans la fenêtre').toBeGreaterThanOrEqual(0);
	await sansDebordement(page, '160 px, menu du compte ouvert');
});

test.describe('sans JavaScript', () => {
	test.use({ javaScriptEnabled: false });

	test('375 px : « Menu » ouvre les liens et le compte sans JavaScript, deux rangées replié (D-41)', async ({ page }) => {
		await page.setViewportSize({ width: 375, height: 812 });
		const sonde = await mockCommune(page);
		await page.goto(COMMUNE.chemin);
		await expect(page.getByRole('heading', { level: 1 })).toContainText(COMMUNE.titre);
		await sonde.assert();
		const header = enTete(page);
		const navigation = header.getByRole('navigation', { name: 'Navigation principale' });
		await expect(navigation.getByRole('link')).toHaveCount(0);
		const hauteur = (await boite(page.locator('header.site-header'))).hauteur;
		expect(hauteur, 'au plus un cinquième de 812 px').toBeLessThanOrEqual(812 / 5);
		await header.locator('summary').click();
		await expect(navigation.getByRole('link')).toHaveText(LIENS_PUBLICS.map((l) => l.nom));
		await sansDebordement(page, '375 px sans JavaScript, menu ouvert');
	});
});

test('160 px (zoom réel de 200 % d’une fenêtre de 320 px) : rien ne se chevauche dans l’en-tête, aucun débordement', async ({ page }, testInfo) => {
	await ouvrir(page, COMMUNE, 320);
	await page.setViewportSize({ width: 160, height: 450 });
	await expect.poll(() => page.evaluate(() => window.innerWidth)).toBe(160);
	// Folded (brand, search, « Menu »), then open (the three links and the account as well).
	for (const [etat, attendus] of [
		['replié', 3],
		['ouvert', 7]
	] as const) {
		if (etat === 'ouvert') await enTete(page).locator('summary').click();
		const controles = await controlesEnTete(page);
		expect(controles.length, `contrôles de l’en-tête mesurés, ${etat}`).toBe(attendus);
		await page.screenshot({ path: capture(testInfo, `commune-en-tete-160-${etat}.png`), animations: 'disabled' });
		for (let i = 0; i < controles.length; i++) {
			for (let j = i + 1; j < controles.length; j++) {
				expect(
					seRecouvrent(controles[i].boite, controles[j].boite),
					`${controles[i].nom} et ${controles[j].nom} se recouvrent (${etat}) : ${JSON.stringify([controles[i], controles[j]])}`
				).toBe(false);
			}
		}
		for (const { nom, boite: b } of controles) expect(b.x + b.largeur, `${nom} dans la fenêtre (${etat})`).toBeLessThanOrEqual(160.5);
		await sansDebordement(page, `160 px, ${etat}`);
	}
});

for (const largeur of [721, 999] as const) {
	test(`${largeur} px : marges latérales de l’en-tête et du pied publics (Opus P3-8)`, async ({ page }) => {
		await ouvrir(page, COMMUNE, largeur);
		const marges = await page.evaluate(() => {
			const s = (sel: string) => getComputedStyle(document.querySelector(sel)!);
			return {
				enTete: [s('.site-header-inner').paddingLeft, s('.site-header-inner').paddingRight],
				pied: [s('.site-footer-inner').paddingLeft, s('.site-footer-inner').paddingRight]
			};
		});
		console.log(`RELEVE ${largeur}px marges ${JSON.stringify(marges)}`);
		expect(marges).toEqual({ enTete: ['24px', '24px'], pied: ['24px', '24px'] });
		await sansDebordement(page, `${largeur} px`);
	});
}

// --- public footer ------------------------------------------------------------------------------

test('pied public : aplat de la charte, padding de 32 et 24 px, 14 px, neuf liens puis le contact, et la phrase d’aujourd’hui', async ({ page }) => {
	await ouvrir(page, COMMUNE, 1440);
	const aplat = await couleurDuJeton(page, '.public-site', '--color-pied');
	const forme = await page.evaluate(() => {
		const f = getComputedStyle(document.querySelector('footer.site-footer')!);
		const i = getComputedStyle(document.querySelector('.site-footer-inner')!);
		return {
			fond: f.backgroundColor,
			filet: f.borderTopWidth,
			largeur: i.maxWidth,
			padding: `${i.paddingTop} ${i.paddingRight} ${i.paddingBottom} ${i.paddingLeft}`,
			taille: i.fontSize
		};
	});
	expect(forme).toEqual({ fond: aplat, filet: '0px', largeur: '1240px', padding: '32px 24px 32px 24px', taille: '14px' });
	const pied = page.getByRole('contentinfo');
	await expect(pied.getByRole('link')).toHaveText(LIENS_PIED);
	await expect(pied.getByRole('link', { name: 'Signaler une erreur' })).toHaveAttribute('href', 'mailto:signalement@datacommune.fr');
	await expect(pied.getByText(PHRASE_PIED, { exact: true })).toBeVisible();
	// The sentence is held at the reading measure: its max-width is 60ch of its own font.
	const mesure = await pied.getByText(PHRASE_PIED, { exact: true }).evaluate((el) => {
		const sonde = document.createElement('span');
		sonde.style.cssText = 'position:absolute;display:block;visibility:hidden;width:60ch';
		el.append(sonde);
		const soixante = sonde.getBoundingClientRect().width;
		sonde.remove();
		return { maximum: Number.parseFloat(getComputedStyle(el).maxWidth), soixante };
	});
	expect(Math.abs(mesure.maximum - mesure.soixante), `max-width de la phrase ${JSON.stringify(mesure)}`).toBeLessThanOrEqual(0.5);
});

// --- quality of the frame at three widths (UI-SPEC § 8) -----------------------------------------

for (const largeur of LARGEURS) {
	test(`${largeur} px : en-tête et pied sans violation axe, sans débordement, plancher de 14 px, graisses et mesure`, async ({ page }, testInfo) => {
		const erreurs = collecterErreurs(page);
		await ouvrir(page, COMMUNE, largeur);
		await enTete(page).screenshot({ path: capture(testInfo, `commune-en-tete-${largeur}.png`), animations: 'disabled' });
		await page.getByRole('contentinfo').screenshot({ path: capture(testInfo, `commune-pied-${largeur}.png`), animations: 'disabled' });
		const portee = 'header.site-header, footer.site-footer';
		const axe = await new AxeBuilder({ page }).include('header.site-header').include('footer.site-footer').withTags([...TAGS_WCAG]).analyze();
		expect(axe.passes.length, 'axe n’a rien inspecté').toBeGreaterThan(0);
		expect(axe.violations.map((v) => ({ id: v.id, noeuds: v.nodes.map((n) => n.target.join(' ')) }))).toEqual([]);
		await sansDebordement(page, `${largeur} px`);
		expect(await verifierPlancherTypo(page, portee), `plancher de 14 px à ${largeur} px`).toEqual([]);
		expect(await verifierGraisses(page, portee), `graisses à ${largeur} px`).toEqual([]);
		expect(await verifierMesure(page, portee, ['p']), `mesure à ${largeur} px`).toEqual([]);
		expect(erreurs).toEqual([]);
	});
}

// --- breadcrumb JSON-LD (reference for task 3) --------------------------------------------------

for (const cible of [DEPARTEMENT, COMMUNE, ARRONDISSEMENT, DEPARTEMENTS]) {
	test(`JSON-LD BreadcrumbList de ${cible.chemin} égal à la référence versionnée`, async ({ page }) => {
		await ouvrir(page, cible, 1440);
		const textes = await page.locator('head script[type="application/ld+json"]').allTextContents();
		const fils = textes.filter((texte) => (JSON.parse(texte) as { '@type'?: string })['@type'] === 'BreadcrumbList');
		expect(fils, `un seul BreadcrumbList sur ${cible.chemin}`).toHaveLength(1);
		// The visible breadcrumb and the structured one come from the same list: its links, then the current page.
		const etapes = (JSON.parse(fils[0]) as { itemListElement: { name: string }[] }).itemListElement.map((e) => e.name);
		const fil = page.getByRole('navigation', { name: "Fil d'Ariane" });
		await expect(fil.locator('a, [aria-current="page"]')).toHaveText(etapes);
		comparerALaReference(`fil-ariane-${cible.id}`, cible.chemin, fils[0]);
		// v4 form (UI-SPEC § 3.11, task 3): an ordered list, the current page last and only it, in a span;
		// 14 px muted, links underlined, the separator « › » drawn by CSS, never text.
		const etapesVisibles = fil.locator('ol > li');
		await expect(etapesVisibles).toHaveCount(etapes.length);
		await expect(fil.locator('[aria-current="page"]')).toHaveCount(1);
		await expect(etapesVisibles.last().locator('span[aria-current="page"]')).toHaveText(etapes.at(-1)!);
		const accent = await couleurDuJeton(page, '.public-site', '--color-accent');
		const forme = await fil.evaluate((nav) => {
			const lien = nav.querySelector('a')!;
			return {
				taille: getComputedStyle(nav).fontSize,
				couleurLien: getComputedStyle(lien).color,
				soulignement: getComputedStyle(lien).textDecorationLine,
				separateur: getComputedStyle(nav.querySelectorAll('li')[1], '::before').content,
				texte: (nav.textContent ?? '').includes('›') || (nav.textContent ?? '').includes('/')
			};
		});
		expect(forme).toMatchObject({ taille: '14px', couleurLien: accent, soulignement: 'underline', texte: false });
		expect(forme.separateur).toContain('›');
		// Targets (UI-SPEC § 8, Codex 02-1 n° 4): 24 px at 1440, 44 px at 375, the line keeping its height.
		for (const [largeur, cible] of [
			[1440, 24],
			[375, 44]
		] as const) {
			await page.setViewportSize({ width: largeur, height: 900 });
			await expect.poll(() => page.evaluate(() => window.innerWidth)).toBe(largeur);
			for (const lien of await fil.getByRole('link').all()) {
				const b = await boite(lien);
				expect(Math.min(b.largeur, b.hauteur), `cible du fil à ${largeur} px : ${JSON.stringify(b)}`).toBeGreaterThanOrEqual(cible);
			}
		}
	});
}
