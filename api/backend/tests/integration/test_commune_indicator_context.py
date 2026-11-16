"""Context-only canaries on a fresh PostGIS; no production qualification."""

import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest

from app.commune import indicator_context as ic
from app.commune.indicator_manifest import ManifestRefused, manifest_sha256
from app.commune.indicator_storage import append_manifest
from app.etl.publication import Publication, PublicationWork
from tests.integration import test_commune_indicator_storage as storage_fixture
from tests.integration.test_commune_indicator_storage import (
    G1,
    G2,
    decision,
    lock,
    publish,
)

db = storage_fixture.db


def trust(directory, **changes):
    directory.mkdir(exist_ok=True)
    path = directory / "cog.json"
    # Public identities only, copied byte-for-byte from the qualified COG receipt.
    raw = (
        Path(__file__).parents[1]
        / "fixtures/commune_indicators/cog-2026-identities.json"
    ).read_bytes()
    path.write_bytes(raw)
    scope = changes.pop("scope", "synthetic")
    return ic.IndicatorTrustInputs(
        cog=ic.CogInput(
            path=path,
            root=directory,
            sha256=hashlib.sha256(raw).hexdigest(),
            version="insee-cog-2026-01-01-identities",
        ),
        evidence_root=directory,
        scope=scope,
        checked_at="2026-09-09T14:00:00Z",
        **changes,
    )


def bound_decision(directory, inputs, generation=G1, dataset="dpe", state=None):
    record, context = decision(directory, generation, dataset, state)
    payload = record.manifest
    payload["cog_sha256"] = inputs.cog.sha256
    payload["cog_version"] = inputs.cog.version
    review_path = directory / "review.json"
    review = json.loads(review_path.read_bytes())
    review["cog_sha256"] = inputs.cog.sha256
    review["cog_version"] = inputs.cog.version
    raw = json.dumps(review).encode()
    review_path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    payload["evidence"]["sha256"] = digest
    return (
        record.model_copy(
            update={"manifest": payload, "manifest_sha256": manifest_sha256(payload)}
        ),
        context.model_copy(
            update={
                "cog_sha256": inputs.cog.sha256,
                "cog_version": inputs.cog.version,
                "evidence_sha256": digest,
            }
        ),
    )


def test_legacy_is_absence_and_context_expires_after_transaction(db, tmp_path):
    conn, _ = db
    inputs = trust(tmp_path)
    lock(conn)
    context = ic.build_indicator_context(conn, trust=inputs)
    assert len(context.communes) == 460
    assert {"44001", "85084", "85165", "85212"} <= set(context.communes)
    assert all(d.refusal == "source_generation_unqualified" for d in context.datasets)
    with pytest.raises(FrozenInstanceError):
        context.communes = ()
    context.assert_current(conn)
    conn.commit()
    conn.execute("SELECT 1")
    with pytest.raises(ManifestRefused, match="transaction"):
        context.assert_current(conn)


@pytest.mark.parametrize("fault", ["none", "shared", "autocommit", "other_connection"])
def test_owner_preconditions_are_not_an_intrans_boolean(db, tmp_path, fault):
    conn, dsn = db
    inputs = trust(tmp_path)
    if fault == "shared":
        from app.commune.dependencies import PUBLICATION_LOCK_ID

        conn.execute("SELECT pg_advisory_lock_shared(%s)", (PUBLICATION_LOCK_ID,))
    elif fault == "autocommit":
        conn.autocommit = True
    elif fault == "other_connection":
        lock(conn)
        with psycopg.connect(dsn) as other, pytest.raises(ManifestRefused):
            ic.build_indicator_context(other, trust=inputs)
        return
    with pytest.raises(ManifestRefused):
        ic.build_indicator_context(conn, trust=inputs)


