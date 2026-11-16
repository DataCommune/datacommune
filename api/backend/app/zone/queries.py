"""Raw SQL queries for zone-based endpoints (v2 frontend).

Covers: commune autocomplete, departement listing,
zone-level DVF/DPE queries with pagination, and zone stats aggregation.
"""

import re
from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.geo import expand_code_insee
from app.common.sql_escape import escape_like
from app.dpe.columns import DPE_SELECT_COLUMNS
from app.dvf.couverture import CLASSES_STATISTIQUES, SEUIL_ND_ZONE
from app.dvf.queries import POSITION_CONNUE_SQL
from app.zone.departements import ImportFacts

# D-03 (phase 04.4): the zone list reads ``mutations_grain`` without alias.
_POSITION_CONNUE_SANS_ALIAS = POSITION_CONNUE_SQL.removeprefix("mg.")

# Deep-OFFSET cap (audit QE-02): Postgres must skip every preceding row on
# OFFSET, so an unbounded page number lets one query burn CPU until the
# statement_timeout while holding a pool connection. 250k rows skipped is
# far beyond any legitimate UI page yet bounds the scan cost.
MAX_OFFSET = 250_000


def dept_from_commune(code_commune: str) -> str:
    """Derive departement code from a commune INSEE code.

    Metropolitan: first 2 chars (e.g. 44109 -> 44, 2A004 -> 2A).
    DOM-TOM: first 3 chars (e.g. 97105 -> 971).
    """
    if code_commune.startswith("97"):
        return code_commune[:3]
    return code_commune[:2]


# ---------------------------------------------------------------------------
# 1. Commune autocomplete (geo_communes + adresses for code_postal)
# ---------------------------------------------------------------------------


_BAN_CODE_POSTAL = """(
                SELECT a.code_postal
                FROM adresses a
                WHERE a.code_commune = gc.code
                  AND a.code_departement = gc.departement_code
                LIMIT 1
            )"""

# Accent-, case-, hyphen- and whitespace-insensitive form of a commune name,
# computed in SQL on both sides (the browser normalizes nothing).
_NORMALIZED_NOM = (
    r"regexp_replace(immutable_unaccent(lower(gc.nom)), '[-\s]+', ' ', 'g')"
)
_NORMALIZED_Q = r"regexp_replace(immutable_unaccent(lower(:q)), '[-\s]+', ' ', 'g')"

POSTAL_CODE_RE = re.compile(r"\d{5}")


def postal_code_departement(code_postal: str) -> str | None:
    """Departement code named by the prefix of a postal code, None if not one.

    The prefix follows the rule of :func:`dept_from_commune` (``97xxx`` -> three
    characters, else two); it names the departement in almost every case. The
    router passes it through ``real_departement``: a prefix naming no
    departement (``20xxx`` Corsica, ``98xxx``) leaves the territory unknown, a
    bounded zero and never a proof of « hors couverture » (02-07 rule). The
    few communes whose postal code carries a neighbouring departement's prefix
    are still found: the ``adresses`` read, bounded to the hosted departements,
    is never skipped because of a prefix.
    """
    code = code_postal.strip()
    if not POSTAL_CODE_RE.fullmatch(code):
        return None
    return dept_from_commune(code)


_LATEST_DEMOGRAPHIE = """
        LEFT JOIN LATERAL (
            SELECT d.population, d.annee
            FROM demographie d
            WHERE gc.departement_code = ANY(CAST(:demographie_departements AS text[]))
              AND d.code_insee = gc.code
              AND d.population IS NOT NULL
            ORDER BY d.annee DESC
            LIMIT 1
        ) dl ON true"""


# The referential of zones. By default the communes alone, as migration 099
# decided (readers that count communes keep counting communes). With
# ``inclure_arrondissements`` (opt-in, plan 04.7-09 D5) the municipal
# arrondissements of ``geo_arrondissements`` are added to the same ``gc`` alias,
# each tied to its commune: the commune stays one row and is never summed with
# its arrondissements. Only the arrondissements of the departements whose BAN
# is served are read (``:arrondissement_departements``): the outlines of a
# departement removed from the scope stay in the table and must not be listed
# (review Codex 04.7-09). Its commune always is. Every fragment is a code
# constant.
_COMMUNES_ONLY = "geo_communes gc"
_COMMUNES_AND_ARRONDISSEMENTS = """(
            SELECT code, nom, departement_code, population,
                   'commune'::text AS type_zone, NULL::text AS commune_parent
            FROM geo_communes
            UNION ALL
            SELECT code, nom, departement_code, population,
                   'arrondissement'::text, commune_code::text
            FROM geo_arrondissements
            WHERE departement_code = ANY(CAST(:arrondissement_departements AS text[]))
        ) gc"""


def _referentiel(inclure_arrondissements: bool) -> tuple[str, str]:
    """``(from_clause, zone_columns)`` of the referential read as ``gc``."""
    if inclure_arrondissements:
        return _COMMUNES_AND_ARRONDISSEMENTS, "gc.type_zone, gc.commune_parent"
    return (
        _COMMUNES_ONLY,
        "'commune'::text AS type_zone, NULL::text AS commune_parent",
    )


