"""Private raw presence and pure, pre-publication DPE lifecycle reconciliation.

The registry must be collected before transformation. Callers must exhaust the
reconciliation iterator into a private staging relation before any publication:
coverage failures can be detected after the final incoming row. SQL payloads do
not replace raw JSON evidence, and no historic document fills a new payload.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import tempfile
from collections.abc import Collection, Iterable, Iterator, Mapping
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.etl_dpe.sources import DPE_SOURCES, DpeImportManifest, expected_pairs
from app.scope.model import DEPARTEMENTS_HEBERGEABLES

LIFECYCLE_COLUMNS = frozenset(
    {
        "source_observations",
        "source_state",
        "source_snapshot_at",
        "is_tombstone",
        "lifecycle_facts",
        "revision_evidence",
    }
)
TOMBSTONE_KEEP = frozenset(
    {
        "id",
        "numero_dpe",
        "identifiant_ban",
        "code_departement",
        "code_insee",
        "numero_dpe_remplace",
    }
)
OBSERVATION_KEYS = frozenset(
    {
        "source_slug",
        "department",
        "source_version",
        "observed_at",
        "last_seen_at",
        "state",
        "import_id",
    }
)
DATE_FIELDS = (
    ("date_etablissement_dpe", "invalid_establishment_date"),
    ("date_fin_validite_dpe", "invalid_end_date"),
)
UTC_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)\Z"
)
VERSION_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and UTC_PATTERN.fullmatch(value):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Invalid DPE provenance timestamp") from None
    else:
        raise ValueError("Invalid DPE provenance timestamp")
    if parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("Invalid DPE provenance timestamp")
    return parsed


def _utc(value: Any) -> str:
    return _timestamp(value).isoformat().replace("+00:00", "Z")


def _number(value: int | Decimal) -> str:
    """Canonical decimal equality without binary floats or context rounding."""
    decimal = Decimal(value)
    if not decimal.is_finite():
        raise ValueError("Invalid DPE numeric payload")
    if not decimal:
        return "0"
    sign, digits, exponent = decimal.as_tuple()
    digits = list(digits)
    while digits[-1] == 0:
        digits.pop()
        exponent += 1
    return f"{sign}:{''.join(map(str, digits))}:{exponent}"


def _typed(value: Any) -> Any:
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, (int, Decimal)):
        return ["number", _number(value)]
    if isinstance(value, float) and math.isfinite(value):
        return ["float", value.hex()]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, (date, datetime)):
        return [type(value).__name__, value.isoformat()]
    if isinstance(value, (bytes, memoryview)):
        return ["bytes", bytes(value).hex()]
    if isinstance(value, Mapping):
        return ["object", [[key, _typed(item)] for key, item in sorted(value.items())]]
    if isinstance(value, (list, tuple)):
        return ["array", [_typed(item) for item in value]]
    raise ValueError("Invalid DPE documentary payload type")


def _canonical(value: Any) -> bytes:
    return json.dumps(_typed(value), ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _manifest_key(manifest: DpeImportManifest) -> bytes:
    return hashlib.sha256(_canonical(asdict(manifest))).digest()


def _date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if type(value) is date:
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Invalid DPE documentary date")
    return date.fromisoformat(value)


def _date_issues(row: Mapping[str, Any]) -> list[str]:
    issues = []
    for field, issue in DATE_FIELDS:
        try:
            _date(row.get(field))
        except (ValueError, TypeError):
            issues.append(issue)
    return issues


def _unverified() -> dict[str, Any]:
    return dict(
        status="unverified",
        kind=None,
        revision_date=None,
        verified_at=None,
        evidence_sha256=None,
        official_source=None,
    )


class DpePresenceRegistry:
    """Disk-backed exact payload comparison and indexed presence, never public.

    The pairs of a document are a bit set over the generation's own pairs,
    stored as hexadecimal text: a national generation has 202 pairs, beyond
    SQLite's 64-bit integers.
    """

    def __init__(self, manifest: DpeImportManifest, directory: Path | None = None):
        self._temporary = tempfile.TemporaryDirectory(
            prefix="dpe-presence-", dir=directory
        )
        self._connection = sqlite3.connect(
            Path(self._temporary.name) / "presence.sqlite"
        )
        self.manifest_key = _manifest_key(manifest)
        self.pairs = tuple(sorted(manifest.pairs))
        self._bits = {pair: 1 << index for index, pair in enumerate(self.pairs)}
        self._complete = False
        # Column names of previous rows, once per distinct set (a cursor: one).
        self._key_sets: dict[tuple[str, ...], int] = {}
        self._keys: list[tuple[str, ...]] = []
        self._connection.execute(
            "CREATE TABLE presence (number TEXT PRIMARY KEY, canonical BLOB NOT NULL, "
            "pairs TEXT NOT NULL, dates TEXT NOT NULL, transformed BLOB)"
        )
        # Previous rows of one reconciliation, read back by number (plan 04.1-11).
        self._connection.execute(
            "CREATE TABLE previous (number TEXT PRIMARY KEY, payload BLOB NOT NULL)"
        )

    def __enter__(self) -> DpePresenceRegistry:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()
        self._temporary.cleanup()

    def __contains__(self, number: str) -> bool:
        return (
            self._connection.execute(
                "SELECT 1 FROM presence WHERE number = ?", (number,)
            ).fetchone()
            is not None
        )

    def add(self, row: Mapping[str, Any], pair: tuple[str, str]) -> None:
        selected = {field: row.get(field) for field in DPE_SOURCES[pair[0]].columns}
        canonical = _canonical(selected)
        number = row["numero_dpe"]
        old = self._connection.execute(
            "SELECT canonical, pairs FROM presence WHERE number = ?", (number,)
        ).fetchone()
        bit = self._bits[pair]
        if old is not None:
            if old[0] != canonical:
                raise ValueError("DPE raw documentary collision")
            self._connection.execute(
                "UPDATE presence SET pairs = ? WHERE number = ?",
                (format(int(old[1], 16) | bit, "x"), number),
            )
        else:
            self._connection.execute(
                "INSERT INTO presence VALUES (?, ?, ?, ?, NULL)",
                (number, canonical, format(bit, "x"), json.dumps(_date_issues(row))),
            )

    def observation(self, number: str) -> tuple[list[tuple[str, str]], list[str]]:
        row = self._connection.execute(
            "SELECT pairs, dates FROM presence WHERE number = ?", (number,)
        ).fetchone()
        if row is None:
            raise ValueError("DPE transformed row absent from raw registry")
        mask = int(row[0], 16)
        present = [pair for pair in self.pairs if mask & self._bits[pair]]
        return present, json.loads(row[1])

    def transformed(self, row: Mapping[str, Any]) -> bool:
        """Record output equality too; return False for an already emitted copy."""
        number = row["numero_dpe"]
        selected = {
            key: value
            for key, value in row.items()
            if key not in LIFECYCLE_COLUMNS | {"id", "regime_dpe"}
        }
        canonical = _canonical(selected)
        old = self._connection.execute(
            "SELECT transformed FROM presence WHERE number = ?", (number,)
        ).fetchone()
        if old is None:
            raise ValueError("DPE transformed row absent from raw registry")
        if old[0] is not None:
            if old[0] != canonical:
                raise ValueError("DPE transformed documentary collision")
            return False
        self._connection.execute(
            "UPDATE presence SET transformed = ? WHERE number = ?", (canonical, number)
        )
        return True

    def untransformed(self) -> Iterator[str]:
        for row in self._connection.execute(
            "SELECT number FROM presence WHERE transformed IS NULL"
        ):
            yield row[0]

    def clear_previous(self) -> None:
        self._connection.execute("DELETE FROM previous")
        self._key_sets.clear()
        self._keys.clear()

    def add_previous(self, number: str, row: Mapping[str, Any]) -> None:
        """Keep what a reconciled row reads of *row* (``PREVIOUS_READ``).

        Column names are kept once per distinct set, values of the read
        columns only: every other column of a retired row is null.
        """
        keys = tuple(row)
        if keys not in self._key_sets:
            self._key_sets[keys] = len(self._keys)
            self._keys.append(keys)
        read = [[key, _pack(row[key])] for key in keys if key in PREVIOUS_READ]
        payload = json.dumps(
            [self._key_sets[keys], read], ensure_ascii=False, separators=(",", ":")
        )
        self._connection.execute(
            "INSERT INTO previous VALUES (?, ?)", (number, payload.encode("utf-8"))
        )

    def _previous_row(self, payload: bytes) -> dict[str, Any]:
        key_set, read = json.loads(payload)
        row = dict.fromkeys(self._keys[key_set])
        row.update((key, _unpacked(value)) for key, value in read)
        return row

    def previous(self, number: str) -> dict[str, Any] | None:
        found = self._connection.execute(
            "SELECT payload FROM previous WHERE number = ?", (number,)
        ).fetchone()
        return None if found is None else self._previous_row(found[0])

    def has_previous(self, number: str) -> bool:
        return (
            self._connection.execute(
                "SELECT 1 FROM previous WHERE number = ?", (number,)
            ).fetchone()
            is not None
        )

    def previous_absent(self) -> Iterator[dict[str, Any]]:
        """Previous rows without raw presence, in their reading order."""
        for (payload,) in self._connection.execute(
            "SELECT payload FROM previous AS old WHERE NOT EXISTS "
            "(SELECT 1 FROM presence WHERE presence.number = old.number) "
            "ORDER BY old.rowid"
        ):
            yield self._previous_row(payload)


def collect_presence(
    manifest: DpeImportManifest, *, directory: Path | None = None
) -> DpePresenceRegistry:
    """Validate the complete manifest, then read its typed, checked JSON pages."""
    manifest.validate()
    _manifest_entries(manifest)
    registry = DpePresenceRegistry(manifest, directory)
    try:
        for entry in manifest.files:
            for row in entry.source_rows():
                registry.add(row, entry.pair)
        registry._connection.commit()
        registry._complete = True
    except BaseException:
        registry.close()
        raise
    return registry


def _manifest_entries(manifest: DpeImportManifest) -> dict:
    """Pairs of the generation, read from its manifest: its complete scope."""
    entries = {entry.pair: entry for entry in manifest.files}
    if (
        not entries
        or len(entries) != len(manifest.files)
        or set(entries) != expected_pairs(manifest.departements)
    ):
        raise ValueError(
            "DPE manifest requires every source on every departement, once"
        )
    for entry in entries.values():
        if not VERSION_PATTERN.fullmatch(entry.version_start):
            raise ValueError("Invalid DPE provenance version")
        _timestamp(entry.observed_at)
    return entries


# Pairs a served lifecycle may name: app/dpe/inventory_service.py calls
# _previous_observations without a generation. Every hostable departement
# since plan 04.1-08: the served ``source_scope`` names the departement of the
# founding observation (app.dpe.lifecycle.source_scope).
SERVED_LIFECYCLE_PAIRS = expected_pairs(DEPARTEMENTS_HEBERGEABLES)


def _previous_observations(
    row: Mapping[str, Any], scope: Collection = SERVED_LIFECYCLE_PAIRS
) -> list[dict]:
    """Previous provenance, each pair within *scope* (a generation's pairs)."""
    observations = row.get("source_observations", [])
    if not isinstance(observations, list) or len(observations) > len(scope):
        raise ValueError("Invalid DPE previous provenance")
    pairs = []
    for observation in observations:
        if not isinstance(observation, dict) or set(observation) != OBSERVATION_KEYS:
            raise ValueError("Invalid DPE previous provenance")
        pair = observation["source_slug"], observation["department"]
        if pair not in scope or pair in pairs:
            raise ValueError("Invalid DPE previous provenance")
        if (
            not isinstance(observation["source_version"], str)
            or not VERSION_PATTERN.fullmatch(observation["source_version"])
            or not isinstance(observation["import_id"], str)
            or not observation["import_id"]
            or observation["state"]
            not in {"present", "absent_from_complete_scope", "unknown"}
        ):
            raise ValueError("Invalid DPE previous provenance")
        if _timestamp(observation["last_seen_at"]) > _timestamp(
            observation["observed_at"]
        ):
            raise ValueError("Invalid DPE previous provenance")
        pairs.append(pair)
    if pairs != sorted(pairs):
        raise ValueError("Invalid DPE previous provenance")
    return observations


def _observations(
    previous: Mapping[str, Any], present: list, entries: dict
) -> list[dict]:
    historic = {
        (obs["source_slug"], obs["department"]): obs
        for obs in _previous_observations(previous, entries)
    }
    result = []
    for pair in sorted(set(historic) | set(present)):
        entry = entries[pair]
        now = _utc(entry.observed_at)
        old = historic.get(pair)
        if old and _timestamp(old["observed_at"]) > _timestamp(now):
            raise ValueError("DPE snapshot predates previous observation")
        seen = pair in present
        result.append(
            dict(
                source_slug=pair[0],
                department=pair[1],
                source_version=entry.version_start,
                observed_at=now,
                last_seen_at=now if seen else _utc(old["last_seen_at"]),
                state="present" if seen else "absent_from_complete_scope",
                import_id=entry.import_id,
            )
        )
    return result


def _regime(observations: list[dict], legacy: Any) -> str | None:
    regimes = {DPE_SOURCES[obs["source_slug"]].regime for obs in observations}
    if not observations:
        return legacy if legacy in ("existant", "neuf") else None
    return next(iter(regimes)) if len(regimes) == 1 else None


def _result(
    row: Mapping[str, Any],
    previous: Mapping[str, Any],
    present: list,
    issues: list,
    entries: dict,
    snapshot: str,
) -> dict[str, Any]:
    retired = not present
    result = (
        {key: value if key in TOMBSTONE_KEEP else None for key, value in row.items()}
        if retired
        else dict(row)
    )
    observations = _observations(previous, present, entries)
    result.update(
        source_observations=observations,
        source_state="absent_from_complete_scope" if retired else "present",
        source_snapshot_at=snapshot,
        is_tombstone=retired,
        lifecycle_facts=dict(
            replacement_state="not_checked",
            graph_issues=[],
            date_issues=[] if retired else issues,
        ),
        revision_evidence=_unverified(),
        regime_dpe=_regime(observations, row.get("regime_dpe")),
    )
    if not retired:
        for field, issue in DATE_FIELDS:
            result[field] = None if issue in issues else _date(row.get(field))
    return result


# What a reconciled row reads of its previous row: the TOMBSTONE_KEEP values of
# a retired one, the legacy regime and the previous provenance. Every other
# column is kept by name only (a retired row nulls it): no address or other
# documentary payload reaches the private disk registry.
PREVIOUS_READ = TOMBSTONE_KEEP | {"regime_dpe", "source_observations"}


def _pack(value: Any) -> Any:
    """Lossless JSON form: types and key order survive, unlike ``_typed``."""
    if value is None or isinstance(value, (bool, str)) or type(value) is int:
        return value
    if isinstance(value, float):
        return {"float": value.hex()}
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, datetime):
        return {"datetime": value.isoformat()}
    if isinstance(value, date):
        return {"date": value.isoformat()}
    if isinstance(value, list):
        return [_pack(item) for item in value]
    if isinstance(value, tuple):
        return {"tuple": [_pack(item) for item in value]}
    if isinstance(value, Mapping) and all(isinstance(key, str) for key in value):
        return {"object": [[key, _pack(item)] for key, item in value.items()]}
    raise ValueError("Invalid DPE previous payload type")


