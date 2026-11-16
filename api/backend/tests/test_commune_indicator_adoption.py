"""Unit contract for the explicit DVF stock-adoption command."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import ClassVar
from unittest.mock import Mock

import pytest

from app.commune import indicator_adoption as adoption


def _request(tmp_path: Path) -> adoption.AdoptionRequest:
    receipt = tmp_path / "backup.json"
    receipt.write_text(
        json.dumps(
            {
                "backup_id": "pbs:ct277:2026-09-10T10:00:00Z",
                "captured_at": "2026-09-10T10:00:00Z",
                "capture_mode": "database_quiescent",
                "catalog_verified": True,
                "restore_path": "CT277 -> datacommune_preprod",
                "restore_tested": True,
                "environment": "preproduction",
                "vmid": 277,
                "database": "datacommune_preprod",
                "sha256": "f" * 64,
                "restore_exit_code": 0,
                "restore_target_removed": True,
                "restored_counts": {
                    "mutations": 698299,
                    "mutations_grain": 287261,
                    "diagnostics_dpe": 540717,
                    "schema": "094_publication_generations",
                },
            }
        ),
        encoding="utf-8",
    )
    return adoption.AdoptionRequest(
        scope="preproduction_ct277",
        expected_candidate_sha="a" * 40,
        expected_runtime_sha256=adoption.runtime_source_sha256(),
        expected_schema_revision="096_commune_indicator_build_activation",
        expected_system_identifier="1234",
        expected_database="datacommune_preprod",
        expected_server_address="192.0.2.277",
        expected_server_port=5432,
        backup_receipt=receipt,
        expected_backup_sha256="f" * 64,
        expected_source_rows=698299,
        expected_mutation_count=287261,
        expected_all_years_projection_sha256="c" * 64,
        expected_projection_count=100090,
        expected_projection_sha256="d" * 64,
        expected_raw_sha256="e" * 64,
        departments=("44", "85"),
    )


def test_invalid_prerequisite_is_refused_before_any_write(tmp_path, monkeypatch):
    request = _request(tmp_path)
    monkeypatch.setattr(adoption.settings, "deployment_sha", "b" * 40)
    request = adoption.AdoptionRequest(
        **{**request.__dict__, "expected_candidate_sha": "a" * 40}
    )
    connect = Mock()
    monkeypatch.setattr(adoption.psycopg, "connect", connect)

    with pytest.raises(
        adoption.AdoptionRefused, match="runtime_candidate_sha_mismatch"
    ):
        adoption.adopt_existing_dvf_stock("secret-dsn", request)

    connect.assert_not_called()


def test_raw_fingerprint_sql_covers_ignored_projection_field():
    columns = (
        adoption.SourceColumn("id_mutation", "text", 25, -1, False),
        adoption.SourceColumn("legacy_note", "text", 25, -1, False),
        adoption.SourceColumn("geom", "geometry(Point,4326)", 0, -1, True),
    )
    query = adoption.raw_multiset_query(columns).as_string()

    assert '"legacy_note"::text' in query
    assert 'ST_AsEWKB("geom")' in query
    assert "<NULL>" in query
    assert "ORDER BY row_sha" in query


def test_fresh_process_registers_canonical_dvf_pipeline():
    environment = os.environ.copy()
    environment["DEBUG"] = "true"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.commune import indicator_adoption; "
            "from app.etl.registry import registry; "
            "assert registry.get('dvf').__name__ == 'DvfPipeline'",
        ],
        check=False,
        capture_output=True,
        cwd=Path(__file__).parents[1],
        env=environment,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr.decode()


@pytest.mark.parametrize("source", ["environment", "file"])
def test_cli_resolves_database_url_like_application_settings(tmp_path, source):
    environment = os.environ.copy()
    environment.pop("DATABASE_URL", None)
    environment.pop("DATABASE_URL_FILE", None)
    configured = "postgresql+asyncpg://operator:private@db:5432/datacommune_preprod"
    if source == "environment":
        environment["DATABASE_URL"] = configured
    else:
        secret = tmp_path / "database_url"
        secret.write_text(configured + "\n", encoding="utf-8")
        environment["DATABASE_URL_FILE"] = str(secret)
    code = (
        "import sys; from types import SimpleNamespace; "
        "import scripts.adopt_commune_indicator_stock as cli; "
        "cli.measure_stock=lambda dsn, departments: "
        "(dsn == 'postgresql://operator:private@db:5432/datacommune_preprod' "
        "and departments == ('44', '85') "
        "and SimpleNamespace(ok=True)) or (_ for _ in ()).throw(AssertionError()); "
        "sys.argv=['adopt','--dry-run','--departements','85,44']; cli.main()"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        cwd=Path(__file__).parents[1],
        env=environment,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().strip() == '{"ok": true}'


def test_cli_refuses_missing_or_unreadable_database_configuration(tmp_path):
    environment = os.environ.copy()
    environment.pop("DATABASE_URL", None)
    environment["DATABASE_URL_FILE"] = str(tmp_path / "missing-secret")
    result = subprocess.run(
        [sys.executable, "scripts/adopt_commune_indicator_stock.py", "--dry-run"],
        check=False,
        capture_output=True,
        cwd=Path(__file__).parents[1],
        env=environment,
        timeout=20,
    )
    assert result.returncode != 0
    assert (
        result.stderr.decode()
        .strip()
        .endswith("DATABASE_URL or a readable DATABASE_URL_FILE is required")
    )
    assert str(tmp_path) not in result.stderr.decode()


def test_adoption_orders_locks_rebuild_hooks_publish_and_receipt(tmp_path, monkeypatch):
    request = _request(tmp_path)
    monkeypatch.setattr(adoption.settings, "deployment_sha", "a" * 40)
    before = adoption.StockFingerprint(
        698299, 287261, "e" * 64, "c" * 64, "d" * 64, 100090, 42
    )
    events: list[str] = []
    monkeypatch.setattr(
        adoption, "validate_database_identity", lambda *_: events.append("preflight")
    )
    monkeypatch.setattr(adoption, "measure_stock", lambda *_: before)
    monkeypatch.setattr(adoption, "dataset_execution_lock", lambda *_: nullcontext())
    monkeypatch.setattr(
        adoption,
        "reconstruit_mutations_grain",
        lambda *_: (
            events.append("rebuild")
            or {
                "mutations_reconstruites": 287261,
                "liaisons_parcelles": 1,
                "valeur_divergente_count": 0,
            }
        ),
    )

    class Work:
        connection = object()

    class Publication:
        generation = adoption.UUID("11111111-1111-4111-8111-111111111111")
        id = 7
        row_count = 698299
        relation_oids: ClassVar = {"mutations": 42, "mutations_grain": 43}
        connection = object()

        def __init__(self, pre_pending_check):
            self.pre_pending_check = pre_pending_check

        def __enter__(self):
            self.pre_pending_check(self.connection)
            events.append("pending")
            return self

        def __exit__(self, *_):
            return None

        def observe_stock(self):
            events.append("observe")
            return self.row_count

        def required_work(self):
            events.append("work")
            return nullcontext(Work())

        def publish(self, callback):
            events.append("publish")
            callback(self.connection)

    monkeypatch.setattr(adoption, "Publication", lambda *args: Publication(args[2]))
    monkeypatch.setattr(
        adoption.ValidationHook,
        "on_run_complete",
        lambda *_: events.append("validation"),
    )
    monkeypatch.setattr(
        adoption.DependentMatviewHook,
        "on_run_complete",
        lambda *_: events.append("views"),
    )
    monkeypatch.setattr(
        adoption, "update_dataset_metadata", lambda *_: events.append("metadata")
    )
    monkeypatch.setattr(
        adoption, "read_published_receipt", lambda *_: {"status": "published"}
    )

    receipt = adoption.adopt_existing_dvf_stock("dsn", request)

    assert events == [
        "preflight",
        "preflight",
        "preflight",
        "pending",
        "rebuild",
        "observe",
        "work",
        "validation",
        "views",
        "publish",
        "metadata",
    ]
    assert receipt["generation_kind"] == "datacommune_observed_stock_adoption"
    assert receipt["supplier_generation"] is None


@pytest.mark.parametrize("departments", [(), ("44", "99"), ("85", "44"), ("975",)])
def test_lot_departements_are_hostable_and_canonical(tmp_path, departments):
    request = adoption.AdoptionRequest(
        **{**_request(tmp_path).__dict__, "departments": departments}
    )
    with pytest.raises(adoption.AdoptionRefused, match="invalid_departments"):
        adoption._validate_request(request)
    with pytest.raises(adoption.AdoptionRefused, match="invalid_departments"):
        adoption.measure_stock("unused-dsn", departments)


def test_hosted_lot_beyond_44_85_is_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(adoption.settings, "deployment_sha", "a" * 40)
    request = adoption.AdoptionRequest(
        **{**_request(tmp_path).__dict__, "departments": ("44", "75", "85")}
    )
    adoption._validate_request(request)
