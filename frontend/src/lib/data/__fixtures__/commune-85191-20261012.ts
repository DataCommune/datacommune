/**
 * Frozen public response observed at deployment SHA 9786664 and replayed locally at code 81e13c0.
 * No new production read was performed. The inclusive DVF window is the sole diagnostic addition.
 */
export const COMMUNE_85191_OBSERVED = {
	"code_insee": "85191",
	"indicateurs_dvf": null,
	"indicateurs_dpe": null,
	"nom_commune": "La Roche-sur-Yon",
	"code_departement": "85",
	"code_region": "52",
	"population": 54699,
	"nb_menages": 28216,
	"nb_logements": 31207,
	"nb_logements_vacants_recensement": 1870,
	"densite": 621.4,
	"superficie": 88.02,
	"annee_demographie": 2022,
	"revenu_median": 21940,
	"taux_pauvrete": 15,
	"indice_gini": 0.361,
	"part_imposee": 49,
	"annee_revenus": 2021,
	"zone_abc": "B1",
	"zone_abc_reclassement": false,
	"taux_vacance": 7.59,
	"nb_vacants_plus_2ans": 296,
	"annee_vacance": 2026,
	"taux_tfpb": 51.05,
	"taux_th_rs": 36.14,
	"taux_teom": null,
	"nom_epci": "CA La Roche-sur-Yon - Agglomération",
	"annee_fiscalite": 2025,
	"loyer_m2_appartement": 11.81,
	"loyer_m2_maison": 10.79,
	"annee_loyers": 2025,
	"loyers_appartement": {
		"code_insee": "85191",
		"nom_commune": "La Roche-sur-Yon",
		"type_habitat": "appartement",
		"loyer_m2": 11.81,
		"loyer_m2_bas": 9.53,
		"loyer_m2_haut": 14.63,
		"r2": 0.822,
		"nb_observations": 7341,
		"annee": 2025,
		"id_zone": "1103",
		"type_prediction": "commune",
		"nb_observations_commune": 7341,
		"nb_observations_maille": 7628,
		"r2_ajuste": 0.822321154895052,
		"trimestre_reference": 3,
		"charges_comprises": true,
		"meuble": false,
		"surface_reference_m2": 52,
		"surface_piece_reference_m2": 22.2
	},
	"loyers_maison": {
		"code_insee": "85191",
		"nom_commune": "La Roche-sur-Yon",
		"type_habitat": "maison",
		"loyer_m2": 10.79,
		"loyer_m2_bas": 8.38,
		"loyer_m2_haut": 13.9,
		"r2": 0.76,
		"nb_observations": 1711,
		"annee": 2025,
		"id_zone": "1728",
		"type_prediction": "commune",
		"nb_observations_commune": 1711,
		"nb_observations_maille": 2196,
		"r2_ajuste": 0.760196813575035,
		"trimestre_reference": 3,
		"charges_comprises": true,
		"meuble": false,
		"surface_reference_m2": 92,
		"surface_piece_reference_m2": 22.4
	},
	"prix_m2_median": 2391,
	"nb_mutations_12m": 835,
	"prix_m2_median_maison": 2357,
	"nb_mutations_12m_maison": 460,
	"prix_m2_median_appartement": 2429,
	"nb_mutations_12m_appartement": 375,
	"dpe_passoires_pct": 3.3,
	"dpe_median": "C",
	"annee_construction_mediane": 1997,
	"nb_equipements": 2392,
	"annee_equipements": 2025,
	"nb_etablissements_scolaires": 4,
	"nb_monuments": 8,
	"nb_proprietaires_pm": null,
	"surface_pm_ha": 3006.5,
	"couverture": {
		"demographie": {
			"etat": "heberge",
			"motif": null,
			"departements": [
				"44",
				"85"
			]
		},
		"revenus": {
			"etat": "heberge",
			"motif": null,
			"departements": [
				"44",
				"85"
			]
		},
		"zonage_abc": {
			"etat": "heberge",
			"motif": null,
			"departements": [
				"44",
				"85"
			]
		},
		"vacance": {
			"etat": "heberge",
			"motif": null,
			"departements": [
				"44",
				"85"
			]
		},
		"fiscalite": {
			"etat": "heberge",
			"motif": null,
			"departements": [
				"44",
				"85"
			]
		},
		"loyers": {
			"etat": "heberge",
			"motif": null,
			"departements": [
				"44",
				"85"
			]
		},
		"dvf": {
			"etat": "heberge",
			"motif": null,
			"departements": [
				"44",
				"85"
			]
		},
		"dpe": {
			"etat": "heberge",
			"motif": null,
			"departements": [
				"44",
				"85"
			]
		},
		"equipements": {
			"etat": "heberge",
			"motif": null,
			"departements": [
				"44",
				"85"
			]
		},
		"scolaire": {
			"etat": "heberge",
			"motif": null,
			"departements": [
				"44",
				"85"
			]
		},
		"monuments": {
			"etat": "heberge",
			"motif": null,
			"departements": [
				"44",
				"85"
			]
		},
		"foncier_pm": {
			"etat": "heberge",
			"motif": null,
			"departements": [
				"44",
				"85"
			]
		},
		"connectivite": {
			"etat": "heberge",
			"motif": null,
			"departements": [
				"44",
				"85"
			]
		}
	},
	"etats": {
		"taux_teom": {
			"etat": "not_available",
			"motif": "La source a une ligne pour la commune à ce millésime, sans cette valeur."
		},
		"nb_proprietaires_pm": {
			"etat": "not_available",
			"motif": "Identifiants SIREN incomplets dans les droits fonciers chargés : aucun compte publié."
		}
	},
	"fibre_pct": 98,
	"fenetre_mediane_dvf": "2024-12-31 → 2025-12-31"
} as const;

