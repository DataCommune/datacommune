"""Staging index contract for every pipeline with its own staging DDL.

Lot 2 of DB-HYGIENE-01.  The first lot fixed ``etl_antennes``; the audit then
found 14 more pipelines overriding ``create_staging_table`` with a bare
``LIKE prod INCLUDING ALL``.  ``INCLUDING ALL`` implies ``INCLUDING INDEXES``,
so PostgreSQL copies every production index onto staging under an
auto-generated name (``foo_bar_idx``, ``foo_bar_idx1``...).  Those copies then
win the post-swap de-duplication over the explicitly named ones, and the
production table ends up carrying auto-generated names instead of the names
its migrations declared — measured here as the red state before the fix.

Two invariants, replayed against the in-memory PostgreSQL catalog of
``test_etl_staging_index_hygiene``:

  1. no pipeline emits ``LIKE ... INCLUDING ALL`` without
     ``EXCLUDING INDEXES``;
  2. after a full swap the production table carries exactly the index names
     its migrations declare — no more (accumulation) and no fewer (silent
     index loss, the failure mode migration 042 had to repair by hand).

Partition child indexes are excluded from invariant 2: PostgreSQL names them
itself and they are not declared by any migration.
"""

import re
from contextlib import ExitStack
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from app.etl.base import DEPARTEMENTS, BasePipeline
from app.etl.runner import PipelineRunner
from app.etl_arcep.pipeline import ArcepPipeline
from app.etl_bdtopo.pipeline import BdTopoPipeline
from app.etl_copropriete.pipeline import CoproprietePipeline
from app.etl_demographie.pipeline import DemographiePipeline
from app.etl_encadrement_loyers.pipeline import EncadrementLoyersPipeline
from app.etl_equipements.pipeline import EquipementsPipeline
from app.etl_fiscalite.pipeline import FiscalitePipeline
from app.etl_loyers.pipeline import LoyersPipeline
from app.etl_monuments.pipeline import MonumentsPipeline
from app.etl_permis.pipeline import PermisPipeline
from app.etl_revenus.pipeline import RevenusPipeline
from app.etl_scolaire.pipeline import ScolairePipeline
from app.etl_transport.pipeline import TransportPipeline
from app.etl_vacance.pipeline import VacancePipeline
from app.etl_zonage_abc.pipeline import ZonageAbcPipeline
from tests.test_etl_staging_index_hygiene import (
    FakeCatalog,
    FakeConnection,
    FakeCursor,
    FakeIndex,
)


@pytest.fixture(autouse=True)
def _full_france_scope(bind_departements):
    # Departmental pipelines read their departements from the bound scope;
    # these catalog doubles model a full-France instance (102 partitions).
    bind_departements(DEPARTEMENTS, BasePipeline)


# Row count returned by every ``SELECT count(*)``: above every GATE-01 floor
# and every "staging < 50% of production" guard (both sides read the same
# number, so the ratio is always 1).
_ROWCOUNT = 400_000_000

# A ``LIKE`` that copies production indexes onto staging.
_BARE_INCLUDING_ALL_RE = re.compile(
    r"LIKE \S+ INCLUDING ALL(?! EXCLUDING INDEXES)", re.IGNORECASE
)


@dataclass(frozen=True)
class Table:
    """One production table: its migration-declared index set and constraints."""

    name: str
    # (index name, access method, column expression)
    indexes: tuple[tuple[str, str, str], ...]
    # (constraint kind, column list) — named by PostgreSQL, not by a migration
    constraints: tuple[tuple[str, str], ...]
    partitioned: bool = False

    @property
    def expected_index_names(self) -> set[str]:
        names = {name for name, _method, _columns in self.indexes}
        for kind, columns in self.constraints:
            cols = "_".join(c.strip() for c in columns.split(","))
            if kind == "PRIMARY KEY":
                names.add(f"{self.name}_pkey")
            else:
                names.add(f"{self.name}_{cols}_key")
        return names


@dataclass(frozen=True)
class Case:
    """A pipeline plus every production table its swap publishes."""

    pipeline_cls: type[BasePipeline]
    tables: tuple[Table, ...]
    # Modules whose ``check_bounds_gate`` opens its own real connection.
    bounds_gate_modules: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return self.pipeline_cls.__module__.split(".")[1]


