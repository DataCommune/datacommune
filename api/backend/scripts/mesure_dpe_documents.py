#!/usr/bin/env python3
"""Read-only measurement of what the work base lets us claim about a DPE
document (plan 04-01, D-108: the measurement precedes any displayed claim).

Blocks, per department and regime (existant / neuf):

- A ``dates``: documents in base, pre-2021-07-01 (old method), expired at the
  import date, replaced by another document;
- B ``consultables`` / ``ecartes_par_motif``: documents reachable from an
  address page (non-empty ``identifiant_ban`` present in ``adresses.ban_id``
  of the department, the production rule of ``dpe_inventory_page``), those
  reachable by number only, and the others by closed motive;
- C ``documents_par_cle``: histogram of documents per address key;
- D ``maisons_d110``: the three D-110 criteria (house, single document at the
  key, single building on the parcel holding the address point), then the
  raw address concordance;
- E ``formes_identifiants``: shapes of ``numero_dpe`` and of BAN keys (never
  a value);
- F ``adresses_declarees``: raw ADEME address diverging from the BAN label,
  complement present in the raw fields (counts only);
- G ``capture``: the frozen capture of the first reboot, matched by number;
- H ``recuperation_adresse_brute``: orphan-key documents whose raw address
  names exactly one local BAN address (exact match after the normalisation
  ``REGLE_NORMALISATION``; no similarity, no distance);
- I ``id_rnb``: id_rnb filled, by exact ``provenance_id_rnb`` value and by
  building type; houses whose BAN point is outside any parcel carrying one;
- J ``cad_parcelles``: parcels named by the raw BAN CSV of the last BAN
  import, read from the source archive (read-only mount of the tool), and the
  D-110 (ii)/(iii) recomputed with them;
- ``d109_plus_recent``: type of the most recent document at addresses with
  two documents or more.

Launch on the work base (role ``wb_reader``, script on standard input, its
imports from the checkout of the served SHA)::

    wb-tool.sh --read-only python - --base --json - \\
        < api/backend/scripts/mesure_dpe_documents.py

The connection is opened by ``_open_read_only_connection`` (asserts
``transaction_read_only = on``) and every statement is checked by
``assert_select_only`` against ``ALLOWED_SQL`` -- both imported from
``verify_golden_cases``, never re-implemented (T-57-14). The output holds
aggregates only; ``serialiser`` refuses any DPE-number or BAN-key shape
(T-58-02). The capture file (``--capture``) carries DPE numbers: it is
produced by ``--extraire-capture`` outside the repository and never committed.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import itertools
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import psycopg

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.etl_dpe.adresse_brute import (  # block H rule, one implementation
    REGLE_NORMALISATION,
    TYPES_CLE,
    VERDICTS_H,
    adresse_du_document,
    classer_document,
    indexer_adresses,
    sans_contradiction,
)

# Re-exported: the block H tests read the rule through this script.
from app.etl_dpe.adresse_brute import TYPES_VOIE_ABREGES as TYPES_VOIE_ABREGES
from app.etl_dpe.adresse_brute import decouper_adresse as decouper_adresse
from app.etl_dpe.adresse_brute import normaliser_libelle as normaliser_libelle
from app.etl_dpe.adresse_brute import normaliser_voie as normaliser_voie
from app.etl_dpe.config import API_SELECT, COLUMN_MAPPING
from scripts.mesure_dpe_maison import NUMERO_TETE_RE
from scripts.verify_golden_cases import (
    GoldenSecurityError,
    _open_read_only_connection,
    assert_select_only,
)

DEPARTEMENTS_DEFAUT: tuple[str, ...] = ("44", "85")
DATE_NOUVELLE_METHODE = date(2021, 7, 1)
MOTIFS_ECART: tuple[str, ...] = (
    "cle_ban_vide",
    "cle_ban_orpheline",
    "sans_geometrie",
    "graphe_incoherent",
    "autre",
)
CLASSES_DOCS_CLE: tuple[str, ...] = ("1", "2-5", "6-10", "11-20", ">20")
MOTIF_NUMERO_DPE = "^[0-9A-Z]{13}$"
MOTIF_CLE_BAN = (
    "^[0-9]{2}[0-9AB][0-9]{2}_[0-9A-Za-z]{4,5}_[0-9]{5}(_[0-9A-Za-z]{1,10})?$"
)
# Output guard: the same shapes as the plan's MESURE_OK gate.
_FUITE_DPE = re.compile(r"(?<![0-9A-Za-z])\d{4}[A-Z]\d{7}[A-Z](?![0-9A-Za-z])")
# Wider than the plan gate: real voie segments have 4 to 6 characters (block E)
# and a two-segment key (commune_voie, lieu-dit) is a key too.
_FUITE_BAN = re.compile(
    r"(?<![0-9A-Za-z])[0-9]{2}[0-9AB][0-9]{2}_[0-9A-Za-z]{4,6}"
    r"(?:_[0-9]{5})?(?![0-9A-Za-z])"
)
# Complement words searched in the normalised raw address (counts only).
COMPLEMENT_RE = (
    "(^| )(etage|etg|porte|bat|batiment|escalier|esc|appartement|appart|appt|apt)( |$)"
)
_FICHIER_CAPTURE = re.compile(r"^dpe-\d{5}\.json$")
ADRESSE_SQL_RE = (
    "^[[:space:]]*0*[0-9]+([[:space:]]*(bis|ter|quater|quinquies)|[a-z])?[[:space:]]+"
)


class PartitionError(RuntimeError):
    """The partition of documents does not add up to the total."""


def normaliser_voie_sql(colonne: str) -> str:
    """Block-2 voie normalisation of ``mesure_dpe_maison`` (equality-tested)."""
    return (
        "regexp_replace(\n"
        f"    lower(unaccent(COALESCE({colonne}, '')))\n"
        "    , '[^a-z0-9]+', ' ', 'g'\n"
        ")"
    )


# --- Facts per document, grouped (blocks A, B, C, D, F and G) ---------------
# SQL selects facts, Python classifies (D-10 doctrine). Identifiers live only in
# the CTEs; the final projection is booleans, classes and counts.
_FAITS_GABARIT = r"""
WITH cibles AS (
    SELECT d.code_departement,
           d.regime_dpe,
           d.type_batiment,
           d.is_tombstone,
           d.position,
           d.numero_dpe,
           d.identifiant_ban,
           d.date_etablissement_dpe,
           d.date_fin_validite_dpe,
           d.lifecycle_facts,
           d.adresse_brut,
           d.adresse_complete_brut,
           d.complement_adresse_logement,
           d.id_rnb
    FROM diagnostics_dpe d
    WHERE __FILTRE__
),
remplaces AS (
    SELECT DISTINCT r.numero_dpe_remplace AS numero
    FROM diagnostics_dpe r
    WHERE r.numero_dpe_remplace IS NOT NULL
),
cles AS (
    SELECT DISTINCT c.code_departement,
           c.identifiant_ban
    FROM cibles c
    WHERE NULLIF(btrim(c.identifiant_ban), '') IS NOT NULL
),
stats_cle AS (
    SELECT k.code_departement,
           k.identifiant_ban,
           COUNT(*) AS n_docs,
           COUNT(*) FILTER (WHERE d.type_batiment = 'maison') AS n_maison
    FROM cles k
    JOIN diagnostics_dpe d
      ON d.identifiant_ban = k.identifiant_ban
     AND d.code_departement = k.code_departement
    GROUP BY k.code_departement, k.identifiant_ban
),
cles_regime AS (
    SELECT DISTINCT c.code_departement,
           c.identifiant_ban,
           COALESCE(c.regime_dpe, 'inconnu') AS regime
    FROM cibles c
    WHERE NULLIF(btrim(c.identifiant_ban), '') IS NOT NULL
),
stats_cle_regime AS (
    SELECT k.code_departement,
           k.identifiant_ban,
           k.regime,
           COUNT(*) AS n_docs
    FROM cles_regime k
    JOIN diagnostics_dpe d
      ON d.identifiant_ban = k.identifiant_ban
     AND d.code_departement = k.code_departement
     AND COALESCE(d.regime_dpe, 'inconnu') = k.regime
    GROUP BY k.code_departement, k.identifiant_ban, k.regime
),
adresse_cle AS (
    SELECT DISTINCT ON (k.code_departement, k.identifiant_ban)
           k.code_departement,
           k.identifiant_ban,
           a.position,
           a.numero,
           a.rep,
           a.nom_voie
    FROM cles k
    JOIN adresses a
      ON a.ban_id = k.identifiant_ban
     AND a.code_departement = k.code_departement
    ORDER BY k.code_departement, k.identifiant_ban, a.id
),
representants_capture AS (
    SELECT c.code_departement,
           c.identifiant_ban,
           MIN(c.numero_dpe) AS premier_numero,
           MIN(c.numero_dpe) FILTER (
               WHERE c.type_batiment = 'maison'
                 AND ac.identifiant_ban IS NOT NULL
                 AND ac.position IS NOT NULL
                 AND NOT COALESCE(c.is_tombstone, false)
                 AND NOT COALESCE(c.lifecycle_facts -> 'graph_issues', '[]'::jsonb)
                     ?| array['cycle', 'fork', 'truncated', 'ambiguous_target']
           ) AS premiere_maison_consultable
    FROM cibles c
    LEFT JOIN adresse_cle ac
      ON ac.code_departement = c.code_departement
     AND ac.identifiant_ban = c.identifiant_ban
    GROUP BY c.code_departement, c.identifiant_ban
),
representants_capture_regime AS (
    SELECT c.code_departement,
           c.identifiant_ban,
           COALESCE(c.regime_dpe, 'inconnu') AS regime,
           MIN(c.numero_dpe) AS premier_numero,
           MIN(c.numero_dpe) FILTER (
               WHERE c.type_batiment = 'maison'
                 AND ac.identifiant_ban IS NOT NULL
                 AND ac.position IS NOT NULL
                 AND NOT COALESCE(c.is_tombstone, false)
                 AND NOT COALESCE(c.lifecycle_facts -> 'graph_issues', '[]'::jsonb)
                     ?| array['cycle', 'fork', 'truncated', 'ambiguous_target']
           ) AS premiere_maison_consultable
    FROM cibles c
    LEFT JOIN adresse_cle ac
      ON ac.code_departement = c.code_departement
     AND ac.identifiant_ban = c.identifiant_ban
    WHERE NULLIF(btrim(c.identifiant_ban), '') IS NOT NULL
    GROUP BY c.code_departement, c.identifiant_ban,
             COALESCE(c.regime_dpe, 'inconnu')
),
batiments_cle AS (
    SELECT ac.code_departement,
           ac.identifiant_ban,
           p.id_parcelle IS NOT NULL AS a_parcelle,
           b.n_batiments,
           b.n_hors_annexe
    FROM adresse_cle ac
    JOIN stats_cle s
      ON s.code_departement = ac.code_departement
     AND s.identifiant_ban = ac.identifiant_ban
     AND s.n_maison > 0
    LEFT JOIN LATERAL (
        SELECT pp.id_parcelle,
               pp.geometrie
        FROM parcelles pp
        WHERE pp.code_departement = ac.code_departement
          AND ST_Intersects(pp.geometrie, ac.position)
        ORDER BY pp.id_parcelle
        LIMIT 1
    ) p ON true
    LEFT JOIN LATERAL (
        SELECT COUNT(*) AS n_batiments,
               COUNT(*) FILTER (
                   WHERE bb.usage_1 IS DISTINCT FROM 'Annexe'
               ) AS n_hors_annexe
        FROM batiments bb
        WHERE bb.code_departement = ac.code_departement
          AND ST_Intersects(bb.geometrie, p.geometrie)
          AND ST_Covers(p.geometrie, ST_PointOnSurface(bb.geometrie))
    ) b ON p.id_parcelle IS NOT NULL
),
faits AS (
    SELECT c.code_departement,
           COALESCE(c.regime_dpe, 'inconnu') AS regime,
           CASE
               WHEN c.type_batiment IN ('maison', 'appartement', 'immeuble')
                   THEN c.type_batiment
               ELSE 'autre'
           END AS type_batiment,
           NULLIF(btrim(c.identifiant_ban), '') IS NULL AS cle_vide,
           ac.identifiant_ban IS NOT NULL AS cle_jointe,
           c.position IS NULL AS sans_position,
           ac.identifiant_ban IS NOT NULL AND ac.position IS NULL
               AS adresse_sans_position,
           COALESCE(c.is_tombstone, false) AS tombstone,
           COALESCE(c.lifecycle_facts -> 'graph_issues', '[]'::jsonb)
               ?| array['cycle', 'fork', 'truncated', 'ambiguous_target']
               AS graphe_incoherent,
           COALESCE(c.lifecycle_facts -> 'graph_issues', '[]'::jsonb)
               ? 'missing_target' AS cible_absente,
           rp.numero IS NOT NULL AS remplace,
           c.date_etablissement_dpe IS NULL AS date_etablissement_absente,
           COALESCE(c.date_etablissement_dpe < DATE '2021-07-01', false)
               AS ancienne_methode,
           c.date_fin_validite_dpe IS NULL AS date_fin_absente,
           COALESCE(c.date_fin_validite_dpe < %(date_import)s::date, false)
               AS expire_import,
           COALESCE(c.date_fin_validite_dpe < %(date_mesure)s::date, false)
               AS expire_mesure,
           CASE
               WHEN ac.identifiant_ban IS NULL THEN NULL
               WHEN s.n_docs = 1 THEN '1'
               WHEN s.n_docs <= 5 THEN '2-5'
               WHEN s.n_docs <= 10 THEN '6-10'
               WHEN s.n_docs <= 20 THEN '11-20'
               ELSE '>20'
           END AS classe_docs_cle,
           CASE
               WHEN ac.identifiant_ban IS NULL THEN NULL
               WHEN sr.n_docs = 1 THEN '1'
               WHEN sr.n_docs <= 5 THEN '2-5'
               WHEN sr.n_docs <= 10 THEN '6-10'
               WHEN sr.n_docs <= 20 THEN '11-20'
               ELSE '>20'
           END AS classe_docs_cle_regime,
           ac.identifiant_ban IS NOT NULL
               AND c.numero_dpe = rc.premier_numero AS premier_de_cle,
           ac.identifiant_ban IS NOT NULL
               AND c.numero_dpe = rcr.premier_numero AS premier_de_cle_regime,
           c.numero_dpe = rc.premiere_maison_consultable
               AS premiere_maison_consultable_de_cle,
           c.numero_dpe = rcr.premiere_maison_consultable
               AS premiere_maison_consultable_de_cle_regime,
           CASE
               WHEN bc.identifiant_ban IS NULL THEN 'non_calcule'
               WHEN NOT bc.a_parcelle THEN 'sans_parcelle'
               WHEN bc.n_batiments = 0 THEN '0'
               WHEN bc.n_batiments = 1 THEN '1'
               ELSE '2+'
           END AS batiments_parcelle,
           CASE
               WHEN bc.identifiant_ban IS NULL THEN 'non_calcule'
               WHEN NOT bc.a_parcelle THEN 'sans_parcelle'
               WHEN bc.n_hors_annexe = 0 THEN '0'
               WHEN bc.n_hors_annexe = 1 THEN '1'
               ELSE '2+'
           END AS batiments_hors_annexe,
           (regexp_match(c.adresse_brut, '__NUMERO_TETE_RE__'))[1]::integer
               AS numero_brut,
           (regexp_match(ac.numero, '__NUMERO_TETE_RE__'))[1]::integer
               AS numero_ban,
           COALESCE(ac.numero ~ '^[[:space:]]*0*[0-9]+[[:space:]]*$', false)
               AS numero_ban_interpretable,
           NULLIF(lower(btrim(ac.rep)), '') AS suffixe_ban,
           COALESCE(
               (regexp_match(
                   lower(unaccent(COALESCE(c.adresse_brut, ''))),
                   '^[[:space:]]*0*[0-9]+[[:space:]]*(bis|ter|quater|quinquies)[[:space:]]+'
               ))[1],
               (regexp_match(
                   lower(unaccent(COALESCE(c.adresse_brut, ''))),
                   '^[[:space:]]*0*[0-9]+([a-z])[[:space:]]+'
               ))[1]
           ) AS suffixe_brut,
           lower(COALESCE(ac.rep, '')) ~
               '^[[:space:]]*(bis|ter|quater|quinquies|[a-z])?[[:space:]]*$'
               AS suffixe_ban_interpretable,
           lower(unaccent(COALESCE(c.adresse_brut, ''))) ~
               '__ADRESSE_SQL_RE__[a-z0-9].*$'
               AS adresse_brute_interpretable,
           NULLIF(btrim(__VOIE_BAN__), '') AS voie_ban_normalisee,
           NULLIF(btrim(regexp_replace(
               __ADRESSE_BRUT__,
               '__ADRESSE_SQL_RE__', '', 'g'
           )), '') AS voie_brut_normalisee,
           __ADRESSES_BRUTES__
               ~ '__COMPLEMENT_RE__'
               AS complement_brut,
           NULLIF(btrim(c.complement_adresse_logement), '') IS NOT NULL
               AS complement_logement,
           NULLIF(btrim(c.id_rnb), '') IS NOT NULL AS id_rnb_renseigne
    FROM cibles c
    LEFT JOIN remplaces rp ON rp.numero = c.numero_dpe
    LEFT JOIN stats_cle s
      ON s.code_departement = c.code_departement
     AND s.identifiant_ban = c.identifiant_ban
    LEFT JOIN stats_cle_regime sr
      ON sr.code_departement = c.code_departement
     AND sr.identifiant_ban = c.identifiant_ban
     AND sr.regime = COALESCE(c.regime_dpe, 'inconnu')
    LEFT JOIN representants_capture rc
      ON rc.code_departement = c.code_departement
     AND rc.identifiant_ban = c.identifiant_ban
    LEFT JOIN representants_capture_regime rcr
      ON rcr.code_departement = c.code_departement
     AND rcr.identifiant_ban = c.identifiant_ban
     AND rcr.regime = COALESCE(c.regime_dpe, 'inconnu')
    LEFT JOIN adresse_cle ac
      ON ac.code_departement = c.code_departement
     AND ac.identifiant_ban = c.identifiant_ban
    LEFT JOIN batiments_cle bc
      ON bc.code_departement = c.code_departement
     AND bc.identifiant_ban = c.identifiant_ban
)
SELECT code_departement,
       regime,
       type_batiment,
       cle_vide,
       cle_jointe,
       sans_position,
       adresse_sans_position,
       tombstone,
       graphe_incoherent,
       cible_absente,
       remplace,
       date_etablissement_absente,
       ancienne_methode,
       date_fin_absente,
       expire_import,
       expire_mesure,
       classe_docs_cle,
       classe_docs_cle_regime,
       premier_de_cle,
       premier_de_cle_regime,
       premiere_maison_consultable_de_cle,
       premiere_maison_consultable_de_cle_regime,
       batiments_parcelle,
       batiments_hors_annexe,
       CASE
           WHEN NOT cle_jointe THEN NULL
           WHEN numero_brut IS NULL OR numero_ban IS NULL THEN 'non_evaluable'
           WHEN NOT numero_ban_interpretable
               OR NOT suffixe_ban_interpretable
               OR NOT adresse_brute_interpretable THEN 'non_evaluable'
           WHEN voie_ban_normalisee IS NULL OR voie_brut_normalisee IS NULL
               THEN 'non_evaluable'
           WHEN numero_brut = numero_ban
               AND COALESCE(suffixe_brut, '') = COALESCE(suffixe_ban, '')
               AND voie_ban_normalisee = voie_brut_normalisee
               THEN 'concordant'
           ELSE 'ecart'
       END AS concordance_adresse,
       complement_brut,
       complement_logement,
       id_rnb_renseigne,
       COUNT(*) AS effectif
