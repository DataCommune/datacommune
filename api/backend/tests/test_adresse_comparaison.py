"""Addresses compared with the 03-09 rule, without a database (04-07, CMP-01).

Four groups in a fixed order, the commune reference always last; every value
is the value of the address page; gaps, reasons and verdicts come from the
single rule (``compare_cell``): a column out of coverage names the address, a
comparable non-numeric value gets ``identique`` / ``different`` and no figure,
two absences of the same state, reason and period stay identical (D-116). The
columns are read by the readers of the page: identities, then the upstreams
outside any transaction, then every guarded block in one transaction.

04-11: a DPE designated on a compared address (``designes``) is kept only when
declared at this address; its rows are those of the direct comparison of the
same documents (R06-09/7), a column without designation says so, and a DPE
block that cannot be read keeps the pair in ``temporary_error``.
"""

import datetime as dt
import json
import os
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.adresse import blocs, comparaison, lieu
from app.adresse.blocs import Bloc
from app.adresse.comparaison import construire_comparaison
from app.adresse.relations import BatimentsParcelle, PointBan
from app.adresse.router import designations_demandees, ids_compares
from app.adresse.schemas import (
    CommuneRef,
    ComparaisonAdresses,
    CouvertureAdresse,
    DepartementRef,
    DocumentSelectionne,
    ElementLieu,
    FicheAdresse,
    GroupeTheme,
    IdentiteAdresse,
    RelationParcelle,
    RepereDpe,
    RepereReferenceCommune,
    ReperesAdresse,
    RepereVentes,
    SelectionPlusRecent,
    ThemeLieu,
    ValeurLieu,
    ValeurTheme,
)
from app.adresse.service import AdresseInconnueError
from app.common.exceptions import ErreurApiError
from app.common.schemas import DatasetHosting, OutOfCoverageReason
from app.dpe import comparaison_public, document_public
from app.scope.dependencies import RouteHosting
from tests.test_adresse_lieu import NON_HEBERGE, SQL_EN_PANNE
from tests.test_adresse_lieu import POINT as POINT_LIEU
from tests.test_adresse_lieu import _hosting as _hosting_lieu
from tests.test_adresse_lieu import _Session as _SessionLieu

# The documents of the direct comparison (04-10): the same objects are
# designated here, so both answers can be compared cell by cell.
from tests.test_dpe_comparaison_public import (  # noqa: F401 -- autouse fixture
    ACTUEL,
    ANCIEN,
    AUTRE,
    _adresse,
    _ancien,
    _document,
    _sans_provenance,
)

pytestmark = pytest.mark.unit


async def _acted(_session, _tables):
    return True


@pytest.fixture(autouse=True)
def _publication_acted(monkeypatch):
    """The fake sessions answer the block reads only: the publication guard of
    the imported tables is proven on PostgreSQL
    (``tests/integration/test_rnb_liens_elargissement.py``)."""
    monkeypatch.setattr(blocs, "publication_actee", _acted)


AS_OF = dt.date(2026, 10, 5)
GROUPES = ["documents", "ventes", "lieu", "commune_reference"]


def _valeur(field_id: str, **etat) -> ValeurLieu:
    return ValeurLieu(field_id=field_id, **{"etat": "value", **etat})


def _champ(field_id: str, **etat) -> ValeurTheme:
    return ValeurTheme(field_id=field_id, **{"etat": "value", **etat})


def _selection(date: str | None = "2024-03-01", classe: str = "D", **changes):
    base = {
        "etat": "unique",
        "date": dt.date.fromisoformat(date) if date else None,
        "document": DocumentSelectionne(numero="2485E0000000X", etiquette_dpe=classe),
        "motif": "document_valide",
    }
    return SelectionPlusRecent(**{**base, **changes})


def _fiche(
    ban_id: str = "85191_0420_00012",
    *,
    dpe: int = 2,
    selection: SelectionPlusRecent | None = None,
    ventes: dict | None = None,
    argiles: str = "Exposition moyenne",
    contenance: int = 1412,
    hauteur: float | None = 7.5,
    zone: tuple[str, ...] = ("UB · Zone urbaine",),
    fibre: bool = True,
    arrets: tuple[int, ...] = (100, 600),
    maisons: int = 2100,
    batiment: dict | None = None,
    voie: tuple[str, str, str] = ("12", "Rue des Lilas", "85000"),
) -> FicheAdresse:
    champs_batiment = [_champ("adresse.parcelle.contenance", valeur=contenance)]
    if hauteur is not None:
        champs_batiment.insert(0, _champ("adresse.batiment.hauteur", valeur=hauteur))
    themes = [
        ThemeLieu(
            cle="batiment_parcelle",
            titre="Bâtiment et parcelle",
            **(batiment or {"etat": "value"}),
            groupes=[]
            if batiment
            else [GroupeTheme(cle="lieu", champs=champs_batiment)],
        ),
        ThemeLieu(
            cle="urbanisme",
            titre="Urbanisme",
            etat="value",
            groupes=[
                GroupeTheme(
                    cle="au_point",
                    champs=[
                        _champ(
                            "adresse.urbanisme.zone_plu",
                            valeur=len(zone),
                            periode="2026-10-05",
                            elements=[ElementLieu(libelle=z) for z in zone],
                        )
                    ],
                )
            ],
        ),
        ThemeLieu(
            cle="reseaux_transports",
            titre="Réseaux et transports",
            etat="value",
            groupes=[
                GroupeTheme(
                    cle="lieu",
                    champs=[
                        _champ(
                            "adresse.connectivite.eligibilite_fibre",
                            valeur=fibre,
                            periode="2026-T2",
                        ),
                        _champ(
                            "adresse.transports.arret_proche",
                            valeur=len(arrets),
                            rayon_m=1000,
                            elements=[
                                ElementLieu(libelle=f"Arrêt {d}", distance_m=d)
                                for d in arrets
                            ],
                        ),
                    ],
                )
            ],
        ),
    ]
    ventes = ventes or {"etat": "value", "valeur": 3, "periode": "2021-2025"}
    derniere = (
        {"etat": "value", "valeur": "2024-06-30", "periode": "2021-2025"}
        if ventes["etat"] == "value"
        else ventes
    )
    reperes = ReperesAdresse(
        dpe=RepereDpe(
            field_id="adresse.dpe.nombre_declares",
            etat="value",
            valeur=dpe,
            periode="2026-09-30",
            rattachement="adresse_saisie_diagnostiqueur",
            selection=selection or _selection(),
        ),
        ventes=RepereVentes(
            nombre=_valeur("adresse.dvf.ventes_parcelle.nombre", **ventes),
            derniere=_valeur("adresse.dvf.ventes_parcelle.derniere", **derniere),
        ),
        argiles=_valeur(
            "adresse.risques.argiles_au_point", valeur=argiles, periode="2026-10-05"
        ),
        reference_commune=RepereReferenceCommune(
            maisons=_valeur(
                "adresse.reference_commune.prix_median_maison",
                valeur=maisons,
                periode="2021-2025",
                rattachement="commune_reference",
            ),
            appartements=_valeur(
                "adresse.reference_commune.prix_median_appartement",
                etat="insufficient_sample",
                periode="2021-2025",
                rattachement="commune_reference",
            ),
        ),
    )
    return FicheAdresse(
        identite=_identite(ban_id, *voie),
        couverture=CouvertureAdresse(etat="heberge"),
        reperes=reperes,
        themes=themes,
    )


def _identite(
    ban_id: str,
    numero: str = "12",
    nom_voie: str = "Rue des Lilas",
    code_postal: str = "85000",
) -> IdentiteAdresse:
    return IdentiteAdresse(
        ban_id=ban_id,
        libelle=f"{numero} {nom_voie}, {code_postal} La Roche-sur-Yon",
        numero=numero,
        nom_voie=nom_voie,
        code_postal=code_postal,
        commune=CommuneRef(code_insee="85191", nom="La Roche-sur-Yon"),
        departement=DepartementRef(code="85", nom="Vendée"),
        parcelle=RelationParcelle(etat="unique", id_parcelle="85191000CA0001"),
    )


def _hors_couverture(ban_id: str = "35238_1000_00001") -> FicheAdresse:
    return FicheAdresse(
        identite=IdentiteAdresse(
            ban_id=ban_id,
            commune=CommuneRef(code_insee="35238", nom="Rennes"),
            departement=DepartementRef(code="35", nom="Ille-et-Vilaine"),
        ),
        couverture=CouvertureAdresse(
            etat="hors_couverture", motif=OutOfCoverageReason.DATASET_NON_HEBERGE
        ),
    )


def _ligne(resultat: ComparaisonAdresses, cle: str):
    return next(
        ligne
        for groupe in resultat.groupes
        for ligne in groupe.lignes
        if ligne.cle == cle
    )


# --- Groups, order, columns ----------------------------------------------------------


