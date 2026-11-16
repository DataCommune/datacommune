"""Wave 0 matrix of the exact DPE attribution policy (Phase 51, D-01..D-08).

Two layers are proven here:

- SQL SHAPE of the identity fetches (``app.dpe.queries``): a bound BAN equality,
  no proximity operator of any kind, a ``limit + 1`` sentinel and a truthful
  truncation flag. A geographic search is a different capability living in
  ``dpe_par_point`` (D-16) and is never an attribution proof.
- CLOSED PUBLIC CONTRACT (``app.dpe.schemas``): the closed migration states, and every
  impossible combination rejected before serialization -- an ``exact`` without
  its diagnostic, a ``multiple`` with a single candidate, a ``building``
  carrying an individual diagnostic, a candidate leaking a BAN key or the raw
  ADEME address complement.
"""

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.unit

from app.dpe import attribution
from app.dpe import queries as dpe_queries
from app.dpe.attribution import classify_dpe_attribution
from app.dpe.schemas import (
    DPE_ATTRIBUTION_RAISONS,
    DpeAttribution,
    DpeBuildingContext,
    DpeCandidate,
    DpePublicDiagnostic,
)

# Synthetic BAN interoperability key (INSEE_FANTOIR_NUMERO) -- never a real
# address: a BAN key is personal data in this project (PRIV-01).
_BAN_ID = "44109_zz01_00002"
_BAN_VOISIN = "44109_zz01_00004"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeRow:
    """Stand-in for a SQLAlchemy Row: only ``_mapping`` is ever read."""

    def __init__(self, mapping: dict):
        self._mapping = mapping


class _FakeResult:
    def __init__(self, rows: tuple[dict, ...]):
        self._rows = rows

    def __iter__(self):
        return iter([_FakeRow(row) for row in self._rows])


class _CaptureSession:
    """Records the emitted statement instead of talking to PostgreSQL."""

    def __init__(self, captured: dict, rows: tuple[dict, ...] = ()):
        self._captured = captured
        self._rows = rows

    async def execute(self, sql, params):
        self._captured["clause"] = sql
        self._captured["sql"] = str(sql)
        self._captured["params"] = params
        self._captured["appels"] = self._captured.get("appels", 0) + 1
        return _FakeResult(self._rows)


def _lignes(nombre: int) -> tuple[dict, ...]:
    return tuple({"numero_dpe": f"DPE-{i}"} for i in range(nombre))


def _diagnostic(**overrides) -> DpePublicDiagnostic:
    champs: dict = {
        "numero_dpe": "DPE-0001",
        "etiquette_dpe": "D",
        "perime": False,
    }
    champs.update(overrides)
    return DpePublicDiagnostic(**champs)


def _candidat(**overrides) -> DpeCandidate:
    champs: dict = {
        "numero_dpe": "DPE-0001",
        "etiquette_dpe": "D",
        "perime": False,
    }
    champs.update(overrides)
    return DpeCandidate(**champs)


# ---------------------------------------------------------------------------
# Layer 1 -- SQL shape of the exact BAN fetch (D-01, D-04, T-51-01, T-51-03)
# ---------------------------------------------------------------------------


async def test_query_exact_binds_ban_equality_without_any_spatial_operator():
    """D-01: identity is an equality on the ADEME key, never a distance."""
    captured: dict = {}
    await dpe_queries.dpe_par_identifiant_ban(
        _CaptureSession(captured), _BAN_ID, limit=5
    )

    sql = captured["sql"]
    params = captured["params"]
    assert "identifiant_ban = :ban_id" in sql, sql
    assert "ST_DWithin" not in sql, "an attribution query is never a radius"
    assert "ST_MakePoint" not in sql, "an attribution query never takes a point"
    assert "<->" not in sql, "an attribution query never uses the KNN operator"
    assert "latitude" not in sql and "longitude" not in sql, sql
    assert params["ban_id"] == _BAN_ID, params
    assert "rayon_m" not in params, params
    assert "lon" not in params, params


