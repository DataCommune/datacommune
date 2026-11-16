"""Synthetic-only result cache under an existing owner transaction.

Hashes detect corruption, not authenticity. This module is not an HTTP reader,
publication permission, or a defense against malicious code owning the process.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

import psycopg
from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import Jsonb
from pydantic import Field, model_validator

from app.commune import indicator_projector as projector
from app.commune.indicator_context import (
    DVF_TERRITORIAL_UNQUALIFIED,
    DatasetContext,
    IndicatorBuildContext,
)
from app.commune.indicator_manifest import (
    Closed,
    Dataset,
    FrozenReceivedAnchor,
    Instant,
    ManifestRefused,
    Sha,
)
from app.commune.indicator_projector import ProjectionRequest, project_indicator_blocks
from app.commune.indicator_schemas import HostedCommuneCode
from app.commune.indicator_storage import PublishedSource
from app.etl.publication import ObservedCandidate

FORMAT: Literal["commune-indicator-results.v1"] = "commune-indicator-results.v1"
# One cell per dataset and commune of the qualified COG reference (920 for the
# 44/85 build). Byte bounds: ample room above the measured synthetic payloads;
# the whole build is read and validated in one statement, so a national build
# needs its own measured bound (4.2), not a silent truncation.
MAX_HEADER_BYTES = 512 * 1024
MAX_CELL_BYTES = 64 * 1024
MAX_BUILD_BYTES = 16 * 1024 * 1024
Count = Annotated[int, Field(strict=True, ge=0)]
Identifier = Annotated[int, Field(strict=True, gt=0)]


class ResultStoreRefused(ManifestRefused):
    """Technical missing/stale/integrity outcome; never a public absence reason."""


class _SourceFacts(Closed):
    state: Literal["published", "candidate_pending"]
    publication_id: Identifier
    generation: UUID
    relation_oids: tuple[tuple[str, Identifier], ...]
    recorded_row_count: Count
    candidate_table: str | None


class _DatasetFacts(Closed):
    dataset: Dataset
    source: _SourceFacts | None
    registry_identity: tuple[Identifier, Sha] | None
    loaded_identity: tuple[Identifier, Sha] | None
    validated_decision_sha256: Sha | None
    source_snapshot_sha256: Sha | None
    refusal: str | None


class _Header(Closed):
    format_version: Literal["commune-indicator-results.v1"]
    scope: Literal["synthetic", "preproduction"]
    cog_sha256: Sha
    cog_version: str
    checked_at: Instant
    calculated_at: Instant
    commune_count: Identifier
    cell_count: Identifier
    codes_sha256: Sha
    observed_context: tuple[_DatasetFacts, ...]

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (
            self.calculated_at < self.checked_at
            or self.cell_count != 2 * self.commune_count
            or len(self.observed_context) != 2
            or {x.dataset for x in self.observed_context} != {"dvf", "dpe"}
        ):
            raise ValueError("Invalid result header")
        return self


class _Cell(Closed):
    dataset_name: Dataset
    code_insee: HostedCommuneCode
    public_payload: dict[str, Any]
    private_binding: dict[str, Any]


@dataclass(frozen=True)
class ResultBuildReceipt:
    build_id: int
    build_sha256: str
    inserted: bool
    commune_count: int
    cell_count: int


@dataclass(frozen=True, repr=False)
class PublicResultBuild:
    # These immutable bytes contain PUBLIC fields only. No header/bindings leave.
    _public_json: bytes

    def public_dump(self) -> list[dict[str, Any]]:
        return json.loads(self._public_json)


def _default(value: Any) -> str:
    if isinstance(value, (datetime, UUID)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    raise ValueError("Unsupported canonical value")


def _canonical(value: Any, limit: int = MAX_BUILD_BYTES) -> bytes:
    try:
        raw = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=_default,
        ).encode("utf-8")
        if len(raw) > limit:
            raise ValueError("Oversized result")
        return raw
    except (ValueError, TypeError, RecursionError):
        raise ResultStoreRefused("result_build_integrity") from None


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _guard(conn: psycopg.Connection, context: IndicatorBuildContext) -> None:
    if not isinstance(context, IndicatorBuildContext) or context.scope not in {
        "synthetic",
        "preproduction",
    }:
        raise ResultStoreRefused("result_build_stale")
    context.assert_current(conn)
    if (
        not context.communes
        or len(set(context.communes)) != len(context.communes)
        or tuple(sorted(context.communes)) != context.communes
    ):
        raise ResultStoreRefused("result_build_integrity")


def _facts(context: IndicatorBuildContext) -> tuple[_DatasetFacts, ...]:
    entries = {d.dataset_name: d for d in context.datasets}
    if len(context.datasets) != 2 or set(entries) != {"dvf", "dpe"}:
        raise ResultStoreRefused("result_build_integrity")
    output = []
    for dataset in ("dvf", "dpe"):
        entry = entries[dataset]
        decision = projector._decision(
            entry, context, ProjectionRequest(context.checked_at, context.scope)
        )
        source = entry.source
        observed = None
        if source is not None:
            if not isinstance(source, (PublishedSource, ObservedCandidate)):
                raise ResultStoreRefused("result_build_integrity")
            pending = isinstance(source, ObservedCandidate)
            observed = _SourceFacts(
                state="candidate_pending" if pending else "published",
                publication_id=source.publication_id,
                generation=source.generation,
                relation_oids=source.relation_oids,
                recorded_row_count=source.row_count
                if isinstance(source, ObservedCandidate)
                else source.recorded_row_count,
                candidate_table=source.table_name
                if isinstance(source, ObservedCandidate)
                else None,
            )
        loaded = entry.decision
        output.append(
            _DatasetFacts(
                dataset=dataset,
                source=observed,
                registry_identity=entry.registry_identity,
                loaded_identity=(loaded.registry_id, loaded.manifest_sha256)
                if loaded
                else None,
                validated_decision_sha256=entry.validated_decision_sha256,
                source_snapshot_sha256=decision.source_snapshot_sha256
                if decision
                else None,
                refusal=entry.refusal,
            )
        )
    return tuple(output)


def _header(context: IndicatorBuildContext, request: ProjectionRequest) -> _Header:
    return _Header(
        format_version=FORMAT,
        scope=context.scope,
        cog_sha256=context.cog_sha256,
        cog_version=context.cog_version,
        checked_at=context.checked_at,
        calculated_at=request.calculated_at,
        commune_count=len(context.communes),
        cell_count=2 * len(context.communes),
        codes_sha256=_digest(context.communes),
        observed_context=_facts(context),
    )


def _versions(entry: DatasetContext, code: str) -> tuple[tuple[str, str], ...]:
    manifest = entry.decision.manifest if entry.decision else None
    if manifest is None:
        return ()
    if entry.dataset_name == "dpe":
        return (
            tuple((c, projector.DPE_PROFILE) for c in projector.DPE_PUBLIC)
            if all(
                projector._supported(manifest, c, projector.DPE_PROFILE)
                for c in projector.DPE_PUBLIC
            )
            else ()
        )
    if (
        code in DVF_TERRITORIAL_UNQUALIFIED
        or not isinstance(manifest.dvf_anchor, FrozenReceivedAnchor)
        or not projector._supported(manifest, "dvf.count", projector.DVF_PROFILE)
    ):
        return ()
    median_supported = projector._supported(
        manifest, "dvf.median_eur_m2", projector.DVF_PROFILE
    )
    return (
        (("dvf.count", projector.DVF_PROFILE),)
        + ((("dvf.median_eur_m2", projector.DVF_PROFILE),) if median_supported else ())
        + (
            (("dvf.trend_pct", projector.DVF_TREND_PROFILE),)
            if median_supported
            and projector._supported(
                manifest, "dvf.trend_pct", projector.DVF_TREND_PROFILE
            )
            else ()
        )
    )


def _binding(
    entry: DatasetContext, code: str, context: IndicatorBuildContext, header: _Header
) -> dict[str, Any]:
    expected = projector._binding(
        entry,
        context,
        ProjectionRequest(header.calculated_at, header.scope),
        _versions(entry, code),
    )
    # The reader's fresh checked_at is not the historical build observation.
    return json.loads(
        _canonical(asdict(replace(expected, checked_at=header.checked_at)))
    )


def _validate_cells(  # noqa: C901
    cells: list[_Cell], header: _Header, context: IndicatorBuildContext
) -> list[dict[str, Any]]:
    expected = {
        (dataset, code) for dataset in ("dvf", "dpe") for code in context.communes
    }
    entries = {d.dataset_name: d for d in context.datasets}
    seen = set()
    public: dict[str, dict[str, Any]] = {
        code: {"code_insee": code} for code in context.communes
    }
    for cell in cells:
        key = cell.dataset_name, cell.code_insee
        if key not in expected or key in seen:
            raise ResultStoreRefused("result_build_integrity")
        seen.add(key)
        entry = entries[cell.dataset_name]
        if _canonical(cell.private_binding) != _canonical(
            _binding(entry, cell.code_insee, context, header)
        ):
            raise ResultStoreRefused("result_build_integrity")
        try:
            block = projector._PUBLIC_ADAPTERS[cell.dataset_name].validate_python(
                cell.public_payload
            )
            payload = projector._public_block(block, cell.dataset_name)
        except (ValueError, TypeError, AttributeError):
            raise ResultStoreRefused("result_build_integrity") from None
        if _canonical(payload) != _canonical(cell.public_payload):
            raise ResultStoreRefused("result_build_integrity")
        versions = _versions(entry, cell.code_insee)
        if not versions:
            reason = (
                "territorial_attribution_unqualified"
                if cell.dataset_name == "dvf"
                and cell.code_insee in DVF_TERRITORIAL_UNQUALIFIED
                else "source_generation_unqualified"
                if entry.decision is None
                else "indicator_policy_unqualified"
            )
            if payload != {"etat": "not_qualified", "motif": reason}:
                raise ResultStoreRefused("result_build_integrity")
        else:
            assert entry.decision is not None and entry.source is not None
            manifest = entry.decision.manifest
            if (
                payload.get("generation") != str(entry.source.generation)
                or getattr(block, "date_calcul", None) != header.calculated_at
                or payload.get("provenance") != projector._sources(cell.dataset_name)
                or payload.get("version_population")
                != (
                    projector.DVF_PROFILE
                    if cell.dataset_name == "dvf"
                    else projector.DPE_PROFILE
                )
            ):
                raise ResultStoreRefused("result_build_integrity")
            if cell.dataset_name == "dpe":
                if getattr(block, "date_evaluation", None) != manifest.dpe_as_of:
                    raise ResultStoreRefused("result_build_integrity")
            else:
                assert isinstance(manifest.dvf_anchor, FrozenReceivedAnchor)
                if payload.get("ancrage_temporel") != manifest.dvf_anchor.model_dump(
                    mode="json"
                ):
                    raise ResultStoreRefused("result_build_integrity")
                for kind in ("residentiel_maison", "residentiel_appartement"):
                    group = payload[kind]
                    trend_supported = (
                        "dvf.trend_pct",
                        projector.DVF_TREND_PROFILE,
                    ) in versions
                    variation = group["variation_pct"]
                    if not trend_supported and (
                        variation
                        != {
                            "etat": "not_qualified",
                            "motif": "indicator_policy_unqualified",
                        }
                        or group["qualification_tendance"] is not None
                    ):
                        raise ResultStoreRefused("result_build_integrity")
                    if trend_supported:
                        if variation["etat"] == "value":
                            if (
                                group["qualification_tendance"]
                                != projector.DVF_TREND_QUALIFICATION
                            ):
                                raise ResultStoreRefused("result_build_integrity")
                        elif group["qualification_tendance"] is not None:
                            raise ResultStoreRefused("result_build_integrity")
                    for period in ("previous", "current"):
                        stats = group[
                            "precedente" if period == "previous" else "courante"
                        ]
                        if stats["quantiles"] != {
                            "etat": "not_qualified",
                            "motif": "indicator_policy_unqualified",
                        }:
                            raise ResultStoreRefused("result_build_integrity")
                        if (
                            "dvf.median_eur_m2",
                            projector.DVF_PROFILE,
                        ) not in versions and stats["mediane"] != {
                            "etat": "not_qualified",
                            "motif": "indicator_policy_unqualified",
                        }:
                            raise ResultStoreRefused("result_build_integrity")
        public[cell.code_insee]["indicateurs_" + cell.dataset_name] = payload
    if seen != expected:
        raise ResultStoreRefused("result_build_integrity")
    return [public[code] for code in context.communes]


def _envelope(header: _Header, cells: list[_Cell]) -> dict[str, Any]:
    return dict(
        header=header.model_dump(mode="json"),
        cells=[
            c.model_dump(mode="json")
            for c in sorted(cells, key=lambda c: (c.dataset_name, c.code_insee))
        ],
    )


_READ = """WITH cells AS MATERIALIZED (
    SELECT dataset_name,code_insee,public_payload,private_binding,
           octet_length(public_payload::text)+octet_length(private_binding::text)
           AS bytes
    FROM public.commune_indicator_result_cells WHERE build_id=%s
    ORDER BY dataset_name,code_insee LIMIT %s
), bounds AS (
    SELECT count(*) AS n,coalesce(sum(bytes),0) AS
    total,coalesce(max(bytes),0) AS largest FROM cells
)
SELECT CASE WHEN octet_length(b.header::text)<=%s THEN b.header END AS header,
       b.build_sha256,
       CASE WHEN n=%s AND total<=%s AND largest<=%s THEN
         (SELECT
         jsonb_agg(jsonb_build_object('dataset_name',dataset_name,'code_insee',code_insee,
             'public_payload',public_payload,'private_binding',private_binding)
             ORDER BY dataset_name,code_insee) FROM cells) END AS cells
