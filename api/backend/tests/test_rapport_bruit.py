"""Noise themes of the address report (04.7-05), without a database.

Major roads and main railways are read live from the BD TOPO WFS, the
airport noise zones from the DGAC PEB, the noise sectors from the PLU. A
distance is a fact of the radius read; « nothing within 500 m » needs a
complete read; an outage is never a « nothing »; a threshold of prudence is
never presented as the prefectoral classification of the road.
"""

import math
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx

from app.adresse import lieu
from app.adresse.relations import PointBan
from app.rapport import partage
from app.rapport.alignement import aligner
from app.rapport.partage import (
    BruitAerien,
    InformationsPlu,
    TronconsLus,
    ZoneBruitAerien,
)
from app.rapport.themes import grand_axe, peb, secteur_bruit, voie_ferree
from tests.test_rapport_themes import AS_OF, POINT, _contexte

pytestmark = pytest.mark.unit

_M_PAR_DEGRE = math.pi / 180 * 6_371_008.8


@pytest.fixture(autouse=True)
def _caches_vides():
    partage.vider_caches()
    yield
    partage.vider_caches()


def _ligne_au_nord(metres: float, point: PointBan = POINT) -> dict:
    """A west-east line *metres* north of the point."""
    lat = point.lat + metres / _M_PAR_DEGRE
    return {
        "type": "LineString",
        "coordinates": [[point.lon - 0.01, lat], [point.lon + 0.01, lat]],
    }


def _feature(metres: float, **proprietes) -> dict:
    return {
        "type": "Feature",
        "geometry": _ligne_au_nord(metres),
        "properties": {"position_par_rapport_au_sol": "0", **proprietes},
    }


def _wfs(*features, matched=None) -> dict:
    return {
        "type": "FeatureCollection",
        "features": list(features),
        "numberMatched": len(features) if matched is None else matched,
        "numberReturned": len(features),
    }


def _route(metres, importance="1", numero="A11", classement="Autoroute", **autres):
    return _feature(
        metres,
        importance=importance,
        cpx_numero=numero,
        cpx_classement_administratif=classement,
        nature="Type autoroutier",
        **autres,
    )


def _rail(metres, nature="Voie ferrée principale", **autres):
    return _feature(metres, nature=nature, nombre_de_voies=2, **autres)


def _lus(donnees, rayon=500) -> TronconsLus:
    return partage.troncons_de(donnees, POINT, rayon)


# --- Shared WFS reading ------------------------------------------------------


def test_the_distance_to_a_line_is_the_distance_to_its_nearest_segment():
    assert partage.distance_ligne_m(POINT, _ligne_au_nord(120)) == pytest.approx(
        120, abs=0.5
    )
    multi = {
        "type": "MultiLineString",
        "coordinates": [
            _ligne_au_nord(400)["coordinates"],
            _ligne_au_nord(80)["coordinates"],
        ],
    }
    assert partage.distance_ligne_m(POINT, multi) == pytest.approx(80, abs=0.5)
    with pytest.raises(ValueError):
        partage.distance_ligne_m(POINT, {"type": "Point", "coordinates": [0, 0]})


def test_sections_are_sorted_kept_within_the_radius_tunnels_left_out():
    lu = _lus(
        _wfs(
            _route(300),
            _route(40, position_par_rapport_au_sol="-1"),
            _route(90),
            _route(70, fictif=True),
            _route(700),
        )
    )
    assert [t.distance_m for t in lu.troncons] == [90, 300]
    assert lu.complet is True


@pytest.mark.parametrize(
    "donnees",
    [
        _wfs(_route(90), matched=2),  # more matched than read
        _wfs(_route(90), matched="unknown"),
        {"type": "FeatureCollection", "features": [_route(90)]},  # no count
    ],
)
def test_a_read_is_complete_only_when_the_count_matches(donnees):
    assert _lus(donnees).complet is False


def test_a_full_page_is_never_complete():
    page = [_route(400)] * partage.TRONCONS_MAX
    assert _lus(_wfs(*page)).complet is False


