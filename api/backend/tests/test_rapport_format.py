"""Format of the address report, its registry, summary and cache (04.7-01).

No database: the model refuses a valence without an answer, the registry
discovers one file per theme, the summary splits the points by real scale
(a fact of the commune is never a point of the address) and the cache never
keeps an outage.
"""

import importlib
import sys
import textwrap
from datetime import date

import pytest
from pydantic import ValidationError

from app.adresse.schemas import (
    CommuneRef,
    CouvertureAdresse,
    DepartementRef,
    IdentiteAdresse,
    RelationParcelle,
)
from app.common.schemas import DatasetHosting, OutOfCoverageReason
from app.common.source_catalog import DATASET_CATALOG
from app.rapport import cache, registre, socle
from app.rapport.resume import CHIFFRES_CLES, resumer
from app.rapport.schemas import (
    Chiffre,
    Granularite,
    RapportAdresse,
    Theme,
)
from app.scope.dependencies import RouteHosting

pytestmark = pytest.mark.unit

SOURCE = socle.source("dvf")
ETATS = ("ok", "zero", "manque", "hors_couverture", "panne", "non_concerne")


def _theme(**champs) -> Theme:
    base = {
        "id": "t",
        "section": "marche",
        "titre": "Titre",
        "etat": "ok",
        "ton": "neutre",
        "granularite": Granularite(niveau="adresse"),
        "verdict": "Une phrase.",
        "sources": [SOURCE],
    }
    return Theme(**{**base, **champs})


# --- Model ------------------------------------------------------------------


def test_verdict_of_140_characters_is_the_limit():
    assert _theme(verdict="x" * 140).verdict
    with pytest.raises(ValidationError):
        _theme(verdict="x" * 141)


def test_at_most_three_figures():
    chiffre = Chiffre(id="c", libelle="C", valeur=1, unite="count")
    assert len(_theme(chiffres=[chiffre] * 3).chiffres) == 3
    with pytest.raises(ValidationError):
        _theme(chiffres=[chiffre] * 4)


@pytest.mark.parametrize("etat", ["manque", "hors_couverture", "panne"])
def test_a_state_without_answer_has_no_valence_and_no_figure(etat):
    assert _theme(etat=etat, ton="info").ton == "info"
    for ton in ("vigilance", "rassurant", "neutre"):
        with pytest.raises(ValidationError):
            _theme(etat=etat, ton=ton)
    with pytest.raises(ValidationError):
        _theme(
            etat=etat,
            ton="info",
            chiffres=[Chiffre(id="c", libelle="C", valeur=0, unite="count")],
        )


@pytest.mark.parametrize("etat", ["ok", "zero", "non_concerne"])
def test_the_tone_info_belongs_to_the_states_without_answer(etat):
    with pytest.raises(ValidationError):
        _theme(etat=etat, ton="info")


def test_a_theme_that_does_not_concern_the_address_is_never_a_point():
    assert _theme(etat="non_concerne", ton="rassurant").etat == "non_concerne"
    with pytest.raises(ValidationError):
        _theme(etat="non_concerne", ton="vigilance")


def test_the_six_states_are_the_closed_set():
    for etat in ETATS:
        ton = "info" if etat in ("manque", "hors_couverture", "panne") else "neutre"
        assert _theme(etat=etat, ton=ton).etat == etat
    with pytest.raises(ValidationError):
        _theme(etat="value")


def test_a_radius_carries_its_length_and_only_a_radius():
    assert Granularite(niveau="rayon", rayon_m=1000).rayon_m == 1000
    with pytest.raises(ValidationError):
        Granularite(niveau="rayon")
    with pytest.raises(ValidationError):
        Granularite(niveau="commune", rayon_m=500)


def test_an_answer_with_a_sub_source_down_never_reassures():
    assert _theme(ton="vigilance", pannes_partielles=["rnb"]).pannes_partielles
    with pytest.raises(ValidationError):
        _theme(etat="zero", ton="rassurant", pannes_partielles=["rnb"])


def test_the_report_reads_the_rnb_through_its_imported_links():
    assert socle.source("rnb").mode == "import"


def test_a_theme_names_at_least_one_source():
    with pytest.raises(ValidationError):
        _theme(sources=[])


# --- Value states -----------------------------------------------------------


