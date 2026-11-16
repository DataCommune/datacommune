// Acronyms spelled out at their first occurrence (04.8-08, D-17, D-31; UI-SPEC v4 § 7.5). A page passes,
// in reading order, the prose segments of its `main` it renders (titles, sentences, notes), never a value,
// an identifier, a badge word, a table cell or a URL; the function returns the same segments with each
// acronym of SIGLES followed by its full form at its first occurrence, « DPE (diagnostic de performance
// énergétique) », unless the reader has already read that full form, in an earlier segment or in the
// segment of the occurrence itself; a full form said further down does not count, the reader meets the
// acronym first. An acronym that closes a parenthesis takes its full form after a comma. It holds no state and reads
// no Svelte context, so server rendering and hydration get the same text. The error page and `(app)` call
// it on one segment at a time (`developperSigles([texte])[0]`): still correct, possibly repeated.
// The list is the 25 entries of the mock-up (`pages.mjs:30-56`), then the additions of the socle.

export interface Sigle {
	readonly sigle: string;
	readonly plein: string;
	/** The full form, or a wording that says it, already on the page. */
	readonly deja: RegExp;
}

export const SIGLES: readonly Sigle[] = [
	{ sigle: 'BAN', plein: 'Base Adresse Nationale', deja: /Base Adresse Nationale/i },
	{ sigle: 'PLU', plein: 'plan local d’urbanisme', deja: /plan local d.urbanisme/i },
	{
		sigle: 'DPE',
		plein: 'diagnostic de performance énergétique',
		deja: /diagnostics? (de performance )?énergétiques?|performance énergétique/i
	},
	{ sigle: 'RNB', plein: 'Référentiel national des bâtiments', deja: /Référentiel national des bâtiments/i },
	{ sigle: 'BDNB', plein: 'Base de données nationale des bâtiments', deja: /Base de données nationale des bâtiments/i },
	{ sigle: 'ANIL', plein: 'Agence nationale pour l’information sur le logement', deja: /Agence nationale pour l.information/i },
	{ sigle: 'ATMO', plein: 'indice national de qualité de l’air', deja: /indice national de qualité de l.air/i },
	{ sigle: 'DVF', plein: 'Demandes de valeurs foncières', deja: /Demandes de valeurs foncières/i },
	{ sigle: 'ADEME', plein: 'Agence de la transition écologique', deja: /Agence de la transition écologique/i },
	{
		sigle: 'IGN',
		plein: 'Institut national de l’information géographique et forestière',
		deja: /Institut national de l.information géographique/i
	},
	{ sigle: 'DGFiP', plein: 'Direction générale des finances publiques', deja: /Direction générale des finances publiques/i },
	{ sigle: 'BRGM', plein: 'Bureau de recherches géologiques et minières', deja: /Bureau de recherches géologiques/i },
	{ sigle: 'SSMSI', plein: 'service statistique de la sécurité intérieure', deja: /statistique de la sécurité intérieure/i },
	{ sigle: 'ARS', plein: 'agence régionale de santé', deja: /agence régionale de santé/i },
	{ sigle: 'OMS', plein: 'Organisation mondiale de la santé', deja: /Organisation mondiale de la santé/i },
	{ sigle: 'GPU', plein: 'Géoportail de l’urbanisme', deja: /Géoportail de l.urbanisme/i },
	{
		sigle: 'ICPE',
		plein: 'installations classées pour la protection de l’environnement',
		deja: /installations classées pour la protection/i
	},
	{ sigle: 'LOVAC', plein: 'fichier des logements vacants', deja: /fichier des logements vacants/i },
	{ sigle: 'MTE', plein: 'ministère de la Transition écologique', deja: /ministère de la Transition écologique/i },
	{ sigle: 'WFS', plein: 'service web de données géographiques', deja: /service web de données géographiques/i },
	{ sigle: 'GTFS', plein: 'format ouvert des horaires de transport', deja: /format ouvert des horaires/i },
	{ sigle: 'ODbL', plein: 'licence ouverte de bases de données', deja: /licence ouverte de bases de données/i },
	{ sigle: 'CGU', plein: 'conditions générales d’utilisation', deja: /conditions générales d.utilisation/i },
	// Additions of the socle (2026-11-09): official full forms only. ANFR and ANAH are spelled out on today's
	// address fiche, in its lines of sources. The others are bare today only in labels, buttons and cells,
	// which no page passes: the page that first passes one in prose checks where its full form lands.
	// The candidates left out, and why, are in the internal note 2026-11-10-socle-mode-d-emploi.
	{ sigle: 'GES', plein: 'gaz à effet de serre', deja: /gaz à effet de serre/i },
	{ sigle: 'VEFA', plein: 'vente en l’état futur d’achèvement', deja: /ventes? en l.état futur d.achèvement/i },
	{ sigle: 'CNIL', plein: 'Commission nationale de l’informatique et des libertés', deja: /Commission nationale de l.informatique/i },
	{ sigle: 'EPCI', plein: 'établissement public de coopération intercommunale', deja: /établissements? publics? de coopération intercommunale/i },
	{ sigle: 'TFPB', plein: 'taxe foncière sur les propriétés bâties', deja: /taxe foncière sur les propriétés bâties/i },
	{ sigle: 'TEOM', plein: 'taxe d’enlèvement des ordures ménagères', deja: /taxe d.enlèvement des ordures ménagères/i },
	{ sigle: 'ANFR', plein: 'Agence nationale des fréquences', deja: /Agence nationale des fréquences/i },
	{ sigle: 'ANAH', plein: 'Agence nationale de l’habitat', deja: /Agence nationale de l.habitat/i },
	{
		sigle: 'DRIHL',
		plein: 'Direction régionale et interdépartementale de l’hébergement et du logement',
		deja: /Direction régionale et interdépartementale de l.hébergement/i
	},
	{ sigle: 'DINUM', plein: 'Direction interministérielle du numérique', deja: /Direction interministérielle du numérique/i },
	{ sigle: '‰', plein: 'pour mille', deja: /pour mille/i },
	{ sigle: '°f', plein: 'degré français de dureté de l’eau', deja: /degré français/i }
];

