// The address fiche (plan 04.8-03, UI-SPEC v4 §§ 2.5, 3.3-3.12, 4, 7, 8; reading of 06/11, CADRAGE R1 to R10),
// on the fixture of 43 themes (`fixtures/rapport-43-themes.json`: one theme per entry of the registry of
// the API, five of them in outage, built by `fixtures/aligner-rapports.py`). Headless runner, isolated
// context, every off-local request refused (AGENTS.md).
//
// What this spec locks:
//   - the 43 anchors `#critere-{id}` are there, distinct: asserted before any capture;
//   - the arrival answers; above the threshold of two columns a sticky summary and four open cards, under it a
//     compact summary and closed categories, a closed one on the first screen; an anchor opens its category,
//     nothing stretches, the keyboard and a page without JavaScript reach everything, paper shows everything;
//   - at 1440, 375 and 320 px: axe (WCAG 2.2 AA) without violation with the details closed, then open; no
//     overflow, not even under the text spacing of WCAG 1.4.12; the 14 px floor, the locked weights and the
//     60ch measure; no error of the page or of its console;
//   - one `h1`, an outline without a skipped level, every `#…` link of the page with its target; a 200 % zoom
//     (1440 × 900 emulated as 720 × 450 CSS px) lays out as a phone; the A4 print opens every detail, drops
//     the navigation and keeps the IGN licence line of the map;
//   - the states: a long commune name and a 140-character verdict at 320 px, an address out of coverage, a
//     report without summary, a theme in outage, a theme the front does not place, a map in error;
//   - D-40: the sentence of a category says « toute la commune » only when its wider rows are all communal.
// Backstop (no threshold, a human reads the captures): `forced-colors: active`, and the captures at 1440 and
// 375 px, kept outside the repository (`DC_FICHE_EVIDENCE_DIR` when set, else the output folder of the run).
import { readFileSync } from 'node:fs';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import AxeBuilder from '@axe-core/playwright';
import { test, expect, type APIRequestContext, type Page, type TestInfo } from '@playwright/test';
import { attendreAucuneCarteNonReconnue, repondreCarteStatique } from './fixtures/carte-parcelles';
import { expectNoUnexpectedOffLocalRequest, installLocalNetworkGuard } from './fixtures/local-network';
import { E2E_APP_ORIGIN, E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { cheminRapport, REQUETE_RAPPORT } from './fixtures/rapport';
import { verifierGraisses, verifierMesure, verifierPlancherTypo } from './fixtures/quality';

interface Theme {
	id: string;
	titre: string;
	verdict: string;
	granularite: { niveau: string };
	[cle: string]: unknown;
}
interface Entree {
	slug: string;
	banId: string;
	rapport: { donnees: { adresse: { libelle: string; commune: { code_insee: string; nom: string } }; resume: Record<string, unknown>; couverture: unknown; themes: Theme[] } };
	documents: unknown;
	ventes: unknown;
	ventesAdresse: unknown;
}

const BASE = JSON.parse(readFileSync(new URL('./fixtures/rapport-43-themes.json', import.meta.url), 'utf8')) as Entree;
const IDS_SERVIS = BASE.rapport.donnees.themes.map((t) => t.id).sort();
const NIVEAU_DE = new Map(BASE.rapport.donnees.themes.map((t) => [t.id, t.granularite.niveau]));
const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] as const;
const LARGEURS = [1440, 375, 320] as const;
const LECTURES = 12;
const NBSP = String.fromCharCode(0xa0);

const clone = (): Entree => structuredClone(BASE);
const repete = (body: unknown) => Array.from({ length: LECTURES }, () => ({ status: 200, body }));
const attente = (pathname: string, query: Record<string, string>, body: unknown) => ({
	method: 'GET',
	pathname,
	query,
	minCalls: 0,
	maxCalls: LECTURES,
	responses: repete(body)
});

async function configurer(request: APIRequestContext, a: Entree) {
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
		data: {
			reset: true,
			expectations: [
				attente(cheminRapport(a.banId), REQUETE_RAPPORT, a.rapport),
				attente(`/v1/adresse/${a.banId}/dpe`, { page: '1' }, a.documents),
				attente(`/v1/adresse/${a.banId}/ventes`, { page: '1' }, a.ventes),
				attente(`/v1/adresse/${a.banId}/ventes-declarees`, { page: '1' }, a.ventesAdresse)
			]
		}
	});
	expect(res.ok()).toBe(true);
}

/** Serves `a`, opens its fiche and waits for the hydration (the account zone is read). */
async function ouvrir(page: Page, request: APIRequestContext, a: Entree, largeur: number, hauteur = 900, titre: RegExp | null = /Essais/) {
	await configurer(request, a);
	await page.setViewportSize({ width: largeur, height: hauteur });
	const hydratee = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
	await page.goto(`/adresse/${a.banId}`);
	await hydratee;
	await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
	if (titre) await expect(page.getByRole('heading', { level: 1 })).toHaveText(titre);
}

/** The 43 anchors, distinct, equal to the served ids: asserted before any capture of the fiche. */
async function verifier43Ancres(page: Page) {
	const ids = await page.locator('li[id^="critere-"]').evaluateAll((els) => els.map((e) => e.id.replace(/^critere-/, '')));
	expect(new Set(ids).size, 'ancres #critere-{id} distinctes').toBe(ids.length);
	expect(ids.sort(), 'les 43 critères du registre').toEqual(IDS_SERVIS);
	expect(ids).toHaveLength(43);
}

function collecterErreurs(page: Page): string[] {
	const erreurs: string[] = [];
	page.on('pageerror', (err) => erreurs.push(`pageerror: ${err.message}`));
	// The browser's own line for a refused session read (401) or a refused image (503 of the map) is not one.
	page.on('console', (message) => {
		if (message.type() === 'error' && !message.text().startsWith('Failed to load resource')) erreurs.push(`console: ${message.text()}`);
	});
	return erreurs;
}

