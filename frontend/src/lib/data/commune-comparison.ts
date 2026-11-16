import {
	mapCommuneToGrid,
	themesCommune,
	type CleTheme,
	type CommuneGridModel,
	type LigneTheme
} from './commune-grid';
import {
	communeKpis,
	PERIODE_ENTAMEE,
	periodeEnLettres,
	type EntreeVolet,
	type EtatAbsence,
	type EtatKpi,
	type SourceKpi,
	type ValeurServie
} from './commune-kpi';
import type {
	CelluleComparaison,
	ComparaisonCommunes,
	CommuneComparee,
	CommuneSynthese,
	CritereComparaison,
	MotifNonCalcul,
	SourceAttribution
} from '$lib/api/commune';
import { displayUnit, formatField, getFieldContract } from './formatters';
import type { DatasetHosting } from '$lib/api/admin';
import type {
	CelluleComparaisonAdresse,
	CleGroupeComparaisonAdresse,
	ColonneAdresse,
	ComparaisonAdresses,
	DesignationDpe,
	LigneComparaisonAdresse,
	MotifNonCalculAdresse,
	ValeurLieu
} from '$lib/api/adresses';
import type {
	CelluleComparaisonDocument,
	CleGroupeComparaisonDocument,
	ColonneDocument,
	ComparaisonDocuments,
	LigneComparaisonDocument,
	MotifNonCalculDocument
} from '$lib/api/documents-dpe';
import { PHRASE_CONSERVATION_ADRESSE_DECLAREE, libelleEtat } from './etats-libelles';
import { definitionPublique } from './explications';
import { CATEGORIES_COMMUNE, categoriesCommune } from './commune-categories';
import type { IdPartie } from './questions';
import { LIBELLE_EN_CLAIR, enClairChamp } from './explications-simples';
import { dateLongue, estClasse, moisAnnee, qualificatifSelection, volet } from './adresse-fiche';
import {
	LIBELLE_BAN_INDETERMINE,
	LIBELLE_RETIRE,
	PHRASE_CLE_BAN_INCONNUE,
	TITRE_ADRESSE_BAN,
	TITRE_ADRESSE_DECLAREE,
	adresseDeclaree,
	badgesDocument,
	ligneAdresseDeclaree,
	methodeProvenance,
	texteCaracteristique,
	titreDocument
} from './document-dpe';
import { distanceTransport, libelleChamp, texteChamp } from './adresse-lieu';
import { TITRE_VENTES_ADRESSE, motifServiVentesAdresse } from './adresse-ventes';
import { libelleCommune, titreAdresseA } from './recherche-lieux';

// --- D-48 / D-51: comparison composed from the API response, without any computation ---
//
// The API serves every verdict: gap, reason for no gap, `identiques`. This module only lines the
// served criteria up with the fiche of each commune (mapCommuneToGrid, themesCommune) through
// `valuePath`, the path the fiche projections give each value row. It never subtracts, compares periods or judges
// comparability: a test reads this source and refuses arithmetic on a cell value.

/** Nature of the compared objects (D-102): a comparison never mixes two natures. */
export type NatureComparaison = 'commune' | 'adresse' | 'dpe';

export interface ComparaisonColonne {
	code: string;
	nom: string;
	departement: string;
	hebergement: DatasetHosting;
	/** The commune is out of coverage: its column stays, without any value cell. */
	horsCouverture: boolean;
	/** The fiche of the commune, built by mapCommuneToGrid on the served synthesis; null without one. */
	grille: CommuneGridModel | null;
	/** Address column (04-07): link of its page, and the 13 px line « {CP} {Commune} ». */
	lien?: string;
	identite?: string;
	/** Served postal code: the discriminant of the header band when the columns' codes differ. */
	codePostal?: string | null;
	/** Served commune of an address: the discriminant when two columns share a postal code. */
	commune?: string;
	/** Document column (04-10): its badges (« Ancienne méthode », « Expiré »…), never a `warn`. */
	badges?: string[];
	/** Name in the header band when it is not `nom` (the DPE number, whole, never cut). */
	nomBande?: string;
	/** Address column (04-11): a DPE may be designated on it (the address is in coverage). */
	designable?: boolean;
	/** Address column (04-11): the DPE the visitor designated, as the header says it. */
	designation?: DesignationColonne | null;
}

/** The designated DPE of an address column (04-11, D-102): in the link only. */
export interface DesignationColonne {
	numero: string;
	/** « DPE désigné : n° {numéro} · classe {X} · {mois année} », the parts the API served. */
	texte: string;
}

export interface ComparaisonModel {
	/** `commune` when absent (the comparator of the phase 3). */
	nature?: NatureComparaison;
	colonnes: ComparaisonColonne[];
	/** Codes absent from the referential, reported as served (« Lien altéré »). */
	codesInconnus: string[];
	/** Communes only (04.1-06, D-123): overseas collectivities out of coverage, never « Lien altéré ». */
	codesHorsCouverture?: string[];
	/** What the comparator shows: the served criteria in the order of the fiche (03-10). */
	groupes: GroupeComparaison[];
	/** Served criteria no fiche row shows (population margins of the DPE…): not displayed. */
	criteresNonAffiches: string[];
	/** Addresses (04-11): designated pairs whose number is not declared at their address. */
	designationsEcartees?: { code: string; numero: string }[];
}

function colonneDe(commune: CommuneComparee, sources: SourceAttribution[] | null | undefined): ComparaisonColonne {
	return {
		code: commune.code_insee,
		nom: commune.nom,
		departement: commune.departement,
		hebergement: commune.hebergement,
		horsCouverture: commune.hebergement.etat === 'hors_couverture',
		grille: commune.synthese ? mapCommuneToGrid(commune.synthese, sources) : null
	};
}

/**
 * Present the served comparison of 2 to 4 communes (D-48, D-51): one column per commune, its fiche
 * built by mapCommuneToGrid; the served criteria joined to the fiche rows by `valuePath` and
 * grouped in the order of the fiche; gap, reason and `identiques` copied from the response. A
 * criterion whose cells do not line up with the columns is refused. Nothing is computed here.
 */
export function composeComparaison(
	reponse: ComparaisonCommunes,
	sources: SourceAttribution[] | null = null
): ComparaisonModel {
	const colonnes = reponse.communes.map(commune => colonneDe(commune, sources));
	for (const critere of reponse.criteres) {
		const alignees = critere.cellules.length === colonnes.length &&
			critere.cellules.every((cellule, position) => cellule.code_insee === colonnes[position].code);
		if (!alignees) throw new Error(`Comparison criterion out of its columns: ${critere.cle}`);
	}
	const fiches = reponse.communes.map((commune, position) =>
		ficheDeColonne(commune.synthese, sources, colonnes[position].grille));
	const { groupes, criteresNonAffiches } = groupesAffiches(reponse.criteres, colonnes, fiches, sources);
	return {
		colonnes,
		codesInconnus: [...reponse.codes_inconnus],
		codesHorsCouverture: (reponse.codes_hors_couverture ?? []).map((entree) => entree.code_insee),
		groupes,
		criteresNonAffiches
	};
}

// --- What the comparator displays (03-10, D-49): the served criteria in the order of the fiche ---
//
// A row of the comparator is a served criterion (`cle`), shown under the label of the fiche row that
// shows the same value (`themesCommune`), in the order of the fiche: the four synthesis cards
// (« Repères »), then its three categories (C1, arbitrated by Ianis on 2026-11-10: « Prix et ventes »,
// « Logement et DPE », « Vivre dans la commune », where `categoriesCommune` puts each row; the five former
// themes are no longer a grouping of the comparator). A criterion no fiche row shows is not displayed, like on the
// fiche (population margins of the DPE, quantiles of the previous window). The list of criteria of
// the dated blocks depends on the data: nothing here assumes a fixed set of rows, rows are joined
// by path.
//
// Each cell renders the SERVED value of the criterion (03-09 review: the fiche cell of three
// quantiles would show the whole dispersion) or its served state through `StateValue`; the gap
// line of columns 2 to 4 recopies the served gap, reason or verdict. Numbers are only formatted
// (`formatField`, Intl), never computed; an undeclared absence or a state the glossary does not
// know gets no state (`etat: null`, the table shows a neutral « Valeur non affichable ») and is named on the
// console (03-07 review).

