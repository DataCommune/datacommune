import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import AxeBuilder from '@axe-core/playwright';
import { test, expect, chromium, type APIRequestContext, type Locator, type Page, type TestInfo } from '@playwright/test';
import { avecVentesDeLaParcelle } from './fixtures/adresse-ventes';
import { DOCUMENT_REEL, DOCUMENT_RETIRE, REEL, RETIRE } from './fixtures/dpe-documents';
import { ANCIEN, DOCUMENT_ANCIEN, DOCUMENT_RECENT, RECENT } from './fixtures/recette-adresses';
import { E2E_APP_ORIGIN, E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { cheminRapport, enRapport, REQUETE_RAPPORT } from './fixtures/rapport';
import {
	expectNoUnexpectedOffLocalRequest,
	installLocalNetworkGuard
} from './fixtures/local-network';

// Plan 04-04 (LIEU-03, DPE-01, SEM-01, SEM-02, UI-01; D-102, D-111) — the page of one DPE document,
// opened from a card of the address page by its ADEME number.
//
// What this spec locks, on the synthetic source of this instance (exact contract: every request
// the page makes is declared below, an undeclared one is a violation):
//   - tracer: the « DPE n° … » link of a card of `/adresse/{id}` opens `/dpe/{numero}`, whose h1 is
//     « DPE n° {numero} » in tabular figures under the overline « Document DPE », with landmark ①
//     (class and served consumption) and `noindex, follow`;
//   - no wording makes the dwelling the subject of a value;
//   - the whole page: four landmarks, three open sections, the declared address published or masked
//     with its reason, the BAN address retained by the source, provenance and status, replacement,
//     no BAN address (short breadcrumb), raw keys never in the HTML, the states (unknown number with
//     the coverage, malformed number without any call, outage and reload, typed refusal, loading in
//     client navigation), 320 px and a real 200 % zoom with a very long declared address, axe WCAG
//     2.1 AA without violation.

type Reply = { status: number; body: unknown; gate?: string };
type Expectation = {
	method: 'GET';
	pathname: string;
	query: Record<string, string>;
	minCalls: number;
	maxCalls: number;
	responses: Reply[];
};

const NB = ' ';
const BAN_ID = '85191_0420_00012';
const NUMERO = '2485E0000003C';

const SOURCE_BAN = { nom: 'Base Adresse Nationale', url: 'https://adresse.data.gouv.fr', licence: 'Licence Ouverte 2.0', producteur: 'IGN' };
const SOURCE_DPE = { cle: 'dpe', nom: 'Diagnostics de performance énergétique (DPE)', producteur: 'ADEME', url: 'https://data.ademe.fr/datasets/dpe03existant' };

function publicMessagesEvidencePath(testInfo: TestInfo, name: string): string {
	const directory = process.env.DC_PUBLIC_MESSAGES_EVIDENCE_DIR;
	if (!directory) return testInfo.outputPath(name);
	mkdirSync(directory, { recursive: true });
	return join(directory, name);
}

function dpeEnergyEvidencePath(testInfo: TestInfo, name: string): string {
	const directory = process.env.DC_DPE_ENERGY_EVIDENCE_DIR;
	if (!directory) return testInfo.outputPath(name);
	mkdirSync(directory, { recursive: true });
	return join(directory, name);
}

/** Before/after captures of plan 04.5-12 (`C:/tmp/dc-refonte-d/12/{avant,apres}`), else the test output. */
function refonteEvidencePath(testInfo: TestInfo, name: string): string {
	const directory = process.env.DC_REFONTE_D_EVIDENCE_DIR;
	if (!directory) return testInfo.outputPath(name);
	mkdirSync(directory, { recursive: true });
	return join(directory, name);
}

const IDENTITE = {
	etat: 'etablie',
	ban_id: BAN_ID,
	libelle: '12 Rue des Lilas, 85000 La Roche-sur-Yon',
	numero: '12',
	rep: null,
	nom_voie: 'Rue des Lilas',
	code_postal: '85000',
	commune: { code_insee: '85191', nom: 'La Roche-sur-Yon' },
	departement: { code: '85', nom: 'Vendée' },
	parcelle: { etat: 'unique', id_parcelle: '85191000AB0012', nombre_candidats: 1, rattachement: 'parcelle_du_point', motif: null }
};

function valeur(partiel: Record<string, unknown>) {
	return { field_id: 'x', etat: 'value', valeur: null, unite: null, periode: null, motif: null, source: null, rattachement: null, reference: null, ...partiel };
}

const UNIQUE = {
	etat: 'unique',
	motif: 'document_valide',
	date: '2024-03-12',
	document: { numero: NUMERO, etiquette_dpe: 'C', etiquette_ges: 'B', date_etablissement: '2024-03-12' },
	documents_ex_aequo: []
};

/** The address page as served by 04-03: its landmarks are not the subject of this spec. */
const FICHE = {
	identite: IDENTITE,
	couverture: { etat: 'heberge', motif: null },
	reperes: null
};

function carte(numero: string, partiel: Record<string, unknown> = {}) {
	return {
		numero,
		etiquette_dpe: 'C',
		etiquette_ges: 'B',
		type_batiment: 'maison',
		surface_habitable: 92,
		date_etablissement: '2024-03-12',
		date_fin_validite: '2034-03-11',
		badges: [],
		adresse_declaree: null,
		...partiel
	};
}

const DOCUMENTS = {
	etat: 'value',
	motif: null,
	source: SOURCE_DPE,
	total: 2,
	page: 1,
	pages: 1,
	par_page: 10,
	generation: '0123456789abcdef',
	distribution: { A: 0, B: 0, C: 1, D: 1, E: 0, F: 0, G: 0, unknown: 0 },
	date_import: '2026-09-30',
	documents: [carte(NUMERO, { badges: ['plus_recent'] }), carte('2285E0000002D', { etiquette_dpe: 'D', date_etablissement: '2022-06-01', date_fin_validite: '2032-05-31' })],
	selection: UNIQUE,
	signal_plusieurs_logements: null,
	constat_d110: null,
	rattachement: 'adresse_saisie_diagnostiqueur'
};

const valeurDpe = (partiel: Record<string, unknown>) => valeur({ source: SOURCE_DPE, rattachement: 'adresse_saisie_diagnostiqueur', ...partiel });

/** The document as served by `GET /v1/dpe/{numero}` (04-04). */
function documentDpe(partiel: Record<string, unknown> = {}) {
	return {
		numero: NUMERO,
		source_identity: { reference: NUMERO, sources: [{ dataset: 'dpe03existant', url: 'https://data.ademe.fr/datasets/dpe03existant' }] },
		disponibilite: 'disponible',
		methode: 'actuelle',
		dates: { etablissement: '2024-03-12', fin_validite: '2034-03-11' },
		reperes: {
			etiquette_energie: {
				classe: valeurDpe({ field_id: 'dpe.document.etiquette_dpe', valeur: 'C', unite: 'none' }),
				mesure: valeurDpe({ field_id: 'dpe.document.conso_ep', valeur: 9120, unite: 'kwh_ep_per_year' }),
				// Published by the ADEME next to the total (04.1-12), never 9 120 / 81,9 (about 111).
				mesure_m2: valeurDpe({ field_id: 'dpe.document.conso_ep_m2', valeur: 104, unite: 'kwh_ep_per_m2_per_year' })
			},
			etiquette_climat: {
				classe: valeurDpe({ field_id: 'dpe.document.etiquette_ges', valeur: 'B', unite: 'none' }),
				mesure: valeurDpe({ field_id: 'dpe.document.emissions_ges', valeur: 1004.8, unite: 'kg_co2_eq_per_year' }),
				mesure_m2: valeurDpe({ field_id: 'dpe.document.emissions_ges_m2', valeur: 11, unite: 'kg_co2_eq_per_m2_per_year' })
			},
			logement_decrit: {
				type_batiment: valeurDpe({ field_id: 'dpe.document.type_batiment', valeur: 'maison', unite: 'none' }),
				surface: valeurDpe({ field_id: 'dpe.document.surface_habitable', valeur: 81.9, unite: 'm2' })
			},
			cout_annuel: valeurDpe({ field_id: 'dpe.document.cout_annuel', valeur: 1240, unite: 'euro_per_year' })
		},
		statut: { code: 'en_vigueur', validite: 'not_expired_by_date', date: null, remplace_par: null, remplace: null },
		caracteristiques: [
			valeurDpe({ field_id: 'dpe.document.type_batiment', valeur: 'maison', unite: 'none' }),
			valeurDpe({ field_id: 'dpe.document.surface_habitable', valeur: 81.9, unite: 'm2' }),
			valeurDpe({ field_id: 'dpe.document.periode_construction', valeur: '2013-2021', unite: 'none' }),
			valeurDpe({ field_id: 'dpe.document.isolation', etat: 'not_reported', valeur: null })
		],
		adresse_declaree: { etat: 'publiee', texte: '14 rue des Lilas', motif: null },
		adresse_ban: {
			etat: 'retenue',
			ban_id: BAN_ID,
			libelle: '12 Rue des Lilas, 85000 La Roche-sur-Yon',
			numero: '12',
			rep: null,
			nom_voie: 'Rue des Lilas',
			commune: { code_insee: '85191', nom: 'La Roche-sur-Yon' },
			motif: null
		},
		adresse_divergente: true,
		provenance: { source: 'ADEME', date_import: '2026-09-30' },
		association: 'source_ban_equality',
		address_check: 'unverified',
		...partiel
	};
}

function attente(pathname: string, replies: Reply[], minCalls = replies.length, query: Record<string, string> = {}): Expectation {
	return { method: 'GET', pathname, query, minCalls, maxCalls: replies.length, responses: replies };
}

const ok = (donnees: unknown, meta: unknown = { source: [SOURCE_DPE] }): Reply => ({ status: 200, body: { statut: 'ok', donnees, meta } });

async function configure(request: APIRequestContext, expectations: Expectation[]) {
	// 04-05: an opened address also reads the first page of the sales of its parcel.
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
		data: { reset: true, expectations: avecVentesDeLaParcelle(expectations) }
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
	const hydrated = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
	const reponse = await page.goto(url);
	await hydrated;
	return reponse;
}

const repere = (page: Page, id: string) => page.locator(`[data-kpi="${id}"]`);

test.beforeEach(async ({ page }) => {
	await installLocalNetworkGuard(page);
});

test.afterEach(async ({ page, request }) => {
	await expectSourceContractMet(request);
	expectNoUnexpectedOffLocalRequest(page);
});

test.describe('Tracer — carte de la page adresse → page du document', () => {
	test('le lien « DPE n° … » ouvre /dpe/{numero} : h1, sur-titre, repère ①, noindex, follow', async ({ page, request }) => {
		await configure(request, [
			attente(cheminRapport(BAN_ID), [enRapport(ok(FICHE, { source: [SOURCE_BAN] }))], 1, REQUETE_RAPPORT),
			attente(`/v1/adresse/${BAN_ID}/dpe`, [ok(DOCUMENTS)], 1, { page: '1' }),
			// A hover preload and the click may read the document once or twice, never more.
			attente(`/v1/dpe/${NUMERO}`, [ok(documentDpe()), ok(documentDpe())], 1)
		]);
		await open(page, `/adresse/${BAN_ID}`);

		const lien = page.locator('ol.doclist-cards > li').first().getByRole('link', { name: `DPE n°${NB}${NUMERO}` });
		await lien.click();
		await page.waitForURL(`**/dpe/${NUMERO}`);

		// UI-SPEC v4 § 6.7 (04.8-04): the h1 names the document by its served type and surface; the served
		// number, in tabular figures, opens the line under it.
		const h1 = page.getByRole('heading', { level: 1 });
		await expect(h1).toHaveText(`Diagnostic de performance énergétique d’une maison de 81,9${NB}m²`);
		await expect(page.locator('.document-sous .document-numero')).toHaveText(`N°${NB}${NUMERO}`);
		await expect(page.locator('.document-sous .document-numero .chiffre')).toHaveCSS('font-variant-numeric', 'tabular-nums');
		await expect(page.locator('.document-overline')).toHaveText('Document DPE');
		await expect(page).toHaveTitle(`DPE n°${NB}${NUMERO} — DataCommune`);
		await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', 'noindex, follow');

		// UI-SPEC v4 § 6.7: the landmarks are tiles of the panel « Classes déclarées », each named by its `dt`.
		await expect(page.getByRole('heading', { level: 2, name: 'Classes déclarées' })).toBeVisible();
		const un = repere(page, 'energie');
		await expect(un.locator('dt.kpi-label')).toHaveText('Étiquette énergie');
		await expect(un.locator('.dpe-tag')).toHaveText('Classe C');
		// C1 (plan 04.5-12): the per-m² value published by the ADEME, with its unit and short line.
		await expect(un.locator('.kpi-value .value')).toHaveText('104');
		await expect(un.locator('.kpi-value .unit')).toHaveText(' kWhEP/m²/an');
		await expect(un.locator('.kpi-meta')).toHaveText('par m² · 5 usages');

		// « logement » is never the subject of a value on this page (LIEU-03, UI-SPEC prohibitions).
		const texte = await page.locator('main').innerText();
		expect(texte).not.toMatch(/le logement (consomme|émet|est classé|a )/i);
		expect(texte).not.toMatch(/DPE (du|de ce) logement|DPE de l'adresse/i);
	});

	// UI-007 (plan 04.5-06): the prefilled surface of the similar sales names the served document it
	// comes from; its link opens the page of that document.
	test('la provenance du préremplissage des ventes ressemblantes mène à /dpe/{numero} (200)', async ({ page, request }) => {
		await configure(request, [
			attente(cheminRapport(BAN_ID), [enRapport(ok(FICHE, { source: [SOURCE_BAN] }))], 1, REQUETE_RAPPORT),
			attente(`/v1/adresse/${BAN_ID}/dpe`, [ok(DOCUMENTS)], 1, { page: '1' }),
			attente(`/v1/dpe/${NUMERO}`, [ok(documentDpe()), ok(documentDpe())], 1)
		]);
		await open(page, `/adresse/${BAN_ID}`);
		// The similar sales are in their panel (04.7-03): opened from its link.
		await page.locator('.actions-plus summary').click();
		await page.getByRole('link', { name: 'Ventes ressemblantes' }).first().click();

		const origine = page.locator('section#ventes-ressemblantes .comparables-origine');
		await expect(origine).toHaveText(
			`Type d'après les DPE de cette adresse. Surface issue du DPE n°${NB}${NUMERO}, le plus récent de cette adresse.`
		);
		const lien = origine.getByRole('link', { name: `Surface issue du DPE n°${NB}${NUMERO}` });
		await expect(lien).toHaveAttribute('href', `/dpe/${NUMERO}`);
		const reponse = page.waitForResponse((res) => new URL(res.url()).pathname === `/dpe/${NUMERO}` || new URL(res.url()).pathname.endsWith('/__data.json'));
		await lien.click();
		await page.waitForURL(`**/dpe/${NUMERO}`);
		expect((await reponse).status()).toBe(200);
		// UI-SPEC v4 § 6.7 (04.8-04): the served number is under the h1, « N° {numéro} ».
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(/^Diagnostic de performance énergétique/);
		await expect(page.locator('.document-sous .document-numero')).toHaveText(`N°${NB}${NUMERO}`);
		// The document page answers 200 when opened directly too.
		expect((await page.request.get(`/dpe/${NUMERO}`)).status()).toBe(200);
	});

	test('ouvert directement : SSR, en-tête x-robots-tag « noindex, follow »', async ({ page, request }) => {
		await configure(request, [attente(`/v1/dpe/${NUMERO}`, [ok(documentDpe())])]);

		const servie = await page.goto(`/dpe/${NUMERO}`);

		expect(servie?.headers()['x-robots-tag']).toBe('noindex, follow');
		const html = (await servie?.text()) ?? '';
		expect(html.match(/<meta[^>]*name="robots"[^>]*>/g)).toEqual([expect.stringContaining('content="noindex, follow"')]);
		// UI-SPEC v4 § 6.7 (04.8-04): the served number is under the h1, « N° {numéro} ».
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(/^Diagnostic de performance énergétique/);
		await expect(page.locator('.document-sous .document-numero')).toHaveText(`N°${NB}${NUMERO}`);
	});
});

// --- Task 2 — the whole document page, its states, 320 px, zoom 200 %, axe ---------------------

const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] as const;
const NNB = String.fromCharCode(0x202f);
const NUMERO_B = '2585E0000004D';
const SENTINELLE = 'SENTINELLE-0404';
// The longest declared address of the fixture: published as typed, it must wrap, never be cut.
const ADRESSE_LONGUE =
	'3 Avenue du Maréchal Jean-Marie Gabriel de Lattre de Tassigny et des Combattants d’Afrique du Nord 85000 La Roche-sur-Yon';

const SANS_CLE = { etat: 'sans_cle', ban_id: null, libelle: null, numero: null, rep: null, nom_voie: null, commune: null, motif: null };
const probleme = (status: number, code: string, title: string, extra: Record<string, unknown> = {}): Reply => ({
	status,
	body: { title, status, code, ...extra }
});

async function texteDePage(page: Page): Promise<string> {
	return page.locator('main').innerText();
}

async function sansDebordement(page: Page) {
	const { scrollWidth, innerWidth } = await page.evaluate(() => ({
		scrollWidth: document.documentElement.scrollWidth,
		innerWidth: window.innerWidth
	}));
	expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
}

async function attendreDeuxRendus(page: Page) {
	await page.evaluate(
		() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
	);
}

async function topsDuTexte(target: Locator, mot?: string): Promise<number[]> {
	return target.evaluate((element, searched) => {
		const text = [...element.childNodes].find((node) => node.nodeType === Node.TEXT_NODE && node.textContent?.trim());
		if (!text?.textContent) return [];
		const start = searched ? text.textContent.indexOf(searched) : 0;
		if (start < 0) return [];
		const range = document.createRange();
		range.setStart(text, start);
		range.setEnd(text, searched ? start + searched.length : text.textContent.length);
		return [...range.getClientRects()].map((rect) => rect.top);
	}, mot);
}

async function verifierGeometrieMobile(
	card: Locator,
	source: Locator,
	targets: readonly { nom: string; locator: Locator }[]
) {
	const sourceHandle = await source.elementHandle();
	const targetHandles = await Promise.all(
		targets.map(async ({ nom, locator }) => ({ nom, element: await locator.elementHandle() }))
	);
	const rectangles = await card.evaluate((element, handles) => {
		// Sequential Playwright calls can observe different layouts while fonts or hydration settle.
		const box = (node: Element | null) => {
			if (!node?.isConnected) return null;
			const rectangle = node.getBoundingClientRect();
			const style = getComputedStyle(node);
			if (!rectangle.width || !rectangle.height || style.visibility === 'hidden' || style.visibility === 'collapse') return null;
			return { x: rectangle.x, y: rectangle.y, width: rectangle.width, height: rectangle.height };
		};
		return {
			card: box(element),
			source: box(handles.source),
			targets: handles.targets.map(({ nom, element: target }) => ({ nom, box: box(target) }))
		};
	}, { source: sourceHandle, targets: targetHandles });
	await Promise.all([sourceHandle, ...targetHandles.map(({ element }) => element)].map((handle) => handle?.dispose()));
	await test.info().attach('dpe-mobile-rectangles', {
		body: JSON.stringify(rectangles, null, 2),
		contentType: 'application/json'
	});
	const cardBox = rectangles.card;
	const sourceBox = rectangles.source;
	expect(cardBox && sourceBox, 'carte et cible Source mesurables à 375 px').toBeTruthy();
	const boxes = [{ nom: 'Source', box: sourceBox! }];
	for (const { nom, box } of rectangles.targets) {
		expect(box, `${nom} mesurable à 375 px`).toBeTruthy();
		boxes.push({ nom, box: box! });
		const overlap =
			Math.max(0, Math.min(box!.x + box!.width, sourceBox!.x + sourceBox!.width) - Math.max(box!.x, sourceBox!.x)) *
			Math.max(0, Math.min(box!.y + box!.height, sourceBox!.y + sourceBox!.height) - Math.max(box!.y, sourceBox!.y));
		expect(overlap, `${nom} ne croise pas la cible Source à 375 px`).toBe(0);
	}
	for (const { nom, box } of boxes) {
		expect(box.x, `${nom} contenu horizontalement à gauche`).toBeGreaterThanOrEqual(cardBox!.x);
		expect(box.x + box.width, `${nom} contenu horizontalement à droite`).toBeLessThanOrEqual(cardBox!.x + cardBox!.width);
		expect(box.y, `${nom} contenu verticalement en haut`).toBeGreaterThanOrEqual(cardBox!.y);
		expect(box.y + box.height, `${nom} contenu verticalement en bas`).toBeLessThanOrEqual(cardBox!.y + cardBox!.height);
	}
}

async function capturerEtatDpe(
	page: Page,
	testInfo: TestInfo,
	nom: string,
	width: 1440 | 375,
	energie: ReturnType<typeof repere> | null = null
) {
	await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' }));
	await attendreDeuxRendus(page);
	await page.screenshot({
		path: dpeEnergyEvidencePath(testInfo, `${nom}-premier-ecran-${width}.png`),
		animations: 'disabled'
	});
	if (!energie) return;
	const [headerBox, energyBox] = await Promise.all([
		page.locator('header').first().boundingBox(),
		energie.boundingBox()
	]);
	expect(headerBox && energyBox, `en-tête et énergie mesurables pour ${nom} à ${width} px`).toBeTruthy();
	await page.evaluate(
		({ absoluteTop, headerHeight }) => scrollTo({ top: Math.max(0, absoluteTop - headerHeight - 16), behavior: 'instant' }),
		{ absoluteTop: energyBox!.y + (await page.evaluate(() => scrollY)), headerHeight: headerBox!.height }
	);
	await attendreDeuxRendus(page);
	const [headerAfter, energyAfter] = await Promise.all([
		page.locator('header').first().boundingBox(),
		energie.boundingBox()
	]);
	expect(headerAfter && energyAfter, `géométrie de capture ${nom} à ${width} px`).toBeTruthy();
	expect(energyAfter!.y, `bloc énergie sous l'en-tête pour ${nom} à ${width} px`).toBeGreaterThanOrEqual(
		headerAfter!.y + headerAfter!.height
	);
	await energie.screenshot({
		path: dpeEnergyEvidencePath(testInfo, `${nom}-bloc-${width}.png`),
		animations: 'disabled'
	});
}

/** The document whose landmark ① carries the given per-m² value (plan 04.5-12), the total kept. */
function documentAvecMesureEnergieM2(partiel: Record<string, unknown>, total = 9120, surface = 81.9) {
	const base = documentDpe();
	const surfaceServie = valeurDpe({ field_id: 'dpe.document.surface_habitable', valeur: surface, unite: 'm2' });
	return {
		...base,
		reperes: {
			...base.reperes,
			etiquette_energie: {
				...base.reperes.etiquette_energie,
				mesure: valeurDpe({ field_id: 'dpe.document.conso_ep', valeur: total, unite: 'kwh_ep_per_year' }),
				mesure_m2: valeurDpe({ field_id: 'dpe.document.conso_ep_m2', unite: 'kwh_ep_per_m2_per_year', ...partiel })
			},
			logement_decrit: { ...base.reperes.logement_decrit, surface: surfaceServie }
		},
		caracteristiques: base.caracteristiques.map((c) => (c.field_id === 'dpe.document.surface_habitable' ? surfaceServie : c))
	};
}

/** The document whose landmark ② carries the given per-m² value (plan 04.5-12), with its total and surface. */
function documentAvecMesureClimatM2(partiel: Record<string, unknown>, total = 1004.8, surface = 81.9) {
	const base = documentDpe();
	const surfaceServie = valeurDpe({ field_id: 'dpe.document.surface_habitable', valeur: surface, unite: 'm2' });
	return {
		...base,
		reperes: {
			...base.reperes,
			etiquette_climat: {
				...base.reperes.etiquette_climat,
				mesure: valeurDpe({ field_id: 'dpe.document.emissions_ges', valeur: total, unite: 'kg_co2_eq_per_year' }),
				mesure_m2: valeurDpe({ field_id: 'dpe.document.emissions_ges_m2', unite: 'kg_co2_eq_per_m2_per_year', ...partiel })
			},
			logement_decrit: { ...base.reperes.logement_decrit, surface: surfaceServie }
		},
		caracteristiques: base.caracteristiques.map((c) => (c.field_id === 'dpe.document.surface_habitable' ? surfaceServie : c))
	};
}

async function mesurerAdresse(page: Page) {
	return page.evaluate(() => {
		const dd = document.querySelector('[data-ligne="adresse-declaree"] dd .document-adresse-texte')!;
		const style = getComputedStyle(dd);
		return {
			texte: dd.textContent,
			hauteur: Math.round(dd.getBoundingClientRect().height),
			coupe: style.textOverflow === 'ellipsis' || dd.scrollWidth > dd.clientWidth + 1,
			scroll: document.documentElement.scrollWidth,
			inner: window.innerWidth
		};
	});
}

test.describe('Page document — contenu servi (LIEU-03, D-111, SEM-02)', () => {
	test('repère énergie : classe avant total, échelle dédiée et géométrie réelle à 1 440 et 375 px', async ({
		page,
		request
	}, testInfo) => {
		await configure(request, [
			attente(`/v1/dpe/${NUMERO}`, [ok(documentDpe()), ok(documentDpe())])
		]);

		for (const width of [1440, 375] as const) {
			await page.setViewportSize({ width, height: 900 });
			await open(page, `/dpe/${NUMERO}`);
			const energie = repere(page, 'energie');
			const climat = repere(page, 'climat');
			const energyTag = energie.locator('.dpe-tag');
			const total = energie.locator('.state-value-kpi');
			const totalValue = total.locator('.value');
			const totalUnit = total.locator('.unit');

			await expect(energyTag).toHaveText('Classe C');
			await expect(totalValue).toHaveText('104');
			await expect(totalUnit).toHaveText(' kWhEP/m²/an');
			await expect(energie.locator('.kpi-meta')).toHaveText('par m² · 5 usages');
			await expect(climat.locator('.dpe-tag')).toHaveText('Classe B');
			await expect(repere(page, 'logement').locator('.kpi-value .value')).toHaveText(`Maison · 81,9${NB}m²`);
			await expect(repere(page, 'cout').locator('.kpi-value .value')).toHaveText(new Intl.NumberFormat('fr-FR').format(1240));

			const tagBeforeTotal = await energie.evaluate((card) => {
				const tag = card.querySelector('.dpe-tag');
				const value = card.querySelector('.state-value-kpi');
				return Boolean(tag && value && (tag.compareDocumentPosition(value) & Node.DOCUMENT_POSITION_FOLLOWING));
			});
			expect(tagBeforeTotal, `classe énergie avant le total à ${width} px`).toBe(true);

			const metrics = await energie.evaluate((card) => {
				const metric = (selector: string) => {
					const element = card.querySelector<HTMLElement>(selector)!;
					const rect = element.getBoundingClientRect();
					return { width: rect.width, height: rect.height, fontSize: Number.parseFloat(getComputedStyle(element).fontSize) };
				};
				return {
					tag: metric('.dpe-tag'),
					value: metric('.state-value-kpi .value'),
					unit: metric('.state-value-kpi .unit')
				};
			});
			// 04.8-02 (UI-SPEC v4 § 11, D-30): DpeTag is the v4 pastille, 16 px for the main class of a key
			// figure (32 px wide at least, 4 px above and below), 14 px otherwise; 48 / 28 and 32 / 20 px before.
			expect(metrics.tag.width, `largeur tag énergie à ${width} px`).toBeGreaterThanOrEqual(32);
			expect(metrics.tag.height, `hauteur tag énergie à ${width} px`).toBe(24);
			expect(metrics.tag.fontSize, `lettre énergie à ${width} px`).toBe(16);
			// 04.8-04 (UI-SPEC v4 § 3.2): the value of a key figure tile is Newsreader 30, 24 up to 720 px (20 in
			// the narrow cards before the tiles); the per-m² value it shows stays on one line (below).
			expect(metrics.value.fontSize, `total énergie à ${width} px`).toBe(width <= 720 ? 24 : 30);
			// 04.8-01 (D-03): the unit follows the 14 px floor of the v4 scale (13 before); 04.8-02 (UI-SPEC v4
			// § 3.2, StateValue of a key figure): Inter 16 px beside the value.
			expect(metrics.unit.fontSize, `unité énergie à ${width} px`).toBe(16);
			const defaultTag = await climat.locator('.dpe-tag').evaluate((element) => {
				const rect = element.getBoundingClientRect();
				return { width: rect.width, height: rect.height, fontSize: Number.parseFloat(getComputedStyle(element).fontSize) };
			});
			expect(defaultTag.width).toBeGreaterThanOrEqual(24);
			expect(defaultTag.height).toBe(22);
			expect(defaultTag.fontSize).toBe(14);

			const unitTops = await totalUnit.evaluate((element) => {
				const text = [...element.childNodes].find((node) => node.nodeType === Node.TEXT_NODE && node.textContent?.trim());
				if (!text?.textContent) return [];
				const start = text.textContent.search(/\S/);
				const range = document.createRange();
				range.setStart(text, start);
				range.setEnd(text, text.textContent.length);
				return [...range.getClientRects()].map((rect) => rect.top);
			});
			expect(unitTops.length, `unité mesurable à ${width} px`).toBeGreaterThan(0);
			expect(Math.max(...unitTops) - Math.min(...unitTops), `unité entière à ${width} px`).toBeLessThanOrEqual(1);

			const source = energie.getByRole('button', { name: 'Source et définition : Étiquette énergie', exact: true });
			const sourceBox = await source.boundingBox();
			expect(sourceBox, `source énergie mesurable à ${width} px`).toBeTruthy();
			expect(sourceBox!.width).toBeGreaterThanOrEqual(44);
			expect(sourceBox!.height).toBeGreaterThanOrEqual(44);
			if (width === 375) {
				const numberTops = await topsDuTexte(totalValue);
				expect(numberTops.length, 'nombre 104 mesurable à 375 px').toBeGreaterThan(0);
				expect(Math.max(...numberTops) - Math.min(...numberTops), 'nombre 104 sur une seule ligne').toBeLessThanOrEqual(1);
				await verifierGeometrieMobile(energie, source, [
					{ nom: 'nombre 104', locator: totalValue },
					{ nom: 'unité kWhEP/m²/an', locator: totalUnit }
				]);
			}
			await source.focus();
			await expect(source).toBeFocused();
			await source.press('Enter');
			const panel = energie.locator('dialog.source-panel');
			await expect(panel).toBeVisible();
			await expect(panel.getByRole('heading', { name: 'Étiquette énergie' })).toBeFocused();
			await page.keyboard.press('Escape');
			await expect(panel).toBeHidden();
			await expect(source).toBeFocused();

			const stripBox = await page.locator('dl.kpi-strip').boundingBox();
			expect(stripBox, `bande de repères mesurable à ${width} px`).toBeTruthy();
			// 04.8-04 (UI-SPEC v4 § 3.2, Ianis's review of 04/11): the tiles are four columns, two up to 1080 px,
			// one up to 400 px; the compact band of 03-17 R-1 (≤ 400 px at 375) is replaced by that grid.
			const colonnes = await page.locator('dl.kpi-strip').evaluate((dl) => getComputedStyle(dl).gridTemplateColumns.split(' ').length);
			expect(colonnes, `colonnes de tuiles à ${width} px`).toBe(width <= 400 ? 1 : width <= 1080 ? 2 : 4);
			await sansDebordement(page);
			const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
			expect(axe.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual([]);

			const texte = await texteDePage(page);
			expect(texte).not.toMatch(/le logement (consomme|émet|est classé|a )/i);
			await capturerEtatDpe(page, testInfo, 'dpe-energie-valeur', width, energie);
		}
	});

	test('repère énergie : mesure absente et zéro restent distincts sans modifier les autres repères', async ({
		page,
		request
	}, testInfo) => {
		const manquant = documentAvecMesureEnergieM2({ etat: 'not_reported', valeur: null, unite: null });
		const zero = documentAvecMesureEnergieM2({ valeur: 0 });
		await configure(request, [
			attente(`/v1/dpe/${NUMERO}`, [ok(manquant), ok(manquant), ok(zero), ok(zero)])
		]);

		for (const scenario of [
			{ nom: 'dpe-energie-manquante', attendu: 'Non renseigné' },
			{ nom: 'dpe-energie-zero', attendu: '0' }
		] as const) {
			for (const width of [1440, 375] as const) {
				await page.setViewportSize({ width, height: 900 });
				await open(page, `/dpe/${NUMERO}`);
				const energie = repere(page, 'energie');
				await expect(energie.locator('.dpe-tag')).toHaveText('Classe C');
				if (scenario.nom === 'dpe-energie-manquante') {
					const badge = energie.locator('.kpi-value .state-badge');
					await expect(badge).toHaveText(scenario.attendu);
					await expect(energie.locator('.kpi-meta')).toHaveCount(0);
					if (width === 375) {
						for (const word of ['Non', 'renseigné']) {
							const tops = await topsDuTexte(badge, word);
							expect(tops.length, `mot ${word} mesurable à 375 px`).toBeGreaterThan(0);
							expect(Math.max(...tops) - Math.min(...tops), `mot ${word} entier à 375 px`).toBeLessThanOrEqual(1);
						}
						const source = energie.getByRole('button', {
							name: 'Source et définition : Étiquette énergie',
							exact: true
						});
						await verifierGeometrieMobile(energie, source, [{ nom: 'badge Non renseigné', locator: badge }]);
					}
				} else {
					await expect(energie.locator('.state-value-kpi .value')).toHaveText(scenario.attendu);
					await expect(energie.locator('.state-value-kpi .unit')).toHaveText(' kWhEP/m²/an');
				}
				await expect(repere(page, 'climat').locator('.dpe-tag')).toHaveText('Classe B');
				await expect(repere(page, 'logement').locator('.kpi-value .value')).toHaveText(`Maison · 81,9${NB}m²`);
				await expect(repere(page, 'cout').locator('.kpi-value .value')).toHaveText(new Intl.NumberFormat('fr-FR').format(1240));
				const tagBeforeResult = await energie.evaluate((card) => {
					const tag = card.querySelector('.dpe-tag');
					const result = card.querySelector('.kpi-line > .kpi-value .state-value-kpi, .kpi-line > .kpi-value .state-badge');
					return Boolean(tag && result && (tag.compareDocumentPosition(result) & Node.DOCUMENT_POSITION_FOLLOWING));
				});
				expect(tagBeforeResult, `classe avant ${scenario.nom} à ${width} px`).toBe(true);
				await sansDebordement(page);
				await capturerEtatDpe(page, testInfo, scenario.nom, width, energie);
			}
		}
	});

	// D45-05, counter-review I3 of plan 04.5-12: total 10 258 and surface 100 would give « 103 »; the
	// landmark shows the published value (63), a published zero, or « Non renseigné » — never a quotient.
	for (const etiquette of [
		{ id: 'energie', champ: 'dpe.document.conso_ep_m2', total: 'dpe.document.conso_ep', unite: 'kWhEP/m²/an', uniteTotal: 'kWhEP/an', document: documentAvecMesureEnergieM2 },
		{ id: 'climat', champ: 'dpe.document.emissions_ges_m2', total: 'dpe.document.emissions_ges', unite: 'kg CO₂ éq/m²/an', uniteTotal: 'kg CO₂ éq/an', document: documentAvecMesureClimatM2 }
	] as const) {
		test(`repère ${etiquette.id} : valeur par m² publiée (63), zéro publié, non renseignée — jamais total ÷ surface`, async ({ page, request }) => {
			const cas = [
				{ nom: 'publiee', document: etiquette.document({ valeur: 63 }, 10258, 100) },
				{ nom: 'zero', document: etiquette.document({ valeur: 0 }, 10258, 100) },
				{ nom: 'non-renseignee', document: etiquette.document({ etat: 'not_reported', valeur: null, unite: null }, 10258, 100) }
			];
			await configure(request, [attente(`/v1/dpe/${NUMERO}`, cas.map(({ document }) => ok(document)))]);
			for (const { nom } of cas) {
				await open(page, `/dpe/${NUMERO}`);
				const ligne = repere(page, etiquette.id).locator(`.kpi-line[data-field-id="${etiquette.champ}"]`);
				await expect(ligne).toHaveCount(1);
				if (nom === 'non-renseignee') {
					await expect(ligne.locator('.state-absence')).toHaveAttribute('data-state', 'not_reported');
					await expect(ligne.locator('.state-badge')).toHaveText('Non renseigné');
					await expect(ligne.locator('.kpi-meta')).toHaveCount(0);
				} else {
					await expect(ligne.locator('.state-value .value')).toHaveText(nom === 'zero' ? '0' : '63');
					await expect(ligne.locator('.state-value .unit')).toHaveText(` ${etiquette.unite}`);
					await expect(ligne.locator('.kpi-meta')).toHaveText('par m² · 5 usages');
				}
				// The served total stays in the detail, the surface as served; no quotient anywhere.
				await expect(page.locator(`dl.tab [data-field-id="${etiquette.total}"] .state-value`)).toHaveText(
					`${new Intl.NumberFormat('fr-FR').format(10258)} ${etiquette.uniteTotal}`
				);
				await expect(page.locator('dl.tab [data-field-id="dpe.document.surface_habitable"] .state-value')).toHaveText(`100${NB}m²`);
				expect(await repere(page, etiquette.id).innerText()).not.toMatch(/\b10[23]\b/);
			}
		});
	}

	// UI-010 of the 2026-10-24 UI review: 10 258 kg CO₂ éq/an read « 10 25 » then « 8 kg » at 320-390 px.
	// Plan 04.5-12: the landmark reads the per-m² value; the five-digit totals are in the detail, where
	// each number must stay on one line too.
	test('repère climat et totaux du détail : chaque nombre reste sur une ligne, classe et Source en tête, de 320 à 390 px', async ({
		page,
		request
	}, testInfo) => {
		const largeurs = [320, 375, 390] as const;
		const document = documentAvecMesureClimatM2({ valeur: 125 }, 10258);
		document.reperes.etiquette_energie = { ...document.reperes.etiquette_energie, mesure: valeurDpe({ field_id: 'dpe.document.conso_ep', valeur: 127982.4, unite: 'kwh_ep_per_year' }) };
		await configure(request, [attente(`/v1/dpe/${NUMERO}`, largeurs.map(() => ok(document)))]);

		for (const width of largeurs) {
			await page.setViewportSize({ width, height: 900 });
			await open(page, `/dpe/${NUMERO}`);
			const climat = repere(page, 'climat');
			const total = climat.locator('.state-value-kpi .value');
			const unite = climat.locator('.state-value-kpi .unit');
			await expect(total).toHaveText('125');
			await expect(unite).toHaveText(' kg CO₂ éq/m²/an');

			const tops = await topsDuTexte(total);
			expect(tops.length, `valeur 125 mesurable à ${width} px`).toBeGreaterThan(0);
			expect(Math.max(...tops) - Math.min(...tops), `valeur 125 sur une seule ligne à ${width} px`).toBeLessThanOrEqual(1);
			for (const [champ, attendu] of [
				['dpe.document.conso_ep', new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 }).format(127982.4)],
				['dpe.document.emissions_ges', new Intl.NumberFormat('fr-FR').format(10258)]
			]) {
				const nombre = page.locator(`dl.tab [data-field-id="${champ}"] .state-value .value`);
				await expect(nombre).toHaveText(attendu);
				const lignes = await topsDuTexte(nombre);
				expect(lignes.length, `${champ} mesurable à ${width} px`).toBeGreaterThan(0);
				expect(Math.max(...lignes) - Math.min(...lignes), `${champ} ${attendu} sur une seule ligne à ${width} px`).toBeLessThanOrEqual(1);
			}

			const classeAvantTotal = await climat.evaluate((card) => {
				const tag = card.querySelector('.dpe-tag');
				const value = card.querySelector('.state-value-kpi');
				return Boolean(tag && value && (tag.compareDocumentPosition(value) & Node.DOCUMENT_POSITION_FOLLOWING));
			});
			expect(classeAvantTotal, `classe climat avant le total à ${width} px`).toBe(true);
			const tailleClasse = await climat.locator('.dpe-tag').evaluate((element) => element.getBoundingClientRect().height);
			// 04.8-02 (UI-SPEC v4 § 11): the secondary pastille, 14 px and 4 px above and below (32 px before).
			expect(tailleClasse, `classe climat à sa taille secondaire à ${width} px`).toBe(22);

			const source = climat.getByRole('button', { name: 'Source et définition : Étiquette climat', exact: true });
			await verifierGeometrieMobile(climat, source, [
				{ nom: 'valeur 125', locator: total },
				{ nom: 'unité kg CO₂ éq/m²/an', locator: unite }
			]);
			await sansDebordement(page);
			await climat.screenshot({ path: dpeEnergyEvidencePath(testInfo, `dpe-climat-10258-${width}.png`), animations: 'disabled' });
		}
	});

	// Review constat of 2026-10-26: only the NUMBERS keep their digits on one line. A served text
	// characteristic, however long, wraps in its row and its Source button stays in the 320 px window.
	test('caractéristique textuelle longue : retour à la ligne, Source dans la fenêtre à 320 px, aucun débordement', async ({
		page,
		request
	}) => {
		const texte = "Installation collective avec plusieurs générateurs de chauffage et production d'eau chaude sanitaire";
		const base = documentAvecMesureClimatM2({ valeur: 125 }, 10258);
		const document = {
			...base,
			caracteristiques: [valeurDpe({ field_id: 'dpe.document.installation_chauffage', valeur: texte, unite: 'none' }), ...base.caracteristiques]
		};
		await configure(request, [attente(`/v1/dpe/${NUMERO}`, [ok(document)])]);
		await page.setViewportSize({ width: 320, height: 900 });
		await open(page, `/dpe/${NUMERO}`);

		const ligne = page.locator('.tab-l[data-field-id="dpe.document.installation_chauffage"]');
		const valeurTexte = ligne.locator('.state-value .value');
		await expect(valeurTexte).toHaveText(texte);
		const source = ligne.getByRole('button', { name: 'Source et définition : Installation de chauffage', exact: true });
		// The hit area is 44 px pulled back by negative margins (SourceButton): the « i », not its box, sits
		// in the row.
		const [boiteTexte, boiteSource, boiteDisque, boiteLigne] = await Promise.all([
			valeurTexte.boundingBox(),
			source.boundingBox(),
			source.locator('svg').boundingBox(),
			ligne.boundingBox()
		]);
		expect(boiteTexte && boiteSource && boiteDisque && boiteLigne, 'texte, Source et ligne mesurables à 320 px').toBeTruthy();
		expect(boiteTexte!.height, 'texte servi sur plusieurs lignes à 320 px').toBeGreaterThan(30);
		expect(boiteSource!.x, 'Source dans la fenêtre, à gauche').toBeGreaterThanOrEqual(0);
		expect(boiteSource!.x + boiteSource!.width, 'Source dans la fenêtre, à droite').toBeLessThanOrEqual(320);
		expect(boiteDisque!.x + boiteDisque!.width, 'disque Source dans la ligne').toBeLessThanOrEqual(boiteLigne!.x + boiteLigne!.width);
		// The « i » stands at the end of the row, beside the served value, never over it.
		expect(boiteTexte!.x + boiteTexte!.width, 'texte servi à gauche du i, sans recouvrement').toBeLessThanOrEqual(boiteDisque!.x + 1);
		await sansDebordement(page);
	});

	test('ancien document : classe, total servi et mention de méthode restent distincts', async ({ page, request }, testInfo) => {
		const ancien = documentDpe({
			methode: 'ancienne',
			statut: { code: 'expire', validite: 'expired', date: null, remplace_par: null, remplace: null }
		});
		await configure(request, [attente(`/v1/dpe/${NUMERO}`, [ok(ancien), ok(ancien)])]);
		for (const width of [1440, 375] as const) {
			await page.setViewportSize({ width, height: 900 });
			await open(page, `/dpe/${NUMERO}`);
			const energie = repere(page, 'energie');
			await expect(page.locator('.document-badge')).toHaveText(['Ancienne méthode', 'Expiré']);
			await expect(energie.locator('.dpe-tag')).toHaveText('Classe C');
			await expect(energie.locator('.state-value-kpi .value')).toHaveText('104');
			await expect(energie.locator('.state-value-kpi .unit')).toHaveText(' kWhEP/m²/an');
			await capturerEtatDpe(page, testInfo, 'dpe-energie-ancien-document', width, energie);
		}
	});

	// P2 (2026-11-10): « Adresse et rattachement » and « Provenance et validité » are folds, closed while the
	// status says « En vigueur »; the address fold is opened here to read it. T6: « Accueil » at the root.
	test('4 repères, une section ouverte et deux replis, adresse publiée, BAN retenue, provenance, statut ; axe sans violation', async ({ page, request }) => {
		await configure(request, [attente(`/v1/dpe/${NUMERO}`, [ok(documentDpe())])]);
		await open(page, `/dpe/${NUMERO}`);

		const fil = page.getByRole('navigation', { name: "Fil d'Ariane" });
		await expect(fil.getByRole('link')).toHaveText(['Accueil', 'La Roche-sur-Yon', '12 Rue des Lilas']);
		await expect(page.locator('dl.kpi-strip > .cle > dt')).toHaveText([
			'Étiquette énergie',
			'Étiquette climat',
			'Type et surface déclarés',
			'Coût annuel estimé des énergies'
		]);
		await expect(repere(page, 'climat').locator('.dpe-tag')).toHaveText('Classe B');
		await expect(repere(page, 'logement').locator('.kpi-value .value')).toHaveText(`Maison · 81,9${NB}m²`);
		await expect(repere(page, 'cout').locator('.kpi-value .unit')).toHaveText(' €/an');
		for (const titre of ['Ce que déclare le document', 'Adresse et rattachement', 'Provenance et validité']) {
			await expect(page.getByRole('heading', { level: 2, name: titre })).toBeVisible();
		}
		await expect(page.locator('details#rattachement')).not.toHaveAttribute('open');
		await expect(page.locator('details#provenance')).not.toHaveAttribute('open');
		await page.locator('details#rattachement > summary').click();

		const declaree = page.locator('[data-ligne="adresse-declaree"] dd');
		await expect(declaree).toHaveText(`«${NNB}14 rue des Lilas${NNB}»`);
		const ban = page.locator('[data-ligne="adresse-ban"] dd');
		await expect(ban.getByRole('link')).toHaveAttribute('href', `/adresse/${BAN_ID}`);
		await expect(ban).toContainText('clé BAN déclarée par la source, non vérifiée');
		await expect(page.getByText(`L'adresse déclarée diffère de l'adresse retenue${NNB}; les deux sont conservées.`)).toBeVisible();
		await expect(page.getByText("Ce document n'est rattaché à aucun logement précis.")).toHaveCount(1);
		await expect(page.locator('[data-field-id="dpe.document.isolation"] .tab-v .state-badge')).toHaveText('Non renseigné');

		const source = page.locator('[data-ligne="source"] a');
		await expect(source).toHaveText('DPE des logements existants');
		await expect(source).toHaveAttribute('href', 'https://data.ademe.fr/datasets/dpe03existant');
		await expect(page.locator('[data-ligne="statut"] dd')).toHaveText('En vigueur');
		await expect(page.getByRole('link', { name: "Voir l'adresse" })).toHaveAttribute('href', `/adresse/${BAN_ID}`);
		// Owner's wording of 2026-10-05 (04-DECISIONS-TEXTES, « Textes et choix de 04-04 », rows 1, 12, 15); since
		// P4 (2026-11-10) the method is said in « Provenance et validité » only, no longer in the identity line.
		await expect(page.locator('.document-identity')).not.toContainText('méthode');
		await expect(page.locator('[data-ligne="methode"] dd')).toHaveText('Méthode actuelle (depuis juillet 2021)');
		await expect(page.locator('dl.kpi-strip > .cle .kpi-meta')).toHaveText([
			'par m² · 5 usages',
			'par m² · 5 usages',
			'estimation à occupation standard'
		]);
		await expect(repere(page, 'logement').locator('.kpi-meta')).toHaveCount(0);
		await expect(page.locator('[data-provisoire]')).toHaveCount(0);
		// Row 15 of 04-04: « Comparer avec un autre DPE » added by 04-10, the one primary action.
		await expect(page.getByRole('link', { name: 'Comparer avec un autre DPE' })).toHaveAttribute('href', `/comparer?dpe=${NUMERO}`);

		// One accent element at most per public route (garde `accent-unique`): the comparison link.
		const accents = await page.evaluate(() => {
			const sonde = document.createElement('span');
			sonde.style.background = 'var(--color-accent)';
			// Resolve the neutral-state oracle in the same public theme as the document.
			document.querySelector('.public-site')!.append(sonde);
			const accent = getComputedStyle(sonde).backgroundColor;
			sonde.remove();
			return [...document.querySelectorAll('main *')].filter((el) => getComputedStyle(el).backgroundColor === accent).length;
		});
		expect(accents).toBeLessThanOrEqual(1);

		const texte = await texteDePage(page);
		expect(texte).not.toMatch(/non disponible/i);
		expect(texte).not.toMatch(/le logement (consomme|émet|est classé|a )|DPE (du|de ce) logement|DPE de l'adresse|attribué à ce logement/i);

		const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
		expect(axe.violations.map((v) => v.id)).toEqual([]);
	});

	test('adresse masquée : « non affichée : {motif} », sans phrase de divergence', async ({ page, request }) => {
		const masquee = documentDpe({ adresse_declaree: { etat: 'masquee', texte: null, motif: 'complement_present' } });
		await configure(request, [attente(`/v1/dpe/${NUMERO}`, [ok(masquee)])]);
		await open(page, `/dpe/${NUMERO}`);

		await expect(page.locator('[data-ligne="adresse-declaree"] dd')).toHaveText(
			`non affichée${NB}: le document y joint un complément (étage, porte, bâtiment ou nom).`
		);
		await expect(page.getByText("L'adresse déclarée diffère", { exact: false })).toHaveCount(0);
	});

	test('pas d’adresse BAN servie : fil d’Ariane court, pas de « Voir l’adresse »', async ({ page, request }) => {
		await configure(request, [attente(`/v1/dpe/${NUMERO}`, [ok(documentDpe({ adresse_ban: SANS_CLE, adresse_divergente: null }))])]);
		await open(page, `/dpe/${NUMERO}`);

		// T6 (decided by Ianis on 2026-11-10): « Accueil » at the root, « Recherche » before.
		await expect(page.getByRole('navigation', { name: "Fil d'Ariane" }).getByRole('link')).toHaveText(['Accueil']);
		await expect(page.getByRole('link', { name: "Voir l'adresse" })).toHaveCount(0);
		await expect(page.locator('[data-ligne="adresse-ban"] .state-badge')).toHaveText('Non renseigné');
	});

	test('remplacé selon l’ADEME : lien vers le remplaçant, chargement en navigation client, puis la page du remplaçant', async ({ page, request }) => {
		const statut = { code: 'remplace', validite: 'expired', date: null, remplace_par: NUMERO_B, remplace: null };
		const remplacant = documentDpe({
			numero: NUMERO_B,
			source_identity: { reference: NUMERO_B, sources: [] },
			statut: { code: 'remplacement_non_verifie', validite: 'not_expired_by_date', date: null, remplace_par: null, remplace: NUMERO }
		});
		await configure(request, [
			attente(`/v1/dpe/${NUMERO}`, [ok(documentDpe({ statut }))]),
			attente(`/v1/dpe/${NUMERO_B}`, [{ ...ok(remplacant), gate: 'doc-b' }])
		]);
		await open(page, `/dpe/${NUMERO}`);

		await expect(page.locator('.document-badge')).toHaveText(['Expiré']);
		const ligne = page.locator('[data-ligne="statut"] dd');
		await expect(ligne).toHaveText(`Remplacé par le DPE n°${NB}${NUMERO_B} selon l'ADEME`);
		await ligne.getByRole('link').click();
		// The pointer leaves the link, so that no hover preload reads anything undeclared.
		await page.locator('.document-overline').hover();

		const chargement = page.locator('[aria-busy="true"]');
		await expect(chargement.getByRole('heading', { level: 2 })).toHaveText('Chargement du document…');
		await expect(chargement.locator('[data-squelette] .cle-squelette')).toHaveCount(4);
		const relache = await request.post(`${E2E_SOURCE_ORIGIN}/__release`, { data: { gate: 'doc-b' } });
		expect(relache.ok()).toBe(true);

		await page.waitForURL(`**/dpe/${NUMERO_B}`);
		// UI-SPEC v4 § 6.7 (04.8-04): the served number is under the h1, « N° {numéro} ».
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(/^Diagnostic de performance énergétique/);
		await expect(page.locator('.document-sous .document-numero')).toHaveText(`N°${NB}${NUMERO_B}`);
		await expect(page.locator('[data-ligne="statut"] dd')).toHaveText('Remplacement non vérifié');
		await expect(page.locator('[data-ligne="document-remplace"] a')).toHaveAttribute('href', `/dpe/${NUMERO}`);
	});

	test('clés brutes injectées dans la réponse : absentes du HTML SSR et de la page hydratée', async ({ page, request }) => {
		const contamine = {
			...documentDpe({ adresse_declaree: { etat: 'masquee', texte: `12 rue ${SENTINELLE}`, motif: 'complement_present' } }),
			adresse_brut: `${SENTINELLE}-BRUT`,
			adresse_complete_brut: `${SENTINELLE}-COMPLETE`,
			complement_adresse_logement: `${SENTINELLE}-COMPLEMENT`
		};
		await configure(request, [attente(`/v1/dpe/${NUMERO}`, [ok(contamine)])]);

		const servie = await page.goto(`/dpe/${NUMERO}`);
		expect((await servie?.text()) ?? '').not.toContain(SENTINELLE);
		// UI-SPEC v4 § 6.7 (04.8-04): the served number is under the h1, « N° {numéro} ».
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(/^Diagnostic de performance énergétique/);
		await expect(page.locator('.document-sous .document-numero')).toHaveText(`N°${NB}${NUMERO}`);
		expect(await page.content()).not.toContain(SENTINELLE);
	});
});

// Reading of the reference pages (inventory of 2026-11-09, P1 to P6, T2; session « Document DPE »).
test.describe('Page document — lecture des pages de référence (2026-11-10)', () => {
	test('P1 : les classes dans le premier écran, avant les actions, à 375 et 1 440 px', async ({ page, request }) => {
		await configure(request, [attente(`/v1/dpe/${NUMERO}`, [ok(documentDpe()), ok(documentDpe())])]);
		for (const { width, height } of [{ width: 375, height: 812 }, { width: 1440, height: 900 }]) {
			await page.setViewportSize({ width, height });
			await open(page, `/dpe/${NUMERO}`);
			const echelle = page.locator('[data-classe-declaree="energie"] ol.echelle-dpe');
			const [boiteEchelle, boiteActions] = await Promise.all([echelle.boundingBox(), page.locator('.document-actions').boundingBox()]);
			expect(boiteEchelle && boiteActions, `échelle et actions mesurables à ${width} px`).toBeTruthy();
			expect(boiteEchelle!.y + boiteEchelle!.height, `échelle énergie dans le premier écran à ${width} px`).toBeLessThanOrEqual(height);
			expect(boiteActions!.y, `actions sous les classes à ${width} px`).toBeGreaterThan(boiteEchelle!.y);
			await expect(page.locator('.document-header a, .document-header button')).toHaveCount(0);
			await sansDebordement(page);
		}
	});

	test('P2 : les deux replis s’ouvrent au clavier, leur contenu devient visible ; un statut expiré ouvre la provenance', async ({ page, request }) => {
		const expire = documentDpe({ statut: { code: 'expire', validite: 'expired', date: '2025-01-01', remplace_par: null, remplace: null } });
		await configure(request, [attente(`/v1/dpe/${NUMERO}`, [ok(documentDpe()), ok(expire)])]);
		await open(page, `/dpe/${NUMERO}`);

		const rattachement = page.locator('details#rattachement');
		await expect(page.getByText("Ce document n'est rattaché à aucun logement précis.")).toBeHidden();
		await rattachement.locator('summary').focus();
		await page.keyboard.press('Enter');
		await expect(rattachement).toHaveAttribute('open');
		await expect(page.getByText("Ce document n'est rattaché à aucun logement précis.")).toBeVisible();
		const resume = await rattachement.locator('summary').boundingBox();
		expect(resume!.height, 'résumé du repli : cible de 44 px au moins').toBeGreaterThanOrEqual(44);
		await expect(page.locator('[data-ligne="statut"]')).toBeHidden();
		const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
		expect(axe.violations.map((v) => v.id)).toEqual([]);

		await open(page, `/dpe/${NUMERO}`);
		await expect(page.locator('details#provenance')).toHaveAttribute('open');
		await expect(page.locator('[data-ligne="statut"] dd')).toBeVisible();
		await expect(page.locator('details#rattachement')).not.toHaveAttribute('open');
	});

	test('P3 : le volet d’une tuile et d’une ligne du détail commence par « En clair »', async ({ page, request }) => {
		await configure(request, [attente(`/v1/dpe/${NUMERO}`, [ok(documentDpe())])]);
		await open(page, `/dpe/${NUMERO}`);

		await repere(page, 'energie').getByRole('button', { name: 'Source et définition : Étiquette énergie', exact: true }).click();
		const volet = repere(page, 'energie').locator('dialog.source-panel');
		await expect(volet.locator('.source-panel-list > .source-panel-entry > dt').first()).toHaveText('En clair');
		await expect(volet.locator('.source-panel-list > .source-panel-entry').first()).toContainText('Classe énergie, de A très performant à G très énergivore');
		// P5: « EP » of « kWhEP » is said in plain words in the same panel.
		await expect(volet).toContainText("kWhEP se lit kilowattheures d'énergie primaire.");
		await page.keyboard.press('Escape');

		const ligne = page.locator('dl.tab [data-field-id="dpe.document.periode_construction"]');
		await ligne.getByRole('button', { name: 'Source et définition : Période de construction', exact: true }).click();
		await expect(ligne.locator('dialog .source-panel-list > .source-panel-entry > dt').first()).toHaveText('En clair');
	});

	test('P6, T2 : panneaux à la teinte logement, liens à l’accent, boutons du socle en graisse 400', async ({ page, request }) => {
		await configure(request, [attente(`/v1/dpe/${NUMERO}`, [ok(documentDpe())])]);
		await open(page, `/dpe/${NUMERO}`);

		const couleurs = await page.evaluate(() => {
			const sonde = (propriete: string, valeur: string) => {
				const span = document.createElement('span');
				span.style.setProperty(propriete, valeur);
				document.querySelector('.public-site')!.append(span);
				const lue = getComputedStyle(span).getPropertyValue(propriete);
				span.remove();
				return lue;
			};
			const lien = document.querySelector('[data-ligne="source"] a')!;
			const titre = document.querySelector('#classes > h2')!;
			return {
				lien: getComputedStyle(lien).color,
				accent: sonde('color', 'var(--color-accent)'),
				titre: getComputedStyle(titre).color,
				encreLogement: sonde('color', 'var(--color-logement-encre)')
			};
		});
		expect(couleurs.lien).toBe(couleurs.accent);
		expect(couleurs.titre).toBe(couleurs.encreLogement);
		const voir = page.getByRole('link', { name: "Voir l'adresse" });
		await expect(voir).toHaveClass(/bouton-secondaire/);
		await expect(voir).toHaveCSS('font-weight', '400');
		await expect(page.getByRole('link', { name: 'Comparer avec un autre DPE' })).toHaveClass(/bouton-primaire/);
	});
});

test.describe('Page document — états (SEM-01)', () => {
	test('DPE inconnu (404) : couverture nommée, deux liens, jamais une panne', async ({ page, request }, testInfo) => {
		await configure(request, [
			attente(`/v1/dpe/${NUMERO}`, [
				probleme(404, 'dpe_inconnu', 'DPE inconnu', { couverture: { departements: ['44', '85'] } }),
				probleme(404, 'dpe_inconnu', 'DPE inconnu', { couverture: { departements: ['44', '85'] } })
			])
		]);
		for (const width of [1440, 375] as const) {
			await page.setViewportSize({ width, height: 900 });
			const reponse = await open(page, `/dpe/${NUMERO}`);
			// 2026-10-05 (04-DECISIONS-TEXTES, 04-04 row 13): the page answers with the status of its state.
			expect(reponse?.status()).toBe(404);
			await expect(page.getByRole('heading', { level: 1 })).toHaveText(`DPE n°${NB}${NUMERO}`);
			await expect(page.locator('main')).toContainText(
				`Aucun document DPE ne porte ce numéro dans la base importée par ce site, qui couvre Loire-Atlantique${NB}(44) et Vendée${NB}(85). Cela ne veut pas dire qu'il n'existe pas.`
			);
			await expect(page.getByRole('link', { name: 'Revenir à la recherche' })).toHaveAttribute('href', '/');
			await expect(page.getByRole('link', { name: 'Voir la couverture des données' })).toHaveAttribute('href', '/datasets');
			await expect(page.locator('main')).not.toContainText(/\binstance\b/i);
			await expect(page.getByText(/momentanément indisponible/)).toHaveCount(0);
			await expect(page.getByRole('alert')).toHaveCount(0);
			await page.screenshot({
				path: publicMessagesEvidencePath(testInfo, `dpe-inconnu-${width}.png`),
				animations: 'disabled'
			});
			await capturerEtatDpe(page, testInfo, 'dpe-energie-404-couvert', width);
		}
	});

	test('DPE inconnu sans territoire importé : absence de couverture explicite, aucun repère', async ({
		page,
		request
	}, testInfo) => {
		await configure(request, [
			attente(`/v1/dpe/${NUMERO}`, [
				probleme(404, 'dpe_inconnu', 'DPE inconnu', { couverture: { departements: [] } }),
				probleme(404, 'dpe_inconnu', 'DPE inconnu', { couverture: { departements: [] } })
			])
		]);
		for (const width of [1440, 375] as const) {
			await page.setViewportSize({ width, height: 900 });
			const reponse = await open(page, `/dpe/${NUMERO}`);
			expect(reponse?.status()).toBe(404);
			await expect(page.locator('main')).toContainText("Aucun document DPE n'est importé par ce site.");
			await expect(page.locator('main')).not.toContainText(/\binstance\b/i);
			await expect(page.locator('.kpi-strip')).toHaveCount(0);
			await capturerEtatDpe(page, testInfo, 'dpe-energie-hors-couverture', width);
		}
	});

	test('numéro mal formé : message, aucun appel à l’API', async ({ page, request }) => {
		await configure(request, []);
		const reponse = await open(page, '/dpe/2485e0000003c');

		// 2026-10-05 (04-04 rows 13 and 14): 404, h1 « Numéro DPE non valide », the input in the sentence only.
		expect(reponse?.status()).toBe(404);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('Numéro DPE non valide');
		await expect(page).toHaveTitle('Numéro DPE non valide — DataCommune');
		await expect(page.locator('main')).toContainText(`«${NNB}2485e0000003c${NNB}» n'a pas la forme d'un numéro DPE de l'ADEME.`);
		await expect(page.locator('.kpi-strip')).toHaveCount(0);
		// The exact contract of the source (afterEach) refuses any request: none was made.
	});

	test('panne (503, publication refusée comprise) : encart warn, aucune valeur, « Recharger le document »', async ({ page, request }, testInfo) => {
		await configure(request, [
			attente(`/v1/dpe/${NUMERO}`, [
				{ status: 503, body: { detail: 'Données temporairement indisponibles : publication non validée.' } },
				ok(documentDpe()),
				{ status: 503, body: { detail: 'Données temporairement indisponibles : publication non validée.' } },
				ok(documentDpe())
			])
		]);
		for (const width of [1440, 375] as const) {
			await page.setViewportSize({ width, height: 900 });
			const reponse = await open(page, `/dpe/${NUMERO}`);

			expect(reponse?.status()).toBe(503);
			await expect(page.getByRole('heading', { level: 1 })).toHaveText(`DPE n°${NB}${NUMERO}`);
			const encart = page.getByRole('alert');
			await expect(encart.getByRole('heading', { level: 2 })).toHaveText('Ce document est momentanément indisponible');
			await expect(encart).toContainText("Le service n'a pas répondu. Aucune valeur n'est affichée plutôt qu'une valeur incomplète.");
			await expect(page.locator('.kpi-strip')).toHaveCount(0);
			await expect(page.getByText('publication non validée')).toHaveCount(0);
			await capturerEtatDpe(page, testInfo, 'dpe-energie-panne', width);

			await encart.getByRole('button', { name: 'Recharger le document' }).click();
			await expect(repere(page, 'energie').locator('.dpe-tag')).toHaveText('Classe C');
			await expect(page.getByRole('alert')).toHaveCount(0);
		}
	});

	test('429 : titré par panne.ts, jamais « momentanément indisponible »', async ({ page, request }) => {
		await configure(request, [attente(`/v1/dpe/${NUMERO}`, [{ status: 429, body: { title: 'Trop de demandes', status: 429 } }])]);
		const reponse = await open(page, `/dpe/${NUMERO}`);

		// A typed refusal is not an outage: the page renders it with 200 (same rule as /adresse/[id]).
		expect(reponse?.status()).toBe(200);

		await expect(page.getByRole('alert').getByRole('heading', { level: 2 })).toHaveText('Trop de requêtes pour le moment');
		await expect(page.getByText(/momentanément indisponible/)).toHaveCount(0);
	});
});

// Plan 04.5-12: a retired document (served without landmark, characteristic nor address) shows no
// landmark, no per-m² value and no total; its retired state stays. A SYNTHETIC answer that still
// carried landmarks (a contract defect) shows none either: the projection drops them.
test.describe('Page document — document retiré (plan 04.5-12)', () => {
	test('aucun repère, aucune valeur par m², aucun total ; l’état « Plus publié par l’ADEME » reste', async ({ page, request }) => {
		const contamine = structuredClone(DOCUMENT_RETIRE);
		(contamine.donnees as Record<string, unknown>).reperes = documentDpe().reperes;
		(contamine.donnees as Record<string, unknown>).caracteristiques = documentDpe().caracteristiques;
		await configure(request, [attente(`/v1/dpe/${RETIRE}`, [{ status: 200, body: DOCUMENT_RETIRE }, { status: 200, body: contamine }])]);
		for (let tour = 0; tour < 2; tour += 1) {
			await open(page, `/dpe/${RETIRE}`);
			// UI-SPEC v4 § 6.7 (04.8-04): the served number is under the h1, « N° {numéro} ».
			await expect(page.getByRole('heading', { level: 1 })).toHaveText(/^Diagnostic de performance énergétique/);
			await expect(page.locator('.document-sous .document-numero')).toHaveText(`N°${NB}${RETIRE}`);
			// The served status, without the word of its source action (text button since 04.8-02, E4).
			expect(
				await page.locator('[data-ligne="statut"] dd').evaluate((dd) => {
					const copie = dd.cloneNode(true) as Element;
					copie.querySelectorAll('.source-button').forEach((action) => action.remove());
					return (copie.textContent ?? '').replace(/\s+/g, ' ').trim();
				})
			).toBe("Plus publié par l'ADEME");
			await expect(page.locator('.kpi-strip')).toHaveCount(0);
			await expect(page.locator('[data-field-id]')).toHaveCount(0);
			const texte = await texteDePage(page);
			expect(texte).not.toMatch(/kWhEP|CO₂|par m²|total annuel|Consommation annuelle|Émissions annuelles|9\s?120|1\s?005/);
		}
	});
});

test.describe('Page document — Source de chaque valeur du détail (D45-08, plan 04.5-12)', () => {
	test('chaque ligne de « Ce que déclare le document » ouvre son volet au clavier ; le focus revient au bouton', async ({ page, request }) => {
		await configure(request, [attente(`/v1/dpe/${NUMERO}`, [ok(documentDpe())])]);
		await open(page, `/dpe/${NUMERO}`);

		const lignes = page.locator('dl.tab [data-field-id]');
		await expect(lignes).toHaveCount(6);
		for (const ligne of await lignes.all()) {
			const libelle = ((await ligne.locator(':scope > dt').textContent()) ?? '').trim();
			const bouton = ligne.getByRole('button', { name: `Source et définition : ${libelle}`, exact: true });
			await expect(bouton).toHaveCount(1);
			await bouton.focus();
			await bouton.press('Enter');
			const volet = ligne.locator('dialog.source-panel');
			await expect(volet).toBeVisible();
			await expect(volet.getByRole('heading', { name: libelle })).toBeFocused();
			await expect(volet).toContainText('Diagnostics de performance énergétique (DPE)');
			await page.keyboard.press('Escape');
			await expect(volet).toBeHidden();
			await expect(bouton).toBeFocused();
		}
		const emissions = page.locator('dl.tab [data-field-id="dpe.document.emissions_ges"]');
		await expect(emissions.locator(':scope > dt')).toHaveText('Émissions annuelles de gaz à effet de serre');
		await expect(emissions.locator('dialog')).toContainText('kg CO₂ éq/an');
		const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
		expect(axe.violations.map((v) => v.id)).toEqual([]);
	});
});

test.describe('Page document — 320 px (UI-01)', () => {
	test.use({ viewport: { width: 320, height: 800 } });

	test('adresse déclarée très longue publiée : rendue entière, retour à la ligne, aucun débordement', async ({ page, request }) => {
		const longue = documentDpe({ adresse_declaree: { etat: 'publiee', texte: ADRESSE_LONGUE, motif: null } });
		await configure(request, [attente(`/v1/dpe/${NUMERO}`, [ok(longue)])]);
		await open(page, `/dpe/${NUMERO}`);

		const mesure = await mesurerAdresse(page);
		console.log(`RELEVE 320px adresse_declaree_longue hauteur=${mesure.hauteur}px scrollWidth=${mesure.scroll} innerWidth=${mesure.inner}`);
		expect(mesure.texte).toBe(`«${NNB}${ADRESSE_LONGUE}${NNB}»`);
		expect(mesure.coupe).toBe(false);
		await sansDebordement(page);
	});
});

test('Page document — zoom navigateur réel 200 % : adresse longue entière, aucun débordement', async ({}, testInfo) => {
	// Same method as adresse-recherche.spec.ts: a local MV3 extension sets Chromium's real tab zoom.
	const extension = mkdtempSync(join(tmpdir(), 'dc0404-zoom-extension-')); // Outside Vite's watched workspace.
	writeFileSync(
		`${extension}/manifest.json`,
		JSON.stringify({ manifest_version: 3, name: 'Local document zoom test', version: '1.0', permissions: ['tabs'], background: { service_worker: 'background.js' } })
	);
	writeFileSync(`${extension}/background.js`, 'chrome.runtime.onInstalled.addListener(() => {});');
	const context = await chromium.launchPersistentContext('', {
		channel: 'chromium',
		headless: true,
		baseURL: E2E_APP_ORIGIN,
		viewport: { width: 1280, height: 900 },
		args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`]
	});
	try {
		const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent('serviceworker'));
		const page = await context.newPage();
		await installLocalNetworkGuard(page);
		const longue = documentDpe({ adresse_declaree: { etat: 'publiee', texte: ADRESSE_LONGUE, motif: null } });
		await configure(page.request, [attente(`/v1/dpe/${NUMERO}`, [ok(longue)])]);
		await open(page, `/dpe/${NUMERO}`);

		const zoom = await worker.evaluate(async (appOrigin) => {
			const api = (globalThis as unknown as { chrome: { tabs: { query: (q: unknown) => Promise<{ id: number }[]>; setZoom: (id: number, z: number) => Promise<void>; getZoom: (id: number) => Promise<number> } } }).chrome;
			const tabs = await api.tabs.query({ url: `${appOrigin}/*` });
			if (tabs.length !== 1) throw new Error('Expected one local test tab');
			await api.tabs.setZoom(tabs[0].id, 2);
			return api.tabs.getZoom(tabs[0].id);
		}, E2E_APP_ORIGIN);
		expect(zoom).toBe(2);
		await expect.poll(() => page.evaluate(() => Math.abs(innerWidth - 640))).toBeLessThanOrEqual(0.5);

		const mesure = await mesurerAdresse(page);
		console.log(`RELEVE zoom200 adresse_declaree_longue hauteur=${mesure.hauteur}px scrollWidth=${mesure.scroll} innerWidth=${mesure.inner}`);
		writeFileSync(testInfo.outputPath('zoom-200.json'), JSON.stringify({ zoom, ...mesure }, null, 2));
		expect(mesure.texte).toBe(`«${NNB}${ADRESSE_LONGUE}${NNB}»`);
		expect(mesure.coupe).toBe(false);
		expect(mesure.scroll).toBeLessThanOrEqual(mesure.inner);
		await expectSourceContractMet(page.request);
		expectNoUnexpectedOffLocalRequest(page);
	} finally {
		await context.close();
	}
});

// Plan 04.5-12: the same answers captured before and after the redesign of the page (first screen and
// whole page at 1 440 × 900, 375 × 812 and 320 × 812): the real document of the UI review (observed
// values), the two documents of the synthetic place, the synthetic document of this spec, a retired one.
test('B Sauge — impression A4 du DPE observé : valeurs, source et validité conservées', async ({ page, request }, testInfo) => {
	await configure(request, [attente(`/v1/dpe/${REEL}`, [{ status: 200, body: DOCUMENT_REEL }])]);
	await open(page, `/dpe/${REEL}`);
	await attendreDeuxRendus(page);
	const valeurs = await page.locator('.state-value').allTextContents();
	expect(valeurs.length).toBeGreaterThan(3);
	await page.emulateMedia({ media: 'print' });
	await expect(page.locator('.document-actions')).toBeHidden();
	await expect(page.locator('.site-header')).toBeHidden();
	await expect(page.locator('[data-ligne="source"]')).toContainText('ADEME');
	await expect(page.locator('.document-identity')).toContainText(/valable/i);
	expect(await page.locator('.state-value').allTextContents()).toEqual(valeurs);
	await page.pdf({ path: testInfo.outputPath('dpe-observe-a4.pdf'), format: 'A4', printBackground: true });
	await expectSourceContractMet(request);
});

test('Captures avant/après du plan 04.5-12 : mêmes réponses, 1 440, 375 et 320 px', async ({ page, request }, testInfo) => {
	const largeurs = [
		{ width: 1440, height: 900 },
		{ width: 375, height: 812 },
		{ width: 320, height: 812 }
	] as const;
	const cas = [
		{ nom: 'reel-2644E8000030X', numero: REEL, reponse: DOCUMENT_REEL },
		{ nom: 'recette-recent', numero: RECENT, reponse: DOCUMENT_RECENT },
		{ nom: 'recette-ancien', numero: ANCIEN, reponse: DOCUMENT_ANCIEN },
		{ nom: 'synthetique', numero: NUMERO, reponse: { statut: 'ok', donnees: documentDpe(), meta: { source: [SOURCE_DPE] } } },
		{ nom: 'retire', numero: RETIRE, reponse: DOCUMENT_RETIRE }
	];
	for (const { nom, numero, reponse } of cas) {
		await configure(request, [attente(`/v1/dpe/${numero}`, largeurs.map(() => ({ status: 200, body: reponse })))]);
		for (const { width, height } of largeurs) {
			await page.setViewportSize({ width, height });
			await open(page, `/dpe/${numero}`);
			// UI-SPEC v4 § 6.7 (04.8-04): the served number is under the h1, « N° {numéro} ».
			await expect(page.getByRole('heading', { level: 1 })).toHaveText(/^Diagnostic de performance énergétique/);
			await expect(page.locator('.document-sous .document-numero')).toHaveText(`N°${NB}${numero}`);
			await attendreDeuxRendus(page);
			await page.screenshot({ path: refonteEvidencePath(testInfo, `dpe-${nom}-${width}-ecran.png`), animations: 'disabled' });
			await page.screenshot({ path: refonteEvidencePath(testInfo, `dpe-${nom}-${width}-page.png`), animations: 'disabled', fullPage: true });
		}
		await expectSourceContractMet(request);
	}
});
