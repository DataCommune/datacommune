"""Offline blocks of the broadband equivalence fingerprints (plan 04.1-16)."""

from __future__ import annotations

import json

from scripts import eligibilite_equivalence as eq


def _rapport(adresses: dict, communes: dict, seed: str = "s") -> dict:
    return {
        "seed": seed,
        "departements": ["44"],
        "adresses": adresses,
        "communes_fibre": communes,
    }


def test_par_adresse_ignores_order_among_ties_but_not_the_sequence() -> None:
    a = ("IMB", "44109", "RUE", "1", "FO", "1000", "OP1")
    b = ("IMB", "44109", "RUE", "1", "FO", "1000", "OP2")
    c = ("IMB", "44109", "RUE", "1", "CU", "8", "OP1")
    assert eq.empreinte_par_adresse([a, b, c]) == eq.empreinte_par_adresse([b, a, c])
    assert eq.empreinte_par_adresse([a, b, c]) != eq.empreinte_par_adresse([c, a, b])


def test_multiset_keeps_duplicates() -> None:
    ligne = ("IMB", "RUE", "1", None, "FO", "1000")
    assert eq.empreinte_multiensemble([ligne]) != eq.empreinte_multiensemble(
        [ligne, ligne]
    )


def test_compare_names_differences_and_missing_keys() -> None:
    avant = _rapport({"k1": {"x": 1}, "k2": {"x": 2}}, {"c1": "f"})
    apres = _rapport({"k1": {"x": 1}, "k3": {"x": 2}}, {"c1": "g"})
    resultat = eq.comparer(avant, apres)
    assert resultat["differentes"] == [
        "adresses:k2",
        "adresses:k3",
        "communes_fibre:c1",
    ]
    assert resultat["egales"] == 1
    assert eq.comparer(avant, avant)["differentes"] == []
    assert eq.comparer(avant, {**avant, "seed": "t"})["meme_echantillon"] is False


def test_compare_exits_non_zero_on_a_difference(tmp_path) -> None:
    avant = tmp_path / "avant.json"
    apres = tmp_path / "apres.json"
    avant.write_text(json.dumps(_rapport({"k": {"x": 1}}, {})))
    apres.write_text(json.dumps(_rapport({"k": {"x": 2}}, {})))
    assert eq.main(["compare", "--avant", str(avant), "--apres", str(avant)]) == 0
    assert eq.main(["compare", "--avant", str(avant), "--apres", str(apres)]) == 1


def test_every_statement_is_allowlisted_and_names_no_address() -> None:
    for sql in eq.ALLOWED_SQL:
        eq.assert_select_only(sql, eq.ALLOWED_SQL)
    assert eq._sha("44109_0001_00012") != "44109_0001_00012"
