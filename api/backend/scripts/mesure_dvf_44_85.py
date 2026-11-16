#!/usr/bin/env python3
"""Replayable measurement of the mutation-grain chain (D-22/D-23/D-24/D-30, DVF-07).

Phase 57 blocks any coverage rule (D-23) and any human verdict (D-24) behind a
measured instrument: nobody may invent a threshold before this script has
produced real numbers. The D-24 checkpoint reads this report's palier table to
CHOOSE the "n.d." threshold -- the code below never proposes one. Plan 57-30
replays this exact script, unmodified, against the production database in
read-only mode: every counter below is therefore computable from
``mutations``/``mutations_grain``/``mutation_parcelles`` alone, through the
SELECT-only guard already proven by ``scripts/verify_golden_cases.py``
(T-57-14).

Run from ``api/backend``::

    uv run python -m scripts.mesure_dvf_44_85 --sortie rapport.md --json rapport.json

The script has no write capability whatsoever, even when pointed at
production (D-30): the connection is opened through
``_open_read_only_connection`` (which asserts
``transaction_read_only = on`` before any query runs) and every SQL statement
is checked against a fixed allowlist by ``assert_select_only`` before
execution -- both mechanisms are imported from ``verify_golden_cases``, never
re-implemented (T-57-14). No query selects an address, a parcel id or a
mutation id: the report publishes AGGREGATES only (volumes, quantiles, counts
by palier) -- T-57-15.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.etl_dvf.qualification import CLASSES_MUTATION
from scripts.verify_golden_cases import (
    GoldenSecurityError,
    _open_read_only_connection,
    assert_select_only,
)

DEPARTEMENTS_DEFAUT: tuple[str, ...] = ("44", "85")

# D-23: la table des paliers permet a Ianis de CHOISIR le seuil "n.d." -- ces
# six valeurs ne sont PAS une proposition de seuil, seulement les colonnes du
# tableau que le rapport affiche.
PALIERS_EFFECTIF: tuple[int, ...] = (1, 3, 5, 10, 20, 30)

CLASSES_RESIDENTIELLES: tuple[str, ...] = (
    "residentiel_maison",
    "residentiel_appartement",
)

# D-05/D-10..D-13: ces quatre classes ne sont jamais attribuables a un KPI
# logement isole -- bloc 5 du rapport.
CLASSES_NON_ATTRIBUABLES: tuple[str, ...] = (
    "mixte",
    "commerciale",
    "ensemble_residentiel",
    "terrain_seul",
)

# --- Bloc 1 : volumes par classe x departement x millesime -------------------
VOLUMES_PAR_CLASSE_SQL = """
SELECT code_departement,
       EXTRACT(YEAR FROM date_mutation)::int AS millesime,
       classe_mutation,
       COUNT(*) AS effectif
FROM mutations_grain
WHERE code_departement = ANY(%(departements)s)
GROUP BY code_departement, millesime, classe_mutation
ORDER BY code_departement, millesime, classe_mutation
"""

# --- Bloc 2 : population des statistiques (D-07 : vente, hors VEFA) ---------
VOLUMES_STATS_SQL = """
SELECT code_departement,
       classe_mutation,
       COUNT(*) AS effectif
FROM mutations_grain
WHERE code_departement = ANY(%(departements)s)
  AND est_vente = true
  AND hors_stats_vefa = false
  AND classe_mutation = ANY(%(classes_residentielles)s)