def test_four_groups_in_order_the_commune_reference_last():
    resultat = construire_comparaison([_fiche(), _fiche("85191_0601_00001")])

    assert [groupe.cle for groupe in resultat.groupes] == GROUPES
    assert resultat.groupes[-1].titre == "Commune de l'adresse (référence)"
    lignes = {g.cle: [ligne.cle for ligne in g.lignes] for g in resultat.groupes}
    assert lignes == {
        "documents": ["dpe_declares", "dpe_plus_recent"],
        "ventes": ["ventes_parcelle", "derniere_vente", "ventes_declarees"],
        "lieu": [
            "hauteur",
            "contenance",
            "argiles",
            "zone_plu",
            "fibre",
            "arret_proche",
        ],
        "commune_reference": ["prix_median_maison", "prix_median_appartement"],
    }
    # A value of the commune never leaves its group (CMP-01).
    for groupe in resultat.groupes:
        for ligne in groupe.lignes:
            communal = all(
                c.rattachement == "commune_reference" for c in ligne.cellules
            )
            assert communal is (groupe.cle == "commune_reference"), ligne.cle


def test_the_schema_refuses_a_reference_group_that_is_not_last():
    resultat = construire_comparaison([_fiche(), _fiche("85191_0601_00001")])
    groupes = [resultat.groupes[-1], *resultat.groupes[:-1]]
    with pytest.raises(ValidationError):
        ComparaisonAdresses(colonnes=resultat.colonnes, groupes=groupes)


@pytest.mark.parametrize("n", [2, 4])
def test_two_and_four_columns_one_cell_per_column_in_order(n):
    ids = [f"85191_0420_0001{i}" for i in range(n)]
    resultat = construire_comparaison([_fiche(ban_id) for ban_id in ids])

    assert [colonne.ban_id for colonne in resultat.colonnes] == ids
    assert resultat.colonnes[0].numero_voie == "12 Rue des Lilas"
    assert resultat.colonnes[0].code_postal == "85000"
    for groupe in resultat.groupes:
        for ligne in groupe.lignes:
            assert [c.ban_id for c in ligne.cellules] == ids
            reference, *autres = ligne.cellules
            assert reference.identique_a_la_reference is None
            assert all(c.identique_a_la_reference is not None for c in autres)


# --- Gaps, reasons, verdicts (one rule) ----------------------------------------------


def test_a_numeric_gap_is_served_cell_minus_reference():
    resultat = construire_comparaison(
        [
            _fiche(contenance=1412, ventes={"etat": "value", "valeur": 3}),
            _fiche(
                "85191_0601_00001",
                contenance=1000,
                ventes={"etat": "value", "valeur": 5},
            ),
        ]
    )

    contenance = _ligne(resultat, "contenance").cellules[1]
    assert contenance.ecart.model_dump() == {"valeur": -412, "unite": "m2"}
    assert contenance.valeur == 1000  # the value of the page, never recomputed
    ventes = _ligne(resultat, "ventes_parcelle").cellules[1]
    assert ventes.ecart.model_dump() == {"valeur": 2, "unite": "count"}


def test_categorical_values_get_a_verdict_and_no_figure():
    resultat = construire_comparaison(
        [
            _fiche(argiles="Exposition moyenne", fibre=True, zone=("UB",)),
            _fiche(
                "85191_0601_00001", argiles="Exposition forte", fibre=True, zone=("UB",)
            ),
        ]
    )

    for cle, verdict in (("argiles", "different"), ("fibre", "identique")):
        cellule = _ligne(resultat, cle).cellules[1]
        assert (cellule.ecart, cellule.motif_non_calcul) == (None, None), cle
        assert cellule.verdict == verdict, cle
    zone = _ligne(resultat, "zone_plu")
    assert zone.cellules[1].verdict == "identique"
    assert zone.identiques is True
    # The zone is compared by its served labels, not by its count.
    autre = construire_comparaison(
        [_fiche(zone=("UB",)), _fiche("85191_0601_00001", zone=("UA",))]
    )
    assert _ligne(autre, "zone_plu").cellules[1].verdict == "different"


def _zone_incomplete(fiche: FicheAdresse) -> FicheAdresse:
    themes = []
    for theme in fiche.themes:
        if theme.cle == "urbanisme":
            groupe = theme.groupes[0]
            champ = groupe.champs[0].model_copy(update={"liste_incomplete": True})
            theme = theme.model_copy(
                update={"groupes": [groupe.model_copy(update={"champs": [champ]})]}
            )
        themes.append(theme)
    return fiche.model_copy(update={"themes": themes})


def test_two_served_units_get_no_gap():
    """D-15 API IN-11: the served unit is compared, never the contract's alone."""
    fiche = _fiche("85191_0601_00001")
    theme = fiche.themes[0]
    champs = [
        champ.model_copy(update={"unite": "hectare"})
        if champ.field_id == "adresse.parcelle.contenance"
        else champ
        for champ in theme.groupes[0].champs
    ]
    theme = theme.model_copy(
        update={"groupes": [GroupeTheme(cle="lieu", champs=champs)]}
    )
    resultat = construire_comparaison([_fiche(), _avec_theme(fiche, theme)])

    cellule = _ligne(resultat, "contenance").cellules[1]
    assert (cellule.ecart, cellule.motif_non_calcul) == (None, "unites_differentes")


def test_zones_read_in_part_get_no_verdict():
    """D-15 API WR-04: zones not read may differ, never « Identique »."""
    for premiere, seconde in (
        (_zone_incomplete(_fiche()), _zone_incomplete(_fiche("85191_0601_00001"))),
        (_fiche(), _zone_incomplete(_fiche("85191_0601_00001"))),
        (_zone_incomplete(_fiche()), _fiche("85191_0601_00001")),
    ):
        ligne = _ligne(construire_comparaison([premiere, seconde]), "zone_plu")
        cellule = ligne.cellules[1]
        assert (cellule.verdict, cellule.ecart) == (None, None)
        assert cellule.motif_non_calcul == "liste_incomplete"
        assert cellule.identique_a_la_reference is False
        assert ligne.identiques is False
    # Two complete lists keep their categorical verdict.
    complete = construire_comparaison([_fiche(), _fiche("85191_0601_00001")])
    assert _ligne(complete, "zone_plu").cellules[1].verdict == "identique"


def test_the_nearest_stop_is_compared_in_metres_never_a_zero_distance():
    resultat = construire_comparaison(
        [_fiche(arrets=(100, 600)), _fiche("85191_0601_00001", arrets=(300,))]
    )
    cellule = _ligne(resultat, "arret_proche").cellules[1]
    assert cellule.ecart.model_dump() == {"valeur": 200, "unite": "metre"}

    sans_arret = construire_comparaison(
        [_fiche(arrets=(100,)), _fiche("85191_0601_00001", arrets=())]
    )
    cellule = _ligne(sans_arret, "arret_proche").cellules[1]
    # No stop within the radius is an observed zero, never « donnée absente »
    # (D-15 API WR-01): no figure, the verdict says it differs.
    assert (cellule.ecart, cellule.motif_non_calcul) == (None, None)
    assert cellule.verdict == "different"
    assert (cellule.etat, cellule.valeur) == ("value", 0)

    deux_zeros = construire_comparaison(
        [_fiche(arrets=()), _fiche("85191_0601_00001", arrets=())]
    )
    ligne = _ligne(deux_zeros, "arret_proche")
    assert ligne.cellules[1].motif_non_calcul is None
    assert ligne.cellules[1].verdict == "identique"
    assert ligne.identiques is True


def test_an_address_out_of_coverage_keeps_its_column_without_any_gap():
    resultat = construire_comparaison([_fiche(), _hors_couverture()])

    colonne = resultat.colonnes[1]
    assert colonne.couverture.etat == "hors_couverture"
    for groupe in resultat.groupes:
        for ligne in groupe.lignes:
            cellule = ligne.cellules[1]
            assert (cellule.etat, cellule.valeur) == ("out_of_coverage", None)
            assert cellule.motif == "dataset_non_heberge"
            assert cellule.motif_non_calcul == "adresse_hors_couverture"
            assert ligne.identiques is False


def test_two_absences_of_the_same_state_and_period_stay_identical():
    """D-116: hidden by « Différences seulement », for addresses too."""
    absent = {"etat": "not_available", "motif": "point_hors_parcelle"}
    resultat = construire_comparaison(
        [_fiche(ventes=absent), _fiche("85191_0601_00001", ventes=absent)]
    )
    ligne = _ligne(resultat, "ventes_parcelle")
    assert ligne.cellules[1].motif_non_calcul == "donnee_absente"
    assert ligne.cellules[1].identique_a_la_reference is True
    assert ligne.identiques is True
    # A theme without value gives its state to its rows, identical as well.
    hors = {"etat": "not_available", "motif": "point_hors_parcelle"}
    resultat = construire_comparaison(
        [_fiche(batiment=hors), _fiche("85191_0601_00001", batiment=hors)]
    )
    contenance = _ligne(resultat, "contenance")
    assert [c.etat for c in contenance.cellules] == ["not_available"] * 2
    assert contenance.identiques is True


