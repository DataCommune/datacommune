"""Public DPE document by its source number, without a database (04-04).

``GET /v1/dpe/{numero}`` serves the ADEME number as is with its source
identity (contract V3): datasets from the validated observations only, a
catalogue URL per closed dataset, a retired document without description.
The problems are constant: neither a malformed input nor a number is
reflected, and no log line carries the number (LIEU-03, D-102, SEM-01).
"""

import logging
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import OperationalError

from app.common.schemas import DatasetHosting
from app.dpe import document_public, queries
from app.dpe import router as dpe_router
from app.scope.dependencies import RouteHosting

pytestmark = pytest.mark.unit

NUMERO = "2385E0123456A"
HOSTILE = "https://evil.example/phish"
_UNVERIFIED = {
    "status": "unverified",
    "kind": None,
    "revision_date": None,
    "verified_at": None,
    "evidence_sha256": None,
    "official_source": None,
}
_NO_SUCCESSOR = {
    "replacement_state": "no_successor_known",
    "graph_issues": [],
    "date_issues": [],
}


def _observation(slug: str, state: str = "present") -> dict:
    return {
        "source_slug": slug,
        "department": "85",
        "source_version": "a" * 64,
        "observed_at": "2026-09-25T20:30:00Z",
        "last_seen_at": "2026-09-20T20:30:00Z",
        "state": state,
        "import_id": "import-synthetique",
    }


def _row(**over) -> dict:
    row = {
        "id": 1,
        "numero_dpe": NUMERO,
        "numero_dpe_remplace": None,
        "type_batiment": "maison",
        "surface_habitable": Decimal("79.0"),
        "etiquette_dpe": "D",
        "etiquette_ges": "C",
        "date_etablissement_dpe": date(2026, 1, 5),
        "date_fin_validite_dpe": date(2036, 1, 4),
        "annee_construction": None,
        "periode_construction": "1948-1974",
        "type_installation_chauffage": "individuel",
        "type_energie_principale_chauffage": "Gaz naturel",
        "qualite_isolation_enveloppe": "moyenne",
        "conso_5_usages_ep": Decimal("17830.4"),
        "emission_ges_5_usages": Decimal("3433.1"),
        "cout_total_5_usages": Decimal("1834.2"),
        # C51 (04.1-12): the per-square-metre values published by the ADEME,
        # deliberately not the quotient of the totals by the surface (225.7, 43.5).
        "conso_5_usages_par_m2_ep": Decimal("231"),
        "emission_ges_5_usages_par_m2": Decimal("47.5"),
        "regime_dpe": None,
        "source_observations": [],
        "source_state": "unknown",
        "source_snapshot_at": None,
        "is_tombstone": False,
        "lifecycle_facts": dict(_NO_SUCCESSOR),
        "revision_evidence": dict(_UNVERIFIED),
    }
    row.update(over)
    return row


def _present(*slugs: str) -> dict:
    """A document observed present by the given datasets (validated provenance)."""
    return {
        "source_state": "present",
        "source_snapshot_at": "2026-09-26T00:00:00Z",
        # Observations are stored sorted by (dataset, departement).
        "source_observations": [_observation(slug) for slug in sorted(slugs)],
    }


class _Session:
    """Fake session: records the statement and serves the given rows."""

    def __init__(self, rows: list[dict] | None = None, error: Exception | None = None):
        self.rows = rows or []
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), dict(params or {})))
        if self.error is not None:
            raise self.error
        rows = self.rows

        class _Result:
            def mappings(self):
                class _Rows:
                    def all(self):
                        return rows

                return _Rows()

        return _Result()


def _hosting(dpe_departements=("44", "85"), *, dpe_heberge=True) -> RouteHosting:
    dpe = (
        DatasetHosting(etat="heberge", departements=list(dpe_departements))
        if dpe_heberge
        else DatasetHosting(etat="hors_couverture", motif="dataset_non_heberge")
    )
    return RouteHosting(
        {"dpe": dpe, "ban": DatasetHosting(etat="heberge", departements=["44", "85"])}
    )


