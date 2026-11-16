"""Complete risks of the address report (04.7-04), without a database.

The Géorisques risk report says, per risk, whether the commune is concerned
and the status at an address it geocodes itself: a status is read at the
address only when that address is the BAN point (20 m), else the risk is a
fact of the commune. A risk not listed in the commune is « not concerned »,
never reassuring; a zero is reassuring only on a complete read; an outage is
never a zero and is never cached.
"""

from datetime import date

import httpx
import pytest
import respx

from app.adresse import lieu
from app.rapport import partage
from app.rapport.partage import (
    ErrialLu,
    LignesLues,
    RapportRisques,
    StatutRisque,
)
from app.rapport.socle import PanneAmont
from app.rapport.themes import (
    catnat,
    debroussaillement,
    inondation,
    mouvements_terrain,
    nucleaire,
    remontee_nappe,
    rupture_barrage,
    seveso,
    sismicite,
    trait_de_cote,
)
from tests.test_rapport_themes import POINT, _contexte

pytestmark = pytest.mark.unit

AS_OF = date(2026, 10, 30)


@pytest.fixture(autouse=True)
def _caches_vides():
    partage.vider_caches()
    yield
    partage.vider_caches()


def _rapport(cle="inondation", present=True, commune=None, adresse=None, **autres):
    statuts = {
        cle: StatutRisque(
            present=present,
            commune=commune
            if commune is not None
            else ("Risque Existant" if present else None),
            adresse=adresse,
        )
    }
    statuts.update(autres)
    return RapportRisques(
        statuts=statuts, adresse_concordante=True, distance_m=0.0, code_insee="44109"
    )


def _lignes(*lignes, complet=True):
    return LignesLues(lignes=list(lignes), complet=complet)


# --- Common rule of the Géorisques risk report -------------------------------


async def test_a_risk_not_listed_in_the_commune_is_not_concerned_and_neutral():
    theme = await remontee_nappe.lire(
        _contexte(amonts={"remontee_nappe": _rapport("remonteeNappe", present=False)})
    )
    assert (theme.etat, theme.ton, theme.granularite.niveau) == (
        "non_concerne",
        "neutre",
        "commune",
    )


@pytest.mark.parametrize(
    ("adresse", "ton", "niveau"),
    [
        ("Risque Existant", "vigilance", "adresse"),
        ("Risque Existant - modéré", "vigilance", "adresse"),
        ("Risque Concerne", "vigilance", "adresse"),
        ("Risque non Concerne", "neutre", "adresse"),
        ("Risque Inconnu", "vigilance", "commune"),
        ("Risque non Connu", "vigilance", "commune"),
        (None, "vigilance", "commune"),
    ],
)
async def test_the_status_at_the_address_sets_tone_and_scale(adresse, ton, niveau):
    rapport = _rapport("ruptureBarrage", adresse=adresse, commune="Risque Concerne")
    theme = await rupture_barrage.lire(_contexte(amonts={"rupture_barrage": rapport}))
    assert (theme.etat, theme.ton, theme.granularite.niveau) == ("ok", ton, niveau)


async def test_another_address_of_georisques_makes_it_a_fact_of_the_commune():
    rapport = _rapport(
        "nucleaire", adresse="Risque Concerne", commune="Risque Concerne"
    )
    loin = RapportRisques(
        statuts=rapport.statuts,
        adresse_concordante=False,
        distance_m=62.0,
        code_insee="44109",
    )
    theme = await nucleaire.lire(_contexte(amonts={"nucleaire": loin}))
    assert (theme.ton, theme.granularite.niveau) == ("vigilance", "commune")
    assert theme.details["adresse_concordante"] is False


async def test_another_commune_of_georisques_concludes_nothing():
    rapport = _rapport("reculTraitCote", adresse="Risque Existant")
    voisine = RapportRisques(
        statuts=rapport.statuts,
        adresse_concordante=True,
        distance_m=0.0,
        code_insee="44020",
    )
    theme = await trait_de_cote.lire(_contexte(amonts={"trait_de_cote": voisine}))
    assert (theme.etat, theme.motif) == ("manque", "commune_georisques_differente")


@pytest.mark.parametrize("amont", [PanneAmont(), lieu.PanneAmont(), None])
async def test_an_outage_of_the_report_is_an_outage(amont):
    contexte = _contexte(amonts={"remontee_nappe": amont} if amont else {})
    theme = await remontee_nappe.lire(contexte)
    assert (theme.etat, theme.ton) == ("panne", "info")


async def test_a_risk_missing_from_the_answer_is_an_outage():
    theme = await remontee_nappe.lire(
        _contexte(amonts={"remontee_nappe": _rapport("autre")})
    )
    assert theme.etat == "panne"


