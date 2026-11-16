import { readFileSync } from 'node:fs';
import { expect, type APIRequestContext, type Page } from '@playwright/test';
import { COMMUNE_NON_SERVIE, PREMIERE_PAGE_DECLAREES, SOURCE_DVF } from './adresse-ventes';
import { E2E_SOURCE_ORIGIN } from './ports';
import { cheminRapport, corpsRapport, REQUETE_RAPPORT } from './rapport';

// Synthetic source of the acceptance replay of phase 4 (plan 04-09, D-117): the seven written tasks
// of `04-UI-SPEC.md` § « Recette » and the SEM-02 audit, on ONE consistent synthetic place. Every
// answer keeps the served shape locked by the specs of the phase, and is reused from them:
//   - the address page, its landmarks and themes: adresse-fiche (04-18), adresse-lieu (04-06),
//     surfaces-phase4 (04-08); the declared documents: adresse-fiche and 04-19 (declared address);
//   - the sales of the parcel and the similar sales of task 4: adresse-ventes (04-05);
//   - the document: dpe-document (04-04); the two comparators: the frozen served answers
//     `__fixtures__/adresse-comparaison.json` (04-07, 04-11) and `dpe-comparaison.json` (04-10);
//   - Rennes out of coverage (task 7): adresse-recherche (04-17).
// Nothing here is production truth: every figure is a fixture value the page must recopy.
//
// The address comparator: the frozen answers are built by the backend test from helper pages that
// carry no source and no relation (`_champ` of tests/test_adresse_comparaison.py), while the served
// comparator copies each value of the address page with its metadata (app/adresse/comparaison.py:
// `_servie`, `_champ_du_theme`, `_plus_recent`, `_absente`). `comparaisonDesAdresses` gives each
// cell the source, relation and period of the same field on the synthetic address page below, as
// production does; values, gaps, verdicts and reasons stay the frozen ones.

type Json = null | boolean | number | string | Json[] | { [cle: string]: Json };
export type Reply = { status: number; body: unknown; gate?: string };
export interface Attente {
	method: 'GET';
	pathname: string;
	query: Record<string, string>;
	minCalls: number;
	maxCalls: number;
	responses: Reply[];
}

export const NB = '\u00a0';

// --- The place -----------------------------------------------------------------------------------

export const A = '85191_0420_00012';
export const B = '85191_0601_00001';
export const LIBELLE_A = '12 Rue des Lilas, 85000 La Roche-sur-Yon';
export const SAISIE_A = '12 rue des Lilas';
export const SAISIE_B = '1 Rue du Lieu';
export const SAISIE_RENNES = '1 rue de la Gare Rennes';
/** The most recent document declared at A (task 1), opened in task 3. */
export const RECENT = '2485E0000003C';
/** Documents of the DPE comparator (`dpe-comparaison.json`, « ancien_et_actuel »): 2020 and 2026. */
export const ANCIEN = '2185E0000001A';
export const ACTUEL = '2385E0123456A';
export const ADRESSE_DECLAREE_RECENT = '12 rue des Lilas';

const SOURCE_BAN = { nom: 'Base Adresse Nationale', url: 'https://adresse.data.gouv.fr', licence: 'Licence Ouverte 2.0', producteur: 'IGN' };
export const SOURCE_DPE = { cle: 'dpe', nom: 'Diagnostics de performance énergétique (DPE)', producteur: 'ADEME', url: 'https://data.ademe.fr/datasets/dpe03existant' };
const SOURCE_GEORISQUES = { cle: 'georisques', nom: 'Géorisques', producteur: 'BRGM / MTE', url: 'https://www.georisques.gouv.fr' };
const SOURCE_BATIMENTS = { cle: 'batiments', nom: 'BD TOPO — Bâtiments', producteur: 'IGN', url: 'https://geoservices.ign.fr/bdtopo' };
const SOURCE_CADASTRE = { cle: 'cadastre', nom: 'Cadastre (Etalab)', producteur: 'DGFiP / Etalab', url: 'https://cadastre.data.gouv.fr' };
const SOURCE_COPRO = { cle: 'copropriete', nom: 'Registre national des copropriétés', producteur: 'ANAH', url: 'https://www.registre-coproprietes.gouv.fr' };
const SOURCE_RNB = { cle: 'rnb', nom: 'Référentiel national des bâtiments (RNB)', producteur: 'beta.gouv.fr / CSTB', url: 'https://rnb.beta.gouv.fr' };
const SOURCE_GPU = { cle: 'urbanisme', nom: 'API Carto GPU', producteur: 'IGN', url: 'https://apicarto.ign.fr' };
const SOURCE_ARCEP = { cle: 'connectivite', nom: 'Arcep — Ma connexion internet', producteur: 'Arcep', url: 'https://maconnexioninternet.arcep.fr' };
const SOURCE_ARRETS = { cle: 'transports', nom: 'Arrêts de transport', producteur: 'PAN', url: 'https://transport.data.gouv.fr' };

const HEBERGE = { etat: 'heberge', motif: null, departements: ['85'] };

function valeur(partiel: Record<string, unknown>) {
	return { field_id: 'x', etat: 'value', valeur: null, unite: null, periode: null, motif: null, source: null, rattachement: null, reference: null, ...partiel };
}
function champ(partiel: Record<string, unknown>) {
	return { ...valeur(partiel), elements: [], liste_incomplete: false, rayon_m: null, ...partiel };
}

