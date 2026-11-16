"""Raw SQL queries for commune synthese matview lookups."""

from collections.abc import Collection, Sequence

from fastapi import HTTPException
from sqlalchemy import RowMapping, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.publication_readiness import acquire_publication_read_lock
from app.commune.dependencies import (
    ANNUEL_EDGES,
    COMMUNE_EDGES,
    COMMUNE_INTERMEDIATES,
    COMMUNE_SOURCES,
    DEPARTEMENT_EDGES,
    DEPARTEMENT_VIEW_SOURCES,
    PUBLICATION_LOCK_ID,
)
from app.commune.indicator_manifest import ManifestRefused
from app.commune.indicator_policy import RuntimeIndicatorPolicy
from app.commune.indicator_reader import read_active_preproduction_build
from app.commune.indicator_schemas import IndicateurAbsent
from app.commune.tendance_prix import build_tendance_prix
from app.config import settings

# A hosted commune the qualified indicator build does not contain: an explicit
# absence of its indicators, never an outage (four states, T-04.1-29).
OUTSIDE_QUALIFIED_BUILD = IndicateurAbsent(
    etat="not_qualified", motif="commune_hors_build_qualifie"
)
# Rollout gate closed (``commune_indicators_enabled``): the dated blocks are
# not published, an explicit absence of both, never a bare null (four
# states, todo 2026-11-01). Distinct from indicator_policy_unqualified, a block
# of an open gate: the interface reads this one as « no block », the flat
# columns stay the served values.
INDICATORS_NOT_PUBLISHED = IndicateurAbsent(
    etat="not_qualified", motif="indicateurs_communaux_non_publies"
)


def _hosted_edges(sources: Collection[str]) -> list[tuple[str, str]]:
    """Edges of the projection that read a relation of a hosted source."""
    relations = {COMMUNE_SOURCES[s] for s in sources if s in COMMUNE_SOURCES}
    relations |= {
        COMMUNE_INTERMEDIATES[s] for s in sources if s in COMMUNE_INTERMEDIATES
    }
    return [edge for edge in COMMUNE_EDGES if edge[1] in relations]


# The rows the chain rebuild writes into matview_metadata, for every view of
# the chain (the departement view included: it is rebuilt with it).
_CHAIN_METADATA = "v_commune_synthese"


def _readiness_sql(edges: Sequence[tuple[str, str]]) -> str:
    """CTEs ending in ``readiness(ready)``, the availability of a chain view.

    The reader lock, the latest publication of each ``:datasets`` (published,
    relations at their recorded OIDs), the chain's ``matview_metadata`` row,
    and every view edge (the view depends on the current OID of its source).
    Checked in one snapshot: a rename swap may leave metadata marked success
    while views still read retired OIDs. Static identifiers from the canonical
    projection, never request input.
    """
    values = ",".join(f"('{view}', '{source}')" for view, source in edges)
    edges_check = (
        f"""AND NOT EXISTS (
                SELECT 1 FROM (VALUES {values}) AS expected(view_name, source_name)
                WHERE NOT EXISTS (
                    SELECT 1 FROM pg_depend dep
                    JOIN pg_rewrite rw ON rw.oid = dep.objid
                    WHERE dep.classid = 'pg_rewrite'::regclass
                      AND dep.refclassid = 'pg_class'::regclass
                      AND rw.ev_class = to_regclass(expected.view_name)
                      AND dep.refobjid = to_regclass(expected.source_name)
                )
            )"""
        if values
        else ""
    )
    return f"""
        WITH reader_lock AS MATERIALIZED (
            SELECT pg_try_advisory_xact_lock_shared(:publication_lock) AS acquired
        ), latest_publications AS (
            SELECT DISTINCT ON (dataset_name) * FROM etl_publications
            WHERE dataset_name = ANY(:datasets)
            ORDER BY dataset_name, id DESC
        ), readiness AS (
            SELECT (SELECT acquired FROM reader_lock) AND NOT EXISTS (
                SELECT 1 FROM latest_publications p
                WHERE p.status <> 'published' OR EXISTS (
                    SELECT 1 FROM jsonb_each_text(p.relation_oids) AS rel(name, oid)
                    WHERE to_regclass(rel.name)::oid IS DISTINCT FROM rel.oid::oid
                )
            ) AND NOT EXISTS (
                SELECT 1 FROM matview_metadata
                WHERE matview_name = '{_CHAIN_METADATA}'
                  AND last_refresh_status IN ('failed', 'pending')
            ) {edges_check} AS ready
        )"""


