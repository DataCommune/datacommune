// What the site publishes for machines and for its legal notice (04.7-14, Origami R5, C5): the URL
// patterns of the public pages, the main sources with their licence, and the text of `/llms.txt`.
// The sources are those of the API source catalogue; the complete list, as served with the period of
// each dataset, is the « Données & méthode » page, never restated here. `/llms.txt` is plain text
// for programs: it keeps ordinary spaces, unlike the French typography of the pages.
import { EXEMPLES_FICHES } from '$lib/data/accueil';
import { CHEMIN_CE_QUE_NOUS_NE_MONTRONS_PAS } from '$lib/data/ce-que-nous-ne-montrons-pas';

// The examples of the patterns are those of the home page: its first example, compared with its second.
const [EXEMPLE, AUTRE_EXEMPLE] = EXEMPLES_FICHES;

export interface ModeleUrl {
	/** Path pattern as printed, with `{placeholders}`. */
	modele: string;
	/** Directory of the route under `src/routes` that serves it (checked by the test). */
	route: string;
	/** A real example, linked; none when no stable example exists. */
	exemple: string | null;
	indexee: boolean;
	description: string;
}

export const MODELES_URL: readonly ModeleUrl[] = [
	{
		modele: '/commune/{code_insee}',
		route: '(marketing)/commune/[code]',
		exemple: EXEMPLE.commune,
		indexee: true,
		description: 'Fiche d’une commune : prix, documents DPE, logement, risques, chacun avec sa source et sa période.'
	},
	{
		modele: '/commune/{code_insee}.json',
		route: '(marketing)/commune/[code].json',
		exemple: `${EXEMPLE.commune}.json`,
		indexee: false,
		description:
			'Données de la fiche d’une commune en JSON, sans clé : la même lecture que la page, avec ses sources et leurs licences.'
	},
	{
		modele: '/departement/{code}',
		route: '(marketing)/departement/[code]',
		exemple: '/departement/44',
		// Not « indexée »: whether a department is indexed depends on its served prices, said in the
		// description, and the flag only appends the unconditional « Indexée. » to the line.
		indexee: false,
		description:
			'Page d’un département : population, prix médian au m² des appartements et des maisons, part des DPE classés F ou G, communes principales. Indexée seulement si les prix du département sont servis.'
	},
	{
		modele: '/adresse/{cle_ban}',
		route: '(marketing)/adresse/[id]',
		exemple: EXEMPLE.href,
		indexee: false,
		description:
			'Fiche d’une adresse (clé de la Base adresse nationale) : bâti, risques, prix, quartier. Non indexée, car elle montre des ventes DVF.'
	},
	{
		modele: '/dpe/{numero}',
		route: '(marketing)/dpe/[numero]',
		exemple: null,
		indexee: false,
		description: 'Un document DPE publié par l’ADEME, tel que publié.'
	},
	{
		modele: '/communes?q={texte}',
		route: '(marketing)/communes',
		exemple: `/communes?q=${EXEMPLE.ville}`,
		indexee: false,
		description: 'Recherche d’une commune ou d’une adresse.'
	},
	{
		modele: '/comparer?communes={code_insee},{code_insee}',
		route: '(marketing)/comparer',
		exemple: `/comparer?communes=${AUTRE_EXEMPLE.insee},${EXEMPLE.insee}`,
		indexee: false,
		description: 'Comparaison de deux à quatre communes.'
	}
];

export interface SourcePrincipale {
	/** Key of the dataset in the source catalogue of the API (`app/common/source_catalog.py`). */
	cle: string;
	donnees: string;
	producteur: string;
	licence: string;
	/** Mention required by the producer, when it is more than the name of the source. */
	mention?: string;
}

/**
 * Main sources and their licence, as the source catalogue of the API declares them (checked by the
 * test): the « Données & méthode » page serves the same catalogue, so the two never disagree.
 */