const IDENTITE_A = {
	etat: 'etablie',
	ban_id: A,
	libelle: LIBELLE_A,
	numero: '12',
	rep: null,
	nom_voie: 'Rue des Lilas',
	code_postal: '85000',
	commune: { code_insee: '85191', nom: 'La Roche-sur-Yon' },
	departement: { code: '85', nom: 'Vendée' },
	parcelle: { etat: 'unique', id_parcelle: '85191000AB0012', nombre_candidats: 1, rattachement: 'parcelle_du_point', motif: null }
};

// Twelve documents declared at A, on two pages of ten (« toutes pages » of the audit): the most
// recent of 2024 with its published declared address, the 2020 document of the DPE comparator
// (former method), then older ones. Classes: C 5, D 5, E 2.
const carte = (numero: string, partiel: Record<string, unknown> = {}) => ({
	numero,
	etiquette_dpe: 'D',
	etiquette_ges: 'C',
	type_batiment: 'maison',
	surface_habitable: 92,
	date_etablissement: '2022-06-01',
	date_fin_validite: '2032-05-31',
	badges: [] as string[],
	adresse_declaree: null as null | { etat: string; texte: string | null; motif: string | null },
	...partiel
});
const DOCUMENTS_A = [
	carte(RECENT, {
		etiquette_dpe: 'C', etiquette_ges: 'B', date_etablissement: '2024-03-12', date_fin_validite: '2034-03-11', badges: ['plus_recent'],
		adresse_declaree: { etat: 'publiee', texte: ADRESSE_DECLAREE_RECENT, motif: null }
	}),
	...[1, 2, 3, 4].map((n) => carte(`2385E000001${n}C`, { etiquette_dpe: 'C', date_etablissement: `2023-0${n}-15`, date_fin_validite: `2033-0${n}-14` })),
	...[1, 2, 3, 4].map((n) => carte(`2285E000002${n}D`, { date_etablissement: `2022-0${n}-10`, date_fin_validite: `2032-0${n}-09` })),
	carte(ANCIEN, { etiquette_dpe: 'D', date_etablissement: '2020-05-04', date_fin_validite: '2030-05-03', badges: ['ancienne_methode'] }),
	carte('1985E0000031E', { etiquette_dpe: 'E', date_etablissement: '2019-12-12', date_fin_validite: '2029-12-11', badges: ['ancienne_methode'] }),
	carte('1885E0000032E', { etiquette_dpe: 'E', date_etablissement: '2018-02-20', date_fin_validite: '2028-02-19', badges: ['ancienne_methode'] })
];
export const NOMBRE_DOCUMENTS_A = DOCUMENTS_A.length;
export const GENERATION_DPE = '0123456789abcdef';
const GENERATION_DVF = 'fedcba9876543210';

const UNIQUE = {
	etat: 'unique',
	motif: 'document_valide',
	date: '2024-03-12',
	document: { numero: RECENT, etiquette_dpe: 'C', etiquette_ges: 'B', date_etablissement: '2024-03-12' },
	documents_ex_aequo: []
};

const REFERENCE = { valeur: 2500, unite: 'euro_per_m2', territoire: { code_insee: '85191', nom: 'La Roche-sur-Yon' }, source: SOURCE_DVF, millesime: '2021-2025', url: SOURCE_DVF.url };

// Landmark ① is dated by the last DPE import, the `date_import` of its list (C14,
// app/adresse/documents.py, `repere_documents`).
const IMPORT_DPE = '2026-09-30';
const REPERES_A = {
	dpe: {
		...valeur({ field_id: 'adresse.dpe.nombre_declares', valeur: NOMBRE_DOCUMENTS_A, unite: 'none', periode: IMPORT_DPE, source: SOURCE_DPE, rattachement: 'adresse_saisie_diagnostiqueur' }),
		selection: UNIQUE
	},
	ventes: {
		nombre: valeur({ field_id: 'adresse.dvf.ventes_parcelle.nombre', valeur: 3, unite: 'count', periode: '2021-2025', source: SOURCE_DVF, rattachement: 'parcelle_du_point' }),
		derniere: valeur({ field_id: 'adresse.dvf.ventes_parcelle.derniere', valeur: '2025-11-20', unite: 'none', periode: '2021-2025', source: SOURCE_DVF, rattachement: 'parcelle_du_point' })
	},
	argiles: valeur({ field_id: 'adresse.risques.argiles_au_point', valeur: 'Exposition moyenne', unite: 'none', periode: '2026-10-02', source: SOURCE_GEORISQUES, rattachement: 'au_point' }),
	reference_commune: {
		maisons: valeur({ field_id: 'adresse.reference_commune.prix_median_maison', valeur: 2500, unite: 'euro_per_m2', periode: '2021-2025', source: SOURCE_DVF, rattachement: 'commune_reference', reference: REFERENCE }),
		appartements: valeur({ field_id: 'adresse.reference_commune.prix_median_appartement', valeur: 1980, unite: 'euro_per_m2', periode: '2021-2025', source: SOURCE_DVF, rattachement: 'commune_reference', reference: { ...REFERENCE, valeur: 1980 } })
	}
};

const element = (libelle: string, distance_m: number | null = null, nature: string | null = null) => ({ libelle, distance_m, nature });

