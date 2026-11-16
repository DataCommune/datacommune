"""Audit of served values against the semantic contract (SEM-01, SEM-02).

What it checks, and nothing more: the structure of the response, the
presence of a contract entry (unit, description) for every served numeric
value, a declared state for every flat column served null (D-47) and for every
indicator absence, each within the allowed states of its field, the
consistency of a state with its reason, and the ORIGIN of a known period in
the producer's facts. It checks neither the accuracy of a value
nor the correctness of an address/object match: those belong to the
real-data measures of plan 02-18.

The contract is read through :mod:`app.common.semantic_contract`, never a copy.
"""

from collections.abc import Iterator, Mapping
from typing import Any

from app.common.schemas import DatasetProvenance
from app.common.semantic_contract import (
    ALLOWED_OUT_OF_COVERAGE_REASONS,
    FieldContract,
    load_registry,
)
from app.commune.schemas import (
    COMMUNE_FIELD_SOURCES,
    COMMUNE_SOURCE_KEYS,
    NON_SEMANTIC_COMMUNE_COLUMNS,
    commune_field_id,
)
from app.datasets.provenance import ProvenanceFacts

_PERIOD_STATES = frozenset({"connue", "glissante", "inconnue"})


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _numbers(value: Any, path: tuple[str, ...]) -> Iterator[tuple[str, ...]]:
    """Paths of every numeric value inside *value*, nested objects included."""
    if _is_number(value):
        yield path
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _numbers(item, (*path, str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _numbers(item, (*path, f"[{index}]"))


def _audit_hosting(where: str, hosting: Any) -> list[str]:
    if not isinstance(hosting, Mapping):
        return [f"{where}: hosting state missing"]
    etat, motif = hosting.get("etat"), hosting.get("motif")
    if etat == "hors_couverture" and motif not in ALLOWED_OUT_OF_COVERAGE_REASONS:
        return [f"{where}: out of coverage without a known reason ({motif!r})"]
    if etat == "heberge" and motif is not None:
        return [f"{where}: reason {motif!r} on a hosted source"]
    if etat not in {"heberge", "hors_couverture"}:
        return [f"{where}: unknown hosting state {etat!r}"]
    return []


def _audit_column_states(
    where: str, row: Mapping[str, Any], couverture: Mapping[str, Any], contract
) -> list[str]:
    """D-47: every flat column served null has a declared, allowed state."""
    etats = row.get("etats") or {}
    if not isinstance(etats, Mapping):
        return [f"{where}.etats: not an object"]
    errors: list[str] = []
    for column, value in row.items():
        declared = isinstance(etats.get(column), Mapping)
        if value is None and commune_field_id((column,)) in contract and not declared:
            errors.append(f"{where}.{column}: null without a declared state")
    for column, state in etats.items():
        field_id = commune_field_id((column,))
        if field_id not in contract:
            errors.append(f"{where}.etats.{column}: not a flat column of the contract")
            continue
        if row.get(column) is not None:
            errors.append(f"{where}.etats.{column}: state declared on a served value")
            continue
        etat = state.get("etat") if isinstance(state, Mapping) else None
        if etat not in contract[field_id].allowed_states:
            errors.append(
                f"{where}.etats.{column}: state {etat!r} not in the allowed "
                f"states of {field_id}"
            )
        hosting = couverture.get(COMMUNE_FIELD_SOURCES[column]) or {}
        out_of_coverage = hosting.get("etat") == "hors_couverture"
        if out_of_coverage != (etat == "out_of_coverage") or (
            out_of_coverage and state.get("motif") != hosting.get("motif")
        ):
            errors.append(
                f"{where}.etats.{column}: state {etat!r} does not match the "
                "hosting of its source"
            )
    return errors


# Leaves that carry the value of an indicator object when it is not absent.
_INDICATOR_LEAVES = ("valeur", "p10", "p50", "p90")


def _absences(
    value: Any, path: tuple[str, ...]
) -> Iterator[tuple[tuple[str, ...], str]]:
    """(path, etat) of every indicator absence ``{etat, motif}`` in *value*."""
    if isinstance(value, Mapping):
        etat = value.get("etat")
        if isinstance(etat, str) and etat != "value" and "motif" in value:
            yield path, etat
            return
        for key, item in value.items():
            yield from _absences(item, (*path, str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _absences(item, (*path, f"[{index}]"))


def _audit_block_absences(where: str, row: Mapping[str, Any], contract) -> list[str]:
    """An indicator absence stays within the allowed states of its field."""
    errors: list[str] = []
    for column, value in row.items():
        if column in {"couverture", "etats"}:
            continue
        for path, etat in _absences(value, (column,)):
            field_ids = {commune_field_id((*path, leaf)) for leaf in _INDICATOR_LEAVES}
            for field_id in sorted(f for f in field_ids if f in contract):
                if etat not in contract[field_id].allowed_states:
                    errors.append(  # noqa: PERF401
                        f"{where}.{'.'.join(path)}: state {etat!r} not in the "
                        f"allowed states of {field_id}"
                    )
    return errors


def _audit_row(index: int, row: Mapping[str, Any], contract) -> list[str]:
    errors: list[str] = []
    where = f"donnees[{index}]"
    couverture = row.get("couverture")
    if not isinstance(couverture, Mapping):
        return [f"{where}: no couverture, the served values cannot be qualified"]
    for source, hosting in couverture.items():
        errors += _audit_hosting(f"{where}.couverture.{source}", hosting)
    for column, value in row.items():
        if column == "couverture" or column in NON_SEMANTIC_COMMUNE_COLUMNS:
            continue
        for path in _numbers(value, (column,)):
            if commune_field_id(path) not in contract:
                errors.append(
                    f"{where}.{'.'.join(path)}: numeric value without a "
                    "semantic contract entry"
                )
        source = COMMUNE_FIELD_SOURCES.get(column)
        hosting = couverture.get(source) if source else None
        served_zero = _is_number(value) and value == 0
        if served_zero and (hosting or {}).get("etat") != "heberge":
            errors.append(
                f"{where}.{column}: 0 served while its source {source!r} is "
                "out of coverage"
            )
    errors += _audit_column_states(where, row, couverture, contract)
    errors += _audit_block_absences(where, row, contract)
    return errors


def _audit_sources(meta: Mapping[str, Any]) -> list[str]:
    sources = meta.get("source")
    entries = sources if isinstance(sources, list) else [sources]
    errors: list[str] = []
    named = {entry.get("dataset") for entry in entries if isinstance(entry, Mapping)}
    if len(entries) != len(COMMUNE_SOURCE_KEYS) or named != set(COMMUNE_SOURCE_KEYS):
        errors.append(
            f"meta.source: {len(entries)} sources, expected the "
            f"{len(COMMUNE_SOURCE_KEYS)} of the synthesis"
        )
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            errors.append(f"meta.source[{index}]: not a source")
            continue
        name = entry.get("dataset") or entry.get("nom") or f"[{index}]"
        where = f"meta.source.{name}"
        if not entry.get("nom") or not entry.get("licence"):
            errors.append(f"{where}: nom and licence are required")
        period = entry.get("periode")
        statut = period.get("statut") if isinstance(period, Mapping) else None
        if statut not in _PERIOD_STATES:
            errors.append(f"{where}: period state {statut!r} not in {_PERIOD_STATES}")
        elif statut == "connue" and not period.get("label"):
            errors.append(f"{where}: known period without a label")
    return errors


def audit_commune_payload(
    payload: Mapping[str, Any],
    contract: Mapping[str, FieldContract] | None = None,
) -> list[str]:
    """Errors of a ``/v1/commune`` body against the contract; ``[]`` if none.

    Every numeric value (nested ones included) has a contract entry unless
    its column is declared non-semantic; no value is 0 while its source is
    out of coverage; every flat column served null has a declared state in
    ``etats`` (D-47), allowed for its field and consistent with the hosting
    of its source; every indicator absence is allowed for its field; every
    hosting state carries a coherent reason; every
    source of ``meta`` has ``nom``, ``licence`` and a period state, with a
    label when the period is known. The ORIGIN of a period is not visible in
    a response: see :func:`audit_provenance_origin`.
    """
    contract = load_registry() if contract is None else contract
    errors: list[str] = []
    for index, row in enumerate(payload.get("donnees") or []):
        errors += _audit_row(index, row, contract)
    errors += _audit_sources(payload.get("meta") or {})
    return errors


def _audit_reason(where: str, reason: Any, motif: Any) -> list[str]:
    if reason == "out_of_coverage":
        if motif not in ALLOWED_OUT_OF_COVERAGE_REASONS:
            return [f"{where}: out_of_coverage without a known reason ({motif!r})"]
        return []
    if motif is not None:
        return [f"{where}: reason {motif!r} on a state that is not out_of_coverage"]
    return []


def audit_resolve_payload(payload: Mapping[str, Any]) -> list[str]:
    """Errors of a ``/v1/resolve`` body: state/reason consistency only."""
    errors: list[str] = []
    for index, item in enumerate(payload.get("donnees") or []):
        states = item.get("dataset_states") or {}
        for name, state in states.items():
            errors += _audit_reason(
                f"donnees[{index}].dataset_states.{name}",
                state.get("absence_reason"),
                state.get("motif"),
            )
    resolution = (payload.get("meta") or {}).get("resolution")
    if isinstance(resolution, Mapping):
        errors += _audit_reason(
            "meta.resolution", resolution.get("issue"), resolution.get("motif")
        )
    return errors


def audit_provenance_origin(
    facts: ProvenanceFacts, provenance: DatasetProvenance
) -> list[str]:
    """A known period must come from a fact of the producer (PROV-03).

    Legitimate only when its label is the upstream period label, or else the
    date of the upstream update — even when that date equals the import date
    (a source published the day it was imported). A label no upstream fact
    carries was invented (e.g. derived from the import date): error.
    """
    period = provenance.periode
    if period.statut != "connue":
        return []
    # The producer's own rule (_assemble_period): a period is known only with
    # a truthy upstream label or an upstream date; its label is then the
    # upstream label when one is given (even empty), else the upstream date.
    label, updated = facts.source_period_label, facts.source_updated_at
    if not (label or updated is not None):
        origin = None
    elif label is not None:
        origin = label
    else:
        origin = updated.date().isoformat()
    if origin is not None and period.label == origin:
        return []
    return [
        f"{provenance.dataset}: known period {period.label!r} is carried by no "
        "upstream fact (source_period_label, source_updated_at)"
    ]
