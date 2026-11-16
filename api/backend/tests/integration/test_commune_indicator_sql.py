"""Real disposable PostGIS; temporary source projections, no published objects."""

from datetime import UTC, date, datetime
from decimal import Decimal
from fractions import Fraction
from time import perf_counter

import pytest
from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from app.commune.indicator_sql import (
    IndicatorSqlRefused,
    query_dpe_indicators,
    query_dvf_indicators,
)
from app.commune.indicators_dpe import CanonicalDpeDocument, calculate_dpe_documents
from app.commune.indicators_dvf import CanonicalMutation, CanonicalStock, calculate_dvf
from tests.oracles.commune_indicators import ordinal_summary

ASOF = date(2026, 9, 9)
OBSERVED = datetime(2026, 9, 9, tzinfo=UTC)
COG = ("44001", "85001")
# Received departements of the synthetic lot (v2 reading rule: 44 and 85).
LOT = ("44", "85")


@pytest.fixture
def source(pg_conn):
    assert pg_conn.execute("SELECT PostGIS_Version()").fetchone()[0]
    pg_conn.execute("SET LOCAL statement_timeout='5s'")
    pg_conn.execute("SET LOCAL lock_timeout='250ms'")
    pg_conn.execute("""CREATE TEMP TABLE diagnostics_dpe (
        numero_dpe text, code_insee text, etiquette_dpe text, type_batiment text,
        source_state text, regime_dpe text, source_observations jsonb,
        is_tombstone boolean, date_etablissement_dpe date, date_fin_validite_dpe date,
        annee_construction integer, lifecycle_facts jsonb, revision_evidence jsonb
    ) ON COMMIT DROP""")
    pg_conn.execute("""CREATE TEMP TABLE mutations_grain (
        id_mutation text, code_departement text, code_commune text,
        date_mutation date, classe_mutation text, mono_local boolean,
        est_vente boolean, hors_stats_vefa boolean, nb_communes integer,
        surface_bati_totale integer, valeur_fonciere numeric(15,2)
    ) ON COMMIT DROP""")
    yield pg_conn


def insert(conn, table, rows):
    for row in rows:
        columns = list(row)
        conn.execute(
            sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(table),
                sql.SQL(",").join(map(sql.Identifier, columns)),
                sql.SQL(",").join(sql.Placeholder() for _ in columns),
            ),
            [
                Jsonb(row[k]) if isinstance(row[k], (dict, list)) else row[k]
                for k in columns
            ],
        )


def dpe(key, label=None, **changes):
    return (
        dict(
            numero_dpe=str(key),
            code_insee="44001",
            etiquette_dpe=label,
            type_batiment=None,
            source_state="unknown",
            regime_dpe=None,
            source_observations=[],
            is_tombstone=False,
            date_etablissement_dpe=None,
            date_fin_validite_dpe=None,
            annee_construction=None,
            lifecycle_facts=dict(
                replacement_state="not_checked", graph_issues=[], date_issues=[]
            ),
            revision_evidence={"status": "unverified"},
        )
        | changes
    )


def canonical_dpe(row):
    slugs = {x["source_slug"] for x in row["source_observations"]}
    regimes = {
        name
        for name, slug in [("existant", "dpe03existant"), ("neuf", "dpe02neuf")]
        if slug in slugs
    }
    if not regimes and row["regime_dpe"] in {"existant", "neuf"}:
        regimes = {row["regime_dpe"]}
    return CanonicalDpeDocument(
        document_key=row["numero_dpe"],
        energy_class=row["etiquette_dpe"],
        building_type=row["type_batiment"],
        source_state=row["source_state"],
        regimes=regimes,
        is_tombstone=row["is_tombstone"],
        established_at=row["date_etablissement_dpe"],
        expires_at=row["date_fin_validite_dpe"],
        construction_year=row["annee_construction"],
        **row["lifecycle_facts"],
        revision_status=row["revision_evidence"]["status"],
    )


