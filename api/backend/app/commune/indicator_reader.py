"""Async exact-build reader running entirely on the caller's SQLAlchemy session.

The whole active build (header, every cell, bindings against the pinned
decisions and their evidence) is validated once per fingerprint: activation
event, build digest, stored header, source publications, decision records and
runtime policy. The fingerprint is read on every request, so a new activation,
a revocation or a changed stored row forces a full validation again. Each
request then reads only the cells of the requested communes and compares every
one with the digest of its validated cell (T-04.1-28).
"""

import json
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.commune.indicator_context import (
    DatasetContext,
    IndicatorBuildContext,
    read_qualified_communes,
)
from app.commune.indicator_manifest import (
    ManifestRefused,
    StoredManifest,
    load_qualified_manifest,
    manifest_sha256,
)
from app.commune.indicator_policy import RuntimeIndicatorPolicy
from app.commune.indicator_results import (
    ResultStoreRefused,
    _digest,
    validate_build_contents,
    validate_stored_build,
)
from app.commune.indicator_storage import LoadedDecision, PublishedSource

_DATASETS = ("dpe", "dvf")


def _sql_digest(column: str) -> str:
    """SQL digest of a stored JSON value: detects any rewrite of the row."""
    return f"encode(sha256(convert_to({column}::text,'UTF8')),'hex')"


@dataclass(frozen=True)
class ActiveIndicators:
    """Public blocks of the requested communes of the active build.

    ``hors_build``: requested codes the qualified build does not contain. A
    requested commune of the build is in ``blocks`` with both of its cells,
    or the read is refused: a missing cell is an outage, never an absence.
    """

    blocks: dict[str, dict[str, Any]]
    hors_build: frozenset[str]


@dataclass(frozen=True)
class _DatasetFacts:
    source: PublishedSource
    record_id: int
    manifest_digest: str


@dataclass(frozen=True)
class _Fingerprint:
    event_id: int
    build_id: int
    build_sha256: str
    header_digest: str
    policy: str
    datasets: tuple[_DatasetFacts, ...]


@dataclass(frozen=True)
class _ValidatedBuild:
    communes: frozenset[str]
    # (dataset, code_insee) -> digest of the validated public payload.
    cells: dict[tuple[str, str], str]


# One entry, the active build of this process; replaced at any new fingerprint.
_validated: tuple[_Fingerprint, _ValidatedBuild] | None = None


async def _one(session: AsyncSession, sql: str, params: dict[str, Any] | None = None):
    return (await session.execute(text(sql), params or {})).mappings().one_or_none()


async def _fingerprint(
    session: AsyncSession, policy: RuntimeIndicatorPolicy
) -> _Fingerprint:
    """Every stored fact the validation depends on, read without the cells."""
    head = await _one(
        session,
        f"""SELECT a.id,a.event_type,a.build_id,b.build_sha256,
                   {_sql_digest("b.header")} AS header_digest
            FROM (SELECT id,event_type,build_id
                  FROM commune_indicator_build_activations
                  ORDER BY id DESC LIMIT 1) a
            LEFT JOIN commune_indicator_result_builds b ON b.id=a.build_id""",
    )
    if head is None:
        raise ResultStoreRefused("active_build_missing")
    if head["event_type"] != "activate":
        raise ResultStoreRefused("active_build_revoked")
    if head["build_sha256"] is None:
        raise ResultStoreRefused("result_build_missing")
    datasets = []
    for dataset, pins in (("dpe", policy.dpe), ("dvf", policy.dvf)):
        source = await _one(
            session,
            """SELECT p.id,p.generation,p.table_name,p.status,p.relation_oids,
                      p.row_count,p.completed_at,
               (SELECT jsonb_object_agg(name,to_regclass('public.'||name)::oid::bigint)
                FROM unnest(CASE WHEN :dataset='dpe' THEN ARRAY['diagnostics_dpe']
                  ELSE ARRAY['mutations','mutations_grain'] END) names(name)
               ) current_oids
               FROM etl_publications p WHERE p.dataset_name=:dataset
               ORDER BY p.id DESC LIMIT 1""",
            {"dataset": dataset},
        )
        expected_table = "diagnostics_dpe" if dataset == "dpe" else "mutations"
        if (
            source is None
            or source["status"] != "published"
            or source["table_name"] != expected_table
            or source["generation"] != pins.source_generation
            or source["completed_at"] is None
            or source["relation_oids"] != source["current_oids"]
        ):
            raise ResultStoreRefused("active_build_source_not_published")
        record = await _one(
            session,
            f"""SELECT id,{_sql_digest("manifest")} AS manifest_digest
               FROM commune_indicator_manifests
               WHERE dataset_name=:dataset AND source_generation=:generation
               ORDER BY id DESC LIMIT 1""",
            {"dataset": dataset, "generation": pins.source_generation},
        )
        if record is None:
            raise ResultStoreRefused("result_build_stale")
        datasets.append(
            _DatasetFacts(
                PublishedSource(
                    source["id"],
                    dataset,
                    source["generation"],
                    tuple(sorted(source["relation_oids"].items())),
                    source["row_count"],
                ),
                record["id"],
                record["manifest_digest"],
            )
        )
    return _Fingerprint(
        head["id"],
        head["build_id"],
        head["build_sha256"],
        head["header_digest"],
        policy.model_dump_json(),
        tuple(datasets),
    )


