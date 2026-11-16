import type {
	IndicateurAbsent,
	IndicateursDpe,
	IndicateursDvf,
	NombreOuAbsence,
	ProvenanceIndicateur,
	StatistiquesFenetreDvf
} from '$lib/api/commune';
import { COMMUNE_85047_CHALLANS } from './commune-85';

const value = (valeur: number): NombreOuAbsence => ({ etat: 'value', valeur });
const absent = (etat: IndicateurAbsent['etat'], motif: string): IndicateurAbsent => ({ etat, motif });

function provenance(prefix: string, snapshot: string, imported: string): ProvenanceIndicateur {
	return {
		nom: `Source ${prefix}`,
		producteur: `Producteur ${prefix}`,
		licence: `Licence ${prefix}`,
		reference: `reference-${prefix.toLowerCase()}`,
		date_publication_source: `${snapshot.slice(0, 10)}`,
		date_snapshot: snapshot,
		date_import: imported,
		couverture: 'complete'
	};
}

function stats(
	debut: string,
	fin: string,
	effectif: number,
	mediane: NombreOuAbsence,
	quantiles: StatistiquesFenetreDvf['quantiles']
): StatistiquesFenetreDvf {
	return { fenetre: { debut_inclus: debut, fin_exclue: fin }, effectif, mediane, quantiles };
}

function dvf44(): IndicateursDvf {
	return {
		version_contrat: '1.1', version_population: 'population-dvf-44', date_calcul: '2026-08-04',
		generation: 'generation-dvf-44', provenance: [{ ...provenance('DVF 44', '2026-08-02T08:00:00Z', '2026-08-03T09:00:00Z'), couverture: 'unknown' }],
		ancrage_temporel: { etat: 'unknown', motif: 'Référentiel temporel non qualifié pour la fixture 44.' },
		population: 'mutations_residentielles_mono_local_hors_vefa_mono_commune', unite_prix: 'EUR/m2',
		methode_quantiles: 'percentile_cont', methode_variation: '100*(mediane_courante/mediane_precedente-1)',
		residentiel_maison: {
			courante: stats('2025-01-01', '2026-01-01', 20, value(2500), { etat: 'value', p10: 1900, p50: 2500, p90: 3200, qualification: 'qualified' }),
			precedente: stats('2024-01-01', '2025-01-01', 18, value(2500), { etat: 'value', p10: 1800, p50: 2500, p90: 3100, qualification: 'qualified' }),
			variation_pct: value(0), qualification_tendance: 'stable'
		},
		residentiel_appartement: {
			courante: stats('2025-01-01', '2026-01-01', 2, absent('suppressed', 'Prix supprimé pour confidentialité.'), absent('insufficient_sample', 'Dispersion non publiée : échantillon insuffisant.')),
			precedente: stats('2024-01-01', '2025-01-01', 0, absent('zero_denominator', 'Aucune mutation au dénominateur.'), absent('zero_denominator', 'Aucune mutation au dénominateur.')),
			variation_pct: absent('not_qualified', 'Tendance non qualifiée.'), qualification_tendance: null
		}
	};
}

function dvf85(): IndicateursDvf {
	return {
		version_contrat: '1.1', version_population: 'population-dvf-85', date_calcul: '2026-08-14',
		generation: 'generation-dvf-85', provenance: [{ ...provenance('DVF 85', '2026-08-12T08:00:00Z', '2026-08-13T09:00:00Z'), couverture: 'unknown' }],
		ancrage_temporel: { etat: 'unknown', motif: 'Référentiel temporel non qualifié pour la fixture 85.' },
		population: 'mutations_residentielles_mono_local_hors_vefa_mono_commune', unite_prix: 'EUR/m2',
		methode_quantiles: 'percentile_cont', methode_variation: '100*(mediane_courante/mediane_precedente-1)',
		residentiel_maison: {
			courante: stats('2025-04-01', '2026-04-01', 11, value(3100), { etat: 'value', p10: 2400, p50: 3100, p90: 3900, qualification: 'qualified' }),
			precedente: stats('2024-04-01', '2025-04-01', 9, absent('insufficient_sample', 'Médiane précédente insuffisamment étayée.'), absent('insufficient_sample', 'Dispersion précédente insuffisamment étayée.')),
			variation_pct: absent('not_qualified', 'Tendance non qualifiée faute de médiane précédente.'), qualification_tendance: null
		},
		residentiel_appartement: {
			courante: stats('2025-04-01', '2026-04-01', 0, absent('zero_denominator', 'Aucune mutation au dénominateur.'), absent('zero_denominator', 'Aucune mutation au dénominateur.')),
			precedente: stats('2024-04-01', '2025-04-01', 4, absent('suppressed', 'Prix supprimé pour confidentialité.'), absent('suppressed', 'Dispersion supprimée pour confidentialité.')),
			variation_pct: absent('not_qualified', 'Tendance non qualifiée.'), qualification_tendance: null
		}
	};
}

function dpe(prefix: '44' | '85', evaluation: string): IndicateursDpe {
	return {
		version_contrat: '1.0', version_population: `population-dpe-${prefix}`, date_calcul: evaluation,
		generation: `generation-dpe-${prefix}`, provenance: [provenance(`DPE ${prefix}`, `${evaluation}T08:00:00Z`, `${evaluation}T09:00:00Z`)],
		population: 'diagnostics_canoniques_disponibles_hors_traces_retirees', periode: 'bornes_etablissement_observees_non_exhaustives',
		date_evaluation: evaluation, methode_mediane: 'percentile_disc', methode_modes: 'toutes_classes_frequence_maximale_ordre_A_G',
		resultat: {
			etat: 'value',
			population: {
				total: 5, classes_connues: 5,
				types_batiment: { maison: 3, appartement: 2, autre: 0, inconnu: 0 },
				validite: { unknown: 0, expired: 1, not_expired_by_date: 4, inconsistent_dates: 0, future_establishment: 0 },
				remplacement: { not_checked: 5, no_successor_known: 0, replaced: 0, ambiguous: 0 },
				regimes: { existant: 5, neuf: 0, inconnu: 0 }, source_inconnue: 0, revision_non_verifiee: 0
			},
			distribution: { classes: { A: 0, B: 0, C: 2, D: 1, E: 2, F: 0, G: 0 }, inconnue: 0 },
			modes: ['C', 'E'], mediane_ordinale: 'D', part_fg_pct: value(0),
			date_etablissement_min: '2021-01-10', date_etablissement_max: evaluation, dates_etablissement_inconnues: 0
		}
	};
}

export const COMMUNE_INDICATORS_44 = {
	...COMMUNE_85047_CHALLANS, code_insee: '44109', nom_commune: 'Nantes', code_departement: '44',
	indicateurs_dvf: dvf44(), indicateurs_dpe: dpe('44', '2026-08-05')
};

export const COMMUNE_INDICATORS_85 = {
	...COMMUNE_85047_CHALLANS, code_insee: '85047', nom_commune: 'Challans', code_departement: '85',
	indicateurs_dvf: dvf85(), indicateurs_dpe: dpe('85', '2026-08-15')
};

export const ABSENT_DVF_BLOCK: IndicateurAbsent = { etat: 'not_reported', motif: 'Bloc DVF absent de la livraison.' };
export const ABSENT_DPE_BLOCK: IndicateurAbsent = { etat: 'out_of_coverage', motif: 'Bloc DPE hors couverture.' };