@pytest.mark.parametrize("state", ["pending", "failed"])
def test_other_pending_or_failed_is_technical_not_absence(db, tmp_path, state):
    conn, _ = db
    publish(conn, status=state)
    lock(conn)
    with pytest.raises(ManifestRefused, match="source_publication_not_ready"):
        ic.build_indicator_context(conn, trust=trust(tmp_path))


def test_candidate_g2_never_loads_trusted_g1_and_ends_with_publication(db, tmp_path):
    conn, dsn = db
    publish(conn)
    conn.commit()
    inputs = trust(tmp_path)
    record, pins = bound_decision(tmp_path, inputs)
    lock(conn)
    append_manifest(conn, record=record, context=pins, evidence_root=tmp_path)
    conn.commit()
    conn.close()
    pipeline = SimpleNamespace(
        dataset_name="dpe", production_table_name="diagnostics_dpe"
    )
    with Publication(dsn, pipeline) as publication:
        publication.observe_stock()
        with publication.required_work() as work:
            context = ic.build_indicator_context(
                work.connection,
                trust=inputs.model_copy(update={"dpe": pins}),
                publication_work=work,
            )
            dpe = context.datasets[0]
            assert dpe.source.generation == publication.generation != G1
            assert (
                dpe.decision is None and dpe.refusal == "source_generation_unqualified"
            )
            context.assert_current(work.connection)
            publication.publish(lambda _: None)
        with pytest.raises(ManifestRefused):
            context.assert_current(publication.connection)


def test_fake_work_cannot_admit_candidate(db, tmp_path):
    conn, dsn = db
    conn.close()
    pipeline = SimpleNamespace(
        dataset_name="dpe", production_table_name="diagnostics_dpe"
    )
    with Publication(dsn, pipeline) as publication:
        publication.observe_stock()
        with publication.required_work() as work:
            with pytest.raises(ManifestRefused):
                ic.build_indicator_context(
                    work.connection,
                    trust=trust(tmp_path),
                    publication_work=PublicationWork(publication),
                )
            publication.publish(lambda _: None)


def test_loaded_decision_is_not_an_executable_policy_and_territory_is_preserved(
    db, tmp_path
):
    conn, _ = db
    publish(conn)
    publish(conn, "dvf", G2)
    inputs = trust(tmp_path)
    record, pins = bound_decision(tmp_path, inputs)
    lock(conn)
    append_manifest(conn, record=record, context=pins, evidence_root=tmp_path)
    context = ic.build_indicator_context(
        conn, trust=inputs.model_copy(update={"dpe": pins})
    )
    loaded = context.loaded_decision(conn, "dpe", "85084")
    assert (
        loaded.manifest.indicators["dpe.document_count"].rule_version
        == "synthetic-rule-v1"
    )
    assert not hasattr(context, "execute")
    for code in ("85084", "85165", "85212"):
        assert code in context.communes
        with pytest.raises(
            ManifestRefused, match="territorial_attribution_unqualified"
        ):
            context.loaded_decision(conn, "dvf", code)
    with pytest.raises(ManifestRefused, match="outside_qualified_reference"):
        context.loaded_decision(conn, "dpe", "99999")


@pytest.mark.parametrize("mutation", ["new_journal", "oid", "unlock", "register"])
def test_context_rechecks_current_source_and_latest_decision(db, tmp_path, mutation):
    conn, _ = db
    publish(conn)
    inputs = trust(tmp_path)
    record, pins = bound_decision(tmp_path, inputs)
    lock(conn)
    append_manifest(conn, record=record, context=pins, evidence_root=tmp_path)
    context = ic.build_indicator_context(
        conn, trust=inputs.model_copy(update={"dpe": pins})
    )
    if mutation == "new_journal":
        publish(conn, generation=G2)
    elif mutation == "oid":
        conn.execute("ALTER TABLE diagnostics_dpe RENAME TO retired_source")
        conn.execute("CREATE TABLE diagnostics_dpe(id bigint)")
    elif mutation == "unlock":
        conn.execute("SELECT pg_advisory_unlock_all()")
    else:
        later = tmp_path / "later"
        later.mkdir()
        newer, newer_pins = bound_decision(later, inputs, state="no_go")
        append_manifest(conn, record=newer, context=newer_pins, evidence_root=later)
    with pytest.raises(ManifestRefused):
        context.assert_current(conn)