def test_an_outage_of_the_address_page_is_never_a_zero_nor_a_gap():
    assert socle.etat_de_valeur("temporary_error") == "panne"
    assert socle.etat_de_valeur("out_of_coverage") == "hors_couverture"
    assert socle.etat_de_valeur("not_applicable") == "non_concerne"
    for gap in (
        "not_reported",
        "not_available",
        "insufficient_sample",
        "suppressed",
        "not_qualified",
        "zero_denominator",
    ):
        assert socle.etat_de_valeur(gap) == "manque"
    assert socle.etat_de_valeur("value") == "ok"


def test_sources_say_their_mode():
    assert socle.source("georisques").mode == "direct"
    assert socle.source("dvf", "2025-06").model_dump()["millesime"] == "2025-06"
    assert socle.source("dvf").mode == "import"
    assert socle.source("dvf").licence == DATASET_CATALOG["dvf"].licence


@pytest.mark.parametrize(
    ("n", "attendu"),
    [
        (4.25, "4,3"),  # a binary tie: `.1f` wrote 4,2
        (14.35, "14,4"),  # just under the tie in binary: `.1f` wrote 14,3
        (3.96, "4"),  # never padded: `.1f` wrote 4,0
        (0, "0"),
    ],
)
def test_a_number_is_rounded_half_up_on_its_decimal_writing(n, attendu):
    assert socle.nombre(n) == attendu


def test_french_writing_of_numbers_and_months():
    assert socle.nombre(2450) == "2\u202f450"
    assert socle.nombre(12.4) == "12,4"
    assert socle.mois_annee("2023-03-14") == "mars 2023"
    assert socle.pluriel(1, "vente") == "1 vente"
    assert socle.pluriel(3, "vente") == "3 ventes"


# --- Parcel relation --------------------------------------------------------

DEFINITION = socle.DefinitionTheme(
    id="t",
    section="batiment",
    ordre=1,
    titre="T",
    granularite=Granularite(niveau="parcelle"),
    sources=("cadastre",),
    lire=None,  # type: ignore[arg-type]
    rattachement_entite="parcelle_du_point",
)


@pytest.mark.parametrize(
    ("relation", "etat", "motif"),
    [
        (None, "manque", "parcelle_indeterminee"),
        (
            RelationParcelle(
                etat="ambigue", nombre_candidats=2, motif="parcelles_multiples"
            ),
            "manque",
            "parcelles_multiples",
        ),
        (
            RelationParcelle(etat="point_hors_parcelle", motif="point_hors_parcelle"),
            "manque",
            "point_hors_parcelle",
        ),
        (RelationParcelle(etat="indisponible"), "panne", "lecture_indisponible"),
        (
            RelationParcelle(
                etat="hors_couverture",
                motif=OutOfCoverageReason.DATASET_NON_HEBERGE,
            ),
            "hors_couverture",
            OutOfCoverageReason.DATASET_NON_HEBERGE,
        ),
    ],
)
def test_a_parcel_that_is_not_unique_attributes_nothing(relation, etat, motif):
    theme = socle.parcelle_unique(DEFINITION, relation)
    assert isinstance(theme, Theme)
    assert theme.etat == etat and theme.ton == "info" and not theme.chiffres
    assert theme.rattachement.confiance == "incertaine"
    assert theme.rattachement.motif == motif
    if relation is not None and relation.nombre_candidats:
        assert theme.rattachement.candidats == relation.nombre_candidats


def test_a_unique_parcel_is_no_absence():
    unique = RelationParcelle(
        etat="unique",
        id_parcelle="85191000AB0012",
        nombre_candidats=1,
        rattachement="parcelle_du_point",
    )
    assert socle.parcelle_unique(DEFINITION, unique) == "85191000AB0012"


def test_a_mandatory_attachment_is_served_in_every_state():
    for etat in ("manque", "hors_couverture", "panne"):
        theme = socle.sans_reponse(DEFINITION, etat, "motif")
        assert theme.rattachement is not None
        assert theme.rattachement.entite == "parcelle_du_point"
        assert theme.rattachement.confiance == "incertaine"


# --- Registry ---------------------------------------------------------------

OBLIGATOIRES = ("dpe", "batiment", "copropriete", "parcelle", "construction")