export type EtatAffichable = Exclude<EtatKpi, { kind: 'absence-non-declaree' }>;

/** The line under a cell of columns 2 to 4, recopied from the served cell. */
export type LigneEcart =
	/** « +100 €/m² par rapport à {Commune 1} » */
	| { kind: 'ecart'; texte: string }
	/** « Écart non calculé · périodes différentes » (`warn`) */
	| { kind: 'motif'; texte: string }
	/** A non-numeric value: the served « identical to the reference » verdict. */
	| { kind: 'verdict'; texte: string };

export interface CelluleAffichee {
	code: string;
	/** Served value or state, as `StateValue` renders it; `null`: nothing honest to render. */
	etat: EtatAffichable | null;
	/** Period of the value (fiche wording, else the served period), as served: read by the inventory. */
	periode: string | null;
	/**
	 * Displayed wording of the period (B6, UI-014): formatted, with its role when a public projection
	 * names it, neutral otherwise. Absent: `periode` is displayed as is (communes, fiche wording).
	 */
	periodeAffichee?: string | null;
	/** Structured period status from the unique exact source attribution; null/absent means unproved. */
	naturePeriode?: NonNullable<SourceAttribution['periode']>['statut'] | null;
	source: SourceKpi | null;
	/** Source panel of the cell: the fiche row's entries, with the served state of this cell. */
	volet: EntreeVolet[];
	brut: ValeurServie[];
	/** `null` in column 1 (the reference). */
	ecart: LigneEcart | null;
	/** Row of a designated DPE (04-11), column without designation: « Aucun DPE désigné », no state. */
	sansDesignation?: boolean;
	/**
	 * R-09: the value is the one of the commune of the arrondissement (`synthese.heritages`). The mark and
	 * the word are those of the fiche row (« Commune »); `enClair` is its object (« Commune de Paris »).
	 */
	echelle?: { pictogramme: 'commune'; mot: string; enClair: string };
}

export interface LigneComparee {
	/** Served criterion key (dotted path of the value). */
	cle: string;
	/** Id of the fiche row showing this value (`LigneTheme.id`), shared by the criteria of one row. */
	ligne: string;
	fieldId: string;
	libelle: string;
	/** Display unit of the field, `null` for a count or a text. */
	unite: string | null;
	/** Served verdict: the same value, state, period, unit and definition in every column. */
	identiques: boolean;
	cellules: CelluleAffichee[];
	/** A row of the DPE designated on the addresses (04-11): worded as the document comparator does. */
	designe?: boolean;
	/**
	 * R5 (plan 04.5-13): the row belongs to a detail level of its group, rendered folded after the
	 * first-level rows; the value is the summary of that level (« Totaux annuels »).
	 */
	detail?: string;
}

export interface MetadonneesFactorisees {
	periode: string;
	nature: NonNullable<SourceAttribution['periode']>['statut'];
	source: SourceKpi;
	/** Unit of the row, null for a row without displayable unit (B6: then nothing to factorise). */
	unite: string | null;
}

/**
 * Common row metadata, only when every displayed value carries the same complete proof. Period
 * wording is never parsed: its nature comes from the structured source attribution. A row without
 * displayable unit (B6, plan 04.5-10, UI-013) is factorised for its period and source under the same
 * guards, provided no value shows a unit either.
 */
export function factoriserMetadonnees(ligne: LigneComparee): MetadonneesFactorisees | null {
	if (ligne.cellules.length < 2) return null;
	const premiere = ligne.cellules[0];
	const {
		periode: periodeReference,
		naturePeriode: natureReference,
		source: sourceReference,
		etat: etatReference
	} = premiere;
	if (!periodeReference || !natureReference || !sourceReference || etatReference?.kind !== 'valeur') return null;
	if (etatReference.unite !== ligne.unite) return null;
	const identiques = ligne.cellules.every(
		({ periode, naturePeriode, source, etat }) =>
			periode === periodeReference &&
			naturePeriode === natureReference &&
			source?.nom === sourceReference.nom &&
			source?.url === sourceReference.url &&
			etat?.kind === 'valeur' &&
			etat.unite === ligne.unite
	);
	return identiques
		? { periode: premiere.periodeAffichee ?? periodeReference, nature: natureReference, source: sourceReference, unite: ligne.unite }
		: null;
}

export type CleGroupe = 'reperes' | IdPartie | CleGroupeComparaisonAdresse | CleGroupeComparaisonDocument;

export interface GroupeComparaison {
	cle: CleGroupe;
	titre: string;
	lignes: LigneComparee[];
	/** The commune of the address, kept apart and last (CMP-01): never a datum of the place. */
	reference?: boolean;
}

/** Wording of the served reasons (UI-SPEC « Écarts »), `non_numerique` has its own verdict line. */
export const LIBELLES_MOTIFS_NON_CALCUL: Readonly<Record<Exclude<MotifNonCalcul, 'non_numerique'>, string>> = {
	periodes_differentes: 'périodes différentes',
	definitions_differentes: 'définitions différentes',
	donnee_absente: 'donnée absente',
	commune_hors_couverture: 'commune hors couverture',
	periode_inconnue: 'période inconnue',
	unites_differentes: 'unités différentes'
};

/** Fiche rows written as context whose figure is a served criterion (they carry no valuePath). */
const CHEMIN_DES_LIGNES_DE_CONTEXTE: Readonly<Record<string, string>> = {
	'dpe-population': 'indicateurs_dpe.resultat.population.total',
	'dpe-distribution': 'indicateurs_dpe.resultat.distribution.classes'
};

// `formatters.ts` has no suffix for the dated blocks' units; same mapping as commune-grid.ts.
const UNITES_BLOCS: Readonly<Record<string, string>> = { 'EUR/m2': '€/m²', percent_points: '%' };

const pluriel = new Intl.PluralRules('fr-FR');

function signaler(raison: string, cle: string) {
	console.error(`[commune-comparison] ${raison}, cellule non affichée : ${cle}`);
}

function uniteDu(fieldId: string): string | null {
	try {
		return displayUnit(fieldId) ?? UNITES_BLOCS[getFieldContract(fieldId).unit] ?? null;
	} catch {
		return null;
	}
}

interface LigneDeColonne {
	ligne: LigneTheme;
	theme: CleTheme;
	/** Path of the value the fiche row shows. */
	chemin: string;
}

/** The fiche of one column, as the comparator reads it. */
interface FicheDeColonne {
	/** Row id → row, theme and path. */
	parLigne: Map<string, LigneDeColonne>;
	/** Path → row id. */
	parChemin: Map<string, string>;
	/** Row ids of the synthesis cards, in the order of the cards. */
	reperes: string[];
	/** Row ids of each category of the fiche, in the order of the fiche (its groups, then their rows). */
	parCategorie: Map<IdPartie, string[]>;
}

const conceptTail = (id: string) => id.slice(id.indexOf(':') + 1).replace(/^context:/, '');

