"""Living-here themes of the address report, part 2 (04.7-07), without a database.

Air quality is the ATMO index of the day for the commune, else for its EPCI:
a forecast of one day, never an exposure, and the annual means stay out of
coverage until they are imported. Incomes are read in the 200 m cell holding
the point (a mean) before the commune (a median), never compared; the IRIS
is named with its socio-professional make-up. Transports name the nearest
station; the frequency at peak hour and the lines are out of coverage until
the GTFS timetables are imported.
"""

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx

from app.adresse import lieu
from app.adresse.relations import PointBan
from app.adresse.schemas import CommuneRef, ElementLieu
from app.common.exceptions import UpstreamUnavailableError
from app.rapport import partage
from app.rapport.partage import (
    AirLu,
    Carreau,
    CarreauLu,
    CompositionIris,
    GareProche,
    IndiceAtmo,
    Iris,
    IrisLu,
    RevenusAmont,
)
from app.rapport.socle import ContexteAmont, Memo, PanneAmont
from app.rapport.themes import qualite_air, revenus, transports
from tests.test_rapport_themes import (  # noqa: F401 (autouse fixtures)
    AS_OF,
    _champ,
    _contexte,
    _publication_validee,
    _requete,
    _reseaux,
    _sans_provenance,
)

pytestmark = pytest.mark.unit

JOUR = AS_OF.isoformat()


@pytest.fixture(autouse=True)
def _caches_vides():
    partage.vider_caches()
    yield
    partage.vider_caches()


def _amont(contexte):
    return ContexteAmont(
        identite=contexte.identite,
        point=contexte.point,
        hosting=contexte.hosting,
        as_of=AS_OF,
        memo=Memo(),
    )


def _avec_commune(contexte, code, nom="Commune"):
    return contexte.__class__(
        **{
            **contexte.__dict__,
            "identite": contexte.identite.model_copy(
                update={"commune": CommuneRef(code_insee=code, nom=nom)}
            ),
        }
    )


def _collection(*proprietes, **annonce):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": None, "properties": p} for p in proprietes
        ],
        "numberMatched": annonce.get("matched", len(proprietes)),
        "numberReturned": len(proprietes),
    }


# --- Qualité de l'air : lecteur ATMO ---------------------------------------------


def _ligne_atmo(code_zone="44109", code_qual=2, type_zone="commune", **autres):
    return {
        "code_zone": code_zone,
        "code_qual": code_qual,
        "lib_qual": "moyen",
        "type_zone": type_zone,
        "lib_zone": "Nantes",
        "source": "Air Pays de la Loire",
        "date_ech": JOUR,
        "code_no2": 1,
        "code_o3": 2,
        "code_pm10": 1,
        "code_pm25": 2,
        "code_so2": 1,
        **autres,
    }


def test_the_index_is_read_on_its_code_never_on_its_label():
    # Probe of 2026-10-31: « moyen » and « Moyen » are both served.
    indices = partage.indices_atmo_de(
        _collection(
            _ligne_atmo(lib_qual="Moyen", type_zone="COMMUNE"),
            _ligne_atmo("200073260", type_zone="epci"),
        ),
        JOUR,
    )
    assert indices["44109"] == IndiceAtmo(
        code=2,
        date=JOUR,
        zone_code="44109",
        zone_nom="Nantes",
        zone_type="commune",
        aasqa="Air Pays de la Loire",
        sous_indices={"no2": 1, "o3": 2, "pm10": 1, "pm25": 2, "so2": 1},
    )
    assert indices["200073260"].zone_type == "epci"


def test_no_row_of_the_day_is_no_index_not_an_outage():
    assert partage.indices_atmo_de(_collection(), JOUR) == {}


def test_two_rows_of_one_zone_that_disagree_choose_none():
    indices = partage.indices_atmo_de(
        _collection(
            _ligne_atmo(code_qual=2),
            _ligne_atmo(code_qual=3),
            _ligne_atmo("200073260", type_zone="EPCI"),
        ),
        JOUR,
    )
    assert indices["44109"] is None
    assert indices["200073260"] is not None
    # Two identical rows are one index.
    assert partage.indices_atmo_de(_collection(_ligne_atmo(), _ligne_atmo()), JOUR)[
        "44109"
    ]


@pytest.mark.parametrize(
    "donnees",
    [
        _collection(_ligne_atmo(code_qual="2")),
        _collection(_ligne_atmo(code_qual=8)),
        _collection(_ligne_atmo(code_qual=True)),
        _collection(_ligne_atmo(date_ech="2026-10-29")),
        _collection(_ligne_atmo(type_zone="region")),
        _collection(_ligne_atmo(code_no2="1")),
        _collection(_ligne_atmo(), matched=2),
        {"type": "FeatureCollection"},
        [],
    ],
)
def test_an_unreadable_atmo_answer_is_an_outage(donnees):
    with pytest.raises(UpstreamUnavailableError):
        partage.indices_atmo_de(donnees, JOUR)


def _filtre(requete: httpx.Request) -> str:
    return parse_qs(urlsplit(str(requete.url)).query)["CQL_FILTER"][0]


