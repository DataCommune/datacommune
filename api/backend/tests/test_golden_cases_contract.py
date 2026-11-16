"""Offline golden-case contract tests (Phase 43, Plan 07).

Promotes the Plan-02 scaffold into the real authority: the validation rules
are now driven by the versioned, minimized schema in
``contracts/golden-cases/v1/schema.json`` and the security guards
(SELECT-only enforcement, PII/secret redaction, artifact leak audit) live in
the opt-in CLI ``scripts/verify_golden_cases.py`` -- never collected by pytest
(it is under ``scripts/``, not ``tests/``, and exposes no ``test_*``).

Everything here runs offline against a 100% synthetic fixture
(``tests/fixtures/golden_manifest_valid.json``). No DB, no network, no real
golden case selected -- that is a later plan's blocking human checkpoint
(D-10). This file intentionally carries no ``live`` in any test name so the
plan's collect-only negative gate stays green.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, ClassVar

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from scripts.select_golden_cases import (
    COMMUNE_FIELDS,
    _build_frontend_case,
    _departement_coverage_mask,
    _derive_category,
    _probe_entry,
    _project_commune_payload,
    _render_frontend_fixture,
    _select_minimal_entries,
    build_candidate_entry,
    extract_private_input,
)
from scripts.select_golden_cases import (
    main as select_main,
)
from scripts.verify_golden_cases import (
    _DPE_DOCUMENT_SOURCE_KEYS,
    _DPE_INVENTORY_ITEM_SOURCE_KEYS,
    _LIFTED_REPORT_KEYS,
    ALLOWED_SQL,
    COMMUNE_SQL,
    COPRO_ROW_SQL,
    DPE_APPARTEMENTS_PAR_BAN_SQL,
    DPE_APPARTEMENTS_SPATIAL_SQL,
    DPE_COUNT_MAX,
    DPE_DOCUMENTARY_SOURCE_MATCH_SQL,
    DPE_INVENTORY_PRESENCES,
    DPE_LIFECYCLE_SOURCE_MATCH_SQL,
    DVF_APPARTEMENTS_SQL,
    FORBIDDEN_KEYS,
    PARCEL_SQL,
    GoldenSecurityError,
    _api_payload,
    _assert_api_identity,
    _compare_observation,
    _dataset_state,
    _db_observation,
    _describe_observation,
    _dpe_rattaches,
    _observation_failure_reason,
    _open_read_only_connection,
    _rattachement_metrics,
    _verify_case,
    _write_report,
    assert_select_only,
    audit_artifact_text,
    audit_manifest_keys,
    build_manifest_patch_entry,
    dpe_attribution_facts,
    dpe_carrier_is_satisfied,
    dpe_inventory_presence,
    load_schema,
    main,
    observation_is_satisfied,
    redact_entry,
    validate_case_entry,
    validate_dpe_carrier_coverage,
    validate_manifest,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_manifest_valid.json"
_REAL_MANIFEST = (
    Path(__file__).parents[3] / "contracts" / "golden-cases" / "v1" / "manifest.json"
)
# Transferred verbatim from the origin repository at 3394395a
# (its internal tracking, 51-09-SUMMARY.md,
# sha256 9e6657614ec261e3814e8ba3c11bffd31e46e70c8642b49297c4bce545e54dfd).
# A file READ BY A TEST is a fixture of this repository, never a reference into
# the internal tracking: that directory is planning state and leaves the repo before any
# public remote, which would turn this test red on that day.
_PHASE_51_SUMMARY = Path(__file__).parent / "fixtures" / "51-09-SUMMARY.md"


def _real_schema() -> dict[str, Any]:
    """schema.json verbatim, corpus pins included."""
    return load_schema()


def _schema() -> dict[str, Any]:
    """Real schema with the corpus pins neutralized.

    Everything below operates on SYNTHETIC pools and manifests: opaque ids
    chosen for the mechanics under test, with no reason to match the production
    corpus. Handing them the real ``pinned_cases`` couples every mechanics test
    to the composition of that corpus -- a coupling that held only by
    coincidence, the eight pins added in Plan 48-16 happening to equal the ids
    of the synthetic fixture. Plan 48-17 pinned a ninth case absent from these
    pools and eighteen tests failed at once, none of them about pinning.

    The pin mechanism keeps its own tests, which set ``pinned_cases``
    explicitly through ``_schema_with_pinned``; the real corpus keeps its own,
    ``test_real_manifest_satisfies_its_pinned_cases``. Neither guarantee is
    weakened here -- they are simply stated where they belong.

    Phase 57 Plan 03 adds the same neutralization for
    ``required_departement_coverage``: it is real-corpus-specific geography
    (44/85), and the synthetic address helpers below (``_observed_candidate``,
    ``_ordinary_address_pool``) deliberately build every address in a single
    fixed departement for simplicity -- coupling them to it would fail dozens
    of unrelated mechanics tests for the wrong reason. Its own tests set it
    explicitly through ``_schema_with_departement_coverage``.
    """
    schema = _real_schema()
    schema["pinned_cases"] = []
    schema["final_dpe_gate"] = False
    schema.pop("required_departement_coverage", None)
    return schema


def _fixture() -> dict[str, Any]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _valid_case(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "case_id": "golden-commune-001",
        "type": "commune",
        "departement": "44",
        "code_insee": "44991",
        "category": "complete",
        "frozen_at": "2026-07-15",
        "source_millesimes": {"dvf": "2024", "dpe": "2024"},
        "invariants": {
            "db": {"has_commune_row": True},
            "api": {"dvf_state": "value"},
            "mapper": {"densite_unit": "density"},
        },
        "private_ref": "golden-commune-001",
    }
    base.update(overrides)
    return base


# --- schema + fixture ---------------------------------------------------------


def test_observation_failure_reason_names_the_class_without_its_message() -> None:
    """R3-01 companion: a generic failure must be actionable AND redacted.

    The negative control is the assertion below: building the reason with the
    exception itself (``f"...: {exc}"``) would put the payload in the redacted
    report uploaded as a workflow artifact.
    """
    payload = "SENSITIVE-PAYLOAD-MUST-NOT-LEAK"
    reason = _observation_failure_reason(RuntimeError(payload))

    assert reason == "live_observation_failed: RuntimeError"
    assert payload not in reason


def test_observation_failure_reason_keeps_the_http_status_code() -> None:
    """An HTTP status is an integer: it discriminates auth from routing errors
    without exposing the URL that produced it."""

    class _Response:
        status_code = 403

    class _HttpError(Exception):
        response = _Response()

    assert _observation_failure_reason(_HttpError("https://host/secret")) == (
        "live_observation_failed: _HttpError 403"
    )


def test_observation_failure_reason_names_the_route_without_its_query() -> None:
    """The path names the failing route; the query string carries the address.

    The negative control is the last assertion: the replayed private address
    travels in the query string, so appending the full URL would leak it into
    the redacted report uploaded as a workflow artifact.
    """

    class _Url:
        path = "/v1/commune"
        query = b"q=SENSITIVE-ADDRESS-MUST-NOT-LEAK"

    class _Request:
        url = _Url()

    class _HttpError(Exception):
        request = _Request()

    reason = _observation_failure_reason(_HttpError("boom"))

    assert reason == "live_observation_failed: _HttpError /v1/commune"
    assert "SENSITIVE-ADDRESS-MUST-NOT-LEAK" not in reason


def test_compare_observation_names_a_bounded_observed_value() -> None:
    """A bare "mismatch" cannot be acted on without guessing.

    `db` invariants are booleans and `api` invariants are coarse dataset states:
    both are closed sets. The negative control is the last assertion -- a value
    outside those sets stays unnamed, so a future free-form invariant cannot
    start leaking into the report uploaded as a workflow artifact.
    """
    expected = {
        "db": {"parcelle_present": True},
        "api": {"dpe_state": "value"},
        "mapper": {},
    }
    actual = {
        "db": {"parcelle_present": False},
        "api": {"dpe_state": "out_of_coverage"},
    }

    assert _compare_observation(expected, actual) == [
        "db.parcelle_present: mismatch (observed false)",
        "api.dpe_state: mismatch (observed out_of_coverage)",
    ]

    unbounded = {"db": {}, "api": {"dpe_state": "value"}, "mapper": {}}
    observed_free_form = {"api": {"dpe_state": "12 rue SENSITIVE"}}
    reasons = _compare_observation(unbounded, observed_free_form)
    assert reasons == ["api.dpe_state: mismatch"]


def test_schema_file_is_loadable_and_versioned() -> None:
    schema = _schema()
    assert schema["schema_version"] == "1"
    assert set(schema["allowed_categories"]) == {
        "complete",
        "partial",
        "no_dvf",
        "no_dpe",
        "dataset_out_of_coverage",
    }
    assert set(schema["forbidden_keys"]) == set(FORBIDDEN_KEYS)
    # 43-09 amendment: 'partial' is allowed but unobservable on the 44/85 live
    # surface, so corpus coverage only requires the four observable categories.
    assert set(schema["required_categories"]) == {
        "complete",
        "no_dvf",
        "no_dpe",
        "dataset_out_of_coverage",
    }


def test_valid_synthetic_manifest_has_no_errors() -> None:
    assert validate_manifest(_fixture(), _schema()) == []


def test_fixture_has_three_to_five_communes_and_five_to_ten_addresses() -> None:
    cases = _fixture()["cases"]
    communes = [c for c in cases if c["type"] == "commune"]
    addresses = [c for c in cases if c["type"] == "address"]
    assert 3 <= len(communes) <= 5
    assert 5 <= len(addresses) <= 10


def test_fixture_covers_all_five_categories() -> None:
    observed = {c["category"] for c in _fixture()["cases"]}
    assert observed == set(_schema()["allowed_categories"])


# --- per-entry validation -----------------------------------------------------


def test_valid_case_has_no_errors() -> None:
    assert validate_case_entry(_valid_case(), _schema()) == []


def test_required_values_cannot_be_null_or_malformed() -> None:
    entry = _valid_case(
        case_id=None,
        type=None,
        departement=None,
        code_insee="not-an-insee-code",
        category=None,
        frozen_at=None,
        source_millesimes=None,
        invariants=None,
        private_ref=None,
    )
    assert validate_case_entry(entry, _schema())


def test_private_ref_and_case_type_must_match_case_id() -> None:
    errors = validate_case_entry(
        _valid_case(type="address", private_ref="golden-address-001"), _schema()
    )
    assert any("type segment" in error for error in errors)
    assert any("private_ref" in error for error in errors)


def test_missing_required_key_is_rejected() -> None:
    entry = _valid_case()
    del entry["frozen_at"]
    errors = validate_case_entry(entry, _schema())
    assert any("missing required" in e for e in errors)


def test_raw_address_key_is_rejected() -> None:
    errors = validate_case_entry(_valid_case(adresse="12 rue de la Paix"), _schema())
    assert any("forbidden" in e for e in errors)


def test_coordinate_key_is_rejected() -> None:
    errors = validate_case_entry(_valid_case(latitude=47.21), _schema())
    assert any("forbidden" in e for e in errors)


def test_category_outside_closed_set_is_rejected() -> None:
    errors = validate_case_entry(_valid_case(category="invented"), _schema())
    assert any("category" in e for e in errors)


def test_departement_outside_44_85_is_rejected() -> None:
    errors = validate_case_entry(
        _valid_case(departement="75", code_insee="75056"), _schema()
    )
    assert any("departement" in e for e in errors)


def test_future_frozen_at_is_rejected() -> None:
    future = (date.today() + timedelta(days=365)).isoformat()
    errors = validate_case_entry(_valid_case(frozen_at=future), _schema())
    assert any("future" in e.lower() for e in errors)


# --- manifest-level validation ------------------------------------------------


def test_duplicate_case_id_is_rejected() -> None:
    manifest = _fixture()
    manifest["cases"].append(dict(manifest["cases"][0]))
    errors = validate_manifest(manifest, _schema())
    assert any("duplicate" in e for e in errors)


def test_too_few_communes_is_rejected() -> None:
    manifest = _fixture()
    manifest["cases"] = [c for c in manifest["cases"] if c["type"] != "commune"]
    manifest["cases"].append(_valid_case(case_id="c1", private_ref="c1"))
    errors = validate_manifest(manifest, _schema())
    assert any("commune" in e for e in errors)


def test_manifest_missing_a_category_is_rejected() -> None:
    manifest = _fixture()
    manifest["cases"] = [c for c in manifest["cases"] if c["category"] != "no_dpe"]
    errors = validate_manifest(manifest, _schema())
    assert any("categor" in e for e in errors)


def test_manifest_rejects_an_extra_null_case() -> None:
    manifest = _fixture()
    manifest["cases"].append({key: None for key in _schema()["required_keys"]})
    assert validate_manifest(manifest, _schema())


# --- SELECT-only enforcement (T-43-13) ----------------------------------------


def test_assert_select_only_accepts_read_query() -> None:
    assert_select_only(COMMUNE_SQL)


def test_assert_select_only_rejects_write() -> None:
    for sql in (
        "UPDATE demographie SET population = 0",
        "DELETE FROM mutations",
        "DROP TABLE geo_communes",
        "INSERT INTO demographie VALUES (1)",
        "SELECT 1; DROP TABLE t",
        "SELECT 1 INTO golden_probe",
        "SELECT pg_notify('golden', 'payload')",
        (
            "SELECT dblink_exec('foreign', "
            "convert_from(decode('REVMRVRFIEZST00gdGFyZ2V0','base64'),'UTF8'))"
        ),
    ):
        try:
            assert_select_only(sql)
        except GoldenSecurityError:
            continue
        raise AssertionError(f"expected rejection for: {sql!r}")


def test_connection_enforces_transaction_read_only(monkeypatch) -> None:
    class Result:
        @staticmethod
        def fetchone() -> dict[str, str]:
            return {"state": "on"}

    class Connection:
        read_only = False
        closed = False

        def execute(self, sql: str) -> Result:
            assert self.read_only is True
            return Result()

        def close(self) -> None:
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(
        "scripts.verify_golden_cases.psycopg.connect",
        lambda *_args, **_kwargs: connection,
    )
    assert _open_read_only_connection("postgresql://redacted") is connection
    assert connection.read_only is True


def test_connection_rejects_server_without_read_only_state(monkeypatch) -> None:
    class Result:
        @staticmethod
        def fetchone() -> dict[str, str]:
            return {"state": "off"}

    class Connection:
        read_only = False
        closed = False

        def execute(self, _sql: str) -> Result:
            return Result()

        def close(self) -> None:
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(
        "scripts.verify_golden_cases.psycopg.connect",
        lambda *_args, **_kwargs: connection,
    )
    with pytest.raises(GoldenSecurityError, match="not read-only"):
        _open_read_only_connection("postgresql://redacted")
    assert connection.closed is True


def test_api_replay_uses_get_only() -> None:
    class Response:
        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, list[dict[str, str]]]:
            return {"donnees": [{"code_insee": "44991"}]}

    class Client:
        calls: ClassVar[list[tuple[str, dict[str, str]]]] = []

        def get(self, path: str, params: dict[str, str]) -> Response:
            self.calls.append((path, params))
            return Response()

    client = Client()
    payload = _api_payload(client, _valid_case(), {})
    assert payload == {"code_insee": "44991"}
    assert client.calls == [("/v1/commune", {"code_insee": "44991"})]


def test_api_replay_rejects_identity_mismatch() -> None:
    with pytest.raises(GoldenSecurityError, match="identity mismatch"):
        _assert_api_identity(_valid_case(), {}, {"code_insee": "85991"})


def test_address_api_identity_is_bound_to_private_parcel() -> None:
    entry = _valid_case(type="address")
    with pytest.raises(GoldenSecurityError, match="parcel identity mismatch"):
        _assert_api_identity(
            entry,
            {"id_parcelle": "private-parcel"},
            {"id_parcelle": "different-parcel"},
        )


def test_address_api_identity_accepts_cadastre_dataset_parcel() -> None:
    entry = _valid_case(type="address")
    _assert_api_identity(
        entry,
        {"id_parcelle": "private-parcel"},
        {
            "id_parcelle": None,
            "datasets": {"cadastre": {"id_parcelle": "private-parcel"}},
        },
    )


def test_address_api_identity_rejects_cadastre_dataset_mismatch() -> None:
    entry = _valid_case(type="address")
    with pytest.raises(GoldenSecurityError, match="parcel identity mismatch"):
        _assert_api_identity(
            entry,
            {"id_parcelle": "private-parcel"},
            {
                "id_parcelle": None,
                "datasets": {"cadastre": {"id_parcelle": "different-parcel"}},
            },
        )


def test_address_api_replay_fails_closed_before_private_query_is_sent() -> None:
    class Client:
        @staticmethod
        def get(*_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("private query must not be sent")

    entry = _valid_case(
        case_id="golden-address-001",
        private_ref="golden-address-001",
        type="address",
    )
    with pytest.raises(GoldenSecurityError, match="redacts private q logs"):
        _api_payload(Client(), entry, {"query": "synthetic-private-value"})  # type: ignore[arg-type]


def test_address_api_replay_uses_get_after_redaction_confirmation() -> None:
    class Response:
        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, list[dict[str, str]]]:
            return {"donnees": [{"id_parcelle": "private-parcel"}]}

    class Client:
        calls: ClassVar[list[tuple[str, dict[str, str]]]] = []

        def get(self, path: str, params: dict[str, str]) -> Response:
            self.calls.append((path, params))
            return Response()

    entry = _valid_case(
        case_id="golden-address-001",
        private_ref="golden-address-001",
        type="address",
    )
    private = {"query": "synthetic-private-value", "id_parcelle": "private-parcel"}
    payload = _api_payload(
        Client(),
        entry,
        private,
        resolve_logging_redacted=True,  # type: ignore[arg-type]
    )
    assert payload == {"id_parcelle": "private-parcel"}
    assert Client.calls == [
        (
            "/v1/resolve",
            {
                "q": "synthetic-private-value",
                "type": "adresse",
                "datasets": "cadastre,dvf,dpe,risques,urbanisme",
            },
        ),
        (
            "/v1/resolve",
            {
                "q": "synthetic-private-value",
                "type": "adresse",
                "datasets": "commune",
            },
        ),
    ]


# --- Phase 48: multi-logement db invariants (D1/D2/D3) ------------------------


def test_schema_allows_multi_logement_db_invariants() -> None:
    schema = _schema()
    assert set(schema["allowed_db_invariants"]) == {
        "has_commune_row",
        "superficie_present",
        "parcelle_present",
        "copro_row_present",
        "dpe_appartements_multiples",
        "dvf_appartements_present",
        "dpe_rattachement_ok",
    }


def test_existing_cases_remain_valid() -> None:
    assert validate_manifest(_fixture(), _schema()) == []


def test_case_with_new_db_invariant_is_valid() -> None:
    entry = _valid_case(
        case_id="golden-address-001",
        private_ref="golden-address-001",
        type="address",
        code_insee="85991",
        departement="85",
        invariants={
            "db": {"parcelle_present": True, "copro_row_present": True},
            "api": {"dvf_state": "value"},
            "mapper": {"prix_m2_unit": "euro_per_m2"},
        },
    )
    assert validate_case_entry(entry, _schema()) == []


def test_unknown_db_invariant_still_rejected() -> None:
    entry = _valid_case(
        invariants={
            "db": {"has_commune_row": True, "not_a_real_invariant": True},
            "api": {"dvf_state": "value"},
            "mapper": {"densite_unit": "density"},
        }
    )
    errors = validate_case_entry(entry, _schema())
    assert any("invariants.db" in e for e in errors)


def test_new_sql_is_select_only() -> None:
    for sql in (
        COPRO_ROW_SQL,
        DPE_APPARTEMENTS_PAR_BAN_SQL,
        DPE_APPARTEMENTS_SPATIAL_SQL,
        DVF_APPARTEMENTS_SQL,
        DPE_DOCUMENTARY_SOURCE_MATCH_SQL,
    ):
        assert_select_only(sql)
        assert sql in ALLOWED_SQL
    neighbour = (
        "SELECT * FROM copropriete WHERE id_parcelle_1 = %(id_parcelle)s AND 1=1"
    )
    with pytest.raises(GoldenSecurityError):
        assert_select_only(neighbour)


def test_dpe_ban_sql_is_an_exact_key_join() -> None:
    # R3-01: the primary path is a pure equality on the BAN key -- no
    # geometry, no text matching, mirroring dpe_appartements_par_ban.
    assert "identifiant_ban = %(ban_id)s" in DPE_APPARTEMENTS_PAR_BAN_SQL
    for forbidden in ("ST_", "LIKE", "ILIKE"):
        assert forbidden not in DPE_APPARTEMENTS_PAR_BAN_SQL


def test_dpe_spatial_sql_mirrors_the_bounded_fallback() -> None:
    # R3-01: the fallback is the 12 m ST_DWithin + snap-margin non-ambiguity
    # rule of dpe_appartements_par_parcelle -- never the old strict
    # ST_Intersects (the L1 bug) and never an unbounded radius.
    assert "ST_DWithin" in DPE_APPARTEMENTS_SPATIAL_SQL
    assert "%(rayon_m)s" in DPE_APPARTEMENTS_SPATIAL_SQL
    assert "%(marge_m)s" in DPE_APPARTEMENTS_SPATIAL_SQL
    assert "distance_m = 0" in DPE_APPARTEMENTS_SPATIAL_SQL
    for forbidden in ("ST_Intersects", "ST_MakePoint", "ST_Buffer", "500"):
        assert forbidden not in DPE_APPARTEMENTS_SPATIAL_SQL


def test_dpe_observation_sql_filters_appartement() -> None:
    assert "type_batiment = 'appartement'" in DPE_APPARTEMENTS_PAR_BAN_SQL
    assert "type_batiment = 'appartement'" in DPE_APPARTEMENTS_SPATIAL_SQL


def test_dvf_observation_sql_applies_surface_threshold() -> None:
    assert "type_local = 'Appartement'" in DVF_APPARTEMENTS_SQL
    assert "surface_reelle_bati >=" in DVF_APPARTEMENTS_SQL
    assert "%(surface_min)s" in DVF_APPARTEMENTS_SQL


def test_copro_observation_sql_uses_id_parcelle_1() -> None:
    assert "id_parcelle_1" in COPRO_ROW_SQL
    assert "LIKE" not in COPRO_ROW_SQL
    assert "ILIKE" not in COPRO_ROW_SQL


def test_observation_binds_values() -> None:
    sentinel = "85047000AB0042-TEST-SENTINEL"
    for sql in (COPRO_ROW_SQL, DPE_APPARTEMENTS_SPATIAL_SQL, DVF_APPARTEMENTS_SQL):
        assert sentinel not in sql
        assert "%(id_parcelle)s" in sql
    for sql in (DPE_APPARTEMENTS_PAR_BAN_SQL, DPE_APPARTEMENTS_SPATIAL_SQL):
        assert "%(ban_id)s" in sql


def _normalize_test_sql(sql: str) -> str:
    return " ".join(sql.split())


_SPATIAL_DEFAULT = {
    "spatial_total": 1,
    "spatial_sans_cle": 1,
    "spatial_cle_exacte": 0,
    "spatial_cle_orpheline": 0,
}


class _FakeMultiLogementConnection:
    """Route SELECT-only queries by normalized text; no DB, no network."""

    def __init__(
        self,
        ban_count: int = 3,
        spatial: dict[str, int] | None = None,
        documentary_match: dict[str, bool] | None = None,
        lifecycle_match: bool = True,
    ) -> None:
        self._ban_count = ban_count
        self._spatial = dict(spatial) if spatial is not None else dict(_SPATIAL_DEFAULT)
        self._documentary_match = documentary_match or {
            "inventory_aggregate_equal": True,
            "inventory_item_equal": True,
            "document_equal": True,
        }
        self.documentary_params: dict[str, Any] | None = None
        self.lifecycle_match = lifecycle_match

    def execute(self, sql: str, params: Any = None) -> Any:
        normalized = _normalize_test_sql(sql)
        if normalized == _normalize_test_sql(COMMUNE_SQL):
            row = {
                "code_insee": "44991",
                "superficie": 12.3,
                "densite": None,
                "annee_demographie": None,
                "annee_revenus": None,
                "annee_fiscalite": None,
                "annee_loyers": None,
            }
        elif normalized == _normalize_test_sql(PARCEL_SQL):
            row = {"id_parcelle": params[0]}
        elif normalized == _normalize_test_sql(COPRO_ROW_SQL):
            row = {"copro_row_present": True}
        elif normalized == _normalize_test_sql(DPE_APPARTEMENTS_PAR_BAN_SQL):
            row = {"dpe_par_ban_count": self._ban_count}
        elif normalized == _normalize_test_sql(DPE_APPARTEMENTS_SPATIAL_SQL):
            row = dict(self._spatial)
        elif normalized == _normalize_test_sql(DVF_APPARTEMENTS_SQL):
            row = {"dvf_appartement_present": True}
        elif normalized == _normalize_test_sql(DPE_DOCUMENTARY_SOURCE_MATCH_SQL):
            self.documentary_params = dict(params)
            row = dict(self._documentary_match)
        elif normalized == _normalize_test_sql(DPE_LIFECYCLE_SOURCE_MATCH_SQL):
            row = {"lifecycle_equal": self.lifecycle_match}
        else:
            raise AssertionError(f"unexpected SQL routed to fake connection: {sql!r}")
        return _FakeRow(row)


class _FakeRow:
    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    def fetchone(self) -> dict[str, Any]:
        return self._row


_ADDRESS_PRIVATE = {"id_parcelle": "85991000AB0001", "ban_id": "85991_0001_00001"}


def test_db_observation_reports_three_new_keys() -> None:
    entry = _valid_case(
        case_id="golden-address-001",
        private_ref="golden-address-001",
        type="address",
        code_insee="85991",
        departement="85",
    )
    observation, _, metrics = _db_observation(
        _FakeMultiLogementConnection(), entry, dict(_ADDRESS_PRIVATE)
    )
    assert set(observation) == {
        "has_commune_row",
        "superficie_present",
        "parcelle_present",
        "copro_row_present",
        "dpe_appartements_multiples",
        "dvf_appartements_present",
        "dpe_rattachement_ok",
    }
    assert all(isinstance(value, bool) for value in observation.values())
    assert set(metrics) == {
        "dpe_avec_cle_ban",
        "dpe_cle_ban_orpheline",
        "dpe_rattaches_par_secours_spatial",
        "dpe_rattaches",
        "dpe_attendus",
    }
    assert all(isinstance(value, int) for value in metrics.values())


# --- R3-01: BAN-key attachment measure (mirrors _charge_dpe) ------------------


def _address_entry() -> dict[str, Any]:
    return _valid_case(
        case_id="golden-address-001",
        private_ref="golden-address-001",
        type="address",
        code_insee="85991",
        departement="85",
    )


def test_db_observation_requires_ban_id_for_address() -> None:
    # Degraded private corpus (BAN key removed): fail closed, naming the
    # rattachement -- never a silent geometry-only fallback.
    with pytest.raises(GoldenSecurityError, match=r"ban_id.*rattachement"):
        _db_observation(
            _FakeMultiLogementConnection(),
            _address_entry(),
            {"id_parcelle": "85991000AB0001"},
        )


def test_dpe_rattaches_mirrors_charge_dpe_branches() -> None:
    # Key path attached rows: complement restricted to keyless rows; the
    # orphan-keyed rows are NOT attached (production behaviour, ticket T5).
    spatial = {
        "spatial_total": 7,
        "spatial_sans_cle": 1,
        "spatial_cle_exacte": 2,
        "spatial_cle_orpheline": 4,
    }
    assert _dpe_rattaches(10, spatial) == 11
    # Zero key rows: FULL spatial fallback, orphan-keyed rows included
    # (commit 6df68c3d) -- never the keyless subset alone.
    assert _dpe_rattaches(0, spatial) == 7


def test_dpe_multiples_uses_full_fallback_when_key_attaches_nothing() -> None:
    observation, _, _ = _db_observation(
        _FakeMultiLogementConnection(
            ban_count=0,
            spatial={
                "spatial_total": 2,
                "spatial_sans_cle": 0,
                "spatial_cle_exacte": 0,
                "spatial_cle_orpheline": 2,
            },
        ),
        _address_entry(),
        dict(_ADDRESS_PRIVATE),
    )
    assert observation["dpe_appartements_multiples"] is True


def test_dpe_multiples_excludes_orphans_when_key_attached_rows() -> None:
    observation, _, _ = _db_observation(
        _FakeMultiLogementConnection(
            ban_count=1,
            spatial={
                "spatial_total": 5,
                "spatial_sans_cle": 0,
                "spatial_cle_exacte": 1,
                "spatial_cle_orpheline": 4,
            },
        ),
        _address_entry(),
        dict(_ADDRESS_PRIVATE),
    )
    assert observation["dpe_appartements_multiples"] is False


class _UnusedClient:
    @staticmethod
    def get(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the API must not be called before the DB guard")


def test_verify_case_blocks_on_missing_private_ban_id() -> None:
    entry = _address_entry()
    result = _verify_case(
        _FakeMultiLogementConnection(),
        _UnusedClient(),  # type: ignore[arg-type]
        entry,
        {"golden-address-001": {"id_parcelle": "85991000AB0001"}},
        resolve_logging_redacted=True,
    )
    assert result["ok"] is False
    assert any("rattachement" in reason for reason in result["reasons"])
    assert any("ban_id" in reason for reason in result["reasons"])


def test_verify_case_nominal_with_private_ban_id() -> None:
    class Response:
        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, Any]:
            return {
                "donnees": [
                    {
                        "id_parcelle": "85991000AB0001",
                        "datasets": {"dvf": [{"prix_m2": 1}]},
                        "dataset_states": {
                            "dpe": {
                                "dataset": "dpe",
                                "presence": "value",
                                "absence_reason": None,
                                "raison": None,
                            }
                        },
                        "dpe_inventory": _inventory("present"),
                        # Phase 51: every address replay now reads the public
                        # decision. A payload without it fails closed -- proven
                        # by test_dpe_attribution_facts_reject_a_malformed_...
                        "dpe_attribution": {
                            "version": 2,
                            "status": "unassigned",
                            "reason": "dwelling_identity_unproven",
                            "diagnostic": None,
                            "candidates": [],
                            "building_context": {
                                "count": 4,
                                "distribution": {"D": 4},
                                "truncated": False,
                            },
                            "graph_state": "not_checked",
                        },
                    }
                ]
            }

    class Client:
        def get(self, path: str, params: dict[str, str]) -> Any:
            if path == "/v1/bien/dpe/inventaire":
                return _JsonResponse(
                    _route_inventory("present"), cache_control="no-store"
                )
            if path == "/v1/bien/dpe/document":
                return _JsonResponse(_route_document(), cache_control="no-store")
            if params.get("datasets") == "commune":
                return _JsonResponse(
                    {
                        "donnees": [
                            {
                                "id_parcelle": "85991000AB0001",
                                "datasets": {"commune": {}},
                            }
                        ]
                    }
                )
            return Response()

    entry = _address_entry()
    entry["invariants"] = {
        "db": {
            "has_commune_row": True,
            "parcelle_present": True,
            "copro_row_present": True,
            "dpe_appartements_multiples": True,
            "dvf_appartements_present": True,
            "dpe_rattachement_ok": True,
        },
        "api": {"dvf_state": "value"},
        "mapper": {"prix_m2_unit": "euro_per_m2"},
        "dpe": {
            "dpe_inventory_presence": "present",
            "dpe_attribution_status": "unassigned",
            "dpe_primary_diagnostic_present": False,
            "dpe_candidate_count": 0,
            "dpe_building_count": 4,
            "dpe_truncated": False,
            "dpe_nearby_diagnostic_present": False,
        },
    }
    result = _verify_case(
        _FakeMultiLogementConnection(),
        Client(),  # type: ignore[arg-type]
        entry,
        {"golden-address-001": {**_ADDRESS_PRIVATE, "query": "synthetic"}},
        resolve_logging_redacted=True,
    )
    assert result["reasons"] == []
    assert result["ok"] is True
    # R3-02: the redacted report carries the observed counters (integers only)
    # -- the 43-09 observation material for the future freeze decision.
    assert result["metrics"] == {
        "dpe_avec_cle_ban": 3,
        "dpe_cle_ban_orpheline": 0,
        "dpe_rattaches_par_secours_spatial": 1,
        "dpe_rattaches": 4,
        "dpe_attendus": 4,
    }
    assert audit_artifact_text(json.dumps(result), source="report") == []


def test_verify_case_names_the_attribution_status_on_a_red_case() -> None:
    """A red case must say WHICH attribution the API decided.

    `api.dpe_state: mismatch (observed not_reported)` names the divergence but
    not its cause: the Phase 51 policy declining to attribute a diagnostic on a
    multi-logement reads exactly like a regression. The status is one of five
    closed constants and decides between the two.

    T-51-26 stays intact -- and that is the second half of this test: a failed
    case still publishes NO carrier and NO manifest patch. `reasons` is a
    diagnostic channel; nothing in the freeze path reads it.
    """

    class Response:
        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, Any]:
            return {
                "donnees": [
                    {
                        "id_parcelle": "85991000AB0001",
                        "datasets": {"dvf": [{"prix_m2": 1}]},
                        "dataset_states": {
                            "dpe": {
                                "dataset": "dpe",
                                "presence": "absent",
                                "absence_reason": "out_of_coverage",
                                "raison": "Couverture indisponible pour ce point.",
                            }
                        },
                        "dpe_inventory": _inventory("present"),
                        "dpe_attribution": {
                            "version": 2,
                            "status": "unassigned",
                            "reason": "dwelling_identity_unproven",
                            "diagnostic": None,
                            "candidates": [],
                            "building_context": {
                                "count": 4,
                                "distribution": {"D": 4},
                                "truncated": False,
                            },
                            "graph_state": "not_checked",
                        },
                    }
                ]
            }

    class Client:
        def get(self, path: str, params: dict[str, str]) -> Any:
            if path == "/v1/bien/dpe/inventaire":
                return _JsonResponse(
                    _route_inventory("present"), cache_control="no-store"
                )
            if path == "/v1/bien/dpe/document":
                return _JsonResponse(_route_document(), cache_control="no-store")
            if params.get("datasets") == "commune":
                return _JsonResponse(
                    {
                        "donnees": [
                            {
                                "id_parcelle": "85991000AB0001",
                                "datasets": {"commune": {}},
                            }
                        ]
                    }
                )
            return Response()

    entry = _address_entry()
    entry["invariants"] = {
        "db": {},
        # `dpe` is absent from `datasets`, so the observed state is
        # out_of_coverage: the case goes red exactly like 003/005/007 did.
        "api": {"dpe_state": "value"},
        "mapper": {},
        "dpe": {
            "dpe_inventory_presence": "present",
            "dpe_attribution_status": "unassigned",
            "dpe_primary_diagnostic_present": False,
            "dpe_candidate_count": 0,
            "dpe_building_count": 4,
            "dpe_truncated": False,
            "dpe_nearby_diagnostic_present": False,
        },
    }
    result = _verify_case(
        _FakeMultiLogementConnection(),
        Client(),  # type: ignore[arg-type]
        entry,
        {"golden-address-001": {**_ADDRESS_PRIVATE, "query": "synthetic"}},
        resolve_logging_redacted=True,
    )

    assert result["ok"] is False
    assert result["reasons"] == [
        "api.dpe_state: mismatch (observed out_of_coverage)",
        "dpe_attribution: unassigned",
    ]
    # The neighbourhood boolean defaults to False when the exact absence is NOT
    # proven -- `building` is such a case. Publishing that default would report
    # "nothing nearby" for a probe that never ran, so it stays out.
    assert not any(reason.startswith("dpe_nearby") for reason in result["reasons"])
    assert "dpe_qualification" not in result
    assert "manifest_patch" not in result
    assert audit_artifact_text(json.dumps(result), source="report") == []


def test_verify_case_names_the_neighbourhood_probe_on_a_measured_absence() -> None:
    """On a MEASURED exact absence, a red case must say whether a DPE sits next door.

    That boolean is the whole difference between "the old frozen value was a
    neighbour's DPE and Phase 51 removed it" and "the row vanished from the
    base" -- i.e. between a manifest to re-freeze and a data problem to chase.
    It is already computed on every replay; it was only being discarded with
    the carrier.
    """

    class Response:
        def __init__(self, body: dict[str, Any]) -> None:
            self._body = body

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self._body

    class Client:
        def get(self, path: str, params: dict[str, Any]) -> Any:
            if path == "/v1/dpe":
                # One row within the probe radius: a diagnostic DOES sit nearby.
                return Response({"donnees": [{"etiquette_dpe": "D"}]})
            if path == "/v1/bien/dpe/inventaire":
                return _JsonResponse(
                    _route_inventory("absent"), cache_control="no-store"
                )
            if params.get("datasets") == "commune":
                return _JsonResponse(
                    {
                        "donnees": [
                            {
                                "id_parcelle": "85991000AB0001",
                                "datasets": {"commune": {}},
                            }
                        ]
                    }
                )
            return Response(
                {
                    "donnees": [
                        {
                            "id_parcelle": "85991000AB0001",
                            "latitude": 47.2,
                            "longitude": -1.55,
                            "datasets": {"dvf": [{"prix_m2": 1}]},
                            "dataset_states": {
                                "dpe": {
                                    "dataset": "dpe",
                                    "presence": "absent",
                                    "absence_reason": "out_of_coverage",
                                    "raison": (
                                        "Couverture indisponible pour ce point."
                                    ),
                                }
                            },
                            "dpe_inventory": _inventory("absent"),
                            "dpe_attribution": {
                                "version": 2,
                                "status": "absent",
                                "reason": "not_reported",
                                "diagnostic": None,
                                "candidates": [],
                                "building_context": None,
                                "graph_state": "not_checked",
                            },
                        }
                    ]
                }
            )

    entry = _address_entry()
    entry["invariants"] = {
        "db": {},
        "api": {"dpe_state": "value"},
        "mapper": {},
        "dpe": {
            "dpe_inventory_presence": "absent",
            "dpe_attribution_status": "absent",
            "dpe_primary_diagnostic_present": False,
            "dpe_candidate_count": 0,
            "dpe_building_count": 0,
            "dpe_truncated": False,
            "dpe_nearby_diagnostic_present": True,
        },
    }
    result = _verify_case(
        _FakeMultiLogementConnection(),
        Client(),  # type: ignore[arg-type]
        entry,
        {"golden-address-001": {**_ADDRESS_PRIVATE, "query": "synthetic"}},
        resolve_logging_redacted=True,
    )

    assert result["ok"] is False
    assert result["reasons"] == [
        "api.dpe_state: mismatch (observed out_of_coverage)",
        "dpe_attribution: absent",
        "dpe_nearby: true",
    ]
    assert "dpe_qualification" not in result
    assert "manifest_patch" not in result
    assert audit_artifact_text(json.dumps(result), source="report") == []


# --- R3-02: B4 metrics + booleanized attachment gate --------------------------


def test_rattachement_metrics_key_branch_flags_orphans() -> None:
    spatial = {
        "spatial_total": 4,
        "spatial_sans_cle": 1,
        "spatial_cle_exacte": 1,
        "spatial_cle_orpheline": 2,
    }
    metrics, ok = _rattachement_metrics(7, spatial)
    # attendus = 7 + (4 - 1) = 10; rattaches = 7 + 1 = 8 -> 0.8 < 0.90
    # and orphelines = 2/10 = 20 % > 5 %: both thresholds fail.
    assert metrics == {
        "dpe_avec_cle_ban": 7,
        "dpe_cle_ban_orpheline": 2,
        "dpe_rattaches_par_secours_spatial": 1,
        "dpe_rattaches": 8,
        "dpe_attendus": 10,
    }
    assert ok is False


def test_rattachement_metrics_gate_thresholds() -> None:
    # 9/10 attached with 0 orphan... impossible on the key branch (the gap IS
    # the orphans), so exercise the exact boundary: 19 attached / 20 expected
    # with 1 orphan = 0.95 ratio and 5 % orphans -- both at the limit, ok.
    boundary = {
        "spatial_total": 2,
        "spatial_sans_cle": 1,
        "spatial_cle_exacte": 0,
        "spatial_cle_orpheline": 1,
    }
    metrics, ok = _rattachement_metrics(18, boundary)
    assert metrics["dpe_rattaches"] == 19
    assert metrics["dpe_attendus"] == 20
    assert ok is True


def test_rattachement_metrics_full_fallback_and_empty_building() -> None:
    spatial = {
        "spatial_total": 3,
        "spatial_sans_cle": 0,
        "spatial_cle_exacte": 0,
        "spatial_cle_orpheline": 3,
    }
    metrics, ok = _rattachement_metrics(0, spatial)
    # Full fallback attaches everything, orphan keys included: nothing missed.
    assert metrics["dpe_rattaches"] == 3
    assert metrics["dpe_cle_ban_orpheline"] == 0
    assert metrics["dpe_rattaches_par_secours_spatial"] == 3
    assert ok is True

    empty = dict.fromkeys(spatial, 0)
    metrics, ok = _rattachement_metrics(0, empty)
    assert metrics["dpe_attendus"] == 0
    assert ok is True


def test_frozen_rattachement_invariant_turns_the_replay_red() -> None:
    # Where the R3-02 gate turns red: the MANUAL live replay
    # (verify-golden-live.yml), through this exact comparison, once a frozen
    # case carries dpe_rattachement_ok. Not ci-api.yml, not the offline audit.
    expected = {"db": {"dpe_rattachement_ok": True}, "api": {}}
    actual = {"db": {"dpe_rattachement_ok": False}, "api": {}}
    assert _compare_observation(expected, actual) == [
        "db.dpe_rattachement_ok: mismatch (observed false)"
    ]
    assert _compare_observation(expected, {"db": dict(expected["db"]), "api": {}}) == []


def test_rattachement_invariant_is_not_required_yet() -> None:
    # Amendment 43-09: a new required_observations entry must first be
    # OBSERVED on the 44/85 live surface. The invariant is allowed (so the
    # selector may freeze it after a green constat replay) but NOT required.
    schema = _real_schema()
    assert "dpe_rattachement_ok" in schema["allowed_db_invariants"]
    for requirement in schema["required_observations"]:
        for triple in _requirement_triples(requirement):
            assert triple["invariant"] != "dpe_rattachement_ok"


def test_db_observation_unchanged_for_commune() -> None:
    entry = _valid_case()  # type "commune" by default
    observation, _, metrics = _db_observation(_FakeMultiLogementConnection(), entry, {})
    assert set(observation) == {"has_commune_row", "superficie_present"}
    assert metrics == {}


# --- redaction + leak audit (T-43-12) -----------------------------------------


def test_redact_entry_strips_pii_and_secret_keys() -> None:
    redacted = redact_entry(
        {
            "case_id": "x",
            "adresse": "12 rue X",
            "latitude": 47.0,
            "api_key": "dc_live_abc",
            "category": "complete",
        }
    )
    assert set(redacted.keys()) == {"case_id", "category"}
    assert FORBIDDEN_KEYS  # non-empty guard set


def test_redact_entry_is_recursive() -> None:
    redacted = redact_entry(
        {"case_id": "x", "invariants": {"adresse": "12 rue de la Paix"}}
    )
    assert redacted == {"case_id": "x", "invariants": {}}


def test_audit_manifest_keys_flags_forbidden_key() -> None:
    violations = audit_manifest_keys({"cases": [{"adresse": "12 rue X"}]})
    assert violations
    assert audit_manifest_keys(_fixture()) == []


def test_audit_artifact_text_flags_leaks() -> None:
    assert audit_artifact_text("lat 47.213456", source="x")
    assert audit_artifact_text('"latitude":47', source="x")
    assert audit_artifact_text("47.21,-1.55", source="x")
    assert audit_artifact_text("key dc_live_deadbeef01", source="x")
    assert audit_artifact_text("12 rue de la Paix", source="x")
    assert audit_artifact_text("12 bis rue de la Paix", source="x")
    assert audit_artifact_text("8 passage du Port", source="x")
    assert audit_artifact_text('"gps":"47.2134,-1.5536"', source="x")
    assert audit_artifact_text("sk-proj-REALISTIC123", source="x")
    assert audit_artifact_text(
        '"payload_raw":"eyJhZHJlc3NlIjoiMTIgcnVlIGRlIGxhIFBhaXgifQ=="',
        source="x",
    )
    assert audit_artifact_text(
        "eyJhZHJlc3NlX3Bvc3RhbGUiOiIxMiBiaXMgcnVlIGRlIGxhIFBhaXgifQ==",
        source="x",
    )
    assert audit_artifact_text('"adresse_postale":"rue privée"', source="x")
    encoded = base64.b64encode(base64.b64encode(b"12 bis rue de la Paix")).decode()
    assert audit_artifact_text(encoded, source="x")
    assert audit_artifact_text(b"12 rue de la Paix".hex(), source="x")
    unpadded = base64.b64encode(b"12 rue de la Paix").decode().rstrip("=")
    assert audit_artifact_text(unpadded, source="x")
    urlsafe = base64.urlsafe_b64encode(b"12 rue de la Paix").decode().rstrip("=")
    assert audit_artifact_text(urlsafe, source="x")
    assert audit_artifact_text("\uff11\uff12 \uff52\uff55\uff45 de la Paix", source="x")
    assert audit_artifact_text("12 r\u200bue de la Paix", source="x")


def test_null_island_sentinel_is_not_treated_as_private_coordinates() -> None:
    assert audit_artifact_text('"latitude":0,"longitude":0', source="x") == []


def test_audit_diagnostics_never_echo_matched_secret() -> None:
    secret = "dc_live_deadbeef01"
    violations = audit_artifact_text(secret, source="manifest")
    assert violations
    assert all(secret not in violation for violation in violations)


def test_audit_artifact_text_clean_on_minimized_fixture() -> None:
    text = _FIXTURE.read_text(encoding="utf-8")
    assert audit_artifact_text(text, source="fixture") == []


# --- selector minimization (D-10/D-11) ----------------------------------------


def test_build_candidate_entry_is_minimized_and_redacted() -> None:
    entry = build_candidate_entry(
        case_id="golden-address-001",
        case_type="address",
        departement="85",
        code_insee="85991",
        category="no_dvf",
        frozen_at="2026-07-15",
        source_millesimes={"dpe": "2024"},
        invariants={
            "db": {"has_commune_row": True, "parcelle_present": True},
            "api": {"dvf_state": "out_of_coverage"},
            "mapper": {"prix_m2_unit": "euro_per_m2"},
        },
    )
    assert audit_manifest_keys(entry) == []
    assert entry["private_ref"] == "golden-address-001"
    assert "adresse" not in entry and "latitude" not in entry


def test_extract_private_input_holds_geocode_indexed_by_case_id() -> None:
    private = extract_private_input(
        case_id="golden-address-001",
        geocode={"query": "synthetic-query", "id_parcelle": "synthetic"},
    )
    assert private["case_id"] == "golden-address-001"
    assert private["query"] == "synthetic-query"
    assert private["id_parcelle"] == "synthetic"


# Default db/api carry the required_observations (Phase 48, schema.json) so
# every pre-existing selector test -- built without overrides -- keeps
# satisfying observation coverage trivially, exactly like it always satisfied
# category coverage. Tests that exercise the observation mechanism itself pass
# explicit db=/api= overrides to build "ordinary" candidates lacking them.
#
# No single case can carry copro_row_present true AND false, so the conjunctive
# "maison divisee" archetype needs a case of its own: one reserved ID plays it,
# the same role golden-address-003 holds in the synthetic fixture. Without it,
# a default pool would stop covering the requirement Plan 48-16 adds.
_DIVIDED_HOUSE_CASE_ID = "golden-address-003"
_DEFAULT_OBSERVED_DB = {
    "has_commune_row": True,
    "copro_row_present": True,
    "dpe_appartements_multiples": True,
    "dvf_appartements_present": True,
}
_DEFAULT_DIVIDED_HOUSE_DB = {
    "has_commune_row": True,
    "copro_row_present": False,
    "dpe_appartements_multiples": True,
}
_DEFAULT_OBSERVED_API = {
    "dvf_state": "value",
    "dpe_state": "value",
    "cadastre_state": "not_reported",
}


def _default_observed_db(case_id: str) -> dict[str, Any]:
    if case_id == _DIVIDED_HOUSE_CASE_ID:
        return dict(_DEFAULT_DIVIDED_HOUSE_DB)
    return dict(_DEFAULT_OBSERVED_DB)


def _observed_candidate(
    case_id: str,
    category: str,
    *,
    db: dict[str, Any] | None = None,
    api: dict[str, Any] | None = None,
) -> dict[str, Any]:
    case_type = "commune" if "commune" in case_id else "address"
    departement = "44" if case_type == "commune" else "85"
    mapper = (
        {"densite_unit": "density"}
        if case_type == "commune"
        else {"prix_m2_unit": "euro_per_m2"}
    )
    return _valid_case(
        case_id=case_id,
        private_ref=case_id,
        type=case_type,
        departement=departement,
        code_insee=f"{departement}991",
        category=category,
        invariants={
            "db": dict(db) if db is not None else _default_observed_db(case_id),
            "api": dict(api) if api is not None else dict(_DEFAULT_OBSERVED_API),
            "mapper": mapper,
        },
    )


def test_selector_reduces_oversized_pool_to_minimal_covered_subset() -> None:
    # The four required categories are carried by the eight lowest case IDs, so
    # the same eight cases are selected whether or not they are pinned
    # (Plan 48-16): the assertions below stay true under both schemas.
    entries = [
        _observed_candidate("golden-commune-001", "complete"),
        _observed_candidate("golden-commune-002", "no_dvf"),
        _observed_candidate("golden-commune-003", "no_dpe"),
        _observed_candidate("golden-commune-004", "partial"),
        _observed_candidate("golden-commune-005", "complete"),
        _observed_candidate("golden-commune-006", "dataset_out_of_coverage"),
        *[
            _observed_candidate(
                f"golden-address-{index:03d}",
                "dataset_out_of_coverage" if index == 5 else "complete",
            )
            for index in range(1, 12)
        ],
    ]

    selected = _select_minimal_entries(entries, _schema())

    assert len(selected) == 8
    assert sum(entry["type"] == "commune" for entry in selected) == 3
    assert sum(entry["type"] == "address" for entry in selected) == 5
    assert {entry["category"] for entry in selected} == set(
        _schema()["required_categories"]
    )
    assert not any(entry["category"] == "partial" for entry in selected)
    assert (
        validate_manifest(
            {"schema_version": "1", "description": "test", "cases": selected},
            _schema(),
        )
        == []
    )


def test_selector_is_deterministic_across_candidate_order() -> None:
    entries = _fixture()["cases"] + [
        _observed_candidate("golden-address-010", "complete")
    ]
    forward = _select_minimal_entries(entries, _schema())
    reverse = _select_minimal_entries(list(reversed(entries)), _schema())
    assert [entry["case_id"] for entry in forward] == [
        entry["case_id"] for entry in reverse
    ]


def test_selector_rejects_pool_missing_required_category() -> None:
    # Re-categorized rather than removed: dropping the case would also break a
    # pinned corpus (Plan 48-16) and make the pool fail for the wrong reason,
    # while this test is about category coverage alone.
    entries = [dict(entry) for entry in _fixture()["cases"]]
    for entry in entries:
        if entry["category"] == "no_dpe":
            entry["category"] = "complete"
    with pytest.raises(GoldenSecurityError, match="cannot satisfy"):
        _select_minimal_entries(entries, _schema())


def test_selector_uses_more_than_minimum_when_coverage_requires_it() -> None:
    # Addresses only carry 'partial' (allowed, not required): every required
    # category must come from a commune, forcing a 4th commune above the min.
    entries = [
        _observed_candidate("golden-commune-001", "complete"),
        _observed_candidate("golden-commune-002", "no_dvf"),
        _observed_candidate("golden-commune-003", "no_dpe"),
        _observed_candidate("golden-commune-004", "dataset_out_of_coverage"),
        _observed_candidate("golden-address-001", "partial"),
        _observed_candidate("golden-address-002", "partial"),
        _observed_candidate("golden-address-003", "partial"),
        _observed_candidate("golden-address-004", "partial"),
        _observed_candidate("golden-address-005", "partial"),
    ]
    selected = _select_minimal_entries(entries, _schema())
    assert len(selected) == 9
    assert sum(entry["type"] == "commune" for entry in selected) == 4


def test_selector_accepts_pool_without_partial() -> None:
    # 43-09 amendment regression: a corpus with the four observable categories
    # and no 'partial' anywhere must satisfy coverage.
    entries = [
        _observed_candidate("golden-commune-001", "complete"),
        _observed_candidate("golden-commune-002", "complete"),
        _observed_candidate("golden-commune-003", "complete"),
        _observed_candidate("golden-address-001", "complete"),
        _observed_candidate("golden-address-002", "no_dvf"),
        _observed_candidate("golden-address-003", "no_dpe"),
        _observed_candidate("golden-address-004", "dataset_out_of_coverage"),
        _observed_candidate("golden-address-005", "dataset_out_of_coverage"),
    ]
    selected = _select_minimal_entries(entries, _schema())
    assert {entry["category"] for entry in selected} >= set(
        _schema()["required_categories"]
    )
    assert (
        validate_manifest(
            {"schema_version": "1", "description": "test", "cases": selected},
            _schema(),
        )
        == []
    )


# --- required_observations coverage (Phase 48, D1/D2/D3) -----------------------


def _requirement_triples(requirement: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a required_observations entry into its triples, either shape.

    Plan 48-14 added the conjunctive shape ``{"all_of": [triple, ...]}`` next
    to the historical triple. The harness must read both BEFORE Plan 48-16
    amends schema.json, otherwise that push turns the offline gate red.
    """
    conjunction = requirement.get("all_of")
    return list(conjunction) if conjunction is not None else [requirement]


