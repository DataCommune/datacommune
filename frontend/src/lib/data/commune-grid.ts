import type {
	CommuneSynthese,
	IndicateursDpe,
	IndicateursDvf,
	LoyerPrediction,
	NombreOuAbsence,
	ProvenanceIndicateur,
	SourceAttribution,
	StatistiquesFenetreDvf
} from '$lib/api/commune';
import type { FieldPresentation, KpiItem, KvRow, ThemeModel } from '$lib/types/fiche';
import type { FicheGrid, FicheGridCell, FicheGridColumn, FicheCriticalSummary } from '$lib/types/fiche-grid';
// Type-only: commune-kpi.ts imports this module at runtime, the reverse import is erased.
import type { EntreeVolet, EtatAbsence, EtatKpi, KpiCommune, SourceKpi, ValeurServie } from './commune-kpi';
import { mapCommuneToFiche } from './commune-fiche';
import { kpisToGrid, rowsToGrid } from './fiche-grid';
import { datasetDuField, definitionPublique, millesimesFromCommune, sourcesCommuneParDataset } from './explications';
import { LIBELLE_EN_CLAIR, enClairChamp } from './explications-simples';
import { contexteLoyer } from './loyers-prediction';
import { enrichCommuneIndicatorPresentation } from './commune-indicator-presentation';
import {
	AUCUNE_VENTE,
	flatCommunePeriod,
	LIBELLE_PERIODE_ENTAMEE,
	LIBELLE_TENDANCE,
	LIBELLES_MEDIANE,
	LIBELLES_TENDANCE,
	LIMITES_DPE,
	limitesDvf,
	METHODE_DPE,
	METHODE_PRIX,
	METHODE_TENDANCE,
	OBJET_TENDANCE,
	PERIODE_ENTAMEE,
	tendanceSansBloc,
	variationPubliable
} from './commune-indicators';
import {
	CHAMP_EVOLUTION,
	entreesDetail,
	etatTendance,
	LIMITES_TENDANCE_ANNUELLE,
	methodeTendance,
	OBJET_TENDANCE_ANNUELLE,
	periodeTendance,
	tendanceServie
} from './commune-tendance';
import { etatDeColonne, type ColonnePlate } from './etats-colonnes';
import { libelleEtat, phraseEtat, phrasePublieeAilleurs } from './etats-libelles';
import { ANCRE_ARRONDISSEMENTS, communeParente } from './arrondissements';
import { displayUnit, getFieldContract } from './formatters';

// These boundaries describe the existing source concepts, not a newly inferred absence reason.
// Sources: explications.ts (Filosofi DEC) and app/vacance/schemas.py (private-housing rate).
const INCOME_RESERVE = 'Revenu déclaré annuel par unité de consommation, avant redistribution. Ce revenu ne mesure pas le niveau de vie après impôts et prestations.';
const VACANCY_RESERVE = 'Taux de vacance du parc privé, toutes durées confondues. Le nombre de logements vacants depuis plus de deux ans est un indicateur distinct ; le dénominateur privé n’est pas fourni par cette fiche.';

export interface CommuneGridModel {
	column: FicheGridColumn;
	/** The unchanged mapper values plus explicit exportable context. */
	fiche: { kpi: KpiItem[]; themes: ThemeModel[] };
	synthese: FicheGrid;
	themes: Record<string, FicheGrid>;
}

export const INDICATOR_SUMMARY_ORDER = [
	'dvf-maison-mediane-courante', 'dvf-maison-effectif-courant',
	'dvf-appartement-mediane-courante', 'dvf-appartement-effectif-courant',
	'dvf-maison-variation', 'dvf-appartement-variation', 'dpe-mediane', 'dpe-modes'
] as const;

function qualifiedIndicatorSummary(commune: CommuneSynthese, themes: ThemeModel[]): KvRow[] | null {
	if (commune.indicateurs_dvf == null && commune.indicateurs_dpe == null) return null;
	const byId = new Map(themes.flatMap(theme => theme.rows).filter(row => row.id).map(row => [row.id!, row]));
	const selected = INDICATOR_SUMMARY_ORDER.flatMap(id => {
		const row = byId.get(id);
		return row ? [row] : [];
	});
	for (const id of ['dvf-indicateurs-absence', 'dpe-indicateurs-absence', 'dpe-resultat-absence']) {
		const row = byId.get(id);
		if (row) selected.push(row);
	}
	return selected;
}

function rentGrain(detail: LoyerPrediction | null | undefined): string {
	const grains: Record<string, string> = {
		commune: 'Commune / arrondissement', epci: 'EPCI', maille: 'Maille', maile: 'Maille'
	};
	return grains[detail?.type_prediction ?? ''] ?? 'Maille d’estimation inconnue';
}

/** Typed source fields only; no extraction from the French reservation paragraphs. */
export function criticalSummariesForCommune(commune: CommuneSynthese): Record<string, FicheCriticalSummary> {
	const rent = (detail: LoyerPrediction | null | undefined): FicheCriticalSummary => {
		if (!detail) return { facts: ['Loyer prédit'], warnings: ['Période, charges, bien type et incertitude non renseignés'] };
		const number = (value: number) => new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 20 }).format(value);
		const period = detail.trimestre_reference != null ? `T${detail.trimestre_reference} ${detail.annee}` : 'Trimestre non renseigné';
		const facts = ['Prédit', period,
			detail.charges_comprises === true ? 'charges comprises' : detail.charges_comprises === false ? 'hors charges' : 'charges inconnues',
			detail.meuble === false ? 'non meublé' : detail.meuble === true ? 'meublé' : 'ameublement inconnu',
			detail.surface_reference_m2 != null ? `${number(detail.surface_reference_m2)} m²` : 'surface type inconnue'];
		const warnings = [detail.nb_observations_commune != null ? `${number(detail.nb_observations_commune)} observations communales` : 'Effectif communal non renseigné'];
		if (detail.loyer_m2_bas == null || detail.loyer_m2_haut == null) warnings.push('incertitude non renseignée');
		else warnings.push(`intervalle à 95 % : ${number(detail.loyer_m2_bas)}–${number(detail.loyer_m2_haut)} €/m²/mois`);
		// Existing source presentation thresholds, not a new statistical qualification.
		if (detail.r2_ajuste != null && detail.r2_ajuste < 0.5) warnings.push('estimation fragile');
		return { facts, warnings };
	};
	return {
		'revenus.revenu_median': { facts: [...(commune.annee_revenus != null ? [String(commune.annee_revenus)] : []), 'Avant redistribution'], warnings: [] },
		'vacance.taux_vacance': { facts: ['Toutes durées'], warnings: [] },
		'loyers.loyer_m2_appartement': rent(commune.loyers_appartement),
		'loyers.loyer_m2_maison': rent(commune.loyers_maison)
	};
}

