---
phase: DC-51-attribution-dpe-exacte
plan: 09
subsystem: golden-cases
tags: [golden-cases, dpe, live-qualification, production-readonly, privacy, amendment]

requires:
  - phase: DC-51-attribution-dpe-exacte
    provides: "qualification redacted, preview loopback et preuve served_checkout_sha du Plan 51-07"
  - phase: DC-48-multi-logements-honn-te
    provides: "pins et observations historiques du corpus golden"
provides:
  - "Qualification live approuvée des 11 cas amendés sur le SHA c6917790"
  - "Couverture des quatre archétypes DPE sur les départements 44 et 85"
  - "manifest_patch redacted complet, entrée exclusive du Plan 51-10"
affects: [51-10, 51-11, golden-cases]

key-files:
  created:
    - .planning/phases/DC-51-attribution-dpe-exacte/51-09-SUMMARY.md
  modified:
    - contracts/golden-cases/v1/schema.json
    - contracts/golden-cases/v1/manifest.json

key-decisions:
  - "Les commits 8c473c82 et c6917790 constituent une seule décision d'amendement, scindée pour préserver l'attribution du checkout réellement servi"
  - "La qualification live réussie dans le run 32454820399 fait autorité même si le job global échoue ensuite sur la fixture frontend"
  - "golden-address-011 reste non épinglé jusqu'au Plan 51-10, après la preuve live de son rôle de porteur"
  - "final_dpe_gate reste désarmé jusqu'au Plan 51-10"

completed: 2026-08-21
---

# Phase 51 Plan 09 — Qualification live et amendement du corpus golden

## Résultat

La **qualification live est verte à l'intérieur d'un run globalement rouge**. Le run GitHub
`32454820399` contient le verdict `live replay: OK (11 redacted cases)` et son rapport redacted
porte `ok=true`. L'échec du job intervient après cette qualification, au step Vitest
`src/lib/data/golden-cases.test.ts`, parce que la fixture frontend n'a pas encore été régénérée.
Cette fixture, son test de parité et le compte de pins backend sont les livrables déclarés du Plan
51-10 ; ils ne sont pas modifiés par cette clôture.

Il ne faut donc pas qualifier le run entier de succès. Le gate final du Plan 51-11 exigera un run
**entièrement vert**, avec `served_checkout_sha` égal au SHA final de la Phase 51. La régénération
de la fixture par le Plan 51-10 est sur son chemin critique.

## Runs et provenance

| Usage | Workflow / run | Checkout | SHA servi | Verdict |
|---|---|---|---|---|
| Calcul de l'amendement | Select Golden Live `32452721703` | `8c473c8230e3c1847794dd0e7f6c58bf716310bf` | `8c473c8230e3c1847794dd0e7f6c58bf716310bf` | succès |
| Qualification post-amendement | Verify Golden Live `32454820399` | `c691779005e0d1acf80ab185542dc6210488ca9d` | `c691779005e0d1acf80ab185542dc6210488ca9d` | qualification live réussie, job global en échec postérieur |

- Rapport consulté : artefact redacted `9436921686`.
- Le vérificateur a observé 11 cas redacted : 8 adresses et 3 communes.
- Le step protégé de vérification est sorti avec succès avant le step Vitest en échec.
- Aucun corpus privé, input, adresse, BAN, parcelle, coordonnée, payload brut ou secret n'est
  recopié dans ce SUMMARY.

## Amendement versionné : une décision, deux commits

Les deux commits suivants matérialisent une seule décision :

1. `8c473c8230e3c1847794dd0e7f6c58bf716310bf` —
   `feat(golden): pin golden-address-009 for the 85 carrier coverage`
2. `c691779005e0d1acf80ab185542dc6210488ca9d` —
   `feat(golden): amend the frozen manifest for the Phase 51 attribution policy`

Le premier commit épingle 009 dans le schéma en override explicite de
`48-GOLDEN-EVIDENCE 10`. Il est volontairement incomplet : CI Data Contracts devait rester rouge
sur `missing pinned case: golden-address-009` jusqu'à ce qu'un checkout réel et attribuable soit
observé par le runner protégé. Le second commit applique exactement le manifest calculé par le run
`32452721703`, sans transcription, et complète la décision. Cette scission préserve la sémantique
de `served_checkout_sha` : aucune mutation du checkout éphémère n'a été utilisée.

## Évolution réelle du manifest

Le sélecteur a ré-observé et re-daté les 11 cas au `2026-08-21`. La date seule ne permet donc plus
de distinguer la nature des changements ; la provenance fonctionnelle est la suivante.

### Changés par la politique Phase 51