def mutation(key, **changes):
    return (
        dict(
            id_mutation=str(key),
            code_departement="44",
            code_commune="44001",
            date_mutation=date(2025, 1, 1),
            classe_mutation="residentiel_maison",
            mono_local=True,
            est_vente=True,
            hors_stats_vefa=False,
            nb_communes=1,
            surface_bati_totale=100,
            valeur_fonciere=Decimal("200000.00"),
        )
        | changes
    )


def canonical_dvf(rows):
    return CanonicalStock(
        input_contract="mutations_grain.complete-reconstruction.v1",
        source_generation=None,
        source_snapshot_sha256="a" * 64,
        reconstruction_sha256="b" * 64,
        mutations=tuple(
            CanonicalMutation(
                **(
                    r
                    | {
                        "surface_bati_totale": Decimal(r["surface_bati_totale"])
                        if r["surface_bati_totale"] is not None
                        else None
                    }
                )
            )
            for r in rows
        ),
    )


def test_real_dpe_distribution_and_construction_with_zero_reference(
    source, record_property
):
    rows = [
        dpe(i, label, annee_construction=[1900, None, 2000, 2020][i % 4])
        for i, label in enumerate("AAAADDDGG")
    ]
    rows.append(dpe("unknown", None, annee_construction=1999))
    insert(source, "diagnostics_dpe", rows)
    started = perf_counter()
    actual = query_dpe_indicators(source, communes=COG, as_of_date=ASOF)
    record_property("dpe_sql_seconds", perf_counter() - started)
    assert tuple(x.commune for x in actual) == COG
    assert actual[0].calculation == calculate_dpe_documents(
        map(canonical_dpe, rows), as_of_date=ASOF
    )
    assert actual[1].calculation.result.population.total == 0
    oracle = ordinal_summary([r["etiquette_dpe"] for r in rows])
    assert actual[0].calculation.result.modes == oracle["modes"] == ["A"]
    assert actual[0].calculation.result.mediane_ordinale == oracle["median"] == "D"
    assert actual[0].calculation.construction_known == 8
    assert actual[0].calculation.construction_median == 1999


def test_real_dvf_windows_n4_n5_and_exact_rational_median(source, record_property):
    rows = [
        mutation(i, surface_bati_totale=3, valeur_fonciere=Decimal(i + 1))
        for i in range(5)
    ]
    rows += [
        mutation("previous-" + str(i), date_mutation=date(2024, 1, 1)) for i in range(4)
    ]
    rows += [
        mutation("outside-end", date_mutation=date(2026, 1, 1)),
        mutation("outside-start", date_mutation=date(2023, 12, 31)),
    ]
    insert(source, "mutations_grain", rows)
    started = perf_counter()
    actual = query_dvf_indicators(
        source,
        communes=COG,
        departments=LOT,
        end_exclusive=date(2026, 1, 1),
        observed_at=OBSERVED,
    )
    record_property("dvf_sql_seconds", perf_counter() - started)
    expected = calculate_dvf(
        canonical_dvf(rows),
        communes=COG,
        departments=LOT,
        end_exclusive=date(2026, 1, 1),
        observed_at=OBSERVED,
    )
    assert actual == expected.cells
    assert actual[0].count == 4 and actual[0].median_eur_m2 is None
    assert actual[1].count == 5 and actual[1].median_eur_m2 == Fraction(1)
    assert len(actual) == 8
    assert all(x.quantiles_status == "pending" for x in actual)