async def test_query_exact_fetches_one_row_beyond_the_public_bound():
    """Pitfall 4: ``len(rows) == 1`` proves nothing without the sentinel."""
    captured: dict = {}
    await dpe_queries.dpe_par_identifiant_ban(
        _CaptureSession(captured), _BAN_ID, limit=120
    )

    assert "LIMIT :limit" in captured["sql"], captured["sql"]
    assert captured["params"]["limit"] == 121, captured["params"]


async def test_query_exact_bound_returns_at_most_the_limit_and_flags_truncation():
    lignes, tronque = await dpe_queries.dpe_par_identifiant_ban(
        _CaptureSession({}, _lignes(3)), _BAN_ID, limit=2
    )

    assert [ligne["numero_dpe"] for ligne in lignes] == ["DPE-0", "DPE-1"]
    assert tronque is True


async def test_query_exact_bound_is_not_truncated_without_the_sentinel_row():
    lignes, tronque = await dpe_queries.dpe_par_identifiant_ban(
        _CaptureSession({}, _lignes(2)), _BAN_ID, limit=2
    )

    assert len(lignes) == 2
    assert tronque is False


async def test_query_exact_orders_deterministically_by_numero_dpe():
    """No date/class ordering: the policy decides, the SQL only selects."""
    captured: dict = {}
    await dpe_queries.dpe_par_identifiant_ban(
        _CaptureSession(captured), _BAN_ID, limit=5
    )

    assert "ORDER BY d.numero_dpe" in captured["sql"], captured["sql"]
    assert "ORDER BY d.date_etablissement_dpe" not in captured["sql"]


async def test_query_exact_prunes_on_the_department_carried_by_the_key():
    captured: dict = {}
    await dpe_queries.dpe_par_identifiant_ban(
        _CaptureSession(captured), _BAN_ID, limit=5
    )

    assert "code_departement = :dept" in captured["sql"], captured["sql"]
    assert captured["params"]["dept"] == "44", captured["params"]


async def test_query_exact_without_identity_runs_no_sql_at_all():
    """D-04: no BAN identity, no query -- and certainly no fallback."""
    captured: dict = {}
    resultat = await dpe_queries.dpe_par_identifiant_ban(
        _CaptureSession(captured), "", limit=5
    )

    assert resultat == ([], False)
    assert captured == {}


# ---------------------------------------------------------------------------
# Layer 1 -- SQL shape of the building-context fetch (D-02, D-03)
# ---------------------------------------------------------------------------


async def test_query_building_keys_empty_runs_no_sql_at_all():
    captured: dict = {}
    resultat = await dpe_queries.dpe_par_identifiants_ban(
        _CaptureSession(captured), [], limit=5
    )

    assert resultat == ([], False)
    assert captured == {}


async def test_query_building_keys_are_deduplicated_and_bound_as_parameters():
    captured: dict = {}
    await dpe_queries.dpe_par_identifiants_ban(
        _CaptureSession(captured),
        [_BAN_ID, _BAN_ID, _BAN_VOISIN, ""],
        limit=5,
    )

    sql = captured["sql"]
    params = captured["params"]
    # An expanding parameter renders as ``IN (__[POSTCOMPILE_ban_ids])``: the
    # marker IS the proof that SQLAlchemy, not an f-string, builds the list.
    assert "identifiant_ban IN (__[POSTCOMPILE_ban_ids])" in sql, sql
    assert _BAN_ID not in sql, "keys are bound, never interpolated"
    assert "ST_DWithin" not in sql and "<->" not in sql, sql
    assert params["ban_ids"] == [_BAN_ID, _BAN_VOISIN], params
    assert params["depts"] == ["44"], params
    # Private attribute on purpose: an ``IN`` over a bound list is only safe
    # when SQLAlchemy expands it -- string interpolation is the T-51-01 threat.
    assert captured["clause"]._bindparams["ban_ids"].expanding is True


async def test_query_building_keys_fetch_one_row_beyond_the_bound():
    captured: dict = {}
    lignes, tronque = await dpe_queries.dpe_par_identifiants_ban(
        _CaptureSession(captured, _lignes(4)), [_BAN_VOISIN], limit=3
    )

    assert captured["params"]["limit"] == 4, captured["params"]
    assert len(lignes) == 3
    assert tronque is True


# ---------------------------------------------------------------------------
# Layer 2 -- closed public contract (D-08, D-13, T-51-02)
# ---------------------------------------------------------------------------