async def _dataset_context(
    session: AsyncSession, policy: RuntimeIndicatorPolicy, facts: _DatasetFacts
) -> DatasetContext:
    dataset = facts.source.dataset_name
    pins = policy.dpe if dataset == "dpe" else policy.dvf
    record = await _one(
        session,
        f"""SELECT id,dataset_name,source_generation,manifest,manifest_sha256,
                  qualified_at,{_sql_digest("manifest")} AS manifest_digest
           FROM commune_indicator_manifests WHERE id=:id""",
        {"id": facts.record_id},
    )
    if record is None or record["manifest_digest"] != facts.manifest_digest:
        # Changed since the fingerprint: the next request reads it again.
        raise ResultStoreRefused("result_build_stale")
    try:
        manifest_value = record["manifest"]
        if isinstance(manifest_value, str):
            manifest_value = json.loads(manifest_value)
        stored = StoredManifest.model_validate(
            {
                **{key: record[key] for key in StoredManifest.model_fields},
                "manifest": manifest_value,
            }
        )
        manifest = load_qualified_manifest(
            stored, context=pins, evidence_root=policy.evidence_root
        )
    except (
        ManifestRefused,
        ValidationError,
        ValueError,
        TypeError,
        KeyError,
    ) as exc:
        code = (
            exc.code if isinstance(exc, ManifestRefused) else "result_build_integrity"
        )
        raise ResultStoreRefused(code) from None
    loaded = LoadedDecision(
        record["id"], record["manifest_sha256"], facts.source, manifest
    )
    return DatasetContext(
        dataset,
        facts.source,
        loaded,
        None,
        (record["id"], record["manifest_sha256"]),
        manifest_sha256(manifest.model_dump(mode="json")),
    )


