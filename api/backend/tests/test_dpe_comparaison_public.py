"""DPE documents compared by their source number, without a database (04-10).

``GET /v1/dpe/comparaison`` reads each document with the reader of the
document page and compares them with the 03-09 rule through the single
documentary projection: a document of before July 2021 and one of after get
no gap nor verdict on their classes, consumptions and emissions
(``methodes_differentes``); an unknown method gives none either; a masked
declared address is never compared nor served; two absences of the same
state and reason stay identical (D-116, RES-4). The problems are constant and
the static path is never read as a number (CMP-02, T-04-10-01 to 04).
"""

import json
import logging
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import OperationalError

from app.adresse.schemas import AdresseDeclaree
from app.commune.comparison import METHODE_INCONNUE
from app.dpe import comparaison_public, comparison_projection, document_public
from app.dpe import router as dpe_router
from tests.test_dpe_document_public import _hosting, _proprietes, _row, _Session

pytestmark = pytest.mark.unit

ACTUEL = "2385E0123456A"
ANCIEN = "2185E0000001A"
AUTRE = "2485E0000003C"
AS_OF = date(2026, 10, 5)
SENTINELLE = "SENTINELLE-BRUT-0410"
GROUPES = ["etiquettes", "logement", "cout_validite", "adresse"]
SENSIBLES = (
    "classe_energie",
    "consommation_m2",
    "classe_climat",
    "emissions_m2",
    "consommation",
    "emissions",
)
# R5 (plan 04.5-13): the per-m² measures at first level, the yearly totals kept
# after them under their own fields (a row never changes value under its field).
PAR_M2 = {
    "consommation_m2": "dpe.document.conso_ep_m2",
    "emissions_m2": "dpe.document.emissions_ges_m2",
}
TOTAUX = {
    "consommation": "dpe.document.conso_ep",
    "emissions": "dpe.document.emissions_ges",
}


@pytest.fixture(autouse=True)
def _sans_provenance(monkeypatch):
    monkeypatch.setattr(
        document_public, "date_import_dpe", AsyncMock(return_value=date(2026, 9, 30))
    )


async def _document(numero: str = ACTUEL, **over) -> document_public.DocumentDpePublic:
    return await document_public.lire_document_public(
        _Session([_row(numero_dpe=numero, **over)]), numero, _hosting(), AS_OF
    )


async def _ancien(**over) -> document_public.DocumentDpePublic:
    return await _document(
        ANCIEN,
        date_etablissement_dpe=date(2020, 5, 4),
        date_fin_validite_dpe=date(2030, 5, 3),
        **over,
    )


def _lignes(comparaison: comparaison_public.ComparaisonDocuments) -> dict:
    return {
        ligne.cle: ligne for groupe in comparaison.groupes for ligne in groupe.lignes
    }


# --- Four groups, the method of calculation (R06-09/7, task 6) ---------------------


async def test_four_groups_in_order_one_cell_per_column():
    comparaison = comparaison_public.construire_comparaison(
        [await _document(), await _document(AUTRE, etiquette_dpe="C")]
    )

    assert [g.cle for g in comparaison.groupes] == GROUPES
    assert [g.titre for g in comparaison.groupes] == [
        "Étiquettes",
        "Logement décrit",
        "Coût et validité",
        "Adresse",
    ]
    assert [[ligne.cle for ligne in g.lignes] for g in comparaison.groupes] == [
        list(SENSIBLES),
        ["type_batiment", "surface", "periode_construction", "energie_chauffage"],
        ["cout_annuel", "etabli_le", "valable_jusqu_au", "methode"],
        ["adresse_declaree", "adresse_ban"],
    ]
    for ligne in _lignes(comparaison).values():
        assert [c.numero for c in ligne.cellules] == [ACTUEL, AUTRE]
        assert ligne.cellules[0].motif_non_calcul is None
        assert ligne.cellules[0].identique_a_la_reference is None
    assert [c.numero for c in comparaison.colonnes] == [ACTUEL, AUTRE]


