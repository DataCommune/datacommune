#!/usr/bin/env python3
"""Replayable measurement of the DPE-house attachment signals (D-02, D-03,
D-04, D-05, D-06, D-09, D-25).

Phase 58 blocks any attachment rule (D-06) and any coverage threshold (D-03
b) behind a measured instrument: nobody may invent a threshold before this
script has produced real numbers on 44 and 85. The D-06 checkpoint reads
this report's coverage-per-palier table to CHOOSE (or reject) a
``score_ban`` threshold -- the code below never proposes one.

Run from ``api/backend``::

    uv run python -m scripts.mesure_dpe_maison \\
        --sortie 58-MESURE-DPE-MAISON.md --json 58-MESURE-DPE-MAISON.json

Add ``--couverture-corrigee`` for the independent 58-22-v2 populations,
conditional exact simulation, global replacement audit and DVF corroboration.
That mode never reads or produces the private sample and makes no HTTP request.
Set PGOPTIONS before connecting to bound statement time and database resources.

The script has no write capability whatsoever, even when pointed at
production (D-19: the same script, unmodified, replays there in read-only
mode for the convergence gate): the connection is opened through
``_open_read_only_connection`` (which asserts
``transaction_read_only = on`` before any query runs) and every SQL
statement is checked against a fixed allowlist by ``assert_select_only``
before execution -- both mechanisms are imported from ``verify_golden_cases``,
never re-implemented (T-57-14). The main report publishes AGGREGATES only
(T-58-02). The explicit ``--echantillon`` output is the sole exception: it
contains identifiers and addresses, and its destination must stay ignored by
Git.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import html
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import groupby
from pathlib import Path
from typing import Any

import httpx
import psycopg
from pydantic import ValidationError

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.dpe.attribution import classify_dpe_attribution
from app.dpe.queries import _ATTRIBUTION_COLS
from scripts.verify_golden_cases import (
    GoldenSecurityError,
    _open_read_only_connection,
    assert_select_only,
)

DEPARTEMENTS_DEFAUT: tuple[str, ...] = ("44", "85")

# D-03 (b): la table des paliers permet a Ianis de CHOISIR (ou de rejeter) le
# seuil score_ban -- ces cinq valeurs ne sont PAS une proposition de seuil,
# seulement les colonnes du tableau de couverture que le rapport affiche
# (RESEARCH P-2 : la fourchette observee au CONTEXT etait fausse a l'echelle,
# min/mediane/max reels mesures 0.03 / 0.60-0.55 / 0.99).
PALIERS_SCORE_BAN: tuple[float, ...] = (0.50, 0.60, 0.70, 0.80, 0.90)

# Formes de cle BAN connues -- utilisees pour le zero-fill du rendu (Task 2)
# et pour classer les lignes SQL cote Python (jamais dans le SQL lui-meme).
FORMES_CLE_CONNUES: tuple[str, ...] = ("1", "2", "3", "4", "sans_cle")

# D-10: les gates de qualite source (1)(2)(3) s'appliquent a tout DPE
# individuel -- le bloc 3 (score_ban) porte donc type_batiment en DIMENSION.
TYPES_BATIMENT_CONNUS: tuple[str, ...] = ("maison", "appartement", "autre")

# Bloc 5 (D-03 d) : valeurs REELLES de type_batiment observees en base 44/85 --
# contrairement a TYPES_BATIMENT_CONNUS (bloc 3), aucun regroupement "autre"
# ici : la question posee est la coherence de TYPAGE, elle a besoin des
# valeurs telles quelles.
TYPES_BATIMENT_TOUS: tuple[str, ...] = ("maison", "appartement", "immeuble")

# Classes d'effectif du signal (c) -- collisions de cle sur les DPE maison
# terminaux (RESEARCH § 5.2).
CLASSES_COLLISION: tuple[str, ...] = ("1", "2", "3-5", ">5")

# D-12 : les trois classes EXHAUSTIVES de la gate (4) -- une gate non
# evaluable (donnee absente) n'est jamais comptee comme un echec (D-13).
CLASSES_GATE_EVALUABILITE: tuple[str, ...] = (
    "evaluable_passe",
    "evaluable_echoue",
    "non_evaluable",
)

# Bloc 2 (D-02, D-09 gate 1) : verdicts EXHAUSTIFS des deux comparaisons
# independantes -- non_evaluable (D-13) n'est jamais un echec.
VERDICTS_NUMERO_CONNUS: tuple[str, ...] = ("concordant", "ecart", "non_evaluable")
VERDICTS_VOIE_CONNUS: tuple[str, ...] = ("presente", "absente", "non_evaluable")
# Gate globale (D-09 gate 1) : concordant exige les DEUX comparaisons
# evaluees et passees -- meme discipline que les gates (4)/(7).
GATES_GLOBALES_CONNUES: tuple[str, ...] = ("concordant", "ecart", "non_evaluable")

# --- Bloc 1 : volumes maison par departement x forme de cle BAN ------------
# GROUP BY sur le nombre de segments de la cle (1 = commune seule, 2 = voie
# sans numero -- lieu-dit --, 3 = cle nominale, 4 = bis/ter) + part joignant
# `adresses` sur le meme departement (RESEARCH § 5.1). `identifiant_ban` est
# ecrit sur sa propre ligne partout ci-dessous (jamais suivi d'une virgule
# sur la meme ligne source) : usage reserve au GROUP BY / aux jointures,
# jamais projete dans un SELECT de sortie (T-58-02).
VOLUMES_PAR_FORME_CLE_SQL = """
SELECT d.code_departement,
       array_length(
           string_to_array(
               d.identifiant_ban
               , '_'
           )
           , 1
       ) AS segments,
       COUNT(*) AS effectif,
       COUNT(*) FILTER (
           WHERE EXISTS (
               SELECT 1
               FROM adresses a
               WHERE a.ban_id = d.identifiant_ban
                 AND a.code_departement = d.code_departement
           )
       ) AS joignant_adresses
FROM diagnostics_dpe d
WHERE d.code_departement = ANY(%(departements)s)
  -- Bloc 1 (D-10): population du bug signale = les maisons ; comme le bloc
  -- 4 (gate parcellaire), ce bloc reste filtre sur le type_batiment -- seul
  -- le bloc 3 (gates de qualite source, communes a tout DPE individuel)
  -- porte type_batiment en dimension plutot qu'en filtre.
  AND d.type_batiment = 'maison'
GROUP BY d.code_departement, segments
ORDER BY d.code_departement, segments
"""

# --- Bloc 2 (D-02, D-09 gate 1) : concordance adresse brute ADEME <-> -------
#     libelle BAN -- DEUX comparaisons INDEPENDANTES et binaires ------------
# Aucune jointure sur `adresses` : les colonnes brutes R1 (`adresse_brut`,
# `numero_voie_ban`, `nom_rue_ban`) donnent DEJA les deux cotes de la
# comparaison cote ADEME (RESEARCH Sec.3.1 -- "rend la gate (1) faisable
# SANS jointure"). La contre-verification optionnelle par `adresses` (P-5,
# ban_id non unique) n'apporte donc aucune information supplementaire ici et
# est deliberement omise : moins de risque de fan-out, pas de perte de
# pouvoir discriminant.
#
# 1. NUMERO : premier entier en tete de `adresse_brut` compare a
#    `numero_voie_ban` -- non_evaluable quand l'un des deux est absent
#    (lieu-dit sans numero en tete, D-13 : jamais un echec). Borne a
#    1-5 chiffres (`{1,5}`) : un numero de voie francais n'en compte
#    jamais plus -- decouvert en base reelle 44/85, une ligne portait un
#    entier de 12 chiffres en tete (`220436010002`, pas un numero de
#    voie) qui debordait `::integer` (`NumericValueOutOfRange`). Le
#    lookahead negatif `(?![0-9])` (`NUMERO_TETE_RE`) impose que la
#    tete numerique s'arrete la : sans lui, `{1,5}` matchait quand meme
#    les 5 premiers chiffres d'un entier plus long et fabriquait un
#    faux numero (`123456789012 RUE X` -> `12345` au lieu de NULL) --
#    revue de code, jamais mesure en base : le cast `::integer` ne
#    deborde jamais (le groupe capture est borne a 5 chiffres), donc ce
#    faux numero passait silencieusement les comparaisons de concordance
#    sans jamais lever d'erreur. Une "tete numerique" trop longue est
#    donc traitee comme un lieu-dit, jamais comme un numero -- coherent
#    avec D-13.
# 2. VOIE : `nom_rue_ban` normalise (minuscules, accents retires via
#    `unaccent()` -- meme idiome que `app/ban/queries.py` --, ponctuation
#    reduite a un espace) recherche dans `adresse_brut` normalise de la
#    meme facon. non_evaluable quand `nom_rue_ban` est vide (sinon une
#    recherche de chaine vide serait TOUJOURS "trouvee", faux-positif que
#    NULLIF empeche structurellement).
#
# Pitfall 9 (RESEARCH Sec.3.1/13.9) : une sequence d'echappement corrompue
# est parfois observee sur le libelle BAN (`"Beno*u00eet"` pour `"Benoit"`).
# La normalisation ci-dessus la traite comme un caractere quelconque (aucun
# decodage tente) -- mais `voie_corrompue` compte separement ces lignes,
# pour ne jamais les classer silencieusement en "absente"/"ecart".
#
# Pattern PostgreSQL ARE (syntaxe identique a `re` standard) utilise par les
# 3 sites `regexp_match` du bloc 2/2bis pour extraire la tete numerique d'une
# adresse -- constante UNIQUE injectee via `.replace()` (les `{1,5}` de la
# regex empechent `.format()`/f-string, en collision avec la syntaxe de
# formatage Python) pour que les 3 sites ne puissent jamais diverger.
NUMERO_TETE_RE: str = r"^\s*([0-9]{1,5})(?![0-9])"
_NUMERO_TETE_TOKEN: str = "__NUMERO_TETE_RE__"

CONCORDANCE_NUMERO_SQL = r"""
WITH normalise AS (
    SELECT d.code_departement,
           CASE
               WHEN d.type_batiment = 'maison' THEN 'maison'
               WHEN d.type_batiment = 'appartement' THEN 'appartement'
               ELSE 'autre'
           END AS type_batiment,
           array_length(
               string_to_array(
                   d.identifiant_ban
                   , '_'
               )
               , 1
           ) AS segments,
           (regexp_match(d.adresse_brut, '__NUMERO_TETE_RE__'))[1]::integer
               AS numero_brut,
           (regexp_match(d.numero_voie_ban, '__NUMERO_TETE_RE__'))[1]::integer
               AS numero_ban
    FROM diagnostics_dpe d
    WHERE d.code_departement = ANY(%(departements)s)
      -- Fill rate quasi totale (58-04 SUMMARY) : la poignee de lignes sans
      -- adresse_brut ne participe a aucune comparaison du bloc 2.
      AND d.adresse_brut IS NOT NULL
)
SELECT code_departement,
       type_batiment,
       segments,
       CASE
           WHEN numero_brut IS NULL OR numero_ban IS NULL THEN 'non_evaluable'
           WHEN numero_brut = numero_ban THEN 'concordant'
           ELSE 'ecart'
       END AS verdict,
       COUNT(*) AS effectif
FROM normalise
GROUP BY code_departement, type_batiment, segments, verdict
ORDER BY code_departement, type_batiment, segments, verdict
""".replace(_NUMERO_TETE_TOKEN, NUMERO_TETE_RE)

CONCORDANCE_VOIE_SQL = r"""
WITH normalise AS (
    SELECT d.code_departement,
           CASE
               WHEN d.type_batiment = 'maison' THEN 'maison'
               WHEN d.type_batiment = 'appartement' THEN 'appartement'
               ELSE 'autre'
           END AS type_batiment,
           array_length(
               string_to_array(
                   d.identifiant_ban
                   , '_'
               )
               , 1
           ) AS segments,
           NULLIF(
               regexp_replace(
                   lower(unaccent(COALESCE(d.nom_rue_ban, '')))
                   , '[^a-z0-9]+', ' ', 'g'
               )
               , ' '
           ) AS voie_ban_normalisee,
           regexp_replace(
               lower(unaccent(COALESCE(d.adresse_brut, '')))
               , '[^a-z0-9]+', ' ', 'g'
           ) AS adresse_brut_normalisee,
           (
               d.nom_rue_ban ~ '\*u[0-9a-fA-F]{4}'
               OR d.adresse_ban ~ '\*u[0-9a-fA-F]{4}'
           ) AS voie_corrompue
    FROM diagnostics_dpe d
    WHERE d.code_departement = ANY(%(departements)s)
      AND d.adresse_brut IS NOT NULL
)
SELECT code_departement,
       type_batiment,
       segments,
       CASE
           WHEN voie_ban_normalisee IS NULL THEN 'non_evaluable'
           WHEN position(voie_ban_normalisee IN adresse_brut_normalisee) > 0
               THEN 'presente'
           ELSE 'absente'
       END AS verdict,
       COUNT(*) AS effectif,
       COUNT(*) FILTER (WHERE voie_corrompue) AS effectif_corrompu
FROM normalise
GROUP BY code_departement, type_batiment, segments, verdict
ORDER BY code_departement, type_batiment, segments, verdict
"""

# Croisement des deux comparaisons avec statut_geocodage (RESEARCH Sec.3.3) :
# les lignes "non geocodees" portent un score_ban PLUS ELEVE que les
# "geocodees" -- cette table verifie si la CONCORDANCE suit la meme
# anomalie ou si elle la contredit, preuve chiffree pour ne pas faire de
# statut_geocodage une gate (D-06).
CONCORDANCE_STATUT_GEOCODAGE_SQL = r"""
WITH normalise AS (
    SELECT d.code_departement,
           CASE
               WHEN d.type_batiment = 'maison' THEN 'maison'
               WHEN d.type_batiment = 'appartement' THEN 'appartement'
               ELSE 'autre'
           END AS type_batiment,
           d.statut_geocodage,
           d.score_ban,
           (regexp_match(d.adresse_brut, '__NUMERO_TETE_RE__'))[1]::integer
               AS numero_brut,
           (regexp_match(d.numero_voie_ban, '__NUMERO_TETE_RE__'))[1]::integer
               AS numero_ban,
           NULLIF(
               regexp_replace(
                   lower(unaccent(COALESCE(d.nom_rue_ban, '')))
                   , '[^a-z0-9]+', ' ', 'g'
               )
               , ' '
           ) AS voie_ban_normalisee,
           regexp_replace(
               lower(unaccent(COALESCE(d.adresse_brut, '')))
               , '[^a-z0-9]+', ' ', 'g'
           ) AS adresse_brut_normalisee
    FROM diagnostics_dpe d
    WHERE d.code_departement = ANY(%(departements)s)
      AND d.adresse_brut IS NOT NULL
)
SELECT code_departement,
       type_batiment,
       statut_geocodage,
       CASE
           WHEN numero_brut IS NULL OR numero_ban IS NULL THEN 'non_evaluable'
           WHEN numero_brut = numero_ban THEN 'concordant'
           ELSE 'ecart'
       END AS verdict_numero,
       CASE
           WHEN voie_ban_normalisee IS NULL THEN 'non_evaluable'
           WHEN position(voie_ban_normalisee IN adresse_brut_normalisee) > 0
               THEN 'presente'
           ELSE 'absente'
       END AS verdict_voie,
       COUNT(*) AS effectif,
       ROUND(
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY score_ban)::numeric
           , 4
       ) AS score_ban_median
FROM normalise
GROUP BY code_departement, type_batiment, statut_geocodage
       , verdict_numero, verdict_voie
ORDER BY code_departement, type_batiment, statut_geocodage
       , verdict_numero, verdict_voie
""".replace(_NUMERO_TETE_TOKEN, NUMERO_TETE_RE)

# Croisement de la gate globale (D-09 gate 1) avec score_ban par palier :
# seule maniere de savoir si score_ban discrimine le vrai du faux, ou s'il
# ne fait que couper la couverture (RESEARCH P-2/Sec.5.3) -- meme idiome
# CROSS JOIN UNNEST que le bloc 3.
CONCORDANCE_SCORE_BAN_PALIER_SQL = r"""
WITH normalise AS (
    SELECT d.code_departement,
           CASE
               WHEN d.type_batiment = 'maison' THEN 'maison'
               WHEN d.type_batiment = 'appartement' THEN 'appartement'
               ELSE 'autre'
           END AS type_batiment,
           d.score_ban,
           (regexp_match(d.adresse_brut, '__NUMERO_TETE_RE__'))[1]::integer
               AS numero_brut,
           (regexp_match(d.numero_voie_ban, '__NUMERO_TETE_RE__'))[1]::integer
               AS numero_ban,
           NULLIF(
               regexp_replace(
                   lower(unaccent(COALESCE(d.nom_rue_ban, '')))
                   , '[^a-z0-9]+', ' ', 'g'
               )
               , ' '
           ) AS voie_ban_normalisee,
           regexp_replace(
               lower(unaccent(COALESCE(d.adresse_brut, '')))
               , '[^a-z0-9]+', ' ', 'g'
           ) AS adresse_brut_normalisee
    FROM diagnostics_dpe d
    WHERE d.code_departement = ANY(%(departements)s)
      AND d.adresse_brut IS NOT NULL
),
gates AS (
    SELECT code_departement,
           type_batiment,
           score_ban,
           CASE
               WHEN numero_brut IS NULL OR numero_ban IS NULL
                   THEN 'non_evaluable'
               WHEN voie_ban_normalisee IS NULL THEN 'non_evaluable'
               WHEN numero_brut = numero_ban
                   AND position(
                       voie_ban_normalisee IN adresse_brut_normalisee
                   ) > 0
                   THEN 'concordant'
               ELSE 'ecart'
           END AS gate_globale
    FROM normalise
)
SELECT code_departement,
       type_batiment,
       gate_globale,
       p.palier,
       COUNT(*) AS effectif,
       COUNT(*) FILTER (WHERE score_ban >= p.palier) AS n_au_dessus_du_palier
