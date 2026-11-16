import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { ADRESSE_44109_zz04_00004_DPE_OBSERVED, ADRESSE_44109_zz04_00004_OBSERVED } from '../../src/lib/data/__fixtures__/adresse-44109-zz04-00004-20261013';
import { ADRESSE_85191_zz05_00011_DPE_OBSERVED, ADRESSE_85191_zz05_00011_OBSERVED } from '../../src/lib/data/__fixtures__/adresse-85191-zz05-00011-20261012';
import { COMMUNE_85191_OBSERVED, COMMUNE_85191_SOURCES } from '../../src/lib/data/__fixtures__/commune-85191-20261012';
import {
	BAN_ID_44109,
	COMMUNE_NON_SERVIE,
	PREMIERE_PAGE_DECLAREES,
	VENTES_DECLAREES_44109_INCLUSES,
	VENTES_DECLAREES_HORS_PARCELLE_INCLUSES,
	VENTES_HORS_PARCELLE,
	VENTES_PARCELLE_44109,
	avecVentesDeLaParcelle,
	ficheHorsParcelle
} from './fixtures/adresse-ventes';
import {
	ATTENDUS_CARTES_ADRESSE_BRUTE,
	NOMS_VARIANTES,
	releveDocumentaire,
	releveLiensDesVolets,
	variante,
	type CarteDocumentaire,
	type EntreeDocumentaire,
	type LienDocumentaire
} from './fixtures/dpe-adresse-brute';
import { DOCUMENT_REEL, REEL } from './fixtures/dpe-documents';
import { expectNoUnexpectedOffLocalRequest, installLocalNetworkGuard } from './fixtures/local-network';
import { ANCIEN, DOCUMENT_ANCIEN, DOCUMENT_RECENT, RECENT } from './fixtures/recette-adresses';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { cheminRapport, corpsRapport, REQUETE_RAPPORT } from './fixtures/rapport';
import { monterVolet } from './fixtures/volets';

// Rendering record of the reading redesign (phase 04.5, plan 01; D45-01). What the visitor sees for
// each field is frozen at the starting SHA of the phase, before any value moves: on the commune
// fiche 85191, the address fiches 85191_zz05_00011 and 44109_zz04_00004, and the three comparators
// (communes, addresses, DPE documents). Plans 02 to 08, 10, 12 and 13 replay it after each move
// (plan 13 declares its per-m² rows of the document comparator below, `AJOUTS_COMPARATEUR`).
//
// Fiches: every element carrying `data-field-id`, its field, the normalised text of the one value
// node rendered by `StateValue` (value and unit; for an absence, its glossary badge) and the
// rendered state. Comparators: every `td[data-code]` of a `tr[data-cle]` of a `tbody[data-groupe]`.
// `textContent` also reads closed panels. The DISTINCT tuples are compared, never their count: a
// value moved, or shown a second time on purpose, is not a difference; a value lost or altered is.
//
// Writing a section: `DC_LECTURE_RENDU_WRITE=1 DC_LECTURE_BASE_REVISION=<sha>`, with `src/` equal to
// <sha> (unit tests, the inventory reference and lecture.ts aside). An existing section is compared,
// never rewritten. A plan that changes a rendering form on purpose (lists of plan 04) declares its
// comparison rule in this spec, never by rewriting the reference.

const CHEMIN_REFERENCE = fileURLToPath(new URL('./fixtures/lecture-rendu-reference.json', import.meta.url));
const RACINE_FRONTEND = fileURLToPath(new URL('../../', import.meta.url));
const REGLE =
	'Relevé du rendu par field_id figé au SHA de départ de chaque section (D45-01). Une section existante ne se réécrit jamais ; ' +
	'un changement voulu de forme de rendu se déclare dans lecture-rendu.spec.ts.';

type Reply = { status: number; body: unknown };
type Expectation = { method: 'GET'; pathname: string; query: Record<string, string>; minCalls: number; maxCalls: number; responses: Reply[] };

/** One rendered value of a fiche. */
interface RenduFiche {
	fieldId: string;
	texte: string | null;
	etat: string | null;
}

/** One rendered cell of a comparator. */
interface RenduCellule {
	groupe: string;
	cle: string;
	code: string;
	texte: string | null;
	etat: string | null;
}

/** One presentation line of a landmark, without a raw value (plan 04.5-03). */
interface RenduLigne {
	ligne: string;
	libelle: string | null;
	texte: string | null;
	meta: string | null;
	classe: string | null;
}

/** One dated entry of the Source panel of a landmark (plan 04.5-03). */
interface RenduVolet {
	carte: string;
	volet: string;
	terme: string;
	valeur: string | null;
}

type Rendu = RenduFiche | RenduCellule | RenduLigne | RenduVolet | EntreeDocumentaire | RenduVentes | LienDocumentaire;

/** What the six sections of plan 01 record: a value node and its state. */
type RenduValeur = RenduFiche | RenduCellule;

interface SectionReference {
	baseRevision: string;
	capturedAt: string;
	page: string;
	rendu: Rendu[];
}

interface Reference {
	regle: string;
	baseRevision: string;
	capturedAt: string;
	sections: Record<string, SectionReference>;
}