def test_schema_declares_required_observations() -> None:
    schema = _schema()
    required_observations = schema["required_observations"]
    assert required_observations
    for item in required_observations:
        assert set(item) in ({"layer", "invariant", "value"}, {"all_of"})
        for triple in _requirement_triples(item):
            assert set(triple) == {"layer", "invariant", "value"}
            assert triple["layer"] in {"db", "api"}


def test_required_observations_are_within_allowlists() -> None:
    schema = _schema()
    for item in schema["required_observations"]:
        for triple in _requirement_triples(item):
            if triple["layer"] == "db":
                assert triple["invariant"] in schema["allowed_db_invariants"]
            else:
                assert triple["value"] in schema["allowed_states"]


_ORDINARY_COMMUNE_DB = {"has_commune_row": True}
_ORDINARY_COMMUNE_API = {"dvf_state": "value", "dpe_state": "value"}
_ORDINARY_ADDRESS_DB = {"has_commune_row": True}
_ORDINARY_ADDRESS_API = {
    "cadastre_state": "value",
    "dvf_state": "value",
    "dpe_state": "value",
}


def _ordinary_commune_pool() -> list[dict[str, Any]]:
    return [
        _observed_candidate(
            f"golden-commune-{index:03d}",
            category,
            db=dict(_ORDINARY_COMMUNE_DB),
            api=dict(_ORDINARY_COMMUNE_API),
        )
        for index, category in enumerate(
            ["complete", "no_dvf", "no_dpe", "dataset_out_of_coverage"], start=1
        )
    ]