def test_absences_of_different_reasons_are_not_identical():
    """« Point hors parcelle » and « Plusieurs parcelles » are distinct states."""
    resultat = construire_comparaison(
        [
            _fiche(ventes={"etat": "not_available", "motif": "point_hors_parcelle"}),
            _fiche(
                "85191_0601_00001",
                ventes={"etat": "not_available", "motif": "parcelles_multiples"},
            ),
        ]
    )
    ligne = _ligne(resultat, "ventes_parcelle")
    assert ligne.cellules[1].identique_a_la_reference is False
    assert ligne.identiques is False
    # Never a zero, never a parcel chosen: the reason is served as such.
    assert [c.motif for c in ligne.cellules] == [
        "point_hors_parcelle",
        "parcelles_multiples",
    ]
    assert [c.valeur for c in ligne.cellules] == [None, None]


def test_the_height_of_a_point_without_a_single_building_is_a_typed_gap():
    fiche = _fiche("85191_0601_00001", hauteur=None)
    theme = fiche.themes[0]
    champs = [
        _champ(
            "adresse.batiment.hauteur",
            etat="not_available",
            motif="batiment_du_point_non_unique",
            rattachement="batiment_du_point",
        ),
        *theme.groupes[0].champs,
    ]
    theme = theme.model_copy(
        update={"groupes": [GroupeTheme(cle="lieu", champs=champs)]}
    )
    resultat = construire_comparaison([_fiche(hauteur=7.5), _avec_theme(fiche, theme)])
    cellule = _ligne(resultat, "hauteur").cellules[1]
    assert (cellule.etat, cellule.motif) == (
        "not_available",
        "batiment_du_point_non_unique",
    )
    assert cellule.rattachement == "batiment_du_point"
    assert cellule.motif_non_calcul == "donnee_absente"
    # A height the page does not serve gets no reason the page did not give
    # (D-15 API CR-01: the absence of a field was read as « several »).
    omise = construire_comparaison(
        [_fiche(hauteur=7.5), _fiche("85191_0601_00001", hauteur=None)]
    )
    cellule = _ligne(omise, "hauteur").cellules[1]
    assert (cellule.etat, cellule.motif) == ("not_reported", None)


# --- The height row read through the real theme reader (D-15 API CR-01) ------------

# Each cause of an absent height, as the reader of the page serves it: the
# expected (state, reason) of the height cell of the comparison.
CAUSES_HAUTEUR = {
    "aucun_batiment": ("not_available", "aucun_batiment_au_point"),
    "plusieurs_batiments": ("not_available", "batiment_du_point_non_unique"),
    "batiments_hors_couverture": ("out_of_coverage", "dataset_non_heberge"),
    "batiments_en_panne": ("temporary_error", "lecture_indisponible"),
    "parcelle_hors_cadastre": ("not_reported", None),
}


async def _theme_batiment_reel(monkeypatch, cause: str) -> ThemeLieu:
    """« Bâtiment et parcelle » served by ``theme_batiment_parcelle`` itself."""

    async def no_guard(session, datasets):
        return None

    monkeypatch.setattr(blocs, "guard_publication", no_guard)
    au_point = {
        "aucun_batiment": [],
        "plusieurs_batiments": [
            {"cleabs": "A", "hauteur": 6.0, "nombre_d_etages": 1},
            {"cleabs": "B", "hauteur": 3.0, "nombre_d_etages": 1},
        ],
    }.get(cause, [])
    sur_parcelle = AsyncMock(return_value=BatimentsParcelle(total=2, hors_annexe=2))
    if cause == "batiments_en_panne":
        sur_parcelle.side_effect = SQL_EN_PANNE
    if cause == "parcelle_hors_cadastre":
        sur_parcelle.return_value = None
    doubles = {
        "batiments_sur_parcelle": sur_parcelle,
        "batiments_par_point": AsyncMock(return_value=au_point),
        "parcelle_par_reference": AsyncMock(return_value=[]),
        "copropriete_par_parcelle": AsyncMock(return_value=[]),
    }
    for nom, double in doubles.items():
        monkeypatch.setattr(lieu, nom, double)
    hors = {"batiments": NON_HEBERGE} if cause == "batiments_hors_couverture" else {}
    contexte = lieu.Contexte(
        identite=_identite("85191_0420_00012"),
        point=POINT_LIEU,
        hosting=_hosting_lieu(**hors),
    )
    return await lieu.theme_batiment_parcelle(_SessionLieu(), contexte)


def _avec_theme(fiche: FicheAdresse, theme: ThemeLieu) -> FicheAdresse:
    return fiche.model_copy(update={"themes": [theme, *fiche.themes[1:]]})


@pytest.mark.parametrize("cause", sorted(CAUSES_HAUTEUR))
async def test_each_cause_of_an_absent_height_keeps_its_own_state(monkeypatch, cause):
    """Zero building, several, out of coverage, outage, parcel off the cadastre."""
    theme = await _theme_batiment_reel(monkeypatch, cause)
    resultat = construire_comparaison(
        [_avec_theme(_fiche(), theme), _fiche("85191_0601_00001")]
    )

    cellule = _ligne(resultat, "hauteur").cellules[0]
    assert (cellule.etat, cellule.motif) == CAUSES_HAUTEUR[cause]
    assert cellule.valeur is None
    if cause != "plusieurs_batiments":
        # « Plusieurs bâtiments couvrent le point » is said for that cause only.
        assert cellule.motif != "batiment_du_point_non_unique"


@pytest.mark.parametrize(
    ("une", "autre"),
    [
        (une, autre)
        for une in sorted(CAUSES_HAUTEUR)
        for autre in sorted(CAUSES_HAUTEUR)
        if une < autre
    ],
)
async def test_two_different_causes_of_an_absent_height_are_not_identical(
    monkeypatch, une, autre
):
    premiere = await _theme_batiment_reel(monkeypatch, une)
    seconde = await _theme_batiment_reel(monkeypatch, autre)
    resultat = construire_comparaison(
        [
            _avec_theme(_fiche(), premiere),
            _avec_theme(_fiche("85191_0601_00001"), seconde),
        ]
    )

    ligne = _ligne(resultat, "hauteur")
    assert ligne.cellules[1].identique_a_la_reference is False
    assert ligne.identiques is False


def test_a_tie_of_the_most_recent_documents_names_no_document():
    """04-03: a tie or an undetermined selection, never a number chosen here."""
    egalite = _selection(
        etat="egalite",
        document=None,
        documents_ex_aequo=[
            DocumentSelectionne(numero="A", etiquette_dpe="C"),
            DocumentSelectionne(numero="B", etiquette_dpe="E"),
        ],
    )
    resultat = construire_comparaison(
        [_fiche(selection=egalite), _fiche("85191_0601_00001", selection=egalite)]
    )
    ligne = _ligne(resultat, "dpe_plus_recent")
    reference = ligne.cellules[0]
    assert reference.field_id == "adresse.dpe.plus_recent"
    assert (reference.etat, reference.valeur) == ("value", "2024-03-01")
    assert reference.selection.etat == "egalite"
    assert reference.selection.document is None
    assert ligne.cellules[1].verdict == "identique"

    unique = construire_comparaison(
        [_fiche(selection=egalite), _fiche("85191_0601_00001")]
    )
    cellule = _ligne(unique, "dpe_plus_recent").cellules[1]
    # Same date, but a tie against one document of class D: different.
    assert cellule.verdict == "different"
    assert cellule.selection.document.numero == "2485E0000000X"


def test_the_most_recent_classes_of_two_methods_get_no_verdict():
    """D-15 API WR-02, R06-09/7: an old-method class never compares to a new one."""
    ancien = _selection(date="2021-06-15", classe="D", motif="ancienne_methode")
    resultat = construire_comparaison(
        [_fiche(selection=ancien), _fiche("85191_0601_00001", selection=_selection())]
    )

    cellule = _ligne(resultat, "dpe_plus_recent").cellules[1]
    assert (cellule.verdict, cellule.ecart) == (None, None)
    assert cellule.motif_non_calcul == "methodes_differentes"
    assert _ligne(resultat, "dpe_plus_recent").identiques is False
    # Same method: the verdict of the served values is unchanged.
    meme = construire_comparaison(
        [_fiche(), _fiche("85191_0601_00001", selection=_selection(classe="C"))]
    )
    assert _ligne(meme, "dpe_plus_recent").cellules[1].verdict == "different"


def test_an_undetermined_or_expired_selection_keeps_its_reason():
    indeterminee = _selection(etat="indeterminee", date=None, document=None)
    indeterminee = indeterminee.model_copy(update={"motif": "aucun_document"})
    expire = _selection(motif="expire")
    resultat = construire_comparaison(
        [
            _fiche(dpe=0, selection=indeterminee),
            _fiche("85191_0601_00001", selection=expire),
        ]
    )
    reference, autre = _ligne(resultat, "dpe_plus_recent").cellules
    assert (reference.etat, reference.motif) == ("not_available", "aucun_document")
    assert autre.motif_non_calcul == "donnee_absente"
    # The validity of the most recent document is part of what is compared.
    valide = construire_comparaison(
        [_fiche(), _fiche("85191_0601_00001", selection=expire)]
    )
    assert _ligne(valide, "dpe_plus_recent").cellules[1].verdict == "different"