async def _read_behind_barrier(
    session: AsyncSession,
    *,
    edges: Sequence[tuple[str, str]],
    select: str,
    params: dict,
    datasets: list[str],
    unavailable: str,
) -> list[RowMapping]:
    """Rows of *select* (reading ``readiness r``), or 503 when not ready.

    *select* keeps one sentinel row even for an unknown code: stale is not
    absent. A retired source DROP CASCADE can remove the view before readiness
    executes: missing relations and a cancelled/timed-out statement are
    transient availability failures; unrelated SQL defects keep their normal
    error reporting.
    """
    await acquire_publication_read_lock(session)
    sql = text(_readiness_sql(edges) + select)
    try:
        result = await session.execute(
            sql,
            {**params, "datasets": datasets, "publication_lock": PUBLICATION_LOCK_ID},
        )
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) not in {"42P01", "57014"}:
            raise
        raise HTTPException(status_code=503, detail=unavailable) from None
    rows = list(result.mappings())
    if not rows or not rows[0]["ready"]:
        raise HTTPException(status_code=503, detail=unavailable)
    return rows


async def commune_synthese_par_codes(
    session: AsyncSession,
    codes: list[str],
    sources: Collection[str],
) -> list[dict]:
    """Fetch commune profiles from the pre-computed matview.

    The matview contains one row per commune (~35K rows) with data from
    demographie, revenus, zonage_abc, vacance, fiscalite, and loyers.

    *sources* are the hosted sources of the synthesis: only their latest
    publication and their view edges gate the read. A source the instance
    does not serve cannot make the synthesis unavailable (its columns are
    masked by ``apply_commune_coverage``); a hosted one still does. Every
    caller projects the rows through ``apply_commune_coverage``. An empty
    *sources* would disable the barrier: a programming error (IN-05).
    """
    if not sources:
        raise ValueError("commune_synthese_par_codes needs the hosted sources")
    rows = await _read_behind_barrier(
        session,
        edges=_hosted_edges(sources),
        select="""
        SELECT r.ready, CASE WHEN r.ready THEN to_jsonb(v) END AS document
        FROM readiness r
        LEFT JOIN v_commune_synthese v
            ON r.ready AND v.code_insee = ANY(:codes)
    """,
        params={"codes": codes},
        datasets=[s for s in COMMUNE_SOURCES if s in sources],
        unavailable="Synthèse communale temporairement indisponible.",
    )
    documents = [row["document"] for row in rows if row["document"] is not None]
    if not settings.commune_indicators_enabled:
        for document in documents:
            document.update(
                indicateurs_dvf=INDICATORS_NOT_PUBLISHED.model_dump(),
                indicateurs_dpe=INDICATORS_NOT_PUBLISHED.model_dump(),
            )
        return documents
    try:
        policy = RuntimeIndicatorPolicy.from_json(settings.commune_indicator_policy)
        indicators = await read_active_preproduction_build(
            session,
            policy=policy,
            environment=settings.app_environment,
            codes=[document["code_insee"] for document in documents],
        )
    except (ManifestRefused, DBAPIError):
        raise HTTPException(
            status_code=503,
            detail="Indicateurs communaux temporairement indisponibles.",
        ) from None
    for document in documents:
        if document["code_insee"] in indicators.hors_build:
            document.update(
                indicateurs_dvf=OUTSIDE_QUALIFIED_BUILD.model_dump(),
                indicateurs_dpe=OUTSIDE_QUALIFIED_BUILD.model_dump(),
            )
            continue
        # A commune of the build without its blocks stays an outage.
        blocks = indicators.blocks.get(document["code_insee"])
        if blocks is None:
            raise HTTPException(
                status_code=503,
                detail="Indicateurs communaux temporairement indisponibles.",
            )
        document.update(
            indicateurs_dvf=blocks["indicateurs_dvf"],
            indicateurs_dpe=blocks["indicateurs_dpe"],
        )
    return documents


async def synthese_autre_maille(
    session: AsyncSession,
    codes: list[str],
    sources: Collection[str],
) -> list[dict]:
    """Flat rows of the other mesh of Paris, Lyon, Marseille (R-09), behind the barrier.

    *codes* come from ``codes_autre_maille``: the commune of an arrondissement,
    or the arrondissements of a commune. Only their flat columns are read, to
    inherit or to point to a value (``app.commune.autre_maille``): no indicator
    block, which these rows never serve. Same barrier and same 503 as
    ``commune_synthese_par_codes``, read in the same transaction.
    """
    if not sources:
        raise ValueError("synthese_autre_maille needs the hosted sources")
    rows = await _read_behind_barrier(
        session,
        edges=_hosted_edges(sources),
        select="""
        SELECT r.ready, CASE WHEN r.ready THEN to_jsonb(v) END AS document
        FROM readiness r
        LEFT JOIN v_commune_synthese v
            ON r.ready AND v.code_insee = ANY(:codes)
    """,
        params={"codes": codes},
        datasets=[s for s in COMMUNE_SOURCES if s in sources],
        unavailable="Synthèse communale temporairement indisponible.",
    )
    return [row["document"] for row in rows if row["document"] is not None]


