"""Fields of the UI-SPEC § 7 served by the API (04.7-17), without a database.

The expected values are those of the front rules (``adresse-rapport.ts``),
checked against the front on 2026-10-31: 61 cases equal, the examples of
``contracts/rapport/exemples/`` included (04.7-17-SUMMARY).
"""

import json
import re
from pathlib import Path

import pytest

from app.adresse.relations import PointBan
from app.rapport import themes as paquet_themes
from app.rapport.alignement import (
    accroche,
    alerte,
    aligner,
    libelle_parcelle,
    phrase_rattachement,
    statut_texte,
)
from app.rapport.carte import carte_de
from app.rapport.libelles import date_fr, distance_fr, nombre_fr, valeur_chiffre
from app.rapport.resume import est_de_l_adresse, resumer
from app.rapport.schemas import Chiffre, Granularite, Rattachement, Theme

pytestmark = pytest.mark.unit

NBSP, NNBSP = "\u00a0", "\u202f"
SOURCE = {
    "cle": "georisques",
    "nom": "Géorisques",
    "producteur": "BRGM",
    "url": "https://x",
    "licence": "LO",
    "millesime": None,
    "mode": "direct",
}


def _theme(**champs) -> Theme:
    return Theme.model_validate(
        {
            "id": "x",
            "section": "risques",
            "titre": "X",
            "etat": "ok",
            "ton": "neutre",
            "granularite": {"niveau": "commune", "rayon_m": None},
            "verdict": "Verdict.",
            "sources": [SOURCE],
            **champs,
        }
    )


def _chiffre(valeur, unite, libelle="Valeur"):
    return [{"id": "c", "libelle": libelle, "valeur": valeur, "unite": unite}]


# --- Écriture ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "decimales", "attendu"),
    [(3385, None, f"3{NNBSP}385"), (98.5, None, "98,5"), (12.456, 2, "12,46"),
     (2.5, 0, "3"), (40.0, None, "40"), (53.37, None, "53,37")],
)  # fmt: skip
def test_numbers_are_written_as_the_front_writes_them(n, decimales, attendu):
    assert nombre_fr(n, decimales) == attendu


def test_dates_and_distances():
    assert date_fr("2026-11-01") == "1er novembre 2026"
    assert date_fr("2026-03") == "mars 2026"
    assert date_fr("pas une date") == "pas une date"
    assert distance_fr(102.5) == f"103{NBSP}m"
    assert distance_fr(1712) == f"1,7{NBSP}km"


# --- Accroche et statut ------------------------------------------------------------


@pytest.mark.parametrize(
    ("chiffres", "attendu"),
    [
        (_chiffre(11, "count", "DPE déclarés"), "11 DPE déclarés"),
        (_chiffre(1, "count", "Lot"), "1 lot"),
        (_chiffre("F", "classe_dpe"), "Classe F"),
        (_chiffre("C\u2013F", "classe_dpe"), "Classes C\u2013F"),
        (_chiffre(3385.4, "euro_per_m2"), f"3{NNBSP}385{NBSP}€/m²"),
        (_chiffre(53.37, "pourcent"), f"53,37{NBSP}%"),
        (_chiffre(1712, "metre"), f"1,7{NBSP}km"),
        (_chiffre("2026-11-01", "date"), "1er novembre 2026"),
        (_chiffre(6.3, "pour_mille"), f"6,3{NBSP}‰"),
        (_chiffre(12.3, "degre_francais"), f"12,3{NBSP}°f"),
        (_chiffre(3, "indice_sur_6"), f"3{NBSP}sur{NBSP}6"),
        (_chiffre(14.4, "euro_per_m2_mois"), f"14,4{NBSP}€/m²/mois"),
        (_chiffre(14.5, "euro_per_m2_mois"), f"14,5{NBSP}€/m²/mois"),
        (_chiffre(15.0, "euro_per_m2_mois"), f"15{NBSP}€/m²/mois"),
    ],
)
def test_the_headline_is_the_first_figure_in_words(chiffres, attendu):
    assert accroche(_theme(chiffres=chiffres)) == attendu