async def test_old_and_current_methods_get_no_gap_on_classes_nor_figures():
    comparaison = comparaison_public.construire_comparaison(
        [await _ancien(), await _document()]
    )
    lignes = _lignes(comparaison)

    for cle in SENSIBLES:
        cellule = lignes[cle].cellules[1]
        assert cellule.motif_non_calcul == "methodes_differentes", cle
        assert (cellule.ecart, cellule.verdict) == (None, None), cle
        # Same class D on both sides: never « the same » across two methods.
        assert cellule.identique_a_la_reference is False, cle
        assert lignes[cle].identiques is False, cle
    # The type does not depend on the method: a verdict, never a figure.
    type_batiment = lignes["type_batiment"].cellules[1]
    assert (type_batiment.verdict, type_batiment.ecart) == ("identique", None)
    # The method row itself says the two methods.
    methode = lignes["methode"].cellules
    assert [c.valeur for c in methode] == ["ancienne", "actuelle"]
    assert methode[1].verdict == "different"
    assert [c.methode for c in comparaison.colonnes] == ["ancienne", "actuelle"]


async def test_same_method_serves_the_gap_of_the_yearly_totals_as_declared():
    comparaison = comparaison_public.construire_comparaison(
        [
            await _document(),
            await _document(
                AUTRE,
                etiquette_dpe="C",
                conso_5_usages_ep=Decimal("12000.4"),
                surface_habitable=Decimal("120.0"),
            ),
        ]
    )
    lignes = _lignes(comparaison)

    assert lignes["classe_energie"].cellules[1].verdict == "different"
    assert lignes["classe_climat"].cellules[1].verdict == "identique"
    consommation = lignes["consommation"].cellules
    # Yearly totals with their true unit: never divided by the surface.
    assert [c.valeur for c in consommation] == [17830.4, 12000.4]
    assert consommation[1].unite == "kwh_ep_per_year"
    assert consommation[1].ecart.model_dump() == {
        "valeur": -5830,
        "unite": "kwh_ep_per_year",
    }
    assert lignes["surface"].cellules[1].ecart.model_dump() == {
        "valeur": 41.0,
        "unite": "m2",
    }


# --- Per-m² measures at first level, totals after them (R5, plan 04.5-13) ---------


async def test_the_per_m2_rows_read_the_published_measure_under_their_own_field():
    lignes = _lignes(
        comparaison_public.construire_comparaison(
            [
                await _document(),
                await _document(
                    AUTRE,
                    conso_5_usages_ep=Decimal("12000.4"),
                    surface_habitable=Decimal("120.0"),
                    conso_5_usages_par_m2_ep=Decimal("158"),
                    emission_ges_5_usages_par_m2=Decimal("12.3"),
                ),
            ]
        )
    )

    assert {cle: lignes[cle].field_id for cle in (*PAR_M2, *TOTAUX)} == {
        **PAR_M2,
        **TOTAUX,
    }
    # The published measures, never total / surface (225.7 and 100.0 here).
    consommation = lignes["consommation_m2"].cellules
    assert [(c.field_id, c.valeur, c.unite) for c in consommation] == [
        ("dpe.document.conso_ep_m2", 231.0, "kwh_ep_per_m2_per_year"),
        ("dpe.document.conso_ep_m2", 158.0, "kwh_ep_per_m2_per_year"),
    ]
    assert consommation[1].ecart.model_dump() == {
        "valeur": -73,
        "unite": "kwh_ep_per_m2_per_year",
    }
    emissions = lignes["emissions_m2"].cellules
    assert [(c.field_id, c.valeur, c.unite) for c in emissions] == [
        ("dpe.document.emissions_ges_m2", 47.5, "kg_co2_eq_per_m2_per_year"),
        ("dpe.document.emissions_ges_m2", 12.3, "kg_co2_eq_per_m2_per_year"),
    ]
    assert emissions[1].ecart.unite == "kg_co2_eq_per_m2_per_year"
    # The yearly totals stay served, unchanged, under their own fields.
    assert [c.valeur for c in lignes["consommation"].cellules] == [17830.4, 12000.4]
    assert [c.valeur for c in lignes["emissions"].cellules] == [3433.1, 3433.1]
    assert lignes["emissions"].cellules[1].unite == "kg_co2_eq_per_year"