def _ordinary_address_pool(count: int = 5) -> list[dict[str, Any]]:
    """``count`` addresses carrying no multi-logement invariant at all.

    The range starts at 001 so any pool built from this helper already holds
    the frozen case IDs a ``pinned_cases`` schema would force in (Plan 48-14
    Task 2): a pool that lacks them fails firmly for the wrong reason.
    """
    return [
        _observed_candidate(
            f"golden-address-{index:03d}",
            "complete",
            db=dict(_ORDINARY_ADDRESS_DB),
            api=dict(_ORDINARY_ADDRESS_API),
        )
        for index in range(1, count + 1)
    ]


def test_selector_selects_a_case_per_required_observation() -> None:
    schema = _schema()
    ordinary_addresses = _ordinary_address_pool()
    special_cases = [
        _observed_candidate(
            "golden-address-090",
            "complete",
            db={"has_commune_row": True, "copro_row_present": True},
            api=dict(_ORDINARY_ADDRESS_API),
        ),
        _observed_candidate(
            "golden-address-091",
            "complete",
            db={"has_commune_row": True, "dpe_appartements_multiples": True},
            api=dict(_ORDINARY_ADDRESS_API),
        ),
        _observed_candidate(
            "golden-address-092",
            "complete",
            db=dict(_ORDINARY_ADDRESS_DB),
            api={
                "cadastre_state": "not_reported",
                "dvf_state": "value",
                "dpe_state": "value",
            },
        ),
        # Two archetypes the schema does not require YET (Plan 48-16 amends it):
        # the pool must already carry them, or the harness goes red on that push.
        _observed_candidate(
            "golden-address-093",
            "complete",
            db={"has_commune_row": True, "dvf_appartements_present": True},
            api=dict(_ORDINARY_ADDRESS_API),
        ),
        _observed_candidate(
            "golden-address-094",
            "complete",
            db={
                "has_commune_row": True,
                "dpe_appartements_multiples": True,
                "copro_row_present": False,
            },
            api=dict(_ORDINARY_ADDRESS_API),
        ),
    ]
    pool = _ordinary_commune_pool() + ordinary_addresses + special_cases
    selected = _select_minimal_entries(pool, schema)
    for requirement in schema["required_observations"]:
        assert any(observation_is_satisfied(entry, requirement) for entry in selected)


def test_selector_rejects_pool_missing_a_required_observation() -> None:
    schema = _schema()
    pool = _ordinary_commune_pool() + _ordinary_address_pool()
    with pytest.raises(GoldenSecurityError, match="cannot satisfy"):
        _select_minimal_entries(pool, schema)


# --- required_departement_coverage (Phase 57 Plan 03, todo 2026-07-27) --------
#
# Incident 48-12 mirrored: golden-address-009, the corpus' only departement 85
# address, was evicted by minimization alone with no gate noticing. This
# generalizes required_categories/required_observations to a third dimension,
# the departement of the code_insee prefix, so a pool lacking a departement
# fails the same closed way instead of silently shipping a 44-only corpus.
#
# ``_observed_candidate`` fixes every address candidate to departement '85'
# (see its definition above): a pool built only from it or
# ``_ordinary_address_pool`` can therefore never carry a '44' address, which
# is exactly the scenario the missing-coverage test below exercises. Tests
# that need both departements build the '44' side explicitly.


def _address_in_departement(
    case_id: str, departement: str, category: str = "complete"
) -> dict[str, Any]:
    """An 'ordinary' address candidate pinned to a specific departement.

    Mirrors ``_observed_candidate``'s address shape (same db/api invariants,
    satisfying ``required_observations`` trivially through the default
    template) but with an explicit departement/code_insee instead of the
    hardcoded '85', so a pool can be given candidates in two departements.
    """
    return _valid_case(
        case_id=case_id,
        private_ref=case_id,
        type="address",
        departement=departement,
        code_insee=f"{departement}991",
        category=category,
        invariants={
            "db": dict(_default_observed_db(case_id)),
            "api": dict(_DEFAULT_OBSERVED_API),
            "mapper": {"prix_m2_unit": "euro_per_m2"},
        },
    )


def _schema_with_departement_coverage(
    departements: tuple[str, ...] = ("44", "85"),
    *,
    min_par_departement: int = 1,
    case_type: str = "address",
) -> dict[str, Any]:
    schema = _schema()
    schema["required_departement_coverage"] = {
        "type": case_type,
        "departements": list(departements),
        "min_par_departement": min_par_departement,
    }
    return schema


