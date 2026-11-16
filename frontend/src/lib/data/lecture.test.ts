// Zero-rule guard of the reading redesign (phase 04.5, D45-01): every value the redesign moves keeps
// its field_id, raw served value, unit, state, source and dates. The inventory of the public models
// was frozen at the starting SHA of plan 04.5-01 in `__fixtures__/lecture-inventaire-reference.json`;
// the reading projection (`lecture.ts`) must produce exactly the same inventory, and so must the
// public models themselves. Each later plan of the phase adds its own sections, never rewrites one.
//
// Writing a section: `DC_LECTURE_REFERENCE_WRITE=1 DC_LECTURE_BASE_REVISION=<sha>` with a working
// tree whose `src/` (tests, the reference and lecture.ts aside) equals <sha>. A section that already
// exists is compared, never rewritten.
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { render } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

// The list of the declared documents (plan 04.5-05) navigates with `goto`; nothing navigates here.
vi.mock('$app/navigation', () => ({ goto: vi.fn(async () => undefined), invalidate: vi.fn(async () => undefined) }));

import { getDocumentsDpe, getVentesAdresse, projeterComparaisonAdresses } from '$lib/api/adresses';
import type { ComparaisonCommunes, CommuneSynthese, SourceAttribution } from '$lib/api/commune';
import DpeDocumentList from '$lib/components/adresse/DpeDocumentList.svelte';
import SourcePanel from '$lib/components/fiche/SourcePanel.svelte';
import { BADGE_ADRESSE_BRUTE } from './adresse-fiche';
import { voletVentesAdresse } from './adresse-ventes';
import { COMMUNE_THEMES, themesCommune } from './commune-grid';
import { communeKpis, TENDANCE_NON_PUBLIEE } from './commune-kpi';
import { LIBELLES_ETATS } from './etats-libelles';
import { definitionPublique } from './explications';
import { LIBELLE_EN_CLAIR, enClairChamp } from './explications-simples';
import { COMMUNE_85191_OBSERVED, COMMUNE_85191_SOURCES } from './__fixtures__/commune-85191-20261012';
import {
	ATTENDUS_CARTES_ADRESSE_BRUTE,
	BAN_ID_ADRESSE_BRUTE,
	NOMS_VARIANTES,
	releveDocumentaire,
	releveLiensDesVolets,
	sansGroupe,
	variante,
	type CarteDocumentaire,
	type EntreeDocumentaire,
	type LienDocumentaire,
	type ReponseDocuments
} from '../../../tests/e2e/fixtures/dpe-adresse-brute';
import {
	BAN_ID_44109,
	PERIODE_44109,
	SOURCE_DVF,
	VENTES_DECLAREES_44109_AVEC_VEFA,
	VENTES_DECLAREES_44109_INCLUSES,
	VENTES_DECLAREES_HORS_PARCELLE_INCLUSES,
	VENTES_DECLAREES_VEFA_HORS_PAGE,
	VENTES_DECLAREES_VEFA_SEULES
} from '../../../tests/e2e/fixtures/adresse-ventes';
import {
	TERMES_DATES,
	inventaireComparaison,
	inventaireDesModeles,
	inventaireLecture,
	lectureCommune,
	lectureDocument,
	type EntreeComparaison,
	type EntreeInventaire,
	type LigneLecture
} from './lecture';
import { projeterComparaisonDocuments, projeterDocument } from '$lib/api/documents-dpe';
import { DOCUMENT_REEL, DOCUMENT_RETIRE } from '../../../tests/e2e/fixtures/dpe-documents';
import {
	composeComparaison,
	composeComparaisonAdresses,
	composeComparaisonDocuments,
	type ComparaisonModel
} from './commune-comparison';
import comparaisonsCommunes from './__fixtures__/commune-comparaison.json';
import comparaisonsAdresses from './__fixtures__/adresse-comparaison.json';
import comparaisonsDocuments from './__fixtures__/dpe-comparaison.json';

// Vitest runs from `frontend/` (same convention as bien-fiche.test.ts).
const RACINE_FRONTEND = process.cwd();
const CHEMIN_REFERENCE = join(RACINE_FRONTEND, 'src/lib/data/__fixtures__/lecture-inventaire-reference.json');

const REGLE =
	'Inventaire figé au SHA de départ de chaque section (D45-01). Une section existante ne se réécrit jamais : ' +
	'un plan qui déplace des valeurs ajoute ses sections avant toute modification du code, et une dérive fait échouer lecture.test.ts.';

interface SectionReference {
	baseRevision: string;
	capturedAt: string;
	entrees: EntreeInventaire[];
}

interface Reference {
	regle: string;
	baseRevision: string;
	capturedAt: string;
	sections: Record<string, SectionReference>;
}

const commune = COMMUNE_85191_OBSERVED as unknown as CommuneSynthese;
const sources = COMMUNE_85191_SOURCES as unknown as SourceAttribution[];

/** The « before » path of each section: the public models read directly, never through lecture.ts. */
const AVANT: Record<string, () => EntreeInventaire[]> = {
	'commune-85191': () => inventaireDesModeles({ kpis: communeKpis(commune, sources) }),
	'commune-85191-themes': () => inventaireDesModeles({ themes: themesCommune(commune, sources) })
};

/** The « after » path: the same answers through the reading projection. */
const APRES: Record<string, () => EntreeInventaire[]> = {
	'commune-85191': () => inventaireLecture(lectureCommune(commune, sources).filter((groupe) => groupe.cle === 'essentiel')),
	'commune-85191-themes': () => inventaireLecture(lectureCommune(commune, sources).filter((groupe) => groupe.cle !== 'essentiel'))
};

// --- declared DPE documents (plan 04.5-05, B3 + C2; counter-review B and I) ----------------------
//
// The lists of documents are not projected by lecture.ts: their guard is the documentary record of
// the list as `DpeDocumentList` renders it (`releveDocumentaire`: header texts and, per DPE number,
// its link and six attributes), on the synthetic variants of `tests/e2e/fixtures/dpe-adresse-brute.ts`.
// Its sections (`documents-<page>-<variant>`) are written at the starting SHA of plan 05, before the
// list changes: at that SHA the page ignores the raw-address group, so each section is the block of
// the ADEME key alone. Afterwards the ADEME block of every variant must still be exactly its section;
// the raw-address group is an explicit addition, checked on its own.

const PAGE_DOCUMENTS = 'documents-44109-zz04-00004';

/** The served answer, projected by the client of the page and rendered by the list component. */
async function releveRendu(reponse: ReponseDocuments): Promise<EntreeDocumentaire[]> {
	return lireLaListe(reponse, releveDocumentaire);
}

async function lireLaListe<T>(reponse: ReponseDocuments, lecture: (section: Element) => T): Promise<T> {
	const fetchImpl = (async () =>
		new Response(JSON.stringify(reponse), { status: 200, headers: { 'Content-Type': 'application/json' } })) as typeof fetch;
	const issue = await getDocumentsDpe(fetchImpl, BAN_ID_ADRESSE_BRUTE, '1', null);
	const { container, unmount } = render(DpeDocumentList, { props: { issue, banId: BAN_ID_ADRESSE_BRUTE } });
	const section = container.querySelector('section.doclist');
	if (!section) throw new Error('no rendered section.doclist');
	const releve = lecture(section);
	unmount();
	return releve;
}

/**
 * Writer of the `documents-*` sections at the starting SHA of plan 05: the list as it renders, its
 * ADEME block (the whole list at that SHA; since plan 05 the raw-address group is checked apart, so a
 * later write compares the existing sections with the ADEME block only).
 */
const DOCUMENTS_AU_DEPART: Record<string, () => Promise<EntreeDocumentaire[]>> = Object.fromEntries(
	NOMS_VARIANTES.map((nom) => [
		`${PAGE_DOCUMENTS}-${nom}`,
		async () => (await releveRendu(variante(nom))).filter((entree) => entree.bloc === 'ademe')
	])
);

// --- sales declared at the address (plan 04.5-06, B4; counter-review I) -------------------------
//
// Plan 06 gives the essential an access to the sales declared at the address, with the served total
// of that list when it is a value (UI-006, R7). That total is a value moved (D45-01): the inventory
// entry of the declared list as the page reads it — its field, its served total, the unit and dates
// of its Source panel, its served state and source — is frozen at the starting SHA of plan 06,
// before the essential changes, on the two replays of `tests/e2e/fixtures/adresse-ventes.ts`
// (`ventes-declarees-<page>`). Afterwards the line of the essential must give exactly this entry.

