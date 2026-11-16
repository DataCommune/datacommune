"""Explicit instance scopes for data-quality tests (D-21: no hidden default)."""

from app.etl.base import DEPARTEMENTS
from app.scope import InstanceScope


def scope(*departements: str) -> InstanceScope:
    return InstanceScope(frozenset(), tuple(departements))


FULL_FRANCE_SCOPE = scope(*DEPARTEMENTS)