// --- served answers -----------------------------------------------------------------------------

function attente(pathname: string, query: Record<string, string>, body: unknown, maxCalls = 2): Expectation {
	return { method: 'GET', pathname, query, minCalls: 1, maxCalls, responses: Array.from({ length: maxCalls }, () => ({ status: 200, body })) };
}

const COMMUNE_85191 = { statut: 'ok', donnees: [COMMUNE_85191_OBSERVED], meta: { source: COMMUNE_85191_SOURCES } };

interface Comparaison { donnees: { colonnes?: { ban_id?: string; numero?: string }[]; communes?: { code_insee: string }[]; codes_inconnus?: string[] } }

function lireFixture<T>(nom: string): Record<string, T> {
	return JSON.parse(readFileSync(new URL(`../../src/lib/data/__fixtures__/${nom}`, import.meta.url), 'utf8')) as Record<string, T>;
}

// The three hosted communes of the frozen harness answer, in its order (as commune-comparison.spec.ts).
const COMMUNES = structuredClone(lireFixture<Comparaison>('commune-comparaison.json').trois_communes_dont_hors_couverture);
COMMUNES.donnees.codes_inconnus = [];
const CODES_COMMUNES = COMMUNES.donnees.communes!.map((commune) => commune.code_insee);
const ADRESSES = lireFixture<Comparaison>('adresse-comparaison.json').deux_adresses;
const IDS_ADRESSES = ADRESSES.donnees.colonnes!.map((colonne) => colonne.ban_id!);
const DOCUMENTS = lireFixture<Comparaison>('dpe-comparaison.json').ancien_et_actuel;
const NUMEROS = DOCUMENTS.donnees.colonnes!.map((colonne) => colonne.numero!);

interface Cas {
	cle: string;
	url: string;
	attentes: () => Expectation[];
	releve: (page: Page) => Promise<Rendu[]>;
}

function pageAdresse(banId: string, fiche: unknown, documents: unknown, communeServie: unknown = null): () => Expectation[] {
	return () =>
		avecVentesDeLaParcelle([
			attente(cheminRapport(banId), REQUETE_RAPPORT, corpsRapport(fiche)),
			attente(`/v1/adresse/${banId}/dpe`, { page: '1' }, documents),
			...(communeServie ? [attente('/v1/commune', { code_insee: banId.split('_', 1)[0] }, communeServie)] : [])
		]);
}

// --- reading what is rendered -------------------------------------------------------------------

/** Value node of an element: the `StateValue` output, else a comparator cell marker; null if none. */
const VALEUR = '.state-value, .state-absence, .kpi-classes, .compare-hors-couverture, .compare-sans-designation, .compare-non-affichable';

async function releveFiche(page: Page): Promise<RenduFiche[]> {
	return page.locator('[data-field-id]').evaluateAll((elements, selecteur) => {
		const norm = (texte: string | null | undefined) => texte?.replace(/\s+/g, ' ').trim() ?? null;
		return elements.map((element) => {
			const fieldId = element.getAttribute('data-field-id') ?? '';
			const noeud = element.querySelector(selecteur);
			const badge = noeud?.classList.contains('state-absence') ? noeud.querySelector('.state-badge') : null;
			return {
				fieldId,
				texte: norm((badge ?? noeud)?.textContent),
				etat: noeud?.getAttribute('data-state') ?? null
			};
		});
	}, VALEUR);
}

async function releveComparateur(page: Page): Promise<RenduCellule[]> {
	return page.locator('tbody[data-groupe] tr[data-cle] td[data-code]').evaluateAll((cellules, selecteur) => {
		const norm = (texte: string | null | undefined) => texte?.replace(/\s+/g, ' ').trim() ?? null;
		return cellules.map((cellule) => {
			const noeud = cellule.querySelector(selecteur);
			const badge = noeud?.classList.contains('state-absence') ? noeud.querySelector('.state-badge') : null;
			return {
				groupe: cellule.closest('tbody')?.getAttribute('data-groupe') ?? '',
				cle: cellule.closest('tr')?.getAttribute('data-cle') ?? '',
				code: cellule.getAttribute('data-code') ?? '',
				texte: norm((badge ?? noeud)?.textContent),
				etat: cellule.getAttribute('data-state')
			};
		});
	}, VALEUR);
}

/** Distinct tuples in a stable order. */
function distincts(rendu: Rendu[]): Rendu[] {
	const parCle = new Map(rendu.map((item) => [JSON.stringify(item), item]));
	return [...parCle.keys()].sort().map((cle) => parCle.get(cle)!);
}

