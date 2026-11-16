// Synthetic answers of `GET /v1/departements`, `GET /v1/departements/{code}` and
// `GET /v1/departements/{code}/fiches` (04.7-10), shaped by the schemas of the API contract
// (`contracts/openapi.v1.json`). The figures are INVENTED to exercise every state; none is a
// measure. Used by the unit tests and by the e2e specs through the synthetic source.
import type { DepartementRow, DepartementSynthese, FichesDepartement } from '$lib/api/communes';

const HEBERGE = (departements: string[]) => ({ etat: 'heberge' as const, motif: null, departements });
const HORS = (motif: 'territoire_hors_perimetre' | 'dataset_non_heberge' | 'source_sans_couverture') => ({
	etat: 'hors_couverture' as const,
	motif,
	departements: [] as string[]
});

/** 44: hosted and imported; the houses are under the publication threshold (4 sales). */
export const DEPARTEMENT_44: DepartementSynthese = {
	code: '44',
	nom: 'Loire-Atlantique',
	region: 'Pays de la Loire',
	derniere_importation: '2026-10-30',
	indexable: true,
	couverture: { dvf: HEBERGE(['44', '85']), dpe: HEBERGE(['44', '85']), demographie: HEBERGE(['44', '85']) },
	communes: { nombre: 207, arrondissements: 0 },
	population: { total: 1450000, communes_comptees: 207, annee: 2022, etat: null },
	prix: {
		fenetre: { debut: '2024-06-30', fin: '2025-06-30' },
		maison: {
			mediane_m2: null,
			effectif: 4,
			etat: { etat: 'insufficient_sample', motif: "4 mutations de maisons retenues sur 12 mois, sous le seuil de publication de 5." }
		},
		appartement: { mediane_m2: 3480, effectif: 7350, etat: null }
	},
	dpe: {
		classes: { A: 4100, B: 8200, C: 30100, D: 52000, E: 38000, F: 14800, G: 9900 },
		inconnue: 2100,
		total: 159200,
		classes_connues: 157100,
		part_fg_pct: 15.7,
		date_min: '2021-07-01',
		date_max: '2026-10-28',
		etat: null,
		etat_part_fg: null
	},
	communes_principales: [
		{ code_insee: '44109', nom: 'Nantes', type_zone: 'commune', commune_parent: null, population: 320732, population_annee: 2022, population_source: 'demographie' },
		{ code_insee: '44184', nom: 'Saint-Nazaire', type_zone: 'commune', commune_parent: null, population: 72299, population_annee: 2022, population_source: 'demographie' },
		{ code_insee: '44162', nom: 'Rezé', type_zone: 'commune', commune_parent: null, population: 43500, population_annee: null, population_source: 'geo_communes' }
	]
};

const NON_RENSEIGNE = { etat: 'not_reported' as const, motif: 'Source hébergée, pas encore importée pour ce département.' };

/** 29: DVF, DPE and the census are hosted but not imported for it: every block is `not_reported`. */
export const DEPARTEMENT_29: DepartementSynthese = {
	code: '29',
	nom: 'Finistère',
	region: 'Bretagne',
	derniere_importation: null,
	indexable: false,
	couverture: { dvf: HEBERGE(['44', '29']), dpe: HEBERGE(['44', '29']), demographie: HEBERGE(['44', '29']) },
	communes: { nombre: 277, arrondissements: 0 },
	population: { total: null, communes_comptees: null, annee: null, etat: NON_RENSEIGNE },
	prix: {
		fenetre: null,
		maison: { mediane_m2: null, effectif: null, etat: NON_RENSEIGNE },
		appartement: { mediane_m2: null, effectif: null, etat: NON_RENSEIGNE }
	},
	dpe: {
		classes: null,
		inconnue: null,
		total: null,
		classes_connues: null,
		part_fg_pct: null,
		date_min: null,
		date_max: null,
		etat: NON_RENSEIGNE,
		etat_part_fg: NON_RENSEIGNE
	},
	communes_principales: [
		{ code_insee: '29019', nom: 'Brest', type_zone: 'commune', commune_parent: null, population: 139000, population_annee: null, population_source: 'geo_communes' },
		{ code_insee: '29232', nom: 'Quimper', type_zone: 'commune', commune_parent: null, population: 63000, population_annee: null, population_source: 'geo_communes' }
	]
};

const HORS_PERIMETRE = { etat: 'out_of_coverage' as const, motif: 'territoire_hors_perimetre' };