async def test_the_commune_is_read_first_then_its_epci():
    contexte = _avec_commune(_contexte(), "85001", "L'Aiguillon-la-Presqu'île")
    with respx.mock() as mock:
        atmo = mock.get(url__startswith=partage.ATMO_WFS_URL).mock(
            side_effect=[
                httpx.Response(200, json=_collection()),
                httpx.Response(
                    200,
                    json=_collection(
                        _ligne_atmo(
                            "200073260",
                            type_zone="EPCI",
                            lib_zone="CC Sud Vendée Littoral",
                        )
                    ),
                ),
            ]
        )
        geo = mock.get(partage.GEO_API_COMMUNE_URL.format(code="85001")).mock(
            return_value=httpx.Response(200, json={"codeEpci": "200073260"})
        )
        lu = await partage.air_au_point(_amont(contexte))
    assert isinstance(lu, AirLu)
    assert lu.indice is not None and lu.indice.zone_code == "200073260"
    assert [_filtre(c.request) for c in atmo.calls] == [
        f"code_zone IN ('85001') AND date_ech='{JOUR}'",
        f"code_zone IN ('200073260') AND date_ech='{JOUR}'",
    ]
    assert geo.call_count == 1


async def test_an_arrondissement_is_read_before_its_commune():
    contexte = _avec_commune(_contexte(), "75112", "Paris 12e Arrondissement")
    # The EPCI lookup is cancelled once the commune answers.
    with respx.mock(assert_all_called=False) as mock:
        atmo = mock.get(url__startswith=partage.ATMO_WFS_URL).mock(
            return_value=httpx.Response(
                200, json=_collection(_ligne_atmo("75112", lib_zone="Paris 12e"))
            )
        )
        mock.get(url__startswith="https://geo.api.gouv.fr/communes/").mock(
            return_value=httpx.Response(503)
        )
        lu = await partage.air_au_point(_amont(contexte))
    # One read for the arrondissement and its commune; the EPCI lookup down
    # does not matter when a zone of the commune answers.
    assert lu.indice.zone_code == "75112"
    assert atmo.call_count == 1
    assert _filtre(atmo.calls[0].request) == (
        f"code_zone IN ('75112','75056') AND date_ech='{JOUR}'"
    )


async def test_a_commune_without_index_nor_epci_has_no_index():
    with respx.mock() as mock:
        mock.get(url__startswith=partage.ATMO_WFS_URL).mock(
            return_value=httpx.Response(200, json=_collection())
        )
        mock.get(url__startswith="https://geo.api.gouv.fr/communes/").mock(
            return_value=httpx.Response(200, json={"code": "44109"})
        )
        lu = await partage.air_au_point(_amont(_contexte()))
    assert lu == AirLu(indice=None, motif="aucun_indice_du_jour")


@pytest.mark.parametrize(
    "panne",
    [
        httpx.Response(500),
        httpx.Response(200, text="<html>"),
        httpx.ConnectError("coupé"),
    ],
)
async def test_an_atmo_outage_is_an_outage_never_kept(panne):
    with respx.mock() as mock:
        route = mock.get(url__startswith=partage.ATMO_WFS_URL)
        route.mock(side_effect=[panne, panne])
        assert isinstance(await partage.air_au_point(_amont(_contexte())), PanneAmont)
        route.mock(return_value=httpx.Response(200, json=_collection(_ligne_atmo())))
        lu = await partage.air_au_point(_amont(_contexte()))
    assert isinstance(lu, AirLu) and lu.indice is not None


async def test_an_epci_lookup_outage_is_an_outage():
    with respx.mock() as mock:
        mock.get(url__startswith=partage.ATMO_WFS_URL).mock(
            return_value=httpx.Response(200, json=_collection())
        )
        mock.get(url__startswith="https://geo.api.gouv.fr/communes/").mock(
            return_value=httpx.Response(503)
        )
        assert isinstance(await partage.air_au_point(_amont(_contexte())), PanneAmont)


async def test_an_unknown_commune_for_geo_api_has_no_epci():
    with respx.mock() as mock:
        mock.get(url__startswith=partage.ATMO_WFS_URL).mock(
            return_value=httpx.Response(200, json=_collection())
        )
        mock.get(url__startswith="https://geo.api.gouv.fr/communes/").mock(
            return_value=httpx.Response(404)
        )
        lu = await partage.air_au_point(_amont(_contexte()))
    assert lu == AirLu(indice=None, motif="aucun_indice_du_jour")


async def test_air_not_hosted_reads_nothing():
    contexte = _contexte(hors=("qualite_air",))
    with respx.mock() as mock:
        assert await partage.air_au_point(_amont(contexte)) is None
        assert not mock.calls


async def test_a_malformed_code_is_never_sent_in_a_filter():
    contexte = _avec_commune(_contexte(), "44109' OR '1'='1")
    with respx.mock() as mock:
        lu = await partage.air_au_point(_amont(contexte))
        assert not mock.calls
    # Nothing was read: never « no index published ».
    assert lu == AirLu(indice=None, motif="commune_indeterminee")


# --- Qualité de l'air : thème ----------------------------------------------------


def _indice(code=2, zone_type="commune", zone_code="44109", zone_nom="Nantes"):
    return IndiceAtmo(
        code=code,
        date=JOUR,
        zone_code=zone_code,
        zone_nom=zone_nom,
        zone_type=zone_type,
        aasqa="Air Pays de la Loire",
        sous_indices={"no2": 1, "o3": 2, "pm10": 1, "pm25": 2, "so2": None},
    )


async def _air(lu, contexte=None):
    contexte = contexte or _contexte()
    contexte.amonts["qualite_air"] = lu
    return await qualite_air.lire(contexte)