const CAS: Cas[] = [
	{
		cle: 'commune-85191',
		url: '/commune/85191',
		attentes: () => [attente('/v1/commune', { code_insee: '85191' }, COMMUNE_85191)],
		releve: releveFiche
	},
	{
		cle: 'comparateur-communes',
		url: `/comparer?communes=${CODES_COMMUNES.join(',')}`,
		attentes: () => [attente('/v1/communes/comparaison', { codes: CODES_COMMUNES.join(',') }, COMMUNES)],
		releve: releveComparateur
	},
	{
		cle: 'comparateur-adresses',
		url: `/comparer?adresses=${IDS_ADRESSES.join(',')}`,
		attentes: () => [attente('/v1/adresses/comparaison', { ids: IDS_ADRESSES.join(',') }, ADRESSES)],
		releve: releveComparateur
	},
	{
		cle: 'comparateur-documents',
		url: `/comparer?dpe=${NUMEROS.join(',')}`,
		attentes: () => [attente('/v1/dpe/comparaison', { numeros: NUMEROS.join(',') }, DOCUMENTS)],
		releve: releveComparateur
	}
];

// --- reference ----------------------------------------------------------------------------------

const ECRITURE = process.env.DC_LECTURE_RENDU_WRITE === '1';

function lireReference(): Reference | null {
	return existsSync(CHEMIN_REFERENCE) ? (JSON.parse(readFileSync(CHEMIN_REFERENCE, 'utf8')) as Reference) : null;
}

function ecrireSection(cle: string, url: string, rendu: Rendu[]) {
	const base = process.env.DC_LECTURE_BASE_REVISION ?? '';
	if (!/^[0-9a-f]{40}$/.test(base)) throw new Error('DC_LECTURE_BASE_REVISION must be the full starting SHA');
	// The rendered code must be the code of the declared base revision.
	execFileSync('git', [
		'diff', '--quiet', base, '--', 'src',
		':(exclude,glob)src/**/*.test.ts',
		':(exclude)src/lib/data/lecture.ts',
		':(exclude)src/lib/data/__fixtures__/lecture-inventaire-reference.json'
	], { cwd: RACINE_FRONTEND, stdio: 'inherit' });
	const existante = lireReference();
	const section = existante?.sections[cle];
	if (section) {
		// Never rewritten: an existing section must still be what the page renders.
		expect(rendu).toEqual(section.rendu);
		return;
	}
	const capturedAt = new Date().toISOString();
	// A new section goes first so that the diff of the file only adds lines.
	const reference: Reference = {
		regle: existante?.regle ?? REGLE,
		baseRevision: existante?.baseRevision ?? base,
		capturedAt: existante?.capturedAt ?? capturedAt,
		sections: { [cle]: { baseRevision: base, capturedAt, page: url, rendu }, ...(existante?.sections ?? {}) }
	};
	writeFileSync(CHEMIN_REFERENCE, `${JSON.stringify(reference, null, '\t')}\n`);
}

async function configure(request: APIRequestContext, expectations: Expectation[]) {
	expect((await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, { data: { reset: true, expectations } })).ok()).toBe(true);
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
	await page.goto(url);
	await hydrated;
}

test.beforeEach(async ({ page }) => {
	await installLocalNetworkGuard(page);
});

test.afterEach(async ({ page, request }) => {
	await expectSourceContractMet(request);
	expectNoUnexpectedOffLocalRequest(page);
});

/**
 * Declared change of plan 13 (R5, « m2-premier-niveau »): the document comparator adds the per-m²
 * measures published by the ADEME at first level, named here with the text their served value gives;
 * the yearly totals keep their rows (`consommation`, `emissions`) and their frozen tuples.
 */
const LIGNES_AJOUTEES_COMPARATEUR = ['consommation_m2', 'emissions_m2'];
const ajouteeComparateur = (item: RenduValeur) => 'cle' in item && LIGNES_AJOUTEES_COMPARATEUR.includes(item.cle);
const AJOUTS_COMPARATEUR: Record<string, RenduCellule[]> = {
	'comparateur-documents': [
		{ groupe: 'etiquettes', cle: 'consommation_m2', code: NUMEROS[0], texte: '231 kWhEP/m²/an', etat: 'value' },
		{ groupe: 'etiquettes', cle: 'consommation_m2', code: NUMEROS[1], texte: '231 kWhEP/m²/an', etat: 'value' },
		{ groupe: 'etiquettes', cle: 'emissions_m2', code: NUMEROS[0], texte: '48 kg CO₂ éq/m²/an', etat: 'value' },
		{ groupe: 'etiquettes', cle: 'emissions_m2', code: NUMEROS[1], texte: '48 kg CO₂ éq/m²/an', etat: 'value' }
	]
};

/**
 * Declared change C1 and C5 (inventory of 2026-11-10, arbitrated by Ianis on 2026-11-10): the comparator of
 * communes groups its rows by « Repères » and the three categories of the commune fiche (the two rent rows
 * among the landmarks), no longer by the five former themes. Only the group of a cell changes: the frozen
 * section is not rewritten, its cells are compared without their group (row key, column, text and state stay
 * exact), and the rendered groups must be the four new ones. The grouping itself is locked by
 * `comparateur-categories.test.ts` and `comparateur-revue.spec.ts`.
 */
const GROUPES_C1 = ['q-logement', 'q-prix', 'q-quotidien', 'reperes'];
const GROUPES_AU_DEPART = ['contexte-communal', 'energie-bati', 'immobilier', 'reperes', 'risques-urbanisme', 'services-transports'];
const sansGroupe = (rendu: RenduValeur[]) => distincts(rendu.map((item) => ('groupe' in item ? { ...item, groupe: '' } : item))) as RenduValeur[];
const groupesDe = (rendu: RenduValeur[]) => [...new Set(rendu.flatMap((item) => ('groupe' in item ? [item.groupe] : [])))].sort();

