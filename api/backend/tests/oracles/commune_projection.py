"""Closed Commune projection of the independently checked L08 source oracle.

Only stdlib and the pure source oracle are imported. HTTP availability and
branch absence are distinguished from a returned, incorrect certified value.
"""

from tests.oracles import communal_sources as source


class ProjectionMismatchError(AssertionError):
    def __init__(self, kind, path, expected, actual):
        self.evidence = {
            "kind": kind,
            "path": path,
            "expected": expected,
            "actual": actual,
        }
        super().__init__(str(self.evidence))


def expected_cells(case):
    cells = {
        field: case["fields"][original]["value"]
        for field, original in case["commune_fields"].items()
    }
    if nested := case.get("commune_object"):
        cells.update(
            {
                f"{nested}.{key}": value
                for key, value in source.expected_row(case).items()
            }
        )
    return cells


def assert_response(case, status, payload):
    """Check identity separately from the original 132 L08 certified cells."""
    cells = expected_cells(case)
    if not cells:
        raise ValueError("No certified Commune projection for " + case["id"])
    if status != 200:
        raise ProjectionMismatchError(
            "publication_unavailable" if status == 503 else "http_error",
            "$status",
            200,
            status,
        )
    code = next(iter(case["key"].values()))
    records = payload["donnees"]
    if not records:
        raise ProjectionMismatchError("commune_absent", "donnees", code, [])
    matching = [record for record in records if record.get("code_insee") == code]
    if len(matching) != 1:
        raise ProjectionMismatchError(
            "identity_mismatch",
            "code_insee",
            code,
            [r.get("code_insee") for r in records],
        )
    row = matching[0]
    for path, expected in cells.items():
        value = row
        for part in path.split("."):
            if value is None:
                raise ProjectionMismatchError("branch_absent", path, expected, None)
            if part not in value:
                raise ProjectionMismatchError(
                    "field_absent", path, expected, "<missing>"
                )
            value = value[part]
        numeric = type(expected) in (int, float) and type(value) in (int, float)
        if not (type(expected) is type(value) or numeric) or value != expected:
            raise ProjectionMismatchError("value_mismatch", path, expected, value)
    return len(cells)