FROM faits
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19,
         20, 21, 22, 23, 24, 25, 26, 27, 28
ORDER BY 1, 2, 3
"""


def _faits_sql(filtre: str) -> str:
    return (
        _FAITS_GABARIT.replace("__FILTRE__", filtre)
        .replace("__NUMERO_TETE_RE__", NUMERO_TETE_RE)
        .replace("__COMPLEMENT_RE__", COMPLEMENT_RE)
        .replace("__ADRESSE_SQL_RE__", ADRESSE_SQL_RE)
        .replace("__VOIE_BAN__", normaliser_voie_sql("ac.nom_voie"))
        .replace("__ADRESSE_BRUT__", normaliser_voie_sql("c.adresse_brut"))
        .replace(
            "__ADRESSES_BRUTES__",
            normaliser_voie_sql(
                "COALESCE(c.adresse_brut, '') || ' ' || "
                "COALESCE(c.adresse_complete_brut, '')"
            ),
        )
    )


FAITS_DOCUMENTS_BASE_SQL = _faits_sql("d.code_departement = ANY(%(departements)s)")
FAITS_DOCUMENTS_CAPTURE_SQL = _faits_sql("d.numero_dpe = ANY(%(numeros)s)")

# --- Block E: identifier shapes (length and character classes, never values) -
FORMES_NUMERO_DPE_SQL = """
SELECT d.code_departement,
       COALESCE(d.regime_dpe, 'inconnu') AS regime,
       d.numero_dpe ~ '^[0-9A-Z]{13}$' AS conforme,
       d.numero_dpe ~ '^[0-9]{4}[A-Z][0-9]{7}[A-Z]$' AS forme_ademe,
       substr(d.numero_dpe, 3, 2) = d.code_departement AS departement_concorde,
       length(d.numero_dpe) AS longueur,
       d.numero_dpe ~ '[0-9]' AS a_chiffre,
       d.numero_dpe ~ '[A-Z]' AS a_majuscule,
       d.numero_dpe ~ '[a-z]' AS a_minuscule,
       d.numero_dpe ~ '[^0-9A-Za-z]' AS a_autre,
       COUNT(*) AS effectif
FROM diagnostics_dpe d
WHERE d.code_departement = ANY(%(departements)s)
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
ORDER BY 1, 2, 3, 4, 5, 6
"""

_FORMES_CLE_GABARIT = """
    SELECT '__TABLE__' AS origine,
           t.code_departement,
           __REGIME__ AS regime,
           t.__COLONNE__ IS NULL OR btrim(t.__COLONNE__) = '' AS absente,
           COALESCE(t.__COLONNE__ ~ '__MOTIF__', false) AS conforme,
           length(t.__COLONNE__) AS longueur,
           array_length(string_to_array(t.__COLONNE__, '_'), 1) AS segments,
           array_to_string(
               ARRAY(
                   SELECT length(s.segment)
                   FROM unnest(string_to_array(t.__COLONNE__, '_'))
                       WITH ORDINALITY AS s(segment, rang)
                   ORDER BY s.rang
               )
               , '-'
           ) AS profil,
           COALESCE(t.__COLONNE__ ~ '[a-z]', false) AS a_minuscule,
           COALESCE(t.__COLONNE__ ~ '[A-Z]', false) AS a_majuscule,
           COALESCE(t.__COLONNE__ ~ '[^0-9A-Za-z_]', false) AS a_autre
    FROM __TABLE__ t
    WHERE t.code_departement = ANY(%(departements)s)
"""


def _formes_cle(table: str, colonne: str, regime: str) -> str:
    return (
        _FORMES_CLE_GABARIT.replace("__TABLE__", table)
        .replace("__COLONNE__", colonne)
        .replace("__MOTIF__", MOTIF_CLE_BAN)
        .replace("__REGIME__", regime)
    )


FORMES_CLES_BAN_SQL = (
    "SELECT origine, code_departement, regime, absente, conforme, longueur, segments,\n"
    "       profil, a_minuscule, a_majuscule, a_autre, COUNT(*) AS effectif\n"
    "FROM (\n"
    + _formes_cle("adresses", "ban_id", "'tous'")
    + "    UNION ALL\n"
    + _formes_cle(
        "adresses",
        "ban_id",
        "r.regime",
    ).replace(
        "    WHERE t.code_departement = ANY(%(departements)s)",
        "    CROSS JOIN LATERAL (\n"
        "        SELECT DISTINCT COALESCE(d.regime_dpe, 'inconnu') AS regime\n"
        "        FROM diagnostics_dpe d\n"
        "        WHERE d.code_departement = t.code_departement\n"
        "          AND d.identifiant_ban = t.ban_id\n"
        "    ) r\n"
        "    WHERE t.code_departement = ANY(%(departements)s)",
    )
    + "    UNION ALL\n"
    + _formes_cle(
        "diagnostics_dpe", "identifiant_ban", "COALESCE(t.regime_dpe, 'inconnu')"
    )
    + ") formes\n"
    "GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11\n"
    "ORDER BY 1, 2, 3, 4, 5\n"
)

# --- Block G: documents of the capture found in base (internal projection) ---
CAPTURE_DOCUMENTS_SQL = """
SELECT d.numero_dpe,
       d.code_departement,
       d.etiquette_dpe,
       d.surface_habitable,
       d.date_etablissement_dpe
FROM diagnostics_dpe d
WHERE d.numero_dpe = ANY(%(numeros)s)
"""

# --- Metadata ----------------------------------------------------------------
DATE_IMPORT_SQL = """
SELECT MAX(r.completed_at) AS termine_le
FROM etl_runs r
WHERE r.dataset_name = 'dpe'
  AND r.status = 'success'
"""
INSTANTANE_SQL = """
SELECT MAX(d.source_snapshot_at) AS instantane
FROM diagnostics_dpe d
WHERE d.code_departement = ANY(%(departements)s)
"""
DEPARTEMENTS_SERVIS_SQL = """
SELECT s.code_departement
FROM instance_scope_departements s
ORDER BY s.code_departement
"""
TETE_ALEMBIC_SQL = "SELECT version_num FROM alembic_version"
RUN_BAN_SQL = """
SELECT r.id,
       r.completed_at
FROM etl_runs r
WHERE r.dataset_name = 'ban'
  AND r.status = 'success'
ORDER BY r.completed_at DESC
LIMIT 1
"""
ADRESSES_PAR_DEPARTEMENT_SQL = """
SELECT a.code_departement,
       COUNT(*) AS effectif
FROM adresses a
WHERE a.code_departement = ANY(%(departements)s)
GROUP BY 1
"""

# --- Block H: orphan-key documents and the local BAN (identifiers in memory) --
# Orphan = the B motive ``cle_ban_orpheline``: non-empty key absent from
# ``adresses`` of the department.
DOCUMENTS_ORPHELINS_SQL = """
SELECT d.code_departement,
       d.identifiant_ban,
       d.adresse_brut,
       d.adresse_complete_brut,
       d.code_postal_brut,
       d.nom_commune_brut
FROM diagnostics_dpe d
WHERE d.code_departement = ANY(%(departements)s)
  AND NULLIF(btrim(d.identifiant_ban), '') IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM adresses a
      WHERE a.ban_id = d.identifiant_ban
        AND a.code_departement = d.code_departement
  )
"""
ADRESSES_COMMUNES_SQL = """
SELECT DISTINCT a.code_commune,
       a.numero,
       a.rep,
       a.nom_voie,
       a.code_postal,
       a.ban_id
FROM adresses a
WHERE a.code_departement = %(departement)s
  AND a.code_commune = ANY(%(communes)s)
"""
CLES_AVEC_DPE_SQL = """
SELECT COUNT(*) AS cles,
       COUNT(*) FILTER (
           WHERE EXISTS (
               SELECT 1
               FROM diagnostics_dpe d
               WHERE d.identifiant_ban = u.cle
                 AND d.code_departement = %(departement)s
           )
       ) AS cles_avec_dpe
FROM unnest(%(cles)s::text[]) AS u(cle)
"""

# --- Block I: id_rnb by exact provenance value and building type -------------
RNB_SQL = """
SELECT d.code_departement,
       CASE
           WHEN d.type_batiment IN ('maison', 'appartement', 'immeuble')
               THEN d.type_batiment
           ELSE 'autre'
       END AS type_batiment,
       NULLIF(btrim(d.id_rnb), '') IS NOT NULL AS id_rnb_renseigne,
       d.provenance_id_rnb AS provenance,
       COUNT(*) AS effectif
FROM diagnostics_dpe d
WHERE d.code_departement = ANY(%(departements)s)
GROUP BY 1, 2, 3, 4
ORDER BY 1, 2, 3, 4
"""

# --- D-109: type of the most recent document at a reachable key --------------
D109_SQL = """
WITH docs AS (
    SELECT d.code_departement,
           d.identifiant_ban,
           CASE
               WHEN d.type_batiment IN ('maison', 'appartement', 'immeuble')
                   THEN d.type_batiment
               ELSE 'autre'
           END AS type_batiment,
           d.date_etablissement_dpe,
           MAX(d.date_etablissement_dpe) OVER (
               PARTITION BY d.code_departement, d.identifiant_ban
           ) AS date_max
    FROM diagnostics_dpe d
    WHERE d.code_departement = ANY(%(departements)s)
      AND NULLIF(btrim(d.identifiant_ban), '') IS NOT NULL
      AND EXISTS (
          SELECT 1
          FROM adresses a
          WHERE a.ban_id = d.identifiant_ban
            AND a.code_departement = d.code_departement
      )
),
cles AS (
    SELECT code_departement,
           identifiant_ban,
           COUNT(*) AS n_docs,
           COUNT(*) FILTER (WHERE date_etablissement_dpe = date_max) AS n_recents,
           MIN(type_batiment) FILTER (WHERE date_etablissement_dpe = date_max)
               AS type_min,
           MAX(type_batiment) FILTER (WHERE date_etablissement_dpe = date_max)
               AS type_max
    FROM docs
    GROUP BY code_departement, identifiant_ban
)
SELECT code_departement,
       n_docs >= 2 AS plusieurs,
       n_recents = 1 AS recent_unique,
       CASE
           WHEN type_min IS NULL THEN 'sans_date'
           WHEN type_min = type_max THEN type_min
           ELSE 'mixte'
       END AS type_recent,
       COUNT(*) AS effectif
FROM cles
GROUP BY 1, 2, 3, 4
ORDER BY 1, 2, 3, 4
"""

# --- Block J: houses per reachable key, parcels named by cad_parcelles -------
# Same point rule and building count as block D (batiments_cle); identifiers
# stay in memory, joined to the raw BAN CSV of the source archive.
MAISONS_CLES_SQL = """
WITH cles AS (
    SELECT d.code_departement,
           d.identifiant_ban,
           COUNT(*) AS n_docs,
           COUNT(*) FILTER (
               WHERE d.type_batiment = 'maison'
                 AND NOT COALESCE(d.is_tombstone, false)
                 AND NOT COALESCE(d.lifecycle_facts -> 'graph_issues', '[]'::jsonb)
                     ?| array['cycle', 'fork', 'truncated', 'ambiguous_target']
           ) AS n_maison
    FROM diagnostics_dpe d
    WHERE d.code_departement = ANY(%(departements)s)
      AND NULLIF(btrim(d.identifiant_ban), '') IS NOT NULL
    GROUP BY d.code_departement, d.identifiant_ban
),
adresse_cle AS (
    SELECT DISTINCT ON (k.code_departement, k.identifiant_ban)
           k.code_departement,
           k.identifiant_ban,
           k.n_docs,
           k.n_maison,
           a.position
    FROM cles k
    JOIN adresses a
      ON a.ban_id = k.identifiant_ban
     AND a.code_departement = k.code_departement
    WHERE k.n_maison > 0
    ORDER BY k.code_departement, k.identifiant_ban, a.id
)
SELECT ac.code_departement,
       ac.identifiant_ban,
       ac.n_docs,
       ac.n_maison,
       p.id_parcelle AS parcelle_point,
       b.n_batiments,
       b.n_hors_annexe
FROM adresse_cle ac
LEFT JOIN LATERAL (
    SELECT pp.id_parcelle,
           pp.geometrie
    FROM parcelles pp
    WHERE pp.code_departement = ac.code_departement
      AND ST_Intersects(pp.geometrie, ac.position)
    ORDER BY pp.id_parcelle
    LIMIT 1
) p ON true
LEFT JOIN LATERAL (
    SELECT COUNT(*) AS n_batiments,
           COUNT(*) FILTER (
               WHERE bb.usage_1 IS DISTINCT FROM 'Annexe'
           ) AS n_hors_annexe
    FROM batiments bb
    WHERE bb.code_departement = ac.code_departement
      AND ST_Intersects(bb.geometrie, p.geometrie)
      AND ST_Covers(p.geometrie, ST_PointOnSurface(bb.geometrie))
) b ON p.id_parcelle IS NOT NULL
WHERE ac.position IS NOT NULL
"""
PARCELLES_TROUVEES_SQL = """
SELECT COUNT(*) AS references_distinctes,
       COUNT(*) FILTER (
           WHERE EXISTS (
               SELECT 1
               FROM parcelles pp
               WHERE pp.code_departement = %(departement)s
                 AND pp.id_parcelle = u.id_parcelle
           )
       ) AS trouvees
FROM unnest(%(parcelles)s::text[]) AS u(id_parcelle)
"""
BATIMENTS_PARCELLES_SQL = """
SELECT u.id_parcelle,
       b.n_batiments,
       b.n_hors_annexe
