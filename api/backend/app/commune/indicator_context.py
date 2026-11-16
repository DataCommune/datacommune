"""Owner-bound context only: no rule execution, results, DDL or publication.

DATA-DECISION authorizes laboratory arithmetic, not executable publication rule
labels. LoadedDecision is evidence to inspect, NEVER permission to run its rule.
The caller supplies independent pins; none are learned from registry JSON.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import tuple_row

from app.commune.indicator_manifest import (
    Closed,
    Dataset,
    Instant,
    Label,
    ManifestRefused,
    QualificationContext,
    Scope,
    Sha,
    manifest_sha256,
    read_bounded,
)
from app.commune.indicator_schemas import (
    HOSTED_DEPARTEMENTS,
    commune_departement,
    hosted_commune,
)
from app.commune.indicator_storage import (
    LoadedDecision,
    PublishedSource,
    _maintenance_required,
    load_current_manifest,
    read_published_source,
)
from app.etl.publication import ObservedCandidate, PublicationWork

MAX_COG_BYTES = 4 * 1024 * 1024
# Above the ~35 000 communes of the national COG, with margin.
MAX_COG_COMMUNES = 40000
# This is a dated restriction, not a filter on the reference or source rows.
# DATA-DECISION amendment: no new DVF attribution until full territorial review.
DVF_TERRITORIAL_UNQUALIFIED = frozenset({"85084", "85165", "85212"})
_DATASETS: tuple[Dataset, ...] = ("dpe", "dvf")
_KNOWN_COG = "insee-cog-2026-01-01-identities"


class CogInput(Closed):
    path: Path
    root: Path
    sha256: Sha
    version: Label


class IndicatorTrustInputs(Closed):
    cog: CogInput
    evidence_root: Path
    scope: Scope
    checked_at: Instant
    dpe: QualificationContext | None = None
    dvf: QualificationContext | None = None


@dataclass(frozen=True)
class DatasetContext:
    dataset_name: Dataset
    source: PublishedSource | ObservedCandidate | None
    decision: LoadedDecision | None
    refusal: str | None
    # A decision selected by id may subsequently be revoked within this txn.
    registry_identity: tuple[int, str] | None = field(repr=False)
    # Object-integrity snapshot, not the registry hash or an external trust pin.
    validated_decision_sha256: str | None = field(default=None, repr=False)


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reference(inputs: IndicatorTrustInputs) -> tuple[str, ...]:
    cog = inputs.cog
    if cog.version != _KNOWN_COG:
        raise ManifestRefused("unsupported_cog_version")
    raw = read_bounded(cog.path, root=cog.root, limit=MAX_COG_BYTES)
    if hashlib.sha256(raw).hexdigest() != cog.sha256:
        raise ManifestRefused("cog_hash_mismatch")
    try:
        rows = json.loads(raw, object_pairs_hook=_object)
        if not isinstance(rows, list) or not rows or len(rows) > MAX_COG_COMMUNES:
            raise ValueError("Invalid reference size")
        codes = []
        for row in rows:
            # Each identity is a hosted commune filed under its own departement
            # (same rule as departement_from_insee); 75056 under 44 is refused.
            if (
                not isinstance(row, dict)
                or set(row) != {"commune", "department"}
                or not isinstance(row["department"], str)
                or not hosted_commune(row["commune"], HOSTED_DEPARTEMENTS)
                or commune_departement(row["commune"]) != row["department"]
            ):
                raise ValueError("Invalid reference identity")
            codes.append(row["commune"])
        if len(set(codes)) != len(codes):
            raise ValueError("Duplicate reference identity")
        # No expected cardinality is coded here: the communes per departement
        # are those of the producer reference itself. Its bytes are the
        # measured geo_communes extract of these departements (sha256 pinned
        # by the caller and bound by the reviewed decision and the received
        # DVF lot), never a list completed or trimmed by this code.
        return tuple(sorted(codes))
    except (ValueError, TypeError, RecursionError):
        raise ManifestRefused("invalid_cog_reference") from None


def read_qualified_communes(inputs: IndicatorTrustInputs) -> tuple[str, ...]:
    """Validate pinned COG identities without database access."""
    return _reference(inputs)


def _owner(conn: psycopg.Connection) -> tuple[Any, ...]:
    if (
        conn.closed
        or conn.autocommit
        or conn.info.transaction_status != TransactionStatus.INTRANS
    ):
        raise ManifestRefused("owner_transaction_required")
    _maintenance_required(conn)
    with conn.cursor(row_factory=tuple_row) as cursor:
        # Allocate an actual transaction identity, not merely an INTRANS flag.
        # Session locks may survive commit; a context must not survive with them.
        row = cursor.execute(
            "SELECT pid,backend_start,datid,pg_current_xact_id()::text,"
            "current_setting('transaction_isolation') "
            "FROM pg_stat_activity WHERE pid=pg_backend_pid()"
        ).fetchone()
    if row is None:
        raise ManifestRefused("owner_transaction_required")
    # A repeatable snapshot may predate acquisition of the session lock.
    # READ COMMITTED obtains current statements while the held lock excludes
    # cooperating publication/qualification writers for the entire build.
    if row[-1] != "read committed":
        raise ManifestRefused("current_snapshot_required")
    return row


def assert_indicator_owner(conn: psycopg.Connection) -> None:
    """Require the existing maintenance transaction without reading sources."""
    _owner(conn)


def _candidate(work: PublicationWork, conn: psycopg.Connection) -> ObservedCandidate:
    if not isinstance(work, PublicationWork):
        raise ManifestRefused("invalid_publication_work")
    try:
        return work.assert_current(conn)
    except RuntimeError:
        raise ManifestRefused("invalid_publication_work") from None


def _published(conn: psycopg.Connection, dataset: Dataset) -> PublishedSource | None:
    try:
        return read_published_source(conn, dataset)
    except ManifestRefused as exc:
        # Legacy means no identifiable generation. Pending/failed/OID errors
        # remain technical exceptions; they cannot be masked as honest absence.
        if exc.code == "source_generation_unqualified":
            return None
        raise


def _latest_registry(
    conn: psycopg.Connection, source: PublishedSource
) -> tuple[int, str] | None:
    with conn.cursor(row_factory=tuple_row) as cursor:
        return cursor.execute(
            "SELECT id,manifest_sha256 FROM public.commune_indicator_manifests "
            "WHERE dataset_name=%s AND source_generation=%s ORDER BY id DESC LIMIT 1",
            (source.dataset_name, source.generation),
        ).fetchone()


@dataclass(frozen=True)
class IndicatorBuildContext:
    communes: tuple[str, ...]
    cog_sha256: str
    cog_version: str
    datasets: tuple[DatasetContext, ...]
    checked_at: datetime
    scope: Scope
    _connection: psycopg.Connection = field(repr=False, compare=False)
    _owner_identity: tuple[Any, ...] = field(repr=False)
    _work: PublicationWork | None = field(repr=False, compare=False)
    _observed: ObservedCandidate | None = field(repr=False)

    def assert_current(self, conn: psycopg.Connection) -> None:
        if conn is not self._connection or _owner(conn) != self._owner_identity:
            raise ManifestRefused("owner_transaction_changed")
        if self._work is not None and _candidate(self._work, conn) != self._observed:
            raise ManifestRefused("publication_candidate_changed")
        for dataset in self.datasets:
            if isinstance(dataset.source, ObservedCandidate):
                continue
            if _published(conn, dataset.dataset_name) != dataset.source:
                raise ManifestRefused("source_identity_changed")
            if (
                dataset.source is not None
                and _latest_registry(conn, dataset.source) != dataset.registry_identity
            ):
                raise ManifestRefused("registry_decision_changed")

    def loaded_decision(
        self, conn: psycopg.Connection, dataset: Dataset, commune: str
    ) -> LoadedDecision:
        """Inspect a loaded decision, never authorize or execute its rule_version."""
        self.assert_current(conn)
        if commune not in self.communes:
            raise ManifestRefused("outside_qualified_reference")
        if dataset == "dvf" and commune in DVF_TERRITORIAL_UNQUALIFIED:
            raise ManifestRefused("territorial_attribution_unqualified")
        for entry in self.datasets:
            if entry.dataset_name == dataset:
                if entry.decision is None:
                    raise ManifestRefused(
                        entry.refusal or "source_generation_unqualified"
                    )
                return entry.decision
        raise ManifestRefused("unsupported_dataset")


def build_indicator_context(
    conn: psycopg.Connection,
    *,
    trust: IndicatorTrustInputs,
    publication_work: PublicationWork | None = None,
) -> IndicatorBuildContext:
    """Read under the caller's existing exclusive lock/transaction, with no writes.

    No real publication rule labels are currently adopted. The builder carries
    reviewed decisions and precise refusals only; a future projector must use
    a separately reviewed closed dispatcher, not treat non-None as executable.
    """
    # Revalidate model_copy/model_construct inputs at this public Python boundary.
    try:
        trust = IndicatorTrustInputs.model_validate(trust.model_dump(mode="json"))
    except (ValueError, TypeError, AttributeError):
        raise ManifestRefused("invalid_trust_inputs") from None
    identity = _owner(conn)
    observed = (
        _candidate(publication_work, conn) if publication_work is not None else None
    )
    communes = _reference(trust)
    datasets = []
    for dataset in _DATASETS:
        if observed is not None and observed.dataset_name == dataset:
            # The first candidate integration deliberately cannot load G1 or
            # qualify G2 before its source publication has committed.
            datasets.append(
                DatasetContext(
                    dataset, observed, None, "source_generation_unqualified", None
                )
            )
            continue
        source = _published(conn, dataset)
        registry_identity = (
            _latest_registry(conn, source) if source is not None else None
        )
        decision = None
        refusal: str | None = "source_generation_unqualified"
        pins = getattr(trust, dataset)
        if source is not None and pins is not None:
            if (
                pins.dataset_name != dataset
                or pins.source_generation != source.generation
                or pins.cog_version != trust.cog.version
                or pins.cog_sha256 != trust.cog.sha256
                or pins.scope != trust.scope
                or pins.checked_at > trust.checked_at
            ):
                refusal = "qualification_context_mismatch"
            else:
                try:
                    decision = load_current_manifest(
                        conn, context=pins, evidence_root=trust.evidence_root
                    )
                    refusal = None
                except ManifestRefused as exc:
                    if exc.code in {
                        "source_publication_not_ready",
                        "caller_transaction_required",
                    }:
                        raise
                    refusal = exc.code
        datasets.append(
            DatasetContext(
                dataset,
                source,
                decision,
                refusal,
                registry_identity,
                manifest_sha256(decision.manifest.model_dump(mode="json"))
                if decision is not None
                else None,
            )
        )
    result = IndicatorBuildContext(
        communes,
        trust.cog.sha256,
        trust.cog.version,
        tuple(datasets),
        trust.checked_at,
        trust.scope,
        conn,
        identity,
        publication_work,
        observed,
    )
    result.assert_current(conn)
    return result