FROM gates
CROSS JOIN UNNEST(%(paliers_score_ban)s::numeric[]) AS p(palier)
GROUP BY code_departement, type_batiment, gate_globale, p.palier
ORDER BY code_departement, type_batiment, gate_globale, p.palier
""".replace(_NUMERO_TETE_TOKEN, NUMERO_TETE_RE)

# --- Bloc 3 : distribution score_ban x type_batiment x forme de cle x -------
#              palier (signal D-03 b, gates de qualite source D-09 (1)(2)(3))
# Le palier est passe en PARAMETRE LIE (%(paliers_score_ban)s, cast en
# numeric[]) -- jamais interpole dans la chaine SQL : c'est le meme principe
# que PALIERS_EFFECTIF/EFFECTIF_COMMUNAL_12M_SQL en 57.
DISTRIBUTION_SCORE_BAN_SQL = """
WITH base AS (
    SELECT d.code_departement,
           CASE
               WHEN d.type_batiment = 'maison' THEN 'maison'
               WHEN d.type_batiment = 'appartement' THEN 'appartement'
               ELSE 'autre'
           END AS type_batiment,
           array_length(
               string_to_array(
                   d.identifiant_ban
                   , '_'
               )
               , 1
           ) AS segments,
           d.score_ban
    FROM diagnostics_dpe d
    WHERE d.code_departement = ANY(%(departements)s)
)
SELECT b.code_departement,
       b.type_batiment,
       b.segments,
       p.palier,
       COUNT(*) AS effectif,
       MIN(b.score_ban) AS score_min,
       MAX(b.score_ban) AS score_max,
       AVG(b.score_ban) AS score_moyenne,
       PERCENTILE_CONT(0.1) WITHIN GROUP (ORDER BY b.score_ban) AS decile_10,
       PERCENTILE_CONT(0.2) WITHIN GROUP (ORDER BY b.score_ban) AS decile_20,
       PERCENTILE_CONT(0.3) WITHIN GROUP (ORDER BY b.score_ban) AS decile_30,
       PERCENTILE_CONT(0.4) WITHIN GROUP (ORDER BY b.score_ban) AS decile_40,
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY b.score_ban) AS decile_50,
       PERCENTILE_CONT(0.6) WITHIN GROUP (ORDER BY b.score_ban) AS decile_60,
       PERCENTILE_CONT(0.7) WITHIN GROUP (ORDER BY b.score_ban) AS decile_70,
       PERCENTILE_CONT(0.8) WITHIN GROUP (ORDER BY b.score_ban) AS decile_80,
       PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY b.score_ban) AS decile_90,
       COUNT(*) FILTER (WHERE b.score_ban IS NULL) AS score_ban_null,
       COUNT(*) FILTER (WHERE b.score_ban >= p.palier) AS n_au_dessus_du_palier
FROM base b
CROSS JOIN UNNEST(%(paliers_score_ban)s::numeric[]) AS p(palier)
GROUP BY b.code_departement, b.type_batiment, b.segments, p.palier
ORDER BY b.code_departement, b.type_batiment, b.segments, p.palier
"""

# --- Bloc 4 : collisions de cle -- DPE maison TERMINAUX par cle BAN --------
# "Terminal" reproduit EXACTEMENT app.dpe.attribution._terminaux() : une
# ligne qu'aucune autre ligne du meme departement ne remplace via
# numero_dpe_remplace (D-03 c). numero_dpe n'apparait que dans cette
# sous-requete de terminaux, jamais dans les colonnes du rapport final.
COLLISIONS_TERMINAUX_SQL = """
WITH terminaux AS (
    SELECT d.code_departement,
           d.numero_dpe,
           d.identifiant_ban
    FROM diagnostics_dpe d
    WHERE d.code_departement = ANY(%(departements)s)
      -- Bloc 4 (D-10): gate parcellaire specifique maison -- comme le bloc
      -- 1, ce bloc reste filtre sur le type_batiment.
      AND d.type_batiment = 'maison'
      AND d.identifiant_ban IS NOT NULL
      AND NOT EXISTS (
          SELECT 1
          FROM diagnostics_dpe r
          WHERE r.numero_dpe_remplace = d.numero_dpe
            AND r.code_departement = d.code_departement
      )
),
par_cle AS (
    SELECT code_departement,
           COUNT(*) AS effectif,
           identifiant_ban
    FROM terminaux
    GROUP BY code_departement, identifiant_ban
)
SELECT code_departement,
       CASE
           WHEN effectif = 1 THEN '1'
           WHEN effectif = 2 THEN '2'
           WHEN effectif BETWEEN 3 AND 5 THEN '3-5'
           ELSE '>5'
       END AS classe,
       COUNT(*) AS nb_cles,
       SUM(effectif) AS nb_lignes
FROM par_cle
GROUP BY code_departement, classe
ORDER BY code_departement, classe
"""

# --- Bloc 5 (D-03 d) : coherence type_batiment x forme de cle ---------------
# TOUS les types_batiment (pas seulement maison, contrairement aux blocs 1/4) :
# la question posee est la coherence de TYPAGE, qui n'a de sens qu'au regard
# des autres types (D-10 : gates de qualite source communes a tout DPE
# individuel).
TYPE_BATIMENT_PAR_FORME_CLE_SQL = """
SELECT d.code_departement,
       d.type_batiment,
       array_length(
           string_to_array(
               d.identifiant_ban
               , '_'
           )
           , 1
       ) AS segments,
       COUNT(*) AS effectif
FROM diagnostics_dpe d
WHERE d.code_departement = ANY(%(departements)s)
GROUP BY d.code_departement, d.type_batiment, segments
ORDER BY d.code_departement, d.type_batiment, segments
"""

# Bloc 5, 2e mesure (D-03 d) : DPE maison TERMINAL dont la date d'etablissement
# precede la derniere mutation de sa parcelle -- signal de renovation probable.
# La jointure DPE -> parcelle n'existe PAS directement (RESEARCH Sec.5.6/6.7) :
# elle passe par `adresses` (cle BAN) puis `parcelles` (ST_Intersects sur le
# POINT geocode par la BAN, jamais le point ADEME brut de `diagnostics_dpe`
# lui-meme -- ce dernier viendrait du MEME geocodeur que celui dont on mesure
# l'erreur, ce qui serait circulaire). `adresses.ban_id` n'est pas unique
# (RESEARCH P-5) : la ligne canonique est choisie en joignant D'ABORD sur la
# petite population (terminaux maison, indexee par `idx_dpe_identifiant_ban`)
# puis en dedupliquant par ROW_NUMBER() -- un `DISTINCT ON` prealable sur les
# ~1,02M lignes d'`adresses` a mesure plus de 120s localement contre <10s pour
# cette forme (jointure d'abord, dedoublonnage ensuite). Couverture publiee en
# clair (`couverts`) : ce bloc ne pretend jamais couvrir toute la population.
DPE_ANTERIEUR_MUTATION_SQL = """
WITH terminaux AS (
    SELECT d.code_departement, d.numero_dpe, d.identifiant_ban
           , d.date_etablissement_dpe
    FROM diagnostics_dpe d
    WHERE d.code_departement = ANY(%(departements)s)
      AND d.type_batiment = 'maison'
      AND d.identifiant_ban IS NOT NULL
      AND NOT EXISTS (
          SELECT 1
          FROM diagnostics_dpe r
          WHERE r.numero_dpe_remplace = d.numero_dpe
            AND r.code_departement = d.code_departement
      )
),
adresse_candidate AS (
    SELECT t.code_departement, t.numero_dpe, t.date_etablissement_dpe
           , a.position
           , ROW_NUMBER() OVER (
                 PARTITION BY t.numero_dpe ORDER BY a.id
             ) AS rn
    FROM terminaux t
    JOIN adresses a
        ON a.ban_id = t.identifiant_ban
       AND a.code_departement = t.code_departement
),
avec_parcelle AS (
    SELECT ac.code_departement, ac.numero_dpe, ac.date_etablissement_dpe
           , p.id_parcelle
    FROM adresse_candidate ac
    LEFT JOIN LATERAL (
        SELECT pp.id_parcelle
        FROM parcelles pp
        WHERE pp.code_departement = ac.code_departement
          AND ST_Intersects(pp.geometrie, ac.position)
        ORDER BY pp.id_parcelle
        LIMIT 1
    ) p ON true
    WHERE ac.rn = 1
),
derniere_mutation AS (
    SELECT ap.numero_dpe, MAX(mg.date_mutation) AS derniere_date
    FROM avec_parcelle ap
    JOIN mutation_parcelles mp ON mp.id_parcelle = ap.id_parcelle
    JOIN mutations_grain mg
        ON mg.id_mutation = mp.id_mutation
       AND mg.code_departement = ap.code_departement
    WHERE ap.id_parcelle IS NOT NULL
    GROUP BY ap.numero_dpe
)
SELECT ap.code_departement,
       COUNT(*) FILTER (WHERE dm.derniere_date IS NOT NULL) AS couverts,
       COUNT(*) FILTER (
           WHERE ap.date_etablissement_dpe < dm.derniere_date
       ) AS dpe_anterieur
FROM avec_parcelle ap
LEFT JOIN derniere_mutation dm ON dm.numero_dpe = ap.numero_dpe
GROUP BY ap.code_departement
ORDER BY ap.code_departement
"""

# --- Bloc 7 (D-12) : evaluabilite de la gate (4) -- un seul local d'habitation
#     sur la parcelle -----------------------------------------------------
# Source EXCLUSIVEMENT mutation_parcelles -> mutations_grain (RESEARCH P-4,
# Sec.5.6) : `batiments` (BDTOPO) n'est JAMAIS interroge ici -- absent du 44,
# et son schema ne porte ni lien parcelle ni nombre de logements. Trois
# classes EXHAUSTIVES : gate evaluable et passee, gate evaluable et echouee,
# gate non_evaluable (donnee absente n'est PAS un echec, D-13). Meme jointure
# canonique DPE -> parcelle que le bloc 5 (join-d'abord, dedoublonnage
# ensuite -- meme raison de performance).
GATE_EVALUABILITE_SQL = """
WITH dpe_maison AS (
    SELECT d.code_departement, d.numero_dpe, d.identifiant_ban
    FROM diagnostics_dpe d
    WHERE d.code_departement = ANY(%(departements)s)
      AND d.type_batiment = 'maison'
      AND d.identifiant_ban IS NOT NULL
),
adresse_candidate AS (
    SELECT dm.code_departement, dm.numero_dpe, dm.identifiant_ban
           , a.position
           , ROW_NUMBER() OVER (
                 PARTITION BY dm.numero_dpe ORDER BY a.id
             ) AS rn
    FROM dpe_maison dm
    JOIN adresses a
        ON a.ban_id = dm.identifiant_ban
       AND a.code_departement = dm.code_departement
),
avec_parcelle AS (
    SELECT ac.code_departement, ac.identifiant_ban
           , p.id_parcelle
    FROM adresse_candidate ac
    LEFT JOIN LATERAL (
        SELECT pp.id_parcelle
        FROM parcelles pp
        WHERE pp.code_departement = ac.code_departement
          AND ST_Intersects(pp.geometrie, ac.position)
        ORDER BY pp.id_parcelle
        LIMIT 1
    ) p ON true
    WHERE ac.rn = 1
),
avec_mutation AS (
    SELECT ap.code_departement, ap.identifiant_ban
           , ap.id_parcelle
           , bool_or(mg.mono_local AND mg.nb_locaux_maison = 1) AS passe_gate
           , bool_or(mg.mono_local IS NOT NULL) AS a_une_mutation
    FROM avec_parcelle ap
    LEFT JOIN mutation_parcelles mp ON mp.id_parcelle = ap.id_parcelle
    LEFT JOIN mutations_grain mg
        ON mg.id_mutation = mp.id_mutation
       AND mg.code_departement = ap.code_departement
    GROUP BY ap.code_departement, ap.identifiant_ban
           , ap.id_parcelle
)
SELECT code_departement,
       CASE
           WHEN passe_gate THEN 'evaluable_passe'
           WHEN a_une_mutation THEN 'evaluable_echoue'
           ELSE 'non_evaluable'
       END AS classe,
       COUNT(*) AS effectif
FROM avec_mutation
GROUP BY code_departement, classe
ORDER BY code_departement, classe
"""

# --- Bloc 8 (D-25) : concordance de surface -- DISTRIBUTION seule, aucune ---
#     tolerance proposee --------------------------------------------------
# Population comparee : cle BAN a EXACTEMENT un DPE maison terminal (meme
# definition que le bloc 4, classe '1') ET dont la parcelle canonique porte
# EXACTEMENT une mutation mono-local maison (`n_mono_maison = 1`, jamais
# `>= 1`). Le rapport publie des DECILES et l'effectif compare -- aucun seuil,
# aucun mot "recommande" : la bascule est tranchee au checkpoint D-06.
CONCORDANCE_SURFACE_SQL = """
WITH cle_unique AS (
    SELECT code_departement, numero_dpe, identifiant_ban
           , surface_habitable
    FROM (
        SELECT d.code_departement, d.numero_dpe, d.identifiant_ban
               , d.surface_habitable
               , COUNT(*) OVER (
                     PARTITION BY d.code_departement, d.identifiant_ban
                 ) AS n_terminaux
        FROM diagnostics_dpe d
        WHERE d.code_departement = ANY(%(departements)s)
          AND d.type_batiment = 'maison'
          AND d.identifiant_ban IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM diagnostics_dpe r
              WHERE r.numero_dpe_remplace = d.numero_dpe
                AND r.code_departement = d.code_departement
          )
    ) t
    WHERE n_terminaux = 1
      AND surface_habitable IS NOT NULL
      AND surface_habitable > 0
),
adresse_candidate AS (
    SELECT cu.code_departement, cu.numero_dpe, cu.surface_habitable
           , a.position
           , ROW_NUMBER() OVER (
                 PARTITION BY cu.numero_dpe ORDER BY a.id
             ) AS rn
    FROM cle_unique cu
    JOIN adresses a
        ON a.ban_id = cu.identifiant_ban
       AND a.code_departement = cu.code_departement
),
avec_parcelle AS (
    SELECT ac.code_departement, ac.numero_dpe, ac.surface_habitable
           , p.id_parcelle
    FROM adresse_candidate ac
    LEFT JOIN LATERAL (
        SELECT pp.id_parcelle
        FROM parcelles pp
        WHERE pp.code_departement = ac.code_departement
          AND ST_Intersects(pp.geometrie, ac.position)
        ORDER BY pp.id_parcelle
        LIMIT 1
    ) p ON true
    WHERE ac.rn = 1
),
mutation_mono_maison AS (
    SELECT ap.code_departement, ap.numero_dpe, ap.surface_habitable
           , COUNT(*) FILTER (
                 WHERE mg.mono_local AND mg.nb_locaux_maison = 1
             ) AS n_mono_maison
           , MAX(mg.surface_bati_totale) FILTER (
                 WHERE mg.mono_local AND mg.nb_locaux_maison = 1
             ) AS surface_bati_totale
    FROM avec_parcelle ap
    LEFT JOIN mutation_parcelles mp ON mp.id_parcelle = ap.id_parcelle
    LEFT JOIN mutations_grain mg
        ON mg.id_mutation = mp.id_mutation
       AND mg.code_departement = ap.code_departement
    WHERE ap.id_parcelle IS NOT NULL
    GROUP BY ap.code_departement, ap.numero_dpe, ap.surface_habitable
)
SELECT code_departement,
       COUNT(*) FILTER (WHERE n_mono_maison = 1) AS population_comparee,
       ROUND(
           PERCENTILE_CONT(0.1) WITHIN GROUP (
               ORDER BY ABS(surface_habitable - surface_bati_totale)
                   / NULLIF(surface_bati_totale, 0)
           ) FILTER (WHERE n_mono_maison = 1)
           ::numeric
           , 4
       ) AS decile_10,
       ROUND(
           PERCENTILE_CONT(0.3) WITHIN GROUP (
               ORDER BY ABS(surface_habitable - surface_bati_totale)
                   / NULLIF(surface_bati_totale, 0)
           ) FILTER (WHERE n_mono_maison = 1)
           ::numeric
           , 4
       ) AS decile_30,
       ROUND(
           PERCENTILE_CONT(0.5) WITHIN GROUP (
               ORDER BY ABS(surface_habitable - surface_bati_totale)
                   / NULLIF(surface_bati_totale, 0)
           ) FILTER (WHERE n_mono_maison = 1)
           ::numeric
           , 4
       ) AS decile_50,
       ROUND(
           PERCENTILE_CONT(0.7) WITHIN GROUP (
               ORDER BY ABS(surface_habitable - surface_bati_totale)
                   / NULLIF(surface_bati_totale, 0)
           ) FILTER (WHERE n_mono_maison = 1)
           ::numeric
           , 4
       ) AS decile_70,
       ROUND(
           PERCENTILE_CONT(0.9) WITHIN GROUP (
               ORDER BY ABS(surface_habitable - surface_bati_totale)
                   / NULLIF(surface_bati_totale, 0)
           ) FILTER (WHERE n_mono_maison = 1)
           ::numeric
           , 4
       ) AS decile_90
