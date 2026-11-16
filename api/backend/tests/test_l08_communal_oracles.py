"""Independent source oracle and deliberate false certificates, no SQL/network."""

import re
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from tests.oracles import communal_sources as oracle

CASES = oracle.load_manifest()["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_every_certified_field_rejects_a_wrong_value(case):
    expected = oracle.expected_row(case)
    oracle.assert_row(case, expected)
    for _label, field, wrong in oracle.mutations(case):
        with pytest.raises(AssertionError, match=re.escape(case["id"])):
            oracle.assert_row(case, {**expected, field: wrong})
    for field in expected:
        missing = dict(expected)
        del missing[field]
        with pytest.raises(AssertionError, match="missing"):
            oracle.assert_row(case, missing)


@pytest.mark.parametrize(
    "damage", ["hash", "value", "unit", "method", "empty", "identity"]
)
def test_changed_manifest_is_not_a_certificate(damage):
    manifest = deepcopy(oracle.load_manifest())
    if damage == "hash":
        manifest["sources"]["guide p1"]["sha256"] = "0" * 64
    elif damage in {"value", "unit", "method"}:
        manifest["cases"][0]["fields"]["annee"][damage] = "WRONG"
    elif damage == "identity":
        manifest["cases"][0]["key"]["code_insee"] = "99999"
    else:
        manifest["cases"] = []
    with pytest.raises(AssertionError, match="manifest differs"):
        oracle.validate_manifest(manifest)


def test_changed_source_bytes_are_rejected_before_any_comparison(tmp_path, monkeypatch):
    folder = tmp_path / "cor05_revenus"
    shutil.copytree(oracle.FIXTURES / "cor05_revenus", folder)
    source = folder / "FILO2021_DEC_COM.csv"
    original = source.read_bytes()
    assert b"24380" in original
    source.write_bytes(original.replace(b"24380", b"24170", 1))
    guide = tmp_path / "l08_oracles"
    guide.mkdir()
    shutil.copyfile(
        oracle.FIXTURES / "l08_oracles/loyers-guide-2025.pdf",
        guide / "loyers-guide-2025.pdf",
    )
    monkeypatch.setattr(oracle, "ROOT", tmp_path)
    monkeypatch.setattr(oracle, "FIXTURES", tmp_path)
    with pytest.raises(AssertionError):
        oracle.build_manifest()


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"], ids=["Linux-LF", "Windows-CRLF"])
def test_checkout_newlines_preserve_manifest_but_content_changes_fail(
    tmp_path, monkeypatch, newline
):
    baseline = oracle.load_manifest()
    fixture_relative = Path("api/backend/tests/fixtures")
    audit_relative = Path("docs/audit-stabilisation-2026-09-08")
    folders = [
        fixture_relative / name
        for name in ("cor05_revenus", "cor08_lovac", "loyers", "l08_oracles")
    ]
    folders.append(audit_relative / "lot-02/cor06-sources")
    for relative in folders:
        shutil.copytree(oracle.ROOT / relative, tmp_path / relative)
    for relative in (
        fixture_relative / "cor05_revenus",
        fixture_relative / "loyers",
        audit_relative / "lot-02/cor06-sources",
    ):
        for path in (tmp_path / relative).glob("*.csv"):
            path.write_bytes(
                path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline)
            )
    monkeypatch.setattr(oracle, "ROOT", tmp_path)
    monkeypatch.setattr(oracle, "FIXTURES", tmp_path / fixture_relative)
    monkeypatch.setattr(oracle, "AUDIT", tmp_path / audit_relative)
    assert oracle.build_manifest() == baseline
    source = oracle.AUDIT / "lot-02/cor06-sources/appartement.prefix.csv"
    original = source.read_bytes()
    changed = original.replace(b"9,75769624568385", b"9,85769624568385", 1)
    assert changed != original
    source.write_bytes(changed)
    with pytest.raises(AssertionError):
        oracle.build_manifest()


def test_oracle_has_no_production_imports_and_preserves_named_counterexamples():
    import ast

    tree = ast.parse(oracle.Path(oracle.__file__).read_text(encoding="utf-8"))
    imports = [
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    ]
    assert all(not name.startswith(("app", "pandas")) for name in imports)
    assert len(CASES) == 22
    rows = {case["id"]: oracle.expected_row(case) for case in CASES}
    assert rows["revenus:44109"]["revenu_median"] == 24380
    assert rows["revenus:01019"]["revenu_median"] is None
    assert rows["loyers:05066:appartement"]["nb_observations_commune"] == 0
    assert rows["loyers:05066:appartement"]["nb_observations_maille"] == 484
    assert rows["vacance:01009"]["taux_vacance"] is None
    assert rows["vacance:02343"]["taux_vacance"] == 0
