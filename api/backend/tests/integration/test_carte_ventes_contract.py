"""04.10-05 (LIEU-02, SEM-01, SEM-02): the sales of a parcel of the map on PostGIS.

``GET /v1/carte/{ban_id}/parcelle/{id_parcelle}/ventes`` serves the sales of
one parcel, five per page, for the map of one address. Proven here on synthetic
squares laid around arbitrary tiles:

- one price rule: each card is the card ``/v1/adresse/{ban_id}/ventes`` serves
  for the same parcel, withheld prices included, and the address page still
  reads ten per page;
- the route answers only for a parcel that touches the bounds of the address
  of the path, computed by the server; an unknown key, an unknown parcel and a
  parcel outside the bounds are the same constant 404, and no sale is read;
- zero sale, DVF out of coverage and outage are three distinct answers;
- no mutation identifier is in any answer.

The scope is written by the production path and read by the real scope
dependency; only the session is overridden.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_carte_ventes_contract.py
"""

from collections.abc import AsyncIterator, Iterator
from datetime import date

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from psycopg.rows import dict_row
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adresse.router import adresse_router
from app.carte.parcelles import emprise_de_deplacement
from app.carte.router import carte_router
from app.common.exceptions import register_exception_handlers
from app.database import get_db
from app.rate_limit import _bucket_windows, _buckets
from tests.integration.instance_scope_fixture import install_scope_at
from tests.integration.test_adresse_ventes_contract import (
    Api,
    Mutation,
    _restore_mutations,
    _swap_mutations,
    injected,
    insert_adresse,
    insert_mutation,
    link_parcelles,
    migrate,
    publish,
)
from tests.integration.test_carte_parcelles_contract import (
    cadre,
    carre,
    chemins,
    exemple,
    insert_parcelle,
    lectures,
    par_id,
    point,
    territoire,
)

pytestmark = pytest.mark.destructive

TOUT = ("ban", "cadastre", "dvf")
HEBERGES = ("44", "85")
PERIODE = "2021-2025"
MILLESIME = "2026-07"
Z = 16
# Arbitrary tiles: nothing depends on where they are, only on their edges.
TUILE = (32500, 23000)
# Far from the first one: an address of a departement DVF does not cover.
TUILE_67 = (TUILE[0], TUILE[1] + 40)
GABARIT = "/v1/carte/{ban_id}/parcelle/{id_parcelle}/ventes"
INTROUVABLE = {
    "type": "about:blank",
    "title": "Parcelle introuvable autour de cette adresse",
    "status": 404,
    "instance": GABARIT,
}

# Synthetic BAN keys: the first two characters are the departement. The
# bounds of an address reach 2.5 tiles each way at zoom 16: those of ADRESSE
# end at the share 3.0 of its tile, those of LOIN run from 2.5 to 7.5.
ADRESSE = "85000_0001_00001"
ADRESSE_ISOLEE = "85000_0002_00001"
LOIN = "85000_0003_00001"
ADRESSE_67 = "67000_0001_00001"
INCONNUE = "85000_0001_00099"
VOIE = "85000_0001"

SEPT = "85000000VA0001"  # under ADRESSE: seven sales
ISOLEE = "85000000VA0002"  # under ADRESSE_ISOLEE: one recent sale, alone
SANS_VENTE = "85000000VA0003"
AU_BORD = "85000000VA0004"  # astride the east edge of the bounds of ADRESSE
DEHORS = "85000000VA0005"  # just past that edge
PAIRE = "85000000VA0006"  # sold once, with SEPT
ABSENTE = "85000000VA0099"  # sold, and in no cadastre row
NON_HEBERGEE = "49000000VA0001"  # inside the bounds, in a departement not hosted
PARCELLE_67 = "67000000VA0001"

# Last imported DVF date: the latest sale of SEPT. The 24-month window starts
# on 2023-09-10 (inclusive).
FIN_DVF = date(2025, 9, 10)
# Sentinel amounts: a withheld price must never be found in a response.
PRIX_PAIRE = 198_765
PRIX_ISOLEE = 312_345

VENTES_DE_SEPT = (
    Mutation("2025-V7-01", FIN_DVF, 251_000, 90, (SEPT,)),
    # PAIRE carries this mutation only: isolated, recent, withheld.
    Mutation("2025-V7-02", date(2025, 3, 1), PRIX_PAIRE, 70, (SEPT, PAIRE)),
    Mutation("2024-V7-03", date(2024, 6, 1), 230_000, 95, (SEPT,), dependances=1),
    Mutation("2023-V7-04", date(2023, 9, 10), 201_000, 88, (SEPT,)),
    Mutation(
        "2022-V7-05",
        date(2022, 5, 5),
        340_000,
        None,
        (SEPT,),
        maisons=0,
        appartements=2,
    ),
    Mutation("2021-V7-06", date(2021, 11, 11), 150_000, 80, (SEPT,), nature="Echange"),
    Mutation("2021-V7-07", date(2021, 1, 5), 99_000, 60, (SEPT,)),
)
# One DVF row duplicated, and one mutation of unknown position (in no count).
DOUBLEE = VENTES_DE_SEPT[2]
SANS_POSITION = Mutation("2022-V7-SP", date(2022, 4, 4), 150_000, 70, (SEPT,))
VENTE_ISOLEE = Mutation("2025-VI-01", date(2025, 6, 1), PRIX_ISOLEE, 95, (ISOLEE,))
AUTRES_VENTES = (
    Mutation("2024-VB-01", date(2024, 2, 2), 150_000, 75, (AU_BORD,)),
    Mutation("2024-VD-01", date(2024, 3, 3), 160_000, 76, (DEHORS,)),
    Mutation("2024-VA-01", date(2024, 4, 4), 170_000, 77, (ABSENTE,)),
)