def test_close_quotients_need_bounded_precision(source):
    pairs = [("21474836.45", 2147483646), ("21474836.46", 2147483647)]
    rows = [
        mutation(
            i, valeur_fonciere=Decimal(pairs[k][0]), surface_bati_totale=pairs[k][1]
        )
        for i, k in enumerate([0, 0, 1, 0, 1])
    ]
    insert(source, "mutations_grain", rows)
    b, a = [Fraction(Decimal(n)) / d for n, d in pairs]
    assert a - b == Fraction(1, 100 * 2147483647 * 2147483646)
    naive = source.execute("""SELECT valeur_fonciere,surface_bati_totale
        FROM mutations_grain ORDER BY valeur_fonciere/surface_bati_totale,id_mutation
        OFFSET 2 LIMIT 1""").fetchone()
    # Negative control, reproduced RED separately: the naive ranking is wrong.
    assert Fraction(naive[0]) / naive[1] == a != b
    actual = query_dvf_indicators(
        source,
        communes=COG,
        departments=LOT,
        end_exclusive=date(2026, 1, 1),
        observed_at=OBSERVED,
    )
    assert actual[1].median_eur_m2 == b


@pytest.mark.parametrize(
    "years", [[1900], [1900, 2000], [1900, 2000, 2020], [1900, 2000, 2020, 2025]]
)
def test_dpe_all_modes_construction_discrete_even_odd(source, years):
    rows = [dpe(i, "AG"[i % 2], annee_construction=y) for i, y in enumerate(years)]
    rows.append(dpe("unknown"))
    insert(source, "diagnostics_dpe", rows)
    got = query_dpe_indicators(source, communes=COG, as_of_date=ASOF)[0].calculation
    assert got == calculate_dpe_documents(map(canonical_dpe, rows), as_of_date=ASOF)
    assert got.construction_known == len(years)
    assert got.construction_unknown == 1
    assert got.construction_median == sorted(years)[(len(years) - 1) // 2]
    assert got.result.part_fg_pct.valeur == 100 * (len(years) // 2) / len(years)


def test_dpe_overlapping_json_margins_and_removal_or(source):
    both = [{"source_slug": "dpe03existant"}, {"source_slug": "dpe02neuf"}]
    facts = dict(
        replacement_state="replaced", graph_issues=["fork", "cycle"], date_issues=[]
    )
    rows = [
        dpe(
            "both",
            "F",
            source_state="present",
            source_observations=both,
            lifecycle_facts=facts,
            date_etablissement_dpe=date(2020, 1, 1),
            date_fin_validite_dpe=date(2021, 1, 1),
        ),
        dpe("unknown"),
        dpe("tomb", "A", source_state="present", is_tombstone=True),
        dpe("absent", "B", source_state="absent_from_complete_scope"),
        dpe("deactivated", "C", source_state="explicitly_deactivated"),
        dpe(
            "both-removed",
            "G",
            source_state="explicitly_deactivated",
            is_tombstone=True,
        ),
    ]
    insert(source, "diagnostics_dpe", rows)
    got = query_dpe_indicators(source, communes=COG, as_of_date=ASOF)[0].calculation
    assert got == calculate_dpe_documents(map(canonical_dpe, rows), as_of_date=ASOF)
    assert got.result.population.total == 2 and got.excluded_removed == 4
    assert got.result.population.regimes == {"existant": 1, "neuf": 1, "inconnu": 1}
    assert got.graph_issues["fork"] == got.graph_issues["cycle"] == 1
    assert got.result.part_fg_pct.valeur == 100


def test_dpe_date_priority_and_explicit_asof(source):
    facts = dict(
        replacement_state="no_successor_known", graph_issues=[], date_issues=[]
    )
    dates = [
        (ASOF, ASOF),
        (date(2020, 1, 1), date(2026, 9, 8)),
        (date(2026, 9, 10), None),
        (ASOF, date(2020, 1, 1)),
        (None, ASOF),
    ]
    rows = [
        dpe(
            i,
            date_etablissement_dpe=start,
            date_fin_validite_dpe=end,
            lifecycle_facts=facts,
        )
        for i, (start, end) in enumerate(dates)
    ]
    rows.append(
        dpe("invalid", lifecycle_facts=facts | {"date_issues": ["invalid_end_date"]})
    )
    insert(source, "diagnostics_dpe", rows)
    for asof in (ASOF, date(2026, 9, 10)):
        got = query_dpe_indicators(source, communes=COG, as_of_date=asof)[0].calculation
        assert got == calculate_dpe_documents(map(canonical_dpe, rows), as_of_date=asof)
        assert got.result.part_fg_pct.etat == "zero_denominator"
    first = query_dpe_indicators(source, communes=COG, as_of_date=ASOF)[0].calculation
    assert first.result.population.validite == dict(
        unknown=1,
        expired=1,
        not_expired_by_date=1,
        inconsistent_dates=2,
        future_establishment=1,
    )
    assert first.restricted_date_and_no_successor_known == 1


def test_dpe_identical_duplicates_and_set_order(source):
    one = dpe(
        "same",
        "A",
        lifecycle_facts=dict(
            replacement_state="ambiguous",
            graph_issues=["fork", "cycle"],
            date_issues=[],
        ),
    )
    two = one | {
        "lifecycle_facts": one["lifecycle_facts"]
        | {"graph_issues": ["cycle", "fork", "cycle"]}
    }
    rows = [one, one, two]
    insert(source, "diagnostics_dpe", rows)
    got = query_dpe_indicators(source, communes=COG, as_of_date=ASOF)[0].calculation
    assert got == calculate_dpe_documents(map(canonical_dpe, rows), as_of_date=ASOF)
    assert got.result.population.total == 1 and got.duplicate_inputs == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"source_state": "invented"},
        {"etiquette_dpe": "H"},
        {"revision_evidence": {"status": "verified"}},
        {"source_observations": {}},
        {"source_observations": [{"source_slug": "unknown"}]},
        {"annee_construction": 0},
        {"is_tombstone": None},
        {
            "lifecycle_facts": {
                "replacement_state": "not_checked",
                "graph_issues": ["invented"],
                "date_issues": [],
            }
        },
        {
            "date_etablissement_dpe": ASOF,
            "lifecycle_facts": {
                "replacement_state": "not_checked",
                "graph_issues": [],
                "date_issues": ["invalid_establishment_date"],
            },
        },
    ],
)
def test_dpe_invalid_projection_refused_even_outside_requested_commune(source, changes):
    insert(source, "diagnostics_dpe", [dpe("private", code_insee="85002", **changes)])
    with pytest.raises(
        IndicatorSqlRefused, match=r"^invalid_canonical_source_projection$"
    ):
        query_dpe_indicators(source, communes=COG, as_of_date=ASOF)