async def departement_synthese(
    session: AsyncSession, code: str, sources: Collection[str]
) -> dict | None:
    """The row of ``v_departement_synthese`` for *code*, behind the barrier.

    Plan 04.7-10, decision 1: the same readiness as the commune synthesis
    (the view is rebuilt with its chain), with the view's own edges to its
    hosted *sources* among dvf, dpe and demographie. None: the departement
    has no retained sale, document or census row. A 503 when not ready.
    """
    hosted = [s for s in DEPARTEMENT_VIEW_SOURCES if s in sources]
    if not hosted:
        raise ValueError("departement_synthese needs the hosted sources")
    relations = {DEPARTEMENT_VIEW_SOURCES[s] for s in hosted}
    rows = await _read_behind_barrier(
        session,
        edges=[edge for edge in DEPARTEMENT_EDGES if edge[1] in relations],
        select="""
        SELECT r.ready, CASE WHEN r.ready THEN to_jsonb(v) END AS document
        FROM readiness r
        LEFT JOIN v_departement_synthese v
            ON r.ready AND v.code_departement = :code
    """,
        params={"code": code},
        datasets=hosted,
        unavailable="Synthèse départementale temporairement indisponible.",
    )
    return rows[0]["document"]


# The yearly series is not readable (view missing, bound to a retired table,
# being rebuilt, or empty): an explicit absence of the block, never an outage
# of the page and never a bare null for a hosted commune.
TENDANCE_NOT_PUBLISHED = IndicateurAbsent(
    etat="not_qualified", motif="tendance_prix_non_publiee"
)


async def tendances_prix_par_codes(
    session: AsyncSession, codes: list[str], sources: Collection[str]
) -> dict[str, dict]:
    """Price trend block of each of *codes*, or its declared absence.

    Read from ``v_commune_dvf_annuel`` with the readiness of the commune
    synthesis (the view is rebuilt with its chain) and the view's own edge to
    the sales. ``{}`` when dvf is not hosted (nothing is read: the field is
    masked by ``apply_commune_coverage``); otherwise one entry per requested
    code: its block, a code without a row having its true zeros, or
    ``TENDANCE_NOT_PUBLISHED`` when the series is not ready or holds no closed
    year.

    Never a 503: the synthesis has its own barrier, and a trend view that is
    missing, stale or cancelled must not make the page unavailable. The read
    runs in a savepoint, so a failed statement leaves the transaction of the
    page usable; unrelated SQL defects keep their normal error reporting.
    """
    if "dvf" not in sources:
        return {}
    absent = {code: TENDANCE_NOT_PUBLISHED.model_dump() for code in codes}
    try:
        async with session.begin_nested():
            rows = await _read_behind_barrier(
                session,
                edges=ANNUEL_EDGES,
                select="""
        SELECT r.ready,
            CASE WHEN r.ready THEN (
                SELECT jsonb_build_object('debut', min(annee), 'fin', max(annee))
                FROM v_commune_dvf_annuel
            ) END AS fenetre,
            CASE WHEN r.ready THEN (
                SELECT jsonb_agg(to_jsonb(a)) FROM v_commune_dvf_annuel a
                WHERE a.code_insee = ANY(:codes)
            ) END AS lignes
        FROM readiness r
    """,
                params={"codes": codes},
                datasets=["dvf"],
                unavailable="Série annuelle des prix temporairement indisponible.",
            )
    except HTTPException as exc:
        if exc.status_code != 503:
            raise
        return absent
    fenetre = rows[0]["fenetre"] or {}
    if fenetre.get("debut") is None or fenetre.get("fin") is None:
        return absent
    par_code: dict[str, list[dict]] = {code: [] for code in codes}
    for ligne in rows[0]["lignes"] or []:
        par_code[ligne["code_insee"]].append(ligne)
    bornes = (int(fenetre["debut"]), int(fenetre["fin"]))
    return {
        code: build_tendance_prix(bornes, lignes) for code, lignes in par_code.items()
    }


# Window of the commune medians (C29): ``v_commune_dvf``
# (app/commune/matview_ddl.py) keeps the sales from max(date_mutation) minus
# 12 months to max(date_mutation), both included, of ``mutations_grain``. Read
# after the synthesis barrier, which checks that the view depends on the
# current ``mutations_grain``: the same rows, hence the same window.
_FENETRE_MEDIANE_SQL = text("""
    SELECT (max(date_mutation) - INTERVAL '12 months')::date AS debut,
           max(date_mutation) AS fin
    FROM mutations_grain
""")


async def fenetre_mediane_commune(session: AsyncSession) -> str | None:
    """``AAAA-MM-JJ → AAAA-MM-JJ`` the medians are computed on; None if empty."""
    try:
        debut, fin = (await session.execute(_FENETRE_MEDIANE_SQL)).one()
    except DBAPIError as exc:
        # Same transient set as commune_synthese_par_codes: the window is read
        # after the same barrier, so its failures follow the synthesis.
        if getattr(exc.orig, "sqlstate", None) not in {"42P01", "57014"}:
            raise
        raise HTTPException(
            status_code=503,
            detail="Synthèse communale temporairement indisponible.",
        ) from None
    if debut is None or fin is None:
        return None
    return f"{debut.isoformat()} → {fin.isoformat()}"
