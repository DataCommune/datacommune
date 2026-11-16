"""Strict qualification input only; no calculation, publication or database access.

Trust pins come from the caller's independently reviewed decision. Matching
reviewer labels and checksums inside an untrusted document are not authority.
Future consumers catch ManifestRefused to leave new blocks unqualified while
continuing to serve historical fields. JSONB hashes use canonical UTF-8 JSON.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_serializer,
    model_validator,
)

from app.commune.indicator_schemas import (
    AncrageDvfInconnu,
    HostedDepartement,
    LivraisonDvfRecue,
)
from app.dpe.schemas import DpeCalendarDate

MAX_MANIFEST_BYTES = 512 * 1024
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Label = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,120}$")]
Dataset = Literal["dvf", "dpe"]
Scope = Literal["synthetic", "preproduction", "publication"]
Status = Literal["unknown", "pending", "no_go", "qualified"]


def _instant_input(value: Any) -> Any:
    if not isinstance(value, (str, datetime)):
        raise ValueError("Explicit datetime required")  # noqa: TRY004
    return value


Instant = Annotated[AwareDatetime, BeforeValidator(_instant_input)]
INDICATORS = {
    "dvf": ("dvf.count", "dvf.median_eur_m2", "dvf.p10_p90_eur_m2", "dvf.trend_pct"),
    "dpe": (
        "dpe.document_count",
        "dpe.class_distribution",
        "dpe.fg_share",
        "dpe.mode",
        "dpe.ordinal_median",
        "dpe.construction_median",
        "dpe.restricted_count",
    ),
}


class ManifestRefused(ValueError):  # noqa: N818 — typed qualification outcome
    """Typed refusal; consumers may map it to absent new indicator blocks."""

    def __init__(self, code: str = "manifest_validation_failed") -> None:
        self.code = code
        super().__init__(code)


class IndicatorNotQualified(ManifestRefused):
    def __init__(self, state: Status) -> None:
        self.state = state
        super().__init__("indicator_not_qualified")


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)


class FrozenUnknownAnchor(AncrageDvfInconnu):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)


class FrozenReceivedAnchor(Closed):
    """Immutable internal view; the public model remains the validation authority.

    A separate representation avoids changing the shared response contract's
    mutable list type. JSON output keeps the same department array and fields.
    """

    etat: Literal["delivery_received_and_stock_bound"]
    version_qualification: Literal[
        "dvf-delivery-qualification.v2", "dvf-delivery-qualification.v3"
    ]
    lot_id: str
    periode_debut_inclus: DpeCalendarDate
    periode_fin_exclue: DpeCalendarDate
    # Received departements of the lot; their rule is the public model's.
    departements: tuple[HostedDepartement, ...]
    acquisition_complete: Literal[True]
    revisions_possibles: Literal[True]
    exhaustivite_historique: Literal["unknown"]
    generation_fournisseur: None
    date_observation: AwareDatetime
    preuve_qualification_sha256: Sha
    preuve_observation_sha256: Sha
    preuve_reconstruction_sha256: Sha
    snapshot_sha256: Sha
    contenu_projete_sha256: Sha
    cog_sha256: Sha
    methode_projection: Literal["sha256-sorted-row-digests-json.v1"]
    generation_stock: UUID | None

    @model_validator(mode="before")
    @classmethod
    def validate_public_contract(cls, value: Any) -> Any:
        return LivraisonDvfRecue.model_validate(value).model_dump()


FrozenDvfAnchor = Annotated[
    FrozenUnknownAnchor | FrozenReceivedAnchor, Field(discriminator="etat")
]


class IndicatorDecision(Closed):
    status: Status
    rule_version: Label | None
    reason_code: Label

    @model_validator(mode="after")
    def rule_required_only_when_qualified(self) -> Self:
        if (self.status == "qualified") != (self.rule_version is not None):
            raise ValueError("Qualified decisions require an explicit rule")
        return self

    def require_qualified(self, indicator: str) -> None:
        if self.status != "qualified":
            raise IndicatorNotQualified(self.status)


class Receipt(Closed):
    path: str
    sha256: Sha


class Policy(Closed):
    schema_version: Literal["commune-indicator-policy.v1"]
    dataset_name: Dataset
    policy_version: Label
    scope: Scope
    indicators: Mapping[str, IndicatorDecision]


class Review(Closed):
    schema_version: Literal["commune-indicator-review.v1"]
    dataset_name: Dataset
    source_generation: UUID
    source_snapshot_sha256: Sha
    cog_sha256: Sha
    cog_version: Label
    policy_sha256: Sha
    scope: Scope
    producer: Label
    reviewer: Label
    qualified_at: Instant
    indicators: Mapping[str, IndicatorDecision]


class QualifiedManifest(Closed):
    schema_version: Literal["commune-indicators-manifest.v1"]
    dataset_name: Dataset
    source_generation: UUID
    source_snapshot_sha256: Sha
    cog_version: Label
    cog_sha256: Sha
    source_observed_at: Instant
    scope: Scope
    producer: Label
    reviewer: Label
    qualified_at: Instant
    policy_version: Label
    policy: Receipt
    evidence: Receipt
    indicators: Mapping[str, IndicatorDecision]
    dpe_as_of: DpeCalendarDate | None
    dvf_anchor: FrozenDvfAnchor | None

    @model_validator(mode="after")
    def scope_and_chronology(self) -> Self:
        if set(self.indicators) != set(INDICATORS[self.dataset_name]):
            raise ValueError("Every dataset indicator decision must be explicit")
        if (
            self.producer == self.reviewer
            or self.qualified_at < self.source_observed_at
        ):
            raise ValueError("Independent review must follow source observation")
        if self.dataset_name == "dpe":
            if self.dpe_as_of is None or self.dvf_anchor is not None:
                raise ValueError("DPE requires its own explicit date")
            if self.dpe_as_of > self.qualified_at.date():
                raise ValueError("Review cannot predate the studied date")
        else:
            anchor = self.dvf_anchor
            if anchor is None or self.dpe_as_of is not None:
                raise ValueError("DVF requires an explicit temporal anchor")
            if isinstance(anchor, AncrageDvfInconnu):
                if any(d.status == "qualified" for d in self.indicators.values()):
                    raise ValueError("An unknown period cannot authorize dated values")
            elif (
                anchor.generation_stock != self.source_generation
                or anchor.snapshot_sha256 != self.source_snapshot_sha256
                or anchor.cog_sha256 != self.cog_sha256
                or anchor.date_observation > self.qualified_at
            ):
                raise ValueError("Received evidence must bind this source and review")
        object.__setattr__(self, "indicators", MappingProxyType(dict(self.indicators)))
        return self

    @field_serializer("indicators")
    def serialize_indicators(
        self, value: Mapping[str, IndicatorDecision]
    ) -> dict[str, Any]:
        return {
            name: decision.model_dump(mode="json") for name, decision in value.items()
        }

    def require_indicator(self, name: str) -> None:
        if name not in self.indicators:
            raise ManifestRefused("indicator_not_qualified")
        self.indicators[name].require_qualified(name)


class QualificationContext(Closed):
    """Trusted caller inputs, never inferred from a manifest or snapshot name."""

    dataset_name: Dataset
    source_generation: UUID
    source_snapshot_sha256: Sha
    cog_sha256: Sha
    cog_version: Label
    policy_version: Label
    policy_sha256: Sha
    evidence_sha256: Sha
    scope: Scope
    checked_at: Instant


class StoredManifest(Closed):
    """Future append-only row envelope; no persistence is implemented here."""

    dataset_name: Dataset
    source_generation: UUID
    manifest: dict[str, Any]
    manifest_sha256: Sha
    qualified_at: Instant


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestRefused()
        result[key] = value
    return result


def parse_document(raw: bytes, *, limit: int = MAX_MANIFEST_BYTES) -> dict[str, Any]:
    if not raw or len(raw) > limit:
        raise ManifestRefused()
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
        if not isinstance(value, dict):
            raise ValueError("Object required")  # noqa: TRY004
        # Reject NaN and infinities, including in a future unknown field.
        json.dumps(value, allow_nan=False)
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise ManifestRefused() from None


def manifest_sha256(value: dict[str, Any]) -> str:
    """Stable across whitespace/key ordering and PostgreSQL JSONB reserialization."""
    try:
        raw = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError("Manifest too large")
        return hashlib.sha256(raw).hexdigest()
    except (ValueError, TypeError, RecursionError):
        raise ManifestRefused() from None


def read_bounded(path: Path, *, root: Path, limit: int) -> bytes:
    """Read immutable bytes from a confined regular file, without following links."""
    try:
        root = root.absolute()
        path = path.absolute()
        relative = path.relative_to(root)
        for parent in (
            root,
            *[
                root.joinpath(*relative.parts[:i])
                for i in range(1, len(relative.parts) + 1)
            ],
        ):
            if parent.is_symlink() or parent.resolve() != parent:
                raise ValueError("Linked path")
        if not root.is_dir() or not path.is_file() or path.stat().st_size > limit:
            raise ValueError("Unbounded file")
        with path.open("rb") as stream:
            value = stream.read(limit + 1)
        if len(value) > limit:
            raise ValueError("Unbounded bytes")
        return value
    except (OSError, ValueError):
        raise ManifestRefused() from None


def _receipt(receipt: Receipt, root: Path) -> dict[str, Any]:
    name = receipt.path
    posix, windows = PurePosixPath(name), PureWindowsPath(name)
    if (
        not name
        or "\\" in name
        or ":" in name
        or posix.is_absolute()
        or windows.drive
        or any(p in {"..", "."} for p in name.split("/"))
    ):
        raise ManifestRefused()
    raw = read_bounded(root / name, root=root, limit=MAX_EVIDENCE_BYTES)
    if hashlib.sha256(raw).hexdigest() != receipt.sha256:
        raise ManifestRefused()
    return parse_document(raw, limit=MAX_EVIDENCE_BYTES)


def validate_manifest(
    raw: bytes,
    *,
    expected_manifest_sha256: str,
    context: QualificationContext,
    evidence_root: Path,
) -> QualifiedManifest:
    try:
        payload = parse_document(raw)
        if manifest_sha256(payload) != expected_manifest_sha256:
            raise ManifestRefused()
        manifest = QualifiedManifest.model_validate(payload)
        if (
            manifest.source_generation != context.source_generation
            or manifest.dataset_name != context.dataset_name
        ):
            raise ManifestRefused("source_generation_unqualified")
        if (
            manifest.source_snapshot_sha256 != context.source_snapshot_sha256
            or manifest.cog_sha256 != context.cog_sha256
            or manifest.cog_version != context.cog_version
            or manifest.policy_version != context.policy_version
            or manifest.policy.sha256 != context.policy_sha256
            or manifest.evidence.sha256 != context.evidence_sha256
            or manifest.scope != context.scope
            or manifest.qualified_at > context.checked_at
        ):
            raise ManifestRefused()
        policy = Policy.model_validate(_receipt(manifest.policy, evidence_root))
        review = Review.model_validate(_receipt(manifest.evidence, evidence_root))
        if (
            policy.dataset_name != manifest.dataset_name
            or policy.scope != manifest.scope
            or policy.policy_version != manifest.policy_version
            or policy.indicators != manifest.indicators
            or review.dataset_name != manifest.dataset_name
            or review.source_generation != manifest.source_generation
            or review.source_snapshot_sha256 != manifest.source_snapshot_sha256
            or review.cog_sha256 != manifest.cog_sha256
            or review.cog_version != manifest.cog_version
            or review.policy_sha256 != manifest.policy.sha256
            or review.scope != manifest.scope
            or review.producer != manifest.producer
            or review.reviewer != manifest.reviewer
            or review.qualified_at != manifest.qualified_at
            or review.indicators != manifest.indicators
        ):
            raise ManifestRefused()
        if (
            isinstance(manifest.dvf_anchor, FrozenReceivedAnchor)
            and manifest.dvf_anchor.preuve_qualification_sha256
            != manifest.evidence.sha256
        ):
            raise ManifestRefused()
        return manifest
    except ManifestRefused:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise ManifestRefused() from None


def load_qualified_manifest(
    record: StoredManifest | None, *, context: QualificationContext, evidence_root: Path
) -> QualifiedManifest:
    if record is None:
        raise ManifestRefused("source_generation_unqualified")
    try:
        raw = json.dumps(record.manifest, ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError):
        raise ManifestRefused() from None
    manifest = validate_manifest(
        raw,
        expected_manifest_sha256=record.manifest_sha256,
        context=context,
        evidence_root=evidence_root,
    )
    if (
        record.source_generation != manifest.source_generation
        or record.dataset_name != manifest.dataset_name
        or record.qualified_at != manifest.qualified_at
    ):
        raise ManifestRefused("source_generation_unqualified")
    return manifest