def test_schema_status_taxonomy_is_closed():
    for interdit in ("proche", "spatial", "value", "voisin"):
        with pytest.raises(ValidationError):
            DpeAttribution(status=interdit)


def test_schema_reason_taxonomy_is_closed():
    assert set(DPE_ATTRIBUTION_RAISONS) >= {
        "identity_unavailable",
        "not_reported",
        "ambiguous_unresolved",
        "truncated",
        "temporary_error",
    }
    with pytest.raises(ValidationError):
        DpeAttribution(status="absent", reason="pas_trouve")


def test_schema_exact_requires_its_diagnostic():
    with pytest.raises(ValidationError):
        DpeAttribution(status="exact")

    atteste = DpeAttribution(status="exact", diagnostic=_diagnostic())
    assert atteste.diagnostic is not None
    assert atteste.candidates == []
    assert atteste.building_context is None


def test_schema_multiple_requires_at_least_two_candidates():
    with pytest.raises(ValidationError):
        DpeAttribution(status="multiple", candidates=[_candidat()])

    ambigu = DpeAttribution(
        status="multiple",
        candidates=[_candidat(), _candidat(numero_dpe="DPE-0002")],
    )
    assert len(ambigu.candidates) == 2
    assert ambigu.diagnostic is None


def test_schema_building_never_carries_an_individual_diagnostic():
    contexte = DpeBuildingContext(count=2, distribution={"D": 2})
    with pytest.raises(ValidationError):
        DpeAttribution(
            status="building",
            reason="not_reported",
            diagnostic=_diagnostic(),
            building_context=contexte,
        )
    with pytest.raises(ValidationError):
        DpeAttribution(
            status="building",
            reason="not_reported",
            candidates=[_candidat(), _candidat(numero_dpe="DPE-0002")],
            building_context=contexte,
        )

    agrege = DpeAttribution(
        status="building", reason="not_reported", building_context=contexte
    )
    assert agrege.diagnostic is None
    assert agrege.candidates == []


def test_schema_building_requires_its_aggregate_context():
    with pytest.raises(ValidationError):
        DpeAttribution(status="building", reason="not_reported")


def test_schema_absent_and_error_carry_no_diagnostic():
    with pytest.raises(ValidationError):
        DpeAttribution(status="absent", reason="not_reported", diagnostic=_diagnostic())
    with pytest.raises(ValidationError):
        DpeAttribution(
            status="error",
            reason="temporary_error",
            candidates=[_candidat(), _candidat(numero_dpe="DPE-0002")],
        )


def test_schema_absent_and_error_never_share_a_reason():
    """D-13: a technical failure never disguises itself as a business absence."""
    with pytest.raises(ValidationError):
        DpeAttribution(status="error", reason="not_reported")
    with pytest.raises(ValidationError):
        DpeAttribution(status="absent", reason="temporary_error")


def test_schema_public_projections_expose_no_join_or_internal_field():
    interdits = {
        "identifiant_ban",
        "numero_dpe_remplace",
        "complement_adresse_logement",
        "code_departement",
        "id_rnb",
        "score_ban",
        "latitude",
        "longitude",
    }
    assert interdits.isdisjoint(DpePublicDiagnostic.model_fields)
    assert interdits.isdisjoint(DpeCandidate.model_fields)


def test_schema_candidate_rejects_a_ban_identifier():
    with pytest.raises(ValidationError):
        DpeCandidate(numero_dpe="DPE-0001", perime=False, identifiant_ban=_BAN_ID)


def test_schema_candidate_rejects_a_raw_ademe_complement():
    with pytest.raises(ValidationError):
        DpeCandidate(
            numero_dpe="DPE-0001",
            perime=False,
            complement_adresse_logement="Bat A; Etage 2; Porte 3",
        )


def test_schema_public_diagnostic_rejects_a_replacement_edge():
    with pytest.raises(ValidationError):
        DpePublicDiagnostic(
            numero_dpe="DPE-0002", perime=False, numero_dpe_remplace="DPE-0001"
        )