# Parcels sold together (task 2): one tile north of the first ones, inside the
# bounds of ADRESSE except G_LOIN, which only the bounds of LOIN hold.
TUILE_NORD = (TUILE[0], TUILE[1] - 1)
G_A = "85000000VG0001"  # the asked one: in six sales
G_B = "85000000VG0002"
G_C = "85000000VG0003"
G_SANS_SURFACE = "85000000VG0004"  # contenance NULL
G_LOIN = "85000000VG0005"
G_AILLEURS = "44000000VG0001"  # in another hosted departement
G_ABSENTE = "85000000VG0099"  # in no cadastre row
PRIX_TROIS = 287_654
# Distinct dates: a sale of G_A is named by its date in the tests below.
VENTES_DE_G = (
    Mutation("2025-VG-SEULE", date(2025, 9, 9), 265_000, 92, (G_A,)),
    # G_C carries this mutation only: isolated, recent, withheld.
    Mutation("2025-VG-TROIS", date(2025, 6, 1), PRIX_TROIS, 110, (G_A, G_B, G_C)),
    Mutation("2024-VG-ABSENTE", date(2024, 4, 2), 190_000, 85, (G_A, G_B, G_ABSENTE)),
    Mutation(
        "2022-VG-SANS",
        date(2022, 3, 3),
        48_000,
        None,
        (G_A, G_SANS_SURFACE),
        maisons=0,
    ),
    Mutation("2021-VG-HORS", date(2021, 5, 5), 75_000, 70, (G_A, G_LOIN)),
    Mutation("2021-VG-AUTRE", date(2021, 2, 2), 66_000, 65, (G_A, G_AILLEURS)),
)
SEULE, TROIS, AVEC_ABSENTE, SANS_SURFACE, HORS_EMPRISE, AUTRE_DEPARTEMENT = (
    mutation.jour.isoformat() for mutation in VENTES_DE_G
)
MUTATIONS = (
    *VENTES_DE_SEPT,
    SANS_POSITION,
    VENTE_ISOLEE,
    *AUTRES_VENTES,
    *VENTES_DE_G,
)

# (identifier, geometry, surface). The squares are 0.08 tile wide.
PARCELLES = (
    (SEPT, carre(TUILE, 0.5, 0.5), 400),
    (ISOLEE, carre(TUILE, 0.7, 0.5), 300),
    (SANS_VENTE, carre(TUILE, 0.3, 0.5), 200),
    (AU_BORD, carre(TUILE, 3.0, 0.5), 500),
    (DEHORS, carre(TUILE, 3.2, 0.5), 600),
    (PAIRE, carre(TUILE, 0.5, 0.7), 120),
    (NON_HEBERGEE, carre(TUILE, 0.5, 0.3), 210),
    (PARCELLE_67, carre(TUILE_67, 0.5, 0.5), 220),
    (G_A, carre(TUILE, 0.5, 1.5), 100),
    (G_B, carre(TUILE, 0.7, 1.5), 250),
    (G_C, carre(TUILE, 0.9, 1.5), 380),
    (G_SANS_SURFACE, carre(TUILE, 0.3, 1.5), None),
    (G_LOIN, carre(TUILE, 3.2, 1.5), 900),
    (G_AILLEURS, carre(TUILE, 0.1, 1.5), 60),
)
ADRESSES = (
    (ADRESSE, point(TUILE, 0.5, 0.5)),
    (ADRESSE_ISOLEE, point(TUILE, 0.7, 0.5)),
    (LOIN, point(TUILE, 5.0, 0.5)),
    (ADRESSE_67, point(TUILE_67, 0.5, 0.5)),
)


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    migrate(isolated_pg_dsn)
    with psycopg.connect(isolated_pg_dsn) as conn:
        # Rectangles, never a real boundary: the tile route reads them.
        conn.execute(
            "INSERT INTO geo_departements (code, nom, geometry) VALUES "
            "('85', 'Hébergé', ST_GeomFromText(%s, 4326))",
            (territoire(cadre(TUILE, -4, -4, 9, 5)),),
        )
        for rang, (ban_id, lon_lat) in enumerate(ADRESSES):
            insert_adresse(conn, 95_500_001 + rang, ban_id, lon_lat)
        for rang, (id_parcelle, wkt, contenance) in enumerate(PARCELLES):
            insert_parcelle(conn, 97_500_001 + rang, id_parcelle, wkt, contenance)

        ident = iter(range(98_500_001, 98_600_000))
        for mutation in MUTATIONS:
            if mutation is SANS_POSITION:
                insert_mutation(conn, next(ident), mutation, point=(None, None))
            else:
                insert_mutation(conn, next(ident), mutation)
            link_parcelles(conn, mutation)
        insert_mutation(conn, next(ident), DOUBLEE)
        for nom, libelle in (("dvf", PERIODE), ("cadastre", MILLESIME)):
            conn.execute(
                "INSERT INTO dataset_metadata (dataset_name, status, "
                "source_period_label) VALUES (%s, 'disponible', %s) "
                "ON CONFLICT (dataset_name) DO UPDATE SET source_period_label = "
                "EXCLUDED.source_period_label",
                (nom, libelle),
            )
    return isolated_pg_dsn


