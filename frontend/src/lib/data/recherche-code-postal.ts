// State of a postal-code search (CR-01, SEM-01, D-44). Pure module — zero Svelte imports, leaf
// module, like `etats-libelles.ts`.
//
// The API serves `meta.hebergement.ban` for every five-digit `q`: out of coverage with its reason
// when the departement named by the postal prefix is not hosted, hosted with the departements it
// is served for otherwise. This module only reads that metadata; the screens consult it when a
// search answers nothing (the results always win over the meta: a hosted commune at a
// neighbouring prefix is listed, never hidden behind a coverage message).
//
// Out of coverage is never worded as a zero, and a zero names the departements where the postal
// code was looked up — read from the answer, never written here (D-34).

import type { DatasetHosting } from '$lib/api/admin';
import { LIBELLES_MOTIFS } from './etats-libelles';

const CODE_POSTAL = /^\d{5}$/;

/**
 * Title of a postal-code search the instance cannot answer for this territory. Worded with the
 * out-of-coverage vocabulary of the glossary, never with the outage one (« disponible »,
 * « indisponible » : 03-17 T-7, V14).
 */
export const TITRE_CODE_POSTAL_HORS_COUVERTURE = 'Code postal hors couverture';

/** Live-region announcement of the same state (A12): the title itself. */
export const ANNONCE_CODE_POSTAL_HORS_COUVERTURE = TITRE_CODE_POSTAL_HORS_COUVERTURE;

/** Link to the coverage page, same label as the out-of-coverage notice of the fiche (V15). */
export const LIEN_COUVERTURE = 'Voir la couverture des données';

/** Advice given with every empty postal-code search: the name search covers every commune. */
export const CONSEIL_RECHERCHE_PAR_NOM = 'Cherchez la commune par son nom.';

export type CouvertureCodePostal =
	| { kind: 'hors-couverture'; phrase: string | null }
	| { kind: 'bornee'; departements: string[] }
	| null;

/** Metadata of a search answer, as `searchCommunes` returns it. */
type MetaRecherche = { hebergement?: Record<string, DatasetHosting> | null } | null | undefined;

/** A five-digit query, spaces around it ignored (same rule as the API). */
export function estCodePostal(q: string): boolean {
	return CODE_POSTAL.test(q.trim());
}

/**
 * Coverage of a postal-code search, read from the served `hebergement.ban`:
 *   - `hors-couverture`: the phrase of its reason (`null` for a reason outside the contract);
 *   - `bornee`: the departements where the postal code was looked up, in the served order;
 *   - `null`: not a postal code, no BAN coverage served, or no departement to name.
 */
export function couvertureCodePostal(q: string, meta: MetaRecherche): CouvertureCodePostal {
	if (!estCodePostal(q)) return null;
	const ban = meta?.hebergement?.ban;
	if (!ban) return null;
	if (ban.etat === 'hors_couverture') {
		const motif = ban.motif;
		const phrase =
			motif !== null && Object.hasOwn(LIBELLES_MOTIFS, motif) ? LIBELLES_MOTIFS[motif].phrase : null;
		return { kind: 'hors-couverture', phrase };
	}
	if (ban.etat === 'heberge' && ban.departements.length > 0) {
		return { kind: 'bornee', departements: [...ban.departements] };
	}
	return null;
}

const NB = ' ';

/** Departements named one by one at most; beyond, counted (Ianis, 09/11, as `phraseInconnu`). */
const DEPARTEMENTS_NOMMES_AU_PLUS = 5;

/**
 * « La recherche par code postal porte sur : {libellés}. », in the served order; beyond five
 * departements, « … porte sur {n} départements. »: with the whole country served the sentence listed
 * 101 names (R-03).
 */
export function phraseRechercheBornee(
	departements: string[],
	nomDepartement: (code: string) => string
): string {
	if (departements.length > DEPARTEMENTS_NOMMES_AU_PLUS) {
		return `La recherche par code postal porte sur ${departements.length}${NB}départements.`;
	}
	return `La recherche par code postal porte sur : ${departements.map(nomDepartement).join(', ')}.`;
}

/**
 * 75000, 13000 and 69000 are what one types for Paris, Marseille and Lyon; none exists, each
 * arrondissement has its own (R-03). Wording validated by Ianis on 2026-11-10.
 */
export const CONSEIL_CODE_POSTAL_PLM =
	'Paris, Lyon et Marseille ont un code postal par arrondissement : essayez 75011, 69003 ou 13001, ou tapez le nom de la ville.';
const CODES_POSTAUX_PLM_INEXISTANTS = new Set(['75000', '13000', '69000']);

/** Advice of an empty postal-code search: the arrondissement one for those three codes only. */
export function conseilCodePostal(q: string): string {
	return CODES_POSTAUX_PLM_INEXISTANTS.has(q.trim()) ? CONSEIL_CODE_POSTAL_PLM : CONSEIL_RECHERCHE_PAR_NOM;
}