async def test_a_published_zero_per_m2_is_a_value_never_an_absence():
    lignes = _lignes(
        comparaison_public.construire_comparaison(
            [
                await _document(),
                await _document(
                    AUTRE,
                    conso_5_usages_par_m2_ep=Decimal("0"),
                    emission_ges_5_usages_par_m2=Decimal("0"),
                ),
            ]
        )
    )

    for cle in PAR_M2:
        cellule = lignes[cle].cellules[1]
        assert (cellule.etat, cellule.valeur, cellule.motif) == ("value", 0.0, None)
        assert cellule.ecart is not None, cle
    assert lignes["consommation_m2"].cellules[1].ecart.valeur == -231


async def test_total_and_surface_without_per_m2_value_is_not_reported():
    """D45-05: no quotient and no fallback to the total, even with both served."""
    sans = await _document(
        AUTRE, conso_5_usages_par_m2_ep=None, emission_ges_5_usages_par_m2=None
    )
    lignes = _lignes(
        comparaison_public.construire_comparaison([await _document(), sans])
    )

    # Both totals and the surface are served for that document.
    assert lignes["consommation"].cellules[1].valeur == 17830.4
    assert lignes["emissions"].cellules[1].valeur == 3433.1
    assert lignes["surface"].cellules[1].valeur == 79.0
    for cle, field_id in PAR_M2.items():
        cellule = lignes[cle].cellules[1]
        assert (cellule.field_id, cellule.etat, cellule.valeur) == (
            field_id,
            "not_reported",
            None,
        ), cle
        assert cellule.motif_non_calcul == "donnee_absente", cle
        assert cellule.ecart is None, cle
        assert lignes[cle].identiques is False, cle


async def test_a_landmark_without_its_per_m2_member_is_an_outage_not_its_total():
    """As on the document page (04.5-12): a member the reader did not serve is an
    outage of its row, never « not reported » (the source did not say so)."""
    document = await _document(AUTRE)
    reperes = document.reperes
    sans_membre = document.model_copy(
        update={
            "reperes": reperes.model_copy(
                update={
                    "etiquette_energie": reperes.etiquette_energie.model_copy(
                        update={"mesure_m2": None}
                    ),
                    "etiquette_climat": reperes.etiquette_climat.model_copy(
                        update={"mesure_m2": None}
                    ),
                }
            )
        }
    )
    lignes = _lignes(
        comparaison_public.construire_comparaison([await _document(), sans_membre])
    )

    for cle, field_id in PAR_M2.items():
        cellule = lignes[cle].cellules[1]
        assert (cellule.field_id, cellule.etat, cellule.valeur) == (
            field_id,
            "temporary_error",
            None,
        ), cle
        assert cellule.ecart is None, cle


@pytest.mark.parametrize(
    "over",
    [
        {"date_etablissement_dpe": None},
        # Inconsistent dates: the cycle reads no validity, hence no method.
        {"date_fin_validite_dpe": date(2020, 1, 1)},
        {"date_etablissement_dpe": date(2027, 1, 1)},
    ],
    ids=["no-establishment-date", "inconsistent-dates", "future-establishment"],
)
async def test_an_unestablished_method_gives_no_verdict(over):
    inconnu = await _document(AUTRE, **over)
    assert comparison_projection.methode_comparable(inconnu) == METHODE_INCONNUE

    lignes = _lignes(
        comparaison_public.construire_comparaison([await _document(), inconnu])
    )

    for cle in SENSIBLES:
        cellule = lignes[cle].cellules[1]
        assert cellule.motif_non_calcul == "methode_indeterminee", cle
        assert cellule.identique_a_la_reference is False, cle
    assert lignes["type_batiment"].cellules[1].verdict == "identique"


# --- D-116 (RES-4): absences and real gaps ---------------------------------------


