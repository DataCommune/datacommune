import type { LoyerPrediction } from '$lib/api/commune';
import type { KvRow } from '$lib/types/fiche';

const fmt = (v: number | null | undefined, digits = 2): string =>
	v == null ? 'inconnu' : new Intl.NumberFormat('fr-FR', { maximumFractionDigits: digits }).format(v);

/** Context remains in the normal rows, including CSV/print exports. No trend is inferred. */
export function contexteLoyer(
	type: 'appartement' | 'maison',
	detail: LoyerPrediction | null | undefined,
	value: number | null
): KvRow[] {
	if (value == null && detail == null) return [];
	if (detail?.charges_comprises == null || detail?.trimestre_reference == null) {
		return [{
			kind: 'context',
			state: 'not_reported',
			label: `Contexte loyer ${type}`,
			value: 'Précisions inconnues : charges, bien type, trimestre, maille et qualité. Ces précisions ne sont pas disponibles pour cette donnée.',
			muted: true
		}];
	}
	const scope = ({ commune: 'Commune / arrondissement', epci: 'EPCI', maille: 'Maille', maile: 'Maille' } as Record<string, string>)[detail.type_prediction ?? ''] ?? 'Maille inconnue';
	const prudence = (detail.nb_observations_commune != null && detail.nb_observations_commune < 30) ||
		(detail.r2_ajuste != null && detail.r2_ajuste < 0.5);
	return [
		{
			kind: 'context',
			state: 'value',
			label: `Bien type et période (${type})`,
			value: `T${detail.trimestre_reference} ${detail.annee} · ${detail.meuble === false ? 'non meublé' : detail.meuble === true ? 'meublé' : 'ameublement inconnu'} · ${fmt(detail.surface_reference_m2)} m² · ${fmt(detail.surface_piece_reference_m2)} m²/pièce · ${detail.charges_comprises ? 'charges comprises' : 'hors charges'}`
		},
		{
			kind: 'context',
			state: 'value',
			label: `Maille et qualité (${type})`,
			value: `${scope}, zone ${detail.id_zone ?? 'inconnue'} · ${fmt(detail.nb_observations_commune, 0)} obs. commune / ${fmt(detail.nb_observations_maille, 0)} obs. maille · intervalle de prédiction à 95 % : ${fmt(detail.loyer_m2_bas)}–${fmt(detail.loyer_m2_haut)} €/m²/mois · R² ajusté ${fmt(detail.r2_ajuste, 3)}.${prudence ? ' Estimation à interpréter avec prudence.' : ''} Aucune évolution inter-cartes déduite.`
		}
	];
}