def test_a_refused_publication_stays_in_the_rows_of_its_source():
    """The page serves a refused DVF publication on ② and ④ only: so does the row."""
    panne = {"etat": "temporary_error", "motif": "publication_non_validee"}
    fiche = _fiche()
    reperes = fiche.reperes.model_copy(
        update={
            "ventes": RepereVentes(
                nombre=_valeur("adresse.dvf.ventes_parcelle.nombre", **panne),
                derniere=_valeur("adresse.dvf.ventes_parcelle.derniere", **panne),
            )
        }
    )
    resultat = construire_comparaison(
        [fiche.model_copy(update={"reperes": reperes}), _fiche("85191_0601_00001")]
    )
    ventes = _ligne(resultat, "ventes_parcelle").cellules[0]
    assert (ventes.etat, ventes.motif) == ("temporary_error", "publication_non_validee")
    assert _ligne(resultat, "dpe_declares").cellules[0].etat == "value"
    assert _ligne(resultat, "contenance").cellules[0].etat == "value"


# --- Sales declared at the address (C40) ---------------------------------------------

FIELD_DECLAREES = "adresse.dvf.ventes_adresse.nombre"


def _declarees(**etat) -> ValeurLieu:
    """The count read for a column by the reader of the C37 route."""
    return _valeur(
        FIELD_DECLAREES,
        unite="count",
        rattachement="adresse_declaree_vente",
        source=blocs.source_valeur("dvf"),
        **{"periode": "2021-2025", **etat},
    )


def test_the_declared_sales_row_follows_the_last_sale_with_its_states():
    """C40: count, period and source of the address page; no sale attached, a
    suffix not distinguished and a column out of coverage stay distinct."""
    lilas, zero, bis = "85191_0420_00012", "85191_0601_00001", "85191_0602_00001"
    resultat = construire_comparaison(
        [_fiche(lilas), _fiche(zero), _fiche(bis), _hors_couverture()],
        annexes={
            lilas: {FIELD_DECLAREES: _declarees(valeur=3)},
            zero: {
                FIELD_DECLAREES: _declarees(
                    etat="not_available", motif="aucune_vente_rattachee"
                )
            },
            bis: {
                FIELD_DECLAREES: _declarees(
                    etat="not_available", motif="suffixe_non_distingue", periode=None
                )
            },
        },
    )

    ventes = next(g for g in resultat.groupes if g.cle == "ventes")
    assert [ligne.cle for ligne in ventes.lignes][-2:] == [
        "derniere_vente",
        "ventes_declarees",
    ]
    reference, vide, suffixe, hors = _ligne(resultat, "ventes_declarees").cellules
    assert (reference.etat, reference.valeur, reference.periode) == (
        "value",
        3,
        "2021-2025",
    )
    assert reference.rattachement == "adresse_declaree_vente"
    assert reference.source.cle == "dvf" and reference.field_id == FIELD_DECLAREES
    # C44 (CR-02): no sale attached is not a zero of the source: its state,
    # with the period searched, never a figure nor a gap.
    assert (vide.etat, vide.motif, vide.valeur) == (
        "not_available",
        "aucune_vente_rattachee",
        None,
    )
    assert (vide.periode, vide.ecart) == ("2021-2025", None)
    assert vide.motif_non_calcul == "donnee_absente"
    assert (suffixe.etat, suffixe.motif) == ("not_available", "suffixe_non_distingue")
    assert (suffixe.valeur, suffixe.ecart) == (None, None)
    assert suffixe.motif_non_calcul == "donnee_absente"
    assert (hors.etat, hors.motif_non_calcul) == (
        "out_of_coverage",
        "adresse_hors_couverture",
    )


def test_a_declared_count_not_read_is_an_outage_never_a_zero():
    """A hosted column without its reading (or a refused publication) stays an
    outage of the row; the rows of the page are untouched."""
    resultat = construire_comparaison(
        [_fiche(), _fiche("85191_0601_00001")],
        annexes={
            "85191_0420_00012": {
                FIELD_DECLAREES: _declarees(
                    etat="temporary_error", motif="publication_non_validee"
                )
            }
        },
    )

    refusee, absente = _ligne(resultat, "ventes_declarees").cellules
    assert (refusee.etat, refusee.motif) == (
        "temporary_error",
        "publication_non_validee",
    )
    assert (absente.etat, absente.valeur) == ("temporary_error", None)
    assert absente.rattachement == "adresse_declaree_vente"
    assert _ligne(resultat, "ventes_parcelle").cellules[1].valeur == 3


async def test_the_declared_count_reuses_the_query_of_the_route(monkeypatch):
    """C40: the comparator counts with the key and the count query of C37,
    never a copy; a suffix, an out-of-coverage source and an outage keep
    their states, and the cards are never read."""
    from app.adresse import ventes as ventes_module

    lues: list[str] = []

    class _Resultat:
        def __init__(self, valeur):
            self.valeur = valeur

        def scalar_one(self):
            return self.valeur

    class _Session:
        async def execute(self, statement, params=None):
            lues.append(str(statement))
            assert statement is ventes_module._REQUETES_DECLAREES[(False, "exclues")][0]
            assert params == {
                "departement": "85",
                "commune": "85191",
                "numero": "12",
                "voies": ["RUE DES LILAS"],
            }
            return _Resultat(4)

    async def cle(session, ban_id, regle="numero"):
        return True, ventes_module.CleAdresseDvf(
            motif=None, commune="85191", numero="12", voies=("RUE DES LILAS",)
        )

    async def bloc(session, sources, lecteur):
        assert sources == ["dvf"]
        return Bloc(valeur=await lecteur())

    async def faits(session, source):
        return None

    monkeypatch.setattr(ventes_module, "_cle_de_l_adresse", cle)
    monkeypatch.setattr(ventes_module, "lire_bloc", bloc)
    monkeypatch.setattr(ventes_module, "faits_de_provenance", faits)
    monkeypatch.setattr(ventes_module, "periode_de_source", lambda _: "2021-2025")
    hosting = _hosting_lieu()

    valeur = await ventes_module.compte_ventes_adresse(
        _Session(), "85191_0420_00012", hosting
    )

    assert (valeur.etat, valeur.valeur, valeur.periode) == ("value", 4, "2021-2025")
    assert valeur.field_id == FIELD_DECLAREES and valeur.unite == "count"
    assert valeur.rattachement == "adresse_declaree_vente"
    assert len(lues) == 1  # the count only, no page of cards

    async def suffixe(session, ban_id, regle="numero"):
        return True, ventes_module.CleAdresseDvf(motif="suffixe_non_distingue")

    monkeypatch.setattr(ventes_module, "_cle_de_l_adresse", suffixe)
    valeur = await ventes_module.compte_ventes_adresse(
        _Session(), "85191_0420_00012", hosting
    )
    assert (valeur.etat, valeur.motif) == ("not_available", "suffixe_non_distingue")

    async def en_panne(session, sources, lecteur):
        return Bloc(panne="publication_non_validee")

    monkeypatch.setattr(ventes_module, "_cle_de_l_adresse", cle)
    monkeypatch.setattr(ventes_module, "lire_bloc", en_panne)
    valeur = await ventes_module.compte_ventes_adresse(
        _Session(), "85191_0420_00012", hosting
    )
    assert (valeur.etat, valeur.motif) == ("temporary_error", "publication_non_validee")

    async def illisible(session, ban_id, regle="numero"):
        raise ventes_module.VentesIndisponiblesError

    monkeypatch.setattr(ventes_module, "_cle_de_l_adresse", illisible)
    valeur = await ventes_module.compte_ventes_adresse(
        _Session(), "85191_0420_00012", hosting
    )
    assert (valeur.etat, valeur.motif) == ("temporary_error", "lecture_indisponible")

    non_heberge = _hosting_lieu(dvf=NON_HEBERGE)
    valeur = await ventes_module.compte_ventes_adresse(
        _Session(), "85191_0420_00012", non_heberge
    )
    assert (valeur.etat, valeur.motif) == ("out_of_coverage", "dataset_non_heberge")
    assert len(lues) == 1


