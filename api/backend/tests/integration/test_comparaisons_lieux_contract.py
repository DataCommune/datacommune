"""CMP-01 / CMP-02 / D-116 / N-01 / N-02 / N-07: places compared on PostGIS.

04-07: addresses. 04-10: DPE documents by their source number. 04-11: a DPE
designated on a compared address (end of file).

``GET /v1/adresses/comparaison`` serves two to four addresses, four groups of
rows with the commune reference last, each value being the value of the
address page, and the gaps, reasons and verdicts of the 03-09 rule. Each
column keeps its own hosting (N-07) and its own parcel relation (N-02); a
refused publication breaks only the rows of its source, in every column
(N-01); the upstreams are called outside any transaction.

The seed, the scope helper and the fixtures are those of the address page
contract (``test_adresse_contract.py``): the same rows, read by both routes.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_comparaisons_lieux_contract.py
"""

# ruff: noqa: F811 -- the fixtures imported below are test parameters.
import psycopg
import pytest

# Fixtures and seed of the address page contract: imported for pytest.
from tests.integration.test_adresse_contract import (  # noqa: F401
    BAN_CHAUSSEE,
    BAN_EGALITE,
    BAN_FRONTIERE,
    BAN_GLYCINES,
    BAN_LILAS,
    BAN_RETIRE,
    BAN_SANS_VENTE,
    DPE_GLYCINES,
    DPE_LILAS,
    DPE_RETIRE,
    SENTINELLE,
    TOUT,
    _fiche,
    _publish,
    _scope,
    _transactions_et_verrous,
    api,
    georisques,
    migrated,
    mounted,
)

pytestmark = pytest.mark.destructive

ROUTE = "/v1/adresses/comparaison"
GROUPES = ["documents", "ventes", "lieu", "commune_reference"]
HORS_COUVERTURE = "35238_1234_00001"
INCONNUE = "85191_9999_00001"


async def _comparer(client, *ids: str, status: int = 200) -> dict:
    response = await client.get(ROUTE, params={"ids": ",".join(ids)})
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def _lignes(donnees: dict) -> dict[str, dict]:
    return {
        ligne["cle"]: ligne
        for groupe in donnees["groupes"]
        for ligne in groupe["lignes"]
    }


# --- Tracer -------------------------------------------------------------------------


async def test_tracer_two_addresses_four_groups_the_commune_last(
    api, migrated, georisques
):
    _scope(migrated, datasets=TOUT)

    donnees = (await _comparer(api.client, BAN_LILAS, BAN_SANS_VENTE))["donnees"]

    assert [g["cle"] for g in donnees["groupes"]] == GROUPES
    assert donnees["groupes"][-1]["titre"] == "Commune de l'adresse (référence)"
    assert [c["ban_id"] for c in donnees["colonnes"]] == [BAN_LILAS, BAN_SANS_VENTE]
    assert donnees["colonnes"][0]["numero_voie"] == "12 Rue des Lilas"
    assert donnees["ids_ecartes"] == []
    lignes = _lignes(donnees)
    # Served gap of the sales of the parcel: 0 minus 3, counted as the page.
    ventes = lignes["ventes_parcelle"]["cellules"]
    assert [(c["etat"], c["valeur"]) for c in ventes] == [("value", 3), ("value", 0)]
    assert ventes[1]["ecart"] == {"valeur": -3, "unite": "count"}
    # The last sale of a parcel without sale is an absence, never a date.
    derniere = lignes["derniere_vente"]["cellules"][1]
    assert (derniere["etat"], derniere["motif"]) == ("not_available", "aucune_vente")
    assert derniere["motif_non_calcul"] == "donnee_absente"
    # Same commune: the reference values are identical, never a gap of 0
    # attributed to the place.
    reference = lignes["prix_median_maison"]
    assert reference["identiques"] is True
    assert {c["rattachement"] for c in reference["cellules"]} == {"commune_reference"}
    # Clay exposure: a verdict, never a figure.
    argiles = lignes["argiles"]["cellules"][1]
    assert (argiles["verdict"], argiles["ecart"]) == ("identique", None)
    assert "46.6" not in str(donnees) and "-1.4" not in str(donnees)