function ficheDeColonne(
	synthese: CommuneSynthese | null,
	sources: SourceAttribution[] | null,
	grille: CommuneGridModel | null
): FicheDeColonne | null {
	if (!synthese || !grille) return null;
	// Row id → path of its value, read from the fiche rows (`valuePath`) and their grid rows (the id);
	// first occurrence kept (the ABC zone shows in two former themes).
	const cheminDeLigne = new Map<string, string>();
	for (const theme of grille.fiche.themes) {
		const grid = grille.themes[theme.key];
		if (!grid || grid.rows.length !== theme.rows.length) {
			throw new Error(`Comparison fiche theme out of step with its grid: ${theme.key}`);
		}
		theme.rows.forEach((row, position) => {
			const id = conceptTail(grid.rows[position].concept.id);
			if (row.valuePath && !cheminDeLigne.has(id)) cheminDeLigne.set(id, row.valuePath);
		});
	}
	for (const [id, chemin] of Object.entries(CHEMIN_DES_LIGNES_DE_CONTEXTE)) cheminDeLigne.set(id, chemin);

	const parLigne = new Map<string, LigneDeColonne>();
	const parChemin = new Map<string, string>();
	const themes = themesCommune(synthese, sources, grille);
	for (const theme of themes) {
		for (const ligne of theme.lignes) {
			const chemin = cheminDeLigne.get(ligne.id);
			if (!chemin || parChemin.has(chemin)) continue;
			parLigne.set(ligne.id, { ligne, theme: theme.cle, chemin });
			parChemin.set(chemin, ligne.id);
		}
	}
	// The place of each row is the one the fiche gives it: nothing is decided here.
	const parCategorie = new Map<IdPartie, string[]>();
	for (const categorie of categoriesCommune(themes)) {
		parCategorie.set(
			categorie.id,
			categorie.groupes.flatMap((groupe) => groupe.lignes).filter((ligne) => parLigne.get(ligne.id)?.ligne === ligne).map((ligne) => ligne.id)
		);
	}
	const reperes = communeKpis(synthese, sources).flatMap(kpi => kpi.lignes).flatMap(kpiLigne => {
		// A card line reads a dated-block row (same id) or a flat column (same field).
		const trouvee = parLigne.get(kpiLigne.id)?.ligne.id ??
			[...parLigne.values()].find(({ ligne }) => ligne.fieldId === kpiLigne.fieldId && !/^(?:dvf|dpe)-/.test(ligne.id))?.ligne.id;
		return trouvee ? [trouvee] : [];
	});
	// C5 (inventory of 2026-11-10; reading priority of 2026-11-06: price, rent, DPE first): the rents are
	// landmarks of a comparison too. Their rows, as the fiche words them, come after the prices and their
	// trend and before the DPE; `groupesAffiches` shows a row once, so they leave « Prix et ventes ».
	const loyers = [...parLigne.values()].filter(({ ligne }) => CHAMPS_LOYERS.has(ligne.fieldId ?? '')).map(({ ligne }) => ligne.id);
	const premierDpe = reperes.findIndex((id) => id.startsWith('dpe-') || parLigne.get(id)?.ligne.fieldId?.startsWith('dpe.') === true);
	const reperesEtLoyers = premierDpe < 0 ? [...reperes, ...loyers] : [...reperes.slice(0, premierDpe), ...loyers, ...reperes.slice(premierDpe)];
	return { parLigne, parChemin, reperes: reperesEtLoyers, parCategorie };
}

/** The rent rows of the fiche that the comparator reads among its landmarks (C5). */
const CHAMPS_LOYERS: ReadonlySet<string> = new Set(['loyers.loyer_m2_appartement', 'loyers.loyer_m2_maison']);

/** Row id of the fiche showing `cle`: same path, else the nearest object holding it. */
function ligneDeCritere(fiche: FicheDeColonne, cle: string): string | null {
	const segments = cle.split('.');
	while (segments.length) {
		const id = fiche.parChemin.get(segments.join('.'));
		if (id) return id;
		segments.pop();
	}
	return null;
}

/** Quantiles worded for a reader, the technical name kept in brackets (03-17 T-15). */
const QUANTILES_LISIBLES: Record<string, string> = {
	'10': '1er décile (P10)',
	'50': 'médiane (P50)',
	'90': '9e décile (P90)'
};

/** Label of one criterion among several of a fiche row (the P10, P50, P90 of an interval…). */
function sousLibelle(reste: string): string | null {
	const quantile = /^p(\d{2})$/.exec(reste);
	if (quantile) return QUANTILES_LISIBLES[quantile[1]] ?? `P${quantile[1]}`;
	if (/^[A-G]$/.test(reste)) return `Classe ${reste}`;
	return null;
}

// An absence served as a value is never rendered blank (CR-02): an empty list or an empty text
// gets no state, the table shows « Valeur non affichable » and the console names the criterion.
// A served value the formatter refuses is not an outage either (03-17 C5): same neutral marker.
function valeurAffichee(fieldId: string, valeur: CelluleComparaison['valeur'], cle: string): EtatAffichable | null {
	if (valeur === null) {
		signaler('valeur servie nulle', cle);
		return null;
	}
	if (Array.isArray(valeur) && valeur.length === 0) {
		signaler('liste servie vide', cle);
		return null;
	}
	const brute = Array.isArray(valeur) ? valeur.join(', ') : valeur;
	if (typeof brute === 'string' && brute.trim() === '') {
		signaler('texte servi vide', cle);
		return null;
	}
	try {
		const ligneFormatee = formatField(fieldId, '', { state: 'value', value: brute });
		if (ligneFormatee.state !== 'value') {
			signaler('valeur refusée par le formateur', cle);
			return null;
		}
		const texte = String(ligneFormatee.value);
		if (texte.trim() === '') {
			signaler('texte servi vide', cle);
			return null;
		}
		const unite = uniteDu(fieldId);
		if (unite && texte.endsWith(` ${unite}`)) return { kind: 'valeur', texte: texte.slice(0, -unite.length - 1), unite };
		return { kind: 'valeur', texte, unite };
	} catch {
		signaler('valeur hors contrat', cle);
		return null;
	}
}

// A4 (04.7-15): the fiche withholds a served variation on a begun period; its cell, raw value and
// gap follow the fiche, never the served criterion.
function periodeEntamee(fiche: LigneTheme | null): boolean {
	return fiche?.etat.kind === 'absence' && fiche.etat.motif === PERIODE_ENTAMEE;
}

const ECART_PERIODE_ENTAMEE: LigneEcart = { kind: 'motif', texte: 'Écart non calculé · période non close' };

function etatDeCellule(
	servie: CelluleComparaison,
	fieldId: string,
	fiche: LigneTheme | null,
	colonne: ComparaisonColonne,
	cle: string
): EtatAffichable | null {
	if (servie.etat === 'value' && periodeEntamee(fiche)) return fiche!.etat as EtatAffichable;
	if (servie.etat === 'value') return valeurAffichee(fieldId, servie.valeur, cle);
	if (servie.etat === null) {
		signaler('absence non déclarée par l’API', cle);
		return null;
	}
	// The reason of the fiche row when it declares the same state; a commune out of coverage
	// carries the reason of its hosting.
	const memeEtat = fiche?.etat.kind === 'absence' && fiche.etat.etat === servie.etat ? fiche.etat : null;
	const motif = memeEtat?.motif ?? (servie.etat === 'out_of_coverage' ? colonne.hebergement.motif ?? '' : '');
	if (!libelleEtat(servie.etat, motif)) {
		signaler(`état sans libellé du glossaire (${servie.etat})`, cle);
		return null;
	}
	return { kind: 'absence', etat: servie.etat as EtatAbsence, motif, effectif: memeEtat?.effectif ?? null };
}