const FIELD_VENTES_ADRESSE = 'adresse.dvf.ventes_adresse.nombre';

/** A replay of plan 06: the served answer of its declared list. */
interface PageVentesDeclarees {
	declarees: unknown;
	/** The served total, written here independently of any projection. */
	total: number;
}

// Since plan 07 (C4) the page asks for the VEFA: each replay is served as a `vefa=incluses` answer without
// any VEFA (`nombre_vefa` 0), which gives the same entry of the list as the frozen answer.
const VENTES_DECLAREES: Record<string, PageVentesDeclarees> = {
	'ventes-declarees-44109-zz04-00004': {
		declarees: VENTES_DECLAREES_44109_INCLUSES.body,
		total: 3
	},
	'ventes-declarees-44109-zz04-00004-hors-parcelle': {
		declarees: VENTES_DECLAREES_HORS_PARCELLE_INCLUSES.body,
		total: 1
	}
};

/** The served answer of the declared list, projected by the client of the page. */
async function issueVentesDeclarees(corps: unknown) {
	const fetchImpl = (async () =>
		new Response(JSON.stringify(corps), { status: 200, headers: { 'Content-Type': 'application/json' } })) as typeof fetch;
	return getVentesAdresse(fetchImpl, BAN_ID_44109, '1', null);
}

/** The entry of the declared list as the page reads it: its served total, its panel and its source. */
async function entreeListeDeclaree(corps: unknown): Promise<EntreeInventaire[]> {
	const issue = await issueVentesDeclarees(corps);
	if (issue.kind !== 'ok' || issue.ventes.etat !== 'value' || typeof issue.ventes.total !== 'number') throw new Error('no served declared list');
	const liste = issue.ventes;
	const volet = voletVentesAdresse(liste);
	const unite = volet.find((entree) => entree.terme === 'Unité');
	return [{
		fieldId: FIELD_VENTES_ADRESSE,
		valeur: JSON.stringify(liste.total),
		unite: unite && 'valeur' in unite ? unite.valeur : null,
		etat: { kind: 'valeur' },
		source: liste.source ? { nom: liste.source.nom, url: liste.source.url ?? null } : null,
		dates: volet.flatMap((entree) => ('valeur' in entree && TERMES_DATES.includes(entree.terme) ? [{ terme: entree.terme, valeur: entree.valeur }] : []))
	}];
}

/** Writer of the `ventes-declarees-*` sections at the starting SHA of plan 06. */
const VENTES_DECLAREES_AU_DEPART: Record<string, () => Promise<EntreeInventaire[]>> = Object.fromEntries(
	Object.entries(VENTES_DECLAREES).map(([cle, { declarees }]) => [cle, () => entreeListeDeclaree(declarees)])
);

// --- VEFA in the declared sales (plan 04.5-07, C4; counter-review I) ---------------------------
//
// Under `vefa=incluses` the served `total` counts the listed mutations, VEFA included: it is not
// `adresse.dvf.ventes_adresse.nombre`, which never counts a VEFA. The entry the page of plan 06 reads
// from these answers — the served total under that field, with the unit, dates, state and source of
// the list — is frozen at the starting SHA of plan 07 (`ventes-declarees-vefa-<variant>`), before the
// page asks for the VEFA. The change of plan 07 is declared against these sections, never by
// rewriting them.

/** The synthetic `vefa=incluses` answers of tests/e2e/fixtures/adresse-ventes.ts, their served counts written here. */
const VENTES_DECLAREES_VEFA: Record<string, { declarees: unknown; total: number; nombreVefa: number }> = {
	'ventes-declarees-vefa-44109-zz04-00004': { declarees: VENTES_DECLAREES_44109_AVEC_VEFA.body, total: 5, nombreVefa: 2 },
	'ventes-declarees-vefa-seules-hors-parcelle': { declarees: VENTES_DECLAREES_VEFA_SEULES.body, total: 2, nombreVefa: 2 },
	'ventes-declarees-vefa-hors-page': { declarees: VENTES_DECLAREES_VEFA_HORS_PAGE.body, total: 23, nombreVefa: 4 }
};

/** Writer of the `ventes-declarees-vefa-*` sections at the starting SHA of plan 07. */
const VENTES_DECLAREES_VEFA_AU_DEPART: Record<string, () => Promise<EntreeInventaire[]>> = Object.fromEntries(
	Object.entries(VENTES_DECLAREES_VEFA).map(([cle, { declarees }]) => [cle, () => entreeListeDeclaree(declarees)])
);

// Review 05 (IMPORTANT 2): the textual record keeps the panel entries without their targets. The links
// of every Source panel of the list (`documents-<page>-<variant>-liens`: block, entry term, text,
// href) are frozen at the starting SHA of plan 06, a complementary section, never by enriching the
// sections above.
const LIENS_AU_DEPART: Record<string, () => Promise<LienDocumentaire[]>> = Object.fromEntries(
	NOMS_VARIANTES.map((nom) => [`${PAGE_DOCUMENTS}-${nom}-liens`, () => lireLaListe(variante(nom), releveLiensDesVolets)])
);

// --- comparison tables (plan 04.5-10, B6; counter-review B1, I1, I3) ----------------------------
//
// Plan 10 regroups the comparison table (factorised metadata, dated wordings). The cells of the three
// comparators, on every frozen answer of `commune-comparaison.json`, `adresse-comparaison.json` and
// `dpe-comparaison.json`, are frozen at the starting SHA of plan 10, before any change of the table
// (`comparaison-communes`, `comparaison-adresses`, `comparaison-documents`): each entry identified by
// answer, nature, group, row key and column code, with its served value, units, state, source, served
// period, every date of its panel and its gap line. The « before » path reads the composed models of
// that SHA; afterwards the models of the table must give exactly these entries. Plan 13 (R5) adds its
// own comparison for the documents, never by rewriting `comparaison-documents`.

type ReponsesFigees<T> = Record<string, { donnees: T; meta?: { source?: SourceAttribution[] | null } }>;

const REPONSES_COMMUNES = comparaisonsCommunes as unknown as ReponsesFigees<ComparaisonCommunes>;
const REPONSES_ADRESSES = comparaisonsAdresses as unknown as ReponsesFigees<Parameters<typeof projeterComparaisonAdresses>[0]>;
const REPONSES_DOCUMENTS = comparaisonsDocuments as unknown as ReponsesFigees<Parameters<typeof projeterComparaisonDocuments>[0]>;

/**
 * Declared change C1 (inventory of 2026-11-10, arbitrated by Ianis on 2026-11-10: « oui aux trois catégories
 * de la fiche commune »): the comparator of communes groups its rows by the three categories of the fiche,
 * no longer by its five former themes. The frozen section `comparaison-communes` is not rewritten: the
 * « after » path puts each row back under the former theme of its fiche row (`themesCommune`, the theme
 * the fiche still gives it) in the order of that theme, as the composer did at the starting SHA. Every
 * other attribute of a cell (value, units, state, source, period, dates, gap) is compared as before; the
 * new grouping itself is checked in `comparateur-categories.test.ts`.
 */
function sousLesAnciensThemes(modele: ComparaisonModel, donnees: ComparaisonCommunes, sources: SourceAttribution[] | null): ComparaisonModel {
	// Declared change C5 (inventory of 2026-11-10): the two rent rows are read among « Repères ». At the
	// starting SHA they were rows of their theme: the « after » path takes them out of the landmarks and
	// puts them back there, like every other row; their place among the landmarks is checked in
	// `comparateur-categories.test.ts`.
	const estLoyer = (ligne: { fieldId: string }) => ligne.fieldId === 'loyers.loyer_m2_appartement' || ligne.fieldId === 'loyers.loyer_m2_maison';
	const reperes = modele.groupes
		.filter((groupe) => groupe.cle === 'reperes')
		.map((groupe) => ({ ...groupe, lignes: groupe.lignes.filter((ligne) => !estLoyer(ligne)) }));
	const lignes = modele.groupes.flatMap((groupe) => (groupe.cle === 'reperes' ? groupe.lignes.filter(estLoyer) : groupe.lignes));
	const fiches = donnees.communes.flatMap((commune) => (commune.synthese ? [themesCommune(commune.synthese, sources)] : []));
	const vus = new Set<string>();
	const themes = COMMUNE_THEMES.map((theme) => {
		const ids = fiches
			.flatMap((fiche) => fiche.find((item) => item.cle === theme.cle)?.lignes.map((ligne) => ligne.id) ?? [])
			.filter((id) => !vus.has(id) && (vus.add(id), true));
		return { cle: theme.cle, titre: theme.titre, lignes: ids.flatMap((id) => lignes.filter((ligne) => ligne.ligne === id)) };
	}).filter((groupe) => groupe.lignes.length > 0);
	// A row no former theme holds would be lost here: the count below refuses it.
	if (themes.reduce((n, groupe) => n + groupe.lignes.length, 0) !== lignes.length) throw new Error('C1: a row of the comparator has no former theme');
	return { ...modele, groupes: [...reperes, ...themes] as unknown as ComparaisonModel['groupes'] };
}