async def test_every_value_is_the_value_of_the_address_page(api, migrated, georisques):
    _scope(migrated, datasets=TOUT)

    donnees = (await _comparer(api.client, BAN_LILAS, BAN_SANS_VENTE))["donnees"]
    lignes = _lignes(donnees)
    for position, ban_id in enumerate((BAN_LILAS, BAN_SANS_VENTE)):
        reperes = (await _fiche(api, ban_id))["reperes"]
        for cle, servie in (
            ("dpe_declares", reperes["dpe"]),
            ("ventes_parcelle", reperes["ventes"]["nombre"]),
            ("derniere_vente", reperes["ventes"]["derniere"]),
            ("argiles", reperes["argiles"]),
            ("prix_median_maison", reperes["reference_commune"]["maisons"]),
            ("prix_median_appartement", reperes["reference_commune"]["appartements"]),
        ):
            cellule = lignes[cle]["cellules"][position]
            for membre in ("field_id", "etat", "motif", "valeur", "periode"):
                assert cellule[membre] == servie[membre], (ban_id, cle, membre)
            assert cellule["rattachement"] == servie["rattachement"], (ban_id, cle)


# --- Columns, parcel relation, coverage ---------------------------------------------


async def test_four_columns_each_with_its_parcel_relation(api, migrated, georisques):
    _scope(migrated, datasets=TOUT)

    donnees = (
        await _comparer(
            api.client, BAN_LILAS, BAN_CHAUSSEE, BAN_FRONTIERE, BAN_SANS_VENTE
        )
    )["donnees"]

    assert len(donnees["colonnes"]) == 4
    ventes = _lignes(donnees)["ventes_parcelle"]["cellules"]
    # N-02: outside every parcel, on two parcels: typed, never a zero and
    # never the nearest parcel.
    assert [(c["etat"], c["motif"], c["valeur"]) for c in ventes[1:3]] == [
        ("not_available", "point_hors_parcelle", None),
        ("not_available", "parcelles_multiples", None),
    ]
    contenance = _lignes(donnees)["contenance"]["cellules"]
    assert contenance[1]["motif"] == "point_hors_parcelle"
    assert contenance[1]["valeur"] is None


async def test_an_unknown_key_is_named_and_gets_no_column(api, migrated, georisques):
    _scope(migrated, datasets=TOUT)

    donnees = (await _comparer(api.client, BAN_LILAS, INCONNUE, BAN_SANS_VENTE))[
        "donnees"
    ]

    assert donnees["ids_ecartes"] == [INCONNUE]
    assert [c["ban_id"] for c in donnees["colonnes"]] == [BAN_LILAS, BAN_SANS_VENTE]


async def test_an_address_out_of_coverage_keeps_its_column(api, migrated, georisques):
    """N-07: the hosting of each column is the one of its own departement."""
    _scope(migrated, datasets=TOUT)

    donnees = (await _comparer(api.client, BAN_LILAS, HORS_COUVERTURE))["donnees"]

    colonne = donnees["colonnes"][1]
    assert colonne["couverture"] == {
        "etat": "hors_couverture",
        "motif": "territoire_hors_perimetre",
    }
    assert colonne["commune"] == {"code_insee": "35238", "nom": "Rennes"}
    assert colonne["departement"] == {"code": "35", "nom": "Ille-et-Vilaine"}
    for ligne in _lignes(donnees).values():
        cellule = ligne["cellules"][1]
        assert (cellule["etat"], cellule["valeur"]) == ("out_of_coverage", None)
        assert cellule["motif_non_calcul"] == "adresse_hors_couverture"
        assert ligne["identiques"] is False
    # The hosted column is read as its own page.
    assert _lignes(donnees)["ventes_parcelle"]["cellules"][0]["valeur"] == 3


