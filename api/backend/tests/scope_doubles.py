"""Hosted scopes served to the API by DB-free tests (D-23).

``/v1/resolve`` and ``/v1/datasets`` read the scope through the
``get_instance_scope`` dependency; tests without a database override it with
one of these. PostGIS tests write their scope into the database instead.
"""

from app.common.source_catalog import DATASET_CATALOG
from app.etl.base import DEPARTEMENTS
from app.scope.coverage import hosting_for
from app.scope.dependencies import RouteHosting
from app.scope.model import SCOPE_EXCLUDED_KEYS, InstanceScope


def catalogue_scope(departements: tuple[str, ...]) -> InstanceScope:
    """The whole catalogue (but ``commune``) on *departements*."""
    return InstanceScope(
        datasets=frozenset(DATASET_CATALOG) - SCOPE_EXCLUDED_KEYS,
        departements=departements,
    )


# Nothing out of coverage: what these tests saw before the scope moved to the
# database (no configured scope = no coverage proof).
FULL_SCOPE = catalogue_scope(tuple(DEPARTEMENTS))
SCOPE_44_85 = catalogue_scope(("44", "85"))


def route_hosting(*keys: str, scope: InstanceScope = FULL_SCOPE) -> RouteHosting:
    """What ``hosted_datasets`` hands a handler called directly by a test."""
    return RouteHosting({key: hosting_for(key, scope, None) for key in keys})