/** The models of the three comparators on every frozen answer, as the page composes them. */
const MODELES_COMPARAISON: Record<string, () => { reponse: string; modele: ComparaisonModel }[]> = {
	'comparaison-communes': () =>
		Object.entries(REPONSES_COMMUNES).map(([reponse, { donnees, meta }]) => ({
			reponse,
			modele: sousLesAnciensThemes(
				composeComparaison(structuredClone(donnees), structuredClone(meta?.source ?? null)),
				donnees,
				meta?.source ?? null
			)
		})),
	'comparaison-adresses': () =>
		Object.entries(REPONSES_ADRESSES).map(([reponse, { donnees }]) => ({
			reponse,
			modele: composeComparaisonAdresses(projeterComparaisonAdresses(structuredClone(donnees)))
		})),
	'comparaison-documents': () =>
		Object.entries(REPONSES_DOCUMENTS).map(([reponse, { donnees }]) => ({
			reponse,
			modele: composeComparaisonDocuments(projeterComparaisonDocuments(structuredClone(donnees)))
		}))
};

// Declared change of plan 13 (R5, « m2-premier-niveau »): the document rows (direct, and designated on
// addresses with the prefix `designe_`) add the per-m² measures, named here, and move the yearly totals
// after them. The totals keep their key, field, value and every other attribute. The frozen sections
// are not rewritten: the « after » path drops the named additions and puts the moved rows back in their
// starting order; the additions and the new order are checked against the served answers below.
const LIGNES_AJOUTEES_PLAN_13: Readonly<Record<string, string>> = {
	consommation_m2: 'dpe.document.conso_ep_m2',
	emissions_m2: 'dpe.document.emissions_ges_m2'
};
const ORDRE_ETIQUETTES_AU_DEPART = ['classe_energie', 'consommation', 'classe_climat', 'emissions'];
const ORDRE_ETIQUETTES_PLAN_13 = ['classe_energie', 'consommation_m2', 'classe_climat', 'emissions_m2', 'consommation', 'emissions'];
const ligneDocument = (ligne: string) => ligne.replace(/^designe_/, '');
const estLigneAjoutee = (ligne: string) => Object.hasOwn(LIGNES_AJOUTEES_PLAN_13, ligneDocument(ligne));

/**
 * Drops the named additions and puts the label rows back in their starting order, in their own slots:
 * each run of label rows (one answer, from its « classe_energie ») is sorted by its starting order.
 */
function auDepart<T>(items: readonly T[], ligneDe: (item: T) => string): T[] {
	const gardes = items.filter((item) => !estLigneAjoutee(ligneDe(item)));
	const rang = (item: T) => ORDRE_ETIQUETTES_AU_DEPART.indexOf(ligneDocument(ligneDe(item)));
	const places = gardes.flatMap((item, index) => (rang(item) >= 0 ? [index] : []));
	let bloc = 0;
	const deplaces = places.map((index, position) => {
		const item = gardes[index];
		const precedent = position ? gardes[places[position - 1]] : null;
		if (rang(item) === 0 && (precedent === null || rang(precedent) !== 0)) bloc += 1;
		return { item, bloc, position };
	});
	const ranges = [...deplaces].sort((a, b) => a.bloc - b.bloc || rang(a.item) - rang(b.item) || a.position - b.position);
	const resultat = [...gardes];
	places.forEach((index, position) => (resultat[index] = ranges[position].item));
	return resultat;
}

/** Writer (and reader) of the `comparaison-*` sections: the cells of the composed models. */
const COMPARAISONS_AU_DEPART: Record<string, () => EntreeComparaison[]> = Object.fromEntries(
	Object.entries(MODELES_COMPARAISON).map(([cle, modeles]) => [
		cle,
		() => auDepart(modeles().flatMap(({ reponse, modele }) => inventaireComparaison(reponse, modele)), (entree) => entree.ligne)
	])
);

// --- the DPE document page (plan 04.5-12, C1; counter-review I2) -------------------------------
//
// Plan 12 moves the yearly totals of landmarks ① and ② to « Ce que déclare le document » and reads
// those landmarks in the per-m² values served next to the totals. The landmarks and characteristics
// of the page are frozen at the starting SHA of plan 12 (`document-dpe-<case>`), before the page
// changes: the landmarks through their public model (`reperesDocument`); the characteristics, which
// have no source panel at that SHA, through their model and the served value — the unit is the one
// the row writes (the contract unit: « 79 m² »), the source the served one, no date. Afterwards the
// reading projection of the page must give exactly these entries plus the two per-m² values, named.

type ReponseDocument = { donnees: unknown };

/** Answers of the page: the real document of the UI review (observed values) and a retired one. */
const DOCUMENTS_DPE: Record<string, ReponseDocument> = {
	'document-dpe-2644E8000030X': DOCUMENT_REEL,
	'document-dpe-retire': DOCUMENT_RETIRE
};

/**
 * The per-m² values the page adds to the frozen entries (C1, plan 04.5-12), named one by one, with the
 * unit their landmark panel declares. Every other entry of the page must stay its frozen section.
 */
const AJOUTS_DOCUMENT_DPE: Readonly<Record<string, { etiquette: 'etiquette_energie' | 'etiquette_climat'; unite: string }>> = {
	'dpe.document.conso_ep_m2': { etiquette: 'etiquette_energie', unite: 'kWhEP/m²/an' },
	'dpe.document.emissions_ges_m2': { etiquette: 'etiquette_climat', unite: 'kg CO₂ éq/m²/an' }
};

const estAjout = (entree: EntreeInventaire) => Object.hasOwn(AJOUTS_DOCUMENT_DPE, entree.fieldId);

/** The « after » path: the reading projection of the page on the same answer. */
function inventaireDocument(reponse: ReponseDocument): EntreeInventaire[] {
	return inventaireLecture(lectureDocument(projeterDocument(structuredClone(reponse.donnees) as Parameters<typeof projeterDocument>[0])));
}

/** The named additions as the answer serves them: value, state and source of `mesure_m2`. */
function ajoutsServis(reponse: ReponseDocument): EntreeInventaire[] {
	const reperes = (reponse.donnees as { reperes: Record<string, { mesure_m2: { etat: string; valeur: unknown; motif: string | null; source: { nom: string; url: string } } }> | null }).reperes;
	if (!reperes) return [];
	return Object.entries(AJOUTS_DOCUMENT_DPE).map(([fieldId, { etiquette, unite }]) => {
		const servie = reperes[etiquette].mesure_m2;
		const etat = (servie.etat === 'value' ? { kind: 'valeur' } : { kind: 'absence', etat: servie.etat, motif: servie.motif ?? '' }) as EntreeInventaire['etat'];
		const valeur = { valeur: servie.etat === 'value' ? JSON.stringify(servie.valeur) : 'null', etat };
		return { fieldId, ...valeur, unite, source: { nom: servie.source.nom, url: servie.source.url }, dates: [] };
	});
}

/**
 * The `document-dpe-*` sections were written at the starting SHA of plan 12 (commit of the freeze) by
 * the public models of that SHA: the landmarks (`reperesDocument`) and the characteristics with the
 * unit their row wrote. What must stay each section since: the « after » path less its named additions.
 */
const DOCUMENTS_DPE_AU_DEPART: Record<string, () => EntreeInventaire[]> = Object.fromEntries(
	Object.entries(DOCUMENTS_DPE).map(([cle, reponse]) => [cle, () => inventaireDocument(reponse).filter((entree) => !estAjout(entree))])
);

function lireReference(): Reference | null {
	return existsSync(CHEMIN_REFERENCE) ? (JSON.parse(readFileSync(CHEMIN_REFERENCE, 'utf8')) as Reference) : null;
}