@pytest.mark.parametrize(
    ("n", "libelle", "attendu"),
    [
        (1, "Sites Seveso à 5 km", "1 site Seveso à 5 km"),
        (1, "Bâtiments sur la parcelle", "1 bâtiment sur la parcelle"),
        (1, "DPE déclarés", "1 DPE déclaré"),
        (1, "Arrêtés CatNat", "1 arrêté CatNat"),
        (1, "Risques recensés", "1 risque recensé"),
        (1, "Installations classées", "1 installation classée"),
        (1, "Commerces", "1 commerce"),
        (1, "Supports 5G", "1 support 5G"),
        (1, "Ventes sur la parcelle", "1 vente sur la parcelle"),
        (1, "Sites sur la parcelle", "1 site sur la parcelle"),
        (1, "Prescriptions au point", "1 prescription au point"),
        (1, "Cavités à 500 m", "1 cavité à 500 m"),
        (1, "Mouvements de terrain à 500 m", "1 mouvement de terrain à 500 m"),
        (1, "Lots", "1 lot"),
        # French: zero takes the singular, two the plural the label already has.
        (0, "Commerces", "0 commerce"),
        (2, "Sites Seveso à 5 km", "2 sites Seveso à 5 km"),
        (2, "DPE déclarés", "2 DPE déclarés"),
    ],
)
def test_a_count_agrees_with_its_label(n, libelle, attendu):
    assert accroche(_theme(chiffres=_chiffre(n, "count", libelle))) == attendu


DOSSIER_THEMES = Path(paquet_themes.__file__).parent
_UNITE = re.compile(r'unite="([a-z0-9_]+)"')
# A literal label of a counted figure, whatever the value expression.
_COMPTE = re.compile(r'libelle="([^"]+)",\s+valeur=[^\n]+\n\s+unite="count"')
# Counted figures never first in their theme: their label stays in the detail.
JAMAIS_EN_TETE = {"Étages"}


def test_every_unit_a_theme_serves_is_written_for_a_reader():
    # The next raw code fails here rather than on the page (« 6,3 pour_mille »).
    unites = {
        unite
        for fichier in DOSSIER_THEMES.glob("*.py")
        for unite in _UNITE.findall(fichier.read_text(encoding="utf-8"))
    }
    assert {"pour_mille", "degre_francais", "indice_sur_6"} <= unites
    for unite in sorted(unites - {"date"}):  # a date is a text, never a number
        chiffre = Chiffre(id="c", libelle="c", valeur=1, unite=unite)
        assert unite not in valeur_chiffre(chiffre)


def test_no_theme_rounds_a_figure_on_the_binary_float():
    # `round` writes 14.35 as 14.3 and 39.125 as 39.12: one rule, `arrondi`.
    for fichier in DOSSIER_THEMES.glob("*.py"):
        assert "round(" not in fichier.read_text(encoding="utf-8"), fichier.name


def test_every_label_a_headline_counts_agrees_at_one():
    libelles = {
        libelle
        for fichier in DOSSIER_THEMES.glob("*.py")
        for libelle in _COMPTE.findall(fichier.read_text(encoding="utf-8"))
    }
    assert "Sites Seveso à 5 km" in libelles
    for libelle in sorted(libelles - JAMAIS_EN_TETE):
        ecrit = accroche(_theme(chiffres=_chiffre(1, "count", libelle)))
        assert ecrit is not None
        assert not re.search(r"^1 (DPE )?\S+s\b", ecrit), ecrit


@pytest.mark.parametrize(
    ("champs", "attendu"),
    [
        ({"etat": "zero"}, "Aucun"),
        ({}, None),  # served without figure: no headline, never invented
        ({"etat": "panne", "ton": "info", "motif": "x"}, "Source indisponible"),
        ({"etat": "hors_couverture", "ton": "info", "motif": "x"}, "Hors couverture"),
        ({"etat": "non_concerne"}, "Sans objet"),
        (
            {"etat": "manque", "ton": "info", "motif": "taux_non_publie"},
            "Taux non publié",
        ),
        (
            {"etat": "manque", "ton": "info", "motif": "point_hors_parcelle"},
            "Non rattaché",
        ),
        (
            {"etat": "manque", "ton": "info", "motif": "parcelle_non_verifiee"},
            "Non rattaché",
        ),
        # A reason too long for a badge is « Non publié ».
        (
            {"etat": "manque", "ton": "info", "motif": "parcelle_indeterminee"},
            "Non publié",
        ),
    ],
)
def test_the_headline_of_a_state_without_figure(champs, attendu):
    assert accroche(_theme(**champs)) == attendu


def test_a_headline_beyond_40_characters_is_never_cut():
    long = _chiffre(3, "count", "Établissements scolaires du secteur de la commune")
    assert accroche(_theme(chiffres=long)) is None