// The four themes of the place (adresse-lieu.spec.ts, 04-06). Periods as served: Géorisques and GPU
// date their reading, Arcep its quarter; buildings, parcel, copro, RNB and stops the millésime or
// the import day of their table (C14, app/adresse/lieu.py, `_periode`).
const PERIODE_BATIMENTS = '2026-09-25';
const PERIODE_RNB = 'Export RNB 2026-09';
const PERIODE_CADASTRE = '2026-09-25';
const PERIODE_COPRO = '2026-09-24';
const PERIODE_ARRETS = '2026-09-24';
const THEMES_A = [
	{
		cle: 'batiment_parcelle', titre: 'Bâtiment et parcelle', applicable: true, etat: 'value', motif: null, liste_incomplete: false,
		groupes: [
			{
				cle: 'lieu', titre: null,
				champs: [
					champ({ field_id: 'adresse.batiments.nombre_parcelle', valeur: 2, unite: 'count', periode: PERIODE_BATIMENTS, source: SOURCE_BATIMENTS, rattachement: 'parcelle_du_point' }),
					champ({ field_id: 'adresse.batiments.nombre_parcelle_hors_annexe', valeur: 2, unite: 'count', periode: PERIODE_BATIMENTS, source: SOURCE_BATIMENTS, rattachement: 'parcelle_du_point' }),
					champ({ field_id: 'adresse.batiment.rnb_id', valeur: 'RNBA00000001', unite: 'none', periode: PERIODE_RNB, source: SOURCE_RNB, rattachement: 'batiment_rnb_de_l_adresse' }),
					champ({ field_id: 'adresse.parcelle.identifiant', valeur: '85191000AB0012', unite: 'none', periode: PERIODE_CADASTRE, source: SOURCE_CADASTRE, rattachement: 'parcelle_du_point' }),
					champ({ field_id: 'adresse.parcelle.contenance', valeur: 1412, unite: 'm2', periode: PERIODE_CADASTRE, source: SOURCE_CADASTRE, rattachement: 'parcelle_du_point' }),
					champ({ field_id: 'adresse.copropriete.immatriculation', valeur: 0, unite: 'count', periode: PERIODE_COPRO, source: SOURCE_COPRO, rattachement: 'parcelle_du_point' })
				]
			}
		]
	},
	{
		cle: 'risques', titre: 'Risques', applicable: true, etat: 'value', motif: null, liste_incomplete: false,
		groupes: [
			{
				cle: 'au_point', titre: "Au point de l'adresse",
				champs: [champ({ field_id: 'adresse.risques.argiles_au_point', valeur: 'Exposition moyenne', unite: 'none', periode: '2026-10-02', source: SOURCE_GEORISQUES, rattachement: 'au_point' })]
			},
			{
				cle: 'commune', titre: 'Sur la commune (référence)',
				champs: [
					champ({ field_id: 'adresse.risques.liste_commune', valeur: 2, unite: 'count', periode: '2026-10-02', source: SOURCE_GEORISQUES, rattachement: 'commune_reference', elements: [element('Inondation'), element('Séisme')] }),
					champ({ field_id: 'adresse.risques.radon_commune', valeur: '3', unite: 'none', periode: '2026-10-02', source: SOURCE_GEORISQUES, rattachement: 'commune_reference' }),
					champ({ field_id: 'adresse.risques.icpe_commune', valeur: 4, unite: 'count', periode: '2026-10-02', source: SOURCE_GEORISQUES, rattachement: 'commune_reference' })
				]
			}
		]
	},
	{
		cle: 'urbanisme', titre: 'Urbanisme', applicable: true, etat: 'value', motif: null, liste_incomplete: false,
		groupes: [
			{
				cle: 'au_point', titre: null,
				champs: [champ({ field_id: 'adresse.urbanisme.zone_plu', valeur: 1, unite: 'count', periode: '2026-10-02', source: SOURCE_GPU, rattachement: 'au_point', elements: [element('UB · Zone urbaine')] })]
			}
		]
	},
	{
		cle: 'reseaux_transports', titre: 'Réseaux et transports', applicable: true, etat: 'value', motif: null, liste_incomplete: false,
		groupes: [
			{
				cle: 'lieu', titre: null,
				champs: [
					champ({ field_id: 'adresse.connectivite.eligibilite_fibre', valeur: true, unite: 'none', periode: '2026-T2', source: SOURCE_ARCEP, rattachement: 'eligibilite_adresse' }),
					champ({
						field_id: 'adresse.transports.arret_proche', valeur: 1, unite: 'count', periode: PERIODE_ARRETS, source: SOURCE_ARRETS, rattachement: 'rayon_du_point', rayon_m: 1000,
						elements: [element('Place Napoléon', 100, 'arret')]
					})
				]
			}
		]
	}
];

export const FICHE_A = {
	statut: 'ok',
	donnees: { identite: IDENTITE_A, couverture: { etat: 'heberge', motif: null }, reperes: REPERES_A, themes: THEMES_A },
	meta: { source: [SOURCE_BAN] }
};

/** One page of the documents declared at A (10 a page). */
export function documentsA(page: 1 | 2) {
	return {
		statut: 'ok',
		donnees: {
			etat: 'value',
			motif: null,
			source: SOURCE_DPE,
			total: NOMBRE_DOCUMENTS_A,
			page,
			pages: 2,
			par_page: 10,
			generation: GENERATION_DPE,
			distribution: { A: 0, B: 0, C: 5, D: 5, E: 2, F: 0, G: 0, unknown: 0 },
			date_import: IMPORT_DPE,
			documents: structuredClone(page === 1 ? DOCUMENTS_A.slice(0, 10) : DOCUMENTS_A.slice(10)),
			selection: UNIQUE,
			signal_plusieurs_logements: null,
			constat_d110: null,
			rattachement: 'adresse_saisie_diagnostiqueur'
		},
		meta: { source: [SOURCE_BAN, SOURCE_DPE] }
	};
}