async def test_no_declared_sale_attached_is_served_as_its_state(monkeypatch):
    """C44 (CR-02): a count of 0 says that no sale was attached to the address,
    not that none took place (first line of a sale only, sales without a BAN
    address): the row serves that state with the period searched, never the
    figure 0 as a fact."""
    from app.adresse import ventes as ventes_module

    class _Resultat:
        def scalar_one(self):
            return 0

    class _Session:
        async def execute(self, statement, params=None):
            assert statement is ventes_module._REQUETES_DECLAREES[(False, "exclues")][0]
            return _Resultat()

    async def cle(session, ban_id, regle="numero"):
        return True, ventes_module.CleAdresseDvf(
            motif=None, commune="85191", numero="12", voies=("RUE DES LILAS",)
        )

    async def bloc(session, sources, lecteur):
        return Bloc(valeur=await lecteur())

    async def faits(session, source):
        return None

    monkeypatch.setattr(ventes_module, "_cle_de_l_adresse", cle)
    monkeypatch.setattr(ventes_module, "lire_bloc", bloc)
    monkeypatch.setattr(ventes_module, "faits_de_provenance", faits)
    monkeypatch.setattr(ventes_module, "periode_de_source", lambda _: "2021-2025")

    valeur = await ventes_module.compte_ventes_adresse(
        _Session(), "85191_0420_00012", _hosting_lieu()
    )

    assert (valeur.etat, valeur.motif, valeur.valeur, valeur.periode) == (
        "not_available",
        "aucune_vente_rattachee",
        None,
        "2021-2025",
    )
    assert valeur.field_id == FIELD_DECLAREES
    assert valeur.rattachement == "adresse_declaree_vente"


async def test_the_declared_count_follows_the_number_and_suffix_rule(monkeypatch):
    """04.5-08 (C3, R8 « comparateur aligné »): under ``numero_et_suffixe`` the
    comparator counts with the key, the marker guard and the suffix-filtered
    count query of the route, the sales only (never a VEFA); a generation
    imported without the suffix is the typed absence of the route
    (``suffixe_non_importe``), never a count of every sale of the number."""
    from app.adresse import ventes as ventes_module

    compte_suffixe = ventes_module._REQUETES_DECLAREES[(True, "exclues")][0]
    assert (
        compte_suffixe is not ventes_module._REQUETES_DECLAREES[(False, "exclues")][0]
    )
    assert "adresse_suffixe" in str(compte_suffixe)
    # The sales only (D-12): the natures of the default list, never those with the VEFA.
    natures = ventes_module._NATURES_DECLAREES
    assert f"AND {natures['exclues']}\n" in str(compte_suffixe)
    assert f"AND {natures['incluses']}" not in str(compte_suffixe)
    lues: list[object] = []
    marqueur = {"valeur": ventes_module.MARQUEUR_ADRESSE_SUFFIXE}

    class _Resultat:
        def __init__(self, valeur):
            self.valeur = valeur

        def scalar_one(self):
            return self.valeur

        def scalar_one_or_none(self):
            return self.valeur

    class _Session:
        async def execute(self, statement, params=None):
            lues.append(statement)
            if statement is ventes_module._MARQUEUR_SUFFIXE_SQL:
                return _Resultat(marqueur["valeur"])
            assert statement is compte_suffixe
            assert params == {
                "departement": "85",
                "commune": "85195",
                "numero": "2",
                "voies": ["RUE DES SUFFIXES"],
                "suffixe": "B",
            }
            return _Resultat(2)

    regles: list[str] = []

    async def cle(session, ban_id, regle="numero"):
        regles.append(regle)
        return True, ventes_module.CleAdresseDvf(
            motif=None,
            commune="85195",
            numero="2",
            voies=("RUE DES SUFFIXES",),
            suffixe="B",
        )

    async def bloc(session, sources, lecteur):
        assert sources == ["dvf"]
        return Bloc(valeur=await lecteur())

    async def faits(session, source):
        return None

    monkeypatch.setattr(ventes_module, "_cle_de_l_adresse", cle)
    monkeypatch.setattr(ventes_module, "lire_bloc", bloc)
    monkeypatch.setattr(ventes_module, "faits_de_provenance", faits)
    monkeypatch.setattr(ventes_module, "periode_de_source", lambda _: "2021-2025")
    hosting = _hosting_lieu()

    valeur = await ventes_module.compte_ventes_adresse(
        _Session(), "85195_0911_00002_bis", hosting, regle="numero_et_suffixe"
    )

    assert regles == ["numero_et_suffixe"]
    assert (valeur.etat, valeur.valeur, valeur.periode) == ("value", 2, "2021-2025")
    assert lues == [ventes_module._MARQUEUR_SUFFIXE_SQL, compte_suffixe]

    # Marker absent (migrated, not reimported): the typed absence, the count unread.
    marqueur["valeur"] = None
    lues.clear()
    valeur = await ventes_module.compte_ventes_adresse(
        _Session(), "85195_0911_00002_bis", hosting, regle="numero_et_suffixe"
    )
    assert (valeur.etat, valeur.motif, valeur.valeur) == (
        "not_available",
        "suffixe_non_importe",
        None,
    )
    assert valeur.field_id == FIELD_DECLAREES
    assert lues == [ventes_module._MARQUEUR_SUFFIXE_SQL]

    # A suffix without single correspondence: the served reason, nothing read.
    async def sans_correspondance(session, ban_id, regle="numero"):
        return True, ventes_module.CleAdresseDvf(motif="suffixe_sans_correspondance")

    monkeypatch.setattr(ventes_module, "_cle_de_l_adresse", sans_correspondance)
    lues.clear()
    valeur = await ventes_module.compte_ventes_adresse(
        _Session(), "85195_0911_00004_quinquies", hosting, regle="numero_et_suffixe"
    )
    assert (valeur.etat, valeur.motif) == (
        "not_available",
        "suffixe_sans_correspondance",
    )
    assert lues == []


# --- Requested keys (T-04-07-01) -----------------------------------------------------


@pytest.mark.parametrize(
    "ids",
    [
        None,
        "",
        "85191_0420_00012",
        ",".join(f"85191_0420_0001{i}" for i in range(5)),
        "85191_0420_00012,46.67;-1.43",
        "85191_0420_00012,85191_0420/00013",
        "85191_0420_00012," + "8" * 300,
    ],
)
def test_malformed_requests_get_a_constant_problem(ids):
    with pytest.raises(ErreurApiError) as erreur:
        ids_compares(ids)
    assert erreur.value.code == 422
    assert erreur.value.headers == {"Cache-Control": "no-store"}
    # The typed problem the contract declares (D-15 API IN-01).
    assert erreur.value.type_code == "ids_invalides"
    texte = f"{erreur.value.message} {erreur.value.detail}"
    assert "46.67" not in texte and "85191" not in texte


def test_duplicates_are_dropped_and_the_order_kept():
    assert ids_compares("85191_0601_00001, 85191_0420_00012,85191_0601_00001") == [
        "85191_0601_00001",
        "85191_0420_00012",
    ]
    with pytest.raises(ErreurApiError):
        ids_compares("85191_0601_00001,85191_0601_00001")


# --- Reading the columns with the readers of the page -------------------------------


class _Session:
    def __init__(self, journal):
        self.journal = journal

    async def rollback(self):
        self.journal.append("rollback")


async def test_columns_are_read_identities_upstreams_then_blocks_in_one_transaction(
    monkeypatch,
):
    journal: list[str] = []
    fiches = {
        "85191_0420_00012": _fiche(),
        "85191_0601_00001": _fiche("85191_0601_00001"),
        "35238_1000_00001": _hors_couverture(),
    }

    async def identite(session, ban_id, hosting):
        journal.append(f"identite {ban_id}")
        if ban_id == "85191_9999_00001":
            raise AdresseInconnueError
        fiche = fiches[ban_id]
        vide = fiche.model_copy(update={"reperes": None, "themes": []})
        return vide, PointBan(lon=-1.4, lat=46.6)

    async def amonts(fiche, point, hosting, as_of):
        journal.append(f"amont {fiche.identite.ban_id}")
        return "argiles", "amont"

    async def lieu(session, ban_id, fiche, point, hosting, scope, amonts_lus, as_of):
        assert amonts_lus == ("argiles", "amont")
        journal.append(f"blocs {ban_id}")
        return fiches[ban_id]

    hostings = []

    def hosting_de(keys, scope, departement):
        hostings.append(departement)
        from app.scope.dependencies import RouteHosting

        return RouteHosting({key: None for key in keys})

    async def declarees(session, ban_id, hosting, *, regle):
        # 04.5-08 (R8 « comparateur aligné »): the rule of the page, never the default.
        assert regle == "numero_et_suffixe"
        journal.append(f"declarees {ban_id}")
        return _declarees(valeur=2)

    monkeypatch.setattr(comparaison, "lire_identite", identite)
    monkeypatch.setattr(comparaison, "lire_amonts", amonts)
    monkeypatch.setattr(comparaison, "lire_lieu", lieu)
    monkeypatch.setattr(comparaison, "compte_ventes_adresse", declarees)
    monkeypatch.setattr(comparaison, "route_hosting", hosting_de)

    resultat = await comparaison.comparer_adresses(
        _Session(journal),
        [
            "85191_0420_00012",
            "85191_9999_00001",
            "35238_1000_00001",
            "85191_0601_00001",
        ],
        scope=None,
        as_of=AS_OF,
    )

    # N-07: each column gets the hosting of the departement of its own key.
    assert hostings == ["85", "85", "35", "85"]
    assert journal == [
        "identite 85191_0420_00012",
        "identite 85191_9999_00001",
        "identite 35238_1000_00001",
        "identite 85191_0601_00001",
        # One rollback after the identities, none between the columns: the
        # publication lock of the first guarded block holds for all (N-01).
        "rollback",
        "amont 85191_0420_00012",
        "amont 85191_0601_00001",
        "blocs 85191_0420_00012",
        # C40: the declared sales of a hosted column, in the same transaction.
        "declarees 85191_0420_00012",
        "blocs 85191_0601_00001",
        "declarees 85191_0601_00001",
    ]
    assert resultat.ids_ecartes == ["85191_9999_00001"]
    declarees = _ligne(resultat, "ventes_declarees").cellules
    assert [(c.etat, c.valeur) for c in declarees] == [
        ("value", 2),
        ("out_of_coverage", None),
        ("value", 2),
    ]
    assert [c.ban_id for c in resultat.colonnes] == [
        "85191_0420_00012",
        "35238_1000_00001",
        "85191_0601_00001",
    ]
    assert resultat.colonnes[1].couverture.etat == "hors_couverture"