for (const cas of CAS) {
	test(`${cas.cle} : chaque valeur rendue garde son field_id, son texte et son état (référence figée)`, async ({ page, request }) => {
		await configure(request, cas.attentes());
		await open(page, cas.url);
		await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
		const rendu = distincts(await cas.releve(page)) as RenduValeur[];
		expect(rendu.length, 'relevé non vide').toBeGreaterThan(0);
		expect(rendu.every((item) => item.texte !== null && item.etat !== null), JSON.stringify(rendu.filter((item) => item.texte === null || item.etat === null))).toBe(true);

		const ajouts = AJOUTS_COMPARATEUR[cas.cle] ?? [];
		const figes = rendu.filter((item) => !ajouteeComparateur(item));
		if (ECRITURE) ecrireSection(cas.cle, cas.url, figes);
		const section = lireReference()?.sections[cas.cle];
		expect(section, `section ${cas.cle} de lecture-rendu-reference.json`).toBeDefined();
		expect((section!.rendu as RenduValeur[]).some(ajouteeComparateur), 'aucune ligne par m² au SHA de départ').toBe(false);
		const regroupe = cas.cle === 'comparateur-communes';
		const reference = section!.rendu as RenduValeur[];
		if (regroupe) {
			expect(groupesDe(reference), 'groupes de la référence figée').toEqual(GROUPES_AU_DEPART);
			expect(groupesDe(figes), 'groupes rendus (C1)').toEqual(GROUPES_C1);
			expect(sansGroupe(figes)).toEqual(sansGroupe(reference));
		} else {
			expect(figes).toEqual(reference);
		}
		expect(rendu.filter(ajouteeComparateur)).toEqual(ajouts);

		// Control: a copy of the reference where one value is altered must fail the comparison.
		const alteree = structuredClone(reference);
		alteree[0] = { ...alteree[0], texte: `${alteree[0].texte} (altérée)` };
		expect(() => expect(regroupe ? sansGroupe(figes) : figes).toEqual(regroupe ? sansGroupe(alteree) : alteree)).toThrow();
	});
}

// --- essentials: presentation lines and panel dates (plan 04.5-03) -----------------------------
//
// Plan 04.5-03 compacts the essentials and spells their dates (counter-review I3, D45-01). Two
// things the record above does not see are frozen at the starting SHA of plan 03, in sections of
// their own (the sections above are never rewritten):
// - the presentation lines of the address landmark ① (« Plus récent » / « Plus récents »): the
//   served selection has no raw value, hence no `data-field-id`. Label, value, short line, class tag.
// - the dated entries of every landmark panel of the strip (« Période », « Dernière importation
//   réussie »), read in the closed dialogs: the face may spell a date, the panel keeps the served form.

const LIGNES_DE_PRESENTATION = 'ol.kpi-strip :is([data-kpi-line="dpe-plus-recent"], [data-kpi-line="dpe-plus-recents"])';
const TERMES_DATES = ['Période', 'Dernière importation réussie'];

async function releveLignesDePresentation(page: Page): Promise<RenduLigne[]> {
	return page.locator(LIGNES_DE_PRESENTATION).evaluateAll((lignes) => {
		const norm = (texte: string | null | undefined) => texte?.replace(/\s+/g, ' ').trim() ?? null;
		return lignes.map((ligne) => ({
			ligne: ligne.getAttribute('data-kpi-line') ?? '',
			libelle: norm(ligne.querySelector('.kpi-sub')?.textContent),
			texte: norm(ligne.querySelector('.state-value, .state-absence')?.textContent),
			meta: norm(ligne.querySelector('.kpi-meta')?.textContent),
			classe: norm(ligne.querySelector('.dpe-tag')?.textContent)
		}));
	});
}

async function releveVoletsDesReperes(page: Page): Promise<RenduVolet[]> {
	// The panels of the commune fiche render at their first opening (Search Console 2026-11-16).
	for (const repere of await page.locator('dl.kpi-strip [data-kpi]').all()) await monterVolet(repere);
	return page.locator('dl.kpi-strip dialog').evaluateAll((dialogues, termes) => {
		const norm = (texte: string | null | undefined) => texte?.replace(/\s+/g, ' ').trim() ?? null;
		return dialogues.flatMap((dialogue) =>
			[...dialogue.querySelectorAll('.source-panel-entry')].flatMap((entree) => {
				const terme = norm(entree.querySelector('dt')?.textContent);
				if (!terme || !termes.includes(terme)) return [];
				return [{
					carte: dialogue.closest('[data-kpi]')?.getAttribute('data-kpi') ?? '',
					volet: norm(dialogue.querySelector('.source-panel-title')?.textContent) ?? '',
					terme,
					valeur: norm(entree.querySelector('dd')?.textContent)
				}];
			})
		);
	}, TERMES_DATES);
}