async def test_a_tie_of_the_most_recent_documents_is_never_resolved(
    api, migrated, georisques
):
    _scope(migrated, datasets=TOUT)

    donnees = (await _comparer(api.client, BAN_EGALITE, BAN_LILAS))["donnees"]

    egalite, lilas = _lignes(donnees)["dpe_plus_recent"]["cellules"]
    assert egalite["selection"]["etat"] == "egalite"
    assert egalite["selection"]["document"] is None
    assert len(egalite["selection"]["documents_ex_aequo"]) == 12
    assert (egalite["etat"], egalite["valeur"]) == ("value", "2024-05-01")
    assert lilas["selection"]["document"]["numero"] == "2485E0000003C"
    assert lilas["verdict"] == "different"


# --- Requests (T-04-07-01) ------------------------------------------------------------


@pytest.mark.parametrize(
    "ids",
    [
        BAN_LILAS,
        f"{BAN_LILAS},46.67;-1.43",
        ",".join([BAN_LILAS, BAN_SANS_VENTE, BAN_CHAUSSEE, BAN_FRONTIERE, INCONNUE]),
    ],
)
async def test_bad_requests_are_refused_before_any_read(api, migrated, ids):
    _scope(migrated, datasets=TOUT)
    api.statements.clear()

    response = await api.client.get(ROUTE, params={"ids": ids})

    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["cache-control"] == "no-store"
    assert "46.67" not in response.text and BAN_LILAS not in response.text
    assert response.json()["code"] == "ids_invalides"
    assert api.statements == []


# --- Publication (N-01) and upstreams (API CR-01) -------------------------------------


async def test_refused_dpe_publication_breaks_the_document_rows_only(
    mounted, migrated, georisques
):
    _scope(migrated, datasets=TOUT)
    _publish(migrated, "dpe", "diagnostics_dpe", "failed")
    try:
        response = await mounted.get(
            ROUTE, params={"ids": f"{BAN_LILAS},{BAN_SANS_VENTE}"}
        )
    finally:
        _publish(migrated, "dpe", "diagnostics_dpe", "published")

    assert response.status_code == 200, response.text
    lignes = _lignes(response.json()["donnees"])
    for cle in ("dpe_declares", "dpe_plus_recent"):
        assert [c["etat"] for c in lignes[cle]["cellules"]] == ["temporary_error"] * 2
        assert {c["motif"] for c in lignes[cle]["cellules"]} == {
            "publication_non_validee"
        }
    assert lignes["ventes_parcelle"]["cellules"][0]["valeur"] == 3
    assert lignes["argiles"]["cellules"][0]["etat"] == "value"
    assert "2485E0000003C" not in response.text


async def test_refused_dvf_publication_breaks_the_sales_rows_only(
    mounted, migrated, georisques
):
    _scope(migrated, datasets=TOUT)
    _publish(migrated, "dvf", "mutations_grain", "failed")
    try:
        response = await mounted.get(
            ROUTE, params={"ids": f"{BAN_LILAS},{BAN_SANS_VENTE}"}
        )
        communes = await mounted.get(
            "/v1/communes/comparaison", params={"codes": "85191,44109"}
        )
    finally:
        _publish(migrated, "dvf", "mutations_grain", "published")

    assert response.status_code == 200, response.text
    lignes = _lignes(response.json()["donnees"])
    for cle in ("ventes_parcelle", "derniere_vente"):
        assert [c["etat"] for c in lignes[cle]["cellules"]] == ["temporary_error"] * 2
    # ④ derives from DVF (decision of 2026-10-03): the reference follows it.
    assert [c["etat"] for c in lignes["prix_median_maison"]["cellules"]] == [
        "temporary_error"
    ] * 2
    assert lignes["dpe_declares"]["cellules"][0]["valeur"] == 3
    # The commune comparator keeps its own behaviour: one barrier, one 503.
    assert communes.status_code == 503, communes.text