async def test_not_hosted_and_no_point():
    assert (await nucleaire.lire(_contexte(hors=("georisques",)))).etat == (
        "hors_couverture"
    )
    assert (await nucleaire.lire(_contexte(point=None))).etat == "manque"


def test_the_address_of_georisques_is_compared_with_the_ban_point():
    donnees = {
        "adresse": {"longitude": POINT.lon + 0.0008, "latitude": POINT.lat},
        "commune": {"codeInsee": "44109"},
        "risquesNaturels": {
            cle: {
                "present": True,
                "libelleStatutCommune": "Risque Existant",
                "libelleStatutAdresse": "Risque Existant",
            }
            for cle in partage.RISQUES_LUS
        },
        "risquesTechnologiques": {},
    }
    loin = partage.rapport_de(donnees, POINT)
    assert loin.adresse_concordante is False and loin.distance_m > 50
    donnees["adresse"] = {"longitude": POINT.lon, "latitude": POINT.lat}
    proche = partage.rapport_de(donnees, POINT)
    assert proche.adresse_concordante is True
    assert proche.statuts["inondation"].adresse == "Risque Existant"


def test_a_malformed_report_is_an_outage():
    from app.common.exceptions import UpstreamUnavailableError

    with pytest.raises(UpstreamUnavailableError):
        partage.rapport_de({"adresse": {}}, POINT)


# --- Inondation -------------------------------------------------------------


def _amont_inondation(rapport, azi=None, tri=None, errial=None):
    return partage.AmontInondation(
        rapport=rapport,
        azi=azi if azi is not None else _lignes(),
        tri=tri if tri is not None else _lignes(),
        errial=errial,
    )


async def test_flood_at_the_address_lists_the_commune_atlases():
    amont = _amont_inondation(
        _rapport("inondation", adresse="Risque Existant"),
        azi=_lignes({"libelle_azi": "Vallée de la Loire"}),
        tri=_lignes({"libelle_tri": "Nantes"}),
    )
    theme = await inondation.lire(_contexte(amonts={"inondation": amont}))
    assert (theme.etat, theme.ton, theme.granularite.niveau) == (
        "ok",
        "vigilance",
        "adresse",
    )
    assert theme.details["atlas_zones_inondables"] == ["Vallée de la Loire"]
    assert theme.details["territoires_risque_important"] == ["Nantes"]


async def test_a_parcel_in_a_tri_flood_extent_is_a_point_to_check():
    errial = ErrialLu(
        parcelle="000-LB-369@44109",
        concordance=True,
        resume={},
        tris=[{"libelle": "Nantes", "aleas": ["Crue rare"]}],
    )
    amont = _amont_inondation(
        _rapport("inondation", adresse="Risque non Concerne"), errial=errial
    )
    theme = await inondation.lire(_contexte(amonts={"inondation": amont}))
    assert (theme.ton, theme.granularite.niveau) == ("vigilance", "adresse")
    assert theme.details["tri_parcelle"] == [
        {"libelle": "Nantes", "aleas": ["Crue rare"]}
    ]


async def test_an_atlas_outage_is_partial_and_said():
    amont = _amont_inondation(
        _rapport("inondation", adresse="Risque non Concerne"), azi=PanneAmont()
    )
    theme = await inondation.lire(_contexte(amonts={"inondation": amont}))
    assert theme.ton == "neutre"
    assert theme.pannes_partielles == ["azi"]


# --- Débroussaillement --------------------------------------------------------


def _errial(has_old, concordance=True):
    return ErrialLu(
        parcelle="000-LB-369@44109",
        concordance=concordance,
        resume={"obligation_debroussaillement": has_old},
    )


async def test_a_legal_clearing_obligation_on_the_parcel_is_a_point_to_check():
    amont = partage.AmontDebroussaillement(errial=_errial(True), rapport=None)
    theme = await debroussaillement.lire(_contexte(amonts={"debroussaillement": amont}))
    assert (theme.etat, theme.ton, theme.granularite.niveau) == (
        "ok",
        "vigilance",
        "parcelle",
    )


async def test_no_obligation_is_not_concerned_and_no_parcel_concludes_nothing():
    amont = partage.AmontDebroussaillement(errial=_errial(False), rapport=None)
    theme = await debroussaillement.lire(_contexte(amonts={"debroussaillement": amont}))
    assert (theme.etat, theme.ton) == ("non_concerne", "neutre")
    autre = partage.AmontDebroussaillement(
        errial=_errial(True, concordance=False), rapport=None
    )
    theme = await debroussaillement.lire(_contexte(amonts={"debroussaillement": autre}))
    assert (theme.etat, theme.ton) == ("manque", "info")
    illisible = partage.AmontDebroussaillement(errial=_errial(None), rapport=None)
    theme = await debroussaillement.lire(
        _contexte(amonts={"debroussaillement": illisible})
    )
    assert theme.etat == "panne"