def test_a_favourable_reading_needs_an_established_link():
    lien = {"entite": "parcelle_du_point", "confiance": "probable"}
    theme = _theme(
        ton="rassurant",
        granularite={"niveau": "parcelle", "rayon_m": None},
        rattachement=lien,
        chiffres=_chiffre(3, "count", "Sites"),
    )
    assert statut_texte(theme) is None
    assert aligner(theme).ton == "neutre"
    # A vigilance is kept: the verdict may be about the uncertainty itself.
    vigilance = theme.model_copy(update={"ton": "vigilance"})
    assert aligner(vigilance).ton == "vigilance"


# --- Rattachement ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lien", "attendu"),
    [
        ({"entite": "x", "confiance": "sure"}, None),
        (
            {
                "entite": "x",
                "confiance": "probable",
                "motif": "dpe_plusieurs_batiments",
            },
            "Ces DPE peuvent venir de plusieurs bâtiments.",
        ),
        (
            {"entite": "x", "confiance": "incertaine", "candidats": 3},
            f"3 rattachements possibles{NBSP}: lien incertain.",
        ),
        ({"entite": "x", "confiance": "probable"}, "Rattachement probable."),
    ],
)
def test_the_alert_of_an_attachment_not_sure(lien, attendu):
    assert alerte(Rattachement.model_validate(lien)) == attendu


def test_the_attachment_carries_its_alert_and_its_link():
    theme = aligner(_theme(rattachement={"entite": "x", "confiance": "probable"}))
    assert theme.rattachement.alerte == "Rattachement probable."
    assert [m.model_dump() for m in theme.rattachement.maillons] == [
        {"lien": "x", "confiance": "probable"}
    ]


def test_the_attachment_sentence_of_the_head():
    sure = {"entite": "parcelle_du_point", "confiance": "sure", "cle": "44109000ZZ0177"}
    dpe = {"entite": "x", "confiance": "probable", "motif": "dpe_plusieurs_batiments"}
    assert libelle_parcelle("44109000ZZ0177") == "ZZ 177"
    themes = [
        _theme(id="parcelle", rattachement=sure),
        _theme(id="dpe", rattachement=dpe),
    ]
    assert phrase_rattachement(themes) == (
        "Le point de l\u2019adresse tombe dans la parcelle ZZ 177. "
        "Ces DPE peuvent venir de plusieurs bâtiments."
    )
    assert phrase_rattachement([_theme(id="parcelle")]) is None


HORS_PARCELLE = {
    "entite": "parcelle_du_point",
    "confiance": "incertaine",
    "motif": "point_hors_parcelle",
}
SURE = {"entite": "parcelle_du_point", "confiance": "sure", "cle": "44109000ZZ0062"}
DPE_PROBABLE = {"entite": "adresse_saisie_diagnostiqueur", "confiance": "probable"}
PHRASE_HORS_PARCELLE = "Le point de l\u2019adresse ne tombe dans aucune parcelle."
PHRASE_PARCELLE = "Le point de l\u2019adresse tombe dans la parcelle ZZ 62."


def _sans_parcelle() -> Theme:
    return _theme(
        id="parcelle",
        etat="manque",
        ton="info",
        motif="point_hors_parcelle",
        rattachement=HORS_PARCELLE,
    )


def test_an_address_without_parcel_says_only_that():
    # 85191_zz05_00011 served: « ne tombe dans aucune parcelle. Rattachement
    # probable. » No DPE at the address: nothing was attached at all.
    themes = [
        _sans_parcelle(),
        _theme(id="dpe", etat="zero", rattachement=DPE_PROBABLE),
        _theme(
            id="batiment",
            etat="manque",
            ton="info",
            motif="point_hors_parcelle",
            rattachement={**HORS_PARCELLE, "entite": "batiment_du_point"},
        ),
    ]
    assert phrase_rattachement(themes) == PHRASE_HORS_PARCELLE


def test_a_zero_dpe_leaves_no_alert_in_the_head():
    themes = [
        _theme(id="parcelle", rattachement=SURE),
        _theme(id="dpe", etat="zero", rattachement=DPE_PROBABLE),
    ]
    assert phrase_rattachement(themes) == PHRASE_PARCELLE


@pytest.mark.parametrize(
    ("parcelle", "debut"),
    [
        (_sans_parcelle, PHRASE_HORS_PARCELLE),
        (lambda: _theme(id="parcelle", rattachement=SURE), PHRASE_PARCELLE),
    ],
)
def test_the_head_names_what_a_bare_alert_speaks_of(parcelle, debut):
    # Beside the sentence of the parcel, a bare « Rattachement probable. »
    # reads as the link of that parcel: it is the one of the DPE.
    themes = [parcelle(), _theme(id="dpe", rattachement=DPE_PROBABLE)]
    assert phrase_rattachement(themes) == (
        f"{debut} Rattachement probable des DPE à l\u2019adresse."
    )