GROUP BY code_departement, classe_mutation
ORDER BY code_departement, classe_mutation
"""

# --- Bloc 3 : distribution du prix au m2 (mono-local residentiel) -----------
# prix_m2 n'est jamais stocke sur mutations_grain (D-15 : calcule a la volee,
# jamais persiste comme une verite figee) -- ce SELECT reproduit exactement
# l'eligibilite de app.etl_dvf.qualification._prix_m2 : mono_local, classe
# residentielle, vendable (vente OU vefa), surface et valeur positives.
DISTRIBUTION_PRIX_M2_SQL = """
SELECT code_departement,
       EXTRACT(YEAR FROM date_mutation)::int AS millesime,
       classe_mutation,
       COUNT(*) AS effectif,
       PERCENTILE_CONT(0.25) WITHIN GROUP (
           ORDER BY (valeur_fonciere / surface_bati_totale)
       ) AS q1,
       PERCENTILE_CONT(0.5) WITHIN GROUP (
           ORDER BY (valeur_fonciere / surface_bati_totale)
       ) AS mediane,
       PERCENTILE_CONT(0.75) WITHIN GROUP (
           ORDER BY (valeur_fonciere / surface_bati_totale)
       ) AS q3
FROM mutations_grain
WHERE code_departement = ANY(%(departements)s)
  AND classe_mutation = ANY(%(classes_residentielles)s)
  AND mono_local = true
  AND (est_vente = true OR hors_stats_vefa = true)
  AND surface_bati_totale > 0
  AND valeur_fonciere > 0
GROUP BY code_departement, millesime, classe_mutation
ORDER BY code_departement, millesime, classe_mutation
"""

# --- Bloc 4 : effectif communal PAR TYPE sur 12 mois (paliers D-23) ---------
# La borne "12 mois glissants" est ancree sur le MAX(date_mutation) mesure --
# jamais sur la date d'execution du script -- pour que le meme contenu de
# base produise toujours le meme rapport (determinisme, T-57-17), y compris
# lors du rejeu D-30 sur production a une date differente.
#
# Important (addendum de coherence 2026-08-25): cette requete doit reproduire
# EXACTEMENT la population publiee par v_commune_dvf. La premiere mesure
# regroupait maison + appartement et comptait aussi des mutations hors du
# perimetre statistique; elle ne pouvait donc pas justifier les seuils des
# medianes par type. Les mutations multi-communes sont exclues des statistiques
# communales: min(code_commune) reste une cle technique deterministe, jamais
# une preuve qu'une mutation appartient semantiquement a cette commune.
EFFECTIF_COMMUNAL_12M_SQL = """
WITH borne AS (
    SELECT MAX(date_mutation) AS date_max
    FROM mutations_grain
    WHERE code_departement = ANY(%(departements)s)
), classes AS (
    SELECT UNNEST(%(classes_residentielles)s::text[]) AS classe_mutation
), communes AS (
    SELECT departement_code AS code_departement,
           code AS code_commune
    FROM geo_communes
    WHERE departement_code = ANY(%(departements)s)
), effectifs AS (
    SELECT g.code_departement,
           g.code_commune,
           g.classe_mutation,
           COUNT(*) AS effectif
    FROM mutations_grain g, borne
    WHERE g.code_departement = ANY(%(departements)s)
      AND g.classe_mutation = ANY(%(classes_residentielles)s)
      AND g.est_vente = true
      AND g.hors_stats_vefa = false
      AND g.surface_bati_totale > 0
      AND g.valeur_fonciere > 0
      AND g.nb_communes = 1
      AND g.code_commune IS NOT NULL
      AND g.date_mutation >= borne.date_max - INTERVAL '12 months'
      AND g.date_mutation <= borne.date_max
    GROUP BY g.code_departement, g.code_commune, g.classe_mutation
)
SELECT c.code_departement,
       c.code_commune,
       k.classe_mutation,
       COALESCE(e.effectif, 0) AS effectif
FROM communes c
CROSS JOIN classes k
LEFT JOIN effectifs e
  ON e.code_departement = c.code_departement
 AND e.code_commune = c.code_commune
 AND e.classe_mutation = k.classe_mutation
ORDER BY c.code_departement, k.classe_mutation, c.code_commune
"""

# --- Bloc 5 : taux de mutations non attribuables ----------------------------
TAUX_NON_ATTRIBUABLE_SQL = """
SELECT code_departement,
       COUNT(*) FILTER (
           WHERE classe_mutation = ANY(%(classes_non_attribuables)s)
       ) AS non_attribuable,
       COUNT(*) AS total