@pytest.mark.parametrize("dataset", ["dpe", "dvf"])
def test_conflicting_duplicate_precedes_geography_and_removal(source, dataset):
    if dataset == "dpe":
        one = dpe("private", "A", code_insee="85002", is_tombstone=True)
        insert(source, "diagnostics_dpe", [one, one | {"etiquette_dpe": "G"}])

        def call():
            return query_dpe_indicators(source, communes=COG, as_of_date=ASOF)
    else:
        one = mutation("private", code_commune="44002", hors_stats_vefa=True)
        insert(source, "mutations_grain", [one, one | {"valeur_fonciere": Decimal(1)}])

        def call():
            return query_dvf_indicators(
                source,
                communes=COG,
                departments=LOT,
                end_exclusive=date(2026, 1, 1),
                observed_at=OBSERVED,
            )

    with pytest.raises(
        IndicatorSqlRefused, match=r"^invalid_canonical_source_projection$"
    ):
        call()


def test_dvf_filters_whole_mutations_and_even_exact_median(source):
    rows = [
        mutation(i, valeur_fonciere=Decimal(i + 1), surface_bati_totale=7)
        for i in range(6)
    ]
    exclusions = [
        dict(nb_communes=2),
        dict(hors_stats_vefa=True),
        dict(hors_stats_vefa=None),
        dict(est_vente=False),
        dict(est_vente=None),
        dict(surface_bati_totale=0),
        dict(surface_bati_totale=-1),
        dict(valeur_fonciere=Decimal(0)),
        dict(valeur_fonciere=Decimal(-1)),
        dict(code_commune=None),
        dict(classe_mutation="mixte", mono_local=False),
        dict(classe_mutation="ensemble_residentiel", mono_local=False),
        dict(classe_mutation="commerciale", mono_local=False),
    ]
    rows += [mutation("excluded-" + str(i), **v) for i, v in enumerate(exclusions)]
    rows.append(rows[0])
    insert(source, "mutations_grain", rows)
    got = query_dvf_indicators(
        source,
        communes=COG,
        departments=LOT,
        end_exclusive=date(2026, 1, 1),
        observed_at=OBSERVED,
    )
    assert (
        got
        == calculate_dvf(
            canonical_dvf(rows),
            communes=COG,
            departments=LOT,
            end_exclusive=date(2026, 1, 1),
            observed_at=OBSERVED,
        ).cells
    )
    assert got[1].count == 6 and got[1].median_eur_m2 == Fraction(1, 2)


