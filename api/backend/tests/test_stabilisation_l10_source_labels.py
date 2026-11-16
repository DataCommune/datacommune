"""Independent L10 source labels; no application imports or HTTP expectations."""

from copy import deepcopy

import pytest

from tests.oracles import communal_sources as oracle


def source_cases_with_labels():
    """Extend verified frozen cases only with explicitly present source columns."""
    manifest = oracle.load_manifest()
    cases = deepcopy(manifest["cases"])
    lovac = {
        row["CODGEO_26"]: row
        for row in oracle.rows(
            oracle.FIXTURES / "cor08_lovac/official-communes26.csv", "latin-1"
        )
    }
    for case in cases:
        if case["dataset"] == "loyers":
            kind = case["key"]["type_habitat"]
            path = oracle.FIXTURES / "loyers" / (kind + ".csv")
            rows = {row["INSEE_C"]: row for row in oracle.rows(path, "latin-1")}
            row = rows[case["key"]["code_insee"]]
            columns = {"nom_commune": "LIBGEO"}
            source = "loyers:" + kind + ":extracted"
        elif case["dataset"] == "vacance":
            row = lovac[case["key"]["code_commune"]]
            columns = {
                "nom_commune": "LIBGEO_26",
                "code_departement": "DEP",
                "nom_departement": "LIB_DEP",
                "nom_region": "LIB_REG",
            }
            source = "lovac"
        else:
            continue
        for field, column in columns.items():
            assert row[column]
            case["fields"][field] = {
                "value": row[column],
                "columns": [source + ":" + column],
                "unit": "source label"
                if field != "code_departement"
                else "source code",
                "method": "exact frozen source text, latin-1 decoding",
            }
    return cases


ADDED = [
    (case, field)
    for old, case in zip(
        oracle.load_manifest()["cases"], source_cases_with_labels(), strict=True
    )
    for field in case["fields"]
    if field not in old["fields"]
]


@pytest.mark.parametrize("case,field", ADDED, ids=[c["id"] + ":" + f for c, f in ADDED])
def test_source_label_mutation_previously_unchecked_is_rejected(case, field):
    original = next(c for c in oracle.load_manifest()["cases"] if c["id"] == case["id"])
    actual = oracle.expected_row(case)
    oracle.assert_row(case, actual)
    actual[field] = "99" if field == "code_departement" else "wrong"
    oracle.assert_row(original, actual)
    with pytest.raises(AssertionError, match=field):
        oracle.assert_row(case, actual)


def test_frozen_label_extension_has_exactly_28_new_cells():
    assert len(ADDED) == 28
    assert sum(len(oracle.expected_row(c)) for c in source_cases_with_labels()) == 316
