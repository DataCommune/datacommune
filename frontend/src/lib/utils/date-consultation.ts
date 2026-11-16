/**
 * Date de consultation imprimée sur les deux fiches publiques (D-12, plan 50-07).
 *
 * POURQUOI « consulté » ET NON « imprimé » : la date est calculée au chargement du loader, pas au
 * moment du `window.print()`. Un opérateur peut ouvrir la fiche puis imprimer plus tard dans la
 * même session — écrire « Imprimé le » serait alors faux, tandis que « Consulté le » décrit
 * exactement ce que la valeur mesure : l'instant où les données affichées ont été lues.
 *
 * POURQUOI ELLE N'EST JAMAIS UN MILLÉSIME : cette date qualifie la CONSULTATION, jamais la donnée.
 * Les millésimes des données vivent dans le bloc final sources/millésimes, à un autre endroit du
 * document et sous un autre libellé (D-12) — les deux ne doivent jamais être confondus.
 *
 * Jour lu en Europe/Paris, quel que soit l'endroit où le code s'exécute : le loader tourne en UTC au
 * SSR puis dans la zone du visiteur à la navigation client. Une zone fixe donne une seule valeur pour
 * une même consultation, et lire UTC affichait la veille entre minuit et 1 ou 2 h (4.9, C8.6).
 */

const JOUR_PARIS = new Intl.DateTimeFormat('fr-FR', {
	timeZone: 'Europe/Paris',
	day: '2-digit',
	month: '2-digit',
	year: 'numeric'
});

/**
 * Renders `d` as JJ/MM/AAAA. Defaults to the current instant.
 *
 * Throws on an invalid Date rather than rendering `NaN/NaN/NaN` or `Invalid Date`: this string is
 * printed on a public document, and a broken placeholder there reads as a data defect. Failing at
 * the call site makes the bug visible where it can still be fixed.
 */
export function dateConsultationFr(d: Date = new Date()): string {
	if (Number.isNaN(d.getTime())) {
		throw new Error('dateConsultationFr: date invalide — aucune date de consultation à imprimer');
	}
	return JOUR_PARIS.format(d);
}
