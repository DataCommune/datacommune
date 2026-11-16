// Reading projection of the commune fiche and the DPE document page (phase 04.5, B1; D45-01, D45-05,
// D45-08). Pure module, zero Svelte imports.
//
// WHAT IT IS: a regrouping of what the public models already project — the landmarks of
// `communeKpis` and the themes of `themesCommune`. A reading
// line keeps, by reference, the displayed lines, the source panel entries, the raw served values
// and the source of the model it comes from. Nothing is computed, worded or re-sourced here: no
// number, no label, no definition, no state (D45-05).
//
// INVENTORY (D45-01): `{ fieldId, valeur, unite, etat, source, dates }` per served value, read from
// the raw value (`ValeurServie`), the source panel and the served source. `lecture.test.ts` compares
// it to the reference frozen at the starting SHA, so that a value moved by the redesign cannot
// change field, value, unit, state, source or dates unnoticed.

import type { CommuneSynthese, SourceAttribution } from '$lib/api/commune';
import type { DocumentDpePublic } from '$lib/api/documents-dpe';
import { themesCommune, type LigneTheme, type ThemeFiche } from './commune-grid';
import { TITRE_CARACTERISTIQUES, caracteristiquesDocument, reperesDocument, type Caracteristique } from './document-dpe';
import {
	communeKpis,
	type EntreeVolet,
	type KpiCommune,
	type KpiLigne,
	type SourceKpi,
	type ValeurServie
} from './commune-kpi';

/** A line as its model displays it: a landmark line (commune, document) or a theme row. */
export type LigneAffichee = KpiLigne | LigneTheme;

/** One value, or one landmark of several lines, with its source panel. */
export interface LigneLecture {
	/** Id of the model item (landmark id, theme row id). */
	id: string;
	/** Field ids of the displayed lines, in their order, each once. */
	fieldIds: string[];
	/** Label of the model item, as the model words it. */
	libelle: string;
	/** The displayed lines of the model item, the same objects. */
	lignes: readonly LigneAffichee[];
	source: SourceKpi | null;
	/** Entries of the public source panel, the same objects (D45-08). */
	volet: EntreeVolet[];
	/** Raw served values behind the item (« Voir la donnée telle que l'API la renvoie »). */
	brut: ValeurServie[];
}

export interface GroupeLecture {
	/** `essentiel`, or the key of a theme of the model. */
	cle: string;
	/** Title of the theme as the model words it; null for the essential (no new label). */
	titre: string | null;
	lignes: LigneLecture[];
}

/** One served value of the inventory: what a move must keep (D45-01). */
export interface EntreeInventaire {
	fieldId: string;
	/** Raw served value, JSON-encoded as `ValeurServie.valeur` carries it; never the formatted text. */
	valeur: string;
	/** Unit declared by the public source panel (« Unité »), null when the panel declares none. */
	unite: string | null;
	etat: ValeurServie['etat'];
	source: SourceKpi | null;
	/** Date entries of the source panel, in their exact form. */
	dates: { terme: string; valeur: string }[];
}

type Sources = SourceAttribution[] | null | undefined;

/**
 * Closed list of the date terms of the public source panels (commune-kpi.ts `volet`, commune-grid.ts
 * `voletDeLigne`, adresse-fiche.ts `volet` and `voletDocuments`). A new date term must be added here.
 */
export const TERMES_DATES: readonly string[] = ['Période', 'Dernière importation réussie'];

function fieldIdsDe(lignes: readonly { fieldId: string | null }[]): string[] {
	return [...new Set(lignes.map((ligne) => ligne.fieldId).filter((id): id is string => Boolean(id)))];
}

/** Raw served values of displayed lines. */
function brutsDe(lignes: readonly { brut: ValeurServie | null }[]): ValeurServie[] {
	return lignes.flatMap((ligne) => (ligne.brut ? [ligne.brut] : []));
}

interface ItemAPlusieursLignes {
	id: string;
	libelle: string;
	lignes: readonly KpiLigne[];
	source: SourceKpi | null;
	volet: EntreeVolet[];
}

/** A landmark (commune or document) with its own panel: one reading line. */
function ligneDuKpi(kpi: ItemAPlusieursLignes): LigneLecture {
	return {
		id: kpi.id,
		fieldIds: fieldIdsDe(kpi.lignes),
		libelle: kpi.libelle,
		lignes: kpi.lignes,
		source: kpi.source,
		volet: kpi.volet,
		brut: brutsDe(kpi.lignes)
	};
}

/** A theme row with its own panel: one reading line. */
function ligneDuTheme(ligne: LigneTheme): LigneLecture {
	return {
		id: ligne.id,
		fieldIds: fieldIdsDe([ligne]),
		libelle: ligne.libelle,
		lignes: [ligne],
		source: ligne.source,
		volet: ligne.volet,
		brut: ligne.brut
	};
}

