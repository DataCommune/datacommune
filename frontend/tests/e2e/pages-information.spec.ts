import AxeBuilder from '@axe-core/playwright';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, expect, type Page, type TestInfo } from '@playwright/test';
import { configureSsrSource } from './fixtures/bien-ssr-source';
import { mockAuthAnonyme } from './fixtures/mocks-matrice';
import { E2E_APP_ORIGIN, E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { verifierGraisses, verifierMesure, verifierPlancherTypo } from './fixtures/quality';

// 04.8-06 — the information pages in their v4 form (UI-SPEC § 6.8, § 6.9; D-13, D-14, D-35).
//
// What this spec locks:
//   - the text of `main` of the six information pages, breadcrumb excluded, whitespace normalised and
//     the test origins replaced by {ORIGINE}, equals the versioned reference taken BEFORE the port
//     (`fixtures/textes-information-reference.json`): the legal texts stay word for word (D-13). A copy
//     with one word altered fails the comparison (red case written below);
//   - none of the mockup sentences enters: « À compléter », « … ne dépose aucun cookie et ne mesure pas l’audience »;
//   - each ported page: `.simple` template, breadcrumb « Accueil › {page} », one `h1`, one `simple` panel
//     per section, and at 1440, 375 and 320 px no axe violation, no overflow, the 14 px floor, the locked
//     weights and the 60ch measure.
//
// Writing the reference: `DC_TEXTES_REFERENCE_WRITE=1 DC_TEXTES_BASE_REVISION=<sha>`, with `src/` equal
// to <sha>. An existing reference is compared, never rewritten.
// Captures go to `DC_INFO_EVIDENCE_DIR` when set, else to the output folder of the run.

const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] as const;
const LARGEURS = [1440, 375, 320] as const;
const CHEMIN_REFERENCE = fileURLToPath(new URL('./fixtures/textes-information-reference.json', import.meta.url));
const ECRITURE = process.env.DC_TEXTES_REFERENCE_WRITE === '1';

interface PageInformation {
	chemin: string;
	titre: string;
	/** Name of the last breadcrumb step. */
	ariane: string;
}

const PAGES: readonly PageInformation[] = [
	{ chemin: '/mentions-legales', titre: 'Mentions légales', ariane: 'Mentions légales' },
	{ chemin: '/cgu', titre: "Conditions Générales d'Utilisation", ariane: 'CGU' },
	{ chemin: '/confidentialite', titre: 'Politique de confidentialité', ariane: 'Confidentialité' },
	{ chemin: '/couverture', titre: 'Couverture et limites', ariane: 'Couverture et limites' },
	{ chemin: '/developpeurs', titre: 'Développeurs', ariane: 'Développeurs' },
	{
		chemin: '/ce-que-nous-ne-montrons-pas',
		titre: 'Ce que nous ne montrons pas, et pourquoi',
		ariane: 'Ce que nous ne montrons pas'
	}
];

interface Reference {
	baseRevision: string;
	capturedAt: string;
	regle: string;
	textes: Record<string, string>;
}

/** Whitespace normalised, both test origins canonised: the reference does not depend on the ports of a run. */
function canoniser(texte: string): string {
	return texte
		.split(E2E_SOURCE_ORIGIN)
		.join('{ORIGINE}')
		.split(E2E_APP_ORIGIN)
		.join('{ORIGINE}')
		.replace(/\s+/g, ' ')
		.trim();
}

/**
 * Rendered text of `main`, the breadcrumb and the decorations hidden from assistive technologies (the
 * arrows of the lists of links, `aria-hidden`) hidden for the read and shown again: the words, not the
 * ornaments. The reference was taken at 4a281019, whose six pages had no `aria-hidden` text.
 */
async function texteMain(page: Page): Promise<string> {
	const brut = await page.evaluate(() => {
		const main = document.querySelector('main');
		if (!main) return '';
		const caches = [
			...main.querySelectorAll<HTMLElement>('nav[aria-label="Fil d\'Ariane"], [aria-hidden="true"]')
		].map((el) => [el, el.style.display] as const);
		for (const [el] of caches) el.style.display = 'none';
		const texte = main.innerText;
		for (const [el, avant] of caches) el.style.display = avant;
		return texte;
	});
	return canoniser(brut);
}

