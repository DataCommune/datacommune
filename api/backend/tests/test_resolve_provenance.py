"""Provenance + typed absence contract for /v1/resolve (Phase 44-03, PROV-01/02).

Unit-level, no DB: the router's assembly helpers are pure and testable in
isolation. Verifies deterministic source expansion/ordering/dedup in
``meta.source`` and the Phase 43 absence taxonomy in ``dataset_states``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.common.source_catalog import expand_source_keys
from app.resolve.router import _assemble_provenance_list
from app.resolve.schemas import (
    DatasetResolutionState,
    resolution_state,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Typed absence taxonomy (D-04)
# ---------------------------------------------------------------------------


class TestResolutionState:
    def test_value_when_data_present(self) -> None:
        state = resolution_state("dvf", [{"id": 1}], is_error=False)
        assert state.presence == "value"
        assert state.absence_reason is None

    def test_not_reported_when_none(self) -> None:
        state = resolution_state("loyers", None, is_error=False)
        assert state.presence == "absent"
        assert state.absence_reason == "not_reported"
        assert state.raison

    def test_not_reported_when_empty_list(self) -> None:
        state = resolution_state("monuments", [], is_error=False)
        assert state.presence == "absent"
        assert state.absence_reason == "not_reported"

    def test_temporary_error_on_error(self) -> None:
        state = resolution_state("risques", None, is_error=True)
        assert state.presence == "error"
        assert state.absence_reason == "temporary_error"

    def test_not_applicable_never_inferred(self) -> None:
        # No code path produces not_applicable from the factory.
        for data in (None, [], [{"x": 1}]):
            for is_err in (True, False):
                state = resolution_state("x", data, is_error=is_err)
                assert state.absence_reason != "not_applicable"

    def test_rejects_taxonomy_outside_phase43(self) -> None:
        with pytest.raises(ValueError):
            DatasetResolutionState(
                dataset="x", presence="absent", absence_reason="bogus"
            )

    def test_value_forbids_absence_reason(self) -> None:
        with pytest.raises(ValueError):
            DatasetResolutionState(
                dataset="x", presence="value", absence_reason="not_reported"
            )


# ---------------------------------------------------------------------------
# Deterministic provenance expansion in meta.source (PROV-01/02)
# ---------------------------------------------------------------------------


class TestProvenanceList:
    def test_aggregate_expands_ordered_and_deduped(self) -> None:
        keys = expand_source_keys(
            ["ban", "risques", "pollutions", "entreprises", "commune"]
        )
        provs = _assemble_provenance_list(keys, {}, _NOW)
        datasets = [p.dataset for p in provs]

        # georisques once (risques + pollutions collapse), sirene once
        assert datasets.count("georisques") == 1
        assert datasets.count("sirene") == 1

        # ban first, then the two aggregate heads, then the 13 commune sources
        assert datasets[:3] == ["ban", "georisques", "sirene"]
        commune_sources = [
            "demographie",
            "revenus",
            "zonage_abc",
            "vacance",
            "fiscalite",
            "loyers",
            "dvf",
            "dpe",
            "equipements",
            "scolaire",
            "monuments",
            "foncier_pm",
            "connectivite",
        ]
        assert datasets[3:] == commune_sources
        assert len(datasets) == 16

    def test_every_prov_carries_static_attribution(self) -> None:
        provs = _assemble_provenance_list(["ban", "dvf"], {}, _NOW)
        for p in provs:
            assert p.nom and p.licence and p.url and p.producteur
            assert p.cadence is not None