def _served_population(
    demographie_departements: Sequence[str],
) -> tuple[str, str, str, str]:
    """Served population of a ``geo_communes gc`` row (H6), as SQL fragments.

    Returns ``(lateral_join, population, annee, source)``: the latest
    ``demographie`` year of the commune when its departement is in
    *demographie_departements*, else the referential value with an unknown
    year (never invented). One rule for the directory, the search and the
    commune page. The caller binds ``:demographie_departements`` when the list
    is not empty; an empty list leaves ``demographie`` out of the statement
    (D-23). Every fragment is a code constant.
    """
    if demographie_departements:
        return (
            _LATEST_DEMOGRAPHIE,
            "COALESCE(dl.population, gc.population)",
            "dl.annee",
            "CASE WHEN dl.population IS NOT NULL THEN 'demographie' "
            "WHEN gc.population IS NOT NULL THEN 'geo_communes' END",
        )
    return (
        "",
        "gc.population",
        "NULL::integer",
        "CASE WHEN gc.population IS NOT NULL THEN 'geo_communes' END",
    )


async def search_communes(
    session: AsyncSession,
    q: str,
    limit: int = 10,
    *,
    departement: str | None = None,
    ban_departements: Sequence[str] = (),
    demographie_departements: Sequence[str] = (),
    inclure_arrondissements: bool = False,
    arrondissement_departements: Sequence[str] = (),
) -> list[dict]:
    """Search communes of the geo_communes referential (35K rows).

    ``inclure_arrondissements`` adds the municipal arrondissements of Paris,
    Lyon and Marseille (``geo_arrondissements``, hosted departements only) to
    the candidates: "Paris" then finds the commune and its arrondissements,
    each code once, in the same order rule. Off, the search is unchanged.

    ``q`` of five digits is a postal code: communes where a BAN address
    carries exactly that code, looked up in *ban_departements* only (no BAN
    hosted: nothing is read, no result). Otherwise ``q`` is a name prefix
    matched on the normalized name (``_NORMALIZED_NOM``), so
    "saint etienne de montluc" finds "Saint-Étienne-de-Montluc".

    Served population (H6): the latest ``demographie`` year of the commune
    when its departement is in *demographie_departements*, else the
    referential value with an unknown year. ``code_postal`` is read in
    ``adresses`` only for communes of *ban_departements*. A departement
    outside those lists never reaches ``demographie`` nor ``adresses``: the
    lateral read is gated on the list (a one-time filter), and an empty list
    leaves the table out of the statement.

    Total, stable order: served population DESC NULLS LAST, normalized
    name, INSEE code.
    """
    params: dict = {"limit": limit}
    where: list[str] = []
    postal_code = q.strip()
    if POSTAL_CODE_RE.fullmatch(postal_code):
        if not ban_departements:
            return []
        where.append(
            "gc.code IN (SELECT a.code_commune FROM adresses a "
            "WHERE a.code_postal = :code_postal "
            "AND a.code_departement = ANY(CAST(:ban_departements AS text[])))"
        )
        params["code_postal"] = postal_code
    else:
        where.append(rf"{_NORMALIZED_NOM} LIKE ({_NORMALIZED_Q} || '%') ESCAPE '\'")
        params["q"] = escape_like(q)
    if departement:
        where.append("gc.departement_code = :departement")
        params["departement"] = departement
    if ban_departements:
        params["ban_departements"] = list(ban_departements)

    if demographie_departements:
        params["demographie_departements"] = list(demographie_departements)
    latest, population, annee, source = _served_population(demographie_departements)
    referentiel, zone_columns = _referentiel(inclure_arrondissements)
    if inclure_arrondissements:
        params["arrondissement_departements"] = list(arrondissement_departements)

    if "code_postal" in params:
        code_postal = "CAST(:code_postal AS text)"
    elif ban_departements:
        code_postal = """CASE
            WHEN m.departement = ANY(CAST(:ban_departements AS text[])) THEN (
                SELECT a.code_postal
                FROM adresses a
                WHERE a.code_commune = m.code_insee
                  AND a.code_departement = m.departement
                LIMIT 1
            ) END"""
    else:
        code_postal = "NULL::text"

    # SAFETY: every fragment interpolated below is a code constant; user values
    # (q, postal code, departement, hosted lists, limit) are bound parameters.
    sql = text(f"""
        SELECT m.code_insee, m.nom, m.departement, m.population,
               m.population_annee, m.population_source,
               m.type_zone, m.commune_parent,
               {code_postal} AS code_postal
        FROM (
            SELECT
                gc.code AS code_insee,
                gc.nom,
                gc.departement_code AS departement,
                {population} AS population,
                {annee} AS population_annee,
                {source} AS population_source,
                {zone_columns},
                {_NORMALIZED_NOM} AS nom_normalise
            FROM {referentiel}{latest}
            WHERE {" AND ".join(where)}
            ORDER BY 4 DESC NULLS LAST, 9, 1
            LIMIT :limit
        ) m
        ORDER BY m.population DESC NULLS LAST, m.nom_normalise, m.code_insee
    """)
    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result]


