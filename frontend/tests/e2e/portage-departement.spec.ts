// Port of the departement pages to the direction A (plan 04.8-05, UI-SPEC v4 §§ 3.10, 6.3, 8). Headless
// runner, isolated context, every SSR read served by the local fail-closed source (AGENTS.md). Split on
// 2026-11-09 from `portage-departement-comparateur.spec.ts`, test for test; the comparator half is
// `portage-comparateur.spec.ts`.
//
// What this spec locks:
//   - the structured data of `/departement/44`, of a departement that is not indexable (29) and of
//     `/departements` do not move with their form: the text of every JSON-LD script equals a reference
//     taken on the pages before the port (`fixtures/departement-reference.json`), the test origin being
//     replaced by `{ORIGINE}` (D-35). `DC_MAJ_REFERENCE_DEPARTEMENT=1` rewrites the reference instead of
//     comparing: only on the state before a port, never to turn a failure green;
//   - the form of the mock-up: « L’essentiel » in tiles (« Chiffres clés : {nom} » until D1 of 2026-11-10), the principal communes in a table, the
//     ways in page links (→), the sources in external links (↗); one panel per region on the list;
//   - at 1440, 375 and 320 px: axe (WCAG 2.2 AA) without violation, no overflow (not even under the text
//     spacing of WCAG 1.4.12), the 14 px floor, the locked weights, the 60ch measure, no error of the page;
//     a 200 % zoom lays out as a phone.
// Captures at 1440 and 375 px are kept outside the repository (`DC_PORTAGE_EVIDENCE_DIR` when set).
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import AxeBuilder from '@axe-core/playwright';
import { test, expect, type Page, type TestInfo } from '@playwright/test';
import {
	DEPARTEMENT_22,
	DEPARTEMENT_29,
	DEPARTEMENT_44,
	DEPARTEMENT_75,
	DEPARTEMENTS_LISTE
} from '../../src/lib/data/__fixtures__/departement';
import { configureSsrSource, type SourceExpectation } from './fixtures/bien-ssr-source';
import { E2E_APP_ORIGIN } from './fixtures/ports';
import { verifierGraisses, verifierMesure, verifierPlancherTypo } from './fixtures/quality';

const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] as const;
const LARGEURS = [1440, 375, 320] as const;
const LECTURES = 12;
const ORIGINE = '{ORIGINE}';
const REFERENCE_URL = new URL('./fixtures/departement-reference.json', import.meta.url);
const MAJ_REFERENCE = process.env.DC_MAJ_REFERENCE_DEPARTEMENT === '1';

const MOCK_USER = { id: 2, email: 'test@datacommune.fr', name: 'Test User', is_verified: true, is_admin: false, created_at: '2026-01-01T00:00:00Z' };

const SOURCES = [
	{ nom: 'DVF', producteur: 'DGFiP / Etalab', licence: 'Licence Ouverte 2.0', url: 'https://www.data.gouv.fr/fr/datasets/demandes-de-valeurs-foncieres/' },
	{ nom: 'DPE', producteur: 'ADEME', licence: 'Licence Ouverte 2.0', url: 'https://data.ademe.fr/datasets/dpe-v2-logements-existants' },
	{ nom: 'Recensement', producteur: 'INSEE', licence: 'Licence Ouverte 2.0' }
];

const DEPARTEMENTS: Record<string, unknown> = { '44': DEPARTEMENT_44, '29': DEPARTEMENT_29, '22': DEPARTEMENT_22, '75': DEPARTEMENT_75 };

type Reponse = { status: number; body: unknown; headers?: Record<string, string> };
const synthese = (donnees: unknown): Reponse => ({ status: 200, body: { statut: 'ok', donnees, meta: { source: SOURCES } } });
const LISTE: Reponse = { status: 200, body: { statut: 'ok', donnees: DEPARTEMENTS_LISTE } };
const PANNE: Reponse = { status: 503, body: { detail: 'Service indisponible' }, headers: { 'Retry-After': '45' } };

function attente(pathname: string, reponse: Reponse): SourceExpectation {
	return { method: 'GET', pathname, query: {}, minCalls: 0, maxCalls: LECTURES, responses: Array.from({ length: LECTURES }, () => reponse) };
}

async function servirDepartement(page: Page, code: string, reponse: Reponse = synthese(DEPARTEMENTS[code])) {
	return configureSsrSource(page.request, [attente(`/v1/departements/${code}`, reponse), attente('/v1/departements', LISTE)]);
}

