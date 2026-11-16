"""Tests: DVF minimization/re-identification detection (Phase 47, D-02/D-03).

FICHE-02 requires a detection capability for the named sensitive case ("parcelle
a mutation unique recente isolee") that feeds the minimization review
(47-DVF-MINIMIZATION-REVIEW.md) and any conditional hardening the review may
decide on. Per RESEARCH Pitfall 4, this detection MUST be a separate function
from ``dvf_par_id_parcelle`` -- the shared query used by the gated (app) fiche
and general API consumers must never be touched by this detection logic.

Levels:
  1. Query shape: dvf_mutation_unique_isolee emits HAVING COUNT(*) = 1, a
     department filter (ANY), and a parameterized recent-window bound -- all
     bound parameters, never concatenated.
  2. Return shape: list of dicts (id_parcelle, nb_mutations, derniere).
  3. Non-regression: dvf_par_id_parcelle keeps its exact shape untouched
     (extends the existing CASE-SQL-04 guard from test_resolve_dvf_parcelle.py).
"""

import pytest

from app.dvf import queries as dvf_queries

pytestmark = pytest.mark.asyncio


class _FakeResult:
    def __iter__(self):
        return iter(())


class _CaptureSession:
    def __init__(self, captured: dict):
        self._captured = captured

    async def execute(self, sql, params):
        self._captured["sql"] = str(sql)
        self._captured["params"] = params
        return _FakeResult()


async def test_dvf_mutation_unique_isolee_query_shape():
    """CASE-MIN-01: detection query has HAVING COUNT(*) = 1, a parameterized
    department filter (ANY) and a parameterized recent-window temporal bound
    on MAX(date_mutation) -- never concatenated values."""
    captured: dict = {}
    await dvf_queries.dvf_mutation_unique_isolee(
        _CaptureSession(captured), departements=["44", "85"], fenetre_mois=24
    )

    sql = captured["sql"]
    params = captured["params"]
    assert "HAVING COUNT(*) = 1" in sql, sql
    assert "code_departement = ANY(:departements)" in sql, sql
    assert "MAX(date_mutation)" in sql, sql
    assert ":fenetre_mois" in sql, sql
    assert params["departements"] == ["44", "85"], params
    assert params["fenetre_mois"] == 24, params
    assert "44" not in sql and "85" not in sql, "values must be bound, not inlined"


async def test_dvf_mutation_unique_isolee_returns_list_of_dicts():
    """CASE-MIN-02: return shape is a list of dicts (id_parcelle, nb_mutations,
    derniere) -- consistent with the other functions of this module."""
    captured: dict = {}
    result = await dvf_queries.dvf_mutation_unique_isolee(
        _CaptureSession(captured), departements=["44"], fenetre_mois=12
    )
    assert result == []


async def test_dvf_par_id_parcelle_still_has_no_point_no_radius_no_centroid():
    """CASE-MIN-03 (non-regression, extends CASE-SQL-04): dvf_par_id_parcelle
    keeps its exact shape -- filter on id_parcelle, no ST_DWithin/point/centroid,
    LIMIT :limit -- proving the detection function (Task 1) never touched the
    shared query used by the gated (app) fiche and the general API."""
    captured: dict = {}
    await dvf_queries.dvf_par_id_parcelle(
        _CaptureSession(captured), "44109000EN0179", limit=5
    )

    sql = captured["sql"]
    params = captured["params"]
    assert "id_parcelle = :id_parcelle" in sql, sql
    assert "ST_DWithin" not in sql, "canonical DVF query must not use a radius"
    assert "ST_MakePoint" not in sql, "canonical DVF query must not take a point"
    assert "rayon" not in params, params
    assert "lat" not in params and "lon" not in params, params
    assert "LIMIT :limit" in sql, sql


# --- Phase 48 Plan 05, revised Phase 57 Plan 13 (D-17):
#     dvf_appartements_par_parcelle (additive, D-09/D-11/D-17) ---

ID_PARCELLE_IMMEUBLE = "85047000AB0042"