FROM unnest(%(parcelles)s::text[]) AS u(id_parcelle)
JOIN LATERAL (
    SELECT pp.geometrie
    FROM parcelles pp
    WHERE pp.code_departement = %(departement)s
      AND pp.id_parcelle = u.id_parcelle
    ORDER BY pp.id
    LIMIT 1
) p ON true
CROSS JOIN LATERAL (
    SELECT COUNT(*) AS n_batiments,
           COUNT(*) FILTER (
               WHERE bb.usage_1 IS DISTINCT FROM 'Annexe'
           ) AS n_hors_annexe
    FROM batiments bb
    WHERE bb.code_departement = %(departement)s
      AND ST_Intersects(bb.geometrie, p.geometrie)
      AND ST_Covers(p.geometrie, ST_PointOnSurface(bb.geometrie))
) b
"""

ALLOWED_SQL: frozenset[str] = frozenset(
    {
        FAITS_DOCUMENTS_BASE_SQL,
        FAITS_DOCUMENTS_CAPTURE_SQL,
        FORMES_NUMERO_DPE_SQL,
        FORMES_CLES_BAN_SQL,
        CAPTURE_DOCUMENTS_SQL,
        DATE_IMPORT_SQL,
        INSTANTANE_SQL,
        DEPARTEMENTS_SERVIS_SQL,
        TETE_ALEMBIC_SQL,
        RUN_BAN_SQL,
        ADRESSES_PAR_DEPARTEMENT_SQL,
        DOCUMENTS_ORPHELINS_SQL,
        ADRESSES_COMMUNES_SQL,
        CLES_AVEC_DPE_SQL,
        RNB_SQL,
        D109_SQL,
        MAISONS_CLES_SQL,
        PARCELLES_TROUVEES_SQL,
        BATIMENTS_PARCELLES_SQL,
    }
)


def _executer(conn: Any, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    assert_select_only(sql, ALLOWED_SQL)
    return list(conn.execute(sql, dict(params)).fetchall())


def _iterer(conn: Any, sql: str, params: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    """Rows one by one: the local BAN of a department does not fit as dicts."""
    assert_select_only(sql, ALLOWED_SQL)
    return iter(conn.execute(sql, dict(params)))


# --- Pure aggregations ---------------------------------------------------------


def _n(ligne: Mapping[str, Any]) -> int:
    return int(ligne.get("effectif", 1))


def _part(numerateur: int, denominateur: int) -> float | None:
    return round(numerateur / denominateur, 4) if denominateur else None


def motif_ecart(ligne: Mapping[str, Any]) -> str | None:
    """Closed cascade: None = reachable from an address page."""
    if ligne["cle_vide"]:
        return "cle_ban_vide"
    if not ligne["cle_jointe"]:
        return "cle_ban_orpheline"
    if ligne["adresse_sans_position"]:
        return "sans_geometrie"
    if ligne["graphe_incoherent"]:
        return "graphe_incoherent"
    if ligne["tombstone"]:
        return "autre"
    return None


def verifier_partition(
    consultables: Mapping[str, Mapping[str, int]],
    ecartes: Mapping[str, Mapping[str, int]],
) -> None:
    for dep, compte in consultables.items():
        motifs = ecartes.get(dep, {})
        somme = compte["consultables_adresse"] + sum(
            motifs.get(motif, 0) for motif in MOTIFS_ECART
        )
        if somme != compte["total"]:
            raise PartitionError(f"partition {dep}: {somme} != {compte['total']}")


def agreger_consultables(lignes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    consultables: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "consultables_adresse": 0, "consultables_numero_seul": 0}
    )
    ecartes: dict[str, dict[str, int]] = defaultdict(
        lambda: dict.fromkeys(MOTIFS_ECART, 0)
    )
    for ligne in lignes:
        dep, regime, n = ligne["code_departement"], ligne["regime"], _n(ligne)
        total = consultables[dep]
        par_regime = total.setdefault("par_regime", {})
        regime_total = par_regime.setdefault(
            regime,
            {"total": 0, "consultables_adresse": 0, "consultables_numero_seul": 0},
        )
        total["total"] += n
        regime_total["total"] += n
        motif = motif_ecart(ligne)
        motifs = ecartes[dep]
        motifs_regime = motifs.setdefault("par_regime", {}).setdefault(
            regime, dict.fromkeys(MOTIFS_ECART, 0)
        )
        if motif is None:
            total["consultables_adresse"] += n
            regime_total["consultables_adresse"] += n
            continue
        motifs[motif] += n
        motifs_regime[motif] += n
        if not ligne["tombstone"]:
            total["consultables_numero_seul"] += n
            regime_total["consultables_numero_seul"] += n
    verifier_partition(consultables, ecartes)
    return {
        "consultables": {k: dict(v) for k, v in sorted(consultables.items())},
        "ecartes_par_motif": {k: dict(v) for k, v in sorted(ecartes.items())},
    }


def agreger_dates(lignes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    champs = {
        "ancienne_methode": "ancienne_methode",
        "date_etablissement_absente": "date_etablissement_absente",
        "expires_a_l_import": "expire_import",
        "expires_a_la_mesure": "expire_mesure",
        "date_fin_absente": "date_fin_absente",
        "remplaces": "remplace",
        "remplacant_cible_absente": "cible_absente",
        "tombstones": "tombstone",
    }
    resultat: dict[str, dict[str, dict[str, int]]] = defaultdict(dict)
    for ligne in lignes:
        dep, regime, n = ligne["code_departement"], ligne["regime"], _n(ligne)
        bloc = resultat[dep].setdefault(
            regime, {"total": 0, **dict.fromkeys(champs, 0)}
        )
        bloc["total"] += n
        for sortie, fait in champs.items():
            if ligne[fait]:
                bloc[sortie] += n
    return {k: dict(sorted(v.items())) for k, v in sorted(resultat.items())}


def agreger_documents_par_cle(lignes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    histo: dict[str, Counter[str]] = defaultdict(Counter)
    histo_regime: dict[str, dict[str, Counter[str]]] = defaultdict(
        lambda: defaultdict(Counter)
    )
    for ligne in lignes:
        if ligne["cle_jointe"] and ligne["premier_de_cle"]:
            histo[ligne["code_departement"]][ligne["classe_docs_cle"]] += _n(ligne)
        if ligne["cle_jointe"] and ligne.get("premier_de_cle_regime"):
            histo_regime[ligne["code_departement"]][ligne["regime"]][
                ligne.get("classe_docs_cle_regime", ligne["classe_docs_cle"])
            ] += _n(ligne)
    resultat = {}
    for dep, compte in sorted(histo.items()):
        adresses = sum(compte.values())
        plus_de_10 = compte["11-20"] + compte[">20"]
        resultat[dep] = {
            "adresses_avec_dpe": adresses,
            "histogramme": {c: compte[c] for c in CLASSES_DOCS_CLE},
            "adresses_plus_de_10": plus_de_10,
            "part_adresses_plus_de_10": _part(plus_de_10, adresses),
            "par_regime": {},
        }
        for regime, compte_regime in sorted(histo_regime[dep].items()):
            adresses_regime = sum(compte_regime.values())
            plus_de_10_regime = compte_regime["11-20"] + compte_regime[">20"]
            resultat[dep]["par_regime"][regime] = {
                "adresses_avec_dpe": adresses_regime,
                "histogramme": {c: compte_regime[c] for c in CLASSES_DOCS_CLE},
                "adresses_plus_de_10": plus_de_10_regime,
                "part_adresses_plus_de_10": _part(plus_de_10_regime, adresses_regime),
            }
    return resultat


def _d110_vide() -> dict[str, Any]:
    return {
        "maisons_consultables": 0,
        "i_cle_un_seul_document": 0,
        "ii_parcelle_un_batiment": 0,
        "ii_parcelle_un_batiment_hors_annexes": 0,
        "iii_cumul": 0,
        "iv_cumul_adresse_concordante": 0,
        "iii_cumul_hors_annexes": 0,
        "iv_cumul_hors_annexes": 0,
        "batiments_sur_la_parcelle": Counter(),
        "adresses_avec_dpe": 0,
        "i_adresses_distinctes": 0,
        "ii_adresses_distinctes": 0,
    }


def agreger_maisons_d110(lignes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    blocs: dict[str, dict[str, Any]] = defaultdict(_d110_vide)
    blocs_regime: dict[str, dict[str, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(_d110_vide)
    )
    for ligne in lignes:
        dep, regime, n = ligne["code_departement"], ligne["regime"], _n(ligne)
        for bloc, rep_adresse, rep_maison in (
            (
                blocs[dep],
                ligne["premier_de_cle"],
                ligne.get("premiere_maison_consultable_de_cle"),
            ),
            (
                blocs_regime[dep][regime],
                ligne.get("premier_de_cle_regime"),
                ligne.get(
                    "premiere_maison_consultable_de_cle_regime",
                    ligne.get("premiere_maison_consultable_de_cle"),
                ),
            ),
        ):
            if ligne["cle_jointe"] and rep_adresse:
                bloc["adresses_avec_dpe"] += n
            if ligne["type_batiment"] != "maison" or motif_ecart(ligne) is not None:
                continue
            unique = ligne["classe_docs_cle"] == "1"
            un_bat = ligne["batiments_parcelle"] == "1"
            un_bat_ha = ligne["batiments_hors_annexe"] == "1"
            concorde = ligne["concordance_adresse"] == "concordant"
            bloc["maisons_consultables"] += n
            bloc["batiments_sur_la_parcelle"][ligne["batiments_parcelle"]] += n
            bloc["i_cle_un_seul_document"] += n if unique else 0
            bloc["ii_parcelle_un_batiment"] += n if un_bat else 0
            bloc["ii_parcelle_un_batiment_hors_annexes"] += n if un_bat_ha else 0
            bloc["iii_cumul"] += n if unique and un_bat else 0
            bloc["iv_cumul_adresse_concordante"] += (
                n if unique and un_bat and concorde else 0
            )
            bloc["iii_cumul_hors_annexes"] += n if unique and un_bat_ha else 0
            bloc["iv_cumul_hors_annexes"] += (
                n if unique and un_bat_ha and concorde else 0
            )
            if rep_maison:
                bloc["i_adresses_distinctes"] += n if unique else 0
                bloc["ii_adresses_distinctes"] += n if un_bat else 0
    resultat = {}

    def finaliser(bloc: dict[str, Any]) -> dict[str, Any]:
        maisons, adresses = bloc["maisons_consultables"], bloc["adresses_avec_dpe"]
        bloc["batiments_sur_la_parcelle"] = dict(
            sorted(bloc["batiments_sur_la_parcelle"].items())
        )
        bloc["parts"] = {
            f"{cle}_sur_{base}": _part(
                bloc[
                    {"i": "i_adresses_distinctes", "ii": "ii_adresses_distinctes"}.get(
                        cle, champ
                    )
                    if base == "adresses"
                    else champ
                ],
                denom,
            )
            for cle, champ in (
                ("i", "i_cle_un_seul_document"),
                ("ii", "ii_parcelle_un_batiment"),
                ("iii", "iii_cumul"),
                ("iv", "iv_cumul_adresse_concordante"),
                ("iii_hors_annexes", "iii_cumul_hors_annexes"),
                ("iv_hors_annexes", "iv_cumul_hors_annexes"),
            )
            for base, denom in (("maisons", maisons), ("adresses", adresses))
        }
        return bloc

    for dep, bloc in sorted(blocs.items()):
        finaliser(bloc)
        bloc["par_regime"] = {
            regime: finaliser(bloc_regime)
            for regime, bloc_regime in sorted(blocs_regime[dep].items())
        }
        resultat[dep] = bloc
    return resultat


def agreger_adresses_declarees(lignes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    blocs: dict[str, Counter[str]] = defaultdict(Counter)
    blocs_regime: dict[str, dict[str, Counter[str]]] = defaultdict(
        lambda: defaultdict(Counter)
    )
    for ligne in lignes:
        if not ligne["cle_jointe"]:
            continue
        n = _n(ligne)
        for bloc in (
            blocs[ligne["code_departement"]],
            blocs_regime[ligne["code_departement"]][ligne["regime"]],
        ):
            bloc["documents_a_cle_jointe"] += n
            verdict = ligne["concordance_adresse"]
            bloc["adresse_divergente" if verdict == "ecart" else verdict] += n
            bloc["complement_dans_adresse_brute"] += (
                n if ligne["complement_brut"] else 0
            )
            bloc["complement_logement_renseigne"] += (
                n if ligne["complement_logement"] else 0
            )
            bloc["complement_l_un_ou_l_autre"] += (
                n if ligne["complement_brut"] or ligne["complement_logement"] else 0
            )
    resultat = {}

    def finaliser(bloc: Counter[str]) -> dict[str, Any]:
        total = bloc["documents_a_cle_jointe"]
        sortie = {
            k: bloc[k]
            for k in (
                "documents_a_cle_jointe",
                "concordant",
                "adresse_divergente",
                "non_evaluable",
                "complement_dans_adresse_brute",
                "complement_logement_renseigne",
                "complement_l_un_ou_l_autre",
            )
        }
        sortie["parts"] = {
            k: _part(sortie[k], total)
            for k in (
                "adresse_divergente",
                "non_evaluable",
                "complement_l_un_ou_l_autre",
            )
        }
        return sortie

    for dep, bloc in sorted(blocs.items()):
        sortie = finaliser(bloc)
        sortie["par_regime"] = {
            regime: finaliser(bloc_regime)
            for regime, bloc_regime in sorted(blocs_regime[dep].items())
        }
        resultat[dep] = sortie
    return resultat


def _classes(ligne: Mapping[str, Any], noms: Sequence[tuple[str, str]]) -> str:
    return "+".join(nom for champ, nom in noms if ligne.get(champ)) or "vide"


def agreger_formes(  # noqa: C901
    lignes_dpe: Iterable[Mapping[str, Any]], lignes_cle: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    def nouveau_numero() -> dict[str, Any]:
        return {
            "total": 0,
            "conformes": 0,
            "forme_ademe": 0,
            "departement_concorde": 0,
            "_non_conformes": Counter(),
        }

    def ajouter_numero(bloc: dict[str, Any], ligne: Mapping[str, Any]) -> None:
        n = _n(ligne)
        bloc["total"] += n
        bloc["conformes"] += n if ligne["conforme"] else 0
        bloc["forme_ademe"] += n if ligne["forme_ademe"] else 0
        bloc["departement_concorde"] += n if ligne["departement_concorde"] else 0
        if not ligne["conforme"]:
            classes = _classes(
                ligne,
                (
                    ("a_chiffre", "chiffres"),
                    ("a_majuscule", "majuscules"),
                    ("a_minuscule", "minuscules"),
                    ("a_autre", "autres"),
                ),
            )
            bloc["_non_conformes"][(ligne["longueur"], classes)] += n

    def finaliser_numero(bloc: dict[str, Any]) -> None:
        formes = bloc.pop("_non_conformes")
        bloc["formes_non_conformes"] = [
            {"longueur": lg, "classes": cl, "effectif": n}
            for (lg, cl), n in sorted(formes.items(), key=lambda x: (-x[1], str(x[0])))
        ]
        bloc["parts"] = {
            k: _part(bloc[k], bloc["total"])
            for k in ("conformes", "forme_ademe", "departement_concorde")
        }

    numero: dict[str, dict[str, Any]] = {}
    for ligne in lignes_dpe:
        bloc = numero.setdefault(ligne["code_departement"], nouveau_numero())
        ajouter_numero(bloc, ligne)
        regime = ligne.get("regime", "inconnu")
        ajouter_numero(
            bloc.setdefault("_par_regime", {}).setdefault(regime, nouveau_numero()),
            ligne,
        )
    for bloc in numero.values():
        par_regime = bloc.pop("_par_regime")
        finaliser_numero(bloc)
        for sous_bloc in par_regime.values():
            finaliser_numero(sous_bloc)
        bloc["par_regime"] = dict(sorted(par_regime.items()))

    def nouvelle_cle() -> dict[str, Any]:
        return {
            "total": 0,
            "absentes": 0,
            "conformes": 0,
            "_non_conformes": Counter(),
            "_profils": Counter(),
        }

    def ajouter_cle(bloc: dict[str, Any], ligne: Mapping[str, Any]) -> None:
        n = _n(ligne)
        bloc["total"] += n
        if ligne["absente"]:
            bloc["absentes"] += n
            return
        bloc["_profils"][(ligne.get("profil"), bool(ligne["conforme"]))] += n
        if ligne["conforme"]:
            bloc["conformes"] += n
        else:
            classes = _classes(
                ligne,
                (
                    ("a_minuscule", "minuscules"),
                    ("a_majuscule", "majuscules"),
                    ("a_autre", "autres"),
                ),
            )
            forme = (ligne["longueur"], ligne["segments"], ligne.get("profil"), classes)
            bloc["_non_conformes"][forme] += n

    cles: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for ligne in lignes_cle:
        origine, dep = ligne["origine"], ligne["code_departement"]
        regime = ligne.get("regime", "tous" if origine == "adresses" else "inconnu")
        if origine == "adresses" and regime != "tous":
            total = cles[origine].setdefault(dep, nouvelle_cle())
            ajouter_cle(
                total.setdefault("_par_regime", {}).setdefault(regime, nouvelle_cle()),
                ligne,
            )
            continue
        bloc = cles[origine].setdefault(dep, nouvelle_cle())
        ajouter_cle(bloc, ligne)
        if origine != "adresses":
            ajouter_cle(
                bloc.setdefault("_par_regime", {}).setdefault(regime, nouvelle_cle()),
                ligne,
            )
    for par_dep in cles.values():
        for bloc in par_dep.values():
            par_regime = bloc.pop("_par_regime", {})
            for cible in (bloc, *par_regime.values()):
                formes = cible.pop("_non_conformes")
                cible["formes_non_conformes"] = [
                    {
                        "longueur": lg,
                        "segments": sg,
                        "profil": pr,
                        "classes": cl,
                        "effectif": n,
                    }
                    for (lg, sg, pr, cl), n in sorted(
                        formes.items(), key=lambda x: (-x[1], str(x[0]))
                    )
                ]
                profils = cible.pop("_profils")
                cible["profils_segments"] = [
                    {"profil": pr, "conforme": cf, "effectif": n}
                    for (pr, cf), n in sorted(
                        profils.items(), key=lambda x: (-x[1], str(x[0]))
                    )
                ]
                cible["part_conformes_parmi_renseignees"] = _part(
                    cible["conformes"], cible["total"] - cible["absentes"]
                )
            bloc["par_regime"] = dict(sorted(par_regime.items()))
    return {
        "motifs": {"numero_dpe": MOTIF_NUMERO_DPE, "cle_ban": MOTIF_CLE_BAN},
        "numero_dpe": dict(sorted(numero.items())),
        "cles_ban": {t: dict(sorted(v.items())) for t, v in sorted(cles.items())},
    }


def _texte(valeur: Any) -> str:
    return "" if valeur is None else str(valeur).strip()


def _surface(valeur: Any) -> Decimal | None:
    texte = _texte(valeur)
    try:
        return Decimal(texte) if texte else None
    except ArithmeticError:
        return None


def agreger_capture(  # noqa: C901
    capture: Sequence[Mapping[str, str]],
    trouves: Iterable[Mapping[str, Any]],
    faits: Iterable[Mapping[str, Any]],
    departements_servis: Sequence[str],
) -> dict[str, Any]:
    par_numero = {ligne["numero_dpe"]: ligne for ligne in trouves}
    absents = {"commune_hors_couverture": 0, "absent_de_l_import": 0}
    differents = {
        "compares": 0,
        "etiquette_dpe": 0,
        "surface_habitable": 0,
        "date_etablissement_dpe": 0,
    }
    par_departement_insee: Counter[str] = Counter()
    for ligne in capture:
        dep_insee = (
            ligne["code_insee"][:3]
            if ligne["code_insee"].startswith("97")
            else ligne["code_insee"][:2]
        )
        par_departement_insee[dep_insee] += 1
        base = par_numero.get(ligne["numero"])
        if base is None:
            servi = dep_insee in departements_servis
            absents["absent_de_l_import" if servi else "commune_hors_couverture"] += 1
            continue
        differents["compares"] += 1
        if _texte(base["etiquette_dpe"]).upper() != _texte(ligne["etiquette"]).upper():
            differents["etiquette_dpe"] += 1
        s_base, s_capture = (
            _surface(base["surface_habitable"]),
            _surface(ligne["surface"]),
        )
        if (
            s_base is None
            or s_capture is None
            or abs(s_base - s_capture) > Decimal("0.05")
        ):
            differents["surface_habitable"] += 1
        date_base = base["date_etablissement_dpe"]
        if (
            _texte(date_base.isoformat() if date_base else None)
            != _texte(ligne["date"])[:10]
        ):
            differents["date_etablissement_dpe"] += 1
    faits = list(faits)
    consult = agreger_consultables(faits)
    total_consultables = sum(
        v["consultables_adresse"] for v in consult["consultables"].values()
    )
    numero_seul = sum(
        v["consultables_numero_seul"] for v in consult["consultables"].values()
    )
    motifs: Counter[str] = Counter()
    motifs_regime: dict[str, Counter[str]] = defaultdict(Counter)
    for par_motif in consult["ecartes_par_motif"].values():
        motifs.update({motif: par_motif[motif] for motif in MOTIFS_ECART})
        for regime, compte in par_motif.get("par_regime", {}).items():
            motifs_regime[regime].update(compte)

    def fusionner_d110(cible: dict[str, Any], source: Mapping[str, Any]) -> None:
        for cle, valeur in source.items():
            if cle in {"parts", "batiments_sur_la_parcelle", "par_regime"}:
                continue
            cible[cle] += valeur

    def finaliser_d110_capture(bloc: dict[str, Any]) -> dict[str, Any]:
        bloc.pop("batiments_sur_la_parcelle")
        bloc["parts"] = {
            "i_sur_maisons": _part(
                bloc["i_cle_un_seul_document"], bloc["maisons_consultables"]
            ),
            "ii_sur_maisons": _part(
                bloc["ii_parcelle_un_batiment"], bloc["maisons_consultables"]
            ),
            "iii_sur_maisons": _part(bloc["iii_cumul"], bloc["maisons_consultables"]),
            "iv_sur_maisons": _part(
                bloc["iv_cumul_adresse_concordante"],
                bloc["maisons_consultables"],
            ),
            "i_sur_adresses": _part(
                bloc["i_adresses_distinctes"], bloc["adresses_avec_dpe"]
            ),
            "ii_sur_adresses": _part(
                bloc["ii_adresses_distinctes"], bloc["adresses_avec_dpe"]
            ),
            "iii_sur_adresses": _part(bloc["iii_cumul"], bloc["adresses_avec_dpe"]),
            "iv_sur_adresses": _part(
                bloc["iv_cumul_adresse_concordante"], bloc["adresses_avec_dpe"]
            ),
        }
        return bloc

    d110 = _d110_vide()
    d110_regime: dict[str, dict[str, Any]] = defaultdict(_d110_vide)
    for bloc in agreger_maisons_d110(faits).values():
        fusionner_d110(d110, bloc)
        for regime, bloc_regime in bloc.get("par_regime", {}).items():
            fusionner_d110(d110_regime[regime], bloc_regime)
    finaliser_d110_capture(d110)
    d110["par_regime"] = {
        regime: finaliser_d110_capture(bloc)
        for regime, bloc in sorted(d110_regime.items())
    }
    return {
        "documents_captures": len(capture),
        "par_departement_insee": dict(sorted(par_departement_insee.items())),
        "departements_servis": list(departements_servis),
        "retrouves_en_base": len(par_numero),
        "consultables_adresse": total_consultables,
        "consultables_numero_seul": numero_seul,
        "ecartes_par_motif": {
            **{m: motifs[m] for m in MOTIFS_ECART},
            "par_regime": {
                regime: {m: compte[m] for m in MOTIFS_ECART}
                for regime, compte in sorted(motifs_regime.items())
            },
        },
        "absents": absents,
        "champs_differents": differents,
        "maisons_cle_unique_capture_seule": (
            "non mesurable sur la capture seule "
            "(identifiant_ban et type_batiment absents de la capture)"
        ),
        "d110_retrouves_attributs_base": d110,
    }


# --- Block H: exact raw-address match, no similarity, no distance -------------
# The rule lives in ``app.etl_dpe.adresse_brute`` (plan 04.1-13): the DPE import
# attaches orphans with the very functions this block measures with. Its names
# are imported above and stay readable here (``mesure.normaliser_voie``...).


def _h_vide() -> dict[str, Any]:
    return {
        "documents": 0,
        "avec_numero": 0,
        "numero_pris_dans_adresse_complete": 0,
        **dict.fromkeys(VERDICTS_H, 0),
        "contradiction_numero_cle": 0,
        "contradiction_numero_cle_parmi_uniques": 0,
        "contradiction_suffixe_cle": 0,
        "contradiction_suffixe_cle_parmi_uniques": 0,
        "suffixe_cle_ininterpretable_parmi_uniques": 0,
        "voie_de_cle_differente_parmi_uniques": 0,
        "dont_voie_differente_meme_format": 0,
        "format_voie_indetermine_parmi_uniques": 0,
        "code_postal_brut_concordant_parmi_uniques": 0,
        "unique_sans_contradiction": 0,
    }


def agreger_recuperables(
    classes: Iterable[tuple[str, Mapping[str, Any]]],
) -> tuple[dict[str, Any], dict[str, set[str]]]:
    """(department, class) pairs -> counts per key type, and the distinct keys
    found (in memory only, for the DPE lookup)."""
    blocs: dict[str, dict[str, dict[str, Any]]] = defaultdict(
        lambda: {t: _h_vide() for t in TYPES_CLE}
    )
    retrouvees: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {t: set() for t in TYPES_CLE}
    )
    for dep, classe in classes:
        bloc = blocs[dep][classe["type_cle"]]
        bloc["documents"] += 1
        bloc[classe["verdict"]] += 1
        if classe["verdict"] in {"sans_numero", "format_non_reconnu", "plage_numeros"}:
            continue
        bloc["avec_numero"] += 1
        bloc["numero_pris_dans_adresse_complete"] += (
            classe["source"] == "adresse_complete_brut"
        )
        unique = classe["verdict"] == "correspondance_unique"
        bloc["contradiction_numero_cle"] += bool(classe["contradiction_numero"])
        bloc["contradiction_numero_cle_parmi_uniques"] += unique and bool(
            classe["contradiction_numero"]
        )
        bloc["contradiction_suffixe_cle"] += bool(classe["contradiction_suffixe"])
        bloc["contradiction_suffixe_cle_parmi_uniques"] += unique and bool(
            classe["contradiction_suffixe"]
        )
        bloc["suffixe_cle_ininterpretable_parmi_uniques"] += (
            unique
            and classe["type_cle"] == "numerotee"
            and classe["contradiction_suffixe"] is None
        )
        voie_de_cle = classe["voie_de_cle"]
        bloc["voie_de_cle_differente_parmi_uniques"] += voie_de_cle in {
            "differente_meme_format",
            "format_different",
        }
        bloc["dont_voie_differente_meme_format"] += (
            voie_de_cle == "differente_meme_format"
        )
        bloc["format_voie_indetermine_parmi_uniques"] += (
            voie_de_cle == "format_different"
        )
        bloc["code_postal_brut_concordant_parmi_uniques"] += bool(
            classe["code_postal_concordant"]
        )
        if sans_contradiction(classe):
            bloc["unique_sans_contradiction"] += 1
            retrouvees[dep][classe["type_cle"]].add(classe["cle_retrouvee"])
    resultat: dict[str, Any] = {}
    cles: dict[str, set[str]] = {}
    for dep, par_type in sorted(blocs.items()):
        sortie: dict[str, Any] = {}
        for nom, bloc in par_type.items():
            bloc["adresses_distinctes_sans_contradiction"] = len(retrouvees[dep][nom])
            bloc["parts"] = {
                "avec_numero_sur_documents": _part(
                    bloc["avec_numero"], bloc["documents"]
                ),
                "unique_sur_documents": _part(
                    bloc["correspondance_unique"], bloc["documents"]
                ),
                "unique_sur_avec_numero": _part(
                    bloc["correspondance_unique"], bloc["avec_numero"]
                ),
                "sans_contradiction_sur_documents": _part(
                    bloc["unique_sans_contradiction"], bloc["documents"]
                ),
            }
            sortie[nom] = bloc
        sortie["commune_ou_voie"] = {
            k: par_type["commune"][k] + par_type["voie"][k]
            for k in par_type["commune"]
            if k not in {"parts", "adresses_distinctes_sans_contradiction"}
        }
        cles[dep] = retrouvees[dep]["commune"] | retrouvees[dep]["voie"]
        cles[dep + ":numerotee"] = retrouvees[dep]["numerotee"]
        sortie["commune_ou_voie"]["adresses_distinctes_sans_contradiction"] = len(
            cles[dep]
        )
        resultat[dep] = sortie
    return resultat, cles


# --- Block I: id_rnb -------------------------------------------------------------


def _libelle_provenance(valeur: Any) -> str:
    if valeur is None:
        return "(null)"
    return str(valeur) if str(valeur).strip() else "(vide)"


def agreger_rnb(
    lignes: Iterable[Mapping[str, Any]], faits: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    blocs: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "documents": 0,
            "id_rnb_renseigne": 0,
            "par_provenance": defaultdict(Counter),
            "par_type_batiment": defaultdict(Counter),
        }
    )
    for ligne in lignes:
        bloc, n = blocs[ligne["code_departement"]], _n(ligne)
        renseigne = n if ligne["id_rnb_renseigne"] else 0
        bloc["documents"] += n
        bloc["id_rnb_renseigne"] += renseigne
        for cle, valeur in (
            ("par_provenance", _libelle_provenance(ligne["provenance"])),
            ("par_type_batiment", ligne["type_batiment"]),
        ):
            bloc[cle][valeur]["documents"] += n
            bloc[cle][valeur]["id_rnb_renseigne"] += renseigne
    hors_parcelle: dict[str, Counter[str]] = defaultdict(Counter)
    for ligne in faits:
        if (
            ligne["type_batiment"] == "maison"
            and motif_ecart(ligne) is None
            and ligne["batiments_parcelle"] == "sans_parcelle"
        ):
            compte = hors_parcelle[ligne["code_departement"]]
            compte["maisons_point_hors_parcelle"] += _n(ligne)
            compte["dont_id_rnb_renseigne"] += (
                _n(ligne) if ligne.get("id_rnb_renseigne") else 0
            )
    resultat = {}
    for dep, bloc in sorted(blocs.items()):
        sortie: dict[str, Any] = {
            "documents": bloc["documents"],
            "id_rnb_renseigne": bloc["id_rnb_renseigne"],
            "part_id_rnb_renseigne": _part(bloc["id_rnb_renseigne"], bloc["documents"]),
        }
        for cle in ("par_provenance", "par_type_batiment"):
            sortie[cle] = {
                valeur: {
                    **dict(compte),
                    "part_id_rnb_renseigne": _part(
                        compte["id_rnb_renseigne"], compte["documents"]
                    ),
                }
                for valeur, compte in sorted(bloc[cle].items())
            }
        compte = hors_parcelle[dep]
        sortie["maisons_point_hors_parcelle"] = {
            "maisons": compte["maisons_point_hors_parcelle"],
            "dont_id_rnb_renseigne": compte["dont_id_rnb_renseigne"],
            "part": _part(
                compte["dont_id_rnb_renseigne"], compte["maisons_point_hors_parcelle"]
            ),
        }
        resultat[dep] = sortie
    return resultat


# --- D-109 -------------------------------------------------------------------------


def agreger_d109(lignes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    blocs: dict[str, Counter[str]] = defaultdict(Counter)
    for ligne in lignes:
        if not ligne["plusieurs"]:
            continue
        bloc, n = blocs[ligne["code_departement"]], _n(ligne)
        bloc["adresses_2_dpe_et_plus"] += n
        bloc[f"plus_recent_{ligne['type_recent']}"] += n
        if not ligne["recent_unique"] and ligne["type_recent"] != "sans_date":
            bloc["plus_recent_a_egalite_de_date"] += n
            bloc[f"egalite_de_date_{ligne['type_recent']}"] += n
    resultat = {}
    for dep, bloc in sorted(blocs.items()):
        sortie = dict(sorted(bloc.items()))
        sortie["part_plus_recent_appartement"] = _part(
            bloc["plus_recent_appartement"], bloc["adresses_2_dpe_et_plus"]
        )
        sortie["part_plus_recent_a_egalite_de_date"] = _part(
            bloc["plus_recent_a_egalite_de_date"], bloc["adresses_2_dpe_et_plus"]
        )
        resultat[dep] = sortie
    return resultat


# --- Block J: cad_parcelles of the raw BAN ------------------------------------------


def lire_ban_brute(
    lignes: Iterable[Mapping[str, str]], cles_utiles: set[str]
) -> dict[str, Any]:
    """One pass over a raw BAN CSV: coverage, referenced parcels, and the
    parcels and type_position of the useful keys (memory only)."""
    compte: Counter[str] = Counter()
    type_position: Counter[str] = Counter()
    references: set[str] = set()
    par_cle: dict[str, tuple[list[str], str]] = {}
    for ligne in lignes:
        compte["adresses_csv"] += 1
        parcelles = [p for p in _texte(ligne.get("cad_parcelles")).split("|") if p]
        position = _texte(ligne.get("type_position")) or "(vide)"
        type_position[position] += 1
        if parcelles:
            compte["cad_parcelles_renseigne"] += 1
            compte[
                "cad_1_parcelle" if len(parcelles) == 1 else "cad_2_parcelles_et_plus"
            ] += 1
            references.update(parcelles)
        cle = _texte(ligne.get("id"))
        if cle in cles_utiles:
            par_cle[cle] = (parcelles, position)
    return {
        "compte": compte,
        "type_position": type_position,
        "references": references,
        "par_cle": par_cle,
    }


def recalculer_d110(
    maisons: Iterable[Mapping[str, Any]],
    par_cle: Mapping[str, tuple[list[str], str]],
    batiments_cad: Mapping[str, tuple[int, int]],
    adresses_avec_dpe: int,
) -> dict[str, Any]:
    """Rule: the cad_parcelles parcel when exactly one is listed and found
    locally, else the parcel holding the BAN point, else undetermined."""
    c: Counter[str] = Counter()
    hors_type_position: Counter[str] = Counter()
    hors_etat_cad: Counter[str] = Counter()
    for ligne in maisons:
        n = int(ligne["n_maison"])
        seule = int(ligne["n_docs"]) == 1 and n == 1
        entree = par_cle.get(_texte(ligne["identifiant_ban"]))
        parcelles = entree[0] if entree else []
        if entree is None:
            etat_cad = "absente_du_csv"
        else:
            etat_cad = {0: "vide", 1: "1"}.get(len(parcelles), "2_et_plus")
        cad = parcelles[0] if len(parcelles) == 1 else None
        cad_trouvee = cad is not None and cad in batiments_cad
        point = ligne.get("parcelle_point")
        point_nb = (int(ligne["n_batiments"] or 0), int(ligne["n_hors_annexe"] or 0))
        c["maisons"] += n
        c["cles_maison_absentes_du_csv"] += entree is None
        if point is not None:
            c["ii_point"] += n if point_nb[0] == 1 else 0
            c["iii_point"] += 1 if seule and point_nb[0] == 1 else 0
            if cad_trouvee:
                c["cles_point_et_cad_unique"] += 1
                c["cles_point_et_cad_meme_parcelle"] += cad == point
        else:
            c["maisons_point_hors_parcelle"] += n
            hors_type_position[entree[1] if entree else "(absente du csv)"] += n
            hors_etat_cad[etat_cad] += n
            if cad_trouvee:
                c["hors_parcelle_cad_unique_trouvee"] += n
                c["hors_parcelle_cad_un_batiment"] += (
                    n if batiments_cad[cad][0] == 1 else 0
                )
                c["hors_parcelle_cad_un_batiment_hors_annexes"] += (
                    n if batiments_cad[cad][1] == 1 else 0
                )
        if cad_trouvee:
            source, nb = "cad_parcelles", batiments_cad[cad]
        elif point is not None:
            source, nb = "point", point_nb
        else:
            source, nb = "indeterminee", None
        c[f"source_{source}"] += n
        if nb is not None:
            c["ii_regle"] += n if nb[0] == 1 else 0
            c["ii_regle_hors_annexes"] += n if nb[1] == 1 else 0
            c["iii_regle"] += 1 if seule and nb[0] == 1 else 0
            c["iii_regle_hors_annexes"] += 1 if seule and nb[1] == 1 else 0
    sortie: dict[str, Any] = {
        k: c[k]
        for k in (
            "maisons",
            "cles_maison_absentes_du_csv",
            "maisons_point_hors_parcelle",
            "hors_parcelle_cad_unique_trouvee",
            "hors_parcelle_cad_un_batiment",
            "hors_parcelle_cad_un_batiment_hors_annexes",
            "source_cad_parcelles",
            "source_point",
            "source_indeterminee",
            "ii_point",
            "iii_point",
            "ii_regle",
            "ii_regle_hors_annexes",
            "iii_regle",
            "iii_regle_hors_annexes",
            "cles_point_et_cad_unique",
            "cles_point_et_cad_meme_parcelle",
        )
    }
    sortie["hors_parcelle_par_type_position"] = dict(
        sorted(hors_type_position.items(), key=lambda x: -x[1])
    )
    sortie["hors_parcelle_par_etat_cad_parcelles"] = dict(sorted(hors_etat_cad.items()))
    sortie["adresses_avec_dpe"] = adresses_avec_dpe
    sortie["parts"] = {
        "iii_point_sur_adresses": _part(c["iii_point"], adresses_avec_dpe),
        "iii_regle_sur_adresses": _part(c["iii_regle"], adresses_avec_dpe),
        "iii_regle_hors_annexes_sur_adresses": _part(
            c["iii_regle_hors_annexes"], adresses_avec_dpe
        ),
        "iii_point_sur_maisons": _part(c["iii_point"], c["maisons"]),
        "iii_regle_sur_maisons": _part(c["iii_regle"], c["maisons"]),
        "hors_parcelle_cad_unique_trouvee": _part(
            c["hors_parcelle_cad_unique_trouvee"], c["maisons_point_hors_parcelle"]
        ),
        "hors_parcelle_cad_un_batiment": _part(
            c["hors_parcelle_cad_un_batiment"], c["maisons_point_hors_parcelle"]
        ),
        "point_et_cad_meme_parcelle": _part(
            c["cles_point_et_cad_meme_parcelle"], c["cles_point_et_cad_unique"]
        ),
    }
    return sortie


# --- Database measurements ------------------------------------------------------


def _date_import(conn: Any) -> date | None:
    lignes = _executer(conn, DATE_IMPORT_SQL, {})
    termine = lignes[0]["termine_le"] if lignes else None
    return termine.date() if isinstance(termine, datetime) else termine


def mesurer_recuperables(
    conn: Any, departements: Sequence[str], orphelins_b: Mapping[str, int]
) -> dict[str, Any]:
    """Block H: orphan-key documents whose raw address names exactly one
    local BAN address on (INSEE of the key, street, number, suffix)."""
    documents = _executer(
        conn, DOCUMENTS_ORPHELINS_SQL, {"departements": list(departements)}
    )
    besoins: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for document in documents:
        decoupe, _ = adresse_du_document(document)
        if decoupe is not None:
            insee = _texte(document["identifiant_ban"]).split("_")[0]
            besoins[document["code_departement"]].add((insee, decoupe[2]))
    index: dict[str, Any] = {}
    for dep in departements:
        communes = sorted({insee for insee, _ in besoins[dep]})
        index[dep] = (
            indexer_adresses(
                _iterer(
                    conn,
                    ADRESSES_COMMUNES_SQL,
                    {"departement": dep, "communes": communes},
                ),
                besoins[dep],
            )
            if communes
            else {}
        )
    classes = (
        (
            d["code_departement"],
            classer_document(d, index.get(d["code_departement"], {})),
        )
        for d in documents
    )
    resultat, cles = agreger_recuperables(classes)
    for dep, sortie in resultat.items():
        for nom, cle_ensemble in (
            ("commune_ou_voie", dep),
            ("numerotee", dep + ":numerotee"),
        ):
            trouvees = sorted(cles.get(cle_ensemble, set()))
            ligne = (
                _executer(
                    conn, CLES_AVEC_DPE_SQL, {"departement": dep, "cles": trouvees}
                )
                if trouvees
                else []
            )
            sortie[nom]["adresses_sans_contradiction_ayant_deja_un_dpe"] = (
                int(ligne[0]["cles_avec_dpe"]) if ligne else 0
            )
        total = sum(sortie[t]["documents"] for t in TYPES_CLE)
        sortie["controle_total_egal_cle_ban_orpheline_bloc_b"] = (
            total == orphelins_b.get(dep)
        )
    return {"regle_normalisation": REGLE_NORMALISATION, "departements": resultat}


def mesurer_cad_parcelles(
    conn: Any,
    departements: Sequence[str],
    adresses_avec_dpe: Mapping[str, int],
    d110: Mapping[str, Mapping[str, Any]],
    racine: Path | None,
) -> dict[str, Any]:
    """Block J: raw BAN CSV of the last successful BAN import, read from the
    source archive (read-only mount); nothing is written anywhere."""
    if racine is None:
        return {"statut": "non_mesure", "raison": "SOURCE_ARCHIVE_DIR absent"}
    # Imported here: app.etl loads the application settings, the other blocks
    # do not need them.
    from app.etl.source_archive import SourceArchiveError, load_replay

    run = (_executer(conn, RUN_BAN_SQL, {}) or [None])[0]
    if run is None:
        return {"statut": "non_mesure", "raison": "aucun import BAN reussi"}
    try:
        records = sorted((Path(racine) / "ban" / "runs").glob(f"{run['id']}-*.json"))
        if len(records) != 1:
            raise SourceArchiveError("record_absent", f"ban/{run['id']}")
        replay = load_replay(racine, "ban", records[0].stem)
    except (SourceArchiveError, OSError) as erreur:
        raison = getattr(erreur, "reason", type(erreur).__name__)
        return {"statut": "non_mesure", "raison": f"archive BAN illisible: {raison}"}
    maisons = _executer(conn, MAISONS_CLES_SQL, {"departements": list(departements)})
    par_dep: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ligne in maisons:
        par_dep[ligne["code_departement"]].append(ligne)
    en_base = {
        ligne["code_departement"]: int(ligne["effectif"])
        for ligne in _executer(
            conn, ADRESSES_PAR_DEPARTEMENT_SQL, {"departements": list(departements)}
        )
    }
    fichiers = {entree["relpath"]: entree for entree in replay.files}
    resultat: dict[str, Any] = {}
    for dep in departements:
        entree = fichiers.get(f"raw/adresses-{dep}.csv")
        if entree is None:
            resultat[dep] = {"statut": "non_mesure", "raison": "fichier absent du run"}
            continue
        cles = {_texte(m["identifiant_ban"]) for m in par_dep[dep]}
        objet = Path(racine) / "ban" / entree["object"]
        with objet.open(encoding="utf-8", newline="") as flux:
            lu = lire_ban_brute(csv.DictReader(flux, delimiter=";"), cles)
        references = sorted(lu["references"])
        trouvees = (
            _executer(
                conn,
                PARCELLES_TROUVEES_SQL,
                {"departement": dep, "parcelles": references},
            )
            if references
            else []
        )
        uniques = sorted({p[0] for p, _ in lu["par_cle"].values() if len(p) == 1})
        batiments = {
            ligne["id_parcelle"]: (
                int(ligne["n_batiments"]),
                int(ligne["n_hors_annexe"]),
            )
            for ligne in (
                _executer(
                    conn,
                    BATIMENTS_PARCELLES_SQL,
                    {"departement": dep, "parcelles": uniques},
                )
                if uniques
                else []
            )
        }
        compte = lu["compte"]
        n_trouvees = int(trouvees[0]["trouvees"]) if trouvees else 0
        recalcul = recalculer_d110(
            par_dep[dep], lu["par_cle"], batiments, adresses_avec_dpe.get(dep, 0)
        )
        bloc_d = d110.get(dep, {})
        recalcul["controle_iii_point_egal_bloc_d"] = recalcul[
            "iii_point"
        ] == bloc_d.get("iii_cumul")
        recalcul["controle_maisons_egal_bloc_d"] = recalcul["maisons"] == bloc_d.get(
            "maisons_consultables"
        )
        resultat[dep] = {
            "adresses_csv": compte["adresses_csv"],
            "adresses_en_base": en_base.get(dep),
            "cad_parcelles_renseigne": compte["cad_parcelles_renseigne"],
            "cad_1_parcelle": compte["cad_1_parcelle"],
            "cad_2_parcelles_et_plus": compte["cad_2_parcelles_et_plus"],
            "parcelles_referencees_distinctes": len(references),
            "parcelles_referencees_trouvees": n_trouvees,
            "type_position": dict(
                sorted(lu["type_position"].items(), key=lambda x: -x[1])
            ),
            "parts": {
                "cad_parcelles_renseigne": _part(
                    compte["cad_parcelles_renseigne"], compte["adresses_csv"]
                ),
                "cad_2_parcelles_et_plus_parmi_renseignees": _part(
                    compte["cad_2_parcelles_et_plus"], compte["cad_parcelles_renseigne"]
                ),
                "parcelles_referencees_trouvees": _part(n_trouvees, len(references)),
            },
            "d110_recalcule": recalcul,
        }
    return {
        "statut": "mesure",
        "run_ban": replay.run_id,
        "record_sha256": replay.record_sha256,
        "regle": (
            "parcelle de cad_parcelles si une seule y est listee et qu'elle "
            "existe dans parcelles ; sinon parcelle contenant le point BAN "
            "(ST_Intersects, regle du bloc D) ; sinon indeterminee"
        ),
        "departements": resultat,
    }


def mesurer_base(
    conn: Any,
    departements: Sequence[str],
    date_mesure: date,
    racine_archive: Path | None = None,
) -> dict[str, Any]:
    params = {"departements": list(departements)}
    date_import = _date_import(conn)
    faits = _executer(
        conn,
        FAITS_DOCUMENTS_BASE_SQL,
        {**params, "date_import": date_import, "date_mesure": date_mesure},
    )
    consultables = agreger_consultables(faits)
    par_cle = agreger_documents_par_cle(faits)
    d110 = agreger_maisons_d110(faits)
    orphelins = {
        dep: motifs["cle_ban_orpheline"]
        for dep, motifs in consultables["ecartes_par_motif"].items()
    }
    return {
        "date_import": date_import.isoformat() if date_import else None,
        "date_mesure": date_mesure.isoformat(),
        "departements": list(departements),
        "dates": agreger_dates(faits),
        **consultables,
        "documents_par_cle": par_cle,
        "maisons_d110": d110,
        "adresses_declarees": agreger_adresses_declarees(faits),
        "formes_identifiants": agreger_formes(
            _executer(conn, FORMES_NUMERO_DPE_SQL, params),
            _executer(conn, FORMES_CLES_BAN_SQL, params),
        ),
        "recuperation_adresse_brute": mesurer_recuperables(
            conn, departements, orphelins
        ),
        "id_rnb": agreger_rnb(_executer(conn, RNB_SQL, params), faits),
        "d109_plus_recent": agreger_d109(_executer(conn, D109_SQL, params)),
        "cad_parcelles": mesurer_cad_parcelles(
            conn,
            departements,
            {dep: v["adresses_avec_dpe"] for dep, v in par_cle.items()},
            d110,
            racine_archive,
        ),
    }


def mesurer_capture(
    conn: Any, capture: Sequence[Mapping[str, str]], departements_servis: Sequence[str]
) -> dict[str, Any]:
    numeros = sorted({ligne["numero"] for ligne in capture})
    date_import = _date_import(conn)
    trouves = _executer(conn, CAPTURE_DOCUMENTS_SQL, {"numeros": numeros})
    faits = _executer(
        conn,
        FAITS_DOCUMENTS_CAPTURE_SQL,
        {
            "numeros": numeros,
            "date_import": date_import,
            "date_mesure": datetime.now(UTC).date(),
        },
    )
    return agreger_capture(capture, trouves, faits, departements_servis)


# --- Capture file (local only, identifiers, never committed) -------------------


def extraire_numeros_capture(dossier: Path) -> list[dict[str, str]]:
    """Rows of the 300-document capture (``dpe-<insee>.json`` files only)."""
    lignes: dict[str, dict[str, str]] = {}
    for fichier in sorted(Path(dossier).iterdir()):
        if not _FICHIER_CAPTURE.match(fichier.name):
            continue
        contenu = json.loads(fichier.read_text(encoding="utf-8"))
        for ligne in contenu.get("results", []):
            numero = _texte(ligne.get("numero_dpe"))
            if not numero or numero in lignes:
                continue
            lignes[numero] = {
                "numero": numero,
                "code_insee": _texte(ligne.get("code_insee_ban")),
                "etiquette": _texte(ligne.get("etiquette_dpe")),
                "surface": _texte(ligne.get("surface_habitable_logement")),
                "date": _texte(ligne.get("date_etablissement_dpe"))[:10],
            }
    return list(lignes.values())


_CHAMPS_CAPTURE = ("numero", "code_insee", "etiquette", "surface", "date")


def ecrire_fichier_capture(
    lignes: Sequence[Mapping[str, str]], destination: Path
) -> None:
    racine = Path(__file__).resolve().parents[3]
    cible = Path(destination).resolve()
    if cible == racine or racine in cible.parents:
        raise GoldenSecurityError("capture file must stay outside the repository")
    texte = "".join(
        "\t".join(ligne[c] for c in _CHAMPS_CAPTURE) + "\n" for ligne in lignes
    )
    cible.write_text(texte, encoding="utf-8")


def lire_fichier_capture(chemin: Path) -> list[dict[str, str]]:
    lignes = []
    for brute in Path(chemin).read_text(encoding="utf-8").splitlines():
        if brute.strip():
            valeurs = (brute.split("\t") + [""] * len(_CHAMPS_CAPTURE))[
                : len(_CHAMPS_CAPTURE)
            ]
            lignes.append(dict(zip(_CHAMPS_CAPTURE, valeurs, strict=True)))
    return lignes


# --- Identity audit (plan 04-14): source -> selection -> archive -> storage ---
# Additive ``--identity-audit`` mode. Nothing above changes: the concordance
# rule (``normaliser_voie_sql``, ``_faits_sql``, ``concordance_adresse``) is
# reused as is (N-12); the D-110 usage variants are derived copies of the
# query, never a replacement of the decided rule (P-D110-NULL, 2026-10-02).
# Only the comparison of archived bytes with the generation imported from them
# establishes an import loss; a live schema is an inventory of availability.

IDENTITE_SCHEMA_VERSION = 1
CLES_RAPPORT_IDENTITE: tuple[str, ...] = (
    "schema_version",
    "sources",
    "generations",
    "fields",
    "lineage",
    "losses",
    "null_usage_variants",
    "limits",
    "commands",
)
REGIMES_SOURCES: dict[str, str] = {"dpe03existant": "existant", "dpe02neuf": "neuf"}
CHAMPS_SELECTIONNES: tuple[str, ...] = tuple(API_SELECT.split(","))
CHAMPS_POSITION: tuple[str, ...] = (
    "coordonnee_cartographique_x_ban",
    "coordonnee_cartographique_y_ban",
)
# Storage column of each requested field; None = requested and archived, not
# stored (pipeline.transform writes OUTPUT_COLUMNS only). Since plan 04-15 every
# requested field is stored: the archive-vs-base audit needs a base at Alembic
# 100_dpe_source_columns (the 04-14 run used the script of a60da0a).
STOCKAGE: dict[str, str | None] = {
    champ: "position" if champ in CHAMPS_POSITION else COLUMN_MAPPING.get(champ)
    for champ in CHAMPS_SELECTIONNES
}
FAMILLES_CHAMPS: dict[str, tuple[str, ...]] = {
    "numero_remplacement_dates": (
        "numero_dpe",
        "numero_dpe_remplace",
        "numero_dpe_immeuble_associe",
        "date_etablissement_dpe",
        "date_fin_validite_dpe",
        "date_derniere_modification_dpe",
        "date_reception_dpe",
        "date_visite_diagnostiqueur",
        "version_dpe",
    ),
    "ban_brut": (
        "adresse_brut",
        "adresse_complete_brut",
        "nom_commune_brut",
        "code_postal_brut",
    ),
    "ban_normalise": (
        "identifiant_ban",
        "adresse_ban",
        "numero_voie_ban",
        "nom_rue_ban",
        "nom_commune_ban",
        "code_postal_ban",
        "code_insee_ban",
        "code_departement_ban",
        "code_region_ban",
        "coordonnee_cartographique_x_ban",
        "coordonnee_cartographique_y_ban",
        "score_ban",
        "statut_geocodage",
    ),
    "rnb": ("id_rnb", "provenance_id_rnb"),
    "logement_immeuble": (
        "numero_rpls_logement",
        "numero_immatriculation_copropriete",
        "numero_etage_appartement",
        "position_logement_dans_immeuble",
        "complement_adresse_logement",
        "complement_adresse_batiment",
        "typologie_logement",
        "type_batiment",
    ),
}
# Name searches in the documented schemas only (no private access): a match
# is a lead to qualify, never a field to import automatically.
RECHERCHES_SCHEMA: dict[str, re.Pattern[str]] = {
    "parcelle": re.compile(r"parcel|cadastr"),
    "fiscal": re.compile(r"fiscal|invar"),
    "logement_identifiant": re.compile(r"rpls|copropriete|immeuble_associe"),
    "valeurs_par_m2": re.compile(r"_par_m2"),
}
# Storage casts of pipeline.swap(): kind, decimals, bound (decimal) or length.
NATURES_STOCKAGE: dict[str, tuple[str, int | None, Decimal | int | None]] = {
    "surface_habitable_logement": ("decimal", 2, Decimal("999999.99")),
    "conso_5_usages_ep": ("decimal", 2, Decimal("99999999.99")),
    "emission_ges_5_usages": ("decimal", 2, Decimal("99999999.99")),
    "cout_total_5_usages": ("decimal", 2, Decimal("9999999999.99")),
    "score_ban": ("decimal", None, None),
    # Plan 04-15 (C2): exact numeric, neither bounded nor rounded.
    "conso_5_usages_par_m2_ep": ("decimal", None, None),
    "emission_ges_5_usages_par_m2": ("decimal", None, None),
    "annee_construction": ("entier", None, None),
    "numero_etage_appartement": ("entier", None, None),
    "date_etablissement_dpe": ("date", None, None),
    "date_fin_validite_dpe": ("date", None, None),
    "etiquette_dpe": ("texte", None, 1),
    "etiquette_ges": ("texte", None, 1),
    "code_postal_ban": ("texte", None, 5),
    "code_insee_ban": ("texte", None, 5),
    "identifiant_ban": ("texte", None, 50),
    "id_rnb": ("texte", None, 32),
}
ISSUES_DATE: dict[str, str] = {
    "date_etablissement_dpe": "invalid_establishment_date",
    "date_fin_validite_dpe": "invalid_end_date",
}
CLASSES_VALEUR: tuple[str, ...] = (
    "identique",
    "vides",
    "presence_conforme",
    "arrondi",
    "borne",
    "tronque",
    "date_invalide_videe",
    "fin_de_ligne_normalisee",
    "perte",
    "ajout",
    "difference",
)
# Classes where the stored value differs from the archived bytes.
CLASSES_ECART: tuple[str, ...] = CLASSES_VALEUR[3:]
CORRECTIONS_MINIMALES: dict[str, str] = {
    "champ_archive_non_stocke": (
        "conserver la valeur source dans une colonne dediee (04-15), "
        "API_SELECT inchange"
    ),
    "arrondi": "conserver le brut ou elargir le type (04-15, sur decision)",
    "borne": "conserver le brut ou elargir le type (04-15, sur decision)",
    "tronque": "conserver le brut ou elargir le type (04-15, sur decision)",
    "date_invalide_videe": "aucune : defaut documente dans date_issues",
    "fin_de_ligne_normalisee": "lire l'archive avec newline='' (04-15)",
    "perte": "corriger la transformation (04-15), rejouer les memes octets",
    "ajout": "corriger la transformation (04-15), rejouer les memes octets",
    "difference": "corriger la transformation (04-15), rejouer les memes octets",
    "lignes_absentes_de_la_base": "diagnostiquer avant toute correction",
    "lignes_en_base_hors_archive": "diagnostiquer avant toute correction",
    "doublon_source_identique": "aucune : fusion exacte attendue",
    "doublon_source_different": "constat source, aucune valeur choisie",
    "rnb_correle_ban": (
        "aucune : ne pas compter Reprise RNB comme preuve independante"
    ),
    "cadastre_perte_import": "diagnostiquer l'import cadastre avant correction",
    "cadastre_millesime_ou_couverture": (
        "aucune sans decision P-PARCELLE : ecart de millesime ou de couverture"
    ),
    "panne": "relancer la mesure ; aucune valeur deduite",
}
_SOURCE_CSV_RE = re.compile(
    r"^(?P<import_id>[0-9a-f]{32})-(?P<slug>dpe[0-9a-z]+)-(?P<dep>[0-9]{2,3})\.csv$"
)
_CADASTRE_LIGNE_RE = re.compile(
    r'"properties":\{"id":"(?P<id>[^"]+)","commune":"(?P<commune>[^"]+)"'
)
# Wider guards for this report: parcel ids and RNB building ids too.
_FUITE_PARCELLE = re.compile(
    r"(?<![0-9A-Za-z])[0-9]{2}[0-9AB][0-9]{5}[0-9A-Z]{2}[0-9]{4}(?![0-9A-Za-z])"
)
_FUITE_RNB = re.compile(
    r"(?<![0-9A-Za-z])(?=[0-9A-Z]*[0-9])[0-9A-Z]{12}(?![0-9A-Za-z])"
)

ETL_RUNS_SQL = """
SELECT r.dataset_name,
       r.id,
       r.status,
       r.started_at,
       r.completed_at,
       r.total_rows_loaded
