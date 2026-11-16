import { readFileSync } from 'node:fs';
import { avecVentesDeLaParcelle } from './adresse-ventes';
import { cheminRapport, rapportAvecThemes, REQUETE_RAPPORT } from './rapport';

// Synthetic sources of the phase 4 public surfaces (04-08): the address page, the DPE document
// page, the two comparators and the typed results page, on the served shapes already locked by
// their own specs (adresse-fiche, dpe-document, comparer-natures, adresse-recherche). Shared by the
// guards that sweep every surface — editorial (`honnetete-publique`), design (`accent-unique`),
// quality matrix and grid — so that a guard reads the page a visitor gets, not a stub of it.
// Every route returns the exact expectations of the local source; reads beyond the SSR load
// (hover preload, a second page load in the same test) are bounded, never required.

export type Reply = { status: number; body: unknown };
export interface Expectation {
	method: 'GET';
	pathname: string;
	query: Record<string, string>;
	minCalls: number;
	maxCalls: number;
	responses: Reply[];
}

export const BAN_ID = '85191_0420_00012';
export const NUMERO = '2485E0000003C';
export const LIBELLE = '12 Rue des Lilas, 85000 La Roche-sur-Yon';
const LECTURES_MAX = 6;

const SOURCE_BAN = { nom: 'Base Adresse Nationale', url: 'https://adresse.data.gouv.fr', licence: 'Licence Ouverte 2.0', producteur: 'IGN' };
const SOURCE_DPE = { cle: 'dpe', nom: 'Diagnostics de performance énergétique (DPE)', producteur: 'ADEME', url: 'https://data.ademe.fr/datasets/dpe03existant' };
const SOURCE_DVF = { cle: 'dvf', nom: 'Demandes de valeurs foncières (DVF)', producteur: 'DGFiP / Etalab', url: 'https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres-geolocalisees' };
const SOURCE_GEORISQUES = { cle: 'georisques', nom: 'Géorisques', producteur: 'BRGM / MTE', url: 'https://www.georisques.gouv.fr' };

const IDENTITE = {
	etat: 'etablie',
	ban_id: BAN_ID,
	libelle: LIBELLE,
	numero: '12',
	rep: null,
	nom_voie: 'Rue des Lilas',
	code_postal: '85000',
	commune: { code_insee: '85191', nom: 'La Roche-sur-Yon' },
	departement: { code: '85', nom: 'Vendée' },
	parcelle: { etat: 'unique', id_parcelle: '85191000AB0012', nombre_candidats: 1, rattachement: 'parcelle_du_point', motif: null }
};

function valeur(partiel: Record<string, unknown>) {
	return { field_id: 'x', etat: 'value', valeur: null, unite: null, periode: null, motif: null, source: null, rattachement: null, reference: null, ...partiel };
}

const UNIQUE = {
	etat: 'unique',
	motif: 'document_valide',
	date: '2024-03-12',
	document: { numero: NUMERO, etiquette_dpe: 'C', etiquette_ges: 'B', date_etablissement: '2024-03-12' },
	documents_ex_aequo: []
};

const REFERENCE = { valeur: 2500, unite: 'euro_per_m2', territoire: { code_insee: '85191', nom: 'La Roche-sur-Yon' }, source: SOURCE_DVF, millesime: '2021-2025', url: SOURCE_DVF.url };

