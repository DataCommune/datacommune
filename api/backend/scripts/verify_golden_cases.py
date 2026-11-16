#!/usr/bin/env python3
"""Golden-case live verifier and offline artifact auditor.

The CLI is opt-in and lives outside ``tests/``. Live mode accepts no SQL, URL,
HTTP method, credential, or private payload on the command line. Credentials
and the private JSON corpus are read from named environment variables. All SQL
is selected from an internal allowlist, uses bind parameters, and runs on a
PostgreSQL transaction whose ``transaction_read_only`` state is verified before
case queries. HTTP replay uses ``httpx.Client.get`` exclusively.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import inspect
import json
import os
import re
import sys
import time
import unicodedata
from collections.abc import Callable, Collection, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import httpx
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

# Support the documented direct invocation from ``api/backend`` (the workflow
# runs ``python scripts/verify_golden_cases.py``) while keeping package imports
# identical when the module is loaded by pytest or by the selector CLI.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Single source of truth for the production attachment constants (R3-01):
# importing them instead of copying them makes any future recalibration of the
# fallback radius or the snap margin flow into this measure automatically.
from app.dpe.queries import dpe_par_identifiant_ban
from app.dpe.schemas import DpeConsultedDocument, DpeInventoryItem, DpeInventoryPage
from app.logements.queries import SECOURS_RAYON_M
from app.resolve.snap import SNAP_MARGIN_M

# Preview constants imported, never copied: the smoke marker, the loopback host
# and the SHA route must be identical on both sides or the provenance proof and
# the request-log suppression silently stop working.
from scripts.serve_golden_preview import (
    EXPECTED_SHA_ENV,
    PREVIEW_HOST,
    PREVIEW_SHA_PATH,
    PREVIEW_SMOKE_MARKER,
)

# The full monorepo checkout is 4 levels above this file (api/backend/scripts/
# foo.py -> repo root). The deployed Docker image flattens api/backend/ to
# /app, so only 3 parents exist there -- parents[3] would raise IndexError on
# import. SCHEMA_PATH/REPORT_PATH below are CI-only (verify-golden-live.yml,
# always run from a full checkout) and are never read by any code path that
# executes inside the deployed image (57-08 deviation): fall back to the
# shallowest available parent instead of crashing an import that other
# scripts (mesure_dvf_44_85.py) legitimately reuse for its read-only helpers.
_FILE_PARENTS = Path(__file__).resolve().parents
_REPO_ROOT = _FILE_PARENTS[3] if len(_FILE_PARENTS) > 3 else _FILE_PARENTS[-1]
SCHEMA_PATH = _REPO_ROOT / "contracts" / "golden-cases" / "v1" / "schema.json"
REPORT_PATH = (
    _REPO_ROOT / "api" / "backend" / "reports" / "golden-live-report.redacted.json"
)

COMMUNE_SQL = """
SELECT code_insee, superficie, densite, annee_demographie, annee_revenus,
       annee_fiscalite, annee_loyers
FROM v_commune_synthese
WHERE code_insee = %s
"""
PARCEL_SQL = """
SELECT id_parcelle
FROM parcelles
WHERE id_parcelle = %s AND commune = %s
"""
READ_ONLY_STATE_SQL = "SELECT current_setting('transaction_read_only') AS state"

# Phase 48 (D1/D2/D3) -- multi-logement detection signals, observed by exact
# cadastre parcel. The copropriete filter mirrors
# app.logements.queries.copropriete_par_parcelle (id_parcelle_1, D-20 --
# never code_commune, never an address match). Each statement is a bounded
# aggregate (EXISTS/COUNT), never a row-level SELECT, and takes bind
# parameters only.
#
# R3-01 (2026-08-04): the DPE measure mirrors the CURRENT production
# attachment logic of app.logements.router._charge_dpe /
# app.logements.queries (commits 7ddf0055 + 6df68c3d, ADR-0006):
#   1. PRIMARY path -- exact equality on the BAN interoperability key
#      (diagnostics_dpe.identifiant_ban = ban_id, dpe_appartements_par_ban);
#   2. SPATIAL fallback -- ST_DWithin SECOURS_RAYON_M (12 m) with the resolve
#      snap non-ambiguity rule (SNAP_MARGIN_M, 3 m): a contained point
#      (distance 0) attaches unconditionally, an outside point only when the
#      parcel leads every other parcel by the margin;
#   3. when the key path attaches ZERO rows, the FULL spatial fallback runs;
#      otherwise the fallback is restricted to identifiant_ban IS NULL rows.
# The former ST_Intersects join here was EXACTLY the L1 bug (a DPE position is
# the BAN entrance point, frequently on the roadway outside every parcel) and
# had been false-as-documentation since 7ddf0055. The measure counts the
# attachment RULE, never the fetch budget (production LIMIT 120 is pagination,
# not attachment). Never the 500 m radius of dpe_par_point.
COPRO_ROW_SQL = """
SELECT EXISTS (
    SELECT 1 FROM copropriete WHERE id_parcelle_1 = %(id_parcelle)s
) AS copro_row_present
"""
DPE_APPARTEMENTS_PAR_BAN_SQL = """
SELECT COUNT(*) AS dpe_par_ban_count
FROM diagnostics_dpe d
WHERE d.identifiant_ban = %(ban_id)s
  AND d.code_departement = CASE
      WHEN LEFT(%(ban_id)s, 2) = '97' THEN LEFT(%(ban_id)s, 3)
      ELSE LEFT(%(ban_id)s, 2)
  END
  AND d.type_batiment = 'appartement'
"""
DPE_APPARTEMENTS_SPATIAL_SQL = """
WITH parc AS (
    SELECT geometrie FROM parcelles
    WHERE id_parcelle = %(id_parcelle)s
      AND code_departement = CASE
          WHEN LEFT(%(id_parcelle)s, 2) = '97' THEN LEFT(%(id_parcelle)s, 3)
          ELSE LEFT(%(id_parcelle)s, 2)
      END
    LIMIT 1
),
candidats AS (
    SELECT d.identifiant_ban,
           d.position,
           ST_Distance(
               parc.geometrie::geography, d.position::geography
           ) AS distance_m
    FROM diagnostics_dpe d
    JOIN parc ON ST_DWithin(
        parc.geometrie,
        d.position,
        %(rayon_m)s / (111320.0 * GREATEST(COS(RADIANS(ST_Y(d.position))), 0.01))
    )
    WHERE d.type_batiment = 'appartement'
),
rattaches AS (
    SELECT c.identifiant_ban
    FROM candidats c
    WHERE c.distance_m <= %(rayon_m)s
      AND (
        c.distance_m = 0
        OR NOT EXISTS (
            SELECT 1 FROM parcelles p
            WHERE p.code_departement = CASE
                  WHEN LEFT(%(id_parcelle)s, 2) = '97' THEN LEFT(%(id_parcelle)s, 3)
                  ELSE LEFT(%(id_parcelle)s, 2)
              END
              AND p.id_parcelle <> %(id_parcelle)s
              AND ST_DWithin(
                  p.geometrie,
                  c.position,
                  (c.distance_m + %(marge_m)s)
                  / (111320.0 * GREATEST(COS(RADIANS(ST_Y(c.position))), 0.01))
              )
              AND ST_Distance(p.geometrie::geography, c.position::geography)
                  < c.distance_m + %(marge_m)s
        )
      )
)
SELECT COUNT(*) AS spatial_total,
       COUNT(*) FILTER (WHERE identifiant_ban IS NULL) AS spatial_sans_cle,
       COUNT(*) FILTER (WHERE identifiant_ban = %(ban_id)s) AS spatial_cle_exacte,
       COUNT(*) FILTER (
           WHERE identifiant_ban IS NOT NULL AND identifiant_ban <> %(ban_id)s
       ) AS spatial_cle_orpheline
FROM rattaches
"""
# D-09 threshold: surface >= 10 m2 discards caves/parkings/residual lots.
_DVF_SURFACE_MIN_D09 = 10
DVF_APPARTEMENTS_SQL = """
SELECT EXISTS (
    SELECT 1 FROM mutations
    WHERE id_parcelle = %(id_parcelle)s
      AND type_local = 'Appartement'
      AND surface_reelle_bati >= %(surface_min)s
      AND code_departement = CASE
          WHEN LEFT(%(id_parcelle)s, 2) = '97' THEN LEFT(%(id_parcelle)s, 3)
          ELSE LEFT(%(id_parcelle)s, 2)
      END
) AS dvf_appartement_present
"""
# Compare the two documentary GET projections with their exact-BAN source while
# returning booleans only. Source values never cross the database boundary: the
# JSON objects are built and compared inside PostgreSQL, then discarded.
DPE_DOCUMENTARY_SOURCE_MATCH_SQL = """
WITH source_documents AS MATERIALIZED (
    SELECT d.id,
           d.numero_dpe,
           d.type_batiment,
           d.surface_habitable::double precision AS surface_habitable,
           CASE
               WHEN upper(btrim(d.etiquette_dpe)) IN
                    ('A', 'B', 'C', 'D', 'E', 'F', 'G')
               THEN upper(btrim(d.etiquette_dpe))
           END AS etiquette_dpe,
           CASE
               WHEN upper(btrim(d.etiquette_ges)) IN
                    ('A', 'B', 'C', 'D', 'E', 'F', 'G')
               THEN upper(btrim(d.etiquette_ges))
           END AS etiquette_ges,
           d.date_etablissement_dpe,
           d.date_fin_validite_dpe,
           d.annee_construction,
           d.periode_construction,
           d.type_installation_chauffage,
           d.type_energie_principale_chauffage,
           d.qualite_isolation_enveloppe,
           d.conso_5_usages_ep::double precision AS conso_5_usages_ep,
           d.emission_ges_5_usages::double precision AS emission_ges_5_usages,
           d.cout_total_5_usages::double precision AS cout_total_5_usages
    FROM public.diagnostics_dpe AS d
    WHERE d.identifiant_ban = %(ban_id)s
      AND d.code_departement = %(department)s
),
first_document AS (
    SELECT *
    FROM source_documents
    ORDER BY numero_dpe, id
    LIMIT 1
),
source_aggregate AS (
    SELECT count(*)::bigint AS total,
           jsonb_build_object(
               'A', count(*) FILTER (WHERE etiquette_dpe = 'A'),
               'B', count(*) FILTER (WHERE etiquette_dpe = 'B'),
               'C', count(*) FILTER (WHERE etiquette_dpe = 'C'),
               'D', count(*) FILTER (WHERE etiquette_dpe = 'D'),
               'E', count(*) FILTER (WHERE etiquette_dpe = 'E'),
               'F', count(*) FILTER (WHERE etiquette_dpe = 'F'),
               'G', count(*) FILTER (WHERE etiquette_dpe = 'G'),
               'unknown', count(*) FILTER (WHERE etiquette_dpe IS NULL)
           ) AS distribution
    FROM source_documents
)
SELECT (
           aggregate.total = %(inventory_total)s
           AND aggregate.distribution = %(inventory_distribution)s::jsonb
       ) AS inventory_aggregate_equal,
       CASE WHEN %(has_document)s THEN COALESCE((
           SELECT jsonb_build_object(
               'type_batiment', first.type_batiment,
               'surface_habitable', first.surface_habitable,
               'etiquette_dpe', first.etiquette_dpe,
               'etiquette_ges', first.etiquette_ges,
               'date_etablissement_dpe', first.date_etablissement_dpe,
               'date_fin_validite_dpe', first.date_fin_validite_dpe
           ) = %(inventory_item)s::jsonb
           FROM first_document AS first
       ), false) ELSE NOT EXISTS (SELECT 1 FROM first_document) END
       AS inventory_item_equal,
       CASE WHEN %(has_document)s THEN COALESCE((
           SELECT jsonb_build_object(
               'type_batiment', first.type_batiment,
               'surface_habitable', first.surface_habitable,
               'etiquette_dpe', first.etiquette_dpe,
               'etiquette_ges', first.etiquette_ges,
               'date_etablissement_dpe', first.date_etablissement_dpe,
               'date_fin_validite_dpe', first.date_fin_validite_dpe,
               'annee_construction', first.annee_construction,
               'periode_construction', first.periode_construction,
               'type_installation_chauffage', first.type_installation_chauffage,
               'type_energie_principale_chauffage',
                   first.type_energie_principale_chauffage,
               'qualite_isolation_enveloppe', first.qualite_isolation_enveloppe,
               'conso_5_usages_ep', first.conso_5_usages_ep,
               'emission_ges_5_usages', first.emission_ges_5_usages,
               'cout_total_5_usages', first.cout_total_5_usages
           ) = %(document)s::jsonb
           FROM first_document AS first
       ), false) ELSE true END AS document_equal