async def test_upstreams_are_called_without_a_transaction_nor_the_lock(
    api, migrated, georisques
):
    _scope(migrated, datasets=TOUT)
    observe: list[dict[str, int]] = []
    reponse = georisques.return_value

    async def amont(*_args, **_kwargs):
        observe.append(_transactions_et_verrous(migrated))
        return reponse

    georisques.side_effect = amont

    donnees = (await _comparer(api.client, BAN_LILAS, BAN_SANS_VENTE))["donnees"]

    assert observe == [{"verrous": 0, "transactions": 0}] * 2
    assert [c["etat"] for c in _lignes(donnees)["argiles"]["cellules"]] == ["value"] * 2


# --- 04-10: DPE documents compared by their number (CMP-02, D-116, N-01) -------------

ROUTE_DPE = "/v1/dpe/comparaison"
# The seed of the address page: a document of 2020 (former method) and one of
# 2022 (current method) declared at « 12 Rue des Lilas ».
DPE_ANCIEN, DPE_ACTUEL, DPE_RECENT = (numero for numero, *_ in DPE_LILAS)
DPE_PUBLIEE = DPE_GLYCINES[0][0]  # declared address published as typed
DPE_MASQUEE = DPE_GLYCINES[3][0]  # raw text, full text and complement: masked
DPE_INCONNU = "2385E9999999X"
SENSIBLES = ("classe_energie", "consommation", "classe_climat", "emissions")


async def _comparer_dpe(client, *numeros: str, status: int = 200) -> dict:
    response = await client.get(ROUTE_DPE, params={"numeros": ",".join(numeros)})
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


async def test_task_6_two_documents_of_two_methods_get_no_class_gap(mounted, migrated):
    _scope(migrated, datasets=TOUT)

    donnees = (await _comparer_dpe(mounted, DPE_ANCIEN, DPE_ACTUEL))["donnees"]

    assert [g["cle"] for g in donnees["groupes"]] == [
        "etiquettes",
        "logement",
        "cout_validite",
        "adresse",
    ]
    assert [c["numero"] for c in donnees["colonnes"]] == [DPE_ANCIEN, DPE_ACTUEL]
    assert [c["methode"] for c in donnees["colonnes"]] == ["ancienne", "actuelle"]
    lignes = _lignes(donnees)
    for cle in SENSIBLES[::2]:  # the classes; the seed declares no figure
        cellule = lignes[cle]["cellules"][1]
        assert cellule["motif_non_calcul"] == "methodes_differentes", cle
        assert (cellule["ecart"], cellule["verdict"]) == (None, None), cle
    assert lignes["type_batiment"]["cellules"][1]["verdict"] == "identique"
    assert donnees["numeros_ecartes"] == []


async def test_every_cell_is_the_value_of_the_document_page(mounted, migrated):
    _scope(migrated, datasets=TOUT)

    donnees = (await _comparer_dpe(mounted, DPE_ACTUEL, DPE_RECENT))["donnees"]
    lignes = _lignes(donnees)
    for position, numero in enumerate((DPE_ACTUEL, DPE_RECENT)):
        page = await mounted.get(f"/v1/dpe/{numero}")
        assert page.status_code == 200, page.text
        reperes = page.json()["donnees"]["reperes"]
        for cle, servie in (
            ("classe_energie", reperes["etiquette_energie"]["classe"]),
            ("classe_climat", reperes["etiquette_climat"]["classe"]),
            ("consommation", reperes["etiquette_energie"]["mesure"]),
            ("surface", reperes["logement_decrit"]["surface"]),
            ("cout_annuel", reperes["cout_annuel"]),
        ):
            cellule = lignes[cle]["cellules"][position]
            for membre in ("field_id", "etat", "motif", "valeur", "unite"):
                assert cellule[membre] == servie[membre], (numero, cle, membre)
    # Same method: the classes get the served verdict.
    assert lignes["classe_energie"]["cellules"][1]["verdict"] == "different"