/** The public models of `src/` must be those of the declared base revision when a section is written. */
function verifierArbreAuSha(base: string) {
	execFileSync(
		'git',
		[
			'diff', '--quiet', base, '--', 'src',
			':(exclude,glob)src/**/*.test.ts',
			':(exclude)src/lib/data/lecture.ts',
			':(exclude)src/lib/data/__fixtures__/lecture-inventaire-reference.json'
		],
		{ cwd: RACINE_FRONTEND, stdio: 'inherit' }
	);
}

async function ecrireReference() {
	const base = process.env.DC_LECTURE_BASE_REVISION ?? '';
	if (!/^[0-9a-f]{40}$/.test(base)) throw new Error('DC_LECTURE_BASE_REVISION must be the full starting SHA');
	verifierArbreAuSha(base);
	const existante = lireReference();
	const capturedAt = new Date().toISOString();
	const nouvelles: Record<string, SectionReference> = {};
	const ecrivains: Record<string, () => unknown[] | Promise<unknown[]>> = {
		...AVANT,
		...DOCUMENTS_AU_DEPART,
		...VENTES_DECLAREES_AU_DEPART,
		...LIENS_AU_DEPART,
		...VENTES_DECLAREES_VEFA_AU_DEPART,
		...COMPARAISONS_AU_DEPART,
		...DOCUMENTS_DPE_AU_DEPART
	};
	for (const [cle, avant] of Object.entries(ecrivains)) {
		const entrees = (await avant()) as EntreeInventaire[];
		const section = existante?.sections[cle];
		// Never rewritten: an existing section must still be what the models produce.
		if (section) expect(entrees).toEqual(section.entrees);
		else nouvelles[cle] = { baseRevision: base, capturedAt, entrees };
	}
	// New sections go first so that the diff of the file only adds lines.
	const reference: Reference = {
		regle: existante?.regle ?? REGLE,
		baseRevision: existante?.baseRevision ?? base,
		capturedAt: existante?.capturedAt ?? capturedAt,
		sections: { ...nouvelles, ...(existante?.sections ?? {}) }
	};
	writeFileSync(CHEMIN_REFERENCE, `${JSON.stringify(reference, null, '\t')}\n`);
}

const ECRITURE = process.env.DC_LECTURE_REFERENCE_WRITE === '1';

function section(cle: string): SectionReference {
	const reference = lireReference();
	const trouvee = reference?.sections[cle];
	if (!trouvee) throw new Error(`Missing section ${cle} in lecture-inventaire-reference.json`);
	return trouvee;
}

describe('règle zéro — inventaire figé au SHA de départ (D45-01)', () => {
	it.runIf(ECRITURE)('writes the missing sections from the public models', async () => {
		await ecrireReference();
	});

	it('the reference carries its rule and its starting revision', () => {
		const reference = lireReference();
		expect(reference?.regle).toBe(REGLE);
		expect(reference?.baseRevision).toMatch(/^[0-9a-f]{40}$/);
	});

	// A section whose subject is gone must leave the reference with it (plan 04.7-18): no orphan, no unread key.
	it('every section of the reference is read by a section map of this file, and every map key exists in the reference', () => {
		const lues = [
			...Object.keys(AVANT),
			...Object.keys(DOCUMENTS_DPE),
			...Object.keys(COMPARAISONS_AU_DEPART),
			...Object.keys(DOCUMENTS_AU_DEPART),
			...Object.keys(LIENS_AU_DEPART),
			...Object.keys(VENTES_DECLAREES_AU_DEPART),
			...Object.keys(VENTES_DECLAREES_VEFA_AU_DEPART)
		];
		expect(new Set(lues).size).toBe(lues.length);
		const presentes = Object.keys(lireReference()?.sections ?? {});
		expect(presentes.filter((cle) => !lues.includes(cle)).sort()).toEqual([]);
		expect(lues.filter((cle) => !presentes.includes(cle)).sort()).toEqual([]);
	});

	it.each(Object.keys(AVANT))('%s: the public models and the reading projection give the frozen inventory', (cle) => {
		const reference = section(cle).entrees;
		expect(AVANT[cle]()).toEqual(reference);
		expect(APRES[cle]()).toEqual(reference);
	});

	// The six attributes of the inventory.
	type AttributHistorique = keyof EntreeInventaire;
	const MUTATIONS: Record<AttributHistorique, (entree: EntreeInventaire) => EntreeInventaire> = {
		fieldId: (entree) => ({ ...entree, fieldId: `${entree.fieldId}.corrompu` }),
		valeur: (entree) => ({ ...entree, valeur: entree.valeur === '1' ? '2' : '1' }),
		unite: (entree) => ({ ...entree, unite: entree.unite === '%' ? '€/m²' : '%' }),
		etat: (entree) => ({
			...entree,
			etat: entree.etat.kind === 'valeur' ? { kind: 'absence', etat: 'not_available', motif: '' } : { kind: 'valeur' }
		}),
		source: (entree) => ({ ...entree, source: { nom: `${entree.source?.nom ?? ''} (corrompue)`, url: entree.source?.url ?? null } }),
		dates: (entree) => ({ ...entree, dates: [...entree.dates, { terme: 'Période', valeur: '1999' }] })
	};

	it.each(Object.keys(MUTATIONS) as AttributHistorique[])('detects a single altered %s', (attribut) => {
		const reference = section('commune-85191').entrees;
		const inventaire = APRES['commune-85191']();
		expect(inventaire).toEqual(reference);
		for (let index = 0; index < inventaire.length; index += 1) {
			const muee = inventaire.map((entree, i) => (i === index ? MUTATIONS[attribut](entree) : entree));
			expect(() => expect(muee).toEqual(reference)).toThrow();
		}
	});
});

describe('règle zéro — page document DPE figée au SHA de départ du plan 12 (D45-01, C1)', () => {
	it.each(Object.keys(DOCUMENTS_DPE))('%s: the reading projection gives the frozen entries plus the named per-m² values only', (cle) => {
		const figee = section(cle);
		expect(figee.baseRevision).toMatch(/^[0-9a-f]{40}$/);
		const inventaire = inventaireDocument(DOCUMENTS_DPE[cle]);
		// Every frozen value is kept with its six attributes (the yearly totals moved to the detail too).
		expect(inventaire.filter((entree) => !estAjout(entree))).toEqual(figee.entrees);
		// The additions are the served per-m² values, exactly, and nothing else.
		expect(inventaire.filter(estAjout)).toEqual(ajoutsServis(DOCUMENTS_DPE[cle]));
	});

	it('the per-m² additions of the real document are its published values (318 kWhEP/m²/an, 25 kg CO₂ éq/m²/an)', () => {
		expect(inventaireDocument(DOCUMENT_REEL).filter(estAjout).map((entree) => [entree.fieldId, entree.valeur, entree.unite])).toEqual([
			['dpe.document.conso_ep_m2', '318', 'kWhEP/m²/an'],
			['dpe.document.emissions_ges_m2', '25', 'kg CO₂ éq/m²/an']
		]);
		expect(inventaireDocument(DOCUMENT_RETIRE)).toEqual([]);
	});

	it('a moved total altered on the way (value, unit or source) fails the equality', () => {
		const figee = section('document-dpe-2644E8000030X').entrees;
		const conserve = inventaireDocument(DOCUMENT_REEL).filter((entree) => !estAjout(entree));
		for (const [total, uniteM2] of [
			['dpe.document.conso_ep', 'kWhEP/m²/an'],
			['dpe.document.emissions_ges', 'kg CO₂ éq/m²/an']
		]) {
			const index = conserve.findIndex((entree) => entree.fieldId === total);
			expect(index).toBeGreaterThanOrEqual(0);
			for (const alteration of [
				{ valeur: '102.58' },
				{ unite: uniteM2 },
				{ source: { nom: 'Autre source', url: null } },
				{ etat: { kind: 'absence', etat: 'not_reported', motif: '' } }
			] as Partial<EntreeInventaire>[]) {
				expect(() => expect(conserve.map((entree, i) => (i === index ? { ...entree, ...alteration } : entree))).toEqual(figee)).toThrow();
			}
		}
	});

	it('the real document is frozen with its two yearly totals, the retired one without any value', () => {
		const reel = section('document-dpe-2644E8000030X').entrees;
		expect(reel.filter((entree) => ['dpe.document.conso_ep', 'dpe.document.emissions_ges'].includes(entree.fieldId))).toEqual([
			expect.objectContaining({ fieldId: 'dpe.document.conso_ep', valeur: '127982.4', unite: 'kWhEP/an', etat: { kind: 'valeur' } }),
			expect.objectContaining({ fieldId: 'dpe.document.emissions_ges', valeur: '10257.5', unite: 'kg CO₂ éq/an', etat: { kind: 'valeur' } })
		]);
		expect(section('document-dpe-retire').entrees).toEqual([]);
	});
});