FROM source_aggregate AS aggregate
"""
# Independent SQL projection of the persisted 091 facts. The API supplies only
# the claimed public JSON and common date; no private row or edge is returned.
# Old V2 responses never execute this query, so replay before migration keeps
# its original source proof against the original documentary columns.
DPE_LIFECYCLE_SOURCE_MATCH_SQL = """
WITH first_document AS MATERIALIZED (
    SELECT d.* FROM public.diagnostics_dpe AS d
    WHERE d.identifiant_ban = %(ban_id)s
      AND d.code_departement = %(department)s
    ORDER BY d.numero_dpe, d.id LIMIT 1
), expected AS (
    SELECT CASE
        WHEN source_state = 'unknown' AND source_snapshot_at IS NULL
             AND source_observations = '[]'::jsonb AND NOT is_tombstone
             AND lifecycle_facts = '{"replacement_state":"not_checked",
                  "graph_issues":[],"date_issues":[]}'::jsonb
        THEN 'null'::jsonb
        WHEN revision_evidence = '{"status":"unverified","kind":null,
                  "revision_date":null,"verified_at":null,
                  "evidence_sha256":null,"official_source":null}'::jsonb
             AND is_tombstone = (source_state = 'absent_from_complete_scope')
             AND (
                 (source_state = 'unknown' AND source_snapshot_at IS NULL
                  AND source_observations = '[]'::jsonb)
                 OR (source_state IN ('present', 'absent_from_complete_scope')
                     AND source_snapshot_at IS NOT NULL
                     AND NOT EXISTS (
                         SELECT 1 FROM jsonb_array_elements(source_observations) o
                         WHERE (o->>'observed_at')::timestamptz > source_snapshot_at
                     )
                     AND (source_state = 'present') = EXISTS (
                         SELECT 1 FROM jsonb_array_elements(source_observations) o
                         WHERE o->>'state' = 'present'
                     ))
             )
        THEN jsonb_build_object(
            'source_state', source_state,
            'source_snapshot_date',
                (source_snapshot_at AT TIME ZONE 'Europe/Paris')::date,
            'source_scope', '44/85',
            'regimes', COALESCE((
                SELECT jsonb_agg(regime ORDER BY regime) FROM (
                    SELECT DISTINCT CASE observation->>'source_slug'
                        WHEN 'dpe03existant' THEN 'existant'
                        WHEN 'dpe02neuf' THEN 'neuf' END AS regime
                    FROM jsonb_array_elements(source_observations) observation
                    UNION
                    SELECT regime_dpe WHERE source_observations = '[]'::jsonb
                      AND regime_dpe IN ('existant', 'neuf')
                ) regimes
            ), '[]'::jsonb),
            'replacement_state', lifecycle_facts->>'replacement_state',
            'graph_issues', lifecycle_facts->'graph_issues',
            'validity', CASE
                WHEN is_tombstone THEN 'unknown'
                WHEN lifecycle_facts->'date_issues' <> '[]'::jsonb
                     OR date_fin_validite_dpe < date_etablissement_dpe
                THEN 'inconsistent_dates'
                WHEN date_etablissement_dpe > %(as_of_date)s::date
                THEN 'future_establishment'
                WHEN date_etablissement_dpe IS NULL OR date_fin_validite_dpe IS NULL
                THEN 'unknown'
                WHEN date_fin_validite_dpe < %(as_of_date)s::date THEN 'expired'
                ELSE 'not_expired_by_date' END,
            'as_of_date', %(as_of_date)s::date,
            'revision_status', 'unverified', 'revision_kind', NULL,
            'revision_date', NULL,
            'detail_availability', CASE WHEN is_tombstone
                THEN 'source_reconciled' ELSE 'available' END
        ) END AS lifecycle
    FROM first_document
)
SELECT CASE WHEN %(has_document)s THEN COALESCE((
    SELECT lifecycle = %(item_lifecycle)s::jsonb
       AND lifecycle = %(document_lifecycle)s::jsonb FROM expected
), false) ELSE NOT EXISTS (SELECT 1 FROM first_document) END AS lifecycle_equal
"""
ALLOWED_SQL: frozenset[str] = frozenset(
    {
        COMMUNE_SQL,
        PARCEL_SQL,
        READ_ONLY_STATE_SQL,
        COPRO_ROW_SQL,
        DPE_APPARTEMENTS_PAR_BAN_SQL,
        DPE_APPARTEMENTS_SPATIAL_SQL,
        DVF_APPARTEMENTS_SQL,
        DPE_DOCUMENTARY_SOURCE_MATCH_SQL,
        DPE_LIFECYCLE_SOURCE_MATCH_SQL,
    }
)

FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        # R3-01: the BAN interoperability key re-identifies one address; it
        # lives ONLY in the gitignored private input, never in the manifest.
        "ban_id",
        "banid",
        "identifiant_ban",
        "adresse",
        "address",
        "adresse_exacte",
        "adresse_postale",
        "voie",
        "rue",
        "latitude",
        "longitude",
        "lat",
        "lon",
        "lng",
        "gps",
        "coordinates",
        "coordonnees",
        "geocode",
        "api_key",
        "apikey",
        "secret",
        "token",
        "password",
        "credential",
        "payload",
        "payload_raw",
        "response_body",
    }
)
_SENSITIVE_KEY_PARTS = (
    "adresse",
    "address",
    "latitude",
    "longitude",
    "coordinate",
    "coordonnee",
    "geocode",
    "apikey",
    "secret",
    "token",
    "password",
    "credential",
    "payload",
    "responsebody",
)
_SAFE_PRIVATE_REF_KEY = "private_ref"

# --- Phase 58.1: closed, non-attributive DPE vocabulary -----------------------
#
# The golden contract observes the PUBLIC decision ``dpe_attribution`` from
# ``/v1/resolve?datasets=dpe`` (Plan 51-02). ``dpe_state=value`` cannot tell an
# exact attribution from a neighbour, a building aggregate or an outage, so it
# can no longer conclude an archetype.
DPE_CARRIERS: tuple[str, ...] = (
    "dpe_unassigned",
    "dpe_nearer_neighbor_negative",
    "dpe_absent",
    "dpe_multiple_or_building",
)
DPE_ATTRIBUTION_STATUSES: tuple[str, ...] = (
    "unassigned",
    "absent",
    "error",
)
DPE_INVENTORY_PRESENCES: tuple[str, ...] = (
    "present",
    "absent",
    "unknown",
)
DPE_BOOLEAN_INVARIANTS: tuple[str, ...] = (
    "dpe_primary_diagnostic_present",
    "dpe_truncated",
    "dpe_nearby_diagnostic_present",
)
DPE_COUNT_INVARIANTS: tuple[str, ...] = (
    "dpe_candidate_count",
    "dpe_building_count",
)
DPE_INVARIANT_KEYS: tuple[str, ...] = (
    "dpe_inventory_presence",
    "dpe_attribution_status",
    *DPE_BOOLEAN_INVARIANTS,
    *DPE_COUNT_INVARIANTS,
)
# Read from the backend signature rather than copied: the published counts can
# never exceed what ``app.dpe.queries`` is allowed to fetch, and a future
# recalibration of that bound flows in here instead of silently diverging.
DPE_COUNT_MAX: int = int(
    inspect.signature(dpe_par_identifiant_ban).parameters["limit"].default
)
_DPE_INVARIANT_LAYER = "dpe"
_DPE_CARRIER_KEY = "carrier_for"
# Closed set of the coarse Phase 43 dataset states. A value from this set names
# WHY a case diverged without exposing anything about the address behind it --
# the same argument that already lets the HTTP status into a failure reason.
_OBSERVABLE_STATES: frozenset[str] = frozenset(
    {"value", "not_reported", "out_of_coverage", "temporary_error", "not_applicable"}
)
_DPE_INVENTORY_KEYS: frozenset[str] = frozenset(
    {
        "version",
        "state",
        "context_ref",
        "items",
        "total",
        "distribution",
        "page_size",
        "has_more",
        "next_cursor",
        "read_state",
        "association",
        "address_check",
        "source",
        "source_coverage",
        "graph_state",
        "imported_at",
    }
)
_DPE_PROVENANCE = {
    "version": 2,
    "association": "source_ban_equality",
    "address_check": "unverified",
    "source": "ADEME",
    "source_coverage": "unknown",
    "graph_state": "not_checked",
    "imported_at": None,
}
_DPE_INVENTORY_ITEM_SOURCE_KEYS: tuple[str, ...] = (
    "type_batiment",
    "surface_habitable",
    "etiquette_dpe",
    "etiquette_ges",
    "date_etablissement_dpe",
    "date_fin_validite_dpe",
)
_DPE_DOCUMENT_SOURCE_KEYS: tuple[str, ...] = (
    *_DPE_INVENTORY_ITEM_SOURCE_KEYS,
    "annee_construction",
    "periode_construction",
    "type_installation_chauffage",
    "type_energie_principale_chauffage",
    "qualite_isolation_enveloppe",
    "conso_5_usages_ep",
    "emission_ges_5_usages",
    "cout_total_5_usages",
)
_DPE_INVENTORY_ITEM_KEYS = frozenset({"document_ref", *_DPE_INVENTORY_ITEM_SOURCE_KEYS})
_DPE_LIFECYCLE_SOURCE_KEYS = frozenset({"lifecycle"})
_DPE_DOCUMENT_KEYS = frozenset(
    {
        *(_DPE_PROVENANCE),
        "context_ref",
        "document_ref",
        *_DPE_DOCUMENT_SOURCE_KEYS,
        "validity_by_date",
    }
)
_DPE_DISTRIBUTION_KEYS: frozenset[str] = frozenset((*"ABCDEFG", "unknown"))
_DPE_DATASET_STATE_KEYS: frozenset[str] = frozenset(
    {"dataset", "presence", "absence_reason", "raison"}
)
# Neighbourhood probe for the ``dpe_nearer_neighbor_negative`` archetype only.
# Deliberately far below the 500 m of ``/v1/dpe``'s default: the question is
# "does a diagnostic exist right next to this address", not "what is around".
_NEARBY_PROBE_RADIUS_M = 50
# Per-case keys the report lifts to the top level instead of duplicating.
_LIFTED_REPORT_KEYS: tuple[str, ...] = ("dpe_qualification", "manifest_patch")

# --- Phase 51 (T-51-27 / T-51-28): provenance of the replayed HTTP surface ----
#
# The replay targets the loopback preview of the CHECKOUT
# (``scripts/serve_golden_preview.py``), never the deployed API: ``cd-api.yml``
# deploys ``master`` only, so the deployment is a different commit and a green
# replay against it proved nothing about the code under review.
SMOKE_HEADER: tuple[str, str] = ("X-Smoke-Test", PREVIEW_SMOKE_MARKER)
_LOOPBACK_PREFIX = f"http://{PREVIEW_HOST}"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_LIVE_REPLAY_CASE_INTERVAL_SECONDS = 9


def _assert_loopback_base_url(url: Any) -> None:
    """Refuse any base URL that is not the loopback preview.

    ``localhost`` is rejected too: it resolves through the runner's resolver and
    can be pointed elsewhere. Only the literal loopback address passes, and the
    port separator is required so ``http://127.0.0.1.evil.invalid`` cannot slip
    through on a prefix match.
    """
    if not isinstance(url, str) or not url.startswith(_LOOPBACK_PREFIX):
        raise GoldenSecurityError("replay base URL must be the loopback preview")
    remainder = url[len(_LOOPBACK_PREFIX) :]
    if remainder and not remainder.startswith((":", "/")):
        raise GoldenSecurityError("replay base URL must be the loopback preview")


def _assert_served_checkout_sha(client: Any, expected_sha_env: str) -> str:
    """Prove the preview serves the expected commit BEFORE any private input.

    Called before the private corpus is read: a provenance failure must abort
    while the runner still holds nothing private (T-51-28).
    """
    expected = os.getenv(expected_sha_env)
    if not expected or _SHA_RE.match(expected) is None:
        raise GoldenSecurityError("expected checkout commit is missing or malformed")
    response = client.get(PREVIEW_SHA_PATH)
    response.raise_for_status()
    body = response.json()
    served = body.get("served_checkout_sha") if isinstance(body, Mapping) else None
    if not isinstance(served, str) or _SHA_RE.match(served) is None:
        raise GoldenSecurityError("preview did not report a usable checkout commit")
    if served != expected:
        raise GoldenSecurityError("preview serves a commit other than the checkout")
    return served


_ADDRESS_RE = re.compile(
    r"(?:\b\d{1,5}\s*(?:bis|ter|quater|[a-z])?\s*[,;-]?\s*"
    r"(?:rue|avenue|av\.?|impasse|boulevard|bd\.?|all[eé]e|chemin|place|quai|"
    r"route|voie|passage|traverse|cours|sentier|lotissement|r[eé]sidence)\b|"
    r"\b(?:rue|avenue|impasse|boulevard|all[eé]e|chemin|place|quai|route|voie|"
    r"passage|traverse|cours|sentier|lotissement|r[eé]sidence)\b"
    r"[^\n,;]{0,80}\b\d{1,5}\b)",
    re.IGNORECASE,
)
_COORDINATE_FIELD_RE = re.compile(
    r"\b(?:lat(?:itude)?|lon(?:gitude)?|lng|gps|coordinates?|coordonn[eé]es?)\b"
    r"[\"'`]?\s*(?::|=|\s)\s*[\[('\"\s]*"
    r"(?!-?0(?:\.0+)?\b)-?\d{1,3}(?:\.\d+)?",
    re.IGNORECASE,
)
_COORDINATE_PAIR_RE = re.compile(
    r"(?<![\d.])(?!-?0(?:\.0+)?\b)-?\d{1,3}\.\d+\s*[,;]\s*"
    r"-?\d{1,3}\.\d+(?![\d.])"
)
_SECRET_PATTERNS = (
    re.compile(r"\b(?:dc|sk|pk)_(?:live|anon|test)_[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bsk-proj-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{16,})\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b"),
)
_RAW_PAYLOAD_RE = re.compile(
    r"\b(?:payload(?:_raw)?|response_body|raw_response|request_body)\b"
    r"[\"'`]?\s*[:=](?!\s*(?:null|None|undefined)\b)\s*\S",
    re.IGNORECASE,
)
_SENSITIVE_FIELD_VALUE_RE = re.compile(
    r"\b(?:adresse(?:_exacte|_postale)?|address(?:_exact|_postal)?|street_address|"
    r"voie|api_?key|secret|token|password|credential)\b[\"'`]?\s*[:=]"
    r"(?!\s*(?:null|None|undefined)\b)\s*\S",
    re.IGNORECASE,
)
_BASE64_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9+/_=-])[A-Za-z0-9+/_-]{20,}={0,2}(?![A-Za-z0-9+/_=-])"
)
_HEX_TOKEN_RE = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}){8,}(?![0-9A-Fa-f])")


class GoldenSecurityError(RuntimeError):
    """Raised when a golden-case security guard fails closed."""


def load_schema(path: Path = SCHEMA_PATH) -> dict[str, Any]:  # noqa: C901
    """Load the versioned golden-case schema descriptor."""
    schema = json.loads(path.read_text(encoding="utf-8"))
    if set(schema.get("forbidden_keys", [])) != set(FORBIDDEN_KEYS):
        raise GoldenSecurityError("schema forbidden_keys diverges from runtime guards")
    required = set(schema.get("required_categories", []))
    if not required or not required <= set(schema.get("allowed_categories", [])):
        raise GoldenSecurityError(
            "schema required_categories must be a non-empty subset of allowed"
        )
    # CR-03: the pins ARE the human freeze signed at the 48-16/48-17 checkpoints.
    # Emptying or removing the key made every consumer -- this suite, the selector
    # CLI, --audit-artifacts-only and the live replay -- pass vacuously, and the
    # 'maison divisee' archetype evictable by minimization again (incident 48-12
    # 9.2). Fail closed at load, symmetric with required_categories above. The key
    # stays optional in _validate_corpus_coverage: what is guarded here is the
    # committed FILE, not an in-memory schema built by a test.
    if not schema.get("pinned_cases"):
        raise GoldenSecurityError("schema pinned_cases must be a non-empty freeze")
    # Phase 51: same fail-closed doctrine as forbidden_keys above. A schema whose
    # carrier vocabulary or DPE invariant set drifts from the runtime predicates
    # would let the file accept labels the code cannot prove -- exactly the
    # tampering surface T-51-26 closes.
    if tuple(schema.get("allowed_carriers", ())) != DPE_CARRIERS:
        raise GoldenSecurityError(
            "schema allowed_carriers diverges from runtime guards"
        )
    if set(schema.get("allowed_dpe_invariants", ())) != set(DPE_INVARIANT_KEYS):
        raise GoldenSecurityError(
            "schema allowed_dpe_invariants diverges from runtime guards"
        )
    if set(schema.get("allowed_dpe_statuses", ())) != set(DPE_ATTRIBUTION_STATUSES):
        raise GoldenSecurityError(
            "schema allowed_dpe_statuses diverges from runtime guards"
        )
    if tuple(schema.get("allowed_dpe_inventory_presences", ())) != (
        DPE_INVENTORY_PRESENCES
    ):
        raise GoldenSecurityError(
            "schema allowed_dpe_inventory_presences diverges from runtime guards"
        )
    if schema.get("dpe_attribution_version") != 2:
        raise GoldenSecurityError("schema dpe_attribution_version must be 2")
    if schema.get("dpe_count_max") != DPE_COUNT_MAX:
        raise GoldenSecurityError(
            "schema dpe_count_max diverges from the backend bound"
        )
    if schema.get("final_dpe_gate") is not True:
        raise GoldenSecurityError("schema final_dpe_gate must stay armed")
    return schema


def _normalize_key(key: str) -> str:
    normalized = unicodedata.normalize("NFKD", key)
    return re.sub(r"[^a-z0-9]", "", normalized.lower())


def _is_sensitive_key(key: str) -> bool:
    if key == _SAFE_PRIVATE_REF_KEY:
        return False
    normalized = _normalize_key(key)
    return key.lower() in FORBIDDEN_KEYS or any(
        part in normalized for part in _SENSITIVE_KEY_PARTS
    )


def _validate_exact_keys(
    entry: Mapping[str, Any], schema: Mapping[str, Any]
) -> list[str]:
    expected = set(schema["required_keys"])
    optional = set(schema.get("optional_keys", ()))
    actual = set(entry)
    errors: list[str] = []
    if expected - actual:
        errors.append(f"missing required keys: {sorted(expected - actual)}")
    unexpected = actual - expected - optional
    if unexpected:
        errors.append(f"unexpected keys: {sorted(unexpected)}")
    return errors


def _validate_identity(
    entry: Mapping[str, Any], schema: Mapping[str, Any]
) -> list[str]:
    errors: list[str] = []
    case_id = entry.get("case_id")
    case_type = entry.get("type")
    if (
        not isinstance(case_id, str)
        or re.fullmatch(schema["case_id_pattern"], case_id) is None
    ):
        errors.append("case_id must match the opaque pattern")
    if case_type not in schema["allowed_types"]:
        errors.append(f"unknown type {case_type!r}")
    if (
        isinstance(case_id, str)
        and case_type in schema["allowed_types"]
        and not case_id.startswith(f"golden-{case_type}-")
    ):
        errors.append("case_id type segment does not match type")
    if entry.get("private_ref") != case_id:
        errors.append("private_ref must equal case_id")
    return errors


def _validate_location(
    entry: Mapping[str, Any], schema: Mapping[str, Any]
) -> list[str]:
    errors: list[str] = []
    dept = entry.get("departement")
    code = entry.get("code_insee")
    if dept not in schema["allowed_departements"]:
        errors.append(f"departement {dept!r} outside the allowed set")
    if (
        not isinstance(code, str)
        or re.fullmatch(schema["code_insee_pattern"], code) is None
    ):
        errors.append("code_insee must be a five-digit 44/85 code")
    elif isinstance(dept, str) and not code.startswith(dept):
        errors.append("code_insee does not match departement")
    return errors


def _validate_frozen_at(value: Any) -> list[str]:
    if not isinstance(value, str):
        return ["frozen_at must be an ISO date string"]
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return ["frozen_at is not an ISO date"]
    return ["frozen_at is in the future"] if parsed > date.today() else []


def _validate_millesimes(value: Any, schema: Mapping[str, Any]) -> list[str]:
    if not isinstance(value, dict):
        return ["source_millesimes must be an object"]
    pattern = re.compile(schema["source_millesime_pattern"])
    for key, millesime in value.items():
        if (
            not isinstance(key, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{1,31}", key) is None
        ):
            return ["source_millesimes contains an invalid dataset id"]
        if not isinstance(millesime, str) or pattern.fullmatch(millesime) is None:
            return [f"source_millesimes.{key} has an invalid value"]
    return []


def _is_count(value: Any) -> bool:
    """Accept a plain non-negative int only -- ``True`` is not a count."""
    return type(value) is int and 0 <= value <= DPE_COUNT_MAX


def _is_inventory_count(value: Any) -> bool:
    """Accept any plain non-negative inventory count, without a page-size cap."""
    return type(value) is int and value >= 0


def _validate_dpe_invariants(value: Any, schema: Mapping[str, Any]) -> list[str]:  # noqa: C901
    """Validate the optional Phase 58.1 ``invariants.dpe`` layer (D-17).

    ``allowed_states`` cannot type these facts: inventory presence and
    attribution status each have a dedicated closed vocabulary, while two
    invariants are bounded counts. Hence a dedicated closed validator.
    """
    if not isinstance(value, dict):
        return ["invariants.dpe must be an object"]
    allowed = set(schema.get("allowed_dpe_invariants", DPE_INVARIANT_KEYS))
    unknown = set(value) - allowed
    if unknown:
        return [f"invariants.dpe contains unknown keys: {sorted(unknown)}"]
    errors: list[str] = []
    presences = set(
        schema.get("allowed_dpe_inventory_presences", DPE_INVENTORY_PRESENCES)
    )
    if "dpe_inventory_presence" not in value:
        errors.append("invariants.dpe requires dpe_inventory_presence")
    elif value["dpe_inventory_presence"] not in presences:
        errors.append("invariants.dpe.dpe_inventory_presence is outside the closed set")
    statuses = set(schema.get("allowed_dpe_statuses", DPE_ATTRIBUTION_STATUSES))
    if "dpe_attribution_status" not in value:
        errors.append("invariants.dpe requires dpe_attribution_status")
    elif value["dpe_attribution_status"] not in statuses:
        errors.append("invariants.dpe.dpe_attribution_status is outside the closed set")
    for key in DPE_BOOLEAN_INVARIANTS:
        if key in value and type(value[key]) is not bool:
            errors.append(f"invariants.dpe.{key} must be a boolean")  # noqa: PERF401
    for key in DPE_COUNT_INVARIANTS:
        if key in value and not _is_count(value[key]):
            errors.append(  # noqa: PERF401
                f"invariants.dpe.{key} must be an integer within [0, {DPE_COUNT_MAX}]"
            )
    if value.get("dpe_primary_diagnostic_present") is not False:
        errors.append(
            "invariants.dpe.dpe_primary_diagnostic_present must be false under V2"
        )
    if value.get("dpe_candidate_count") != 0:
        errors.append("invariants.dpe.dpe_candidate_count must be zero under V2")
    return errors


def _dpe_facts(entry: Mapping[str, Any]) -> Mapping[str, Any] | None:
    invariants = entry.get("invariants")
    if not isinstance(invariants, Mapping):
        return None
    facts = invariants.get(_DPE_INVARIANT_LAYER)
    return facts if isinstance(facts, Mapping) else None


def _exact_absence_is_proven(facts: Mapping[str, Any]) -> bool:
    """True only for a MEASURED exact absence -- never for a missing measure."""
    return (
        facts.get("dpe_inventory_presence") == "absent"
        and facts.get("dpe_attribution_status") == "absent"
        and facts.get("dpe_primary_diagnostic_present") is False
        and facts.get("dpe_candidate_count") == 0
        and facts.get("dpe_building_count") == 0
    )


def dpe_carrier_is_satisfied(entry: Mapping[str, Any], carrier: str) -> bool:
    """Test one closed ``carrier_for`` label against a case's DPE invariants.

    Single predicate shared by validation, the qualification runner and the
    coverage gate: selection and validation cannot diverge, the same rule that
    ``observation_is_satisfied`` established for Phase 48.

    ``dpe_nearer_neighbor_negative`` is what distinguishes "no DPE here, and we
    checked" from "no DPE here": it requires the redacted boolean
    ``dpe_nearby_diagnostic_present=true`` on top of the exact absence.
    ``dpe_absent`` requires the absence ALONE and therefore never proves the
    neighbour archetype by default.
    """
    facts = _dpe_facts(entry)
    if facts is None or carrier not in DPE_CARRIERS:
        return False
    status = facts.get("dpe_attribution_status")
    if carrier == "dpe_unassigned":
        return (
            facts.get("dpe_inventory_presence") == "present"
            and status == "unassigned"
            and facts.get("dpe_primary_diagnostic_present") is False
            and facts.get("dpe_candidate_count") == 0
            and facts.get("dpe_building_count") == 0
        )
    if carrier == "dpe_multiple_or_building":
        building = facts.get("dpe_building_count")
        return (
            facts.get("dpe_inventory_presence") in {"present", "unknown"}
            and status == "unassigned"
            and facts.get("dpe_primary_diagnostic_present") is False
            and facts.get("dpe_candidate_count") == 0
            and _is_count(building)
            and building >= 1
        )
    if carrier == "dpe_absent":
        return _exact_absence_is_proven(facts)
    return (
        _exact_absence_is_proven(facts)
        and facts.get("dpe_nearby_diagnostic_present") is True
    )


def _validate_carrier_for(
    entry: Mapping[str, Any], schema: Mapping[str, Any]
) -> list[str]:
    """Validate the optional ``carrier_for`` declaration against its own proof."""
    if _DPE_CARRIER_KEY not in entry:
        return []
    labels = entry[_DPE_CARRIER_KEY]
    allowed = set(schema.get("allowed_carriers", DPE_CARRIERS))
    if not isinstance(labels, list) or not labels:
        return ["carrier_for must be a non-empty array"]
    if any(not isinstance(label, str) for label in labels):
        return ["carrier_for must contain strings only"]
    if len(set(labels)) != len(labels):
        return ["carrier_for contains duplicate labels"]
    unknown = sorted(set(labels) - allowed)
    if unknown:
        return [f"carrier_for contains unknown labels: {unknown}"]
    # T-51-26: a declared archetype must be BACKED by the observed invariants.
    # Without this the label would be free text and the corpus could claim a
    # coverage it never measured.
    return [
        f"carrier_for label {label} is not proven by invariants.dpe"
        for label in labels
        if not dpe_carrier_is_satisfied(entry, label)
    ]


def validate_dpe_carrier_coverage(
    cases: list[Any], schema: Mapping[str, Any]
) -> list[str]:
    """Check the DPE-04 coverage: four archetypes, and one address per 44/85.

    Kept OUT of the default manifest validation on purpose (amendment 43-09): a
    requirement must first be OBSERVED on the live 44/85 surface. Plan 51-10
    arms it through ``validate_manifest(final_dpe_gate=True)`` once the
    qualification report proves each archetype exists.
    """
    typed = [case for case in cases if isinstance(case, dict)]
    covered: set[str] = set()
    departements: set[str] = set()
    for case in typed:
        labels = case.get(_DPE_CARRIER_KEY)
        if not isinstance(labels, list):
            continue
        for label in labels:
            if not isinstance(label, str) or not dpe_carrier_is_satisfied(case, label):
                continue
            covered.add(label)
            if case.get("type") == "address":
                departements.add(str(case.get("departement")))
    errors: list[str] = []
    missing = [
        carrier
        for carrier in schema.get("required_carriers", DPE_CARRIERS)
        if carrier not in covered
    ]
    if missing:
        errors.append(f"missing_dpe_carrier_coverage: archetypes {sorted(missing)}")
    for departement in schema.get("required_carrier_departements", ()):
        if departement not in departements:
            errors.append(  # noqa: PERF401
                f"missing_dpe_carrier_coverage: no address carrier in {departement}"
            )
    return errors


def _validate_invariants(value: Any, schema: Mapping[str, Any]) -> list[str]:
    if not isinstance(value, dict):
        return ["invariants must contain exactly db, api and mapper objects"]
    layers = set(value) - {_DPE_INVARIANT_LAYER}
    if layers != {"db", "api", "mapper"}:
        return ["invariants must contain exactly db, api and mapper objects"]
    errors: list[str] = []
    if _DPE_INVARIANT_LAYER in value:
        errors += _validate_dpe_invariants(value[_DPE_INVARIANT_LAYER], schema)
    db, api, mapper = value["db"], value["api"], value["mapper"]
    if not isinstance(db, dict) or any(type(v) is not bool for v in db.values()):
        errors.append("invariants.db values must be booleans")
    elif not set(db) <= set(schema["allowed_db_invariants"]):
        errors.append("invariants.db contains an unknown key")
    if not isinstance(api, dict) or any(
        v not in schema["allowed_states"] for v in api.values()
    ):
        errors.append("invariants.api values must be canonical states")
    elif any(re.fullmatch(r"[a-z][a-z0-9_]*_state", k) is None for k in api):
        errors.append("invariants.api keys must end in _state")
    if not isinstance(mapper, dict) or any(
        v not in schema["allowed_units"] for v in mapper.values()
    ):
        errors.append("invariants.mapper values must be registered units")
    elif any(re.fullmatch(r"[a-z][a-z0-9_]*_unit", k) is None for k in mapper):
        errors.append("invariants.mapper keys must end in _unit")
    return errors


def validate_case_entry(entry: Any, schema: Mapping[str, Any]) -> list[str]:
    """Validate one minimized manifest entry, including types and closed shapes."""
    if not isinstance(entry, dict):
        return ["case entry must be an object"]
    errors = _validate_exact_keys(entry, schema)
    errors += _validate_identity(entry, schema)
    errors += _validate_location(entry, schema)
    if entry.get("category") not in schema["allowed_categories"]:
        errors.append(f"unknown category {entry.get('category')!r}")
    errors += _validate_frozen_at(entry.get("frozen_at"))
    errors += _validate_millesimes(entry.get("source_millesimes"), schema)
    errors += _validate_invariants(entry.get("invariants"), schema)
    errors += _validate_carrier_for(entry, schema)
    if any(_is_sensitive_key(key) for key in entry if key != _SAFE_PRIVATE_REF_KEY):
        errors.append("forbidden PII/secret/coordinate key")
    return errors


def validate_manifest(
    manifest: Any, schema: Mapping[str, Any], *, final_dpe_gate: bool = False
) -> list[str]:
    """Validate the complete manifest with closed structure and corpus bounds.

    The committed schema arms DPE-04 permanently after the Plan 51-10 freeze.
    ``final_dpe_gate`` remains an explicit opt-in for synthetic transition
    tests, while in-memory schemas may set ``final_dpe_gate`` to false.
    """
    if not isinstance(manifest, dict):
        return ["manifest must be an object"]
    errors: list[str] = []
    if set(manifest) - {"schema_version", "description", "cases"}:
        errors.append("manifest contains unexpected top-level keys")
    if manifest.get("schema_version") != schema["schema_version"]:
        errors.append("schema_version mismatch")
    cases = manifest.get("cases")
    if not isinstance(cases, list):
        return [*errors, "cases must be an array"]
    seen: set[str] = set()
    for index, entry in enumerate(cases):
        entry_errors = validate_case_entry(entry, schema)
        errors.extend(f"cases[{index}]: {error}" for error in entry_errors)
        if isinstance(entry, dict) and isinstance(entry.get("case_id"), str):
            case_id = entry["case_id"]
            if case_id in seen:
                errors.append(f"duplicate case_id at cases[{index}]")
            seen.add(case_id)
    errors += _validate_corpus_coverage(cases, schema)
    if final_dpe_gate or schema.get("final_dpe_gate") is True:
        errors += validate_dpe_carrier_coverage(cases, schema)
    return errors


def _triple_is_satisfied(
    invariants: Mapping[str, Any], triple: Mapping[str, Any]
) -> bool:
    """Test a single ``{layer, invariant, value}`` triple against a case.

    Strict equality, deliberately: an invariant ABSENT from the case never
    satisfies ``value: false``, because ``dict.get`` yields ``None`` and
    ``None == False`` is False in Python. That is the wanted never-guess
    behaviour (D-04) -- an absent measurement is not a measured negative.
    """
    layer = invariants.get(triple["layer"])
    if not isinstance(layer, Mapping):
        return False
    return layer.get(triple["invariant"]) == triple["value"]


def observation_is_satisfied(
    entry: Mapping[str, Any], requirement: Mapping[str, Any]
) -> bool:
    """Test one ``required_observations`` entry, in either accepted shape.

    A requirement is either the historical triple ``{layer, invariant, value}``
    or a conjunction ``{"all_of": [triple, ...]}`` that a case satisfies only
    when it verifies EVERY triple of the list. The conjunctive shape exists
    because the composite archetype of Phase 48 -- "maison divisee": several
    appartement DPE AND no copropriete row -- cannot be expressed as
    independent triples (48-GOLDEN-EVIDENCE 13.1): a single condominium case
    satisfies both unitary requirements on its own, so the minimizing selector
    has no reason to keep a second case.

    This function lives HERE and not in ``select_golden_cases.py`` because the
    selector already imports from this module: the reverse direction would be a
    circular import. Both the selection coverage mask and the manifest coverage
    check call it, so the two can never diverge -- and a divergence would be
    worse than the WR-08 hole it closes.
    """
    invariants = entry.get("invariants")
    if not isinstance(invariants, Mapping):
        return False
    conjunction = requirement.get("all_of")
    if conjunction is not None:
        return all(_triple_is_satisfied(invariants, item) for item in conjunction)
    return _triple_is_satisfied(invariants, requirement)


def _describe_observation(requirement: Mapping[str, Any]) -> str:
    """Name a requirement for an error message: invariant names only, no value."""
    conjunction = requirement.get("all_of")
    if conjunction is not None:
        return " AND ".join(_describe_observation(item) for item in conjunction)
    layer = requirement["layer"]
    return f"{layer}.{requirement['invariant']}={requirement['value']}"


def _validate_corpus_coverage(cases: list[Any], schema: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    typed = [case for case in cases if isinstance(case, dict)]
    for case_type, bounds in schema["cardinality"].items():
        count = sum(case.get("type") == case_type for case in typed)
        if not bounds["min"] <= count <= bounds["max"]:
            errors.append(
                f"expected {bounds['min']}-{bounds['max']} {case_type}, got {count}"
            )
    observed = {case.get("category") for case in typed}
    missing = set(schema["required_categories"]) - observed
    if missing:
        errors.append(f"missing required categories: {sorted(missing)}")
    # WR-08: coverage used to be enforced by the selector alone, so a committed
    # manifest could lose its only multi-logement case -- by hand-edit, unlucky
    # merge, or regeneration from an impoverished pool -- and still pass both
    # --audit-artifacts-only and the live replay (_run_live reuses this
    # function). Both keys stay OPTIONAL: absent from the schema, nothing new
    # is checked.
    for requirement in schema.get("required_observations", ()):
        if not any(observation_is_satisfied(case, requirement) for case in typed):
            errors.append(  # noqa: PERF401
                f"missing required observation: {_describe_observation(requirement)}"
            )
    present = {case.get("case_id") for case in typed}
    for case_id in schema.get("pinned_cases", ()):
        if case_id not in present:
            errors.append(f"missing pinned case: {case_id}")  # noqa: PERF401
    return errors


def _normalize_sql(sql: str) -> str:
    return " ".join(sql.split())


def assert_select_only(sql: str, allowed_sql: Collection[str] = ALLOWED_SQL) -> None:
    """Allow only exact, internal, pre-reviewed SQL statements."""
    normalized = _normalize_sql(sql)
    allowed = {_normalize_sql(candidate) for candidate in allowed_sql}
    if normalized not in allowed:
        raise GoldenSecurityError("SQL statement is not in the read-only allowlist")


def redact_entry(value: Any) -> Any:
    """Recursively remove sensitive keys and redact sensitive string values."""
    if isinstance(value, dict):
        return {
            key: redact_entry(item)
            for key, item in value.items()
            if not _is_sensitive_key(str(key))
        }
    if isinstance(value, list):
        return [redact_entry(item) for item in value]
    if isinstance(value, str) and audit_artifact_text(value, source="value"):
        return "[REDACTED]"
    return value


def audit_manifest_keys(obj: Any, path: str = "") -> list[str]:
    """Flag sensitive keys recursively without returning their values."""
    violations: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{path}.{key}" if path else str(key)
            if _is_sensitive_key(str(key)):
                violations.append(f"forbidden key at {child}")
            violations.extend(audit_manifest_keys(value, child))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            violations.extend(audit_manifest_keys(value, f"{path}[{index}]"))
    return violations


def _decode_escaped_text(text: str) -> str:
    text = re.sub(
        r"\\u([0-9a-fA-F]{4})", lambda match: chr(int(match.group(1), 16)), text
    )
    return re.sub(
        r"\\x([0-9a-fA-F]{2})", lambda match: chr(int(match.group(1), 16)), text
    )


def _normalize_scan_text(text: str) -> str:
    """Fold Unicode compatibility forms and remove invisible format controls."""
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(char for char in normalized if unicodedata.category(char) != "Cf")


def _decoded_variants(text: str) -> list[tuple[str, int]]:
    variants: list[tuple[str, int]] = []
    queue = [(text, 1, 0)]
    seen: set[str] = set()
    while queue and len(variants) < 64:
        current, base_line, depth = queue.pop(0)
        current = _normalize_scan_text(current)
        if current in seen:
            continue
        seen.add(current)
        variants.append((current, base_line))
        if depth >= 3:
            continue
        for decoded in (unquote(current), _decode_escaped_text(current)):
            if decoded != current:
                queue.append((decoded, base_line, depth + 1))  # noqa: PERF401
        queue.extend(_decode_base64_tokens(current, base_line, depth))
        queue.extend(_decode_hex_tokens(current, base_line, depth))
    return variants


def _decode_base64_tokens(
    text: str, base_line: int, depth: int
) -> list[tuple[str, int, int]]:
    decoded_tokens: list[tuple[str, int, int]] = []
    for match in _BASE64_TOKEN_RE.finditer(text):
        try:
            token = match.group(0)
            padded = token + "=" * (-len(token) % 4)
            decoded = base64.b64decode(
                padded.replace("-", "+").replace("_", "/"), validate=True
            ).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            continue
        line = base_line + text.count("\n", 0, match.start())
        decoded_tokens.append((decoded, line, depth + 1))
    return decoded_tokens


def _decode_hex_tokens(
    text: str, base_line: int, depth: int
) -> list[tuple[str, int, int]]:
    decoded_tokens: list[tuple[str, int, int]] = []
    for match in _HEX_TOKEN_RE.finditer(text):
        try:
            decoded = bytes.fromhex(match.group(0)).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        line = base_line + text.count("\n", 0, match.start())
        decoded_tokens.append((decoded, line, depth + 1))
    return decoded_tokens


def _scan_text_variant(text: str, source: str, base_line: int) -> list[str]:
    patterns: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("street_address", _ADDRESS_RE),
        ("precise_coordinate", _COORDINATE_FIELD_RE),
        ("precise_coordinate", _COORDINATE_PAIR_RE),
        ("raw_payload", _RAW_PAYLOAD_RE),
        ("sensitive_field", _SENSITIVE_FIELD_VALUE_RE),
        *(("secret", pattern) for pattern in _SECRET_PATTERNS),
    )
    violations: list[str] = []
    for label, pattern in patterns:
        for match in pattern.finditer(text):
            line = base_line + text.count("\n", 0, match.start())
            violations.append(f"{source}: {label} at line {line}")
    return violations


def audit_artifact_text(text: str, source: str) -> list[str]:
    """Scan raw and decoded text without ever echoing matched content."""
    violations: list[str] = []
    for variant, base_line in _decoded_variants(text):
        violations.extend(_scan_text_variant(variant, source, base_line))
    return sorted(set(violations))


def _observation_failure_reason(exc: BaseException) -> str:
    """Name the failed observation without leaking the exception message.

    ``type(exc).__name__`` is an internal constant -- no address, no DSN and no
    coordinate can transit through it -- whereas ``str(exc)`` can carry the
    queried URL or a row value. Naming the class beats the unactionable generic
    reason, exactly as the ``GoldenSecurityError`` branch already argues (R3-01).
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    suffix = f" {status}" if isinstance(status, int) else ""
    # The PATH names the route that failed; it is a static literal of this
    # module (``/v1/commune``, ``/v1/resolve``, ``/v1/dpe``), never private --
    # the replayed address travels in the QUERY STRING, which is dropped here
    # exactly as the request-logging middleware drops it.
    path = getattr(getattr(getattr(exc, "request", None), "url", None), "path", None)
    route = f" {path}" if isinstance(path, str) else ""
    return f"live_observation_failed: {type(exc).__name__}{suffix}{route}"


