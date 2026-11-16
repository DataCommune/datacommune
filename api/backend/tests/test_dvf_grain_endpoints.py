"""CI guards for the Phase 57 mutation-grain DVF contract.

These tests intentionally use a capture-only session: they exercise SQL shape,
bound parameters and Pydantic validation without opening a database connection.
"""

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from app.dvf import queries as dvf_queries
from app.dvf.schemas import ResultatDvf

pytestmark = pytest.mark.unit


class _FakeResult:
    def __iter__(self):
        return iter(())


class _CaptureSession:
    def __init__(self) -> None:
        self.sql = ""
        self.params: dict[str, Any] = {}

    async def execute(self, sql, params):
        self.sql = str(sql)
        self.params = params
        return _FakeResult()


QueryCall = Callable[[_CaptureSession], Awaitable[list[dict]]]


async def _point(session: _CaptureSession) -> list[dict]:
    return await dvf_queries.dvf_par_point(
        session,
        47.123456789,
        -1.987654321,
        rayon_m=733.33331,
        annee_min=1873,
        annee_max=1964,
        prix_min=123456.789,
        prix_max=987654.321,
        limit=9871,
        offset=9883,
    )


async def _adresse(session: _CaptureSession) -> list[dict]:
    return await dvf_queries.dvf_par_adresse(
        session,
        46.234567891,
        -2.876543219,
        rayon_m=811.11113,
        annee_min=1861,
        annee_max=1951,
        prix_min=234567.891,
        prix_max=876543.219,
        limit=9887,
        offset=9901,
    )


async def _id_parcelle(session: _CaptureSession) -> list[dict]:
    return await dvf_queries.dvf_par_id_parcelle(
        session, "44109000EN0179-injection-'--", limit=37
    )


async def _parcelle(session: _CaptureSession) -> list[dict]:
    return await dvf_queries.dvf_par_parcelle(
        session, 45.345678912, -3.765432198, limit=9907
    )


async def _unique_isolee(session: _CaptureSession) -> list[dict]:
    return await dvf_queries.dvf_mutation_unique_isolee(
        session,
        ["44-injection-'--", "85/*probe*/"],
        fenetre_mois=9913,
        limit=9923,
    )


QUERY_CALLS: tuple[tuple[str, QueryCall], ...] = (
    ("point", _point),
    ("adresse", _adresse),
    ("id_parcelle", _id_parcelle),
    ("parcelle", _parcelle),
    ("unique_isolee", _unique_isolee),
)


@pytest.mark.parametrize(("name", "query_call"), QUERY_CALLS)
async def test_each_query_reads_mutations_grain(name: str, query_call: QueryCall):
    session = _CaptureSession()
    await query_call(session)

    assert "mutations_grain" in session.sql, name
    # A bare line-grain FROM/JOIN is forbidden; the mono-local metadata
    # subquery may still inspect `mutations m2` without changing result grain.
    assert "FROM mutations\n" not in session.sql, name
    assert "JOIN mutations\n" not in session.sql, name


@pytest.mark.parametrize(("name", "query_call"), QUERY_CALLS)
async def test_no_query_divides_by_line_surface(name: str, query_call: QueryCall):
    session = _CaptureSession()
    await query_call(session)

    normalized = " ".join(session.sql.lower().split())
    assert "valeur_fonciere / nullif(surface_reelle_bati" not in normalized, name
    assert "valeur_fonciere / surface_reelle_bati" not in normalized, name


@pytest.mark.parametrize(("name", "query_call"), QUERY_CALLS)
async def test_all_user_values_are_bound_for_each_query(
    name: str, query_call: QueryCall
):
    """T-57-37: public values stay in params and never enter SQL text."""
    session = _CaptureSession()
    await query_call(session)

    assert session.params, name
    for value in session.params.values():
        candidates = value if isinstance(value, list) else [value]
        for candidate in candidates:
            assert str(candidate) not in session.sql, (name, candidate, session.sql)


async def test_id_parcelle_uses_partition_pruning_and_bound_identity():
    session = _CaptureSession()
    parcel_id = "97123000AB0042-injection-'--"
    await dvf_queries.dvf_par_id_parcelle(session, parcel_id, limit=29)

    assert "WHEN LEFT(:id_parcelle, 2) = '97'" in session.sql
    assert "THEN LEFT(:id_parcelle, 3)" in session.sql
    assert "mp.id_parcelle = :id_parcelle" in session.sql
    assert session.params == {"id_parcelle": parcel_id, "limit": 29}
    assert parcel_id not in session.sql


@pytest.mark.parametrize(
    "query",
    (dvf_queries.dvf_par_point, dvf_queries.dvf_par_adresse),
)
async def test_appartement_filter_is_mapped_and_bound(query):
    session = _CaptureSession()
    await query(
        session,
        48.123456781,
        2.876543219,
        type_local="Appartement",
    )

    assert "mg.classe_mutation = :classe_mutation" in session.sql
    assert session.params["classe_mutation"] == "residentiel_appartement"
    assert "mg.classe_mutation = 'residentiel_appartement'" not in session.sql