describe('règle zéro — cellules des trois comparateurs figées au SHA de départ du plan 10 (D45-01, B6)', () => {
	const NATURES: Record<string, EntreeComparaison['nature']> = {
		'comparaison-communes': 'commune',
		'comparaison-adresses': 'adresse',
		'comparaison-documents': 'dpe'
	};
	const comparaison = (cle: string) => section(cle).entrees as unknown as EntreeComparaison[];

	it.each(Object.keys(COMPARAISONS_AU_DEPART))('%s: the models of the table give the frozen cells, column by column', (cle) => {
		expect(COMPARAISONS_AU_DEPART[cle]()).toEqual(comparaison(cle));
	});

	it.each(Object.keys(MODELES_COMPARAISON))('%s: one entry per cell of every frozen answer, identified by nature, row and column', (cle) => {
		const reference = comparaison(cle);
		const attendues = auDepart(
			MODELES_COMPARAISON[cle]().flatMap(({ reponse, modele }) =>
				modele.groupes.flatMap((groupe) =>
					groupe.lignes.flatMap((ligne) => ligne.cellules.map((_, position) => [reponse, groupe.cle, ligne.cle, modele.colonnes[position].code]))
				)
			),
			(identite) => identite[2]
		);
		expect(reference.map((entree) => [entree.reponse, entree.groupe, entree.ligne, entree.colonne])).toEqual(attendues);
		expect(new Set(reference.map((entree) => entree.nature))).toEqual(new Set([NATURES[cle]]));
		// Each cell is said once: answer, group, row and column identify it.
		const identites = reference.map((entree) => JSON.stringify([entree.reponse, entree.groupe, entree.ligne, entree.colonne]));
		expect(new Set(identites).size).toBe(identites.length);
	});

	it('the frozen cells keep the dates of their panels, the date of the last import included', () => {
		const termes = new Set(Object.keys(NATURES).flatMap((cle) => comparaison(cle).flatMap((entree) => entree.dates.map((date) => date.terme))));
		expect([...termes].sort()).toEqual([...TERMES_DATES].sort());
	});

	// Counter-review I1: two columns of one row exchanging their sources keep the set of sources; the
	// inventory identified by column must still fail.
	it.each(Object.keys(NATURES))('%s: two sources exchanged between two columns of one row fail the equality', (cle) => {
		const reference = comparaison(cle);
		const inventaire = COMPARAISONS_AU_DEPART[cle]();
		const memeLigne = (a: EntreeComparaison, b: EntreeComparaison) => a.reponse === b.reponse && a.groupe === b.groupe && a.ligne === b.ligne;
		const index = inventaire.findIndex((entree, i) =>
			inventaire.some((autre, j) => j > i && memeLigne(entree, autre) && JSON.stringify(autre.source) !== JSON.stringify(entree.source)));
		expect(index).toBeGreaterThanOrEqual(0);
		const autre = inventaire.findIndex((item, j) => j > index && memeLigne(inventaire[index], item) && JSON.stringify(item.source) !== JSON.stringify(inventaire[index].source));
		const permute = inventaire.map((entree, i) =>
			i === index ? { ...entree, source: inventaire[autre].source } : i === autre ? { ...entree, source: inventaire[index].source } : entree);
		// The set of sources of the row is unchanged, the inventory is not.
		expect(permute.map((entree) => JSON.stringify(entree.source)).sort()).toEqual(inventaire.map((entree) => JSON.stringify(entree.source)).sort());
		expect(() => expect(permute).toEqual(reference)).toThrow();
	});

	it.each(Object.keys(NATURES))('%s: a date removed from one cell fails the equality', (cle) => {
		const reference = comparaison(cle);
		const inventaire = COMPARAISONS_AU_DEPART[cle]();
		const datees = inventaire.flatMap((entree, index) => (entree.dates.length ? [index] : []));
		if (cle === 'comparaison-documents') {
			// The document panels carry no dated entry: the guard is the comparison of the empty list.
			expect(datees).toEqual([]);
			return;
		}
		expect(datees.length).toBeGreaterThan(0);
		for (const index of datees) {
			const muee = inventaire.map((entree, i) => (i === index ? { ...entree, dates: entree.dates.slice(1) } : entree));
			expect(() => expect(muee).toEqual(reference)).toThrow();
		}
	});

	it('the date of the last import removed from one commune cell fails the equality', () => {
		const reference = comparaison('comparaison-communes');
		const inventaire = COMPARAISONS_AU_DEPART['comparaison-communes']();
		const index = inventaire.findIndex((entree) => entree.dates.some((date) => date.terme === 'Dernière importation réussie'));
		expect(index).toBeGreaterThanOrEqual(0);
		const muee = inventaire.map((entree, i) =>
			i === index ? { ...entree, dates: entree.dates.filter((date) => date.terme !== 'Dernière importation réussie') } : entree);
		expect(() => expect(muee).toEqual(reference)).toThrow();
	});

	it.each(Object.keys(NATURES))('%s: a served value, period or gap given to another column fails the equality', (cle) => {
		const reference = comparaison(cle);
		const inventaire = COMPARAISONS_AU_DEPART[cle]();
		for (const attribut of ['valeur', 'periode', 'ecart'] as const) {
			const index = inventaire.findIndex((entree, i) =>
				inventaire.some((autre, j) => j !== i && autre.ligne === entree.ligne && autre.reponse === entree.reponse && JSON.stringify(autre[attribut]) !== JSON.stringify(entree[attribut])));
			if (index < 0) continue;
			const autre = inventaire.findIndex((item, j) => j !== index && item.ligne === inventaire[index].ligne && item.reponse === inventaire[index].reponse && JSON.stringify(item[attribut]) !== JSON.stringify(inventaire[index][attribut]));
			const permute = inventaire.map((entree, i) =>
				i === index ? { ...entree, [attribut]: inventaire[autre][attribut] } : i === autre ? { ...entree, [attribut]: inventaire[index][attribut] } : entree);
			expect(() => expect(permute).toEqual(reference), `${cle} ${attribut}`).toThrow();
		}
	});

	// The decided phrases of a masked declared address (owner, P-ADR-01 of 2026-10-02, document page),
	// written here apart from the generator: `non affichée : {phrase}` for a known reason, else `non affichée`.
	const ADRESSE_MASQUEE: Record<string, string> = {
		complement_present: 'non affichée : le document y joint un complément (étage, porte, bâtiment ou nom).',
		non_verifiable_sans_complement: 'non affichée : elle ne se vérifie pas comme une adresse sans complément.'
	};

	// Codex review of plan 10 (IMPORTANT): the inventory reads the state of `brut`, the table renders `etat`.
	// The rendered state of every cell must be the frozen one, so an absence never renders as a value.
	const etatsRendusDivergents = (cle: string, modeles: { reponse: string; modele: ComparaisonModel }[]) => {
		const figes = new Map(comparaison(cle).map((entree) => [JSON.stringify([entree.reponse, entree.groupe, entree.ligne, entree.colonne]), entree.etat]));
		return modeles.flatMap(({ reponse, modele }) =>
			modele.groupes.flatMap((groupe) =>
				groupe.lignes.filter((ligne) => !estLigneAjoutee(ligne.cle)).flatMap((ligne) =>
					ligne.cellules.flatMap((cellule) => {
						const rendu = cellule.etat
							? cellule.etat.kind === 'absence'
								? { kind: 'absence', etat: cellule.etat.etat, motif: cellule.etat.motif }
								: { kind: 'valeur' }
							: null;
						const fige = figes.get(JSON.stringify([reponse, groupe.cle, ligne.cle, cellule.code]));
						// The one decided exception (owner, document page): a masked declared address is worded by
						// the decided phrase of its frozen reason, exactly — never « Secret statistique », never a number.
						const motifFige = fige?.kind === 'absence' ? (fige as { motif?: string | null }).motif ?? '' : '';
						const adresseMasquee = ligne.cle.endsWith('adresse_declaree') && fige?.kind === 'absence' && fige.etat === 'suppressed'
							&& cellule.etat?.kind === 'valeur'
							&& cellule.etat.texte === (Object.hasOwn(ADRESSE_MASQUEE, motifFige) ? ADRESSE_MASQUEE[motifFige] : 'non affichée');
						if (adresseMasquee) return [];
						return JSON.stringify(rendu) === JSON.stringify(fige ?? null) ? [] : [`${reponse} ${ligne.cle} ${cellule.code} ${JSON.stringify(rendu)} != ${JSON.stringify(fige ?? null)}`];
					})
				)
			)
		);
	};

	it.each(Object.keys(NATURES))('%s: the rendered state of every cell is its frozen state', (cle) => {
		expect(etatsRendusDivergents(cle, MODELES_COMPARAISON[cle]())).toEqual([]);
	});

	it.each(Object.keys(NATURES))('%s: an absence rendered as a value in the model, its raw untouched, fails the guard', (cle) => {
		const modeles = MODELES_COMPARAISON[cle]();
		const cellule = modeles
			.flatMap(({ modele }) => modele.groupes.flatMap((groupe) => groupe.lignes.flatMap((ligne) => ligne.cellules)))
			.find((item) => item.etat?.kind === 'absence');
		if (!cellule) return;
		(cellule as { etat: unknown }).etat = { kind: 'valeur', texte: '0', unite: null };
		expect(etatsRendusDivergents(cle, modeles).length).toBe(1);
	});

	// Codex review of plan 10 (MINOR): the exception of the masked declared address is the decided
	// phrase only; an empty text, « Secret statistique » or any other wording without a figure fails.
	it.each(['', 'Secret statistique', 'rue des Lilas', 'non affichée'])('a masked declared address rendered as « %s » fails the guard', (texte) => {
		const modeles = MODELES_COMPARAISON['comparaison-documents']();
		const masquees = modeles.flatMap(({ modele }) =>
			modele.groupes.flatMap((groupe) => groupe.lignes.filter((ligne) => ligne.cle.endsWith('adresse_declaree')).flatMap((ligne) => ligne.cellules))
		).filter((cellule) => cellule.etat?.kind === 'valeur' && cellule.brut[0]?.etat?.kind === 'absence');
		expect(masquees.length).toBeGreaterThan(0);
		expect(etatsRendusDivergents('comparaison-documents', modeles)).toEqual([]);
		(masquees[0] as { etat: unknown }).etat = { kind: 'valeur', texte, unite: null };
		expect(etatsRendusDivergents('comparaison-documents', modeles).length).toBe(1);
	});
});

