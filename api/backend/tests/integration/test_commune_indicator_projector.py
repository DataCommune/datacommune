"""Synthetic decisions through a real owner, registry and SQL in a disposable DB."""

import hashlib
import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from psycopg.errors import UndefinedColumn

from app.commune import indicator_projector as ip
from app.commune.indicator_context import build_indicator_context
from app.commune.indicator_manifest import (
    ManifestRefused,
    QualificationContext,
    StoredManifest,
    manifest_sha256,
)
from app.commune.indicator_storage import append_manifest
from app.etl.publication import Publication
from tests.integration import test_commune_indicator_storage as storage
from tests.integration.test_commune_indicator_context import trust
from tests.integration.test_commune_indicator_sql import dpe, insert, mutation
from tests.test_commune_indicator_projector import NOW, REQUEST, payload

# Each module owns a child DB of the disposable wrapper, never a shared DB.
db = storage.db


@pytest.fixture
def source(db):
    conn, dsn = db
    conn.execute("DROP TABLE diagnostics_dpe, mutations_grain")
    conn.execute("""CREATE TABLE diagnostics_dpe (
        numero_dpe text, code_insee text, etiquette_dpe text, type_batiment text,
        source_state text, regime_dpe text, source_observations jsonb,
        is_tombstone boolean, date_etablissement_dpe date, date_fin_validite_dpe date,
        annee_construction integer, lifecycle_facts jsonb, revision_evidence jsonb)""")
    conn.execute("""CREATE TABLE mutations_grain (
        id_mutation text, code_departement text, code_commune text,
        date_mutation date, classe_mutation text, mono_local boolean,
        est_vente boolean, hors_stats_vefa boolean, nb_communes integer,
        surface_bati_totale integer, valeur_fonciere numeric(15,2))""")
    conn.commit()
    yield conn, dsn


def register(conn, directory, inputs, dataset, *, state=None, trend=False, anchor=None):
    folder = directory / dataset
    doc = payload(folder, dataset)
    doc["scope"] = inputs.scope
    doc.update(cog_sha256=inputs.cog.sha256, cog_version=inputs.cog.version)
    if dataset == "dvf":
        doc["dvf_anchor"]["cog_sha256"] = inputs.cog.sha256
        doc["dvf_anchor"].update(anchor or {})
    if state:
        for item in doc["indicators"].values():
            item.update(
                status=state, rule_version=None, reason_code="synthetic_refusal"
            )
    if dataset == "dvf" and trend:
        doc["indicators"]["dvf.trend_pct"].update(
            status="qualified", rule_version=ip.DVF_TREND_PROFILE
        )
    policy = json.loads((folder / "policy.json").read_bytes())
    policy["scope"] = inputs.scope
    policy["indicators"] = doc["indicators"]
    policy_raw = json.dumps(policy).encode()
    ph = hashlib.sha256(policy_raw).hexdigest()
    review = json.loads((folder / "review.json").read_bytes())
    review["scope"] = inputs.scope
    review.update(
        indicators=doc["indicators"],
        cog_sha256=inputs.cog.sha256,
        cog_version=inputs.cog.version,
        policy_sha256=ph,
    )
    review_raw = json.dumps(review).encode()
    eh = hashlib.sha256(review_raw).hexdigest()
    (folder / "policy.json").write_bytes(policy_raw)
    (folder / "review.json").write_bytes(review_raw)
    doc["policy"] = dict(path=f"{dataset}/policy.json", sha256=ph)
    doc["evidence"] = dict(path=f"{dataset}/review.json", sha256=eh)
    if dataset == "dvf":
        doc["dvf_anchor"]["preuve_qualification_sha256"] = eh
    pins = QualificationContext(
        dataset_name=dataset,
        source_generation=doc["source_generation"],
        source_snapshot_sha256=doc["source_snapshot_sha256"],
        cog_sha256=inputs.cog.sha256,
        cog_version=inputs.cog.version,
        policy_version=doc["policy_version"],
        policy_sha256=ph,
        evidence_sha256=eh,
        scope=inputs.scope,
        checked_at=NOW,
    )
    record = StoredManifest(
        dataset_name=dataset,
        source_generation=doc["source_generation"],
        manifest=doc,
        manifest_sha256=manifest_sha256(doc),
        qualified_at=doc["qualified_at"],
    )
    append_manifest(conn, record=record, context=pins, evidence_root=directory)
    return pins


def prepare(
    conn,
    tmp_path,
    n=5,
    labels=("A", "A", "G", "G"),
    scope="synthetic",
    trend=False,
):
    insert(conn, "mutations_grain", [mutation(str(i)) for i in range(n)])
    insert(
        conn, "diagnostics_dpe", [dpe(str(i), label) for i, label in enumerate(labels)]
    )
    storage.publish(conn)
    storage.publish(conn, "dvf", storage.G2)
    storage.lock(conn)
    inputs = trust(tmp_path, scope=scope)
    dpe_pins = register(conn, tmp_path, inputs, "dpe")
    dvf_pins = register(conn, tmp_path, inputs, "dvf", trend=trend)
    inputs = inputs.model_copy(update={"dpe": dpe_pins, "dvf": dvf_pins})
    return inputs, build_indicator_context(conn, trust=inputs)