function lireReference(): Reference | null {
	return existsSync(CHEMIN_REFERENCE) ? (JSON.parse(readFileSync(CHEMIN_REFERENCE, 'utf8')) as Reference) : null;
}

async function ouvrir(page: Page, chemin: string, largeur = 1440) {
	await mockAuthAnonyme(page);
	await page.setViewportSize({ width: largeur, height: 900 });
	await page.goto(chemin);
	await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
	await page.evaluate(() => document.fonts.ready.then(() => undefined));
}

function dossierPreuves(testInfo: TestInfo): string {
	const dossier = process.env.DC_INFO_EVIDENCE_DIR || testInfo.outputPath('preuves');
	mkdirSync(dossier, { recursive: true });
	return dossier;
}

test.describe('textes des pages d’information, mot pour mot (D-13)', () => {
	test('référence : écriture, ou présence des six pages', async ({ page }) => {
		if (ECRITURE) {
			if (lireReference()) throw new Error('La référence existe : elle ne se réécrit jamais.');
			const baseRevision = process.env.DC_TEXTES_BASE_REVISION;
			if (!baseRevision) throw new Error('DC_TEXTES_BASE_REVISION est requis pour écrire la référence.');
			const textes: Record<string, string> = {};
			for (const { chemin } of PAGES) {
				await ouvrir(page, chemin);
				textes[chemin] = await texteMain(page);
			}
			const reference: Reference = {
				baseRevision,
				capturedAt: new Date().toISOString(),
				regle:
					'Texte de main hors fil d’Ariane (innerText), espaces normalisés, origines de test remplacées par {ORIGINE}, ' +
					'pris avant le portage du plan 04.8-06. Ne se réécrit jamais ; un changement de texte voulu se décide hors de ce plan (D-13).',
				textes
			};
			writeFileSync(CHEMIN_REFERENCE, `${JSON.stringify(reference, null, '\t')}\n`, 'utf8');
		}
		const reference = lireReference();
		expect(reference, 'référence versionnée absente').not.toBeNull();
		expect(Object.keys(reference!.textes).sort()).toEqual(PAGES.map((p) => p.chemin).sort());
		for (const texte of Object.values(reference!.textes)) expect(texte.length).toBeGreaterThan(200);
	});

	test('cas rouge : un mot altéré dans la page rendue fait échouer la comparaison', async ({ page }) => {
		const reference = lireReference()!;
		await ouvrir(page, '/mentions-legales');
		expect(await texteMain(page)).toBe(reference.textes['/mentions-legales']);
		// One word changed in the rendered DOM: the same read no longer equals the reference.
		await page.evaluate(() => {
			const marcheur = document.createTreeWalker(document.querySelector('main')!, NodeFilter.SHOW_TEXT);
			for (let n = marcheur.nextNode(); n; n = marcheur.nextNode()) {
				if (n.textContent?.includes('Directeur de la publication')) {
					n.textContent = n.textContent.replace('Directeur', 'Directrice');
					return;
				}
			}
			throw new Error('texte à altérer introuvable');
		});
		expect(await texteMain(page)).not.toBe(reference.textes['/mentions-legales']);
	});

	for (const { chemin } of PAGES) {
		test(`${chemin} : texte égal à la référence`, async ({ page }) => {
			const reference = lireReference()!;
			await ouvrir(page, chemin);
			expect(await texteMain(page)).toBe(reference.textes[chemin]);
		});
	}

	test('aucun texte de la maquette n’entre (D-13)', async ({ page }) => {
		for (const { chemin } of PAGES) {
			await ouvrir(page, chemin);
			const texte = await texteMain(page);
			// The mockup sentences, not the words: the true privacy policy does say « ne dépose aucun cookie »
			// of the audience measurement, which is correct (04.7-14b).
			expect(texte, chemin).not.toMatch(/À compléter|ne demande aucun compte, ne dépose aucun cookie|ne mesure pas l['’]audience/);
		}
	});
});

/** The v4 form of a simple page: template, breadcrumb, one h1 at 44/32 px, sections as simple panels. */
async function verifierForme(page: Page, info: PageInformation, largeur: number) {
	await expect(page.locator('main .public-article')).toHaveCount(0);
	const simple = page.locator('main .simple');
	await expect(simple).toHaveCount(1);
	expect(await simple.evaluate((el) => getComputedStyle(el).maxWidth)).toBe('1240px');
	const ariane = page.getByRole('navigation', { name: "Fil d'Ariane" });
	await expect(ariane.getByRole('link', { name: 'Accueil' })).toHaveAttribute('href', '/');
	await expect(ariane.locator('[aria-current="page"]')).toHaveText(info.ariane);
	await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);
	const taille = await page.getByRole('heading', { level: 1 }).evaluate((el) => getComputedStyle(el).fontSize);
	expect(taille).toBe(largeur > 720 ? '44px' : '32px');
	// Every h2 of the page opens a simple panel.
	const h2 = page.locator('main h2');
	const panneaux = page.locator('main section.panneau-simple > h2');
	expect(await h2.count()).toBeGreaterThan(0);
	expect(await panneaux.count()).toBe(await h2.count());
}

async function verifierQualite(page: Page, largeur: number, libelle: string) {
	const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
	expect(axe.violations.map((v) => `${v.id}: ${v.nodes.length}`), `axe ${libelle}`).toEqual([]);
	const disposition = await page.evaluate(() => ({ largeur: innerWidth, defilement: document.documentElement.scrollWidth }));
	expect(disposition.defilement, `débordement ${libelle}`).toBeLessThanOrEqual(disposition.largeur);
	expect(await verifierPlancherTypo(page, 'main'), `plancher ${libelle}`).toEqual([]);
	expect(await verifierGraisses(page, 'main'), `graisses ${libelle}`).toEqual([]);
	expect(await verifierMesure(page, 'main', ['p', 'ul.puces > li', 'dd']), `mesure ${libelle}`).toEqual([]);
}

test.describe('forme v4 des pages d’information (UI-SPEC § 6.9)', () => {
	for (const info of PAGES) {
		test(`${info.chemin} : gabarit simple et qualité à 1440, 375 et 320 px`, async ({ page }, testInfo) => {
			const dossier = dossierPreuves(testInfo);
			for (const largeur of LARGEURS) {
				await ouvrir(page, info.chemin, largeur);
				await verifierForme(page, info, largeur);
				await verifierQualite(page, largeur, `${info.chemin} à ${largeur} px`);
				if (largeur !== 320) {
					await page.screenshot({
						path: join(dossier, `${info.chemin.slice(1)}-${largeur}.png`),
						fullPage: true,
						animations: 'disabled'
					});
				}
			}
		});
	}

	test('/mentions-legales : éditeur, directeur et contact en dl.tab, mêmes valeurs', async ({ page }) => {
		await ouvrir(page, '/mentions-legales');
		const lignes = page.locator('main dl.tab > div.tab-l');
		await expect(lignes).toHaveCount(2);
		await expect(lignes.nth(0).locator('dt')).toHaveText(/^Email/);
		await expect(lignes.nth(0).locator('dd a')).toHaveAttribute('href', 'mailto:contact@datacommune.fr');
		await expect(lignes.nth(1).locator('dt')).toHaveText(/^Directeur de la publication/);
		await expect(lignes.nth(1).locator('dd')).toHaveText('Ianis P., éditeur du site.');
		// The value in the value role of § 3.13: Newsreader 20 px, 500.
		const style = await lignes.nth(1).locator('dd').evaluate((el) => {
			const s = getComputedStyle(el);
			return [s.fontFamily.split(',')[0].replace(/["']/g, ''), s.fontSize, s.fontWeight];
		});
		expect(style).toEqual(['Newsreader', '20px', '500']);
		// The associated documents are whole-row links, 44 px high at 375 px.
		await page.setViewportSize({ width: 375, height: 800 });
		for (const lien of await page.locator('main .liste-liens a').all()) {
			expect((await lien.boundingBox())!.height).toBeGreaterThanOrEqual(44);
		}
	});

	test('/ce-que-nous-ne-montrons-pas : chaque limite en ligne de définition, tête en 600 ; #etats gardé', async ({ page }) => {
		for (const largeur of [1440, 375] as const) {
			await ouvrir(page, '/ce-que-nous-ne-montrons-pas', largeur);
			const lignes = page.locator('main ul.def-l > li.ligne');
			expect(await lignes.count()).toBeGreaterThanOrEqual(4);
			const premiere = lignes.first().locator('.def');
			const colonnes = await premiere.evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(' ').length);
			expect(colonnes, `colonnes à ${largeur} px`).toBe(largeur > 720 ? 2 : 1);
			expect(await lignes.first().locator('h3').evaluate((el) => getComputedStyle(el).fontWeight)).toBe('600');
			// Each limit keeps its anchor, and the states their own.
			for (const id of await lignes.locator('article').evaluateAll((els) => els.map((el) => el.id))) expect(id).not.toBe('');
			const etats = page.locator('main #etats');
			await expect(etats).toHaveCount(1);
			await expect(etats.getByRole('heading', { level: 2 })).toHaveText('Quand une valeur manque');
			expect(await etats.locator('dt').count()).toBeGreaterThanOrEqual(5);
		}
	});

	test('/developpeurs : la commande passe à la ligne dans une enveloppe nommée, sans défilement ni débordement à 320 px', async ({ page }) => {
		await ouvrir(page, '/developpeurs', 320);
		const enveloppe = page.getByRole('region', { name: 'Exemple de requête curl' });
		// I7 (2026-11-10, option A): the command wraps; nothing scrolls, so the wrapper is not a keyboard stop.
		await expect(enveloppe).not.toHaveAttribute('tabindex', /.*/);
		await expect(enveloppe.locator('pre')).toHaveCount(1);
		expect(await enveloppe.evaluate((el) => getComputedStyle(el).overflowX)).not.toBe('auto');
		expect(await enveloppe.locator('pre').evaluate((el) => getComputedStyle(el).fontSize)).toBe('14px');
		expect(await enveloppe.locator('pre').evaluate((el) => getComputedStyle(el).whiteSpace)).toBe('pre-wrap');
		expect(await enveloppe.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
		const disposition = await page.evaluate(() => ({ largeur: innerWidth, defilement: document.documentElement.scrollWidth }));
		expect(disposition.defilement).toBeLessThanOrEqual(disposition.largeur);
		// Inline code takes the size around it, on the soft background.
		const enLigne = page.locator('main p code').first();
		expect(await enLigne.evaluate((el) => getComputedStyle(el).fontSize)).toBe(
			await enLigne.evaluate((el) => getComputedStyle(el.parentElement!).fontSize)
		);
	});
});

// --- /datasets (UI-SPEC § 6.8, D-14) and the footer link ------------------------------------------------

const DATASETS_API = JSON.parse(
	readFileSync(new URL('../../src/lib/data/__fixtures__/datasets-api.json', import.meta.url), 'utf8')
) as { donnees: { nom: string; provenance: { nom: string } | null; hebergement: unknown }[] };

async function ouvrirDatasets(page: Page, largeur = 1440, chemin = '/datasets') {
	const source = await configureSsrSource(page.request, [
		{ method: 'GET', pathname: '/v1/datasets', query: {}, minCalls: 1, maxCalls: 1, responses: [{ status: 200, body: DATASETS_API }] }
	]);
	await ouvrir(page, chemin, largeur);
	await source.assert();
}

test.describe('/datasets : catalogue servi et « Lire une fiche » en #lire (D-14)', () => {
	test('fil, h1, panneaux dans l’ordre, légende complète en #lire', async ({ page }) => {
		await ouvrirDatasets(page);
		const ariane = page.getByRole('navigation', { name: "Fil d'Ariane" });
		await expect(ariane.locator('[aria-current="page"]')).toHaveText('Données & méthode');
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('Données & méthode');
		await expect(page.locator('main section.panneau > h2')).toHaveText([
			// Review of 2026-11-11: three panels, the four states inside « Lire une fiche ».
			'Les sources',
			'Lire une fiche',
			'Couverture'
		]);
		const lire = page.locator('#lire');
		const [echelles, etats] = [lire.locator('ul.lignes').nth(0), lire.locator('ul.lignes').nth(1)];
		// One line per served level of the scale table (9, UI-SPEC § 3.5) and one per key of the badges.
		await expect(echelles.locator(':scope > li')).toHaveCount(9);
		expect(await etats.locator(':scope > li').count()).toBeGreaterThanOrEqual(9);
		for (const ligne of await lire.locator('ul.lignes > li').all()) await expect(ligne.locator('p')).not.toBeEmpty();
	});

	test('la liste des jeux est celle que sert /v1/datasets, aucune source écrite dans le gabarit', async ({ page }) => {
		await ouvrirDatasets(page);
		const servis = DATASETS_API.donnees.filter((jeu) => jeu.hebergement).map((jeu) => jeu.provenance?.nom ?? jeu.nom);
		const affiches = await page.locator('.methode-cards .dataset-ligne-nom').allTextContents();
		expect(affiches.length).toBeGreaterThan(10);
		expect(affiches.sort()).toEqual([...servis].sort());
		// The example of the search field (`placeholder`) names a producer on purpose; it is no list.
		const gabarit = readFileSync(new URL('../../src/routes/(marketing)/datasets/+page.svelte', import.meta.url), 'utf8').replace(
			/placeholder="[^"]*"/g,
			''
		);
		for (const nom of servis) expect(gabarit, nom).not.toContain(nom);
	});

	test('qualité à 1440, 375 et 320 px', async ({ page }, testInfo) => {
		const dossier = dossierPreuves(testInfo);
		for (const largeur of LARGEURS) {
			await ouvrirDatasets(page, largeur);
			expect(await page.locator('main .simple').evaluate((el) => getComputedStyle(el).maxWidth)).toBe('1240px');
			await verifierQualite(page, largeur, `/datasets à ${largeur} px`);
			if (largeur !== 320) {
				await page.screenshot({ path: join(dossier, `datasets-${largeur}.png`), fullPage: true, animations: 'disabled' });
			}
		}
	});

	test('le pied a dix liens, « Lire une fiche » en deuxième, qui mène à #lire', async ({ page }) => {
		await ouvrir(page, '/mentions-legales');
		const pied = page.getByRole('contentinfo').getByRole('link');
		await expect(pied).toHaveCount(10);
		await expect(pied.nth(1)).toHaveText('Lire une fiche');
		await expect(pied.nth(1)).toHaveAttribute('href', '/datasets#lire');
		const source = await configureSsrSource(page.request, [
			{ method: 'GET', pathname: '/v1/datasets', query: {}, minCalls: 1, maxCalls: 1, responses: [{ status: 200, body: DATASETS_API }] }
		]);
		await pied.nth(1).click();
		await expect(page).toHaveURL(/\/datasets#lire$/);
		await expect(page.locator('#lire').getByRole('heading', { level: 2, name: 'Lire une fiche' })).toBeInViewport();
		await source.assert();
	});
});

// Captures of the seven surfaces at 1440 and 375 px, before and after the port (out of the repository):
// `DC_INFO_EVIDENCE_DIR=<folder>` sends them out of the output folder of the run.
test.describe('captures des sept surfaces', () => {
	test('captures 1440 et 375 px', async ({ page }, testInfo) => {
		const dossier = dossierPreuves(testInfo);
		for (const chemin of [...PAGES.map((p) => p.chemin), '/datasets']) {
			for (const largeur of [1440, 375]) {
				if (chemin === '/datasets') await ouvrirDatasets(page, largeur);
				else await ouvrir(page, chemin, largeur);
				await page.screenshot({ path: join(dossier, `${chemin.slice(1)}-${largeur}.png`), fullPage: true, animations: 'disabled' });
			}
		}
	});
});