FROM mutation_mono_maison
GROUP BY code_departement
ORDER BY code_departement
"""

# --- Bloc 6 (D-14/D-15) : perimetre neuf -- volet LOCAL ---------------------
# Le comptage HORS BASE (effectif ADEME, identifiants du jeu neuf) est du
# HTTP, jamais du SQL (Task 2 action) -- il est recupere par
# `_recuperer_perimetre_neuf_ademe`/`_neuf_non_interroge` AVANT ce bloc.
# Ce bloc ne fait que RAPPROCHER localement les identifiants deja recuperes
# (parametres lies, `unnest()` en tuples zippes) avec `diagnostics_dpe` --
# aucune requete ici ne selectionne jamais un `identifiant_ban`/`numero_dpe`
# dans ses colonnes de SORTIE (uniquement des agregats, T-58-02) ; ils ne
# vivent que dans les CTE intermediaires `neuf`/`existant`/`rapproche`, sur
# le meme principe que le bloc 4 (`terminaux`/`par_cle`).
PERIMETRE_NEUF_SQL = """
WITH neuf AS (
    SELECT * FROM unnest(
        %(ids_ban_neuf)s::text[]
        , %(numero_dpe_neuf)s::text[]
        , %(numero_dpe_remplace_neuf)s::text[]
        , %(dates_neuf)s::date[]
        , %(surfaces_neuf)s::numeric[]
    ) AS t(
        identifiant_ban
        , numero_dpe
        , numero_dpe_remplace
        , date_neuf
        , surface_neuf
    )
),
existant AS (
    -- DISTINCT ON (identifiant_ban) : une cle EXISTANT peut porter plusieurs
    -- DPE maison terminaux (signal (c), bloc 4 -- 10 698 clés a 2 terminaux
    -- rien qu'en 44). Sans ce dedoublonnage deterministe, la jointure
    -- ci-dessous fanne et double `neuf_et_existant` (mesure locale :
    -- neuf_sans_existant + neuf_et_existant depassait l'effectif ADEME
    -- avant ce correctif). Le representant retenu est le plus RECENT --
    -- comparaison la plus significative pour l'ecart de date/surface.
    SELECT DISTINCT ON (d.identifiant_ban)
           d.identifiant_ban, d.numero_dpe
           , d.date_etablissement_dpe, d.surface_habitable
    FROM diagnostics_dpe d
    WHERE d.code_departement = ANY(%(departements)s)
      AND d.type_batiment = 'maison'
      AND d.identifiant_ban IS NOT NULL
      AND NOT EXISTS (
          SELECT 1
          FROM diagnostics_dpe r
          WHERE r.numero_dpe_remplace = d.numero_dpe
            AND r.code_departement = d.code_departement
      )
    ORDER BY d.identifiant_ban, d.date_etablissement_dpe DESC NULLS LAST
           , d.numero_dpe
),
rapproche AS (
    SELECT n.identifiant_ban, n.numero_dpe
           , e.numero_dpe IS NOT NULL AS a_existant
           , (n.numero_dpe_remplace = e.numero_dpe) AS relie_par_remplace
           , (n.date_neuf - e.date_etablissement_dpe) AS ecart_jours
           , CASE
                 WHEN e.surface_habitable IS NOT NULL
                      AND e.surface_habitable != 0
                 THEN ABS(n.surface_neuf - e.surface_habitable)
                          / e.surface_habitable
             END AS ecart_surface_relatif
    FROM neuf n
    LEFT JOIN existant e ON e.identifiant_ban = n.identifiant_ban
),
collision AS (
    SELECT COUNT(*) AS n_collisions
    FROM neuf n
    WHERE EXISTS (
        SELECT 1
        FROM diagnostics_dpe d
        WHERE d.numero_dpe = n.numero_dpe
          AND d.code_departement = ANY(%(departements)s)
    )
)
SELECT
    (SELECT n_collisions FROM collision) AS collisions_numero_dpe,
    COUNT(*) FILTER (WHERE NOT a_existant) AS neuf_sans_existant,
    COUNT(*) FILTER (WHERE a_existant) AS neuf_et_existant,
    COUNT(*) FILTER (WHERE a_existant AND relie_par_remplace) AS relies_par_remplace,
    ROUND(
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ecart_jours)
            FILTER (WHERE a_existant)
        ::numeric
        , 1
    ) AS ecart_jours_median,
    ROUND(
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ecart_surface_relatif)
            FILTER (WHERE a_existant)
        ::numeric
        , 4
    ) AS ecart_surface_median
FROM rapproche
"""

# --- Bloc 10 : controle d'integrite -- totaux recoupes, jamais masques ------
# Rendu comme des LIGNES DE TABLE (D-06), jamais comme des assertions qui
# feraient echouer le script : un ecart avec l'effectif ADEME (dernier run
# ancien) est un fait a ecrire, pas une panne.
CONTROLE_INTEGRITE_SQL = """
SELECT code_departement,
       COUNT(*) AS effectif_brut_dpe,
       COUNT(*) FILTER (WHERE type_batiment = 'maison') AS effectif_maison,
       COUNT(*) FILTER (
           WHERE type_batiment = 'maison' AND identifiant_ban IS NULL
       ) AS maison_sans_cle_ban
FROM diagnostics_dpe
WHERE code_departement = ANY(%(departements)s)
GROUP BY code_departement
ORDER BY code_departement
"""

# --- D-11 : qualite du match /v1/resolve -- gate resolve seulement si -------
#     prouvee (D-11, jamais posee d'office) ------------------------------
# `adresses` ne porte AUCUNE ligne "lieu-dit" (numero IS NULL) -- verifie
# (1 021 393 lignes 44/85, 100% numerotees) : le seul signal de lieu-dit
# disponible est la FORME de la cle DPE elle-meme (2 segments, RESEARCH
# Sec.5.1). `ban_id`/`identifiant_ban` encode "commune_voie[_numero[_rep]]" :
# le prefixe "commune_voie" (2 premiers segments) est donc comparable
# directement entre les deux jeux, sans jointure spatiale ni texte de voie.
RESOLVE_NUMEROTEE_AVEC_LIEU_DIT_SQL = """
WITH dpe_numerotee AS (
    SELECT DISTINCT d.code_departement, d.identifiant_ban
           , (string_to_array(d.identifiant_ban, '_'))[1]
                 || '_' || (string_to_array(d.identifiant_ban, '_'))[2]
             AS prefixe_voie
    FROM diagnostics_dpe d
    WHERE d.code_departement = ANY(%(departements)s)
      AND d.type_batiment = 'maison'
      AND array_length(string_to_array(d.identifiant_ban, '_'), 1) IN (3, 4)
),
lieu_dit_connu AS (
    SELECT DISTINCT code_departement, identifiant_ban AS prefixe_voie
    FROM diagnostics_dpe
    WHERE code_departement = ANY(%(departements)s)
      AND array_length(string_to_array(identifiant_ban, '_'), 1) = 2
)
SELECT dn.code_departement,
       COUNT(*) AS population_numerotee,
       COUNT(*) FILTER (WHERE ld.prefixe_voie IS NOT NULL) AS avec_lieu_dit_meme_voie
FROM dpe_numerotee dn
LEFT JOIN lieu_dit_connu ld
    ON ld.prefixe_voie = dn.prefixe_voie
   AND ld.code_departement = dn.code_departement
GROUP BY dn.code_departement
ORDER BY dn.code_departement
"""

RESOLVE_LIEU_DIT_AVEC_NUMEROTEE_SQL = """
WITH dpe_lieu_dit AS (
    SELECT DISTINCT d.code_departement, d.identifiant_ban
    FROM diagnostics_dpe d
    WHERE d.code_departement = ANY(%(departements)s)
      AND d.type_batiment = 'maison'
      AND array_length(string_to_array(d.identifiant_ban, '_'), 1) = 2
),
adresses_prefixe AS (
    SELECT DISTINCT code_departement
           , (string_to_array(ban_id, '_'))[1]
                 || '_' || (string_to_array(ban_id, '_'))[2]
             AS prefixe_voie
    FROM adresses
    WHERE code_departement = ANY(%(departements)s)
)
SELECT dl.code_departement,
       COUNT(*) AS population_lieu_dit,
       COUNT(*) FILTER (WHERE ap.prefixe_voie IS NOT NULL) AS avec_numerotee_meme_voie
FROM dpe_lieu_dit dl
LEFT JOIN adresses_prefixe ap
    ON ap.prefixe_voie = dl.identifiant_ban
   AND ap.code_departement = dl.code_departement
GROUP BY dl.code_departement
ORDER BY dl.code_departement
"""

# --- D-04 : tirage de l'echantillon stratifie pour le verdict humain --------
# SEULES requetes du script qui projettent des identifiants (`identifiant_ban`,
# `numero_dpe`) -- jamais dans ``ALLOWED_SQL`` de ``_construire_rapport`` en
# usage normal, exclusivement appelees par ``_tirer_echantillon`` derriere
# l'option ``--echantillon``, dont la sortie est le SEUL artefact du script a
# porter des identifiants (T-58-10/T-58-11). ``LIMIT %(limite)s`` borne
# chaque signal, appele une fois PAR DEPARTEMENT (44/85 equilibres, D-04).
ECHANTILLON_SCORE_BAN_BAS_SQL = """
WITH selection AS (
    SELECT d.code_departement, d.identifiant_ban, d.numero_dpe, d.score_ban
           , d.adresse_brut, d.adresse_ban, d.numero_voie_ban
           , d.nom_rue_ban, d.statut_geocodage
           , d.date_etablissement_dpe, d.etiquette_dpe
           , array_length(string_to_array(d.identifiant_ban, '_'), 1) AS forme_cle
    FROM diagnostics_dpe d
    WHERE d.code_departement = ANY(%(departements)s)
      AND d.type_batiment = 'maison'
      AND d.identifiant_ban IS NOT NULL
      AND array_length(string_to_array(d.identifiant_ban, '_'), 1) = 3
      AND d.score_ban IS NOT NULL
    ORDER BY d.score_ban ASC, d.code_departement, d.numero_dpe
    LIMIT %(limite)s
)
SELECT s.*,
       (
           SELECT COUNT(*)
           FROM diagnostics_dpe terminal
           WHERE terminal.code_departement = s.code_departement
             AND terminal.identifiant_ban = s.identifiant_ban
             AND terminal.type_batiment = 'maison'
             AND NOT EXISTS (
                 SELECT 1
                 FROM diagnostics_dpe remplacement
                 WHERE remplacement.numero_dpe_remplace = terminal.numero_dpe
                   AND remplacement.code_departement = terminal.code_departement
             )
       ) AS nombre_dpe_terminaux,
       adresse_dc.numero AS numero_dc, adresse_dc.rep AS rep_dc,
       adresse_dc.nom_voie AS nom_voie_dc,
       adresse_dc.code_postal AS code_postal_dc,
       adresse_dc.nom_commune AS nom_commune_dc
FROM selection s
LEFT JOIN LATERAL (
    SELECT a.numero, a.rep, a.nom_voie, a.code_postal, a.nom_commune
    FROM adresses a
    WHERE a.ban_id = s.identifiant_ban
      AND a.code_departement = s.code_departement
    ORDER BY a.id
    LIMIT 1
) adresse_dc ON true
ORDER BY s.score_ban ASC, s.code_departement, s.numero_dpe
"""

ECHANTILLON_COLLISION_SQL = """
WITH terminaux AS (
    SELECT d.code_departement, d.numero_dpe, d.identifiant_ban, d.score_ban
           , d.adresse_brut, d.adresse_ban, d.numero_voie_ban
           , d.nom_rue_ban, d.statut_geocodage
           , d.date_etablissement_dpe, d.etiquette_dpe
           , array_length(string_to_array(d.identifiant_ban, '_'), 1) AS forme_cle
           , COUNT(*) OVER (
                 PARTITION BY d.code_departement, d.identifiant_ban
             ) AS nombre_dpe_terminaux
    FROM diagnostics_dpe d
    WHERE d.code_departement = ANY(%(departements)s)
      AND d.type_batiment = 'maison'
      AND d.identifiant_ban IS NOT NULL
      AND NOT EXISTS (
          SELECT 1
          FROM diagnostics_dpe r
          WHERE r.numero_dpe_remplace = d.numero_dpe
            AND r.code_departement = d.code_departement
      )
),
selection AS (
    SELECT *
    FROM terminaux
    WHERE nombre_dpe_terminaux >= 3
    ORDER BY code_departement, identifiant_ban, numero_dpe
    LIMIT %(limite)s
)
SELECT s.*,
       adresse_dc.numero AS numero_dc, adresse_dc.rep AS rep_dc,
       adresse_dc.nom_voie AS nom_voie_dc,
       adresse_dc.code_postal AS code_postal_dc,
       adresse_dc.nom_commune AS nom_commune_dc
FROM selection s
LEFT JOIN LATERAL (
    SELECT a.numero, a.rep, a.nom_voie, a.code_postal, a.nom_commune
    FROM adresses a
    WHERE a.ban_id = s.identifiant_ban
      AND a.code_departement = s.code_departement
    ORDER BY a.id
    LIMIT 1
) adresse_dc ON true
ORDER BY s.code_departement, s.identifiant_ban, s.numero_dpe
"""

ECHANTILLON_LIEU_DIT_SQL = """
WITH selection AS (
    SELECT d.code_departement, d.identifiant_ban, d.numero_dpe, d.score_ban
           , d.adresse_brut, d.adresse_ban, d.numero_voie_ban
           , d.nom_rue_ban, d.statut_geocodage
           , d.date_etablissement_dpe, d.etiquette_dpe
           , array_length(string_to_array(d.identifiant_ban, '_'), 1) AS forme_cle
    FROM diagnostics_dpe d
    WHERE d.code_departement = ANY(%(departements)s)
      AND d.type_batiment = 'maison'
      AND d.identifiant_ban IS NOT NULL
      AND array_length(string_to_array(d.identifiant_ban, '_'), 1) IN (1, 2)
    ORDER BY d.code_departement, d.identifiant_ban, d.numero_dpe
    LIMIT %(limite)s
)
SELECT s.*,
       (
           SELECT COUNT(*)
           FROM diagnostics_dpe terminal
           WHERE terminal.code_departement = s.code_departement
             AND terminal.identifiant_ban = s.identifiant_ban
             AND terminal.type_batiment = 'maison'
             AND NOT EXISTS (
                 SELECT 1
                 FROM diagnostics_dpe remplacement
                 WHERE remplacement.numero_dpe_remplace = terminal.numero_dpe
                   AND remplacement.code_departement = terminal.code_departement
             )
       ) AS nombre_dpe_terminaux,
       adresse_dc.numero AS numero_dc, adresse_dc.rep AS rep_dc,
       adresse_dc.nom_voie AS nom_voie_dc,
       adresse_dc.code_postal AS code_postal_dc,
       adresse_dc.nom_commune AS nom_commune_dc
