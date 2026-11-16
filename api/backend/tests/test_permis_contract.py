"""COR-07 regressions from the frozen audit counterexamples."""

import csv
from unittest.mock import AsyncMock

import pytest

from app.etl_permis.pipeline import PermisPipeline


def source_file(tmp_path, **overrides):
    row = dict(
        nom_dossier="COR07-SYNTHETIC",
        type_dossier="PC",
        date_commencement="2025-03-15",
        date_decision="2024-12-01",
        n_commune="93999",
        nom_commune="Commune test",
        departement="93",
        nature_construction="Habitation",
        nb_logements="2",
        sdp_creee="100",
        designation="Logement",
        annee="2024",
    )
    row.update(overrides)
    path = tmp_path / "source.csv"
    with path.open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    return path


def test_nom_dossier_alone_is_not_a_source_schema(tmp_path):
    path = tmp_path / "partial.csv"
    path.write_text("nom_dossier\nSYNTHETIC\n", encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        PermisPipeline().validate_schema(path)


def test_commencement_never_becomes_deposit(tmp_path, monkeypatch):
    import hashlib

    monkeypatch.setattr("app.etl_permis.pipeline.get_download_dir", lambda _: tmp_path)
    source = source_file(tmp_path)
    output = PermisPipeline().transform(source)
    row = next(csv.DictReader(output.open(encoding="utf-8")))
    assert row["date_depot"] == ""
    assert row["date_commencement"] == "2025-03-15"
    assert row["date_decision"] == "2024-12-01"
    assert row["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "change",
    [dict(date_decision="invalid"), dict(n_commune="44999"), dict(annee="oops")],
)
def test_invalid_source_refused(tmp_path, monkeypatch, change):
    monkeypatch.setattr("app.etl_permis.pipeline.get_download_dir", lambda _: tmp_path)
    with pytest.raises(ValueError):
        PermisPipeline().transform(source_file(tmp_path, **change))


async def test_legacy_public_query_never_returns_unqualified_rows():
    from app.permis.queries import permis_par_codes

    session = AsyncMock()
    assert await permis_par_codes(session, ["93999"]) == []
    session.execute.assert_not_called()


@pytest.mark.parametrize(
    "raw,expected", [("N/C", ""), ("2025-03-15 00:00:00", "2025-03-15")]
)
def test_real_source_date_forms(tmp_path, monkeypatch, raw, expected):
    monkeypatch.setattr("app.etl_permis.pipeline.get_download_dir", lambda _: tmp_path)
    output = PermisPipeline().transform(source_file(tmp_path, date_commencement=raw))
    row = next(csv.DictReader(output.open(encoding="utf-8")))
    assert row["date_commencement"] == expected
    assert row["date_depot"] == ""


async def test_resolver_quarantine_is_error_not_proven_absence():
    from unittest.mock import Mock

    from app.resolve.multi import query_multiple_datasets

    factory = Mock(
        side_effect=AssertionError("No identity/source query for quarantine")
    )
    result = await query_multiple_datasets(factory, ["permis"], 0, 0)
    state = result["dataset_states"]["permis"]
    assert state.presence == "error"
    assert state.absence_reason == "temporary_error"
    assert result["avertissements"][0].code == "SOURCE_EN_QUARANTAINE"
    factory.assert_not_called()


def test_metadata_ignores_legacy_false_availability():
    from app.datasets.router import build_dataset_info

    row = dict(
        dataset_name="permis",
        status="available",
        last_updated_at=None,
        row_count=727,
        last_import_duration_seconds=1,
        effective_departments=["44", "85"],
        cron_schedule="0 2 5 * *",
    )
    info = build_dataset_info(row)
    assert info.statut == "quarantined"
    assert info.provenance.couverture.type == "inconnue"
    assert info.cron_schedule is None
    assert info.etat_pipeline == "degrade"


def test_quarantine_has_no_automatic_import():
    from app.scheduler.config import FRESHNESS_THRESHOLDS, SCHEDULES

    assert "permis" not in SCHEDULES
    assert "permis" not in FRESHNESS_THRESHOLDS
