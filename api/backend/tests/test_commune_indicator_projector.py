"""Synthetic projection contracts, without SQL or real qualification."""

from datetime import UTC, datetime

import pytest

from app.commune.indicator_manifest import ManifestRefused
from app.commune.indicator_projector import ProjectionRequest


def test_explicit_aware_calculation_required():
    with pytest.raises(ManifestRefused):
        ProjectionRequest(calculated_at=datetime(2026, 9, 9), scope="synthetic")
    assert ProjectionRequest(
        calculated_at=datetime(2026, 9, 9, tzinfo=UTC), scope="synthetic"
    )


from dataclasses import FrozenInstanceError, replace
from datetime import date
from fractions import Fraction
from unittest.mock import Mock

from app.commune import indicator_projector as ip
from app.commune.indicator_context import DatasetContext, IndicatorBuildContext
from app.commune.indicator_manifest import QualifiedManifest, manifest_sha256
from app.commune.indicator_sql import DpeCommuneProjection
from app.commune.indicator_storage import LoadedDecision, PublishedSource
from app.commune.indicators_dpe import CanonicalDpeDocument, calculate_dpe_documents
from app.commune.indicators_dvf import WindowStatistic
from tests.test_commune_indicator_manifest import G1, G2, fixture

NOW = datetime(2026, 9, 9, 14, tzinfo=UTC)
REQUEST = ip.ProjectionRequest(NOW, "synthetic")
CODES = ("44001", "85084", "85165", "85212")


def payload(directory, dataset):
    doc, _ = fixture(directory, G1 if dataset == "dpe" else G2, dataset)
    for name, decision in doc["indicators"].items():
        if name in ip.DPE_PUBLIC or name in ("dvf.count", "dvf.median_eur_m2"):
            decision.update(
                status="qualified",
                rule_version=ip.DPE_PROFILE if dataset == "dpe" else ip.DVF_PROFILE,
            )
    if dataset == "dvf":
        doc["dvf_anchor"] = dict(
            etat="delivery_received_and_stock_bound",
            version_qualification="dvf-delivery-qualification.v2",
            lot_id="synthetic-lot",
            periode_debut_inclus="2024-01-01",
            periode_fin_exclue="2026-01-01",
            departements=["44", "85"],
            acquisition_complete=True,
            revisions_possibles=True,
            exhaustivite_historique="unknown",
            generation_fournisseur=None,
            date_observation="2026-09-09T00:00:00Z",
            generation_stock=str(G2),
            methode_projection="sha256-sorted-row-digests-json.v1",
            **{
                k: ("b" if k == "cog_sha256" else "a") * 64
                for k in (
                    "preuve_qualification_sha256",
                    "preuve_observation_sha256",
                    "preuve_reconstruction_sha256",
                    "snapshot_sha256",
                    "contenu_projete_sha256",
                    "cog_sha256",
                )
            },
        )
    return doc


def entry(doc, registry=1):
    model = QualifiedManifest.model_validate(doc)
    dataset = model.dataset_name
    source = PublishedSource(1, dataset, model.source_generation, ((dataset, 42),), 5)
    digest = manifest_sha256(doc)
    loaded = LoadedDecision(registry, digest, source, model)
    return DatasetContext(
        dataset,
        source,
        loaded,
        None,
        (registry, digest),
        manifest_sha256(model.model_dump(mode="json")),
    )


def context(tmp_path, monkeypatch, dpe=None, dvf=None):
    dpe = payload(tmp_path / "dpe", "dpe") if dpe is None else dpe
    dvf = payload(tmp_path / "dvf", "dvf") if dvf is None else dvf
    ctx = IndicatorBuildContext(
        CODES,
        "b" * 64,
        "synthetic-cog-v1",
        (entry(dpe), entry(dvf)),
        NOW,
        "synthetic",
        object(),
        (),
        None,
        None,
    )
    guard = Mock()
    monkeypatch.setattr(IndicatorBuildContext, "assert_current", guard)
    return ctx, guard