@pytest.mark.parametrize(
    ("code", "libelle"),
    [(1, "bon"), (2, "moyen"), (3, "dégradé"), (4, "mauvais"),
     (5, "très mauvais"), (6, "extrêmement mauvais")],
)  # fmt: skip
async def test_the_index_of_the_day_is_a_neutral_forecast(code, libelle):
    theme = await _air(AirLu(indice=_indice(code)))
    assert (theme.etat, theme.ton, theme.granularite.niveau) == (
        "ok",
        "neutre",
        "commune",
    )
    assert theme.verdict == (
        f"Qualité de l'air prévue le 30 octobre 2026 : {libelle} ({code} sur 6), "
        "pour la commune."
    )
    assert [(c.id, c.valeur, c.millesime) for c in theme.chiffres] == [
        ("indice_atmo", code, JOUR)
    ]
    assert theme.details["indice"]["libelle"] == libelle
    assert theme.details["moyennes_annuelles"] == {
        "etat": "hors_couverture",
        "motif": "moyennes_geodair_non_importees",
    }
    assert theme.sources[0].millesime == JOUR


async def test_the_index_of_an_epci_says_the_epci():
    theme = await _air(
        AirLu(
            indice=_indice(
                zone_type="epci",
                zone_code="200073260",
                zone_nom="CC Sud Vendée Littoral",
            )
        )
    )
    assert theme.granularite.niveau == "epci"
    assert theme.verdict.endswith("pour l'intercommunalité (CC Sud Vendée Littoral).")
    assert theme.details["zone"] == {
        "type": "epci",
        "code": "200073260",
        "nom": "CC Sud Vendée Littoral",
    }
    long = await _air(
        AirLu(indice=_indice(zone_type="epci", zone_nom="X" * 120, zone_code="1"))
    )
    assert long.verdict.endswith("pour l'intercommunalité.")


async def test_the_index_of_an_arrondissement_says_the_arrondissement():
    contexte = _avec_commune(_contexte(), "75112")
    theme = await _air(AirLu(indice=_indice(zone_code="75112")), contexte)
    assert theme.verdict.endswith("pour l'arrondissement.")
    # 04.7-09: the arrondissement is a level of its own (Opus 07, M4).
    assert theme.granularite.niveau == "arrondissement"
    assert theme.granularite.arrondissement.nom == "12e arrondissement de Paris"


async def test_an_absent_index_and_no_index_are_gaps():
    absent = await _air(AirLu(indice=_indice(0)))
    assert (absent.etat, absent.motif, absent.ton) == (
        "manque",
        "indice_absent",
        "info",
    )
    aucun = await _air(AirLu(indice=None, motif="aucun_indice_du_jour"))
    assert (aucun.etat, aucun.motif) == ("manque", "aucun_indice_du_jour")
    assert "Aucun indice" in aucun.verdict
    contradictoire = await _air(AirLu(indice=None, motif="indices_contradictoires"))
    assert (contradictoire.etat, contradictoire.motif) == (
        "manque",
        "indices_contradictoires",
    )


async def test_an_event_index_is_said_as_such():
    theme = await _air(AirLu(indice=_indice(7)))
    assert (theme.etat, theme.ton) == ("ok", "neutre")
    assert "événement" in theme.verdict
    assert theme.chiffres == []


async def test_air_outage_and_out_of_coverage_are_told_apart():
    panne = await _air(PanneAmont())
    assert panne.etat == "panne"
    hors = await qualite_air.lire(_contexte(hors=("qualite_air",)))
    assert (hors.etat, hors.motif) == ("hors_couverture", "dataset_non_heberge")


# --- Revenus : lecteurs ----------------------------------------------------------


def _carreau_brut(**autres):
    return {
        "id_inspire": "CRS3035RES200mN2746200E3450000",
        "ind": 101.5,
        "men": 55,
        "ind_snv": 2516578.3,
        "men_pauv": 8,
        "i_car_est": 0,
        "depcom": "44109",
        **autres,
    }


def test_the_cell_living_standard_is_a_mean():
    lu = partage.carreau_de(_collection(_carreau_brut()))
    assert lu == CarreauLu(
        carreau=Carreau(
            id="CRS3035RES200mN2746200E3450000",
            individus=101.5,
            menages=55,
            niveau_vie_moyen=24794,
            part_menages_pauvres=14.5,
            impute=False,
        )
    )


def test_the_cell_rounds_half_up():
    brut = _carreau_brut(ind=4, men=400, ind_snv=98_002, men_pauv=1)
    carreau = partage.carreau_de(_collection(brut)).carreau
    assert carreau.niveau_vie_moyen == 24_501  # `round`: 24 500, half to even
    assert carreau.part_menages_pauvres == 0.3  # `round`: 0.2


def test_no_cell_and_two_cells_are_told_apart():
    assert partage.carreau_de(_collection()) == CarreauLu(
        carreau=None, motif="aucun_carreau"
    )
    assert partage.carreau_de(
        _collection(_carreau_brut(), _carreau_brut(id_inspire="autre"))
    ) == CarreauLu(carreau=None, motif="carreaux_multiples")


@pytest.mark.parametrize(
    "brut",
    [
        _carreau_brut(ind=0),
        _carreau_brut(ind="101"),
        _carreau_brut(men=None),
        _carreau_brut(i_car_est=None),
        _carreau_brut(id_inspire=None),
    ],
)
def test_an_unreadable_cell_is_an_outage(brut):
    with pytest.raises(UpstreamUnavailableError):
        partage.carreau_de(_collection(brut))


def _melodi(*observations, suivante=False):
    paging = {"first": "x"}
    if suivante:
        paging["next"] = "y"
    return {"observations": list(observations), "paging": paging}


def _obs(pcs, valeur, sexe="_T", annee="2023", geo="2025-IRIS-441090507"):
    return {
        "dimensions": {
            "PCS": pcs,
            "SEX": sexe,
            "AGE": "Y_GE15",
            "TIME_PERIOD": annee,
            "GEO": geo,
        },
        "measures": {"OBS_VALUE_NIVEAU": {"value": valeur}},
    }


