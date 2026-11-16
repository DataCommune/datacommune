"""Adversarial fixture oracles for the private measurement instrument."""

import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from scripts import mesure_commune_indicators as measure
from scripts.verify_golden_cases import GoldenSecurityError

pytestmark = pytest.mark.unit


@pytest.fixture
def manifest_file(tmp_path, monkeypatch):
    for key in tuple(measure.os.environ):
        if key.startswith("PG") or key == "DC_MEASURE_DATABASE_URL":
            monkeypatch.delenv(key)
    receipt = tmp_path / "restore-receipt.json"
    receipt.write_text('{"synthetic": true}', encoding="utf-8")
    proof = {"path": receipt.name, "sha256": measure.digest(receipt.read_bytes())}
    payload = {
        "schema_version": "commune-measure-source.v1",
        "target": {
            "kind": "synthetic_disposable",
            "host": "127.0.0.1",
            "port": 55491,
            "database": "dc_measure_fixture",
            "cluster_name": "dc_measure_fixture",
            "restore_receipt": proof,
            "relation_oids": {"mutations_grain": 1, "diagnostics_dpe": 2},
        },
        "snapshot_id": "synthetic-fixture",
        "snapshot_sha256": "a" * 64,
        "cog_version": "synthetic",
        "cog_sha256": "b" * 64,
        "migrations": ["094_publication_generations"],
        "sources": {
            name: {
                "snapshot_sha256": "c" * 64,
                "generation": None,
                "publication_relation_oid": None,
            }
            for name in ("dvf", "dpe")
        },
        "departments": ["44", "85"],
        "window_end_exclusive": "2024-02-29",
        "dpe_as_of": "2026-09-08",
        "watermarks": {"44": None, "85": None},
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def rewrite(path, mutate):
    payload = json.loads(path.read_text())
    mutate(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_calendar_boundaries_are_half_open_and_include_zero_reference_rows():
    communes = [
        {"department": "44", "commune": "44001"},
        {"department": "85", "commune": "85001"},
    ]
    rows = [
        dvf(when=when)
        for when in (
            date(2022, 2, 27),
            date(2022, 2, 28),
            date(2023, 2, 27),
            date(2023, 2, 28),
            date(2024, 2, 28),
            date(2024, 2, 29),
        )
    ]
    result = measure.measure_dvf(communes, rows, date(2024, 2, 29))
    assert measure.windows(date(2024, 2, 29)) == {
        "previous": (date(2022, 2, 28), date(2023, 2, 28)),
        "current": (date(2023, 2, 28), date(2024, 2, 29)),
    }
    assert len(result["matrix"]) == 8
    assert sorted(row["n"] for row in result["matrix"]) == [0, 0, 0, 0, 0, 0, 2, 2]
    assert result["exclusions_overlapping"]["outside_windows"] == 2
    assert all(
        row["quantiles"]["p50"]["value"] is None
        for row in result["matrix"]
        if row["n"] == 0
    )


def dvf(when=date(2024, 1, 1), **changes):
    return {
        "department": "44",
        "commune": "44001",
        "classe_mutation": "residentiel_maison",
        "date_mutation": when,
        "est_vente": True,
        "hors_stats_vefa": False,
        "nb_communes": 1,
        "surface_bati_totale": Decimal(10),
        "valeur_fonciere": Decimal(1000),
        **changes,
    }


def test_multi_commune_extreme_cannot_contaminate_quantiles():
    reference = [{"department": "44", "commune": "44001"}]
    rows = [
        dvf(),
        dvf(nb_communes=2, valeur_fonciere=Decimal(10**12)),
        dvf(hors_stats_vefa=True),
        dvf(surface_bati_totale=None),
        dvf(est_vente=False),
        dvf(classe_mutation="mixte"),
        dvf(commune="44999"),
    ]
    result = measure.measure_dvf(reference, rows, date(2024, 2, 29))
    house = next(
        row
        for row in result["matrix"]
        if row["window"] == "current" and row["type"] == "residentiel_maison"
    )
    assert house["n"] == 1
    assert house["quantiles"]["p90"]["value"] == 100
    assert result["exclusions_overlapping"] == {
        "multi_commune_or_unknown": 1,
        "vefa_or_unknown": 1,
        "surface_nonpositive_or_unknown": 1,
        "not_sale": 1,
        "non_residential": 1,
        "outside_geographic_reference": 1,
    }


def test_quantile_sort_interpolation_and_leave_one_out_have_fixed_oracles():
    result = measure.price_metrics(map(Decimal, [100, 10, 30, 20]))
    assert result["n_distinct"] == 4
    quantiles = result["quantiles"]
    assert [quantiles[name]["value"] for name in ("p10", "p50", "p90")] == [13, 25, 79]
    assert quantiles["p10"]["max_leave_one_out_delta"] == 9
    assert quantiles["p90"]["max_leave_one_out_delta"] == 51
    assert quantiles["p10"]["distance_min"] == 3
    assert quantiles["p90"]["distance_max"] == 21
    assert quantiles["p50"]["equals_observation"] is False


def test_identical_and_rounded_equal_quantiles_are_not_conflated():
    equal = measure.price_metrics([Decimal("100.1")] * 5)
    close = measure.price_metrics([Decimal("100.1"), Decimal("100.2")])
    assert equal["n_distinct"] == 1
    assert equal["quantiles"]["p90"]["value"] == equal["quantiles"]["p10"]["value"]
    assert equal["quantiles"]["p10"]["max_leave_one_out_delta"] == 0
    assert close["quantiles"]["p10"]["value"] == Decimal("100.11")
    assert close["quantiles"]["p90"]["value"] == Decimal("100.19")


def dpe(label="A", **changes):
    return {
        "commune": "44001",
        "etiquette_dpe": label,
        "type_batiment": "maison",
        "regime_dpe": None,
        "date_etablissement_dpe": date(2020, 1, 1),
        "date_fin_validite_dpe": date(2026, 9, 8),
        "annee_construction": None,
        "is_tombstone": False,
        "source_state": "unknown",
        "lifecycle_facts": {
            "replacement_state": "not_checked",
            "date_issues": [],
            "graph_issues": [],
        },
        "revision": "unverified",
        "source_slugs": [],
        **changes,
    }


def test_mode_median_ties_and_unknown_denominator_are_distinct():
    result = measure.dpe_population([dpe(c) for c in "AAAADDDGG"], date(2026, 9, 8))
    assert (result["modes"], result["ordinal_median"], result["n_known_classes"]) == (
        ["A"],
        "D",
        9,
    )
    tied = measure.dpe_population([dpe(c) for c in "GAGA"], date(2026, 9, 8))
    assert (tied["modes"], tied["ordinal_median"]) == (["A", "G"], "A")
    unknown = measure.dpe_population([dpe(None), dpe("X")], date(2026, 9, 8))
    assert unknown["n_documentary"] == unknown["n_unknown_class"] == 2
    assert unknown["modes"] == []
    assert unknown["ordinal_median"] is None and unknown["fg_pct"] is None


def test_two_sources_count_once_expired_replaced_are_retained():
    row = dpe(
        "G",
        source_slugs=["dpe03existant", "dpe02neuf", "dpe03existant"],
        date_fin_validite_dpe=date(2026, 9, 7),
        lifecycle_facts={
            "replacement_state": "replaced",
            "date_issues": [],
            "graph_issues": ["fork"],
        },
    )
    result = measure.dpe_population(
        [row, dpe("A", is_tombstone=True)], date(2026, 9, 8)
    )
    assert result["n_documentary"] == result["n_both_regimes"] == 1
    assert result["axes"]["regimes_overlapping"] == {"existant": 1, "neuf": 1}
    assert result["axes"]["validity"] == {"expired": 1}
    assert result["axes"]["replacement"] == {"replaced": 1}
    assert result["axes"]["graph_issues_overlapping"] == {"fork": 1}
    assert result["n_restricted_date_and_no_successor_known"] == 0


def test_dpe_inclusive_validity_and_construction_have_separate_denominators():
    rows = [
        dpe("A", annee_construction=1980),
        dpe(None, date_etablissement_dpe=None),
        dpe(
            "G",
            date_etablissement_dpe=date(2027, 1, 1),
            date_fin_validite_dpe=date(2028, 1, 1),
        ),
        dpe("B", date_etablissement_dpe=date(2026, 9, 9)),
    ]
    result = measure.dpe_population(rows, date(2026, 9, 8))
    assert result["axes"]["validity"] == {
        "not_expired_by_date": 1,
        "unknown": 1,
        "future_establishment": 1,
        "inconsistent_dates": 1,
    }
    assert (
        result["n_construction_known"] == 1
        and result["construction_ordinal_median"] == 1980
    )
    assert result["n_unknown_establishment"] == 1
    assert result["n_known_classes"] == 3


@pytest.mark.parametrize(
    "mutation",
    [
        lambda x: x["target"].update(host="192.0.2.1"),
        lambda x: x["target"].update(database="datacommune"),
        lambda x: x["target"].update(kind="production"),
        lambda x: x.update(window_end_exclusive=None),
        lambda x: x.update(observed_max_date="2024-02-29"),
        lambda x: x["watermarks"].pop("85"),
        lambda x: x["sources"].pop("dpe"),
        lambda x: x["target"]["restore_receipt"].update(sha256="f" * 64),
        lambda x: x["target"]["restore_receipt"].update(path="../escape.json"),
    ],
)
def test_manifest_refuses_missing_or_conflicting_attestations(manifest_file, mutation):
    rewrite(manifest_file, mutation)
    with pytest.raises(measure.MeasurementError):
        measure.read_manifest(manifest_file)


def test_missing_watermark_stays_unknown_even_with_observed_future_date(manifest_file):
    manifest = measure.read_manifest(manifest_file)
    report = measure.measure_dvf(
        [{"department": "44", "commune": "44001"}],
        [dvf(when=date(2099, 1, 1))],
        manifest.window_end_exclusive,
    )
    assert manifest.watermarks == {"44": None, "85": None}
    assert manifest.window_end_exclusive == date(2024, 2, 29)
    assert report["observed_dates_not_watermarks"]["44"] == {"2099-01-01": 1}
    assert all(row["n"] == 0 for row in report["matrix"])


def test_explicit_dsn_no_fallback_and_no_secret_in_error(
    manifest_file, monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://private:do-not-print@prod/datacommune"
    )
    args = [
        "--dsn-env",
        "DC_MEASURE_DATABASE_URL",
        "--source-manifest",
        str(manifest_file),
        "--output-dir",
        str(tmp_path / "out"),
    ]
    assert measure.main(args) == 1
    assert not (tmp_path / "out").exists()
    assert "do-not-print" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "dsn",
    [
        "host=127.0.0.1 port=55491 dbname=dc_measure_fixture user=test service=prod",
        "host=127.0.0.1 port=55491 dbname=dc_measure_fixture "
        "user=test hostaddr=192.0.2.1",
        "host=127.0.0.1 port=55491 dbname=dc_measure_fixture "
        "user=test options='-c default_transaction_read_only=off'",
        "host=prod port=55491 dbname=dc_measure_fixture user=test",
        "host=127.0.0.1 port=55491 dbname=datacommune user=test",
    ],
)
def test_dsn_cannot_override_isolation(manifest_file, monkeypatch, dsn):
    monkeypatch.setenv("DC_MEASURE_DATABASE_URL", dsn)
    with pytest.raises(measure.MeasurementError):
        measure.connection_parameters(
            "DC_MEASURE_DATABASE_URL", measure.read_manifest(manifest_file)
        )


def test_all_sql_exact_allowlist_rejects_write_before_execution():
    called = []
    conn = SimpleNamespace(execute=lambda *args: called.append(args))
    for sql in measure.SQL:
        assert sql.strip().startswith("SELECT")
        measure.select(conn, sql)
    assert len(called) == len(measure.SQL)
    for sql in (
        "DELETE FROM geo_communes",
        measure.DVF_SQL + "; DROP TABLE mutations_grain",
        "SELECT set_config('transaction_read_only', 'off', true)",
    ):
        with pytest.raises(GoldenSecurityError):
            measure.select(conn, sql)
    assert len(called) == len(measure.SQL)


def database_double(manifest):
    responses = {
        measure.IDENTITY_SQL: [
            {
                "database": manifest.target.database,
                "cluster_name": manifest.target.cluster_name,
                "read_only": "on",
                "isolation": "repeatable read",
                "statement_timeout": "60s",
            }
        ],
        measure.MIGRATIONS_SQL: [{"version_num": "094_publication_generations"}],
        measure.PUBLICATIONS_SQL: [],
        measure.OIDS_SQL: [
            {"name": "mutations_grain", "oid": 1},
            {"name": "diagnostics_dpe", "oid": 2},
        ],
    }

    class Cursor(list):
        def fetchone(self):
            return self[0] if self else None

    return responses, SimpleNamespace(
        execute=lambda sql, params=None: Cursor(responses[sql])
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("cluster_name", "prod"),
        ("database", "prod"),
        ("read_only", "off"),
        ("isolation", "read committed"),
        ("statement_timeout", "0"),
    ],
)
def test_database_identity_is_checked_before_reading_sources(
    manifest_file, field, value
):
    manifest = measure.read_manifest(manifest_file)
    responses, conn = database_double(manifest)
    responses[measure.IDENTITY_SQL][0][field] = value
    with pytest.raises(measure.MeasurementError):
        measure.check_database(conn, manifest)