const REPERES = {
	dpe: {
		...valeur({ field_id: 'adresse.dpe.nombre_declares', valeur: 1, unite: 'none', source: SOURCE_DPE, rattachement: 'adresse_saisie_diagnostiqueur' }),
		selection: UNIQUE
	},
	ventes: {
		nombre: valeur({ field_id: 'adresse.dvf.ventes_parcelle.nombre', valeur: 2, unite: 'count', periode: '2021-2025', source: SOURCE_DVF, rattachement: 'parcelle_du_point' }),
		derniere: valeur({ field_id: 'adresse.dvf.ventes_parcelle.derniere', valeur: '2025-11-20', unite: 'none', periode: '2021-2025', source: SOURCE_DVF, rattachement: 'parcelle_du_point' })
	},
	argiles: valeur({ field_id: 'adresse.risques.argiles_au_point', valeur: 'Exposition moyenne', unite: 'none', periode: '2026-10-02', source: SOURCE_GEORISQUES, rattachement: 'au_point' }),
	reference_commune: {
		maisons: valeur({ field_id: 'adresse.reference_commune.prix_median_maison', valeur: 2500, unite: 'euro_per_m2', periode: '2021-2025', source: SOURCE_DVF, rattachement: 'commune_reference', reference: REFERENCE }),
		appartements: valeur({ field_id: 'adresse.reference_commune.prix_median_appartement', valeur: 1980, unite: 'euro_per_m2', periode: '2021-2025', source: SOURCE_DVF, rattachement: 'commune_reference', reference: { ...REFERENCE, valeur: 1980 } })
	}
};

/** The D-110 finding as served by 04-19 when its five criteria hold (adresse-fiche.spec.ts). */
export const CONSTAT_D110 = {
	criteres: ['maison', 'document_unique_cle', 'concordance_stricte', 'batiment_unique_parcelle_du_point', 'annexes_exclues_usage_inconnu_compte'],
	sources: [SOURCE_DPE, { cle: 'batiments', nom: 'BD TOPO — Bâtiments', producteur: 'IGN', url: 'https://geoservices.ign.fr/bdtopo' }]
};

/** One declared document of a house: the page where the D-110 finding may or may not be served. */
function documents(constat: typeof CONSTAT_D110 | null) {
	return {
		etat: 'value',
		motif: null,
		source: SOURCE_DPE,
		total: 1,
		page: 1,
		pages: 1,
		par_page: 10,
		generation: '0123456789abcdef',
		distribution: { A: 0, B: 0, C: 1, D: 0, E: 0, F: 0, G: 0, unknown: 0 },
		date_import: '2026-09-30',
		documents: [
			{
				numero: NUMERO,
				etiquette_dpe: 'C',
				etiquette_ges: 'B',
				type_batiment: 'maison',
				surface_habitable: 92,
				date_etablissement: '2024-03-12',
				date_fin_validite: '2034-03-11',
				badges: ['plus_recent'],
				adresse_declaree: { etat: 'publiee', texte: '12 rue des Lilas', motif: null }
			}
		],
		selection: UNIQUE,
		signal_plusieurs_logements: null,
		constat_d110: constat,
		rattachement: 'adresse_saisie_diagnostiqueur'
	};
}

const valeurDpe = (partiel: Record<string, unknown>) => valeur({ source: SOURCE_DPE, rattachement: 'adresse_saisie_diagnostiqueur', ...partiel });