function voletDeCellule(fiche: LigneTheme | null, fieldId: string, etat: EtatAffichable | null): EntreeVolet[] {
	// Public definition (03-17 F66); a field without one has no « Définition » entry.
	const definition = definitionPublique(fieldId) ?? '';
	const entrees: EntreeVolet[] = fiche
		? fiche.volet.filter(entree => !('absences' in entree))
		: [[LIBELLE_EN_CLAIR, enClairChamp(fieldId) ?? ''], ['Définition', definition], ['Unité', uniteDu(fieldId) ?? '']].flatMap(([terme, texte]) =>
			texte ? [{ terme, valeur: texte }] : []);
	if (etat?.kind === 'absence') entrees.push({ terme: 'État et motif', absences: [{ libelle: null, etat }] });
	return entrees;
}

/**
 * C41: a served gap between two values the API serves as the same (equal at the contract precision,
 * C49; same unit, period and definition, `identique_a_la_reference`) reads « Identique », like a comparable non-numeric value,
 * never « 0 {unité} par rapport à … ». Nothing is compared here: the verdict is the served one.
 */
const IDENTIQUE: LigneEcart = { kind: 'verdict', texte: 'Identique' };

function ligneEcart(servie: CelluleComparaison, fieldId: string, reference: string, cle: string): LigneEcart | null {
	if (servie.ecart && servie.identique_a_la_reference === true) return IDENTIQUE;
	if (servie.ecart) {
		const contrat = (() => { try { return getFieldContract(fieldId).precision.fraction_digits; } catch { return null; } })();
		const nombre = new Intl.NumberFormat('fr-FR', { signDisplay: 'exceptZero', maximumFractionDigits: contrat ?? 3 });
		const unite = servie.ecart.unite === 'point'
			? (pluriel.select(servie.ecart.valeur) === 'one' ? 'point' : 'points')
			: uniteDu(fieldId);
		// Minus sign U+2212, not the hyphen Intl writes in fr-FR (V-B C-06, D-15 WR-04).
		const signe = nombre
			.formatToParts(servie.ecart.valeur)
			.map(part => (part.type === 'minusSign' ? '−' : part.value))
			.join('');
		const texte = [signe, unite, `par rapport à ${reference}`].filter(Boolean).join(' ');
		return { kind: 'ecart', texte };
	}
	if (servie.motif_non_calcul === 'non_numerique') {
		return { kind: 'verdict', texte: servie.identique_a_la_reference ? `Même valeur que ${reference}` : `Valeur différente de ${reference}` };
	}
	const motif = servie.motif_non_calcul ? LIBELLES_MOTIFS_NON_CALCUL[servie.motif_non_calcul] : undefined;
	if (motif) return { kind: 'motif', texte: `Écart non calculé · ${motif}` };
	signaler(`ni écart ni motif connu (${String(servie.motif_non_calcul)})`, cle);
	return null;
}

function ligneComparee(
	critere: CritereComparaison,
	ligneId: string,
	libelle: string,
	colonnes: ComparaisonColonne[],
	fiches: (FicheDeColonne | null)[],
	sources: SourceAttribution[] | null
): LigneComparee {
	const reference = colonnes[0].nom;
	const lignesFiche = critere.cellules.map((_, position) => {
		const fiche = fiches[position];
		const propre = fiche ? ligneDeCritere(fiche, critere.cle) : null;
		return propre ? fiche!.parLigne.get(propre)!.ligne : null;
	});
	return {
		cle: critere.cle,
		ligne: ligneId,
		fieldId: critere.field_id,
		libelle,
		unite: uniteDu(critere.field_id),
		identiques: critere.identiques,
		cellules: critere.cellules.map((servie, position) => {
			const colonne = colonnes[position];
			const ligneFiche = lignesFiche[position];
			const retenue = periodeEntamee(ligneFiche);
			const etat = etatDeCellule(servie, critere.field_id, ligneFiche, colonne, critere.cle);
			const valeur = etat?.kind === 'valeur';
			const source = valeur ? ligneFiche?.source ?? null : null;
			const attributions = source
				? (sources ?? []).filter(
						({ nom, url }) => nom === source.nom && (url ?? null) === source.url
					)
				: [];
			return {
				code: colonne.code,
				etat,
				periode: valeur ? ligneFiche?.periode ?? servie.periode : null,
				naturePeriode: attributions.length === 1 ? attributions[0].periode?.statut ?? null : null,
				source,
				volet: voletDeCellule(ligneFiche, critere.field_id, etat),
				brut: etat ? [{
					fieldId: critere.field_id,
					valeur: JSON.stringify(retenue ? null : servie.valeur),
					etat: etat.kind === 'absence' ? { kind: 'absence', etat: etat.etat, motif: etat.motif } : { kind: 'valeur' }
				}] : [],
				ecart: position === 0
					? null
					: retenue || periodeEntamee(lignesFiche[0])
						? ECART_PERIODE_ENTAMEE
						: ligneEcart(servie, critere.field_id, reference, critere.cle),
				// The fiche row already says whose value it is (`themesCommune`); nothing is decided here.
				...(valeur && ligneFiche?.echelle ? { echelle: { ...ligneFiche.echelle, enClair: ligneFiche.objet } } : {})
			};
		})
	};
}

/** The served criteria grouped and ordered like the fiche; those no fiche row shows, apart. */
function groupesAffiches(
	criteres: CritereComparaison[],
	colonnes: ComparaisonColonne[],
	fiches: (FicheDeColonne | null)[],
	sources: SourceAttribution[] | null
): { groupes: GroupeComparaison[]; criteresNonAffiches: string[] } {
	// Row id of each criterion, read in the first column whose fiche shows it. A fiche whose row does
	// not name the criterion (a whole block absent, UI-032) leaves it to the next column.
	const parLigne = new Map<string, { critere: CritereComparaison; libelle: string }[]>();
	const criteresNonAffiches: string[] = [];
	for (const critere of criteres) {
		let place = false;
		for (const fiche of fiches) {
			const id = fiche ? ligneDeCritere(fiche, critere.cle) : null;
			if (!fiche || !id) continue;
			const { ligne, chemin } = fiche.parLigne.get(id)!;
			const sous = critere.cle === chemin ? '' : sousLibelle(critere.cle.slice(chemin.length + 1));
			if (sous === null) continue;
			const libelle = sous ? `${ligne.libelle} · ${sous}` : ligne.libelle;
			parLigne.set(id, [...(parLigne.get(id) ?? []), { critere, libelle }]);
			place = true;
			break;
		}
		if (!place) criteresNonAffiches.push(critere.cle);
	}

	const vus = new Set<string>();
	const ordre = (ids: string[]) => ids.filter(id => parLigne.has(id) && !vus.has(id) && (vus.add(id), true));
	const presentes = fiches.filter((fiche): fiche is FicheDeColonne => fiche !== null);
	const sequences: { cle: CleGroupe; titre: string; ids: string[] }[] = [
		{ cle: 'reperes', titre: 'Repères', ids: ordre(presentes.flatMap(fiche => fiche.reperes)) },
		...CATEGORIES_COMMUNE.map(categorie => ({
			cle: categorie.id,
			titre: categorie.titre,
			ids: ordre(presentes.flatMap(fiche => fiche.parCategorie.get(categorie.id) ?? []))
		}))
	];
	const groupes = sequences
		.map(({ cle, titre, ids }) => ({
			cle,
			titre,
			lignes: ids.flatMap(id => parLigne.get(id)!.map(({ critere, libelle }) =>
				ligneComparee(critere, id, libelle, colonnes, fiches, sources)))
		}))
		.filter(groupe => groupe.lignes.length > 0);
	return { groupes, criteresNonAffiches };
}

// --- Addresses (04-07, CMP-01, D-116): the groups served by the API, rendered as served ----------
//
// The API serves the four groups in their order (the commune of the address last), each cell being
// the value of the address page with its gap, reason or verdict. This part only words them: the
// value text of the page (`texteChamp`, `moisAnnee`, the qualifier of ①), the served gap formatted,
// the served verdict « Identique » / « Différent ». Nothing is compared or recomputed here.

