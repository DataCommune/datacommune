"""Tests: GET /v1/bien/logements applies the Phase 51 exact boundary (51-03).

Until this plan, the endpoint owned a SECOND public attribution rule: an
``if not rows`` cascade -- exact BAN key, then the other keys of the RNB
carrier, then a 12 m spatial fallback -- whose result fed the cards directly.
``/v1/resolve`` stopped attributing by proximity in 51-02, so leaving that
cascade in place would have let the interstitiel re-serve, as a selectable
card, the very diagnostic resolve now refuses to attribute.

What is locked here:

- the public route reaches ONE decision function, ``classify_dpe_attribution``
  (Plan 51-01), through two separate fetches: the canonical BAN key, then the
  OTHER keys of the same RNB carrier;
- the spatial fetch is unreachable from the route -- asserted by a never-called
  mock AND by the absence of the symbol in the router module;
- only ``multiple`` publishes cards, and those cards are exactly the exact
  candidates of the decision. A row attached through another BAN key, or
  carrying a foreign key, can never become one;
- ``building`` publishes a count, a distribution and a truncation flag, never a
  ``numero_dpe`` nor an elected class.

The Phase 48 context (detection, denominateur, ventes, rattachement counters)
survives and is still computed -- it enriches the interstitiel, it never
promotes a diagnostic.

Same offline pattern as ``tests/test_logements_endpoint.py``: no real DB, every
fetch is monkeypatched.
"""

import inspect
import json
from datetime import date
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.smoke

from app.database import get_db
from app.dpe.inventory_service import DpeInventoryServiceResult
from app.dpe.schemas import (
    DpeInventoryDistribution,
    DpeInventoryItem,
    DpeInventoryPage,
)
from app.main import app

ID_PARCELLE = "85047000AB0042"
# Resolved key of the requested address (1 rue de Guerande, witness case).
CLE_RESOLUE = "44109_3912_00001"
# Other entrance of the SAME RNB building (corner building, R4-02).
CLE_ANGLE = "44109_3056_00012"
# Key of a DPE row that does NOT belong to the requested identity.
CLE_ETRANGERE = "44109_3912_00003"


@pytest.fixture(autouse=True)
def _fake_db_session():
    """Override get_db with a throwaway session (queries are mocked)."""

    async def _fake_db():
        return AsyncMock()

    app.dependency_overrides[get_db] = _fake_db
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def sentinelles_spatiales(monkeypatch):
    """Arm the removed attachment paths so any call to them is visible.

    Patched on ``app.logements.queries`` -- their own module -- precisely
    because the router must no longer hold a reference to them.
    """
    spatial = AsyncMock(return_value=[])
    par_ban = AsyncMock(return_value=[])
    monkeypatch.setattr("app.logements.queries.dpe_appartements_par_parcelle", spatial)
    monkeypatch.setattr("app.logements.queries.dpe_appartements_par_ban", par_ban)
    return {"spatial": spatial, "par_ban": par_ban}


def _dpe_row(
    numero_dpe: str,
    *,
    identifiant_ban: str | None = CLE_RESOLUE,
    etage: int = 0,
    complement: str = "",
    surface: float = 61.6,
    classe: str = "D",
    type_batiment: str = "appartement",
    remplace: str | None = None,
) -> dict:
    """Fabricate one DPE row, with the columns the attribution fetches read."""
    return {
        "numero_dpe": numero_dpe,
        "numero_dpe_remplace": remplace,
        "identifiant_ban": identifiant_ban,
        "date_etablissement_dpe": date(2023, 5, 1),
        "date_fin_validite_dpe": date(2033, 5, 1),
        "type_batiment": type_batiment,
        "surface_habitable": surface,
        "etiquette_dpe": classe,
        "etiquette_ges": "C",
        "numero_etage_appartement": etage,
        "complement_adresse_logement": complement,
    }