@pytest.mark.parametrize(
    "donnees",
    [
        {"features": None},
        {"type": "FeatureCollection", "features": [{"properties": None}]},
        {
            "type": "FeatureCollection",
            "features": [{"properties": {}, "geometry": {"type": "Polygon"}}],
        },
        "<ServiceExceptionReport/>",
    ],
)
def test_an_unreadable_wfs_answer_is_an_outage(donnees):
    from app.common.exceptions import UpstreamUnavailableError

    with pytest.raises(UpstreamUnavailableError):
        _lus(donnees)


# --- Grands axes routiers ------------------------------------------------------


async def _grand_axe(*features, matched=None):
    lu = _lus(_wfs(*features, matched=matched))
    return await grand_axe.lire(_contexte(amonts={"grand_axe": lu}))


async def test_a_motorway_within_300_m_is_a_point_to_check():
    theme = await _grand_axe(_route(250))
    assert (theme.etat, theme.ton) == ("ok", "vigilance")
    assert theme.verdict == "Grand axe routier proche : A11 (autoroute) à 250 m."
    assert (theme.granularite.niveau, theme.granularite.rayon_m) == ("rayon", 500)
    assert theme.chiffres[0].valeur == 250
    assert theme.details["axes"][0] == {
        "nom": "A11",
        "type": "autoroute",
        "importance": "1",
        "distance_m": 250,
    }


async def test_a_road_of_importance_2_counts_within_100_m_only():
    loin = await _grand_axe(
        _route(150, importance="2", numero="D723", classement="Départementale")
    )
    assert (loin.ton, loin.verdict) == (
        "neutre",
        "Grand axe routier le plus proche : D723 (route départementale) à 150 m.",
    )
    pres = await _grand_axe(
        _route(80, importance="2", numero="D723", classement="Départementale")
    )
    assert pres.ton == "vigilance"


async def test_a_motorway_behind_a_nearer_road_still_sets_the_tone():
    theme = await _grand_axe(
        _route(150, importance="2", numero="D723", classement="Départementale"),
        _route(280),
    )
    assert theme.ton == "vigilance"
    assert theme.verdict == (
        "Grand axe routier proche : A11 (autoroute) à 280 m ; le plus proche : "
        "D723 (route départementale) à 150 m."
    )
    assert [a["distance_m"] for a in theme.details["axes"]] == [150, 280]


async def test_no_major_road_within_500_m_on_a_complete_read_is_reassuring():
    theme = await _grand_axe()
    assert (theme.etat, theme.ton) == ("zero", "rassurant")
    assert theme.verdict == "Aucun grand axe routier à moins de 500 m."


async def test_nothing_read_on_an_incomplete_read_is_a_gap_never_reassuring():
    theme = await _grand_axe(matched=3)
    assert (theme.etat, theme.motif) == ("manque", "lecture_incomplete")
    trouve = await _grand_axe(_route(450), matched=5)
    assert trouve.ton == "neutre"
    assert "lecture incomplète" in trouve.verdict


async def test_a_wfs_outage_is_an_outage_never_a_zero():
    theme = await grand_axe.lire(_contexte(amonts={"grand_axe": lieu.PanneAmont()}))
    assert (theme.etat, theme.ton) == ("panne", "info")


async def test_the_verdict_never_claims_a_classification():
    for metres in (50, 250, 450):
        theme = await _grand_axe(_route(metres))
        assert "catégorie" not in theme.verdict
        assert "classé" not in theme.verdict


async def test_a_long_road_name_keeps_the_verdict_within_its_bound():
    nom = "Boulevard " + "x" * 80
    theme = await _grand_axe(
        _route(90, importance="2", numero=None, classement=None),
        _route(260, numero=None, cpx_toponyme_route_nommee=nom),
    )
    assert len(theme.verdict) <= 140
    assert theme.ton == "vigilance"


# --- Voies ferrées -------------------------------------------------------------


async def _voie(*features, matched=None):
    lu = _lus(_wfs(*features, matched=matched))
    return await voie_ferree.lire(_contexte(amonts={"voie_ferree": lu}))


async def test_a_high_speed_line_within_300_m_is_a_point_to_check():
    theme = await _voie(_rail(280, nature="LGV", cpx_toponyme="LGV Atlantique"))
    assert theme.ton == "vigilance"
    assert theme.verdict == "Voie ferrée proche : LGV « LGV Atlantique » à 280 m."


