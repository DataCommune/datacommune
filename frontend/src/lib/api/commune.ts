import type { DatasetHosting } from './admin';
import type { DatasetProvenance } from './resolve';
import { api } from './client';

// --- Types (mirror backend ResultatCommuneSynthese — 09-RESEARCH §CONTRAT API) ---

// Reject mixtures of absence tags and complete-block payloads at this boundary.
type BlocIndicateurOuAbsence<T> =
	| (T & { [K in keyof IndicateurAbsent]?: never })
	| (IndicateurAbsent & { [K in keyof T]?: never });

/**
 * Commune synthese as served by GET /v1/commune.
 * Everything is nullable except `code_insee`.
 * Aggregates 13 datasets: demographie, revenus, zonage ABC, vacance, fiscalite, loyers,
 * plus commune-level aggregates of DVF, DPE, equipements, scolaire, monuments, foncier PM
 * and connectivite. Remaining gaps (georisques/RNB, copropriété) are degraded honestly.
 */
/** One closed calendar year of the yearly price series. */
export interface PrixAnnuel {
	annee: number;
	/** Null under `seuil_point` retained sales. */
	prix_m2_median: number | null;
	/** Real count of the retained sales; 0: none. */
	nb_ventes: number;
}

export interface TendancePrixType {
	type_bien: 'maison' | 'appartement';
	etat: 'evolution' | 'points' | 'echantillon_insuffisant';
	/** Served only in the state `evolution`. */
	evolution_pct: number | null;
	nb_ventes: number;
	annees: PrixAnnuel[];
}

/** Median price per m² per closed calendar year (migration 115), the most sold type first. */
export interface TendancePrix {
	annee_debut: number;
	annee_fin: number;
	seuil_evolution: number;
	seuil_point: number;
	types: TendancePrixType[];
}

export interface CommuneSynthese {
	code_insee: string;
	/** `arrondissement` for the 45 municipal arrondissements of Paris, Lyon and Marseille; null or absent: a commune. */
	type_zone?: 'commune' | 'arrondissement' | null;
	/** INSEE code of the commune of an arrondissement (75112 -> 75056); null for a commune. */
	commune_parent?: string | null;
	// Dated blocks or their absence, as served. A block the instance does not publish
	// (`MOTIF_BLOCS_NON_PUBLIES`) is read as null by the screens (`blocsPublies`).
	indicateurs_dvf?: BlocIndicateurOuAbsence<IndicateursDvf> | null;
	indicateurs_dpe?: BlocIndicateurOuAbsence<IndicateursDpe> | null;
	nom_commune: string | null;
	code_departement: string | null;
	code_region: string | null;
	// demographie
	population: number | null;
	nb_menages: number | null;
	nb_logements: number | null;
	nb_logements_vacants_recensement: number | null;
	densite: number | null;
	superficie: number | null;
	annee_demographie: number | null;
	// revenus
	revenu_median: number | null;
	taux_pauvrete: number | null;
	indice_gini: number | null;
	part_imposee: number | null;
	annee_revenus: number | null;
	// zonage ABC
	zone_abc: string | null;
	zone_abc_reclassement: boolean | null;
	// vacance
	taux_vacance: number | null;
	nb_vacants_plus_2ans: number | null;
	annee_vacance: number | null;
	// fiscalite
	taux_tfpb: number | null;
	taux_th_rs: number | null;
	taux_teom: number | null;
	nom_epci: string | null;
	annee_fiscalite: number | null;
	// loyers
	loyer_m2_appartement: number | null;
	loyer_m2_maison: number | null;
	annee_loyers: number | null;
	loyers_appartement?: LoyerPrediction | null;
	loyers_maison?: LoyerPrediction | null;
	// dvf (commune aggregate)
	prix_m2_median: number | null;
	nb_mutations_12m: number | null;
	/** Inclusive bounds of the flat DVF medians and counts, never the import date. */
	fenetre_mediane_dvf?: string | null;
	/**
	 * Yearly price series, or its declared absence (`MOTIF_TENDANCE_NON_PUBLIEE`: the series is not
	 * readable or holds no closed year); null when dvf is out of coverage.
	 */
	tendance_prix?: TendancePrix | IndicateurAbsent | null;
	// dvf — ventilation maison / appartement du MÊME agrégat (Phase 57, D-14). Optionnels : la
	// ventilation est additive, et une fixture antérieure à la Phase 57 reste un littéral valide.
	// La médiane vaut `null` sous le seuil de non-divulgation ; l'effectif, lui, est toujours réel.
	prix_m2_median_maison?: number | null;
	nb_mutations_12m_maison?: number | null;
	prix_m2_median_appartement?: number | null;
	nb_mutations_12m_appartement?: number | null;
	// dpe (commune aggregate)
	dpe_passoires_pct: number | null;
	dpe_median: string | null;
	annee_construction_mediane: number | null;
	// equipements / scolaire / monuments (commune aggregate)
	nb_equipements: number | null;
	annee_equipements?: number | null;
	nb_etablissements_scolaires: number | null;
	nb_monuments: number | null;
	// foncier_pm (commune aggregate)
	nb_proprietaires_pm: number | null;
	surface_pm_ha: number | null;
	// connectivite (commune aggregate)
	fibre_pct: number | null;
	// D-47 (additive, 2026-09-24): declared state of each flat column served null, keyed by the
	// column name. Omitted when no column is null. Read it through `etatDeColonne`
	// (`$lib/data/etats-colonnes`), never by defaulting a null to 0 or « Non renseigné ».
	etats?: Record<string, EtatColonne> | null;
}