# ---------------------------------------------------------------------------
# Migration-declared index sets, one entry per production table
# ---------------------------------------------------------------------------

CASES: tuple[Case, ...] = (
    # migration 010
    Case(
        ArcepPipeline,
        (
            Table(
                "eligibilite_fixe",
                (
                    ("idx_eligibilite_code_insee", "btree", "code_insee"),
                    ("idx_eligibilite_code_insee_voie", "btree", "code_insee, voie"),
                    ("idx_eligibilite_id_imb", "btree", "id_imb"),
                ),
                (("PRIMARY KEY", "id"),),
            ),
        ),
    ),
    # migrations 008 + 021
    Case(
        BdTopoPipeline,
        (
            Table(
                "batiments",
                (
                    ("idx_batiments_geometrie", "gist", "geometrie"),
                    ("idx_batiments_geometrie_geog", "gist", "geography(geometrie)"),
                    ("idx_batiments_cleabs", "btree", "cleabs"),
                    ("idx_batiments_code_departement", "btree", "code_departement"),
                ),
                (("PRIMARY KEY", "id, code_departement"),),
                partitioned=True,
            ),
        ),
        bounds_gate_modules=("app.etl_bdtopo.pipeline",),
    ),
    # migrations 031 + 076
    Case(
        CoproprietePipeline,
        (
            Table(
                "copropriete",
                (
                    ("idx_copropriete_code_commune", "btree", "code_commune"),
                    ("idx_copropriete_code_postal", "btree", "code_postal"),
                    ("idx_copropriete_id_parcelle_1", "btree", "id_parcelle_1"),
                    ("idx_copropriete_code_insee", "btree", "code_insee"),
                    ("idx_copropriete_id_parcelle_2", "btree", "id_parcelle_2"),
                    ("idx_copropriete_id_parcelle_3", "btree", "id_parcelle_3"),
                ),
                (("PRIMARY KEY", "id"), ("UNIQUE", "numero_immatriculation")),
            ),
        ),
    ),
    # migration 030
    Case(
        DemographiePipeline,
        (
            Table(
                "demographie",
                (
                    ("idx_demographie_code_insee", "btree", "code_insee"),
                    ("idx_demographie_code_departement", "btree", "code_departement"),
                ),
                (("PRIMARY KEY", "id"), ("UNIQUE", "code_insee, annee")),
            ),
        ),
    ),
    # migration 028
    Case(
        EncadrementLoyersPipeline,
        (
            Table(
                "encadrement_loyers",
                (
                    ("idx_encadrement_ville", "btree", "ville"),
                    ("idx_encadrement_annee", "btree", "annee"),
                    ("idx_encadrement_geom", "gist", "geom"),
                    ("idx_encadrement_quartier", "btree", "code_insee_quartier"),
                ),
                (
                    ("PRIMARY KEY", "id"),
                    (
                        "UNIQUE",
                        "annee, ville, numero_quartier, nb_pieces, "
                        "epoque_construction, type_location",
                    ),
                ),
            ),
        ),
    ),
    # migrations 034/041 + 037/042
    Case(
        EquipementsPipeline,
        (
            Table(
                "equipements",
                (
                    ("idx_equipements_code_commune", "btree", "code_commune"),
                    ("idx_equipements_type", "btree", "type_equipement"),
                    ("idx_equipements_domaine", "btree", "domaine"),
                    ("idx_equipements_geom", "gist", "geom"),
                    ("idx_equipements_dept", "btree", "code_departement"),
                    ("idx_equipements_geom_geog", "gist", "geography(geom)"),
                ),
                (("PRIMARY KEY", "id, code_departement"),),
                partitioned=True,
            ),
        ),
        bounds_gate_modules=("app.etl_equipements.pipeline",),
    ),
    # migration 027
    Case(
        FiscalitePipeline,
        (
            Table(
                "fiscalite_locale",
                (
                    ("idx_fiscalite_code_insee", "btree", "code_insee"),
                    ("idx_fiscalite_code_departement", "btree", "code_departement"),
                ),
                (("PRIMARY KEY", "id"), ("UNIQUE", "code_insee, annee")),
            ),
        ),
    ),
    # migration 007
    Case(
        LoyersPipeline,
        (
            Table(
                "indicateurs_loyers",
                (
                    ("idx_loyers_code_insee", "btree", "code_insee"),
                    ("idx_loyers_code_departement", "btree", "code_departement"),
                ),
                (
                    ("PRIMARY KEY", "id"),
                    ("UNIQUE", "code_insee, type_habitat, annee"),
                ),
            ),
        ),
    ),
    # migration 009
    Case(
        MonumentsPipeline,
        (
            Table(
                "monuments_historiques",
                (
                    ("idx_monuments_position", "gist", "position"),
                    ("idx_monuments_code_insee", "btree", "code_insee"),
                    ("idx_monuments_reference", "btree", "reference"),
                ),
                (("PRIMARY KEY", "id"),),
            ),
        ),
    ),
    # migrations 032 and 109
    Case(
        PermisPipeline,
        (
            Table(
                "permis",
                (
                    ("idx_permis_code_commune", "btree", "code_commune"),
                    ("idx_permis_annee", "btree", "annee"),
                    ("idx_permis_code_departement", "btree", "code_departement"),
                ),
                (
                    ("PRIMARY KEY", "id"),
                    ("UNIQUE NULLS NOT DISTINCT", "numero_dossier, destination"),
                ),
            ),
        ),
    ),
    # migration 029
    Case(
        RevenusPipeline,
        (
            Table(
                "revenus",
                (
                    ("idx_revenus_code_insee", "btree", "code_insee"),
                    ("idx_revenus_code_departement", "btree", "code_departement"),
                ),
                (("PRIMARY KEY", "id"), ("UNIQUE", "code_insee, annee")),
            ),
        ),
    ),
    # migration 011
    Case(
        ScolairePipeline,
        (
            Table(
                "carte_scolaire",
                (
                    ("idx_scolaire_code_insee", "btree", "code_insee"),
                    ("idx_scolaire_code_insee_rne", "btree", "code_insee, code_rne"),
                ),
                (("PRIMARY KEY", "id"),),
            ),
        ),
    ),
    # migration 012 — two production tables
    Case(
        TransportPipeline,
        (
            Table(
                "arrets_transport",
                (
                    ("idx_arrets_position", "gist", "position"),
                    ("idx_arrets_stop_name", "btree", "stop_name"),
                ),
                (("PRIMARY KEY", "id"),),
            ),
            Table(
                "gares",
                (
                    ("idx_gares_position", "gist", "position"),
                    ("idx_gares_code_insee", "btree", "code_insee"),
                ),
                (("PRIMARY KEY", "id"),),
            ),
        ),
    ),
    # migration 035
    Case(
        VacancePipeline,
        (
            Table(
                "vacance",
                (
                    ("idx_vacance_code_commune", "btree", "code_commune"),
                    ("idx_vacance_code_departement", "btree", "code_departement"),
                ),
                (("PRIMARY KEY", "id"), ("UNIQUE", "code_commune, annee")),
            ),
        ),
    ),
    # migration 026
    Case(
        ZonageAbcPipeline,
        (
            Table(
                "zonage_abc",
                (
                    ("idx_zonage_abc_code_insee", "btree", "code_insee"),
                    ("idx_zonage_abc_zone", "btree", "zone"),
                    ("idx_zonage_abc_code_departement", "btree", "code_departement"),
                ),
                (("PRIMARY KEY", "id"), ("UNIQUE", "code_insee")),
            ),
        ),
    ),
)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class RecordingCatalog(FakeCatalog):
    """Catalog that also keeps every statement it was handed."""

    def __init__(self) -> None:
        super().__init__()
        self.statements: list[str] = []

    def execute(self, sql: str, params: object = None) -> list[tuple]:
        normalized = " ".join(sql.split())
        self.statements.append(normalized)
        # The catalog has no data rows. COR-04 covers this rejection with real
        # PostGIS; model only its successful empty result in the DDL contract.
        if normalized == (
            "SELECT 1 FROM encadrement_loyers_staging "
            "WHERE numero_quartier IS NULL "
            "GROUP BY annee, ville, nb_pieces, epoque_construction, "
            "type_location HAVING count(*) > 1 LIMIT 1"
        ):
            return []
        return super().execute(sql, params)