describe('plan 13 (R5) — mesures par m² des comparateurs de documents, ajouts nommés (D45-01, D45-05)', () => {
	type CelluleServie = { numero?: string; ban_id?: string; field_id: string; etat: string; valeur: unknown; motif: string | null; ecart: { valeur: number } | null };
	type ReponseServie = { donnees: { groupes: { cle: string; lignes: { cle: string; field_id: string; cellules: CelluleServie[] }[] }[] } };
	const REPONSES: Record<string, Record<string, ReponseServie>> = {
		'comparaison-adresses': comparaisonsAdresses as unknown as Record<string, ReponseServie>,
		'comparaison-documents': comparaisonsDocuments as unknown as Record<string, ReponseServie>
	};
	const UNITES: Readonly<Record<string, string>> = {
		'dpe.document.conso_ep_m2': 'kWhEP/m²/an',
		'dpe.document.emissions_ges_m2': 'kg CO₂ éq/m²/an'
	};

	it.each(Object.keys(REPONSES))('%s: the label rows follow the declared order, per m² first, totals after', (cle) => {
		let vues = 0;
		for (const { reponse, modele } of MODELES_COMPARAISON[cle]()) {
			const lignes = modele.groupes.flatMap((groupe) => groupe.lignes.map((ligne) => ligne.cle));
			const etiquettes = lignes.filter((ligne) => ORDRE_ETIQUETTES_PLAN_13.includes(ligneDocument(ligne))).map(ligneDocument);
			if (!etiquettes.length) continue;
			vues += 1;
			expect(etiquettes, reponse).toEqual(ORDRE_ETIQUETTES_PLAN_13);
		}
		expect(vues).toBeGreaterThan(0);
	});

	it.each(Object.keys(REPONSES))('%s: each added cell is the served per-m² value, state and gap, never a quotient', (cle) => {
		let vues = 0;
		for (const { reponse, modele } of MODELES_COMPARAISON[cle]()) {
			const servies = REPONSES[cle][reponse].donnees.groupes.flatMap((groupe) => groupe.lignes).filter((ligne) => estLigneAjoutee(ligne.cle));
			const entrees = inventaireComparaison(reponse, modele).filter((entree) => estLigneAjoutee(entree.ligne));
			expect(entrees.length, reponse).toBe(servies.reduce((total, ligne) => total + ligne.cellules.length, 0));
			for (const ligne of servies) {
				expect(ligne.field_id).toBe(LIGNES_AJOUTEES_PLAN_13[ligneDocument(ligne.cle)]);
				for (const servie of ligne.cellules) {
					const entree = entrees.find((item) => item.ligne === ligne.cle && item.colonne === (servie.numero ?? servie.ban_id));
					expect(entree, `${reponse} ${ligne.cle}`).toBeDefined();
					vues += 1;
					if (servie.etat === 'not_applicable' && servie.motif === 'aucun_dpe_designe') {
						expect(entree!.etat).toBeNull();
						continue;
					}
					expect(entree!.fieldId).toBe(ligne.field_id);
					expect(entree!.valeur).toBe(servie.etat === 'value' ? JSON.stringify(servie.valeur) : 'null');
					expect(entree!.etat).toEqual(servie.etat === 'value' ? { kind: 'valeur' } : { kind: 'absence', etat: servie.etat, motif: servie.motif ?? '' });
					expect(entree!.uniteValeur).toBe(servie.etat === 'value' ? UNITES[ligne.field_id] : null);
					if (servie.ecart) expect(entree!.ecart?.texte.startsWith(`${String(servie.ecart.valeur).replace('-', '\u2212')} `)).toBe(true);
				}
			}
		}
		expect(vues).toBeGreaterThan(0);
	});

	it('a missing per-m² value rendered as a quotient fails the guard', () => {
		const { reponse, modele } = MODELES_COMPARAISON['comparaison-documents']().find((item) => item.reponse === 'meme_methode_trois')!;
		const numero = modele.colonnes[2].code;
		const garde = () => {
			const entree = inventaireComparaison(reponse, modele).find((item) => item.ligne === 'consommation_m2' && item.colonne === numero)!;
			expect(entree.etat).toEqual({ kind: 'absence', etat: 'not_reported', motif: '' });
			expect(entree.valeur).toBe('null');
		};
		garde();
		const cellule = modele.groupes[0].lignes.find((ligne) => ligne.cle === 'consommation_m2')!.cellules[2];
		cellule.brut = [{ fieldId: 'dpe.document.conso_ep_m2', valeur: '225.7', etat: { kind: 'valeur' } }];
		expect(garde).toThrow();
	});
});