// A whole word only: not glued to a letter, a digit or `_` (BANQUE, DPE_2), not inside a path or a host
// (`/DVF`, `.BAN`, `-DPE`, `#CGU`). The two symbols follow a number, so only the trailing guards apply to them.
const motif = (sigle: string): RegExp =>
	/^\p{L}/u.test(sigle)
		? new RegExp(`(?<![\\p{L}\\p{N}_/.\\-@=?&#])${sigle}(?![\\p{L}\\p{N}_/\\-])`, 'u')
		: new RegExp(`${sigle}(?![\\p{L}\\p{N}_])`, 'u');

const MOTIFS = new Map(SIGLES.map(({ sigle }) => [sigle, motif(sigle)]));

// A URL in a segment is left as it is: the search skips its span.
const URL = /\bhttps?:\/\/\S+/gu;

function remplacerHorsUrl(texte: string, re: RegExp, par: (trouve: string, suite: string) => string): string | null {
	let debut = 0;
	for (const m of [...texte.matchAll(URL), null]) {
		const fin = m ? m.index : texte.length;
		const morceau = texte.slice(debut, fin);
		const trouve = re.exec(morceau);
		if (trouve) {
			const i = debut + trouve.index;
			const suite = texte.slice(i + trouve[0].length);
			return texte.slice(0, i) + par(trouve[0], suite) + suite;
		}
		if (m) debut = m.index + m[0].length;
	}
	return null;
}

export function developperSigles(segments: readonly string[]): string[] {
	const sortie = [...segments];
	for (const { sigle, plein, deja } of SIGLES) {
		const re = MOTIFS.get(sigle)!;
		for (let i = 0; i < sortie.length; i++) {
			// The full form already read, in this segment or an earlier one: nothing to add.
			if (deja.test(segments[i])) break;
			// An ordinary space before the parenthesis: the form of § 7.5, not a « name (code) » pair. At the
			// end of a parenthesis, after a comma: « (…, de la DGFiP, Direction générale des finances publiques) ».
			const remplace = remplacerHorsUrl(sortie[i], re, (trouve, suite) =>
				suite.startsWith(')') ? trouve + ', ' + plein : trouve + ' (' + plein + ')'
			);
			if (remplace !== null) {
				sortie[i] = remplace;
				break;
			}
		}
	}
	return sortie;
}

/**
 * The same, for a page whose segments are rendered by several components: the page lists, in reading
 * order, each prose segment under a key; an absent segment (null, empty) is skipped. A component reads
 * its text by key and falls back to the raw text when it is rendered without the table.
 */
export function developperParCle(
	entrees: readonly (readonly [cle: string, texte: string | null | undefined])[]
): ReadonlyMap<string, string> {
	const presentes = entrees.filter((e): e is readonly [string, string] => typeof e[1] === 'string' && e[1] !== '');
	const sortie = developperSigles(presentes.map(([, texte]) => texte));
	return new Map(presentes.map(([cle], i) => [cle, sortie[i]]));
}

// A quotation « … »: an input quoted back, a state word, a mention a producer imposes word for word.
const CITATION = /(«[^»]*»)/u;

/**
 * developperParCle, keeping each quotation « … » as it is: only the text around it is passed, so an
 * acronym inside a quotation is neither spelled out nor counted as the first occurrence.
 */
export function developperHorsCitations(
	entrees: readonly (readonly [cle: string, texte: string | null | undefined])[]
): ReadonlyMap<string, string> {
	const decoupees = entrees.map(([cle, texte]) => [cle, texte ? texte.split(CITATION) : []] as const);
	const sortie = developperParCle(
		decoupees.flatMap(([cle, morceaux]) => morceaux.map((m, i) => [`${cle}#${i}`, i % 2 ? null : m] as const))
	);
	return new Map(
		decoupees
			.filter(([, morceaux]) => morceaux.length > 0)
			.map(([cle, morceaux]) => [cle, morceaux.map((m, i) => sortie.get(`${cle}#${i}`) ?? m).join('')])
	);
}

/**
 * For checks: the text behind a spelled-out one. Removes, once per acronym, the full form added right after
 * it (« DPE (diagnostic…) » or « DGFiP, Direction… » at the end of a parenthesis); any other text is kept,
 * so a full form added anywhere else still shows.
 */
export function sansDeveloppement(texte: string): string {
	return SIGLES.reduce(
		(t, { sigle, plein }) => t.replace(sigle + ' (' + plein + ')', sigle).replace(sigle + ', ' + plein + ')', sigle + ')'),
		texte
	);
}