FROM etl_runs r
WHERE r.dataset_name = ANY(%(datasets)s)
ORDER BY r.id
"""
GENERATIONS_DPE_SQL = """
SELECT d.code_departement,
       COALESCE(d.regime_dpe, 'inconnu') AS regime,
       o.value ->> 'source_slug' AS source_slug,
       o.value ->> 'import_id' AS import_id,
       o.value ->> 'source_version' AS source_version,
       o.value ->> 'observed_at' AS observed_at,
       COUNT(*) AS effectif,
       COUNT(DISTINCT d.numero_dpe) AS documents
FROM diagnostics_dpe d
LEFT JOIN LATERAL jsonb_array_elements(
    CASE
        WHEN jsonb_typeof(d.source_observations) = 'array'
            THEN d.source_observations
        ELSE '[]'::jsonb
    END
) o ON true
WHERE d.code_departement = ANY(%(departements)s)
GROUP BY 1, 2, 3, 4, 5, 6
ORDER BY 1, 2, 3, 4, 5, 6
"""
# Stored projection of one department (identifiers stay in memory).
LIGNES_DPE_DEPARTEMENT_SQL = """
SELECT d.numero_dpe,
       d.code_departement,
       d.date_etablissement_dpe,
       d.annee_construction,
       d.periode_construction,
       d.type_batiment,
       d.surface_habitable,
       d.etiquette_dpe,
       d.etiquette_ges,
       d.type_installation_chauffage,
       d.type_energie_principale_chauffage,
       d.conso_5_usages_ep,
       d.emission_ges_5_usages,
       d.cout_total_5_usages,
       d.qualite_isolation_enveloppe,
       d.code_postal,
       d.code_insee,
       d.nom_commune,
       d.numero_etage_appartement,
       d.complement_adresse_logement,
       d.identifiant_ban,
       d.numero_dpe_remplace,
       d.date_fin_validite_dpe,
       d.id_rnb,
       d.provenance_id_rnb,
       d.score_ban,
       d.adresse_brut,
       d.adresse_complete_brut,
       d.nom_commune_brut,
       d.code_postal_brut,
       d.adresse_ban,
       d.numero_voie_ban,
       d.nom_rue_ban,
       d.statut_geocodage,
       d.version_dpe,
       d.date_derniere_modification_dpe,
       d.date_reception_dpe,
       d.date_visite_diagnostiqueur,
       d.conso_5_usages_par_m2_ep,
       d.emission_ges_5_usages_par_m2,
       d.numero_dpe_immeuble_associe,
       d.numero_immatriculation_copropriete,
       d.position IS NOT NULL AS a_position,
       d.lifecycle_facts -> 'date_issues' AS date_issues
