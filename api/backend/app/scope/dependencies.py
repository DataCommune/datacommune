"""FastAPI dependencies: the hosted scope, read from the database per request."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.geo import commune_at_point
from app.common.schemas import DatasetHosting
from app.common.source_catalog import DATASET_CATALOG
from app.database import get_db
from app.etl.base import DEPARTEMENTS
from app.scope.coverage import hosting_for, hosting_for_key
from app.scope.model import SCOPE_EXCLUDED_KEYS, InstanceScope, territoire_couverture
from app.scope.store import load_scope


async def get_instance_scope(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> InstanceScope:
    """The only scope source of the API (D-23): no settings fallback."""
    return await load_scope(db)


def real_departement(code: str | None) -> str | None:
    """*code* when it names a real departement, else None.

    A code naming no departement is never a proof of « hors couverture »
    (02-07 rule).
    """
    departement = (code or "").strip().upper()
    return departement if departement in DEPARTEMENTS else None


def commune_departement(code_insee: str | None) -> str | None:
    """Territory of a commune INSEE code (2A004 -> 2A, 97105 -> 971).

    An overseas collectivity gives its own code (97701 -> 977), which no
    scope hosts: « hors couverture », reason ``territoire_hors_perimetre``,
    never unknown (D-123, ``territoire_couverture``). A code naming no
    territory gives None.
    """
    return territoire_couverture(code_insee)


def code_insee_departement(request: Request) -> str | None:
    """Territory of a route located by ``code_insee``.

    ``code_insee`` wins over lat/lon, as in ``resolve_location``; without it
    (GPS mode) the territory stays unknown (None), as before.
    """
    return commune_departement(request.query_params.get("code_insee"))


@dataclass(frozen=True)
class RouteHosting:
    """Hosting of every key a route serves, computed before its handler runs."""

    hostings: dict[str, DatasetHosting]

    def hosts(self, key: str) -> bool:
        return self.hostings[key].etat == "heberge"

    @property
    def none_hosted(self) -> bool:
        """Every served key is out of coverage: read no table, call no upstream."""
        return not any(self.hosts(key) for key in self.hostings)

    def meta(self, meta: dict[str, Any], keys: Iterable[str] | None = None) -> dict:
        """*meta* plus ``hebergement`` (one entry per served key) when one is out.

        A route whose keys are all hosted keeps its response unchanged.
        """
        served = {key: self.hostings[key] for key in (keys or self.hostings)}
        if all(hosting.etat == "heberge" for hosting in served.values()):
            return meta
        return {**meta, "hebergement": served}


def _hosting(key: str, scope: InstanceScope, departement: str | None) -> DatasetHosting:
    # A synthesis (commune) is hosted through its sources (02-07 rule) and
    # its structural one (AGGREGATE_REQUIRED_SOURCES).
    if key in SCOPE_EXCLUDED_KEYS:
        return hosting_for_key(key, scope, departement)
    return hosting_for(key, scope, departement)


def route_hosting(
    keys: Iterable[str], scope: InstanceScope, departement: str | None
) -> RouteHosting:
    """Hosting of *keys* for *departement*, as :func:`hosted_datasets` computes it.

    For a route serving several territories at once (the address comparison,
    04-07, N-07): each compared object gets the hosting of its own
    departement, computed with the same rule as a single-object route.
    """
    return RouteHosting({key: _hosting(key, scope, departement) for key in keys})


async def hosting_at_point(
    db: AsyncSession,
    scope: InstanceScope,
    hosting: RouteHosting,
    lat: float,
    lon: float,
) -> RouteHosting:
    """*hosting* requalified for the departement of the commune covering a point.

    C52 (B2): a generic route located by a point qualifies the departement it
    is asked about, not « hosted somewhere ». The commune comes from the
    referential (``geo_arrondissements`` then ``geo_communes``), never from a
    dataset table. A point in no commune, or a code naming no departement,
    keeps *hosting* unchanged: no proof of « hors couverture » (02-07 rule).
    The qualification holds for the departement of the point only, not for
    every departement a search radius reaches.
    """
    departement = commune_departement(await commune_at_point(db, lat, lon))
    if departement is None:
        return hosting
    return route_hosting(hosting.hostings, scope, departement)


def hosted_datasets(
    *keys: str,
    departement: Callable[[Request], str | None] | None = None,
) -> Callable[..., Any]:
    """Dependency declaring the catalogue keys a route serves (DATA-03).

    It reads the persisted scope and computes each key's hosting before the
    handler; the handler returns its empty form without any read when
    :attr:`RouteHosting.none_hosted`. *departement* derives the territory from
    the request, for routes that carry one. ``served_keys`` is read by the
    route inventory test.
    """
    unknown = [key for key in keys if key not in DATASET_CATALOG]
    if unknown or not keys:
        raise KeyError(f"hosted_datasets needs catalogue keys, got {unknown or keys}")

    async def dependency(
        request: Request,
        scope: Annotated[InstanceScope, Depends(get_instance_scope)],
    ) -> RouteHosting:
        territory = departement(request) if departement is not None else None
        return route_hosting(keys, scope, territory)

    dependency.served_keys = keys  # type: ignore[attr-defined]
    return dependency