GEO_IRIS = "2025-IRIS-441090507"


def _complete(total=100.0, **autres):
    """Every category the census serves for an IRIS, the rest at zero."""
    parts = {pcs: 0.0 for pcs in sorted(partage.PCS_IRIS)} | {
        pcs.removeprefix("pcs"): v for pcs, v in autres.items()
    }
    return _melodi(_obs("_T", total), *(_obs(pcs, v) for pcs, v in parts.items()))


def test_the_iris_make_up_is_a_share_of_the_population_over_fifteen():
    donnees = _complete(3042.4, pcs3=764.2, pcs6=342.1)
    donnees["observations"].append(_obs("3", 400.0, sexe="M"))
    composition = partage.composition_iris_de(donnees, GEO_IRIS)
    assert composition == CompositionIris(
        annee="2023",
        population_15_ans_plus=3042,
        parts={"1": 0.0, "2": 0.0, "3": 25.1, "4": 0.0, "5": 0.0, "6": 11.2,
               "7": 0.0, "9": 0.0},
    )  # fmt: skip


def test_the_iris_make_up_rounds_half_up():
    composition = partage.composition_iris_de(
        _complete(400.0, pcs3=1.0, pcs6=399.0), GEO_IRIS
    )
    assert composition.parts["3"] == 0.3  # `round`: 0.2


@pytest.mark.parametrize(
    "donnees",
    [
        _melodi(_obs("_T", 10.0), suivante=True),  # a page left unread
        _melodi(_obs("3", 1.0)),  # no total
        _melodi(_obs("_T", 10.0), _obs("3", "1")),
        _melodi(_obs("_T", 10.0), _obs("3", 1.0, annee="2022")),
        {"paging": {}},
    ],
)
def test_an_unreadable_melodi_answer_is_an_outage(donnees):
    with pytest.raises(UpstreamUnavailableError):
        partage.composition_iris_de(donnees, GEO_IRIS)


def test_an_iris_without_population_has_no_make_up():
    assert partage.composition_iris_de(_melodi(), GEO_IRIS) is None
    assert partage.composition_iris_de(_melodi(_obs("_T", 0.0)), GEO_IRIS) is None


def test_the_iris_holding_the_point_is_read_from_its_contours():
    propriete = {
        "code_iris": "441090507",
        "nom_iris": "Vieux Malakoff",
        "type_iris": "A",
        "code_insee": "44109",
    }
    assert partage.iris_de(_collection(propriete)) == IrisLu(
        iris=Iris(code="441090507", nom="Vieux Malakoff", type="A")
    )
    assert partage.iris_de(_collection()) == IrisLu(iris=None, motif="aucun_iris")
    autre = {**propriete, "code_iris": "441090508"}
    assert partage.iris_de(_collection(propriete, autre)) == IrisLu(
        iris=None, motif="iris_multiples"
    )
    with pytest.raises(UpstreamUnavailableError):
        partage.iris_de(_collection({"code_iris": None}))


async def test_the_income_readers_read_the_cell_and_the_iris_at_the_point():
    with respx.mock() as mock:
        wfs = mock.get(url__startswith=partage.WFS_URL).mock(
            side_effect=lambda requete: httpx.Response(
                200,
                json=_collection(_carreau_brut())
                if "FILOSOFI" in str(requete.url)
                else _collection(
                    {"code_iris": "441090507", "nom_iris": "V", "type_iris": "A"}
                ),
            )
        )
        melodi = mock.get(url__startswith=partage.MELODI_URL).mock(
            return_value=httpx.Response(200, json=_complete())
        )
        lu = await partage.revenus_au_point(_amont(_contexte()))
    assert isinstance(lu, RevenusAmont)
    assert lu.carreau.carreau.niveau_vie_moyen == 24794
    assert lu.iris.iris.code == "441090507"
    assert lu.iris.composition.population_15_ans_plus == 100
    filtres = sorted(_filtre(c.request) for c in wfs.calls)
    assert filtres == [
        "INTERSECTS(geom,SRID=4326;POINT(-1.55 47.21))",
        "INTERSECTS(geometrie,SRID=4326;POINT(-1.55 47.21))",
    ]
    assert "GEO=2025-IRIS-441090507" in str(melodi.calls[0].request.url)


async def test_a_melodi_outage_keeps_the_iris_name():
    with respx.mock() as mock:
        mock.get(url__startswith=partage.WFS_URL).mock(
            side_effect=lambda requete: httpx.Response(
                200,
                json=_collection(_carreau_brut())
                if "FILOSOFI" in str(requete.url)
                else _collection(
                    {"code_iris": "441090507", "nom_iris": "V", "type_iris": "A"}
                ),
            )
        )
        mock.get(url__startswith=partage.MELODI_URL).mock(
            return_value=httpx.Response(500)
        )
        lu = await partage.revenus_au_point(_amont(_contexte()))
    assert lu.iris.iris.code == "441090507"
    assert lu.iris.composition is None and lu.iris.panne_composition


async def test_a_cell_outage_does_not_lose_the_iris():
    with respx.mock() as mock:
        mock.get(url__startswith=partage.WFS_URL).mock(
            side_effect=lambda requete: (
                httpx.Response(500)
                if "FILOSOFI" in str(requete.url)
                else httpx.Response(
                    200,
                    json=_collection(
                        {"code_iris": "441090507", "nom_iris": "V", "type_iris": "A"}
                    ),
                )
            )
        )
        mock.get(url__startswith=partage.MELODI_URL).mock(
            return_value=httpx.Response(200, json=_melodi())
        )
        lu = await partage.revenus_au_point(_amont(_contexte()))
    assert isinstance(lu.carreau, PanneAmont)
    assert lu.iris.iris.code == "441090507"