def _read_private_input(path: str | None, env_name: str | None) -> dict[str, Any]:
    if bool(path) == bool(env_name):
        raise GoldenSecurityError("provide exactly one private input source")
    raw = _read_private_file(path) if path else os.getenv(env_name or "")
    if not raw:
        raise GoldenSecurityError("private input is unavailable")
    parsed = json.loads(raw)
    cases = parsed.get("cases") if isinstance(parsed, dict) else None
    if not isinstance(cases, dict):
        raise GoldenSecurityError("private input must contain a cases object")
    return cases


def _read_private_file(raw_path: str | None) -> str:
    if raw_path is None:
        raise GoldenSecurityError("private input path is missing")
    path = Path(raw_path).resolve()
    private_root = (
        _REPO_ROOT / "contracts" / "golden-cases" / "v1" / "private"
    ).resolve()
    if path.suffix != ".json" or path.parent != private_root:
        raise GoldenSecurityError(
            "private input path is outside the gitignored directory"
        )
    return path.read_text(encoding="utf-8")


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise GoldenSecurityError(f"required environment variable is missing: {name}")
    return value


def _required_redaction_confirmation(name: str) -> None:
    if os.getenv(name) != "1":
        raise GoldenSecurityError("resolve query-log redaction is not confirmed")