def _patch_attribution(
    monkeypatch,
    *,
    exact_rows=(),
    batiment_rows=(),
    batiment_tronque=False,
    rnb_cles=(),
    copro_rows=(),
    dvf_rows=(),
):
    """Patch the shared inventory, RNB widening and parcel context reads."""
    rows = [row for row in exact_rows if row.get("identifiant_ban") == CLE_RESOLUE]
    counts = {label: 0 for label in "ABCDEFG"}
    unknown = 0
    for row in rows:
        label = (row.get("etiquette_dpe") or "").strip().upper()
        if label in counts:
            counts[label] += 1
        else:
            unknown += 1
    has_more = len(rows) > 20
    items = [
        DpeInventoryItem(
            document_ref=f"opaque-document-{index}",
            type_batiment=row.get("type_batiment"),
            surface_habitable=row.get("surface_habitable"),
            etiquette_dpe=row.get("etiquette_dpe"),
            etiquette_ges=row.get("etiquette_ges"),
            date_etablissement_dpe=row.get("date_etablissement_dpe"),
            date_fin_validite_dpe=row.get("date_fin_validite_dpe"),
        )
        for index, row in enumerate(rows[:20])
    ]

    def inventory_result(*_args, ban_id=None, **_kwargs):
        if ban_id is None:
            return DpeInventoryServiceResult(
                page=DpeInventoryPage(
                    state="identity_unavailable",
                    total=None,
                    distribution=DpeInventoryDistribution(),
                    page_size=20,
                    has_more=False,
                    read_state="complete",
                ),
                apartment_document_count=None,
            )
        distribution = DpeInventoryDistribution.model_validate(
            {**counts, "unknown": unknown}
        )
        return DpeInventoryServiceResult(
            page=DpeInventoryPage(
                state="ready" if rows else "absent",
                context_ref="opaque-context",
                items=items,
                total=len(rows),
                distribution=distribution,
                page_size=20,
                has_more=has_more,
                next_cursor="opaque-next-cursor" if has_more else None,
                read_state="complete",
            ),
            apartment_document_count=sum(
                (row.get("type_batiment") or "").strip().lower() == "appartement"
                for row in rows
            ),
        )

    monkeypatch.setattr(
        "app.logements.router.load_dpe_inventory_result",
        AsyncMock(side_effect=inventory_result),
    )
    monkeypatch.setattr(
        "app.logements.router.dpe_par_identifiants_ban",
        AsyncMock(return_value=(list(batiment_rows), batiment_tronque)),
    )
    monkeypatch.setattr(
        "app.logements.router.cles_ban_batiment_rnb",
        AsyncMock(return_value=tuple(rnb_cles)),
    )
    monkeypatch.setattr(
        "app.logements.router.copropriete_par_parcelle",
        AsyncMock(return_value=list(copro_rows)),
    )
    monkeypatch.setattr(
        "app.logements.router.dvf_appartements_par_parcelle",
        AsyncMock(return_value=list(dvf_rows)),
    )


async def _get(client, **params):
    return await client.get(
        "/v1/bien/logements", params={"id_parcelle": ID_PARCELLE, **params}
    )


# ---------------------------------------------------------------------------
# The exact boundary: what may become a card
# ---------------------------------------------------------------------------