async def test_two_absences_of_the_same_state_are_identical_a_real_gap_stays():
    lignes = _lignes(
        comparaison_public.construire_comparaison(
            [
                await _document(type_energie_principale_chauffage=None),
                await _document(
                    AUTRE,
                    type_energie_principale_chauffage=None,
                    cout_total_5_usages=Decimal("2034.2"),
                ),
            ]
        )
    )

    chauffage = lignes["energie_chauffage"]
    assert [c.etat for c in chauffage.cellules] == ["not_reported"] * 2
    assert chauffage.cellules[1].motif_non_calcul == "donnee_absente"
    assert chauffage.identiques is True
    cout = lignes["cout_annuel"]
    assert cout.identiques is False
    assert cout.cellules[1].ecart.model_dump() == {
        "valeur": 200,
        "unite": "euro_per_year",
    }


async def test_one_absence_against_a_value_is_not_identical():
    lignes = _lignes(
        comparaison_public.construire_comparaison(
            [await _document(), await _document(AUTRE, etiquette_dpe=None)]
        )
    )

    classe = lignes["classe_energie"]
    assert classe.cellules[1].etat == "not_reported"
    assert classe.cellules[1].motif_non_calcul == "donnee_absente"
    assert classe.identiques is False


# --- Declared address (P-ADR-01, T-04-10-03) --------------------------------------


def _adresse(document, etat, texte=None, motif=None):
    adresse = AdresseDeclaree(etat=etat, texte=texte, motif=motif)
    return document.model_copy(update={"adresse_declaree": adresse})


async def test_declared_addresses_are_compared_between_two_published_texts_only():
    publiee = _adresse(await _document(), "publiee", "12 rue des Lilas")
    autre = _adresse(await _document(AUTRE), "publiee", "14 rue des Lilas")
    masquee = _adresse(
        await _document("2485E0000004C"), "masquee", motif="complement_present"
    )

    lignes = _lignes(
        comparaison_public.construire_comparaison([publiee, autre, masquee])
    )

    adresse = lignes["adresse_declaree"].cellules
    assert [c.etat for c in adresse] == ["value", "value", "suppressed"]
    assert adresse[1].verdict == "different"
    assert adresse[2].motif_non_calcul == "adresse_declaree_non_affichee"
    assert (adresse[2].valeur, adresse[2].motif) == (None, "complement_present")
    assert adresse[2].identique_a_la_reference is False


async def test_two_masked_addresses_are_never_identical_nor_served():
    masquees = [
        _adresse(
            await _document(numero), "masquee", motif="non_verifiable_sans_complement"
        )
        for numero in (ACTUEL, AUTRE)
    ]

    comparaison = comparaison_public.construire_comparaison(masquees)
    ligne = _lignes(comparaison)["adresse_declaree"]

    assert ligne.identiques is False
    assert ligne.cellules[1].motif_non_calcul == "adresse_declaree_non_affichee"
    assert [c.adresse_declaree.texte for c in comparaison.colonnes] == [None, None]


async def test_the_raw_declared_address_never_reaches_the_answer(monkeypatch):
    """The reader projects the raw text; the comparison serves the projection."""
    brut = f"12 rue des Lilas {SENTINELLE}"
    documents = [
        await _document(adresse_brut=brut),
        await _document(
            AUTRE, adresse_brut=brut, complement_adresse_logement="porte 3"
        ),
    ]

    texte = comparaison_public.construire_comparaison(documents).model_dump_json()

    assert SENTINELLE not in texte
    assert "porte 3" not in texte


# --- Columns kept, nothing attributed ---------------------------------------------


async def test_a_retired_document_keeps_its_column_every_row_saying_so():
    retire = await _document(
        AUTRE, is_tombstone=True, source_state="absent_from_complete_scope"
    )
    assert retire.disponibilite == "retire"

    comparaison = comparaison_public.construire_comparaison([await _document(), retire])

    assert comparaison.colonnes[1].statut == "retire"
    for ligne in _lignes(comparaison).values():
        cellule = ligne.cellules[1]
        assert (cellule.etat, cellule.motif) == ("not_reported", "document_retire")
        assert cellule.motif_non_calcul == "donnee_absente"


def test_no_dwelling_sale_or_commune_datum_in_the_schema():
    noms = _proprietes(comparaison_public.ComparaisonDocuments)

    for interdit in ("logement", "vente", "mutation", "prix", "dvf", "synthese"):
        assert not [nom for nom in noms if interdit in nom], interdit
    assert not noms & {
        "adresse_brut",
        "adresse_complete_brut",
        "complement_adresse_logement",
        "identifiant_ban",
    }