def test_dvf_physical_boundaries_and_calendar_leap(source):
    rows = [
        mutation(
            i,
            valeur_fonciere=Decimal(n),
            surface_bati_totale=d,
            date_mutation=date(2023, 2, 28),
        )
        for i, (n, d) in enumerate(
            [
                # NUMERIC(15,2) of migration 108: the largest amount stored.
                ("0.01", 2147483647),
                ("9999999999999.99", 1),
                ("9999999999999.99", 2147483647),
                ("0.01", 1),
                ("12345678901", 100),
            ]
        )
    ]
    insert(source, "mutations_grain", rows)
    got = query_dvf_indicators(
        source,
        communes=COG,
        departments=LOT,
        end_exclusive=date(2024, 2, 29),
        observed_at=OBSERVED,
    )
    assert (
        got
        == calculate_dvf(
            canonical_dvf(rows),
            communes=COG,
            departments=LOT,
            end_exclusive=date(2024, 2, 29),
            observed_at=OBSERVED,
        ).cells
    )
    assert got[1].start_inclusive == date(2023, 2, 28)
    ratios = sorted(
        Fraction(r["valeur_fonciere"]) / r["surface_bati_totale"] for r in rows
    )
    assert got[1].median_eur_m2 == ratios[2]


@pytest.mark.parametrize(
    "column,datatype",
    [
        ("valeur_fonciere", "numeric"),
        ("valeur_fonciere", "double precision"),
        # The type of migration 087, before 108: no longer the served type.
        ("valeur_fonciere", "numeric(12,2)"),
        ("valeur_fonciere", "numeric(16,2)"),
        ("surface_bati_totale", "bigint"),
    ],
)
def test_dvf_incompatible_physical_precision_refused(source, column, datatype):
    # Closed test parameters only; alter the disposable temporary projection.
    source.execute(
        sql.SQL("ALTER TABLE mutations_grain ALTER COLUMN {} TYPE " + datatype).format(
            sql.Identifier(column)
        )
    )
    with pytest.raises(
        IndicatorSqlRefused, match=r"^invalid_canonical_source_projection$"
    ):
        query_dvf_indicators(
            source,
            communes=COG,
            departments=LOT,
            end_exclusive=date(2026, 1, 1),
            observed_at=OBSERVED,
        )


def test_dvf_amount_beyond_numeric_15_2_refused(source):
    # Only a wider column can hold it; the projection is refused, not rounded.
    source.execute(
        "ALTER TABLE mutations_grain ALTER COLUMN valeur_fonciere TYPE numeric(16,2)"
    )
    insert(
        source,
        "mutations_grain",
        [mutation("wide", valeur_fonciere=Decimal("10000000000000.00"))],
    )
    with pytest.raises(
        IndicatorSqlRefused, match=r"^invalid_canonical_source_projection$"
    ):
        query_dvf_indicators(
            source,
            communes=COG,
            departments=LOT,
            end_exclusive=date(2026, 1, 1),
            observed_at=OBSERVED,
        )