async def test_income_readers_not_hosted_read_nothing():
    contexte = _contexte(hors=("filosofi_carreaux", "iris"))
    with respx.mock() as mock:
        lu = await partage.revenus_au_point(_amont(contexte))
        assert not mock.calls
    assert (lu.carreau, lu.iris) == (None, None)


# --- Revenus : thème -------------------------------------------------------------


COMMUNE = [
    {
        "code_insee": "44109",
        "revenu_median": 24310,
        "taux_pauvrete": 16.1,
        "annee": 2021,
    }
]


def _carreau(impute=False):
    return CarreauLu(
        carreau=Carreau(
            id="CRS3035RES200mN2746200E3450000",
            individus=101.5,
            menages=55,
            niveau_vie_moyen=24794,
            part_menages_pauvres=14.5,
            impute=impute,
        )
    )


def _iris(composition=True, panne=False):
    return IrisLu(
        iris=Iris(code="441090507", nom="Vieux Malakoff", type="A"),
        composition=CompositionIris(
            annee="2023", population_15_ans_plus=3042, parts={"3": 25.1}
        )
        if composition
        else None,
        panne_composition=panne,
    )


async def _revenus(monkeypatch, carreau, iris, lignes=COMMUNE, contexte=None):
    monkeypatch.setattr(revenus, "revenus_par_codes", _requete(lignes))
    contexte = contexte or _contexte()
    contexte.amonts["revenus"] = RevenusAmont(carreau=carreau, iris=iris)
    return await revenus.lire(contexte)


async def test_income_figures_follow_the_commune_fiche(monkeypatch):
    # Contract: no decimal for the median, two for the poverty rate.
    lignes = [{**COMMUNE[0], "revenu_median": 24310.5, "taux_pauvrete": 16.96}]
    theme = await _revenus(monkeypatch, _carreau(), _iris(), lignes=lignes)
    assert [c.valeur for c in theme.chiffres] == [24311, 16.96]
    assert "24\u202f311 €" in theme.verdict


async def test_the_verdict_is_the_commune_and_the_cell_of_2015_is_a_detail(
    monkeypatch,
):
    theme = await _revenus(monkeypatch, _carreau(), _iris())
    assert (theme.etat, theme.ton, theme.granularite.niveau) == (
        "ok",
        "neutre",
        "commune",
    )
    assert theme.verdict.startswith("Revenu médian des habitants de la commune")
    # The mean of the cell (2015) is never a figure beside the median (2021).
    assert [c.id for c in theme.chiffres] == ["revenu_median", "taux_pauvrete"]
    carreau = theme.details["filosofi_carreau"]
    assert (carreau["etat"], carreau["millesime"], carreau["niveau_vie_moyen"]) == (
        "ok",
        "2015",
        24794,
    )
    assert theme.details["iris"] == {
        "etat": "ok",
        "code": "441090507",
        "nom": "Vieux Malakoff",
        "type": "A",
        "composition": {
            "etat": "ok",
            "annee": "2023",
            "population_15_ans_plus": 3042,
            "parts": {"3": 25.1},
        },
    }
    millesimes = {s.cle: s.millesime for s in theme.sources}
    assert millesimes == {
        "revenus": "2021",
        "filosofi_carreaux": "2015",
        "iris": "2023",
    }
    assert theme.pannes_partielles == []


async def test_an_imputed_or_missing_cell_is_a_gap_of_the_detail(monkeypatch):
    impute = await _revenus(monkeypatch, _carreau(impute=True), _iris())
    assert impute.details["filosofi_carreau"] == {
        "etat": "manque",
        "motif": "carreau_impute",
    }
    assert {s.cle: s.millesime for s in impute.sources}["filosofi_carreaux"] is None
    absent = await _revenus(
        monkeypatch,
        CarreauLu(carreau=None, motif="aucun_carreau"),
        None,
        contexte=_contexte(hors=("iris",)),
    )
    assert absent.etat == "ok"
    assert absent.details["filosofi_carreau"] == {
        "etat": "manque",
        "motif": "aucun_carreau",
    }
    assert absent.details["iris"] == {
        "etat": "hors_couverture",
        "motif": "dataset_non_heberge",
    }


async def test_an_outage_of_the_detail_never_changes_the_state(monkeypatch):
    servi = await _revenus(
        monkeypatch, PanneAmont(), _iris(composition=False, panne=True)
    )
    assert (servi.etat, servi.ton) == ("ok", "neutre")
    assert servi.pannes_partielles == ["filosofi_carreaux", "iris"]
    assert servi.details["iris"]["composition"] == {
        "etat": "panne",
        "motif": "amont_indisponible",
    }
    sans = [{"code_insee": "44109", "revenu_median": None, "annee": 2021}]
    manque = await _revenus(
        monkeypatch, PanneAmont(), _iris(composition=False, panne=True), lignes=sans
    )
    assert (manque.etat, manque.motif) == ("manque", "revenu_non_publie")
    assert manque.pannes_partielles == ["filosofi_carreaux", "iris"]
    hors = await _revenus(
        monkeypatch, PanneAmont(), None, contexte=_contexte(hors=("revenus", "iris"))
    )
    assert (hors.etat, hors.motif) == ("hors_couverture", "dataset_non_heberge")


