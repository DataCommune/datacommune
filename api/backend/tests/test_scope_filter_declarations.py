"""Every national pipeline filters to the instance scope (D-22, D-23).

No database: declarations are read from the registered classes and checked
against the staging DDL written in each pipeline module.
"""

import inspect
import re
import sys

import pytest

import run_etl
from app.common.source_catalog import DATASET_CATALOG
from app.data_quality.profiles import PROFILES, _compute_volume_floor
from app.etl.base import DEPARTEMENTS
from app.etl_encadrement_loyers.pipeline import EncadrementLoyersPipeline
from app.scope import SCOPE_EXCLUDED_KEYS, InstanceScope

pytestmark = pytest.mark.unit

# Downloaded per departement: filtered at download, not at import (plan 02-06).
DEPARTMENTAL = {
    "ban",
    "cadastre",
    "dvf",
    "dpe",
    "batiments",
    "connectivite",
    "rnb_liens",
    "bdnb_batiment_parcelle",
}
NATIONAL = [
    cls for cls in run_etl.ALL_PIPELINES if cls().dataset_name not in DEPARTMENTAL
] + [EncadrementLoyersPipeline]


def _new(cls):
    # encadrement_loyers is quarantined: its constructor refuses on purpose.
    # Its declarations are plain properties, read without constructing it.
    return cls.__new__(cls) if cls is EncadrementLoyersPipeline else cls()


COMMUNE_COLUMNS = {"code_insee", "code_commune", "code_insee_quartier"}
# DQ profile key of each national dataset, when it differs from dataset_name.
PROFILE_KEY = {"transports": "transport"}

SCOPE_44_85 = InstanceScope(
    frozenset(set(DATASET_CATALOG) - SCOPE_EXCLUDED_KEYS), ("44", "85")
)
FULL_SCOPE = InstanceScope(SCOPE_44_85.datasets, tuple(DEPARTEMENTS))


def _ids(classes):
    return [_new(cls).dataset_name for cls in classes]


def _staging_columns(cls, table):
    source = inspect.getsource(sys.modules[cls.__module__])
    ddl = re.search(
        rf"TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{re.escape(table)}\s*\((.*?)\n\s*\)",
        source,
        re.S,
    )
    assert ddl, f"{table}: no CREATE TABLE in {cls.__module__}"
    return {line.split()[0] for line in ddl.group(1).split(",") if line.split()}


def _floor(pipeline):
    # Flat pipelines that publish inside swap() check their floor there.
    return pipeline.min_staging_rows or pipeline.min_staging_rows_final


def test_fifteen_pipeline_classes_are_national():
    assert len(NATIONAL) == 15
    assert len(run_etl.ALL_PIPELINES) - len(DEPARTMENTAL) == 14


@pytest.mark.parametrize("cls", NATIONAL, ids=_ids(NATIONAL))
def test_scope_filter_targets_staging_ddl_columns(cls):
    declared = _new(cls).scope_filter_sql
    assert declared, "national pipeline without scope_filter_sql"
    for table, expression in declared.items():
        columns = _staging_columns(cls, table)
        insee = re.fullmatch(r"dc_departement_from_insee\((\w+)\)", expression)
        if expression == "code_departement":
            assert "code_departement" in columns
        elif insee:
            assert insee.group(1) in columns & COMMUNE_COLUMNS
        else:
            # Stops and stations carry coordinates only: containment in the
            # departement polygon (geo_departements), a code constant.
            assert expression.startswith("(SELECT g.code FROM geo_departements g")
            coordinates = re.findall(r"NULLIF\((\w+), ''\)", expression)
            assert len(coordinates) == 2
            assert set(coordinates) <= columns


@pytest.mark.parametrize("cls", NATIONAL, ids=_ids(NATIONAL))
def test_floor_follows_the_bound_scope(cls):
    national = _floor(_new(cls))
    assert national > 0
    pipeline = _new(cls)
    pipeline.bind_scope(SCOPE_44_85)
    assert pipeline.is_department_scope_limited
    scoped = _floor(pipeline)
    if national == 1:
        # permis only refuses an empty table: nothing to scale.
        assert scoped == 1
    else:
        assert scoped == pipeline.scoped_min_rows(national) < national
        assert all(pipeline.departement_min_rows(code) > 0 for code in ("44", "85"))
    pipeline.bind_scope(FULL_SCOPE)
    assert _floor(pipeline) == national


@pytest.mark.parametrize("cls", NATIONAL, ids=_ids(NATIONAL))
def test_data_quality_volume_floor_follows_the_scope(cls):
    dataset = _new(cls).dataset_name
    volume = PROFILES[PROFILE_KEY.get(dataset, dataset)].volume
    assert volume is not None
    if dataset == "permis":
        # Not a national file: the source is local to departement 93 (KI-16);
        # its floor stays that source's floor (CR-01, test_dq_conditional).
        assert volume.local_source and not volume.is_national
        return
    assert volume.is_national and not volume.local_source
    key = PROFILE_KEY.get(dataset, dataset)
    scoped = _compute_volume_floor(
        volume.full_france_min, volume.is_national, SCOPE_44_85, key
    )
    assert 0 < scoped < volume.full_france_min
    assert (
        _compute_volume_floor(
            volume.full_france_min, volume.is_national, FULL_SCOPE, key
        )
        == volume.full_france_min
    )
    # Per-departement volume check reads the published table like the import.
    pipeline = _new(cls)
    assert volume.departement_sql == pipeline.production_departement_sql