/** Why a flat column of /v1/commune is served null (D-47). Never carries a value. */
export type EtatAbsenceColonne =
	| 'out_of_coverage'
	| 'insufficient_sample'
	| 'not_available'
	| 'not_reported'
	| 'zero_denominator';

/**
 * `motif`: for `out_of_coverage`, a key of `allowed_out_of_coverage_reasons` (D-24); otherwise a
 * short French sentence giving the cause (e.g. the real count under the DVF threshold).
 */
export interface EtatColonne {
	etat: EtatAbsenceColonne;
	motif: string;
}

export type IndicateurAbsent = {
	etat:
		| 'not_reported'
		| 'out_of_coverage'
		| 'insufficient_sample'
		| 'suppressed'
		| 'not_qualified'
		| 'zero_denominator';
	motif: string;
};
export type IndicateurNombre = { etat: 'value'; valeur: number };
export type NombreOuAbsence = IndicateurNombre | IndicateurAbsent;
export type QuantilesDvf = {
	etat: 'value';
	p10: number;
	p50: number;
	p90: number;
	qualification: string;
};
export type QuantilesOuAbsence = QuantilesDvf | IndicateurAbsent;
export interface FenetreIndicateur {
	debut_inclus: string;
	fin_exclue: string;
}
export interface ProvenanceIndicateur {
	nom: string;
	producteur: string;
	licence: string;
	reference: string;
	date_publication_source: string | null;
	date_snapshot: string | null;
	date_import: string | null;
	couverture: 'complete' | 'partial' | 'unknown';
}
export interface BlocIndicateur {
	version_contrat: '1.0' | '1.1';
	version_population: string;
	date_calcul: string;
	generation: string | null;
	provenance: ProvenanceIndicateur[];
}
export interface ProvenanceDvf extends ProvenanceIndicateur {
	couverture: 'unknown';
}
export interface AncrageDvfInconnu {
	etat: 'unknown';
	motif: string;
}
/** B ends the frozen received lot; retrospective revisions remain possible.
 * Evidence hashes are references, never an automatic publication decision.
 */