FROM selection s
LEFT JOIN LATERAL (
    SELECT a.numero, a.rep, a.nom_voie, a.code_postal, a.nom_commune
    FROM adresses a
    WHERE a.ban_id = s.identifiant_ban
      AND a.code_departement = s.code_departement
    ORDER BY a.id
    LIMIT 1
) adresse_dc ON true
ORDER BY s.code_departement, s.identifiant_ban, s.numero_dpe
"""

# --- Meta : tete Alembic sur laquelle le rapport a ete produit --------------
TETE_ALEMBIC_SQL = "SELECT version_num FROM alembic_version"

# 58-22 is opt-in: historical SQL and reports above retain their definitions.
# Keys below are internal inputs to the real classifier / graph, never outputs.
VERSION_COUVERTURE = "58-22-v2"
LIMITE_EXACT = 120
MAX_LIGNES_GRAPHE = 1_000_000
FORME_CLE_BAN_RE = r"^[0-9AB]{5}_[0-9A-Za-z]+(_[0-9]+(_[0-9A-Za-z]+)?)?$"

POPULATIONS_CORRIGEES_SQL = r"""
WITH d AS MATERIALIZED (
    SELECT code_departement, numero_dpe, identifiant_ban,
           CASE WHEN identifiant_ban LIKE '97%%'
                THEN left(identifiant_ban, 3)
                ELSE left(identifiant_ban, 2) END AS dept_cle
    FROM diagnostics_dpe
    WHERE code_departement = ANY(%(departements)s)
), a AS MATERIALIZED (
    SELECT code_departement, ban_id, COUNT(*) AS lignes
    FROM adresses
    WHERE code_departement = ANY(%(departements)s)
      AND NULLIF(ban_id, '') IS NOT NULL
    GROUP BY code_departement, ban_id
), cles_d AS MATERIALIZED (
    SELECT DISTINCT code_departement, identifiant_ban
    FROM diagnostics_dpe WHERE NULLIF(identifiant_ban, '') IS NOT NULL
), volumes_d AS (
    SELECT d.code_departement, COUNT(*) AS lignes_dpe,
           COUNT(DISTINCT numero_dpe) AS diagnostics_distincts,
           COUNT(*) FILTER (WHERE numero_dpe IS NULL) AS numeros_absents,
           COUNT(DISTINCT NULLIF(identifiant_ban, '')) AS cles_dpe_distinctes,
           COUNT(*) FILTER (WHERE NULLIF(identifiant_ban, '') IS NULL)
               AS lignes_sans_cle,
           COUNT(DISTINCT identifiant_ban) FILTER (
               WHERE identifiant_ban <> '' AND identifiant_ban !~ %(forme_cle)s
           ) AS cles_forme_non_reconnue,
           COUNT(DISTINCT identifiant_ban) FILTER (
               WHERE identifiant_ban ~ '^[0-9AB]{5}$'
           ) AS cles_commune_seule,
           COUNT(*) FILTER (WHERE dept_cle <> d.code_departement)
               AS lignes_departement_cle_incoherent,
           COUNT(DISTINCT identifiant_ban) FILTER (WHERE a.ban_id IS NOT NULL)
               AS cles_dpe_joignant_adresses,
           COUNT(DISTINCT identifiant_ban) FILTER (
               WHERE NULLIF(identifiant_ban, '') IS NOT NULL AND a.ban_id IS NULL
           ) AS cles_dpe_orphelines_dans_departement
    FROM d LEFT JOIN a ON a.code_departement = d.code_departement
      AND a.ban_id = d.identifiant_ban
    GROUP BY d.code_departement
), volumes_a AS (
    SELECT a.code_departement, SUM(lignes) AS lignes_adresses_avec_cle,
           COUNT(*) AS cles_adresses_distinctes,
           SUM(lignes - 1) AS lignes_adresses_doublons_cle,
           COUNT(*) FILTER (WHERE dd.identifiant_ban IS NOT NULL)
               AS cles_adresses_avec_dpe_meme_departement,
           COUNT(*) FILTER (WHERE da.identifiant_ban IS NOT NULL)
               AS cles_adresses_avec_candidats_api
    FROM a LEFT JOIN cles_d dd ON dd.identifiant_ban = a.ban_id
      AND dd.code_departement = a.code_departement
    LEFT JOIN cles_d da ON da.identifiant_ban = a.ban_id
      AND da.code_departement = CASE WHEN a.ban_id LIKE '97%%'
          THEN left(a.ban_id, 3) ELSE left(a.ban_id, 2) END
    GROUP BY a.code_departement
), sans_cle AS (
    SELECT code_departement, COUNT(*) AS lignes_adresses_sans_cle
    FROM adresses WHERE code_departement = ANY(%(departements)s)
      AND NULLIF(ban_id, '') IS NULL GROUP BY code_departement
)
SELECT p.code_departement,
       COALESCE(to_jsonb(vd) - 'code_departement', '{}'::jsonb) AS diagnostics,
       COALESCE(to_jsonb(va) - 'code_departement', '{}'::jsonb) AS adresses,
       COALESCE(sc.lignes_adresses_sans_cle, 0) AS lignes_adresses_sans_cle
FROM unnest(%(departements)s::text[]) p(code_departement)
LEFT JOIN volumes_d vd USING (code_departement)
LEFT JOIN volumes_a va USING (code_departement)
LEFT JOIN sans_cle sc USING (code_departement)
ORDER BY p.code_departement
"""

# The inner equality/department/order/sentinel matches dpe_par_identifiant_ban.
# DISTINCT keys avoid BAN fan-out; zero-candidate keys are counted separately.
CANDIDATS_EXACT_CORRIGE_SQL = """
WITH cles AS (
    SELECT DISTINCT a.code_departement, a.ban_id
    FROM adresses a
    WHERE a.code_departement = ANY(%(departements)s)
      AND NULLIF(a.ban_id, '') IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM diagnostics_dpe d
          WHERE d.identifiant_ban = a.ban_id
            AND d.code_departement = CASE WHEN a.ban_id LIKE '97%%'
                THEN left(a.ban_id, 3) ELSE left(a.ban_id, 2) END
      )
)
SELECT a.code_departement AS departement_reference, q.*
FROM cles a
JOIN LATERAL (
    SELECT __COLONNES__ FROM diagnostics_dpe d
    WHERE d.identifiant_ban = a.ban_id
      AND d.code_departement = CASE WHEN a.ban_id LIKE '97%%'
          THEN left(a.ban_id, 3) ELSE left(a.ban_id, 2) END
    ORDER BY d.numero_dpe
    LIMIT %(limite_exact_sentinelle)s
) q ON true
ORDER BY a.code_departement, a.ban_id, q.numero_dpe
""".replace("__COLONNES__", ", ".join(f"d.{col}" for col in _ATTRIBUTION_COLS))

# All departments and types: every edge and every present referenced predecessor.
# Isolated rows have no graph contribution; the inventory still counts them.
GRAPHE_REMPLACEMENTS_SQL = """
WITH liens AS MATERIALIZED (
    SELECT numero_dpe, numero_dpe_remplace, identifiant_ban,
           code_departement, type_batiment
    FROM diagnostics_dpe WHERE NULLIF(numero_dpe_remplace, '') IS NOT NULL
)
SELECT * FROM (
    SELECT * FROM liens
    UNION ALL
    SELECT d.numero_dpe, d.numero_dpe_remplace, d.identifiant_ban,
           d.code_departement, d.type_batiment
    FROM diagnostics_dpe d
    WHERE NULLIF(d.numero_dpe_remplace, '') IS NULL
      AND EXISTS (SELECT 1 FROM liens l WHERE l.numero_dpe_remplace = d.numero_dpe)
) graphe
LIMIT %(limite_graphe_sentinelle)s
"""

INVENTAIRE_CORRIGE_SQL = """
SELECT code_departement, COUNT(*) AS lignes_dpe,
       COUNT(*) FILTER (WHERE NULLIF(numero_dpe_remplace, '') IS NOT NULL)
           AS lignes_avec_remplacement
FROM diagnostics_dpe GROUP BY code_departement ORDER BY code_departement
"""

PARAMETRES_LECTURE_SQL = """
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_setting('transaction_isolation') AS transaction_isolation,
       current_setting('statement_timeout') AS statement_timeout,
       current_setting('lock_timeout') AS lock_timeout,
       current_setting('work_mem') AS work_mem,
       current_setting('temp_file_limit') AS temp_file_limit,
       current_setting('max_parallel_workers_per_gather') AS parallel_workers
"""

# No arbitrary parcel election. The corroboration covers all intersecting parcels
# of known BAN keys with a DPE; it neither reproduces resolve nor counts dwellings.
CORROBORATION_DVF_SQL = """
WITH cles AS (
    SELECT a.code_departement, a.ban_id, a.position FROM adresses a
    WHERE a.code_departement = ANY(%(departements)s)
      AND NULLIF(a.ban_id, '') IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM diagnostics_dpe d WHERE d.identifiant_ban = a.ban_id
            AND d.code_departement = a.code_departement
      )
), parcelles_cles AS (
    SELECT DISTINCT a.code_departement, a.ban_id, p.id_parcelle
    FROM cles a LEFT JOIN parcelles p
      ON p.code_departement = a.code_departement
     AND ST_Intersects(p.geometrie, a.position)
), observations AS (
    SELECT pc.code_departement, pc.ban_id,
           COUNT(DISTINCT pc.id_parcelle) AS parcelles,
           COUNT(DISTINCT mg.id_mutation) AS mutations,
           bool_or(mg.mono_local AND mg.nb_locaux_maison = 1) AS mono_maison,
           bool_or(mg.mono_local IS FALSE OR mg.nb_locaux_maison > 1)
               AS observation_contraire,
           MIN(mg.date_mutation) AS date_min, MAX(mg.date_mutation) AS date_max
    FROM parcelles_cles pc
    LEFT JOIN mutation_parcelles mp ON mp.id_parcelle = pc.id_parcelle
    LEFT JOIN mutations_grain mg ON mg.id_mutation = mp.id_mutation
      AND mg.code_departement = pc.code_departement
    GROUP BY pc.code_departement, pc.ban_id
)
SELECT code_departement, COUNT(*) AS cles_observees,
       COUNT(*) FILTER (WHERE parcelles = 0) AS cles_sans_parcelle_intersectante,
       COUNT(*) FILTER (WHERE parcelles > 1) AS cles_plusieurs_parcelles,
       COUNT(*) FILTER (WHERE mutations = 0) AS cles_sans_mutation_observee,
       COUNT(*) FILTER (WHERE mutations > 0) AS cles_avec_mutation_observee,
       COUNT(*) FILTER (WHERE mono_maison) AS cles_avec_observation_mono_maison,
       COUNT(*) FILTER (WHERE observation_contraire)
           AS cles_avec_observation_contraire,
       COUNT(*) FILTER (WHERE mono_maison AND observation_contraire)
           AS cles_observations_contradictoires,
       MIN(date_min)::text AS date_mutation_min,
       MAX(date_max)::text AS date_mutation_max
FROM observations GROUP BY code_departement ORDER BY code_departement
"""

ALLOWED_SQL: frozenset[str] = frozenset(
    {
        VOLUMES_PAR_FORME_CLE_SQL,
        CONCORDANCE_NUMERO_SQL,
        CONCORDANCE_VOIE_SQL,
        CONCORDANCE_STATUT_GEOCODAGE_SQL,
        CONCORDANCE_SCORE_BAN_PALIER_SQL,
        DISTRIBUTION_SCORE_BAN_SQL,
        COLLISIONS_TERMINAUX_SQL,
        TYPE_BATIMENT_PAR_FORME_CLE_SQL,
        DPE_ANTERIEUR_MUTATION_SQL,
        GATE_EVALUABILITE_SQL,
        CONCORDANCE_SURFACE_SQL,
        PERIMETRE_NEUF_SQL,
        CONTROLE_INTEGRITE_SQL,
        RESOLVE_NUMEROTEE_AVEC_LIEU_DIT_SQL,
        RESOLVE_LIEU_DIT_AVEC_NUMEROTEE_SQL,
        ECHANTILLON_SCORE_BAN_BAS_SQL,
        ECHANTILLON_COLLISION_SQL,
        ECHANTILLON_LIEU_DIT_SQL,
        TETE_ALEMBIC_SQL,
        POPULATIONS_CORRIGEES_SQL,
        CANDIDATS_EXACT_CORRIGE_SQL,
        GRAPHE_REMPLACEMENTS_SQL,
        INVENTAIRE_CORRIGE_SQL,
        PARAMETRES_LECTURE_SQL,
        CORROBORATION_DVF_SQL,
    }
)

# Champs publiables par bloc (T-58-02) : whitelist explicite, jamais un
# passthrough du dict retourne par psycopg -- une colonne ajoutee par
# megarde a une requete future serait silencieusement ecartee ici plutot
# que publiee.
CHAMPS_BLOC1: tuple[str, ...] = (
    "code_departement",
    "segments",
    "effectif",
    "joignant_adresses",
)
CHAMPS_BLOC3: tuple[str, ...] = (
    "code_departement",
    "type_batiment",
    "segments",
    "palier",
    "effectif",
    "score_min",
    "score_max",
    "score_moyenne",
    "decile_10",
    "decile_20",
    "decile_30",
    "decile_40",
    "decile_50",
    "decile_60",
    "decile_70",
    "decile_80",
    "decile_90",
    "score_ban_null",
    "n_au_dessus_du_palier",
)
CHAMPS_BLOC2_NUMERO: tuple[str, ...] = (
    "code_departement",
    "type_batiment",
    "segments",
    "verdict",
    "effectif",
)
CHAMPS_BLOC2_VOIE: tuple[str, ...] = (
    "code_departement",
    "type_batiment",
    "segments",
    "verdict",
    "effectif",
    "effectif_corrompu",
)
CHAMPS_BLOC2_STATUT_GEOCODAGE: tuple[str, ...] = (
    "code_departement",
    "type_batiment",
    "statut_geocodage",
    "verdict_numero",
    "verdict_voie",
    "effectif",
    "score_ban_median",
)
CHAMPS_BLOC2_SCORE_BAN_PALIER: tuple[str, ...] = (
    "code_departement",
    "type_batiment",
    "gate_globale",
    "palier",
    "effectif",
    "n_au_dessus_du_palier",
)
CHAMPS_BLOC4: tuple[str, ...] = ("code_departement", "classe", "nb_cles", "nb_lignes")
CHAMPS_BLOC5_TYPE: tuple[str, ...] = (
    "code_departement",
    "type_batiment",
    "segments",
    "effectif",
)
CHAMPS_BLOC5_ANTERIORITE: tuple[str, ...] = (
    "code_departement",
    "couverts",
    "dpe_anterieur",
)
CHAMPS_BLOC7: tuple[str, ...] = ("code_departement", "classe", "effectif")
CHAMPS_BLOC8: tuple[str, ...] = (
    "code_departement",
    "population_comparee",
    "decile_10",
    "decile_30",
    "decile_50",
    "decile_70",
    "decile_90",
)
# PERIMETRE_NEUF_SQL est appele UNE FOIS PAR DEPARTEMENT (params scopes a un
# seul departement a la fois) -- la ligne retournee ne porte donc pas
# code_departement, il est rattache cote Python apres l'appel.
CHAMPS_BLOC6: tuple[str, ...] = (
    "collisions_numero_dpe",
    "neuf_sans_existant",
    "neuf_et_existant",
    "relies_par_remplace",
    "ecart_jours_median",
    "ecart_surface_median",
)
CHAMPS_BLOC10: tuple[str, ...] = (
    "code_departement",
    "effectif_brut_dpe",
    "effectif_maison",
    "maison_sans_cle_ban",
)
CHAMPS_RESOLVE_A: tuple[str, ...] = (
    "code_departement",
    "population_numerotee",
    "avec_lieu_dit_meme_voie",
)
CHAMPS_RESOLVE_B: tuple[str, ...] = (
    "code_departement",
    "population_lieu_dit",
    "avec_numerotee_meme_voie",
)


def _executer(
    conn: psycopg.Connection[Any], sql: str, params: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Run one allowlisted, read-only, bind-parameterized query."""
    assert_select_only(sql, ALLOWED_SQL)
    return list(conn.execute(sql, params).fetchall())


def _projeter(
    rows: Sequence[Mapping[str, Any]], champs: Sequence[str]
) -> list[dict[str, Any]]:
    """Whitelist projection: only the named fields survive (T-58-02).

    Internal classifier/graph queries may read identifiers. This closed
    projection makes aggregate output independent of extra columns: an extra
    key from a future query change is dropped, never published in the report.
    """
    return [{champ: row[champ] for champ in champs} for row in rows]


# --- Bloc 6 : comptage HORS BASE du perimetre neuf (D-14) -- HTTP, jamais ---
#     du SQL (Task 2 action) -----------------------------------------------
# Slug data-fair ADEME : "dpe02neuf", JAMAIS "dpe03neuf" (RESEARCH P-1, ce
# dernier slug rend 404). Reutilise l'idiome de pagination de
# `app.etl_dpe.pipeline._download_page` (parametres `size`/`select`/`qs`,
# curseur `next`), jamais reimplemente depuis zero -- borne a
# `MAX_PAGES_NEUF` pages (T-58-12 : jamais de telechargement complet du jeu
# neuf, 1 417 452 lignes France entiere).
#
# `_api_base_url` DUPLIQUE volontairement `app.etl_dpe.config.api_base_url`
# (une ligne) plutot que de l'importer : ce module doit s'importer SANS base
# ni reseau (verification de phase), et `app.etl_dpe.config` tire la chaine
# complete `app.etl.base` -> `app.config.settings`, qui leve en l'absence de
# secrets de production -- cout d'import disproportionne pour une constante.
def _api_base_url(source: str) -> str:
    return f"https://data.ademe.fr/data-fair/api/v1/datasets/{source}/lines"


API_DPE02NEUF_URL: str = _api_base_url("dpe02neuf")
CHAMPS_NEUF_ADEME: str = (
    "identifiant_ban,numero_dpe,numero_dpe_remplace,"
    "date_etablissement_dpe,surface_habitable_logement"
)
MAX_PAGES_NEUF: int = 5  # borne de securite -- couvre jusqu'a 50 000 lignes
CSV_PAGE_SIZE_NEUF: int = 10000  # borne T-58-12 -- jamais de page plus large
TIMEOUT_ADEME_S: float = 10.0
USER_AGENT_ADEME: str = "DataCommune-mesure-dpe-maison/58-03 (+https://datacommune.fr)"


def _neuf_non_interroge(departements: Sequence[str]) -> dict[str, Any]:
    """Safe default for ``recuperer_neuf`` -- NO network call.

    Used whenever ``_construire_rapport`` is called without an explicit
    fetcher, which is every offline test in ``test_mesure_dpe_script.py``
    (Task 3 acceptance: the guard-test suite stays fast and deterministic,
    never dependent on ADEME's availability). ``main()`` always overrides
    this with ``_recuperer_perimetre_neuf_ademe`` -- the real fetcher is
    never exercised by the offline suite.
    """
    return {
        "disponible": False,
        "message": "source ADEME non interrogee (appel hors main())",
        "par_departement": {
            dept: {
                "effectif_neuf_ademe": None,
                "effectif_existant_ademe": None,
                "lignes": [],
            }
            for dept in departements
        },
    }


