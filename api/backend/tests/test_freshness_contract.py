"""Freshness → resolve state contract (Phase 44-03, PROV-03).

Proves the router only upgrades ``not_reported`` to ``source_delayed`` or
``out_of_coverage`` with actual proof (editorial delay / effective coverage),
staying ``not_reported`` otherwise. No new taxonomy is introduced.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.common.schemas import EditorialState, OutOfCoverageReason
from app.datasets.provenance import ProvenanceFacts, assemble_provenance
from app.resolve.router import _departement_from_cp, _refine_dataset_states
from app.resolve.schemas import resolution_state
from app.scope.model import InstanceScope

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)
_SCOPE = InstanceScope(datasets=frozenset({"dpe", "loyers"}), departements=("44", "85"))


def _delayed_dpe():
    """dpe (official weekly cadence) with an upstream date 30 days old = retarde."""
    facts = ProvenanceFacts(source_updated_at=_NOW - timedelta(days=30))
    prov = assemble_provenance("dpe", facts, _NOW)
    assert prov.etat == EditorialState.RETARDE
    return prov


def _fresh_dpe():
    facts = ProvenanceFacts(source_updated_at=_NOW - timedelta(days=1))
    prov = assemble_provenance("dpe", facts, _NOW)
    assert prov.etat == EditorialState.ACTUEL
    return prov


def _scoped_loyers(departments):
    facts = ProvenanceFacts(effective_departments=departments)
    return assemble_provenance("loyers", facts, _NOW)


class TestDepartementFromCp:
    def test_extracts_two_digits(self) -> None:
        assert _departement_from_cp("44000") == "44"
        assert _departement_from_cp("85300") == "85"

    def test_none_on_garbage(self) -> None:
        assert _departement_from_cp(None) is None
        assert _departement_from_cp("") is None
        assert _departement_from_cp("A") is None


class TestRefineStates:
    def test_not_reported_stays_without_proof(self) -> None:
        states = {"dpe": resolution_state("dpe", None, is_error=False)}
        _refine_dataset_states(states, {"dpe": _fresh_dpe()}, "44000", scope=_SCOPE)
        assert states["dpe"].absence_reason == "not_reported"

    def test_source_delayed_only_with_proof(self) -> None:
        states = {"dpe": resolution_state("dpe", None, is_error=False)}
        _refine_dataset_states(states, {"dpe": _delayed_dpe()}, "44000", scope=_SCOPE)
        assert states["dpe"].absence_reason == "source_delayed"
        assert states["dpe"].raison

    def test_value_state_untouched(self) -> None:
        states = {"dpe": resolution_state("dpe", [{"x": 1}], is_error=False)}
        _refine_dataset_states(states, {"dpe": _delayed_dpe()}, "44000", scope=_SCOPE)
        assert states["dpe"].presence == "value"
        assert states["dpe"].absence_reason is None

    def test_error_state_untouched(self) -> None:
        states = {"dpe": resolution_state("dpe", None, is_error=True)}
        _refine_dataset_states(states, {"dpe": _delayed_dpe()}, "44000", scope=_SCOPE)
        assert states["dpe"].absence_reason == "temporary_error"

    def test_out_of_coverage_with_department_proof(self) -> None:
        states = {"loyers": resolution_state("loyers", None, is_error=False)}
        _refine_dataset_states(
            states, {"loyers": _scoped_loyers(["44"])}, "85000", scope=_SCOPE
        )
        assert states["loyers"].absence_reason == "out_of_coverage"
        # 85 is hosted but the imported source holds only 44.
        assert states["loyers"].motif == OutOfCoverageReason.SOURCE_SANS_COUVERTURE

    def test_out_of_coverage_outside_the_hosted_departements(self) -> None:
        states = {"loyers": resolution_state("loyers", None, is_error=False)}
        loyers = {"loyers": _scoped_loyers(["44"])}
        _refine_dataset_states(states, loyers, "75001", scope=_SCOPE)
        assert states["loyers"].absence_reason == "out_of_coverage"
        assert states["loyers"].motif == OutOfCoverageReason.TERRITOIRE_HORS_PERIMETRE

    def test_covered_department_stays_not_reported(self) -> None:
        states = {"loyers": resolution_state("loyers", None, is_error=False)}
        _refine_dataset_states(
            states, {"loyers": _scoped_loyers(["44"])}, "44000", scope=_SCOPE
        )
        assert states["loyers"].absence_reason == "not_reported"

    def test_no_department_no_coverage_upgrade(self) -> None:
        states = {"loyers": resolution_state("loyers", None, is_error=False)}
        _refine_dataset_states(
            states, {"loyers": _scoped_loyers(["44"])}, None, scope=_SCOPE
        )
        assert states["loyers"].absence_reason == "not_reported"