# --- Designation of a DPE on a compared address (04-11, D-102, R06-09/7) -------------

LILAS = "85191_0420_00012"
LIEU = "85191_0601_00001"


@pytest.mark.parametrize(
    ("designes", "attendu"),
    [
        (None, []),
        ("", []),
        (f"{LILAS}:{ACTUEL}", [(LILAS, ACTUEL)]),
        (f"{LILAS}:{ACTUEL},{LIEU}:{AUTRE}", [(LILAS, ACTUEL), (LIEU, AUTRE)]),
        # An exact duplicate is dropped, as a duplicate key.
        (f"{LILAS}:{ACTUEL}, {LILAS}:{ACTUEL}", [(LILAS, ACTUEL)]),
    ],
)
def test_designations_are_pairs_of_a_compared_key_and_a_number(designes, attendu):
    assert designations_demandees([LILAS, LIEU], designes) == attendu


@pytest.mark.parametrize(
    "designes",
    [
        LILAS,  # no number
        f"{LILAS}:",
        f"{LILAS}:2385e0123456a",  # not the ADEME form
        f"{LILAS}:2385E0123456",
        f"{LILAS}:{ACTUEL}/x",
        f"85191_9999_00001:{ACTUEL}",  # not a compared key
        f":{ACTUEL}",
        # Two designations for one address.
        f"{LILAS}:{ACTUEL},{LILAS}:{AUTRE}",
        ",".join(f"{LILAS}:{ACTUEL}" for _ in range(40)) + "x",
    ],
)
def test_malformed_designations_get_a_constant_problem(designes):
    with pytest.raises(ErreurApiError) as erreur:
        designations_demandees([LILAS, LIEU], designes)
    assert erreur.value.code == 422
    assert erreur.value.headers == {"Cache-Control": "no-store"}
    assert erreur.value.type_code == "designations_invalides"
    texte = f"{erreur.value.message} {erreur.value.detail}"
    assert ACTUEL not in texte and "85191" not in texte


async def test_a_typed_problem_is_rendered_with_its_code_and_path_only():
    """D-15 API IN-01: the 422 is the ``ProblemeAdresse`` the contract declares.

    ``instance`` is the template of the matched route (C60), set in the scope
    as the router does.
    """
    from types import SimpleNamespace

    from starlette.requests import Request

    from app.adresse.schemas import ProblemeAdresse
    from app.common.exceptions import _handle_exception_api

    with pytest.raises(ErreurApiError) as erreur:
        ids_compares("85191_0420_00012")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/adresses/comparaison",
            "query_string": b"ids=85191_0420_00012",
            "headers": [],
            "route": SimpleNamespace(path="/v1/adresses/comparaison"),
        }
    )
    reponse = await _handle_exception_api(request, erreur.value)

    corps = json.loads(reponse.body)
    assert corps["code"] == "ids_invalides"
    assert corps["instance"] == "/v1/adresses/comparaison"
    assert "85191" not in reponse.body.decode()
    assert reponse.headers["cache-control"] == "no-store"
    ProblemeAdresse.model_validate(corps)


def _designee(document) -> comparaison.Designee:
    return comparaison.Designee(document.numero, document=document)


def _lignes_designees(resultat: ComparaisonAdresses) -> dict:
    documents = next(g for g in resultat.groupes if g.cle == "documents")
    return {
        ligne.cle.removeprefix("designe_"): ligne
        for ligne in documents.lignes
        if ligne.designe
    }


def _servi(cellule) -> dict:
    """What both comparators serve for a cell, the column key aside."""
    return cellule.model_dump(
        include={
            "field_id",
            "etat",
            "motif",
            "valeur",
            "unite",
            "periode",
            "rattachement",
            "ecart",
            "motif_non_calcul",
            "verdict",
            "identique_a_la_reference",
        }
    )


async def _replaced():
    document = await _document(AUTRE)
    statut = document.statut.model_copy(update={"code": "remplace"})
    return document.model_copy(update={"statut": statut})


@pytest.mark.parametrize(
    "couple",
    ["ancienne_actuelle", "methode_inconnue", "remplace", "champ_absent"],
)
async def test_the_same_couple_gets_the_same_rows_directly_or_designated(couple):
    """R06-09/7: one projection, so the same values, gaps and reasons."""
    documents = {
        "ancienne_actuelle": lambda: _pair(_ancien(), _document()),
        "methode_inconnue": lambda: _pair(
            _document(), _document(AUTRE, date_etablissement_dpe=None)
        ),
        "remplace": lambda: _pair(_document(), _replaced()),
        "champ_absent": lambda: _pair(
            _document(type_energie_principale_chauffage=None),
            _document(AUTRE, surface_habitable=None, etiquette_dpe=None),
        ),
    }
    premier, second = await documents[couple]()

    directe = comparaison_public.construire_comparaison([premier, second])
    designee = construire_comparaison(
        [_fiche(LILAS), _fiche(LIEU, voie=("1", "Rue du Lieu", "85000"))],
        designations={LILAS: _designee(premier), LIEU: _designee(second)},
    )

    lignes = _lignes_designees(designee)
    attendues = {
        ligne.cle: ligne for groupe in directe.groupes for ligne in groupe.lignes
    }
    assert list(lignes) == list(attendues)
    for cle, ligne in attendues.items():
        assert lignes[cle].identiques == ligne.identiques, cle
        assert lignes[cle].field_id == ligne.field_id, cle
        for servie, attendue in zip(lignes[cle].cellules, ligne.cellules, strict=True):
            assert _servi(servie) == _servi(attendue), (couple, cle)
    if couple == "ancienne_actuelle":
        classe = lignes["classe_energie"].cellules[1]
        assert classe.motif_non_calcul == "methodes_differentes"
        assert (classe.ecart, classe.verdict) == (None, None)
    if couple == "methode_inconnue":
        motif = lignes["classe_energie"].cellules[1].motif_non_calcul
        assert motif == "methode_indeterminee"


async def _pair(premier, second):
    return await premier, await second


async def test_designated_rows_follow_the_documents_rows_and_name_their_column():
    document = _adresse(await _ancien(), "publiee", "12 rue des Lilas")
    resultat = construire_comparaison(
        [_fiche(LILAS), _fiche(LIEU, voie=("1", "Rue du Lieu", "85000"))],
        designations={LILAS: _designee(document)},
    )

    documents = resultat.groupes[0]
    assert [g.cle for g in resultat.groupes] == GROUPES
    cles = [ligne.cle for ligne in documents.lignes]
    assert cles[:2] == ["dpe_declares", "dpe_plus_recent"]
    assert [ligne.designe for ligne in documents.lignes[:2]] == [False, False]
    # The rows of the document comparator, in its order (R5, plan 04.5-13: the
    # per-m² measures first, the yearly totals after them).
    assert cles[2:] == [
        f"designe_{cle}"
        for cle in (
            "classe_energie",
            "consommation_m2",
            "classe_climat",
            "emissions_m2",
            "consommation",
            "emissions",
            "type_batiment",
            "surface",
            "periode_construction",
            "energie_chauffage",
            "cout_annuel",
            "etabli_le",
            "valable_jusqu_au",
            "methode",
            "adresse_declaree",
            "adresse_ban",
        )
    ]
    # Only « Documents à l'adresse » carries them.
    for groupe in resultat.groupes[1:]:
        assert not any(ligne.designe for ligne in groupe.lignes)
    designation, sans = (c.designation for c in resultat.colonnes)
    assert designation.model_dump() == {
        "numero": ANCIEN,
        "etat": "designe",
        "motif": None,
        "classe_energie": "D",
        "date_etablissement": dt.date(2020, 5, 4),
    }
    assert sans is None
    # The column without designation: not a datum, its own absence.
    for ligne in documents.lignes[2:]:
        cellule = ligne.cellules[1]
        assert (cellule.etat, cellule.motif) == ("not_applicable", "aucun_dpe_designe")
        assert cellule.motif_non_calcul == "donnee_absente"
        assert ligne.identiques is False
    assert resultat.designations_ecartees == []