@pytest.mark.parametrize(
    "query",
    (dvf_queries.dvf_par_point, dvf_queries.dvf_par_adresse),
)
async def test_unknown_type_local_fails_closed_without_interpolation(query):
    session = _CaptureSession()
    malicious = "Appartement' OR TRUE --"
    await query(session, 48.2, 2.3, type_local=malicious)

    assert "AND FALSE" in session.sql
    assert "classe_mutation" not in session.params
    assert malicious not in session.sql


def _base_result(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id_mutation": "2024-000001",
        "date_mutation": date(2024, 5, 2),
        "longitude": -1.55,
        "latitude": 47.22,
    }
    payload.update(overrides)
    return payload


def test_schema_accepts_honest_mono_local_price():
    result = ResultatDvf.model_validate(
        _base_result(
            classe_mutation="residentiel_appartement",
            mutation_mono_local=True,
            prix_m2=5385.0,
            prix_m2_raison=None,
            nb_locaux_appartement=1,
            surface_bati_totale=65,
        )
    )

    assert result.prix_m2 == 5385.0
    assert result.prix_m2_raison is None
    assert result.mutation_mono_local is True


def test_schema_accepts_mixed_mutation_only_with_null_price_and_reason():
    result = ResultatDvf.model_validate(
        _base_result(
            classe_mutation="mixte",
            mutation_mono_local=False,
            prix_m2=None,
            prix_m2_raison="vente_mixte",
            nb_locaux_appartement=1,
            nb_locaux_commercial=1,
        )
    )

    assert result.prix_m2 is None
    assert result.prix_m2_raison == "vente_mixte"


@pytest.mark.parametrize(
    "overrides",
    (
        {"prix_m2": None, "prix_m2_raison": None},
        {"prix_m2": 4000.0, "prix_m2_raison": "vente_mixte"},
        {"prix_m2": None, "prix_m2_raison": "raison-inventee"},
        {
            "classe_mutation": "classe-inventee",
            "prix_m2": None,
            "prix_m2_raison": "mutation_multi_locaux",
        },
    ),
)
def test_schema_rejects_incoherent_or_open_vocabulary_payloads(overrides):
    with pytest.raises(ValidationError):
        ResultatDvf.model_validate(_base_result(**overrides))


def test_pre_phase_contract_fields_cannot_be_removed():
    # D-15: hard-coded pre-Phase-57 contract. A future field removal must fail CI.
    pre_phase_fields = {
        "id_mutation",
        "date_mutation",
        "nature_mutation",
        "valeur_fonciere",
        "type_local",
        "surface_reelle_bati",
        "nombre_pieces_principales",
        "surface_terrain",
        "prix_m2",
        "adresse_numero",
        "adresse_nom_voie",
        "code_postal",
        "nom_commune",
        "id_parcelle",
        "code_type_local",
        "code_nature_culture",
        "nature_culture",
        "code_nature_culture_speciale",
        "nature_culture_speciale",
        "longitude",
        "latitude",
    }

    assert pre_phase_fields <= ResultatDvf.model_fields.keys()


# --- Phase 04.4, D-03: a mutation of unknown position is a statistic only -------


async def _appartements(session: _CaptureSession) -> list[dict]:
    return await dvf_queries.dvf_appartements_par_parcelle(
        session, "44109000EN0179", surface_min=11, limit=13
    )


@pytest.mark.parametrize(
    ("name", "query_call"),
    (
        ("id_parcelle", _id_parcelle),
        ("parcelle", _parcelle),
        ("appartements", _appartements),
    ),
)
async def test_parcel_queries_exclude_the_mutations_of_unknown_position(
    name: str, query_call: QueryCall
):
    session = _CaptureSession()
    await query_call(session)

    assert dvf_queries.POSITION_CONNUE_SQL in session.sql, name


def test_the_parcel_fragment_excludes_the_mutations_of_unknown_position():
    """Landmark ② and the parcel sales of the address page read this fragment."""
    assert dvf_queries.POSITION_CONNUE_SQL == "mg.position IS NOT NULL"
    assert dvf_queries.POSITION_CONNUE_SQL in dvf_queries._MUTATIONS_DE_LA_PARCELLE_SQL


@pytest.mark.parametrize(
    ("name", "query_call"), (("point", _point), ("adresse", _adresse))
)
async def test_radius_queries_exclude_them_by_their_radius_on_the_position(
    name: str, query_call: QueryCall
):
    """No added predicate: ``ST_DWithin`` on a NULL position is never true."""
    session = _CaptureSession()
    await query_call(session)

    where = session.sql.split("WHERE", 1)[1]
    assert "ST_DWithin(    mg.position::geography," in where, name