// --- Sales (task 4) -------------------------------------------------------------------------------

function vente(partiel: Record<string, unknown> = {}) {
	return {
		date: '2025-11-20', nature: 'Vente', composition: { maisons: 1, appartements: 0, dependances: 1, locaux_activite: 0 },
		surface_batie: 90, prix: 238000, prix_m2: 2644, prix_affiche: true, minimisation: null, distance_m: null, ...partiel
	};
}

export const VENTES_A = {
	statut: 'ok',
	donnees: {
		etat: 'value', motif: null, nombre_parcelles: null, rattachement: 'parcelle_du_point', source: SOURCE_DVF, periode: '2021-2025',
		total: 3, page: 1, pages: 1, par_page: 10, generation: GENERATION_DVF,
		ventes: [
			vente(),
			vente({ date: '2023-06-30', composition: { maisons: 0, appartements: 2, dependances: 1, locaux_activite: 0 }, surface_batie: null, prix: 225000, prix_m2: null }),
			vente({ date: '2021-03-15', surface_batie: 85, prix: 210000, prix_m2: 2471 })
		]
	},
	meta: { source: [SOURCE_DVF] }
};

/**
 * C37: the sales declared at the address of A (« Ventes déclarées à cette adresse »), one sale
 * with its whole amount, a relation distinct from the parcel's.
 */
export const VENTES_DECLAREES_A = {
	statut: 'ok',
	donnees: {
		etat: 'value', motif: null, rattachement: 'adresse_declaree_vente', source: SOURCE_DVF, periode: '2021-2025',
		total: 1, page: 1, pages: 1, par_page: 10, generation: GENERATION_DVF,
		ventes: [vente({ date: '2022-09-01', composition: { maisons: 0, appartements: 2, dependances: 0, locaux_activite: 0 }, surface_batie: 120, prix: 330000, prix_m2: null })]
	},
	meta: { source: [SOURCE_DVF] }
};

/**
 * C4 (04.5-07): the same answer as the page reads it, `vefa=incluses` (no VEFA: `nombre_vefa` 0, every card
 * a sale). The SEM-02 collector also reads the answer of the default mode (`VENTES_DECLAREES_A`).
 */
export const VENTES_DECLAREES_A_INCLUSES = {
	...VENTES_DECLAREES_A,
	donnees: { ...VENTES_DECLAREES_A.donnees, nombre_vefa: 0, ventes: VENTES_DECLAREES_A.donnees.ventes.map((carte) => ({ ...carte, hors_stats_vefa: false })) }
};

/**
 * SEM-02 mixed counter-proof (plan 04.5-07, counter-review I): one sale and two VEFA declared at A.
 * SYNTHETIC. Under `vefa=incluses` the API lists the three mutations (`total` 3) and counts the VEFA
 * among them (`nombre_vefa` 2); under the default mode it lists the sale alone (`total` 1), the value of
 * `adresse.dvf.ventes_adresse.nombre`. The expected values are fixed here, never computed.
 */
export const ATTENDU_MIXTE = { total_liste: 3, nombre_vefa: 2, nombre_statistique: 1 } as const;
const VEFA_MIXTE = (date: string, surface: number, prix: number) =>
	vente({ date, nature: "Vente en l'état futur d'achèvement", composition: { maisons: 0, appartements: 1, dependances: 0, locaux_activite: 0 }, surface_batie: surface, prix, prix_m2: null, hors_stats_vefa: true });
export const VENTES_DECLAREES_MIXTE = {
	...VENTES_DECLAREES_A_INCLUSES,
	donnees: {
		...VENTES_DECLAREES_A_INCLUSES.donnees,
		total: 3,
		nombre_vefa: 2,
		ventes: [VEFA_MIXTE('2024-02-01', 61, 289000), ...VENTES_DECLAREES_A_INCLUSES.donnees.ventes, VEFA_MIXTE('2021-11-15', 45, 199000)]
	}
};
/** The same address read in the default mode: the sale alone. */
export const VENTES_DECLAREES_MIXTE_EXCLUES = VENTES_DECLAREES_A;

/** Query of task 4 as the GET form submits it (« maison », 90 m², 1 km; tolerance and period by default). */
export const CRITERES_TACHE_4 = { type: 'maison', surface: '90', tolerance: '20', rayon: '1000', periode: '5' };
export const PHRASE_TACHE_4 = '6 ventes ressemblantes · maisons · 72 à 108 m² · 1 km · 2021 → 2025';

