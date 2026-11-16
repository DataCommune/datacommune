"""Single rule for « hors couverture » and its reason (D-24).

Pure functions over the persisted scope (D-23): no settings, no I/O. Every
route that declares a dataset hosted or out of coverage goes through here.
"""

from collections.abc import Iterable

from app.common.schemas import DatasetHosting, OutOfCoverageReason
from app.common.source_catalog import (
    AGGREGATE_REQUIRED_SOURCES,
    DATASET_CATALOG,
    RESOLVER_SOURCE_MAP,
    covered_departements,
    source_covers,
)
from app.scope.model import (
    COLLECTIVITES_OUTRE_MER,
    SCOPE_EXCLUDED_KEYS,
    InstanceScope,
    sort_departements,
)

# Precedence when several reasons apply (aggregates, D-24).
REASON_ORDER: tuple[OutOfCoverageReason, ...] = tuple(OutOfCoverageReason)


def _out(motif: OutOfCoverageReason) -> DatasetHosting:
    return DatasetHosting(etat="hors_couverture", motif=motif)


def hosting_for(
    key: str, scope: InstanceScope, departement: str | None
) -> DatasetHosting:
    """Hosting of catalogue source *key* for *departement* (None: any).

    Reasons, first match wins: dataset not hosted, then departement outside
    the hosted departements, then source without coverage — the catalogue
    (``source_coverage``) states the source publishes nothing for this
    departement, or, without a departement, for any hosted one. Hosted: the
    departements served are the hosted ones the source publishes.
    """
    if key in SCOPE_EXCLUDED_KEYS:
        raise ValueError(f"{key!r} is a synthesis: ask for its sources")
    if key not in DATASET_CATALOG:
        raise KeyError(key)
    # A scope without departement hosts nothing (same rule as the import).
    if not scope.hosts(key) or scope.is_empty:
        return _out(OutOfCoverageReason.DATASET_NON_HEBERGE)
    if departement is not None:
        # A collectivity is never in the perimeter (D-123), even if a scope
        # written by code (not the CLI) names one.
        if (
            departement not in scope.departements
            or departement in COLLECTIVITES_OUTRE_MER
        ):
            return _out(OutOfCoverageReason.TERRITOIRE_HORS_PERIMETRE)
        if not source_covers(DATASET_CATALOG[key], departement):
            return _out(OutOfCoverageReason.SOURCE_SANS_COUVERTURE)
    served = covered_departements((key,), scope.departements)
    if not served:
        return _out(OutOfCoverageReason.SOURCE_SANS_COUVERTURE)
    return DatasetHosting(etat="heberge", departements=sort_departements(served))


def hosting_by_source(
    sources: Iterable[str], scope: InstanceScope, departement: str | None
) -> dict[str, DatasetHosting]:
    """Hosting of each source of an aggregate key, in the given order."""
    return {source: hosting_for(source, scope, departement) for source in sources}


def hosting_for_sources(
    sources: Iterable[str],
    scope: InstanceScope,
    departement: str | None,
    *,
    required: Iterable[str] = (),
) -> DatasetHosting:
    """Hosting of a resolver key backed by one or more sources.

    A *required* (structural) source out of coverage takes the whole key out
    with its own reason, whatever the other sources. Otherwise the key is out
    of coverage only when every source is; the reason is then the first in
    :data:`REASON_ORDER` among the sources' reasons.
    """
    hostings = hosting_by_source(sources, scope, departement)
    if not hostings:
        raise ValueError("a resolver key has at least one source")
    for source in required:
        if hostings[source].etat != "heberge":
            return hostings[source]
    hosted = [h for h in hostings.values() if h.etat == "heberge"]
    if hosted:
        return hosted[0]
    reasons = {h.motif for h in hostings.values()}
    return _out(next(reason for reason in REASON_ORDER if reason in reasons))


def hosting_for_key(
    key: str, scope: InstanceScope, departement: str | None
) -> DatasetHosting:
    """Hosting of resolver key *key* through its sources and structural ones."""
    return hosting_for_sources(
        RESOLVER_SOURCE_MAP[key],
        scope,
        departement,
        required=AGGREGATE_REQUIRED_SOURCES.get(key, ()),
    )