async def _lire(row: dict, **kwargs) -> document_public.DocumentDpePublic:
    return await document_public.lire_document_public(
        _Session([row]), NUMERO, _hosting(**kwargs)
    )


# --- source identity (contract V3) ---------------------------------------------


@pytest.mark.parametrize(
    ("over", "datasets"),
    [
        (_present("dpe03existant"), ["dpe03existant"]),
        (_present("dpe02neuf"), ["dpe02neuf"]),
        # Both regimes established: both kept, existing then new, none chosen.
        (_present("dpe03existant", "dpe02neuf"), ["dpe03existant", "dpe02neuf"]),
        # Old record without observation: the explicit, known regime only.
        ({"regime_dpe": "neuf"}, ["dpe02neuf"]),
        # No established origin: empty list, number kept.
        ({}, []),
        ({"regime_dpe": "inconnu"}, []),
    ],
)
async def test_source_identity_names_the_established_datasets(over, datasets):
    document = await _lire(_row(**over))

    identity = document.source_identity
    assert identity.reference == NUMERO == document.numero
    assert [source.dataset for source in identity.sources] == datasets
    assert [source.url for source in identity.sources] == [
        f"https://data.ademe.fr/datasets/{dataset}" for dataset in datasets
    ]


async def test_no_source_url_comes_from_a_free_text():
    # A hostile URL in the free regime column and in a malformed observation.
    row = _row(
        regime_dpe=HOSTILE,
        source_state="present",
        source_snapshot_at="2026-09-26T00:00:00Z",
        source_observations=[{**_observation("dpe03existant"), "source_slug": HOSTILE}],
    )

    document = await _lire(row)

    assert document.source_identity.sources == []
    assert "evil.example" not in document.model_dump_json()


def test_a_source_url_other_than_the_catalogue_constant_is_refused():
    with pytest.raises(ValueError):
        document_public.SourceJeu(dataset="dpe03existant", url=HOSTILE)


# --- retired document (tombstone) ------------------------------------------------


async def test_a_retired_document_keeps_its_number_and_datasets_without_description():
    row = _row(
        is_tombstone=True,
        source_state="absent_from_complete_scope",
        source_snapshot_at="2026-09-26T00:00:00Z",
        source_observations=[
            _observation("dpe03existant", state="absent_from_complete_scope")
        ],
    )

    document = await _lire(row)

    assert document.disponibilite == "retire"
    assert document.numero == NUMERO
    assert [s.dataset for s in document.source_identity.sources] == ["dpe03existant"]
    assert document.dates is None and document.reperes is None
    assert document.methode == "inconnue"
    dumped = document.model_dump_json()
    for valeur in (
        "17830",
        "3433",
        "1834",
        "1948-1974",
        "Gaz naturel",
        "79.0",
        "231",
        "47.5",
    ):
        assert valeur not in dumped


def test_a_retired_document_cannot_carry_a_description():
    with pytest.raises(ValueError):
        document_public.DocumentDpePublic(
            numero=NUMERO,
            source_identity=document_public.SourceIdentity(
                reference=NUMERO, sources=[]
            ),
            disponibilite="retire",
            methode="inconnue",
            dates=document_public.DatesDocument(etablissement=date(2020, 1, 1)),
        )


# --- landmark ① and the method axis ----------------------------------------------


async def test_energy_landmark_is_the_declared_class_and_consumption():
    document = await _lire(_row())

    energie = document.reperes.etiquette_energie
    assert (energie.classe.etat, energie.classe.valeur) == ("value", "D")
    assert energie.classe.field_id == "dpe.document.etiquette_dpe"
    assert (energie.mesure.etat, energie.mesure.valeur) == ("value", 17830.4)
    assert energie.mesure.field_id == "dpe.document.conso_ep"
    # The stored ADEME column is a yearly total, never a per-square-metre value.
    assert energie.mesure.unite == "kwh_ep_per_year"
    assert energie.mesure.rattachement == "adresse_saisie_diagnostiqueur"
    assert energie.mesure.source.cle == "dpe"
    assert document.methode == "actuelle"
    assert document.dates.etablissement == date(2026, 1, 5)
    assert document.dates.fin_validite == date(2036, 1, 4)