def cells(codes=("44001",), n=5, fraction=Fraction(100, 3)):
    return tuple(
        WindowStatistic(
            code,
            kind,
            period,
            date(start, 1, 1),
            date(end, 1, 1),
            n,
            "empty_sample" if n == 0 else "insufficient_sample" if n < 5 else "value",
            fraction if n >= 5 else None,
        )
        for code in codes
        for kind in ("residentiel_maison", "residentiel_appartement")
        for period, start, end in [("previous", 2024, 2025), ("current", 2025, 2026)]
    )


def dpe_rows(codes=CODES, labels=("A", "A", "G", "G")):
    calc = calculate_dpe_documents(
        tuple(
            CanonicalDpeDocument(
                document_key=str(i),
                energy_class=c,
                building_type=None,
                source_state="unknown",
                regimes=frozenset(),
                is_tombstone=False,
                established_at=None,
                expires_at=None,
                construction_year=None,
                replacement_state="not_checked",
                graph_issues=frozenset(),
                date_issues=frozenset(),
                revision_status="unverified",
            )
            for i, c in enumerate(labels)
        ),
        as_of_date=date(2026, 9, 9),
    )
    return tuple(DpeCommuneProjection(c, calc) for c in codes)


@pytest.fixture
def queries(monkeypatch):
    dvf, dpe = Mock(return_value=cells()), Mock(return_value=dpe_rows())
    monkeypatch.setattr(ip, "query_dvf_indicators", dvf)
    monkeypatch.setattr(ip, "query_dpe_indicators", dpe)
    return dvf, dpe


def test_qualified_models_bindings_and_territorial_absence(
    tmp_path, monkeypatch, queries
):
    ctx, guard = context(tmp_path, monkeypatch)
    result = ip.project_indicator_blocks(None, context=ctx, request=REQUEST)
    assert guard.call_count == 2
    assert [r.code_insee for r in result] == list(CODES)
    assert all(q.call_count == 1 for q in queries)
    assert queries[0].call_args.kwargs["communes"] == ("44001",)
    a = result[0]
    assert a.indicateurs_dvf.residentiel_maison.courante.mediane.valeur == float(
        Fraction(100, 3)
    )
    assert a.indicateurs_dvf.residentiel_maison.variation_pct.etat == "not_qualified"
    assert a.indicateurs_dvf.provenance[0].couverture == "unknown"
    assert a.indicateurs_dpe.resultat.modes == ["A", "G"]
    assert a.indicateurs_dpe.resultat.mediane_ordinale == "A"
    assert len(a.indicateurs_dpe.provenance) == 2
    assert all(
        p.date_import is p.date_snapshot is p.date_publication_source is None
        for p in a.indicateurs_dpe.provenance
    )
    assert "binding" not in str(a.public_dump())
    assert "construction" not in str(a.indicateurs_dpe.model_dump())
    assert len(a.dpe_binding.executed_versions) == 5
    with pytest.raises(FrozenInstanceError):
        a.dvf_binding.scope = "publication"
    for row in result[1:]:
        assert row.indicateurs_dvf.motif == "territorial_attribution_unqualified"
        assert row.dvf_binding.executed_versions == ()
        assert row.indicateurs_dpe.resultat.population.total == 4