def _observation_compliant_pool() -> list[dict[str, Any]]:
    """A pool satisfying required_categories AND required_observations on its
    own, isolating the departement-coverage mechanic under test.

    Same construction as ``test_selector_selects_a_case_per_required_observation``
    above (one special case per required_observations item), minimized to the
    three that are strictly needed: 090 alone covers copro_row_present=true,
    093 covers dvf_appartements_present=true, 094 covers BOTH
    dpe_appartements_multiples=true and the conjunctive 'maison divisee' item
    (dpe_appartements_multiples=true AND copro_row_present=false). Every
    address here -- ordinary and special alike -- is departement '85'
    (``_observed_candidate``'s fixed template): without an explicit '44'
    entry added on top, this pool can never satisfy a departement requirement
    naming '44', which is exactly what the missing-coverage test below relies
    on to fail for the RIGHT reason.
    """
    special_cases = [
        _observed_candidate(
            "golden-address-090",
            "complete",
            db={"has_commune_row": True, "copro_row_present": True},
            api=dict(_ORDINARY_ADDRESS_API),
        ),
        _observed_candidate(
            "golden-address-093",
            "complete",
            db={"has_commune_row": True, "dvf_appartements_present": True},
            api=dict(_ORDINARY_ADDRESS_API),
        ),
        _observed_candidate(
            "golden-address-094",
            "complete",
            db={
                "has_commune_row": True,
                "dpe_appartements_multiples": True,
                "copro_row_present": False,
            },
            api=dict(_ORDINARY_ADDRESS_API),
        ),
    ]
    return _ordinary_commune_pool() + _ordinary_address_pool() + special_cases


def test_selector_selects_a_case_per_required_departement() -> None:
    schema = _schema_with_departement_coverage()
    pool = [
        *_observation_compliant_pool(),
        _address_in_departement("golden-address-095", "44"),
    ]
    selected = _select_minimal_entries(pool, schema)
    covered = {
        entry["code_insee"][:2] for entry in selected if entry["type"] == "address"
    }
    assert {"44", "85"} <= covered


def test_selector_rejects_pool_missing_a_required_departement() -> None:
    schema = _schema_with_departement_coverage()
    # Compliant on every other axis (categories, observations); no '44'
    # address anywhere -- only the departement dimension can be responsible
    # for the failure below.
    pool = _observation_compliant_pool()
    with pytest.raises(GoldenSecurityError, match="cannot satisfy"):
        _select_minimal_entries(pool, schema)


def test_selection_is_unchanged_without_the_departement_coverage_key() -> None:
    # Absent key and an empty ``departements`` list are the two equivalent
    # neutral forms (the mask is () either way): both must select identically,
    # the same property test_selection_is_unchanged_without_the_pinned_cases_key
    # proves for pinned_cases.
    schema = _schema()
    schema.pop("required_departement_coverage", None)
    pool = _observation_compliant_pool()
    without_key = [entry["case_id"] for entry in _select_minimal_entries(pool, schema)]
    with_empty = [
        entry["case_id"]
        for entry in _select_minimal_entries(
            pool, _schema_with_departement_coverage(departements=())
        )
    ]
    assert without_key == with_empty


def test_selector_departement_coverage_selection_is_deterministic() -> None:
    schema = _schema_with_departement_coverage()
    pool = [
        *_observation_compliant_pool(),
        _address_in_departement("golden-address-095", "44"),
    ]
    forward = _select_minimal_entries(pool, schema)
    reverse = _select_minimal_entries(list(reversed(pool)), schema)
    assert [entry["case_id"] for entry in forward] == [
        entry["case_id"] for entry in reverse
    ]


def test_selector_honors_min_par_departement_above_one() -> None:
    # The pool carries two '44' addresses: min_par_departement=2 must keep
    # both, not just one -- a plain OR'd presence bit could not tell the
    # difference (48-12 mirrored the other way: a single bit collapses count).
    schema = _schema_with_departement_coverage(min_par_departement=2)
    pool = [
        *_observation_compliant_pool(),
        _address_in_departement("golden-address-095", "44"),
        _address_in_departement("golden-address-096", "44"),
    ]
    selected = _select_minimal_entries(pool, schema)
    addresses_44 = [
        entry
        for entry in selected
        if entry["type"] == "address" and entry["code_insee"].startswith("44")
    ]
    assert len(addresses_44) >= 2


def test_selector_rejects_pool_with_insufficient_addresses_per_departement() -> None:
    # Same min_par_departement=2 requirement, but the pool only carries one
    # '44' address: firm failure, never a silently-relaxed corpus.
    schema = _schema_with_departement_coverage(min_par_departement=2)
    pool = [
        *_observation_compliant_pool(),
        _address_in_departement("golden-address-095", "44"),
    ]
    with pytest.raises(GoldenSecurityError, match="cannot satisfy"):
        _select_minimal_entries(pool, schema)


def test_departement_coverage_mask_is_shaped_like_the_declared_departements() -> None:
    coverage = {
        "type": "address",
        "departements": ["44", "85"],
        "min_par_departement": 1,
    }
    entry_44 = _address_in_departement("golden-address-095", "44")
    entry_85 = _address_in_departement("golden-address-096", "85")
    commune = _observed_candidate("golden-commune-001", "complete")
    assert _departement_coverage_mask(entry_44, coverage) == (1, 0)
    assert _departement_coverage_mask(entry_85, coverage) == (0, 1)
    # Wrong type never contributes, even from a matching departement.
    assert _departement_coverage_mask(commune, coverage) == (0, 0)
    assert _departement_coverage_mask(entry_44, None) == ()


# --- conjunctive required_observations (Plan 48-14, GAP-1) --------------------
#
# schema.json is NOT amended by this plan: the conjunctive requirement and the
# corpus that satisfies it ship together in Plan 48-16. The mechanism is
# therefore exercised on in-memory schema copies.

_MAISON_DIVISEE = {
    "all_of": [
        {"layer": "db", "invariant": "dpe_appartements_multiples", "value": True},
        {"layer": "db", "invariant": "copro_row_present", "value": False},
    ]
}


def _schema_with_observations(*requirements: dict[str, Any]) -> dict[str, Any]:
    schema = _schema()
    schema["required_observations"] = [dict(req) for req in requirements]
    return schema


def _address(case_id: str, **db: Any) -> dict[str, Any]:
    return _observed_candidate(
        case_id,
        "complete",
        db={"has_commune_row": True, **db},
        api=dict(_ORDINARY_ADDRESS_API),
    )


def test_conjunctive_requirement_needs_every_triple() -> None:
    entry = _address(
        "golden-address-090", dpe_appartements_multiples=True, copro_row_present=False
    )
    assert observation_is_satisfied(entry, _MAISON_DIVISEE) is True
    assert (
        observation_is_satisfied(
            entry,
            {
                "all_of": [
                    {
                        "layer": "db",
                        "invariant": "dpe_appartements_multiples",
                        "value": True,
                    },
                    {"layer": "db", "invariant": "copro_row_present", "value": True},
                ]
            },
        )
        is False
    )


def test_selector_rejects_pool_where_no_case_satisfies_the_whole_conjunction() -> None:
    # The exact scenario that escaped Plan 48-11: each triple is satisfied
    # separately, no candidate satisfies them together, so the composite
    # archetype never enters the corpus (48-GOLDEN-EVIDENCE 13.1).
    schema = _schema_with_observations(_MAISON_DIVISEE)
    pool = (
        _ordinary_commune_pool()
        + _ordinary_address_pool()
        + [
            _address("golden-address-090", copro_row_present=False),
            _address("golden-address-091", dpe_appartements_multiples=True),
        ]
    )
    with pytest.raises(GoldenSecurityError, match="cannot satisfy"):
        _select_minimal_entries(pool, schema)


def test_selector_keeps_the_case_satisfying_the_whole_conjunction() -> None:
    schema = _schema_with_observations(_MAISON_DIVISEE)
    pool = (
        _ordinary_commune_pool()
        + _ordinary_address_pool()
        + [
            _address("golden-address-090", copro_row_present=False),
            _address("golden-address-091", dpe_appartements_multiples=True),
            _address(
                "golden-address-092",
                dpe_appartements_multiples=True,
                copro_row_present=False,
            ),
        ]
    )
    selected = _select_minimal_entries(pool, schema)
    assert "golden-address-092" in {entry["case_id"] for entry in selected}


def test_absent_invariant_does_not_satisfy_a_false_requirement() -> None:
    # never-guess (D-04): an invariant ABSENT from the case is not a measured
    # negative. dict.get() yields None and None == False is False in Python, so
    # a pool that never measured copro_row_present cannot satisfy value: false.
    schema = _schema_with_observations(
        {"layer": "db", "invariant": "copro_row_present", "value": False}
    )
    pool = _ordinary_commune_pool() + _ordinary_address_pool()
    with pytest.raises(GoldenSecurityError, match="cannot satisfy"):
        _select_minimal_entries(pool, schema)

    measured = [*pool, _address("golden-address-090", copro_row_present=False)]
    selected = _select_minimal_entries(measured, schema)
    assert "golden-address-090" in {entry["case_id"] for entry in selected}


def test_triple_shape_is_honoured_alongside_the_conjunctive_shape() -> None:
    # Non-regression: the historical triple keeps its exact semantics when a
    # conjunction sits next to it in the same required_observations list.
    schema = _schema_with_observations(
        {"layer": "db", "invariant": "copro_row_present", "value": True},
        _MAISON_DIVISEE,
    )
    pool = (
        _ordinary_commune_pool()
        + _ordinary_address_pool()
        + [
            _address("golden-address-090", copro_row_present=True),
            _address(
                "golden-address-092",
                dpe_appartements_multiples=True,
                copro_row_present=False,
            ),
        ]
    )
    selected = _select_minimal_entries(pool, schema)
    case_ids = {entry["case_id"] for entry in selected}
    assert {"golden-address-090", "golden-address-092"} <= case_ids


# --- pinned_cases (Plan 48-14, incident 48-12 9.2) ----------------------------
#
# schema.json carries a real pinned_cases list since Plan 48-16. The mechanics
# below are exercised on in-memory copies with explicit pins; the real list is
# asserted against the real manifest by the single test that reads both.


def test_real_manifest_satisfies_its_pinned_cases() -> None:
    """The committed corpus contains every case the schema pins.

    Until Plan 48-17 this held only through --audit-artifacts-only: no unit
    test read the real manifest, so neutralizing the pins in _schema() would
    have left the property unasserted in this suite. It is asserted here.
    """
    schema = _real_schema()
    manifest = json.loads(_REAL_MANIFEST.read_text(encoding="utf-8"))
    committed = {case["case_id"] for case in manifest["cases"]}
    assert set(schema["pinned_cases"]) <= committed
    assert validate_manifest(manifest, schema) == []


def test_real_schema_pins_the_frozen_corpus() -> None:
    """CR-03: the freeze itself must survive, not only the cases it names.

    ``test_real_manifest_satisfies_its_pinned_cases`` is vacuously true on an
    emptied list (``set() <= committed``) and on a removed key
    (``validate_manifest`` loops over ``()``): with ``pinned_cases: []`` the
    whole offline suite AND ``--audit-artifacts-only`` stay green, and the
    'maison divisee' archetype becomes evictable by minimization again --
    incident 48-12 9.2. The assertions below name FACTS, not the shape:
    which archetypes have a single carrier, and that a single carrier must be
    pinned. The last block keeps the freeze self-maintained when a future
    archetype becomes mono-carrier.
    """
    schema = _real_schema()
    pinned = set(schema["pinned_cases"])
    assert pinned, "pinned_cases must never be emptied: it IS the human freeze"
    manifest = json.loads(_REAL_MANIFEST.read_text(encoding="utf-8"))
    for requirement in schema["required_observations"]:
        carriers = {
            case["case_id"]
            for case in manifest["cases"]
            if observation_is_satisfied(case, requirement)
        }
        # A single-carrier archetype MUST be pinned, otherwise the selector's
        # lexicographic tie-break can replace it without any human decision.
        if len(carriers) == 1:
            assert carriers <= pinned, _describe_observation(requirement)


def test_load_schema_rejects_an_emptied_or_removed_freeze(tmp_path: Path) -> None:
    """The freeze fails closed at load time, like required_categories.

    A test-only guard would protect this suite alone; the selector CLI, the
    audit job and the live replay all go through ``load_schema``, so the
    invariant belongs there -- symmetric with the ``required_categories``
    non-empty-subset guard three lines above it.
    """
    for mutation in ("empty", "removed"):
        schema = _real_schema()
        if mutation == "empty":
            schema["pinned_cases"] = []
        else:
            schema.pop("pinned_cases")
        path = tmp_path / f"schema_{mutation}.json"
        path.write_text(json.dumps(schema), encoding="utf-8")
        with pytest.raises(GoldenSecurityError, match="pinned_cases"):
            load_schema(path)


def _schema_with_pinned(*case_ids: str) -> dict[str, Any]:
    schema = _schema()
    schema["pinned_cases"] = list(case_ids)
    return schema


def _pinnable_pool() -> list[dict[str, Any]]:
    """Pool whose 6th address is pure filler: no requirement needs it.

    That is the exact situation of golden-address-009 in 48-12 9.3 -- a frozen
    case carrying no unique requirement, therefore evictable by minimization.
    """
    return [
        _observed_candidate("golden-commune-001", "complete"),
        _observed_candidate("golden-commune-002", "no_dvf"),
        _observed_candidate("golden-commune-003", "no_dpe"),
        *[
            _observed_candidate(
                f"golden-address-{index:03d}",
                "dataset_out_of_coverage" if index == 5 else "complete",
            )
            for index in range(1, 7)
        ],
    ]


def test_pinned_case_survives_minimization() -> None:
    pool = _pinnable_pool()
    baseline = {entry["case_id"] for entry in _select_minimal_entries(pool, _schema())}
    assert "golden-address-006" not in baseline

    schema = _schema_with_pinned("golden-address-006")
    selected = _select_minimal_entries(pool, schema)
    assert "golden-address-006" in {entry["case_id"] for entry in selected}
    assert (
        validate_manifest(
            {"schema_version": "1", "description": "test", "cases": selected}, schema
        )
        == []
    )


def test_pinned_case_absent_from_the_pool_fails_closed() -> None:
    schema = _schema_with_pinned("golden-address-042")
    with pytest.raises(GoldenSecurityError) as excinfo:
        _select_minimal_entries(_pinnable_pool(), schema)
    message = str(excinfo.value)
    # A case_id is opaque and may appear; nothing else may.
    assert "golden-address-042" in message
    assert "rue" not in message.lower()
    assert re.search(r"[0-9]{5}[0-9A-Z]{9}", message) is None
    assert "://" not in message and "password" not in message.lower()


def test_selection_is_unchanged_without_the_pinned_cases_key() -> None:
    # The proof that this plan changes NOTHING to the corpus while the key is
    # absent from the schema: absent means no-op. The key is stripped rather
    # than assumed missing -- Plan 48-16 will add it, and this test must keep
    # proving the same property afterwards.
    schema = _schema()
    schema.pop("pinned_cases", None)
    pool = _pinnable_pool()
    without_key = [entry["case_id"] for entry in _select_minimal_entries(pool, schema)]
    with_empty = _select_minimal_entries(pool, _schema_with_pinned())
    assert without_key == [entry["case_id"] for entry in with_empty]


def test_pinned_cases_beyond_cardinality_maximum_fail_closed() -> None:
    max_communes = _schema()["cardinality"]["commune"]["max"]
    pool = _pinnable_pool() + [
        _observed_candidate(f"golden-commune-{index:03d}", "complete")
        for index in range(4, max_communes + 2)
    ]
    pinned = [f"golden-commune-{index:03d}" for index in range(1, max_communes + 2)]
    with pytest.raises(GoldenSecurityError, match="cardinality maximum"):
        _select_minimal_entries(pool, _schema_with_pinned(*pinned))


# --- symmetric manifest coverage (Plan 48-14, WR-08) --------------------------
#
# Until now required_observations was enforced by the selector alone: a
# committed manifest could lose its only multi-logement case and still pass
# --audit-artifacts-only AND the live replay.


def _fixture_without_case_id(case_id: str) -> dict[str, Any]:
    manifest = _fixture()
    manifest["cases"] = [c for c in manifest["cases"] if c["case_id"] != case_id]
    return manifest


def test_manifest_losing_its_multi_logement_case_is_rejected() -> None:
    # Hand-edit, unlucky merge, or regeneration from an impoverished pool.
    manifest = _fixture()
    for case in manifest["cases"]:
        case["invariants"]["db"].pop("copro_row_present", None)
    errors = validate_manifest(manifest, _schema())
    assert any("db.copro_row_present=True" in error for error in errors)


def test_manifest_losing_the_conjunctive_archetype_is_rejected() -> None:
    schema = _schema_with_observations(_MAISON_DIVISEE)
    assert validate_manifest(_fixture(), schema) == []

    manifest = _fixture()
    for case in manifest["cases"]:
        if case["invariants"]["db"].get("dpe_appartements_multiples"):
            case["invariants"]["db"]["copro_row_present"] = True
    errors = validate_manifest(manifest, schema)
    assert any("missing required observation" in error for error in errors)
    assert any("db.copro_row_present=False" in error for error in errors)


def test_manifest_missing_a_pinned_case_is_rejected() -> None:
    schema = _schema_with_pinned("golden-address-004")
    assert validate_manifest(_fixture(), schema) == []
    errors = validate_manifest(_fixture_without_case_id("golden-address-004"), schema)
    assert any("missing pinned case: golden-address-004" in error for error in errors)


def test_validation_adds_nothing_when_both_keys_are_absent() -> None:
    schema = _schema()
    schema.pop("required_observations")
    schema.pop("pinned_cases", None)
    stripped = _fixture()
    for case in stripped["cases"]:
        case["invariants"]["db"] = {"parcelle_present": True}
    assert validate_manifest(stripped, schema) == []


def test_selector_and_validation_share_one_requirement_test() -> None:
    # A divergence between the two would be worse than the WR-08 hole itself,
    # so the selector's coverage mask calls the very function used here.
    entry = _address(
        "golden-address-090", dpe_appartements_multiples=True, copro_row_present=False
    )
    assert observation_is_satisfied(entry, _MAISON_DIVISEE) is True
    schema = _schema_with_observations(_MAISON_DIVISEE)
    manifest = {"schema_version": "1", "description": "test", "cases": [entry]}
    assert not any(
        "missing required observation" in error
        for error in validate_manifest(manifest, schema)
    )


def test_selector_still_satisfies_required_categories() -> None:
    schema = _schema()
    selected = _select_minimal_entries(_fixture()["cases"], schema)
    assert set(schema["required_categories"]) <= {
        entry["category"] for entry in selected
    }
    for requirement in schema["required_observations"]:
        assert any(observation_is_satisfied(entry, requirement) for entry in selected)


def test_selector_remains_deterministic() -> None:
    schema = _schema()
    entries = _fixture()["cases"] + [
        _observed_candidate("golden-address-010", "complete")
    ]
    forward = _select_minimal_entries(entries, schema)
    reverse = _select_minimal_entries(list(reversed(entries)), schema)
    assert [entry["case_id"] for entry in forward] == [
        entry["case_id"] for entry in reverse
    ]


def test_selector_respects_address_cardinality() -> None:
    schema = _schema()
    max_addresses = schema["cardinality"]["address"]["max"]
    addresses = [
        _observed_candidate(f"golden-address-{index:03d}", "complete")
        for index in range(1, max_addresses + 5)
    ]
    selected = _select_minimal_entries(_ordinary_commune_pool() + addresses, schema)
    assert sum(entry["type"] == "address" for entry in selected) <= max_addresses


def test_selected_manifest_still_validates() -> None:
    schema = _schema()
    selected = _select_minimal_entries(_fixture()["cases"], schema)
    manifest = {"schema_version": "1", "description": "test", "cases": selected}
    assert validate_manifest(manifest, schema) == []


def test_synthetic_fixture_still_covers_categories() -> None:
    observed = {c["category"] for c in _fixture()["cases"]}
    assert observed == set(_schema()["allowed_categories"])