describe('règle zéro — liste des documents DPE, bloc de la clé ADEME figé (D45-01, C2)', () => {
	const documentaire = (cle: string) => section(cle).entrees as unknown as EntreeDocumentaire[];
	const cartesDe = (entrees: readonly EntreeDocumentaire[]) => entrees.filter((entree): entree is CarteDocumentaire => entree.element === 'carte');
	// Owner review of 09/11: the « En clair » line that now opens a panel is a named addition, never part of a
	// frozen section; it is checked on its own below.
	const estEnClair = (entree: EntreeDocumentaire) => entree.element === 'volet' && JSON.stringify(entree).includes(`"${LIBELLE_EN_CLAIR}`);
	const sansEnClair = (entrees: readonly EntreeDocumentaire[]) => entrees.filter((entree) => !estEnClair(entree));
	const ademe = (entrees: readonly EntreeDocumentaire[]) => sansEnClair(entrees).filter((entree) => entree.bloc === 'ademe');

	it.each(Object.keys(DOCUMENTS_AU_DEPART))('%s: the frozen section is the block of the ADEME key', (cle) => {
		const reference = documentaire(cle);
		expect(reference.length).toBeGreaterThan(0);
		expect(reference.every((entree) => entree.bloc === 'ademe')).toBe(true);
	});

	it.each(NOMS_VARIANTES)('%s: the ADEME block of the rendered list is its frozen section, with and without the raw-address group', async (nom) => {
		const reference = documentaire(`${PAGE_DOCUMENTS}-${nom}`);
		expect(ademe(await releveRendu(variante(nom)))).toEqual(reference);
		// Without the three keys of the group (an answer before 2026-10-19), nothing else is rendered.
		expect(sansEnClair(await releveRendu(sansGroupe(variante(nom))))).toEqual(reference);
	});

	it.each(NOMS_VARIANTES)('%s: the only addition to the ADEME block is the « En clair » line of its panel, first', async (nom) => {
		const bloc = (await releveRendu(variante(nom))).filter((entree) => entree.bloc === 'ademe');
		const ajouts = bloc.filter(estEnClair);
		expect(ajouts).toHaveLength(1);
		expect(JSON.stringify(ajouts[0])).toContain(enClairChamp('adresse.dpe.nombre_declares')!);
		expect(bloc.filter((entree) => entree.element === 'volet')[0]).toBe(ajouts[0]);
	});

	it('the cards of the frozen sections are the served ADEME documents, in the served order, each with its link', () => {
		for (const nom of NOMS_VARIANTES) {
			const servis = (variante(nom).donnees.documents as { numero: string }[]).map((document) => document.numero);
			const cartes = cartesDe(documentaire(`${PAGE_DOCUMENTS}-${nom}`));
			expect(cartes.map((carte) => [carte.rang, carte.numero, carte.lien]), nom).toEqual(servis.map((numero, rang) => [rang, numero, `/dpe/${numero}`]));
		}
	});

	// The raw-address group (C2) is an addition, inventoried apart and named here: its title, its head
	// sentence, its limit when the served total exceeds its cards, its panel, then its served cards in
	// the served order, each with its link and the badge of 4.1-13. No ADEME card carries the badge.
	it.each(NOMS_VARIANTES)('%s: the raw-address group is an explicit addition, its served cards in the served order with the badge', async (nom) => {
		const reponse = variante(nom);
		const releve = await releveRendu(reponse);
		const ajouts = releve.filter((entree) => entree.bloc === 'adresse_brute');
		expect(ajouts.length + releve.filter((entree) => entree.bloc === 'ademe').length).toBe(releve.length);
		expect(cartesDe(ademe(releve)).every((carte) => carte.rattachement === null)).toBe(true);
		if (nom === 'sans-groupe') {
			expect(ajouts).toEqual([]);
			return;
		}
		const elements = ajouts.map((entree) => entree.element);
		const enTete = ['titre', 'phrase', ...(nom === 'douze' ? ['limite'] : [])];
		expect(elements.slice(0, enTete.length)).toEqual(enTete);
		expect(elements.filter((element) => !['titre', 'phrase', 'limite', 'volet', 'carte'].includes(element))).toEqual([]);
		expect(elements).toContain('volet');
		const servies = (reponse.donnees.documents_adresse_brute ?? []) as { numero: string }[];
		expect(cartesDe(ajouts).map((carte) => [carte.rang, carte.numero, carte.lien, carte.rattachement])).toEqual(
			servies.map((carte, rang) => [rang, carte.numero, `/dpe/${carte.numero}`, BADGE_ADRESSE_BRUTE])
		);
	});

	const ATTRIBUTS = ['energie', 'climat', 'typeSurface', 'dates', 'badges', 'adresse'] as const;

	// Review 05, IMPORTANT 1: the six attributes of each card of the raw-address group, against values
	// written from the served cards (ATTENDUS_CARTES_ADRESSE_BRUTE), never read from the rendering.
	const sixAttributs = (carte: CarteDocumentaire) => ({ numero: carte.numero, ...Object.fromEntries(ATTRIBUTS.map((attribut) => [attribut, carte[attribut]])) });
	const attendusBruts = (nom: (typeof NOMS_VARIANTES)[number]) =>
		((variante(nom).donnees.documents_adresse_brute ?? []) as { numero: string }[]).map(({ numero }) => ({ numero, ...ATTENDUS_CARTES_ADRESSE_BRUTE[numero] }));

	it.each(NOMS_VARIANTES)('%s: each raw-address card renders the six attributes of its served card', async (nom) => {
		const cartes = cartesDe((await releveRendu(variante(nom))).filter((entree) => entree.bloc === 'adresse_brute'));
		const attendus = attendusBruts(nom);
		expect(attendus.every((attendu) => attendu.energie !== undefined)).toBe(true);
		expect(cartes.map(sixAttributs)).toEqual(attendus);
	});

	it.each(ATTRIBUTS)('a single altered %s of one raw-address card fails the comparison with the served values', async (attribut) => {
		const cartes = cartesDe((await releveRendu(variante('douze'))).filter((entree) => entree.bloc === 'adresse_brute'));
		const attendus = attendusBruts('douze');
		expect(cartes).toHaveLength(10);
		for (const [index, carte] of cartes.entries()) {
			const alteree = attribut === 'badges' ? [...carte.badges, 'Expiré'] : attribut === 'climat' ? null : `${carte[attribut] ?? ''} (altéré)`;
			const muees = cartes.map((item, i) => (i === index ? { ...carte, [attribut]: alteree } : item));
			if (attribut === 'climat' && carte.climat === null) continue;
			expect(() => expect(muees.map(sixAttributs)).toEqual(attendus)).toThrow();
		}
	});

	it.each(ATTRIBUTS)('a single altered %s of one card fails the equality', async (attribut) => {
		const reference = documentaire(`${PAGE_DOCUMENTS}-douze`);
		const releve = ademe(await releveRendu(variante('douze')));
		expect(releve).toEqual(reference);
		for (const [index, item] of releve.entries()) {
			if (cartesDe([item]).length === 0) continue;
			const entree = item as CarteDocumentaire;
			const alteree = attribut === 'badges' ? [...entree.badges, 'Expiré'] : `${entree[attribut] ?? ''} (altéré)`;
			const muee = releve.map((item, i) => (i === index ? { ...entree, [attribut]: alteree } : item));
			expect(() => expect(muee).toEqual(reference)).toThrow();
		}
	});
});

describe('liste des documents DPE — liens des volets Source figés (D45-01, revue 05)', () => {
	it.each(NOMS_VARIANTES)('%s: the rendered links of the panels are their frozen section, the source link the served URL', async (nom) => {
		const cle = `${PAGE_DOCUMENTS}-${nom}-liens`;
		const reference = section(cle).entrees as unknown as LienDocumentaire[];
		const liens = await LIENS_AU_DEPART[cle]();
		expect(liens).toEqual(reference);
		const sources = reference.filter((lien) => lien.terme === 'Source');
		expect(sources.length).toBeGreaterThan(0);
		const servie = (variante(nom).donnees.source as { url: string }).url;
		expect(sources.every((lien) => lien.href === servie)).toBe(true);
		// Control: a source link pointing elsewhere, or lost, fails the comparison.
		expect(() => expect(liens.map((lien, i) => (i === 0 ? { ...lien, href: 'https://example.invalid/' } : lien))).toEqual(reference)).toThrow();
		expect(() => expect(liens.slice(1)).toEqual(reference)).toThrow();
	});
});

describe('règle zéro — total des ventes déclarées à cette adresse figé (D45-01, B4)', () => {
	it.each(Object.keys(VENTES_DECLAREES_AU_DEPART))('%s: the frozen entry is the declared list as read, its served total, source and period', async (cle) => {
		const reference = section(cle).entrees;
		expect(await VENTES_DECLAREES_AU_DEPART[cle]()).toEqual(reference);
		expect(reference).toEqual([{
			fieldId: FIELD_VENTES_ADRESSE,
			valeur: String(VENTES_DECLAREES[cle].total),
			unite: null,
			etat: { kind: 'valeur' },
			source: { nom: SOURCE_DVF.nom, url: SOURCE_DVF.url },
			dates: [{ terme: 'Période', valeur: PERIODE_44109 }]
		}]);
	});
});

describe('règle zéro — réponses `vefa=incluses` figées au SHA de départ du plan 07 (D45-01, C4)', () => {
	it.each(Object.keys(VENTES_DECLAREES_VEFA_AU_DEPART))('%s: the frozen entry is the served total of the list, as the page of plan 06 reads it', async (cle) => {
		const reference = section(cle).entrees;
		expect(await VENTES_DECLAREES_VEFA_AU_DEPART[cle]()).toEqual(reference);
		expect(reference).toEqual([{
			fieldId: FIELD_VENTES_ADRESSE,
			valeur: String(VENTES_DECLAREES_VEFA[cle].total),
			unite: null,
			etat: { kind: 'valeur' },
			source: { nom: SOURCE_DVF.nom, url: SOURCE_DVF.url },
			dates: [{ terme: 'Période', valeur: PERIODE_44109 }]
		}]);
	});
});

