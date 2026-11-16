"""scripts/bdnb_equivalence.py, without network (04.7-19, T5).

Hand-built zips stand for the producer files and a simulated API stands for
``api.bdnb.io``. What is checked: the sample is deterministic and its cost is
exact (``g // 10 + 1`` pages), the collection never exceeds budget nor reserve
and stops at the first 429, a perfectly equivalent API is ``egal`` at 10, 20
and 50 groups (red with the ``ceil(g / 10)`` formula), one extra group at 20 is
a complete collection classed ``ecart_a_consigner`` while ten extra groups end
on a full terminal page, hence ``non_collecte``, and a gap is explained only by
a written proof naming it exactly.

Drift of the rule against the pipeline, on the mini-export of
``tests/etl/bdnb_fixture``: here against its published rows written by hand
(and the per-file counts of ``app.etl_bdnb.transform``), and in
``tests/integration/test_bdnb_fidelite.py`` against the rows the real swap
publishes in PostGIS.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import zipfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from scripts import bdnb_equivalence as be

pytestmark = pytest.mark.unit

VERSION_CSV = (
    "millesime;version;date_publication;changelog_rapide;changelog_detaille\n"
    '2025-07.a;v0.7.0;2025/08/01;"un\nsur deux lignes";"x"\n'
    "2026-02.b;v0.7.12;2026/10/07;rapide;detaille\n"
)
REL_HEADER = (
    "batiment_groupe_id;parcelle_id;code_departement_insee;parcelle_principale\n"
)
FFO_HEADER = (
    "batiment_groupe_id;code_departement_insee;nb_niveau;annee_construction;"
    "usage_niveau_1_txt;mat_mur_txt;mat_toit_txt;nb_log\n"
)
PARCELLE_HEADER = (
    "geom_parcelle;parcelle_id;code_departement_insee;code_commune_insee;"
    "s_geom_parcelle\n"
)


def _zip(
    chemin: Path,
    relations: Sequence[tuple[str, str, int]],
    ffo: Sequence[tuple[str, str, str, str]],
    parcelles: Sequence[str] = (),
    *,
    rel_bytes: bytes | None = None,
    version: str = VERSION_CSV,
) -> Path:
    """Write a dep zip.

    relations=(group, parcel, main); ffo=(group, year, wall, roof).
    """
    rel = REL_HEADER + "".join(f'{g};{p};"85";{m}\n' for g, p, m in relations)
    ffo_txt = FFO_HEADER + "".join(
        f'{g};"85";1;{a};R;{mur};{toit};1\n' for g, a, mur, toit in ffo
    )
    par = PARCELLE_HEADER + "".join(
        f'"POLYGON ((0 0))";{p};"85";"85001";1\n' for p in parcelles
    )
    with zipfile.ZipFile(chemin, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            be.MEMBRE_RELATIONS, rel_bytes if rel_bytes is not None else rel.encode()
        )
        zf.writestr(be.MEMBRE_FFO, ffo_txt.encode())
        zf.writestr(be.MEMBRE_PARCELLES, par.encode())
        zf.writestr(be.MEMBRE_VERSION, version.encode())
        zf.writestr("csv/batiment_groupe.csv", b"\xff\xfe not read")  # never opened
    return chemin


def _pid(i: int) -> str:
    return f"85001000AA{i:04d}"


def _gid(i: int) -> str:
    return f"bdnb-bg-{i:04d}"


@pytest.fixture
def zips(tmp_path: Path) -> Path:
    """Parcels: 0 one group+year; 1 two groups; 2 non-main; 3 no year; 4 no ffo;
    5 duplicated relation (main and non-main); 6 twelve groups; 7-8 no relation.
    """
    rel: list[tuple[str, str, int]] = [
        (_gid(0), _pid(0), 1),
        (_gid(1), _pid(1), 1),
        (_gid(2), _pid(1), 1),
        (_gid(3), _pid(2), 0),
        (_gid(4), _pid(3), 1),
        (_gid(5), _pid(4), 1),
        (_gid(6), _pid(5), 0),
        (_gid(6), _pid(5), 1),
    ]
    rel += [(_gid(100 + k), _pid(6), 1) for k in range(12)]
    # The two fixed parcels of the real sample (all relations main).
    rel += [
        (_gid(200), be.PARCELLES_FIXES[0], 1),
        (_gid(201), be.PARCELLES_FIXES[1], 1),
        (_gid(202), be.PARCELLES_FIXES[1], 1),
    ]
    ffo = [
        (_gid(0), "1984", "BRIQUES", "TUILES"),
        (_gid(1), "1950", "PIERRE", "ARDOISES"),
        (_gid(2), "1960", "", "TUILES"),
        (_gid(3), "1999", "BETON", "TUILES"),
        (_gid(4), "", "BRIQUES", ""),
        (_gid(6), "1970", "BRIQUES", "TUILES"),
    ]
    ffo += [(_gid(100 + k), str(1900 + k), "BRIQUES", "TUILES") for k in range(12)]
    ffo += [(_gid(200 + k), str(1980 + k), "BRIQUES", "TUILES") for k in range(3)]
    _zip(
        tmp_path / "dep85.zip",
        rel,
        ffo,
        [_pid(i) for i in range(9)],
    )
    return tmp_path


# ------------------------------------------------------------------ rule --


def test_rule_keeps_non_main_relations_and_left_joins_ffo(zips: Path) -> None:
    regle = be.charger_regle(be.lister_zips(zips))
    assert regle.millesime == "2026-02.b"
    non_principale = regle.groupes(_pid(2))
    assert [g["batiment_groupe_id"] for g in non_principale] == [_gid(3)]
    assert non_principale[0]["parcelle_principale"] is False
    sans_ffo = regle.groupes(_pid(4))[0]
    assert sans_ffo["annee_construction"] is None
    assert sans_ffo["mat_mur_txt"] is None
    sans_annee = regle.groupes(_pid(3))[0]
    assert sans_annee["annee_construction"] is None
    assert sans_annee["mat_mur_txt"] == "BRIQUES"
    assert regle.groupes(_pid(1))[1]["mat_mur_txt"] is None  # empty -> NULL
    assert regle.groupes("85999000ZZ9999") == []


def test_duplicated_relation_is_one_row_with_bool_or(zips: Path) -> None:
    regle = be.charger_regle(be.lister_zips(zips))
    lignes = regle.groupes(_pid(5))
    assert len(lignes) == 1
    assert lignes[0]["parcelle_principale"] is True
    assert regle.compteurs["relations_doublons"] == 1
    assert regle.compteurs["relations_lues"] == 8 + 12 + 3
    assert regle.compteurs["ffo_divergents"] == 0


def test_divergent_ffo_duplicates_are_counted(tmp_path: Path) -> None:
    _zip(tmp_path / "dep44.zip", [(_gid(1), _pid(1), 1)], [(_gid(1), "1950", "A", "B")])
    _zip(tmp_path / "dep85.zip", [(_gid(1), _pid(1), 1)], [(_gid(1), "1951", "A", "B")])
    regle = be.charger_regle(be.lister_zips(tmp_path))
    assert regle.compteurs["ffo_doublons"] == 1
    assert regle.compteurs["ffo_divergents"] == 1
    assert regle.compteurs["relations_doublons"] == 1  # same pair in both files


def test_strict_utf8_and_bom(tmp_path: Path) -> None:
    mauvais = (REL_HEADER + 'bdnb-bg-1;85001000AA0001;"85";1\n').encode() + b"\xff\n"
    _zip(tmp_path / "dep85.zip", [], [], rel_bytes=mauvais)
    with pytest.raises(be.EquivalenceError, match="UTF-8"):
        be.charger_regle(be.lister_zips(tmp_path))
    bom = b"\xef\xbb\xbf" + (REL_HEADER + 'bdnb-bg-1;85001000AA0001;"85";1\n').encode()
    _zip(tmp_path / "dep85.zip", [], [(_gid(1), "1984", "A", "B")], rel_bytes=bom)
    regle = be.charger_regle(be.lister_zips(tmp_path))
    assert regle.groupes("85001000AA0001")[0]["batiment_groupe_id"] == "bdnb-bg-1"


def test_non_integer_year_and_missing_member_are_refused(tmp_path: Path) -> None:
    _zip(tmp_path / "dep85.zip", [(_gid(1), _pid(1), 1)], [(_gid(1), "19x4", "A", "B")])
    with pytest.raises(be.EquivalenceError, match="entier"):
        be.charger_regle(be.lister_zips(tmp_path))
    with zipfile.ZipFile(tmp_path / "dep85.zip", "w") as zf:
        zf.writestr(be.MEMBRE_VERSION, VERSION_CSV)
    with pytest.raises(be.EquivalenceError, match="membre absent"):
        be.charger_regle(be.lister_zips(tmp_path))


def test_latest_vintage_is_the_most_recent_publication(zips: Path) -> None:
    with zipfile.ZipFile(zips / "dep85.zip") as zf:
        assert be.millesime_du_zip(zf) == "2026-02.b"


# ---------------------------------------------------------------- sample --


def _echantillon(zips: Path, graine: int = be.GRAINE_DEFAUT, **kw: Any) -> dict:
    liste = be.lister_zips(zips)
    regle = be.charger_regle(liste)
    cibles = {"S1": 1, "S2": 1, "S3": 1, "S4": 2, "S5": 1, "S6": 2}
    return be.choisir_echantillon(
        regle,
        be.parcelles_sans_relation(liste, regle),
        graine,
        cibles=cibles,
        fixes=(_pid(0), _pid(1)),
        **kw,
    )


def test_sample_is_deterministic_and_ordered_by_sha256(zips: Path) -> None:
    a = _echantillon(zips)
    assert a == _echantillon(zips)
    s6 = [p["parcelle"] for p in a["parcelles"] if p["strate"] == "S6"]
    attendu = sorted(
        (_pid(7), _pid(8)),
        key=lambda p: hashlib.sha256(f"{be.GRAINE_DEFAUT}{p}".encode()).hexdigest(),
    )
    assert s6 == attendu
    autre = _echantillon(zips, graine=1)
    assert a["graine"] != autre["graine"]


def test_strata_do_not_share_a_parcel_and_follow_their_definition(zips: Path) -> None:
    ech = _echantillon(zips)
    pids = [p["parcelle"] for p in ech["parcelles"]]
    assert len(pids) == len(set(pids))
    par_strate = {p["parcelle"]: p["strate"] for p in ech["parcelles"]}
    assert par_strate[_pid(0)] == par_strate[_pid(1)] == "fixe"
    assert par_strate[_pid(6)] == "S5"
    assert par_strate[_pid(2)] == "S3"
    assert {p for p, s in par_strate.items() if s == "S4"} == {_pid(3), _pid(4)}
    assert all(p["groupes"] == 0 for p in ech["parcelles"] if p["strate"] == "S6")


def test_exact_cost_per_group_count() -> None:
    assert [be.pages_prevues(g) for g in (0, 1, 9, 10, 11, 19, 20, 50)] == [
        1, 1, 1, 2, 2, 2, 3, 6,
    ]  # fmt: skip
    cout = be.cout_total([{"groupes": 0}, {"groupes": 20}, {"groupes": 50}])
    assert cout == {"pages": 1 + 3 + 6, "millesime_api": 1, "total": 11}


def test_cost_over_budget_reduces_s5_then_s6_and_records_why(zips: Path) -> None:
    complet = _echantillon(zips)
    total = complet["cout"]["total"]
    reduit = _echantillon(zips, budget=total - 1)
    assert [r["strate"] for r in reduit["reductions"]] == ["S5"]
    assert reduit["cout"]["total"] <= total - 1
    assert "budget" in reduit["reductions"][0]["motif"]
    tres_serre = _echantillon(zips, budget=5)
    assert {r["strate"] for r in tres_serre["reductions"]} == {"S5", "S6"}
    assert _echantillon(zips, budget=total)["reductions"] == []


def test_fixed_parcel_absent_from_files_is_refused(zips: Path) -> None:
    regle = be.charger_regle(be.lister_zips(zips))
    with pytest.raises(be.EquivalenceError, match="parcelle fixe absente"):
        be.choisir_echantillon(regle, [], fixes=("85000000ZZ0000",))


def test_attendu_matches_the_sample_and_refuses_another_vintage(zips: Path) -> None:
    ech = _echantillon(zips)
    regle = be.charger_regle(be.lister_zips(zips))
    doc = be.attendu(regle, ech)
    assert set(doc["parcelles"]) == {p["parcelle"] for p in ech["parcelles"]}
    assert doc["millesime_fichier"] == "2026-02.b"
    with pytest.raises(be.EquivalenceError, match="millésime"):
        be.attendu(regle, {**ech, "millesime_fichier": "2025-07.a"})
    faux = json.loads(json.dumps(ech))
    faux["parcelles"][0]["groupes"] += 1
    with pytest.raises(be.EquivalenceError, match="groupes"):
        be.attendu(regle, faux)


# ------------------------------------------------------- simulated API --


class FauxApi:
    """Serves ``rows`` per parcel the way PostgREST pages them."""

    def __init__(
        self,
        groupes: Mapping[str, Sequence[Mapping[str, Any]]],
        *,
        quota: int = 7_800,
        vintage: str | None = "v0.7.12 millésime 2026-02.b",
        statut: Callable[[int], int] | None = None,
    ) -> None:
        self.groupes = groupes
        self.quota = quota
        self.vintage = vintage
        self.statut = statut
        self.urls: list[str] = []

    def __call__(self, url: str) -> be.Reponse:
        self.urls.append(url)
        n = len(self.urls)
        self.quota -= 1
        entetes = {"X-Quota-Remaining": str(self.quota)}
        code = self.statut(n) if self.statut else 200
        if code != 200:
            return be.Reponse(code, entetes, b"")
        if url.rstrip("/").endswith("/v1/bdnb"):
            info = {"info": {"description": self.vintage or "sans millésime"}}
            return be.Reponse(200, entetes, json.dumps(info).encode())
        requete = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(requete.query)
        pid = params["parcelle_id"][0].removeprefix("eq.")
        limite = int(params["limit"][0])
        decalage = int(params.get("offset", ["0"])[0])
        lignes = sorted(
            self.groupes.get(pid, []), key=lambda r: r["batiment_groupe_id"]
        )
        page = lignes[decalage : decalage + limite]
        return be.Reponse(200, entetes, json.dumps(page).encode())


def _ligne(i: int, annee: int | None = 1984) -> dict[str, Any]:
    return {
        "batiment_groupe_id": _gid(i),
        "annee_construction": annee,
        "mat_mur_txt": "BRIQUES",
        "mat_toit_txt": "TUILES",
        "l_parcelle_id": [_pid(0)],
    }


def _attendu_de(lignes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {k: r[k] for k in ("batiment_groupe_id", *be.CHAMPS)}
        | {"parcelle_principale": True}
        for r in lignes
    ]


def _monde(tailles: Mapping[str, int]) -> tuple[dict, dict, dict]:
    """(sample, attendu, api rows) for parcels named after their sizes."""
    parcelles, attendu, api = [], {}, {}
    for k, (pid, g) in enumerate(tailles.items()):
        lignes = [_ligne(1000 * k + i) for i in range(g)]
        parcelles.append(
            {
                "parcelle": pid,
                "strate": "S5" if g > 10 else "S2",
                "departement": "85",
                "groupes": g,
                "pages": be.pages_prevues(g),
            }
        )
        attendu[pid] = _attendu_de(lignes)
        api[pid] = lignes
    echantillon = {
        "millesime_fichier": "2026-02.b",
        "parcelles": parcelles,
        "cout": be.cout_total(parcelles),
    }
    return echantillon, {"millesime_fichier": "2026-02.b", "parcelles": attendu}, api


def _collecter(ech: Mapping, api: FauxApi, **kw: Any) -> dict:
    options = {"budget": 200, "reserve": 7000, "pause": 0.0, "sleep": lambda _: None}
    return be.collecter(ech, api, **(options | kw))


@pytest.mark.parametrize("g", [10, 20, 50])
def test_equivalent_api_is_egal_and_never_non_collecte(g: int) -> None:
    ech, attendu, rows = _monde({_pid(1): g})
    api = FauxApi(rows)
    collecte = _collecter(ech, api)
    resultat = collecte["parcelles"][0]
    assert resultat["statut"] == "collecte"
    assert resultat["pages_demandees"] == g // 10 + 1  # not ceil(g / 10)
    assert collecte["requetes_depensees"] == g // 10 + 1 + be.MILLESIME_REQUETES
    comparaison = be.comparer(ech, attendu, collecte)
    assert comparaison["classes"]["egal"] == 1
    assert comparaison["classes"]["non_collecte"] == 0
    assert comparaison["exit"] == 0


def test_one_extra_group_at_twenty_is_complete_and_to_be_recorded() -> None:
    ech, attendu, rows = _monde({_pid(1): 20})
    rows[_pid(1)].append(_ligne(99_999))
    collecte = _collecter(ech, FauxApi(rows))
    assert collecte["parcelles"][0]["statut"] == "collecte"
    comparaison = be.comparer(ech, attendu, collecte)
    assert comparaison["classes"]["ecart_a_consigner"] == 1
    assert comparaison["ecarts"][0]["champ"] == "presence"
    assert comparaison["ecarts"][0]["groupe"] == _gid(99_999)
    assert (comparaison["ecarts"][0]["fichier"], comparaison["ecarts"][0]["api"]) == (
        "absent",
        "present",
    )
    assert comparaison["exit"] == 1


def test_ten_extra_groups_end_on_a_full_page_so_non_collecte() -> None:
    ech, attendu, rows = _monde({_pid(1): 20})
    rows[_pid(1)] += [_ligne(90_000 + i) for i in range(10)]
    api = FauxApi(rows)
    collecte = _collecter(ech, api)
    assert collecte["parcelles"][0]["statut"] == "non_collecte"
    assert collecte["parcelles"][0]["pages_demandees"] == 3  # no page off plan
    assert len(api.urls) == 1 + 3
    comparaison = be.comparer(ech, attendu, collecte)
    assert comparaison["classes"]["non_collecte"] == 1
    assert comparaison["exit"] == 1


def test_a_short_page_before_the_end_completes_the_parcel() -> None:
    ech, attendu, rows = _monde({_pid(1): 20})
    rows[_pid(1)] = rows[_pid(1)][:5]
    api = FauxApi(rows)
    collecte = _collecter(ech, api)
    assert collecte["parcelles"][0]["pages_demandees"] == 1
    comparaison = be.comparer(ech, attendu, collecte)
    assert comparaison["classes"]["ecart_a_consigner"] == 1
    assert {e["champ"] for e in comparaison["ecarts"]} == {"presence"}
    assert len(comparaison["ecarts"]) == 15


def test_paged_requests_carry_a_stable_order_and_offsets() -> None:
    ech, _, rows = _monde({_pid(1): 20, _pid(2): 1})
    api = FauxApi(rows)
    _collecter(ech, api)
    pagines = [u for u in api.urls if _pid(1) in u]
    assert [("offset" in u) for u in pagines] == [False, True, True]
    assert all("order=batiment_groupe_id.asc" in u for u in pagines)
    simple = next(u for u in api.urls if _pid(2) in u)
    assert "order=" not in simple and "offset" not in simple
    assert "limit=10" in simple
    assert "proprietaire" not in simple and "l_siren" not in simple


def test_pause_between_requests_is_at_least_one_second() -> None:
    ech, _, rows = _monde({_pid(1): 1, _pid(2): 1})
    horloge_s = iter(range(0, 1000))
    pauses: list[float] = []
    _collecter(
        ech,
        FauxApi(rows),
        pause=1.0,
        sleep=pauses.append,
        horloge=lambda: float(next(horloge_s)) * 0.25,
    )
    assert len(pauses) == 2  # three requests (vintage + two parcels)
    assert all(0 < p <= 1.0 for p in pauses)


def test_over_budget_is_refused_before_any_request() -> None:
    ech, _, rows = _monde({_pid(1): 50, _pid(2): 1})
    api = FauxApi(rows)
    cout = be.cout_total(ech["parcelles"])["total"]
    with pytest.raises(be.BudgetDepasseError, match="au-delà du budget"):
        _collecter(ech, api, budget=cout - 1)
    assert api.urls == []
    assert _collecter(ech, api, budget=cout)["requetes_depensees"] == cout


def test_budget_also_counts_retries() -> None:
    ech, _, rows = _monde({_pid(1): 1, _pid(2): 1})
    cout = be.cout_total(ech["parcelles"])["total"]  # 3
    api = FauxApi(rows, statut=lambda n: 500 if n == 2 else 200)
    collecte = _collecter(ech, api, budget=cout)
    assert collecte["requetes_depensees"] == cout  # never above the budget
    assert collecte["arret"] == "budget"
    assert collecte["parcelles"][-1]["statut"] == "non_collecte"


def test_reserve_stops_the_collection_and_marks_the_rest() -> None:
    ech, _, rows = _monde({_pid(i): 1 for i in range(1, 6)})
    api = FauxApi(rows, quota=7_003)  # reserve 7000: stops after 4 requests
    collecte = _collecter(ech, api)
    assert collecte["arret"] == "reserve"
    assert collecte["requetes_depensees"] == 4
    statuts = [r["statut"] for r in collecte["parcelles"]]
    assert statuts == ["collecte"] * 3 + ["non_collecte"] * 2
    assert collecte["quota_derniere_reponse"] == 6_999


def test_first_429_stops_everything() -> None:
    ech, attendu, rows = _monde({_pid(i): 1 for i in range(1, 5)})
    api = FauxApi(rows, statut=lambda n: 429 if n == 3 else 200)
    collecte = _collecter(ech, api)
    assert collecte["arret"] == "429"
    assert collecte["requetes_depensees"] == 3
    assert len(api.urls) == 3
    statuts = [r["statut"] for r in collecte["parcelles"]]
    assert statuts == ["collecte", "non_collecte", "non_collecte", "non_collecte"]
    assert be.comparer(ech, attendu, collecte)["exit"] == 1


def test_server_errors_are_retried_once_then_panne_api() -> None:
    ech, attendu, rows = _monde({_pid(1): 1, _pid(2): 1})
    api = FauxApi(rows, statut=lambda n: 503 if n in (2, 3) else 200)
    collecte = _collecter(ech, api)
    assert [r["statut"] for r in collecte["parcelles"]] == ["panne_api", "collecte"]
    assert collecte["requetes_depensees"] == 1 + 2 + 1
    comparaison = be.comparer(ech, attendu, collecte)
    assert comparaison["classes"]["panne_api"] == 1
    assert comparaison["exit"] == 1


def test_three_consecutive_failures_stop_the_collection() -> None:
    ech, _, rows = _monde({_pid(i): 1 for i in range(1, 7)})
    api = FauxApi(rows, statut=lambda n: 404 if n > 1 else 200)
    collecte = _collecter(ech, api)
    assert collecte["arret"] == "pannes_consecutives"
    assert [r["statut"] for r in collecte["parcelles"]] == (
        ["panne_api"] * 3 + ["non_collecte"] * 3
    )


# -------------------------------------------------------------- classes --


def _ecart_annee() -> tuple[dict, dict, dict]:
    ech, attendu, rows = _monde({_pid(1): 2})
    rows[_pid(1)][0]["annee_construction"] = 1990
    return ech, attendu, rows


def test_gap_is_explained_only_by_an_exact_written_proof() -> None:
    ech, attendu, rows = _ecart_annee()
    collecte = _collecter(ech, FauxApi(rows))
    nu = be.comparer(ech, attendu, collecte)
    assert nu["classes"]["ecart_a_consigner"] == 1
    ecart = nu["ecarts"][0]
    assert (ecart["groupe"], ecart["champ"], ecart["fichier"], ecart["api"]) == (
        _gid(0),
        "annee_construction",
        1984,
        1990,
    )
    explication = {
        "parcelle": _pid(1),
        "groupe": _gid(0),
        "champ": "annee_construction",
        "fichier": 1984,
        "api": 1990,
        "preuve": "ligne 12 de csv/batiment_groupe_ffo_bat.csv du zip 2025-07.a",
    }
    ok = be.comparer(ech, attendu, collecte, [explication])
    assert ok["classes"]["ecart_explique"] == 1
    assert ok["exit"] == 0
    assert ok["ecarts"][0]["preuve"] == explication["preuve"]
    assert ok["explications_inutilisees"] == []
    for cle, valeur in (
        ("groupe", _gid(1)),
        ("parcelle", _pid(2)),
        ("champ", "mat_mur_txt"),
        ("api", 1991),
        ("fichier", 1985),
        ("preuve", ""),
        ("preuve", None),
    ):
        faux = be.comparer(ech, attendu, collecte, [explication | {cle: valeur}])
        assert faux["classes"]["ecart_a_consigner"] == 1, cle
        assert faux["exit"] == 1
        assert faux["explications_inutilisees"] == [0]


def test_each_field_is_compared_group_by_group() -> None:
    ech, attendu, rows = _monde({_pid(1): 3})
    rows[_pid(1)][1]["mat_mur_txt"] = "PIERRE"
    rows[_pid(1)][2]["mat_toit_txt"] = None
    rows[_pid(1)][2]["annee_construction"] = None
    comparaison = be.comparer(ech, attendu, _collecter(ech, FauxApi(rows)))
    assert sorted((e["groupe"], e["champ"]) for e in comparaison["ecarts"]) == [
        (_gid(1), "mat_mur_txt"),
        (_gid(2), "annee_construction"),
        (_gid(2), "mat_toit_txt"),
    ]


def test_duplicate_group_in_api_is_a_gap() -> None:
    ech, attendu, rows = _monde({_pid(1): 2})
    rows[_pid(1)].append(dict(rows[_pid(1)][0]))
    comparaison = be.comparer(ech, attendu, _collecter(ech, FauxApi(rows)))
    assert [(e["champ"], e["fichier"], e["api"]) for e in comparaison["ecarts"]] == [
        ("occurrences", 1, 2)
    ]


def test_parcel_without_relation_is_egal_on_an_empty_answer() -> None:
    ech, attendu, rows = _monde({_pid(1): 0})
    api = FauxApi(rows)
    collecte = _collecter(ech, api)
    assert collecte["parcelles"][0]["pages_demandees"] == 1
    assert be.comparer(ech, attendu, collecte)["classes"]["egal"] == 1


def test_parcel_missing_from_the_collection_is_non_collecte() -> None:
    ech, attendu, rows = _monde({_pid(1): 1, _pid(2): 1})
    collecte = _collecter(ech, FauxApi(rows))
    collecte["parcelles"].pop()
    comparaison = be.comparer(ech, attendu, collecte)
    assert comparaison["classes"]["non_collecte"] == 1
    assert comparaison["exit"] == 1


def test_classes_by_stratum_add_up() -> None:
    ech, attendu, rows = _monde({_pid(1): 1, _pid(2): 12})
    rows[_pid(2)][3]["annee_construction"] = 2001
    comparaison = be.comparer(ech, attendu, _collecter(ech, FauxApi(rows)))
    assert comparaison["par_strate"]["S2"]["egal"] == 1
    assert comparaison["par_strate"]["S5"]["ecart_a_consigner"] == 1
    assert sum(comparaison["classes"].values()) == len(ech["parcelles"])


# -------------------------------------------------------------- vintage --


def test_unknown_api_vintage_is_stated_and_gaps_stay_classified() -> None:
    ech, attendu, rows = _ecart_annee()
    collecte = _collecter(ech, FauxApi(rows, vintage=None))
    assert collecte["millesime_api"] is None
    comparaison = be.comparer(ech, attendu, collecte)
    assert comparaison["millesime"]["api"] is None
    assert comparaison["millesime"]["egal"] is None
    assert "inconnu" in comparaison["millesime"]["note"]
    assert comparaison["classes"]["ecart_a_consigner"] == 1
    assert comparaison["exit"] == 1


def test_known_vintage_equal_or_different() -> None:
    ech, attendu, rows = _monde({_pid(1): 1})
    egal = _collecter(ech, FauxApi(rows, vintage="mise a jour 2026-02.b"))
    assert be.comparer(ech, attendu, egal)["millesime"]["egal"] is True
    autre = _collecter(ech, FauxApi(rows, vintage="mise a jour 2025-07.a"))
    note = be.comparer(ech, attendu, autre)["millesime"]
    assert note["egal"] is False
    assert note["api"] == "2025-07.a"
    assert "différent" in note["note"]


def test_vintage_read_costs_one_counted_request() -> None:
    ech, _, rows = _monde({_pid(1): 1})
    api = FauxApi(rows)
    collecte = _collecter(ech, api)
    assert collecte["requetes_depensees"] == 2
    assert len(api.urls) == 2
    assert collecte["quota_premiere_reponse"] == 7_799
    assert re.search(r"/v1/bdnb/?$", api.urls[0])


# ------------------------------------------------------------------ cli --


def test_cli_chain_writes_files_and_exit_code(zips: Path, tmp_path: Path) -> None:
    sortie = tmp_path / "out"
    assert (
        be.main(["echantillon", "--zips", str(zips), "--out", str(sortie / "e.json")])
        == 0
    )
    ech = json.loads((sortie / "e.json").read_text(encoding="utf-8"))
    assert ech["cout"]["total"] == ech["cout"]["pages"] + 1
    # The real sample targets are larger than the mini files: fixed parcels need
    # the real data, so rebuild a sample that fits the fixture for the chain.
    regle = be.charger_regle(be.lister_zips(zips))
    petit = be.choisir_echantillon(
        regle, [], cibles={"S1": 1, "S2": 1}, fixes=(_pid(0), _pid(1))
    )
    (sortie / "petit.json").write_text(json.dumps(petit), encoding="utf-8")
    assert be.main(
        ["attendu", "--zips", str(zips), "--echantillon", str(sortie / "petit.json"),
         "--out", str(sortie / "a.json")]
    ) == 0  # fmt: skip
    attendu = json.loads((sortie / "a.json").read_text(encoding="utf-8"))
    api = FauxApi(
        {
            pid: [{**g, "l_parcelle_id": [pid]} for g in lignes]
            for pid, lignes in attendu["parcelles"].items()
        }
    )
    code = be.main(
        ["collecte", "--echantillon", str(sortie / "petit.json"),
         "--out", str(sortie / "c.json"), "--pause", "0"],
        fetch=api,
        sleep=lambda _: None,
    )  # fmt: skip
    assert code == 0
    commande = [
        "compare", "--echantillon", str(sortie / "petit.json"),
        "--attendu", str(sortie / "a.json"), "--collecte", str(sortie / "c.json"),
        "--out", str(sortie / "r.json"),
    ]  # fmt: skip
    assert be.main(commande) == 0
    rapport = json.loads((sortie / "r.json").read_text(encoding="utf-8"))
    assert rapport["classes"]["egal"] == len(petit["parcelles"])
    collecte = json.loads((sortie / "c.json").read_text(encoding="utf-8"))
    collecte["parcelles"][0]["groupes"][0]["annee_construction"] = 1
    (sortie / "c.json").write_text(json.dumps(collecte), encoding="utf-8")
    assert be.main(commande) == 1


def test_cli_refuses_an_over_budget_collection(tmp_path: Path) -> None:
    ech, _, _ = _monde({_pid(1): 50})
    chemin = tmp_path / "e.json"
    chemin.write_text(json.dumps(ech), encoding="utf-8")
    appels: list[str] = []

    def jamais(url: str) -> be.Reponse:
        appels.append(url)
        return be.Reponse(200, {}, b"[]")

    code = be.main(
        [
            "collecte",
            "--echantillon",
            str(chemin),
            "--budget",
            "5",
            "--out",
            str(tmp_path / "c.json"),
        ],
        fetch=jamais,
    )
    assert code == be.EXIT_USAGE
    assert appels == []
    assert not (tmp_path / "c.json").exists()


# ------------------------------------------------------ drift vs pipeline --


def mini_zips(directory: Path) -> Path:
    """The mini-export of the pipeline tests, under the ``dep*.zip`` names of
    this script (the bytes are the producer-shaped zips)."""
    from tests.etl.bdnb_fixture import build_export, mini_exports

    for dept, export in mini_exports().items():
        built = build_export(directory / "producteur", dept, export)
        (directory / f"dep{dept.lower()}.zip").write_bytes(built.read_bytes())
    return directory


def rule_rows(directory: Path) -> dict[str, list[dict[str, Any]]]:
    """``attendu`` on every parcel of the zips of *directory*."""
    regle = be.charger_regle(be.lister_zips(directory))
    echantillon = {
        "millesime_fichier": regle.millesime,
        "parcelles": [
            {"parcelle": p, "groupes": len(regle.relations[p])}
            for p in sorted(regle.relations)
        ],
    }
    return be.attendu(regle, echantillon)["parcelles"]


def rows_by_parcel(rows) -> dict[str, list[dict[str, Any]]]:
    """Published rows (code, parcelle, groupe, principale, annee, mur, toit)
    in the shape of ``attendu``, per parcel ordered by group."""
    parcelles: dict[str, list[dict[str, Any]]] = {}
    for _code, parcelle, groupe, principale, annee, mur, toit in rows:
        parcelles.setdefault(parcelle, []).append(
            {
                "batiment_groupe_id": groupe,
                "annee_construction": annee,
                "mat_mur_txt": mur,
                "mat_toit_txt": toit,
                "parcelle_principale": principale,
            }
        )
    return {
        p: sorted(lignes, key=lambda r: r["batiment_groupe_id"])
        for p, lignes in parcelles.items()
    }


def test_the_rule_gives_the_published_rows_of_the_mini_export(tmp_path: Path) -> None:
    from tests.etl.bdnb_fixture import attendu as published

    # Same parcels, same groups, same attributes and principal flag: the
    # integration test checks these written rows against the real swap.
    assert rule_rows(mini_zips(tmp_path)) == rows_by_parcel(published())


def test_the_rule_reads_the_records_the_transform_reads(tmp_path: Path) -> None:
    from app.etl_bdnb.transform import transform_export

    zips = mini_zips(tmp_path)
    regle = be.charger_regle(be.lister_zips(zips))
    lues = ecartees = ffo_lues = 0
    for path in sorted(zips.glob("dep*.zip")):
        comptes = transform_export(path, tmp_path / f"{path.stem}.csv", "85")
        lues += comptes.relations_lues
        ecartees += comptes.relations_ecartees
        ffo_lues += comptes.ffo_lues
    assert regle.compteurs["relations_lues"] == lues == 22
    assert regle.compteurs["relations_ecartees"] == ecartees == 1
    assert regle.compteurs["ffo_lues"] == ffo_lues == 20
    assert regle.compteurs["ffo_divergents"] == 0