/** Uses only fields already loaded by the Commune route; no fetch, aggregation or comparison. */
export function mapCommuneToGrid(
	commune: CommuneSynthese,
	sources: SourceAttribution[] | null | undefined = undefined
): CommuneGridModel {
	const column: FicheGridColumn = {
		id: `commune:${commune.code_insee}`, entity: 'commune', scope: 'commune',
		grain: 'Commune', label: commune.nom_commune ?? commune.code_insee, code: commune.code_insee
	};
	const mapped = mapCommuneToFiche(commune);
	const rentContexts = {
		'loyers.loyer_m2_appartement': contexteLoyer('appartement', commune.loyers_appartement, commune.loyer_m2_appartement),
		'loyers.loyer_m2_maison': contexteLoyer('maison', commune.loyers_maison, commune.loyer_m2_maison)
	};
	function qualify(item: KvRow | KpiItem): FieldPresentation | undefined {
		const presentation = item.presentation;
		if (!presentation) return undefined;
		switch (item.fieldId) {
			case 'loyers.loyer_m2_appartement':
			case 'loyers.loyer_m2_maison':
				return {
					...presentation,
					grain: rentGrain(item.fieldId === 'loyers.loyer_m2_appartement' ? commune.loyers_appartement : commune.loyers_maison),
					reserves: rentContexts[item.fieldId].map(row => row.value)
				};
			case 'revenus.revenu_median':
				return { ...presentation, reserves: [INCOME_RESERVE] };
			case 'vacance.taux_vacance':
				return { ...presentation, grain: 'Parc privé communal', reserves: [VACANCY_RESERVE] };
			case 'vacance.nb_vacants_plus_2ans':
				return { ...presentation, grain: 'Parc privé communal' };
			default:
				return presentation;
		}
	}
	const kpi = mapped.kpi.map(item => ({ ...item, presentation: qualify(item) }));
	const themeModels = mapped.themes.map(theme => {
		const rows = theme.rows.map(row => ({ ...row, presentation: qualify(row) }));
		// Additional context travels through both existing CSV paths; baseline rows are untouched.
		if (theme.key === 'environnement') rows.push({ kind: 'context', state: 'value', label: 'Portée du revenu déclaré', value: INCOME_RESERVE, presentation: undefined });
		if (theme.key === 'propriete') rows.push({ kind: 'context', state: 'value', label: 'Portée de la vacance', value: VACANCY_RESERVE, presentation: undefined });
		return { ...theme, rows };
	});
	const metadata = {
		criticalSummaries: criticalSummariesForCommune(commune),
		millesimes: millesimesFromCommune(commune),
		sources: sourcesCommuneParDataset(sources)
	};
	const indicatorSummary = qualifiedIndicatorSummary(commune, themeModels);
	const summary = indicatorSummary === null
		? kpisToGrid(kpi, column, { ...metadata, id: 'commune-synthese' })
		: enrichCommuneIndicatorPresentation(rowsToGrid(indicatorSummary, column, { ...metadata, id: 'commune-synthese' }), commune);
	return {
		column,
		fiche: { kpi, themes: themeModels },
		synthese: summary,
		themes: Object.fromEntries(themeModels.map(theme => [
			theme.key, enrichCommuneIndicatorPresentation(rowsToGrid(theme.rows, column, { ...metadata, id: `commune-${theme.key}` }), commune)
		]))
	};
}

// --- Themes of the fiche (D-46, UI-SPEC « /commune/[code] » points 5 to 7) ---------------------
//
// Below the synthesis, the rest stays folded in the five themes of the mock-up. This section only
// REGROUPS the rows the existing projections already produce (mapCommuneToFiche →
// mapCommuneToGrid, D-51): it computes no value. What it adds is what those projections could
// not say — the state the API DECLARED for an absence. A flat column read null is resolved by
// `etatDeColonne` (D-47), a dated-block row by the block's own served state; the historical
// out_of_coverage default of `formatNullableField` is never shown. An absence the API did not
// declare, or a state the glossary does not know, is not rendered: it is named on the console.

export type CleTheme =
	| 'immobilier'
	| 'energie-bati'
	| 'services-transports'
	| 'contexte-communal'
	| 'risques-urbanisme';

export interface ThemeCommune {
	cle: CleTheme;
	titre: string;
	/** Fragment of the theme: `/commune/{code}#{ancre}` opens it. */
	ancre: string;
}

/** The five folded themes, in the mock-up order (D-46). */
export const COMMUNE_THEMES: readonly ThemeCommune[] = [
	{ cle: 'immobilier', titre: 'Immobilier', ancre: 'immobilier' },
	{ cle: 'energie-bati', titre: 'Énergie & bâti', ancre: 'energie-bati' },
	{ cle: 'services-transports', titre: 'Services & transports', ancre: 'services-transports' },
	{ cle: 'contexte-communal', titre: 'Contexte communal', ancre: 'contexte-communal' },
	{ cle: 'risques-urbanisme', titre: 'Risques & urbanisme', ancre: 'risques-urbanisme' }
];

/**
 * field_id → theme: exactly one theme per field (UI-SPEC point 5). Every field the Commune
 * projections render has an entry; a served field missing here is a build defect, not a row to
 * hide (themesCommune throws).
 */
