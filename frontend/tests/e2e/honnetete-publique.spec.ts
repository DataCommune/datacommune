import { readFileSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { test, expect, type Page } from '@playwright/test';
import { LIBELLE_HORS_COUVERTURE } from '../../src/lib/data/etats-libelles';
import {
	COMMUNE_META_SOURCE_NOMS,
	COMMUNE_META_SOURCE_ORDER
} from '../../src/lib/data/explications';
import { configureSsrSource } from './fixtures/bien-ssr-source';
import { COMMUNE_FLAT_85047 } from './fixtures/commune-indicators';
import {
	CONSTAT_D110,
	DOCUMENTS_EN_PANNE,
	DOCUMENTS_HORS_COUVERTURE,
	DOCUMENTS_ZERO,
	ROUTES_PHASE4,
	sourceAdresse,
	type Reply,
	type RoutePhase4
} from './fixtures/surfaces-phase4';
import { CONCLUSIONS_INTERDITES } from './fixtures/conclusions-interdites';
import { lireTheme } from './fixtures/themes';
import { monterVolet } from './fixtures/volets';

// Phase 49 « synthèse & états honnêtes » — anti-jargon gate (plan 49-07, D-11/D-12).
//
// Proves in a real browser that the public surfaces of the product — the home page, the Fiche
// Commune, and since 04-08 the address page and the DPE document page that replaced the Fiche Bien
// (D-101, `/bien` is a 308) — carry no internal vocabulary and no unjustified verdict:
//   - no internal level token (L0/L1/L2) anywhere;
//   - no machine-facing state identifier (out_of_coverage, not_reported…) anywhere;
//   - no normative verdict word on the two FICHES (D-01: a position is shown only when a
//     published referential backs it — see the scoping note below);
//   - no self-certification claim on the home page (D-21), in the DOM AND in the metadata.
//
// Structural copy of marketing-truth.spec.ts (FORBIDDEN_CLAIMS / domText / metadataText /
// findViolations): same mutation-provable shape, with the forbidden lists SCOPED PER SURFACE.
//
// /roadmap was scanned here on its two present-tense anchors (49-09 A-03, 49-10 WR-12) until
// phase 03-02 removed the route (D-39); its two tests left with the surface.
//
// API-independent by design: /auth/me is route-mocked (same policy as marketing-truth.spec.ts
// and phase42-uat.spec.ts), the home page performs zero data fetch at load time (D-38), and
// every fiche is rendered from contract-shaped answers of the local source (the address and
// document ones are shared with their own specs: fixtures/surfaces-phase4.ts). No private address
// is ever asserted: the addresses are synthetic.

// ---------------------------------------------------------------------------------------------
// LEXIQUE ADMIS — never to be added to any forbidden list below.
//
// 1. Official acronyms, each expanded for the reader by the ℹ️ dictionary (D-12):
//      DVF · DPE · GES · INSEE · Filosofi · RNIC · RNC · BAN · EPCI · ABC
//    They are the NAMES of public referentials and producers, not internal jargon. Banning them
//    would delete the very provenance this phase exists to display.
//
// 2. The verdict ban below is scoped to the two fiches. It used to be because the landing FAQ
//    asked « Conforme RGPD ? » (a question about regulatory compliance, not a verdict on data).
//    That FAQ left with the landing in 03-05 (D-38); verdict words on `/` are now guarded by
//    marketing-truth.spec.ts, whose forbidden list is the UI-SPEC « Textes interdits ».
// ---------------------------------------------------------------------------------------------

interface Motif {
	name: string;
	pattern: RegExp;
}

/** Banned on the THREE surfaces — internal vocabulary that must never reach a reader. */
const FORBIDDEN_PARTOUT: ReadonlyArray<Motif> = [
	{ name: 'jargon de niveau interne « L0 / L1 / L2 » (D-09)', pattern: /\bL[012]\b/ },
	{
		name: 'identifiant technique d’état ou de résolution (D-10)',
		pattern:
			/out_of_coverage|not_reported|temporary_error|not_applicable|dataset_states|DatasetResolutionState/
	}
];

/**
 * Banned on the TWO FICHES ONLY — never on `/` (see admission #2 of the lexique above).
 * A normative word with no published referential behind it is exactly the self-judgment that
 * D-01 removed from the mappers; if one reappears in the rendered copy, it was written by hand.
 */
const FORBIDDEN_FICHES: ReadonlyArray<Motif> = [
	{
		name: 'verdict normatif sans référentiel publié (D-01)',
		// Case-SENSITIVE on purpose: « avis conforme » (explications.ts, monuments historiques) is
		// a legal term inside a sourced definition, not a verdict. Adding /i would ban it.
		pattern: /\bFavorable\b|\bConforme\b|\bVigilance\b/
	},
	// The field_id `dpe.dpe_passoires_pct`, shown in the unfolded raw value of the panel, is not a
	// whole word followed by a boundary: it does not trip this guard (03-17 plan check, finding 5).
	{ name: 'verdict « passoire » (UI-SPEC, F44)', pattern: /\bpassoires?\b(?![_.])/i }
];

/**
 * Banned on `/` ONLY (scope D-21): the home page never certifies itself. If this list ever goes
 * red, the cause is the copy — NEVER the regex.
 */
const FORBIDDEN_LANDING: ReadonlyArray<Motif> = [
	{ name: 'auto-certification « certifié » (D-21)', pattern: /certifi(é|ée|és|ées|cation)/i }
];

/**
 * 04-UI-SPEC « Contrat de rédaction », « Textes interdits ajoutés » (04-08): banned on `/` and on
 * every phase 4 surface (results, address, document, both comparators), DOM and metadata. The D-110
 * phrase is NOT here: it is allowed when the finding is served (R06-09/9), and its negative case
 * is a test of its own below.
 */
const TEXTES_INTERDITS_PHASE4: ReadonlyArray<Motif> = [
	{ name: '« Fiche Bien »', pattern: /Fiche Bien/i },
	{ name: '« du bien »', pattern: /\bdu bien\b/i },
	{ name: '« ce bien »', pattern: /\bce bien\b/i },
	{ name: '« au bien »', pattern: /\bau bien\b/i },
	{ name: '« Logement recherché »', pattern: /Logement recherché/i },
	{ name: '« DPE du logement »', pattern: /DPE du logement/i },
	{ name: '« le DPE de l’adresse »', pattern: /le DPE de l['’]adresse/i },
	{ name: '« votre DPE »', pattern: /votre DPE/i },
	{ name: '« attribué à ce logement »', pattern: /attribué à ce logement/i },
	{ name: '« Exporter en PDF (dossier de mandat) »', pattern: /Exporter en PDF|dossier de mandat/i },
	{ name: '« Prêt pour le rendez-vous d’estimation ? »', pattern: /rendez-vous d['’]estimation/i },
	{ name: '« L’essentiel en 10 s »', pattern: /essentiel en 10\s*s/i },
	{ name: '« Adresse non retrouvée »', pattern: /Adresse non retrouvée/i },
	{ name: '« hors périmètre de la démonstration »', pattern: /hors périmètre de la démonstration/i },
	// Case-sensitive: the state label, capitalised as a badge; « non disponible » inside a sentence
	// is not a state label.
	// Any case (final review FRONT WR-02): « non disponible » is the same wording in lower case.
	{ name: '« Non disponible » en libellé d’état', pattern: /\bnon disponible\b/i },
	{ name: '« Bientôt »', pattern: /\bBientôt\b/i },
	{ name: '« Périmètre · »', pattern: /Périmètre\s*·/ },
	{ name: '« Ventes récentes dans l’immeuble »', pattern: /Ventes récentes dans l['’]immeuble/i },
	{ name: '« instantané »', pattern: /instantané/i },
	{ name: '« graphe de remplacement »', pattern: /graphe de remplacement/i },
	{ name: '« maille »', pattern: /\bmailles?\b/i },
	{ name: '« Document 01 »', pattern: /Document 01/i }
];

/**
 * Banned on the address page and the document page (04-08): the forbidden wordings of the DPE
 * display policy (04-UI-SPEC « Politique d'affichage DPE »), the vocabulary T13 (« Diagnostics »
 * stays the word of the commune counts; these pages say « DPE » and « documents DPE ») and the
 * landmark ③ decision of 2026-10-02. T13 names the documents of the address: « Diagnostics de
 * performance énergétique » (the published name of the ADEME dataset, served as the source of a
 * value: a proper name, admitted like the acronyms of the lexique above) and the shared print
 * mention of phase 3 (« ne remplace pas les diagnostics officiels », « Diagnostics et risques
 * officiels : ERRIAL · Géorisques », EnTetePrint, the regulatory diagnostics, not the DPE
 * documents of the address) are not that vocabulary — reading kept to Ianis in 04-08-SUMMARY.md.
 */
const FORBIDDEN_ADRESSE_DPE: ReadonlyArray<Motif> = [
	{ name: '« DPE du bien »', pattern: /DPE du bien/i },
	{
		name: '« le logement » sujet d’une valeur DPE',
		pattern: /le logement (consomme|émet|est classé|a une étiquette)/i
	},
	{ name: '« Diagnostics » (T13)', pattern: /Diagnostics(?! de performance énergétique| et risques officiels| officiels)/i },
	{ name: '« Risques recensés au point » (repère ③)', pattern: /Risques recensés au point/i },
	{ name: '« 0 risque » (repère ③)', pattern: /(?<![\d,.])0\s+risques?\b/i }
];

/** The D-110 phrase (04-19): rendered only from a served finding (DPE-01, P-D110-NULL). */
const PHRASE_D110 = /Une adresse, un bâtiment, un DPE\s*: les éléments concordent/;

/** « aucun DPE » / « 0 DPE »: a zero said about the inventory of the address. */
const ZERO_DPE = /aucun (document )?DPE|\b0 (document )?DPE\b/i;

function findViolations(text: string, ...listes: ReadonlyArray<Motif>[]): string[] {
	return listes
		.flat()
		.filter((m) => m.pattern.test(text))
		.map((m) => {
			// The matched text travels with the failure: a bare pattern name is not actionable.
			const trouve = m.pattern.exec(text)!;
			const contexte = text.slice(Math.max(0, trouve.index - 40), trouve.index + trouve[0].length + 40).replace(/\s+/g, ' ');
			return `${m.name} (motif ${m.pattern}) : « ${contexte} »`;
		});
}

/** Visible + hidden text of the current DOM, scripts/styles stripped (no bundler noise). */
async function domText(page: Page): Promise<string> {
	return page.evaluate(() => {
		const clone = document.body.cloneNode(true) as HTMLElement;
		clone.querySelectorAll('script, style').forEach((el) => el.remove());
		return clone.textContent ?? '';
	});
}

/** All metadata surfaces of the page: title + every <meta> content attribute. */
async function metadataText(page: Page): Promise<string> {
	return page.evaluate(() => {
		const metas = Array.from(document.querySelectorAll('head meta'))
			.map((m) => m.getAttribute('content') ?? '')
			.join('\n');
		return `${document.title}\n${metas}`;
	});
}

async function mockAuthAnonymous(page: Page) {
	await page.route('**/auth/me', (route) =>
		route.fulfill({
			status: 401,
			contentType: 'application/json',
			body: JSON.stringify({ detail: 'Non authentifié' })
		})
	);
}

// --- Fiche Commune fixture ---------------------------------------------------------------------

// Producer names, one per key of COMMUNE_META_SOURCE_ORDER and IN THAT ORDER: a
// `SourceAttribution` carries no dataset key, so its RANK is the whole contract (cf.
// explications.ts §COMMUNE_META_SOURCE_ORDER). THIRTEEN entries is not decoration — the
// `sourcesCommuneParDataset` zip is FAIL-CLOSED on any other length (T-49-12), so a 3-entry mock
// (phase42-uat.spec.ts) would render `{}` and the nominal « source affichée » path would never be
// exercised by this gate at all. The smoke assertion below is what proves it IS exercised.
const PRODUCTEURS: Record<(typeof COMMUNE_META_SOURCE_ORDER)[number], string> = {
	demographie: 'INSEE — Recensement de la population',
	revenus: 'INSEE — Filosofi',
	zonage_abc: 'Ministère chargé du logement — Zonage A/B/C',
	vacance: 'DGFiP — LOVAC',
	fiscalite: 'DGFiP — Taux de fiscalité locale',
	loyers: 'Ministère de la Transition écologique — Carte des loyers',
	dvf: 'DGFiP — Demandes de valeurs foncières',
	dpe: 'ADEME — Diagnostics de performance énergétique',
	equipements: 'INSEE — Base permanente des équipements',
	scolaire: "Ministère de l'Éducation nationale — Carte scolaire",
	monuments: 'Ministère de la Culture — Monuments historiques',
	foncier_pm: 'DGFiP — Fichiers fonciers (personnes morales)',
	connectivite: 'ARCEP — Le marché du haut et très haut débit fixe'
};

// Phase 50 (D-21 #4): `nom` now carries the REAL publication name served by the API
// (`DATASET_CATALOG[<dataset>].nom`, mirrored in COMMUNE_META_SOURCE_NOMS) instead of the
// producer labels above. Until now this mock was INFIDÈLE on that field — and since the
// attribution is keyed on the name, an infidèle mock would render `{}` and silently stop
// exercising the nominal « source affichée » path this gate exists to prove. `producteur`,
// `licence` and `url` are untouched.
const META_SOURCES_13 = COMMUNE_META_SOURCE_ORDER.map((dataset) => ({
	nom: COMMUNE_META_SOURCE_NOMS[dataset],
	licence: 'Licence ouverte',
	url: 'https://www.data.gouv.fr/',
	producteur: PRODUCTEURS[dataset]
}));

// Contract-shaped synthese: every flat column as served since D-47 (03-07), each null one with
// its declared state — the synthesis reads them and never guesses an undeclared absence.
const MOCK_COMMUNE_85047 = {
	statut: 'ok',
	donnees: [COMMUNE_FLAT_85047],
	meta: { source: META_SOURCES_13 }
};

async function mockCommune(page: Page) {
	return configureSsrSource(page.request, [
		{
			method: 'GET',
			pathname: '/v1/commune',
			query: { code_insee: '85047' },
			minCalls: 1,
			maxCalls: 1,
			responses: [{ status: 200, body: MOCK_COMMUNE_85047 }]
		}
	]);
}

/**
 * Opens a phase 4 surface on its synthetic source and returns the text read by the scan: the DOM at
 * rest, then the DOM with each « Source et définition » panel opened in turn (a panel is part of
 * the surface, as on the commune), and the metadata.
 */
async function lireSurface(page: Page, route: RoutePhase4): Promise<{ dom: string; meta: string; volets: number }> {
	await mockAuthAnonymous(page);
	const source = await configureSsrSource(page.request, route.source());
	const hydrated = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
	await page.goto(route.chemin);
	await hydrated;
	if (route.id === 'adresse') {
		// D-33: the street heading and the postal identity are rendered separately.
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('12 rue des Lilas', { timeout: 15_000 });
		await expect(page.locator('.adresse-identity')).toContainText('85000 La Roche-sur-Yon');
	} else {
		await expect(page.getByRole('heading', { level: 1 })).toContainText(route.titre, { timeout: 15_000 });
	}
	const textes: string[] = [await domText(page)];
	const boutons = page.getByRole('button', { name: /^Source et définition/ });
	const volets = await boutons.count();
	for (let i = 0; i < volets; i++) {
		const bouton = boutons.nth(i);
		if (!(await bouton.isVisible())) continue;
		await bouton.click();
		await expect(page.locator('dialog[open]')).toHaveCount(1);
		textes.push(await domText(page));
		await page.keyboard.press('Escape');
		await expect(page.locator('dialog[open]')).toHaveCount(0);
	}
	await source.assert();
	return { dom: textes.join('\n'), meta: await metadataText(page), volets };
}

// ===============================================================================================

test.describe('Honnêteté publique — accueil (D-11/D-21, D-38)', () => {
	test('le DOM de l’accueil ne porte ni jargon de niveau, ni identifiant d’état, ni « certifié »', async ({
		page
	}) => {
		await mockAuthAnonymous(page);
		// Attendre que l'accueil soit rendu et hydraté avant de relever le DOM, sinon le scan
		// porterait sur une page vide et serait vacuous : la réponse /auth/me n'arrive qu'une
		// fois le layout racine hydraté, et le champ de recherche est l'ancre de l'écran.
		const hydrated = page.waitForResponse(
			(response) => new URL(response.url()).pathname === '/auth/me'
		);
		await page.goto('/');
		await hydrated;
		// 04.8-07 (UI-SPEC § 3.13, § 6.6): the large field and the v4 h1 of the home.
		await expect(page.getByRole('combobox', { name: 'Adresse, commune ou code postal' })).toBeVisible();

		const texte = await domText(page);
		expect(texte, 'DOM vide — le scan serait vacuous').toContain(
			'Une adresse, en clair'
		);
		expect(
			findViolations(texte, FORBIDDEN_PARTOUT, FORBIDDEN_LANDING),
			'Jargon ou auto-certification dans le DOM de l’accueil'
		).toEqual([]);
	});

	test('les métadonnées de l’accueil sont propres (title + meta)', async ({
		page
	}) => {
		await mockAuthAnonymous(page);
		await page.goto('/');

		const meta = await metadataText(page);
		expect(meta, 'métadonnées vides — le scan serait vacuous').not.toBe('\n');
		expect(
			findViolations(meta, FORBIDDEN_PARTOUT, FORBIDDEN_LANDING),
			`Jargon ou auto-certification dans les métadonnées :\n${meta}`
		).toEqual([]);
	});
});

test.describe('Honnêteté publique — Fiche Commune (D-11/D-12)', () => {
	async function ouvrirFiche(page: Page) {
		await mockAuthAnonymous(page);
		const source = await mockCommune(page);
		const hydrated = page.waitForResponse((response) => response.url().endsWith('/auth/me'));
		await page.goto('/commune/85047');
		await hydrated;
		await expect(page.getByRole('heading', { name: 'Challans', level: 1 })).toBeVisible({ timeout: 15_000 });
		await source.assert();
	}

	test('le DOM de la fiche commune, volet source ouvert, ne porte ni jargon ni verdict normatif', async ({
		page
	}) => {
		await ouvrirFiche(page);

		// Sonde 1 — fiche au repos.
		const textes: string[] = [await domText(page)];

		// Sonde 2 — un volet source OUVERT, valeur brute dépliée : définitions, sources, états.
		await page.locator('[data-field-id="dvf.prix_m2_median_appartement"] .source-button').click();
		const volet = page.locator('dialog.source-panel[open]');
		await expect(volet).toBeVisible();
		await volet.getByText("Voir la donnée telle que l'API la renvoie").click();
		textes.push(await domText(page));

		expect(
			findViolations(textes.join('\n'), FORBIDDEN_PARTOUT, FORBIDDEN_FICHES),
			'Jargon ou verdict normatif dans le DOM de la fiche commune'
		).toEqual([]);
		// Since 03-08 the whole fiche — synthesis, the five themes and every source panel — is on the
		// D-47 vocabulary: none of its keys anywhere in the main content.
		const synthese = await page.locator('main').evaluate((main) => {
			const clone = main.cloneNode(true) as HTMLElement;
			clone.querySelectorAll('script, style').forEach((el) => el.remove());
			return clone.textContent ?? '';
		});
		expect(synthese).not.toMatch(
			/insufficient_sample|not_available|zero_denominator|not_qualified|suppressed|territoire_hors_perimetre|dataset_non_heberge|source_sans_couverture/
		);
	});

	test('chemin nominal « source » exercé : le volet source affiche une source réelle (W-06)', async ({
		page
	}) => {
		await ouvrirFiche(page);

		await page.locator('[data-kpi="prix-maisons"] .source-button').click();
		const volet = page.getByRole('dialog', { name: 'Prix médian maison (hors ventes sur plan)' });
		await expect(volet).toBeVisible();
		// Proof that the fail-closed attribution did NOT short-circuit: the Source entry carries the
		// publication name expected for « dvf » — by name, not by rank (Phase 50, D-21 #4). A shifted
		// attribution would show another producer here; a refused one, no Source entry at all.
		const sourceServie = volet.locator('.source-panel-entry', { has: page.locator('dt', { hasText: /^Source$/ }) }).locator('dd');
		await expect(sourceServie).toHaveText(COMMUNE_META_SOURCE_NOMS.dvf);
		// The card no longer repeats the source: the panel carries it (03-17 R-1).
		await expect(page.locator('[data-kpi="prix-maisons"] .kpi-source')).toHaveCount(0);
	});

	test('une valeur absente porte la copie EXACTE du glossaire d’états (RW-03)', async ({
		page
	}) => {
		await ouvrirFiche(page);

		// POSITIVE COUNTERPART of the forbidden lists: they ban the technical key of a state from the
		// DOM, this pins the words that replace it for the reader. `toHaveText` compares the whole
		// element — a prefix, a suffix or a rewording goes red here (RW-03).
		// 4.9, lot 3: an absence is on its theme row, the badge and the sentence A2 keeps on it.
		await expect(page.locator('[data-kpi="prix-appartements"]')).toHaveCount(0);
		const appartements = page.locator('[data-field-id="dvf.prix_m2_median_appartement"]');
		await expect(appartements.locator('.tab-v .state-badge')).toHaveText('Effectif insuffisant');
		await expect(appartements.locator('.tab-v .state-help')).toHaveText("Trop peu d'observations (3) pour publier une valeur.");
		await monterVolet(appartements);
		await expect(appartements.locator('dialog .source-panel-entry', { has: page.locator('dt', { hasText: /^État et motif$/ }) }).locator('.state-help'))
			.toHaveText("Trop peu d'observations (3) pour publier une valeur.");

		// No dated block on this instance: the trend says so in its own words. The glossary sentence
		// of not_available (« La source ne publie pas… ») would wrongly blame DVF.
		// Houses and flats absent in the same way: one row of Immobilier, its sentence in the panel (A1, A2).
		await expect(page.locator('[data-kpi="tendance"]')).toHaveCount(0);
		const tendance = page.locator('[data-ligne="dvf-tendance"]');
		await expect(tendance.locator(':scope > dt')).toHaveText('Tendance du prix médian');
		await expect(tendance.locator('.tab-v .state-badge')).toHaveText('Non publiée');
		await expect(tendance.locator('.tab-v .state-help')).toHaveCount(0);
		await monterVolet(tendance);
		await expect(tendance.locator('dialog .source-panel-entry', { has: page.locator('dt', { hasText: /^État et motif$/ }) }).locator('.state-help')).toHaveText("Ce site ne publie pas l'évolution des prix pour cette commune.");
		await expect(tendance.getByText('La source ne publie pas cette valeur pour ce territoire.')).toHaveCount(0);

		// 03-08: the themes are on the glossary too. A missing value of a theme row carries the exact
		// words of its served state; the historical « non disponible » is gone from the themes, and
		// so is the structural « Risques naturels » row, which no API column serves.
		const services = await lireTheme(page, 'services-transports');
		const fibre = services.locator('.tab-l[data-field-id="connectivite.fibre_pct"] .tab-v .state-absence');
		await expect(fibre.locator('.state-badge')).toHaveText('Non renseigné');
		await expect(fibre.locator('.state-help')).toHaveText('La source ne fournit pas cette valeur pour cette commune.');
		// Read on the categories that hold the rows of the five themes (4.9): the locator has to find them.
		const themes = page.locator('details.cat:is(#q-prix, #q-logement, #q-quotidien)');
		await expect(themes).not.toHaveCount(0);
		await expect(themes.getByText('non disponible', { exact: true })).toHaveCount(0);
		await expect(themes.getByText('Risques naturels')).toHaveCount(0);
	});
});

// 04-08 (D-101): the former « Fiche Bien » scan, ported to the two pages that replaced it.
test.describe('Honnêteté publique — pages adresse et document (D-11/D-12)', () => {
	for (const route of ROUTES_PHASE4.filter((r) => r.id === 'adresse' || r.id === 'document')) {
		test(`le DOM de ${route.id}, volets ouverts, ne porte ni jargon ni verdict normatif`, async ({ page }) => {
			const { dom, meta, volets } = await lireSurface(page, route);
			if (route.id === 'adresse') expect(volets, 'aucun volet ouvert — la sonde serait partielle').toBeGreaterThan(0);
			expect(
				findViolations(`${dom}\n${meta}`, FORBIDDEN_PARTOUT, FORBIDDEN_FICHES),
				`Jargon ou verdict normatif sur ${route.chemin}`
			).toEqual([]);
		});
	}
});

// 04-08 Task 3 (DPE-01, D-101, T13): the forbidden texts of the UI-SPEC and the forbidden
// conclusions of 04-DPE-EVALUATION.md on `/` and every phase 4 surface, read on the synthetic
// answers of the specs of 04-17 to 04-19 and 04-04 to 04-11 (fixtures/surfaces-phase4.ts).
// 04.8-07 (UI-SPEC § 6.6): the v4 h1 of the home.
const ACCUEIL: RoutePhase4 = { id: 'accueil', chemin: '/', source: () => [], titre: 'Une adresse, en clair' };
const ADRESSE = ROUTES_PHASE4.find((route) => route.id === 'adresse')!;

/** The address page with another served answer of its documents or of its D-110 finding. */
function adresseAvec(constat: typeof CONSTAT_D110 | null, documents?: Reply): RoutePhase4 {
	return { ...ADRESSE, source: () => sourceAdresse(constat, 1, documents) };
}

test.describe('Textes et conclusions interdits — surfaces de la phase 4 (DPE-01, D-101, T13)', () => {
	for (const route of [ACCUEIL, ...ROUTES_PHASE4]) {
		test(`${route.id} : aucun texte interdit du contrat ni conclusion interdite (DOM, volets, métadonnées)`, async ({ page }) => {
			const { dom, meta } = await lireSurface(page, route);
			const listes = [FORBIDDEN_PARTOUT, TEXTES_INTERDITS_PHASE4, CONCLUSIONS_INTERDITES];
			if (route.id === 'adresse' || route.id === 'document') listes.push(FORBIDDEN_ADRESSE_DPE);
			expect(findViolations(`${dom}\n${meta}`, ...listes), `Texte ou conclusion interdits sur ${route.chemin}`).toEqual([]);
			// T13: these pages name the documents « documents DPE » and « DPE ».
			if (route.id === 'adresse') expect(dom).toMatch(/documents DPE/i);
			// 04.8-04 (UI-SPEC v4 § 6.7): « N° {numéro} » under the h1, « DPE n° … » in the breadcrumb (glued to the
			// crumb before it in innerText): the 13 characters of the ADEME number follow.
			if (route.id === 'document') {
				expect(dom).toMatch(/DPE n° [0-9A-Z]{13}\b/);
				expect(dom).toMatch(/\bN° [0-9A-Z]{13}\b/);
			}
		});
	}

	test('adresse, constat D-110 servi : la phrase est rendue', async ({ page }) => {
		const { dom } = await lireSurface(page, adresseAvec(CONSTAT_D110));
		expect(dom).toMatch(PHRASE_D110);
	});

	test('adresse, constat D-110 nul : aucune phrase, la section des documents rendue', async ({ page }) => {
		const { dom } = await lireSurface(page, adresseAvec(null));
		// Non-vacuity: the same document is listed, only the finding is null.
		expect(dom).toMatch(/DPE n°/);
		expect(dom).not.toMatch(PHRASE_D110);
		expect(dom).not.toMatch(/les éléments concordent/);
	});

	test('documents : un zéro mesuré dit « Aucun DPE » ; une panne ou un hors couverture, jamais', async ({ page }) => {
		// Non-vacuity of the pattern: the measured zero says it (D-109).
		const zero = await lireSurface(page, adresseAvec(null, DOCUMENTS_ZERO));
		expect(zero.dom).toMatch(ZERO_DPE);

		const panne = await lireSurface(page, adresseAvec(null, DOCUMENTS_EN_PANNE));
		expect(panne.dom).toMatch(/Recharger les documents DPE/);
		expect(panne.dom).not.toMatch(ZERO_DPE);

		const horsCouverture = await lireSurface(page, adresseAvec(null, DOCUMENTS_HORS_COUVERTURE));
		expect(horsCouverture.dom).toMatch(new RegExp(LIBELLE_HORS_COUVERTURE));
		expect(horsCouverture.dom).not.toMatch(ZERO_DPE);
	});

	test('export CSV de l’adresse : ni formulation DPE interdite ni conclusion interdite', async ({ page }) => {
		// The CSV has one row per criterion of the report (04.7-03): the real DPE theme of 04.7-01.
		const themeDpe = JSON.parse(readFileSync(new URL('../../src/lib/data/__fixtures__/rapport-theme-dpe-plusieurs-batiments.json', import.meta.url), 'utf8'));
		await lireSurface(page, { ...ADRESSE, source: () => sourceAdresse(null, 1, undefined, [themeDpe]) });
		const telechargement = page.waitForEvent('download');
		await page.locator('.actions-plus summary').click();
		await page.getByRole('button', { name: 'Télécharger (CSV)' }).click();
		await page.getByRole('dialog', { name: 'Exporter les données' }).getByRole('button', { name: /^Télécharger \(\d+\)$/ }).click();
		const csv = await readFile((await (await telechargement).path())!, 'utf8');
		expect(csv, 'export vide — le scan serait vacuous').toContain('DPE de plusieurs bâtiments à cette adresse');
		// The « État » column carries the state key by design of the phase 3 export (D-10 guards the
		// page, not this column): only the DPE wordings and the conclusions are read here.
		expect(findViolations(csv, FORBIDDEN_ADRESSE_DPE, CONCLUSIONS_INTERDITES)).toEqual([]);
	});
});

// ===========================================================================
// T10 — la politique de confidentialité ne déclare que les sous-traitants réels
// ===========================================================================
//
// Ce bloc rendait solidaires la FAQ publique et la politique de confidentialité : la FAQ
// affirmait « aucun tracking tiers » alors qu'un suivi d'erreurs tiers était déclaré
// sous-traitant. La FAQ est partie avec la landing (03-05, D-38) et son test avec elle ;
// la sentinelle de la politique reste : le suivi d'erreurs tiers a été retiré du dépôt
// (plan 01-05), la page ne doit plus le nommer, doit déclarer son retrait et nommer les
// sous-traitants réellement en place.

test.describe('Cohérence vie privée — politique de confidentialité (T10)', () => {
	test('la politique de confidentialité ne déclare aucun suivi d’erreurs tiers', async ({
		page
	}) => {
		await page.goto('/confidentialite');

		const main = page.locator('main');
		await expect(main).toBeVisible({ timeout: 10_000 });
		// Whitespace collapsed: the page wraps its sentences over several source lines.
		const texte = ((await main.textContent()) ?? '').replace(/\s+/g, ' ');

		expect(texte, 'page /confidentialite vide — le scan serait vacuous').toContain(
			'Destinataires des données'
		);

		// Plus aucun suivi d'erreurs tiers dans le code : la section 6 (destinataires)
		// et la section 7 (transferts hors UE) ne doivent plus le nommer.
		expect(texte).not.toMatch(/sentry/i);
		// Le retrait doit être déclaré, pas seulement omis — sinon la page est muette
		// là où elle engageait juridiquement.
		expect(texte).toMatch(/aucun service tiers de supervision des erreurs/i);
		// Public instance without accounts nor payment: the only processor in place is
		// Cloudflare (network and tunnel); billing and mail processors are not named.
		expect(texte).toMatch(/Cloudflare/);
		expect(texte).not.toMatch(/Stripe/);
		expect(texte).not.toMatch(/Resend/);
	});
});