def test_pending_failed_generation_and_wrong_oid_cannot_be_measured(manifest_file):
    generation = "11111111-1111-4111-8111-111111111111"
    rewrite(
        manifest_file,
        lambda x: x["sources"]["dvf"].update(
            generation=generation, publication_relation_oid=1
        ),
    )
    manifest = measure.read_manifest(manifest_file)
    for status, oid, actual_generation in (
        ("pending", 1, generation),
        ("failed", 1, generation),
        ("published", 9, generation),
        ("published", 1, "other"),
    ):
        responses, conn = database_double(manifest)
        responses[measure.PUBLICATIONS_SQL] = [
            {
                "dataset_name": "dvf",
                "generation": actual_generation,
                "status": status,
                "relation_oids": {"mutations_grain": oid},
            }
        ]
        with pytest.raises(measure.MeasurementError):
            measure.check_database(conn, manifest)


def test_existing_output_and_missing_manifest_refused_before_connect(
    manifest_file, monkeypatch, tmp_path
):
    called = []
    monkeypatch.setattr(measure, "run", lambda *args: called.append(args))
    for path, out in (
        (manifest_file, tmp_path),
        (tmp_path / "missing.json", tmp_path / "new"),
    ):
        assert (
            measure.main(
                [
                    "--dsn-env",
                    "DC_MEASURE_DATABASE_URL",
                    "--source-manifest",
                    str(path),
                    "--output-dir",
                    str(out),
                ]
            )
            == 1
        )
    assert called == []


