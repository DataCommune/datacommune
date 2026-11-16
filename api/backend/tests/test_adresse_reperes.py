"""Landmarks of the address page, without a database (04-03, D-113).

``lire_bloc`` turns a refused publication, a SQL error or an upstream outage
into an outage of its block only; the clay landmark says what is measured at
the point and nothing else (never a count, never a zero); the parcel sales
landmark keeps every state of the parcel relation apart (N-02, N-03, N-14).
"""

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import ProgrammingError

from app.adresse import blocs, documents, risques, service
from app.adresse.relations import PointBan
from app.adresse.schemas import RelationParcelle, SelectionPlusRecent
from app.common.exceptions import UpstreamUnavailableError
from app.common.schemas import DatasetHosting, OutOfCoverageReason
from app.datasets.provenance import ProvenanceFacts
from app.scope.dependencies import RouteHosting

pytestmark = pytest.mark.unit

AS_OF = date(2026, 10, 2)
POINT = PointBan(lon=-1.43, lat=46.67)
HEBERGE = DatasetHosting(etat="heberge", departements=["85"])
NON_HEBERGE = DatasetHosting(
    etat="hors_couverture", motif=OutOfCoverageReason.DATASET_NON_HEBERGE
)


def _hosting(**out: DatasetHosting) -> RouteHosting:
    keys = ("ban", "cadastre", "dpe", "dvf", "georisques", "commune")
    return RouteHosting({key: out.get(key, HEBERGE) for key in keys})


class _Session:
    """Fake session: savepoints are recorded, nothing is executed."""

    def __init__(self) -> None:
        self.savepoints = 0
        self.rolled_back = 0

    @asynccontextmanager
    async def begin_nested(self):
        self.savepoints += 1
        try:
            yield
        except BaseException:
            self.rolled_back += 1
            raise


# --- lire_bloc ----------------------------------------------------------------


@pytest.fixture
def guard(monkeypatch):
    calls: list[list[str]] = []

    async def fake_guard(session, datasets):
        calls.append(list(datasets))

    monkeypatch.setattr(blocs, "guard_publication", fake_guard)
    return calls


def test_journaled_sources_are_the_published_datasets():
    # 04.7-01 (B1): every source the routes guard, so that a block reading
    # a communal source takes its guard too; the page's three are in it.
    from app.common.publication_readiness import PUBLICATION_DATASETS

    assert blocs.SOURCES_JOURNALISEES == PUBLICATION_DATASETS
    assert {"dpe", "dvf", "connectivite"} <= blocs.SOURCES_JOURNALISEES
    assert not {"ban", "cadastre", "georisques", "commune"} & blocs.SOURCES_JOURNALISEES


async def test_a_journaled_block_is_guarded_inside_its_savepoint(guard):
    session = _Session()

    bloc = await blocs.lire_bloc(session, ["dvf"], AsyncMock(return_value=3))

    assert (bloc.valeur, bloc.panne) == (3, None)
    assert guard == [["dvf"]]
    assert session.savepoints == 1


@pytest.mark.parametrize("sources", [["georisques"], ["commune"], ["copropriete"]])
async def test_a_source_without_journal_takes_no_publication_lock(guard, sources):
    bloc = await blocs.lire_bloc(_Session(), sources, AsyncMock(return_value="x"))

    assert bloc.valeur == "x"
    assert guard == []


async def test_a_refused_publication_is_an_outage_of_its_block(monkeypatch):
    async def refused(session, datasets):
        raise HTTPException(503, "Données temporairement indisponibles.")

    monkeypatch.setattr(blocs, "guard_publication", refused)
    lecteur = AsyncMock()
    session = _Session()

    bloc = await blocs.lire_bloc(session, ["dpe"], lecteur)

    assert (bloc.en_panne, bloc.panne) == (True, "publication_non_validee")
    lecteur.assert_not_called()
    assert session.rolled_back == 1


@pytest.mark.parametrize(
    ("error", "cause"),
    [
        (
            ProgrammingError("SELECT", {}, Exception("relation absente")),
            "lecture_indisponible",
        ),
        (UpstreamUnavailableError("Georisques API"), "amont_indisponible"),
    ],
)
async def test_a_failed_read_is_an_outage_of_its_block(guard, error, cause):
    session = _Session()

    bloc = await blocs.lire_bloc(session, ["dvf"], AsyncMock(side_effect=error))

    assert (bloc.valeur, bloc.panne) == (None, cause)
    assert session.rolled_back == 1


