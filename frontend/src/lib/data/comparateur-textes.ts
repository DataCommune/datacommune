// Wordings of the readable comparator (direction A, chosen by Ianis on 2026-11-10: « A est mieux, on y va »).
//
// PROPOSED ON 2026-11-10, TO BE REREAD BY IANIS BEFORE PUBLICATION. He chose the direction; every sentence
// below was carried on the orchestrator's reading of « on y va », not on his own words. They live here, in
// one place, so that a rereading changes one file. Numbers T1 to T12: the table of
// the internal note 2026-11-10-comparateur-lisibilite, § 7.
import { LIBELLES_MEDIANE } from './commune-indicators';

/** T1: what the comparator of communes gives, in one line, under its title while nothing is compared. */
export const PROMESSE_COMMUNES = 'Jusqu’à quatre communes côte à côte : prix au m², loyers, DPE, puis le reste par catégorie.';

/** T3: the line above the ready comparisons of the empty comparator of communes. */
export const TITRE_EXEMPLES = 'Ou partez d’un exemple';

/**
 * T3 (decision D3): ready comparisons. No commune with arrondissements (Paris, Lyon, Marseille): at the code
 * of the whole commune the rents, the vacancy, the equipments and the colleges are « Non renseigné », the
 * sources publish them by arrondissement only. Checked read-only in production on 2026-11-10. Never a
 * commune of the perimeter first hosted (D-34, design gate `perimetre-en-dur`).
 */
export const EXEMPLES_COMMUNES: readonly { libelle: string; lien: string }[] = [
	{ libelle: 'Toulouse et Montpellier', lien: '/comparer?communes=31555,34172' },
	{ libelle: 'Rennes et Bordeaux', lien: '/comparer?communes=35238,33063' }
];

/** T4 (decision D2, shape without values): title of the preview of the empty comparator. */
export const TITRE_APERCU = 'Ce que vous obtiendrez';

/** T4: the columns of the preview name no territory. */
export const COLONNES_APERCU = ['Commune A', 'Commune B'] as const;

/** The rows the preview draws: the first labels of « Repères », as the table words them. */
export const LIGNES_APERCU: readonly string[] = [
	LIBELLES_MEDIANE.maison,
	LIBELLES_MEDIANE.appartement,
	"Loyer d'annonce prédit appartement",
	'Classe DPE la plus fréquente'
];

/** T11: the number of rows a folded category holds. */
export const nombreDeLignes = (n: number) => (n > 1 ? `${n} lignes` : `${n} ligne`);

/** T7, T8: the sign shown for a value identical to, or different from, the first column. */
export const SIGNE_IDENTIQUE = '=';
export const SIGNE_DIFFERENT = '≠';
