// Yearly price series of a commune (`tendance_prix` of /v1/commune, decision of Ianis of 2026-11-11).
// Pure module, zero Svelte imports. Nothing is computed here: the change, the medians, the counts,
// the period and the two thresholds are the served ones; this module only words them.
//
// What the series says: the median price per m² of what was sold each closed calendar year. What it
// never says: the change of one dwelling, nor a forecast.
//
// Three served states per type of dwelling:
// - `evolution`: the change between the first and the last year is written;
// - `points`: the medians are drawn, no change is written (too few sales at one end);
// - `echantillon_insuffisant`: fewer than two years have a median; no tile, the theme row says it.

import type { CommuneSynthese, TendancePrix, TendancePrixType } from '$lib/api/commune';
import type { EntreeVolet, EtatKpi } from './commune-kpi';
import { formatBareNumber } from './formatters';

export const CHAMP_EVOLUTION = 'dvf.tendance.evolution_pct';
const CHAMP_PRIX = 'dvf.tendance.prix_m2_median';
const CHAMP_VENTES = 'dvf.tendance.nb_ventes';

export const DEFINITION_TENDANCE_ANNUELLE =
	'Prix médian au m² de ce qui s’est vendu chaque année\u00a0: ni l’évolution d’un logement, ni une prévision.';
export const OBJET_TENDANCE_ANNUELLE =
	'Ventes de la commune (mutations DVF), année civile par année civile, maisons et appartements séparés';
export const LIMITES_TENDANCE_ANNUELLE =
	'Le prix médian suit ce qui s’est vendu\u00a0: une année où se vendent surtout de petits logements neufs fait monter le prix au m² sans qu’aucun logement ait pris de valeur.';
export const LIBELLE_EVOLUTION_NON_CHIFFREE = 'Évolution non chiffrée';

const TITRES = { maison: 'Maisons', appartement: 'Appartements' } as const;

/**
 * The served block, or null when the instance serves none: dvf out of coverage (null), or a declared
 * absence of the series (not readable, no closed year). The page then keeps its former trend row.
 */
export function tendanceServie(commune: CommuneSynthese): TendancePrix | null {
	const servi = commune.tendance_prix;
	return servi && 'types' in servi ? servi : null;
}

/** « 2021 → 2025 », from the served bounds; a single closed year reads alone. */
export function periodeTendance(tendance: TendancePrix): string {
	return tendance.annee_debut === tendance.annee_fin
		? String(tendance.annee_debut)
		: `${tendance.annee_debut} → ${tendance.annee_fin}`;
}

export function methodeTendance(tendance: TendancePrix): string {
	return `Écart en % entre le prix médian au m² de ${tendance.annee_debut} et celui de ${tendance.annee_fin}. Il n’est chiffré que si chacune de ces deux années compte au moins ${tendance.seuil_evolution}\u00a0ventes\u202f; le prix médian d’une année est publié à partir de ${tendance.seuil_point}\u00a0ventes.`;
}

export function aideNonChiffree(tendance: TendancePrix): string {
	return `Trop peu de ventes pour chiffrer une évolution\u00a0: il en faut au moins ${tendance.seuil_evolution} en ${tendance.annee_debut} et en ${tendance.annee_fin}.`;
}

export function motifInsuffisant(tendance: TendancePrix): string {
	return `Moins de deux années comptent au moins ${tendance.seuil_point}\u00a0ventes entre ${tendance.annee_debut} et ${tendance.annee_fin}.`;
}

const ventes = (n: number): string => `${formatBareNumber(CHAMP_VENTES, n)}\u00a0${n === 1 ? 'vente' : 'ventes'}`;

/** The state of one type as a tile or a theme row shows it; the served state is kept. */
export function etatTendance(tendance: TendancePrix, type: TendancePrixType): Exclude<EtatKpi, { kind: 'absence-non-declaree' }> {
	if (type.etat === 'evolution' && type.evolution_pct != null) {
		const texte = formatBareNumber(CHAMP_EVOLUTION, type.evolution_pct);
		// A fall reads with the typographic minus (U+2212), as in the comparison table, never a hyphen.
		return { kind: 'valeur', texte: type.evolution_pct > 0 ? `+${texte}` : texte.replace(/^-/, '−'), unite: '%' };
	}
	if (type.etat === 'points') {
		const aide = aideNonChiffree(tendance);
		return { kind: 'absence', etat: 'insufficient_sample', motif: aide, effectif: null, libelle: LIBELLE_EVOLUTION_NON_CHIFFREE, aide };
	}
	const motif = motifInsuffisant(tendance);
	return { kind: 'absence', etat: 'insufficient_sample', motif, effectif: null, aide: motif };
}

export interface PointTendance {
	annee: number;
	/** Served median of the year, null when the year has none. */
	prix: number | null;
}

/** The points of the curve: every year of the period, in order; drawn from two medians. */
export function pointsTendance(type: TendancePrixType): PointTendance[] {
	if (type.etat === 'echantillon_insuffisant') return [];
	return type.annees.map((annee) => ({ annee: annee.annee, prix: annee.prix_m2_median }));
}

/** Text alternative of the curve: its first and last served medians. */
export function descriptionCourbe(points: readonly PointTendance[]): string {
	const connus = points.filter((point) => point.prix != null);
	const [premier, dernier] = [connus[0], connus.at(-1)];
	if (!premier || !dernier) return '';
	const prix = (point: PointTendance) => `${formatBareNumber(CHAMP_PRIX, point.prix!)}\u00a0€/m² en ${point.annee}`;
	return `Prix médian au m² par année\u00a0: ${prix(premier)}, ${prix(dernier)}.`;
}

/** One line per year of the panel: the served median and count, a year without a sale said as such. */
export function detailParAnnee(type: TendancePrixType): string {
	return type.annees
		.map((annee) => {
			if (annee.nb_ventes === 0) return `${annee.annee}\u00a0: aucune vente`;
			if (annee.prix_m2_median == null) return `${annee.annee}\u00a0: ${ventes(annee.nb_ventes)}, prix médian non publié`;
			return `${annee.annee}\u00a0: ${formatBareNumber(CHAMP_PRIX, annee.prix_m2_median)}\u00a0€/m², ${ventes(annee.nb_ventes)}`;
		})
		.join(' · ');
}

export const titreDuType = (type: TendancePrixType): string => TITRES[type.type_bien];

/** Entries of the panel that only the yearly series has: the detail behind the information button. */
export function entreesDetail(types: readonly TendancePrixType[]): EntreeVolet[] {
	return types.map((type) => ({ terme: `${titreDuType(type)}, année par année`, valeur: detailParAnnee(type) }));
}

/**
 * The types a tile reads: the most sold first (served order). A second type without two medians is
 * left to its theme row. When the first has none either, « L'essentiel » drops the tile as it drops
 * every tile without a value (decision of Ianis of 2026-11-11): the state is read on the theme rows.
 */
export function typesDeLaTuile(tendance: TendancePrix): TendancePrixType[] {
	return tendance.types.filter((type, index) => index === 0 || type.etat !== 'echantillon_insuffisant');
}