def _unpacked(value: Any) -> Any:
    if isinstance(value, list):
        return [_unpacked(item) for item in value]
    if not isinstance(value, dict):
        return value
    ((tag, item),) = value.items()
    if tag == "object":
        return {key: _unpacked(inner) for key, inner in item}
    if tag == "tuple":
        return tuple(_unpacked(inner) for inner in item)
    if tag == "float":
        return float.fromhex(item)
    if tag == "decimal":
        return Decimal(item)
    if tag == "datetime":
        return datetime.fromisoformat(item)
    return date.fromisoformat(item)


def _store_previous(
    previous: Iterable[Mapping[str, Any]],
    snapshot: str,
    entries: dict,
    registry: DpePresenceRegistry,
) -> None:
    """Check previous rows, then keep them in the registry, read by number.

    Each must lie in the generation's departements: a previous row outside
    them could not be judged absent from a complete scope, and the import is
    refused rather than retiring it (manifest scope). Memory holds one row at
    a time, whatever the served stock (plan 04.1-11).
    """
    departements = {department for _slug, department in entries}
    registry.clear_previous()
    for row in previous:
        number = row.get("numero_dpe")
        if (
            not isinstance(number, str)
            or not number.strip()
            or registry.has_previous(number)
        ):
            raise ValueError("Invalid or duplicate DPE previous identity")
        if row.get("code_departement") not in departements:
            raise ValueError("DPE previous row outside complete scope")
        _previous_observations(row, entries)
        if row.get("source_snapshot_at") is not None and _timestamp(
            row["source_snapshot_at"]
        ) > _timestamp(snapshot):
            raise ValueError("DPE snapshot predates previous observation")
        registry.add_previous(number, row)