async def test_a_masked_declared_address_never_reaches_the_answer(mounted, migrated):
    _scope(migrated, datasets=TOUT)

    response = await mounted.get(
        ROUTE_DPE, params={"numeros": f"{DPE_PUBLIEE},{DPE_MASQUEE}"}
    )

    assert response.status_code == 200, response.text
    assert SENTINELLE not in response.text
    donnees = response.json()["donnees"]
    publiee, masquee = (c["adresse_declaree"] for c in donnees["colonnes"])
    assert publiee == {"etat": "publiee", "texte": "14 rue des Glycines", "motif": None}
    assert masquee == {"etat": "masquee", "texte": None, "motif": "complement_present"}
    cellule = _lignes(donnees)["adresse_declaree"]["cellules"][1]
    assert (cellule["etat"], cellule["valeur"]) == ("suppressed", None)
    assert cellule["motif_non_calcul"] == "adresse_declaree_non_affichee"


async def test_an_unknown_number_is_named_and_the_number_route_is_not_captured(
    mounted, migrated
):
    _scope(migrated, datasets=TOUT)

    donnees = (await _comparer_dpe(mounted, DPE_ACTUEL, DPE_INCONNU))["donnees"]
    absente = await mounted.get(ROUTE_DPE)
    document = await mounted.get(f"/v1/dpe/{DPE_ACTUEL}")

    assert [c["numero"] for c in donnees["colonnes"]] == [DPE_ACTUEL]
    assert donnees["numeros_ecartes"] == [DPE_INCONNU]
    assert donnees["groupes"]
    # The static path is the comparison route, never a malformed number.
    assert absente.status_code == 422
    assert absente.json()["code"] == "numeros_invalides"
    assert document.status_code == 200, document.text
    assert document.json()["donnees"]["numero"] == DPE_ACTUEL


async def test_refused_dpe_publication_serves_no_document_comparators_unchanged(
    mounted, migrated, georisques
):
    _scope(migrated, datasets=TOUT)
    _publish(migrated, "dpe", "diagnostics_dpe", "failed")
    try:
        documents = await mounted.get(
            ROUTE_DPE, params={"numeros": f"{DPE_ANCIEN},{DPE_ACTUEL}"}
        )
        adresses = await mounted.get(
            ROUTE, params={"ids": f"{BAN_LILAS},{BAN_SANS_VENTE}"}
        )
    finally:
        _publish(migrated, "dpe", "diagnostics_dpe", "published")

    assert documents.status_code == 503, documents.text
    assert documents.headers["cache-control"] == "no-store"
    assert DPE_ANCIEN not in documents.text and DPE_ACTUEL not in documents.text
    assert "donnees" not in documents.json()
    # The address comparator keeps its behaviour: only its document rows break.
    assert adresses.status_code == 200, adresses.text
    lignes = _lignes(adresses.json()["donnees"])
    assert {c["etat"] for c in lignes["dpe_declares"]["cellules"]} == {
        "temporary_error"
    }


# --- 04-11: a DPE designated on a compared address (D-102, CMP-02, R06-09/7) --------


