import { readFileSync } from 'node:fs';
import { DEPARTEMENT_44, DEPARTEMENTS_LISTE } from '../../../src/lib/data/__fixtures__/departement';
import { COMMUNE_META_SOURCE_NOMS, COMMUNE_META_SOURCE_ORDER } from '../../../src/lib/data/explications';
import { COMMUNE_FLAT_85047 } from './commune-indicators';
import { cheminRapport, REQUETE_RAPPORT } from './rapport';
import {
	IDS_COMPARES,
	NUMERO,
	NUMEROS_COMPARES,
	sourceComparerAdresses,
	sourceComparerDpe,
	sourceDocument,
	sourceResultats
} from './surfaces-phase4';

// Every public page on the synthetic source, for the guards that sweep the whole site at every width
// (`forme-publique.spec.ts`). Shapes come from the fixtures the page specs already use. The reads are
// bounded and optional, never required: this table says what a page gets, its own spec locks what it reads.

export interface LecturePublique {
	method: 'GET';
	pathname: string;
	query: Record<string, string>;
	minCalls: number;
	maxCalls: number;
	responses: Array<{ status: number; body: unknown }>;
}

export interface PagePublique {
	id: string;
	chemin: string;
	source: () => LecturePublique[];
	/** The address fiche asks for its map on our own proxy, served by the spec. */
	carte?: boolean;
}

const LECTURES = 12;
const repete = (body: unknown, status = 200) => Array.from({ length: LECTURES }, () => ({ status, body }));
const lecture = (pathname: string, query: Record<string, string>, body: unknown): LecturePublique => ({
	method: 'GET',
	pathname,
	query,
	minCalls: 0,
	maxCalls: LECTURES,
	responses: repete(body)
});
const json = <T>(url: URL) => JSON.parse(readFileSync(url, 'utf8')) as T;

interface Fiche43 {
	banId: string;
	rapport: unknown;
	documents: unknown;
	ventes: unknown;
	ventesAdresse: unknown;
}
export const FICHE_43 = json<Fiche43>(new URL('./rapport-43-themes.json', import.meta.url));

const sourceFiche43 = () => [
	lecture(cheminRapport(FICHE_43.banId), REQUETE_RAPPORT, FICHE_43.rapport),
	lecture(`/v1/adresse/${FICHE_43.banId}/dpe`, { page: '1' }, FICHE_43.documents),
	lecture(`/v1/adresse/${FICHE_43.banId}/ventes`, { page: '1' }, FICHE_43.ventes),
	lecture(`/v1/adresse/${FICHE_43.banId}/ventes-declarees`, { page: '1' }, FICHE_43.ventesAdresse)
];

const META_COMMUNE = {
	source: COMMUNE_META_SOURCE_ORDER.map((dataset) => ({
		nom: COMMUNE_META_SOURCE_NOMS[dataset],
		licence: 'Licence ouverte',
		url: 'https://www.data.gouv.fr/',
		producteur: 'Producteur public'
	}))
};
const CODE_COMMUNE = '85047';
const sourceCommune = () => [
	lecture('/v1/commune', { code_insee: CODE_COMMUNE }, { statut: 'ok', donnees: [COMMUNE_FLAT_85047], meta: META_COMMUNE })
];

const HEBERGEE = { etat: 'heberge', motif: null, departements: ['35'] };
const LIGNE_ANNUAIRE = (code_insee: string, nom: string, population: number) => ({
	code_insee,
	nom,
	departement: '35',
	population,
	population_annee: 2022,
	population_source: 'demographie',
	hebergement: HEBERGEE,
	prix: {
		maison: { mediane_m2: 3300, effectif: 88, etat: null },
		appartement: {
			mediane_m2: null,
			effectif: 3,
			etat: { etat: 'insufficient_sample', motif: "3 mutations d'appartements retenues sur 12 mois, sous le seuil de publication de 5." }
		}
	}
});
const ANNUAIRE_DEFAUT = { couverture: 'servies', sort_by: 'population', order: 'desc', page: '1', per_page: '50', inclure_arrondissements: 'true' };
const sourceAnnuaire = () => [
	lecture('/v1/communes/annuaire', ANNUAIRE_DEFAUT, {
		statut: 'ok',
		donnees: {
			results: [LIGNE_ANNUAIRE('35238', 'Rennes', 227830), LIGNE_ANNUAIRE('35047', 'Bruz', 19100), LIGNE_ANNUAIRE('35115', 'Fougères', 20300)],
			total_count: 3,
			page: 1,
			per_page: 50,
			has_next: false,
			couverture: 'servies',
			departements_servis: ['35'],
			prix: { etat: 'servi', motif: null, fenetre_mediane_dvf: '2024-12-31 → 2025-12-31' }
		}
	})
];