# --- Mouvements de terrain ------------------------------------------------------


def _mvt(lon, lat, type_="Glissement"):
    return {"type": type_, "longitude": lon, "latitude": lat}


async def test_ground_movements_within_500_m_are_a_point_to_check():
    amont = partage.AmontTerrain(
        cavites=_lignes(), mouvements=_lignes(_mvt(POINT.lon, POINT.lat + 0.002))
    )
    theme = await mouvements_terrain.lire(
        _contexte(amonts={"mouvements_terrain": amont})
    )
    assert (theme.etat, theme.ton) == ("ok", "vigilance")
    assert (theme.granularite.niveau, theme.granularite.rayon_m) == ("rayon", 500)
    assert theme.details["mouvements"][0]["distance_m"] in range(215, 230)


async def test_none_within_500_m_reassures_only_on_complete_reads():
    complet = partage.AmontTerrain(cavites=_lignes(), mouvements=_lignes())
    theme = await mouvements_terrain.lire(
        _contexte(amonts={"mouvements_terrain": complet})
    )
    assert (theme.etat, theme.ton) == ("zero", "rassurant")
    # One inventory down: no global « none », the outage says which one.
    partiel = partage.AmontTerrain(cavites=PanneAmont(), mouvements=_lignes())
    theme = await mouvements_terrain.lire(
        _contexte(amonts={"mouvements_terrain": partiel})
    )
    assert (theme.etat, theme.ton) == ("panne", "info")
    assert theme.verdict.startswith("Inventaire des cavités indisponible")
    assert theme.pannes_partielles == ["cavites"]
    tout = partage.AmontTerrain(cavites=PanneAmont(), mouvements=PanneAmont())
    theme = await mouvements_terrain.lire(
        _contexte(amonts={"mouvements_terrain": tout})
    )
    assert theme.etat == "panne"


# --- Seveso -----------------------------------------------------------------------


def _icpe(nom, statut, metres_nord):
    return {
        "raisonSociale": nom,
        "statutSeveso": statut,
        "longitude": POINT.lon,
        "latitude": POINT.lat + metres_nord / 111_150,
        "etatActivite": "En exploitation avec titre",
    }


@pytest.mark.parametrize(
    ("lignes", "ton"),
    [
        ([_icpe("A", "Seveso seuil bas", 800)], "vigilance"),
        ([_icpe("A", "Seveso seuil haut", 4000)], "vigilance"),
        ([_icpe("A", "Seveso seuil bas", 3000)], "neutre"),
        ([_icpe("A", "Non Seveso", 100)], "rassurant"),
    ],
)
async def test_seveso_tone_rule(lignes, ton):
    theme = await seveso.lire(_contexte(amonts={"seveso": _lignes(*lignes)}))
    assert theme.ton == ton
    assert (theme.granularite.niveau, theme.granularite.rayon_m) == ("rayon", 5000)


async def test_no_seveso_on_a_partial_read_is_a_gap():
    theme = await seveso.lire(
        _contexte(amonts={"seveso": _lignes(_icpe("A", None, 100), complet=False)})
    )
    assert (theme.etat, theme.motif) == ("manque", "lecture_incomplete")
    found = await seveso.lire(
        _contexte(
            amonts={
                "seveso": _lignes(_icpe("B", "Seveso seuil haut", 900), complet=False)
            }
        )
    )
    assert (found.etat, found.ton) == ("ok", "vigilance")


# --- CatNat --------------------------------------------------------------------


def _arrete(debut):
    return {
        "libelle_risque_jo": "Inondations et/ou Coulées de Boue",
        "date_debut_evt": debut,
        "date_fin_evt": debut,
        "date_publication_jo": debut,
    }


@pytest.mark.parametrize(
    ("debuts", "etat", "ton"),
    [
        (["07/10/2024", "05/01/2001"], "ok", "vigilance"),
        (["05/01/2001"], "ok", "neutre"),
        ([], "zero", "rassurant"),
    ],
)
async def test_catnat_tone_rule(debuts, etat, ton):
    amont = _lignes(*(_arrete(d) for d in debuts))
    theme = await catnat.lire(_contexte(amonts={"catnat": amont}))
    assert (theme.etat, theme.ton, theme.granularite.niveau) == (etat, ton, "commune")


async def test_catnat_on_a_partial_read_never_says_none():
    theme = await catnat.lire(_contexte(amonts={"catnat": _lignes(complet=False)}))
    assert theme.etat == "manque"


# --- Sismicité (Géorisques zoning by INSEE code) -------------------------------------