def test_schema_exact_serialization_leaks_no_identity():
    charge = DpeAttribution(
        status="exact",
        diagnostic=_diagnostic(
            date_etablissement_dpe=date(2023, 1, 1), annee_diagnostic=2023
        ),
    ).model_dump()

    assert _BAN_ID not in str(charge)
    assert "identifiant_ban" not in str(charge)


# ---------------------------------------------------------------------------
# Layer 3 -- V2 policy and independent public consultation contracts
# ---------------------------------------------------------------------------


def _dpe(**overrides) -> dict:
    """A synthetic imported DPE row tied to the target BAN by default."""
    row: dict = {
        "numero_dpe": "DPE-0001",
        "numero_dpe_remplace": None,
        "identifiant_ban": _BAN_ID,
        "date_etablissement_dpe": date(2023, 1, 1),
        "date_fin_validite_dpe": date(2033, 1, 1),
        "type_batiment": "appartement",
        "surface_habitable": 60.0,
        "etiquette_dpe": "D",
        "etiquette_ges": "C",
        "annee_construction": 1985,
        "periode_construction": "1975-1977",
        "type_installation_chauffage": "chaudiere gaz",
        "type_energie_principale_chauffage": "Gaz naturel",
        "conso_5_usages_ep": 245.3,
        "emission_ges_5_usages": 42.1,
        "cout_total_5_usages": 1180.0,
        "qualite_isolation_enveloppe": "moyenne",
        "numero_etage_appartement": 2,
        "complement_adresse_logement": "Appt 1",
    }
    row.update(overrides)
    return row


def test_v2_schema_requires_the_constant_unassigned_reason():
    with pytest.raises(ValidationError):
        DpeAttribution(status="unassigned", reason="ambiguous_unresolved")
    with pytest.raises(ValidationError):
        DpeAttribution(
            status="unassigned",
            reason="dwelling_identity_unproven",
            diagnostic=_diagnostic(),
        )
    with pytest.raises(ValidationError):
        DpeAttribution(version=1, status="absent", reason="not_reported")


def test_absent_without_ban_identity_even_when_rows_are_supplied():
    decision = classify_dpe_attribution(None, [_dpe(), _dpe(numero_dpe="DPE-0002")])

    assert decision.version == 2
    assert decision.status == "absent"
    assert decision.reason == "identity_unavailable"
    assert decision.diagnostic is None
    assert decision.building_context is None


def test_absent_not_reported_without_an_exact_address_row():
    context_rows = [
        _dpe(numero_dpe="X", identifiant_ban=_BAN_VOISIN, etiquette_dpe="C"),
        _dpe(numero_dpe="Y", identifiant_ban=_BAN_VOISIN, etiquette_dpe="D"),
    ]
    decision = classify_dpe_attribution(
        _BAN_ID,
        [],
        building_rows=context_rows,
    )

    assert decision.status == "absent"
    assert decision.reason == "not_reported"
    assert decision.building_context is not None
    assert decision.building_context.count == 2


@pytest.mark.parametrize(
    "rows",
    [
        [_dpe(type_batiment="maison", complement_adresse_logement=None)],
        [_dpe(type_batiment="appartement")],
        [
            _dpe(numero_dpe="A", complement_adresse_logement="Appt 1"),
            _dpe(numero_dpe="B", complement_adresse_logement="Appt 2"),
        ],
        [
            _dpe(numero_dpe="A"),
            _dpe(numero_dpe="B", numero_dpe_remplace="A"),
        ],
        [_dpe(type_batiment="immeuble", complement_adresse_logement=None)],
    ],
)
def test_ban_equality_never_proves_a_dwelling_attribution(rows):
    """D-02: uniqueness, type, discriminants and terminals are inventory facts."""
    decision = classify_dpe_attribution(_BAN_ID, rows)

    assert decision.status == "unassigned"
    assert decision.reason == "dwelling_identity_unproven"
    assert decision.diagnostic is None
    assert decision.candidates == []


def test_foreign_rows_are_never_substituted_or_counted_as_target_inventory():
    voisin = _dpe(numero_dpe="DPE-VOISIN", identifiant_ban=_BAN_VOISIN)
    sans_cle = _dpe(numero_dpe="DPE-SPATIAL", identifiant_ban=None)

    decision = classify_dpe_attribution(_BAN_ID, [voisin, _dpe(), sans_cle])
    serialized = str(decision.model_dump())

    assert decision.status == "unassigned"
    assert "DPE-VOISIN" not in serialized
    assert "DPE-SPATIAL" not in serialized