export const THEME_DU_CHAMP: Readonly<Record<string, CleTheme>> = {
	// DVF (dated blocks and flat columns), rents, housing stock and vacancy → Immobilier.
	'dvf.indicateurs.maison.mediane_courante': 'immobilier',
	'dvf.indicateurs.maison.mediane_precedente': 'immobilier',
	'dvf.indicateurs.maison.variation_pct': 'immobilier',
	'dvf.indicateurs.appartement.mediane_courante': 'immobilier',
	'dvf.indicateurs.appartement.mediane_precedente': 'immobilier',
	'dvf.indicateurs.appartement.variation_pct': 'immobilier',
	'dvf.indicateurs.effectif': 'immobilier',
	'dvf.indicateurs.quantiles.p10': 'immobilier',
	'dvf.prix_m2_median': 'immobilier',
	'dvf.prix_m2_median_maison': 'immobilier',
	'dvf.nb_mutations_12m_maison': 'immobilier',
	'dvf.prix_m2_median_appartement': 'immobilier',
	'dvf.nb_mutations_12m_appartement': 'immobilier',
	'dvf.nb_mutations_12m': 'immobilier',
	// Yearly price series (migration 115): the change is the row, the rest is read in its panel.
	'dvf.tendance.evolution_pct': 'immobilier',
	'dvf.tendance.prix_m2_median': 'immobilier',
	'dvf.tendance.nb_ventes': 'immobilier',
	'dvf.tendance.annee': 'immobilier',
	'dvf.tendance.seuil': 'immobilier',
	'loyers.loyer_m2_appartement': 'immobilier',
	'loyers.loyer_m2_maison': 'immobilier',
	'loyers.annee': 'immobilier',
	'demographie.nb_logements': 'immobilier',
	'demographie.nb_logements_vacants_recensement': 'immobilier',
	'vacance.taux_vacance': 'immobilier',
	'vacance.nb_vacants_plus_2ans': 'immobilier',
	// DPE (dated block and flat columns) → Énergie & bâti.
	'dpe.indicateurs.population.total': 'energie-bati',
	'dpe.indicateurs.distribution.classes': 'energie-bati',
	'dpe.indicateurs.mediane_ordinale': 'energie-bati',
	'dpe.indicateurs.modes': 'energie-bati',
	'dpe.indicateurs.part_fg_pct': 'energie-bati',
	'dpe.dpe_passoires_pct': 'energie-bati',
	'dpe.dpe_median': 'energie-bati',
	'dpe.annee_construction_mediane': 'energie-bati',
	// Facilities, schools, fibre → Services & transports.
	'equipements.nb_equipements': 'services-transports',
	'scolaire.nb_etablissements_scolaires': 'services-transports',
	'connectivite.fibre_pct': 'services-transports',
	// Population (housing excepted), income, local taxation, legal-entity land → Contexte communal.
	'demographie.population': 'contexte-communal',
	'demographie.densite': 'contexte-communal',
	'demographie.superficie': 'contexte-communal',
	'demographie.nb_menages': 'contexte-communal',
	'revenus.revenu_median': 'contexte-communal',
	'revenus.taux_pauvrete': 'contexte-communal',
	'revenus.indice_gini': 'contexte-communal',
	'revenus.part_imposee': 'contexte-communal',
	'fiscalite.taux_tfpb': 'contexte-communal',
	'fiscalite.taux_th_rs': 'contexte-communal',
	'fiscalite.taux_teom': 'contexte-communal',
	'fiscalite.nom_epci': 'contexte-communal',
	'foncier_pm.nb_proprietaires_pm': 'contexte-communal',
	'foncier_pm.surface_pm_ha': 'contexte-communal',
	// ABC zoning, historical monuments → Risques & urbanisme.
	'zonage_abc.zone': 'risques-urbanisme',
	'zonage_abc.reclassement': 'risques-urbanisme',
	'monuments.nb_monuments': 'risques-urbanisme'
};

/**
 * Rows of the former themes that no /v1/commune field serves: structural placeholders the mapper
 * wrote as « out of coverage » without any reason. A field the API does not serve is not
 * displayed (UI-SPEC point 5); the mock-up fields without a served value are deferred in
 * the internal tracking (champs-maquette-hors-synthese).
 */
const CHAMPS_NON_SERVIS: ReadonlySet<string> = new Set([
	'commune.risques.risques_naturels',
	'commune.propriete.coproprietes'
]);

/**
 * Caveat rows `mapCommuneToGrid` appends to a former theme whose last row is another field's (R-04): each
 * takes the theme of the field it qualifies, never the one of the row above it.
 */
const THEME_DE_LA_NOTE: Readonly<Record<string, CleTheme>> = {
	'label:Portée du revenu déclaré': THEME_DU_CHAMP['revenus.revenu_median'],
	'label:Portée de la vacance': THEME_DU_CHAMP['vacance.taux_vacance']
};

/** Flat column behind each flat field of the themes (the API key its state is declared on). */
const COLONNE_DU_CHAMP: Readonly<Record<string, ColonnePlate>> = {
	'dvf.prix_m2_median': 'prix_m2_median',
	'dvf.prix_m2_median_maison': 'prix_m2_median_maison',
	'dvf.nb_mutations_12m_maison': 'nb_mutations_12m_maison',
	'dvf.prix_m2_median_appartement': 'prix_m2_median_appartement',
	'dvf.nb_mutations_12m_appartement': 'nb_mutations_12m_appartement',
	'dvf.nb_mutations_12m': 'nb_mutations_12m',
	'loyers.loyer_m2_appartement': 'loyer_m2_appartement',
	'loyers.loyer_m2_maison': 'loyer_m2_maison',
	'loyers.annee': 'annee_loyers',
	'demographie.nb_logements': 'nb_logements',
	'demographie.nb_logements_vacants_recensement': 'nb_logements_vacants_recensement',
	'vacance.taux_vacance': 'taux_vacance',
	'vacance.nb_vacants_plus_2ans': 'nb_vacants_plus_2ans',
	'dpe.dpe_passoires_pct': 'dpe_passoires_pct',
	'dpe.dpe_median': 'dpe_median',
	'dpe.annee_construction_mediane': 'annee_construction_mediane',
	'equipements.nb_equipements': 'nb_equipements',
	'scolaire.nb_etablissements_scolaires': 'nb_etablissements_scolaires',
	'connectivite.fibre_pct': 'fibre_pct',
	'demographie.population': 'population',
	'demographie.densite': 'densite',
	'demographie.superficie': 'superficie',
	'demographie.nb_menages': 'nb_menages',
	'revenus.revenu_median': 'revenu_median',
	'revenus.taux_pauvrete': 'taux_pauvrete',
	'revenus.indice_gini': 'indice_gini',
	'revenus.part_imposee': 'part_imposee',
	'fiscalite.taux_tfpb': 'taux_tfpb',
	'fiscalite.taux_th_rs': 'taux_th_rs',
	'fiscalite.taux_teom': 'taux_teom',
	'fiscalite.nom_epci': 'nom_epci',
	'foncier_pm.nb_proprietaires_pm': 'nb_proprietaires_pm',
	'foncier_pm.surface_pm_ha': 'surface_pm_ha',
	'zonage_abc.zone': 'zone_abc',
	'zonage_abc.reclassement': 'zone_abc_reclassement',
	'monuments.nb_monuments': 'nb_monuments'
};

/** The count of a flat median: the sentence of its absence says it, as the tile did. */
const EFFECTIF_DU_CHAMP: Readonly<Record<string, ColonnePlate>> = {
	'dvf.prix_m2_median_maison': 'nb_mutations_12m_maison',
	'dvf.prix_m2_median_appartement': 'nb_mutations_12m_appartement'
};

/** Dated-block rows the data layer writes without a field id: the contract entry they read. */
function champDeLigneDeBloc(id: string): string | null {
	if (/^dvf-(?:maison|appartement)-effectif-/.test(id)) return 'dvf.indicateurs.effectif';
	if (/^dvf-(?:maison|appartement)-quantiles-/.test(id)) return 'dvf.indicateurs.quantiles.p10';
	const champs: Record<string, string> = {
		'dpe-population': 'dpe.indicateurs.population.total',
		'dpe-distribution': 'dpe.indicateurs.distribution.classes',
		'dpe-mediane': 'dpe.indicateurs.mediane_ordinale',
		'dpe-modes': 'dpe.indicateurs.modes'
	};
	return champs[id] ?? null;
}