// 85191_zz05_00011 counts 0 DPE: its landmark ① has no « Plus récent » line, only 44109_zz04_00004
// (five documents, one most recent served) is recorded for the presentation lines.
const CAS_ESSENTIEL: Cas[] = [
	{ cle: 'essentiel-volets-commune-85191', url: CAS[0].url, attentes: CAS[0].attentes, releve: releveVoletsDesReperes }
];

for (const cas of CAS_ESSENTIEL) {
	test(`${cas.cle} : lignes de présentation et dates des volets de l'essentiel (référence figée du plan 03)`, async ({ page, request }) => {
		await configure(request, cas.attentes());
		await open(page, cas.url);
		await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
		const rendu = distincts(await cas.releve(page));
		expect(rendu.length, 'relevé non vide').toBeGreaterThan(0);

		if (ECRITURE) ecrireSection(cas.cle, cas.url, rendu);
		const section = lireReference()?.sections[cas.cle];
		expect(section, `section ${cas.cle} de lecture-rendu-reference.json`).toBeDefined();
		expect(rendu).toEqual(section!.rendu);

		// Control: a reference where the first entry is lost must fail the comparison.
		expect(() => expect(rendu).toEqual(section!.rendu.slice(1))).toThrow();
	});
}

// --- declared DPE documents: the list as rendered, per DPE number (plan 04.5-05) ----------------
//
// Plan 04.5-05 puts the comparison instruction before the cards (B3) and adds the group of the
// documents attached by their exact raw address (C2). The record above reads `data-field-id`
// elements only; the cards carry none. The list section of `/adresse/44109_zz04_00004?theme=documents`
// is read by `releveDocumentaire` (header texts, then per DPE number its link and six attributes) on
// the synthetic variants of `fixtures/dpe-adresse-brute.ts`, frozen at the starting SHA of plan 05:
// at that SHA the page ignores the group, so a section is the block of the ADEME key alone. The
// ADEME block must stay exactly its section; the group is an addition, checked in adresse-fiche.spec.ts.

const ADEME = (entree: EntreeDocumentaire) => entree.bloc === 'ademe';
// Owner review of 09/11: the « En clair » line that now opens the panel is a named addition, never part of
// a frozen section (the sections are not rewritten); it is checked on its own in the test below.
const EN_CLAIR = (entree: EntreeDocumentaire) => entree.element === 'volet' && (entree as { texte?: string }).texte?.startsWith('En clair') === true;
const ADEME_FIGE = (entree: EntreeDocumentaire) => ADEME(entree) && !EN_CLAIR(entree);

const CAS_DOCUMENTS: Cas[] = NOMS_VARIANTES.map((nom) => ({
	cle: `documents-44109-zz04-00004-${nom}`,
	url: '/adresse/44109_zz04_00004?theme=documents',
	attentes: pageAdresse('44109_zz04_00004', ADRESSE_44109_zz04_00004_OBSERVED, variante(nom)),
	releve: (page: Page) => page.locator('section.doclist').evaluate(releveDocumentaire)
}));

for (const cas of CAS_DOCUMENTS) {
	test(`${cas.cle} : bloc de la clé ADEME de la liste rendue, par numéro DPE (référence figée du plan 05)`, async ({ page, request }) => {
		await configure(request, cas.attentes());
		await open(page, cas.url);
		await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
		const rendu = (await cas.releve(page)) as EntreeDocumentaire[];
		expect(rendu.length, 'relevé non vide').toBeGreaterThan(0);

		// The section is the ADEME block (the whole list at the starting SHA of plan 05); the group is
		// checked apart, so a later write compares the ADEME block only.
		if (ECRITURE) ecrireSection(cas.cle, cas.url, rendu.filter(ADEME_FIGE));
		const section = lireReference()?.sections[cas.cle];
		expect(section, `section ${cas.cle} de lecture-rendu-reference.json`).toBeDefined();
		const reference = section!.rendu as EntreeDocumentaire[];
		expect(reference.every(ADEME)).toBe(true);
		expect(rendu.filter(ADEME_FIGE)).toEqual(reference);
		// The only addition to the ADEME block: one « En clair » line, the first line of its panel.
		const volet = rendu.filter((entree) => ADEME(entree) && entree.element === 'volet');
		expect(volet.filter(EN_CLAIR)).toHaveLength(1);
		expect(EN_CLAIR(volet[0])).toBe(true);

		// Control: a reference where one card lost its climate class must fail the comparison.
		const index = reference.findIndex((entree) => entree.element === 'carte');
		const alteree = index < 0 ? reference.slice(1) : reference.map((entree, i) => (i === index ? { ...(entree as CarteDocumentaire), climat: null } : entree));
		expect(() => expect(rendu.filter(ADEME_FIGE)).toEqual(alteree)).toThrow();
	});
}