async def test_a_non_outage_error_is_not_swallowed(guard):
    with pytest.raises(HTTPException):
        await blocs.lire_bloc(
            _Session(), ["dvf"], AsyncMock(side_effect=HTTPException(404))
        )


# --- period of a value read from an imported source (C14, SEM-02) ---------------

# 2026-09-25 22:38 UTC is already the 26th in Paris: the day of an import is a
# day of the instance, the one `/v1/adresse/{ban_id}/dpe` serves as `date_import`.
IMPORT_TARDIF = datetime(2026, 9, 25, 22, 38, tzinfo=UTC)


@pytest.mark.parametrize(
    ("faits", "attendu"),
    [
        (None, None),
        (ProvenanceFacts(), None),
        (
            ProvenanceFacts(
                source_period_label="Export RNB 2026-09",
                source_updated_at=datetime(2026, 9, 21, tzinfo=UTC),
                last_success_at=IMPORT_TARDIF,
            ),
            "Export RNB 2026-09",
        ),
        (
            ProvenanceFacts(
                source_updated_at=datetime(2026, 9, 21, tzinfo=UTC),
                last_success_at=IMPORT_TARDIF,
            ),
            "2026-09-21",
        ),
        (ProvenanceFacts(last_success_at=IMPORT_TARDIF), "2026-09-26"),
    ],
    ids=["inconnu", "vide", "millesime", "date_amont", "import"],
)
def test_the_period_of_a_source_is_its_millesime_else_its_import_never_invented(
    faits, attendu
):
    assert blocs.periode_de_source(faits) == attendu


async def _repere_dpe(monkeypatch, faits, hosting=None):
    agregat = SimpleNamespace(total=3)
    selection = SelectionPlusRecent(etat="indeterminee", motif="aucun_document_date")
    monkeypatch.setattr(
        documents,
        "_documents_et_selection",
        AsyncMock(return_value=(agregat, selection)),
    )
    lu = AsyncMock(return_value=faits)
    monkeypatch.setattr(documents, "faits_de_provenance", lu)
    repere = await documents.repere_documents(
        _Session(), "85191_0420_00012", hosting or _hosting(), AS_OF
    )
    return repere, lu


async def test_landmark_one_is_dated_by_the_last_dpe_import(guard, monkeypatch):
    repere, lu = await _repere_dpe(
        monkeypatch, ProvenanceFacts(last_success_at=IMPORT_TARDIF)
    )

    assert (repere.etat, repere.valeur, repere.periode) == ("value", 3, "2026-09-26")
    lu.assert_awaited_once_with(ANY, "dpe")


async def test_landmark_one_without_a_known_import_serves_no_period(guard, monkeypatch):
    repere, _ = await _repere_dpe(monkeypatch, None)

    assert (repere.etat, repere.valeur, repere.periode) == ("value", 3, None)


async def test_landmark_one_out_of_coverage_reads_no_period(guard, monkeypatch):
    repere, lu = await _repere_dpe(
        monkeypatch,
        ProvenanceFacts(last_success_at=IMPORT_TARDIF),
        hosting=_hosting(dpe=NON_HEBERGE),
    )

    assert (repere.etat, repere.periode) == ("out_of_coverage", None)
    lu.assert_not_called()


# --- the table of phenomena (N-03) ---------------------------------------------


def test_only_the_clay_phenomenon_is_measured_at_the_point():
    assert risques.PHENOMENES["argiles"].portees == ("au_point",)
    assert risques.PHENOMENES["gaspar"].portees == ("commune",)
    assert risques.PHENOMENES["radon"].portees == ("commune",)
    assert risques.PHENOMENES["icpe"].portees == ("commune", "proximite")
    assert risques.phenomenes_au_point() == ("argiles",)


@pytest.mark.parametrize(
    ("bloc", "attendu"),
    [
        ({"exposition": "Exposition moyenne", "niveau": "2"}, "Exposition moyenne"),
        ({"exposition": None, "niveau": "Faible"}, "Faible"),
        ({"libelle": "Exposition forte"}, "Exposition forte"),
        ({"exposition": "  ", "code": "0"}, None),
        ({"code": "2"}, None),
        (None, None),
    ],
)
def test_clay_exposure_is_read_from_the_rga_block_only(bloc, attendu):
    assert risques.exposition_argiles(bloc) == attendu


