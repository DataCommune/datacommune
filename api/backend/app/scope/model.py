"""Hosted instance scope: which catalogue datasets and departements (D-21, D-22).

The scope has no default. It lives in the database (``instance_scope_*``
tables, migration 097); an empty scope hosts nothing.

Overseas collectivities (D-123): 975, 977, 978 and 984-989 are never hosted.
``DEPARTEMENTS`` keeps 975 (its partitions exist since the first migrations,
removing them would cost a migration for nothing); ``DEPARTEMENTS_HEBERGEABLES``
is the list a scope may name. An INSEE code of a collectivity is « hors
couverture », reason ``territoire_hors_perimetre``, never unknown nor zero
(``territoire_couverture``). ``departement_from_insee`` and the SQL function
``dc_departement_from_insee`` are unchanged: same rule everywhere.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from app.etl.base import DEPARTEMENTS
from app.etl.errors import ScopeLockError

# ``commune`` is a synthesis derived from its source datasets, never imported
# on its own: hosting it means hosting its sources.
SCOPE_EXCLUDED_KEYS: frozenset[str] = frozenset({"commune"})

_DEPARTEMENT_ORDER = {code: index for index, code in enumerate(DEPARTEMENTS)}

# Overseas collectivities (D-123): an INSEE code of one starts with these
# three digits. Out of coverage, never hosted.
COLLECTIVITES_OUTRE_MER: frozenset[str] = frozenset(
    {"975", "977", "978", "984", "986", "987", "988", "989"}
)
COLLECTIVITE_HORS_COUVERTURE = "collectivité d'outre-mer hors couverture (D-123)"

# The departements a scope may host: 96 metropolitan and the five DROM.
DEPARTEMENTS_HEBERGEABLES: tuple[str, ...] = tuple(
    code for code in DEPARTEMENTS if code not in COLLECTIVITES_OUTRE_MER
)

# Departement of a point: the departement polygon covering it (reference
# table geo_departements, metropolitan departements). ORDER BY makes a point
# on a shared border deterministic. Code constant; {point} is a code constant.
POINT_DEPARTEMENT_SQL = (
    "(SELECT g.code FROM geo_departements g "
    "WHERE ST_Covers(g.geometry, {point}) ORDER BY g.code LIMIT 1)"
)


# Schema locks: departements a migration hard-codes for a dataset or view.
# Recorded until a new migration widens them. key -> (accepted departements,
# origin). None left: DPE lifted by migration 101 (04.1-07), the scoring view
# scoring_renov_target by migration 102 (04.1-08). The mechanism stays for any
# future lock; 04.1-VERROUS.md lists the 44/85 literals outside the schema.
SCOPE_LOCKS: dict[str, tuple[tuple[str, ...], str]] = {}


def locked_departements(key: str, departements: Iterable[str]) -> list[str]:
    """Departements of *key* within its schema lock; refuse any outside it."""
    codes = sort_departements(set(departements))
    if key not in SCOPE_LOCKS:
        return codes
    allowed, origin = SCOPE_LOCKS[key]
    outside = [code for code in codes if code not in allowed]
    if outside:
        raise ScopeLockError(key, outside, allowed, origin)
    return codes


def scale_floor(full_france_min: int, departement_count: int) -> int:
    """Linear share of a full-France floor for *departement_count* departements.

    Per-departement sources: their floors are calibrated on this share. A
    national file filtered to the scope uses ``app.scope.floors`` instead.
    """
    if departement_count >= len(DEPARTEMENTS):
        return full_france_min
    if departement_count <= 0:
        return 0
    return max(1, int(full_france_min * departement_count / len(DEPARTEMENTS)))


@dataclass(frozen=True)
class InstanceScope:
    """Immutable scope read once per import."""

    datasets: frozenset[str]
    departements: tuple[str, ...]

    def hosts(self, key: str) -> bool:
        return key in self.datasets

    @property
    def is_empty(self) -> bool:
        return not self.departements

    @property
    def covers_all_departements(self) -> bool:
        """Every hostable departement (a collectivity is never hosted, D-123)."""
        return set(self.departements) >= set(DEPARTEMENTS_HEBERGEABLES)

    def as_json(self) -> dict[str, list[str]]:
        return {
            "datasets": sorted(self.datasets),
            "departements": sort_departements(self.departements),
        }


def sort_departements(codes: "tuple[str, ...] | list[str] | set[str]") -> list[str]:
    """Canonical order of the 102 codes (01..19, 2A, 2B, 21..95, 971..976)."""
    return sorted(set(codes), key=lambda code: _DEPARTEMENT_ORDER[code])


def refuse_collectivites(codes: Iterable[str]) -> None:
    """Refuse a scope naming an overseas collectivity (D-123)."""
    named = sorted({code for code in codes if code in COLLECTIVITES_OUTRE_MER})
    if named:
        raise ValueError(f"{COLLECTIVITE_HORS_COUVERTURE}: {named}")


def parse_departements(raw: str) -> list[str]:
    """Parse a comma-separated departement list; reject unknown codes and
    overseas collectivities (D-123)."""
    requested = [code.strip().upper() for code in raw.split(",") if code.strip()]
    invalid = sorted({code for code in requested if code not in _DEPARTEMENT_ORDER})
    if invalid:
        raise ValueError(f"Unknown departement codes: {invalid}")
    refuse_collectivites(requested)
    return sort_departements(requested)


def departement_from_insee(code: str) -> str:
    """Departement of an INSEE commune code; same rule as dc_departement_from_insee.

    Overseas codes carry three digits (97411 -> 974); Corsican codes already
    carry 2A/2B.
    """
    return code[:3] if code.startswith("97") else code[:2]


def collectivite_outre_mer(code_insee: str | None) -> str | None:
    """Collectivity (975, 977, ...) of an INSEE commune code, else None."""
    prefix = (code_insee or "").strip().upper()[:3]
    return prefix if prefix in COLLECTIVITES_OUTRE_MER else None


def territoire_couverture(code_insee: str | None) -> str | None:
    """Territory a coverage rule reads for an INSEE commune code.

    A collectivity first (out of coverage, D-123), then the departement when
    it is hostable, else None: a code naming no territory is never a proof of
    « hors couverture » (02-07 rule) and stays unknown.
    """
    if collectivite := collectivite_outre_mer(code_insee):
        return collectivite
    code = (code_insee or "").strip().upper()
    if not code:
        return None
    departement = departement_from_insee(code)
    return departement if departement in DEPARTEMENTS_HEBERGEABLES else None