def seeded_catalog(case: Case) -> RecordingCatalog:
    """Seed production exactly as its migrations leave it."""
    catalog = RecordingCatalog()
    catalog.rowcount = _ROWCOUNT
    for table in case.tables:
        catalog.add_table(table.name)
        catalog.sequences.add(f"{table.name}_id_seq")
        for kind, columns in table.constraints:
            catalog.add_constraint(table.name, kind, columns)
        for name, method, columns in table.indexes:
            catalog.add_index(FakeIndex(name, table.name, False, method, columns))
    return catalog


def run_pipeline(case: Case, catalog: RecordingCatalog) -> None:
    """Replay ``create_staging_table`` then the full ``swap``."""
    if case.pipeline_cls is EncadrementLoyersPipeline:
        # COR-13 retires publication; the seeded catalog must remain untouched.
        from app.common.unqualified_surfaces import SurfaceQuarantinedError

        with pytest.raises(SurfaceQuarantinedError):
            case.pipeline_cls()
        assert catalog.statements == []
        return
    pipeline = case.pipeline_cls()
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "psycopg.connect",
                side_effect=lambda *a, **kw: FakeConnection(catalog),
            )
        )
        # GATE-02 reads information_schema, which this catalog does not model.
        stack.enter_context(
            patch.object(
                PipelineRunner,
                "_check_schema_drift_conn",
                staticmethod(lambda *a, **kw: None),
            )
        )
        # The geo bounds gate runs its own PostGIS query — covered elsewhere.
        for module in case.bounds_gate_modules:
            stack.enter_context(patch(f"{module}.check_bounds_gate", return_value=None))
        pipeline.create_staging_table(pipeline.get_sync_dsn())
        pipeline.swap()