| Cas | Avant | Après | Changement d'état |
|---|---|---|---|
| `golden-address-003` | `no_dvf` | `dataset_out_of_coverage` | `dpe_state: value -> not_reported` |
| `golden-address-005` | `complete` | `no_dpe` | `dpe_state: value -> not_reported` |
| `golden-address-007` | `complete` | `no_dpe` | `dpe_state: value -> not_reported` |

### Ajoutés

- `golden-address-009` — département 85, catégorie `no_dpe`.
- `golden-address-011` — département 44, catégorie `no_dvf`.

### Simplement re-vérifiés et re-datés

Aucun changement de fond pour `golden-address-001`, `golden-address-002`,
`golden-address-004`, `golden-commune-001`, `golden-commune-002` et
`golden-commune-003`.

## Couverture obtenue

La simulation du manifest amendé dans `app/golden/coverage_sim.py` consigne :

```json
{
  "remaining_missing": [],
  "missing_observations": [],
  "remaining_missing_observations": [],
  "coverage_possible": true
}
```

La qualification redacted apporte les porteurs suivants :

| Archétype | Porteurs qualifiés | Départements |
|---|---|---|
| `dpe_exact` | 001, 011 | 44 |
| `dpe_nearer_neighbor_negative` | 003, 005, 009 | 44, 85 |
| `dpe_absent` | 002, 003, 004, 005, 009 | 44, 85 |
| `dpe_multiple_or_building` | 007 | 44 |

Pour chaque porteur `dpe_nearer_neighbor_negative`, la qualification porte simultanément
`dpe_attribution_status=absent`, aucun diagnostic principal, des comptes candidat/bâtiment nuls
et `dpe_nearby_diagnostic_present=true`. Une absence simple n'est pas utilisée comme substitut.

## Norme d'amendement

> Les `reasons` d'un cas rouge peuvent motiver une hypothèse d'amendement, jamais en fournir la
> matière finale. Tout amendement manuel du manifest est confirmé par la qualification live verte
> du replay qui le suit ; ce sont les carriers produits par cette qualification, pas les
> `reasons`, qui gèlent.

Dans ce plan, le manifest de catégorie a été calculé par le sélecteur protégé, puis la matière DPE
finale ci-dessous a été produite par la qualification live du checkout amendé. Le statut rouge du
job après cette étape ne transforme pas des `reasons` en autorité et n'altère pas le rapport
redacted déjà produit.

## Handoff obligatoire vers 51-10

- Épingler `golden-address-011` **après** cette qualification, selon le précédent du Plan 48-17
  pour `golden-address-007`.
- Garder `golden-address-009` parmi les pins : il est le seul cas ADRESSE en département 85 et le
  seul porteur possible de `required_carrier_departements` côté 85.
- Armer `final_dpe_gate` uniquement au Plan 51-10, après application exacte du
  `manifest_patch` et régénération des fixtures frontend.
- Ne lire ni artefact live, ni corpus privé, ni adresse pendant le Plan 51-10 : le bloc JSON suivant
  est son entrée exclusive.

## manifest_patch réel

Bloc copié structurellement depuis l'artefact redacted `9436921686` du run
`32454820399`. Il contient les huit porteurs adresse complets qualifiés ; il n'est ni reconstruit
ni déduit du manifest courant.