FROM diagnostics_dpe d
WHERE d.code_departement = %(departement)s
"""
RNB_LIGNEE_SQL = """
SELECT d.code_departement,
       CASE
           WHEN d.type_batiment IN ('maison', 'appartement', 'immeuble')
               THEN d.type_batiment
           ELSE 'autre'
       END AS type_batiment,
       d.provenance_id_rnb AS provenance,
       NULLIF(btrim(d.id_rnb), '') IS NOT NULL AS id_rnb_renseigne,
       EXISTS (
           SELECT 1
           FROM adresses a
           WHERE a.ban_id = d.identifiant_ban
             AND a.code_departement = d.code_departement
       ) AS cle_jointe,
       EXISTS (
           SELECT 1
           FROM rnb_liens l
           WHERE l.cle_ban = d.identifiant_ban
       ) AS cle_dans_rnb_liens,
       EXISTS (
           SELECT 1
           FROM rnb_liens l
           WHERE l.cle_ban = d.identifiant_ban
             AND l.id_rnb = d.id_rnb
       ) AS id_rnb_lie_a_la_cle,
       EXISTS (
           SELECT 1
           FROM rnb_liens l
           WHERE l.id_rnb = d.id_rnb
       ) AS id_rnb_dans_rnb_liens,
       COUNT(*) AS effectif
FROM diagnostics_dpe d
WHERE d.code_departement = ANY(%(departements)s)
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
ORDER BY 1, 2, 3, 4, 5, 6, 7, 8
"""
USAGES_BATIMENTS_SQL = """
SELECT bb.code_departement,
       CASE
           WHEN bb.usage_1 IS NULL THEN '(null)'
           WHEN btrim(bb.usage_1) = '' THEN '(vide)'
           ELSE bb.usage_1
       END AS usage,
       COUNT(*) AS effectif