const NB_ADRESSE = ' ';

// Type guards of a served value, for its wording only: nothing is compared or computed with it.
const texteServi = (servie: unknown): string | null => (typeof servie === 'string' ? servie : null);
const estNombre = (servie: unknown): servie is number => typeof servie === 'number';
const sansBooleen = <T>(servie: T | boolean): T | null => (typeof servie === 'boolean' ? null : servie);

/** Wording of the served reasons for addresses (UI-SPEC « Écarts (ajouts) »). */
export const LIBELLES_MOTIFS_ADRESSE: Readonly<Record<Exclude<MotifNonCalculAdresse, 'non_numerique'>, string>> = {
	adresse_hors_couverture: 'adresse hors couverture',
	// Reasons of the documents (04-10), served on the rows of a designated DPE (04-11).
	methodes_differentes: 'méthodes de calcul différentes',
	// Derived from the method wording of the document page, confirmed (04-DECISIONS-TEXTES row 62).
	methode_indeterminee: 'méthode non déterminée',
	// Derived from « Adresse déclarée non affichée », confirmed (04-DECISIONS-TEXTES row 62).
	adresse_declaree_non_affichee: 'adresse déclarée non affichée',
	// D-15 API WR-04: a list of zones read in part gets no verdict; the reserve « Liste incomplète »
	// of the address page, in lower case after « Écart non calculé · ».
	liste_incomplete: 'liste incomplète',
	periodes_differentes: LIBELLES_MOTIFS_NON_CALCUL.periodes_differentes,
	definitions_differentes: LIBELLES_MOTIFS_NON_CALCUL.definitions_differentes,
	donnee_absente: LIBELLES_MOTIFS_NON_CALCUL.donnee_absente,
	periode_inconnue: LIBELLES_MOTIFS_NON_CALCUL.periode_inconnue,
	unites_differentes: LIBELLES_MOTIFS_NON_CALCUL.unites_differentes
};

/** The served verdict of a comparable non-numeric value (UI-SPEC « Écarts (ajouts) »). */
export const VERDICTS_ADRESSE = { identique: 'Identique', different: 'Différent' } as const;

/**
 * Row labels by served key: the UI-SPEC names of the rows (« /comparer », groups of addresses), or
 * the label of the same value on the address page. A row the API serves under another key is shown
 * under the label of its field on the page, never under its key.
 */
const LIBELLES_LIGNES_ADRESSE: Readonly<Record<string, string>> = {
	dpe_declares: 'DPE déclarés',
	dpe_plus_recent: 'Le plus récent',
	ventes_parcelle: 'Ventes sur la parcelle',
	derniere_vente: 'Dernière vente',
	// C40: the title of the section of the address page (04-DECISIONS-TEXTES row 69).
	ventes_declarees: TITRE_VENTES_ADRESSE,
	hauteur: 'Hauteur du bâtiment',
	argiles: 'Argiles au point',
	arret_proche: 'Arrêt le plus proche',
	prix_median_maison: 'Prix médian dans la commune · Maisons',
	prix_median_appartement: 'Prix médian dans la commune · Appartements'
};

/** A served row without a wording here (a key added by a later API): neutral, never its key. */
const LIBELLE_LIGNE_INCONNUE = 'Valeur non affichable';

/** Objects of the source panel, as on the address page (« Volet ⓘ »). */
const OBJETS_ADRESSE: Readonly<Record<CleGroupeComparaisonAdresse, string>> = {
	documents: 'Document DPE',
	ventes: 'Vente',
	lieu: 'Point',
	commune_reference: 'Commune (référence)'
};

/** Header of an address column: « {numéro} {voie} », else « Adresse à {Commune} ({dép.}) ». */
function nomAdresse(colonne: ColonneAdresse): string {
	if (colonne.numero_voie) return colonne.numero_voie;
	return titreAdresseA(colonne.commune.nom ?? colonne.commune.code_insee, colonne.departement);
}

/**
 * Header of a designated DPE (UI-SPEC « Désignation »): « DPE désigné : n° {numéro} · classe {X} ·
 * {mois année} », with the parts the API served only. A designation that could not be read keeps
 * its number, « (détails indisponibles) » (04-DECISIONS-TEXTES row 65); its rows say the outage.
 */
export function texteDesignation(designation: DesignationDpe): string {
	if (designation.etat !== 'designe') {
		return `DPE désigné${NB_ADRESSE}: n°${NB_ADRESSE}${designation.numero} (détails indisponibles)`;
	}
	const classe = estClasse(designation.classe_energie) ? `classe ${designation.classe_energie}` : null;
	return [`DPE désigné${NB_ADRESSE}: n°${NB_ADRESSE}${designation.numero}`, classe, moisAnnee(designation.date_etablissement)]
		.filter(Boolean)
		.join(' · ');
}

function colonneAdresse(colonne: ColonneAdresse): ComparaisonColonne {
	const horsCouverture = colonne.couverture.etat === 'hors_couverture';
	return {
		code: colonne.ban_id,
		nom: nomAdresse(colonne),
		departement: colonne.departement.code,
		hebergement: horsCouverture
			? { etat: 'hors_couverture', motif: colonne.couverture.motif, departements: [] }
			: { etat: 'heberge', motif: null, departements: [] },
		horsCouverture,
		grille: null,
		lien: `/adresse/${encodeURIComponent(colonne.ban_id)}`,
		identite: libelleCommune(colonne.code_postal, colonne.commune.nom ?? colonne.commune.code_insee),
		codePostal: colonne.code_postal,
		commune: colonne.commune.nom ?? colonne.commune.code_insee,
		designable: !horsCouverture,
		designation: colonne.designation ? { numero: colonne.designation.numero, texte: texteDesignation(colonne.designation) } : null
	};
}

/** Value text of one served cell, as the address page writes the same value. */
function texteAdresse(ligne: LigneComparaisonAdresse, cellule: CelluleComparaisonAdresse): string | null {
	if (cellule.etat !== 'value') return null;
	switch (ligne.cle) {
		case 'dpe_plus_recent': {
			const selection = cellule.selection;
			const mois = moisAnnee(texteServi(cellule.valeur));
			if (!selection || !mois) return null;
			if (selection.etat === 'egalite') {
				// A tie names no document (D-109): the wording of ① on the page.
				return `${selection.documents_ex_aequo.length} documents à la même date · ${mois}`;
			}
			const classe = selection.document?.etiquette_dpe;
			const qualificatif = qualificatifSelection(selection.motif);
			return [estClasse(classe) ? `Classe ${classe}` : null, mois, qualificatif].filter(Boolean).join(' · ');
		}
		case 'derniere_vente':
			return moisAnnee(texteServi(cellule.valeur));
		case 'arret_proche': {
			const premier = cellule.elements.find((element) => element.distance_m !== null);
			if (premier && premier.distance_m !== null) return `${premier.libelle} · ${distanceTransport(premier.distance_m)}`;
			// No stop in the radius: the count and the label of the same row of the page.
			return [libelleChamp(cellule.field_id), texteChamp(cellule)].filter(Boolean).join(`${NB_ADRESSE}: `);
		}
		default:
			// The text of the same value on the page: a list by its served items, a count as a number.
			return texteChamp(cellule);
	}
}