@pytest.mark.parametrize(
    "keys",
    [
        (),
        ("44001", "44001"),
        ("97501",),
        ("99001",),
        ("44001';--",),
        ["44001"],
    ],
)
def test_explicit_reference_validation_before_connection(keys):
    with pytest.raises(IndicatorSqlRefused, match=r"^invalid_reference_keys$"):
        query_dpe_indicators(None, communes=keys, as_of_date=ASOF)


def test_dvf_communes_and_departments_belong_to_the_received_lot():
    # A hosted commune outside the lot is never read as an empty sample.
    with pytest.raises(IndicatorSqlRefused, match=r"^invalid_reference_keys$"):
        query_dvf_indicators(
            None,
            communes=("44001", "75056"),
            departments=LOT,
            end_exclusive=date(2026, 1, 1),
            observed_at=OBSERVED,
        )
    for lot in ((), ("44", "99"), ("44", "975"), ("85", "44"), ("44", "44")):
        with pytest.raises(IndicatorSqlRefused, match=r"^invalid_lot_departments$"):
            query_dvf_indicators(
                None,
                communes=COG,
                departments=lot,
                end_exclusive=date(2026, 1, 1),
                observed_at=OBSERVED,
            )


def test_connection_and_transaction_remain_caller_owned(source):
    insert(source, "diagnostics_dpe", [dpe("one")])
    transaction = source.execute("SELECT txid_current()").fetchone()[0]
    query_dpe_indicators(source, communes=COG, as_of_date=ASOF)
    query_dvf_indicators(
        source,
        communes=COG,
        departments=LOT,
        end_exclusive=date(2026, 1, 1),
        observed_at=OBSERVED,
    )
    assert source.execute(
        "SELECT txid_current(),count(*) FROM diagnostics_dpe"
    ).fetchone() == (transaction, 1)


@pytest.mark.parametrize(
    "changes",
    [
        {"mono_local": False},
        {"mono_local": None},
        {"classe_mutation": "invented"},
        {"code_departement": "85"},
        # A hosted departement outside the received lot, a 75 commune filed
        # under 44, a collectivity: the whole projection is refused.
        {"code_departement": "75", "code_commune": "75056"},
        {"code_commune": "75101"},
        {"code_departement": "975", "code_commune": "97501"},
        {"code_commune": "44000x"},
        {"nb_communes": -1},
        {"valeur_fonciere": Decimal("NaN")},
        {"id_mutation": "private key"},
    ],
)
def test_dvf_invalid_canonical_projection_refused(source, changes):
    insert(source, "mutations_grain", [mutation("private", **changes)])
    with pytest.raises(
        IndicatorSqlRefused, match=r"^invalid_canonical_source_projection$"
    ):
        query_dvf_indicators(
            source,
            communes=COG,
            departments=LOT,
            end_exclusive=date(2026, 1, 1),
            observed_at=OBSERVED,
        )


@pytest.mark.parametrize(
    "end,observed",
    [
        (date(2027, 1, 1), OBSERVED),
        (date(2026, 1, 1), datetime(2026, 9, 9)),
        (date(2, 1, 1), OBSERVED),
        (OBSERVED, OBSERVED),
    ],
)
def test_period_calendar_and_observation_are_explicit(end, observed):
    with pytest.raises(IndicatorSqlRefused):
        query_dvf_indicators(
            None, communes=COG, departments=LOT, end_exclusive=end, observed_at=observed
        )


def test_dpe_no_known_class_is_not_measured_zero(source):
    insert(source, "diagnostics_dpe", [dpe("one"), dpe("two")])
    cells = query_dpe_indicators(source, communes=COG, as_of_date=ASOF)
    for cell in cells:
        assert cell.calculation.result.part_fg_pct.etat == "zero_denominator"
        assert cell.calculation.result.modes == []
        assert cell.calculation.result.mediane_ordinale is None
    assert cells[0].calculation.result.population.total == 2
    assert cells[1].calculation.result.population.total == 0