async def _argiles(monkeypatch, bloc=None, *, error=None, hosting=None, point=POINT):
    fake = AsyncMock(return_value=bloc, side_effect=error)
    monkeypatch.setattr(risques, "query_rga", fake)
    valeur = await risques.repere_argiles_au_point(point, hosting or _hosting(), AS_OF)
    return valeur, fake


async def test_clay_exposure_at_the_point_is_served(monkeypatch):
    valeur, fake = await _argiles(
        monkeypatch, {"codeExposition": "2", "exposition": "Exposition moyenne"}
    )

    assert valeur.field_id == "adresse.risques.argiles_au_point"
    assert (valeur.etat, valeur.valeur) == ("value", "Exposition moyenne")
    assert valeur.rattachement == "au_point"
    assert valeur.source.cle == "georisques"
    assert valeur.periode == "2026-10-02"
    # The point only: no INSEE code, hence no communal phenomenon (API CR-01).
    fake.assert_awaited_once_with(POINT.lat, POINT.lon)


async def test_an_rga_answer_without_exposure_is_never_a_zero(monkeypatch):
    valeur, _ = await _argiles(monkeypatch, {"exposition": None, "code": None})

    assert valeur.etat == "not_reported"
    assert valeur.valeur is None
    assert ":0" not in valeur.model_dump_json().replace(" ", "")


async def test_an_empty_rga_answer_is_not_reported_never_an_outage(monkeypatch):
    valeur, _ = await _argiles(monkeypatch, None)

    assert valeur.etat == "not_reported"
    assert valeur.motif is None
    assert valeur.periode == "2026-10-02"


async def test_an_rga_answer_without_its_keys_is_an_outage(monkeypatch):
    valeur, _ = await _argiles(monkeypatch, {"message": "inattendu"})

    assert (valeur.etat, valeur.motif) == ("temporary_error", "amont_indisponible")


async def test_georisques_down_is_an_outage(monkeypatch):
    valeur, _ = await _argiles(
        monkeypatch, error=UpstreamUnavailableError("Georisques API")
    )

    assert (valeur.etat, valeur.motif) == ("temporary_error", "amont_indisponible")


async def test_georisques_not_hosted_is_out_of_coverage_without_a_call(monkeypatch):
    valeur, fake = await _argiles(monkeypatch, hosting=_hosting(georisques=NON_HEBERGE))

    assert (valeur.etat, valeur.motif) == ("out_of_coverage", "dataset_non_heberge")
    fake.assert_not_called()


async def test_no_single_point_is_a_gap_without_a_call(monkeypatch):
    valeur, fake = await _argiles(monkeypatch, point=None)

    assert (valeur.etat, valeur.motif) == ("not_available", "position_indeterminee")
    fake.assert_not_called()


# --- parcel sales (N-02) -------------------------------------------------------


async def _ventes(monkeypatch, relation, *, hosting=None, compte=None, faits=None):
    fake = AsyncMock(return_value=compte or {"nombre": 0, "derniere": None})
    monkeypatch.setattr(service, "dvf_compte_par_id_parcelle", fake)
    monkeypatch.setattr(service, "faits_de_provenance", AsyncMock(return_value=faits))

    async def no_guard(session, datasets):
        return None

    monkeypatch.setattr(blocs, "guard_publication", no_guard)
    ventes = await service.repere_ventes(_Session(), relation, hosting or _hosting())
    return ventes, fake


@pytest.mark.parametrize(
    ("relation", "etat", "motif"),
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
            RelationParcelle(etat="hors_couverture", motif="dataset_non_heberge"),
            "out_of_coverage",
            "dataset_non_heberge",
        ),
        (
            RelationParcelle(etat="indisponible"),
            "temporary_error",
            "lecture_indisponible",
        ),
        (
            RelationParcelle(etat="indeterminee", motif="positions_contradictoires"),
            "not_available",
            "positions_contradictoires",
        ),
    ],
)
async def test_a_parcel_relation_that_is_not_unique_reads_no_sale(
    monkeypatch, relation, etat, motif
):
    ventes, fake = await _ventes(monkeypatch, relation)

    for valeur in (ventes.nombre, ventes.derniere):
        assert (valeur.etat, valeur.motif) == (etat, motif)
        assert valeur.valeur is None
    fake.assert_not_called()