def test_foreign_row_alone_is_an_absence_not_a_substitution():
    voisin = _dpe(numero_dpe="DPE-VOISIN", identifiant_ban=_BAN_VOISIN)

    decision = classify_dpe_attribution(_BAN_ID, [voisin])

    assert decision.status == "absent"
    assert decision.reason == "not_reported"


def test_target_and_other_address_effectives_are_never_merged():
    others = [
        _dpe(numero_dpe="X", identifiant_ban=_BAN_VOISIN, etiquette_dpe="C"),
        _dpe(numero_dpe="Y", identifiant_ban=_BAN_VOISIN, etiquette_dpe="D"),
    ]

    decision = classify_dpe_attribution(
        _BAN_ID,
        [_dpe(numero_dpe="TARGET")],
        building_rows=others,
    )

    assert decision.status == "unassigned"
    assert decision.building_context is not None
    assert decision.building_context.count == 2
    assert decision.building_context.distribution == {"C": 1, "D": 1}
    assert "TARGET" not in str(decision.building_context.model_dump())


def test_other_address_context_rechecks_the_source_ban_partition():
    rows = [
        _dpe(numero_dpe="TARGET-CONTAMINATION", etiquette_dpe="G"),
        _dpe(numero_dpe="NO-BAN", identifiant_ban=None, etiquette_dpe="F"),
        _dpe(numero_dpe="OTHER", identifiant_ban=_BAN_VOISIN, etiquette_dpe="C"),
    ]

    decision = classify_dpe_attribution(
        _BAN_ID,
        [_dpe(numero_dpe="TARGET")],
        building_rows=rows,
    )

    assert decision.building_context is not None
    assert decision.building_context.count == 1
    assert decision.building_context.distribution == {"C": 1}


def test_bounded_exact_page_remains_unassigned_and_signals_incomplete_context():
    decision = classify_dpe_attribution(
        _BAN_ID,
        [_dpe()],
        exact_truncated=True,
    )

    assert decision.status == "unassigned"
    assert decision.reason == "dwelling_identity_unproven"
    assert decision.building_context is None
    assert decision.graph_state == "not_checked"


def test_incoherent_replacement_graph_never_changes_the_unassigned_decision():
    a = _dpe(numero_dpe="A", numero_dpe_remplace="B")
    b = _dpe(numero_dpe="B", numero_dpe_remplace="A")

    decision = classify_dpe_attribution(_BAN_ID, [a, b])

    assert decision.status == "unassigned"
    assert decision.reason == "dwelling_identity_unproven"
    assert decision.building_context is None
    assert decision.graph_state == "incoherent"


def test_other_address_distribution_is_ordered_and_elects_no_class():
    others = [
        _dpe(numero_dpe="X", identifiant_ban=_BAN_VOISIN, etiquette_dpe="G"),
        _dpe(numero_dpe="Y", identifiant_ban=_BAN_VOISIN, etiquette_dpe="B"),
        _dpe(numero_dpe="Z", identifiant_ban=_BAN_VOISIN, etiquette_dpe="B"),
        _dpe(numero_dpe="W", identifiant_ban=_BAN_VOISIN, etiquette_dpe=None),
        _dpe(numero_dpe="V", identifiant_ban=_BAN_VOISIN, etiquette_dpe="Z"),
    ]

    decision = classify_dpe_attribution(_BAN_ID, [], building_rows=others)

    assert decision.building_context is not None
    assert list(decision.building_context.distribution) == ["B", "G"]
    assert decision.building_context.distribution == {"B": 2, "G": 1}
    assert decision.building_context.count == 5


def test_unassigned_serialization_leaks_no_source_identity():
    payload = classify_dpe_attribution(_BAN_ID, [_dpe()]).model_dump()
    serialized = str(payload)

    assert _BAN_ID not in serialized
    assert "identifiant_ban" not in serialized
    assert "numero_dpe_remplace" not in serialized
    assert "complement_adresse_logement" not in serialized
    assert "Appt 1" not in serialized
    assert payload["diagnostic"] is None
    assert payload["candidates"] == []


