// Status of a theme row, its key and its label, moved out of `adresse-rapport.ts` (2026-11-13): a leaf
// module, so a page that only shows a status badge (the home page) does not load the address report.

export type CleStatut = 'vigilance' | 'rassurant' | 'zero' | 'manque' | 'hors_couverture' | 'panne' | 'non_concerne' | 'en_cours';
export interface Statut {
	cle: CleStatut;
	texte: string;
}

export const LIBELLE_STATUT: Record<CleStatut, string> = {
	vigilance: 'Vigilance',
	rassurant: 'Favorable',
	zero: 'Zéro',
	manque: 'Non publié',
	hors_couverture: 'Hors couverture',
	panne: 'Source indisponible',
	non_concerne: 'Sans objet',
	// R-07: a slow read is not an outage. Wording proposed, to be validated by Ianis.
	en_cours: 'Lecture en cours'
};
