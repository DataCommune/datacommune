"""Contract tests for the pure provenance assembly service (Task 44-02-02/03).

``assemble_provenance()`` combines the static catalog (attribution, coverage
policy, official cadence) with authentic dynamic facts (last successful import,
upstream period/date, effective coverage) and derives an editorial state. It
performs no I/O. All time-dependent assertions inject ``now`` so they are
date-independent.

Key honesty invariants (PROV-03, D-03, D-06):
- a recent import alone never yields a known period or ``current`` state;
- only an official cadence with a real upstream date can be ``current`` or,
  past interval + grace, ``source_delayed``;
- an irregular cadence never produces ``source_delayed``.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.common.schemas import DatasetProvenance, EditorialState
from app.datasets.provenance import ProvenanceFacts, assemble_provenance
from app.datasets.router import build_dataset_info

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Period + editorial state honesty
# ---------------------------------------------------------------------------


class TestPeriodAndEditorialState:
    def test_recent_import_without_source_date_is_unknown(self):
        """A recent import with no upstream date -> unknown period + state.

        cadastre has an irregular cadence: no invented millesime, no current.
        """
        facts = ProvenanceFacts(
            last_success_at=_NOW - timedelta(hours=2),
            source_period_label=None,
            source_updated_at=None,
            effective_departments=None,
        )
        prov = assemble_provenance("cadastre", facts, now=_NOW)
        assert isinstance(prov, DatasetProvenance)
        assert prov.periode.statut == "inconnue"
        assert prov.etat == EditorialState.INCONNU

    def test_ban_within_interval_is_current(self):
        """BAN (official weekly) dated < 7 days ago -> current, known period."""
        facts = ProvenanceFacts(
            last_success_at=_NOW - timedelta(hours=1),
            source_updated_at=_NOW - timedelta(days=3),
        )
        prov = assemble_provenance("ban", facts, now=_NOW)
        assert prov.periode.statut == "connue"
        assert prov.etat == EditorialState.ACTUEL

    def test_ban_beyond_interval_and_grace_is_source_delayed(self):
        """BAN dated far beyond the weekly interval + grace -> source_delayed.

        The editorial 'retarde' state is DataCommune's structured 'source_delayed'
        signal: the source itself is late, distinct from a technical failure.
        """
        facts = ProvenanceFacts(
            last_success_at=_NOW - timedelta(hours=1),
            source_updated_at=_NOW - timedelta(days=30),
        )
        prov = assemble_provenance("ban", facts, now=_NOW)
        assert prov.etat == EditorialState.RETARDE

    def test_irregular_cadence_never_source_delayed(self):
        """An irregular cadence with a very old upstream date stays unknown.

        source_delayed is impossible without an official interval to breach.
        """
        facts = ProvenanceFacts(
            last_success_at=_NOW - timedelta(hours=1),
            source_updated_at=_NOW - timedelta(days=900),
        )
        prov = assemble_provenance("loyers", facts, now=_NOW)
        assert prov.etat != EditorialState.RETARDE
        assert prov.etat == EditorialState.INCONNU

    def test_official_cadence_without_source_date_is_unknown(self):
        """BAN official but no upstream date -> cannot claim current."""
        facts = ProvenanceFacts(
            last_success_at=_NOW - timedelta(hours=1),
            source_updated_at=None,
        )
        prov = assemble_provenance("ban", facts, now=_NOW)
        assert prov.etat == EditorialState.INCONNU

    def test_source_period_label_alone_makes_period_known(self):
        """A real upstream label makes the period known even without a date."""
        facts = ProvenanceFacts(
            last_success_at=_NOW - timedelta(hours=1),
            source_period_label="2024",
        )
        prov = assemble_provenance("dvf", facts, now=_NOW)
        assert prov.periode.statut == "connue"
        assert prov.periode.label == "2024"


# ---------------------------------------------------------------------------
# Coverage + attribution
# ---------------------------------------------------------------------------


class TestCoverageAndAttribution:
    def test_effective_departments_yield_departements_coverage(self):
        facts = ProvenanceFacts(
            last_success_at=_NOW,
            effective_departments=["44", "85"],
        )
        prov = assemble_provenance("dpe", facts, now=_NOW)
        assert prov.couverture.type == "departements"
        assert prov.couverture.departements == ["44", "85"]

    def test_upstream_proxy_has_rolling_period_and_amont_coverage(self):
        """A proxied live API (georisques) is rolling + upstream coverage."""
        facts = ProvenanceFacts(last_success_at=None)
        prov = assemble_provenance("georisques", facts, now=_NOW)
        assert prov.periode.statut == "glissante"
        assert prov.couverture.type == "amont"

    def test_instance_without_departments_is_unknown_coverage(self):
        facts = ProvenanceFacts(last_success_at=_NOW, effective_departments=None)
        prov = assemble_provenance("cadastre", facts, now=_NOW)
        assert prov.couverture.type == "inconnue"

    def test_attribution_and_last_success_come_from_catalog_and_facts(self):
        last_success = _NOW - timedelta(days=1)
        facts = ProvenanceFacts(last_success_at=last_success)
        prov = assemble_provenance("ban", facts, now=_NOW)
        assert prov.dataset == "ban"
        assert prov.producteur == "DINUM / IGN"
        assert prov.derniere_importation_reussie == last_success


# ---------------------------------------------------------------------------
# Purity: no framework/session/scheduler coupling
# ---------------------------------------------------------------------------


def _make_row(dataset_name: str, **overrides) -> dict:
    """Build a batch-query row dict with all columns present."""
    row = {
        "dataset_name": dataset_name,
        "status": "disponible",
        "last_updated_at": _NOW - timedelta(hours=1),
        "row_count": 10_000,
        "last_import_duration_seconds": 120.0,
        "cron_schedule": None,
        "freshness_threshold_days": 7,
        "source_period_label": None,
        "source_updated_at": None,
        "effective_departments": None,
        "last_run_status": "success",
        "last_run_started_at": _NOW - timedelta(hours=1),
        "last_run_completed_at": _NOW - timedelta(minutes=30),
        "last_run_duration": 120.0,
        "last_run_rows": 10_000,
        "last_run_error": None,
        "last_success_at": _NOW - timedelta(minutes=30),
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Row assembly (build_dataset_info) — legacy fields + provenance
# ---------------------------------------------------------------------------


class TestBuildDatasetInfo:
    def test_legacy_fields_preserved_alongside_provenance(self):
        row = _make_row(
            "ban", source_updated_at=_NOW - timedelta(days=3), cron_schedule="0 2 * * 0"
        )
        info = build_dataset_info(row, now=_NOW)
        # Historical contract fields unchanged
        assert info.nom == "ban"
        assert info.statut == "disponible"
        assert info.nombre_lignes == 10_000
        assert info.cron_schedule == "0 2 * * 0"
        assert info.fraicheur in ("fresh", "stale", "outdated")
        assert info.etat_pipeline in ("sain", "degrade", "echec")
        assert info.derniere_run is not None
        # Provenance added and complete
        assert info.provenance is not None
        assert info.provenance.dataset == "ban"
        assert info.provenance.etat == EditorialState.ACTUEL

    def test_derniere_importation_equals_last_success(self):
        last_success = _NOW - timedelta(hours=5)
        row = _make_row("dvf", last_success_at=last_success)
        info = build_dataset_info(row, now=_NOW)
        assert info.provenance is not None
        assert info.provenance.derniere_importation_reussie == last_success

    def test_off_catalog_dataset_yields_none_provenance(self):
        row = _make_row("not_a_real_dataset")
        info = build_dataset_info(row, now=_NOW)
        assert info.provenance is None
        assert info.nom == "not_a_real_dataset"


# ---------------------------------------------------------------------------
# Endpoint contract (/v1/datasets) — mocked DB, single batch query
# ---------------------------------------------------------------------------


class TestDatasetsEndpointProvenance:
    @pytest.fixture()
    def _mock_rows(self):
        rows = [
            _make_row("ban", source_updated_at=_NOW - timedelta(days=2)),
            _make_row("dvf", last_success_at=_NOW - timedelta(hours=4)),
        ]
        mock = AsyncMock(return_value=rows)
        with patch("app.datasets.router.get_all_datasets", new=mock):
            yield mock

    @pytest.mark.asyncio
    async def test_endpoint_preserves_legacy_and_adds_provenance(
        self, client, _mock_rows
    ):
        resp = await client.get("/v1/datasets")
        assert resp.status_code == 200, resp.text
        data = resp.json()["donnees"]
        ban = next(d for d in data if d["nom"] == "ban")
        # Legacy fields still present
        for field in (
            "nom",
            "statut",
            "derniere_maj",
            "nombre_lignes",
            "cron_schedule",
            "fraicheur",
            "etat_pipeline",
            "prochaine_maj_prevue",
            "derniere_run",
        ):
            assert field in ban, f"legacy field {field} missing"
        # Provenance complete
        assert ban["provenance"] is not None
        prov = ban["provenance"]
        for field in (
            "nom",
            "licence",
            "url",
            "producteur",
            "dataset",
            "periode",
            "couverture",
            "cadence",
            "etat",
        ):
            assert field in prov, f"provenance field {field} missing"

    @pytest.mark.asyncio
    async def test_endpoint_calls_query_exactly_once(self, client, _mock_rows):
        """Batch: a single DB query, no per-row DB call in the router loop."""
        resp = await client.get("/v1/datasets")
        assert resp.status_code == 200
        assert _mock_rows.await_count == 1

    @pytest.mark.asyncio
    async def test_endpoint_derniere_importation_matches_last_success(
        self, client, _mock_rows
    ):
        resp = await client.get("/v1/datasets")
        data = resp.json()["donnees"]
        dvf = next(d for d in data if d["nom"] == "dvf")
        # dvf row last_success_at = _NOW - 4h ; provenance must echo it exactly
        assert dvf["provenance"]["derniere_importation_reussie"] is not None


class TestServicePurity:
    def test_module_imports_no_framework_session_or_scheduler(self):
        import app.datasets.provenance as prov_mod

        tree = ast.parse(inspect.getsource(prov_mod))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        forbidden = ("fastapi", "sqlalchemy", "scheduler")
        offending = [mod for mod in imported if any(tok in mod for tok in forbidden)]
        assert not offending, f"forbidden imports in provenance service: {offending}"