/** Fields whose public definitions define one row together (the P10–P90 interval). */
const DEFINITIONS_JOINTES: Readonly<Record<string, readonly string[]>> = {
	'dvf.indicateurs.quantiles.p10': ['dvf.indicateurs.quantiles.p10', 'dvf.indicateurs.quantiles.p50', 'dvf.indicateurs.quantiles.p90']
};

/** Dated-block rows written as « context » that carry a served figure, not a caveat. */
const LIGNES_DE_BLOC_CHIFFREES: ReadonlySet<string> = new Set(['dpe-population', 'dpe-distribution', 'dpe-periode']);

/**
 * Labels aligned on the definition the API serves (UI-SPEC point 6): the price interval is
 * P10–P90, never an interquartile range (E9); the school count is the school map's, not a census
 * (E10); the ABC zone and its reclassification say what the contract says.
 */
const LIBELLES_ALIGNES: Readonly<Record<string, string>> = {
	'dvf-maison-quantiles-courants': 'Intervalle P10–P90 des maisons (fenêtre courante)',
	'dvf-appartement-quantiles-courants': 'Intervalle P10–P90 des appartements (fenêtre courante)',
	'scolaire.nb_etablissements_scolaires': 'Collèges référencés par la sectorisation',
	'demographie.nb_logements_vacants_recensement': 'Logements vacants au recensement',
	'vacance.taux_vacance': 'Taux de vacance du parc privé',
	'zonage_abc.zone': 'Zone ABC (tension du marché locatif)',
	'zonage_abc.reclassement': 'Reclassement de zone ABC demandé'
};

/**
 * Label on the page when it differs from the exported one: the CSV keeps its reference labels. The
 * tiles read the same words (one field, one label, 4.9, lot 3).
 */
const LIBELLES_AFFICHES: Readonly<Record<string, string>> = {
	'dvf-maison-mediane-courante': LIBELLES_MEDIANE.maison,
	'dvf-appartement-mediane-courante': LIBELLES_MEDIANE.appartement,
	'dvf-maison-variation': LIBELLES_TENDANCE.maison,
	'dvf-appartement-variation': LIBELLES_TENDANCE.appartement,
	// Plain words (4.9, lot 4), the forms of the address fiche; the unit €/UC/an stays, explained in its panel.
	'fiscalite.taux_tfpb': 'Taxe foncière sur le bâti',
	'fiscalite.taux_th_rs': 'Taxe d’habitation (résidences secondaires)',
	'fiscalite.taux_teom': 'Taxe d’ordures ménagères',
	'fiscalite.nom_epci': 'Intercommunalité',
	'revenus.indice_gini': 'Inégalités de revenus (indice de Gini)'
};

/**
 * Help taken from the served reason instead of the glossary sentence. `zone_abc_reclassement`
 * served null is typed `not_available`, but « the source does not publish it for this territory »
 * would overstate what is known (03-06 review): the reason the API served is shown, as served.
 */
const AIDE_DU_MOTIF_SERVI: ReadonlySet<string> = new Set(['zonage_abc.reclassement']);

// `formatters.ts` has no suffix for the dated blocks' units; same mapping as commune-kpi.ts.
const UNITES_BLOCS: Readonly<Record<string, string>> = { 'EUR/m2': '€/m²', percent_points: '%' };

/** One indicator of a theme, as the rows of its panel render it. */
export interface LigneTheme {
	/** Row key of the data layer (field id or block row id): stable, unique within the fiche. */
	id: string;
	fieldId: string | null;
	/** Label on the page; `libelleExport` is the one of the CSV. */
	libelle: string;
	libelleExport: string;
	/** A row the page adds for a state no theme carried: not a row of the CSV of reference. */
	horsExport?: boolean;
	etat: Exclude<EtatKpi, { kind: 'absence-non-declaree' }>;
	/** Served period or vintage of the value; `null` when not served (never invented). */
	periode: string | null;
	source: SourceKpi | null;
	/** Object and grain the value describes (« Commune », « Parc privé communal »…). */
	objet: string;
	/** Caveats of a served value, shown under it. */
	reserves: string[];
	volet: EntreeVolet[];
	brut: ValeurServie[];
	/** Every caveat the data layer attached, provenance included: exported, not displayed. */
	contexteExport: string[];
	/** R-09: a value of its commune served on an arrondissement: the scale mark of the row. */
	echelle?: { pictogramme: 'commune'; mot: string };
	/** R-09: where a value absent here is published (the arrondissements, or the fiche of the commune). */
	renvoi?: { href: string; texte: string };
}

/** A caveat of the theme itself (scope of a count), kept visible and exported. */
export interface NoteTheme {
	id: string;
	libelle: string;
	texte: string;
}

export interface ThemeFiche extends ThemeCommune {
	lignes: LigneTheme[];
	notes: NoteTheme[];
}

type Sources = SourceAttribution[] | null | undefined;

interface AbsenceServie {
	etat: EtatAbsence;
	motif: string;
	effectif: number | null;
	libelle?: string;
	aide?: string;
}

/** Explanations of a block row that its tile said, kept on the row of an absent value (4.9, lot 3). */
interface ComplementBloc {
	methode?: string;
	qualification?: string | null;
	limites?: string;
}

/** What a dated block serves behind one of its rows. */
interface LectureBloc {
	brut: unknown;
	absence: AbsenceServie | null;
	periode: string | null;
	effectif: number | null;
	provenance: ProvenanceIndicateur[];
	complement?: ComplementBloc;
}

function estAbsent(value: unknown): value is { etat: EtatAbsence; motif: string } {
	return typeof value === 'object' && value !== null && 'etat' in value && !('version_contrat' in value);
}

function fenetre(stats: StatistiquesFenetreDvf): string | null {
	return stats.fenetre ? `${stats.fenetre.debut_inclus} → ${stats.fenetre.fin_exclue} (fin exclue)` : stats.motif_periode ?? null;
}

function lectureNombre(
	value: NombreOuAbsence,
	effectif: number | null,
	periode: string | null,
	provenance: ProvenanceIndicateur[],
	complement?: ComplementBloc,
	decide?: Pick<AbsenceServie, 'libelle' | 'aide'>
): LectureBloc {
	return value.etat === 'value'
		? { brut: value.valeur, absence: null, periode, effectif, provenance, complement }
		: { brut: null, absence: { etat: value.etat, motif: value.motif, effectif, ...decide }, periode, effectif, provenance, complement };
}