@pytest.mark.parametrize(
    ("zone", "ton"),
    [("5", "vigilance"), ("3", "vigilance"), ("2", "neutre"), ("1", "rassurant")],
)
async def test_sismicite_tone_rule(zone, ton):
    amont = _lignes({"code_insee": "44109", "code_zone": zone})
    theme = await sismicite.lire(_contexte(amonts={"sismicite": amont}))
    assert (theme.etat, theme.ton, theme.granularite.niveau) == ("ok", ton, "commune")


async def test_sismicite_states():
    assert (
        await sismicite.lire(_contexte(amonts={"sismicite": PanneAmont()}))
    ).etat == ("panne")
    vide = await sismicite.lire(_contexte(amonts={"sismicite": _lignes()}))
    assert vide.etat == "manque"
    autre = await sismicite.lire(
        _contexte(
            amonts={"sismicite": _lignes({"code_insee": "44020", "code_zone": "3"})}
        )
    )
    assert autre.etat == "manque"


# --- Readers: retry, pagination, cache ------------------------------------------

URL = f"{partage.GEORISQUES_BASE}/gaspar/catnat"


async def test_a_dropped_connection_is_retried_once():
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(URL).mock(
            side_effect=[
                httpx.ReadError("coupée"),
                httpx.Response(200, json={"data": [], "total_pages": 1}),
            ]
        )
        assert await partage.get_georisques("/gaspar/catnat", {}) == {
            "data": [],
            "total_pages": 1,
        }
    assert route.call_count == 2


async def test_two_dropped_connections_are_an_outage():
    from app.common.exceptions import UpstreamUnavailableError

    with respx.mock() as mock:
        mock.get(URL).mock(side_effect=httpx.ReadError("coupée"))
        with pytest.raises(UpstreamUnavailableError):
            await partage.get_georisques("/gaspar/catnat", {})


async def test_pages_are_read_up_to_their_bound():
    pages = [
        httpx.Response(200, json={"data": [{"n": 1}], "total_pages": 3, "next": "p2"}),
        httpx.Response(200, json={"data": [{"n": 2}], "total_pages": 3, "next": "p3"}),
    ]
    with respx.mock() as mock:
        mock.get(URL).mock(side_effect=pages)
        lu = await partage.lignes_georisques("/gaspar/catnat", {}, pages_max=2)
    assert (len(lu.lignes), lu.complet) == (2, False)
    with respx.mock() as mock:
        mock.get(URL).mock(
            return_value=httpx.Response(
                200, json={"data": [{"n": 1}], "total_pages": 1}
            )
        )
        lu = await partage.lignes_georisques("/gaspar/catnat", {}, pages_max=2)
    assert lu.complet is True


async def test_an_answer_is_cached_and_an_outage_is_not():
    cache = partage.CacheAmont()
    appels = 0

    async def lecteur():
        nonlocal appels
        appels += 1
        return "lu"

    assert await cache.obtenir("k", lecteur) == "lu"
    assert await cache.obtenir("k", lecteur) == "lu"
    assert appels == 1

    async def panne():
        raise httpx.ReadError("coupée")

    for _ in range(2):
        with pytest.raises(httpx.ReadError):
            await cache.obtenir("p", panne)
    assert await cache.obtenir("p", lecteur) == "lu"


# --- Fallback on the commune's GASPAR list when the risk report is down ---------


def _gaspar(*libelles, complet=True):
    from app.adresse.risques import RisquesCommune
    from app.common.proxy_coverage import ProxySource

    couverture = ProxySource(statut="disponible", complet=complet, pages_lues=1)
    return RisquesCommune(
        lignes={
            "gaspar": [
                {"risques_detail": [{"libelle_risque_long": x} for x in libelles]}
            ],
            "radon": None,
            "icpe": None,
        },
        couverture={"gaspar": couverture, "radon": couverture, "icpe": couverture},
        total={"gaspar": None, "radon": None, "icpe": None},
    )


def _repli(gaspar):
    return partage.AmontRisque(rapport=lieu.PanneAmont(), gaspar=gaspar)


async def test_report_down_the_commune_list_answers_at_the_commune():
    amont = _repli(_gaspar("Inondation", "Par remontées de nappes naturelles"))
    theme = await remontee_nappe.lire(_contexte(amonts={"remontee_nappe": amont}))
    assert (theme.etat, theme.ton, theme.granularite.niveau) == (
        "ok",
        "vigilance",
        "commune",
    )
    assert theme.pannes_partielles == ["statut_adresse"]
    assert theme.details["risques_gaspar"] == ["Par remontées de nappes naturelles"]