def test_observe_candidates_builds_schema_valid_entries(monkeypatch: Any) -> None:
    # Live-path regression: collect_live_observation_projected returns only
    # {db, api}; the observer must re-attach the probe's fixed mapper block or
    # every real candidate fails 'invariants must contain exactly db, api and
    # mapper objects'.
    import scripts.select_golden_cases as sgc

    def fake_collect(conn, client, probe, private, projector, **_kwargs):
        observed = {
            "db": {
                "has_commune_row": True,
                "dpe_rattachement_ok": True,
            },
            "api": {f"{d}_state": "value" for d in ("dvf", "dpe")},
        }
        return (
            observed,
            {"recensement": "2022"},
            {},
            {"code_insee": probe["code_insee"]},
        )

    class FakeConn:
        def rollback(self) -> None:
            return None

        def close(self) -> None:
            return None

    captured_headers: dict[str, str] = {}

    class FakeClient:
        def __init__(self, *, headers, **_kwargs):
            captured_headers.update(headers)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(sgc, "collect_live_observation_projected", fake_collect)
    monkeypatch.setattr(sgc, "_open_read_only_connection", lambda dsn: FakeConn())
    monkeypatch.setattr(sgc.httpx, "Client", FakeClient)
    monkeypatch.setattr(sgc.time, "sleep", lambda seconds: None)

    entries, projections = sgc._observe_candidates(
        {"golden-commune-001": {"type": "commune", "code_insee": "44991"}},
        "dsn-sentinel",
        "https://api.invalid",
        "key-sentinel",
        resolve_logging_redacted=True,
    )
    assert len(entries) == 1
    assert set(entries[0]["invariants"]) == {"db", "api", "mapper"}
    assert "dpe_rattachement_ok" not in entries[0]["invariants"]["db"]
    assert captured_headers[sgc.SMOKE_HEADER[0]] == sgc.SMOKE_HEADER[1]
    assert validate_case_entry(entries[0], _schema()) == []
    assert "golden-commune-001" in projections


def test_selector_rejects_duplicate_or_malformed_candidates() -> None:
    entries = _fixture()["cases"]
    with pytest.raises(GoldenSecurityError, match="duplicate"):
        _select_minimal_entries([*entries, dict(entries[0])], _schema())

    malformed = [dict(entry) for entry in entries]
    malformed[0]["departement"] = "75"
    with pytest.raises(GoldenSecurityError, match="invalid redacted"):
        _select_minimal_entries(malformed, _schema())


def test_temporary_error_is_never_promoted_to_stable_category() -> None:
    assert (
        _derive_category({"dvf_state": "temporary_error", "dpe_state": "value"}) is None
    )


def _aggregate_commune(code_insee: str = "85991") -> dict[str, Any]:
    aggregate = {field: None for field in COMMUNE_FIELDS}
    aggregate.update(
        {
            "code_insee": code_insee,
            "nom_commune": "Commune agrégée",
            "code_departement": code_insee[:2],
        }
    )
    return aggregate


def test_frontend_projection_keeps_only_closed_aggregate_commune_shape() -> None:
    probe = _probe_entry(
        "golden-address-001",
        {"type": "address", "code_insee": "85991"},
    )
    raw_payload = {
        "adresse": "private-address-sentinel",
        "datasets": {
            "commune": _aggregate_commune(),
            "dvf": [{"private_property_value": "private-dvf-sentinel"}],
        },
    }
    projected = _project_commune_payload(probe, raw_payload)
    rendered = json.dumps(projected, ensure_ascii=False)
    assert set(projected) == set(COMMUNE_FIELDS)
    assert "private-address-sentinel" not in rendered
    assert "private-dvf-sentinel" not in rendered


def test_frontend_projection_accepts_referential_labels() -> None:
    probe = _probe_entry(
        "golden-address-001",
        {"type": "address", "code_insee": "85991"},
    )
    commune = _aggregate_commune()
    commune.update({"nom_departement": "Vendée", "nom_region": "Pays de la Loire"})
    projected = _project_commune_payload(probe, {"datasets": {"commune": commune}})
    assert projected["nom_departement"] == "Vendée"
    assert projected["nom_region"] == "Pays de la Loire"


def test_frontend_projection_rejects_unapproved_aggregate_field() -> None:
    probe = _probe_entry(
        "golden-commune-001",
        {"type": "commune", "code_insee": "44991"},
    )
    payload = _aggregate_commune("44991")
    payload["adresse_exacte"] = "private-address-sentinel"
    with pytest.raises(GoldenSecurityError, match="unapproved"):
        _project_commune_payload(probe, payload)


def test_frontend_fixture_is_state_derived_and_passes_leak_audit() -> None:
    entry = _observed_candidate("golden-address-001", "complete")
    entry["invariants"]["api"] = {
        "cadastre_state": "value",
        "dvf_state": "value",
        "dpe_state": "value",
        "risques_state": "value",
        "urbanisme_state": "value",
        "commune_state": "value",
    }
    frontend_case = _build_frontend_case(entry, _aggregate_commune())
    fixture_text = _render_frontend_fixture([frontend_case])

    assert audit_artifact_text(fixture_text, "frontend-fixture") == []
    assert frontend_case["resolve"]["latitude"] == 0
    assert frontend_case["resolve"]["longitude"] == 0
    assert frontend_case["resolve"]["adresse"] is None
    assert frontend_case["resolve"]["id_parcelle"] is None
    assert frontend_case["resolve"]["datasets"]["dvf"] == [{"prix_m2": 0}]
    assert "source_millesimes" in frontend_case
    assert "invariants" in frontend_case


def test_selector_suppresses_private_values_from_operational_errors(
    monkeypatch, capsys
) -> None:
    private_value = "12 rue de la Paix"

    def fail_without_rendering(*_args) -> dict[str, Any]:
        raise RuntimeError(private_value)

    monkeypatch.setattr(
        "scripts.select_golden_cases._read_private_input", fail_without_rendering
    )
    result = select_main(
        [
            "--manifest",
            "manifest.json",
            "--frontend-fixture",
            "golden-cases.ts",
            "--private-input-env",
            "PRIVATE_INPUT",
            "--read-only",
        ]
    )
    captured = capsys.readouterr()
    assert result == 1
    assert private_value not in captured.out
    assert private_value not in captured.err


def test_selector_documented_direct_invocation_starts_offline() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/select_golden_cases.py", "--help"],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0
    assert "--private-input-env" in result.stdout
    assert "ModuleNotFoundError" not in result.stderr


def test_opt_in_cli_accepts_workflow_flags_without_secret_values(monkeypatch) -> None:
    monkeypatch.setattr("scripts.verify_golden_cases._run_live", lambda _args: 0)
    assert (
        main(
            [
                "--manifest",
                "manifest.json",
                "--private-input-env",
                "DC_GOLDEN_PRIVATE_INPUT",
                "--read-only",
            ]
        )
        == 0
    )


def test_audit_cli_rejects_replay_only_options() -> None:
    try:
        main(
            [
                "--audit-artifacts-only",
                "--manifest",
                "manifest.json",
                "--frontend-fixture",
                "fixture.ts",
                "--dsn-env",
                "DATABASE_URL",
            ]
        )
    except SystemExit:
        return
    raise AssertionError("audit mode must reject live-only options")


# --- Phase 58.1: closed V2 carrier vocabulary + attribution invariants ----------
#
# The golden contract observes the non-attributive V2 decision, not the coarse
# ``dpe_state``. Presence at an address remains separate from dwelling identity.

_UNASSIGNED_FACTS: dict[str, Any] = {
    "dpe_inventory_presence": "present",
    "dpe_attribution_status": "unassigned",
    "dpe_primary_diagnostic_present": False,
    "dpe_candidate_count": 0,
    "dpe_building_count": 0,
    "dpe_truncated": False,
}
_ABSENT_FACTS: dict[str, Any] = {
    "dpe_inventory_presence": "absent",
    "dpe_attribution_status": "absent",
    "dpe_primary_diagnostic_present": False,
    "dpe_candidate_count": 0,
    "dpe_building_count": 0,
    "dpe_truncated": False,
}
_BUILDING_CONTEXT_FACTS: dict[str, Any] = {
    "dpe_inventory_presence": "unknown",
    "dpe_attribution_status": "unassigned",
    "dpe_primary_diagnostic_present": False,
    "dpe_candidate_count": 0,
    "dpe_building_count": 7,
    "dpe_truncated": False,
}


def _carrier_case(
    case_id: str,
    *,
    facts: dict[str, Any],
    carrier_for: list[str] | None = None,
    departement: str = "44",
    case_type: str = "address",
) -> dict[str, Any]:
    """Build a manifest entry carrying the Phase 58.1 DPE invariants."""
    entry = _valid_case(
        case_id=case_id,
        private_ref=case_id,
        type=case_type,
        departement=departement,
        code_insee=f"{departement}991",
    )
    entry["invariants"] = {
        "db": {"has_commune_row": True},
        "api": {"dvf_state": "value"},
        "mapper": {"prix_m2_unit": "euro_per_m2"},
        "dpe": dict(facts),
    }
    if carrier_for is not None:
        entry["carrier_for"] = carrier_for
    return entry


def test_schema_declares_the_closed_dpe_carrier_vocabulary() -> None:
    schema = _real_schema()
    assert schema["allowed_carriers"] == [
        "dpe_unassigned",
        "dpe_nearer_neighbor_negative",
        "dpe_absent",
        "dpe_multiple_or_building",
    ]
    assert schema["required_carriers"] == [
        "dpe_unassigned",
        "dpe_nearer_neighbor_negative",
        "dpe_absent",
    ]
    assert schema["required_carrier_departements"] == ["44", "85"]
    assert "carrier_for" in schema["optional_keys"]
    assert "carrier_for" not in schema["required_keys"]
    assert schema["dpe_attribution_version"] == 2


def test_schema_declares_the_closed_dpe_inventory_and_attribution_invariants() -> None:
    schema = _real_schema()
    assert set(schema["allowed_dpe_invariants"]) == {
        "dpe_inventory_presence",
        "dpe_attribution_status",
        "dpe_primary_diagnostic_present",
        "dpe_candidate_count",
        "dpe_building_count",
        "dpe_truncated",
        "dpe_nearby_diagnostic_present",
    }
    assert set(schema["allowed_dpe_statuses"]) == {
        "unassigned",
        "absent",
        "error",
    }
    assert tuple(schema["allowed_dpe_inventory_presences"]) == (DPE_INVENTORY_PRESENCES)
    assert schema["dpe_count_max"] == DPE_COUNT_MAX


def test_load_schema_rejects_a_non_v2_dpe_contract(tmp_path: Path) -> None:
    schema = _real_schema()
    schema["dpe_attribution_version"] = 1
    path = tmp_path / "legacy-schema.json"
    path.write_text(json.dumps(schema), encoding="utf-8")

    with pytest.raises(GoldenSecurityError, match="version must be 2"):
        load_schema(path)


def test_dpe_attribution_invariants_are_optional_and_additive() -> None:
    # Phase 43/48 entries carry no `dpe` layer: they must stay valid.
    assert validate_case_entry(_valid_case(), _schema()) == []
    assert validate_manifest(_fixture(), _schema()) == []


def test_dpe_attribution_status_outside_the_closed_set_is_rejected() -> None:
    entry = _carrier_case(
        "golden-address-001",
        facts={**_UNASSIGNED_FACTS, "dpe_attribution_status": "probably_exact"},
    )
    errors = validate_case_entry(entry, _schema())
    assert any("dpe" in error for error in errors)


def test_dpe_inventory_presence_is_required_and_closed() -> None:
    schema = _schema()
    for presence in (None, "inferred", True):
        facts = dict(_UNASSIGNED_FACTS)
        if presence is None:
            facts.pop("dpe_inventory_presence")
        else:
            facts["dpe_inventory_presence"] = presence
        assert (
            validate_case_entry(
                _carrier_case("golden-address-001", facts=facts), schema
            )
            != []
        )


def test_dpe_attribution_unknown_invariant_key_is_rejected() -> None:
    entry = _carrier_case(
        "golden-address-001",
        facts={**_UNASSIGNED_FACTS, "dpe_best_guess": True},
    )
    assert validate_case_entry(entry, _schema()) != []


def test_dpe_attribution_counts_must_be_bounded_non_negative_integers() -> None:
    schema = _schema()
    for bad in (-1, DPE_COUNT_MAX + 1, True, 2.5, "3"):
        entry = _carrier_case(
            "golden-address-001",
            facts={**_BUILDING_CONTEXT_FACTS, "dpe_candidate_count": bad},
        )
        assert validate_case_entry(entry, schema) != [], f"accepted count {bad!r}"


def test_dpe_attribution_booleans_reject_non_boolean_values() -> None:
    entry = _carrier_case(
        "golden-address-001",
        facts={**_UNASSIGNED_FACTS, "dpe_primary_diagnostic_present": 1},
    )
    assert validate_case_entry(entry, _schema()) != []


def test_unassigned_carrier_forbids_a_primary_or_candidates() -> None:
    assert dpe_carrier_is_satisfied(
        _carrier_case("golden-address-001", facts=_UNASSIGNED_FACTS),
        "dpe_unassigned",
    )
    assert not dpe_carrier_is_satisfied(
        _carrier_case(
            "golden-address-001",
            facts={**_UNASSIGNED_FACTS, "dpe_primary_diagnostic_present": True},
        ),
        "dpe_unassigned",
    )
    assert not dpe_carrier_is_satisfied(
        _carrier_case(
            "golden-address-001",
            facts={**_UNASSIGNED_FACTS, "dpe_candidate_count": 1},
        ),
        "dpe_unassigned",
    )


def test_unassigned_carrier_requires_explicit_inventory_presence() -> None:
    for presence in ("absent", "unknown", None):
        facts = dict(_UNASSIGNED_FACTS)
        if presence is None:
            facts.pop("dpe_inventory_presence")
        else:
            facts["dpe_inventory_presence"] = presence
        assert not dpe_carrier_is_satisfied(
            _carrier_case("golden-address-001", facts=facts),
            "dpe_unassigned",
        )


def test_absence_carriers_require_explicit_absent_inventory_presence() -> None:
    for carrier in ("dpe_absent", "dpe_nearer_neighbor_negative"):
        facts = {**_ABSENT_FACTS, "dpe_nearby_diagnostic_present": True}
        assert dpe_carrier_is_satisfied(
            _carrier_case("golden-address-001", facts=facts), carrier
        )
        for presence in ("present", "unknown"):
            assert not dpe_carrier_is_satisfied(
                _carrier_case(
                    "golden-address-001",
                    facts={**facts, "dpe_inventory_presence": presence},
                ),
                carrier,
            )


def test_v2_dpe_invariants_forbid_a_primary_or_candidates_without_a_carrier() -> None:
    schema = _schema()
    for facts in (
        {**_UNASSIGNED_FACTS, "dpe_primary_diagnostic_present": True},
        {**_UNASSIGNED_FACTS, "dpe_candidate_count": 1},
    ):
        entry = _carrier_case("golden-address-001", facts=facts)
        assert validate_case_entry(entry, schema) != []


def test_carrier_nearer_neighbor_negative_requires_nearby_diagnostic_true() -> None:
    proven = _carrier_case(
        "golden-address-001",
        facts={**_ABSENT_FACTS, "dpe_nearby_diagnostic_present": True},
    )
    assert dpe_carrier_is_satisfied(proven, "dpe_nearer_neighbor_negative")
    # Without the redacted neighbour boolean the label is unprovable.
    assert not dpe_carrier_is_satisfied(
        _carrier_case("golden-address-001", facts=_ABSENT_FACTS),
        "dpe_nearer_neighbor_negative",
    )
    assert not dpe_carrier_is_satisfied(
        _carrier_case(
            "golden-address-001",
            facts={**_ABSENT_FACTS, "dpe_nearby_diagnostic_present": False},
        ),
        "dpe_nearer_neighbor_negative",
    )


def test_carrier_absent_does_not_require_the_nearby_diagnostic_invariant() -> None:
    plain = _carrier_case("golden-address-001", facts=_ABSENT_FACTS)
    assert dpe_carrier_is_satisfied(plain, "dpe_absent")
    # ...and a plain absence never satisfies the neighbour predicate by default.
    assert not dpe_carrier_is_satisfied(plain, "dpe_nearer_neighbor_negative")


def test_building_context_carrier_requires_an_unassigned_aggregate() -> None:
    assert dpe_carrier_is_satisfied(
        _carrier_case("golden-address-002", facts=_BUILDING_CONTEXT_FACTS),
        "dpe_multiple_or_building",
    )
    for inconsistent in (
        {**_BUILDING_CONTEXT_FACTS, "dpe_candidate_count": 1},
        {**_BUILDING_CONTEXT_FACTS, "dpe_building_count": 0},
        {**_BUILDING_CONTEXT_FACTS, "dpe_attribution_status": "absent"},
        {**_BUILDING_CONTEXT_FACTS, "dpe_inventory_presence": "absent"},
    ):
        assert not dpe_carrier_is_satisfied(
            _carrier_case("golden-address-003", facts=inconsistent),
            "dpe_multiple_or_building",
        )


def test_carrier_predicate_rejects_an_unknown_label() -> None:
    assert not dpe_carrier_is_satisfied(
        _carrier_case("golden-address-001", facts=_UNASSIGNED_FACTS),
        "dpe_probably_exact",
    )


def test_unknown_carrier_label_is_rejected_by_the_schema() -> None:
    entry = _carrier_case(
        "golden-address-001",
        facts=_UNASSIGNED_FACTS,
        carrier_for=["dpe_probably_exact"],
    )
    assert validate_case_entry(entry, _schema()) != []


def test_carrier_for_without_matching_evidence_is_rejected() -> None:
    # A case declaring a label its own invariants do not prove is tampering.
    entry = _carrier_case(
        "golden-address-001",
        facts=_ABSENT_FACTS,
        carrier_for=["dpe_nearer_neighbor_negative"],
    )
    errors = validate_case_entry(entry, _schema())
    assert any("carrier_for" in error for error in errors)


def test_carrier_for_cardinality_and_duplicates_are_rejected() -> None:
    schema = _schema()
    for bad in (
        [],
        ["dpe_unassigned", "dpe_unassigned"],
        "dpe_unassigned",
        [1],
    ):
        entry = _carrier_case(
            "golden-address-001", facts=_UNASSIGNED_FACTS, carrier_for=bad
        )
        assert validate_case_entry(entry, schema) != [], f"accepted {bad!r}"


def test_carrier_for_is_accepted_when_every_label_is_proven() -> None:
    entry = _carrier_case(
        "golden-address-001",
        facts={**_ABSENT_FACTS, "dpe_nearby_diagnostic_present": True},
        carrier_for=["dpe_absent", "dpe_nearer_neighbor_negative"],
    )
    assert validate_case_entry(entry, _schema()) == []


def _covered_carrier_pool() -> list[dict[str, Any]]:
    return [
        _carrier_case(
            "golden-address-001",
            facts=_UNASSIGNED_FACTS,
            carrier_for=["dpe_unassigned"],
            departement="44",
        ),
        _carrier_case(
            "golden-address-002",
            facts={**_ABSENT_FACTS, "dpe_nearby_diagnostic_present": True},
            carrier_for=["dpe_nearer_neighbor_negative", "dpe_absent"],
            departement="85",
        ),
        _carrier_case(
            "golden-address-003",
            facts=_BUILDING_CONTEXT_FACTS,
            carrier_for=["dpe_multiple_or_building"],
            departement="44",
        ),
    ]


def test_dpe_carrier_coverage_accepts_required_archetypes_over_44_and_85() -> None:
    assert validate_dpe_carrier_coverage(_covered_carrier_pool(), _real_schema()) == []


def test_dpe_carrier_coverage_does_not_require_optional_building_context() -> None:
    pool = _covered_carrier_pool()[:2]
    assert validate_dpe_carrier_coverage(pool, _real_schema()) == []


def test_dpe_carrier_coverage_requires_each_frozen_archetype() -> None:
    pool = _covered_carrier_pool()[:1]
    errors = validate_dpe_carrier_coverage(pool, _real_schema())
    assert any("missing_dpe_carrier_coverage" in error for error in errors)
    assert any("dpe_nearer_neighbor_negative" in error for error in errors)
    assert any("dpe_absent" in error for error in errors)