def test_trend_closed_rule_uses_unrounded_medians_and_exact_reserve(
    tmp_path, monkeypatch, queries
):
    doc = payload(tmp_path / "trend", "dvf")
    doc["indicators"]["dvf.trend_pct"].update(
        status="qualified",
        rule_version="dvf-canonical-window-median-change.v1",
    )
    ctx, _ = context(tmp_path, monkeypatch, dvf=doc)
    rows = list(cells(fraction=Fraction(100, 3)))
    rows = [
        replace(row, median_eur_m2=Fraction(101, 3)) if row.period == "current" else row
        for row in rows
    ]
    queries[0].return_value = tuple(rows)
    result = ip.project_indicator_blocks(None, context=ctx, request=REQUEST)[0]
    group = result.indicateurs_dvf.residentiel_maison
    assert group.variation_pct.valeur == pytest.approx(1.0)
    assert group.qualification_tendance == (
        "Évolution descriptive des médianes observées ; ce n'est pas un indice à "
        "qualité constante. La composition des ventes peut varier entre les fenêtres "
        "et les données peuvent être révisées."
    )
    assert ("dvf.trend_pct", "dvf-canonical-window-median-change.v1") in (
        result.dvf_binding.executed_versions
    )


@pytest.mark.parametrize(
    "previous_n,current_n,expected",
    [(4, 5, "insufficient_sample"), (5, 4, "insufficient_sample")],
)
def test_trend_preserves_median_absence(
    tmp_path, monkeypatch, queries, previous_n, current_n, expected
):
    doc = payload(tmp_path / "trend-absence", "dvf")
    doc["indicators"]["dvf.trend_pct"].update(
        status="qualified", rule_version=ip.DVF_TREND_PROFILE
    )
    ctx, _ = context(tmp_path, monkeypatch, dvf=doc)
    queries[0].return_value = tuple(
        replace(
            row,
            count=previous_n if row.period == "previous" else current_n,
            median_state="value"
            if (previous_n if row.period == "previous" else current_n) >= 5
            else "insufficient_sample",
            median_eur_m2=Fraction(100, 3)
            if (previous_n if row.period == "previous" else current_n) >= 5
            else None,
        )
        for row in cells()
    )
    group = ip.project_indicator_blocks(None, context=ctx, request=REQUEST)[
        0
    ].indicateurs_dvf.residentiel_maison
    assert group.variation_pct.etat == expected
    assert group.qualification_tendance is None


@pytest.mark.parametrize("rule", [None, "latest", ip.DVF_PROFILE])
def test_trend_unknown_or_wrong_rule_never_executes(
    tmp_path, monkeypatch, queries, rule
):
    doc = payload(tmp_path / "trend-refused", "dvf")
    if rule is not None:
        doc["indicators"]["dvf.trend_pct"].update(status="qualified", rule_version=rule)
    ctx, _ = context(tmp_path, monkeypatch, dvf=doc)
    group = ip.project_indicator_blocks(None, context=ctx, request=REQUEST)[
        0
    ].indicateurs_dvf.residentiel_maison
    assert group.variation_pct.etat == "not_qualified"
    assert group.qualification_tendance is None


@pytest.mark.parametrize(
    "n,state",
    [
        (0, "not_reported"),
        (1, "insufficient_sample"),
        (4, "insufficient_sample"),
        (5, "value"),
        (6, "value"),
    ],
)
def test_median_gate_zero_is_count_not_absence(
    tmp_path, monkeypatch, queries, n, state
):
    ctx, _ = context(tmp_path, monkeypatch)
    queries[0].return_value = cells(n=n)
    block = ip.project_indicator_blocks(None, context=ctx, request=REQUEST)[
        0
    ].indicateurs_dvf
    cell = block.residentiel_maison.courante
    assert cell.effectif == n and cell.mediane.etat == state
    assert ("valeur" in cell.mediane.model_dump()) == (n >= 5)