FROM mutations_grain
WHERE code_departement = ANY(%(departements)s)
GROUP BY code_departement
ORDER BY code_departement
"""

# --- Bloc 6 : VEFA sans surface / avec Carrez (mesure, jamais substitue) ---
# RESEARCH.md Open Question 1 : mesurer le volume Carrez des VEFA sans
# surface, ne RIEN substituer. lotN_surface_carrez vit sur `mutations`
# (grain ligne), jamais sur `mutations_grain` -- jointure obligatoire.
VEFA_TOTAUX_SQL = """
SELECT code_departement,
       COUNT(*) AS vefa_total,
       COUNT(*) FILTER (
           WHERE surface_bati_totale IS NULL OR surface_bati_totale = 0
       ) AS vefa_sans_surface
FROM mutations_grain
WHERE code_departement = ANY(%(departements)s)
  AND hors_stats_vefa = true
GROUP BY code_departement
ORDER BY code_departement
"""

VEFA_SANS_SURFACE_AVEC_CARREZ_SQL = """
SELECT g.code_departement,
       COUNT(DISTINCT g.id_mutation) AS vefa_sans_surface_avec_carrez
FROM mutations_grain g
JOIN mutations m
  ON m.id_mutation = g.id_mutation
 AND m.code_departement = g.code_departement
WHERE g.code_departement = ANY(%(departements)s)
  AND g.hors_stats_vefa = true
  AND (g.surface_bati_totale IS NULL OR g.surface_bati_totale = 0)
  AND (
      m.lot1_surface_carrez IS NOT NULL
      OR m.lot2_surface_carrez IS NOT NULL
      OR m.lot3_surface_carrez IS NOT NULL
      OR m.lot4_surface_carrez IS NOT NULL
      OR m.lot5_surface_carrez IS NOT NULL
  )
GROUP BY g.code_departement
ORDER BY g.code_departement
"""

# --- Bloc 7 : garde A2 -- valeur_fonciere divergente entre dispositions ----
GARDE_VALEUR_DIVERGENTE_SQL = """
SELECT code_departement,
       EXTRACT(YEAR FROM date_mutation)::int AS millesime,
       COUNT(*) FILTER (WHERE valeur_divergente = true) AS valeur_divergente,
       COUNT(*) FILTER (WHERE nb_dispositions > 1) AS multi_disposition
FROM mutations_grain
WHERE code_departement = ANY(%(departements)s)
GROUP BY code_departement, millesime
ORDER BY code_departement, millesime
"""

# --- Bloc 8 : multi-parcelles et multi-communes (D-09) ----------------------
MULTI_PARCELLE_COMMUNE_SQL = """
SELECT code_departement,
       COUNT(*) FILTER (WHERE nb_parcelles > 1) AS multi_parcelles,
       COUNT(*) FILTER (WHERE nb_communes > 1) AS multi_communes,
       COUNT(*) AS total
FROM mutations_grain
WHERE code_departement = ANY(%(departements)s)
GROUP BY code_departement
ORDER BY code_departement
"""

# --- Bloc 9 : controle d'integrite mutations <-> mutations_grain/parcelles -
LIGNES_ORPHELINES_SQL = """
SELECT COUNT(*) AS lignes_orphelines
FROM mutations m
WHERE m.code_departement = ANY(%(departements)s)
  AND NOT EXISTS (
      SELECT 1 FROM mutations_grain g
      WHERE g.id_mutation = m.id_mutation
        AND g.code_departement = m.code_departement
  )
"""

MUTATIONS_SANS_PARCELLE_SQL = """
SELECT COUNT(*) AS mutations_sans_parcelle
FROM mutations_grain g
WHERE g.code_departement = ANY(%(departements)s)
  AND NOT EXISTS (
      SELECT 1 FROM mutation_parcelles p
      WHERE p.id_mutation = g.id_mutation
        AND p.code_departement = g.code_departement
  )