def reconcile_observations(  # noqa: C901
    previous: Iterable[Mapping[str, Any]],
    incoming: Iterable[Mapping[str, Any]],
    manifest: DpeImportManifest,
    *,
    registry: DpePresenceRegistry | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield one current row per number; require full exhaustion before swap.

    Incoming rows use SQL column names and decoded JSON columns. Build a registry
    before transforming: every raw document must be transformed, including one
    without source geometry, which is kept with a null position (DATA-02).
    An omitted registry is rebuilt from the manifest, never from incoming rows.
    Previous rows are kept in the registry's private file, read back by number:
    memory does not grow with the served stock (plan 04.1-11).
    """
    if registry is None:
        with collect_presence(manifest) as collected:
            yield from reconcile_observations(
                previous, incoming, manifest, registry=collected
            )
        return
    if registry.manifest_key != _manifest_key(manifest):
        raise ValueError("DPE presence registry generation mismatch")
    if not registry._complete:
        raise ValueError("DPE presence registry is incomplete")
    entries = _manifest_entries(manifest)
    snapshot = _utc(max(_timestamp(entry.observed_at) for entry in entries.values()))
    _store_previous(previous, snapshot, entries, registry)
    registry._connection.execute("UPDATE presence SET transformed = NULL")
    for row in incoming:
        number = row.get("numero_dpe")
        if (
            not isinstance(number, str)
            or not number.strip()
            or number != number.strip()
        ):
            raise ValueError("Invalid DPE transformed identity")
        present, issues = registry.observation(number)
        if row.get("code_departement") not in {pair[1] for pair in present}:
            raise ValueError("DPE transformed row outside raw scope")
        if registry.transformed(row):
            yield _result(
                row,
                registry.previous(number) or {},
                present,
                issues,
                entries,
                snapshot,
            )
    for number in registry.untransformed():
        if registry.has_previous(number):
            raise ValueError("Historical DPE present but not transformed")
        raise ValueError("DPE transformation coverage is incomplete")
    for row in registry.previous_absent():
        yield _result(row, row, [], [], entries, snapshot)