function preuve(testInfo: TestInfo, nom: string): string {
	const dossier = process.env.DC_FICHE_EVIDENCE_DIR;
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

/** Every fold of the fiche, categories and details of the rows. */
const deplier = async (page: Page, ouvert: boolean) => {
	await page.evaluate((o) => {
		document.querySelectorAll('.adresse details').forEach((d) => ((d as HTMLDetailsElement).open = o));
	}, ouvert);
};

/** Opens the category of a criterion, as a reader does. */
const ouvrirCategorie = (page: Page, id: string) => page.locator(`details#${id} > summary`).click();

/** WCAG 1.4.12: line height 1.5, letter spacing 0.12em, word spacing 0.16em, paragraph spacing 2em. */
const ESPACEMENT_DU_TEXTE = '*{line-height:1.5 !important;letter-spacing:0.12em !important;word-spacing:0.16em !important}p{margin-bottom:2em !important}';

// The map of the head comes from our own proxy under `/carte/`: its two pictures are a grey 448 × 448 PNG
// here, or the 503 of a failing IGN (`repondreCarteStatique`, strict pattern: any other map path fails).
test.beforeEach(async ({ page }) => {
	await installLocalNetworkGuard(page);
	await repondreCarteStatique(page);
});

test.afterEach(async ({ page }) => {
	attendreAucuneCarteNonReconnue(page);
});

for (const largeur of LARGEURS) {
	test(`${largeur} px : 43 critères, axe fermé puis ouvert, débordement, espacement du texte, plancher, graisses, mesure`, async ({ page, request }, testInfo) => {
		test.setTimeout(120_000);
		const erreurs = collecterErreurs(page);
		await ouvrir(page, request, clone(), largeur, largeur >= 1024 ? 900 : 800);
		// Before any capture: every theme of the registry has its anchor, once.
		await verifier43Ancres(page);
		await expect(page.getByRole('navigation', { name: 'Sur cette fiche' })).toBeAttached();

		await sansViolationAxe(page, `${largeur} px, détails fermés`);
		await sansDebordement(page, `${largeur} px`);
		if (largeur !== 320) {
			await page.screenshot({ path: preuve(testInfo, `fiche-43-${largeur}-ecran.jpg`), quality: 70, animations: 'disabled' });
			await page.screenshot({ path: preuve(testInfo, `fiche-43-${largeur}-page.jpg`), quality: 70, fullPage: true, animations: 'disabled' });
		}

		await deplier(page, true);
		await sansViolationAxe(page, `${largeur} px, détails ouverts`);
		await sansDebordement(page, `${largeur} px, tout déplié`);
		// Soft: a finding of one check must not hide the others.
		expect.soft(await verifierPlancherTypo(page, '.adresse'), `plancher de 14 px à ${largeur} px`).toEqual([]);
		expect.soft(await verifierGraisses(page, '.adresse'), `graisses à ${largeur} px`).toEqual([]);
		// 4.9 harmonisation: the identity line is metadata, it takes the width of the head, not the 60ch of a text.
		expect.soft(await verifierMesure(page, '.adresse', ['p:not(.adresse-identity)']), `mesure à ${largeur} px`).toEqual([]);

		await page.addStyleTag({ content: ESPACEMENT_DU_TEXTE });
		await sansDebordement(page, `${largeur} px, espacement du texte WCAG 1.4.12`);
		await deplier(page, false);
		await sansDebordement(page, `${largeur} px, espacement du texte, fermé`);

		expect(erreurs).toEqual([]);
		expectNoUnexpectedOffLocalRequest(page);
	});
}

test('plan des titres : un seul h1, aucun saut de niveau, chaque ancre de la page a sa cible', async ({ page, request }) => {
	await ouvrir(page, request, clone(), 1440);
	for (const ouvert of [false, true]) {
		await deplier(page, ouvert);
		const niveaux = await page.evaluate(() =>
			[...document.querySelectorAll('h1, h2, h3, h4, h5, h6')]
				.filter((h) => h.checkVisibility({ checkOpacity: false, checkVisibilityCSS: true }))
				.map((h) => ({ niveau: Number(h.tagName.slice(1)), texte: (h.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 40) }))
		);
		expect(niveaux.filter((h) => h.niveau === 1), 'un seul h1').toHaveLength(1);
		expect(niveaux[0].niveau, 'le premier titre est le h1').toBe(1);
		let precedent = 0;
		for (const h of niveaux) {
			expect(h.niveau, `saut de niveau avant « ${h.texte} » (après un h${precedent}, détails ${ouvert ? 'ouverts' : 'fermés'})`).toBeLessThanOrEqual(precedent + 1);
			precedent = h.niveau;
		}
		// The deepest level of the fiche is the h5 of a criterion under the title of a group.
		expect(Math.max(...niveaux.map((h) => h.niveau))).toBeLessThanOrEqual(5);
	}
	const sansCible = await page.evaluate(() =>
		[...document.querySelectorAll<HTMLAnchorElement>('a[href^="#"]')]
			.map((a) => a.getAttribute('href')!)
			.filter((href) => href.length > 1 && !document.getElementById(decodeURIComponent(href.slice(1))))
	);
	expect(sansCible, 'ancres sans cible').toEqual([]);
	// The anchors of the arrival are really followed: the target is a criterion.
	const liens = await page.locator('section.bref a[href^="#critere-"]').evaluateAll((a) => a.map((x) => x.getAttribute('href')));
	expect(liens.length).toBeGreaterThan(0);
	for (const href of liens) expect(IDS_SERVIS).toContain(href!.replace('#critere-', ''));
});

test('zoom 200 % (1440 × 900 émulé en 720 × 450) : mise en page ≤ 720 px, une colonne, aucun débordement', async ({ page, request }, testInfo) => {
	const erreurs = collecterErreurs(page);
	await ouvrir(page, request, clone(), 720, 450);
	await verifier43Ancres(page);
	await sansDebordement(page, 'zoom 200 %');
	// The page is one column: the categories start under the answer, not beside it.
	const reponse = (await page.locator('section#bref').boundingBox())!;
	const liste = (await page.locator('.lecture').boundingBox())!;
	expect(liste.y, 'les catégories passent sous la réponse').toBeGreaterThanOrEqual(reponse.y + reponse.height - 1);
	await deplier(page, true);
	await sansDebordement(page, 'zoom 200 %, tout déplié');
	await sansViolationAxe(page, 'zoom 200 %');
	await page.screenshot({ path: preuve(testInfo, 'fiche-43-zoom200.jpg'), quality: 70, animations: 'disabled' });
	expect(erreurs).toEqual([]);
});

test('A7 : la légende est repliée, ouverte par #lire, écrite sur le papier', async ({ page, request }) => {
	await ouvrir(page, request, clone(), 1440);
	const repli = page.locator('#lire details.repli');
	await expect(repli).not.toHaveAttribute('open');
	await expect(page.locator('#lire .lire-impression')).toBeHidden();
	await page.locator('nav.sommaire-large a[href="#lire"]').click();
	await expect(repli).toHaveAttribute('open');
	await expect(repli.getByRole('heading', { name: 'Les échelles' })).toBeVisible();
	await page.reload();
	await expect(page.locator('#lire details.repli')).toHaveAttribute('open');
	await page.emulateMedia({ media: 'print' });
	await expect(page.locator('#lire .lire-impression').getByRole('heading', { name: 'Les états' })).toBeVisible();
	await expect(repli).toBeHidden();
});

test('impression A4 : détails ouverts, navigation absente, ligne de licence IGN présente', async ({ page, request }, testInfo) => {
	const entry = clone();
	// Keep all six API columns present: the base example has null names and speeds throughout.
	const rail = entry.rapport.donnees.themes.find((theme) => theme.id === 'voie_ferree')!;
	const railDetails = rail.details as { voies: { nom: string | null; vitesse_maximale_kmh: number | null }[] };
	railDetails.voies[0].nom = 'Synthetic rail line';
	railDetails.voies[0].vitesse_maximale_kmh = 160;
	await ouvrir(page, request, entry, 1024);
	await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
	// A4 content width at 96 CSS px/in, with the same 14 mm side margins as the PDF below.
	await page.setViewportSize({ width: Math.floor((210 - 28) * 96 / 25.4), height: 1000 });
	await page.emulateMedia({ media: 'print' });
	expect(await page.locator('.adresse details:not([open])').count(), 'aucun détail fermé à l’impression').toBe(0);
	await expect(page.locator('#critere-radon .ligne-phrase')).toBeVisible();
	await expect(page.getByRole('navigation', { name: 'Sur cette fiche' })).toBeHidden();
	await expect(page.locator('.adresse-actions')).toBeHidden();
	const navigationsVisibles = await page.evaluate(() =>
		[...document.querySelectorAll('nav')].filter((n) => n.checkVisibility({ checkOpacity: false, checkVisibilityCSS: true })).map((n) => n.getAttribute('aria-label') ?? n.className)
	);
	// The breadcrumb is part of the document title block; no menu, no table of contents on paper.
	expect(navigationsVisibles.filter((n) => n !== "Fil d'Ariane"), 'navigation imprimée').toEqual([]);
	// The map prints with its licence line once both images are loaded.
	await expect.poll(() => page.locator('.adresse-carte img').evaluateAll((imgs) => imgs.map((i) => (i as HTMLImageElement).naturalWidth))).toEqual([448, 448]);
	const licence = page.locator('.adresse-carte figure.carte figcaption');
	await expect(licence).toBeVisible();
	await expect(licence).toContainText('© IGN');
	await expect(licence).toContainText('Licence Ouverte 2.0');
	const railHeaders = page.locator('#critere-voie_ferree table thead th');
	await expect(railHeaders).toHaveCount(6);
	const printHeaderOverflows = await railHeaders.evaluateAll((headers) => headers
		.filter((header) => header.scrollWidth > header.clientWidth + 1)
		.map((header) => header.textContent));
	expect.soft(printHeaderOverflows, 'A4 table headers stay inside their own columns').toEqual([]);
	await page.pdf({ path: preuve(testInfo, 'fiche-43-impression.pdf'), format: 'A4', margin: { top: '14mm', bottom: '14mm', left: '14mm', right: '14mm' } });
	await page.emulateMedia({ media: 'screen' });
	await page.evaluate(() => window.dispatchEvent(new Event('afterprint')));
	expect(await page.locator('.adresse details:not([open])').count(), 'les détails reprennent leur état').toBeGreaterThan(0);
});

test('D-40 : « toute la commune » seulement pour une catégorie dont les lignes plus larges sont toutes communales', async ({ page, request }) => {
	await ouvrir(page, request, clone(), 1440);
	const categories = await page.locator('details.cat').evaluateAll((els) =>
		els.map((cat) => ({
			id: cat.id,
			// Spaces and line breaks of the markup collapse; the no-break spaces of the title stay.
			titre: (cat.querySelector('p.com-t')?.textContent ?? '').replace(/[ \t\r\n]+/g, ' ').trim(),
			phrases: cat.querySelectorAll('p.com-t').length,
			larges: [...cat.querySelectorAll('li.is-commune')].map((li) => li.id.replace(/^critere-/, ''))
		}))
	);
	const TOUTE = `Pour toute la commune de Nantes${NBSP}: ne dit pas si l’adresse est concernée`;
	const LARGE = `Pour un périmètre plus large que l’adresse${NBSP}: ne dit pas si l’adresse est concernée`;
	let communaux = 0;
	let larges = 0;
	for (const c of categories) {
		expect(c.phrases, `${c.id} : une phrase au plus`).toBe(c.larges.length ? 1 : 0);
		if (!c.larges.length) continue;
		const toutCommunal = c.larges.every((id) => NIVEAU_DE.get(id) === 'commune');
		// The exact characters, the no-break space before the colon included.
		expect(c.titre).toBe(toutCommunal ? TOUTE : LARGE);
		if (toutCommunal) communaux += 1;
		else larges += 1;
	}
	expect(communaux + larges, 'la fixture a des lignes plus larges que l’adresse').toBeGreaterThan(0);
	// Every row says its own scale beside its value.
	await deplier(page, true);
	await expect(page.locator('details.cat li[id^="critere-"] .ligne-ech .ech-mot:visible')).toHaveCount(43);
});

test.describe('états de la fiche', () => {
	test('A3/A4 : la vigilance intercommunale élargit le titre, la phrase et le chapô', async ({ page, request }) => {
		const a = clone();
		const radon = a.rapport.donnees.themes.find((t) => t.id === 'radon')!;
		radon.granularite.niveau = 'epci';
		// Keep the served list and its theme consistent; no summary is recalculated in the page.
		const points = a.rapport.donnees.resume.points_commune as { theme: string; granularite: { niveau: string } }[];
		points.find((p) => p.theme === 'radon')!.granularite.niveau = 'epci';
		await ouvrir(page, request, a, 1440);
		await verifier43Ancres(page);
		await expect(page.locator('#q-risques > summary')).toContainText('pour un périmètre plus large que l’adresse');
		await ouvrirCategorie(page, 'q-risques');
		await expect(page.locator('#q-risques').getByRole('heading', { level: 4, name: 'Vigilances pour un périmètre plus large que l’adresse', exact: true })).toBeVisible();
		await expect(page.locator('#q-risques .bref-p')).toContainText('pour un périmètre plus large que l’adresse');
		await expect(page.locator('#critere-radon .ligne-ech .ech-mot')).toHaveText('Intercommunalité');
	});

	test('D-33 : le h1 est le numéro, le suffixe et la voie lisible servis, repli sur le libellé servi', async ({ page, request }) => {
		const a = clone();
		const adresse = a.rapport.donnees.adresse as Record<string, unknown>;
		adresse.numero = '12';
		adresse.rep = 'bis';
		adresse.nom_voie = 'Chemin Puits Fictif (du)';
		await ouvrir(page, request, a, 1440, 900, /^12 bis chemin du Puits Fictif$/);
		await expect(page.locator('.adresse-identity')).toContainText('44000 Nantes');
		// The identity is undetermined (no number): the served label is the title.
		const b = clone();
		(b.rapport.donnees.adresse as Record<string, unknown>).numero = null;
		await ouvrir(page, request, b, 1440, 900, null);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(b.rapport.donnees.adresse.libelle);
	});

	test('commune au nom long et verdict de 140 caractères à 320 px : aucun débordement', async ({ page, request }) => {
		const a = clone();
		const nom = 'Saint-Martin-de-Fontenay-sur-Mer-et-Autres-Lieux-Réunis-en-Commune-Nouvelle';
		a.rapport.donnees.adresse.commune.nom = nom;
		const verdict = `${'Une vigilance dont le verdict long est servi tel quel par le serveur, sans troncature, '.repeat(2)}fin.`.slice(0, 140);
		expect(verdict).toHaveLength(140);
		a.rapport.donnees.themes.find((t) => t.id === 'grand_axe')!.verdict = verdict;
		const erreurs = collecterErreurs(page);
		await ouvrir(page, request, a, 320, 800);
		await verifier43Ancres(page);
		await expect(page.locator('.adresse-identity')).toContainText(nom);
		await expect(page.locator('#critere-grand_axe .ligne-phrase')).toHaveText(verdict);
		await sansDebordement(page, 'nom long et verdict de 140 caractères, 320 px');
		await deplier(page, true);
		await sansDebordement(page, 'nom long et verdict de 140 caractères, tout déplié');
		await sansViolationAxe(page, 'nom long à 320 px');
		expect(erreurs).toEqual([]);
	});

	test('adresse hors couverture : le panneau d’état, aucune partie, aucune valeur', async ({ page, request }) => {
		const a = clone();
		a.rapport.donnees.couverture = { etat: 'hors_couverture', motif: 'territoire_hors_perimetre' };
		a.rapport.donnees.themes = [];
		a.rapport.donnees.resume = { points_adresse: [], points_commune: [], rassurants: [], chiffres_cles: [] };
		// The title of an address out of coverage stays the generic one (only a served address is named).
		await ouvrir(page, request, a, 1440, 900, /^Adresse à Nantes/);
		const notice = page.locator('.notice.notice-warn');
		await expect(notice.getByRole('heading', { level: 2 })).toHaveText('Adresse hors couverture');
		await expect(notice).toContainText('Une absence de couverture ne vaut pas zéro.');
		await expect(page.locator('section.bref')).toHaveCount(0);
		await expect(page.locator('details.cat')).toHaveCount(0);
		await expect(page.locator('li[id^="critere-"]')).toHaveCount(0);
		await expect(page.getByRole('navigation', { name: 'Sur cette fiche' })).toHaveCount(0);
		await sansDebordement(page, 'hors couverture');
		await sansViolationAxe(page, 'hors couverture');
	});

	test('rapport sans résumé : « Le résumé de cette fiche n’est pas disponible. », rien n’est calculé', async ({ page, request }) => {
		const a = clone();
		delete a.rapport.donnees.resume.lus;
		await ouvrir(page, request, a, 1440);
		await verifier43Ancres(page);
		const bref = page.locator('section.bref');
		await expect(bref.locator('.bref-indisponible')).toHaveText('Le résumé de cette fiche n’est pas disponible.');
		await expect(page.locator('.bref-p')).toHaveCount(0);
		await expect(bref.locator('.bref-l')).toHaveCount(0);
		// No count on a closed row: a category counts nothing the API did not serve.
		await expect(page.locator('.cat-signaux')).toHaveCount(0);
		await expect(page.locator('details.cat')).toHaveCount(4);
		await sansViolationAxe(page, 'sans résumé');
	});

	test('thème en panne : dit sur la ligne fermée de sa catégorie, sa ligne reste, badge « Source indisponible », dit dans la phrase de Risques', async ({ page, request }) => {
		await ouvrir(page, request, clone(), 1440);
		await expect(page.locator('#q-risques > summary .signal-panne')).toBeVisible();
		await expect(page.locator('#q-risques > summary .signal-panne')).toContainText('Rupture de barrage');
		await expect(page.locator('.lecture-sources')).toBeVisible();
		await expect(page.locator('.lecture-sources')).toContainText('ce n’est jamais un');
		await ouvrirCategorie(page, 'q-risques');
		const ligne = page.locator('#critere-rupture_barrage');
		await expect(ligne).toBeVisible();
		await expect(ligne.locator('.badge')).toHaveText('Source indisponible');
		await expect(page.locator('#q-risques .bref-p')).toContainText('n’ont pas pu être lus');
	});

	test('thème hors table : le panneau final « Autres critères », compté dans aucune catégorie', async ({ page, request }) => {
		const a = clone();
		const modele = a.rapport.donnees.themes.find((t) => t.id === 'radon')!;
		a.rapport.donnees.themes.push({ ...structuredClone(modele), id: 'theme_inconnu_du_front', titre: 'Critère que ce site ne connaît pas', verdict: 'Servi par l’API, rendu tel quel.' });
		await ouvrir(page, request, a, 1440);
		const autres = page.locator('section#q-autres');
		await expect(autres.getByRole('heading', { level: 2 })).toHaveText('Autres critères');
		await expect(autres.locator('li[id="critere-theme_inconnu_du_front"]')).toBeVisible();
		await expect(autres.locator('li[id^="critere-"]')).toHaveCount(1);
		await expect(page.locator('nav.sommaire-large a[href="#q-autres"]')).toBeVisible();
		// Placed in no category: the 43 known themes keep theirs, the 44th is only in « Autres critères ».
		await expect(page.locator('details.cat li[id^="critere-"]')).toHaveCount(43);
		await sansViolationAxe(page, 'autres critères');
	});

	test('carte en erreur : « Carte indisponible. » sans cadre, la colonne est retirée', async ({ page, request }) => {
		await repondreCarteStatique(page, { statut: 503 });
		await ouvrir(page, request, clone(), 1440);
		const carte = page.locator('.adresse-carte figure.carte');
		await carte.scrollIntoViewIfNeeded();
		await expect(carte.locator('figcaption')).toContainText('Carte indisponible.');
		await expect(carte.locator('img')).toHaveCount(0);
		const texte = (await page.locator('.fiche-identite').boundingBox())!;
		expect((await carte.boundingBox())!.y, 'la légende passe sous l’identité, sans colonne').toBeGreaterThanOrEqual(texte.y + texte.height - 1);
		await sansDebordement(page, 'carte indisponible');
	});
});

test('backstop forced-colors: active : captures sans seuil, pictogrammes présents', async ({ page, request }, testInfo) => {
	await page.emulateMedia({ forcedColors: 'active' });
	await ouvrir(page, request, clone(), 1440);
	await verifier43Ancres(page);
	// The drawing of a scale stays an inline SVG painted in currentColor: it is there for every criterion.
	await page.screenshot({ path: preuve(testInfo, 'fiche-43-forced-colors-ecran.png'), animations: 'disabled' });
	await ouvrirCategorie(page, 'q-risques');
	await expect(page.locator('#q-risques li[id^="critere-"] .ligne-ech svg').first()).toBeVisible();
	await page.locator('#q-risques').screenshot({ path: preuve(testInfo, 'fiche-43-forced-colors-risques.png'), animations: 'disabled' });
});

// Reading of 06/11: the fiche answers first, at every width, in the order of the DOM, and shows that it goes on.
// The vigilance line of the arrival as datacommune.fr served it for 44109_zz02_00031 on 2026-11-09 (read from
// its `__data.json`): the texts and their order decide where a phone breaks the line, so they are the real ones.
const VIGILANCES_REELLES = [
	{ id: 'urbanisme', titre: 'Urbanisme', accroche: '9 prescriptions au point', niveau: 'adresse', rayon_m: null },
	{ id: 'inondation', titre: 'Inondation', accroche: null, niveau: 'adresse', rayon_m: null },
	{ id: 'remontee_nappe', titre: 'Remontée de nappe', accroche: null, niveau: 'adresse', rayon_m: null },
	{ id: 'mouvements_terrain', titre: 'Cavités et mouvements de terrain', accroche: '1 mouvement de terrain à 500 m', niveau: 'rayon', rayon_m: 500 },
	{ id: 'secteur_bruit', titre: 'Secteur affecté par le bruit', accroche: null, niveau: 'adresse', rayon_m: null }
] as const;

function avecVigilancesReelles(): Entree {
	const a = clone();
	for (const v of VIGILANCES_REELLES) {
		const theme = a.rapport.donnees.themes.find((t) => t.id === v.id)!;
		Object.assign(theme, { titre: v.titre, accroche: v.accroche, ton: 'vigilance', etat: 'ok', granularite: { niveau: v.niveau, rayon_m: v.rayon_m, arrondissement: null } });
	}
	a.rapport.donnees.resume.points_adresse = VIGILANCES_REELLES.map((v) => ({
		theme: v.id,
		titre: v.titre,
		verdict: `${v.titre} : vigilance à l'adresse.`,
		granularite: { niveau: v.niveau, rayon_m: v.rayon_m, arrondissement: null }
	}));
	return a;
}

/** Where each item of the vigilance line lies, line by line: the words of its title and of the whole item. */
async function lignesDesVigilances(page: Page) {
	return page.locator('.arrivee').evaluate((bande) => {
		const gauche = bande.getBoundingClientRect().left + parseFloat(getComputedStyle(bande).paddingLeft);
		const mots = (el: Element) => {
			const sortie: { top: number; left: number }[] = [];
			const parcours = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
			for (let n = parcours.nextNode() as Text | null; n; n = parcours.nextNode() as Text | null) {
				for (const m of n.data.matchAll(/\S+/g)) {
					const r = document.createRange();
					r.setStart(n, m.index!);
					r.setEnd(n, m.index! + m[0].length);
					const rect = r.getClientRects()[0];
					if (rect) sortie.push({ top: Math.round(rect.top), left: rect.left });
				}
			}
			return sortie;
		};
		const lignes = (el: Element | null) => (el ? new Set(mots(el).map((m) => m.top)).size : 1);
		return [...bande.querySelectorAll('li')].map((li) => {
			const tous = mots(li);
			return {
				texte: li.textContent!.replace(/\s+/g, ' ').trim(),
				lignes: lignes(li),
				lignesTitre: lignes(li.querySelector('.bref-t')),
				lignesAccroche: lignes(li.querySelector('.bref-v')),
				// The arrow is the last word: it never starts a line alone.
				flecheSeule: tous.length > 1 && tous.at(-1)!.top !== tous.at(-2)!.top,
				enDebutDeLigne: Math.abs(tous[0].left - gauche) < 2
			};
		});
	});
}

test.describe('lecture de la fiche (06/11)', () => {
	// 4.9 finish: on a phone the line of the vigilances cut its items anywhere (« prescriptions au » left alone,
	// « Remontée de / nappe »). An item that fits on a line is never split; one longer than a line starts a line
	// and breaks only between its title and its value, each kept whole, its arrow with them.
	for (const largeur of [375, 390, 1440] as const) {
		test(`${largeur} px : la ligne des vigilances ne coupe aucun critère, sur le texte réel`, async ({ page, request }, testInfo) => {
			await ouvrir(page, request, avecVigilancesReelles(), largeur, 812);
			await expect(page.locator('.bref-l-adresse > li')).toHaveCount(VIGILANCES_REELLES.length);
			for (const item of await lignesDesVigilances(page)) {
				expect(item.lignesTitre, `titre coupé : ${item.texte}`).toBe(1);
				expect(item.lignesAccroche, `accroche coupée : ${item.texte}`).toBe(1);
				expect(item.flecheSeule, `flèche seule en début de ligne : ${item.texte}`).toBe(false);
				if (item.lignes > 1) expect(item.enDebutDeLigne, `critère coupé en cours de ligne : ${item.texte}`).toBe(true);
			}
			await sansDebordement(page, `ligne des vigilances à ${largeur} px`);
			await page.locator('section#bref').screenshot({ path: preuve(testInfo, `vigilances-reelles-${largeur}.png`) });
		});
	}

	test('375 px : la réponse, puis une catégorie fermée dépasse du premier écran, les actions et la carte après', async ({ page, request }) => {
		await ouvrir(page, request, clone(), 375, 812);
		const haut = async (selecteur: string) => (await page.locator(selecteur).first().boundingBox())!.y;
		const bref = await haut('section#bref');
		const categorie = await haut('details.cat');
		expect(categorie, 'une ligne fermée au premier écran').toBeLessThan(812);
		expect(categorie).toBeGreaterThan(bref);
		for (const selecteur of ['.adresse-actions', '.adresse-carte']) expect(await haut(selecteur), selecteur).toBeGreaterThan(categorie);
		await expect(page.locator('details.cat[open]')).toHaveCount(0);
		await expect(page.locator('details.cat .cat-annonce').first()).toBeVisible();
	});

	test('1440 px : la réponse en pleine largeur, puis le sommaire à gauche et quatre cartes ouvertes', async ({ page, request }) => {
		await ouvrir(page, request, clone(), 1440, 900);
		await expect(page.locator('dl.chiffres-cles > .cle').first()).toBeInViewport();
		await expect(page.locator('details#q-prix > summary')).toBeInViewport();
		const identite = (await page.locator('.fiche-identite').boundingBox())!;
		const carte = (await page.locator('.adresse-carte').boundingBox())!;
		const reponse = (await page.locator('section#bref').boundingBox())!;
		const sommaire = (await page.locator('nav.sommaire-large').boundingBox())!;
		const prix = (await page.locator('details#q-prix').boundingBox())!;
		expect(carte.x).toBeGreaterThan(identite.x + identite.width - 1);
		expect(sommaire.y, 'le sommaire commence sous la réponse').toBeGreaterThanOrEqual(reponse.y + reponse.height);
		expect(sommaire.x).toBe(reponse.x);
		expect(prix.x, 'le contenu est à droite du sommaire').toBeGreaterThanOrEqual(sommaire.x + sommaire.width);
		expect(prix.x + prix.width).toBeCloseTo(reponse.x + reponse.width, 0);
		// Open at once, every row of the fiche read without a click (review of 06/11).
		await expect(page.locator('details.cat:not([open])')).toHaveCount(0);
		await expect(page.locator('details.cat > .cat-corps li[id^="critere-"] .ligne-phrase:visible')).toHaveCount(43);
		await expect(page.locator('.voir-autres')).toHaveCount(0);
		await expect(page.locator('details.cat > summary .cat-plus').first()).toBeHidden();
		await expect(page.getByRole('button', { name: /Tout (afficher|replier|déplier)/ })).toHaveCount(0);
	});

	test('sommaire collant : il reste en vue, dit où l’on est, et ne dépasse jamais d’une fenêtre basse', async ({ page, request }) => {
		await ouvrir(page, request, clone(), 1440, 900);
		const sommaire = page.locator('nav.sommaire-large');
		await page.evaluate(() => window.scrollTo({ top: document.querySelector('#critere-secteur_bruit')!.getBoundingClientRect().top + scrollY - 90, behavior: 'instant' }));
		await expect(sommaire).toBeInViewport({ ratio: 1 });
		await expect(sommaire.locator('[aria-current]')).toHaveCount(1);
		await expect(sommaire.locator('[aria-current="location"]')).toHaveAttribute('href', '#critere-secteur_bruit');
		await page.evaluate(() => window.scrollTo({ top: document.querySelector('#q-risques')!.getBoundingClientRect().top + scrollY - 90, behavior: 'instant' }));
		await expect(sommaire.locator('[aria-current="location"]')).toHaveText('Risques');

		await page.setViewportSize({ width: 1440, height: 420 });
		const mesure = await sommaire.evaluate((nav) => ({ haut: nav.scrollHeight, boite: nav.clientHeight, bas: nav.getBoundingClientRect().bottom, vue: innerHeight }));
		expect(mesure.haut, 'le sommaire a plus à montrer que la fenêtre').toBeGreaterThan(mesure.boite);
		expect(mesure.bas, 'rien n’est coupé').toBeLessThanOrEqual(mesure.vue);
		await sommaire.locator('a[href="#sources"]').scrollIntoViewIfNeeded();
		await expect(sommaire.locator('a[href="#sources"]')).toBeInViewport();
	});

	test('le sommaire nomme chaque ligne, suit la lecture ligne par ligne et se déplace seul, sans bouger la page', async ({ page, request }) => {
		await ouvrir(page, request, clone(), 1440, 600);
		const sommaire = page.locator('nav.sommaire-large');
		const courante = sommaire.locator('[aria-current="location"]');
		const lignes = await page.locator('details.cat li[id^="critere-"]:visible').evaluateAll((els) => els.map((el) => el.id));
		const toutes = await page.locator('details.cat li[id^="critere-"]').evaluateAll((els) => els.map((el) => el.id));
		const entrees = await sommaire.locator('a[href^="#critere-"]').evaluateAll((els) => els.map((el) => el.getAttribute('href')!.slice(1)));
		expect(lignes.length).toBeGreaterThan(20);
		expect(entrees, 'chaque ligne, repliée comprise, dans l’ordre de la page').toEqual(toutes);

		const enVue = () =>
			sommaire.evaluate((nav) => {
				const lien = nav.querySelector('[aria-current]')!.getBoundingClientRect();
				const boite = nav.getBoundingClientRect();
				return lien.top >= boite.top && lien.bottom <= boite.bottom;
			});
		const hauts = new Set<number>();
		for (const id of lignes) {
			const y = await page.evaluate((cible) => {
				window.scrollTo({ top: document.getElementById(cible)!.getBoundingClientRect().top + scrollY - 96, behavior: 'instant' });
				return scrollY;
			}, id);
			await expect(courante, id).toHaveAttribute('href', `#${id}`);
			await expect.poll(enVue, { message: `${id} reste en vue dans le sommaire` }).toBe(true);
			expect(await page.evaluate(() => scrollY), 'le sommaire ne déplace pas la page').toBe(y);
			hauts.add(await sommaire.evaluate((nav) => nav.scrollTop));
		}
		expect(hauts.size, 'le sommaire a bien défilé').toBeGreaterThan(3);

		await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
		await expect(courante).toHaveCount(0);
		await expect.poll(() => sommaire.evaluate((nav) => nav.scrollTop), { message: 'revenu en haut de la page, revenu en haut du sommaire' }).toBe(0);
	});

	test('le focus dans le sommaire collant ne déplace jamais la page, à 1440 comme à 375 px', async ({ page, request }) => {
		// Motion on, as the jump needs it: the smooth scroll of the page starts at once, a few frames show it.
		const images = () => page.evaluate(() => new Promise((fait) => {
			let reste = 6;
			const suite = () => (--reste > 0 ? requestAnimationFrame(suite) : fait(null));
			requestAnimationFrame(suite);
		}));
		const aller = async () => {
			await page.evaluate(() => window.scrollTo({ top: document.querySelector('#critere-argiles')!.getBoundingClientRect().top + scrollY - 96, behavior: 'instant' }));
			await images();
			return page.evaluate(() => scrollY);
		};

		await ouvrir(page, request, clone(), 1440, 900);
		const y = await aller();
		const liens = page.locator('nav.sommaire-large a');
		for (const rang of [0, 5, 20, 35, 49]) {
			await liens.nth(rang).evaluate((a) => (a as HTMLElement).focus());
			await images();
			expect(await page.evaluate(() => scrollY), `lien ${rang}`).toBe(y);
		}

		await ouvrir(page, request, clone(), 375, 812);
		await page.locator('details#q-risques > summary').click();
		const bas = await aller();
		await page.locator('nav.sommaire-compact summary').focus();
		await page.keyboard.press('Enter');
		await expect(page.locator('nav.sommaire-compact details')).toHaveJSProperty('open', true);
		await images();
		expect(await page.evaluate(() => scrollY), 'la barre compacte').toBe(bas);
		await expect(page.locator('nav.sommaire-compact [aria-current="location"]')).toHaveAttribute('href', '#critere-argiles');
	});

	test('une ligne en fin de catégorie : son entrée y mène, la lecture la suit, et le bas de page se lit aussi', async ({ page, request }) => {
		await ouvrir(page, request, clone(), 1440, 900);
		const sommaire = page.locator('nav.sommaire-large');
		await sommaire.locator('a[href="#critere-radon"]').click();
		await expect(page.locator('#critere-radon .ligne-titre')).toBeFocused();
		await expect(page.locator('#critere-radon')).toBeInViewport();
		await expect(sommaire.locator('[aria-current="location"]')).toHaveAttribute('href', '#critere-radon');

		await page.evaluate(() => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'instant' }));
		await expect(sommaire.locator('[aria-current="location"]')).toHaveText('Sources de la fiche');
	});

	test('un clic du sommaire : une vraie ancre, le titre sous le haut de la fenêtre et le focus sur lui, sans animation si demandé', async ({ page, request }) => {
		await page.emulateMedia({ reducedMotion: 'reduce' });
		await ouvrir(page, request, clone(), 1440, 900);
		expect(await page.evaluate(() => getComputedStyle(document.documentElement).scrollBehavior)).toBe('auto');
		await page.locator('nav.sommaire-large a[href="#critere-grand_axe"]').click();
		await expect(page).toHaveURL(/#critere-grand_axe$/);
		await expect(page.locator('#critere-grand_axe .ligne-titre')).toBeFocused();
		const haut = await page.locator('#critere-grand_axe').evaluate((el) => el.getBoundingClientRect().top);
		expect(haut).toBeGreaterThan(0);
		expect(haut).toBeLessThan(120);
		// A row at the end of a category is reached too.
		await page.locator('section#bref').scrollIntoViewIfNeeded();
		await page.evaluate(() => (location.hash = '#critere-radon'));
		await expect(page.locator('#critere-radon')).toBeInViewport();
		await expect(page.locator('#critere-radon .ligne-titre')).toBeFocused();
	});

	test('clavier à 1440 px : de l’identité à la réponse, puis du sommaire au titre visé, les cartes ne sont pas des contrôles', async ({ page, request }) => {
		await ouvrir(page, request, clone(), 1440);
		await page.getByRole('button', { name: 'Source et définition : adresse' }).focus();
		await page.keyboard.press('Tab');
		expect(await page.evaluate(() => !!document.activeElement?.closest('section#bref')), 'le Tab suivant entre dans la réponse').toBe(true);

		const lien = page.locator('nav.sommaire-large a[href="#q-risques"]');
		await lien.focus();
		await page.keyboard.press('Enter');
		await expect(page.locator('details#q-risques h3')).toBeFocused();
		await expect(page.locator('details#q-risques > summary')).toHaveAttribute('tabindex', '-1');
		await page.keyboard.press('Enter');
		await expect(page.locator('details#q-risques')).toHaveJSProperty('open', true);

		const plus = page.locator('.actions-plus summary');
		await plus.focus();
		await page.keyboard.press('Enter');
		await expect(page.locator('.actions-plus')).toHaveJSProperty('open', true);
		for (const nom of ['Ventes ressemblantes', 'Télécharger (CSV)', 'Imprimer la fiche']) {
			await page.keyboard.press('Tab');
			await expect(page.locator(':focus')).toHaveText(nom);
		}
	});

	test('clavier à 375 px : une catégorie s’ouvre et se referme, le sommaire compact s’atteint', async ({ page, request }) => {
		await ouvrir(page, request, clone(), 375, 812);
		const risques = page.locator('details#q-risques');
		await risques.locator('> summary').focus();
		await page.keyboard.press('Enter');
		await expect(risques).toHaveJSProperty('open', true);
		await page.keyboard.press('Enter');
		await expect(risques).toHaveJSProperty('open', false);
		await page.locator('nav.sommaire-compact summary').focus();
		await page.keyboard.press('Enter');
		await expect(page.locator('nav.sommaire-compact a[href="#q-logement"]')).toBeVisible();
	});

	test('sous le seuil : un sommaire compact toujours en vue, un lien ouvre sa catégorie et mène à son titre', async ({ page, request }) => {
		await ouvrir(page, request, clone(), 375, 812);
		const compact = page.locator('nav.sommaire-compact');
		await expect(page.locator('nav.sommaire-large')).toBeHidden();
		await page.evaluate(() => window.scrollTo({ top: 1500, behavior: 'instant' }));
		await expect(compact).toBeInViewport({ ratio: 1 });
		await compact.locator('summary').click();
		await compact.locator('a[href="#critere-grand_axe"]').click();
		await expect(page.locator('details#q-quotidien')).toHaveJSProperty('open', true);
		await expect(compact.locator('details')).toHaveJSProperty('open', false);
		await expect(page.locator('#critere-grand_axe .ligne-titre')).toBeFocused();
		await expect(page.locator('#critere-grand_axe .ligne-titre')).toBeInViewport();
		const barre = (await compact.boundingBox())!;
		const titre = (await page.locator('#critere-grand_axe .ligne-titre').boundingBox())!;
		expect(titre.y, 'le titre n’est pas sous la barre').toBeGreaterThanOrEqual(barre.y + barre.height);
	});

	test('sous le seuil : la barre nomme où l’on est sur une ligne, et la liste s’ouvre sur cet endroit', async ({ page, request }) => {
		await ouvrir(page, request, clone(), 320, 640);
		const compact = page.locator('nav.sommaire-compact');
		const hauteur = (await compact.locator('summary').boundingBox())!.height;
		await page.locator('details#q-risques > summary').click();
		await page.evaluate(() => window.scrollTo({ top: document.querySelector('#critere-mouvements_terrain')!.getBoundingClientRect().top + scrollY - 96, behavior: 'instant' }));
		await expect(compact.locator('.sommaire-courante')).toHaveText('Cavités et mouvements de terrain');
		expect((await compact.locator('summary').boundingBox())!.height, 'la barre ne grandit pas').toBe(hauteur);

		await compact.locator('summary').click();
		const lien = compact.locator('[aria-current="location"]');
		await expect(lien).toBeVisible();
		await expect.poll(() =>
			lien.evaluate((a) => {
				const boite = a.closest('.sommaire-menu')!.getBoundingClientRect();
				const ligne = a.getBoundingClientRect();
				return ligne.top >= boite.top && ligne.bottom <= boite.bottom;
			})
		).toBe(true);
	});

	test('375 px : une ligne d’une catégorie fermée n’est jamais l’endroit où l’on est', async ({ page, request }) => {
		await ouvrir(page, request, clone(), 375, 812);
		const invisibles = await page.evaluate(async () => {
			const trouvees: string[] = [];
			for (let y = 0; y < document.documentElement.scrollHeight; y += 150) {
				window.scrollTo({ top: y, behavior: 'instant' });
				await new Promise((fait) => requestAnimationFrame(() => requestAnimationFrame(fait)));
				const lien = document.querySelector('nav.sommaire-compact [aria-current]');
				if (lien?.getAttribute('href') && document.querySelector(lien.getAttribute('href')!)?.parentElement?.closest('details:not([open])')) trouvees.push(lien.textContent!);
			}
			return trouvees;
		});
		expect(invisibles, 'du haut au bas de la page, le sommaire ne désigne jamais une ligne que le lecteur ne voit pas').toEqual([]);
	});

	test('passer le seuil : les cartes s’ouvrent au-dessus, les catégories se referment en dessous', async ({ page, request }) => {
		await ouvrir(page, request, clone(), 1440, 900);
		await expect(page.locator('details.cat:not([open])')).toHaveCount(0);
		await page.setViewportSize({ width: 1000, height: 900 });
		await expect(page.locator('details.cat[open]')).toHaveCount(0);
		await expect(page.locator('nav.sommaire-compact')).toBeVisible();
		await page.setViewportSize({ width: 1440, height: 900 });
		await expect(page.locator('details.cat:not([open])')).toHaveCount(0);
	});

	test('sous le seuil, plusieurs catégories restent ouvertes, et une ouverture n’étire rien', async ({ page, request }) => {
		await ouvrir(page, request, clone(), 1000, 900);
		const hauteur = async (id: string) => (await page.locator(`details#${id}`).boundingBox())!.height;
		const voisine = await hauteur('q-quotidien');
		await ouvrirCategorie(page, 'q-prix');
		await ouvrirCategorie(page, 'q-logement');
		await expect(page.locator('details.cat[open]')).toHaveCount(2);
		expect(await hauteur('q-quotidien'), 'la voisine fermée garde sa hauteur').toBe(voisine);
		// The detail of a row pushes what follows, it stretches no other row.
		const ligne = page.locator('#critere-fiscalite');
		const avant = (await ligne.boundingBox())!.height;
		await page.locator('#critere-loyers details.det > summary').click();
		await expect(page.locator('#critere-loyers details.det')).toHaveJSProperty('open', true);
		expect((await ligne.boundingBox())!.height).toBe(avant);
	});

	test('une catégorie ouverte avant l’hydratation reste ouverte', async ({ page, request }) => {
		const a = clone();
		await configurer(request, a);
		await page.setViewportSize({ width: 375, height: 812 });
		let liberer!: () => void;
		const libere = new Promise<void>((resolve) => (liberer = resolve));
		// The scripts wait: the server render is all there is when the reader taps.
		await page.route(/\.(js|ts|svelte|mjs)(\?|$)|\/@fs\/|\/@vite\/|\/\.svelte-kit\//, async (route) => {
			await libere;
			await route.fallback();
		});
		const hydratee = page.waitForResponse((reponse) => new URL(reponse.url()).pathname === '/auth/me', { timeout: 30_000 });
		await page.goto(`/adresse/${a.banId}`, { waitUntil: 'commit' });
		await page.locator('details#q-prix > summary').click();
		await expect(page.locator('details#q-prix')).toHaveJSProperty('open', true);
		liberer();
		await hydratee;
		await expect(page.locator('details#q-prix')).toHaveJSProperty('open', true);
		await expect(page.locator('details#q-logement')).toHaveJSProperty('open', false);
	});

	test('une ancre ouvre sa catégorie à l’arrivée, et la suite de la liste quand la ligne y est', async ({ page, request }) => {
		await configurer(request, clone());
		await page.setViewportSize({ width: 375, height: 812 });
		const a = clone();
		const hydratee = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
		await page.goto(`/adresse/${a.banId}#critere-radon`);
		await hydratee;
		await expect(page.locator('details#q-risques')).toHaveJSProperty('open', true);
		await expect(page.locator('#critere-radon')).toBeInViewport();
		await expect(page.locator('details#q-prix')).toHaveJSProperty('open', false);
	});

	test('un lien de la ligne d’arrivée ouvre la catégorie et mène à sa ligne, même deux fois de suite', async ({ page, request }) => {
		await ouvrir(page, request, clone(), 375, 812);
		const lien = page.locator('section#bref .bref-l a').first();
		const cible = (await lien.getAttribute('href'))!;
		const categorie = page.locator(`details.cat:has(${cible})`);
		await lien.click();
		await expect(categorie).toHaveJSProperty('open', true);
		await expect(page.locator(cible)).toBeInViewport();
		await categorie.locator('> summary').click();
		await expect(categorie).toHaveJSProperty('open', false);
		await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
		await lien.click();
		await expect(categorie).toHaveJSProperty('open', true);
		await expect(page.locator(cible)).toBeInViewport();
	});

	test('un état par ligne : aucune ligne sans valeur ne dit son état à la fois en accroche et en badge', async ({ page, request }) => {
		await ouvrir(page, request, clone(), 1440);
		for (const etat of ['manque', 'hors_couverture', 'panne', 'non_concerne']) {
			const lignes = page.locator(`li[data-etat="${etat}"]`);
			expect(await lignes.count(), etat).toBeGreaterThan(0);
			await expect(lignes.locator('.badge')).toHaveCount(await lignes.count());
			await expect(lignes.locator('.val')).toHaveCount(0);
		}
	});

	test('les têtes de catégorie disent pannes, manques et hors couverture sans se confondre, sans ouvrir', async ({ page, request }) => {
		await ouvrir(page, request, clone(), 375, 812);
		await expect(page.locator('details.cat > summary .signal-panne').first()).toBeVisible();
		const comptes = await page.locator('details.cat > summary .signal-discret').allTextContents();
		expect(comptes.join(' | '), 'le hors couverture reste compté').toMatch(/hors couverture/);
		expect(comptes.join(' | ')).toMatch(/sans valeur ici/);
		for (const texte of await page.locator('details.cat > summary .signal-panne').allTextContents()) expect(texte).toMatch(/^Source indisponible/);
		await page.setViewportSize({ width: 1440, height: 900 });
		await expect(page.locator('details.cat > summary .signal-panne').first()).toBeVisible();
	});

	for (const largeur of [375, 320]) {
		test(`axe à ${largeur} px : actions, catégories, sommaire et lignes ouverts, sans débordement`, async ({ page, request }) => {
			await ouvrir(page, request, clone(), largeur);
			await page.locator('.actions-plus summary').click();
			await deplier(page, true);
			await sansViolationAxe(page, `tout ouvert à ${largeur}`);
			expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'pas de défilement horizontal').toBe(true);
		});
	}

	test('impression sans geste : les catégories fermées et la suite des listes sont sur le papier, ni le sommaire ni les actions', async ({ page, request }) => {
		await ouvrir(page, request, clone(), 1000);
		await page.emulateMedia({ media: 'print' });
		await expect(page.locator('#critere-radon .ligne-phrase')).toBeVisible();
		await expect(page.locator('#critere-fiscalite .ligne-phrase')).toBeVisible();
		await expect(page.locator('.adresse-actions')).toBeHidden();
		await expect(page.locator('nav.sommaire-large')).toBeHidden();
		await expect(page.locator('nav.sommaire-compact')).toBeHidden();
	});

	test.describe('sans JavaScript', () => {
		test.use({ javaScriptEnabled: false });

		test('375 px : les catégories s’ouvrent nativement, la suite des listes est là, le sommaire reste de simples liens', async ({ page, request }) => {
			const a = clone();
			await configurer(request, a);
			await page.setViewportSize({ width: 375, height: 812 });
			await page.goto(`/adresse/${a.banId}`);
			await page.locator('details#q-risques > summary').click();
			await expect(page.locator('#critere-inondation .ligne-phrase')).toBeVisible();
			await expect(page.locator('#critere-radon .ligne-phrase'), 'la ligne à deux clics est dans la liste').toBeVisible();
			await page.locator('#critere-inondation details.det > summary').click();
			await expect(page.locator('#critere-inondation .det-in')).toBeVisible();
			await page.locator('nav.sommaire-compact summary').click();
			await expect(page.locator('nav.sommaire-compact a[href="#q-prix"]')).toBeVisible();
			await page.locator('nav.sommaire-compact summary').click();
			await page.locator('.actions-plus summary').click();
			await expect(page.locator('.actions-plus').getByRole('link', { name: 'Ventes ressemblantes' })).toHaveAttribute('href', /panneau=ventes/);
			await expect(page.locator('section#bref .lien-suite')).toHaveAttribute('href', /panneau=ventes/);
		});

		test('1440 px : tout se lit sans clic, le sommaire est là, une ancre mène à sa ligne', async ({ page, request }) => {
			const a = clone();
			await configurer(request, a);
			await page.setViewportSize({ width: 1440, height: 900 });
			await page.goto(`/adresse/${a.banId}`);
			await expect(page.locator('details.cat li[id^="critere-"] .ligne-phrase:visible')).toHaveCount(43);
			await expect(page.locator('nav.sommaire-large a[href="#q-risques"]')).toBeVisible();
			await page.goto(`/adresse/${a.banId}#critere-radon`);
			await expect(page.locator('#critere-radon')).toBeInViewport();
		});

		test('375 px : une ancre mène à sa ligne, sa catégorie s’ouvrant d’elle-même', async ({ page, request }) => {
			const a = clone();
			await configurer(request, a);
			await page.setViewportSize({ width: 375, height: 812 });
			await page.goto(`/adresse/${a.banId}#critere-radon`);
			await expect(page.locator('details#q-risques')).toHaveJSProperty('open', true);
			await expect(page.locator('#critere-radon')).toBeInViewport();
		});
	});
});
