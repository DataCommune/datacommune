"""Raw SQL queries for DPE energy diagnostic lookups.

Two families of access live here, and they must never be confused:

- :func:`dpe_par_point` -- PROXIMITY search (D-16). PostGIS ST_DWithin plus KNN
  (<-> operator) for distance-ordered results. It answers "which diagnostics are
  around this point", which is a legitimate public capability and NEVER a proof
  that one of them describes the queried property.
- :func:`dpe_par_identifiant_ban` / :func:`dpe_par_identifiants_ban` -- IDENTITY
  fetches (Phase 51, D-01/D-02). A bound equality on the ADEME interoperability
  key ``diagnostics_dpe.identifiant_ban``: no geometry, no distance, no text
  matching. Both are bounded and read one row BEYOND the public limit so the
  caller can tell "exactly N rows" from "at least N rows" -- declaring a
  diagnostic unique out of a silently truncated result is the Pitfall 4 defect.

Neither identity fetch decides anything: they select rows and report truncation.
The whole business decision lives in the pure module ``app.dpe.attribution``
(D-10 doctrine: SQL selects, Python decides).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dpe.columns import DPE_SELECT_COLUMNS
from app.etl_dpe.adresse_brute import MARQUEUR_CALCUL

# Columns the attribution policy needs. Superset of the public projection: the
# join key, the replacement edge and the raw address complement are read HERE
# because the policy needs them to decide, and dropped by the Pydantic
# allowlists of ``app.dpe.schemas`` before anything is published (T-51-02).
# Deliberately NOT reusing DPE_SELECT_COLUMNS: that constant is the contract of
# the two proximity/zone paths (locked by tests/test_dpe_columns_shared.py) and
# carries none of the Phase 48 logement columns this path depends on.
_ATTRIBUTION_COLS: tuple[str, ...] = (
    "numero_dpe",
    "numero_dpe_remplace",
    "identifiant_ban",
    "date_etablissement_dpe",
    "date_fin_validite_dpe",
    "type_batiment",
    "surface_habitable",
    "etiquette_dpe",
    "etiquette_ges",
    "annee_construction",
    "periode_construction",
    "type_installation_chauffage",
    "type_energie_principale_chauffage",
    "conso_5_usages_ep",
    "emission_ges_5_usages",
    "cout_total_5_usages",
    "qualite_isolation_enveloppe",
    "numero_etage_appartement",
    "complement_adresse_logement",
)

_ATTRIBUTION_SELECT_COLS = ",\n               ".join(
    f"d.{col}" for col in _ATTRIBUTION_COLS
)

_INVENTORY_COLUMNS: tuple[str, ...] = (
    "id",
    "numero_dpe",
    "numero_dpe_remplace",
    "type_batiment",
    "surface_habitable",
    "etiquette_dpe",
    "etiquette_ges",
    "date_etablissement_dpe",
    "date_fin_validite_dpe",
    "annee_construction",
    "periode_construction",
    "type_installation_chauffage",
    "type_energie_principale_chauffage",
    "qualite_isolation_enveloppe",
    "conso_5_usages_ep",
    "emission_ges_5_usages",
    "cout_total_5_usages",
    "regime_dpe",
    "source_observations",
    "source_state",
    "source_snapshot_at",
    "is_tombstone",
    "lifecycle_facts",
    "revision_evidence",
)
_INVENTORY_SELECT_COLUMNS = ",\n               ".join(
    f"d.{column}" for column in _INVENTORY_COLUMNS
)


@dataclass(frozen=True, slots=True)
class DpeRelationGenerations:
    """OID identities read while both source parents are locked."""

    addresses: int
    diagnostics: int


@dataclass(frozen=True, slots=True)
class DpeInventoryTarget:
    """Private address target used only inside the inventory transaction."""

    address_id: int
    department: str
    ban_id: str


@dataclass(frozen=True, slots=True)
class DpeInventoryRecord:
    """Private database row; technical identifiers never cross the API model."""

    document_id: int
    dpe_number: str
    replaced_dpe_number: str | None
    type_batiment: str | None
    surface_habitable: Decimal | float | None
    etiquette_dpe: str | None
    etiquette_ges: str | None
    date_etablissement_dpe: date | None
    date_fin_validite_dpe: date | None
    annee_construction: int | None
    periode_construction: str | None
    type_installation_chauffage: str | None
    type_energie_principale_chauffage: str | None
    qualite_isolation_enveloppe: str | None
    conso_5_usages_ep: Decimal | float | None
    emission_ges_5_usages: Decimal | float | None
    cout_total_5_usages: Decimal | float | None
    regime_dpe: str | None = None
    source_observations: list[dict] = field(default_factory=list)
    source_state: str = "unknown"
    source_snapshot_at: datetime | None = None
    is_tombstone: bool = False
    lifecycle_facts: dict = field(
        default_factory=lambda: {
            "replacement_state": "not_checked",
            "graph_issues": [],
            "date_issues": [],
        }
    )
    revision_evidence: dict = field(
        default_factory=lambda: {
            "status": "unverified",
            "kind": None,
            "revision_date": None,
            "verified_at": None,
            "evidence_sha256": None,
            "official_source": None,
        }
    )


@dataclass(frozen=True, slots=True)
class DpeInventoryAggregate:
    """Complete inventory counts, independent from the requested page."""

    total: int
    apartment_document_count: int
    distribution: dict[str, int]


def _dept_depuis_cle(cle: str) -> str:
    """Partition key read from a BAN interoperability key (Pattern B).

    A key is ``INSEE_FANTOIR_NUMERO``, so its leading INSEE code carries the
    department (3 chars overseas). Same derivation as
    ``app.logements.queries``; kept local so this module owes nothing to the
    logements module.
    """
    return cle[:3] if cle.startswith("97") else cle[:2]


def _borne(rows: list[dict], limit: int) -> tuple[list[dict], bool]:
    """Cut the sentinel row off and report whether it was there (Pitfall 4).

    The caller asked for *limit* rows and the query fetched ``limit + 1``. A
    present sentinel means the address carries MORE diagnostics than were read,
    which forbids any claim of uniqueness downstream.
    """
    return rows[:limit], len(rows) > limit


def _record_from_mapping(row: dict) -> DpeInventoryRecord:
    """Build the private record from an explicit SQL projection."""
    return DpeInventoryRecord(
        document_id=row["id"],
        dpe_number=row["numero_dpe"],
        replaced_dpe_number=row["numero_dpe_remplace"],
        type_batiment=row["type_batiment"],
        surface_habitable=row["surface_habitable"],
        etiquette_dpe=row["etiquette_dpe"],
        etiquette_ges=row["etiquette_ges"],
        date_etablissement_dpe=row["date_etablissement_dpe"],
        date_fin_validite_dpe=row["date_fin_validite_dpe"],
        annee_construction=row["annee_construction"],
        periode_construction=row["periode_construction"],
        type_installation_chauffage=row["type_installation_chauffage"],
        type_energie_principale_chauffage=row["type_energie_principale_chauffage"],
        qualite_isolation_enveloppe=row["qualite_isolation_enveloppe"],
        conso_5_usages_ep=row["conso_5_usages_ep"],
        emission_ges_5_usages=row["emission_ges_5_usages"],
        cout_total_5_usages=row["cout_total_5_usages"],
        regime_dpe=row.get("regime_dpe"),
        source_observations=row.get("source_observations", []),
        source_state=row.get("source_state", "unknown"),
        source_snapshot_at=row.get("source_snapshot_at"),
        is_tombstone=row.get("is_tombstone", False),
        lifecycle_facts=row.get(
            "lifecycle_facts",
            {
                "replacement_state": "not_checked",
                "graph_issues": [],
                "date_issues": [],
            },
        ),
        revision_evidence=row.get(
            "revision_evidence",
            {
                "status": "unverified",
                "kind": None,
                "revision_date": None,
                "verified_at": None,
                "evidence_sha256": None,
                "official_source": None,
            },
        ),
    )


async def lock_dpe_inventory_relations(
    session: AsyncSession,
) -> DpeRelationGenerations:
    """Lock both real parents in a stable order, then read their identities."""
    await session.execute(text("LOCK TABLE ONLY public.adresses IN ACCESS SHARE MODE"))
    await session.execute(
        text("LOCK TABLE ONLY public.diagnostics_dpe IN ACCESS SHARE MODE")
    )
    result = await session.execute(
        text("""
            SELECT to_regclass('public.adresses')::oid::bigint
                       AS addresses_generation,
                   to_regclass('public.diagnostics_dpe')::oid::bigint
                       AS diagnostics_generation
        """)
    )
    row = result.mappings().one()
    return DpeRelationGenerations(
        addresses=int(row["addresses_generation"]),
        diagnostics=int(row["diagnostics_generation"]),
    )


async def dpe_inventory_target_by_ban(
    session: AsyncSession,
    *,
    ban_id: str,
    department: str,
) -> DpeInventoryTarget | None:
    """Resolve one private address row from the exact source BAN key."""
    result = await session.execute(
        text("""
            SELECT a.id, a.code_departement, a.ban_id
            FROM public.adresses AS a
            WHERE a.ban_id = :ban_id
              AND a.code_departement = :department
            ORDER BY a.id
            LIMIT 1
        """),
        {"ban_id": ban_id, "department": department},
    )
    row = result.mappings().first()
    if row is None:
        return None
    return DpeInventoryTarget(
        address_id=int(row["id"]),
        department=row["code_departement"],
        ban_id=row["ban_id"],
    )


async def dpe_inventory_target_by_id(
    session: AsyncSession,
    *,
    address_id: int,
    department: str,
) -> DpeInventoryTarget | None:
    """Reload a signed target without accepting a caller-supplied BAN key."""
    result = await session.execute(
        text("""
            SELECT a.id, a.code_departement, a.ban_id
            FROM public.adresses AS a
            WHERE a.id = :address_id
              AND a.code_departement = :department
            LIMIT 1
        """),
        {"address_id": address_id, "department": department},
    )
    row = result.mappings().first()
    if row is None or not row["ban_id"]:
        return None
    return DpeInventoryTarget(
        address_id=int(row["id"]),
        department=row["code_departement"],
        ban_id=row["ban_id"],
    )


async def dpe_inventory_record_by_id(
    session: AsyncSession,
    *,
    document_id: int,
    ban_id: str,
    department: str,
) -> DpeInventoryRecord | None:
    """Read a technical row only inside its signed address target."""
    result = await session.execute(
        text(f"""
            SELECT {_INVENTORY_SELECT_COLUMNS}
            FROM public.diagnostics_dpe AS d
            WHERE d.id = :document_id
              AND d.identifiant_ban = :ban_id
              AND d.code_departement = :department
            LIMIT 1
        """),
        {
            "document_id": document_id,
            "ban_id": ban_id,
            "department": department,
        },
    )
    row = result.mappings().first()
    return None if row is None else _record_from_mapping(dict(row))


async def dpe_inventory_page(
    session: AsyncSession,
    *,
    ban_id: str,
    department: str,
    limit: int,
    anchor: DpeInventoryRecord | None = None,
) -> tuple[list[DpeInventoryRecord], bool]:
    """Read one keyset page plus a sentinel from the exact address source."""
    keyset = ""
    params: dict[str, object] = {
        "ban_id": ban_id,
        "department": department,
        "fetch_limit": limit + 1,
    }
    if anchor is not None:
        keyset = "AND (d.numero_dpe, d.id) > (:anchor_number, :anchor_id)"
        params.update(
            {"anchor_number": anchor.dpe_number, "anchor_id": anchor.document_id}
        )
    result = await session.execute(
        text(f"""
            SELECT {_INVENTORY_SELECT_COLUMNS}
            FROM public.diagnostics_dpe AS d
            WHERE d.identifiant_ban = :ban_id
              AND d.code_departement = :department
              {keyset}
            ORDER BY d.numero_dpe, d.id
            LIMIT :fetch_limit
        """),
        params,
    )
    rows = [_record_from_mapping(dict(row)) for row in result.mappings().all()]
    return rows[:limit], len(rows) > limit


async def dpe_inventory_aggregate(
    session: AsyncSession,
    *,
    ban_id: str,
    department: str,
) -> DpeInventoryAggregate:
    """Count the complete exact-address inventory in the current snapshot."""
    result = await session.execute(
        text("""
            SELECT count(*)::bigint AS total,
                   count(*) FILTER (
                       WHERE NOT d.is_tombstone
                         AND lower(btrim(d.type_batiment)) = 'appartement'
                   )::bigint AS apartment_document_count,
                   count(*) FILTER (
                       WHERE NOT d.is_tombstone
                         AND upper(btrim(d.etiquette_dpe)) = 'A'
                   )::bigint AS class_a,
                   count(*) FILTER (
                       WHERE NOT d.is_tombstone
                         AND upper(btrim(d.etiquette_dpe)) = 'B'
                   )::bigint AS class_b,
                   count(*) FILTER (
                       WHERE NOT d.is_tombstone
                         AND upper(btrim(d.etiquette_dpe)) = 'C'
                   )::bigint AS class_c,
                   count(*) FILTER (
                       WHERE NOT d.is_tombstone
                         AND upper(btrim(d.etiquette_dpe)) = 'D'
                   )::bigint AS class_d,
                   count(*) FILTER (
                       WHERE NOT d.is_tombstone
                         AND upper(btrim(d.etiquette_dpe)) = 'E'
                   )::bigint AS class_e,
                   count(*) FILTER (
                       WHERE NOT d.is_tombstone
                         AND upper(btrim(d.etiquette_dpe)) = 'F'
                   )::bigint AS class_f,
                   count(*) FILTER (
                       WHERE NOT d.is_tombstone
                         AND upper(btrim(d.etiquette_dpe)) = 'G'
                   )::bigint AS class_g,
                   count(*) FILTER (
                       WHERE d.is_tombstone
                          OR upper(btrim(d.etiquette_dpe)) IS NULL
                          OR upper(btrim(d.etiquette_dpe)) NOT IN
                             ('A', 'B', 'C', 'D', 'E', 'F', 'G')
                   )::bigint AS class_unknown
            FROM public.diagnostics_dpe AS d
            WHERE d.identifiant_ban = :ban_id
              AND d.code_departement = :department
        """),
        {"ban_id": ban_id, "department": department},
    )
    row = result.mappings().one()
    return DpeInventoryAggregate(
        total=int(row["total"]),
        apartment_document_count=int(row["apartment_document_count"]),
        distribution={
            "A": int(row["class_a"]),
            "B": int(row["class_b"]),
            "C": int(row["class_c"]),
            "D": int(row["class_d"]),
            "E": int(row["class_e"]),
            "F": int(row["class_f"]),
            "G": int(row["class_g"]),
            "unknown": int(row["class_unknown"]),
        },
    )


@dataclass(frozen=True, slots=True)
class DpeDocumentAdresse:
    """Private row of a document declared at an address (04-03).

    ``document_id`` is a technical tie-breaker of the order; it never
    leaves the API. ``numero_dpe`` is the source number, served as is.
    """

    document_id: int
    numero_dpe: str
    type_batiment: str | None
    surface_habitable: Decimal | float | None
    etiquette_dpe: str | None
    etiquette_ges: str | None
    date_etablissement_dpe: date | None
    date_fin_validite_dpe: date | None
    lifecycle_facts: dict = field(
        default_factory=lambda: {
            "replacement_state": "not_checked",
            "graph_issues": [],
            "date_issues": [],
        }
    )


# Population of the documents declared at an address (04-03, N-11): equality
# on the exact source key within its partition, retired documents
# (tombstones) excluded from the count, the list, the distribution and the
# selection. Shared by every reader below so they count the same documents.
DOCUMENTS_ADRESSE_POPULATION_SQL = """
            FROM public.diagnostics_dpe AS d
            WHERE d.identifiant_ban = :ban_id
              AND d.code_departement = :department
              AND NOT d.is_tombstone