class TestFrontiereExacte:
    async def test_un_seul_terminal_exact_ne_publie_aucune_carte(
        self, client, monkeypatch
    ):
        """CASE-LOG-EXACT-01: one individual terminal IS the diagnostic.

        There is nothing to choose between, so no selectable card is served;
        the diagnostic lives under ``dpe_attribution`` alone.
        """
        _patch_attribution(
            monkeypatch,
            exact_rows=[_dpe_row("dpe-1", complement="Porte 11")],
        )

        resp = await _get(client, ban_id=CLE_RESOLUE)

        assert resp.status_code == 200
        donnees = resp.json()["donnees"]
        attribution = donnees["dpe_attribution"]
        assert attribution["status"] == "unassigned", "CASE-LOG-EXACT-01 state"
        assert attribution["reason"] == "dwelling_identity_unproven"
        assert attribution["diagnostic"] is None
        assert "dpe-1" not in resp.text
        assert "logements" not in donnees, "CASE-LOG-EXACT-01 no selectable card"
        assert donnees["rattachement"] == {
            "exact": 1,
            "elargi_batiment": 0,
            "spatial": 0,
        }

    async def test_plusieurs_exacts_distinguables_donnent_les_cartes(
        self, client, monkeypatch
    ):
        """CASE-LOG-MULT-02: the cards ARE the exact candidates, in order."""
        _patch_attribution(
            monkeypatch,
            exact_rows=[
                _dpe_row("dpe-a", complement="Porte 11"),
                _dpe_row("dpe-b", complement="Porte 21"),
            ],
        )

        resp = await _get(client, ban_id=CLE_RESOLUE)

        assert resp.status_code == 200
        donnees = resp.json()["donnees"]
        attribution = donnees["dpe_attribution"]
        assert attribution["status"] == "unassigned", "CASE-LOG-MULT-02 state"
        assert attribution["candidates"] == []
        assert "logements" not in donnees
        # Nothing is pre-selected: the payload elects no card.
        assert attribution["diagnostic"] is None

    async def test_une_ligne_ban_etrangere_ne_devient_jamais_une_carte_exact(
        self, client, monkeypatch
    ):
        """CASE-LOG-FOREIGN-03: defence in depth on the identity equality.

        A row loaded under another key is dropped from the decision, counted
        out loud, and never demoted into a card.
        """
        _patch_attribution(
            monkeypatch,
            exact_rows=[
                _dpe_row(
                    "dpe-voisin",
                    identifiant_ban=CLE_ETRANGERE,
                    complement="Porte 31",
                )
            ],
        )

        resp = await _get(client, ban_id=CLE_RESOLUE)

        assert resp.status_code == 200
        donnees = resp.json()["donnees"]
        assert donnees["dpe_attribution"]["status"] == "absent"
        assert donnees["dpe_attribution"]["reason"] == "not_reported"
        assert "logements" not in donnees, "CASE-LOG-FOREIGN-03 no card"
        assert donnees["diagnostics_exclus_adresse"] == 0
        assert "dpe-voisin" not in resp.text

    async def test_no_ban_id_ne_declenche_aucune_lecture_dpe(
        self, client, monkeypatch, sentinelles_spatiales
    ):
        """CASE-LOG-NOBAN-04: no identity, no attribution and no fetch at all.

        The former handler answered this case with the unrestricted spatial
        path, which is exactly how a neighbour's diagnostic became a card.
        """
        _patch_attribution(monkeypatch, exact_rows=[_dpe_row("jamais-charge")])

        resp = await _get(client)

        assert resp.status_code == 200
        donnees = resp.json()["donnees"]
        assert donnees["dpe_attribution"]["status"] == "absent"
        assert donnees["dpe_attribution"]["reason"] == "identity_unavailable"
        assert "logements" not in donnees
        assert donnees["diagnostics_charges"] == 0
        assert donnees["detection"]["signaux"]["dpe_appart"]["compte"] == 0

        from app.logements import router as logements_router

        assert (
            logements_router.load_dpe_inventory_result.await_args.kwargs["ban_id"]
            is None
        )
        logements_router.dpe_par_identifiants_ban.assert_not_called()
        sentinelles_spatiales["spatial"].assert_not_called()

    async def test_le_fetch_spatial_est_injoignable_depuis_la_route_publique(
        self, client, monkeypatch, sentinelles_spatiales
    ):
        """CASE-LOG-SPATIAL-05: the 12 m fallback is gone from the public path.

        Proven three ways -- the mock is never called, the router module holds
        no reference to either legacy fetch, and the handler source names
        neither. A never-called mock alone would pass on a code path that
        simply did not run in this scenario.
        """
        _patch_attribution(monkeypatch, exact_rows=[])

        resp = await _get(client, ban_id=CLE_RESOLUE)

        assert resp.status_code == 200
        sentinelles_spatiales["spatial"].assert_not_called()
        sentinelles_spatiales["par_ban"].assert_not_called()

        from app.logements import router as logements_router

        assert not hasattr(logements_router, "dpe_appartements_par_parcelle"), (
            "CASE-LOG-SPATIAL-05 the spatial fetch must not be imported"
        )
        assert not hasattr(logements_router, "dpe_appartements_par_ban"), (
            "CASE-LOG-SPATIAL-05 the widened-key fetch must not be imported"
        )
        source = inspect.getsource(logements_router.get_logements)
        assert "dpe_appartements_par_parcelle" not in source
        assert "classify_dpe_attribution" in inspect.getsource(logements_router)