function lecturesDvf(dvf: IndicateursDvf, lectures: Map<string, LectureBloc>) {
	const provenance = dvf.provenance;
	const limites = limitesDvf(dvf);
	for (const kind of ['maison', 'appartement'] as const) {
		const data = kind === 'maison' ? dvf.residentiel_maison : dvf.residentiel_appartement;
		const { courante, precedente } = data;
		const fenetres = [fenetre(precedente), fenetre(courante)].filter(Boolean).join(' / ') || null;
		lectures.set(`dvf-${kind}-mediane-courante`, lectureNombre(courante.mediane, courante.effectif, fenetre(courante), provenance, { methode: METHODE_PRIX, limites }, courante.effectif === 0 ? { aide: AUCUNE_VENTE } : undefined));
		lectures.set(`dvf-${kind}-mediane-precedente`, lectureNombre(precedente.mediane, precedente.effectif, fenetre(precedente), provenance));
		const variation = variationPubliable(dvf, data);
		const entamee = variation.etat !== 'value' && variation.motif === PERIODE_ENTAMEE;
		lectures.set(`dvf-${kind}-variation`, lectureNombre(variation, courante.effectif, fenetres, provenance, { methode: METHODE_TENDANCE, qualification: data.qualification_tendance, limites }, entamee ? { libelle: LIBELLE_PERIODE_ENTAMEE, aide: PERIODE_ENTAMEE } : undefined));
		// A null size is served without a state: it stays undeclared (no entry), never « not reported ».
		const effectifs: [string, StatistiquesFenetreDvf][] = [[`dvf-${kind}-effectif-courant`, courante], [`dvf-${kind}-effectif-precedent`, precedente]];
		for (const [id, stats] of effectifs) {
			if (stats.effectif != null) lectures.set(id, { brut: stats.effectif, absence: null, periode: fenetre(stats), effectif: stats.effectif, provenance });
		}
		const quantiles = courante.quantiles;
		lectures.set(`dvf-${kind}-quantiles-courants`, quantiles.etat === 'value'
			? { brut: { p10: quantiles.p10, p50: quantiles.p50, p90: quantiles.p90 }, absence: null, periode: fenetre(courante), effectif: courante.effectif, provenance }
			: { brut: null, absence: { etat: quantiles.etat, motif: quantiles.motif, effectif: courante.effectif }, periode: fenetre(courante), effectif: courante.effectif, provenance });
	}
}

function lecturesDpe(dpe: IndicateursDpe, lectures: Map<string, LectureBloc>) {
	const provenance = dpe.provenance;
	const periode = `Évaluation : ${dpe.date_evaluation}`;
	const resultat = dpe.resultat;
	if (resultat.etat !== 'value') {
		lectures.set('dpe-resultat-absence', { brut: null, absence: { etat: resultat.etat, motif: resultat.motif, effectif: null }, periode, effectif: null, provenance, complement: { limites: LIMITES_DPE } });
		return;
	}
	const valeur = (brut: unknown): LectureBloc => ({ brut, absence: null, periode, effectif: resultat.population.total, provenance });
	lectures.set('dpe-population', valeur(resultat.population.total));
	lectures.set('dpe-distribution', valeur(resultat.distribution));
	lectures.set('dpe-periode', valeur({ min: resultat.date_etablissement_min, max: resultat.date_etablissement_max }));
	lectures.set('dpe-part-fg', lectureNombre(resultat.part_fg_pct, resultat.population.total, periode, provenance));
	// modes [] and a null ordinal median are served bare: their cause is the F/G share's state
	// (03-06 review). Only a declared zero_denominator is an absence; anything else is undeclared.
	const sansClasse: LectureBloc | null = resultat.part_fg_pct.etat === 'zero_denominator'
		? { brut: null, absence: { etat: 'zero_denominator', motif: resultat.part_fg_pct.motif, effectif: null }, periode, effectif: null, provenance, complement: { methode: METHODE_DPE, limites: LIMITES_DPE } }
		: null;
	const mediane = resultat.mediane_ordinale ? valeur(resultat.mediane_ordinale) : sansClasse;
	const modes = resultat.modes.length ? valeur(resultat.modes) : sansClasse;
	if (mediane) lectures.set('dpe-mediane', mediane);
	if (modes) lectures.set('dpe-modes', modes);
}

/** Served readings behind every dated-block row, keyed by the data layer's row id. */
function lecturesDesBlocs(commune: CommuneSynthese): Map<string, LectureBloc> {
	const lectures = new Map<string, LectureBloc>();
	const dvf = commune.indicateurs_dvf;
	if (dvf && estAbsent(dvf)) {
		lectures.set('dvf-indicateurs-absence', { brut: null, absence: { etat: dvf.etat, motif: dvf.motif, effectif: null }, periode: null, effectif: null, provenance: [] });
	} else if (dvf) lecturesDvf(dvf as IndicateursDvf, lectures);
	const dpe = commune.indicateurs_dpe;
	if (dpe && estAbsent(dpe)) {
		lectures.set('dpe-indicateurs-absence', { brut: null, absence: { etat: dpe.etat, motif: dpe.motif, effectif: null }, periode: null, effectif: null, provenance: [], complement: { limites: LIMITES_DPE } });
	} else if (dpe) lecturesDpe(dpe as IndicateursDpe, lectures);
	return lectures;
}

function dateFr(iso: string | null | undefined): string | null {
	if (!iso) return null;
	const date = new Date(iso);
	return Number.isNaN(date.getTime()) ? null : date.toLocaleDateString('fr-FR', { timeZone: 'UTC' });
}

function sourceDuBloc(provenance: ProvenanceIndicateur[]): SourceKpi | null {
	const noms = [...new Set(provenance.map((item) => item.nom).filter(Boolean))];
	if (!noms.length) return null;
	const reference = provenance[0]?.reference ?? null;
	return { nom: noms.join(' · '), url: reference && provenance.every((item) => item.reference === reference) ? reference : null };
}

/** Last successful import of a flat dataset, only when the served source carries it. */
function importationServie(sources: Sources, dataset: string): string | null {
	const servie = (sources ?? []).find((source) => (source as { dataset?: string } | null)?.dataset === dataset) as
		| { derniere_importation_reussie?: string | null }
		| undefined;
	return dateFr(servie?.derniere_importation_reussie);
}

/** Served source of a flat dataset, by its name in the attribution of the response. */
function sourceDuDatasetPlat(sources: Sources, dataset: string): SourceKpi | null {
	const servie = sourcesCommuneParDataset(sources)[dataset] as { nom?: string; url?: string | null } | undefined;
	return servie?.nom ? { nom: servie.nom, url: servie.url ?? null } : null;
}

function uniteDu(fieldId: string | null): string | null {
	if (!fieldId) return null;
	return displayUnit(fieldId) ?? UNITES_BLOCS[getFieldContract(fieldId).unit] ?? null;
}