# --- HTTP: route order, constant problems, no-store ------------------------------


@pytest.mark.parametrize(
    "numeros",
    [
        None,
        ACTUEL,
        f"{ACTUEL},{ACTUEL}",
        ",".join([ACTUEL, ANCIEN, AUTRE, "2485E0000004C", "2485E0000005K"]),
        f"{ACTUEL},2385e0123456a",
        f"{ACTUEL},<script>",
        f"{ACTUEL},{'9' * 300}",
    ],
    ids=["absent", "one", "duplicate-only", "five", "lowercase", "markup", "long"],
)
async def test_bad_requests_are_a_constant_422_before_any_read(
    client, monkeypatch, numeros
):
    lecture = AsyncMock()
    monkeypatch.setattr(comparaison_public, "lire_document_public", lecture)

    params = {} if numeros is None else {"numeros": numeros}
    response = await client.get("/v1/dpe/comparaison", params=params)

    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    # The static path is never read as a number (« numero_mal_forme »).
    assert body["code"] == "numeros_invalides"
    assert body["instance"] == "/v1/dpe/comparaison"
    assert ACTUEL not in response.text and "script" not in response.text
    lecture.assert_not_awaited()


def test_four_numbers_separated_by_a_comma_and_a_space_are_read():
    """D-15 API IN-09: the length is bounded, the spaces after commas tolerated."""
    quatre = [ACTUEL, ANCIEN, AUTRE, "2485E0000004C"]

    assert dpe_router.numeros_compares(", ".join(quatre)) == quatre
    assert dpe_router.numeros_compares(" , ".join(quatre)) == quatre
    assert dpe_router.numeros_compares(", ".join([*quatre, "9" * 40])) is None


async def test_the_number_route_still_serves_a_document(client, monkeypatch):
    document = await _document()
    monkeypatch.setattr(
        dpe_router, "lire_document_public", AsyncMock(return_value=document)
    )

    response = await client.get(f"/v1/dpe/{ACTUEL}")

    assert response.status_code == 200, response.text
    assert response.json()["donnees"]["numero"] == ACTUEL


async def test_unknown_numbers_are_named_and_get_no_column(client, monkeypatch):
    connu = await _document()

    async def lire(_session, numero, _hosting, _as_of):
        if numero != ACTUEL:
            raise document_public.DpeInconnuError(["44", "85"])
        return connu

    monkeypatch.setattr(comparaison_public, "lire_document_public", lire)

    response = await client.get(
        "/v1/dpe/comparaison", params={"numeros": f"{ACTUEL},{AUTRE},{ACTUEL}"}
    )

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    donnees = response.json()["donnees"]
    assert [c["numero"] for c in donnees["colonnes"]] == [ACTUEL]
    assert donnees["numeros_ecartes"] == [AUTRE]


async def test_a_failed_read_makes_the_whole_comparison_a_typed_503(
    client, monkeypatch
):
    monkeypatch.setattr(
        comparaison_public,
        "lire_document_public",
        AsyncMock(side_effect=document_public.DocumentIndisponibleError()),
    )

    response = await client.get(
        "/v1/dpe/comparaison", params={"numeros": f"{ACTUEL},{AUTRE}"}
    )

    assert response.status_code == 503
    assert response.json()["code"] == "comparaison_dpe_indisponible"
    assert response.headers["cache-control"] == "no-store"
    assert ACTUEL not in response.text and AUTRE not in response.text


async def test_the_reader_of_the_page_reads_each_number_in_column_order():
    session = _Session(error=OperationalError("SELECT", {}, Exception("down")))
    with pytest.raises(document_public.DocumentIndisponibleError):
        await comparaison_public.comparer_documents(
            session, [ACTUEL, AUTRE], _hosting(), AS_OF
        )
    # The first failed read stops: nothing partial is served.
    assert [params["numero"] for _, params in session.calls] == [ACTUEL]