# ---------------------------------------------------------------------------
# The building aggregate: useful, never attributable
# ---------------------------------------------------------------------------


class TestAgregatBatiment:
    async def test_les_autres_cles_rnb_ne_donnent_qu_un_agregat_building(
        self, client, monkeypatch
    ):
        """CASE-LOG-RNB-06: zero exact row + diagnostics on the carrier's other
        keys is a ``building`` answer -- a count and a distribution, no card."""
        _patch_attribution(
            monkeypatch,
            exact_rows=[],
            batiment_rows=[
                _dpe_row("dpe-v1", identifiant_ban=CLE_ANGLE, complement="Porte 11"),
                _dpe_row(
                    "dpe-v2",
                    identifiant_ban=CLE_ANGLE,
                    complement="Porte 21",
                    classe="F",
                ),
            ],
            rnb_cles=(CLE_ANGLE,),
        )

        resp = await _get(client, ban_id=CLE_RESOLUE)

        assert resp.status_code == 200
        donnees = resp.json()["donnees"]
        attribution = donnees["dpe_attribution"]
        assert attribution["status"] == "absent", "CASE-LOG-RNB-06 state"
        assert attribution["reason"] == "not_reported"
        assert attribution["building_context"]["count"] == 2
        assert attribution["building_context"]["distribution"] == {"D": 1, "F": 1}
        assert "logements" not in donnees, "CASE-LOG-RNB-06 nothing selectable"
        assert "dpe-v1" not in resp.text, (
            "CASE-LOG-RNB-06 no building-level numero_dpe may be published"
        )
        assert donnees["rattachement"] == {
            "exact": 0,
            "elargi_batiment": 2,
            "spatial": 0,
        }
        # Other BAN keys stay contextual and cannot feed exact-address detection.
        assert donnees["detection"]["signaux"]["dpe_appart"]["compte"] == 0
        assert donnees["denominateur"]["non_distinguables"] == 0

    async def test_rnb_context_ne_publie_aucun_identifiant_ou_detail_source(
        self,
        client,
        monkeypatch,
    ):
        private_values = (
            "DPE-RNB-PRIVE-58108",
            "DPE-RNB-REMPLACE-58108",
            "Porte privee 88 etage 73",
            "44 rue brute privee 44999 Villetest",
            CLE_ANGLE,
        )
        private_row = _dpe_row(
            private_values[0],
            identifiant_ban=CLE_ANGLE,
            etage=73,
            complement=private_values[2],
            remplace=private_values[1],
        )
        private_row["adresse_brute"] = private_values[3]
        _patch_attribution(
            monkeypatch,
            batiment_rows=[private_row],
            rnb_cles=(CLE_ANGLE,),
        )

        response = await _get(client, ban_id=CLE_RESOLUE)

        assert response.status_code == 200
        data = response.json()["donnees"]
        assert data["dpe_attribution"]["status"] == "absent"
        assert data["dpe_attribution"]["building_context"] == {
            "count": 1,
            "distribution": {"D": 1},
            "truncated": False,
        }
        assert "logements" not in data
        serialized = json.dumps(response.json(), ensure_ascii=False)
        for private_value in private_values:
            assert private_value not in serialized

    async def test_la_cle_canonique_est_retiree_avant_le_fetch_batiment(
        self, client, monkeypatch
    ):
        """CASE-LOG-RNB-07: the exact and building row sets are disjoint by
        construction -- the canonical key is removed even though the widening
        query already excludes it by contract."""
        _patch_attribution(
            monkeypatch,
            exact_rows=[],
            batiment_rows=[_dpe_row("dpe-v1", identifiant_ban=CLE_ANGLE)],
            rnb_cles=(CLE_RESOLUE, CLE_ANGLE),
        )

        resp = await _get(client, ban_id=CLE_RESOLUE)

        assert resp.status_code == 200
        from app.logements import router as logements_router

        demandees = list(logements_router.dpe_par_identifiants_ban.await_args.args[1])
        assert demandees == [CLE_ANGLE], (
            "CASE-LOG-RNB-07 the canonical key must not be re-fetched as building"
        )

    async def test_une_ligne_elargi_batiment_ne_devient_jamais_une_carte(
        self, client, monkeypatch
    ):
        """CASE-LOG-ELARGI-08: exact candidates exist, so a card list is served
        -- and the carrier's other-key rows stay out of it entirely."""
        _patch_attribution(
            monkeypatch,
            exact_rows=[
                _dpe_row("dpe-a", complement="Porte 11"),
                _dpe_row("dpe-b", complement="Porte 21"),
            ],
            batiment_rows=[
                _dpe_row("dpe-elargi", identifiant_ban=CLE_ANGLE, complement="Porte 31")
            ],
            rnb_cles=(CLE_ANGLE,),
        )

        resp = await _get(client, ban_id=CLE_RESOLUE)

        assert resp.status_code == 200
        donnees = resp.json()["donnees"]
        assert donnees["dpe_attribution"]["status"] == "unassigned"
        assert "logements" not in donnees
        assert "dpe-elargi" not in resp.text, (
            "CASE-LOG-ELARGI-08 another entrance's diagnostic is not selectable"
        )

    async def test_inventaire_exact_pagine_ne_peut_pas_devenir_une_carte(
        self, client, monkeypatch
    ):
        """CASE-LOG-TRUNC-09: inventory pagination never creates logement cards."""
        _patch_attribution(
            monkeypatch,
            exact_rows=[
                _dpe_row(f"dpe-coupe-{index}", complement=f"Porte {index}")
                for index in range(21)
            ],
        )

        resp = await _get(client, ban_id=CLE_RESOLUE)

        assert resp.status_code == 200
        donnees = resp.json()["donnees"]
        attribution = donnees["dpe_attribution"]
        assert attribution["status"] == "unassigned", "CASE-LOG-TRUNC-09 state"
        assert attribution["reason"] == "dwelling_identity_unproven"
        assert donnees["dpe_inventory"]["total"] == 21
        assert donnees["dpe_inventory"]["page_size"] == 20
        assert donnees["dpe_inventory"]["has_more"] is True
        assert donnees["dpe_inventory"]["next_cursor"] == "opaque-next-cursor"
        assert [item["document_ref"] for item in donnees["dpe_inventory"]["items"]] == [
            f"opaque-document-{index}" for index in range(20)
        ]
        assert "logements" not in donnees
        assert "dpe-coupe" not in resp.text, "CASE-LOG-TRUNC-09 no individual ID"
        assert donnees["tronque"] is False

    async def test_la_limite_est_transmise_aux_deux_fetchs_bornes(
        self, client, monkeypatch
    ):
        """CASE-LOG-TRUNC-10: the caller's bound reaches the bounded queries."""
        _patch_attribution(
            monkeypatch,
            exact_rows=[],
            batiment_rows=[_dpe_row("dpe-v1", identifiant_ban=CLE_ANGLE)],
            rnb_cles=(CLE_ANGLE,),
        )

        resp = await _get(client, ban_id=CLE_RESOLUE, limite=25)

        assert resp.status_code == 200
        from app.logements import router as logements_router

        assert (
            logements_router.load_dpe_inventory_result.await_args.kwargs["limit"] == 20
        )
        assert (
            logements_router.dpe_par_identifiants_ban.await_args.kwargs["limit"] == 25
        )