interface ReponseComparaison {
	donnees: { communes: Array<{ code_insee: string; synthese: unknown }> };
	meta: unknown;
}
const COMPARAISONS = json<Record<string, ReponseComparaison>>(
	new URL('../../../src/lib/data/__fixtures__/commune-comparaison.json', import.meta.url)
);
const DEUX = COMPARAISONS.deux_communes_hebergees;
const CODES_DEUX = DEUX.donnees.communes.map((commune) => commune.code_insee);
const sourceComparerCommunes = () => [lecture('/v1/communes/comparaison', { codes: CODES_DEUX.join(',') }, DEUX)];
const sourceComparerEnPanne = () => [
	{
		...lecture('/v1/communes/comparaison', { codes: CODES_DEUX.join(',') }, null),
		responses: repete({ detail: 'Service indisponible' }, 503)
	}
];

const sourceDatasets = () => [
	lecture('/v1/datasets', {}, json(new URL('../../../src/lib/data/__fixtures__/datasets-api.json', import.meta.url)))
];

const SOURCES_DEPARTEMENT = [
	{ nom: 'DVF', producteur: 'DGFiP / Etalab', licence: 'Licence Ouverte 2.0', url: 'https://www.data.gouv.fr/fr/datasets/demandes-de-valeurs-foncieres/' },
	{ nom: 'DPE', producteur: 'ADEME', licence: 'Licence Ouverte 2.0', url: 'https://data.ademe.fr/datasets/dpe-v2-logements-existants' }
];
const sourceDepartements = () => [
	lecture('/v1/departements/44', {}, { statut: 'ok', donnees: DEPARTEMENT_44, meta: { source: SOURCES_DEPARTEMENT } }),
	lecture('/v1/departements', {}, { statut: 'ok', donnees: DEPARTEMENTS_LISTE })
];

const bornees = (source: () => Array<Omit<LecturePublique, 'minCalls' | 'maxCalls'> & { maxCalls?: number }>) => () =>
	source().map((attente) => ({
		...attente,
		minCalls: 0,
		maxCalls: LECTURES,
		responses: Array.from({ length: LECTURES }, (_, i) => attente.responses[Math.min(i, attente.responses.length - 1)])
	}));

const aucune = () => [];

export const PAGES_PUBLIQUES: readonly PagePublique[] = [
	{ id: 'accueil', chemin: '/', source: aucune },
	{ id: 'adresse', chemin: `/adresse/${FICHE_43.banId}`, source: sourceFiche43, carte: true },
	{ id: 'commune', chemin: `/commune/${CODE_COMMUNE}`, source: sourceCommune },
	{ id: 'annuaire', chemin: '/communes', source: sourceAnnuaire },
	{ id: 'resultats', chemin: '/communes?q=12+rue+des+Lilas', source: bornees(sourceResultats) },
	{ id: 'departement', chemin: '/departement/44', source: sourceDepartements },
	{ id: 'departements', chemin: '/departements', source: sourceDepartements },
	{ id: 'comparer-vide', chemin: '/comparer', source: aucune },
	{ id: 'comparer-communes', chemin: `/comparer?communes=${CODES_DEUX.join(',')}`, source: sourceComparerCommunes },
	{ id: 'comparer-panne', chemin: `/comparer?communes=${CODES_DEUX.join(',')}`, source: sourceComparerEnPanne },
	{ id: 'comparer-adresses', chemin: `/comparer?adresses=${IDS_COMPARES.join(',')}`, source: bornees(sourceComparerAdresses) },
	{ id: 'comparer-dpe', chemin: `/comparer?dpe=${NUMEROS_COMPARES.join(',')}`, source: bornees(sourceComparerDpe) },
	{ id: 'document', chemin: `/dpe/${NUMERO}`, source: bornees(sourceDocument) },
	{ id: 'datasets', chemin: '/datasets', source: sourceDatasets },
	{ id: 'developpeurs', chemin: '/developpeurs', source: aucune },
	{ id: 'couverture', chemin: '/couverture', source: aucune },
	{ id: 'ce-que-nous-ne-montrons-pas', chemin: '/ce-que-nous-ne-montrons-pas', source: aucune },
	{ id: 'mentions-legales', chemin: '/mentions-legales', source: aucune },
	{ id: 'cgu', chemin: '/cgu', source: aucune },
	{ id: 'confidentialite', chemin: '/confidentialite', source: aucune },
	{ id: 'introuvable', chemin: '/page-qui-n-existe-pas', source: aucune }
];