async def test_a_main_line_counts_within_100_m_only():
    assert (await _voie(_rail(150))).ton == "neutre"
    pres = await _voie(_rail(60, electrifie=True, vitesse_maximale=160))
    assert pres.ton == "vigilance"
    assert pres.verdict == "Voie ferrée proche : voie ferrée principale à 60 m."
    assert pres.details["voies"][0]["nombre_de_voies"] == 2
    assert pres.details["voies"][0]["electrifiee"] is True


async def test_no_main_railway_within_500_m_is_reassuring_on_a_complete_read():
    theme = await _voie()
    assert (theme.etat, theme.ton, theme.verdict) == (
        "zero",
        "rassurant",
        "Aucune voie ferrée principale à moins de 500 m.",
    )
    assert (await _voie(matched=1)).etat == "manque"


# --- Live reads: request, cache, outage, out of coverage ----------------------


def _amont(contexte):
    from app.rapport.socle import ContexteAmont, Memo

    return ContexteAmont(
        identite=contexte.identite,
        point=contexte.point,
        hosting=contexte.hosting,
        as_of=AS_OF,
        memo=Memo(),
    )


async def test_the_wfs_request_filters_major_roads_in_service_around_the_point():
    with respx.mock() as mock:
        route = mock.get(url__startswith=partage.WFS_URL).mock(
            return_value=httpx.Response(200, json=_wfs(_route(120)))
        )
        lu = await grand_axe.amont(_amont(_contexte()))
    params = parse_qs(urlsplit(str(route.calls[0].request.url)).query)
    assert params["TYPENAMES"] == ["BDTOPO_V3:troncon_de_route"]
    assert "importance IN ('1','2')" in params["CQL_FILTER"][0]
    assert "etat_de_l_objet='En service'" in params["CQL_FILTER"][0]
    assert params["CQL_FILTER"][0].startswith("BBOX(geometrie,")
    assert [t.distance_m for t in lu.troncons] == [120]


async def test_an_outage_of_the_wfs_is_never_kept_in_cache():
    with respx.mock() as mock:
        route = mock.get(url__startswith=partage.WFS_URL).mock(
            return_value=httpx.Response(503)
        )
        for _ in range(2):
            lu = await voie_ferree.amont(_amont(_contexte()))
            assert isinstance(lu, lieu.PanneAmont)
    assert route.call_count == 2


async def test_a_sound_wfs_answer_is_kept_and_measured_again_from_each_point():
    with respx.mock() as mock:
        route = mock.get(url__startswith=partage.WFS_URL).mock(
            return_value=httpx.Response(200, json=_wfs(_route(120)))
        )
        await grand_axe.amont(_amont(_contexte()))
        lu = await grand_axe.amont(_amont(_contexte()))
    assert route.call_count == 1
    assert [t.distance_m for t in lu.troncons] == [120]


@pytest.mark.parametrize(
    ("module", "cle"),
    [(grand_axe, "axes_transport"), (voie_ferree, "axes_transport"), (peb, "peb")],
)
async def test_until_the_scope_hosts_them_they_are_out_of_coverage_without_call(
    module, cle
):
    contexte = _contexte(hors=(cle,))
    with respx.mock(assert_all_called=False) as mock:
        assert await module.THEME.amont(_amont(contexte)) is None
    assert mock.calls.call_count == 0
    theme = await module.lire(contexte)
    assert (theme.etat, theme.ton) == ("hors_couverture", "info")


# --- Bruit des avions (PEB) ---------------------------------------------------


def _zone_peb(zone, nom="NANTES", date_arret="2004-09-17Z"):
    return {
        "type": "Feature",
        "id": f"dgac_peb_x.{zone}",
        "properties": {
            "zone": zone,
            "indldenext": "50",
            "indldenint": "57",
            "code_oaci": "LFRS",
            "nom": nom,
            "date_arret": date_arret,
            "ref_doc": "http://piece-jointe-carto.developpement-durable.gouv.fr/x.pdf",
        },
    }


def _zone_pgs(zone):
    return {
        "type": "Feature",
        "id": f"dgac_pgs_x.{zone}",
        "properties": {
            "zone": zone,
            "indice_lde": 55,
            "code_oaci": "LFRS",
            "nom": "NANTES",
            "date_arrete": "2003-08-27Z",
        },
    }