"""
# Chronological order of the list; the technical id only breaks exact ties
# of the page cut, never a tie of the most-recent selection (D-109).
_ORDRE_CHRONOLOGIQUE_SQL = (
    "d.date_etablissement_dpe DESC NULLS LAST, d.numero_dpe, d.id"
)
_DOCUMENT_ADRESSE_COLUMNS = """
            SELECT d.id, d.numero_dpe, d.type_batiment, d.surface_habitable,
                   d.etiquette_dpe, d.etiquette_ges, d.date_etablissement_dpe,
                   d.date_fin_validite_dpe, d.lifecycle_facts
"""


def _document_adresse(row: dict) -> DpeDocumentAdresse:
    return DpeDocumentAdresse(
        document_id=int(row["id"]),
        numero_dpe=row["numero_dpe"],
        type_batiment=row["type_batiment"],
        surface_habitable=row["surface_habitable"],
        etiquette_dpe=row["etiquette_dpe"],
        etiquette_ges=row["etiquette_ges"],
        date_etablissement_dpe=row["date_etablissement_dpe"],
        date_fin_validite_dpe=row["date_fin_validite_dpe"],
        lifecycle_facts=row["lifecycle_facts"],
    )


async def dpe_documents_adresse_chronologiques(
    session: AsyncSession,
    *,
    ban_id: str,
    department: str,
    limit: int,
    offset: int,
) -> list[DpeDocumentAdresse]:
    """One page of the documents declared at *ban_id*, most recent first."""
    result = await session.execute(
        text(f"""
            {_DOCUMENT_ADRESSE_COLUMNS}
            {DOCUMENTS_ADRESSE_POPULATION_SQL}
            ORDER BY {_ORDRE_CHRONOLOGIQUE_SQL}
            LIMIT :limit OFFSET :offset
        """),
        {"ban_id": ban_id, "department": department, "limit": limit, "offset": offset},
    )
    return [_document_adresse(dict(row)) for row in result.mappings().all()]


async def dpe_documents_adresse_agregat(
    session: AsyncSession,
    *,
    ban_id: str,
    department: str,
) -> DpeInventoryAggregate:
    """Count and A-G distribution of the documents declared at *ban_id*.

    Same population as the list (tombstones excluded); a missing or
    malformed class is counted as unknown.
    """
    classes = ",\n".join(
        f"count(*) FILTER (WHERE upper(btrim(d.etiquette_dpe)) = '{label}')"
        f"::bigint AS class_{label.lower()}"
        for label in "ABCDEFG"
    )
    result = await session.execute(
        text(f"""
            SELECT count(*)::bigint AS total,
                   count(*) FILTER (
                       WHERE lower(btrim(d.type_batiment)) = 'appartement'
                   )::bigint AS apartment_document_count,
                   {classes}
            {DOCUMENTS_ADRESSE_POPULATION_SQL}
        """),
        {"ban_id": ban_id, "department": department},
    )
    row = result.mappings().one()
    known = {label: int(row[f"class_{label.lower()}"]) for label in "ABCDEFG"}
    total = int(row["total"])
    return DpeInventoryAggregate(
        total=total,
        apartment_document_count=int(row["apartment_document_count"]),
        distribution={**known, "unknown": total - sum(known.values())},
    )


async def dpe_documents_adresse_candidats(
    session: AsyncSession,
    *,
    ban_id: str,
    department: str,
    limit: int,
) -> tuple[list[DpeDocumentAdresse], bool]:
    """The documents of the population, for the global selection (D-109).

    The selection is computed on the whole population, never on the
    requested page. Reads one row beyond *limit*: a present sentinel means
    the population was not read entirely, which forbids any selection.
    """
    result = await session.execute(
        text(f"""
            {_DOCUMENT_ADRESSE_COLUMNS}
            {DOCUMENTS_ADRESSE_POPULATION_SQL}
            ORDER BY {_ORDRE_CHRONOLOGIQUE_SQL}
            LIMIT :fetch_limit
        """),
        {"ban_id": ban_id, "department": department, "fetch_limit": limit + 1},
    )
    rows = [_document_adresse(dict(row)) for row in result.mappings().all()]
    return rows[:limit], len(rows) > limit


# Plan 04.1-13: documents whose ADEME key is orphan and whose raw address
# names this BAN key exactly (``ban_id_adresse_brute``, filled by the DPE
# import). A distinct population: never mixed with the one above.
DOCUMENTS_ADRESSE_BRUTE_POPULATION_SQL = """
            FROM public.diagnostics_dpe AS d
            WHERE d.ban_id_adresse_brute = :ban_id
              AND d.code_departement = :department
              AND NOT d.is_tombstone