# ---------------------------------------------------------------------------
# 2. Departements listing (geo_departements, 101 rows)
# ---------------------------------------------------------------------------


async def list_departements(session: AsyncSession) -> list[dict]:
    """List all departements ordered by code.

    Region name is not stored in geo_departements, so it is mapped
    in the router layer via a Python dict.
    """
    sql = text("""
        SELECT code, nom
        FROM geo_departements
        ORDER BY code
    """)
    result = await session.execute(sql)
    return [dict(row._mapping) for row in result]


async def departement_identite(session: AsyncSession, code: str) -> dict | None:
    """Code and name of a departement of the referential; None if unknown."""
    result = await session.execute(
        text("SELECT code, nom FROM geo_departements WHERE code = :code"),
        {"code": code},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def import_facts(
    session: AsyncSession, names: Sequence[str]
) -> dict[str, ImportFacts]:
    """The last import of each dataset of *names* (decision 4).

    ``effective_departments`` as stored (an empty list covers nothing, null
    names no perimeter) with the ``last_updated_at`` of the same row, written
    together by the publication; and the last successful run, its
    ``completed_at`` and the ``departements`` of its ``scope_snapshot``. A
    dataset without metadata row is absent: never imported.
    """
    result = await session.execute(
        text("""
        SELECT
            dm.dataset_name,
            dm.effective_departments,
            dm.last_updated_at,
            ls.completed_at AS last_success_at,
            CASE WHEN jsonb_typeof(ls.scope_snapshot -> 'departements') = 'array'
                THEN ARRAY(
                    SELECT jsonb_array_elements_text(
                        ls.scope_snapshot -> 'departements'
                    )
                )
            END AS scope_departements
        FROM dataset_metadata dm
        LEFT JOIN LATERAL (
            SELECT completed_at, scope_snapshot
            FROM etl_runs
            WHERE dataset_name = dm.dataset_name AND status = 'success'
            ORDER BY completed_at DESC NULLS LAST
            LIMIT 1
        ) ls ON TRUE
        WHERE dm.dataset_name = ANY(:names)
    """),
        {"names": list(names)},
    )
    return {
        row["dataset_name"]: ImportFacts(
            last_success_at=row["last_success_at"],
            effective_departments=(
                None
                if row["effective_departments"] is None
                else tuple(row["effective_departments"])
            ),
            scope_departements=(
                None
                if row["scope_departements"] is None
                else tuple(row["scope_departements"])
            ),
            metadata_updated_at=row["last_updated_at"],
        )
        for row in result.mappings()
    }


async def departement_communes(
    session: AsyncSession, code: str, *, arrondissements: bool
) -> tuple[int, int]:
    """Communes of the departement and, when listed, its arrondissements."""
    result = await session.execute(
        text("""
        SELECT
            (SELECT count(*) FROM geo_communes WHERE departement_code = :code),
            CASE WHEN CAST(:arrondissements AS boolean) THEN (
                SELECT count(*) FROM geo_arrondissements
                WHERE departement_code = :code
            ) ELSE 0 END
    """),
        {"code": code, "arrondissements": arrondissements},
    )
    communes, arrondissements_count = result.one()
    return int(communes), int(arrondissements_count)


async def communes_principales(
    session: AsyncSession,
    code: str,
    *,
    arrondissements: bool,
    demographie_departements: Sequence[str] = (),
    limit: int = 10,
) -> list[dict]:
    """The *limit* most populated zones of a departement, ties by code.

    The communes, or with *arrondissements* the municipal arrondissements
    (Paris, a departement of one commune). Same served population as the
    directory (H6): the latest ``demographie`` year where it is hosted, else
    the referential value with an unknown year.
    """
    latest, population, annee, source = _served_population(demographie_departements)
    if arrondissements:
        referentiel = "geo_arrondissements gc"
        zone = "'arrondissement'::text AS type_zone, gc.commune_code::text"
    else:
        referentiel = "geo_communes gc"
        zone = "'commune'::text AS type_zone, NULL::text"
    params: dict = {"code": code, "limit": limit}
    if demographie_departements:
        params["demographie_departements"] = list(demographie_departements)
    # SAFETY: every interpolated fragment is a code constant; the departement,
    # the hosted list and the limit are bound parameters.
    result = await session.execute(
        text(f"""
        SELECT gc.code AS code_insee, gc.nom,
               {zone} AS commune_parent,
               {population} AS population,
               {annee} AS population_annee,
               {source} AS population_source
        FROM {referentiel}{latest}
        WHERE gc.departement_code = :code
        ORDER BY {population} DESC NULLS LAST, gc.code
        LIMIT :limit
    """),
        params,
    )
    return [dict(row) for row in result.mappings()]


async def departement_fiches(
    session: AsyncSession, code: str, *, arrondissements: bool, limit: int
) -> list[dict]:
    """Code and name of every page of the departement, sorted, at most *limit*."""
    union = (
        """
        UNION ALL
        SELECT code, nom, 'arrondissement'::text FROM geo_arrondissements
        WHERE departement_code = :code"""
        if arrondissements
        else ""
    )
    result = await session.execute(
        text(f"""
        SELECT code AS code_insee, nom, 'commune'::text AS type_zone
        FROM geo_communes WHERE departement_code = :code{union}
        ORDER BY 1
        LIMIT :limit
    """),
        {"code": code, "limit": limit},
    )
    return [dict(row) for row in result.mappings()]


# Two contours closer than this (in degrees, about 100 m) share a border: the
# referential contours are simplified, so neighbours may not touch exactly.
VOISINE_LIMITROPHE_DEGRES = 0.001


async def zone_existe(session: AsyncSession, code: str) -> bool:
    """True when *code* is a commune or an arrondissement of the referential."""
    result = await session.execute(
        text("""
        SELECT EXISTS (SELECT 1 FROM geo_communes WHERE code = :code)
            OR EXISTS (SELECT 1 FROM geo_arrondissements WHERE code = :code)
    """),
        {"code": code},
    )
    return bool(result.scalar())


async def communes_voisines(
    session: AsyncSession, code: str, *, limit: int, candidats: int
) -> list[dict]:
    """The pages nearest to *code*: bordering ones first, the largest first.

    The *candidats* nearest contours are taken by the GiST index (KNN ``<->``
    against a constant, the contour of the page); among them the bordering
    ones come first, by population, then the others by distance. A commune
    lists communes; an arrondissement lists the other arrondissements of its
    commune and the communes around it, never its own commune.
    """
    result = await session.execute(
        text("""
        WITH source AS (
            SELECT geometry, NULL::varchar AS parent
            FROM geo_communes WHERE code = :code
            UNION ALL
            SELECT geometry, commune_code
            FROM geo_arrondissements WHERE code = :code
            LIMIT 1
        ),
        proches AS (
            (SELECT c.code, c.nom, c.departement_code,
                    'commune'::text AS type_zone, c.population,
                    ST_Distance(c.geometry, (SELECT geometry FROM source))
                        AS distance
             FROM geo_communes c
             WHERE c.code <> :code
               AND c.code IS DISTINCT FROM (SELECT parent FROM source)
             ORDER BY c.geometry <-> (SELECT geometry FROM source)
             LIMIT :candidats)
            UNION ALL
            (SELECT a.code, a.nom, a.departement_code,
                    'arrondissement'::text, a.population,
                    ST_Distance(a.geometry, (SELECT geometry FROM source))
             FROM geo_arrondissements a
             WHERE a.code <> :code
               AND a.commune_code = (SELECT parent FROM source)
             ORDER BY a.geometry <-> (SELECT geometry FROM source)
             LIMIT :candidats)
        )
        SELECT code AS code_insee, nom, departement_code, type_zone,
               distance <= :seuil AS limitrophe
        FROM proches
        ORDER BY distance <= :seuil DESC,
                 CASE WHEN distance <= :seuil THEN population END
                     DESC NULLS LAST,
                 distance, code
        LIMIT :limit
    """),
        {
            "code": code,
            "limit": limit,
            "candidats": candidats,
            "seuil": VOISINE_LIMITROPHE_DEGRES,
        },
    )
    return [dict(row) for row in result.mappings()]


# ---------------------------------------------------------------------------
# 2b. Paginated commune list (geo_communes, ~35K rows)
# ---------------------------------------------------------------------------

# Whitelist mapping sort_by -> SQL expression. Never interpolate raw user
# input into ORDER BY. The API layer already constrains sort_by via a Literal;
# this dict is defense in depth for any other caller. ``None`` stands for the
# served population (H6), resolved to the expression of _served_population.
_COMMUNE_SORT_COLUMNS: dict[str, str | None] = {
    "nom": "gc.nom",
    "departement": "gc.departement_code",
    "population": None,
}


def _population_bounds(
    population: str, population_min: int | None, population_max: int | None
) -> tuple[list[str], dict[str, int]]:
    """Inclusive bounds on the served *population* expression: clauses, params.

    A NULL population fails the comparison, so an unknown one is outside any
    bounded range.
    """
    clauses: list[str] = []
    params: dict[str, int] = {}
    if population_min is not None:
        clauses.append(f"{population} >= :population_min")
        params["population_min"] = population_min
    if population_max is not None:
        clauses.append(f"{population} <= :population_max")
        params["population_max"] = population_max
    return clauses, params


async def list_communes(
    session: AsyncSession,
    *,
    q: str | None = None,
    departement: str | None = None,
    code_insee: str | None = None,
    with_code_postal: bool = False,
    sort_by: str = "population",
    order: str = "desc",
    page: int = 1,
    per_page: int = 50,
    demographie_departements: Sequence[str] = (),
    departements: Sequence[str] | None = None,
    population_min: int | None = None,
    population_max: int | None = None,
    inclure_arrondissements: bool = False,
    arrondissement_departements: Sequence[str] = (),
) -> tuple[list[dict], int]:
    """List communes from geo_communes with search, filter, sort, pagination.

    ``inclure_arrondissements`` (default off: rows, total and pagination are
    those of ``geo_communes`` alone) adds the arrondissements of
    ``geo_arrondissements`` to the list, count and every filter alike; each row
    says ``type_zone`` and ``commune_parent``. Amends the decision of migration
    099 (the referential lists communes only) by opt-in, plan 04.7-09 D5.

    Returns (rows, total_count). Search `q` is an accent-insensitive substring
    match on the name (unaccent). Sorting is restricted to a hardcoded column
    whitelist so no user input reaches the ORDER BY clause.

    Served population (H6, same rule as :func:`search_communes` and the
    commune page): the latest ``demographie`` year when the commune's
    departement is in *demographie_departements*, with ``population_annee``
    and ``population_source``; otherwise the referential value, year null.
    ``sort_by="population"`` orders on the served value. ``demographie`` is
    never read for another departement, nor at all with an empty list (D-23).

    ``departements`` restricts the referential to those departements (the
    directory's served ones, 04.6-02); an empty list matches nothing.
    ``population_min`` / ``population_max`` are inclusive bounds on the
    served population: an unknown population is outside any bounded range.
    Every filter applies to the total and to the page alike; the count reads
    ``demographie`` only when a population bound needs the served value.
    A page past the total is an empty page (its total is still served).

    ``code_insee`` restricts the referential to that code (H2).
    ``with_code_postal`` (only with ``code_insee``, BAN hosted for its
    departement) adds the BAN postal code; otherwise ``adresses`` is not read.
    """
    if with_code_postal and code_insee is None:
        raise ValueError("code_postal is served for a single code_insee only")
    if sort_by not in _COMMUNE_SORT_COLUMNS:
        raise ValueError(f"Invalid sort_by: {sort_by!r}")
    latest, population, annee, source = _served_population(demographie_departements)
    referentiel, zone_columns = _referentiel(inclure_arrondissements)
    sort_column = _COMMUNE_SORT_COLUMNS[sort_by] or population
    order_sql = "DESC" if order.lower() == "desc" else "ASC"

    where_clauses: list[str] = []
    params: dict = {}
    if inclure_arrondissements:
        params["arrondissement_departements"] = list(arrondissement_departements)

    if departement:
        where_clauses.append("gc.departement_code = :departement")
        params["departement"] = departement
    if departements is not None:
        where_clauses.append("gc.departement_code = ANY(CAST(:departements AS text[]))")
        params["departements"] = list(departements)
    bounds, bound_params = _population_bounds(
        population, population_min, population_max
    )
    where_clauses.extend(bounds)
    params.update(bound_params)
    if code_insee is not None:
        where_clauses.append("gc.code = :code_insee")
        params["code_insee"] = code_insee
    if q:
        where_clauses.append(
            r"regexp_replace(immutable_unaccent(lower(gc.nom)), '[-\s]+', ' ', 'g') "
            r"LIKE ('%' || regexp_replace("
            r"immutable_unaccent(lower(:q)), '[-\s]+', ' ', 'g') || '%') ESCAPE '\'"
        )
        params["q"] = escape_like(q)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    if demographie_departements:
        params["demographie_departements"] = list(demographie_departements)

    # The served population lives in the lateral join: a bounded range counts
    # on it, otherwise the count stays on geo_communes alone.
    count_from = f"{referentiel}{latest}" if bounds else referentiel
    count_sql = text(f"SELECT COUNT(*) FROM {count_from} {where_sql}")
    count_result = await session.execute(count_sql, params)
    total_count = count_result.scalar() or 0

    offset = min((page - 1) * per_page, MAX_OFFSET)
    params["limit"] = per_page
    params["offset"] = offset

    code_postal = (
        f",\n            {_BAN_CODE_POSTAL} AS code_postal" if with_code_postal else ""
    )

    # SAFETY: sort_column comes from the hardcoded whitelist above or is the
    # served-population expression of _served_population, order_sql is a
    # literal ASC/DESC, and where_sql and the H6 fragments are code constants.
    # Every user value and the hosted departement list are :param bindings.
    data_sql = text(f"""
        SELECT
            gc.code AS code_insee,
            gc.nom,
            gc.departement_code AS departement,
            {population} AS population,
            {annee} AS population_annee,
            {source} AS population_source,
            {zone_columns}{code_postal}
        FROM {referentiel}{latest}
        {where_sql}
        ORDER BY {sort_column} {order_sql} NULLS LAST, gc.code ASC
        LIMIT :limit OFFSET :offset
    """)
    result = await session.execute(data_sql, params)
    rows = [dict(row._mapping) for row in result]
    return rows, total_count


# ---------------------------------------------------------------------------
# 3. Zone resolve — paginated DVF/DPE by commune or departement
# ---------------------------------------------------------------------------

# Public `type_local` filter -> `classe_mutation` at mutation grain (D-25).
# `mutations_grain` has no per-line `type_local` text column (D-01: it is a
# closed classification of the WHOLE mutation, not a per-local label) -- an
# unrecognised value must return nothing rather than silently ignoring the
# filter and returning everything (fail-closed, mirrors app.dvf.queries 57-10).
_TYPE_LOCAL_A_CLASSE: dict[str, str] = {
    "Maison": "residentiel_maison",
    "Appartement": "residentiel_appartement",
}


# Paris, Lyon and Marseille: DVF and DPE are coded at the arrondissement, so a
# commune zone is read on the commune code and all its arrondissements (plan
# 04.7-09, D4). ``mutations_grain`` has one row per mutation and a DPE document
# one ``code_insee``: each sale and each document is read once, whatever the
# number of arrondissements it touches. Any other code is its own single code.
def _commune_codes(code: str) -> list[str]:
    return expand_code_insee(code)


# Statistics keep the sales of one commune only (``nb_communes = 1``). On a PLM
# commune a sale on several arrondissements is still one commune's sale: it
# stays in when every line of the mutation is coded in the commune (the
# arrondissements or the commune itself), the same rule as ``v_commune_dvf``
# (migration 111). ``:codes`` is the set of the commune being read.
_SAME_COMMUNE_STATS = """(
              nb_communes = 1
              OR (
                  nb_communes > 1
                  AND NOT EXISTS (
                      SELECT 1 FROM mutations m
                      WHERE m.code_departement = mutations_grain.code_departement
                        AND m.id_mutation = mutations_grain.id_mutation
                        AND m.code_commune IS NOT NULL
                        AND m.code_commune <> ALL(CAST(:codes AS text[]))
                  )
              )
          )"""


async def zone_resolve_dvf(
    session: AsyncSession,
    *,
    zone_type: str,
    code: str,
    page: int = 1,
    per_page: int = 25,
    surface_min: float | None = None,
    surface_max: float | None = None,
    prix_min: float | None = None,
    prix_max: float | None = None,
    type_local: str | None = None,
    date_min: str | None = None,
    date_max: str | None = None,
) -> tuple[list[dict], int]:
    """Query DVF mutations by commune or departement with filters (D-25).

    Returns (rows, total_count). Reads `mutations_grain` (one row per
    mutation, already qualified by the ETL, D-01) instead of the raw
    per-line `mutations` table -- `total_count` now counts MUTATIONS, not
    lines: an 8-lot ensemble sale used to inflate this count by 8, it counts
    as 1 now (semantic change of an existing response field, documented per
    plan discipline).

    `surface_reelle_bati`, `nombre_pieces_principales` and `surface_terrain`
    keep their field NAMES for contract stability but now read the
    mutation-level SUM columns (`surface_bati_totale`,
    `nombre_pieces_totales`, `surface_terrain_totale`) -- same "field kept,
    semantics corrected" discipline applied to `/v1/dvf` (D-15). `type_local`
    is reconstructed only for a mono-local mutation (`NULL` otherwise -- it
    would be dishonest to pick one local's type out of a mixte/ensemble
    mutation). `id_parcelle` is dropped: a mutation can span more than one
    parcelle (`mutation_parcelles`, D-09) so there is no single honest value
    to put there at this grain; `nb_parcelles` is exposed instead so a
    consumer can see when a mutation is multi-parcelle.

    Uses idx_mutations_grain_code_commune or partition pruning on
    code_departement for efficient access.
    """
    if zone_type == "commune":
        where_zone = (
            "code_commune = ANY(CAST(:codes AS text[])) AND code_departement = :dept"
        )
        params: dict = {"codes": _commune_codes(code), "dept": dept_from_commune(code)}
    else:
        where_zone = "code_departement = :code"
        params = {"code": code}

    # A mutation of unknown position is a statistic only (D-03): neither a
    # row nor counted in the total of this list (``zone_stats_dvf`` counts it).
    where_clauses = [where_zone, _POSITION_CONNUE_SANS_ALIAS]

    if surface_min is not None:
        where_clauses.append("surface_bati_totale >= :surface_min")
        params["surface_min"] = surface_min
    if surface_max is not None:
        where_clauses.append("surface_bati_totale <= :surface_max")
        params["surface_max"] = surface_max
    if prix_min is not None:
        where_clauses.append("valeur_fonciere >= :prix_min")
        params["prix_min"] = prix_min
    if prix_max is not None:
        where_clauses.append("valeur_fonciere <= :prix_max")
        params["prix_max"] = prix_max
    if type_local is not None:
        classe_filtre = _TYPE_LOCAL_A_CLASSE.get(type_local)
        if classe_filtre is None:
            # Unrecognised type_local: fail closed, never fall through to
            # "no filter applied" (which would return every class).
            where_clauses.append("1 = 0")
        else:
            where_clauses.append("classe_mutation = :classe_mutation_filtre")
            params["classe_mutation_filtre"] = classe_filtre
    if date_min is not None:
        where_clauses.append("date_mutation >= :date_min")
        params["date_min"] = date_min
    if date_max is not None:
        where_clauses.append("date_mutation <= :date_max")
        params["date_max"] = date_max

    # SAFETY: where_sql is built from hardcoded clause templates above,
    # never from user input. All user values go through :param bindings.
    where_sql = " AND ".join(where_clauses)
    offset = min((page - 1) * per_page, MAX_OFFSET)
    params["limit"] = per_page
    params["offset"] = offset
    # Bound array param for the prix_m2/prix_m2_raison CASE below -- never a
    # literal IN (...) list (same discipline as :classe_mutation_filtre).
    params["classes_statistiques"] = list(CLASSES_STATISTIQUES)

    count_sql = text(f"SELECT COUNT(*) FROM mutations_grain WHERE {where_sql}")
    count_result = await session.execute(count_sql, params)
    total_count = count_result.scalar() or 0

    # prix_m2/prix_m2_raison are NOT stored on mutations_grain (D-15: derived
    # at query time by the plans reading this table, never re-ingested).
    # This CASE mirrors app.etl_dvf.qualification._prix_m2's decision tree
    # for the sole purpose of "is this mono-local residential mutation a real
    # or VEFA sale with a positive price/surface" -- it reads the ALREADY
    # qualified classe_mutation/est_vente/hors_stats_vefa columns, it never
    # re-classifies a mutation (D-05 remains the only classification
    # authority: `classe_mutation` itself is never recomputed here).
    data_sql = text(f"""
        SELECT id_mutation, date_mutation, nature_mutation,
               valeur_fonciere,
               CASE
                   WHEN mono_local AND nb_locaux_maison = 1 THEN 'Maison'
                   WHEN mono_local AND nb_locaux_appartement = 1 THEN 'Appartement'
                   ELSE NULL
               END AS type_local,
               surface_bati_totale AS surface_reelle_bati,
               nombre_pieces_totales AS nombre_pieces_principales,
               surface_terrain_totale AS surface_terrain,
               adresse_numero, adresse_nom_voie, code_postal,
               nom_commune, code_commune, nb_parcelles,
               ST_X(position) AS longitude,
               ST_Y(position) AS latitude,
               classe_mutation,
               mono_local AS mutation_mono_local,
               hors_stats_vefa,
               surface_bati_totale,
               nb_locaux_maison + nb_locaux_appartement AS nb_locaux_residentiel,
               nb_locaux_commercial,
               CASE
                   WHEN classe_mutation = ANY(:classes_statistiques)
                        AND (est_vente OR hors_stats_vefa)
                        AND surface_bati_totale > 0
                        AND valeur_fonciere > 0
                   THEN
                       ROUND((valeur_fonciere / surface_bati_totale)::numeric, 2)::float
                   ELSE NULL
               END AS prix_m2,
               CASE
                   WHEN classe_mutation = 'mixte' THEN 'vente_mixte'
                   WHEN classe_mutation = 'commerciale' THEN 'mutation_commerciale'
                   WHEN classe_mutation = 'terrain_seul' THEN 'terrain_seul'
                   WHEN NOT (classe_mutation = ANY(:classes_statistiques))
                       THEN 'mutation_multi_locaux'
                   WHEN NOT (est_vente OR hors_stats_vefa) THEN 'nature_hors_vente'
                   WHEN surface_bati_totale IS NULL OR surface_bati_totale <= 0
                       OR valeur_fonciere IS NULL OR valeur_fonciere <= 0
                       THEN 'surface_absente'
                   ELSE NULL
               END AS prix_m2_raison
        FROM mutations_grain
        WHERE {where_sql}
        ORDER BY date_mutation DESC
        LIMIT :limit OFFSET :offset
    """)
    result = await session.execute(data_sql, params)
    rows = [dict(row._mapping) for row in result]

    return rows, total_count


async def zone_resolve_dpe(
    session: AsyncSession,
    *,
    zone_type: str,
    code: str,
    page: int = 1,
    per_page: int = 25,
    dpe: str | None = None,
    date_min: str | None = None,
    date_max: str | None = None,
) -> tuple[list[dict], int]:
    """Query DPE diagnostics by commune or departement with filters.

    Returns (rows, total_count). Uses idx_dpe_code_insee for commune
    lookups, code_departement for departement-level queries.
    """
    if zone_type == "commune":
        where_zone = "code_insee = ANY(CAST(:codes AS text[]))"
        params: dict = {"codes": _commune_codes(code)}
    else:
        where_zone = "code_departement = :code"
        params = {"code": code}

    where_clauses = [where_zone]

    if dpe is not None:
        where_clauses.append("etiquette_dpe = :dpe")
        params["dpe"] = dpe.upper()
    if date_min is not None:
        where_clauses.append("date_etablissement_dpe >= :date_min")
        params["date_min"] = date_min
    if date_max is not None:
        where_clauses.append("date_etablissement_dpe <= :date_max")
        params["date_max"] = date_max

    # SAFETY: where_sql is built from hardcoded clause templates above,
    # never from user input. All user values go through :param bindings.
    where_sql = " AND ".join(where_clauses)
    offset = min((page - 1) * per_page, MAX_OFFSET)
    params["limit"] = per_page
    params["offset"] = offset

    count_sql = text(f"SELECT COUNT(*) FROM diagnostics_dpe WHERE {where_sql}")
    count_result = await session.execute(count_sql, params)
    total_count = count_result.scalar() or 0

    select_cols = ", ".join(DPE_SELECT_COLUMNS)

    # Scalar columns come from the shared DPE_SELECT_COLUMNS; longitude/latitude
    # are the stored columns here (the point path derives them from geometry).
    data_sql = text(f"""
        SELECT {select_cols},
               longitude, latitude
        FROM diagnostics_dpe
        WHERE {where_sql}
        ORDER BY date_etablissement_dpe DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """)
    result = await session.execute(data_sql, params)
    rows = [dict(row._mapping) for row in result]

    return rows, total_count


# ---------------------------------------------------------------------------
# 4. Zone stats — aggregated indicators
# ---------------------------------------------------------------------------


async def zone_stats_dvf(
    session: AsyncSession,
    zone_type: str,
    code: str,
) -> dict:
    """Aggregate DVF stats (median price/m2, transaction count, avg surface).

    Reads `mutations_grain` (D-25, one row per mutation): `nb_transactions`
    now counts MUTATIONS, not lines -- an 8-lot ensemble sale used to inflate
    it by 8 (RESEARCH Pitfall 1), it counts as 1 now, correct by
    construction, no `COUNT(DISTINCT id_mutation)` workaround needed. The
    statistical population is restricted to mono-local residential sales
    (`classe_mutation` in `CLASSES_STATISTIQUES`, `est_vente = true`,
    `hors_stats_vefa = false`) and to mutations attached to exactly one
    commune (`nb_communes = 1`). Multi-commune mutations remain available in
    `zone_resolve_dvf` as history, but assigning their full price to every
    commune would duplicate them in local statistics. The population is
    filtered AFTER qualification, never on a partial view (D-14 pattern).
    A sale at 0 euro is no price (A5a-01: `valeur_fonciere > 0`, as every
    other DVF projection); a sale of unknown position counts (D-03, phase
    04.4: a statistic reads no position).
    `prix_median_m2` and the maison/appartement
    breakdown (`*_maison`/`*_appartement`) use PERCENTILE_CONT and are masked
    to NULL (`n.d.`) when the underlying effectif is below `SEUIL_ND_ZONE`
    (C-02, `app.dvf.couverture`) -- a median at 1 or 2 points would reveal an
    individual sale's price. `nb_transactions*` counters are NEVER masked
    (an effectif being small is not sensitive, an individual price is).
    """
    if zone_type == "commune":
        where = (
            "code_commune = ANY(CAST(:codes AS text[])) AND code_departement = :dept"
        )
        codes = _commune_codes(code)
        params: dict = {"codes": codes, "dept": dept_from_commune(code)}
        one_commune = _SAME_COMMUNE_STATS if len(codes) > 1 else "nb_communes = 1"
    else:
        where = "code_departement = :code"
        params = {"code": code}
        one_commune = "nb_communes = 1"

    params["classes_statistiques"] = list(CLASSES_STATISTIQUES)
    params["seuil"] = SEUIL_ND_ZONE

    sql = text(f"""
        SELECT
            CASE WHEN COUNT(*) >= :seuil THEN
                ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (
                    ORDER BY valeur_fonciere / surface_bati_totale
                ))::numeric, 2)::float
            ELSE NULL END AS prix_median_m2,
            COUNT(*) AS nb_transactions,
            AVG(surface_bati_totale) AS surface_moyenne,
            CASE WHEN COUNT(*) FILTER (
                WHERE classe_mutation = 'residentiel_maison'
            ) >= :seuil THEN
                ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (
                    ORDER BY valeur_fonciere / surface_bati_totale
                ) FILTER (
                    WHERE classe_mutation = 'residentiel_maison'
                ))::numeric, 2)::float
            ELSE NULL END AS prix_median_m2_maison,
            COUNT(*) FILTER (
                WHERE classe_mutation = 'residentiel_maison'
            ) AS nb_transactions_maison,
            CASE WHEN COUNT(*) FILTER (
                WHERE classe_mutation = 'residentiel_appartement'
            ) >= :seuil THEN
                ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (
                    ORDER BY valeur_fonciere / surface_bati_totale
                ) FILTER (
                    WHERE classe_mutation = 'residentiel_appartement'
                ))::numeric, 2)::float
            ELSE NULL END AS prix_median_m2_appartement,
            COUNT(*) FILTER (
                WHERE classe_mutation = 'residentiel_appartement'
            ) AS nb_transactions_appartement
        FROM mutations_grain
        WHERE {where}
          AND classe_mutation = ANY(:classes_statistiques)
          AND est_vente = true
          AND hors_stats_vefa = false
          AND {one_commune}
          AND valeur_fonciere > 0
          AND surface_bati_totale > 0
    """)
    result = await session.execute(sql, params)
    row = result.mappings().first()
    if row is None:
        return {}
    return dict(row)


async def zone_stats_dpe(
    session: AsyncSession,
    zone_type: str,
    code: str,
) -> dict[str, int]:
    """Aggregate DPE distribution by etiquette (A-G).

    Returns {A: count, B: count, ...} dict.
    """
    if zone_type == "commune":
        where = "code_insee = ANY(CAST(:codes AS text[]))"
        params: dict = {"codes": _commune_codes(code)}
    else:
        where = "code_departement = :code"
        params = {"code": code}

    sql = text(f"""
        SELECT etiquette_dpe, COUNT(*) AS cnt
        FROM diagnostics_dpe
        WHERE {where}
          AND etiquette_dpe IS NOT NULL
        GROUP BY etiquette_dpe
        ORDER BY etiquette_dpe
    """)
    result = await session.execute(sql, params)
    return {row.etiquette_dpe: row.cnt for row in result}