async def test_dvf_appartements_query_filters_classe_mutation():
    """D-17: selection is a bound ``classe_mutation`` filter, not ``type_local``.

    Revised from ``test_dvf_appartements_query_filters_type_local``: the
    listing now reads ``mutations_grain``, where the unit is a whole
    mutation and no per-line ``type_local`` exists to filter on. The two
    residential classes that can contain an appartement are retained -- the
    mono-local one AND the whole-building sale, which D-17 makes displayable
    because it is labelled as such.
    """
    captured: dict = {}
    await dvf_queries.dvf_appartements_par_parcelle(
        _CaptureSession(captured), ID_PARCELLE_IMMEUBLE
    )

    sql = captured["sql"]
    params = captured["params"]
    assert "classe_mutation = ANY(:classes_mutation)" in sql, sql
    assert params["classes_mutation"] == [
        "residentiel_appartement",
        "ensemble_residentiel",
    ], params
    assert "'residentiel_appartement'" not in sql.split("WHERE")[1], (
        "the retained classes must be bound in the WHERE clause, not inlined"
    )


async def test_dvf_appartements_query_reads_mutation_grain():
    """D-01/D-09: one row is one reconstructed mutation, never a DVF line."""
    captured: dict = {}
    await dvf_queries.dvf_appartements_par_parcelle(
        _CaptureSession(captured), ID_PARCELLE_IMMEUBLE
    )

    sql = captured["sql"]
    assert "FROM mutations_grain mg" in sql, sql
    assert "JOIN mutation_parcelles mp ON mp.id_mutation = mg.id_mutation" in sql, sql
    assert "FROM mutations\n" not in sql, sql


async def test_dvf_appartements_query_filters_surface_min():
    """D-09/D-17: the bound surface floor now applies to the MUTATION's surface.

    Revised: the floor used to read ``surface_reelle_bati`` (one line's
    surface), which let a dependency-bearing line drag a real sale under the
    threshold. At the mutation grain the honest denominator is
    ``surface_bati_totale`` -- dependencies carry no surface at all.
    """
    captured: dict = {}
    await dvf_queries.dvf_appartements_par_parcelle(
        _CaptureSession(captured), ID_PARCELLE_IMMEUBLE
    )

    sql = captured["sql"]
    params = captured["params"]
    assert "mg.surface_bati_totale >= :surface_min" in sql, sql
    assert params["surface_min"] == 10, params


async def test_dvf_appartements_query_never_projects_a_price_without_its_label():
    """D-17: an amount is projected, ALWAYS beside its ``classe_mutation``.

    Replaces ``test_dvf_appartements_query_selects_no_price``. The Phase 48
    interdiction targeted a per-LINE price (the mutation's total beside one
    local's surface -- false on any multi-local sale); the reconstruction
    removed that cause. What replaces the interdiction is this pairing: the
    amount never travels without the label that says what was sold.
    """
    captured: dict = {}
    await dvf_queries.dvf_appartements_par_parcelle(
        _CaptureSession(captured), ID_PARCELLE_IMMEUBLE
    )

    sql = captured["sql"]
    assert "mg.valeur_fonciere" in sql, sql
    assert "mg.classe_mutation," in sql, "the label must be projected too"
    assert "AS prix_m2" in sql and "AS prix_m2_raison" in sql, sql
    # The honest prix_m2 fragment (D-15) is reused verbatim, never a second
    # decision table: outside a mono-local residential sale it is NULL.
    assert "WHEN NOT mg.mono_local" in sql, sql


async def test_dvf_appartements_query_derives_partition():
    """Same partition derivation as the certified queries (Pattern B)."""
    captured: dict = {}
    await dvf_queries.dvf_appartements_par_parcelle(
        _CaptureSession(captured), ID_PARCELLE_IMMEUBLE
    )

    sql = captured["sql"]
    assert "WHEN LEFT(:id_parcelle, 2) = '97' THEN LEFT(:id_parcelle, 3)" in sql, sql


async def test_dvf_appartements_query_binds_values():
    """Neither the parcel id nor the surface floor are ever inlined."""
    captured: dict = {}
    await dvf_queries.dvf_appartements_par_parcelle(
        _CaptureSession(captured), ID_PARCELLE_IMMEUBLE, surface_min=10
    )

    sql = captured["sql"]
    assert ID_PARCELLE_IMMEUBLE not in sql, "id_parcelle must be bound, not inlined"
    assert " 10 " not in sql and "'10'" not in sql, sql


async def test_dvf_par_id_parcelle_still_intact():
    """Non-regression: dvf_par_id_parcelle still projects valeur_fonciere and
    has NOT been contaminated by the new appartements-only function."""
    captured: dict = {}
    await dvf_queries.dvf_par_id_parcelle(
        _CaptureSession(captured), "44109000EN0179", limit=5
    )

    sql = captured["sql"]
    assert "valeur_fonciere" in sql, sql
    assert "ST_DWithin" not in sql, sql
    assert "ST_MakePoint" not in sql, sql