// Review 05 (IMPORTANT 2): the record above reads the panel entries as text, without their targets. The
// links of every Source panel of the list (block, entry term, text, href) are frozen at the starting
// SHA of plan 06 in complementary sections `documents-44109-zz04-00004-<variant>-liens`.
for (const nom of NOMS_VARIANTES) {
	const cle = `documents-44109-zz04-00004-${nom}-liens`;
	const url = '/adresse/44109_zz04_00004?theme=documents';
	test(`${cle} : liens des volets Source de la liste rendue (référence figée du plan 06)`, async ({ page, request }) => {
		await configure(request, pageAdresse('44109_zz04_00004', ADRESSE_44109_zz04_00004_OBSERVED, variante(nom))());
		await open(page, url);
		await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
		const liens = await page.locator('section.doclist').evaluate(releveLiensDesVolets);
		expect(liens.length, 'relevé non vide').toBeGreaterThan(0);

		if (ECRITURE) ecrireSection(cle, url, liens);
		const section = lireReference()?.sections[cle];
		expect(section, `section ${cle} de lecture-rendu-reference.json`).toBeDefined();
		const reference = section!.rendu as LienDocumentaire[];
		expect(liens).toEqual(reference);
		const servie = (variante(nom).donnees.source as { url: string }).url;
		expect(reference.filter((lien) => lien.terme === 'Source').every((lien) => lien.href === servie)).toBe(true);

		// Control: a source link pointing elsewhere must fail the comparison.
		expect(() => expect(liens.map((lien, i) => (i === 0 ? { ...lien, href: 'https://example.invalid/' } : lien))).toEqual(reference)).toThrow();
	});
}

// Review 05 (IMPORTANT 1): the cards of the raw-address group were compared by rank, number, link and
// badge only. Each rendered card of the group must now carry the six attributes of its served card,
// written from the served cards in fixtures/dpe-adresse-brute.ts, never read from the rendering.
const SIX_ATTRIBUTS = ['energie', 'climat', 'typeSurface', 'dates', 'badges', 'adresse'] as const;

for (const nom of NOMS_VARIANTES) {
	test(`documents-44109-zz04-00004-${nom} : chaque carte du groupe par adresse saisie porte les six attributs servis`, async ({ page, request }) => {
		await configure(request, pageAdresse('44109_zz04_00004', ADRESSE_44109_zz04_00004_OBSERVED, variante(nom))());
		await open(page, '/adresse/44109_zz04_00004?theme=documents');
		await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
		const releve = await page.locator('section.doclist').evaluate(releveDocumentaire);
		const cartes = releve.filter((entree): entree is CarteDocumentaire => entree.element === 'carte' && entree.bloc === 'adresse_brute');
		const servies = (variante(nom).donnees.documents_adresse_brute ?? []) as { numero: string }[];
		const attendus = servies.map(({ numero }) => ({ numero, ...ATTENDUS_CARTES_ADRESSE_BRUTE[numero] }));
		const six = (carte: CarteDocumentaire) => ({ numero: carte.numero, ...Object.fromEntries(SIX_ATTRIBUTS.map((attribut) => [attribut, carte[attribut]])) });
		expect(cartes.map(six)).toEqual(attendus);
		// Control: one raw card whose climate class is lost must fail the comparison.
		if (cartes.length) expect(() => expect([{ ...six(cartes[0]), climat: null }, ...cartes.slice(1).map(six)]).toEqual(attendus)).toThrow();
	});
}

// --- sales declared at the address: the list and the essential (plan 04.5-06) -------------------
//
// Plan 04.5-06 gives the essential an access to the sales declared at the address, with the served
// total of that list when it is a value (UI-006). The record of plan 01 reads `data-field-id`
// elements only: the total of the declared list has none (it is the range of its pagination), and its
// replays serve that list empty. The theme of the sales of `/adresse/44109_zz04_00004?theme=ventes` is
// read on the two replays of fixtures/adresse-ventes.ts whose declared total is positive, frozen at
// the starting SHA of plan 06 (counter-review I): the range and the common line of the declared list,
// the entries of its Source panel with their links, and every value of the sales landmark of the
// essential (zone `essentiel`). The access that plan 06 adds to the landmark (`[data-acces]`, zone
// `acces`) does not exist at that SHA: the other zones must stay their section, and the access is
// checked against the section where it is added, never by rewriting it.

/** One rendered element of the sales: the declared list, the landmark or its access. */
interface RenduVentes {
	zone: 'liste' | 'essentiel' | 'acces';
	element: string;
	fieldId: string | null;
	texte: string | null;
	etat: string | null;
	lien: string | null;
}