@pytest.mark.parametrize(
    "dataset,concept",
    [("dvf", "dvf.count"), ("dvf", "dvf.median_eur_m2"), ("dpe", "dpe.mode")],
)
@pytest.mark.parametrize(
    "status", ["pending", "no_go", "unknown", "unsupported", "mixed"]
)
def test_decision_priority_and_closed_profile(
    tmp_path, monkeypatch, queries, dataset, concept, status
):
    doc = payload(tmp_path / "changed", dataset)
    item = doc["indicators"][concept]
    if status in ("unsupported", "mixed"):
        item["rule_version"] = (
            "latest"
            if status == "unsupported"
            else (ip.DVF_PROFILE if dataset == "dpe" else ip.DPE_PROFILE)
        )
    else:
        item.update(status=status, rule_version=None)
    item["reason_code"] = "PRIVATE_SENTINEL_1847"
    ctx, _ = context(tmp_path, monkeypatch, **{dataset: doc})
    row = ip.project_indicator_blocks(None, context=ctx, request=REQUEST)[0]
    if concept == "dvf.median_eur_m2":
        assert row.indicateurs_dvf.residentiel_maison.courante.effectif == 5
        assert (
            row.indicateurs_dvf.residentiel_maison.courante.mediane.etat
            == "not_qualified"
        )
    else:
        assert getattr(row, "indicateurs_" + dataset).etat == "not_qualified"
        assert queries[0 if dataset == "dvf" else 1].call_count == 0
    assert "PRIVATE_SENTINEL" not in str(row.public_dump())
    assert "100/3" not in repr(row.dvf_binding)


@pytest.mark.parametrize("dataset", ["dvf", "dpe"])
@pytest.mark.parametrize("change", ["missing", "duplicate", "extra", "date", "invalid"])
def test_impossible_sql_is_technical(tmp_path, monkeypatch, queries, dataset, change):
    ctx, _ = context(tmp_path, monkeypatch)
    query = queries[0 if dataset == "dvf" else 1]
    rows = query.return_value
    if change == "missing":
        rows = rows[:-1]
    elif change == "duplicate":
        rows = (*rows, rows[0])
    elif change == "extra":
        rows = (*rows, replace(rows[0], commune="99999"))
    elif change == "date":
        rows = (
            replace(rows[0], start_inclusive=date(2023, 1, 1))
            if dataset == "dvf"
            else replace(
                rows[0],
                calculation=replace(rows[0].calculation, as_of_date=date(2020, 1, 1)),
            ),
            *rows[1:],
        )
    elif dataset == "dvf":
        rows = (replace(rows[0], count=True), *rows[1:])
    else:
        rows[0].calculation.result.population.total = 12345
    query.return_value = rows
    with pytest.raises(ValueError):
        ip.project_indicator_blocks(None, context=ctx, request=REQUEST)


@pytest.mark.parametrize("labels", [(), (None, None)])
def test_documentary_zero_and_unknown_are_not_invented_percentages(
    tmp_path, monkeypatch, queries, labels
):
    ctx, _ = context(tmp_path, monkeypatch)
    queries[1].return_value = dpe_rows(labels=labels)
    r = ip.project_indicator_blocks(None, context=ctx, request=REQUEST)[
        0
    ].indicateurs_dpe.resultat
    assert (
        r.population.total == len(labels) and r.part_fg_pct.etat == "zero_denominator"
    )
    assert r.modes == [] and r.mediane_ordinale is None


@pytest.mark.parametrize(
    "fault",
    [
        "scope",
        "unsupported_scope",
        "date",
        "manifest_mutated",
        "result_mutated",
        "final_guard",
    ],
)
def test_boundary_refusals(tmp_path, monkeypatch, queries, fault):
    ctx, guard = context(tmp_path, monkeypatch)
    req = REQUEST
    if fault == "scope":
        req = ip.ProjectionRequest(NOW, "publication")
    if fault == "unsupported_scope":
        ctx = replace(ctx, scope="preproduction")
        req = ip.ProjectionRequest(NOW, "preproduction")
    if fault == "date":
        req = ip.ProjectionRequest(NOW.replace(hour=13), "synthetic")
    if fault == "manifest_mutated":
        object.__setattr__(ctx.datasets[0].decision.manifest, "scope", "publication")
    if fault == "result_mutated":
        queries[1].return_value[0].calculation.result.modes = ["G"]
    if fault == "final_guard":
        guard.side_effect = [None, ManifestRefused("owner_transaction_changed")]
    with pytest.raises(ValueError):
        ip.project_indicator_blocks(None, context=ctx, request=req)