def _open_read_only_connection(dsn: str) -> psycopg.Connection[Any]:
    conn = psycopg.connect(dsn, connect_timeout=5, row_factory=dict_row)
    conn.read_only = True
    try:
        assert_select_only(READ_ONLY_STATE_SQL)
        state = conn.execute(READ_ONLY_STATE_SQL).fetchone()
        if not state or state["state"] != "on":
            raise GoldenSecurityError("PostgreSQL transaction is not read-only")
    except Exception:
        conn.close()
        raise
    return conn


def _db_observation(
    conn: psycopg.Connection[Any], entry: Mapping[str, Any], private: Mapping[str, Any]
) -> tuple[dict[str, bool], dict[str, str], dict[str, int]]:
    assert_select_only(COMMUNE_SQL)
    commune = conn.execute(COMMUNE_SQL, (entry["code_insee"],)).fetchone()
    observation = {
        "has_commune_row": commune is not None,
        "superficie_present": commune is not None and commune["superficie"] is not None,
    }
    metrics: dict[str, int] = {}
    if entry["type"] == "address":
        parcel_id = private.get("id_parcelle")
        if not isinstance(parcel_id, str):
            raise GoldenSecurityError("address private input requires id_parcelle")
        ban_id = private.get("ban_id")
        if not isinstance(ban_id, str) or not ban_id.strip():
            # R3-01, fail closed: without the BAN key the DPE rattachement
            # cannot be measured on the production path (BAN key primary,
            # spatial fallback secondary) -- a degraded private corpus must
            # BLOCK, never silently fall back to a geometry-only measure.
            raise GoldenSecurityError(
                "address private input requires ban_id: the DPE rattachement "
                "measure follows the BAN-key production path"
            )
        assert_select_only(PARCEL_SQL)
        parcel = conn.execute(PARCEL_SQL, (parcel_id, entry["code_insee"])).fetchone()
        observation["parcelle_present"] = parcel is not None
        multi, metrics = _multi_logement_observation(conn, parcel_id, ban_id)
        observation.update(multi)
    return observation, _source_millesimes(commune), metrics