/** The grid display keeps its unit inline; the metric row writes it apart, as the KPIs do. */
function valeurAffichee(display: string, fieldId: string | null): Extract<EtatKpi, { kind: 'valeur' }> {
	const unite = uniteDu(fieldId);
	if (unite && display.endsWith(` ${unite}`)) return { kind: 'valeur', texte: display.slice(0, -unite.length - 1), unite };
	return { kind: 'valeur', texte: display, unite: null };
}

// Public definitions only (03-17 F66, M-15): a field without an `EXPLICATIONS` entry (a structural
// milestone whose state sentence already says why) has no « Définition » entry.
function definition(fieldId: string | null): string | null {
	if (!fieldId) return null;
	const textes = (DEFINITIONS_JOINTES[fieldId] ?? [fieldId]).map(definitionPublique).filter((texte): texte is string => Boolean(texte));
	return textes.length ? textes.join(' ') : null;
}

function signaler(raison: string, id: string) {
	if (import.meta.env.DEV) console.error(`[commune-grid] ${raison}, ligne non affichée : ${id}`);
}

/**
 * A row the mapper renders but THEME_DU_CHAMP does not place is a build defect: loud in
 * development and tests (the attachment table must be completed), never a failed page in
 * production (D-15 03-08) — the row is left out, the fiche still renders.
 */
function ligneSansTheme(id: string) {
	if (import.meta.env.DEV) throw new Error(`Commune row without a theme: ${id}`);
	signaler('champ sans thème dans THEME_DU_CHAMP', id);
}

function effectifTexte(n: number | null): string | null {
	if (n == null) return null;
	return `${new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 }).format(n)} ${n === 1 ? 'observation' : 'observations'}`;
}

type LigneSansVolet = Omit<LigneTheme, 'volet' | 'brut' | 'contexteExport' | 'libelleExport'>;

/** Source panel entries of one row, in the UI-SPEC order; an entry not served is omitted. */
function voletDeLigne(ligne: LigneSansVolet, importation: string | null, effectif: string | null, complement?: ComplementBloc): EntreeVolet[] {
	const entrees: EntreeVolet[] = [];
	const ajouter = (terme: string, texte: string | null | undefined) => {
		if (texte) entrees.push({ terme, valeur: texte });
	};
	ajouter(LIBELLE_EN_CLAIR, enClairChamp(ligne.fieldId));
	ajouter('Définition', definition(ligne.fieldId));
	ajouter('Objet', ligne.objet);
	ajouter('Période', ligne.periode);
	ajouter('Unité', uniteDu(ligne.fieldId));
	ajouter('Source', ligne.source?.nom);
	ajouter('Dernière importation réussie', importation);
	// A value keeps these on its tile; an absent value has no tile any more, its row says them.
	const lu = ligne.etat.kind === 'absence' ? complement : undefined;
	ajouter('Méthode', lu?.methode);
	ajouter('Qualification', lu?.qualification);
	ajouter('Effectif', effectif);
	if (ligne.etat.kind === 'absence') entrees.push({ terme: 'État et motif', absences: [{ libelle: null, etat: ligne.etat }] });
	ajouter('Limites', ligne.reserves.join(' ') || lu?.limites);
	return entrees;
}

/**
 * The trend of a commune without a dated DVF block had a tile and no theme row: its state moves to
 * Immobilier, with the label of the tile, once for houses and flats when the two are the same. No value,
 * no reason of a served block: the row is the page's, not one of the CSV of reference (4.9, A1).
 */
function lignesTendanceSansBloc(commune: CommuneSynthese): LigneTheme[] {
	const champs = { maison: 'dvf.indicateurs.maison.variation_pct', appartement: 'dvf.indicateurs.appartement.variation_pct' };
	const lues = (['maison', 'appartement'] as const).map((type) => ({ type, tendance: tendanceSansBloc(commune, type) }));
	const [maison, appartement] = lues;
	const unique = maison.tendance.etat === appartement.tendance.etat && maison.tendance.motif === appartement.tendance.motif;
	return (unique ? [maison] : lues).map(({ type, tendance }) => {
		const etat: LigneTheme['etat'] = { kind: 'absence', effectif: null, ...tendance };
		const base: LigneSansVolet = {
			id: unique ? 'dvf-tendance' : `dvf-${type}-tendance`,
			fieldId: champs[type],
			libelle: unique ? LIBELLE_TENDANCE : LIBELLES_TENDANCE[type],
			etat,
			periode: null,
			source: null,
			objet: OBJET_TENDANCE,
			reserves: []
		};
		return {
			...base,
			libelleExport: base.libelle,
			horsExport: true,
			volet: voletDeLigne(base, null, null),
			brut: (unique ? (['maison', 'appartement'] as const) : [type]).map((cle) => ({
				fieldId: champs[cle],
				valeur: 'null',
				etat: { kind: 'absence' as const, etat: tendance.etat, motif: tendance.motif }
			})),
			contexteExport: []
		};
	});
}

/**
 * The yearly price series (decision of 2026-11-11): one row per type of dwelling, the most sold first,
 * with its served change or the state that withholds it. Rows of the CSV export, unlike the rows above.
 */
function lignesTendanceAnnuelle(commune: CommuneSynthese, sources: Sources): LigneTheme[] {
	const tendance = tendanceServie(commune);
	if (!tendance) return [];
	const source = sourceDuDatasetPlat(sources, 'dvf');
	return tendance.types.map((type) => {
		const base: LigneSansVolet = {
			id: `dvf-tendance-${type.type_bien}`,
			fieldId: CHAMP_EVOLUTION,
			libelle: LIBELLES_TENDANCE[type.type_bien],
			etat: etatTendance(tendance, type),
			periode: `${periodeTendance(tendance)}, années civiles entières`,
			source,
			objet: OBJET_TENDANCE_ANNUELLE,
			reserves: []
		};
		const volet = voletDeLigne(base, importationServie(sources, 'dvf'), null);
		const limites = { terme: 'Limites', valeur: LIMITES_TENDANCE_ANNUELLE };
		return {
			...base,
			libelleExport: base.libelle,
			volet: [...volet, { terme: 'Méthode', valeur: methodeTendance(tendance) }, ...entreesDetail([type]), limites],
			brut: [{
				fieldId: CHAMP_EVOLUTION,
				valeur: JSON.stringify(type.evolution_pct),
				etat: base.etat.kind === 'absence' ? { kind: 'absence' as const, etat: base.etat.etat, motif: base.etat.motif } : { kind: 'valeur' as const }
			}],
			contexteExport: [base.periode!, ...(source ? [`Source\u00a0: ${source.nom}`] : []), ...(base.etat.kind === 'absence' ? [`Motif\u00a0: ${base.etat.motif}`] : [])]
		};
	});
}

/** Rows the page shows before the rents: the trend follows the sales it compares. */
const APRES_LES_VENTES = 'dvf.nb_mutations_12m_appartement';