def test_dpe_carrier_department_coverage_requires_44_and_85() -> None:
    pool = _covered_carrier_pool()
    pool[1] = _carrier_case(
        "golden-address-002",
        facts={**_ABSENT_FACTS, "dpe_nearby_diagnostic_present": True},
        carrier_for=["dpe_nearer_neighbor_negative", "dpe_absent"],
        departement="44",
    )
    errors = validate_dpe_carrier_coverage(pool, _real_schema())
    assert any("missing_dpe_carrier_coverage" in error for error in errors)
    assert any("85" in error for error in errors)


def test_dpe_carrier_department_coverage_ignores_commune_carriers() -> None:
    pool = _covered_carrier_pool()
    pool[1]["type"] = "commune"
    pool[1]["case_id"] = "golden-commune-002"
    pool[1]["private_ref"] = "golden-commune-002"
    errors = validate_dpe_carrier_coverage(pool, _real_schema())
    assert any("85" in error for error in errors)


def _phase_51_manifest_patch() -> list[dict[str, Any]]:
    summary = _PHASE_51_SUMMARY.read_text(encoding="utf-8")
    section = summary.split("## manifest_patch réel", maxsplit=1)
    assert len(section) == 2, "51-09-SUMMARY.md must expose its manifest_patch"
    fenced = re.search(r"```json\s*(\[.*?\])\s*```", section[1], re.DOTALL)
    assert fenced is not None, "manifest_patch must be a fenced JSON array"
    patch = json.loads(fenced.group(1))
    assert isinstance(patch, list)
    return patch


def test_phase_51_manifest_patch_provenance_and_privacy_are_preserved() -> None:
    summary = _PHASE_51_SUMMARY.read_text(encoding="utf-8")
    run_row = next(
        line for line in summary.splitlines() if "Qualification post-amendement" in line
    )
    cells = [cell.strip().strip("`") for cell in run_row.strip("|").split("|")]
    assert "32454820399" in cells[1]
    assert cells[2] == cells[3]
    assert cells[2] == "c691779005e0d1acf80ab185542dc6210488ca9d"

    required_keys = {
        "case_id",
        "type",
        "departement",
        "code_insee",
        "category",
        "frozen_at",
        "source_millesimes",
        "invariants",
        "private_ref",
        "carrier_for",
    }
    patch = _phase_51_manifest_patch()
    assert len(patch) == 8
    assert all(set(entry) == required_keys for entry in patch)
    assert audit_manifest_keys(patch) == []
    patch_text = json.dumps(patch, ensure_ascii=False)
    assert audit_artifact_text(patch_text, "manifest_patch") == []
    # Historical V1 attribution evidence remains auditable, but cannot pass the
    # active V2 schema and thereby reintroduce ``exact``.
    assert any(validate_case_entry(entry, _real_schema()) for entry in patch)


def test_real_manifest_preserves_phase_51_facts_except_refrozen_dpe() -> None:
    manifest = json.loads(_REAL_MANIFEST.read_text(encoding="utf-8"))
    committed = {case["case_id"]: case for case in manifest["cases"]}
    patch = _phase_51_manifest_patch()
    assert {entry["case_id"] for entry in patch} == {
        "golden-address-001",
        "golden-address-002",
        "golden-address-003",
        "golden-address-004",
        "golden-address-005",
        "golden-address-007",
        "golden-address-009",
        "golden-address-011",
    }
    for entry in patch:
        current = json.loads(json.dumps(committed[entry["case_id"]]))
        historical = json.loads(json.dumps(entry))
        current.pop("carrier_for", None)
        historical.pop("carrier_for", None)
        current["invariants"].pop("dpe", None)
        historical["invariants"].pop("dpe", None)
        if entry["case_id"] == "golden-address-007":
            assert current["frozen_at"] == "2026-09-05"
            assert current["category"] == "complete"
            assert current["invariants"]["api"]["dpe_state"] == "value"
            assert (
                committed[entry["case_id"]]["invariants"]["dpe"]["dpe_building_count"]
                == 0
            )
            assert committed[entry["case_id"]]["carrier_for"] == ["dpe_unassigned"]
            historical["frozen_at"] = current["frozen_at"]
            historical["category"] = "complete"
            historical["invariants"]["api"]["dpe_state"] = "value"
        assert current == historical


def test_real_manifest_contains_only_v2_non_attributive_dpe_facts() -> None:
    manifest = json.loads(_REAL_MANIFEST.read_text(encoding="utf-8"))
    addresses = [case for case in manifest["cases"] if case["type"] == "address"]
    assert all(
        case["invariants"]["dpe"]["dpe_attribution_status"]
        in {"unassigned", "absent", "error"}
        for case in addresses
    )
    assert all(
        case["invariants"]["dpe"]["dpe_primary_diagnostic_present"] is False
        for case in addresses
    )
    assert {
        case["case_id"]: case["invariants"]["dpe"]["dpe_inventory_presence"]
        for case in addresses
    } == {
        "golden-address-001": "present",
        "golden-address-002": "absent",
        "golden-address-003": "absent",
        "golden-address-004": "absent",
        "golden-address-005": "absent",
        "golden-address-007": "present",
        "golden-address-009": "absent",
        "golden-address-011": "present",
    }
    serialized = json.dumps(addresses)
    assert '"dpe_exact"' not in serialized
    assert '"exact"' not in serialized


def test_007_keeps_house_division_without_inventing_building_context() -> None:
    manifest = json.loads(_REAL_MANIFEST.read_text(encoding="utf-8"))
    schema = _real_schema()
    case = next(
        entry for entry in manifest["cases"] if entry["case_id"] == "golden-address-007"
    )

    assert case["case_id"] in schema["pinned_cases"]
    assert case["invariants"]["db"]["dpe_appartements_multiples"] is True
    assert case["invariants"]["db"]["copro_row_present"] is False
    assert case["invariants"]["dpe"]["dpe_building_count"] == 0
    assert case["carrier_for"] == ["dpe_unassigned"]
    assert "dpe_multiple_or_building" in schema["allowed_carriers"]
    assert "dpe_multiple_or_building" not in schema["required_carriers"]


def test_real_manifest_passes_the_armed_final_dpe_gate() -> None:
    manifest = json.loads(_REAL_MANIFEST.read_text(encoding="utf-8"))
    schema = _real_schema()
    assert schema["final_dpe_gate"] is True
    assert validate_manifest(manifest, schema) == []
    assert validate_manifest(manifest, schema, final_dpe_gate=True) == []


def test_armed_final_gate_rejects_carrier_or_department_eviction() -> None:
    manifest = json.loads(_REAL_MANIFEST.read_text(encoding="utf-8"))
    schema = _real_schema()
    for label in schema["required_carriers"]:
        mutated = json.loads(json.dumps(manifest))
        for case in mutated["cases"]:
            labels = case.get("carrier_for", [])
            if label in labels:
                remaining = [candidate for candidate in labels if candidate != label]
                if remaining:
                    case["carrier_for"] = remaining
                else:
                    case.pop("carrier_for")
        errors = validate_manifest(mutated, schema)
        assert any("missing_dpe_carrier_coverage" in error for error in errors)
        assert any(label in error for error in errors)

    without_85_carrier = json.loads(json.dumps(manifest))
    for case in without_85_carrier["cases"]:
        if case["type"] == "address" and case["departement"] == "85":
            case.pop("carrier_for", None)
    errors = validate_manifest(without_85_carrier, schema)
    assert any("missing_dpe_carrier_coverage" in error for error in errors)
    assert any("85" in error for error in errors)


def test_forbidden_keys_are_unchanged_by_the_carrier_amendment() -> None:
    schema = _real_schema()
    for key in (
        "ban_id",
        "identifiant_ban",
        "adresse",
        "latitude",
        "longitude",
        "coordonnees",
        "api_key",
        "secret",
        "token",
        "payload",
        "response_body",
    ):
        assert key in schema["forbidden_keys"]
    assert set(schema["forbidden_keys"]) == set(FORBIDDEN_KEYS)


def test_phase_43_48_pins_and_requirements_survive_the_carrier_amendment() -> None:
    schema = _real_schema()
    assert set(schema["required_categories"]) == {
        "complete",
        "no_dvf",
        "no_dpe",
        "dataset_out_of_coverage",
    }
    assert schema["cardinality"] == {
        "commune": {"min": 3, "max": 5},
        "address": {"min": 5, "max": 10},
    }
    inherited_pins = set(schema["pinned_cases"]) - {"golden-address-011"}
    assert len(inherited_pins) == 10
    assert "golden-address-011" in schema["pinned_cases"]
    assert len(schema["pinned_cases"]) == 11
    manifest = json.loads(_REAL_MANIFEST.read_text(encoding="utf-8"))
    committed = {case["case_id"]: case for case in manifest["cases"]}
    inherited_addresses = sum(
        committed[case_id]["type"] == "address" for case_id in inherited_pins
    )
    assert inherited_addresses == 7
    assert (
        sum(
            committed[case_id]["type"] == "address"
            for case_id in schema["pinned_cases"]
        )
        == 8
    )
    assert len(schema["required_observations"]) == 4


# --- Phase 51 Task 2: redacted qualification of the private pool ---------------
#
# Synthetic sentinels only. They are deliberately shaped like the things that
# must NEVER leave the protected environment, so the assertions below are real
# negative controls rather than a vocabulary check.
_SENTINEL_ADDRESS = "12 rue de la Paix"
_SENTINEL_BAN = "44991_0001_00001"
_SENTINEL_SECRET = "dc_live_goldensentinelvalue0001"
_SENTINEL_DPE_NUMBER = "2291E0123456X"
_PRIVATE_SENTINELS = (
    _SENTINEL_ADDRESS,
    _SENTINEL_BAN,
    _SENTINEL_SECRET,
    _SENTINEL_DPE_NUMBER,
)
_PARCEL = "44991000AB0001"