async def test_an_empty_field_of_an_existing_document_is_not_reported():
    document = await _lire(_row(etiquette_dpe="N", conso_5_usages_ep=None))

    energie = document.reperes.etiquette_energie
    assert (energie.classe.etat, energie.classe.valeur) == ("not_reported", None)
    assert (energie.mesure.etat, energie.mesure.valeur) == ("not_reported", None)


# --- per-square-metre landmarks (C51, D-127, 04.1-12) ------------------------------
# Served next to the yearly totals under ``mesure_m2``: ``mesure`` keeps the
# total and its unit for the current page and both comparators.


async def test_per_square_metre_landmarks_are_the_published_columns():
    document = await _lire(_row())

    energie = document.reperes.etiquette_energie.mesure_m2
    assert (energie.field_id, energie.etat, energie.valeur) == (
        "dpe.document.conso_ep_m2",
        "value",
        231.0,
    )
    assert energie.unite == "kwh_ep_per_m2_per_year"
    climat = document.reperes.etiquette_climat.mesure_m2
    assert (climat.field_id, climat.etat, climat.valeur) == (
        "dpe.document.emissions_ges_m2",
        "value",
        47.5,
    )
    assert climat.unite == "kg_co2_eq_per_m2_per_year"
    for valeur in (energie, climat):
        assert valeur.rattachement == "adresse_saisie_diagnostiqueur"
        assert valeur.source.cle == "dpe"


async def test_the_yearly_totals_keep_their_identifier_and_unit():
    document = await _lire(_row())

    reperes = document.reperes
    assert (
        reperes.etiquette_energie.mesure.field_id,
        reperes.etiquette_energie.mesure.valeur,
        reperes.etiquette_energie.mesure.unite,
    ) == ("dpe.document.conso_ep", 17830.4, "kwh_ep_per_year")
    assert (
        reperes.etiquette_climat.mesure.field_id,
        reperes.etiquette_climat.mesure.valeur,
        reperes.etiquette_climat.mesure.unite,
    ) == ("dpe.document.emissions_ges", 3433.1, "kg_co2_eq_per_year")


async def test_a_missing_per_square_metre_value_is_never_computed_nor_zero():
    # Totals and surface are present: a division would give 225.7 and 43.5.
    document = await _lire(
        _row(conso_5_usages_par_m2_ep=None, emission_ges_5_usages_par_m2=None)
    )

    for repere in (
        document.reperes.etiquette_energie,
        document.reperes.etiquette_climat,
    ):
        assert (repere.mesure_m2.etat, repere.mesure_m2.valeur) == (
            "not_reported",
            None,
        )
        assert repere.mesure_m2.unite is None
        assert repere.mesure.etat == "value"


async def test_a_published_zero_per_square_metre_is_a_measured_zero():
    document = await _lire(_row(emission_ges_5_usages_par_m2=Decimal("0")))

    climat = document.reperes.etiquette_climat.mesure_m2
    assert (climat.etat, climat.valeur) == ("value", 0.0)


async def test_the_document_read_selects_the_per_square_metre_columns():
    session = _Session([_row()])

    await document_public.lire_document_public(session, NUMERO, _hosting())

    lecture = session.calls[0][0]
    assert "d.conso_5_usages_par_m2_ep" in lecture
    assert "d.emission_ges_5_usages_par_m2" in lecture


def test_the_per_square_metre_member_is_optional_and_never_public_as_a_column():
    champ = document_public.RepereEtiquette.model_fields["mesure_m2"]
    assert not champ.is_required() and champ.default is None
    noms = _proprietes(document_public.DocumentDpePublic)
    assert not noms & {"conso_5_usages_par_m2_ep", "emission_ges_5_usages_par_m2"}