/** The document as served by `GET /v1/dpe/{numero}` (04-04, dpe-document.spec.ts). */
const DOCUMENT = {
	numero: NUMERO,
	source_identity: { reference: NUMERO, sources: [{ dataset: 'dpe03existant', url: 'https://data.ademe.fr/datasets/dpe03existant' }] },
	disponibilite: 'disponible',
	methode: 'actuelle',
	dates: { etablissement: '2024-03-12', fin_validite: '2034-03-11' },
	reperes: {
		etiquette_energie: {
			classe: valeurDpe({ field_id: 'dpe.document.etiquette_dpe', valeur: 'C', unite: 'none' }),
			mesure: valeurDpe({ field_id: 'dpe.document.conso_ep', valeur: 9120, unite: 'kwh_ep_per_year' }),
			// Served next to the total since 04.1-12, read by the page since plan 04.5-12 (SYNTHETIC).
			mesure_m2: valeurDpe({ field_id: 'dpe.document.conso_ep_m2', valeur: 104, unite: 'kwh_ep_per_m2_per_year' })
		},
		etiquette_climat: {
			classe: valeurDpe({ field_id: 'dpe.document.etiquette_ges', valeur: 'B', unite: 'none' }),
			mesure: valeurDpe({ field_id: 'dpe.document.emissions_ges', valeur: 1004.8, unite: 'kg_co2_eq_per_year' }),
			mesure_m2: valeurDpe({ field_id: 'dpe.document.emissions_ges_m2', valeur: 11, unite: 'kg_co2_eq_per_m2_per_year' })
		},
		logement_decrit: {
			type_batiment: valeurDpe({ field_id: 'dpe.document.type_batiment', valeur: 'maison', unite: 'none' }),
			surface: valeurDpe({ field_id: 'dpe.document.surface_habitable', valeur: 81.9, unite: 'm2' })
		},
		cout_annuel: valeurDpe({ field_id: 'dpe.document.cout_annuel', valeur: 1240, unite: 'euro_per_year' })
	},
	statut: { code: 'en_vigueur', validite: 'not_expired_by_date', date: null, remplace_par: null, remplace: null },
	caracteristiques: [
		valeurDpe({ field_id: 'dpe.document.type_batiment', valeur: 'maison', unite: 'none' }),
		valeurDpe({ field_id: 'dpe.document.surface_habitable', valeur: 81.9, unite: 'm2' }),
		valeurDpe({ field_id: 'dpe.document.periode_construction', valeur: '2013-2021', unite: 'none' }),
		valeurDpe({ field_id: 'dpe.document.isolation', etat: 'not_reported', valeur: null })
	],
	adresse_declaree: { etat: 'publiee', texte: '12 rue des Lilas', motif: null },
	adresse_ban: {
		etat: 'retenue',
		ban_id: BAN_ID,
		libelle: LIBELLE,
		numero: '12',
		rep: null,
		nom_voie: 'Rue des Lilas',
		commune: { code_insee: '85191', nom: 'La Roche-sur-Yon' },
		motif: null
	},
	adresse_divergente: false,
	provenance: { source: 'ADEME', date_import: '2026-09-30' },
	association: 'source_ban_equality',
	address_check: 'unverified'
};

type Comparaison = { donnees: { colonnes: { ban_id?: string; numero?: string }[] } };
function fixture(nom: string): Record<string, Comparaison> {
	return JSON.parse(readFileSync(new URL(`../../../src/lib/data/__fixtures__/${nom}`, import.meta.url), 'utf8'));
}
const ADRESSES = fixture('adresse-comparaison.json').deux_adresses;
const DOCUMENTS_DPE = fixture('dpe-comparaison.json').ancien_et_actuel;
export const IDS_COMPARES = ADRESSES.donnees.colonnes.map((colonne) => colonne.ban_id!);
export const NUMEROS_COMPARES = DOCUMENTS_DPE.donnees.colonnes.map((colonne) => colonne.numero!);

const ok = (donnees: unknown, meta: unknown = { source: [SOURCE_DPE] }): Reply => ({ status: 200, body: { statut: 'ok', donnees, meta } });
const repete = (reply: Reply, n = LECTURES_MAX) => Array.from({ length: n }, () => reply);

function attente(pathname: string, query: Record<string, string>, responses: Reply[], minCalls: number): Expectation {
	return { method: 'GET', pathname, query, minCalls, maxCalls: responses.length, responses };
}

/**
 * The address page: identity with its landmarks, one declared document, the finding as given;
 * *documentsServis* replaces the answer of the list (a zero, an outage, out of coverage).
 */
export function sourceAdresse(
	constat: typeof CONSTAT_D110 | null = null,
	minCalls = 1,
	documentsServis?: Reply,
	themes: unknown[] = []
): Expectation[] {
	const fiche = ok({ identite: IDENTITE, couverture: { etat: 'heberge', motif: null }, reperes: REPERES }, { source: [SOURCE_BAN] });
	return avecVentesDeLaParcelle([
		attente(cheminRapport(BAN_ID), REQUETE_RAPPORT, repete(rapportAvecThemes(fiche, themes)), minCalls),
		attente(`/v1/adresse/${BAN_ID}/dpe`, { page: '1' }, repete(documentsServis ?? ok(documents(constat))), minCalls)
	]) as Expectation[];
}

/** A measured zero: no document declared at the address in the imported base (D-109). */
export const DOCUMENTS_ZERO: Reply = ok({
	...documents(null),
	total: 0,
	distribution: { A: 0, B: 0, C: 0, D: 0, E: 0, F: 0, G: 0, unknown: 0 },
	documents: [],
	selection: { etat: 'indeterminee', motif: 'aucun_document', date: null, document: null, documents_ex_aequo: [] }
});