class _JsonResponse:
    def __init__(
        self, payload: dict[str, Any], *, cache_control: str | None = None
    ) -> None:
        self._payload = payload
        self.headers = (
            {"cache-control": cache_control} if cache_control is not None else {}
        )

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _QualificationClient:
    """Serve the resolve, proximity and documentary routes, GET-only."""

    def __init__(
        self,
        attribution: dict[str, Any] | None,
        *,
        inventory: dict[str, Any] | None,
        dpe_dataset_state: dict[str, Any],
        nearby_rows: list[dict[str, Any]] | None = None,
        route_inventory: dict[str, Any] | None = None,
        route_document: dict[str, Any] | None = None,
        cache_control: str = "no-store",
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._attribution = attribution
        self._inventory = inventory
        self._dpe_dataset_state = dpe_dataset_state
        self._nearby_rows = nearby_rows if nearby_rows is not None else []
        self._route_inventory = route_inventory
        self._route_document = route_document
        self._cache_control = cache_control

    def get(self, path: str, params: dict[str, Any] | None = None) -> _JsonResponse:
        self.calls.append((path, dict(params or {})))
        if path == "/v1/dpe":
            return _JsonResponse({"donnees": list(self._nearby_rows)})
        if path == "/v1/bien/dpe/inventaire":
            if self._route_inventory is None:
                raise AssertionError("unexpected documentary inventory call")
            return _JsonResponse(
                dict(self._route_inventory), cache_control=self._cache_control
            )
        if path == "/v1/bien/dpe/document":
            if self._route_document is None:
                raise AssertionError("unexpected documentary document call")
            return _JsonResponse(
                dict(self._route_document), cache_control=self._cache_control
            )
        if (params or {}).get("datasets") == "commune":
            payload = self._resolve_payload()
            return _JsonResponse(
                {
                    "donnees": [
                        {
                            key: payload.get(key)
                            for key in ("id_parcelle", "code_insee", "ban_id")
                        }
                        | {"datasets": {"commune": {}}}
                    ]
                }
            )
        return _JsonResponse({"donnees": [self._resolve_payload()]})

    def _resolve_payload(self) -> dict[str, Any]:
        # Phase 58.1: the document inventory stays independent from the coarse
        # datasets block and never promotes a diagnostic into the Fiche.
        datasets: dict[str, Any] = {"dvf": [{"prix_m2": 1}]}
        return {
            "id_parcelle": _PARCEL,
            # Private material the runner reads in memory and must never emit.
            "adresse": _SENTINEL_ADDRESS,
            "ban_id": _SENTINEL_BAN,
            "latitude": 47.218371,
            "longitude": -1.553621,
            "datasets": datasets,
            "dataset_states": {"dpe": dict(self._dpe_dataset_state)},
            "dpe_inventory": self._inventory,
            "dpe_attribution": self._attribution,
        }


def _qualified_entry(
    case_id: str = "golden-address-001",
    *,
    dpe_state: str = "out_of_coverage",
    inventory_presence: str | None,
) -> dict[str, Any]:
    entry = _valid_case(
        case_id=case_id,
        private_ref=case_id,
        type="address",
        departement="44",
        code_insee="44991",
    )
    entry["invariants"] = {
        "db": {
            "has_commune_row": True,
            "superficie_present": True,
            "parcelle_present": True,
            "copro_row_present": True,
            "dpe_appartements_multiples": True,
            "dvf_appartements_present": True,
            "dpe_rattachement_ok": True,
        },
        "api": {"dvf_state": "value", "dpe_state": dpe_state},
        "mapper": {"prix_m2_unit": "euro_per_m2"},
    }
    if inventory_presence is not None:
        entry["invariants"]["dpe"] = {"dpe_inventory_presence": inventory_presence}
    return entry


def _qualified_private() -> dict[str, Any]:
    return {
        "golden-address-001": {
            "id_parcelle": _PARCEL,
            "ban_id": "44991_0001_00002",
            "query": _SENTINEL_ADDRESS,
        }
    }


_UNASSIGNED_ATTRIBUTION: dict[str, Any] = {
    "version": 2,
    "status": "unassigned",
    "reason": "dwelling_identity_unproven",
    "diagnostic": None,
    "candidates": [],
    "building_context": None,
    "graph_state": "not_checked",
}
_ABSENT_ATTRIBUTION: dict[str, Any] = {
    "version": 2,
    "status": "absent",
    "reason": "not_reported",
    "diagnostic": None,
    "candidates": [],
    "building_context": None,
    "graph_state": "not_checked",
}
_IDENTITY_UNAVAILABLE_ATTRIBUTION: dict[str, Any] = {
    **_ABSENT_ATTRIBUTION,
    "reason": "identity_unavailable",
}
_BUILDING_CONTEXT_ATTRIBUTION: dict[str, Any] = {
    "version": 2,
    "status": "unassigned",
    "reason": "dwelling_identity_unproven",
    "diagnostic": None,
    "candidates": [],
    "building_context": {
        "count": 9,
        "distribution": {"D": 5, "E": 4},
        "truncated": True,
    },
    "graph_state": "not_checked",
}
_ERROR_ATTRIBUTION: dict[str, Any] = {
    "version": 2,
    "status": "error",
    "reason": "temporary_error",
    "diagnostic": None,
    "candidates": [],
    "building_context": None,
    "graph_state": "not_checked",
}


def _inventory(presence: str) -> dict[str, Any]:
    common: dict[str, Any] = {
        "version": 2,
        "association": "source_ban_equality",
        "address_check": "unverified",
        "source": "ADEME",
        "source_coverage": "unknown",
        "graph_state": "not_checked",
        "imported_at": None,
        "context_ref": None,
        "items": [],
        "page_size": 20,
        "has_more": False,
        "next_cursor": None,
        "read_state": "complete",
    }
    if presence == "present":
        return {
            **common,
            "state": "ready",
            "context_ref": "opaque-context",
            "items": [{"private_source_value": _SENTINEL_DPE_NUMBER}],
            "total": 2,
            "distribution": {
                **dict.fromkeys("ABCEFG", 0),
                "D": 1,
                "unknown": 1,
            },
        }
    if presence == "absent":
        return {
            **common,
            "state": "absent",
            "context_ref": "opaque-context",
            "total": 0,
            "distribution": {**dict.fromkeys("ABCDEFG", 0), "unknown": 0},
        }
    if presence == "unknown":
        return {
            **common,
            "state": "identity_unavailable",
            "total": None,
            "distribution": {**dict.fromkeys("ABCDEFG", 0), "unknown": 0},
        }
    if presence == "unavailable":
        return {
            **common,
            "state": "unavailable",
            "total": None,
            "distribution": {**dict.fromkeys("ABCDEFG", 0), "unknown": 0},
            "read_state": "partial",
        }
    raise AssertionError(f"unsupported synthetic presence: {presence}")


_DOCUMENT_ITEM: dict[str, Any] = {
    "document_ref": "opaque-document-reference",
    "type_batiment": "appartement",
    "surface_habitable": 42.5,
    "etiquette_dpe": "D",
    "etiquette_ges": "E",
    "date_etablissement_dpe": "2024-02-03",
    "date_fin_validite_dpe": "2034-02-02",
}


def _route_inventory(presence: str) -> dict[str, Any]:
    inventory = _inventory(presence)
    inventory["page_size"] = 1
    if presence == "present":
        inventory["items"] = [dict(_DOCUMENT_ITEM)]
        inventory["has_more"] = True
        inventory["next_cursor"] = "opaque-next-cursor"
    return inventory


def _route_document() -> dict[str, Any]:
    return {
        "version": 2,
        "association": "source_ban_equality",
        "address_check": "unverified",
        "source": "ADEME",
        "source_coverage": "unknown",
        "graph_state": "not_checked",
        "imported_at": None,
        "context_ref": "opaque-context",
        **_DOCUMENT_ITEM,
        "annee_construction": 1975,
        "periode_construction": "1971 - 1977",
        "type_installation_chauffage": "individuel",
        "type_energie_principale_chauffage": "électricité",
        "qualite_isolation_enveloppe": "moyenne",
        "conso_5_usages_ep": 218.4,
        "emission_ges_5_usages": 7.2,
        "cout_total_5_usages": 1130.0,
        "validity_by_date": "not_expired_by_date",
    }


def _coarse_dpe_state(attribution: dict[str, Any] | None) -> dict[str, Any]:
    status = attribution.get("status") if isinstance(attribution, dict) else None
    if status == "unassigned":
        return {
            "dataset": "dpe",
            "presence": "value",
            "absence_reason": None,
            "raison": None,
        }
    if status == "error":
        return {
            "dataset": "dpe",
            "presence": "error",
            "absence_reason": "temporary_error",
            "raison": "Source temporairement indisponible.",
        }
    return {
        "dataset": "dpe",
        "presence": "absent",
        "absence_reason": "not_reported",
        "raison": "Aucune donnee rapportee pour ce point.",
    }


def _run_qualification(
    attribution: dict[str, Any] | None,
    *,
    inventory_presence: str | None,
    observed_inventory_presence: str | None = None,
    observed_inventory: dict[str, Any] | None = None,
    dpe_state: str | None = None,
    nearby_rows: list[dict[str, Any]] | None = None,
    route_inventory: dict[str, Any] | None = None,
    route_document: dict[str, Any] | None = None,
    cache_control: str = "no-store",
    documentary_match: dict[str, bool] | None = None,
    lifecycle_match: bool = True,
) -> tuple[dict[str, Any], _QualificationClient]:
    observed_presence = observed_inventory_presence or (
        inventory_presence
        if inventory_presence in {"present", "absent", "unknown", "unavailable"}
        else "present"
    )
    inventory = (
        observed_inventory
        if observed_inventory is not None
        else _inventory(observed_presence)
    )
    coarse = _coarse_dpe_state(attribution)
    expected_dpe_state = dpe_state or (
        "value" if coarse["presence"] == "value" else coarse["absence_reason"]
    )
    client = _QualificationClient(
        attribution,
        inventory=inventory,
        dpe_dataset_state=coarse,
        nearby_rows=nearby_rows,
        route_inventory=(
            route_inventory
            if route_inventory is not None
            else (
                _route_inventory(observed_presence)
                if observed_presence in {"present", "absent"}
                else None
            )
        ),
        route_document=(
            route_document
            if route_document is not None
            else (_route_document() if observed_presence == "present" else None)
        ),
        cache_control=cache_control,
    )
    entry = _qualified_entry(
        dpe_state=expected_dpe_state,
        inventory_presence=inventory_presence,
    )
    if inventory_presence in {"present", "absent", "unknown"}:
        try:
            expected_facts = dpe_attribution_facts({"dpe_attribution": attribution})
        except GoldenSecurityError:
            pass
        else:
            expected_facts["dpe_inventory_presence"] = inventory_presence
            expected_facts["dpe_nearby_diagnostic_present"] = (
                bool(nearby_rows) if (attribution == _ABSENT_ATTRIBUTION) else False
            )
            entry["invariants"]["dpe"] = expected_facts
    result = _verify_case(
        _FakeMultiLogementConnection(
            documentary_match=documentary_match, lifecycle_match=lifecycle_match
        ),
        client,  # type: ignore[arg-type]
        entry,
        _qualified_private(),
        resolve_logging_redacted=True,
    )
    return result, client


def test_dpe_attribution_facts_read_the_public_decision() -> None:
    assert dpe_attribution_facts(
        {"dpe_attribution": dict(_UNASSIGNED_ATTRIBUTION)}
    ) == {
        "dpe_attribution_status": "unassigned",
        "dpe_primary_diagnostic_present": False,
        "dpe_candidate_count": 0,
        "dpe_building_count": 0,
        "dpe_truncated": False,
    }
    assert dpe_attribution_facts(
        {"dpe_attribution": dict(_BUILDING_CONTEXT_ATTRIBUTION)}
    ) == {
        "dpe_attribution_status": "unassigned",
        "dpe_primary_diagnostic_present": False,
        "dpe_candidate_count": 0,
        "dpe_building_count": 9,
        "dpe_truncated": True,
    }


def test_dpe_attribution_facts_reject_a_malformed_or_absent_block() -> None:
    for payload in (
        {},
        {"dpe_attribution": None},
        {"dpe_attribution": {"status": "probably_exact"}},
        {"dpe_attribution": {**_UNASSIGNED_ATTRIBUTION, "version": 1}},
        {"dpe_attribution": {**_UNASSIGNED_ATTRIBUTION, "status": "exact"}},
        {
            "dpe_attribution": {
                **_UNASSIGNED_ATTRIBUTION,
                "diagnostic": {"numero_dpe": _SENTINEL_DPE_NUMBER},
            }
        },
    ):
        with pytest.raises(GoldenSecurityError):
            dpe_attribution_facts(payload)


def test_dpe_dataset_state_comes_from_dataset_states_never_datasets() -> None:
    absent = {
        "datasets": {"dpe": [{"numero_dpe": _SENTINEL_DPE_NUMBER}]},
        "dataset_states": {
            "dpe": {
                "dataset": "dpe",
                "presence": "absent",
                "absence_reason": "not_reported",
                "raison": "Aucune donnee rapportee pour ce point.",
            }
        },
    }
    present = {
        "datasets": {"dpe": None},
        "dataset_states": {
            "dpe": {
                "dataset": "dpe",
                "presence": "value",
                "absence_reason": None,
                "raison": None,
            }
        },
    }

    assert _dataset_state(absent, "dpe", "address") == "not_reported"
    assert _dataset_state(present, "dpe", "address") == "value"


@pytest.mark.parametrize(
    ("presence", "expected"),
    [("present", "present"), ("absent", "absent"), ("unknown", "unknown")],
)
def test_dpe_inventory_presence_is_derived_from_the_public_inventory(
    presence: str,
    expected: str,
) -> None:
    block = _inventory(presence)
    assert dpe_inventory_presence({"dpe_inventory": block}) == expected
    assert _SENTINEL_DPE_NUMBER not in json.dumps(expected)


def test_dpe_inventory_presence_never_reads_items_or_opaque_references() -> None:
    class UnreadableValue:
        def __iter__(self):
            raise AssertionError("golden presence must not iterate private values")

        def __len__(self):
            raise AssertionError("golden presence must not count private values")

        def __bool__(self):
            raise AssertionError("golden presence must not inspect private values")

        def __eq__(self, other: object) -> bool:
            raise AssertionError("golden presence must not compare private values")

        def __str__(self) -> str:
            raise AssertionError("golden presence must not render private values")

    block = _inventory("present")
    block["context_ref"] = UnreadableValue()
    block["items"] = UnreadableValue()
    block["next_cursor"] = UnreadableValue()

    assert dpe_inventory_presence({"dpe_inventory": block}) == "present"


def test_dpe_inventory_presence_accepts_unbounded_strict_integer_counts() -> None:
    block = _inventory("present")
    block["total"] = 121
    block["distribution"] = {
        **dict.fromkeys("ABCEFG", 0),
        "D": 120,
        "unknown": 1,
    }

    assert dpe_inventory_presence({"dpe_inventory": block}) == "present"


@pytest.mark.parametrize("bad_count", [True, False, 1.0, 2.5])
def test_dpe_inventory_presence_rejects_non_strict_integer_counts(
    bad_count: bool | float,
) -> None:
    bad_total = _inventory("present")
    bad_total["total"] = bad_count
    with pytest.raises(GoldenSecurityError):
        dpe_inventory_presence({"dpe_inventory": bad_total})

    bad_absent_total = _inventory("absent")
    bad_absent_total["total"] = bad_count
    with pytest.raises(GoldenSecurityError):
        dpe_inventory_presence({"dpe_inventory": bad_absent_total})

    bad_distribution = _inventory("present")
    bad_distribution["distribution"] = {
        **bad_distribution["distribution"],
        "D": bad_count,
    }
    with pytest.raises(GoldenSecurityError):
        dpe_inventory_presence({"dpe_inventory": bad_distribution})


def test_dpe_inventory_presence_rejects_missing_or_extra_root_keys() -> None:
    missing = _inventory("present")
    missing.pop("source")
    extra = {**_inventory("present"), "private_source_value": "hidden"}

    for block in (missing, extra):
        with pytest.raises(GoldenSecurityError, match="closed dpe_inventory"):
            dpe_inventory_presence({"dpe_inventory": block})


@pytest.mark.parametrize(
    ("presence", "read_state"),
    [
        ("present", "partial"),
        ("absent", "partial"),
        ("unknown", "partial"),
        ("present", "incomplete"),
    ],
)
def test_dpe_inventory_presence_rejects_partial_or_invalid_read_state(
    presence: str,
    read_state: str,
) -> None:
    block = _inventory(presence)
    block["read_state"] = read_state

    with pytest.raises(GoldenSecurityError):
        dpe_inventory_presence({"dpe_inventory": block})


@pytest.mark.parametrize(
    "block",
    [
        None,
        {**_inventory("present"), "version": 1},
        {**_inventory("present"), "total": 0},
        {**_inventory("present"), "distribution": {"D": 1}},
        {**_inventory("absent"), "total": 1},
        {**_inventory("unknown"), "total": 0},
        _inventory("unavailable"),
    ],
)
def test_dpe_inventory_presence_fails_closed_on_missing_incoherent_or_outage(
    block: dict[str, Any] | None,
) -> None:
    with pytest.raises(GoldenSecurityError):
        dpe_inventory_presence({"dpe_inventory": block})


@pytest.mark.parametrize(
    ("attribution", "manifest_presence", "observed_presence"),
    [
        (_UNASSIGNED_ATTRIBUTION, "present", "absent"),
        (_ABSENT_ATTRIBUTION, "absent", "present"),
        (_IDENTITY_UNAVAILABLE_ATTRIBUTION, "unknown", "absent"),
        (_UNASSIGNED_ATTRIBUTION, "present", "unknown"),
    ],
)
def test_dpe_inventory_presence_drift_blocks_the_case_in_every_direction(
    attribution: dict[str, Any],
    manifest_presence: str,
    observed_presence: str,
) -> None:
    result, _ = _run_qualification(
        attribution,
        inventory_presence=manifest_presence,
        observed_inventory_presence=observed_presence,
    )
    assert result["ok"] is False
    assert result["reasons"] == [
        "golden_security: live DPE inventory presence diverges from manifest: "
        f"expected={manifest_presence}, observed={observed_presence}"
    ]
    assert "dpe_qualification" not in result


def test_source_divergence_blocks_before_reporting_stale_presence() -> None:
    result, _ = _run_qualification(
        _ABSENT_ATTRIBUTION,
        inventory_presence="unknown",
        observed_inventory_presence="absent",
        documentary_match={
            "inventory_aggregate_equal": False,
            "inventory_item_equal": False,
            "document_equal": True,
        },
    )
    assert result["ok"] is False
    assert result["reasons"] == [
        "golden_security: DPE documentary projection diverges from source"
    ]
    assert "dpe_documentary_proof" not in result
    assert "manifest_patch" not in result


def test_unknown_inventory_is_accepted_without_carrier() -> None:
    result, client = _run_qualification(
        _IDENTITY_UNAVAILABLE_ATTRIBUTION,
        inventory_presence="unknown",
    )

    assert result["ok"] is True
    qualification = result["dpe_qualification"]
    assert qualification["dpe_inventory_presence"] == "unknown"
    assert qualification["dpe_attribution_status"] == "absent"
    assert qualification["carrier_for"] == []
    assert result["manifest_patch"]["invariants"]["dpe"] == {
        "dpe_inventory_presence": "unknown",
        "dpe_attribution_status": "absent",
        "dpe_primary_diagnostic_present": False,
        "dpe_candidate_count": 0,
        "dpe_building_count": 0,
        "dpe_truncated": False,
        "dpe_nearby_diagnostic_present": False,
    }
    assert all(path != "/v1/dpe" for path, _ in client.calls)


@pytest.mark.parametrize(
    ("attribution", "inventory_presence"),
    [
        (_UNASSIGNED_ATTRIBUTION, "absent"),
        (_ABSENT_ATTRIBUTION, "present"),
    ],
)
def test_valid_attribution_with_incoherent_inventory_blocks_the_case(
    attribution: dict[str, Any],
    inventory_presence: str,
) -> None:
    result, _ = _run_qualification(
        attribution,
        inventory_presence=inventory_presence,
    )
    assert result["ok"] is False
    assert result["reasons"] == [
        "golden_security: DPE inventory and attribution are inconsistent"
    ]


def test_dpe_qualification_reads_inventory_but_not_datasets_dpe() -> None:
    result, _ = _run_qualification(
        _BUILDING_CONTEXT_ATTRIBUTION, inventory_presence="present"
    )
    assert result["ok"] is True
    assert result["dpe_qualification"]["dpe_attribution_status"] == "unassigned"
    assert result["dpe_qualification"]["carrier_for"] == ["dpe_multiple_or_building"]

    unassigned, _ = _run_qualification(
        _UNASSIGNED_ATTRIBUTION, inventory_presence="present"
    )
    assert unassigned["dpe_qualification"]["carrier_for"] == ["dpe_unassigned"]


def test_dpe_nearby_probe_runs_only_for_an_exact_absence() -> None:
    _, unassigned_client = _run_qualification(
        _UNASSIGNED_ATTRIBUTION, inventory_presence="present"
    )
    assert all(path != "/v1/dpe" for path, _ in unassigned_client.calls)

    _, absent_client = _run_qualification(
        _ABSENT_ATTRIBUTION,
        inventory_presence="absent",
        nearby_rows=[{"numero_dpe": _SENTINEL_DPE_NUMBER}],
    )
    probes = [params for path, params in absent_client.calls if path == "/v1/dpe"]
    assert len(probes) == 1

    _, unavailable_client = _run_qualification(
        _IDENTITY_UNAVAILABLE_ATTRIBUTION, inventory_presence="unknown"
    )
    assert all(path != "/v1/dpe" for path, _ in unavailable_client.calls)


def test_dpe_nearby_diagnostic_projects_only_a_boolean() -> None:
    result, _ = _run_qualification(
        _ABSENT_ATTRIBUTION,
        inventory_presence="absent",
        nearby_rows=[{"numero_dpe": _SENTINEL_DPE_NUMBER, "distance_m": 8.2}],
    )
    qualification = result["dpe_qualification"]
    assert qualification["dpe_nearby_diagnostic_present"] is True
    assert set(qualification["carrier_for"]) == {
        "dpe_absent",
        "dpe_nearer_neighbor_negative",
    }
    rendered = json.dumps(result, ensure_ascii=False)
    for sentinel in _PRIVATE_SENTINELS:
        assert sentinel not in rendered
    assert "distance" not in rendered


def test_dpe_absence_without_a_neighbour_carries_only_the_plain_label() -> None:
    result, _ = _run_qualification(
        _ABSENT_ATTRIBUTION, inventory_presence="absent", nearby_rows=[]
    )
    qualification = result["dpe_qualification"]
    assert qualification["dpe_nearby_diagnostic_present"] is False
    assert qualification["carrier_for"] == ["dpe_absent"]


def test_dpe_error_state_yields_no_carrier_and_fails_the_case_redacted() -> None:
    result, _ = _run_qualification(
        _ERROR_ATTRIBUTION,
        inventory_presence="unknown",
        observed_inventory_presence="unavailable",
    )
    assert result["ok"] is False
    assert "dpe_qualification" not in result
    rendered = json.dumps(result, ensure_ascii=False)
    for sentinel in _PRIVATE_SENTINELS:
        assert sentinel not in rendered
    assert audit_artifact_text(rendered, source="report") == []


def test_dpe_malformed_attribution_fails_the_case_without_echoing_input() -> None:
    result, _ = _run_qualification(
        {"status": "probably_exact"}, inventory_presence="present"
    )
    assert result["ok"] is False
    rendered = json.dumps(result, ensure_ascii=False)
    for sentinel in _PRIVATE_SENTINELS:
        assert sentinel not in rendered


@pytest.mark.parametrize("inventory_presence", [None, "inferred"])
def test_dpe_qualification_requires_explicit_inventory_presence(
    inventory_presence: str | None,
) -> None:
    result, client = _run_qualification(
        _UNASSIGNED_ATTRIBUTION,
        inventory_presence=inventory_presence,
    )
    assert result["ok"] is False
    assert "dpe_qualification" not in result
    assert "manifest_patch" not in result
    assert all(path != "/v1/dpe" for path, _ in client.calls)
    assert result["reasons"] == [
        "golden_security: golden DPE inventory presence is missing or invalid"
    ]


def test_dpe_qualification_carries_only_allowlisted_keys() -> None:
    result, _ = _run_qualification(
        _BUILDING_CONTEXT_ATTRIBUTION, inventory_presence="present"
    )
    qualification = result["dpe_qualification"]
    assert set(qualification) == {
        "case_id",
        "departement",
        "dpe_inventory_presence",
        "dpe_attribution_status",
        "dpe_primary_diagnostic_present",
        "dpe_candidate_count",
        "dpe_building_count",
        "dpe_truncated",
        "dpe_nearby_diagnostic_present",
        "carrier_for",
    }
    assert qualification["case_id"] == "golden-address-001"
    assert qualification["departement"] == "44"
    assert qualification["carrier_for"] == ["dpe_multiple_or_building"]
    assert audit_manifest_keys(qualification) == []


def test_dpe_manifest_patch_is_a_complete_schema_valid_entry() -> None:
    result, _ = _run_qualification(
        _BUILDING_CONTEXT_ATTRIBUTION, inventory_presence="present"
    )
    patch = result["manifest_patch"]
    assert set(patch) == {
        "case_id",
        "type",
        "departement",
        "code_insee",
        "category",
        "frozen_at",
        "source_millesimes",
        "invariants",
        "private_ref",
        "carrier_for",
    }
    assert patch["private_ref"] == patch["case_id"]
    assert patch["invariants"]["dpe"]["dpe_candidate_count"] == 0
    assert patch["invariants"]["dpe"]["dpe_building_count"] == 9
    assert patch["invariants"]["dpe"]["dpe_inventory_presence"] == "present"
    assert patch["carrier_for"] == ["dpe_multiple_or_building"]
    assert validate_case_entry(patch, _real_schema()) == []
    assert audit_manifest_keys(patch) == []
    assert audit_artifact_text(json.dumps(patch, ensure_ascii=False), "patch") == []


def test_dpe_manifest_patch_is_blocked_by_an_unstable_category() -> None:
    # An outage never becomes a frozen category: the patch blocks instead of
    # handing Plan 51-10 a case labelled from a temporary failure.
    result, _ = _run_qualification(
        _BUILDING_CONTEXT_ATTRIBUTION, inventory_presence="present"
    )
    observation = {
        "db": {"has_commune_row": True},
        "api": {"dvf_state": "temporary_error", "dpe_state": "value"},
    }
    with pytest.raises(GoldenSecurityError, match="unstable category"):
        build_manifest_patch_entry(
            _qualified_entry(inventory_presence="present"),
            observation,
            {},
            result["dpe_qualification"],
        )


def test_dpe_manifest_patch_is_blocked_by_a_forbidden_key() -> None:
    result, _ = _run_qualification(
        _BUILDING_CONTEXT_ATTRIBUTION, inventory_presence="present"
    )
    entry = _qualified_entry(inventory_presence="present")
    # A mapper block smuggling an address-shaped key must block the whole
    # qualification rather than reach the report.
    entry["invariants"]["mapper"] = {
        "prix_m2_unit": "euro_per_m2",
        "adresse_exacte": _SENTINEL_ADDRESS,
    }
    with pytest.raises(GoldenSecurityError):
        build_manifest_patch_entry(
            entry,
            {"db": {"has_commune_row": True}, "api": {"dvf_state": "value"}},
            {},
            result["dpe_qualification"],
        )


def test_dpe_commune_case_is_not_qualified() -> None:
    entry = _valid_case()
    result = _verify_case(
        _FakeMultiLogementConnection(),
        _QualificationClient(
            None,
            inventory=None,
            dpe_dataset_state=_coarse_dpe_state(None),
        ),  # type: ignore[arg-type]
        entry,
        {"golden-commune-001": {"id_parcelle": _PARCEL}},
        resolve_logging_redacted=True,
    )
    assert "dpe_qualification" not in result
    assert "manifest_patch" not in result


def test_dpe_report_lifts_qualification_and_patch_to_the_top_level(
    tmp_path: Path, monkeypatch
) -> None:
    report_path = tmp_path / "golden-live-report.redacted.json"
    monkeypatch.setattr("scripts.verify_golden_cases.REPORT_PATH", report_path)
    result, _ = _run_qualification(
        _BUILDING_CONTEXT_ATTRIBUTION, inventory_presence="present"
    )
    _write_report(
        [{k: v for k, v in result.items() if k not in _LIFTED_REPORT_KEYS}],
        True,
        qualification=[result["dpe_qualification"]],
        manifest_patch=[result["manifest_patch"]],
    )
    written = json.loads(report_path.read_text(encoding="utf-8"))
    assert written["redacted"] is True
    assert len(written["dpe_qualification"]) == 1
    assert len(written["manifest_patch"]) == 1
    text = report_path.read_text(encoding="utf-8")
    for sentinel in _PRIVATE_SENTINELS:
        assert sentinel not in text
    assert audit_artifact_text(text, "report") == []


def test_dpe_qualification_get_only_and_read_only_are_preserved(capsys) -> None:
    result, client = _run_qualification(
        _ABSENT_ATTRIBUTION,
        inventory_presence="absent",
        nearby_rows=[{"numero_dpe": _SENTINEL_DPE_NUMBER}],
    )
    assert {path for path, _ in client.calls} == {
        "/v1/resolve",
        "/v1/dpe",
        "/v1/bien/dpe/inventaire",
    }
    captured = capsys.readouterr()
    for sentinel in _PRIVATE_SENTINELS:
        assert sentinel not in captured.out
        assert sentinel not in captured.err
    assert result["ok"] is True


def test_dpe_documentary_routes_match_the_exact_ban_source() -> None:
    result, client = _run_qualification(
        _BUILDING_CONTEXT_ATTRIBUTION,
        inventory_presence="present",
    )
    assert [path for path, _ in client.calls] == [
        "/v1/resolve",
        "/v1/resolve",
        "/v1/bien/dpe/inventaire",
        "/v1/bien/dpe/document",
    ]
    assert result["dpe_documentary_proof"] == {
        "inventory_route": "verified",
        "document_route": "verified",
        "source_fields_equal": True,
        "no_store": True,
    }
    rendered = json.dumps(result, ensure_ascii=False)
    for private in (*_PRIVATE_SENTINELS, "opaque-context", "opaque-document"):
        assert private not in rendered
    assert audit_artifact_text(rendered, "documentary-proof") == []


def test_empty_inventory_exercises_only_the_inventory_route() -> None:
    result, client = _run_qualification(
        _ABSENT_ATTRIBUTION,
        inventory_presence="absent",
    )
    paths = [path for path, _ in client.calls]
    assert "/v1/bien/dpe/inventaire" in paths
    assert "/v1/bien/dpe/document" not in paths
    assert result["dpe_documentary_proof"] == {
        "inventory_route": "verified",
        "document_route": "not_applicable_empty_inventory",
        "source_fields_equal": True,
        "no_store": True,
    }


def test_identity_unavailable_is_the_only_documentary_not_applicable_state() -> None:
    result, client = _run_qualification(
        _IDENTITY_UNAVAILABLE_ATTRIBUTION,
        inventory_presence="unknown",
    )
    assert all("/bien/dpe/" not in path for path, _ in client.calls)
    assert result["dpe_documentary_proof"] == {
        "inventory_route": "not_applicable_identity_unavailable",
        "document_route": "not_applicable_identity_unavailable",
        "source_fields_equal": "not_applicable",
        "no_store": "not_applicable",
    }

    degraded, _ = _run_qualification(
        _ERROR_ATTRIBUTION,
        inventory_presence="unavailable",
    )
    assert degraded["ok"] is False
    assert "dpe_documentary_proof" not in degraded


@pytest.mark.parametrize(
    "failed_key",
    ["inventory_aggregate_equal", "inventory_item_equal", "document_equal"],
)
def test_documentary_source_mismatch_fails_closed_without_values(
    failed_key: str,
) -> None:
    matches = {
        "inventory_aggregate_equal": True,
        "inventory_item_equal": True,
        "document_equal": True,
    }
    matches[failed_key] = False
    result, _ = _run_qualification(
        _BUILDING_CONTEXT_ATTRIBUTION,
        inventory_presence="present",
        documentary_match=matches,
    )
    assert result["ok"] is False
    assert result["reasons"] == [
        "golden_security: DPE documentary projection diverges from source"
    ]
    assert "dpe_documentary_proof" not in result
    assert _SENTINEL_BAN not in json.dumps(result)


def test_documentary_routes_require_no_store() -> None:
    result, _ = _run_qualification(
        _BUILDING_CONTEXT_ATTRIBUTION,
        inventory_presence="present",
        cache_control="private",
    )
    assert result["reasons"] == ["golden_security: DPE inventory response is cacheable"]
    assert "dpe_documentary_proof" not in result


def test_documentary_source_allowlists_cover_the_public_db_fields() -> None:
    from app.dpe.schemas import DpeConsultedDocument, DpeInventoryItem
    from scripts.verify_golden_cases import _DPE_LIFECYCLE_SOURCE_KEYS

    assert set(_DPE_INVENTORY_ITEM_SOURCE_KEYS) | _DPE_LIFECYCLE_SOURCE_KEYS == (
        set(DpeInventoryItem.model_fields) - {"document_ref"}
    )
    assert set(_DPE_DOCUMENT_SOURCE_KEYS) | _DPE_LIFECYCLE_SOURCE_KEYS == (
        set(DpeConsultedDocument.model_fields)
        - {
            "version",
            "context_ref",
            "document_ref",
            "association",
            "address_check",
            "source",
            "source_coverage",
            "graph_state",
            "imported_at",
            "validity_by_date",
        }
    )


@pytest.mark.destructive
def test_documentary_source_sql_executes_with_dates_and_numerics() -> None:
    """Run only through run_contract_integration.ps1's disposable PostGIS."""
    raw_dsn = os.getenv("CONTRACT_TEST_DATABASE_URL")
    if not raw_dsn:
        pytest.skip("disposable contract database is not running")
    if os.getenv("ALLOW_DESTRUCTIVE_DB_TESTS") != "1":
        pytest.fail("destructive database opt-in is missing")
    dsn = raw_dsn.replace("postgresql+asyncpg://", "postgresql://")
    host = dsn.split("@", 1)[-1].split(":", 1)[0]
    if host not in {"127.0.0.1", "localhost"}:
        pytest.fail("contract database must be loopback-only")

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute(
            """
            CREATE TABLE public.diagnostics_dpe (
                id bigint PRIMARY KEY,
                numero_dpe text NOT NULL,
                identifiant_ban text,
                code_departement text,
                type_batiment text,
                surface_habitable numeric,
                etiquette_dpe text,
                etiquette_ges text,
                date_etablissement_dpe date,
                date_fin_validite_dpe date,
                annee_construction integer,
                periode_construction text,
                type_installation_chauffage text,
                type_energie_principale_chauffage text,
                qualite_isolation_enveloppe text,
                conso_5_usages_ep numeric,
                emission_ges_5_usages numeric,
                cout_total_5_usages numeric
            )
            """
        )
        conn.execute(
            """
            INSERT INTO public.diagnostics_dpe VALUES (
                1, 'synthetic-document', '44991_0001_00002', '44',
                'appartement', 42.5, ' d ', 'E', '2024-02-03', '2034-02-02',
                1975, '1971 - 1977', 'individuel', 'électricité', 'moyenne',
                218.4, 7.2, 1130.0
            )
            """
        )
        distribution = {**dict.fromkeys("ABCEFG", 0), "D": 1, "unknown": 0}
        item = _source_documentary_projection(
            _DOCUMENT_ITEM, _DPE_INVENTORY_ITEM_SOURCE_KEYS
        )
        document = _source_documentary_projection(
            _route_document(), _DPE_DOCUMENT_SOURCE_KEYS
        )
        base_params = {
            "ban_id": "44991_0001_00002",
            "department": "44",
            "inventory_total": 1,
            "inventory_distribution": Jsonb(distribution),
            "has_document": True,
            "inventory_item": Jsonb(item),
            "document": Jsonb(document),
        }
        row = conn.execute(DPE_DOCUMENTARY_SOURCE_MATCH_SQL, base_params).fetchone()
        assert row == {
            "inventory_aggregate_equal": True,
            "inventory_item_equal": True,
            "document_equal": True,
        }

        for field in _DPE_DOCUMENT_SOURCE_KEYS:
            divergent = dict(document)
            divergent[field] = None
            observed = conn.execute(
                DPE_DOCUMENTARY_SOURCE_MATCH_SQL,
                {**base_params, "document": Jsonb(divergent)},
            ).fetchone()
            assert observed is not None
            assert all(type(value) is bool for value in observed.values())
            assert observed == {
                "inventory_aggregate_equal": True,
                "inventory_item_equal": True,
                "document_equal": False,
            }

        for field in _DPE_INVENTORY_ITEM_SOURCE_KEYS:
            divergent = dict(item)
            divergent[field] = None
            observed = conn.execute(
                DPE_DOCUMENTARY_SOURCE_MATCH_SQL,
                {**base_params, "inventory_item": Jsonb(divergent)},
            ).fetchone()
            assert observed is not None
            assert all(type(value) is bool for value in observed.values())
            assert observed == {
                "inventory_aggregate_equal": True,
                "inventory_item_equal": False,
                "document_equal": True,
            }
        conn.rollback()


def _source_documentary_projection(
    block: dict[str, Any], keys: tuple[str, ...]
) -> dict[str, Any]:
    return {key: block[key] for key in keys}


def _lifecycle_payload() -> dict[str, Any]:
    from scripts.verify_golden_cases import _paris_today

    return {
        "source_state": "present",
        "source_snapshot_date": "2026-09-05",
        "source_scope": "44/85",
        "regimes": ["existant", "neuf"],
        "replacement_state": "replaced",
        "graph_issues": ["missing_target"],
        "validity": "not_expired_by_date",
        "as_of_date": _paris_today().isoformat(),
        "revision_status": "unverified",
        "revision_kind": None,
        "revision_date": None,
        "detail_availability": "available",
    }


def _lifecycle_routes() -> tuple[dict[str, Any], dict[str, Any]]:
    inventory = _route_inventory("present")
    inventory["as_of_date"] = _lifecycle_payload()["as_of_date"]
    inventory["items"][0]["lifecycle"] = _lifecycle_payload()
    document = {**_route_document(), "lifecycle": _lifecycle_payload()}
    return inventory, document


def test_golden_lifecycle_contract_accepts_whole_new_v2_and_checks_private_facts():
    inventory, document = _lifecycle_routes()
    for matches in (True, False):
        result, _ = _run_qualification(
            _BUILDING_CONTEXT_ATTRIBUTION,
            inventory_presence="present",
            observed_inventory=inventory,
            route_inventory=inventory,
            route_document=document,
            lifecycle_match=matches,
        )
        assert result["ok"] is matches
        if not matches:
            assert result["reasons"] == [
                "golden_security: DPE lifecycle projection diverges from source"
            ]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_page_date",
        "null_page_date",
        "bad_page_date",
        "old_page_date",
        "missing_item_lifecycle",
        "missing_document_lifecycle",
        "private_key",
        "unknown_enum",
        "partial_lifecycle",
        "different_date",
        "different_graph",
        "tombstone_with_content",
        "wrong_legacy_validity",
        "mixed_routes",
    ],
)
def test_golden_lifecycle_contract_refuses_mixed_or_malformed_shapes(mutation):  # noqa: C901
    inventory, document = _lifecycle_routes()
    observed = copy.deepcopy(inventory)
    if mutation == "missing_page_date":
        inventory.pop("as_of_date")
    elif mutation == "null_page_date":
        inventory["as_of_date"] = None
    elif mutation == "bad_page_date":
        inventory["as_of_date"] = "20260907"
    elif mutation == "old_page_date":
        inventory["as_of_date"] = "2020-01-01"
    elif mutation == "missing_item_lifecycle":
        inventory["items"][0].pop("lifecycle")
    elif mutation == "missing_document_lifecycle":
        document.pop("lifecycle")
    elif mutation == "private_key":
        document["lifecycle"][_SENTINEL_DPE_NUMBER] = "private"
    elif mutation == "unknown_enum":
        document["lifecycle"]["source_state"] = _SENTINEL_DPE_NUMBER
    elif mutation == "partial_lifecycle":
        document["lifecycle"].pop("revision_date")
    elif mutation == "different_date":
        document["lifecycle"]["as_of_date"] = "2020-01-01"
    elif mutation == "different_graph":
        document["lifecycle"]["replacement_state"] = "no_successor_known"
    elif mutation == "tombstone_with_content":
        document["lifecycle"].update(
            source_state="absent_from_complete_scope",
            detail_availability="source_reconciled",
            validity="unknown",
        )
        document["validity_by_date"] = "unknown"
    elif mutation == "wrong_legacy_validity":
        document["validity_by_date"] = "unknown"
    elif mutation == "mixed_routes":
        observed = _inventory("present")
    result, _ = _run_qualification(
        _BUILDING_CONTEXT_ATTRIBUTION,
        inventory_presence="present",
        observed_inventory=observed,
        route_inventory=inventory,
        route_document=document,
    )
    assert result["ok"] is False
    assert "dpe_documentary_proof" not in result
    assert _SENTINEL_DPE_NUMBER not in json.dumps(result)


