"""Synthetic complete snapshots exercise raw presence and conservative retirement."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.etl_dpe.config import (
    EXPECTED_COLUMNS,
    SOURCE_FIELD_FORMATS,
    SOURCE_FIELD_TYPES,
)
from app.etl_dpe.reconciliation import (
    DpePresenceRegistry,
    _previous_observations,
    collect_presence,
    reconcile_observations,
)
from app.etl_dpe.sources import (
    DPE_SOURCES,
    MAPPING_VERSION,
    DpeArtifact,
    DpeImportManifest,
    DpePageProof,
    DpeSourceFile,
    csv_record,
    csv_value,
    decode_json,
    expected_pairs,
    sha256_file,
    source_version,
)
from app.scope.model import DEPARTEMENTS_HEBERGEABLES

# The 44/85 generation these fixtures were written for (any scope works).
PAIRS_44_85 = expected_pairs(("44", "85"))

EXISTING = ("dpe03existant", "44")
NEW = ("dpe02neuf", "44")
EXISTING_971 = ("dpe03existant", "971")
NOW = "2026-09-05T10:00:00Z"
LATER = "2026-09-06T11:00:00Z"


def raw(number="fixture-one", **changes):
    row = dict.fromkeys(EXPECTED_COLUMNS)
    row.update(numero_dpe=number, code_departement_ban="44", version_dpe=2.6)
    row.update(changes)
    return row


def manifest_at(
    tmp_path,
    rows=None,
    *,
    generation="first",
    observed_at=NOW,
    departements=("44", "85"),
):
    directory = tmp_path / generation
    directory.mkdir()
    files = []
    for slug, department in sorted(expected_pairs(departements)):
        descriptor = DPE_SOURCES[slug]
        prefix = f"{slug}-{department}"
        metadata = {
            "id": descriptor.dataset_id,
            "slug": slug,
            "href": descriptor.endpoint.removesuffix("/lines"),
            "dataUpdatedAt": observed_at,
            "schema": [
                {
                    "key": key,
                    "type": SOURCE_FIELD_TYPES[key],
                    **(
                        {"format": SOURCE_FIELD_FORMATS[key]}
                        if key in SOURCE_FIELD_FORMATS
                        else {}
                    ),
                }
                for key in EXPECTED_COLUMNS
            ],
        }
        meta_path = directory / f"{prefix}.metadata.json"
        meta_path.write_text(json.dumps(metadata), encoding="utf-8")
        artifact = DpeArtifact(str(meta_path), sha256_file(meta_path))
        selected = (rows or {}).get((slug, department), [])
        page_path = directory / f"{prefix}.page.json"
        page_path.write_text(
            json.dumps({"total": len(selected), "results": selected}), encoding="utf-8"
        )
        typed = decode_json(page_path.read_text(encoding="utf-8"))["results"]
        csv_path = directory / f"{prefix}.csv"
        csv_path.write_bytes(
            csv_record(EXPECTED_COLUMNS)
            + b"".join(
                csv_record(csv_value(row.get(key)) for key in EXPECTED_COLUMNS)
                for row in typed
            )
        )
        version = source_version(decode_json(meta_path.read_text()), descriptor)
        files.append(
            DpeSourceFile(
                source_slug=slug,
                department=department,
                import_id=generation,
                path=str(csv_path),
                sha256=sha256_file(csv_path),
                endpoint=descriptor.endpoint,
                filter=descriptor.filter_for(department),
                columns=tuple(EXPECTED_COLUMNS),
                mapping_version=MAPPING_VERSION,
                version_start=version,
                version_end=version,
                initial_total=len(selected),
                final_total=len(selected),
                rows=len(selected),
                pagination_complete=True,
                observed_at=observed_at,
                metadata_start=artifact,
                metadata_end=artifact,
                pages=(
                    DpePageProof(
                        str(page_path),
                        sha256_file(page_path),
                        len(selected),
                        None,
                        None,
                    ),
                ),
            )
        )
    return DpeImportManifest(generation, str(directory), tuple(files))


def payload(number="fixture-one", **changes):
    result = {
        "numero_dpe": number,
        "code_departement": "44",
        "code_insee": "44109",
        "identifiant_ban": "fixture-ban",
        "regime_dpe": "existant",
        "etiquette_dpe": "C",
        "surface_habitable": Decimal("90.1"),
        "date_etablissement_dpe": None,
        "date_fin_validite_dpe": None,
        "numero_dpe_remplace": None,
        "geom": "fixture-position",
    }
    result.update(changes)
    return result


def reconcile(previous, incoming, manifest, **kwargs):
    return list(reconcile_observations(previous, incoming, manifest, **kwargs))


def test_exact_copies_merge_provenance_without_scalar_winner(tmp_path):
    manifest = manifest_at(tmp_path, {EXISTING: [raw(), raw()], NEW: [raw()]})
    rows = reconcile([], [payload(), payload(regime_dpe="neuf")], manifest)
    assert len(rows) == 1
    assert rows[0]["regime_dpe"] is None
    assert [o["source_slug"] for o in rows[0]["source_observations"]] == [
        "dpe02neuf",
        "dpe03existant",
    ]
    assert rows[0]["source_state"] == "present"
    assert rows[0]["source_snapshot_at"] == NOW


@pytest.mark.parametrize(
    "field,value",
    [
        ("date_reception_dpe", "2026-09-01"),
        ("adresse_brut", " "),
        ("surface_habitable_logement", 1),
        ("version_dpe", "2.6"),
        ("numero_dpe_remplace", "fixture-previous"),
    ],
)
def test_raw_divergence_refuses_even_when_transformed_payload_is_equal(
    tmp_path, field, value
):
    manifest = manifest_at(tmp_path, {EXISTING: [raw()], NEW: [raw(**{field: value})]})
    with pytest.raises(ValueError, match="DPE raw documentary collision"):
        reconcile([], [payload(), payload()], manifest)


def test_distinct_numbers_are_never_deduplicated_by_address(tmp_path):
    manifest = manifest_at(tmp_path, {EXISTING: [raw(), raw("fixture-two")]})
    assert len(reconcile([], [payload(), payload("fixture-two")], manifest)) == 2


@pytest.mark.parametrize("missing", sorted(PAIRS_44_85))
def test_any_missing_pair_refuses_absence(tmp_path, missing):
    complete = manifest_at(tmp_path)
    incomplete = replace(
        complete, files=tuple(f for f in complete.files if f.pair != missing)
    )
    with pytest.raises(ValueError, match="every source on every departement"):
        reconcile([payload()], [], incomplete)


def test_one_source_disappears_other_remains_present(tmp_path):
    first = manifest_at(tmp_path, {EXISTING: [raw()], NEW: [raw()]})
    previous = reconcile([], [payload()], first)
    second = manifest_at(
        tmp_path, {NEW: [raw()]}, generation="second", observed_at=LATER
    )
    current = reconcile(previous, [payload(regime_dpe="neuf")], second)[0]
    assert current["source_state"] == "present"
    assert current["regime_dpe"] is None  # Historical provenance remains authoritative.
    observations = current["source_observations"]
    assert observations[0]["state"] == "present"
    assert observations[1]["state"] == "absent_from_complete_scope"
    assert observations[1]["last_seen_at"] == NOW
    assert observations[1]["observed_at"] == LATER
    assert (
        observations[1]["source_version"]
        != previous[0]["source_observations"][1]["source_version"]
    )


def test_complete_absence_purges_by_allowlist_and_reappearance_uses_only_new(tmp_path):
    first = manifest_at(tmp_path, {EXISTING: [raw()]})
    old = reconcile(
        [], [payload(adresse_brut="private", future_documentary="private")], first
    )
    empty = manifest_at(tmp_path, generation="second", observed_at=LATER)
    tombstone = reconcile(old, [], empty)[0]
    assert tombstone["source_state"] == "absent_from_complete_scope"
    assert tombstone["is_tombstone"] is True
    assert tombstone["identifiant_ban"] == "fixture-ban"
    for field in (
        "etiquette_dpe",
        "surface_habitable",
        "geom",
        "adresse_brut",
        "future_documentary",
    ):
        assert tombstone[field] is None
    third = manifest_at(
        tmp_path, {NEW: [raw()]}, generation="third", observed_at="2026-09-07T10:00:00Z"
    )
    reappeared = reconcile(
        [tombstone], [payload(etiquette_dpe=None, identifiant_ban="new-ban")], third
    )[0]
    assert reappeared["is_tombstone"] is False
    assert reappeared["etiquette_dpe"] is None
    assert reappeared["identifiant_ban"] == "new-ban"
    assert "adresse_brut" not in reappeared
    assert reappeared["regime_dpe"] is None


def test_movement_replaces_location_without_duplicate_or_false_retirement(tmp_path):
    first = manifest_at(tmp_path, {EXISTING: [raw()]})
    old = reconcile([], [payload()], first)
    moved_raw = raw(code_departement_ban="85")
    second = manifest_at(
        tmp_path,
        {("dpe02neuf", "85"): [moved_raw]},
        generation="second",
        observed_at=LATER,
    )
    current = reconcile(
        old,
        [payload(code_departement="85", code_insee="85109", identifiant_ban="new-ban")],
        second,
    )
    assert len(current) == 1
    assert current[0]["code_departement"] == "85"
    assert current[0]["identifiant_ban"] == "new-ban"
    assert current[0]["source_state"] == "present"


def test_legacy_absent_keeps_unknown_provenance_empty(tmp_path):
    current = reconcile(
        [payload(source_observations=[], source_state="unknown")],
        [],
        manifest_at(tmp_path),
    )[0]
    assert current["source_observations"] == []
    assert current["source_state"] == "absent_from_complete_scope"
    assert current["source_snapshot_at"] == NOW
    assert current["regime_dpe"] == "existant"


def test_historical_raw_presence_without_transformation_blocks(tmp_path):
    manifest = manifest_at(tmp_path, {EXISTING: [raw()]})
    with pytest.raises(ValueError, match="Historical DPE present but not transformed"):
        reconcile([payload()], [], manifest)


def test_document_without_geometry_is_present_never_rejected(tmp_path):
    # Plan 02-16 (DATA-02): a missing source geometry is no rejection cause.
    manifest = manifest_at(
        tmp_path, {EXISTING: [raw(coordonnee_cartographique_x_ban=None)]}
    )
    current = reconcile([], [payload(geom=None)], manifest)
    assert len(current) == 1
    assert current[0]["source_state"] == "present"
    assert current[0]["is_tombstone"] is False
    assert current[0]["geom"] is None
    with pytest.raises(TypeError):
        reconcile([], [], manifest, rejected={"fixture-one": "invalid_geometry"})


def test_untransformed_new_document_always_blocks(tmp_path):
    with pytest.raises(ValueError, match="DPE transformation coverage is incomplete"):
        reconcile([], [], manifest_at(tmp_path, {EXISTING: [raw()]}))


def test_invalid_dates_preserved_as_flags_but_absent_dates_are_not_invalid(tmp_path):
    manifest = manifest_at(
        tmp_path,
        {
            EXISTING: [
                raw(date_etablissement_dpe="bad", date_fin_validite_dpe="2026-02-30")
            ]
        },
    )
    current = reconcile(
        [],
        [payload(date_etablissement_dpe="bad", date_fin_validite_dpe="2026-02-30")],
        manifest,
    )[0]
    assert current["date_etablissement_dpe"] is None
    assert current["lifecycle_facts"] == {
        "replacement_state": "not_checked",
        "graph_issues": [],
        "date_issues": ["invalid_establishment_date", "invalid_end_date"],
    }
    empty = manifest_at(tmp_path, generation="empty", observed_at=LATER)
    assert reconcile([current], [], empty)[0]["lifecycle_facts"]["date_issues"] == []


def test_absent_dates_and_unverified_revision_defaults(tmp_path):
    current = reconcile([], [payload()], manifest_at(tmp_path, {EXISTING: [raw()]}))[0]
    assert current["lifecycle_facts"]["date_issues"] == []
    assert current["revision_evidence"] == {
        "status": "unverified",
        "kind": None,
        "revision_date": None,
        "verified_at": None,
        "evidence_sha256": None,
        "official_source": None,
    }


def test_unknown_incoming_and_out_of_scope_history_refuse(tmp_path):
    manifest = manifest_at(tmp_path)
    with pytest.raises(
        ValueError, match="DPE transformed row absent from raw registry"
    ):
        reconcile([], [payload()], manifest)
    with pytest.raises(ValueError, match="DPE previous row outside complete scope"):
        reconcile([payload(code_departement="75")], [], manifest)


def test_transformed_divergence_is_not_hidden_by_raw_equality(tmp_path):
    manifest = manifest_at(tmp_path, {EXISTING: [raw()]})
    with pytest.raises(ValueError, match="DPE transformed documentary collision"):
        reconcile([], [payload(), payload(etiquette_dpe="G")], manifest)


def test_registry_cannot_be_reused_for_a_different_generation(tmp_path):
    first = manifest_at(tmp_path, {EXISTING: [raw()]})
    second = manifest_at(tmp_path, generation="second", observed_at=LATER)
    with (
        collect_presence(first) as registry,
        pytest.raises(ValueError, match="DPE presence registry generation mismatch"),
    ):
        reconcile([], [], second, registry=registry)


@pytest.mark.parametrize("value", ["", " ", "private-number"])
def test_raw_null_empty_and_whitespace_are_not_equivalent(tmp_path, value):
    manifest = manifest_at(
        tmp_path, {EXISTING: [raw()], NEW: [raw(adresse_brut=value)]}
    )
    with pytest.raises(ValueError, match="DPE raw documentary collision"):
        collect_presence(manifest)


def test_decimal_precision_is_not_rounded_or_binary_float_coerced(tmp_path):
    manifest = manifest_at(tmp_path)
    with collect_presence(manifest) as registry:
        registry.add(
            raw(conso_5_usages_ep=Decimal("1.00000000000000000000000000001")), EXISTING
        )
        registry.add(
            raw(conso_5_usages_ep=Decimal("1.000000000000000000000000000010")), NEW
        )
        with pytest.raises(ValueError, match="DPE raw documentary collision"):
            registry.add(
                raw(conso_5_usages_ep=Decimal("1.00000000000000000000000000002")), NEW
            )


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_version", "file-derived-version"),
        ("observed_at", "2026-09-05 10:00:00"),
        ("last_seen_at", "2026-09-07T10:00:00Z"),
        ("department", "75"),
        ("import_id", ""),
        ("unexpected", "private-number"),
    ],
)
def test_malformed_previous_provenance_refuses_without_echo(tmp_path, field, value):
    first = manifest_at(tmp_path, {EXISTING: [raw()]})
    previous = reconcile([], [payload()], first)
    previous[0]["source_observations"][0][field] = value
    second = manifest_at(tmp_path, generation="second", observed_at=LATER)
    with pytest.raises(ValueError) as error:
        reconcile(previous, [], second)
    assert "private-number" not in str(error.value)
    assert "fixture-one" not in str(error.value)


def test_older_snapshot_is_not_a_reappearance(tmp_path):
    first = manifest_at(tmp_path, {EXISTING: [raw()]}, observed_at=LATER)
    previous = reconcile([], [payload()], first)
    older = manifest_at(tmp_path, generation="older", observed_at=NOW)
    with pytest.raises(ValueError, match="DPE snapshot predates previous observation"):
        reconcile(previous, [], older)


def test_latest_pair_observation_is_global_snapshot_time(tmp_path):
    manifest = manifest_at(tmp_path, {EXISTING: [raw()]})
    latest = replace(manifest.files[-1], observed_at=LATER)
    manifest = replace(manifest, files=(*manifest.files[:-1], latest))
    assert reconcile([], [payload()], manifest)[0]["source_snapshot_at"] == LATER


def test_registry_cleans_private_artifacts_after_exception(tmp_path):
    manifest = manifest_at(tmp_path, {EXISTING: [raw()]})
    with pytest.raises(RuntimeError), collect_presence(manifest, directory=tmp_path):
        assert len(list(tmp_path.glob("dpe-presence-*"))) == 1
        raise RuntimeError("synthetic failure")
    assert list(tmp_path.glob("dpe-presence-*")) == []


def test_uncollected_registry_never_proves_absence(tmp_path):
    manifest = manifest_at(tmp_path, {EXISTING: [raw()]})
    with (
        DpePresenceRegistry(manifest) as registry,
        pytest.raises(ValueError, match="DPE presence registry is incomplete"),
    ):
        reconcile([payload()], [], manifest, registry=registry)


def test_national_generation_tracks_pairs_beyond_64_bits(tmp_path):
    # 202 pairs: (dpe03existant, 976) is the last one, far past bit 63.
    mayotte = raw(code_departement_ban="976")
    manifest = manifest_at(
        tmp_path,
        {("dpe03existant", "976"): [mayotte], ("dpe02neuf", "976"): [mayotte]},
        departements=DEPARTEMENTS_HEBERGEABLES,
    )
    assert len(manifest.pairs) == 202
    with collect_presence(manifest) as registry:
        assert registry.pairs.index(("dpe03existant", "976")) == 201
        assert registry.observation("fixture-one")[0] == [
            ("dpe02neuf", "976"),
            ("dpe03existant", "976"),
        ]
    current = reconcile(
        [], [payload(code_departement="976", code_insee="97611")], manifest
    )[0]
    assert [o["department"] for o in current["source_observations"]] == [
        "976",
        "976",
    ]


def test_previous_rows_are_judged_on_the_generation_departements(tmp_path):
    guadeloupe = raw(code_departement_ban="971")
    first = manifest_at(
        tmp_path,
        {EXISTING_971: [guadeloupe]},
        departements=("44", "85", "971"),
    )
    previous = reconcile(
        [], [payload(code_departement="971", code_insee="97107")], first
    )
    second = manifest_at(
        tmp_path,
        generation="second",
        observed_at=LATER,
        departements=("44", "85", "971"),
    )
    retired = reconcile(previous, [], second)[0]
    assert retired["is_tombstone"] is True
    assert retired["source_observations"][0]["department"] == "971"
    # A generation without 971 cannot judge a 971 row absent: refused.
    third = manifest_at(tmp_path, generation="third", observed_at=LATER)
    with pytest.raises(ValueError, match="DPE previous row outside complete scope"):
        reconcile(previous, [], third)


def test_served_lifecycle_check_accepts_every_hostable_departement():
    # app/dpe/inventory_service.py calls it with one argument; since plan
    # 04.1-08 the served lifecycle names the founding departement, so a 971
    # provenance is served (under 971), a collectivity code still refused.
    observation = {
        "source_slug": "dpe03existant",
        "department": "44",
        "source_version": "a" * 64,
        "observed_at": NOW,
        "last_seen_at": NOW,
        "state": "present",
        "import_id": "generation",
    }
    assert _previous_observations({"source_observations": [observation]})
    observation["department"] = "971"
    assert _previous_observations({"source_observations": [observation]})
    scope = expected_pairs(("44",))
    with pytest.raises(ValueError, match="Invalid DPE previous provenance"):
        _previous_observations({"source_observations": [observation]}, scope)
    for code in ("975", "99"):
        observation["department"] = code
        with pytest.raises(ValueError, match="Invalid DPE previous provenance"):
            _previous_observations({"source_observations": [observation]})


# --- Equivalence with the code before plan 04.1-11 ---------------------------
# Outputs and errors below were recorded on 996ddbf (in-memory previous index)
# and frozen in tests/fixtures/dpe_reconciliation_equivalence.json; they are
# never recomputed by the code under test. ``repr`` keeps key order and types.

EQUIVALENCE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "dpe_reconciliation_equivalence.json"
)
OBSERVED_FIRST = "2026-09-01T08:00:00Z"
VERSION_FIRST = "b" * 64


def served(number, department="44", **changes):
    """A previous row as the served table returns it (every column, in order)."""
    row = {
        "id": int(sum(map(ord, number))),
        "numero_dpe": number,
        "code_departement": department,
        "code_insee": f"{department}109",
        "identifiant_ban": f"ban-{number}",
        "regime_dpe": "existant",
        "etiquette_dpe": "D",
        "surface_habitable": Decimal("71.50"),
        "date_etablissement_dpe": date(2021, 7, 1),
        "date_fin_validite_dpe": date(2031, 6, 30),
        "numero_dpe_remplace": None,
        "adresse_brut": f"private address {number}",
        "geom": "previous-position",
        "source_observations": [
            {
                "source_slug": "dpe03existant",
                "department": department,
                "source_version": VERSION_FIRST,
                "observed_at": OBSERVED_FIRST,
                "last_seen_at": OBSERVED_FIRST,
                "state": "present",
                "import_id": "first",
            }
        ],
        "source_state": "present",
        "source_snapshot_at": datetime(2026, 9, 1, 8, tzinfo=UTC),
        "is_tombstone": False,
        "lifecycle_facts": {
            "replacement_state": "no_successor_known",
            "graph_issues": [],
            "date_issues": [],
        },
        "revision_evidence": {"status": "unverified"},
    }
    row.update(changes)
    return row


def _both_sources(department):
    return [
        dict(o, source_slug=slug)
        for slug in ("dpe02neuf", "dpe03existant")
        for o in served("x", department)["source_observations"]
    ]


def equivalence_corpus(tmp_path):
    """Previous, incoming, absent, duplicate, moved, legacy and reappearing rows."""
    manifest = manifest_at(
        tmp_path,
        {
            EXISTING: [
                raw("kept-1"),
                raw("new-1"),
                raw("dup-1"),
                raw("dates-1", date_etablissement_dpe="bad"),
            ],
            NEW: [raw("dup-1"), raw("kept-2")],
            ("dpe03existant", "85"): [raw("moved-1", code_departement_ban="85")],
            ("dpe02neuf", "85"): [raw("tomb-1", code_departement_ban="85")],
        },
        generation="second",
        observed_at=LATER,
    )
    previous = [
        served("kept-1"),
        served("kept-2", source_observations=_both_sources("44")),
        served("moved-1"),
        served("gone-1", numero_dpe_remplace="gone-0"),
        served(
            "legacy-1",
            regime_dpe="neuf",
            source_observations=[],
            source_state="unknown",
            source_snapshot_at=None,
        ),
        served(
            "tomb-1",
            department="85",
            is_tombstone=True,
            source_state="absent_from_complete_scope",
            etiquette_dpe=None,
        ),
        served("gone-2", department="85", regime_dpe="other"),
    ]
    incoming = [
        payload("kept-1", etiquette_dpe="B"),
        payload("new-1", date_etablissement_dpe="2026-01-02"),
        payload("dup-1"),
        payload("dup-1"),
        payload("dates-1", date_etablissement_dpe="bad"),
        payload("kept-2", regime_dpe="neuf"),
        payload("moved-1", code_departement="85", code_insee="85109"),
        payload("tomb-1", code_departement="85", code_insee="85109"),
    ]
    return manifest, previous, incoming


def error_cases(tmp_path):
    """Each case: previous rows, incoming rows, raw rows of the generation."""
    one = {EXISTING: [raw()]}
    later = datetime(2026, 9, 7, tzinfo=UTC)
    newer = dict(
        served("x")["source_observations"][0], observed_at="2026-09-08T00:00:00Z"
    )
    newer["last_seen_at"] = newer["observed_at"]
    return {
        "duplicate_previous": ([served("p"), served("p")], [], {}),
        "blank_previous": ([served(" ")], [], {}),
        "non_text_previous": ([served("p", numero_dpe=7)], [], {}),
        "previous_outside_scope": ([served("p", department="75")], [], {}),
        "previous_snapshot_later": (
            [served("p", source_snapshot_at=later)],
            [],
            {},
        ),
        "previous_observation_later": (
            [served("p", source_observations=[newer])],
            [],
            {},
        ),
        "malformed_previous_provenance": (
            [served("p", source_observations=[{"source_slug": "dpe03existant"}])],
            [],
            {},
        ),
        "incoming_outside_raw_scope": (
            [],
            [payload(code_departement="85")],
            one,
        ),
        "historical_not_transformed": ([served("fixture-one")], [], one),
        "coverage_incomplete": ([served("p")], [], one),
        "incoming_unknown": ([], [payload("unknown")], one),
        "first_error_wins": (
            [served("p"), served("q", department="75"), served("p")],
            [],
            {},
        ),
    }


def equivalence_outputs(tmp_path):
    """Reconciled rows and (type, message) errors, as frozen representations."""
    manifest, previous, incoming = equivalence_corpus(tmp_path)
    rows = [repr(row) for row in reconcile(previous, incoming, manifest)]
    errors = {}
    for index, (name, (old, new, raws)) in enumerate(error_cases(tmp_path).items()):
        case = manifest_at(
            tmp_path, raws, generation=f"error-{index}", observed_at=LATER
        )
        try:
            reconcile(old, new, case)
        except Exception as error:  # noqa: BLE001 - the type is the evidence
            errors[name] = [type(error).__name__, str(error)]
        else:
            errors[name] = None
    return {"rows": rows, "errors": errors}


def test_reconciliation_equals_the_frozen_pre_11_outputs(tmp_path):
    expected = json.loads(EQUIVALENCE_FIXTURE.read_text(encoding="utf-8"))
    actual = equivalence_outputs(tmp_path)
    assert actual["rows"] == expected["rows"]
    assert actual["errors"] == expected["errors"]