def _dpe_rattaches(nb_par_cle: int, spatial: Mapping[str, Any]) -> int:
    """Count DPE rows the production logic attaches to this building.

    Pure mirror of ``app.logements.router._charge_dpe`` on aggregates:
    key path first; zero key rows -> FULL spatial fallback; otherwise the
    spatial complement is restricted to keyless rows. Orphan-keyed rows
    (a non-null key differing from the address key) are attached only on
    the full-fallback branch -- exactly the production behaviour.
    """
    if nb_par_cle:
        return nb_par_cle + int(spatial["spatial_sans_cle"])
    return int(spatial["spatial_total"])


# R3-02 gate thresholds (B4). Per-building, on golden address cases only:
# - attached/expected < 0.90 on a building fails;
# - orphan keys (non-null, differing from the address key, therefore attached
#   by NEITHER branch when the key path attached rows) > 5 % fails.
# The 5 % mark is the L1-research corpus-level anomaly threshold; the measured
# R3-04 denominator (api/docs/etl-runbook.md section 6, 2026-08-02) sits at
# 91.27 % (44) / 80.02 % (85) real key attachment -- corpus-level orphans are
# ABOVE the mark, which is exactly why the frozen golden buildings must prove
# they are not affected.
_RATTACHEMENT_MIN_TAUX = 0.90
_CLE_ORPHELINE_MAX_TAUX = 0.05


def _rattachement_metrics(
    nb_par_cle: int, spatial: Mapping[str, Any]
) -> tuple[dict[str, int], bool]:
    """Compute the three B4 metrics and the boolean gate verdict (R3-02).

    The golden db contract only carries BOOLEAN invariants, so the gate is
    booleanized as ``dpe_rattachement_ok`` rather than extending the validator
    to ratios -- the least invasive contract decision: schema, selector and
    manifest validation stay untouched. The raw counters still reach the
    redacted live report (integers only, no key, no address) so the freeze
    decision can be made on observed numbers.

    Where the gate turns red: in the MANUAL replay workflow
    (.github/workflows/verify-golden-live.yml) via ``_compare_observation``,
    once a frozen case carries ``dpe_rattachement_ok`` -- NOT in ci-api.yml.
    Per amendment 43-09 the invariant is NOT frozen yet on any case and NOT
    added to ``required_observations``: a new requirement must first be
    OBSERVED on the 44/85 live surface. Remaining step: one green live replay,
    read the metrics in the redacted report, then freeze the invariant on the
    multi-logement cases (005/007, already pinned -- zero cardinality cost).

    ``attendus`` (expected) is the union of key-attached rows and spatially
    attachable rows; on the key branch the only gap is the orphan-keyed rows.
    An empty building (0 expected) is trivially ok -- the gate targets
    buildings, not maisons without any appartement DPE.
    """
    rattaches = _dpe_rattaches(nb_par_cle, spatial)
    if nb_par_cle:
        secours = int(spatial["spatial_sans_cle"])
        orphelines = int(spatial["spatial_cle_orpheline"])
        attendus = (
            nb_par_cle
            + int(spatial["spatial_total"])
            - int(spatial["spatial_cle_exacte"])
        )
    else:
        # Full-fallback branch: every spatially attachable row IS attached,
        # orphan keys included -- nothing is missed, the gap is zero.
        secours = rattaches
        orphelines = 0
        attendus = rattaches
    ok = attendus == 0 or (
        rattaches / attendus >= _RATTACHEMENT_MIN_TAUX
        and orphelines / attendus <= _CLE_ORPHELINE_MAX_TAUX
    )
    metrics = {
        "dpe_avec_cle_ban": nb_par_cle,
        "dpe_cle_ban_orpheline": orphelines,
        "dpe_rattaches_par_secours_spatial": secours,
        "dpe_rattaches": rattaches,
        "dpe_attendus": attendus,
    }
    return metrics, ok


def _multi_logement_observation(
    conn: psycopg.Connection[Any], parcel_id: str, ban_id: str
) -> tuple[dict[str, bool], dict[str, int]]:
    """Observe the three multi-logement signals (D1/D2/D3, Phase 48) for one parcel.

    SELECT-only bounded aggregates, bind parameters only -- see the
    ``COPRO_ROW_SQL``/``DPE_APPARTEMENTS_PAR_BAN_SQL``/
    ``DPE_APPARTEMENTS_SPATIAL_SQL``/``DVF_APPARTEMENTS_SQL`` constants above
    for the exact statements and their production analogs (R3-01).
    """
    assert_select_only(COPRO_ROW_SQL)
    copro = conn.execute(COPRO_ROW_SQL, {"id_parcelle": parcel_id}).fetchone()
    assert_select_only(DPE_APPARTEMENTS_PAR_BAN_SQL)
    par_ban = conn.execute(DPE_APPARTEMENTS_PAR_BAN_SQL, {"ban_id": ban_id}).fetchone()
    assert_select_only(DPE_APPARTEMENTS_SPATIAL_SQL)
    spatial = conn.execute(
        DPE_APPARTEMENTS_SPATIAL_SQL,
        {
            "id_parcelle": parcel_id,
            "ban_id": ban_id,
            "rayon_m": SECOURS_RAYON_M,
            "marge_m": SNAP_MARGIN_M,
        },
    ).fetchone()
    assert_select_only(DVF_APPARTEMENTS_SQL)
    dvf = conn.execute(
        DVF_APPARTEMENTS_SQL,
        {"id_parcelle": parcel_id, "surface_min": _DVF_SURFACE_MIN_D09},
    ).fetchone()
    if par_ban is None or spatial is None:
        raise GoldenSecurityError("DPE rattachement observation returned no row")
    metrics, rattachement_ok = _rattachement_metrics(
        int(par_ban["dpe_par_ban_count"]), spatial
    )
    observation = {
        "copro_row_present": bool(copro and copro["copro_row_present"]),
        # D-03: >=2 appartement DPE rows attached to the building (production
        # attachment rule, R3-01) is the detection threshold.
        "dpe_appartements_multiples": metrics["dpe_rattaches"] >= 2,
        "dvf_appartements_present": bool(dvf and dvf["dvf_appartement_present"]),
        # R3-02: booleanized attachment gate -- see _rattachement_metrics.
        "dpe_rattachement_ok": rattachement_ok,
    }
    return observation, metrics


def _source_millesimes(commune: Mapping[str, Any] | None) -> dict[str, str]:
    if commune is None:
        return {}
    fields = {
        "recensement": "annee_demographie",
        "revenus": "annee_revenus",
        "fiscalite": "annee_fiscalite",
        "loyers": "annee_loyers",
    }
    return {
        dataset: str(commune[field])
        for dataset, field in fields.items()
        if commune.get(field) is not None
    }


_ADDRESS_RESOLVE_DATASET_BATCHES: tuple[tuple[str, ...], ...] = (
    ("cadastre", "dvf", "dpe", "risques", "urbanisme"),
    ("commune",),
)


def _merge_resolve_batch(merged: dict[str, Any], batch: Mapping[str, Any]) -> None:  # noqa: C901
    """Merge one bounded resolve response without accepting silent conflicts."""
    identity_keys = ("id_parcelle", "code_insee", "ban_id")
    if any(merged.get(key) != batch.get(key) for key in identity_keys):
        raise GoldenSecurityError("API resolve batch identity mismatch")

    for field in ("datasets", "dataset_states"):
        incoming = batch.get(field)
        if incoming is None:
            continue
        if not isinstance(incoming, Mapping):
            raise GoldenSecurityError(f"API resolve batch {field} is malformed")
        current = merged.get(field)
        if current is None:
            current = {}
            merged[field] = current
        if not isinstance(current, dict):
            raise GoldenSecurityError(f"API resolve batch {field} is malformed")
        if set(current).intersection(incoming):
            raise GoldenSecurityError(f"API resolve batch {field} overlaps")
        current.update(incoming)

    for field in ("erreurs", "avertissements"):
        incoming = batch.get(field)
        if incoming is None:
            continue
        if not isinstance(incoming, list):
            raise GoldenSecurityError(f"API resolve batch {field} are malformed")
        current = merged.get(field)
        if current is None:
            current = []
            merged[field] = current
        if not isinstance(current, list):
            raise GoldenSecurityError(f"API resolve batch {field} are malformed")
        current.extend(incoming)


def _api_payload(  # noqa: C901
    client: httpx.Client,
    entry: Mapping[str, Any],
    private: Mapping[str, Any],
    *,
    resolve_logging_redacted: bool = False,
) -> Mapping[str, Any]:
    if entry["type"] == "commune":
        requests = [("/v1/commune", {"code_insee": entry["code_insee"]})]
    else:
        if not resolve_logging_redacted:
            raise GoldenSecurityError(
                "address API replay is blocked until /v1/resolve redacts private q logs"
            )
        query = private.get("query")
        if not isinstance(query, str) or not query.strip():
            raise GoldenSecurityError("address private input requires query")
        requests = [
            (
                "/v1/resolve",
                {
                    "q": query,
                    "type": "adresse",
                    "datasets": ",".join(batch),
                },
            )
            for batch in _ADDRESS_RESOLVE_DATASET_BATCHES
        ]

    results: list[dict[str, Any]] = []
    for path, params in requests:
        response = client.get(path, params=params)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("donnees") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise GoldenSecurityError("API response has no case payload")
        result = data[0]
        results.append(result)

    # The first address batch deliberately contains cadastre: it is the only
    # batch that can bind an address result to the private parcel identity.
    # Later batches are bound to it by _merge_resolve_batch's public identity
    # checks, because they do not repeat the cadastre dataset.
    _assert_api_identity(entry, private, results[0])
    merged = dict(results[0])
    for field in ("datasets", "dataset_states"):
        if isinstance(merged.get(field), Mapping):
            merged[field] = dict(merged[field])
    for field in ("erreurs", "avertissements"):
        if isinstance(merged.get(field), list):
            merged[field] = list(merged[field])
    for result in results[1:]:
        _merge_resolve_batch(merged, result)
    return merged