def test_inventory_models_are_closed_and_preserve_unknown_values():
    from app.dpe import schemas as dpe_schemas

    item = dpe_schemas.DpeInventoryItem(document_ref="doc-opaque")
    assert item.surface_habitable is None
    assert item.date_fin_validite_dpe is None

    forbidden = {
        "numero_dpe",
        "identifiant_ban",
        "adresse_brute",
        "complement_adresse_logement",
        "longitude",
        "latitude",
        "numero_etage_appartement",
        "numero_dpe_remplace",
    }
    assert forbidden.isdisjoint(dpe_schemas.DpeInventoryItem.model_fields)
    assert forbidden.isdisjoint(dpe_schemas.DpeConsultedDocument.model_fields)

    for field in forbidden:
        with pytest.raises(ValidationError):
            dpe_schemas.DpeInventoryItem(
                document_ref="doc-opaque",
                **{field: "private"},
            )


def test_closed_models_hide_rejected_private_values_from_error_text():
    from app.dpe import schemas as dpe_schemas

    private_ademe_number = "ADEME-PRIVATE-SENTINEL"
    factories = (
        lambda: dpe_schemas.DpeInventoryItem(
            document_ref="doc-opaque", numero_dpe=private_ademe_number
        ),
        lambda: dpe_schemas.DpeConsultedDocument(
            context_ref="context-opaque",
            document_ref="doc-opaque",
            validity_by_date="unknown",
            numero_dpe=private_ademe_number,
        ),
    )
    for factory in factories:
        with pytest.raises(ValidationError) as error:
            factory()
        assert private_ademe_number not in str(error.value)


@pytest.mark.parametrize(
    "distribution",
    [
        {_BAN_ID: 1},
        {"Z": 1},
        {"D": -1},
        {"D": 2},
    ],
)
def test_building_context_rejects_open_or_incoherent_distributions(distribution):
    with pytest.raises(ValidationError) as error:
        DpeBuildingContext(count=1, distribution=distribution)

    assert _BAN_ID not in str(error.value)


def test_inventory_page_validates_total_cursor_and_state():
    from app.dpe import schemas as dpe_schemas

    item = dpe_schemas.DpeInventoryItem(document_ref="doc-opaque")
    distribution = dpe_schemas.DpeInventoryDistribution(D=1)
    page = dpe_schemas.DpeInventoryPage(
        state="ready",
        context_ref="context-opaque",
        items=[item],
        total=1,
        distribution=distribution,
        page_size=20,
        has_more=False,
        next_cursor=None,
        read_state="complete",
    )

    assert page.version == 2
    assert page.distribution.unknown == 0
    assert page.model_dump()["distribution"]["D"] == 1

    invalid = page.model_dump()
    invalid["has_more"] = True
    with pytest.raises(ValidationError):
        dpe_schemas.DpeInventoryPage.model_validate(invalid)
    invalid = page.model_dump()
    invalid["total"] = 2
    with pytest.raises(ValidationError):
        dpe_schemas.DpeInventoryPage.model_validate(invalid)
    invalid = page.model_dump()
    invalid["total"] = 0
    invalid["distribution"] = dpe_schemas.DpeInventoryDistribution()
    with pytest.raises(ValidationError):
        dpe_schemas.DpeInventoryPage.model_validate(invalid)


def test_inventory_page_rejects_contradictory_non_ready_states():
    from app.dpe import schemas as dpe_schemas

    common = {
        "distribution": dpe_schemas.DpeInventoryDistribution(),
        "page_size": 20,
        "has_more": False,
        "next_cursor": None,
        "read_state": "complete",
    }

    for state, total in (
        ("absent", 0),
        ("identity_unavailable", None),
        ("unavailable", None),
    ):
        dpe_schemas.DpeInventoryPage(state=state, total=total, **common)

    invalid_pages = [
        {"state": "absent", "total": 1, "distribution": {"D": 1}},
        {
            "state": "identity_unavailable",
            "total": None,
            "context_ref": "context-must-not-exist",
        },
        {"state": "identity_unavailable", "total": 0},
        {
            "state": "unavailable",
            "total": None,
            "has_more": True,
            "next_cursor": "cursor-must-not-exist",
        },
    ]
    for overrides in invalid_pages:
        payload = {**common, **overrides}
        with pytest.raises(ValidationError):
            dpe_schemas.DpeInventoryPage(**payload)