export const SOURCES_PRINCIPALES: readonly SourcePrincipale[] = [
	{ cle: 'dvf', donnees: 'Ventes immobilières (DVF)', producteur: 'DGFiP / Etalab', licence: 'Licence Ouverte 2.0' },
	{ cle: 'dpe', donnees: 'Documents DPE', producteur: 'ADEME', licence: 'Licence Ouverte 2.0' },
	{ cle: 'ban', donnees: 'Adresses (BAN)', producteur: 'DINUM / IGN', licence: 'Licence Ouverte 2.0' },
	{ cle: 'cadastre', donnees: 'Cadastre', producteur: 'DGFiP / Etalab', licence: 'Licence Ouverte 1.0' },
	{ cle: 'bdnb', donnees: 'Bâtiments (BDNB)', producteur: 'CSTB', licence: 'Licence Ouverte 2.0' },
	{ cle: 'georisques', donnees: 'Risques (Géorisques)', producteur: 'BRGM / MTE', licence: 'Licence Ouverte 2.0' },
	{ cle: 'urbanisme', donnees: 'Urbanisme (API Carto GPU)', producteur: 'IGN', licence: 'Licence Ouverte 1.0' },
	{ cle: 'demographie', donnees: 'Recensement de la population', producteur: 'INSEE', licence: 'Licence Ouverte 2.0' },
	{ cle: 'connectivite', donnees: 'Connexion internet', producteur: 'Arcep', licence: 'Licence Ouverte 2.0' },
	{
		cle: 'loyers',
		donnees: 'Carte des loyers',
		producteur: 'ANIL / MTE',
		licence: 'Licence Ouverte 2.0',
		mention: 'Estimations ANIL, à partir des données du Groupe SeLoger et de leboncoin'
	},
	{ cle: 'gares', donnees: 'Gares', producteur: 'SNCF', licence: 'ODbL 1.0', mention: 'partage à l’identique d’une base dérivée' }
];

export const ATTRIBUTION =
	'Citez la source de chaque valeur (son producteur) et la date de sa dernière mise à jour, comme le demande la Licence Ouverte, et respectez la licence de ce producteur ; mentionner DataCommune, avec le lien de la page, est bienvenu sans être une condition.';

/**
 * Address of the public source repository (D-137), linked by the footer, the legal notice, the CGU and the
 * developer page. Served only from a commit already on its `master` (D-135, guard of public-deploy.sh).
 */
export const CODE_SOURCE_URL = 'https://github.com/DataCommune/datacommune';

/** The URL model *modele*, as printed in `MODELES_URL` (the developer page reads its examples there). */
export function modeleUrl(modele: string): ModeleUrl {
	const trouve = MODELES_URL.find((m) => m.modele === modele);
	if (!trouve) throw new Error(`Modèle d'URL inconnu : ${modele}`);
	return trouve;
}

/**
 * Text of `/llms.txt` (llmstxt.org format): Markdown, absolute URLs on *site*, API on *api*.
 * *clesOuvertes*: whether a visitor can get an API key on this instance (the account space is built).
 */
export function texteLlms(site: string, api: string, clesOuvertes = false): string {
	const lignes: string[] = [
		'# DataCommune',
		'',
		'> Données publiques françaises par lieu. Pour une commune ou une adresse : prix de vente (DVF), documents DPE (ADEME), risques, logement et cadre de vie. Chaque valeur porte sa source, sa période et son échelle. Aucune note globale. Une valeur nulle, une donnée manquante, un territoire hors couverture et une source en panne sont toujours distingués.',
		'',
		`Le territoire servi aujourd’hui est décrit sur ${site}/couverture : hors de ce territoire, le site ne sait pas, ce qui ne vaut jamais zéro.`,
		'',
		'## Pages et modèles d’URL',
		'',
		...MODELES_URL.map(
			(m) =>
				`- ${m.exemple ? `[${m.modele}](${site}${m.exemple})` : `${m.modele}`} : ${m.description}${m.indexee ? ' Indexée.' : ''}`
		),
		'',
		'## Sources et licences',
		'',
		`- [Données & méthode](${site}/datasets) : chaque jeu de données servi, son producteur, sa licence, sa période et sa fraîcheur.`,
		...SOURCES_PRINCIPALES.map(
			(s) => `- ${s.donnees} : ${s.producteur}, ${s.licence}${s.mention ? ` (mention : ${s.mention})` : ''}.`
		),
		'',
		'## Attribution',
		'',
		ATTRIBUTION,
		'',
		'## Limites',
		'',
		`- [Ce que nous ne montrons pas, et pourquoi](${site}${CHEMIN_CE_QUE_NOUS_NE_MONTRONS_PAS}) : ni acheteur ni vendeur, pas de prix pour une vente isolée récente, aucun occupant ; les fiches d’adresse ne sont pas indexées.`,
		`- [Couverture et limites](${site}/couverture)`,
		'',
		'## API',
		'',
		`- [Développeurs](${site}/developpeurs) : export JSON sans clé, contrat de l’API. L’API demande une clé${clesOuvertes ? '.' : ' ; les clés ne sont pas encore ouvertes.'}`,
		`- [Spécification OpenAPI](${api}/openapi.json) : chaque propriété porte sa source (extension x-source).`,
		''
	];
	return lignes.join('\n');
}
