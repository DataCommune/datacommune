// Dated DPE block of a synthetic commune (04.7-15): served counts that add up to the known classes,
// one mode, a served F/G share and both establishment bounds. The served share (12,5 %) is a
// sentinel the counts would not give ((300 + 184) / 3 456 = 14,0 %): a share recomputed in the
// browser would show.
import type { CommuneSynthese, IndicateursDpe } from '$lib/api/commune';
import { COMMUNE_85047_CHALLANS } from './commune-85';

export const DPE_BLOCK_15: IndicateursDpe = {
	version_contrat: '1.0', version_population: 'fixture', date_calcul: '2026-08-03', generation: null,
	provenance: [{ nom: 'DPE fixture', producteur: 'Fixture', licence: 'fixture', reference: 'https://example.org/dpe', date_publication_source: null, date_snapshot: '2026-08-01', date_import: '2026-08-02', couverture: 'unknown' }],
	population: 'diagnostics_canoniques_disponibles_hors_traces_retirees', periode: 'bornes_etablissement_observees_non_exhaustives',
	date_evaluation: '2026-08-01', methode_mediane: 'percentile_disc', methode_modes: 'toutes_classes_frequence_maximale_ordre_A_G',
	resultat: {
		etat: 'value',
		population: { total: 3512, classes_connues: 3456, types_batiment: { maison: 2000, appartement: 1400, autre: 100, inconnu: 12 }, validite: { unknown: 12, expired: 300, not_expired_by_date: 3200, inconsistent_dates: 0, future_establishment: 0 }, remplacement: { not_checked: 3512, no_successor_known: 0, replaced: 0, ambiguous: 0 }, regimes: { existant: 3300, neuf: 200, inconnu: 12 }, source_inconnue: 0, revision_non_verifiee: 0 },
		distribution: { classes: { A: 40, B: 120, C: 610, D: 1071, E: 1131, F: 300, G: 184 }, inconnue: 56 },
		modes: ['E'], mediane_ordinale: 'D', part_fg_pct: { etat: 'value', valeur: 12.5 },
		date_etablissement_min: '2021-07-01', date_etablissement_max: '2026-07-15', dates_etablissement_inconnues: 0
	}
};

export const COMMUNE_DPE_15: CommuneSynthese = {
	...COMMUNE_85047_CHALLANS,
	indicateurs_dvf: null,
	indicateurs_dpe: DPE_BLOCK_15,
	fenetre_mediane_dvf: '2024-12-31 → 2025-12-31',
	prix_m2_median_maison: 2480,
	nb_mutations_12m_maison: 96,
	prix_m2_median_appartement: 3120.5,
	nb_mutations_12m_appartement: 41
};