/** Complete 13-source metadata served with the same frozen public response. */
export const COMMUNE_85191_SOURCES = [
	{
		"nom": "Recensement de la population",
		"licence": "Licence Ouverte 2.0",
		"url": "https://www.insee.fr/fr/statistiques/8290591",
		"producteur": "INSEE",
		"dataset": "demographie",
		"periode": {
			"statut": "inconnue",
			"label": null,
			"debut": null,
			"fin": null
		},
		"derniere_importation_reussie": "2026-09-24T23:36:12.305874Z",
		"couverture": {
			"type": "inconnue",
			"departements": null
		},
		"cadence": {
			"statut": "non_publiee_ou_irreguliere",
			"label": null,
			"evidence_url": null,
			"intervalle_jours": null
		},
		"etat": "inconnu"
	},
	{
		"nom": "Filosofi (revenus et pauvreté)",
		"licence": "Licence Ouverte 2.0",
		"url": "https://www.insee.fr/fr/statistiques/7756855",
		"producteur": "INSEE",
		"dataset": "revenus",
		"periode": {
			"statut": "inconnue",
			"label": null,
			"debut": null,
			"fin": null
		},
		"derniere_importation_reussie": "2026-09-24T23:35:43.724146Z",
		"couverture": {
			"type": "inconnue",
			"departements": null
		},
		"cadence": {
			"statut": "non_publiee_ou_irreguliere",
			"label": null,
			"evidence_url": null,
			"intervalle_jours": null
		},
		"etat": "inconnu"
	},
	{
		"nom": "Zonage ABC (tension immobilière)",
		"licence": "Licence Ouverte 2.0",
		"url": "https://www.data.gouv.fr/datasets/liste-des-communes-selon-le-zonage-abc",
		"producteur": "Ministère de la Transition écologique",
		"dataset": "zonage_abc",
		"periode": {
			"statut": "inconnue",
			"label": null,
			"debut": null,
			"fin": null
		},
		"derniere_importation_reussie": "2026-09-24T23:35:22.766561Z",
		"couverture": {
			"type": "inconnue",
			"departements": null
		},
		"cadence": {
			"statut": "non_publiee_ou_irreguliere",
			"label": null,
			"evidence_url": null,
			"intervalle_jours": null
		},
		"etat": "inconnu"
	},
	{
		"nom": "Logements vacants (LOVAC)",
		"licence": "Licence Ouverte 2.0",
		"url": "https://www.data.gouv.fr/datasets/logements-vacants-du-parc-prive-par-commune-departement-region-france-de-2020-a-2026",
		"producteur": "CEREMA",
		"dataset": "vacance",
		"periode": {
			"statut": "inconnue",
			"label": null,
			"debut": null,
			"fin": null
		},
		"derniere_importation_reussie": "2026-09-25T21:12:07.564704Z",
		"couverture": {
			"type": "inconnue",
			"departements": null
		},
		"cadence": {
			"statut": "non_publiee_ou_irreguliere",
			"label": null,
			"evidence_url": null,
			"intervalle_jours": null
		},
		"etat": "inconnu"
	},
	{
		"nom": "Fiscalité locale des particuliers",
		"licence": "Licence Ouverte 2.0",
		"url": "https://www.data.gouv.fr/datasets/fiscalite-locale-des-particuliers",
		"producteur": "DGFiP",
		"dataset": "fiscalite",
		"periode": {
			"statut": "inconnue",
			"label": null,
			"debut": null,
			"fin": null
		},
		"derniere_importation_reussie": "2026-09-24T23:35:07.120928Z",
		"couverture": {
			"type": "inconnue",
			"departements": null
		},
		"cadence": {
			"statut": "non_publiee_ou_irreguliere",
			"label": null,
			"evidence_url": null,
			"intervalle_jours": null
		},
		"etat": "inconnu"
	},
	{
		"nom": "Carte des loyers",
		"licence": "Licence Ouverte 2.0",
		"url": "https://www.data.gouv.fr/datasets/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025",
		"producteur": "ANIL / MTE",
		"dataset": "loyers",
		"periode": {
			"statut": "inconnue",
			"label": null,
			"debut": null,
			"fin": null
		},
		"derniere_importation_reussie": "2026-09-24T23:34:26.556383Z",
		"couverture": {
			"type": "inconnue",
			"departements": null
		},
		"cadence": {
			"statut": "non_publiee_ou_irreguliere",
			"label": null,
			"evidence_url": null,
			"intervalle_jours": null
		},
		"etat": "inconnu"
	},
	{
		"nom": "Demandes de valeurs foncières (DVF)",
		"licence": "Licence Ouverte 2.0",
		"url": "https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres-geolocalisees",
		"producteur": "DGFiP / Etalab",
		"dataset": "dvf",
		"periode": {
			"statut": "connue",
			"label": "2021-01-02 → 2025-12-31",
			"debut": null,
			"fin": null
		},
		"derniere_importation_reussie": "2026-09-25T21:55:38.499963Z",
		"couverture": {
			"type": "departements",
			"departements": [
				"44",
				"85"
			]
		},
		"cadence": {
			"statut": "non_publiee_ou_irreguliere",
			"label": null,
			"evidence_url": null,
			"intervalle_jours": null
		},
		"etat": "inconnu"
	},
	{
		"nom": "Diagnostics de performance énergétique (DPE)",
		"licence": "Licence Ouverte 2.0",
		"url": "https://data.ademe.fr/datasets/dpe03existant",
		"producteur": "ADEME",
		"dataset": "dpe",
		"periode": {
			"statut": "inconnue",
			"label": null,
			"debut": null,
			"fin": null
		},
		"derniere_importation_reussie": "2026-10-08T10:43:15.141979Z",
		"couverture": {
			"type": "departements",
			"departements": [
				"44",
				"85"
			]
		},
		"cadence": {
			"statut": "official",
			"label": "Hebdomadaire",
			"evidence_url": "https://data.ademe.fr/datasets/dpe03existant",
			"intervalle_jours": 7
		},
		"etat": "inconnu"
	},
	{
		"nom": "Base permanente des équipements (BPE)",
		"licence": "Licence Ouverte 2.0",
		"url": "https://www.insee.fr/fr/statistiques/8217537",
		"producteur": "INSEE",
		"dataset": "equipements",
		"periode": {
			"statut": "inconnue",
			"label": null,
			"debut": null,
			"fin": null
		},
		"derniere_importation_reussie": "2026-09-25T16:02:39.252744Z",
		"couverture": {
			"type": "inconnue",
			"departements": null
		},
		"cadence": {
			"statut": "non_publiee_ou_irreguliere",
			"label": null,
			"evidence_url": null,
			"intervalle_jours": null
		},
		"etat": "inconnu"
	},
	{
		"nom": "Carte scolaire des collèges",
		"licence": "Licence Ouverte 2.0",
		"url": "https://www.data.gouv.fr/datasets/carte-scolaire-des-colleges-publics",
		"producteur": "DGESCO / MEN",
		"dataset": "scolaire",
		"periode": {
			"statut": "inconnue",
			"label": null,
			"debut": null,
			"fin": null
		},
		"derniere_importation_reussie": "2026-09-24T23:30:58.185326Z",
		"couverture": {
			"type": "inconnue",
			"departements": null
		},
		"cadence": {
			"statut": "non_publiee_ou_irreguliere",
			"label": null,
			"evidence_url": null,
			"intervalle_jours": null
		},
		"etat": "inconnu"
	},
	{
		"nom": "Monuments historiques",
		"licence": "Licence Ouverte 2.0",
		"url": "https://www.data.gouv.fr/datasets/immeubles-proteges-au-titre-des-monuments-historiques-2",
		"producteur": "Ministère de la Culture",
		"dataset": "monuments",
		"periode": {
			"statut": "inconnue",
			"label": null,
			"debut": null,
			"fin": null
		},
		"derniere_importation_reussie": "2026-09-25T16:03:24.433503Z",
		"couverture": {
			"type": "inconnue",
			"departements": null
		},
		"cadence": {
			"statut": "non_publiee_ou_irreguliere",
			"label": null,
			"evidence_url": null,
			"intervalle_jours": null
		},
		"etat": "inconnu"
	},
	{
		"nom": "Fichiers des personnes morales (MAJIC)",
		"licence": "Licence Ouverte 2.0",
		"url": "https://www.data.gouv.fr/datasets/fichiers-des-locaux-et-des-parcelles-des-personnes-morales",
		"producteur": "DGFiP / Etalab",
		"dataset": "foncier_pm",
		"periode": {
			"statut": "inconnue",
			"label": null,
			"debut": null,
			"fin": null
		},
		"derniere_importation_reussie": "2026-09-24T23:24:59.101051Z",
		"couverture": {
			"type": "inconnue",
			"departements": null
		},
		"cadence": {
			"statut": "non_publiee_ou_irreguliere",
			"label": null,
			"evidence_url": null,
			"intervalle_jours": null
		},
		"etat": "inconnu"
	},
	{
		"nom": "Ma Connexion Internet (Arcep)",
		"licence": "Licence Ouverte 2.0",
		"url": "https://www.data.gouv.fr/datasets/ma-connexion-internet",
		"producteur": "Arcep",
		"dataset": "connectivite",
		"periode": {
			"statut": "connue",
			"label": "2026_T2",
			"debut": null,
			"fin": null
		},
		"derniere_importation_reussie": "2026-09-26T11:43:46.433269Z",
		"couverture": {
			"type": "departements",
			"departements": [
				"44",
				"85"
			]
		},
		"cadence": {
			"statut": "non_publiee_ou_irreguliere",
			"label": null,
			"evidence_url": null,
			"intervalle_jours": null
		},
		"etat": "inconnu"
	}
] as const;