@pytest.mark.parametrize(
    ("etabli", "methode"),
    [
        (date(2021, 6, 30), "ancienne"),
        (date(2021, 7, 1), "actuelle"),
        (None, "inconnue"),
    ],
)
async def test_the_method_axis_is_served_as_read(etabli, methode):
    document = await _lire(_row(date_etablissement_dpe=etabli))

    assert document.methode == methode


# --- reading: every served departement, never the digits of the number -----------


async def test_the_number_is_read_on_every_served_departement():
    session = _Session([_row()])

    await document_public.lire_document_public(session, NUMERO, _hosting())

    [(sql, params)] = session.calls
    # « 23 » + « 85 » in the number name no partition: both are read.
    assert params == {"numero": NUMERO, "departements": ["44", "85"]}
    flat = " ".join(sql.split())
    assert "d.numero_dpe = :numero" in flat
    assert "d.code_departement = ANY(CAST(:departements AS text[]))" in flat


async def test_unknown_number_names_the_served_departements():
    with pytest.raises(document_public.DpeInconnuError) as exc:
        await document_public.lire_document_public(_Session([]), NUMERO, _hosting())

    assert exc.value.departements == ["44", "85"]


async def test_dpe_not_hosted_reads_nothing_and_names_no_departement():
    session = _Session([_row()])

    with pytest.raises(document_public.DpeInconnuError) as exc:
        await document_public.lire_document_public(
            session, NUMERO, _hosting(dpe_heberge=False)
        )

    assert exc.value.departements == []
    assert session.calls == []


@pytest.mark.parametrize(
    "session",
    [
        _Session([_row(), _row(id=2)]),
        _Session(error=OperationalError("SELECT", {}, Exception("down"))),
    ],
    ids=["duplicate-number-never-chosen", "sql-failure"],
)
async def test_a_failed_or_ambiguous_read_is_unavailable(session):
    with pytest.raises(document_public.DocumentIndisponibleError):
        await document_public.lire_document_public(session, NUMERO, _hosting())


async def test_no_log_line_carries_the_number(caplog):
    caplog.set_level(logging.DEBUG)

    await _lire(_row())
    with pytest.raises(document_public.DpeInconnuError):
        await document_public.lire_document_public(_Session([]), NUMERO, _hosting())

    assert all(NUMERO not in record.getMessage() for record in caplog.records)


# --- HTTP: constant problems, no reflection, no-store ----------------------------


@pytest.mark.parametrize(
    "saisie",
    ["2385e0123456a", "2385E0123456", "2385E0123456AB", "2385E01234-6A"],
)
async def test_malformed_number_is_a_constant_422_without_reading(
    client, monkeypatch, saisie
):
    lecture = AsyncMock()
    monkeypatch.setattr(dpe_router, "lire_document_public", lecture)

    response = await client.get(f"/v1/dpe/{saisie}")

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "numero_mal_forme"
    assert body["instance"] == "/v1/dpe/{numero}"
    assert saisie not in response.text
    assert response.headers["cache-control"] == "no-store"
    lecture.assert_not_awaited()


async def test_unknown_number_is_a_404_naming_the_coverage_without_reflection(
    client, monkeypatch
):
    monkeypatch.setattr(
        dpe_router,
        "lire_document_public",
        AsyncMock(side_effect=document_public.DpeInconnuError(["44", "85"])),
    )

    response = await client.get(f"/v1/dpe/{NUMERO}")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["code"] == "dpe_inconnu"
    assert body["couverture"] == {"departements": ["44", "85"]}
    assert NUMERO not in response.text


async def test_unavailable_read_is_a_typed_503(client, monkeypatch):
    monkeypatch.setattr(
        dpe_router,
        "lire_document_public",
        AsyncMock(side_effect=document_public.DocumentIndisponibleError()),
    )

    response = await client.get(f"/v1/dpe/{NUMERO}")

    assert response.status_code == 503
    assert response.json()["code"] == "document_dpe_indisponible"
    assert response.headers["cache-control"] == "no-store"
    assert NUMERO not in response.text


