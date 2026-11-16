// Typed reading of the declared state of a flat /v1/commune column (D-47, D-51). Pure module,
// zero Svelte imports, leaf of the data layer.
//
// WHY: a flat column served `null` used to say nothing about why. The API now declares the state
// of each null column in `etats` (option A of the D-47 gate, 2026-09-24). This is the one place
// that reads it: a caller gets the value, the declared absence, or an explicit « undeclared
// absence » — never a `0` and never a default « Non renseigné » in place of a missing state.
// An undeclared absence is an API defect (UI-SPEC « Les quatre états servis »): the caller shows
// it as such, it does not disguise it.

import type { CommuneSynthese, EtatAbsenceColonne } from '$lib/api/commune';

/** Flat scalar columns. Object columns carry their own states (blocks) or their own year. */
export type ColonnePlate = Exclude<
	keyof CommuneSynthese,
	| 'code_insee'
	| 'nom_commune'
	| 'code_departement'
	| 'code_region'
	| 'indicateurs_dvf'
	| 'indicateurs_dpe'
	| 'loyers_appartement'
	| 'loyers_maison'
	| 'etats'
	| 'fenetre_mediane_dvf'
	| 'tendance_prix'
>;

export type EtatDeColonne =
	| { kind: 'valeur'; valeur: number | string | boolean }
	| { kind: 'absence'; etat: EtatAbsenceColonne; motif: string }
	| { kind: 'absence-non-declaree'; colonne: ColonnePlate };

/** The served value of `colonne`, or the reason the API declared for its absence. */
export function etatDeColonne(commune: CommuneSynthese, colonne: ColonnePlate): EtatDeColonne {
	const valeur = commune[colonne];
	if (valeur !== null && valeur !== undefined) return { kind: 'valeur', valeur };
	const declare = commune.etats?.[colonne];
	if (declare) return { kind: 'absence', etat: declare.etat, motif: declare.motif };
	return { kind: 'absence-non-declaree', colonne };
}
