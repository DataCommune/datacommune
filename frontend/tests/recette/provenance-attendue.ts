// Expected text of the provenance line of the similar-sales form (UI-007, plan 04.5-06), as the real
// recipe reads it (`norm`: no-break spaces as ordinary ones). Written here by hand, independently of
// the application (`qualificatifSelection`, `provenanceSurface`): the review of the corrections of
// 04.5-06 (MINOR, carried to 04.5-07) found that the recipe accepted any qualification between
// brackets. Each served reason of the selection (D-111) has its full expected text; a reason without
// one fails the recipe, named, rather than accepting whatever the page writes.

/** Qualification of each served reason of the most recent selection (04-DECISIONS-TEXTES rows 3, 4, 24). */
export const QUALIFICATIONS_ATTENDUES: Readonly<Record<string, string | null>> = {
	document_valide: null,
	expire: 'expiré',
	ancienne_methode: 'ancienne méthode',
	remplace: "remplacé selon l'ADEME",
	remplacement_incertain: 'remplacement non vérifié',
	validite_inconnue: 'validité non déterminée'
};

/** The line of a prefilled type (04-DECISIONS-TEXTES row 71). */
export const ORIGINE_TYPE_ATTENDUE = "Type d'après les DPE de cette adresse.";

/**
 * Full expected text of the line when a surface is prefilled from the document `numero` of the served
 * selection whose reason is `motif`; preceded by the line of the type when the type is prefilled too.
 */
export function provenanceAttendue(numero: string, motif: string, typePrerempli: boolean): string {
	if (!Object.hasOwn(QUALIFICATIONS_ATTENDUES, motif)) throw new Error(`motif de sélection sans texte attendu : ${motif}`);
	const qualification = QUALIFICATIONS_ATTENDUES[motif];
	const debut = `${typePrerempli ? `${ORIGINE_TYPE_ATTENDUE} ` : ''}Surface issue du DPE n° ${numero}, le plus récent de cette adresse`;
	return qualification ? `${debut} (${qualification}).` : `${debut}.`;
}