def production_index_names(catalog: FakeCatalog, table: str) -> set[str]:
    """Indexes carried by the production table itself, partitions excluded."""
    return catalog.indexes_of(table)


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_staging_creation_never_copies_production_indexes(case: Case) -> None:
    """Invariant 1: no ``LIKE ... INCLUDING ALL`` without ``EXCLUDING INDEXES``."""
    catalog = seeded_catalog(case)

    run_pipeline(case, catalog)

    offenders = [s for s in catalog.statements if _BARE_INCLUDING_ALL_RE.search(s)]
    assert offenders == [], (
        f"{case.id} copies production indexes onto staging: {offenders}"
    )


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_swap_publishes_exactly_the_migration_index_names(case: Case) -> None:
    """Invariant 2: production carries its declared index set, no more, no less."""
    catalog = seeded_catalog(case)

    run_pipeline(case, catalog)

    for table in case.tables:
        assert production_index_names(catalog, table.name) == (
            table.expected_index_names
        ), f"{case.id}: {table.name} drifted from its migration index set"
    assert catalog.unhandled == []


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_repeated_runs_do_not_accumulate_indexes(case: Case) -> None:
    """Two consecutive runs converge — the production symptom was growth."""
    catalog = seeded_catalog(case)

    for _ in range(2):
        run_pipeline(case, catalog)
        for table in case.tables:
            assert production_index_names(catalog, table.name) == (
                table.expected_index_names
            ), f"{case.id}: {table.name} drifted on the second run"


# ---------------------------------------------------------------------------
# Chantier B — gares swaps outside swap_tables()
# ---------------------------------------------------------------------------

TRANSPORT_CASE = next(c for c in CASES if c.pipeline_cls is TransportPipeline)


def test_gares_swap_gets_the_shared_index_cleanup() -> None:
    """``gares`` renames by hand, so the cleanup must be invoked for it too.

    Seeded with the exact production symptom: staging-named indexes already
    sitting on the production table, left there by earlier runs.
    """
    catalog = seeded_catalog(TRANSPORT_CASE)
    for name, method, columns in (
        ("idx_gares_staging_position", "gist", "position"),
        ("gares_staging_code_insee_idx", "btree", "code_insee"),
        ("gares_staging_code_insee_idx1", "btree", "code_insee"),
    ):
        catalog.add_index(FakeIndex(name, "gares", False, method, columns))

    run_pipeline(TRANSPORT_CASE, catalog)

    published = catalog.indexes_of("gares")
    assert sorted(n for n in published if "staging" in n) == []
    assert published == {
        "gares_pkey",
        "idx_gares_position",
        "idx_gares_code_insee",
    }