async def test_served_document_is_never_cached(client, monkeypatch):
    document = await _lire(_row(**_present("dpe03existant")))
    monkeypatch.setattr(
        dpe_router, "lire_document_public", AsyncMock(return_value=document)
    )

    response = await client.get(f"/v1/dpe/{NUMERO}")

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    donnees = response.json()["donnees"]
    assert donnees["numero"] == NUMERO
    assert donnees["source_identity"] == {
        "reference": NUMERO,
        "sources": [
            {
                "dataset": "dpe03existant",
                "url": "https://data.ademe.fr/datasets/dpe03existant",
            }
        ],
    }
    assert donnees["association"] == "source_ban_equality"
    assert donnees["address_check"] == "unverified"


def test_the_route_declares_the_number_pattern_in_its_contract():
    from app.main import app

    operation = app.openapi()["paths"]["/v1/dpe/{numero}"]["get"]
    [parametre] = [p for p in operation["parameters"] if p["name"] == "numero"]
    assert parametre["schema"]["pattern"] == document_public.NUMERO_DPE_PATTERN


# --- Task 2: the whole document page -------------------------------------------

AS_OF = date(2026, 10, 4)


@pytest.fixture(autouse=True)
def _sans_provenance(monkeypatch):
    """The import date is read by its own savepointed helper (integration)."""
    monkeypatch.setattr(
        document_public, "date_import_dpe", AsyncMock(return_value=date(2026, 9, 30))
    )


async def test_the_four_landmarks_are_served_as_declared():
    document = await _lire(_row())

    reperes = document.reperes
    climat = reperes.etiquette_climat
    assert (climat.classe.field_id, climat.classe.valeur) == (
        "dpe.document.etiquette_ges",
        "C",
    )
    assert (climat.mesure.field_id, climat.mesure.valeur) == (
        "dpe.document.emissions_ges",
        3433.1,
    )
    # A yearly total of the document, never a per-square-metre value.
    assert climat.mesure.unite == "kg_co2_eq_per_year"
    logement = reperes.logement_decrit
    assert (logement.type_batiment.field_id, logement.type_batiment.valeur) == (
        "dpe.document.type_batiment",
        "maison",
    )
    assert (logement.surface.field_id, logement.surface.valeur) == (
        "dpe.document.surface_habitable",
        79.0,
    )
    # The cost as served: one value, no range invented.
    cout = reperes.cout_annuel
    assert (cout.field_id, cout.etat, cout.valeur, cout.unite) == (
        "dpe.document.cout_annuel",
        "value",
        1834.2,
        "euro_per_year",
    )


async def test_characteristics_are_the_served_columns_an_empty_one_not_reported():
    document = await _lire(_row(qualite_isolation_enveloppe=None))

    assert [c.field_id for c in document.caracteristiques] == [
        "dpe.document.type_batiment",
        "dpe.document.surface_habitable",
        "dpe.document.periode_construction",
        "dpe.document.energie_chauffage",
        "dpe.document.installation_chauffage",
        "dpe.document.isolation",
    ]
    valeurs = {c.field_id: (c.etat, c.valeur) for c in document.caracteristiques}
    assert valeurs["dpe.document.periode_construction"] == ("value", "1948-1974")
    assert valeurs["dpe.document.energie_chauffage"] == ("value", "Gaz naturel")
    assert valeurs["dpe.document.isolation"] == ("not_reported", None)


# Exhaustive table R02-05/6: every validity_at state, the method and both
# replacement outcomes; « En vigueur » is never a fallback.
def _facts(state="no_successor_known", graph=(), dates=()):
    return {
        "replacement_state": state,
        "graph_issues": list(graph),
        "date_issues": list(dates),
    }