def test_the_head_names_the_building_of_a_bare_alert():
    lien = {"entite": "batiment_du_point", "confiance": "incertaine"}
    themes = [
        _theme(id="parcelle", rattachement=SURE),
        _theme(id="batiment", rattachement=lien),
    ]
    assert phrase_rattachement(themes) == (
        f"{PHRASE_PARCELLE} Rattachement incertain du bâtiment à l\u2019adresse."
    )


def test_a_zero_has_no_alert_but_keeps_its_confidence():
    lien = aligner(_theme(etat="zero", rattachement=DPE_PROBABLE)).rattachement
    assert lien.alerte is None
    assert lien.confiance == "probable"
    assert [m.confiance for m in lien.maillons] == ["probable"]


def test_a_zero_with_a_reason_keeps_its_alert():
    lien = {
        "entite": "batiment_du_point",
        "confiance": "incertaine",
        "motif": "aucun_batiment_au_point",
    }
    theme = aligner(_theme(etat="zero", rattachement=lien))
    assert theme.rattachement.alerte == "Aucun bâtiment au point."


def test_a_missing_value_keeps_the_alert_of_its_link():
    # Nantes: a building group attached, its year not reported.
    lien = {"entite": "batiment_groupe", "confiance": "probable", "cle": "bg"}
    theme = aligner(_theme(etat="manque", ton="info", motif="x", rattachement=lien))
    assert theme.rattachement.alerte == "Rattachement probable."


# --- Résumé ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("niveau", "rayon", "attendu"),
    [("adresse", None, True), ("parcelle", None, True), ("rayon", 1000, True),
     ("rayon", 1001, False), ("commune", None, False),
     ("iris", None, False)],
)  # fmt: skip
def test_a_radius_counts_for_the_address_up_to_1_km(niveau, rayon, attendu):
    assert est_de_l_adresse(Granularite(niveau=niveau, rayon_m=rayon)) is attendu


def test_the_summary_is_split_and_counts_outages():
    themes = [
        _theme(
            id="a", ton="vigilance", granularite={"niveau": "rayon", "rayon_m": 500}
        ),
        _theme(
            id="b", ton="vigilance", granularite={"niveau": "rayon", "rayon_m": 5000}
        ),
        _theme(
            id="c", ton="rassurant", granularite={"niveau": "adresse", "rayon_m": None}
        ),
        _theme(id="d", ton="rassurant"),
        _theme(id="e", etat="panne", ton="info", section="vivre"),
        _theme(id="f", etat="non_concerne"),
    ]
    resume = resumer(themes)
    assert [p.theme for p in resume.points_adresse] == ["a"]
    assert [p.theme for p in resume.points_commune] == ["b"]
    assert [p.theme for p in resume.rassurants_adresse] == ["c"]
    assert [p.theme for p in resume.rassurants_commune] == ["d"]
    assert [p.theme for p in resume.rassurants] == ["c", "d"]
    assert [p.theme for p in resume.pannes] == ["e"]
    assert resume.sources_en_panne == ["georisques"]
    assert [p.theme for p in resume.non_concernes] == ["f"]
    assert resume.total_criteres == 6
    assert resume.par_section["risques"].vigilance == 2
    assert resume.par_section["vivre"].pannes == 1


# --- Carte -------------------------------------------------------------------------


def test_the_map_is_served_on_the_site_origin_never_the_ign():
    carte = carte_de("44109_zz01_00002", PointBan(lon=-1.5536, lat=47.2184))
    assert carte.images.plan == "/carte/44109_zz01_00002/plan.png"
    assert carte.images.parcelles == "/carte/44109_zz01_00002/parcelles.png"
    assert "data.geopf.fr" not in carte.images.plan
    assert carte.emprise_m == 300
    assert carte.licence == "Licence Ouverte 2.0"
    assert carte.geoportail.startswith(
        "https://www.geoportail.gouv.fr/carte?c=-1.5536,47.2184"
    )


def test_no_map_without_a_point():
    assert carte_de("44109_zz01_00002", None) is None


# --- Revues n° 1 (Codex, Opus) ---------------------------------------------------


@pytest.mark.parametrize(
    ("m", "attendu"),
    [
        (-2.5, f"-2{NBSP}m"),
        (102.5, f"103{NBSP}m"),
        (999.4, f"999{NBSP}m"),
        (1250, f"1,3{NBSP}km"),
        # A whole number of kilometres, as the front writes it (`distanceFr`).
        (1000, f"1{NBSP}km"),
        (3000, f"3{NBSP}km"),
    ],
)
def test_a_distance_is_rounded_as_math_round(m, attendu):
    assert distance_fr(m) == attendu