function groupeDuTheme(theme: ThemeFiche): GroupeLecture {
	return { cle: theme.cle, titre: theme.titre, lignes: theme.lignes.map(ligneDuTheme) };
}

/** The essential of the commune: its four landmarks, in the served order of their ranks. */
function essentielCommune(kpis: KpiCommune[]): GroupeLecture {
	return { cle: 'essentiel', titre: null, lignes: [...kpis].sort((a, b) => a.rang - b.rang).map(ligneDuKpi) };
}

/**
 * Reading groups of the commune fiche, on the same answer as the page: the essential (the four
 * landmarks of `communeKpis`), then one group per theme of `themesCommune`, in the COMMUNE_THEMES
 * order (a theme without served row is absent, as on the page).
 */
export function lectureCommune(commune: CommuneSynthese, sources: Sources = null): GroupeLecture[] {
	return [essentielCommune(communeKpis(commune, sources)), ...themesCommune(commune, sources).map(groupeDuTheme)];
}

// --- the DPE document page (plan 04.5-12, C1) --------------------------------------------------

/** A row of « Ce que déclare le document » with its own panel: one reading line. */
function ligneDeCaracteristique(caracteristique: Caracteristique): LigneLecture {
	const { ligne } = caracteristique;
	return {
		id: caracteristique.fieldId,
		fieldIds: [caracteristique.fieldId],
		libelle: caracteristique.libelle,
		lignes: [ligne],
		source: caracteristique.source,
		volet: caracteristique.volet,
		brut: ligne.brut ? [ligne.brut] : []
	};
}

/**
 * Reading groups of the DPE document page, on the same answer as the page: the essential (the four
 * landmarks of `reperesDocument`, ① and ② read in the served per-m² values), then « Ce que déclare le
 * document » (`caracteristiquesDocument`, the yearly totals included). A retired document has neither.
 * The address and provenance sections carry no served value of the contract with a panel.
 */
export function lectureDocument(document: DocumentDpePublic): GroupeLecture[] {
	const reperes = reperesDocument(document);
	const caracteristiques = caracteristiquesDocument(document);
	return [
		...(reperes.length ? [{ cle: 'essentiel', titre: null, lignes: reperes.map(ligneDuKpi) }] : []),
		...(caracteristiques.length
			? [{ cle: 'caracteristiques', titre: TITRE_CARACTERISTIQUES, lignes: caracteristiques.map(ligneDeCaracteristique) }]
			: [])
	];
}

// --- inventory ---------------------------------------------------------------------------------

function dates(volet: readonly EntreeVolet[]): EntreeInventaire['dates'] {
	return volet.flatMap((entree) =>
		'valeur' in entree && TERMES_DATES.includes(entree.terme) ? [{ terme: entree.terme, valeur: entree.valeur }] : []
	);
}

function unite(volet: readonly EntreeVolet[]): string | null {
	const entree = volet.find((item) => item.terme === 'Unité');
	return entree && 'valeur' in entree ? entree.valeur : null;
}

/**
 * Inventory entries of one item: one per raw served value, with the panel and source of the item.
 */
function entrees(brut: readonly (ValeurServie | null)[], volet: readonly EntreeVolet[], source: SourceKpi | null): EntreeInventaire[] {
	return brut.flatMap((valeur) =>
		valeur
			? [{
					fieldId: valeur.fieldId,
					valeur: valeur.valeur,
					unite: unite(volet),
					etat: valeur.etat.kind === 'absence'
						? { kind: 'absence', etat: valeur.etat.etat, motif: valeur.etat.motif }
						: { kind: 'valeur' },
					source: source ? { nom: source.nom, url: source.url ?? null } : null,
					dates: dates(volet)
				}]
			: []
	);
}

/**
 * Distinct entries in a stable order: a value moved to another group, or shown twice on purpose,
 * is not a difference; a value lost or altered is.
 */
function normaliser(liste: EntreeInventaire[]): EntreeInventaire[] {
	const parCle = new Map(liste.map((entree) => [JSON.stringify(entree), entree]));
	return [...parCle.keys()].sort().map((cle) => parCle.get(cle)!);
}

/** Inventory of reading groups (the « after » path). */
export function inventaireLecture(groupes: readonly GroupeLecture[]): EntreeInventaire[] {
	return normaliser(
		groupes.flatMap((groupe) =>
			groupe.lignes.flatMap((ligne) => entrees(ligne.brut, ligne.volet, ligne.source))
		)
	);
}

