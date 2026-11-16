"""Counterexamples for the pure, source-derived Commune projection oracle."""

import ast
import copy
from pathlib import Path

import pytest

from tests.oracles import communal_sources as source
from tests.oracles import commune_projection as oracle

CASES = [c for c in source.load_manifest()["cases"] if oracle.expected_cells(c)]
CELLS = [(c, p, v) for c in CASES for p, v in oracle.expected_cells(c).items()]


def payload(case):
    row = {"code_insee": next(iter(case["key"].values()))}
    for path, value in oracle.expected_cells(case).items():
        if "." in path:
            parent, field = path.split(".")
            row.setdefault(parent, {})[field] = value
        else:
            row[path] = value
    return {"donnees": [row]}


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_original_projection_positive(case):
    assert oracle.assert_response(case, 200, payload(case)) == len(
        oracle.expected_cells(case)
    )


@pytest.mark.parametrize(
    "case,path,value", CELLS, ids=[c["id"] + ":" + p for c, p, _ in CELLS]
)
def test_every_certified_cell_rejects_a_wrong_value(case, path, value):
    response = copy.deepcopy(payload(case))
    row = response["donnees"][0]
    if "." in path:
        parent, path = path.split(".")
        row = row[parent]
    row[path] = 0 if value is None else None
    with pytest.raises(oracle.ProjectionMismatchError) as error:
        oracle.assert_response(case, 200, response)
    assert error.value.evidence["kind"] == "value_mismatch"


def test_counts_and_unclaimed_habitat_are_explicit():
    assert len(CASES) == 18
    assert len(CELLS) == 132
    unclaimed = [
        c for c in source.load_manifest()["cases"] if not oracle.expected_cells(c)
    ]
    assert len(unclaimed) == 4
    for case in unclaimed:
        with pytest.raises(ValueError, match="No certified"):
            oracle.assert_response(case, 200, {"donnees": []})


@pytest.mark.parametrize(
    "kind",
    [
        "publication_unavailable",
        "commune_absent",
        "identity_mismatch",
        "branch_absent",
        "field_absent",
    ],
)
def test_availability_and_structure_are_distinct(kind):
    case = next(c for c in CASES if c["id"] == "loyers:05066:appartement")
    response = payload(case)
    status = 200
    if kind == "publication_unavailable":
        status, response = 503, {"detail": "unavailable"}
    elif kind == "commune_absent":
        response["donnees"] = []
    elif kind == "identity_mismatch":
        response["donnees"][0]["code_insee"] = "99990"
    elif kind == "branch_absent":
        response["donnees"][0][case["commune_object"]] = None
    else:
        del response["donnees"][0][case["commune_object"]]["loyer_m2"]
    with pytest.raises(oracle.ProjectionMismatchError) as error:
        oracle.assert_response(case, status, response)
    assert error.value.evidence["kind"] == kind


def test_oracles_do_not_import_application():
    for module in (source, oracle):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("app")
            elif isinstance(node, ast.Import):
                assert all(not alias.name.startswith("app") for alias in node.names)