```json
[
  {
    "case_id": "golden-address-001",
    "type": "address",
    "departement": "44",
    "code_insee": "44001",
    "category": "complete",
    "frozen_at": "2026-08-21",
    "source_millesimes": {
      "recensement": "2022",
      "revenus": "2021",
      "fiscalite": "2025",
      "loyers": "2025"
    },
    "invariants": {
      "db": {
        "has_commune_row": true,
        "superficie_present": true,
        "parcelle_present": true,
        "copro_row_present": false,
        "dpe_appartements_multiples": false,
        "dvf_appartements_present": false,
        "dpe_rattachement_ok": true
      },
      "api": {
        "cadastre_state": "value",
        "dvf_state": "value",
        "urbanisme_state": "value",
        "risques_state": "value",
        "commune_state": "value",
        "dpe_state": "value"
      },
      "mapper": {
        "prix_m2_unit": "euro_per_m2"
      },
      "dpe": {
        "dpe_attribution_status": "exact",
        "dpe_primary_diagnostic_present": true,
        "dpe_truncated": false,
        "dpe_nearby_diagnostic_present": false,
        "dpe_candidate_count": 0,
        "dpe_building_count": 0
      }
    },
    "private_ref": "golden-address-001",
    "carrier_for": [
      "dpe_exact"
    ]
  },
  {
    "case_id": "golden-address-002",
    "type": "address",
    "departement": "44",
    "code_insee": "44001",
    "category": "no_dpe",
    "frozen_at": "2026-08-21",
    "source_millesimes": {
      "recensement": "2022",
      "revenus": "2021",
      "fiscalite": "2025",
      "loyers": "2025"
    },
    "invariants": {
      "db": {
        "has_commune_row": true,
        "superficie_present": true,
        "parcelle_present": true,
        "copro_row_present": false,
        "dpe_appartements_multiples": false,
        "dvf_appartements_present": false,
        "dpe_rattachement_ok": true
      },
      "api": {
        "cadastre_state": "value",
        "dvf_state": "value",
        "urbanisme_state": "value",
        "risques_state": "value",
        "commune_state": "value",
        "dpe_state": "not_reported"
      },
      "mapper": {
        "prix_m2_unit": "euro_per_m2"
      },
      "dpe": {
        "dpe_attribution_status": "absent",
        "dpe_primary_diagnostic_present": false,
        "dpe_truncated": false,
        "dpe_nearby_diagnostic_present": false,
        "dpe_candidate_count": 0,
        "dpe_building_count": 0
      }
    },
    "private_ref": "golden-address-002",
    "carrier_for": [
      "dpe_absent"
    ]
  },
  {
    "case_id": "golden-address-003",
    "type": "address",
    "departement": "44",
    "code_insee": "44001",
    "category": "dataset_out_of_coverage",
    "frozen_at": "2026-08-21",
    "source_millesimes": {
      "recensement": "2022",
      "revenus": "2021",
      "fiscalite": "2025",
      "loyers": "2025"
    },
    "invariants": {
      "db": {
        "has_commune_row": true,
        "superficie_present": true,
        "parcelle_present": true,
        "copro_row_present": false,
        "dpe_appartements_multiples": false,
        "dvf_appartements_present": false,
        "dpe_rattachement_ok": true
      },
      "api": {
        "cadastre_state": "value",
        "dvf_state": "not_reported",
        "urbanisme_state": "value",
        "risques_state": "value",
        "commune_state": "value",
        "dpe_state": "not_reported"
      },
      "mapper": {
        "prix_m2_unit": "euro_per_m2"
      },
      "dpe": {
        "dpe_attribution_status": "absent",
        "dpe_primary_diagnostic_present": false,
        "dpe_truncated": false,
        "dpe_nearby_diagnostic_present": true,
        "dpe_candidate_count": 0,
        "dpe_building_count": 0
      }
    },
    "private_ref": "golden-address-003",
    "carrier_for": [
      "dpe_nearer_neighbor_negative",
      "dpe_absent"
    ]
  },
  {
    "case_id": "golden-address-004",
    "type": "address",
    "departement": "44",
    "code_insee": "44001",
    "category": "dataset_out_of_coverage",
    "frozen_at": "2026-08-21",
    "source_millesimes": {
      "recensement": "2022",
      "revenus": "2021",
      "fiscalite": "2025",
      "loyers": "2025"
    },
    "invariants": {
      "db": {
        "has_commune_row": true,
        "superficie_present": true,
        "parcelle_present": true,
        "copro_row_present": false,
        "dpe_appartements_multiples": false,
        "dvf_appartements_present": false,
        "dpe_rattachement_ok": true
      },
      "api": {
        "cadastre_state": "value",
        "dvf_state": "not_reported",
        "urbanisme_state": "value",
        "risques_state": "value",
        "commune_state": "value",
        "dpe_state": "not_reported"
      },
      "mapper": {
        "prix_m2_unit": "euro_per_m2"
      },
      "dpe": {
        "dpe_attribution_status": "absent",
        "dpe_primary_diagnostic_present": false,
        "dpe_truncated": false,
        "dpe_nearby_diagnostic_present": false,
        "dpe_candidate_count": 0,
        "dpe_building_count": 0
      }
    },
    "private_ref": "golden-address-004",
    "carrier_for": [
      "dpe_absent"
    ]
  },
  {
    "case_id": "golden-address-005",
    "type": "address",
    "departement": "44",
    "code_insee": "44109",
    "category": "no_dpe",
    "frozen_at": "2026-08-21",
    "source_millesimes": {
      "recensement": "2022",
      "revenus": "2021",
      "fiscalite": "2025",
      "loyers": "2025"
    },
    "invariants": {
      "db": {
        "has_commune_row": true,
        "superficie_present": true,
        "parcelle_present": true,
        "copro_row_present": true,
        "dpe_appartements_multiples": true,
        "dvf_appartements_present": true,
        "dpe_rattachement_ok": true
      },
      "api": {
        "cadastre_state": "value",
        "dvf_state": "value",
        "urbanisme_state": "value",
        "risques_state": "value",
        "commune_state": "value",
        "dpe_state": "not_reported"
      },
      "mapper": {
        "prix_m2_unit": "euro_per_m2"
      },
      "dpe": {
        "dpe_attribution_status": "absent",
        "dpe_primary_diagnostic_present": false,
        "dpe_truncated": false,
        "dpe_nearby_diagnostic_present": true,
        "dpe_candidate_count": 0,
        "dpe_building_count": 0
      }
    },
    "private_ref": "golden-address-005",
    "carrier_for": [
      "dpe_nearer_neighbor_negative",
      "dpe_absent"
    ]
  },
  {
    "case_id": "golden-address-007",
    "type": "address",
    "departement": "44",
    "code_insee": "44184",
    "category": "no_dpe",
    "frozen_at": "2026-08-21",
    "source_millesimes": {
      "recensement": "2022",
      "revenus": "2021",
      "fiscalite": "2025",
      "loyers": "2025"
    },
    "invariants": {
      "db": {
        "has_commune_row": true,
        "superficie_present": true,
        "parcelle_present": true,
        "copro_row_present": false,
        "dpe_appartements_multiples": true,
        "dvf_appartements_present": true,
        "dpe_rattachement_ok": true
      },
      "api": {
        "cadastre_state": "value",
        "dvf_state": "value",
        "urbanisme_state": "value",
        "risques_state": "value",
        "commune_state": "value",
        "dpe_state": "not_reported"
      },
      "mapper": {
        "prix_m2_unit": "euro_per_m2"
      },
      "dpe": {
        "dpe_attribution_status": "building",
        "dpe_primary_diagnostic_present": false,
        "dpe_truncated": false,
        "dpe_nearby_diagnostic_present": false,
        "dpe_candidate_count": 0,
        "dpe_building_count": 4
      }
    },
    "private_ref": "golden-address-007",
    "carrier_for": [
      "dpe_multiple_or_building"
    ]
  },
  {
    "case_id": "golden-address-009",
    "type": "address",
    "departement": "85",
    "code_insee": "85001",
    "category": "no_dpe",
    "frozen_at": "2026-08-21",
    "source_millesimes": {
      "recensement": "2022",
      "revenus": "2021",
      "fiscalite": "2025",
      "loyers": "2025"
    },
    "invariants": {
      "db": {
        "has_commune_row": true,
        "superficie_present": true,
        "parcelle_present": true,
        "copro_row_present": false,
        "dpe_appartements_multiples": false,
        "dvf_appartements_present": false,
        "dpe_rattachement_ok": true
      },
      "api": {
        "cadastre_state": "value",
        "dvf_state": "value",
        "urbanisme_state": "value",
        "risques_state": "value",
        "commune_state": "value",
        "dpe_state": "not_reported"
      },
      "mapper": {
        "prix_m2_unit": "euro_per_m2"
      },
      "dpe": {
        "dpe_attribution_status": "absent",
        "dpe_primary_diagnostic_present": false,
        "dpe_truncated": false,
        "dpe_nearby_diagnostic_present": true,
        "dpe_candidate_count": 0,
        "dpe_building_count": 0
      }
    },
    "private_ref": "golden-address-009",
    "carrier_for": [
      "dpe_nearer_neighbor_negative",
      "dpe_absent"
    ]
  },
  {
    "case_id": "golden-address-011",
    "type": "address",
    "departement": "44",
    "code_insee": "44001",
    "category": "no_dvf",
    "frozen_at": "2026-08-21",
    "source_millesimes": {
      "recensement": "2022",
      "revenus": "2021",
      "fiscalite": "2025",
      "loyers": "2025"
    },
    "invariants": {
      "db": {
        "has_commune_row": true,
        "superficie_present": true,
        "parcelle_present": true,
        "copro_row_present": false,
        "dpe_appartements_multiples": false,
        "dvf_appartements_present": false,
        "dpe_rattachement_ok": true
      },
      "api": {
        "cadastre_state": "value",
        "dvf_state": "not_reported",
        "urbanisme_state": "value",
        "risques_state": "value",
        "commune_state": "value",
        "dpe_state": "value"
      },
      "mapper": {
        "prix_m2_unit": "euro_per_m2"
      },
      "dpe": {
        "dpe_attribution_status": "exact",
        "dpe_primary_diagnostic_present": true,
        "dpe_truncated": false,
        "dpe_nearby_diagnostic_present": false,
        "dpe_candidate_count": 0,
        "dpe_building_count": 0
      }
    },
    "private_ref": "golden-address-011",
    "carrier_for": [
      "dpe_exact"
    ]
  }
]
```

