// Readable street of the address fiche (04.8-03; UI-SPEC v4 § 7.5), ported from `voieLisible` of the
// mockup (`a/pages.mjs:77-80`). The BAN writes the street type with a capital and files the article at the
// end of the name (« Chemin Puits Fictif (du) »); the h1, the breadcrumb and the page title read it the
// way French writes it (« chemin du Puits Fictif »). Applied to what is shown, never to a datum that is
// re-exported (CSV, JSON).

const TYPES_DE_VOIE =
	'Rue|Boulevard|Avenue|Chemin|Place|Impasse|All[ée]e|Route|Quai|Cours|Square|Passage|Rond-Point|Chauss[ée]e|Sentier|Venelle|R[ée]sidence|Lotissement|Hameau|Lieu-dit|Esplanade|Promenade|Mail|Cit[ée]|Faubourg';

// The type ends on a letter test, not on `\b`: `\b` is ASCII-only, so « Cité » would never match.
const TYPE_AU_DEBUT = new RegExp(`^(\\d+\\s*(?:bis|ter|quater)?\\s+)?(${TYPES_DE_VOIE})(?![\\p{L}\\p{N}])`, 'u');

// « Type Nom (article) »: the article put back between the type and the name.
const ARTICLE_RANGE_A_LA_FIN = /^(\S+) (.+) \((du|de la|des|de l’|de l'|de|d’|d')\)$/;

/** « Chemin Puits Fictif (du) » → « chemin du Puits Fictif »; a name with no street type is returned as is. */
export function voieLisible(nomVoie: string): string {
	return nomVoie
		.replace(ARTICLE_RANGE_A_LA_FIN, (_, type: string, nom: string, article: string) => `${type} ${article}${/[’']$/.test(article) ? '' : ' '}${nom}`)
		.replace(TYPE_AU_DEBUT, (_, numero: string | undefined, type: string) => `${numero ?? ''}${type.toLowerCase()}`);
}