@pytest.mark.parametrize("n", [0, 4, 5])
def test_real_460_keys_counts_median_and_documentary_family(source, tmp_path, n):
    conn, _ = source
    _, ctx = prepare(conn, tmp_path, n)
    rows = ip.project_indicator_blocks(conn, context=ctx, request=REQUEST)
    assert len(rows) == 460 and len({r.code_insee for r in rows}) == 460
    by_code = {r.code_insee: r for r in rows}
    sample = by_code["44001"].indicateurs_dvf.residentiel_maison
    assert sample.courante.effectif == n and sample.precedente.effectif == 0
    assert sample.courante.mediane.etat == (
        "value" if n == 5 else "insufficient_sample" if n else "not_reported"
    )
    if n == 5:
        assert sample.courante.mediane.valeur == 2000.0
    assert by_code["44001"].indicateurs_dpe.resultat.modes == ["A", "G"]
    assert by_code["44001"].indicateurs_dpe.resultat.mediane_ordinale == "A"
    assert by_code["44002"].indicateurs_dpe.resultat.population.total == 0
    for code in ("85084", "85165", "85212"):
        assert (
            by_code[code].indicateurs_dvf.motif == "territorial_attribution_unqualified"
        )
        assert by_code[code].indicateurs_dpe.resultat.etat == "value"
    assert all("binding" not in str(r.public_dump()) for r in rows)
    assert ctx.checked_at == NOW and ctx.scope == "synthetic"


@pytest.mark.parametrize("dataset", ["dvf", "dpe"])
def test_latest_no_go_never_uses_old_qualified(source, tmp_path, dataset):
    conn, _ = source
    inputs, old = prepare(conn, tmp_path)
    new_pins = register(conn, tmp_path, inputs, dataset, state="no_go")
    with pytest.raises(ManifestRefused, match="registry_decision_changed"):
        ip.project_indicator_blocks(conn, context=old, request=REQUEST)
    ctx = build_indicator_context(
        conn, trust=inputs.model_copy(update={dataset: new_pins})
    )
    rows = ip.project_indicator_blocks(conn, context=ctx, request=REQUEST)
    assert all(
        getattr(row, "indicateurs_" + dataset).etat == "not_qualified" for row in rows
    )


def test_authentic_pending_g2_has_no_favorable_g1_projection(source, tmp_path):
    conn, dsn = source
    inputs, ctx = prepare(conn, tmp_path)
    assert any(
        r.indicateurs_dpe.resultat.population.total
        for r in ip.project_indicator_blocks(conn, context=ctx, request=REQUEST)
    )
    conn.commit()
    conn.close()
    pipeline = SimpleNamespace(
        dataset_name="dpe", production_table_name="diagnostics_dpe"
    )
    with Publication(dsn, pipeline) as publication:
        publication.observe_stock()
        with publication.required_work() as work:
            ctx = build_indicator_context(
                work.connection, trust=inputs, publication_work=work
            )
            rows = ip.project_indicator_blocks(
                work.connection, context=ctx, request=REQUEST
            )
            assert all(r.indicateurs_dpe.etat == "not_qualified" for r in rows)
            assert (
                rows[0].dpe_binding.source_generation
                == publication.generation
                != storage.G1
            )
            assert rows[0].dpe_binding.registry_id is None
            assert rows[0].dpe_binding.snapshot_sha256 is None
            assert rows[0].dpe_binding.source_status == "pending"
            publication.publish(lambda _: None)


@pytest.mark.parametrize("fault", ["commit", "rollback", "unlock", "source_failed"])
def test_expired_or_technically_failed_context_cannot_project(source, tmp_path, fault):
    conn, _ = source
    _, ctx = prepare(conn, tmp_path)
    if fault == "commit":
        conn.commit()
        conn.execute("SELECT 1")
    elif fault == "rollback":
        conn.rollback()
        conn.execute("SELECT 1")
    elif fault == "unlock":
        conn.execute("SELECT pg_advisory_unlock_all()")
    else:
        storage.publish(
            conn,
            generation=UUID("33333333-3333-4333-8333-333333333333"),
            status="failed",
        )
    with pytest.raises(ManifestRefused):
        ip.project_indicator_blocks(conn, context=ctx, request=REQUEST)


@pytest.mark.parametrize("fault", ["registry", "sql", "missing_column"])
def test_no_partial_results_after_technical_failure(
    source, tmp_path, monkeypatch, fault
):
    conn, _ = source
    inputs, ctx = prepare(conn, tmp_path)
    if fault == "missing_column":
        conn.execute("ALTER TABLE diagnostics_dpe DROP COLUMN numero_dpe")
    else:
        original = ip.query_dpe_indicators

        def changed(*args, **kwargs):
            rows = original(*args, **kwargs)
            if fault == "registry":
                register(conn, tmp_path, inputs, "dpe", state="no_go")
            else:
                raise RuntimeError("synthetic_sql_failure")
            return rows

        monkeypatch.setattr(ip, "query_dpe_indicators", changed)
    expected = {
        "registry": ManifestRefused,
        "sql": RuntimeError,
        "missing_column": UndefinedColumn,
    }[fault]
    with pytest.raises(expected):
        ip.project_indicator_blocks(conn, context=ctx, request=REQUEST)