async def _validate(
    session: AsyncSession, policy: RuntimeIndicatorPolicy, fingerprint: _Fingerprint
) -> _ValidatedBuild:
    """Full integrity validation of the fingerprinted build (reads every cell)."""
    # The cell bound follows the pinned COG reference (one cell per dataset and
    # commune; 920 for 44/85): one more cell is read, so an oversized build is
    # refused, never truncated. Checked here once, never per request.
    trust = policy.trust_inputs()
    communes = read_qualified_communes(trust)
    row = await _one(
        session,
        f"""SELECT b.header,b.build_sha256,{_sql_digest("b.header")} AS header_digest,
          (SELECT jsonb_agg(jsonb_build_object(
             'dataset_name',c.dataset_name,'code_insee',c.code_insee,
             'public_payload',c.public_payload,'private_binding',c.private_binding)
             ORDER BY c.dataset_name,c.code_insee)
           FROM (SELECT * FROM commune_indicator_result_cells
                 WHERE build_id=b.id ORDER BY dataset_name,code_insee
                 LIMIT :cell_limit) c
          ) AS cells
        FROM commune_indicator_result_builds b WHERE b.id=:build_id""",
        {"build_id": fingerprint.build_id, "cell_limit": 2 * len(communes) + 1},
    )
    if row is None:
        raise ResultStoreRefused("result_build_missing")
    if (row["build_sha256"], row["header_digest"]) != (
        fingerprint.build_sha256,
        fingerprint.header_digest,
    ):
        raise ResultStoreRefused("result_build_stale")
    header, cells = validate_stored_build(
        header_value=row["header"],
        cells_value=row["cells"],
        build_sha256=row["build_sha256"],
    )
    if (
        header.scope != "preproduction"
        or header.cog_sha256 != policy.cog.sha256
        or header.cog_version != policy.cog.version
    ):
        raise ResultStoreRefused("result_build_stale")
    datasets = [
        await _dataset_context(session, policy, facts) for facts in fingerprint.datasets
    ]
    context = IndicatorBuildContext(
        communes,
        policy.cog.sha256,
        policy.cog.version,
        tuple(datasets),
        policy.checked_at,
        "preproduction",
        None,  # type: ignore[arg-type] -- pure validation never touches a connection
        (),
        None,
        None,
    )
    public = validate_build_contents(header=header, cells=cells, context=context)
    return _ValidatedBuild(
        frozenset(context.communes),
        {
            (dataset, entry["code_insee"]): _digest(entry["indicateurs_" + dataset])
            for entry in public.public_dump()
            for dataset in _DATASETS
        },
    )


def _forget(fingerprint: _Fingerprint) -> None:
    global _validated
    if _validated is not None and _validated[0] == fingerprint:
        _validated = None


async def _requested_cells(
    session: AsyncSession,
    fingerprint: _Fingerprint,
    build: _ValidatedBuild,
    codes: list[str],
) -> dict[str, dict[str, Any]]:
    """Public blocks of *codes* (all in the build), each checked by digest."""
    if not codes:
        return {}
    rows = (
        await session.execute(
            text(
                """SELECT dataset_name,code_insee,public_payload
                   FROM commune_indicator_result_cells
                   WHERE build_id=:build_id AND dataset_name IN ('dpe','dvf')
                     AND code_insee=ANY(:codes)"""
            ),
            {"build_id": fingerprint.build_id, "codes": codes},
        )
    ).mappings()
    blocks: dict[str, dict[str, Any]] = {code: {"code_insee": code} for code in codes}
    intact = True
    for row in rows:
        payload = row["public_payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        key = row["dataset_name"], row["code_insee"]
        block = blocks[row["code_insee"]]
        column = "indicateurs_" + row["dataset_name"]
        if column in block or build.cells.get(key) != _digest(payload):
            intact = False
            break
        block[column] = payload
    if not intact or any(len(block) != 1 + len(_DATASETS) for block in blocks.values()):
        # A cell changed or vanished after validation: refuse, and validate the
        # whole build again at the next request instead of trusting the cache.
        _forget(fingerprint)
        raise ResultStoreRefused("result_build_integrity")
    return blocks


async def read_active_preproduction_build(
    session: AsyncSession,
    *,
    policy: RuntimeIndicatorPolicy,
    environment: str,
    codes: Collection[str],
) -> ActiveIndicators:
    """Blocks of *codes* from the latest global activation, or refuse."""
    global _validated
    policy.assert_runtime_environment(environment)
    fingerprint = await _fingerprint(session, policy)
    cached = _validated
    if cached is not None and cached[0] == fingerprint:
        build = cached[1]
    else:
        build = await _validate(session, policy, fingerprint)
        _validated = (fingerprint, build)
    asked = sorted(set(codes))
    blocks = await _requested_cells(
        session, fingerprint, build, [code for code in asked if code in build.communes]
    )
    return ActiveIndicators(blocks, frozenset(asked) - build.communes)