/** The list in outage (503 `documents_dpe_indisponibles`, 04-03): never a zero. */
export const DOCUMENTS_EN_PANNE: Reply = {
	status: 503,
	body: { title: 'Documents DPE indisponibles', status: 503, code: 'documents_dpe_indisponibles' }
};

/** The DPE dataset not hosted for this address: out of coverage, never a zero. */
export const DOCUMENTS_HORS_COUVERTURE: Reply = ok({
	...documents(null),
	etat: 'out_of_coverage',
	motif: 'dataset_non_heberge',
	total: null,
	distribution: null,
	documents: [],
	selection: null
});

/** The read of the document by its page (`GET /v1/dpe/{numero}`). */
export function attenteDocument(minCalls = 1): Expectation {
	return attente(`/v1/dpe/${NUMERO}`, {}, repete(ok(DOCUMENT)), minCalls);
}

/** The document page; its address may be preloaded by a hover, never required. */
export function sourceDocument(): Expectation[] {
	return [attenteDocument(1), ...sourceAdresse(null, 0)];
}

export function sourceComparerAdresses(): Expectation[] {
	return [attente('/v1/adresses/comparaison', { ids: IDS_COMPARES.join(',') }, repete({ status: 200, body: ADRESSES }), 1)];
}

export function sourceComparerDpe(): Expectation[] {
	return [attente('/v1/dpe/comparaison', { numeros: NUMEROS_COMPARES.join(',') }, repete({ status: 200, body: DOCUMENTS_DPE }), 1)];
}

/** `/communes?q=12 rue des Lilas`: the typed results page, one address and no commune. */
export function sourceResultats(): Expectation[] {
	const resultat = {
		type_resultat: 'adresse', ban_id: BAN_ID, numero: '12', rep: null, nom_voie: 'Rue des Lilas', code_postal: '85000',
		code_commune: '85191', nom_commune: 'La Roche-sur-Yon', departement: { code: '85', nom: 'Vendée' }
	};
	const adresses = ok(
		{ ordre_types: ['adresse', 'commune'], resultats: [resultat], hebergement: { ban: { etat: 'heberge', motif: null, departements: ['85'] } } },
		{ source: SOURCE_BAN }
	);
	return [
		attente('/v1/adresses/recherche', { q: '12 rue des Lilas', limite: '8' }, repete(adresses), 1),
		attente('/v1/communes/search', { q: '12 rue des Lilas', limit: '20', inclure_arrondissements: 'true' }, repete({ status: 200, body: { statut: 'ok', donnees: [] } }), 1)
	];
}

export interface RoutePhase4 {
	id: string;
	chemin: string;
	source: () => Expectation[];
	/** Text of the h1: its visibility means the surface really rendered (not an error page). */
	titre: string;
}

/** The surfaces swept by the guards of 04-08 (`/` has no API read: it is listed by each guard). */
export const ROUTES_PHASE4: readonly RoutePhase4[] = [
	{ id: 'resultats', chemin: '/communes?q=12+rue+des+Lilas', source: sourceResultats, titre: '12 rue des Lilas' },
	{ id: 'adresse', chemin: `/adresse/${BAN_ID}`, source: () => sourceAdresse(CONSTAT_D110), titre: LIBELLE },
	// 04.8-04 (UI-SPEC v4 § 6.7): the h1 names the document; its served number is the line under it.
	{ id: 'document', chemin: `/dpe/${NUMERO}`, source: sourceDocument, titre: 'Diagnostic de performance énergétique' },
	{ id: 'comparer-adresses', chemin: `/comparer?adresses=${IDS_COMPARES.join(',')}`, source: sourceComparerAdresses, titre: 'Comparer des adresses' },
	{ id: 'comparer-dpe', chemin: `/comparer?dpe=${NUMEROS_COMPARES.join(',')}`, source: sourceComparerDpe, titre: 'Comparer des documents DPE' }
];