async def test_no_designation_no_designated_row():
    resultat = construire_comparaison([_fiche(LILAS), _fiche(LIEU)])

    assert not any(ligne.designe for g in resultat.groupes for ligne in g.lignes)
    assert [c.designation for c in resultat.colonnes] == [None, None]
    # Two columns without designation stay identical (D-116) when both have one
    # row each: not applicable here, nothing is added.
    assert len(resultat.groupes[0].lignes) == 2


async def test_two_columns_without_designation_are_identical_on_designated_rows():
    trois = construire_comparaison(
        [_fiche(LILAS), _fiche(LIEU), _fiche("85191_0700_00001")],
        designations={"85191_0700_00001": _designee(await _document())},
    )

    classe = _lignes_designees(trois)["classe_energie"]
    assert classe.cellules[1].identique_a_la_reference is True
    assert classe.identiques is False


async def test_a_designation_out_of_coverage_or_unread_keeps_its_pair():
    resultat = construire_comparaison(
        [_fiche(LILAS), _hors_couverture()],
        designations={
            LILAS: comparaison.Designee(
                ACTUEL, etat="temporary_error", motif="publication_non_validee"
            ),
            "35238_1000_00001": comparaison.Designee(
                AUTRE, etat="out_of_coverage", motif="dataset_non_heberge"
            ),
        },
    )

    panne, hors = (c.designation for c in resultat.colonnes)
    assert (panne.numero, panne.etat, panne.classe_energie) == (
        ACTUEL,
        "temporary_error",
        None,
    )
    assert (hors.numero, hors.etat) == (AUTRE, "out_of_coverage")
    for ligne in _lignes_designees(resultat).values():
        reference, autre = ligne.cellules
        assert (reference.etat, reference.motif) == (
            "temporary_error",
            "publication_non_validee",
        )
        assert autre.etat == "out_of_coverage"
        assert autre.motif_non_calcul == "adresse_hors_couverture"
    assert resultat.designations_ecartees == []


def test_a_set_aside_pair_is_named_and_gets_no_row():
    resultat = construire_comparaison(
        [_fiche(LILAS), _fiche(LIEU)], designations_ecartees=[(LIEU, AUTRE)]
    )

    assert [d.model_dump() for d in resultat.designations_ecartees] == [
        {"ban_id": LIEU, "numero": AUTRE}
    ]
    assert not any(ligne.designe for g in resultat.groupes for ligne in g.lignes)


async def test_a_masked_declared_address_of_a_designated_document_never_leaks():
    masquee = _adresse(
        await _document(adresse_brut="12 rue des Lilas SENTINELLE-0411"),
        "masquee",
        motif="complement_present",
    )

    texte = construire_comparaison(
        [_fiche(LILAS), _fiche(LIEU)], designations={LILAS: _designee(masquee)}
    ).model_dump_json()

    assert "SENTINELLE" not in texte
    adresse = _lignes_designees(
        construire_comparaison(
            [_fiche(LILAS), _fiche(LIEU)], designations={LILAS: _designee(masquee)}
        )
    )["adresse_declaree"].cellules[0]
    assert (adresse.etat, adresse.valeur, adresse.motif) == (
        "suppressed",
        None,
        "complement_present",
    )


class _Imbrique:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _SessionDesignation:
    """No database: the readers of the designation are replaced in the test."""

    def begin_nested(self):
        return _Imbrique()


@pytest.fixture
def _publication_validee(monkeypatch):
    """The guard of the DPE block lets the read through (tested on PostGIS)."""

    async def guard(session, sources):
        assert list(sources) == ["dpe"]

    monkeypatch.setattr(blocs, "guard_publication", guard)


def _colonnes_lues(*fiches: FicheAdresse, dpe_heberge: bool = True) -> dict:
    dpe = (
        DatasetHosting(etat="heberge", departements=["85"])
        if dpe_heberge
        else DatasetHosting(etat="hors_couverture", motif="dataset_non_heberge")
    )
    hosting = RouteHosting({"dpe": dpe})
    return {fiche.identite.ban_id: (fiche, hosting) for fiche in fiches}


async def test_only_a_number_declared_at_the_address_is_kept(
    monkeypatch, _publication_validee
):
    declares = {(LILAS, ACTUEL)}
    lus: list[str] = []

    async def declare(session, ban_id, numero):
        return (ban_id, numero) in declares

    async def lire(session, numero, hosting, as_of):
        lus.append(numero)
        return await _document(numero)

    monkeypatch.setattr(comparaison, "_declare_a_l_adresse", declare)
    monkeypatch.setattr(comparaison, "lire_document_public", lire)

    gardees, ecartees = await comparaison.valider_designations(
        _SessionDesignation(),
        # AUTRE is declared at another address: set aside, never read.
        [(LILAS, ACTUEL), (LIEU, AUTRE), ("85191_9999_00001", ACTUEL)],
        _colonnes_lues(_fiche(LILAS), _fiche(LIEU)),
        AS_OF,
    )

    assert list(gardees) == [LILAS]
    assert gardees[LILAS].document.numero == ACTUEL
    assert ecartees == [(LIEU, AUTRE)]
    assert lus == [ACTUEL]


@pytest.mark.parametrize("panne", ["publication_non_validee", "lecture_indisponible"])
async def test_an_unread_dpe_block_keeps_the_pair_never_not_declared(
    monkeypatch, panne
):
    async def bloc(session, sources, lecteur):
        assert list(sources) == ["dpe"]
        return Bloc(panne=panne)

    monkeypatch.setattr(comparaison, "lire_bloc", bloc)

    gardees, ecartees = await comparaison.valider_designations(
        _SessionDesignation(),
        [(LILAS, ACTUEL)],
        _colonnes_lues(_fiche(LILAS)),
        AS_OF,
    )

    assert ecartees == []
    assert (gardees[LILAS].etat, gardees[LILAS].motif) == ("temporary_error", panne)
    assert gardees[LILAS].document is None


async def test_a_number_unreadable_by_the_document_reader_is_an_outage(
    monkeypatch, _publication_validee
):
    async def declare(session, ban_id, numero):
        return True

    async def lire(session, numero, hosting, as_of):
        raise document_public.DocumentIndisponibleError

    monkeypatch.setattr(comparaison, "_declare_a_l_adresse", declare)
    monkeypatch.setattr(comparaison, "lire_document_public", lire)

    gardees, ecartees = await comparaison.valider_designations(
        _SessionDesignation(),
        [(LILAS, ACTUEL)],
        _colonnes_lues(_fiche(LILAS)),
        AS_OF,
    )

    assert ecartees == []
    assert gardees[LILAS].etat == "temporary_error"


async def test_an_unreadable_designation_is_logged_bounded(
    monkeypatch, caplog, _publication_validee
):
    """D-15 API IN-10: the same bounded event as a block, never a key or number."""

    async def declare(session, ban_id, numero):
        return True

    async def lire(session, numero, hosting, as_of):
        raise document_public.DocumentIndisponibleError

    monkeypatch.setattr(comparaison, "_declare_a_l_adresse", declare)
    monkeypatch.setattr(comparaison, "lire_document_public", lire)

    with caplog.at_level("WARNING", logger="app.adresse.comparaison"):
        await comparaison.valider_designations(
            _SessionDesignation(),
            [(LILAS, ACTUEL)],
            _colonnes_lues(_fiche(LILAS)),
            AS_OF,
        )

    evenements = [
        r for r in caplog.records if r.getMessage() == "address_block_unavailable"
    ]
    assert [(r.dataset, r.outcome) for r in evenements] == [
        ("dpe", "lecture_indisponible")
    ]
    assert evenements[0].error_code == "DocumentIndisponibleError"
    trace = str(vars(evenements[0]))
    assert LILAS not in trace and ACTUEL not in trace


async def test_a_dpe_out_of_coverage_keeps_the_pair_without_any_read(monkeypatch):
    async def interdit(*args, **kwargs):
        raise AssertionError("nothing is read for a DPE out of coverage")

    monkeypatch.setattr(comparaison, "lire_bloc", interdit)

    gardees, ecartees = await comparaison.valider_designations(
        _SessionDesignation(),
        [(LILAS, ACTUEL)],
        _colonnes_lues(_fiche(LILAS), dpe_heberge=False),
        AS_OF,
    )

    assert ecartees == []
    assert (gardees[LILAS].etat, gardees[LILAS].motif) == (
        "out_of_coverage",
        "dataset_non_heberge",
    )