def test_lifecycle_sql_is_independent_select_only_and_compares_all_dimensions():
    assert_select_only(DPE_LIFECYCLE_SOURCE_MATCH_SQL)
    for field in (
        "source_observations",
        "source_snapshot_at",
        "source_state",
        "lifecycle_facts",
        "revision_evidence",
        "is_tombstone",
    ):
        assert field in DPE_LIFECYCLE_SOURCE_MATCH_SQL
    assert "identifiant_ban = %(ban_id)s" in DPE_LIFECYCLE_SOURCE_MATCH_SQL
    assert "%(as_of_date)s" in DPE_LIFECYCLE_SOURCE_MATCH_SQL


@pytest.mark.parametrize(
    "start,end,validity",
    [
        ("2024-02-03", "2034-02-02", "not_expired_by_date"),
        ("2024-02-03", "2025-02-02", "expired"),
        (None, "2034-02-02", "unknown"),
        ("2024-02-03", None, "unknown"),
        ("2034-02-03", "2024-02-02", "unknown"),
        ("2034-02-03", "2035-02-02", "unknown"),
    ],
)
def test_new_v2_can_report_null_lifecycle_with_independently_dated_legacy_validity(
    start, end, validity
):
    inventory, document = _lifecycle_routes()
    fields = {
        "lifecycle": None,
        "date_etablissement_dpe": start,
        "date_fin_validite_dpe": end,
    }
    inventory["items"][0].update(fields)
    document.update(fields, validity_by_date=validity)
    result, _ = _run_qualification(
        _BUILDING_CONTEXT_ATTRIBUTION,
        inventory_presence="present",
        observed_inventory=inventory,
        route_inventory=inventory,
        route_document=document,
    )
    assert result["ok"] is True


def test_new_v2_accepts_minimized_tombstone_without_recovering_old_content():
    inventory, document = _lifecycle_routes()
    lifecycle = {
        **_lifecycle_payload(),
        "source_state": "absent_from_complete_scope",
        "validity": "unknown",
        "detail_availability": "source_reconciled",
    }
    inventory["items"][0].update(dict.fromkeys(_DPE_INVENTORY_ITEM_SOURCE_KEYS))
    inventory["items"][0]["lifecycle"] = lifecycle
    document.update(dict.fromkeys(_DPE_DOCUMENT_SOURCE_KEYS))
    document.update(lifecycle=lifecycle, validity_by_date="unknown")
    result, _ = _run_qualification(
        _BUILDING_CONTEXT_ATTRIBUTION,
        inventory_presence="present",
        observed_inventory=inventory,
        route_inventory=inventory,
        route_document=document,
    )
    assert result["ok"] is True


@pytest.mark.parametrize(
    "presence,attribution",
    [
        ("absent", _ABSENT_ATTRIBUTION),
        ("unknown", _IDENTITY_UNAVAILABLE_ATTRIBUTION),
    ],
)
def test_new_v2_empty_and_unavailable_identity_keep_the_dated_shape(
    presence, attribution
):
    from scripts.verify_golden_cases import _paris_today

    inventory = {**_route_inventory(presence), "as_of_date": _paris_today().isoformat()}
    result, _ = _run_qualification(
        attribution,
        inventory_presence=presence,
        observed_inventory=inventory,
        route_inventory=inventory,
    )
    assert result["ok"] is True


@pytest.mark.destructive
def test_lifecycle_source_sql_executes_independently_on_091_facts():
    raw_dsn = os.getenv("CONTRACT_TEST_DATABASE_URL")
    if not raw_dsn:
        pytest.skip("disposable contract database is not running")
    if os.getenv("ALLOW_DESTRUCTIVE_DB_TESTS") != "1":
        pytest.fail("destructive database opt-in is missing")
    dsn = raw_dsn.replace("postgresql+asyncpg://", "postgresql://")
    if dsn.split("@", 1)[-1].split(":", 1)[0] not in {"127.0.0.1", "localhost"}:
        pytest.fail("contract database must be loopback-only")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("""CREATE TABLE public.diagnostics_dpe (
            id bigint, numero_dpe text, identifiant_ban text, code_departement text,
            date_etablissement_dpe date, date_fin_validite_dpe date, regime_dpe text,
            source_state text, source_snapshot_at timestamptz,
            source_observations jsonb,
            lifecycle_facts jsonb, revision_evidence jsonb, is_tombstone bool
        )""")
        facts = {
            "replacement_state": "replaced",
            "graph_issues": ["missing_target"],
            "date_issues": [],
        }
        evidence = {
            "status": "unverified",
            "kind": None,
            "revision_date": None,
            "verified_at": None,
            "evidence_sha256": None,
            "official_source": None,
        }
        observations = [
            {
                "source_slug": slug,
                "department": "44",
                "source_version": "synthetic",
                "observed_at": "2026-09-04T22:00:00Z",
                "last_seen_at": "2026-09-04T22:00:00Z",
                "state": "present",
                "import_id": "synthetic-import",
            }
            for slug in ("dpe02neuf", "dpe03existant")
        ]
        conn.execute(
            """INSERT INTO public.diagnostics_dpe VALUES (
            1, 'synthetic-document', '44991_0001_00002', '44', '2024-02-03',
            '2034-02-02', NULL, 'present', '2026-09-04T23:00:00Z', %s, %s, %s, false
        )""",
            (
                Jsonb(observations),
                Jsonb(facts),
                Jsonb(evidence),
            ),
        )
        expected = _lifecycle_payload()
        params = {
            "ban_id": "44991_0001_00002",
            "department": "44",
            "has_document": True,
            "as_of_date": expected["as_of_date"],
            "item_lifecycle": Jsonb(expected),
            "document_lifecycle": Jsonb(expected),
        }

        def check(claim, matches=True):
            row = conn.execute(
                DPE_LIFECYCLE_SOURCE_MATCH_SQL,
                {
                    **params,
                    "item_lifecycle": Jsonb(claim),
                    "document_lifecycle": Jsonb(claim),
                },
            ).fetchone()
            assert row == {"lifecycle_equal": matches}

        check(expected)
        conn.execute(
            "UPDATE public.diagnostics_dpe SET source_observations='[]'::jsonb"
        )
        check(expected, False)
        check(None, False)
        conn.execute(
            "UPDATE public.diagnostics_dpe SET source_observations=%s",
            (Jsonb(observations),),
        )
        conn.execute(
            "UPDATE public.diagnostics_dpe "
            "SET source_snapshot_at='2026-09-04T21:00:00Z'"
        )
        check(expected, False)
        conn.execute(
            "UPDATE public.diagnostics_dpe "
            "SET source_snapshot_at='2026-09-04T23:00:00Z'"
        )
        conn.execute(
            "UPDATE public.diagnostics_dpe "
            "SET source_state='explicitly_deactivated', is_tombstone=true"
        )
        check(
            {
                **expected,
                "source_state": "explicitly_deactivated",
                "validity": "unknown",
                "detail_availability": "source_reconciled",
            },
            False,
        )
        check(None, False)
        conn.execute(
            "UPDATE public.diagnostics_dpe "
            "SET source_state='present', is_tombstone=false"
        )
        # Every public fact participates, independently of API projection code.
        for field in expected:
            changed = {**expected, field: "divergent"}
            check(changed, False)
        # A known replacement remains replaced even when its source dates expire.
        conn.execute(
            "UPDATE public.diagnostics_dpe SET date_fin_validite_dpe='2025-01-01'"
        )
        check({**expected, "validity": "expired"})
        conn.execute(
            "UPDATE public.diagnostics_dpe SET date_etablissement_dpe='2035-01-01'"
        )
        check({**expected, "validity": "inconsistent_dates"})
        conn.execute(
            "UPDATE public.diagnostics_dpe SET date_fin_validite_dpe='2036-01-01'"
        )
        check({**expected, "validity": "future_establishment"})
        conn.execute("UPDATE public.diagnostics_dpe SET date_etablissement_dpe=NULL")
        check({**expected, "validity": "unknown"})
        conn.execute(
            "UPDATE public.diagnostics_dpe SET lifecycle_facts=%s",
            (Jsonb({**facts, "date_issues": ["invalid_establishment_date"]}),),
        )
        check({**expected, "validity": "inconsistent_dates"})
        conn.execute("""UPDATE public.diagnostics_dpe
                     SET source_state='absent_from_complete_scope',
                     is_tombstone=true, date_fin_validite_dpe=NULL""")
        conn.execute(
            "UPDATE public.diagnostics_dpe SET source_observations=%s",
            (
                Jsonb(
                    [
                        {**item, "state": "absent_from_complete_scope"}
                        for item in observations
                    ]
                ),
            ),
        )
        check(
            {
                **expected,
                "source_state": "absent_from_complete_scope",
                "validity": "unknown",
                "detail_availability": "source_reconciled",
            }
        )
        conn.execute(
            """UPDATE public.diagnostics_dpe SET source_state='unknown',
            is_tombstone=false, source_snapshot_at=NULL,
            source_observations='[]'::jsonb,
            lifecycle_facts=%s""",
            (
                Jsonb(
                    {
                        "replacement_state": "not_checked",
                        "graph_issues": [],
                        "date_issues": [],
                    }
                ),
            ),
        )
        check(None)
        check(expected, False)
        conn.rollback()
