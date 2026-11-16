"""Hosted instance scope (datasets x departements), persisted in the database."""

# app.etl (its runner) imports app.scope.model, which imports app.etl.base:
# initialise app.etl first so ``python -m app.scope`` never enters that cycle
# from this side.
import app.etl  # noqa: F401
from app.scope.model import (
    SCOPE_EXCLUDED_KEYS,
    InstanceScope,
    departement_from_insee,
    parse_departements,
)
from app.scope.store import load_scope, load_scope_sync, replace_scope, validate_scope

__all__ = [
    "SCOPE_EXCLUDED_KEYS",
    "InstanceScope",
    "departement_from_insee",
    "load_scope",
    "load_scope_sync",
    "parse_departements",
    "replace_scope",
    "validate_scope",
]