def _recuperer_perimetre_neuf_ademe(
    departements: Sequence[str],
    *,
    sources: Sequence[str] = ("dpe03existant", "dpe02neuf"),
    timeout: float = TIMEOUT_ADEME_S,
) -> dict[str, Any]:
    """Real network call to the ADEME data-fair API (``dpe02neuf`` slug).

    Bounded and read-only: one ``size=0`` request per department for each
    of the neuf/existant maison counts (T-58-12), then paginated minimal-
    field pages capped at ``MAX_PAGES_NEUF`` x 10 000 rows, followed via the
    ``next`` header exactly like ``etl_dpe/pipeline.py._download_page``. Any
    network failure (timeout, DNS, HTTP error) is caught HERE and reported
    as an explicit "source ADEME injoignable" sentinel: the local SQL
    measurement never depends on the network (Task 2 acceptance).

    ``sources`` (``--sources-ademe``, defaut les deux) restreint les COMPTES
    HORS BASE effectues -- une source absente rend ``None`` pour son
    effectif plutot que d'appeler l'API ; la pagination d'identifiants ne
    porte de toute facon que sur ``dpe02neuf``, jamais sur
    ``dpe03existant`` (deja en base).
    """
    resultat: dict[str, Any] = {
        "disponible": True,
        "message": None,
        "par_departement": {},
    }
    headers = {"User-Agent": USER_AGENT_ADEME}
    try:
        with httpx.Client(timeout=timeout, headers=headers) as client:
            for dept in departements:
                qs_maison = f'code_departement_ban:"{dept}" AND type_batiment:"maison"'

                effectif_neuf = None
                if "dpe02neuf" in sources:
                    compte_neuf = client.get(
                        API_DPE02NEUF_URL, params={"size": 0, "qs": qs_maison}
                    )
                    compte_neuf.raise_for_status()
                    effectif_neuf = compte_neuf.json().get("total")

                effectif_existant = None
                if "dpe03existant" in sources:
                    compte_existant = client.get(
                        _api_base_url("dpe03existant"),
                        params={"size": 0, "qs": qs_maison},
                    )
                    compte_existant.raise_for_status()
                    effectif_existant = compte_existant.json().get("total")

                lignes: list[dict[str, Any]] = []
                if "dpe02neuf" in sources:
                    url: str | None = API_DPE02NEUF_URL
                    params: dict[str, Any] | None = {
                        "size": CSV_PAGE_SIZE_NEUF,
                        "select": CHAMPS_NEUF_ADEME,
                        "qs": qs_maison,
                    }
                    for _ in range(MAX_PAGES_NEUF):
                        reponse = client.get(url, params=params)
                        reponse.raise_for_status()
                        corps = reponse.json()
                        lignes.extend(corps.get("results", []))
                        url = corps.get("next")
                        params = None
                        if not url:
                            break

                resultat["par_departement"][dept] = {
                    "effectif_neuf_ademe": effectif_neuf,
                    "effectif_existant_ademe": effectif_existant,
                    "lignes": lignes,
                }
    except (httpx.HTTPError, OSError) as exc:
        return {
            "disponible": False,
            "message": (
                f"source ADEME injoignable le {datetime.now(UTC).date().isoformat()} "
                f"({exc.__class__.__name__})"
            ),
            "par_departement": {
                dept: {
                    "effectif_neuf_ademe": None,
                    "effectif_existant_ademe": None,
                    "lignes": [],
                }
                for dept in departements
            },
        }
    return resultat


def _mesurer_perimetre_neuf(
    conn: psycopg.Connection[Any],
    departements: Sequence[str],
    recuperer_neuf: Any,
) -> list[dict[str, Any]]:
    """Bloc 6: fetch (HTTP, injectable) then reconcile locally (SQL).

    Returns one row per department, ``code_departement`` attached in Python
    (``PERIMETRE_NEUF_SQL`` is scoped to a single department per call, so it
    never selects the column itself).
    """
    donnees_ademe = recuperer_neuf(departements)
    message = None if donnees_ademe.get("disponible") else donnees_ademe.get("message")
    lignes: list[dict[str, Any]] = []
    for dept in departements:
        par_dept = donnees_ademe.get("par_departement", {}).get(dept, {})
        rows_ademe = par_dept.get("lignes", [])
        params = {
            "departements": [dept],
            "ids_ban_neuf": [r.get("identifiant_ban") for r in rows_ademe],
            "numero_dpe_neuf": [r.get("numero_dpe") for r in rows_ademe],
            "numero_dpe_remplace_neuf": [
                r.get("numero_dpe_remplace") for r in rows_ademe
            ],
            "dates_neuf": [r.get("date_etablissement_dpe") for r in rows_ademe],
            # ADEME rend un melange int/float pour ce champ numerique -- un
            # cast Python uniforme evite `psycopg.DataError: cannot dump
            # lists of mixed types` sur le tableau lie (mesure locale).
            "surfaces_neuf": [
                float(r["surface_habitable_logement"])
                if r.get("surface_habitable_logement") is not None
                else None
                for r in rows_ademe
            ],
        }
        # `next(iter(...), {})`, jamais `[0]` : le double SELECT scalaire sans
        # GROUP BY rend TOUJOURS une ligne en base reelle, mais une connexion
        # de test peut rendre une liste vide pour une requete non configuree
        # (offline tests, Task 3) -- une liste vide reste une mesure zero.
        rows = _projeter(_executer(conn, PERIMETRE_NEUF_SQL, params), CHAMPS_BLOC6)
        row = dict(next(iter(rows), {}))
        row["code_departement"] = dept
        row["effectif_neuf_ademe"] = par_dept.get("effectif_neuf_ademe")
        row["effectif_existant_ademe"] = par_dept.get("effectif_existant_ademe")
        row["message"] = message
        lignes.append(row)
    return lignes


def _construire_rapport(
    conn: psycopg.Connection[Any],
    departements: Sequence[str],
    etiquette: str,
    jour: date,
    *,
    recuperer_neuf: Any = _neuf_non_interroge,
) -> dict[str, Any]:
    """Run every block measured by this plan (blocs 1, 2, 3, 4, 5, 6, 7, 8,
    10, ``resolve_d11``).

    Returns the closed report dict (D-05): ``meta`` + one key per measured
    bloc. Bloc 9 (croisement signaux x verdict humain, D-04) is added by
    plan 58-09 -- this function never writes a placeholder key for it, so
    ``_rendre_markdown`` can tell "not yet measured" from "measured empty".

    ``recuperer_neuf`` is the injectable HTTP fetcher for bloc 6 (Task 2
    acceptance: the SQL guard test must not depend on network availability).
    It defaults to ``_neuf_non_interroge`` -- a network-free sentinel used by
    every offline test in ``test_mesure_dpe_script.py``. ``main()`` always
    passes ``_recuperer_perimetre_neuf_ademe`` explicitly.
    """
    params = {
        "departements": list(departements),
        "paliers_score_ban": [float(palier) for palier in PALIERS_SCORE_BAN],
    }

    tete_alembic_rows = _executer(conn, TETE_ALEMBIC_SQL, {})
    tete_alembic = tete_alembic_rows[0]["version_num"] if tete_alembic_rows else None

    bloc1 = _projeter(_executer(conn, VOLUMES_PAR_FORME_CLE_SQL, params), CHAMPS_BLOC1)
    bloc2_numero = _projeter(
        _executer(conn, CONCORDANCE_NUMERO_SQL, params), CHAMPS_BLOC2_NUMERO
    )
    bloc2_voie = _projeter(
        _executer(conn, CONCORDANCE_VOIE_SQL, params), CHAMPS_BLOC2_VOIE
    )
    bloc2_statut_geocodage = _projeter(
        _executer(conn, CONCORDANCE_STATUT_GEOCODAGE_SQL, params),
        CHAMPS_BLOC2_STATUT_GEOCODAGE,
    )
    bloc2_score_ban_palier = _projeter(
        _executer(conn, CONCORDANCE_SCORE_BAN_PALIER_SQL, params),
        CHAMPS_BLOC2_SCORE_BAN_PALIER,
    )
    bloc3 = _projeter(_executer(conn, DISTRIBUTION_SCORE_BAN_SQL, params), CHAMPS_BLOC3)
    bloc4 = _projeter(_executer(conn, COLLISIONS_TERMINAUX_SQL, params), CHAMPS_BLOC4)
    bloc5_type = _projeter(
        _executer(conn, TYPE_BATIMENT_PAR_FORME_CLE_SQL, params), CHAMPS_BLOC5_TYPE
    )
    bloc5_anteriorite = _projeter(
        _executer(conn, DPE_ANTERIEUR_MUTATION_SQL, params), CHAMPS_BLOC5_ANTERIORITE
    )
    bloc6 = _mesurer_perimetre_neuf(conn, departements, recuperer_neuf)
    bloc7 = _projeter(_executer(conn, GATE_EVALUABILITE_SQL, params), CHAMPS_BLOC7)
    bloc8 = _projeter(_executer(conn, CONCORDANCE_SURFACE_SQL, params), CHAMPS_BLOC8)
    bloc10 = _projeter(_executer(conn, CONTROLE_INTEGRITE_SQL, params), CHAMPS_BLOC10)
    resolve_a = _projeter(
        _executer(conn, RESOLVE_NUMEROTEE_AVEC_LIEU_DIT_SQL, params), CHAMPS_RESOLVE_A
    )
    resolve_b = _projeter(
        _executer(conn, RESOLVE_LIEU_DIT_AVEC_NUMEROTEE_SQL, params), CHAMPS_RESOLVE_B
    )

    return {
        "meta": {
            "etiquette": etiquette,
            "jour": jour.isoformat(),
            "departements": list(departements),
            "tete_alembic": tete_alembic,
        },
        "bloc1": bloc1,
        "bloc2": {
            "numero": bloc2_numero,
            "voie": bloc2_voie,
            "statut_geocodage": bloc2_statut_geocodage,
            "score_ban_palier": bloc2_score_ban_palier,
        },
        "bloc3": bloc3,
        "bloc4": bloc4,
        "bloc5": {"type_batiment": bloc5_type, "anteriorite": bloc5_anteriorite},
        "bloc6": bloc6,
        "bloc7": bloc7,
        "bloc8": bloc8,
        "bloc10": bloc10,
        "resolve_d11": {
            "numerotee_avec_lieu_dit": resolve_a,
            "lieu_dit_avec_numerotee": resolve_b,
        },
    }


# --- Rendu Markdown/JSON -- deterministe et zero-aware par construction -----
# Titre du bloc 9 : mesure par le plan 58-09 (verdict humain d'echantillon,
# D-04). Rend un titre + la note ci-dessous tant que sa cle est absente de
# `donnees` -- il ne disparait jamais silencieusement du rapport (Task 2).
BLOCS_NON_MESURES: dict[int, str] = {
    9: "Croisement signaux x verdict humain (D-04)",
}

NOTE_NON_MESURE = "_Bloc non encore mesure (voir plan 58-09)._"


def _formate_valeur(valeur: Any) -> str:
    if valeur is None:
        return "n.d."
    if isinstance(valeur, Decimal):
        return f"{valeur:.3f}"
    texte = str(valeur).replace("\r\n", "\n").replace("\r", "\n")
    return html.escape(texte, quote=False).replace("|", r"\|").replace("\n", "<br>")


def _table_markdown(en_tetes: Sequence[str], lignes: Sequence[Sequence[Any]]) -> str:
    entete = "| " + " | ".join(en_tetes) + " |"
    separateur = "| " + " | ".join("---" for _ in en_tetes) + " |"
    if not lignes:
        return "\n".join([entete, separateur, "| _(aucune donnee)_ |"])
    corps = [
        "| " + " | ".join(_formate_valeur(v) for v in ligne) + " |" for ligne in lignes
    ]
    return "\n".join([entete, separateur, *corps])


def _forme_cle(segments: int | None) -> str:
    """Map a segment count to a known key-shape bucket (never in the SQL)."""
    if segments is None:
        return "sans_cle"
    if segments in (1, 2, 3, 4):
        return str(segments)
    return "autre"


def _bloc1_lignes(
    bloc1: Sequence[Mapping[str, Any]], departements: Sequence[str]
) -> list[tuple[Any, ...]]:
    """One row per (departement, forme_cle) for every KNOWN combination --
    an absent combination is a measured zero, never a missing row."""
    par_combo: dict[tuple[str, str], Mapping[str, Any]] = {}
    autres: list[tuple[Any, ...]] = []
    for row in bloc1:
        forme = _forme_cle(row["segments"])
        if forme == "autre":
            autres.append(
                (
                    row["code_departement"],
                    "autre",
                    row["effectif"],
                    row["joignant_adresses"],
                )
            )
            continue
        par_combo[(row["code_departement"], forme)] = row
    lignes: list[tuple[Any, ...]] = []
    for departement in sorted(departements):
        for forme in FORMES_CLE_CONNUES:
            row = par_combo.get((departement, forme))
            effectif = row["effectif"] if row else 0
            joignant = row["joignant_adresses"] if row else 0
            lignes.append((departement, forme, effectif, joignant))
    lignes.extend(sorted(autres))
    return lignes


def _bloc2_numero_lignes(
    bloc2_numero: Sequence[Mapping[str, Any]], departements: Sequence[str]
) -> list[tuple[Any, ...]]:
    """One row per (departement, type_batiment, forme_cle) -- pivots the 3
    ``verdict`` rows emitted by SQL into columns (bloc3-style), zero-aware
    over every KNOWN combination. ``pct_ecart`` is computed over the
    EVALUABLE population only (excludes non_evaluable from the denominator
    -- same methodology as RESEARCH Sec.3.2's own percentages)."""
    par_combo: dict[tuple[str, str, str], dict[str, int]] = {}
    for row in bloc2_numero:
        forme = _forme_cle(row["segments"])
        cle = (row["code_departement"], row["type_batiment"], forme)
        par_combo.setdefault(cle, {})[row["verdict"]] = row["effectif"]
    lignes: list[tuple[Any, ...]] = []
    for departement in sorted(departements):
        for type_batiment in TYPES_BATIMENT_CONNUS:
            for forme in FORMES_CLE_CONNUES:
                counts = par_combo.get((departement, type_batiment, forme), {})
                n_concordant = counts.get("concordant", 0)
                n_ecart = counts.get("ecart", 0)
                n_non_evaluable = counts.get("non_evaluable", 0)
                effectif = n_concordant + n_ecart + n_non_evaluable
                evaluables = n_concordant + n_ecart
                pct = f"{(n_ecart / evaluables * 100):.1f}%" if evaluables else "n.d."
                lignes.append(
                    (
                        departement,
                        type_batiment,
                        forme,
                        effectif,
                        n_concordant,
                        n_ecart,
                        n_non_evaluable,
                        pct,
                    )
                )
    return lignes


def _bloc2_voie_lignes(
    bloc2_voie: Sequence[Mapping[str, Any]], departements: Sequence[str]
) -> list[tuple[Any, ...]]:
    """Same pivot as ``_bloc2_numero_lignes`` for the voie comparison, plus
    the corrupted-escape sub-count (Pitfall 9) carried alongside each
    verdict -- never merged silently into "absente"."""
    par_combo: dict[tuple[str, str, str], dict[str, tuple[int, int]]] = {}
    for row in bloc2_voie:
        forme = _forme_cle(row["segments"])
        cle = (row["code_departement"], row["type_batiment"], forme)
        par_combo.setdefault(cle, {})[row["verdict"]] = (
            row["effectif"],
            row["effectif_corrompu"],
        )
    lignes: list[tuple[Any, ...]] = []
    for departement in sorted(departements):
        for type_batiment in TYPES_BATIMENT_CONNUS:
            for forme in FORMES_CLE_CONNUES:
                counts = par_combo.get((departement, type_batiment, forme), {})
                n_presente, corr_presente = counts.get("presente", (0, 0))
                n_absente, corr_absente = counts.get("absente", (0, 0))
                n_non_evaluable, corr_non_evaluable = counts.get(
                    "non_evaluable", (0, 0)
                )
                effectif = n_presente + n_absente + n_non_evaluable
                evaluables = n_presente + n_absente
                pct = f"{(n_absente / evaluables * 100):.1f}%" if evaluables else "n.d."
                effectif_corrompu = corr_presente + corr_absente + corr_non_evaluable
                lignes.append(
                    (
                        departement,
                        type_batiment,
                        forme,
                        effectif,
                        n_presente,
                        n_absente,
                        n_non_evaluable,
                        pct,
                        effectif_corrompu,
                    )
                )
    return lignes


def _bloc2_statut_geocodage_lignes(
    bloc2_statut: Sequence[Mapping[str, Any]], departements: Sequence[str]
) -> list[tuple[Any, ...]]:
    """Cross both verdicts with statut_geocodage (RESEARCH Sec.3.3 anomaly).
    Zero-fills every KNOWN (departement, type_batiment, verdict) combo over
    the statuts OBSERVED in this run (statut_geocodage's own vocabulary is
    not hard-coded -- only its known 2 values per RESEARCH, but a future
    third value must not disappear silently)."""
    par_combo: dict[tuple[str, str, str, str, str], Mapping[str, Any]] = {}
    statuts: set[str] = set()
    for row in bloc2_statut:
        statut = row["statut_geocodage"] or "n.d."
        statuts.add(statut)
        cle = (
            row["code_departement"],
            row["type_batiment"],
            statut,
            row["verdict_numero"],
            row["verdict_voie"],
        )
        par_combo[cle] = row
    lignes: list[tuple[Any, ...]] = []
    for departement in sorted(departements):
        for type_batiment in TYPES_BATIMENT_CONNUS:
            for statut in sorted(statuts):
                for verdict_numero in VERDICTS_NUMERO_CONNUS:
                    for verdict_voie in VERDICTS_VOIE_CONNUS:
                        row = par_combo.get(
                            (
                                departement,
                                type_batiment,
                                statut,
                                verdict_numero,
                                verdict_voie,
                            )
                        )
                        effectif = row["effectif"] if row else 0
                        score_median = row["score_ban_median"] if row else None
                        lignes.append(
                            (
                                departement,
                                type_batiment,
                                statut,
                                verdict_numero,
                                verdict_voie,
                                effectif,
                                score_median,
                            )
                        )
    return lignes