def test_sql_absence_reason_cannot_leak_private_text(tmp_path, monkeypatch, queries):
    ctx, _ = context(tmp_path, monkeypatch)
    queries[1].return_value = dpe_rows(labels=(None,))
    for row in queries[1].return_value:
        row.calculation.result.part_fg_pct.motif = "PRIVATE_SENTINEL_1847"
    rows = ip.project_indicator_blocks(None, context=ctx, request=REQUEST)
    assert "PRIVATE_SENTINEL" not in str([r.public_dump() for r in rows])


@pytest.mark.parametrize("dataset", ["dvf", "dpe"])
def test_public_dump_rejects_subclasses_with_private_fields(
    tmp_path, monkeypatch, queries, dataset
):
    from app.commune.indicator_schemas import IndicateursDpe, IndicateursDvf

    base = IndicateursDpe if dataset == "dpe" else IndicateursDvf

    class Extended(base):
        private_value: str

    ctx, _ = context(tmp_path, monkeypatch)
    row = ip.project_indicator_blocks(None, context=ctx, request=REQUEST)[0]
    block = getattr(row, "indicateurs_" + dataset)
    extended = Extended.model_validate(
        block.model_dump() | {"private_value": "PRIVATE_SENTINEL_1847"}
    )
    forged = replace(row, **{"indicateurs_" + dataset: extended})
    with pytest.raises(ValueError):
        forged.public_dump()


def test_absence_without_source_never_invents_binding_facts(
    tmp_path, monkeypatch, queries
):
    ctx, _ = context(tmp_path, monkeypatch)
    ctx = replace(
        ctx,
        datasets=tuple(
            DatasetContext(d, None, None, "PRIVATE_SENTINEL_1847", None)
            for d in ("dpe", "dvf")
        ),
    )
    rows = ip.project_indicator_blocks(None, context=ctx, request=REQUEST)
    assert all(q.call_count == 0 for q in queries)
    binding = rows[0].dpe_binding
    assert (
        binding.publication_id
        is binding.source_generation
        is binding.registry_id
        is binding.snapshot_sha256
        is None
    )
    assert binding.relation_oids == binding.executed_versions == ()
    assert rows[0].indicateurs_dpe.model_dump() == {
        "etat": "not_qualified",
        "motif": "source_generation_unqualified",
    }
    assert "PRIVATE_SENTINEL" not in str(rows[0].public_dump())


def test_unsupported_quantile_and_trend_do_not_enable_values_or_revoke_core(
    tmp_path, monkeypatch, queries
):
    doc = payload(tmp_path / "changed", "dvf")
    for concept in ("dvf.p10_p90_eur_m2", "dvf.trend_pct"):
        doc["indicators"][concept].update(
            status="qualified", rule_version="unsupported-v1"
        )
    ctx, _ = context(tmp_path, monkeypatch, dvf=doc)
    block = ip.project_indicator_blocks(None, context=ctx, request=REQUEST)[
        0
    ].indicateurs_dvf
    assert block.residentiel_maison.courante.mediane.etat == "value"
    assert block.residentiel_maison.courante.quantiles.etat == "not_qualified"
    assert block.residentiel_maison.variation_pct.etat == "not_qualified"
    assert block.residentiel_maison.qualification_tendance is None


def test_each_exact_fraction_crosses_the_public_float_boundary_once(
    tmp_path, monkeypatch, queries
):
    conversions = []

    class CountedFraction(Fraction):
        def __float__(self):
            conversions.append(1)
            return super().__float__()

    ctx, _ = context(tmp_path, monkeypatch)
    exact = CountedFraction(999999999999, 100 * 2147483647)
    queries[0].return_value = cells(fraction=exact)
    rows = ip.project_indicator_blocks(None, context=ctx, request=REQUEST)
    assert len(conversions) == 4
    assert rows[0].indicateurs_dvf.residentiel_maison.courante.mediane.valeur == float(
        Fraction(999999999999, 100 * 2147483647)
    )