def test_successful_measurement_still_has_no_publication_policy(
    manifest_file, monkeypatch, tmp_path
):
    monkeypatch.setenv(
        "DC_MEASURE_DATABASE_URL",
        "host=127.0.0.1 port=55491 dbname=dc_measure_fixture user=test",
    )
    monkeypatch.setattr(measure, "run", lambda *args: {"dvf": {}, "dpe": {}})
    output = tmp_path / "results"
    assert (
        measure.main(
            [
                "--dsn-env",
                "DC_MEASURE_DATABASE_URL",
                "--source-manifest",
                str(manifest_file),
                "--output-dir",
                str(output),
            ]
        )
        == 0
    )
    report = json.loads((output / "59.1-MEASURES.json").read_text(encoding="utf-8"))
    assert report["publication_decision"] == "pending_independent_review"
    assert report["window_coverage"] == {"44": "unknown", "85": "unknown"}
    assert "target" not in report["source"]
    assert "quantile disclosure and trend policy" in report["pending"]


def test_trend_uses_unrounded_medians_and_never_fills_missing_previous():
    reference = [{"department": "44", "commune": "44001"}]
    rows = [
        dvf(when=date(2022, 6, 1), valeur_fonciere=Decimal("1001")),
        dvf(valeur_fonciere=Decimal("1002")),
    ]
    report = measure.measure_dvf(reference, rows, date(2024, 2, 29))
    house = next(
        row for row in report["comparisons"] if row["type"] == "residentiel_maison"
    )
    apartment = next(
        row for row in report["comparisons"] if row["type"] == "residentiel_appartement"
    )
    assert (
        house["unrounded_median_change_pct"]
        == (Decimal("100.2") / Decimal("100.1") - 1) * 100
    )
    assert house["unrounded_median_change_pct"] != 0
    assert house["publication_policy"] == "pending"
    assert apartment["unrounded_median_change_pct"] is None