async def _designer(client, ids: tuple[str, ...], designes: str) -> dict:
    response = await client.get(
        ROUTE, params={"ids": ",".join(ids), "designes": designes}
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()["donnees"]


def _designees(donnees: dict) -> dict[str, dict]:
    documents = next(g for g in donnees["groupes"] if g["cle"] == "documents")
    return {
        ligne["cle"].removeprefix("designe_"): ligne
        for ligne in documents["lignes"]
        if ligne["designe"]
    }


SERVI = (
    "field_id",
    "etat",
    "motif",
    "valeur",
    "unite",
    "ecart",
    "motif_non_calcul",
    "verdict",
    "identique_a_la_reference",
)


async def test_the_same_couple_designated_or_compared_gets_the_same_rows(
    mounted, migrated, georisques
):
    """R06-09/7 on the seed: 2020 (former method) at Lilas, 2024 at Glycines."""
    _scope(migrated, datasets=TOUT)

    donnees = await _designer(
        mounted,
        (BAN_LILAS, BAN_GLYCINES),
        f"{BAN_LILAS}:{DPE_ANCIEN},{BAN_GLYCINES}:{DPE_PUBLIEE}",
    )
    directe = (await _comparer_dpe(mounted, DPE_ANCIEN, DPE_PUBLIEE))["donnees"]

    designation = [c["designation"] for c in donnees["colonnes"]]
    assert [(d["numero"], d["etat"]) for d in designation] == [
        (DPE_ANCIEN, "designe"),
        (DPE_PUBLIEE, "designe"),
    ]
    assert designation[0]["classe_energie"] == "E"
    assert designation[0]["date_etablissement"] == "2020-05-04"
    assert donnees["designations_ecartees"] == []
    lignes = _designees(donnees)
    attendues = _lignes(directe)
    assert list(lignes) == list(attendues)
    for cle, attendue in attendues.items():
        assert lignes[cle]["identiques"] == attendue["identiques"], cle
        for servie, cellule in zip(
            lignes[cle]["cellules"], attendue["cellules"], strict=True
        ):
            assert {m: servie[m] for m in SERVI} == {m: cellule[m] for m in SERVI}, cle
    classe = lignes["classe_energie"]["cellules"][1]
    assert classe["motif_non_calcul"] == "methodes_differentes"
    # The rows of the address keep their own values next to the designation.
    assert _lignes(donnees)["dpe_declares"]["cellules"][0]["valeur"] == 3


async def test_a_number_of_another_address_or_retired_is_set_aside(
    api, migrated, georisques
):
    _scope(migrated, datasets=TOUT)

    donnees = await _designer(
        api.client,
        (BAN_LILAS, BAN_RETIRE),
        # The number is declared at Glycines; the retired one is excluded from
        # the population of its address, as on the address page.
        f"{BAN_LILAS}:{DPE_PUBLIEE},{BAN_RETIRE}:{DPE_RETIRE}",
    )

    assert donnees["designations_ecartees"] == [
        {"ban_id": BAN_LILAS, "numero": DPE_PUBLIEE},
        {"ban_id": BAN_RETIRE, "numero": DPE_RETIRE},
    ]
    assert [c["designation"] for c in donnees["colonnes"]] == [None, None]
    assert _designees(donnees) == {}


async def test_a_designation_without_its_column_partner_says_aucun_dpe_designe(
    api, migrated, georisques
):
    _scope(migrated, datasets=TOUT)

    donnees = await _designer(
        api.client, (BAN_SANS_VENTE, BAN_LILAS), f"{BAN_LILAS}:{DPE_RECENT}"
    )

    colonnes = donnees["colonnes"]
    assert colonnes[0]["designation"] is None
    assert colonnes[1]["designation"]["numero"] == DPE_RECENT
    for ligne in _designees(donnees).values():
        sans, designee = ligne["cellules"]
        assert (sans["etat"], sans["motif"]) == ("not_applicable", "aucun_dpe_designe")
        assert designee["motif_non_calcul"] == "donnee_absente"


@pytest.mark.parametrize(
    "designes",
    [
        f"{BAN_LILAS}:2385e0123456a",
        f"{BAN_LILAS}",
        f"{INCONNUE}:{DPE_ACTUEL}",
        f"{BAN_LILAS}:{DPE_ACTUEL},{BAN_LILAS}:{DPE_RECENT}",
    ],
    ids=["number-form", "no-number", "key-not-compared", "two-for-one-address"],
)
async def test_bad_designations_are_refused_before_any_read(api, migrated, designes):
    _scope(migrated, datasets=TOUT)
    api.statements.clear()

    response = await api.client.get(
        ROUTE, params={"ids": f"{BAN_LILAS},{BAN_SANS_VENTE}", "designes": designes}
    )

    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == "no-store"
    assert BAN_LILAS not in response.text and DPE_ACTUEL not in response.text
    assert response.json()["code"] == "designations_invalides"
    assert api.statements == []


async def test_refused_dpe_publication_keeps_the_designation_in_outage(
    mounted, migrated, georisques
):
    """N-01 on the mounted application: never « not declared » when unread."""
    _scope(migrated, datasets=TOUT)
    _publish(migrated, "dpe", "diagnostics_dpe", "failed")
    try:
        response = await mounted.get(
            ROUTE,
            params={
                "ids": f"{BAN_LILAS},{BAN_SANS_VENTE}",
                "designes": f"{BAN_LILAS}:{DPE_ACTUEL}",
            },
        )
    finally:
        _publish(migrated, "dpe", "diagnostics_dpe", "published")

    assert response.status_code == 200, response.text
    donnees = response.json()["donnees"]
    assert donnees["designations_ecartees"] == []
    designation = donnees["colonnes"][0]["designation"]
    assert designation == {
        "numero": DPE_ACTUEL,
        "etat": "temporary_error",
        "motif": "publication_non_validee",
        "classe_energie": None,
        "date_etablissement": None,
    }
    lignes = _designees(donnees)
    assert lignes
    for ligne in lignes.values():
        assert (ligne["cellules"][0]["etat"], ligne["cellules"][0]["motif"]) == (
            "temporary_error",
            "publication_non_validee",
        )
        assert ligne["cellules"][0]["valeur"] is None
    # The rest of the comparison stays served.
    assert _lignes(donnees)["ventes_parcelle"]["cellules"][0]["valeur"] == 3


# --- 04-15: identifiers stored, never served (owner decision C3) ---------------------

C3_SENTINELLES = {
    "numero_dpe_immeuble_associe": "C3IMMEUBLESENTINELLE",
    "numero_immatriculation_copropriete": "C3COPROSENTINELLE",
}


async def test_0415_c3_identifiers_never_leave_the_mounted_api(
    mounted, migrated, georisques
):
    """Values stored in the two C3 columns of documents declared at an address
    appear in no response of the document, address and comparison routes."""
    _scope(migrated, datasets=TOUT)
    numeros = [DPE_ANCIEN, DPE_ACTUEL]
    assignments = ", ".join(f"{column} = %s" for column in C3_SENTINELLES)
    with psycopg.connect(migrated, autocommit=True) as conn:
        conn.execute(
            f"UPDATE diagnostics_dpe SET {assignments} WHERE numero_dpe = ANY(%s)",
            (*C3_SENTINELLES.values(), numeros),
        )
    try:
        responses = [
            await mounted.get(f"/v1/dpe/{DPE_ACTUEL}"),
            await mounted.get(ROUTE_DPE, params={"numeros": ",".join(numeros)}),
            await mounted.get(f"/v1/adresse/{BAN_LILAS}"),
            await mounted.get(f"/v1/adresse/{BAN_LILAS}/dpe"),
            await mounted.get(
                ROUTE,
                params={
                    "ids": f"{BAN_LILAS},{BAN_SANS_VENTE}",
                    "designes": f"{BAN_LILAS}:{DPE_ACTUEL}",
                },
            ),
        ]
    finally:
        with psycopg.connect(migrated, autocommit=True) as conn:
            nulls = ", ".join(f"{column} = NULL" for column in C3_SENTINELLES)
            conn.execute(
                f"UPDATE diagnostics_dpe SET {nulls} WHERE numero_dpe = ANY(%s)",
                (numeros,),
            )

    # The values are really read where they are stored (no vacuous pass).
    assert all(response.status_code == 200 for response in responses), [
        (response.url, response.status_code) for response in responses
    ]
    assert DPE_ACTUEL in responses[0].text and DPE_ACTUEL in responses[3].text
    for response in responses:
        for column, sentinelle in C3_SENTINELLES.items():
            assert sentinelle not in response.text, response.url
            assert column not in response.text, response.url