async function ouvrir(page: Page, chemin: string, largeur: number, hauteur = 900) {
	await page.setViewportSize({ width: largeur, height: hauteur });
	const hydratee = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
	const reponse = await page.goto(chemin);
	await hydratee;
	await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
	return reponse;
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
			.filter((el) => !el.closest('[role="region"][tabindex="0"]'))
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

const ESPACEMENT_DU_TEXTE = '*{line-height:1.5 !important;letter-spacing:0.12em !important;word-spacing:0.16em !important}p{margin-bottom:2em !important}';

async function planDesTitres(page: Page) {
	const niveaux = await page.evaluate(() =>
		[...document.querySelectorAll('main h1, main h2, main h3, main h4, main h5, main h6')]
			.filter((h) => h.checkVisibility({ checkOpacity: false, checkVisibilityCSS: true }))
			.map((h) => ({ niveau: Number(h.tagName.slice(1)), texte: (h.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 40) }))
	);
	expect(niveaux.filter((h) => h.niveau === 1), 'un seul h1').toHaveLength(1);
	let precedent = 0;
	for (const h of niveaux) {
		expect(h.niveau, `saut de niveau avant « ${h.texte} »`).toBeLessThanOrEqual(precedent + 1);
		precedent = h.niveau;
	}
}

async function qualite(page: Page, contexte: string, largeur: number, testInfo: TestInfo, capture: string) {
	await sansViolationAxe(page, contexte);
	await sansDebordement(page, contexte);
	if (largeur !== 320) {
		await page.screenshot({ path: preuve(testInfo, `${capture}-${largeur}-ecran.jpg`), quality: 70, animations: 'disabled' });
		await page.screenshot({ path: preuve(testInfo, `${capture}-${largeur}-page.jpg`), quality: 70, fullPage: true, animations: 'disabled' });
	}
	expect.soft(await verifierPlancherTypo(page, 'main'), `${contexte} : plancher de 14 px`).toEqual([]);
	expect.soft(await verifierGraisses(page, 'main'), `${contexte} : graisses`).toEqual([]);
	expect.soft(await verifierMesure(page, 'main', ['p']), `${contexte} : mesure`).toEqual([]);
	await page.addStyleTag({ content: ESPACEMENT_DU_TEXTE });
	await sansDebordement(page, `${contexte}, espacement du texte WCAG 1.4.12`);
}

test.beforeEach(async ({ page }) => {
	await page.route('**/auth/me', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_USER) }));
});

test.describe('Départements — données structurées égales à la référence (D-35)', () => {
	test('JSON-LD de /departement/44, /departement/29 (non indexable) et /departements', async ({ page }) => {
		const releve: Record<string, { robots: string | null; jsonLd: string[] }> = {};
		for (const chemin of ['/departement/44', '/departement/29', '/departements']) {
			const code = chemin.split('/')[2];
			const source = code ? await servirDepartement(page, code) : await configureSsrSource(page.request, [attente('/v1/departements', LISTE)]);
			await ouvrir(page, chemin, 1440);
			const robots = await page.evaluate(() => document.querySelector('meta[name="robots"]')?.getAttribute('content') ?? null);
			releve[chemin] = { robots, jsonLd: (await page.locator('script[type="application/ld+json"]').allTextContents()).map(canonique) };
			await source.assert();
		}
		expect(releve['/departement/29'].robots).toBe('noindex, follow');
		if (MAJ_REFERENCE) {
			writeFileSync(REFERENCE_URL, `${JSON.stringify(releve, null, '\t')}\n`, 'utf8');
			return;
		}
		const reference = JSON.parse(readFileSync(REFERENCE_URL, 'utf8')) as typeof releve;
		expect(releve).toEqual(reference);
	});

	test('panne : 503 et panneau d’état « Recharger les chiffres »', async ({ page }) => {
		await servirDepartement(page, '44', PANNE);
		const reponse = await ouvrir(page, '/departement/44', 1440);
		expect(reponse?.status()).toBe(503);
		const alerte = page.getByRole('alert');
		await expect(alerte).toBeVisible();
		await expect(alerte.getByRole('button', { name: 'Recharger les chiffres' })).toBeVisible();
		await expect(page.locator('main .notice')).toHaveCount(1);
	});
});