@pytest.mark.parametrize("state", ["unknown", "pending", "no_go"])
def test_last_unqualified_decision_never_falls_back(db, tmp_path, state):
    conn, _ = db
    publish(conn)
    inputs = trust(tmp_path)
    lock(conn)
    first = tmp_path / "first"
    first.mkdir()
    old_record, old_pins = bound_decision(first, inputs)
    append_manifest(conn, record=old_record, context=old_pins, evidence_root=first)
    record, pins = bound_decision(tmp_path, inputs, state=state)
    append_manifest(conn, record=record, context=pins, evidence_root=tmp_path)
    context = ic.build_indicator_context(
        conn, trust=inputs.model_copy(update={"dpe": pins})
    )
    assert set(
        d.status
        for d in context.loaded_decision(
            conn, "dpe", "44001"
        ).manifest.indicators.values()
    ) == {state}


@pytest.mark.parametrize(
    "fault",
    [
        "hash",
        "duplicate",
        "extra",
        "mismatch",
        "version",
        "path",
        "size",
        "collectivity",
        "unknown_departement",
    ],
)
def test_cog_refusals_are_bounded_and_typed(db, tmp_path, fault):
    conn, _ = db
    inputs = trust(tmp_path)
    path = inputs.cog.path
    if fault == "hash":
        path.write_bytes(path.read_bytes().replace(b"44001", b"44002"))
    elif fault == "version":
        inputs = inputs.model_copy(
            update={"cog": inputs.cog.model_copy(update={"version": "future-cog"})}
        )
    elif fault in {"collectivity", "unknown_departement"}:
        # A reference row outside the hostable departements (D-123: 975 is an
        # overseas collectivity; 99 names no departement) is never qualified.
        raw = (
            b'[{"commune":"97501","department":"975"}]'
            if fault == "collectivity"
            else b'[{"commune":"99001","department":"99"}]'
        )
        path.write_bytes(raw)
        inputs = inputs.model_copy(
            update={
                "cog": inputs.cog.model_copy(
                    update={"sha256": hashlib.sha256(raw).hexdigest()}
                )
            }
        )
    elif fault == "path":
        inputs = inputs.model_copy(
            update={"cog": inputs.cog.model_copy(update={"root": tmp_path / "other"})}
        )
    elif fault == "size":
        path.write_bytes(b" " * (ic.MAX_COG_BYTES + 1))
    else:
        rows = json.loads(path.read_bytes())
        if fault == "duplicate":
            rows.append(rows[0])
        elif fault == "extra":
            rows[0]["name"] = "unqualified"
        else:
            rows[0]["department"] = "85"
        raw = json.dumps(rows).encode()
        path.write_bytes(raw)
        inputs = inputs.model_copy(
            update={
                "cog": inputs.cog.model_copy(
                    update={"sha256": hashlib.sha256(raw).hexdigest()}
                )
            }
        )
    lock(conn)
    with pytest.raises(ManifestRefused):
        ic.build_indicator_context(conn, trust=inputs)


def test_cardinality_is_read_in_the_pinned_reference_not_coded(db, tmp_path):
    """Plan 04.1-10: no frozen 207/253; the reference's own communes count.

    A pinned reference of one 44 commune and two 75 communes is read as such:
    its bytes are the producer extract bound by the caller's pin and the
    reviewed decision, the code never completes nor trims it.
    """
    conn, _ = db
    inputs = trust(tmp_path)
    raw = (
        b'[{"commune":"44001","department":"44"},'
        b'{"commune":"75056","department":"75"},'
        b'{"commune":"97411","department":"974"}]'
    )
    inputs.cog.path.write_bytes(raw)
    inputs = inputs.model_copy(
        update={
            "cog": inputs.cog.model_copy(
                update={"sha256": hashlib.sha256(raw).hexdigest()}
            )
        }
    )
    lock(conn)
    context = ic.build_indicator_context(conn, trust=inputs)
    assert context.communes == ("44001", "75056", "97411")