async def test_no_log_line_carries_a_number(client, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    documents = {ACTUEL: await _document(), AUTRE: await _document(AUTRE)}

    async def lire(_session, numero, _hosting, _as_of):
        return documents[numero]

    monkeypatch.setattr(comparaison_public, "lire_document_public", lire)

    response = await client.get(
        "/v1/dpe/comparaison", params={"numeros": f"{ACTUEL},{AUTRE}"}
    )

    assert response.status_code == 200, response.text
    # Every record of the application, its extra members included (the
    # request log records the route template only, decision n° 26); the
    # records of the test client itself are not the application's.
    lignes = [
        f"{record.getMessage()} {record.__dict__}"
        for record in caplog.records
        if record.name.startswith("app")
    ]
    assert any("/v1/dpe/comparaison" in ligne for ligne in lignes)
    assert not [ligne for ligne in lignes if ACTUEL in ligne or AUTRE in ligne]


async def test_the_publication_guard_covers_the_comparison_never_cached(monkeypatch):
    from fastapi import HTTPException, Request

    from app.common import publication_readiness as readiness

    async def refuse(_session, datasets):
        assert datasets == ["dpe"]
        raise HTTPException(503, "Donnees temporairement indisponibles")

    monkeypatch.setattr(readiness, "guard_publication", refuse)
    request = Request(
        {
            "type": "http",
            "headers": [],
            "path": "/v1/dpe/comparaison",
            "query_string": f"numeros={ACTUEL},{AUTRE}".encode(),
        }
    )

    with pytest.raises(HTTPException) as exc:
        await readiness.require_publication(request, object())

    assert exc.value.status_code == 503
    assert exc.value.headers == {"Cache-Control": "no-store"}


def test_the_static_route_is_declared_before_the_number_route():
    chemins = [route.path for route in dpe_router.router.routes]
    assert chemins.index("/v1/dpe/comparaison") < chemins.index("/v1/dpe/{numero}")


# --- Frozen responses replayed by the frontend -----------------------------------

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "src"
    / "lib"
    / "data"
    / "__fixtures__"
    / "dpe-comparaison.json"
)


async def _reponses_figees() -> dict:
    cas = {
        "ancien_et_actuel": comparaison_public.construire_comparaison(
            [
                _adresse(await _ancien(), "publiee", "12 rue des Lilas"),
                _adresse(
                    await _document(
                        type_energie_principale_chauffage=None,
                        cout_total_5_usages=Decimal("2034.2"),
                    ),
                    "masquee",
                    motif="complement_present",
                ),
            ]
        ),
        "meme_methode_trois": comparaison_public.construire_comparaison(
            [
                _adresse(await _document(), "publiee", "12 rue des Lilas"),
                # R5 (plan 04.5-13): per-m² measures of their own, never the
                # quotient total / surface (151.9 and 43.5 here).
                _adresse(
                    await _document(
                        AUTRE,
                        etiquette_dpe="C",
                        conso_5_usages_ep=Decimal("12000.4"),
                        conso_5_usages_par_m2_ep=Decimal("158"),
                        emission_ges_5_usages_par_m2=Decimal("12.3"),
                    ),
                    "publiee",
                    "12 rue des Lilas",
                ),
                # No published energy per m² (total and surface served), a zero
                # for the climate one.
                await _document(
                    "2485E0000004C",
                    date_etablissement_dpe=None,
                    conso_5_usages_par_m2_ep=None,
                    emission_ges_5_usages_par_m2=Decimal("0"),
                ),
            ],
            numeros_ecartes=["2385E9999999X"],
        ),
    }
    return {
        cle: {"statut": "ok", "donnees": resultat.model_dump(mode="json"), "meta": None}
        for cle, resultat in cas.items()
    }


async def test_frozen_responses_for_the_frontend_are_the_served_shape():
    """The frontend replays these responses: they are what the rule serves.

    Written to the fixture when ``DPE_COMPARAISON_FIXTURE_OUTPUT=1``.
    """
    reponses = await _reponses_figees()
    if os.environ.get("DPE_COMPARAISON_FIXTURE_OUTPUT") == "1":
        texte = json.dumps(reponses, ensure_ascii=False, indent="\t") + "\n"
        FIXTURE.write_bytes(texte.replace("\n", "\r\n").encode("utf-8"))
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == reponses