def test_every_theme_file_is_discovered_once_in_report_order():
    ids = [d.id for d in registre.THEMES]
    assert len(ids) == len(set(ids)) == 43
    sections = [d.section for d in registre.THEMES]
    assert sections == sorted(
        sections, key=("marche", "risques", "batiment", "vivre").index
    )
    assert {"dpe", "prix_commune", "loyers", "construction", "revenus"} <= set(ids)


def test_the_attachment_is_mandatory_for_dpe_building_condominium_and_parcel():
    par_id = {d.id: d for d in registre.THEMES}
    for id_ in OBLIGATOIRES:
        assert par_id[id_].rattachement_entite, id_


def test_every_theme_writes_its_tone_rule_and_reads_catalogue_sources():
    for definition in registre.THEMES:
        module = sys.modules[definition.lire.__module__]
        assert "Tone rule" in (module.__doc__ or ""), definition.id
        assert definition.sources and all(
            s in DATASET_CATALOG for s in definition.sources
        )


def test_the_route_declares_every_source_of_every_theme():
    for definition in registre.THEMES:
        assert set(definition.sources) <= set(registre.CLES_RAPPORT)
    assert {"ban", "cadastre"} <= set(registre.CLES_RAPPORT)


def _paquet(tmp_path, monkeypatch, nom: str, fichiers: dict[str, str]):
    racine = tmp_path / nom
    racine.mkdir()
    (racine / "__init__.py").write_text("")
    for fichier, code in fichiers.items():
        (racine / fichier).write_text(textwrap.dedent(code))
    monkeypatch.syspath_prepend(str(tmp_path))
    return importlib.import_module(nom)


THEME_FICHIER = """
    from app.rapport.schemas import Granularite
    from app.rapport.socle import DefinitionTheme
    THEME = DefinitionTheme(
        id={id!r}, section={section!r}, ordre={ordre}, titre="T",
        granularite=Granularite(niveau="adresse"), sources=("ban",), lire=None,
    )
"""


def test_a_new_file_is_a_new_theme_and_order_is_section_then_rank(
    tmp_path, monkeypatch
):
    paquet = _paquet(
        tmp_path,
        monkeypatch,
        "themes_essai_ordre",
        {
            "z.py": THEME_FICHIER.format(id="z", section="marche", ordre=1),
            "a.py": THEME_FICHIER.format(id="a", section="vivre", ordre=1),
            "b.py": THEME_FICHIER.format(id="b", section="marche", ordre=2),
        },
    )
    assert [d.id for d in registre.charger_themes(paquet)] == ["z", "b", "a"]


def test_two_files_declaring_one_id_fail(tmp_path, monkeypatch):
    paquet = _paquet(
        tmp_path,
        monkeypatch,
        "themes_essai_doublon",
        {
            "a.py": THEME_FICHIER.format(id="x", section="marche", ordre=1),
            "b.py": THEME_FICHIER.format(id="x", section="vivre", ordre=1),
        },
    )
    with pytest.raises(ValueError, match="declared twice"):
        registre.charger_themes(paquet)


def test_a_file_without_theme_fails(tmp_path, monkeypatch):
    paquet = _paquet(tmp_path, monkeypatch, "themes_essai_vide", {"a.py": "X = 1\n"})
    with pytest.raises(TypeError, match="no THEME"):
        registre.charger_themes(paquet)


# --- Summary ----------------------------------------------------------------


def test_a_fact_of_the_commune_is_counted_apart_from_the_address():
    themes = [
        _theme(
            id="sismicite", ton="vigilance", granularite=Granularite(niveau="commune")
        ),
        _theme(
            id="argiles", ton="vigilance", granularite=Granularite(niveau="adresse")
        ),
        _theme(
            id="transports",
            etat="zero",
            ton="vigilance",
            granularite=Granularite(niveau="rayon", rayon_m=1000),
        ),
        _theme(
            id="dep", ton="vigilance", granularite=Granularite(niveau="departement")
        ),
    ]
    resume = resumer(themes)
    assert [p.theme for p in resume.points_adresse] == ["argiles", "transports"]
    assert [p.theme for p in resume.points_commune] == ["sismicite", "dep"]