def test_mismatched_external_cog_pin_does_not_use_registry_claim(db, tmp_path):
    conn, _ = db
    publish(conn)
    inputs = trust(tmp_path)
    record, pins = decision(tmp_path)
    lock(conn)
    append_manifest(conn, record=record, context=pins, evidence_root=tmp_path)
    context = ic.build_indicator_context(
        conn, trust=inputs.model_copy(update={"dpe": pins})
    )
    assert context.datasets[0].decision is None
    assert context.datasets[0].refusal == "qualification_context_mismatch"


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_snapshot_that_may_predate_lock_is_not_admitted(db, tmp_path, isolation):
    conn, _ = db
    conn.execute("SET TRANSACTION ISOLATION LEVEL " + isolation)
    lock(conn)
    with pytest.raises(ManifestRefused, match="current_snapshot_required"):
        ic.build_indicator_context(conn, trust=trust(tmp_path))


def test_new_qualified_generation_requires_its_own_external_pins(db, tmp_path):
    conn, _ = db
    publish(conn, generation=G2)
    inputs = trust(tmp_path)
    _, old_pins = bound_decision(tmp_path, inputs)
    lock(conn)
    context = ic.build_indicator_context(
        conn, trust=inputs.model_copy(update={"dpe": old_pins})
    )
    assert context.datasets[0].decision is None
    assert context.datasets[0].refusal == "qualification_context_mismatch"
    record, pins = bound_decision(tmp_path, inputs, generation=G2)
    append_manifest(conn, record=record, context=pins, evidence_root=tmp_path)
    current = ic.build_indicator_context(
        conn, trust=inputs.model_copy(update={"dpe": pins})
    )
    assert current.loaded_decision(conn, "dpe", "44001").source.generation == G2


def test_missing_evidence_is_qualification_absence_not_a_registry_fallback(
    db, tmp_path
):
    conn, _ = db
    publish(conn)
    inputs = trust(tmp_path)
    record, pins = bound_decision(tmp_path, inputs)
    lock(conn)
    append_manifest(conn, record=record, context=pins, evidence_root=tmp_path)
    (tmp_path / "review.json").rename(tmp_path / "review-moved.json")
    context = ic.build_indicator_context(
        conn, trust=inputs.model_copy(update={"dpe": pins})
    )
    assert (
        context.datasets[0].decision is None and context.datasets[0].refusal is not None
    )


def test_duplicate_cog_json_keys_are_not_silently_overwritten(db, tmp_path):
    conn, _ = db
    inputs = trust(tmp_path)
    raw = b'[{"commune":"44002","commune":"44001","department":"44"}]'
    inputs.cog.path.write_bytes(raw)
    inputs = inputs.model_copy(
        update={
            "cog": inputs.cog.model_copy(
                update={"sha256": hashlib.sha256(raw).hexdigest()}
            )
        }
    )
    lock(conn)
    with pytest.raises(ManifestRefused, match="invalid_cog_reference"):
        ic.build_indicator_context(conn, trust=inputs)


def test_no_synthetic_cog_validator_in_production_or_test_scope(db, tmp_path):
    conn, _ = db
    inputs = trust(tmp_path)
    inputs = inputs.model_copy(
        update={"cog": inputs.cog.model_copy(update={"version": "synthetic-cog-v1"})}
    )
    lock(conn)
    with pytest.raises(ManifestRefused, match="unsupported_cog_version"):
        ic.build_indicator_context(conn, trust=inputs)
