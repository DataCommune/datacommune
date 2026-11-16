"""Departement pages: dates, states and route checks (plan 04.7-10, D2 to D4).

Pure rules first: a dataset is imported for a departement by its
``effective_departments``, else by the ``scope_snapshot`` of its last success;
a page's date is the latest import day (Paris) of its hosted and imported
datasets, never the response date. Then the projection of a view row into the
served blocks, and the order of the route checks (422 before any read, 404,
no view read without a hosted source). No database: recording doubles; the SQL
is proven by tests/integration/test_departement_synthese.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import pytest

from app.database import get_db
from app.main import app
from app.scope.dependencies import get_instance_scope
from app.scope.model import InstanceScope
from app.zone import departements as dep
from app.zone import router as zone_router

pytestmark = pytest.mark.unit

ON_20 = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
# 23:30 UTC on 30 October is 00:30 on the 31st in Paris (CET).
LATE_30 = datetime(2026, 10, 30, 23, 30, tzinfo=UTC)


def _facts(at=ON_20, effective=None, snapshot=None) -> dep.ImportFacts:
    """One generation: a declared perimeter published at *at*, or a run at *at*."""
    return dep.ImportFacts(
        last_success_at=at,
        effective_departments=None if effective is None else tuple(effective),
        scope_departements=None if snapshot is None else tuple(snapshot),
        metadata_updated_at=at if effective is not None else None,
    )


@pytest.fixture
def scope(full_scope_loader) -> InstanceScope:
    return InstanceScope(full_scope_loader("").datasets, ("29", "44", "57", "85"))


# ------------------------------------------------------ imported for D (D4) --


def test_effective_departments_say_what_an_import_covered():
    facts = _facts(effective=["44", "85"], snapshot=["29", "44", "85"])
    assert dep.imported_for(facts, "44")
    # The snapshot is a fallback, never a widening of a declared perimeter.
    assert not dep.imported_for(facts, "29")


def test_an_empty_perimeter_covers_nothing():
    assert not dep.imported_for(_facts(effective=[], snapshot=["44"]), "44")


def test_without_a_perimeter_the_last_success_snapshot_is_read():
    facts = _facts(effective=None, snapshot=["44", "85"])
    assert dep.imported_for(facts, "85")
    assert not dep.imported_for(facts, "29")


def test_no_successful_import_imports_nothing():
    assert not dep.imported_for(None, "44")
    assert not dep.imported_for(_facts(at=None, effective=["44"]), "44")
    assert not dep.imported_for(_facts(effective=None, snapshot=None), "44")


def test_a_declared_perimeter_is_dated_by_its_own_publication(scope):
    # Review Codex 04.7-10 n. 3: dataset_metadata (perimeter 85, written with
    # last_updated_at in the publication) is one generation; the last run
    # marked success (20 October, scope 44 only) may be an older one.
    facts = dep.ImportFacts(
        last_success_at=ON_20,
        scope_departements=("44",),
        effective_departments=("85",),
        metadata_updated_at=LATE_30,
    )
    assert dep.imported_for(facts, "85")
    assert not dep.imported_for(facts, "44")
    assert dep.page_day(scope, {"dvf": facts}, "85") == date(2026, 10, 31)
    assert dep.page_day(scope, {"dvf": facts}, "44") is None


def test_a_published_perimeter_needs_no_run_marked_success(scope):
    # Interrupted after the publication, before the run is marked success.
    facts = dep.ImportFacts(effective_departments=("85",), metadata_updated_at=LATE_30)
    assert dep.imported_for(facts, "85")
    assert dep.page_day(scope, {"dvf": facts}, "85") == date(2026, 10, 31)
    # A perimeter without the date of its publication proves no import.
    undated = dep.ImportFacts(last_success_at=ON_20, effective_departments=("85",))
    assert not dep.imported_for(undated, "85")


# ------------------------------------------------------------ dates (D4) --


def test_the_page_date_is_the_latest_import_day_in_paris(scope):
    facts = {
        "dvf": _facts(ON_20, effective=["44", "85"]),
        "dpe": _facts(LATE_30, effective=["44"]),
        "demographie": _facts(ON_20, snapshot=["44", "85"]),
    }
    assert dep.page_day(scope, facts, "44") == date(2026, 10, 31)
    # dpe is not imported for 85: its later day is ignored.
    assert dep.page_day(scope, facts, "85") == date(2026, 10, 20)
    # 29 is hosted, but no import covered it: no date at all.
    assert dep.page_day(scope, facts, "29") is None


def test_a_dataset_out_of_coverage_gives_no_date(scope):
    facts = {"dvf": _facts(LATE_30, effective=["57", "44"])}
    # DVF publishes nothing for the Moselle: out of coverage, whatever the run.
    assert dep.page_day(scope, facts, "57") is None
    narrow = InstanceScope(frozenset({"dpe"}), ("44",))
    assert dep.page_day(narrow, facts, "44") is None


def test_the_pages_date_reads_the_hosted_commune_sources(scope):
    facts = {
        "revenus": _facts(LATE_30, effective=["44"]),
        "dvf": _facts(ON_20, effective=["44"]),
    }
    # revenus is a source of the commune page, not of the departement page.
    assert dep.fiches_day(scope, facts, "44") == date(2026, 10, 31)
    assert dep.page_day(scope, facts, "44") == date(2026, 10, 20)


def test_indexable_needs_dvf_hosted_and_imported(scope):
    facts = {"dvf": _facts(ON_20, effective=["44", "57"])}
    assert dep.indexable(scope, facts, "44")
    assert not dep.indexable(scope, facts, "85")  # hosted, not imported
    assert not dep.indexable(scope, facts, "57")  # not published by DVF
    assert not dep.indexable(scope, facts, "22")  # outside the scope


# ------------------------------------------------- blocks of a row (D2) --

_ROW = {
    "code_departement": "44",
    "fenetre_debut": "2024-06-30",
    "fenetre_fin": "2025-06-30",
    "prix_m2_median_maison": None,
    "nb_mutations_12m_maison": 4,
    "prix_m2_median_appartement": 3150,
    "nb_mutations_12m_appartement": 8,
    **{f"dpe_{c}": n for c, n in zip("abcdefg", (1, 1, 1, 0, 0, 2, 2), strict=True)},
    "dpe_inconnue": 1,
    "dpe_total": 8,
    "dpe_classes_connues": 7,
    "dpe_part_fg_pct": 57.1,
    "dpe_date_min": "2021-07-01",
    "dpe_date_max": "2026-09-30",
    "population": 366000,
    "population_communes": 2,
    "annee_population": 2022,
}


def test_a_served_row_gives_values_and_the_masked_median_its_count():
    prix = dep.prix_block(_ROW, None)
    assert prix.appartement.mediane_m2 == 3150
    assert (prix.maison.mediane_m2, prix.maison.effectif) == (None, 4)
    assert prix.maison.etat.etat == "insufficient_sample"
    assert "4" in prix.maison.etat.motif and "5" in prix.maison.etat.motif
    assert prix.fenetre.debut == date(2024, 6, 30)
    dpe = dep.dpe_block(_ROW, None)
    assert (dpe.total, dpe.part_fg_pct, dpe.etat_part_fg) == (8, 57.1, None)
    population = dep.population_block(_ROW, None)
    assert (population.total, population.annee) == (366000, 2022)


def test_no_sale_of_a_type_is_not_reported_never_zero():
    row = {**_ROW, "nb_mutations_12m_maison": 0}
    prix = dep.prix_block(row, None)
    assert prix.maison.effectif is None
    assert prix.maison.etat.etat == "not_reported"
    assert prix.fenetre is not None  # the flats are served


def test_a_departement_without_row_is_not_reported_in_every_block():
    prix = dep.prix_block(None, None)
    assert prix.fenetre is None
    assert prix.maison.etat.etat == prix.appartement.etat.etat == "not_reported"
    assert dep.dpe_block(None, None).etat.etat == "not_reported"
    assert dep.population_block(None, None).etat.etat == "not_reported"


def test_documents_without_known_class_have_no_share():
    row = {
        **_ROW,
        **{f"dpe_{c}": 0 for c in "abcdefg"},
        "dpe_inconnue": 3,
        "dpe_total": 3,
        "dpe_classes_connues": 0,
        "dpe_part_fg_pct": None,
    }
    dpe = dep.dpe_block(row, None)
    assert dpe.total == 3
    assert dpe.etat_part_fg.etat == "zero_denominator"


def test_a_block_state_wins_over_the_row():
    state = dep.block_state(
        dep.hosting_for("dvf", InstanceScope(frozenset({"dvf"}), ("44",)), "44"),
        imported=False,
    )
    assert state.etat == "not_reported"
    prix = dep.prix_block(_ROW, state)
    assert prix.appartement.mediane_m2 is None
    assert prix.appartement.effectif is None
    out = dep.block_state(
        dep.hosting_for("dvf", InstanceScope(frozenset({"dvf"}), ("44",)), "22"),
        imported=True,
    )
    assert (out.etat, out.motif) == ("out_of_coverage", "territoire_hors_perimetre")


# ---------------------------------------------- order of the checks (D2) --


@dataclass
class Reads:
    """Recording doubles of the referential, the journal and the view."""

    identity: dict | None = field(
        default_factory=lambda: {"code": "44", "nom": "Loire-Atlantique"}
    )
    facts: dict = field(default_factory=dict)
    calls: list[tuple[str, tuple]] = field(default_factory=list)
    keywords: dict[str, list[dict]] = field(default_factory=dict)


@pytest.fixture
def reads(monkeypatch) -> Reads:
    state = Reads()

    def record(name, result):
        async def double(_db, *args, **kwargs):
            state.calls.append((name, args))
            state.keywords.setdefault(name, []).append(kwargs)
            return result() if callable(result) else result

        return double

    monkeypatch.setattr(
        zone_router, "departement_identite", record("identite", lambda: state.identity)
    )
    monkeypatch.setattr(
        zone_router, "acquire_publication_read_lock", record("verrou", None)
    )
    monkeypatch.setattr(
        zone_router, "import_facts", record("facts", lambda: state.facts)
    )
    monkeypatch.setattr(zone_router, "departement_synthese", record("vue", None))
    monkeypatch.setattr(zone_router, "departement_communes", record("communes", (2, 0)))
    monkeypatch.setattr(zone_router, "communes_principales", record("principales", []))
    monkeypatch.setattr(
        zone_router,
        "departement_fiches",
        record(
            "fiches", [{"code_insee": "44109", "nom": "Nantes", "type_zone": "commune"}]
        ),
    )

    async def no_session():
        yield object()

    app.dependency_overrides[get_db] = no_session
    yield state
    app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize("code", ["4A", "123", "4", "44x", "2C", "9999"])
@pytest.mark.parametrize("suffix", ["", "/fiches"])
async def test_a_malformed_code_is_refused_before_any_read(client, reads, code, suffix):
    def no_scope():
        raise AssertionError("the scope is read after the code is validated")

    app.dependency_overrides[get_instance_scope] = no_scope
    response = await client.get(f"/v1/departements/{code}{suffix}")
    assert response.status_code == 422, response.text
    assert reads.calls == []


async def test_a_lower_case_corsican_code_is_read_upper_case(client, reads):
    reads.identity = {"code": "2A", "nom": "Corse-du-Sud"}
    response = await client.get("/v1/departements/2a")
    assert response.status_code == 200, response.text
    assert response.json()["donnees"]["code"] == "2A"
    assert ("identite", ("2A",)) in reads.calls


@pytest.mark.parametrize("suffix", ["", "/fiches"])
async def test_an_unknown_departement_is_404(client, reads, suffix):
    reads.identity = None
    response = await client.get(f"/v1/departements/99{suffix}")
    assert response.status_code == 404, response.text
    assert [name for name, _ in reads.calls] == ["identite"]


async def test_no_hosted_source_reads_no_view(client, reads):
    app.dependency_overrides[get_instance_scope] = lambda: InstanceScope(
        frozenset({"ban", "dvf", "dpe", "demographie"}), ("85",)
    )
    response = await client.get("/v1/departements/44")
    assert response.status_code == 200, response.text
    names = [name for name, _ in reads.calls]
    assert "vue" not in names
    # Nothing hosted: no view to pair with the facts, no lock to wait for.
    assert "verrou" not in names
    body = response.json()["donnees"]
    assert body["prix"]["maison"]["etat"]["motif"] == "territoire_hors_perimetre"
    assert body["indexable"] is False


async def test_the_import_facts_are_read_under_the_view_lock(client, reads):
    # Review Opus 04.7-10 n. 2: the lock is taken before the facts, so a
    # publication cannot commit between the facts and the view.
    response = await client.get("/v1/departements/44")
    assert response.status_code == 200, response.text
    names = [name for name, _ in reads.calls]
    assert names.index("verrou") < names.index("facts") < names.index("vue")


async def test_the_pages_are_listed_with_their_date(client, reads):
    response = await client.get("/v1/departements/44/fiches")
    assert response.status_code == 200, response.text
    body = response.json()["donnees"]
    assert body == {
        "code": "44",
        "derniere_importation": None,
        "fiches": [{"code_insee": "44109", "nom": "Nantes", "type_zone": "commune"}],
    }
    assert "vue" not in [name for name, _ in reads.calls]


async def test_a_departement_too_large_is_an_error_never_a_cut_list(
    client, reads, monkeypatch
):
    monkeypatch.setattr(zone_router, "MAX_FICHES", 1)
    many = [
        {"code_insee": "44109", "nom": "Nantes", "type_zone": "commune"},
        {"code_insee": "44162", "nom": "Saint-Herblain", "type_zone": "commune"},
    ]

    limits = []

    async def fiches(_db, *args, limit, **kwargs):
        # As the SQL: LIMIT cuts. Asked for MAX_FICHES rows, the list would be
        # cut without error; one more row is how the route sees the excess.
        limits.append(limit)
        return many[:limit]

    monkeypatch.setattr(zone_router, "departement_fiches", fiches)
    response = await client.get("/v1/departements/44/fiches")
    assert limits == [2]
    assert response.status_code == 500
    assert "donnees" not in response.json()


async def test_principal_communes_read_the_census_only_where_it_is_imported(
    client, reads
):
    # Review Codex 04.7-10 n. 1: a census hosted but not imported for the
    # departement is never read for its principal communes; they keep the
    # referential population, its year unknown (rule H6).
    response = await client.get("/v1/departements/44")
    assert response.status_code == 200, response.text
    assert reads.keywords["principales"][-1]["demographie_departements"] == []

    reads.facts = {
        "demographie": dep.ImportFacts(
            last_success_at=ON_20, scope_departements=("44",)
        )
    }
    response = await client.get("/v1/departements/44")
    assert response.status_code == 200, response.text
    assert reads.keywords["principales"][-1]["demographie_departements"] == ["44"]


# ------------------------------------------------ neighbours of a page --


@pytest.fixture
def voisines(monkeypatch):
    calls: list[tuple[str, tuple, dict]] = []
    state = {"existe": True}

    async def existe(_db, code):
        calls.append(("existe", (code,), {}))
        return state["existe"]

    async def proches(_db, code, **kwargs):
        calls.append(("voisines", (code,), kwargs))
        return [
            {
                "code_insee": "44162",
                "nom": "Saint-Herblain",
                "departement_code": "44",
                "type_zone": "commune",
                "limitrophe": True,
            }
        ]

    monkeypatch.setattr(zone_router, "zone_existe", existe)
    monkeypatch.setattr(zone_router, "communes_voisines", proches)

    async def no_session():
        yield object()

    app.dependency_overrides[get_db] = no_session
    yield calls, state
    app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize("code", ["4410", "441090", "4A109", "2C004", ""])
async def test_a_malformed_commune_code_reads_no_neighbour(client, voisines, code):
    calls, _ = voisines
    response = await client.get(f"/v1/communes/voisines?code_insee={code}")
    assert response.status_code == 422, response.text
    assert calls == []


async def test_an_unknown_commune_has_no_neighbour_list(client, voisines):
    calls, state = voisines
    state["existe"] = False
    response = await client.get("/v1/communes/voisines?code_insee=44999")
    assert response.status_code == 404, response.text
    assert [name for name, *_ in calls] == ["existe"]


async def test_the_neighbours_are_bounded_and_read_upper_case(client, voisines):
    calls, _ = voisines
    response = await client.get("/v1/communes/voisines?code_insee=2a004")
    assert response.status_code == 200, response.text
    assert response.json()["donnees"]["code_insee"] == "2A004"
    assert calls[1] == (
        "voisines",
        ("2A004",),
        {
            "limit": zone_router.VOISINES_MAX,
            "candidats": zone_router.VOISINES_CANDIDATS,
        },
    )
    assert 6 <= zone_router.VOISINES_MAX <= 10