def test_forged_request_is_revalidated_before_sql(tmp_path, monkeypatch, queries):
    ctx, _ = context(tmp_path, monkeypatch)
    req = ip.ProjectionRequest(NOW, "synthetic")
    object.__setattr__(req, "calculated_at", NOW.replace(tzinfo=None))
    with pytest.raises(ManifestRefused):
        ip.project_indicator_blocks(None, context=ctx, request=req)
    assert all(q.call_count == 0 for q in queries)


@pytest.mark.parametrize("location", ["top", "median", "quantiles", "trend", "fg"])
def test_public_dump_closes_mutated_absence_reasons(
    tmp_path, monkeypatch, queries, location
):
    ctx, _ = context(tmp_path, monkeypatch)
    queries[0].return_value = cells(n=4)
    queries[1].return_value = dpe_rows(labels=(None,))
    rows = ip.project_indicator_blocks(None, context=ctx, request=REQUEST)
    row = rows[1] if location == "top" else rows[0]
    absence = {
        "top": rows[1].indicateurs_dvf,
        "median": rows[0].indicateurs_dvf.residentiel_maison.courante.mediane,
        "quantiles": rows[0].indicateurs_dvf.residentiel_maison.courante.quantiles,
        "trend": rows[0].indicateurs_dvf.residentiel_maison.variation_pct,
        "fg": rows[0].indicateurs_dpe.resultat.part_fg_pct,
    }[location]
    absence.motif = "PRIVATE_SENTINEL_1847"
    with pytest.raises(ValueError):
        row.public_dump()


@pytest.mark.parametrize("uuid_case", [False, True])
def test_equivalent_timestamp_spelling_keeps_valid_registry_identity(
    tmp_path, monkeypatch, queries, uuid_case
):
    doc = payload(tmp_path / "changed", "dpe")
    if uuid_case:
        doc["source_generation"] = "ABCDEFAB-CDEF-4ABC-8ABC-ABCDEFABCDEF"
    doc["source_observed_at"] = "2026-09-09T00:00:00+00:00"
    doc["qualified_at"] = "2026-09-09T12:00:00+00:00"
    ctx, _ = context(tmp_path, monkeypatch, dpe=doc)
    row = ip.project_indicator_blocks(None, context=ctx, request=REQUEST)[0]
    assert row.indicateurs_dpe.resultat.etat == "value"


def test_semantically_valid_model_mutation_is_not_a_new_review(
    tmp_path, monkeypatch, queries
):
    ctx, _ = context(tmp_path, monkeypatch)
    manifest = ctx.datasets[0].decision.manifest
    changed = dict(manifest.indicators)
    changed["dpe.mode"] = changed["dpe.mode"].model_copy(
        update={"rule_version": "unknown-v2"}
    )
    object.__setattr__(manifest, "indicators", changed)
    with pytest.raises(ManifestRefused, match="invalid_projection_decision"):
        ip.project_indicator_blocks(None, context=ctx, request=REQUEST)
    assert all(q.call_count == 0 for q in queries)


def test_loaded_model_changed_during_projection_cannot_change_binding(
    tmp_path, monkeypatch, queries
):
    ctx, _ = context(tmp_path, monkeypatch)
    original = queries[1].return_value

    def change(**kwargs):
        object.__setattr__(
            ctx.datasets[0].decision.manifest, "source_snapshot_sha256", "f" * 64
        )
        return original

    queries[1].side_effect = lambda *args, **kwargs: change(**kwargs)
    with pytest.raises(ManifestRefused, match="invalid_projection_decision"):
        ip.project_indicator_blocks(None, context=ctx, request=REQUEST)
