import { COMMUNE_85191_OBSERVED, COMMUNE_85191_SOURCES } from '../../../src/lib/data/__fixtures__/commune-85191-20261012';

export const COMMUNE_INDICATORS_SYNTHETIC = {
	indicateurs_dvf: {
		version_contrat: '1.1', version_population: 'synthetic-e2e', date_calcul: '2026-08-03', generation: 'synthetic-g1',
		provenance: [{ nom: 'DVF synthétique', producteur: 'Fixture E2E', licence: 'fixture', reference: 'synthetic-only', date_publication_source: null, date_snapshot: '2026-08-01', date_import: '2026-08-02', couverture: 'unknown' }],
		ancrage_temporel: { etat: 'unknown', motif: 'Fixture synthétique : aucune qualification réelle.' },
		population: 'mutations_residentielles_mono_local_hors_vefa_mono_commune', unite_prix: 'EUR/m2', methode_quantiles: 'percentile_cont', methode_variation: '100*(mediane_courante/mediane_precedente-1)',
		residentiel_maison: {
			precedente: { fenetre: { debut_inclus: '2024-01-01', fin_exclue: '2025-01-01' }, effectif: 10, mediane: { etat: 'value', valeur: 2000 }, quantiles: { etat: 'value', p10: 1800, p50: 2000, p90: 2200, qualification: 'synthetic' } },
			courante: { fenetre: { debut_inclus: '2025-01-01', fin_exclue: '2026-01-01' }, effectif: 12, mediane: { etat: 'value', valeur: 2000 }, quantiles: { etat: 'value', p10: 2000, p50: 2000, p90: 2000, qualification: 'synthetic' } },
			variation_pct: { etat: 'value', valeur: 0 }, qualification_tendance: 'synthetic'
		},
		residentiel_appartement: {
			precedente: { fenetre: { debut_inclus: '2024-01-01', fin_exclue: '2025-01-01' }, effectif: 3, mediane: { etat: 'insufficient_sample', motif: 'Échantillon insuffisant.' }, quantiles: { etat: 'insufficient_sample', motif: 'Échantillon insuffisant.' } },
			courante: { fenetre: { debut_inclus: '2025-01-01', fin_exclue: '2026-01-01' }, effectif: 0, mediane: { etat: 'insufficient_sample', motif: 'Aucune mutation retenue.' }, quantiles: { etat: 'insufficient_sample', motif: 'Aucune mutation retenue.' } },
			variation_pct: { etat: 'insufficient_sample', motif: 'Tendance indisponible.' }, qualification_tendance: null
		}
	},
	indicateurs_dpe: {
		version_contrat: '1.0', version_population: 'synthetic-e2e', date_calcul: '2026-08-03', generation: 'synthetic-g1',
		provenance: [{ nom: 'DPE synthétique', producteur: 'Fixture E2E', licence: 'fixture', reference: 'synthetic-only', date_publication_source: null, date_snapshot: '2026-08-01', date_import: '2026-08-02', couverture: 'unknown' }],
		population: 'diagnostics_canoniques_disponibles_hors_traces_retirees', periode: 'bornes_etablissement_observees_non_exhaustives', date_evaluation: '2026-08-01', methode_mediane: 'percentile_disc', methode_modes: 'toutes_classes_frequence_maximale_ordre_A_G',
		resultat: { etat: 'value', population: { total: 6, classes_connues: 5, types_batiment: { maison: 2, appartement: 2, autre: 1, inconnu: 1 }, validite: { unknown: 1, expired: 1, not_expired_by_date: 4, inconsistent_dates: 0, future_establishment: 0 }, remplacement: { not_checked: 5, no_successor_known: 1, replaced: 0, ambiguous: 0 }, regimes: { existant: 4, neuf: 1, inconnu: 1 }, source_inconnue: 0, revision_non_verifiee: 5 }, distribution: { classes: { A: 0, B: 0, C: 2, D: 1, E: 2, F: 0, G: 0 }, inconnue: 1 }, modes: ['C', 'E'], mediane_ordinale: 'D', part_fg_pct: { etat: 'value', valeur: 0 }, date_etablissement_min: '2020-01-01', date_etablissement_max: '2026-07-31', dates_etablissement_inconnues: 1 }
	}
} as const;