export const RESSEMBLANTES_A = {
	statut: 'ok',
	donnees: {
		etat: 'value', motif: null, rattachement: 'rayon_du_point', source: SOURCE_DVF,
		criteres_appliques: { type: 'maison', surface: 90, tolerance: 20, surface_min: 72, surface_max: 108, perimetre: 'rayon', rayon_m: 1000, periode: { annees: 5, debut: 2021, fin: 2025 } },
		effectif: 6, page: 1, pages: 1, par_page: 10, generation: GENERATION_DVF,
		ventes: [
			vente({ date: '2025-02-01', surface_batie: 90, prix: 300000, prix_m2: 3333, distance_m: 120 }),
			vente({ date: '2024-01-01', surface_batie: 90, prix: 305000, prix_m2: 3389, distance_m: 120 }),
			vente({ date: '2024-06-01', surface_batie: 100, prix: 310000, prix_m2: 3100, distance_m: 400 }),
			vente({ date: '2025-07-01', surface_batie: 95, prix: null, prix_m2: null, prix_affiche: false, minimisation: { motif: 'vente_isolee_recente' }, distance_m: 700 }),
			vente({ date: '2022-03-01', surface_batie: 88, prix: 270000, prix_m2: 3068, distance_m: 800 }),
			vente({ date: '2023-01-15', surface_batie: 80, prix: 250000, prix_m2: 3125, distance_m: 900 })
		]
	},
	meta: { source: [SOURCE_DVF, SOURCE_BAN] }
};

// --- Documents (tasks 3 and 6) --------------------------------------------------------------------

const valeurDpe = (partiel: Record<string, unknown>) => valeur({ source: SOURCE_DPE, rattachement: 'adresse_saisie_diagnostiqueur', ...partiel });

/**
 * A document as `GET /v1/dpe/{numero}` serves it (dpe-document.spec.ts, 04-04). Since 04.1-12 (C51)
 * the API serves next to each yearly total its per-square-metre value published by the ADEME
 * (`mesure_m2`). The SYNTHETIC per-m² values are deliberately not total ÷ surface (9 120 / 92 ≈ 99,
 * 17 830,4 / 79 ≈ 226): a page that divided would show another figure (D45-05, plan 04.5-12).
 */
function documentDpe(numero: string, ancien: boolean) {
	const etabli = ancien ? '2020-05-04' : '2024-03-12';
	return {
		statut: 'ok',
		donnees: {
			numero,
			source_identity: { reference: numero, sources: [{ dataset: 'dpe03existant', url: 'https://data.ademe.fr/datasets/dpe03existant' }] },
			disponibilite: 'disponible',
			methode: ancien ? 'ancienne' : 'actuelle',
			dates: { etablissement: etabli, fin_validite: ancien ? '2030-05-03' : '2034-03-11' },
			reperes: {
				etiquette_energie: {
					classe: valeurDpe({ field_id: 'dpe.document.etiquette_dpe', valeur: ancien ? 'D' : 'C', unite: 'none' }),
					mesure: valeurDpe({ field_id: 'dpe.document.conso_ep', valeur: ancien ? 17830.4 : 9120, unite: 'kwh_ep_per_year' }),
					mesure_m2: valeurDpe({ field_id: 'dpe.document.conso_ep_m2', valeur: ancien ? 231 : 101, unite: 'kwh_ep_per_m2_per_year' })
				},
				etiquette_climat: {
					classe: valeurDpe({ field_id: 'dpe.document.etiquette_ges', valeur: ancien ? 'C' : 'B', unite: 'none' }),
					mesure: valeurDpe({ field_id: 'dpe.document.emissions_ges', valeur: ancien ? 3433.1 : 1004.8, unite: 'kg_co2_eq_per_year' }),
					mesure_m2: valeurDpe({ field_id: 'dpe.document.emissions_ges_m2', valeur: ancien ? 41 : 9, unite: 'kg_co2_eq_per_m2_per_year' })
				},
				logement_decrit: {
					type_batiment: valeurDpe({ field_id: 'dpe.document.type_batiment', valeur: 'maison', unite: 'none' }),
					surface: valeurDpe({ field_id: 'dpe.document.surface_habitable', valeur: ancien ? 79 : 92, unite: 'm2' })
				},
				cout_annuel: valeurDpe({ field_id: 'dpe.document.cout_annuel', valeur: ancien ? 1834.2 : 1240, unite: 'euro_per_year' })
			},
			statut: { code: ancien ? 'ancienne_methode' : 'en_vigueur', validite: 'not_expired_by_date', date: null, remplace_par: null, remplace: null },
			caracteristiques: [
				valeurDpe({ field_id: 'dpe.document.type_batiment', valeur: 'maison', unite: 'none' }),
				valeurDpe({ field_id: 'dpe.document.surface_habitable', valeur: ancien ? 79 : 92, unite: 'm2' }),
				valeurDpe({ field_id: 'dpe.document.periode_construction', valeur: '1948-1974', unite: 'none' }),
				valeurDpe({ field_id: 'dpe.document.isolation', etat: 'not_reported', valeur: null })
			],
			adresse_declaree: { etat: 'publiee', texte: ADRESSE_DECLAREE_RECENT, motif: null },
			adresse_ban: {
				etat: 'retenue', ban_id: A, libelle: LIBELLE_A, numero: '12', rep: null, nom_voie: 'Rue des Lilas',
				commune: { code_insee: '85191', nom: 'La Roche-sur-Yon' }, motif: null
			},
			adresse_divergente: false,
			provenance: { source: 'ADEME', date_import: '2026-09-30' },
			association: 'source_ban_equality',
			address_check: 'unverified'
		},
		meta: { source: [] }
	};
}
export const DOCUMENT_RECENT = documentDpe(RECENT, false);
export const DOCUMENT_ANCIEN = documentDpe(ANCIEN, true);

// The real document of the UI review and a retired one (plan 04.5-12), in a module without
// Playwright so that the unit tests of the inventory read the same answers.
export { DOCUMENT_REEL, DOCUMENT_RETIRE, REEL, RETIRE } from './dpe-documents';