// --- Paris, Lyon, Marseille: the other mesh (R-09) -------------------------------------------
//
// The API serves on an arrondissement the values its commune alone publishes (`heritages`: scale and commune)
// and, on an absence the other mesh fills, where the value is published (`etats[…].publiee_a`). The row says
// both; it computes nothing (D-51).

/** Origin of a column served from the commune (`heritages` of /v1/commune). */
interface HeritageServi {
	echelle: 'commune';
	code_insee: string;
	nom: string | null;
}

type SyntheseMailles = CommuneSynthese & { heritages?: Record<string, HeritageServi> | null };

/** The scale mark of an inherited row: the mark the fiche already draws for the commune. */
const ECHELLE_COMMUNE = { pictogramme: 'commune', mot: 'Commune' } as const;

function heritageDe(commune: CommuneSynthese, colonne: ColonnePlate): HeritageServi | null {
	return (commune as SyntheseMailles).heritages?.[colonne] ?? null;
}

/** Object of an inherited row (« Objet » of its panel, scope of its CSV row). */
const objetHerite = (heritage: HeritageServi): string => `Commune de ${heritage.nom ?? heritage.code_insee}`;

/** Where a value absent from `colonne` is published, with the sentence and the link of its row. */
function renvoiDe(commune: CommuneSynthese, colonne: ColonnePlate): { aide: string; href: string; texte: string } | null {
	const maille = (commune.etats?.[colonne] as { publiee_a?: string } | undefined)?.publiee_a;
	if (maille === 'arrondissement') {
		const ville = commune.nom_commune ?? commune.code_insee;
		return { aide: phrasePublieeAilleurs('arrondissement', ville), href: `#${ANCRE_ARRONDISSEMENTS}`, texte: `Arrondissements de ${ville}` };
	}
	if (maille === 'commune') {
		const parente = communeParente(commune.code_insee);
		if (!parente) return null;
		return { aide: phrasePublieeAilleurs('commune', parente.nom), href: `/commune/${parente.code}`, texte: `Fiche de ${parente.nom}` };
	}
	return null;
}

/** The served state of one row of the former grids, or `null` when it must not be rendered. */
function etatDeLigne(
	commune: CommuneSynthese,
	cell: FicheGridCell,
	cle: string,
	fieldId: string | null,
	colonne: ColonnePlate | undefined,
	bloc: LectureBloc | null
): LigneTheme['etat'] | null {
	if (cell.kind === 'context') return { kind: 'valeur', texte: cell.text, unite: null };
	if (cell.kind === 'value') return valeurAffichee(String(cell.display), fieldId);
	if (colonne) {
		const lu = etatDeColonne(commune, colonne);
		if (lu.kind !== 'absence') {
			signaler(lu.kind === 'valeur' ? 'valeur servie rendue absente' : 'absence non déclarée par l’API', cle);
			return null;
		}
		const compte = fieldId && EFFECTIF_DU_CHAMP[fieldId] ? etatDeColonne(commune, EFFECTIF_DU_CHAMP[fieldId]) : null;
		const etat: LigneTheme['etat'] = { kind: 'absence', etat: lu.etat, motif: lu.motif, effectif: compte?.kind === 'valeur' ? Number(compte.valeur) : null };
		if (fieldId && AIDE_DU_MOTIF_SERVI.has(fieldId)) etat.aide = lu.motif;
		return etat;
	}
	if (bloc?.absence) return { kind: 'absence', ...bloc.absence };
	signaler('absence non déclarée par l’API', cle);
	return null;
}

/**
 * The five themes of the fiche with the rows the API serves, in the mock-up order. A theme with no
 * served row is absent (UI-01: no empty `<details>`). Uses only the loaded response: no fetch, no
 * aggregation, no comparison, no value computed in the browser (D-51).
 */
