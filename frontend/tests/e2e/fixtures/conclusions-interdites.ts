// Conclusions forbidden by 04-DPE-EVALUATION.md « Conclusions permises et interdites » (list
// confirmed by Ianis on 2026-10-06, C11 of 04-DECISIONS-COLONNES-DPE.md). Moved here from
// honnetete-publique.spec.ts (04-08) without change, so that the guard of the surfaces and the
// acceptance replay of 04-09 (synthetic and real) read ONE list.

export interface Motif {
	name: string;
	pattern: RegExp;
}

/**
 * Conclusions forbidden by 04-DPE-EVALUATION.md « Conclusions permises et interdites » (list
 * confirmed by Ianis on 2026-10-06, C11 of 04-DECISIONS-COLONNES-DPE.md) and by the « Contrat de
 * preuve » of 04-DPE-FIABILITE.md, on the address page, the document page, the export and both
 * comparators. The state-dependent one (« aucun DPE » for a partial, failed or out of coverage
 * inventory) is its own test below. A negation states the limit, it is not the conclusion: « pas
 * un logement identifié » (volet of the document page) is allowed. Mirrored on the served literals
 * by api/backend/tests/test_textes_publics_interdits.py.
 */
export const CONCLUSIONS_INTERDITES: ReadonlyArray<Motif> = [
	{
		name: 'identité du logement prouvée, vérifiée ou retrouvée',
		pattern:
			/(?<!pas un )(?<!aucun )logement identifié|identité vérifiée|DPE vérifié pour ce logement|correspond à votre logement|le bon DPE|DPE de ce logement/i
	},
	{
		name: 'fiabilité, précision ou confiance du rattachement logement',
		pattern: /fiabilité du rattachement|précision du rattachement|taux de fiabilité|indice de confiance/i
	},
	{
		name: 'taux de couverture ou de concordance présenté comme une précision',
		pattern:
			/\d+(?:[.,]\d+)?\s?%[^.\n]{0,60}(précision|exactitude|fiabilité|fiable|certitude)|(précision|exactitude|fiabilité|certitude)[^.\n]{0,60}\d+(?:[.,]\d+)?\s?%|\b176\s?\/\s?200\b/i
	},
	{
		name: 'choix automatique d’un document pour un logement',
		pattern: /DPE attribué|DPE retenu pour ce logement|DPE le plus probable/i
	},
	{
		name: 'exactitude générale tirée de la fidélité de l’import',
		pattern: /données vérifiées|DPE exact\b|surface exacte/i
	},
	{
		name: 'généralisation hors 44/85 ou à partir de petits corpus',
		pattern: /fiable en France|0 erreur constatée/i
	}
];

/** Every forbidden conclusion found in *texte* (name and matched words). */
export function conclusionsTrouvees(texte: string): string[] {
	return CONCLUSIONS_INTERDITES.flatMap((motif) => {
		const trouve = texte.match(motif.pattern);
		return trouve ? [`${motif.name} : « ${trouve[0]} »`] : [];
	});
}
