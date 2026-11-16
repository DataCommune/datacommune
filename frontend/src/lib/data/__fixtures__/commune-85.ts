import type { CommuneSynthese } from '$lib/api/commune';

/**
 * Frozen representative fixture for Challans (85047), Vendée.
 *
 * DEGRADED MODE (2026-06-01): api.datacommune.fr was returning HTTP 530 (Cloudflare 1033,
 * tunnel/origin down) when this fixture was frozen, so the live non-null readout could NOT
 * be confirmed. Values below are frozen on the schema + assumption A3 (dept-85 commune-level
 * datasets are loaded). They are illustrative ("à confirmer quand l'API sera up"), chosen to
 * exercise BOTH paths of the mapper:
 *   - populated commune-level fields → deterministic verdicts (zone_abc, taux_vacance, revenu_median)
 *   - a deliberately null field (loyer_m2_appartement) → per-field degradation ('n.d.' + info)
 *
 * Commune-level aggregates (DVF, DPE, equipements, scolaire, monuments, foncier PM, fibre) are
 * now part of the schema and populated below with representative dept-85 values. Remaining gaps
 * (georisques risques naturels, copropriété RNIC) are rendered as available:false rows.
 */
export const COMMUNE_85047_CHALLANS: CommuneSynthese = {
	code_insee: '85047',
	nom_commune: 'Challans',
	code_departement: '85',
	code_region: '52',
	// demographie
	population: 21179,
	nb_menages: 9876,
	nb_logements: 11240,
	nb_logements_vacants_recensement: 842,
	densite: 281.4,
	superficie: 75.27,
	annee_demographie: 2021,
	// revenus
	revenu_median: 21340,
	taux_pauvrete: 12.8,
	indice_gini: 0.27,
	part_imposee: 54.2,
	annee_revenus: 2021,
	// zonage ABC
	zone_abc: 'B2',
	zone_abc_reclassement: false,
	// vacance
	taux_vacance: 9.4,
	nb_vacants_plus_2ans: 311,
	annee_vacance: 2023,
	// fiscalite
	taux_tfpb: 41.85,
	taux_th_rs: 18.2,
	taux_teom: 11.5,
	nom_epci: "CC Challans Gois Communauté",
	annee_fiscalite: 2024,
	// loyers — loyer_m2_appartement deliberately null to exercise per-field degradation (D9-01)
	loyer_m2_appartement: null,
	loyer_m2_maison: 9.8,
	annee_loyers: 2023,
	// dvf (commune aggregate)
	prix_m2_median: 2650,
	nb_mutations_12m: 184,
	// dpe (commune aggregate)
	dpe_passoires_pct: 7.2,
	dpe_median: 'D',
	annee_construction_mediane: 1990,
	// equipements / scolaire / monuments (commune aggregate)
	nb_equipements: 1124,
	nb_etablissements_scolaires: 4,
	nb_monuments: 2,
	// foncier_pm (commune aggregate)
	nb_proprietaires_pm: 1187,
	surface_pm_ha: 932.5,
	// connectivite (commune aggregate)
	fibre_pct: 86.4
};
