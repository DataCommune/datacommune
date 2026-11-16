"""Themes of the place on the address page, without a database (04-06).

« Bâtiment et parcelle » counts the buildings of the parcel that contains the
BAN point and serves the characteristics of the building of the point only
when one geometry covers it (LIEU-02, R06-09/2-3); a point outside every
parcel or on several parcels attributes nothing (N-02); an outage of one
source stays in its value (N-01, N-14).
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from fastapi import HTTPException
from sqlalchemy.exc import ProgrammingError

from app.adresse import blocs, lieu, risques
from app.adresse.blocs import source_valeur
from app.adresse.relations import BatimentsParcelle, PointBan
from app.adresse.schemas import (
    CommuneRef,
    DepartementRef,
    IdentiteAdresse,
    RelationParcelle,
    ValeurLieu,
)
from app.common.exceptions import UpstreamUnavailableError
from app.common.proxy_coverage import ProxySource
from app.common.schemas import DatasetHosting, OutOfCoverageReason
from app.connectivite import queries as connectivite_queries
from app.datasets.provenance import ProvenanceFacts
from app.scope.dependencies import RouteHosting

pytestmark = pytest.mark.unit


async def _acted(_session, _tables):
    return True


@pytest.fixture(autouse=True)
def _publication_acted(monkeypatch):
    """The fake sessions answer the block reads only: the publication guard of
    ``rnb_liens`` is proven on PostgreSQL
    (``tests/integration/test_rnb_liens_elargissement.py``)."""
    from app.adresse import blocs

    monkeypatch.setattr(blocs, "publication_actee", _acted)


BAN_ID = "85191_0420_00012"
PARCELLE = "85191000AB0012"
POINT = PointBan(lon=-1.43, lat=46.67)
HEBERGE = DatasetHosting(etat="heberge", departements=["85"])
NON_HEBERGE = DatasetHosting(
    etat="hors_couverture", motif=OutOfCoverageReason.DATASET_NON_HEBERGE
)
CLES = (
    "ban",
    "cadastre",
    "dpe",
    "dvf",
    "georisques",
    "commune",
    "batiments",
    "rnb",
    "copropriete",
    "urbanisme",
    "connectivite",
    "transports",
    "gares",
)
SQL_EN_PANNE = ProgrammingError("SELECT", {}, Exception("relation absente"))


def _hosting(**out: DatasetHosting) -> RouteHosting:
    return RouteHosting({key: out.get(key, HEBERGE) for key in CLES})


def _identite(parcelle: RelationParcelle | None) -> IdentiteAdresse:
    return IdentiteAdresse(
        ban_id=BAN_ID,
        libelle="12 Rue des Lilas, 85000 La Roche-sur-Yon",
        numero="12",
        nom_voie="Rue des Lilas",
        code_postal="85000",
        commune=CommuneRef(code_insee="85191", nom="La Roche-sur-Yon"),
        departement=DepartementRef(code="85", nom="Vendée"),
        parcelle=parcelle,
    )


UNIQUE = RelationParcelle(
    etat="unique",
    id_parcelle=PARCELLE,
    nombre_candidats=1,
    rattachement="parcelle_du_point",
)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _Session:
    """Fake session: savepoints recorded; ``execute`` answers the RNB read."""

    def __init__(self, rnb: list[str] | Exception = ()) -> None:
        self.savepoints = 0
        self.rnb = rnb
        self.executed = 0

    @asynccontextmanager
    async def begin_nested(self):
        self.savepoints += 1
        yield

    async def execute(self, statement, params=None):
        self.executed += 1
        if isinstance(self.rnb, Exception):
            raise self.rnb
        return _Result([(identifiant,) for identifiant in self.rnb])


@pytest.fixture
def lecteurs(monkeypatch):
    """Readers of the theme, served by default for a parcel of two buildings."""

    async def no_guard(session, datasets):
        return None

    monkeypatch.setattr(blocs, "guard_publication", no_guard)
    doubles = {
        "batiments_sur_parcelle": AsyncMock(
            return_value=BatimentsParcelle(total=2, hors_annexe=2)
        ),
        "batiments_par_point": AsyncMock(return_value=[]),
        "parcelle_par_reference": AsyncMock(
            return_value=[{"id_parcelle": PARCELLE, "contenance": 412}]
        ),
        "copropriete_par_parcelle": AsyncMock(return_value=[]),
        # No millésime nor import known by default: no period (C14).
        "faits_de_provenance": AsyncMock(return_value=None),
    }
    for nom, double in doubles.items():
        monkeypatch.setattr(lieu, nom, double)
    return doubles


async def _theme(session=None, parcelle=UNIQUE, point=POINT, **hosting):
    contexte = lieu.Contexte(
        identite=_identite(parcelle), point=point, hosting=_hosting(**hosting)
    )
    return await lieu.theme_batiment_parcelle(session or _Session(), contexte)


def _champs(theme) -> dict[str, dict]:
    return {
        champ.field_id: champ.model_dump()
        for groupe in theme.groupes
        for champ in groupe.champs
    }


async def test_tracer_two_buildings_on_the_parcel_without_a_unique_building_at_point(
    lecteurs,
):
    theme = await _theme()

    assert (theme.cle, theme.titre, theme.etat) == (
        "batiment_parcelle",
        "Bâtiment et parcelle",
        "value",
    )
    champs = _champs(theme)
    assert champs[lieu.FIELD_BATIMENTS]["valeur"] == 2
    assert champs[lieu.FIELD_BATIMENTS]["rattachement"] == "parcelle_du_point"
    assert champs[lieu.FIELD_BATIMENTS_HORS_ANNEXE]["valeur"] == 2
    # No building contains the point alone: no characteristic is served; the
    # height says why, typed by its cause (D-15 API CR-01: zero, not several).
    assert lieu.FIELD_ETAGES not in champs
    hauteur = champs[lieu.FIELD_HAUTEUR]
    assert (hauteur["etat"], hauteur["motif"], hauteur["valeur"]) == (
        "not_available",
        "aucun_batiment_au_point",
        None,
    )
    assert hauteur["rattachement"] == "batiment_du_point"
    assert champs[lieu.FIELD_PARCELLE]["valeur"] == PARCELLE
    assert champs[lieu.FIELD_CONTENANCE]["valeur"] == 412
    assert champs[lieu.FIELD_COPROPRIETE]["valeur"] == 0
    lecteurs["batiments_sur_parcelle"].assert_awaited_once()
    # Nothing of a dwelling or a lot is ever served.
    texte = theme.model_dump_json()
    assert "logement" not in texte and "lot" not in texte


async def test_one_building_covering_the_point_serves_its_characteristics(lecteurs):
    lecteurs["batiments_sur_parcelle"].return_value = BatimentsParcelle(1, 1)
    lecteurs["batiments_par_point"].return_value = [
        {"cleabs": "BATI1", "hauteur": 7.5, "nombre_d_etages": 2}
    ]

    champs = _champs(await _theme())

    assert champs[lieu.FIELD_BATIMENTS]["valeur"] == 1
    assert champs[lieu.FIELD_HAUTEUR]["valeur"] == 7.5
    assert champs[lieu.FIELD_HAUTEUR]["rattachement"] == "batiment_du_point"
    assert champs[lieu.FIELD_ETAGES]["valeur"] == 2
    # The building of the point is read on the point itself (0 m), at most two.
    args = lecteurs["batiments_par_point"].await_args
    assert args.args[1:] == (POINT.lat, POINT.lon, 0.0)
    assert args.kwargs == {"limit": 2}


async def test_six_buildings_with_annexes_are_counted_apart_from_the_point(lecteurs):
    lecteurs["batiments_sur_parcelle"].return_value = BatimentsParcelle(6, 4)
    lecteurs["batiments_par_point"].return_value = [
        {"cleabs": "A", "hauteur": 6.0, "nombre_d_etages": 1},
        {"cleabs": "B", "hauteur": 3.0, "nombre_d_etages": 1},
    ]

    champs = _champs(await _theme())

    assert champs[lieu.FIELD_BATIMENTS]["valeur"] == 6
    assert champs[lieu.FIELD_BATIMENTS_HORS_ANNEXE]["valeur"] == 4
    # Several buildings at the point: no height value, the reason says so.
    assert lieu.FIELD_ETAGES not in champs
    hauteur = champs[lieu.FIELD_HAUTEUR]
    assert (hauteur["etat"], hauteur["motif"], hauteur["valeur"]) == (
        "not_available",
        "batiment_du_point_non_unique",
        None,
    )


@pytest.mark.parametrize(
    ("parcelle", "etat", "motif"),
    [
        (
            RelationParcelle(etat="point_hors_parcelle", motif="point_hors_parcelle"),
            "not_available",
            "point_hors_parcelle",
        ),
        (
            RelationParcelle(
                etat="ambigue", nombre_candidats=2, motif="parcelles_multiples"
            ),
            "not_available",
            "parcelles_multiples",
        ),
        (
            RelationParcelle(etat="indeterminee", motif="positions_contradictoires"),
            "not_available",
            "parcelle_indeterminee",
        ),
        (None, "not_available", "parcelle_indeterminee"),
        (
            RelationParcelle(etat="hors_couverture", motif="dataset_non_heberge"),
            "out_of_coverage",
            "dataset_non_heberge",
        ),
        (
            RelationParcelle(etat="indisponible"),
            "temporary_error",
            "lecture_indisponible",
        ),
    ],
)
async def test_without_a_unique_parcel_no_building_is_attributed(
    lecteurs, parcelle, etat, motif
):
    theme = await _theme(session=_Session(rnb=["RNB1ABC234DE"]), parcelle=parcelle)

    # The theme keeps the state and reason of the parcel relation (N-02) ...
    assert (theme.etat, theme.motif) == (etat, motif)
    # ... and serves only the RNB field, linked to the BAN key, never to the
    # parcel: no building, parcel nor condominium value (C55, fix 1).
    assert list(_champs(theme)) == [lieu.FIELD_RNB]
    assert _champs(theme)[lieu.FIELD_RNB]["valeur"] == "RNB1ABC234DE"
    for nom, double in lecteurs.items():
        if nom != "faits_de_provenance":
            double.assert_not_called()
    # The only period read is the one of the RNB links export.
    lus = {appel.args[1] for appel in lecteurs["faits_de_provenance"].await_args_list}
    assert lus == {"rnb_liens"}


@pytest.mark.parametrize(
    ("parcelle", "rnb", "attendu"),
    [
        (
            RelationParcelle(etat="point_hors_parcelle", motif="point_hors_parcelle"),
            ["RNB1", "RNB2"],
            ("not_available", None, "batiment_rnb_non_unique"),
        ),
        (
            RelationParcelle(
                etat="ambigue", nombre_candidats=2, motif="parcelles_multiples"
            ),
            [],
            ("not_reported", None, None),
        ),
        (
            RelationParcelle(etat="hors_couverture", motif="dataset_non_heberge"),
            ["RNB1"],
            ("value", "RNB1", None),
        ),
        (
            RelationParcelle(etat="indisponible"),
            SQL_EN_PANNE,
            ("temporary_error", None, "lecture_indisponible"),
        ),
    ],
    ids=["hors_parcelle_deux_liens", "ambigue_sans_lien", "cadastre_hors", "panne"],
)
async def test_the_rnb_state_is_served_by_ban_key_without_a_unique_parcel(
    lecteurs, parcelle, rnb, attendu
):
    theme = await _theme(session=_Session(rnb=rnb), parcelle=parcelle)

    champ = _champs(theme)[lieu.FIELD_RNB]
    assert (champ["etat"], champ["valeur"], champ["motif"]) == attendu
    assert champ["rattachement"] == "batiment_rnb_de_l_adresse"
    assert theme.etat != "value"
    assert "RNB2" not in str(theme.model_dump())


async def test_rnb_not_hosted_without_a_unique_parcel_is_out_of_coverage_unread(
    lecteurs,
):
    session = _Session(rnb=["RNB1"])

    theme = await _theme(
        session=session,
        parcelle=RelationParcelle(
            etat="point_hors_parcelle", motif="point_hors_parcelle"
        ),
        rnb=NON_HEBERGE,
    )

    champ = _champs(theme)[lieu.FIELD_RNB]
    assert (champ["etat"], champ["motif"]) == ("out_of_coverage", "dataset_non_heberge")
    assert session.executed == 0


async def test_a_sql_outage_of_the_buildings_stays_in_their_value(lecteurs):
    lecteurs["batiments_sur_parcelle"].side_effect = SQL_EN_PANNE

    theme = await _theme()

    champs = _champs(theme)
    assert theme.etat == "value"
    assert champs[lieu.FIELD_BATIMENTS]["etat"] == "temporary_error"
    assert champs[lieu.FIELD_BATIMENTS]["motif"] == "lecture_indisponible"
    assert champs[lieu.FIELD_CONTENANCE]["valeur"] == 412
    assert champs[lieu.FIELD_COPROPRIETE]["etat"] == "value"


async def test_buildings_or_condominium_not_hosted_are_out_of_coverage_without_read(
    lecteurs,
):
    champs = _champs(await _theme(batiments=NON_HEBERGE, copropriete=NON_HEBERGE))

    assert champs[lieu.FIELD_BATIMENTS]["etat"] == "out_of_coverage"
    assert champs[lieu.FIELD_BATIMENTS]["motif"] == "dataset_non_heberge"
    assert champs[lieu.FIELD_COPROPRIETE]["etat"] == "out_of_coverage"
    lecteurs["batiments_sur_parcelle"].assert_not_called()
    lecteurs["batiments_par_point"].assert_not_called()
    lecteurs["copropriete_par_parcelle"].assert_not_called()


async def test_a_registered_condominium_is_counted_never_turned_into_lots(lecteurs):
    lecteurs["copropriete_par_parcelle"].return_value = [
        {"numero_immatriculation": "AA0000001", "nb_lots_habitation_strict": 12}
    ]

    theme = await _theme()

    champ = _champs(theme)[lieu.FIELD_COPROPRIETE]
    assert (champ["valeur"], champ["liste_incomplete"]) == (1, False)
    assert "AA0000001" not in theme.model_dump_json()
    # One more than the bound is read: reaching the bound is not incomplete.
    assert lecteurs["copropriete_par_parcelle"].await_args.args[1:] == (
        PARCELLE,
        lieu.COPROPRIETES_MAX + 1,
    )


async def test_the_rnb_identifier_is_served_when_the_rnb_links_exactly_one(lecteurs):
    champs = _champs(await _theme(session=_Session(rnb=["RNB1ABC234DE"])))

    rnb = champs[lieu.FIELD_RNB]
    assert (rnb["etat"], rnb["valeur"], rnb["motif"]) == ("value", "RNB1ABC234DE", None)
    assert rnb["rattachement"] == "batiment_rnb_de_l_adresse"


async def test_no_rnb_link_is_not_reported_never_omitted_nor_zero(lecteurs):
    # C52 (B3): the field is served; no link for the key is « not reported »,
    # with the period of the export that was read.
    lecteurs["faits_de_provenance"].side_effect = _faits_par_jeu(FAITS_DU_LIEU)

    champs = _champs(await _theme(session=_Session(rnb=[])))

    rnb = champs[lieu.FIELD_RNB]
    assert (rnb["etat"], rnb["valeur"], rnb["motif"]) == ("not_reported", None, None)
    assert rnb["periode"] == "Export RNB 2026-09"
    assert rnb["rattachement"] == "batiment_rnb_de_l_adresse"


async def test_several_rnb_links_are_not_published_and_none_is_chosen(lecteurs):
    lecteurs["faits_de_provenance"].side_effect = _faits_par_jeu(FAITS_DU_LIEU)

    theme = await _theme(session=_Session(rnb=["RNB1", "RNB2"]))

    rnb = _champs(theme)[lieu.FIELD_RNB]
    assert (rnb["etat"], rnb["valeur"], rnb["motif"]) == (
        "not_available",
        None,
        "batiment_rnb_non_unique",
    )
    assert rnb["periode"] == "Export RNB 2026-09"
    # No identifier of the candidates is exposed.
    texte = theme.model_dump_json()
    assert "RNB1" not in texte and "RNB2" not in texte


async def test_rnb_not_hosted_is_out_of_coverage_without_read(lecteurs):
    session = _Session(rnb=["RNB1"])

    champs = _champs(await _theme(session=session, rnb=NON_HEBERGE))

    rnb = champs[lieu.FIELD_RNB]
    assert (rnb["etat"], rnb["motif"], rnb["valeur"]) == (
        "out_of_coverage",
        "dataset_non_heberge",
        None,
    )
    assert rnb["periode"] is None
    assert session.executed == 0


async def test_an_rnb_read_failure_is_visible(lecteurs):
    champs = _champs(await _theme(session=_Session(rnb=SQL_EN_PANNE)))

    rnb = champs[lieu.FIELD_RNB]
    assert (rnb["etat"], rnb["motif"]) == ("temporary_error", "lecture_indisponible")


# --- Periods of the place (C14, SEM-02) ---------------------------------------------

# What the imports know of each dataset read by « Bâtiment et parcelle »: a
# millésime declared by the import, or only the date of its last success.
FAITS_DU_LIEU = {
    "batiments": ProvenanceFacts(source_period_label="BD TOPO 2026-06"),
    "rnb_liens": ProvenanceFacts(
        source_period_label="Export RNB 2026-09",
        last_success_at=datetime(2026, 9, 24, 22, 47, tzinfo=UTC),
    ),
    "cadastre": ProvenanceFacts(
        last_success_at=datetime(2026, 9, 25, 21, 54, tzinfo=UTC)
    ),
    "copropriete": ProvenanceFacts(
        last_success_at=datetime(2026, 9, 24, 23, 29, tzinfo=UTC)
    ),
}


def _faits_par_jeu(faits: dict[str, ProvenanceFacts]):
    async def lire(session, cle):
        return faits.get(cle)

    return lire


async def test_every_value_of_the_place_carries_the_millesime_or_import_of_its_source(
    lecteurs,
):
    lecteurs["faits_de_provenance"].side_effect = _faits_par_jeu(FAITS_DU_LIEU)
    lecteurs["batiments_par_point"].return_value = [
        {"cleabs": "BATI1", "hauteur": 7.5, "nombre_d_etages": 2}
    ]

    champs = _champs(await _theme(session=_Session(rnb=["RNB1ABC234DE"])))

    assert {cle: champ["periode"] for cle, champ in champs.items()} == {
        lieu.FIELD_BATIMENTS: "BD TOPO 2026-06",
        lieu.FIELD_BATIMENTS_HORS_ANNEXE: "BD TOPO 2026-06",
        lieu.FIELD_HAUTEUR: "BD TOPO 2026-06",
        lieu.FIELD_ETAGES: "BD TOPO 2026-06",
        # The RNB identifier is read in `rnb_liens`: the millésime of that export.
        lieu.FIELD_RNB: "Export RNB 2026-09",
        lieu.FIELD_PARCELLE: "2026-09-25",
        lieu.FIELD_CONTENANCE: "2026-09-25",
        lieu.FIELD_COPROPRIETE: "2026-09-25",
    }
    lus = {appel.args[1] for appel in lecteurs["faits_de_provenance"].await_args_list}
    assert lus == {"batiments", "rnb_liens", "cadastre", "copropriete"}


async def test_a_source_without_millesime_nor_import_serves_no_period(lecteurs):
    champs = _champs(await _theme(session=_Session(rnb=["RNB1ABC234DE"])))

    assert {champ["periode"] for champ in champs.values()} == {None}
    assert champs[lieu.FIELD_BATIMENTS]["valeur"] == 2


async def test_an_outage_or_a_source_not_hosted_carries_no_period(lecteurs):
    lecteurs["faits_de_provenance"].side_effect = _faits_par_jeu(FAITS_DU_LIEU)
    lecteurs["batiments_sur_parcelle"].side_effect = SQL_EN_PANNE

    champs = _champs(await _theme(copropriete=NON_HEBERGE))

    assert champs[lieu.FIELD_BATIMENTS]["etat"] == "temporary_error"
    assert champs[lieu.FIELD_BATIMENTS]["periode"] is None
    assert champs[lieu.FIELD_COPROPRIETE]["etat"] == "out_of_coverage"
    assert champs[lieu.FIELD_COPROPRIETE]["periode"] is None
    assert champs[lieu.FIELD_CONTENANCE]["periode"] == "2026-09-25"


# --- Task 2: risks, planning, networks and transports -------------------------------

AS_OF = date(2026, 10, 5)
ARGILES = ValeurLieu(
    field_id=risques.FIELD_ID_ARGILES,
    etat="value",
    valeur="Exposition moyenne",
    unite="none",
    periode=AS_OF.isoformat(),
    source=source_valeur("georisques"),
    rattachement="au_point",
)


def _contexte(parcelle=UNIQUE, point=POINT, identite=None, **hosting):
    return lieu.Contexte(
        identite=identite or _identite(parcelle),
        point=point,
        hosting=_hosting(**hosting),
    )


def _couverture(complet=True, total=None):
    return ProxySource(
        statut="disponible" if complet else "partiel",
        nombre_retournes=1,
        total=total,
        complet=complet,
        pages_lues=1,
    )


def _commune(gaspar=None, radon=None, icpe=None, **couvertures):
    lignes = {
        "gaspar": [
            {
                "risques_detail": [
                    {"libelle_risque_long": "Inondation"},
                    {"libelle_risque_long": "Séisme"},
                ]
            }
        ]
        if gaspar is None
        else gaspar,
        "radon": [{"classe_potentiel": "3"}] if radon is None else radon,
        "icpe": [{"nom": "Usine"}] * 4 if icpe is None else icpe,
    }
    couverture = {
        cle: couvertures.get(cle, _couverture(total=len(lignes[cle] or [])))
        for cle in lignes
    }
    total = {cle: couverture[cle].total for cle in lignes}
    return risques.RisquesCommune(lignes=lignes, couverture=couverture, total=total)


def _par_champ(theme) -> dict[str, dict]:
    return {
        champ.field_id: champ.model_dump()
        for groupe in theme.groupes
        for champ in groupe.champs
    }


def test_risks_at_the_point_and_of_the_commune_never_merge():
    theme = lieu.theme_risques(
        _contexte(), ARGILES, lieu.AmontLieu(risques_commune=_commune()), AS_OF
    )

    assert theme.etat == "value"
    au_point, commune = theme.groupes
    assert (au_point.cle, au_point.titre) == ("au_point", "Au point de l'adresse")
    assert (commune.cle, commune.titre) == ("commune", "Sur la commune (référence)")
    # Only what is measured at the point is in the first group: clay, the
    # very value of landmark ③.
    assert [champ.field_id for champ in au_point.champs] == [risques.FIELD_ID_ARGILES]
    assert au_point.champs[0].valeur == "Exposition moyenne"
    assert {champ.rattachement for champ in commune.champs} == {"commune_reference"}
    assert [champ.field_id for champ in commune.champs] == [
        risques.FIELD_ID_GASPAR,
        risques.FIELD_ID_RADON,
        risques.FIELD_ID_ICPE,
    ]
    gaspar, radon, icpe = commune.champs
    assert [element.libelle for element in gaspar.elements] == ["Inondation", "Séisme"]
    assert (radon.valeur, icpe.valeur) == ("3", 4)
    # No merged count of risks anywhere.
    assert not set(_par_champ(theme)) & {"adresse.risques.total", "adresse.risques"}


def test_clay_without_exposure_and_gaspar_without_risk_are_never_a_zero():
    argiles = ARGILES.model_copy(update={"etat": "not_reported", "valeur": None})

    theme = lieu.theme_risques(
        _contexte(),
        argiles,
        lieu.AmontLieu(risques_commune=_commune(gaspar=[])),
        AS_OF,
    )

    champs = _par_champ(theme)
    assert champs[risques.FIELD_ID_ARGILES]["etat"] == "not_reported"
    assert champs[risques.FIELD_ID_GASPAR]["etat"] == "not_reported"
    assert champs[risques.FIELD_ID_GASPAR]["valeur"] is None
    assert 0 not in [champ["valeur"] for champ in champs.values()][:2]


def test_a_communal_outage_stays_in_its_group():
    theme = lieu.theme_risques(
        _contexte(), ARGILES, lieu.AmontLieu(risques_commune=lieu.PanneAmont()), AS_OF
    )

    au_point, commune = theme.groupes
    assert theme.etat == "value"
    assert au_point.champs[0].etat == "value"
    assert {champ.etat for champ in commune.champs} == {"temporary_error"}


def test_every_risk_down_is_an_outage_of_the_theme_and_not_hosted_is_out():
    argiles = ARGILES.model_copy(
        update={
            "etat": "temporary_error",
            "valeur": None,
            "motif": "amont_indisponible",
        }
    )
    panne = lieu.theme_risques(
        _contexte(), argiles, lieu.AmontLieu(risques_commune=lieu.PanneAmont()), AS_OF
    )
    hors = lieu.theme_risques(
        _contexte(georisques=NON_HEBERGE), ARGILES, lieu.AmontLieu(), AS_OF
    )

    assert (panne.etat, panne.groupes) == ("temporary_error", [])
    assert (hors.etat, hors.motif, hors.groupes) == (
        "out_of_coverage",
        "dataset_non_heberge",
        [],
    )


def test_a_partial_installation_list_without_total_is_not_counted():
    lu = _commune(icpe=[{"nom": "Usine"}] * 2)
    lu.couverture["icpe"] = _couverture(complet=None)
    lu.total["icpe"] = None

    theme = lieu.theme_risques(
        _contexte(), ARGILES, lieu.AmontLieu(risques_commune=lu), AS_OF
    )

    icpe = _par_champ(theme)[risques.FIELD_ID_ICPE]
    assert (icpe["etat"], icpe["valeur"], icpe["liste_incomplete"]) == (
        "not_reported",
        None,
        True,
    )
    assert theme.liste_incomplete is True


GEO = "https://georisques.gouv.fr/api/v1"


def _json(body, status=200):
    return httpx.Response(status, json=body)


async def test_the_communal_risks_are_read_by_insee_code_only():
    with respx.mock(assert_all_called=True) as mock:
        gaspar = mock.get(f"{GEO}/gaspar/risques").mock(
            return_value=_json(
                {
                    "results": 1,
                    "data": [
                        {"risques_detail": [{"libelle_risque_long": "Inondation"}]}
                    ],
                }
            )
        )
        mock.get(f"{GEO}/radon").mock(
            return_value=_json({"results": 1, "data": [{"classe_potentiel": "2"}]})
        )
        mock.get(f"{GEO}/installations_classees").mock(
            return_value=_json({"results": 7, "data": [{"nom": "A"}] * 7})
        )
        lu = await risques.lire_risques_commune("85191")

    params = gaspar.calls.last.request.url.params
    assert params["code_insee"] == "85191" and "latlon" not in params
    assert lu.total == {"gaspar": 1, "radon": 1, "icpe": 7}
    assert lu.couverture["icpe"].complet is True


async def test_one_communal_sub_source_down_and_all_down():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{GEO}/gaspar/risques").mock(return_value=_json({}, 503))
        mock.get(f"{GEO}/radon").mock(
            return_value=_json({"results": 1, "data": [{"classe_potentiel": "1"}]})
        )
        mock.get(f"{GEO}/installations_classees").mock(
            side_effect=httpx.ConnectError("down")
        )
        lu = await risques.lire_risques_commune("85191")
    assert lu.lignes["gaspar"] is None and lu.lignes["icpe"] is None
    assert lu.couverture["gaspar"].statut == "indisponible"

    with respx.mock(assert_all_called=False) as mock:
        for chemin in ("/gaspar/risques", "/radon", "/installations_classees"):
            mock.get(f"{GEO}{chemin}").mock(return_value=_json({}, 500))
        with pytest.raises(UpstreamUnavailableError):
            await risques.lire_risques_commune("85191")


async def test_the_communal_read_is_bounded_by_its_deadline(monkeypatch):
    async def lent(_request):
        await asyncio.sleep(1)
        return _json({"results": 0, "data": []})

    monkeypatch.setattr(risques, "RGA_DEADLINE_S", 0.05)
    with respx.mock(assert_all_called=False) as mock:
        for chemin in ("/gaspar/risques", "/radon", "/installations_classees"):
            mock.get(f"{GEO}{chemin}").mock(side_effect=lent)
        with pytest.raises(UpstreamUnavailableError):
            await risques.lire_risques_commune("85191")


async def test_upstreams_are_called_only_where_hosted_and_their_outage_is_kept(
    monkeypatch,
):
    commune = AsyncMock(return_value=_commune())
    urbanisme = AsyncMock(return_value={"zones_plu": [], "sources": {}})
    monkeypatch.setattr(risques, "lire_risques_commune", commune)
    monkeypatch.setattr(lieu, "query_urbanisme", urbanisme)

    rien = await lieu.lire_amont_lieu(
        POINT, "85191", _hosting(georisques=NON_HEBERGE, urbanisme=NON_HEBERGE)
    )
    sans_point = await lieu.lire_amont_lieu(None, "85191", _hosting())
    assert (rien.risques_commune, rien.urbanisme) == (None, None)
    assert sans_point.urbanisme is None
    assert commune.await_count == 1 and urbanisme.await_count == 0

    commune.side_effect = UpstreamUnavailableError("Georisques commune")
    urbanisme.side_effect = TimeoutError()
    panne = await lieu.lire_amont_lieu(POINT, "85191", _hosting())
    assert panne.risques_commune == lieu.PanneAmont()
    assert panne.urbanisme == lieu.PanneAmont()


def _gpu(zones, prescriptions, zones_complet=True, prescriptions_complet=True):
    def source(rows, complet):
        if rows is None:
            return {"statut": "indisponible"}
        return {"statut": "disponible" if complet else "partiel", "complet": complet}

    return {
        "zones_plu": zones,
        "prescriptions": prescriptions,
        "sources": {
            "zones_plu": source(zones, zones_complet),
            "prescriptions": source(prescriptions, prescriptions_complet),
        },
    }


def _feature(**proprietes):
    return {"type": "Feature", "properties": proprietes}


def test_planning_zone_and_prescriptions_at_the_point():
    lu = _gpu(
        [_feature(libelle="UB", libelong="Zone urbaine mixte", typezone="U")],
        [_feature(libelle="Espace boisé classé")],
        prescriptions_complet=None,
    )

    theme = lieu.theme_urbanisme(_contexte(), lieu.AmontLieu(urbanisme=lu), AS_OF)

    zone, prescriptions = theme.groupes[0].champs
    assert [e.libelle for e in zone.elements] == ["UB · Zone urbaine mixte"]
    assert zone.rattachement == "au_point"
    assert (prescriptions.valeur, prescriptions.liste_incomplete) == (1, True)
    assert theme.liste_incomplete is True


def test_no_planning_document_published_is_a_typed_gap_never_zero():
    theme = lieu.theme_urbanisme(
        _contexte(), lieu.AmontLieu(urbanisme=_gpu([], [])), AS_OF
    )

    assert {(c.etat, c.motif) for c in theme.groupes[0].champs} == {
        ("not_available", "aucun_document_publie")
    }
    assert all(champ.valeur is None for champ in theme.groupes[0].champs)


def test_planning_states_outage_coverage_and_point():
    une_panne = lieu.theme_urbanisme(
        _contexte(),
        lieu.AmontLieu(urbanisme=_gpu([_feature(libelle="A")], None)),
        AS_OF,
    )
    assert [c.etat for c in une_panne.groupes[0].champs] == ["value", "temporary_error"]
    panne = lieu.theme_urbanisme(
        _contexte(), lieu.AmontLieu(urbanisme=lieu.PanneAmont()), AS_OF
    )
    assert (panne.etat, panne.groupes) == ("temporary_error", [])
    hors = lieu.theme_urbanisme(
        _contexte(urbanisme=NON_HEBERGE), lieu.AmontLieu(), AS_OF
    )
    assert hors.etat == "out_of_coverage"
    sans_point = lieu.theme_urbanisme(_contexte(point=None), lieu.AmontLieu(), AS_OF)
    assert (sans_point.etat, sans_point.motif) == (
        "not_available",
        "position_indeterminee",
    )


class _Lignes:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(type("Row", (), {"_mapping": row})() for row in self._rows)


class _SessionConnectivite:
    def __init__(self, rows):
        self.rows = rows
        self.params = None

    async def execute(self, statement, params=None):
        self.params = params
        return _Lignes(self.rows)


def _ligne(
    voie="Rue Victor Hugo", numero="12", complement=None, imb="IMB1", techno="FO"
):
    return {
        "id_imb": imb,
        "voie": voie,
        "numero": numero,
        "complement": complement,
        "technologie": techno,
        "debit_max": "THD1G",
    }


async def _exacte(rows, rep=None, nom_voie="rue  Victor HUGO"):
    session = _SessionConnectivite(rows)
    lu = await connectivite_queries.eligibilite_adresse_exacte(
        session, "85191", numero="12", rep=rep, nom_voie=nom_voie
    )
    return lu, session


async def test_connectivity_identity_is_verified_exactly():
    lu, session = await _exacte([_ligne(), _ligne(techno="CU")])
    assert lu.etat == "unique" and len(lu.lignes) == 2
    assert session.params["numero"] == "12"

    # « prolongée » shares the prefix, not the street.
    lu, _ = await _exacte([_ligne(voie="Rue Victor Hugo Prolongée")])
    assert (lu.etat, lu.lignes) == ("indeterminee", ())
    # 12 bis is not 12, and 12 is not 12 bis.
    lu, _ = await _exacte([_ligne(complement="bis")])
    assert lu.etat == "indeterminee"
    lu, _ = await _exacte([_ligne()], rep="bis")
    assert lu.etat == "indeterminee"
    # Two buildings at the same address: no eligibility is chosen.
    lu, _ = await _exacte([_ligne(imb="IMB1"), _ligne(imb="IMB2")])
    assert lu.etat == "indeterminee"
    lu, _ = await _exacte([])
    assert lu.etat == "aucune"
    lu, _ = await _exacte([_ligne()] * connectivite_queries.CANDIDATS_MAX)
    assert lu.etat == "indeterminee"


@pytest.fixture
def reseaux(monkeypatch):
    async def no_guard(session, datasets):
        return None

    monkeypatch.setattr(blocs, "guard_publication", no_guard)
    doubles = {
        "eligibilite_adresse_exacte": AsyncMock(
            return_value=connectivite_queries.EligibiliteAdresse(
                etat="unique", lignes=(_ligne(), _ligne(techno="CU"))
            )
        ),
        "transports_a_proximite": AsyncMock(
            return_value=[
                {
                    "stop_name": "Place Napoléon",
                    "type_transport": "arret",
                    "distance_m": 118.6,
                    "latitude": 46.67,
                    "longitude": -1.43,
                },
                {
                    "stop_name": "Gare SNCF",
                    "type_transport": "gare",
                    "distance_m": 840.2,
                    "latitude": 46.66,
                    "longitude": -1.42,
                },
            ]
        ),
        "faits_de_provenance": AsyncMock(return_value=None),
        # The 1 km disc stays within the hosted departements by default (C16).
        "rayon_deborde": AsyncMock(return_value=False),
    }
    for nom, double in doubles.items():
        monkeypatch.setattr(lieu, nom, double)
    return doubles


async def test_networks_fibre_published_for_this_address_and_nearest_stops(reseaux):
    theme = await lieu.theme_reseaux_transports(_Session(), _contexte())

    champs = _par_champ(theme)
    assert champs[lieu.FIELD_FIBRE]["valeur"] is True
    assert champs[lieu.FIELD_FIBRE]["rattachement"] == "eligibilite_adresse"
    assert champs[lieu.FIELD_DEBIT]["valeur"] == "THD1G"
    arrets = champs[lieu.FIELD_ARRETS]
    assert (arrets["valeur"], arrets["rayon_m"], arrets["rattachement"]) == (
        2,
        1000,
        "rayon_du_point",
    )
    assert [
        (e["libelle"], e["distance_m"], e["nature"]) for e in arrets["elements"]
    ] == [
        ("Place Napoléon", 119, "arret"),
        ("Gare SNCF", 840, "gare"),
    ]
    # No coordinate of a stop leaves the API.
    assert "46.6" not in theme.model_dump_json()
    args = reseaux["eligibilite_adresse_exacte"].await_args
    assert args.kwargs == {"numero": "12", "rep": None, "nom_voie": "Rue des Lilas"}


@pytest.mark.parametrize(
    ("eligibilite", "etat", "motif"),
    [
        ("aucune", "not_reported", None),
        ("indeterminee", "not_available", "adresse_non_verifiable"),
    ],
)
async def test_no_or_undetermined_eligibility_is_never_a_value(
    reseaux, eligibilite, etat, motif
):
    reseaux[
        "eligibilite_adresse_exacte"
    ].return_value = connectivite_queries.EligibiliteAdresse(etat=eligibilite)

    champs = _par_champ(await lieu.theme_reseaux_transports(_Session(), _contexte()))

    assert (champs[lieu.FIELD_FIBRE]["etat"], champs[lieu.FIELD_FIBRE]["motif"]) == (
        etat,
        motif,
    )
    assert champs[lieu.FIELD_FIBRE]["valeur"] is None
    assert lieu.FIELD_DEBIT not in champs


async def test_a_building_without_fibre_is_published_as_not_eligible(reseaux):
    reseaux[
        "eligibilite_adresse_exacte"
    ].return_value = connectivite_queries.EligibiliteAdresse(
        etat="unique", lignes=(_ligne(techno="CU"),)
    )

    champs = _par_champ(await lieu.theme_reseaux_transports(_Session(), _contexte()))

    assert champs[lieu.FIELD_FIBRE]["valeur"] is False
    assert lieu.FIELD_DEBIT not in champs


async def test_a_refused_broadband_publication_stays_in_its_value(reseaux, monkeypatch):
    async def refused(session, datasets):
        if "connectivite" in datasets:
            raise HTTPException(503, "Données temporairement indisponibles.")

    monkeypatch.setattr(blocs, "guard_publication", refused)

    champs = _par_champ(await lieu.theme_reseaux_transports(_Session(), _contexte()))

    assert champs[lieu.FIELD_FIBRE]["etat"] == "temporary_error"
    assert champs[lieu.FIELD_FIBRE]["motif"] == "publication_non_validee"
    assert champs[lieu.FIELD_ARRETS]["etat"] == "value"
    reseaux["eligibilite_adresse_exacte"].assert_not_called()


async def test_the_nearest_stops_carry_the_period_of_the_transport_import(reseaux):
    # One import loads the stops and the SNCF stations (`etl_transport`).
    reseaux["faits_de_provenance"].side_effect = _faits_par_jeu(
        {
            "connectivite": ProvenanceFacts(source_period_label="2026-T2"),
            "transports": ProvenanceFacts(
                last_success_at=datetime(2026, 9, 24, 23, 32, tzinfo=UTC)
            ),
        }
    )

    champs = _par_champ(await lieu.theme_reseaux_transports(_Session(), _contexte()))
    gares = _par_champ(
        await lieu.theme_reseaux_transports(
            _Session(), _contexte(transports=NON_HEBERGE)
        )
    )

    assert champs[lieu.FIELD_ARRETS]["periode"] == "2026-09-25"
    assert champs[lieu.FIELD_FIBRE]["periode"] == "2026-T2"
    assert gares[lieu.FIELD_ARRETS]["periode"] == "2026-09-25"


async def test_broadband_without_a_declared_label_carries_its_import_day(reseaux):
    """C24 (b), WINDOWS n° 86: an empty label falls back on C14, never None."""
    reseaux["faits_de_provenance"].side_effect = _faits_par_jeu(
        {
            "connectivite": ProvenanceFacts(
                last_success_at=datetime(2026, 9, 26, 22, 43, tzinfo=UTC)
            )
        }
    )

    champs = _par_champ(await lieu.theme_reseaux_transports(_Session(), _contexte()))

    assert champs[lieu.FIELD_FIBRE]["periode"] == "2026-09-27"
    assert champs[lieu.FIELD_DEBIT]["periode"] == "2026-09-27"


async def test_networks_coverage_identity_and_point(reseaux):
    hors = _par_champ(
        await lieu.theme_reseaux_transports(
            _Session(),
            _contexte(
                connectivite=NON_HEBERGE, transports=NON_HEBERGE, gares=NON_HEBERGE
            ),
        )
    )
    assert {c["etat"] for c in hors.values()} == {"out_of_coverage"}
    reseaux["eligibilite_adresse_exacte"].assert_not_called()
    reseaux["transports_a_proximite"].assert_not_called()

    gares = await lieu.theme_reseaux_transports(
        _Session(), _contexte(transports=NON_HEBERGE)
    )
    assert reseaux["transports_a_proximite"].await_args.kwargs == {"sources": ["gares"]}
    assert _par_champ(gares)[lieu.FIELD_ARRETS]["etat"] == "value"

    indeterminee = _identite(UNIQUE).model_copy(update={"etat": "indeterminee"})
    reseaux["transports_a_proximite"].reset_mock()
    sans_point = _par_champ(
        await lieu.theme_reseaux_transports(
            _Session(), _contexte(point=None, identite=indeterminee)
        )
    )
    assert sans_point[lieu.FIELD_FIBRE]["motif"] == "adresse_non_verifiable"
    assert sans_point[lieu.FIELD_ARRETS]["motif"] == "position_indeterminee"
    reseaux["transports_a_proximite"].assert_not_called()


async def test_the_four_themes_come_in_the_d115_order(lecteurs, reseaux):
    themes = await lieu.lire_themes_lieu(
        _Session(),
        _identite(UNIQUE),
        POINT,
        _hosting(),
        ARGILES,
        lieu.AmontLieu(
            risques_commune=_commune(),
            urbanisme=_gpu([_feature(libelle="UB")], []),
        ),
        AS_OF,
    )

    assert [theme.cle for theme in themes] == [
        "batiment_parcelle",
        "risques",
        "urbanisme",
        "reseaux_transports",
    ]
    assert all(theme.applicable for theme in themes)


# --- Final review: partial lists are never served as complete counts --------------


@pytest.mark.parametrize(
    ("lues", "valeur", "incomplete"), [(5, 5, False), (6, 5, True)]
)
async def test_exactly_the_bound_of_condominiums_is_a_complete_list(
    lecteurs, lues, valeur, incomplete
):
    """API IN-04: five registrations read are five, never « incomplete »."""
    lecteurs["copropriete_par_parcelle"].return_value = [
        {"numero_immatriculation": f"AA{i:07d}"} for i in range(lues)
    ]

    champ = _champs(await _theme())[lieu.FIELD_COPROPRIETE]

    assert (champ["valeur"], champ["liste_incomplete"]) == (valeur, incomplete)


def _arret(nom: str, metres: float) -> dict:
    return {
        "stop_name": nom,
        "type_transport": "arret",
        "distance_m": metres,
        "latitude": 46.67,
        "longitude": -1.43,
    }


@pytest.mark.parametrize(("lus", "servis", "incomplete"), [(4, 3, True), (3, 3, False)])
async def test_a_capped_list_of_stops_is_never_served_as_a_complete_count(
    reseaux, lus, servis, incomplete
):
    """API IN-01: the three nearest of more stops say the list is partial."""
    reseaux["transports_a_proximite"].return_value = [
        _arret(f"Arret {i}", 100 * i) for i in range(1, lus + 1)
    ]

    theme = await lieu.theme_reseaux_transports(_Session(), _contexte())

    arrets = _par_champ(theme)[lieu.FIELD_ARRETS]
    assert (arrets["etat"], arrets["valeur"], arrets["motif"]) == (
        "value",
        servis,
        None,
    )
    assert arrets["liste_incomplete"] is incomplete
    assert theme.liste_incomplete is incomplete
    assert [e["libelle"] for e in arrets["elements"]] == [
        f"Arret {i}" for i in range(1, servis + 1)
    ]
    assert reseaux["transports_a_proximite"].await_args.args[-1] == lieu.ARRETS_MAX + 1


async def test_stops_of_a_disc_crossing_a_departement_not_hosted_are_a_partial_list(
    reseaux,
):
    """C16 (API WR-02): the hosted tables hold no stop of the other departement."""
    reseaux["rayon_deborde"].return_value = True

    theme = await lieu.theme_reseaux_transports(_Session(), _contexte())

    arrets = _par_champ(theme)[lieu.FIELD_ARRETS]
    assert (arrets["etat"], arrets["valeur"], arrets["liste_incomplete"]) == (
        "value",
        2,
        True,
    )
    assert arrets["motif"] == "territoire_hors_perimetre"
    assert theme.liste_incomplete is True
    assert reseaux["rayon_deborde"].await_args.args[1:] == (
        POINT.lon,
        POINT.lat,
        lieu.RAYON_ARRETS_M,
        ["85"],
    )


async def test_no_stop_in_a_disc_crossing_a_departement_not_hosted_is_never_a_zero(
    reseaux,
):
    """C16: « aucun arrêt » is a measured zero only within the hosted territory."""
    reseaux["transports_a_proximite"].return_value = []
    zero = _par_champ(await lieu.theme_reseaux_transports(_Session(), _contexte()))
    reseaux["rayon_deborde"].return_value = True

    theme = await lieu.theme_reseaux_transports(_Session(), _contexte())

    assert (zero[lieu.FIELD_ARRETS]["etat"], zero[lieu.FIELD_ARRETS]["valeur"]) == (
        "value",
        0,
    )
    arrets = _par_champ(theme)[lieu.FIELD_ARRETS]
    assert (arrets["etat"], arrets["valeur"], arrets["motif"]) == (
        "out_of_coverage",
        None,
        "territoire_hors_perimetre",
    )
    assert arrets["liste_incomplete"] is True and arrets["elements"] == []
    assert theme.liste_incomplete is True