function etatAdresse(
	ligne: LigneComparaisonAdresse,
	cellule: CelluleComparaisonAdresse,
	colonne: ComparaisonColonne
): EtatAffichable | null {
	if (cellule.etat === 'value') {
		const texte = texteAdresse(ligne, cellule);
		if (texte === null || texte.trim() === '') {
			signaler('valeur servie sans texte', `${ligne.cle}:${cellule.ban_id}`);
			return null;
		}
		const avecUnite = estNombre(cellule.valeur) && cellule.elements.length === 0 && ligne.cle !== 'arret_proche';
		return { kind: 'valeur', texte, unite: avecUnite ? displayUnit(cellule.field_id) : null };
	}
	const motif = cellule.motif ?? (cellule.etat === 'out_of_coverage' ? colonne.hebergement.motif ?? '' : '');
	if (!libelleEtat(cellule.etat, motif)) {
		signaler(`état sans libellé du glossaire (${cellule.etat})`, `${ligne.cle}:${cellule.ban_id}`);
		return null;
	}
	// C40: an address whose declared sales are not searched (suffix, number, undetermined), or to
	// which no sale is attached (C44), keeps the label and the sentence of its page (rows 70, 73),
	// never a bare state nor the glossary's « La source ne publie pas cette valeur… ».
	const decide = ligne.cle === 'ventes_declarees' && cellule.etat === 'not_available' ? motifServiVentesAdresse(motif) : null;
	// A reason without wording (Codex reviews of 04.5-08): its neutral provisional label, named on the
	// console, never the glossary's « Non publiée », its sentence nor another reason. The served
	// absence stays typed, with its reason and its raw data (D45-01, D45-08).
	if (decide?.provisoire) {
		signaler(`motif sans libellé (${motif})`, `${ligne.cle}:${cellule.ban_id}`);
		return { kind: 'absence', etat: cellule.etat as EtatAbsence, motif, effectif: null, libelle: decide.libelle, provisoire: true };
	}
	return {
		kind: 'absence',
		etat: cellule.etat as EtatAbsence,
		motif,
		effectif: null,
		...(decide?.phrase ? { libelle: decide.libelle, aide: decide.phrase } : {})
	};
}

// --- B6 (plan 04.5-10, UI-014): the dates of the address cells, with their role ------------------
//
// A served period may be a measured period or, by the C14/C24 fallback, the day of the last import:
// the API does not serve which one. A role is written only when a public projection already names it
// for this field_id; any other period is formatted under the neutral « Période : » (the term of the
// Source panel). Formatting reads the FORM of the served text (an ISO day, a window of days, a quarter),
// never its meaning; any other label is kept as served. The panel keeps the served form.

/** Closed table field_id → wording of the role of its served date, each from a public projection. */
export const ROLES_DATES: Readonly<Record<string, string>> = {
	// C14 (04-DECISIONS-COLONNES-DPE): a value served without period carries the day of the last import,
	// which is what `adresse.dpe.nombre_declares` serves. Wording proposed by plan 04.5-10, to be read by
	// Ianis (R10).
	'adresse.dpe.nombre_declares': 'Corpus importé le'
};

/** A value served without period: neutral, no cause invented (counter-review I2, R10). */
export const PERIODE_NON_RENSEIGNEE = 'Période non renseignée';

const JOUR_ISO = /^\d{4}-\d{2}-\d{2}$/;
const TRIMESTRE = /^(\d{4})[-_]T([1-4])$/;

/** A served period in words: an ISO day, a window of days, a quarter; any other label as served. */
function periodeEnClair(periode: string): string {
	if (JOUR_ISO.test(periode)) return dateLongue(periode) ?? periode;
	const trimestre = periode.match(TRIMESTRE);
	if (trimestre) return `${trimestre[2] === '1' ? '1er' : `${trimestre[2]}e`} trimestre ${trimestre[1]}`;
	return periodeEnLettres(periode);
}

/** Displayed period of a served address value: its role and formatted date, or a neutral wording. */
function periodeQualifiee(fieldId: string, periode: string | null): string {
	if (!periode) return PERIODE_NON_RENSEIGNEE;
	const role = Object.hasOwn(ROLES_DATES, fieldId) ? ROLES_DATES[fieldId] : null;
	const jour = role && JOUR_ISO.test(periode) ? dateLongue(periode) : null;
	return role && jour ? `${role} ${jour}` : `Période${NB_ADRESSE}: ${periodeEnClair(periode)}`;
}

/** Units of a served gap that are not the unit of the field (the distance of the nearest stop). */
const UNITES_ECARTS_ADRESSE: Readonly<Record<string, string>> = { metre: 'm' };

function ligneEcartAdresse(cellule: CelluleComparaisonAdresse, fieldId: string, reference: string, cle: string): LigneEcart | null {
	if (cellule.ecart && cellule.identique_a_la_reference === true) return IDENTIQUE;
	if (cellule.ecart) {
		const contrat = (() => { try { return getFieldContract(fieldId).precision.fraction_digits; } catch { return null; } })();
		const nombre = new Intl.NumberFormat('fr-FR', { signDisplay: 'exceptZero', maximumFractionDigits: contrat ?? 3 });
		const unite = UNITES_ECARTS_ADRESSE[cellule.ecart.unite] ?? uniteDu(fieldId);
		// Minus sign U+2212, as the commune comparator writes it (V-B C-06, D-15 WR-04).
		const signe = nombre
			.formatToParts(cellule.ecart.valeur)
			.map(part => (part.type === 'minusSign' ? '−' : part.value))
			.join('');
		return { kind: 'ecart', texte: [signe, unite, `par rapport à ${reference}`].filter(Boolean).join(' ') };
	}
	if (cellule.verdict) return { kind: 'verdict', texte: VERDICTS_ADRESSE[cellule.verdict] };
	const motif = cellule.motif_non_calcul && cellule.motif_non_calcul !== 'non_numerique'
		? LIBELLES_MOTIFS_ADRESSE[cellule.motif_non_calcul]
		: undefined;
	if (motif) return { kind: 'motif', texte: `Écart non calculé · ${motif}` };
	signaler(`ni écart, ni motif, ni verdict connu (${String(cellule.motif_non_calcul)})`, cle);
	return null;
}

/** Key prefix of the rows of the designated documents, as the API serves them (04-11). */
const PREFIXE_DESIGNE = 'designe_';
/** Served absence of a column without designation on those rows: not a state of a datum. */
const MOTIF_AUCUN_DPE_DESIGNE = 'aucun_dpe_designe';

/**
 * A row of the DPE designated on the addresses (04-11, R06-09/7): the API serves it from the
 * projection of the document comparator, so it is worded by the same function (`ligneDocument`):
 * same value text, same gap line, same reason, the gap read against « DPE n° {numéro} » of column
 * 1. A column without designation says « Aucun DPE désigné », without a state nor a gap.
 */
function ligneDesignee(ligne: LigneComparaisonAdresse, colonnes: ComparaisonColonne[]): LigneComparee {
	const cle = ligne.cle.startsWith(PREFIXE_DESIGNE) ? ligne.cle.slice(PREFIXE_DESIGNE.length) : ligne.cle;
	const reference = colonnes[0].designation ? titreDocument(colonnes[0].designation.numero) : colonnes[0].nom;
	const document = ligneDocument(
		{
			cle,
			field_id: ligne.field_id,
			identiques: ligne.identiques,
			cellules: ligne.cellules.map((cellule) => ({
				...cellule,
				valeur: sansBooleen(cellule.valeur),
				numero: cellule.ban_id,
				motif_non_calcul: cellule.motif_non_calcul as MotifNonCalculDocument | null
			}))
		},
		colonnes,
		reference
	);
	return {
		...document,
		cle: ligne.cle,
		ligne: ligne.cle,
		designe: true,
		cellules: document.cellules.map((cellule, position) => {
			const servie = ligne.cellules[position];
			if (servie.etat !== 'not_applicable' || servie.motif !== MOTIF_AUCUN_DPE_DESIGNE) return cellule;
			return { code: cellule.code, etat: null, periode: null, source: null, volet: [], brut: [], ecart: null, sansDesignation: true };
		})
	};
}