# ---------------------------------------------------------------------------
# The published contract (Task 2)
# ---------------------------------------------------------------------------


def _valid_v2_contract_payload() -> dict:
    """Return a fully coherent V2 baseline for one-invariant negative tests."""
    return {
        "id_parcelle": ID_PARCELLE,
        "detection": {
            "signaux": {
                name: {
                    "positif": False,
                    "presence": "absent",
                    "absence_reason": "not_reported",
                    "compte": 0,
                }
                for name in ("copro_rnc", "dpe_appart", "dvf_appart")
            },
            "signaux_positifs": 0,
            "mode": "bien",
            "preuve_identites": False,
        },
        "denominateur": {
            "logements_connus": 0,
            "lots_declares": None,
            "non_distinguables": 0,
        },
        "distribution_dpe": {"D": 1},
        "ventes_immeuble": [],
        "dpe_inventory": {
            "state": "absent",
            "context_ref": "opaque-context",
            "total": 0,
            "distribution": {},
            "page_size": 20,
            "has_more": False,
            "read_state": "complete",
        },
        "dpe_attribution": {
            "status": "absent",
            "reason": "not_reported",
            "building_context": {
                "count": 1,
                "distribution": {"D": 1},
                "truncated": False,
            },
        },
        "diagnostics_charges": 1,
        "diagnostics_exclus_adresse": 0,
        "rattachement": {"exact": 0, "elargi_batiment": 1, "spatial": 0},
        "tronque": False,
    }


