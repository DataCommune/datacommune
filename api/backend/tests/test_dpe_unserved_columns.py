"""Owner decision C3 (plan 04-15): two DPE identifiers stored, never served.

``numero_dpe_immeuble_associe`` and ``numero_immatriculation_copropriete`` are
stored by the DPE import before the first archived import (P-ARCHIVE-DPE) and
must stay out of every response without a later decision. Readers project
explicit columns, so a structural guard is enough here: the names appear in
no module outside the DPE import package, in no public contract and in no
OpenAPI schema. The served-response sentinel lives in
``tests/integration/test_dpe_document_public_contract.py``.
"""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parents[1]
UNSERVED = ("numero_dpe_immeuble_associe", "numero_immatriculation_copropriete")
# The only application code allowed to name them: the DPE import itself.
IMPORT_PACKAGE = BACKEND / "app" / "etl_dpe"


def test_unserved_dpe_columns_are_requested_by_the_import():
    from app.etl_dpe import config

    for name in UNSERVED:
        assert config.COLUMN_MAPPING[name] == name


def test_no_application_module_outside_the_import_names_them():
    offenders = [
        f"{path.relative_to(BACKEND)}: {name}"
        for path in sorted((BACKEND / "app").rglob("*.py"))
        if not path.is_relative_to(IMPORT_PACKAGE)
        for name in UNSERVED
        if name in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_no_public_contract_or_openapi_schema_names_them():
    from app.main import app

    documents = {"openapi (app)": json.dumps(app.openapi())}
    for path in sorted((REPO / "contracts").rglob("*.json")):
        documents[str(path.relative_to(REPO))] = path.read_text(encoding="utf-8")
    assert len(documents) > 1
    offenders = [
        f"{where}: {name}"
        for where, text in documents.items()
        for name in UNSERVED
        if name in text
    ]
    assert offenders == []