export interface LivraisonDvfRecue {
	etat: 'delivery_received_and_stock_bound';
	/** v2: exactly 44 and 85; v3 (04.1-10): the hosted departements received in the lot, canonical order. */
	version_qualification: 'dvf-delivery-qualification.v2' | 'dvf-delivery-qualification.v3';
	lot_id: string;
	periode_debut_inclus: string;
	periode_fin_exclue: string;
	departements: string[];
	acquisition_complete: true;
	revisions_possibles: true;
	exhaustivite_historique: 'unknown';
	generation_fournisseur: null;
	date_observation: string;
	preuve_qualification_sha256: string;
	preuve_observation_sha256: string;
	preuve_reconstruction_sha256: string;
	snapshot_sha256: string;
	contenu_projete_sha256: string;
	cog_sha256: string;
	methode_projection: 'sha256-sorted-row-digests-json.v1';
	generation_stock: string | null;
}
export type AncrageTemporelDvf = AncrageDvfInconnu | LivraisonDvfRecue;
export interface StatistiquesFenetreDvf {
	fenetre: FenetreIndicateur | null;
	motif_periode?: string | null;
	effectif: number | null;
	mediane: NombreOuAbsence;
	quantiles: QuantilesOuAbsence;
}
export interface ClasseDvf {
	precedente: StatistiquesFenetreDvf;
	courante: StatistiquesFenetreDvf;
	variation_pct: NombreOuAbsence;
	qualification_tendance: string | null;
}
export interface IndicateursDvf extends BlocIndicateur {
	version_contrat: '1.1';
	provenance: ProvenanceDvf[];
	ancrage_temporel: AncrageTemporelDvf;
	population: 'mutations_residentielles_mono_local_hors_vefa_mono_commune';
	unite_prix: 'EUR/m2';
	methode_quantiles: 'percentile_cont';
	methode_variation: '100*(mediane_courante/mediane_precedente-1)';
	residentiel_maison: ClasseDvf;
	residentiel_appartement: ClasseDvf;
}
export type EnergyClass = 'A' | 'B' | 'C' | 'D' | 'E' | 'F' | 'G';
export interface DistributionDpeIndicateur {
	classes: Record<EnergyClass, number>;
	inconnue: number;
}
export interface PopulationDpeIndicateur {
	total: number;
	classes_connues: number;
	types_batiment: Record<'maison' | 'appartement' | 'autre' | 'inconnu', number>;
	validite: Record<
		'unknown' | 'expired' | 'not_expired_by_date' | 'inconsistent_dates' | 'future_establishment',
		number
	>;
	remplacement: Record<'not_checked' | 'no_successor_known' | 'replaced' | 'ambiguous', number>;
	regimes: Record<'existant' | 'neuf' | 'inconnu', number>;
	source_inconnue: number;
	revision_non_verifiee: number;
}
export interface ResultatDpeIndicateur {
	etat: 'value';
	population: PopulationDpeIndicateur;
	distribution: DistributionDpeIndicateur;
	modes: EnergyClass[];
	mediane_ordinale: EnergyClass | null;
	part_fg_pct: NombreOuAbsence;
	date_etablissement_min: string | null;
	date_etablissement_max: string | null;
	dates_etablissement_inconnues: number;
}
export interface IndicateursDpe extends BlocIndicateur {
	version_contrat: '1.0';
	population: 'diagnostics_canoniques_disponibles_hors_traces_retirees';
	periode: 'bornes_etablissement_observees_non_exhaustives';
	date_evaluation: string;
	methode_mediane: 'percentile_disc';
	methode_modes: 'toutes_classes_frequence_maximale_ordre_A_G';
	resultat: ResultatDpeIndicateur | IndicateurAbsent;
}

/** Additive source metadata; legacy imports deliberately keep unknown fields null. */
export interface LoyerPrediction {
	code_insee: string;
	type_habitat: string;
	annee: number;
	loyer_m2: number | null;
	loyer_m2_bas: number | null;
	loyer_m2_haut: number | null;
	id_zone: string | null;
	type_prediction: string | null;
	nb_observations_commune: number | null;
	nb_observations_maille: number | null;
	r2_ajuste: number | null;
	trimestre_reference: number | null;
	charges_comprises: boolean | null;
	meuble: boolean | null;
	surface_reference_m2: number | null;
	surface_piece_reference_m2: number | null;
}

/** Source attribution returned in the envelope `meta`. */
export interface SourceAttribution {
	nom?: string;
	licence?: string;
	url?: string;
	producteur?: string;
	/** Additive provenance served by current APIs; older envelopes may omit it. */
	dataset?: string;
	periode?: DatasetProvenance['periode'];
	derniere_importation_reussie?: string | null;
}

/**
 * Envelope for GET /v1/commune.
 * `donnees` is a LIST: found → 1 element; unknown INSEE → [] (HTTP 200, NOT 404).
 * Always read `donnees[0]`.
 */
export interface ReponseApiCommune {
	statut: string;
	donnees: CommuneSynthese[];
	meta: { source: SourceAttribution[] };
}

// --- API functions ---

/**
 * Reason of a whole block the instance does not publish (rollout gate closed, todo 2026-11-01).
 * Unlike `indicator_policy_unqualified` (a block of an open gate, shown as such), it says « no
 * dated block here »: the fiche and the comparator fall back to the flat columns, as for a null
 * block, so no screen shows it as a quarantine. Applied where a screen reads the synthesis (the
 * commune page, the comparator), never to what is exported: the open JSON export serves the
 * absence as the API serves it, never a bare null.
 */
export const MOTIF_BLOCS_NON_PUBLIES = 'indicateurs_communaux_non_publies';