def test_peb_and_pgs_zones_are_told_apart_strongest_first():
    lu = partage.bruit_aerien_de(
        {
            "type": "FeatureCollection",
            "features": [_zone_peb("D"), _zone_pgs("3"), _zone_peb("B")],
        }
    )
    assert [z.zone for z in lu.peb] == ["B", "D"]
    assert [z.zone for z in lu.pgs] == ["3"]
    assert lu.peb[0].date_arrete == "2004-09-17"


@pytest.mark.parametrize(
    "donnees",
    [
        {"features": None},
        {
            "type": "FeatureCollection",
            "features": [{"properties": {"zone": "E", "indldenext": "50"}}],
        },
        {"type": "FeatureCollection", "features": [{"properties": {"zone": "A"}}]},
        "<ServiceExceptionReport/>",
    ],
)
def test_an_unreadable_peb_answer_is_an_outage(donnees):
    from app.common.exceptions import UpstreamUnavailableError

    with pytest.raises(UpstreamUnavailableError):
        partage.bruit_aerien_de(donnees)


async def _peb(lu):
    return await peb.lire(_contexte(amonts={"peb": lu}))


async def test_a_peb_zone_names_its_airport_its_order_and_what_it_imposes():
    lu = partage.bruit_aerien_de(
        {"type": "FeatureCollection", "features": [_zone_peb("D"), _zone_pgs("3")]}
    )
    theme = await _peb(lu)
    assert (theme.etat, theme.ton, theme.granularite.niveau) == (
        "ok",
        "vigilance",
        "adresse",
    )
    assert theme.verdict == (
        "Zone D du plan d'exposition au bruit de NANTES (arrêté du 17 septembre "
        "2004) : isolation acoustique imposée aux constructions neuves."
    )
    assert theme.details["zones_pgs"][0]["zone"] == "3"


async def test_the_strongest_of_two_overlapping_plans_counts():
    lu = BruitAerien(
        peb=[
            ZoneBruitAerien("B", "ORLY", "LFPO", None, None),
            ZoneBruitAerien("D", "TOUSSUS", "LFPN", None, None),
        ],
        pgs=[],
    )
    theme = await _peb(lu)
    assert theme.verdict == "Zone B du plan d'exposition au bruit de ORLY : bruit fort."


async def test_outside_any_peb_says_no_plan_and_never_no_aircraft_noise():
    # A plan only covers the surroundings of an airport: outside of it the
    # source says nothing of overflights, so the row neither reassures nor
    # reads « none ».
    hors = await _peb(BruitAerien(peb=[], pgs=[]))
    assert (hors.etat, hors.ton, hors.verdict) == (
        "zero",
        "neutre",
        "Hors de tout plan d'exposition au bruit d'aérodrome ; "
        "le survol en altitude n'est pas cartographié.",
    )
    assert aligner(hors).accroche == "Hors plan"
    panne = await _peb(lieu.PanneAmont())
    assert (panne.etat, panne.ton) == ("panne", "info")


async def test_the_peb_request_fills_the_zones_to_read_inside_them():
    with respx.mock() as mock:
        route = mock.get(url__startswith=partage.WMS_VECTEUR_URL).mock(
            return_value=httpx.Response(
                200, json={"type": "FeatureCollection", "features": [_zone_peb("C")]}
            )
        )
        lu = await peb.THEME.amont(_amont(_contexte()))
    params = parse_qs(urlsplit(str(route.calls[0].request.url)).query)
    assert "<PolygonSymbolizer><Fill>" in params["SLD_BODY"][0]
    assert params["QUERY_LAYERS"] == ["dgac_peb_plan_wmsv,dgac_pgs_plan_wmsv"]
    assert [z.zone for z in lu.peb] == ["C"]


async def test_a_refused_style_is_an_outage_never_outside_any_peb():
    with respx.mock() as mock:
        route = mock.get(url__startswith=partage.WMS_VECTEUR_URL).mock(
            return_value=httpx.Response(
                200,
                content=b"<ServiceExceptionReport/>",
                headers={"content-type": "text/xml"},
            )
        )
        for _ in range(2):
            lu = await peb.THEME.amont(_amont(_contexte()))
            assert isinstance(lu, lieu.PanneAmont)
    assert route.call_count == 2


