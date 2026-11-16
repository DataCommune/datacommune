/** Public snapshot observed on 2026-10-12 from the diagnostic artifacts, then anonymised: address,
 * BAN key and DPE numbers are fictitious (export guard `scripts/check-real-data.py`). */
export const ADRESSE_85191_zz05_00011_OBSERVED = {
  "statut": "ok",
  "donnees": {
    "identite": {
      "etat": "etablie",
      "ban_id": "85191_zz05_00011",
      "libelle": "11 Rue du Modèle, 85000 La Roche-sur-Yon",
      "numero": "11",
      "rep": null,
      "nom_voie": "Rue du Modèle",
      "code_postal": "85000",
      "commune": {
        "code_insee": "85191",
        "nom": "La Roche-sur-Yon"
      },
      "departement": {
        "code": "85",
        "nom": "Vendée"
      },
      "parcelle": {
        "etat": "point_hors_parcelle",
        "id_parcelle": null,
        "nombre_candidats": null,
        "rattachement": null,
        "motif": "point_hors_parcelle"
      }
    },
    "couverture": {
      "etat": "heberge",
      "motif": null
    },
    "reperes": {
      "dpe": {
        "field_id": "adresse.dpe.nombre_declares",
        "etat": "value",
        "motif": null,
        "valeur": 0,
        "unite": null,
        "periode": "2026-10-08",
        "source": {
          "cle": "dpe",
          "nom": "Diagnostics de performance énergétique (DPE)",
          "producteur": "ADEME",
          "url": "https://data.ademe.fr/datasets/dpe03existant"
        },
        "rattachement": "adresse_saisie_diagnostiqueur",
        "reference": null,
        "selection": {
          "etat": "indeterminee",
          "date": null,
          "document": null,
          "documents_ex_aequo": [],
          "motif": "aucun_document"
        }
      },
      "ventes": {
        "nombre": {
          "field_id": "adresse.dvf.ventes_parcelle.nombre",
          "etat": "not_available",
          "motif": "point_hors_parcelle",
          "valeur": null,
          "unite": "count",
          "periode": null,
          "source": {
            "cle": "dvf",
            "nom": "Demandes de valeurs foncières (DVF)",
            "producteur": "DGFiP / Etalab",
            "url": "https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres-geolocalisees"
          },
          "rattachement": "parcelle_du_point",
          "reference": null
        },
        "derniere": {
          "field_id": "adresse.dvf.ventes_parcelle.derniere",
          "etat": "not_available",
          "motif": "point_hors_parcelle",
          "valeur": null,
          "unite": "none",
          "periode": null,
          "source": {
            "cle": "dvf",
            "nom": "Demandes de valeurs foncières (DVF)",
            "producteur": "DGFiP / Etalab",
            "url": "https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres-geolocalisees"
          },
          "rattachement": "parcelle_du_point",
          "reference": null
        }
      },
      "argiles": {
        "field_id": "adresse.risques.argiles_au_point",
        "etat": "value",
        "motif": null,
        "valeur": "Exposition moyenne",
        "unite": "none",
        "periode": "2026-10-12",
        "source": {
          "cle": "georisques",
          "nom": "Géorisques",
          "producteur": "BRGM / MTE",
          "url": "https://www.georisques.gouv.fr"
        },
        "rattachement": "au_point",
        "reference": null
      },
      "reference_commune": {
        "maisons": {
          "field_id": "adresse.reference_commune.prix_median_maison",
          "etat": "value",
          "motif": null,
          "valeur": 2357,
          "unite": "euro_per_m2",
          "periode": "2024-12-31 → 2025-12-31",
          "source": {
            "cle": "dvf",
            "nom": "Demandes de valeurs foncières (DVF)",
            "producteur": "DGFiP / Etalab",
            "url": "https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres-geolocalisees"
          },
          "rattachement": "commune_reference",
          "reference": {
            "valeur": 2357,
            "unite": "euro_per_m2",
            "territoire": {
              "code_insee": "85191",
              "nom": "La Roche-sur-Yon"
            },
            "source": {
              "cle": "dvf",
              "nom": "Demandes de valeurs foncières (DVF)",
              "producteur": "DGFiP / Etalab",
              "url": "https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres-geolocalisees"
            },
            "millesime": "2024-12-31 → 2025-12-31",
            "url": "https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres-geolocalisees"
          }
        },
        "appartements": {
          "field_id": "adresse.reference_commune.prix_median_appartement",
          "etat": "value",
          "motif": null,
          "valeur": 2429,
          "unite": "euro_per_m2",
          "periode": "2024-12-31 → 2025-12-31",
          "source": {
            "cle": "dvf",
            "nom": "Demandes de valeurs foncières (DVF)",
            "producteur": "DGFiP / Etalab",
            "url": "https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres-geolocalisees"
          },
          "rattachement": "commune_reference",
          "reference": {
            "valeur": 2429,
            "unite": "euro_per_m2",
            "territoire": {
              "code_insee": "85191",
              "nom": "La Roche-sur-Yon"
            },
            "source": {
              "cle": "dvf",
              "nom": "Demandes de valeurs foncières (DVF)",
              "producteur": "DGFiP / Etalab",
              "url": "https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres-geolocalisees"
            },
            "millesime": "2024-12-31 → 2025-12-31",
            "url": "https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres-geolocalisees"
          }
        }
      }
    },
    "themes": [
      {
        "cle": "batiment_parcelle",
        "titre": "Bâtiment et parcelle",
        "applicable": true,
        "etat": "not_available",
        "motif": "point_hors_parcelle",
        "groupes": [
          {
            "cle": "lieu",
            "titre": null,
            "champs": [
              {
                "field_id": "adresse.batiment.rnb_id",
                "etat": "value",
                "motif": null,
                "valeur": "8CF491SSG29F",
                "unite": "none",
                "periode": "Export RNB 2026-09",
                "source": {
                  "cle": "rnb",
                  "nom": "Référentiel national des bâtiments (RNB)",
                  "producteur": "beta.gouv.fr / CSTB",
                  "url": "https://rnb.beta.gouv.fr"
                },
                "rattachement": "batiment_rnb_de_l_adresse",
                "reference": null,
                "elements": [],
                "liste_incomplete": false,
                "rayon_m": null
              }
            ]
          }
        ],
        "liste_incomplete": false
      },
      {
        "cle": "risques",
        "titre": "Risques",
        "applicable": true,
        "etat": "value",
        "motif": null,
        "groupes": [
          {
            "cle": "au_point",
            "titre": "Au point de l'adresse",
            "champs": [
              {
                "field_id": "adresse.risques.argiles_au_point",
                "etat": "value",
                "motif": null,
                "valeur": "Exposition moyenne",
                "unite": "none",
                "periode": "2026-10-12",
                "source": {
                  "cle": "georisques",
                  "nom": "Géorisques",
                  "producteur": "BRGM / MTE",
                  "url": "https://www.georisques.gouv.fr"
                },
                "rattachement": "au_point",
                "reference": null,
                "elements": [],
                "liste_incomplete": false,
                "rayon_m": null
              }
            ]
          },
          {
            "cle": "commune",
            "titre": "Sur la commune (référence)",
            "champs": [
              {
                "field_id": "adresse.risques.liste_commune",
                "etat": "value",
                "motif": null,
                "valeur": 11,
                "unite": "count",
                "periode": "2026-10-12",
                "source": {
                  "cle": "georisques",
                  "nom": "Géorisques",
                  "producteur": "BRGM / MTE",
                  "url": "https://www.georisques.gouv.fr"
                },
                "rattachement": "commune_reference",
                "reference": null,
                "elements": [
                  {
                    "libelle": "Affaissements et effondrements d'origine naturelle (cavités souterraines)",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "Inondation",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "Mouvement de terrain",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "Neige et pluies verglaçantes",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "Par une crue à débordement lent de cours d'eau",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "Phénomène lié à l'atmosphère",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "Radon",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "Rupture de barrage",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "Séisme",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "Tassements différentiels",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "Transport de marchandises dangereuses",
                    "distance_m": null,
                    "nature": null
                  }
                ],
                "liste_incomplete": false,
                "rayon_m": null
              },
              {
                "field_id": "adresse.risques.radon_commune",
                "etat": "value",
                "motif": null,
                "valeur": "3",
                "unite": "none",
                "periode": "2026-10-12",
                "source": {
                  "cle": "georisques",
                  "nom": "Géorisques",
                  "producteur": "BRGM / MTE",
                  "url": "https://www.georisques.gouv.fr"
                },
                "rattachement": "commune_reference",
                "reference": null,
                "elements": [],
                "liste_incomplete": false,
                "rayon_m": null
              },
              {
                "field_id": "adresse.risques.icpe_commune",
                "etat": "value",
                "motif": null,
                "valeur": 60,
                "unite": "count",
                "periode": "2026-10-12",
                "source": {
                  "cle": "georisques",
                  "nom": "Géorisques",
                  "producteur": "BRGM / MTE",
                  "url": "https://www.georisques.gouv.fr"
                },
                "rattachement": "commune_reference",
                "reference": null,
                "elements": [],
                "liste_incomplete": false,
                "rayon_m": null
              }
            ]
          }
        ],
        "liste_incomplete": false
      },
      {
        "cle": "urbanisme",
        "titre": "Urbanisme",
        "applicable": true,
        "etat": "value",
        "motif": null,
        "groupes": [
          {
            "cle": "au_point",
            "titre": null,
            "champs": [
              {
                "field_id": "adresse.urbanisme.zone_plu",
                "etat": "value",
                "motif": null,
                "valeur": 1,
                "unite": "count",
                "periode": "2026-10-12",
                "source": {
                  "cle": "urbanisme",
                  "nom": "API Carto GPU",
                  "producteur": "IGN",
                  "url": "https://apicarto.ign.fr"
                },
                "rattachement": "au_point",
                "reference": null,
                "elements": [
                  {
                    "libelle": "UB · UB - Zone urbaine mixte",
                    "distance_m": null,
                    "nature": null
                  }
                ],
                "liste_incomplete": false,
                "rayon_m": null
              },
              {
                "field_id": "adresse.urbanisme.prescriptions",
                "etat": "value",
                "motif": null,
                "valeur": 1,
                "unite": "count",
                "periode": "2026-10-12",
                "source": {
                  "cle": "urbanisme",
                  "nom": "API Carto GPU",
                  "producteur": "IGN",
                  "url": "https://apicarto.ign.fr"
                },
                "rattachement": "au_point",
                "reference": null,
                "elements": [
                  {
                    "libelle": "OAP TVBN",
                    "distance_m": null,
                    "nature": null
                  }
                ],
                "liste_incomplete": false,
                "rayon_m": null
              }
            ]
          }
        ],
        "liste_incomplete": false
      },
      {
        "cle": "reseaux_transports",
        "titre": "Réseaux et transports",
        "applicable": true,
        "etat": "value",
        "motif": null,
        "groupes": [
          {
            "cle": "lieu",
            "titre": null,
            "champs": [
              {
                "field_id": "adresse.connectivite.eligibilite_fibre",
                "etat": "value",
                "motif": null,
                "valeur": true,
                "unite": "none",
                "periode": "2026_T2",
                "source": {
                  "cle": "connectivite",
                  "nom": "Ma Connexion Internet (Arcep)",
                  "producteur": "Arcep",
                  "url": "https://www.data.gouv.fr/datasets/ma-connexion-internet"
                },
                "rattachement": "eligibilite_adresse",
                "reference": null,
                "elements": [],
                "liste_incomplete": false,
                "rayon_m": null
              },
              {
                "field_id": "adresse.connectivite.debit",
                "etat": "value",
                "motif": null,
                "valeur": "THD1G",
                "unite": "none",
                "periode": "2026_T2",
                "source": {
                  "cle": "connectivite",
                  "nom": "Ma Connexion Internet (Arcep)",
                  "producteur": "Arcep",
                  "url": "https://www.data.gouv.fr/datasets/ma-connexion-internet"
                },
                "rattachement": "eligibilite_adresse",
                "reference": null,
                "elements": [],
                "liste_incomplete": false,
                "rayon_m": null
              },
              {
                "field_id": "adresse.transports.arret_proche",
                "etat": "value",
                "motif": null,
                "valeur": 3,
                "unite": "count",
                "periode": "2026-09-25",
                "source": {
                  "cle": "transports",
                  "nom": "Arrêts de transport",
                  "producteur": "PAN transport",
                  "url": "https://transport.data.gouv.fr/datasets/arrets-de-transport-en-france"
                },
                "rattachement": "rayon_du_point",
                "reference": null,
                "elements": [
                  {
                    "libelle": "Pâquerettes",
                    "distance_m": 76,
                    "nature": "arret"
                  },
                  {
                    "libelle": "Pâquerettes",
                    "distance_m": 93,
                    "nature": "arret"
                  },
                  {
                    "libelle": "Square Lange",
                    "distance_m": 201,
                    "nature": "arret"
                  }
                ],
                "liste_incomplete": true,
                "rayon_m": 1000
              }
            ]
          }
        ],
        "liste_incomplete": true
      }
    ]
  },
  "meta": {
    "source": [
      {
        "nom": "Base Adresse Nationale (BAN)",
        "licence": "Licence Ouverte 2.0",
        "url": "https://adresse.data.gouv.fr",
        "producteur": "DINUM / IGN"
      },
      {
        "nom": "Cadastre Etalab",
        "licence": "Licence Ouverte 1.0",
        "url": "https://cadastre.data.gouv.fr",
        "producteur": "DGFiP / Etalab"
      }
    ],
    "resolution": null
  }
} as const;