def _assert_api_identity(
    entry: Mapping[str, Any], private: Mapping[str, Any], payload: Mapping[str, Any]
) -> None:
    """Bind the observed API result to the manifest/private case identity."""
    if entry["type"] == "commune":
        if payload.get("code_insee") != entry["code_insee"]:
            raise GoldenSecurityError("API commune identity mismatch")
        return
    parcel_id = private.get("id_parcelle")
    observed = payload.get("id_parcelle")
    if observed is None:
        # Address resolutions never populate the root id_parcelle (only the
        # gps/cadastre input paths do): the multi payload's cadastre block is
        # the API's authoritative parcel statement for an address query.
        datasets = payload.get("datasets")
        cadastre = datasets.get("cadastre") if isinstance(datasets, Mapping) else None
        if isinstance(cadastre, Mapping):
            observed = cadastre.get("id_parcelle")
    if not isinstance(parcel_id, str) or observed != parcel_id:
        raise GoldenSecurityError("API parcel identity mismatch")


def _dataset_state(payload: Mapping[str, Any], dataset: str, case_type: str) -> str:  # noqa: C901
    if case_type == "commune":
        fields = {
            "dvf": ("prix_m2_median", "nb_mutations_12m"),
            "dpe": ("dpe_passoires_pct", "dpe_median"),
        }.get(dataset, ())
        values = [payload.get(field) for field in fields]
        return "value" if any(value is not None for value in values) else "not_reported"
    if dataset == "dpe":
        states = payload.get("dataset_states")
        block = states.get("dpe") if isinstance(states, Mapping) else None
        if not isinstance(block, Mapping) or set(block) != _DPE_DATASET_STATE_KEYS:
            raise GoldenSecurityError(
                "API response carries no closed dpe dataset state"
            )
        if block.get("dataset") != "dpe":
            raise GoldenSecurityError("dpe dataset state names another dataset")
        presence = block.get("presence")
        absence_reason = block.get("absence_reason")
        if presence == "value" and absence_reason is None:
            return "value"
        if presence == "error" and absence_reason == "temporary_error":
            return "temporary_error"
        if presence == "absent" and absence_reason in _OBSERVABLE_STATES - {
            "value",
            "temporary_error",
        }:
            return absence_reason
        raise GoldenSecurityError("dpe dataset state is inconsistent")
    errors = payload.get("erreurs")
    if isinstance(errors, list) and any(
        isinstance(error, dict) and error.get("dataset") == dataset for error in errors
    ):
        return "temporary_error"
    datasets = payload.get("datasets")
    if not isinstance(datasets, dict) or dataset not in datasets:
        return "out_of_coverage"
    value = datasets[dataset]
    if value is None:
        return "not_reported"
    if isinstance(value, (list, dict)) and not value:
        return "out_of_coverage"
    return "value"


def derive_category(api: Mapping[str, str]) -> str | None:
    """Derive the stable manifest category from observed API states.

    Single source of truth shared by the selector (which imports it) and the
    Phase 51 ``manifest_patch``: two derivations of the same category would let
    a qualified case be frozen under a label the selector would never assign.
    ``temporary_error`` yields ``None`` -- an outage never becomes a category.
    """
    if "temporary_error" in api.values():
        return None
    dvf = api.get("dvf_state")
    dpe = api.get("dpe_state")
    if dvf == "value" and dpe == "value":
        return (
            "complete" if all(state == "value" for state in api.values()) else "partial"
        )
    if dvf != "value" and dpe == "value":
        return "no_dvf"
    if dvf == "value" and dpe != "value":
        return "no_dpe"
    unavailable = {"out_of_coverage", "not_reported", "not_applicable"}
    if dvf in unavailable and dpe in unavailable:
        return "dataset_out_of_coverage"
    return "partial"


def dpe_attribution_facts(payload: Mapping[str, Any]) -> dict[str, Any]:  # noqa: C901
    """Read bounded V2 facts without turning an address hit into a dwelling."""
    block = payload.get("dpe_attribution") if isinstance(payload, Mapping) else None
    if not isinstance(block, Mapping):
        raise GoldenSecurityError("API response carries no dpe_attribution decision")
    if set(block) != {
        "version",
        "status",
        "reason",
        "diagnostic",
        "candidates",
        "building_context",
        "graph_state",
    }:
        raise GoldenSecurityError("dpe_attribution payload has an unexpected shape")
    if block.get("version") != 2:
        raise GoldenSecurityError("dpe_attribution version is unsupported")
    status = block.get("status")
    if status not in DPE_ATTRIBUTION_STATUSES:
        raise GoldenSecurityError("dpe_attribution status is outside the closed set")
    reason = block.get("reason")
    if (
        (status == "unassigned" and reason != "dwelling_identity_unproven")
        or (
            status == "absent"
            and reason not in {"identity_unavailable", "not_reported"}
        )
        or (status == "error" and reason != "temporary_error")
    ):
        raise GoldenSecurityError("dpe_attribution reason is inconsistent")
    if block.get("diagnostic") is not None or block.get("candidates") != []:
        raise GoldenSecurityError("dpe_attribution cannot select a dwelling diagnostic")
    if block.get("graph_state") not in {"not_checked", "incoherent"}:
        raise GoldenSecurityError("dpe_attribution graph state is unsupported")

    building = block.get("building_context")
    if status == "error" and building is not None:
        raise GoldenSecurityError("dpe_attribution payload has an unexpected shape")
    if not (building is None or isinstance(building, Mapping)):
        raise GoldenSecurityError("dpe_attribution payload has an unexpected shape")
    building_count = 0
    truncated = False
    if isinstance(building, Mapping):
        if set(building) != {"count", "distribution", "truncated"}:
            raise GoldenSecurityError("dpe building context has an unexpected shape")
        building_count = building.get("count")
        distribution = building.get("distribution")
        truncated = building.get("truncated")
        if (
            not _is_count(building_count)
            or not isinstance(distribution, Mapping)
            or set(distribution) - set("ABCDEFG")
            or any(not _is_count(value) for value in distribution.values())
            or type(truncated) is not bool
            or sum(distribution.values()) > building_count
        ):
            raise GoldenSecurityError("dpe building context is outside public bounds")
    facts = {
        "dpe_attribution_status": status,
        "dpe_primary_diagnostic_present": False,
        "dpe_candidate_count": 0,
        "dpe_building_count": building_count,
        "dpe_truncated": truncated,
    }
    return facts


def _dpe_inventory_presence_from_entry(entry: Mapping[str, Any]) -> str:
    """Read the explicit frozen inventory fact without deriving it from attribution."""
    facts = _dpe_facts(entry)
    presence = facts.get("dpe_inventory_presence") if facts is not None else None
    if presence not in DPE_INVENTORY_PRESENCES:
        raise GoldenSecurityError("golden DPE inventory presence is missing or invalid")
    return presence


def dpe_inventory_presence(payload: Mapping[str, Any]) -> str:
    """Derive only documentary presence from the closed public inventory.

    Item contents and opaque references are deliberately never inspected: the
    live gate needs the complete inventory count, not one document identifier.
    """
    block = payload.get("dpe_inventory") if isinstance(payload, Mapping) else None
    if not isinstance(block, Mapping):
        raise GoldenSecurityError("API response carries no closed dpe_inventory block")
    _inventory_contract_date(block)
    if block.get("version") != 2:
        raise GoldenSecurityError("dpe_inventory version is unsupported")

    distribution = block.get("distribution")
    if (
        not isinstance(distribution, Mapping)
        or set(distribution) != _DPE_DISTRIBUTION_KEYS
        or any(not _is_inventory_count(value) for value in distribution.values())
    ):
        raise GoldenSecurityError("dpe_inventory distribution is inconsistent")
    distribution_total = sum(distribution.values())
    total = block.get("total")
    state = block.get("state")
    read_state = block.get("read_state")

    if (
        state == "ready"
        and _is_inventory_count(total)
        and total > 0
        and distribution_total == total
        and read_state == "complete"
    ):
        return "present"
    if (
        state == "absent"
        and _is_inventory_count(total)
        and total == 0
        and distribution_total == 0
        and read_state == "complete"
    ):
        return "absent"
    if (
        state == "identity_unavailable"
        and total is None
        and distribution_total == 0
        and read_state == "complete"
    ):
        return "unknown"
    if state == "unavailable":
        raise GoldenSecurityError("dpe_inventory reported an unstable outage")
    raise GoldenSecurityError("dpe_inventory state, total and distribution disagree")


def _assert_dpe_provenance(block: Mapping[str, Any], label: str) -> None:
    if any(block.get(key) != value for key, value in _DPE_PROVENANCE.items()):
        raise GoldenSecurityError(f"{label} provenance is inconsistent")


def _paris_today() -> date:
    """Keep the independent verifier's clock on the server's business calendar."""
    return datetime.now(ZoneInfo("Europe/Paris")).date()