def _bloc2_score_ban_palier_lignes(
    bloc2_palier: Sequence[Mapping[str, Any]], departements: Sequence[str]
) -> list[tuple[Any, ...]]:
    """Coverage cost of the composite gate (D-09 gate 1) per score_ban
    palier -- same methodology as ``_bloc3_couverture_lignes`` (D-03 b):
    'combien de lignes de ce verdict tombent sous ce palier'."""
    par_combo: dict[tuple[str, str, str, float], Mapping[str, Any]] = {}
    for row in bloc2_palier:
        cle = (
            row["code_departement"],
            row["type_batiment"],
            row["gate_globale"],
            float(row["palier"]),
        )
        par_combo[cle] = row
    lignes: list[tuple[Any, ...]] = []
    for departement in sorted(departements):
        for type_batiment in TYPES_BATIMENT_CONNUS:
            for gate in GATES_GLOBALES_CONNUES:
                for palier in PALIERS_SCORE_BAN:
                    row = par_combo.get(
                        (departement, type_batiment, gate, float(palier))
                    )
                    effectif = row["effectif"] if row else 0
                    au_dessus = row["n_au_dessus_du_palier"] if row else 0
                    sous = effectif - au_dessus
                    pct = f"{(sous / effectif * 100):.1f}%" if effectif else "n.d."
                    lignes.append(
                        (departement, type_batiment, gate, palier, effectif, sous, pct)
                    )
    return lignes


def _bloc3_distribution_lignes(
    bloc3: Sequence[Mapping[str, Any]], departements: Sequence[str]
) -> list[tuple[Any, ...]]:
    """One row per (departement, type_batiment, forme_cle) -- min/max/deciles
    are identical across paliers within a combo, so the first row wins."""
    par_combo: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for row in bloc3:
        forme = _forme_cle(row["segments"])
        cle = (row["code_departement"], row["type_batiment"], forme)
        par_combo.setdefault(cle, row)
    lignes: list[tuple[Any, ...]] = []
    for departement in sorted(departements):
        for type_batiment in TYPES_BATIMENT_CONNUS:
            for forme in FORMES_CLE_CONNUES:
                row = par_combo.get((departement, type_batiment, forme))
                if row is None:
                    lignes.append(
                        (
                            departement,
                            type_batiment,
                            forme,
                            0,
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                            0,
                        )
                    )
                    continue
                lignes.append(
                    (
                        departement,
                        type_batiment,
                        forme,
                        row["effectif"],
                        row["score_min"],
                        row["score_max"],
                        row["score_moyenne"],
                        row["decile_10"],
                        row["decile_20"],
                        row["decile_30"],
                        row["decile_40"],
                        row["decile_50"],
                        row["decile_60"],
                        row["decile_70"],
                        row["decile_80"],
                        row["decile_90"],
                        row["score_ban_null"],
                    )
                )
    return lignes


def _bloc3_couverture_lignes(
    bloc3: Sequence[Mapping[str, Any]], departements: Sequence[str]
) -> list[tuple[Any, ...]]:
    """Coverage cost per (departement, forme_cle, palier), maisons only --
    D-03 (b): 'combien de maisons a cle nominale perdraient exact a ce
    palier'. Zero-aware over every KNOWN (departement, forme, palier) combo
    (Task 2)."""
    par_combo: dict[tuple[str, str, float], Mapping[str, Any]] = {}
    for row in bloc3:
        if row["type_batiment"] != "maison":
            continue
        forme = _forme_cle(row["segments"])
        par_combo[(row["code_departement"], forme, float(row["palier"]))] = row
    lignes: list[tuple[Any, ...]] = []
    for departement in sorted(departements):
        for forme in FORMES_CLE_CONNUES:
            for palier in PALIERS_SCORE_BAN:
                row = par_combo.get((departement, forme, float(palier)))
                if row is None:
                    lignes.append((departement, forme, palier, 0, 0, "n.d."))
                    continue
                effectif = row["effectif"]
                au_dessus = row["n_au_dessus_du_palier"]
                perdraient = effectif - au_dessus
                pct = f"{(perdraient / effectif * 100):.1f}%" if effectif else "n.d."
                lignes.append((departement, forme, palier, effectif, perdraient, pct))
    return lignes


def _bloc4_lignes(
    bloc4: Sequence[Mapping[str, Any]], departements: Sequence[str]
) -> list[tuple[Any, ...]]:
    par_combo = {(row["code_departement"], row["classe"]): row for row in bloc4}
    lignes: list[tuple[Any, ...]] = []
    for departement in sorted(departements):
        for classe in CLASSES_COLLISION:
            row = par_combo.get((departement, classe))
            nb_cles = row["nb_cles"] if row else 0
            nb_lignes = row["nb_lignes"] if row else 0
            lignes.append((departement, classe, nb_cles, nb_lignes))
    return lignes


def _bloc5_type_lignes(
    bloc5_type: Sequence[Mapping[str, Any]], departements: Sequence[str]
) -> list[tuple[Any, ...]]:
    """One row per (departement, type_batiment, forme_cle) -- zero-aware over
    every KNOWN combination; an observed type_batiment outside
    TYPES_BATIMENT_TOUS is appended (sorted) rather than dropped."""
    par_combo: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    autres: list[tuple[Any, ...]] = []
    for row in bloc5_type:
        forme = _forme_cle(row["segments"])
        if row["type_batiment"] not in TYPES_BATIMENT_TOUS:
            autres.append(
                (row["code_departement"], row["type_batiment"], forme, row["effectif"])
            )
            continue
        par_combo[(row["code_departement"], row["type_batiment"], forme)] = row
    lignes: list[tuple[Any, ...]] = []
    for departement in sorted(departements):
        for type_batiment in TYPES_BATIMENT_TOUS:
            for forme in FORMES_CLE_CONNUES:
                row = par_combo.get((departement, type_batiment, forme))
                effectif = row["effectif"] if row else 0
                lignes.append((departement, type_batiment, forme, effectif))
    lignes.extend(sorted(autres))
    return lignes


def _bloc5_anteriorite_lignes(
    bloc5_anteriorite: Sequence[Mapping[str, Any]], departements: Sequence[str]
) -> list[tuple[Any, ...]]:
    par_dept = {row["code_departement"]: row for row in bloc5_anteriorite}
    lignes: list[tuple[Any, ...]] = []
    for departement in sorted(departements):
        row = par_dept.get(departement)
        couverts = row["couverts"] if row else 0
        anterieur = row["dpe_anterieur"] if row else 0
        pct = f"{(anterieur / couverts * 100):.1f}%" if couverts else "n.d."
        lignes.append((departement, couverts, anterieur, pct))
    return lignes


def _bloc7_lignes(
    bloc7: Sequence[Mapping[str, Any]], departements: Sequence[str]
) -> list[tuple[Any, ...]]:
    par_combo = {(row["code_departement"], row["classe"]): row for row in bloc7}
    lignes: list[tuple[Any, ...]] = []
    for departement in sorted(departements):
        for classe in CLASSES_GATE_EVALUABILITE:
            row = par_combo.get((departement, classe))
            effectif = row["effectif"] if row else 0
            lignes.append((departement, classe, effectif))
    return lignes


def _bloc8_lignes(
    bloc8: Sequence[Mapping[str, Any]], departements: Sequence[str]
) -> list[tuple[Any, ...]]:
    par_dept = {row["code_departement"]: row for row in bloc8}
    lignes: list[tuple[Any, ...]] = []
    for departement in sorted(departements):
        row = par_dept.get(departement)
        if row is None:
            lignes.append((departement, 0, None, None, None, None, None))
            continue
        lignes.append(
            (
                departement,
                row["population_comparee"],
                row["decile_10"],
                row["decile_30"],
                row["decile_50"],
                row["decile_70"],
                row["decile_90"],
            )
        )
    return lignes


def _bloc6_lignes(
    bloc6: Sequence[Mapping[str, Any]], departements: Sequence[str]
) -> list[tuple[Any, ...]]:
    par_dept = {row["code_departement"]: row for row in bloc6}
    lignes: list[tuple[Any, ...]] = []
    for departement in sorted(departements):
        row = par_dept.get(departement)
        if row is None:
            lignes.append((departement,) + (None,) * 7)
            continue
        lignes.append(
            (
                departement,
                row.get("effectif_neuf_ademe"),
                row.get("collisions_numero_dpe"),
                row.get("neuf_sans_existant"),
                row.get("neuf_et_existant"),
                row.get("relies_par_remplace"),
                row.get("ecart_jours_median"),
                row.get("ecart_surface_median"),
            )
        )
    return lignes


def _bloc10_lignes(
    bloc10: Sequence[Mapping[str, Any]], departements: Sequence[str]
) -> list[tuple[Any, ...]]:
    par_dept = {row["code_departement"]: row for row in bloc10}
    lignes: list[tuple[Any, ...]] = []
    for departement in sorted(departements):
        row = par_dept.get(departement)
        effectif_brut = row["effectif_brut_dpe"] if row else 0
        effectif_maison = row["effectif_maison"] if row else 0
        sans_cle = row["maison_sans_cle_ban"] if row else 0
        lignes.append((departement, effectif_brut, effectif_maison, sans_cle))
    return lignes


def _resolve_d11_lignes(
    resolve_a: Sequence[Mapping[str, Any]],
    resolve_b: Sequence[Mapping[str, Any]],
    departements: Sequence[str],
) -> list[tuple[Any, ...]]:
    par_dept_a = {row["code_departement"]: row for row in resolve_a}
    par_dept_b = {row["code_departement"]: row for row in resolve_b}
    lignes: list[tuple[Any, ...]] = []
    for departement in sorted(departements):
        row_a = par_dept_a.get(departement)
        row_b = par_dept_b.get(departement)
        lignes.append(
            (
                departement,
                row_a["population_numerotee"] if row_a else 0,
                row_a["avec_lieu_dit_meme_voie"] if row_a else 0,
                row_b["population_lieu_dit"] if row_b else 0,
                row_b["avec_numerotee_meme_voie"] if row_b else 0,
            )
        )
    return lignes


def _rendre_markdown(donnees: Mapping[str, Any]) -> str:
    """Render the full 10-block report from a (possibly partial) report
    dict. Every block that Task 1 does not measure yet renders its title and
    the "not yet measured" note -- never disappears (Task 2 acceptance)."""
    meta = donnees["meta"]
    departements = meta["departements"]

    lignes_bloc1 = _bloc1_lignes(donnees.get("bloc1", []), departements)
    bloc2 = donnees.get("bloc2") or {}
    lignes_bloc2_numero = _bloc2_numero_lignes(bloc2.get("numero", []), departements)
    lignes_bloc2_voie = _bloc2_voie_lignes(bloc2.get("voie", []), departements)
    lignes_bloc2_statut = _bloc2_statut_geocodage_lignes(
        bloc2.get("statut_geocodage", []), departements
    )
    lignes_bloc2_palier = _bloc2_score_ban_palier_lignes(
        bloc2.get("score_ban_palier", []), departements
    )
    lignes_bloc3_distribution = _bloc3_distribution_lignes(
        donnees.get("bloc3", []), departements
    )
    lignes_bloc3_couverture = _bloc3_couverture_lignes(
        donnees.get("bloc3", []), departements
    )
    lignes_bloc4 = _bloc4_lignes(donnees.get("bloc4", []), departements)
    bloc5 = donnees.get("bloc5")
    lignes_bloc6 = _bloc6_lignes(donnees.get("bloc6", []), departements)
    lignes_bloc7 = _bloc7_lignes(donnees.get("bloc7", []), departements)
    lignes_bloc8 = _bloc8_lignes(donnees.get("bloc8", []), departements)
    lignes_bloc10 = _bloc10_lignes(donnees.get("bloc10", []), departements)
    resolve_d11 = donnees.get("resolve_d11") or {}
    lignes_resolve_d11 = _resolve_d11_lignes(
        resolve_d11.get("numerotee_avec_lieu_dit", []),
        resolve_d11.get("lieu_dit_avec_numerotee", []),
        departements,
    )
    message_neuf_indisponible = next(
        (row.get("message") for row in donnees.get("bloc6", []) if row.get("message")),
        None,
    )

    parties = [
        "# Phase 58 -- Mesure du rattachement DPE maison (44, 85)",
        "",
        f"Genere le {meta['jour']}. Departements mesures : "
        f"{', '.join(departements)}. Etiquette : {meta['etiquette']}.",
        f"Tete Alembic : {meta.get('tete_alembic') or 'n.d.'}.",
        "",
        "Rapport produit par `scripts/mesure_dpe_maison.py` (D-02, D-03, D-04, "
        "D-05, D-09, D-25) -- lecture seule, chiffres agreges uniquement -- "
        "aucune adresse, aucune cle BAN, aucun numero de DPE.",
        "",
        "> **Ce que ce rapport ne dit pas.** Aucun seuil n'est propose, "
        "aucune gate n'est recommandee, aucune tolerance de surface n'est "
        "suggeree : chaque table ci-dessous chiffre une distribution ou un "
        "cout de couverture, jamais un seuil retenu -- le choix (ou le "
        "rejet) d'un seuil, d'une gate ou d'une tolerance appartient "
        "exclusivement au checkpoint D-06 (58-09). L'anomalie "
        "`statut_geocodage` (section 2.3) est mesuree mais NON expliquee "
        "(confiance BASSE, RESEARCH Sec.3.3/22) -- elle n'est pas, a ce "
        "stade, un signal de qualite exploitable.",
        "",
        "## 1. Volumes maison par departement x forme de cle BAN",
        _table_markdown(
            ["departement", "forme_cle", "effectif", "joignant_adresses"],
            lignes_bloc1,
        ),
        "",
        "## 2. Signal (a) -- concordance adresse brute ADEME <-> libelle BAN"
        " (D-02, D-09 gate 1)",
        "### 2.1 Numero (premier entier en tete d'adresse_brut vs numero_voie_ban)",
        _table_markdown(
            [
                "departement",
                "type_batiment",
                "forme_cle",
                "effectif",
                "concordant",
                "ecart",
                "non_evaluable",
                "ecart_pct",
            ],
            lignes_bloc2_numero,
        ),
        "",
        "### 2.2 Voie (nom_rue_ban normalise recherche dans adresse_brut normalise)",
        _table_markdown(
            [
                "departement",
                "type_batiment",
                "forme_cle",
                "effectif",
                "presente",
                "absente",
                "non_evaluable",
                "absente_pct",
                "dont_corrompues",
            ],
            lignes_bloc2_voie,
        ),
        "",
        "`dont_corrompues` : lignes portant la sequence d'echappement "
        "corrompue observee sur le libelle BAN (Pitfall 9, ex. "
        '`"Beno*u00eet"` pour `"Benoit"`) -- comptees a part, jamais '
        "fondues silencieusement dans `absente`.",
        "",
        "### 2.3 Croisement des deux comparaisons avec statut_geocodage",
        _table_markdown(
            [
                "departement",
                "type_batiment",
                "statut_geocodage",
                "verdict_numero",
                "verdict_voie",
                "effectif",
                "score_ban_median",
            ],
            lignes_bloc2_statut,
        ),
        "",
        "A lire avec le bloc 3 : si les lignes `statut_geocodage` "
        '"non geocodee" ne montrent pas MOINS de concordance que les '
        '"geocodee" malgre un `score_ban` median plus eleve (RESEARCH '
        "Sec.3.3), `statut_geocodage` ne discrimine rien et ne peut pas "
        "fonder une gate.",
        "",
        "### 2.4 Gate globale (D-09 gate 1) par palier de score_ban",
        _table_markdown(
            [
                "departement",
                "type_batiment",
                "gate_globale",
                "palier",
                "effectif",
                "perdraient_le_palier",
                "perdraient_pct",
            ],
            lignes_bloc2_palier,
        ),
        "",
        "`gate_globale` = concordant exige les DEUX comparaisons (2.1 et "
        "2.2) evaluees ET passees -- aucun de ces paliers n'est mis en "
        "avant par ce rapport, le choix (ou le rejet) du seuil appartient "
        "au checkpoint D-06.",
        "",
        "## 3. Signal (b) -- distribution score_ban (D-09 gates 1/2/3)",
        "### 3.1 Distribution (deciles)",
        _table_markdown(
            [
                "departement",
                "type_batiment",
                "forme_cle",
                "effectif",
                "min",
                "max",
                "moyenne",
                "d10",
                "d20",
                "d30",
                "d40",
                "d50",
                "d60",
                "d70",
                "d80",
                "d90",
                "score_ban_null",
            ],
            lignes_bloc3_distribution,
        ),
        "",
        "### 3.2 Table de couverture par palier (maisons a cle nominale)",
        _table_markdown(
            [
                "departement",
                "forme_cle",
                "palier",
                "effectif",
                "perdraient_exact",
                "perdraient_pct",
            ],
            lignes_bloc3_couverture,
        ),
        "",
        "Aucun de ces paliers n'est mis en avant par ce rapport : le choix "
        "(ou le rejet) du seuil score_ban appartient au checkpoint D-06.",
        "",
        "## 4. Signal (c) -- collisions de cle (DPE maison terminaux par cle BAN)",
        _table_markdown(
            ["departement", "classe_effectif", "nb_cles", "nb_lignes"],
            lignes_bloc4,
        ),
        "",
        "## 5. Signal (d) -- coherence type_batiment / mutation DVF recente",
        "### 5.1 type_batiment x forme de cle (toutes valeurs, D-10)",
        _table_markdown(
            ["departement", "type_batiment", "forme_cle", "effectif"],
            _bloc5_type_lignes((bloc5 or {}).get("type_batiment", []), departements),
        ),
        "",
        "### 5.2 DPE maison terminal anterieur a la derniere mutation de sa parcelle",
        _table_markdown(
            ["departement", "couverts", "dpe_anterieur", "part"],
            _bloc5_anteriorite_lignes(
                (bloc5 or {}).get("anteriorite", []), departements
            ),
        ),
        "",
        "`couverts` = population dont la parcelle canonique a ete resolue "
        "(adresses -> parcelle) ET porte au moins une mutation -- ce bloc ne "
        "pretend jamais couvrir toute la population maison.",
        "",
        "## 6. Perimetre neuf (D-14/D-15)",
        _table_markdown(
            [
                "departement",
                "effectif_neuf_ademe",
                "collisions_numero_dpe",
                "neuf_sans_existant",
                "neuf_et_existant",
                "relies_par_remplace",
                "ecart_jours_median",
                "ecart_surface_median",
            ],
            lignes_bloc6,
        ),
        "",
        *(
            [f"_source ADEME injoignable : {message_neuf_indisponible}._", ""]
            if message_neuf_indisponible
            else []
        ),
        "`collisions_numero_dpe` doit valoir 0 (Assumption A1) : une valeur "
        "positive est un fait bloquant pour l'ingestion du neuf (contrainte "
        "UNIQUE, migration 006). Sans arete `numero_dpe_remplace` explicite, "
        "deux DPE en coexistence ne sont jamais departageables par la date "
        "(D-15, 51 D-06).",
        "",
        "## 7. Gate (4) -- evaluabilite (mono-local maison sur la parcelle, D-12)",
        _table_markdown(
            ["departement", "classe", "effectif"],
            lignes_bloc7,
        ),
        "",
        "`non_evaluable` (parcelle sans aucune mutation) n'est jamais compte "
        "comme un echec (D-13) -- exact exige que la gate soit evaluable ET "
        "passee.",
        "",
        "## 8. Concordance de surface (D-25)",
        _table_markdown(
            [
                "departement",
                "population_comparee",
                "d10",
                "d30",
                "d50",
                "d70",
                "d90",
            ],
            lignes_bloc8,
        ),
        "",
        "Distribution seule : aucune tolerance n'est proposee par ce "
        "rapport, la bascule est tranchee au checkpoint D-06.",
        "",
        "## D-11. Qualite du match `/v1/resolve`",
        _table_markdown(
            [
                "departement",
                "population_numerotee",
                "avec_lieu_dit_meme_voie",
                "population_lieu_dit",
                "avec_numerotee_meme_voie",
            ],
            lignes_resolve_d11,
        ),
        "",
        "`adresses` ne porte aucune ligne lieu-dit (verifie : 0 sur 1 021 393 "
        "lignes 44/85) -- le signal de lieu-dit utilise ici est la forme de "
        "cle DPE elle-meme (2 segments), jamais une jointure spatiale ou "
        "textuelle sur `adresses`. Si la part `avec_lieu_dit_meme_voie` / "
        "`avec_numerotee_meme_voie` est faible, la gate resolve est "
        "consignee comme non-cause et n'entre pas au checkpoint (D-11 : "
        "aucune gate resolve d'office).",
        "",
        f"## 9. {BLOCS_NON_MESURES[9]}",
        NOTE_NON_MESURE,
        "",
        "## 10. Controle d'integrite",
        _table_markdown(
            [
                "departement",
                "effectif_brut_dpe",
                "effectif_maison",
                "maison_sans_cle_ban",
            ],
            lignes_bloc10,
        ),
        "",
        "`effectif_maison` de ce tableau doit correspondre a la somme des "
        "effectifs du bloc 1 ; `effectif_neuf_ademe`/`effectif_existant_ademe` "
        "du bloc 6 recoupent l'API ADEME avec la base -- un ecart signale un "
        "dernier run ancien, pas une panne du script (ligne de table, jamais "
        "une assertion qui ferait echouer le script).",
        "",
    ]
    return "\n".join(parties)