@pytest.mark.parametrize(
    ("over", "remplacant", "attendu"),
    [
        ({}, None, ("en_vigueur", None, None)),
        (
            {
                "date_etablissement_dpe": date(2016, 5, 2),
                "date_fin_validite_dpe": date(2026, 5, 1),
            },
            None,
            ("expire", date(2026, 5, 1), None),
        ),
        ({"date_fin_validite_dpe": None}, None, ("validite_inconnue", None, None)),
        (
            {"date_etablissement_dpe": None, "date_fin_validite_dpe": None},
            None,
            ("validite_inconnue", None, None),
        ),
        (
            {
                "date_etablissement_dpe": date(2027, 1, 1),
                "date_fin_validite_dpe": date(2037, 1, 1),
            },
            None,
            ("etablissement_futur", None, None),
        ),
        (
            {"date_fin_validite_dpe": date(2020, 1, 1)},
            None,
            ("dates_incoherentes", None, None),
        ),
        (
            {"lifecycle_facts": _facts(dates=("invalid_end_date",))},
            None,
            ("dates_incoherentes", None, None),
        ),
        (
            {
                "date_etablissement_dpe": date(2021, 6, 30),
                "date_fin_validite_dpe": date(2031, 6, 29),
            },
            None,
            ("ancienne_methode", None, None),
        ),
        (
            {
                "date_etablissement_dpe": date(2014, 6, 30),
                "date_fin_validite_dpe": date(2024, 6, 29),
            },
            None,
            ("expire", date(2024, 6, 29), None),
        ),
        (
            {"lifecycle_facts": _facts("replaced")},
            "2485E0000009R",
            ("remplace", None, "2485E0000009R"),
        ),
        # Decision n° 24 (API WR-01): verified, successor not served.
        (
            {"lifecycle_facts": _facts("replaced")},
            None,
            ("remplace", None, None),
        ),
        (
            {"lifecycle_facts": _facts("replaced", graph=("fork",))},
            "2485E0000009R",
            ("remplacement_non_verifie", None, None),
        ),
        (
            {"lifecycle_facts": _facts("not_checked")},
            None,
            ("remplacement_non_verifie", None, None),
        ),
        (
            {"lifecycle_facts": _facts("ambiguous")},
            None,
            ("remplacement_non_verifie", None, None),
        ),
        (
            {"lifecycle_facts": {"replacement_state": "en_vigueur"}},
            None,
            ("remplacement_non_verifie", None, None),
        ),
    ],
    ids=[
        "en-vigueur",
        "expire",
        "fin-inconnue",
        "dates-inconnues",
        "etablissement-futur",
        "fin-avant-etablissement",
        "date-source-invalide",
        "ancienne-methode",
        "ancienne-methode-expiree",
        "remplacement-verifie",
        "remplacant-introuvable",
        "graphe-en-fourche",
        "remplacement-non-verifie",
        "remplacement-ambigu",
        "faits-malformes",
    ],
)
def test_status_table_is_exhaustive_without_fallback(over, remplacant, attendu):
    record = queries._record_from_mapping(_row(**over))

    statut = document_public.statut_validite(record, AS_OF, remplacant)

    assert (statut.code, statut.date, statut.remplace_par) == attendu


def test_the_validity_axis_is_served_apart_from_the_status_code():
    # Replaced (verified) and expired: the code says the replacement, the
    # calendar axis still says expired (D-111: three distinct axes).
    record = queries._record_from_mapping(
        _row(
            date_etablissement_dpe=date(2016, 5, 2),
            date_fin_validite_dpe=date(2026, 5, 1),
            lifecycle_facts=_facts("replaced"),
        )
    )

    statut = document_public.statut_validite(record, AS_OF, "2485E0000009R")

    assert (statut.code, statut.validite) == ("remplace", "expired")


def test_the_replaced_document_is_served_as_the_source_declares_it():
    record = queries._record_from_mapping(_row(numero_dpe_remplace="2185E0000001A"))

    statut = document_public.statut_validite(record, AS_OF, None)

    assert statut.remplace == "2185E0000001A"