def test_a_week_date_is_not_a_date_and_a_float_year_has_no_decimal():
    assert date_fr("2026-W01-1") == "2026-W01-1"
    assert date_fr("2026-13-01") == "2026-13-01"
    annee = _theme(chiffres=_chiffre(2020.0, "annee"))
    assert accroche(annee) == "2020"


@pytest.mark.parametrize(
    ("granularite", "ton"),
    [
        ({"niveau": "batiment", "rayon_m": None}, "neutre"),
        ({"niveau": "parcelle", "rayon_m": None}, "neutre"),
        ({"niveau": "commune", "rayon_m": None}, "rassurant"),
        ({"niveau": "rayon", "rayon_m": 500}, "rassurant"),
    ],
)
def test_a_favourable_entity_needs_its_link_a_place_does_not(granularite, ton):
    theme = _theme(ton="rassurant", granularite=granularite)
    assert aligner(theme).ton == ton


def test_the_worded_reason_of_a_gap():
    manque = _theme(etat="manque", ton="info", motif="parcelles_multiples")
    assert aligner(manque).etat_libelle == "Plusieurs parcelles possibles"
    assert aligner(_theme(etat="panne", ton="info", motif="x")).etat_libelle is None
    assert aligner(_theme()).etat_libelle is None


@pytest.mark.parametrize("motif", ["point_hors_parcelle", "parcelle_non_verifiee"])
def test_an_unattached_theme_serves_the_same_badge_and_headline(motif):
    theme = aligner(_theme(etat="manque", ton="info", motif=motif))
    assert theme.etat_libelle == "Non rattaché"
    assert theme.accroche == "Non rattaché"


def test_the_head_sentence_keeps_the_dpe_before_the_building_once():
    parcelle = {
        "entite": "p",
        "confiance": "incertaine",
        "motif": "parcelles_multiples",
    }
    dpe = {"entite": "x", "confiance": "probable", "motif": "parcelles_multiples"}
    batiment = {"entite": "x", "confiance": "probable"}
    themes = [
        _theme(id="parcelle", etat="manque", ton="info", rattachement=parcelle),
        _theme(id="batiment", rattachement=batiment),
        _theme(id="dpe", rattachement=dpe),
    ]
    # The alert of the DPE equals the one of the parcel: said once.
    assert phrase_rattachement(themes) == "Plusieurs parcelles possibles."


def test_the_summary_counts_answers_gaps_and_partial_reads():
    themes = [
        _theme(id="a", granularite={"niveau": "adresse", "rayon_m": None}),
        _theme(id="b", ton="neutre", pannes_partielles=["iris"]),
        _theme(id="c", etat="manque", ton="info", motif="x"),
        _theme(id="d", etat="hors_couverture", ton="info", motif="x"),
        _theme(id="e", etat="non_concerne", ton="rassurant"),
        _theme(
            id="f",
            etat="panne",
            ton="info",
            sources=[{**SOURCE, "cle": "dvf", "mode": "import"}],
        ),
    ]
    resume = resumer(themes)
    assert [p.theme for p in resume.partiels] == ["b"]
    assert [p.theme for p in resume.sans_valeur] == ["c", "d"]
    assert (resume.lus, resume.lus_adresse, resume.lus_secteur) == (3, 1, 2)
    assert resume.rassurants == []
    # A theme read from an import only names that source.
    assert resume.sources_en_panne == ["dvf"]
    assert resume.par_section["risques"].model_dump() == {
        "vigilance": 0,
        "pannes": 1,
        "lus": 3,
        "total": 6,
    }


def test_full_e2e_report_uses_current_api_alignment():
    fixture = (
        Path(__file__).resolve().parents[3]
        / "frontend/tests/e2e/fixtures/rapport-43-themes.json"
    )
    report = json.loads(fixture.read_text(encoding="utf-8"))["rapport"]["donnees"]
    expected = [
        aligner(Theme.model_validate(theme)).model_dump(mode="json")
        for theme in report["themes"]
    ]
    assert report["themes"] == expected


def test_full_e2e_report_uses_current_api_summary():
    fixture = (
        Path(__file__).resolve().parents[3]
        / "frontend/tests/e2e/fixtures/rapport-43-themes.json"
    )
    report = json.loads(fixture.read_text(encoding="utf-8"))["rapport"]["donnees"]
    themes = [aligner(Theme.model_validate(theme)) for theme in report["themes"]]
    assert report["resume"] == resumer(themes).model_dump(mode="json")
