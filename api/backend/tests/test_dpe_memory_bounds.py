"""Memory bounds of the DPE reconciliation and lifecycle (plan 04.1-11).

``tracemalloc`` sees Python allocations only: SQLite keeps its own page cache
(2 MiB per connection by default), constant whatever the corpus. The real RSS
of a first import and of a refresh is measured on the archived generation by
plan 04.1-20.
"""

import gc
import tracemalloc
from collections import deque
from collections.abc import Callable, Iterator

from app.dpe.lifecycle import iter_lifecycle_facts
from app.etl_dpe.reconciliation import collect_presence, reconcile_observations
from tests.test_dpe_reconciliation import LATER, manifest_at, served

PREVIOUS_ROWS = 20_000
RECONCILIATION_GROWTH = 1.25
GRAPH_DOCUMENTS = 50_000
LIFECYCLE_BYTES_PER_DOCUMENT = 64
# National DPE documents (internal note 2026-09-30-taille-precise-base-france).
NATIONAL_DOCUMENTS = 16_428_032


def _peak(run: Callable[[], None]) -> int:
    gc.collect()
    tracemalloc.start()
    try:
        run()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def _previous(count: int) -> Iterator[dict]:
    """Served rows, built one at a time: the source never holds the corpus."""
    for index in range(count):
        number = f"2644E{index:010d}"
        yield served(number, id=index, identifiant_ban=f"44109_{index:07d}_00001")


def _reconciliation_peak(tmp_path, count: int) -> int:
    manifest = manifest_at(tmp_path, generation=f"empty-{count}", observed_at=LATER)
    with collect_presence(manifest, directory=tmp_path) as registry:
        return _peak(
            lambda: deque(
                reconcile_observations(
                    _previous(count), [], manifest, registry=registry
                ),
                maxlen=0,
            )
        )


def test_reconciliation_peak_does_not_grow_with_previous_rows(tmp_path):
    small = _reconciliation_peak(tmp_path, PREVIOUS_ROWS)
    large = _reconciliation_peak(tmp_path, 4 * PREVIOUS_ROWS)
    print(
        f"reconciliation peak: {small} B at {PREVIOUS_ROWS} previous rows, "
        f"{large} B at {4 * PREVIOUS_ROWS} ({large / small:.2f}x)"
    )
    assert large <= RECONCILIATION_GROWTH * small, (small, large)


def _graph(count: int) -> Iterator[dict]:
    """Staged rows, one in three replacing its predecessor, built one at a time."""
    for index in range(count):
        yield {
            "numero_dpe": f"2644E{index:010d}",
            "numero_dpe_remplace": f"2644E{index - 1:010d}" if index % 3 else None,
            "lifecycle_facts": {"date_issues": []},
        }


def _lifecycle_peak(tmp_path, count: int) -> int:
    return _peak(
        lambda: deque(iter_lifecycle_facts(_graph(count), directory=tmp_path), maxlen=0)
    )


def test_lifecycle_bytes_per_document_and_national_projection(tmp_path):
    small = _lifecycle_peak(tmp_path, GRAPH_DOCUMENTS)
    large = _lifecycle_peak(tmp_path, 4 * GRAPH_DOCUMENTS)
    slope = (large - small) / (3 * GRAPH_DOCUMENTS)
    print(
        f"lifecycle peak: {small} B at {GRAPH_DOCUMENTS} documents, {large} B at "
        f"{4 * GRAPH_DOCUMENTS}; {slope:.1f} B per document; projection "
        f"{slope * NATIONAL_DOCUMENTS / 2**20:.0f} MiB at {NATIONAL_DOCUMENTS}"
    )
    assert slope <= LIFECYCLE_BYTES_PER_DOCUMENT, (small, large, slope)
    assert list(tmp_path.glob("dpe-lifecycle-*")) == []