async def test_an_iris_unknown_to_the_census_is_a_gap(monkeypatch):
    lu = IrisLu(
        iris=Iris(code="441090507", nom="V", type="A"),
        motif_composition="iris_hors_recensement",
    )
    theme = await _revenus(monkeypatch, _carreau(), lu)
    assert theme.details["iris"]["composition"] == {
        "etat": "manque",
        "motif": "iris_hors_recensement",
    }
    assert theme.pannes_partielles == []


# --- Transports ------------------------------------------------------------------


def _arret(distance=180):
    return ElementLieu(libelle="Commerce", distance_m=distance, nature="arret")


def _gare(monkeypatch, valeur):
    async def lu(contexte):
        return valeur

    monkeypatch.setattr(transports, "gare_proche", lu)


async def test_the_nearest_station_is_named_with_its_distance(monkeypatch):
    _reseaux(
        monkeypatch,
        [_champ(lieu.FIELD_ARRETS, valeur=1, elements=[_arret()], rayon_m=1000)],
    )
    _gare(monkeypatch, GareProche(nom="Nantes", distance_m=1712, complete=True))
    theme = await transports.lire(_contexte())
    assert theme.details["gare"] == {
        "etat": "ok",
        "nom": "Nantes",
        "distance_m": 1712,
        "plus_proche_connue": False,
    }
    for cle in ("frequence_pointe", "lignes"):
        assert theme.details[cle] == {
            "etat": "hors_couverture",
            "motif": "horaires_gtfs_non_importes",
        }
    # The verdict and the tone stay those of the nearest stop.
    assert (theme.etat, theme.ton) == ("ok", "rassurant")


async def test_a_station_beyond_the_hosted_area_is_the_nearest_known(monkeypatch):
    _reseaux(
        monkeypatch,
        [_champ(lieu.FIELD_ARRETS, valeur=1, elements=[_arret()], rayon_m=1000)],
    )
    _gare(monkeypatch, GareProche(nom="Clisson", distance_m=14200, complete=False))
    theme = await transports.lire(_contexte())
    assert theme.details["gare"]["plus_proche_connue"] is True


HORS_PERIMETRE = {"etat": "hors_couverture", "motif": "territoire_hors_perimetre"}


@pytest.mark.parametrize(
    ("valeur", "attendu"),
    [
        (
            GareProche(nom=None, distance_m=None, complete=True),
            {"etat": "zero", "rayon_m": 20000},
        ),
        (GareProche(nom=None, distance_m=None, complete=False), HORS_PERIMETRE),
        (PanneAmont(), {"etat": "panne", "motif": "lecture_indisponible"}),
        (None, {"etat": "manque", "motif": "position_indeterminee"}),
    ],
)
async def test_no_station_is_told_apart_from_an_outage(monkeypatch, valeur, attendu):
    _reseaux(
        monkeypatch,
        [_champ(lieu.FIELD_ARRETS, valeur=1, elements=[_arret()], rayon_m=1000)],
    )
    _gare(monkeypatch, valeur)
    theme = await transports.lire(_contexte())
    assert theme.details["gare"] == attendu
    assert theme.pannes_partielles == (
        ["gares"] if isinstance(valeur, PanneAmont) else []
    )


async def test_the_station_reader_reads_nothing_when_stations_are_not_hosted():
    contexte = _contexte(hors=("gares",))
    assert await partage.gare_proche(contexte) is None
    sans_point = contexte.__class__(**{**_contexte().__dict__, "point": None})
    assert await partage.gare_proche(sans_point) is None


async def test_the_station_reader_says_an_overflow(monkeypatch):
    async def gare(session, lon, lat, rayon_m):
        return {"nom_gare": " Clisson ", "distance_m": 14200.4}

    async def deborde(session, lon, lat, rayon_m, heberges):
        assert rayon_m == 14200.4
        return True

    monkeypatch.setattr(partage, "gare_la_plus_proche", gare)
    monkeypatch.setattr(partage, "rayon_deborde", deborde)
    assert await partage.gare_proche(_contexte()) == GareProche(
        nom="Clisson", distance_m=14200, complete=False
    )

    async def aucune(session, lon, lat, rayon_m):
        return None

    async def interieur(session, lon, lat, rayon_m, heberges):
        assert rayon_m == partage.RAYON_GARE_M
        return False

    monkeypatch.setattr(partage, "gare_la_plus_proche", aucune)
    monkeypatch.setattr(partage, "rayon_deborde", interieur)
    assert await partage.gare_proche(_contexte()) == GareProche(
        nom=None, distance_m=None, complete=True
    )


def test_point_is_shared_with_the_report_tests():
    # The readers above build their filters on the report test point.
    assert PointBan(lon=-1.55, lat=47.21) == _contexte().point


# --- Revue Codex n° 1 -------------------------------------------------------------


async def test_no_index_of_the_day_is_never_kept():
    # The index is published once a day: a read before it is not kept.
    with respx.mock() as mock:
        atmo = mock.get(url__startswith=partage.ATMO_WFS_URL).mock(
            side_effect=[
                httpx.Response(200, json=_collection()),
                httpx.Response(200, json=_collection(_ligne_atmo())),
            ]
        )
        mock.get(url__startswith="https://geo.api.gouv.fr/communes/").mock(
            return_value=httpx.Response(200, json={})
        )
        premier = await partage.air_au_point(_amont(_contexte()))
        second = await partage.air_au_point(_amont(_contexte()))
    assert premier == AirLu(indice=None, motif="aucun_indice_du_jour")
    assert second.indice is not None
    assert atmo.call_count == 2