async def test_dvf_not_hosted_is_out_of_coverage_without_reading(monkeypatch):
    relation = RelationParcelle(
        etat="unique", id_parcelle="85191000AB0012", rattachement="parcelle_du_point"
    )

    ventes, fake = await _ventes(
        monkeypatch, relation, hosting=_hosting(dvf=NON_HEBERGE)
    )

    assert (ventes.nombre.etat, ventes.nombre.motif) == (
        "out_of_coverage",
        "dataset_non_heberge",
    )
    fake.assert_not_called()


async def test_zero_sale_on_the_unique_parcel_is_a_measured_zero(monkeypatch):
    relation = RelationParcelle(
        etat="unique", id_parcelle="85191000AB0012", rattachement="parcelle_du_point"
    )

    ventes, fake = await _ventes(monkeypatch, relation)

    assert (ventes.nombre.etat, ventes.nombre.valeur) == ("value", 0)
    assert ventes.nombre.rattachement == "parcelle_du_point"
    assert ventes.nombre.source.cle == "dvf"
    assert (ventes.derniere.etat, ventes.derniere.motif) == (
        "not_available",
        "aucune_vente",
    )
    fake.assert_awaited_once()


@pytest.mark.parametrize(
    ("faits", "attendu"),
    [
        (ProvenanceFacts(last_success_at=IMPORT_TARDIF), "2026-09-26"),
        (
            ProvenanceFacts(
                source_period_label="2021-01-02 → 2025-12-31",
                last_success_at=IMPORT_TARDIF,
            ),
            "2021-01-02 → 2025-12-31",
        ),
        (None, None),
    ],
    ids=["sans_libelle", "libelle", "inconnu"],
)
async def test_parcel_sales_without_a_declared_label_carry_the_dvf_import_day(
    monkeypatch, faits, attendu
):
    """C24 (b), WINDOWS n° 86: the label wins, else C14; nothing invented."""
    relation = RelationParcelle(
        etat="unique", id_parcelle="85191000AB0012", rattachement="parcelle_du_point"
    )

    ventes, _ = await _ventes(
        monkeypatch,
        relation,
        compte={"nombre": 2, "derniere": date(2025, 3, 1)},
        faits=faits,
    )

    assert (ventes.nombre.valeur, ventes.nombre.periode) == (2, attendu)
    assert (ventes.derniere.valeur, ventes.derniere.periode) == ("2025-03-01", attendu)


# --- API CR-01: Géorisques between two transactions -----------------------------


async def test_the_identity_transaction_ends_before_georisques_is_called(monkeypatch):
    """No publication lock nor pool connection held during the upstream call."""
    journal: list[str] = []

    class _Fiche:
        couverture = type("C", (), {"etat": "heberge"})()
        identite = type(
            "I",
            (),
            {"commune": type("K", (), {"code_insee": "85191"})(), "parcelle": None},
        )()

        def model_copy(self, update):
            return update

    class _Transaction:
        async def rollback(self):
            journal.append("rollback")

    async def identite(session, ban_id, hosting):
        journal.append("identite")
        return _Fiche(), POINT

    async def rga(lat, lon):
        journal.append("georisques")
        return {"exposition": "Exposition moyenne"}

    def bloc(nom):
        async def lire(*_args, **_kwargs):
            journal.append(nom)
            return None

        return lire

    monkeypatch.setattr(service, "lire_identite", identite)
    monkeypatch.setattr(risques, "query_rga", rga)
    monkeypatch.setattr(service, "repere_documents", bloc("dpe"))
    monkeypatch.setattr(service, "repere_ventes", bloc("ventes"))
    monkeypatch.setattr(service, "repere_reference_commune", bloc("reference"))
    monkeypatch.setattr(service, "ReperesAdresse", lambda **kw: kw)

    # 04-06: the upstreams of the themes are read with ③, before any block;
    # the themes read after the landmarks.
    async def amont(point, code_insee, hosting):
        journal.append("amont")

    async def themes(*_args, **_kwargs):
        journal.append("themes")
        return []

    monkeypatch.setattr(service, "lire_amont_lieu", amont)
    monkeypatch.setattr(service, "lire_themes_lieu", themes)

    reperes = await service.lire_fiche_adresse(
        _Transaction(), "85191_0420_00012", _hosting(), None, AS_OF
    )

    assert journal == [
        "identite",
        "rollback",
        "georisques",
        "amont",
        "dpe",
        "ventes",
        "reference",
        "themes",
    ]
    assert reperes["reperes"]["argiles"].etat == "value"