// --- Comparators (tasks 5 and 6) ------------------------------------------------------------------

function figees(nom: string): Record<string, { statut: string; donnees: Record<string, Json>; meta: Json }> {
	return JSON.parse(readFileSync(new URL(`../../../src/lib/data/__fixtures__/${nom}`, import.meta.url), 'utf8'));
}

/** Metadata of each compared field as the synthetic page of A serves it (source, relation, period). */
const META_DE_LA_PAGE: Record<string, { source: Json; rattachement: string; periode: string | null }> = {
	// The comparator copies the period of ① on both DPE rows (app/adresse/comparaison.py, `_plus_recent`).
	'adresse.dpe.nombre_declares': { source: SOURCE_DPE, rattachement: 'adresse_saisie_diagnostiqueur', periode: IMPORT_DPE },
	'adresse.dpe.plus_recent': { source: SOURCE_DPE, rattachement: 'adresse_saisie_diagnostiqueur', periode: IMPORT_DPE },
	'adresse.dvf.ventes_parcelle.nombre': { source: SOURCE_DVF, rattachement: 'parcelle_du_point', periode: '2021-2025' },
	'adresse.dvf.ventes_parcelle.derniere': { source: SOURCE_DVF, rattachement: 'parcelle_du_point', periode: '2021-2025' },
	// C40: the count of the C37 route, dated by the DVF period of the source.
	'adresse.dvf.ventes_adresse.nombre': { source: SOURCE_DVF, rattachement: 'adresse_declaree_vente', periode: '2021-2025' },
	// The building alone under the point (BD TOPO), dated by its table as the count (app/adresse/lieu.py).
	'adresse.batiment.hauteur': { source: SOURCE_BATIMENTS, rattachement: 'batiment_du_point', periode: PERIODE_BATIMENTS },
	'adresse.parcelle.contenance': { source: SOURCE_CADASTRE, rattachement: 'parcelle_du_point', periode: PERIODE_CADASTRE },
	'adresse.risques.argiles_au_point': { source: SOURCE_GEORISQUES, rattachement: 'au_point', periode: '2026-10-02' },
	'adresse.urbanisme.zone_plu': { source: SOURCE_GPU, rattachement: 'au_point', periode: '2026-10-02' },
	'adresse.connectivite.eligibilite_fibre': { source: SOURCE_ARCEP, rattachement: 'eligibilite_adresse', periode: '2026-T2' },
	'adresse.transports.arret_proche': { source: SOURCE_ARRETS, rattachement: 'rayon_du_point', periode: PERIODE_ARRETS },
	'adresse.reference_commune.prix_median_maison': { source: SOURCE_DVF, rattachement: 'commune_reference', periode: '2021-2025' },
	'adresse.reference_commune.prix_median_appartement': { source: SOURCE_DVF, rattachement: 'commune_reference', periode: '2021-2025' }
};

/**
 * A frozen answer of the address comparator with each cell carrying the metadata the served
 * comparator copies from the address page (designated rows keep theirs: a document's).
 */
function comparaisonDesAdresses(nom: string) {
	const reponse = structuredClone(figees('adresse-comparaison.json')[nom]);
	for (const groupe of reponse.donnees.groupes as { lignes: { field_id: string; designe: boolean; cellules: Record<string, Json>[] }[] }[]) {
		for (const ligne of groupe.lignes) {
			if (ligne.designe) continue;
			const meta = META_DE_LA_PAGE[ligne.field_id];
			if (!meta) throw new Error(`champ comparé sans métadonnées de page : ${ligne.field_id}`);
			for (const cellule of ligne.cellules) Object.assign(cellule, meta);
		}
	}
	return reponse;
}

export const COMPARAISON_AB = comparaisonDesAdresses('deux_adresses');
/** Three addresses, the third out of coverage (states of the comparator). */
export const COMPARAISON_TROIS = comparaisonDesAdresses('trois_dont_hors_couverture');
/** A and B, the 2020 document designated on A (task 5). */
export const COMPARAISON_AB_DESIGNEE = comparaisonDesAdresses('une_designation');
export const DESIGNATION = `${A}:${ANCIEN}`;
export const COMPARAISON_DPE = figees('dpe-comparaison.json').ancien_et_actuel;

// --- Searches (tasks 1, 5 and 7) ------------------------------------------------------------------

const resultatAdresse = (banId: string, numero: string, voie: string) => ({
	type_resultat: 'adresse', ban_id: banId, numero, rep: null, nom_voie: voie, code_postal: '85000',
	code_commune: '85191', nom_commune: 'La Roche-sur-Yon', departement: { code: '85', nom: 'Vendée' }
});
const adresses = (resultats: unknown[], ordre: string[], ban: unknown = HEBERGE) => ({
	statut: 'ok', donnees: { ordre_types: ordre, resultats, hebergement: { ban } }, meta: { source: SOURCE_BAN }
});
const HORS_COUVERTURE = { etat: 'hors_couverture', motif: 'territoire_hors_perimetre', departements: [] };
const RENNES = {
	code_insee: '35238', nom: 'Rennes', code_postal: '35000', departement: '35', population: 1000, population_annee: null,
	population_source: 'geo_communes', hebergement: HORS_COUVERTURE
};
const BAN_RENNES = { ...HORS_COUVERTURE, territoire: { code_insee: '35238', nom: 'Rennes', departement: { code: '35', nom: 'Ille-et-Vilaine' } } };