FROM public.commune_indicator_result_builds b CROSS JOIN bounds WHERE b.id=%s"""


def _load(
    conn: psycopg.Connection, build_id: int, cell_count: int
) -> tuple[_Header, list[_Cell], str]:
    """Read at most *cell_count* + 1 cells: the expected build or a refusal."""
    with conn.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            _READ,
            (
                build_id,
                cell_count + 1,
                MAX_HEADER_BYTES,
                cell_count,
                MAX_BUILD_BYTES,
                MAX_CELL_BYTES,
                build_id,
            ),
        ).fetchone()
    if row is None:
        raise ResultStoreRefused("result_build_missing")
    header, cells = validate_stored_build(
        header_value=row["header"],
        cells_value=row["cells"],
        build_sha256=row["build_sha256"],
    )
    return header, cells, row["build_sha256"]


def validate_stored_build(
    *, header_value: Any, cells_value: Any, build_sha256: str
) -> tuple[_Header, list[_Cell]]:
    """Pure integrity validation shared by sync and async readers."""
    try:
        header = _Header.model_validate(header_value)
        if not isinstance(cells_value, list) or len(cells_value) != header.cell_count:
            raise ValueError("Wrong cardinality")
        cells = [_Cell.model_validate(cell) for cell in cells_value]
        if _digest(_envelope(header, cells)) != build_sha256:
            raise ValueError("Wrong integrity digest")
        if (
            len({(cell.dataset_name, cell.code_insee) for cell in cells})
            != header.cell_count
        ):
            raise ValueError("Duplicate cells")
        for cell in cells:
            block = projector._PUBLIC_ADAPTERS[cell.dataset_name].validate_python(
                cell.public_payload
            )
            if _canonical(
                projector._public_block(block, cell.dataset_name)
            ) != _canonical(cell.public_payload):
                raise ValueError("Non-canonical public payload")
        return header, cells
    except (ValueError, TypeError, KeyError, RecursionError, AttributeError):
        raise ResultStoreRefused("result_build_integrity") from None


def validate_build_contents(
    *, header: _Header, cells: list[_Cell], context: IndicatorBuildContext
) -> PublicResultBuild:
    """Validate bindings, policy states and payloads against fresh facts."""
    _match(header, context)
    return PublicResultBuild(_canonical(_validate_cells(cells, header, context)))


def _match(header: _Header, context: IndicatorBuildContext) -> None:
    # No XID or fresh checked_at comparison: a new owner reads a historical build.
    if (
        header.scope != context.scope
        or header.commune_count != len(context.communes)
        or header.cog_sha256 != context.cog_sha256
        or header.cog_version != context.cog_version
        or header.codes_sha256 != _digest(context.communes)
        or header.observed_context != _facts(context)
    ):
        raise ResultStoreRefused("result_build_stale")


def build_and_store(
    conn: psycopg.Connection,
    *,
    context: IndicatorBuildContext,
    request: ProjectionRequest,
) -> ResultBuildReceipt:
    """Compute internally and persist one complete immutable synthetic build.

    No result tuple or user-supplied binding is accepted. Savepoint only: the
    existing owner transaction remains responsible for the eventual commit.
    """
    _guard(conn, context)
    with conn.transaction():
        rows = project_indicator_blocks(conn, context=context, request=request)
        header = _header(context, request)
        if len(rows) != len(context.communes) or {r.code_insee for r in rows} != set(
            context.communes
        ):
            raise ResultStoreRefused("result_build_integrity")
        cells = []
        for row in rows:
            public = row.public_dump()
            for dataset in ("dvf", "dpe"):
                binding = getattr(row, dataset + "_binding")
                cells.append(
                    _Cell(
                        dataset_name=dataset,
                        code_insee=row.code_insee,
                        public_payload=public["indicateurs_" + dataset],
                        private_binding=json.loads(_canonical(asdict(binding))),
                    )
                )
        _validate_cells(cells, header, context)
        envelope = _envelope(header, cells)
        digest = _digest(envelope)
        for cell in cells:
            _canonical(cell.model_dump(mode="json"), MAX_CELL_BYTES)
        _canonical(header.model_dump(mode="json"), MAX_HEADER_BYTES)
        with conn.cursor(row_factory=tuple_row) as cursor:
            found = cursor.execute(
                (
                    "INSERT INTO "
                    "public.commune_indicator_result_builds(build_sha256,header) "
                    "VALUES (%s,%s) ON CONFLICT(build_sha256) DO NOTHING "
                    "RETURNING id"
                ),
                (digest, Jsonb(header.model_dump(mode="json"))),
            ).fetchone()
            inserted = found is not None
            if not inserted:
                found = cursor.execute(
                    (
                        "SELECT id FROM public.commune_indicator_result_builds WHERE "
                        "build_sha256=%s"
                    ),
                    (digest,),
                ).fetchone()
            if found is None:
                raise ResultStoreRefused("result_build_integrity")
            build_id = found[0]
            if inserted:
                cursor.executemany(
                    (
                        "INSERT INTO "
                        "public.commune_indicator_result_cells(build_id,dataset_name,code_insee,public_payload,private_binding)"
                        " VALUES (%s,%s,%s,%s,%s)"
                    ),
                    [
                        (
                            build_id,
                            c.dataset_name,
                            c.code_insee,
                            Jsonb(c.public_payload),
                            Jsonb(c.private_binding),
                        )
                        for c in cells
                    ],
                )
        actual_header, actual_cells, _ = _load(conn, build_id, header.cell_count)
        if _canonical(_envelope(actual_header, actual_cells)) != _canonical(envelope):
            raise ResultStoreRefused("result_build_integrity")
        _guard(conn, context)
        _match(header, context)
        return ResultBuildReceipt(
            build_id, digest, inserted, header.commune_count, header.cell_count
        )


def read_build(
    conn: psycopg.Connection, *, build_id: int, context: IndicatorBuildContext
) -> PublicResultBuild:
    """Exact build only, using a fresh guarded context; no aggregation or fallback.

    Two rejected external pin sets are indistinguishable if the builder exposes
    the same source/registry/refusal and no loaded decision. Both remain absent.
    A loaded decision appearing or disappearing always changes these facts.
    """
    if type(build_id) is not int or build_id <= 0:
        raise ResultStoreRefused("result_build_missing")
    _guard(conn, context)
    header, cells, _ = _load(conn, build_id, 2 * len(context.communes))
    _match(header, context)
    public = _validate_cells(cells, header, context)
    _guard(conn, context)
    _match(header, context)
    return PublicResultBuild(_canonical(public))