async def test_report_down_a_complete_list_without_the_risk_is_not_concerned():
    theme = await rupture_barrage.lire(
        _contexte(amonts={"rupture_barrage": _repli(_gaspar("Inondation"))})
    )
    assert (theme.etat, theme.ton) == ("non_concerne", "neutre")
    assert theme.pannes_partielles == ["statut_adresse"]
    partiel = await rupture_barrage.lire(
        _contexte(
            amonts={"rupture_barrage": _repli(_gaspar("Inondation", complet=False))}
        )
    )
    assert partiel.etat == "manque"


async def test_report_and_list_down_is_an_outage():
    theme = await nucleaire.lire(
        _contexte(amonts={"nucleaire": _repli(lieu.PanneAmont())})
    )
    assert theme.etat == "panne"


# --- Lot B: altitude (IGN) and lithology (BRGM) -------------------------------

from app.rapport.themes import altitude, geologie

GML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<msGMLOutput '
    'xmlns:gml="http://www.opengis.net/gml">\n<LITHO_1M_SIMPLIFIEE_layer>\n'
    "<LITHO_1M_SIMPLIFIEE_feature>\n<OBJECTID>1977</OBJECTID>\n"
    "<CODE_GEOL>11</CODE_GEOL>\n<DESCR>Micaschistes</DESCR>\n"
    "<TYPE>Roches M&#233;tamorphiques</TYPE>\n</LITHO_1M_SIMPLIFIEE_feature>\n"
    "</LITHO_1M_SIMPLIFIEE_layer>\n</msGMLOutput>"
)


def test_the_ign_and_brgm_answers_are_read_as_served():
    assert partage.altitude_de({"elevations": [9.23]}) == 9.23
    assert partage.altitude_de({"elevations": [{"z": 9.23}]}) == 9.23
    assert partage.altitude_de({"elevations": [-99999]}) is None
    (roche,) = partage.lithologies_de(GML)
    assert (roche.description, roche.type_roche, roche.code) == (
        "Micaschistes",
        "Roches Métamorphiques",
        "11",
    )
    assert partage.lithologies_de("<msGMLOutput></msGMLOutput>") == []


@pytest.mark.parametrize(
    "reponse",
    [{"elevations": []}, {"elevations": ["x"]}, {}, [9.23], {"elevations": [True]}],
)
def test_a_malformed_altitude_answer_is_an_outage(reponse):
    from app.common.exceptions import UpstreamUnavailableError

    with pytest.raises(UpstreamUnavailableError):
        partage.altitude_de(reponse)


def test_a_wms_exception_is_an_outage():
    from app.common.exceptions import UpstreamUnavailableError

    with pytest.raises(UpstreamUnavailableError):
        partage.lithologies_de(
            "<ServiceExceptionReport>erreur</ServiceExceptionReport>"
        )


async def test_altitude_is_rounded_half_up():
    theme = await altitude.lire(
        _contexte(amonts={"altitude": partage.AltitudeLue(metres=24.5)})
    )
    assert theme.chiffres[0].valeur == 25  # `round`: 24, half to even
    assert theme.verdict.endswith(" : 25 m.")


async def test_altitude_is_neutral_at_the_address():
    theme = await altitude.lire(
        _contexte(amonts={"altitude": partage.AltitudeLue(metres=9.23)})
    )
    assert (theme.etat, theme.ton, theme.granularite.niveau) == (
        "ok",
        "neutre",
        "adresse",
    )
    assert theme.chiffres[0].valeur == 9
    sans = await altitude.lire(
        _contexte(amonts={"altitude": partage.AltitudeLue(metres=None)})
    )
    assert (sans.etat, sans.motif) == ("manque", "hors_rge_alti")
    panne = await altitude.lire(_contexte(amonts={"altitude": lieu.PanneAmont()}))
    assert panne.etat == "panne"


async def test_geology_is_the_rock_of_the_sector_never_of_the_address():
    roche = partage.Lithologie("Micaschistes", "Roches Métamorphiques", "11")
    theme = await geologie.lire(_contexte(amonts={"geologie": [roche]}))
    assert (theme.etat, theme.ton, theme.granularite.niveau) == (
        "ok",
        "neutre",
        "commune",
    )
    assert "secteur" in theme.verdict and "millionième" in theme.verdict
    vide = await geologie.lire(_contexte(amonts={"geologie": []}))
    assert (vide.etat, vide.motif) == ("manque", "aucune_formation")
    autre = partage.Lithologie("Sables", "Roches Sédimentaires", "6")
    deux = await geologie.lire(_contexte(amonts={"geologie": [roche, autre]}))
    assert (deux.etat, deux.motif) == ("manque", "formations_multiples")
    panne = await geologie.lire(_contexte(amonts={"geologie": lieu.PanneAmont()}))
    assert panne.etat == "panne"