# --- Secteur affecté par le bruit (PLU) ----------------------------------------


def _info(typeinf, libelle):
    return {"type": "Feature", "properties": {"typeinf": typeinf, "libelle": libelle}}


async def _secteur(lu):
    return await secteur_bruit.lire(_contexte(amonts={"secteur_bruit": lu}))


async def test_a_noise_sector_reported_by_the_plu_is_a_point_to_check():
    lu = partage.informations_plu_de(
        {
            "type": "FeatureCollection",
            "features": [
                _info("19", "zonage pluvial"),
                _info("14", "secteur affecté par le bruit du transport terrestre"),
            ],
        }
    )
    theme = await _secteur(lu)
    assert (theme.etat, theme.ton, theme.granularite.niveau) == (
        "ok",
        "vigilance",
        "adresse",
    )
    assert theme.details["secteurs"] == [
        "secteur affecté par le bruit du transport terrestre"
    ]


async def test_a_plu_without_the_sector_is_a_gap_never_reassuring():
    sans = await _secteur(InformationsPlu(secteurs_bruit=[], informations=3))
    assert (sans.etat, sans.ton, sans.motif) == ("manque", "info", "non_reporte_au_plu")
    assert "fait foi" in sans.verdict
    vide = await _secteur(InformationsPlu(secteurs_bruit=[], informations=0))
    assert vide.motif == "sans_information_plu"
    panne = await _secteur(lieu.PanneAmont())
    assert panne.etat == "panne"


async def test_the_gpu_information_read_is_out_of_coverage_without_urbanisme():
    contexte = _contexte(hors=("urbanisme",))
    with respx.mock(assert_all_called=False) as mock:
        assert await secteur_bruit.THEME.amont(_amont(contexte)) is None
    assert mock.calls.call_count == 0
    assert (await secteur_bruit.lire(contexte)).etat == "hors_couverture"


async def test_the_gpu_request_reads_the_areas_at_the_point_without_geometry():
    with respx.mock() as mock:
        route = mock.get(url__startswith=partage.WFS_URL).mock(
            return_value=httpx.Response(
                200, json={"type": "FeatureCollection", "features": [_info("14", "x")]}
            )
        )
        lu = await secteur_bruit.THEME.amont(_amont(_contexte()))
    params = parse_qs(urlsplit(str(route.calls[0].request.url)).query)
    assert params["TYPENAMES"] == ["wfs_du:info_surf"]
    assert params["CQL_FILTER"] == [
        f"INTERSECTS(the_geom,SRID=4326;POINT({POINT.lon} {POINT.lat}))"
    ]
    assert "the_geom" not in params["PROPERTYNAME"][0]
    assert lu.secteurs_bruit == ["x"]


# --- Données & méthode -----------------------------------------------------------


async def test_the_datasets_page_serves_the_noise_sources_with_their_licence(client):
    from unittest.mock import AsyncMock, patch

    from app.common.source_catalog import DATASET_CATALOG

    with patch("app.datasets.router.get_all_datasets", new=AsyncMock(return_value=[])):
        response = await client.get("/v1/datasets")
    assert response.status_code == 200, response.text
    servis = {jeu["nom"]: jeu for jeu in response.json()["donnees"]}
    for cle in ("axes_transport", "peb"):
        entree = DATASET_CATALOG[cle]
        assert servis[cle]["reutilisation"]["licence"] == entree.licence
        assert servis[cle]["provenance"]["producteur"] == entree.producteur
    assert servis["peb"]["reutilisation"]["licence"] == (
        "Licence non déclarée par le producteur"
    )


# --- Regressions of the first Codex review of 04.7-05 ---------------------------


@pytest.mark.parametrize(
    "donnees",
    [
        # Counts contradicting the features read.
        {
            "type": "FeatureCollection",
            "features": [],
            "numberMatched": 0,
            "numberReturned": 1,
        },
        {
            "type": "FeatureCollection",
            "features": [_route(90), _route(120)],
            "numberMatched": 1,
            "numberReturned": 2,
        },
        # Not a feature collection.
        {"type": "Polygon", "features": [], "numberMatched": 0, "numberReturned": 0},
        {"features": [], "numberMatched": 0, "numberReturned": 0},
    ],
)
def test_a_contradictory_wfs_answer_is_an_outage(donnees):
    from app.common.exceptions import UpstreamUnavailableError

    with pytest.raises(UpstreamUnavailableError):
        _lus(donnees)