test.describe('Département — forme de la maquette (UI-SPEC § 6.3)', () => {
	test('L’essentiel en tuiles, communes principales en tableau, Parcourir (→), Sources (↗)', async ({ page }) => {
		const source = await servirDepartement(page, '44');
		await ouvrir(page, '/departement/44', 1440);
		const chiffres = page.locator('section.panneau-essentiel').filter({ has: page.getByRole('heading', { level: 2, name: 'L’essentiel' }) });
		await expect(chiffres).toHaveCount(1);
		await expect(chiffres.locator('dl.cles > .cle')).toHaveCount(4);
		const tableau = page.locator('section.panneau [role="region"][tabindex="0"] table');
		await expect(tableau).toHaveCount(1);
		await expect(tableau.locator('caption')).toBeAttached();
		const parcourir = page.locator('section.panneau').filter({ has: page.getByRole('heading', { level: 2, name: 'Parcourir' }) });
		const flechesPages = await parcourir.locator('ul.liste-liens a .fleche').allTextContents();
		expect(flechesPages.length).toBeGreaterThan(0);
		expect(flechesPages.every((f) => f === '→')).toBe(true);
		const sources = page.locator('section.panneau').filter({ has: page.getByRole('heading', { level: 2, name: 'Sources et licences' }) });
		const externes = sources.locator('ul.liste-liens a');
		await expect(externes.first()).toBeVisible();
		for (const lien of await externes.all()) {
			await expect(lien).toHaveAttribute('rel', 'noopener noreferrer');
			await expect(lien.locator('.fleche')).toHaveText('↗');
		}
		await expect(page.locator('.dep-reperes')).toHaveCount(0);
		await planDesTitres(page);
		await source.assert();
	});

	test('/departements : un panneau par région, chaque département en lien de page avec son état', async ({ page }) => {
		const source = await configureSsrSource(page.request, [attente('/v1/departements', LISTE)]);
		await ouvrir(page, '/departements', 1440);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('Départements');
		const panneaux = page.locator('section.panneau.panneau-simple');
		expect(await panneaux.count()).toBeGreaterThan(1);
		for (const panneau of await panneaux.all()) {
			const liens = panneau.locator('ul.liste-liens a');
			await expect(liens.first()).toBeVisible();
			for (const href of await liens.evaluateAll((as) => as.map((a) => a.getAttribute('href')))) expect(href).toMatch(/^\/departement\/[0-9AB]{2,3}$/);
		}
		await planDesTitres(page);
		await source.assert();
	});
});

test.describe('Départements — qualité aux trois largeurs (UI-SPEC § 8)', () => {
	for (const largeur of LARGEURS) {
		test(`${largeur} px : /departement/44, 29, 22, 75 et /departements`, async ({ page }, testInfo) => {
			test.setTimeout(180_000);
			const erreurs = collecterErreurs(page);
			for (const code of ['44', '29', '22', '75']) {
				await servirDepartement(page, code);
				await ouvrir(page, `/departement/${code}`, largeur, largeur >= 1024 ? 900 : 800);
				await qualite(page, `/departement/${code} à ${largeur} px`, largeur, testInfo, `departement-${code}`);
			}
			await configureSsrSource(page.request, [attente('/v1/departements', LISTE)]);
			await ouvrir(page, '/departements', largeur, largeur >= 1024 ? 900 : 800);
			await qualite(page, `/departements à ${largeur} px`, largeur, testInfo, 'departements');
			expect(erreurs).toEqual([]);
		});
	}

	// 4.9 (A9-8): the pilot control, reached and read with its panel open, every severity counted.
	for (const [largeur, hauteur] of [[1440, 900], [375, 812], [320, 800], [720, 450]] as const) {
		test(`volet ouvert au clavier à ${largeur} px : aucune violation, aucun débordement, le focus revient`, async ({ page }) => {
			await servirDepartement(page, '44');
			await ouvrir(page, '/departement/44', largeur, hauteur);
			const bouton = page.getByRole('button', { name: 'Détail : Population des communes' });
			await bouton.focus();
			await page.keyboard.press('Enter');
			const volet = page.getByRole('dialog', { name: 'Population des communes' });
			await expect(volet).toBeVisible();
			await expect(volet.getByRole('heading', { name: 'Population des communes' })).toBeFocused();
			// Measured once the 150 ms entrance is over: a fading panel reads as low contrast.
			await volet.evaluate((dialogue) => Promise.all(dialogue.getAnimations().map((animation) => animation.finished)));
			await sansViolationAxe(page, `volet ouvert à ${largeur} px`);
			await sansDebordement(page, `volet ouvert à ${largeur} px`);
			await page.keyboard.press('Escape');
			await expect(volet).toBeHidden();
			await expect(bouton).toBeFocused();
			const cible = (await bouton.boundingBox())!;
			expect(cible.height).toBeGreaterThanOrEqual(44);
			expect(cible.width).toBeGreaterThanOrEqual(44);
		});
	}

	test('zoom 200 % (720 × 450) : tuiles sur deux colonnes, aucun débordement', async ({ page }) => {
		await servirDepartement(page, '44');
		await ouvrir(page, '/departement/44', 720, 450);
		await sansDebordement(page, 'zoom 200 %');
		const colonnes = await page.locator('section.panneau-essentiel dl.cles').evaluate((dl) => getComputedStyle(dl).gridTemplateColumns.split(' ').length);
		expect(colonnes).toBe(2);
		await sansViolationAxe(page, 'zoom 200 %');
	});
});
