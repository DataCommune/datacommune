"""Independent contracts for DPE audit, manifest drift and HTTP comparison."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from app.data_quality.base import AuditResult
from app.data_quality.checkers.dpe import DpeAuditor
from app.etl_dpe.sources import expected_pairs
from app.validation.validators.dpe import DpeValidator as DriftValidator
from scripts.validate.validators.dpe import DpeValidator as HttpValidator

pytestmark = pytest.mark.unit

# The 44/85 generation of the transition reference; other scopes below.
EXPECTED_PAIRS = expected_pairs(("44", "85"))


def audit_result():
    return AuditResult(dataset="dpe", table="diagnostics_dpe", row_count=540654)


def test_auditor_required_document_fields_exclude_tombstones_and_position():
    auditor = DpeAuditor()
    auditor.null_rates = MagicMock(return_value={})
    auditor._check_required_nulls(None, audit_result(), 10)
    _, table, columns = auditor.null_rates.call_args.args
    assert "NOT is_tombstone" in table
    assert "position" not in columns


def test_auditor_geometry_null_is_valid_but_present_invalid_is_error():
    auditor = DpeAuditor()
    auditor.scalar = MagicMock(return_value=1)
    result = audit_result()
    auditor._check_geom_validity(None, result)
    sql = auditor.scalar.call_args.args[1]
    assert "position IS NOT NULL" in sql
    assert "ST_IsValid" in sql
    assert "NaN" in sql
    assert result.findings[0].severity == "ERROR"


def generation(rows: dict, import_id="generation-1"):
    """Synthetic verified manifest: {(source, departement): rows}."""
    return SimpleNamespace(
        import_id=import_id,
        validate=MagicMock(),
        departements=frozenset(department for _slug, department in rows),
        files=[
            SimpleNamespace(
                pair=pair,
                source_slug=pair[0],
                department=pair[1],
                rows=count,
                version_start="v1",
            )
            for pair, count in sorted(rows.items())
        ],
    )


def manifest():
    return generation({pair: 100 for pair in EXPECTED_PAIRS})


# Scope {44, 85, 75}: 75 has 50 existing-build documents and a verified zero
# for new builds (the manifest proves the source has none).
SCOPE_75 = {
    **{pair: 100 for pair in EXPECTED_PAIRS},
    ("dpe03existant", "75"): 50,
    ("dpe02neuf", "75"): 0,
}


def test_auditor_requires_every_counted_pair_even_with_correct_global_volume():
    auditor = DpeAuditor(manifest=manifest())
    auditor.rows = MagicMock(return_value=[(s, d, 100) for s, d in EXPECTED_PAIRS])
    result = audit_result()
    auditor._check_dept_coverage(None, result)
    assert not result.findings
    auditor.rows.return_value = [(s, d, 200) for s, d in sorted(EXPECTED_PAIRS)[:3]]
    auditor._check_dept_coverage(None, result)
    assert any(f.severity == "ERROR" for f in result.findings)


def test_auditor_judges_the_generation_pairs_missing_zero_and_unexpected():
    auditor = DpeAuditor(manifest=generation(SCOPE_75))
    present = [(s, d, n) for (s, d), n in SCOPE_75.items() if n]
    auditor.rows = MagicMock(return_value=present)
    result = audit_result()
    auditor._check_dept_coverage(None, result)
    # (dpe02neuf, 75) absent from the table: a verified zero, no finding.
    assert not result.findings
    assert result.stats["generation_pairs"] == 6
    assert result.stats["verified_zero_pairs"] == 1

    without_75 = [row for row in present if row[1] != "75"]
    auditor.rows.return_value = [*without_75, ("dpe03existant", "33", 4)]
    result = audit_result()
    auditor._check_dept_coverage(None, result)
    titles = {f.title: f.description for f in result.findings}
    missing = titles["Paires DPE source x département manquantes"]
    assert "('dpe03existant', '75')" in missing
    # The verified zero is never reported missing, a gap never as zero.
    assert "dpe02neuf', '75'" not in missing
    unexpected = titles["Paires DPE source x département inattendues"]
    assert "('dpe03existant', '33')" in unexpected
    assert all("44/85" not in f.title for f in result.findings)


def test_auditor_without_manifest_judges_nothing(monkeypatch):
    monkeypatch.delenv("DPE_VALIDATION_MANIFEST", raising=False)
    auditor = DpeAuditor()
    auditor.rows = MagicMock(side_effect=AssertionError("no fixed scope"))
    result = audit_result()
    auditor._check_dept_coverage(None, result)
    auditor._check_volume(None, result, 540654)
    assert [f.title for f in result.findings] == [
        "Contrôle DPE de couverture non jugé",
        "Contrôle DPE de volume non jugé",
    ]
    assert all(f.severity == "ERROR" for f in result.findings)


def test_auditor_volume_keeps_the_transition_floor_for_44_85():
    auditor = DpeAuditor(manifest=manifest())
    auditor.rows = MagicMock(return_value=[("44", 400_000), ("85", 140_654)])
    result = audit_result()
    auditor._check_volume(None, result, 540654)
    assert not result.findings
    auditor.rows.return_value = [("44", 200_000), ("85", 100_000)]
    auditor._check_volume(None, result, 300_000)
    assert [f.title for f in result.findings] == [
        "Volume DPE anormalement bas : départements 44, 85"
    ]
    assert "plancher de transition 350000" in result.findings[0].description


def test_auditor_volume_judges_another_departement_on_its_manifest_only():
    auditor = DpeAuditor(manifest=generation(SCOPE_75))
    # 44/85 above their floor; 75 at its manifest count, without any ratio.
    auditor.rows = MagicMock(
        return_value=[("44", 400_000), ("85", 140_000), ("75", 50)]
    )
    result = audit_result()
    auditor._check_volume(None, result, 540_050)
    assert not result.findings
    auditor.rows.return_value = [("44", 400_000), ("85", 140_000), ("75", 49)]
    auditor._check_volume(None, result, 540_049)
    assert [f.title for f in result.findings] == [
        "Volume DPE anormalement bas : département 75"
    ]
    assert "au moins 50" in result.findings[0].description
    # A departement alone is never judged on the 44/85 floor.
    alone = DpeAuditor(
        manifest=generation({("dpe03existant", "75"): 50, ("dpe02neuf", "75"): 0})
    )
    alone.rows = MagicMock(return_value=[("75", 50)])
    result = audit_result()
    alone._check_volume(None, result, 50)
    assert not result.findings


def test_drift_manifest_transition_keeps_threshold_and_checks_every_pair():
    validator = DriftValidator()
    observed = {p: (100, {"v1"}, {"generation-1"}) for p in EXPECTED_PAIRS}
    assert validator.compare_populations(manifest(), observed) == []
    missing = dict(observed)
    del missing[sorted(EXPECTED_PAIRS)[0]]
    missing[sorted(EXPECTED_PAIRS)[1]] = (200, {"v1"}, {"generation-1"})
    assert validator.compare_populations(manifest(), missing)
    observed[sorted(EXPECTED_PAIRS)[0]] = (94, {"v1"}, {"generation-1"})
    assert validator.compare_populations(manifest(), observed)
    assert validator.discrepancy_threshold == 5.0


def test_drift_rejects_wrong_source_version_and_incomplete_manifest():
    validator = DriftValidator()
    observed = {p: (100, {"other"}, {"generation-1"}) for p in EXPECTED_PAIRS}
    assert validator.compare_populations(manifest(), observed)
    incomplete = manifest()
    incomplete.files.pop()
    assert validator.compare_populations(incomplete, observed)


@pytest.mark.parametrize("pair", sorted(EXPECTED_PAIRS))
def test_drift_each_missing_pair_fails_independently(pair):
    observed = {p: (100, {"v1"}, {"generation-1"}) for p in EXPECTED_PAIRS if p != pair}
    assert DriftValidator().compare_populations(manifest(), observed)


def _observed(rows: dict) -> dict:
    return {
        pair: (count, {"v1"}, {"generation-1"}) for pair, count in rows.items() if count
    }


def test_drift_judges_a_new_departement_on_its_exact_manifest_total():
    validator = DriftValidator()
    observed = _observed(SCOPE_75)
    # (dpe02neuf, 75): verified zero, absent from the base, not an issue.
    assert validator.compare_populations(generation(SCOPE_75), observed) == []
    # 44: 3 % off its manifest, within the transition reference's tolerance.
    observed[("dpe03existant", "44")] = (97, {"v1"}, {"generation-1"})
    assert validator.compare_populations(generation(SCOPE_75), observed) == []
    # 75: 2 % off, refused: no threshold carried over from 44/85.
    observed[("dpe03existant", "75")] = (49, {"v1"}, {"generation-1"})
    assert validator.compare_populations(generation(SCOPE_75), observed) == [
        "Population differs from manifest: ('dpe03existant', '75') (49 != 50)"
    ]


def test_drift_reports_a_missing_pair_as_missing_and_an_extra_one():
    observed = _observed(SCOPE_75)
    del observed[("dpe03existant", "75")]
    observed[("dpe02neuf", "33")] = (3, {"v1"}, {"generation-1"})
    issues = DriftValidator().compare_populations(generation(SCOPE_75), observed)
    assert issues == [
        "Unexpected pair outside the generation: ('dpe02neuf', '33')",
        "Missing pair: ('dpe03existant', '75') (manifest counts 50)",
    ]
    # Never read as a zero count nor as a percentage discrepancy.
    assert not any("discrepancy" in issue or "differs" in issue for issue in issues)


def test_drift_run_persists_verified_pair_result(monkeypatch):
    validator = DriftValidator()
    monkeypatch.setattr(validator, "load_manifest", manifest)
    monkeypatch.setattr(
        validator,
        "read_populations",
        lambda conn: {p: (100, {"v1"}, {"generation-1"}) for p in EXPECTED_PAIRS},
    )
    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = (400,)
    context = MagicMock()
    context.__enter__.return_value = connection
    monkeypatch.setattr(
        "app.validation.validators.dpe.psycopg.connect", lambda dsn: context
    )
    persist = MagicMock()
    monkeypatch.setattr("app.etl.validation.persist_validation_run", persist)
    result = validator.run("fixture")
    assert result.passed
    assert result.local_row_count == result.source_row_count == 400
    assert persist.call_args.kwargs["passed"] is True


def test_drift_run_missing_manifest_is_failure_not_drift_only_success(monkeypatch):
    validator = DriftValidator()
    monkeypatch.setattr(
        validator, "load_manifest", MagicMock(side_effect=FileNotFoundError)
    )
    persist = MagicMock()
    monkeypatch.setattr("app.etl.validation.persist_validation_run", persist)
    result = validator.run("fixture")
    assert not result.passed
    assert "manifest" in result.notes.lower()
    assert persist.call_args.kwargs["passed"] is False


def document(*sources):
    return {
        "numero_dpe": "FIXTURE",
        "code_departement": "44",
        "source_state": "present",
        "source_observations": [
            {"source_slug": source, "department": "44", "state": "present"}
            for source in sources
        ],
        "etiquette_dpe": "B",
        "etiquette_ges": "A",
        "surface_habitable": 50,
        "conso_5_usages_ep": 200,
        "emission_ges_5_usages": 10,
    }


def source_document():
    result = document()
    result["code_departement_ban"] = "44"
    result["surface_habitable_logement"] = result.pop("surface_habitable")
    return result


def test_http_new_source_and_surface_mapping_are_real_requests():
    paths = []

    def respond(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"results": [source_document()]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = HttpValidator().validate_row(client, document("dpe02neuf"))
    assert result.status == "ok"
    assert paths == ["/data-fair/api/v1/datasets/dpe02neuf/lines"]


@pytest.mark.parametrize(
    "mode,status",
    [
        ("absent", "source_absent"),
        ("error", "api_error"),
        ("contradiction", "source_contradiction"),
    ],
)
def test_http_absence_inaccessibility_and_contradiction_are_distinct(mode, status):
    def respond(request):
        if mode == "error":
            return httpx.Response(503)
        if mode == "absent":
            return httpx.Response(200, json={"results": []})
        record = source_document()
        record["numero_dpe"] = "WRONG"
        return httpx.Response(200, json={"results": [record]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = HttpValidator().validate_row(client, document("dpe02neuf"))
    assert result.status == status


def test_http_multisource_checks_both_copies_without_winning_regime():
    paths = []

    def respond(request):
        paths.append(request.url.path)
        record = source_document()
        if "dpe02neuf" in request.url.path:
            record["surface_habitable_logement"] = 60
        return httpx.Response(200, json={"results": [record]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = HttpValidator().validate_row(
            client, document("dpe03existant", "dpe02neuf")
        )
    assert len(paths) == 2
    assert result.status == "source_contradiction"


def test_http_sample_contains_provenance_and_excludes_tombstones():
    sql = HttpValidator().sample_query(10)
    assert "source_observations" in sql
    assert "code_departement" in sql
    assert "NOT is_tombstone" in sql


@pytest.mark.parametrize("missing", [False, True])
def test_http_multisource_requires_every_declared_copy(missing):
    paths = []

    def respond(request):
        paths.append(request.url.path)
        results = (
            []
            if missing and "dpe03existant" in request.url.path
            else [source_document()]
        )
        return httpx.Response(200, json={"results": results})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = HttpValidator().validate_row(
            client, document("dpe03existant", "dpe02neuf")
        )
    assert len(paths) == 2
    assert result.status == ("source_contradiction" if missing else "ok")


@pytest.mark.parametrize("payload", [{}, {"files": None}, [], {"files": [None]}])
def test_drift_malformed_manifest_records_a_failed_run(tmp_path, monkeypatch, payload):
    import json

    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("DPE_VALIDATION_MANIFEST", str(path))
    persist = MagicMock()
    monkeypatch.setattr("app.etl.validation.persist_validation_run", persist)
    validator = DriftValidator()
    from app.etl_dpe.sources import DpeImportManifest

    monkeypatch.setattr(
        validator, "load_manifest", lambda: DpeImportManifest.load(path)
    )
    result = validator.run("fixture")
    assert not result.passed
    assert persist.call_args.kwargs["passed"] is False


def test_drift_uses_explicit_private_manifest_without_cache_fallback(
    tmp_path, monkeypatch
):
    expected = manifest()
    loader = MagicMock(return_value=expected)
    monkeypatch.setattr("app.validation.validators.dpe.DpeImportManifest.load", loader)
    environment_path = tmp_path / "durable" / "manifest.json"
    explicit_path = tmp_path / "selected" / "manifest.json"
    monkeypatch.setenv("DPE_VALIDATION_MANIFEST", str(environment_path))
    assert DriftValidator().load_manifest() is expected
    loader.assert_called_once_with(environment_path)
    loader.reset_mock()
    assert DriftValidator(manifest_path=explicit_path).load_manifest() is expected
    loader.assert_called_once_with(explicit_path)
    monkeypatch.delenv("DPE_VALIDATION_MANIFEST")
    loader.reset_mock()
    with pytest.raises(ValueError, match="DPE_VALIDATION_MANIFEST"):
        DriftValidator().load_manifest()
    loader.assert_not_called()


@pytest.mark.parametrize("generations", [{"old-generation"}, {"generation-1", None}])
def test_drift_rejects_other_generation_even_with_same_versions_counts(generations):
    observed = {p: (100, {"v1"}, generations) for p in EXPECTED_PAIRS}
    issues = DriftValidator().compare_populations(manifest(), observed)
    assert any("generation" in issue.lower() for issue in issues)


def test_drift_population_read_includes_generation_ids():
    connection = MagicMock()
    connection.execute.return_value.fetchall.return_value = [
        ("dpe02neuf", "44", 100, ["v1"], ["generation-1"])
    ]
    assert DriftValidator.read_populations(connection) == {
        ("dpe02neuf", "44"): (100, {"v1"}, {"generation-1"})
    }
    assert "o->>'import_id'" in connection.execute.call_args.args[0]


@pytest.mark.parametrize(
    "payload", [None, [], {}, {"results": None}, {"results": {}}, {"results": [None]}]
)
def test_http_invalid_response_shape_is_api_error(payload):
    import json

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=json.dumps(payload))
        )
    ) as client:
        result = HttpValidator().validate_row(client, document("dpe02neuf"))
    assert result.status == "api_error"


def test_http_invalid_json_is_api_error():
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="{"))
    ) as client:
        result = HttpValidator().validate_row(client, document("dpe02neuf"))
    assert result.status == "api_error"


@pytest.mark.parametrize("department", ["85", None])
def test_http_response_department_must_match_declared_scope(department):
    record = source_document()
    record["code_departement_ban"] = department
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"results": [record]})
        )
    ) as client:
        result = HttpValidator().validate_row(client, document("dpe02neuf"))
    assert result.status == "source_contradiction"
