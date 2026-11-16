import type { CommuneSynthese, TendancePrix, TendancePrixType } from '$lib/api/commune';
import { COMMUNE_85047_CHALLANS } from './commune-85';

// Synthetic blocks of the yearly price series (`tendance_prix`), in the shape /v1/commune serves:
// one per state of the tile. The figures are invented for the tests; none is a measure.

type Annee = [annee: number, prix: number | null, ventes: number];

function type(type_bien: TendancePrixType['type_bien'], etat: TendancePrixType['etat'], evolution_pct: number | null, annees: Annee[]): TendancePrixType {
	return {
		type_bien,
		etat,
		evolution_pct,
		nb_ventes: annees.reduce((somme, [, , ventes]) => somme + ventes, 0),
		annees: annees.map(([annee, prix_m2_median, nb_ventes]) => ({ annee, prix_m2_median, nb_ventes }))
	};
}

const bloc = (types: TendancePrixType[]): TendancePrix => ({ annee_debut: 2021, annee_fin: 2025, seuil_evolution: 30, seuil_point: 5, types });

/** A change is written for flats (the most sold); houses have points only. */
export const TENDANCE_EVOLUTION = bloc([
	type('appartement', 'evolution', 12, [[2021, 3500, 3996], [2022, 3700, 3800], [2023, 3900, 3100], [2024, 3850, 2900], [2025, 3920, 3200]]),
	type('maison', 'points', null, [[2021, 4100, 28], [2022, 4300, 31], [2023, null, 4], [2024, 4250, 22], [2025, 4400, 35]])
]);

/** Points without a change: too few sales at both ends; a year without a median leaves a gap. */
export const TENDANCE_POINTS = bloc([
	type('maison', 'points', null, [[2021, 2000, 12], [2022, 2100, 9], [2023, null, 3], [2024, 2250, 7], [2025, 2400, 14]]),
	type('appartement', 'echantillon_insuffisant', null, [[2021, null, 1], [2022, null, 0], [2023, null, 2], [2024, null, 0], [2025, null, 1]])
]);

/** Fewer than two years with a median, for both types. */
export const TENDANCE_INSUFFISANTE = bloc([
	type('maison', 'echantillon_insuffisant', null, [[2021, null, 2], [2022, 1800, 6], [2023, null, 0], [2024, null, 1], [2025, null, 3]]),
	type('appartement', 'echantillon_insuffisant', null, [[2021, null, 0], [2022, null, 0], [2023, null, 0], [2024, null, 0], [2025, null, 0]])
]);

/** A commune of the flat columns (no dated block), with the given yearly series. */
export function communeAvecTendance(tendance_prix: TendancePrix | null): CommuneSynthese {
	return {
		...COMMUNE_85047_CHALLANS,
		indicateurs_dvf: null,
		indicateurs_dpe: null,
		fenetre_mediane_dvf: '2024-12-31 → 2025-12-31',
		prix_m2_median_maison: 4400,
		nb_mutations_12m_maison: 35,
		prix_m2_median_appartement: 3920,
		nb_mutations_12m_appartement: 3200,
		tendance_prix
	};
}