// Flat columns of GET /v1/commune for Challans (85047) as served since D-47 (03-06): every column
// served null carries its declared state in `etats`. The dated blocks are null, as on an instance
// that does not serve them. Illustrative values, never asserted as production truth.
export const COMMUNE_FLAT_85047 = {
	code_insee: '85047',
	indicateurs_dvf: null,
	indicateurs_dpe: null,
	nom_commune: 'Challans',
	code_departement: '85',
	code_region: '52',
	population: 21516,
	nb_menages: 9800,
	nb_logements: 11200,
	nb_logements_vacants_recensement: 820,
	densite: 287.4,
	superficie: 74.9,
	annee_demographie: 2021,
	revenu_median: 21340,
	taux_pauvrete: 12.8,
	indice_gini: 0.27,
	part_imposee: 58.2,
	annee_revenus: 2021,
	zone_abc: 'B2',
	zone_abc_reclassement: false,
	taux_vacance: 9.4,
	nb_vacants_plus_2ans: 410,
	annee_vacance: 2022,
	taux_tfpb: 41.3,
	taux_th_rs: 18.7,
	taux_teom: 11.2,
	nom_epci: 'CC Challans Gois Vie Océan',
	annee_fiscalite: 2023,
	loyer_m2_appartement: 9.1,
	loyer_m2_maison: 8.4,
	annee_loyers: 2023,
	loyers_appartement: null,
	loyers_maison: null,
	prix_m2_median: 2410,
	nb_mutations_12m: 271,
	prix_m2_median_maison: 2480,
	nb_mutations_12m_maison: 268,
	prix_m2_median_appartement: null,
	nb_mutations_12m_appartement: 3,
	dpe_passoires_pct: 9.25,
	dpe_median: 'D',
	annee_construction_mediane: 1985,
	nb_equipements: 1124,
	annee_equipements: 2023,
	nb_etablissements_scolaires: 4,
	nb_monuments: 2,
	nb_proprietaires_pm: 1187,
	surface_pm_ha: 932.5,
	fibre_pct: null,
	etats: {
		prix_m2_median_appartement: {
			etat: 'insufficient_sample',
			motif: '3 ventes d’appartements sur 12 mois, sous le seuil de publication de 5.'
		},
		fibre_pct: { etat: 'not_reported', motif: 'Aucune ligne de la source pour cette commune.' }
	}
} as const;

/** Current API contract: the proven inclusive DVF window is served verbatim. Keep
 * `COMMUNE_FLAT_85047` without the field so older fixtures still exercise the unknown case. */
export const COMMUNE_FLAT_85047_WITH_DVF_WINDOW = {
	...COMMUNE_FLAT_85047,
	fenetre_mediane_dvf: '2024-12-31 → 2025-12-31'
} as const;

export { COMMUNE_85191_OBSERVED, COMMUNE_85191_SOURCES };

/** `/v1/commune` of a commune the instance does not host: no row, the reason in `meta` (D-24). */
export function reponseHorsCouverture(
	motif: 'dataset_non_heberge' | 'territoire_hors_perimetre' | 'source_sans_couverture'
) {
	return {
		statut: 'ok',
		donnees: [],
		meta: { source: [], hebergement: { commune: { etat: 'hors_couverture', motif, departements: [] } } }
	};
}

/** `/v1/commune` for a code without a synthesis row and without a reason. */
export const REPONSE_SANS_LIGNE = { statut: 'ok', donnees: [], meta: { source: [] } } as const;

/** `/v1/commune` outage: one global 503, never a partial block. */
export const REPONSE_PANNE = { detail: 'Synthèse communale temporairement indisponible.' } as const;

/** `/v1/communes?code_insee=` referential answer (H2); an empty list means an unknown INSEE code. */
export function reponseReferentiel(
	lignes: { code_insee: string; nom: string; departement: string; population: number | null; code_postal: string | null }[]
) {
	return {
		statut: 'ok',
		donnees: { results: lignes, total_count: lignes.length, page: 1, per_page: 50, has_next: false }
	};
}