FROM batiments bb
WHERE bb.code_departement = ANY(%(departements)s)
GROUP BY 1, 2
ORDER BY 1, 2
"""
# One row per distinct BAN key of the department: point in a parcel (block D
# rule, ST_Intersects), and the RNB links of the key (built buildings).
ADRESSES_PARCELLE_RNB_SQL = """
WITH adr AS (
    SELECT DISTINCT ON (a.ban_id)
           a.code_departement,
           a.ban_id,
           a.code_commune,
           a.position
    FROM adresses a
    WHERE a.code_departement = %(departement)s
      AND NULLIF(btrim(a.ban_id), '') IS NOT NULL
    ORDER BY a.ban_id, a.id
)
SELECT adr.ban_id,
       adr.code_commune,
       adr.position IS NOT NULL AS a_position,
       p.id_parcelle IS NOT NULL AS point_dans_parcelle,
       r.n_batiments_rnb,
       r.n_parcelles_rnb,
       r.n_parcelles_rnb_locales,
       r.point_dans_parcelles_rnb
FROM adr
LEFT JOIN LATERAL (
    SELECT pp.id_parcelle
    FROM parcelles pp
    WHERE pp.code_departement = adr.code_departement
      AND ST_Intersects(pp.geometrie, adr.position)
    ORDER BY pp.id_parcelle
    LIMIT 1
) p ON adr.position IS NOT NULL
CROSS JOIN LATERAL (
    SELECT COUNT(DISTINCT l.id_rnb) AS n_batiments_rnb,
           COUNT(DISTINCT x.parcelle) AS n_parcelles_rnb,
           COUNT(DISTINCT x.parcelle) FILTER (
               WHERE EXISTS (
                   SELECT 1
                   FROM parcelles q
                   WHERE q.code_departement = adr.code_departement
                     AND q.id_parcelle = x.parcelle
               )
           ) AS n_parcelles_rnb_locales,
           COALESCE(bool_or(x.parcelle = p.id_parcelle), false)
               AS point_dans_parcelles_rnb
    FROM rnb_liens l
    LEFT JOIN LATERAL unnest(l.parcelles) AS x(parcelle) ON true
    WHERE l.cle_ban = adr.ban_id
      AND l.statut = 'constructed'
) r
"""
PARCELLES_DEPARTEMENT_SQL = """
SELECT pp.id_parcelle,
       pp.commune
FROM parcelles pp
WHERE pp.code_departement = %(departement)s
"""
MILLESIMES_SQL = """
SELECT 'parcelles' AS origine,
       pp.code_departement,
       MIN(pp.updated) AS date_min,
       MAX(pp.updated) AS date_max,
       COUNT(*) AS effectif
FROM parcelles pp
WHERE pp.code_departement = ANY(%(departements)s)
GROUP BY 1, 2
UNION ALL
SELECT 'rnb_liens' AS origine,
       l.code_departement,
       MIN(l.millesime) AS date_min,
       MAX(l.millesime) AS date_max,
       COUNT(*) AS effectif
FROM rnb_liens l
WHERE l.code_departement = ANY(%(departements)s)
GROUP BY 1, 2
ORDER BY 1, 2
"""
_HORS_ANNEXE_RE = re.compile(
    r"COUNT\(\*\) FILTER \(\s*WHERE bb\.usage_1 IS DISTINCT FROM 'Annexe'\s*\)"
    r" AS n_hors_annexe"
)


def variante_usage_sql(expression: str) -> str:
    """The base facts query with only its 'hors annexes' count replaced."""
    sql, remplacements = _HORS_ANNEXE_RE.subn(
        lambda _: expression, FAITS_DOCUMENTS_BASE_SQL
    )
    if remplacements != 1:
        raise ValueError("D-110 usage count not found exactly once")
    return sql


# P-D110-NULL is decided (count every building except an explicit Annexe);
# these variants only measure the delta, the decided rule stays the served one.
VARIANTES_USAGE: dict[str, str] = {
    "usage_null_exclu_du_compte": variante_usage_sql(
        "COUNT(*) FILTER (WHERE bb.usage_1 <> 'Annexe') AS n_hors_annexe"
    ),
    "aucun_constat_si_usage_null": variante_usage_sql(
        "CASE WHEN bool_or(bb.usage_1 IS NULL) THEN NULL"
        " ELSE COUNT(*) FILTER (WHERE bb.usage_1 <> 'Annexe') END AS n_hors_annexe"
    ),
    "aucun_constat_si_usage_null_ou_indifferencie": variante_usage_sql(
        "CASE WHEN bool_or(bb.usage_1 IS NULL OR bb.usage_1 = 'Indifférencié')"
        " THEN NULL ELSE COUNT(*) FILTER ("
        "WHERE bb.usage_1 NOT IN ('Annexe', 'Indifférencié')) END AS n_hors_annexe"
    ),
}

ALLOWED_SQL_IDENTITE: frozenset[str] = frozenset(
    {
        ETL_RUNS_SQL,
        GENERATIONS_DPE_SQL,
        LIGNES_DPE_DEPARTEMENT_SQL,
        RNB_LIGNEE_SQL,
        USAGES_BATIMENTS_SQL,
        ADRESSES_PARCELLE_RNB_SQL,
        PARCELLES_DEPARTEMENT_SQL,
        MILLESIMES_SQL,
        *VARIANTES_USAGE.values(),
    }
)


def _executer_identite(
    conn: Any, sql: str, params: Mapping[str, Any]
) -> list[dict[str, Any]]:
    assert_select_only(sql, ALLOWED_SQL | ALLOWED_SQL_IDENTITE)
    return list(conn.execute(sql, dict(params)).fetchall())


def _mesure(
    population: str, numerateur: int, denominateur: int, origine: str
) -> dict[str, Any]:
    """A rate always names its population, numerator, denominator and origin."""
    return {
        "population": population,
        "numerateur": numerateur,
        "denominateur": denominateur,
        "part": _part(numerateur, denominateur),
        "etat": "mesure",
        "origine": origine,
    }


def _non_mesure(raison: str, origine: str) -> dict[str, Any]:
    return {"etat": "non_mesure", "raison": raison, "origine": origine}


# --- Pure classification -----------------------------------------------------


def _brut(valeur: Any) -> str | None:
    if valeur is None:
        return None
    texte = str(valeur).strip()
    return texte or None


def position_valide(x: Any, y: Any) -> bool:
    """Same predicate as ``DpePipeline._invalid_geometry`` (negated)."""
    try:
        fx, fy = float(x), float(y)
    except (TypeError, ValueError):
        return False
    return math.isfinite(fx) and math.isfinite(fy) and not (fx == fy == 0)


def classer_position(x: Any, y: Any, a_position: bool) -> str:
    attendue = position_valide(x, y)
    if attendue == bool(a_position):
        return "presence_conforme" if attendue else "vides"
    return "perte" if attendue else "ajout"


def classer_valeur(  # noqa: C901
    champ: str, brut: Any, stocke: Any, date_issues: Collection[str] = ()
) -> str:
    """Archived value against the stored one, through the swap() casts."""
    nature, decimales, limite = NATURES_STOCKAGE.get(champ, ("texte", None, None))
    valeur = _brut(brut)
    if valeur is None:
        return "vides" if stocke is None else "ajout"
    if stocke is None:
        if ISSUES_DATE.get(champ) in date_issues:
            return "date_invalide_videe"
        return "perte"
    if nature == "decimal":
        try:
            nombre = Decimal(valeur)
        except InvalidOperation:
            return "difference"
        enregistre = Decimal(str(stocke))
        if enregistre == nombre:
            return "identique"
        borne = nombre if limite is None else min(nombre, Decimal(limite))
        attendu = (
            borne
            if decimales is None
            else borne.quantize(Decimal(1).scaleb(-decimales), rounding=ROUND_HALF_UP)
        )
        if enregistre != attendu:
            return "difference"
        return "borne" if borne != nombre else "arrondi"
    if nature == "entier":
        try:
            nombre = Decimal(valeur)
        except InvalidOperation:
            return "difference"
        return "identique" if nombre == Decimal(int(stocke)) else "difference"
    enregistre_texte = str(stocke)
    if enregistre_texte == valeur:
        return "identique"
    if isinstance(limite, int) and enregistre_texte == valeur[:limite]:
        return "tronque"
    if enregistre_texte == valeur.replace("\r\n", "\n").replace("\r", "\n"):
        return "fin_de_ligne_normalisee"
    return "difference"


def _empreinte_ligne(ligne: Mapping[str, Any], colonnes: Sequence[str]) -> bytes:
    texte = json.dumps([ligne.get(c) for c in colonnes], ensure_ascii=False)
    return hashlib.blake2b(texte.encode("utf-8"), digest_size=16).digest()


def comparer_generation(  # noqa: C901
    fichiers: Mapping[str, Iterable[Mapping[str, Any]]],
    base: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Archived rows of one department (by source) against its stored rows.

    ``base`` maps a DPE number to its stored projection
    (``LIGNES_DPE_DEPARTEMENT_SQL``); identifiers stay in memory. The first
    occurrence of a number is compared; repeats are counted as source
    multiplicity, identical or not.
    """
    vus: dict[str, bytes] = {}
    retrouves: set[str] = set()
    doublons: Counter[str] = Counter()
    absents = 0
    par_source: dict[str, Any] = {}
    for slug, lignes in fichiers.items():
        champs: dict[str, Counter[str]] = defaultdict(Counter)
        renseigne: Counter[str] = Counter()
        n_lignes = 0
        repetees = 0
        # A DictReader names its columns even when the file has no row.
        colonnes = list(getattr(lignes, "fieldnames", None) or [])
        for ligne in lignes:
            if not colonnes:
                colonnes = list(ligne.keys())
            n_lignes += 1
            numero = _texte(ligne.get("numero_dpe"))
            empreinte = _empreinte_ligne(ligne, colonnes)
            if numero in vus:
                identique = vus[numero] == empreinte
                doublons["identiques" if identique else "differents"] += 1
                repetees += 1
                continue
            vus[numero] = empreinte
            for champ in CHAMPS_SELECTIONNES:
                if _brut(ligne.get(champ)) is not None:
                    renseigne[champ] += 1
            stockee = base.get(numero)
            if stockee is None:
                absents += 1
                continue
            retrouves.add(numero)
            issues = stockee.get("date_issues") or ()
            for champ, colonne in STOCKAGE.items():
                if colonne is None or champ not in ligne:
                    continue
                if colonne == "position":
                    classe = classer_position(
                        ligne.get(CHAMPS_POSITION[0]),
                        ligne.get(CHAMPS_POSITION[1]),
                        stockee.get("a_position"),
                    )
                else:
                    classe = classer_valeur(
                        champ, ligne.get(champ), stockee.get(colonne), issues
                    )
                champs[champ][classe] += 1
        par_source[slug] = {
            "lignes": n_lignes,
            "premieres_occurrences": n_lignes - repetees,
            "colonnes_manquantes": [
                c for c in CHAMPS_SELECTIONNES if c not in colonnes
            ],
            "colonnes_hors_selection": sorted(set(colonnes) - set(CHAMPS_SELECTIONNES)),
            "renseigne": {c: renseigne[c] for c in CHAMPS_SELECTIONNES},
            "champs": {
                champ: {k: compte[k] for k in CLASSES_VALEUR if compte[k]}
                for champ, compte in sorted(champs.items())
            },
        }
    return {
        "par_source": par_source,
        "numeros_distincts_archive": len(vus),
        "doublons": {
            "identiques": doublons["identiques"],
            "differents": doublons["differents"],
        },
        "retrouves_en_base": len(retrouves),
        "absents_de_la_base": absents,
        "documents_en_base": len(base),
        "en_base_hors_archive": sum(1 for n in base if n not in vus),
    }