@pytest.mark.parametrize(
    ("module", "cle", "lecteur"),
    [
        (altitude, "altimetrie", partage.altitude_au_point),
        (geologie, "geologie", partage.geologie_au_point),
    ],
)
async def test_until_the_scope_hosts_them_they_are_out_of_coverage_without_call(
    module, cle, lecteur
):
    from app.rapport.socle import ContexteAmont, Memo

    contexte = _contexte(hors=(cle,))
    amont = ContexteAmont(
        identite=contexte.identite,
        point=contexte.point,
        hosting=contexte.hosting,
        as_of=AS_OF,
        memo=Memo(),
    )
    with respx.mock(assert_all_called=False) as mock:
        assert await lecteur(amont) is None
    assert mock.calls.call_count == 0
    theme = await module.lire(contexte)
    assert (theme.etat, theme.ton) == ("hors_couverture", "info")
    assert theme.etat not in ("zero", "panne")


async def test_the_datasets_page_serves_both_with_their_licence(client):
    from unittest.mock import AsyncMock, patch

    from app.common.source_catalog import DATASET_CATALOG

    with patch("app.datasets.router.get_all_datasets", new=AsyncMock(return_value=[])):
        response = await client.get("/v1/datasets")
    assert response.status_code == 200, response.text
    servis = {jeu["nom"]: jeu for jeu in response.json()["donnees"]}
    for cle in ("altimetrie", "geologie"):
        entree = DATASET_CATALOG[cle]
        assert servis[cle]["reutilisation"]["licence"] == entree.licence
        assert servis[cle]["provenance"]["producteur"] == entree.producteur
        assert servis[cle]["provenance"]["url"] == entree.url


# --- Regressions of the first Codex review of 04.7-04 ---------------------------


def _donnees_rapport(lon, lat, code="44109"):
    return {
        "adresse": {"longitude": lon, "latitude": lat},
        "commune": {"codeInsee": code},
        "risquesNaturels": {
            cle: {
                "present": True,
                "libelleStatutCommune": "Risque Existant",
                "libelleStatutAdresse": "Risque Existant",
            }
            for cle in partage.RISQUES_LUS
        },
        "risquesTechnologiques": {},
    }


async def test_a_tri_on_the_parcel_prevails_over_the_commune_list():
    errial = ErrialLu(
        parcelle="000-LB-369@44109",
        concordance=True,
        resume={},
        tris=[{"libelle": "Nantes", "aleas": ["Crue rare"]}],
    )
    amont = partage.AmontInondation(
        rapport=lieu.PanneAmont(),
        azi=_lignes(),
        tri=_lignes(),
        errial=errial,
        gaspar=_gaspar("Séisme"),
    )
    theme = await inondation.lire(_contexte(amonts={"inondation": amont}))
    assert (theme.etat, theme.ton, theme.granularite.niveau) == (
        "ok",
        "vigilance",
        "adresse",
    )
    assert "statut_adresse" in theme.pannes_partielles
    assert {s.cle for s in theme.sources} == {"georisques", "errial"}


def test_a_report_without_its_commune_or_a_risk_concludes_nothing():
    from app.common.exceptions import UpstreamUnavailableError

    sans_commune = _donnees_rapport(POINT.lon, POINT.lat)
    sans_commune["commune"] = {}
    with pytest.raises(UpstreamUnavailableError):
        partage.rapport_de(sans_commune, POINT)
    ampute = _donnees_rapport(POINT.lon, POINT.lat)
    del ampute["risquesNaturels"]["remonteeNappe"]
    with pytest.raises(UpstreamUnavailableError):
        partage.rapport_de(ampute, POINT)


def _contexte_amont(point):
    from app.rapport.socle import ContexteAmont, Memo

    contexte = _contexte(point=point)
    return ContexteAmont(
        identite=contexte.identite,
        point=point,
        hosting=contexte.hosting,
        as_of=AS_OF,
        memo=Memo(),
    )


async def test_the_concordance_is_computed_for_each_point_of_a_shared_answer():
    from app.adresse.relations import PointBan

    loin = PointBan(lon=-1.55, lat=47.212396)
    proche = PointBan(lon=-1.55, lat=47.2124)
    # Géorisques' address 20.8 m north of the first point: 20.4 m of the second
    # (distances rounded to the metre: 21 and 20).
    adresse_lat = loin.lat + 20.8 / 111_195
    url = f"{partage.GEORISQUES_BASE}/resultats_rapport_risque"
    with respx.mock() as mock:
        route = mock.get(url).mock(
            return_value=httpx.Response(200, json=_donnees_rapport(-1.55, adresse_lat))
        )
        premier = await partage.rapport_risques(_contexte_amont(loin))
        second = await partage.rapport_risques(_contexte_amont(proche))
    assert route.call_count == 1
    assert (premier.adresse_concordante, second.adresse_concordante) == (False, True)