def test_consulted_document_keeps_missing_end_date_unknown_and_rejects_private_data():
    from app.dpe import schemas as dpe_schemas

    detail = dpe_schemas.DpeConsultedDocument(
        context_ref="context-opaque",
        document_ref="doc-opaque",
        validity_by_date="unknown",
    )
    assert detail.date_fin_validite_dpe is None
    assert detail.surface_habitable is None

    with pytest.raises(ValidationError):
        dpe_schemas.DpeConsultedDocument(
            context_ref="context-opaque",
            document_ref="doc-opaque",
            validity_by_date="expired",
        )
    with pytest.raises(ValidationError):
        dpe_schemas.DpeConsultedDocument(
            context_ref="context-opaque",
            document_ref="doc-opaque",
            validity_by_date="unknown",
            numero_dpe="private",
        )


@pytest.mark.parametrize(
    ("end_date", "expected"),
    [
        (None, "unknown"),
        (date(2026, 9, 3), "expired"),
        (date(2026, 9, 4), "not_expired_by_date"),
    ],
)
def test_dpe_validity_by_date_uses_an_explicit_reference(end_date, expected):
    from app.dpe import schemas as dpe_schemas

    assert (
        dpe_schemas.dpe_validity_by_date(
            end_date,
            reference_date=date(2026, 9, 4),
        )
        == expected
    )


@pytest.mark.parametrize(
    ("end_date", "valid_state"),
    [
        (date(2000, 1, 1), "expired"),
        (date(2999, 1, 1), "not_expired_by_date"),
    ],
)
def test_consulted_document_legacy_end_date_does_not_force_known_validity(
    end_date,
    valid_state,
):
    from app.dpe import schemas as dpe_schemas

    common = {
        "context_ref": "context-opaque",
        "document_ref": "document-opaque",
        "date_fin_validite_dpe": end_date,
    }
    dpe_schemas.DpeConsultedDocument(
        validity_by_date=valid_state,
        **common,
    )
    # A source end date alone cannot prove coherent establishment/validity.
    # Explicit lifecycle dates now enforce the detailed state independently.
    dpe_schemas.DpeConsultedDocument(validity_by_date="unknown", **common)


def test_policy_module_has_no_database_framework_or_dwelling_discriminants():
    source = Path(attribution.__file__).read_text(encoding="utf-8").lower()

    for forbidden in (
        "sqlalchemy",
        "fastapi",
        "httpx",
        "psycopg",
        "discriminants",
        "est_distinguable",
        "await ",
    ):
        assert forbidden not in source


def test_replacement_chain_remains_unassigned_without_electing_its_terminal():
    a = _dpe(numero_dpe="A", etiquette_dpe="G")
    b = _dpe(numero_dpe="B", numero_dpe_remplace="A", etiquette_dpe="F")
    c = _dpe(numero_dpe="C", numero_dpe_remplace="B", etiquette_dpe="E")

    decision = classify_dpe_attribution(_BAN_ID, [a, b, c])

    assert decision.status == "unassigned"
    assert decision.graph_state == "not_checked"
    assert decision.diagnostic is None
    assert decision.candidates == []
    assert "C" not in str(decision.model_dump())


def test_replacement_result_is_invariant_under_dates_classes_and_order():
    first = [
        _dpe(numero_dpe="A", date_etablissement_dpe=date(2025, 1, 1)),
        _dpe(
            numero_dpe="B",
            numero_dpe_remplace="A",
            date_etablissement_dpe=date(2015, 1, 1),
            etiquette_dpe="G",
        ),
    ]
    second = [
        _dpe(
            numero_dpe="B",
            numero_dpe_remplace="A",
            date_etablissement_dpe=date(2030, 1, 1),
            etiquette_dpe="A",
        ),
        _dpe(numero_dpe="A", date_etablissement_dpe=date(2010, 1, 1)),
    ]

    for rows in (first, second):
        decision = classify_dpe_attribution(_BAN_ID, rows)
        assert decision.status == "unassigned"
        assert decision.diagnostic is None
        assert decision.candidates == []