async def test_a_slow_epci_lookup_never_loses_the_index_of_the_commune(monkeypatch):
    import asyncio

    monkeypatch.setattr(partage, "DEADLINE_S", 0.5)

    async def lent(requete):
        await asyncio.sleep(5)
        return httpx.Response(200, json={"codeEpci": "244400404"})

    # The EPCI lookup is cancelled once the commune answers.
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__startswith=partage.ATMO_WFS_URL).mock(
            return_value=httpx.Response(200, json=_collection(_ligne_atmo()))
        )
        mock.get(url__startswith="https://geo.api.gouv.fr/communes/").mock(
            side_effect=lent
        )
        lu = await partage.air_au_point(_amont(_contexte()))
    assert isinstance(lu, AirLu) and lu.indice is not None


@pytest.mark.parametrize(
    ("etat", "attendu"), [("out_of_coverage", "hors_couverture"),
                          ("temporary_error", "panne")]
)  # fmt: skip
async def test_the_station_is_read_whatever_the_stops(monkeypatch, etat, attendu):
    _reseaux(monkeypatch, [_champ(lieu.FIELD_ARRETS, etat=etat, rayon_m=1000)])
    _gare(monkeypatch, GareProche(nom="Nantes", distance_m=1712, complete=True))
    theme = await transports.lire(_contexte())
    assert theme.etat == attendu
    assert theme.details["gare"]["nom"] == "Nantes"
    assert theme.details["frequence_pointe"]["etat"] == "hors_couverture"


# --- Revue indépendante n° 1 ------------------------------------------------------


async def test_an_epci_code_that_is_not_a_siren_is_an_outage_never_a_filter():
    with respx.mock(assert_all_called=False) as mock:
        atmo = mock.get(url__startswith=partage.ATMO_WFS_URL).mock(
            return_value=httpx.Response(200, json=_collection())
        )
        mock.get(url__startswith="https://geo.api.gouv.fr/communes/").mock(
            return_value=httpx.Response(200, json={"codeEpci": "1' OR '1'='1"})
        )
        lu = await partage.air_au_point(_amont(_contexte()))
    assert isinstance(lu, PanneAmont)
    assert atmo.call_count == 1


async def test_the_index_is_kept_by_day_and_by_commune():
    from dataclasses import replace

    with respx.mock(assert_all_called=False) as mock:
        atmo = mock.get(url__startswith=partage.ATMO_WFS_URL).mock(
            side_effect=lambda requete: httpx.Response(
                200,
                json=_collection(
                    _ligne_atmo(
                        "85191" if "85191" in str(requete.url) else "44109",
                        date_ech=_filtre(requete)[-11:-1],
                    )
                ),
            )
        )
        amont = _amont(_contexte())
        lendemain = replace(amont, as_of=AS_OF.replace(day=31), memo=Memo())
        autre = _amont(_avec_commune(_contexte(), "85191"))
        lus = [await partage.air_au_point(c) for c in (amont, lendemain, autre, amont)]
    assert [lu.indice.date for lu in lus] == [JOUR, "2026-10-31", JOUR, JOUR]
    assert [lu.indice.zone_code for lu in lus] == ["44109", "44109", "85191", "44109"]
    assert atmo.call_count == 3


async def test_the_cell_is_kept_by_point():
    with respx.mock() as mock:
        wfs = mock.get(url__startswith=partage.WFS_URL).mock(
            side_effect=lambda requete: httpx.Response(
                200,
                json=_collection(_carreau_brut(id_inspire=_filtre(requete)[-15:-1])),
            )
        )
        contexte = _contexte(hors=("iris",))
        ailleurs = contexte.__class__(
            **{**contexte.__dict__, "point": PointBan(lon=-1.3, lat=46.3)}
        )
        ici = await partage.revenus_au_point(_amont(contexte))
        la = await partage.revenus_au_point(_amont(ailleurs))
    assert ici.carreau.carreau.id != la.carreau.carreau.id
    assert wfs.call_count == 2


async def test_contradictory_rows_of_the_commune_are_said_through_the_reader():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__startswith=partage.ATMO_WFS_URL).mock(
            return_value=httpx.Response(
                200,
                json=_collection(_ligne_atmo(code_qual=2), _ligne_atmo(code_qual=4)),
            )
        )
        lu = await partage.air_au_point(_amont(_contexte()))
    assert lu == AirLu(indice=None, motif="indices_contradictoires")


async def test_a_zone_that_was_not_asked_is_an_outage():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__startswith=partage.ATMO_WFS_URL).mock(
            return_value=httpx.Response(200, json=_collection(_ligne_atmo("85191")))
        )
        assert isinstance(await partage.air_au_point(_amont(_contexte())), PanneAmont)


def _wfs_iris(requete):
    if "FILOSOFI" in str(requete.url):
        return httpx.Response(200, json=_collection(_carreau_brut()))
    return httpx.Response(
        200,
        json=_collection({"code_iris": "441090507", "nom_iris": "V", "type_iris": "A"}),
    )


async def test_an_iris_unknown_to_melodi_is_a_gap_and_is_kept():
    with respx.mock() as mock:
        mock.get(url__startswith=partage.WFS_URL).mock(side_effect=_wfs_iris)
        melodi = mock.get(url__startswith=partage.MELODI_URL).mock(
            return_value=httpx.Response(
                400, json={"message": "Aucun territoire trouvé"}
            )
        )
        lu = await partage.revenus_au_point(_amont(_contexte()))
        await partage.revenus_au_point(_amont(_contexte()))
    assert lu.iris.motif_composition == "iris_hors_recensement"
    assert not lu.iris.panne_composition
    assert melodi.call_count == 1