async def test_an_incomplete_report_is_an_outage_and_never_kept():
    url = f"{partage.GEORISQUES_BASE}/resultats_rapport_risque"
    ampute = _donnees_rapport(POINT.lon, POINT.lat)
    del ampute["risquesTechnologiques"]
    ampute["risquesTechnologiques"] = {}
    del ampute["risquesNaturels"]["nucleaire"]
    with respx.mock() as mock:
        route = mock.get(url).mock(
            side_effect=[
                httpx.Response(200, json=ampute),
                httpx.Response(200, json=_donnees_rapport(POINT.lon, POINT.lat)),
            ]
        )
        premier = await partage.rapport_risques(_contexte_amont(POINT))
        second = await partage.rapport_risques(_contexte_amont(POINT))
    assert isinstance(premier, lieu.PanneAmont)
    assert isinstance(second, RapportRisques)
    assert route.call_count == 2


async def test_contradictory_pagination_is_never_complete():
    with respx.mock() as mock:
        mock.get(URL).mock(
            return_value=httpx.Response(200, json={"data": [], "total_pages": 2})
        )
        lu = await partage.lignes_georisques("/gaspar/catnat", {}, pages_max=1)
    assert lu.complet is False


async def test_an_illegible_commune_list_never_proves_an_absence():
    from app.adresse.risques import RisquesCommune
    from app.common.proxy_coverage import ProxySource

    complet = ProxySource(statut="disponible", complet=True, pages_lues=1)
    illisible = RisquesCommune(
        lignes={"gaspar": [{}], "radon": None, "icpe": None},
        couverture={"gaspar": complet, "radon": complet, "icpe": complet},
        total={"gaspar": None, "radon": None, "icpe": None},
    )
    theme = await trait_de_cote.lire(
        _contexte(amonts={"trait_de_cote": _repli(illisible)})
    )
    assert (theme.etat, theme.motif) == ("manque", "liste_illisible")


async def test_catnat_recency_is_the_start_of_the_event():
    avant = {**_arrete("29/10/2016"), "date_publication_jo": "01/12/2016"}
    dedans = {**_arrete("31/10/2016"), "date_publication_jo": "01/12/2016"}
    theme = await catnat.lire(_contexte(amonts={"catnat": _lignes(avant)}))
    assert (theme.ton, theme.chiffres[1].valeur) == ("neutre", 0)
    assert theme.chiffres[1].libelle.startswith("Événements")
    theme = await catnat.lire(_contexte(amonts={"catnat": _lignes(dedans)}))
    assert (theme.ton, theme.chiffres[1].valeur) == ("vigilance", 1)


# --- Regressions of the second Codex review of 04.7-04 --------------------------


async def test_a_tri_on_the_parcel_prevails_over_a_report_saying_not_listed():
    errial = ErrialLu(
        parcelle="000-LB-369@44109",
        concordance=True,
        resume={},
        tris=[{"libelle": "Nantes", "aleas": ["Crue rare"]}],
    )
    amont = partage.AmontInondation(
        rapport=_rapport("inondation", present=False),
        azi=_lignes(),
        tri=_lignes(),
        errial=errial,
    )
    theme = await inondation.lire(_contexte(amonts={"inondation": amont}))
    assert (theme.etat, theme.ton, theme.granularite.niveau) == (
        "ok",
        "vigilance",
        "adresse",
    )


async def test_a_next_page_announced_is_never_a_complete_read():
    with respx.mock() as mock:
        mock.get(URL).mock(
            return_value=httpx.Response(
                200, json={"data": [], "total_pages": 1, "next": "page=2"}
            )
        )
        lu = await partage.lignes_georisques("/gaspar/catnat", {}, pages_max=1)
    assert lu.complet is False


async def test_one_unreadable_detail_makes_the_whole_list_illegible():
    gaspar = _gaspar("Séisme")
    gaspar.lignes["gaspar"][0]["risques_detail"].append({})
    theme = await trait_de_cote.lire(
        _contexte(amonts={"trait_de_cote": _repli(gaspar)})
    )
    assert (theme.etat, theme.motif) == ("manque", "liste_illisible")


@pytest.mark.parametrize(
    "gml",
    [
        "<msGMLOutput><LITHO_1M_SIMPLIFIEE_layer>",  # cut answer
        "<ServiceExceptionReport/>",
        "<msGMLOutput><LITHO_1M_SIMPLIFIEE_feature><TYPE>x</TYPE>"
        "</LITHO_1M_SIMPLIFIEE_feature></msGMLOutput>",
    ],
)
def test_a_malformed_brgm_answer_is_an_outage(gml):
    from app.common.exceptions import UpstreamUnavailableError

    with pytest.raises(UpstreamUnavailableError):
        partage.lithologies_de(gml)