async def test_designations_are_read_after_every_column_in_the_same_transaction(
    monkeypatch,
):
    journal: list[str] = []

    async def identite(session, ban_id, hosting):
        vide = _fiche(ban_id).model_copy(update={"reperes": None, "themes": []})
        return vide, PointBan(lon=-1.4, lat=46.6)

    async def amonts(fiche, point, hosting, as_of):
        return "argiles", "amont"

    async def lieu(session, ban_id, fiche, point, hosting, scope, amonts_lus, as_of):
        journal.append(f"blocs {ban_id}")
        return _fiche(ban_id)

    async def valider(session, paires, colonnes, as_of):
        journal.append(f"designations {list(colonnes)}")
        assert paires == [(LIEU, AUTRE)]
        return {LIEU: _designee(await _document(AUTRE))}, []

    def hosting_de(keys, scope, departement):
        return RouteHosting({key: None for key in keys})

    async def declarees(session, ban_id, hosting, *, regle):
        # 04.5-08 (R8 « comparateur aligné »): the rule of the page, never the default.
        assert regle == "numero_et_suffixe"
        journal.append(f"declarees {ban_id}")
        return _declarees(valeur=2)

    monkeypatch.setattr(comparaison, "lire_identite", identite)
    monkeypatch.setattr(comparaison, "lire_amonts", amonts)
    monkeypatch.setattr(comparaison, "lire_lieu", lieu)
    monkeypatch.setattr(comparaison, "compte_ventes_adresse", declarees)
    monkeypatch.setattr(comparaison, "route_hosting", hosting_de)
    monkeypatch.setattr(comparaison, "valider_designations", valider)

    resultat = await comparaison.comparer_adresses(
        _Session(journal), [LILAS, LIEU], None, AS_OF, [(LIEU, AUTRE)]
    )

    assert journal == [
        "rollback",
        f"blocs {LILAS}",
        f"declarees {LILAS}",
        f"blocs {LIEU}",
        f"declarees {LIEU}",
        f"designations {[LILAS, LIEU]}",
    ]
    assert [c.designation and c.designation.numero for c in resultat.colonnes] == [
        None,
        AUTRE,
    ]


# --- Frozen responses replayed by the frontend ---------------------------------------

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "src"
    / "lib"
    / "data"
    / "__fixtures__"
    / "adresse-comparaison.json"
)


def _annexes(**comptes: ValeurLieu) -> dict:
    """The declared sales read per column (C40), by address key."""
    cles = {"lilas": "85191_0420_00012", "autre": "85191_0601_00001"}
    return {cles[nom]: {FIELD_DECLAREES: valeur} for nom, valeur in comptes.items()}


async def _reponses_figees() -> dict:
    autre = "85191_0601_00001"
    # C40: two equal counts (C41 « Identique »), no sale attached (C44), a suffix.
    egales = _annexes(lilas=_declarees(valeur=3), autre=_declarees(valeur=3))
    zero = _annexes(
        lilas=_declarees(valeur=1),
        autre=_declarees(etat="not_available", motif="aucune_vente_rattachee"),
    )
    egalite = _selection(
        etat="egalite",
        document=None,
        documents_ex_aequo=[
            DocumentSelectionne(numero="2485E0000000A", etiquette_dpe="C"),
            DocumentSelectionne(numero="2485E0000000B", etiquette_dpe="E"),
        ],
    )
    cas = {
        "deux_adresses": construire_comparaison(
            [
                _fiche(
                    contenance=1412,
                    ventes={"etat": "value", "valeur": 3, "periode": "2021-2025"},
                ),
                _fiche(
                    autre,
                    voie=("1", "Rue du Lieu", "85000"),
                    selection=_selection(motif="expire"),
                    argiles="Exposition forte",
                    contenance=1000,
                    ventes={"etat": "not_available", "motif": "point_hors_parcelle"},
                    arrets=(300,),
                    zone=("UA",),
                ),
            ],
            annexes=egales,
        ),
        "trois_dont_hors_couverture": construire_comparaison(
            [
                _fiche(),
                _fiche(autre, voie=("12", "Rue des Lilas", "85100"), selection=egalite),
                _hors_couverture(),
            ],
            ids_ecartes=["85191_9999_00001"],
            annexes=_annexes(
                lilas=_declarees(valeur=2),
                autre=_declarees(
                    etat="not_available", motif="suffixe_non_distingue", periode=None
                ),
            ),
        ),
        # 04-11: the two documents of `dpe-comparaison.json` « ancien_et_actuel »,
        # designated on two addresses (parity of both comparators, e2e).
        "deux_designations": construire_comparaison(
            [_fiche(), _fiche(autre, voie=("1", "Rue du Lieu", "85000"))],
            designations={
                "85191_0420_00012": _designee(
                    _adresse(await _ancien(), "publiee", "12 rue des Lilas")
                ),
                autre: _designee(
                    _adresse(
                        await _document(
                            type_energie_principale_chauffage=None,
                            cout_total_5_usages=Decimal("2034.2"),
                        ),
                        "masquee",
                        motif="complement_present",
                    )
                ),
            },
            annexes=zero,
        ),
        "une_designation": construire_comparaison(
            [_fiche(), _fiche(autre, voie=("1", "Rue du Lieu", "85000"))],
            designations={"85191_0420_00012": _designee(await _ancien())},
            annexes=zero,
        ),
        "une_designation_une_ecartee": construire_comparaison(
            [_fiche(), _fiche(autre, voie=("1", "Rue du Lieu", "85000"))],
            designations={"85191_0420_00012": _designee(await _ancien())},
            designations_ecartees=[(autre, "2485E0000099Z")],
            annexes=zero,
        ),
        "designation_en_panne": construire_comparaison(
            [_fiche(), _fiche(autre, voie=("1", "Rue du Lieu", "85000"))],
            designations={
                autre: comparaison.Designee(
                    ACTUEL, etat="temporary_error", motif="publication_non_validee"
                )
            },
            annexes=zero,
        ),
    }
    return {
        cle: {"statut": "ok", "donnees": resultat.model_dump(mode="json"), "meta": None}
        for cle, resultat in cas.items()
    }


async def test_frozen_responses_for_the_frontend_are_the_served_shape():
    """The frontend replays these responses: they are what the rule serves.

    Written to the fixture when ``ADRESSE_COMPARAISON_FIXTURE_OUTPUT=1``.
    """
    reponses = await _reponses_figees()
    if os.environ.get("ADRESSE_COMPARAISON_FIXTURE_OUTPUT") == "1":
        texte = json.dumps(reponses, ensure_ascii=False, indent="\t") + "\n"
        FIXTURE.write_bytes(texte.replace("\n", "\r\n").encode("utf-8"))
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == reponses


def test_every_reason_of_the_rule_is_a_served_reason():
    """D-15 API IN-07: a reason added to ``compare_cell`` and not to the served
    ``Literal`` would turn the whole comparison into a 500; caught here."""
    from typing import get_args

    from app.adresse.schemas import MotifNonCalculAdresse
    from app.commune.comparison import MotifDocument, MotifNonCalcul
    from app.dpe.comparaison_public import MotifNonCalculDocument

    regle = (set(get_args(MotifNonCalcul)) - {"commune_hors_couverture"}) | set(
        get_args(MotifDocument)
    )
    adresse = regle | {"adresse_hors_couverture", comparaison.MOTIF_LISTE_INCOMPLETE}
    assert adresse <= set(get_args(MotifNonCalculAdresse))
    assert regle <= set(get_args(MotifNonCalculDocument))


def _arrets_partiels(fiche: FicheAdresse, motif: str | None) -> FicheAdresse:
    themes = []
    for theme in fiche.themes:
        if theme.cle == "reseaux_transports":
            groupes = [
                groupe.model_copy(
                    update={
                        "champs": [
                            champ.model_copy(
                                update={"liste_incomplete": True, "motif": motif}
                            )
                            if champ.field_id == "adresse.transports.arret_proche"
                            else champ
                            for champ in groupe.champs
                        ]
                    }
                )
                for groupe in theme.groupes
            ]
            theme = theme.model_copy(update={"groupes": groupes})
        themes.append(theme)
    return fiche.model_copy(update={"themes": themes})


def test_a_nearest_stop_read_across_a_departement_not_hosted_gets_no_verdict():
    """C16: a stop beyond the hosted departements may be nearer, no gap."""
    hors = "territoire_hors_perimetre"
    autre = "85191_0601_00001"
    for premiere, seconde in (
        (_fiche(arrets=(100,)), _arrets_partiels(_fiche(autre, arrets=(300,)), hors)),
        (_arrets_partiels(_fiche(arrets=(100,)), hors), _fiche(autre, arrets=(300,))),
    ):
        ligne = _ligne(construire_comparaison([premiere, seconde]), "arret_proche")
        cellule = ligne.cellules[1]
        assert (cellule.verdict, cellule.ecart) == (None, None)
        assert cellule.motif_non_calcul == "liste_incomplete"
        assert ligne.identiques is False
    # The three nearest of a longer list (API IN-01): the nearest stays known.
    plafond = construire_comparaison(
        [_fiche(arrets=(100,)), _arrets_partiels(_fiche(autre, arrets=(300,)), None)]
    )
    cellule = _ligne(plafond, "arret_proche").cellules[1]
    assert cellule.ecart.model_dump() == {"valeur": 200, "unite": "metre"}