async def test_a_contradictory_wfs_answer_is_never_kept_nor_reassuring():
    contradiction = {
        "type": "FeatureCollection",
        "features": [],
        "numberMatched": 0,
        "numberReturned": 1,
    }
    with respx.mock() as mock:
        route = mock.get(url__startswith=partage.WFS_URL).mock(
            return_value=httpx.Response(200, json=contradiction)
        )
        for _ in range(2):
            lu = await grand_axe.amont(_amont(_contexte()))
            assert isinstance(lu, lieu.PanneAmont)
    assert route.call_count == 2
    theme = await grand_axe.lire(_contexte(amonts={"grand_axe": lu}))
    assert theme.etat == "panne"


@pytest.mark.parametrize(
    "donnees",
    [
        {"type": "Polygon", "features": []},
        {"features": []},
        {"type": "FeatureCollection", "features": [], "numberReturned": 1},
        # A full page may hide a zone: never read as complete.
        {
            "type": "FeatureCollection",
            "features": [_zone_pgs("3")] * partage.ZONES_MAX,
        },
    ],
)
def test_a_peb_answer_that_is_not_a_full_collection_is_an_outage(donnees):
    from app.common.exceptions import UpstreamUnavailableError

    with pytest.raises(UpstreamUnavailableError):
        partage.bruit_aerien_de(donnees)


async def test_a_peb_answer_of_another_shape_is_never_outside_any_peb():
    with respx.mock() as mock:
        route = mock.get(url__startswith=partage.WMS_VECTEUR_URL).mock(
            return_value=httpx.Response(200, json={"type": "Polygon", "features": []})
        )
        for _ in range(2):
            lu = await peb.THEME.amont(_amont(_contexte()))
            assert isinstance(lu, lieu.PanneAmont)
    assert route.call_count == 2


def test_a_plu_answer_that_is_not_a_collection_is_an_outage():
    from app.common.exceptions import UpstreamUnavailableError

    with pytest.raises(UpstreamUnavailableError):
        partage.informations_plu_de({"type": "Polygon", "features": []})


async def test_a_motorway_section_of_a_road_named_like_a_nearer_one_still_counts():
    theme = await _grand_axe(
        _route(150, importance="2", numero="N10", classement="Nationale"),
        _route(280, importance="1", numero="N10", classement="Nationale"),
    )
    assert theme.ton == "vigilance"
    assert "N10 (route nationale) à 280 m" in theme.verdict
    assert [(a["importance"], a["distance_m"]) for a in theme.details["axes"]] == [
        ("2", 150),
        ("1", 280),
    ]


async def test_an_incomplete_read_never_claims_the_nearest():
    routes = await _grand_axe(_route(450), matched=5)
    assert "plus proche" not in routes.verdict
    assert routes.verdict == (
        "Grand axe routier observé : A11 (autoroute) à 450 m (lecture incomplète)."
    )
    assert "plus proche" not in routes.chiffres[0].libelle
    rail = await _voie(_rail(450), matched=5)
    assert "plus proche" not in rail.verdict
    assert "plus proche" not in rail.chiffres[0].libelle