function ligneAdresse(
	groupe: CleGroupeComparaisonAdresse,
	ligne: LigneComparaisonAdresse,
	colonnes: ComparaisonColonne[]
): LigneComparee {
	if (ligne.designe) return ligneDesignee(ligne, colonnes);
	const reference = colonnes[0].nom;
	const libelle = LIBELLES_LIGNES_ADRESSE[ligne.cle] ?? libelleChamp(ligne.field_id) ?? LIBELLE_LIGNE_INCONNUE;
	return {
		cle: ligne.cle,
		ligne: ligne.cle,
		fieldId: ligne.field_id,
		libelle,
		unite: ligne.cle === 'arret_proche' ? null : uniteDu(ligne.field_id),
		identiques: ligne.identiques,
		cellules: ligne.cellules.map((cellule, position) => {
			const colonne = colonnes[position];
			const etat = etatAdresse(ligne, cellule, colonne);
			const valeur = etat?.kind === 'valeur';
			const brut: ValeurServie = {
				fieldId: cellule.field_id,
				valeur: valeur ? JSON.stringify(cellule.valeur) : 'null',
				etat: etat?.kind === 'absence' ? { kind: 'absence', etat: etat.etat, motif: etat.motif } : { kind: 'valeur' }
			};
			return {
				code: colonne.code,
				etat,
				periode: valeur ? cellule.periode : null,
				periodeAffichee: valeur ? periodeQualifiee(cellule.field_id, cellule.periode) : null,
				source: valeur && cellule.source ? { nom: cellule.source.nom, url: cellule.source.url } : null,
				volet: volet({
					fieldId: cellule.field_id,
					objet: OBJETS_ADRESSE[groupe],
					valeur: { ...cellule, valeur: sansBooleen(cellule.valeur) } as ValeurLieu,
					lignes: etat ? [{ id: ligne.cle, fieldId: cellule.field_id, libelle, etat, effectif: null, fenetre: null, brut }] : [],
					periode: cellule.periode
				}),
				brut: etat ? [brut] : [],
				ecart: position === 0 ? null : ligneEcartAdresse(cellule, ligne.field_id, reference, ligne.cle)
			};
		})
	};
}

/**
 * Present the served comparison of 2 to 4 addresses (04-07, CMP-01, D-116): one column per address,
 * the four served groups in the served order, the commune of the address last and flagged as a
 * reference. A row whose cells do not line up with the columns is refused. Nothing is computed.
 */
export function composeComparaisonAdresses(reponse: ComparaisonAdresses): ComparaisonModel {
	const colonnes = reponse.colonnes.map(colonneAdresse);
	const groupes: GroupeComparaison[] = reponse.groupes.map((groupe) => {
		for (const ligne of groupe.lignes) {
			const alignees = ligne.cellules.length === colonnes.length &&
				ligne.cellules.every((cellule, position) => cellule.ban_id === colonnes[position].code);
			if (!alignees) throw new Error(`Comparison row out of its columns: ${ligne.cle}`);
		}
		return {
			cle: groupe.cle,
			titre: groupe.titre,
			lignes: groupe.lignes.map((ligne) => ligneAdresse(groupe.cle, ligne, colonnes)),
			reference: groupe.cle === 'commune_reference'
		};
	});
	return {
		nature: 'adresse',
		colonnes,
		codesInconnus: [...reponse.ids_ecartes],
		groupes,
		criteresNonAffiches: [],
		designationsEcartees: reponse.designations_ecartees.map((paire) => ({ code: paire.ban_id, numero: paire.numero }))
	};
}

// --- DPE documents (04-10, CMP-02, D-116): the groups served by the API, rendered as served -------
//
// The API serves four groups (« Étiquettes », « Logement décrit », « Coût et validité », « Adresse »),
// each cell being the value of the document page with its gap, reason or verdict; a document of
// before July 2021 and one of after get « méthodes de calcul différentes » on their classes and
// figures. This part only words them as the document page does: nothing is compared, divided or
// recomputed here. Values ① ② (R5, plan 04.5-13): the per-m² measures published by the ADEME first,
// the yearly totals after them, each under its own field_id and with its true unit.

/** Wording of the served reasons for documents (UI-SPEC « Écarts (ajouts) »; two derived, 04-10). */
export const LIBELLES_MOTIFS_DOCUMENT: Readonly<Record<Exclude<MotifNonCalculDocument, 'non_numerique'>, string>> = {
	// One wording for both comparators (04-11: the rows of a designated DPE use it too).
	methodes_differentes: LIBELLES_MOTIFS_ADRESSE.methodes_differentes,
	methode_indeterminee: LIBELLES_MOTIFS_ADRESSE.methode_indeterminee,
	adresse_declaree_non_affichee: LIBELLES_MOTIFS_ADRESSE.adresse_declaree_non_affichee,
	periodes_differentes: LIBELLES_MOTIFS_NON_CALCUL.periodes_differentes,
	definitions_differentes: LIBELLES_MOTIFS_NON_CALCUL.definitions_differentes,
	donnee_absente: LIBELLES_MOTIFS_NON_CALCUL.donnee_absente,
	periode_inconnue: LIBELLES_MOTIFS_NON_CALCUL.periode_inconnue,
	unites_differentes: LIBELLES_MOTIFS_NON_CALCUL.unites_differentes
};

/**
 * Row labels by served key: the UI-SPEC names of the rows (« Groupes de lignes — documents DPE »),
 * or the label of the same value on the document page.
 */
const LIBELLES_LIGNES_DOCUMENT: Readonly<Record<string, string>> = {
	classe_energie: 'Classe énergie',
	consommation_m2: 'Consommation par m²',
	classe_climat: 'Classe climat',
	emissions_m2: 'Émissions par m²',
	// The labels of the same totals on the document page (plan 04.5-12, report 4.1-12).
	consommation: "Consommation annuelle d'énergie primaire",
	emissions: 'Émissions annuelles de gaz à effet de serre',
	type_batiment: 'Type de bâtiment',
	surface: 'Surface habitable',
	periode_construction: 'Période de construction',
	energie_chauffage: 'Énergie de chauffage',
	cout_annuel: 'Coût annuel estimé des énergies',
	etabli_le: 'Établi le',
	valable_jusqu_au: "Valable jusqu'au",
	methode: 'Méthode',
	adresse_declaree: TITRE_ADRESSE_DECLAREE,
	adresse_ban: TITRE_ADRESSE_BAN
};

/**
 * R5 (plan 04.5-13, « totaux en détail »): the yearly totals of a document, read after the per-m²
 * measures in a detail level of the table; identified by their field, direct and designated alike.
 */
const CHAMPS_DETAIL_TOTAUX: ReadonlySet<string> = new Set(['dpe.document.conso_ep', 'dpe.document.emissions_ges']);
const DETAIL_TOTAUX = 'Totaux annuels';

/** Rows whose served figure is formatted with its unit (per-m² measures, yearly totals, cost, surface). */
const LIGNES_CHIFFREES = new Set(['consommation_m2', 'emissions_m2', 'consommation', 'emissions', 'cout_annuel', 'surface']);

function colonneDocument(colonne: ColonneDocument): ComparaisonColonne {
	const badges = badgesDocument(colonne.methode, colonne.validite);
	if (colonne.disponibilite === 'retire') badges.push(LIBELLE_RETIRE);
	return {
		code: colonne.numero,
		nom: titreDocument(colonne.numero),
		nomBande: colonne.numero,
		departement: '',
		hebergement: { etat: 'heberge', motif: null, departements: [] },
		horsCouverture: false,
		grille: null,
		lien: `/dpe/${encodeURIComponent(colonne.numero)}`,
		identite: ligneAdresseDeclaree(colonne.adresse_declaree) ?? undefined,
		badges
	};
}