async def test_successor_is_looked_up_only_for_a_replaced_document():
    session = _Session([_row()])

    await document_public.lire_document_public(session, NUMERO, _hosting())

    assert not any("numero_dpe_remplace = :numero" in sql for sql, _ in session.calls)


def _ban(**over):
    ligne = {
        "numero": "12",
        "rep": None,
        "nom_voie": "Rue des Lilas",
        "code_postal": "85000",
        "code_commune": "85191",
        "nom_commune": "La Roche-sur-Yon",
    }
    ligne.update(over)
    return ligne


def test_ban_address_retained_by_the_source():
    adresse = document_public.adresse_ban_de_lignes("85191_0420_00012", [_ban()])

    assert adresse.etat == "retenue"
    assert adresse.ban_id == "85191_0420_00012"
    assert adresse.libelle == "12 Rue des Lilas, 85000 La Roche-sur-Yon"
    assert (adresse.numero, adresse.rep, adresse.nom_voie) == (
        "12",
        None,
        "Rue des Lilas",
    )
    assert adresse.commune.model_dump() == {
        "code_insee": "85191",
        "nom": "La Roche-sur-Yon",
    }


def test_contradictory_ban_rows_keep_the_key_without_a_label():
    adresse = document_public.adresse_ban_de_lignes(
        "85191_0420_00012", [_ban(), _ban(nom_voie="Rue des Roses")]
    )

    assert (adresse.etat, adresse.ban_id, adresse.libelle) == (
        "retenue",
        "85191_0420_00012",
        None,
    )
    assert adresse.nom_voie is None


def test_a_key_absent_from_the_ban_is_not_retained():
    adresse = document_public.adresse_ban_de_lignes("85191_0420_00012", [])

    assert (adresse.etat, adresse.ban_id, adresse.libelle) == (
        "cle_inconnue",
        None,
        None,
    )


async def test_a_document_without_ban_key_has_no_retained_address():
    document = await _lire(_row())

    assert document.adresse_ban.etat == "sans_cle"
    assert document.adresse_ban.ban_id is None
    assert document.adresse_declaree.etat == "absente"
    assert document.adresse_divergente is None


async def test_ban_not_hosted_for_the_departement_is_out_of_coverage(monkeypatch):
    lecture = AsyncMock()
    monkeypatch.setattr(document_public, "lire_lignes_ban", lecture)
    row = _row(
        identifiant_ban="85191_0420_00012",
        code_departement="85",
        adresse_brut="12 rue des Lilas",
    )
    hosting = RouteHosting(
        {
            "dpe": DatasetHosting(etat="heberge", departements=["44", "85"]),
            "ban": DatasetHosting(etat="heberge", departements=["44"]),
        }
    )

    document = await document_public.lire_document_public(
        _Session([row]), NUMERO, hosting
    )

    assert (document.adresse_ban.etat, document.adresse_ban.motif) == (
        "hors_couverture",
        # The departement is served for the DPE, so in the scope: the BAN
        # source does not cover it.
        "source_sans_couverture",
    )
    lecture.assert_not_awaited()
    # Nothing verifiable: masked, never published unverified.
    assert document.adresse_declaree.etat == "masquee"


async def test_ban_outage_keeps_the_document_and_types_the_match(monkeypatch):
    monkeypatch.setattr(
        document_public,
        "lire_lignes_ban",
        AsyncMock(side_effect=document_public.BanIndisponibleError()),
    )
    row = _row(identifiant_ban="85191_0420_00012", code_departement="85")

    document = await _lire(row)

    assert document.adresse_ban.etat == "indisponible"
    assert document.reperes.etiquette_energie.classe.valeur == "D"
    assert document.adresse_declaree.etat == "absente"