describe('lectureCommune — l’essentiel reprend les quatre repères publics (D45-05, D45-08)', () => {
	it('four reading lines in the served order, each the communeKpis objects as they are', () => {
		const kpis = communeKpis(commune, sources);
		const [essentiel] = lectureCommune(commune, sources);
		expect(essentiel.cle).toBe('essentiel');
		expect(essentiel.lignes.map((ligne) => ligne.id)).toEqual(['prix-maisons', 'prix-appartements', 'tendance', 'dpe-dominant']);
		essentiel.lignes.forEach((ligne, index) => {
			const kpi = kpis[index];
			expect(ligne.libelle).toBe(kpi.libelle);
			expect(ligne.lignes).toEqual(kpi.lignes);
			expect(ligne.volet).toEqual(kpi.volet);
			expect(ligne.source).toEqual(kpi.source);
			expect(ligne.brut).toEqual(kpi.lignes.map((item) => item.brut));
			expect(ligne.fieldIds).toEqual([...new Set(kpi.lignes.map((item) => item.fieldId))]);
		});
	});

	it('the section of the four landmarks holds one entry per served line', () => {
		const kpis = communeKpis(commune, sources);
		expect(section('commune-85191').entrees).toHaveLength(kpis.flatMap((kpi) => kpi.lignes).length);
	});
});

/** Every reading line of the commune fixture, with the page it comes from. */
function toutesLesLignes(): { page: string; ligne: LigneLecture }[] {
	return lectureCommune(commune, sources).flatMap((groupe) => groupe.lignes.map((ligne) => ({ page: 'commune-85191', ligne })));
}

function definitionDuVolet(ligne: LigneLecture): string | null {
	const entree = ligne.volet.find((item) => item.terme === 'Définition');
	return entree && 'valeur' in entree ? entree.valeur : null;
}

/** UI-026: the « Définition » of a reading line is the public definition of its field, unchanged. */
function verifierDefinition(ligne: LigneLecture) {
	expect(definitionDuVolet(ligne), ligne.id).toBe(definitionPublique(ligne.fieldIds[0] ?? null));
}

describe('lectureCommune — thèmes de la fiche commune (D45-01)', () => {
	it('one group per served theme, in the COMMUNE_THEMES order, each line the themesCommune row', () => {
		const themes = themesCommune(commune, sources);
		const [, ...groupes] = lectureCommune(commune, sources);
		const ordre: string[] = COMMUNE_THEMES.map((theme) => theme.cle);
		expect(groupes.length).toBeGreaterThan(0);
		expect(groupes.map((groupe) => groupe.cle)).toEqual(themes.map((theme) => theme.cle));
		const rangs = groupes.map((groupe) => ordre.indexOf(groupe.cle));
		expect(rangs).toEqual([...rangs].sort((a, b) => a - b));
		groupes.forEach((groupe, index) => {
			const theme = themes[index];
			expect(groupe.titre).toBe(theme.titre);
			expect(groupe.lignes.map((ligne) => ligne.id)).toEqual(theme.lignes.map((ligne) => ligne.id));
			groupe.lignes.forEach((ligne, rang) => {
				const modele = theme.lignes[rang];
				expect(ligne.lignes).toEqual([modele]);
				expect(ligne.volet).toEqual(modele.volet);
				expect(ligne.brut).toEqual(modele.brut);
				expect(ligne.source).toEqual(modele.source);
			});
		});
	});
});

describe('UI-026 — définitions publiques conservées', () => {
	it('every reading line keeps the public definition of its field_id', () => {
		const lignes = toutesLesLignes();
		expect(lignes.length).toBeGreaterThan(20);
		for (const { ligne } of lignes) verifierDefinition(ligne);
	});

	const SENTINELLES: Record<string, RegExp> = {
		'revenus.revenu_median': /avant redistribution/,
		'dpe.annee_construction_mediane': /ayant fait l'objet d'un DPE/,
		'connectivite.fibre_pct': /immeubles/
	};

	it.each(Object.entries(SENTINELLES))('%s keeps its meaning on the commune 85191', (fieldId, sens) => {
		const ligne = toutesLesLignes().find((item) => item.page === 'commune-85191' && item.ligne.fieldIds.includes(fieldId))?.ligne;
		expect(ligne, fieldId).toBeDefined();
		verifierDefinition(ligne!);
		expect(definitionDuVolet(ligne!)).toMatch(sens);
	});

	it('a definition replaced by the mock-ups’ « Niveau de vie médian » is caught', () => {
		const ligne = toutesLesLignes().find((item) => item.ligne.fieldIds.includes('revenus.revenu_median'))!.ligne;
		const maquette: LigneLecture = {
			...ligne,
			volet: ligne.volet.map((entree) => (entree.terme === 'Définition' ? { terme: 'Définition', valeur: 'Niveau de vie médian' } : entree))
		};
		expect(() => verifierDefinition(maquette)).toThrow();
	});
});

describe('UI-028 — états typés et causes d’absence conservés', () => {
	function texteDuVolet(ligne: LigneLecture): string {
		const { container, unmount } = render(SourcePanel, { titre: ligne.libelle, entrees: ligne.volet, brut: ligne.brut, source: ligne.source });
		const texte = container.textContent ?? '';
		unmount();
		return texte;
	}

	it('the unpublished trend keeps not_available and its own reason, never « not reported »', () => {
		const tendance = lectureCommune(commune, sources)[0].lignes.find((ligne) => ligne.id === 'tendance')!;
		expect(tendance.lignes.length).toBeGreaterThan(0);
		for (const item of tendance.lignes) {
			expect(item.etat).toMatchObject({ kind: 'absence', etat: 'not_available', motif: TENDANCE_NON_PUBLIEE });
		}
		expect(tendance.brut.map((valeur) => valeur.etat)).toEqual(
			tendance.lignes.map(() => ({ kind: 'absence', etat: 'not_available', motif: TENDANCE_NON_PUBLIEE }))
		);
		const texte = texteDuVolet(tendance);
		expect(texte).toContain(LIBELLES_ETATS.not_available);
		expect(texte).not.toContain(LIBELLES_ETATS.not_reported);
	});

	it('a served not_available row of the commune themes stays « Non publiée »', () => {
		const ligne = lectureCommune(commune, sources)
			.flatMap((groupe) => groupe.lignes)
			.find((item) => item.brut.some((valeur) => valeur.etat.kind === 'absence' && valeur.etat.etat === 'not_available' && item.id !== 'tendance'))!;
		expect(ligne).toBeDefined();
		const texte = texteDuVolet(ligne);
		expect(texte).toContain(LIBELLES_ETATS.not_available);
		expect(texte).not.toContain(LIBELLES_ETATS.not_reported);
	});
});

describe('D45-08 — aucun volet n’affiche une clé d’état technique', () => {
	const CLES = ['not_available', 'not_reported', 'temporary_error', 'out_of_coverage', 'not_qualified', 'not_applicable'];
	const CLE = new RegExp(String.raw`\b(?:${CLES.join('|')})\b`);

	function textesDuVolet(ligne: LigneLecture): string[] {
		return ligne.volet.flatMap((entree) =>
			'valeur' in entree
				? [entree.terme, entree.valeur]
				: [entree.terme, ...entree.absences.flatMap((absence) => [absence.libelle ?? '', absence.etat.motif, absence.etat.aide ?? '', absence.etat.libelle ?? ''])]
		);
	}

	it('no entry text of any projected panel is, or contains, a technical state key', () => {
		const lignes = toutesLesLignes();
		expect(lignes.length).toBeGreaterThan(20);
		for (const { page, ligne } of lignes) {
			for (const texte of textesDuVolet(ligne)) expect(texte, `${page} ${ligne.id}`).not.toMatch(CLE);
		}
	});

	it('no rendered panel of the commune fixture shows a technical state key', () => {
		for (const { page, ligne } of toutesLesLignes()) {
			const { container, unmount } = render(SourcePanel, { titre: ligne.libelle, entrees: ligne.volet, brut: ligne.brut, source: ligne.source });
			expect(container.textContent ?? '', `${page} ${ligne.id}`).not.toMatch(CLE);
			unmount();
		}
	});

	it('the check catches a raw key put in a panel entry', () => {
		const ligne = toutesLesLignes()[0].ligne;
		const fautive = [...textesDuVolet(ligne), 'not_reported'];
		expect(() => fautive.forEach((texte) => expect(texte).not.toMatch(CLE))).toThrow();
	});
});