@pytest.mark.parametrize(
    "dataset,key",
    [
        ("dpe", "\tsynthetic"),
        ("dpe", "synthetic\n"),
        ("dpe", "\u00a0synthetic"),
        ("dvf", "synthetic\u00a0"),
        ("dvf", "x" * 121),
        ("dvf", "x" * 120),
        ("dvf", "😀" * 120),
        ("dvf", "😀" * 121),
        ("dpe", "internal space"),
        ("dpe", "x" * 121),
    ],
)
def test_identity_domain_matches_canonical_model(source, dataset, key):
    assert_identity_parity(source, dataset, key)


def assert_identity_parity(source, dataset, key):
    row = dpe(key) if dataset == "dpe" else mutation(key)
    accepted = True
    try:
        canonical_dpe(row) if dataset == "dpe" else canonical_dvf([row])
    except ValidationError:
        accepted = False
    table = "diagnostics_dpe" if dataset == "dpe" else "mutations_grain"
    source.execute(sql.SQL("TRUNCATE {}").format(sql.Identifier(table)))
    insert(source, table, [row])
    sql_accepted = True
    try:
        if dataset == "dpe":
            query_dpe_indicators(source, communes=COG, as_of_date=ASOF)
        else:
            query_dvf_indicators(
                source,
                communes=COG,
                departments=LOT,
                end_exclusive=date(2026, 1, 1),
                observed_at=OBSERVED,
            )
    except IndicatorSqlRefused as exc:
        assert str(exc) == "invalid_canonical_source_projection"
        sql_accepted = False
    assert sql_accepted is accepted


def test_all_unicode_whitespace_and_nearby_controls_against_models(source):
    # Enumerate Python's entire Unicode whitespace domain independently from SQL.
    whitespace = [chr(n) for n in range(0x110000) if chr(n).isspace()]
    assert len(whitespace) == 29
    for character in [*whitespace, "\u200b", "\ufeff"]:
        for key in (character + "id", "id" + character, "i" + character + "d"):
            for dataset in ("dpe", "dvf"):
                assert_identity_parity(source, dataset, key)


def test_dpe_source_audit_does_not_rescan_per_reference_key(source, record_property):
    # Observe the executed statement through the public interface, then inspect
    # actual source scans. This bounds repeated work, not wall-clock duration.
    captured = []

    class CaptureConnection:
        def cursor(self, **kwargs):
            cursor = source.cursor(**kwargs)

            class CaptureCursor:
                def __enter__(self):
                    cursor.__enter__()
                    return self

                def __exit__(self, *args):
                    return cursor.__exit__(*args)

                def execute(self, query, params):
                    captured.append((query, params))
                    return cursor.execute(query, params)

            return CaptureCursor()

    keys = tuple(f"44{i:03}" for i in range(20))
    rows = [dpe(i, "F", code_insee=keys[i % len(keys)]) for i in range(200)]
    rows += [
        rows[0],
        dpe("removed", source_state="explicitly_deactivated", code_insee=keys[0]),
    ]
    insert(source, "diagnostics_dpe", rows)
    got = query_dpe_indicators(CaptureConnection(), communes=keys, as_of_date=ASOF)
    for cell in got:
        expected = calculate_dpe_documents(
            map(canonical_dpe, (r for r in rows if r["code_insee"] == cell.commune)),
            as_of_date=ASOF,
        )
        assert cell.calculation == expected
    assert len(captured) == 1
    query, params = captured[0]
    plan = source.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + query, params).fetchone()[
        0
    ][0]

    def nodes(node):
        yield node
        for child in node.get("Plans", []):
            yield from nodes(child)

    loops = [
        n["Actual Loops"]
        for n in nodes(plan["Plan"])
        if n["Node Type"] == "CTE Scan" and n.get("CTE Name") in {"raw", "canonical"}
    ]
    assert loops
    record_property("materialized_source_max_scan_loops", max(loops))
    assert max(loops) <= 1
