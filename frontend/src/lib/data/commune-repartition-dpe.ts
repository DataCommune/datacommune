// Distribution of the DPE classes of a commune (04.7-15 T2, Origami R9): one bar per class A→G and
// one reading sentence under the chart. Pure module, zero Svelte imports.
//
// NOTHING IS COMPUTED HERE THAT THE PAGE WOULD SHOW AS A FIGURE: the bars carry the served counts of
// `distribution.classes`, the sentence cites the served `classes_connues`, `modes` and `part_fg_pct`.
// The length of a bar is a presentation of the served count, never a displayed share.
//
// The chart exists only when the dated DPE block serves a value with at least one known class and
// its counts add up to the served `classes_connues`: otherwise the sentence would contradict the
// bars, and the theme rows keep the served state.
import type { CommuneSynthese, EnergyClass, IndicateursDpe, ResultatDpeIndicateur } from '$lib/api/commune';
import { formatBareNumber } from './formatters';
import { dateEnLettres } from './commune-indicators';

export const CLASSES: readonly EnergyClass[] = ['A', 'B', 'C', 'D', 'E', 'F', 'G'];

export interface BarreDpe {
	classe: EnergyClass;
	/** Served count of the class. */
	effectif: number;
	/** Served count, formatted. */
	texte: string;
	/** Width of the bar, 0 to 1, relative to the largest served count (presentation only). */
	longueur: number;
}

export interface RepartitionDpe {
	barres: BarreDpe[];
	/** The reading sentence, made of served values only. */
	lecture: string;
	/** Diagnostics without a known class, said apart and never drawn; null when none is served. */
	sansClasse: string | null;
	/** Served bounds of the establishment dates, spelled; null when one is unknown. */
	bornes: string | null;
}

const NB = ' ';
const NNB = ' ';

function diagnostics(n: number): string {
	return `${formatBareNumber('dpe.indicateurs.population.total', n)}${NB}${n === 1 ? 'diagnostic' : 'diagnostics'}`;
}

function liste(classes: readonly EnergyClass[]): string {
	return classes.length === 1 ? classes[0] : `${classes.slice(0, -1).join(', ')} et ${classes.at(-1)}`;
}

/** The served result of the dated DPE block, or null for a null or absent block or result. */
export function resultatDpeServi(commune: CommuneSynthese): ResultatDpeIndicateur | null {
	const block = commune.indicateurs_dpe;
	if (!block || !('version_contrat' in block)) return null;
	const resultat = (block as IndicateursDpe).resultat;
	return resultat.etat === 'value' ? (resultat as ResultatDpeIndicateur) : null;
}

/** The reading sentence: known classes, the most frequent served class(es), the served F/G share. */
function lecture(resultat: ResultatDpeIndicateur): string {
	const debut = `Sur ${diagnostics(resultat.population.classes_connues)} de classe connue`;
	const modes = resultat.modes;
	const effectifMode = diagnostics(resultat.distribution.classes[modes[0]]);
	const frequente = modes.length === 1
		? `la classe la plus fréquente est ${modes[0]}, avec ${effectifMode}`
		: `les classes les plus fréquentes, à égalité, sont ${liste(modes)}, avec ${effectifMode} chacune`;
	const part = resultat.part_fg_pct;
	const fg = part.etat === 'value'
		? `${NNB}; ${formatBareNumber('dpe.indicateurs.part_fg_pct', part.valeur)}${NB}% sont classés F ou G`
		: '';
	return `${debut}, ${frequente}${fg}.`;
}

function sansDate(n: number): string {
	return n > 0 ? ` ; ${diagnostics(n)} sans date connue` : '';
}

export function repartitionDpe(commune: CommuneSynthese): RepartitionDpe | null {
	const resultat = resultatDpeServi(commune);
	if (!resultat || resultat.population.classes_connues <= 0 || !resultat.modes.length) return null;
	// A missing class key is not a zero: the chart is withheld, never drawn with an invented bar.
	const effectifs = CLASSES.map((classe) => resultat.distribution.classes[classe]);
	if (effectifs.some((n) => typeof n !== 'number')) return null;
	const somme = effectifs.reduce((total, n) => total + n, 0);
	if (somme !== resultat.population.classes_connues) {
		if (import.meta.env.DEV) console.error(`[commune-repartition-dpe] classes servies (${somme}) ≠ classes connues (${resultat.population.classes_connues})`);
		return null;
	}
	const max = Math.max(...effectifs);
	const min = resultat.date_etablissement_min && dateEnLettres(resultat.date_etablissement_min);
	const fin = resultat.date_etablissement_max && dateEnLettres(resultat.date_etablissement_max);
	return {
		barres: CLASSES.map((classe, index) => ({
			classe,
			effectif: effectifs[index],
			texte: formatBareNumber('dpe.indicateurs.population.total', effectifs[index]),
			longueur: max > 0 ? effectifs[index] / max : 0
		})),
		lecture: lecture(resultat),
		sansClasse: resultat.distribution.inconnue > 0
			? `${diagnostics(resultat.distribution.inconnue)} sans classe connue ne ${resultat.distribution.inconnue === 1 ? 'figure' : 'figurent'} pas dans le graphique.`
			: null,
		// Observed bounds, not an exhaustive period (`bornes_etablissement_observees_non_exhaustives`).
		bornes: min && fin
			? `Dates d’établissement observées : du ${min} au ${fin}${sansDate(resultat.dates_etablissement_inconnues)}.`
			: null
	};
}