def _vincenty_m(lon1, lat1, lon2, lat2):
    """Geodesic distance on WGS84 (Vincenty's inverse formula), as reference."""
    a, f = 6_378_137.0, 1 / 298.257223563
    b = (1 - f) * a
    u1 = math.atan((1 - f) * math.tan(math.radians(lat1)))
    u2 = math.atan((1 - f) * math.tan(math.radians(lat2)))
    ecart = math.radians(lon2 - lon1)
    lam = ecart
    for _ in range(200):
        sin_l, cos_l = math.sin(lam), math.cos(lam)
        sin_s = math.hypot(
            math.cos(u2) * sin_l,
            math.cos(u1) * math.sin(u2) - math.sin(u1) * math.cos(u2) * cos_l,
        )
        cos_s = math.sin(u1) * math.sin(u2) + math.cos(u1) * math.cos(u2) * cos_l
        sigma = math.atan2(sin_s, cos_s)
        sin_a = math.cos(u1) * math.cos(u2) * sin_l / sin_s
        cos2_a = 1 - sin_a**2
        cos_2sm = cos_s - 2 * math.sin(u1) * math.sin(u2) / cos2_a
        c = f / 16 * cos2_a * (4 + f * (4 - 3 * cos2_a))
        precedent = lam
        lam = ecart + (1 - c) * f * sin_a * (
            sigma + c * sin_s * (cos_2sm + c * cos_s * (-1 + 2 * cos_2sm**2))
        )
        if abs(lam - precedent) < 1e-12:
            break
    u_2 = cos2_a * (a * a - b * b) / (b * b)
    grand_a = 1 + u_2 / 16384 * (4096 + u_2 * (-768 + u_2 * (320 - 175 * u_2)))
    grand_b = u_2 / 1024 * (256 + u_2 * (-128 + u_2 * (74 - 47 * u_2)))
    delta = (
        grand_b
        * sin_s
        * (
            cos_2sm
            + grand_b
            / 4
            * (
                cos_s * (-1 + 2 * cos_2sm**2)
                - grand_b / 6 * cos_2sm * (-3 + 4 * sin_s**2) * (-3 + 4 * cos_2sm**2)
            )
        )
    )
    return b * grand_a * (sigma - delta)


@pytest.mark.parametrize(
    "point", [PointBan(lon=-1.55, lat=47.21), PointBan(lon=6.31, lat=43.39)]
)
@pytest.mark.parametrize(("dlon", "dlat"), [(0.0066, 0.0), (0.0, 0.0045)])
def test_the_distance_holds_to_a_decimetre_on_the_ellipsoid(point, dlon, dlat):
    lon, lat = point.lon + dlon, point.lat + dlat
    if dlat:  # a west-east line north of the point
        ligne = [[point.lon - 0.01, lat], [point.lon + 0.01, lat]]
    else:  # a south-north line east of the point
        ligne = [[lon, point.lat - 0.01], [lon, point.lat + 0.01]]
    reference = _vincenty_m(point.lon, point.lat, lon, lat)
    calcule = partage.distance_ligne_m(
        point, {"type": "LineString", "coordinates": ligne}
    )
    assert calcule == pytest.approx(reference, abs=0.1)


async def test_a_long_airport_name_keeps_the_peb_verdict_within_its_bound():
    zone = ZoneBruitAerien("D", "X" * 80, "LFXX", "2004-09-17", None)
    theme = await _peb(BruitAerien(peb=[zone], pgs=[]))
    assert len(theme.verdict) <= 140
    assert "LFXX" in theme.verdict


# --- Regressions of the second Codex review of 04.7-05 --------------------------


@pytest.mark.parametrize("compteur", ["numberMatched", "totalFeatures"])
@pytest.mark.parametrize("lecteur", ["bruit_aerien_de", "informations_plu_de"])
def test_a_zone_announced_but_not_served_is_an_outage(compteur, lecteur):
    from app.common.exceptions import UpstreamUnavailableError

    donnees = {"type": "FeatureCollection", "features": [], compteur: 1}
    with pytest.raises(UpstreamUnavailableError):
        getattr(partage, lecteur)(donnees)


async def test_an_incomplete_peb_answer_is_never_kept_nor_reassuring():
    incomplet = {
        "type": "FeatureCollection",
        "features": [],
        "numberMatched": 1,
        "numberReturned": 0,
    }
    with respx.mock() as mock:
        route = mock.get(url__startswith=partage.WMS_VECTEUR_URL).mock(
            return_value=httpx.Response(200, json=incomplet)
        )
        for _ in range(2):
            lu = await peb.THEME.amont(_amont(_contexte()))
            assert isinstance(lu, lieu.PanneAmont)
    assert route.call_count == 2
    assert (await _peb(lu)).etat == "panne"


def test_the_unknown_total_of_a_getfeatureinfo_is_not_a_contradiction():
    lu = partage.bruit_aerien_de(
        {
            "type": "FeatureCollection",
            "features": [_zone_peb("D")],
            "totalFeatures": "unknown",
            "numberReturned": 1,
        }
    )
    assert [z.zone for z in lu.peb] == ["D"]