// --- Synthetic source contract ----------------------------------------------------------------

const ok = (body: unknown): Reply => ({ status: 200, body });
/** One exact read, answered up to *max* times, required at least *min* times. */
export function lecture(pathname: string, query: Record<string, string>, body: unknown, max = 1, min = 1): Attente {
	return { method: 'GET', pathname, query, minCalls: min, maxCalls: max, responses: Array.from({ length: max }, () => ok(body)) };
}
export function reponses(pathname: string, query: Record<string, string>, replies: Reply[], min = replies.length): Attente {
	return { method: 'GET', pathname, query, minCalls: min, maxCalls: replies.length, responses: replies };
}

/** The first page of the address A (identity, documents, sales, commune), read up to *max* times. */
export function pageA(max = 2, min = 1): Attente[] {
	return [
		lecture(cheminRapport(A), REQUETE_RAPPORT, corpsRapport(FICHE_A), max, min),
		lecture(`/v1/adresse/${A}/dpe`, { page: '1' }, documentsA(1), max, min),
		lecture(`/v1/adresse/${A}/ventes`, { page: '1' }, VENTES_A, max, 0),
		lecture(`/v1/adresse/${A}/ventes-declarees`, { ...PREMIERE_PAGE_DECLAREES }, VENTES_DECLAREES_A_INCLUSES, max, 0),
		lecture('/v1/commune', { code_insee: '85191' }, COMMUNE_NON_SERVIE.body, max, 0)
	];
}

/**
 * Companion reads of an address page whose identity is not served (outage, refusal, out of
 * coverage): the page reads its documents, sales and commune in parallel; none is required.
 */
export function compagnons(banId: string): Attente[] {
	const zero = documentsA(1);
	Object.assign(zero.donnees, { total: 0, pages: 1, documents: [], distribution: { A: 0, B: 0, C: 0, D: 0, E: 0, F: 0, G: 0, unknown: 0 } });
	const sansVente = { ...VENTES_A, donnees: { ...VENTES_A.donnees, total: 0, ventes: [] } };
	const sansVenteDeclaree = { ...VENTES_DECLAREES_A_INCLUSES, donnees: { ...VENTES_DECLAREES_A_INCLUSES.donnees, total: 0, ventes: [] } };
	return [
		lecture(`/v1/adresse/${banId}/dpe`, { page: '1' }, zero, 2, 0),
		lecture(`/v1/adresse/${banId}/ventes`, { page: '1' }, sansVente, 2, 0),
		lecture(`/v1/adresse/${banId}/ventes-declarees`, { ...PREMIERE_PAGE_DECLAREES }, sansVenteDeclaree, 2, 0),
		lecture('/v1/commune', { code_insee: banId.slice(0, 5) }, COMMUNE_NON_SERVIE.body, 2, 0)
	];
}

/** The single field of `/` typing the address A: both halves of the search. */
export function rechercheA(): Attente[] {
	return [
		lecture('/v1/communes/search', { q: SAISIE_A, limit: '8', inclure_arrondissements: 'true' }, { statut: 'ok', donnees: [] }),
		lecture('/v1/adresses/recherche', { q: SAISIE_A, limite: '8' }, adresses([resultatAdresse(A, '12', 'Rue des Lilas')], ['adresse', 'commune']))
	];
}

export function rechercheB(): Attente {
	return lecture('/v1/adresses/recherche', { q: SAISIE_B, limite: '8' }, adresses([resultatAdresse(B, '1', 'Rue du Lieu')], ['adresse', 'commune']));
}

export function rechercheRennes(): Attente[] {
	return [
		lecture('/v1/communes/search', { q: SAISIE_RENNES, limit: '8', inclure_arrondissements: 'true' }, { statut: 'ok', donnees: [RENNES] }),
		lecture('/v1/adresses/recherche', { q: SAISIE_RENNES, limite: '8' }, adresses([], ['adresse', 'commune'], BAN_RENNES))
	];
}

export const ressemblantesA = (max = 1) => lecture(`/v1/adresse/${A}/ventes-ressemblantes`, { ...CRITERES_TACHE_4, page: '1' }, RESSEMBLANTES_A, max);
export const documentRecent = (max = 2, min = 1) => lecture(`/v1/dpe/${RECENT}`, {}, DOCUMENT_RECENT, max, min);
export const documentAncien = (max = 3, min = 1) => lecture(`/v1/dpe/${ANCIEN}`, {}, DOCUMENT_ANCIEN, max, min);
export const comparaisonAB = (max = 2) => lecture('/v1/adresses/comparaison', { ids: `${A},${B}` }, COMPARAISON_AB, max);
export const comparaisonABDesignee = (max = 1) => lecture('/v1/adresses/comparaison', { ids: `${A},${B}`, designes: DESIGNATION }, COMPARAISON_AB_DESIGNEE, max);
export const comparaisonDpe = (max = 2) => lecture('/v1/dpe/comparaison', { numeros: `${ANCIEN},${ACTUEL}` }, COMPARAISON_DPE, max);

/**
 * Every public answer of the place, read once each by the SEM-02 collector (sem02-audit.ts): the page
 * of A, both pages of its documents, its sales, its similar sales, the document, both comparators.
 * *remplacer* swaps one answer for a modified one (counter-proofs).
 */