async function releveVentesDeclarees(page: Page): Promise<RenduVentes[]> {
	return page.locator('body').evaluate((body, selecteur) => {
		const norm = (texte: string | null | undefined) => (texte == null ? null : texte.replace(/\s+/g, ' ').trim());
		const entrees: RenduVentes[] = [];
		const ajouter = (zone: RenduVentes['zone'], element: string, partiel: Partial<RenduVentes>) =>
			entrees.push({ zone, element, fieldId: null, texte: null, etat: null, lien: null, ...partiel });
		const liste = body.querySelector('section#ventes-adresse');
		if (liste) {
			for (const [element, cible] of [['plage', '.ventes-adresse-range'], ['commune', '.ventes-commune > span']] as const) {
				for (const noeud of Array.from(liste.querySelectorAll(cible))) ajouter('liste', element, { texte: norm(noeud.textContent) });
			}
			for (const entree of Array.from(liste.querySelectorAll('dialog .source-panel-entry'))) {
				const texte = `${norm(entree.querySelector('dt')?.textContent) ?? ''} : ${norm(entree.querySelector('dd')?.textContent) ?? ''}`;
				ajouter('liste', 'volet', { texte, lien: entree.querySelector('a[href]')?.getAttribute('href') ?? null });
			}
		}
		const carte = body.querySelector('ol.kpi-strip [data-kpi="ventes"]');
		if (carte) {
			const zone = (noeud: Element): RenduVentes['zone'] => (noeud.closest('[data-acces]') ? 'acces' : 'essentiel');
			for (const element of Array.from(carte.querySelectorAll('[data-field-id]'))) {
				const noeud = element.querySelector(selecteur);
				const badge = noeud?.classList.contains('state-absence') ? noeud.querySelector('.state-badge') : null;
				ajouter(zone(element), 'valeur', {
					fieldId: element.getAttribute('data-field-id'),
					texte: norm((badge ?? noeud)?.textContent),
					etat: noeud?.getAttribute('data-state') ?? null
				});
			}
			// The link of the access itself; the links of its Source panel are read with its entries.
			for (const lien of Array.from(carte.querySelectorAll('[data-acces] a[href]')).filter((noeud) => !noeud.closest('dialog'))) {
				ajouter('acces', 'lien', { texte: norm(lien.getAttribute('aria-label') ?? lien.textContent), lien: lien.getAttribute('href') });
			}
			for (const entree of Array.from(carte.querySelectorAll('[data-acces] dialog .source-panel-entry'))) {
				const texte = `${norm(entree.querySelector('dt')?.textContent) ?? ''} : ${norm(entree.querySelector('dd')?.textContent) ?? ''}`;
				ajouter('acces', 'volet', { texte, lien: entree.querySelector('a[href]')?.getAttribute('href') ?? null });
			}
		}
		return entrees;
	}, VALEUR);
}

function pageVentes(fiche: unknown, parcelle: Reply, declarees: Reply): () => Expectation[] {
	return () => [
		attente(cheminRapport(BAN_ID_44109), REQUETE_RAPPORT, corpsRapport(fiche)),
		attente(`/v1/adresse/${BAN_ID_44109}/dpe`, { page: '1' }, ADRESSE_44109_zz04_00004_DPE_OBSERVED),
		{ ...attente(`/v1/adresse/${BAN_ID_44109}/ventes`, { page: '1' }, null), responses: [parcelle, parcelle] },
		{ ...attente(`/v1/adresse/${BAN_ID_44109}/ventes-declarees`, { ...PREMIERE_PAGE_DECLAREES }, null), responses: [declarees, declarees] },
		{ ...attente('/v1/commune', { code_insee: '44109' }, null), minCalls: 0, responses: [COMMUNE_NON_SERVIE, COMMUNE_NON_SERVIE] }
	];
}

// Plan 07 (C4): the page asks for the declared sales with their VEFA; the replays of plan 06 are served
// as `vefa=incluses` answers without any VEFA (`nombre_vefa` 0), and must still give the frozen sections.
const CAS_VENTES: Cas[] = [
	{
		cle: 'ventes-declarees-44109-zz04-00004',
		url: '/adresse/44109_zz04_00004?theme=ventes',
		attentes: pageVentes(ADRESSE_44109_zz04_00004_OBSERVED, VENTES_PARCELLE_44109, VENTES_DECLAREES_44109_INCLUSES),
		releve: releveVentesDeclarees
	},
	{
		cle: 'ventes-declarees-44109-zz04-00004-hors-parcelle',
		url: '/adresse/44109_zz04_00004?theme=ventes',
		attentes: pageVentes(ficheHorsParcelle(ADRESSE_44109_zz04_00004_OBSERVED), VENTES_HORS_PARCELLE, VENTES_DECLAREES_HORS_PARCELLE_INCLUSES),
		releve: releveVentesDeclarees
	}
];

/**
 * Declared change of plan 07 (C4, report 4.1-15, R10): the definition of `adresse.dvf.ventes_adresse.nombre`
 * gains one sentence, appended to its frozen text and nothing else. The frozen sections are compared
 * with that sentence appended to their « Définition » entry, never rewritten.
 */
const AJOUT_DEFINITION_VEFA = "Les ventes en l'état futur d'achèvement (VEFA) déclarées à l'adresse sont listées et marquées, mais ne sont pas comptées.";
/**
 * Declared change of plan 08 (C3, report 4.1-14, R10): the sentence of the report replaces, at its
 * place, the sentence it withdraws; nothing else of the frozen definition changes.
 */
const PHRASE_RETIREE_SUFFIXE = 'Les données DVF importées ne gardent pas le suffixe (bis, ter) : une vente déclarée au « 2 bis » peut apparaître sur la page du « 2 ».';
const PHRASE_AJOUTEE_SUFFIXE =
	'La vente est rattachée par le numéro et le suffixe déclarés : une vente déclarée au « 2 » sans suffixe ne figure que sur la page du « 2 », une vente déclarée au « 2 B » sur celle du « 2 bis ». ' +
	"Quand un suffixe ne correspond à aucune adresse, ou à plusieurs, la vente n'est montrée sur aucune.";
