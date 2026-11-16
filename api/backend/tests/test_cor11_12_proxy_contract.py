"""PAR-06/07 counterexamples, synthetic protocols; never a live upstream call."""

import httpx
import pytest
import respx

from app.bdnb import client as bdnb
from app.common.exceptions import UpstreamUnavailableError
from app.errial.client import extract_avis_summary
from app.georisques import client as risks
from app.rnb import client as rnb
from app.urbanisme import client as gpu

pytestmark = pytest.mark.unit
POINT = {"lat": 47.2, "lon": -1.5}
FEATURE = {"type": "Feature", "properties": {"libelle": "UA", "typezone": "U"}}


@pytest.mark.parametrize("failed", [0, 1])
async def test_gpu_each_failed_branch_is_explicit_not_empty(failed):
    with respx.mock as mock:
        for index, endpoint in enumerate(["zone-urba", "prescription-surf"]):
            route = mock.get(f"{gpu.APICARTO_BASE}/{endpoint}")
            if index == failed:
                route.mock(side_effect=httpx.ConnectError("private marker"))
            else:
                route.respond(
                    200, json={"type": "FeatureCollection", "features": [FEATURE]}
                )
        result = await gpu.query_urbanisme(**POINT)
    key = ["zones_plu", "prescriptions"][failed]
    assert result[key] is None
    assert result["sources"][key]["statut"] == "indisponible"
    assert "private marker" not in str(result)


async def test_gpu_malformed_success_is_not_official_empty():
    with respx.mock as mock:
        for endpoint in ["zone-urba", "prescription-surf"]:
            mock.get(f"{gpu.APICARTO_BASE}/{endpoint}").respond(
                200, json={"error": "hidden"}
            )
        with pytest.raises(UpstreamUnavailableError):
            await gpu.query_urbanisme(**POINT)


async def test_gpu_explicit_empty_is_distinct_from_failure():
    with respx.mock as mock:
        for endpoint in ["zone-urba", "prescription-surf"]:
            mock.get(f"{gpu.APICARTO_BASE}/{endpoint}").respond(
                200, json={"type": "FeatureCollection", "features": []}
            )
        result = await gpu.query_urbanisme(**POINT)
    assert result["zones_plu"] == []
    assert result["sources"]["zones_plu"]["statut"] == "vide"


@pytest.mark.parametrize("failed", range(4))
async def test_each_risk_subsource_failure_stays_explicit(failed):
    with respx.mock as mock:
        for index, endpoint in enumerate(
            ["gaspar/risques", "rga", "radon", "installations_classees"]
        ):
            route = mock.get(f"{risks.GEORISQUES_BASE}/{endpoint}")
            if index == failed:
                route.respond(503)
            else:
                route.respond(
                    200,
                    json={"exposition": "Faible"}
                    if index == 1
                    else {"results": 0, "data": []},
                )
        result = await risks.query_all_risks(**POINT)
    key = ["risques_naturels", "argiles", "radon", "icpe"][failed]
    assert result[key] is None
    assert result["sources"][key]["statut"] == "indisponible"


async def test_ssp_51_results_retains_total_and_first_page_scope():
    body = {
        key: {"results": 0, "data": []}
        for key in ["casias", "instructions", "conclusions_sis", "conclusions_sup"]
    }
    body["casias"] = {
        "results": 51,
        "data": [{"nom": f"site-{i}"} for i in range(50)],
        "next": "https://untrusted.example/page2",
    }
    with respx.mock as mock:
        route = mock.get(f"{risks.GEORISQUES_BASE}/ssp").respond(200, json=body)
        result = await risks.query_polluted_sites(**POINT)
        assert route.call_count == 1
    assert len(result["casias"]) == 50
    assert result["sources"]["casias"]["total"] == 51
    assert result["sources"]["casias"]["complet"] is False
    assert result["sources"]["casias"]["statut"] == "partiel"


async def test_ssp_missing_blocks_are_not_successful_empty():
    with respx.mock as mock:
        mock.get(f"{risks.GEORISQUES_BASE}/ssp").respond(200, json={"error": "hidden"})
        with pytest.raises(UpstreamUnavailableError):
            await risks.query_polluted_sites(**POINT)


async def test_rnb_next_survives_without_following_external_url():
    with respx.mock as mock:
        route = mock.get(f"{rnb.RNB_BASE}/buildings/closest/").respond(
            200,
            json={
                "count": 2,
                "results": [{"rnb_id": "ABC123"}],
                "next": "https://untrusted.example/page2",
            },
        )
        result = await rnb.query_by_coords(**POINT)
        assert route.call_count == 1
    assert result.coverage.total == 2
    assert result.coverage.complet is False
    assert result.coverage.nombre_retournes == 1


async def test_bdnb_limit_is_not_misnamed_as_inventory_total():
    bdnb._parcelle_cache.clear()
    with respx.mock as mock:
        mock.get(f"{bdnb.BDNB_BASE}/donnees/batiment_groupe_complet/parcelle").respond(
            200, json=[{"batiment_groupe_id": str(i)} for i in range(11)]
        )
        result = await bdnb.query_batiment_by_parcelle("44109000AB0001")
    bdnb._parcelle_cache.clear()
    assert len(result) == 10
    assert result.coverage.total is None
    assert result.coverage.complet is False
    assert result.coverage.nombre_retournes == 10


def test_errial_missing_fields_do_not_fabricate_zero_or_false():
    result = extract_avis_summary({"summary": {"commune": {}}})
    assert result["pollution"]["basias_sur_parcelle"] is None
    assert result["nucleaire"] is None
    assert result["canalisations"] is None
    assert result["ppr"] is None


def test_errial_explicit_zero_false_and_empty_are_preserved():
    result = extract_avis_summary(
        {
            "summary": {"commune": {}},
            "siteIndustrielBasiasSurParcelleDTOs": [],
            "hasCentraleNucleaire": False,
            "geogCanalisations": [],
            "planPreventionRisquesDTOs": [],
        }
    )
    assert result["pollution"]["basias_sur_parcelle"] == 0
    assert result["nucleaire"] is False
    assert result["canalisations"] == 0
    assert result["ppr"] == []


@pytest.mark.parametrize("bad", [{"data": [None]}, {"data": [{}], "results": 0}])
async def test_ssp_invalid_branch_preserves_other_official_empty_branches(bad):
    body = {
        key: {"results": 0, "data": []}
        for key in ["casias", "instructions", "conclusions_sis", "conclusions_sup"]
    }
    body["casias"] = bad
    with respx.mock as mock:
        mock.get(f"{risks.GEORISQUES_BASE}/ssp").respond(200, json=body)
        result = await risks.query_polluted_sites(**POINT)
    assert result["casias"] is None
    assert result["sources"]["casias"]["statut"] == "indisponible"
    assert result["sources"]["instructions"]["statut"] == "vide"


async def test_bdnb_invalid_rows_are_unavailable():
    bdnb._parcelle_cache.clear()
    with respx.mock as mock:
        mock.get(f"{bdnb.BDNB_BASE}/donnees/batiment_groupe_complet/parcelle").respond(
            200, json=[None]
        )
        with pytest.raises(UpstreamUnavailableError):
            await bdnb.query_batiment_by_parcelle("44109000AB0001")
    bdnb._parcelle_cache.clear()