def test_only_an_answer_counts_and_an_outage_is_never_a_point():
    themes = [
        _theme(id="a", etat="panne", ton="info"),
        _theme(id="b", etat="manque", ton="info"),
        _theme(id="c", etat="ok", ton="neutre"),
        _theme(id="d", etat="zero", ton="rassurant"),
        _theme(id="e", etat="non_concerne", ton="rassurant"),
        _theme(id="f", etat="ok", ton="rassurant"),
    ]
    resume = resumer(themes)
    assert resume.points_adresse == resume.points_commune == []
    # 04.7-17: a theme that does not concern the address is said apart, as
    # the front does (UI-SPEC § 7, ``non_concernes``), never a favourable.
    assert [p.theme for p in resume.rassurants] == ["d", "f"]
    assert [p.theme for p in resume.non_concernes] == ["e"]


def test_the_four_key_figures_take_the_state_of_their_theme():
    prix = _theme(
        id="prix_commune",
        granularite=Granularite(niveau="commune"),
        chiffres=[
            Chiffre(id="maison", libelle="M", valeur=2450, unite="euro_per_m2"),
            Chiffre(id="appartement", libelle="A", valeur=3100, unite="euro_per_m2"),
        ],
    )
    loyers = _theme(id="loyers", etat="panne", ton="info", motif="lecture_indisponible")
    dpe = _theme(id="dpe", etat="ok", chiffres=[])
    resume = resumer([prix, loyers, dpe])
    cles = {c.id: c for c in resume.chiffres_cles}
    assert [c.id for c in resume.chiffres_cles] == [c[0] for c in CHIFFRES_CLES]
    assert cles["prix_m2_median"].etat == "ok"
    assert [c.valeur for c in cles["prix_m2_median"].chiffres] == [2450, 3100]
    assert cles["prix_m2_median"].granularite.niveau == "commune"
    assert cles["loyer_m2"].etat == "panne" and not cles["loyer_m2"].chiffres
    assert (cles["dpe_dominant"].etat, cles["dpe_dominant"].motif) == (
        "manque",
        "chiffre_absent",
    )
    assert (cles["annee_construction"].etat, cles["annee_construction"].motif) == (
        "manque",
        "theme_absent",
    )


# --- Cache ------------------------------------------------------------------

HEBERGE = DatasetHosting(etat="heberge", departements=["85"])


def _rapport(*themes: Theme) -> RapportAdresse:
    return RapportAdresse(
        adresse=IdentiteAdresse(
            ban_id="85191_0420_00012",
            commune=CommuneRef(code_insee="85191", nom="La Roche-sur-Yon"),
            departement=DepartementRef(code="85", nom="Vendée"),
        ),
        couverture=CouvertureAdresse(etat="heberge"),
        date=date(2026, 10, 30),
        resume=resumer(list(themes)),
        themes=list(themes),
    )


@pytest.fixture(autouse=True)
def _cache_vide():
    cache.vider()
    yield
    cache.vider()


def test_a_report_with_an_outage_is_never_kept():
    cle = cache.cle_cache(
        "85191_0420_00012", date(2026, 10, 30), RouteHosting({"ban": HEBERGE})
    )
    cache.ecrire(cle, _rapport(_theme(etat="panne", ton="info")))
    assert cache.lire(cle) is None
    rapport = _rapport(_theme())
    cache.ecrire(cle, rapport)
    assert cache.lire(cle) is rapport


def test_a_report_with_a_partial_outage_is_never_kept():
    cle = cache.cle_cache(
        "85191_0420_00012", date(2026, 10, 30), RouteHosting({"ban": HEBERGE})
    )
    cache.ecrire(cle, _rapport(_theme(pannes_partielles=["prescriptions"])))
    assert cache.lire(cle) is None


def test_the_key_changes_with_the_day_and_the_hosting():
    ban_id = "85191_0420_00012"
    jour = date(2026, 10, 30)
    hors = DatasetHosting(
        etat="hors_couverture", motif=OutOfCoverageReason.DATASET_NON_HEBERGE
    )
    base = cache.cle_cache(ban_id, jour, RouteHosting({"ban": HEBERGE}))
    assert base != cache.cle_cache(
        ban_id, date(2026, 10, 31), RouteHosting({"ban": HEBERGE})
    )
    assert base != cache.cle_cache(ban_id, jour, RouteHosting({"ban": hors}))
    assert base == cache.cle_cache(ban_id, jour, RouteHosting({"ban": HEBERGE}))