def agreger_rnb_lignee(lignes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """RNB lineage by exact provenance: is the document's RNB id the one the
    RNB links to its own BAN key? Agreement is correlation, never proof."""
    blocs: dict[str, dict[str, Counter[str]]] = defaultdict(
        lambda: defaultdict(Counter)
    )
    for ligne in lignes:
        dep, n = ligne["code_departement"], _n(ligne)
        compte = blocs[dep][_libelle_provenance(ligne["provenance"])]
        compte["documents"] += n
        if not ligne["id_rnb_renseigne"]:
            continue
        compte["id_rnb_renseigne"] += n
        compte["cle_jointe"] += n if ligne["cle_jointe"] else 0
        compte["id_rnb_dans_rnb_liens"] += n if ligne["id_rnb_dans_rnb_liens"] else 0
        if ligne["cle_dans_rnb_liens"]:
            compte["cle_dans_rnb_liens"] += n
            compte["id_rnb_lie_a_la_cle"] += n if ligne["id_rnb_lie_a_la_cle"] else 0
    origine = "diagnostics_dpe x rnb_liens (base de travail)"
    resultat: dict[str, Any] = {}
    for dep, par_provenance in sorted(blocs.items()):
        resultat[dep] = {
            provenance: {
                "documents": c["documents"],
                "id_rnb_renseigne": _mesure(
                    f"documents {dep}, provenance {provenance}",
                    c["id_rnb_renseigne"],
                    c["documents"],
                    origine,
                ),
                "id_rnb_connu_de_rnb_liens": _mesure(
                    f"documents {dep} a id_rnb, provenance {provenance}",
                    c["id_rnb_dans_rnb_liens"],
                    c["id_rnb_renseigne"],
                    origine,
                ),
                "id_rnb_lie_a_la_cle_ban": _mesure(
                    f"documents {dep} a id_rnb dont la cle BAN est dans rnb_liens,"
                    f" provenance {provenance}",
                    c["id_rnb_lie_a_la_cle"],
                    c["cle_dans_rnb_liens"],
                    origine,
                ),
            }
            for provenance, c in sorted(par_provenance.items())
        }
    return {
        "lecture": (
            "accord id_rnb du document / lien RNB de sa cle BAN : correlation "
            "attendue si l'id derive du geocodage BAN, jamais une preuve "
            "independante de l'adresse ni du logement"
        ),
        "departements": resultat,
    }


def lire_cadastre_source(lignes: Iterable[str]) -> dict[str, Any]:
    """Parcel ids and communes of an archived Etalab cadastre GeoJSON, one
    feature per line (memory only)."""
    ids: set[str] = set()
    communes: set[str] = set()
    non_reconnues = 0
    features = 0
    for ligne in lignes:
        if '"type":"Feature"' not in ligne:
            continue
        features += 1
        trouve = _CADASTRE_LIGNE_RE.search(ligne)
        if trouve is None:
            non_reconnues += 1
            continue
        ids.add(trouve["id"])
        communes.add(trouve["commune"])
    return {
        "ids": ids,
        "communes": communes,
        "features": features,
        "non_reconnues": non_reconnues,
    }


CAUSES_HORS_PARCELLE: tuple[str, ...] = (
    "absente_du_csv_ban",
    "commune_absente_du_cadastre_local",
    "cad_parcelles_vide",
    "parcelle_citee_absente_du_cadastre_local",
    "parcelle_citee_presente_point_hors_geometrie",
)


def _classe_multiplicite(n: int) -> str:
    return "0" if n == 0 else "1" if n == 1 else "2-5" if n <= 5 else ">5"


def agreger_adresses_parcelle(  # noqa: C901
    dep: str,
    adresses: Iterable[Mapping[str, Any]],
    ban: Mapping[str, tuple[list[str], str]],
    parcelles_locales: Collection[str],
    communes_locales: Collection[str],
    parcelles_source: Collection[str] | None,
    communes_source: Collection[str] | None,
) -> dict[str, Any]:
    """BAN points outside every local parcel, by type_position, and the
    explanation our cadastre gives (closed cascade); then the address ->
    building -> parcel path of rnb_liens. Counts only."""
    c: Counter[str] = Counter()
    par_type: dict[str, Counter[str]] = defaultdict(Counter)
    causes: Counter[str] = Counter()
    causes_parcelle: Counter[str] = Counter()
    source_cause: Counter[str] = Counter()
    rnb_hors: Counter[str] = Counter()
    multiplicite: dict[str, Counter[str]] = defaultdict(Counter)
    accord: Counter[str] = Counter()
    for ligne in adresses:
        cle = _texte(ligne["ban_id"])
        c["adresses"] += 1
        if not ligne["a_position"]:
            c["sans_position"] += 1
            continue
        c["avec_position"] += 1
        entree = ban.get(cle)
        position = entree[1] if entree else "(absente du csv)"
        par_type[position]["adresses"] += 1
        n_rnb = int(ligne["n_batiments_rnb"] or 0)
        n_parcelles_rnb = int(ligne["n_parcelles_rnb"] or 0)
        dans = bool(ligne["point_dans_parcelle"])
        groupe = "point_dans_parcelle" if dans else "point_hors_parcelle"
        multiplicite[groupe][_classe_multiplicite(n_rnb)] += 1
        if dans:
            c["avec_rnb_point_dans_parcelle"] += n_rnb > 0
            if n_parcelles_rnb:
                accord["cles_point_et_rnb"] += 1
                inclus = bool(ligne["point_dans_parcelles_rnb"])
                if n_parcelles_rnb == 1:
                    accord[
                        "une_parcelle_rnb_egale"
                        if inclus
                        else "une_parcelle_rnb_differente"
                    ] += 1
                else:
                    accord[
                        "plusieurs_parcelles_rnb_point_inclus"
                        if inclus
                        else "plusieurs_parcelles_rnb_point_exclu"
                    ] += 1
            continue
        c["hors_parcelle"] += 1
        par_type[position]["hors_parcelle"] += 1
        rnb_hors["avec_lien_rnb"] += n_rnb > 0
        rnb_hors["avec_parcelle_rnb"] += n_parcelles_rnb > 0
        rnb_hors["avec_parcelle_rnb_locale"] += (
            int(ligne["n_parcelles_rnb_locales"] or 0) > 0
        )
        cad = entree[0] if entree else []
        if entree is None:
            cause = "absente_du_csv_ban"
        elif _texte(ligne["code_commune"]) not in communes_locales:
            cause = "commune_absente_du_cadastre_local"
            if communes_source is not None:
                source_cause[
                    "commune_dans_la_source_archivee"
                    if _texte(ligne["code_commune"]) in communes_source
                    else "commune_absente_de_la_source_archivee"
                ] += 1
        elif not cad:
            cause = "cad_parcelles_vide"
        elif not any(p in parcelles_locales for p in cad):
            cause = "parcelle_citee_absente_du_cadastre_local"
            if parcelles_source is not None:
                source_cause[
                    "parcelle_citee_dans_la_source_archivee"
                    if any(p in parcelles_source for p in cad)
                    else "parcelle_citee_absente_de_la_source_archivee"
                ] += 1
        else:
            cause = "parcelle_citee_presente_point_hors_geometrie"
        causes[cause] += 1
        if position == "parcelle":
            causes_parcelle[cause] += 1
    hors = c["hors_parcelle"]
    origine = "adresses x parcelles x CSV BAN archive x rnb_liens"
    expliquee = (
        causes["commune_absente_du_cadastre_local"]
        + causes["parcelle_citee_absente_du_cadastre_local"]
    )
    return {
        "adresses_distinctes": c["adresses"],
        "sans_position": c["sans_position"],
        "point_hors_parcelle": _mesure(
            f"adresses {dep} avec point", hors, c["avec_position"], origine
        ),
        "par_type_position": {
            position: {
                "adresses": compte["adresses"],
                "hors_parcelle": compte["hors_parcelle"],
                "part": _part(compte["hors_parcelle"], compte["adresses"]),
            }
            for position, compte in sorted(
                par_type.items(), key=lambda x: -x[1]["adresses"]
            )
        },
        "hors_parcelle_par_cause": {k: causes[k] for k in CAUSES_HORS_PARCELLE},
        "hors_parcelle_type_parcelle_par_cause": {
            k: causes_parcelle[k] for k in CAUSES_HORS_PARCELLE
        },
        "controle_partition": sum(causes.values()) == hors,
        "part_expliquee_par_le_cadastre_local": _mesure(
            f"adresses {dep} au point hors parcelle",
            expliquee,
            hors,
            origine,
        ),
        "source_archivee_pour_les_causes_cadastre": (
            dict(sorted(source_cause.items()))
            if parcelles_source is not None
            else _non_mesure("archive cadastre non lue", "archive des sources")
        ),
        "rnb_liens": {
            "hors_parcelle_avec_lien": _mesure(
                f"adresses {dep} au point hors parcelle",
                rnb_hors["avec_lien_rnb"],
                hors,
                origine,
            ),
            "hors_parcelle_avec_parcelle_rnb": _mesure(
                f"adresses {dep} au point hors parcelle",
                rnb_hors["avec_parcelle_rnb"],
                hors,
                origine,
            ),
            "hors_parcelle_avec_parcelle_rnb_locale": _mesure(
                f"adresses {dep} au point hors parcelle",
                rnb_hors["avec_parcelle_rnb_locale"],
                hors,
                origine,
            ),
            "point_dans_parcelle_avec_lien": _mesure(
                f"adresses {dep} au point dans une parcelle",
                c["avec_rnb_point_dans_parcelle"],
                c["avec_position"] - hors,
                origine,
            ),
            "multiplicite_batiments_rnb": {
                groupe: {k: compte[k] for k in ("0", "1", "2-5", ">5")}
                for groupe, compte in sorted(multiplicite.items())
            },
            "accord_avec_la_parcelle_du_point": {
                **{
                    k: accord[k]
                    for k in (
                        "cles_point_et_rnb",
                        "une_parcelle_rnb_egale",
                        "une_parcelle_rnb_differente",
                        "plusieurs_parcelles_rnb_point_inclus",
                        "plusieurs_parcelles_rnb_point_exclu",
                    )
                },
                "accord_parcelle_unique": _mesure(
                    f"adresses {dep} au point dans une parcelle et a une seule"
                    " parcelle RNB",
                    accord["une_parcelle_rnb_egale"],
                    accord["une_parcelle_rnb_egale"]
                    + accord["une_parcelle_rnb_differente"],
                    origine,
                ),
                "point_dans_les_parcelles_rnb": _mesure(
                    f"adresses {dep} au point dans une parcelle et a parcelle RNB",
                    accord["une_parcelle_rnb_egale"]
                    + accord["plusieurs_parcelles_rnb_point_inclus"],
                    accord["cles_point_et_rnb"],
                    origine,
                ),
            },
        },
    }


def comparer_cadastre(
    locales: Collection[str], source: Collection[str]
) -> dict[str, int]:
    return {
        "parcelles_source_archivee": len(source),
        "parcelles_en_base": len(locales),
        "source_absentes_de_la_base": sum(1 for p in source if p not in locales),
        "base_absentes_de_la_source": sum(1 for p in locales if p not in source),
    }


# --- Database and archive measurements -----------------------------------------


def _runs(conn: Any) -> list[dict[str, Any]]:
    return [
        {
            "dataset": ligne["dataset_name"],
            "id": int(ligne["id"]),
            "statut": ligne["status"],
            "debut": ligne["started_at"],
            "fin": ligne["completed_at"],
            "lignes_chargees": ligne["total_rows_loaded"],
        }
        for ligne in _executer_identite(
            conn,
            ETL_RUNS_SQL,
            {"datasets": ["dpe", "ban", "cadastre", "batiments", "rnb_liens"]},
        )
    ]


def _replays(racine: Path, dataset: str) -> tuple[list[Any], list[dict[str, Any]]]:
    from app.etl.source_archive import SourceArchiveError, load_replay

    lus, refus = [], []
    for record in sorted((Path(racine) / dataset / "runs").glob("*.json")):
        try:
            lus.append(load_replay(racine, dataset, record.stem))
        except (SourceArchiveError, OSError) as erreur:
            refus.append(
                {
                    "run_id": record.stem,
                    "raison": getattr(erreur, "reason", type(erreur).__name__),
                }
            )
    return lus, refus


def _fichiers_dpe(replay: Any) -> dict[tuple[str, str], dict[str, Any]]:
    fichiers = {}
    for entree in replay.files:
        trouve = _SOURCE_CSV_RE.match(entree["name"])
        if trouve:
            fichiers[(trouve["slug"], trouve["dep"])] = {
                **entree,
                "import_id": trouve["import_id"],
            }
    return fichiers


def mesurer_lignee_archive(  # noqa: C901
    conn: Any,
    departements: Sequence[str],
    racine: Path | None,
    import_ids: Collection[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(archives, archive_vs_base): every DPE archive record verified by
    ``load_replay``; the one holding the imported generation is compared row
    by row with ``diagnostics_dpe``."""
    if racine is None:
        absent = _non_mesure("SOURCE_ARCHIVE_DIR absent", "archive des sources")
        return {"statut": "non_mesure", "raison": absent["raison"]}, absent
    replays, refus = _replays(racine, "dpe")
    archives = []
    generation = None
    for replay in replays:
        fichiers = _fichiers_dpe(replay)
        ids = {f["import_id"] for f in fichiers.values()}
        archives.append(
            {
                "run_id": replay.run_id,
                "origine": replay.record.get("origin"),
                "deployment_sha": replay.record.get("deployment_sha"),
                "archive_le": replay.record.get("archived_at"),
                "record_sha256": replay.record_sha256,
                "import_ids": sorted(ids),
                "fichiers": {
                    f"{slug}-{dep}": {"octets": f["bytes"], "sha256": f["sha256"]}
                    for (slug, dep), f in sorted(fichiers.items())
                },
                "contient_un_manifeste": any(
                    f["name"] == "manifest.json" for f in replay.files
                ),
            }
        )
        if ids and ids <= set(import_ids):
            generation = (replay, fichiers)
    evolution = []
    for avant, apres in itertools.pairwise(archives):
        communs = set(avant["fichiers"]) & set(apres["fichiers"])
        identiques = sum(
            avant["fichiers"][k]["sha256"] == apres["fichiers"][k]["sha256"]
            for k in communs
        )
        evolution.append(
            {
                "de": avant["run_id"],
                "vers": apres["run_id"],
                "fichiers_communs": len(communs),
                "fichiers_identiques": identiques,
                "lecture": (
                    "octets identiques"
                    if identiques == len(communs)
                    else "evolution de la source possible, pas une perte d'import"
                ),
            }
        )
    bloc_archives = {
        "statut": "mesure",
        "records": archives,
        "records_refuses": refus,
        "evolution_entre_records": evolution,
    }
    if generation is None:
        return bloc_archives, _non_mesure(
            "aucune archive ne contient la generation importee", "archive des sources"
        )
    replay, fichiers = generation
    resultat: dict[str, Any] = {}
    for dep in departements:
        base = {
            ligne["numero_dpe"]: ligne
            for ligne in _executer_identite(
                conn, LIGNES_DPE_DEPARTEMENT_SQL, {"departement": dep}
            )
        }
        flux = {}
        manquants = []
        for slug in REGIMES_SOURCES:
            entree = fichiers.get((slug, dep))
            if entree is None:
                manquants.append(slug)
                continue
            flux[slug] = Path(racine) / "dpe" / entree["object"]
        if manquants:
            resultat[dep] = _non_mesure(
                f"fichier absent du record: {','.join(manquants)}",
                "archive des sources",
            )
            continue
        ouverts = {
            slug: chemin.open(encoding="utf-8-sig", newline="")
            for slug, chemin in flux.items()
        }
        try:
            resultat[dep] = comparer_generation(
                {slug: csv.DictReader(f) for slug, f in ouverts.items()}, base
            )
        finally:
            for f in ouverts.values():
                f.close()
        del base
    return bloc_archives, {
        "statut": "mesure",
        "run_archive": replay.run_id,
        "record_sha256": replay.record_sha256,
        "lecture": (
            "octets archives de la generation importee contre diagnostics_dpe ;"
            " casts de pipeline.swap() appliques avant comparaison"
        ),
        "departements": resultat,
    }


def mesurer_variantes_usage(
    conn: Any,
    departements: Sequence[str],
    date_import: date | None,
    date_mesure: date,
    d110: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    params = {
        "departements": list(departements),
        "date_import": date_import,
        "date_mesure": date_mesure,
    }
    usages: dict[str, dict[str, int]] = defaultdict(dict)
    for ligne in _executer_identite(
        conn, USAGES_BATIMENTS_SQL, {"departements": list(departements)}
    ):
        usages[ligne["code_departement"]][ligne["usage"]] = int(ligne["effectif"])
    variantes = {
        nom: agreger_maisons_d110(_executer_identite(conn, sql, params))
        for nom, sql in VARIANTES_USAGE.items()
    }
    champs = ("maisons_consultables", "iii_cumul_hors_annexes", "iv_cumul_hors_annexes")
    resultat = {}
    for dep in departements:
        decide = d110.get(dep, {})
        resultat[dep] = {
            "regle_decidee": {k: decide.get(k) for k in champs},
            "variantes": {
                nom: {
                    **{k: v.get(dep, {}).get(k) for k in champs},
                    "delta_iii": (v.get(dep, {}).get(champs[1]) or 0)
                    - (decide.get(champs[1]) or 0),
                    "delta_iv": (v.get(dep, {}).get(champs[2]) or 0)
                    - (decide.get(champs[2]) or 0),
                }
                for nom, v in variantes.items()
            },
            "usages_batiments": dict(sorted(usages[dep].items())),
        }
    return {
        "statut": "mesure",
        "regle_decidee": (
            "usage_1 IS DISTINCT FROM 'Annexe' (P-D110-NULL, Ianis 2026-10-02) ;"
            " decision ni rouverte ni remplacee"
        ),
        "variantes": {
            "usage_null_exclu_du_compte": "usage_1 <> 'Annexe' (NULL non compte)",
            "aucun_constat_si_usage_null": "aucun constat si un usage NULL",
            "aucun_constat_si_usage_null_ou_indifferencie": (
                "aucun constat si un usage NULL ou 'Indifférencié' (information)"
            ),
        },
        "population": "maisons consultables (bloc D), regle de concordance inchangee",
        "departements": resultat,
    }


def mesurer_adresses_parcelle(  # noqa: C901
    conn: Any, departements: Sequence[str], racine: Path | None
) -> dict[str, Any]:
    if racine is None:
        return _non_mesure("SOURCE_ARCHIVE_DIR absent", "archive des sources")
    from app.etl.source_archive import SourceArchiveError, load_replay

    runs = _runs(conn)
    derniers = {}
    for run in runs:
        if run["statut"] == "success":
            derniers[run["dataset"]] = run
    archives = {}
    for dataset in ("ban", "cadastre"):
        run = derniers.get(dataset)
        if run is None:
            archives[dataset] = None
            continue
        try:
            records = sorted(
                (Path(racine) / dataset / "runs").glob(f"{run['id']}-*.json")
            )
            if len(records) != 1:
                raise SourceArchiveError("record_absent", f"{dataset}/{run['id']}")
            archives[dataset] = load_replay(racine, dataset, records[0].stem)
        except (SourceArchiveError, OSError) as erreur:
            archives[dataset] = getattr(erreur, "reason", type(erreur).__name__)
    if not hasattr(archives.get("ban"), "files"):
        return _non_mesure(
            f"archive BAN illisible: {archives.get('ban')}", "archive des sources"
        )
    millesimes = [
        {
            "origine": ligne["origine"],
            "departement": ligne["code_departement"],
            "date_min": ligne["date_min"],
            "date_max": ligne["date_max"],
            "effectif": int(ligne["effectif"]),
        }
        for ligne in _executer_identite(
            conn, MILLESIMES_SQL, {"departements": list(departements)}
        )
    ]
    resultat: dict[str, Any] = {}
    for dep in departements:
        adresses = _executer_identite(
            conn, ADRESSES_PARCELLE_RNB_SQL, {"departement": dep}
        )
        fichiers_ban = {e["relpath"]: e for e in archives["ban"].files}
        entree = fichiers_ban.get(f"raw/adresses-{dep}.csv")
        if entree is None:
            resultat[dep] = _non_mesure("CSV BAN absent du run", "archive des sources")
            continue
        cles = {_texte(a["ban_id"]) for a in adresses}
        with (Path(racine) / "ban" / entree["object"]).open(
            encoding="utf-8", newline=""
        ) as flux:
            ban = lire_ban_brute(csv.DictReader(flux, delimiter=";"), cles)["par_cle"]
        locales: set[str] = set()
        communes_locales: set[str] = set()
        for ligne in _executer_identite(
            conn, PARCELLES_DEPARTEMENT_SQL, {"departement": dep}
        ):
            locales.add(ligne["id_parcelle"])
            communes_locales.add(_texte(ligne["commune"]))
        source = None
        cadastre = archives.get("cadastre")
        if hasattr(cadastre, "files"):
            fichiers_cad = {e["relpath"]: e for e in cadastre.files}
            entree_cad = fichiers_cad.get(f"raw/cadastre-{dep}-parcelles.json.gz")
            if entree_cad is not None:
                with gzip.open(
                    Path(racine) / "cadastre" / entree_cad["object"],
                    "rt",
                    encoding="utf-8",
                ) as flux:
                    source = lire_cadastre_source(flux)
        bloc = agreger_adresses_parcelle(
            dep,
            adresses,
            ban,
            locales,
            communes_locales,
            source["ids"] if source and not source["non_reconnues"] else None,
            source["communes"] if source and not source["non_reconnues"] else None,
        )
        bloc["cadastre_source_contre_base"] = (
            {
                **comparer_cadastre(locales, source["ids"]),
                "features_source": source["features"],
                "features_non_reconnues": source["non_reconnues"],
            }
            if source is not None
            else _non_mesure(
                f"archive cadastre illisible: {cadastre}", "archive des sources"
            )
        )
        resultat[dep] = bloc
        del adresses, ban, locales
    return {
        "statut": "mesure",
        "run_ban": archives["ban"].run_id,
        "run_cadastre": getattr(archives.get("cadastre"), "run_id", None),
        "millesimes": millesimes,
        "millesime_ban_cad_parcelles": _non_mesure(
            "le CSV BAN ne date pas le cadastre de ses cad_parcelles",
            "CSV BAN archive",
        ),
        "regle_point": "ST_Intersects(parcelle, point BAN), regle du bloc D",
        "rnb": (
            "rnb_liens statut constructed ; provenance BAN possible (meme geocodage)"
        ),
        "departements": resultat,
    }


def mesurer_identite(
    conn: Any,
    departements: Sequence[str],
    rapport_base: Mapping[str, Any],
    racine: Path | None,
) -> dict[str, Any]:
    """Database and archive part of the audit, inside the read-only snapshot."""
    params = {"departements": list(departements)}
    observations = _executer_identite(conn, GENERATIONS_DPE_SQL, params)
    import_ids = {
        ligne["import_id"] for ligne in observations if ligne["import_id"] is not None
    }
    archives, archive_vs_base = mesurer_lignee_archive(
        conn, departements, racine, import_ids
    )
    date_import = rapport_base.get("date_import")
    return {
        "generations": {
            "observations_en_base": [
                {
                    "departement": ligne["code_departement"],
                    "regime": ligne["regime"],
                    "source": ligne["source_slug"],
                    "import_id": ligne["import_id"],
                    "source_version": ligne["source_version"],
                    "observe_le": ligne["observed_at"],
                    "observations": int(ligne["effectif"]),
                    "documents": int(ligne["documents"]),
                }
                for ligne in observations
            ],
            "archives_dpe": archives,
            "sha_servi": rapport_base.get("sha_servi"),
            "alembic": rapport_base.get("alembic"),
            "instantane_source": rapport_base.get("instantane_source"),
            "date_import_dpe": date_import,
        },
        "lineage": {
            "archive_vs_base": archive_vs_base,
            "rnb": agreger_rnb_lignee(_executer_identite(conn, RNB_LIGNEE_SQL, params)),
            "adresse_batiment_parcelle": mesurer_adresses_parcelle(
                conn, departements, racine
            ),
        },
        "null_usage_variants": mesurer_variantes_usage(
            conn,
            departements,
            date.fromisoformat(date_import) if date_import else None,
            date.fromisoformat(rapport_base["date_mesure"]),
            rapport_base.get("maisons_d110", {}),
        ),
    }


# --- Closed report ----------------------------------------------------------------

LIMITES_IDENTITE: tuple[str, ...] = (
    "Seul le rapprochement des octets archives et de la generation importee "
    "etablit une perte d'import ; le schema live inventorie la disponibilite "
    "a sa date, pas ce que contenait la generation.",
    "Non demande ne signifie pas absent a la source : un champ du schema hors "
    "API_SELECT est non_selectionne, son remplissage 44/85 reste non mesure.",
    "Le lecteur CSV de Python perd la distinction NULL / chaine vide ecrite "
    "par QUOTE_NOTNULL : une valeur source vide et une valeur absente sont "
    "comptees ensemble (vides).",
    "Coordonnees : seule la presence de la position est comparee ; la "
    "reprojection Lambert 93 -> WGS84 n'est pas recalculee.",
    "RNB : 'Reprise RNB' derive du meme geocodage BAN ; un accord avec "
    "rnb_liens est une correlation, jamais une preuve independante ; aucun "
    "identifiant n'identifie un logement.",
    "La cardinalite x-cardinality des metadonnees ADEME compte des valeurs "
    "distinctes France entiere, pas un taux de remplissage 44/85.",
    "Aucune extrapolation hors 44/85 ; aucun identifiant reel dans ce rapport.",
)


def _famille(champ: str) -> str:
    for famille, champs in FAMILLES_CHAMPS.items():
        if champ in champs:
            return famille
    for famille, motif in RECHERCHES_SCHEMA.items():
        if motif.search(champ):
            return famille
    return "autre"


def _schemas_par_source(
    schemas: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, dict[str, Mapping[str, Any]]] | None:
    if schemas is None:
        return None
    return {
        slug: {
            f["key"]: f
            for f in (meta.get("schema") or [])
            if isinstance(f, dict) and "key" in f
        }
        for slug, meta in schemas.items()
    }


def inventorier_champs(  # noqa: C901
    lineage: Mapping[str, Any], schemas: Mapping[str, Mapping[str, Any]] | None
) -> dict[str, Any]:
    par_source = _schemas_par_source(schemas)
    noms = list(CHAMPS_SELECTIONNES)
    for champs in FAMILLES_CHAMPS.values():
        noms += [c for c in champs if c not in noms]
    for cles in (par_source or {}).values():
        noms += sorted(
            c
            for c in cles
            if c not in noms and any(m.search(c) for m in RECHERCHES_SCHEMA.values())
        )
    avb = lineage.get("archive_vs_base", {})
    deps = avb.get("departements", {}) if avb.get("statut") == "mesure" else {}
    champs_sortie: dict[str, Any] = {}
    for nom in noms:
        selectionne = nom in CHAMPS_SELECTIONNES
        if par_source is None:
            schema: dict[str, Any] = dict.fromkeys(REGIMES_SOURCES, "non_mesure")
        else:
            schema = {
                slug: "present" if nom in cles else "absent_du_schema"
                for slug, cles in par_source.items()
            }
        archive: dict[str, Any] = {}
        renseigne: dict[str, Any] = {}
        for dep, bloc in sorted(deps.items()):
            if bloc.get("etat") == "non_mesure":
                continue
            for slug, src in bloc["par_source"].items():
                cle = f"{slug}-{dep}"
                archive[cle] = (
                    "archive"
                    if nom not in src["colonnes_manquantes"] and selectionne
                    else "non_archive"
                )
                if selectionne:
                    renseigne[cle] = _mesure(
                        f"lignes archivees {cle} (premiere occurrence du numero)",
                        src["renseigne"].get(nom, 0),
                        src["premieres_occurrences"],
                        "CSV archive de la generation importee",
                    )
        stockage = STOCKAGE.get(nom) if selectionne else None
        if par_source is not None and all(
            v == "absent_du_schema" for v in schema.values()
        ):
            etat = "absent_du_schema"
        elif not selectionne:
            etat = "non_selectionne"
        elif stockage is None:
            etat = "archive_non_stocke"
        else:
            etat = "stocke"
        entree: dict[str, Any] = {
            "famille": _famille(nom),
            "schema": schema,
            "selection": "selectionne" if selectionne else "non_selectionne",
            "archive": archive or "non_mesure",
            "stockage": stockage or ("non_stocke" if selectionne else "non_demande"),
            "etat": etat,
        }
        if renseigne:
            entree["renseigne_archive"] = renseigne
        if par_source is not None and not selectionne:
            entree["cardinalite_declaree"] = {
                slug: cles[nom].get("x-cardinality")
                for slug, cles in par_source.items()
                if nom in cles
            }
        champs_sortie[nom] = entree
    return champs_sortie


def _sources(
    schemas: Mapping[str, Mapping[str, Any]] | None,
    generations: Mapping[str, Any],
    empreintes: Mapping[str, str],
) -> dict[str, Any]:
    versions_base: dict[str, set[str]] = defaultdict(set)
    for obs in generations.get("observations_en_base", []):
        if obs.get("source") and obs.get("source_version"):
            versions_base[obs["source"]].add(obs["source_version"])
    sortie: dict[str, Any] = {}
    for slug, regime in REGIMES_SOURCES.items():
        entree: dict[str, Any] = {
            "regime": regime,
            "champs_selectionnes": len(CHAMPS_SELECTIONNES),
            "source_version_generation": sorted(versions_base.get(slug, ())),
        }
        meta = (schemas or {}).get(slug)
        if meta is None:
            entree["schema_live"] = _non_mesure(
                "metadonnees non fournies", "data.ademe.fr"
            )
        else:
            from app.etl_dpe.sources import DPE_SOURCES, source_version

            try:
                version = source_version(dict(meta), DPE_SOURCES[slug])
                compatible = True
            except ValueError:
                version, compatible = None, False
            entree["schema_live"] = {
                "etat": "mesure",
                "origine": "data.ademe.fr (metadonnees seules)",
                "metadata_sha256": empreintes.get(slug),
                "dataset_id": meta.get("id"),
                "data_updated_at": meta.get("dataUpdatedAt"),
                "documents_france": meta.get("count"),
                "champs_schema": len(meta.get("schema") or []),
                "compatible_avec_la_selection": compatible,
                "source_version_live": version,
                "meme_version_que_la_generation": (
                    version in versions_base.get(slug, ()) if version else False
                ),
            }
        sortie[slug] = entree
    return sortie


def _ecart(
    type_: str,
    classe: str,
    population: str,
    numerateur: int,
    denominateur: int,
    preuve: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "type": type_,
        "classe": classe,
        "etat": "etabli",
        **_mesure(population, numerateur, denominateur, preuve),
        "correction_minimale_possible": CORRECTIONS_MINIMALES.get(
            type_, CORRECTIONS_MINIMALES.get(extra.get("cle_correction", ""), "")
        ),
        **{k: v for k, v in extra.items() if k != "cle_correction"},
    }


def registre_ecarts(lineage: Mapping[str, Any]) -> list[dict[str, Any]]:  # noqa: C901
    """Closed register: proof, class (source / import / ambiguite / panne)
    and minimal correction; a failed read is a 'panne', never a zero."""
    ecarts: list[dict[str, Any]] = []
    avb = lineage.get("archive_vs_base", {})
    preuve = "lineage.archive_vs_base"
    if avb and avb.get("statut") != "mesure":
        ecarts.append(
            {
                "type": "panne",
                "classe": "panne",
                "etat": "non_mesure",
                "population": "generation DPE importee",
                "raison": avb.get("raison"),
                "correction_minimale_possible": CORRECTIONS_MINIMALES["panne"],
            }
        )
    for dep, bloc in sorted(avb.get("departements", {}).items()):
        if bloc.get("etat") == "non_mesure":
            ecarts.append(
                {
                    "type": "panne",
                    "classe": "panne",
                    "etat": "non_mesure",
                    "population": f"archive DPE {dep}",
                    "raison": bloc.get("raison"),
                    "correction_minimale_possible": CORRECTIONS_MINIMALES["panne"],
                }
            )
            continue
        lignes = sum(s["lignes"] for s in bloc["par_source"].values())
        distincts = bloc["numeros_distincts_archive"]
        for type_, n, denom, classe in (
            (
                "lignes_absentes_de_la_base",
                bloc["absents_de_la_base"],
                distincts,
                "import",
            ),
            (
                "lignes_en_base_hors_archive",
                bloc["en_base_hors_archive"],
                bloc["documents_en_base"],
                "import",
            ),
            (
                "doublon_source_identique",
                bloc["doublons"]["identiques"],
                lignes,
                "source",
            ),
            (
                "doublon_source_different",
                bloc["doublons"]["differents"],
                lignes,
                "source",
            ),
        ):
            if n:
                ecarts.append(
                    _ecart(type_, classe, f"archive DPE {dep}", n, denom, preuve)
                )
        for slug, src in sorted(bloc["par_source"].items()):
            for champ, colonne in STOCKAGE.items():
                if colonne is None and src["renseigne"].get(champ):
                    ecarts.append(
                        _ecart(
                            "champ_archive_non_stocke",
                            "import",
                            f"lignes archivees {slug}-{dep}",
                            src["renseigne"][champ],
                            src["premieres_occurrences"],
                            preuve,
                            champ=champ,
                        )
                    )
            for champ, classes in sorted(src["champs"].items()):
                retrouves = sum(classes.values())
                for classe_valeur in CLASSES_ECART:
                    if classes.get(classe_valeur):
                        ecarts.append(  # noqa: PERF401
                            _ecart(
                                classe_valeur,
                                "import",
                                f"documents {slug}-{dep} retrouves en base",
                                classes[classe_valeur],
                                retrouves,
                                preuve,
                                champ=champ,
                            )
                        )
    rnb = lineage.get("rnb", {}).get("departements", {})
    for _dep, par_provenance in sorted(rnb.items()):
        reprise = par_provenance.get("Reprise RNB")
        if reprise and reprise["id_rnb_lie_a_la_cle_ban"]["denominateur"]:
            m = reprise["id_rnb_lie_a_la_cle_ban"]
            ecarts.append(
                _ecart(
                    "rnb_correle_ban",
                    "ambiguite",
                    m["population"],
                    m["numerateur"],
                    m["denominateur"],
                    "lineage.rnb",
                )
            )
    adr = lineage.get("adresse_batiment_parcelle", {})
    for dep, bloc in sorted(adr.get("departements", {}).items()):
        if bloc.get("etat") == "non_mesure":
            continue
        hors = bloc["point_hors_parcelle"]["numerateur"]
        source = bloc["source_archivee_pour_les_causes_cadastre"]
        perte = (
            source.get("parcelle_citee_dans_la_source_archivee", 0)
            + source.get("commune_dans_la_source_archivee", 0)
            if source.get("etat") != "non_mesure"
            else None
        )
        expliquee = bloc["part_expliquee_par_le_cadastre_local"]["numerateur"]
        if perte:
            ecarts.append(
                _ecart(
                    "cadastre_perte_import",
                    "import",
                    f"adresses {dep} au point hors parcelle",
                    perte,
                    hors,
                    "lineage.adresse_batiment_parcelle",
                )
            )
        reste = expliquee - (perte or 0)
        if reste:
            ecarts.append(
                _ecart(
                    "cadastre_millesime_ou_couverture",
                    "ambiguite",
                    f"adresses {dep} au point hors parcelle",
                    reste,
                    hors,
                    "lineage.adresse_batiment_parcelle",
                )
            )
    return ecarts


def construire_rapport_identite(
    mesures: Mapping[str, Any],
    schemas: Mapping[str, Mapping[str, Any]] | None = None,
    empreintes: Mapping[str, str] | None = None,
    commandes: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Closed report: exactly CLES_RAPPORT_IDENTITE."""
    lineage = mesures.get("lineage", {})
    generations = mesures.get("generations", {})
    limites = list(LIMITES_IDENTITE)
    for nom, bloc in (
        ("archive_vs_base", lineage.get("archive_vs_base", {})),
        ("adresse_batiment_parcelle", lineage.get("adresse_batiment_parcelle", {})),
    ):
        if bloc.get("statut") != "mesure":
            limites.append(f"{nom}: non_mesure ({bloc.get('raison')})")
    if schemas is None:
        limites.append("schema live: non_mesure (metadonnees non fournies)")
    rapport = {
        "schema_version": IDENTITE_SCHEMA_VERSION,
        "sources": _sources(schemas, generations, empreintes or {}),
        "generations": generations,
        "fields": inventorier_champs(lineage, schemas),
        "lineage": lineage,
        "losses": registre_ecarts(lineage),
        "null_usage_variants": mesures.get(
            "null_usage_variants", _non_mesure("non execute", "base de travail")
        ),
        "limits": limites,
        "commands": [*mesures.get("commands", []), *commandes],
    }
    if tuple(rapport) != CLES_RAPPORT_IDENTITE:
        raise ValueError("identity report keys are not the closed set")
    return rapport


def serialiser_identite(rapport: Mapping[str, Any]) -> str:
    texte = serialiser(rapport)
    if _FUITE_PARCELLE.search(texte) or _FUITE_RNB.search(texte):
        raise GoldenSecurityError("identifier shape in the aggregated output")
    return texte


def _sha256_fichier(chemin: Path) -> str:
    with Path(chemin).open("rb") as flux:
        return hashlib.file_digest(flux, "sha256").hexdigest()


def finaliser_identite(
    rapport_base: Path, schemas: Sequence[str], commande: str
) -> dict[str, Any]:
    """Offline: the closed report of a CT run, completed with the live schema
    metadata saved outside the repository (no database, no network)."""
    mesure = json.loads(Path(rapport_base).read_text(encoding="utf-8"))
    audit = mesure.get("identity_audit")
    if not isinstance(audit, dict):
        raise ValueError("base report without identity_audit")  # noqa: TRY004
    lus: dict[str, Any] = {}
    empreintes: dict[str, str] = {}
    for valeur in schemas:
        slug, _, chemin = valeur.partition("=")
        if slug not in REGIMES_SOURCES or not chemin:
            raise ValueError("--schema-metadata expects <source>=<path>")
        lus[slug] = json.loads(Path(chemin).read_text(encoding="utf-8"))
        empreintes[slug] = _sha256_fichier(Path(chemin))
    script = Path(__file__)
    return construire_rapport_identite(
        audit,
        lus or None,
        empreintes,
        [
            {
                "commande": commande,
                "environnement": "poste local, sans base ni reseau",
                "rapport_base_sha256": _sha256_fichier(Path(rapport_base)),
                "script_sha256": _sha256_fichier(script) if script.is_file() else None,
                "date_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        ],
    )


# --- Output -----------------------------------------------------------------------


def _json_defaut(valeur: Any) -> Any:
    if isinstance(valeur, Decimal):
        return float(valeur)
    if isinstance(valeur, (date, datetime)):
        return valeur.isoformat()
    raise TypeError(type(valeur).__name__)


def serialiser(rapport: Mapping[str, Any]) -> str:
    texte = json.dumps(
        rapport, default=_json_defaut, ensure_ascii=False, sort_keys=True, indent=2
    )
    if _FUITE_DPE.search(texte) or _FUITE_BAN.search(texte):
        raise GoldenSecurityError("identifier shape in the aggregated output")
    return texte


def _liste_departements(valeur: str) -> list[str]:
    return [v.strip() for v in valeur.split(",") if v.strip()]


def _analyseur() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mesure_dpe_documents",
        description="Mesure DPE documentaire en lecture seule (D-108), agregats.",
    )
    parser.add_argument(
        "--base", action="store_true", help="Blocs A a F, H a J et D-109 sur la base"
    )
    parser.add_argument(
        "--capture", type=Path, help="Fichier de la capture (hors depot)"
    )
    parser.add_argument(
        "--json",
        dest="chemin_json",
        default="-",
        help="Chemin du JSON agrege, '-' pour stdout (defaut)",
    )
    parser.add_argument(
        "--departements", type=_liste_departements, default=list(DEPARTEMENTS_DEFAUT)
    )
    parser.add_argument(
        "--extraire-capture",
        type=Path,
        help="Dossier de la capture figee (poste local, sans base)",
    )
    parser.add_argument(
        "--vers",
        type=Path,
        help="Fichier de numeros produit par --extraire-capture (hors depot)",
    )
    parser.add_argument(
        "--identity-audit",
        action="store_true",
        help=(
            "Audit d'identite 04-14 : avec --base sur la base (lecture seule) ;"
            " sans --base, finalisation locale de --rapport-base"
        ),
    )
    parser.add_argument(
        "--rapport-base",
        type=Path,
        help="JSON d'une execution --base --identity-audit (finalisation locale)",
    )
    parser.add_argument(
        "--schema-metadata",
        action="append",
        default=[],
        help="<source>=<chemin> des metadonnees ADEME sauvegardees hors depot",
    )
    return parser


COMMANDE_CT_IDENTITE = (
    "infra/pve02/wb-tool.sh --read-only python - --base --identity-audit --json -"
    " < api/backend/scripts/mesure_dpe_documents.py"
)


def _mesurer(args: argparse.Namespace) -> dict[str, Any]:
    capture = lire_fichier_capture(args.capture) if args.capture else None
    raw_dsn = os.getenv("DATABASE_URL")
    if not raw_dsn:
        raise GoldenSecurityError("DATABASE_URL is missing")
    conn = _open_read_only_connection(
        raw_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    )
    try:
        # One stable snapshot for every denominator, still read-only.
        conn.rollback()
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        params = {"departements": list(args.departements)}
        rapport: dict[str, Any] = {
            "sha_servi": os.getenv("WORKBASE_SHA"),
            "alembic": (_executer(conn, TETE_ALEMBIC_SQL, {}) or [{}])[0].get(
                "version_num"
            ),
            "instantane_source": (_executer(conn, INSTANTANE_SQL, params) or [{}])[
                0
            ].get("instantane"),
        }
        servis = [
            ligne["code_departement"]
            for ligne in _executer(conn, DEPARTEMENTS_SERVIS_SQL, {})
        ]
        rapport["departements_servis"] = servis
        if args.base:
            archive = os.getenv("SOURCE_ARCHIVE_DIR")
            rapport.update(
                mesurer_base(
                    conn,
                    args.departements,
                    datetime.now(UTC).date(),
                    Path(archive) if archive else None,
                )
            )
            if getattr(args, "identity_audit", False):
                rapport["identity_audit"] = _auditer_identite(
                    conn, args.departements, rapport, archive
                )
        if capture is not None:
            rapport["capture"] = mesurer_capture(conn, capture, servis)
    finally:
        conn.rollback()
        conn.close()
    return rapport


def _auditer_identite(
    conn: Any, departements: Sequence[str], rapport: Mapping[str, Any], archive: Any
) -> dict[str, Any]:
    """Identity audit inside the snapshot, then the run list read again in a
    new snapshot: a concurrent import between start and end is reported."""
    debut = datetime.now(UTC)
    runs_debut = _runs(conn)
    mesures = mesurer_identite(
        conn, departements, rapport, Path(archive) if archive else None
    )
    conn.rollback()
    runs_fin = _runs(conn)
    cle = [(r["dataset"], r["id"], r["statut"]) for r in runs_debut]
    mesures["generations"]["etl_runs"] = runs_debut
    mesures["generations"]["stabilite"] = {
        "runs_identiques_debut_fin": cle
        == [(r["dataset"], r["id"], r["statut"]) for r in runs_fin],
        "run_en_cours": any(
            r["statut"] not in {"success", "failed"} for r in [*runs_debut, *runs_fin]
        ),
    }
    mesures["commands"] = [
        {
            "commande": COMMANDE_CT_IDENTITE,
            "environnement": "CT 102 datacommune-work, role wb_reader, archive"
            " des sources montee en lecture seule",
            "sha_servi": rapport.get("sha_servi"),
            "alembic": rapport.get("alembic"),
            "debut_audit_utc": debut.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fin_audit_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "script_sha256": None,
            "note": "script transmis sur l'entree standard ; empreinte relevee"
            " a la finalisation locale",
        }
    ]
    return construire_rapport_identite(mesures)


def _finaliser(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if not args.rapport_base:
        parser.print_usage(sys.stderr)
        return 2
    commande = (
        "python scripts/mesure_dpe_documents.py --identity-audit --rapport-base"
        " <rapport CT prive> "
        + " ".join(
            f"--schema-metadata {v.partition('=')[0]}=<metadonnees privees>"
            for v in args.schema_metadata
        )
        + " --json <rapport>"
    )
    try:
        texte = serialiser_identite(
            finaliser_identite(args.rapport_base, args.schema_metadata, commande)
        )
    except (GoldenSecurityError, ValueError, OSError) as erreur:
        print(f"audit d'identite bloque: {type(erreur).__name__}", file=sys.stderr)
        return 1
    if args.chemin_json == "-":
        print(texte)
    else:
        Path(args.chemin_json).write_text(texte + "\n", encoding="utf-8")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _analyseur()
    args = parser.parse_args(argv)
    if args.identity_audit and not args.base:
        return _finaliser(args, parser)
    try:
        if args.extraire_capture:
            if not args.vers:
                parser.print_usage(sys.stderr)
                return 2
            lignes = extraire_numeros_capture(args.extraire_capture)
            ecrire_fichier_capture(lignes, args.vers)
            print(f"capture: {len(lignes)} documents")
            return 0
        if not (args.base or args.capture):
            parser.print_usage(sys.stderr)
            return 2
        debut = datetime.now(UTC)
        rapport = _mesurer(args)
        rapport["duree_secondes"] = round(
            (datetime.now(UTC) - debut).total_seconds(), 1
        )
        texte = (
            serialiser_identite(rapport) if args.identity_audit else serialiser(rapport)
        )
    except (GoldenSecurityError, PartitionError, psycopg.Error, OSError) as erreur:
        print(f"mesure DPE documents bloquee: {type(erreur).__name__}", file=sys.stderr)
        return 1
    if args.chemin_json == "-":
        print(texte)
    else:
        Path(args.chemin_json).write_text(texte + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