@pytest.fixture
async def api(migrated) -> AsyncIterator[Api]:
    engine = create_async_engine(
        migrated.replace("postgresql://", "postgresql+asyncpg://")
    )
    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def record(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db():
        async with sessions() as session:
            yield session

    application = FastAPI()
    register_exception_handlers(application)
    # The map, and the address page its cards are compared with.
    application.include_router(carte_router)
    application.include_router(adresse_router)
    application.dependency_overrides[get_db] = db
    transport = httpx.ASGITransport(app=application)
    _buckets.clear()
    _bucket_windows.clear()
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield Api(c, statements)
    finally:
        await engine.dispose()
        _buckets.clear()
        _bucket_windows.clear()


def scope(dsn, datasets=TOUT, departements=HEBERGES):
    install_scope_at(dsn, sorted(datasets), departements)


def chemin(ban_id: str, id_parcelle: str) -> str:
    return f"/v1/carte/{ban_id}/parcelle/{id_parcelle}/ventes"


async def ventes(
    api: Api, ban_id: str, id_parcelle: str, status: int = 200, /, **params
) -> dict:
    response = await api.client.get(chemin(ban_id, id_parcelle), params=params)
    assert response.status_code == status, response.text
    # A page of one generation and a problem are never stored by a cache.
    assert response.headers["cache-control"] == "no-store"
    if status != 200:
        assert response.headers["content-type"].startswith("application/problem+json")
        assert ban_id not in response.text and id_parcelle not in response.text
        assert response.json()["instance"] == GABARIT
        return response.json()
    return response.json()["donnees"]


async def fiche(api: Api, ban_id: str, **params) -> dict:
    """The sales of the parcel of the point, as the address page reads them."""
    response = await api.client.get(f"/v1/adresse/{ban_id}/ventes", params=params)
    assert response.status_code == 200, response.text
    return response.json()["donnees"]


def chaines(valeur) -> Iterator[str]:
    """Every key and every string of a JSON value, at any depth."""
    if isinstance(valeur, dict):
        for cle, contenu in valeur.items():
            yield cle
            yield from chaines(contenu)
    elif isinstance(valeur, list):
        for contenu in valeur:
            yield from chaines(contenu)
    elif isinstance(valeur, str):
        yield valeur


# --- Five per page, latest first ----------------------------------------------------


async def test_tracer_seven_sales_are_a_page_of_five_then_a_page_of_two(api, migrated):
    scope(migrated)

    une = await ventes(api, ADRESSE, SEPT)
    deux = await ventes(api, ADRESSE, SEPT, page=2, vg=une["generation"])

    for page, rang, nombre in ((une, 1, 5), (deux, 2, 2)):
        assert (page["etat"], page["motif"]) == ("value", None)
        assert (page["total"], page["page"], page["pages"], page["par_page"]) == (
            7,
            rang,
            2,
            5,
        )
        assert (page["periode"], page["annee_debut"]) == (PERIODE, 2021)
        assert len(page["ventes"]) == nombre == len(page["groupes"])
    assert len(une["generation"]) == 16 and une["generation"] == deux["generation"]
    # The duplicated DVF row is one sale; the mutation of unknown position none.
    assert [carte["date"] for page in (une, deux) for carte in page["ventes"]] == [
        "2025-09-10",
        "2025-03-01",
        "2024-06-01",
        "2023-09-10",
        "2022-05-05",
        "2021-11-11",
        "2021-01-05",
    ]
    assert set(une) == {
        "etat",
        "motif",
        "periode",
        "annee_debut",
        "total",
        "page",
        "pages",
        "par_page",
        "generation",
        "ventes",
        "groupes",
    }


async def test_the_total_is_the_count_the_tile_serves_for_the_same_parcel(
    api, migrated
):
    scope(migrated)

    reponse = await api.client.get(f"/v1/carte/parcelles/{Z}/{TUILE[0]}/{TUILE[1]}")
    assert reponse.status_code == 200, reponse.text
    de_la_tuile = {
        id_parcelle: entite["properties"]["ventes"]
        for id_parcelle, entite in par_id(reponse.json()["donnees"]).items()
    }

    assert de_la_tuile == {SEPT: 7, ISOLEE: 1, SANS_VENTE: 0, PAIRE: 1}
    for id_parcelle, nombre in de_la_tuile.items():
        assert (await ventes(api, ADRESSE, id_parcelle))["total"] == nombre


# --- One price rule: the cards of the address page ----------------------------------


async def test_each_card_is_the_card_of_the_address_page(api, migrated):
    """T-04.10-10: same reader, same ``minimiser``, proven card by card."""
    scope(migrated)

    de_la_fiche = await fiche(api, ADRESSE)
    pages = [await ventes(api, ADRESSE, SEPT, page=rang) for rang in (1, 2)]
    de_la_carte = [carte for page in pages for carte in page["ventes"]]

    # The address page still reads ten per page: its seven sales are one page.
    assert (de_la_fiche["par_page"], de_la_fiche["pages"]) == (10, 1)
    assert de_la_fiche["total"] == pages[0]["total"] == 7
    assert len(de_la_fiche["ventes"]) == 7
    for rang, (servie, attendue) in enumerate(
        zip(de_la_carte, de_la_fiche["ventes"], strict=True)
    ):
        assert servie == attendue, rang
    assert de_la_fiche["generation"] == pages[0]["generation"]
    assert de_la_fiche["periode"] == pages[0]["periode"]
    # Both a shown and a withheld price went through the comparison.
    retenue = de_la_carte[1]
    assert (retenue["prix"], retenue["prix_m2"], retenue["prix_affiche"]) == (
        None,
        None,
        False,
    )
    assert retenue["minimisation"] == {"motif": "vente_isolee_recente"}
    assert de_la_carte[0]["prix"] == 251_000 and de_la_carte[0]["prix_affiche"] is True
    assert de_la_carte[0]["prix_m2"] == 2789
    # A sale exactly 24 months before the last DVF date is recent, and shown
    # here because its parcel carries other sales.
    assert (de_la_carte[3]["date"], de_la_carte[3]["prix"]) == ("2023-09-10", 201_000)
    assert de_la_carte[5]["nature"] == "Echange"
    assert de_la_carte[4]["composition"] == {
        "maisons": 0,
        "appartements": 2,
        "dependances": 0,
        "locaux_activite": 0,
    }
    assert all(carte["distance_m"] is None for carte in de_la_carte)


async def test_a_neighbour_parcel_gets_the_cards_of_its_own_address_page(api, migrated):
    """Asked from the map of another address, the cards are still the same."""
    scope(migrated)

    de_la_fiche = await fiche(api, ADRESSE_ISOLEE)
    de_la_carte = await ventes(api, ADRESSE, ISOLEE)

    assert de_la_carte["ventes"] == de_la_fiche["ventes"]
    assert de_la_carte["total"] == de_la_fiche["total"] == 1
    (carte,) = de_la_carte["ventes"]
    assert (carte["prix"], carte["prix_m2"], carte["prix_affiche"]) == (
        None,
        None,
        False,
    )
    assert carte["minimisation"] == {"motif": "vente_isolee_recente"}
    assert carte["surface_batie"] == 95


async def test_a_withheld_price_never_leaves_the_api(api, migrated):
    scope(migrated)

    bruts = [
        (await api.client.get(chemin(ADRESSE, SEPT))).text,
        (await api.client.get(chemin(ADRESSE, SEPT), params={"page": 2})).text,
        (await api.client.get(chemin(ADRESSE, ISOLEE))).text,
        (await api.client.get(chemin(ADRESSE, PAIRE))).text,
    ]

    assert all('"etat":"value"' in brut for brut in bruts)
    for brut in bruts:
        for prix in (PRIX_PAIRE, PRIX_ISOLEE):
            assert str(prix) not in brut
    # The shown ones are there: the search above is not vacuous.
    assert "251000" in bruts[0]


async def test_an_isolation_outage_withholds_the_recent_prices_on_the_map_too(
    api, migrated, monkeypatch
):
    """The predicate fails at the SQL level: no exact recent price, list kept."""
    from app.adresse import ventes as module

    scope(migrated)
    en_panne = module._ISOLEMENT_SQL.text.replace(
        "FROM mutation_parcelles c", "FROM mutation_parcelles_panne c"
    )
    assert en_panne != module._ISOLEMENT_SQL.text
    monkeypatch.setattr(module, "_ISOLEMENT_SQL", module.text(en_panne))

    page = await ventes(api, ADRESSE, SEPT)

    assert page["total"] == 7
    assert [carte["prix"] for carte in page["ventes"]] == [
        None,
        None,
        None,
        None,
        340_000,
    ]
    assert page["ventes"][0]["minimisation"] == {"motif": "isolement_indetermine"}
    assert page["ventes"] == (await fiche(api, ADRESSE))["ventes"][:5]


async def test_no_mutation_identifier_is_in_any_answer(api, migrated):
    """T-04.10-12: walked through the whole JSON, keys included."""
    scope(migrated)

    reponses = [
        await api.client.get(chemin(ADRESSE, SEPT)),
        await api.client.get(chemin(ADRESSE, SEPT), params={"page": 2}),
        await api.client.get(chemin(ADRESSE, ISOLEE)),
        await api.client.get(chemin(ADRESSE, PAIRE)),
        await api.client.get(chemin(ADRESSE, SANS_VENTE)),
        await api.client.get(chemin(LOIN, DEHORS)),
        # The pages that carry groups: a group is keyed by its rank, never by
        # its sale.
        await api.client.get(chemin(ADRESSE, G_A)),
        await api.client.get(chemin(ADRESSE, G_A), params={"page": 2}),
        await api.client.get(chemin(ADRESSE, G_B)),
        await api.client.get(chemin(LOIN, G_LOIN)),
    ]

    identifiants = [mutation.id_mutation for mutation in MUTATIONS]
    assert len(set(identifiants)) == len(MUTATIONS) >= 12
    for reponse in reponses:
        assert reponse.status_code == 200, reponse.text
        textes = list(chaines(reponse.json()))
        assert textes
        for texte in textes:
            assert "mutation" not in texte.lower(), texte
            for identifiant in identifiants:
                assert identifiant not in texte, texte
        for identifiant in identifiants:
            assert identifiant not in reponse.text


# --- Zero, out of coverage, outage --------------------------------------------------


async def test_a_parcel_without_sale_is_a_measured_zero_with_its_period(api, migrated):
    scope(migrated)

    zero = await ventes(api, ADRESSE, SANS_VENTE)

    assert zero == {
        "etat": "value",
        "motif": None,
        "periode": PERIODE,
        "annee_debut": 2021,
        "total": 0,
        "page": 1,
        "pages": 1,
        "par_page": 5,
        "generation": zero["generation"],
        "ventes": [],
        "groupes": [],
    }
    assert len(zero["generation"]) == 16


async def test_dvf_not_hosted_is_out_of_coverage_without_any_sales_read(api, migrated):
    scope(migrated, ("ban", "cadastre"))
    api.statements.clear()

    hors = await ventes(api, ADRESSE, SEPT, page=2)

    assert hors == {
        "etat": "out_of_coverage",
        "motif": "dataset_non_heberge",
        "periode": None,
        "annee_debut": None,
        "total": None,
        "page": 2,
        "pages": None,
        "par_page": 5,
        "generation": None,
        "ventes": [],
        "groupes": [],
    }
    assert lectures(api, "mutations_grain") == []
    assert lectures(api, "mutation_parcelles") == []


async def test_dvf_coverage_is_the_one_of_the_departement_of_the_parcel(api, migrated):
    """DVF publishes nothing for this departement; its cadastre is hosted."""
    scope(migrated, departements=("44", "67", "85"))
    api.statements.clear()

    hors = await ventes(api, ADRESSE_67, PARCELLE_67)

    assert (hors["etat"], hors["motif"]) == (
        "out_of_coverage",
        "source_sans_couverture",
    )
    assert hors["total"] is None and hors["ventes"] == []
    # The parcel was looked for (it is on the map), the sales were not.
    assert lectures(api, "parcelles") != []
    assert lectures(api, "mutations_grain") == []
    assert lectures(api, "mutation_parcelles") == []


async def test_a_refused_dvf_publication_is_a_503_never_an_empty_list(api, migrated):
    scope(migrated)
    publish(migrated, "failed")
    try:
        probleme = await ventes(api, ADRESSE, SEPT, 503)
    finally:
        publish(migrated, "published")

    assert probleme["code"] == "ventes_indisponibles"
    assert "ventes" not in probleme and "donnees" not in probleme
    # Served again once the publication is valid.
    assert (await ventes(api, ADRESSE, SEPT))["total"] == 7


@pytest.mark.parametrize(
    "table", ["mutations_grain", "mutation_parcelles", "parcelles"]
)
async def test_a_failed_read_is_a_503_never_an_empty_list(api, migrated, table):
    scope(migrated)
    with injected(
        migrated,
        f"ALTER TABLE {table} RENAME TO {table}_panne",
        restore=(f"ALTER TABLE {table}_panne RENAME TO {table}",),
    ):
        probleme = await ventes(api, ADRESSE, SEPT, 503)

    assert probleme["code"] == "ventes_indisponibles"
    assert probleme["status"] == 503 and "ventes" not in probleme


async def test_a_page_beyond_the_last_is_a_404_with_its_code(api, migrated):
    scope(migrated)

    assert (await ventes(api, ADRESSE, SEPT, 404, page=3))["code"] == (
        "page_hors_limites"
    )
    assert (await ventes(api, ADRESSE, SANS_VENTE, 404, page=2))["code"] == (
        "page_hors_limites"
    )


async def test_a_page_link_of_a_replaced_generation_is_refused(api, migrated):
    scope(migrated)
    premiere = await ventes(api, ADRESSE, SEPT)

    _swap_mutations(migrated)
    try:
        probleme = await ventes(
            api, ADRESSE, SEPT, 409, page=2, vg=premiere["generation"]
        )
        neuve = await ventes(api, ADRESSE, SEPT)
    finally:
        _restore_mutations(migrated)

    assert probleme["code"] == "generation_changee"
    assert premiere["generation"] not in str(probleme)
    assert neuve["generation"] != premiere["generation"] and neuve["total"] == 7


# --- Only around the address of the path --------------------------------------------


async def test_a_parcel_astride_the_edge_of_the_bounds_is_served(api, migrated):
    scope(migrated)

    au_bord = await ventes(api, ADRESSE, AU_BORD)

    assert (au_bord["etat"], au_bord["total"]) == ("value", 1)


@pytest.mark.parametrize(
    ("ban_id", "id_parcelle"),
    [
        (ADRESSE, DEHORS),  # in the cadastre, sold, just past the bounds
        (ADRESSE, ABSENTE),  # sold, in no cadastre row
        (INCONNUE, SEPT),  # a key the BAN does not hold
        (VOIE, SEPT),  # a street key
        (ADRESSE, NON_HEBERGEE),  # inside the bounds, cadastre not hosted there
        (LOIN, SEPT),  # served for its own address, not for this one
    ],
)
async def test_what_is_not_around_the_address_is_one_404_and_no_sale_is_read(
    api, migrated, ban_id, id_parcelle
):
    """T-04.10-11, T-04.10-13: the answer does not say which case it is."""
    scope(migrated)
    api.statements.clear()

    probleme = await ventes(api, ban_id, id_parcelle, 404)

    assert probleme == INTROUVABLE
    assert lectures(api, "mutations_grain") == []
    assert lectures(api, "mutation_parcelles") == []


async def test_unknown_key_unknown_parcel_and_parcel_out_of_bounds_are_one_body(
    api, migrated
):
    scope(migrated)

    reponses = [
        await api.client.get(chemin(*paire))
        for paire in ((INCONNUE, SEPT), (ADRESSE, ABSENTE), (ADRESSE, DEHORS))
    ]

    assert {reponse.status_code for reponse in reponses} == {404}
    assert len({reponse.content for reponse in reponses}) == 1
    entetes = [
        {
            nom: reponse.headers.get(nom)
            for nom in ("content-type", "cache-control", "content-length")
        }
        for reponse in reponses
    ]
    assert entetes[0] == entetes[1] == entetes[2]
    assert reponses[0].json() == INTROUVABLE


async def test_the_bounds_are_those_of_the_address_of_the_path(api, migrated):
    scope(migrated)

    # The two parcels the first address does not reach are served for the
    # address whose bounds hold them.
    assert (await ventes(api, LOIN, DEHORS))["total"] == 1
    assert (await ventes(api, LOIN, AU_BORD))["total"] == 1
    assert (await ventes(api, ADRESSE, SEPT))["total"] == 7


async def test_no_query_parameter_moves_the_bounds(api, migrated):
    """The visitor supplies no bounds: an unknown parameter is ignored."""
    scope(migrated)
    large = {
        "emprise": "-180,-90,180,90",
        "ouest": "-180",
        "sud": "-90",
        "est": "180",
        "nord": "90",
        "rayon": "100000",
        "ban_id": LOIN,
    }

    assert (await ventes(api, ADRESSE, DEHORS, 404, **large)) == INTROUVABLE
    assert (await ventes(api, ADRESSE, SEPT, **large))["total"] == 7


async def test_the_ban_not_hosted_is_the_same_404_without_reading_the_address(
    api, migrated
):
    scope(migrated, ("cadastre", "dvf"))
    api.statements.clear()

    assert (await ventes(api, ADRESSE, SEPT, 404)) == INTROUVABLE
    assert lectures(api, "adresses") == []
    assert lectures(api, "parcelles") == []
    assert lectures(api, "mutations_grain") == []


async def test_the_cadastre_not_hosted_is_the_same_404_without_reading_a_parcel(
    api, migrated
):
    scope(migrated, ("ban", "dvf"))
    api.statements.clear()

    assert (await ventes(api, ADRESSE, SEPT, 404)) == INTROUVABLE
    assert lectures(api, "parcelles") == []
    assert lectures(api, "mutations_grain") == []


def test_the_fixture_is_what_the_tests_say(migrated):
    """Which parcel touches which bounds is told by the base, not by the arithmetic."""

    def touchees(lon_lat: tuple[float, float]) -> set[str]:
        emprise = emprise_de_deplacement(*lon_lat)
        with psycopg.connect(migrated, row_factory=dict_row) as conn:
            lignes = conn.execute(
                "SELECT id_parcelle FROM parcelles WHERE ST_Intersects(geometrie, "
                "ST_MakeEnvelope(%s, %s, %s, %s, 4326))",
                (emprise.ouest, emprise.sud, emprise.est, emprise.nord),
            ).fetchall()
        return {ligne["id_parcelle"] for ligne in lignes}

    autour = touchees(point(TUILE, 0.5, 0.5))
    assert {SEPT, ISOLEE, SANS_VENTE, AU_BORD, PAIRE, NON_HEBERGEE} <= autour
    assert DEHORS not in autour and PARCELLE_67 not in autour
    plus_loin = touchees(point(TUILE, 5.0, 0.5))
    assert {AU_BORD, DEHORS} <= plus_loin and SEPT not in plus_loin
    # The parcels sold together: all around the first address but one, which
    # only the second address reaches.
    assert {G_A, G_B, G_C, G_SANS_SURFACE, G_AILLEURS} <= autour
    assert G_LOIN not in autour and G_LOIN in plus_loin and G_A not in plus_loin
    with psycopg.connect(migrated, row_factory=dict_row) as conn:
        absentes = conn.execute(
            "SELECT count(*) AS n FROM parcelles WHERE id_parcelle = ANY(%s)",
            ([ABSENTE, G_ABSENTE],),
        ).fetchone()
        sans_surface = conn.execute(
            "SELECT contenance FROM parcelles WHERE id_parcelle = %s",
            (G_SANS_SURFACE,),
        ).fetchone()
        fin = conn.execute("SELECT max(date_mutation) AS fin FROM mutations_grain")
        assert absentes["n"] == 0
        assert sans_surface["contenance"] is None
        assert fin.fetchone()["fin"] == FIN_DVF


# --- The parcels sold together (task 2) ---------------------------------------------

EXEMPLE = "ventes-parcelle.json"


async def groupes_de(api: Api, ban_id: str = ADRESSE, **params) -> dict[str, dict]:
    """The group of each sale of G_A, by the date of the sale, over its two pages."""
    pages = [await ventes(api, ban_id, G_A, page=rang, **params) for rang in (1, 2)]
    assert [len(page["ventes"]) for page in pages] == [5, 1]
    for page in pages:
        # One entry per sale, in the order of the sales.
        assert len(page["groupes"]) == len(page["ventes"])
    return {
        carte["date"]: groupe
        for page in pages
        for carte, groupe in zip(page["ventes"], page["groupes"], strict=True)
    }


def identifiants(groupe: dict) -> list[str]:
    return [parcelle["id_parcelle"] for parcelle in groupe["parcelles"]]


def plafonner(monkeypatch, plafond: int) -> None:
    """Cap of the parcels drawn for one sale.

    ``monkeypatch.setattr`` on a setting that does not exist prints the whole
    settings object in the report: the absence is said here instead.
    """
    from app.config import settings

    connus = set(type(settings).model_fields)
    if "carte_groupe_geometries_max" not in connus:
        pytest.fail("setting carte_groupe_geometries_max does not exist")
    monkeypatch.setattr(settings, "carte_groupe_geometries_max", plafond)


async def test_a_sale_of_the_parcel_alone_has_no_group(api, migrated):
    scope(migrated)

    groupes = await groupes_de(api)
    de_sept = await ventes(api, ADRESSE, SEPT)

    assert list(groupes) == [
        SEULE,
        TROIS,
        AVEC_ABSENTE,
        SANS_SURFACE,
        HORS_EMPRISE,
        AUTRE_DEPARTEMENT,
    ]
    assert groupes[SEULE] is None
    assert all(groupes[jour] is not None for jour in list(groupes)[1:])
    # Of the five latest sales of the other parcel, one took a second parcel.
    assert [groupe is None for groupe in de_sept["groupes"]] == [
        True,
        False,
        True,
        True,
        True,
    ]
    assert de_sept["groupes"][1] == {
        "total": 2,
        "autres": 1,
        "absentes_du_cadastre": 0,
        "hors_emprise": 0,
        "sans_surface": 0,
        "surface_totale_m2": 520,
        "parcelles": de_sept["groupes"][1]["parcelles"],
    }
    assert identifiants(de_sept["groupes"][1]) == [SEPT, PAIRE]


async def test_three_parcels_sold_together_are_served_whole_with_their_total(
    api, migrated
):
    scope(migrated)

    groupe = (await groupes_de(api))[TROIS]
    reponse = await api.client.get(
        f"/v1/carte/parcelles/{Z}/{TUILE_NORD[0]}/{TUILE_NORD[1]}"
    )
    assert reponse.status_code == 200, reponse.text
    de_la_tuile = par_id(reponse.json()["donnees"])

    assert {cle: valeur for cle, valeur in groupe.items() if cle != "parcelles"} == {
        "total": 3,
        "autres": 2,
        "absentes_du_cadastre": 0,
        "hors_emprise": 0,
        "sans_surface": 0,
        "surface_totale_m2": 100 + 250 + 380,
    }
    # The asked parcel is one of them; each one is drawn as its tile draws it.
    assert identifiants(groupe) == [G_A, G_B, G_C]
    for parcelle in groupe["parcelles"]:
        assert set(parcelle) == {"id_parcelle", "geometry", "etiquette"}
        entite = de_la_tuile[parcelle["id_parcelle"]]
        assert parcelle["geometry"] == entite["geometry"]
        assert parcelle["etiquette"] == entite["properties"]["etiquette"]
        assert parcelle["geometry"]["type"] == "MultiPolygon"


async def test_a_parcel_gone_from_the_cadastre_is_counted_and_leaves_no_total(
    api, migrated
):
    scope(migrated)

    groupe = (await groupes_de(api))[AVEC_ABSENTE]

    assert (groupe["total"], groupe["autres"]) == (3, 2)
    assert (groupe["absentes_du_cadastre"], groupe["hors_emprise"]) == (1, 0)
    # Never the 350 m2 of the two parcels that are left.
    assert groupe["surface_totale_m2"] is None
    assert identifiants(groupe) == [G_A, G_B]


async def test_a_parcel_without_surface_leaves_no_total_and_is_not_absent(
    api, migrated
):
    scope(migrated)

    groupe = (await groupes_de(api))[SANS_SURFACE]

    assert (groupe["total"], groupe["absentes_du_cadastre"]) == (2, 0)
    assert groupe["surface_totale_m2"] is None
    assert identifiants(groupe) == [G_A, G_SANS_SURFACE]


async def test_a_parcel_outside_the_bounds_is_counted_not_drawn_and_in_the_total(
    api, migrated
):
    scope(migrated)

    groupe = (await groupes_de(api))[HORS_EMPRISE]

    assert (groupe["total"], groupe["hors_emprise"]) == (2, 1)
    assert groupe["absentes_du_cadastre"] == 0
    assert identifiants(groupe) == [G_A]
    assert groupe["surface_totale_m2"] == 100 + 900
    assert G_LOIN not in str(groupe)


async def test_a_parcel_of_another_departement_is_found(api, migrated):
    scope(migrated)

    groupe = (await groupes_de(api))[AUTRE_DEPARTEMENT]

    assert (groupe["total"], groupe["absentes_du_cadastre"]) == (2, 0)
    assert groupe["hors_emprise"] == 0
    assert identifiants(groupe) == [G_AILLEURS, G_A]
    assert groupe["surface_totale_m2"] == 100 + 60


async def test_beyond_the_cap_the_parcels_are_not_served_and_the_counts_are(
    api, migrated, monkeypatch
):
    scope(migrated)
    plafonner(monkeypatch, 2)

    groupes = await groupes_de(api)

    assert groupes[TROIS] == {
        "total": 3,
        "autres": 2,
        "absentes_du_cadastre": 0,
        "hors_emprise": 0,
        "sans_surface": 0,
        "surface_totale_m2": 730,
        "parcelles": None,
    }
    # Two parcels to draw are within the cap.
    assert identifiants(groupes[AVEC_ABSENTE]) == [G_A, G_B]
    assert identifiants(groupes[HORS_EMPRISE]) == [G_A]


async def test_the_parcels_drawn_are_those_of_the_bounds_of_the_address_of_the_path(
    api, migrated
):
    """The same sale, seen from two addresses: each one draws its own side."""
    scope(migrated)
    large = {
        "emprise": "-180,-90,180,90",
        "ouest": "-180",
        "sud": "-90",
        "est": "180",
        "nord": "90",
        "plafond": "1",
    }

    d_ici = (await groupes_de(api))[HORS_EMPRISE]
    d_ici_avec_parametres = (await groupes_de(api, **large))[HORS_EMPRISE]
    de_loin = await ventes(api, LOIN, G_LOIN)

    assert d_ici_avec_parametres == d_ici
    (groupe,) = de_loin["groupes"]
    assert de_loin["ventes"][0]["date"] == HORS_EMPRISE
    assert identifiants(groupe) == [G_LOIN] and identifiants(d_ici) == [G_A]
    for vu in (groupe, d_ici):
        assert (vu["total"], vu["hors_emprise"], vu["surface_totale_m2"]) == (
            2,
            1,
            1000,
        )


async def test_the_groups_of_a_page_cost_two_statements_at_most(api, migrated):
    scope(migrated)

    def lus(table: str) -> int:
        return len(lectures(api, table))

    api.statements.clear()
    await ventes(api, ADRESSE, G_A)
    # The membership of the asked parcel, then the parcels of the groups.
    assert lus("parcelles") == 2
    liens = [
        sql
        for sql in lectures(api, "mutation_parcelles")
        if "mutations_grain" not in sql
    ]
    assert len(liens) == 1

    api.statements.clear()
    await ventes(api, ADRESSE, ISOLEE)
    # One sale of this parcel alone: no parcel is read again.
    assert lus("parcelles") == 1

    api.statements.clear()
    await ventes(api, ADRESSE, SANS_VENTE)
    assert lus("parcelles") == 1
    assert [
        sql
        for sql in lectures(api, "mutation_parcelles")
        if "mutations_grain" not in sql
    ] == []


async def test_a_withheld_price_of_a_group_never_leaves_the_api(api, migrated):
    scope(migrated)

    page = await api.client.get(chemin(ADRESSE, G_A))

    assert page.status_code == 200 and '"total":3' in page.text
    assert str(PRIX_TROIS) not in page.text
    retenue = page.json()["donnees"]["ventes"][1]
    assert (retenue["date"], retenue["prix"]) == (TROIS, None)
    assert retenue["minimisation"] == {"motif": "vente_isolee_recente"}


async def test_a_failed_read_of_the_parcels_of_a_group_is_a_503(
    api, migrated, monkeypatch
):
    """No group can be served: an outage, never sales without their groups."""
    from app.carte import ventes as module

    scope(migrated)
    en_panne = module._PARCELLES_DU_GROUPE.text.replace(
        "FROM parcelles p", "FROM parcelles_panne p"
    )
    assert en_panne != module._PARCELLES_DU_GROUPE.text
    monkeypatch.setattr(module, "_PARCELLES_DU_GROUPE", module.text(en_panne))

    probleme = await ventes(api, ADRESSE, G_A, 503)

    assert probleme["code"] == "ventes_indisponibles"
    # A page without any group to read is still served.
    assert (await ventes(api, ADRESSE, ISOLEE))["total"] == 1


async def test_a_real_page_has_exactly_the_key_paths_of_its_example(
    api, migrated, monkeypatch
):
    """The shared contract: sales shown and withheld, every case of a group."""
    scope(migrated)
    # As in the example, one group has more parcels to draw than the cap.
    plafonner(monkeypatch, 2)

    reponse = await api.client.get(chemin(ADRESSE, G_A))

    assert reponse.status_code == 200
    assert chemins(reponse.json()) == chemins(exemple(EXEMPLE))
    assert reponse.json()["meta"] == exemple(EXEMPLE)["meta"]


def test_with_statistics_the_three_reads_take_the_index_of_their_identifier(migrated):
    """The plan registry runs on a base without row nor statistic, where the
    parcels of a group asked for one departement are read by the primary key
    (``test_explain_statements_sql.py``). On this base, with its rows and its
    statistics, each read takes the index of the identifier it names. Sequential
    scans are turned off, as in the registry: a dozen rows would be read whole.
    No migration is needed; the real plans are measured on the work base."""
    from app.carte import ventes as module
    from scripts import explain_statements as ex

    emprise = emprise_de_deplacement(*point(TUILE, 0.5, 0.5))
    bornes = {
        "ouest": emprise.ouest,
        "sud": emprise.sud,
        "est": emprise.est,
        "nord": emprise.nord,
    }
    with psycopg.connect(migrated) as conn:
        conn.execute("ANALYZE parcelles")
        conn.execute("ANALYZE mutation_parcelles")
        conn.execute("SET enable_seqscan = off")

        def lus(enonce, parametres: dict) -> list[str]:
            plan = conn.execute(
                "EXPLAIN (FORMAT JSON) " + ex.en_psycopg(str(enonce)), parametres
            ).fetchone()[0]
            return ex.index_du_plan(plan)["index"]

        appartenance = lus(
            module._PARCELLE_DANS_L_EMPRISE,
            {"id_parcelle": G_A, "departement": "85", **bornes},
        )
        liens = lus(
            module._PARCELLES_DES_VENTES,
            {"ids": [mutation.id_mutation for mutation in VENTES_DE_G[:5]]},
        )
        un_departement = lus(
            module._PARCELLES_DU_GROUPE,
            {"ids": [G_A, G_B, G_C], "departements": ["85"], **bornes},
        )
        deux_departements = lus(
            module._PARCELLES_DU_GROUPE,
            {"ids": [G_A, G_AILLEURS], "departements": ["44", "85"], **bornes},
        )

    assert appartenance == ["parcelles_85_id_parcelle_idx"]
    assert liens == ["mutation_parcelles_id_mutation_id_parcelle_key"]
    assert un_departement == ["parcelles_85_id_parcelle_idx"]
    assert deux_departements == [
        "parcelles_44_id_parcelle_idx",
        "parcelles_85_id_parcelle_idx",
    ]
