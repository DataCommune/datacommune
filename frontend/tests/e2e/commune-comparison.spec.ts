import { mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import AxeBuilder from '@axe-core/playwright';
import { test, expect, type APIRequestContext, type Locator, type Page } from '@playwright/test';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { expectNoUnexpectedOffLocalRequest, installLocalNetworkGuard } from './fixtures/local-network';
import { textesVisiblesDeLaBande } from './fixtures/bande';

// Comparator `/comparer` (D-48, D-49, UI-01, COM-03). What this spec locks:
//   - 2 to 4 communes are read in ONE call to /v1/communes/comparaison, SSR included, in the order
//     of the link; the columns follow that order; « Placer en premier » asks the API again;
//   - the gap line of columns 2 to 4 is the served one (gap, reason or verdict), never computed;
//     a commune out of coverage keeps its column with badge and reason;
//   - each cell renders the served value or state; the fiche and the comparator agree on state,
//     reason and value for every fiche row (parity tightened in 03-10: state and motif compared);
//   - selection in the link: history, altered link, four communes at most, « Différences
//     seulement », « Copier le lien »; focus after « Retirer » and « Placer en premier »;
//   - the four states (empty, one commune, outage then « Recharger la comparaison »);
//   - 375 px: two columns without any scroll, three or four in the region, which scrolls
//     horizontally only (the page scrolls, 03-18 R-2); criteria stick left; the column names stay in
//     view through a duplicated header band aligned on the real `th`; 1280 px; print without
//     scrolling nor band; no browser request leaves the local environment.
//
// Responses: the frozen harness responses of 03-09 (`__fixtures__/commune-comparaison.json`,
// guarded by `api/backend/tests/test_comparison_fixture_contract.py`). The variants built below
// from them (subset, reordered columns, a fourth commune, a long name) are SYNTHETIC: they keep
// the served shape and only exercise the page; the page never computes what they would change.

interface Cellule { code_insee: string; etat: string | null; valeur: unknown; periode: string | null; ecart: { valeur: number; unite: string } | null; motif_non_calcul: string | null; identique_a_la_reference: boolean | null }
interface Critere { cle: string; field_id: string; unite: string; identiques: boolean; cellules: Cellule[] }
interface Commune { code_insee: string; nom: string; departement: string; hebergement: { etat: string; motif: string | null; departements: string[] }; synthese: Record<string, unknown> | null }
interface Reponse { statut: string; donnees: { communes: Commune[]; criteres: Critere[]; codes_inconnus: string[] }; meta: { source: unknown[] } | null }

const REPONSES = JSON.parse(readFileSync(new URL('../../src/lib/data/__fixtures__/commune-comparaison.json', import.meta.url), 'utf8')) as Record<string, Reponse>;
const DEUX = REPONSES.deux_communes_hebergees;
const TROIS_AVEC_INCONNU = REPONSES.trois_communes_dont_hors_couverture;

/** Columns of the three-commune response taken in `codes` order (SYNTHETIC when reordered). */
function colonnes(codes: string[], base: Reponse = TROIS_AVEC_INCONNU): Reponse {
	const copie = structuredClone(base);
	const index = codes.map((code) => copie.donnees.communes.findIndex((commune) => commune.code_insee === code));
	if (index.some((i) => i < 0)) throw new Error(`unknown code in ${codes}`);
	const memeOrdre = index.every((i, position) => i === position);
	copie.donnees.communes = index.map((i) => copie.donnees.communes[i]);
	copie.donnees.criteres = copie.donnees.criteres.map((critere) => ({
		...critere,
		cellules: index.map((i, position) => {
			const cellule = critere.cellules[i];
			if (memeOrdre) return cellule;
			if (position === 0) return { ...cellule, ecart: null, motif_non_calcul: null, identique_a_la_reference: null };
			return cellule.ecart || cellule.motif_non_calcul ? cellule : { ...cellule, motif_non_calcul: 'donnee_absente', identique_a_la_reference: false };
		})
	}));
	copie.donnees.codes_inconnus = [];
	return copie;
}

/** A fourth hosted commune, copied from the second column under another code (SYNTHETIC). */
function quatre(nom = 'Quatrième commune'): Reponse {
	const copie = colonnes(['44991', '44992', '85991']);
	const modele = structuredClone(copie.donnees.communes[1]);
	modele.code_insee = '44993';
	modele.nom = nom;
	if (modele.synthese) Object.assign(modele.synthese, { code_insee: '44993', nom_commune: nom });
	copie.donnees.communes.push(modele);
	for (const critere of copie.donnees.criteres) critere.cellules.push({ ...critere.cellules[1], code_insee: '44993' });
	return copie;
}

const TROIS = colonnes(['44991', '44992', '85991']);

type Reply = { status: number; body: unknown };
type Contract = { method: string; pathname: string; query: Record<string, string>; minCalls: number; maxCalls: number; responses: Reply[] };

function comparaison(codes: string[], body: unknown, count = 1, responses?: Reply[]): Contract {
	return {
		method: 'GET', pathname: '/v1/communes/comparaison', query: { codes: codes.join(',') },
		minCalls: 1, maxCalls: count, responses: responses ?? Array.from({ length: count }, () => ({ status: 200, body }))
	};
}
function fiche(code: string, count = 1): Contract {
	const commune = DEUX.donnees.communes.find((item) => item.code_insee === code)!;
	const body = { statut: 'ok', donnees: [commune.synthese], meta: DEUX.meta };
	return { method: 'GET', pathname: '/v1/commune', query: { code_insee: code }, minCalls: 1, maxCalls: count, responses: Array.from({ length: count }, () => ({ status: 200, body })) };
}
function referentiel(code: string, nom: string): Contract {
	const body = { statut: 'ok', donnees: { results: [{ code_insee: code, nom, departement: code.slice(0, 2), code_postal: null }] } };
	return { method: 'GET', pathname: '/v1/communes', query: { code_insee: code }, minCalls: 1, maxCalls: 1, responses: [{ status: 200, body }] };
}
function recherche(q: string, resultats: unknown[]): Contract {
	return { method: 'GET', pathname: '/v1/communes/search', query: { q, limit: '8', inclure_arrondissements: 'true' }, minCalls: 1, maxCalls: 1, responses: [{ status: 200, body: { statut: 'ok', donnees: resultats } }] };
}

async function configure(request: APIRequestContext, expectations: Contract[]) {
	expect((await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, { data: { reset: true, expectations } })).ok()).toBe(true);
}
async function audit(request: APIRequestContext) {
	const res = await request.get(`${E2E_SOURCE_ORIGIN}/__requests`);
	expect(res.ok()).toBe(true);
	return (await res.json()) as { requests: { pathname: string; query: Record<string, string> }[]; violations: unknown[]; unmetExpectations: unknown[] };
}
async function lectures(request: APIRequestContext): Promise<string[]> {
	return (await audit(request)).requests.filter((row) => row.pathname === '/v1/communes/comparaison').map((row) => row.query.codes);
}
async function open(page: Page, url: string) {
	const hydrated = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
	await page.goto(url);
	await hydrated;
	// C2: the categories of a comparison of communes are folded on arrival. The cases of this spec read
	// their rows: they are unfolded here, as a reader would open them; the fold itself is locked by
	// `comparateur-revue.spec.ts`. No fold exists for the addresses and the DPE documents.
	await page.locator('details.compare-groupe-pli').evaluateAll((plis) => plis.forEach((pli) => ((pli as HTMLDetailsElement).open = true)));
}
const norm = (value: string | null | undefined) => value?.replace(/\s+/g, ' ').trim() ?? null;
const region = (page: Page) => page.getByRole('region', { name: 'Tableau de comparaison' });
async function entetes(page: Page): Promise<string[]> {
	return page.locator('thead th[data-code]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-code')!));
}
function cellule(page: Page, cle: string, code: string): Locator {
	return page.locator(`tr[data-cle="${cle}"] td[data-code="${code}"]`);
}
async function sansDebordement(page: Page) {
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

/**
 * Opens the folded panel « Modifier la sélection » (04.5-09, B5): with two objects or more the add
 * control and the filter live in it, closed on arrival. Clicked on its summary, as a visitor does.
 */
async function ouvrirModification(page: Page) {
	const pli = page.locator('details.comparer-modifier');
	await expect(pli).not.toHaveAttribute('open', '');
	await pli.locator('summary').click();
	await expect(pli).toHaveAttribute('open', '');
}

function evidencePath(name: string): string {
	const directory = process.env.DC_COMPARE_HEADER_EVIDENCE_DIR;
	if (!directory) return test.info().outputPath(name);
	mkdirSync(directory, { recursive: true });
	return join(directory, name);
}

function tableEvidencePath(name: string): string {
	const directory = process.env.DC_COMPARE_TABLE_EVIDENCE_DIR;
	if (!directory) return test.info().outputPath(name);
	mkdirSync(directory, { recursive: true });
	return join(directory, name);
}

async function expectWholeWords(locator: Locator, label: string) {
	const splitWords = await locator.evaluate((root) => {
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
	expect(splitWords, `${label} garde ses mots entiers`).toEqual([]);
}

async function expectSingleLineMatches(locator: Locator, label: string) {
	const result = await locator.evaluate((root) => {
		const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
		const matches: { text: string; tops: number[] }[] = [];
		for (let node = walker.nextNode(); node; node = walker.nextNode()) {
			const text = node.textContent ?? '';
			for (const match of text.matchAll(/appartements|\d{4}-\d{2}-\d{2}/giu)) {
				const range = document.createRange();
				range.setStart(node, match.index ?? 0);
				range.setEnd(node, (match.index ?? 0) + match[0].length);
				matches.push({
					text: match[0],
					tops: [...range.getClientRects()].map((rect) => Math.round(rect.top))
				});
			}
		}
		return matches;
	});
	expect(result.length, `${label} contient les libellés et bornes attendus`).toBeGreaterThan(0);
	for (const match of result) {
		expect(new Set(match.tops).size, `${label} coupe « ${match.text} » sur plusieurs lignes`).toBeLessThanOrEqual(1);
	}
}

async function expectBadgeWordSingleLine(row: Locator, label: string) {
	const badges = row.locator('.state-badge').filter({ hasText: 'Non renseigné' });
	await expect(badges, `${label} expose les deux absences servies`).toHaveCount(2);
	const matches = await badges.evaluateAll((elements) => elements.map((element) => {
		const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
		for (let node = walker.nextNode(); node; node = walker.nextNode()) {
			const index = (node.textContent ?? '').indexOf('renseigné');
			if (index < 0) continue;
			const range = document.createRange();
			range.setStart(node, index);
			range.setEnd(node, index + 'renseigné'.length);
			const badge = element.getBoundingClientRect();
			const cell = element.closest('td')!.getBoundingClientRect();
			return {
				tops: [...range.getClientRects()].map((rect) => Math.round(rect.top)),
				contained: badge.left >= cell.left - 1 && badge.right <= cell.right + 1 && badge.top >= cell.top - 1 && badge.bottom <= cell.bottom + 1
			};
		}
		return { tops: [], contained: false };
	}));
	for (const match of matches) {
		expect(match.tops.length, `${label} contient le mot « renseigné »`).toBeGreaterThan(0);
		expect(new Set(match.tops).size, `${label} coupe le mot « renseigné » sur plusieurs lignes`).toBeLessThanOrEqual(1);
		expect(match.contained, `${label} reste dans sa cellule`).toBe(true);
	}
}

async function expectSourceButtonsContained(row: Locator, label: string) {
	const buttons = row.getByRole('button', { name: /^Détail : /i });
	const count = await buttons.count();
	expect(count, `${label} expose ses sources`).toBeGreaterThan(0);
	for (let index = 0; index < count; index++) {
		const button = buttons.nth(index);
		await button.scrollIntoViewIfNeeded();
		await button.focus();
		await expect(button).toBeFocused();
		const measure = await button.evaluate((element) => {
			const buttonBox = element.getBoundingClientRect();
			const cellBox = element.closest('td, th')!.getBoundingClientRect();
			const x = buttonBox.left + buttonBox.width / 2;
			const y = buttonBox.top + buttonBox.height / 2;
			const hit = document.elementFromPoint(x, y);
			return {
				button: { left: buttonBox.left, right: buttonBox.right, top: buttonBox.top, bottom: buttonBox.bottom },
				cell: { left: cellBox.left, right: cellBox.right, top: cellBox.top, bottom: cellBox.bottom },
				width: buttonBox.width,
				height: buttonBox.height,
				hit: !!hit && element.contains(hit)
			};
		});
		expect(measure.button.left, JSON.stringify(measure)).toBeGreaterThanOrEqual(measure.cell.left - 1);
		expect(measure.button.right, JSON.stringify(measure)).toBeLessThanOrEqual(measure.cell.right + 1);
		expect(measure.button.top, JSON.stringify(measure)).toBeGreaterThanOrEqual(measure.cell.top - 1);
		expect(measure.button.bottom, JSON.stringify(measure)).toBeLessThanOrEqual(measure.cell.bottom + 1);
		expect(measure.width, JSON.stringify(measure)).toBeGreaterThanOrEqual(44);
		expect(measure.height, JSON.stringify(measure)).toBeGreaterThanOrEqual(44);
		expect(measure.hit, JSON.stringify(measure)).toBe(true);
	}
}

/** Top of the table in document coordinates (unchanged when the band shows: G1 of 03-18). */
const hautDuTableau = (page: Page) =>
	page.evaluate(() => document.querySelector('table.compare-table')!.getBoundingClientRect().top + window.scrollY);

/** Scrolls the page until the real `thead` has left the window under the site header. */
async function depasserEntete(page: Page) {
	await page.evaluate(() => {
		const bas = document.querySelector('thead')!.getBoundingClientRect().bottom;
		window.scrollTo(0, window.scrollY + bas + 120);
	});
	await expect(page.locator('.compare-bande')).toHaveCSS('visibility', 'visible');
}

/** Box of a cell of the band and of its real header cell (`coin` for the corner), in one frame. */
function boites(page: Page, code: string) {
	return page.evaluate((colonne) => {
		const bande = document.querySelector('.compare-bande')!;
		const [cellule, th] =
			colonne === 'coin'
				? [bande.querySelector('.compare-bande-coin')!, document.querySelector('thead th.compare-coin')!]
				: [bande.querySelector(`.compare-bande-cellule[data-code="${colonne}"]`)!, document.querySelector(`thead th[data-code="${colonne}"]`)!];
		const a = cellule.getBoundingClientRect();
		const b = th.getBoundingClientRect();
		// The visible bottom of the site header: 0 once it has scrolled away (not sticky on the public
		// pages since 04.8-02, A-01), so the band then sticks at the top of the window.
		return {
			bande: { left: a.left, width: a.width }, th: { left: b.left, width: b.width },
			haut: bande.getBoundingClientRect().top, basEnTete: Math.max(0, document.querySelector('header.site-header')!.getBoundingClientRect().bottom)
		};
	}, code);
}
function alignees(mesure: Awaited<ReturnType<typeof boites>>, contexte: string) {
	const texte = `${contexte} ${JSON.stringify(mesure)}`;
	expect(Math.abs(mesure.bande.left - mesure.th.left), texte).toBeLessThanOrEqual(2);
	expect(Math.abs(mesure.bande.width - mesure.th.width), texte).toBeLessThanOrEqual(2);
}

const MOTIFS: Record<string, string> = {
	periodes_differentes: 'périodes différentes', definitions_differentes: 'définitions différentes',
	donnee_absente: 'donnée absente', commune_hors_couverture: 'commune hors couverture',
	periode_inconnue: 'période inconnue', unites_differentes: 'unités différentes'
};

test.beforeEach(async ({ page }) => {
	await installLocalNetworkGuard(page);
	await page.route('**/auth/me', (route) => route.fulfill({ status: 401, json: { detail: 'Non authentifié' } }));
});
test.afterEach(async ({ page, request }) => {
	const result = await audit(request);
	expect(result.violations, JSON.stringify(result.violations)).toEqual([]);
	expect(result.unmetExpectations, JSON.stringify(result.unmetExpectations)).toEqual([]);
	expectNoUnexpectedOffLocalRequest(page);
});

// 04.5-09 (B5, UI-012, amendment 4): the reference and the access to the groups are folded AFTER the
// table. Replaced presentation assertions: « reference, help and summary visible on arrival », « the
// reference precedes the table » and « reference and action inside the first screen ». They become: the
// folded summary accessible and closed, the exact text after one action, the reference and the anchors
// working. Content assertions (text, description, anchors, targets, axe, focus) are kept.
test('la référence et les groupes, repliés après le tableau, s’ouvrent en une action à 1 440 et 375 px', async ({
	page,
	request
}) => {
	await configure(request, [comparaison(['44991', '44992'], DEUX, 2)]);
	let hrefsAttendus: string[] | null = null;

	for (const width of [1440, 375] as const) {
		const height = width === 1440 ? 900 : 800;
		await page.setViewportSize({ width, height });
		await open(page, '/comparer?communes=44991,44992');

		const reference = page.getByRole('heading', {
			level: 2,
			name: 'Référence des écarts : Ancienne'
		});
		const texteAide =
			'« Placer en premier » change la référence utilisée pour lire les écarts. L’ordre ne constitue pas un classement.';
		const aide = page.getByText(texteAide);
		const sommaire = page.getByRole('navigation', { name: 'Accès aux groupes de comparaison' });
		const liens = sommaire.getByRole('link');
		const pli = page.locator('details.comparer-lecture');
		const resume = pli.locator('summary');
		await expect(pli).not.toHaveAttribute('open', '');
		await expect(resume).toBeVisible();
		await expect(resume).toHaveText('Référence des écarts : Ancienne');
		await expect(reference).toBeVisible();
		await expect(aide).toBeHidden();
		await expect(sommaire).toBeHidden();
		expect(
			await reference.evaluate((element) =>
				Boolean(
					document.querySelector('table.compare-table')!.compareDocumentPosition(element) &
						Node.DOCUMENT_POSITION_FOLLOWING
				)
			),
			'la référence suit le tableau dans le document'
		).toBe(true);
		// One action, at the keyboard: the summary opens the reference, its help and the groups.
		await resume.focus();
		await expect(resume).toBeFocused();
		await resume.press('Enter');
		await expect(pli).toHaveAttribute('open', '');
		await expect(aide).toBeVisible();
		await expect(aide).toHaveText(texteAide);
		await expect(sommaire).toBeVisible();
		await expect(pli.getByRole('heading', { name: 'Lire les écarts' })).toBeVisible();
		await expect(page.getByRole('button', { name: 'Placer Courante en premier' })).toHaveAccessibleDescription(
			texteAide
		);
		await expect(page.locator('.comparer')).not.toContainText(/meilleur|gagnant/i);

		const hrefs = await liens.evaluateAll((nodes) => nodes.map((node) => node.getAttribute('href')!));
		expect(hrefs.length, `groupes présents à ${width} px`).toBeGreaterThan(0);
		expect(new Set(hrefs).size, `href de groupes uniques à ${width} px`).toBe(hrefs.length);
		expect(hrefs.every((href) => /^#comparaison-groupe-[a-z0-9_-]+$/.test(href))).toBe(true);
		if (hrefsAttendus === null) hrefsAttendus = hrefs;
		else expect(hrefs, 'les ancres restent stables entre les largeurs').toEqual(hrefsAttendus);
		expect(
			await page.locator('tbody[data-groupe]').evaluateAll((nodes) =>
				nodes.map((node) => `#${node.querySelector('th[scope="rowgroup"]')!.id}`)
			)
		).toEqual(hrefs);

		for (let index = 0; index < hrefs.length; index++) {
			const lien = liens.nth(index);
			const boite = await lien.boundingBox();
			expect(boite, `lien de groupe ${index + 1} mesurable à ${width} px`).toBeTruthy();
			expect(boite!.height, `cible de groupe ${index + 1} à ${width} px`).toBeGreaterThanOrEqual(44);
		}

		await sansDebordement(page);
		await expectWholeWords(page.locator('.comparer-lecture'), `référence et groupes à ${width} px`);
		const axe = await new AxeBuilder({ page })
			.withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
			.analyze();
		expect(axe.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual([]);

		await pli.evaluate((element) => element.scrollIntoView({ block: 'start', behavior: 'instant' }));
		await region(page).evaluate((element) => {
			element.scrollLeft = 0;
		});
		await page.screenshot({
			path: evidencePath(`entete-reference-${width}.png`),
			animations: 'disabled'
		});

		const premierLien = liens.first();
		const premierHref = hrefs[0]!;
		const premierTitre = page.locator(premierHref);
		await expect(premierTitre).toHaveAttribute('scope', 'rowgroup');
		await premierLien.focus();
		await expect(premierLien).toBeFocused();
		await premierLien.press('Enter');
		await expect(premierTitre).toBeFocused();
		const mesurerPosition = () => premierTitre.evaluate((element) => {
			const boite = element.getBoundingClientRect();
			const header = document.querySelector('header.site-header')!.getBoundingClientRect();
			const bande = document.querySelector('.compare-bande')!;
			const styleBande = getComputedStyle(bande);
			const basBande = styleBande.visibility === 'visible' ? bande.getBoundingClientRect().bottom : 0;
			const bloqueur = Math.max(header.bottom, basBande);
			const x = Math.min(window.innerWidth - 2, Math.max(2, boite.left + Math.min(boite.width / 2, 80)));
			const y = Math.min(window.innerHeight - 2, boite.top + Math.min(boite.height / 2, 20));
			const dessus = document.elementFromPoint(x, y);
			return {
				top: boite.top,
				bottom: boite.bottom,
				blockerBottom: bloqueur,
				viewportHeight: window.innerHeight,
				hit: !!dessus && element.contains(dessus)
			};
		});
		await expect
			.poll(async () => {
				const mesure = await mesurerPosition();
				return (
					mesure.top >= mesure.blockerBottom - 1 &&
					mesure.top < mesure.viewportHeight &&
					mesure.hit
				);
			}, { message: `titre du premier groupe visible après activation à ${width} px` })
			.toBe(true);
		const position = await mesurerPosition();
		expect(position.top, JSON.stringify(position)).toBeGreaterThanOrEqual(position.blockerBottom - 1);
		expect(position.top, JSON.stringify(position)).toBeLessThan(position.viewportHeight);
		expect(position.hit, JSON.stringify(position)).toBe(true);
		await page.screenshot({
			path: evidencePath(`premier-groupe-${width}.png`),
			animations: 'disabled'
		});
	}

	expect(await lectures(request)).toEqual(['44991,44992', '44991,44992']);
});

test('une provenance communale structurée et strictement identique est affichée une seule fois par ligne', async ({
	page,
	request
}) => {
	const reponse = structuredClone(DEUX);
	const sources = reponse.meta!.source as Array<{
		dataset: string;
		periode: { statut: 'connue' | 'glissante' | 'inconnue'; label: string | null; debut: string | null; fin: string | null };
	}>;
	const revenus = sources.find((source) => source.dataset === 'revenus')!;
	revenus.periode = { statut: 'connue', label: '2021', debut: null, fin: null };
	await configure(request, [comparaison(['44991', '44992'], reponse, 2)]);

	for (const width of [1440, 375] as const) {
		const height = width === 1440 ? 900 : 800;
		await page.setViewportSize({ width, height });
		await open(page, '/comparer?communes=44991,44992');
		const ligne = page.locator('tr[data-cle="revenu_median"]');
		// C3 (inventory of 2026-11-10, arbitrated by Ianis on 2026-11-10: « oui à la période et à la source derrière le bouton d'info »): the period and the source left the face; the one
		// control of the row opens the panel that says both.
		await expect(ligne.locator('.compare-meta-commune, .compare-meta')).toHaveCount(0);
		await ligne.locator('th.compare-critere .source-button').click();
		const voletLigne = page.getByRole('dialog');
		await expect(voletLigne).toContainText('Millésime 2021');
		await expect(voletLigne).toContainText('Filosofi (revenus et pauvreté)');
		await page.keyboard.press('Escape');
		await expect(voletLigne).toBeHidden();
		// Review of 09/11: the unit is read with each value, never alone under the label.
		// Declared change (direction A, T10, 2026-11-10): from 721 px the row says its unit once after its label;
		// on a phone the label says none and the cells keep theirs.
		if (width === 1440) await expect(ligne.locator('th.compare-critere .compare-unite')).toBeVisible();
		else await expect(ligne.locator('th.compare-critere .compare-unite')).toBeHidden();
		await expect(ligne.locator('td .state-value').first()).toContainText('€/UC/an');
		await expect(ligne.getByText('Millésime 2021', { exact: true })).toHaveCount(0);

		// Divergent vintages: nothing on the face either; each cell keeps its own control, whose panel says its vintage.
		const divergente = page.locator('tr[data-cle="population"]');
		await expect(divergente.locator('.compare-meta-commune, .compare-meta')).toHaveCount(0);
		await expect(divergente.locator('th.compare-critere .source-button')).toHaveCount(0);
		await expect(divergente.locator('td .source-button')).toHaveCount(2);
		for (const [position, millesime] of ['2022', '2025'].entries()) {
			await divergente.locator('td .source-button').nth(position).click();
			const voletCellule = page.getByRole('dialog');
			await expect(voletCellule).toContainText(millesime);
			await page.keyboard.press('Escape');
			await expect(voletCellule).toBeHidden();
		}

		await expectSingleLineMatches(page.locator('table.compare-table'), `tableau communal à ${width} px`);
		if (width === 375) {
			const fibre = page.locator('tr[data-cle="fibre_pct"]');
			const colleges = page.locator('tr[data-cle="nb_etablissements_scolaires"]');
			await expectBadgeWordSingleLine(fibre, 'badges Fibre à 375 px');
			await expectBadgeWordSingleLine(
				colleges,
				'badges Collèges à 375 px'
			);
			await expectSourceButtonsContained(fibre, 'sources Fibre à 375 px');
			await expectSourceButtonsContained(colleges, 'sources Collèges à 375 px');
		}
		await expectSourceButtonsContained(ligne, `revenu médian à ${width} px`);
		await sansDebordement(page);
		const axe = await new AxeBuilder({ page })
			.withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
			.analyze();
		expect(axe.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual([]);

		await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
		await page.screenshot({
			path: tableEvidencePath(`commune-tableau-${width}.png`),
			animations: 'disabled'
		});
		await ligne.evaluate((element) => element.scrollIntoView({ block: 'center', behavior: 'instant' }));
		await page.screenshot({
			path: tableEvidencePath(`commune-meta-commune-${width}.png`),
			animations: 'disabled'
		});
		await divergente.evaluate((element) => element.scrollIntoView({ block: 'center', behavior: 'instant' }));
		await page.screenshot({
			path: tableEvidencePath(`commune-meta-divergente-${width}.png`),
			animations: 'disabled'
		});
		await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
		await depasserEntete(page);
		const bande = await page.locator('.compare-bande').boundingBox();
		expect(bande, `bande mesurable à ${width} px`).toBeTruthy();
		expect(bande!.height, `bande <= 1/5 à ${width} px`).toBeLessThanOrEqual(height / 5);
	}
});

test('two communes in one call: the gap shown is the served one, and 375 px needs no page scroll', async ({ page, request }) => {
	await configure(request, [comparaison(['44991', '44992'], DEUX)]);
	await page.setViewportSize({ width: 375, height: 800 });
	await open(page, '/comparer?communes=44991,44992');
	// UI-SPEC v4 § 6.4 (04.8-05): titled by the names beyond 720 px; at 375 px the short title of the
	// nature, the names in the header of the table (gap decided on 05/11 to keep UI-012).
	await expect(page.getByRole('heading', { level: 1, name: 'Comparer des communes', exact: true })).toBeVisible();
	await expect(region(page)).toBeVisible();
	expect(await lectures(request)).toEqual(['44991,44992']);

	const servi = DEUX.donnees.criteres.find((critere) => critere.cle === 'indicateurs_dvf.residentiel_maison.courante.mediane')!;
	const ecart = servi.cellules[1].ecart!;
	expect(norm(await cellule(page, servi.cle, '44992').locator('.compare-ecart').textContent())).toBe(`+${ecart.valeur} €/m² par rapport à Ancienne`);

	await sansDebordement(page);
	expect(await region(page).evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
});

test('the columns follow the link, SSR reads once per load, and a reload reads again', async ({ page, request }) => {
	await configure(request, [comparaison(['44991', '44992', '85991'], TROIS, 2)]);
	await open(page, '/comparer?communes=44991,44992,85991');
	expect(await entetes(page)).toEqual(['44991', '44992', '85991']);
	expect(await lectures(request)).toEqual(['44991,44992,85991']);
	await page.reload();
	await expect(region(page)).toBeVisible();
	expect(await lectures(request)).toEqual(['44991,44992,85991', '44991,44992,85991']);
});

test('la fenêtre DVF inclusive accompagne le prix médian publié sans changer les absences', async ({ page, request }) => {
	await configure(request, [comparaison(['44991', '44992', '85991'], TROIS, 2)]);
	for (const width of [1440, 375] as const) {
		await page.setViewportSize({ width, height: width === 1440 ? 900 : 800 });
		await open(page, '/comparer?communes=44991,44992,85991');
		// UI-SPEC v4 § 6.4 (04.8-05): titled by the names beyond 720 px, the short title of the nature under
		// it (gap decided on 05/11 to keep UI-012).
		const nomH1 = width === 1440 ? 'Comparer Ancienne, Courante et Couverture inconnue' : 'Comparer des communes';
		const titre = page.getByRole('heading', { level: 1, name: nomH1, exact: true });
		await expect(titre).toBeVisible();
		await expect(region(page)).toBeVisible();
		await page.waitForLoadState('networkidle');
		await page.evaluate(async () => {
			await document.fonts.ready;
			await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
		});
		await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
		const arrivee = await titre.evaluate((element) => {
			const rect = element.getBoundingClientRect();
			const header = document.querySelector('header.site-header')!.getBoundingClientRect();
			const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
			return {
				scrollY: window.scrollY,
				title: { top: rect.top, bottom: rect.bottom },
				headerBottom: header.bottom,
				viewportHeight: window.innerHeight,
				hit: hit !== null && element.contains(hit)
			};
		});
		expect(arrivee.scrollY, JSON.stringify(arrivee)).toBe(0);
		expect(arrivee.title.top, JSON.stringify(arrivee)).toBeGreaterThanOrEqual(arrivee.headerBottom);
		expect(arrivee.title.bottom, JSON.stringify(arrivee)).toBeLessThanOrEqual(arrivee.viewportHeight);
		expect(arrivee.hit, JSON.stringify(arrivee)).toBe(true);
		await page.screenshot({ path: tableEvidencePath(`cr01-premier-ecran-${width}.png`), animations: 'disabled' });

		const ligne = page.locator('tr[data-cle="prix_m2_median_appartement"]');
		const publiee = cellule(page, 'prix_m2_median_appartement', '44991');
		const sousSeuil = cellule(page, 'prix_m2_median_appartement', '44992');
		const horsCouverture = cellule(page, 'prix_m2_median_appartement', '85991');

		await expect(publiee.locator('.state-value')).toHaveText(`3${' '}040 €/m²`);
		// C3 (arbitrated by Ianis on 2026-11-10): the period is read in the panel of the cell, no longer under its value.
		await expect(publiee.locator('.compare-meta')).toHaveCount(0);
		await publiee.locator('.source-button').click();
		const voletPrix = page.getByRole('dialog');
		await expect(voletPrix).toContainText('2024-06-01 → 2025-06-01');
		await page.keyboard.press('Escape');
		await expect(voletPrix).toBeHidden();
		await expect(publiee).not.toContainText('Période de mesure non renseignée');
		await expect(sousSeuil).toHaveAttribute('data-state', 'insufficient_sample');
		await expect(sousSeuil.locator('.state-value')).toHaveCount(0);
		await expect(horsCouverture).toHaveAttribute('data-state', 'out_of_coverage');
		await expect(horsCouverture).toContainText('Hors couverture');
		await sansDebordement(page);

		await ligne.evaluate((element) => element.scrollIntoView({ block: 'center', behavior: 'instant' }));
		await page.screenshot({ path: tableEvidencePath(`cr01-prix-metadata-${width}.png`), animations: 'disabled' });

		const disposition = await ligne.evaluate((row) => {
			const box = (element: Element | null) => {
				if (!element) throw new Error('élément de preuve absent');
				const rect = element.getBoundingClientRect();
				return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
			};
			const overlaps = (a: ReturnType<typeof box>, b: ReturnType<typeof box>) =>
				a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
			const withheld = row.querySelector('td[data-code="44992"]')!;
			const cell = box(withheld);
			const badge = box(withheld.querySelector('.state-badge'));
			const source = box(withheld.querySelector('button[aria-label^="Détail"]'));
			const gap = box(withheld.querySelector('.compare-ecart'));
			const metadata = withheld.querySelector('.compare-meta');
			return {
				cell, badge, source, gap, metadataPresent: metadata !== null,
				badgeSourceOverlap: overlaps(badge, source),
				sourceGapOverlap: overlaps(source, gap)
			};
		});
		expect(disposition.source.left, JSON.stringify(disposition)).toBeGreaterThanOrEqual(disposition.cell.left - 1);
		expect(disposition.source.right, JSON.stringify(disposition)).toBeLessThanOrEqual(disposition.cell.right + 1);
		expect(disposition.badgeSourceOverlap, JSON.stringify(disposition)).toBe(false);
		expect(disposition.sourceGapOverlap, JSON.stringify(disposition)).toBe(false);
		expect(disposition.metadataPresent, JSON.stringify(disposition)).toBe(false);
		await expectWholeWords(sousSeuil, `prix appartement sous seuil à ${width} px`);
		await expectSourceButtonsContained(ligne, `source DVF 44 à ${width} px`);

		const source = sousSeuil.getByRole('button', { name: /^Détail : /i });
		await source.focus();
		await expect(source).toBeFocused();
		await page.screenshot({ path: tableEvidencePath(`cr01-source-focus-${width}.png`), animations: 'disabled' });
		await source.press('Enter');
		await expect(page.getByRole('dialog')).toBeVisible();
		await page.keyboard.press('Escape');
		await expect(source).toBeFocused();

		const axe = await new AxeBuilder({ page })
			.withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
			.analyze();
		expect(axe.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual([]);
	}
	expect(await lectures(request)).toEqual(['44991,44992,85991', '44991,44992,85991']);
});

test('every gap line recopies the served gap, reason or verdict of the fixture', async ({ page, request }) => {
	await configure(request, [comparaison(['44991', '44992', '85991', '44999'], TROIS_AVEC_INCONNU)]);
	await open(page, '/comparer?communes=44991,44992,85991,44999');
	await expect(page.getByText('Le lien contenait un code INSEE inconnu (44999) ; il a été écarté.')).toBeVisible();
	const affichees = await page.locator('tr[data-cle]').evaluateAll((rows) => rows.map((row) => ({
		cle: row.getAttribute('data-cle')!,
		cellules: [...row.querySelectorAll('td')].map((td) => ({
			code: td.getAttribute('data-code'),
			state: td.getAttribute('data-state'),
			texte: td.textContent?.replace(/\s+/g, ' ').trim() ?? null,
			ecart: td.querySelector('.compare-ecart')?.textContent?.replace(/\s+/g, ' ').trim() ?? null,
			warn: td.querySelector('.compare-ecart')?.classList.contains('compare-ecart-warn') ?? false
		}))
	})));
	expect(affichees.length).toBeGreaterThan(50);
	const parCle = new Map(TROIS_AVEC_INCONNU.donnees.criteres.map((critere) => [critere.cle, critere]));
	const colonnesHorsCouverture = new Set(
		TROIS_AVEC_INCONNU.donnees.communes.filter((commune) => commune.hebergement.etat === 'hors_couverture').map((commune) => commune.code_insee)
	);
	expect([...colonnesHorsCouverture]).toEqual(['85991']);
	let ecarts = 0;
	let identiques = 0;
	let horsCouverture = 0;
	for (const ligne of affichees) {
		const servi = parCle.get(ligne.cle)!;
		expect(ligne.cellules[0].ecart, `${ligne.cle}: the reference carries no gap`).toBeNull();
		for (const [position, affichee] of ligne.cellules.entries()) {
			if (position === 0) continue;
			const cellule = servi.cellules[position];
			expect(affichee.code).toBe(cellule.code_insee);
			if (colonnesHorsCouverture.has(cellule.code_insee) && cellule.etat === 'out_of_coverage') {
				// A column out of coverage says its state once per cell, typed, without a repeated gap (03-18 C-03).
				expect(cellule.motif_non_calcul, ligne.cle).toBe('commune_hors_couverture');
				expect(affichee.state, ligne.cle).toBe('out_of_coverage');
				expect(affichee.texte, ligne.cle).toBe('Hors couverture');
				expect(affichee.ecart, ligne.cle).toBeNull();
				horsCouverture++;
			} else if (cellule.ecart && cellule.identique_a_la_reference === true) {
				// C41: a gap served between two values served as the same reads « Identique », never « 0 … ».
				expect(affichee.ecart, ligne.cle).toBe('Identique');
				expect(affichee.warn).toBe(false);
				identiques++;
			} else if (cellule.ecart) {
				// Minus sign U+2212 (D-15 WR-04): a hyphen in the page fails here.
				const nombre = norm(`${cellule.ecart.valeur > 0 ? '+' : ''}${cellule.ecart.valeur.toLocaleString('fr-FR')}`.replace(/^-/, '−'));
				expect(affichee.ecart?.startsWith(`${nombre} `), `${ligne.cle}: « ${affichee.ecart} » for ${nombre}`).toBe(true);
				expect(affichee.ecart?.endsWith(' par rapport à Ancienne'), ligne.cle).toBe(true);
				expect(affichee.warn).toBe(false);
				ecarts++;
			} else if (cellule.motif_non_calcul === 'non_numerique') {
				expect(affichee.ecart).toBe(cellule.identique_a_la_reference ? 'Même valeur que Ancienne' : 'Valeur différente de Ancienne');
			} else {
				expect(affichee.ecart, ligne.cle).toBe(`Écart non calculé · ${MOTIFS[cellule.motif_non_calcul!]}`);
				expect(affichee.warn).toBe(true);
			}
		}
	}
	// The served gaps checked, as figures or (C41) as « Identique »: both kinds are present.
	expect(ecarts + identiques).toBeGreaterThan(5);
	expect(ecarts).toBeGreaterThan(0);
	expect(identiques).toBeGreaterThan(0);
	expect(horsCouverture).toBeGreaterThan(10);
	// The out-of-coverage commune keeps its column, with badge, reason and « ne vaut pas zéro » once in its header.
	const enTeteHorsCouverture = page.locator('thead th[data-code="85991"]');
	await expect(enTeteHorsCouverture.getByRole('link', { name: 'Couverture inconnue' })).toHaveAttribute('href', '/commune/85991');
	await expect(enTeteHorsCouverture.locator('.state-badge')).toHaveText('Hors couverture · département non hébergé');
	await expect(enTeteHorsCouverture.locator('.compare-hc-note')).toHaveText('Aucune valeur : ne vaut pas zéro.');
	await expect(page.getByText('territoire_hors_perimetre')).toHaveCount(0);
	await expect(page.getByText(/non disponible/i)).toHaveCount(0);

	const ligneEtats = page.locator('tr:has(td[data-state="not_qualified"]):has(td[data-state="out_of_coverage"])').first();
	await expect(ligneEtats).toBeVisible();
	await expect(ligneEtats.locator('td[data-state="not_qualified"]')).toHaveCount(2);
	await expect(ligneEtats.locator('td[data-state="out_of_coverage"]')).toHaveCount(1);
	for (const width of [1440, 375] as const) {
		await page.setViewportSize({ width, height: width === 1440 ? 900 : 800 });
		await ligneEtats.evaluate((element) => element.scrollIntoView({ block: 'center', behavior: 'instant' }));
		await page.screenshot({
			path: tableEvidencePath(`commune-etats-${width}.png`),
			animations: 'disabled'
		});
	}
});

test('« Retirer » and « Placer en premier » move focus to a column header; history restores each selection', async ({ page, request }) => {
	await configure(request, [
		comparaison(['44991', '44992', '85991'], TROIS, 2),
		comparaison(['44991', '85991'], colonnes(['44991', '85991']), 2),
		comparaison(['85991', '44991'], colonnes(['85991', '44991']))
	]);
	await open(page, '/comparer?communes=44991,44992,85991');
	await expect(page.getByRole('heading', { level: 2, name: 'Référence des écarts : Ancienne' })).toBeVisible();
	const retirer = page.getByRole('button', { name: 'Retirer Courante', exact: true });
	// Reached by Tab, never focused by the test: from the name of its column, « Placer en premier », then « Retirer ».
	await page.locator('thead th[data-code="44992"] a.compare-nom').focus();
	await page.keyboard.press('Tab');
	await expect(page.getByRole('button', { name: 'Placer Courante en premier', exact: true })).toBeFocused();
	await page.keyboard.press('Tab');
	await expect(retirer).toBeFocused();
	await page.keyboard.press('Enter');
	await expect(page).toHaveURL(/communes=44991,85991$/);
	await expect(page.locator('thead th[data-code="85991"]')).toBeFocused();
	await expect(page.getByRole('status').filter({ hasText: 'Courante retirée de la comparaison.' })).toBeVisible();

	await page.getByRole('button', { name: 'Placer Couverture inconnue en premier', exact: true }).click();
	await expect(page).toHaveURL(/communes=85991,44991$/);
	expect(await entetes(page)).toEqual(['85991', '44991']);
	await expect(
		page.getByRole('heading', { level: 2, name: 'Référence des écarts : Couverture inconnue' })
	).toBeVisible();
	await expect(page.locator('thead th[data-code="85991"]')).toBeFocused();

	await page.goBack();
	await expect(page).toHaveURL(/communes=44991,85991$/);
	await expect.poll(() => entetes(page)).toEqual(['44991', '85991']);
	await page.goBack();
	await expect.poll(() => entetes(page)).toEqual(['44991', '44992', '85991']);
	expect(await lectures(request)).toEqual(['44991,44992,85991', '44991,85991', '85991,44991', '44991,85991', '44991,44992,85991']);
});

test('an altered link: duplicate and malformed codes dropped with a message, the address rewritten', async ({ page, request }) => {
	await configure(request, [comparaison(['44991', '44992'], DEUX)]);
	await open(page, '/comparer?communes=44991,44991,%3Cscript%3E,44992');
	const notice = page.getByRole('heading', { name: 'Lien altéré' }).locator('..');
	await expect(notice).toContainText('Le lien contenait deux fois la même commune (44991) ; le doublon a été écarté.');
	await expect(notice).toContainText('Le lien contenait un code INSEE inconnu (<SCRIPT>) ; il a été écarté.');
	await expect(page.locator('.comparer script, .comparer img')).toHaveCount(0);
	await expect(page).toHaveURL(/\/comparer\?communes=44991,44992$/);
	expect(await lectures(request)).toEqual(['44991,44992']);
});

test('four communes, 375 px: the page scrolls, the region scrolls horizontally only, criteria stick left, the header band follows', async ({ page, request }) => {
	await configure(request, [comparaison(['44991', '44992', '85991', '44993'], quatre())]);
	await page.setViewportSize({ width: 375, height: 800 });
	await open(page, '/comparer?communes=44991,44992,85991,44993,44994');
	await expect(page.getByText('Seules les quatre premières communes du lien sont affichées.')).toBeVisible();
	expect(await entetes(page)).toEqual(['44991', '44992', '85991', '44993']);
	// 04.5-09 (B5): the add control and the sentence of the limit live in « Modifier la sélection ».
	await ouvrirModification(page);
	const ajouter = page.getByRole('button', { name: 'Ajouter une commune' });
	await expect(ajouter).toBeDisabled();
	await expect(page.getByText('Quatre communes au maximum : retirez-en une pour en ajouter une autre.')).toBeVisible();
	await page.locator('details.comparer-modifier > summary').click();
	await expect(page.locator('details.comparer-modifier')).not.toHaveAttribute('open', '');

	await sansDebordement(page);
	const zone = region(page);
	const cadre = page.locator('.compare-cadre');
	expect(await zone.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);
	// No frame of limited height (C-01): the region never scrolls vertically, the page does.
	expect(await zone.evaluate((element) => getComputedStyle(element).maxHeight)).toBe('none');
	expect(await zone.evaluate((element) => element.scrollHeight <= element.clientHeight + 1)).toBe(true);
	expect(await page.evaluate(() => document.scrollingElement!.scrollHeight > window.innerHeight)).toBe(true);
	// Hidden columns are announced above the table and signalled at the right edge (C-04).
	await expect(cadre).toHaveAttribute('data-defilable', 'true');
	await expect(cadre).toHaveAttribute('data-fin', 'false');
	const annonce = page.locator('.compare-defilement');
	await expect(annonce).toBeVisible();
	await expect(annonce).toContainText('4 communes : faites défiler le tableau vers la droite pour voir');
	await expect(annonce).toContainText('Quatrième commune');
	// Column header with a column out of coverage: measured and recorded only. The fifth of the
	// window applies to what stays on screen while reading, the band (Ianis, 2026-09-29, option (a));
	// the real header scrolls with the page. Without any column out of coverage it is capped (next test).
	const ligneEntete = (await page.locator('thead tr').boundingBox())!.height;
	const plafond = await page.evaluate(() => window.innerHeight / 5);
	console.log(`RELEVE e2e 375px 4 communes hors_couverture=1 entete_colonnes=${ligneEntete} plafond_bande=${plafond}`);
	// Non-regression guard, not the R-2 criterion (D-15 WR-03): option (a) must not leave the real
	// header unbounded; two fifths of the window (273.5 px measured on 2026-09-29).
	expect(ligneEntete, `garde de non-régression : thead ${ligneEntete}px > ${2 * plafond}px`).toBeLessThanOrEqual(2 * plafond);
	// 44 × 44 px actions (C-13).
	for (const nom of ['Placer Courante en premier', 'Retirer Courante']) {
		const boite = (await page.getByRole('button', { name: nom, exact: true }).boundingBox())!;
		console.log(`RELEVE e2e 375px cible « ${nom} » ${boite.width}x${boite.height}`);
		expect(boite.width, nom).toBeGreaterThanOrEqual(44);
		expect(boite.height, nom).toBeGreaterThanOrEqual(44);
	}
	// Keyboard: the region takes the focus and scrolls with the arrows.
	await zone.focus();
	await page.keyboard.press('ArrowRight');
	await page.keyboard.press('ArrowRight');
	await expect.poll(() => zone.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
	await zone.evaluate((element) => { element.scrollLeft = 0; });

	// Real header in view: the band stays hidden, or it would cover that header and its buttons (D-15 WR-01).
	// 04-10: the nature switch adds one line of 44 px above the table, so with four communes, the limit
	// sentence and two notices the real header starts below the first screen of 375 x 800 (852 px
	// measured; 808 px before 04-10, in view only through the reveal of the focused region). Checked
	// at the top of the page, then with the real header brought just under the site header, the last
	// position before the band takes over.
	await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
	await expect(page.locator('.compare-bande')).toHaveCSS('visibility', 'hidden');
	await page.evaluate(() => {
		const sous = document.querySelector('header.site-header')!.getBoundingClientRect().bottom + 8;
		const haut = document.querySelector('thead')!.getBoundingClientRect().top;
		window.scrollTo({ top: window.scrollY + haut - sous, behavior: 'instant' });
	});
	await expect(page.locator('thead')).toBeInViewport();
	await expect(page.locator('.compare-bande')).toHaveCSS('visibility', 'hidden');

	// The page scrolls past the real header: the band shows under the site header, the table does
	// not move (G1), and each band cell sits on its `th` (position and width).
	const avant = await hautDuTableau(page);
	await depasserEntete(page);
	expect(Math.abs((await hautDuTableau(page)) - avant), 'the band pushes nothing').toBeLessThanOrEqual(1);
	const debut = await boites(page, '44992');
	expect(Math.abs(debut.haut - debut.basEnTete), JSON.stringify(debut)).toBeLessThanOrEqual(2);
	alignees(debut, 'scrollLeft=0');
	// What sticks while reading takes a fifth of the window at most (R-2; option (a) of 2026-09-29).
	const hauteurBande = (await page.locator('.compare-bande').boundingBox())!.height;
	console.log(`RELEVE e2e 375px 4 communes bande=${hauteurBande} plafond=${plafond} haut=${debut.haut} bas_en_tete=${debut.basEnTete}`);
	expect(hauteurBande, `bande ${hauteurBande}px > ${plafond}px`).toBeLessThanOrEqual(plafond);
	await expect(page.locator('.compare-bande')).toHaveAttribute('aria-hidden', 'true');

	// Scrolled to the far right: the criteria column stays at the left edge of the region, the band
	// follows the region, the right-edge hint goes away. Measured in one frame, inside the page.
	const mesures = await zone.evaluate(async (element) => {
		element.scrollLeft = element.scrollWidth;
		await new Promise((fin) => requestAnimationFrame(() => requestAnimationFrame(fin)));
		const zoneBox = element.getBoundingClientRect();
		const critereBox = element.querySelector('tr[data-cle="revenu_median"] th[scope="row"]')!.getBoundingClientRect();
		return { gauche: element.scrollLeft, critere: critereBox.left - zoneBox.left };
	});
	expect(mesures.gauche, JSON.stringify(mesures)).toBeGreaterThan(100);
	expect(Math.abs(mesures.critere), JSON.stringify(mesures)).toBeLessThan(2);
	await expect(cadre).toHaveAttribute('data-fin', 'true');
	await expect.poll(async () => {
		const fin = await boites(page, '44992');
		return Math.max(Math.abs(fin.bande.left - fin.th.left), Math.abs(fin.bande.width - fin.th.width));
	}).toBeLessThanOrEqual(2);
	alignees(await boites(page, '44992'), `scrollLeft=${mesures.gauche}`);
	await sansDebordement(page);

	// The whole table left above the site header: the band hides again (D-15 WR-01). Below the table
	// the page holds less than a window at 375 × 800: room is added under the page, in the test only.
	const depasse = await page.evaluate(() => {
		document.body.style.paddingBottom = `${window.innerHeight}px`;
		// One pixel past the table: the window scrolls by whole pixels, the table bottom is fractional.
		window.scrollTo({ top: window.scrollY + Math.ceil(document.querySelector('table.compare-table')!.getBoundingClientRect().bottom) + 1, behavior: 'instant' });
		return {
			basTableau: document.querySelector('table.compare-table')!.getBoundingClientRect().bottom,
			// Visible bottom of the site header (0 once scrolled away, A-01).
			basEnTete: Math.max(0, document.querySelector('header.site-header')!.getBoundingClientRect().bottom)
		};
	});
	expect(depasse.basTableau, JSON.stringify(depasse)).toBeLessThanOrEqual(depasse.basEnTete);
	await expect(page.locator('.compare-bande')).toHaveCSS('visibility', 'hidden');
});

test('375 px: keyboard focus inside the table never lands under the header band', async ({ page, request }) => {
	await configure(request, [comparaison(['44991', '44992', '85991', '44993'], quatre())]);
	await page.setViewportSize({ width: 375, height: 800 });
	await open(page, '/comparer?communes=44991,44992,85991,44993');
	await expect(region(page)).toBeVisible();
	await depasserEntete(page);
	// An i button of the body placed just under the site header, where the band lies, then reached
	// backwards (Shift+Tab from the next focusable), as when reading up the table (D-15 WR-02).
	const cible = page.locator('.compare-region tbody td[data-code="44991"] button').nth(10);
	await cible.evaluate((element) => {
		// Visible bottom of the site header (0 once scrolled away, A-01): where the band lies.
		const basEnTete = Math.max(0, document.querySelector('header.site-header')!.getBoundingClientRect().bottom);
		window.scrollTo({ top: window.scrollY + element.getBoundingClientRect().top - basEnTete - 4, behavior: 'instant' });
		const tous = [...document.querySelectorAll<HTMLElement>('.compare-region a[href], .compare-region button:not([disabled])')];
		tous[tous.indexOf(element as HTMLElement) + 1].focus({ preventScroll: true });
	});
	await expect(page.locator('.compare-bande')).toHaveCSS('visibility', 'visible');
	await page.keyboard.press('Shift+Tab');
	await expect(cible).toBeFocused();
	const mesure = () =>
		cible.evaluate((element) => {
			const boite = element.getBoundingClientRect();
			const bande = document.querySelector('.compare-bande')!.getBoundingClientRect();
			const dessus = document.elementFromPoint(boite.left + boite.width / 2, boite.top + boite.height / 2);
			return { haut: boite.top, basBande: bande.bottom, degage: boite.top >= bande.bottom - 1 && !!dessus && element.contains(dessus) };
		});
	await expect.poll(async () => (await mesure()).degage, { message: 'focused i button under the band' }).toBe(true);
	console.log(`RELEVE e2e 375px focus_sous_bande ${JSON.stringify(await mesure())}`);
});

test('375 px, four hosted communes with long names: the column header takes a fifth of the window at most', async ({ page, request }) => {
	// SYNTHETIC: the two hosted communes of the fixture, plus two copies under long homonym names.
	const reponse = structuredClone(DEUX);
	for (const [code, nom] of [['44993', 'Saint-Philbert-de-Grand-Lieu'], ['44994', 'Saint-Philbert-de-Bouaine']]) {
		const modele = structuredClone(reponse.donnees.communes[1]);
		modele.code_insee = code;
		modele.nom = nom;
		reponse.donnees.communes.push(modele);
		for (const critere of reponse.donnees.criteres) critere.cellules.push({ ...critere.cellules[1], code_insee: code });
	}
	await configure(request, [comparaison(['44991', '44992', '44993', '44994'], reponse)]);
	await page.setViewportSize({ width: 375, height: 800 });
	await open(page, '/comparer?communes=44991,44992,44993,44994');
	await expect(region(page)).toBeVisible();
	await expect(page.locator('thead .compare-hc-note')).toHaveCount(0);
	const ligneEntete = (await page.locator('thead tr').boundingBox())!.height;
	const plafond = await page.evaluate(() => window.innerHeight / 5);
	console.log(`RELEVE e2e 375px 4 communes hebergees entete_colonnes=${ligneEntete} plafond=${plafond}`);
	expect(ligneEntete, `thead ${ligneEntete}px > ${plafond}px`).toBeLessThanOrEqual(plafond);
	// The full names stay readable in the real header: they wrap, they are never cut.
	await expect(page.locator('thead th[data-code="44993"] .compare-nom')).toHaveText('Saint-Philbert-de-Grand-Lieu');
	expect(await page.locator('thead th[data-code="44993"]').evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
	await depasserEntete(page);
	const hauteurBande = (await page.locator('.compare-bande').boundingBox())!.height;
	expect(hauteurBande, `bande ${hauteurBande}px > ${plafond}px`).toBeLessThanOrEqual(plafond);
});

test('the header band matches the real column widths at every width', async ({ page, request }) => {
	const cas = [
		{ largeur: 1280, reponse: DEUX, codes: ['44991', '44992'] },
		{ largeur: 768, reponse: TROIS, codes: ['44991', '44992', '85991'] },
		{ largeur: 375, reponse: quatre(), codes: ['44991', '44992', '85991', '44993'] }
	];
	await configure(request, cas.map(({ reponse, codes }) => comparaison(codes, reponse)));
	for (const { largeur, codes } of cas) {
		await page.setViewportSize({ width: largeur, height: 800 });
		await open(page, `/comparer?communes=${codes.join(',')}`);
		await expect(region(page)).toBeVisible();
		await depasserEntete(page);
		// At 1280 px with two communes the table is stretched to the region (columns far wider than
		// 224 px): a band of fixed widths would drift from the second column on.
		const largeurs: string[] = [];
		for (const code of ['coin', ...codes]) {
			const mesure = await boites(page, code);
			largeurs.push(`${code}=${Math.round(mesure.th.width)}`);
			alignees(mesure, `${largeur}px ${code}`);
		}
		console.log(`RELEVE e2e ${largeur}px bande ${largeurs.join(' ')}`);
	}
});

test('the header band tells homonyms and long shared prefixes apart at 375, 768 and 1280 px', async ({ page, request }) => {
	// SYNTHETIC names and departements on the served shape (D-15 CR-01): the pair of Ianis's task 3
	// (85262 and 44188) and two real homonyms, Le Pin (35) and Le Pin (79), the second out of coverage.
	const renommer = (base: Reponse, noms: [string, string][]) => {
		const copie = structuredClone(base);
		copie.donnees.communes.forEach((commune, index) => Object.assign(commune, { nom: noms[index][0], departement: noms[index][1] }));
		return copie;
	};
	const paires = [
		{ codes: ['44991', '44992'], reponse: renommer(DEUX, [['Saint-Philbert-de-Bouaine', '85'], ['Saint-Philbert-de-Grand-Lieu', '44']]) },
		{ codes: ['44992', '85991'], reponse: renommer(colonnes(['44992', '85991']), [['Le Pin', '35'], ['Le Pin', '79']]) }
	];
	await configure(request, paires.map(({ codes, reponse }) => comparaison(codes, reponse, 3)));
	for (const { codes } of paires) {
		for (const largeur of [375, 768, 1280]) {
			await page.setViewportSize({ width: largeur, height: 800 });
			await open(page, `/comparer?communes=${codes.join(',')}`);
			await expect(region(page)).toBeVisible();
			await depasserEntete(page);
			const textes = await textesVisiblesDeLaBande(page);
			console.log(`RELEVE e2e ${largeur}px bande_textes=${JSON.stringify(textes)}`);
			expect(textes, `${largeur}px`).toHaveLength(2);
			expect(textes.every((texte) => texte.length > 0), `${largeur}px ${JSON.stringify(textes)}`).toBe(true);
			expect(new Set(textes).size, `${largeur}px: band cells the reader cannot tell apart ${JSON.stringify(textes)}`).toBe(textes.length);
		}
	}
});

test('« Différences seulement » hides what the API served as identical, kept by the link and the copy', async ({ page, request, context }) => {
	await context.grantPermissions(['clipboard-read', 'clipboard-write']);
	await configure(request, [comparaison(['44991', '44992'], DEUX, 2)]);
	await open(page, '/comparer?communes=44991,44992');
	const avant = await page.locator('tr[data-cle]').count();
	await ouvrirModification(page);
	await page.getByRole('checkbox', { name: 'Différences seulement' }).check();
	await expect(page).toHaveURL(/communes=44991,44992&diff=1$/);
	await page.locator('details.comparer-lecture > summary').click();
	const liensGroupes = page
		.getByRole('navigation', { name: 'Accès aux groupes de comparaison' })
		.getByRole('link');
	const hrefsGroupes = await liensGroupes.evaluateAll((nodes) =>
		nodes.map((node) => node.getAttribute('href')!)
	);
	expect(new Set(hrefsGroupes).size).toBe(hrefsGroupes.length);
	expect(
		await page.locator('tbody[data-groupe]').evaluateAll((nodes) =>
			nodes.map((node) => `#${node.querySelector('th[scope="rowgroup"]')!.id}`)
		)
	).toEqual(hrefsGroupes);
	const identiques = await page.locator('tr[data-cle]').evaluateAll((rows) => rows.filter((row) => row.getAttribute('data-identiques') === 'true').length);
	expect(identiques).toBe(0);
	const apres = await page.locator('tr[data-cle]').count();
	expect(apres).toBeGreaterThan(0);
	expect(apres).toBeLessThan(avant);
	await expect(page.getByText(`${avant - apres} critères identiques dans toutes les colonnes sont masqués.`)).toBeVisible();
	expect(await lectures(request)).toEqual(['44991,44992']);

	await page.getByRole('button', { name: 'Copier le lien', exact: true }).click();
	await expect(page.getByRole('status').filter({ hasText: 'Lien copié.' })).toHaveText('Lien copié. Il reprend les communes, leur ordre et le filtre ; les valeurs sont relues à l’ouverture.');
	const copie = new URL(await page.evaluate(() => navigator.clipboard.readText()));
	expect(copie.pathname + copie.search).toBe('/comparer?communes=44991,44992&diff=1');
	await open(page, copie.href);
	await ouvrirModification(page);
	await expect(page.getByRole('checkbox', { name: 'Différences seulement' })).toBeChecked();
	await expect(page.locator('tr[data-cle]')).toHaveCount(apres);
});

test('« Différences seulement » says why nothing is hidden when a commune is out of coverage', async ({ page, request }) => {
	await configure(request, [comparaison(['44991', '44992', '85991'], TROIS)]);
	await open(page, '/comparer?communes=44991,44992,85991&diff=1');
	await expect(page.getByText('Aucun critère n’est masqué : Couverture inconnue est hors couverture, donc aucun critère n’est identique dans toutes les colonnes.')).toBeVisible();
	await expect(page.locator('tr[data-cle]')).toHaveCount(TROIS.donnees.criteres.length - 27);
});

test('adding a commune from the in-page search: link written, confirmation announced, columns kept meanwhile', async ({ page, request }) => {
	const couverture = { code_insee: '85991', nom: 'Couverture inconnue', code_postal: null, departement: '85', population: 10, population_annee: null, population_source: 'geo_communes', hebergement: { etat: 'hors_couverture', motif: 'territoire_hors_perimetre', departements: [] } };
	await configure(request, [
		comparaison(['44991', '44992'], DEUX),
		recherche('Couv', [couverture]),
		comparaison(['44991', '44992', '85991'], TROIS)
	]);
	await open(page, '/comparer?communes=44991,44992');
	await ouvrirModification(page);
	await page.getByRole('button', { name: 'Ajouter une commune' }).click();
	const champ = page.getByRole('combobox', { name: 'Rechercher une commune' });
	await expect(champ).toBeFocused();
	await champ.fill('Couv');
	await expect(page.getByRole('listbox', { name: 'Communes proposées' })).toBeVisible();
	await champ.press('ArrowDown');
	await champ.press('Enter');
	await expect(page).toHaveURL(/communes=44991,44992,85991$/);
	await expect(page.getByRole('status').filter({ hasText: 'ajoutée' })).toHaveText('Couverture inconnue ajoutée à la comparaison (3/4).');
	expect(await entetes(page)).toEqual(['44991', '44992', '85991']);
	await expect(page.getByRole('button', { name: 'Ajouter une commune' })).toBeFocused();
});

test('communes set aside from the results open in the comparator from the header link', async ({ page, request }) => {
	const resultat = (commune: Commune) => ({
		code_insee: commune.code_insee, nom: commune.nom, code_postal: null, departement: commune.departement,
		population: 100, population_annee: null, population_source: 'geo_communes', hebergement: commune.hebergement
	});
	const [ancienne, courante, horsCouverture] = TROIS.donnees.communes;
	await configure(request, [
		{ method: 'GET', pathname: '/v1/communes/search', query: { q: 'commune', limit: '20', inclure_arrondissements: 'true' }, minCalls: 1, maxCalls: 1,
			responses: [{ status: 200, body: { statut: 'ok', donnees: [ancienne, courante, horsCouverture].map(resultat) } }] },
		// The results page reads the addresses of the same query too (04-17): nothing in a hosted BAN.
		{ method: 'GET', pathname: '/v1/adresses/recherche', query: { q: 'commune', limite: '8' }, minCalls: 1, maxCalls: 1,
			responses: [{ status: 200, body: { statut: 'ok', donnees: { ordre_types: ['commune', 'adresse'], resultats: [], hebergement: { ban: { etat: 'heberge', motif: null, departements: ['35'] } } } } }] },
		comparaison(['44991', '44992', '85991'], TROIS)
	]);
	await open(page, '/communes?q=commune');
	for (const code of ['44991', '44992', '85991']) {
		await page.locator(`li.resultat[data-code-insee="${code}"]`).getByRole('button', { name: 'Ajouter à la comparaison' }).click();
	}
	const lien = page.getByRole('navigation', { name: 'Navigation principale' }).getByRole('link', { name: /^Comparer/ });
	await expect(lien).toContainText('3');
	await lien.click();
	await expect(page).toHaveURL(/\/comparer\?communes=44991,44992,85991$/);
	await expect(region(page)).toBeVisible();
	expect(await entetes(page)).toEqual(['44991', '44992', '85991']);
	await expect(lien).toContainText('3');
	expect(await lectures(request)).toEqual(['44991,44992,85991']);
});

test('empty and single-commune states follow the contract, without any comparison call', async ({ page, request }) => {
	await configure(request, [referentiel('44991', 'Ancienne')]);
	await open(page, '/comparer');
	await expect(page.getByRole('heading', { level: 2, name: 'Choisissez deux communes à comparer' })).toBeVisible();
	await expect(page.getByText('Ajoutez-les ici ou depuis une fiche.')).toBeVisible();
	await expect(region(page)).toHaveCount(0);
	await open(page, '/comparer?communes=44991');
	await expect(page.getByRole('link', { name: 'Ancienne' })).toHaveAttribute('href', '/commune/44991');
	await expect(page.getByText('Ajoutez une deuxième commune pour lire les écarts.')).toBeVisible();
	await expect(region(page)).toHaveCount(0);
	expect(await lectures(request)).toEqual([]);
});

test('an outage shows one warn notice and nothing partial; « Recharger la comparaison » reads again', async ({ page, request }) => {
	await configure(request, [comparaison(['44991', '44992'], DEUX, 2, [{ status: 503, body: {} }, { status: 200, body: DEUX }])]);
	await open(page, '/comparer?communes=44991,44992');
	const alerte = page.getByRole('alert');
	await expect(alerte.getByRole('heading', { name: 'Les données de comparaison sont momentanément indisponibles.' })).toBeVisible();
	await expect(region(page)).toHaveCount(0);
	await expect(page.locator('td')).toHaveCount(0);
	await page.getByRole('button', { name: 'Recharger la comparaison' }).click();
	await expect(region(page)).toBeVisible();
	await expect(page.getByRole('alert')).toHaveCount(0);
	expect(await lectures(request)).toEqual(['44991,44992', '44991,44992']);
});

test('the explanation of a row opens its panel in one action, and gives the focus back', async ({ page, request }) => {
	await configure(request, [comparaison(['44991', '44992'], DEUX)]);
	await open(page, '/comparer?communes=44991,44992');
	const bouton = page.getByRole('button', { name: 'Détail : Revenu déclaré médian', exact: true });
	await expect(bouton).not.toHaveAttribute('title');
	await bouton.click();
	const volet = page.getByRole('dialog');
	await expect(volet.getByRole('heading', { name: 'Revenu déclaré médian', exact: true })).toBeFocused();
	await expect(volet).toContainText('Définition');
	await expect(volet).toContainText('Source');
	await page.keyboard.press('Escape');
	await expect(volet).toBeHidden();
	await expect(bouton).toBeFocused();
});

// Parity fiche ↔ comparator (03-08 review, tightened in 03-10): for every row of the fiche, the
// comparator row(s) of the same `data-ligne` in the commune's column show the same STATE, the same
// badge (label of the state or of its reason) and, for a row that is one served value, the same
// text. Rows written as a sentence on the fiche (DPE population and distribution) or split into
// several criteria (P10, P50, P90) compare state and badge only.
type LigneFiche = { ligne: string; state: string | null; badge: string | null; value: string | null };
type LigneComparee = { ligne: string; cle: string; state: string | null; badge: string | null; value: string | null };
const PHRASES_DE_FICHE = new Set(['dpe-population', 'dpe-distribution']);
/** Fiche rows no criterion carries: a period (loyers.annee), the bounds of the DPE dates. */
const SANS_CRITERE = ['dpe-periode', 'loyers.annee'];

async function lignesFiche(page: Page): Promise<LigneFiche[]> {
	// 4.9: the rows of the fiche are the `dl.tab` rows of its categories, each row once.
	return page.locator('details.cat dl.tab > .tab-l').evaluateAll((rows) => rows.map((row) => {
		const text = (element: Element | null) => element?.textContent?.replace(/\s+/g, ' ').trim() ?? null;
		const valeur = row.querySelector(':scope > dd > .tab-v > .state-value');
		const absence = row.querySelector(':scope > dd > .tab-v > .state-absence');
		return {
			ligne: row.getAttribute('data-ligne')!,
			state: valeur ? 'value' : absence?.getAttribute('data-state') ?? null,
			badge: text(absence?.querySelector('.state-badge') ?? null),
			value: text(valeur)
		};
	}));
}
async function lignesComparees(page: Page, code: string): Promise<LigneComparee[]> {
	return page.locator('tr[data-ligne]').evaluateAll((rows, colonne) => rows.map((row) => {
		const text = (element: Element | null) => element?.textContent?.replace(/\s+/g, ' ').trim() ?? null;
		const td = row.querySelector(`td[data-code="${colonne}"]`)!;
		return {
			ligne: row.getAttribute('data-ligne')!, cle: row.getAttribute('data-cle')!,
			state: td.getAttribute('data-state'), badge: text(td.querySelector('.state-badge')), value: text(td.querySelector('.state-value'))
		};
	}), code);
}
function assertParite(fiche: LigneFiche[], comparees: LigneComparee[]): number {
	let comparees_ = 0;
	const sans: string[] = [];
	for (const ligne of fiche) {
		const lignes = comparees.filter((item) => item.ligne === ligne.ligne);
		if (!lignes.length) { sans.push(ligne.ligne); continue; }
		for (const item of lignes) {
			if (item.state !== ligne.state) throw new Error(`${item.cle}: fiche ${ligne.state}, comparator ${item.state}`);
			if (ligne.state !== 'value' && item.badge !== ligne.badge) throw new Error(`${item.cle}: fiche « ${ligne.badge} », comparator « ${item.badge} »`);
		}
		if (ligne.state === 'value' && lignes.length === 1 && !PHRASES_DE_FICHE.has(ligne.ligne) && lignes[0].value !== ligne.value) {
			throw new Error(`${lignes[0].cle}: fiche « ${ligne.value} », comparator « ${lignes[0].value} »`);
		}
		comparees_++;
	}
	expect(sans.sort(), 'fiche rows without a comparator row').toEqual(SANS_CRITERE);
	return comparees_;
}

test('fiche and comparator agree on state, reason and value for every fiche row', async ({ page, request }) => {
	await configure(request, [fiche('44991'), fiche('44992'), comparaison(['44991', '44992'], DEUX)]);
	await open(page, '/commune/44991');
	const ficheA = await lignesFiche(page);
	await open(page, '/commune/44992');
	const ficheB = await lignesFiche(page);
	expect(ficheA.length).toBeGreaterThan(30);
	await open(page, '/comparer?communes=44991,44992');
	const colonneA = await lignesComparees(page, '44991');
	const colonneB = await lignesComparees(page, '44992');
	expect(assertParite(ficheA, colonneA)).toBeGreaterThan(30);
	expect(assertParite(ficheB, colonneB)).toBeGreaterThan(30);
	expect(ficheA.some((ligne) => ligne.state !== 'value'), 'the fixture must exercise absences').toBe(true);

	// The same oracle rejects a changed state, a changed badge and a changed value (copies of the
	// captured DOM evidence, never the application).
	const absente = colonneA.findIndex((item) => item.state !== 'value' && item.state !== null && !PHRASES_DE_FICHE.has(item.ligne));
	const etat = structuredClone(colonneA); etat[absente].state = 'not_reported' === etat[absente].state ? 'not_available' : 'not_reported';
	expect(() => assertParite(ficheA, etat)).toThrow();
	const badge = structuredClone(colonneA); badge[absente].badge = 'Non renseigné · autre motif';
	expect(() => assertParite(ficheA, badge)).toThrow();
	const valeur = structuredClone(colonneA);
	const servie = valeur.findIndex((item) => item.state === 'value' && valeur.filter((other) => other.ligne === item.ligne).length === 1 && !PHRASES_DE_FICHE.has(item.ligne));
	valeur[servie].value = '999 999';
	expect(() => assertParite(ficheA, valeur)).toThrow();
});

test('long hostile names stay text and wrap in a 124 px header at 375 px; 1280 px fits three columns', async ({ page, request }, testInfo) => {
	const nom = 'Saint-Commune-au-Nom-Très-Long-et-Composé <img src=x onerror=alert(1)>';
	const reponse = colonnes(['44991', '44992', '85991']);
	reponse.donnees.communes[1].nom = nom;
	await configure(request, [comparaison(['44991', '44992', '85991'], reponse, 2)]);
	await page.setViewportSize({ width: 375, height: 800 });
	await open(page, '/comparer?communes=44991,44992,85991');
	await expect(page.locator('.comparer img')).toHaveCount(0);
	const entete = page.locator('thead th[data-code="44992"]');
	await expect(entete.getByRole('link', { name: nom })).toBeVisible();
	const boite = (await entete.boundingBox())!;
	expect(boite.width).toBeLessThan(140);
	expect(await entete.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
	expect(await entete.locator('.compare-nom').evaluate((element) => getComputedStyle(element).textOverflow)).not.toBe('ellipsis');
	await sansDebordement(page);
	await page.screenshot({ path: testInfo.outputPath('comparateur-375.png'), fullPage: true });

	await page.setViewportSize({ width: 1280, height: 900 });
	await page.reload();
	await expect(region(page)).toBeVisible();
	expect(await region(page).evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
	expect((await entete.boundingBox())!.width).toBeGreaterThanOrEqual(224);
	await sansDebordement(page);
	await page.screenshot({ path: testInfo.outputPath('comparateur-1280.png'), fullPage: true });
});

test('printed whole: no scrolling, no sticky header, no control', async ({ page, request }, testInfo) => {
	await configure(request, [comparaison(['44991', '44992', '85991', '44993'], quatre())]);
	await page.setViewportSize({ width: 1280, height: 900 });
	await open(page, '/comparer?communes=44991,44992,85991,44993');
	await page.emulateMedia({ media: 'print' });
	const zone = region(page);
	expect(await zone.evaluate((element) => getComputedStyle(element).overflowX)).toBe('visible');
	expect(await zone.evaluate((element) => getComputedStyle(element).maxHeight)).toBe('none');
	expect(await page.locator('thead th').first().evaluate((element) => getComputedStyle(element).position)).toBe('static');
	expect(await page.locator('th[scope="row"]').first().evaluate((element) => getComputedStyle(element).position)).toBe('static');
	// The duplicated header band is never printed (the real `thead` is).
	expect(await page.locator('.compare-bande-ancre').evaluate((element) => getComputedStyle(element).display)).toBe('none');
	await expect(page.locator('.compare-defilement')).toBeHidden();
	for (const nom of ['Ajouter une commune', 'Copier le lien', 'Retirer Ancienne']) {
		await expect(page.getByRole('button', { name: nom })).toBeHidden();
	}
	await expect(page.locator('tr[data-cle]').last()).toBeVisible();
	await page.screenshot({ path: testInfo.outputPath('comparateur-impression.png'), fullPage: true });
	await page.pdf({ path: testInfo.outputPath('comparateur-a4-paysage.pdf'), format: 'A4', landscape: true, printBackground: true });
});

// --- B5 (plan 04.5-09, UI-012; D45-01, D45-11): header of the comparator -------------------------
//
// Inputs wired in the fixtures (amendment 2 of the counter-review): `communes=44991,44992,85991`
// (`trois_communes_dont_hors_couverture` without its unknown code), at 1 440 × 900 and 375 × 812.
// Captured at the starting SHA of plan 09 (d14f433d) and after, same input (`DC_REFONTE_D_EVIDENCE_DIR`,
// the test output otherwise). The comparator has no export (pre-existing, plan 09 amendment 5): the
// shared content kept is the copied link and the printed page.

const B5_COMMUNES = ['44991', '44992', '85991'];
const B5_LARGEURS = [[1440, 900], [375, 812]] as const;

function preuveB5(nom: string): string {
	const dossier = process.env.DC_REFONTE_D_EVIDENCE_DIR;
	if (!dossier) return test.info().outputPath(nom);
	mkdirSync(dossier, { recursive: true });
	return join(dossier, nom);
}

/**
 * Lines printed outside the table (print media, after `beforeprint`), whitespace normalised. A child
 * hidden in print is skipped: `innerText` of an element that is not rendered returns its `textContent`.
 */
function lignesImprimees(page: Page): Promise<string[]> {
	return page.evaluate(() =>
		[...document.querySelector('.comparer')!.children]
			.filter((element) => !element.classList.contains('comparer-tableau') && getComputedStyle(element).display !== 'none')
			.flatMap((element) => (element as HTMLElement).innerText.split('\n'))
			.map((ligne) => ligne.replace(/\s+/g, ' ').trim())
			.filter(Boolean)
	);
}

/** Every line printed outside the table at the starting SHA (d14f433d), `communes=44991,44992,85991`. */
const B5_IMPRIME_AU_DEPART_COMMUNES: string[] = [
	"Comparer sans changer le sens des données",
	// UI-SPEC v4 § 6.4 (04.8-05): the h1 names the compared communes (« Comparer des communes » before).
	"Comparer Ancienne, Courante et Couverture inconnue",
	"Les valeurs gardent leur période, leur définition et leur source. Aucun score global.",
	"Référence des écarts : Ancienne",
	"« Placer en premier » change la référence utilisée pour lire les écarts. L’ordre ne constitue pas un classement.",
	"Lire les écarts",
	"Une différence de taux est affichée en points. Aucun écart n'est calculé quand les périodes, les unités ou les définitions diffèrent : la ligne le dit."
];

test('B5 — captures du comparateur de communes, 1 440 × 900 et 375 × 812 (premier écran, page entière)', async ({ page, request }) => {
	for (const [largeur, hauteur] of B5_LARGEURS) {
		await configure(request, [comparaison(B5_COMMUNES, TROIS)]);
		await page.setViewportSize({ width: largeur, height: hauteur });
		await open(page, `/comparer?communes=${B5_COMMUNES.join(',')}`);
		await expect(region(page)).toBeVisible();
		await page.evaluate(async () => {
			await document.fonts.ready;
			window.scrollTo({ top: 0, behavior: 'instant' });
		});
		await page.screenshot({ path: preuveB5(`communes-${largeur}-ecran.png`), animations: 'disabled' });
		await page.screenshot({ path: preuveB5(`communes-${largeur}-page.png`), animations: 'disabled', fullPage: true });
	}
});

test('B5 — le lien copié et la page imprimée gardent le contenu du SHA de départ (communes)', async ({ page, request, context }) => {
	await context.grantPermissions(['clipboard-read', 'clipboard-write']);
	await configure(request, [comparaison(B5_COMMUNES, TROIS)]);
	await page.setViewportSize({ width: 1440, height: 900 });
	await open(page, `/comparer?communes=${B5_COMMUNES.join(',')}&diff=1`);
	await expect(region(page)).toBeVisible();
	await page.getByRole('button', { name: 'Copier le lien', exact: true }).click();
	await expect(page.getByRole('status').filter({ hasText: 'Lien copié.' })).toHaveText(
		'Lien copié. Il reprend les communes, leur ordre et le filtre ; les valeurs sont relues à l’ouverture.'
	);
	const copie = new URL(await page.evaluate(() => navigator.clipboard.readText()));
	expect(copie.pathname + copie.search).toBe('/comparer?communes=44991,44992,85991&diff=1');

	await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
	await page.emulateMedia({ media: 'print' });
	const imprimees = await lignesImprimees(page);
	console.log(`RELEVE b5 imprime_communes=${JSON.stringify(imprimees)}`);
	for (const ligne of B5_IMPRIME_AU_DEPART_COMMUNES) expect(imprimees, ligne).toContain(ligne);
	await expect(page.locator('tr[data-cle]').last()).toBeVisible();
	await page.emulateMedia({ media: 'screen' });
	await page.evaluate(() => window.dispatchEvent(new Event('afterprint')));
});

// Review of plan 09 (Codex): the folded reading note starts closed and only `beforeprint` (JavaScript)
// opened it, so without JavaScript the method and the cautions were not printed. The print style now
// shows the folded content even when the `details` is closed; on screen the fold stays closed.
test.describe('B5 impression sans JavaScript', () => {
	test.use({ javaScriptEnabled: false });

	test('le repli fermé reste fermé à l’écran, et sa méthode et sa référence s’impriment', async ({ page, request }) => {
		await configure(request, [comparaison(B5_COMMUNES, TROIS)]);
		await page.setViewportSize({ width: 1280, height: 900 });
		await page.goto(`/comparer?communes=${B5_COMMUNES.join(',')}`);
		await expect(region(page)).toBeVisible();
		const pli = page.locator('details.comparer-lecture');
		const methode = page.getByText('Les valeurs gardent leur période, leur définition et leur source. Aucun score global.');
		const aide = page.getByText('« Placer en premier » change la référence utilisée pour lire les écarts. L’ordre ne constitue pas un classement.');
		const ecarts = page.getByText('Une différence de taux est affichée en points.');
		const lues = [methode, aide, ecarts];

		await page.emulateMedia({ media: 'screen' });
		await expect(pli).not.toHaveAttribute('open', '');
		for (const lue of lues) await expect(lue).toBeHidden();

		await page.emulateMedia({ media: 'print' });
		// Still folded for the reader: the print style shows its content, it does not open the element.
		await expect(pli).not.toHaveAttribute('open', '');
		for (const lue of lues) await expect(lue).toBeVisible();
		await expect(page.getByRole('heading', { level: 2, name: 'Référence des écarts : Ancienne' })).toBeVisible();
		await expect(page.locator('tr[data-cle]').last()).toBeVisible();

		await page.emulateMedia({ media: 'screen' });
		for (const lue of lues) await expect(lue).toBeHidden();
	});
});

/** The first served value of the table (first `.state-value` of its body), its box, the scroll and the site header. */
function premiereValeur(page: Page) {
	return page.evaluate(() => {
		const valeur = document.querySelector('table.compare-table tbody .state-value')!;
		const ligne = valeur.closest('tr[data-cle]')!;
		const cellule = valeur.closest('td[data-code]')!;
		const boite = valeur.getBoundingClientRect();
		const dessus = document.elementFromPoint(boite.left + boite.width / 2, boite.top + boite.height / 2);
		return {
			cle: ligne.getAttribute('data-cle'),
			code: cellule.getAttribute('data-code'),
			texte: valeur.textContent?.replace(/\s+/g, ' ').trim() ?? null,
			scrollY: window.scrollY,
			haut: boite.top,
			bas: boite.bottom,
			gauche: boite.left,
			droite: boite.right,
			basEnTete: document.querySelector('header.site-header')!.getBoundingClientRect().bottom,
			hauteurFenetre: window.innerHeight,
			largeurFenetre: window.innerWidth,
			visible: !!dessus && valeur.contains(dessus)
		};
	});
}

// UI-012 (amendment 1): a value expected from the fixture, its WHOLE box inside the window of 375 × 812,
// under the site header, painted on top, with `scrollY === 0`. Red at the starting SHA (measure in
// the SUMMARY), green after the header is reordered.
test('UI-012 — trois communes à 375 × 812 : une valeur servie entière au premier écran, sans défilement', async ({ page, request }) => {
	await configure(request, [comparaison(B5_COMMUNES, TROIS)]);
	await page.setViewportSize({ width: 375, height: 812 });
	await open(page, `/comparer?communes=${B5_COMMUNES.join(',')}`);
	await expect(region(page)).toBeVisible();
	await page.evaluate(async () => {
		await document.fonts.ready;
		await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
	});
	const mesure = await premiereValeur(page);
	console.log(`RELEVE b5 premiere_valeur_375 ${JSON.stringify(mesure)}`);
	// The value is the expected one (field, commune, served text), not any cell at that position.
	// `texte` has its whitespace normalised, so the served thin/no-break spaces compare as plain spaces.
	expect(mesure.cle, JSON.stringify(mesure)).toBe('indicateurs_dvf.residentiel_maison.courante.mediane');
	expect(mesure.code, JSON.stringify(mesure)).toBe('44991');
	expect(mesure.texte, JSON.stringify(mesure)).toBe('2 200 €/m²');
	expect(mesure.scrollY, JSON.stringify(mesure)).toBe(0);
	expect(mesure.haut, JSON.stringify(mesure)).toBeGreaterThanOrEqual(mesure.basEnTete);
	expect(mesure.bas, JSON.stringify(mesure)).toBeLessThanOrEqual(mesure.hauteurFenetre);
	expect(mesure.gauche, JSON.stringify(mesure)).toBeGreaterThanOrEqual(0);
	expect(mesure.droite, JSON.stringify(mesure)).toBeLessThanOrEqual(mesure.largeurFenetre);
	expect(mesure.visible, JSON.stringify(mesure)).toBe(true);
});

test('B5 — à 1 440 et 375 px : objets, commande compacte puis tableau, aucun réglage déplié', async ({ page, request }) => {
	for (const [largeur, hauteur] of B5_LARGEURS) {
		await configure(request, [comparaison(B5_COMMUNES, TROIS)]);
		await page.setViewportSize({ width: largeur, height: hauteur });
		await open(page, `/comparer?communes=${B5_COMMUNES.join(',')}`);
		await expect(region(page)).toBeVisible();
		const objets = page.locator('.comparer-objets');
		await expect(objets).toHaveText('3 communes : Ancienne, Courante, Couverture inconnue');
		const modifier = page.locator('details.comparer-modifier');
		await expect(modifier).not.toHaveAttribute('open', '');
		await expect(modifier.locator('summary')).toHaveText('Modifier la sélection');
		await expect(page.getByRole('button', { name: 'Copier le lien', exact: true })).toBeVisible();
		await expect(page.locator('details.comparer-lecture')).not.toHaveAttribute('open', '');
		const ordre = await page.evaluate(() => {
			const blocs = ['h1', '.comparer-objets', 'details.comparer-modifier > summary', 'table.compare-table', 'details.comparer-lecture > summary'];
			const elements = blocs.map((selecteur) => document.querySelector(selecteur)!);
			return elements.slice(1).map((element, i) => Boolean(elements[i].compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING));
		});
		expect(ordre, `ordre des blocs à ${largeur} px`).toEqual([true, true, true, true]);
		// Objects and command in the first screen, without scrolling. Declared change (direction A, T6,
		// 2026-11-10): beyond 720 px the title names the communes and the line that repeats them is not drawn.
		if (largeur === 1440) await expect(objets).toBeHidden();
		for (const cible of largeur === 1440 ? [modifier.locator('summary')] : [objets, modifier.locator('summary')]) {
			const boite = (await cible.boundingBox())!;
			expect(boite.y + boite.height, `${largeur} px`).toBeLessThanOrEqual(hauteur);
		}
		await sansDebordement(page);
		const axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
		expect(axe.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual([]);
	}
});

// --- B6 (plan 04.5-10): continuous table, safe factorisation, qualified dates ----------------------
//
// Height input wired in the fixtures (amendment M1 of the counter-verification): `communes=44991,44992,85991`,
// the input of plan 09, at 375 × 812. Its third commune is out of coverage: no row of it is a value in
// every column, so the strict factorisation leaves it as it was (measured, not asserted lower). The
// factorisation is measured on three hosted communes, the two served ones and a SYNTHETIC copy of the
// second (`troisHebergees`). Heights measured at the starting SHA 7ea0811c, written below.

/** Height of the page and of the comparison table, after fonts and two frames. */
async function hauteursB6(page: Page) {
	return page.evaluate(async () => {
		await document.fonts.ready;
		await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
		return {
			page: document.scrollingElement!.scrollHeight,
			tableau: Math.round(document.querySelector('table.compare-table')!.getBoundingClientRect().height)
		};
	});
}

/** Three hosted communes: the two served ones and a copy of the second under another code (SYNTHETIC). */
function troisHebergees(): Reponse {
	const copie = colonnes(['44991', '44992'], DEUX);
	const modele = structuredClone(copie.donnees.communes[1]);
	modele.code_insee = '44993';
	modele.nom = 'Troisième commune';
	if (modele.synthese) Object.assign(modele.synthese, { code_insee: '44993', nom_commune: 'Troisième commune' });
	copie.donnees.communes.push(modele);
	for (const critere of copie.donnees.criteres) critere.cellules.push({ ...critere.cellules[1], code_insee: '44993' });
	copie.meta = DEUX.meta;
	return copie;
}

const B6_HEBERGEES = ['44991', '44992', '44993'];
/**
 * Measured at 7ea0811c, before the factorisation (RELEVE b6, C:/tmp/dc-refonte-d/10/avant-e2e.log):
 * entree_m1 { page: 11089, tableau: 10308 }, hebergees { page: 10964, tableau: 10183 }.
 * Recalculated for the 14 px floor of 04.8-01 (text 13 → 14 px and 15 → 16 px, cells of 4 px on each
 * side under 720 px), with the same definitions:
 * - hebergees: the same three hosted communes without the factorisation (`factoriserMetadonnees`
 *   returning null), measured at the new sizes: { page: 11414, tableau: 10620 }. The factorised table
 *   measures { 11351, 10557 }: 0.6 % under the reference, as at the old sizes (10117 under 10183,
 *   0.65 %). A scaled value (10183 × 10557 / 10117 = 10626) was rejected: the table without
 *   factorisation (10620) would have passed it.
 * - entree_m1 (never grows; the factorisation does not apply to it): 10295 at the old sizes, 10755 at
 *   the new ones, same relative margin: 10308 × 10755 / 10295 = 10768.6 → 10769; page 11089 × 11549 /
 *   11079 = 11559.4 → 11559 (not asserted).
 * Re-measured at 04.8-05 (direction A mobile header of /comparer, 05/11): unfactorised { page: 11590, tableau: 10440 }; factorised { page: 11552, tableau: 10402 }.
 * Lot 6 of phase 4.9 (one control per factorised row, budgets unchanged): factorised { page: 11574, tableau: 10424 }, entree_m1 { page: 11720, tableau: 10570 }.
 */
const B6_AU_DEPART = {
	entree_m1: { page: 11559, tableau: 10769 },
	hebergees: { page: 11590, tableau: 10440 }
};

test('B6 — trois communes à 375 × 812 : hauteurs relevées, plus courte sur trois communes couvertes', async ({ page, request }) => {
	await page.setViewportSize({ width: 375, height: 812 });
	await configure(request, [comparaison(B5_COMMUNES, TROIS)]);
	await open(page, `/comparer?communes=${B5_COMMUNES.join(',')}`);
	await expect(region(page)).toBeVisible();
	const m1 = await hauteursB6(page);
	console.log(`RELEVE b6 hauteur_375 ${JSON.stringify(m1)}`);
	await sansDebordement(page);

	await configure(request, [comparaison(B6_HEBERGEES, troisHebergees())]);
	await open(page, `/comparer?communes=${B6_HEBERGEES.join(',')}`);
	await expect(region(page)).toBeVisible();
	const hebergees = await hauteursB6(page);
	console.log(`RELEVE b6 hauteur_375_hebergees ${JSON.stringify(hebergees)}`);
	await sansDebordement(page);
	expect(hebergees.tableau).toBeLessThan(B6_AU_DEPART.hebergees.tableau);
	expect(hebergees.page).toBeLessThan(B6_AU_DEPART.hebergees.page);
	// The input of plan 09 never grows.
	expect(m1.tableau).toBeLessThanOrEqual(B6_AU_DEPART.entree_m1.tableau);
});

// UI-013, UI-027, amended by lot 6 of phase 4.9, then by C3 (inventory of 2026-11-10, arbitrated by Ianis on 2026-11-10: « oui à la période et à la source derrière le bouton d'info »). The DVF counts of two communes (no unit, one
// structured period, one source) were written once under the row label; the period and the source are now
// read in the panel of the row, opened by the one control under the label. That panel covers both communes,
// the raw value of each included.
test('B6 — une ligne sans unité aux métadonnées identiques : rien sous le libellé, son Détail dit période et source et couvre chaque commune, valeur brute comprise', async ({ page, request }) => {
	for (const [largeur, hauteur] of B5_LARGEURS) {
		await configure(request, [comparaison(['44991', '44992'], DEUX)]);
		await page.setViewportSize({ width: largeur, height: hauteur });
		await open(page, '/comparer?communes=44991,44992');
		const ligne = page.locator('tr[data-cle="nb_mutations_12m_maison"]');
		const tete = ligne.locator('th.compare-critere');
		await expect(tete.locator('.compare-meta-commune')).toHaveCount(0);
		await expect(tete).not.toContainText('2024-06-01');
		await expect(tete).not.toContainText('Demandes de valeurs foncières');
		await expect(tete.locator('.compare-unite')).toHaveCount(0);
		await expect(ligne.locator('td .compare-meta')).toHaveCount(0);
		const libelle = norm(await tete.locator('span').first().textContent());
		await expect(ligne.locator('td button')).toHaveCount(0);
		await expect(ligne.getByRole('button')).toHaveCount(1);
		const bouton = ligne.getByRole('button', { name: `Détail : ${libelle}`, exact: true });
		await bouton.click();
		const volet = page.getByRole('dialog');
		await expect(volet.getByRole('heading', { name: libelle!, exact: true })).toBeFocused();
		await expect(volet).toContainText('Définition');
		await expect(volet).toContainText('Demandes de valeurs foncières (DVF)');
		await expect(volet).toContainText('2024-06-01 → 2025-06-01');
		await volet.locator('summary').click();
		const bruts = volet.locator('.source-panel-raw-bloc');
		await expect(bruts).toHaveCount(2);
		const critere = DEUX.donnees.criteres.find((item) => item.cle === 'nb_mutations_12m_maison')!;
		for (const [index, nom] of ['Ancienne (44991)', 'Courante (44992)'].entries()) {
			await expect(bruts.nth(index).locator('.source-panel-sub')).toHaveText(nom);
			await expect(bruts.nth(index)).toContainText(JSON.stringify(critere.cellules[index].valeur));
		}
		await page.keyboard.press('Escape');
		await expect(volet).toBeHidden();
		await expect(bouton).toBeFocused();
		await sansDebordement(page);
	}
});

// Lot 6 of phase 4.9 (plan § 4): the controls are measured, never the word « Source ». One per criterion
// whose metadata are common to its row, one per displayed cell everywhere else. Since C3 the header no longer
// writes those metadata: the row says they are common (`data-meta-communes`).
test('lot 6 — un contrôle d’explication par critère de provenance identique, un par cellule sinon', async ({ page, request }) => {
	await configure(request, [comparaison(['44991', '44992', '85991'], TROIS, 2), comparaison(['44991', '44992'], DEUX, 2)]);
	for (const communes of ['44991,44992', '44991,44992,85991']) {
		await open(page, `/comparer?communes=${communes}`);
		const releve = await page.locator('tr[data-cle]').evaluateAll((lignes) =>
			lignes.map((ligne) => ({
				cle: (ligne as HTMLElement).dataset.cle,
				factorisee: (ligne as HTMLElement).dataset.metaCommunes === 'true',
				enTete: ligne.querySelectorAll('th .source-button').length,
				cellules: ligne.querySelectorAll('td .source-button').length,
				valeurs: ligne.querySelectorAll('td .compare-valeur:has(.state-value, .state-badge)').length
			}))
		);
		expect(releve.length, communes).toBeGreaterThan(0);
		for (const { cle, factorisee, enTete, cellules, valeurs } of releve) {
			expect([cle, enTete, cellules], communes).toEqual(factorisee ? [cle, 1, 0] : [cle, 0, valeurs]);
		}
		console.log(`RELEVE lot6 ${communes} ${JSON.stringify({ lignes: releve.length, controles: releve.reduce((total, ligne) => total + ligne.enTete + ligne.cellules, 0) })}`);
	}
	expect(await lectures(request)).toEqual(['44991,44992', '44991,44992,85991']);
});

test('lot 6 — volet de ligne ouvert : clavier, aucune violation axe de toute gravité à 1 440, 375 et 320 px', async ({ page, request }) => {
	test.setTimeout(120_000); // Six full axe analyses.
	await configure(request, [comparaison(['44991', '44992'], DEUX, 3)]);
	for (const [largeur, hauteur] of [[1440, 900], [375, 812], [320, 640]] as const) {
		await page.setViewportSize({ width: largeur, height: hauteur });
		await open(page, '/comparer?communes=44991,44992');
		const bouton = page.locator('tr[data-cle="nb_mutations_12m_maison"] th .source-button');
		await expect(bouton.locator('svg')).toBeVisible();
		await bouton.focus();
		await bouton.press('Enter');
		const volet = page.getByRole('dialog');
		await expect(volet).toBeVisible();
		await volet.locator('summary').click();
		await page.evaluate(() => Promise.all(document.getAnimations().map((animation) => animation.finished)));
		const axe = await new AxeBuilder({ page }).include('dialog.source-panel').analyze();
		expect(axe.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`), `${largeur} px`).toEqual([]);
		await page.screenshot({ path: tableEvidencePath(`lot6-volet-ligne-${largeur}.png`), animations: 'disabled' });
		await page.keyboard.press('Escape');
		await expect(bouton).toBeFocused();
		const fermee = await new AxeBuilder({ page }).include('table.compare-table').analyze();
		expect(fermee.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`), `${largeur} px fermé`).toEqual([]);
		await sansDebordement(page);
	}
});

// 04.8-05 (Opus 04.8-05-1, P2): the comparator lives in `.gabarit` (1192 px inside, 04.9); four
// communes still fit at 1440 px, without horizontal scroll of the table nor of the page.
test('1440 px, four communes: the table fits its page width, no horizontal scroll', async ({ page, request }) => {
	await configure(request, [comparaison(['44991', '44992', '85991', '44993'], quatre())]);
	await page.setViewportSize({ width: 1440, height: 900 });
	await open(page, '/comparer?communes=44991,44992,85991,44993');
	expect(await entetes(page)).toEqual(['44991', '44992', '85991', '44993']);
	expect(await region(page).evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
	await expect(page.locator('.compare-defilement')).toBeHidden();
	await sansDebordement(page);
});

// Review of 09/11 (Ianis): at every width, the information icon of a row stays on the line of the last
// word of its label, never alone on a line of its own, and no unit hangs alone under the label.
test('icône d’information collée au dernier mot du libellé, aucune unité seule sous le libellé (375, 768, 1440 px)', async ({ page, request }) => {
	for (const [largeur, hauteur] of [[375, 812], [768, 1024], [1440, 900]] as const) {
		await configure(request, [comparaison(['44991', '44992'], DEUX)]);
		await page.setViewportSize({ width: largeur, height: hauteur });
		await open(page, '/comparer?communes=44991,44992');
		// Declared change (direction A, T10, 2026-11-10): the rule « never a unit alone under a label » is kept
		// and read on the geometry. On a phone no label draws a unit; from 721 px a unit follows the last word
		// of its label on the same line, tied to it by a no-break space.
		const seules = await page.locator('th.compare-critere').evaluateAll((tetes) =>
			tetes.flatMap((tete) => {
				const unite = tete.querySelector<HTMLElement>('.compare-unite');
				if (!unite || unite.offsetParent === null) return [];
				const boite = unite.getBoundingClientRect();
				const zone = document.createRange();
				zone.selectNodeContents(tete.querySelector('.compare-libelle')!);
				const lignes = [...zone.getClientRects()];
				const derniere = lignes[lignes.length - 1];
				return boite.top < derniere.bottom && boite.bottom > derniere.top ? [] : [tete.closest('tr')?.dataset.cle];
			})
		);
		expect(seules, `unités seules sous leur libellé à ${largeur} px`).toEqual([]);
		expect(await page.locator('th.compare-critere .compare-unite:visible').count(), `unités dessinées à ${largeur} px`).toBe(largeur === 375 ? 0 : await page.locator('tr[data-unite-dite="true"]:visible').count());
		const decalages = await page.locator('th.compare-critere').evaluateAll((tetes) =>
			tetes.flatMap((tete) => {
				const icone = tete.querySelector('.source-button svg');
				if (!icone) return [];
				const fin = tete.querySelector('.compare-libelle-fin');
				const mot = fin?.firstChild;
				if (!mot || mot.nodeType !== Node.TEXT_NODE) return [{ cle: tete.closest('tr')?.dataset.cle, ecart: Infinity }];
				const plage = document.createRange();
				plage.selectNodeContents(mot);
				const lignes = [...plage.getClientRects()];
				const derniere = lignes[lignes.length - 1];
				const boite = icone.getBoundingClientRect();
				const centre = boite.top + boite.height / 2;
				// The icon centre must sit within the box of the last line of the last word.
				const ecart = centre < derniere.top ? derniere.top - centre : centre > derniere.bottom ? centre - derniere.bottom : 0;
				return [{ cle: tete.closest('tr')?.dataset.cle, ecart }];
			})
		);
		expect(decalages.length, `lignes à icône à ${largeur} px`).toBeGreaterThan(3);
		expect(decalages.filter((item) => item.ecart > 0), `icônes décrochées à ${largeur} px`).toEqual([]);
		await sansDebordement(page);
	}
});
