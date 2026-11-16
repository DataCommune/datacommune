"""Every served materialized view is rebuilt by the publications it reads.

A rename swap gives the production table a new OID; a materialized view
stays bound to the old one until it is DROPped and CREATEd again. A view
reading a table whose dataset is missing from ``DependentMatviewHook``
therefore keeps serving the previous generation after every publication
(frozen), then disappears with the ``*_old`` drop.

Sources of truth, all read from the code, none restated here:
- the views: every ``CREATE MATERIALIZED VIEW`` of the migrations;
- their definitions: the canonical runtime DDL (``app.commune.matview_ddl``,
  ``app.common.communes_lookup_ddl``), the one the hook executes;
- the swapped tables of each dataset: ``published_old_tables`` of the
  pipelines ``run_etl`` imports.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.common import communes_lookup_ddl
from app.common.matviews import EXPECTED_MATVIEWS
from app.commune import matview_ddl
from app.etl.hooks import DependentMatviewHook

pytestmark = pytest.mark.unit

_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"

# Views a migration creates that are deliberately not served, hence not
# rebuilt on publication. Each entry names the guard that keeps it unserved.
_UNSERVED = {
    # COR-09 quarantine: tests/test_unqualified_surfaces.py asserts it is
    # neither an expected rebuild nor a hook dependency.
    "scoring_renov_target",
}

# Source relations no publication renames, so their OID never changes and a
# bound view never goes stale on them.
_NEVER_SWAPPED = {
    # Offer dictionary appended in place by the connectivite import
    # (app/etl_arcep/pipeline.py: LOCK TABLE ... INSERT), never renamed.
    "eligibilite_offres",
    # Referential replaced in place by scripts/import_geo_boundaries.py
    # (DELETE then INSERT in one transaction, then the chain rebuilt), never
    # renamed: read by v_departement_synthese for membership (04.7-10). An
    # arrondissement is placed through the fixed list plm, not
    # geo_arrondissements, which no view reads.
    "geo_communes",
}

_RELATION = re.compile(
    r"\b(?:FROM|JOIN)\s+(?:LATERAL\s+)?(?!LATERAL\b)(\w+)\b(?!\s*\()"
)
# A CTE name, with or without its column list: ``plm(code, commune_code) AS (``.
_CTE = re.compile(r"(\w+)\s*(?:\([^)]*\))?\s+AS\s*\(")


def _relations(create_sql: str) -> set[str]:
    """Relations a view definition reads, minus its own CTE names."""
    body = re.sub(r"--[^\n]*", "", create_sql)
    return set(_RELATION.findall(body)) - set(_CTE.findall(body))


def _definitions() -> dict[str, str]:
    """View name -> canonical runtime CREATE statement."""
    defs = dict(matview_ddl.INTERMEDIATE_MATVIEWS)
    defs["v_commune_synthese"] = matview_ddl.CREATE_SQL
    defs[matview_ddl.DEPARTEMENT_VIEW] = matview_ddl.DEPARTEMENT_CREATE_SQL
    defs[matview_ddl.ANNUEL_VIEW] = matview_ddl.ANNUEL_CREATE_SQL
    defs["communes_lookup"] = communes_lookup_ddl.CREATE_SQL
    return defs


def _rebuilt_by() -> dict[str, str]:
    """View name -> the hook entry (root view) whose rebuild recreates it."""
    roots = {"communes_lookup": "communes_lookup"}
    chain = matview_ddl.commune_synthese_statements()
    for name in _definitions():
        if any(f"CREATE MATERIALIZED VIEW {name} AS" in sql for sql in chain):
            roots[name] = "v_commune_synthese"
    return roots


def _swapped_by() -> dict[str, str]:
    """Production relation -> dataset whose publication renames it."""
    import run_etl

    owners: dict[str, str] = {}
    for dataset, cls in run_etl.PIPELINE_MAP.items():
        for old in cls().published_old_tables:
            assert old.endswith("_old"), (dataset, old)
            owners[old.removesuffix("_old")] = dataset
    return owners


def _base_relations(view: str, defs: dict[str, str]) -> set[str]:
    """Tables a view reads, through the views it reads (transitive)."""
    tables: set[str] = set()
    for rel in _relations(defs[view]):
        tables |= _base_relations(rel, defs) if rel in defs else {rel}
    return tables


def _migration_matviews() -> set[str]:
    names: set[str] = set()
    for path in _MIGRATIONS.glob("*.py"):
        names |= set(
            re.findall(
                r"CREATE MATERIALIZED VIEW (\w+) AS", path.read_text(encoding="utf-8")
            )
        )
    return names


def _hook_rebuilds(dataset: str) -> set[str]:
    return {name for name, _ in DependentMatviewHook._DEPENDENCIES.get(dataset, ())}


def test_every_migration_matview_is_rebuilt_or_declared_unserved():
    created = _migration_matviews()
    assert created, "no materialized view found in the migrations"
    rebuilt = _rebuilt_by()
    assert created - _UNSERVED <= set(rebuilt), (
        "materialized views created by a migration but rebuilt by no publication: "
        f"{sorted(created - _UNSERVED - set(rebuilt))}"
    )
    # A stale exemption hides nothing: it must still name a migration view.
    assert created >= _UNSERVED


def test_rebuild_roots_are_the_expected_matviews():
    """The roots the hook rebuilds are the ones the cleanup repairs."""
    assert set(_rebuilt_by().values()) == set(EXPECTED_MATVIEWS)


def test_each_view_source_publication_rebuilds_the_view():
    defs = _definitions()
    owners = _swapped_by()
    rebuilt = _rebuilt_by()
    missing: list[str] = []
    unowned: set[str] = set()
    for view in defs:
        for table in _base_relations(view, defs):
            if table in _NEVER_SWAPPED:
                continue
            dataset = owners.get(table)
            if dataset is None:
                unowned.add(table)
            elif rebuilt[view] not in _hook_rebuilds(dataset):
                missing.append(f"{view} reads {table} ({dataset})")
    assert not unowned, f"source relations no pipeline publishes: {sorted(unowned)}"
    assert not missing, f"publications that leave a view frozen: {missing}"


def test_exemptions_are_still_read_by_a_view():
    defs = _definitions()
    read = set().union(*(_base_relations(view, defs) for view in defs))
    assert read >= _NEVER_SWAPPED
    assert not (_NEVER_SWAPPED & set(_swapped_by()))