export function themesCommune(
	commune: CommuneSynthese,
	sources: Sources = null,
	grid: CommuneGridModel = mapCommuneToGrid(commune, sources)
): ThemeFiche[] {
	const blocs = lecturesDesBlocs(commune);
	const lignes = new Map<CleTheme, LigneTheme[]>(COMMUNE_THEMES.map((theme) => [theme.cle, []]));
	const notes = new Map<CleTheme, NoteTheme[]>(COMMUNE_THEMES.map((theme) => [theme.cle, []]));
	const contextes: { theme: CleTheme; note: NoteTheme }[] = [];
	const vus = new Set<string>();

	for (const themeGrid of Object.values(grid.themes)) {
		// A caveat row qualifies the row above it: it joins that row's theme.
		let precedent: CleTheme | null = null;
		for (const row of themeGrid.rows) {
			const cell = row.cells[grid.column.id];
			const cle = row.concept.id.slice(row.concept.id.indexOf(':') + 1).replace(/^context:/, '');
			const fieldId = row.concept.fieldId ?? champDeLigneDeBloc(cle);
			if (fieldId && CHAMPS_NON_SERVIS.has(fieldId)) continue;
			const estBloc = /^(?:dvf|dpe)-/.test(cle);
			// A field takes the theme THEME_DU_CHAMP gives it, never its neighbour's; only a row
			// without a field (a caveat, a dated-block row) follows its block or the row above it.
			const theme: CleTheme | null = fieldId
				? THEME_DU_CHAMP[fieldId] ?? null
				: THEME_DE_LA_NOTE[cle] ?? (cle.startsWith('dvf-') ? 'immobilier' : cle.startsWith('dpe-') ? 'energie-bati' : precedent);
			if (!theme) {
				ligneSansTheme(row.concept.id);
				continue;
			}
			precedent = theme;

			if (cell.kind === 'context' && !LIGNES_DE_BLOC_CHIFFREES.has(cle)) {
				contextes.push({ theme, note: { id: cle, libelle: row.concept.label, texte: cell.text } });
				continue;
			}
			// One row per field: the ABC zone was written in two former themes.
			const unique = fieldId && !estBloc ? fieldId : cle;
			if (vus.has(unique)) continue;
			vus.add(unique);

			const colonne = fieldId && !estBloc ? COLONNE_DU_CHAMP[fieldId] : undefined;
			const bloc = estBloc ? blocs.get(cle) ?? null : null;
			const etat = etatDeLigne(commune, cell, cle, fieldId, colonne, bloc);
			if (!etat) continue;
			if (etat.kind === 'absence' && !libelleEtat(etat.etat, etat.motif)) {
				signaler(`état sans libellé du glossaire (${etat.etat})`, cle);
				continue;
			}

			const heritage = colonne && etat.kind === 'valeur' ? heritageDe(commune, colonne) : null;
			const renvoi = colonne && etat.kind === 'absence' ? renvoiDe(commune, colonne) : null;
			if (renvoi && etat.kind === 'absence') etat.aide = renvoi.aide;

			const dataset = fieldId ? datasetDuField(fieldId) : null;
			const periode = etat.kind === 'absence' && etat.etat === 'out_of_coverage'
				? null
				: estBloc ? bloc?.periode ?? null : flatCommunePeriod(commune, sources, dataset, cell.millesime, fieldId);
			const source = estBloc ? sourceDuBloc(bloc?.provenance ?? []) : cell.source;
			const libelleExport = LIBELLES_ALIGNES[cle] ?? LIBELLES_ALIGNES[fieldId ?? ''] ?? row.concept.label;
			const base: LigneSansVolet = {
				id: cle,
				fieldId,
				libelle: LIBELLES_AFFICHES[cle] ?? LIBELLES_AFFICHES[fieldId ?? ''] ?? libelleExport,
				etat,
				periode,
				source,
				objet: heritage ? objetHerite(heritage) : cell.grain,
				reserves: etat.kind === 'valeur' && !estBloc ? [...cell.reserves] : []
			};
			const importation = estBloc
				? dateFr((bloc?.provenance ?? []).map((item) => item.date_import).filter((d): d is string => Boolean(d)).sort().at(-1))
				: dataset ? importationServie(sources, dataset) : null;
			const brut = etat.kind === 'valeur' ? (colonne ? commune[colonne] ?? null : bloc?.brut ?? null) : null;
			lignes.get(theme)!.push({
				...base,
				...(heritage ? { echelle: { ...ECHELLE_COMMUNE } } : {}),
				...(renvoi ? { renvoi: { href: renvoi.href, texte: renvoi.texte } } : {}),
				libelleExport,
				volet: voletDeLigne(base, importation, effectifTexte(bloc?.effectif ?? null), bloc?.complement),
				brut: fieldId ? [{
					fieldId,
					valeur: JSON.stringify(brut),
					etat: etat.kind === 'absence' ? { kind: 'absence', etat: etat.etat, motif: etat.motif } : { kind: 'valeur' }
				}] : [],
				contexteExport: [
					...(estBloc ? [] : [periode, source ? `Source : ${source.nom}` : null].filter((item): item is string => Boolean(item))),
					...(etat.kind === 'absence' && etat.etat !== 'out_of_coverage' && etat.motif.trim() ? [`Motif : ${etat.motif}`] : []),
					...cell.reserves
				]
			});
		}
	}

	// The yearly series is the trend of the page when it is served; else the state of a missing block.
	const tendance = lignesTendanceAnnuelle(commune, sources);
	if (tendance.length || commune.indicateurs_dvf == null) {
		const immobilier = lignes.get('immobilier')!;
		const apres = immobilier.findIndex((ligne) => ligne.fieldId === APRES_LES_VENTES);
		immobilier.splice(apres + 1 || immobilier.length, 0, ...(tendance.length ? tendance : lignesTendanceSansBloc(commune)));
	}

	// A caveat already shown under the value it qualifies is not repeated as a note.
	const affichees = new Set([...lignes.values()].flat().flatMap((ligne) => ligne.reserves));
	for (const { theme, note } of contextes) if (!affichees.has(note.texte)) notes.get(theme)!.push(note);

	return COMMUNE_THEMES
		.map((theme) => ({ ...theme, lignes: lignes.get(theme.cle)!, notes: notes.get(theme.cle)! }))
		.filter((theme) => theme.lignes.length > 0);
}

/**
 * A sentence under a badge says again what the badge says, except for the two states read by mistake as
 * « no risk »: those keep it on the row. « Non publiée » and « Sans objet » keep theirs in the panel (A2).
 */
export function aideAuNiveau1(etat: LigneTheme['etat']): boolean {
	return etat.kind === 'absence' && etat.etat !== 'not_available' && etat.etat !== 'not_applicable';
}

/** One entry of the block « Définitions », for paper and for a page without JavaScript (A9-9). */
export interface EntreeDefinition {
	id: string;
	libelle: string;
	textes: string[];
}

/** The texts a reader finds in the panels, where a page without panels can show them. */
export function definitionsCommune(reperes: KpiCommune[], themes: ThemeFiche[]): EntreeDefinition[] {
	const tuiles = reperes.map((kpi): EntreeDefinition => ({
		id: `kpi:${kpi.id}`,
		libelle: kpi.libelle,
		textes: [kpi.definition, ...(kpi.source ? [`Source : ${kpi.source.nom}`] : [])]
	}));
	const lignes = themes.flatMap((theme) => theme.lignes).map((ligne): EntreeDefinition => {
		const termes = new Map(ligne.volet.flatMap((entree) => ('valeur' in entree ? [[entree.terme, entree.valeur] as const] : [])));
		const phrase = ligne.etat.kind === 'absence' && !aideAuNiveau1(ligne.etat)
			? ligne.etat.aide ?? phraseEtat(ligne.etat.etat, ligne.etat.motif, ligne.etat.effectif)
			: null;
		const source = termes.get('Source');
		return {
			id: `ligne:${ligne.id}`,
			libelle: ligne.libelle,
			textes: [termes.get('Définition'), phrase, source && `Source : ${source}`].filter((texte): texte is string => Boolean(texte))
		};
	});
	return [...tuiles, ...lignes].filter((entree) => entree.textes.length > 0);
}

/** A row as the CSV export reads it. `KvRow` of the other fiches fits this shape. */
export interface LigneExport {
	id?: string;
	kind?: 'context';
	label: string;
	value: string;
	/** Served state key (`value`, `insufficient_sample`…), exported as is in the « État » column. */
	state?: string;
	available?: boolean;
	presentation?: { state?: string; grain?: string; reserves?: readonly string[] };
}

/** A theme as the CSV export reads it: its rows with their served state, grain and caveats. */
export interface GroupeExport {
	key: string;
	name: string;
	rows: readonly LigneExport[];
}

/** The themes in their order, for the CSV export (UI-SPEC « Impression »). */
export function themesPourExport(themes: ThemeFiche[]): GroupeExport[] {
	return themes.map((theme) => ({
		key: theme.cle,
		name: theme.titre,
		rows: [
			...theme.lignes.filter((ligne) => !ligne.horsExport).map((ligne): LigneExport => ({
				id: ligne.id,
				label: ligne.libelleExport,
				value: ligne.etat.kind === 'valeur'
					? [ligne.etat.texte, ligne.etat.unite].filter(Boolean).join(' ')
					: libelleEtat(ligne.etat.etat, ligne.etat.motif),
				state: ligne.etat.kind === 'valeur' ? 'value' : ligne.etat.etat,
				presentation: { grain: ligne.objet, reserves: ligne.contexteExport }
			})),
			...theme.notes.map((note): LigneExport => ({ id: note.id, kind: 'context', label: note.libelle, value: note.texte }))
		]
	}));
}