const METHODES_SERVIES: readonly string[] = ['ancienne', 'actuelle'];

/** Value text of one served cell, as the document page writes the same value. */
function texteDocument(ligne: LigneComparaisonDocument, cellule: CelluleComparaisonDocument): string | null {
	switch (ligne.cle) {
		case 'etabli_le':
		case 'valable_jusqu_au':
			return dateLongue(texteServi(cellule.valeur));
		case 'methode': {
			const methode = texteServi(cellule.valeur);
			return methode && METHODES_SERVIES.includes(methode) ? methodeProvenance(methode as 'ancienne' | 'actuelle') : null;
		}
		case 'adresse_declaree': {
			const declaree = adresseDeclaree({ etat: 'publiee', texte: texteServi(cellule.valeur), motif: null });
			return declaree.kind === 'publiee' ? declaree.texte : null;
		}
		default:
			return texteCaracteristique(cellule);
	}
}

function etatDocument(ligne: LigneComparaisonDocument, cellule: CelluleComparaisonDocument): EtatAffichable | null {
	const cle = `${ligne.cle}:${cellule.numero}`;
	if (cellule.etat === 'value') {
		if (LIGNES_CHIFFREES.has(ligne.cle)) return valeurAffichee(cellule.field_id, cellule.valeur, cle);
		const texte = texteDocument(ligne, cellule);
		if (texte === null || texte.trim() === '') {
			signaler('valeur servie sans texte', cle);
			return null;
		}
		return { kind: 'valeur', texte, unite: null };
	}
	// A masked declared address is worded as on the document page, never « Secret statistique ».
	if (ligne.cle === 'adresse_declaree' && cellule.etat === 'suppressed') {
		const declaree = adresseDeclaree({ etat: 'masquee', texte: null, motif: cellule.motif });
		return declaree.kind === 'etat' ? declaree.etat : { kind: 'valeur', texte: declaree.texte, unite: null };
	}
	// The BAN address retained by the source, worded as on the document page (owner, rows 9, 10).
	if (ligne.cle === 'adresse_ban' && cellule.etat === 'not_available') {
		if (cellule.motif === 'cle_inconnue') return { kind: 'valeur', texte: PHRASE_CLE_BAN_INCONNUE, unite: null };
		if (cellule.motif === 'adresse_ban_indeterminee') return { kind: 'valeur', texte: LIBELLE_BAN_INDETERMINE, unite: null };
	}
	const motif = cellule.motif ?? '';
	if (!libelleEtat(cellule.etat, motif)) {
		signaler(`état sans libellé du glossaire (${cellule.etat})`, cle);
		return null;
	}
	return { kind: 'absence', etat: cellule.etat as EtatAbsence, motif, effectif: null };
}

function ligneEcartDocument(cellule: CelluleComparaisonDocument, fieldId: string, reference: string, cle: string): LigneEcart | null {
	if (cellule.ecart && cellule.identique_a_la_reference === true) return IDENTIQUE;
	if (cellule.ecart) {
		const contrat = (() => { try { return getFieldContract(fieldId).precision.fraction_digits; } catch { return null; } })();
		const nombre = new Intl.NumberFormat('fr-FR', { signDisplay: 'exceptZero', maximumFractionDigits: contrat ?? 3 });
		// Minus sign U+2212, as the other comparators write it (V-B C-06, D-15 WR-04).
		const signe = nombre
			.formatToParts(cellule.ecart.valeur)
			.map(part => (part.type === 'minusSign' ? '−' : part.value))
			.join('');
		return { kind: 'ecart', texte: [signe, uniteDu(fieldId), `par rapport à ${reference}`].filter(Boolean).join(' ') };
	}
	if (cellule.verdict) return { kind: 'verdict', texte: VERDICTS_ADRESSE[cellule.verdict] };
	// A designated DPE (04-11) may also be read on an address out of coverage.
	const libelles: Readonly<Record<string, string>> = { ...LIBELLES_MOTIFS_ADRESSE, ...LIBELLES_MOTIFS_DOCUMENT };
	const motif = cellule.motif_non_calcul && cellule.motif_non_calcul !== 'non_numerique'
		? libelles[cellule.motif_non_calcul]
		: undefined;
	if (motif) return { kind: 'motif', texte: `Écart non calculé · ${motif}` };
	signaler(`ni écart, ni motif, ni verdict connu (${String(cellule.motif_non_calcul)})`, cle);
	return null;
}

function ligneDocument(ligne: LigneComparaisonDocument, colonnes: ComparaisonColonne[], reference = colonnes[0].nom): LigneComparee {
	const libelle = LIBELLES_LIGNES_DOCUMENT[ligne.cle] ?? LIBELLE_LIGNE_INCONNUE;
	return {
		cle: ligne.cle,
		ligne: ligne.cle,
		fieldId: ligne.field_id,
		libelle,
		unite: LIGNES_CHIFFREES.has(ligne.cle) ? uniteDu(ligne.field_id) : null,
		identiques: ligne.identiques,
		...(CHAMPS_DETAIL_TOTAUX.has(ligne.field_id) ? { detail: DETAIL_TOTAUX } : {}),
		cellules: ligne.cellules.map((cellule, position) => {
			const etat = etatDocument(ligne, cellule);
			const valeur = cellule.etat === 'value' && etat?.kind === 'valeur';
			// The panel keeps the served state: a masked address stays an absence there.
			const brut: ValeurServie = {
				fieldId: cellule.field_id,
				valeur: valeur ? JSON.stringify(cellule.valeur) : 'null',
				etat: cellule.etat === 'value' ? { kind: 'valeur' } : { kind: 'absence', etat: cellule.etat, motif: cellule.motif ?? '' }
			};
			const entrees = volet({
				fieldId: cellule.field_id,
				objet: 'Document DPE',
				valeur: cellule,
				lignes: etat?.kind === 'absence' ? [{ id: ligne.cle, fieldId: cellule.field_id, libelle, etat, effectif: null, fenetre: null, brut }] : [],
				periode: null
			});
			if (ligne.cle === 'adresse_declaree' && cellule.etat === 'suppressed') {
				entrees.push({ terme: 'Adresse déclarée non affichée', valeur: PHRASE_CONSERVATION_ADRESSE_DECLAREE });
			}
			return {
				code: colonnes[position].code,
				etat,
				periode: null,
				source: valeur && cellule.source ? { nom: cellule.source.nom, url: cellule.source.url } : null,
				volet: entrees,
				brut: etat ? [brut] : [],
				ecart: position === 0 ? null : ligneEcartDocument(cellule, ligne.field_id, reference, ligne.cle)
			};
		})
	};
}

/**
 * Present the served comparison of 2 to 4 DPE documents (04-10, CMP-02, D-116): one column per
 * document (« DPE n° {numéro} », its declared address as published, its badges), the four served
 * groups in the served order. A row whose cells do not line up with the columns is refused.
 */
export function composeComparaisonDocuments(reponse: ComparaisonDocuments): ComparaisonModel {
	const colonnes = reponse.colonnes.map(colonneDocument);
	const groupes: GroupeComparaison[] = reponse.groupes.map((groupe) => {
		for (const ligne of groupe.lignes) {
			const alignees = ligne.cellules.length === colonnes.length &&
				ligne.cellules.every((cellule, position) => cellule.numero === colonnes[position].code);
			if (!alignees) throw new Error(`Comparison row out of its columns: ${ligne.cle}`);
		}
		return { cle: groupe.cle, titre: groupe.titre, lignes: groupe.lignes.map((ligne) => ligneDocument(ligne, colonnes)) };
	});
	return { nature: 'dpe', colonnes, codesInconnus: [...reponse.numeros_ecartes], groupes, criteresNonAffiches: [] };
}