# --- Regressions of the third Codex review of 04.7-04 ---------------------------


async def test_a_tri_with_a_sound_report_declares_no_outage_and_keeps_its_statuses():
    errial = ErrialLu(
        parcelle="000-LB-369@44109",
        concordance=True,
        resume={},
        tris=[{"libelle": "Nantes", "aleas": ["Crue rare"]}],
    )
    amont = _amont_inondation(
        _rapport("inondation", adresse="Risque non Concerne"), errial=errial
    )
    theme = await inondation.lire(_contexte(amonts={"inondation": amont}))
    assert (theme.ton, theme.granularite.niveau) == ("vigilance", "adresse")
    assert theme.pannes_partielles == []
    assert theme.details["statut_commune"] == "Risque Existant"
    assert theme.details["statut_adresse"] == "Risque non Concerne"
    assert theme.details["adresse_concordante"] is True


async def test_a_tri_with_the_report_down_declares_the_address_status_once():
    errial = ErrialLu(
        parcelle="000-LB-369@44109",
        concordance=True,
        resume={},
        tris=[{"libelle": "Nantes", "aleas": ["Crue rare"]}],
    )
    amont = _amont_inondation(lieu.PanneAmont(), errial=errial)
    theme = await inondation.lire(_contexte(amonts={"inondation": amont}))
    assert (theme.ton, theme.granularite.niveau) == ("vigilance", "adresse")
    assert theme.pannes_partielles == ["statut_adresse"]
    assert theme.details["statut_adresse"] is None


@pytest.mark.parametrize(
    ("declaration", "charset"),
    [
        ('<?xml version="1.0" encoding="ISO-8859-1"?>\n', "ISO-8859-1"),
        ("", "ISO-8859-1"),  # the HTTP charset alone names the encoding
        ('<?xml version="1.0" encoding="ISO-8859-1"?>\n', None),
    ],
)
async def test_a_brgm_answer_in_latin_1_keeps_its_accents(declaration, charset):
    from app.rapport.socle import ContexteAmont, Memo

    gml = declaration + GML.split("\n", 1)[1].replace(
        "M&#233;tamorphiques", "Métamorphiques"
    )
    type_contenu = "application/vnd.ogc.gml"
    if charset:
        type_contenu += f"; charset={charset}"
    contexte = _contexte()
    amont = ContexteAmont(
        identite=contexte.identite,
        point=contexte.point,
        hosting=contexte.hosting,
        as_of=AS_OF,
        memo=Memo(),
    )
    with respx.mock() as mock:
        mock.get(url__startswith=partage.GEOLOGIE_URL).mock(
            return_value=httpx.Response(
                200,
                content=gml.encode("iso-8859-1"),
                headers={"content-type": type_contenu},
            )
        )
        (roche,) = await partage.geologie_au_point(amont)
    assert roche.type_roche == "Roches Métamorphiques"


async def test_bytes_invalid_in_the_http_charset_are_an_outage_never_cached():
    from app.rapport.socle import ContexteAmont, Memo

    contexte = _contexte()
    gml = GML.replace("M&#233;tamorphiques", "Métamorphiques").encode("iso-8859-1")
    with respx.mock() as mock:
        route = mock.get(url__startswith=partage.GEOLOGIE_URL).mock(
            return_value=httpx.Response(
                200,
                content=gml,
                headers={"content-type": "application/vnd.ogc.gml; charset=UTF-8"},
            )
        )
        for _ in range(2):
            amont = ContexteAmont(
                identite=contexte.identite,
                point=contexte.point,
                hosting=contexte.hosting,
                as_of=AS_OF,
                memo=Memo(),
            )
            assert isinstance(await partage.geologie_au_point(amont), lieu.PanneAmont)
    assert route.call_count == 2


def test_cdata_and_entities_of_the_brgm_answer_are_decoded():
    gml = (
        "<msGMLOutput><LITHO_1M_SIMPLIFIEE_layer><LITHO_1M_SIMPLIFIEE_feature>"
        "<DESCR><![CDATA[Sables & argiles]]></DESCR>"
        "<TYPE>Roches s&#233;dimentaires</TYPE>"
        "</LITHO_1M_SIMPLIFIEE_feature></LITHO_1M_SIMPLIFIEE_layer></msGMLOutput>"
    )
    (roche,) = partage.lithologies_de(gml)
    assert (roche.description, roche.type_roche) == (
        "Sables & argiles",
        "Roches sédimentaires",
    )