const avecAjoutVefa = (entrees: RenduVentes[]): RenduVentes[] =>
	entrees.map((entree) => {
		if (entree.element !== 'volet' || !entree.texte?.startsWith('Définition : ')) return entree;
		expect(entree.texte.split(PHRASE_RETIREE_SUFFIXE)).toHaveLength(2);
		return { ...entree, texte: `${entree.texte.replace(PHRASE_RETIREE_SUFFIXE, PHRASE_AJOUTEE_SUFFIXE)} ${AJOUT_DEFINITION_VEFA}` };
	});

// The frozen sections of the declared sales read the former page (its fold and its landmark ②);
// since 04.7-03 the list is checked by adresse-ventes.spec.ts (cards, range, VEFA, definitions).

// --- the DPE document page (plan 04.5-12, C1; counter-review I2) -------------------------------
//
// The record of plan 01 visits no `/dpe/[numero]` page. Plan 12 reads landmarks ① and ② in the per-m²
// values served next to the yearly totals and moves those totals to « Ce que déclare le document ».
// Every `data-field-id` of the page (landmark lines and characteristic rows), its value node and its
// state are frozen at the starting SHA of plan 12, before the page changes, in sections of their own
// (`document-dpe-<case>`), on the two documents of the synthetic place and the real document of the
// UI review (observed values, fixtures/dpe-documents.ts). The sections of plan 01 are not rewritten.

const CAS_DOCUMENT_DPE: Cas[] = [
	{ cle: 'document-dpe-recent', numero: RECENT, reponse: DOCUMENT_RECENT },
	{ cle: 'document-dpe-ancien', numero: ANCIEN, reponse: DOCUMENT_ANCIEN },
	{ cle: 'document-dpe-2644E8000030X', numero: REEL, reponse: DOCUMENT_REEL }
].map(({ cle, numero, reponse }) => ({
	cle,
	url: `/dpe/${numero}`,
	attentes: () => [attente(`/v1/dpe/${numero}`, {}, reponse)],
	releve: releveFiche
}));

/**
 * Declared change of plan 12 (C1, report 4.1-12): the landmarks read the per-m² values published by
 * the ADEME. Each is an addition named here, with the text its served value gives; every frozen tuple
 * must stay (the yearly totals are rendered in « Ce que déclare le document » with the same text).
 */
const AJOUTS_DOCUMENT_DPE: Record<string, RenduFiche[]> = {
	'document-dpe-recent': [
		{ fieldId: 'dpe.document.conso_ep_m2', texte: '101 kWhEP/m²/an', etat: 'value' },
		{ fieldId: 'dpe.document.emissions_ges_m2', texte: '9 kg CO₂ éq/m²/an', etat: 'value' }
	],
	'document-dpe-ancien': [
		{ fieldId: 'dpe.document.conso_ep_m2', texte: '231 kWhEP/m²/an', etat: 'value' },
		{ fieldId: 'dpe.document.emissions_ges_m2', texte: '41 kg CO₂ éq/m²/an', etat: 'value' }
	],
	'document-dpe-2644E8000030X': [
		{ fieldId: 'dpe.document.conso_ep_m2', texte: '318 kWhEP/m²/an', etat: 'value' },
		{ fieldId: 'dpe.document.emissions_ges_m2', texte: '25 kg CO₂ éq/m²/an', etat: 'value' }
	]
};
const CHAMPS_AJOUTES = ['dpe.document.conso_ep_m2', 'dpe.document.emissions_ges_m2'];
const ajoute = (item: RenduFiche) => CHAMPS_AJOUTES.includes(item.fieldId);

for (const cas of CAS_DOCUMENT_DPE) {
	test(`${cas.cle} : chaque valeur rendue de la page document garde son field_id, son texte et son état (référence figée du plan 12)`, async ({ page, request }) => {
		await configure(request, cas.attentes());
		await open(page, cas.url);
		await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
		const rendu = distincts(await cas.releve(page)) as RenduFiche[];
		expect(rendu.length, 'relevé non vide').toBeGreaterThan(0);
		expect(rendu.every((item) => item.texte !== null && item.etat !== null), JSON.stringify(rendu)).toBe(true);

		if (ECRITURE) ecrireSection(cas.cle, cas.url, rendu.filter((item) => !ajoute(item)));
		const section = lireReference()?.sections[cas.cle];
		expect(section, `section ${cas.cle} de lecture-rendu-reference.json`).toBeDefined();
		const reference = section!.rendu as RenduFiche[];
		expect(reference.some(ajoute), 'aucune valeur par m² au SHA de départ').toBe(false);
		expect(rendu.filter((item) => !ajoute(item))).toEqual(reference);
		expect(rendu.filter(ajoute)).toEqual(AJOUTS_DOCUMENT_DPE[cas.cle]);

		// Control: a reference where one value is altered must fail the comparison.
		expect(() => expect(rendu.filter((item) => !ajoute(item))).toEqual(reference.map((item, i) => (i === 0 ? { ...item, texte: `${item.texte}0` } : item)))).toThrow();
	});
}