def test_dpe_communal_matrix_keeps_zero_and_does_not_mix_departments():
    reference = [
        {"department": "44", "commune": "44001"},
        {"department": "85", "commune": "85001"},
    ]
    report = measure.measure_dpe(
        reference, [dpe("G"), dpe("A", commune="44999")], date(2026, 9, 8)
    )
    assert [row["n_documentary"] for row in report["matrix"]] == [1, 0]
    assert report["matrix"][1]["fg_pct"] is None
    assert report["n_outside_geographic_reference"] == 1


def test_libpq_environment_and_too_early_watermark_are_refused(
    manifest_file, monkeypatch
):
    monkeypatch.setenv(
        "DC_MEASURE_DATABASE_URL",
        "host=127.0.0.1 port=55491 dbname=dc_measure_fixture user=test",
    )
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.1")
    with pytest.raises(measure.MeasurementError):
        measure.connection_parameters(
            "DC_MEASURE_DATABASE_URL", measure.read_manifest(manifest_file)
        )
    rewrite(
        manifest_file,
        lambda x: x["watermarks"].update(
            {
                "44": {
                    "complete_before": "2024-02-28",
                    "evidence": x["target"]["restore_receipt"],
                }
            }
        ),
    )
    with pytest.raises(measure.MeasurementError):
        measure.read_manifest(manifest_file)


def test_logical_restore_mapping_preserves_source_oid_but_checks_target_oid(
    manifest_file,
):
    generation = "11111111-1111-4111-8111-111111111111"
    rewrite(
        manifest_file,
        lambda x: x["sources"]["dvf"].update(
            generation=generation, publication_relation_oid=991
        ),
    )
    manifest = measure.read_manifest(manifest_file)
    responses, conn = database_double(manifest)
    responses[measure.PUBLICATIONS_SQL] = [
        {
            "dataset_name": "dvf",
            "generation": generation,
            "status": "published",
            "relation_oids": {"mutations_grain": 991},
        }
    ]
    assert measure.check_database(conn, manifest)["oids"]["mutations_grain"] == 1
    responses[measure.OIDS_SQL][0]["oid"] = 3
    with pytest.raises(measure.MeasurementError):
        measure.check_database(conn, manifest)


def test_postgresql_minute_timeout_format_is_equivalent(manifest_file):
    manifest = measure.read_manifest(manifest_file)
    responses, conn = database_double(manifest)
    responses[measure.IDENTITY_SQL][0]["statement_timeout"] = "1min"
    assert measure.check_database(conn, manifest)["identity"]["read_only"] == "on"
