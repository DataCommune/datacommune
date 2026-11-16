/** Public anonymous snapshots observed on 2026-10-13 from deployed source a2a8020, then anonymised:
 * address, BAN key, parcel, DPE numbers, sale dates and prices are fictitious (export guard
 * `scripts/check-real-data.py`). */
export const ADRESSE_44109_zz04_00004_OBSERVED = {
  "statut": "ok",
  "donnees": {
    "identite": {
      "etat": "etablie",
      "ban_id": "44109_zz04_00004",
      "libelle": "4 Rue des Tests, 44000 Nantes",
      "numero": "4",
      "rep": null,
      "nom_voie": "Rue des Tests",
      "code_postal": "44000",
      "commune": {
        "code_insee": "44109",
        "nom": "Nantes"
      },
      "departement": {
        "code": "44",
        "nom": "Loire-Atlantique"
      },
      "parcelle": {
        "etat": "unique",
        "id_parcelle": "44109000ZZ0062",
        "nombre_candidats": 1,
        "rattachement": "parcelle_du_point",
        "motif": null
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
        "valeur": 5,
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
          "etat": "unique",
          "date": "2026-03-16",
          "document": {
            "numero": "2644E8000029X",
            "etiquette_dpe": "D",
            "etiquette_ges": "D",
            "date_etablissement": "2026-03-16"
          },
          "documents_ex_aequo": [],
          "motif": "document_valide"
        }
      },
      "ventes": {
        "nombre": {
          "field_id": "adresse.dvf.ventes_parcelle.nombre",
          "etat": "value",
          "motif": null,
          "valeur": 3,
          "unite": "count",
          "periode": "2021-01-02 → 2025-12-31",
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
          "etat": "value",
          "motif": null,
          "valeur": "2025-06-09",
          "unite": "none",
          "periode": "2021-01-02 → 2025-12-31",
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
        "valeur": "Exposition faible",
        "unite": "none",
        "periode": "2026-10-13",
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
          "valeur": 4102,
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
            "valeur": 4102,
            "unite": "euro_per_m2",
            "territoire": {
              "code_insee": "44109",
              "nom": "Nantes"
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
          "valeur": 3385,
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
            "valeur": 3385,
            "unite": "euro_per_m2",
            "territoire": {
              "code_insee": "44109",
              "nom": "Nantes"
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
        "etat": "value",
        "motif": null,
        "groupes": [
          {
            "cle": "lieu",
            "titre": null,
            "champs": [
              {
                "field_id": "adresse.batiments.nombre_parcelle",
                "etat": "value",
                "motif": null,
                "valeur": 1,
                "unite": "count",
                "periode": "2026-09-25",
                "source": {
                  "cle": "batiments",
                  "nom": "BD TOPO – Bâtiments",
                  "producteur": "IGN",
                  "url": "https://cartes.gouv.fr/rechercher-une-donnee/dataset/IGNF_BD-TOPO"
                },
                "rattachement": "parcelle_du_point",
                "reference": null,
                "elements": [],
                "liste_incomplete": false,
                "rayon_m": null
              },
              {
                "field_id": "adresse.batiments.nombre_parcelle_hors_annexe",
                "etat": "value",
                "motif": null,
                "valeur": 1,
                "unite": "count",
                "periode": "2026-09-25",
                "source": {
                  "cle": "batiments",
                  "nom": "BD TOPO – Bâtiments",
                  "producteur": "IGN",
                  "url": "https://cartes.gouv.fr/rechercher-une-donnee/dataset/IGNF_BD-TOPO"
                },
                "rattachement": "parcelle_du_point",
                "reference": null,
                "elements": [],
                "liste_incomplete": false,
                "rayon_m": null
              },
              {
                "field_id": "adresse.batiment.hauteur",
                "etat": "value",
                "motif": null,
                "valeur": 16.3,
                "unite": "metre",
                "periode": "2026-09-25",
                "source": {
                  "cle": "batiments",
                  "nom": "BD TOPO – Bâtiments",
                  "producteur": "IGN",
                  "url": "https://cartes.gouv.fr/rechercher-une-donnee/dataset/IGNF_BD-TOPO"
                },
                "rattachement": "batiment_du_point",
                "reference": null,
                "elements": [],
                "liste_incomplete": false,
                "rayon_m": null
              },
              {
                "field_id": "adresse.batiment.etages",
                "etat": "value",
                "motif": null,
                "valeur": 5,
                "unite": "count",
                "periode": "2026-09-25",
                "source": {
                  "cle": "batiments",
                  "nom": "BD TOPO – Bâtiments",
                  "producteur": "IGN",
                  "url": "https://cartes.gouv.fr/rechercher-une-donnee/dataset/IGNF_BD-TOPO"
                },
                "rattachement": "batiment_du_point",
                "reference": null,
                "elements": [],
                "liste_incomplete": false,
                "rayon_m": null
              },
              {
                "field_id": "adresse.batiment.rnb_id",
                "etat": "value",
                "motif": null,
                "valeur": "2SNZFXM29DZQ",
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
              },
              {
                "field_id": "adresse.parcelle.identifiant",
                "etat": "value",
                "motif": null,
                "valeur": "44109000ZZ0062",
                "unite": "none",
                "periode": "2026-09-26",
                "source": {
                  "cle": "cadastre",
                  "nom": "Cadastre Etalab",
                  "producteur": "DGFiP / Etalab",
                  "url": "https://cadastre.data.gouv.fr"
                },
                "rattachement": "parcelle_du_point",
                "reference": null,
                "elements": [],
                "liste_incomplete": false,
                "rayon_m": null
              },
              {
                "field_id": "adresse.parcelle.contenance",
                "etat": "value",
                "motif": null,
                "valeur": 201,
                "unite": "m2",
                "periode": "2026-09-26",
                "source": {
                  "cle": "cadastre",
                  "nom": "Cadastre Etalab",
                  "producteur": "DGFiP / Etalab",
                  "url": "https://cadastre.data.gouv.fr"
                },
                "rattachement": "parcelle_du_point",
                "reference": null,
                "elements": [],
                "liste_incomplete": false,
                "rayon_m": null
              },
              {
                "field_id": "adresse.copropriete.immatriculation",
                "etat": "value",
                "motif": null,
                "valeur": 0,
                "unite": "count",
                "periode": "2026-09-25",
                "source": {
                  "cle": "copropriete",
                  "nom": "Registre national des copropriétés (RNIC)",
                  "producteur": "ANAH",
                  "url": "https://www.data.gouv.fr/datasets/registre-national-dimmatriculation-des-coproprietes"
                },
                "rattachement": "parcelle_du_point",
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
                "valeur": "Exposition faible",
                "unite": "none",
                "periode": "2026-10-13",
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
                "valeur": 22,
                "unite": "count",
                "periode": "2026-10-13",
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
                    "libelle": "Affaissements et effondrements d'origine anthropique (anciennes carrières souterraines, hors mines)",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "Affaissements et effondrements d'origine naturelle (cavités souterraines)",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "Avancée dunaire",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "Eboulement ou chutes de pierres et de blocs",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "Engins de guerre",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "Foudre",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "Glissement de terrain",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "Grêle",
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
                    "libelle": "Par remontées de nappes naturelles",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "Par ruissellement et coulée de boue",
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
                    "libelle": "Risque industriel",
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
                    "libelle": "Tempête et grains (vent)",
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
                "periode": "2026-10-13",
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
                "valeur": 157,
                "unite": "count",
                "periode": "2026-10-13",
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
                "periode": "2026-10-13",
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
                    "libelle": "UMa · Secteur de développement des centralités actuelles ou en devenir",
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
                "valeur": 9,
                "unite": "count",
                "periode": "2026-10-13",
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
                    "libelle": "norme plafond applicable à la sous-destination Bureau pour les véhicules à moteur",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "Orientation d'Aménagement et de Programmation  Loire (OAP)",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "polarité commerciale de proximité et centralité commerciale Nantaise",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "Orientation dAménagement et de Programmation Commerce (OAP)",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "secteur de renforcement de la mixité sociale",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "norme plancher applicable à la sous-destination Logement collectif pour les véhicules à moteur",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "hauteur par niveaux, hors tout",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "norme de stationnement applicable à la sous-destination Artisanat et commerce de détail",
                    "distance_m": null,
                    "nature": null
                  },
                  {
                    "libelle": "périmètre indicatif de 500 m autour des stations de transport collectif structurant",
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
                    "libelle": "Jean Jaurès",
                    "distance_m": 130,
                    "nature": "arret"
                  },
                  {
                    "libelle": "Jean Jaurès",
                    "distance_m": 139,
                    "nature": "arret"
                  },
                  {
                    "libelle": "Jean Jaurès",
                    "distance_m": 169,
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

export const ADRESSE_44109_zz04_00004_DPE_OBSERVED = {
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
    "total": 5,
    "page": 1,
    "pages": 1,
    "par_page": 10,
    "generation": "2e71bc44da41d218",
    "distribution": {
      "A": 0,
      "B": 0,
      "C": 1,
      "D": 2,
      "E": 1,
      "F": 1,
      "G": 0,
      "unknown": 0
    },
    "date_import": "2026-10-08",
    "documents": [
      {
        "numero": "2644E8000029X",
        "etiquette_dpe": "D",
        "etiquette_ges": "D",
        "type_batiment": "appartement",
        "surface_habitable": 118.9,
        "date_etablissement": "2026-03-16",
        "date_fin_validite": "2036-03-15",
        "badges": [
          "plus_recent"
        ],
        "adresse_declaree": null
      },
      {
        "numero": "2644E8000030X",
        "etiquette_dpe": "E",
        "etiquette_ges": "C",
        "type_batiment": "immeuble",
        "surface_habitable": null,
        "date_etablissement": "2026-02-27",
        "date_fin_validite": "2036-02-26",
        "badges": [],
        "adresse_declaree": null
      },
      {
        "numero": "2544E8000031X",
        "etiquette_dpe": "C",
        "etiquette_ges": "A",
        "type_batiment": "appartement",
        "surface_habitable": 36.9,
        "date_etablissement": "2025-02-10",
        "date_fin_validite": "2035-02-09",
        "badges": [],
        "adresse_declaree": {
          "etat": "masquee",
          "texte": null,
          "motif": "complement_present"
        }
      },
      {
        "numero": "2444E8000032X",
        "etiquette_dpe": "F",
        "etiquette_ges": "C",
        "type_batiment": "appartement",
        "surface_habitable": 31.8,
        "date_etablissement": "2024-03-14",
        "date_fin_validite": "2034-03-13",
        "badges": [
          "remplacement_incertain"
        ],
        "adresse_declaree": null
      },
      {
        "numero": "2244E8000033X",
        "etiquette_dpe": "D",
        "etiquette_ges": "D",
        "type_batiment": "appartement",
        "surface_habitable": 118.4,
        "date_etablissement": "2022-06-16",
        "date_fin_validite": "2032-06-15",
        "badges": [],
        "adresse_declaree": null
      }
    ],
    "selection": {
      "etat": "unique",
      "date": "2026-03-16",
      "document": {
        "numero": "2644E8000029X",
        "etiquette_dpe": "D",
        "etiquette_ges": "D",
        "date_etablissement": "2026-03-16"
      },
      "documents_ex_aequo": [],
      "motif": "document_valide"
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