/** Reason of a yearly price series served absent: the page reads it as « no series ». */
export const MOTIF_TENDANCE_NON_PUBLIEE = 'tendance_prix_non_publiee';

function blocPublie<T>(bloc: T | null | undefined): T | null | undefined {
	const absence = bloc as { etat?: unknown; motif?: unknown } | null | undefined;
	return absence?.etat === 'not_qualified' && absence.motif === MOTIF_BLOCS_NON_PUBLIES ? null : bloc;
}

/** The synthesis with its unpublished blocks read as null; every other block is kept as served. */
export function blocsPublies(synthese: CommuneSynthese): CommuneSynthese {
	return {
		...synthese,
		indicateurs_dvf: blocPublie(synthese.indicateurs_dvf),
		indicateurs_dpe: blocPublie(synthese.indicateurs_dpe)
	};
}

/**
 * Fetch the commune synthese by INSEE code.
 * `code_insee` is a QUERY param (exactly 5 digits), not a path param.
 * The caller is responsible for passing an already-validated code (T-09-01).
 */
export function getCommuneSynthese(
	code: string,
	fetchImpl?: typeof fetch
): Promise<ReponseApiCommune> {
	return api<ReponseApiCommune>(`/v1/commune?code_insee=${code}`, {}, fetchImpl);
}

// --- Comparison of communes (D-48): gaps and comparability are served by the API ---

/** Why no gap is served between a column and the first one (COM-03). */
export type MotifNonCalcul =
	| 'commune_hors_couverture'
	| 'donnee_absente'
	| 'periode_inconnue'
	| 'periodes_differentes'
	| 'unites_differentes'
	| 'definitions_differentes'
	| 'non_numerique';

/** `value`, or the absence state declared by the synthesis; `null`: undeclared absence. */
export type EtatCellule =
	| 'value'
	| 'not_reported'
	| 'out_of_coverage'
	| 'insufficient_sample'
	| 'not_available'
	| 'zero_denominator'
	| 'suppressed'
	| 'not_qualified';

/** Gap to the first column, rounded by the API; `unite` is « point » for rates. */
export interface EcartComparaison {
	valeur: number;
	unite: string;
}

export interface CelluleComparaison {
	code_insee: string;
	etat: EtatCellule | null;
	valeur: number | boolean | string | string[] | null;
	periode: string | null;
	ecart: EcartComparaison | null;
	motif_non_calcul: MotifNonCalcul | null;
	/** `null` for the first column (the reference). */
	identique_a_la_reference: boolean | null;
}

export interface CritereComparaison {
	/** Dotted path of the value in a /v1/commune row (the `valuePath` of a fiche row). */
	cle: string;
	field_id: string;
	unite: string;
	identiques: boolean;
	cellules: CelluleComparaison[];
}

export interface CommuneComparee {
	code_insee: string;
	nom: string;
	departement: string;
	hebergement: DatasetHosting;
	synthese: CommuneSynthese | null;
}

export interface ComparaisonCommunes {
	communes: CommuneComparee[];
	criteres: CritereComparaison[];
	codes_inconnus: string[];
	/**
	 * Overseas collectivities (975, 977, 978, 984 to 989) out of coverage, never in `codes_inconnus`
	 * (04.1-06, D-123). Always served; optional for the frozen fixtures written before it.
	 */
	codes_hors_couverture?: CodeHorsCouverture[];
}

export interface CodeHorsCouverture {
	code_insee: string;
	hebergement: DatasetHosting;
}

export interface ReponseApiComparaison {
	statut: string;
	donnees: ComparaisonCommunes;
	meta: { source: SourceAttribution[] } | null;
}

/**
 * Compare 2 to 4 communes in one call (GET /v1/communes/comparaison). The first code is the
 * reference of the gaps. The API validates the codes (422) and serves every verdict.
 */
export function getCommuneComparaison(
	codes: readonly string[],
	fetchImpl?: typeof fetch
): Promise<ReponseApiComparaison> {
	const sp = new URLSearchParams({ codes: codes.join(',') });
	return api<ReponseApiComparaison>(`/v1/communes/comparaison?${sp}`, {}, fetchImpl).then((reponse) => ({
		...reponse,
		donnees: {
			...reponse.donnees,
			communes: reponse.donnees.communes.map((commune) => ({
				...commune,
				synthese: commune.synthese && blocsPublies(commune.synthese)
			}))
		}
	}));
}
