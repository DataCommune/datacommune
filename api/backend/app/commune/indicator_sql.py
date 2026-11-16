"""Read-only internal SQL projections; never source/publication qualification.

The caller owns connection, search_path, timeouts, transaction and publication
admission. Each function uses one statement/snapshot, including global duplicate
checks before any geographic/eligibility filter. No commit, lock, DDL, route,
generation or trust-pin inference occurs here. Source contracts are the physical
087 DVF projection and the already canonical documentary DPE projection.
"""

from calendar import monthrange
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.commune.indicator_schemas import (
    HOSTED_DEPARTEMENTS,
    ResultatDpeIndicateur,
    hosted_commune,
    hosted_departements,
)
from app.commune.indicators_dpe import DpeDocumentaryCalculation
from app.commune.indicators_dvf import WindowStatistic
from app.dvf.couverture import SEUIL_ND_COMMUNE

# Unicode White_Space used by Pydantic's Rust regex \S (not locale POSIX space).
_DVF_ID_WHITESPACE = (
    "\u0009\u000a\u000b\u000c\u000d\u0020\u0085\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)
# str.strip additionally regards the four information separators as whitespace.
_DPE_ID_STRIP = _DVF_ID_WHITESPACE + "\u001c\u001d\u001e\u001f"


class IndicatorSqlRefused(ValueError):  # noqa: N818
    """Closed diagnostic code, never a private canonical identifier."""


@dataclass(frozen=True)
class DpeCommuneProjection:
    commune: str
    calculation: DpeDocumentaryCalculation


def _communes(
    value: tuple[str, ...], departments: Collection[str] = HOSTED_DEPARTEMENTS
) -> list[str]:
    if (
        not isinstance(value, tuple)
        or not value
        or any(not hosted_commune(c, departments) for c in value)
        or len(set(value)) != len(value)
    ):
        raise IndicatorSqlRefused("invalid_reference_keys")
    return sorted(value)


def _departments(value: tuple[str, ...]) -> list[str]:
    try:
        return list(hosted_departements(value))
    except ValueError:
        raise IndicatorSqlRefused("invalid_lot_departments") from None


def _calendar(value: date) -> None:
    if type(value) is not date:
        raise IndicatorSqlRefused("explicit_calendar_date_required")


def _read(conn: psycopg.Connection[Any], query: str, params: dict) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        result = cur.execute(query, params).fetchone()
    if result is None or result["invalid"]:
        raise IndicatorSqlRefused("invalid_canonical_source_projection")
    return result["cells"]


def _counts(conditions: dict[str, str]) -> str:
    # All keys/expressions below are closed module constants, never caller data.
    return (
        "jsonb_build_object("
        + ",".join(
            f"'{key}',count(d.numero_dpe) FILTER (WHERE {condition})"
            for key, condition in conditions.items()
        )
        + ")"
    )


_CLASSES = _counts({c: f"d.etiquette_dpe='{c}'" for c in "ABCDEFG"})
_BUILDINGS = _counts(
    {
        "maison": "d.type_batiment='maison'",
        "appartement": "d.type_batiment='appartement'",
        "autre": "d.type_batiment IN ('immeuble','autre')",
        "inconnu": "d.type_batiment IS NULL",
    }
)
_VALIDITY = _counts(
    {
        key: f"d.validity='{key}'"
        for key in (
            "unknown",
            "expired",
            "not_expired_by_date",
            "inconsistent_dates",
            "future_establishment",
        )
    }
)
_REPLACEMENTS = _counts(
    {
        key: f"d.replacement='{key}'"
        for key in ("not_checked", "no_successor_known", "replaced", "ambiguous")
    }
)
_SOURCES = _counts(
    {
        key: f"d.source_state='{key}'"
        for key in (
            "present",
            "unknown",
            "absent_from_complete_scope",
            "explicitly_deactivated",
        )
    }
)
_GRAPHS = _counts(
    {
        key: f"d.graph_issues ? '{key}'"
        for key in ("cycle", "fork", "missing_target", "truncated", "ambiguous_target")
    }
)
_REGIMES = _counts(
    {
        "existant": "d.existant",
        "neuf": "d.neuf",
        "inconnu": "NOT d.existant AND NOT d.neuf",
    }
)

_DPE_SQL = f"""
WITH raw AS MATERIALIZED (
 SELECT r.numero_dpe,r.code_insee,r.etiquette_dpe,r.type_batiment,r.source_state,
        r.is_tombstone,r.date_etablissement_dpe,r.date_fin_validite_dpe,
    r.annee_construction,
        r.lifecycle_facts->>'replacement_state' AS replacement,
        CASE WHEN jsonb_typeof(r.lifecycle_facts->'graph_issues')='array' THEN
          coalesce((SELECT jsonb_agg(v ORDER BY v) FROM
            (SELECT DISTINCT v FROM
    jsonb_array_elements(r.lifecycle_facts->'graph_issues') e(v)) a),'[]'::jsonb)
          ELSE r.lifecycle_facts->'graph_issues' END AS graph_issues,
        CASE WHEN jsonb_typeof(r.lifecycle_facts->'date_issues')='array' THEN
          coalesce((SELECT jsonb_agg(v ORDER BY v) FROM
            (SELECT DISTINCT v FROM
    jsonb_array_elements(r.lifecycle_facts->'date_issues') e(v)) a),'[]'::jsonb)
          ELSE r.lifecycle_facts->'date_issues' END AS date_issues,
        r.revision_evidence->>'status' AS revision,
        CASE WHEN jsonb_array_length(obs.items)>0 THEN
          EXISTS (SELECT 1 FROM jsonb_array_elements(obs.items) o WHERE
    o->>'source_slug'='dpe03existant')
          ELSE coalesce(r.regime_dpe='existant',false) END AS existant,
        CASE WHEN jsonb_array_length(obs.items)>0 THEN
          EXISTS (SELECT 1 FROM jsonb_array_elements(obs.items) o WHERE
    o->>'source_slug'='dpe02neuf')
          ELSE coalesce(r.regime_dpe='neuf',false) END AS neuf,
        (jsonb_typeof(r.source_observations) IS DISTINCT FROM 'array'
         OR EXISTS (SELECT 1 FROM jsonb_array_elements(obs.items) o
             WHERE o->>'source_slug' IS NULL OR o->>'source_slug' NOT IN
    ('dpe03existant','dpe02neuf'))
         OR (r.regime_dpe IS NOT NULL AND r.regime_dpe NOT IN ('existant','neuf')))
    AS invalid_regime
 FROM diagnostics_dpe r
 CROSS JOIN LATERAL (SELECT CASE WHEN jsonb_typeof(r.source_observations)='array'
                            THEN r.source_observations ELSE '[]'::jsonb END AS items)
    obs
), canonical AS MATERIALIZED (SELECT DISTINCT * FROM raw),
bad AS (
 SELECT EXISTS (SELECT 1 FROM canonical GROUP BY numero_dpe HAVING count(*)>1)
 OR EXISTS (SELECT 1 FROM canonical WHERE
   numero_dpe IS NULL OR numero_dpe=''
   OR numero_dpe<>btrim(numero_dpe,%(identity_strip)s)
   OR is_tombstone IS NULL OR invalid_regime
   OR (etiquette_dpe IS NOT NULL AND etiquette_dpe NOT IN ('A','B','C','D','E','F','G'))
   OR (type_batiment IS NOT NULL AND type_batiment NOT IN ('maison','appartement',
    'immeuble','autre'))
   OR source_state IS NULL OR source_state NOT IN ('present','unknown',
    'absent_from_complete_scope','explicitly_deactivated')
   OR replacement IS NULL OR replacement NOT IN ('not_checked','no_successor_known',
    'replaced','ambiguous')
   OR revision IS DISTINCT FROM 'unverified'
   OR jsonb_typeof(graph_issues) IS DISTINCT FROM 'array'
   OR NOT (graph_issues <@
    '["cycle","fork","missing_target","truncated","ambiguous_target"]'::jsonb)
   OR jsonb_typeof(date_issues) IS DISTINCT FROM 'array'
   OR NOT (date_issues <@ '["invalid_establishment_date","invalid_end_date"]'::jsonb)
   OR (date_issues ? 'invalid_establishment_date' AND date_etablissement_dpe IS NOT
    NULL)
   OR (date_issues ? 'invalid_end_date' AND date_fin_validite_dpe IS NOT NULL)
   OR annee_construction NOT BETWEEN 1 AND 9999
   OR date_etablissement_dpe NOT BETWEEN DATE '0001-01-01' AND DATE '9999-12-31'
   OR date_fin_validite_dpe NOT BETWEEN DATE '0001-01-01' AND DATE '9999-12-31'
 ) AS invalid
), documents AS (
 SELECT *, CASE
   WHEN date_issues<>'[]'::jsonb OR date_fin_validite_dpe<date_etablissement_dpe THEN
    'inconsistent_dates'
   WHEN date_etablissement_dpe>%(as_of)s THEN 'future_establishment'
   WHEN date_etablissement_dpe IS NULL OR date_fin_validite_dpe IS NULL THEN 'unknown'
   WHEN date_fin_validite_dpe<%(as_of)s THEN 'expired'
   ELSE 'not_expired_by_date' END AS validity
 FROM canonical WHERE NOT is_tombstone AND source_state NOT IN
    ('absent_from_complete_scope','explicitly_deactivated')
), stats AS (
 SELECT c.code, count(d.numero_dpe) AS total,
   count(d.etiquette_dpe) AS known, {_CLASSES} AS classes,
   {_BUILDINGS} AS buildings, {_VALIDITY} AS validities, {_REPLACEMENTS} AS
    replacements,
   {_SOURCES} AS sources, {_GRAPHS} AS graphs, {_REGIMES} AS regimes,
   count(d.numero_dpe) FILTER (WHERE d.source_state='unknown') AS unknown_source,
   percentile_disc(0.5) WITHIN GROUP (ORDER BY d.etiquette_dpe) AS ordinal,
   count(d.numero_dpe) FILTER (WHERE d.etiquette_dpe IN ('F','G')) AS fg,
   count(d.annee_construction) AS construction_known,
   percentile_disc(0.5) WITHIN GROUP (ORDER BY d.annee_construction) AS
    construction_median,
   min(d.date_etablissement_dpe) AS earliest, max(d.date_etablissement_dpe) AS latest,
   count(d.date_etablissement_dpe) AS dates_known,
   count(d.numero_dpe) FILTER (WHERE validity='not_expired_by_date' AND
    replacement='no_successor_known') AS restricted
 FROM unnest(%(communes)s::text[]) c(code)
 LEFT JOIN documents d ON d.code_insee=c.code
 GROUP BY c.code
), raw_counts AS MATERIALIZED (
 SELECT code_insee, count(*) AS inputs FROM raw GROUP BY code_insee
), canonical_counts AS MATERIALIZED (
 SELECT code_insee, count(*) AS documents,
   count(*) FILTER (WHERE is_tombstone OR source_state IN
     ('absent_from_complete_scope','explicitly_deactivated')) AS removed
 FROM canonical GROUP BY code_insee
), cells AS (
 SELECT s.code AS commune, jsonb_build_object(
   'etat','value','population',jsonb_build_object('total',s.total,'classes_connues',
    s.known,
     'types_batiment',s.buildings,'validite',s.validities,'remplacement',s.replacements,
     'regimes',s.regimes,'source_inconnue',s.unknown_source,'revision_non_verifiee',
    s.total),
   'distribution',jsonb_build_object('classes',s.classes,'inconnue',s.total-s.known),
   'modes',coalesce((SELECT jsonb_agg(key ORDER BY key) FROM jsonb_each_text(s.classes)
      WHERE value::bigint=(SELECT max(value::bigint) FROM jsonb_each_text(s.classes))
    AND s.known>0),'[]'::jsonb),
   'mediane_ordinale',s.ordinal,
   'part_fg_pct',CASE WHEN s.known>0 THEN jsonb_build_object('etat','value','valeur',
    100.0*s.fg/s.known)
     ELSE jsonb_build_object('etat','zero_denominator','motif',
    'Aucune classe DPE connue') END,
   'date_etablissement_min',s.earliest,'date_etablissement_max',s.latest,
   'dates_etablissement_inconnues',s.total-s.dates_known) AS result,
   s.construction_known,s.total-s.construction_known AS construction_unknown,
    s.construction_median,
   s.graphs AS graph_issues,s.sources AS source_states,s.restricted AS
    restricted_date_and_no_successor_known,
   coalesce(c.removed,0) AS excluded_removed,
   coalesce(r.inputs,0)-coalesce(c.documents,0) AS duplicate_inputs
 FROM stats s
 LEFT JOIN raw_counts r ON r.code_insee=s.code
 LEFT JOIN canonical_counts c ON c.code_insee=s.code
)
SELECT bad.invalid, CASE WHEN NOT bad.invalid THEN
  (SELECT jsonb_agg(to_jsonb(cells) ORDER BY commune) FROM cells) ELSE '[]'::jsonb
    END AS cells FROM bad
"""


def query_dpe_indicators(
    conn: psycopg.Connection[Any], *, communes: tuple[str, ...], as_of_date: date
) -> tuple[DpeCommuneProjection, ...]:
    """Documentary counts for explicit COG keys; global facts are already supplied."""
    keys = _communes(communes)
    _calendar(as_of_date)
    cells = _read(
        conn,
        _DPE_SQL,
        {"communes": keys, "as_of": as_of_date, "identity_strip": _DPE_ID_STRIP},
    )
    result = []
    for cell in cells:
        code = cell.pop("commune")
        payload = ResultatDpeIndicateur.model_validate(cell.pop("result"))
        result.append(
            DpeCommuneProjection(
                code, DpeDocumentaryCalculation(as_of_date, payload, **cell)
            )
        )
    return tuple(result)


# 108 amount NUMERIC(15,2) (typmod 983046, the type of mutations since 015):
# cents are integral, |cents|<=999999999999999.
# 087 surface INTEGER: eligible 1<=area<=2147483647. Two DISTINCT prices differ
# by >=1/(100*2147483647**2). Scaling by 10**24 makes this >2168; rounding the
# NUMERIC quotient to an integer changes it by <1, so distinct order is exact.
# The largest scaled price is <10**37, safely within NUMERIC(60,0). Return only
# the central original numerator/denominator pairs; Python constructs Fraction,
# never a binary-float percentile or a rounded monetary median. Domain checked
# in this same statement before any result is accepted.
_DVF_SQL = """
WITH raw AS MATERIALIZED (
 SELECT id_mutation,code_departement,code_commune,date_mutation,classe_mutation,
   mono_local,est_vente,hors_stats_vefa,nb_communes,surface_bati_totale,valeur_fonciere
 FROM mutations_grain
), canonical AS MATERIALIZED (SELECT DISTINCT * FROM raw), bad AS (
 SELECT
  NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=to_regclass('mutations_grain')
    AND attname='valeur_fonciere' AND atttypid='numeric'::regtype AND
    atttypmod=983046 AND NOT attisdropped)
  OR NOT EXISTS (SELECT 1 FROM pg_attribute WHERE
    attrelid=to_regclass('mutations_grain')
    AND attname='surface_bati_totale' AND atttypid='integer'::regtype AND NOT
    attisdropped)
  OR EXISTS (SELECT 1 FROM canonical GROUP BY id_mutation HAVING count(*)>1)
  OR EXISTS (SELECT 1 FROM canonical WHERE id_mutation IS NULL OR id_mutation='' OR
    char_length(id_mutation)>120
    OR translate(id_mutation,%(identity_whitespace)s,'')<>id_mutation
    OR code_departement IS NULL
    OR NOT (code_departement = ANY(%(departments)s::text[]))
    OR (code_commune IS NOT NULL AND (code_commune !~ '^([0-9]{2}|2[AB])[0-9]{3}$'
    OR CASE WHEN left(code_commune,2)='97' THEN left(code_commune,3)
       ELSE left(code_commune,2) END<>code_departement))
    OR mono_local IS NULL
    OR (classe_mutation IN ('residentiel_maison','residentiel_appartement') AND NOT
    mono_local)
    OR classe_mutation IS NULL OR classe_mutation NOT IN ('residentiel_maison',
    'residentiel_appartement',
      'ensemble_residentiel','mixte','commerciale','terrain_seul','hors_perimetre')
    OR nb_communes<0 OR valeur_fonciere::text IN ('NaN','Infinity','-Infinity')
    OR abs(valeur_fonciere)>9999999999999.99 OR valeur_fonciere*
    100<>trunc(valeur_fonciere*100)
    OR date_mutation NOT BETWEEN DATE '0001-01-01' AND DATE '9999-12-31'
  ) AS invalid
), periods(period,lower,upper) AS (
 VALUES ('previous',%(start)s::date,%(middle)s::date),('current',%(middle)s::date,
    %(end)s::date)
), eligible AS (
 SELECT d.*,p.period,p.lower,p.upper,
   (valeur_fonciere*1000000000000000000000000::numeric/
    surface_bati_totale)::numeric(60,0) AS price_rank
 FROM canonical d JOIN periods p ON d.date_mutation>=p.lower AND d.date_mutation<p.upper
 WHERE classe_mutation IN ('residentiel_maison','residentiel_appartement') AND
    mono_local
   AND est_vente IS TRUE AND hors_stats_vefa IS FALSE AND nb_communes=1
   AND surface_bati_totale>0 AND valeur_fonciere>0 AND
    code_commune=ANY(%(communes)s::text[])
), ranked AS (
 SELECT *, count(*) OVER (PARTITION BY code_commune,classe_mutation,period) AS n,
   row_number() OVER (PARTITION BY code_commune,classe_mutation,period ORDER BY
    price_rank,id_mutation) AS rank
 FROM eligible
), central AS (
 SELECT code_commune,classe_mutation,period,
   array_agg(valeur_fonciere::text ORDER BY rank) AS numerators,
   array_agg(surface_bati_totale ORDER BY rank) AS denominators
 FROM ranked WHERE n>=%(minimum)s AND rank IN ((n+1)/2,(n+2)/2)
 GROUP BY code_commune,classe_mutation,period
), amounts AS (
 SELECT code_commune,classe_mutation,period,count(*) AS n FROM eligible
 GROUP BY code_commune,classe_mutation,period
), cells AS (
 SELECT c.code AS commune,k.kind,p.period,p.lower AS start_inclusive,p.upper AS
    end_exclusive,
   coalesce(a.n,0) AS count, CASE WHEN a.n IS NULL THEN 'empty_sample'
     WHEN a.n<%(minimum)s THEN 'insufficient_sample' ELSE 'value' END AS median_state,
   m.numerators,m.denominators
 FROM unnest(%(communes)s::text[]) c(code)
 CROSS JOIN (VALUES ('residentiel_maison'),('residentiel_appartement')) k(kind)
 CROSS JOIN periods p
 LEFT JOIN amounts a ON a.code_commune=c.code AND a.classe_mutation=k.kind AND
    a.period=p.period
 LEFT JOIN central m ON m.code_commune=c.code AND m.classe_mutation=k.kind AND
    m.period=p.period
)
SELECT bad.invalid,CASE WHEN NOT bad.invalid THEN
  (SELECT jsonb_agg(to_jsonb(cells) ORDER BY commune,
    CASE kind WHEN 'residentiel_maison' THEN 0 ELSE 1 END,
    CASE period WHEN 'previous' THEN 0 ELSE 1 END) FROM cells) ELSE '[]'::jsonb END
    AS cells FROM bad
"""


def query_dvf_indicators(
    conn: psycopg.Connection[Any],
    *,
    communes: tuple[str, ...],
    departments: tuple[str, ...],
    end_exclusive: date,
    observed_at: datetime,
) -> tuple[WindowStatistic, ...]:
    """Counts/exact medians over whole reconstructed mutations, never raw lines.

    *departments* are the received departements of the qualified lot: the whole
    projection is refused if one mutation lies elsewhere, or if a commune is
    filed under a departement which is not its own (dc_departement_from_insee).
    """
    lot = _departments(departments)
    keys = _communes(communes, lot)
    _calendar(end_exclusive)
    if (
        end_exclusive.year < 3
        or not isinstance(observed_at, datetime)
        or observed_at.tzinfo is None
        or observed_at.utcoffset() is None
        or end_exclusive > observed_at.date()
    ):
        raise IndicatorSqlRefused("invalid_period_observation")

    def previous(value: date) -> date:
        return value.replace(
            year=value.year - 1,
            day=min(value.day, monthrange(value.year - 1, value.month)[1]),
        )

    middle = previous(end_exclusive)
    cells = _read(
        conn,
        _DVF_SQL,
        dict(
            communes=keys,
            departments=lot,
            start=previous(middle),
            middle=middle,
            end=end_exclusive,
            minimum=SEUIL_ND_COMMUNE,
            identity_whitespace=_DVF_ID_WHITESPACE,
        ),
    )
    result = []
    for cell in cells:
        numerators, denominators = cell.pop("numerators"), cell.pop("denominators")
        median = None
        if numerators is not None:
            median = sum(
                (
                    Fraction(Decimal(n)) / d
                    for n, d in zip(numerators, denominators, strict=True)
                ),
                Fraction(),
            ) / len(numerators)
        cell["start_inclusive"] = date.fromisoformat(cell["start_inclusive"])
        cell["end_exclusive"] = date.fromisoformat(cell["end_exclusive"])
        result.append(WindowStatistic(**cell, median_eur_m2=median))
    return tuple(result)