/** 22: not hosted at all: every block is out of coverage, with its reason. */
export const DEPARTEMENT_22: DepartementSynthese = {
	code: '22',
	nom: "Côtes-d'Armor",
	region: 'Bretagne',
	derniere_importation: null,
	indexable: false,
	couverture: {
		dvf: HORS('territoire_hors_perimetre'),
		dpe: HORS('territoire_hors_perimetre'),
		demographie: HORS('territoire_hors_perimetre')
	},
	communes: { nombre: 348, arrondissements: 0 },
	population: { total: null, communes_comptees: null, annee: null, etat: HORS_PERIMETRE },
	prix: {
		fenetre: null,
		maison: { mediane_m2: null, effectif: null, etat: HORS_PERIMETRE },
		appartement: { mediane_m2: null, effectif: null, etat: HORS_PERIMETRE }
	},
	dpe: {
		classes: null,
		inconnue: null,
		total: null,
		classes_connues: null,
		part_fg_pct: null,
		date_min: null,
		date_max: null,
		etat: HORS_PERIMETRE,
		etat_part_fg: HORS_PERIMETRE
	},
	communes_principales: [
		{ code_insee: '22278', nom: 'Saint-Brieuc', type_zone: 'commune', commune_parent: null, population: 45000, population_annee: null, population_source: 'geo_communes' }
	]
};

/** 75: a departement of one commune; its principal zones are the arrondissements (75056 is the commune). */
export const DEPARTEMENT_75: DepartementSynthese = {
	...DEPARTEMENT_44,
	code: '75',
	nom: 'Paris',
	region: 'Île-de-France',
	couverture: { dvf: HEBERGE(['75']), dpe: HEBERGE(['75']), demographie: HEBERGE(['75']) },
	communes: { nombre: 1, arrondissements: 20 },
	population: { total: 2100000, communes_comptees: 1, annee: 2022, etat: null },
	prix: {
		fenetre: { debut: '2024-06-30', fin: '2025-06-30' },
		maison: { mediane_m2: 11200, effectif: 41, etat: null },
		appartement: { mediane_m2: 10450, effectif: 9120, etat: null }
	},
	communes_principales: [
		{ code_insee: '75115', nom: 'Paris 15e Arrondissement', type_zone: 'arrondissement', commune_parent: '75056', population: 230000, population_annee: 2022, population_source: 'demographie' },
		{ code_insee: '75120', nom: 'Paris 20e Arrondissement', type_zone: 'arrondissement', commune_parent: '75056', population: 195000, population_annee: 2022, population_source: 'demographie' },
		{ code_insee: '75112', nom: 'Paris 12e Arrondissement', type_zone: 'arrondissement', commune_parent: '75056', population: 140000, population_annee: 2022, population_source: 'demographie' }
	]
};

const ligne = (code: string, nom: string, region: string, indexable: boolean, prix: DepartementRow['prix'], derniere: string | null): DepartementRow => ({
	code,
	nom,
	region,
	derniere_importation: derniere,
	prix,
	indexable
});

/** The list of `GET /v1/departements`, out of order on purpose (the page sorts). */
export const DEPARTEMENTS_LISTE: DepartementRow[] = [
	ligne('85', 'Vendée', 'Pays de la Loire', true, HEBERGE(['44', '85']), '2026-10-30'),
	ligne('44', 'Loire-Atlantique', 'Pays de la Loire', true, HEBERGE(['44', '85']), '2026-10-30'),
	ligne('29', 'Finistère', 'Bretagne', false, HEBERGE(['44', '29']), null),
	ligne('22', "Côtes-d'Armor", 'Bretagne', false, HORS('territoire_hors_perimetre'), null),
	ligne('2B', 'Haute-Corse', 'Corse', false, HORS('territoire_hors_perimetre'), null),
	ligne('2A', 'Corse-du-Sud', 'Corse', false, HORS('territoire_hors_perimetre'), null)
];

/** 44 in `GET /v1/departements/44/fiches`. */
export const FICHES_44: FichesDepartement = {
	code: '44',
	derniere_importation: '2026-10-29',
	fiches: [
		{ code_insee: '44020', nom: 'Bouguenais', type_zone: 'commune' },
		{ code_insee: '44109', nom: 'Nantes', type_zone: 'commune' },
		{ code_insee: '44184', nom: 'Saint-Nazaire', type_zone: 'commune' }
	]
};

/** 75 lists the commune and its arrondissements. */
export const FICHES_75: FichesDepartement = {
	code: '75',
	derniere_importation: '2026-10-29',
	fiches: [
		{ code_insee: '75056', nom: 'Paris', type_zone: 'commune' },
		{ code_insee: '75101', nom: 'Paris 1er Arrondissement', type_zone: 'arrondissement' },
		{ code_insee: '75112', nom: 'Paris 12e Arrondissement', type_zone: 'arrondissement' }
	]
};