/** Companion DPE response observed with the same public snapshot. */
export const ADRESSE_85191_zz05_00011_DPE_OBSERVED = {
  "statut": "ok",
  "donnees": {
    "etat": "value",
    "motif": null,
    "rattachement": "adresse_saisie_diagnostiqueur",
    "source": {
      "cle": "dpe",
      "nom": "Diagnostics de performance énergétique (DPE)",
      "producteur": "ADEME",
      "url": "https://data.ademe.fr/datasets/dpe03existant"
    },
    "total": 0,
    "page": 1,
    "pages": 1,
    "par_page": 10,
    "generation": "2e71bc44da41d218",
    "distribution": {
      "A": 0,
      "B": 0,
      "C": 0,
      "D": 0,
      "E": 0,
      "F": 0,
      "G": 0,
      "unknown": 0
    },
    "date_import": "2026-10-08",
    "documents": [],
    "selection": {
      "etat": "indeterminee",
      "date": null,
      "document": null,
      "documents_ex_aequo": [],
      "motif": "aucun_document"
    },
    "signal_plusieurs_logements": null,
    "constat_d110": null
  },
  "meta": {
    "source": [
      {
        "nom": "Base Adresse Nationale (BAN)",
        "licence": "Licence Ouverte 2.0",
        "url": "https://adresse.data.gouv.fr",
        "producteur": "DINUM / IGN"
      },
      {
        "nom": "Diagnostics de performance énergétique (DPE)",
        "licence": "Licence Ouverte 2.0",
        "url": "https://data.ademe.fr/datasets/dpe03existant",
        "producteur": "ADEME"
      }
    ],
    "resolution": null
  }
} as const;