def _inventory_contract_date(block: Mapping[str, Any]) -> date | None:
    if set(block) == _DPE_INVENTORY_KEYS:
        return None
    if set(block) != _DPE_INVENTORY_KEYS | {"as_of_date"}:
        raise GoldenSecurityError("API response carries no closed dpe_inventory block")
    raw = block.get("as_of_date")
    try:
        parsed = date.fromisoformat(raw) if isinstance(raw, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.isoformat() != raw or parsed != _paris_today():
        raise GoldenSecurityError("DPE inventory reference date is invalid or stale")
    items = block.get("items")
    if not isinstance(items, list) or any(
        not isinstance(item, Mapping)
        or set(item) != _DPE_INVENTORY_ITEM_KEYS | _DPE_LIFECYCLE_SOURCE_KEYS
        for item in items
    ):
        raise GoldenSecurityError("DPE inventory lifecycle contract is mixed")
    try:
        DpeInventoryPage.model_validate(block)
    except ValidationError:
        raise GoldenSecurityError(
            "DPE inventory lifecycle contract is malformed"
        ) from None
    return parsed


def _assert_lifecycle_document(
    block: Mapping[str, Any], *, as_of_date: date, detail: bool
) -> None:
    try:
        model = DpeConsultedDocument if detail else DpeInventoryItem
        model.model_validate(block)
    except ValidationError:
        raise GoldenSecurityError(
            "DPE document lifecycle contract is malformed"
        ) from None
    lifecycle = block.get("lifecycle")
    if lifecycle is not None and lifecycle["as_of_date"] != as_of_date.isoformat():
        raise GoldenSecurityError("DPE document lifecycle date is inconsistent")


def _assert_no_store(response: Any, label: str) -> None:
    headers = getattr(response, "headers", None)
    cache_control = headers.get("cache-control") if hasattr(headers, "get") else None
    if cache_control != "no-store":
        raise GoldenSecurityError(f"{label} response is cacheable")


def _source_projection(
    block: Mapping[str, Any], keys: Collection[str]
) -> dict[str, Any]:
    """Copy the public allowlist in memory; opaque references are never included."""
    return {key: block.get(key) for key in keys}


def _assert_document_validity(
    document: Mapping[str, Any], as_of_date: date | None = None
) -> None:
    if as_of_date is not None:
        _assert_lifecycle_document(document, as_of_date=as_of_date, detail=True)
        # The SQL lifecycle proof independently checks precise source validity.
        if document.get("lifecycle") is None:
            established = document.get("date_etablissement_dpe")
            raw_end = document.get("date_fin_validite_dpe")
            start = date.fromisoformat(established) if established is not None else None
            end = date.fromisoformat(raw_end) if raw_end is not None else None
            expected = "unknown"
            if (
                start is not None
                and end is not None
                and start <= as_of_date
                and start <= end
            ):
                expected = "expired" if end < as_of_date else "not_expired_by_date"
            if document.get("validity_by_date") != expected:
                raise GoldenSecurityError("DPE document validity is inconsistent")
        return
    raw_end = document.get("date_fin_validite_dpe")
    if raw_end is None:
        expected = "unknown"
    elif isinstance(raw_end, str):
        try:
            end = date.fromisoformat(raw_end)
        except ValueError as exc:
            raise GoldenSecurityError("DPE document end date is malformed") from exc
        expected = "expired" if end < date.today() else "not_expired_by_date"
    else:
        raise GoldenSecurityError("DPE document end date is malformed")
    if document.get("validity_by_date") != expected:
        raise GoldenSecurityError("DPE document validity is inconsistent")


def _documentary_source_matches(
    conn: psycopg.Connection[Any],
    *,
    private: Mapping[str, Any],
    entry: Mapping[str, Any],
    inventory: Mapping[str, Any],
    item: Mapping[str, Any] | None,
    document: Mapping[str, Any] | None,
) -> None:
    ban_id = private.get("ban_id")
    if not isinstance(ban_id, str) or not ban_id.strip():
        raise GoldenSecurityError("documentary proof requires a private BAN key")
    distribution = inventory.get("distribution")
    total = inventory.get("total")
    if not isinstance(distribution, Mapping) or not _is_inventory_count(total):
        raise GoldenSecurityError("documentary inventory aggregate is malformed")
    has_document = item is not None
    if has_document != (document is not None):
        raise GoldenSecurityError("documentary proof is incomplete")

    assert_select_only(DPE_DOCUMENTARY_SOURCE_MATCH_SQL)
    row = conn.execute(
        DPE_DOCUMENTARY_SOURCE_MATCH_SQL,
        {
            "ban_id": ban_id,
            "department": entry["departement"],
            "inventory_total": total,
            "inventory_distribution": Jsonb(dict(distribution)),
            "has_document": has_document,
            "inventory_item": Jsonb(
                _source_projection(item or {}, _DPE_INVENTORY_ITEM_SOURCE_KEYS)
            ),
            "document": Jsonb(
                _source_projection(document or {}, _DPE_DOCUMENT_SOURCE_KEYS)
            ),
        },
    ).fetchone()
    expected_keys = {
        "inventory_aggregate_equal",
        "inventory_item_equal",
        "document_equal",
    }
    if (
        not isinstance(row, Mapping)
        or set(row) != expected_keys
        or any(type(row[key]) is not bool for key in expected_keys)
        or not all(row[key] for key in expected_keys)
    ):
        raise GoldenSecurityError("DPE documentary projection diverges from source")
    as_of_date = _inventory_contract_date(inventory)
    if as_of_date is not None:
        assert_select_only(DPE_LIFECYCLE_SOURCE_MATCH_SQL)
        row = conn.execute(
            DPE_LIFECYCLE_SOURCE_MATCH_SQL,
            {
                "ban_id": ban_id,
                "department": entry["departement"],
                "as_of_date": as_of_date,
                "has_document": has_document,
                "item_lifecycle": Jsonb((item or {}).get("lifecycle")),
                "document_lifecycle": Jsonb((document or {}).get("lifecycle")),
            },
        ).fetchone()
        if (
            not isinstance(row, Mapping)
            or dict(row) != {"lifecycle_equal": True}
            or type(row["lifecycle_equal"]) is not bool
        ):
            raise GoldenSecurityError("DPE lifecycle projection diverges from source")


def _prove_dpe_documentary_routes(  # noqa: C901
    conn: psycopg.Connection[Any],
    client: httpx.Client,
    entry: Mapping[str, Any],
    private: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Exercise inventory/document GETs and retain only closed proof constants."""
    if entry.get("type") != "address":
        return None
    resolved = payload.get("dpe_inventory")
    if not isinstance(resolved, Mapping):
        raise GoldenSecurityError("API response carries no closed dpe_inventory block")
    resolved_presence = dpe_inventory_presence(payload)
    resolved_date = _inventory_contract_date(resolved)
    context_ref = resolved.get("context_ref")
    if resolved_presence == "unknown":
        if context_ref is not None:
            raise GoldenSecurityError("unavailable DPE identity carries a context")
        proof = {
            "inventory_route": "not_applicable_identity_unavailable",
            "document_route": "not_applicable_identity_unavailable",
            "source_fields_equal": "not_applicable",
            "no_store": "not_applicable",
        }
        _assert_projection_is_clean(proof, "documentary_proof")
        return proof
    if not isinstance(context_ref, str) or not context_ref:
        raise GoldenSecurityError("DPE documentary context is missing")

    inventory_response = client.get(
        "/v1/bien/dpe/inventaire",
        params={"contexte": context_ref, "limite": 1},
    )
    inventory_response.raise_for_status()
    _assert_no_store(inventory_response, "DPE inventory")
    inventory = inventory_response.json()
    if not isinstance(inventory, Mapping):
        raise GoldenSecurityError("DPE inventory route has an unexpected shape")
    as_of_date = _inventory_contract_date(inventory)
    if as_of_date != resolved_date:
        raise GoldenSecurityError(
            "DPE inventory routes use different contracts or dates"
        )
    _assert_dpe_provenance(inventory, "DPE inventory")
    if inventory.get("context_ref") != context_ref:
        raise GoldenSecurityError("DPE inventory context changed")
    if inventory.get("page_size") != 1 or inventory.get("read_state") != "complete":
        raise GoldenSecurityError("DPE inventory route is not a complete bounded page")
    explicit_presence = dpe_inventory_presence({"dpe_inventory": inventory})
    if explicit_presence != resolved_presence:
        raise GoldenSecurityError("DPE inventory routes disagree")

    items = inventory.get("items")
    if not isinstance(items, list) or len(items) > 1:
        raise GoldenSecurityError("DPE inventory route has an unexpected item page")
    item: Mapping[str, Any] | None = None
    document: Mapping[str, Any] | None = None
    document_route = "not_applicable_empty_inventory"
    document_no_store = True
    if explicit_presence == "present":
        if len(items) != 1 or not isinstance(items[0], Mapping):
            raise GoldenSecurityError("DPE inventory route omitted its first item")
        item = items[0]
        lifecycle_keys = (
            _DPE_LIFECYCLE_SOURCE_KEYS if as_of_date is not None else frozenset()
        )
        if set(item) != _DPE_INVENTORY_ITEM_KEYS | lifecycle_keys:
            raise GoldenSecurityError("DPE inventory item has an unexpected shape")
        document_ref = item.get("document_ref")
        if not isinstance(document_ref, str) or not document_ref:
            raise GoldenSecurityError("DPE inventory item has no document reference")
        document_response = client.get(
            "/v1/bien/dpe/document",
            params={"contexte": context_ref, "reference": document_ref},
        )
        document_response.raise_for_status()
        _assert_no_store(document_response, "DPE document")
        document = document_response.json()
        if (
            not isinstance(document, Mapping)
            or set(document) != _DPE_DOCUMENT_KEYS | lifecycle_keys
        ):
            raise GoldenSecurityError("DPE document route has an unexpected shape")
        _assert_dpe_provenance(document, "DPE document")
        if (
            document.get("context_ref") != context_ref
            or document.get("document_ref") != document_ref
        ):
            raise GoldenSecurityError("DPE document references changed")
        if _source_projection(document, _DPE_INVENTORY_ITEM_SOURCE_KEYS) != (
            _source_projection(item, _DPE_INVENTORY_ITEM_SOURCE_KEYS)
        ):
            raise GoldenSecurityError("DPE inventory and document projections disagree")
        _assert_document_validity(document, as_of_date)
        if as_of_date is not None and item.get("lifecycle") != document.get(
            "lifecycle"
        ):
            raise GoldenSecurityError("DPE inventory and document lifecycle disagree")
        document_route = "verified"
    elif items:
        raise GoldenSecurityError("an empty DPE inventory carries items")

    _documentary_source_matches(
        conn,
        private=private,
        entry=entry,
        inventory=inventory,
        item=item,
        document=document,
    )
    if as_of_date is not None and as_of_date != _paris_today():
        raise GoldenSecurityError("DPE inventory reference date changed during replay")
    proof = {
        "inventory_route": "verified",
        "document_route": document_route,
        "source_fields_equal": True,
        "no_store": document_no_store,
    }
    _assert_projection_is_clean(proof, "documentary_proof")
    return proof


def _assert_dpe_inventory_attribution_coherent(
    payload: Mapping[str, Any], facts: Mapping[str, Any]
) -> None:
    block = payload.get("dpe_attribution")
    reason = block.get("reason") if isinstance(block, Mapping) else None
    expected = {
        ("unassigned", "dwelling_identity_unproven"): "present",
        ("absent", "not_reported"): "absent",
        ("absent", "identity_unavailable"): "unknown",
    }.get((facts["dpe_attribution_status"], reason))
    if expected != facts["dpe_inventory_presence"]:
        raise GoldenSecurityError("DPE inventory and attribution are inconsistent")


def _reported_absence_is_proven(
    payload: Mapping[str, Any], facts: Mapping[str, Any]
) -> bool:
    """Allow a proximity negative only after an exact-source absence."""
    block = payload.get("dpe_attribution")
    return (
        isinstance(block, Mapping)
        and block.get("reason") == "not_reported"
        and _exact_absence_is_proven(facts)
    )


def dpe_eligible_carriers(facts: Mapping[str, Any]) -> list[str]:
    """List the archetypes these observed facts PROVE, in schema order."""
    probe = {"invariants": {_DPE_INVARIANT_LAYER: dict(facts)}}
    return [
        carrier for carrier in DPE_CARRIERS if dpe_carrier_is_satisfied(probe, carrier)
    ]


def _observe_nearby_diagnostic(
    client: httpx.Client, payload: Mapping[str, Any]
) -> bool:
    """Probe the neighbourhood and return ONE boolean -- nothing else (T-51-25).

    The coordinates are read from the live payload and stay local variables:
    they are never returned, logged or serialized. The response rows are
    discarded on the spot; only their emptiness leaves this function. That
    boolean is the entire difference between "no DPE here" and "no DPE here,
    and we checked the neighbourhood" -- the negative that makes
    ``dpe_nearer_neighbor_negative`` a measurable archetype instead of a claim.
    """
    lat = payload.get("latitude")
    lon = payload.get("longitude")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        raise GoldenSecurityError("neighbourhood probe requires resolved coordinates")
    response = client.get(
        "/v1/dpe",
        params={
            "lat": lat,
            "lon": lon,
            "rayon_m": _NEARBY_PROBE_RADIUS_M,
            "limite": 1,
        },
    )
    response.raise_for_status()
    body = response.json()
    rows = body.get("donnees") if isinstance(body, Mapping) else None
    return bool(isinstance(rows, list) and rows)


def _project_dpe_qualification(
    client: httpx.Client, entry: Mapping[str, Any], payload: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Project the raw payload into the closed qualification facts, or nothing.

    Runs INSIDE ``collect_live_observation_projected`` so the raw payload is
    reduced before it can escape the call. Commune cases carry no attribution
    and are not qualified.
    """
    if entry.get("type") != "address":
        return None
    facts = dpe_attribution_facts(payload)
    observed_presence = dpe_inventory_presence(payload)
    expected_presence = _dpe_inventory_presence_from_entry(entry)
    if observed_presence != expected_presence:
        # Both values have passed the closed presence vocabulary. Report only
        # these enums so a stale expectation can be diagnosed without raw data.
        raise GoldenSecurityError(
            "live DPE inventory presence diverges from manifest: "
            f"expected={expected_presence}, observed={observed_presence}"
        )
    facts["dpe_inventory_presence"] = observed_presence
    if facts["dpe_attribution_status"] == "error":
        # D-13/D-14: an outage is a fact of the run, never an archetype. It
        # must fail the case rather than be recorded as an absence.
        raise GoldenSecurityError("dpe_attribution reported an unstable error state")
    _assert_dpe_inventory_attribution_coherent(payload, facts)
    facts["dpe_nearby_diagnostic_present"] = (
        _observe_nearby_diagnostic(client, payload)
        if _reported_absence_is_proven(payload, facts)
        else False
    )
    qualification = {
        "case_id": entry["case_id"],
        "departement": entry["departement"],
        **facts,
        "carrier_for": dpe_eligible_carriers(facts),
    }
    _assert_projection_is_clean(qualification, "qualification")
    return qualification


def _project_dpe_evidence(
    conn: psycopg.Connection[Any],
    client: httpx.Client,
    entry: Mapping[str, Any],
    private: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Reduce the raw resolve and documentary payloads before returning."""
    documentary = _prove_dpe_documentary_routes(
        conn,
        client,
        entry,
        private,
        payload,
    )
    qualification = _project_dpe_qualification(client, entry, payload)
    return qualification, documentary


def _assert_projection_is_clean(value: Any, label: str) -> None:
    """Fail closed on any forbidden key or sensitive value before writing."""
    if audit_manifest_keys(value):
        raise GoldenSecurityError(f"{label} projection carries a forbidden key")
    if audit_artifact_text(json.dumps(value, ensure_ascii=False), source=label):
        raise GoldenSecurityError(f"{label} projection carries a sensitive value")


def build_manifest_patch_entry(
    entry: Mapping[str, Any],
    observation: Mapping[str, Any],
    millesimes: Mapping[str, str],
    qualification: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a COMPLETE manifest entry from non-sensitive live metadata.

    Plan 51-10 must be able to freeze a new 44/85 case without reopening the
    private corpus, so every schema-required field is derived here and the
    result is validated before it is allowed anywhere near the report. An
    incomplete, schema-unknown or forbidden-key entry blocks the whole
    qualification rather than deferring the problem to the freeze.
    """
    api_states = observation["api"]
    category = derive_category(api_states)
    if category is None:
        raise GoldenSecurityError("manifest patch cannot freeze an unstable category")
    facts = {key: qualification[key] for key in DPE_INVARIANT_KEYS}
    patch = {
        "case_id": entry["case_id"],
        "type": entry["type"],
        "departement": entry["departement"],
        "code_insee": entry["code_insee"],
        "category": category,
        "frozen_at": date.today().isoformat(),
        "source_millesimes": dict(millesimes),
        "invariants": {
            "db": dict(observation["db"]),
            "api": dict(api_states),
            "mapper": dict(entry["invariants"]["mapper"]),
            _DPE_INVARIANT_LAYER: facts,
        },
        "private_ref": entry["case_id"],
    }
    carriers = list(qualification["carrier_for"])
    if carriers:
        patch[_DPE_CARRIER_KEY] = carriers
    # Audit the RAW patch first, on purpose. ``redact_entry`` would strip a
    # forbidden key silently and hand Plan 51-10 a quietly amputated entry;
    # the contract says a forbidden key BLOCKS the qualification. Redaction
    # stays below as defence in depth, never as the primary guard (T-51-25).
    _assert_projection_is_clean(patch, "manifest_patch")
    redacted = redact_entry(patch)
    if validate_case_entry(redacted, load_schema()):
        raise GoldenSecurityError("manifest patch entry violates the minimized schema")
    _assert_projection_is_clean(redacted, "manifest_patch")
    return redacted


def collect_live_observation(
    conn: psycopg.Connection[Any],
    client: httpx.Client,
    entry: Mapping[str, Any],
    private: Mapping[str, Any],
    *,
    resolve_logging_redacted: bool = False,
) -> tuple[dict[str, Any], dict[str, str], dict[str, int]]:
    """Collect minimized DB/API evidence for one case without returning PII."""
    observation, millesimes, metrics, _ = collect_live_observation_projected(
        conn,
        client,
        entry,
        private,
        lambda _payload: None,
        resolve_logging_redacted=resolve_logging_redacted,
    )
    return observation, millesimes, metrics


def collect_live_observation_projected(
    conn: psycopg.Connection[Any],
    client: httpx.Client,
    entry: Mapping[str, Any],
    private: Mapping[str, Any],
    projector: Callable[[Mapping[str, Any]], Any],
    *,
    resolve_logging_redacted: bool = False,
) -> tuple[dict[str, Any], dict[str, str], dict[str, int], Any]:
    """Collect evidence and project the raw API payload before it leaves scope.

    The third element carries the R3-02 rattachement metrics (bounded integer
    counters, never a key or an address) -- empty for commune cases.
    """
    db, millesimes, metrics = _db_observation(conn, entry, private)
    payload = _api_payload(
        client,
        entry,
        private,
        resolve_logging_redacted=resolve_logging_redacted,
    )
    datasets = {key.removesuffix("_state") for key in entry["invariants"]["api"]}
    api = {
        f"{dataset}_state": _dataset_state(payload, dataset, entry["type"])
        for dataset in datasets
    }
    return {"db": db, "api": api}, millesimes, metrics, projector(payload)


def _compare_observation(
    expected: Mapping[str, Any], actual: Mapping[str, Any]
) -> list[str]:
    reasons: list[str] = []
    # Mapper assertions run in the protected workflow against the real frontend
    # fixture. This Python runner owns only the DB/API layers.
    for layer in ("db", "api"):
        for key, value in expected[layer].items():
            observed = actual.get(layer, {}).get(key)
            if observed != value:
                reasons.append(f"{layer}.{key}: mismatch{_observed_suffix(observed)}")
    return reasons


def _compare_dpe_observation(
    expected: Mapping[str, Any], observed: Mapping[str, Any] | None
) -> list[str]:
    """Compare every frozen DPE fact without exposing unbounded values."""
    expected_dpe = expected.get(_DPE_INVARIANT_LAYER)
    expected_facts = expected_dpe if isinstance(expected_dpe, Mapping) else {}
    observed_facts = observed if isinstance(observed, Mapping) else {}
    reasons: list[str] = []
    for key in DPE_INVARIANT_KEYS:
        expected_present = key in expected_facts
        observed_present = key in observed_facts
        value = observed_facts.get(key)
        if (
            not expected_present
            or not observed_present
            or expected_facts.get(key) != value
        ):
            suffix = _dpe_observed_suffix(key, value) if observed_present else ""
            reasons.append(f"dpe.{key}: mismatch{suffix}")
    return reasons


def _dpe_observed_suffix(key: str, observed: Any) -> str:
    """Render only closed DPE enums, booleans and bounded counters."""
    if key == "dpe_inventory_presence" and observed in DPE_INVENTORY_PRESENCES:
        return f" (observed {observed})"
    if key == "dpe_attribution_status" and observed in DPE_ATTRIBUTION_STATUSES:
        return f" (observed {observed})"
    if key in DPE_BOOLEAN_INVARIANTS and type(observed) is bool:
        return f" (observed {str(observed).lower()})"
    if key in DPE_COUNT_INVARIANTS and _is_count(observed):
        return f" (observed {observed})"
    return ""


def _observed_suffix(observed: Any) -> str:
    """Name the observed value when -- and only when -- it is bounded.

    ``db`` invariants are booleans and ``api`` invariants are coarse dataset
    states: both are closed sets that say nothing about the address. Anything
    else stays unnamed, so a future invariant carrying a free-form value cannot
    silently start leaking into the uploaded report.
    """
    if isinstance(observed, bool):
        return f" (observed {str(observed).lower()})"
    if isinstance(observed, str) and observed in _OBSERVABLE_STATES:
        return f" (observed {observed})"
    return ""


def _write_report(
    results: list[dict[str, Any]],
    ok: bool,
    *,
    qualification: list[dict[str, Any]] | None = None,
    manifest_patch: list[dict[str, Any]] | None = None,
    served_checkout_sha: str | None = None,
) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "report": "golden-live-report",
        "generated_at": datetime.now(UTC).isoformat(),
        "redacted": True,
        "ok": ok,
        # T-51-28: the commit the preview actually served, proven equal to the
        # checkout before the first private case was observed.
        "served_checkout_sha": served_checkout_sha,
        "cases": results,
        "dpe_qualification": list(qualification or []),
        "manifest_patch": list(manifest_patch or []),
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def _run_audit(manifest_path: str, frontend_fixture_path: str) -> int:
    schema = load_schema()
    manifest_text = Path(manifest_path).read_text(encoding="utf-8")
    fixture_text = Path(frontend_fixture_path).read_text(encoding="utf-8")
    violations = audit_artifact_text(manifest_text, "manifest")
    violations += audit_artifact_text(fixture_text, "frontend-fixture")
    manifest = json.loads(manifest_text)
    violations += audit_manifest_keys(manifest)
    reasons = validate_manifest(manifest, schema)
    print(f"audit: {'OK' if not violations and not reasons else 'BLOCKED'}")
    print(f"schema_errors: {len(reasons)}")
    print(f"leak_indicators: {len(set(violations))}")
    return 0 if not violations and not reasons else 1


def _run_live(args: argparse.Namespace) -> int:
    manifest_text = Path(args.manifest).read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    reasons = validate_manifest(manifest, load_schema())
    violations = audit_artifact_text(manifest_text, "manifest") + audit_manifest_keys(
        manifest
    )
    if reasons or violations:
        print("live replay: BLOCKED by manifest audit")
        _write_report(
            [
                {
                    "case_id": "manifest",
                    "type": "contract",
                    "ok": False,
                    "reasons": ["manifest_audit_failed"],
                }
            ],
            False,
        )
        return 1
    base_url = _required_env(args.api_base_url_env)
    _assert_loopback_base_url(base_url)
    headers = {
        "X-API-Key": _required_env(args.api_key_env),
        # Loopback marker: the preview declares the same constant, so the
        # request-logging middleware skips the api_request_log INSERT and the
        # read-only replay attempts no write at all (T-51-27).
        SMOKE_HEADER[0]: SMOKE_HEADER[1],
    }
    results: list[dict[str, Any]] = []
    served_sha: str | None = None
    with httpx.Client(base_url=base_url, headers=headers, timeout=30) as client:
        # Provenance BEFORE the private corpus is read: a wrong commit must
        # abort while this process still holds nothing private (T-51-28).
        served_sha = _assert_served_checkout_sha(client, args.expected_sha_env)
        private_cases = _read_private_input(args.private_input, args.private_input_env)
        _required_redaction_confirmation(args.resolve_log_redaction_env)
        conn = _open_read_only_connection(_required_env(args.dsn_env))
        try:
            for index, entry in enumerate(manifest["cases"]):
                if index:
                    # One address can issue two resolve GETs plus inventory and
                    # document/proximity GETs. Nine seconds admits at most seven
                    # cases per 60 s: 7 * 4 + two preview/SHA probes = 30.
                    time.sleep(_LIVE_REPLAY_CASE_INTERVAL_SECONDS)
                results.append(
                    _verify_case(
                        conn,
                        client,
                        entry,
                        private_cases,
                        resolve_logging_redacted=True,
                    )
                )
        finally:
            conn.rollback()
            conn.close()
    ok = all(result["ok"] for result in results)
    qualification, manifest_patch = _lift_qualification(results)
    _write_report(
        results,
        ok,
        qualification=qualification,
        manifest_patch=manifest_patch,
        served_checkout_sha=served_sha,
    )
    print(f"live replay: {'OK' if ok else 'BLOCKED'} ({len(results)} redacted cases)")
    print(f"dpe qualification: {len(qualification)} carriers observed")
    return 0 if ok else 1


def _lift_qualification(
    results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Move the qualification blocks from each case to the report top level."""
    qualification: list[dict[str, Any]] = []
    manifest_patch: list[dict[str, Any]] = []
    for result in results:
        observed = result.pop("dpe_qualification", None)
        patch = result.pop("manifest_patch", None)
        if observed is not None:
            qualification.append(observed)
        if patch is not None:
            manifest_patch.append(patch)
    return qualification, manifest_patch


def _verify_case(  # noqa: C901
    conn: psycopg.Connection[Any],
    client: httpx.Client,
    entry: Mapping[str, Any],
    private_cases: Mapping[str, Any],
    *,
    resolve_logging_redacted: bool = False,
) -> dict[str, Any]:
    case_id = entry["case_id"]
    private = private_cases.get(case_id)
    if not isinstance(private, dict):
        return {
            "case_id": case_id,
            "type": entry["type"],
            "ok": False,
            "reasons": ["private_input_missing"],
        }
    metrics: dict[str, int] = {}
    qualification: dict[str, Any] | None = None
    documentary_proof: dict[str, Any] | None = None
    patch: dict[str, Any] | None = None
    try:
        actual, millesimes, metrics, projected = collect_live_observation_projected(
            conn,
            client,
            entry,
            private,
            lambda payload: _project_dpe_evidence(
                conn,
                client,
                entry,
                private,
                payload,
            ),
            resolve_logging_redacted=resolve_logging_redacted,
        )
        qualification, documentary_proof = projected
        reasons = _compare_observation(entry["invariants"], actual)
        if qualification is not None or _DPE_INVARIANT_LAYER in entry["invariants"]:
            reasons.extend(_compare_dpe_observation(entry["invariants"], qualification))
        if qualification is not None and not reasons:
            patch = build_manifest_patch_entry(entry, actual, millesimes, qualification)
    except GoldenSecurityError as exc:
        # Guard messages are static internal constants (no address, no DSN,
        # no coordinate can transit through them): naming the failed guard --
        # e.g. a private entry missing the ban_id rattachement key -- beats
        # the unactionable generic reason below (R3-01).
        reasons = [f"golden_security: {exc}"]
        qualification = documentary_proof = patch = None
    except Exception as exc:  # noqa: BLE001
        reasons = [_observation_failure_reason(exc)]
        qualification = documentary_proof = patch = None
    if reasons and qualification is not None:
        # NOT a carrier: T-51-26 keeps `dpe_qualification` and `manifest_patch`
        # off a failed case, and nothing in the freeze path reads `reasons`.
        # The attribution STATUS alone is the variable that decides whether a
        # divergence is the Phase 51 policy doing its job on a multi-logement
        # or a regression, and it is one of five closed constants -- without it
        # the only way to read a red case is to guess.
        status = qualification.get("dpe_attribution_status")
        if status in DPE_ATTRIBUTION_STATUSES:
            reasons.append(f"dpe_attribution: {status}")
        # Only when the neighbourhood was actually probed. The fact defaults to
        # False whenever the exact absence is NOT proven, and publishing that
        # default would report "nothing nearby" for a measure that never ran --
        # the very confusion _exact_absence_is_proven exists to prevent. On a
        # measured absence the boolean separates "the old value was a
        # neighbour's DPE, Phase 51 removed it" from "the row vanished".
        if _exact_absence_is_proven(qualification):
            nearby = qualification.get("dpe_nearby_diagnostic_present")
            reasons.append(f"dpe_nearby: {str(bool(nearby)).lower()}")
    result = {
        "case_id": case_id,
        "type": entry["type"],
        "ok": not reasons,
        "reasons": reasons,
    }
    if metrics:
        # R3-02: surface the observed rattachement counters in the redacted
        # report -- integers only. This is the observation material required
        # by amendment 43-09 before dpe_rattachement_ok may be frozen.
        result["metrics"] = metrics
    # A failed case publishes NO carrier: an unstable observation must never
    # become material for the Plan 51-10 freeze (T-51-26).
    if not reasons and qualification is not None:
        result["dpe_qualification"] = qualification
        if documentary_proof is not None:
            result["dpe_documentary_proof"] = documentary_proof
        if patch is not None:
            result["manifest_patch"] = patch
    return result


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-artifacts-only", action="store_true")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--frontend-fixture")
    private = parser.add_mutually_exclusive_group()
    private.add_argument("--private-input", help="Path to gitignored private JSON")
    private.add_argument(
        "--private-input-env", help="Environment variable containing private JSON"
    )
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--dsn-env", default="DATABASE_URL")
    parser.add_argument("--api-base-url-env", default="API_BASE_URL")
    parser.add_argument("--api-key-env", default="DC_API_KEY")
    parser.add_argument(
        "--resolve-log-redaction-env",
        default="DC_GOLDEN_RESOLVE_LOG_REDACTED",
    )
    parser.add_argument("--expected-sha-env", default=EXPECTED_SHA_ENV)
    args = parser.parse_args(argv)
    if args.audit_artifacts_only:
        live_only = {
            "--private-input",
            "--private-input-env",
            "--read-only",
            "--dsn-env",
            "--api-base-url-env",
            "--api-key-env",
            "--resolve-log-redaction-env",
            "--expected-sha-env",
        }
        if not args.frontend_fixture or any(flag in raw_argv for flag in live_only):
            parser.error("audit mode requires only --manifest and --frontend-fixture")
        return _run_audit(args.manifest, args.frontend_fixture)
    if not args.read_only or not (args.private_input or args.private_input_env):
        parser.error("live replay requires --read-only and one private input source")
    if args.frontend_fixture:
        parser.error("--frontend-fixture is audit-only")
    try:
        return _run_live(args)
    except (GoldenSecurityError, OSError, ValueError, httpx.HTTPError, psycopg.Error):
        _write_report(
            [
                {
                    "case_id": "runner",
                    "type": "contract",
                    "ok": False,
                    "reasons": ["live_replay_failed"],
                }
            ],
            False,
        )
        print("live replay: BLOCKED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