/** The public models of a page, read directly (the « before » path of the inventory). */
export interface ModelesPublics {
	/** Landmarks of the commune fiche (`communeKpis`). */
	kpis?: readonly KpiCommune[];
	/** Themes of the commune fiche (`themesCommune`). */
	themes?: readonly ThemeFiche[];
}

/** Inventory of the public models themselves, without the reading projection. */
export function inventaireDesModeles(modeles: ModelesPublics): EntreeInventaire[] {
	const liste: EntreeInventaire[] = [];
	for (const kpi of modeles.kpis ?? []) liste.push(...entrees(kpi.lignes.map((ligne) => ligne.brut), kpi.volet, kpi.source));
	for (const theme of modeles.themes ?? []) {
		for (const ligne of theme.lignes) liste.push(...entrees(ligne.brut, ligne.volet, ligne.source));
	}
	return normaliser(liste);
}

// --- inventory of the comparators (plan 04.5-10, B6; counter-review B1 and I1) ------------------
//
// The comparison table is not a reading projection: its model (`composeComparaison`,
// `composeComparaisonAdresses`, `composeComparaisonDocuments`) is inventoried cell by cell. Each
// entry is identified by its answer, nature, group, row key and column code, so that a value, a
// source or a date given to another column is a difference, never an equal set. It keeps the served
// period of the cell (not its displayed wording), every date of its panel (« Période », « Dernière
// importation réussie ») and the served gap line. Entries keep the order of the model.

/** One cell of a comparison table: what the redesign of the table must keep (D45-01). */
export interface EntreeComparaison {
	reponse: string;
	nature: 'commune' | 'adresse' | 'dpe';
	groupe: string;
	ligne: string;
	colonne: string;
	fieldId: string;
	/** Raw served value, JSON-encoded as `ValeurServie.valeur` carries it; null without raw value. */
	valeur: string | null;
	/** Unit declared by the source panel of the cell (« Unité »), null when it declares none. */
	unite: string | null;
	/** Unit of the displayed value (`EtatAffichable.unite`), null for an absence or a text. */
	uniteValeur: string | null;
	/** Served state of the raw value; null when nothing honest is rendered (no raw value). */
	etat: ValeurServie['etat'] | null;
	source: SourceKpi | null;
	/** Period of the cell as the model carries it (served or fiche wording), before any display. */
	periode: string | null;
	/** Date entries of the source panel of the cell, in their exact form. */
	dates: { terme: string; valeur: string }[];
	/** Gap line of columns 2 to 4, as the model words the served gap, reason or verdict. */
	ecart: { kind: string; texte: string } | null;
	/** A row of a designated DPE on an address column without designation (04-11). */
	sansDesignation?: true;
}

/** The parts of a comparison model the inventory reads (structural: no import of the comparator). */
export interface ModeleComparaisonInventorie {
	nature?: 'commune' | 'adresse' | 'dpe';
	groupes: readonly {
		cle: string;
		lignes: readonly {
			cle: string;
			fieldId: string;
			cellules: readonly {
				code: string;
				etat: { kind: string; unite?: string | null } | null;
				periode: string | null;
				source: SourceKpi | null;
				volet: readonly EntreeVolet[];
				brut: readonly ValeurServie[];
				ecart: { kind: string; texte: string } | null;
				sansDesignation?: boolean;
			}[];
		}[];
	}[];
}

/** Inventory of a comparison model, one entry per cell, in the order of the model. */
export function inventaireComparaison(reponse: string, modele: ModeleComparaisonInventorie): EntreeComparaison[] {
	const nature = modele.nature ?? 'commune';
	return modele.groupes.flatMap((groupe) =>
		groupe.lignes.flatMap((ligne) =>
			ligne.cellules.map((cellule): EntreeComparaison => {
				const brut = cellule.brut[0] ?? null;
				return {
					reponse,
					nature,
					groupe: groupe.cle,
					ligne: ligne.cle,
					colonne: cellule.code,
					fieldId: brut?.fieldId ?? ligne.fieldId,
					valeur: brut ? brut.valeur : null,
					unite: unite(cellule.volet),
					uniteValeur: cellule.etat?.kind === 'valeur' ? (cellule.etat.unite ?? null) : null,
					etat: brut
						? brut.etat.kind === 'absence'
							? { kind: 'absence', etat: brut.etat.etat, motif: brut.etat.motif }
							: { kind: 'valeur' }
						: null,
					source: cellule.source ? { nom: cellule.source.nom, url: cellule.source.url ?? null } : null,
					periode: cellule.periode,
					dates: dates(cellule.volet),
					ecart: cellule.ecart ? { kind: cellule.ecart.kind, texte: cellule.ecart.texte } : null,
					...(cellule.sansDesignation ? { sansDesignation: true as const } : {})
				};
			})
		)
	);
}