# ---------------------------------------------------------------------------
# Chantier C — partitioned tables: the de-duplication pass and child indexes
# ---------------------------------------------------------------------------

PARTITIONED_CASES = tuple(c for c in CASES if any(t.partitioned for t in c.tables))


class DropSpyCatalog(RecordingCatalog):
    """Records which indexes a ``DROP INDEX`` actually removed."""

    def __init__(self) -> None:
        super().__init__()
        self.dropped_indexes: list[str] = []
        self.dropped_child_indexes: list[str] = []

    def _ddl_indexes(self, statement: str) -> bool:
        m = re.match(r"DROP INDEX (?:IF EXISTS )?(\S+)$", statement, re.IGNORECASE)
        if m and m.group(1) in self.indexes:
            self.dropped_indexes.append(m.group(1))
            if m.group(1) in self.index_parents:
                self.dropped_child_indexes.append(m.group(1))
        return super()._ddl_indexes(statement)


def test_catalog_refuses_to_drop_an_attached_partition_child_index() -> None:
    """Negative control: the model enforces PostgreSQL's refusal.

    Without this the two tests below would be vacuous.
    """
    catalog = FakeCatalog()
    catalog.add_table("batiments")
    catalog.execute(
        "CREATE TABLE batiments_44 PARTITION OF batiments FOR VALUES IN ('44')"
    )
    catalog.execute("CREATE INDEX idx_batiments_cleabs ON batiments (cleabs)")
    child = next(iter(catalog.indexes_of("batiments_44")))

    with pytest.raises(AssertionError, match="requires it"):
        catalog.execute(f"DROP INDEX {child}")


def test_dedup_keeps_the_attached_child_and_drops_its_standalone_twin() -> None:
    """The adversarial shape: a clean-named twin outranking an attached child.

    Right after the swap the partitions still carry staging names (they are
    renamed later, in phase C), so *every* child index is staging-tainted. A
    same-shape index with a clean name on the same partition would therefore
    win the de-duplication and the attached child would be the one dropped —
    which PostgreSQL refuses, aborting the whole swap transaction.
    """
    catalog = FakeCatalog()
    catalog.add_table("batiments")
    catalog.execute(
        "CREATE TABLE batiments_staging_44 PARTITION OF batiments FOR VALUES IN ('44')"
    )
    catalog.execute("CREATE INDEX idx_batiments_cleabs_staging ON batiments (cleabs)")
    (child,) = catalog.indexes_of("batiments_staging_44")
    catalog.execute(
        "CREATE INDEX idx_batiments_cleabs_44 ON batiments_staging_44 (cleabs)"
    )

    pipeline = BdTopoPipeline()
    pipeline.rename_indexes_after_swap(FakeCursor(catalog))

    # The child survives the de-duplication; pass 2 only strips its staging name.
    surviving = pipeline.canonical_index_name(child)
    assert surviving in catalog.indexes, "the attached child index must survive"
    assert surviving in catalog.index_parents, "it must still hang off its parent"
    assert "idx_batiments_cleabs_44" not in catalog.indexes


@pytest.mark.parametrize(
    "case", PARTITIONED_CASES, ids=[c.id for c in PARTITIONED_CASES]
)
def test_partitioned_swap_never_drops_a_partition_child_index(case: Case) -> None:
    """A full swap on a partitioned table only ever drops parents/standalones."""
    catalog = DropSpyCatalog()
    catalog.rowcount = _ROWCOUNT
    for table in case.tables:
        catalog.add_table(table.name)
        catalog.sequences.add(f"{table.name}_id_seq")
        for kind, columns in table.constraints:
            catalog.add_constraint(table.name, kind, columns)
        for name, method, columns in table.indexes:
            catalog.add_index(FakeIndex(name, table.name, False, method, columns))

    run_pipeline(case, catalog)
    run_pipeline(case, catalog)  # second run: the accumulation scenario

    assert catalog.dropped_child_indexes == []
    # Non-vacuous: the swap really did build attached child indexes.
    assert catalog.index_parents