async def test_a_retired_document_carries_no_address_nor_characteristic():
    row = _row(
        identifiant_ban="85191_0420_00012",
        code_departement="85",
        adresse_brut="12 rue des Lilas",
        is_tombstone=True,
        source_state="absent_from_complete_scope",
        source_snapshot_at="2026-09-26T00:00:00Z",
        source_observations=[
            _observation("dpe03existant", state="absent_from_complete_scope")
        ],
    )

    document = await _lire(row)

    assert document.statut.code == "retire"
    assert document.caracteristiques == []
    assert document.adresse_declaree is None and document.adresse_ban is None
    assert "Lilas" not in document.model_dump_json()


async def test_provenance_is_the_source_and_its_last_successful_import():
    document = await _lire(_row())

    assert document.provenance.model_dump() == {
        "source": "ADEME",
        "date_import": date(2026, 9, 30),
    }


def _proprietes(model) -> set[str]:
    noms: set[str] = set()
    pile: list = [model.model_json_schema()]
    while pile:
        noeud = pile.pop()
        if isinstance(noeud, dict):
            if isinstance(noeud.get("properties"), dict):
                noms.update(noeud["properties"])
            pile.extend(noeud.values())
        elif isinstance(noeud, list):
            pile.extend(noeud)
    return noms


def test_no_dwelling_sale_or_commune_datum_in_the_schema():
    # « Logement décrit par le document » (UI-SPEC landmark ③) names what the
    # document describes, not a dwelling of the base: the only allowed member.
    noms = _proprietes(document_public.DocumentDpePublic) - {"logement_decrit"}

    for interdit in ("logement", "vente", "mutation", "prix", "dvf", "synthese"):
        assert not [nom for nom in noms if interdit in nom], interdit
    # The raw address columns never become a public member.
    assert not noms & {
        "adresse_brut",
        "adresse_complete_brut",
        "complement_adresse_logement",
        "identifiant_ban",
    }


async def test_the_publication_guard_of_the_document_route_is_never_cached(
    monkeypatch,
):
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
            "path": f"/v1/dpe/{NUMERO}",
            "query_string": b"",
        }
    )

    with pytest.raises(HTTPException) as exc:
        await readiness.require_publication(request, object())

    assert exc.value.status_code == 503
    assert exc.value.headers == {"Cache-Control": "no-store"}


@pytest.mark.parametrize(
    ("concordance", "attendu"),
    [("ecart", True), ("concordant", False), ("non_evaluable", None), (None, None)],
)
def test_divergence_is_served_for_a_decided_concordance_only(concordance, attendu):
    from app.dpe.document_public import divergence

    assert divergence(concordance) is attendu


# --- API WR-01: one wording of a replaced document on every surface -----------


@pytest.mark.parametrize(
    ("faits", "carte", "selection", "page"),
    [
        (_facts("replaced"), "remplace", "remplace", "remplace"),
        (
            _facts("replaced", graph=("fork",)),
            "remplacement_incertain",
            "remplacement_incertain",
            "remplacement_non_verifie",
        ),
        (
            _facts("ambiguous"),
            "remplacement_incertain",
            "remplacement_incertain",
            "remplacement_non_verifie",
        ),
        (
            _facts("not_checked"),
            "remplacement_incertain",
            "remplacement_incertain",
            "remplacement_non_verifie",
        ),
    ],
    ids=["verifie", "fourche", "ambigu", "non_verifie"],
)
def test_a_replaced_document_has_one_wording_on_card_selection_and_page(
    faits, carte, selection, page
):
    """Same document → same fact on the card, the selection and the page."""
    from app.adresse import documents

    record = queries._record_from_mapping(_row(lifecycle_facts=faits))
    doc = document_public._document_adresse(record)
    statut = documents.statut_document(doc, AS_OF)

    badges = documents.badges_statut(statut)
    choisi = documents.choisir_plus_recent([doc], AS_OF)
    code = document_public.statut_validite(record, AS_OF, None).code

    assert [b for b in badges if b.startswith("remplace")] == [carte]
    assert (choisi.etat, choisi.motif) == ("unique", selection)
    assert code == page