def test_independent_terminals_remain_unassigned_under_date_class_permutation():
    first = [
        _dpe(numero_dpe="A", complement_adresse_logement="Appt 1"),
        _dpe(numero_dpe="B", complement_adresse_logement="Appt 2"),
    ]
    second = [
        _dpe(
            numero_dpe="A",
            complement_adresse_logement="Appt 1",
            date_etablissement_dpe=date(2015, 1, 1),
            etiquette_dpe="G",
        ),
        _dpe(
            numero_dpe="B",
            complement_adresse_logement="Appt 2",
            date_etablissement_dpe=date(2025, 1, 1),
            etiquette_dpe="A",
        ),
    ]

    for rows in (first, second):
        decision = classify_dpe_attribution(_BAN_ID, rows)
        assert decision.status == "unassigned"
        assert decision.candidates == []


def test_exact_truncation_does_not_truncate_other_address_context():
    decision = classify_dpe_attribution(
        _BAN_ID,
        [_dpe()],
        exact_truncated=True,
    )

    assert decision.status == "unassigned"
    assert decision.building_context is None
    assert decision.graph_state == "not_checked"


def test_building_truncation_only_marks_the_other_address_context():
    other = _dpe(numero_dpe="X", identifiant_ban=_BAN_VOISIN)

    decision = classify_dpe_attribution(
        _BAN_ID,
        [],
        building_rows=[other],
        building_truncated=True,
    )

    assert decision.status == "absent"
    assert decision.graph_state == "not_checked"
    assert decision.building_context is not None
    assert decision.building_context.count == 1
    assert decision.building_context.truncated is True


@pytest.mark.parametrize(
    "rows",
    [
        [
            _dpe(numero_dpe="A", numero_dpe_remplace="B"),
            _dpe(numero_dpe="B", numero_dpe_remplace="A"),
        ],
        [_dpe(numero_dpe="A", numero_dpe_remplace="A")],
        [
            _dpe(numero_dpe="B", numero_dpe_remplace="A"),
            _dpe(numero_dpe="C", numero_dpe_remplace="A"),
        ],
    ],
)
def test_cycle_self_cycle_and_fork_publish_incoherent_graph_state(rows):
    decision = classify_dpe_attribution(_BAN_ID, rows)

    assert decision.status == "unassigned"
    assert decision.graph_state == "incoherent"
    assert decision.diagnostic is None
    assert decision.candidates == []


def test_dangling_replacement_is_prudent_without_claiming_incoherence():
    decision = classify_dpe_attribution(
        _BAN_ID,
        [_dpe(numero_dpe="C", numero_dpe_remplace="OUTSIDE-SNAPSHOT")],
    )

    assert decision.status == "unassigned"
    assert decision.graph_state == "not_checked"
    assert decision.diagnostic is None


def test_inconsistent_duplicate_numbers_publish_incoherent_graph_state():
    first = _dpe(numero_dpe="A", etiquette_dpe="D")
    second = _dpe(numero_dpe="A", etiquette_dpe="F")

    decision = classify_dpe_attribution(_BAN_ID, [first, second])

    assert decision.status == "unassigned"
    assert decision.graph_state == "incoherent"


def test_other_address_graph_incoherence_stays_context_only():
    others = [
        _dpe(
            numero_dpe="X",
            numero_dpe_remplace="Y",
            identifiant_ban=_BAN_VOISIN,
        ),
        _dpe(
            numero_dpe="Y",
            numero_dpe_remplace="X",
            identifiant_ban=_BAN_VOISIN,
        ),
    ]

    decision = classify_dpe_attribution(_BAN_ID, [], building_rows=others)

    assert decision.status == "absent"
    assert decision.graph_state == "not_checked"
    assert decision.building_context is not None
    assert decision.building_context.count == 2
    assert decision.building_context.truncated is True


def test_policy_never_orders_or_selects_by_date_class_or_discriminant():
    source = Path(attribution.__file__).read_text(encoding="utf-8")

    for forbidden in (
        "sorted(",
        "max(",
        "min(",
        "dedup_logements",
        "_plus_recent",
        "_diagnostic_public",
        "_candidat",
    ):
        assert forbidden not in source