CHAMPS_POPULATION_DPE = (
    "lignes_dpe",
    "diagnostics_distincts",
    "numeros_absents",
    "cles_dpe_distinctes",
    "lignes_sans_cle",
    "cles_forme_non_reconnue",
    "cles_commune_seule",
    "lignes_departement_cle_incoherent",
    "cles_dpe_joignant_adresses",
    "cles_dpe_orphelines_dans_departement",
)
CHAMPS_POPULATION_ADRESSES = (
    "lignes_adresses_avec_cle",
    "cles_adresses_distinctes",
    "lignes_adresses_doublons_cle",
    "cles_adresses_avec_dpe_meme_departement",
    "cles_adresses_avec_candidats_api",
)
CHAMPS_CORROBORATION = (
    "code_departement",
    "cles_observees",
    "cles_sans_parcelle_intersectante",
    "cles_plusieurs_parcelles",
    "cles_sans_mutation_observee",
    "cles_avec_mutation_observee",
    "cles_avec_observation_mono_maison",
    "cles_avec_observation_contraire",
    "cles_observations_contradictoires",
    "date_mutation_min",
    "date_mutation_max",
)
CHAMPS_LECTURE = (
    "transaction_read_only",
    "transaction_isolation",
    "statement_timeout",
    "lock_timeout",
    "work_mem",
    "temp_file_limit",
    "parallel_workers",
)
CHAMPS_GRAPHE = (
    "liens_declares",
    "liens_predecesseur_present",
    "references_absentes_base",
    "auto_liens",
    "liens_changement_type",
    "liens_changement_ban",
    "liens_changement_departement",
    "predecesseurs_avec_successeur",
    "predecesseurs_plusieurs_successeurs",
    "references_absentes_avec_plusieurs_successeurs",
    "noeuds_dans_cycle",
    "cycles_touchant_perimetre",
    "noeuds_chaine_reference_absente",
)


def _lire_par_lots(
    conn: psycopg.Connection[Any], sql: str, params: Mapping[str, Any], nom: str
) -> Iterator[dict[str, Any]]:
    """Bound client memory; the same allowlist also guards server cursors."""
    assert_select_only(sql, ALLOWED_SQL)
    with conn.cursor(name=nom) as cursor:
        cursor.execute(sql, params)
        while lignes := cursor.fetchmany(2000):
            yield from lignes


def _statut_simule(cle: str, lignes: Sequence[dict]) -> dict[str, Any]:
    """Replay the actual exact fetch and pure policy, without global repair."""
    ordonnees = sorted(lignes, key=lambda row: row.get("numero_dpe") or "")
    candidats = [
        {champ: row.get(champ) for champ in _ATTRIBUTION_COLS}
        for row in ordonnees[:LIMITE_EXACT]
    ]
    tronque = len(ordonnees) > LIMITE_EXACT
    try:
        attribution = classify_dpe_attribution(cle, candidats, exact_truncated=tronque)
    except (ValidationError, TypeError, ValueError, AttributeError):
        # An invalid source row is unevaluable, never a manufactured absence.
        return {"statut": "inevaluable_classificateur", "tronque": tronque}
    resultat: dict[str, Any] = {
        "statut": attribution.status,
        "raison": attribution.reason,
        "tronque": tronque,
    }
    if attribution.status == "exact":
        diagnostic = attribution.diagnostic
        assert diagnostic is not None
        resultat["type"] = (diagnostic.type_batiment or "").strip().lower()
        resultat["perime"] = diagnostic.perime
        resultat["validite_absente"] = diagnostic.date_fin_validite_dpe is None
    return resultat


def _simuler_exact(lignes: Iterable[dict]) -> dict[str, Counter]:
    """Consume ordered BAN groups, never retaining more than 121 candidates."""
    comptes: dict[str, Counter] = defaultdict(Counter)

    def cle_groupe(row: dict) -> tuple[str, str]:
        return row["departement_reference"], row["identifiant_ban"]

    for (departement, cle), groupe in groupby(lignes, key=cle_groupe):
        statut = _statut_simule(cle, list(groupe))
        compte = comptes[departement]
        compte["cles_avec_candidats_simulees"] += 1
        compte[statut["statut"]] += 1
        compte["cles_lecture_tronquee"] += int(statut["tronque"])
        if statut.get("raison"):
            compte["raison_" + statut["raison"]] += 1
        if statut["statut"] == "exact":
            compte["exact_" + statut["type"]] += 1
            compte["exact_perime"] += int(statut["perime"])
            compte["exact_validite_absente"] += int(statut["validite_absente"])
    return comptes


def _cycles_et_references_absentes(
    noeuds: Mapping[str, dict],
) -> tuple[list[list[str]], set[str]]:
    """Linear functional-graph walk, with no depth cutoff mistaken for a leaf."""
    termines: set[str] = set()
    absents: set[str] = set()
    cycles: list[list[str]] = []
    for depart in noeuds:
        chemin: list[str] = []
        positions: dict[str, int] = {}
        courant = depart
        while courant and courant in noeuds and courant not in termines:
            if courant in positions:
                cycles.append(chemin[positions[courant] :])
                break
            positions[courant] = len(chemin)
            chemin.append(courant)
            courant = noeuds[courant].get("numero_dpe_remplace")
        if courant and (courant not in noeuds or courant in absents):
            absents.update(chemin)
        termines.update(chemin)
    return cycles, absents


def _compter_graphe(  # noqa: C901
    noeuds: Mapping[str, dict], departements: Sequence[str]
) -> dict[str, dict[str, int]]:
    """Count outgoing and incoming edges in the same complete graph."""
    comptes = {dept: Counter() for dept in ["tous", *departements]}
    entrants: Counter = Counter()
    references_absentes: dict[str, list[dict]] = defaultdict(list)

    def ajouter(row: dict, champ: str, effectif: int = 1) -> None:
        comptes["tous"][champ] += effectif
        if row.get("code_departement") in comptes:
            comptes[row["code_departement"]][champ] += effectif

    for numero, row in noeuds.items():
        precedent = row.get("numero_dpe_remplace")
        if not precedent:
            continue
        ajouter(row, "liens_declares")
        if precedent not in noeuds:
            ajouter(row, "references_absentes_base")
            references_absentes[precedent].append(row)
            continue
        ancien = noeuds[precedent]
        entrants[precedent] += 1
        ajouter(row, "liens_predecesseur_present")
        ajouter(row, "auto_liens", int(precedent == numero))
        for champ, dimension in (
            ("type_batiment", "type"),
            ("identifiant_ban", "ban"),
            ("code_departement", "departement"),
        ):
            ajouter(
                row,
                "liens_changement_" + dimension,
                int(row.get(champ) != ancien.get(champ)),
            )
    for numero, effectif in entrants.items():
        ajouter(noeuds[numero], "predecesseurs_avec_successeur")
        ajouter(
            noeuds[numero], "predecesseurs_plusieurs_successeurs", int(effectif > 1)
        )
    for auteurs in references_absentes.values():
        if len(auteurs) > 1:
            champ = "references_absentes_avec_plusieurs_successeurs"
            comptes["tous"][champ] += 1
            for dept in {row.get("code_departement") for row in auteurs}:
                if dept in comptes:
                    comptes[dept][champ] += 1
    cycles, absents = _cycles_et_references_absentes(noeuds)
    for cycle in cycles:
        comptes["tous"]["cycles_touchant_perimetre"] += 1
        for dept in {noeuds[num].get("code_departement") for num in cycle}:
            if dept in comptes:
                comptes[dept]["cycles_touchant_perimetre"] += 1
        for numero in cycle:
            ajouter(noeuds[numero], "noeuds_dans_cycle")
    for numero in absents:
        ajouter(noeuds[numero], "noeuds_chaine_reference_absente")
    return {
        dept: {champ: compte[champ] for champ in CHAMPS_GRAPHE}
        for dept, compte in comptes.items()
    }


def _auditer_remplacements(
    lignes: Iterable[dict], departements: Sequence[str], limite: int
) -> dict[str, Any]:
    """Never call a truncated graph complete or an unloaded reference absent."""
    noeuds: dict[str, dict] = {}
    effectif = 0
    incomplet = False
    numeros_invalides = False
    for row in lignes:
        effectif += 1
        if effectif > limite:
            incomplet = True
            break
        numero = row.get("numero_dpe")
        if not numero or numero in noeuds:
            numeros_invalides = True
        noeuds[numero] = row
    statut = (
        "inconnu_borne_lecture"
        if incomplet
        else "inconnu_numeros_non_uniques"
        if numeros_invalides
        else "complet_base"
    )
    return {
        "statut": statut,
        "lignes_pertinentes_lues": effectif,
        "limite_lignes": limite,
        "mesures": _compter_graphe(noeuds, departements)
        if statut == "complet_base"
        else None,
        "definition": (
            "Liens sur tous types/departements disponibles ; sortants comptes "
            "au successeur, entrants au predecesseur."
        ),
        "limite_source": (
            "Une reference absente de cette base reste inconnue hors base ; "
            "complet_base ne signifie pas complet ADEME."
        ),
    }


def _populations_corrigees(lignes: Sequence[dict]) -> list[dict]:
    """Closed nested projections: neither SQL JSON nor poisoned columns escape."""
    return [
        {
            "code_departement": row["code_departement"],
            "diagnostics": {
                champ: int(row["diagnostics"].get(champ, 0))
                for champ in CHAMPS_POPULATION_DPE
            },
            "adresses": {
                **{
                    champ: int(row["adresses"].get(champ, 0))
                    for champ in CHAMPS_POPULATION_ADRESSES
                },
                "lignes_adresses_sans_cle": row["lignes_adresses_sans_cle"],
            },
        }
        for row in lignes
    ]


def _resultats_simulation(populations: Sequence[dict], comptes: Mapping) -> list[dict]:
    champs = (
        "cles_avec_candidats_simulees",
        "unassigned",
        "exact",
        "multiple",
        "building",
        "inevaluable_classificateur",
        "cles_lecture_tronquee",
        "exact_maison",
        "exact_appartement",
        "exact_perime",
        "exact_validite_absente",
        "raison_not_reported",
        "raison_ambiguous_unresolved",
        "raison_truncated",
        "raison_dwelling_identity_unproven",
    )
    resultats = []
    for population in populations:
        dept = population["code_departement"]
        compte = comptes.get(dept, Counter())
        adresses = population["adresses"]
        attendu = adresses["cles_adresses_avec_candidats_api"]
        # Detect drift or a query contract error rather than publishing a false rate.
        if compte["cles_avec_candidats_simulees"] != attendu:
            raise GoldenSecurityError("population exacte incoherente entre lectures")
        sans_candidat = adresses["cles_adresses_distinctes"] - attendu
        resultats.append(
            {
                "code_departement": dept,
                "denominateur_cles_reference": adresses["cles_adresses_distinctes"],
                "cles_sans_ligne_ban_exacte": sans_candidat,
                **{champ: compte[champ] for champ in champs},
                "absent_sans_contexte_batiment": compte["absent"] + sans_candidat,
            }
        )
    return resultats


