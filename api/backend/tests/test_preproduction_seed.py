import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[3] / "scripts" / "seed_preproduction.py"
SPEC = importlib.util.spec_from_file_location("seed_preproduction", SCRIPT)
seed = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = seed
SPEC.loader.exec_module(seed)


def test_rejects_same_database_before_writer_is_created():
    source = seed.DatabaseIdentity("cluster-a", "datacommune", "192.0.2.1", 5432)
    with pytest.raises(seed.SeedRefusedError, match="same PostgreSQL database"):
        seed.validate_isolation(source, source, "preproduction")


def test_rejects_target_without_preproduction_marker():
    source = seed.DatabaseIdentity("cluster-a", "datacommune", "192.0.2.1", 5432)
    target = seed.DatabaseIdentity(
        "cluster-b", "datacommune_preprod", "192.0.2.2", 5432
    )
    with pytest.raises(seed.SeedRefusedError, match="marker"):
        seed.validate_isolation(source, target, "production")


def test_repair_target_requires_preprod_name_and_marker():
    preprod = seed.DatabaseIdentity("cluster-b", "datacommune_preprod", "db", 5432)
    seed.validate_repair_target(preprod, "preproduction")
    with pytest.raises(seed.SeedRefusedError, match="marker"):
        seed.validate_repair_target(preprod, "production")
    production = seed.DatabaseIdentity("cluster-a", "datacommune", "db", 5432)
    with pytest.raises(seed.SeedRefusedError, match="marker"):
        seed.validate_repair_target(production, "preproduction")
    lookalike = seed.DatabaseIdentity(
        "cluster-b", "datacommune_preprod_shadow", "db", 5432
    )
    with pytest.raises(seed.SeedRefusedError, match="marker"):
        seed.validate_repair_target(lookalike, "preproduction")


@pytest.mark.parametrize(
    "table", ["users", "api_keys", "quota_usage_account", "chat_sessions"]
)
def test_private_tables_are_refused(table):
    manifest = seed.load_manifest(
        Path(__file__).parents[3] / "infra" / "preproduction-seed-tables.json"
    )
    with pytest.raises(seed.SeedRefusedError, match="private table"):
        seed.validate_requested_tables([table], manifest, {table})


def test_unknown_table_is_refused_even_if_it_exists():
    manifest = seed.load_manifest(
        Path(__file__).parents[3] / "infra" / "preproduction-seed-tables.json"
    )
    with pytest.raises(seed.SeedRefusedError, match="not classified"):
        seed.validate_requested_tables(
            ["future_public_table"], manifest, {"future_public_table"}
        )


def test_allowlisted_public_table_is_accepted():
    manifest = seed.load_manifest(
        Path(__file__).parents[3] / "infra" / "preproduction-seed-tables.json"
    )
    assert seed.validate_requested_tables(
        ["geo_communes"], manifest, {"geo_communes"}
    ) == ["geo_communes"]


def test_nonempty_target_requires_verify_only_mode():
    with pytest.raises(seed.SeedRefusedError, match="not empty"):
        seed.validate_target_empty({"geo_communes": 1}, mode="seed-empty")
    seed.validate_target_empty({"geo_communes": 1}, mode="verify-existing")


def test_migration_baseline_is_allowed_but_public_rows_are_not():
    seed.validate_target_empty(
        {"dataset_metadata": 27, "geo_communes": 0},
        mode="seed-empty",
        migration_baseline_tables={"dataset_metadata"},
    )
    with pytest.raises(seed.SeedRefusedError, match="not empty"):
        seed.validate_target_empty(
            {"dataset_metadata": 27, "geo_communes": 1},
            mode="seed-empty",
            migration_baseline_tables={"dataset_metadata"},
        )


def test_exact_address_migration_fixture_is_accepted():
    seed.validate_migration_address_fixture(set(seed.MIGRATION_ADDRESS_FIXTURE))


def test_modified_or_real_address_data_is_refused():
    modified = set(seed.MIGRATION_ADDRESS_FIXTURE)
    row = modified.pop()
    modified.add((*row[:7], row[7] + 0.0001, row[8], row[9]))
    with pytest.raises(seed.SeedRefusedError, match="exact migration fixture"):
        seed.validate_migration_address_fixture(modified)

    with pytest.raises(seed.SeedRefusedError, match="exact migration fixture"):
        seed.validate_migration_address_fixture(
            seed.MIGRATION_ADDRESS_FIXTURE
            | {
                (
                    "44",
                    "8",
                    None,
                    "Rue du test",
                    "44000",
                    "44109",
                    "Nantes",
                    -1.5,
                    47.2,
                    None,
                )
            }
        )


def test_publication_oid_rebind_sql_uses_target_regclass():
    sql = seed.publication_oid_rebind_sql()
    assert "to_regclass" in sql
    assert "relation_oids" in sql
    assert "jsonb_object_keys" in sql
    assert "missing target relation" in sql


def test_dump_command_refuses_empty_selector_set(tmp_path):
    with pytest.raises(seed.SeedRefusedError, match="non-baseline public table"):
        seed.build_dump_command(tmp_path / "dump", [], "snapshot")


def test_dump_command_has_selector_for_every_table(tmp_path):
    command = seed.build_dump_command(
        tmp_path / "dump", ["geo_communes", "adresses"], "snapshot"
    )
    assert command.count("--table-and-children") == 2
    assert "public.geo_communes" in command
    assert "public.adresses" in command


def test_restore_toc_excludes_source_sequence_values_only():
    lines = [
        "; archive header",
        "123; 0 0 SEQUENCE SET public adresses_id_seq datacommune",
        "456; 0 123 TABLE DATA public adresses datacommune",
    ]
    assert seed.filter_restore_toc(lines) == [lines[0], lines[2]]


def test_only_full_manifest_seed_rebuilds_canonical_views():
    manifest = {"tables": ["adresses", "geo_communes"]}
    assert seed.should_repair_canonical_views(list(manifest["tables"]), manifest)
    assert not seed.should_repair_canonical_views(["adresses"], manifest)


def test_full_seed_substitutes_canonical_rebuild_for_legacy_refresh(monkeypatch):
    calls = []

    class Target:
        committed = False

        def commit(self):
            self.committed = True

    target = Target()
    monkeypatch.setattr(
        seed,
        "_post_restore",
        lambda _target, views, *, rebind_publications: calls.append(
            ("refresh", views, rebind_publications)
        ),
    )
    monkeypatch.setattr(seed, "_counts", lambda _target, _tables: {"adresses": 2})
    monkeypatch.setattr(
        seed,
        "_repair_canonical_views",
        lambda _target: calls.append(("canonical",)) or ({"builder": "hash"}, 1.0),
    )

    counts, hashes = seed._finalize_restored_target(
        target,
        views=["communes_lookup", "v_commune_synthese", "other_view"],
        tables=["adresses"],
        source_counts={"adresses": 2},
        full_seed=True,
    )

    assert calls == [("refresh", ["other_view"], False), ("canonical",)]
    assert counts == {"adresses": 2}
    assert hashes == {"builder": "hash"}
    assert target.committed