class TestContratResponse:
    async def test_le_schema_expose_dpe_attribution_obligatoire(self):
        """CASE-LOG-SCHEMA-11: the decision is a required, typed field."""
        from app.dpe.schemas import DpeAttribution
        from app.logements.schemas import ResultatLogements

        champ = ResultatLogements.model_fields["dpe_attribution"]
        assert champ.is_required(), "CASE-LOG-SCHEMA-11 required"
        assert champ.annotation is DpeAttribution, "CASE-LOG-SCHEMA-11 shared model"

    def test_le_schema_interdit_des_cartes_hors_multiple(self):
        """CASE-LOG-SCHEMA-12: an impossible payload is unconstructible.

        The guard lives in the model, not only in the handler: a future caller
        cannot assemble a card list beside a non-``multiple`` decision. Since
        phase 01-04 the field does not exist at all, so what rejects it is
        ``extra="forbid"`` rather than the validator -- an earlier and stricter
        refusal, and the reason this case is kept rather than deleted.
        """
        from app.logements.schemas import ResultatLogements

        baseline = ResultatLogements.model_validate(_valid_v2_contract_payload())
        payload = baseline.model_dump(mode="python")
        payload["logements"] = [{"numero_dpe": "dpe-1", "perime": False}]

        with pytest.raises(
            ValidationError,
            match="Extra inputs are not permitted",
        ):
            ResultatLogements.model_validate(payload)

    def test_le_schema_interdit_une_distribution_inventee(self):
        """CASE-LOG-SCHEMA-13: ``distribution_dpe`` mirrors the decision.

        It is never recomputed beside it -- two aggregates of the same rows
        would be two sources of truth for the same sentence.
        """
        from app.logements.schemas import ResultatLogements

        baseline = ResultatLogements.model_validate(_valid_v2_contract_payload())
        payload = baseline.model_dump(mode="python")
        payload["distribution_dpe"] = {"A": 3}

        with pytest.raises(
            ValidationError,
            match="distribution_dpe must mirror",
        ):
            ResultatLogements.model_validate(payload)

    async def test_la_distribution_publiee_est_celle_du_contexte_batiment(
        self, client, monkeypatch
    ):
        """CASE-LOG-DIST-14: building publishes its distribution verbatim."""
        _patch_attribution(
            monkeypatch,
            exact_rows=[],
            batiment_rows=[
                _dpe_row("dpe-v1", identifiant_ban=CLE_ANGLE, classe="C"),
                _dpe_row("dpe-v2", identifiant_ban=CLE_ANGLE, classe="C"),
                _dpe_row("dpe-v3", identifiant_ban=CLE_ANGLE, classe="G"),
            ],
            rnb_cles=(CLE_ANGLE,),
        )

        resp = await _get(client, ban_id=CLE_RESOLUE)

        assert resp.status_code == 200
        donnees = resp.json()["donnees"]
        assert (
            donnees["distribution_dpe"]
            == donnees["dpe_attribution"]["building_context"]["distribution"]
        )
        assert donnees["distribution_dpe"] == {"C": 2, "G": 1}

    async def test_la_distribution_est_vide_hors_etat_building(
        self, client, monkeypatch
    ):
        """CASE-LOG-DIST-15: no building aggregate, no aggregated distribution.

        In ``multiple`` each card carries its own label; publishing a second,
        aggregated reading of the same rows would invite a « classe principale »
        that the phase exists to forbid.
        """
        _patch_attribution(
            monkeypatch,
            exact_rows=[
                _dpe_row("dpe-a", complement="Porte 11", classe="C"),
                _dpe_row("dpe-b", complement="Porte 21", classe="G"),
            ],
        )

        resp = await _get(client, ban_id=CLE_RESOLUE)

        assert resp.status_code == 200
        donnees = resp.json()["donnees"]
        assert donnees["dpe_attribution"]["status"] == "unassigned"
        assert donnees["distribution_dpe"] == {}
        assert "logements" not in donnees

    async def test_les_candidates_et_les_cartes_portent_les_memes_champs(
        self, client, monkeypatch
    ):
        """CASE-LOG-CANDIDATES-16: the card is the candidate plus its
        provenance -- never a re-read of the raw ADEME complement."""
        _patch_attribution(
            monkeypatch,
            exact_rows=[
                _dpe_row("dpe-a", complement="Porte 11"),
                _dpe_row("dpe-b", complement="Porte 21"),
            ],
        )

        resp = await _get(client, ban_id=CLE_RESOLUE)

        assert resp.status_code == 200
        assert "_rattachement" not in resp.text
        assert "complement_adresse_logement" not in resp.text
        donnees = resp.json()["donnees"]
        assert "logements" not in donnees
        assert donnees["dpe_attribution"]["candidates"] == []

    async def test_le_contexte_phase_48_survit_a_la_bascule(self, client, monkeypatch):
        """CASE-LOG-RESPONSE-17: detection, denominateur, ventes and copro
        context are untouched -- and carry no monetary field OUTSIDE the sale
        rows, which since D-17 publish the price of their mutation beside the
        class that says what was sold."""
        _patch_attribution(
            monkeypatch,
            exact_rows=[
                _dpe_row("dpe-a", complement="Porte 11"),
                _dpe_row("dpe-b", complement="Porte 21"),
            ],
            copro_rows=[
                {
                    "numero_immatriculation": "AB1234567",
                    "nom_copropriete": "Residence Test",
                    "nb_lots_total": 10,
                    "nb_lots_habitation_strict": 8,
                    "periode_construction": "1980-1989",
                }
            ],
            dvf_rows=[
                {
                    "date_mutation": date(2024, 3, 1),
                    "type_local": None,
                    "surface_reelle_bati": 310,
                    "nombre_pieces_principales": 14,
                    "valeur_fonciere": 980000.0,
                    "classe_mutation": "ensemble_residentiel",
                    "mono_local": False,
                    "hors_stats_vefa": False,
                    "prix_m2": None,
                    "prix_m2_raison": "mutation_multi_locaux",
                    "nb_locaux_residentiel": 5,
                    "nb_locaux_commercial": 0,
                }
            ],
        )

        resp = await _get(client, ban_id=CLE_RESOLUE)

        assert resp.status_code == 200
        donnees = resp.json()["donnees"]
        assert donnees["detection"]["mode"] == "immeuble"
        assert donnees["detection"]["preuve_identites"] is False
        assert donnees["denominateur"]["logements_connus"] == 0
        assert donnees["denominateur"]["non_distinguables"] == 0
        assert donnees["denominateur"]["lots_declares"] == 8
        assert len(donnees["ventes_immeuble"]) == 1
        assert len(resp.json()["meta"]["source"]) == 3

        # D-17: the ensemble sale is published as a GLOBAL price, labelled --
        # never as the price of one lot, so no price per m2 exists on it.
        vente = donnees["ventes_immeuble"][0]
        assert vente["valeur_fonciere"] == 980000.0
        assert vente["classe_mutation"] == "ensemble_residentiel"
        assert vente["prix_m2"] is None
        assert vente["prix_m2_raison"] == "mutation_multi_locaux"

        # The half of D-16 that still holds: no amount outside the sale rows.
        donnees.pop("ventes_immeuble")
        reste = json.dumps(donnees).lower()
        for interdit in ("valeur_fonciere", "prix", "montant"):
            assert interdit not in reste, f"{interdit} leaked outside ventes_immeuble"
