// Served answers of `GET /v1/dpe/{numero}` shared by the e2e specs and the unit tests of the reading
// inventory (plan 04.5-12). No Playwright import: `lecture.test.ts` reads this module under Vitest.

const SOURCE_DPE = { cle: 'dpe', nom: 'Diagnostics de performance énergétique (DPE)', producteur: 'ADEME', url: 'https://data.ademe.fr/datasets/dpe03existant' };

function valeurDpe(partiel: Record<string, unknown>) {
	return { field_id: 'x', etat: 'value', valeur: null, unite: null, periode: null, motif: null, source: SOURCE_DPE, rattachement: 'adresse_saisie_diagnostiqueur', reference: null, ...partiel };
}

/** A real ADEME number, the document of the 2026-10-24 UI review (`/dpe/2644E8000030X`). */
export const REEL = '2644E8000030X';

/**
 * `GET /v1/dpe/2644E8000030X` rebuilt from OBSERVED values, never a production truth for the tests:
 * every member but `mesure_m2` is the public model of `https://datacommune.fr/dpe/2644E8000030X`
 * read on 2026-10-26 (`__data.json`, the page then ignoring `mesure_m2`); the two per-m² values are
 * the columns `conso_5_usages_par_m2_ep` (318) and `emission_ges_5_usages_par_m2` (25) of its line in
 * the ADEME open dataset `dpe03existant`, read the same day. Its surface is not reported: no quotient
 * could even be made (plan 04.5-12).
 */
export const DOCUMENT_REEL = {
	statut: 'ok',
	donnees: {
		numero: REEL,
		source_identity: { reference: REEL, sources: [{ dataset: 'dpe03existant', url: 'https://data.ademe.fr/datasets/dpe03existant' }] },
		disponibilite: 'disponible',
		methode: 'actuelle',
		dates: { etablissement: '2026-02-27', fin_validite: '2036-02-26' },
		reperes: {
			etiquette_energie: {
				classe: valeurDpe({ field_id: 'dpe.document.etiquette_dpe', valeur: 'E', unite: 'none' }),
				mesure: valeurDpe({ field_id: 'dpe.document.conso_ep', valeur: 127982.4, unite: 'kwh_ep_per_year' }),
				mesure_m2: valeurDpe({ field_id: 'dpe.document.conso_ep_m2', valeur: 318, unite: 'kwh_ep_per_m2_per_year' })
			},
			etiquette_climat: {
				classe: valeurDpe({ field_id: 'dpe.document.etiquette_ges', valeur: 'C', unite: 'none' }),
				mesure: valeurDpe({ field_id: 'dpe.document.emissions_ges', valeur: 10257.5, unite: 'kg_co2_eq_per_year' }),
				mesure_m2: valeurDpe({ field_id: 'dpe.document.emissions_ges_m2', valeur: 25, unite: 'kg_co2_eq_per_m2_per_year' })
			},
			logement_decrit: {
				type_batiment: valeurDpe({ field_id: 'dpe.document.type_batiment', valeur: 'immeuble', unite: 'none' }),
				surface: valeurDpe({ field_id: 'dpe.document.surface_habitable', etat: 'not_reported', valeur: null })
			},
			cout_annuel: valeurDpe({ field_id: 'dpe.document.cout_annuel', valeur: 14750.1, unite: 'euro_per_year' })
		},
		statut: { code: 'en_vigueur', validite: 'not_expired_by_date', date: null, remplace_par: null, remplace: null },
		caracteristiques: [
			valeurDpe({ field_id: 'dpe.document.type_batiment', valeur: 'immeuble', unite: 'none' }),
			valeurDpe({ field_id: 'dpe.document.surface_habitable', etat: 'not_reported', valeur: null }),
			valeurDpe({ field_id: 'dpe.document.periode_construction', valeur: 'avant 1948', unite: 'none' }),
			valeurDpe({ field_id: 'dpe.document.energie_chauffage', valeur: 'Électricité', unite: 'none' }),
			valeurDpe({ field_id: 'dpe.document.installation_chauffage', valeur: 'individuel', unite: 'none' }),
			valeurDpe({ field_id: 'dpe.document.isolation', valeur: 'insuffisante', unite: 'none' })
		],
		adresse_declaree: { etat: 'publiee', texte: '4 rue des Tests', motif: null },
		adresse_ban: {
			etat: 'retenue', ban_id: '44109_zz04_00004', libelle: '4 Rue des Tests, 44000 Nantes', numero: '4', rep: null, nom_voie: 'Rue des Tests',
			commune: { code_insee: '44109', nom: 'Nantes' }, motif: null
		},
		adresse_divergente: false,
		provenance: { source: 'ADEME', date_import: '2026-10-19' },
		association: 'source_ban_equality',
		address_check: 'unverified'
	},
	meta: { source: [] }
};

/** A SYNTHETIC number of a document the ADEME no longer publishes. */
export const RETIRE = '2285E0000007R';

/**
 * A retired document as the contract serves it (`DocumentDpePublic._retire_sans_descriptif`): its
 * number, datasets, method, status « retire » and provenance; no date, landmark, characteristic or
 * address (plan 04.5-12: no landmark, no per-m² value, no total).
 */
export const DOCUMENT_RETIRE = {
	statut: 'ok',
	donnees: {
		numero: RETIRE,
		source_identity: { reference: RETIRE, sources: [{ dataset: 'dpe03existant', url: 'https://data.ademe.fr/datasets/dpe03existant' }] },
		disponibilite: 'retire',
		methode: 'actuelle',
		statut: { code: 'retire', validite: null, date: null, remplace_par: null, remplace: null },
		dates: null,
		reperes: null,
		caracteristiques: [],
		adresse_declaree: null,
		adresse_ban: null,
		adresse_divergente: null,
		provenance: { source: 'ADEME', date_import: '2026-09-30' },
		association: 'source_ban_equality',
		address_check: 'unverified'
	},
	meta: { source: [] }
};