async def test_the_iris_and_its_census_share_one_deadline(monkeypatch):
    import asyncio

    monkeypatch.setattr(partage, "DEADLINE_S", 0.5)

    async def iris_lent(couche, geometrie, lon, lat):
        await asyncio.sleep(0.3)
        return _collection({"code_iris": "441090507", "nom_iris": "V"})

    async def recensement_lent(code):
        await asyncio.sleep(0.3)
        return None

    monkeypatch.setattr(partage, "_wfs_au_point", iris_lent)
    monkeypatch.setattr(partage, "_composition_melodi", recensement_lent)
    lu = await partage.revenus_au_point(_amont(_contexte(hors=("filosofi_carreaux",))))
    # 0.3 s for the IRIS, then 0.3 s for the census: beyond the one deadline.
    assert lu.iris.iris.code == "441090507"
    assert lu.iris.panne_composition


# --- Revue Codex n° 2 -------------------------------------------------------------


async def test_the_cell_and_the_iris_are_read_at_the_exact_point():
    # Rounded to 5 decimals, this point falls in the neighbouring 200 m cell.
    exact = PointBan(lon=-1.543355, lat=47.217716)
    with respx.mock(assert_all_called=False) as mock:
        wfs = mock.get(url__startswith=partage.WFS_URL).mock(
            side_effect=lambda requete: httpx.Response(
                200,
                json=_collection(_carreau_brut())
                if "FILOSOFI" in str(requete.url)
                else _collection(),
            )
        )
        contexte = _contexte()
        ici = contexte.__class__(**{**contexte.__dict__, "point": exact})
        await partage.revenus_au_point(_amont(ici))
    filtres = sorted(_filtre(c.request) for c in wfs.calls)
    assert filtres == [
        "INTERSECTS(geom,SRID=4326;POINT(-1.543355 47.217716))",
        "INTERSECTS(geometrie,SRID=4326;POINT(-1.543355 47.217716))",
    ]


@pytest.mark.parametrize("etat", ["out_of_coverage", "temporary_error"])
async def test_a_station_outage_is_declared_whatever_the_stops(monkeypatch, etat):
    _reseaux(monkeypatch, [_champ(lieu.FIELD_ARRETS, etat=etat, rayon_m=1000)])
    _gare(monkeypatch, PanneAmont())
    theme = await transports.lire(_contexte())
    assert theme.details["gare"]["etat"] == "panne"
    assert "gares" in theme.pannes_partielles


async def test_an_absent_index_is_never_kept():
    # Code 0 is a gap of the day: the index may be computed later.
    with respx.mock(assert_all_called=False) as mock:
        atmo = mock.get(url__startswith=partage.ATMO_WFS_URL).mock(
            side_effect=[
                httpx.Response(200, json=_collection(_ligne_atmo(code_qual=0))),
                httpx.Response(200, json=_collection(_ligne_atmo(code_qual=2))),
            ]
        )
        premier = await partage.air_au_point(_amont(_contexte()))
        second = await partage.air_au_point(_amont(_contexte()))
    assert premier.indice.code == 0
    assert second.indice.code == 2
    assert atmo.call_count == 2


# --- Revue Codex n° 3 -------------------------------------------------------------


@pytest.mark.parametrize(
    "donnees",
    [
        _melodi(_obs("_T", 100.0)),  # a total without any category
        _melodi(_obs("_T", 100.0), _obs("3", 10.0)),  # categories missing
        _complete(pcs8=1.0),  # a category the census does not serve
    ],
)
def test_an_incomplete_make_up_is_an_outage(donnees):
    with pytest.raises(UpstreamUnavailableError):
        partage.composition_iris_de(donnees, GEO_IRIS)


def test_two_rows_of_one_category_are_an_outage():
    donnees = _complete(pcs3=10.0)
    donnees["observations"].append(_obs("3", 80.0))
    with pytest.raises(UpstreamUnavailableError):
        partage.composition_iris_de(donnees, GEO_IRIS)


def test_rows_of_another_iris_are_an_outage_never_kept():
    etrangere = _complete()
    for obs in etrangere["observations"]:
        obs["dimensions"]["GEO"] = "2025-IRIS-751120101"
    with pytest.raises(UpstreamUnavailableError):
        partage.composition_iris_de(etrangere, GEO_IRIS)
    melangee = _complete()
    melangee["observations"][0]["dimensions"]["GEO"] = "2025-IRIS-751120101"
    with pytest.raises(UpstreamUnavailableError):
        partage.composition_iris_de(melangee, GEO_IRIS)


async def test_a_foreign_census_answer_keeps_the_iris_named_and_is_never_kept():
    etrangere = _complete()
    for obs in etrangere["observations"]:
        obs["dimensions"]["GEO"] = "2025-IRIS-751120101"
    with respx.mock() as mock:
        mock.get(url__startswith=partage.WFS_URL).mock(side_effect=_wfs_iris)
        melodi = mock.get(url__startswith=partage.MELODI_URL).mock(
            return_value=httpx.Response(200, json=etrangere)
        )
        lu = await partage.revenus_au_point(_amont(_contexte()))
        await partage.revenus_au_point(_amont(_contexte()))
    assert lu.iris.iris.code == "441090507"
    assert lu.iris.composition is None and lu.iris.panne_composition
    assert melodi.call_count == 2


async def test_an_absent_index_of_an_epci_says_the_epci():
    theme = await _air(
        AirLu(indice=_indice(code=0, zone_type="epci", zone_code="244400404"))
    )
    assert theme.etat == "manque"
    assert theme.granularite.niveau == "epci"