export function sourceDeLAudit(
	remplacer: Partial<Record<'fiche' | 'documents1' | 'documents2' | 'ventes' | 'declarees' | 'declareesIncluses' | 'ressemblantes' | 'document' | 'cmpAdresses' | 'cmpDpe', unknown>> = {}
): Attente[] {
	return [
		lecture(`/v1/adresse/${A}`, {}, remplacer.fiche ?? FICHE_A),
		lecture(`/v1/adresse/${A}/dpe`, { page: '1' }, remplacer.documents1 ?? documentsA(1)),
		lecture(`/v1/adresse/${A}/dpe`, { page: '2', g: GENERATION_DPE }, remplacer.documents2 ?? documentsA(2)),
		lecture(`/v1/adresse/${A}/ventes`, { page: '1' }, remplacer.ventes ?? VENTES_A),
		// The mode of the field (`adresse.dvf.ventes_adresse.nombre`, VEFA excluded) and the mode the page
		// reads (C4, 04.5-07), both by number and suffix (C3, 04.5-08).
		lecture(`/v1/adresse/${A}/ventes-declarees`, { page: '1', regle: 'numero_et_suffixe' }, remplacer.declarees ?? VENTES_DECLAREES_A),
		lecture(`/v1/adresse/${A}/ventes-declarees`, { ...PREMIERE_PAGE_DECLAREES }, remplacer.declareesIncluses ?? VENTES_DECLAREES_A_INCLUSES),
		lecture(`/v1/adresse/${A}/ventes-ressemblantes`, { ...CRITERES_TACHE_4, page: '1' }, remplacer.ressemblantes ?? RESSEMBLANTES_A),
		lecture(`/v1/dpe/${RECENT}`, {}, remplacer.document ?? DOCUMENT_RECENT),
		lecture('/v1/adresses/comparaison', { ids: `${A},${B}`, designes: DESIGNATION }, remplacer.cmpAdresses ?? COMPARAISON_AB_DESIGNEE),
		lecture('/v1/dpe/comparaison', { numeros: `${ANCIEN},${ACTUEL}` }, remplacer.cmpDpe ?? COMPARAISON_DPE)
	];
}

/** The keys of the synthetic place, for the SEM-02 collector. */
export const CLES_SYNTHETIQUES = {
	banId: A,
	criteres: CRITERES_TACHE_4,
	numero: RECENT,
	idsComparaison: [A, B],
	designes: DESIGNATION,
	numerosComparaison: [ANCIEN, ACTUEL]
};

/** Replaces the whole contract of the synthetic source (fail-closed: anything else is a 599). */
export async function configurer(request: APIRequestContext, attentes: Attente[]): Promise<void> {
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, { data: { reset: true, expectations: attentes } });
	expect(res.ok()).toBe(true);
}

/** No violation and no unmet expectation in the journal of the synthetic source. */
export async function contratTenu(request: APIRequestContext): Promise<void> {
	const res = await request.get(`${E2E_SOURCE_ORIGIN}/__requests`);
	expect(res.ok()).toBe(true);
	const audit = (await res.json()) as { violations: unknown[]; unmetExpectations: unknown[] };
	expect(audit.violations, JSON.stringify(audit.violations)).toEqual([]);
	expect(audit.unmetExpectations, JSON.stringify(audit.unmetExpectations)).toEqual([]);
}

// --- Shared reading helpers of both recette specs (synthetic and real) ------------------------------

/** Navigates and waits for hydration (the root layout reads the session once hydrated). */
export async function ouvrir(page: Page, url: string) {
	const hydrated = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
	const reponse = await page.goto(url);
	await hydrated;
	return reponse;
}

/** Text with every no-break space as an ordinary one, whitespace collapsed. */
export const norm = (texte: string | null | undefined) => (texte ?? '').replace(/[\u00a0\u202f]/g, ' ').replace(/\s+/g, ' ').trim();

/**
 * Layout invariants of UI-01 measured in one pass inside the page (same method as the phase 3
 * recette): document overflow, interactive `title`, `position: fixed` outside the skip link and
 * `<dialog>` (the site header is sticky), boxes outside the window outside the comparator region.
 */
export function mesurerInvariants(regionLabel: string) {
	const visible = (el: Element) => {
		const box = el.getBoundingClientRect();
		return box.width > 1 && box.height > 1 && el.checkVisibility({ visibilityProperty: true });
	};
	const nom = (el: Element) => `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ''}${el.classList.length ? `.${[...el.classList].join('.')}` : ''}`;
	const dansDefilant = (el: Element) => {
		for (let parent = el.parentElement; parent; parent = parent.parentElement) if (parent.getAttribute('aria-label') === regionLabel) return true;
		return false;
	};
	const tous = [...document.querySelectorAll('body *')];
	return {
		scrollWidth: document.documentElement.scrollWidth,
		innerWidth: window.innerWidth,
		titres: [...document.querySelectorAll('a[title], button[title], summary[title], [role="button"][title], input[title], select[title], [tabindex][title]')].map(nom),
		fixes: tous.filter((el) => getComputedStyle(el).position === 'fixed').filter((el) => el.tagName !== 'DIALOG' && !el.closest('dialog') && !el.classList.contains('skip-link')).map(nom),
		horsCadre: tous
			.filter((el) => visible(el) && !dansDefilant(el) && !el.closest('dialog') && !el.classList.contains('skip-link'))
			.filter((el) => {
				const box = el.getBoundingClientRect();
				return box.left < -1 || box.right > window.innerWidth + 1;
			})
			.map(nom)
	};
}

export const REGION_COMPARATEUR = 'Tableau de comparaison';
