"""Row floors of national files filtered to the hosted departements (D-22, D-23).

A national file is not uniformly dense across departements, so a share of a
full-France floor says little about one departement. Each hosted departement
gets its own floor:

- measured reference: 80 % of the rows the reference database published for
  (dataset, departement) — same convention as the per-departement profiles
  of ``app.data_quality.profiles`` ("count 44+85 -> scoped floor ~80 %");
- otherwise the proportional share of the full-France floor times a
  per-dataset margin.

A truncated departement is refused even when another one compensates in the
total. Code constants, never read from tests at run time; the reference is
locked against ``tests/fixtures/fidelity_ct275/level1_counts.json`` by
``tests/test_scope_floors.py``.

Row floors only: which departements a source publishes at all is declared in
the catalogue (``SourceCoverage``, ``app.common.source_catalog``).

Per-departement sources with producer counts (plan 04.1-17,
``app.scope.producer_counts``, generated from ``tests/fixtures/producteurs``): each
departement gets 80 % of the count its producer published, in the unit the
caller counts (``lignes_importees`` by the import, ``lignes_stockees`` by the
quality profile); no extrapolation of 44/85. A departement the source does
not publish, or that cannot be hosted, has no floor; a hosted departement the
source publishes without a count is an error, never a zero floor.
"""

from collections.abc import Iterable
from typing import Literal

from app.common.source_catalog import DATASET_CATALOG, source_covers
from app.etl.base import DEPARTEMENTS
from app.scope.model import DEPARTEMENTS_HEBERGEABLES
from app.scope.producer_counts import PRODUCER_COUNTS

REFERENCE_FLOOR_RATIO = 0.8

# Published rows per (pipeline dataset, departement) in the legacy production reference
# database, extracted 2026-09-21 (level1_counts.json, table in comment).
REFERENCE_COUNTS: dict[str, dict[str, int]] = {
    "antennes": {"44": 15_156, "85": 9_110},
    "copropriete": {"44": 8_137, "85": 2_822},
    "demographie": {"44": 207, "85": 255},
    "equipements": {"44": 49_211, "85": 26_092},
    "fiscalite": {"44": 1_035, "85": 1_278},  # fiscalite_locale
    "foncier_pm": {"44": 359_855, "85": 273_776},
    "loyers": {"44": 828, "85": 1_012},  # indicateurs_loyers
    "monuments": {"44": 231, "85": 336},  # monuments_historiques
    "permis": {"44": 0, "85": 0},  # source local to 93 (KI-16)
    "revenus": {"44": 207, "85": 257},
    "vacance": {"44": 207, "85": 253},
    "zonage_abc": {"44": 207, "85": 253},
    # The reference attributes carte_scolaire by code_departement, which the
    # source writes on three digits ("044"): 0 there. Rows of the source
    # itself by code_insee, data.education.gouv.fr export of 2026-09-22
    # (520 678 rows, sha256 34d19fc6...ac2e4).
    "scolaire": {"44": 11_940, "85": 6_039},
}

# Margin on the proportional share, for departements without a reference and
# datasets whose rows the reference could not attribute (transports:
# coordinates only; encadrement_loyers: city and district). The lowest
# single-departement density observed in the reference is 0.53 of the mean
# (monuments, 44); 0.60 for the commune-level files in 44. A margin of 0.5
# stays under it; a departement amputated by more than half is refused.
FALLBACK_MARGINS: dict[str, float] = {
    "antennes": 0.5,
    "copropriete": 0.5,
    "demographie": 0.5,
    "encadrement_loyers": 0.5,
    "equipements": 0.5,
    "fiscalite": 0.5,
    "foncier_pm": 0.5,
    "loyers": 0.5,
    "monuments": 0.5,
    "permis": 0.5,
    "revenus": 0.5,
    "scolaire": 0.5,
    "transports": 0.5,
    "vacance": 0.5,
    "zonage_abc": 0.5,
}


def has_reference_count(dataset: str, departement: str) -> bool:
    """A legacy production reference count exists for this (dataset, departement)."""
    return REFERENCE_COUNTS.get(dataset, {}).get(departement) is not None


def departement_floor(dataset: str, departement: str, full_france_min: int) -> int:
    """Minimum rows of one hosted departement for a filtered national file."""
    reference = REFERENCE_COUNTS.get(dataset, {}).get(departement)
    if reference is not None:
        return int(reference * REFERENCE_FLOOR_RATIO)
    share = full_france_min / len(DEPARTEMENTS) * FALLBACK_MARGINS[dataset]
    return max(1, int(share))


UniteProducteur = Literal["lignes_importees", "lignes_stockees"]
# Pipeline dataset -> catalogue key, when they differ.
_CATALOG_KEY = {"rnb_liens": "rnb", "bdnb_batiment_parcelle": "bdnb"}


class ProducerCountMissingError(RuntimeError):
    """A hosted departement the source publishes has no producer count."""

    def __init__(self, dataset: str, departement: str) -> None:
        super().__init__(
            f"no producer count for {dataset} in departement {departement}: "
            "regenerate app/scope/producer_counts.py (fidelite_nationale floors)"
        )


def _source_publishes(dataset: str, departement: str) -> bool:
    entry = DATASET_CATALOG.get(_CATALOG_KEY.get(dataset, dataset))
    return entry is None or source_covers(entry, departement)


def producer_departement_floor(
    dataset: str, departement: str, unite: UniteProducteur
) -> int | None:
    """80 % of the producer count of one departement; None without a floor.

    None when the dataset has no producer counts (the former floors apply),
    the departement cannot be hosted (D-123) or the source does not publish it.
    """
    comptes = PRODUCER_COUNTS.get(dataset)
    if (
        comptes is None
        or departement not in DEPARTEMENTS_HEBERGEABLES
        or not _source_publishes(dataset, departement)
    ):
        return None
    compte = comptes[unite].get(departement)
    if compte is None:
        raise ProducerCountMissingError(dataset, departement)
    return int(compte * REFERENCE_FLOOR_RATIO)


def producer_floor(
    dataset: str, departements: Iterable[str], unite: UniteProducteur
) -> int | None:
    """Sum of the departement floors of *departements*; None without counts."""
    if dataset not in PRODUCER_COUNTS:
        return None
    floors = (
        producer_departement_floor(dataset, code, unite) for code in set(departements)
    )
    return sum(floor for floor in floors if floor is not None)


def national_floor(
    dataset: str, departements: Iterable[str], full_france_min: int
) -> int:
    """Total floor: sum of the departement floors (full France: unchanged)."""
    codes = set(departements)
    if codes >= set(DEPARTEMENTS):
        return full_france_min
    return sum(departement_floor(dataset, code, full_france_min) for code in codes)