"""

ALLOWED_SQL: frozenset[str] = frozenset(
    {
        VOLUMES_PAR_CLASSE_SQL,
        VOLUMES_STATS_SQL,
        DISTRIBUTION_PRIX_M2_SQL,
        EFFECTIF_COMMUNAL_12M_SQL,
        TAUX_NON_ATTRIBUABLE_SQL,
        VEFA_TOTAUX_SQL,
        VEFA_SANS_SURFACE_AVEC_CARREZ_SQL,
        GARDE_VALEUR_DIVERGENTE_SQL,
        MULTI_PARCELLE_COMMUNE_SQL,
        LIGNES_ORPHELINES_SQL,
        MUTATIONS_SANS_PARCELLE_SQL,
    }
)


def _executer(
    conn: psycopg.Connection[Any], sql: str, params: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Run one allowlisted, read-only, bind-parameterized query."""
    assert_select_only(sql, ALLOWED_SQL)
    return list(conn.execute(sql, params).fetchall())


def _fusionner_vefa(
    totaux: Sequence[Mapping[str, Any]], carrez: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    carrez_par_dept = {
        row["code_departement"]: row["vefa_sans_surface_avec_carrez"] for row in carrez
    }
    return [
        {
            "code_departement": row["code_departement"],
            "vefa_total": row["vefa_total"],
            "vefa_sans_surface": row["vefa_sans_surface"],
            "vefa_sans_surface_avec_carrez": carrez_par_dept.get(
                row["code_departement"], 0
            ),
        }
        for row in totaux
    ]


def _formate_valeur(valeur: Any) -> str:
    if valeur is None:
        return "n.d."
    if isinstance(valeur, Decimal):
        return f"{valeur:.2f}"
    return str(valeur)


def _table_markdown(en_tetes: Sequence[str], lignes: Sequence[Sequence[Any]]) -> str:
    entete = "| " + " | ".join(en_tetes) + " |"
    separateur = "| " + " | ".join("---" for _ in en_tetes) + " |"
    if not lignes:
        return "\n".join([entete, separateur, "| _(aucune donnee)_ |"])
    corps = [
        "| " + " | ".join(_formate_valeur(v) for v in ligne) + " |" for ligne in lignes
    ]
    return "\n".join([entete, separateur, *corps])


def _bloc1_lignes(volumes: Sequence[Mapping[str, Any]]) -> list[tuple[Any, ...]]:
    """One row per (departement, millesime, classe), classes a effectif nul inclus."""
    effectifs: dict[tuple[str, int, str], int] = {}
    combos: set[tuple[str, int]] = set()
    for row in volumes:
        combo = (row["code_departement"], row["millesime"])
        combos.add(combo)
        effectifs[(*combo, row["classe_mutation"])] = row["effectif"]
    lignes: list[tuple[Any, ...]] = []
    for departement, millesime in sorted(combos):
        total = 0
        for classe in CLASSES_MUTATION:
            effectif = effectifs.get((departement, millesime, classe), 0)
            total += effectif
            lignes.append((departement, millesime, classe, effectif))
        lignes.append((departement, millesime, "TOTAL", total))
    return lignes


def _bloc2_lignes(
    rows: Sequence[Mapping[str, Any]], departements: Sequence[str]
) -> list[tuple[Any, ...]]:
    effectifs = {
        (row["code_departement"], row["classe_mutation"]): row["effectif"]
        for row in rows
    }
    lignes: list[tuple[Any, ...]] = []
    for departement in sorted(departements):
        for classe in CLASSES_RESIDENTIELLES:
            lignes.append(  # noqa: PERF401
                (departement, classe, effectifs.get((departement, classe), 0))
            )
    return lignes


def _bloc3_lignes(rows: Sequence[Mapping[str, Any]]) -> list[tuple[Any, ...]]:
    return sorted(
        (
            (
                row["code_departement"],
                row["millesime"],
                row["classe_mutation"],
                row["effectif"],
                row["q1"],
                row["mediane"],
                row["q3"],
            )
            for row in rows
        ),
        key=lambda ligne: (ligne[0], ligne[1], ligne[2]),
    )


def _bloc4_lignes(
    rows: Sequence[Mapping[str, Any]], departements: Sequence[str]
) -> list[tuple[Any, ...]]:
    """Coverage cost per department and residential class.

    The denominator is every reference commune crossed with every residential
    class. Zero-observation communes are part of the publication cost: their
    median is unavailable just like a median hidden below the chosen floor.
    """
    par_groupe: dict[tuple[str, str], list[int]] = {
        (dep, classe): [] for dep in departements for classe in CLASSES_RESIDENTIELLES
    }
    for row in rows:
        cle = (row["code_departement"], row["classe_mutation"])
        par_groupe.setdefault(cle, []).append(row["effectif"])
    lignes: list[tuple[Any, ...]] = []
    for (departement, classe), effectifs in sorted(par_groupe.items()):
        comptes = tuple(
            sum(1 for effectif in effectifs if effectif < palier)
            for palier in PALIERS_EFFECTIF
        )
        lignes.append((departement, classe, len(effectifs), *comptes))
    return lignes


def _bloc5_lignes(
    rows: Sequence[Mapping[str, Any]], departements: Sequence[str]
) -> list[tuple[Any, ...]]:
    par_departement = {row["code_departement"]: row for row in rows}
    lignes: list[tuple[Any, ...]] = []
    for departement in sorted(set(departements) | set(par_departement)):
        row = par_departement.get(departement, {"non_attribuable": 0, "total": 0})
        total = row["total"]
        non_attribuable = row["non_attribuable"]
        taux = f"{(non_attribuable / total * 100):.1f}%" if total else "n.d."
        lignes.append((departement, non_attribuable, total, taux))
    return lignes


def _bloc6_lignes(
    vefa_rows: Sequence[Mapping[str, Any]], departements: Sequence[str]
) -> list[tuple[Any, ...]]:
    par_departement = {row["code_departement"]: row for row in vefa_rows}
    valeurs_nulles = {
        "vefa_total": 0,
        "vefa_sans_surface": 0,
        "vefa_sans_surface_avec_carrez": 0,
    }
    lignes: list[tuple[Any, ...]] = []
    for departement in sorted(set(departements) | set(par_departement)):
        row = par_departement.get(departement, valeurs_nulles)
        lignes.append(
            (
                departement,
                row["vefa_total"],
                row["vefa_sans_surface"],
                row["vefa_sans_surface_avec_carrez"],
            )
        )
    return lignes


def _bloc7_lignes(rows: Sequence[Mapping[str, Any]]) -> list[tuple[Any, ...]]:
    return sorted(
        (
            row["code_departement"],
            row["millesime"],
            row["valeur_divergente"],
            row["multi_disposition"],
        )
        for row in rows
    )


def _bloc8_lignes(
    rows: Sequence[Mapping[str, Any]], departements: Sequence[str]
) -> list[tuple[Any, ...]]:
    par_departement = {row["code_departement"]: row for row in rows}
    valeurs_nulles = {"multi_parcelles": 0, "multi_communes": 0, "total": 0}
    lignes: list[tuple[Any, ...]] = []
    for departement in sorted(set(departements) | set(par_departement)):
        row = par_departement.get(departement, valeurs_nulles)
        lignes.append(
            (departement, row["multi_parcelles"], row["multi_communes"], row["total"])
        )
    return lignes


def _rendre_markdown(donnees: Mapping[str, Any]) -> str:
    departements = donnees["departements"]
    lignes_bloc1 = _bloc1_lignes(donnees["volumes_par_classe"])
    lignes_bloc2 = _bloc2_lignes(donnees["volumes_population_stats"], departements)
    lignes_bloc3 = _bloc3_lignes(donnees["distribution_prix_m2"])
    lignes_bloc4 = _bloc4_lignes(donnees["effectifs_communaux_12m"], departements)
    lignes_bloc5 = _bloc5_lignes(donnees["taux_non_attribuable"], departements)
    lignes_bloc6 = _bloc6_lignes(donnees["vefa"], departements)
    lignes_bloc7 = _bloc7_lignes(donnees["garde_valeur_divergente"])
    lignes_bloc8 = _bloc8_lignes(donnees["multi_parcelle_commune"], departements)
    integrite = donnees["controle_integrite"]

    parties = [
        f"# Mesure DVF au grain mutation -- {donnees['etiquette']}",
        "",
        f"Genere le {donnees['genere_le']}. Departements mesures : "
        f"{', '.join(departements)}.",
        "",
        "Rapport produit par `scripts/mesure_dvf_44_85.py` (D-22/D-23/D-24/D-30, "
        "DVF-07) -- lecture seule, chiffres agreges uniquement.",
        "",
        "## 1. Volumes par classe x departement x millesime",
        _table_markdown(
            ["departement", "millesime", "classe_mutation", "effectif"], lignes_bloc1
        ),
        "",
        "## 2. Population des statistiques (vente, hors VEFA) par classe",
        _table_markdown(["departement", "classe_mutation", "effectif"], lignes_bloc2),
        "",
        "## 3. Distribution du prix au m2 (mono-local residentiel)",
        _table_markdown(
            [
                "departement",
                "millesime",
                "classe_mutation",
                "effectif",
                "q1",
                "mediane",
                "q3",
            ],
            lignes_bloc3,
        ),
        "",
        "## 4. Communes sous chaque palier par type (12 mois glissants)",
        _table_markdown(
            [
                "departement",
                "classe_mutation",
                "communes_reference",
                *[f"< {palier}" for palier in PALIERS_EFFECTIF],
            ],
            lignes_bloc4,
        ),
        "",
        "## 5. Taux de mutations non attribuables (mixte/commerciale/ensemble/terrain)",
        _table_markdown(
            ["departement", "non_attribuable", "total", "taux"], lignes_bloc5
        ),
        "",
        "## 6. VEFA -- surface absente et repli Carrez mesure (jamais substitue)",
        _table_markdown(
            [
                "departement",
                "vefa_total",
                "vefa_sans_surface",
                "vefa_sans_surface_avec_carrez",
            ],
            lignes_bloc6,
        ),
        "",
        "## 7. Garde A2 -- valeur_fonciere divergente entre dispositions",
        _table_markdown(
            ["departement", "millesime", "valeur_divergente", "multi_disposition"],
            lignes_bloc7,
        ),
        "",
        "## 8. Multi-parcelles et multi-communes (D-09)",
        _table_markdown(
            ["departement", "multi_parcelles", "multi_communes", "total"],
            lignes_bloc8,
        ),
        "",
        "## 9. Controle d'integrite mutations <-> mutations_grain/mutation_parcelles",
        f"- lignes de `mutations` non rattachees a `mutations_grain` par "
        f"`id_mutation` : {integrite['lignes_orphelines']}",
        f"- mutations de `mutations_grain` sans ligne dans `mutation_parcelles` : "
        f"{integrite['mutations_sans_parcelle']}",
        "",
    ]
    return "\n".join(parties)


class _EncodeurDecimal(json.JSONEncoder):
    """Serialize the report's Decimal aggregates (quantiles) as JSON numbers."""

    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


def _construire_rapport(
    conn: psycopg.Connection[Any],
    departements: Sequence[str],
    etiquette: str,
    genere_le: date,
) -> tuple[str, dict[str, Any]]:
    """Run every measured block and render both output formats.

    Deliberately NOT split from the SQL execution: this function both fetches
    and shapes the report data in one pass. Task 2 of this plan refactors the
    RENDERING half into a pure function once a test demands one (RED before
    GREEN) -- this function stays the single entry point either way.
    """
    params_base = {
        "departements": list(departements),
        "classes_residentielles": list(CLASSES_RESIDENTIELLES),
        "classes_non_attribuables": list(CLASSES_NON_ATTRIBUABLES),
    }

    volumes = _executer(conn, VOLUMES_PAR_CLASSE_SQL, params_base)
    volumes_stats = _executer(conn, VOLUMES_STATS_SQL, params_base)
    distribution_prix = _executer(conn, DISTRIBUTION_PRIX_M2_SQL, params_base)
    effectifs_communaux = _executer(conn, EFFECTIF_COMMUNAL_12M_SQL, params_base)
    taux_non_attribuable = _executer(conn, TAUX_NON_ATTRIBUABLE_SQL, params_base)
    vefa_totaux = _executer(conn, VEFA_TOTAUX_SQL, params_base)
    vefa_carrez = _executer(conn, VEFA_SANS_SURFACE_AVEC_CARREZ_SQL, params_base)
    garde_valeur = _executer(conn, GARDE_VALEUR_DIVERGENTE_SQL, params_base)
    multi = _executer(conn, MULTI_PARCELLE_COMMUNE_SQL, params_base)
    lignes_orphelines = _executer(conn, LIGNES_ORPHELINES_SQL, params_base)
    mutations_sans_parcelle = _executer(conn, MUTATIONS_SANS_PARCELLE_SQL, params_base)

    donnees: dict[str, Any] = {
        "etiquette": etiquette,
        "genere_le": genere_le.isoformat(),
        "departements": list(departements),
        "paliers_effectif": list(PALIERS_EFFECTIF),
        "volumes_par_classe": volumes,
        "volumes_population_stats": volumes_stats,
        "distribution_prix_m2": distribution_prix,
        "effectifs_communaux_12m": effectifs_communaux,
        "taux_non_attribuable": taux_non_attribuable,
        "vefa": _fusionner_vefa(vefa_totaux, vefa_carrez),
        "garde_valeur_divergente": garde_valeur,
        "multi_parcelle_commune": multi,
        "controle_integrite": {
            "lignes_orphelines": (
                lignes_orphelines[0]["lignes_orphelines"] if lignes_orphelines else 0
            ),
            "mutations_sans_parcelle": (
                mutations_sans_parcelle[0]["mutations_sans_parcelle"]
                if mutations_sans_parcelle
                else 0
            ),
        },
    }

    markdown = _rendre_markdown(donnees)
    return markdown, donnees


def _liste_departements(valeur: str) -> list[str]:
    departements = [item.strip() for item in valeur.split(",") if item.strip()]
    if not departements:
        raise argparse.ArgumentTypeError("au moins un departement est requis")
    return departements


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn-env", default="DATABASE_URL")
    parser.add_argument(
        "--departements",
        type=_liste_departements,
        default=list(DEPARTEMENTS_DEFAUT),
        help="Liste separee par des virgules (defaut: 44,85)",
    )
    parser.add_argument("--sortie", help="Chemin du rapport Markdown (defaut: stdout)")
    parser.add_argument(
        "--json", dest="chemin_json", help="Chemin optionnel du rapport en JSON"
    )
    parser.add_argument(
        "--etiquette",
        default="local",
        help="Libelle libre de l'environnement mesure (local, production-readonly...)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    raw_dsn = os.getenv(args.dsn_env)
    if not raw_dsn:
        raise GoldenSecurityError(
            f"variable d'environnement DSN manquante: {args.dsn_env}"
        )
    dsn = raw_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = _open_read_only_connection(dsn)
    try:
        markdown, donnees = _construire_rapport(
            conn, args.departements, args.etiquette, datetime.now(UTC).date()
        )
    finally:
        conn.rollback()
        conn.close()

    if args.sortie:
        Path(args.sortie).write_text(markdown, encoding="utf-8")
    else:
        print(markdown)

    if args.chemin_json:
        Path(args.chemin_json).write_text(
            json.dumps(donnees, cls=_EncodeurDecimal, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (GoldenSecurityError, psycopg.Error, OSError, ValueError):
        print("mesure DVF bloquee", file=sys.stderr)
        raise SystemExit(1) from None