"""
_MARQUEUR_ADRESSE_BRUTE_SQL = """
    SELECT col_description(a.attrelid, a.attnum) AS marqueur
    FROM pg_attribute AS a
    WHERE a.attrelid = 'public.diagnostics_dpe'::regclass
      AND a.attname = 'ban_id_adresse_brute'
      AND NOT a.attisdropped
"""


async def dpe_adresse_brute_calculee(session: AsyncSession) -> bool:
    """True when the served relation carries the raw-address attachment.

    The DPE import comments the column of its candidate once the step ran
    (``app.etl_dpe.adresse_brute.MARQUEUR_CALCUL``): a column absent, or
    present but never computed (migrated, not yet re-imported), is not a zero.
    """
    result = await session.execute(text(_MARQUEUR_ADRESSE_BRUTE_SQL))
    row = result.mappings().first()
    return row is not None and row["marqueur"] == MARQUEUR_CALCUL


async def dpe_documents_adresse_brute(
    session: AsyncSession,
    *,
    ban_id: str,
    department: str,
    limit: int,
) -> tuple[int, list[DpeDocumentAdresse]]:
    """Total and the *limit* most recent documents attached by raw address."""
    result = await session.execute(
        text(f"""
            {_DOCUMENT_ADRESSE_COLUMNS},
                   count(*) OVER ()::bigint AS total
            {DOCUMENTS_ADRESSE_BRUTE_POPULATION_SQL}
            ORDER BY {_ORDRE_CHRONOLOGIQUE_SQL}
            LIMIT :limit
        """),
        {"ban_id": ban_id, "department": department, "limit": limit},
    )
    rows = [dict(row) for row in result.mappings().all()]
    total = int(rows[0]["total"]) if rows else 0
    return total, [_document_adresse(row) for row in rows]


async def dpe_par_identifiant_ban(
    session: AsyncSession,
    ban_id: str,
    limit: int = 120,
) -> tuple[list[dict], bool]:
    """Diagnostics whose ADEME key IS the resolved BAN key (D-01, D-04).

    The only fetch a public individual attribution may ever be built on.
    ``diagnostics_dpe.identifiant_ban`` is byte-identical to ``adresses.ban_id``
    (``INSEE_FANTOIR_NUMERO``) and produced by ADEME itself, so the predicate is
    a plain bound equality -- no geometry, no radius, no fuzzy score. A
    diagnostic reached by proximity is a different fact and never enters here.

    No ``type_batiment`` filter, unlike the logements path: a collective
    diagnostic on this very address is legitimate building-granularity
    information, and the policy classifies it (Pitfall 2). Filtering it out here
    would erase it instead.

    Ordered by ``numero_dpe``: a deterministic order that carries no meaning.
    Ordering by date would hand the policy a "most recent first" list, i.e. the
    exact temptation D-06 forbids.

    Empty identity -> ``([], False)`` with no round trip: D-04 says no BAN
    identity means no attribution, and an empty predicate must never be sent to
    the database as ``identifiant_ban = ''``.
    """
    if not ban_id:
        return [], False

    sql = text(f"""
        SELECT {_ATTRIBUTION_SELECT_COLS}
        FROM diagnostics_dpe d
        WHERE d.identifiant_ban = :ban_id
          AND NOT d.is_tombstone
          AND d.code_departement = :dept
        ORDER BY d.numero_dpe
        LIMIT :limit
    """)

    result = await session.execute(
        sql,
        {
            "ban_id": ban_id,
            "dept": _dept_depuis_cle(ban_id),
            # Sentinel: read one row beyond what the caller may publish.
            "limit": limit + 1,
        },
    )
    return _borne([dict(row._mapping) for row in result], limit)


async def dpe_par_identifiants_ban(
    session: AsyncSession,
    ban_ids: Sequence[str],
    limit: int = 120,
) -> tuple[list[dict], bool]:
    """Diagnostics of the OTHER BAN keys of the same RNB building (D-02).

    Reserved for the building context: a corner building carries several keys
    and its diagnostics are split across them. Those rows may feed a count and a
    class distribution, NEVER an individual attribution nor a selectable
    candidate -- they belong to another address of the same building.

    Same doctrine as the exact fetch: keys are deduplicated in Python, bound
    through an expanding parameter (string interpolation is the T-51-01 threat),
    the scan is pruned to the departments the keys name, and the sentinel row is
    read. An empty key list returns ``([], False)`` without a round trip.
    """
    cles = [c for c in dict.fromkeys(ban_ids) if c]
    if not cles:
        return [], False

    depts = sorted({_dept_depuis_cle(c) for c in cles})
    sql = text(f"""
        SELECT {_ATTRIBUTION_SELECT_COLS}
        FROM diagnostics_dpe d
        WHERE d.identifiant_ban IN :ban_ids
          AND NOT d.is_tombstone
          AND d.code_departement IN :depts
        ORDER BY d.numero_dpe
        LIMIT :limit
    """).bindparams(
        bindparam("ban_ids", expanding=True),
        bindparam("depts", expanding=True),
    )

    result = await session.execute(
        sql, {"ban_ids": cles, "depts": depts, "limit": limit + 1}
    )
    return _borne([dict(row._mapping) for row in result], limit)


async def dpe_par_point(
    session: AsyncSession,
    lat: float,
    lon: float,
    rayon_m: float = 500.0,
    etiquette_dpe: str | None = None,
    annee_min: int | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """Find DPE diagnostics near a GPS point with optional filters.

    Spatial filter uses ST_DWithin on the geography GIST index. Results
    are ordered by geodesic KNN distance on that same index: migration 107
    dropped the geometry index a planar sort needs (audit C10, 04.7-01).
    All filter values use bind parameters for SQL injection safety.
    """
    # Build WHERE clause dynamically -- only structure changes, values via params
    where_clauses = [
        "position IS NOT NULL",
        "NOT is_tombstone",
        "source_state NOT IN ('absent_from_complete_scope', 'explicitly_deactivated')",
        "ST_DWithin("
        "    position::geography,"
        "    ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,"
        "    :rayon_m"
        ")",
    ]
    params: dict = {
        "lat": lat,
        "lon": lon,
        "rayon_m": rayon_m,
        "limit": limit,
        "offset": offset,
    }

    if etiquette_dpe is not None:
        where_clauses.append("etiquette_dpe = :etiquette_dpe")
        params["etiquette_dpe"] = etiquette_dpe.upper()

    if annee_min is not None:
        where_clauses.append("annee_construction >= :annee_min")
        params["annee_min"] = annee_min

    where_sql = " AND ".join(where_clauses)
    select_cols = ", ".join(DPE_SELECT_COLUMNS)

    # Scalar columns come from the shared DPE_SELECT_COLUMNS; longitude/latitude
    # are derived from the PostGIS geometry here (the zone path reads the stored
    # columns instead), so the geo projection stays local to each query.
    sql = text(f"""
        SELECT {select_cols},
               ST_X(position) AS longitude,
               ST_Y(position) AS latitude
        FROM diagnostics_dpe
        WHERE {where_sql}
        ORDER BY position::geography
            <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
        LIMIT :limit OFFSET :offset
    """)

    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result]