def _construire_rapport_corrige(
    conn: psycopg.Connection[Any],
    departements: Sequence[str],
    etiquette: str,
    jour: date,
    *,
    limite_graphe: int = MAX_LIGNES_GRAPHE,
) -> dict[str, Any]:
    params = {
        "departements": list(departements),
        "forme_cle": FORME_CLE_BAN_RE,
        "limite_exact_sentinelle": LIMITE_EXACT + 1,
        "limite_graphe_sentinelle": limite_graphe + 1,
    }
    populations = _populations_corrigees(
        _executer(conn, POPULATIONS_CORRIGEES_SQL, params)
    )
    simulation = _simuler_exact(
        _lire_par_lots(conn, CANDIDATS_EXACT_CORRIGE_SQL, params, "mesure_exact_v2")
    )
    graphe = _auditer_remplacements(
        _lire_par_lots(conn, GRAPHE_REMPLACEMENTS_SQL, params, "mesure_graphe_v2"),
        departements,
        limite_graphe,
    )
    return {
        "meta": {
            "script_sha256_lf": hashlib.sha256(
                Path(__file__).read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest(),
            "schema": VERSION_COUVERTURE,
            "etiquette": etiquette,
            "jour": jour.isoformat(),
            "departements": list(departements),
            "tete_alembic": [
                row["version_num"] for row in _executer(conn, TETE_ALEMBIC_SQL, {})
            ],
            "parametres_lecture": _projeter(
                _executer(conn, PARAMETRES_LECTURE_SQL, {}), CHAMPS_LECTURE
            ),
            "inventaire_base_tous_departements": _projeter(
                _executer(conn, INVENTAIRE_CORRIGE_SQL, {}),
                ("code_departement", "lignes_dpe", "lignes_avec_remplacement"),
            ),
            "sources_externes": {
                "statut": "non_interrogees",
                "effectif_manquant": None,
                "limite": (
                    "Sources et neuf non distingues par cette mesure SQL ; "
                    "aucun total ADEME ou national infere."
                ),
            },
        },
        "definitions": {
            "diagnostics": (
                "Lignes DPE et numeros distincts, tous types, par departement stocke."
            ),
            "adresses": (
                "Referentiel adresses en base ; une adresse de mesure est une "
                "paire (code_departement, ban_id) non vide distincte, "
                "pas un logement ni une ligne technique."
            ),
            "formes": (
                "Forme reconnue = code INSEE + voie, avec numero/suffixe optionnels ; "
                "controle syntaxique uniquement. Commune seule incluse dans forme "
                "non reconnue. Aucune forme ne prouve l'identite."
            ),
            "orphelines": (
                "Cles DPE sans egalite BAN dans adresses du meme departement ; "
                "distinctes des lignes sans cle. Ces populations ne sont pas "
                "simulees comme adresses connues."
            ),
            "simulation": (
                "Classificateur courant a cle BAN de reference fixee : tous types, "
                "departement derive de la cle, tri numero_dpe, 121 lus/120 gardes. "
                "Les statuts sont simules sans contexte batiment. "
                "Erreurs source = inevaluable."
            ),
            "resolve": (
                "Simulation conditionnelle aux lectures reussies ; ni mesure live "
                "/resolve ni adresses effectivement resolues. Geocodage, erreurs "
                "reseau/SQL/RNB, parcelle et modes adresse/GPS/cadastre ne sont pas "
                "rejoues. Cadastre sans BAN reste identity_unavailable. "
                "Une cle sans candidat BAN est comptee dans "
                "absent_sans_contexte_batiment, mais pourrait devenir building "
                "via d'autres cles RNB : aucune absence a l'echelle du batiment "
                "n'est mesuree ici."
            ),
            "exact": (
                "L'exact courant peut inclure un DPE perime ou sans validite ; "
                "aucun seuil ni future gate ajoute. Graphe global audite separement, "
                "jamais injecte dans cette simulation."
            ),
            "dvf": (
                "Corroboration parcellaire observee sur toutes parcelles intersectant "
                "les positions BAN des cles avec DPE. Ni parcelle canonique resolve "
                "ni recensement du stock de logements. Mono-maison historique, "
                "absence et contradictions restent des observations."
            ),
            "taux": (
                "Numerateurs et denominateurs publies sans seuil ; aucune couverture "
                "de logements ou Fiches live calculable ici."
            ),
        },
        "populations": populations,
        "simulation_exact": _resultats_simulation(populations, simulation),
        "remplacements": graphe,
        "corroboration_parcellaire_observee": {
            "statut": "mesure_base",
            "population": "Cles adresses avec DPE de meme departement, tous types",
            "mesures": _projeter(
                _executer(conn, CORROBORATION_DVF_SQL, params), CHAMPS_CORROBORATION
            ),
        },
        "observations_indisponibles": {
            "adresses_resolues_live": None,
            "fiches_exactes_live": None,
            "logements_recenses": None,
            "verdicts_humains": "non_lus_non_modifies",
        },
    }


def _rendre_markdown_corrige(donnees: Mapping[str, Any]) -> str:
    parties = ["# Mesure de couverture DPE corrigee — 58-22-v2", ""]
    for definition, texte in donnees["definitions"].items():
        parties.extend([f"**{definition}** : {texte}", ""])
    for bloc in (
        "meta",
        "populations",
        "simulation_exact",
        "remplacements",
        "corroboration_parcellaire_observee",
        "observations_indisponibles",
    ):
        parties.extend(
            [
                f"## {bloc}",
                "",
                "```json",
                json.dumps(
                    donnees[bloc],
                    cls=_EncodeurDecimal,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                "```",
                "",
            ]
        )
    return "\n".join(parties)


class _EncodeurDecimal(json.JSONEncoder):
    """Serialize the report's Decimal aggregates (quantiles, moyennes) as
    JSON numbers -- required for a diffable JSON artifact (Task 2)."""

    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


# D-04 : 3 signaux stratifies (score_ban bas, collision de cle, lieu-dit) --
# 4 cas/signal/departement = ~24 cas, proche du "~20 cas" demande. Les
# signaux (a) concordance adresse (bloc 2) et gate (4) echouee exigent soit
# R1 (colonnes brutes, NULL avant 58-04) soit la jointure spatiale couteuse
# (bloc 7/8) -- hors perimetre de ce tirage v0, a etendre en 58-05.
ECHANTILLON_LIMITE_PAR_SIGNAL: int = 4
_SIGNAUX_ECHANTILLON: tuple[tuple[str, str], ...] = (
    ("score_ban_bas", ECHANTILLON_SCORE_BAN_BAS_SQL),
    ("collision_cle", ECHANTILLON_COLLISION_SQL),
    ("lieu_dit", ECHANTILLON_LIEU_DIT_SQL),
)

# The sample has two different grains which must never be collapsed:
# - one UI observation per resolved property page;
# - one D-02 documentary verdict per DPE row.
#
# `temporary` is deliberately distinct from the three business outcomes. It
# means "observe again later", never "absent" and never a human verdict.
ETATS_FICHE_ECHANTILLON: tuple[str, ...] = (
    "exact",
    "multiple",
    "building",
    "absent",
    "temporary",
    "sans_fiche",
)
VERDICTS_DOCUMENTAIRES_ECHANTILLON: tuple[str, ...] = (
    "rattachement correct",
    "rattachement faux",
    "indecidable",
)

_CHAMPS_ECHANTILLON: tuple[str, ...] = (
    "code_departement",
    "identifiant_ban",
    "numero_dpe",
    "score_ban",
    "adresse_brut",
    "adresse_ban",
    "numero_voie_ban",
    "nom_rue_ban",
    "statut_geocodage",
    "forme_cle",
    "nombre_dpe_terminaux",
    "adresse_datacommune",
    "date_etablissement_dpe",
    "etiquette_dpe",
)

_RACINE_DEPOT = Path(__file__).resolve().parents[3]
_MOTIF_GITIGNORE_ECHANTILLON = ".planning/**/58-ECHANTILLON*"


def _valider_chemin_echantillon(chemin: str) -> Path:
    """Allow sensitive sample output only under the ignored planning pattern."""
    cible = Path(chemin).expanduser().resolve()
    racine_privee = (_RACINE_DEPOT / ".planning").resolve()
    try:
        cible.relative_to(racine_privee)
    except ValueError as exc:
        raise GoldenSecurityError("--echantillon doit rester sous .planning/") from exc
    if not cible.name.startswith("58-ECHANTILLON") or cible.suffix.lower() != ".md":
        raise GoldenSecurityError(
            "--echantillon exige un fichier Markdown nomme 58-ECHANTILLON*.md"
        )
    gitignore = _RACINE_DEPOT / ".gitignore"
    try:
        motifs = {
            ligne.strip()
            for ligne in gitignore.read_text(encoding="utf-8").splitlines()
        }
    except OSError as exc:
        raise GoldenSecurityError(".gitignore illisible pour --echantillon") from exc
    if _MOTIF_GITIGNORE_ECHANTILLON not in motifs:
        raise GoldenSecurityError(
            "le motif de confidentialite 58-ECHANTILLON manque dans .gitignore"
        )
    return cible


def _formater_adresse_datacommune(row: Mapping[str, Any]) -> str:
    """Mirror the resolver's canonical address label from the selected BAN row."""
    numero = str(row.get("numero_dc") or "")
    repetition = str(row.get("rep_dc") or "")
    nom_voie = str(row.get("nom_voie_dc") or "")
    code_postal = str(row.get("code_postal_dc") or "")
    nom_commune = str(row.get("nom_commune_dc") or "")
    rue = " ".join(part for part in (f"{numero}{repetition}", nom_voie) if part)
    commune = " ".join(part for part in (code_postal, nom_commune) if part)
    if rue and commune:
        return f"{rue}, {commune}"
    return rue or commune


def _slugifier_adresse_echantillon(label: str) -> str:
    """Mirror frontend ``slugifyAdresse`` for a replayable production URL."""
    normalisee = unicodedata.normalize("NFD", label)
    sans_diacritiques = "".join(
        caractere for caractere in normalisee if not "\u0300" <= caractere <= "\u036f"
    )
    return re.sub(r"[^a-z0-9]+", "-", sans_diacritiques.lower()).strip("-")


def _preparer_echantillon(
    strates: Sequence[tuple[str, Sequence[Mapping[str, Any]]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate unique property pages from DPE rows without judging either.

    A collision stratum legitimately contains several diagnostics attached to
    the same BAN identity. Those rows remain distinct documentary cases, but
    share one ``fiche_ref`` so the live page is opened only once. A diagnostic
    selected by several signals is rendered once with all its signals.
    """
    fiches_par_identite: dict[tuple[str, str], dict[str, Any]] = {}
    cas_par_diagnostic: dict[tuple[str, str], dict[str, Any]] = {}

    for signal, lignes in strates:
        for row in lignes:
            departement = str(row["code_departement"])
            identifiant_ban = str(row["identifiant_ban"])
            identite_fiche = (departement, identifiant_ban)
            fiche = fiches_par_identite.get(identite_fiche)
            if fiche is None:
                adresse_datacommune = _formater_adresse_datacommune(row)
                slug = _slugifier_adresse_echantillon(adresse_datacommune)
                fiche = {
                    "fiche_ref": f"F-{len(fiches_par_identite) + 1:02d}",
                    "code_departement": departement,
                    "identifiant_ban": identifiant_ban,
                    "adresse_datacommune": adresse_datacommune or "ABSENT",
                    "url_fiche": (
                        f"https://datacommune.fr/bien/{slug}?type_bien=maison"
                        if slug
                        else ""
                    ),
                    "etat_fiche": "" if adresse_datacommune else "sans_fiche",
                    "observe_le": "",
                }
                fiches_par_identite[identite_fiche] = fiche

            identite_diagnostic = (departement, str(row["numero_dpe"]))
            cas = cas_par_diagnostic.get(identite_diagnostic)
            if cas is None:
                cas = {
                    champ: (
                        _formater_adresse_datacommune(row)
                        if champ == "adresse_datacommune"
                        else row.get(champ)
                    )
                    for champ in _CHAMPS_ECHANTILLON
                }
                cas.update(
                    {
                        "fiche_ref": fiche["fiche_ref"],
                        "signaux": [signal],
                        "verdict_documentaire": "",
                    }
                )
                cas_par_diagnostic[identite_diagnostic] = cas
            elif signal not in cas["signaux"]:
                cas["signaux"].append(signal)

    return list(cas_par_diagnostic.values()), list(fiches_par_identite.values())


def _tirer_echantillon(
    conn: psycopg.Connection[Any], departements: Sequence[str], chemin: str
) -> None:
    """D-04: write the ~20-case stratified sample for the human verdict.

    The ONLY function in this module whose queries project an identifier
    (T-58-10/T-58-11) -- called ONLY when ``--echantillon`` is passed, NEVER
    from ``_construire_rapport``/``_rendre_markdown``, so the main report
    (``--sortie``/``--json``) stays identifier-free even when this option is
    used. Runs each signal once PER DEPARTMENT (44/85 balanced) so no single
    department dominates the sample.
    """
    chemin_prive = _valider_chemin_echantillon(chemin)
    strates: list[tuple[str, Sequence[Mapping[str, Any]]]] = []
    for nom_signal, sql in _SIGNAUX_ECHANTILLON:
        for dept in departements:
            params = {
                "departements": [dept],
                "limite": ECHANTILLON_LIMITE_PAR_SIGNAL,
            }
            strates.append((nom_signal, _executer(conn, sql, params)))

    cas, fiches = _preparer_echantillon(strates)
    parties = [
        "# Phase 58 -- Echantillon stratifie D-04 (NON VERSIONNE)",
        "",
        "**Ce fichier porte des identifiants (cle BAN, numero de DPE, "
        "adresse BAN) -- c'est le SEUL artefact du script "
        "`mesure_dpe_maison.py` dans ce cas. Ne JAMAIS le committer** "
        "(motif `.gitignore` : `58-ECHANTILLON*`). Le rapport principal "
        "(`--sortie`/`--json`) n'en porte aucun.",
        "",
        "3-4 cas par signal, par departement (44/85). `adresse_ban` peut "
        "valoir `n.d.` sur une instance ou 58-04 (colonnes brutes ADEME) "
        "n'a pas encore tourne.",
        "",
        "## Deux colonnes independantes",
        "",
        "`etat_fiche` est une observation technique par Fiche unique : "
        + ", ".join(f"`{etat}`" for etat in ETATS_FICHE_ECHANTILLON)
        + ". `building` et `absent` sont des etats metier sans DPE individuel ; "
        "`temporary` est une panne transitoire a reobserver, jamais une absence ; "
        "`sans_fiche` signifie que la cle ne joint aucune adresse DataCommune et "
        "qu'aucune URL ne doit etre ouverte.",
        "",
        "`verdict_documentaire` est rendu par un humain pour chaque diagnostic "
        "en comparant l'adresse saisie par le diagnostiqueur au libelle BAN. "
        "Valeurs admises : "
        + ", ".join(f"`{verdict}`" for verdict in VERDICTS_DOCUMENTAIRES_ECHANTILLON)
        + ". L'etat de la Fiche ne remplit et ne suggere jamais ce verdict.",
        "",
        "## Fiches uniques a observer",
        "",
        _table_markdown(
            [
                "fiche_ref",
                "code_departement",
                "identifiant_ban",
                "adresse_datacommune",
                "url_fiche",
                "etat_fiche",
                "observe_le",
            ],
            [
                tuple(
                    fiche[champ]
                    for champ in (
                        "fiche_ref",
                        "code_departement",
                        "identifiant_ban",
                        "adresse_datacommune",
                        "url_fiche",
                        "etat_fiche",
                        "observe_le",
                    )
                )
                for fiche in fiches
            ],
        ),
        "",
        "Ouvrir chaque Fiche une seule fois. Respecter le rythme du runbook "
        "du checkpoint ; en cas de `temporary`, attendre puis reessayer au "
        "lieu d'inscrire `absent`.",
        "",
        "## Diagnostics a juger (D-02)",
        "",
        _table_markdown(
            [
                "signaux",
                "fiche_ref",
                *_CHAMPS_ECHANTILLON,
                "verdict_documentaire",
            ],
            [
                (
                    ",".join(cas_ligne["signaux"]),
                    cas_ligne["fiche_ref"],
                    *(cas_ligne[champ] for champ in _CHAMPS_ECHANTILLON),
                    cas_ligne["verdict_documentaire"],
                )
                for cas_ligne in cas
            ],
        ),
        "",
    ]
    chemin_prive.write_text("\n".join(parties), encoding="utf-8")


def _liste_departements(valeur: str) -> list[str]:
    departements = [item.strip() for item in valeur.split(",") if item.strip()]
    if not departements:
        raise argparse.ArgumentTypeError("au moins un departement est requis")
    return departements


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--couverture-corrigee",
        action="store_true",
        help="Mesure 58-22-v2 agregee, sans appel ADEME ni corpus humain.",
    )
    parser.add_argument(
        "--max-lignes-graphe",
        type=int,
        default=MAX_LIGNES_GRAPHE,
        help=(
            "Borne memoire des lignes pertinentes du graphe v2 ; depassement = inconnu."
        ),
    )
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
    parser.add_argument(
        "--sources-ademe",
        type=_liste_departements,
        default=["dpe03existant", "dpe02neuf"],
        help=(
            "Slugs data-fair ADEME a compter HORS BASE pour le bloc 6/10 "
            "(defaut: dpe03existant,dpe02neuf). Comptage size=0 seulement, "
            "jamais un telechargement complet (T-58-12)."
        ),
    )
    parser.add_argument(
        "--echantillon",
        help=(
            "Chemin du fichier d'echantillon stratifie D-04 (~20 cas, 3-5 "
            "par signal, 44/85). SEUL artefact du script portant des "
            "identifiants (cle BAN, numero_dpe) -- accepte uniquement sous "
            ".planning/ avec un nom 58-ECHANTILLON*.md, donc couvert par "
            ".gitignore. Le rapport principal (--sortie/--json) n'en "
            "porte aucun, meme quand cette option est utilisee."
        ),
    )
    args = parser.parse_args(argv)
    if args.max_lignes_graphe < 1:
        parser.error("--max-lignes-graphe doit etre positif")
    if args.couverture_corrigee and args.echantillon:
        parser.error("--couverture-corrigee est incompatible avec --echantillon")
    return args


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
        if args.couverture_corrigee:
            # The helper already verified read-only. Start one stable snapshot
            # for all v2 denominators without changing its write prohibition.
            conn.rollback()
            conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            donnees = _construire_rapport_corrige(
                conn,
                args.departements,
                args.etiquette,
                datetime.now(UTC).date(),
                limite_graphe=args.max_lignes_graphe,
            )
        else:
            donnees = _construire_rapport(
                conn,
                args.departements,
                args.etiquette,
                datetime.now(UTC).date(),
                recuperer_neuf=functools.partial(
                    _recuperer_perimetre_neuf_ademe, sources=args.sources_ademe
                ),
            )
        if args.echantillon:
            _tirer_echantillon(conn, args.departements, args.echantillon)
    finally:
        conn.rollback()
        conn.close()

    markdown = (
        _rendre_markdown_corrige(donnees)
        if args.couverture_corrigee
        else _rendre_markdown(donnees)
    )

    if args.sortie:
        Path(args.sortie).write_text(markdown, encoding="utf-8")
    else:
        print(markdown)

    if args.chemin_json:
        Path(args.chemin_json).write_text(
            json.dumps(
                donnees,
                cls=_EncodeurDecimal,
                sort_keys=True,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (GoldenSecurityError, psycopg.Error, OSError, ValueError):
        print("mesure DPE maison bloquee", file=sys.stderr)
        raise SystemExit(1) from None
